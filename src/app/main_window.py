"""The single application window: tabs for macros, watchers and settings."""

from __future__ import annotations

import logging
from dataclasses import replace

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QStatusBar,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from app.settings import (
    AppSettings,
    MacroConfig,
    WatcherConfig,
    load_settings,
    rename_in_settings,
    save_settings,
)
from core.macro_engine import MacroEngine
from core.watcher_engine import ScreenWatcherEngine
from services.input import describe_hotkey
from ui.macros_tab import MacrosTab
from ui.settings_tab import SettingsTab
from ui.watchers_tab import WatchersTab

logger = logging.getLogger(__name__)


class MainWindow(QMainWindow):
    """Owns the settings document and wires the tabs to the engines."""

    # Engine threads may only touch the GUI through these signals.
    engine_state_changed = Signal()
    watcher_fired = Signal(str)

    def __init__(self, settings_path=None) -> None:
        super().__init__()
        self._settings_path = settings_path
        self._settings: AppSettings = (
            load_settings(settings_path) if settings_path else load_settings()
        )

        self.setWindowTitle("Macro Tool")
        self.resize(900, 640)

        # -- engines ---------------------------------------------------
        self._macro_engine = MacroEngine()
        self._macro_engine.on_state_changed = self.engine_state_changed.emit
        self._watcher_engine = ScreenWatcherEngine(self._macro_engine, self._macros_by_name)
        self._watcher_engine.on_state_changed = lambda: self.engine_state_changed.emit()

        self.engine_state_changed.connect(self._refresh_status)
        self.watcher_fired.connect(lambda name: self._set_status(f"Watcher '{name}' fired"))

        # -- tabs ------------------------------------------------------
        self.macros_tab = MacrosTab(
            self._on_macros_changed, self._run_macro, lambda: self._settings.stop_hotkey
        )
        self.watchers_tab = WatchersTab(
            self._on_watchers_changed, self._start_watcher, self._stop_watcher, self._macro_names
        )
        self.settings_tab = SettingsTab(self._on_global_settings_changed)

        tabs = QTabWidget()
        tabs.addTab(self.macros_tab, "Macros")
        tabs.addTab(self.watchers_tab, "Screen watchers")
        tabs.addTab(self.settings_tab, "Settings")

        # -- top bar ---------------------------------------------------
        self.stop_button = QPushButton(f"■ STOP ALL ({describe_hotkey(self._settings.stop_hotkey)})")
        self.stop_button.setStyleSheet(
            "background-color:#c0392b; color:white; font-weight:bold; padding:6px;"
        )
        self.stop_button.clicked.connect(self._stop_all)
        title = QLabel("<b>Macro Tool</b>")
        top_row = QHBoxLayout()
        top_row.addWidget(title)
        top_row.addStretch()
        top_row.addWidget(self.stop_button)

        # -- status bar ------------------------------------------------
        central = QWidget()
        layout = QVBoxLayout(central)
        layout.addLayout(top_row)
        layout.addWidget(tabs)
        self.setStatusBar(QStatusBar())
        layout.setContentsMargins(8, 8, 8, 0)
        self.setCentralWidget(central)

        # -- initial state ---------------------------------------------
        self._push_settings_to_ui()
        self._apply_to_engines(start_auto_watchers=True)
        self._refresh_status()

    # ------------------------------------------------------------------
    # Settings plumbing
    # ------------------------------------------------------------------
    def _push_settings_to_ui(self) -> None:
        self.macros_tab.set_macros(list(self._settings.macros))
        self.watchers_tab.set_watchers(list(self._settings.watchers))
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
        self._watcher_engine.stop_all()
        self._macro_engine.shutdown()
        self._save()
        super().closeEvent(event)

    # ------------------------------------------------------------------
    # Macro tab callbacks
    # ------------------------------------------------------------------
    def _macro_names(self) -> list[str]:
        return [m.name for m in self._settings.macros]

    def _macros_by_name(self) -> dict[str, MacroConfig]:
        return {m.name: m for m in self._settings.macros}

    def _on_macros_changed(self, macros: list[MacroConfig]) -> None:
        old_names = {m.name for m in self._settings.macros}
        new_names = {m.name for m in macros}
        self._settings = replace(self._settings, macros=tuple(macros))
        # If a macro was renamed, keep watcher references in sync.
        removed = old_names - new_names
        added = new_names - old_names
        if len(removed) == 1 and len(added) == 1:
            self._settings = rename_in_settings(self._settings, removed.pop(), added.pop())
            self.watchers_tab.set_watchers(list(self._settings.watchers))
        self.watchers_tab.refresh_macro_choices()
        self._apply_to_engines()
        self._save()

    def _run_macro(self, macro: MacroConfig) -> None:
        token = self._macro_engine.start_macro(macro, self._settings.execution_delay_ms / 1000.0)
        if token is None:
            QMessageBox.information(self, "Empty macro", f"'{macro.name}' has no actions yet.")
        else:
            stop_key = describe_hotkey(self._settings.stop_hotkey)
            self._set_status(f"Running '{macro.name}' — press {stop_key} to stop")

    # ------------------------------------------------------------------
    # Watcher tab callbacks
    # ------------------------------------------------------------------
    def _on_watchers_changed(self, watchers: list[WatcherConfig]) -> None:
        self._update_settings(watchers=tuple(watchers))
        # Watchers whose config changed should restart if currently running.
        running = {w.name for w in watchers if self._watcher_engine.is_running(w.name)}
        for watcher in watchers:
            if watcher.name in running:
                self._watcher_engine.start(watcher)
        self._refresh_status()

    def _start_watcher(self, watcher: WatcherConfig) -> None:
        if self._watcher_engine.start(watcher):
            self._set_status(f"Watcher '{watcher.name}' started")
        else:
            QMessageBox.warning(
                self, "Cannot start watcher", "Select a valid template image before starting."
            )
        self._refresh_status()

    def _stop_watcher(self, name: str) -> None:
        self._watcher_engine.stop(name)
        self._refresh_status()

    # ------------------------------------------------------------------
    # Global settings callbacks
    # ------------------------------------------------------------------
    def _on_global_settings_changed(self) -> None:
        self._pull_global_settings()
        self._apply_to_engines()
        self._save()
        self.stop_button.setText(f"■ STOP ALL ({describe_hotkey(self._settings.stop_hotkey)})")

    def _pull_global_settings(self) -> None:
        """Copy the settings-tab widgets into the settings document."""
        self._settings = self.settings_tab.apply_to(self._settings)

    # ------------------------------------------------------------------
    # Engines
    # ------------------------------------------------------------------
    def _apply_to_engines(self, start_auto_watchers: bool = False) -> None:
        self._pull_global_settings()
        self._macro_engine.apply_settings(self._settings)
        if start_auto_watchers:
            for watcher in self._settings.watchers:
                if watcher.auto_start:
                    self._watcher_engine.start(watcher)

    def _stop_all(self) -> None:
        self._macro_engine.stop_all()
        self._watcher_engine.stop_all()
        self._set_status("All stopped")
        self._refresh_status()

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------
    def _refresh_status(self) -> None:
        busy = "running" if self._macro_engine.is_busy() else "idle"
        self._set_status(f"{busy} · {self._watcher_engine.status_text()}")
        running = {
            w.name for w in self._settings.watchers if self._watcher_engine.is_running(w.name)
        }
        self.watchers_tab.set_running_state(running)

    def _set_status(self, text: str) -> None:
        self.statusBar().showMessage(text)
