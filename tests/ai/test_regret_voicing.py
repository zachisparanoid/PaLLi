"""Step 25 · Regret — the one voicing (spec §4).

A regret may be said to him ONCE, ever. The truth of that lives in one
column: ``stakes.voiced_at`` — stamped by the arbiter ON SEND (record on
send, never on intent — the custody law), read by every render as the
voicing marker. Additive migration: the CREATE carries the column for new
ledgers; the catalog's ALTER, on the database's one version-0 boot, carries
it to a ledger born before step 25 (Batch 8, task 4: the catalog owns the
table, and ensure_schema keeps only its CREATE).

DBs under tmp_path; flags pinned in-body (3352e3d)."""
from __future__ import annotations

import shutil
import sqlite3
import uuid
from pathlib import Path

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio

import ai.arbiter as arb
import ai.present_moment as pm
import ai.present_moment_gather as pmg
import ai.quest_store as qs
from ai import stake_ledger
from ai.stake_ledger import Stake
from core.database import PalliBrain

# Captured at import — the root conftest pins the module attribute to
# lambda: False for every test (the live yaml ships regret: true); the
# gate test below exercises the REAL function through that pin.
_REAL_REGRET_ENABLED = stake_ledger.regret_enabled


@pytest.fixture
def local_tmp_dir(tmp_path: Path):
    d = tmp_path / f"regret_voicing_{uuid.uuid4().hex}"
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


async def _open(brain, kind, text):
    await stake_ledger.apply_ops(
        [{"op": "open", "kind": kind, "stake": text, "horizon": None}],
        source_channel_id="t", brain_client=brain,
    )
    return next(s for s in await stake_ledger.open_stakes(limit=20, brain_client=brain)
                if s.stake == text)


# ── the column and the stamp ────────────────────────────────────────────

def test_stake_carries_voiced_at_defaulting_to_none():
    s = stake_ledger.Stake(id=1, kind="regret", stake="x", status="open", resolution="",
                           horizon_at=None, source_channel_id="", last_surfaced_at=None,
                           created_at=0.0, updated_at=0.0, resolved_at=None)
    assert s.voiced_at is None


@pytest.mark.asyncio
async def test_mark_voiced_stamps_only_regrets(brain):
    await stake_ledger.apply_ops(
        [{"op": "open", "kind": "regret", "stake": "I buried him in settings", "horizon": None},
         {"op": "open", "kind": "hope", "stake": "the survey lands", "horizon": None}],
        source_channel_id="t", brain_client=brain)
    stakes = await stake_ledger.open_stakes(limit=10, brain_client=brain)
    regret = next(s for s in stakes if s.kind == "regret")
    hope = next(s for s in stakes if s.kind == "hope")
    assert regret.voiced_at is None
    assert await stake_ledger.mark_voiced(regret.id, brain_client=brain, now=123.0) is True
    assert await stake_ledger.mark_voiced(hope.id, brain_client=brain, now=123.0) is False
    stakes2 = await stake_ledger.open_stakes(limit=10, brain_client=brain)
    assert next(s for s in stakes2 if s.id == regret.id).voiced_at == 123.0
    assert next(s for s in stakes2 if s.id == hope.id).voiced_at is None


@pytest.mark.asyncio
async def test_mark_voiced_is_once_only_and_open_only(brain):
    regret = await _open(brain, "regret", "I called it finished when it wasn't")
    assert await stake_ledger.mark_voiced(regret.id, brain_client=brain, now=100.0) is True
    # a second stamp changes nothing — the first voicing is the record
    assert await stake_ledger.mark_voiced(regret.id, brain_client=brain, now=200.0) is False
    row = next(s for s in await stake_ledger.open_stakes(limit=5, brain_client=brain) if s.id == regret.id)
    assert row.voiced_at == 100.0
    # a closed regret never takes a stamp
    other = await _open(brain, "regret", "I didn't ask the question")
    await stake_ledger.apply_ops(
        [{"op": "resolve", "id": other.id, "resolution": "it taught me to ask"}],
        source_channel_id="night_review", brain_client=brain)
    assert await stake_ledger.mark_voiced(other.id, brain_client=brain, now=300.0) is False
    closed = next(r for r in await stake_ledger.recent_stakes(brain_client=brain) if r.id == other.id)
    assert closed.voiced_at is None
    # an unknown id is simply False — never an exception
    assert await stake_ledger.mark_voiced(99_999, brain_client=brain) is False


@pytest.mark.asyncio
async def test_mark_voiced_never_raises(brain, monkeypatch, caplog):
    caplog.set_level("WARNING", logger="ai.stake_ledger")

    async def boom():
        raise RuntimeError("db on fire")

    monkeypatch.setattr(brain, "get_connection", boom)
    assert await stake_ledger.mark_voiced(1, brain_client=brain) is False
    assert any("mark_voiced failed" in r.getMessage() for r in caplog.records)


# ── the migration: a ledger born before step 25 ─────────────────────────

_OLD_CREATE = """
CREATE TABLE IF NOT EXISTS stakes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    stake TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open',
    resolution TEXT NOT NULL DEFAULT '',
    horizon_at REAL,
    source_channel_id TEXT NOT NULL DEFAULT '',
    last_surfaced_at REAL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    resolved_at REAL
)
"""


async def _columns(brain) -> list[str]:
    conn = await brain.get_connection()
    try:
        rows = await conn.fetch("PRAGMA table_info(stakes)")
    finally:
        await conn.close()
    return [str(r["name"]) for r in rows]


@pytest.mark.asyncio
async def test_ensure_schema_migrates_a_pre_step_25_ledger(local_tmp_dir):
    # Batch 8, task 4 (ruling T4-4): the catalog owns stakes now, and what
    # carries an old ledger forward is the catalog's ALTER on the database's
    # one version-0 boot. So the old ledger is built raw BEFORE the brain's
    # first initialize, the way a database from before step 25 arrives.
    db_path = local_tmp_dir / "pre_step_25.sqlite3"
    raw = sqlite3.connect(db_path)
    try:
        raw.execute(_OLD_CREATE)
        raw.execute(
            "INSERT INTO stakes (kind, stake, status, created_at, updated_at) "
            "VALUES ('regret', 'born before the column', 'open', 1.0, 1.0)"
        )
        raw.commit()
        assert "voiced_at" not in {r[1] for r in raw.execute("PRAGMA table_info(stakes)")}
    finally:
        raw.close()

    brain = PalliBrain(str(db_path))
    await brain.initialize()
    try:
        await stake_ledger.ensure_schema(brain_client=brain)
        assert "voiced_at" in await _columns(brain)
        # the old row reads back unvoiced, and takes the stamp like any regret
        (row,) = await stake_ledger.open_stakes(limit=5, brain_client=brain)
        assert row.stake == "born before the column" and row.voiced_at is None
        assert await stake_ledger.mark_voiced(row.id, brain_client=brain, now=5.0) is True
        # ensure_schema is idempotent on the migrated table
        await stake_ledger.ensure_schema(brain_client=brain)
        assert (await _columns(brain)).count("voiced_at") == 1
    finally:
        await brain.close()


@pytest.mark.asyncio
async def test_fresh_ledger_is_born_with_the_column(brain):
    await stake_ledger.ensure_schema(brain_client=brain)
    assert "voiced_at" in await _columns(brain)


def test_catalog_registers_the_additive_alter():
    from core.sqlite_schema_catalog import DEFAULT_SQLITE_MIGRATION_STATEMENTS

    assert "ALTER TABLE stakes ADD COLUMN voiced_at REAL" in DEFAULT_SQLITE_MIGRATION_STATEMENTS


# ── the say rides regret_id (the want_id pattern, spec §4) ──────────────

def test_parse_impulse_keeps_regret_id_and_absent_adds_no_key():
    out = pm._parse_impulse({"kind": "say", "why": "w", "framing": "f", "regret_id": " 7 "})
    assert out["regret_id"] == "7"
    out = pm._parse_impulse({"kind": "say", "why": "w", "framing": "f"})
    assert "regret_id" not in out                      # absent adds NO key (meta purity)
    out = pm._parse_impulse({"kind": "say", "why": "w", "framing": "f", "regret_id": "x" * 100})
    assert len(out["regret_id"]) == 64
    out = pm._parse_impulse({"kind": "say", "why": "w", "framing": "f", "regret_id": 7})
    assert "regret_id" not in out                      # strings only, like want_id


def test_moment_prompt_live_rungs_carry_the_slots_with_zero_byte_collapse():
    for name in ("present_moment_appraisal_wanting.txt", "present_moment_appraisal_arbiter.txt"):
        text = (Path("prompts") / name).read_text(encoding="utf-8")
        assert text.count("{regret_say_line}") == 1, name
        assert text.count("{regret_id_field}") == 1, name
        # the prose slot sits mid-line between two sentences: "" leaves the
        # sentence flow byte-identical; the value starts with its own space.
        # Organ 4.13 (opinions) put its own say slot immediately after this
        # one, and organ 4.9 (grounded vulnerability) a third after that —
        # all three collapse to "", so the same sentence still follows.
        i = text.index("{regret_say_line}")
        assert text[i - 1] == "." and text[i + len("{regret_say_line}"):].startswith(
            "{view_say_line}{disclosure_say_line} When they arrive"), name
        # the field slot sits inside the JSON grammar right before
        # held_before — with the views' (4.13) and the small truths' (4.9)
        # own field slots between them
        j = text.index("{regret_id_field}")
        assert text[j - 2:j] == ", " and text[j + len("{regret_id_field}"):].startswith(
            '{view_id_field}{disclosure_id_field}"held_before"'), name
    for name in ("present_moment_appraisal.txt", "present_moment_appraisal_affect.txt"):
        assert "{regret_" not in (Path("prompts") / name).read_text(encoding="utf-8"), name


def test_regret_voicing_kwargs_off_is_zero_bytes_on_names_the_field():
    off = pm._regret_voicing_kwargs(False)
    on = pm._regret_voicing_kwargs(True)
    assert off == {"regret_say_line": "", "regret_id_field": ""}
    assert on["regret_say_line"].startswith(" ") and not on["regret_say_line"].endswith(" ")
    assert '"regret_id"' in on["regret_say_line"]
    assert "once" in on["regret_say_line"] and "never to make them feel" in on["regret_say_line"]
    assert on["regret_id_field"].startswith('"regret_id": "<') and on["regret_id_field"].endswith('>", ')


def _stake(kind: str, sid: int, *, voiced_at=None) -> Stake:
    return Stake(id=sid, kind=kind, stake=f"stake {sid}", status="open", resolution="",
                 horizon_at=None, source_channel_id="", last_surfaced_at=None,
                 created_at=1.0, updated_at=1.0, resolved_at=None, voiced_at=voiced_at)


def _fake_open_stakes(rows):
    async def fake(*, limit=8, brain_client=None):
        return list(rows)
    return fake


def _gather():
    return asyncio.run(pmg.gather_moment_inputs(owner_user_id=1))


def test_gather_offers_regret_voicing_only_when_on_and_a_regret_is_open():
    hope, regret = _stake("hope", 2), _stake("regret", 7)
    with patch("ai.stake_ledger.ledger_enabled", return_value=True), \
         patch("ai.stake_ledger.regret_enabled", return_value=True), \
         patch("ai.stake_ledger.open_stakes", _fake_open_stakes([hope, regret])):
        out = _gather()
    assert out.regret_voicing is True
    assert out.sense_health.get("regret_voicing") == "ok"
    with patch("ai.stake_ledger.ledger_enabled", return_value=True), \
         patch("ai.stake_ledger.regret_enabled", return_value=True), \
         patch("ai.stake_ledger.open_stakes", _fake_open_stakes([hope])):
        out = _gather()
    assert out.regret_voicing is False                 # the empty book is honest
    assert out.sense_health.get("regret_voicing") == "ok"
    with patch("ai.stake_ledger.ledger_enabled", return_value=True), \
         patch("ai.stake_ledger.regret_enabled", return_value=False), \
         patch("ai.stake_ledger.open_stakes", _fake_open_stakes([hope, regret])):
        out = _gather()
    assert out.regret_voicing is False
    assert "regret_voicing" not in out.sense_health    # flag off: the sense never ran


def test_regret_enabled_is_the_ledgers_gate(monkeypatch):
    from core.turn import companion_flags

    monkeypatch.setattr(companion_flags, "companion_settings", lambda: SimpleNamespace(regret=True))
    assert _REAL_REGRET_ENABLED() is True
    monkeypatch.setattr(companion_flags, "companion_settings", lambda: SimpleNamespace(regret=False))
    assert _REAL_REGRET_ENABLED() is False
    monkeypatch.setattr(companion_flags, "companion_settings", lambda: None)
    assert _REAL_REGRET_ENABLED() is False             # default-off faculty: unbooted = off


# ── the arbiter stamps ON SEND, never on intent ─────────────────────────

def _impulse(**kw):
    base = {"kind": "say", "why": "saying the thing", "framing": "I called it finished when it wasn't. That was mine."}
    base.update(kw)
    return base


async def _release(brain, impulse, tmp_path, *, freshness=True, sender=None):
    """Drive the real release gate with every deterministic gate open and
    the tmp brain as the ledger's default (mark_voiced resolves it at call
    time) — the want_id harness, plus the brain."""
    calls = {}

    async def fake_outbound(**kwargs):
        calls["outbound"] = kwargs
        return True

    with patch.object(arb, "arbiter_enabled", return_value=True), \
         patch.object(arb, "_pick_surface", new=AsyncMock(return_value=("discord", "42"))), \
         patch.object(arb, "_run_phase", return_value="idle"), \
         patch.object(arb, "_is_working", return_value=False), \
         patch.object(arb, "_last_user_message_at", new=AsyncMock(return_value=0.0)), \
         patch.object(arb, "_list_active", new=AsyncMock(return_value=[])), \
         patch.object(arb, "_record_outbound", new=sender or fake_outbound), \
         patch.object(arb, "_freshness_check", new=AsyncMock(return_value=(freshness, "fine"))), \
         patch.object(arb, "ARBITER_DATA_DIR", tmp_path / "arbiter"), \
         patch.object(qs, "QUESTS_DIR", tmp_path / "quests"), \
         patch("core.database.brain", brain):
        verdict = await arb.consider_release(impulse=impulse, owner_user_id=1, arbiter_live=True)
    return verdict, calls


def _sent_lines(tmp_path):
    files = list((tmp_path / "arbiter").glob("*.jsonl"))
    if not files:
        return []
    return [json.loads(l) for l in files[0].read_text().splitlines()
            if l.strip() and json.loads(l).get("disposition") == "sent"]


async def _voiced_at(brain, sid):
    return next(s for s in await stake_ledger.open_stakes(limit=20, brain_client=brain) if s.id == sid).voiced_at


@pytest.mark.asyncio
async def test_a_sent_say_stamps_the_regret_and_a_dropped_one_does_not(brain, tmp_path):
    regret = await _open(brain, "regret", "I called it finished when it wasn't")
    second = await _open(brain, "regret", "I didn't ask the question")
    verdict, calls = await _release(brain, _impulse(regret_id=str(regret.id)), tmp_path)
    assert verdict["disposition"] == "sent"
    assert verdict.get("regret_voiced") is True and "regret_unmatched" not in verdict
    assert calls["outbound"]["extra"]["regret_id"] == regret.id
    sent = _sent_lines(tmp_path)
    assert sent and sent[0]["regret_id"] == regret.id
    assert await _voiced_at(brain, regret.id) is not None
    # the freshness judge drops the second one — a dropped say stamps nothing
    verdict, _ = await _release(brain, _impulse(regret_id=str(second.id)), tmp_path, freshness=False)
    assert verdict["disposition"] == "stale_dropped"
    assert await _voiced_at(brain, second.id) is None


@pytest.mark.asyncio
async def test_a_failed_send_stamps_nothing(brain, tmp_path):
    regret = await _open(brain, "regret", "I buried him in settings")

    async def boom(**kwargs):
        raise RuntimeError("discord down")

    verdict, _ = await _release(brain, _impulse(regret_id=str(regret.id)), tmp_path, sender=boom)
    assert verdict["disposition"] == "error_closed"
    assert await _voiced_at(brain, regret.id) is None


@pytest.mark.asyncio
async def test_the_id_may_come_in_the_tag_form_she_reads(brain, tmp_path):
    regret = await _open(brain, "regret", "I left the question unasked")
    verdict, calls = await _release(brain, _impulse(regret_id=f"regret {regret.id}"), tmp_path)
    assert verdict["disposition"] == "sent" and verdict.get("regret_voiced") is True
    assert calls["outbound"]["extra"]["regret_id"] == regret.id
    assert await _voiced_at(brain, regret.id) is not None


@pytest.mark.asyncio
async def test_unparseable_regret_id_is_recorded_and_never_blocks(brain, tmp_path):
    verdict, calls = await _release(brain, _impulse(regret_id="the lake one"), tmp_path)
    assert verdict["disposition"] == "sent"
    assert verdict.get("regret_unmatched") is True and "regret_voiced" not in verdict
    assert "regret_id" not in calls["outbound"]["extra"]
    sent = _sent_lines(tmp_path)
    assert sent and sent[0]["regret_id"] == "the lake one"     # her token, auditable


@pytest.mark.asyncio
async def test_no_regret_id_changes_nothing(brain, tmp_path):
    verdict, calls = await _release(brain, _impulse(), tmp_path)
    assert verdict["disposition"] == "sent"
    assert "regret_voiced" not in verdict and "regret_unmatched" not in verdict
    assert "regret_id" not in calls["outbound"]["extra"]
    sent = _sent_lines(tmp_path)
    assert sent and "regret_id" not in sent[0]


@pytest.mark.asyncio
async def test_a_stamp_that_finds_no_open_unvoiced_regret_still_sends(brain, tmp_path):
    hope = await _open(brain, "hope", "the survey lands")
    verdict, _ = await _release(brain, _impulse(regret_id=str(hope.id)), tmp_path)
    assert verdict["disposition"] == "sent"
    assert verdict.get("regret_voiced") is False              # nothing to stamp; the say was hers to make
    assert await _voiced_at(brain, hope.id) is None


@pytest.mark.asyncio
async def test_stamp_failure_never_unsends(brain, tmp_path):
    regret = await _open(brain, "regret", "fragile bookkeeping")
    with patch("ai.stake_ledger.mark_voiced", new=AsyncMock(side_effect=RuntimeError("disk gone"))):
        verdict, _ = await _release(brain, _impulse(regret_id=str(regret.id)), tmp_path)
    assert verdict["disposition"] == "sent"                   # fail-open, like the want progress append
    assert verdict.get("regret_voiced") is False
