from __future__ import annotations

import asyncio
import sqlite3
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.home_presence import HomePresenceService, MachinePresenceSource


@pytest.fixture(autouse=True)
def _pin_arrival_thresholds(monkeypatch):
    """Pin derivation thresholds to the shipped defaults (600s locked /
    1200s idle). The owner's .env.local raises them (3600/5400) and
    core.config loads .env files at import, so without this pin the
    away durations used below (601s–700s) never cross the threshold
    and the derivation tests fail against the live environment.
    """
    from core.config import config

    monkeypatch.setattr(
        config, "AMBIENT_ARRIVAL_LOCKED_THRESHOLD_SECONDS", 600.0, raising=False,
    )
    monkeypatch.setattr(
        config, "AMBIENT_ARRIVAL_IDLE_THRESHOLD_SECONDS", 1200.0, raising=False,
    )


@pytest.mark.asyncio
async def test_reduce_context_tracks_arrival_and_machine_activity():
    service = HomePresenceService()
    policy = SimpleNamespace(quiet_hours_mode="off")
    previous = {
        "owner_user_id": 7,
        "owner_presence_state": "away",
        "machine_state": "unknown",
        "privacy_state": "unknown",
        "quiet_hours_active": False,
        "last_arrival_at": None,
        "last_departure_at": None,
        "last_machine_active_at": None,
        "last_event_at": None,
        "source_health": {},
        "metadata": {},
    }

    arrived = service._reduce_context(
        previous_state=previous,
        event={
            "owner_user_id": 7,
            "source": "home_assistant",
            "event_type": "owner_arrived",
            "occurred_at": 100.0,
            "payload": {},
        },
        policy=policy,
    )
    active = service._reduce_context(
        previous_state=arrived,
        event={
            "owner_user_id": 7,
            "source": "machine",
            "event_type": "machine_active",
            "occurred_at": 110.0,
            "payload": {},
        },
        policy=policy,
    )

    assert arrived["owner_presence_state"] == "home"
    assert arrived["last_arrival_at"] == 100.0
    assert active["machine_state"] == "active"
    assert active["last_machine_active_at"] == 110.0
    assert active["quiet_hours_active"] is False
    assert active["metadata"]["last_event_type"] == "machine_active"


@pytest.mark.asyncio
async def test_reduce_context_tracks_privacy_and_source_health():
    service = HomePresenceService()
    policy = SimpleNamespace(quiet_hours_mode="off")
    previous = {
        "owner_user_id": 7,
        "owner_presence_state": "home",
        "machine_state": "active",
        "privacy_state": "unknown",
        "quiet_hours_active": False,
        "last_arrival_at": 100.0,
        "last_departure_at": None,
        "last_machine_active_at": 105.0,
        "last_event_at": 105.0,
        "source_health": {},
        "metadata": {},
    }

    shared = service._reduce_context(
        previous_state=previous,
        event={
            "owner_user_id": 7,
            "source": "home_assistant",
            "event_type": "privacy_shared",
            "occurred_at": 120.0,
            "payload": {},
        },
        policy=policy,
    )
    degraded = service._reduce_context(
        previous_state=shared,
        event={
            "owner_user_id": 7,
            "source": "machine",
            "event_type": "machine_source_unavailable",
            "occurred_at": 130.0,
            "payload": {"message": "loginctl failed"},
        },
        policy=policy,
    )

    assert shared["privacy_state"] == "shared"
    assert degraded["source_health"]["machine"]["status"] == "degraded"
    assert degraded["source_health"]["machine"]["message"] == "loginctl failed"


@pytest.mark.asyncio
async def test_reduce_context_accepts_mapping_policy_for_quiet_hours():
    service = HomePresenceService()
    previous = {
        "owner_user_id": 7,
        "owner_presence_state": "home",
        "machine_state": "active",
        "privacy_state": "unknown",
        "quiet_hours_active": False,
        "last_arrival_at": 100.0,
        "last_departure_at": None,
        "last_machine_active_at": 105.0,
        "last_event_at": 105.0,
        "source_health": {},
        "metadata": {},
    }

    updated = service._reduce_context(
        previous_state=previous,
        event={
            "owner_user_id": 7,
            "source": "machine",
            "event_type": "machine_active",
            "occurred_at": 120.0,
            "payload": {},
        },
        policy={"quiet_hours_mode": "off"},
    )

    assert updated["quiet_hours_active"] is False


@pytest.mark.asyncio
async def test_machine_source_tracks_away_clock_on_active_transitions():
    # The away clock (`_away_started_at`) is a single monotonic bookmark:
    # it starts when the machine leaves `active`, is preserved across any
    # number of hops between non-active states, and clears when the machine
    # returns to `active`. This test locks that contract in place.
    service = MagicMock()
    service.ingest_event = AsyncMock(return_value=None)
    service.list_recent_events = AsyncMock(return_value=[])
    source = MachinePresenceSource(service, owner_user_id=1)

    # Initial: no away clock running
    assert source._away_started_at is None

    # Bootstrap to active: still None (we ARE active, not away)
    await source._emit_state_changes("active")
    assert source._away_started_at is None

    # Same-state poll: no-op, early-returns, still None
    await source._emit_state_changes("active")
    assert source._away_started_at is None

    # Leave active state: the clock starts
    await source._emit_state_changes("idle")
    first_away_start = source._away_started_at
    assert first_away_start is not None

    # Hop between non-active states: clock preserved (NOT reset)
    await source._emit_state_changes("locked")
    assert source._away_started_at == first_away_start

    # Return to active: clock clears
    await source._emit_state_changes("active")
    assert source._away_started_at is None


def _make_machine_source():
    service = MagicMock()
    service.ingest_event = AsyncMock(return_value=None)
    service.list_recent_events = AsyncMock(return_value=[])
    source = MachinePresenceSource(service, owner_user_id=1)
    return service, source


def _derived_calls(service):
    return [
        call for call in service.ingest_event.await_args_list
        if call.kwargs.get("event_type") == "owner_arrived"
    ]


@pytest.mark.asyncio
async def test_machine_unlock_after_long_lock_derives_owner_arrived():
    service, source = _make_machine_source()
    source._last_state = "locked"
    source._away_started_at = time.time() - 601.0

    await source._emit_state_changes("active")

    derived = _derived_calls(service)
    assert len(derived) == 1
    kwargs = derived[0].kwargs
    assert kwargs["source"] == "machine_derived"
    assert kwargs["payload"]["derived_from"] == "locked"
    assert kwargs["payload"]["away_duration_seconds"] >= 601.0
    assert kwargs["metadata"]["derived"] is True
    assert kwargs["metadata"]["bootstrap"] is False
    assert kwargs["dedupe_key"] == "machine_derived:owner_arrived"


@pytest.mark.asyncio
async def test_machine_unlock_before_threshold_does_not_derive_arrival():
    service, source = _make_machine_source()
    source._last_state = "locked"
    source._away_started_at = time.time() - 300.0

    await source._emit_state_changes("active")

    assert _derived_calls(service) == []
    emitted_types = {
        call.kwargs.get("event_type")
        for call in service.ingest_event.await_args_list
    }
    assert "machine_unlocked" in emitted_types
    assert "machine_active" in emitted_types


@pytest.mark.asyncio
async def test_machine_active_after_long_idle_derives_owner_arrived():
    service, source = _make_machine_source()
    source._last_state = "idle"
    source._away_started_at = time.time() - 1201.0

    await source._emit_state_changes("active")

    derived = _derived_calls(service)
    assert len(derived) == 1
    assert derived[0].kwargs["payload"]["derived_from"] == "idle"
    assert derived[0].kwargs["source"] == "machine_derived"


@pytest.mark.asyncio
async def test_machine_active_after_idle_below_idle_threshold_does_not_derive_arrival():
    # Duration (900s) exceeds locked threshold (600s) but NOT idle threshold (1200s).
    # This test specifically guards against regression into a single-threshold implementation.
    service, source = _make_machine_source()
    source._last_state = "idle"
    source._away_started_at = time.time() - 900.0

    await source._emit_state_changes("active")

    assert _derived_calls(service) == []


@pytest.mark.asyncio
async def test_bootstrap_transition_does_not_derive_arrival():
    service, source = _make_machine_source()
    assert source._last_state is None
    assert source._away_started_at is None

    await source._emit_state_changes("active")

    assert _derived_calls(service) == []


@pytest.mark.asyncio
async def test_rapid_lock_unlock_cycle_does_not_derive_arrival():
    # Rapid sequences where the machine briefly enters and leaves `active`
    # should never accumulate enough away time to derive an arrival, because
    # each return to `active` clears the away clock. This is the correct
    # behavior even though it means a "mouse bump wakes the screen for 5 seconds"
    # during a real away period would reset the clock (acceptable trade-off).
    service, source = _make_machine_source()
    source._last_state = "active"
    source._away_started_at = None

    # First lock
    await source._emit_state_changes("locked")
    # Rewind to simulate 10s elapsed in the locked state
    source._away_started_at = time.time() - 10.0

    # First unlock — 10s < 600s threshold, no derivation, clock clears
    await source._emit_state_changes("active")
    assert source._away_started_at is None

    # Second lock
    await source._emit_state_changes("locked")
    source._away_started_at = time.time() - 8.0

    # Second unlock — 8s < 600s threshold
    await source._emit_state_changes("active")

    assert _derived_calls(service) == []


@pytest.mark.asyncio
async def test_derived_arrival_carries_machine_derived_source():
    service, source = _make_machine_source()
    source._last_state = "locked"
    source._away_started_at = time.time() - 700.0

    await source._emit_state_changes("active")

    derived = _derived_calls(service)
    assert len(derived) == 1
    kwargs = derived[0].kwargs
    assert kwargs["source"] == "machine_derived"
    assert kwargs["metadata"]["derived"] is True


@pytest.mark.asyncio
async def test_home_assistant_arrival_and_derived_arrival_dedup():
    # Simulate an HA-sourced owner_arrived event that happened 5 seconds ago.
    recent_ha_arrival = {
        "event_id": "ctx_ha_recent",
        "source": "home_assistant",
        "event_type": "owner_arrived",
        "occurred_at": time.time() - 5.0,
    }
    service = MagicMock()
    service.ingest_event = AsyncMock(return_value=None)
    service.list_recent_events = AsyncMock(return_value=[recent_ha_arrival])
    source = MachinePresenceSource(service, owner_user_id=1)
    source._last_state = "locked"
    source._away_started_at = time.time() - 700.0

    await source._emit_state_changes("active")

    # The machine_* events should still fire, but no derived owner_arrived.
    assert _derived_calls(service) == []
    service.list_recent_events.assert_awaited()


@pytest.mark.asyncio
async def test_multihop_active_to_idle_to_locked_to_active_derives_arrival():
    # THE REAL-WORLD SCENARIO: user walks away without explicitly pressing
    # Win+L. The machine drifts to `idle` first, Windows' auto-lock eventually
    # kicks in and transitions to `locked`, then the user returns and unlocks.
    # The total away time spans the idle + locked hops, so the bridge must
    # compute duration from the original active-exit timestamp (the moment we
    # left `active`), not just the brief final locked period.
    #
    # This test is the post-mortem of tonight's physical integration validation:
    # the original Phase B used `_last_state_changed_at` which reset on every
    # hop, so the `idle → locked` transition (10s before unlock) wiped the
    # actual away-exit timestamp and the derivation silently skipped.
    service, source = _make_machine_source()
    source._last_state = "active"
    source._away_started_at = None

    # Leave active state (machine drifts to idle)
    await source._emit_state_changes("idle")
    # Rewind the clock to simulate 700 seconds elapsed since active-exit
    simulated_start = time.time() - 700.0
    source._away_started_at = simulated_start

    # Windows auto-lock kicks in after a long idle period: idle → locked.
    # The away clock must NOT reset on this non-active-to-non-active hop.
    await source._emit_state_changes("locked")
    assert source._away_started_at == simulated_start

    # User returns and unlocks
    await source._emit_state_changes("active")

    # Derivation must fire: total away time (~700s) exceeds the locked
    # threshold (600s) that applies because the final prior state was locked.
    derived = _derived_calls(service)
    assert len(derived) == 1
    kwargs = derived[0].kwargs
    assert kwargs["source"] == "machine_derived"
    assert kwargs["payload"]["derived_from"] == "locked"
    assert kwargs["payload"]["away_duration_seconds"] >= 700.0
    # Clock clears after successful derivation
    assert source._away_started_at is None


@pytest.mark.asyncio
async def test_brief_active_flap_between_away_periods_does_not_accumulate():
    # Two sub-threshold away periods separated by a brief return to `active`
    # should NOT accumulate into a single fire. Each return to active clears
    # the away clock, so the second away period starts from scratch.
    #
    # This documents the intentional trade-off of using a single bookmark
    # (`_away_started_at`) rather than cumulative tracking: a mouse bump that
    # briefly wakes the screen mid-away will reset the clock. The alternative
    # design (cumulative-time across multiple exits) would be more forgiving
    # but harder to reason about and more complex to implement. The trade-off
    # was accepted because users who actually walked away would have been
    # gone far past any individual threshold, and users who bumped the mouse
    # are still right there and don't need a greeting.
    service, source = _make_machine_source()
    source._last_state = "active"
    source._away_started_at = None

    # First away period: 500s locked — under the 600s locked threshold
    await source._emit_state_changes("locked")
    source._away_started_at = time.time() - 500.0

    # Brief return to active — no derivation (500 < 600)
    await source._emit_state_changes("active")
    assert _derived_calls(service) == []
    assert source._away_started_at is None  # clock cleared

    # Second away period: 400s locked — also under threshold
    await source._emit_state_changes("locked")
    source._away_started_at = time.time() - 400.0

    # Return to active
    await source._emit_state_changes("active")

    # Cumulative would be 900s > 600s, but each period is evaluated
    # independently because the middle `active` reset the clock. No fires.
    assert _derived_calls(service) == []


# ── 2026-08-09: the health write must never kill the health loop ────────────
# Observed live: "database is locked" raised inside the except handler's
# degraded-status write, unwound _run, and the presence task died silently
# until the next restart. The loop now records health best-effort.


@pytest.mark.asyncio
async def test_record_health_best_effort_swallows_store_failures():
    from core.home_presence import _record_health_best_effort

    service = MagicMock()
    service.record_source_health = AsyncMock(
        side_effect=sqlite3.OperationalError("database is locked")
    )
    # Must not raise.
    await _record_health_best_effort(
        service,
        owner_user_id=1,
        source_name="machine",
        status="degraded",
        message="x",
        emit_event=True,
    )
    service.record_source_health.assert_awaited_once()


@pytest.mark.asyncio
async def test_machine_loop_survives_health_write_failure(monkeypatch):
    """Worst case both ways: state detection raises AND the degraded health
    write raises. The loop must come around for another iteration instead of
    dying — proven by a second record_source_health attempt."""
    from core import home_presence

    backend = MagicMock()
    backend.supports_machine_presence.return_value = True
    monkeypatch.setattr(
        home_presence.host_platform_service, "backend", lambda: backend
    )
    monkeypatch.setattr(
        home_presence.config, "LOCAL_MACHINE_PRESENCE_POLL_SECONDS", 0.01, raising=False
    )

    service = MagicMock()
    service.record_source_health = AsyncMock(
        side_effect=sqlite3.OperationalError("database is locked")
    )
    source = MachinePresenceSource(service, owner_user_id=1)
    monkeypatch.setattr(
        source, "_detect_state", AsyncMock(side_effect=RuntimeError("probe failed"))
    )

    source._running = True
    task = asyncio.get_running_loop().create_task(source._run())
    try:
        for _ in range(200):
            if service.record_source_health.await_count >= 2:
                break
            await asyncio.sleep(0.01)
        assert service.record_source_health.await_count >= 2, (
            "loop died on the first health-write failure instead of surviving it"
        )
        assert not task.done(), "presence loop exited instead of continuing"
    finally:
        source._running = False
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
