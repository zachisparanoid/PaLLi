"""Step 25 · Regret — the ledger accepts the kind; the cap is code (spec §0 call 3, §2).

A regret is a fifth stake kind whose arrow points backward — at PaLLi's own
move. The per-kind cap (companion.regret_max_open) is enforced in
``apply_ops`` as a code rail: an ``open`` beyond it is DECLINED with a
reason, never queued (the fears store's declining cap, same shape). The
shared cap (stake_ledger_max_open) still applies on top.

DBs live under tmp_path; every test pins its own knobs (3352e3d)."""
from __future__ import annotations

import shutil
import uuid
from pathlib import Path

import pytest
import pytest_asyncio

from ai import stake_ledger
from core.database import PalliBrain


@pytest.fixture
def local_tmp_dir(tmp_path: Path):
    d = tmp_path / f"regret_ledger_{uuid.uuid4().hex}"
    d.mkdir(parents=True)
    try:
        yield d
    finally:
        shutil.rmtree(d, ignore_errors=True)


@pytest_asyncio.fixture
async def brain(local_tmp_dir: Path):
    db = PalliBrain(str(local_tmp_dir / "stakes.sqlite3"))
    await db.initialize()
    try:
        yield db
    finally:
        await db.close()


def test_regret_is_a_valid_kind():
    assert "regret" in stake_ledger.VALID_KINDS
    # Organ 4.13 · Opinions (2026-08-31) appended the sixth kind, "view"
    # (spec §1). Regret's own position and semantics are unchanged.
    assert stake_ledger.VALID_KINDS == ("hope", "worry", "taste", "wonder", "regret", "view")


@pytest.mark.asyncio
async def test_regret_opens_like_any_stake_until_its_own_cap(brain, monkeypatch):
    monkeypatch.setattr(stake_ledger, "_regret_max_open", lambda: 2)
    ops = [
        {"op": "open", "kind": "regret", "stake": f"I buried him in settings ({i})", "horizon": None}
        for i in range(3)
    ]
    summary = await stake_ledger.apply_ops(ops, source_channel_id="t", brain_client=brain)
    assert summary["applied"] == 2 and summary["rejected"] == 1
    open_now = await stake_ledger.open_stakes(limit=20, brain_client=brain)
    assert sum(1 for s in open_now if s.kind == "regret") == 2


@pytest.mark.asyncio
async def test_other_kinds_ignore_the_regret_cap(brain, monkeypatch):
    monkeypatch.setattr(stake_ledger, "_regret_max_open", lambda: 1)
    ops = [
        {"op": "open", "kind": "hope", "stake": "the survey lands", "horizon": None},
        {"op": "open", "kind": "worry", "stake": "the taper wobbles", "horizon": None},
    ]
    summary = await stake_ledger.apply_ops(ops, source_channel_id="t", brain_client=brain)
    assert summary["applied"] == 2 and summary["rejected"] == 0


@pytest.mark.asyncio
async def test_regret_cap_is_declined_never_queued_and_reason_is_logged(brain, monkeypatch, caplog):
    """A declined regret leaves no trace in the ledger — nothing waits for a slot."""
    monkeypatch.setattr(stake_ledger, "_regret_max_open", lambda: 1)
    caplog.set_level("WARNING", logger="ai.stake_ledger")
    await stake_ledger.apply_ops(
        [{"op": "open", "kind": "regret", "stake": "I called it finished when it wasn't", "horizon": None}],
        source_channel_id="t", brain_client=brain,
    )
    summary = await stake_ledger.apply_ops(
        [{"op": "open", "kind": "regret", "stake": "I didn't ask the question", "horizon": None}],
        source_channel_id="t", brain_client=brain,
    )
    assert summary == {"applied": 0, "rejected": 1, "opened": [], "closed": [], "dormant": []}
    assert any("regret cap" in r.getMessage() for r in caplog.records)
    counts = await stake_ledger.counts(brain_client=brain)
    assert counts == {"open": 1}


def test_regret_max_open_reads_the_knob_and_floors_at_one(monkeypatch):
    from types import SimpleNamespace
    from core.turn import companion_flags

    monkeypatch.setattr(companion_flags, "companion_settings", lambda: SimpleNamespace(regret_max_open=3))
    assert stake_ledger._regret_max_open() == 3
    monkeypatch.setattr(companion_flags, "companion_settings", lambda: SimpleNamespace(regret_max_open=0))
    assert stake_ledger._regret_max_open() == 1
    monkeypatch.setattr(companion_flags, "companion_settings", lambda: None)
    assert stake_ledger._regret_max_open() == 2
