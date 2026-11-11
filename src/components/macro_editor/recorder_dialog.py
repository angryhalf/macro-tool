"""Whole-macro input recording dialog.

:class:`MacroRecorderDialog` records all keyboard/mouse input into an
ordered action list: start the recording, work normally for a while, then
press the global stop hotkey (default ``F8``) or click *Stop*.  Raw events
are shown live and converted into compact macro steps when accepted.

The event -> action conversion itself is pure domain logic and lives in
:mod:`components.macro.recording` (headless-testable); this module only
captures events and displays them.  The pynput listener threads never touch
Qt widgets directly: stopping is requested through the
:attr:`_stop_recording_requested` signal, which is delivered on the GUI
thread.
"""

from __future__ import annotations

import threading
import time

from PySide6.QtCore import QTimer, Signal
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from common.events import describe_hotkey, sort_combo
from components.macro.macro import ActionConfig
from components.macro.recording import events_to_actions
from components.input import InputRecorder, current_mouse_position


def _canonical(name: str) -> str:
    """Map pynput side-specific names onto their generic alias (ctrl_l->ctrl)."""
    return name.rsplit("_", 1)[0] if name.endswith(("_l", "_r")) else name


def _describe_event(kind: str, payload: dict) -> str:
    if kind == "key_down":
        return f"↓ {payload['key']}"
    if kind == "key_up":
        return f"↑ {payload['key']}"
    if kind == "move":
        return f"move → ({payload['x']}, {payload['y']})"
    if kind == "scroll":
        return f"scroll {payload['amount']:+d}"
    if kind == "button_down":
        return f"mouse down ({payload['button']}) at ({payload['x']}, {payload['y']})"
    if kind == "button_up":
        return f"mouse up ({payload['button']}) at ({payload['x']}, {payload['y']})"
    return kind


class MacroRecorderDialog(QDialog):
    """Record all keyboard/mouse input into an ordered action list.

    Start the recording, work normally for a while, then press the global
    stop hotkey (default ``F8``) or click *Stop*.  Raw events are shown live
    and converted into compact macro steps when accepted.

    The pynput listener threads never touch Qt widgets directly: stopping is
    requested through the :attr:`_stop_recording_requested` signal, which is
    delivered on the GUI thread.
    """

    #: Emitted from a pynput listener thread to request stopping on the GUI thread.
    _stop_recording_requested = Signal()

    def __init__(self, stop_hotkey: str = "f8", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._stop_recording_requested.connect(self._stop)
        self.setWindowTitle("Record macro inputs")
        self.setMinimumSize(560, 400)

        # Keep the *whole* normalized stop combination (e.g. ctrl+shift+s),
        # not just its final key -- otherwise plain 's' would also stop.
        self._stop_hotkey = sort_combo(stop_hotkey.strip().lower())
        self._stop_combo: frozenset[str] = frozenset(
            _canonical(part) for part in self._stop_hotkey.split("+") if part.strip()
        )
        self._pressed_keys: set[str] = set()
        self._events: list[tuple[float, str, dict]] = []
        self._lock = threading.Lock()
        self._recorder: InputRecorder | None = None
        self._started_at = 0.0

        self._status = QLabel("Press Start, perform your inputs, then press the stop hotkey.")
        self._status.setWordWrap(True)
        self._count_label = QLabel("0 events")

        self._table = QTableWidget(0, 3)
        self._table.setHorizontalHeaderLabels(["#", "Time", "Event"])
        self._table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)

        self._record_button = QPushButton("● Start recording")
        self._record_button.clicked.connect(self._toggle)
        top = QHBoxLayout()
        top.addWidget(self._record_button)
        top.addWidget(self._count_label)
        top.addStretch()

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        ok = buttons.button(QDialogButtonBox.StandardButton.Ok)
        if ok is not None:
            ok.setText("Use recording")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addWidget(self._status)
        layout.addWidget(self._table)
        layout.addWidget(buttons)

        self._refresh_timer = QTimer(self)
        self._refresh_timer.timeout.connect(self._refresh_table)

    # ------------------------------------------------------------------
    @property
    def recording(self) -> bool:
        return self._recorder is not None and self._recorder.running

    def _toggle(self) -> None:
        self._stop() if self.recording else self._start()

    def _start(self) -> None:
        with self._lock:
            self._events.clear()
        self._pressed_keys.clear()
        self._started_at = time.monotonic()
        self._record_button.setText("■ Stop recording")
        self._status.setText(
            f"Recording… press the stop hotkey ({describe_hotkey(self._stop_hotkey)}) when done."
        )
        self._refresh_timer.start(400)
        self._recorder = InputRecorder(
            on_press=self._on_press,
            on_release=self._on_release,
            on_move=self._on_move,
            on_scroll=self._on_scroll,
            on_button_press=self._on_button_press,
            on_button_release=self._on_button_release,
        ).start()

    def _stop(self) -> None:
        if self._recorder is not None:
            self._recorder.stop()
            self._recorder = None
        self._pressed_keys.clear()
        self._refresh_timer.stop()
        self._record_button.setText("● Start recording")
        if self._status.text().startswith("Recording"):
            self._status.setText("Recording stopped.")
        self._refresh_table()

    def accept(self) -> None:
        self._stop()
        super().accept()

    def reject(self) -> None:
        self._stop()
        super().reject()

    # ------------------------------------------------------------------
    # Event sinks (called from pynput listener threads)
    # ------------------------------------------------------------------
    def _append(self, kind: str, payload: dict) -> None:
        stamp = time.monotonic() - self._started_at
        with self._lock:
            self._events.append((stamp, kind, payload))

    def _on_press(self, name: str) -> None:
        # Runs on a pynput listener thread -- never touch Qt widgets/timers
        # here; stopping is requested via the _stop_recording_requested
        # signal, which is delivered on the GUI thread.
        canonical = _canonical(name)
        self._pressed_keys.add(canonical)
        if self._stop_combo.issubset(self._pressed_keys):
            self._stop_recording_requested.emit()
            return
        self._append("key_down", {"key": canonical})

    def _on_release(self, name: str) -> None:
        canonical = _canonical(name)
        self._pressed_keys.discard(canonical)
        if self._stop_combo.issubset(self._pressed_keys):
            self._stop_recording_requested.emit()
            return
        self._append("key_up", {"key": canonical})

    def _on_move(self, x: int, y: int) -> None:
        self._append("move", {"x": x, "y": y})

    def _on_scroll(self, amount: int) -> None:
        self._append("scroll", {"amount": amount})

    def _on_button_press(self, button: str) -> None:
        x, y = current_mouse_position()
        self._append("button_down", {"button": button, "x": x, "y": y})

    def _on_button_release(self, button: str) -> None:
        x, y = current_mouse_position()
        self._append("button_up", {"button": button, "x": x, "y": y})

    # ------------------------------------------------------------------
    def _refresh_table(self) -> None:
        """Show the recorded events, appending only rows that are new.

        The recorder's event list is append-only while recording, so a full
        rebuild every 400 ms was pure waste (and reset selection/scroll);
        track how many rows are already displayed and add just the delta.
        ``setRowCount`` shrinking handles the clear-on-restart case.
        """
        with self._lock:
            events = list(self._events)
        self._count_label.setText(f"{len(events)} events")
        scroll_bar = self._table.verticalScrollBar()
        at_bottom = scroll_bar.value() >= scroll_bar.maximum() - 4
        existing = self._table.rowCount()
        if len(events) < existing:
            self._table.setRowCount(len(events))
            existing = len(events)
        for row in range(existing, len(events)):
            stamp, kind, payload = events[row]
            self._table.insertRow(row)
            self._table.setItem(row, 0, QTableWidgetItem(str(row + 1)))
            self._table.setItem(row, 1, QTableWidgetItem(f"{stamp:.2f}s"))
            self._table.setItem(row, 2, QTableWidgetItem(_describe_event(kind, payload)))
        if at_bottom:
            scroll_bar.setValue(scroll_bar.maximum())

    def actions(self) -> list[ActionConfig]:
        """Convert the recorded events into a compact action sequence."""
        with self._lock:
            events = list(self._events)
        return events_to_actions(events)
