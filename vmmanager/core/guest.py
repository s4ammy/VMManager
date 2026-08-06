"""Guest-agent operations: info, files, exec, filesystem health."""

from __future__ import annotations

import time

import libvirt

from .connection import _with_conn

def svc_screenshot(uuid: str) -> bytes:
    def go(conn):
        dom = conn.lookupByUUIDString(uuid)
        stream = conn.newStream()
        dom.screenshot(stream, 0)
        chunks: list[bytes] = []
        try:
            stream.recvAll(lambda s, data, opaque: chunks.append(data), None)
            stream.finish()
        except libvirt.libvirtError:
            # Left open otherwise, and with card thumbnails on this runs
            # every few seconds - a failing guest would leak one each time.
            stream.abort()
            raise
        return b"".join(chunks)

    return _with_conn(go)

def svc_guest_info(uuid: str) -> dict:
    def go(conn):
        dom = conn.lookupByUUIDString(uuid)
        out: dict[str, str] = {}
        try:
            info = dom.guestInfo(0, 0)
            if "os.pretty-name" in info:
                out["os"] = info["os.pretty-name"]
            if "hostname" in info:
                out["hostname"] = info["hostname"]
            if "os.kernel-release" in info:
                out["kernel"] = info["os.kernel-release"]
        except libvirt.libvirtError as e:
            out["agent"] = f"unavailable ({e.get_error_message()})"
            return out
        try:
            ifaces = dom.interfaceAddresses(
                libvirt.VIR_DOMAIN_INTERFACE_ADDRESSES_SRC_AGENT
            )
            ips = []
            for name, data in ifaces.items():
                if name == "lo":
                    continue
                for addr in data.get("addrs") or []:
                    ips.append(addr["addr"])
            if ips:
                out["ips"] = ", ".join(ips)
        except libvirt.libvirtError:
            pass
        return out

    return _with_conn(go)

def svc_send_file(uuid: str, local_path: str, guest_path: str) -> str:
    import base64
    import json

    import libvirt_qemu

    def cmd(dom, name: str, args: dict):
        payload = json.dumps({"execute": name, "arguments": args})
        return json.loads(libvirt_qemu.qemuAgentCommand(dom, payload, 30, 0))

    def go(conn):
        dom = conn.lookupByUUIDString(uuid)
        handle = cmd(dom, "guest-file-open", {"path": guest_path, "mode": "wb"})["return"]
        sent = 0
        try:
            with open(local_path, "rb") as f:
                while chunk := f.read(48 * 1024):
                    cmd(
                        dom, "guest-file-write",
                        {"handle": handle, "buf-b64": base64.b64encode(chunk).decode()},
                    )
                    sent += len(chunk)
        finally:
            cmd(dom, "guest-file-close", {"handle": handle})
        return f"Sent {sent / 1024:.0f} KB to {guest_path}"

    return _with_conn(go)

def _agent_cmd(dom, name: str, args: dict | None = None):
    import json

    import libvirt_qemu

    payload = {"execute": name}
    if args:
        payload["arguments"] = args
    return json.loads(libvirt_qemu.qemuAgentCommand(dom, json.dumps(payload), 30, 0))

def svc_fetch_file(uuid: str, guest_path: str, local_path: str) -> str:
    """Read a file out of the guest through the agent."""
    import base64

    def go(conn):
        dom = conn.lookupByUUIDString(uuid)
        handle = _agent_cmd(dom, "guest-file-open", {"path": guest_path, "mode": "rb"})["return"]
        got = 0
        try:
            with open(local_path, "wb") as f:
                while True:
                    r = _agent_cmd(
                        dom, "guest-file-read", {"handle": handle, "count": 48 * 1024}
                    )["return"]
                    if r.get("count", 0):
                        f.write(base64.b64decode(r["buf-b64"]))
                        got += r["count"]
                    if r.get("eof"):
                        break
        finally:
            _agent_cmd(dom, "guest-file-close", {"handle": handle})
        return f"Fetched {got / 1024:.0f} KB into {local_path}"

    return _with_conn(go)

def svc_guest_exec(uuid: str, cmdline: str, timeout: int = 60) -> tuple[int, str, str]:
    """Run a shell command in the guest; (exit code, stdout, stderr)."""
    import base64
    import time as _time

    def go(conn):
        dom = conn.lookupByUUIDString(uuid)
        pid = _agent_cmd(
            dom, "guest-exec",
            {"path": "/bin/sh", "arg": ["-c", cmdline], "capture-output": True},
        )["return"]["pid"]
        deadline = _time.monotonic() + timeout
        while True:
            status = _agent_cmd(dom, "guest-exec-status", {"pid": pid})["return"]
            if status.get("exited"):
                out = base64.b64decode(status.get("out-data", "")).decode("utf-8", "replace")
                err = base64.b64decode(status.get("err-data", "")).decode("utf-8", "replace")
                return status.get("exitcode", -1), out, err
            if _time.monotonic() > deadline:
                raise RuntimeError(f"command still running after {timeout}s")
            _time.sleep(0.3)

    return _with_conn(go)

def svc_guest_fs_health(uuid: str) -> list[tuple[str, float]]:
    """(mountpoint, used %) for real filesystems, worst first."""

    def go(conn):
        dom = conn.lookupByUUIDString(uuid)
        info = _agent_cmd(dom, "guest-get-fsinfo")["return"]
        out = []
        for fs in info:
            total = fs.get("total-bytes", 0)
            if not total:
                continue
            pct = fs.get("used-bytes", 0) * 100.0 / total
            out.append((fs.get("mountpoint", "?"), pct))
        out.sort(key=lambda x: -x[1])
        return out

    return _with_conn(go)

def svc_agent_action(uuid: str, op: str) -> str:
    def go(conn):
        dom = conn.lookupByUUIDString(uuid)
        if op == "ping":
            import libvirt_qemu

            libvirt_qemu.qemuAgentCommand(
                dom, '{"execute":"guest-ping"}', 5, 0
            )
            return "Agent responded."
        if op == "freeze":
            n = dom.fsFreeze(None, 0)
            return f"Froze {n} filesystem(s). Thaw before writing to disk!"
        if op == "thaw":
            n = dom.fsThaw(None, 0)
            return f"Thawed {n} filesystem(s)."
        if op == "sync-time":
            dom.setTime(flags=libvirt.VIR_DOMAIN_TIME_SYNC)
            return "Guest clock synced to the host."
        if op == "shutdown":
            dom.shutdownFlags(libvirt.VIR_DOMAIN_SHUTDOWN_GUEST_AGENT)
            return "Shutdown requested through the agent."
        if op == "reboot":
            dom.reboot(libvirt.VIR_DOMAIN_REBOOT_GUEST_AGENT)
            return "Reboot requested through the agent."
        raise RuntimeError(f"unknown agent op {op}")

    return _with_conn(go)
