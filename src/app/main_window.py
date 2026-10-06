"""The single application window: tabs for macros and settings.

Stop-trigger/start-trigger screen conditions are set *inside* each macro's action list;
the macro engine's background monitor watches the screen for them
automatically while a macro runs -- no separate watcher tab is needed.
"""

from __future__ import annotations

import logging
from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QTabWidget,
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
from core.macro_engine import MacroEngine
from services.input import describe_hotkey
from ui.macros_tab import MacrosTab
from ui.settings_tab import SettingsTab
from ui.theme import MODE_DARK, MODE_LIGHT, apply_role, set_mode

logger = logging.getLogger(__name__)


class MainWindow(QMainWindow):
    """Owns the settings document and wires the tabs to the engines."""

    # Engine threads may only touch the GUI through these signals.
    engine_state_changed = Signal()

    def __init__(self, settings_path: str | Path | None = None) -> None:
        super().__init__()
        self._settings_path = Path(settings_path) if settings_path else None
        self._settings: AppSettings = (
            load_settings(self._settings_path) if self._settings_path else load_settings()
        )

        self.setWindowTitle("Macro Tool")
        self.resize(900, 640)

        # -- engines ---------------------------------------------------
        self._macro_engine = MacroEngine()
        self._macro_engine.on_state_changed = self.engine_state_changed.emit

        self.engine_state_changed.connect(self._refresh_status)

        # -- tabs ------------------------------------------------------
        self.macros_tab = MacrosTab(
            self._on_macros_changed,
            self._run_macro,
            self._stop_macro,
            lambda: self._settings.stop_hotkey,
            engine=self._macro_engine,
        )
        self.settings_tab = SettingsTab(self._on_global_settings_changed)

        # -- header bar ------------------------------------------------
        self.stop_button = QPushButton(f"■  STOP ALL · {describe_hotkey(self._settings.stop_hotkey)}")
        apply_role(self.stop_button, "danger")
        self.stop_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.stop_button.setToolTip(
            "Cancel every running macro immediately (same as the stop hotkey)."
        )
        self.stop_button.clicked.connect(self._stop_all)

        title = QLabel("Macro Tool")
        title.setObjectName("appTitle")
        subtitle = QLabel("Screen-aware macros & trigger conditions")
        subtitle.setObjectName("appSubtitle")
        title_block = QVBoxLayout()
        title_block.setSpacing(1)
        title_block.addWidget(title)
        title_block.addWidget(subtitle)

        header = QHBoxLayout()
        header.setContentsMargins(2, 2, 2, 0)
        header.addLayout(title_block)
        header.addStretch()

        # Quick dark/light switch living in the header bar.
        self.theme_toggle = QPushButton("Dark mode")
        apply_role(self.theme_toggle, "ghost")
        self.theme_toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        self.theme_toggle.setToolTip("Switch between light and dark appearance.")
        self.theme_toggle.clicked.connect(self._toggle_theme)
        self._sync_theme_toggle()

        self._status_badge = QLabel("idle")
        apply_role(self._status_badge, "badge-muted")
        self._status_badge.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed
        )
        header.addWidget(self.theme_toggle)
        header.addSpacing(8)
        header.addWidget(self._status_badge)
        header.addSpacing(8)
        header.addWidget(self.stop_button)

        tabs = QTabWidget()
        tabs.addTab(self.macros_tab, "Macros")
        tabs.addTab(self.settings_tab, "Settings")

        # -- central layout --------------------------------------------
        central = QWidget()
        central.setObjectName("pageRoot")
        layout = QVBoxLayout(central)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(10)
        layout.addLayout(header)
        layout.addWidget(tabs)
        self.setCentralWidget(central)

        # -- initial state ---------------------------------------------
        # The monitor thread watches the screen for any stop-trigger/start-trigger
        # conditions inside running macros.  It is also started lazily by
        # ``start_macro``; starting it here keeps hotkeys that target rules warm
        # from launch (idempotent).
        self._macro_engine.start_monitor()
        # Honour the persisted appearance before any widget paints.
        set_mode(self._settings.theme_mode)
        self._push_settings_to_ui()
        self._apply_to_engines()
        self._refresh_status()

    # ------------------------------------------------------------------
    # Theme
    # ------------------------------------------------------------------
    def _sync_theme_toggle(self) -> None:
        """Keep the header toggle's label pointing at the *other* mode."""
        dark = self._settings.theme_mode == MODE_DARK
        self.theme_toggle.setText("Light mode" if dark else "Dark mode")

    def _toggle_theme(self) -> None:
        mode = MODE_LIGHT if self._settings.theme_mode == MODE_DARK else MODE_DARK
        self._apply_theme(mode)

    def _apply_theme(self, mode: str) -> None:
        """Switch the app-wide palette and persist the choice."""
        applied = set_mode(mode)
        if applied != self._settings.theme_mode:
            self._settings = replace(self._settings, theme_mode=applied)
            self._save()
        self._sync_theme_toggle()

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
        # Snapshot the widgets *before* touching settings: this callback also
        # fires when we programmatically sync widgets (e.g. load()), and
        # pulling a half-updated tab into the document would clobber fields
        # like the theme that other code paths just changed.
        pulled = self.settings_tab.apply_to(self._settings)
        if pulled.theme_mode != self._settings.theme_mode:
            # The Appearance combo changed — restyle immediately.
            set_mode(pulled.theme_mode)
            self._sync_theme_toggle()
        self._settings = pulled
        self._apply_to_engines()
        self._save()
        self.stop_button.setText(
            f"■  STOP ALL · {describe_hotkey(self._settings.stop_hotkey)}"
        )

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
        """Mirror the engine state in the header badge (no status bar)."""
        running = text.startswith("running:") or text.startswith("Running")
        self._status_badge.setText(f"● {text}" if running else text)
        apply_role(self._status_badge, "badge-running" if running else "badge-muted")
        self._status_badge.style().unpolish(self._status_badge)
        self._status_badge.style().polish(self._status_badge)
