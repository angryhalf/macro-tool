"""Global hotkey registry backed by a pynput keyboard listener."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Iterable

from pynput import keyboard as kb

from components.input.naming import (
    HotkeyKey,
    _canonical_hotkey_key,
    _to_kb_key,
)

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
        """Replace all registrations with *bindings* ((combo string, callback)).

        Duplicate combos are dropped by the caller (``MacroEngine._sync_hotkeys``),
        which knows the macro names for a meaningful warning; dict insertion
        order makes the first binding win here.
        """
        rebuilt: dict[frozenset[HotkeyKey], Callable[[], None]] = {}
        for combo, callback in bindings:
            parsed = self.parse_combo(combo)
            if parsed is None:
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
