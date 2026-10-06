"""Central UI theme: a modern design system (light + dark) applied app-wide via QSS.

Everything visual lives here — the colour palettes, typography, spacing and
the stylesheet installed on the :class:`QApplication` — so widgets can style
themselves consistently through dynamic properties instead of scattered
inline ``setStyleSheet`` calls.

Dark mode is a first-class palette: call :func:`set_mode` (or flip with
:func:`toggle_mode`) at runtime and every widget re-styles instantly because
both the QSS and the Qt palette are regenerated from the active
:class:`Palette`.

Dynamic properties understood by the stylesheet (set with
``widget.setProperty(name, value)``):

* ``role``  on QPushButton / QLabel badges — one of ``primary``, ``danger``,
  ``success``, ``ghost``, ``badge-running``, ``badge-disabled``,
  ``badge-muted``.
* ``hint="true"`` on QLabel — muted secondary text.
* ``card="true"`` on QWidget — rounded elevated card panel.
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import QApplication, QLabel, QPushButton

MODE_LIGHT = "light"
MODE_DARK = "dark"

RADIUS = "10px"


@dataclass(frozen=True)
class Palette:
    """All colours the stylesheet needs for one appearance mode."""

    accent: str
    accent_hover: str
    accent_pressed: str
    danger: str
    danger_hover: str
    success: str
    success_hover: str
    surface: str
    background: str
    border: str
    text: str
    muted: str
    selection: str
    hover: str        # generic button/row hover fill
    pressed: str      # generic button press fill
    disabled_fill: str
    disabled_text: str
    header_fill: str  # table header / corner button background
    alt_row: str      # zebra striping in tables
    badge_success_bg: str
    badge_danger_bg: str
    badge_muted_bg: str
    tooltip_bg: str
    tooltip_text: str


LIGHT = Palette(
    accent="#4f6df5",
    accent_hover="#3d5ae8",
    accent_pressed="#2f49c9",
    danger="#e5484d",
    danger_hover="#d93a40",
    success="#30a46c",
    success_hover="#2b9160",
    surface="#ffffff",
    background="#f3f5fa",
    border="#dfe3ec",
    text="#1c2333",
    muted="#697386",
    selection="#e8edff",
    hover="#f6f8fc",
    pressed="#eef1f8",
    disabled_fill="#f2f4f8",
    disabled_text="#a6adbd",
    header_fill="#f4f6fb",
    alt_row="#f8fafd",
    badge_success_bg="#e6f6ee",
    badge_danger_bg="#fdecec",
    badge_muted_bg="#eef1f6",
    tooltip_bg="#1c2333",
    tooltip_text="#ffffff",
)

DARK = Palette(
    accent="#6d84ff",
    accent_hover="#8195ff",
    accent_pressed="#5a71ec",
    danger="#f2555a",
    danger_hover="#ff6b70",
    success="#4cc38a",
    success_hover="#3aa876",
    surface="#1e232e",
    background="#14171e",
    border="#2c3342",
    text="#e6eaf2",
    muted="#9aa4b8",
    selection="#2b3550",
    hover="#262c3a",
    pressed="#2f3646",
    disabled_fill="#232937",
    disabled_text="#5f6879",
    header_fill="#232937",
    alt_row="#222835",
    badge_success_bg="#1d3b2f",
    badge_danger_bg="#47232a",
    badge_muted_bg="#2a3040",
    tooltip_bg="#0c0f14",
    tooltip_text="#e6eaf2",
)

#: Module-level colour names mirror the *light* palette so existing imports
#: (``MUTED`` etc.) keep working unchanged.
ACCENT = LIGHT.accent
ACCENT_HOVER = LIGHT.accent_hover
ACCENT_PRESSED = LIGHT.accent_pressed
DANGER = LIGHT.danger
DANGER_HOVER = LIGHT.danger_hover
SUCCESS = LIGHT.success
SURFACE = LIGHT.surface
BACKGROUND = LIGHT.background
BORDER = LIGHT.border
TEXT = LIGHT.text
MUTED = LIGHT.muted
SELECTION = LIGHT.selection

_state: dict[str, str] = {"mode": MODE_LIGHT}


def current_mode() -> str:
    """Return the active appearance: ``"light"`` or ``"dark"``."""
    return _state["mode"]


def current_palette() -> Palette:
    """Return the :class:`Palette` belonging to the active mode."""
    return DARK if _state["mode"] == MODE_DARK else LIGHT


# ----------------------------------------------------------------------
# Stylesheet generation
# ----------------------------------------------------------------------


def build_stylesheet(p: Palette) -> str:
    """Render the full application stylesheet for palette *p*."""
    return f"""
/* ---------------- global typography ---------------- */
QWidget {{
    color: {p.text};
    font-family: "Segoe UI", "Inter", "SF Pro Text", "Helvetica Neue",
                 "Noto Sans", Arial, sans-serif;
    font-size: 13px;
}}

QMainWindow, QDialog, QWidget#pageRoot {{
    background: {p.background};
}}

/* ---------------- cards / panels ---------------- */
QWidget[card="true"] {{
    background: {p.surface};
    border: 1px solid {p.border};
    border-radius: {RADIUS};
}}

/* ---------------- labels ---------------- */
QLabel {{
    background: transparent;
}}
QLabel[hint="true"] {{
    color: {p.muted};
}}
QLabel#appTitle {{
    font-size: 19px;
    font-weight: 700;
    letter-spacing: -0.2px;
}}
QLabel#appSubtitle {{
    font-size: 12px;
    color: {p.muted};
}}
QLabel[role^="badge"] {{
    border-radius: 9px;
    padding: 2px 10px;
    font-size: 11px;
    font-weight: 600;
}}
QLabel[role="badge-running"] {{
    background: {p.badge_success_bg};
    color: {p.success};
}}
QLabel[role="badge-disabled"] {{
    background: {p.badge_danger_bg};
    color: {p.danger};
}}
QLabel[role="badge-muted"] {{
    background: {p.badge_muted_bg};
    color: {p.muted};
}}

/* ---------------- group boxes as cards ---------------- */
QGroupBox {{
    background: {p.surface};
    border: 1px solid {p.border};
    border-radius: {RADIUS};
    margin-top: 14px;
    padding: 14px 12px 12px 12px;
    font-weight: 600;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 14px;
    padding: 0 6px;
    color: {p.text};
    background: {p.surface};
    border: 1px solid {p.border};
    border-radius: 6px;
}}

/* ---------------- buttons ---------------- */
QPushButton {{
    background: {p.surface};
    border: 1px solid {p.border};
    border-radius: 8px;
    padding: 6px 14px;
    font-weight: 600;
    min-height: 18px;
}}
QPushButton:hover {{
    background: {p.hover};
    border-color: {p.muted};
}}
QPushButton:pressed {{
    background: {p.pressed};
}}
QPushButton:disabled {{
    color: {p.disabled_text};
    background: {p.disabled_fill};
    border-color: {p.border};
}}

QPushButton[role="primary"] {{
    background: {p.accent};
    border: 1px solid {p.accent};
    color: white;
}}
QPushButton[role="primary"]:hover {{
    background: {p.accent_hover};
    border-color: {p.accent_hover};
}}
QPushButton[role="primary"]:pressed {{
    background: {p.accent_pressed};
}}
QPushButton[role="primary"]:disabled {{
    background: {p.disabled_fill};
    border-color: {p.border};
    color: {p.disabled_text};
}}

QPushButton[role="danger"] {{
    background: {p.danger};
    border: 1px solid {p.danger};
    color: white;
}}
QPushButton[role="danger"]:hover {{
    background: {p.danger_hover};
    border-color: {p.danger_hover};
}}
QPushButton[role="danger"]:disabled {{
    background: {p.disabled_fill};
    border-color: {p.border};
    color: {p.disabled_text};
}}

QPushButton[role="success"] {{
    background: {p.success};
    border: 1px solid {p.success};
    color: white;
}}
QPushButton[role="success"]:hover {{
    background: {p.success_hover};
}}

QPushButton[role="ghost"] {{
    background: transparent;
    border: 1px dashed {p.border};
    color: {p.muted};
}}
QPushButton[role="ghost"]:hover {{
    color: {p.text};
    border-color: {p.muted};
    background: {p.hover};
}}

/* hotkey capture button while listening for keys */
QPushButton[hotkeyCapturing="true"],
QPushButton:checked {{
    background: {p.selection};
    border: 1px solid {p.accent};
    color: {p.accent};
}}

/* ---------------- inputs ---------------- */
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
    background: {p.surface};
    border: 1px solid {p.border};
    border-radius: 8px;
    padding: 5px 8px;
    selection-background-color: {p.accent};
    selection-color: white;
}}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {{
    border: 1px solid {p.accent};
}}
QLineEdit:disabled, QSpinBox:disabled, QComboBox:disabled {{
    background: {p.disabled_fill};
    color: {p.disabled_text};
}}
QSpinBox::up-button, QSpinBox::down-button,
QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{
    background: {p.hover};
    border: none;
    width: 18px;
}}
QSpinBox::up-button:hover, QSpinBox::down-button:hover,
QDoubleSpinBox::up-button:hover, QDoubleSpinBox::down-button:hover {{
    background: {p.selection};
}}
QComboBox::drop-down {{
    border: none;
    width: 22px;
}}
QComboBox QAbstractItemView {{
    background: {p.surface};
    border: 1px solid {p.border};
    border-radius: 8px;
    selection-background-color: {p.selection};
    selection-color: {p.text};
    padding: 4px;
}}

/* ---------------- checkboxes / radios ---------------- */
QCheckBox, QRadioButton {{
    spacing: 8px;
    background: transparent;
}}
QCheckBox::indicator, QRadioButton::indicator {{
    width: 16px;
    height: 16px;
}}
QCheckBox::indicator:unchecked {{
    border: 1px solid {p.muted};
    border-radius: 4px;
    background: {p.surface};
}}
QCheckBox::indicator:checked {{
    border: 1px solid {p.accent};
    border-radius: 4px;
    background: {p.accent};
}}
QRadioButton::indicator:unchecked {{
    border: 1px solid {p.muted};
    border-radius: 8px;
    background: {p.surface};
}}
QRadioButton::indicator:checked {{
    border: 5px solid {p.accent};
    border-radius: 8px;
    background: {p.surface};
}}

/* ---------------- tabs ---------------- */
QTabWidget::pane {{
    background: {p.surface};
    border: 1px solid {p.border};
    border-radius: {RADIUS};
    top: -1px;
}}
QTabBar::tab {{
    background: transparent;
    border: 1px solid transparent;
    border-bottom: 2px solid transparent;
    border-radius: 8px 8px 0 0;
    padding: 8px 18px;
    margin-right: 4px;
    color: {p.muted};
    font-weight: 600;
}}
QTabBar::tab:hover {{
    color: {p.text};
    background: {p.hover};
}}
QTabBar::tab:selected {{
    color: {p.accent};
    border-bottom: 2px solid {p.accent};
    background: {p.surface};
}}

/* ---------------- tables ---------------- */
QTableWidget, QTableView {{
    background: {p.surface};
    alternate-background-color: {p.alt_row};
    border: 1px solid {p.border};
    border-radius: {RADIUS};
    gridline-color: transparent;
    selection-background-color: {p.selection};
    selection-color: {p.text};
    padding: 4px;
}}
QTableWidget::item, QTableView::item {{
    padding: 6px 8px;
    border: none;
}}
QTableWidget::item:selected, QTableView::item:selected {{
    background: {p.selection};
    color: {p.text};
}}
QHeaderView::section {{
    background: {p.header_fill};
    border: none;
    border-bottom: 1px solid {p.border};
    padding: 7px 8px;
    font-weight: 700;
    color: {p.muted};
}}
QTableCornerButton::section {{
    background: {p.header_fill};
    border: none;
}}

/* ---------------- lists ---------------- */
QListWidget, QListView {{
    background: {p.surface};
    border: 1px solid {p.border};
    border-radius: 8px;
    padding: 4px;
    selection-background-color: {p.selection};
    selection-color: {p.text};
}}
QListWidget::item, QListView::item {{
    padding: 6px 8px;
    border-radius: 6px;
}}
QListWidget::item:selected, QListView::item:selected {{
    background: {p.selection};
    color: {p.text};
}}

/* ---------------- scrollbars ---------------- */
QScrollBar:vertical {{
    background: transparent;
    width: 10px;
    margin: 2px;
}}
QScrollBar::handle:vertical {{
    background: {p.muted};
    border-radius: 5px;
    min-height: 30px;
}}
QScrollBar::handle:vertical:hover {{
    background: {p.accent};
}}
QScrollBar:horizontal {{
    background: transparent;
    height: 10px;
    margin: 2px;
}}
QScrollBar::handle:horizontal {{
    background: {p.muted};
    border-radius: 5px;
    min-width: 30px;
}}
QScrollBar::add-line, QScrollBar::sub-line {{
    width: 0;
    height: 0;
}}
QScrollBar::add-page, QScrollBar::sub-page {{
    background: transparent;
}}

/* ---------------- dialogs / menus / tooltips ---------------- */
QMessageBox {{
    background: {p.surface};
}}
QMenu {{
    background: {p.surface};
    border: 1px solid {p.border};
    border-radius: 8px;
    padding: 5px;
}}
QMenu::item {{
    padding: 6px 20px;
    border-radius: 6px;
}}
QMenu::item:selected {{
    background: {p.selection};
}}
QToolTip {{
    background: {p.tooltip_bg};
    color: {p.tooltip_text};
    border: none;
    border-radius: 6px;
    padding: 5px 8px;
}}

/* ---------------- misc ---------------- */
QFrame[frameShape="4"], QFrame[classic="VLine"] {{
    color: {p.border};
}}
QSplitter::handle {{
    background: transparent;
}}
"""


#: The full application stylesheet for the light theme (kept for backwards
#: compatibility; prefer :func:`build_stylesheet` / :func:`set_mode`).
STYLESHEET = build_stylesheet(LIGHT)


def apply_role(widget: QPushButton | QLabel, role: str) -> None:
    """Tag *widget* with a themed ``role`` so the stylesheet styles it."""
    widget.setProperty("role", role)


def _apply_palette(app: QApplication, p: Palette) -> None:
    """Set the Qt palette on *app* so areas not covered by QSS match too."""
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor(p.background))
    palette.setColor(QPalette.ColorRole.WindowText, QColor(p.text))
    palette.setColor(QPalette.ColorRole.Base, QColor(p.surface))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor(p.alt_row))
    palette.setColor(QPalette.ColorRole.Text, QColor(p.text))
    palette.setColor(QPalette.ColorRole.PlaceholderText, QColor(p.muted))
    palette.setColor(QPalette.ColorRole.Button, QColor(p.surface))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor(p.text))
    palette.setColor(QPalette.ColorRole.Highlight, QColor(p.accent))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor("white"))
    palette.setColor(QPalette.ColorRole.Link, QColor(p.accent))
    palette.setColor(QPalette.ColorRole.ToolTipBase, QColor(p.tooltip_bg))
    palette.setColor(QPalette.ColorRole.ToolTipText, QColor(p.tooltip_text))
    palette.setColor(QPalette.ColorRole.BrightText, QColor(p.danger))
    disabled = QColor(p.disabled_text)
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, disabled)
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.WindowText, disabled)
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, disabled)
    app.setPalette(palette)


def set_mode(mode: str, app: QApplication | None = None) -> str:
    """Switch between ``"light"`` and ``"dark"`` and restyle *app* instantly.

    Returns the mode that was applied.  If *app* is omitted, the running
    :class:`QApplication` instance is used (stylesheet state still updates
    when no application exists yet).
    """
    mode = mode if mode in (MODE_LIGHT, MODE_DARK) else MODE_LIGHT
    _state["mode"] = mode
    if app is None:
        app = QApplication.instance()
    if app is not None:
        app.setStyleSheet(build_stylesheet(current_palette()))
        _apply_palette(app, current_palette())
    return mode


def toggle_mode(app: QApplication | None = None) -> str:
    """Flip light <-> dark; returns the newly active mode."""
    other = MODE_LIGHT if current_mode() == MODE_DARK else MODE_DARK
    return set_mode(other, app)


def style_app(app: QApplication, mode: str | None = None) -> None:
    """Install the theme on *app*: default font plus the active-mode stylesheet.

    *mode* (``"light"``/``"dark"``) optionally selects the appearance up
    front; otherwise the previously chosen mode is kept.
    """
    base = QFont()
    base.setPointSize(10)
    app.setFont(base)
    set_mode(mode if mode is not None else current_mode(), app)
