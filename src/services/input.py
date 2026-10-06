"""Keyboard and mouse input service built on pynput.

This module is the single place that talks to the operating system for input
synthesis (macro actions) and global hotkey listening.  Keeping it isolated
behind small, typed helpers makes the rest of the code base independent from
the pynput API:

* :func:`perform_action` -- execute one :class:`~app.settings.ActionConfig`.
* :class:`HotkeyManager` -- register/replace global hotkeys from any thread.
* :func:`current_mouse_position` -- read the live pointer position.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, Iterable
from typing import Self

from pynput import keyboard as kb
from pynput import mouse as ms

from app.settings import ActionConfig

logger = logging.getLogger(__name__)

# Keys that must be released when the listener stops (pynput keeps their
# state pressed until explicitly released).
_MODIFIER_KEYS: tuple[kb.Key, ...] = (
    kb.Key.ctrl_l,
    kb.Key.ctrl_r,
    kb.Key.alt_l,
    kb.Key.alt_r,
    kb.Key.shift_l,
    kb.Key.shift_r,
    kb.Key.cmd,
    kb.Key.cmd_r,
)

_BUTTONS: dict[str, ms.Button] = {
    "left": ms.Button.left,
    "right": ms.Button.right,
    "middle": ms.Button.middle,
}

_BUTTON_NAMES: dict[ms.Button, str] = {button: name for name, button in _BUTTONS.items()}

#: Names treated as modifiers when ordering hotkey combos (ctrl+alt+shift+key).
MODIFIER_NAMES: frozenset[str] = frozenset(
    {"ctrl", "ctrl_l", "ctrl_r", "alt", "alt_l", "alt_r", "shift", "shift_l", "shift_r", "cmd", "cmd_r"}
)

#: Side-specific modifier keys mapped onto their generic alias so that a
#: parsed combo like ``ctrl+shift+d`` matches whichever physical keys the
#: user actually presses (e.g. ``ctrl_l``).
_MODIFIER_ALIASES: dict[kb.Key, kb.Key] = {
    kb.Key.ctrl_l: kb.Key.ctrl,
    kb.Key.ctrl_r: kb.Key.ctrl,
    kb.Key.alt_l: kb.Key.alt,
    kb.Key.alt_r: kb.Key.alt,
    kb.Key.shift_l: kb.Key.shift,
    kb.Key.shift_r: kb.Key.shift,
    kb.Key.cmd: kb.Key.cmd,
    kb.Key.cmd_r: kb.Key.cmd,
}


#: Everything that can appear in a parsed hotkey combination or in the set of
#: currently pressed keys: named keys, raw character codes, plain characters.
HotkeyKey = kb.Key | kb.KeyCode | str


def _canonical_hotkey_key(key: HotkeyKey) -> HotkeyKey:
    """Normalize side-specific modifiers and character case."""
    if isinstance(key, kb.KeyCode):
        if key.char:
            return key.char.lower()
        return key
    if isinstance(key, kb.Key):
        return _MODIFIER_ALIASES.get(key, key)
    return key


def _to_kb_key(name: str) -> HotkeyKey | None:
    """Resolve an action key name ("a", "space", "f5") into a pynput key."""
    if not name:
        return None
    lowered = name.strip().lower()
    member = getattr(kb.Key, lowered, None)
    if type(member).__name__ == "Key":  # real enum member, not the class itself
        return member
    if len(lowered) == 1:
        return lowered
    try:
        return kb.KeyCode.from_char(lowered)
    except ValueError:
        logger.warning("Unknown key name: %r", name)
        return None


def to_pynput_key(key: kb.Key | kb.KeyCode) -> str:
    """Inverse of :func:`_to_kb_key`: a pynput key event -> canonical name."""
    if isinstance(key, kb.KeyCode):
        if key.char:
            return key.char
        if key.vk is not None:
            return f"<{hex(key.vk)}>"
        return "<unknown>"
    return key.name


def _to_ms_button(name: str) -> ms.Button:
    return _BUTTONS.get(name, ms.Button.left)


def sort_combo(combo: str) -> str:
    """Normalize a ``"a+ctrl"`` string into modifier-first order ``"ctrl+a"``."""
    parts = [p.strip().lower() for p in combo.split("+") if p.strip()]
    modifiers = sorted(p for p in parts if p in MODIFIER_NAMES)
    others = [p for p in parts if p not in MODIFIER_NAMES]
    return "+".join(modifiers + others)


def current_mouse_position() -> tuple[int, int]:
    """Absolute screen coordinates of the mouse pointer."""
    x, y = ms.Controller().position
    return int(x), int(y)


def _interruptible_sleep(seconds: float, cancel: Callable[[], bool] | None = None) -> bool:
    """Sleep up to *seconds*, waking early when *cancel* reports True.

    Returns True when the full duration elapsed, False when cancelled.
    Sleeping in short slices keeps long actions (a 600 s key hold, a slow
    typing run) interruptible so an emergency stop takes effect promptly.
    """
    deadline = time.monotonic() + seconds
    while True:
        if cancel is not None and cancel():
            return False
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return True
        time.sleep(min(remaining, 0.05))


def perform_action(action: ActionConfig, cancel: Callable[[], bool] | None = None) -> None:
    """Execute a single macro action via pynput (blocking).

    *cancel* is an optional predicate checked between sleeps and between
    individual input steps; when it returns True the action aborts as soon
    as possible **while releasing every key/button it already pressed**, so
    an emergency stop never leaves modifiers or mouse buttons stuck down.

    Flow-control kinds (``wait_for``, ``stop_trigger``, ``start_trigger``) are handled by
    :class:`~core.macro_engine.MacroEngine` itself and never reach real input
    synthesis; they are accepted here as no-ops so ad-hoc action lists can be
    played through this function without special-casing.
    """
    kind = action.kind
    if kind in ("wait_for", "stop_trigger", "start_trigger"):
        return
    if kind in ("key", "hold_key"):
        _perform_key(kind, action.key, action.duration_ms, cancel)
    elif kind == "combo":
        _perform_combo(action.combo, cancel)
    elif kind == "type":
        _perform_type(action.text, max(action.amount, 0), cancel)
    elif kind == "move":
        if action.x is not None and action.y is not None:
            ms.Controller().position = (action.x, action.y)
    elif kind in ("click", "double_click"):
        _perform_click(action.button, double=kind == "double_click", x=action.x, y=action.y, cancel=cancel)
    elif kind == "mouse_down":
        ms.Controller().press(_to_ms_button(action.button))
    elif kind == "mouse_up":
        ms.Controller().release(_to_ms_button(action.button))
    elif kind == "drag":
        _perform_drag(action, cancel)
    elif kind == "scroll":
        if action.amount:
            ms.Controller().scroll(0, action.amount)
    elif kind == "wait":
        _interruptible_sleep(max(action.duration_ms, 0) / 1000.0, cancel)
    else:
        logger.warning("Unknown action kind: %r", kind)


def _perform_key(
    kind: str, name: str, duration_ms: int, cancel: Callable[[], bool] | None = None
) -> None:
    key = _to_kb_key(name)
    if key is None:
        logger.warning("Key action skipped: no key specified")
        return
    ctrl = kb.Controller()
    ctrl.press(key)
    try:
        if kind == "hold_key" and not _interruptible_sleep(max(duration_ms, 1) / 1000.0, cancel):
            logger.info("Hold of %r cancelled; releasing key", name)
    finally:
        # Always release, even on cancellation -- otherwise the key stays
        # physically "down" for the rest of the session.
        ctrl.release(key)


def _perform_combo(combo: str, cancel: Callable[[], bool] | None = None) -> None:
    """Press all keys of ``"ctrl+shift+d"``, release them in reverse order."""
    names = [part.strip().lower() for part in combo.split("+") if part.strip()]
    keys = [_to_kb_key(name) for name in names]
    if not keys or any(key is None for key in keys):
        logger.warning("Invalid combo: %r", combo)
        return
    ctrl = kb.Controller()
    # ``keys`` was validated above, so every entry is a real pynput key.
    pressed = [key for key in keys if key is not None]
    held: list = []
    try:
        for key in pressed:
            if cancel is not None and cancel():
                logger.info("Combo %r cancelled after %d key(s)", combo, len(held))
                break
            ctrl.press(key)  # pyright: ignore[reportArgumentType]
            held.append(key)
    finally:
        # Release exactly what was pressed, in reverse -- never leave a
        # modifier stuck down after a cancelled combo.
        for key in reversed(held):
            ctrl.release(key)  # pyright: ignore[reportArgumentType]


def _perform_type(text: str, delay_ms: int, cancel: Callable[[], bool] | None = None) -> None:
    """Type *text* one character at a time using a shared controller."""
    if not text:
        return
    ctrl = kb.Controller()
    for index, char in enumerate(text):
        if cancel is not None and cancel():
            logger.info("Typing cancelled after %d char(s)", index)
            return
        try:
            ctrl.type(char)
        except Exception as exc:  # noqa: BLE001 - controller errors are not typed
            logger.warning("Cannot type character %r: %s", char, exc)
        if delay_ms > 0 and not _interruptible_sleep(delay_ms / 1000.0, cancel):
            return


def _perform_click(
    button_name: str,
    *,
    double: bool,
    x: int | None,
    y: int | None,
    cancel: Callable[[], bool] | None = None,
) -> None:
    button = _to_ms_button(button_name)
    ctrl = ms.Controller()
    if x is not None and y is not None:
        ctrl.position = (x, y)
    clicks = 2 if double else 1
    for i in range(clicks):
        if cancel is not None and cancel():
            return
        ctrl.click(button)
        if double and i == 0:
            _interruptible_sleep(0.03, cancel)


def _perform_drag(action: ActionConfig, cancel: Callable[[], bool] | None = None) -> None:
    """Press at (x, y), move to (amount, duration_ms fields), release.

    The end position uses ``None``-checks rather than truthiness so that a
    legitimate drag ending at coordinate 0 is honored.
    """
    if action.x is None or action.y is None:
        logger.warning("Drag action missing start position")
        return
    button = _to_ms_button(action.button)
    ctrl = ms.Controller()
    steps = 15
    start_x, start_y = action.x, action.y
    end_x = action.amount if action.amount is not None else start_x
    end_y = action.duration_ms if action.duration_ms is not None else start_y
    ctrl.position = (start_x, start_y)
    ctrl.press(button)
    try:
        for step in range(1, steps + 1):
            if cancel is not None and cancel():
                logger.info("Drag cancelled at step %d/%d; releasing button", step, steps)
                return
            frac = step / steps
            ctrl.position = (
                int(start_x + (end_x - start_x) * frac),
                int(start_y + (end_y - start_y) * frac),
            )
            _interruptible_sleep(0.01, cancel)
    finally:
        # Never leave the mouse button physically pressed.
        ctrl.release(button)


class HotkeyManager:
    """Global hotkey registry backed by a pynput keyboard listener.

    The underlying listener always runs with *all currently registered*
    hotkeys; :meth:`set_hotkeys` swaps the whole set atomically, which is
    safe to call from the GUI thread while the listener thread is active.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._hotkeys: dict[frozenset[HotkeyKey], Callable[[], None]] = {}
        self._pressed: set[HotkeyKey] = set()
        # Combos that matched since the last full release; they re-arm only
        # once every key of the combo is physically up again.  Without this,
        # a held combo keeps firing on each additional key press (any superset
        # of the pressed set still matches).
        self._consumed: set[frozenset[HotkeyKey]] = set()
        self._listener: kb.Listener | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def set_hotkeys(self, bindings: Iterable[tuple[str, Callable[[], None]]]) -> None:
        """Replace all registrations with *bindings* ((combo string, callback))."""
        rebuilt: dict[frozenset[HotkeyKey], Callable[[], None]] = {}
        for combo, callback in bindings:
            parsed = self.parse_combo(combo)
            if parsed is None:
                continue
            if parsed in rebuilt:
                logger.warning("Duplicate hotkey '%s' ignored", combo)
                continue
            rebuilt[parsed] = callback

        with self._lock:
            self._hotkeys = rebuilt
            # Do NOT clear ``_pressed`` here: physical keys may be mid-keystroke
            # while the GUI re-syncs hotkeys (e.g. after any settings edit).
            # Dropping the set would make held modifiers look unpressed and
            # corrupt trigger matching.  Stale entries are pruned lazily by
            # ``_on_release``; combos that vanished are cleaned up below.
            stale = self._consumed - rebuilt.keys()
            if stale:
                self._consumed -= stale
            self._sync_locked()

    def stop(self) -> None:
        """Stop the listener and release any held modifier keys."""
        with self._lock:
            self._hotkeys.clear()
            self._pressed.clear()
            listener, self._listener = self._listener, None
        if listener is not None:
            listener.stop()
        self._release_modifiers()

    @staticmethod
    def parse_combo(combo: str) -> frozenset[HotkeyKey] | None:
        """Parse ``"ctrl+shift+d"`` into a canonical frozenset usable as a map key."""
        names = [part.strip().lower() for part in combo.split("+") if part.strip()]
        if not names:
            return None

        keys = [_to_kb_key(name) for name in names]
        if any(key is None for key in keys):
            logger.warning("Invalid hotkey '%s'", combo)
            return None

        canonical = [_canonical_hotkey_key(key) for key in keys if key is not None]
        return frozenset(canonical)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    def _sync_locked(self) -> None:
        if self._hotkeys and self._listener is None:
            self._listener = kb.Listener(
                on_press=self._on_press,
                on_release=self._on_release,
            )
            self._listener.daemon = True
            self._listener.start()
            logger.info("Hotkey listener started (%d binding(s))", len(self._hotkeys))
        elif not self._hotkeys and self._listener is not None:
            listener, self._listener = self._listener, None
            listener.stop()
            logger.info("Hotkey listener stopped")

    def _on_press(self, key: kb.Key | kb.KeyCode | None) -> bool | None:
        # pynput may pass ``None`` for unmapable keys; ignore those events.
        if key is None:
            return None
        canonical = _canonical_hotkey_key(key)

        with self._lock:
            # Ignore operating-system key auto-repeat.
            if canonical in self._pressed:
                return None

            self._pressed.add(canonical)

            matched = max(
                (
                    combo
                    for combo in self._hotkeys
                    if combo not in self._consumed and combo.issubset(self._pressed)
                ),
                key=len,
                default=None,
            )
            callback = self._hotkeys.get(matched) if matched is not None else None
            if matched is not None:
                # Fire once per physical press cycle: stay consumed until all
                # of the combo's keys have been released (see _on_release).
                self._consumed.add(matched)

        if callback is None:
            return None

        # Run callbacks off the listener thread so a macro can start here
        # without blocking further key events.
        threading.Thread(target=self._fire, args=(callback,), daemon=True).start()
        return None

    def _on_release(self, key: kb.Key | kb.KeyCode | None) -> bool | None:
        if key is None:
            return None
        with self._lock:
            self._pressed.discard(_canonical_hotkey_key(key))
            # Re-arm any combo whose keys are now fully released.
            if self._consumed:
                self._consumed = {combo for combo in self._consumed if not combo.isdisjoint(self._pressed)}
        return None

    @staticmethod
    def _fire(callback: Callable[[], None]) -> None:
        try:
            callback()
        except Exception:  # pragma: no cover - defensive
            logger.exception("Hotkey callback failed")

    @staticmethod
    def _release_modifiers() -> None:
        ctrl = kb.Controller()
        for key in _MODIFIER_KEYS:
            try:
                ctrl.release(key)
            except Exception as exc:  # noqa: BLE001 - best-effort cleanup
                # The key was simply not held down; nothing to release.
                logger.debug("Ignoring modifier release failure for %s: %s", key, exc)


def describe_hotkey(combo: str) -> str:
    """Human-readable form for UI captions: 'ctrl+shift+d' -> 'Ctrl+Shift+D'."""
    parts = [p.strip().lower() for p in combo.split("+") if p.strip()]
    aliases = {"ctrl": "Ctrl", "alt": "Alt", "shift": "Shift", "cmd": "Cmd"}
    return "+".join(aliases.get(p, p.upper()) for p in parts)


class InputRecorder:
    """Record live keyboard and mouse input through pynput listeners.

    Callbacks all receive simple, canonical values so callers never touch
    pynput types:

    * ``on_press(name)`` / ``on_release(name)`` -- key names like ``"a"``,
      ``"f8"``, ``"space"`` (modifiers included, e.g. ``"ctrl_l"``).
    * ``on_move(x, y)`` -- pointer position; move events are throttled to
      one per :attr:`MOVE_THROTTLE_S` seconds.
    * ``on_scroll(amount)`` -- positive scrolls up.
    * ``on_button_press(name)`` / ``on_button_release(name)`` -- button is
      ``"left" | "right" | "middle"``.

    Usage::

        recorder = InputRecorder(on_press=print)
        recorder.start()
        ...
        recorder.stop()   # idempotent, safe from any thread/callback
    """

    #: Minimum interval between two ``on_move`` callbacks (seconds).
    MOVE_THROTTLE_S = 0.05

    def __init__(
        self,
        on_press: Callable[[str], None] | None = None,
        on_release: Callable[[str], None] | None = None,
        on_move: Callable[[int, int], None] | None = None,
        on_scroll: Callable[[int], None] | None = None,
        on_button_press: Callable[[str], None] | None = None,
        on_button_release: Callable[[str], None] | None = None,
    ) -> None:
        self._callbacks = {
            "press": on_press,
            "release": on_release,
            "move": on_move,
            "scroll": on_scroll,
            "button_press": on_button_press,
            "button_release": on_button_release,
        }
        self._listeners: list[kb.Listener | ms.Listener] = []
        self._lock = threading.Lock()
        self._last_move = 0.0
        self._stopping = False

    @property
    def running(self) -> bool:
        with self._lock:
            return bool(self._listeners)

    def start(self) -> Self:
        """Start both listeners (no-op if already running)."""
        with self._lock:
            if self._listeners:
                return self

            def on_press(key: kb.Key | kb.KeyCode | None) -> bool | None:
                if key is not None:
                    self._on_key(key, pressed=True)
                return None

            def on_release(key: kb.Key | kb.KeyCode | None) -> bool | None:
                if key is not None:
                    self._on_key(key, pressed=False)
                return None

            keyboard = kb.Listener(on_press=on_press, on_release=on_release)
            mouse = ms.Listener(
                on_move=self._on_move,
                on_click=self._on_click,
                on_scroll=self._on_scroll,
            )
            for listener in (keyboard, mouse):
                listener.daemon = True
                listener.start()
            self._listeners = [keyboard, mouse]
            self._stopping = False
        logger.info("Input recorder started")
        return self

    def stop(self) -> None:
        """Stop both listeners; safe to call multiple times or from a callback."""
        with self._lock:
            listeners, self._listeners = self._listeners, []
            self._stopping = True
        for listener in listeners:
            try:
                listener.stop()
            except Exception:  # pragma: no cover - defensive
                logger.debug("Listener already stopped", exc_info=True)
        if listeners:
            logger.info("Input recorder stopped")

    def __enter__(self) -> Self:
        return self.start()

    def __exit__(self, *exc_info: object) -> None:
        self.stop()

    # ------------------------------------------------------------------
    # pynput event adapters (each drops events already queued during stop)
    # ------------------------------------------------------------------
    def _stopped(self) -> bool:
        with self._lock:
            return self._stopping

    def _emit(self, name: str, *args: object) -> None:
        callback = self._callbacks.get(name)
        if callback is not None:
            callback(*args)  # type: ignore[arg-type]

    def _on_key(self, key: kb.Key | kb.KeyCode, *, pressed: bool) -> None:
        if self._stopped():
            return
        self._emit("press" if pressed else "release", to_pynput_key(key))

    def _on_move(self, x: float, y: float) -> None:
        if self._stopped():
            return
        now = time.monotonic()
        if now - self._last_move < self.MOVE_THROTTLE_S:
            return
        self._last_move = now
        self._emit("move", int(x), int(y))

    def _on_click(self, x: float, y: float, button: ms.Button, pressed: bool) -> None:
        if self._stopped():
            return
        del x, y  # positions come from current_mouse_position() when needed
        name = _BUTTON_NAMES.get(button, "left")
        self._emit("button_press" if pressed else "button_release", name)

    def _on_scroll(self, x: float, y: float, dx: float, dy: float) -> None:
        if self._stopped():
            return
        del x, y
        self._emit("scroll", int(dy))


__all__ = [
    "HotkeyKey",
    "HotkeyManager",
    "InputRecorder",
    "current_mouse_position",
    "describe_hotkey",
    "perform_action",
    "sort_combo",
    "to_pynput_key",
]
