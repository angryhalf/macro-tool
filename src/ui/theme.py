"""Central UI theme: a modern light design system applied app-wide via QSS.

Everything visual lives here — the colour palette, typography, spacing and
the stylesheet installed on the :class:`QApplication` — so widgets can style
themselves consistently through dynamic properties instead of scattered
inline ``setStyleSheet`` calls.

Dynamic properties understood by the stylesheet (set with
``widget.setProperty(name, value)``):

* ``role``  on QPushButton / QLabel badges — one of ``primary``, ``danger``,
  ``success``, ``ghost``, ``badge-running``, ``badge-disabled``,
  ``badge-muted``.
* ``hint="true"`` on QLabel — muted secondary text.
* ``card="true"`` on QWidget — rounded elevated card panel.
"""

from __future__ import annotations

from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import QApplication, QLabel, QPushButton

# ----------------------------------------------------------------------
# Palette
# ----------------------------------------------------------------------
ACCENT = "#4f6df5"          # brand indigo-blue
ACCENT_HOVER = "#3d5ae8"
ACCENT_PRESSED = "#2f49c9"
DANGER = "#e5484d"
DANGER_HOVER = "#d93a40"
SUCCESS = "#30a46c"
SURFACE = "#ffffff"
BACKGROUND = "#f3f5fa"
BORDER = "#dfe3ec"
TEXT = "#1c2333"
MUTED = "#697386"
SELECTION = "#e8edff"

RADIUS = "10px"

#: The full application stylesheet.  Written once; every widget picks it up
#: automatically because it is installed on the QApplication instance.
STYLESHEET = f"""
/* ---------------- global typography ---------------- */
QWidget {{
    color: {TEXT};
    font-family: "Segoe UI", "Inter", "SF Pro Text", "Helvetica Neue",
                 "Noto Sans", Arial, sans-serif;
    font-size: 13px;
}}

QMainWindow, QDialog, QWidget#pageRoot {{
    background: {BACKGROUND};
}}

/* ---------------- cards / panels ---------------- */
QWidget[card="true"] {{
    background: {SURFACE};
    border: 1px solid {BORDER};
    border-radius: {RADIUS};
}}

/* ---------------- labels ---------------- */
QLabel {{
    background: transparent;
}}
QLabel[hint="true"] {{
    color: {MUTED};
}}
QLabel#appTitle {{
    font-size: 19px;
    font-weight: 700;
    letter-spacing: -0.2px;
}}
QLabel#appSubtitle {{
    font-size: 12px;
    color: {MUTED};
}}
QLabel[role^="badge"] {{
    border-radius: 9px;
    padding: 2px 10px;
    font-size: 11px;
    font-weight: 600;
}}
QLabel[role="badge-running"] {{
    background: #e6f6ee;
    color: {SUCCESS};
}}
QLabel[role="badge-disabled"] {{
    background: #fdecec;
    color: {DANGER};
}}
QLabel[role="badge-muted"] {{
    background: #eef1f6;
    color: {MUTED};
}}

/* ---------------- group boxes as cards ---------------- */
QGroupBox {{
    background: {SURFACE};
    border: 1px solid {BORDER};
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
    color: {TEXT};
    background: {SURFACE};
    border: 1px solid {BORDER};
    border-radius: 6px;
}}

/* ---------------- buttons ---------------- */
QPushButton {{
    background: {SURFACE};
    border: 1px solid {BORDER};
    border-radius: 8px;
    padding: 6px 14px;
    font-weight: 600;
    min-height: 18px;
}}
QPushButton:hover {{
    background: #f6f8fc;
    border-color: #c9d2e4;
}}
QPushButton:pressed {{
    background: #eef1f8;
}}
QPushButton:disabled {{
    color: #a6adbd;
    background: #f2f4f8;
    border-color: {BORDER};
}}

QPushButton[role="primary"] {{
    background: {ACCENT};
    border: 1px solid {ACCENT};
    color: white;
}}
QPushButton[role="primary"]:hover {{
    background: {ACCENT_HOVER};
    border-color: {ACCENT_HOVER};
}}
QPushButton[role="primary"]:pressed {{
    background: {ACCENT_PRESSED};
}}
QPushButton[role="primary"]:disabled {{
    background: #b9c4f2;
    border-color: #b9c4f2;
    color: #eef1ff;
}}

QPushButton[role="danger"] {{
    background: {DANGER};
    border: 1px solid {DANGER};
    color: white;
}}
QPushButton[role="danger"]:hover {{
    background: {DANGER_HOVER};
    border-color: {DANGER_HOVER};
}}
QPushButton[role="danger"]:pressed {{
    background: #c22c31;
}}
QPushButton[role="danger"]:disabled {{
    background: #f2b4b6;
    border-color: #f2b4b6;
    color: #fdf1f1;
}}

QPushButton[role="success"] {{
    background: {SUCCESS};
    border: 1px solid {SUCCESS};
    color: white;
}}
QPushButton[role="success"]:hover {{
    background: #2b9160;
}}

QPushButton[role="ghost"] {{
    background: transparent;
    border: 1px dashed {BORDER};
    color: {MUTED};
}}
QPushButton[role="ghost"]:hover {{
    color: {TEXT};
    border-color: #c9d2e4;
    background: #f6f8fc;
}}

/* hotkey capture button while listening for keys */
QPushButton[hotkeyCapturing="true"],
QPushButton:checked {{
    background: {SELECTION};
    border: 1px solid {ACCENT};
    color: {ACCENT};
}}

/* ---------------- inputs ---------------- */
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
    background: {SURFACE};
    border: 1px solid {BORDER};
    border-radius: 8px;
    padding: 5px 8px;
    selection-background-color: {ACCENT};
    selection-color: white;
}}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {{
    border: 1px solid {ACCENT};
}}
QLineEdit:disabled, QSpinBox:disabled, QComboBox:disabled {{
    background: #f2f4f8;
    color: #a6adbd;
}}
QComboBox::drop-down {{
    border: none;
    width: 22px;
}}
QComboBox QAbstractItemView {{
    background: {SURFACE};
    border: 1px solid {BORDER};
    border-radius: 8px;
    selection-background-color: {SELECTION};
    selection-color: {TEXT};
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
    border: 1px solid #b8c1d4;
    border-radius: 4px;
    background: {SURFACE};
}}
QCheckBox::indicator:checked {{
    border: 1px solid {ACCENT};
    border-radius: 4px;
    background: {ACCENT};
}}
QRadioButton::indicator:unchecked {{
    border: 1px solid #b8c1d4;
    border-radius: 8px;
    background: {SURFACE};
}}
QRadioButton::indicator:checked {{
    border: 5px solid {ACCENT};
    border-radius: 8px;
    background: {SURFACE};
}}

/* ---------------- tabs ---------------- */
QTabWidget::pane {{
    background: {SURFACE};
    border: 1px solid {BORDER};
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
    color: {MUTED};
    font-weight: 600;
}}
QTabBar::tab:hover {{
    color: {TEXT};
    background: #eceff7;
}}
QTabBar::tab:selected {{
    color: {ACCENT};
    border-bottom: 2px solid {ACCENT};
    background: {SURFACE};
}}

/* ---------------- tables ---------------- */
QTableWidget, QTableView {{
    background: {SURFACE};
    alternate-background-color: #f8fafd;
    border: 1px solid {BORDER};
    border-radius: {RADIUS};
    gridline-color: transparent;
    selection-background-color: {SELECTION};
    selection-color: {TEXT};
    padding: 4px;
}}
QTableWidget::item, QTableView::item {{
    padding: 6px 8px;
    border: none;
}}
QTableWidget::item:selected, QTableView::item:selected {{
    background: {SELECTION};
    color: {TEXT};
}}
QHeaderView::section {{
    background: #f4f6fb;
    border: none;
    border-bottom: 1px solid {BORDER};
    padding: 7px 8px;
    font-weight: 700;
    color: {MUTED};
}}
QTableCornerButton::section {{
    background: #f4f6fb;
    border: none;
}}

/* ---------------- lists ---------------- */
QListWidget, QListView {{
    background: {SURFACE};
    border: 1px solid {BORDER};
    border-radius: 8px;
    padding: 4px;
    selection-background-color: {SELECTION};
    selection-color: {TEXT};
}}
QListWidget::item, QListView::item {{
    padding: 6px 8px;
    border-radius: 6px;
}}
QListWidget::item:selected, QListView::item:selected {{
    background: {SELECTION};
    color: {TEXT};
}}

/* ---------------- scrollbars ---------------- */
QScrollBar:vertical {{
    background: transparent;
    width: 10px;
    margin: 2px;
}}
QScrollBar::handle:vertical {{
    background: #ccd4e2;
    border-radius: 5px;
    min-height: 30px;
}}
QScrollBar::handle:vertical:hover {{
    background: #b4bfd3;
}}
QScrollBar:horizontal {{
    background: transparent;
    height: 10px;
    margin: 2px;
}}
QScrollBar::handle:horizontal {{
    background: #ccd4e2;
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

/* ---------------- status bar ---------------- */
QStatusBar {{
    background: {SURFACE};
    border-top: 1px solid {BORDER};
    color: {MUTED};
}}
QStatusBar::item {{
    border: none;
}}

/* ---------------- dialogs / menus / tooltips ---------------- */
QMessageBox {{
    background: {SURFACE};
}}
QMenu {{
    background: {SURFACE};
    border: 1px solid {BORDER};
    border-radius: 8px;
    padding: 5px;
}}
QMenu::item {{
    padding: 6px 20px;
    border-radius: 6px;
}}
QMenu::item:selected {{
    background: {SELECTION};
}}
QToolTip {{
    background: {TEXT};
    color: white;
    border: none;
    border-radius: 6px;
    padding: 5px 8px;
}}

/* ---------------- misc ---------------- */
QFrame[frameShape="4"], QFrame[classic="VLine"] {{
    color: {BORDER};
}}
QSplitter::handle {{
    background: transparent;
}}
"""


def apply_role(widget: QPushButton | QLabel, role: str) -> None:
    """Tag *widget* with a themed ``role`` so the stylesheet styles it."""
    widget.setProperty("role", role)


def style_app(app: QApplication) -> None:
    """Install the theme on *app*: palette, default font and stylesheet."""
    app.setStyleSheet(STYLESHEET)

    base = QFont()
    base.setPointSize(10)
    app.setFont(base)

    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor(BACKGROUND))
    palette.setColor(QPalette.ColorRole.WindowText, QColor(TEXT))
    palette.setColor(QPalette.ColorRole.Base, QColor(SURFACE))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor("#f8fafd"))
    palette.setColor(QPalette.ColorRole.Text, QColor(TEXT))
    palette.setColor(QPalette.ColorRole.PlaceholderText, QColor(MUTED))
    palette.setColor(QPalette.ColorRole.Button, QColor(SURFACE))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor(TEXT))
    palette.setColor(QPalette.ColorRole.Highlight, QColor(ACCENT))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor("white"))
    palette.setColor(QPalette.ColorRole.Link, QColor(ACCENT))
    palette.setColor(QPalette.ColorRole.ToolTipBase, QColor(TEXT))
    palette.setColor(QPalette.ColorRole.ToolTipText, QColor("white"))
    disabled = QColor("#a6adbd")
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, disabled)
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.WindowText, disabled)
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, disabled)
    app.setPalette(palette)
