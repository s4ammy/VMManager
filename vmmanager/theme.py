"""The application stylesheet, and the tokens it is built from.

The values themselves live in a theme file - see core/themes.py. This module
holds whichever theme is in use, as module-level names, because that is how the
rest of the app reads them: `theme.ACCENT` in a painter, `theme.TEXT_DIM` in a
label. Switching theme rebinds those names and rebuilds QSS, so code that reads
them at paint time picks up the change with no work of its own.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtGui import QFontDatabase

from .core.themes import TOKENS, Theme, builtin_theme, derived

FONT_DIR = Path(__file__).parent / "assets" / "fonts"
ICON_DIR = Path(__file__).parent / "assets" / "icons"


# Qt draws subcontrol arrows from an image, not CSS borders, the
# border-triangle trick paints the box instead, which is where the little
# squares came from. Absolute paths, so the sheet works from any cwd.
def _icon(name: str) -> str:
    return ICON_DIR.joinpath(f"{name}.svg").as_posix()


_ICONS = {
    f"ICON_{name.upper().replace('-', '_')}": _icon(name)
    for name in (
        "check", "chevron-down", "chevron-down-hover", "chevron-left",
        "chevron-left-hover", "chevron-right", "chevron-right-hover",
        "chevron-up", "chevron-up-hover",
    )
}


# The tokens of whichever theme is in use. Declared here so that reading
# `theme.ACCENT_DIM` in some painter leads back to something you can find;
# apply() rebinds them all when the theme changes.
active: Theme = builtin_theme()
_START = active.tokens()

BG = _START["BG"]
BG_RAISED = _START["BG_RAISED"]
BG_INSET = _START["BG_INSET"]
BORDER = _START["BORDER"]
BORDER_BRIGHT = _START["BORDER_BRIGHT"]
TEXT = _START["TEXT"]
TEXT_DIM = _START["TEXT_DIM"]
TEXT_FAINT = _START["TEXT_FAINT"]
ACCENT = _START["ACCENT"]
ACCENT_DIM = _START["ACCENT_DIM"]
OK = _START["OK"]
WARN = _START["WARN"]
DANGER = _START["DANGER"]

# Corner radii. Kept small deliberately: heavily rounded panels read as soft,
# and this is a tool for machines. RADIUS is the default for anything with a
# border; the other two are for what sits inside it and what contains it.
RADIUS_SM = _START["RADIUS_SM"]   # chips, badges, indicator marks
RADIUS = _START["RADIUS"]         # inputs, buttons, list rows
RADIUS_LG = _START["RADIUS_LG"]   # panels, cards, dialogs, outermost surfaces

DISPLAY = _START["DISPLAY"]
BODY = _START["BODY"]
MONO = _START["MONO"]

# Worked out from the above rather than chosen: see themes.derived.
ACCENT_HOVER = _START["ACCENT_HOVER"]
ACCENT_PRESSED = _START["ACCENT_PRESSED"]
DANGER_HOVER = _START["DANGER_HOVER"]
BANNER_BG = _START["BANNER_BG"]
BANNER_BORDER = _START["BANNER_BORDER"]
ON_ACCENT = _START["ON_ACCENT"]
ON_DANGER = _START["ON_DANGER"]

STATE_COLORS = {
    "running": OK,
    "paused": WARN,
    "suspended": WARN,
    "shutting-down": WARN,
    "blocked": DANGER,
    "crashed": DANGER,
}

QSS = ""  # built at the bottom of this module, once _TEMPLATE exists


def build_qss(theme: Theme) -> str:
    """The stylesheet for a theme.

    Every value has been through themes.validate() before it gets here, so
    nothing in the mapping can close a rule and start another one.
    """
    return _TEMPLATE.format(**theme.tokens(), **_ICONS)


def state_color(state: str) -> str:
    return STATE_COLORS.get(state, TEXT_FAINT)


# Which of the state colours a machine's state falls under. Enough for a rule
# to pick the colour, so that changing theme repaints the dot rather than
# leaving it the old colour until the next poll.
_STATE_CLASSES = {
    "running": "Ok",
    "paused": "Warn",
    "suspended": "Warn",
    "shutting-down": "Warn",
    "blocked": "Bad",
    "crashed": "Bad",
}


def state_class(state: str) -> str:
    return _STATE_CLASSES.get(state, "Faint")


def set_class(widget, *names: str) -> None:
    """Give a widget a stylesheet class, and make Qt notice.

    Qt resolves a widget's rules once and caches the result, so changing the
    property it matched on has no effect until the style is asked again.
    """
    widget.setProperty("class", " ".join(n for n in names if n))
    style = widget.style()
    style.unpolish(widget)
    style.polish(widget)


def load_fonts() -> None:
    for ttf in FONT_DIR.glob("*.ttf"):
        QFontDatabase.addApplicationFont(str(ttf))


def apply(theme: Theme) -> str:
    """Make this the theme these module-level names describe.

    Returns the stylesheet, which the caller hands to the QApplication. Nothing
    here touches Qt: a theme can be applied in a test with no widgets at all.
    """
    global QSS, STATE_COLORS, active
    values = theme.tokens()
    for token in TOKENS:
        globals()[token.key] = values[token.key]
    for key, value in derived(theme.values).items():
        globals()[key] = value
    STATE_COLORS = {
        "running": values["OK"],
        "paused": values["WARN"],
        "suspended": values["WARN"],
        "shutting-down": values["WARN"],
        "blocked": values["DANGER"],
        "crashed": values["DANGER"],
    }
    active = theme
    QSS = build_qss(theme)
    return QSS


_TEMPLATE = """
QWidget {{
    background: transparent;
    color: {TEXT};
    font-family: "{BODY}";
    font-size: 13px;
}}

QMainWindow, #Root {{
    background: {BG};
}}

QToolTip {{
    background: {BG_RAISED};
    color: {TEXT};
    border: 1px solid {BORDER_BRIGHT};
    padding: 4px 8px;
}}

/* ---------- sidebar ---------- */

#Sidebar {{
    background: {BG_RAISED};
    border-right: 1px solid {BORDER};
}}

#BrandName {{
    font-family: "{DISPLAY}";
    font-size: 17px;
    font-weight: 600;
    color: {TEXT};
}}

.NavButton {{
    text-align: left;
    padding: 9px 14px;
    border-radius: {RADIUS}px;
    color: {TEXT_DIM};
    font-weight: 500;
    border: none;
}}

.NavButton:hover:enabled {{
    background: {ACCENT_DIM};
    color: {TEXT};
}}

.NavButton[active="true"] {{
    background: {ACCENT_DIM};
    color: {ACCENT};
}}

.NavButton:disabled {{
    color: {TEXT_FAINT};
}}

#HostPanel {{
    background: {BG_INSET};
    border: 1px solid {BORDER};
    border-radius: {RADIUS}px;
}}

#HostPanelLabel {{
    font-family: "{MONO}";
    font-size: 10px;
    letter-spacing: 2px;
    color: {TEXT_FAINT};
}}

.HostKey {{
    font-family: "{MONO}";
    font-size: 11px;
    color: {TEXT_FAINT};
}}

.HostVal {{
    font-family: "{MONO}";
    font-size: 11px;
    color: {TEXT_DIM};
}}

/* ---------- page ---------- */

#PageTitle {{
    font-family: "{DISPLAY}";
    font-size: 24px;
    font-weight: 600;
}}

#PageSub {{
    font-family: "{MONO}";
    font-size: 11px;
    color: {TEXT_DIM};
}}

#ErrorBanner {{
    background: {BANNER_BG};
    border: 1px solid {BANNER_BORDER};
    border-radius: {RADIUS}px;
    color: {TEXT};
    padding: 10px;
}}

#EmptyState {{
    border: 1px dashed {BORDER_BRIGHT};
    border-radius: {RADIUS_LG}px;
}}

#EmptyTitle {{
    font-family: "{DISPLAY}";
    font-size: 15px;
}}

#EmptyBody {{
    color: {TEXT_DIM};
}}

/* ---------- vm cards ---------- */

#VmCard {{
    background: {BG_RAISED};
    border: 1px solid {BORDER};
    border-radius: {RADIUS_LG}px;
}}

#VmCard:hover {{
    border: 1px solid {BORDER_BRIGHT};
}}

#VmCard[selected="true"] {{
    border: 1px solid {ACCENT};
    background: {ACCENT_DIM};
}}

#VmThumb {{
    background: {BG_INSET};
    border: 1px solid {BORDER};
    border-radius: {RADIUS}px;
}}

#SpecChip {{
    background: {BG_INSET};
    border: 1px solid {BORDER};
    border-radius: {RADIUS}px;
}}

#HwBadge {{
    font-family: "{MONO}";
    font-size: 9px;
    letter-spacing: 2px;
    color: {ON_ACCENT};
    background: {ACCENT};
    border-radius: {RADIUS_SM}px;
    padding: 3px 8px;
}}

.SwitchTab {{
    background: transparent;
    color: {TEXT_FAINT};
    font-family: "{MONO}";
    font-size: 10px;
    letter-spacing: 1px;
    border: none;
    border-bottom: 2px solid transparent;
    padding: 4px 8px;
}}

.SwitchTab:hover {{
    color: {TEXT};
}}

.SwitchTab[active="true"] {{
    color: {ACCENT};
    border-bottom: 2px solid {ACCENT};
}}

#VmName {{
    font-family: "{DISPLAY}";
    font-size: 15px;
    font-weight: 500;
}}

#VmState {{
    font-family: "{MONO}";
    font-size: 10px;
    letter-spacing: 1px;
}}

.StatKey {{
    font-family: "{MONO}";
    font-size: 9px;
    letter-spacing: 1px;
    color: {TEXT_FAINT};
}}

.StatVal {{
    font-family: "{MONO}";
    font-size: 12px;
    color: {TEXT_DIM};
}}

/* ---------- buttons ---------- */

.PrimaryButton {{
    background: {ACCENT};
    color: {ON_ACCENT};
    font-weight: 600;
    border: none;
    border-radius: {RADIUS_LG}px;
    min-height: 28px;
    padding: 0px 16px;
}}

.PrimaryButton:hover {{
    background: {ACCENT_HOVER};
}}

.PrimaryButton:pressed {{
    background: {ACCENT_PRESSED};
}}

.GhostButton {{
    background: transparent;
    color: {TEXT_DIM};
    border: 1px solid {BORDER_BRIGHT};
    border-radius: {RADIUS_LG}px;
    min-height: 26px;
    padding: 0px 14px;
}}

.GhostButton:hover {{
    color: {TEXT};
    border-color: {ACCENT};
}}

.PrimaryButton:disabled, .GhostButton:disabled {{
    background: {BG_RAISED};
    color: {TEXT_FAINT};
    border-color: {BORDER};
}}

/* ---------- detail page ---------- */

#BackButton {{
    color: {TEXT_DIM};
    border: none;
    border-radius: {RADIUS}px;
    padding: 6px 10px;
    font-family: "{MONO}";
    font-size: 12px;
}}

#BackButton:hover {{
    background: {ACCENT_DIM};
    color: {ACCENT};
}}

#DetailName {{
    font-family: "{DISPLAY}";
    font-size: 22px;
    font-weight: 600;
}}

#DetailState {{
    font-family: "{MONO}";
    font-size: 11px;
    letter-spacing: 1px;
}}

.IconButton {{
    background: transparent;
    color: {TEXT_DIM};
    border: 1px solid {BORDER_BRIGHT};
    border-radius: {RADIUS_LG}px;
    min-height: 26px;
    padding: 0px 12px;
}}

.IconButton:hover {{
    color: {TEXT};
    border-color: {ACCENT};
}}

/* The tab strip reads as one hairline-anchored bar: the pane's top border
   runs the full width, and the selected tab overdraws it in accent. */

QTabWidget::pane {{
    border: none;
    border-top: 1px solid {BORDER};
    margin-top: 0px;
    padding-top: 12px;
}}

QTabBar {{
    qproperty-drawBase: 0;
}}

QTabBar::tab {{
    background: transparent;
    color: {TEXT_DIM};
    font-family: "{MONO}";
    font-size: 11px;
    letter-spacing: 0.5px;
    padding: 8px 9px;
    border-bottom: 2px solid transparent;
    margin-right: 1px;
}}

QTabBar::tab:hover {{
    color: {TEXT};
}}

QTabBar::tab:selected {{
    color: {ACCENT};
    border-bottom: 2px solid {ACCENT};
}}

/* Scroll arrows, for windows too narrow to show every tab. Qt draws these as
   plain tool buttons, which look nothing like the rest of the app unless they
   are styled explicitly - chevrons built from borders, same trick as the
   combo box and spin box arrows. */

QTabBar::scroller {{
    width: 38px;
}}

QTabBar QToolButton {{
    background: {BG};
    border: none;
    border-bottom: 1px solid {BORDER};
    width: 18px;
    margin: 0px;
}}

QTabBar QToolButton:hover {{
    background: {ACCENT_DIM};
}}

QTabBar QToolButton:disabled {{
    background: {BG};
}}

QTabBar QToolButton::right-arrow {{
    image: url({ICON_CHEVRON_RIGHT});
    width: 14px;
    height: 14px;
}}

QTabBar QToolButton::left-arrow {{
    image: url({ICON_CHEVRON_LEFT});
    width: 14px;
    height: 14px;
}}

QTabBar QToolButton::right-arrow:hover {{
    image: url({ICON_CHEVRON_RIGHT_HOVER});
}}

QTabBar QToolButton::left-arrow:hover {{
    image: url({ICON_CHEVRON_LEFT_HOVER});
}}

.ChartCard {{
    background: {BG_RAISED};
    border: 1px solid {BORDER};
    border-radius: {RADIUS_LG}px;
}}

.ChartTitle {{
    font-family: "{MONO}";
    font-size: 10px;
    letter-spacing: 2px;
    color: {TEXT_FAINT};
}}

.ChartValue {{
    font-family: "{MONO}";
    font-size: 16px;
    color: {TEXT};
}}

.SectionTitle {{
    font-family: "{DISPLAY}";
    font-size: 15px;
    font-weight: 500;
    color: {TEXT};
}}

/* ---------- tables & trees ---------- */

QTreeWidget {{
    background: {BG_RAISED};
    border: 1px solid {BORDER};
    border-radius: {RADIUS_LG}px;
    font-size: 12px;
    outline: none;
}}

QTreeWidget::item {{
    padding: 5px 8px;
    color: {TEXT_DIM};
}}

QTreeWidget::item:selected {{
    background: {ACCENT_DIM};
    color: {TEXT};
}}

QTableWidget {{
    background: {BG_RAISED};
    border: 1px solid {BORDER};
    border-radius: {RADIUS_LG}px;
    gridline-color: transparent;
    font-size: 12px;
}}

QTableWidget::item {{
    padding: 6px 10px;
    border-bottom: 1px solid {BORDER};
    color: {TEXT_DIM};
}}

QTableWidget::item:selected {{
    background: {ACCENT_DIM};
    color: {TEXT};
}}

QHeaderView::section {{
    background: {BG_RAISED};
    color: {TEXT_FAINT};
    font-family: "{MONO}";
    font-size: 10px;
    letter-spacing: 1px;
    border: none;
    border-bottom: 1px solid {BORDER_BRIGHT};
    padding: 8px 10px;
    text-align: left;
}}

QTableCornerButton::section {{
    background: {BG_RAISED};
    border: none;
}}

/* ---------- inputs ---------- */

QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QPlainTextEdit {{
    background: {BG_INSET};
    border: 1px solid {BORDER_BRIGHT};
    border-radius: {RADIUS}px;
    padding: 6px 10px;
    min-height: 20px;
    color: {TEXT};
    selection-background-color: {ACCENT_DIM};
    selection-color: {TEXT};
}}

QSpinBox, QDoubleSpinBox {{
    padding-right: 26px;
}}

QLineEdit:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled,
QComboBox:disabled, QPlainTextEdit:disabled {{
    background: {BG};
    color: {TEXT_FAINT};
    border-color: {BORDER};
}}

QCheckBox:disabled, QRadioButton:disabled {{
    color: {TEXT_FAINT};
}}

QCheckBox::indicator:disabled, QRadioButton::indicator:disabled {{
    border-color: {BORDER};
    background: {BG};
}}

QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus,
QPlainTextEdit:focus {{
    border-color: {ACCENT};
}}

QPlainTextEdit {{
    font-family: "{MONO}";
    font-size: 12px;
}}

/* Pin the steppers to the corners and give each a stated height. Left to
   itself Qt splits whatever is left after padding, which was too little for the
   arrow image and clipped the bottom off the down chevron. */
QSpinBox::up-button, QSpinBox::down-button,
QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{
    subcontrol-origin: border;
    background: transparent;
    border: none;
    width: 18px;
    height: 15px;
    margin-right: 4px;
}}

QSpinBox::up-button, QDoubleSpinBox::up-button {{
    subcontrol-position: top right;
    margin-top: 2px;
}}

QSpinBox::down-button, QDoubleSpinBox::down-button {{
    subcontrol-position: bottom right;
    margin-bottom: 2px;
}}

QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {{
    image: url({ICON_CHEVRON_UP});
    width: 11px;
    height: 11px;
}}

QSpinBox::up-arrow:hover, QDoubleSpinBox::up-arrow:hover {{
    image: url({ICON_CHEVRON_UP_HOVER});
}}

QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {{
    image: url({ICON_CHEVRON_DOWN});
    width: 11px;
    height: 11px;
}}

QSpinBox::down-arrow:hover, QDoubleSpinBox::down-arrow:hover {{
    image: url({ICON_CHEVRON_DOWN_HOVER});
}}

QComboBox::drop-down {{
    border: none;
    width: 24px;
}}

QComboBox::down-arrow {{
    image: url({ICON_CHEVRON_DOWN});
    width: 14px;
    height: 14px;
    margin-right: 6px;
}}

QComboBox::down-arrow:hover {{
    image: url({ICON_CHEVRON_DOWN_HOVER});
}}

QComboBox QAbstractItemView {{
    background: {BG_RAISED};
    border: 1px solid {BORDER_BRIGHT};
    border-radius: {RADIUS}px;
    selection-background-color: {ACCENT_DIM};
    selection-color: {TEXT};
    outline: none;
}}

QTreeView::branch:has-children:closed,
QTreeWidget::branch:has-children:closed {{
    image: url({ICON_CHEVRON_RIGHT});
}}

QTreeView::branch:has-children:open,
QTreeWidget::branch:has-children:open {{
    image: url({ICON_CHEVRON_DOWN});
}}

/* Without a rule of its own this falls back to the platform's bright blue. */
QProgressBar {{
    background: {BG_INSET};
    border: 1px solid {BORDER};
    border-radius: {RADIUS}px;
    height: 12px;
    text-align: center;
    color: {ON_ACCENT};
    font-family: "{MONO}";
    font-size: 10px;
}}

QProgressBar::chunk {{
    background: {ACCENT};
    border-radius: {RADIUS}px;
}}

/* The vertical ones were styled and these weren't, so they turned up with
   platform chrome and full-size arrow buttons. */
QScrollBar:horizontal {{
    background: transparent;
    height: 10px;
    margin: 2px;
}}

QScrollBar::handle:horizontal {{
    background: {BORDER_BRIGHT};
    border-radius: {RADIUS_SM}px;
    min-width: 30px;
}}

QScrollBar::handle:horizontal:hover {{
    background: {ACCENT};
}}

QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
    width: 0;
}}

QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{
    background: transparent;
}}

QCheckBox, QRadioButton {{
    spacing: 8px;
    color: {TEXT_DIM};
}}

QRadioButton::indicator {{
    width: 15px;
    height: 15px;
    border: 1px solid {BORDER_BRIGHT};
    border-radius: {RADIUS}px;
    background: {BG_INSET};
}}

QRadioButton::indicator:checked {{
    background: {ACCENT};
    border: 4px solid {BG_INSET};
    width: 9px;
    height: 9px;
}}

QListWidget {{
    background: {BG_RAISED};
    border: 1px solid {BORDER};
    border-radius: {RADIUS}px;
    padding: 4px;
    font-family: "{MONO}";
    font-size: 12px;
}}

QListWidget::item {{
    padding: 6px 8px;
    border-radius: {RADIUS}px;
    color: {TEXT_DIM};
}}

QListWidget::item:selected {{
    background: {ACCENT_DIM};
    color: {TEXT};
}}

QSlider::groove:horizontal {{
    height: 4px;
    background: {BG_INSET};
    border-radius: {RADIUS_SM}px;
}}

QSlider::handle:horizontal {{
    width: 14px;
    height: 14px;
    margin: -5px 0;
    border-radius: {RADIUS}px;
    background: {ACCENT};
}}

QSlider::handle:horizontal:disabled {{
    background: {BORDER_BRIGHT};
}}

QSlider::sub-page:horizontal {{
    background: {ACCENT_DIM};
    border-radius: {RADIUS_SM}px;
}}

QCheckBox::indicator {{
    width: 16px;
    height: 16px;
    border: 1px solid {BORDER_BRIGHT};
    border-radius: {RADIUS_SM}px;
    background: {BG_INSET};
}}

QCheckBox::indicator:checked {{
    background: {ACCENT};
    border-color: {ACCENT};
    image: url({ICON_CHECK});
}}

/* ---------- menus & dialogs ---------- */

QMenu {{
    background: {BG_RAISED};
    border: 1px solid {BORDER_BRIGHT};
    border-radius: {RADIUS}px;
    padding: 6px;
}}

QMenu::item {{
    padding: 7px 24px 7px 14px;
    border-radius: {RADIUS}px;
    color: {TEXT_DIM};
}}

QMenu::item:selected {{
    background: {ACCENT_DIM};
    color: {TEXT};
}}

QMenu::item:disabled {{
    color: {TEXT_FAINT};
}}

QMenu::separator {{
    height: 1px;
    background: {BORDER};
    margin: 5px 8px;
}}

QDialog {{
    background: {BG};
}}

#DialogTitle {{
    font-family: "{DISPLAY}";
    font-size: 17px;
    font-weight: 600;
}}

.FieldLabel {{
    font-family: "{MONO}";
    font-size: 11px;
    letter-spacing: 1px;
    color: {TEXT_DIM};
}}

/* The explanatory line under a dialog heading. A rule rather than a colour set
   on each label, so a theme change repaints it along with everything else. */
.Dim {{
    color: {TEXT_DIM};
}}

.Faint {{
    color: {TEXT_FAINT};
}}

.Ok {{
    color: {OK};
}}

.Warn {{
    color: {WARN};
}}

.Bad {{
    color: {DANGER};
}}

.Accent {{
    color: {ACCENT};
}}

.Body {{
    color: {TEXT};
}}

#ConsoleWindow {{
    background: {BG};
}}

.DangerButton {{
    background: {DANGER};
    color: {ON_DANGER};
    font-weight: 600;
    border: none;
    border-radius: {RADIUS_LG}px;
    min-height: 28px;
    padding: 0px 16px;
}}

.DangerButton:hover {{
    background: {DANGER_HOVER};
}}

/* ---------- console ---------- */

#ConsoleView {{
    background: {BG_INSET};
    border: 1px solid {BORDER};
    border-radius: {RADIUS_LG}px;
}}

#ConsoleHint {{
    color: {TEXT_FAINT};
    font-family: "{MONO}";
    font-size: 11px;
}}

/* The "?" beside a faceplate field. The explanation lives in its tooltip:
   spelling every one of them out in prose made a device's properties
   scroll off the panel, which is the opposite of the point. */
#FieldHint {{
    color: {TEXT_FAINT};
    background: {BG_INSET};
    border: 1px solid {BORDER};
    border-radius: {RADIUS_SM}px;
    font-family: "{MONO}";
    font-size: 11px;
    font-weight: 600;
}}

#FieldHint:hover {{
    color: {ACCENT};
    border-color: {ACCENT};
}}

/* ---------- scrollbars ---------- */

QScrollArea {{
    border: none;
}}

QScrollBar:vertical {{
    background: transparent;
    width: 10px;
    margin: 2px;
}}

QScrollBar::handle:vertical {{
    background: {BORDER_BRIGHT};
    border-radius: {RADIUS_SM}px;
    min-height: 30px;
}}

QScrollBar::handle:vertical:hover {{
    background: {ACCENT};
}}

QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0;
}}

QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
    background: transparent;
}}
"""

QSS = apply(active)
