"""Domain models for the macro tool: actions, macros and app settings.

These frozen dataclasses are the single source of truth for what a macro
*is*; everything else (persistence, engine, UI) consumes them.  The module
only depends on :mod:`models.condition`, keeping the domain layer free of
Qt, OpenCV, pynput or filesystem concerns.

JSON (de)serialisation and document migration live in
:mod:`persistence.settings_store`; runtime condition evaluation lives in
:mod:`engine.condition_runtime`.
"""

from __future__ import annotations

import uuid as _uuid
from dataclasses import dataclass, replace

from models.condition import ScreenCondition


def new_macro_uid() -> str:
    """Return a fresh stable identity string for a :class:`MacroConfig`."""
    return _uuid.uuid4().hex


@dataclass(frozen=True)
class ActionConfig:
    """A single step inside a macro sequence.

    Besides keyboard/mouse steps, flow-control kinds exist:

    * ``wait_for`` -- block until a condition (stored in ``condition``)
      holds.
    * ``stop_trigger`` / ``start_trigger`` -- trigger-style flow steps that
      behave like an if/else pair over the stretch of actions written
      between them.  Every action after a *stop_trigger* is held back while
      its condition is true (and resumes when it turns false); every action
      after a *start_trigger* stays held until its condition becomes true,
      then runs freely.  The condition lives in the action's ``condition``
      field and can watch the screen, user input or other actions executing.
    """

    kind: str = "key"  # key | hold_key | combo | type | move | click | double_click
    #                    mouse_down | mouse_up | drag | scroll | wait
    #                    wait_for | stop_trigger | start_trigger | loop_start | loop_end
    key: str = ""  # for key/hold_key actions (e.g. "a", "space", "f1")
    button: str = "left"  # for click/double_click/mouse_down/mouse_up/drag actions
    x: int | None = None  # absolute screen X (mouse actions; drag start)
    y: int | None = None  # absolute screen Y (mouse actions; drag start)
    amount: int = 0  # scroll steps; type delay ms; drag end X; loop_start iterations (0 = forever)
    duration_ms: int = 50  # hold time; wait delay; drag end Y; loop_end delay ms
    combo: str = ""  # for combo actions, e.g. "ctrl+shift+d"
    text: str = ""  # for type actions, the literal string to type
    condition: ScreenCondition | None = None  # for wait_for/stop-trigger/start-trigger actions


#: Action kinds that gate execution on a condition (trigger flow steps).
CONDITION_ACTION_KINDS: frozenset[str] = frozenset(
    {"wait_for", "stop_trigger", "start_trigger"}
)

#: Action kinds that jump back to their counterpart instead of advancing.
LOOP_ACTION_KINDS: frozenset[str] = frozenset({"loop_start", "loop_end"})


@dataclass(frozen=True)
class MacroConfig:
    """A named sequence of actions with playback options.

    The user turns the macro on and off (run button, hotkey, stop); there is
    no separate "paused" macro state.  Triggering is expressed *inside the
    action list* only: ``stop_trigger`` and ``start_trigger`` actions carry a
    :class:`~models.condition.ScreenCondition` and act like an if/else pair
    that gates the stretch of actions written between them -- the condition
    source (screen, user input, executed actions) is watched automatically
    while the macro runs, holding back those actions according to the rules.
    A ``wait_for`` action simply blocks until its condition holds.

    Looping is likewise an *action*, not a general option: wrap the steps to
    repeat in a ``loop_start`` / ``loop_end`` pair (iteration count on the
    start step, delay between passes on the end step).  The legacy
    ``repeat``/``loops``/``interval_ms`` fields below are kept only so old
    documents still load; they are migrated into loop actions at load time
    (see :mod:`persistence.settings_store`).
    """

    name: str = "New macro"
    start_hotkey: str = "f6"
    #: Legacy general-section looping (replaced by the loop_start/loop_end
    #: actions).  Migrated at load time; new edits always leave these alone.
    repeat: bool = False
    loops: int = 1  # ignored when repeat is True
    interval_ms: int = 0  # delay between loops
    actions: tuple[ActionConfig, ...] = ()
    #: Stable identity across renames.  Never displayed; lets the window
    #: detect real renames instead of guessing from name-set differences.
    uid: str = ""
    #: Disabled macros keep their hotkey registered-free: pressing it (or
    #: clicking Run) does nothing until re-enabled.  Lets users park a
    #: misbehaving macro without deleting it or losing its configuration.
    enabled: bool = True


@dataclass(frozen=True)
class AppSettings:
    """Top-level settings document."""

    macros: tuple[MacroConfig, ...] = ()
    stop_hotkey: str = "f8"
    execution_delay_ms: int = 500  # countdown before a macro starts, to allow focusing the target window


def rename_in_settings(settings: AppSettings, old_name: str, new_name: str) -> AppSettings:
    """Return settings with all references to a macro name updated."""
    macros = tuple(replace(m, name=new_name) if m.name == old_name else m for m in settings.macros)
    return replace(settings, macros=macros)
