"""Opinions organ (4.13) — ledger foundations. Harness = test_stake_ledger.py's."""
from __future__ import annotations

import shutil
import time
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
import pytest_asyncio

from ai import stake_ledger
from core.database import PalliBrain

# Captured at import — the root conftest pins the module attribute to
# lambda: False for every test (the live yaml ships opinions: true); the
# snapshot test below exercises the REAL function through that pin.
_REAL_OPINIONS_ENABLED = stake_ledger.opinions_enabled


@pytest.fixture
def local_tmp_dir(tmp_path: Path):
    directory = tmp_path / f"opinions_test_{uuid.uuid4().hex}"
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


def test_view_is_a_valid_kind_and_statuses_exist():
    assert "view" in stake_ledger.VALID_KINDS
    assert stake_ledger.STATUS_DORMANT == "dormant"
    assert stake_ledger.STATUS_SETTLED == "settled"


def test_knob_defaults_and_gate_fail_closed(monkeypatch):
    monkeypatch.setattr(
        "core.turn.companion_flags.companion_settings", lambda: None
    )
    assert stake_ledger._views_max_active() == 5
    assert stake_ledger._views_dormant_cap() == 10
    assert _REAL_OPINIONS_ENABLED() is False  # None snapshot = OFF


@pytest.mark.asyncio
async def test_schema_gains_evidence_and_muted_at(brain: PalliBrain):
    await stake_ledger.ensure_schema(brain_client=brain)
    conn = await brain.get_connection()
    try:
        cols = {r["name"] for r in await conn.fetch("PRAGMA table_info(stakes)")}
    finally:
        await conn.close()
    assert {"evidence", "muted_at"} <= cols


@pytest.mark.asyncio
async def test_row_mapping_carries_new_fields(brain: PalliBrain):
    await stake_ledger.ensure_schema(brain_client=brain)
    conn = await brain.get_connection()
    try:
        await conn.execute(
            "INSERT INTO stakes (kind, stake, status, evidence, muted_at, "
            "created_at, updated_at) VALUES ('view', 'I hold X', 'open', "
            "'two invoices, one bad weld (06-12, 06-19)', 123.0, 1.0, 1.0)"
        )
    finally:
        await conn.close()
    rows = await stake_ledger.open_stakes(limit=8, brain_client=brain)
    assert rows and rows[0].evidence.startswith("two invoices")
    assert rows[0].muted_at == 123.0


def test_knobs_and_gate_read_the_snapshot(monkeypatch):
    """The positive path: a booted snapshot's values win over the defaults."""
    monkeypatch.setattr(
        "core.turn.companion_flags.companion_settings",
        lambda: SimpleNamespace(opinions=True, views_max_active=3, views_dormant_cap=7),
    )
    assert _REAL_OPINIONS_ENABLED() is True
    assert stake_ledger._views_max_active() == 3
    assert stake_ledger._views_dormant_cap() == 7


def test_valid_ops_is_the_eight_tuple():
    assert stake_ledger.VALID_OPS == (
        "open", "update", "resolve", "fade", "settle", "promote", "mute", "unmute",
    )


def _view_op(stake="I side with the 1978 manuals over the forums",
             evidence="his 03-14 scan vs the forum thread; the torque table"):
    return {"op": "open", "kind": "view", "stake": stake, "evidence": evidence}


@pytest.mark.asyncio
async def test_view_without_evidence_is_refused(brain: PalliBrain):
    s = await stake_ledger.apply_ops(
        [{"op": "open", "kind": "view", "stake": "an unfounded take"}],
        brain_client=brain,
    )
    assert s["applied"] == 0 and s["rejected"] == 1


@pytest.mark.asyncio
async def test_view_files_dormant_at_active_cap(brain: PalliBrain, monkeypatch):
    monkeypatch.setattr(stake_ledger, "_views_max_active", lambda: 2)
    for i in range(2):
        await stake_ledger.apply_ops([_view_op(stake=f"view {i}")], brain_client=brain)
    s = await stake_ledger.apply_ops([_view_op(stake="the third")], brain_client=brain)
    assert s["applied"] == 1 and s["dormant"], "third view must file dormant"
    conn = await brain.get_connection()
    try:
        n = await conn.fetchval(
            "SELECT COUNT(*) FROM stakes WHERE kind='view' AND status='dormant'"
        )
    finally:
        await conn.close()
    assert int(n) == 1


@pytest.mark.asyncio
async def test_revise_with_reason_appends_the_trail(brain: PalliBrain):
    s = await stake_ledger.apply_ops([_view_op()], brain_client=brain)
    vid = s["opened"][0]
    s2 = await stake_ledger.apply_ops(
        [{"op": "update", "id": vid, "stake": "the forums had it right on the late models",
          "evidence": "the 08-30 line items", "reason": "he showed me the revised table"}],
        brain_client=brain,
    )
    assert s2["applied"] == 1
    rows = await stake_ledger.open_stakes(limit=8, brain_client=brain)
    v = next(r for r in rows if r.id == vid)
    assert "was \"I side with the 1978 manuals" in v.evidence  # the arc survives
    assert "he showed me the revised table" in v.evidence


@pytest.mark.asyncio
async def test_settle_promote_mute_unmute_are_view_only(brain: PalliBrain):
    hope = await stake_ledger.apply_ops(
        [{"op": "open", "kind": "hope", "stake": "the survey lands clean",
          "evidence": "a stray key a non-view must never keep"}],
        brain_client=brain,
    )
    s = await stake_ledger.apply_ops(
        [{"op": "mute", "id": hope["opened"][0]}], brain_client=brain
    )
    assert s["applied"] == 0 and s["rejected"] == 1  # not a view → rowcount 0
    # evidence is view-only AT THE STORE: the key was dropped, not persisted
    open_now = await stake_ledger.open_stakes(limit=8, brain_client=brain)
    assert next(r for r in open_now if r.id == hope["opened"][0]).evidence == ""

    v = await stake_ledger.apply_ops([_view_op()], brain_client=brain)
    vid = v["opened"][0]
    assert (await stake_ledger.apply_ops(
        [{"op": "mute", "id": vid}], brain_client=brain))["applied"] == 1
    rows = await stake_ledger.open_stakes(limit=8, brain_client=brain)
    assert next(r for r in rows if r.id == vid).muted_at is not None
    assert (await stake_ledger.apply_ops(
        [{"op": "unmute", "id": vid}], brain_client=brain))["applied"] == 1
    assert (await stake_ledger.apply_ops(
        [{"op": "settle", "id": vid,
          "resolution": "not a live question any more — it's just how I read them"}],
        brain_client=brain))["applied"] == 1
    # settle is TERMINAL: the view leaves the open set. Scoped to vid because
    # the hope opened above is still open on purpose — it is the control that
    # proved mute view-only, and nothing in this test closes it.
    still_open = await stake_ledger.open_stakes(limit=8, brain_client=brain)
    assert not [r for r in still_open if r.id == vid]


@pytest.mark.asyncio
async def test_promote_respects_the_active_cap(brain: PalliBrain, monkeypatch):
    monkeypatch.setattr(stake_ledger, "_views_max_active", lambda: 1)
    a = await stake_ledger.apply_ops([_view_op(stake="first")], brain_client=brain)
    b = await stake_ledger.apply_ops([_view_op(stake="second")], brain_client=brain)
    dormant_id = b["dormant"][0]
    blocked = await stake_ledger.apply_ops(
        [{"op": "promote", "id": dormant_id}], brain_client=brain
    )
    assert blocked["applied"] == 0 and blocked["rejected"] == 1  # slot full
    await stake_ledger.apply_ops(
        [{"op": "settle", "id": a["opened"][0], "resolution": "settled into me"}],
        brain_client=brain,
    )
    ok = await stake_ledger.apply_ops(
        [{"op": "promote", "id": dormant_id}], brain_client=brain
    )
    assert ok["applied"] == 1


@pytest.mark.asyncio
async def test_mark_view_voiced_stamps_every_send(brain: PalliBrain):
    v = await stake_ledger.apply_ops([_view_op()], brain_client=brain)
    vid = v["opened"][0]
    assert await stake_ledger.mark_view_voiced(vid, brain_client=brain, now=10.0)
    assert await stake_ledger.mark_view_voiced(vid, brain_client=brain, now=20.0)
    rows = await stake_ledger.open_stakes(limit=8, brain_client=brain)
    assert next(r for r in rows if r.id == vid).voiced_at == 20.0  # last-said, not once-ever


@pytest.mark.asyncio
async def test_a_view_ignores_the_shared_open_cap(brain: PalliBrain, monkeypatch):
    """spec §6: views are governed ONLY by their own caps. A shared cap full of
    hopes must not keep her from forming a view."""
    monkeypatch.setattr(stake_ledger, "_max_open", lambda: 2)
    for i in range(2):
        await stake_ledger.apply_ops(
            [{"op": "open", "kind": "hope", "stake": f"hope {i}"}], brain_client=brain
        )
    # the shared cap is full of non-views — one more hope is refused ...
    blocked = await stake_ledger.apply_ops(
        [{"op": "open", "kind": "hope", "stake": "one too many"}], brain_client=brain
    )
    assert blocked["applied"] == 0 and blocked["rejected"] == 1
    # ... but a view files anyway, on its own caps (open, not dormant)
    v = await stake_ledger.apply_ops([_view_op()], brain_client=brain)
    assert v["applied"] == 1 and not v["dormant"]


@pytest.mark.asyncio
async def test_open_views_never_squat_the_shared_cap(brain: PalliBrain, monkeypatch):
    """The other direction: open views must not consume a hope's slot."""
    monkeypatch.setattr(stake_ledger, "_max_open", lambda: 2)
    for i in range(2):
        await stake_ledger.apply_ops([_view_op(stake=f"view {i}")], brain_client=brain)
    h = await stake_ledger.apply_ops(
        [{"op": "open", "kind": "hope", "stake": "still room for me"}], brain_client=brain
    )
    assert h["applied"] == 1


@pytest.mark.asyncio
async def test_settle_promote_unmute_refuse_a_non_view(brain: PalliBrain):
    """The rowcount-0 guard op by op — kind='view' is in each WHERE."""
    hope = await stake_ledger.apply_ops(
        [{"op": "open", "kind": "hope", "stake": "the survey lands clean"}],
        brain_client=brain,
    )
    hid = hope["opened"][0]
    for op in ({"op": "settle", "id": hid, "resolution": "not hers to settle"},
               {"op": "promote", "id": hid},
               {"op": "unmute", "id": hid}):
        s = await stake_ledger.apply_ops([op], brain_client=brain)
        assert s["applied"] == 0 and s["rejected"] == 1, op["op"]
    # and the hope is untouched: still open, never stamped
    row = next(r for r in await stake_ledger.open_stakes(limit=8, brain_client=brain)
               if r.id == hid)
    assert row.status == "open" and row.muted_at is None


@pytest.mark.asyncio
async def test_mark_view_voiced_refuses_a_regret(brain: PalliBrain):
    """The view stamp never touches a regret — its one-voicing law is separate."""
    r = await stake_ledger.apply_ops(
        [{"op": "open", "kind": "regret", "stake": "I answered before I understood"}],
        brain_client=brain,
    )
    rid = r["opened"][0]
    assert await stake_ledger.mark_view_voiced(rid, brain_client=brain, now=10.0) is False
    row = next(x for x in await stake_ledger.open_stakes(limit=8, brain_client=brain)
               if x.id == rid)
    assert row.voiced_at is None


# ── the id-bearing renders' shorter tell (review · appraiser) ──────────


def _stake_row(**kw):
    base = dict(id=4, kind="view", stake="I side with the 1978 manuals",
                status="open", resolution="", horizon_at=None,
                source_channel_id="", last_surfaced_at=None,
                created_at=1000.0, updated_at=1000.0, resolved_at=None,
                voiced_at=None, evidence="his 03-14 scan", muted_at=None)
    base.update(kw)
    return stake_ledger.Stake(**base)


def test_voiced_note_carries_the_view_tells_and_mute_wins():
    said_at = 1756600000.0
    stamp = time.strftime("%m-%d", time.localtime(said_at))
    assert stake_ledger._voiced_note(_stake_row()) == ""
    assert stake_ledger._voiced_note(_stake_row(voiced_at=said_at)) == f" [last said {stamp}]"
    assert stake_ledger._voiced_note(_stake_row(muted_at=1.0)) == (
        " [held quietly — they asked to leave it]"
    )
    # muted outranks said: one tell per line, and "leave it" is the louder one
    assert stake_ledger._voiced_note(_stake_row(voiced_at=said_at, muted_at=1.0)) == (
        " [held quietly — they asked to leave it]"
    )


def test_voiced_note_leaves_regret_and_every_other_kind_byte_unchanged():
    assert stake_ledger._voiced_note(
        _stake_row(kind="regret", evidence="", voiced_at=1756600000.0)
    ) == " [said to them once]"
    assert stake_ledger._voiced_note(_stake_row(kind="regret", evidence="")) == ""
    for kind in ("hope", "worry", "taste", "wonder"):
        assert stake_ledger._voiced_note(
            _stake_row(kind=kind, evidence="", voiced_at=1756600000.0, muted_at=1.0)
        ) == "", kind
