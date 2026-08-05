"""Virtual network listing and definition."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass

import libvirt

from .connection import _with_conn
from .models import LeaseInfo, NetworkDef, NetworkInfo
from .xmlesc import check_name, x
from .xmlutil import _network_xml

def _parse_or_none(xml: str) -> ET.Element | None:
    try:
        return ET.fromstring(xml)
    except ET.ParseError:
        return None


def svc_list_networks() -> list[NetworkInfo]:
    def go(conn):
        nets = []
        for net in conn.listAllNetworks():
            # libvirt occasionally reports XML it would not itself accept (an
            # unescaped & in a DNS domain, say). Skip that one network rather
            # than fail the whole page.
            root = _parse_or_none(net.XMLDesc(0))
            if root is None:
                continue
            forward = root.find("forward")
            leases: list[LeaseInfo] = []
            if net.isActive():
                try:
                    for lease in net.DHCPLeases():
                        leases.append(
                            LeaseInfo(
                                ip=lease.get("ipaddr", "?"),
                                mac=lease.get("mac", "?"),
                                hostname=lease.get("hostname") or " - ",
                                expires=lease.get("expirytime", 0),
                            )
                        )
                except libvirt.libvirtError:
                    pass
            nets.append(
                NetworkInfo(
                    name=net.name(),
                    active=bool(net.isActive()),
                    autostart=bool(net.autostart()),
                    persistent=bool(net.isPersistent()),
                    bridge=root.findtext("bridge/[@name]") or (root.find("bridge").get("name") if root.find("bridge") is not None else " - "),
                    mode=forward.get("mode", "isolated") if forward is not None else "isolated",
                    leases=tuple(leases),
                )
            )
        nets.sort(key=lambda n: n.name.lower())
        return nets

    return _with_conn(go)

def svc_network_action(name: str, op: str) -> None:
    def go(conn):
        net = conn.networkLookupByName(name)
        ops = {
            "start": net.create,
            "stop": net.destroy,
            "autostart-on": lambda: net.setAutostart(1),
            "autostart-off": lambda: net.setAutostart(0),
        }
        ops[op]()

    _with_conn(go)

def svc_list_network_names() -> list[str]:
    return _with_conn(lambda c: sorted(n.name() for n in c.listAllNetworks()))

def svc_get_network_def(name: str) -> NetworkDef:
    def go(conn):
        import ipaddress

        root = ET.fromstring(conn.networkLookupByName(name).XMLDesc(0))
        forward = root.find("forward")
        mode = forward.get("mode", "isolated") if forward is not None else "isolated"
        if mode == "bridge":
            bridge = root.find("bridge")
            return NetworkDef(
                name=name, mode="bridge", subnet="", dhcp_start="", dhcp_end="",
                bridge_dev=bridge.get("name", "") if bridge is not None else "",
            )
        subnet = dhcp_start = dhcp_end = ""
        ip = root.find("ip")
        if ip is not None and ip.get("address"):
            iface = ipaddress.ip_interface(
                f"{ip.get('address')}/{ip.get('netmask') or ip.get('prefix') or '24'}"
            )
            subnet = str(iface.network)
            rng = ip.find("dhcp/range")
            if rng is not None:
                dhcp_start = rng.get("start", "")
                dhcp_end = rng.get("end", "")
        return NetworkDef(
            name=name, mode="nat" if mode == "nat" else "isolated", subnet=subnet,
            dhcp_start=dhcp_start, dhcp_end=dhcp_end, bridge_dev="",
        )

    return _with_conn(go)

def svc_create_network(
    name: str,
    mode: str,
    subnet: str = "",
    dhcp_start: str = "",
    dhcp_end: str = "",
    bridge_dev: str = "",
) -> None:
    def go(conn):
        net = conn.networkDefineXML(
            _network_xml(name, mode, subnet, dhcp_start, dhcp_end, bridge_dev)
        )
        try:
            net.create()
            net.setAutostart(1)
        except libvirt.libvirtError:
            net.undefine()
            raise

    _with_conn(go)

def svc_redefine_network(
    old_name: str,
    name: str,
    mode: str,
    subnet: str = "",
    dhcp_start: str = "",
    dhcp_end: str = "",
    bridge_dev: str = "",
) -> None:
    """Replace a network's definition (stop -> undefine -> define -> start)."""

    def go(conn):
        old = conn.networkLookupByName(old_name)
        was_active = bool(old.isActive())
        autostart = bool(old.autostart())
        if was_active:
            old.destroy()
        old.undefine()
        net = conn.networkDefineXML(
            _network_xml(name, mode, subnet, dhcp_start, dhcp_end, bridge_dev)
        )
        if autostart:
            net.setAutostart(1)
        if was_active:
            net.create()

    _with_conn(go)

def svc_delete_network(name: str) -> None:
    def go(conn):
        net = conn.networkLookupByName(name)
        if net.isActive():
            net.destroy()
        if net.isPersistent():
            net.undefine()

    _with_conn(go)


# -- fuller network definitions
#
# The basic builder covers NAT/isolated/bridge with one DHCP range. This one
# adds what libvirt's network XML also supports and people actually configure:
# a second IPv6 subnet, static routes, a DNS domain with forwarders and host
# entries, and portgroups for per-interface QoS/VLAN.


@dataclass(frozen=True)
class NetworkSpec:
    name: str
    mode: str  # nat | isolated | bridge | open
    subnet: str = ""
    dhcp_start: str = ""
    dhcp_end: str = ""
    bridge_dev: str = ""
    forward_dev: str = ""  # bind NAT to one host interface
    domain_name: str = ""
    dns_forwarders: tuple[str, ...] = ()  # upstream resolvers
    dns_hosts: tuple[tuple[str, str], ...] = ()  # (ip, hostname)
    static_leases: tuple[tuple[str, str, str], ...] = ()  # (mac, ip, name)
    routes: tuple[tuple[str, str], ...] = ()  # (cidr, gateway)
    ipv6_subnet: str = ""  # e.g. fd00:dead:beef::/64
    ipv6_dhcp: bool = False
    portgroups: tuple[tuple[str, bool, int, int], ...] = ()
    # (name, is_default, inbound_kbps, outbound_kbps)


def _ip_block(subnet: str, dhcp_start: str, dhcp_end: str, leases, family: str,
              dhcp_v6: bool = False) -> str:
    import ipaddress

    if not subnet:
        return ""
    net = ipaddress.ip_network(subnet, strict=False)
    gateway = str(next(net.hosts()))
    lease_xml = "".join(
        f"<host mac='{x(mac)}' ip='{x(ip)}' name='{x(name)}'/>" if mac
        else f"<host ip='{x(ip)}' name='{x(name)}'/>"
        for mac, ip, name in leases
    )
    dhcp = ""
    if dhcp_start and dhcp_end:
        dhcp = f"<dhcp><range start='{x(dhcp_start)}' end='{x(dhcp_end)}'/>{lease_xml}</dhcp>"
    elif lease_xml:
        dhcp = f"<dhcp>{lease_xml}</dhcp>"
    elif family == "ipv6" and dhcp_v6:
        # libvirt discards an empty <dhcp/>, so DHCPv6 needs a real range;
        # ::10 through ::1000 of the prefix is roomy and out of the way.
        first = net.network_address + 0x10
        last = net.network_address + 0x1000
        dhcp = f"<dhcp><range start='{first}' end='{last}'/></dhcp>"
    if family == "ipv6":
        return (
            f"<ip family='ipv6' address='{gateway}' prefix='{net.prefixlen}'>"
            f"{dhcp}</ip>"
        )
    return f"<ip address='{gateway}' netmask='{net.netmask}'>{dhcp}</ip>"


def _network_xml_ex(spec: NetworkSpec) -> str:
    if spec.mode == "bridge":
        return (
            f"<network><name>{x(spec.name)}</name><forward mode='bridge'/>"
            f"<bridge name='{x(spec.bridge_dev)}'/></network>"
        )
    forward = ""
    if spec.mode in ("nat", "open", "route"):
        dev = f" dev='{x(spec.forward_dev)}'" if spec.forward_dev else ""
        forward = f"<forward mode='{spec.mode}'{dev}/>"
    domain = ""
    if spec.domain_name:
        domain = f"<domain name='{x(spec.domain_name)}' localOnly='yes'/>"
    dns = ""
    if spec.dns_forwarders or spec.dns_hosts:
        fwd = "".join(f"<forwarder addr='{x(a)}'/>" for a in spec.dns_forwarders)
        hosts = "".join(
            f"<host ip='{x(ip)}'><hostname>{x(name)}</hostname></host>"
            for ip, name in spec.dns_hosts
        )
        dns = f"<dns>{fwd}{hosts}</dns>"
    routes = "".join(
        f"<route address='{x(cidr.split('/')[0])}' prefix='{x(cidr.split('/')[1])}'"
        f" gateway='{x(gw)}'/>"
        for cidr, gw in spec.routes if "/" in cidr
    )
    v4 = _ip_block(
        spec.subnet, spec.dhcp_start, spec.dhcp_end, spec.static_leases, "ipv4"
    )
    v6 = _ip_block(spec.ipv6_subnet, "", "", (), "ipv6", spec.ipv6_dhcp)
    groups = "".join(
        f"<portgroup name='{x(name)}'{' default=\'yes\'' if is_default else ''}>"
        + (
            "<bandwidth>"
            + (f"<inbound average='{x(inb)}'/>" if inb else "")
            + (f"<outbound average='{x(outb)}'/>" if outb else "")
            + "</bandwidth>"
            if (inb or outb) else ""
        )
        + "</portgroup>"
        for name, is_default, inb, outb in spec.portgroups
    )
    return (
        f"<network><name>{x(spec.name)}</name>{forward}{domain}{dns}"
        f"{v4}{v6}{routes}{groups}</network>"
    )


def _check_spec(spec: NetworkSpec) -> None:
    """Refuse text libvirt cannot report back to us intact."""
    check_name(spec.name, "A network name")
    check_name(spec.domain_name, "A DNS domain")
    for _ip, hostname in spec.dns_hosts:
        check_name(hostname, "A DNS host name")
    for name, _default, _inb, _outb in spec.portgroups:
        check_name(name, "A portgroup name")


def svc_create_network_ex(spec: NetworkSpec) -> None:
    def go(conn):
        _check_spec(spec)
        net = conn.networkDefineXML(_network_xml_ex(spec))
        try:
            net.create()
            net.setAutostart(1)
        except libvirt.libvirtError:
            net.undefine()
            raise

    _with_conn(go)


def svc_redefine_network_ex(old_name: str, spec: NetworkSpec) -> None:
    def go(conn):
        _check_spec(spec)
        old = conn.networkLookupByName(old_name)
        was_active = bool(old.isActive())
        autostart = bool(old.autostart())
        if was_active:
            old.destroy()
        old.undefine()
        net = conn.networkDefineXML(_network_xml_ex(spec))
        if autostart:
            net.setAutostart(1)
        if was_active:
            net.create()

    _with_conn(go)


def svc_get_network_spec(name: str) -> NetworkSpec:
    """Read a network back into a NetworkSpec so the dialog can edit it."""

    def go(conn):
        import ipaddress

        root = ET.fromstring(conn.networkLookupByName(name).XMLDesc(0))
        forward = root.find("forward")
        mode = forward.get("mode", "isolated") if forward is not None else "isolated"
        bridge = root.find("bridge")
        subnet = dhcp_start = dhcp_end = ipv6_subnet = ""
        leases: list[tuple[str, str, str]] = []
        ipv6_dhcp = False
        for ip in root.findall("ip"):
            family = ip.get("family", "ipv4")
            addr = ip.get("address")
            if not addr:
                continue
            prefix = ip.get("prefix") or ip.get("netmask") or "24"
            iface = ipaddress.ip_interface(f"{addr}/{prefix}")
            if family == "ipv6":
                ipv6_subnet = str(iface.network)
                ipv6_dhcp = ip.find("dhcp") is not None
                continue
            subnet = str(iface.network)
            rng = ip.find("dhcp/range")
            if rng is not None:
                dhcp_start = rng.get("start", "")
                dhcp_end = rng.get("end", "")
            for host in ip.findall("dhcp/host"):
                leases.append(
                    (host.get("mac", ""), host.get("ip", ""), host.get("name", ""))
                )
        dns_el = root.find("dns")
        forwarders = tuple(
            f.get("addr", "") for f in dns_el.findall("forwarder")
        ) if dns_el is not None else ()
        dns_hosts = tuple(
            (h.get("ip", ""), h.findtext("hostname") or "")
            for h in (dns_el.findall("host") if dns_el is not None else [])
        )
        routes = tuple(
            (f"{r.get('address')}/{r.get('prefix')}", r.get("gateway", ""))
            for r in root.findall("route")
        )
        def _rate(group, direction: int) -> int:
            el = group.find(f"bandwidth/{direction}")
            return int(el.get("average", 0)) if el is not None else 0

        groups = tuple(
            (
                g.get("name", ""),
                g.get("default") == "yes",
                _rate(g, "inbound"),
                _rate(g, "outbound"),
            )
            for g in root.findall("portgroup")
        )
        domain_el = root.find("domain")
        return NetworkSpec(
            name=name,
            mode="bridge" if mode == "bridge" else mode,
            subnet=subnet, dhcp_start=dhcp_start, dhcp_end=dhcp_end,
            bridge_dev=bridge.get("name", "") if bridge is not None else "",
            forward_dev=forward.get("dev", "") if forward is not None else "",
            domain_name=domain_el.get("name", "") if domain_el is not None else "",
            dns_forwarders=forwarders, dns_hosts=dns_hosts,
            static_leases=tuple(leases), routes=routes,
            ipv6_subnet=ipv6_subnet, ipv6_dhcp=ipv6_dhcp, portgroups=groups,
        )

    return _with_conn(go)
