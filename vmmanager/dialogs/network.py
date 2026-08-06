"""Virtual network dialog."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QPushButton,
    QRadioButton,
    QSpinBox,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from .base import SizedDialog, _buttons, _field_label, _title


class NetworkDialog(SizedDialog):
    """Create or edit a virtual network."""

    def __init__(self, parent, existing=None) -> None:  # existing: NetworkDef | None
        super().__init__(parent)
        editing = existing is not None
        self.setWindowTitle("Edit network" if editing else "New network")
        self.setMinimumWidth(460)
        box = QVBoxLayout(self)
        box.setContentsMargins(24, 22, 24, 20)
        box.setSpacing(10)
        box.addWidget(_title("Edit network" if editing else "New virtual network"))
        if editing:
            note = QLabel(
                "Saving restarts the network. Running machines keep their "
                "interface but may need a DHCP refresh."
            )
            note.setWordWrap(True)
            note.setProperty("class", "Dim")
            box.addWidget(note)
        box.addWidget(_field_label("name"))
        self.name = QLineEdit(existing.name if editing else "")
        self.name.setPlaceholderText("lab")
        box.addWidget(self.name)
        box.addWidget(_field_label("mode"))
        self.mode = QComboBox()
        self.mode.addItems(
            ["nat, guests reach out, host routes", "isolated, guests only",
             "bridge - join an existing host bridge"]
        )
        box.addWidget(self.mode)

        self._ip_widget = QWidget()
        ip_box = QVBoxLayout(self._ip_widget)
        ip_box.setContentsMargins(0, 0, 0, 0)
        ip_box.setSpacing(8)
        ip_box.addWidget(_field_label("subnet (CIDR)"))
        self.subnet = QLineEdit(existing.subnet if editing else "192.168.150.0/24")
        ip_box.addWidget(self.subnet)
        dhcp_row = QHBoxLayout()
        s_col = QVBoxLayout()
        s_col.addWidget(_field_label("dhcp start (blank = none)"))
        self.dhcp_start = QLineEdit(existing.dhcp_start if editing else "192.168.150.10")
        s_col.addWidget(self.dhcp_start)
        e_col = QVBoxLayout()
        e_col.addWidget(_field_label("dhcp end"))
        self.dhcp_end = QLineEdit(existing.dhcp_end if editing else "192.168.150.254")
        e_col.addWidget(self.dhcp_end)
        dhcp_row.addLayout(s_col)
        dhcp_row.addLayout(e_col)
        ip_box.addLayout(dhcp_row)
        box.addWidget(self._ip_widget)

        self._bridge_widget = QWidget()
        br_box = QVBoxLayout(self._bridge_widget)
        br_box.setContentsMargins(0, 0, 0, 0)
        br_box.setSpacing(8)
        br_box.addWidget(_field_label("host bridge device"))
        self.bridge_dev = QLineEdit(existing.bridge_dev if editing else "br0")
        br_box.addWidget(self.bridge_dev)
        self._bridge_widget.hide()
        box.addWidget(self._bridge_widget)

        box.addSpacing(6)
        box.addLayout(_buttons(self, "Save" if editing else "Create network"))
        self._ok_button.setEnabled(editing)
        self.name.textChanged.connect(
            lambda t: self._ok_button.setEnabled(bool(t.strip()))
        )
        self.mode.currentIndexChanged.connect(self._mode_changed)
        if editing:
            self.mode.setCurrentIndex(
                {"nat": 0, "isolated": 1, "bridge": 2}.get(existing.mode, 0)
            )
            self._mode_changed(self.mode.currentIndex())

    def _mode_changed(self, index: int) -> None:
        self._ip_widget.setVisible(index != 2)
        self._bridge_widget.setVisible(index == 2)

    def network_mode(self) -> str:
        return ("nat", "isolated", "bridge")[self.mode.currentIndex()]


class NetworkDetailsDialog(SizedDialog):
    """Create or edit a network, including the parts virt-manager exposes:
    a second IPv6 subnet, static leases, routes, DNS and portgroups."""

    MODES = (
        ("nat", "nat - guests reach out, host routes"),
        ("isolated", "isolated - guests only talk to each other"),
        ("bridge", "bridge, join an existing host bridge"),
        ("open", "open - no filtering, host handles routing"),
    )

    def __init__(self, parent, existing=None) -> None:  # existing: NetworkSpec | None
        super().__init__(parent)
        from PySide6.QtWidgets import QPlainTextEdit, QTabWidget

        editing = existing is not None
        self.setWindowTitle("Edit network" if editing else "New network")
        self.setMinimumSize(560, 520)
        box = QVBoxLayout(self)
        box.setContentsMargins(24, 22, 24, 20)
        box.setSpacing(10)
        box.addWidget(_title("Edit network" if editing else "New virtual network"))
        if editing:
            note = QLabel(
                "Saving restarts the network. Running machines keep their "
                "interface but may need a DHCP refresh."
            )
            note.setWordWrap(True)
            note.setProperty("class", "Dim")
            box.addWidget(note)

        row = QHBoxLayout()
        name_col = QVBoxLayout()
        name_col.addWidget(_field_label("name"))
        self.name = QLineEdit(existing.name if editing else "")
        self.name.setPlaceholderText("lab")
        name_col.addWidget(self.name)
        mode_col = QVBoxLayout()
        mode_col.addWidget(_field_label("mode"))
        self.mode = QComboBox()
        for _key, label in self.MODES:
            self.mode.addItem(label)
        mode_col.addWidget(self.mode)
        row.addLayout(name_col, 1)
        row.addLayout(mode_col, 1)
        box.addLayout(row)

        tabs = QTabWidget()
        # -- IPv4
        v4 = QWidget()
        v4box = QVBoxLayout(v4)
        v4box.setSpacing(8)
        v4box.addWidget(_field_label("subnet (CIDR)"))
        self.subnet = QLineEdit(existing.subnet if editing else "192.168.150.0/24")
        v4box.addWidget(self.subnet)
        dhcp_row = QHBoxLayout()
        for label, attr, default in (
            ("dhcp start (blank = none)", "dhcp_start", "192.168.150.10"),
            ("dhcp end", "dhcp_end", "192.168.150.254"),
        ):
            col = QVBoxLayout()
            col.addWidget(_field_label(label))
            edit = QLineEdit(getattr(existing, attr) if editing else default)
            setattr(self, attr, edit)
            col.addWidget(edit)
            dhcp_row.addLayout(col)
        v4box.addLayout(dhcp_row)
        v4box.addWidget(_field_label("static leases, one per line: mac ip name"))
        self.leases = QPlainTextEdit(
            "\n".join(f"{m} {i} {n}" for m, i, n in existing.static_leases)
            if editing else ""
        )
        self.leases.setPlaceholderText("52:54:00:aa:bb:cc 192.168.150.50 buildbox")
        v4box.addWidget(self.leases)
        tabs.addTab(v4, "IPv4")

        # -- IPv6
        v6 = QWidget()
        v6box = QVBoxLayout(v6)
        v6box.setSpacing(8)
        v6box.addWidget(_field_label("subnet (CIDR, blank = no IPv6)"))
        self.ipv6_subnet = QLineEdit(existing.ipv6_subnet if editing else "")
        self.ipv6_subnet.setPlaceholderText("fd00:dead:beef::/64")
        v6box.addWidget(self.ipv6_subnet)
        self.ipv6_dhcp = QCheckBox("Hand out addresses with DHCPv6")
        self.ipv6_dhcp.setChecked(existing.ipv6_dhcp if editing else False)
        v6box.addWidget(self.ipv6_dhcp)
        v6hint = QLabel(
            "A unique-local prefix (fd00::/8) is the safe choice for a private "
            "network; the first address becomes the host's."
        )
        v6hint.setWordWrap(True)
        v6hint.setObjectName("ConsoleHint")
        v6box.addWidget(v6hint)
        v6box.addStretch(1)
        tabs.addTab(v6, "IPv6")

        # -- DNS and routing
        dns = QWidget()
        dbox = QVBoxLayout(dns)
        dbox.setSpacing(8)
        dbox.addWidget(_field_label("dns domain name"))
        self.domain_name = QLineEdit(existing.domain_name if editing else "")
        self.domain_name.setPlaceholderText("lab.local")
        dbox.addWidget(self.domain_name)
        dbox.addWidget(_field_label("dns forwarders, one address per line"))
        self.forwarders = QPlainTextEdit(
            "\n".join(existing.dns_forwarders) if editing else ""
        )
        self.forwarders.setPlaceholderText("1.1.1.1")
        self.forwarders.setMaximumHeight(70)
        dbox.addWidget(self.forwarders)
        dbox.addWidget(_field_label("dns host entries, one per line: ip name"))
        self.dns_hosts = QPlainTextEdit(
            "\n".join(f"{ip} {n}" for ip, n in existing.dns_hosts) if editing else ""
        )
        self.dns_hosts.setMaximumHeight(70)
        dbox.addWidget(self.dns_hosts)
        dbox.addWidget(_field_label("static routes, one per line: cidr gateway"))
        self.routes = QPlainTextEdit(
            "\n".join(f"{c} {g}" for c, g in existing.routes) if editing else ""
        )
        self.routes.setPlaceholderText("10.10.0.0/16 192.168.150.254")
        self.routes.setMaximumHeight(70)
        dbox.addWidget(self.routes)
        tabs.addTab(dns, "DNS & routes")

        # -- bridge / portgroups
        adv = QWidget()
        abox = QVBoxLayout(adv)
        abox.setSpacing(8)
        abox.addWidget(_field_label("host bridge device (bridge mode)"))
        self.bridge_dev = QLineEdit(existing.bridge_dev if editing else "br0")
        abox.addWidget(self.bridge_dev)
        abox.addWidget(_field_label("bind to host interface (optional)"))
        self.forward_dev = QLineEdit(existing.forward_dev if editing else "")
        self.forward_dev.setPlaceholderText("eth0 - leave blank for any")
        abox.addWidget(self.forward_dev)
        abox.addWidget(
            _field_label("portgroups, one per line: name inbound_kbps outbound_kbps")
        )
        self.portgroups = QPlainTextEdit(
            "\n".join(
                f"{n} {i} {o}{' default' if d else ''}"
                for n, d, i, o in existing.portgroups
            ) if editing else ""
        )
        self.portgroups.setPlaceholderText("throttled 10000 10000\nfast 0 0 default")
        abox.addWidget(self.portgroups)
        tabs.addTab(adv, "Advanced")
        box.addWidget(tabs, 1)

        box.addSpacing(6)
        box.addLayout(_buttons(self, "Save" if editing else "Create network"))
        self._ok_button.setEnabled(editing)
        self.name.textChanged.connect(
            lambda t: self._ok_button.setEnabled(bool(t.strip()))
        )
        if editing:
            index = next(
                (i for i, (key, _l) in enumerate(self.MODES) if key == existing.mode), 0
            )
            self.mode.setCurrentIndex(index)

    def spec(self):
        from ..core.networks import NetworkSpec

        def lines(widget):
            return [l.strip() for l in widget.toPlainText().splitlines() if l.strip()]

        leases = []
        for line in lines(self.leases):
            parts = line.split()
            if len(parts) >= 3:
                leases.append((parts[0], parts[1], parts[2]))
        hosts = []
        for line in lines(self.dns_hosts):
            parts = line.split()
            if len(parts) >= 2:
                hosts.append((parts[0], parts[1]))
        routes = []
        for line in lines(self.routes):
            parts = line.split()
            if len(parts) >= 2:
                routes.append((parts[0], parts[1]))
        groups = []
        for line in lines(self.portgroups):
            parts = line.split()
            if not parts:
                continue
            name = parts[0]
            inbound = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
            outbound = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
            groups.append((name, "default" in parts[1:], inbound, outbound))
        return NetworkSpec(
            name=self.name.text().strip(),
            mode=self.MODES[self.mode.currentIndex()][0],
            subnet=self.subnet.text().strip(),
            dhcp_start=self.dhcp_start.text().strip(),
            dhcp_end=self.dhcp_end.text().strip(),
            bridge_dev=self.bridge_dev.text().strip(),
            forward_dev=self.forward_dev.text().strip(),
            domain_name=self.domain_name.text().strip(),
            dns_forwarders=tuple(lines(self.forwarders)),
            dns_hosts=tuple(hosts),
            static_leases=tuple(leases),
            routes=tuple(routes),
            ipv6_subnet=self.ipv6_subnet.text().strip(),
            ipv6_dhcp=self.ipv6_dhcp.isChecked(),
            portgroups=tuple(groups),
        )


class NwFiltersDialog(SizedDialog):
    """libvirt's network filters: list, edit, define, delete.

    Filters are per-connection, not per-network, which is why they open
    from the page header rather than from a card.

    Data comes in and actions go out through callbacks, like every other
    dialog here - it used to fetch its own list in the constructor, which
    meant its size depended on when a background call happened to land.
    """

    def __init__(self, parent, filters=None, status: str = "") -> None:
        # filters: list[NwFilterInfo]
        super().__init__(parent)
        from PySide6.QtWidgets import QPlainTextEdit

        from ..syntax import XmlHighlighter

        # set by the caller
        self.load_requested = None      # (name) -> None
        self.save_requested = None      # (xml) -> None
        self.delete_requested = None    # (name) -> None
        self.new_template = None        # (name) -> str

        self.setWindowTitle("Network filters")
        self.setMinimumSize(760, 520)
        box = QVBoxLayout(self)
        box.setContentsMargins(24, 22, 24, 20)
        box.setSpacing(10)
        box.addWidget(_title("Network filters"))
        note = QLabel(
            "A filter is firewall rules a NIC opts into - assign one from the "
            "interface's editor on the Hardware tab. clean-traffic and the "
            "other no- filters ship with libvirt; filters can include each "
            "other by reference."
        )
        note.setWordWrap(True)
        note.setProperty("class", "Dim")
        box.addWidget(note)

        split = QHBoxLayout()
        split.setSpacing(12)
        self.filter_list = QListWidget()
        self.filter_list.setMaximumWidth(280)
        self.filter_list.currentTextChanged.connect(self._load_filter)
        split.addWidget(self.filter_list)
        self.editor = QPlainTextEdit()
        self._highlighter = XmlHighlighter(self.editor.document())
        split.addWidget(self.editor, 1)
        box.addLayout(split, 1)

        self.status = QLabel(status)
        self.status.setObjectName("ConsoleHint")
        self.status.setWordWrap(True)
        box.addWidget(self.status)

        row = QHBoxLayout()
        row.setSpacing(8)
        new_btn = QPushButton("New filter")
        new_btn.setProperty("class", "GhostButton")
        new_btn.clicked.connect(self._new_filter)
        row.addWidget(new_btn)
        delete_btn = QPushButton("Delete")
        delete_btn.setProperty("class", "GhostButton")
        delete_btn.clicked.connect(self._delete_filter)
        row.addWidget(delete_btn)
        row.addStretch(1)
        save_btn = QPushButton("Save filter")
        save_btn.setProperty("class", "PrimaryButton")
        save_btn.clicked.connect(self._save_filter)
        row.addWidget(save_btn)
        close_btn = QPushButton("Close")
        close_btn.setProperty("class", "GhostButton")
        close_btn.clicked.connect(self.accept)
        row.addWidget(close_btn)
        box.addLayout(row)

        self.populate(filters or [])

    def populate(self, filters, select: str = "") -> None:
        """Fill the list; the caller reads the filters and hands them here."""
        self.filter_list.blockSignals(True)
        self.filter_list.clear()
        for f in filters:
            extra = f" + {', '.join(f.refs)}" if f.refs else ""
            self.filter_list.addItem(f.name)
            item = self.filter_list.item(self.filter_list.count() - 1)
            item.setToolTip(
                f"chain {f.chain or 'root'} · {f.rules} rule(s){extra}"
            )
        self.filter_list.blockSignals(False)
        if select:
            matches = self.filter_list.findItems(
                select, Qt.MatchFlag.MatchExactly
            )
            if matches:
                self.filter_list.setCurrentItem(matches[0])

    def show_xml(self, xml: str) -> None:
        self.editor.setPlainText(xml)

    def set_status(self, text: str) -> None:
        self.status.setText(text)

    def _load_filter(self, name: str) -> None:
        if name and self.load_requested is not None:
            self.load_requested(name)

    def _new_filter(self) -> None:
        self.filter_list.setCurrentItem(None)
        if self.new_template is not None:
            self.editor.setPlainText(self.new_template("my-filter"))
        self.status.setText(
            "edit the name and rules, then Save filter defines it"
        )

    def _save_filter(self) -> None:
        if self.save_requested is not None:
            self.save_requested(self.editor.toPlainText())

    def _delete_filter(self) -> None:
        item = self.filter_list.currentItem()
        if item is not None and self.delete_requested is not None:
            self.delete_requested(item.text())
