"""Stake ledger Phase 2 — the queue-side gates.

Pins the restraint budget (code-enforced, fail-closed) and the one-thread-
per-stake-lifetime idempotency. (The delivery-confirm bookkeeping half
retired with step 19: the synth-and-deliver lane is deleted; delivery is
the arbiter's, which marks threads surfaced on release.)
"""
from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

import core.turn.proactive as proactive
from ai.stake_ledger import Stake


def _stake(sid=1, kind="hope", horizon_passed_days=2.0):
    now = time.time()
    return Stake(
        id=sid,
        kind=kind,
        stake="I'm hoping the survey came back clean.",
        status="open",
        resolution="",
        horizon_at=now - horizon_passed_days * 86400,
        source_channel_id="777",
        last_surfaced_at=None,
        created_at=now - 5 * 86400,
        updated_at=now,
        resolved_at=None,
    )


class _FakeBrain:
    def __init__(self, existing_by_source=None):
        self.existing = existing_by_source or {}
        self.added = []
        self.surfaced_threads = []
        self.active_threads = []

    async def get_open_thread_by_source_id(self, *, owner_user_id, source_kind, source_id):
        return self.existing.get(source_id)

    async def add_source_open_thread(self, **kwargs):
        self.added.append(kwargs)
        return f"thread-{len(self.added)}"

    async def list_active_open_threads(self, *, owner_user_id):
        return self.active_threads

    async def mark_open_thread_surfaced(self, *, thread_id):
        self.surfaced_threads.append(thread_id)


def _wire_ledger(monkeypatch, *, enabled=True, budget_closed=False, due=None):
    import ai.stake_ledger as ledger

    monkeypatch.setattr(ledger, "ledger_enabled", lambda: enabled)

    async def _any_surfaced_within(seconds, **kw):
        return budget_closed

    async def _horizon_due(*, per_stake_cooldown_s, **kw):
        return list(due or [])

    monkeypatch.setattr(ledger, "any_surfaced_within", _any_surfaced_within)
    monkeypatch.setattr(ledger, "horizon_due_stakes", _horizon_due)


@pytest.mark.asyncio
async def test_due_stake_becomes_one_thread(monkeypatch):
    fake = _FakeBrain()
    monkeypatch.setattr(proactive, "brain", fake)
    _wire_ledger(monkeypatch, due=[_stake(1), _stake(2)])

    await proactive._ensure_stake_threads(owner_id=1)

    assert len(fake.added) == 1  # at most one per tick, even with two due
    added = fake.added[0]
    assert added["source_id"] == "stake:1"
    assert added["source_payload"]["origin"] == "stake_ledger"
    assert added["source_payload"]["stake_id"] == 1
    assert added["topic_summary"].startswith("I'm hoping")
    # Mechanical case-note register (Direction-C: no quotable first-person
    # lines stored in DATA — the voice happens live at synthesis).
    assert "Raise once, lightly" in added["why_it_matters"]
    assert "PaLLi's own held stakes" in added["why_it_matters"]


@pytest.mark.asyncio
async def test_budget_closed_queues_nothing(monkeypatch):
    fake = _FakeBrain()
    monkeypatch.setattr(proactive, "brain", fake)
    _wire_ledger(monkeypatch, budget_closed=True, due=[_stake(1)])

    await proactive._ensure_stake_threads(owner_id=1)
    assert fake.added == []


@pytest.mark.asyncio
async def test_flag_off_queues_nothing(monkeypatch):
    fake = _FakeBrain()
    monkeypatch.setattr(proactive, "brain", fake)
    _wire_ledger(monkeypatch, enabled=False, due=[_stake(1)])

    await proactive._ensure_stake_threads(owner_id=1)
    assert fake.added == []


@pytest.mark.asyncio
async def test_existing_thread_means_no_requeue_but_next_due_can_go(monkeypatch):
    """stake:1 already had its deterministic raise -> skipped forever by this
    path; the next due stake still gets its turn."""
    fake = _FakeBrain(existing_by_source={"stake:1": SimpleNamespace(id="old-thread")})
    monkeypatch.setattr(proactive, "brain", fake)
    _wire_ledger(monkeypatch, due=[_stake(1), _stake(2)])

    await proactive._ensure_stake_threads(owner_id=1)
    assert len(fake.added) == 1
    assert fake.added[0]["source_id"] == "stake:2"


@pytest.mark.asyncio
async def test_ledger_failure_never_raises(monkeypatch, caplog):
    fake = _FakeBrain()
    monkeypatch.setattr(proactive, "brain", fake)
    import ai.stake_ledger as ledger

    monkeypatch.setattr(ledger, "ledger_enabled", lambda: True)

    async def _boom(seconds, **kw):
        raise RuntimeError("ledger db gone")

    monkeypatch.setattr(ledger, "any_surfaced_within", _boom)
    with caplog.at_level("WARNING"):
        await proactive._ensure_stake_threads(owner_id=1)  # must not raise
    assert fake.added == []
