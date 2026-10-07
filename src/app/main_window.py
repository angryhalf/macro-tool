"""The single application window: a sidebar switches between pages.

Pages (Macros / Settings) are swapped in a ``QStackedWidget`` driven by
flat sidebar buttons -- the old ``QTabWidget`` row is gone.  The sidebar
also carries the app title and the emergency-stop button, and the whole
window is themed light/dark from the settings document (see
:mod:`app.theme`).

Stop-trigger/start-trigger screen conditions are set *inside* each macro's action list;
the macro engine's background monitor watches the screen for them
automatically while a macro runs -- no separate watcher page is needed.
"""

from __future__ import annotations

import logging
from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QApplication,
    QStackedWidget,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from app.settings import (
    AppSettings,
    MacroConfig,
    load_settings,
    rename_in_settings,
    save_settings,
)
from app.theme import STOP_BUTTON_QSS, build_stylesheet, get_theme, sidebar_button_qss
from core.macro_engine import MacroEngine
from services.input import describe_hotkey
from ui.macros_tab import MacrosTab
from ui.settings_tab import SettingsTab

logger = logging.getLogger(__name__)


class MainWindow(QMainWindow):
    """Owns the settings document and wires the pages to the engines."""

    # Engine threads may only touch the GUI through these signals.
    engine_state_changed = Signal()

    def __init__(self, settings_path: str | Path | None = None) -> None:
        super().__init__()
        self._settings_path = Path(settings_path) if settings_path else None
        self._settings: AppSettings = (
            load_settings(self._settings_path) if self._settings_path else load_settings()
        )

        self.setWindowTitle("Macro Tool")
        self.resize(980, 660)

        # -- engines ---------------------------------------------------
        self._macro_engine = MacroEngine()
        self._macro_engine.on_state_changed = self.engine_state_changed.emit

        self.engine_state_changed.connect(self._refresh_status)

        # -- pages -------------------------------------------------------
        self.macros_tab = MacrosTab(
            self._on_macros_changed,
            self._run_macro,
            self._stop_macro,
            lambda: self._settings.stop_hotkey,
            engine=self._macro_engine,
        )
        self.settings_tab = SettingsTab(self._on_global_settings_changed)

        self._stack = QStackedWidget()
        self._stack.addWidget(self.macros_tab)
        self._stack.addWidget(self.settings_tab)

        # -- sidebar ------------------------------------------------------
        self._nav_group = QButtonGroup(self)
        self._nav_group.setExclusive(True)

        brand = QLabel("Macro Tool")
        brand.setObjectName("brand")
        subtitle = QLabel("screen-aware automation")
        subtitle.setObjectName("subtitle")

        nav_macros = self._make_nav_button("≡   Macros", 0)
        nav_settings = self._make_nav_button("⚙   Settings", 1)
        self._nav_group.idClicked.connect(self._stack.setCurrentIndex)

        self.stop_button = QPushButton(f"■ STOP ALL ({describe_hotkey(self._settings.stop_hotkey)})")
        self.stop_button.setStyleSheet(STOP_BUTTON_QSS)
        self.stop_button.clicked.connect(self._stop_all)

        sidebar = QWidget()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(200)
        side_layout = QVBoxLayout(sidebar)
        side_layout.setContentsMargins(0, 18, 0, 14)
        side_layout.setSpacing(2)
        side_layout.addWidget(brand)
        side_layout.addWidget(subtitle)
        side_layout.addSpacing(14)
        side_layout.addWidget(nav_macros)
        side_layout.addWidget(nav_settings)
        side_layout.addStretch()
        side_layout.addWidget(self.stop_button)

        central = QWidget()
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(sidebar)
        page_host = QWidget()
        page_host.setObjectName("pageHost")
        page_layout = QVBoxLayout(page_host)
        page_layout.setContentsMargins(16, 16, 16, 16)
        page_layout.addWidget(self._stack)
        root.addWidget(page_host, stretch=1)

        self.setStatusBar(QStatusBar())
        self.setCentralWidget(central)

        # -- initial state ---------------------------------------------
        nav_macros.setChecked(True)
        self._stack.setCurrentIndex(0)
        self._apply_theme()
        # The monitor thread watches the screen for any stop-trigger/start-trigger
        # conditions inside running macros.  It is also started lazily by
        # ``start_macro``; starting it here keeps hotkeys that target rules warm
        # from launch (idempotent).
        self._macro_engine.start_monitor()
        self._push_settings_to_ui()
        self._apply_to_engines()
        self._refresh_status()

    # ------------------------------------------------------------------
    # Sidebar / chrome
    # ------------------------------------------------------------------
    def _make_nav_button(self, text: str, page_index: int) -> QPushButton:
        """A flat, checkable sidebar button that shows *page_index* on click."""
        button = QPushButton(text)
        button.setObjectName("navButton")
        button.setCheckable(True)
        self._nav_group.addButton(button, page_index)
        return button

    def _apply_theme(self) -> None:
        """Re-tone the whole window (global sheet + sidebar chrome) live."""
        t = get_theme(self._settings.theme, self._settings.accent_color)
        # Nav buttons and the sidebar paint per-theme QSS because their
        # checked-state / background colours vary with the palette.
        for button in self._nav_group.buttons():
            button.setStyleSheet(sidebar_button_qss(t))
        sidebar = self.findChild(QWidget, "sidebar")
        if sidebar is not None:
            sidebar.setStyleSheet(
                f"""
                #sidebar {{ background-color: {t.sidebar}; border-right: 1px solid {t.border}; }}
                #sidebar QLabel#brand {{
                    font-size: 17px; font-weight: bold; color: {t.text};
                    padding: 0 16px; background: transparent;
                }}
                #sidebar QLabel#subtitle {{
                    font-size: 11px; color: {t.muted};
                    padding: 0 16px; background: transparent;
                }}
                """
            )
        # The rest of the UI follows the application-wide stylesheet.
        app = QApplication.instance()
        if app is not None:
            app.setStyleSheet(build_stylesheet(t))

    # ------------------------------------------------------------------
    # Settings plumbing
    # ------------------------------------------------------------------
    def _push_settings_to_ui(self) -> None:
        self.macros_tab.set_macros(list(self._settings.macros))
        self.settings_tab.load(self._settings)

    def _update_settings(self, **changes) -> None:
        """Apply a new settings document to engines and persist it."""
        self._settings = replace(self._settings, **changes)
        self._apply_to_engines()
        self._save()

    def _save(self) -> None:
        try:
            if self._settings_path:
                save_settings(self._settings, self._settings_path)
            else:
                save_settings(self._settings)
        except OSError:
            logger.exception("Could not save settings")
            QMessageBox.warning(self, "Save failed", "Settings could not be written to disk.")

    def closeEvent(self, event) -> None:  # noqa: N802
        self._macro_engine.shutdown()
        self._save()
        super().closeEvent(event)

    # ------------------------------------------------------------------
    # Macro tab callbacks
    # ------------------------------------------------------------------
    def _macros_by_name(self) -> dict[str, MacroConfig]:
        return {m.name: m for m in self._settings.macros}

    def _on_macros_changed(self, macros: list[MacroConfig]) -> None:
        # Renames are detected by stable uid, not by guessing from name-set
        # differences (the old heuristic misfired whenever one macro was
        # deleted and another added in the same edit).
        old_names_by_uid = {m.uid: m.name for m in self._settings.macros if m.uid}
        updated: list[MacroConfig] = []
        renames: list[tuple[str, str]] = []  # (old_name, new_name)
        for macro in macros:
            uid = macro.uid or next(
                (known for known, name in old_names_by_uid.items() if name == macro.name), ""
            )
            previous_name = old_names_by_uid.get(uid)
            if previous_name is not None and previous_name != macro.name:
                renames.append((previous_name, macro.name))
            updated.append(replace(macro, uid=uid) if uid != macro.uid else macro)
        self._settings = replace(self._settings, macros=tuple(updated))
        for old_name, new_name in renames:
            # Keep references to the old name in sync across the document.
            self._settings = rename_in_settings(self._settings, old_name, new_name)
        self._apply_to_engines()
        self._save()

    def _run_macro(self, macro: MacroConfig) -> None:
        token = self._macro_engine.start_macro(macro, self._settings.execution_delay_ms / 1000.0)
        if token is None:
            QMessageBox.information(self, "Empty macro", f"'{macro.name}' has no actions yet.")
        else:
            stop_key = describe_hotkey(self._settings.stop_hotkey)
            self._set_status(f"Running '{macro.name}' — press {stop_key} to stop all")

    def _stop_macro(self, macro: MacroConfig) -> None:
        """Cancel just the selected macro's running sequence (if any)."""
        if self._macro_engine.stop_macro(macro.name):
            self._set_status(f"Stopped '{macro.name}'")
        else:
            self._set_status(f"'{macro.name}' is not running")
        self._refresh_status()

    # ------------------------------------------------------------------
    # Global settings callbacks
    # ------------------------------------------------------------------
    def _on_global_settings_changed(self) -> None:
        self._pull_global_settings()
        self._apply_to_engines()
        self._apply_theme()  # theme/accent changes must show up instantly
        self._save()
        self.stop_button.setText(f"■ STOP ALL ({describe_hotkey(self._settings.stop_hotkey)})")

    def _pull_global_settings(self) -> None:
        """Copy the settings-tab widgets into the settings document."""
        self._settings = self.settings_tab.apply_to(self._settings)

    # ------------------------------------------------------------------
    # Engines
    # ------------------------------------------------------------------
    def _apply_to_engines(self) -> None:
        self._pull_global_settings()
        self._macro_engine.apply_settings(self._settings)

    def _stop_all(self) -> None:
        self._macro_engine.stop_all()
        self._set_status("All stopped")
        self._refresh_status()

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------
    def _refresh_status(self) -> None:
        running = sorted(self._macro_engine.running_macros())
        if not running:
            self._set_status("idle")
            return
        # Enrich with session stats: loops completed and, crucially, which
        # trigger rule is currently holding a macro's actions back -- the
        # answer to "my macro looks like it's running but nothing happens".
        parts = []
        for name in running:
            macro = next((m for m in self._settings.macros if m.name == name), None)
            if macro is None:
                parts.append(name)
                continue
            stats = self._macro_engine.macro_stats(macro)
            detail = f"{name} ({stats['loops']} loops)"
            if stats["blocked_by"]:
                detail += f" — blocked by {stats['blocked_by']}"
            parts.append(detail)
        self._set_status("running: " + "; ".join(parts))

    def _set_status(self, text: str) -> None:
        self.statusBar().showMessage(text)
