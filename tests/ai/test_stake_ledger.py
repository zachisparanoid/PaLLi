"""Stake ledger store tests — schema, lifecycle ops, cap, restraint budget.

House rules honored here:
- DBs live under pytest tmp_path (never the repo data/ dir — the 1.5GB
  phase7d litter scar), with a stays-out-of-data/ regression guard.
- Strict pytest-asyncio mode: explicit @pytest.mark.asyncio everywhere,
  async fixtures via @pytest_asyncio.fixture.
"""
from __future__ import annotations

import shutil
import time
import uuid
from pathlib import Path

import pytest
import pytest_asyncio

from ai import stake_ledger
from core.database import PalliBrain


@pytest.fixture
def local_tmp_dir(tmp_path: Path):
    directory = tmp_path / f"stakes_test_{uuid.uuid4().hex}"
    directory.mkdir(parents=True)
    try:
        yield directory
    finally:
        shutil.rmtree(directory, ignore_errors=True)


@pytest_asyncio.fixture
async def brain(local_tmp_dir: Path):
    db = PalliBrain(str(local_tmp_dir / "stakes.sqlite3"))
    await db.initialize()
    try:
        yield db
    finally:
        await db.close()


def test_db_path_stays_out_of_repo_data_dir(local_tmp_dir: Path):
    """Hygiene regression guard (the phase7d litter scar)."""
    repo_data = Path(__file__).resolve().parents[2] / "data"
    assert repo_data not in (local_tmp_dir / "stakes.sqlite3").resolve().parents


@pytest.mark.asyncio
async def test_ensure_schema_idempotent(brain: PalliBrain):
    await stake_ledger.ensure_schema(brain_client=brain)
    await stake_ledger.ensure_schema(brain_client=brain)
    conn = await brain.get_connection()
    try:
        table = await conn.fetchval(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='stakes'"
        )
    finally:
        await conn.close()
    assert table == "stakes"


@pytest.mark.asyncio
async def test_open_update_resolve_lifecycle(brain: PalliBrain):
    summary = await stake_ledger.apply_ops(
        [
            {
                "op": "open",
                "kind": "hope",
                "stake": "I'm hoping the inspection comes back clean — he wants this one bad.",
                "horizon_at": time.time() + 3 * 86400,
            }
        ],
        source_channel_id="chan-1",
        brain_client=brain,
    )
    assert summary["applied"] == 1 and len(summary["opened"]) == 1
    sid = summary["opened"][0]

    stakes = await stake_ledger.open_stakes(brain_client=brain)
    assert len(stakes) == 1
    assert stakes[0].kind == "hope"
    assert stakes[0].stake.startswith("I'm hoping")
    assert stakes[0].source_channel_id == "chan-1"
    assert stakes[0].horizon_at is not None

    # Update rewrites the prose in place.
    await stake_ledger.apply_ops(
        [{"op": "update", "id": sid, "stake": "Survey got moved — still hoping, now for Friday."}],
        brain_client=brain,
    )
    stakes = await stake_ledger.open_stakes(brain_client=brain)
    assert "Friday" in stakes[0].stake

    # Resolve closes it with felt prose; it leaves the open set.
    await stake_ledger.apply_ops(
        [{"op": "resolve", "id": sid, "resolution": "Clean! The relief is real."}],
        brain_client=brain,
    )
    assert await stake_ledger.open_stakes(brain_client=brain) == []
    assert (await stake_ledger.counts(brain_client=brain)).get("resolved") == 1


@pytest.mark.asyncio
async def test_fade_keeps_a_note_never_deletes(brain: PalliBrain):
    summary = await stake_ledger.apply_ops(
        [{"op": "open", "kind": "wonder", "stake": "I wonder where the van project went."}],
        brain_client=brain,
    )
    sid = summary["opened"][0]
    await stake_ledger.apply_ops(
        [{"op": "fade", "id": sid, "resolution": "It just stopped coming up. Letting it rest."}],
        brain_client=brain,
    )
    c = await stake_ledger.counts(brain_client=brain)
    assert c.get("faded") == 1 and "open" not in c


@pytest.mark.asyncio
async def test_open_cap_rejected_with_warning(brain: PalliBrain, caplog):
    ops = [
        {"op": "open", "kind": "worry", "stake": f"worry number {i} about something real"}
        for i in range(15)
    ]
    with caplog.at_level("WARNING"):
        summary = await stake_ledger.apply_ops(ops, brain_client=brain)
    assert summary["applied"] == 12  # default cap
    assert summary["rejected"] == 3
    assert any("cap" in r.message for r in caplog.records)
    # Cap applies to OPEN stakes only — resolving frees a slot.
    stakes = await stake_ledger.open_stakes(brain_client=brain, limit=20)
    assert len(stakes) == 12


@pytest.mark.asyncio
async def test_invalid_ops_rejected_individually(brain: PalliBrain, caplog):
    ops = [
        {"op": "open", "kind": "hope", "stake": "a real hope about his week"},
        {"op": "open", "kind": "dread", "stake": "invalid kind"},  # rejected
        {"op": "resolve", "id": None, "resolution": "x"},  # rejected
        {"op": "resolve", "id": 999999},  # rejected: no resolution text
        "not even a dict",  # rejected
    ]
    with caplog.at_level("WARNING"):
        summary = await stake_ledger.apply_ops(ops, brain_client=brain)
    assert summary["applied"] == 1
    assert summary["rejected"] == 4


@pytest.mark.asyncio
async def test_open_stakes_orders_nearest_horizon_first(brain: PalliBrain):
    now = time.time()
    await stake_ledger.apply_ops(
        [
            {"op": "open", "kind": "wonder", "stake": "no horizon on this one"},
            {"op": "open", "kind": "hope", "stake": "far horizon", "horizon_at": now + 10 * 86400},
            {"op": "open", "kind": "worry", "stake": "near horizon", "horizon_at": now + 86400},
        ],
        brain_client=brain,
    )
    stakes = await stake_ledger.open_stakes(brain_client=brain)
    assert [s.stake for s in stakes[:2]] == ["near horizon", "far horizon"]
    assert stakes[2].stake == "no horizon on this one"


@pytest.mark.asyncio
async def test_restraint_budget_bookkeeping(brain: PalliBrain):
    summary = await stake_ledger.apply_ops(
        [
            {
                "op": "open",
                "kind": "hope",
                "stake": "hoping the survey lands clean",
                "horizon_at": time.time() - 3600,  # horizon already passed
            }
        ],
        brain_client=brain,
    )
    sid = summary["opened"][0]

    # Fresh ledger: nothing surfaced, horizon-due stake is eligible.
    assert not await stake_ledger.any_surfaced_within(86400, brain_client=brain)
    due = await stake_ledger.horizon_due_stakes(per_stake_cooldown_s=72 * 3600, brain_client=brain)
    assert [s.id for s in due] == [sid]

    # After surfacing: global 24h gate closes AND the per-stake cooldown holds.
    await stake_ledger.record_surfaced(sid, brain_client=brain)
    assert await stake_ledger.any_surfaced_within(86400, brain_client=brain)
    due = await stake_ledger.horizon_due_stakes(per_stake_cooldown_s=72 * 3600, brain_client=brain)
    assert due == []


@pytest.mark.asyncio
async def test_read_helpers_fail_soft(local_tmp_dir: Path, caplog):
    """A broken DB must never break the turn: [] / {} / fail-closed budget."""

    class _BrokenBrain:
        db_path = str(local_tmp_dir / "nonexistent" / "broken.sqlite3")

        async def get_connection(self):
            raise RuntimeError("db unavailable")

    broken = _BrokenBrain()
    with caplog.at_level("WARNING"):
        assert await stake_ledger.open_stakes(brain_client=broken) == []
        assert await stake_ledger.counts(brain_client=broken) == {}
        assert (
            await stake_ledger.horizon_due_stakes(
                per_stake_cooldown_s=1.0, brain_client=broken
            )
            == []
        )
        # Restraint gate fails CLOSED — when in doubt, stay quiet.
        assert await stake_ledger.any_surfaced_within(60, brain_client=broken) is True
    assert any("stake_ledger" in r.message for r in caplog.records)


def test_render_stakes_block_first_person_and_horizons():
    now = time.time()
    stakes = [
        stake_ledger.Stake(
            id=1,
            kind="hope",
            stake="I'm hoping the survey comes back clean.",
            status="open",
            resolution="",
            horizon_at=now - 2 * 86400,
            source_channel_id="",
            last_surfaced_at=None,
            created_at=now - 4 * 86400,
            updated_at=now,
            resolved_at=None,
        ),
        stake_ledger.Stake(
            id=2,
            kind="taste",
            stake="I genuinely like the timber-frame idea — slow builds suit him.",
            status="open",
            resolution="",
            horizon_at=None,
            source_channel_id="",
            last_surfaced_at=None,
            created_at=now - 3600,
            updated_at=now,
            resolved_at=None,
        ),
    ]
    block = stake_ledger.render_stakes_block(stakes, now=now)
    assert "I'm hoping the survey" in block
    assert "horizon passed 2d ago" in block
    assert "(hope, open 4d" in block
    assert "(taste, open 1h)" in block
    assert stake_ledger.render_stakes_block([], now=now) == ""


@pytest.mark.asyncio
async def test_update_horizon_set_clear_and_untouched(brain: PalliBrain):
    """Review finding: an update's horizon handling must be explicit —
    present numeric sets, present None clears, ABSENT key leaves unchanged."""
    now = time.time()
    summary = await stake_ledger.apply_ops(
        [{"op": "open", "kind": "hope", "stake": "hoping!", "horizon_at": now + 86400}],
        brain_client=brain,
    )
    sid = summary["opened"][0]

    # Absent key -> unchanged.
    await stake_ledger.apply_ops(
        [{"op": "update", "id": sid, "stake": "hoping, updated!"}], brain_client=brain
    )
    s = (await stake_ledger.open_stakes(brain_client=brain))[0]
    assert s.horizon_at is not None and s.stake == "hoping, updated!"

    # Present numeric -> set to the new value.
    await stake_ledger.apply_ops(
        [{"op": "update", "id": sid, "horizon_at": now + 5 * 86400}], brain_client=brain
    )
    s = (await stake_ledger.open_stakes(brain_client=brain))[0]
    assert abs(s.horizon_at - (now + 5 * 86400)) < 2
    assert s.stake == "hoping, updated!"  # horizon-only update left prose alone

    # Present None -> cleared.
    await stake_ledger.apply_ops(
        [{"op": "update", "id": sid, "horizon_at": None}], brain_client=brain
    )
    s = (await stake_ledger.open_stakes(brain_client=brain))[0]
    assert s.horizon_at is None


@pytest.mark.asyncio
async def test_non_numeric_horizon_at_rejected(brain: PalliBrain, caplog):
    """Review finding: a stray string in horizon_at (REAL column) used to
    poison every subsequent read's float() conversion. Must be rejected."""
    with caplog.at_level("WARNING"):
        summary = await stake_ledger.apply_ops(
            [
                {"op": "open", "kind": "hope", "stake": "good one"},
                {"op": "open", "kind": "worry", "stake": "bad one", "horizon_at": "2026-07-10"},
            ],
            brain_client=brain,
        )
    assert summary["applied"] == 1 and summary["rejected"] == 1
    # Reads still work — the ledger was not poisoned.
    stakes = await stake_ledger.open_stakes(brain_client=brain)
    assert [s.stake for s in stakes] == ["good one"]


@pytest.mark.asyncio
async def test_phantom_ids_counted_rejected_not_applied(brain: PalliBrain, caplog):
    """Review finding (verified repro): zero-row update/resolve/fade used to
    report applied + phantom closed ids."""
    with caplog.at_level("WARNING"):
        summary = await stake_ledger.apply_ops(
            [
                {"op": "resolve", "id": 999999, "resolution": "phantom"},
                {"op": "update", "id": 888888, "stake": "phantom"},
                {"op": "fade", "id": 777777},
            ],
            brain_client=brain,
        )
    assert summary["applied"] == 0
    assert summary["rejected"] == 3
    assert summary["closed"] == []
    assert sum("missing/non-open" in r.message for r in caplog.records) == 3


@pytest.mark.asyncio
async def test_multiline_prose_flattened_on_write(brain: PalliBrain):
    """Review finding: embedded newlines misaligned the appraiser's id-labeled
    render. Prose is whitespace-normalized at write time."""
    summary = await stake_ledger.apply_ops(
        [{"op": "open", "kind": "hope", "stake": "line one.\nline two.\n\tline three."}],
        brain_client=brain,
    )
    s = (await stake_ledger.open_stakes(brain_client=brain))[0]
    assert "\n" not in s.stake and "\t" not in s.stake
    assert s.stake == "line one. line two. line three."
    await stake_ledger.apply_ops(
        [{"op": "resolve", "id": summary["opened"][0], "resolution": "felt\nit\nland"}],
        brain_client=brain,
    )
    c = await stake_ledger.counts(brain_client=brain)
    assert c.get("resolved") == 1


@pytest.mark.asyncio
async def test_open_cap_race_two_concurrent_batches(brain: PalliBrain):
    """Review finding: the cap's race-safety was a docstring claim with no
    test. Two concurrent apply_ops batches must never overshoot 12 open."""
    import asyncio

    seed = [
        {"op": "open", "kind": "worry", "stake": f"seed worry {i} about real things"}
        for i in range(11)
    ]
    await stake_ledger.apply_ops(seed, brain_client=brain)

    racer_a = stake_ledger.apply_ops(
        [{"op": "open", "kind": "hope", "stake": "racer A's hope"}], brain_client=brain
    )
    racer_b = stake_ledger.apply_ops(
        [{"op": "open", "kind": "hope", "stake": "racer B's hope"}], brain_client=brain
    )
    a, b = await asyncio.gather(racer_a, racer_b)

    stakes = await stake_ledger.open_stakes(brain_client=brain, limit=30)
    assert len(stakes) == 12  # never 13
    assert a["applied"] + b["applied"] == 1
    assert a["rejected"] + b["rejected"] == 1


# ── recent_stakes (introspect tool read path, 2026-07-13) ─────────────────


@pytest.mark.asyncio
async def test_recent_stakes_returns_closed_with_resolutions(brain: PalliBrain):
    await stake_ledger.apply_ops(
        [
            {"op": "open", "kind": "worry", "stake": "I'm watching the storm track toward the cellar."},
            {"op": "open", "kind": "wonder", "stake": "I'm curious how the survey will land."},
        ],
        brain_client=brain,
    )
    await stake_ledger.apply_ops(
        [{"op": "resolve", "id": 2, "resolution": "It landed clean; the relief was real."}],
        brain_client=brain,
    )
    got = await stake_ledger.recent_stakes(brain_client=brain)
    assert {s.status for s in got} == {"open", "resolved"}
    resolved = next(s for s in got if s.status == "resolved")
    assert "relief" in resolved.resolution


@pytest.mark.asyncio
async def test_recent_stakes_include_closed_false_is_open_only(brain: PalliBrain):
    await stake_ledger.apply_ops(
        [{"op": "open", "kind": "hope", "stake": "open one"}], brain_client=brain
    )
    await stake_ledger.apply_ops(
        [{"op": "open", "kind": "worry", "stake": "will fade"}], brain_client=brain
    )
    await stake_ledger.apply_ops(
        [{"op": "fade", "id": 2, "resolution": "it drifted"}], brain_client=brain
    )
    got = await stake_ledger.recent_stakes(include_closed=False, brain_client=brain)
    assert [s.status for s in got] == ["open"]


@pytest.mark.asyncio
async def test_recent_stakes_days_back_window(brain: PalliBrain):
    await stake_ledger.apply_ops(
        [
            {"op": "open", "kind": "hope", "stake": "fresh stake"},
            {"op": "open", "kind": "taste", "stake": "stale stake"},
        ],
        brain_client=brain,
    )
    conn = await brain.get_connection()
    try:
        await conn.execute(
            "UPDATE stakes SET updated_at = $1 WHERE stake = 'stale stake'",
            time.time() - 90 * 86400.0,
        )
    finally:
        await conn.close()
    got = await stake_ledger.recent_stakes(days_back=30, brain_client=brain)
    assert [s.stake for s in got] == ["fresh stake"]


@pytest.mark.asyncio
async def test_recent_stakes_newest_activity_first_with_limit(brain: PalliBrain):
    for i in range(4):
        await stake_ledger.apply_ops(
            [{"op": "open", "kind": "wonder", "stake": f"stake {i}"}], brain_client=brain
        )
    conn = await brain.get_connection()
    try:
        for i in range(4):
            await conn.execute(
                "UPDATE stakes SET updated_at = $1 WHERE stake = $2",
                time.time() - i * 3600.0,
                f"stake {i}",
            )
    finally:
        await conn.close()
    got = await stake_ledger.recent_stakes(limit=2, brain_client=brain)
    assert [s.stake for s in got] == ["stake 0", "stake 1"]


@pytest.mark.asyncio
async def test_recent_stakes_best_effort_on_broken_brain():
    class _BrokenBrain:
        db_path = "broken"

        async def get_connection(self):
            raise RuntimeError("db down")

    assert await stake_ledger.recent_stakes(brain_client=_BrokenBrain()) == []
