"""Macros tab: list, create, edit, run and pause/unpause macro sequences.

The user turns a macro on and off (Run / Stop); the pause controls only hold
its actions back temporarily.  Macros start *paused* unless configured
otherwise -- press Unpause (or let an ``unpause`` screen rule clear) to let it
begin acting.  Screen-based pausing is expressed with ``pause``/``unpause``
*actions* inside the sequence, not with separate condition sections.
"""

from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.settings import MacroConfig
from ui.macro_editor import MacroEditorDialog


class MacrosTab(QWidget):
    """Table of all macros with add/edit/remove/run/pause controls."""

    def __init__(
        self,
        on_changed: Callable[[list[MacroConfig]], None],
        on_run: Callable[[MacroConfig], None],
        stop_hotkey_provider: Callable[[], str] = lambda: "f8",
        engine=None,
        parent: QWidget | None = None,
    ) -> None:
        """*engine* is the :class:`~core.macro_engine.MacroEngine` (optional so
        the tab stays usable in tests without one); it provides the live
        per-macro pause/unpause state shown by the Pause button."""
        super().__init__(parent)
        self._on_changed = on_changed
        self._on_run = on_run
        self._stop_hotkey_provider = stop_hotkey_provider
        self._engine = engine
        self._macros: list[MacroConfig] = []

        self._table = QTableWidget(0, 5)
        self._table.setHorizontalHeaderLabels(
            ["Name", "Hotkey", "Loops", "Actions", "Start state"]
        )
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.doubleClicked.connect(self._edit_selected)

        toolbar = QHBoxLayout()
        for text, slot in (
            ("Add macro", self._add_macro),
            ("Edit macro", self._edit_selected),
            ("Duplicate", self._duplicate_selected),
            ("Remove", self._remove_selected),
        ):
            button = QPushButton(text)
            button.clicked.connect(slot)
            toolbar.addWidget(button)
        toolbar.addStretch()

        run_row = QHBoxLayout()
        self._run_button = QPushButton("▶ Run selected macro")
        self._run_button.clicked.connect(self._run_selected)
        self._pause_button = QPushButton("⏸ Pause")
        self._pause_button.setToolTip(
            "Hold the running macro's actions back (Unpause resumes them).\n"
            "Macros start paused by default — press Unpause to let it begin."
        )
        self._pause_button.setEnabled(False)
        self._pause_button.clicked.connect(self._toggle_pause)
        self._status = QLabel("")
        run_row.addWidget(self._run_button)
        run_row.addWidget(self._pause_button)
        run_row.addWidget(self._status)
        run_row.addStretch()

        layout = QVBoxLayout(self)
        layout.addLayout(toolbar)
        layout.addWidget(self._table)
        layout.addLayout(run_row)

        # Poll the engine so the Pause button reflects live run state.
        self._state_timer = QTimer(self)
        self._state_timer.setInterval(750)
        self._state_timer.timeout.connect(self._refresh_run_state)
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
                start_paused=copy.start_paused,
            )
        )
        self._commit()

    def _remove_selected(self) -> None:
        index = self._selected_index()
        if index is not None:
            del self._macros[index]
            self._commit()

    def _run_selected(self) -> None:
        index = self._selected_index()
        if index is not None:
            self._on_run(self._macros[index])
            self._refresh_run_state()

    def _toggle_pause(self) -> None:
        index = self._selected_index()
        if index is None or self._engine is None:
            return
        name = self._macros[index].name
        was_paused = self._engine.is_macro_paused(name)
        self._engine.toggle_macro_pause(name)
        # If the conditional gate still holds, the macro remains paused.
        still_paused = self._engine.is_macro_paused(name)
        if was_paused and still_paused:
            self.set_status(f"'{name}' still held by a pause rule on screen")
        elif was_paused:
            self.set_status(f"'{name}' unpaused")
        else:
            self.set_status(f"'{name}' paused")
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

    def _flow_summary(self, macro: MacroConfig) -> tuple[int, int]:
        """Count (flow steps, steps missing their screen rule)."""
        flow = [a for a in macro.actions if a.kind in ("wait_for", "pause", "unpause")]
        missing = [
            a for a in flow
            if a.condition is None or not a.condition.configured
        ]
        return len(flow), len(missing)

    def _reload_table(self) -> None:
        self._table.setRowCount(len(self._macros))
        for row, macro in enumerate(self._macros):
            loops = "∞" if macro.repeat else str(macro.loops)
            flow_count, missing_count = self._flow_summary(macro)
            start_state = "paused" if macro.start_paused else "running"
            if flow_count:
                start_state += f" · {flow_count} pause rule(s)"
            if missing_count:
                start_state += f" ({missing_count} missing!)"
            values = (
                macro.name,
                macro.start_hotkey or "—",
                loops,
                str(len(macro.actions)),
                start_state,
            )
            for column, value in enumerate(values):
                self._table.setItem(row, column, QTableWidgetItem(value))
        self._refresh_run_state()

    def _refresh_run_state(self) -> None:
        """Enable/disable and relabel the Pause button for the selection."""
        index = self._selected_index()
        if index is None or self._engine is None:
            self._pause_button.setEnabled(False)
            self._pause_button.setText("⏸ Pause")
            return
        name = self._macros[index].name
        running = self._engine.is_macro_running(name)
        self._pause_button.setEnabled(running)
        if not running:
            self._pause_button.setText("⏸ Pause")
            return
        gated = False
        token = self._engine.running_macros().get(name)
        if token is not None:
            gated = token.gated
        paused = self._engine.is_macro_paused(name)
        self._pause_button.setText("▶ Unpause" if paused else "⏸ Pause")
        if paused and gated:
            self._pause_button.setToolTip("A pause rule is on screen; it releases when the rule clears.")
