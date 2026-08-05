"""Doing things as root, through polkit, without shell injection.

Passthrough setup writes to /etc and /sys, which this process cannot do.
Everything here goes through `pkexec`, and every value that could come
from a device, a machine name or a file picker is passed as an *argument*
to a fixed script rather than pasted into one - so a name with a space or
a semicolon in it cannot become a command.

Callers get a plain RuntimeError with what the password prompt said when
it fails, because "returned 126" is not something to show anyone.
"""

from __future__ import annotations

import re
import shutil
import subprocess

# 0000:03:00.0 - libvirt and sysfs both write them this way
PCI_ADDRESS = re.compile(r"^[0-9a-fA-F]{4}:[0-9a-fA-F]{2}:[0-9a-fA-F]{2}\.[0-7]$")
# 10de:2705
PCI_ID = re.compile(r"^[0-9a-fA-F]{4}:[0-9a-fA-F]{4}$")
# a domain name we are willing to put in a path
SAFE_NAME = re.compile(r"^[A-Za-z0-9._-]{1,64}$")

def check_address(address: str) -> str:
    if not PCI_ADDRESS.match(address):
        raise ValueError(f"{address!r} is not a PCI address like 0000:03:00.0")
    return address.lower()

def check_name(name: str) -> str:
    if not SAFE_NAME.match(name):
        raise ValueError(
            f"{name!r} has characters that cannot go in a hook path - "
            "letters, digits, dot, dash and underscore only"
        )
    return name

def available() -> bool:
    """Whether we can ask for a password at all."""
    return shutil.which("pkexec") is not None

def _pkexec():
    pkexec = shutil.which("pkexec")
    if pkexec is None:
        raise RuntimeError(
            "pkexec is not installed, so this cannot ask for a password. "
            "The commands are shown so you can run them with sudo instead."
        )
    return pkexec

def run_root_script(script: str, args: list[str] | None = None,
                    timeout: int = 300) -> str:
    """Run a fixed shell script as root; anything variable goes in `args`.

    The script text is ours and never contains caller data; `args` land in
    $1, $2 … where the shell quotes nothing further.
    """
    argv = [_pkexec(), "/bin/sh", "-c", script, "vmmanager", *(args or [])]
    result = subprocess.run(
        argv, capture_output=True, timeout=timeout,
    )
    if result.returncode != 0:
        detail = result.stderr.decode(errors="replace").strip()
        if result.returncode == 126:
            raise RuntimeError("The password prompt was dismissed.")
        raise RuntimeError(detail or f"the command failed ({result.returncode})")
    return result.stdout.decode(errors="replace")

def install_root_file(path: str, content: str, mode: str = "0644") -> str:
    """Root-owned file whose *content* arrives on stdin rather than argv.

    A hook script is a few kilobytes of shell; putting that on a command
    line is asking for trouble with length limits and quoting both.
    """
    if not path.startswith("/") or any(c in path for c in "\n\r"):
        raise ValueError(f"{path!r} is not a plain absolute path")
    if mode not in ("0644", "0755"):
        raise ValueError(f"{mode!r} is not a mode this writes")
    argv = [
        _pkexec(), "/bin/sh", "-c",
        'mkdir -p "$(dirname "$1")" && cat > "$1" && chmod "$2" "$1"',
        "vmmanager", path, mode,
    ]
    result = subprocess.run(
        argv, input=content.encode(), capture_output=True, timeout=300,
    )
    if result.returncode != 0:
        detail = result.stderr.decode(errors="replace").strip()
        if result.returncode == 126:
            raise RuntimeError("The password prompt was dismissed.")
        raise RuntimeError(detail or "could not write the file")
    return path
