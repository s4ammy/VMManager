"""Toolbox tab: guest agent, files, command runner, hand-off."""

from __future__ import annotations

from .common import *  # noqa: F403 - shared imports for the tab modules


class ToolboxMixin:
    """Mixed into DetailPage; expects its attributes."""
    def _build_toolbox(self) -> QWidget:
        # Easily the tallest tab: four sections and three output boxes. It
        # scrolls rather than dictating how tall the window has to be.
        page = QWidget()
        box = QVBoxLayout(page)
        box.setContentsMargins(0, 12, 6, 12)
        box.setSpacing(10)

        agent_title = QLabel("Guest agent")
        agent_title.setProperty("class", "SectionTitle")
        box.addWidget(agent_title)
        self.agent_info = QLabel("Machine must be running, with qemu-guest-agent installed.")
        self.agent_info.setProperty("class", "StatVal")
        self.agent_info.setWordWrap(True)
        self.agent_info.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        box.addWidget(self.agent_info)
        agent_buttons = [_ghost("Query guest agent", self._query_agent)]
        for label, op in (
            ("Ping", "ping"),
            ("Sync clock", "sync-time"),
            ("Freeze FS", "freeze"),
            ("Thaw FS", "thaw"),
            ("Shutdown via agent", "shutdown"),
            ("Reboot via agent", "reboot"),
        ):
            agent_buttons.append(
                _ghost(label, lambda _=False, o=op: self._agent_action(o))
            )
        box.addLayout(flow_row(agent_buttons))

        file_title = QLabel("Files (through the guest agent)")
        file_title.setProperty("class", "SectionTitle")
        box.addWidget(file_title)
        self.send_status = QLabel(
            "Send or fetch files. No network or shared folder needed."
        )
        self.send_status.setObjectName("ConsoleHint")
        self.send_status.setWordWrap(True)
        box.addWidget(self.send_status)
        box.addLayout(flow_row([
            _ghost("Send file…", self._send_file),
            _ghost("Fetch file…", self._fetch_file),
            _ghost("Windows guest tools…", self._windows_tooling),
            # Not through the agent, but this is where someone setting a Windows
            # guest up is already looking, and the drivers are what the agent
            # needs to exist at all.
            _ghost("Add virtio-win disc…", self._add_virtio_iso),
        ]))

        inspect_title = QLabel("Inspect the guest")
        inspect_title.setProperty("class", "SectionTitle")
        box.addWidget(inspect_title)
        self.inspect_info = QLabel(
            "Reports the operating system, hostname and installed software - "
            "from the running guest through its agent, or by reading its disks "
            "when it is shut off."
        )
        self.inspect_info.setObjectName("ConsoleHint")
        self.inspect_info.setWordWrap(True)
        box.addWidget(self.inspect_info)
        box.addLayout(flow_row([
            _ghost("Inspect", lambda: self._inspect(False)),
            _ghost("Inspect with software list", lambda: self._inspect(True)),
        ]))
        self.inspect_output = QPlainTextEdit()
        self.inspect_output.setReadOnly(True)
        self.inspect_output.setMinimumHeight(150)
        self.inspect_output.setMaximumHeight(150)
        self.inspect_output.setPlaceholderText("inspection results appear here")
        box.addWidget(self.inspect_output)

        run_title = QLabel("Run a command in the guest")
        run_title.setProperty("class", "SectionTitle")
        box.addWidget(run_title)
        run_row = QHBoxLayout()
        run_row.setSpacing(8)
        from PySide6.QtWidgets import QLineEdit

        self.cmd_input = QLineEdit()
        self.cmd_input.setPlaceholderText("uname -a")
        self.cmd_input.setMinimumWidth(240)
        self.cmd_input.returnPressed.connect(self._run_guest_command)
        run_row.addWidget(self.cmd_input, 1)
        run_btn = QPushButton("Run")
        run_btn.setProperty("class", "PrimaryButton")
        run_btn.clicked.connect(self._run_guest_command)
        run_row.addWidget(run_btn)
        box.addLayout(run_row)
        self.cmd_output = QPlainTextEdit()
        self.cmd_output.setReadOnly(True)
        self.cmd_output.setMinimumHeight(140)
        self.cmd_output.setMaximumHeight(140)
        self.cmd_output.setPlaceholderText("command output appears here")
        box.addWidget(self.cmd_output)

        ext_title = QLabel("Hand off")
        ext_title.setProperty("class", "SectionTitle")
        box.addWidget(ext_title)
        ext_row = QHBoxLayout()
        vm_btn = _ghost(
            "Open in virt-manager", lambda: self.uuid and open_external(self.uuid, "manager")
        )
        ext_row.addWidget(vm_btn)
        ext_row.addStretch(1)
        box.addLayout(ext_row)

        cmd_title = QLabel("Generated QEMU command line")
        cmd_title.setProperty("class", "SectionTitle")
        box.addWidget(cmd_title)
        self.cmdline = QPlainTextEdit()
        self.cmdline.setReadOnly(True)
        self.cmdline.setPlaceholderText("Load to see how libvirt launches this machine.")
        self.cmdline.setMinimumHeight(140)
        box.addWidget(self.cmdline, 1)
        cmd_btn = _ghost("Load command line", self._load_cmdline)
        box.addWidget(cmd_btn, alignment=Qt.AlignmentFlag.AlignLeft)
        return _scrolled(page)

    def _send_file(self) -> None:
        if not self.uuid:
            return
        local, _ = QFileDialog.getOpenFileName(self, "Choose file to send")
        if not local:
            return
        from PySide6.QtWidgets import QInputDialog

        default_guest = f"/tmp/{local.rsplit('/', 1)[-1]}"
        guest_path, ok = QInputDialog.getText(
            self, "Destination in guest", "Path inside the guest:", text=default_guest
        )
        if not ok or not guest_path.strip():
            return
        uuid = self.uuid
        self.send_status.setText("sending…")
        run_task(
            lambda: svc_send_file(uuid, local, guest_path.strip()),
            done=self.send_status.setText,
            failed=lambda m: self.send_status.setText(f"failed: {m}"),
        )

    def _fetch_file(self) -> None:
        if not self.uuid:
            return
        from PySide6.QtWidgets import QInputDialog

        guest_path, ok = QInputDialog.getText(
            self, "Fetch file from guest", "Path inside the guest:", text="/var/log/syslog"
        )
        if not ok or not guest_path.strip():
            return
        suggested = guest_path.strip().rsplit("/", 1)[-1] or "fetched"
        local, _ = QFileDialog.getSaveFileName(self, "Save as", suggested)
        if not local:
            return
        uuid = self.uuid
        self.send_status.setText("fetching…")
        run_task(
            lambda: svc_fetch_file(uuid, guest_path.strip(), local),
            done=self.send_status.setText,
            failed=lambda m: self.send_status.setText(f"failed: {m}"),
        )

    def _run_guest_command(self) -> None:
        cmd = self.cmd_input.text().strip()
        if not cmd or not self.uuid:
            return
        uuid = self.uuid
        self.cmd_output.setPlainText("running…")

        def apply(result) -> None:
            rc, out, err = result
            text = out
            if err:
                text += ("\n" if text else "") + "[stderr]\n" + err
            self.cmd_output.setPlainText(f"[exit {rc}]\n{text}".strip())

        run_task(
            lambda: svc_guest_exec(uuid, cmd),
            done=apply,
            failed=lambda m: self.cmd_output.setPlainText(f"failed: {m}"),
        )

    def _agent_action(self, op: str) -> None:
        uuid = self.uuid
        if not uuid:
            return

        def apply(message: str) -> None:
            if self.uuid == uuid:
                self.agent_info.setText(message)

        run_task(
            lambda: svc_agent_action(uuid, op),
            done=apply,
            failed=lambda m: self.agent_info.setText(f"agent: {m}"),
        )

    def _query_agent(self) -> None:
        uuid = self.uuid

        def apply(info: dict) -> None:
            if self.uuid != uuid:
                return
            if not info:
                self.agent_info.setText("No information returned.")
                return
            self.agent_info.setText(
                "\n".join(f"{k}: {v}" for k, v in info.items())
            )

        run_task(lambda: svc_guest_info(uuid), done=apply, failed=self._show_error)

    def _load_cmdline(self) -> None:
        uuid = self.uuid
        run_task(
            lambda: svc_qemu_cmdline(uuid),
            done=lambda text: self.uuid == uuid and self.cmdline.setPlainText(text),
            failed=lambda m: self.cmdline.setPlainText(f"unavailable: {m}"),
        )

    def _inspect(self, with_apps: bool) -> None:
        """Describe the guest, whichever way is available."""
        uuid = self.uuid
        if not uuid:
            return
        from ...core.inspect import inspection_backends, svc_inspect

        backends = inspection_backends()
        self.inspect_output.setPlainText(
            "inspecting… (reading disks can take a moment)"
            if not backends["libguestfs"] else "inspecting…"
        )

        def apply(result) -> None:
            if self.uuid != uuid:
                return
            lines = [f"via {result.source}"]
            for label, value in (
                ("os", result.summary),
                ("type", result.os_type),
                ("distro", result.distro),
                ("hostname", result.hostname),
                ("packages", result.package_format),
            ):
                if value:
                    lines.append(f"{label:>10}: {value}")
            if result.mountpoints:
                lines.append("filesystems:")
                lines += [
                    f"    {device} → {mount}" for device, mount in result.mountpoints
                ]
            if result.applications:
                lines.append(f"installed software ({len(result.applications)}):")
                lines += [
                    f"    {name} {version}".rstrip()
                    for name, version in result.applications[:400]
                ]
                if len(result.applications) > 400:
                    lines.append(f"    … and {len(result.applications) - 400} more")
            if result.note:
                lines.append("")
                lines.append(result.note)
            self.inspect_output.setPlainText("\n".join(lines))

        run_task(
            lambda: svc_inspect(uuid, with_apps),
            done=apply,
            failed=lambda m: self.inspect_output.setPlainText(str(m)),
        )
