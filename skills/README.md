# Agent Skills

Task-oriented skill guides for AI agents (and humans) working on this repo.
Each skill is a self-contained playbook: **when to load it, the rules it
enforces, recipes, and a checklist**.

Skills state *actionable rules*; the deeper "why" lives in `docs/` — every
skill links the relevant docs instead of duplicating them.

## Index

| Skill | Load when... |
|---|---|
| [`project-structure/SKILL.md`](project-structure/SKILL.md) | Creating/moving files or modules, adding features, deciding where code belongs, touching `data/` paths |
| [`python-style/SKILL.md`](python-style/SKILL.md) | Writing or editing any Python file — naming, docstrings, typing, imports, logging, dataclasses |
| [`threading-and-performance/SKILL.md`](threading-and-performance/SKILL.md) | Touching playback engine, monitor loop, hotkeys, recorder, screen capture, or anything that runs per-frame/per-tick |
| [`macro-domain/SKILL.md`](macro-domain/SKILL.md) | Changing action kinds, conditions, settings schema, serialization or migrations |
| [`ui-components/SKILL.md`](ui-components/SKILL.md) | Working on Qt tabs, dialogs, widgets, signals, region picker / capture tools |
| [`testing/SKILL.md`](testing/SKILL.md) | Adding/adjusting tests, verifying a change headlessly, choosing monkeypatch seams |

## How to use

1. Identify the task type from the index above and read that skill first.
2. Most non-trivial changes activate 2–3 skills (e.g. "add a new action
   kind" → macro-domain + project-structure + testing).
3. When a skill and the code disagree, the code + `docs/` win — fix the
   skill in the same commit.
