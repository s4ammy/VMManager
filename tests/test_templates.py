"""Templates page: matching clones to their template, and naming them."""

from __future__ import annotations

import pytest

from vmmanager.core.models import BackingIndex, DomainSnapshot


def snap(name: str, *, template: bool = False, disks=(), state: str = "shutoff"):
    return DomainSnapshot(
        uuid=f"uuid-{name}", name=name, state=state, vcpus=2, memory_mb=1024,
        autostart=False, is_template=template, disk_paths=tuple(disks),
    )


BASE = "/pool/base.qcow2"
MID = "/pool/mid.qcow2"          # a template that is itself a clone of BASE
INDEX = BackingIndex(
    backing_of={
        "/pool/web-01.qcow2": BASE,
        "/pool/web-02.qcow2": BASE,
        MID: BASE,
        "/pool/leaf.qcow2": MID,
        "/pool/other.qcow2": "/pool/somethingelse.qcow2",
    },
    capacity_of={BASE: 2 * 1024**3, "/pool/web-01.qcow2": 2 * 1024**3},
    allocation_of={
        "/pool/web-01.qcow2": 200_704, "/pool/web-02.qcow2": 196_608,
        MID: 512_000, "/pool/leaf.qcow2": 65_536,
    },
)


def test_backing_index_finds_overlays_on_a_template():
    assert sorted(INDEX.clones_of([BASE])) == [
        MID, "/pool/web-01.qcow2", "/pool/web-02.qcow2"
    ]


def test_backing_index_ignores_overlays_on_other_images():
    assert INDEX.clones_of(["/pool/unrelated.qcow2"]) == []


def test_backing_index_of_nothing_is_nothing():
    assert INDEX.clones_of([]) == []


@pytest.fixture
def page(qapp):
    from vmmanager.pages.templates import TemplatesPage

    p = TemplatesPage()
    p._index = INDEX
    p._domains = [
        snap("base", template=True, disks=[BASE]),
        snap("web-01", disks=["/pool/web-01.qcow2"], state="running"),
        snap("web-02", disks=["/pool/web-02.qcow2"]),
        snap("mid", template=True, disks=[MID]),
        snap("leaf", disks=["/pool/leaf.qcow2"]),
        snap("unrelated", disks=["/pool/other.qcow2"]),
    ]
    return p


def test_clones_are_matched_and_sized(page):
    base = page._domains[0]
    found = page._clones_of(base)
    # mid is layered on base too, so it counts as one of its clones
    assert [c.name for c, _size in found] == ["mid", "web-01", "web-02"]
    assert dict((c.name, size) for c, size in found)["web-01"] == 200_704


def test_a_template_that_is_itself_a_clone_lists_its_own_children(page):
    """mid sits on base and leaf sits on mid; each level reports its own."""
    mid = next(d for d in page._domains if d.name == "mid")
    assert [c.name for c, _ in page._clones_of(mid)] == ["leaf"]


def test_only_direct_children_count(page):
    """leaf sits under mid, which sits under base; base should not claim leaf."""
    base = page._domains[0]
    assert "leaf" not in [c.name for c, _ in page._clones_of(base)]


def test_machines_on_other_images_are_not_clones(page):
    names = [c.name for c, _ in page._clones_of(page._domains[0])]
    assert "unrelated" not in names


def test_without_the_index_no_relationships_are_guessed(qapp):
    from vmmanager.pages.templates import TemplatesPage

    p = TemplatesPage()
    p._domains = [snap("base", template=True, disks=[BASE]),
                  snap("web-01", disks=["/pool/web-01.qcow2"])]
    assert p._clones_of(p._domains[0]) == []


def test_empty_state_shows_only_when_there_are_no_templates(qapp):
    from vmmanager.pages.templates import TemplatesPage

    p = TemplatesPage()
    p.show()
    p.set_domains([snap("plain")], ["default"])
    assert p.empty.isVisible()

    p.set_domains([snap("base", template=True, disks=[BASE])], ["default"])
    assert not p.empty.isVisible()


@pytest.mark.parametrize("template,expected", [
    ("debian-13-base", "debian-13"),
    ("win11-golden", "win11"),
    ("ubuntu-template", "ubuntu"),
    ("arch-tmpl", "arch"),
    # nothing to strip, so add rather than clash with the template's own name
    ("plain", "plain-clone"),
    ("base", "base-clone"),
])
def test_prefix_suggestion(template, expected):
    from vmmanager.pages.templates import _suggest_prefix

    assert _suggest_prefix(template) == expected


def test_one_clone_keeps_the_plain_name(qapp):
    from vmmanager.pages.templates import DeployDialog

    d = DeployDialog(None, "debian-base", ["default"], "default")
    d.name.setText("web")
    d.count.setValue(1)
    assert d.names() == ["web"]


def test_several_clones_are_numbered(qapp):
    from vmmanager.pages.templates import DeployDialog

    d = DeployDialog(None, "debian-base", ["default"], "default")
    d.name.setText("web")
    d.count.setValue(3)
    assert d.names() == ["web-01", "web-02", "web-03"]


def test_deploy_dialog_defaults_to_the_templates_network(qapp):
    from vmmanager.pages.templates import DeployDialog

    d = DeployDialog(None, "debian-base", ["default", "lab"], "lab")
    assert d.network.currentText() == "lab"


@pytest.mark.parametrize("size,expected", [
    (0, "0B"),
    (512, "512B"),
    (196_608, "192K"),
    (int(1.5 * 1024**3), "1.5G"),
    (2 * 1024**3, "2.0G"),
])
def test_sizes_keep_a_decimal_where_it_matters(size, expected):
    """A 1.5G image reading as 2G is what this replaced."""
    from vmmanager.widgets import fmt_size

    assert fmt_size(size) == expected
