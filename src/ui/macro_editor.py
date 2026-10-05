"""Macro editor: actions, pause/unpause flow steps and input recording.

* :class:`ActionEditorDialog` -- edit one step of a macro's action list.  On
  top of the keyboard/mouse kinds there are three *flow-control* actions that
  carry a screen condition (built with :class:`ConditionEditor` from
  :mod:`ui.condition_editor`):

  - **Wait until** (``wait_for``) blocks until the rule holds on screen.
  - **Pause when** (``pause``) opens a stretch whose actions are held back
    while the rule is present on screen.
  - **Unpause when** (``unpause``) closes that stretch (and can itself wait
    for its rule before execution continues).

  Every action type shows only the fields it needs, and *Record* buttons
  capture live keyboard/mouse input through pynput.
* :class:`MacroRecorderDialog` -- record an entire input session (keys,
  clicks, moves, scrolls, timings) into an ordered action list in one go.
* :class:`MacroEditorDialog` -- edit a macro: metadata (including whether it
  starts paused), plus its action table.
"""

from __future__ import annotations

import threading
import time
from typing import Callable

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.conditions import ScreenCondition
from app.settings import CONDITION_ACTION_KINDS, ActionConfig, MacroConfig
from services.input import InputRecorder, current_mouse_position
from ui.condition_editor import ConditionEditor
from ui.widgets import HotkeyButton

#: Human label -> action kind understood by ``services.input.perform_action``
#: (the last three are flow-control kinds handled by the macro engine).
ACTION_KINDS: dict[str, str] = {
    "Press key": "key",
    "Hold key": "hold_key",
    "Hotkey combo": "combo",
    "Type text": "type",
    "Move mouse": "move",
    "Click": "click",
    "Double click": "double_click",
    "Mouse down": "mouse_down",
    "Mouse up": "mouse_up",
    "Drag": "drag",
    "Scroll": "scroll",
    "Wait": "wait",
    "Wait until (screen)": "wait_for",
    "Pause when (screen)": "pause",
    "Unpause when (screen)": "unpause",
}

BUTTONS = ["left", "right", "middle"]

_KEY_FIELD_HINT = "One press of the key when the macro runs (e.g. a, space, f5, enter)."

_CONDITION_HINTS: dict[str, str] = {
    "wait_for": (
        "Execution stops here until this screen event appears; the optional "
        "give-up timeout ends the macro if it never happens."
    ),
    "pause": (
        "Opens a gated stretch: every action written between this step and an "
        "'Unpause when' step is held back while this screen event is present, "
        "and resumes as soon as it clears.  Without a rule it simply waits "
        "for you to press Unpause."
    ),
    "unpause": (
        "Closes the gated stretch opened by the last 'Pause when'.  With a "
        "rule attached, execution stays held until that screen event appears; "
        "without one it continues immediately."
    ),
}


class _FieldRow(QHBoxLayout):
    """A line edit plus a 'record' button on the same row."""

    def __init__(self, editor: QWidget, button: QWidget) -> None:
        super().__init__()
        self.addWidget(editor, 1)
        self.addWidget(button)


class ActionEditorDialog(QDialog):
    """Modal editor for one :class:`ActionConfig`.

    A :class:`QStackedWidget` holds one page per action kind so only the
    relevant fields are visible.  Recording buttons use short countdowns or
    background pynput listeners so you can focus the window you actually
    want to send input to.
    """

    def __init__(self, action: ActionConfig | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Macro action")
        self.setMinimumWidth(440)
        action = action or ActionConfig()

        self._recorder: InputRecorder | None = None

        self._kind = QComboBox()
        for label, kind in ACTION_KINDS.items():
            self._kind.addItem(label, kind)
        index = self._kind.findData(action.kind)
        self._kind.setCurrentIndex(index if index >= 0 else 0)

        self._pages = QStackedWidget()
        self._page_for_kind: dict[str, QWidget] = {}
        self._condition_editors: dict[str, ConditionEditor] = {}
        # One shared screen-rule editor reused by every flow-control kind.
        # Built *before* the per-kind pages so their default values can read
        # the action being edited (e.g. a wait_for's give-up timeout).
        self._condition_page, self._shared_condition = self._build_shared_condition_page(action)
        self._add_page("key", self._build_key_page(action))
        self._add_page("hold_key", self._build_hold_page(action))
        self._add_page("combo", self._build_combo_page(action))
        self._add_page("type", self._build_type_page(action))
        self._add_page("move", self._build_move_page(action))
        self._add_page("click", self._build_click_page(action))
        self._add_page("double_click", self._build_click_page(action, double=True))
        self._add_page("mouse_down", self._build_button_page("down"))
        self._add_page("mouse_up", self._build_button_page("up"))
        self._add_page("drag", self._build_drag_page(action))
        self._add_page("scroll", self._build_scroll_page(action))
        self._add_page("wait", self._build_wait_page(action))
        # All three flow-control kinds share the single condition page above;
        # it is registered for each kind so the stacked widget can show it.
        for kind in ("wait_for", "pause", "unpause"):
            self._add_page(kind, self._condition_page)
        # The dict keeps its original (per-kind editor) contract: every kind
        # maps to the same shared editor instance.
        for kind in ("wait_for", "pause", "unpause"):
            self._condition_editors[kind] = self._shared_condition

        self._kind.currentIndexChanged.connect(self._show_page)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        form = QFormLayout()
        form.addRow("Type:", self._kind)
        layout.addLayout(form)
        layout.addWidget(self._pages)
        layout.addWidget(buttons)
        self._show_page()

    # ------------------------------------------------------------------
    # Page construction
    # ------------------------------------------------------------------
    def _add_page(self, kind: str, page: QWidget) -> None:
        self._pages.addWidget(page)
        self._page_for_kind[kind] = page

    @staticmethod
    def _hint(text: str) -> QLabel:
        label = QLabel(text)
        label.setStyleSheet("color: gray;")
        label.setWordWrap(True)
        return label

    def _build_key_page(self, action: ActionConfig) -> QWidget:
        page = QWidget()
        self._key = QLineEdit(action.key)
        self._key.setPlaceholderText("e.g. a, space, f5, enter")
        record = QPushButton("Record key…")
        record.clicked.connect(lambda: self._record_single_key(self._key))
        layout = QVBoxLayout(page)
        layout.addLayout(_FieldRow(self._key, record))
        layout.addWidget(self._hint(_KEY_FIELD_HINT))
        return page

    def _build_hold_page(self, action: ActionConfig) -> QWidget:
        page = self._build_key_page(action)
        self._hold_duration = QSpinBox(minimum=1, maximum=600_000, value=max(1, action.duration_ms))
        self._hold_duration.setSuffix(" ms")
        record = QPushButton("Record hold…")
        record.clicked.connect(self._record_hold)
        form = QFormLayout()
        form.addRow("Hold for:", self._hold_duration)
        form.addRow("", record)
        page.layout().insertLayout(1, form)
        page.layout().insertWidget(2, self._hint("Tip: recording captures how long you hold the key."))
        return page

    def _build_combo_page(self, action: ActionConfig) -> QWidget:
        page = QWidget()
        self._combo = QLineEdit(action.combo)
        self._combo.setPlaceholderText("e.g. ctrl+c")
        record = QPushButton("Record combo…")
        record.clicked.connect(self._record_combo)
        layout = QVBoxLayout(page)
        layout.addLayout(_FieldRow(self._combo, record))
        layout.addWidget(self._hint("Press modifiers together with a main key while recording."))
        return page

    def _build_type_page(self, action: ActionConfig) -> QWidget:
        page = QWidget()
        self._text = QLineEdit(action.text)
        self._text.setPlaceholderText("The exact string to type")
        self._type_delay = QSpinBox(minimum=0, maximum=5000, value=action.amount if action.amount > 0 else 30)
        self._type_delay.setSuffix(" ms/char")
        record = QPushButton("Record typing…")
        record.clicked.connect(self._record_typing)
        form = QFormLayout(page)
        form.addRow("Text:", self._text)
        form.addRow("Speed:", self._type_delay)
        form.addRow("", record)
        form.addRow(self._hint("Recording stops when you press Enter."))
        return page

    def _build_move_page(self, action: ActionConfig) -> QWidget:
        page = QWidget()
        self._move_x, self._move_y = self._position_spins(action)
        record = QPushButton("Record position…")
        record.clicked.connect(lambda: self._capture_after_countdown(self._move_x, self._move_y))
        form = QFormLayout(page)
        form.addRow("Position X:", self._move_x)
        form.addRow("Position Y:", self._move_y)
        form.addRow("", record)
        return page

    def _build_click_page(self, action: ActionConfig, *, double: bool = False) -> QWidget:
        page = QWidget()
        self._click_button = QComboBox()
        self._click_button.addItems(BUTTONS)
        self._click_button.setCurrentText(action.button)
        self._click_x, self._click_y = self._position_spins(action)
        self._click_at_position = QCheckBox("Click at specific position")
        self._click_at_position.setChecked(action.x is not None)
        self._click_at_position.toggled.connect(self._click_x.setEnabled)
        self._click_at_position.toggled.connect(self._click_y.setEnabled)
        self._click_x.setEnabled(action.x is not None)
        self._click_y.setEnabled(action.y is not None)
        record = QPushButton("Record click…")
        record.clicked.connect(lambda: self._record_click(self._click_button, self._click_x, self._click_y))
        form = QFormLayout(page)
        form.addRow("Mouse button:", self._click_button)
        form.addRow("", self._click_at_position)
        form.addRow("Position X:", self._click_x)
        form.addRow("Position Y:", self._click_y)
        form.addRow("", record)
        if double:
            form.addRow(self._hint("Sends two rapid clicks when the macro runs."))
        return page

    def _build_button_page(self, direction: str) -> QWidget:
        page = QWidget()
        box = QComboBox()
        box.addItems(BUTTONS)
        setattr(self, f"_button_{direction}", box)
        record = QPushButton(f"Record which button you press…")
        record.clicked.connect(lambda d=direction, b=box: self._record_button(d, b))
        form = QFormLayout(page)
        form.addRow("Mouse button:", box)
        form.addRow("", record)
        form.addRow(
            self._hint("Pair Mouse down / Mouse up around other actions to hold or drag with a button.")
        )
        return page

    def _build_drag_page(self, action: ActionConfig) -> QWidget:
        page = QWidget()
        self._drag_button = QComboBox()
        self._drag_button.addItems(BUTTONS)
        self._drag_button.setCurrentText(action.button)
        self._drag_from_x = QSpinBox(minimum=-32000, maximum=32000, value=action.x or 0)
        self._drag_from_y = QSpinBox(minimum=-32000, maximum=32000, value=action.y or 0)
        self._drag_to_x = QSpinBox(minimum=-32000, maximum=32000, value=action.amount)
        self._drag_to_y = QSpinBox(minimum=-32000, maximum=32000, value=action.duration_ms)
        from_record = QPushButton("Record start…")
        from_record.clicked.connect(
            lambda: self._capture_after_countdown(self._drag_from_x, self._drag_from_y)
        )
        to_record = QPushButton("Record end…")
        to_record.clicked.connect(lambda: self._capture_after_countdown(self._drag_to_x, self._drag_to_y))
        form = QFormLayout(page)
        form.addRow("Mouse button:", self._drag_button)
        form.addRow("From X:", self._drag_from_x)
        form.addRow("From Y:", self._drag_from_y)
        form.addRow("", from_record)
        form.addRow("To X:", self._drag_to_x)
        form.addRow("To Y:", self._drag_to_y)
        form.addRow("", to_record)
        form.addRow(self._hint("Presses the button at the start point, moves smoothly, releases at the end."))
        return page

    def _build_scroll_page(self, action: ActionConfig) -> QWidget:
        page = QWidget()
        self._scroll_amount = QSpinBox(minimum=-100, maximum=100, value=action.amount)
        form = QFormLayout(page)
        form.addRow("Amount (+up / −down):", self._scroll_amount)
        return page

    def _build_wait_page(self, action: ActionConfig) -> QWidget:
        page = QWidget()
        self._wait_duration = QSpinBox(minimum=0, maximum=600_000, value=action.duration_ms)
        self._wait_duration.setSuffix(" ms")
        form = QFormLayout(page)
        form.addRow("Wait:", self._wait_duration)
        return page

    def _build_shared_condition_page(self, action: ActionConfig) -> tuple[QWidget, ConditionEditor]:
        """Page for the flow-control actions (``wait_for``/``pause``/``unpause``).

        All three kinds share this one page -- a single :class:`ConditionEditor`
        so the screen rule lives *on the action itself* and stays intact while
        the user flips between the three entries in the Type combo.  The hint
        text at the top swaps per kind (see :meth:`_show_page`) and the
        give-up timeout row is only shown for ``wait_for``.
        """
        page = QWidget()
        layout = QVBoxLayout(page)
        self._condition_hint = QLabel(_CONDITION_HINTS["wait_for"])
        self._condition_hint.setWordWrap(True)
        self._condition_hint.setStyleSheet("color: gray;")
        layout.addWidget(self._condition_hint)
        editor = ConditionEditor(show_timeout=True)
        # Seed defaults from the action being edited (its own condition plus
        # the wait_for give-up timeout stored in duration_ms).
        editor.load(action.condition if action.condition is not None else ScreenCondition(template_path=""))
        editor.timeout_spin.setValue(max(0, min(editor.timeout_spin.maximum(), action.duration_ms)))
        self._shared_condition = editor
        layout.addWidget(editor)
        return page, editor

    @staticmethod
    def _position_spins(action: ActionConfig) -> tuple[QSpinBox, QSpinBox]:
        x = QSpinBox(minimum=-32000, maximum=32000, value=action.x or 0)
        y = QSpinBox(minimum=-32000, maximum=32000, value=action.y or 0)
        return x, y

    # ------------------------------------------------------------------
    # Navigation between pages
    # ------------------------------------------------------------------
    def _show_page(self) -> None:
        kind = self._kind.currentData()
        if kind in _CONDITION_HINTS:  # a flow-control action
            self._condition_hint.setText(_CONDITION_HINTS[kind])
            self._shared_condition.set_timeout_visible(kind == "wait_for")
        page = self._page_for_kind.get(kind)
        if page is not None:
            self._pages.setCurrentWidget(page)

    # ------------------------------------------------------------------
    # Recording helpers
    # ------------------------------------------------------------------
    def _capture_after_countdown(self, x_spin: QSpinBox, y_spin: QSpinBox, seconds: int = 2) -> None:
        """Write the pointer position into *x_spin*/*y_spin* after a countdown.

        The countdown lets you move the mouse to the desired spot first.
        """
        remaining = {"count": seconds}
        x_spin.setEnabled(False)
        y_spin.setEnabled(False)

        def tick() -> None:
            remaining["count"] -= 1
            if remaining["count"] > 0:
                x_spin.setToolTip(f"Capturing in {remaining['count']}s — move the mouse…")
                QTimer.singleShot(1000, tick)
                return
            pos_x, pos_y = current_mouse_position()
            x_spin.setValue(pos_x)
            y_spin.setValue(pos_y)
            x_spin.setToolTip("")
            y_spin.setToolTip("")
            x_spin.setEnabled(True)
            y_spin.setEnabled(True)

        x_spin.setToolTip(f"Capturing in {seconds}s — move the mouse…")
        QTimer.singleShot(1000, tick)

    def _start_recording_window(
        self,
        title: str,
        timeout_s: float,
        on_press: Callable[[str], bool] | None = None,
        on_release: Callable[[str], bool] | None = None,
    ) -> None:
        """Listen globally until a handler returns True (or *timeout_s* elapses).

        Handlers run on the pynput listener thread; they must only touch Qt
        widgets through thread-safe calls (``setText``/``setValue`` are fine
        because we stop via :meth:`QTimer.singleShot` back on the GUI thread).
        """
        if self._recorder is not None and self._recorder.running:
            return

        def dispatch(handler: Callable[[str], bool] | None, name: str) -> None:
            if handler is None:
                return
            try:
                done = handler(name)
            except Exception:  # pragma: no cover - defensive
                done = True
            if done:
                QTimer.singleShot(0, self._stop_recording_window)

        self._recorder = InputRecorder(
            on_press=lambda name: dispatch(on_press, name),
            on_release=lambda name: dispatch(on_release, name),
        ).start()
        self.setWindowTitle(title)
        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.timeout.connect(self._stop_recording_window)
        timer.start(int(timeout_s * 1000))
        self._record_timeout = timer

    def _stop_recording_window(self) -> None:
        timer = getattr(self, "_record_timeout", None)
        if timer is not None:
            timer.stop()
            self._record_timeout = None
        if self._recorder is not None:
            self._recorder.stop()
            self._recorder = None
        self.setWindowTitle("Macro action")

    def _record_single_key(self, target: QLineEdit) -> None:
        """Capture exactly one non-modifier key press into *target*."""
        def handle(name: str) -> bool:
            if name.startswith(("ctrl", "alt", "shift", "cmd")):
                return False
            target.setText(name)
            return True

        self._start_recording_window("Recording — press one key…", 10.0, on_press=handle)

    def _record_combo(self) -> None:
        """Capture modifiers + one main key, e.g. ``ctrl+shift+d``."""
        pressed: set[str] = set()

        def handle(name: str) -> bool:
            pressed.add(name)
            if name.startswith(("ctrl", "alt", "shift", "cmd")):
                return False
            modifiers = sorted(p for p in pressed if p.startswith(("ctrl", "alt", "shift", "cmd")))
            base = name.split("_")[0] if "_" in name else name
            self._combo.setText("+".join(modifiers + [base]))
            return True

        self._start_recording_window("Recording — press a combo (e.g. Ctrl+C)…", 10.0, on_press=handle)

    def _record_hold(self) -> None:
        """Measure how long the user holds the next key (press *and* release)."""
        state: dict[str, float] = {}

        def on_press(name: str) -> bool:
            if name.startswith(("ctrl", "alt", "shift", "cmd")):
                return False
            state["press_at"] = time.monotonic()
            self._key.setText(name)
            self.setWindowTitle("Recording — release the key…")
            return False  # keep listening for the release event

        def on_release(name: str) -> bool:
            if "press_at" not in state:
                return False
            held_ms = max(1, int((time.monotonic() - state["press_at"]) * 1000))
            self._hold_duration.setValue(held_ms)
            return True

        self._start_recording_window(
            "Recording — press and hold a key…", 10.0, on_press=on_press, on_release=on_release
        )

    def _record_typing(self) -> None:
        """Capture typed characters until the user presses Enter."""
        buffer: list[str] = []

        def handle(name: str) -> bool:
            if name == "enter":
                if buffer:
                    self._text.setText("".join(buffer))
                return True
            if len(name) == 1:
                buffer.append(name)
            elif name == "space":
                buffer.append(" ")
            elif name == "backspace" and buffer:
                buffer.pop()
            return False

        self._start_recording_window("Recording typing — press Enter to finish…", 30.0, on_press=handle)

    def _record_click(
        self, button_box: QComboBox, x_spin: QSpinBox, y_spin: QSpinBox
    ) -> None:
        """Capture the next mouse click: which button and where."""

        def on_button(button: str) -> None:
            button_box.setCurrentText(button)
            if x_spin.isEnabled():
                pos_x, pos_y = current_mouse_position()
                x_spin.setValue(pos_x)
                y_spin.setValue(pos_y)
            QTimer.singleShot(0, self._stop_recording_window)

        self._start_mouse_recording("Recording — perform a click…", on_button)

    def _record_button(self, direction: str, button_box: QComboBox) -> None:
        """Capture which physical mouse button the user presses next."""

        def on_button(button: str) -> None:
            button_box.setCurrentText(button)
            QTimer.singleShot(0, self._stop_recording_window)

        self._start_mouse_recording("Recording — press a mouse button…", on_button)

    def _start_mouse_recording(self, title: str, on_button: Callable[[str], None]) -> None:
        """Listen for the next mouse-button press, then stop automatically."""
        if self._recorder is not None and self._recorder.running:
            return
        self._recorder = InputRecorder(on_button_press=on_button).start()
        self.setWindowTitle(title)
        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.timeout.connect(self._stop_recording_window)
        timer.start(10_000)
        self._record_timeout = timer

    def reject(self) -> None:
        self._stop_recording_window()
        super().reject()

    def accept(self) -> None:
        self._stop_recording_window()
        super().accept()

    # ------------------------------------------------------------------
    # Result
    # ------------------------------------------------------------------
    def action(self) -> ActionConfig:
        """Build the :class:`ActionConfig` described by the current page."""
        kind = self._kind.currentData()
        if kind == "key":
            return ActionConfig(kind=kind, key=self._key.text().strip().lower())
        if kind == "hold_key":
            return ActionConfig(
                kind=kind, key=self._key.text().strip().lower(), duration_ms=self._hold_duration.value()
            )
        if kind == "combo":
            return ActionConfig(kind=kind, combo=self._combo.text().strip().lower())
        if kind == "type":
            return ActionConfig(kind=kind, text=self._text.text(), amount=self._type_delay.value())
        if kind == "move":
            return ActionConfig(kind=kind, x=self._move_x.value(), y=self._move_y.value())
        if kind in ("click", "double_click"):
            at_position = self._click_at_position.isChecked()
            return ActionConfig(
                kind=kind,
                button=self._click_button.currentText(),
                x=self._click_x.value() if at_position else None,
                y=self._click_y.value() if at_position else None,
            )
        if kind in ("mouse_down", "mouse_up"):
            box: QComboBox = getattr(self, f"_button_{kind.split('_')[1]}")
            return ActionConfig(kind=kind, button=box.currentText())
        if kind == "drag":
            return ActionConfig(
                kind=kind,
                button=self._drag_button.currentText(),
                x=self._drag_from_x.value(),
                y=self._drag_from_y.value(),
                amount=self._drag_to_x.value(),
                duration_ms=self._drag_to_y.value(),
            )
        if kind == "scroll":
            return ActionConfig(kind=kind, amount=self._scroll_amount.value())
        if kind == "wait":
            return ActionConfig(kind=kind, duration_ms=self._wait_duration.value())
        if kind in CONDITION_ACTION_KINDS:
            editor = self._condition_editors.get(kind)
            condition = editor.build() if editor is not None else None
            duration_ms = 0
            if condition is not None and not condition.configured:
                condition = None
            elif kind == "wait_for" and condition is not None:
                # Give-up timeout lives on the action's duration_ms field.
                duration_ms = condition.timeout_ms
            return ActionConfig(kind=kind, condition=condition, duration_ms=duration_ms)
        return ActionConfig(kind=kind)


# ----------------------------------------------------------------------
# Whole-macro recording
# ----------------------------------------------------------------------
def _canonical(name: str) -> str:
    """Map pynput side-specific names onto their generic alias (ctrl_l->ctrl)."""
    return name.rsplit("_", 1)[0] if name.endswith(("_l", "_r")) else name


def _events_to_actions(events: list[tuple[float, str, dict]]) -> list[ActionConfig]:
    """Coalesce timestamped raw events into compact macro steps.

    Rules:
    * key press+release <= 250 ms apart -> Press key; longer -> Hold key.
    * consecutive mouse moves are thinned to the last position of each burst.
    * button press/release pairs shorter than 300 ms with little movement
      become Click; otherwise Mouse down + Move + Mouse up.
    * idle gaps between events become Wait steps (rounded to 10 ms).
    """
    actions: list[ActionConfig] = []
    pending_wait = 0.0
    previous_time = 0.0
    opened: dict[str, tuple[float, dict]] = {}
    move_buffer: tuple[int, int] | None = None

    def flush_wait() -> None:
        nonlocal pending_wait
        ms = int(round(pending_wait * 100) * 10)
        if ms >= 20:
            actions.append(ActionConfig(kind="wait", duration_ms=ms))
        pending_wait = 0.0

    def flush_move() -> None:
        nonlocal move_buffer
        if move_buffer is not None:
            actions.append(ActionConfig(kind="move", x=move_buffer[0], y=move_buffer[1]))
            move_buffer = None

    for stamp, kind, payload in events:
        pending_wait += stamp - previous_time
        previous_time = stamp

        if kind == "move":
            move_buffer = (payload["x"], payload["y"])
            continue

        if kind == "key_down":
            flush_move()
            flush_wait()
            opened[f"key:{payload['key']}"] = (stamp, payload)
            continue

        if kind == "key_up":
            pair = opened.pop(f"key:{payload['key']}", None)
            if pair is None:
                continue
            held_ms = int((stamp - pair[0]) * 1000)
            key = pair[1]["key"]
            if held_ms <= 250:
                actions.append(ActionConfig(kind="key", key=key))
            else:
                actions.append(ActionConfig(kind="hold_key", key=key, duration_ms=held_ms))
            pending_wait = 0.0
            continue

        if kind == "scroll":
            flush_move()
            flush_wait()
            actions.append(ActionConfig(kind="scroll", amount=payload["amount"]))
            continue

        if kind == "button_down":
            flush_move()
            flush_wait()
            opened[f"button:{payload['button']}"] = (stamp, payload)
            continue

        if kind == "button_up":
            pair = opened.pop(f"button:{payload['button']}", None)
            if pair is None:
                continue
            held_ms = int((stamp - pair[0]) * 1000)
            moved = abs(payload["x"] - pair[1]["x"]) + abs(payload["y"] - pair[1]["y"])
            button = payload["button"]
            if held_ms <= 300 and moved <= 5:
                actions.append(ActionConfig(kind="click", button=button))
            else:
                actions.append(ActionConfig(kind="mouse_down", button=button))
                actions.append(ActionConfig(kind="move", x=payload["x"], y=payload["y"]))
                actions.append(ActionConfig(kind="mouse_up", button=button))
            pending_wait = 0.0
            continue

    flush_move()
    flush_wait()
    return actions


class MacroRecorderDialog(QDialog):
    """Record all keyboard/mouse input into an ordered action list.

    Start the recording, work normally for a while, then press the global
    stop hotkey (default ``F8``) or click *Stop*.  Raw events are shown live
    and converted into compact macro steps when accepted.
    """

    def __init__(self, stop_hotkey: str = "f8", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Record macro inputs")
        self.setMinimumSize(560, 400)

        self._stop_hotkey = _canonical(stop_hotkey.strip().lower().split("+")[-1])
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
        self._started_at = time.monotonic()
        self._record_button.setText("■ Stop recording")
        self._status.setText(f"Recording… press the stop hotkey ({self._stop_hotkey.upper()}) when done.")
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
        if name == self._stop_hotkey:
            QTimer.singleShot(0, self._stop)
            return
        self._append("key_down", {"key": _canonical(name)})

    def _on_release(self, name: str) -> None:
        self._append("key_up", {"key": _canonical(name)})

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
        with self._lock:
            events = list(self._events)
        self._count_label.setText(f"{len(events)} events")
        scroll_to_bottom = self._table.verticalScrollBar()
        at_bottom = scroll_to_bottom.value() >= scroll_to_bottom.maximum() - 4
        self._table.setRowCount(len(events))
        for row, (stamp, kind, payload) in enumerate(events):
            self._table.setItem(row, 0, QTableWidgetItem(str(row + 1)))
            self._table.setItem(row, 1, QTableWidgetItem(f"{stamp:.2f}s"))
            self._table.setItem(row, 2, QTableWidgetItem(_describe_event(kind, payload)))
        if at_bottom:
            scroll_to_bottom.setValue(scroll_to_bottom.maximum())

    def actions(self) -> list[ActionConfig]:
        """Convert the recorded events into a compact action sequence."""
        with self._lock:
            events = list(self._events)
        return _events_to_actions(events)


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


# ----------------------------------------------------------------------
# Macro editor
# ----------------------------------------------------------------------
class MacroEditorDialog(QDialog):
    """Edit one macro.

    Layout top-to-bottom:

    * **General** -- name, start hotkey, whether the macro starts paused or
      runs immediately, and repeat/loops/interval.  The user turns the macro
      on and off; nothing else stops it.
    * **Actions** -- an ordered table of steps with add/edit/remove/reorder
      controls plus a whole-session recorder.  Pause/unpause are *actions*:
      insert a "Pause when" step to open a gated stretch and an "Unpause
      when" step to close it -- everything written between them is held back
      while the pause step's screen rule is present on screen.
    """

    def __init__(
        self,
        macro: MacroConfig,
        parent: QWidget | None = None,
        stop_hotkey: str = "f8",
    ) -> None:
        super().__init__(parent)
        self._stop_hotkey = stop_hotkey
        self.setWindowTitle(f"Edit macro — {macro.name}")
        self.setMinimumSize(680, 620)

        # -- general ---------------------------------------------------
        self._name = QLineEdit(macro.name)
        self._hotkey = HotkeyButton(macro.start_hotkey)
        self._start_paused = QCheckBox("Start paused (wait for Unpause before acting)")
        self._start_paused.setChecked(macro.start_paused)
        self._repeat = QCheckBox("Repeat until stopped")
        self._repeat.setChecked(macro.repeat)
        self._loops = QSpinBox(minimum=1, maximum=9999, value=max(1, macro.loops))
        self._loops.setEnabled(not macro.repeat)
        self._repeat.toggled.connect(self._loops.setDisabled)
        self._interval = QSpinBox(minimum=0, maximum=600_000, value=macro.interval_ms)
        self._interval.setSuffix(" ms")

        meta_form = QFormLayout()
        meta_form.addRow("Name:", self._name)
        meta_form.addRow("Start hotkey:", self._hotkey)
        meta_form.addRow("Start state:", self._start_paused)
        meta_form.addRow("Repeat:", self._repeat)
        meta_form.addRow("Loops:", self._loops)
        meta_form.addRow("Loop interval:", self._interval)
        meta_box = QGroupBox("General")
        meta_box.setLayout(meta_form)

        # -- actions ---------------------------------------------------
        self._table = QTableWidget(0, 2)
        self._table.setHorizontalHeaderLabels(["Action", "Details"])
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.doubleClicked.connect(self._edit_action)

        toolbar = QHBoxLayout()
        for text, slot in (
            ("Add", self._add_action),
            ("Edit", self._edit_action),
            ("Remove", self._remove_action),
            ("Up", lambda: self._move(-1)),
            ("Down", lambda: self._move(+1)),
        ):
            button = QPushButton(text)
            button.clicked.connect(slot)
            toolbar.addWidget(button)
        toolbar.addStretch()
        record_button = QPushButton("🎥 Record inputs…")
        record_button.clicked.connect(self._record_inputs)
        toolbar.addWidget(record_button)

        actions_box = QGroupBox("Actions")
        actions_layout = QVBoxLayout(actions_box)
        actions_layout.addLayout(toolbar)
        actions_layout.addWidget(self._table)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(meta_box)
        layout.addWidget(actions_box, 1)
        layout.addWidget(buttons)

        self._actions: list[ActionConfig] = list(macro.actions)
        self._reload_table()

    # ------------------------------------------------------------------
    # Action table
    # ------------------------------------------------------------------
    @staticmethod
    def describe(action: ActionConfig) -> tuple[str, str]:
        """Human-readable (type, details) pair for the action table."""
        where = "" if action.x is None else f" at ({action.x}, {action.y})"
        cond = MacroEditorDialog._describe_condition(action)
        labels = {
            "key": ("Press key", action.key),
            "hold_key": ("Hold key", f"{action.key} for {action.duration_ms} ms"),
            "combo": ("Hotkey combo", action.combo),
            "type": ("Type text", f'"{action.text}"'),
            "move": ("Move mouse", f"({action.x}, {action.y})"),
            "click": ("Click", action.button + where),
            "double_click": ("Double click", action.button + where),
            "mouse_down": ("Mouse down", action.button),
            "mouse_up": ("Mouse up", action.button),
            "drag": (
                "Drag",
                f"{action.button}: ({action.x}, {action.y}) → ({action.amount}, {action.duration_ms})",
            ),
            "scroll": ("Scroll", str(action.amount)),
            "wait": ("Wait", f"{action.duration_ms} ms"),
            "wait_for": ("Wait until", cond),
            "pause": ("Pause when", cond),
            "unpause": ("Unpause when", cond),
        }
        return labels.get(action.kind, (action.kind, ""))

    @staticmethod
    def _describe_condition(action: ActionConfig) -> str:
        """Short summary of a flow action's screen rule for the table."""
        condition = action.condition
        if condition is None or not condition.configured:
            return "no rule configured"
        parts = [condition.description]
        if action.duration_ms:
            parts.append(f"gives up after {action.duration_ms} ms")
        return ", ".join(part for part in parts if part) or "rule configured"

    def _reload_table(self) -> None:
        self._table.setRowCount(len(self._actions))
        for row, action in enumerate(self._actions):
            name, details = self.describe(action)
            self._table.setItem(row, 0, QTableWidgetItem(name))
            self._table.setItem(row, 1, QTableWidgetItem(details))

    def _selected_row(self) -> int:
        return self._table.currentRow()

    def _add_action(self) -> None:
        dialog = ActionEditorDialog(parent=self)
        if dialog.exec():
            self._actions.append(dialog.action())
            self._reload_table()

    def _edit_action(self) -> None:
        row = self._selected_row()
        if row < 0:
            return
        dialog = ActionEditorDialog(self._actions[row], parent=self)
        if dialog.exec():
            self._actions[row] = dialog.action()
            self._reload_table()

    def _remove_action(self) -> None:
        row = self._selected_row()
        if row >= 0:
            del self._actions[row]
            self._reload_table()

    def _move(self, delta: int) -> None:
        row = self._selected_row()
        target = row + delta
        if 0 <= row < len(self._actions) and 0 <= target < len(self._actions):
            self._actions[row], self._actions[target] = self._actions[target], self._actions[row]
            self._reload_table()
            self._table.selectRow(target)

    def _record_inputs(self) -> None:
        dialog = MacroRecorderDialog(stop_hotkey=self._stop_hotkey, parent=self)
        if dialog.exec():
            recorded = dialog.actions()
            if recorded:
                self._actions.extend(recorded)
                self._reload_table()

    # ------------------------------------------------------------------
    def macro(self) -> MacroConfig:
        """Build the :class:`MacroConfig` described by this dialog."""
        return MacroConfig(
            name=self._name.text().strip() or "Unnamed macro",
            start_hotkey=self._hotkey.hotkey,
            repeat=self._repeat.isChecked(),
            loops=self._loops.value(),
            interval_ms=self._interval.value(),
            actions=tuple(self._actions),
            start_paused=self._start_paused.isChecked(),
        )
