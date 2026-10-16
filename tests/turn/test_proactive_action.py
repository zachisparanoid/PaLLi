"""Tests for the proactive-action path in core.turn.proactive.

D.4 step 4b (2026-05-13): patched name swapped from
``core.turn.proactive.process_turn`` to ``core.turn.proactive.dispatch_turn``
when proactive.py was rewired through dispatch_turn (honors
PALLI_AGENT_LOOP). Same TurnResult contract on either side.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# The three pre-cutover routing tests that lived here (parked with
# skip reason "until/unless the candidate flow is rewired") were
# superseded on 2026-07-01: the candidate flow IS rewired, with the
# post-cutover contract (observations ingest into the unified
# open_threads queue instead of dispatching directly). The rewired
# routing contract lives in tests/turn/test_candidate_rail.py.



@pytest.mark.asyncio
async def test_consider_proactive_action_dispatches_read_file_for_path_like_hint():
    from core.turn import proactive

    candidate = MagicMock()
    candidate.text = "look at bridge.py"
    candidate.action_hint = "ai/delegation/bridge.py"
    candidate.timestamp = 0.0

    fake_result = MagicMock()
    fake_result.allowed = True
    fake_result.is_binary = False
    fake_result.to_text_for_voicer = MagicMock(return_value="--- bridge.py ---\nfile contents here")

    with patch(
        "core.turn.local_read_file.execute_local_read_file", return_value=fake_result,
    ), patch(
        "core.turn.proactive.dispatch_turn", new=AsyncMock(),
    ) as message_path:
        await proactive.consider_proactive_action(owner_id=1, candidate=candidate)
    message_path.assert_awaited_once()
    sent_turn = message_path.call_args.args[0]
    assert sent_turn.metadata.origin == "proactive_action"
    assert "autonomous_action_report" in sent_turn.content


@pytest.mark.asyncio
async def test_consider_proactive_action_dispatches_local_shell_for_command_like_hint():
    from core.turn import proactive

    candidate = MagicMock()
    candidate.text = "check git state"
    candidate.action_hint = "git status"
    candidate.timestamp = 0.0

    fake_result = MagicMock()
    fake_result.allowed = True
    fake_result.return_code = 0
    fake_result.to_text_for_voicer = MagicMock(return_value="$ git status\nclean working tree")

    with patch(
        "core.turn.local_shell.execute_local_shell", new=AsyncMock(return_value=fake_result),
    ) as shell_call, patch(
        "core.turn.proactive.dispatch_turn", new=AsyncMock(),
    ) as message_path:
        await proactive.consider_proactive_action(owner_id=1, candidate=candidate)
    shell_call.assert_awaited_once()
    message_path.assert_awaited_once()
    sent_turn = message_path.call_args.args[0]
    assert sent_turn.metadata.origin == "proactive_action"


@pytest.mark.asyncio
async def test_consider_proactive_action_silent_on_empty_hint():
    from core.turn import proactive

    candidate = MagicMock()
    candidate.text = ""
    candidate.action_hint = ""
    candidate.timestamp = 0.0

    with patch(
        "core.turn.proactive.dispatch_turn", new=AsyncMock(),
    ) as message_path:
        await proactive.consider_proactive_action(owner_id=1, candidate=candidate)
    message_path.assert_not_called()
