"""Arrival-gate tests for the ambient service.

Regression home for the 2026-07-10 "welcome home mid-conversation" bug:
a presence event fired five minutes after an active exchange and
_plan_arrival_action greeted the owner ("Hey, welcome home. Glad you're
back.") because nothing in the gate consulted conversation recency. A
person does not welcome-home someone they were just talking to.
"""
from __future__ import annotations

import time
from unittest.mock import AsyncMock

import pytest

from core.ambient_service import ambient_service


def _arrival_event(now: float) -> dict:
    return {"event_type": "owner_arrived", "occurred_at": now, "metadata": {}}


def _context_snapshot() -> dict:
    return {
        "privacy_state": "private",
        "quiet_hours_active": False,
        "machine_state": "active",
    }


def _patch_gate_deps(monkeypatch, *, last_message_age_seconds: float | None):
    """Stub the gate's collaborators: no prior arrival actions, no runtime
    jobs, and a conversations table whose newest user row is
    ``last_message_age_seconds`` old (None = no conversation history)."""
    now = time.time()
    monkeypatch.setattr(
        "core.ambient_service.brain.list_ambient_actions",
        AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(
        "core.ambient_service.runtime_control.list_jobs",
        AsyncMock(return_value=[]),
    )
    last_at = 0.0 if last_message_age_seconds is None else now - last_message_age_seconds
    monkeypatch.setattr(
        "core.turn.proactive._safe_last_user_message_at",
        AsyncMock(return_value=last_at),
    )
    return now


@pytest.mark.asyncio
async def test_arrival_suppressed_when_conversation_is_recent(monkeypatch):
    now = _patch_gate_deps(monkeypatch, last_message_age_seconds=300)  # 5 min ago

    decision = await ambient_service._plan_arrival_action(
        owner_user_id=1,
        event=_arrival_event(now),
        context_snapshot=_context_snapshot(),
        previous_context={},
        policy={},
    )

    assert decision["should_act"] is False
    assert decision["suppression_reason"] == "recent_conversation"


@pytest.mark.asyncio
async def test_arrival_proceeds_when_conversation_is_stale(monkeypatch):
    now = _patch_gate_deps(monkeypatch, last_message_age_seconds=3 * 3600)  # 3h ago

    decision = await ambient_service._plan_arrival_action(
        owner_user_id=1,
        event=_arrival_event(now),
        context_snapshot=_context_snapshot(),
        previous_context={},
        policy={},
    )

    assert decision["should_act"] is True
    assert decision["action_type"] == "arrival_greeting"


@pytest.mark.asyncio
async def test_arrival_proceeds_when_no_conversation_history(monkeypatch):
    """A fresh install (no conversations at all) must still greet — the
    helper returns 0.0 on empty history and that must not read as
    'moments ago'."""
    now = _patch_gate_deps(monkeypatch, last_message_age_seconds=None)

    decision = await ambient_service._plan_arrival_action(
        owner_user_id=1,
        event=_arrival_event(now),
        context_snapshot=_context_snapshot(),
        previous_context={},
        policy={},
    )

    assert decision["should_act"] is True
