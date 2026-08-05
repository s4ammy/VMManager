"""Themes: the colours, corner radii and fonts the stylesheet is built from.

The look used to live as constants at the top of theme.py. It lives in a file
now - vmmanager's own theme ships as assets/themes/vmmanager.toml, and anything
you make sits in ~/.config/vmmanager/themes - so a theme can be edited in the
app, copied between machines, or kept in a dotfiles repo.

A theme names thirteen colours. The hover and pressed shades are not among them:
they are worked out from the colours you did pick, so an accent change stays
coherent instead of leaving a purple hover on a green button.

Values from a file end up interpolated into a Qt stylesheet, so every one of
them is checked against a pattern before it gets near the sheet. A theme file is
data, not code, and a colour that is not a colour is refused rather than passed
through.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field, replace
from pathlib import Path

from ..logs import log

BUILTIN_DIR = Path(__file__).parent.parent / "assets" / "themes"
BUILTIN_NAME = "vmmanager"  # the file name; the theme calls itself VMManager
USER_DIR = Path.home() / ".config" / "vmmanager" / "themes"

HEX = re.compile(r"^#[0-9a-fA-F]{6}$")
FONT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 \-]{0,47}$")
MAX_RADIUS = 24  # past this, panels read as lozenges and text starts colliding


@dataclass(frozen=True)
class Token:
    """One editable value, and how to present it."""

    key: str      # the name the stylesheet template uses
    field: str    # the name in the file
    group: str
    label: str
    note: str
    kind: str = "color"  # color | radius | font


TOKENS: tuple[Token, ...] = (
    Token("BG", "bg", "Surfaces", "Window",
          "Behind everything."),
    Token("BG_RAISED", "bg_raised", "Surfaces", "Panels",
          "Cards, the sidebar, menus and tooltips."),
    Token("BG_INSET", "bg_inset", "Surfaces", "Wells",
          "What sits inside a panel: text boxes, consoles, the host readout."),
    Token("BORDER", "border", "Surfaces", "Lines",
          "The edge of a panel or a field at rest."),
    Token("BORDER_BRIGHT", "border_bright", "Surfaces", "Lines, emphasised",
          "Hovered edges, dashed outlines and the focus ring."),

    Token("TEXT", "text", "Text", "Body",
          "Anything you are meant to read first."),
    Token("TEXT_DIM", "text_dim", "Text", "Secondary",
          "The explanatory line under a heading."),
    Token("TEXT_FAINT", "text_faint", "Text", "Faint",
          "Field labels, units, and values that are not set."),

    Token("ACCENT", "accent", "Accent", "Accent",
          "Buttons you are meant to press, the current page, selected rows."),
    Token("ACCENT_DIM", "accent_dim", "Accent", "Accent, muted",
          "The background behind a hovered row or the current nav item."),

    Token("OK", "ok", "States", "Running",
          "A machine that is up, and anything else going to plan."),
    Token("WARN", "warn", "States", "Paused",
          "Paused, suspended, shutting down: true but not finished."),
    Token("DANGER", "danger", "States", "Trouble",
          "Crashed, blocked, and the buttons that destroy things."),

    Token("RADIUS_SM", "radius_small", "Shape", "Small corners",
          "Chips, badges and indicator marks.", "radius"),
    Token("RADIUS", "radius_base", "Shape", "Corners",
          "Fields, buttons and list rows.", "radius"),
    Token("RADIUS_LG", "radius_large", "Shape", "Large corners",
          "Panels, cards and dialogs - the outermost surfaces.", "radius"),

    Token("DISPLAY", "font_display", "Type", "Display face",
          "Headings and the brand.", "font"),
    Token("BODY", "font_body", "Type", "Body face",
          "Everything you read.", "font"),
    Token("MONO", "font_mono", "Type", "Monospace face",
          "Addresses, sizes, XML and the terminal.", "font"),
)

BY_FIELD = {token.field: token for token in TOKENS}
GROUPS = tuple(dict.fromkeys(token.group for token in TOKENS))
COLOR_FIELDS = tuple(t.field for t in TOKENS if t.kind == "color")


# -- colour arithmetic, for the shades nobody should have to pick by hand


def parse(color: str) -> tuple[int, int, int]:
    return tuple(int(color[i:i + 2], 16) for i in (1, 3, 5))  # type: ignore[return-value]


def to_hex(rgb: tuple[float, float, float]) -> str:
    return "#" + "".join(f"{max(0, min(255, round(c))):02x}" for c in rgb)


def mix(first: str, second: str, amount: float) -> str:
    """`amount` of the way from the first colour to the second."""
    a, b = parse(first), parse(second)
    return to_hex(tuple(x + (y - x) * amount for x, y in zip(a, b)))


def lighten(color: str, amount: float) -> str:
    return mix(color, "#ffffff", amount)


def darken(color: str, amount: float) -> str:
    return mix(color, "#000000", amount)


def luminance(color: str) -> float:
    """Rough perceived brightness, 0 to 1. Enough to tell dark from light."""
    r, g, b = parse(color)
    return (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255


def derived(values: dict) -> dict[str, str]:
    """The shades worked out from the ones the theme names.

    The amounts here were picked to land on the shades vmmanager's own theme
    used when they were written by hand, and they carry over to any accent:
    a hover is always the accent moved a quarter of the way to white.
    """
    accent, danger, bg = values["accent"], values["danger"], values["bg"]
    return {
        "ACCENT_HOVER": lighten(accent, 0.26),
        "ACCENT_PRESSED": darken(accent, 0.08),
        "DANGER_HOVER": lighten(danger, 0.18),
        "BANNER_BG": mix(bg, danger, 0.14),
        "BANNER_BORDER": mix(bg, danger, 0.36),
        # Text on top of a solid accent fill: whichever of the window and body
        # colours is further from it in brightness. Deciding on the accent alone
        # gets a light theme backwards, because there "dark text" is the body
        # colour and "light text" is the window.
        "ON_ACCENT": _readable_on(accent, bg, values["text"]),
        "ON_DANGER": _readable_on(danger, bg, values["text"]),
    }


def _readable_on(fill: str, first: str, second: str) -> str:
    target = luminance(fill)
    return first if abs(luminance(first) - target) >= abs(
        luminance(second) - target) else second


# -- the theme itself


@dataclass
class Theme:
    name: str
    values: dict = field(default_factory=dict)
    path: Path | None = None
    builtin: bool = False

    def tokens(self) -> dict:
        """Everything the stylesheet template needs, keyed as it expects."""
        out = {token.key: self.values[token.field] for token in TOKENS}
        out.update(derived(self.values))
        return out

    def copy_as(self, name: str) -> Theme:
        return Theme(name=name, values=dict(self.values))


def validate(values: dict) -> dict[str, str]:
    """Complaints keyed by field. Empty means the theme is usable."""
    problems = {}
    for token in TOKENS:
        value = values.get(token.field)
        if value is None:
            problems[token.field] = "missing"
        elif token.kind == "color":
            if not isinstance(value, str) or not HEX.match(value):
                problems[token.field] = "not a #rrggbb colour"
        elif token.kind == "radius":
            if not isinstance(value, int) or isinstance(value, bool):
                problems[token.field] = "not a whole number"
            elif not 0 <= value <= MAX_RADIUS:
                problems[token.field] = f"outside 0 to {MAX_RADIUS}"
        elif token.kind == "font":
            if not isinstance(value, str) or not FONT.match(value):
                problems[token.field] = "not a font family name"
    return problems


def _read(path: Path) -> dict:
    with path.open("rb") as handle:
        return tomllib.load(handle)


def _flatten(document: dict) -> dict:
    """The file groups values under [colors] and friends; TOKENS does not."""
    flat = {}
    for section in ("colors", "radii", "fonts"):
        for key, value in (document.get(section) or {}).items():
            prefix = {"radii": "radius_", "fonts": "font_"}.get(section, "")
            flat[f"{prefix}{key}"] = value
    return flat


def load(path: Path, builtin: bool = False) -> Theme:
    """Read a theme, filling anything it leaves out from the shipped one.

    A file missing a value is far more likely to be one written by hand against
    an older version than a deliberate omission, so it gets the default rather
    than an error. A value that is present and wrong is still an error.
    """
    document = _read(path)
    values = _flatten(document)
    name = document.get("name") or path.stem
    if not builtin:
        base = dict(builtin_theme().values)
        base.update({k: v for k, v in values.items() if k in BY_FIELD})
        values = base
    problems = validate(values)
    if problems:
        raise ValueError(
            f"{path.name}: "
            + ", ".join(f"{k} {why}" for k, why in sorted(problems.items()))
        )
    return Theme(name=str(name)[:48], values=values, path=path, builtin=builtin)


_builtin: Theme | None = None


def builtin_theme() -> Theme:
    """The theme vmmanager ships with, read from its file once."""
    global _builtin
    if _builtin is None:
        _builtin = load(BUILTIN_DIR / f"{BUILTIN_NAME}.toml", builtin=True)
    return _builtin


def slug(name: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
    return cleaned or "theme"


def dump(theme: Theme) -> str:
    """A theme as TOML.

    Written by hand rather than with a library because the standard one only
    reads. Every value has been through validate() by this point, so quoting a
    string is just wrapping it - none of them can contain a quote.
    """
    problems = validate(theme.values)
    if problems:
        raise ValueError(f"refusing to write an invalid theme: {problems}")
    lines = [
        "# A vmmanager theme. Edit it here or in the app's Themes page.",
        f'name = "{theme.name}"' if '"' not in theme.name else 'name = "theme"',
        "",
    ]
    for section, kinds in (("colors", ("color",)), ("radii", ("radius",)),
                           ("fonts", ("font",))):
        lines.append(f"[{section}]")
        for token in TOKENS:
            if token.kind not in kinds:
                continue
            key = token.field.removeprefix("radius_").removeprefix("font_")
            value = theme.values[token.field]
            rendered = str(value) if token.kind == "radius" else f'"{value}"'
            lines.append(f"{key} = {rendered}")
        lines.append("")
    return "\n".join(lines)


def save(theme: Theme) -> Theme:
    """Write a theme to the user's theme directory, and say where it went."""
    if theme.builtin:
        raise ValueError(
            "The theme vmmanager ships with is not editable. Make a copy of it "
            "and change that, so there is always a way back."
        )
    USER_DIR.mkdir(parents=True, exist_ok=True)
    path = theme.path or USER_DIR / f"{slug(theme.name)}.toml"
    path.write_text(dump(theme))
    return replace(theme, path=path)


def delete(theme: Theme) -> None:
    if theme.builtin:
        raise ValueError("The theme vmmanager ships with cannot be deleted.")
    if theme.path is not None:
        theme.path.unlink(missing_ok=True)


def available() -> list[Theme]:
    """The shipped theme first, then whatever is in the user's directory.

    A file that will not parse is logged and skipped: one bad theme should not
    stop you picking a good one, and the Themes page is where you would go to
    fix it.
    """
    themes = [builtin_theme()]
    if USER_DIR.is_dir():
        for path in sorted(USER_DIR.glob("*.toml")):
            try:
                themes.append(load(path))
            except (OSError, ValueError, tomllib.TOMLDecodeError) as exc:
                log.warning("skipping theme %s: %s", path.name, exc)
    return themes


def by_name(name: str) -> Theme | None:
    return next((t for t in available() if t.name == name), None)
