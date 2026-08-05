"""Stacks page: labs of linked clones deployed and torn down as a unit."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFrame,
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
from ..dialogs import (
    ConfirmDialog,
    ErrorDialog,
    SizedDialog,
    _buttons,
    _field_label,
    _title,
)
from ..libvirt_service import (
    DomainSnapshot,
    svc_deploy_stack,
    svc_teardown_stack,
)
from ..tasks import run_task


class NewStackDialog(SizedDialog):
    def __init__(self, parent, templates: list[str]) -> None:
        super().__init__(parent)
        self.setWindowTitle("New stack")
        self.setMinimumWidth(460)
        box = QVBoxLayout(self)
        box.setContentsMargins(24, 22, 24, 20)
        box.setSpacing(10)
        box.addWidget(_title("New stack"))
        note = QLabel(
            "A stack is N linked clones of a template, deployed and torn "
            "down together - clones are named <stack>-1 … <stack>-N."
        )
        note.setWordWrap(True)
        note.setProperty("class", "Dim")
        box.addWidget(note)
        box.addWidget(_field_label("stack name"))
        self.name = QLineEdit()
        self.name.setPlaceholderText("lab")
        box.addWidget(self.name)
        box.addWidget(_field_label("template"))
        self.template = QComboBox()
        self.template.addItems(templates or ["(no templates, mark one first)"])
        self.template.setEnabled(bool(templates))
        box.addWidget(self.template)
        row = QHBoxLayout()
        count_col = QVBoxLayout()
        count_col.addWidget(_field_label("machines"))
        self.count = QSpinBox()
        self.count.setRange(1, 50)
        self.count.setValue(3)
        count_col.addWidget(self.count)
        net_col = QVBoxLayout()
        net_col.addWidget(_field_label("network"))
        self.network = QComboBox()
        self.network.addItems(
            ["new-isolated, private lab network", "default, the shared NAT network"]
        )
        net_col.addWidget(self.network)
        row.addLayout(count_col)
        row.addLayout(net_col, 1)
        box.addLayout(row)
        box.addSpacing(6)
        box.addLayout(_buttons(self, "Save stack"))
        self._ok_button.setEnabled(False)
        self.name.textChanged.connect(
            lambda t: self._ok_button.setEnabled(bool(t.strip()) and bool(templates))
        )

    def network_name(self) -> str:
        return "new-isolated" if self.network.currentIndex() == 0 else "default"


class StackCard(QFrame):
    def __init__(self, page: "StacksPage", name: str, template: str,
                 count: int, network: str, deployed: int, running: int) -> None:
        super().__init__()
        self.setProperty("class", "ChartCard")
        box = QVBoxLayout(self)
        box.setContentsMargins(18, 15, 18, 15)
        box.setSpacing(8)
        head = QHBoxLayout()
        head.setSpacing(12)
        title = QLabel(name)
        title.setProperty("class", "SectionTitle")
        state = QLabel(
            f"{running}/{deployed} RUNNING" if deployed else "NOT DEPLOYED"
        )
        state.setObjectName("VmState")
        state.setStyleSheet(
            f"color: {theme.OK if running else theme.TEXT_FAINT};"
        )
        meta = QLabel(f"{count} × {template} · net {network}")
        meta.setProperty("class", "StatVal")
        head.addWidget(title)
        head.addWidget(state)
        head.addWidget(meta)
        head.addStretch(1)
        if deployed:
            teardown = QPushButton("Tear down")
            teardown.setProperty("class", "GhostButton")
            teardown.clicked.connect(lambda: page.teardown(name))
            head.addWidget(teardown)
        else:
            deploy = QPushButton("Deploy")
            deploy.setProperty("class", "PrimaryButton")
            deploy.clicked.connect(lambda: page.deploy(name))
            head.addWidget(deploy)
        forget = QPushButton("Delete…")
        forget.setProperty("class", "GhostButton")
        forget.clicked.connect(lambda: page.delete_stack(name))
        head.addWidget(forget)
        box.addLayout(head)


class StacksPage(QWidget):
    def __init__(self, store) -> None:  # store: StatsStore
        super().__init__()
        self._store = store
        self._domains: list[DomainSnapshot] = []
        content = QVBoxLayout(self)
        content.setContentsMargins(36, 30, 36, 0)
        content.setSpacing(0)

        head = QHBoxLayout()
        head.setSpacing(10)  # inherits 0 from `content` otherwise
        title_box = QVBoxLayout()
        title_box.setSpacing(0)
        title = QLabel("Stacks")
        title.setObjectName("PageTitle")
        self.subtitle = QLabel("labs of linked clones, deployed as a unit")
        self.subtitle.setObjectName("PageSub")
        title_box.addWidget(title)
        title_box.addWidget(self.subtitle)
        head.addLayout(title_box)
        head.addStretch(1)
        new_btn = QPushButton("+ New stack")
        new_btn.setProperty("class", "PrimaryButton")
        new_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        new_btn.clicked.connect(self._new_stack)
        head.addWidget(new_btn, alignment=Qt.AlignmentFlag.AlignTop)
        content.addLayout(head)
        content.addSpacing(20)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        inner = QWidget()
        self.card_list = QVBoxLayout(inner)
        self.card_list.setContentsMargins(0, 0, 6, 30)
        self.card_list.setSpacing(14)
        self.card_list.addStretch(1)
        scroll.setWidget(inner)
        content.addWidget(scroll, 1)

    def set_domains(self, domains: list[DomainSnapshot]) -> None:
        self._domains = domains

    def refresh(self) -> None:
        while self.card_list.count() > 1:
            item = self.card_list.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        stacks = self._store.stacks()
        for i, (name, template, count, network) in enumerate(stacks):
            members = [
                d for d in self._domains
                if d.name.startswith(f"{name}-")
                and d.name[len(name) + 1 :].isdigit()
            ]
            running = sum(1 for d in members if d.state == "running")
            self.card_list.insertWidget(
                i, StackCard(self, name, template, count, network,
                             len(members), running)
            )
        self.subtitle.setText(f"{len(stacks)} defined")

    def _templates(self) -> list[str]:
        return [d.name for d in self._domains if d.is_template]

    def _new_stack(self) -> None:
        dialog = NewStackDialog(self, self._templates())
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self._store.save_stack(
            dialog.name.text().strip(),
            dialog.template.currentText(),
            dialog.count.value(),
            dialog.network_name(),
        )
        self.refresh()

    def deploy(self, name: str) -> None:
        spec = next((s for s in self._store.stacks() if s[0] == name), None)
        if spec is None:
            return
        _name, template, count, network = spec
        tmpl = next((d for d in self._domains if d.name == template), None)
        if tmpl is None:
            ErrorDialog(self, "Deploy failed", f"Template '{template}' not found.").exec()
            return
        if tmpl.state != "shutoff":
            ErrorDialog(self, "Deploy failed", "The template must be shut off.").exec()
            return
        self.subtitle.setText(f"deploying {name}…")
        run_task(
            lambda: svc_deploy_stack(name, tmpl.uuid, count, network),
            done=lambda msg: (self.subtitle.setText(msg), self.refresh()),
            failed=lambda m: (
                self.subtitle.setText(""),
                ErrorDialog(self, "Deploy failed", m).exec(),
            ),
        )

    def teardown(self, name: str) -> None:
        confirm = ConfirmDialog(
            self, "Tear down stack",
            f"Force off and delete every machine in '{name}' (including "
            "their overlay disks)?", "Tear down",
        )
        if confirm.exec() != QDialog.DialogCode.Accepted:
            return
        self.subtitle.setText(f"tearing down {name}…")
        run_task(
            lambda: svc_teardown_stack(name),
            done=lambda msg: (self.subtitle.setText(msg), self.refresh()),
            failed=lambda m: ErrorDialog(self, "Teardown failed", m).exec(),
        )

    def delete_stack(self, name: str) -> None:
        members = [
            d for d in self._domains
            if d.name.startswith(f"{name}-") and d.name[len(name) + 1 :].isdigit()
        ]
        if members:
            ErrorDialog(
                self, "Stack is deployed", "Tear it down before deleting the definition."
            ).exec()
            return
        self._store.delete_stack(name)
        self.refresh()
