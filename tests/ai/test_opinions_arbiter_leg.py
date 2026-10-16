"""Organ 4.13 — the view_id leg. Shape copied from test_regret_voicing.py's
arbiter-leg section; only the kind changes. See that file for the release
harness (monkeypatched _moment_gates/_freshness_check/_release_outbound).

The templates already promise the moment that a named view is "recorded as
said on send" — this leg is what makes that promise true. Views are LAST-SAID,
not once-ever (mark_view_voiced re-stamps an open view every time), so the
arbiter's job is only: resolve the token, carry it on the row, stamp after a
successful send, and never let a bad token block her.

DBs under tmp_path; flags pinned in-body (3352e3d)."""
from __future__ import annotations

import shutil
import uuid
from pathlib import Path

import json
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio

import ai.arbiter as arb
import ai.quest_store as qs
from ai import stake_ledger
from core.database import PalliBrain


@pytest.fixture
def local_tmp_dir(tmp_path: Path):
    d = tmp_path / f"opinions_arbiter_{uuid.uuid4().hex}"
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


async def _open(brain, kind, text, *, evidence="his 03-14 scan vs the forum thread"):
    await stake_ledger.apply_ops(
        [{"op": "open", "kind": kind, "stake": text, "horizon": None,
          **({"evidence": evidence} if kind == "view" else {})}],
        source_channel_id="t", brain_client=brain,
    )
    return next(s for s in await stake_ledger.open_stakes(limit=20, brain_client=brain)
                if s.stake == text)


# ── the arbiter stamps ON SEND, never on intent ─────────────────────────

def _impulse(**kw):
    base = {"kind": "say", "why": "saying the thing",
            "framing": "I still think the 1978 manuals have the torque right."}
    base.update(kw)
    return base


async def _release(brain, impulse, tmp_path, *, freshness=True, sender=None):
    """Drive the real release gate with every deterministic gate open and
    the tmp brain as the ledger's default (mark_view_voiced resolves it at
    call time) — the want_id harness, plus the brain."""
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
    return next(s for s in await stake_ledger.open_stakes(limit=20, brain_client=brain)
                if s.id == sid).voiced_at


@pytest.mark.asyncio
async def test_a_sent_say_stamps_the_view_and_a_dropped_one_does_not(brain, tmp_path):
    view = await _open(brain, "view", "the 1978 manuals have the torque right")
    second = await _open(brain, "view", "the forum thread is copying itself")
    verdict, calls = await _release(brain, _impulse(view_id=str(view.id)), tmp_path)
    assert verdict["disposition"] == "sent"
    assert verdict.get("view_voiced") is True and "view_unmatched" not in verdict
    assert calls["outbound"]["extra"]["view_id"] == view.id
    sent = _sent_lines(tmp_path)
    assert sent and sent[0]["view_id"] == view.id
    assert await _voiced_at(brain, view.id) is not None
    # the freshness judge drops the second one — a dropped say stamps nothing
    verdict, _ = await _release(brain, _impulse(view_id=str(second.id)), tmp_path, freshness=False)
    assert verdict["disposition"] == "stale_dropped"
    assert await _voiced_at(brain, second.id) is None


@pytest.mark.asyncio
async def test_the_id_may_come_in_the_tag_form_she_reads(brain, tmp_path):
    """(a) the gathers tag it "view 7" — the digits are the id."""
    view = await _open(brain, "view", "the torque table was revised for a reason")
    verdict, calls = await _release(brain, _impulse(view_id=f"view {view.id}"), tmp_path)
    assert verdict["disposition"] == "sent" and verdict.get("view_voiced") is True
    assert calls["outbound"]["extra"]["view_id"] == view.id
    sent = _sent_lines(tmp_path)
    assert sent and sent[0]["view_id"] == view.id
    assert await _voiced_at(brain, view.id) is not None


@pytest.mark.asyncio
async def test_the_stamp_is_awaited_with_the_resolved_id(brain, tmp_path):
    """(b) the row, the verdict and the awaited stamp all carry the int 7."""
    seen = []

    async def recorder(stake_id, **kwargs):
        seen.append(stake_id)
        return True

    with patch("ai.stake_ledger.mark_view_voiced", new=recorder):
        verdict, calls = await _release(brain, _impulse(view_id="view 7"), tmp_path)
    assert verdict["disposition"] == "sent"
    assert seen == [7]                                        # resolved, and AWAITED
    assert verdict.get("view_voiced") is True
    assert calls["outbound"]["extra"]["view_id"] == 7
    sent = _sent_lines(tmp_path)
    assert sent and sent[0]["view_id"] == 7


@pytest.mark.asyncio
async def test_a_failed_send_stamps_nothing(brain, tmp_path):
    view = await _open(brain, "view", "the invoices say otherwise")

    async def boom(**kwargs):
        raise RuntimeError("discord down")

    verdict, _ = await _release(brain, _impulse(view_id=str(view.id)), tmp_path, sender=boom)
    assert verdict["disposition"] == "error_closed"
    assert await _voiced_at(brain, view.id) is None


@pytest.mark.asyncio
async def test_unparseable_view_id_is_recorded_and_never_blocks(brain, tmp_path):
    """(c) her token survives on the row; nothing about it stops the say."""
    verdict, calls = await _release(brain, _impulse(view_id="the manuals one"), tmp_path)
    assert verdict["disposition"] == "sent"
    assert verdict.get("view_unmatched") is True and "view_voiced" not in verdict
    assert "view_id" not in calls["outbound"]["extra"]
    sent = _sent_lines(tmp_path)
    assert sent and sent[0]["view_id"] == "the manuals one"    # her token, auditable


@pytest.mark.asyncio
async def test_no_view_id_changes_nothing(brain, tmp_path):
    """The negative: an impulse without a view names no view anywhere."""
    verdict, calls = await _release(brain, _impulse(), tmp_path)
    assert verdict["disposition"] == "sent"
    assert "view_voiced" not in verdict and "view_unmatched" not in verdict
    assert "view_id" not in calls["outbound"]["extra"]
    sent = _sent_lines(tmp_path)
    assert sent and "view_id" not in sent[0]


@pytest.mark.asyncio
async def test_a_stamp_that_finds_no_open_view_still_sends(brain, tmp_path):
    hope = await _open(brain, "hope", "the survey lands")
    verdict, _ = await _release(brain, _impulse(view_id=str(hope.id)), tmp_path)
    assert verdict["disposition"] == "sent"
    assert verdict.get("view_voiced") is False       # nothing to stamp; the say was hers to make
    assert await _voiced_at(brain, hope.id) is None


@pytest.mark.asyncio
async def test_stamp_failure_never_unsends(brain, tmp_path):
    view = await _open(brain, "view", "fragile bookkeeping")
    with patch("ai.stake_ledger.mark_view_voiced",
               new=AsyncMock(side_effect=RuntimeError("disk gone"))):
        verdict, _ = await _release(brain, _impulse(view_id=str(view.id)), tmp_path)
    assert verdict["disposition"] == "sent"          # fail-open, like the want progress append
    assert verdict.get("view_voiced") is False


@pytest.mark.asyncio
async def test_a_view_may_be_said_again_last_said_not_once_ever(brain, tmp_path):
    """The regret leg's one difference: a view re-stamps on every send."""
    view = await _open(brain, "view", "the torque spec never changed")
    verdict, _ = await _release(brain, _impulse(view_id=str(view.id)), tmp_path)
    assert verdict.get("view_voiced") is True
    first = await _voiced_at(brain, view.id)
    assert first is not None
    verdict, _ = await _release(brain, _impulse(view_id=str(view.id)), tmp_path)
    assert verdict.get("view_voiced") is True        # not a once-ever guard
    assert await _voiced_at(brain, view.id) >= first


@pytest.mark.asyncio
async def test_the_regret_leg_is_untouched_beside_it(brain, tmp_path):
    """Both ids may ride one say; neither leg reads the other's key."""
    regret = await _open(brain, "regret", "I called it finished when it wasn't")
    view = await _open(brain, "view", "the manuals still win on torque")
    verdict, calls = await _release(
        brain, _impulse(regret_id=str(regret.id), view_id=str(view.id)), tmp_path)
    assert verdict["disposition"] == "sent"
    assert verdict.get("regret_voiced") is True and verdict.get("view_voiced") is True
    assert calls["outbound"]["extra"]["regret_id"] == regret.id
    assert calls["outbound"]["extra"]["view_id"] == view.id
    assert await _voiced_at(brain, regret.id) is not None
    assert await _voiced_at(brain, view.id) is not None
