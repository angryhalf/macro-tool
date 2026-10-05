"""Application settings: dataclasses plus JSON load/save helpers."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, is_dataclass, replace
from pathlib import Path
from typing import Any

from app.conditions import ScreenCondition

DEFAULT_SETTINGS_PATH = Path("data") / "settings.json"


@dataclass(frozen=True)
class ActionConfig:
    """A single step inside a macro sequence.

    Besides keyboard/mouse steps, two flow-control kinds exist:

    * ``wait_for`` -- block until a screen condition (stored in ``condition``)
      holds on screen.
    * ``stop_trigger`` / ``start_trigger`` -- trigger-style flow steps.  The screen is
      watched automatically while the macro runs: every action written after
      a *stop_trigger* step is held back while its rule holds on screen (and resumes
      when it clears); every action after an *start_trigger* step stays held until
      its rule is met, then runs freely.  The rule lives in the action's
      ``condition`` field.
    """

    kind: str = "key"  # key | hold_key | combo | type | move | click | double_click
    #                    mouse_down | mouse_up | drag | scroll | wait
    #                    wait_for | stop_trigger | start_trigger
    key: str = ""  # for key/hold_key actions (e.g. "a", "space", "f1")
    button: str = "left"  # for click/double_click/mouse_down/mouse_up/drag actions
    x: int | None = None  # absolute screen X (mouse actions; drag start)
    y: int | None = None  # absolute screen Y (mouse actions; drag start)
    amount: int = 0  # scroll steps; type delay ms; drag end X
    duration_ms: int = 50  # hold time; wait delay; drag end Y
    combo: str = ""  # for combo actions, e.g. "ctrl+shift+d"
    text: str = ""  # for type actions, the literal string to type
    condition: ScreenCondition | None = None  # for wait_for/stop-trigger/start-trigger actions


#: Action kinds that gate execution on a screen condition.
CONDITION_ACTION_KINDS: frozenset[str] = frozenset({"wait_for", "stop_trigger", "start_trigger"})


@dataclass(frozen=True)
class MacroConfig:
    """A named sequence of actions with playback options.

    The user turns the macro on and off (run button, hotkey, stop); there is
    no separate "paused" macro state.  Triggering is expressed *inside the
    action list* only: ``stop_trigger`` and ``start_trigger`` actions carry a
    :class:`ScreenCondition` and act like triggers that gate the stretch of
    actions written between them -- the screen is watched automatically while
    the macro runs, holding back those actions according to the rules.  A
    ``wait_for`` action simply blocks until its condition holds.
    """

    name: str = "New macro"
    start_hotkey: str = "f6"
    repeat: bool = False
    loops: int = 1  # ignored when repeat is True
    interval_ms: int = 0  # delay between loops
    actions: tuple[ActionConfig, ...] = ()


@dataclass(frozen=True)
class AppSettings:
    """Top-level settings document."""

    macros: tuple[MacroConfig, ...] = ()
    stop_hotkey: str = "f8"
    execution_delay_ms: int = 500  # countdown before a macro starts, to allow focusing the target window


def _action_from_dict(raw: dict[str, Any]) -> ActionConfig:
    known = {k: v for k, v in raw.items() if k in ActionConfig.__dataclass_fields__ and k != "condition"}
    condition = _condition_from_dict(raw.get("condition"))
    return ActionConfig(condition=condition, **known)


def _condition_from_dict(raw: dict[str, Any] | None) -> ScreenCondition | None:
    """Rebuild an optional :class:`ScreenCondition` from its JSON form."""
    if not isinstance(raw, dict):
        return None
    known = {k: v for k, v in raw.items() if k in ScreenCondition.__dataclass_fields__}
    if "region" in known:
        known["region"] = tuple(known["region"])  # type: ignore[assignment]
    return ScreenCondition(**known)


def _macro_from_dict(raw: dict[str, Any]) -> MacroConfig:
    known = {k: v for k, v in raw.items() if k in MacroConfig.__dataclass_fields__ and k != "actions"}
    actions = [_action_from_dict(a) for a in raw.get("actions", [])]
    actions = _migrate_legacy_conditions(raw, actions)
    return MacroConfig(actions=tuple(actions), **known)


def _migrate_legacy_conditions(raw: dict[str, Any], actions: list[ActionConfig]) -> list[ActionConfig]:
    """Fold old top-level stop-trigger/start-trigger conditions into the action list.

    Earlier versions stored ``start_condition``/``stop_condition`` (later
    renamed ``pause_condition``/``unpause_condition``) directly on the macro.
    Those semantics are now expressed as *actions*: a ``wait_for`` step at the
    head of the sequence, and a ``stop_trigger``/``start_trigger`` pair wrapped around it.
    """
    legacy_stop = _condition_from_dict(raw.get("stop_trigger_condition", raw.get("pause_condition", raw.get("start_condition"))))
    legacy_start = _condition_from_dict(raw.get("start_trigger_condition", raw.get("unpause_condition", raw.get("stop_condition"))))
    if legacy_stop is None and legacy_start is None:
        return actions
    if not actions:
        return actions
    gate = legacy_start or legacy_stop
    assert gate is not None
    prefix = [ActionConfig(kind="wait_for", condition=legacy_stop)] if legacy_stop else []
    suffix = (
        [
            ActionConfig(kind="stop_trigger", condition=gate),
            ActionConfig(kind="start_trigger", condition=gate),
        ]
        if legacy_start
        else []
    )
    return prefix + actions + suffix


def load_settings(path: Path = DEFAULT_SETTINGS_PATH) -> AppSettings:
    """Load settings from JSON, falling back to defaults on any problem."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return AppSettings(
            macros=tuple(_macro_from_dict(m) for m in raw.get("macros", [])),
            stop_hotkey=raw.get("stop_hotkey", "f8"),
            execution_delay_ms=int(raw.get("execution_delay_ms", 500)),
        )
    except (OSError, ValueError, TypeError, KeyError):
        return AppSettings()


def save_settings(settings: AppSettings, path: Path = DEFAULT_SETTINGS_PATH) -> None:
    """Persist settings as pretty-printed JSON."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "macros": [_to_plain(m) for m in settings.macros],
        "stop_hotkey": settings.stop_hotkey,
        "execution_delay_ms": settings.execution_delay_ms,
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _to_plain(obj: Any) -> Any:
    """Convert frozen dataclasses/tuples into JSON-friendly dicts/lists.

    ``asdict`` already recurses through nested dataclasses (e.g. a macro's
    :class:`ScreenCondition`), turning tuples into lists on the way out; this
    wrapper only normalizes any remaining tuples.
    """
    if is_dataclass(obj) and not isinstance(obj, type):
        return {k: _to_plain(v) for k, v in asdict(obj).items()}
    if isinstance(obj, tuple):
        return [_to_plain(v) for v in obj]
    if isinstance(obj, list):
        return [_to_plain(v) for v in obj]
    if isinstance(obj, dict):
        return {k: _to_plain(v) for k, v in obj.items()}
    return obj


def rename_in_settings(settings: AppSettings, old_name: str, new_name: str) -> AppSettings:
    """Return settings with all references to a macro name updated."""
    macros = tuple(replace(m, name=new_name) if m.name == old_name else m for m in settings.macros)
    return replace(settings, macros=macros)
