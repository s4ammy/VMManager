from __future__ import annotations

from PySide6.QtCore import QTimer
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QMainWindow,
    QMenu,
    QStackedWidget,
    QWidget,
)

from PySide6.QtWidgets import QFileDialog

from .dialogs import (
    CompareDialog,
    ChoiceDialog,
    DiffDialog,
    CloneDetailsDialog,
    CloneDialog,
    ConfirmDialog,
    DeleteVmDialog,
    StartCheckDialog,
    ErrorDialog,
    MigrateDialog,
    ModesDialog,
    OsIconDialog,
    ScheduleDialog,
    UsbRulesDialog,
    WakeScheduleDialog,
)
from .data.history import StatsStore
from .core.profiles import apply_to_spec, from_json, to_json
from .libvirt_service import (
    svc_capture_profile,
    svc_compare_definitions,
    svc_compare_machines,
    DomainSnapshot,
    HostSnapshot,
    PollWorker,
    current_uri,
    set_poll_seconds,
    set_uri,
    svc_clone_advanced,
    svc_create_snapshot,
    svc_create_vm,
    svc_delete,
    svc_domain_action,
    svc_export_vm,
    svc_get_on_crash,
    svc_import_backup,
    svc_list_domain_disks,
    svc_list_network_names,
    svc_list_pools,
    svc_linked_clone,
    svc_migrate_advanced,
    svc_prune_snapshots,
    svc_attach_hostdev,
    svc_list_host_devices,
    svc_restore_backup,
    svc_restore_from_file,
    svc_usb_watch_state,
    usb_auto_attach_plan,
    svc_save_to_file,
    svc_set_on_crash,
    svc_start_problems,
    svc_backing_chain,
    svc_delete_mode,
    svc_list_modes,
    svc_mode_diff,
    svc_save_mode,
    set_hook_script,
    svc_marker_state,
    svc_switch_mode,
    svc_write_marker_elevated,
    svc_flatten_disk,
    svc_set_os_icon,
    svc_set_tags,
    svc_set_template,
    open_external,
)
from .pages.activity import ActivityPage
from .pages.detail import DetailPage
from .pages.detail.window import MachineWindow
from .pages.host import HostPage
from .pages.machines import MachinesPage
from .pages.networks import NetworksPage
from .pages.stacks import StacksPage
from .pages.templates import TemplatesPage
from .pages.themes import ThemesPage
from .pages.settings import (
    hook_script_path,
    SettingsPage,
    active_uri,
    confirmations_enabled,
    saved_connections,
    saved_poll_seconds,
)
from .pages.storage import StoragePage
from .tasks import connect_guarded, run_task
from . import APP_NAME
from .logs import log
from .widgets import Sidebar
from .wizard import NewVmDialog


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.resize(1120, 700)
        self.setMinimumSize(880, 520)

        set_uri(active_uri())
        set_poll_seconds(saved_poll_seconds())
        self.stats = StatsStore()

        root = QWidget()
        root.setObjectName("Root")
        self.setCentralWidget(root)
        shell = QHBoxLayout(root)
        shell.setContentsMargins(0, 0, 0, 0)
        shell.setSpacing(0)

        self.sidebar = Sidebar()
        self.sidebar.navigate.connect(self._navigate)
        shell.addWidget(self.sidebar)

        self.stack = QStackedWidget()
        shell.addWidget(self.stack, 1)

        self.machines = MachinesPage()
        self.detail = DetailPage()
        self.stacks_page = StacksPage(self.stats)
        self.templates_page = TemplatesPage()
        self.storage = StoragePage()
        self.networks = NetworksPage()
        self.settings = SettingsPage()
        self.host_page = HostPage()
        self.activity_page = ActivityPage()
        self.themes = ThemesPage()
        for page in (self.machines, self.detail, self.templates_page,
                     self.stacks_page, self.host_page, self.storage,
                     self.networks, self.activity_page, self.themes,
                     self.settings):
            self.stack.addWidget(page)

        # One window per machine, keyed by uuid. See _pop_out.
        self._windows: dict[str, MachineWindow] = {}
        # The window whatever is happening now belongs to. See _owned.
        self._owner: QWidget = self

        self.machines.action.connect(self._domain_action)
        self.machines.open_detail.connect(self._open_detail)
        self.machines.context.connect(self._owned(self, self._show_menu))
        self.machines.new_vm.connect(self._owned(self, self._new_vm))
        self.machines.restore_file.connect(
            self._owned(self, self._restore_from_file)
        )
        self.machines.import_backup.connect(self._owned(self, self._import_backup))
        self.machines.restore_backup.connect(self._owned(self, self._restore_backup))
        self.machines.bulk_action.connect(self._owned(self, self._bulk_action))
        self.machines.health_updated.connect(self._on_health_alert)

        self.detail.back.connect(lambda: self._navigate("Machines"))
        self.detail.pop_out.connect(self._pop_out)
        self.detail.action.connect(self._domain_action)
        self.detail.menu_requested.connect(self._owned(self, self._show_menu))
        self.detail.mode_switch.connect(self._owned(self, self._switch_mode_direct))
        self.detail.modes_requested.connect(
            self._owned(self, lambda uuid: self._edit_modes(self._snap_for(uuid)))
        )
        self.templates_page.open_machine.connect(self._open_detail)
        self.settings.connection_changed.connect(self._switch_connection)
        self.settings.poll_changed.connect(set_poll_seconds)
        set_hook_script(hook_script_path())
        self.networks.open_detail.connect(self._open_detail)

        self._domains: list[DomainSnapshot] = []
        self._host: HostSnapshot | None = None

        self.worker = PollWorker()
        self.templates_page.changed.connect(self.worker.poke)
        self.worker.updated.connect(self._on_update)
        self.worker.failed.connect(self._on_error)
        self.worker.start()

        QShortcut(QKeySequence("F5"), self, activated=self.worker.poke)
        QShortcut(
            QKeySequence("Ctrl+N"), self, activated=self._owned(self, self._new_vm)
        )
        QShortcut(
            QKeySequence("Ctrl+K"), self,
            activated=self._owned(self, self._open_palette),
        )

        self.worker.xml_changed.connect(self.stats.record_xml)
        self._schedule_timer = QTimer(self)
        self._schedule_timer.setInterval(60_000)
        self._schedule_timer.timeout.connect(self._run_schedules)
        self._schedule_timer.timeout.connect(self._run_wake_schedules)
        self._schedule_timer.start()
        # auto-attach USB rules: often enough to feel immediate on plug-in,
        # and the tick costs nothing when no rules exist
        self._usb_timer = QTimer(self)
        self._usb_timer.setInterval(10_000)
        self._usb_timer.timeout.connect(self._usb_tick)
        self._usb_timer.start()
        self._setup_tray()

    def _setup_tray(self) -> None:
        from pathlib import Path

        from PySide6.QtGui import QIcon
        from PySide6.QtWidgets import QSystemTrayIcon

        self.tray = None
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        icon = QIcon(str(Path(__file__).parent / "assets" / "icon.svg"))
        tray = QSystemTrayIcon(icon, self)
        tray.setToolTip(APP_NAME)
        menu = QMenu()
        menu.addAction("Show vmmanager", self._show_from_tray)
        menu.addSeparator()
        menu.addAction("Quit", self._quit_for_real)
        tray.setContextMenu(menu)
        tray.activated.connect(
            lambda reason: reason == QSystemTrayIcon.ActivationReason.Trigger
            and self._show_from_tray()
        )
        tray.show()
        self.tray = tray

    def _show_from_tray(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def _quit_for_real(self) -> None:
        self._really_quit = True
        self.close()
        QShortcut(
            QKeySequence("Escape"),
            self,
            activated=lambda: self.stack.currentWidget() is self.detail
            and self._navigate("Machines"),
        )

    # ---------------------------------------------------------------- nav

    def _navigate(self, label: str) -> None:
        pages = {
            "Machines": self.machines,
            "Templates": self.templates_page,
            "Stacks": self.stacks_page,
            "Host": self.host_page,
            "Storage": self.storage,
            "Networks": self.networks,
            "Activity": self.activity_page,
            "Themes": self.themes,
            "Settings": self.settings,
        }
        page = pages.get(label)
        if page is None:
            return
        self.stack.setCurrentWidget(page)
        self.sidebar.set_active(label)
        self.detail.set_visible_page(False)
        if page is self.storage:
            self.storage.refresh()
        elif page is self.networks:
            self.networks.refresh()
        elif page is self.stacks_page:
            self.stacks_page.refresh()
        elif page is self.host_page:
            self.host_page.refresh()
        elif page is self.activity_page:
            self.activity_page.refresh()
        elif page is self.templates_page:
            self.templates_page.refresh()
        elif page is self.themes:
            self.themes.refresh()

    def _open_detail(self, uuid: str) -> None:
        # Already in a window of its own? Raise that rather than opening the
        # same machine twice - two consoles would fight over one VNC server.
        if uuid in self._windows:
            self._raise_window(self._windows[uuid])
            return
        snap = next((d for d in self._domains if d.uuid == uuid), None)
        if snap is None:
            return
        self.detail.show_domain(snap)
        self._load_detail_modes()
        self.stack.setCurrentWidget(self.detail)
        self.detail.set_visible_page(True)

    # ------------------------------------------------------- machine windows

    def _owned(self, window: QWidget, slot):
        """Wrap a slot so whatever it opens belongs to `window`.

        A menu or a dialog is placed relative to the window it belongs to -
        under Wayland there is no way for a client to ask for a screen position
        at all, so its parent is the only thing that decides where it lands.
        Everything here hangs off the main window by default, which put the
        right-click menu and every dialog behind it back on the main window even
        when the machine they were about was in a window of its own.

        The wrapper records which window asked before the slot runs; `_owner` is
        what the dialogs are then parented to. It stays put until the next
        request, so a dialog opened from a menu action - or from a service call
        that answers later - lands on the same window as the menu did.
        """

        def called(*args):
            self._owner = window
            return slot(*args)

        return called

    def _pop_out(self, uuid: str) -> None:
        """Give this machine a window of its own."""
        existing = self._windows.get(uuid)
        if existing is not None:
            self._raise_window(existing)
            return
        snap = next((d for d in self._domains if d.uuid == uuid), None)
        if snap is None:
            return

        window = MachineWindow(snap, self._host)
        page = window.page
        page.action.connect(self._domain_action)
        # Anything these open belongs to the new window, not to this one.
        page.menu_requested.connect(self._owned(window, self._show_menu))
        page.mode_switch.connect(self._owned(window, self._switch_mode_direct))
        page.modes_requested.connect(
            self._owned(window, lambda u: self._edit_modes(self._snap_for(u)))
        )
        page.back.connect(window.close)
        page.pop_out.connect(window.close)  # nothing to pop out of a window
        window.closed.connect(self._forget_window)
        self._windows[uuid] = window
        window.show()
        self._load_modes_for(uuid)

        # The machine has moved out of the main window, so send that back to
        # the list rather than leaving a copy of it behind.
        if self.stack.currentWidget() is self.detail and self.detail.uuid == uuid:
            self._navigate("Machines")

    def _forget_window(self, uuid: str) -> None:
        """Drop a closed window, and stop parenting dialogs to it.

        A dialog given a closed window as its parent is a dialog nobody can see.
        """
        window = self._windows.pop(uuid, None)
        if window is not None and self._owner is window:
            self._owner = self

    def _raise_window(self, window: MachineWindow) -> None:
        window.showNormal()
        window.raise_()
        window.activateWindow()

    def _detail_pages(self, uuid: str) -> list:
        """Every page showing this machine: the embedded one and any window."""
        pages = []
        if self.detail.uuid == uuid:
            pages.append(self.detail)
        window = self._windows.get(uuid)
        if window is not None:
            pages.append(window.page)
        return pages

    # ---------------------------------------------------------------- data

    def _switch_connection(self, uri: str) -> None:
        set_uri(uri)
        self.detail.shutdown()
        self._domains = []
        self.worker.poke()
        self._navigate("Machines")
        self.machines.subtitle.setText(f"Connecting to {uri}…")

    def _on_update(self, domains: list[DomainSnapshot], host: HostSnapshot) -> None:
        self._domains = domains
        self._host = host
        self.stats.record(domains, host)
        self.sidebar.host_panel.update_from(host)
        self.machines.update_from(domains, host)
        self.machines.set_modes(self.stats.all_active_modes())
        self.stacks_page.set_domains(domains)
        # the networks in use, which is what a clone realistically wants; no
        # extra libvirt call for it
        in_use = sorted({n for d in domains for n in d.networks}) or ["default"]
        self.templates_page.set_domains(domains, in_use)
        self.networks.set_domains(domains)
        self.host_page.update_from(domains, host)
        self.activity_page.set_machine_names(domains)
        self.detail.host = host
        self._record_state_events(domains)
        self.settings.set_event_status(self.worker.event_driven)
        if not getattr(self, "_logos_checked", False) and domains:
            self._logos_checked = True
            self._fetch_os_logos()
        if self.stack.currentWidget() is self.detail and self.detail.uuid:
            snap = next((d for d in self._domains if d.uuid == self.detail.uuid), None)
            if snap is not None:
                self.detail.update_from(snap)
            else:
                self._navigate("Machines")  # machine was deleted elsewhere
        self._update_windows(domains, host)

    def _update_windows(self, domains: list[DomainSnapshot],
                        host: HostSnapshot) -> None:
        """Popped-out windows poll from the same tick as the main one.

        A window whose machine is gone closes itself: it was deleted, here or
        somewhere else, and there is nothing left to show.
        """
        alive = {d.uuid: d for d in domains}
        for uuid, window in list(self._windows.items()):
            snap = alive.get(uuid)
            if snap is None:
                window.close()
                continue
            window.page.host = host
            window.update_from(snap)

    def _record_state_events(self, domains: list[DomainSnapshot]) -> None:
        """Timeline events + notifications for state transitions."""
        if not hasattr(self, "_prev_states"):
            self._prev_states = {}
        for d in domains:
            old = self._prev_states.get(d.uuid)
            if old is not None and old != d.state:
                self.stats.record_event(d.uuid, "state", f"{old} → {d.state}")
                if d.state == "crashed":
                    self._notify(f"{d.name} crashed", "The guest hit a fatal error.")
            self._prev_states[d.uuid] = d.state

    def _on_health_alert(self, uuid: str, mount: str, pct: float) -> None:
        name = next((d.name for d in self._domains if d.uuid == uuid), uuid)
        key = f"{uuid}:{mount}"
        if not hasattr(self, "_health_notified"):
            self._health_notified = set()
        if key in self._health_notified:
            return
        self._health_notified.add(key)
        self._notify(
            f"{name}: disk almost full",
            f"{mount} is at {pct:.0f}% inside the guest.",
        )

    def _notify(self, title: str, body: str) -> None:
        tray = getattr(self, "tray", None)
        if tray is not None and tray.isVisible():
            from PySide6.QtWidgets import QSystemTrayIcon

            tray.showMessage(title, body, QSystemTrayIcon.MessageIcon.Warning, 8000)
        else:
            self.machines.show_action_error(f"{title} - {body}")

    def _on_error(self, message: str) -> None:
        self.sidebar.host_panel.set_offline()
        self.machines.show_error(message)

    # ---------------------------------------------------------------- actions

    def _domain_action(self, uuid: str, op: str) -> None:
        def failed(message: str) -> None:
            self.machines.show_action_error(message)
            if op == "start":
                # libvirt's reason for a failed start is accurate and rarely
                # useful. Look at the host and say what about it the
                # definition can no longer count on.
                self._explain_start_failure(uuid, message)

        run_task(
            lambda: svc_domain_action(uuid, op),
            done=lambda _: self.worker.poke(),
            failed=failed,
        )

    def _explain_start_failure(self, uuid: str, message: str = "") -> None:
        snap = self._snap_for(uuid)
        name = snap.name if snap else "this machine"

        def show(problems) -> None:
            if not problems:
                return  # the message libvirt gave is all there is
            StartCheckDialog(self._owner, name, problems, message).exec()

        run_task(
            lambda: svc_start_problems(uuid),
            done=show,
            failed=lambda _m: None,  # a diagnosis that fails is not a bug
        )

    def _snap_for(self, uuid: str) -> DomainSnapshot | None:
        return next((d for d in self._domains if d.uuid == uuid), None)

    def _show_menu(self, uuid: str, global_pos) -> None:
        snap = self._snap_for(uuid)
        if snap is None:
            return
        self._build_menu(snap).exec(global_pos)

    def _build_menu(self, snap: DomainSnapshot) -> QMenu:
        """Separate from showing it: QMenu.exec never returns without a display,
        so this is the part a test can look at.

        The menu belongs to `_owner` - the window it was asked for from - because
        that is what decides where a popup is put on screen.
        """
        uuid = snap.uuid
        running = snap.state == "running"
        paused = snap.state in ("paused", "suspended")

        menu = QMenu(self._owner)
        menu.addAction(
            "Raise its window" if uuid in self._windows else "Open in a window",
            lambda: self._pop_out(uuid),
        )
        menu.addSeparator()
        if not running and not paused:
            menu.addAction(
                "Restore" if snap.has_managed_save else "Start",
                lambda: self._domain_action(uuid, "start"),
            )
        if paused:
            menu.addAction("Resume", lambda: self._domain_action(uuid, "resume"))
        if running:
            menu.addAction("Shut down", lambda: self._domain_action(uuid, "shutdown"))
            menu.addAction("Reboot", lambda: self._domain_action(uuid, "reboot"))
            menu.addAction("Pause", lambda: self._domain_action(uuid, "pause"))
            menu.addAction("Force off…", lambda: self._force_off(snap))
            menu.addSeparator()
            menu.addAction(
                "Save state && stop", lambda: self._domain_action(uuid, "managedsave")
            )
            menu.addAction("Save state to file…", lambda: self._save_to_file(snap))
        if snap.has_managed_save:
            menu.addAction(
                "Discard saved state…", lambda: self._discard_saved(snap)
            )
        menu.addSeparator()
        menu.addAction(
            "Disable autostart" if snap.autostart else "Enable autostart",
            lambda: self._domain_action(
                uuid, "autostart-off" if snap.autostart else "autostart-on"
            ),
        )
        menu.addAction("Tags…", lambda: self._edit_tags(snap))
        menu.addAction("OS icon…", lambda: self._edit_os_icon(snap))
        menu.addAction("Modes…", lambda: self._edit_modes(snap))
        menu.addAction(
            "Flatten into a standalone disk…", lambda: self._flatten(snap)
        )
        menu.addAction("Clone…", lambda: self._clone(snap))
        menu.addAction(
            "Save as hardware profile…", lambda: self._save_profile_from(uuid)
        )
        if snap.is_template:
            menu.addAction("Deploy linked clone…", lambda: self._linked_clone(snap))
            menu.addAction(
                "Unmark template", lambda: self._set_template(snap, False)
            )
        elif not running and not paused:
            menu.addAction("Mark as template", lambda: self._set_template(snap, True))
            menu.addAction("Linked clone…", lambda: self._linked_clone(snap))
        menu.addAction("Scheduled snapshots…", lambda: self._edit_schedule(snap))
        menu.addAction("Power schedule…", lambda: self._edit_wake_schedule(snap))
        menu.addAction("Auto-restart on crash…", lambda: self._edit_on_crash(snap))
        menu.addAction("Auto-attach USB…", lambda: self._edit_usb_rules(snap))
        menu.addAction(
            "Why won't it start?…",
            lambda: self._explain_start_failure(snap.uuid),
        )
        if not running:
            menu.addAction("Export backup…", lambda: self._export_vm(snap))
        menu.addAction("Migrate…", lambda: self._migrate(snap))
        menu.addSeparator()
        if running:
            menu.addAction("Open console (virt-viewer)", lambda: open_external(uuid, "viewer"))
        menu.addAction("Open in virt-manager", lambda: open_external(uuid, "manager"))
        menu.addSeparator()
        menu.addAction("Delete…", lambda: self._delete(snap))
        return menu

    def _confirm(self, title: str, body: str, ok_text: str) -> bool:
        if not confirmations_enabled():
            return True
        confirm = ConfirmDialog(self._owner, title, body, ok_text)
        return confirm.exec() == QDialog.DialogCode.Accepted

    def _force_off(self, snap: DomainSnapshot) -> None:
        if self._confirm(
            "Force off",
            f"Pull the plug on {snap.name}? Unsaved data in the guest will be lost.",
            "Force off",
        ):
            self._domain_action(snap.uuid, "force-off")

    def _discard_saved(self, snap: DomainSnapshot) -> None:
        if self._confirm(
            "Discard saved state",
            f"Throw away the saved state of {snap.name}? Its next start will "
            "be a cold boot.",
            "Discard",
        ):
            self._domain_action(snap.uuid, "discard-saved")

    def _save_to_file(self, snap: DomainSnapshot) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self._owner, "Save machine state", f"{snap.name}.vmstate", "VM state (*.vmstate)"
        )
        if not path:
            return
        uuid = snap.uuid
        run_task(
            lambda: svc_save_to_file(uuid, path),
            done=lambda _: self.worker.poke(),
            failed=lambda m: ErrorDialog(self._owner, "Save failed", m).exec(),
        )

    def _restore_from_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self._owner, "Restore machine state", "", "VM state (*.vmstate);;All files (*)"
        )
        if not path:
            return
        run_task(
            lambda: svc_restore_from_file(path),
            done=lambda _: self.worker.poke(),
            failed=lambda m: ErrorDialog(self._owner, "Restore failed", m).exec(),
        )

    def _migrate(self, snap: DomainSnapshot) -> None:
        others = [u for u in saved_connections() if u != current_uri()]
        dialog = MigrateDialog(self._owner, snap.name, others)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        dest = dialog.uri.currentText().strip()
        if not dest:
            return
        uuid = snap.uuid
        options = dict(
            live=dialog.live.isChecked(),
            tunnelled=dialog.tunnelled.isChecked(),
            unsafe=dialog.unsafe.isChecked(),
            temporary=dialog.temporary.isChecked(),
            dest_address=dialog.dest_address.text().strip(),
            dest_port=dialog.dest_port.value(),
            bandwidth_mib=dialog.bandwidth.value(),
            max_downtime_ms=dialog.downtime.value(),
        )
        self.machines.subtitle.setText(f"Migrating {snap.name} → {dest}…")
        run_task(
            lambda: svc_migrate_advanced(uuid, dest, **options),
            done=lambda _: self.worker.poke(),
            failed=lambda m: ErrorDialog(self._owner, "Migration failed", m).exec(),
        )

    def _clone(self, snap: DomainSnapshot) -> None:
        if snap.state != "shutoff":
            ErrorDialog(
                self._owner, "Can't clone", "Shut the machine down before cloning."
            ).exec()
            return

        def show(disks) -> None:
            dialog = CloneDetailsDialog(self._owner, snap.name, disks)
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return
            new_name = dialog.name.text().strip()
            plan = dialog.disk_plan()
            preserve = dialog.preserve_macs.isChecked()
            run_task(
                lambda: svc_clone_advanced(snap.uuid, new_name, plan, preserve),
                done=lambda _: self.worker.poke(),
                failed=lambda m: ErrorDialog(self._owner, "Clone failed", m).exec(),
            )

        run_task(
            lambda: svc_list_domain_disks(snap.uuid),
            done=show,
            failed=lambda m: ErrorDialog(self._owner, "libvirt error", m).exec(),
        )

    def _delete(self, snap: DomainSnapshot) -> None:
        def show(disks) -> None:
            dialog = DeleteVmDialog(self._owner, snap.name, disks)
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return
            paths = dialog.paths_to_delete()
            run_task(
                lambda: svc_delete(snap.uuid, paths),
                done=lambda message: (
                    self.machines.subtitle.setText(str(message)),
                    self.worker.poke(),
                ),
                failed=lambda m: ErrorDialog(self._owner, "Delete failed", m).exec(),
            )

        run_task(
            lambda: svc_list_domain_disks(snap.uuid),
            done=show,
            failed=lambda m: ErrorDialog(self._owner, "libvirt error", m).exec(),
        )

    def _bulk_action(self, uuids: list, op: str) -> None:
        import time as _time

        if op in ("start", "shutdown"):
            for u in uuids:
                self._domain_action(u, op)
        elif op == "force-off":
            if self._confirm(
                "Force off", f"Pull the plug on {len(uuids)} machines?", "Force off"
            ):
                for u in uuids:
                    self._domain_action(u, "force-off")
        elif op == "snapshot":
            name = "bulk-" + _time.strftime("%Y%m%d-%H%M%S")
            for u in uuids:
                run_task(
                    lambda u=u: svc_create_snapshot(u, name, "bulk snapshot", True),
                    failed=lambda m: self.machines.show_action_error(m),
                )
            self.machines.subtitle.setText(f"snapshotting {len(uuids)} machines…")
        elif op == "compare":
            if len(uuids) != 2:
                self.machines.show_action_error(
                    "Select exactly two machines to compare them."
                )
                return
            left, right = uuids

            def show(result) -> None:
                (names, rows), diff = result
                CompareDialog(self._owner, names, rows, diff).exec()

            run_task(
                lambda: (
                    svc_compare_machines(left, right),
                    svc_compare_definitions(left, right),
                ),
                done=show,
                failed=lambda m: self.machines.show_action_error(m),
            )
        elif op == "tag":
            from PySide6.QtWidgets import QInputDialog

            text, ok = QInputDialog.getText(
                self._owner, "Tag machines",
                f"Tags for {len(uuids)} machines (comma-separated, replaces existing):",
            )
            if not ok:
                return
            tags = tuple(t.strip() for t in text.split(",") if t.strip())
            for u in uuids:
                run_task(
                    lambda u=u: svc_set_tags(u, tags),
                    done=lambda _: self.worker.poke(),
                    failed=lambda m: self.machines.show_action_error(m),
                )
        self.machines.clear_selection()

    def _edit_tags(self, snap: DomainSnapshot) -> None:
        from PySide6.QtWidgets import QInputDialog

        text, ok = QInputDialog.getText(
            self._owner, "Tags", f"Tags for {snap.name} (comma-separated):",
            text=", ".join(snap.tags),
        )
        if not ok:
            return
        tags = tuple(t.strip() for t in text.split(",") if t.strip())
        run_task(
            lambda: svc_set_tags(snap.uuid, tags),
            done=lambda _: self.worker.poke(),
            failed=lambda m: ErrorDialog(self._owner, "Tagging failed", m).exec(),
        )

    def _switch_mode_direct(self, uuid: str, name: str) -> None:
        """Switch straight from the header button, with the same confirmation."""
        self._switch_mode(uuid, name)

    def _switch_mode(self, uuid: str, name: str, after=None) -> None:
        """Confirm, switch, then make sure the marker actually moved.

        A mode may name a file that something outside libvirt reads to know
        which mode is in use. That file usually needs root, which this process
        does not have, and a switch that leaves it stale is a switch that did
        only half of what it looks like it did - so it is raised before
        committing, and offered again afterwards.
        """
        snap = next((d for d in self._domains if d.uuid == uuid), None)
        if snap is None:
            return

        # Checking the marker reads files, so it goes on a worker like every
        # other service call; the dialog waits for the answer.
        run_task(
            lambda: svc_marker_state(uuid, name),
            done=lambda state: self._confirm_switch(uuid, name, snap, state, after),
            failed=lambda message: (
                log.warning("could not check the marker: %s", message),
                self._confirm_switch(uuid, name, snap, None, after),
            ),
        )

    def _confirm_switch(self, uuid: str, name: str, snap, state, after) -> None:
        body = ("This replaces the machine's definition. What is there now is "
                "kept as 'before last switch', so you can come back.")
        if state is not None and state.concerns():
            body += "\n\n" + "\n\n".join(state.concerns())

        # Show what the switch actually changes, not just that it changes
        # something. If the diff cannot be read the plain confirmation stands.
        def ask(diff: str | None) -> None:
            if diff:
                dialog = DiffDialog(
                    self._owner, f"Switch {snap.name} to '{name}'",
                    diff, confirm="Switch", note=body,
                )
                accepted = dialog.exec() == QDialog.DialogCode.Accepted
            else:
                accepted = ConfirmDialog(
                    self._owner, f"Switch {snap.name} to '{name}'", body,
                    "Switch",
                ).exec() == QDialog.DialogCode.Accepted
            if accepted:
                self._do_switch(uuid, name, after)

        run_task(
            lambda: svc_mode_diff(uuid, name),
            done=ask,
            failed=lambda _m: ask(None),
        )

    def _do_switch(self, uuid: str, name: str, after) -> None:

        def done(message: str) -> None:
            self.machines.subtitle.setText(message)
            self.worker.poke()
            self._load_modes_for(uuid)
            if after is not None:
                after(message)
            self._check_marker_after(uuid, name)

        run_task(
            lambda: svc_switch_mode(uuid, name),
            done=done,
            failed=lambda m: ErrorDialog(self._owner, "Could not switch", m).exec(),
        )

    def _check_marker_after(self, uuid: str, name: str) -> None:
        run_task(
            lambda: svc_marker_state(uuid, name),
            done=lambda state: self._offer_marker_write(name, state),
            failed=lambda message: log.warning(
                "could not re-check the marker: %s", message),
        )

    def _offer_marker_write(self, name: str, state) -> None:
        """If the marker is still stale, say so where it will be seen."""
        if not state.matters:
            return  # no marker, or it already says the right thing

        confirm = ConfirmDialog(
            self._owner, f"{name}: the marker was not updated",
            f"The definition is now '{name}', but {state.path} still says "
            f"'{state.holds or 'something else'}'. Anything reading that file - "
            "a libvirt hook, usually - will act on the old mode.\n\n"
            "Writing it needs root.",
            "Write it with pkexec",
        )
        if confirm.exec() != QDialog.DialogCode.Accepted:
            return
        run_task(
            lambda: svc_write_marker_elevated(state.path, name),
            done=lambda msg: self.machines.subtitle.setText(msg),
            failed=lambda m: ErrorDialog(
                self._owner, "Could not write the marker", m).exec(),
        )

    def _load_detail_modes(self) -> None:
        """Refresh the header's mode button for whichever machine is open."""
        if self.detail.uuid:
            self._load_modes_for(self.detail.uuid)

    def _load_modes_for(self, uuid: str) -> None:
        """Every page showing this machine gets the button, not just the one
        in the main window."""

        def apply(modes) -> None:
            for page in self._detail_pages(uuid):
                page.set_modes(modes)

        run_task(lambda: svc_list_modes(uuid), done=apply, failed=lambda _m: None)

    def _edit_modes(self, snap) -> None:
        """Named whole-definition configurations, for machines that are really
        two machines: one with the GPU handed over, one with a console."""
        if snap is None:
            return
        uuid = snap.uuid

        def show(modes) -> None:
            dialog = ModesDialog(self._owner, snap.name, modes, snap.state != "shutoff")

            def refresh(message: str) -> None:
                self.machines.subtitle.setText(message)
                self.worker.poke()
                self._load_detail_modes()
                dialog.accept()

            def switch(name: str) -> None:
                # Same path as the header button, marker check and all.
                self._switch_mode(uuid, name, after=lambda _msg: dialog.accept())

            def save(name: str, note: str, marker: str) -> None:
                run_task(
                    lambda: svc_save_mode(uuid, name, note, marker),
                    done=refresh,
                    failed=lambda m: ErrorDialog(self._owner, "Could not save", m).exec(),
                )

            def drop(name: str) -> None:
                run_task(
                    lambda: svc_delete_mode(uuid, name),
                    done=refresh,
                    failed=lambda m: ErrorDialog(self._owner, "Could not delete", m).exec(),
                )

            def diff(name: str) -> None:
                run_task(
                    lambda: svc_mode_diff(uuid, name),
                    done=lambda text: DiffDialog(
                        self._owner, f"{snap.name}: current vs '{name}'", text
                    ).exec(),
                    failed=lambda m: ErrorDialog(self._owner, "Could not compare", m).exec(),
                )

            dialog.switch_requested.connect(switch)
            dialog.save_requested.connect(save)
            dialog.delete_requested.connect(drop)
            dialog.diff_requested.connect(diff)
            dialog.exec()

        run_task(
            lambda: svc_list_modes(uuid),
            done=show,
            failed=lambda m: ErrorDialog(self._owner, "libvirt error", m).exec(),
        )

    def _flatten(self, snap: DomainSnapshot) -> None:
        """Pull a linked clone's backing image in, so it stands on its own."""
        uuid = snap.uuid

        def ask(chain: dict) -> None:
            if not chain:
                ErrorDialog(
                    self._owner, "Nothing to flatten",
                    f"{snap.name} has no disk layered on another image, so it "
                    "already stands alone.",
                ).exec()
                return
            devs = sorted(chain)
            dialog = ChoiceDialog(
                self._owner, "Flatten a disk", "disk", devs,
                note="Copies the shared base image into this machine's own "
                     "overlay. Afterwards the template can be deleted, at the "
                     "cost of the space the sharing was saving. The machine "
                     "must be running, and stays usable while it works.",
            )
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return
            dev = dialog.combo.currentText()
            self.machines.subtitle.setText(f"flattening {snap.name} {dev}…")
            run_task(
                lambda: svc_flatten_disk(uuid, dev),
                done=lambda msg: (self.machines.subtitle.setText(msg),
                                  self.worker.poke()),
                failed=lambda m: ErrorDialog(self._owner, "Flatten failed", m).exec(),
            )

        run_task(
            lambda: svc_backing_chain(uuid),
            done=ask,
            failed=lambda m: ErrorDialog(self._owner, "libvirt error", m).exec(),
        )

    def _edit_os_icon(self, snap: DomainSnapshot) -> None:
        dialog = OsIconDialog(self._owner, snap.name, snap.os_key, snap.os_icon_override)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        key = dialog.chosen_key()
        run_task(
            lambda: svc_set_os_icon(snap.uuid, key),
            done=lambda _: self.worker.poke(),
            failed=lambda m: ErrorDialog(self._owner, "Couldn't set the icon", m).exec(),
        )

    def _fetch_os_logos(self) -> None:
        """Fetch any missing OS logos once, in the background.

        Only runs when the feature is on; everything still works offline from
        the host icon theme and our own drawn glyphs.
        """
        from .data.oslogos import (LogoDownloader, forget_cached_pixmaps,
                                   missing_downloads)
        from .pages.settings import os_icons_enabled

        if not os_icons_enabled():
            return
        keys = {d.os_key for d in self._domains if d.os_key}
        todo = missing_downloads(keys | {"windows", "linux"})
        if not todo:
            return
        downloader = LogoDownloader(todo)
        self._logo_downloader = downloader

        def arrived(_keys) -> None:
            from shiboken6 import isValid

            # This lands from a download thread, which can finish after the
            # window it belongs to has gone - the Python wrapper outlives the
            # C++ widget, and touching it raises out of the event loop.
            if not isValid(self.machines):
                return
            forget_cached_pixmaps()
            self.machines.refresh_cards()

        connect_guarded(downloader.fetched, arrived)
        downloader.start()

    def _set_template(self, snap: DomainSnapshot, on: bool) -> None:
        if on and not self._confirm(
            "Mark as template",
            f"Templates never start; linked clones share {snap.name}'s disks "
            "as read-only backing files, so the template must stay unchanged.",
            "Mark as template",
        ):
            return
        run_task(
            lambda: svc_set_template(snap.uuid, on),
            done=lambda _: self.worker.poke(),
            failed=lambda m: ErrorDialog(self._owner, "Template change failed", m).exec(),
        )

    def _linked_clone(self, snap: DomainSnapshot) -> None:
        dialog = CloneDialog(self._owner, snap.name)
        dialog.setWindowTitle("Linked clone")
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        new_name = dialog.name.text().strip()
        run_task(
            lambda: svc_linked_clone(snap.uuid, new_name),
            done=lambda _: self.worker.poke(),
            failed=lambda m: ErrorDialog(self._owner, "Linked clone failed", m).exec(),
        )

    # -- scheduled snapshots

    def _edit_schedule(self, snap: DomainSnapshot) -> None:
        current = self.stats.schedule_for(snap.uuid)
        dialog = ScheduleDialog(self._owner, snap.name, current)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        if dialog.enabled.isChecked():
            self.stats.set_schedule(
                snap.uuid,
                dialog.interval_seconds(),
                dialog.keep.value(),
                dialog.external.isChecked(),
            )
        else:
            self.stats.clear_schedule(snap.uuid)

    def _run_schedules(self) -> None:
        import time as _time

        from .scheduler import external_scheduler_active, snapshots_due

        if external_scheduler_active():
            return  # the --daemon service is handling these
        known = {d.uuid for d in self._domains}
        for uuid, keep, external in snapshots_due(
            self.stats.schedules(), known, _time.time()
        ):
            self.stats.mark_schedule_run(uuid)
            name = "auto-" + _time.strftime("%Y%m%d-%H%M%S")

            def work(u=uuid, n=name, ext=external, k=keep):
                svc_create_snapshot(u, n, "scheduled snapshot", ext)
                return svc_prune_snapshots(u, "auto-", k)

            vm_name = next((d.name for d in self._domains if d.uuid == uuid), uuid)
            run_task(
                work,
                done=lambda pruned, v=vm_name: self.machines.subtitle.setText(
                    f"snapshotted {v}"
                    + (f" · pruned {pruned}" if pruned else "")
                ),
                failed=lambda m, v=vm_name: self.machines.show_action_error(
                    f"scheduled snapshot of {v}: {m}"
                ),
            )

    def _run_wake_schedules(self) -> None:
        import time as _time

        from .scheduler import external_scheduler_active, wake_actions

        if external_scheduler_active():
            return  # the --daemon service is handling these
        now = _time.localtime()
        by_uuid = {d.uuid: d for d in self._domains}
        actions = wake_actions(
            self.stats.wake_schedules(),
            {u: (d.state, d.is_template) for u, d in by_uuid.items()},
            _time.strftime("%H:%M", now),
            _time.strftime("%Y-%m-%d", now),
            now.tm_wday < 5,
        )
        for uuid, action, key in actions:
            d = by_uuid[uuid]
            self.stats.mark_wake_fired(uuid, key)
            self._domain_action(uuid, action)
            what = "started" if action == "start" else "shutting down"
            self._notify(
                f"{d.name} {what}",
                f"Power schedule ({_time.strftime('%H:%M', now)}).",
            )

    def _edit_wake_schedule(self, snap: DomainSnapshot) -> None:
        dialog = WakeScheduleDialog(
            self._owner, snap.name, self.stats.wake_schedule_for(snap.uuid)
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        start_hm, stop_hm, days = dialog.result_schedule()
        if not start_hm and not stop_hm:
            self.stats.clear_wake_schedule(snap.uuid)
        else:
            self.stats.set_wake_schedule(snap.uuid, start_hm, stop_hm, days)

    def _edit_on_crash(self, snap: DomainSnapshot) -> None:
        def show(current: str) -> None:
            enable = current != "restart"
            if self._confirm(
                "Auto-restart on crash",
                f"{snap.name} currently {'restarts' if not enable else 'stays off'} "
                f"after a crash. "
                + ("Enable automatic restart? libvirt enforces this even when "
                   "vmmanager isn't running." if enable
                   else "Disable automatic restart?"),
                "Enable" if enable else "Disable",
            ):
                run_task(
                    lambda: svc_set_on_crash(snap.uuid, enable),
                    done=lambda _: self.worker.poke(),
                    failed=lambda m: ErrorDialog(self._owner, "Change failed", m).exec(),
                )

        run_task(
            lambda: svc_get_on_crash(snap.uuid),
            done=show,
            failed=lambda m: ErrorDialog(self._owner, "libvirt error", m).exec(),
        )

    def _edit_usb_rules(self, snap: DomainSnapshot) -> None:
        rules = self.stats.usb_rules_for(snap.uuid)

        def show(devices) -> None:
            usb = [d for d in devices if d.kind == "usb"]
            dialog = UsbRulesDialog(self._owner, snap.name, usb, rules)
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return
            self.stats.set_usb_rules(snap.uuid, dialog.chosen())
            self._usb_tick()  # apply immediately if the device is here now

        run_task(
            svc_list_host_devices,
            done=show,
            failed=lambda m: ErrorDialog(self._owner, "libvirt error", m).exec(),
        )

    def _usb_tick(self) -> None:
        rules = self.stats.usb_rules()
        if not rules:
            return

        def act(state) -> None:
            present, running, attached = state
            for uuid, ident in usb_auto_attach_plan(
                rules, present, running, attached
            ):
                name = next(
                    (d.name for d in self._domains if d.uuid == uuid), uuid
                )

                def attached_now(_msg, u=uuid, n=name, i=ident) -> None:
                    self.machines.subtitle.setText(f"usb {i} → {n}")
                    self.stats.record_event(u, "usb-auto-attach", i)

                run_task(
                    lambda u=uuid, i=ident: svc_attach_hostdev(u, "usb", i),
                    done=attached_now,
                    failed=lambda m: log.warning("usb auto-attach: %s", m),
                )

        run_task(svc_usb_watch_state, done=act, failed=lambda _m: None)

    def _export_vm(self, snap: DomainSnapshot) -> None:
        dest = QFileDialog.getExistingDirectory(self._owner, "Export into folder")
        if not dest:
            return
        self.machines.subtitle.setText(f"exporting {snap.name}…")
        run_task(
            lambda: svc_export_vm(snap.uuid, dest),
            done=lambda folder: self.machines.subtitle.setText(f"exported to {folder}"),
            failed=lambda m: ErrorDialog(self._owner, "Export failed", m).exec(),
        )

    def _import_backup(self) -> None:
        folder = QFileDialog.getExistingDirectory(self._owner, "Choose backup folder")
        if not folder:
            return
        self.machines.subtitle.setText("importing backup…")
        run_task(
            lambda: svc_import_backup(folder, "default"),
            done=lambda name: (
                self.machines.subtitle.setText(f"imported {name}"),
                self.worker.poke(),
            ),
            failed=lambda m: ErrorDialog(self._owner, "Import failed", m).exec(),
        )

    def _restore_backup(self) -> None:
        folder = QFileDialog.getExistingDirectory(
            self._owner, "Choose any backup in the chain"
        )
        if not folder:
            return
        self.machines.subtitle.setText("rebuilding backup chain…")
        run_task(
            lambda: svc_restore_backup(folder, "default"),
            done=lambda name: (
                self.machines.subtitle.setText(f"restored as {name}"),
                self.worker.poke(),
            ),
            failed=lambda m: (
                self.machines.subtitle.setText(""),
                ErrorDialog(self._owner, "Restore failed", m).exec(),
            ),
        )

    # -- command palette

    def _open_palette(self) -> None:
        from .palette import CommandPalette

        entries: list[tuple[str, object]] = []
        for d in self._domains:
            uuid = d.uuid
            entries.append((f"open  ·  {d.name}", lambda u=uuid: self._open_detail(u)))
            entries.append(
                (f"console  ·  {d.name}", lambda u=uuid: self._open_console(u))
            )
            if d.state == "running":
                entries.append(
                    (f"shut down  ·  {d.name}",
                     lambda u=uuid: self._domain_action(u, "shutdown"))
                )
            elif not d.is_template:
                entries.append(
                    (f"start  ·  {d.name}",
                     lambda u=uuid: self._domain_action(u, "start"))
                )
        # From the sidebar rather than a list of its own, which had already
        # drifted: Templates and Stacks were missing from it.
        for page in Sidebar.NAV:
            entries.append((f"go to  ·  {page}", lambda p=page: self._navigate(p)))
        entries.append(("new machine…", self._new_vm))
        CommandPalette(self._owner, entries).exec()

    def _open_console(self, uuid: str) -> None:
        self._open_detail(uuid)
        self.detail.tabs.setCurrentIndex(self.detail.TAB_CONSOLE)

    def _saved_profiles(self) -> list:
        """Hardware profiles, newest schema tolerated. A profile that will
        not load is skipped rather than stopping the wizard opening."""
        out = []
        for name, payload, _created in self.stats.profiles():
            try:
                out.append(from_json(payload))
            except Exception:  # noqa: BLE001 - one bad row is not fatal
                log.warning("could not load the profile %r", name)
        return out

    def _save_profile_from(self, uuid: str) -> None:
        """Capture a working machine's shape, to build the next one like it."""
        from PySide6.QtWidgets import QInputDialog

        name, ok = QInputDialog.getText(
            self._owner, "Save as profile",
            "A profile keeps this machine's firmware, chipset, CPU, memory, "
            "video and guest features - and none of its storage.\n\nName:",
        )
        if not ok or not name.strip():
            return

        def done(profile) -> None:
            self.stats.save_profile(profile.name, to_json(profile))
            self.machines.subtitle.setText(
                f"saved the profile '{profile.name}' - it is offered when you "
                "make a new machine"
            )

        run_task(
            lambda: svc_capture_profile(uuid, name),
            done=done,
            failed=lambda m: ErrorDialog(self._owner, "Could not save", m).exec(),
        )

    def _new_vm(self) -> None:
        host = self._host

        def show_dialog(result) -> None:
            networks, pools = result
            dialog = NewVmDialog(
                self._owner,
                networks,
                pools,
                host_cpus=host.cpus if host else 16,
                host_mem_mb=host.memory_mb if host else 65536,
                templates=[(d.name, d.uuid) for d in self._domains if d.is_template],
            )
            dialog.set_profiles(self._saved_profiles())
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return
            spec = dialog.spec()
            profile = dialog.chosen_profile()
            if profile is not None:
                spec = apply_to_spec(profile, spec)
            template = dialog.template_uuid()
            if template:
                # a clone is an overlay on the template's image, not a new build
                run_task(
                    lambda: svc_linked_clone(template, spec.name, spec.network),
                    done=lambda _: self.worker.poke(),
                    failed=lambda m: ErrorDialog(self._owner, "Clone failed", m).exec(),
                )
                return
            run_task(
                lambda: svc_create_vm(spec),
                done=lambda _: self.worker.poke(),
                failed=lambda m: ErrorDialog(self._owner, "Create failed", m).exec(),
            )

        def gather():
            networks = svc_list_network_names()
            pools = svc_list_pools()
            return networks, pools

        run_task(
            gather,
            done=show_dialog,
            failed=lambda m: ErrorDialog(self._owner, "libvirt error", m).exec(),
        )

    def closeEvent(self, event) -> None:
        from .pages.settings import close_to_tray

        if (
            not getattr(self, "_really_quit", False)
            and self.tray is not None
            and close_to_tray()
        ):
            self.hide()
            event.ignore()
            return
        for window in list(self._windows.values()):
            window.close()
        self.detail.shutdown()
        self.worker.stop()
        downloader = getattr(self, "_logo_downloader", None)
        if downloader is not None:
            downloader.stop()  # it checks between fetches; do not block on it
        self.stats.close()
        super().closeEvent(event)
