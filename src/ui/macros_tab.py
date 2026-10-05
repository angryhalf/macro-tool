"""Macros tab: list, create, edit and run macro sequences."""

from __future__ import annotations

from typing import Callable

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
    """Table of all macros with add/edit/remove/run controls."""

    def __init__(
        self,
        on_changed: Callable[[list[MacroConfig]], None],
        on_run: Callable[[MacroConfig], None],
        stop_hotkey_provider: Callable[[], str] = lambda: "f8",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._on_changed = on_changed
        self._on_run = on_run
        self._stop_hotkey_provider = stop_hotkey_provider
        self._macros: list[MacroConfig] = []

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
        self._status = QLabel("")
        run_row.addWidget(self._run_button)
        run_row.addWidget(self._status)
        run_row.addStretch()

        layout = QVBoxLayout(self)
        layout.addLayout(toolbar)
        layout.addWidget(self._table)
        layout.addLayout(run_row)

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
            values = (macro.name, macro.start_hotkey or "—", loops, str(len(macro.actions)))
            for column, value in enumerate(values):
                self._table.setItem(row, column, QTableWidgetItem(value))
