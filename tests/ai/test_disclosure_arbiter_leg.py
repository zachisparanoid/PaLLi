"""Organ 4.9 — door 2's leg: the arbiter learns `disclosure_id`.

Spec §4/§5. Task 5 gave her say a board of ≤2 unsaid small truths (with
their ids) and a say line promising that a named one "is stamped as shared
on send". This file is what makes that promise true — and what keeps it
from over-promising:

* **Slugs, never digits.** A disclosure id is a `_slugify()` slug
  (`small-truth-0-9f2c1b`), so the resolve is the want_id shape — exact id,
  then a UNIQUE prefix, over the UNSAID book — and never the regret/view
  `re.search(r"\\d+")` shape. `"7"` is not an id here; it is an unmatched
  token, recorded and harmless.
* **Stamped on send, never on intent** (spec §5, the organ's spine). A
  stale drop, a failed send, or any gate above stamps NOTHING: the truth
  survives unsaid and offerable. Said-once is forever, so a stamp on
  intent would spend a truth she never actually told him.
* **Unmatched is recorded, never blocking** — the view_id posture. Her raw
  token rides the ledger row so the claim stays auditable either way, and
  the say she already composed goes out untouched (the framing IS the
  message).
* **No board cross-check.** The rail does not verify the id was on THIS
  turn's board; `mark_said`'s own refusals (unknown / said / faded) are the
  authority. A truth that is unsaid but sorted behind the offered two is
  still hers to share.
* **Flag off ⇒ the leg is dark.** No resolve, no extra key, no ledger key,
  no stamp — byte-identical to the arbiter before this organ existed. A
  truth planted while the organ was on must not be spendable after the
  owner turns it off.

Harness copied from tests/ai/test_opinions_arbiter_leg.py (which took it
from test_regret_voicing.py): monkeypatched `_moment_gates` pieces,
`_freshness_check`, `_record_outbound`, ARBITER_DATA_DIR under tmp_path,
flags pinned in-body (3352e3d). Two deliberate substitutions: that file's
`brain`/`local_tmp_dir` fixtures and `_open`/`_voiced_at` helpers are
stake_ledger scaffolding — this organ's book is a JSONL file, so they
become the DISCLOSURES_DIR redirect plus `ds.plant`/`_row`; and the
suite-wide autouse `_vulnerability_off_by_default` guard means the flag is
pinned ON inside `_release` (a test-body patch lands after the fixture and
wins).
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

import ai.arbiter as arb
from ai import disclosure_store as ds


def _book(tmp_path: Path) -> Path:
    return tmp_path / "disclosures"


def _seed(tmp_path: Path, n: int, *, text=None) -> list:
    """Plant n truths in this test's book. `_max_open` is pinned because
    plant() otherwise consults the LIVE settings snapshot
    (disclosures_max_open, schema default 4, absent from the yaml today):
    the day the owner sets it to 2, an unpinned seeder would silently plant
    fewer rows than the test reads (the board file's precedent)."""
    with patch.object(ds, "_max_open", lambda: max(4, n)):
        return [ds.plant(text=text or f"small truth {i}",
                         referent={"source": "journal", "dates": "08-29"},
                         root=_book(tmp_path))
                for i in range(n)]


def _row(tmp_path: Path, did: str):
    return ds.load_disclosures(root=_book(tmp_path)).get(did)


# ── the store's resolve: the want_id shape, over UNSAID ─────────────────

def test_resolve_takes_the_exact_slug_then_a_unique_prefix(tmp_path: Path):
    a, b = _seed(tmp_path, 2)
    assert ds.resolve_disclosure_id(a.id, root=_book(tmp_path)).id == a.id
    assert ds.resolve_disclosure_id(b.id, root=_book(tmp_path)).id == b.id
    assert ds.resolve_disclosure_id(a.id[:-3], root=_book(tmp_path)).id == a.id
    assert ds.resolve_disclosure_id("  " + a.id + "  ", root=_book(tmp_path)).id == a.id


def test_resolve_refuses_an_ambiguous_prefix(tmp_path: Path):
    """Two truths in the same words share a slug base — a prefix that fits
    both is no answer at all. Better an unmatched token on the ledger than
    the wrong truth spent forever."""
    a, b = _seed(tmp_path, 2, text="I lost a thread by morning")
    assert a.id != b.id
    stem = a.id.rsplit("-", 1)[0]
    assert b.id.startswith(stem)
    assert ds.resolve_disclosure_id(stem, root=_book(tmp_path)) is None
    assert ds.resolve_disclosure_id(a.id, root=_book(tmp_path)).id == a.id


def test_resolve_is_blind_to_said_faded_and_nonsense(tmp_path: Path):
    """The pool is UNSAID — the same set every other surface reads. A truth
    already told cannot be told again (said once, ever), and one that faded
    left the book for good."""
    said, other = _seed(tmp_path, 2)
    ds.mark_said(said.id, via="reply", root=_book(tmp_path))
    assert ds.resolve_disclosure_id(said.id, root=_book(tmp_path)) is None
    assert ds.resolve_disclosure_id(other.id, root=_book(tmp_path)).id == other.id
    ds.fade_stale(now=other.harvested_at + 10 ** 9, root=_book(tmp_path))
    assert ds.resolve_disclosure_id(other.id, root=_book(tmp_path)) is None
    assert ds.resolve_disclosure_id("no-such-truth", root=_book(tmp_path)) is None
    assert ds.resolve_disclosure_id("", root=_book(tmp_path)) is None
    assert ds.resolve_disclosure_id("   ", root=_book(tmp_path)) is None
    assert ds.resolve_disclosure_id(None, root=_book(tmp_path)) is None
    # and a digit token is just a token — this organ's ids are slugs
    assert ds.resolve_disclosure_id("7", root=_book(tmp_path)) is None


def test_resolve_on_an_empty_book_is_none_not_a_crash(tmp_path: Path):
    assert ds.resolve_disclosure_id("anything", root=_book(tmp_path)) is None


# ── the arbiter stamps ON SEND, never on intent ─────────────────────────

def _impulse(**kw):
    base = {"kind": "say", "why": "saying the thing",
            "framing": "I lost a thread I'd been carrying this week — just gone."}
    base.update(kw)
    return base


def _boom():
    def raiser(*a, **k):
        raise RuntimeError("disk gone")
    return raiser


async def _release(impulse, tmp_path, *, freshness=True, sender=None, flag=True):
    """Drive the real release gate with every deterministic gate open, this
    test's book as the store's root, and the organ's flag pinned in-body
    (`flag` may be a callable — the suite-wide autouse guard already owns
    this attribute, so a test cannot patch it from outside this harness)."""
    calls = {}
    gate = flag if callable(flag) else (lambda: bool(flag))

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
         patch.object(ds, "DISCLOSURES_DIR", _book(tmp_path)), \
         patch.object(ds, "vulnerability_enabled", gate):
        verdict = await arb.consider_release(impulse=impulse, owner_user_id=1,
                                             arbiter_live=True)
    return verdict, calls


def _sent_lines(tmp_path):
    files = list((tmp_path / "arbiter").glob("*.jsonl"))
    if not files:
        return []
    return [json.loads(l) for l in files[0].read_text().splitlines()
            if l.strip() and json.loads(l).get("disposition") == "sent"]


@pytest.mark.asyncio
async def test_a_sent_say_stamps_the_truth_and_a_dropped_one_does_not(tmp_path):
    first, second = _seed(tmp_path, 2)
    verdict, calls = await _release(_impulse(disclosure_id=first.id), tmp_path)
    assert verdict["disposition"] == "sent"
    assert verdict.get("disclosure_shared") is True
    assert "disclosure_unmatched" not in verdict
    assert calls["outbound"]["extra"]["disclosure_id"] == first.id
    sent = _sent_lines(tmp_path)
    assert sent and sent[0]["disclosure_id"] == first.id
    row = _row(tmp_path, first.id)
    assert row.said_at is not None and row.said_via == "say"

    # the freshness judge drops the second one — a dropped say stamps
    # nothing, and the truth stays offerable tomorrow
    verdict, _ = await _release(_impulse(disclosure_id=second.id), tmp_path,
                                freshness=False)
    assert verdict["disposition"] == "stale_dropped"
    assert _row(tmp_path, second.id).said_at is None
    assert [d.id for d in ds.unsaid_disclosures(root=_book(tmp_path))] == [second.id]


@pytest.mark.asyncio
async def test_a_unique_prefix_resolves_and_the_full_id_is_what_is_recorded(tmp_path):
    """She may name it short; the row, the ledger and the stamp carry the
    whole id — a half id on the ledger is not auditable."""
    truth, = _seed(tmp_path, 1)
    verdict, calls = await _release(_impulse(disclosure_id=truth.id[:12]), tmp_path)
    assert verdict["disposition"] == "sent" and verdict.get("disclosure_shared") is True
    assert calls["outbound"]["extra"]["disclosure_id"] == truth.id
    sent = _sent_lines(tmp_path)
    assert sent and sent[0]["disclosure_id"] == truth.id
    assert _row(tmp_path, truth.id).said_at is not None


@pytest.mark.asyncio
async def test_the_stamp_is_called_with_the_resolved_id_and_via_say(tmp_path):
    """The stamp's arguments are the contract: the RESOLVED id (never her
    raw token) and via="say" (the ledger tells the two doors apart)."""
    truth, = _seed(tmp_path, 1)
    seen = []

    def recorder(disclosure_id, *, via, **kwargs):
        seen.append((disclosure_id, via))
        return True

    with patch.object(ds, "mark_said", recorder):
        verdict, _ = await _release(_impulse(disclosure_id=truth.id[:12]), tmp_path)
    assert verdict["disposition"] == "sent"
    assert seen == [(truth.id, "say")]
    assert verdict.get("disclosure_shared") is True


@pytest.mark.asyncio
async def test_a_failed_send_stamps_nothing(tmp_path):
    """The law's hard edge: the send raised, so nothing was shared, so the
    truth is untouched — offerable again tonight."""
    truth, = _seed(tmp_path, 1)

    async def boom(**kwargs):
        raise RuntimeError("discord down")

    verdict, _ = await _release(_impulse(disclosure_id=truth.id), tmp_path, sender=boom)
    assert verdict["disposition"] == "error_closed"
    assert _row(tmp_path, truth.id).said_at is None
    assert [d.id for d in ds.unsaid_disclosures(root=_book(tmp_path))] == [truth.id]


@pytest.mark.asyncio
async def test_an_unresolvable_id_is_recorded_and_never_blocks(tmp_path):
    """Her token survives on the row; nothing about it stops the say."""
    truth, = _seed(tmp_path, 1)
    verdict, calls = await _release(_impulse(disclosure_id="the journal one"), tmp_path)
    assert verdict["disposition"] == "sent"
    assert verdict.get("disclosure_unmatched") is True
    assert "disclosure_shared" not in verdict
    assert "disclosure_id" not in calls["outbound"]["extra"]
    sent = _sent_lines(tmp_path)
    assert sent and sent[0]["disclosure_id"] == "the journal one"   # auditable
    assert _row(tmp_path, truth.id).said_at is None                 # nothing spent


@pytest.mark.asyncio
async def test_a_digit_token_is_not_an_id_here(tmp_path):
    """The regret/view legs int-parse their tokens; this one must not — a
    slug book has no row 7, and guessing one would spend a truth forever."""
    truth, = _seed(tmp_path, 1)
    verdict, calls = await _release(_impulse(disclosure_id="7"), tmp_path)
    assert verdict["disposition"] == "sent"
    assert verdict.get("disclosure_unmatched") is True
    assert "disclosure_id" not in calls["outbound"]["extra"]
    assert _sent_lines(tmp_path)[0]["disclosure_id"] == "7"
    assert _row(tmp_path, truth.id).said_at is None


@pytest.mark.asyncio
async def test_no_disclosure_id_changes_nothing(tmp_path):
    """The negative: an impulse without one names no truth anywhere."""
    _seed(tmp_path, 1)
    verdict, calls = await _release(_impulse(), tmp_path)
    assert verdict["disposition"] == "sent"
    assert "disclosure_shared" not in verdict and "disclosure_unmatched" not in verdict
    assert "disclosure_id" not in calls["outbound"]["extra"]
    sent = _sent_lines(tmp_path)
    assert sent and "disclosure_id" not in sent[0]


@pytest.mark.asyncio
async def test_the_rail_never_second_guesses_the_board(tmp_path):
    """No cross-check that the id was on THIS turn's offered two: the store's
    refusals (unknown / said / faded) are the whole authority. The third
    oldest is unsaid and hers to share."""
    _seed(tmp_path, 3)
    third = ds.unsaid_disclosures(root=_book(tmp_path))[2]
    assert third.id not in [d.id for d in ds.offered_disclosures(root=_book(tmp_path))]
    verdict, calls = await _release(_impulse(disclosure_id=third.id), tmp_path)
    assert verdict["disposition"] == "sent" and verdict.get("disclosure_shared") is True
    assert calls["outbound"]["extra"]["disclosure_id"] == third.id
    assert _row(tmp_path, third.id).said_at is not None


@pytest.mark.asyncio
async def test_a_stamp_the_store_refuses_still_sends(tmp_path):
    """The race the ordering creates: the reply door stamped this truth
    between the resolve and the send. The store refuses, the verdict says
    so, and the say — already his — stands."""
    truth, = _seed(tmp_path, 1)
    with patch.object(ds, "mark_said", lambda *a, **k: False):
        verdict, calls = await _release(_impulse(disclosure_id=truth.id), tmp_path)
    assert verdict["disposition"] == "sent"
    assert verdict.get("disclosure_shared") is False
    assert calls["outbound"]["extra"]["disclosure_id"] == truth.id


@pytest.mark.asyncio
async def test_stamp_failure_never_unsends(tmp_path):
    """Fail-open, like the want progress append beside it: a broken book
    costs her the bookkeeping, never the message she already sent."""
    truth, = _seed(tmp_path, 1)
    with patch.object(ds, "mark_said", _boom()):
        verdict, _ = await _release(_impulse(disclosure_id=truth.id), tmp_path)
    assert verdict["disposition"] == "sent"
    assert verdict.get("disclosure_shared") is False


@pytest.mark.asyncio
async def test_a_truth_is_said_once_ever_not_last_said(tmp_path):
    """The view leg's one difference, inverted: a view re-stamps on every
    send; a small truth is spent. The second naming resolves to nothing."""
    truth, = _seed(tmp_path, 1)
    verdict, _ = await _release(_impulse(disclosure_id=truth.id), tmp_path)
    assert verdict.get("disclosure_shared") is True
    said_at = _row(tmp_path, truth.id).said_at
    assert said_at is not None

    verdict, calls = await _release(_impulse(disclosure_id=truth.id), tmp_path)
    assert verdict["disposition"] == "sent"
    assert verdict.get("disclosure_unmatched") is True     # already spent
    assert "disclosure_shared" not in verdict
    assert "disclosure_id" not in calls["outbound"]["extra"]
    assert _row(tmp_path, truth.id).said_at == said_at     # the first telling stands


@pytest.mark.asyncio
async def test_the_sibling_legs_are_untouched_beside_it(tmp_path):
    """Both ids may ride one say; neither leg reads the other's key."""
    truth, = _seed(tmp_path, 1)
    with patch("ai.stake_ledger.mark_voiced", new=AsyncMock(return_value=True)):
        verdict, calls = await _release(
            _impulse(regret_id="regret 7", disclosure_id=truth.id), tmp_path)
    assert verdict["disposition"] == "sent"
    assert verdict.get("regret_voiced") is True
    assert verdict.get("disclosure_shared") is True
    assert calls["outbound"]["extra"]["regret_id"] == 7
    assert calls["outbound"]["extra"]["disclosure_id"] == truth.id
    sent = _sent_lines(tmp_path)
    assert sent[0]["regret_id"] == 7 and sent[0]["disclosure_id"] == truth.id


@pytest.mark.asyncio
async def test_flag_off_leaves_no_trace_and_spends_nothing(tmp_path):
    """The organ's floor, at the rail: with vulnerability off, a truth
    planted while it was on is unspendable and her token is not even read.
    The verdict, the outbound extra and the ledger row are byte-identical
    to the arbiter before this organ existed."""
    truth, = _seed(tmp_path, 1)
    verdict, calls = await _release(_impulse(disclosure_id=truth.id), tmp_path, flag=False)
    assert verdict["disposition"] == "sent"
    assert "disclosure_shared" not in verdict and "disclosure_unmatched" not in verdict
    assert "disclosure_id" not in calls["outbound"]["extra"]
    sent = _sent_lines(tmp_path)
    assert sent and "disclosure_id" not in sent[0]
    assert _row(tmp_path, truth.id).said_at is None

    # and an unresolvable token stays equally invisible while dark
    verdict, calls = await _release(_impulse(disclosure_id="the journal one"),
                                    tmp_path, flag=False)
    assert "disclosure_unmatched" not in verdict
    assert "disclosure_id" not in calls["outbound"]["extra"]
    assert "disclosure_id" not in _sent_lines(tmp_path)[1]


@pytest.mark.asyncio
async def test_a_flag_read_that_raises_reads_as_dark(tmp_path):
    """Fail-closed on the gate itself (reflect's `_disclosure_filing_on`
    posture): an unreadable flag is not an excuse to spend a truth."""
    truth, = _seed(tmp_path, 1)

    def angry():
        raise RuntimeError("settings gone")

    verdict, calls = await _release(_impulse(disclosure_id=truth.id), tmp_path,
                                    flag=angry)
    assert verdict["disposition"] == "sent"
    assert "disclosure_shared" not in verdict and "disclosure_unmatched" not in verdict
    assert "disclosure_id" not in calls["outbound"]["extra"]
    assert _row(tmp_path, truth.id).said_at is None


@pytest.mark.asyncio
async def test_a_broken_resolve_records_her_token_and_still_sends(tmp_path):
    """A raising store is a degraded organ, not a dropped message."""
    truth, = _seed(tmp_path, 1)
    with patch.object(ds, "resolve_disclosure_id", _boom()):
        verdict, calls = await _release(_impulse(disclosure_id=truth.id), tmp_path)
    assert verdict["disposition"] == "sent"
    assert verdict.get("disclosure_unmatched") is True
    assert "disclosure_id" not in calls["outbound"]["extra"]
    assert _sent_lines(tmp_path)[0]["disclosure_id"] == truth.id   # her raw token
    assert _row(tmp_path, truth.id).said_at is None
