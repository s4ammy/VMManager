"""Templates page: base images and what has been cloned from them."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from .. import theme
from ..core.osident import display_name
from ..data.oslogos import logo_pixmap
from ..dialogs import (
    ConfirmDialog,
    ErrorDialog,
    SizedDialog,
    _buttons,
    _field_label,
    _title,
)
from ..libvirt_service import (
    BackingIndex,
    DomainSnapshot,
    svc_backing_index,
    svc_linked_clone,
    svc_set_template,
)
from ..tasks import run_task
from ..widgets import fmt_size


class DeployDialog(SizedDialog):
    """How many clones, called what, on which network."""

    def __init__(self, parent, template: str, networks: list[str],
                 default_network: str) -> None:
        super().__init__(parent)
        self.setWindowTitle("Deploy clones")
        self.setMinimumWidth(460)
        box = QVBoxLayout(self)
        box.setContentsMargins(24, 22, 24, 20)
        box.setSpacing(10)
        box.addWidget(_title(f"Clone {template}"))
        note = QLabel(
            "Each clone is a copy-on-write overlay on this template's disk, so "
            "it takes seconds and almost no space. More than one is numbered: "
            "web becomes web-01, web-02."
        )
        note.setWordWrap(True)
        note.setObjectName("ConsoleHint")
        box.addWidget(note)

        box.addWidget(_field_label("name"))
        self.name = QLineEdit()
        self.name.setPlaceholderText(_suggest_prefix(template))
        self.name.setText(_suggest_prefix(template))
        box.addWidget(self.name)

        row = QHBoxLayout()
        row.setSpacing(14)
        count_col = QVBoxLayout()
        count_col.addWidget(_field_label("how many"))
        self.count = QSpinBox()
        self.count.setRange(1, 50)
        self.count.setValue(1)
        count_col.addWidget(self.count)
        net_col = QVBoxLayout()
        net_col.addWidget(_field_label("network"))
        self.network = QComboBox()
        self.network.addItems(networks or [default_network])
        if default_network in networks:
            self.network.setCurrentText(default_network)
        net_col.addWidget(self.network)
        row.addLayout(count_col)
        row.addLayout(net_col, 1)
        box.addLayout(row)
        box.addSpacing(6)
        box.addLayout(_buttons(self, "Deploy"))
        self.name.textChanged.connect(
            lambda t: self._ok_button.setEnabled(bool(t.strip()))
        )

    def names(self) -> list[str]:
        base = self.name.text().strip()
        if self.count.value() == 1:
            return [base]
        return [f"{base}-{i:02d}" for i in range(1, self.count.value() + 1)]


def _suggest_prefix(template: str) -> str:
    """debian-13-base -> debian-13, plain -> plain-clone.

    A name matching the template's own would clash with it, so anything
    without a recognisable suffix gets one added instead of removed.
    """
    for suffix in ("-base", "-template", "-golden", "-tmpl"):
        if template.endswith(suffix) and len(template) > len(suffix):
            return template[: -len(suffix)]
    return f"{template}-clone"


class CloneChip(QFrame):
    """One machine derived from the template, with what it costs."""

    def __init__(self, page: "TemplatesPage", snap: DomainSnapshot,
                 unique_bytes: int) -> None:
        super().__init__()
        self.setProperty("class", "SpecChip")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._uuid = snap.uuid
        self._page = page
        row = QHBoxLayout(self)
        row.setContentsMargins(10, 5, 10, 5)
        row.setSpacing(8)
        dot = QLabel("●")
        dot.setProperty("class", theme.state_class(snap.state))
        row.addWidget(dot)
        name = QLabel(snap.name)
        name.setProperty("class", "StatVal")
        row.addWidget(name)
        if unique_bytes:
            size = QLabel(fmt_size(unique_bytes))
            size.setProperty("class", "Faint")
            row.addWidget(size)
        self.setToolTip(
            f"{snap.name} - {snap.state}"
            + (f", {fmt_size(unique_bytes)} of its own" if unique_bytes else "")
        )

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._page.open_machine.emit(self._uuid)
        super().mousePressEvent(event)


class TemplateCard(QFrame):
    def __init__(self, page: "TemplatesPage", snap: DomainSnapshot,
                 clones: list[tuple[DomainSnapshot, int]], base_bytes: int) -> None:
        super().__init__()
        self.setProperty("class", "ChartCard")
        box = QVBoxLayout(self)
        box.setContentsMargins(18, 15, 18, 15)
        box.setSpacing(10)

        head = QHBoxLayout()
        head.setSpacing(12)
        if snap.os_key and snap.os_key != "unknown":
            icon = QLabel()
            icon.setPixmap(logo_pixmap(snap.os_key, 24))
            icon.setFixedSize(26, 26)
            head.addWidget(icon)
        title = QLabel(snap.name)
        title.setProperty("class", "SectionTitle")
        head.addWidget(title)
        if snap.os_key and snap.os_key != "unknown":
            os_label = QLabel(display_name(snap.os_key))
            os_label.setProperty("class", "StatVal")
            head.addWidget(os_label)
        head.addStretch(1)

        deploy = QPushButton("Deploy…")
        deploy.setProperty("class", "PrimaryButton")
        deploy.setCursor(Qt.CursorShape.PointingHandCursor)
        deploy.clicked.connect(lambda: page.deploy(snap))
        head.addWidget(deploy)
        open_btn = QPushButton("Open")
        open_btn.setProperty("class", "GhostButton")
        open_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        open_btn.clicked.connect(lambda: page.open_machine.emit(snap.uuid))
        head.addWidget(open_btn)
        unmark = QPushButton("Unmark")
        unmark.setProperty("class", "GhostButton")
        unmark.setCursor(Qt.CursorShape.PointingHandCursor)
        unmark.clicked.connect(lambda: page.unmark(snap))
        head.addWidget(unmark)
        box.addLayout(head)

        unique = sum(size for _snap, size in clones)
        summary = [f"{fmt_size(base_bytes)} base image" if base_bytes else "base image"]
        if clones:
            summary.append(
                f"{len(clones)} clone{'s' if len(clones) != 1 else ''}"
                + (f" using {fmt_size(unique)} of their own" if unique else "")
            )
        else:
            summary.append("nothing cloned from it yet")
        if snap.state != "shutoff":
            summary.append("running - shut it down to clone from it")
        meta = QLabel(" · ".join(summary))
        meta.setProperty("class", "StatVal")
        box.addWidget(meta)

        if clones:
            grid = QGridLayout()
            grid.setSpacing(6)
            for i, (clone, size) in enumerate(clones):
                grid.addWidget(CloneChip(page, clone, size), i // 4, i % 4)
            grid.setColumnStretch(4, 1)
            box.addLayout(grid)


class TemplatesPage(QWidget):
    """Base images, what came from them, and how to make more."""

    open_machine = Signal(str)  # uuid
    changed = Signal()  # something was created or unmarked; re-poll

    def __init__(self) -> None:
        super().__init__()
        self._domains: list[DomainSnapshot] = []
        self._networks: list[str] = []
        self._index: BackingIndex | None = None
        self._fingerprint: object = None

        content = QVBoxLayout(self)
        content.setContentsMargins(36, 30, 36, 0)
        content.setSpacing(0)

        head = QHBoxLayout()
        head.setSpacing(10)
        title_box = QVBoxLayout()
        title_box.setSpacing(0)
        title = QLabel("Templates")
        title.setObjectName("PageTitle")
        self.subtitle = QLabel("base images to clone from")
        self.subtitle.setObjectName("PageSub")
        title_box.addWidget(title)
        title_box.addWidget(self.subtitle)
        head.addLayout(title_box)
        head.addStretch(1)
        self.mark_btn = QPushButton("Mark a machine…")
        self.mark_btn.setProperty("class", "PrimaryButton")
        self.mark_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.mark_btn.clicked.connect(self._mark_machine)
        head.addWidget(self.mark_btn, alignment=Qt.AlignmentFlag.AlignTop)
        content.addLayout(head)
        content.addSpacing(20)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        inner = QWidget()
        self.card_list = QVBoxLayout(inner)
        self.card_list.setContentsMargins(0, 0, 6, 30)
        self.card_list.setSpacing(14)
        self.empty = QLabel(
            "No templates yet.\n\n"
            "Any shut-off machine can become one. Set a machine up the way you "
            "want it - packages, users, keys - shut it down, then mark it. "
            "Clones of it are copy-on-write overlays: they take seconds to "
            "make and almost no disk until they diverge."
        )
        self.empty.setWordWrap(True)
        self.empty.setObjectName("ConsoleHint")
        self.card_list.addWidget(self.empty)
        self.card_list.addStretch(1)
        scroll.setWidget(inner)
        content.addWidget(scroll, 1)

    # -- data

    def set_domains(self, domains: list[DomainSnapshot], networks: list[str]) -> None:
        self._domains = domains
        self._networks = networks
        templates = [d for d in domains if d.is_template]
        # Walking every volume is not something to do on a timer, so only when
        # the set of machines actually changes.
        fingerprint = (
            frozenset(d.uuid for d in templates),
            frozenset(d.uuid for d in domains),
        )
        stale = fingerprint != self._fingerprint
        self._fingerprint = fingerprint
        # draw with what we have, so the page never sits empty while the volume
        # chain is being read
        self.refresh()
        if templates and stale:
            run_task(
                svc_backing_index,
                done=lambda index: (setattr(self, "_index", index), self.refresh()),
                failed=lambda _m: None,
            )

    def refresh(self) -> None:
        while self.card_list.count() > 2:  # keep the empty-state label and stretch
            item = self.card_list.takeAt(1)
            if item.widget():
                item.widget().deleteLater()

        templates = [d for d in self._domains if d.is_template]
        self.empty.setVisible(not templates)
        self.subtitle.setText(
            f"{len(templates)} template{'s' if len(templates) != 1 else ''}"
            if templates else "base images to clone from"
        )
        for i, template in enumerate(templates):
            clones = self._clones_of(template)
            base = sum(
                self._index.capacity_of.get(p, 0) for p in template.disk_paths
            ) if self._index else 0
            self.card_list.insertWidget(
                i + 1, TemplateCard(self, template, clones, base)
            )

    def _clones_of(self, template: DomainSnapshot) -> list[tuple[DomainSnapshot, int]]:
        """Machines whose disks are layered on this template's, and their size."""
        if self._index is None:
            return []
        overlays = set(self._index.clones_of(template.disk_paths))
        found = []
        for snap in self._domains:
            if snap.uuid == template.uuid:
                continue
            own = [p for p in snap.disk_paths if p in overlays]
            if own:
                found.append(
                    (snap, sum(self._index.allocation_of.get(p, 0) for p in own))
                )
        return sorted(found, key=lambda pair: pair[0].name.lower())

    # -- actions

    def deploy(self, template: DomainSnapshot) -> None:
        if template.state != "shutoff":
            ErrorDialog(
                self, "Cannot clone a running machine",
                f"Shut {template.name} down first. A clone copies its disk "
                "state, which is only consistent while it is off.",
            ).exec()
            return
        dialog = DeployDialog(
            self, template.name, self._networks,
            template.networks[0] if template.networks else "default",
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        names = dialog.names()
        taken = {d.name for d in self._domains}
        clash = [n for n in names if n in taken]
        if clash:
            ErrorDialog(
                self, "Name already used",
                f"{', '.join(clash)} already exist. Pick another name.",
            ).exec()
            return
        network = dialog.network.currentText()
        uuid = template.uuid
        self.subtitle.setText(f"cloning {len(names)}…")

        def work():
            for name in names:
                svc_linked_clone(uuid, name, network)
            return f"{len(names)} clone{'s' if len(names) != 1 else ''} of {template.name}"

        run_task(
            work,
            done=lambda msg: (self.subtitle.setText(msg), self._invalidate()),
            failed=lambda m: (
                self.subtitle.setText(""),
                ErrorDialog(self, "Clone failed", m).exec(),
                self._invalidate(),
            ),
        )

    def unmark(self, template: DomainSnapshot) -> None:
        clones = self._clones_of(template)
        note = f"{template.name} goes back to being an ordinary machine."
        if clones:
            note += (
                f"\n\n{len(clones)} machine(s) still depend on its disk and keep "
                "working, but deleting it would break them."
            )
        if ConfirmDialog(self, "Unmark template", note, "Unmark").exec() != (
            QDialog.DialogCode.Accepted
        ):
            return
        run_task(
            lambda: svc_set_template(template.uuid, False),
            done=lambda _: self._invalidate(),
            failed=lambda m: ErrorDialog(self, "Could not unmark", m).exec(),
        )

    def _mark_machine(self) -> None:
        from ..dialogs import ChoiceDialog

        options = sorted(
            d.name for d in self._domains
            if not d.is_template and d.state == "shutoff"
        )
        if not options:
            ErrorDialog(
                self, "Nothing to mark",
                "A template has to be shut off. Shut a machine down first, or "
                "create one to use as a base.",
            ).exec()
            return
        dialog = ChoiceDialog(
            self, "Mark as template", "machine", options,
            note="Templates cannot be started. Clone them instead, which "
                 "leaves the base image untouched.",
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        chosen = next(
            (d for d in self._domains if d.name == dialog.combo.currentText()), None
        )
        if chosen is None:
            return
        run_task(
            lambda: svc_set_template(chosen.uuid, True),
            done=lambda _: self._invalidate(),
            failed=lambda m: ErrorDialog(self, "Could not mark", m).exec(),
        )

    def _invalidate(self) -> None:
        """Force a re-read of the volume chain, then a fresh poll."""
        self._fingerprint = None
        self.changed.emit()
