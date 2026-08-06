"""Hardware tab: the component bay and its per-device faceplate."""

from __future__ import annotations

from .common import *  # noqa: F403 - shared imports for the tab modules


class _FlowLayout(QLayout):
    """Lay widgets out left to right, wrapping when the row runs out.

    The faceplate's action buttons are between one and four depending on
    the device, and a row of four is wider than the panel gets when the
    window is not maximised - which pushed the whole faceplate wide and
    clipped every field on it. Qt has no flow layout of its own; this is
    the usual small one, height-for-width so the panel above it knows how
    much room the buttons will actually need.
    """

    def __init__(self, spacing: int = 8) -> None:
        super().__init__()
        self._items: list = []
        self.setSpacing(spacing)
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item) -> None:  # noqa: N802 - Qt's name
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index):  # noqa: N802 - Qt's name
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index):  # noqa: N802 - Qt's name
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    def expandingDirections(self):  # noqa: N802 - Qt's name
        return Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool:  # noqa: N802 - Qt's name
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802 - Qt's name
        return self._lay_out(QRect(0, 0, width, 0), place=False)

    def setGeometry(self, rect) -> None:  # noqa: N802 - Qt's name
        super().setGeometry(rect)
        self._lay_out(rect, place=True)

    def sizeHint(self):  # noqa: N802 - Qt's name
        return self.minimumSize()

    def minimumSize(self):  # noqa: N802 - Qt's name
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        return size

    def _lay_out(self, rect, place: bool) -> int:
        x, y, line_height = rect.x(), rect.y(), 0
        for item in self._items:
            hint = item.sizeHint()
            if x > rect.x() and x + hint.width() > rect.right():
                x = rect.x()
                y += line_height + self.spacing()
                line_height = 0
            if place:
                item.setGeometry(QRect(QPoint(x, y), hint))
            x += hint.width() + self.spacing()
            line_height = max(line_height, hint.height())
        return y + line_height - rect.y()



def _hint_mark(text: str):
    """The "?" beside a field, explaining it on hover.

    These were paragraphs under each field, which read well on one device
    and turned a disk into a page of prose. The explanation is worth
    keeping and worth getting out of the way.
    """
    mark = QLabel("?")
    mark.setObjectName("FieldHint")
    mark.setToolTip(text)
    mark.setFixedSize(18, 18)
    mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
    mark.setCursor(Qt.CursorShape.WhatsThisCursor)
    return mark


def _arrow_icon(up: bool, colour: str, size: int = 12):
    """A triangle, drawn rather than typed.

    ▲ and ▼ are not in the faces this app ships, so they came out of
    whatever fallback the system found - a different weight and baseline
    from everything around them, when they appeared at all. Painting the
    shape is four lines and always looks like the rest of the interface.
    """
    from PySide6.QtCore import QPointF
    from PySide6.QtGui import QIcon, QPainter, QPixmap, QPolygonF

    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setBrush(QColor(colour))
    painter.setPen(Qt.PenStyle.NoPen)
    inset, mid = size * 0.22, size / 2
    tip, base = (inset, size - inset) if up else (size - inset, inset)
    painter.drawPolygon(QPolygonF([
        QPointF(mid, tip), QPointF(inset, base), QPointF(size - inset, base),
    ]))
    painter.end()
    return QIcon(pixmap)


# What a machine can be told to boot from, and what to call each one. The
# faceplate offers every class the machine actually has a device for.
_BOOT_NAMES = {
    "hd": "Hard disk", "cdrom": "Optical drive",
    "network": "Network (PXE)", "fd": "Floppy drive",
}


def _boot_label(entry: str) -> str:
    """A boot entry as something to read, either form."""
    if " " not in entry:
        return _BOOT_NAMES.get(entry, entry)
    kind, _, which = entry.partition(" ")
    return f"{_BOOT_NAMES.get({'disk': 'hd', 'nic': 'network'}.get(kind, kind), kind)} · {which}"


def _boot_candidates(hw) -> list[str]:
    """The boot entries in use, then the ones that could be added.

    libvirt has two ways of saying this - <boot order> on each device, or
    a list of device classes under <os> - and a machine uses one or the
    other. Whichever it already uses is the one offered, because writing
    the other kind would silently drop the existing order.
    """
    order = list(hw.boot or ())
    if any(" " in e for e in order):
        possible = [f"{d.device} {d.dev}" for d in hw.disks]
        possible += [f"nic {n.mac}" for n in hw.nics]
    else:
        possible = []
        if any(d.device != "cdrom" for d in hw.disks):
            possible.append("hd")
        if any(d.device == "cdrom" for d in hw.disks):
            possible.append("cdrom")
        if hw.nics:
            possible.append("network")
    return order + [e for e in possible if e not in order]


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

        # The faceplate carries every property of a device as a live field,
        # so a disk or a display is taller than the tab. Scroll the inside of
        # the card rather than the card itself: the frame stays put and only
        # the fields move, which is what makes the scrollbar readable as
        # "there is more of this device" rather than "the page is long".
        panel_frame = QFrame()
        panel_frame.setProperty("class", "ChartCard")
        frame_box = QVBoxLayout(panel_frame)
        frame_box.setContentsMargins(0, 0, 0, 0)
        panel_body = QWidget()
        panel_body.setObjectName("HwPanelBody")
        self.hw_panel = QVBoxLayout(panel_body)
        self.hw_panel.setContentsMargins(22, 18, 22, 18)
        self.hw_panel.setSpacing(8)
        self.hw_scroll = QScrollArea()
        self.hw_scroll.setWidgetResizable(True)
        self.hw_scroll.setFrameShape(QFrame.Shape.NoFrame)
        # As-needed rather than off: a faceplate that will not fit is a
        # layout to fix, but clipping it hides the fields entirely and
        # scrolling at least leaves them reachable.
        self.hw_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        self.hw_scroll.setWidget(panel_body)
        frame_box.addWidget(self.hw_scroll, 1)

        # Save and Discard sit outside the scrolled area, so a change made
        # at the top of a long faceplate does not put the button that saves
        # it below the fold.
        self.hw_save_bar = QWidget()
        save_row = QHBoxLayout(self.hw_save_bar)
        save_row.setContentsMargins(22, 10, 22, 12)
        save_row.setSpacing(8)
        save_row.addStretch(1)
        discard = QPushButton("Discard")
        discard.setProperty("class", "GhostButton")
        discard.setCursor(Qt.CursorShape.PointingHandCursor)
        discard.clicked.connect(self._discard_fields)
        save_row.addWidget(discard)
        save = QPushButton("Save changes")
        save.setProperty("class", "PrimaryButton")
        save.setCursor(Qt.CursorShape.PointingHandCursor)
        save.clicked.connect(self._save_fields)
        save_row.addWidget(save)
        self.hw_save_bar.setVisible(False)
        frame_box.addWidget(self.hw_save_bar)
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
            *[("gfx", f"{g.type} display · {g.ident}", g) for g in hw.graphics],
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
        "gfx": "display",
        "video": "video adapter",
    }
    # "gfx" and "video" go through svc_remove_display / svc_remove_video, which
    # name what they are removing. svc_remove_simple_device would take the
    # first element with the tag, and a machine may have two displays -
    # removing the wrong one silently is worse than not offering it.

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
        if kind == "gfx":
            if payload is None:
                return None  # a display row always carries its detail
            return lambda: self._remove_display(payload.type, payload.ident)
        if kind == "video":
            return self._remove_video
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

    def _discard_fields(self) -> None:
        """Put the faceplate back to what the machine says."""
        self._boot_draft = None
        self._show_hw_detail()

    def _panel_clear(self) -> None:
        self._fields = []
        self._field_bar = None
        if getattr(self, "hw_save_bar", None) is not None:
            self.hw_save_bar.setVisible(False)
        # setParent(None) before deleteLater, not just deleteLater: the
        # delete does not happen until the event loop runs, and until then
        # the old faceplate's widgets are still children of the panel. A
        # redraw would otherwise leave two of every field in the tree.
        def drop(w) -> None:
            w.setParent(None)
            w.deleteLater()

        while self.hw_panel.count():
            item = self.hw_panel.takeAt(0)
            w = item.widget()
            if w is not None:
                drop(w)
            elif item.layout() is not None:
                sub = item.layout()
                while sub.count():
                    s = sub.takeAt(0)
                    if s.widget():
                        drop(s.widget())
                sub.deleteLater()

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
        v.setWordWrap(True)  # a path wraps at its slashes rather than pushing
        v.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        row.addWidget(k, alignment=Qt.AlignmentFlag.AlignTop)
        row.addWidget(v, 1)
        self.hw_panel.addLayout(row)

    # ---------------------------------------------------------------- fields
    #
    # A faceplate row that can be changed is the widget itself, not a reading
    # with an Edit button beside it. Each one registers what it is worth
    # applying and how; Save walks the ones whose value moved, Discard just
    # redraws the panel from the machine.

    def _panel_control(self, key: str, widget, hint: str = "") -> None:
        """A control on the faceplate that is not itself a value.

        The boot arrows are the case: they rearrange the list that is the
        value, and are not worth saving on their own.
        """
        self._panel_field(key, widget, None, None, hint)

    def _panel_field(self, key: str, widget, read, apply, hint: str = "",
                     wide: bool = False) -> None:
        """One editable property. `read` returns the widget's current value,
        `apply` takes it and returns a message.

        Several fields may share one `apply` when libvirt takes them
        together - a CPU's sockets, cores and threads are one call, not
        three. Save calls each distinct applier once; a shared one should
        read the widgets itself and ignore the value it is handed.
        """
        row = QHBoxLayout()
        k = QLabel(key.upper())
        k.setProperty("class", "StatKey")
        k.setFixedWidth(90)
        row.addWidget(k, alignment=Qt.AlignmentFlag.AlignTop)
        row.addWidget(widget, 1)
        if hint:
            row.addWidget(_hint_mark(hint), 0, Qt.AlignmentFlag.AlignTop)
        if wide:  # a notes box wants the whole width, under its label
            self.hw_panel.addWidget(k)
            row.removeWidget(k)
            k.setFixedWidth(90)
        self.hw_panel.addLayout(row)
        if read is None:
            return
        self._fields.append((read, read(), apply))
        for signal in ("textEdited", "textChanged", "currentTextChanged",
                       "toggled", "valueChanged"):
            if hasattr(widget, signal):
                getattr(widget, signal).connect(self._field_touched)
                break

    def _panel_watch(self, read, original, apply) -> None:
        """Register a value that is not one widget.

        The boot order is the case: it is a list the arrows and the tick
        boxes both rewrite, so what is dirty is the list, not any control
        on the faceplate.
        """
        self._fields.append((read, original, apply))

    def _field_touched(self, *_args) -> None:
        if getattr(self, "_field_bar", None) is not None:
            self._field_bar.setVisible(bool(self._dirty_fields()))

    def _dirty_fields(self) -> list:
        return [
            (read, apply) for read, original, apply in self._fields
            if read() != original
        ]

    def _panel_save_bar(self) -> None:
        """Arm the faceplate's Save and Discard pair.

        The bar itself is built once and lives below the scroll area; a
        faceplate with editable fields on it says so here. Not simply
        hidden on arming: the boot arrows redraw the faceplate mid-edit,
        and the change they are part of has to still look unsaved.
        """
        self._field_bar = self.hw_save_bar
        self.hw_save_bar.setVisible(bool(self._dirty_fields()))

    def _save_fields(self) -> None:
        changed = self._dirty_fields()
        if not changed:
            return
        # One call per distinct applier, in the order the fields appear:
        # fields that libvirt takes together share theirs.
        values: list = []
        for read, apply in changed:
            if not any(apply is seen for seen, _v in values):
                values.append((apply, read()))
        self.hw_status.setText(f"applying {len(values)} change(s)…")

        def work():
            return " · ".join(dict.fromkeys(
                str(apply(value)) for apply, value in values
            ))

        run_task(
            work,
            done=lambda msg: (self.hw_status.setText(str(msg)),
                              self._load_hardware()),
            failed=self._hw_failed,
        )

    def _panel_actions(self, *buttons) -> None:
        self.hw_panel.addSpacing(10)
        row = _FlowLayout()
        for btn in buttons:
            row.addWidget(btn)
        self.hw_panel.addLayout(row)

    @staticmethod
    def _hw_ident(kind: str, payload) -> str:
        if kind in ("disk", "cdrom"):
            return payload.dev
        if kind == "nic":
            return payload.mac
        if kind == "gfx":
            return payload.type
        if kind == "sound":
            return str(payload)
        if kind == "input":
            return f"{payload[0]}/{payload[1]}"
        if kind in ("usb", "pci", "mdev"):
            return payload.ident
        if kind == "fs":
            return payload.tag
        if kind == "controller":
            ctype, index, _model = payload
            return f"{ctype}/{index}"
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
        hw = self._hw
        sel = self._selected_device()
        # A half-rearranged boot order belongs to the row it was started on.
        showing = (sel[0], self._hw_ident(*sel)) if sel else None
        if showing != getattr(self, "_hw_showing", None):
            self._boot_draft = None
            self._hw_showing = showing
            if getattr(self, "hw_scroll", None) is not None:
                self.hw_scroll.verticalScrollBar().setValue(0)
        self._panel_clear()
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
            self._show_cpu_detail(badge, hw)
        elif kind == "mem":
            self._show_memory_detail(badge, hw)
        elif kind == "boot":
            self._show_boot_detail(badge, hw)
        elif kind in ("disk", "cdrom"):
            d = payload
            self._panel_title(badge, f"{d.dev} - {'optical drive' if kind == 'cdrom' else 'disk'}")
            self._panel_row("source", d.source)
            self._panel_row("bus", d.bus)
            self._panel_row("format", d.format)

            cache = QComboBox()
            cache.addItems(["default", "none", "writeback", "writethrough",
                            "directsync", "unsafe"])
            cache.setCurrentText(d.cache or "default")
            self._panel_field(
                "cache", cache, cache.currentText,
                lambda v: svc_set_disk_cache(self.uuid, d.dev, v),
                "none is safest for a host crash and best for raw throughput; "
                "writeback is faster for bursty writes; unsafe is for a "
                "machine you would not mind losing. Takes effect at the next "
                "start.",
            )

            serial = QLineEdit(d.serial)
            serial.setPlaceholderText("what the guest reads as the serial")
            self._panel_field(
                "serial", serial, serial.text,
                lambda v: svc_set_disk_options(self.uuid, d.dev, serial=v),
                "udev names the drive /dev/disk/by-id/…-<serial> inside the "
                "guest, so setting this gives it a name that survives the "
                "disks being reordered.",
            )
            discard = QComboBox()
            discard.addItems(["default", "unmap", "ignore"])
            discard.setCurrentText(d.discard or "default")
            self._panel_field(
                "discard", discard, discard.currentText,
                lambda v: svc_set_disk_options(
                    self.uuid, d.dev, discard="" if v == "default" else v),
                "unmap passes the guest's TRIM through to the host image, "
                "which is what stops a thin image only ever growing.",
            )
            ro = QCheckBox("Write-protected")
            ro.setChecked(d.readonly)
            self._panel_field(
                "read only", ro, ro.isChecked,
                lambda v: svc_set_disk_options(self.uuid, d.dev, readonly=v),
                "Enforced by QEMU rather than asked of the guest, so a write "
                "fails at the device instead of reaching the image.",
            )
            sh = QCheckBox("Shared between machines")
            sh.setChecked(d.shareable)
            self._panel_field(
                "shareable", sh, sh.isChecked,
                lambda v: svc_set_disk_options(self.uuid, d.dev, shareable=v),
                "Nothing coordinates the writes, so this is for a cluster "
                "filesystem or a disk both sides only read. Anything else "
                "corrupts it.",
            )
            self._panel_save_bar()

            buttons = []
            if kind == "cdrom":
                buttons.append(_ghost("Change media…", self._change_media))
            if kind == "disk":
                buttons.append(_ghost("Grow…", self._grow_disk))
                buttons.append(_ghost("Move to pool…", self._move_disk))
            buttons.append(_ghost("Remove", self._remove_disk))
            self._panel_actions(*buttons)
        elif kind == "nic":
            self._show_nic_detail(badge, payload)
        elif kind == "video":
            self._panel_title(badge, "Video adapter")
            model = QComboBox()
            known = ["virtio", "qxl", "vga", "bochs", "ramfb", "none"]
            # Keep whatever it actually has, even when it is not one of these:
            # a combo that cannot show the current value reads as a request
            # to change it the moment anything else on the faceplate is saved.
            model.addItems(known if hw.video in known else [hw.video, *known])
            model.setCurrentText(hw.video)
            self._panel_field(
                "model", model, model.currentText,
                lambda v: svc_set_video(self.uuid, v),
                "virtio for a modern guest with its driver installed, qxl for "
                "SPICE with more than one monitor, vga for something ancient. "
                "Takes effect at the next start.",
            )
            accel = QCheckBox("Accelerated")
            accel.setChecked(hw.video_accel3d)
            self._panel_field(
                "3d", accel, accel.isChecked,
                lambda v: svc_set_video_accel(self.uuid, v),
                "virtio only, and it needs OpenGL on the display as well.",
            )
            self._panel_save_bar()
            self._panel_actions(_ghost("Remove", self._remove_video))
        elif kind == "gfx":
            self._show_display_detail(badge, payload)
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
            if h.kind == "pci":
                self._pci_option_fields(h)
            elif h.kind == "usb":
                policy = QComboBox()
                policy.addItems(["mandatory", "requisite", "optional"])
                policy.setCurrentText(h.startup_policy or "mandatory")
                self._panel_field(
                    "if missing", policy, policy.currentText,
                    lambda v: svc_set_hostdev_options(
                        self.uuid, "usb", h.ident, startup_policy=v),
                    "mandatory refuses to start the machine without the "
                    "device; optional starts anyway and picks it up when it "
                    "is plugged in.",
                )
                self._panel_save_bar()
            self._panel_actions(_ghost("Detach from machine", self._remove_hostdev))
        elif kind == "labels":
            self._panel_title(badge, "Name and notes")
            title = QLineEdit(hw.title)
            title.setPlaceholderText("Build server")
            notes = QPlainTextEdit(hw.description)
            notes.setMinimumHeight(120)

            def save_labels(_v) -> str:
                return svc_set_labels(
                    self.uuid, title.text().strip(), notes.toPlainText().strip()
                )

            self._panel_field(
                "title", title, title.text, save_labels,
                "A friendly label shown beside the machine name. The name "
                "itself is what libvirt knows it as and does not change here.",
            )
            self._panel_field(
                "notes", notes, notes.toPlainText, save_labels,
                wide=True,
            )
            self._panel_save_bar()
        elif kind == "watchdog":
            model, action = payload
            self._panel_title(badge, f"Watchdog - {model}")
            self._panel_row("model", model)
            on_timeout = QComboBox()
            on_timeout.addItems(list(WATCHDOG_ACTIONS))
            on_timeout.setCurrentText(action)
            self._panel_field(
                "on timeout", on_timeout, on_timeout.currentText,
                lambda v: svc_set_watchdog_action(self.uuid, v),
                "The guest has to run a watchdog daemon. If it stops petting "
                "the device, the host takes this action.",
            )
            self._panel_save_bar()
            self._panel_actions(
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
            backend = QComboBox()
            backend.addItems(list(AUDIO_BACKENDS))
            backend.setCurrentText(str(payload) or "spice")
            self._panel_field(
                "backend", backend, backend.currentText,
                lambda v: svc_add_audio(self.uuid, v),
                "Where the emulated sound card's output goes on this host. "
                "spice sends it down the console connection.",
            )
            self._panel_save_bar()
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
            options = CONTROLLER_MODELS.get(ctype, [])
            choice = QComboBox()
            choice.addItems(options if model in options else [model, *options])
            choice.setCurrentText(model)
            self._panel_field(
                "model", choice, choice.currentText,
                lambda v: svc_set_controller_model(self.uuid, ctype, index, v),
                "Takes effect at the next start.",
            )
            self._panel_save_bar()
        elif kind == "fs":
            f = payload
            self._panel_title(badge, f"Shared folder - {f.tag}")
            self._panel_row("host path", f.source)
            self._panel_row("driver", f.driver)
            self._panel_row("mount", f"mount -t {f.driver if f.driver == 'virtiofs' else '9p'} {f.tag} /mnt")
            self._panel_actions(_ghost("Remove", self._remove_share))
        self.hw_panel.addStretch(1)

    def _pci_option_fields(self, dev) -> None:
        """ROM BAR and video BIOS file, with the dump beside the field.

        Dumping is an action rather than a property - it reads the card and
        writes a file - so it stays a button, but it fills in the field it
        belongs to instead of living in a separate window.
        """
        rombar = QCheckBox("Exposed to the guest")
        rombar.setChecked(dev.rom_bar)
        rom_file = QLineEdit(dev.rom_file)
        rom_file.setPlaceholderText("no vBIOS file - the card's own ROM is used")

        def save_pci(_v) -> str:
            return svc_set_hostdev_options(
                self.uuid, "pci", dev.ident,
                rombar=rombar.isChecked(), rom_file=rom_file.text().strip(),
            )

        self._panel_field(
            "option rom", rombar, rombar.isChecked, save_pci,
            "Turn it off when a passed-through GPU's video BIOS stops the "
            "guest from booting.",
        )

        holder = QWidget()
        row = QHBoxLayout(holder)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        row.addWidget(rom_file, 1)
        dump = QPushButton("Dump…")
        dump.setProperty("class", "GhostButton")
        dump.setCursor(Qt.CursorShape.PointingHandCursor)
        dump.clicked.connect(lambda: self._dump_rom(dev, rom_file))
        row.addWidget(dump)
        self._panel_field(
            "vbios", holder, rom_file.text, save_pci,
            "Some cards - consumer NVIDIA especially - will not initialise in "
            "a guest unless it is handed a copy of their video BIOS. Dump "
            "reads it from the card and trims it to the legacy image a guest "
            "looks for; the card must not be in use, so do it with the "
            "machine shut down, ideally before the host driver has claimed it.",
        )
        rom_file.textEdited.connect(self._field_touched)
        self._panel_save_bar()

    def _dump_rom(self, dev, rom_file) -> None:
        """Read the card's ROM as root, then trim it to the legacy image."""
        from PySide6.QtWidgets import QFileDialog

        dest, _ = QFileDialog.getSaveFileName(
            self, "Save the video BIOS as",
            f"{dev.ident.replace(':', '_')}.rom", "ROM images (*.rom)",
        )
        if not dest:
            return

        def work():
            message = svc_dump_rom(dev.ident, dest)
            with open(dest, "rb") as f:
                data = f.read()
            trimmed = trim_rom_to_legacy(data)
            note = ""
            if trimmed != data:
                with open(dest, "wb") as f:
                    f.write(trimmed)
                note = f" · trimmed to its {len(trimmed) // 1024} KB legacy image"
            ids = read_device_ids(dev.ident)
            if not rom_matches_device(trimmed, ids):
                note += (
                    f" · warning: the ROM does not name {ids.ident}, so it may "
                    "belong to another card"
                )
            return message + note

        def filled(message) -> None:
            rom_file.setText(dest)
            rom_file.textEdited.emit(dest)  # so Save notices
            self.hw_status.setText(str(message))

        self.hw_status.setText("reading the card's ROM…")
        run_task(work, done=filled, failed=self._hw_failed)

    def _show_nic_detail(self, badge: str, n) -> None:
        """MAC, model, link state and filter, in place.

        MAC and model are one libvirt write and the filter is another, so
        they have separate appliers - a filter that will not attach should
        not take a MAC change down with it.
        """
        self._panel_title(badge, f"Network interface - {n.source or 'direct'}")
        self._panel_row("network", n.source or " - ")

        mac = QLineEdit(n.mac)
        mac.setPlaceholderText("52:54:00:…")
        model = QComboBox()
        known = ["virtio", "e1000e", "e1000", "rtl8139"]
        model.addItems(known if n.model in known else [n.model, *known])
        model.setCurrentText(n.model)
        link = QCheckBox("Connected")
        link.setChecked(n.link_up)

        def save_nic(_v) -> str:
            typed = mac.text().strip()
            return svc_set_nic(
                self.uuid, n.mac,
                new_mac=typed if typed.lower() != n.mac.lower() else None,
                model=model.currentText() if model.currentText() != n.model else None,
                link_up=link.isChecked(),
            )

        self._panel_field(
            "mac", mac, mac.text, save_nic,
            "The guest sees this as the card's hardware address, and a DHCP "
            "reservation is usually keyed on it.",
        )
        self._panel_field("model", model, model.currentText, save_nic,
                          "virtio is fastest and needs the guest's driver; "
                          "e1000e is what an unmodified guest recognises.")
        self._panel_field(
            "link", link, link.isChecked, save_nic,
            "Unticking is the software equivalent of pulling the cable out - "
            "the card stays on the machine and the guest sees it go down.",
        )

        nwfilter = QComboBox()
        nwfilter.addItem("(none)")
        if n.filter:
            nwfilter.addItem(n.filter)
        nwfilter.setCurrentText(n.filter or "(none)")
        self._panel_field(
            "filter", nwfilter,
            lambda: "" if nwfilter.currentText() == "(none)" else nwfilter.currentText(),
            lambda v: svc_set_nic_filter(self.uuid, mac.text().strip() or n.mac,
                                         v, ""),
            "A libvirt network filter, applied by the host to everything this "
            "card sends and receives.",
        )
        self._fill_nic_choices(nwfilter, n.filter)
        self._panel_save_bar()
        self._panel_actions(_ghost("Remove", self._remove_nic))

    def _fill_nic_choices(self, combo, current: str) -> None:
        """The filters this host defines, once they have been read."""
        from shiboken6 import isValid

        def show(names: list[str]) -> None:
            if not isValid(combo):
                return
            combo.blockSignals(True)
            combo.clear()
            combo.addItems(["(none)", *names])
            combo.setCurrentText(current or "(none)")
            combo.blockSignals(False)

        run_task(svc_nwfilter_names, done=show, failed=lambda _m: None)

    def _show_cpu_detail(self, badge: str, hw) -> None:
        """Model, topology and machine type, all in place.

        libvirt takes the model and the topology in one write and the vCPU
        count in another, so the four controls that make up the first share
        an applier and the count is derived from them rather than typed -
        a topology and a vCPU count that disagree is a machine that will
        not start.
        """
        self._panel_title(badge, "Processor")

        mode = QComboBox()
        mode.addItems(["host-passthrough", "host-model", "custom"])
        mode.setCurrentText(
            hw.cpu_mode if hw.cpu_mode in
            ("host-passthrough", "host-model", "custom") else "custom"
        )
        sockets, cores, threads = hw.topology or (1, max(hw.vcpus, 1), 1)
        host_cpus = self.host.cpus if self.host else 64
        spins = {}
        for name, value in (("sockets", sockets), ("cores", cores),
                            ("threads", threads)):
            spin = QSpinBox()
            spin.setRange(1, max(host_cpus, value))
            spin.setValue(value)
            spins[name] = spin
        total = QLabel("")
        total.setProperty("class", "ChartValue")

        def recount(*_a) -> None:
            n = (spins["sockets"].value() * spins["cores"].value()
                 * spins["threads"].value())
            total.setText(f"= {n} vcpu" + ("s" if n != 1 else ""))

        def save_cpu(_v) -> str:
            topo = (spins["sockets"].value(), spins["cores"].value(),
                    spins["threads"].value())
            messages = [svc_set_cpu(self.uuid, mode.currentText(), *topo)]
            wanted = topo[0] * topo[1] * topo[2]
            if wanted != hw.vcpus:
                messages.append(svc_set_vcpus(self.uuid, wanted))
            return messages[-1]

        self._panel_field(
            "model", mode, mode.currentText, save_cpu,
            "host-passthrough is fastest; host-model still migrates to a "
            "different CPU; custom is the most compatible and the slowest. "
            "Takes effect at the next start.",
        )
        # One row each rather than three side by side: together they wanted
        # more width than the panel has, and the panel then clipped rather
        # than scrolled.
        for name, note in (
            ("sockets", "Physical packages. More than one only matters to a "
                        "guest that counts licences per socket."),
            ("cores", "Cores per socket."),
            ("threads", "Hardware threads per core - 2 to mirror a host with "
                        "SMT, 1 to hide it from the guest."),
        ):
            self._panel_field(name, spins[name], spins[name].value, save_cpu,
                              note)
            spins[name].valueChanged.connect(recount)
        recount()
        self._panel_field(
            "vcpus", total, lambda: None, None,
            "The three above multiplied together. The count applies live "
            "where the guest supports it; the shape does not.",
        )

        machine = QComboBox()
        machine.addItem(hw.machine)
        machine.setCurrentText(hw.machine)
        self._panel_field(
            "machine", machine, machine.currentText,
            lambda v: svc_set_machine_type(self.uuid, v),
            "q35 is the modern chipset, i440fx suits very old guests. "
            "Changing it on a machine with an installed guest can stop it "
            "booting.",
        )
        self._fill_machine_types(machine)

        self._panel_save_bar()
        self._panel_row("firmware", hw.firmware)
        self._panel_row("hypervisor", hw.hypervisor or " - ")
        self._panel_row("architecture", hw.arch or " - ")
        self._panel_row("emulator", hw.emulator or " - ")
        self._panel_row("uuid", hw.uuid or " - ")

    def _fill_machine_types(self, combo) -> None:
        """The chipsets this host's QEMU offers, read once and kept.

        A libvirt capabilities read is too slow to do every time a row is
        clicked, so the combo starts holding only the current value and
        gains the rest when the list arrives.
        """
        from shiboken6 import isValid

        def show(types: list[str]) -> None:
            # The read is async: the row may have been clicked away from,
            # taking the combo with it, before the list arrives.
            if not isValid(combo) or not types:
                return
            current = combo.currentText()
            combo.blockSignals(True)
            combo.clear()
            combo.addItems(types if current in types else [current, *types])
            combo.setCurrentText(current)
            combo.blockSignals(False)

        if getattr(self, "_machine_types", None):
            show(self._machine_types)
            return

        def keep(types: list[str]) -> None:
            self._machine_types = types
            show(types)

        run_task(svc_machine_types, done=keep, failed=lambda _m: None)

    def _show_memory_detail(self, badge: str, hw) -> None:
        self._panel_title(badge, "Memory")
        host_mb = self.host.memory_mb if self.host else 262144
        # 128 MiB is a sensible smallest thing to ask for, but a machine that
        # is already smaller has to stay editable: a floor above its current
        # size silently raises the value the moment the faceplate is drawn.
        floor = max(1, min(128, hw.memory_mb, hw.max_memory_mb))
        ceiling = max(host_mb, hw.max_memory_mb, hw.memory_mb)
        current = QSpinBox()
        current.setRange(floor, ceiling)
        current.setSingleStep(512)
        current.setSuffix(" MiB")
        current.setValue(hw.memory_mb)
        maximum = QSpinBox()
        maximum.setRange(floor, ceiling)
        maximum.setSingleStep(512)
        maximum.setSuffix(" MiB")
        maximum.setValue(hw.max_memory_mb)

        def save_memory(_v) -> str:
            top = maximum.value()
            return svc_set_memory(self.uuid, min(current.value(), top), top)

        self._panel_field(
            "current", current, current.value, save_memory,
            "Balloons live while the machine runs. It cannot go above the "
            "maximum, and asking for more than that quietly gets the maximum.",
        )
        self._panel_field(
            "maximum", maximum, maximum.value, save_memory,
            "Only changes across a restart: it is the size of the address "
            "space the guest is given at boot.",
        )
        shared = QCheckBox("Mappable by the host")
        shared.setChecked(hw.shared_memory)
        self._panel_field(
            "shared", shared, shared.isChecked,
            lambda on: svc_set_shared_memory(self.uuid, on),
            "virtiofs shared folders and Looking Glass both need the guest's "
            "memory to be mappable by another process on the host. Takes "
            "effect at the next start.",
        )
        self._panel_save_bar()

    def _show_boot_detail(self, badge: str, hw) -> None:
        """Tick devices on and off, and move them, without a dialog.

        The order is held as a draft while it is being rearranged: the
        arrows and the tick boxes both rewrite it, so what is dirty is the
        list rather than any one control. Discard drops the draft.
        """
        self._panel_title(badge, "Boot order")
        if self._boot_draft is None:
            self._boot_draft = [
                (entry, entry in (hw.boot or ()))
                for entry in _boot_candidates(hw)
            ]
        draft = self._boot_draft

        def move(index: int, delta: int) -> None:
            target = index + delta
            if not 0 <= target < len(draft):
                return
            draft[index], draft[target] = draft[target], draft[index]
            self._show_hw_detail()

        def toggle(index: int, on: bool) -> None:
            draft[index] = (draft[index][0], on)
            self._field_touched()

        position = 0
        for i, (entry, on) in enumerate(draft):
            if on:
                position += 1
            holder = QWidget()
            row = QHBoxLayout(holder)
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(6)
            box = QCheckBox(_boot_label(entry))
            box.setChecked(on)
            box.toggled.connect(lambda v, _i=i: toggle(_i, v))
            row.addWidget(box, 1)
            for up, delta, usable in ((True, -1, i > 0),
                                      (False, 1, i < len(draft) - 1)):
                arrow = QPushButton()
                arrow.setIcon(_arrow_icon(
                    up, theme.TEXT if usable else theme.TEXT_FAINT
                ))
                arrow.setProperty("class", "GhostButton")
                arrow.setFixedWidth(30)
                arrow.setEnabled(usable)
                arrow.setToolTip("Move up" if up else "Move down")
                arrow.setCursor(Qt.CursorShape.PointingHandCursor)
                arrow.clicked.connect(lambda _=False, _i=i, _d=delta: move(_i, _d))
                row.addWidget(arrow)
            self._panel_control(f"{position}." if on else "off", holder)

        self._panel_watch(
            lambda: tuple(e for e, on in draft if on),
            tuple(hw.boot or ()),
            lambda order: svc_set_boot_order(self.uuid, list(order)),
        )
        menu = QCheckBox("Offered at startup")
        menu.setChecked(hw.boot_menu)
        self._panel_field(
            "boot menu", menu, menu.isChecked,
            lambda v: svc_set_boot_menu(self.uuid, v),
            "The firmware waits a moment for Esc or F12 so you can pick "
            "something other than the first entry.",
        )
        self._panel_save_bar()

    def _show_display_detail(self, badge: str, g) -> None:
        """A display, with everything about it editable in place.

        None of it can hot-plug, so every field here lands at the next
        start. Password and listen address are the two that matter for
        reaching it from another machine, and they are the two people
        most often go to raw XML for.
        """
        self._panel_title(badge, f"{g.type.upper()} display")

        gtype = QComboBox()
        gtype.addItems(["spice", "vnc"])
        gtype.setCurrentText(g.type)
        self._panel_field(
            "type", gtype, gtype.currentText,
            lambda v: svc_set_display_type(self.uuid, g.type, v),
            "SPICE carries the clipboard, USB redirection and audio; VNC is "
            "reachable from anything.",
        )

        listen = QComboBox()
        listen.addItems(["address", "socket", "none"])
        listen.setCurrentText(g.listen_type or "address")
        self._panel_field(
            "listen", listen, listen.currentText,
            lambda v: svc_set_graphics(self.uuid, g.type, g.ident,
                                       listen_type=v),
            "none is what a machine with its GPU handed over wants: the "
            "display exists for the agent's channel and listens nowhere.",
        )

        address = QComboBox()
        address.setEditable(True)
        address.addItems(["127.0.0.1", "0.0.0.0", "::"])
        address.setCurrentText(g.address or "127.0.0.1")
        self._panel_field(
            "address", address, address.currentText,
            lambda v: svc_set_graphics(self.uuid, g.type, g.ident,
                                       listen_type="address", address=v),
            "0.0.0.0 puts the console on the network. Without a password "
            "that is an open seat at the machine's keyboard.",
        )

        auto = QCheckBox("Chosen by libvirt")
        auto.setChecked(g.autoport)
        self._panel_field(
            "auto port", auto, auto.isChecked,
            lambda v: svc_set_graphics(self.uuid, g.type, g.ident, autoport=v),
            "libvirt hands out the first free port from 5900 up at each "
            "start, so the console's port moves between runs.",
        )
        port = QSpinBox()
        port.setRange(-1, 65535)
        port.setValue(g.port)
        port.setSpecialValueText("automatic")
        self._panel_field(
            "port", port, port.value,
            lambda v: svc_set_graphics(self.uuid, g.type, g.ident, port=v),
            "Setting a port turns the automatic choice off, or the port "
            "would be ignored.",
        )

        password = QLineEdit(g.password)
        password.setEchoMode(QLineEdit.EchoMode.Password)
        password.setPlaceholderText("no password")
        show = QCheckBox("Show")
        show.toggled.connect(lambda on: password.setEchoMode(
            QLineEdit.EchoMode.Normal if on else QLineEdit.EchoMode.Password
        ))
        holder = QWidget()
        row = QHBoxLayout(holder)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(password, 1)
        row.addWidget(show)
        self._panel_field(
            "password", holder, password.text,
            lambda v: svc_set_graphics(self.uuid, g.type, g.ident, password=v),
            "Asked for on connecting. Stored in the definition as plain "
            "text, which anyone who can read the machine's XML can see.",
        )
        password.textEdited.connect(self._field_touched)

        gl = QCheckBox("Enabled")
        gl.setChecked(g.gl)
        self._panel_field(
            "opengl", gl, gl.isChecked,
            lambda v: svc_set_graphics(self.uuid, g.type, g.ident, gl=v),
            "Renders on the host GPU and hands over the buffer. It needs a "
            "virtio video adapter, and it only works locally - a display "
            "with OpenGL on cannot be reached over the network.",
        )
        self._panel_save_bar()

        hint = QLabel(
            "The Console tab connects to this display. With both a VNC "
            "and a SPICE display it uses the VNC one, so remove that to "
            "work over SPICE - which is what the shared clipboard needs."
        )
        hint.setWordWrap(True)
        hint.setObjectName("ConsoleHint")
        self.hw_panel.addWidget(hint)
        self._panel_actions(
            _ghost("Remove", lambda: self._remove_display(g.type, g.ident)),
        )

    def _install_menu(self, anchor: QPushButton) -> None:
        menu = self._build_install_menu()
        if menu is not None:
            menu.exec(anchor.mapToGlobal(anchor.rect().bottomLeft()))

    def _build_install_menu(self):
        """Everything that can be added to this machine, minus what it has.

        Separate from showing it so a test can walk the whole menu: exec()
        blocks, so anything only reached while it is open - a submenu built
        from the machine's current devices, say - is otherwise unreachable
        from the suite.
        """
        if not self.uuid or not self._hw:
            return None
        uuid = self.uuid
        menu = QMenu(self)
        menu.addAction("Disk…", self._add_disk)
        menu.addAction("virtio-win driver disc (Windows)…", self._add_virtio_iso)
        menu.addAction("Network interface…", self._add_nic)
        menu.addAction("Shared folder…", self._add_share)
        menu.addAction("Host device (USB / PCI)…", self._add_hostdev)
        menu.addAction("Mediated device (vGPU)…", self._add_mdev)
        menu.addAction("Check PCI passthrough…", self._passthrough_check)
        menu.addAction("Single-GPU passthrough…", self._single_gpu_setup)
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
        have = {g.type for g in self._hw.graphics}
        for gtype in ("vnc", "spice"):
            if gtype not in have:
                displays.addAction(
                    f"{gtype.upper()} display",
                    lambda _=False, g=gtype: run_task(
                        lambda: svc_add_display(uuid, g),
                        done=self._hw_done, failed=self._hw_failed,
                    ),
                )
        return menu

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

        def show(report) -> None:
            self.hw_status.setText("")
            dialog = PassthroughDialog(
                self, report, persisted=persisted_ids(),
                iommu_hint=iommu_advice(),
            )

            def done(message: str) -> None:
                dialog.status.setText(str(message))

            def failed(message: str) -> None:
                dialog.status.setText(str(message))

            def whole_card(dev) -> list[str]:
                """Every function of the card, because a group moves together."""
                return function_siblings(dev.address)

            def bind(dev) -> None:
                addresses = whole_card(dev)
                if dev.attached_to:
                    dialog.status.setText(
                        f"{dev.address} is assigned to '{dev.attached_to}' - "
                        "shut that machine down first"
                    )
                    return
                dialog.status.setText(
                    f"binding {', '.join(addresses)} to vfio-pci…"
                )
                run_task(
                    lambda: svc_bind_vfio(addresses), done=done, failed=failed
                )

            def restore(dev) -> None:
                addresses = whole_card(dev)
                dialog.status.setText("handing back to the host…")
                run_task(
                    lambda: svc_restore_driver(addresses),
                    done=done, failed=failed,
                )

            def persist(dev) -> None:
                addresses = whole_card(dev)
                idents = []
                for address in addresses:
                    ids = read_device_ids(address)
                    if ids.vendor and ids.device:
                        idents.append(ids.ident)
                if not idents:
                    dialog.status.setText("could not read the card's PCI ids")
                    return
                if not ConfirmDialog(
                    dialog, "Bind at boot",
                    "This writes /etc/modprobe.d/vmmanager-vfio.conf claiming "
                    f"{', '.join(idents)} for vfio-pci, and rebuilds the "
                    "initramfs so it is read early enough to matter.\n\n"
                    "The host will not drive this card after the next reboot. "
                    "On a single-GPU machine, set the passthrough hooks up "
                    "first, or you will boot to a black screen.",
                    "Write it",
                ).exec() == QDialog.DialogCode.Accepted:
                    return
                dialog.status.setText("writing and rebuilding the initramfs…")
                run_task(
                    lambda: svc_persist_vfio(idents),
                    done=lambda m: (
                        done(m),
                        dialog._unpersist.setVisible(True),
                    ),
                    failed=failed,
                )

            def unpersist(_dev) -> None:
                dialog.status.setText("removing and rebuilding…")
                run_task(
                    lambda: svc_clear_persist_vfio(),
                    done=lambda m: (
                        done(m),
                        dialog._unpersist.setVisible(False),
                    ),
                    failed=failed,
                )

            dialog.bind_requested = bind
            dialog.restore_requested = restore
            dialog.persist_requested = persist
            dialog.unpersist_requested = unpersist
            dialog.exec()

        run_task(svc_iommu_report, done=show, failed=self._hw_failed)

    def _single_gpu_setup(self) -> None:
        """Write the libvirt hooks that hand this host's only card over."""
        uuid = self.uuid
        snap = self._snap
        if not uuid or snap is None:
            return
        if is_remote_uri(current_uri()):
            ErrorDialog(
                self, "Local hosts only",
                "The hooks are written to this host's /etc/libvirt/hooks, so "
                "they can only be set up on a local connection.",
            ).exec()
            return
        name = snap.name

        def gather():
            report = svc_iommu_report()
            gpus = [
                d for d in report.devices
                if not d.is_bridge and read_device_ids(d.address).is_display
            ]
            return (
                gpus,
                svc_hook_state(name),
                svc_get_tuning(uuid),
                svc_host_topology(),
            )

        def show(data) -> None:
            gpus, state, tuning, topology = data
            if not gpus:
                ErrorDialog(
                    self, "No graphics card found",
                    "No PCI display device turned up in this host's IOMMU "
                    "groups, so there is nothing to hand over.",
                ).exec()
                return

            def build(address: str, isolate: bool, governor: str):
                return plan_handoff(
                    name, address,
                    tuning=tuning if isolate else None,
                    topology=topology if isolate else None,
                    governor=governor,
                )

            first = plan_handoff(name, gpus[0].address)
            dialog = SingleGpuDialog(
                self, name, gpus, first, state,
                governor_available=governor_available(),
                pinned=bool(tuning.vcpu_pins),
            )

            def preview(address: str, isolate: bool, governor: str) -> None:
                if not address:
                    return
                try:
                    dialog.show_preview(
                        start_script(build(address, isolate, governor))
                    )
                except Exception as e:  # noqa: BLE001 - shown, not raised
                    # a pinning that leaves the host nothing lands here
                    dialog.show_preview(f"# cannot plan this: {e}")

            def install(address: str, isolate: bool, governor: str) -> None:
                try:
                    plan = build(address, isolate, governor)
                except Exception as e:  # noqa: BLE001
                    dialog.status.setText(str(e))
                    return
                if not ConfirmDialog(
                    dialog, "Install the passthrough hooks",
                    f"This writes two scripts under /etc/libvirt/hooks for "
                    f"'{name}' and asks for your password.\n\n"
                    "From then on, starting this machine takes your desktop "
                    "down and gives the card to the guest; stopping it brings "
                    "the desktop back. Try it with a way to reach the host "
                    "that does not need the screen - ssh from another "
                    "machine - the first time.",
                    "Install",
                ).exec() == QDialog.DialogCode.Accepted:
                    return
                dialog.status.setText("writing the hooks…")
                run_task(
                    lambda: svc_install_hooks(plan),
                    done=lambda m: (
                        dialog.status.setText(str(m)),
                        dialog._remove.setVisible(True),
                    ),
                    failed=lambda m: dialog.status.setText(str(m)),
                )

            def remove() -> None:
                run_task(
                    lambda: svc_remove_hooks(name),
                    done=lambda m: (
                        dialog.status.setText(str(m)),
                        dialog._remove.setVisible(False),
                    ),
                    failed=lambda m: dialog.status.setText(str(m)),
                )

            dialog.preview_requested = preview
            dialog.install_requested = install
            dialog.remove_requested = remove
            preview(*dialog.choices())
            dialog.exec()

        self.hw_status.setText("reading this host's graphics devices…")
        run_task(
            gather,
            done=lambda data: (self.hw_status.setText(""), show(data)),
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
        connect_guarded(
            downloader.progress,
            lambda _pct, text: self._virtio_status(f"virtio-win: {text}"),
        )
        connect_guarded(
            downloader.failed,
            lambda m: self._virtio_status(f"virtio-win download failed: {m}"),
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
        shares, cap_pct = _tuning_cpu_limits(dialog)
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
            if (shares, cap_pct) != (before.cpu_shares, before.cpu_cap_pct):
                messages.append(svc_set_cpu_limits(uuid, shares, cap_pct))
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

    def _grow_disk(self) -> None:
        sel = self._selected_device()
        if not sel or sel[0] != "disk" or not self.uuid:
            self.hw_status.setText("select a disk first")
            return
        disk = sel[1]
        uuid = self.uuid

        def show(pools) -> None:
            current = 0.0
            for pool in pools:
                for vol in pool.volumes:
                    if vol.path == disk.source:
                        current = vol.capacity / 1024**3
            dialog = GrowDiskDialog(self, disk.dev, current)
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return
            new_gb = dialog.size.value()
            self.hw_status.setText(f"growing {disk.dev}…")
            run_task(
                lambda: svc_grow_disk(uuid, disk.dev, new_gb),
                done=lambda msg: (self.hw_status.setText(str(msg)),
                                  self._load_hardware()),
                failed=self._hw_failed,
            )

        run_task(svc_list_pools, done=show, failed=self._hw_failed)

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
        self._popup(menu)

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

    def _remove_display(self, gtype: str, ident: str) -> None:
        uuid = self.uuid
        if not uuid:
            return
        if not self._confirm_removal(
            f"{gtype.upper()} display",
            "Displays cannot be unplugged from a running machine, so this "
            "takes effect on its next start.",
        ):
            return
        self._hw_run(lambda: svc_remove_display(uuid, gtype, ident))

    def _remove_video(self) -> None:
        uuid = self.uuid
        if not uuid:
            return
        if not self._confirm_removal(
            "video adapter",
            "A machine that still has a display gets one back from libvirt; "
            "this is for a machine whose graphics are passed through.",
        ):
            return
        self._hw_run(lambda: svc_remove_video(uuid))

    def _popup_pos(self):
        """Where a menu opened from a faceplate button belongs.

        Under that button. It used to open at the centre of the device
        list, which is a different column of the window - 600px away from
        the pointer on a normal-sized window. sender() is the button when
        a signal brought us here; the list is only a fallback for a call
        that did not come from one.
        """
        anchor = self.sender()
        if isinstance(anchor, QPushButton):
            return anchor.mapToGlobal(anchor.rect().bottomLeft())
        return self.hw_tree.mapToGlobal(self.hw_tree.rect().center())

    def _popup(self, menu: QMenu) -> None:
        menu.exec(self._popup_pos())


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
