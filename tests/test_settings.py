"""Headless tests for the settings document: round-trips, tolerance, import/export.

Everything here is pure logic -- no display, no screen capture, no input
synthesis -- so it runs in CI on any platform.
"""

from __future__ import annotations

import json
from dataclasses import replace

import pytest

from app.conditions import ScreenCondition
from app.settings import (
    SETTINGS_SCHEMA_VERSION,
    ActionConfig,
    AppSettings,
    MacroConfig,
    import_macro_files,
    load_macro_json,
    load_settings,
    new_macro_uid,
    rename_in_settings,
    save_macro_json,
    save_settings,
)


def sample_macro(name: str = "Demo") -> MacroConfig:
    return MacroConfig(
        name=name,
        start_hotkey="ctrl+f6",
        uid=new_macro_uid(),
        enabled=False,
        actions=(
            ActionConfig(kind="loop_start", amount=2),
            ActionConfig(kind="combo", combo="ctrl+c"),
            ActionConfig(
                kind="wait_for",
                duration_ms=4000,
                condition=ScreenCondition(
                    mode="image_found", template_path="t.png", confidence=0.9
                ),
            ),
            ActionConfig(kind="drag", x=10, y=20, amount=30, duration_ms=40),
            ActionConfig(
                kind="stop_trigger",
                condition=ScreenCondition(mode="region_changed"),
            ),
            ActionConfig(kind="key", key="a"),
            ActionConfig(
                kind="start_trigger",
                condition=ScreenCondition(mode="region_changed"),
            ),
            ActionConfig(kind="loop_end", duration_ms=250),
        ),
    )


# --------------------------------------------------------------------- settings
def test_settings_round_trip(tmp_path):
    path = tmp_path / "settings.json"
    original = AppSettings(macros=(sample_macro("One"), sample_macro("Two")), stop_hotkey="f9")
    save_settings(original, path)
    loaded = load_settings(path)
    assert loaded.stop_hotkey == "f9"
    assert [m.name for m in loaded.macros] == ["One", "Two"]
    first = loaded.macros[0]
    assert first.uid == original.macros[0].uid  # stable identity survives saves
    assert first.enabled is False
    assert first.actions == original.macros[0].actions
    kinds = [a.kind for a in first.actions]
    assert "stop_trigger" in kinds and "start_trigger" in kinds
    assert "if_else" not in kinds  # removed flow kind never round-trips
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == SETTINGS_SCHEMA_VERSION


def test_load_missing_file_returns_defaults(tmp_path):
    loaded = load_settings(tmp_path / "does-not-exist.json")
    assert loaded.macros == ()


@pytest.mark.parametrize(
    "content",
    [
        "not json at all",
        "[]",                       # valid JSON, wrong shape
        '{"macros": "oops"}',       # wrong type for macros
        '{"macros": [42, null]}',   # junk entries inside the list
    ],
)
def test_load_tolerates_garbage_without_raising(tmp_path, content):
    path = tmp_path / "settings.json"
    path.write_text(content, encoding="utf-8")
    loaded = load_settings(path)  # must fall back, not raise AttributeError
    assert isinstance(loaded, AppSettings)


def test_unknown_action_fields_are_dropped(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(
        json.dumps(
            {
                "macros": [
                    {"name": "M", "actions": [{"kind": "key", "key": "z", "bogus": 1}]}
                ]
            }
        ),
        encoding="utf-8",
    )
    macro = load_settings(path).macros[0]
    assert macro.actions[0].key == "z"


def test_legacy_if_else_is_migrated_to_trigger_pair(tmp_path):
    """Removed ``if_else`` steps unfold into a start/stop trigger pair.

    Old documents could contain an *If / else* flow step with nested branch
    sub-sequences.  That kind no longer exists -- start/stop triggers provide
    the same if/else gating directly -- so on load the "then" actions are
    wrapped between a ``start_trigger`` and a ``stop_trigger`` carrying the
    old condition, and any "else" actions are dropped with a warning.
    """
    path = tmp_path / "settings.json"
    path.write_text(
        json.dumps(
            {
                "macros": [
                    {
                        "name": "Legacy",
                        "actions": [
                            {"kind": "key", "key": "z"},
                            {
                                "kind": "if_else",
                                "condition": {"mode": "image_found", "template_path": "x.png"},
                                "then_actions": [{"kind": "key", "key": "a"}],
                                "else_actions": [{"kind": "key", "key": "b"}],
                            },
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    macro = load_settings(path).macros[0]
    kinds = [a.kind for a in macro.actions]
    assert "if_else" not in kinds
    assert kinds == ["key", "start_trigger", "key", "stop_trigger"]
    gate = macro.actions[1]
    assert gate.condition is not None and gate.condition.template_path == "x.png"
    assert macro.actions[2].key == "a"


def test_legacy_general_looping_migrates_to_loop_actions(tmp_path):
    """Old repeat/loops/interval_ms options become a loop_start/loop_end pair."""
    path = tmp_path / "settings.json"
    path.write_text(
        json.dumps(
            {
                "macros": [
                    {
                        "name": "Looped",
                        "repeat": True,
                        "loops": 3,
                        "interval_ms": 250,
                        "actions": [{"kind": "key", "key": "a"}],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    macro = load_settings(path).macros[0]
    assert macro.repeat is False and macro.loops == 1 and macro.interval_ms == 0
    kinds = [a.kind for a in macro.actions]
    assert kinds == ["loop_start", "key", "loop_end"]
    assert macro.actions[0].amount == 0  # repeat -> forever (0 iterations)
    assert macro.actions[2].duration_ms == 250


# ------------------------------------------------------------------ macro export
def test_macro_export_import_round_trip(tmp_path):
    macro = sample_macro("Exported")
    path = tmp_path / "exported.json"
    save_macro_json(macro, path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["kind"] == "macro"
    reloaded = load_macro_json(path)
    assert reloaded.name == "Exported"
    assert reloaded.actions == macro.actions  # loop + trigger actions included


def test_load_macro_json_accepts_whole_settings_document(tmp_path):
    path = tmp_path / "settings.json"
    save_settings(AppSettings(macros=(sample_macro("A"), sample_macro("B"))), path)
    macro = load_macro_json(path)
    assert macro.name == "A"


def test_load_macro_json_rejects_non_macro(tmp_path):
    path = tmp_path / "junk.json"
    path.write_text('{"hello": "world"}', encoding="utf-8")
    with pytest.raises(ValueError):
        load_macro_json(path)


def test_import_dedupes_names_and_disarms_hotkeys(tmp_path):
    existing = [sample_macro("Copy"), sample_macro("Copy (imported)")]
    files = []
    for i in range(3):
        p = tmp_path / f"m{i}.json"
        save_macro_json(sample_macro("Copy"), p)
        files.append(p)
    imported = import_macro_files(files, existing)
    names = [m.name for m in imported]
    assert names == ["Copy (imported 2)", "Copy (imported 3)", "Copy (imported 4)"]
    for macro in imported:
        assert macro.start_hotkey == ""      # never steal an assigned hotkey
        assert macro.enabled is False        # parked until the user opts in
        assert macro.uid not in {m.uid for m in existing}
    # uids unique among themselves too
    assert len({m.uid for m in imported}) == 3


# ----------------------------------------------------------------------- rename
def test_rename_targets_uid_not_name():
    macro = sample_macro("Old")
    settings = AppSettings(macros=(macro, sample_macro("Other")))
    # The app's rename path: main_window detects the uid-based rename and
    # calls rename_in_settings with (old_name, new_name).  A uid passed as
    # old_name must NOT rename anything -- only exact name matches count.
    renamed = rename_in_settings(settings, "Old", "New")
    by_uid = {m.uid: m.name for m in renamed.macros}
    assert by_uid[macro.uid] == "New"
    assert by_uid[settings.macros[1].uid] == "Other"


def test_rename_does_not_fall_back_to_uid_matching():
    """rename_in_settings is name-keyed; passing a uid renames nothing."""
    macro = sample_macro("Old")
    settings = AppSettings(macros=(macro,))
    unchanged = rename_in_settings(settings, macro.uid, "Whatever")
    assert unchanged.macros[0].name == "Old"


def test_uid_rename_detection_end_to_end():
    """Simulate MainWindow._on_macros_changed: real renames follow the uid,
    while delete-one/add-one is never mistaken for a rename (the old bug)."""

    def apply_change(settings: AppSettings, macros: list[MacroConfig]) -> AppSettings:
        old_names_by_uid = {m.uid: m.name for m in settings.macros if m.uid}
        updated: list[MacroConfig] = []
        renames: list[tuple[str, str]] = []
        for macro in macros:
            uid = macro.uid or next(
                (known for known, name in old_names_by_uid.items() if name == macro.name), ""
            )
            previous_name = old_names_by_uid.get(uid)
            if previous_name is not None and previous_name != macro.name:
                renames.append((previous_name, macro.name))
            updated.append(replace(macro, uid=uid) if uid != macro.uid else macro)
        result = replace(settings, macros=tuple(updated))
        for old_name, new_name in renames:
            result = rename_in_settings(result, old_name, new_name)
        return result

    keep = sample_macro("Keep")
    drop = sample_macro("Drop")
    settings = AppSettings(macros=(keep, drop))

    # Case 1: genuine rename of "Keep" -> "Renamed" (same uid).
    renamed_list = [replace(keep, name="Renamed"), drop]
    after = apply_change(settings, renamed_list)
    assert {m.uid: m.name for m in after.macros}[keep.uid] == "Renamed"

    # Case 2: delete one macro, add a brand-new one in the same edit --
    # the newcomer must keep its own name (old heuristic renamed it to "Drop").
    fresh = sample_macro("Fresh")
    after = apply_change(settings, [keep, fresh])
    names = {m.uid: m.name for m in after.macros}
    assert names[fresh.uid] == "Fresh"
    assert names[keep.uid] == "Keep"
