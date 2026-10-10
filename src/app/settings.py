"""Application settings: dataclasses plus JSON load/save helpers."""

from __future__ import annotations

import json
import logging
import uuid as _uuid
from dataclasses import asdict, dataclass, is_dataclass, replace
from pathlib import Path
from typing import Any, Sequence

from app.conditions import ScreenCondition

logger = logging.getLogger(__name__)

DEFAULT_SETTINGS_PATH = Path("data") / "settings.json"

#: Bumped whenever the on-disk document layout changes; written by
#: ``save_settings`` so future loaders can migrate deliberately instead of
#: guessing from missing fields.
SETTINGS_SCHEMA_VERSION = 1


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
    :class:`ScreenCondition` and act like an if/else pair that gates the
    stretch of actions written between them -- the condition source (screen,
    user input, executed actions) is watched automatically while the macro
    runs, holding back those actions according to the rules.  A ``wait_for``
    action simply blocks until its condition holds.

    Looping is likewise an *action*, not a general option: wrap the steps to
    repeat in a ``loop_start`` / ``loop_end`` pair (iteration count on the
    start step, delay between passes on the end step).  The legacy
    ``repeat``/``loops``/``interval_ms`` fields below are kept only so old
    documents still load; they are migrated into loop actions at load time.
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


def _action_from_dict(raw: Any) -> ActionConfig | dict[str, Any]:
    """Rebuild an action from JSON, keeping unknown legacy keys intact.

    Unknown scalar fields are dropped, but nested sub-sequences (e.g. the
    old ``if_else`` step's ``then_actions`` / ``else_actions``) survive as
    raw dicts so :func:`_migrate_legacy_actions` can unfold them.
    """
    if not isinstance(raw, dict):
        return ActionConfig()

    known = {
        k: v
        for k, v in raw.items()
        if k in ActionConfig.__dataclass_fields__ and k != "condition"
    }
    condition = _condition_from_dict(raw.get("condition"))
    extra = {
        k: v
        for k, v in raw.items()
        if k not in ActionConfig.__dataclass_fields__ and isinstance(v, list)
    }
    if extra:
        return {"__legacy__": ActionConfig(condition=condition, **known), **extra}
    return ActionConfig(condition=condition, **known)


def _unwrap_action(item: Any) -> ActionConfig:
    if isinstance(item, dict) and "__legacy__" in item:
        return item["__legacy__"]
    if isinstance(item, ActionConfig):
        return item
    if isinstance(item, dict):
        inner = _action_from_dict(item)
        return _unwrap_action(inner)
    return ActionConfig()


def _migrate_legacy_actions(actions: list[Any]) -> list[ActionConfig]:
    """Drop removed kinds from older documents, keeping the rest intact.

    Two flow kinds no longer exist:

    * ``if_else`` -- start/stop triggers already behave like an if/else pair
      over the stretch between them, so a dedicated branch step is redundant.
      An old step is unfolded into its "then" sub-sequence wrapped between a
      ``start_trigger`` (runs the stretch while the rule holds) and a
      ``stop_trigger`` (holds it again once the rule reappears); the "else"
      sub-sequence is dropped with a warning.  Nested branches are unfolded
      recursively.
    * ``loop`` -- looping is now expressed by a ``loop_start`` / ``loop_end``
      action pair, so stray old steps are simply dropped.
    """
    migrated: list[ActionConfig] = []
    for item in actions:
        if isinstance(item, dict) and "__legacy__" not in item and item.get("kind") == "if_else":
            item = {"__legacy__": _unwrap_action(item), **{k: v for k, v in item.items() if k != "kind"}}
        action = _unwrap_action(item)
        if action.kind == "if_else":
            condition = action.condition
            gated = condition is not None and getattr(condition, "configured", False)
            if gated:
                migrated.append(ActionConfig(kind="start_trigger", condition=condition))
            then_raw = item.get("then_actions", []) if isinstance(item, dict) else []
            migrated.extend(_migrate_legacy_actions(list(then_raw) if isinstance(then_raw, list) else []))
            else_raw = item.get("else_actions", []) if isinstance(item, dict) else []
            if isinstance(else_raw, list) and else_raw:
                logger.warning(
                    "Removed if_else step: %d 'else' action(s) dropped; 'then' "
                    "actions wrapped in a start/stop trigger pair instead",
                    len(else_raw),
                )
            if gated:
                migrated.append(ActionConfig(kind="stop_trigger", condition=condition))
            continue
        if action.kind == "loop":
            logger.warning("Removed legacy loop step; use loop_start/loop_end actions instead")
            continue
        migrated.append(action)
    return migrated


def _condition_from_dict(raw: Any) -> ScreenCondition | None:
    """Rebuild an optional :class:`ScreenCondition` from its JSON form."""
    if not isinstance(raw, dict):
        return None
    known = {k: v for k, v in raw.items() if k in ScreenCondition.__dataclass_fields__}
    if "region" in known:
        region = known["region"]
        if isinstance(region, (list, tuple)) and len(region) == 4:
            known["region"] = tuple(region)
        else:
            known.pop("region", None)
    if "all_regions" in known:
        extra = known["all_regions"]
        cleaned = []
        if isinstance(extra, (list, tuple)):
            for rect in extra:
                if isinstance(rect, (list, tuple)) and len(rect) == 4:
                    cleaned.append(tuple(int(v) for v in rect))
        known["all_regions"] = tuple(cleaned)
    return ScreenCondition(**known)


def _macro_from_dict(raw: Any) -> MacroConfig:
    if not isinstance(raw, dict):
        return MacroConfig(uid=new_macro_uid())

    known = {k: v for k, v in raw.items() if k in MacroConfig.__dataclass_fields__ and k != "actions"}
    raw_actions = raw.get("actions", [])
    if not isinstance(raw_actions, list):
        raw_actions = []
    actions = [_action_from_dict(a) for a in raw_actions]
    actions = _migrate_legacy_actions(actions)
    actions = _migrate_legacy_conditions(raw, actions)
    # Older files predate the uid field; assign a fresh stable identity.
    if not str(known.get("uid", "")):
        known["uid"] = new_macro_uid()
    migrated = MacroConfig(actions=tuple(actions), **known)
    if _needs_loop_migration(raw, actions):
        # Legacy general-section looping becomes a loop_start/loop_end action
        # pair; the repeat/loops/interval_ms fields are cleared so the actions
        # become the single source of truth.
        iterations = 0 if bool(raw.get("repeat", False)) else max(1, int(raw.get("loops", 1)) - 1)
        interval_ms = max(0, int(raw.get("interval_ms", 0)))
        wrapped = [ActionConfig(kind="loop_start", amount=iterations)]
        wrapped.extend(actions)
        wrapped.append(ActionConfig(kind="loop_end", duration_ms=interval_ms))
        migrated = replace(migrated, repeat=False, loops=1, interval_ms=0,
                           actions=tuple(wrapped))
    return migrated


def _needs_loop_migration(raw: dict[str, Any], actions: list[ActionConfig]) -> bool:
    """True when an old document still carries general-section looping.

    Earlier versions had ``repeat`` / ``loops`` / ``interval_ms`` options on
    the macro itself (a general setting next to the name and hotkey).
    Looping is now an *action*: wrap the sequence in a ``loop_start`` /
    ``loop_end`` pair carrying the iteration count (0 = forever) and the
    delay between passes.  Documents that already contain loop actions are
    left untouched.
    """
    try:
        repeat = bool(raw.get("repeat", False))
        loops = int(raw.get("loops", 1))
        interval_ms = int(raw.get("interval_ms", 0))
    except (TypeError, ValueError):
        return False
    if any(a.kind in LOOP_ACTION_KINDS for a in actions):
        return False
    return bool(actions) and (repeat or loops > 1 or interval_ms > 0)


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
    """Load settings from JSON, falling back to defaults on any problem.

    Tolerates valid JSON with the wrong shape (e.g. a top-level list or
    non-object macro entries) instead of raising ``AttributeError``.
    """
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return AppSettings()

        raw_macros = raw.get("macros", [])
        if not isinstance(raw_macros, list):
            return AppSettings()

        return AppSettings(
            macros=tuple(_macro_from_dict(m) for m in raw_macros),
            stop_hotkey=str(raw.get("stop_hotkey", "f8")),
            execution_delay_ms=int(raw.get("execution_delay_ms", 500)),
        )
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return AppSettings()


def save_settings(settings: AppSettings, path: Path = DEFAULT_SETTINGS_PATH) -> None:
    """Persist settings as pretty-printed JSON."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": SETTINGS_SCHEMA_VERSION,
        "macros": [_to_plain(m) for m in settings.macros],
        "stop_hotkey": settings.stop_hotkey,
        "execution_delay_ms": settings.execution_delay_ms,
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


#: Extensions accepted by the single-macro import feature.
MACRO_FILE_EXTENSIONS: tuple[str, ...] = (".json", ".macro")


def _looks_like_macro(raw: Any) -> bool:
    return isinstance(raw, dict) and ("actions" in raw or "name" in raw)


def save_macro_json(macro: MacroConfig, path: Path) -> None:
    """Write exactly one macro to *path* (self-contained, human-readable)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"kind": "macro", "schema_version": SETTINGS_SCHEMA_VERSION, **_to_plain(macro)}
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def load_macro_json(path: Path) -> MacroConfig:
    """Read a single macro from *path*.

    Accepts files written by :func:`save_macro_json` as well as any JSON
    object that looks like a macro (``{"name": ..., "actions": [...]}``) --
    including whole-settings documents, from which the first macro is taken.
    Raises ``ValueError`` when the file holds no usable macro.
    """
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, dict):
        macros = raw.get("macros")
        if isinstance(macros, list) and macros and isinstance(macros[0], dict):
            return _macro_from_dict(macros[0])
        if _looks_like_macro(raw):
            return _macro_from_dict(raw)
    raise ValueError(f"{path} does not contain a macro")


def import_macro_files(paths: list[Path], existing: Sequence[MacroConfig]) -> list[MacroConfig]:
    """Load one or more macro files into fresh, collision-free configs.

    Names are de-duplicated against *existing* (``"X"`` -> ``"X (imported)"``
    -> ``"X (imported 2)"``...), uids are always regenerated so imports never
    impersonate an already-known macro, and every imported macro starts
    disabled with its hotkey cleared -- importing must not silently steal a
    hotkey the user already assigned elsewhere.
    """
    taken_names = {m.name for m in existing}
    imported: list[MacroConfig] = []
    for path in paths:
        macro = load_macro_json(path)
        name = macro.name.strip() or "Imported macro"
        if name in taken_names:
            candidate = f"{name} (imported)"
            counter = 2
            while candidate in taken_names:
                candidate = f"{name} (imported {counter})"
                counter += 1
            name = candidate
        taken_names.add(name)
        imported.append(
            replace(macro, name=name, uid=new_macro_uid(), start_hotkey="", enabled=False)
        )
    return imported


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
