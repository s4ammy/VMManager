"""The stylesheet, checked for two Qt traps we already fell into.

Arrows and ticks come from `image:`, not CSS borders. The border-triangle trick
makes Qt paint the subcontrol's box instead, so you get a square.

`QWidget:hover::subcontrol` draws a stray second copy of the image centred on
the widget, even unhovered. Put the pseudo-state last instead:
`QWidget::subcontrol:hover`.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

import vmmanager.theme as theme

QSS = theme.QSS


def test_no_subcontrol_relies_on_border_triangles():
    """`image: none` on an arrow subcontrol is the square bug."""
    offenders = [
        line.strip()
        for line in QSS.splitlines()
        if "image: none" in line
    ]
    assert offenders == [], f"arrows drawn with borders instead of an image: {offenders}"


def test_every_referenced_icon_exists():
    urls = re.findall(r"url\(([^)]+)\)", QSS)
    assert urls, "the stylesheet should reference at least the arrow icons"
    missing = [u for u in urls if not Path(u).exists()]
    assert missing == [], f"stylesheet points at files that are not there: {missing}"


def test_icons_are_addressed_absolutely():
    """A relative url() resolves against the cwd, silently."""
    for url in re.findall(r"url\(([^)]+)\)", QSS):
        assert url.startswith("/"), f"relative icon path: {url}"


def test_no_hover_before_subcontrol():
    """This form double-draws. Pseudo-state goes last."""
    offenders = re.findall(r"[A-Za-z]+:hover::[a-z-]+", QSS)
    assert offenders == [], (
        "these draw a stray centred copy of the image - write them as "
        f"::subcontrol:hover instead: {sorted(set(offenders))}"
    )


@pytest.mark.parametrize("subcontrol", [
    "QComboBox::down-arrow",
    "QSpinBox::up-arrow",
    "QSpinBox::down-arrow",
    "QCheckBox::indicator:checked",
    "QTabBar QToolButton::left-arrow",
    "QTabBar QToolButton::right-arrow",
])
def test_arrow_subcontrols_have_an_image(subcontrol):
    """Without one, these render as bare boxes."""
    block = re.search(
        re.escape(subcontrol) + r"[^{]*\{([^}]*)\}", QSS
    )
    assert block, f"no rule for {subcontrol}"
    assert "image: url(" in block.group(1), f"{subcontrol} has no image"


def test_widgets_qt_would_otherwise_style_itself_are_covered():
    """Unstyled widgets arrive in the platform's colours, not ours."""
    for selector in ("QProgressBar", "QScrollBar:horizontal", "QScrollBar:vertical"):
        assert selector in QSS, f"{selector} left to the platform default"


def test_renders_without_qt_warnings(qapp):
    """Qt shouldn't complain about the syntax."""
    from PySide6.QtCore import qInstallMessageHandler

    problems: list[str] = []
    handler = qInstallMessageHandler(
        lambda _mode, _ctx, message: problems.append(message)
    )
    try:
        qapp.setStyleSheet(QSS)
    finally:
        qInstallMessageHandler(handler)
    assert [p for p in problems if "Could not parse" in p or "Unknown property" in p] == []


# ---------------------------------------------------------------- corner radii


def test_every_radius_comes_from_the_token_scale():
    """Three sizes, defined once. A stray 12px is how a theme drifts."""
    allowed = {str(theme.RADIUS_SM), str(theme.RADIUS), str(theme.RADIUS_LG)}
    found = set(re.findall(r"border-radius: (\d+)px", QSS))
    assert found <= allowed, f"radii outside the scale: {sorted(found - allowed)}"


def test_the_scale_stays_restrained():
    """Corners hint at a shape, they do not round it.

    A theme may set all three the same - the one vmmanager ships does - so the
    scale only has to grow outwards, not strictly. What it must not do is get
    large: past 8px a panel reads as soft, which was the point of the change.
    """
    assert theme.RADIUS_SM <= theme.RADIUS <= theme.RADIUS_LG, (
        "an inner corner rounder than the panel around it looks like a mistake"
    )
    assert theme.RADIUS_LG <= 8, "anything more starts to read as soft again"


def test_painted_widgets_use_the_same_scale():
    """A rounded rect drawn in code should not disagree with the stylesheet."""
    from pathlib import Path

    project = Path(__file__).resolve().parent.parent / "vmmanager"
    offenders = []
    for path in project.rglob("*.py"):
        if path.name == "oslogos.py":
            continue  # logo glyphs are artwork, not chrome
        for line in path.read_text().splitlines():
            if "drawRoundedRect" in line and "theme.RADIUS" not in line:
                offenders.append(f"{path.name}: {line.strip()}")
    assert offenders == [], "painted corners bypassing the scale:\n" + "\n".join(offenders)
