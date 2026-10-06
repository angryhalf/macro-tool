"""Macros tab: list, create, edit, run and stop macro sequences.

Each macro has an *enabled* checkbox (disabled macros ignore their hotkey
and Run clicks -- park a misbehaving macro without deleting it) and can be
exported to / imported from a single-macro JSON file for sharing.  Stop-
trigger/start-trigger are ordinary *actions* inside each macro's action
list that trigger or untrigger other actions based on screen rules; the
engine watches the screen for those rules automatically while the macro
runs.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

from dataclasses import replace

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QFrame,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.settings import (
    MacroConfig,
    import_macro_files,
    new_macro_uid,
    save_macro_json,
)
from ui.macro_editor import MacroEditorDialog
from ui.theme import MUTED, apply_role

logger = logging.getLogger(__name__)


class MacrosTab(QWidget):
    """Table of all macros with add/edit/remove/run controls."""

    def __init__(
        self,
        on_changed: Callable[[list[MacroConfig]], None],
        on_run: Callable[[MacroConfig], None],
        on_stop: Callable[[MacroConfig], None],
        stop_hotkey_provider: Callable[[], str] = lambda: "f8",
        engine=None,
        parent: QWidget | None = None,
    ) -> None:
        """*engine* is the :class:`~core.macro_engine.MacroEngine` (optional so
        the tab stays usable in tests without one); it only provides live
        run-state info shown in the table (e.g. which macros are running)."""
        super().__init__(parent)
        self._on_changed = on_changed
        self._on_run = on_run
        self._on_stop = on_stop
        self._stop_hotkey_provider = stop_hotkey_provider
        self._engine = engine
        self._macros: list[MacroConfig] = []
        # Snapshot of the running-macro set from the last refresh; the poll
        # timer compares against it and only rewrites cells on a real change.
        self._last_running: frozenset[str] = frozenset()
        # True while a checkbox programmatic-toggle round-trip is in flight,
        # so itemChanged does not treat our own write as a user click.
        self._toggling = False

        self._table = QTableWidget(0, 5)
        self._table.setHorizontalHeaderLabels(["", "Name", "Hotkey", "Loops", "Actions"])
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        for column in range(1, 5):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.Stretch)
        header.setHighlightSections(False)
        self._table.verticalHeader().setVisible(False)
        self._table.setShowGrid(False)
        self._table.setAlternatingRowColors(True)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setFrameShape(QFrame.Shape.NoFrame)
        self._table.viewport().setCursor(Qt.CursorShape.PointingHandCursor)
        self._table.doubleClicked.connect(self._edit_selected)
        self._table.itemChanged.connect(self._on_item_changed)

        toolbar = QHBoxLayout()
        toolbar.setSpacing(8)
        for text, slot, role in (
            ("＋  Add macro", self._add_macro, "primary"),
            ("✎  Edit", self._edit_selected, ""),
            ("⧉  Duplicate", self._duplicate_selected, ""),
            ("🗑  Remove", self._remove_selected, ""),
            ("↗  Export…", self._export_selected, ""),
            ("↙  Import…", self._import_macros, ""),
        ):
            button = QPushButton(text)
            if role:
                apply_role(button, role)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(slot)
            toolbar.addWidget(button)
        toolbar.addStretch()

        run_row = QHBoxLayout()
        run_row.setSpacing(8)
        self._run_button = QPushButton("▶  Run selected macro")
        apply_role(self._run_button, "success")
        self._run_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._run_button.clicked.connect(self._run_selected)
        self._stop_button = QPushButton("■  Stop selected macro")
        apply_role(self._stop_button, "danger")
        self._stop_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._stop_button.clicked.connect(self._stop_selected)
        self._status = QLabel("")
        self._status.setProperty("hint", "true")
        run_row.addWidget(self._run_button)
        run_row.addWidget(self._stop_button)
        run_row.addSpacing(6)
        run_row.addWidget(self._status)
        run_row.addStretch()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(12)
        layout.addLayout(toolbar)
        layout.addWidget(self._table)
        layout.addLayout(run_row)

        # Fallback poll so the table reflects live run state even if an
        # engine event was missed; cheap no-op when nothing changed.
        self._state_timer = QTimer(self)
        self._state_timer.setInterval(750)
        self._state_timer.timeout.connect(self._poll_run_state)
        self._state_timer.start()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def set_macros(self, macros: list[MacroConfig]) -> None:
        self._macros = list(macros)
        self._reload_table()

    def set_status(self, text: str) -> None:
        self._status.setText(text)

    # ------------------------------------------------------------------
    # Slots
    # ------------------------------------------------------------------
    def _add_macro(self) -> None:
        dialog = MacroEditorDialog(MacroConfig(), parent=self, stop_hotkey=self._stop_hotkey_provider())
        if dialog.exec():
            self._macros.append(dialog.macro())
            self._commit()

    def _edit_selected(self) -> None:
        index = self._selected_index()
        if index is None:
            return
        original = self._macros[index]
        dialog = MacroEditorDialog(original, parent=self, stop_hotkey=self._stop_hotkey_provider())
        if dialog.exec():
            self._macros[index] = dialog.macro()
            self._commit()

    def _duplicate_selected(self) -> None:
        index = self._selected_index()
        if index is None:
            return
        copy = self._macros[index]
        self._macros.append(
            MacroConfig(
                name=f"{copy.name} (copy)",
                start_hotkey="",
                repeat=copy.repeat,
                loops=copy.loops,
                interval_ms=copy.interval_ms,
                actions=copy.actions,
                uid=new_macro_uid(),  # a copy is a distinct macro identity
                enabled=copy.enabled,
            )
        )
        self._commit()

    def _remove_selected(self) -> None:
        index = self._selected_index()
        if index is not None:
            del self._macros[index]
            self._commit()

    def _export_selected(self) -> None:
        """Write the selected macro to one JSON file for sharing."""
        index = self._selected_index()
        if index is None:
            return
        macro = self._macros[index]
        safe = "".join(ch for ch in macro.name if ch.isalnum() or ch in "-_ ")[:60].strip()
        path, _ = QFileDialog.getSaveFileName(
            self, "Export macro", f"{safe or 'macro'}.json", "Macro JSON (*.json *.macro)"
        )
        if not path:
            return
        try:
            save_macro_json(macro, Path(path))
        except OSError as exc:
            self.set_status(f"Export failed: {exc}")
            logger.warning("macro export failed: %s", exc)
        else:
            self.set_status(f"Exported “{macro.name}” to {path}")

    def _import_macros(self) -> None:
        """Add macros from exported JSON files (disabled, hotkey cleared)."""
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Import macro(s)", "", "Macro/settings JSON (*.json *.macro)"
        )
        if not paths:
            return
        added = 0
        failures = []
        for path in paths:
            try:
                self._macros.extend(import_macro_files([Path(path)], self._macros))
                added += 1
            except (OSError, ValueError) as exc:
                failures.append(Path(path).name)
                logger.warning("macro import failed for %s: %s", path, exc)
        if added:
            self._commit()
        status = f"Imported {added} macro(s)" + (" (disabled, no hotkey)" if added else "")
        if failures:
            status += f"; skipped {len(failures)} unreadable file(s)"
        self.set_status(status)

    def _run_selected(self) -> None:
        index = self._selected_index()
        if index is None:
            return
        macro = self._macros[index]
        if not macro.enabled:
            self.set_status(f"“{macro.name}” is disabled — tick its checkbox or enable it in the editor first.")
            return
        self._on_run(macro)
        self._refresh_run_state()

    def _stop_selected(self) -> None:
        index = self._selected_index()
        if index is not None:
            self._on_stop(self._macros[index])
            self._refresh_run_state()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _selected_index(self) -> int | None:
        row = self._table.currentRow()
        return row if 0 <= row < len(self._macros) else None

    def _commit(self) -> None:
        """Refresh the table and notify the owner that macros changed."""
        self._reload_table()
        self._on_changed(self._macros)

    def _reload_table(self) -> None:
        self._toggling = True  # our own writes must not read as user toggles
        try:
            self._table.setRowCount(len(self._macros))
            for row, macro in enumerate(self._macros):
                loops = "∞" if macro.repeat else str(macro.loops)
                check = QTableWidgetItem()
                check.setFlags(
                    Qt.ItemFlag.ItemIsUserCheckable
                    | Qt.ItemFlag.ItemIsEnabled
                    | Qt.ItemFlag.ItemIsSelectable
                )
                check.setCheckState(
                    Qt.CheckState.Checked if macro.enabled else Qt.CheckState.Unchecked
                )
                check.setToolTip("Enable/disable this macro")
                self._table.setItem(row, 0, check)
                values = (
                    macro.name,
                    macro.start_hotkey or "—",
                    loops,
                    str(len(macro.actions)),
                )
                for column, value in enumerate(values, start=1):
                    self._table.setItem(row, column, QTableWidgetItem(value))
        finally:
            self._toggling = False
        self._refresh_run_state()

    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        """Checkbox toggle in the table: flip enabled and persist."""
        if self._toggling or item.column() != 0:
            return
        row = item.row()
        if not 0 <= row < len(self._macros):
            return
        enabled = item.checkState() == Qt.CheckState.Checked
        if enabled == self._macros[row].enabled:
            return
        self._macros[row] = replace(self._macros[row], enabled=enabled)
        state = "enabled" if enabled else "disabled"
        logger.info("macro %r %s from list", self._macros[row].name, state)
        self._commit()

    def _refresh_run_state(self) -> None:
        """Mark running macros with ▶ and gray out disabled ones."""
        if self._engine is None:
            return
        running = set(self._engine.running_macros())
        self._last_running = frozenset(running)
        for row, macro in enumerate(self._macros):
            item = self._table.item(row, 1)  # name column (0 is the checkbox)
            if item is None:
                continue
            prefix = "▶  " if macro.name in running else ""
            suffix = "" if macro.enabled else "   ·  disabled"
            item.setText(f"{prefix}{macro.name}{suffix}")
            if macro.name in running:
                item.setForeground(QBrush(QColor("#30a46c")))
            elif macro.enabled:
                item.setForeground(QBrush(QColor("#1c2333")))
            else:
                item.setForeground(QBrush(QColor(MUTED)))

    def _poll_run_state(self) -> None:
        """Timer tick: only touch the table when the run set actually changed.

        Engine start/stop events already refresh via ``set_engine``; this
        fallback poll exists to catch state changes that missed a signal, so
        it does a cheap comparison instead of rewriting every name cell each
        tick (which reset selection styling and repainted needlessly).
        """
        if self._engine is None:
            return
        current = frozenset(self._engine.running_macros())
        if current != self._last_running:
            self._last_running = current
            self._refresh_run_state()
