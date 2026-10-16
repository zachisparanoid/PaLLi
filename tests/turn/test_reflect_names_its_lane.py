"""The reflect lane (2026-08-21): her post-turn reflection — memory notes,
corrections of record, quest and fear filings — called the provider with
NO log_context for four months (30 s ceiling since 04-27), ran as
`general`, and when all four attempts timed out it returned None without
a word (`if not raw_text: return None`). Since the 08-09 timeout split:
144 attempt timeouts, 25 exchanges whose reflection was lost silently —
the #1 timing-out lane in the house, invisible because it had no name.

Two things pinned here: the lane is NAMED (so the patient allowlist can
hold it and the logs/activity monitor can see it), and nothing-back is
said out loud at WARNING."""
from __future__ import annotations

import logging
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from core.turn import reflect as reflect_module


def _ctx():
    return SimpleNamespace(
        compiled_identity=None,
        turn=SimpleNamespace(owner_id=42, content="hey", received_at=datetime(2026, 8, 21, 20, 0)),
        unfulfilled_first_contact_targets=[],
        turn_read=None,
    )


class _FakeLLM:
    def __init__(self, reply):
        self.reply, self.calls = reply, []

    async def chat_completion(self, **kw):
        self.calls.append(kw)
        return self.reply


def _harness(fake):
    return (
        patch.object(reflect_module.prompt_loader, "load_prompt", return_value="reflect on: {user_message} / {assistant_reply}"),
        patch.object(reflect_module, "brain", SimpleNamespace(list_active_open_threads=AsyncMock(return_value=[]))),
        patch.object(reflect_module, "_resolve_reflect_model", lambda: "fake-model"),
        patch.object(reflect_module, "llm", fake),
    )


@pytest.mark.asyncio
async def test_the_reflect_lane_names_itself():
    fake = _FakeLLM(reply=None)
    a, b, c, d = _harness(fake)
    with a, b, c, d:
        await reflect_module._extract_plan(_ctx(), "a reply")
    assert len(fake.calls) == 1
    ctx = fake.calls[0].get("log_context")
    assert ctx == "reflect_turn", f"unnamed lane: {ctx!r}"  # one constant: single owner, clean logs, per-lane telemetry


@pytest.mark.asyncio
async def test_nothing_back_is_said_out_loud(caplog):
    fake = _FakeLLM(reply=None)
    a, b, c, d = _harness(fake)
    with a, b, c, d, caplog.at_level(logging.WARNING, logger="core.turn.reflect"):
        plan = await reflect_module._extract_plan(_ctx(), "a reply")
    assert plan is None
    said = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    assert any("no content" in m and "lost" in m for m in said), f"silent again: {said}"
