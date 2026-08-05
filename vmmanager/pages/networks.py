"""Networks page: virtual networks with state controls and DHCP leases."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .. import theme
from ..dialogs import ConfirmDialog, ErrorDialog, NetworkDialog
from ..libvirt_service import (
    NetworkInfo,
    svc_create_network,
    svc_delete_network,
    svc_get_network_def,
    svc_list_networks,
    svc_network_action,
    svc_redefine_network,
)
from ..tasks import run_task


class NetworkCard(QFrame):
    def __init__(self, page: "NetworksPage", net: NetworkInfo) -> None:
        super().__init__()
        self.setProperty("class", "ChartCard")
        box = QVBoxLayout(self)
        box.setContentsMargins(18, 15, 18, 15)
        box.setSpacing(8)

        head = QHBoxLayout()
        head.setSpacing(12)
        name = QLabel(net.name)
        name.setProperty("class", "SectionTitle")
        state = QLabel("ACTIVE" if net.active else "STOPPED")
        state.setObjectName("VmState")
        state.setStyleSheet(
            f"color: {theme.OK if net.active else theme.TEXT_FAINT};"
        )
        meta = QLabel(
            f"bridge {net.bridge} · {net.mode} · "
            f"autostart {'on' if net.autostart else 'off'}"
        )
        meta.setProperty("class", "StatVal")
        head.addWidget(name)
        head.addWidget(state)
        head.addWidget(meta)
        head.addStretch(1)

        if net.active:
            toggle = QPushButton("Stop")
            toggle.setProperty("class", "GhostButton")
            toggle.clicked.connect(lambda: page.net_action(net.name, "stop"))
        else:
            toggle = QPushButton("Start")
            toggle.setProperty("class", "PrimaryButton")
            toggle.clicked.connect(lambda: page.net_action(net.name, "start"))
        auto = QPushButton(
            "Disable autostart" if net.autostart else "Enable autostart"
        )
        auto.setProperty("class", "GhostButton")
        auto.clicked.connect(
            lambda: page.net_action(
                net.name, "autostart-off" if net.autostart else "autostart-on"
            )
        )
        edit = QPushButton("Edit…")
        edit.setProperty("class", "GhostButton")
        edit.clicked.connect(lambda: page.edit_network(net.name))
        delete = QPushButton("Delete…")
        delete.setProperty("class", "GhostButton")
        delete.clicked.connect(lambda: page.delete_network(net.name))
        head.addWidget(toggle)
        head.addWidget(auto)
        head.addWidget(edit)
        head.addWidget(delete)
        box.addLayout(head)

        if net.leases:
            table = QTableWidget(len(net.leases), 3)
            table.setHorizontalHeaderLabels(["IP address", "MAC", "Hostname"])
            table.horizontalHeader().setStretchLastSection(True)
            table.verticalHeader().hide()
            table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
            table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
            table.setShowGrid(False)
            for r, lease in enumerate(net.leases):
                table.setItem(r, 0, QTableWidgetItem(lease.ip))
                table.setItem(r, 1, QTableWidgetItem(lease.mac))
                table.setItem(r, 2, QTableWidgetItem(lease.hostname))
            for c in range(table.columnCount() - 1):
                table.resizeColumnToContents(c)
                table.setColumnWidth(c, table.columnWidth(c) + 24)
            table.setMinimumHeight(min(80 + 30 * len(net.leases), 240))
            box.addWidget(table)
        elif net.active:
            hint = QLabel("No DHCP leases right now.")
            hint.setObjectName("ConsoleHint")
            box.addWidget(hint)


class NetworksPage(QWidget):
    def __init__(self) -> None:
        super().__init__()
        content = QVBoxLayout(self)
        content.setContentsMargins(36, 30, 36, 0)
        content.setSpacing(0)

        head = QHBoxLayout()
        head.setSpacing(10)  # inherits 0 from `content` otherwise
        title_box = QVBoxLayout()
        title_box.setSpacing(0)
        title = QLabel("Networks")
        title.setObjectName("PageTitle")
        self.subtitle = QLabel("")
        self.subtitle.setObjectName("PageSub")
        title_box.addWidget(title)
        title_box.addWidget(self.subtitle)
        head.addLayout(title_box)
        head.addStretch(1)
        self.map_btn = QPushButton("Map")
        self.map_btn.setProperty("class", "GhostButton")
        self.map_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.map_btn.clicked.connect(self._toggle_map)
        head.addWidget(self.map_btn, alignment=Qt.AlignmentFlag.AlignTop)
        filters_btn = QPushButton("Filters…")
        filters_btn.setProperty("class", "GhostButton")
        filters_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        filters_btn.clicked.connect(self._open_filters)
        head.addWidget(filters_btn, alignment=Qt.AlignmentFlag.AlignTop)
        new_btn = QPushButton("+ New network")
        new_btn.setProperty("class", "PrimaryButton")
        new_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        new_btn.clicked.connect(self._new_network)
        head.addWidget(new_btn, alignment=Qt.AlignmentFlag.AlignTop)
        content.addLayout(head)
        content.addSpacing(20)

        from PySide6.QtWidgets import QStackedWidget

        from ..topology import TopologyView

        self.view_stack = QStackedWidget()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        inner = QWidget()
        self.net_list = QVBoxLayout(inner)
        self.net_list.setContentsMargins(0, 0, 6, 30)
        self.net_list.setSpacing(14)
        self.net_list.addStretch(1)
        scroll.setWidget(inner)
        self.view_stack.addWidget(scroll)
        self.topology = TopologyView()
        self.open_detail = self.topology.open_detail  # re-exported signal
        self.view_stack.addWidget(self.topology)
        content.addWidget(self.view_stack, 1)
        self._domains = []
        self._nets: list[NetworkInfo] = []

    def set_domains(self, domains) -> None:
        self._domains = domains
        if self.view_stack.currentWidget() is self.topology:
            self.topology.set_data(self._nets, domains)

    def _toggle_map(self) -> None:
        if self.view_stack.currentWidget() is self.topology:
            self.view_stack.setCurrentIndex(0)
            self.map_btn.setText("Map")
        else:
            self.topology.set_data(self._nets, self._domains)
            self.view_stack.setCurrentWidget(self.topology)
            self.map_btn.setText("List")

    def refresh(self) -> None:
        run_task(
            svc_list_networks,
            done=self._apply,
            failed=lambda m: ErrorDialog(self, "libvirt error", m).exec(),
        )

    def _apply(self, nets: list[NetworkInfo]) -> None:
        self._nets = nets
        while self.net_list.count() > 1:
            item = self.net_list.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        for i, net in enumerate(nets):
            self.net_list.insertWidget(i, NetworkCard(self, net))
        active = sum(1 for n in nets if n.active)
        self.subtitle.setText(f"{active} active · {len(nets)} defined")
        self.topology.set_data(nets, self._domains)

    def net_action(self, name: str, op: str) -> None:
        run_task(
            lambda: svc_network_action(name, op),
            done=lambda _: self.refresh(),
            failed=lambda m: ErrorDialog(self, "Network action failed", m).exec(),
        )

    def _open_filters(self) -> None:
        from ..dialogs import NwFiltersDialog

        NwFiltersDialog(self).exec()

    def _new_network(self) -> None:
        from ..core.networks import svc_create_network_ex
        from ..dialogs import NetworkDetailsDialog

        dialog = NetworkDetailsDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        spec = dialog.spec()
        run_task(
            lambda: svc_create_network_ex(spec),
            done=lambda _: self.refresh(),
            failed=lambda m: ErrorDialog(self, "Create network failed", m).exec(),
        )

    def edit_network(self, name: str) -> None:
        from ..core.networks import svc_get_network_spec, svc_redefine_network_ex
        from ..dialogs import NetworkDetailsDialog

        def show(spec) -> None:
            dialog = NetworkDetailsDialog(self, existing=spec)
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return
            updated = dialog.spec()
            run_task(
                lambda: svc_redefine_network_ex(name, updated),
                done=lambda _: self.refresh(),
                failed=lambda m: ErrorDialog(self, "Edit network failed", m).exec(),
            )

        run_task(
            lambda: svc_get_network_spec(name),
            done=show,
            failed=lambda m: ErrorDialog(self, "libvirt error", m).exec(),
        )

    def delete_network(self, name: str) -> None:
        confirm = ConfirmDialog(
            self,
            "Delete network",
            f"Stop and delete network '{name}'? Machines configured to use "
            "it won't start until they're reassigned.",
            "Delete network",
        )
        if confirm.exec() != QDialog.DialogCode.Accepted:
            return
        run_task(
            lambda: svc_delete_network(name),
            done=lambda _: self.refresh(),
            failed=lambda m: ErrorDialog(self, "Delete failed", m).exec(),
        )
