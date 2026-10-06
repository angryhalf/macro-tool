"""Macro editor: actions, stop-trigger/start-trigger flow steps and input recording.

* :class:`ActionEditorDialog` -- edit one step of a macro's action list.  On
  top of the keyboard/mouse kinds there are three *flow-control* actions that
  carry a screen condition (built with :class:`ConditionEditor` from
  :mod:`ui.condition_editor`):

  - **Wait until** (``wait_for``) blocks until the rule holds on screen.
  - **Stop trigger** (``stop_trigger``) opens a stretch whose actions are held back
    while the rule is present on screen.
  - **Start trigger** (``start_trigger``) closes that stretch (and can itself wait
    for its rule before execution continues).

  Every action type shows only the fields it needs, and *Record* buttons
  capture live keyboard/mouse input through pynput.
* :class:`MacroRecorderDialog` -- record an entire input session (keys,
  clicks, moves, scrolls, timings) into an ordered action list in one go.
* :class:`MacroEditorDialog` -- edit a macro: metadata (name, hotkey,
  looping), plus its action table.
"""

from __future__ import annotations

import threading
import time
from typing import Callable

from PySide6.QtCore import Qt, QTimer, Signal
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
    QListWidget,
    QPushButton,
    QSpinBox,
    QStackedWidget,
    QListWidgetItem,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.conditions import ScreenCondition
from app.settings import CONDITION_ACTION_KINDS, ActionConfig, MacroConfig, new_macro_uid
from services.input import (
    InputRecorder,
    current_mouse_position,
    describe_hotkey,
    is_modifier_name,
    sort_combo,
)
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
    "Stop trigger (screen)": "stop_trigger",
    "Start trigger (screen)": "start_trigger",
    "If / else (screen)": "if_else",
}

#: Action kinds that gate execution on a screen rule (kept in sync with the
#: engine's ``CONDITION_ACTION_KINDS``).
_FLOW_KINDS: tuple[str, ...] = ("wait_for", "stop_trigger", "start_trigger", "if_else")

#: Kinds whose page is the shared condition editor and which store their
#: screen rule in ``ActionConfig.condition``.
_CONDITION_PAGE_KINDS: tuple[str, ...] = ("wait_for", "stop_trigger", "start_trigger", "if_else")


def _branch_editor(kind_label: str) -> tuple[QListWidget, QHBoxLayout]:
    """Build one *If* / *Else* branch list widget plus its add/remove row."""
    box = QListWidget()
    box.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
    add = QPushButton(f"Add step to {kind_label}")
    remove = QPushButton("Remove selected")
    row = QHBoxLayout()
    row.addWidget(add)
    row.addWidget(remove)
    row.addStretch()
    return box, row

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
    "if_else": (
        "Samples the screen rule once right here and runs only the matching "
        "branch below -- 'If' when the rule is on screen, 'Else' otherwise. "
        "Branches hold plain keyboard/mouse steps only (no nested flow "
        "actions).  An unconfigured rule takes the Else branch."
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
        # All flow-control kinds share the single condition page above;
        # it is registered for each kind so the stacked widget can show it.
        for kind in _CONDITION_PAGE_KINDS:
            self._add_page(kind, self._condition_page)
        # The dict keeps its original (per-kind editor) contract: every kind
        # maps to the same shared editor instance.
        for kind in _CONDITION_PAGE_KINDS:
            self._condition_editors[kind] = self._shared_condition
        # ``if_else`` additionally gets branch editors appended to that page.
        self._if_else_box = self._build_if_else_branches(action)

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

    def _build_if_else_branches(self, action: ActionConfig) -> QWidget:
        """If/Else branch editors appended to the shared condition page.

        Each branch is a plain list of keyboard/mouse steps edited through
        :class:`ActionEditorDialog` itself (nesting one level deep is the
        supported maximum -- nested flow actions are refused on input).
        The whole box is only visible for the ``if_else`` kind.
        """
        box = QWidget()
        outer = QVBoxLayout(box)
        outer.setContentsMargins(0, 4, 0, 0)
        self._branch_lists: dict[str, QListWidget] = {}
        for field, label in (("then_actions", "If"), ("else_actions", "Else")):
            outer.addWidget(QLabel(f"<b>{label} branch</b>"))
            list_box, button_row = _branch_editor(label)
            add_button = button_row.itemAt(0).widget()
            remove_button = button_row.itemAt(1).widget()
            add_button.clicked.connect(lambda _=False, f=field: self._edit_branch_step(f, None))
            remove_button.clicked.connect(
                lambda _=False, lb=list_box: self._remove_branch_step(lb)
            )
            outer.addWidget(list_box)
            outer.addLayout(button_row)
            self._branch_lists[field] = list_box
            for child in getattr(action, field):
                self._append_branch_item(list_box, child)
        box.setVisible(False)
        return box

    def _append_branch_item(self, list_box: QListWidget, child: ActionConfig) -> None:
        name, details = MacroEditorDialog.describe(child)
        item = QListWidgetItem(f"{name}: {details}")
        # Keep the model object on the item so order survives edits.
        item.setData(Qt.ItemDataRole.UserRole, child)
        list_box.addItem(item)

    def _edit_branch_step(self, field: str, existing: ActionConfig | None) -> None:
        """Open an :class:`ActionEditorDialog` for one branch step (in place)."""
        list_box = self._branch_lists[field]
        dialog = ActionEditorDialog(existing or ActionConfig(), parent=self)
        if not dialog.exec():
            return
        new_action = dialog.action()
        if new_action.kind in CONDITION_ACTION_KINDS:
            # Nested flow actions inside a branch are unsupported; silently
            # drop them at the UI boundary instead of writing bad data.
            from PySide6.QtWidgets import QMessageBox

            QMessageBox.warning(
                self,
                "Not allowed",
                "Branch steps must be plain keyboard/mouse actions; "
                "flow-control actions cannot nest inside If/Else.",
            )
            return
        if existing is None:
            self._append_branch_item(list_box, new_action)
            return
        row = list_box.currentRow()
        if row >= 0:
            item = list_box.takeItem(row)
            del item
            fresh = QListWidgetItem()
            name, details = MacroEditorDialog.describe(new_action)
            fresh.setText(f"{name}: {details}")
            fresh.setData(Qt.ItemDataRole.UserRole, new_action)
            list_box.insertItem(row, fresh)

    @staticmethod
    def _remove_branch_step(list_box: QListWidget) -> None:
        row = list_box.currentRow()
        if row >= 0:
            list_box.takeItem(row)

    def _branch_actions(self, field: str) -> tuple[ActionConfig, ...]:
        list_box = self._branch_lists[field]
        return tuple(
            list_box.item(row).data(Qt.ItemDataRole.UserRole)
            for row in range(list_box.count())
            if list_box.item(row).data(Qt.ItemDataRole.UserRole) is not None
        )

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
            self._if_else_box.setVisible(kind == "if_else")
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
                # Unconfigured rule: store None, but *keep* the user's other
                # inputs (branches below) rather than silently discarding them.
                condition = None
            elif kind == "wait_for" and condition is not None:
                # Give-up timeout lives on the action's duration_ms field.
                duration_ms = condition.timeout_ms
            if kind == "if_else":
                return ActionConfig(
                    kind=kind,
                    condition=condition,
                    then_actions=self._branch_actions("then_actions"),
                    else_actions=self._branch_actions("else_actions"),
                )
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
      become Click; otherwise a single **Drag** step (start = press point,
      end = release point) -- one semantic action instead of three raw ones.
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
                # A press/release pair with travel is a drag gesture: emit one
                # semantic ``drag`` step (start -> end) instead of the raw
                # mouse_down/move/mouse_up triple, which replays brittlely.
                # NOTE: drag reuses amount/duration_ms for the end point --
                # see ActionConfig's field-overloading warning in app.settings.
                actions.append(
                    ActionConfig(
                        kind="drag",
                        button=button,
                        x=pair[1]["x"],
                        y=pair[1]["y"],
                        amount=payload["x"],
                        duration_ms=payload["y"],
                    )
                )
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

    * **General** -- name, start hotkey, repeat/loops and loop interval.
      The user turns the macro on and off; there is no separate pause state.
    * **Actions** -- an ordered table of steps with add/edit/remove/reorder
      controls plus a whole-session recorder.  The triggers are *actions*:
      insert a "Stop trigger" step to open a gated stretch and a "Start
      trigger" step to close it -- everything written between them is held back
      while the stop-trigger step's screen rule is present on screen.
    """

    def __init__(
        self,
        macro: MacroConfig,
        parent: QWidget | None = None,
        stop_hotkey: str = "f8",
    ) -> None:
        super().__init__(parent)
        self._stop_hotkey = stop_hotkey
        # Preserve identity across edits; stamp a fresh uid for brand-new macros.
        self._uid = macro.uid or new_macro_uid()
        self.setWindowTitle(f"Edit macro — {macro.name}")
        self.setMinimumSize(680, 620)

        # -- general ---------------------------------------------------
        self._name = QLineEdit(macro.name)
        self._hotkey = HotkeyButton(macro.start_hotkey)
        self._repeat = QCheckBox("Repeat until stopped")
        self._repeat.setChecked(macro.repeat)
        self._loops = QSpinBox(minimum=1, maximum=9999, value=max(1, macro.loops))
        self._loops.setEnabled(not macro.repeat)
        self._repeat.toggled.connect(self._loops.setDisabled)
        self._interval = QSpinBox(minimum=0, maximum=600_000, value=macro.interval_ms)
        self._interval.setSuffix(" ms")
        self._enabled = QCheckBox("Enabled (unchecked macros ignore their hotkey and Run clicks)")
        self._enabled.setChecked(macro.enabled)

        meta_form = QFormLayout()
        meta_form.addRow("Name:", self._name)
        meta_form.addRow("Start hotkey:", self._hotkey)
        meta_form.addRow("Repeat:", self._repeat)
        meta_form.addRow("Loops:", self._loops)
        meta_form.addRow("Loop interval:", self._interval)
        meta_form.addRow("", self._enabled)
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
            "stop_trigger": ("Stop trigger", cond),
            "start_trigger": ("Start trigger", cond),
            "if_else": (
                "If / else",
                f"{cond}  →  if: {len(action.then_actions)} step(s), "
                f"else: {len(action.else_actions)} step(s)",
            ),
        }
        return labels.get(action.kind, (action.kind, ""))

    @staticmethod
    def _describe_condition(action: ActionConfig) -> str:
        """Short summary of a flow action's screen rule for the table."""
        condition = action.condition
        if condition is None or not condition.configured:
            return "no rule configured"
        parts = [condition.description]
        # The give-up timeout only means something for wait_for; drag steps
        # reuse duration_ms as the end Y coordinate and must not be mislabeled.
        if action.duration_ms and action.kind == "wait_for":
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
            uid=self._uid,
            enabled=self._enabled.isChecked(),
        )
