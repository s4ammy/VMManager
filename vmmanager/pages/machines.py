"""Machines page: rack-bay list of every domain with live sparklines."""

from __future__ import annotations

from PySide6.QtCore import QSettings, Qt, QTimer, Signal
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ..libvirt_service import (
    DomainSnapshot,
    HostSnapshot,
    svc_backing_index,
    svc_guest_fs_health,
    svc_screenshot,
)
from ..tasks import run_task
from ..widgets import VmCard


def thumbnails_enabled() -> bool:
    return QSettings("vmmanager", "vmmanager").value("thumbnails", "false") in (
        "true", True,
    )


class MachinesPage(QWidget):
    action = Signal(str, str)  # uuid, op
    open_detail = Signal(str)
    context = Signal(str, object)
    new_vm = Signal()
    restore_file = Signal()
    import_backup = Signal()
    restore_backup = Signal()
    bulk_action = Signal(list, str)  # uuids, op
    health_updated = Signal(str, str, float)  # uuid, mountpoint, used %

    def __init__(self) -> None:
        super().__init__()
        content = QVBoxLayout(self)
        content.setContentsMargins(36, 30, 36, 0)
        content.setSpacing(0)

        head = QHBoxLayout()
        head.setSpacing(10)  # inherits 0 from `content` otherwise
        title_box = QVBoxLayout()
        title_box.setSpacing(0)
        title = QLabel("Machines")
        title.setObjectName("PageTitle")
        self.subtitle = QLabel("Connecting to libvirt…")
        self.subtitle.setObjectName("PageSub")
        title_box.addWidget(title)
        title_box.addWidget(self.subtitle)
        head.addLayout(title_box)
        head.addStretch(1)
        import_btn = QPushButton("Import ▾")
        import_btn.setProperty("class", "GhostButton")
        import_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        import_btn.clicked.connect(lambda: self._import_menu(import_btn))
        head.addWidget(import_btn, alignment=Qt.AlignmentFlag.AlignTop)
        new_btn = QPushButton("+ New machine")
        new_btn.setProperty("class", "PrimaryButton")
        new_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        new_btn.clicked.connect(self.new_vm.emit)
        head.addWidget(new_btn, alignment=Qt.AlignmentFlag.AlignTop)
        content.addLayout(head)
        content.addSpacing(20)

        self.error_banner = QLabel()
        self.error_banner.setObjectName("ErrorBanner")
        self.error_banner.setWordWrap(True)
        self.error_banner.hide()
        self._banner_text = ("", "")
        content.addWidget(self.error_banner)

        # tag filter + bulk-action bar
        filter_row = QHBoxLayout()
        filter_row.setSpacing(10)
        self.tag_filter = QComboBox()
        self.tag_filter.addItem("all machines")
        self.tag_filter.currentIndexChanged.connect(lambda _i: self._apply_filter())
        filter_row.addWidget(self.tag_filter)
        self.selection_hint = QLabel("ctrl+click cards to select several")
        self.selection_hint.setObjectName("ConsoleHint")
        filter_row.addWidget(self.selection_hint)
        filter_row.addStretch(1)
        content.addLayout(filter_row)
        content.addSpacing(8)

        self.bulk_bar = QFrame()
        self.bulk_bar.setObjectName("HostPanel")
        bulk_box = QHBoxLayout(self.bulk_bar)
        bulk_box.setContentsMargins(14, 8, 14, 8)
        bulk_box.setSpacing(8)
        self.bulk_label = QLabel("")
        self.bulk_label.setProperty("class", "StatVal")
        bulk_box.addWidget(self.bulk_label)
        bulk_box.addStretch(1)
        for label, op in (
            ("Start", "start"), ("Shut down", "shutdown"),
            ("Force off", "force-off"), ("Snapshot", "snapshot"),
            ("Tag…", "tag"),
        ):
            btn = QPushButton(label)
            btn.setProperty("class", "GhostButton")
            btn.clicked.connect(lambda _=False, o=op: self._emit_bulk(o))
            bulk_box.addWidget(btn)
        clear = QPushButton("Clear")
        clear.setProperty("class", "GhostButton")
        clear.clicked.connect(self.clear_selection)
        bulk_box.addWidget(clear)
        self.bulk_bar.hide()
        content.addWidget(self.bulk_bar)
        content.addSpacing(8)

        self.empty = QFrame()
        self.empty.setObjectName("EmptyState")
        empty_box = QVBoxLayout(self.empty)
        empty_box.setContentsMargins(24, 42, 24, 42)
        empty_title = QLabel("No machines defined")
        empty_title.setObjectName("EmptyTitle")
        empty_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_body = QLabel("Create one with the button above to get started.")
        empty_body.setObjectName("EmptyBody")
        empty_body.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_box.addWidget(empty_title)
        empty_box.addWidget(empty_body)
        self.empty.hide()
        content.addWidget(self.empty)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll_host = QWidget()
        self.card_list = QVBoxLayout(scroll_host)
        self.card_list.setContentsMargins(0, 0, 6, 30)
        self.card_list.setSpacing(12)
        self.card_list.addStretch(1)
        scroll.setWidget(scroll_host)
        content.addWidget(scroll, 1)

        self._cards: dict[str, VmCard] = {}
        self._domains: list[DomainSnapshot] = []
        self._selected: set[str] = set()

        self._thumb_timer = QTimer(self)
        self._thumb_timer.setInterval(5000)
        self._thumb_timer.timeout.connect(self._refresh_thumbnails)
        self._thumb_timer.start()
        self._health_timer = QTimer(self)
        self._health_timer.setInterval(30000)
        self._health_timer.timeout.connect(self._refresh_health)
        self._health_timer.start()

    def update_from(self, domains: list[DomainSnapshot], host: HostSnapshot) -> None:
        self.error_banner.hide()
        self._domains = domains
        self.subtitle.setText(f"{host.running} running · {host.total} defined")
        self.empty.setVisible(not domains)

        seen = set()
        for i, snap in enumerate(domains):
            seen.add(snap.uuid)
            card = self._cards.get(snap.uuid)
            if card is None:
                card = VmCard(snap)
                card.action.connect(self.action.emit)
                card.open_detail.connect(self.open_detail.emit)
                card.context.connect(self.context.emit)
                card.toggle_select.connect(self._toggle_select)
                self._cards[snap.uuid] = card
                self.card_list.insertWidget(i, card)
            else:
                card.update_from(snap)
        for uuid in list(self._cards):
            if uuid not in seen:
                card = self._cards.pop(uuid)
                self._selected.discard(uuid)
                self.card_list.removeWidget(card)
                card.deleteLater()
        self._refresh_tag_filter(domains)
        self._refresh_template_use(domains)
        self._apply_filter()
        self._update_bulk_bar()

    # -- tags & filtering

    def _refresh_template_use(self, domains: list[DomainSnapshot]) -> None:
        """Show each template's clone count, re-reading volumes only when needed.

        Walking every volume is not something to do on a timer, so it happens
        when the set of machines or templates actually changes.
        """
        templates = {d.uuid for d in domains if d.is_template}
        fingerprint = (frozenset(templates), frozenset(d.uuid for d in domains))
        if not templates:
            self._template_fingerprint = fingerprint
            return
        if fingerprint == getattr(self, "_template_fingerprint", None):
            self._paint_template_use(domains)
            return
        self._template_fingerprint = fingerprint

        def apply(index) -> None:
            self._index = index
            self._paint_template_use(self._domains)

        run_task(svc_backing_index, done=apply, failed=lambda _m: None)

    def _paint_template_use(self, domains: list[DomainSnapshot]) -> None:
        index = getattr(self, "_index", None)
        if index is None:
            return
        backing_of, capacity_of = index.backing_of, index.capacity_of
        for template in (d for d in domains if d.is_template):
            card = self._cards.get(template.uuid)
            if card is None:
                continue
            own = set(template.disk_paths)
            clones = sum(
                1 for d in domains
                if d.uuid != template.uuid
                and any(backing_of.get(p) in own for p in d.disk_paths)
            )
            shared = sum(capacity_of.get(p, 0) for p in own)
            card.set_template_use(clones, shared)

    def set_modes(self, active: dict) -> None:
        """uuid -> mode name, for the chip on each card."""
        for uuid, card in self._cards.items():
            card.set_mode(active.get(uuid, ""))

    def refresh_cards(self) -> None:
        """Re-apply the current snapshots - used when OS logos arrive."""
        for snap in self._domains:
            card = self._cards.get(snap.uuid)
            if card is not None:
                card.update_from(snap)

    def _refresh_tag_filter(self, domains: list[DomainSnapshot]) -> None:
        tags = sorted({t for d in domains for t in d.tags})
        current = self.tag_filter.currentText()
        wanted = ["all machines"]
        if any(d.is_template for d in domains):
            wanted += ["templates", "machines only"]
        wanted += [f"#{t}" for t in tags]
        existing = [self.tag_filter.itemText(i) for i in range(self.tag_filter.count())]
        if wanted != existing:
            self.tag_filter.blockSignals(True)
            self.tag_filter.clear()
            self.tag_filter.addItems(wanted)
            if current in wanted:
                self.tag_filter.setCurrentText(current)
            self.tag_filter.blockSignals(False)

    def _apply_filter(self) -> None:
        selected = self.tag_filter.currentText()
        for d in self._domains:
            card = self._cards.get(d.uuid)
            if card is None:
                continue
            if selected == "templates":
                card.setVisible(d.is_template)
            elif selected == "machines only":
                card.setVisible(not d.is_template)
            elif selected.startswith("#"):
                card.setVisible(selected[1:] in d.tags)
            else:
                card.setVisible(True)

    # -- selection & bulk actions

    def _toggle_select(self, uuid: str) -> None:
        if uuid in self._selected:
            self._selected.discard(uuid)
        else:
            self._selected.add(uuid)
        card = self._cards.get(uuid)
        if card is not None:
            card.set_selected(uuid in self._selected)
        self._update_bulk_bar()

    def clear_selection(self) -> None:
        for uuid in list(self._selected):
            card = self._cards.get(uuid)
            if card is not None:
                card.set_selected(False)
        self._selected.clear()
        self._update_bulk_bar()

    def _update_bulk_bar(self) -> None:
        n = len(self._selected)
        self.bulk_bar.setVisible(n > 0)
        names = [d.name for d in self._domains if d.uuid in self._selected]
        self.bulk_label.setText(f"{n} selected · " + ", ".join(names[:4]) + ("…" if n > 4 else ""))

    def _emit_bulk(self, op: str) -> None:
        if self._selected:
            self.bulk_action.emit(sorted(self._selected), op)

    # -- live thumbnails & guest health

    def _refresh_thumbnails(self) -> None:
        if not self.isVisible() or not thumbnails_enabled():
            for card in self._cards.values():
                card.set_thumbnail(None)
            return
        for d in self._domains:
            if d.state != "running":
                continue
            uuid = d.uuid

            def apply(data: bytes, u=uuid) -> None:
                card = self._cards.get(u)
                if card is None:
                    return
                image = QImage.fromData(data)
                if not image.isNull():
                    card.set_thumbnail(
                        QPixmap.fromImage(
                            image.scaled(208, 116, Qt.AspectRatioMode.KeepAspectRatio,
                                         Qt.TransformationMode.SmoothTransformation)
                        )
                    )

            run_task(lambda u=uuid: svc_screenshot(u), done=apply,
                     failed=lambda _m: None)

    def _refresh_health(self) -> None:
        if not self.isVisible():
            return
        for d in self._domains:
            if d.state != "running":
                continue
            uuid = d.uuid

            def apply(fs_list, u=uuid) -> None:
                card = self._cards.get(u)
                if card is None or not fs_list:
                    return
                worst = fs_list[0]
                card.set_health(worst)
                if worst[1] >= 90:
                    self.health_updated.emit(u, worst[0], worst[1])

            run_task(lambda u=uuid: svc_guest_fs_health(u), done=apply,
                     failed=lambda _m: None)

    def _import_menu(self, anchor: QPushButton) -> None:
        from PySide6.QtWidgets import QMenu

        menu = QMenu(self)
        menu.addAction("Import backup folder…", self.import_backup.emit)
        menu.addAction("Restore incremental backup…", self.restore_backup.emit)
        menu.addAction("Restore state from .vmstate file…", self.restore_file.emit)
        menu.exec(anchor.mapToGlobal(anchor.rect().bottomLeft()))

    def show_error(self, message: str) -> None:
        self.subtitle.setText("Disconnected")
        self._banner(
            "Can't reach libvirt.",
            f"{message} - check that libvirtd is running; "
            "reconnecting automatically.",
        )

    def show_action_error(self, message: str) -> None:
        self._banner("Action failed.", message)

    def _banner(self, headline: str, detail: str) -> None:
        """The headline is coloured in markup, so remember what it said.

        Rich text cannot carry a stylesheet class, which means the colour is
        written into the text and a theme change has to write it again.
        """
        self._banner_text = (headline, detail)
        self.error_banner.setText(
            f"<b style='color:{theme.DANGER}'>{headline}</b> {detail}"
        )
        self.error_banner.show()

    def restyle(self) -> None:
        if self.error_banner.isVisible():
            self._banner(*self._banner_text)
