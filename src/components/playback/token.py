"""Per-run execution state shared by the macro runner and condition monitor.

:class:`ExecutionToken` is the single mutable object both the worker thread
(running actions) and the background monitor (polling armed trigger rules)
touch while a macro runs; :class:`MacroStats` holds the session counters the
UI reads for debugging.  They live here so :mod:`components.playback.macro_flow` and
:mod:`components.playback.condition_monitor` can share them without importing each other.
"""

from __future__ import annotations

import threading

from components.macro.macro import MacroConfig


class ExecutionToken:
    """Per-run bookkeeping for one running sequence.

    Holds cooperative **cancellation** (user/emergency stop), the armed
    trigger rules from the ``stop_trigger``/``start_trigger`` steps reached
    so far, and the event counters backing non-screen conditions.  Each entry
    in :attr:`blocked_rules` has a ``mode``:

    * ``"while"`` -- block later actions while the rule holds.
    * ``"until"`` -- block until the rule holds once, then self-disarm.
    * ``"hold"``  -- bare marker: block until a bare ``start_trigger`` removes it.

    When any armed rule currently blocks, :attr:`gated` is True: normal
    actions call :meth:`wait_while_blocked` before firing, but the flow steps
    themselves never halt, so a later ``start_trigger`` can re-arm the tail.
    """

    def __init__(self, macro: MacroConfig | None = None) -> None:
        self.macro = macro  # identity (name/uid) for stats reporting; may be None
        self._cancelled = threading.Event()
        self._blocked = threading.Event()  # set => later actions are triggered off
        # Guards ``blocked_rules``: the worker thread and the background
        # monitor both mutate the list, so every read-modify-write of it
        # happens while holding this lock (prevents lost updates).
        self.rules_lock = threading.Lock()
        # Rules armed by the ``stop_trigger``/``start_trigger`` steps reached so far.
        # Each entry: {"mode": "while"|"until"|"hold", "condition": ...}
        self.blocked_rules: list[dict] = []
        #: Monotonic per-run counters keyed by ``action:<kind>`` /
        #: ``key:<name>`` / ``mouse:<button>`` / ``wheel``.  Incremented by
        #: the worker when an action executes and by the input watcher when
        #: the user physically performs an input; the monitor compares each
        #: non-screen rule's stored baseline against the current value to
        #: decide whether the event happened since the rule was armed.
        self.event_counts: dict[str, int] = {}
        self._events_lock = threading.Lock()
        #: Set while at least one running token watches user-input events so
        #: the engine keeps exactly one global listener alive.
        self.needs_input_watch = False

    def bump_event(self, key: str) -> None:
        """Record that the event *key* (e.g. ``"key:a"``) just happened."""
        with self._events_lock:
            self.event_counts[key] = self.event_counts.get(key, 0) + 1

    def event_count(self, key: str) -> int:
        with self._events_lock:
            return self.event_counts.get(key, 0)

    # -- cancellation --------------------------------------------------
    def cancel(self) -> None:
        self._cancelled.set()
        # Never leave a blocked sequence hanging after cancellation.
        self._blocked.set()

    @property
    def cancelled(self) -> bool:
        return self._cancelled.is_set()

    # -- trigger state (set by the stop-trigger/start-trigger actions) ---------------
    def refresh_block(self) -> None:
        """Recompute whether later actions are triggered off from the rules.

        Called by the execution thread whenever the trigger set changes and
        periodically by the background monitor as screen rules come and go.
        """
        blocking = any(entry.get("_blocking") for entry in self.blocked_rules)
        if blocking:
            self._blocked.set()
        else:
            self._blocked.clear()

    @property
    def gated(self) -> bool:
        """True while a rule holds later actions back (worker + monitor read this)."""
        return self._blocked.is_set()

    def wait_while_blocked(self) -> None:
        """Block while later actions are triggered off; returns when they
        are triggered on again (or the sequence is cancelled)."""
        while self._blocked.is_set() and not self._cancelled.is_set():
            self._cancelled.wait(0.05)

    def wait(self, seconds: float) -> None:
        """Interruptible wait used for delays and polling cadence."""
        self._cancelled.wait(seconds)


class MacroStats:
    """Mutable per-macro session counters for debuggability.

    The engine's failure mode is a macro that *silently stops firing* (a
    trigger rule holding everything back), so each run records which rule
    last blocked it and how many loops completed.  Read via
    :meth:`MacroEngine.macro_stats`; guarded by the engine lock.
    """

    __slots__ = ("loops", "blocked_by", "last_event")

    def __init__(self) -> None:
        self.loops = 0
        self.blocked_by: str | None = None
        self.last_event: str = ""
