"""Candidate rail: inner-monologue hunches become open-thread rows.

2026-07-01 rewire; step 19 (2026-08-19) re-homed the rail: the old
consider_outreach entry is DELETED and the rail now rides
run_outreach_residuals on the moment engine's loop, so this suite enters
_consider_candidate_rail directly. The contract is unchanged:

- OBSERVATION candidates are ingested into the unified open_threads
  queue as ACTIVE topic rows with monologue provenance in ``source_id``;
  the arbiter's thread-board digest is what raises them now.
- ACTION candidates run ``consider_proactive_action`` when
  ``features.proactive_actions`` is enabled — and return True (the rail
  "fired").
- A disabled action candidate falls back to ingestion: the hunch gets
  *said* eventually instead of silently dying.
- Every routed candidate writes a consumption record via
  ``create_proactive_action`` citing ``candidate_tick_timestamp`` so
  the consumed-check and rumination guard in
  ``ai/proactive_candidate_source.py`` keep working unchanged.
- The rail never raises — a failure returns False.
"""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _settings_stub(*, actions: bool = False):
    return SimpleNamespace(
        snapshot=lambda: SimpleNamespace(
            features=SimpleNamespace(proactive_actions=actions),
        ),
    )


def _candidate(*, kind: str, text: str, hint: str = "", ts: float = 1234.5):
    candidate = MagicMock()
    candidate.text = text
    candidate.kind = kind
    candidate.action_hint = hint
    candidate.timestamp = ts
    return candidate


def _fake_brain():
    fake = MagicMock()
    fake.add_source_open_thread = AsyncMock(return_value="ot_new_1")
    fake.create_proactive_action = AsyncMock(return_value=42)
    return fake


@pytest.mark.asyncio
async def test_observation_candidate_ingested_as_active_open_thread() -> None:
    import core.settings_boot as settings_boot
    from core.turn import proactive

    candidate = _candidate(
        kind="observation",
        text="he said he'd hear back from the broker today — worth a gentle check-in",
    )
    fake_brain = _fake_brain()

    with patch.object(
        settings_boot, "settings_service", _settings_stub(),
    ), patch.object(
        proactive, "_load_unconsumed_candidate", AsyncMock(return_value=candidate),
    ), patch.object(
        proactive, "brain", fake_brain,
    ), patch.object(
        proactive, "consider_proactive_action", AsyncMock(),
    ) as action_path:
        fired = await proactive._consider_candidate_rail(owner_id=1)

    action_path.assert_not_called()
    # Ingestion does not "fire" the rail — the thread waits for the
    # arbiter's digest; nothing was sent.
    assert fired is False

    fake_brain.add_source_open_thread.assert_awaited_once()
    kwargs = fake_brain.add_source_open_thread.call_args.kwargs
    assert kwargs["topic_summary"] == candidate.text
    assert getattr(kwargs["source_kind"], "value", kwargs["source_kind"]) == "topic"
    assert getattr(kwargs["status"], "value", kwargs["status"]) == "active"
    assert kwargs["source_id"] == "monologue:1234.500000"

    fake_brain.create_proactive_action.assert_awaited_once()
    consumption = fake_brain.create_proactive_action.call_args.kwargs
    assert consumption["action_type"] == "candidate_ingested"
    trigger = json.loads(consumption["trigger_reason"])
    assert trigger["candidate_tick_timestamp"] == "1234.500000"
    assert trigger["routed"] == "open_thread"


@pytest.mark.asyncio
async def test_action_candidate_fires_action_path_and_wins_the_tick() -> None:
    import core.settings_boot as settings_boot
    from core.turn import proactive

    candidate = _candidate(kind="action", text="check git state", hint="git status")
    fake_brain = _fake_brain()

    with patch.object(
        settings_boot, "settings_service", _settings_stub(actions=True),
    ), patch.object(
        proactive, "_load_unconsumed_candidate", AsyncMock(return_value=candidate),
    ), patch.object(
        proactive, "_outreach_allowed", AsyncMock(return_value=True),
    ), patch.object(
        proactive, "brain", fake_brain,
    ), patch.object(
        proactive, "consider_proactive_action", AsyncMock(),
    ) as action_path:
        fired = await proactive._consider_candidate_rail(owner_id=1)

    action_path.assert_awaited_once()
    assert action_path.call_args.kwargs["candidate"] is candidate
    assert fired is True   # an action that ran "fired" the rail

    fake_brain.add_source_open_thread.assert_not_called()
    consumption = fake_brain.create_proactive_action.call_args.kwargs
    assert consumption["action_type"] == "proactive_message"
    trigger = json.loads(consumption["trigger_reason"])
    assert trigger["candidate_tick_timestamp"] == "1234.500000"
    assert trigger["routed"] == "action"


@pytest.mark.asyncio
async def test_action_candidate_disabled_falls_back_to_thread_ingestion() -> None:
    import core.settings_boot as settings_boot
    from core.turn import proactive

    candidate = _candidate(kind="action", text="check git state", hint="git status")
    fake_brain = _fake_brain()

    with patch.object(
        settings_boot, "settings_service", _settings_stub(actions=False),
    ), patch.object(
        proactive, "_load_unconsumed_candidate", AsyncMock(return_value=candidate),
    ), patch.object(
        proactive, "brain", fake_brain,
    ), patch.object(
        proactive, "consider_proactive_action", AsyncMock(),
    ) as action_path:
        fired = await proactive._consider_candidate_rail(owner_id=1)

    action_path.assert_not_called()
    assert fired is False
    fake_brain.add_source_open_thread.assert_awaited_once()
    consumption = fake_brain.create_proactive_action.call_args.kwargs
    assert consumption["action_type"] == "candidate_ingested"


@pytest.mark.asyncio
async def test_action_blocked_by_policy_leaves_candidate_unconsumed() -> None:
    import core.settings_boot as settings_boot
    from core.turn import proactive

    candidate = _candidate(kind="action", text="check git state", hint="git status")
    fake_brain = _fake_brain()

    with patch.object(
        settings_boot, "settings_service", _settings_stub(actions=True),
    ), patch.object(
        proactive, "_load_unconsumed_candidate", AsyncMock(return_value=candidate),
    ), patch.object(
        proactive, "_outreach_allowed", AsyncMock(return_value=False),
    ), patch.object(
        proactive, "brain", fake_brain,
    ), patch.object(
        proactive, "consider_proactive_action", AsyncMock(),
    ) as action_path:
        fired = await proactive._consider_candidate_rail(owner_id=1)

    # Cap blocked: nothing runs, nothing is consumed — retry on a later
    # pass while the candidate is still inside its staleness window.
    action_path.assert_not_called()
    assert fired is False
    fake_brain.add_source_open_thread.assert_not_called()
    fake_brain.create_proactive_action.assert_not_called()


@pytest.mark.asyncio
async def test_rail_failure_never_raises() -> None:
    import core.settings_boot as settings_boot
    from core.turn import proactive

    candidate = _candidate(kind="observation", text="a hunch")
    fake_brain = _fake_brain()
    fake_brain.add_source_open_thread = AsyncMock(side_effect=RuntimeError("db down"))

    with patch.object(
        settings_boot, "settings_service", _settings_stub(),
    ), patch.object(
        proactive, "_load_unconsumed_candidate", AsyncMock(return_value=candidate),
    ), patch.object(
        proactive, "brain", fake_brain,
    ):
        fired = await proactive._consider_candidate_rail(owner_id=1)

    # Swallowed and logged — the residual host's other jobs must not pay
    # for a broken rail.
    assert fired is False
