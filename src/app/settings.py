"""Application settings: dataclasses plus JSON load/save helpers."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, fields, is_dataclass, replace
from pathlib import Path
from typing import Any

from app.conditions import ScreenCondition

DEFAULT_SETTINGS_PATH = Path("data") / "settings.json"


@dataclass(frozen=True)
class ActionConfig:
    """A single keyboard/mouse action inside a macro sequence."""

    kind: str = "key"  # key | hold_key | combo | type | move | click | double_click
    #                    mouse_down | mouse_up | drag | scroll | wait
    key: str = ""  # for key/hold_key actions (e.g. "a", "space", "f1")
    button: str = "left"  # for click/double_click/mouse_down/mouse_up/drag actions
    x: int | None = None  # absolute screen X (mouse actions; drag start)
    y: int | None = None  # absolute screen Y (mouse actions; drag start)
    amount: int = 0  # scroll steps; type delay ms; drag end X
    duration_ms: int = 50  # hold time; wait delay; drag end Y
    combo: str = ""  # for combo actions, e.g. "ctrl+shift+d"
    text: str = ""  # for type actions, the literal string to type


@dataclass(frozen=True)
class MacroConfig:
    """A named sequence of actions with playback and screen-condition options.

    ``start_condition`` gates execution: the macro waits until it holds on
    screen before running its first action.  ``stop_condition`` interrupts a
    running macro as soon as it holds.  Either may be left unconfigured.
    """

    name: str = "New macro"
    start_hotkey: str = "f6"
    repeat: bool = False
    loops: int = 1  # ignored when repeat is True
    interval_ms: int = 0  # delay between loops
    actions: tuple[ActionConfig, ...] = ()
    start_condition: ScreenCondition | None = None
    stop_condition: ScreenCondition | None = None


@dataclass(frozen=True)
class WatcherConfig:
    """Standalone background trigger kept for advanced use cases.

    Wraps a :class:`~app.conditions.ScreenCondition` that is polled
    continuously; when it fires, the watcher runs its target macro and/or
    its own action list.
    """

    name: str = "New watcher"
    condition: ScreenCondition = field(default_factory=ScreenCondition)
    cooldown_ms: int = 1000  # min delay between two triggers
    auto_start: bool = False
    target_macro: str = ""  # macro started when the watcher fires
    actions: tuple[ActionConfig, ...] = ()  # extra actions run after the macro


@dataclass(frozen=True)
class AppSettings:
    """Top-level settings document."""

    macros: tuple[MacroConfig, ...] = ()
    watchers: tuple[WatcherConfig, ...] = ()
    stop_hotkey: str = "f8"
    execution_delay_ms: int = 500  # countdown before a macro starts, to allow focusing the target window


def _action_from_dict(raw: dict[str, Any]) -> ActionConfig:
    known = {k: v for k, v in raw.items() if k in ActionConfig.__dataclass_fields__}
    return ActionConfig(**known)


def _condition_from_dict(raw: dict[str, Any] | None) -> ScreenCondition | None:
    """Rebuild an optional :class:`ScreenCondition` from its JSON form."""
    if not isinstance(raw, dict):
        return None
    known = {k: v for k, v in raw.items() if k in ScreenCondition.__dataclass_fields__}
    if "region" in known:
        known["region"] = tuple(known["region"])  # type: ignore[assignment]
    return ScreenCondition(**known)


def _macro_from_dict(raw: dict[str, Any]) -> MacroConfig:
    skip = {"actions", "start_condition", "stop_condition"}
    known = {k: v for k, v in raw.items() if k in MacroConfig.__dataclass_fields__ and k not in skip}
    return MacroConfig(
        actions=tuple(_action_from_dict(a) for a in raw.get("actions", [])),
        start_condition=_condition_from_dict(raw.get("start_condition")),
        stop_condition=_condition_from_dict(raw.get("stop_condition")),
        **known,
    )


def _watcher_from_dict(raw: dict[str, Any]) -> WatcherConfig:
    skip = {"actions", "condition"}
    known = {k: v for k, v in raw.items() if k in WatcherConfig.__dataclass_fields__ and k not in skip}
    condition = _condition_from_dict(raw.get("condition")) or ScreenCondition()
    return WatcherConfig(condition=condition, actions=tuple(_action_from_dict(a) for a in raw.get("actions", [])), **known)


def load_settings(path: Path = DEFAULT_SETTINGS_PATH) -> AppSettings:
    """Load settings from JSON, falling back to defaults on any problem."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return AppSettings(
            macros=tuple(_macro_from_dict(m) for m in raw.get("macros", [])),
            watchers=tuple(_watcher_from_dict(w) for w in raw.get("watchers", [])),
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
        "watchers": [_to_plain(w) for w in settings.watchers],
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
    watchers = tuple(
        replace(w, target_macro=new_name) if w.target_macro == old_name else w for w in settings.watchers
    )
    return replace(settings, macros=macros, watchers=watchers)
