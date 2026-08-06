"""Anything that redefines a domain has to read it with its secrets.

libvirt leaves security-sensitive values out of XMLDesc unless asked:
the display password, chiefly. Read the plain form, change one element,
hand the whole thing back to defineXML, and every secret in it is gone -
silently, from an edit that had nothing to do with them.

Setting a console password and then toggling the boot menu was enough to
lose it, and thirty other operations did the same. So this is a rule about
the shape of the code rather than a test of any one of them: a function
that calls defineXML reads through _editable_xml, which asks for the
secure form.
"""

from __future__ import annotations

import ast
from pathlib import Path

CORE = Path(__file__).resolve().parent.parent / "vmmanager" / "core"


def _redefining_functions(tree: ast.Module):
    """Every function whose body reaches defineXML, with its XMLDesc reads.

    A read is attributed to the innermost function holding it - these are
    written as an outer svc_* around an inner go(conn), and reporting both
    would name every site twice.
    """
    functions = [
        n for n in ast.walk(tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]

    def calls(node, name):
        return [
            c for c in ast.walk(node)
            if isinstance(c, ast.Call)
            and (getattr(c.func, "attr", "") or getattr(c.func, "id", "")) == name
        ]

    for node in functions:
        if not calls(node, "defineXML"):
            continue
        inner = [
            f for f in functions
            if f is not node and f.lineno >= node.lineno
            and f.end_lineno <= node.end_lineno
        ]
        mine = [
            c for c in calls(node, "XMLDesc")
            if not any(f.lineno <= c.lineno <= f.end_lineno for f in inner)
            # The domain being edited is `dom` throughout this package.
            # Reading some other domain, or a node device, to look at it is
            # not what this rule is about.
            and getattr(c.func.value, "id", "") == "dom"
        ]
        if mine:
            yield node, mine


def _asks_for_secrets(call: ast.Call) -> bool:
    return "SECURE" in ast.unparse(call)


def _offenders(path: Path) -> list[str]:
    tree = ast.parse(path.read_text())
    out = []
    for node, reads in _redefining_functions(tree):
        for call in reads:
            if not _asks_for_secrets(call):
                out.append(f"{path.name}:{call.lineno} {node.name}")
    return out


def test_the_detector_catches_a_planted_violation(tmp_path):
    """A test that cannot fail is not a test."""
    sample = tmp_path / "sample.py"
    sample.write_text(
        "def svc_edit(uuid):\n"
        "    def go(conn):\n"
        "        dom = conn.lookupByUUIDString(uuid)\n"
        "        root = ET.fromstring(dom.XMLDesc(libvirt.VIR_DOMAIN_XML_INACTIVE))\n"
        "        conn.defineXML(ET.tostring(root))\n"
        "    return _with_conn(go)\n"
    )
    assert len(_offenders(sample)) == 1


def test_the_detector_accepts_the_secure_form(tmp_path):
    sample = tmp_path / "ok.py"
    sample.write_text(
        "def svc_edit(uuid):\n"
        "    def go(conn):\n"
        "        dom = conn.lookupByUUIDString(uuid)\n"
        "        root = ET.fromstring(dom.XMLDesc(\n"
        "            libvirt.VIR_DOMAIN_XML_INACTIVE | libvirt.VIR_DOMAIN_XML_SECURE))\n"
        "        conn.defineXML(ET.tostring(root))\n"
        "    return _with_conn(go)\n"
    )
    assert _offenders(sample) == []


def test_no_redefine_reads_the_definition_without_its_secrets():
    problems = [
        line for path in sorted(CORE.glob("*.py")) for line in _offenders(path)
    ]
    assert problems == [], (
        "these drop the display password on the way past - read through "
        "_editable_xml:\n" + "\n".join(problems)
    )


def test_the_helper_asks_for_the_secure_form(testconn):
    """The rule above is about call sites; this is about the helper they
    all now go through, which is where the flag actually gets set."""
    from vmmanager.core.xmlutil import _editable_xml

    dom = testconn.defineXML("""
      <domain type='test'>
        <name>secretive</name>
        <memory unit='MiB'>64</memory>
        <os><type arch='x86_64'>hvm</type></os>
        <devices>
          <graphics type='spice' port='-1' autoport='yes' passwd='hunter2'/>
        </devices>
      </domain>
    """)
    try:
        import libvirt as _libvirt

        plain = dom.XMLDesc(_libvirt.VIR_DOMAIN_XML_INACTIVE)
        assert "hunter2" not in plain, (
            "libvirt masks it by default - that is the whole problem"
        )
        assert _editable_xml(dom).find("devices/graphics").get("passwd") == "hunter2"
    finally:
        dom.undefine()


def test_a_secure_read_written_straight_back_keeps_the_password(testconn):
    """The failure in one line: read, define, and the secret is gone."""
    from vmmanager.core.xmlutil import _editable_xml
    import xml.etree.ElementTree as ET

    dom = testconn.defineXML("""
      <domain type='test'>
        <name>keeps-it</name>
        <memory unit='MiB'>64</memory>
        <os><type arch='x86_64'>hvm</type></os>
        <devices>
          <graphics type='spice' port='-1' autoport='yes' passwd='hunter2'/>
        </devices>
      </domain>
    """)
    try:
        root = _editable_xml(dom)
        testconn.defineXML(ET.tostring(root, encoding="unicode"))
        assert _editable_xml(dom).find("devices/graphics").get("passwd") == "hunter2"
    finally:
        dom.undefine()
