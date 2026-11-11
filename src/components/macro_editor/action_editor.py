"""Editor for one macro action (the per-kind dialog page builder).

:class:`ActionEditorDialog` edits a single :class:`~components.macro.macro.ActionConfig`.
On top of the keyboard/mouse kinds there are five *flow-control* actions:

- **Wait until** (``wait_for``) blocks until its rule holds; the rule can
  watch the screen, physical user input or another action executing
  (built with :class:`ConditionEditor` from :mod:`components.condition_editor`).
- **Stop trigger** (``stop_trigger``) opens a stretch whose actions are held back
  while the rule is present (or triggers them off on an event).
- **Start trigger** (``start_trigger``) closes that stretch (and can itself wait
  for its rule before execution continues).
- **Loop start** / **Loop end** (``loop_start`` / ``loop_end``) repeat the
  steps written between them -- looping is an action, not a general option.

Every action type shows only the fields it needs, and *Record* buttons
capture live keyboard/mouse input through pynput.  This module was split
out of the former 1232-line ``macro_editor.py``; the whole-session recorder
dialog lives in :mod:`components.macro_editor.recorder_dialog` and the
macro-level dialog in :mod:`components.macro_editor.macro_editor`.
"""

from __future__ import annotations

import time
from typing import Callable

from PySide6.QtCore import QTimer, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from common.events import is_modifier_name
from components.macro.condition import ScreenCondition
from components.macro.macro import (
    CONDITION_ACTION_KINDS,
    LOOP_ACTION_KINDS,
    ActionConfig,
)
from components.condition_editor import ConditionEditor
from components.input import InputRecorder, current_mouse_position

#: Human label -> action kind understood by ``components.input.synthesis.perform_action``
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
    "Wait until": "wait_for",
    "Stop trigger": "stop_trigger",
    "Start trigger": "start_trigger",
    "Loop start": "loop_start",
    "Loop end": "loop_end",
}

#: Kinds whose page is the shared condition editor and which store their
#: rule in ``ActionConfig.condition``.
_CONDITION_PAGE_KINDS: tuple[str, ...] = ("wait_for", "stop_trigger", "start_trigger")

#: Kinds that jump back/forward to their counterpart (kept in sync with the
#: settings/engine constant of the same name); they have no keyboard/mouse
#: fields of their own.
BUTTONS = ["left", "right", "middle"]

_KEY_FIELD_HINT = "One press of the key when the macro runs (e.g. a, space, f5, enter)."

_CONDITION_HINTS: dict[str, str] = {
    "wait_for": (
        "Execution stops here until this screen event appears; the optional "
        "give-up timeout ends the macro if it never happens."
    ),
    "stop_trigger": (
        "Triggers off every action written after this step while this screen "
        "event is present on screen, and triggers them back on as soon as it "
        "clears (the screen is watched automatically).  Without a rule it "
        "opens a stretch whose actions stay held until the next 'Start "
        "trigger' step closes it."
    ),
    "start_trigger": (
        "Triggers on every action written after this step as soon as this "
        "screen event appears; until then those actions stay held.  Without "
        "a rule it simply releases the hold opened by the last 'Stop trigger'."
    ),
    "loop_start": (
        "Opens a loop.  Everything written between this step and the matching "
        "'Loop end' step repeats.  Optionally set an iteration delay below."
    ),
    "loop_end": (
        "Closes the nearest open 'Loop start'.  Jump back to the loop start "
        "after waiting the optional iteration delay set here (in ms).  An "
        "unmatched 'Loop end' is ignored at run time."
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

    pynput listener callbacks never touch Qt widgets directly; they marshal
    their updates onto the GUI thread through the :attr:`_gui_call` signal.
    Per-kind widgets live in :attr:`_key_fields` / :attr:`_click_fields` so
    that e.g. the hold-key page cannot overwrite the normal key page's line
    edit (and vice versa).
    """

    #: Carries a zero-argument callable from a pynput listener thread to the
    #: GUI thread (Qt objects may only be modified from the GUI thread).
    _gui_call = Signal(object)

    def __init__(self, action: ActionConfig | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._gui_call.connect(self._dispatch_gui_call)
        self.setWindowTitle("Macro action")
        self.setMinimumWidth(440)
        action = action or ActionConfig()

        self._recorder: InputRecorder | None = None
        # Per-kind widget storage: each page keeps its *own* fields keyed by
        # action kind instead of sharing one attribute name across pages.
        self._key_fields: dict[str, QLineEdit] = {}
        self._click_fields: dict[str, tuple[QComboBox, QSpinBox, QSpinBox, QCheckBox]] = {}

        self._kind = QComboBox()
        for label, kind in ACTION_KINDS.items():
            self._kind.addItem(label, kind)
        index = self._kind.findData(action.kind)
        self._kind.setCurrentIndex(max(index, 0))

        self._pages = QStackedWidget()
        self._page_for_kind: dict[str, QWidget] = {}
        self._condition_editors: dict[str, ConditionEditor] = {}
        # One shared screen-rule editor reused by every flow-control kind.
        # Built *before* the per-kind pages so their default values can read
        # the action being edited (e.g. a wait_for's give-up timeout).
        self._condition_page, self._shared_condition = self._build_shared_condition_page(action)
        self._add_page("key", self._build_key_page(action, "key"))
        self._add_page("hold_key", self._build_hold_page(action))
        self._add_page("combo", self._build_combo_page(action))
        self._add_page("type", self._build_type_page(action))
        self._add_page("move", self._build_move_page(action))
        self._add_page("click", self._build_click_page(action, "click"))
        self._add_page("double_click", self._build_click_page(action, "double_click"))
        self._add_page("mouse_down", self._build_button_page("down"))
        self._add_page("mouse_up", self._build_button_page("up"))
        self._add_page("drag", self._build_drag_page(action))
        self._add_page("scroll", self._build_scroll_page(action))
        self._add_page("wait", self._build_wait_page(action))
        self._add_page("loop_start", self._build_loop_page(action, "loop_start"))
        self._add_page("loop_end", self._build_loop_page(action, "loop_end"))
        # All flow-control kinds share the single condition page above;
        # it is registered for each kind so the stacked widget can show it.
        for kind in _CONDITION_PAGE_KINDS:
            self._add_page(kind, self._condition_page)
        # The dict keeps its original (per-kind editor) contract: every kind
        # maps to the same shared editor instance.
        for kind in _CONDITION_PAGE_KINDS:
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

    def _build_key_page(self, action: ActionConfig, kind: str) -> QWidget:
        page = QWidget()
        key_edit = QLineEdit(action.key)
        key_edit.setPlaceholderText("e.g. a, space, f5, enter")
        record = QPushButton("Record key…")
        record.clicked.connect(lambda: self._record_single_key(key_edit))
        layout = QVBoxLayout(page)
        layout.addLayout(_FieldRow(key_edit, record))
        layout.addWidget(self._hint(_KEY_FIELD_HINT))
        self._key_fields[kind] = key_edit
        return page

    def _build_hold_page(self, action: ActionConfig) -> QWidget:
        page = self._build_key_page(action, "hold_key")
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

    def _build_click_page(self, action: ActionConfig, kind: str) -> QWidget:
        page = QWidget()
        button_box = QComboBox()
        button_box.addItems(BUTTONS)
        button_box.setCurrentText(action.button)
        x_spin, y_spin = self._position_spins(action)
        at_position = QCheckBox("Click at specific position")
        at_position.setChecked(action.x is not None)
        at_position.toggled.connect(x_spin.setEnabled)
        at_position.toggled.connect(y_spin.setEnabled)
        x_spin.setEnabled(action.x is not None)
        y_spin.setEnabled(action.x is not None)
        record = QPushButton("Record click…")
        record.clicked.connect(lambda: self._record_click(button_box, x_spin, y_spin))
        form = QFormLayout(page)
        form.addRow("Mouse button:", button_box)
        form.addRow("", at_position)
        form.addRow("Position X:", x_spin)
        form.addRow("Position Y:", y_spin)
        form.addRow("", record)
        if kind == "double_click":
            form.addRow(self._hint("Sends two rapid clicks when the macro runs."))
        self._click_fields[kind] = (button_box, x_spin, y_spin, at_position)
        return page

    def _build_button_page(self, direction: str) -> QWidget:
        page = QWidget()
        box = QComboBox()
        box.addItems(BUTTONS)
        setattr(self, f"_button_{direction}", box)
        record = QPushButton("Record which button you press…")
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
        """Page for the flow-control actions (``wait_for``/``stop_trigger``/``start_trigger``).

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
        if action.kind == "wait_for":
            editor.timeout_spin.setValue(
                max(0, min(editor.timeout_spin.maximum(), action.duration_ms))
            )
        self._shared_condition = editor
        layout.addWidget(editor)
        return page, editor

    def _build_loop_page(self, action: ActionConfig, kind: str) -> QWidget:
        """Page for the ``loop_start`` / ``loop_end`` actions.

        Each loop marker only carries an optional delay in ms (stored on
        ``duration_ms``): for ``loop_start`` it is applied before every
        iteration, for ``loop_end`` before jumping back to the start.
        """
        page = QWidget()
        form = QFormLayout(page)
        spin = QSpinBox(minimum=0, maximum=600_000, value=max(0, action.duration_ms))
        spin.setSuffix(" ms")
        label = (
            "Delay per iteration:"
            if kind == "loop_start"
            else "Delay before repeating:"
        )
        form.addRow(label, spin)
        setattr(self, f"_loop_{kind}_delay", spin)
        return page

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
    def _run_on_gui(self, callback: Callable[[], None]) -> None:
        """Marshal *callback* onto the Qt GUI thread (safe from pynput threads)."""
        self._gui_call.emit(callback)

    @staticmethod
    def _dispatch_gui_call(callback: Callable[[], None]) -> None:
        """Slot invoked on the GUI thread for every :attr:`_gui_call` emission."""
        callback()

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
        widgets through :meth:`_run_on_gui`, which marshals the update onto
        the GUI thread via the :attr:`_gui_call` signal.  Stopping also goes
        through that signal -- ``QTimer`` objects may not be created or
        started from a non-Qt thread.
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
                self._run_on_gui(self._stop_recording_window)

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
            if is_modifier_name(name):
                return False
            self._run_on_gui(lambda: target.setText(name))
            return True

        self._start_recording_window("Recording — press one key…", 10.0, on_press=handle)

    def _record_combo(self) -> None:
        """Capture modifiers + one main key, e.g. ``ctrl+shift+d``."""
        pressed: set[str] = set()

        def handle(name: str) -> bool:
            pressed.add(name)
            if is_modifier_name(name):
                return False
            modifiers = sorted(p for p in pressed if is_modifier_name(p))
            base = name.split("_")[0] if "_" in name else name
            combo = "+".join(modifiers + [base])
            self._run_on_gui(lambda: self._combo.setText(combo))
            return True

        self._start_recording_window("Recording — press a combo (e.g. Ctrl+C)…", 10.0, on_press=handle)

    def _record_hold(self) -> None:
        """Measure how long the user holds the next key (press *and* release)."""
        state: dict[str, float] = {}

        def on_press(name: str) -> bool:
            if is_modifier_name(name):
                return False
            state["press_at"] = time.monotonic()
            key_edit = self._key_fields["hold_key"]
            self._run_on_gui(
                lambda: (key_edit.setText(name), self.setWindowTitle("Recording — release the key…"))
            )
            return False  # keep listening for the release event

        def on_release(name: str) -> bool:
            if "press_at" not in state:
                return False
            held_ms = max(1, int((time.monotonic() - state["press_at"]) * 1000))
            self._run_on_gui(lambda: self._hold_duration.setValue(held_ms))
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
                    text = "".join(buffer)
                    self._run_on_gui(lambda: self._text.setText(text))
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

        def apply_click(button: str, pos_x: int, pos_y: int, use_position: bool) -> None:
            button_box.setCurrentText(button)
            if use_position:
                x_spin.setValue(pos_x)
                y_spin.setValue(pos_y)

        def on_button(button: str) -> None:
            pos_x, pos_y = current_mouse_position()
            use_position = x_spin.isEnabled()
            self._run_on_gui(lambda: apply_click(button, pos_x, pos_y, use_position))
            self._run_on_gui(self._stop_recording_window)

        self._start_mouse_recording("Recording — perform a click…", on_button)

    def _record_button(self, direction: str, button_box: QComboBox) -> None:
        """Capture which physical mouse button the user presses next."""

        def on_button(button: str) -> None:
            self._run_on_gui(lambda: button_box.setCurrentText(button))
            self._run_on_gui(self._stop_recording_window)

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
            return ActionConfig(kind=kind, key=self._key_fields["key"].text().strip().lower())
        if kind == "hold_key":
            return ActionConfig(
                kind=kind,
                key=self._key_fields["hold_key"].text().strip().lower(),
                duration_ms=self._hold_duration.value(),
            )
        if kind == "combo":
            return ActionConfig(kind=kind, combo=self._combo.text().strip().lower())
        if kind == "type":
            return ActionConfig(kind=kind, text=self._text.text(), amount=self._type_delay.value())
        if kind == "move":
            return ActionConfig(kind=kind, x=self._move_x.value(), y=self._move_y.value())
        if kind in ("click", "double_click"):
            button_box, x_spin, y_spin, at_position = self._click_fields[kind]
            use_position = at_position.isChecked()
            return ActionConfig(
                kind=kind,
                button=button_box.currentText(),
                x=x_spin.value() if use_position else None,
                y=y_spin.value() if use_position else None,
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
                # Unconfigured rule: store None rather than a half-built rule.
                condition = None
            elif kind == "wait_for" and condition is not None:
                # Give-up timeout lives on the action's duration_ms field.
                duration_ms = condition.timeout_ms
            return ActionConfig(kind=kind, condition=condition, duration_ms=duration_ms)
        if kind in LOOP_ACTION_KINDS:
            delay = getattr(self, f"_loop_{kind}_delay").value()
            return ActionConfig(kind=kind, duration_ms=delay)
        return ActionConfig(kind=kind)
