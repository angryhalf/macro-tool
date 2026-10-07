"""Light/dark theming: palettes, QSS stylesheets and application-wide helpers.

The app uses a *soft* dark mode: instead of swapping Qt's system palette
(which would give native widgets jarring dark-on-dark details), every colour
lives in a :class:`Theme` dataclass and is applied through a global
stylesheet.  ``QApplication.style().polish(w)`` re-resolves the sheet on a
single widget, so toggling the theme takes effect instantly everywhere.
"""

from __future__ import annotations

from dataclasses import dataclass, replace as _dc_replace

from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication


@dataclass(frozen=True)
class Theme:
    """Named colour scheme; the values feed both the stylesheet and widgets."""

    name: str
    window: str
    surface: str      # cards / group boxes
    sidebar: str
    text: str
    muted: str        # secondary text / hints
    accent: str       # selection + primary buttons
    accent_text: str  # readable label colour on top of *accent*
    border: str
    hover: str
    field: str        # input backgrounds (tables, spin boxes, line edits)
    danger: str       # stop button background
    danger_text: str


#: The emergency-stop button keeps this exact look in both themes.
STOP_BUTTON_QSS = (
    "QPushButton { background-color:#c0392b; color:white;"
    " font-weight:bold; padding:8px 14px; border:none; border-radius:6px; }"
    " QPushButton:hover { background-color:#a93226; }"
)

LIGHT = Theme(
    name="light",
    window="#f5f6f8",
    surface="#ffffff",
    sidebar="#e9ecf2",
    text="#1f2430",
    muted="#5f6b7d",
    accent="#3d7bfd",
    accent_text="#ffffff",
    border="#d6dae2",
    hover="#dde3ee",
    field="#ffffff",
    danger="#c0392b",
    danger_text="#ffffff",
)

DARK = Theme(
    name="dark",
    window="#1b1e24",
    surface="#232830",
    sidebar="#15181d",
    text="#e8eaf0",
    muted="#9aa4b5",
    accent="#4f8cff",
    accent_text="#ffffff",
    border="#39414e",
    hover="#2c333d",
    field="#1e2229",
    danger="#c0392b",
    danger_text="#ffffff",
)

THEMES: dict[str, Theme] = {LIGHT.name: LIGHT, DARK.name: DARK}

DEFAULT_THEME = LIGHT.name


def _readable_on(hex_color: str) -> str:
    """Pick black or white text for a background of *hex_color* (WCAG-ish)."""
    c = QColor(hex_color)
    luminance = 0.299 * c.red() + 0.587 * c.green() + 0.114 * c.blue()
    return "#111318" if luminance > 150 else "#ffffff"


def get_theme(name: str, accent: str | None = None) -> Theme:
    """Return the theme called *name*, falling back to the light theme.

    When *accent* is a valid ``#rrggbb`` colour it overrides the palette's
    default accent (the label colour on top of it is recomputed so buttons
    and selections stay readable no matter what the user picked).
    """
    t = THEMES.get((name or "").strip().lower(), LIGHT)
    if accent:
        probe = QColor(accent)
        if probe.isValid():
            new_accent = probe.name(QColor.NameFormat.HexRgb)
            t = _dc_replace(t, accent=new_accent, accent_text=_readable_on(new_accent))
    return t


def build_stylesheet(t: Theme) -> str:
    """Global QSS for one theme.

    Keep this sheet free of hard-coded colours -- everything must come from
    *t* so the two themes stay symmetric.  Widgets that need per-theme
    colours in code (e.g. the sidebar nav buttons) read the attributes of
    :class:`Theme` directly.
    """
    return f"""
/* ---- windows & containers -------------------------------------- */
QMainWindow, QDialog {{ background-color: {t.window}; color: {t.text}; }}
QWidget {{ color: {t.text}; font-size: 13px; background-color: transparent; }}
QToolTip {{
    background-color: {t.surface}; color: {t.text};
    border: 1px solid {t.border}; padding: 4px 6px; border-radius: 4px;
}}

/* ---- inputs ----------------------------------------------------- */
QLineEdit, QTextEdit, QPlainTextEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
    background-color: {t.field}; color: {t.text};
    border: 1px solid {t.border}; border-radius: 6px; padding: 4px 6px;
}}
QSpinBox::up-button, QSpinBox::down-button,
QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{
    background: {t.surface}; width: 16px; border: none;
}}
QComboBox::drop-down {{ border: none; width: 20px; }}
QComboBox QAbstractItemView {{
    background-color: {t.field}; color: {t.text};
    border: 1px solid {t.border};
    selection-background-color: {t.accent}; selection-color: {t.accent_text};
    outline: none;
}}
QCheckBox, QRadioButton {{ spacing: 6px; }}
QCheckBox::indicator, QRadioButton::indicator {{ width: 16px; height: 16px; }}
QCheckBox::indicator:unchecked {{
    border: 1px solid {t.border}; border-radius: 4px; background: {t.field};
}}
QCheckBox::indicator:checked {{
    border: 1px solid {t.accent}; border-radius: 4px; background: {t.accent};
}}
QRadioButton::indicator:unchecked {{
    border: 1px solid {t.border}; border-radius: 8px; background: {t.field};
}}
QRadioButton::indicator:checked {{
    border: 1px solid {t.accent}; border-radius: 8px; background: {t.accent};
}}

/* ---- buttons ---------------------------------------------------- */
QPushButton {{
    background-color: {t.surface}; color: {t.text};
    border: 1px solid {t.border}; border-radius: 6px; padding: 5px 12px;
}}
QPushButton:hover {{ background-color: {t.hover}; border-color: {t.accent}; }}
QPushButton:pressed {{ background-color: {t.accent}; color: {t.accent_text}; }}
QPushButton:disabled {{ color: {t.muted}; background-color: {t.window}; }}

/* ---- group boxes ------------------------------------------------ */
QGroupBox {{
    background-color: {t.surface};
    border: 1px solid {t.border}; border-radius: 8px;
    margin-top: 14px; padding-top: 8px; font-weight: bold;
}}
QGroupBox::title {{
    subcontrol-origin: margin; left: 10px;
    padding: 0 4px; color: {t.muted};
}}

/* ---- tables / trees / lists ----------------------------------- */
QTableView, QTreeView, QListWidget {{
    background-color: {t.field}; alternate-background-color: {t.surface};
    color: {t.text}; border: 1px solid {t.border}; border-radius: 8px;
    gridline-color: {t.border}; selection-background-color: {t.accent};
    selection-color: {t.accent_text}; outline: none;
}}
QHeaderView::section {{
    background-color: {t.sidebar}; color: {t.text};
    border: none; border-bottom: 1px solid {t.border}; padding: 5px;
}}
QTableCornerButton::section {{ background-color: {t.sidebar}; border: none; }}

/* ---- tabs (editors still use QTabWidget internally) ------------- */
QTabBar::tab {{
    background: transparent; color: {t.muted};
    padding: 6px 14px; border-bottom: 2px solid transparent;
}}
QTabBar::tab:selected {{ color: {t.text}; border-bottom: 2px solid {t.accent}; }}
QTabWidget::pane {{ border: 1px solid {t.border}; border-radius: 6px; }}

/* ---- status bar -------------------------------------------------- */
QStatusBar {{
    background-color: {t.sidebar}; color: {t.muted};
    border-top: 1px solid {t.border};
}}
QStatusBar::item {{ border: none; }}

/* ---- scrollbars -------------------------------------------------- */
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{
    background: {t.border}; border-radius: 4px; min-height: 24px;
}}
QScrollBar::handle:vertical:hover {{ background: {t.muted}; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{
    background: {t.border}; border-radius: 4px; min-width: 24px;
}}
QScrollBar::handle:horizontal:hover {{ background: {t.muted}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
"""


def sidebar_button_qss(t: Theme) -> str:
    """Stylesheet for the flat navigation buttons inside the sidebar."""
    return f"""
QPushButton {{
    background-color: transparent; color: {t.text};
    border: none; border-left: 3px solid transparent;
    border-radius: 0px; padding: 10px 14px; text-align: left; font-size: 14px;
}}
QPushButton:hover {{ background-color: {t.hover}; }}
QPushButton:checked {{
    background-color: {t.accent}; color: {t.accent_text};
    border-left: 3px solid {t.accent}; font-weight: bold;
}}
"""


def apply_theme(app: QApplication, name: str, accent: str | None = None) -> None:
    """Set *name*'s global stylesheet on the running application."""
    app.setStyleSheet(build_stylesheet(get_theme(name, accent)))
