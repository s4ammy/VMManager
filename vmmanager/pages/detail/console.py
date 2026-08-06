"""Console tab: VNC and SPICE clients, detaching, paste and keys."""

from __future__ import annotations

from .common import *  # noqa: F403 - shared imports for the tab modules


def pick_display(graphics, spice_available: bool):
    """Which of a machine's displays to connect to.

    SPICE first where this build can speak it: it carries the clipboard and
    the guest resize, and VNC carries neither. Preferring VNC - as this did
    - meant adding a SPICE display changed nothing, and the clipboard went
    on quietly not working with no way to tell why.
    """
    spice = next(
        (g for g in graphics
         if g.type == "spice" and (g.port > 0 or g.tls_port > 0)),
        None,
    )
    vnc = next(
        (g for g in graphics if g.type == "vnc" and (g.port > 0 or g.socket)),
        None,
    )
    if spice is not None and spice_available:
        return spice
    # no spice-glib here, so VNC even though it can do less; a SPICE-only
    # machine still returns its display, and the client says what is missing
    return vnc or spice


def dropped_files(mime) -> list[str]:
    """Local file paths out of a drag's mime data; anything else is skipped."""
    if not mime.hasUrls():
        return []
    return [u.toLocalFile() for u in mime.urls() if u.isLocalFile()]


def drop_destination(os_key: str, filename: str) -> str:
    """Where a dropped file lands inside the guest.

    /tmp exists on every Unix guest; Windows has no /tmp, so files go to
    the Public profile, which every account can read.
    """
    if os_key == "windows":
        return f"C:\\Users\\Public\\{filename}"
    return f"/tmp/{filename}"


class ConsoleMixin:
    """Mixed into DetailPage; expects its attributes."""

    _display_health = None  # last read of what the definition does for us
    _hint_base = ""  # the hint without the display note, so it can be recomposed

    def _build_console(self) -> QWidget:
        from PySide6.QtWidgets import QStackedWidget

        page = QWidget()
        box = QVBoxLayout(page)
        box.setContentsMargins(0, 12, 0, 0)
        box.setSpacing(10)
        self.vnc = VncClient()
        self.vnc.state_changed.connect(self._console_state_changed)
        self.vnc.password_required.connect(self._vnc_ask_password)
        self.vnc.grab_changed.connect(self._grab_changed)
        self.spice = SpiceClient()
        self.spice.state_changed.connect(self._console_state_changed)
        self.spice.mouse_mode_changed.connect(self._spice_mouse_hint)
        self.spice.capture_changed.connect(self._spice_capture_changed)
        self.spice.grab_changed.connect(self._grab_changed)
        self.spice.usb_changed.connect(self._on_usb_changed)
        self.spice.monitors_changed.connect(self._monitors_changed)
        self.console_stack = QStackedWidget()
        self.console_stack.setMinimumHeight(300)
        self.console_stack.addWidget(self.vnc)
        self.console_stack.addWidget(self.spice)
        box.addWidget(self.console_stack, 1)
        # files dropped on the console are sent into the guest via the agent
        self.console_stack.setAcceptDrops(True)
        self.console_stack.installEventFilter(self)
        self._tunnel: SSHTunnel | None = None

        # Four controls, not seven: the everyday ones stay out, the rest live
        # behind "⋯" so the row never squeezes its own labels on a narrow
        # window. The hint is allowed to be clipped instead of pushing buttons.
        from PySide6.QtWidgets import QSizePolicy

        row = QHBoxLayout()
        row.setSpacing(8)
        self.console_hint = QLabel("connect starts automatically while running")
        self.console_hint.setObjectName("ConsoleHint")
        self.console_hint.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred
        )
        row.addWidget(self.console_hint, 1)
        # One button, two jobs: add a display when there is none to connect
        # to, offer the display fixes when the one there is holds the console
        # back. Both are "the machine's definition is why this is not working".
        self.console_fix = QPushButton("Add VNC display")
        self.console_fix.setProperty("class", "GhostButton")
        self._console_fix_job = "display"
        self.console_fix.clicked.connect(self._console_fix_clicked)
        self.console_fix.hide()
        row.addWidget(self.console_fix)
        # Only shown for a guest that actually has more than one head, so
        # the usual single-monitor console is unchanged.
        self.monitor_combo = QComboBox()
        self.monitor_combo.setToolTip(
            "Which of the guest's monitors this view is painting"
        )
        self.monitor_combo.currentIndexChanged.connect(self._monitor_chosen)
        self.monitor_combo.hide()
        row.addWidget(self.monitor_combo)
        keys_btn = QPushButton("Send key ▾")
        keys_btn.setProperty("class", "GhostButton")
        keys_btn.clicked.connect(lambda: self._keys_menu(keys_btn))
        row.addWidget(keys_btn)
        paste_btn = QPushButton("Paste ▾")
        paste_btn.setProperty("class", "GhostButton")
        paste_btn.clicked.connect(lambda: self._paste_menu(paste_btn))
        row.addWidget(paste_btn)
        detach_btn = QPushButton("Detach ⧉")
        detach_btn.setProperty("class", "GhostButton")
        detach_btn.setToolTip("Console in its own window - F11 for fullscreen")
        detach_btn.clicked.connect(self._detach_console)
        row.addWidget(detach_btn)
        more_btn = QPushButton("⋯")
        more_btn.setProperty("class", "IconButton")
        more_btn.setToolTip(
            "Reconnect, display setup, keyboard grab, screenshot, external viewer"
        )
        more_btn.clicked.connect(lambda: self._console_more_menu(more_btn))
        row.addWidget(more_btn)
        box.addLayout(row)
        return page

    def eventFilter(self, obj, event) -> bool:
        if obj is getattr(self, "console_stack", None):
            from PySide6.QtCore import QEvent

            if event.type() in (QEvent.Type.DragEnter, QEvent.Type.DragMove):
                if dropped_files(event.mimeData()):
                    event.acceptProposedAction()
                    return True
            elif event.type() == QEvent.Type.Drop:
                paths = dropped_files(event.mimeData())
                if paths:
                    event.acceptProposedAction()
                    self._send_dropped(paths)
                    return True
        return super().eventFilter(obj, event)

    def _send_dropped(self, paths: list[str]) -> None:
        if not self.uuid:
            return
        if not self._snap or self._snap.state != "running":
            self.console_hint.setText(
                "dropped file not sent - the machine is not running"
            )
            return
        uuid = self.uuid
        os_key = self._snap.os_key
        remaining = list(paths)
        total = len(paths)

        def send_next() -> None:
            if not remaining:
                return
            local = remaining.pop(0)
            dest = drop_destination(os_key, local.rsplit("/", 1)[-1])
            n = total - len(remaining)
            self.console_hint.setText(
                f"sending {local.rsplit('/', 1)[-1]} → {dest}"
                + (f" ({n}/{total})" if total > 1 else "")
                + "…"
            )

            def done(msg) -> None:
                self.console_hint.setText(str(msg))
                send_next()

            run_task(
                lambda: svc_send_file(uuid, local, dest),
                done=done,
                failed=lambda m: self.console_hint.setText(
                    "send failed - it needs qemu-guest-agent in the guest: "
                    f"{m}"
                ),
            )

        send_next()

    def _console_more_menu(self, anchor: QPushButton) -> None:
        menu = QMenu(self)
        menu.addAction("Reconnect", lambda: self._connect_console(forced=True))
        menu.addAction("Display setup…", self._display_setup)
        client = self._active_client()
        if client is not None and getattr(client, "grab", None) is not None:
            if client.grab.held:
                menu.addAction("Release the keyboard", client.release_input)
            else:
                menu.addAction("Send every key to the guest", client.take_input)
        self._add_usb_menu(menu)
        menu.addAction("Save screenshot…", self._save_screenshot)
        menu.addAction(
            "Open in virt-viewer",
            lambda: self.uuid and open_external(self.uuid, "viewer"),
        )
        menu.exec(anchor.mapToGlobal(anchor.rect().bottomLeft()))

    def _add_usb_menu(self, menu: QMenu) -> None:
        """Send a host USB device to the guest for as long as it is plugged in.

        Different from handing the device over as hardware: this goes over
        the SPICE connection, the host keeps ownership, and unplugging it
        gives it straight back. Needs a USB redirection channel on the
        machine, which is what the empty case points at.
        """
        client = self._active_client()
        if not isinstance(client, SpiceClient) or not client.active:
            return
        devices = client.usb_devices()
        # Built with the parent menu as its owner rather than by
        # menu.addMenu("…"): that hands back a submenu nothing holds a
        # reference to, and it can be collected before the menu is shown.
        usb = QMenu("USB device", menu)
        menu.addMenu(usb)
        if not devices:
            nothing = usb.addAction("No USB devices to redirect")
            nothing.setEnabled(False)
            return
        for device, label, connected in devices:
            action = usb.addAction(
                f"{'✓ ' if connected else ''}{label}",
                lambda _=False, d=device, c=connected: self._redirect_usb(d, not c),
            )
            action.setCheckable(True)
            action.setChecked(connected)

    def _redirect_usb(self, device, connect: bool) -> None:
        client = self._active_client()
        if not isinstance(client, SpiceClient):
            return
        self.console_hint.setText(
            "sending the device to the guest…" if connect
            else "taking the device back…"
        )
        client.redirect_usb(device, connect)

    def _on_usb_changed(self, error: str) -> None:
        if error:
            self._set_hint(f"USB redirection failed: {error}")
            return
        self._set_hint(self._connected_hint())

    def _active_client(self):
        if self.spice.active:
            return self.spice
        if self.vnc.active:
            return self.vnc
        return None

    def _detach_console(self) -> None:
        if getattr(self, "_detached", None) is not None:
            self._detached.showNormal()  # in case it was minimised
            self._detached.raise_()
            self._detached.activateWindow()
            return
        client = self.console_stack.currentWidget()
        name = self._snap.name if self._snap else "console"
        window = DetachedConsoleWindow(client, name)
        self._detached = window
        self._detached_client = client

        def reattach() -> None:
            # No show() needed on the way back, checked rather than assumed:
            # setCurrentWidget shows it while the stack is on screen, and
            # switching to the tab shows it when the stack is not.
            self.console_stack.addWidget(client)
            self.console_stack.setCurrentWidget(client)
            self._detached = None
            self._detached_client = None

        window.closed.connect(reattach)
        window.show()
        # Without these the window can open behind the one it was launched
        # from - the console is detached, and nothing appears to have
        # happened. Compositors that prevent focus stealing do exactly that.
        window.raise_()
        window.activateWindow()
        client.setFocus()

    def _close_console(self) -> None:
        self.vnc.close_connection()
        self.spice.close_connection()
        if self._tunnel is not None:
            self._tunnel.stop()
            self._tunnel.deleteLater()
            self._tunnel = None

    def _monitors_changed(self, monitors: list) -> None:
        """Offer a chooser once the guest has more than one head."""
        self.monitor_combo.blockSignals(True)
        self.monitor_combo.clear()
        for monitor in monitors:
            self.monitor_combo.addItem(f"monitor {monitor + 1}", monitor)
        current = self.spice.current_monitor()
        index = self.monitor_combo.findData(current)
        self.monitor_combo.setCurrentIndex(max(index, 0))
        self.monitor_combo.blockSignals(False)
        self.monitor_combo.setVisible(len(monitors) > 1)

    def _monitor_chosen(self, index: int) -> None:
        if index < 0:
            return
        monitor = self.monitor_combo.itemData(index)
        if monitor is not None:
            self.spice.show_monitor(int(monitor))

    def _keys_menu(self, anchor: QPushButton) -> None:
        if not self.uuid:
            return
        menu = QMenu(self)
        for combo in KEY_COMBOS:
            menu.addAction(combo, lambda c=combo: self._send_combo(c))
        menu.exec(anchor.mapToGlobal(anchor.rect().bottomLeft()))

    def _paste_menu(self, anchor: QPushButton) -> None:
        client = self._active_client()
        text = QApplication.clipboard().text()
        menu = QMenu(self)
        clip = menu.addAction(
            "Into guest clipboard (needs guest agent)",
            lambda: client and client.send_clipboard(text),
        )
        keys = menu.addAction(
            "As keystrokes",
            lambda: isinstance(client, VncClient) and client.type_text(text),
        )
        clip.setEnabled(bool(text) and client is not None)
        keys.setEnabled(bool(text) and isinstance(client, VncClient))
        menu.exec(anchor.mapToGlobal(anchor.rect().bottomLeft()))

    def _send_combo(self, combo: str) -> None:
        if self.vnc.active and combo in _COMBO_KEYSYMS:
            self.vnc.send_combo(_COMBO_KEYSYMS[combo])
            return
        if self.spice.active and combo in _COMBO_SCANCODES:
            self.spice.send_scancodes(_COMBO_SCANCODES[combo])
            return
        uuid = self.uuid
        run_task(
            lambda: svc_send_keys(uuid, KEY_COMBOS[combo]),
            failed=lambda m: ErrorDialog(self, "Send keys failed", m).exec(),
        )

    def _connect_console(self, forced: bool = False) -> None:
        """`forced` comes from the Reconnect action, which ignores the
        autoconnect preference."""
        from ...pages.settings import console_autoconnect

        self._close_console()
        if not forced and not console_autoconnect():
            self.console_hint.setText(
                "autoconnect is off, use Reconnect in the ⋯ menu"
            )
            return
        self.console_fix.hide()
        if not self.uuid or not self._snap or self._snap.state != "running":
            # The best time to change a display device, in fact: it only takes
            # effect on the next start anyway.
            self._display_health = None
            self._set_hint("machine is not running")
            self._check_display_health()
            return
        uuid = self.uuid

        def apply(graphics: list[GraphicsInfo]) -> None:
            if self.uuid != uuid or self.tabs.currentIndex() != self.TAB_CONSOLE:
                return
            display = pick_display(graphics, SPICE_AVAILABLE)
            if display is None:
                types = ", ".join(g.type for g in graphics) or "none"
                self.console_hint.setText(
                    f"no connectable display on this machine (has: {types})"
                )
                self._offer_fix("add-vnc", "Add VNC display")
                return
            self._vnc_target = display
            if display.type == "vnc" and display.has_password and not self._vnc_password:
                self._vnc_ask_password()
                return
            self._open_display(display)

        run_task(
            lambda: svc_graphics_info(uuid),
            done=apply,
            failed=lambda m: self.console_hint.setText(f"console unavailable: {m}"),
        )

    def _open_display(self, display: GraphicsInfo) -> None:
        client = self.spice if display.type == "spice" else self.vnc
        self.console_stack.setCurrentWidget(client)
        if display.type == "vnc" and display.socket:
            self.vnc.open_unix(display.socket, self._vnc_password)
            return
        ssh = ssh_target_of(current_uri())
        if ssh is None:
            from ..settings import console_tls

            tls_port = -1
            if display.type == "spice" and display.tls_port > 0 and (
                console_tls() or display.port <= 0  # TLS-only server
            ):
                tls_port = display.tls_port
            self._open_client(client, display.host, display.port, tls_port)
            return
        # remote host: the display listens on its loopback - tunnel to it.
        # The tunnel itself is the encryption, so the plain port is used
        # when there is one; a TLS-only display tunnels its TLS port.
        target, keyfile = ssh
        remote_port = display.port if display.port > 0 else display.tls_port
        tls_only = display.port <= 0
        self.console_hint.setText(f"opening ssh tunnel to {target}…")
        tunnel = SSHTunnel(target, display.host, remote_port, keyfile, parent=self)
        self._tunnel = tunnel
        tunnel.ready.connect(
            lambda port: self._open_client(
                client, "127.0.0.1",
                -1 if tls_only else port,
                port if tls_only else -1,
            )
        )
        tunnel.failed.connect(
            lambda m: self.console_hint.setText(f"tunnel failed: {m}")
        )
        tunnel.start()

    def _open_client(self, client, host: str, port: int,
                     tls_port: int = -1) -> None:
        if client is self.spice:
            client.open_tcp(host, port, tls_port=tls_port)
        else:
            client.open_tcp(host, port, self._vnc_password)

    def _set_hint(self, text: str) -> None:
        """The resting hint, with whatever the display check found appended.

        The note is composed on every set rather than appended to what is there,
        because the resting text is put back each time a grab or a capture ends
        - appending would stack up a copy of the note every time.
        """
        self._hint_base = text
        problems = self._display_health.problems() if self._display_health else []
        note = f" · {problems[0][1].lower()}" if problems else ""
        self.console_hint.setText(text + note)

    def _connected_hint(self) -> str:
        """The line under an idle, connected console."""
        from ...console.grab import grab_on_click, release_combo_name

        proto = "spice" if self._active_client() is self.spice else "vnc"
        tunneled = " · ssh tunnel" if self._tunnel is not None else ""
        how = (
            f"click the display to send every key to the guest, "
            f"{release_combo_name()} to release"
            if grab_on_click()
            else "click to type, Tab and arrows go to the guest"
        )
        return f"interactive {proto}{tunneled} · {how}"

    def _console_state_changed(self, state: str) -> None:
        client = self._active_client()
        if state == "connected":
            self._display_health = None  # re-read for whatever we just opened
            self._set_hint(self._connected_hint())
            if client is not None:
                client.setFocus()
            self._check_display_health()
        elif state == "connecting":
            self.console_hint.setText("connecting…")
        elif state == "closed":
            self.console_hint.setText("display closed")
        else:
            self.console_hint.setText(state)

    def _spice_mouse_hint(self, mode: int) -> None:
        from ...console.grab import release_combo_name

        if not self.spice.active:
            return
        if mode == SpiceClient.MOUSE_SERVER:
            self.console_hint.setText(
                "relative mouse, click the display to capture the pointer, "
                f"{release_combo_name()} to release"
            )
        else:
            self._set_hint(self._connected_hint())

    def _spice_capture_changed(self, captured: bool) -> None:
        from ...console.grab import release_combo_name

        if captured:
            self.console_hint.setText(
                "pointer captured: the guest owns your mouse · "
                f"{release_combo_name()} to release"
            )
        elif self.spice.active:
            self._set_hint(self._connected_hint())


    def _grab_changed(self, held: bool) -> None:
        client = self._active_client()
        if client is None:
            return
        if held:
            self.console_hint.setText(client.grab.hint())
        else:
            self._set_hint(self._connected_hint())

    # -- what the machine's own definition does to the console

    def _check_display_health(self) -> None:
        """After connecting, say whether the machine can do better than this.

        Installing drivers in the guest cannot help a device that has no
        accelerated driver to install, and this is where that gets said - next
        to the console someone is looking at while wondering why.
        """
        uuid = self.uuid

        def apply(health) -> None:
            if self.uuid != uuid:
                return
            self._display_health = health
            problems = health.problems()
            if not problems:
                return
            self._offer_fix("display", "Improve display…")
            self._set_hint(self._hint_base)

        run_task(
            lambda: svc_display_health(uuid), done=apply, failed=lambda _m: None
        )

    def _offer_fix(self, job: str, label: str) -> None:
        self._console_fix_job = job
        self.console_fix.setText(label)
        self.console_fix.show()

    def _console_fix_clicked(self) -> None:
        if self._console_fix_job == "add-vnc":
            self._add_vnc_display()
        else:
            self._display_setup()

    def _display_setup(self) -> None:
        uuid = self.uuid
        if not uuid:
            return
        name = self._snap.name if self._snap else "this machine"

        def show(health) -> None:
            if self.uuid != uuid:
                return
            self._display_health = health
            dialog = DisplayFixDialog(self, name, health)
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return
            from ...pages.settings import save_console_resize_guest

            save_console_resize_guest(dialog.resize_guest.isChecked())
            if dialog.actions:
                self._apply_display_fixes(uuid, health, dialog.actions)

        run_task(
            lambda: svc_display_health(uuid),
            done=show,
            failed=lambda m: ErrorDialog(self, "Couldn't read the display", m).exec(),
        )

    def _apply_display_fixes(self, uuid: str, health, keys: list[str]) -> None:
        """Each fix is one definition edit; they are applied in one go."""
        work = {
            "video": lambda: svc_set_video(uuid, health.best_video),
            "agent": lambda: svc_add_spice_agent_channel(uuid),
            "spice": lambda: (
                svc_add_display(uuid, "spice"),
                svc_add_spice_agent_channel(uuid),
                "SPICE display and agent channel added",
            )[-1],
            "tablet": lambda: svc_attach_input(uuid, "tablet", "usb"),
        }

        def apply_all() -> str:
            done = [work[k]() for k in keys if k in work]
            return " · ".join(str(d) for d in done)

        def finished(message: str) -> None:
            if self.uuid != uuid:
                return
            self.console_fix.hide()
            tail = (" Start it again to pick up the new display device."
                    if health.running else "")
            self.console_hint.setText(f"display updated - {message}{tail}")

        run_task(
            apply_all,
            done=finished,
            failed=lambda m: ErrorDialog(self, "Display change failed", m).exec(),
        )

    def _vnc_ask_password(self) -> None:
        dialog = VncPasswordDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            self.console_hint.setText("display needs a password")
            return
        self._vnc_password = dialog.password.text()
        self._connect_console()

    def _add_vnc_display(self) -> None:
        uuid = self.uuid

        def done(msg: str) -> None:
            self.console_hint.setText(f"VNC display added - {msg.lower()}")
            self.console_fix.hide()

        run_task(
            lambda: svc_add_vnc_display(uuid),
            done=done,
            failed=lambda m: ErrorDialog(self, "Couldn't add display", m).exec(),
        )

    def _save_screenshot(self) -> None:
        if not self.uuid or not self._snap:
            return
        uuid, name = self.uuid, self._snap.name
        path, _ = QFileDialog.getSaveFileName(
            self, "Save screenshot", f"{name}.png", "PNG image (*.png)"
        )
        if not path:
            return

        def save(data: bytes) -> None:
            image = QImage.fromData(data)
            if image.isNull():
                ErrorDialog(self, "Screenshot failed", "No display to capture.").exec()
                return
            image.save(path, "PNG")

        run_task(
            lambda: svc_screenshot(uuid),
            done=save,
            failed=lambda m: ErrorDialog(self, "Screenshot failed", m).exec(),
        )
