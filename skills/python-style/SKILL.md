---
name: python-style
description: Write code matching this repo's style — modern typing, module docstrings, dataclass domain models, per-module loggers, import ordering, naming. Use for any Python file edit or creation.
---

# Python Style & Code Structure

Readable, explicit, boringly consistent. Match the surrounding file before
inventing a new idiom.

## File anatomy (order is fixed)

```python
"""One-paragraph module docstring: what this module is and who may use it.
Mention design decisions a future reader/agent needs (e.g. why something is
thread-local, why a field is kept for migration)."""

from __future__ import annotations          # every module has this

import json                                 # 1. stdlib
import logging

from dataclasses import dataclass           # 2. third-party (PySide6, cv2, ...)
from PySide6.QtCore import Signal

from common.paths import SETTINGS_PATH      # 3. local (bare roots: app/components/common)
from components.macro.macro import MacroConfig

logger = logging.getLogger(__name__)        # 4. module logger, right after imports

#: Public constant with a `#:`-style doc comment.
CONDITION_ACTION_KINDS: frozenset[str] = frozenset({"wait_for", "stop_trigger"})
```

- No `sys.path` manipulation, no wildcard imports, no circular-import tricks
  beyond the documented lazy-import pattern inside functions (use sparingly
  and *always* comment why).
- Private helpers are prefixed `_`; anything re-exported by a package
  `__init__.py` is public API — keep names stable or update all callers.

## Typing

- Full type hints on every public function/method signature; use builtin
  generics (`dict[str, int]`, `list[ActionConfig]`, `X | None`) — never
  `typing.Dict`/`Optional`.
- Dataclasses are the default model shape:
  - Domain models in `components/macro/` (`ActionConfig`, `MacroConfig`,
    `AppSettings`, `ScreenCondition`) are all `@dataclass(frozen=True)` with
    a class docstring and tuple-valued collections; "editing" means
    `dataclasses.replace` + swap, never in-place mutation.
  - Field-level comments use `#:` only where meaning isn't obvious from name.
- Return `None` implicitly; don't annotate `-> None` on trivial setters if
  the file doesn't already do so — consistency wins over dogma.

## Naming

| Thing | Convention | Example |
|---|---|---|
| Modules | snake_case, noun-ish | `macro_flow.py`, `condition_runtime.py` |
| Classes | CapWords | `MacroEngine`, `ExecutionToken` |
| Action-kind strings | snake_case literals | `"wait_for"`, `"stop_trigger"` |
| Constants | UPPER_SNAKE at module level | `CONDITION_ACTION_KINDS` |
| Booleans | `is_/has_/should_` prefix | `is_modifier_name()` |
| Events/counters | verb past-tense for fired events | `macro_started`, `loops_completed` |
| Qt signals | past-tense event names | `changed`, `status_message` |

Never shadow builtins (`id`, `type`, `list` as params). Key/button names are
lowercase canonical strings (`"ctrl+f6"`, `"left"`) produced/parsed via
`common/events.py` — don't hand-roll string splitting elsewhere.

## Docstrings & comments

- Functions/classes: one-line imperative summary ("Return ...", "Arm the
  rule ..."), multi-line detail only when behaviour surprises.
- Comment the *why* of non-obvious control flow (deadlock bypasses,
  cancellation races, migration edge cases); delete comments that restate
  the code.
- TODOs must be actionable and owned: `# TODO(<area>): <specific change>`.

## Logging

- `logger = logging.getLogger(__name__)` per module; never `print()`.
- Levels: `debug` = per-tick/per-action chatter, `info` = lifecycle
  (start/stop/save), `warning` = recoverable oddities (dropped legacy
  actions, unknown kind), `error` = caught failures that degrade a feature.
- Use `%s` lazy formatting (`logger.info("started %s", macro.name)`), not
  f-strings, in logging calls.
- Anything user-visible at runtime also goes to the status bar via engine
  signals — logs alone are not feedback.

## Errors & robustness

- Catch narrowly at boundaries (file IO, hotkey parsing, screen capture);
  re-raise or degrade explicitly with a warning — never silent `except: pass`.
- Validation lives in the layer that owns the data (`store.py` sanitises
  imports; `condition.parse_event` rejects malformed rules) — UI trusts but
  still shows messages.

## Checklist

- [ ] Module docstring updated to reflect what changed.
- [ ] Imports grouped stdlib → third-party → local, bare roots only.
- [ ] New public functions fully typed; new models are dataclasses.
- [ ] `logging` used (lazy `%s`), no prints.
- [ ] Names follow the table above; no dead code left behind.
- [ ] `python -m compileall src tests` passes; diff introduces no
      `sys.path` edits or wildcard imports.

Related: `docs/general/development_guide.md` (style guardrails section).
