"""A record of what this app did, kept alongside what libvirt reported.

The `events` table already holds domain lifecycle events - libvirt saying a
machine started or stopped. What was missing is the other half: which of
those the app asked for, what else it asked for, and what came back. A
machine that shut down on its own and one that was shut down from here look
identical afterwards, and a schedule that quietly failed at 04:00 leaves no
trace at all.

Recording happens at the service boundary rather than at each call site.
Every write in `core` is wrapped once, here, by name - so a new one is
covered the day it is written, and nobody has to remember to log it.

Reads are not recorded. There are far more of them, they say nothing about
intent, and a log of `svc_get_hardware` every two seconds would bury the
one line that mattered.
"""

from __future__ import annotations

import functools
import inspect

# Most writes are named for what they do, so a prefix catches them and a
# service function added tomorrow is recorded the day it lands.
WRITE_PREFIXES = (
    "svc_add_", "svc_set_", "svc_remove_", "svc_delete_", "svc_create_",
    "svc_attach_", "svc_detach_", "svc_insert_", "svc_eject_", "svc_grow_",
    "svc_move_", "svc_import_", "svc_clone_", "svc_restore_", "svc_revert_",
    "svc_apply_", "svc_install_", "svc_write_", "svc_save_", "svc_bind_",
    "svc_unbind_", "svc_prune_", "svc_compact_", "svc_flatten_", "svc_wipe_",
    "svc_migrate", "svc_backup", "svc_start", "svc_stop", "svc_shutdown",
    "svc_reboot", "svc_reset", "svc_pause", "svc_resume", "svc_destroy",
    "svc_undefine", "svc_define", "svc_autostart", "svc_send_keys",
    "svc_rename", "svc_snapshot", "svc_isolate", "svc_dump_rom",
)

# Writes whose names say what they are about rather than what they do.
# Listed by hand because guessing wrong here means an operation nobody can
# account for afterwards - and a guard test fails if a new svc_ function
# matches neither this nor READS.
EXTRA_WRITES = frozenset({
    "svc_agent_action", "svc_change_media", "svc_clone", "svc_delete",
    "svc_deploy_stack", "svc_domain_action", "svc_export_vm",
    "svc_guest_exec", "svc_linked_clone", "svc_network_action",
    "svc_persist_vfio", "svc_clear_persist_vfio", "svc_pool_action",
    "svc_redefine_network", "svc_redefine_network_ex", "svc_resize_volume",
    "svc_send_file", "svc_switch_mode", "svc_teardown_stack",
    "svc_upload_volume", "svc_upload_volume_conn",
    "svc_upload_volume_from_file", "svc_upload_volume_from_file_conn",
    # qemu-img writing to an image is exactly the kind of thing you want a
    # record of afterwards, and both of these can ask for a root password.
    "svc_repair_image", "svc_convert_image",
})

# Reads that a prefix catches by accident. A read changes nothing that
# outlives the call, so recording one says nothing and buries the rest.
READS = frozenset({
    "svc_set_uri",          # a preference of this app, not a change to a guest
    "svc_start_problems",   # answers "why will this not start"
    "svc_backup_state",     # reports on backups, does not take one
    "svc_check_image",      # qemu-img check without -r writes nothing
    "svc_image_info",
    "svc_compare_machines",
    "svc_compare_definitions",
    "svc_capture_profile",  # reads a machine; saving the result is separate
})


def _describe(name: str, args, kwargs) -> tuple[str, str]:
    """The machine it was about, and a short note of the arguments.

    Positional arguments are matched to parameter names so the uuid is found
    wherever it sits, rather than assuming it comes first.
    """
    uuid = str(kwargs.get("uuid", "") or "")
    rest = []
    for key, value in kwargs.items():
        if key == "uuid" or value is None:
            continue
        rest.append(f"{key}={_short(value)}")
    for value in args:
        if not uuid and _looks_like_uuid(value):
            uuid = str(value)
        else:
            rest.append(_short(value))
    return uuid, ", ".join(rest)[:400]


def _looks_like_uuid(value) -> bool:
    text = str(value)
    return len(text) == 36 and text.count("-") == 4


def _short(value) -> str:
    text = str(value)
    return text if len(text) <= 60 else text[:57] + "…"


def records(fn):
    """Wrap one service function so its result reaches the activity log."""

    @functools.wraps(fn)
    def logged(*args, **kwargs):
        try:
            bound = inspect.signature(fn).bind_partial(*args, **kwargs)
            uuid, detail = _describe(fn.__name__, bound.args, bound.kwargs)
        except (TypeError, ValueError):
            uuid, detail = "", ""
        try:
            result = fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 - re-raised below
            _record(fn.__name__, uuid, f"{detail} → {exc}"[:400], False)
            raise
        note = detail
        if isinstance(result, str) and result:
            note = f"{detail} → {result}" if detail else result
        _record(fn.__name__, uuid, note[:400], True)
        return result

    logged.__wrapped_for_activity__ = True
    return logged


def _record(action: str, uuid: str, detail: str, ok: bool) -> None:
    """Never let logging be the thing that breaks an operation."""
    try:
        from ..data.history import record_activity

        record_activity(action, uuid, detail, ok)
    except Exception:  # noqa: BLE001 - a missing log is not worth an error
        pass


def is_write(name: str) -> bool:
    if name in READS:
        return False
    return name.startswith(WRITE_PREFIXES) or name in EXTRA_WRITES


def wrap_module(namespace: dict) -> list[str]:
    """Wrap every write in a module namespace. Returns the names wrapped."""
    wrapped = []
    for name, value in list(namespace.items()):
        if not callable(value) or not is_write(name):
            continue
        if getattr(value, "__wrapped_for_activity__", False):
            continue
        namespace[name] = records(value)
        wrapped.append(name)
    return wrapped
