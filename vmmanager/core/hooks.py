"""Single-GPU passthrough, as libvirt hook scripts we write for you.

Handing over the only graphics card in the machine means the host has to
let go of it first: stop the graphical session, take the virtual consoles
and the boot framebuffer off it, unload the driver. Then all of that in
reverse when the guest stops. It is a page of shell that everybody copies
from the same few gists, gets subtly wrong, and debugs with the screen
turned off.

So we generate it, for this host: its display manager, its driver, its
card and every function on that card. And because the same hook is the
right place for it, optionally the CPU isolation that stops the host
scheduling its own work onto the cores the guest is pinned to.

libvirt's own hook layout is used - /etc/libvirt/hooks/qemu.d/<machine>/
<operation>/<sub-operation>/ - so these scripts sit beside anyone else's
rather than replacing them. The dispatcher at /etc/libvirt/hooks/qemu is
only written when there is not one already; a hand-written one is left
exactly as it is, with instructions instead.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from .elevate import check_address, check_name, install_root_file, run_root_script
from .tuning import format_cpuset
from .vfio import function_siblings, read_device_ids

HOOK_DIR = "/etc/libvirt/hooks"
DISPATCHER = f"{HOOK_DIR}/qemu"
MARKER = "# vmmanager-dispatcher"

# Which modules have to come out for the host to let go of the card. The
# audio function is deliberately absent: snd_hda_intel drives the host's
# own sound cards too, and libvirt's managed detach unbinds just the one
# device without taking everybody's audio away.
DRIVER_MODULES = {
    "nvidia": ["nvidia_drm", "nvidia_modeset", "nvidia_uvm", "nvidia"],
    "nouveau": ["nouveau"],
    "amdgpu": ["amdgpu"],
    "radeon": ["radeon"],
    "i915": ["i915"],
    "xe": ["xe"],
}

@dataclass(frozen=True)
class GpuHandoff:
    """Everything the generated scripts need to know about this host."""

    vm_name: str
    addresses: tuple[str, ...]  # every function of the card
    driver: str  # host driver on the graphics function, "" if none
    modules: tuple[str, ...]  # what to unload, in order
    display_manager: str  # systemd unit, "" when there is none
    host_cpus: tuple[int, ...] = ()  # cores left to the host, () to skip
    all_cpus: tuple[int, ...] = ()
    governor: str = ""  # performance governor while the guest runs

    @property
    def isolates_cpus(self) -> bool:
        return bool(self.host_cpus and self.all_cpus)

def detect_display_manager(root: str = "/") -> str:
    """The display manager unit, read rather than guessed.

    The symlink is what systemd itself follows, so it is right even on a
    host running something the list of usual suspects has never heard of.
    """
    link = os.path.join(root, "etc/systemd/system/display-manager.service")
    if os.path.islink(link):
        return os.path.basename(os.readlink(link))
    return ""

def host_cpuset(all_cpus, guest_cpus) -> tuple[int, ...]:
    """The CPUs left for the host once the guest has taken its own.

    Refuses to leave the host nothing: a system.slice with no CPUs at all
    cannot run the thing that would give them back.
    """
    remaining = tuple(sorted(set(all_cpus) - set(guest_cpus)))
    if not remaining:
        raise RuntimeError(
            "That pinning leaves the host no CPUs at all. Free at least "
            "one physical core for the host before isolating."
        )
    return remaining

def plan_handoff(vm_name: str, address: str, tuning=None, topology=None,
                 governor: str = "", sys_root: str = "/sys",
                 fs_root: str = "/") -> GpuHandoff:
    """Work out what this host needs to hand `address` to `vm_name`."""
    check_name(vm_name)
    address = check_address(address)
    addresses = tuple(function_siblings(address, root=sys_root))
    ids = read_device_ids(address, root=sys_root)
    modules = tuple(DRIVER_MODULES.get(ids.driver, [ids.driver] if ids.driver else []))
    host_cpus: tuple[int, ...] = ()
    all_cpus: tuple[int, ...] = ()
    if tuning is not None and topology is not None and tuning.vcpu_pins:
        guest = {c for cpus in tuning.vcpu_pins.values() for c in cpus}
        guest |= set(tuning.emulator_pin)
        all_cpus = tuple(sorted(c.id for c in topology.cpus))
        host_cpus = host_cpuset(all_cpus, guest)
    return GpuHandoff(
        vm_name=vm_name,
        addresses=addresses,
        driver=ids.driver,
        modules=modules,
        display_manager=detect_display_manager(root=fs_root),
        host_cpus=host_cpus,
        all_cpus=all_cpus,
        governor=governor,
    )

_HEADER = """#!/usr/bin/env bash
# Written by VMManager for the machine '{vm}'. Safe to edit - it is only
# rewritten when you set this up again from the app.
#
# libvirt runs this as root, with the graphical session about to go away -
# nowhere to print to - so it logs to the journal instead:
#   journalctl -t vmmanager-hook
if command -v systemd-cat >/dev/null 2>&1; then
    exec 1> >(systemd-cat -t vmmanager-hook) 2>&1
fi
set -x
"""

def start_script(plan: GpuHandoff) -> str:
    """What runs before the guest starts: the host lets go of the card."""
    out = [_HEADER.format(vm=plan.vm_name)]
    if plan.display_manager:
        out.append(f"""
# 1. stop the graphical session that is using the card
systemctl stop {plan.display_manager}
""")
    else:
        out.append("""
# 1. no display manager was found on this host, so nothing to stop. If you
#    start your session by hand, stop it before starting the machine.
""")
    out.append("""
# 2. take the virtual consoles off the framebuffer
for vtcon in /sys/class/vtconsole/vtcon*; do
    [ -e "$vtcon/bind" ] || continue
    case "$(cat "$vtcon/name" 2>/dev/null)" in
        *"frame buffer"*) echo 0 > "$vtcon/bind" || true ;;
    esac
done

# 3. and the boot framebuffer, whichever kind this kernel set up
for fb in efi-framebuffer.0 simple-framebuffer.0 vesa-framebuffer.0; do
    driver="/sys/bus/platform/drivers/${fb%.*}"
    if [ -e "$driver/$fb" ]; then
        echo "$fb" > "$driver/unbind" || true
    fi
done
sleep 1
""")
    if plan.modules:
        mods = " ".join(plan.modules)
        out.append(f"""
# 4. unload the host's graphics driver. Anything still holding the card
#    makes this fail, and the machine will not start - which is better
#    than starting with a card the host has not let go of.
modprobe -r {mods}
""")
    else:
        out.append("""
# 4. no host driver is bound to this card, so there is nothing to unload.
""")
    out.append("""
# libvirt detaches the card itself: the devices are managed='yes', so it
# binds each function to vfio-pci here and hands it back afterwards.
""")
    if plan.isolates_cpus:
        host = format_cpuset(plan.host_cpus)
        out.append(f"""
# 5. keep the host's own work off the cores this guest is pinned to. The
#    guest's qemu lives in machine.slice, which is left alone.
for slice in system.slice user.slice init.scope; do
    systemctl set-property --runtime -- "$slice" AllowedCPUs={host} || true
done
""")
    if plan.governor:
        out.append(f"""
# 6. and ask for the {plan.governor} governor while the guest runs
if [ -e /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor ]; then
    for g in /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor; do
        echo {plan.governor} > "$g" || true
    done
fi
""")
    return "".join(out)

def revert_script(plan: GpuHandoff) -> str:
    """What runs after the guest stops: the host takes the card back."""
    out = [_HEADER.format(vm=plan.vm_name)]
    if plan.isolates_cpus:
        every = format_cpuset(plan.all_cpus)
        out.append(f"""
# 1. give the host back every core
for slice in system.slice user.slice init.scope; do
    systemctl set-property --runtime -- "$slice" AllowedCPUs={every} || true
done
""")
    if plan.governor:
        out.append("""
# 2. and its usual governor, whatever the kernel defaults to
if [ -e /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor ]; then
    default=$(cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor)
    for g in /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor; do
        echo "$default" > "$g" || true
    done
fi
""")
    if plan.modules:
        mods = " ".join(reversed(plan.modules))
        out.append(f"""
# 3. load the host's graphics driver again
modprobe {mods}
sleep 1
""")
    out.append("""
# 4. bind the boot framebuffer and the consoles back
for fb in efi-framebuffer.0 simple-framebuffer.0 vesa-framebuffer.0; do
    driver="/sys/bus/platform/drivers/${fb%.*}"
    if [ -d "$driver" ] && [ ! -e "$driver/$fb" ]; then
        echo "$fb" > "$driver/bind" 2>/dev/null || true
    fi
done
for vtcon in /sys/class/vtconsole/vtcon*; do
    [ -e "$vtcon/bind" ] || continue
    case "$(cat "$vtcon/name" 2>/dev/null)" in
        *"frame buffer"*) echo 1 > "$vtcon/bind" || true ;;
    esac
done
""")
    if plan.display_manager:
        out.append(f"""
# 5. and bring the desktop back
systemctl start {plan.display_manager}
""")
    return "".join(out)

DISPATCHER_SCRIPT = f"""#!/usr/bin/env bash
{MARKER} - written by VMManager because there was no hook here yet.
#
# libvirt calls this for every machine; it runs whatever is in
#   /etc/libvirt/hooks/qemu.d/<machine>/<operation>/<sub-operation>/
# so several tools (and you) can add hooks without treading on each other.
GUEST="$1"
OPERATION="$2"
SUBOPERATION="$3"
DIR="$(dirname "$0")/qemu.d/$GUEST/$OPERATION/$SUBOPERATION"
[ -d "$DIR" ] || exit 0
for script in "$DIR"/*; do
    [ -x "$script" ] || continue
    "$script" "$GUEST" "$OPERATION" "$SUBOPERATION" || exit $?
done
exit 0
"""

def script_paths(vm_name: str) -> tuple[str, str]:
    """Where this machine's two scripts live."""
    check_name(vm_name)
    return (
        f"{HOOK_DIR}/qemu.d/{vm_name}/prepare/begin/10-vmmanager-gpu.sh",
        f"{HOOK_DIR}/qemu.d/{vm_name}/release/end/10-vmmanager-gpu.sh",
    )

@dataclass(frozen=True)
class HookState:
    """What is installed on this host right now."""

    dispatcher_exists: bool = False
    dispatcher_is_ours: bool = False
    start_installed: bool = False
    revert_installed: bool = False
    installed_for: tuple[str, ...] = ()  # machines with our scripts

    @property
    def foreign_dispatcher(self) -> bool:
        return self.dispatcher_exists and not self.dispatcher_is_ours

def hook_state(vm_name: str, root: str = "/") -> HookState:
    def full(path: str) -> str:
        return os.path.join(root, path.lstrip("/"))

    dispatcher = full(DISPATCHER)
    exists = os.path.exists(dispatcher)
    ours = False
    if exists:
        try:
            with open(dispatcher) as f:
                ours = MARKER in f.read()
        except OSError:
            ours = False
    start, revert = script_paths(vm_name)
    installed = []
    qemu_d = full(f"{HOOK_DIR}/qemu.d")
    if os.path.isdir(qemu_d):
        for name in sorted(os.listdir(qemu_d)):
            try:
                begin, _ = script_paths(name)
            except ValueError:
                continue
            if os.path.exists(full(begin)):
                installed.append(name)
    return HookState(
        dispatcher_exists=exists,
        dispatcher_is_ours=ours,
        start_installed=os.path.exists(full(start)),
        revert_installed=os.path.exists(full(revert)),
        installed_for=tuple(installed),
    )

def svc_install_hooks(plan: GpuHandoff) -> str:
    """Write the two scripts, and the dispatcher if nothing else owns it."""
    start_path, revert_path = script_paths(plan.vm_name)
    state = hook_state(plan.vm_name)
    messages = []
    if not state.dispatcher_exists:
        install_root_file(DISPATCHER, DISPATCHER_SCRIPT, "0755")
        messages.append("installed the hook dispatcher")
    elif not state.dispatcher_is_ours:
        messages.append(
            f"left your existing {DISPATCHER} alone - make sure it runs "
            "the scripts in qemu.d/, or these will never fire"
        )
    install_root_file(start_path, start_script(plan), "0755")
    install_root_file(revert_path, revert_script(plan), "0755")
    messages.append(f"wrote the hooks for {plan.vm_name}")
    return " · ".join(messages)

def svc_remove_hooks(vm_name: str) -> str:
    start_path, revert_path = script_paths(vm_name)
    run_root_script('rm -f "$1" "$2"', [start_path, revert_path])
    return f"removed the hooks for {vm_name}"

def svc_hook_state(vm_name: str) -> HookState:
    return hook_state(vm_name)

def governor_available(root: str = "/sys") -> bool:
    """Whether this host exposes a CPU governor to switch at all."""
    return os.path.exists(
        os.path.join(root, "devices/system/cpu/cpu0/cpufreq/scaling_governor")
    )
