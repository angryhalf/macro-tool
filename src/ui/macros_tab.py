"""Macros tab: list, create, edit, run and stop macro sequences.

The user turns a macro on and off (Run / Stop) -- that is the only macro
state there is; there is no pause button and no start-paused option.
Stop-trigger/start-trigger are ordinary *actions* inside each macro's action
list that trigger or untrigger other actions based on screen rules; the engine
watches the screen for those rules automatically while the macro runs.
"""

from __future__ import annotations

from collections.abc import Callable

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

from app.settings import MacroConfig, new_macro_uid
from ui.macro_editor import MacroEditorDialog


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

        self._table = QTableWidget(0, 4)
        self._table.setHorizontalHeaderLabels(["Name", "Hotkey", "Loops", "Actions"])
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
        self._stop_button = QPushButton("■ Stop selected macro")
        self._stop_button.setStyleSheet(
            "background-color:#c0392b; color:white; font-weight:bold;"
        )
        self._stop_button.clicked.connect(self._stop_selected)
        self._status = QLabel("")
        run_row.addWidget(self._run_button)
        run_row.addWidget(self._stop_button)
        run_row.addWidget(self._status)
        run_row.addStretch()

        layout = QVBoxLayout(self)
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
        self._table.setRowCount(len(self._macros))
        for row, macro in enumerate(self._macros):
            loops = "∞" if macro.repeat else str(macro.loops)
            values = (
                macro.name,
                macro.start_hotkey or "—",
                loops,
                str(len(macro.actions)),
            )
            for column, value in enumerate(values):
                self._table.setItem(row, column, QTableWidgetItem(value))
        self._refresh_run_state()

    def _refresh_run_state(self) -> None:
        """Mark running macros with ▶ and keep their name cell truthful."""
        if self._engine is None:
            return
        running = set(self._engine.running_macros())
        self._last_running = frozenset(running)
        for row, macro in enumerate(self._macros):
            item = self._table.item(row, 0)
            if item is None:
                continue
            base = macro.name
            item.setText(f"▶ {base}" if macro.name in running else base)

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
