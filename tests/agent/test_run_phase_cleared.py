"""A run's phase is cleared on every way out (Fable's review of web-page
reminders, 2026-10-10).

``set_run_phase(state_key, "prep")`` runs near the top of run_turn; the
``finally`` that clears it belongs to the loop much further down. An early
return in between (a recall that failed) left the phase set until that
chat's next run. The web-page reminder waits for the phase to clear before
writing into her transcript, so a stuck phase made it wait 30 minutes and
write nothing. The Discord fork reads the same phase to choose barge-in or
steer.
"""
from __future__ import annotations

import asyncio

from core.agent import steering
from tests.agent import test_grounding_check as g


def test_a_run_whose_recall_failed_leaves_no_phase_behind(tmp_path, monkeypatch):
    mod = g._load_runner()
    turn = g.FakeTurn()
    state_key = mod._run_state_key(turn, mod._derive_session_key(turn))
    deps = g._deps(mod, tmp_path, g.EchoRegistry())

    async def broken_recall(turn):
        raise RuntimeError("the memory store is down")

    deps.build_turn_context = broken_recall
    steering.clear_run_state(state_key)
    result = asyncio.run(mod.run_turn(turn, deps=deps))
    assert "recall_failed" in str(result.error)
    assert steering.get_run_phase(state_key) == ""
