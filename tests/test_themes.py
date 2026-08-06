"""Themes: the file format, the stylesheet built from it, and the editor.

The value of a theme file is that it is data, so the tests that matter are the
ones about data getting in and out intact, and about a bad value being refused
rather than interpolated into a stylesheet.
"""

from __future__ import annotations

import pytest

from vmmanager import theme as theme_module
from vmmanager.core import themes


@pytest.fixture
def theme_dir(tmp_path, monkeypatch):
    """Point the theme directory somewhere disposable."""
    monkeypatch.setattr(themes, "USER_DIR", tmp_path / "themes")
    return tmp_path / "themes"


@pytest.fixture
def restore_theme():
    """Put the module back on the built-in theme, whatever a test did to it."""
    yield
    theme_module.apply(themes.builtin_theme())


# -- the shipped theme


def test_the_shipped_theme_is_a_file_and_it_parses():
    shipped = themes.BUILTIN_DIR / f"{themes.BUILTIN_NAME}.toml"
    assert shipped.is_file(), "the theme vmmanager ships with should be a file"
    assert themes.load(shipped, builtin=True).name == "VMManager"


def test_the_shipped_theme_defines_every_token():
    """A token added without a value in the file would break every theme."""
    assert themes.validate(themes.builtin_theme().values) == {}


def test_the_shipped_theme_is_what_we_ship():
    """The look cannot drift without someone saying so here.

    If a change to the file is deliberate, change these too. The greys replaced
    the original purple-tinted surfaces; the accent and the state colours are
    the ones vmmanager has always had.
    """
    assert themes.builtin_theme().values == {
        "bg": "#1e1e1e", "bg_raised": "#141414", "bg_inset": "#0a0a0a",
        "border": "#2f2f2f", "border_bright": "#4a4a4a",
        "text": "#e8e8f2", "text_dim": "#ababab", "text_faint": "#727272",
        "accent": "#babaff", "accent_dim": "#4e4e7f",
        "ok": "#8fdcb0", "warn": "#e8c47f", "danger": "#e89090",
        "radius_small": 2, "radius_base": 2, "radius_large": 2,
        "font_display": "Chakra Petch", "font_body": "IBM Plex Sans",
        "font_mono": "IBM Plex Mono",
    }


def test_the_shipped_theme_cannot_be_written_to(theme_dir):
    with pytest.raises(ValueError, match="not editable"):
        themes.save(themes.builtin_theme())
    with pytest.raises(ValueError, match="cannot be deleted"):
        themes.delete(themes.builtin_theme())


# -- validation


@pytest.mark.parametrize("field,value,why", [
    ("bg", "0d0d13", "a colour without its hash"),
    ("bg", "#0d0d1", "five hex digits"),
    ("bg", "#gggggg", "not hex at all"),
    ("bg", "red; } * { color: red", "a colour that closes the rule"),
    ("bg", 4, "a number where a colour goes"),
    ("radius_base", "4", "a radius as a string"),
    ("radius_base", -1, "a negative radius"),
    ("radius_base", 999, "a radius past the cap"),
    ("radius_base", True, "a bool, which is an int in Python"),
    ("font_body", "Sans; } * { color: red", "a font name with punctuation"),
    ("font_body", "", "an empty font name"),
])
def test_validate_refuses(field, value, why):
    values = dict(themes.builtin_theme().values)
    values[field] = value
    assert field in themes.validate(values), f"should have refused {why}"


def test_validate_notices_a_missing_value():
    values = dict(themes.builtin_theme().values)
    del values["accent"]
    assert themes.validate(values) == {"accent": "missing"}


def test_a_theme_that_cannot_be_written_is_not_written(theme_dir):
    """The file on disk should never hold a value the app would refuse to read."""
    bad = themes.Theme("bad", dict(themes.builtin_theme().values) | {"bg": "nope"})
    with pytest.raises(ValueError, match="invalid"):
        themes.save(bad)
    assert not list(theme_dir.glob("*.toml")) if theme_dir.exists() else True


# -- round trip


def test_a_theme_survives_being_written_and_read(theme_dir):
    values = dict(themes.builtin_theme().values)
    values.update({"accent": "#268bd2", "radius_base": 0,
                   "font_body": "DejaVu Sans"})
    saved = themes.save(themes.Theme("Solarised light", values))

    assert saved.path == theme_dir / "solarised-light.toml"
    read_back = themes.load(saved.path)
    assert read_back.name == "Solarised light"
    assert read_back.values == values
    assert not read_back.builtin


def test_a_hand_written_file_may_leave_things_out(theme_dir):
    """Most partial files are written against an older version, not on purpose."""
    theme_dir.mkdir(parents=True)
    path = theme_dir / "partial.toml"
    path.write_text('name = "Partial"\n\n[colors]\naccent = "#ff8800"\n')

    shipped = themes.builtin_theme().values
    loaded = themes.load(path)
    assert loaded.values["accent"] == "#ff8800"
    assert loaded.values["bg"] == shipped["bg"]
    assert loaded.values["radius_base"] == shipped["radius_base"]
    assert loaded.values["font_mono"] == shipped["font_mono"]


def test_a_present_but_wrong_value_is_still_an_error(theme_dir):
    theme_dir.mkdir(parents=True)
    path = theme_dir / "broken.toml"
    path.write_text('name = "Broken"\n\n[colors]\naccent = "chartreuse"\n')
    with pytest.raises(ValueError, match="accent"):
        themes.load(path)


def test_one_unreadable_theme_does_not_hide_the_others(theme_dir):
    theme_dir.mkdir(parents=True)
    (theme_dir / "fine.toml").write_text(
        themes.dump(themes.builtin_theme().copy_as("Fine"))
    )
    (theme_dir / "broken.toml").write_text("this is not toml [[[")

    names = [t.name for t in themes.available()]
    assert names == ["VMManager", "Fine"]


def test_delete_removes_the_file(theme_dir):
    saved = themes.save(themes.builtin_theme().copy_as("Doomed"))
    assert saved.path.exists()
    themes.delete(saved)
    assert not saved.path.exists()
    assert [t.name for t in themes.available()] == ["VMManager"]


@pytest.mark.parametrize("name,expected", [
    ("Solarised Light", "solarised-light"),
    ("  spaced  out  ", "spaced-out"),
    ("../../etc/passwd", "etc-passwd"),
    ("///", "theme"),
    ("Ünicode", "nicode"),
])
def test_slug_makes_a_safe_file_name(name, expected):
    assert themes.slug(name) == expected


# -- derived colours


def test_hover_shades_follow_the_accent():
    values = dict(themes.builtin_theme().values) | {"accent": "#268bd2"}
    shades = themes.derived(values)
    assert shades["ACCENT_HOVER"] != themes.derived(
        themes.builtin_theme().values)["ACCENT_HOVER"]
    # lighter than the accent it came from, and still a colour
    assert themes.luminance(shades["ACCENT_HOVER"]) > themes.luminance("#268bd2")
    assert themes.HEX.match(shades["ACCENT_HOVER"])


def test_text_on_the_accent_is_the_one_that_contrasts():
    """A light theme needs the pale colour on a mid-tone accent, not the dark."""
    dark = dict(themes.builtin_theme().values)
    assert themes.derived(dark)["ON_ACCENT"] == dark["bg"]  # bg is the dark one

    light = dark | {"bg": "#fdf6e3", "text": "#3b3a32", "accent": "#268bd2"}
    assert themes.derived(light)["ON_ACCENT"] == "#fdf6e3"


# -- the stylesheet


def test_the_stylesheet_builds_for_the_shipped_theme():
    sheet = theme_module.build_qss(themes.builtin_theme())
    assert "#babaff" in sheet
    assert "{" in sheet and "}" in sheet
    assert "{BG}" not in sheet, "a token was left uninterpolated"


def test_the_stylesheet_has_no_leftover_placeholders():
    """Every name in the template must be something a theme provides."""
    import re

    sheet = theme_module.build_qss(themes.builtin_theme())
    leftovers = re.findall(r"\{[A-Z_]+\}", sheet)
    assert leftovers == []


def test_changing_a_theme_changes_the_styleheet():
    values = dict(themes.builtin_theme().values) | {"accent": "#268bd2"}
    sheet = theme_module.build_qss(themes.Theme("other", values))
    assert "#268bd2" in sheet
    assert "#babaff" not in sheet


def test_apply_rebinds_the_names_the_app_reads(restore_theme):
    values = dict(themes.builtin_theme().values)
    values.update({"accent": "#268bd2", "ok": "#859900", "radius_base": 0})
    theme_module.apply(themes.Theme("other", values))

    assert theme_module.ACCENT == "#268bd2"
    assert theme_module.RADIUS == 0
    assert theme_module.state_color("running") == "#859900"
    assert theme_module.state_color("nonsense") == theme_module.TEXT_FAINT
    assert "#268bd2" in theme_module.QSS
    assert theme_module.active.name == "other"


@pytest.mark.parametrize("token", themes.TOKENS, ids=lambda t: t.field)
def test_every_token_changes_the_stylesheet(token):
    """A token nothing interpolates is a knob in the editor that does nothing.

    Two sheets that differ only in this one value, compared whole - rather than
    looking for the value in the output, which finds it in `font-size: 17px` and
    passes for a radius that is not used at all.
    """
    pair = {"color": ("#010203", "#040506"), "radius": (11, 23),
            "font": ("Marker One", "Marker Two")}[token.kind]
    sheets = []
    for value in pair:
        values = dict(themes.builtin_theme().values)
        values[token.field] = value
        sheets.append(theme_module.build_qss(themes.Theme("t", values)))
    assert sheets[0] != sheets[1], (
        f"{token.field} is offered in the editor but the stylesheet ignores it"
    )


# -- no colour should be hardcoded where a theme cannot reach it


def test_token_colours_are_not_hardcoded_in_the_app():
    """A literal #9494ab in a widget is a place a theme has no effect.

    Excluded: theme.py and themes.py, which is where colours belong; the OS logo
    catalogue, whose colours are other people's brands; and the terminal's blue
    and cyan, which no token names.
    """
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent / "vmmanager"
    allowed = {"theme.py", "themes.py", "oslogos.py", "osident.py",
               "serialterm.py"}
    tokens = {
        str(v).lower() for v in themes.builtin_theme().values.values()
        if isinstance(v, str) and v.startswith("#")
    }
    offenders = []
    for path in sorted(root.rglob("*.py")):
        if path.name in allowed:
            continue
        for number, line in enumerate(path.read_text().splitlines(), 1):
            for found in re.findall(r"#[0-9a-fA-F]{6}\b", line):
                if found.lower() in tokens:
                    offenders.append(f"{path.relative_to(root)}:{number} {found}")
    assert offenders == [], (
        "these are theme colours written as literals, so a theme cannot change "
        "them:\n  " + "\n  ".join(offenders)
    )


# -- the page


@pytest.fixture
def page(qapp, theme_dir, restore_theme):
    from vmmanager.pages.themes import ThemesPage

    widget = ThemesPage()
    widget.show()
    qapp.processEvents()
    yield widget
    widget.close()


def make(page, name: str, **values):
    """Add a theme on disk and select it, without going through the dialogs."""
    merged = dict(themes.builtin_theme().values)
    merged.update(values)
    themes.save(themes.Theme(name, merged))
    page.refresh()
    page._select_by_name(name)
    return page._current


def test_the_page_lists_the_shipped_theme_first(page):
    assert page.list.item(0).text() == "VMManager"
    assert page._current.builtin


def test_the_shipped_theme_is_not_editable_on_the_page(page):
    assert not page._rows["accent"].swatch.isEnabled()
    assert page._rows["accent"].hex.isReadOnly()
    assert page.read_only.isVisible()
    assert not page._rename_btn.isEnabled()
    assert not page._delete_btn.isEnabled()


def test_editing_the_shipped_theme_is_ignored_even_if_something_tries(page):
    was = dict(themes.builtin_theme().values)
    page._edited("accent", "#ff0000")
    assert themes.builtin_theme().values == was


def test_selecting_a_theme_puts_it_on_the_app(page, qapp):
    make(page, "Blue", accent="#268bd2")
    qapp.processEvents()
    assert theme_module.active.name == "Blue"
    assert theme_module.ACCENT == "#268bd2"
    assert "#268bd2" in qapp.styleSheet()


def test_a_custom_theme_is_editable(page):
    make(page, "Mine")
    assert page._rows["accent"].swatch.isEnabled()
    assert not page.read_only.isVisible()
    assert page._rename_btn.isEnabled()
    assert page._delete_btn.isEnabled()


def test_editing_a_colour_applies_it_and_writes_it(page, qapp):
    saved = make(page, "Mine")
    page._rows["accent"]._from_text("#268bd2")
    page._apply_now()
    page._save_now()
    qapp.processEvents()

    assert theme_module.ACCENT == "#268bd2"
    assert 'accent = "#268bd2"' in saved.path.read_text()


def test_half_a_hex_code_is_not_applied(page):
    make(page, "Mine", accent="#268bd2")
    page._rows["accent"]._from_text("#26")
    assert page._current.values["accent"] == "#268bd2", "still the last whole colour"


def test_a_hex_code_without_its_hash_is_understood(page):
    make(page, "Mine")
    page._rows["accent"]._from_text("268bd2")
    assert page._current.values["accent"] == "#268bd2"


def test_editing_a_radius_applies_it(page, qapp):
    make(page, "Mine")
    page._numbers["radius_base"].setValue(0)
    page._apply_now()
    assert theme_module.RADIUS == 0
    assert "border-radius: 0px" in theme_module.QSS


def test_delete_falls_back_to_the_shipped_theme(page, qapp):
    saved = make(page, "Doomed")
    assert theme_module.active.name == "Doomed"
    themes.delete(saved)
    page._current = None
    page.refresh()
    qapp.processEvents()
    assert theme_module.active.name == "VMManager"
    assert not saved.path.exists()


def test_rename_keeps_the_values_and_drops_the_old_file(page):
    was = make(page, "Before", accent="#268bd2")
    renamed = themes.Theme("After", dict(was.values))
    themes.save(renamed)
    themes.delete(was)
    page._current = None
    page.refresh()

    assert [t.name for t in page._themes] == ["VMManager", "After"]
    assert themes.by_name("After").values["accent"] == "#268bd2"
    assert not was.path.exists()


def test_a_unique_name_is_suggested_rather_than_a_clash(page):
    make(page, "Mine")
    assert page._unique("Mine") == "Mine 2"
    make(page, "Mine 2")
    assert page._unique("Mine") == "Mine 3"
    assert page._unique("Other") == "Other"


def test_modules_that_use_theme_have_it_imported():
    """`theme.DANGER` in a module that never imported theme is a NameError
    waiting for the code path that reaches it.

    machines.py did exactly this: the line only runs when an action fails,
    so the crash hid behind a rare path until a suspended machine refused
    to resume and the error banner took the window down with it.

    The modules are imported rather than parsed, because several tabs get
    `theme` through `from .common import *` and only the import system
    knows that.
    """
    import ast
    import importlib
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent / "vmmanager"
    missing = []
    for path in sorted(root.rglob("*.py")):
        if path.name == "__main__.py":
            continue
        tree = ast.parse(path.read_text())
        uses_theme = any(
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == "theme"
            for node in ast.walk(tree)
        )
        if not uses_theme:
            continue
        # theme.py and themes.py take a Theme *called* theme, which is a
        # local and nothing to do with the module.
        local = any(
            (isinstance(node, ast.arg) and node.arg == "theme")
            or (isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store)
                and node.id == "theme")
            for node in ast.walk(tree)
        )
        if local:
            continue
        dotted = ".".join(
            ["vmmanager", *path.relative_to(root).with_suffix("").parts]
        ).removesuffix(".__init__")
        module = importlib.import_module(dotted)
        if not hasattr(module, "theme"):
            missing.append(dotted)
    assert missing == [], (
        "these use `theme.` but never bind the name: " + ", ".join(missing)
    )
