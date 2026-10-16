"""The mood read (caring memory's per-turn read) in recall, step 2 part 4 (the
who-is-talking plan, A1). Run through the real build_turn_context:

- on a guest's turn it is not taken: it reads how the OWNER is doing, and a
  guest turn's prompt dropped it anyway (context_adapter's P(); reflection
  never runs for guests), so the call was paid for nothing;
- in a shared room it reads the room the runner set (guests' lines included),
  not the conversations database, which never holds a guest's words;
- with her first look in charge (R14, T5: ``ai.turn_read.first_look_in_charge``,
  set by the runner around recall) recall takes none on any turn — the
  runner's look is the turn's one read, and the runner hands it on
  (tests/agent/test_first_look_runner.py). With the switch down, today's.
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Dict
from unittest.mock import AsyncMock

import pytest

from tests.turn.test_recall_stakes import _build_turn, _load_recall, _stub_recall_dependencies


def _capture_the_read(monkeypatch) -> Dict[str, Any]:
    import ai.turn_read as turn_read_mod

    captured: Dict[str, Any] = {"calls": 0}

    async def fake_take_turn_read(**kwargs):
        captured["calls"] += 1
        captured.update(kwargs)
        return None

    monkeypatch.setattr(turn_read_mod, "take_turn_read", fake_take_turn_read)
    monkeypatch.setattr(turn_read_mod, "render_recent_arc", AsyncMock(return_value=""))
    return captured


@pytest.mark.asyncio
async def test_a_guests_turn_takes_no_mood_read(monkeypatch):
    _stub_recall_dependencies(monkeypatch)
    captured = _capture_the_read(monkeypatch)
    import ai.turn_read as turn_read_mod

    from dataclasses import replace

    turn = _build_turn("hey palli")
    turn = replace(turn, metadata=replace(turn.metadata, origin="guest"))
    ctx = await _load_recall().build_turn_context(turn)
    assert captured["calls"] == 0
    assert turn_read_mod.render_recent_arc.await_count == 0
    assert ctx.turn_read is None and ctx.emotional_arc_text == ""


@contextmanager
def _the_look_in_charge(on: bool):
    """The runner's switch around recall (R14, T5), as the runner sets it."""
    import ai.turn_read as turn_read_mod

    token = turn_read_mod.first_look_in_charge.set(True) if on else None
    try:
        yield
    finally:
        if token is not None:
            turn_read_mod.first_look_in_charge.reset(token)


@pytest.mark.asyncio
@pytest.mark.parametrize("look_in_charge", [False, True])
async def test_the_owners_turn_takes_one_unless_the_look_is_in_charge(monkeypatch, look_in_charge):
    _stub_recall_dependencies(monkeypatch)
    captured = _capture_the_read(monkeypatch)
    import ai.turn_read as turn_read_mod

    with _the_look_in_charge(look_in_charge):
        ctx = await _load_recall().build_turn_context(_build_turn("hey palli"))
    if look_in_charge:
        # Recall takes none, and reads no arc for it either: the runner's
        # look supplies the read and the slice's arc.
        assert captured["calls"] == 0
        assert turn_read_mod.render_recent_arc.await_count == 0
        assert ctx.turn_read is None and ctx.emotional_arc_text == ""
    else:
        assert captured["calls"] == 1
    assert turn_read_mod.first_look_in_charge.get() is False


@pytest.mark.asyncio
@pytest.mark.parametrize("look_in_charge", [False, True])
async def test_in_a_shared_room_the_mood_read_reads_the_room(monkeypatch, look_in_charge):
    from core.agent import room_tail

    _stub_recall_dependencies(monkeypatch)
    captured = _capture_the_read(monkeypatch)
    room = "\n".join(f"(20:{i:02d}) [Sam]: line {i}" for i in range(12))
    token = room_tail.current_room_tail.set(room)
    try:
        with _the_look_in_charge(look_in_charge):
            await _load_recall().build_turn_context(_build_turn("how are you all?"))
    finally:
        room_tail.current_room_tail.reset(token)
    if look_in_charge:
        assert captured["calls"] == 0                # the look read the room instead
        return
    said = captured["recent_conversation"].splitlines()
    assert said == room.splitlines()[-8:]          # the room's last eight lines, as today's depth
