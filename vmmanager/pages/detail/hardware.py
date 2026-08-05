"""Hardware tab: the component bay and its per-device faceplate."""

from __future__ import annotations

from .common import *  # noqa: F403 - shared imports for the tab modules


class HardwareMixin:
    """Mixed into DetailPage; expects its attributes."""

    # Silkscreen-style labels for the component bay, one per device class.
    HW_BADGES = {
        "cpu": "CPU", "mem": "MEM", "boot": "BOOT", "labels": "TAG",
        "disk": "DSK", "cdrom": "ODD", "nic": "NIC", "video": "GPU",
        "gfx": "DSP", "sound": "SND", "input": "INP", "usb": "USB",
        "pci": "PCI", "mdev": "MDV", "fs": "FS", "watchdog": "WDT", "redir": "URD",
        "vsock": "VSK", "panic": "PNC", "smartcard": "SCD", "audio": "AUD",
        "dimm": "DIM", "controller": "CTL", "ports": "PRT", "tune": "TUN",
        "features": "FEA",
    }
    def _build_hardware(self) -> QWidget:
        from PySide6.QtWidgets import QTreeWidget

        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(0, 12, 0, 0)
        outer.setSpacing(10)

        split = QHBoxLayout()
        split.setSpacing(14)

        left = QVBoxLayout()
        left.setSpacing(10)
        self.hw_tree = QTreeWidget()
        self.hw_tree.setHeaderHidden(True)
        self.hw_tree.setColumnCount(2)
        self.hw_tree.setColumnWidth(0, 66)
        self.hw_tree.setIndentation(10)
        self.hw_tree.setFixedWidth(330)
        self.hw_tree.setRootIsDecorated(False)
        self.hw_tree.itemSelectionChanged.connect(self._show_hw_detail)
        self.hw_tree.setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu
        )
        self.hw_tree.customContextMenuRequested.connect(self._hw_context_menu)
        left.addWidget(self.hw_tree, 1)
        install = QPushButton("+ Install hardware ▾")
        install.setProperty("class", "PrimaryButton")
        install.setCursor(Qt.CursorShape.PointingHandCursor)
        install.clicked.connect(lambda: self._install_menu(install))
        left.addWidget(install)
        split.addLayout(left)

        panel_frame = QFrame()
        panel_frame.setProperty("class", "ChartCard")
        self.hw_panel = QVBoxLayout(panel_frame)
        self.hw_panel.setContentsMargins(22, 18, 22, 18)
        self.hw_panel.setSpacing(8)
        split.addWidget(panel_frame, 1)
        outer.addLayout(split, 1)

        self.hw_status = QLabel("")
        self.hw_status.setObjectName("ConsoleHint")
        self.hw_status.setProperty("class", "Accent")
        self.hw_status.setWordWrap(True)
        outer.addWidget(self.hw_status)
        self._show_hw_detail()
        return page

    def _hw_item(self, kind: str, label: str, payload=None):
        from PySide6.QtGui import QFont
        from PySide6.QtWidgets import QTreeWidgetItem

        item = QTreeWidgetItem([self.HW_BADGES.get(kind, "···"), label])
        badge_font = QFont(theme.MONO, 8)
        badge_font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 1.0)
        item.setFont(0, badge_font)
        item.setForeground(0, QColor(theme.ACCENT))
        item.setData(0, Qt.ItemDataRole.UserRole, (kind, payload))
        return item

    def _hw_group(self, title: str):
        """A banded heading, so the list reads as groups rather than one run.

        The rows below it are all badge-plus-text at the same weight, which left
        the headings looking like more of the same. A darker band across both
        columns separates them at a glance, without a rule to align.
        """
        from PySide6.QtCore import QSize
        from PySide6.QtGui import QFont
        from PySide6.QtWidgets import QTreeWidgetItem

        item = QTreeWidgetItem(["", title])
        font = QFont(theme.MONO, 8)
        font.setBold(True)
        font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 2.0)
        item.setFont(1, font)
        item.setForeground(1, QColor(theme.TEXT_DIM))
        band = QColor(theme.BG_INSET)
        for column in (0, 1):
            item.setBackground(column, band)
            item.setSizeHint(column, QSize(0, 26))
        item.setFlags(Qt.ItemFlag.ItemIsEnabled)  # not selectable
        return item

    def _populate_hw_tree(self, hw: Hardware) -> None:
        prev = self._selected_device()
        prev_key = (prev[0], str(prev[1])) if prev else None
        self.hw_tree.clear()

        def add_group(title: str, entries) -> None:
            if not entries:
                return
            group = self._hw_group(title)
            self.hw_tree.addTopLevelItem(group)
            for kind, label, payload in entries:
                group.addChild(self._hw_item(kind, label, payload))
            group.setExpanded(True)

        topo = ""
        if hw.topology:
            s, c, t = hw.topology
            topo = f" ({s}s·{c}c·{t}t)"
        mem = fmt_mem(hw.memory_mb)
        if hw.max_memory_mb != hw.memory_mb:
            mem += f" of {fmt_mem(hw.max_memory_mb)}"
        add_group("SYSTEM", [
            ("cpu", f"{hw.vcpus} vcpu · {hw.cpu_mode}{topo}", None),
            ("mem", mem, None),
            ("boot", " → ".join(hw.boot) if hw.boot else "hd", None),
            ("labels", hw.title or "(no title set)", None),
            ("tune", self._tuning_summary(), None),
            ("features", self._features_summary(), None),
        ])
        add_group("STORAGE", [
            ("cdrom" if d.device == "cdrom" else "disk",
             f"{d.dev} · {d.source.rsplit('/', 1)[-1]}", d)
            for d in hw.disks
        ])
        add_group("NETWORK", [
            ("nic", f"{n.source or 'direct'} · {n.mac}", n) for n in hw.nics
        ])
        add_group("DISPLAY", [
            ("video", f"video · {hw.video}", None),
            *[("gfx", f"{t} display · {p}", (t, p)) for t, p in hw.graphics],
        ])
        add_group("PERIPHERALS", [
            *[("sound", f"sound · {m}", m) for m in hw.sounds],
            *[("input", f"{bus} {itype}", (itype, bus)) for itype, bus in hw.inputs],
            *[(h.kind, h.ident, h) for h in hw.hostdevs],
        ])
        add_group("SHARED FOLDERS", [
            ("fs", f"{f.tag} · {f.source}", f) for f in hw.filesystems
        ])
        extras = []
        if hw.watchdog:
            model, action = hw.watchdog
            extras.append(("watchdog", f"watchdog {model} → {action}", hw.watchdog))
        for i in range(hw.redirdevs):
            extras.append(("redir", f"usb redirection #{i + 1}", i))
        if hw.vsock:
            extras.append(("vsock", f"vsock cid {hw.vsock}", hw.vsock))
        if hw.panic:
            extras.append(("panic", f"panic notifier · {hw.panic}", hw.panic))
        if hw.smartcard:
            extras.append(("smartcard", f"smartcard · {hw.smartcard}", hw.smartcard))
        if hw.audio:
            extras.append(("audio", f"audio out · {hw.audio}", hw.audio))
        for size in hw.memory_devices:
            extras.append(("dimm", f"memory device · {size} MiB", size))
        add_group("OTHER DEVICES", extras)
        # Spare PCIe root ports exist so devices can hot-plug; listing a dozen
        # identical rows buries the controllers that are worth seeing, so they
        # collapse into one line.
        ports = [c for c in hw.controllers if c[2] == "pcie-root-port"]
        others = [c for c in hw.controllers if c[2] != "pcie-root-port"]
        entries = [
            ("controller", f"{ctype} {index} · {model}", (ctype, index, model))
            for ctype, index, model in others
        ]
        if ports:
            entries.append(
                ("ports", f"{len(ports)} pcie root ports (hotplug headroom)", ports)
            )
        add_group("CONTROLLERS", entries)

        # restore selection (or select the CPU row)
        target = None
        for i in range(self.hw_tree.topLevelItemCount()):
            group = self.hw_tree.topLevelItem(i)
            for j in range(group.childCount()):
                child = group.child(j)
                kind, payload = child.data(0, Qt.ItemDataRole.UserRole)
                if prev_key and (kind, str(payload)) == prev_key:
                    target = child
                    break
                if target is None:
                    target = child
            if prev_key and target is not None and (
                target.data(0, Qt.ItemDataRole.UserRole)[0],
                str(target.data(0, Qt.ItemDataRole.UserRole)[1]),
            ) == prev_key:
                break
        if target is not None:
            self.hw_tree.setCurrentItem(target)

    # What each row's badge means for removal. One table, so the right-click
    # menu and the panel's own Remove button can never disagree about whether a
    # row can go, or about what it is called when asked.
    HW_REMOVABLE = {
        "disk": "disk",
        "cdrom": "disc drive",
        "nic": "network interface",
        "sound": "sound device",
        "input": "input device",
        "usb": "USB device",
        "pci": "PCI device",
        "mdev": "mediated device",
        "fs": "shared folder",
        "watchdog": "watchdog",
        "redir": "USB redirection",
        "vsock": "vsock",
        "panic": "panic notifier",
        "smartcard": "smartcard",
        "dimm": "memory device",
        "audio": "audio device",
    }
    # Not here on purpose: "gfx". svc_remove_simple_device takes the first
    # element with that tag, and a machine may have two displays - removing
    # the wrong one silently is worse than not offering it. It needs a service
    # call that can say which.

    def _hw_remover(self, kind: str, payload):
        """The callable that removes this row, or None if it cannot be removed.

        The disk, NIC, host-device and shared-folder removers read the current
        selection rather than taking arguments, which is why the menu selects
        the row it was opened on before running one.
        """
        simple = {"watchdog": "watchdog", "redir": "redirdev", "vsock": "vsock",
                  "panic": "panic", "smartcard": "smartcard", "dimm": "memory",
                  "audio": "audio"}
        if kind in ("disk", "cdrom"):
            return self._remove_disk
        if kind == "nic":
            return self._remove_nic
        if kind in ("usb", "pci", "mdev"):
            return self._remove_hostdev
        if kind == "fs":
            return self._remove_share
        if kind == "sound":
            return lambda: self._remove_sound(payload)
        if kind == "input":
            itype, bus = payload
            return lambda: self._remove_input(itype, bus)
        if kind in simple:
            return lambda: self._remove_simple(simple[kind])
        return None

    def _confirm_removal(self, what: str, detail: str = "") -> bool:
        """Ask before taking hardware out, unless Settings says not to.

        The same question whichever way you got here: a right-click is easy to
        aim at the wrong row.
        """
        from ...pages.settings import confirmations_enabled

        if not confirmations_enabled():
            return True
        body = f"Take the {what} off this machine?"
        if detail:
            body += f" {detail}"
        body += ("\n\nIt can be added again from Install hardware. Nothing on "
                 "disk is deleted.")
        confirm = ConfirmDialog(self, f"Remove {what}", body, "Remove")
        return confirm.exec() == QDialog.DialogCode.Accepted

    def _hw_context_menu(self, pos) -> None:
        """Right-click a row to take that device off the machine."""
        item = self._aim_at_hw_row(pos)
        if item is None:
            return
        kind, payload = item.data(0, Qt.ItemDataRole.UserRole)
        self._build_hw_menu(kind, payload).exec(
            self.hw_tree.viewport().mapToGlobal(pos)
        )

    def _aim_at_hw_row(self, pos):
        return self._aim_at_hw_item(self.hw_tree.itemAt(pos))

    def _aim_at_hw_item(self, item):
        """Make this row current, if it is a device row at all.

        The disk, NIC, host-device and folder removers read the selection, so a
        right-click has to move it first or it would act on whatever happened to
        be selected before. None - a group heading, or nothing under the cursor -
        is what tells the caller to open no menu.

        Takes the item rather than a position so it can be checked without
        depending on where Qt happened to lay the rows out.
        """
        if item is None or item.data(0, Qt.ItemDataRole.UserRole) is None:
            return None
        self.hw_tree.setCurrentItem(item)
        return item

    def _build_hw_menu(self, kind: str, payload) -> QMenu:
        """Separate from showing it: QMenu.exec never returns without a display,
        so this is the part a test can look at."""
        menu = QMenu(self)
        remover = self._hw_remover(kind, payload)
        if remover is None:
            nothing = menu.addAction("Nothing to remove on this row")
            nothing.setEnabled(False)
        else:
            menu.addAction(
                f"Remove {self.HW_REMOVABLE.get(kind, 'device')}", remover
            )
        return menu

    def _selected_device(self):
        items = self.hw_tree.selectedItems() if hasattr(self, "hw_tree") else []
        if not items:
            return None
        return items[0].data(0, Qt.ItemDataRole.UserRole)

    def _panel_clear(self) -> None:
        while self.hw_panel.count():
            item = self.hw_panel.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
            elif item.layout() is not None:
                sub = item.layout()
                while sub.count():
                    s = sub.takeAt(0)
                    if s.widget():
                        s.widget().deleteLater()

    def _panel_title(self, badge: str, title: str) -> None:
        row = QHBoxLayout()
        chip = QLabel(badge)
        chip.setObjectName("HwBadge")
        row.addWidget(chip)
        t = QLabel(title)
        t.setProperty("class", "SectionTitle")
        row.addWidget(t)
        row.addStretch(1)
        for label, mode in (("DETAILS", "details"), ("XML", "xml")):
            btn = QPushButton(label)
            btn.setProperty("class", "SwitchTab")
            btn.setProperty(
                "active", "true" if self._hw_view_mode == mode else "false"
            )
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _=False, m=mode: self._set_hw_mode(m))
            row.addWidget(btn)
        self.hw_panel.addLayout(row)
        self.hw_panel.addSpacing(6)

    def _set_hw_mode(self, mode: str) -> None:
        if mode != self._hw_view_mode:
            self._hw_view_mode = mode
            self._show_hw_detail()

    def _panel_row(self, key: str, value: str) -> None:
        row = QHBoxLayout()
        k = QLabel(key.upper())
        k.setProperty("class", "StatKey")
        k.setFixedWidth(90)
        v = QLabel(value)
        v.setProperty("class", "StatVal")
        v.setWordWrap(True)
        v.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        row.addWidget(k, alignment=Qt.AlignmentFlag.AlignTop)
        row.addWidget(v, 1)
        self.hw_panel.addLayout(row)

    def _panel_actions(self, *buttons) -> None:
        self.hw_panel.addSpacing(10)
        row = QHBoxLayout()
        row.setSpacing(8)
        for btn in buttons:
            row.addWidget(btn)
        row.addStretch(1)
        self.hw_panel.addLayout(row)

    @staticmethod
    def _hw_ident(kind: str, payload) -> str:
        if kind in ("disk", "cdrom"):
            return payload.dev
        if kind == "nic":
            return payload.mac
        if kind == "gfx":
            return payload[0]
        if kind == "sound":
            return str(payload)
        if kind == "input":
            return f"{payload[0]}/{payload[1]}"
        if kind in ("usb", "pci", "mdev"):
            return payload.ident
        if kind == "fs":
            return payload.tag
        return ""

    def _show_hw_xml(self, kind: str, payload) -> None:
        from ...syntax import XmlHighlighter

        uuid = self.uuid
        ident = self._hw_ident(kind, payload)
        editor = QPlainTextEdit()
        editor.setPlaceholderText("loading…")
        self._hw_xml_highlighter = XmlHighlighter(editor.document())
        self._hw_xml_edit = editor
        self.hw_panel.addWidget(editor, 1)

        def load() -> None:
            run_task(
                lambda: svc_get_device_xml(uuid, kind, ident),
                done=lambda xml: self.uuid == uuid and editor.setPlainText(xml),
                failed=lambda m: editor.setPlainText(f"<!-- unavailable: {m} -->"),
            )

        def apply() -> None:
            text = editor.toPlainText()
            run_task(
                lambda: svc_set_device_xml(uuid, kind, ident, text),
                done=self._hw_done,
                failed=self._hw_failed,
            )

        load()
        self._panel_actions(
            _ghost("Reload", load),
            _ghost("Apply XML", apply),
        )

    def _show_hw_detail(self) -> None:
        self._panel_clear()
        hw = self._hw
        sel = self._selected_device()
        if sel is None or hw is None:
            hint = QLabel("Select a component on the left.")
            hint.setObjectName("ConsoleHint")
            self.hw_panel.addWidget(hint)
            self.hw_panel.addStretch(1)
            return
        kind, payload = sel
        badge = self.HW_BADGES.get(kind, "···")

        if self._hw_view_mode == "xml":
            self._panel_title(badge, "Component XML")
            self._show_hw_xml(kind, payload)
            return

        if kind == "features":
            self._panel_title(badge, "Guest features")
            feats = self._features
            if feats is None:
                self._panel_row("status", "reading…")
                return
            on = feats.hyperv_on
            self._panel_row(
                "hyper-v", f"{len(on)} on" if on else "none",
            )
            if feats.vendor_id:
                self._panel_row("vendor id", feats.vendor_id)
            self._panel_row("kvm signature", "hidden" if feats.kvm_hidden else "visible")
            self._panel_row("vmware port", "on" if feats.vmport else "off")
            self._panel_row(
                "cpu flags",
                ", ".join(f"{n} ({p})" for n, p in sorted(feats.cpu_features.items()))
                or "none",
            )
            self._panel_row(
                "looking glass", f"{feats.shmem_mb} MiB" if feats.shmem_mb else "off"
            )
            self._panel_row("input passthrough", f"{len(feats.evdev)} device(s)"
                            if feats.evdev else "none")
            self._panel_row("secure boot", "on" if feats.secure_boot else "off")
            self._panel_actions(_ghost("Edit features…", self._edit_features))
            return

        if kind == "tune":
            self._panel_title(badge, "Tuning")
            tuning = self._tuning
            if tuning is None:
                self._panel_row("status", "reading…")
                return
            pins = tuning.vcpu_pins
            self._panel_row(
                "cpu pinning",
                f"{len(pins)} of {hw.vcpus} vcpus pinned" if pins else "not pinned",
            )
            if tuning.emulator_pin:
                from ...core.tuning import format_cpuset

                self._panel_row("emulator", format_cpuset(tuning.emulator_pin))
            self._panel_row(
                "memory backing",
                "hugepages" if tuning.hugepage_size_kb else "4 KiB pages",
            )
            self._panel_row("iothreads", str(tuning.iothreads or 0))
            limited = [d for d, t in tuning.throttles.items() if t.limited]
            self._panel_row("disk limits", ", ".join(limited) if limited else "none")
            self._panel_actions(_ghost("Edit tuning…", self._edit_tuning))
            return

        if kind == "cpu":
            self._panel_title(badge, "Processor")
            self._panel_row("model", hw.cpu_mode)
            if hw.topology:
                s, c, t = hw.topology
                self._panel_row("topology", f"{s} sockets · {c} cores · {t} threads")
            self._panel_row("vcpus", str(hw.vcpus))
            self._panel_row("machine", hw.machine)
            self._panel_row("firmware", hw.firmware)
            self._panel_actions(
                _ghost("Edit processor…", self._edit_cpu),
                _ghost("Machine type…", self._edit_machine_type),
            )
        elif kind == "mem":
            self._panel_title(badge, "Memory")
            self._panel_row("current", fmt_mem(hw.memory_mb))
            self._panel_row("maximum", fmt_mem(hw.max_memory_mb))
            self._panel_actions(_ghost("Edit memory…", self._edit_memory))
        elif kind == "boot":
            self._panel_title(badge, "Boot order")
            for i, entry in enumerate(hw.boot or ("hd",), 1):
                self._panel_row(f"{i}.", entry)
            self._panel_row("boot menu", "on" if hw.boot_menu else "off")
            self._panel_actions(
                _ghost("Reorder…", self._edit_boot_order),
                _ghost(
                    "Turn boot menu " + ("off" if hw.boot_menu else "on"),
                    lambda: self._toggle_boot_menu(not hw.boot_menu),
                ),
            )
        elif kind in ("disk", "cdrom"):
            d = payload
            self._panel_title(badge, f"{d.dev} - {'optical drive' if kind == 'cdrom' else 'disk'}")
            self._panel_row("source", d.source)
            self._panel_row("bus", d.bus)
            self._panel_row("format", d.format)
            self._panel_row("cache", d.cache)
            buttons = []
            if kind == "cdrom":
                buttons.append(_ghost("Change media…", self._change_media))
            buttons.append(_ghost("Cache mode…", self._edit_disk_cache))
            if kind == "disk":
                buttons.append(_ghost("Move to pool…", self._move_disk))
            buttons.append(_ghost("Remove", self._remove_disk))
            self._panel_actions(*buttons)
        elif kind == "nic":
            n = payload
            self._panel_title(badge, f"Network interface - {n.source or 'direct'}")
            self._panel_row("mac", n.mac)
            self._panel_row("model", n.model)
            self._panel_row("network", n.source or " - ")
            self._panel_row("filter", n.filter or " - ")
            self._panel_actions(
                _ghost("Edit…", lambda: self._edit_nic(n)),
                _ghost("Remove", self._remove_nic),
            )
        elif kind == "video":
            self._panel_title(badge, "Video adapter")
            self._panel_row("model", hw.video)
            self._panel_row("3d acceleration", "on" if hw.video_accel3d else "off")
            self._panel_actions(
                _ghost("Change model…", self._edit_video),
                _ghost(
                    "Turn 3D " + ("off" if hw.video_accel3d else "on"),
                    lambda: self._toggle_accel3d(not hw.video_accel3d),
                ),
            )
        elif kind == "gfx":
            gtype, port = payload
            self._panel_title(badge, f"{gtype.upper()} display")
            self._panel_row("type", gtype)
            self._panel_row("port", str(port))
            hint = QLabel("The Console tab connects to this display.")
            hint.setObjectName("ConsoleHint")
            self.hw_panel.addWidget(hint)
        elif kind == "sound":
            self._panel_title(badge, f"Sound - {payload}")
            self._panel_row("model", str(payload))
            self._panel_actions(_ghost("Remove", lambda: self._remove_sound(payload)))
        elif kind == "input":
            itype, bus = payload
            self._panel_title(badge, f"{itype} ({bus})")
            self._panel_row("type", itype)
            self._panel_row("bus", bus)
            if bus != "ps2":
                self._panel_actions(
                    _ghost("Remove", lambda: self._remove_input(itype, bus))
                )
            else:
                hint = QLabel("Built into the machine type.")
                hint.setObjectName("ConsoleHint")
                self.hw_panel.addWidget(hint)
        elif kind in ("usb", "pci", "mdev"):
            h = payload
            title = ("Mediated device" if h.kind == "mdev"
                     else "Host device")
            self._panel_title(badge, f"{title} - {h.ident}")
            self._panel_row("type", h.kind.upper())
            self._panel_row("address" if h.kind != "mdev" else "uuid", h.ident)
            actions = []
            if h.kind != "mdev":
                actions.append(
                    _ghost("Options…", lambda: self._edit_hostdev_options(h))
                )
            actions.append(_ghost("Detach from machine", self._remove_hostdev))
            self._panel_actions(*actions)
        elif kind == "labels":
            self._panel_title(badge, "Name and notes")
            self._panel_row("title", hw.title or " - ")
            self._panel_row("description", hw.description or " - ")
            self._panel_actions(_ghost("Edit…", self._edit_labels))
        elif kind == "watchdog":
            model, action = payload
            self._panel_title(badge, f"Watchdog - {model}")
            self._panel_row("model", model)
            self._panel_row("on timeout", action)
            hint = QLabel(
                "The guest must run a watchdog daemon; if it stops petting the "
                "device, the host takes the action above."
            )
            hint.setWordWrap(True)
            hint.setObjectName("ConsoleHint")
            self.hw_panel.addWidget(hint)
            self._panel_actions(
                _ghost("Change action…", self._edit_watchdog),
                _ghost("Remove", lambda: self._remove_simple("watchdog")),
            )
        elif kind == "redir":
            self._panel_title(badge, "USB redirection channel")
            self._panel_row("bus", "usb")
            self._panel_row("type", "spicevmc")
            hint = QLabel(
                "A SPICE viewer can hand a host USB device to the guest through "
                "this channel. No passthrough configuration needed."
            )
            hint.setWordWrap(True)
            hint.setObjectName("ConsoleHint")
            self.hw_panel.addWidget(hint)
            self._panel_actions(
                _ghost("Remove", lambda: self._remove_simple("redirdev"))
            )
        elif kind == "vsock":
            self._panel_title(badge, "vsock channel")
            self._panel_row("cid", str(payload))
            self._panel_actions(_ghost("Remove", lambda: self._remove_simple("vsock")))
        elif kind == "panic":
            self._panel_title(badge, f"Panic notifier - {payload}")
            self._panel_row("model", str(payload))
            hint = QLabel(
                "Lets the guest report a kernel panic, so the crash policy can "
                "act on it instead of the machine just hanging."
            )
            hint.setWordWrap(True)
            hint.setObjectName("ConsoleHint")
            self.hw_panel.addWidget(hint)
            self._panel_actions(_ghost("Remove", lambda: self._remove_simple("panic")))
        elif kind == "smartcard":
            self._panel_title(badge, f"Smartcard - {payload}")
            self._panel_row("mode", str(payload))
            self._panel_actions(
                _ghost("Remove", lambda: self._remove_simple("smartcard"))
            )
        elif kind == "audio":
            self._panel_title(badge, f"Audio output - {payload}")
            self._panel_row("backend", str(payload))
            self._panel_actions(_ghost("Change backend…", self._edit_audio))
        elif kind == "dimm":
            self._panel_title(badge, f"Memory device - {payload} MiB")
            self._panel_row("size", f"{payload} MiB")
            self._panel_row("node", "0")
            self._panel_actions(_ghost("Remove", lambda: self._remove_simple("memory")))
        elif kind == "ports":
            self._panel_title(badge, f"{len(payload)} PCIe root ports")
            self._panel_row("indexes", ", ".join(str(index) for _t, index, _m in payload))
            hint = QLabel(
                "Empty slots kept in reserve. A disk, NIC or host device can "
                "only be hot-plugged into a machine that has a free port, so "
                "new machines get a dozen."
            )
            hint.setWordWrap(True)
            hint.setObjectName("ConsoleHint")
            self.hw_panel.addWidget(hint)
        elif kind == "controller":
            ctype, index, model = payload
            self._panel_title(badge, f"{ctype} controller {index}")
            self._panel_row("type", ctype)
            self._panel_row("index", str(index))
            self._panel_row("model", model)
            self._panel_actions(
                _ghost("Change model…", lambda: self._edit_controller(ctype, index, model))
            )
        elif kind == "fs":
            f = payload
            self._panel_title(badge, f"Shared folder - {f.tag}")
            self._panel_row("host path", f.source)
            self._panel_row("driver", f.driver)
            self._panel_row("mount", f"mount -t {f.driver if f.driver == 'virtiofs' else '9p'} {f.tag} /mnt")
            self._panel_actions(_ghost("Remove", self._remove_share))
        self.hw_panel.addStretch(1)

    def _install_menu(self, anchor: QPushButton) -> None:
        if not self.uuid or not self._hw:
            return
        uuid = self.uuid
        menu = QMenu(self)
        menu.addAction("Disk…", self._add_disk)
        menu.addAction("virtio-win driver disc (Windows)…", self._add_virtio_iso)
        menu.addAction("Network interface…", self._add_nic)
        menu.addAction("Shared folder…", self._add_share)
        menu.addAction("Host device (USB / PCI)…", self._add_hostdev)
        menu.addAction("Mediated device (vGPU)…", self._add_mdev)
        menu.addAction("Check PCI passthrough…", self._passthrough_check)
        other = menu.addMenu("Other devices")
        if not self._hw.watchdog:
            other.addAction(
                "Watchdog (reset a hung guest)",
                lambda: self._hw_run(lambda: svc_add_watchdog(uuid)),
            )
        other.addAction(
            "USB redirection channel (SPICE)",
            lambda: self._hw_run(lambda: svc_add_usb_redirection(uuid)),
        )
        if not self._hw.vsock:
            other.addAction(
                "vsock (host/guest sockets)",
                lambda: self._hw_run(lambda: svc_add_vsock(uuid)),
            )
        if not self._hw.panic:
            panic = other.addMenu("Panic notifier")
            for model in PANIC_MODELS:
                panic.addAction(
                    model, lambda _=False, m=model: self._hw_run(
                        lambda: svc_add_panic(uuid, m))
                )
        if not self._hw.smartcard:
            other.addAction(
                "Smartcard passthrough",
                lambda: self._hw_run(lambda: svc_add_smartcard(uuid)),
            )
        other.addAction("Memory device (DIMM)…", self._add_memory_device)
        audio = other.addMenu("Audio backend")
        for backend in AUDIO_BACKENDS:
            audio.addAction(
                backend, lambda _=False, b=backend: self._hw_run(
                    lambda: svc_add_audio(uuid, b))
            )
        sound = menu.addMenu("Sound")
        for model in ("ich9", "ac97", "usb"):
            if model not in self._hw.sounds:
                sound.addAction(
                    model,
                    lambda _=False, m=model: run_task(
                        lambda: svc_add_sound(uuid, m),
                        done=self._hw_done, failed=self._hw_failed,
                    ),
                )
        inputs = menu.addMenu("Input")
        existing = set(self._hw.inputs)
        for label, itype, bus in (
            ("USB tablet (precise mouse - Windows)", "tablet", "usb"),
            ("virtio tablet (precise mouse - Linux)", "tablet", "virtio"),
            ("virtio keyboard", "keyboard", "virtio"),
        ):
            if (itype, bus) not in existing:
                inputs.addAction(
                    label,
                    lambda _=False, t=itype, b=bus: run_task(
                        lambda: svc_attach_input(uuid, t, b),
                        done=self._hw_done, failed=self._hw_failed,
                    ),
                )
        displays = menu.addMenu("Display")
        have = {t for t, _p in self._hw.graphics}
        for gtype in ("vnc", "spice"):
            if gtype not in have:
                displays.addAction(
                    f"{gtype.upper()} display",
                    lambda _=False, g=gtype: run_task(
                        lambda: svc_add_display(uuid, g),
                        done=self._hw_done, failed=self._hw_failed,
                    ),
                )
        menu.exec(anchor.mapToGlobal(anchor.rect().bottomLeft()))

    def _add_mdev(self) -> None:
        uuid = self.uuid
        if not uuid:
            return

        def load(done) -> None:
            run_task(
                lambda: (svc_mdev_types(), svc_list_mdevs()),
                done=done, failed=self._hw_failed,
            )

        def show(data) -> None:
            types, mdevs = data
            dialog = MdevDialog(self, types, mdevs)

            def refresh(message: str) -> None:
                dialog.status.setText(message)
                load(lambda d: dialog.populate(*d))

            def create(parent: str, type_id: str) -> None:
                run_task(
                    lambda: svc_create_mdev(parent, type_id),
                    done=lambda mdev_uuid: refresh(f"created {mdev_uuid}"),
                    failed=lambda m: dialog.status.setText(str(m)),
                )

            def delete(mdev_uuid: str) -> None:
                run_task(
                    lambda: svc_delete_mdev(mdev_uuid),
                    done=lambda _: refresh(f"deleted {mdev_uuid}"),
                    failed=lambda m: dialog.status.setText(str(m)),
                )

            dialog.create_requested = create
            dialog.delete_requested = delete
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return
            chosen = dialog.chosen()
            if chosen is None:
                return
            if chosen.attached_to:
                ErrorDialog(
                    self, "Already assigned",
                    f"That instance is assigned to '{chosen.attached_to}'.",
                ).exec()
                return
            self._hw_run(
                lambda: svc_attach_hostdev(uuid, "mdev", chosen.uuid)
            )

        load(show)

    def _passthrough_check(self) -> None:
        if is_remote_uri(current_uri()):
            ErrorDialog(
                self, "Local hosts only",
                "Passthrough diagnostics read the host's IOMMU groups from "
                "sysfs, which only works for a local connection.",
            ).exec()
            return
        self.hw_status.setText("reading IOMMU groups…")
        run_task(
            svc_iommu_report,
            done=lambda report: (
                self.hw_status.setText(""),
                PassthroughDialog(self, report).exec(),
            ),
            failed=self._hw_failed,
        )

    def _windows_tooling(self) -> None:
        uuid = self.uuid
        if not uuid or not self._snap:
            return
        name = self._snap.name
        self.send_status.setText("checking guest tooling…")

        def show(state: dict) -> None:
            if self.uuid != uuid:
                return
            self.send_status.setText("")
            dialog = WindowsToolingDialog(self, name, state)
            if dialog.exec() != QDialog.DialogCode.Accepted or not dialog.action:
                return
            if dialog.action == "iso":
                # Through the picker, not straight to the download: the disc is
                # usually already on the host.
                self._add_virtio_iso()
            else:
                fn = {
                    "agent": lambda: svc_add_agent_channel(uuid),
                    "spice": lambda: svc_add_spice_agent_channel(uuid),
                    "tablet": lambda: svc_attach_input(uuid, "tablet", "usb"),
                }[dialog.action]
                run_task(
                    fn,
                    done=lambda msg: (
                        self.send_status.setText(str(msg)),
                        self._load_hardware(),
                    ),
                    failed=lambda m: ErrorDialog(self, "Change failed", m).exec(),
                )

        run_task(
            lambda: svc_windows_tooling_state(uuid), done=show, failed=self._show_error
        )

    def _add_virtio_iso(self) -> None:
        """Attach the virtio-win driver disc from wherever this host keeps it.

        Windows cannot see a virtio disk or NIC until the drivers off this disc
        are installed, so an install onto virtio storage stalls at "no drives
        found" without it. Most people already have a copy - a distro package,
        or one downloaded for the last machine - and it is 700 MB to fetch
        again, so the dialog looks for one, remembers what it is told, and keeps
        the download as the fallback rather than the first move.
        """
        uuid = self.uuid
        if not uuid:
            return
        from ...data.catalog import virtio_win_candidates
        from ...pages.settings import save_virtio_win_iso, virtio_win_iso

        remote = is_remote_uri(current_uri())
        saved = virtio_win_iso()

        def show(pools) -> None:
            if self.uuid != uuid:
                return
            dialog = VirtioIsoDialog(
                self,
                saved=saved,
                found=[] if remote else virtio_win_candidates(),
                pools=pools,
                remote=remote,
            )
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return
            if dialog.download:
                self._get_virtio_win(uuid)
                return
            path = dialog.chosen_path()
            if not path:
                return
            if dialog.remember.isChecked():
                save_virtio_win_iso(path)
            elif saved == path:
                # Unticked on the disc that was being offered: stop offering it.
                save_virtio_win_iso("")
            self.hw_status.setText("attaching the virtio-win disc…")
            run_task(
                lambda: svc_attach_cdrom(uuid, path),
                done=self._hw_done,
                failed=self._hw_failed,
            )

        run_task(svc_list_pools, done=show, failed=self._hw_failed)

    def _virtio_status(self, text: str) -> None:
        """Say it on both tabs: the disc can be asked for from either of them."""
        self.send_status.setText(text)
        self.hw_status.setText(text)

    def _get_virtio_win(self, uuid: str) -> None:
        """Download the virtio-win disc, import it, attach it as a drive."""
        from ...data.catalog import VIRTIO_WIN, ImageDownloader

        self._virtio_status("downloading virtio-win…")
        downloader = ImageDownloader(VIRTIO_WIN)
        self._virtio_downloader = downloader
        downloader.progress.connect(
            lambda _pct, text: self._virtio_status(f"virtio-win: {text}")
        )
        downloader.failed.connect(
            lambda m: self._virtio_status(f"virtio-win download failed: {m}")
        )

        def imported(path: str) -> None:
            from ...pages.settings import save_virtio_win_iso

            # Downloaded once, offered from here on: the next machine gets the
            # copy now sitting in the pool instead of another 700 MB.
            save_virtio_win_iso(path)
            run_task(
                lambda: svc_attach_cdrom(uuid, path),
                done=lambda msg: (
                    self._virtio_status(f"virtio-win attached - {msg}"),
                    self._load_hardware(),
                ),
                failed=lambda m: ErrorDialog(self, "Couldn't attach disc", m).exec(),
            )

        def downloaded(local: str) -> None:
            self._virtio_status("importing the disc into the default pool…")
            run_task(
                lambda: svc_upload_volume_from_file(
                    "default", "virtio-win.iso", local, "raw"
                ),
                done=imported,
                failed=lambda m: ErrorDialog(self, "Couldn't import disc", m).exec(),
            )

        downloader.finished_ok.connect(downloaded)
        downloader.start()

    def _remove_sound(self, model: str) -> None:
        if not self._confirm_removal("sound device", f"({model})"):
            return
        uuid = self.uuid
        run_task(
            lambda: svc_remove_sound(uuid, model),
            done=self._hw_done, failed=self._hw_failed,
        )

    def _remove_input(self, itype: str, bus: str) -> None:
        if not self._confirm_removal("input device", f"({bus} {itype})"):
            return
        uuid = self.uuid
        run_task(
            lambda: svc_detach_input(uuid, itype, bus),
            done=self._hw_done, failed=self._hw_failed,
        )

    def _hw_done(self, status) -> None:
        self.hw_status.setText(str(status) if status else "")
        self._load_hardware()

    def _hw_failed(self, message: str) -> None:
        ErrorDialog(self, "Hardware change failed", message).exec()

    def _tuning_summary(self) -> str:
        tuning = getattr(self, "_tuning", None)
        if tuning is None:
            return "pinning, hugepages, io limits"
        bits = []
        bits.append(f"{len(tuning.vcpu_pins)} pinned" if tuning.vcpu_pins else "unpinned")
        if tuning.hugepage_size_kb:
            bits.append("hugepages")
        if tuning.iothreads:
            bits.append(f"{tuning.iothreads} iothread(s)")
        if any(t.limited for t in tuning.throttles.values()):
            bits.append("io limited")
        return " · ".join(bits)

    def _features_summary(self) -> str:
        feats = getattr(self, "_features", None)
        if feats is None:
            return "hyper-v, hiding, cpu flags, looking glass"
        bits = []
        if feats.hyperv_on:
            bits.append(f"{len(feats.hyperv_on)} hyper-v")
        if feats.kvm_hidden:
            bits.append("kvm hidden")
        if feats.cpu_features:
            bits.append(f"{len(feats.cpu_features)} cpu flag(s)")
        if feats.shmem_mb:
            bits.append("looking glass")
        if feats.evdev:
            bits.append(f"{len(feats.evdev)} evdev")
        if feats.secure_boot:
            bits.append("secure boot")
        return " · ".join(bits) or "nothing set"

    def _edit_features(self) -> None:
        hw, uuid = self._hw, self.uuid
        if hw is None or not uuid or self._features is None:
            return
        dialog = GuestFeaturesDialog(
            self, self._snap.name if self._snap else "machine", self._features,
            self._feature_support, self._evdev_devices, machine=hw.machine,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        problem = dialog.problem()
        if problem:
            ErrorDialog(self, "Cannot apply these features", problem).exec()
            return
        wanted = dialog.result_features()
        support = self._feature_support
        self.hw_status.setText("applying…")
        run_task(
            lambda: svc_set_features(uuid, wanted, support),
            done=lambda msg: (self.hw_status.setText(msg), self._load_hardware(),
                              self._load_features()),
            failed=lambda m: (self.hw_status.setText(""),
                              ErrorDialog(self, "Could not apply", m).exec()),
        )

    def _load_features(self) -> None:
        uuid = self.uuid
        if not uuid:
            return

        def apply(result) -> None:
            if self.uuid != uuid:
                return
            self._features, self._feature_support, self._evdev_devices = result
            if self._hw is not None:
                self._populate_hw_tree(self._hw)
                self._show_hw_detail()

        run_task(
            lambda: (svc_get_features(uuid), svc_feature_support(), svc_list_evdev()),
            done=apply,
            failed=lambda _m: None,
        )

    def _load_tuning(self) -> None:
        """Tuning and host topology are only needed by this panel, so they are
        fetched alongside the hardware rather than on every poll."""
        uuid = self.uuid
        if not uuid:
            return

        def apply(result) -> None:
            if self.uuid != uuid:
                return
            self._tuning, self._topology = result
            # the bay row's summary was drawn before this arrived
            if self._hw is not None:
                self._populate_hw_tree(self._hw)
                self._show_hw_detail()

        run_task(
            lambda: (svc_get_tuning(uuid), svc_host_topology()),
            done=apply,
            failed=lambda _m: None,
        )

    def _edit_tuning(self) -> None:
        hw, uuid = self._hw, self.uuid
        if hw is None or not uuid or self._tuning is None or self._topology is None:
            return
        dialog = TuningDialog(
            self, self._snap.name if self._snap else "machine", hw.vcpus,
            self._topology, self._tuning, hw.disks,
            guest_topology=hw.topology,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        problem = dialog.topology_problem()
        if problem:
            ErrorDialog(self, "Topology does not add up", problem).exec()
            return
        bad = dialog.invalid_cpus()
        if bad:
            ErrorDialog(
                self, "No such host CPU",
                f"This host has CPUs 0-{self._topology.total_cpus - 1}. "
                f"Asked for: {', '.join(str(c) for c in bad)}.",
            ).exec()
            return
        pins = dialog.pins()
        emulator = dialog.emulator_pin()
        guest_topology = dialog.guest_topology()
        hugepages = dialog.hugepage_size_kb()
        iothreads = dialog.iothread_count()
        throttles = dialog.throttles()
        before = self._tuning

        def work():
            messages = []
            if pins != before.vcpu_pins or emulator != before.emulator_pin:
                messages.append(svc_set_cpu_pinning(uuid, pins, emulator))
            # a guest told it has N independent cores, when its vCPUs are really
            # paired onto N/2, schedules two busy threads onto one core
            if guest_topology and guest_topology != hw.topology:
                messages.append(svc_set_cpu(uuid, hw.cpu_mode, *guest_topology))
            if hugepages != before.hugepage_size_kb:
                messages.append(svc_set_hugepages(uuid, hugepages))
            if iothreads != before.iothreads:
                messages.append(svc_set_iothreads(uuid, iothreads))
            for dev, limits in throttles.items():
                if limits != before.throttles.get(dev, type(limits)()):
                    messages.append(svc_set_disk_throttle(uuid, dev, limits))
            return " · ".join(dict.fromkeys(messages)) or "Nothing changed."

        self.hw_status.setText("applying…")
        run_task(
            work,
            done=lambda msg: (self.hw_status.setText(msg), self._load_hardware(),
                              self._load_tuning()),
            failed=lambda m: (self.hw_status.setText(""),
                              ErrorDialog(self, "Tuning failed", m).exec()),
        )

    def _edit_cpu(self) -> None:
        if not self._hw or not self.uuid:
            return
        hw, uuid = self._hw, self.uuid
        dialog = CpuDialog(
            self, hw, host_cpus=self.host.cpus if self.host else 64
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        mode = dialog.mode.currentText()
        topo = (dialog.sockets.value(), dialog.cores.value(), dialog.threads.value())
        vcpus = dialog.vcpu_count()
        old_topo = hw.topology or (1, max(hw.vcpus, 1), 1)

        def work():
            messages = []
            if mode != hw.cpu_mode or topo != old_topo:
                messages.append(svc_set_cpu(uuid, mode, *topo))
            if vcpus != hw.vcpus:
                messages.append(svc_set_vcpus(uuid, vcpus))
            return messages[-1] if messages else ""

        run_task(work, done=self._hw_done, failed=self._hw_failed)

    def _edit_memory(self) -> None:
        if not self._hw or not self.uuid:
            return
        hw, uuid = self._hw, self.uuid
        dialog = MemoryDialog(
            self, hw, host_mem_mb=self.host.memory_mb if self.host else 262144
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        mem, max_mem = dialog.memory.value(), dialog.max_memory.value()
        mem = min(mem, max_mem)
        if mem == hw.memory_mb and max_mem == hw.max_memory_mb:
            return
        run_task(
            lambda: svc_set_memory(uuid, mem, max_mem),
            done=self._hw_done,
            failed=self._hw_failed,
        )

    def _edit_video(self) -> None:
        if not self._hw or not self.uuid:
            return
        uuid = self.uuid
        dialog = VideoDialog(self, self._hw.video)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        model = dialog.model.currentText()
        run_task(
            lambda: svc_set_video(uuid, model),
            done=self._hw_done,
            failed=self._hw_failed,
        )

    def _edit_disk_cache(self) -> None:
        sel = self._selected_device()
        if not sel or sel[0] not in ("disk", "cdrom") or not self.uuid:
            self.hw_status.setText("select a disk first")
            return
        disk = sel[1]
        uuid = self.uuid
        dialog = DiskCacheDialog(self, disk.dev, disk.cache)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        cache = dialog.cache.currentText()
        run_task(
            lambda: svc_set_disk_cache(uuid, disk.dev, cache),
            done=self._hw_done,
            failed=self._hw_failed,
        )

    def _move_disk(self) -> None:
        sel = self._selected_device()
        if not sel or sel[0] != "disk" or not self.uuid:
            self.hw_status.setText("select a disk first")
            return
        disk = sel[1]
        uuid = self.uuid
        running = self._snap is not None and self._snap.state == "running"

        def show(pools) -> None:
            dialog = MoveDiskDialog(self, disk.dev, pools, disk.source, running)
            if dialog.pool.count() == 0:
                ErrorDialog(
                    self, "Nowhere to move it",
                    "No other active pool on this connection.",
                ).exec()
                return
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return
            pool = dialog.pool.currentText()
            delete_source = dialog.delete_source.isChecked()
            self.hw_status.setText(
                f"moving {disk.dev} to '{pool}'"
                + (" while the machine runs…" if running else "…")
            )
            run_task(
                lambda: svc_move_disk(uuid, disk.dev, pool, delete_source),
                done=lambda msg: (
                    self.hw_status.setText(str(msg)),
                    self._load_hardware(),
                ),
                failed=self._hw_failed,
            )

        run_task(svc_list_pools, done=show, failed=self._hw_failed)

    def _edit_boot_order(self) -> None:
        if not self._hw or not self.uuid:
            return
        uuid = self.uuid
        entries = list(self._hw.boot) or ["hd"]
        dialog = BootOrderDialog(self, entries)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        new_order = dialog.entries()
        run_task(
            lambda: svc_set_boot_order(uuid, new_order),
            done=self._hw_done,
            failed=self._hw_failed,
        )

    def _add_disk(self) -> None:
        uuid = self.uuid
        if not uuid:
            return

        def show(pools) -> None:
            dialog = AttachDiskDialog(self, pools, remote=is_remote_uri(current_uri()))
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return
            bus = dialog.bus.currentText()
            if dialog.create_new.isChecked():
                pool = dialog.pool.currentText()
                vol_name = dialog.vol_name.text().strip() or "disk.qcow2"
                size = dialog.size.value()

                def work():
                    path = svc_create_volume(pool, vol_name, size, "qcow2")
                    return svc_attach_disk(uuid, path, bus, "qcow2")

                run_task(work, done=self._hw_done, failed=self._hw_failed)
            else:
                path = dialog.path.text().strip()
                if not path:
                    return
                run_task(
                    lambda: svc_attach_disk(uuid, path, bus),
                    done=self._hw_done,
                    failed=self._hw_failed,
                )

        run_task(svc_list_pools, done=show, failed=self._hw_failed)

    def _remove_disk(self) -> None:
        sel = self._selected_device()
        if not sel or sel[0] not in ("disk", "cdrom") or not self.uuid:
            return
        dev = sel[1].dev
        uuid = self.uuid
        what = "disc drive" if sel[0] == "cdrom" else "disk"
        if not self._confirm_removal(what, f"({dev})"):
            return
        run_task(
            lambda: svc_detach_disk(uuid, dev),
            done=self._hw_done,
            failed=self._hw_failed,
        )

    def _change_media(self) -> None:
        sel = self._selected_device()
        if not sel or sel[0] != "cdrom" or not self.uuid:
            self.hw_status.setText("select an optical drive first")
            return
        uuid, dev = self.uuid, sel[1].dev
        menu = QMenu(self)
        menu.addAction("Choose ISO…", lambda: self._insert_iso(uuid, dev))
        menu.addAction(
            "Eject",
            lambda: run_task(
                lambda: svc_change_media(uuid, dev, None),
                done=self._hw_done,
                failed=self._hw_failed,
            ),
        )
        menu.exec(self.hw_tree.mapToGlobal(self.hw_tree.rect().center()))

    def _insert_iso(self, uuid: str, dev: str) -> None:
        if is_remote_uri(current_uri()):
            def show(pools) -> None:
                picker = VolumePickerDialog(self, pools)
                if picker.exec() != QDialog.DialogCode.Accepted:
                    return
                path = picker.selected_path()
                if path:
                    run_task(
                        lambda: svc_change_media(uuid, dev, path),
                        done=self._hw_done,
                        failed=self._hw_failed,
                    )

            run_task(svc_list_pools, done=show, failed=self._hw_failed)
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose ISO", "", "Disc images (*.iso);;All files (*)"
        )
        if not path:
            return
        run_task(
            lambda: svc_change_media(uuid, dev, path),
            done=self._hw_done,
            failed=self._hw_failed,
        )

    def _add_nic(self) -> None:
        uuid = self.uuid
        if not uuid:
            return

        def show(networks: list[str]) -> None:
            dialog = AttachNicDialog(self, networks)
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return
            network = dialog.network.currentText()
            model = dialog.model.currentText()
            run_task(
                lambda: svc_attach_nic(uuid, network, model),
                done=self._hw_done,
                failed=self._hw_failed,
            )

        run_task(svc_list_network_names, done=show, failed=self._hw_failed)

    def _remove_nic(self) -> None:
        sel = self._selected_device()
        if not sel or sel[0] != "nic" or not self.uuid:
            return
        mac = sel[1].mac
        uuid = self.uuid
        if not self._confirm_removal("network interface", f"({mac})"):
            return
        run_task(
            lambda: svc_detach_nic(uuid, mac),
            done=self._hw_done,
            failed=self._hw_failed,
        )

    def _add_hostdev(self) -> None:
        uuid = self.uuid
        if not uuid:
            return

        def show(devices) -> None:
            dialog = HostDeviceDialog(self, devices)
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return
            dev = dialog.selected()
            if dev is None:
                return
            run_task(
                lambda: svc_attach_hostdev(uuid, dev.kind, dev.ident),
                done=self._hw_done,
                failed=self._hw_failed,
            )

        run_task(svc_list_host_devices, done=show, failed=self._hw_failed)

    def _remove_hostdev(self) -> None:
        sel = self._selected_device()
        if not sel or sel[0] not in ("usb", "pci", "mdev") or not self.uuid:
            return
        dev = sel[1]
        uuid = self.uuid
        what = "USB device" if dev.kind == "usb" else "PCI device"
        if not self._confirm_removal(what, f"({dev.ident})"):
            return
        run_task(
            lambda: svc_detach_hostdev(uuid, dev.kind, dev.ident),
            done=self._hw_done,
            failed=self._hw_failed,
        )

    def _add_share(self) -> None:
        uuid = self.uuid
        if not uuid:
            return
        dialog = ShareFolderDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        source = dialog.source.text().strip()
        tag = dialog.tag.text().strip()
        driver = dialog.driver.currentText()
        run_task(
            lambda: svc_attach_filesystem(uuid, source, tag, driver),
            done=self._hw_done,
            failed=self._hw_failed,
        )

    def _remove_share(self) -> None:
        sel = self._selected_device()
        if not sel or sel[0] != "fs" or not self.uuid:
            return
        tag = sel[1].tag
        uuid = self.uuid
        if not self._confirm_removal("shared folder", f"({tag})"):
            return
        run_task(
            lambda: svc_detach_filesystem(uuid, tag),
            done=self._hw_done,
            failed=self._hw_failed,
        )

    def _load_hardware(self) -> None:
        uuid = self.uuid

        def apply(hw: Hardware) -> None:
            if self.uuid != uuid:
                return
            self._hw = hw
            self._populate_hw_tree(hw)
            self._show_hw_detail()
            self._update_chips(hw)

        run_task(lambda: svc_get_hardware(uuid), done=apply, failed=self._show_error)

    # -- editors for the newer devices and fields

    def _hw_run(self, fn) -> None:
        run_task(fn, done=self._hw_done, failed=self._hw_failed)

    def _remove_simple(self, tag: str) -> None:
        names = {"watchdog": "watchdog", "redirdev": "USB redirection",
                 "vsock": "vsock", "panic": "panic notifier",
                 "smartcard": "smartcard", "memory": "memory device",
                 "audio": "audio device"}
        if not self._confirm_removal(names.get(tag, tag)):
            return
        uuid = self.uuid
        if uuid:
            self._hw_run(lambda: svc_remove_simple_device(uuid, tag))

    def _edit_labels(self) -> None:
        if not self.uuid or not self._hw:
            return
        uuid = self.uuid
        dialog = LabelsDialog(self, self._hw.title, self._hw.description)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        title = dialog.title_edit.text().strip()
        notes = dialog.notes.toPlainText().strip()
        self._hw_run(lambda: svc_set_labels(uuid, title, notes))

    def _edit_machine_type(self) -> None:
        if not self.uuid or not self._hw:
            return
        uuid, current = self.uuid, self._hw.machine

        def show(types: list[str]) -> None:
            dialog = ChoiceDialog(
                self, "Machine type", "chipset / machine",
                types or [current], current,
                "q35 is the modern chipset; i440fx suits very old guests. "
                "Changing this can stop an installed guest from booting.",
            )
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return
            choice = dialog.value()
            self._hw_run(lambda: svc_set_machine_type(uuid, choice))

        run_task(svc_machine_types, done=show, failed=self._hw_failed)

    def _toggle_boot_menu(self, enable: bool) -> None:
        uuid = self.uuid
        if uuid:
            self._hw_run(lambda: svc_set_boot_menu(uuid, enable))

    def _toggle_accel3d(self, enable: bool) -> None:
        uuid = self.uuid
        if uuid:
            self._hw_run(lambda: svc_set_video_accel(uuid, enable))

    def _edit_nic(self, nic) -> None:
        uuid = self.uuid
        if not uuid:
            return

        def show(networks: list[str], filters: list[str]) -> None:
            dialog = NicEditDialog(self, nic, networks, filters)
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return
            new_mac = dialog.mac.text().strip()
            model = dialog.model.currentText()
            link_up = dialog.link_up.isChecked()
            new_filter = dialog.chosen_filter()
            filter_ip = (
                dialog.filter_ip.text().strip() if dialog.filter_ip else ""
            )
            filter_changed = filters and (new_filter != nic.filter or filter_ip)

            def apply() -> str:
                messages = [svc_set_nic(
                    uuid, nic.mac,
                    new_mac=new_mac if new_mac.lower() != nic.mac.lower() else None,
                    model=model if model != nic.model else None,
                    link_up=link_up,
                )]
                if filter_changed:
                    # the MAC may just have changed above
                    mac_now = new_mac or nic.mac
                    messages.append(
                        "filter: " + svc_set_nic_filter(
                            uuid, mac_now, new_filter, filter_ip
                        )
                    )
                return " · ".join(messages)

            self._hw_run(apply)

        run_task(
            svc_list_network_names,
            done=lambda networks: run_task(
                svc_nwfilter_names,
                done=lambda filters: show(networks, filters),
                failed=lambda _m: show(networks, []),
            ),
            failed=self._hw_failed,
        )

    def _edit_watchdog(self) -> None:
        uuid = self.uuid
        if not uuid or not self._hw or not self._hw.watchdog:
            return
        dialog = ChoiceDialog(
            self, "Watchdog action", "on timeout", list(WATCHDOG_ACTIONS),
            self._hw.watchdog[1],
            "What the host does when the guest stops petting the watchdog.",
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        action = dialog.value()
        self._hw_run(lambda: svc_set_watchdog_action(uuid, action))

    def _edit_audio(self) -> None:
        uuid = self.uuid
        if not uuid or not self._hw:
            return
        dialog = ChoiceDialog(
            self, "Audio backend", "backend", list(AUDIO_BACKENDS),
            self._hw.audio or "spice",
            "Where the emulated sound card's output goes on this host.",
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        backend = dialog.value()
        self._hw_run(lambda: svc_add_audio(uuid, backend))

    def _edit_controller(self, ctype: str, index: int, model: str) -> None:
        uuid = self.uuid
        if not uuid:
            return
        options = {
            "usb": ["qemu-xhci", "nec-xhci", "ich9-ehci1", "piix3-uhci", "none"],
            "scsi": ["virtio-scsi", "lsilogic", "megasas"],
            "pci": ["pcie-root-port", "pcie-to-pci-bridge", "pci-bridge"],
        }.get(ctype, [model])
        dialog = ChoiceDialog(
            self, f"{ctype} controller {index}", "model", options, model,
            "Applies on next start.",
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        choice = dialog.value()
        self._hw_run(lambda: svc_set_controller_model(uuid, ctype, index, choice))

    def _edit_hostdev_options(self, dev) -> None:
        uuid = self.uuid
        if not uuid:
            return
        dialog = HostdevOptionsDialog(self, dev)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        rombar = dialog.rombar.isChecked() if dev.kind == "pci" else None
        policy = dialog.policy.currentText() if dev.kind == "usb" else None
        self._hw_run(
            lambda: svc_set_hostdev_options(
                uuid, dev.kind, dev.ident, rombar=rombar, startup_policy=policy
            )
        )

    def _add_memory_device(self) -> None:
        uuid = self.uuid
        if not uuid:
            return
        from PySide6.QtWidgets import QInputDialog

        size, ok = QInputDialog.getInt(
            self, "Memory device",
            "Size in MiB (hot-pluggable DIMM; adds NUMA and a memory maximum "
            "if the machine has none):",
            1024, 128, 1024 * 512, 128,
        )
        if not ok:
            return
        self._hw_run(lambda: svc_add_memory_device(uuid, size))
