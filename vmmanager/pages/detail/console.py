"""Console tab: VNC and SPICE clients, detaching, paste and keys."""

from __future__ import annotations

from .common import *  # noqa: F403 - shared imports for the tab modules


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
        self.console_stack = QStackedWidget()
        self.console_stack.setMinimumHeight(300)
        self.console_stack.addWidget(self.vnc)
        self.console_stack.addWidget(self.spice)
        box.addWidget(self.console_stack, 1)
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
        menu.addAction("Save screenshot…", self._save_screenshot)
        menu.addAction(
            "Open in virt-viewer",
            lambda: self.uuid and open_external(self.uuid, "viewer"),
        )
        menu.exec(anchor.mapToGlobal(anchor.rect().bottomLeft()))

    def _active_client(self):
        if self.spice.active:
            return self.spice
        if self.vnc.active:
            return self.vnc
        return None

    def _detach_console(self) -> None:
        if getattr(self, "_detached", None) is not None:
            self._detached.raise_()
            return
        client = self.console_stack.currentWidget()
        name = self._snap.name if self._snap else "console"
        window = DetachedConsoleWindow(client, name)
        self._detached = window
        self._detached_client = client

        def reattach() -> None:
            self.console_stack.addWidget(client)
            self.console_stack.setCurrentWidget(client)
            self._detached = None
            self._detached_client = None

        window.closed.connect(reattach)
        window.show()
        client.setFocus()

    def _close_console(self) -> None:
        self.vnc.close_connection()
        self.spice.close_connection()
        if self._tunnel is not None:
            self._tunnel.stop()
            self._tunnel.deleteLater()
            self._tunnel = None

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
            display = next(
                (g for g in graphics if g.type == "vnc" and (g.port > 0 or g.socket)),
                None,
            ) or next(
                (g for g in graphics if g.type == "spice" and g.port > 0), None
            )
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
            self._open_client(client, display.host, display.port)
            return
        # remote host: the display listens on its loopback - tunnel to it
        target, keyfile = ssh
        self.console_hint.setText(f"opening ssh tunnel to {target}…")
        tunnel = SSHTunnel(target, display.host, display.port, keyfile, parent=self)
        self._tunnel = tunnel
        tunnel.ready.connect(
            lambda port: self._open_client(client, "127.0.0.1", port)
        )
        tunnel.failed.connect(
            lambda m: self.console_hint.setText(f"tunnel failed: {m}")
        )
        tunnel.start()

    def _open_client(self, client, host: str, port: int) -> None:
        if client is self.spice:
            client.open_tcp(host, port)
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
