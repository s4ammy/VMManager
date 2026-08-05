"""The generated single-GPU hooks, and the CPU isolation in them.

These scripts run as root at the moment the screen goes dark, so the
things worth pinning down are: that they are valid shell, that they undo
in the reverse order they do, that an existing hook of someone else's is
never overwritten, and that isolation can never leave the host with no
CPUs to run the undo on.
"""

from __future__ import annotations

import os
import shutil
import subprocess

import pytest

from vmmanager.core.hooks import (
    DISPATCHER_SCRIPT,
    MARKER,
    GpuHandoff,
    detect_display_manager,
    host_cpuset,
    hook_state,
    plan_handoff,
    revert_script,
    script_paths,
    start_script,
)
from vmmanager.core.tuning import HostCpu, HostTopology, Tuning

PLAN = GpuHandoff(
    vm_name="win11",
    addresses=("0000:01:00.0", "0000:01:00.1"),
    driver="nvidia",
    modules=("nvidia_drm", "nvidia_modeset", "nvidia_uvm", "nvidia"),
    display_manager="sddm.service",
    host_cpus=(4, 5, 6, 7, 12, 13, 14, 15),
    all_cpus=tuple(range(16)),
    governor="performance",
)


def _topology(cpus: int = 16) -> HostTopology:
    return HostTopology(
        sockets=1, cores=cpus // 2, threads=2, cells=1, hugepages=(),
        cpus=tuple(
            HostCpu(id=i, socket=0, core=i % (cpus // 2), cell=0,
                    siblings=(i, (i + cpus // 2) % cpus))
            for i in range(cpus)
        ),
    )


# -- the scripts are shell


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
@pytest.mark.parametrize("script", ["start", "revert", "dispatcher"])
def test_generated_scripts_are_valid_shell(tmp_path, script):
    text = {
        "start": start_script(PLAN),
        "revert": revert_script(PLAN),
        "dispatcher": DISPATCHER_SCRIPT,
    }[script]
    path = tmp_path / f"{script}.sh"
    path.write_text(text)
    result = subprocess.run(
        ["bash", "-n", str(path)], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr


def test_start_lets_go_of_the_card_in_the_right_order():
    text = start_script(PLAN)
    order = [
        text.index("systemctl stop sddm.service"),
        text.index("for vtcon in"),
        text.index("for fb in"),
        text.index("modprobe -r"),
    ]
    assert order == sorted(order), "the desktop must go before the driver does"
    assert "nvidia_drm nvidia_modeset nvidia_uvm nvidia" in text


def test_revert_undoes_it_backwards():
    text = revert_script(PLAN)
    # the driver comes back before the desktop that needs it
    assert text.index("modprobe nvidia") < text.index("systemctl start")
    # and modules load in the reverse of the unload order
    assert "modprobe nvidia nvidia_uvm nvidia_modeset nvidia_drm" in text


def test_a_host_with_no_display_manager_says_so_instead_of_guessing():
    plan = GpuHandoff(vm_name="v", addresses=("0000:01:00.0",), driver="amdgpu",
                      modules=("amdgpu",), display_manager="")
    text = start_script(plan)
    assert "systemctl stop" not in text
    assert "no display manager" in text
    assert "systemctl start" not in revert_script(plan)


def test_a_card_with_no_host_driver_has_nothing_to_unload():
    plan = GpuHandoff(vm_name="v", addresses=("0000:01:00.0",), driver="",
                      modules=(), display_manager="sddm.service")
    assert "modprobe -r" not in start_script(plan)
    assert "nothing to unload" in start_script(plan)


def test_the_audio_function_driver_is_never_unloaded():
    """snd_hda_intel drives the host's own sound cards too, so unloading
    it would silence the host; libvirt's managed detach unbinds the one
    device instead. Checked against the table the plan is built from,
    not against a plan written out by hand here - which would pass
    whatever the table said."""
    from vmmanager.core.hooks import DRIVER_MODULES

    for driver, modules in DRIVER_MODULES.items():
        assert "snd_hda_intel" not in modules, driver
    every_module = {m for mods in DRIVER_MODULES.values() for m in mods}
    assert not {m for m in every_module if m.startswith("snd")}


def test_the_display_manager_is_read_not_guessed(tmp_path):
    assert detect_display_manager(root=str(tmp_path)) == ""
    unit_dir = tmp_path / "etc/systemd/system"
    unit_dir.mkdir(parents=True)
    os.symlink("/usr/lib/systemd/system/plasmalogin.service",
               unit_dir / "display-manager.service")
    assert detect_display_manager(root=str(tmp_path)) == "plasmalogin.service"


# -- CPU isolation


def test_host_keeps_the_cores_the_guest_does_not_take():
    assert host_cpuset(range(16), [0, 1, 8, 9]) == (2, 3, 4, 5, 6, 7,
                                                    10, 11, 12, 13, 14, 15)


def test_isolation_that_would_leave_the_host_nothing_is_refused():
    with pytest.raises(RuntimeError, match="no CPUs"):
        host_cpuset(range(8), range(8))


def test_isolation_sets_the_host_slices_and_gives_them_back():
    start, revert = start_script(PLAN), revert_script(PLAN)
    assert "AllowedCPUs=4-7,12-15" in start
    for slice_name in ("system.slice", "user.slice", "init.scope"):
        assert slice_name in start and slice_name in revert
    # machine.slice is where the guest lives and must not be restricted
    assert "machine.slice" not in start.split("# ")[0]
    assert "AllowedCPUs=0-15" in revert


def test_no_pinning_means_no_isolation_in_the_script():
    plan = GpuHandoff(vm_name="v", addresses=("0000:01:00.0",), driver="nvidia",
                      modules=("nvidia",), display_manager="sddm.service")
    assert "AllowedCPUs" not in start_script(plan)
    assert "AllowedCPUs" not in revert_script(plan)


def test_the_governor_is_only_touched_when_asked():
    assert "scaling_governor" in start_script(PLAN)
    plain = GpuHandoff(vm_name="v", addresses=("0000:01:00.0",),
                       driver="nvidia", modules=("nvidia",),
                       display_manager="sddm.service")
    assert "scaling_governor" not in start_script(plain)


def test_plan_reads_pinning_into_the_isolation_set(tmp_path):
    devices = tmp_path / "bus/pci/devices/0000:01:00.0"
    devices.mkdir(parents=True)
    for name, value in (("vendor", "0x10de"), ("device", "0x2705"),
                        ("class", "0x030000")):
        (devices / name).write_text(value)
    tuning = Tuning(vcpu_pins={0: (0,), 1: (8,)}, emulator_pin=(1,))
    plan = plan_handoff(
        "win11", "0000:01:00.0", tuning=tuning, topology=_topology(),
        sys_root=str(tmp_path), fs_root=str(tmp_path),
    )
    assert plan.isolates_cpus
    assert 0 not in plan.host_cpus and 8 not in plan.host_cpus
    assert 1 not in plan.host_cpus  # the emulator's core is the guest's too
    assert 2 in plan.host_cpus


# -- not standing on anyone else's hooks


def test_the_scripts_go_under_the_machines_own_hook_directory():
    start, revert = script_paths("win11")
    assert start == (
        "/etc/libvirt/hooks/qemu.d/win11/prepare/begin/10-vmmanager-gpu.sh"
    )
    assert revert.endswith("/win11/release/end/10-vmmanager-gpu.sh")


def test_a_machine_name_that_would_escape_the_directory_is_refused():
    with pytest.raises(ValueError):
        script_paths("../../etc/cron.d/x")


def test_someone_elses_dispatcher_is_recognised_as_theirs(tmp_path):
    hooks = tmp_path / "etc/libvirt/hooks"
    hooks.mkdir(parents=True)
    (hooks / "qemu").write_text("#!/bin/bash\n# my own hook\n")
    state = hook_state("win11", root=str(tmp_path))
    assert state.dispatcher_exists and not state.dispatcher_is_ours
    assert state.foreign_dispatcher
    assert not state.start_installed

    (hooks / "qemu").write_text(DISPATCHER_SCRIPT)
    state = hook_state("win11", root=str(tmp_path))
    assert state.dispatcher_is_ours and not state.foreign_dispatcher
    assert MARKER in DISPATCHER_SCRIPT


def test_hook_state_lists_the_machines_we_have_set_up(tmp_path):
    for name in ("win11", "gaming"):
        start, _ = script_paths(name)
        path = tmp_path / start.lstrip("/")
        path.parent.mkdir(parents=True)
        path.write_text("#!/bin/sh\n")
    state = hook_state("win11", root=str(tmp_path))
    assert state.installed_for == ("gaming", "win11")
    assert state.start_installed
