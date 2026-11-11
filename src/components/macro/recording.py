"""Convert recorded input events into compact macro actions (pure logic).

This is the *recording half* of the macro domain: it turns a timestamped
stream of raw input events -- the shape the recorder dialog collects
through :class:`~components.input.recorder.InputRecorder` -- into the
ordered :class:`~components.macro.macro.ActionConfig` list a macro stores.
It is deliberately free of Qt and pynput imports so every layer can use it
and it stays testable headless; the dialog that captures the events lives
in :mod:`components.macro_editor.recorder_dialog`.

Event-stream contract:

* each event is ``(timestamp_s, kind, payload)`` with ``timestamp_s`` in
  seconds since the recording started (monotonic clock);
* ``kind`` is one of ``move`` / ``key_down`` / ``key_up`` / ``scroll`` /
  ``button_down`` / ``button_up``;
* ``payload["key"]`` / ``payload["button"]`` carry canonical names
  (``"a"``, ``"f5"``, ``"left"``) -- side-specific modifiers such as
  ``ctrl_l`` are canonicalised by the dialog before appending;
* ``move`` payloads carry ``x`` / ``y``; ``scroll`` carries ``amount``;
  button events carry the press/release position in ``x`` / ``y``.
"""

from __future__ import annotations

from components.macro.macro import ActionConfig


def events_to_actions(events: list[tuple[float, str, dict]]) -> list[ActionConfig]:
    """Coalesce timestamped raw events into compact macro steps.

    Rules:

    * key press+release <= 250 ms apart -> ``key``; longer -> ``hold_key``.
    * consecutive mouse moves are thinned to the last position of each burst.
    * button press/release pairs shorter than 300 ms with little movement
      (<= 5 px) become ``click``; otherwise a single **drag** step (start =
      press point, end = release point) -- one semantic action instead of
      three raw ones.
    * idle gaps between events become ``wait`` steps (rounded to 10 ms,
      minimum 20 ms).
    """
    actions: list[ActionConfig] = []
    pending_wait = 0.0
    previous_time = 0.0
    opened: dict[str, tuple[float, dict]] = {}
    move_buffer: tuple[int, int] | None = None

    def flush_wait() -> None:
        nonlocal pending_wait
        ms = int(round(pending_wait * 100) * 10)
        if ms >= 20:
            actions.append(ActionConfig(kind="wait", duration_ms=ms))
        pending_wait = 0.0

    def flush_move() -> None:
        nonlocal move_buffer
        if move_buffer is not None:
            actions.append(ActionConfig(kind="move", x=move_buffer[0], y=move_buffer[1]))
            move_buffer = None

    for stamp, kind, payload in events:
        pending_wait += stamp - previous_time
        previous_time = stamp

        if kind == "move":
            move_buffer = (payload["x"], payload["y"])
            continue

        if kind == "key_down":
            flush_move()
            flush_wait()
            opened[f"key:{payload['key']}"] = (stamp, payload)
            continue

        if kind == "key_up":
            pair = opened.pop(f"key:{payload['key']}", None)
            if pair is None:
                continue
            held_ms = int((stamp - pair[0]) * 1000)
            key = pair[1]["key"]
            if held_ms <= 250:
                actions.append(ActionConfig(kind="key", key=key))
            else:
                actions.append(ActionConfig(kind="hold_key", key=key, duration_ms=held_ms))
            pending_wait = 0.0
            continue

        if kind == "scroll":
            flush_move()
            flush_wait()
            actions.append(ActionConfig(kind="scroll", amount=payload["amount"]))
            continue

        if kind == "button_down":
            flush_move()
            flush_wait()
            opened[f"button:{payload['button']}"] = (stamp, payload)
            continue

        if kind == "button_up":
            pair = opened.pop(f"button:{payload['button']}", None)
            if pair is None:
                continue
            held_ms = int((stamp - pair[0]) * 1000)
            moved = abs(payload["x"] - pair[1]["x"]) + abs(payload["y"] - pair[1]["y"])
            button = payload["button"]
            if held_ms <= 300 and moved <= 5:
                actions.append(ActionConfig(kind="click", button=button))
            else:
                # A press/release pair with travel is a drag gesture: emit one
                # semantic ``drag`` step (start -> end) instead of the raw
                # mouse_down/move/mouse_up triple, which replays brittlely.
                # NOTE: drag reuses amount/duration_ms for the end point --
                # see ActionConfig's field-overloading warning in components.macro.macro.
                actions.append(
                    ActionConfig(
                        kind="drag",
                        button=button,
                        x=pair[1]["x"],
                        y=pair[1]["y"],
                        amount=payload["x"],
                        duration_ms=payload["y"],
                    )
                )
            pending_wait = 0.0
            continue

    flush_move()
    flush_wait()
    return actions
