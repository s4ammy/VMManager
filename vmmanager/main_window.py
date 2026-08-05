from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
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
    ChoiceDialog,
    DiffDialog,
    CloneDetailsDialog,
    CloneDialog,
    ConfirmDialog,
    DeleteVmDialog,
    ErrorDialog,
    MigrateDialog,
    ModesDialog,
    OsIconDialog,
    ScheduleDialog,
    WakeScheduleDialog,
)
from .data.history import StatsStore
from .libvirt_service import (
    DomainSnapshot,
    HostSnapshot,
    PollWorker,
    current_uri,
    set_poll_seconds,
    set_uri,
    svc_clone,
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
    svc_migrate,
    svc_migrate_advanced,
    svc_prune_snapshots,
    svc_restore_from_file,
    svc_save_to_file,
    svc_set_on_crash,
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
from .pages.detail import DetailPage
from .pages.detail.window import MachineWindow
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
from .tasks import run_task
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
        self.themes = ThemesPage()
        for page in (self.machines, self.detail, self.templates_page,
                     self.stacks_page, self.storage,
                     self.networks, self.themes, self.settings):
            self.stack.addWidget(page)

        self.machines.action.connect(self._domain_action)
        self.machines.open_detail.connect(self._open_detail)
        self.machines.context.connect(self._show_menu)
        self.machines.new_vm.connect(self._new_vm)
        self.machines.restore_file.connect(self._restore_from_file)
        self.machines.import_backup.connect(self._import_backup)
        self.machines.bulk_action.connect(self._bulk_action)
        self.machines.health_updated.connect(self._on_health_alert)
        # One window per machine, keyed by uuid. See _pop_out.
        self._windows: dict[str, MachineWindow] = {}

        self.detail.back.connect(lambda: self._navigate("Machines"))
        self.detail.pop_out.connect(self._pop_out)
        self.detail.action.connect(self._domain_action)
        self.detail.menu_requested.connect(self._show_menu)
        self.detail.mode_switch.connect(self._switch_mode_direct)
        self.detail.modes_requested.connect(
            lambda uuid: self._edit_modes(
                next((d for d in self._domains if d.uuid == uuid), None)
            )
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
        QShortcut(QKeySequence("Ctrl+N"), self, activated=self._new_vm)
        QShortcut(QKeySequence("Ctrl+K"), self, activated=self._open_palette)

        self.worker.xml_changed.connect(self.stats.record_xml)
        self._schedule_timer = QTimer(self)
        self._schedule_timer.setInterval(60_000)
        self._schedule_timer.timeout.connect(self._run_schedules)
        self._schedule_timer.timeout.connect(self._run_wake_schedules)
        self._schedule_timer.start()
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
            "Storage": self.storage,
            "Networks": self.networks,
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
        page.menu_requested.connect(self._show_menu)
        page.mode_switch.connect(self._switch_mode_direct)
        page.modes_requested.connect(
            lambda u: self._edit_modes(
                next((d for d in self._domains if d.uuid == u), None)
            )
        )
        page.back.connect(window.close)
        page.pop_out.connect(window.close)  # nothing to pop out of a window
        window.closed.connect(self._windows.pop)
        self._windows[uuid] = window
        window.show()
        self._load_modes_for(uuid)

        # The machine has moved out of the main window, so send that back to
        # the list rather than leaving a copy of it behind.
        if self.stack.currentWidget() is self.detail and self.detail.uuid == uuid:
            self._navigate("Machines")

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
        run_task(
            lambda: svc_domain_action(uuid, op),
            done=lambda _: self.worker.poke(),
            failed=self.machines.show_action_error,
        )

    def _snap_for(self, uuid: str) -> DomainSnapshot | None:
        return next((d for d in self._domains if d.uuid == uuid), None)

    def _show_menu(self, uuid: str, global_pos) -> None:
        snap = self._snap_for(uuid)
        if snap is None:
            return
        running = snap.state == "running"
        paused = snap.state in ("paused", "suspended")

        menu = QMenu(self)
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
        if not running:
            menu.addAction("Export backup…", lambda: self._export_vm(snap))
        menu.addAction("Migrate…", lambda: self._migrate(snap))
        menu.addSeparator()
        if running:
            menu.addAction("Open console (virt-viewer)", lambda: open_external(uuid, "viewer"))
        menu.addAction("Open in virt-manager", lambda: open_external(uuid, "manager"))
        menu.addSeparator()
        menu.addAction("Delete…", lambda: self._delete(snap))
        menu.exec(global_pos)

    def _confirm(self, title: str, body: str, ok_text: str) -> bool:
        if not confirmations_enabled():
            return True
        confirm = ConfirmDialog(self, title, body, ok_text)
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
            self, "Save machine state", f"{snap.name}.vmstate", "VM state (*.vmstate)"
        )
        if not path:
            return
        uuid = snap.uuid
        run_task(
            lambda: svc_save_to_file(uuid, path),
            done=lambda _: self.worker.poke(),
            failed=lambda m: ErrorDialog(self, "Save failed", m).exec(),
        )

    def _restore_from_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Restore machine state", "", "VM state (*.vmstate);;All files (*)"
        )
        if not path:
            return
        run_task(
            lambda: svc_restore_from_file(path),
            done=lambda _: self.worker.poke(),
            failed=lambda m: ErrorDialog(self, "Restore failed", m).exec(),
        )

    def _migrate(self, snap: DomainSnapshot) -> None:
        others = [u for u in saved_connections() if u != current_uri()]
        dialog = MigrateDialog(self, snap.name, others)
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
            failed=lambda m: ErrorDialog(self, "Migration failed", m).exec(),
        )

    def _clone(self, snap: DomainSnapshot) -> None:
        if snap.state != "shutoff":
            ErrorDialog(
                self, "Can't clone", "Shut the machine down before cloning."
            ).exec()
            return

        def show(disks) -> None:
            dialog = CloneDetailsDialog(self, snap.name, disks)
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return
            new_name = dialog.name.text().strip()
            plan = dialog.disk_plan()
            preserve = dialog.preserve_macs.isChecked()
            run_task(
                lambda: svc_clone_advanced(snap.uuid, new_name, plan, preserve),
                done=lambda _: self.worker.poke(),
                failed=lambda m: ErrorDialog(self, "Clone failed", m).exec(),
            )

        run_task(
            lambda: svc_list_domain_disks(snap.uuid),
            done=show,
            failed=lambda m: ErrorDialog(self, "libvirt error", m).exec(),
        )

    def _delete(self, snap: DomainSnapshot) -> None:
        def show(disks) -> None:
            dialog = DeleteVmDialog(self, snap.name, disks)
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return
            paths = dialog.paths_to_delete()
            run_task(
                lambda: svc_delete(snap.uuid, paths),
                done=lambda _: self.worker.poke(),
                failed=lambda m: ErrorDialog(self, "Delete failed", m).exec(),
            )

        run_task(
            lambda: svc_list_domain_disks(snap.uuid),
            done=show,
            failed=lambda m: ErrorDialog(self, "libvirt error", m).exec(),
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
        elif op == "tag":
            from PySide6.QtWidgets import QInputDialog

            text, ok = QInputDialog.getText(
                self, "Tag machines",
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
            self, "Tags", f"Tags for {snap.name} (comma-separated):",
            text=", ".join(snap.tags),
        )
        if not ok:
            return
        tags = tuple(t.strip() for t in text.split(",") if t.strip())
        run_task(
            lambda: svc_set_tags(snap.uuid, tags),
            done=lambda _: self.worker.poke(),
            failed=lambda m: ErrorDialog(self, "Tagging failed", m).exec(),
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

        confirm = ConfirmDialog(
            self, f"Switch {snap.name} to '{name}'", body, "Switch",
        )
        if confirm.exec() != QDialog.DialogCode.Accepted:
            return

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
            failed=lambda m: ErrorDialog(self, "Could not switch", m).exec(),
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
            self, f"{name}: the marker was not updated",
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
                self, "Could not write the marker", m).exec(),
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
            dialog = ModesDialog(self, snap.name, modes, snap.state != "shutoff")

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
                    failed=lambda m: ErrorDialog(self, "Could not save", m).exec(),
                )

            def drop(name: str) -> None:
                run_task(
                    lambda: svc_delete_mode(uuid, name),
                    done=refresh,
                    failed=lambda m: ErrorDialog(self, "Could not delete", m).exec(),
                )

            def diff(name: str) -> None:
                run_task(
                    lambda: svc_mode_diff(uuid, name),
                    done=lambda text: DiffDialog(
                        self, f"{snap.name}: current vs '{name}'", text
                    ).exec(),
                    failed=lambda m: ErrorDialog(self, "Could not compare", m).exec(),
                )

            dialog.switch_requested.connect(switch)
            dialog.save_requested.connect(save)
            dialog.delete_requested.connect(drop)
            dialog.diff_requested.connect(diff)
            dialog.exec()

        run_task(
            lambda: svc_list_modes(uuid),
            done=show,
            failed=lambda m: ErrorDialog(self, "libvirt error", m).exec(),
        )

    def _flatten(self, snap: DomainSnapshot) -> None:
        """Pull a linked clone's backing image in, so it stands on its own."""
        uuid = snap.uuid

        def ask(chain: dict) -> None:
            if not chain:
                ErrorDialog(
                    self, "Nothing to flatten",
                    f"{snap.name} has no disk layered on another image, so it "
                    "already stands alone.",
                ).exec()
                return
            devs = sorted(chain)
            dialog = ChoiceDialog(
                self, "Flatten a disk", "disk", devs,
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
                failed=lambda m: ErrorDialog(self, "Flatten failed", m).exec(),
            )

        run_task(
            lambda: svc_backing_chain(uuid),
            done=ask,
            failed=lambda m: ErrorDialog(self, "libvirt error", m).exec(),
        )

    def _edit_os_icon(self, snap: DomainSnapshot) -> None:
        dialog = OsIconDialog(self, snap.name, snap.os_key, snap.os_icon_override)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        key = dialog.chosen_key()
        run_task(
            lambda: svc_set_os_icon(snap.uuid, key),
            done=lambda _: self.worker.poke(),
            failed=lambda m: ErrorDialog(self, "Couldn't set the icon", m).exec(),
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
            forget_cached_pixmaps()
            self.machines.refresh_cards()

        downloader.fetched.connect(arrived)
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
            failed=lambda m: ErrorDialog(self, "Template change failed", m).exec(),
        )

    def _linked_clone(self, snap: DomainSnapshot) -> None:
        dialog = CloneDialog(self, snap.name)
        dialog.setWindowTitle("Linked clone")
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        new_name = dialog.name.text().strip()
        run_task(
            lambda: svc_linked_clone(snap.uuid, new_name),
            done=lambda _: self.worker.poke(),
            failed=lambda m: ErrorDialog(self, "Linked clone failed", m).exec(),
        )

    # -- scheduled snapshots

    def _edit_schedule(self, snap: DomainSnapshot) -> None:
        current = self.stats.schedule_for(snap.uuid)
        dialog = ScheduleDialog(self, snap.name, current)
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

        now = _time.time()
        known = {d.uuid for d in self._domains}
        for uuid, interval_s, keep, external, last_run in self.stats.schedules():
            if uuid not in known or now - last_run < interval_s:
                continue
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

        now = _time.localtime()
        hm = _time.strftime("%H:%M", now)
        date = _time.strftime("%Y-%m-%d", now)
        is_weekday = now.tm_wday < 5
        by_uuid = {d.uuid: d for d in self._domains}
        for uuid, start_hm, stop_hm, days, last_fired in self.stats.wake_schedules():
            d = by_uuid.get(uuid)
            if d is None:
                continue
            if days == "weekdays" and not is_weekday:
                continue
            if days == "weekends" and is_weekday:
                continue
            if start_hm and hm == start_hm:
                key = f"{date} {hm} start"
                if last_fired != key and d.state == "shutoff" and not d.is_template:
                    self.stats.mark_wake_fired(uuid, key)
                    self._domain_action(uuid, "start")
                    self._notify(f"{d.name} started", f"Power schedule ({hm}).")
            elif stop_hm and hm == stop_hm:
                key = f"{date} {hm} stop"
                if last_fired != key and d.state == "running":
                    self.stats.mark_wake_fired(uuid, key)
                    self._domain_action(uuid, "shutdown")
                    self._notify(f"{d.name} shutting down", f"Power schedule ({hm}).")

    def _edit_wake_schedule(self, snap: DomainSnapshot) -> None:
        dialog = WakeScheduleDialog(
            self, snap.name, self.stats.wake_schedule_for(snap.uuid)
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
                    failed=lambda m: ErrorDialog(self, "Change failed", m).exec(),
                )

        run_task(
            lambda: svc_get_on_crash(snap.uuid),
            done=show,
            failed=lambda m: ErrorDialog(self, "libvirt error", m).exec(),
        )

    def _export_vm(self, snap: DomainSnapshot) -> None:
        dest = QFileDialog.getExistingDirectory(self, "Export into folder")
        if not dest:
            return
        self.machines.subtitle.setText(f"exporting {snap.name}…")
        run_task(
            lambda: svc_export_vm(snap.uuid, dest),
            done=lambda folder: self.machines.subtitle.setText(f"exported to {folder}"),
            failed=lambda m: ErrorDialog(self, "Export failed", m).exec(),
        )

    def _import_backup(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Choose backup folder")
        if not folder:
            return
        self.machines.subtitle.setText("importing backup…")
        run_task(
            lambda: svc_import_backup(folder, "default"),
            done=lambda name: (
                self.machines.subtitle.setText(f"imported {name}"),
                self.worker.poke(),
            ),
            failed=lambda m: ErrorDialog(self, "Import failed", m).exec(),
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
        CommandPalette(self, entries).exec()

    def _open_console(self, uuid: str) -> None:
        self._open_detail(uuid)
        self.detail.tabs.setCurrentIndex(self.detail.TAB_CONSOLE)

    def _new_vm(self) -> None:
        host = self._host

        def show_dialog(result) -> None:
            networks, pools = result
            dialog = NewVmDialog(
                self,
                networks,
                pools,
                host_cpus=host.cpus if host else 16,
                host_mem_mb=host.memory_mb if host else 65536,
                templates=[(d.name, d.uuid) for d in self._domains if d.is_template],
            )
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return
            spec = dialog.spec()
            template = dialog.template_uuid()
            if template:
                # a clone is an overlay on the template's image, not a new build
                run_task(
                    lambda: svc_linked_clone(template, spec.name, spec.network),
                    done=lambda _: self.worker.poke(),
                    failed=lambda m: ErrorDialog(self, "Clone failed", m).exec(),
                )
                return
            run_task(
                lambda: svc_create_vm(spec),
                done=lambda _: self.worker.poke(),
                failed=lambda m: ErrorDialog(self, "Create failed", m).exec(),
            )

        def gather():
            networks = svc_list_network_names()
            pools = svc_list_pools()
            return networks, pools

        run_task(
            gather,
            done=show_dialog,
            failed=lambda m: ErrorDialog(self, "libvirt error", m).exec(),
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
        self.stats.close()
        super().closeEvent(event)
