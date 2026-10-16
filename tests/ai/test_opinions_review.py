"""Organ 4.13 — the review: view law armed, views never die of age.

Task 5 also carries five riders from the reviews of tasks 1-4:
R1 the review's resolve/fade can never reach a view (settle is the only
terminal) · R2 revise only for reasons · R3 the pressure line counts
NON-view stakes only · R4 both prompts say views hold no slot · R5 unmute
is reachable in the appraiser's grammar.

Harness = tests/ai/test_opinions_ledger.py's (the house pattern).
"""
from __future__ import annotations

import shutil
import uuid
from pathlib import Path

import pytest
import pytest_asyncio

from ai import stake_appraiser, stake_ledger, stake_review
from ai.prompt_loader import prompt_loader
from core.database import PalliBrain


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


# ── the brief: the age exemption, settle/promote, the block ─────────────


@pytest.mark.asyncio
async def test_backstop_never_fades_a_view(brain, monkeypatch):
    monkeypatch.setattr(stake_review, "review_enabled", lambda: True)
    monkeypatch.setattr(stake_review, "_opinions_enabled", lambda: True)
    await stake_ledger.apply_ops(
        [{"op": "open", "kind": "view", "stake": "old but alive",
          "evidence": "the 06-02 thread"},
         {"op": "open", "kind": "worry", "stake": "an old worry"}],
        brain_client=brain,
    )
    conn = await brain.get_connection()
    try:  # age both far past the 45d backstop
        await conn.execute("UPDATE stakes SET updated_at = 1.0, created_at = 1.0")
    finally:
        await conn.close()

    async def quiet_llm(prompt):
        return '{"ops": []}'

    out = await stake_review.run_nightly_stake_review(
        owner_id=1, day="2026-08-31", day_reflection="",
        llm_call=quiet_llm, brain_client=brain,
    )
    assert out["backstop_faded"] == 1  # the worry, never the view
    kinds = {s.kind for s in await stake_ledger.open_stakes(limit=8, brain_client=brain)}
    assert kinds == {"view"}


@pytest.mark.asyncio
async def test_review_can_settle_and_promote(brain, monkeypatch):
    monkeypatch.setattr(stake_review, "review_enabled", lambda: True)
    monkeypatch.setattr(stake_ledger, "_views_max_active", lambda: 1)
    a = await stake_ledger.apply_ops(
        [{"op": "open", "kind": "view", "stake": "first", "evidence": "e1"}],
        brain_client=brain)
    b = await stake_ledger.apply_ops(
        [{"op": "open", "kind": "view", "stake": "second", "evidence": "e2"}],
        brain_client=brain)

    async def her_review(prompt):
        return ('{"ops": [{"op": "settle", "id": %d, "resolution": "part of me now"},'
                '{"op": "promote", "id": %d}]}' % (a["opened"][0], b["dormant"][0]))

    out = await stake_review.run_nightly_stake_review(
        owner_id=1, day="2026-08-31", day_reflection="",
        llm_call=her_review, brain_client=brain,
    )
    assert out["applied"] == 2
    rows = await stake_ledger.open_stakes(limit=8, brain_client=brain)
    assert [r.stake for r in rows] == ["second"]


def test_view_block_bytes(monkeypatch):
    monkeypatch.setattr(stake_review, "_opinions_enabled", lambda: False)
    assert stake_review._view_block() == ""
    monkeypatch.setattr(stake_review, "_opinions_enabled", lambda: True)
    block = stake_review._view_block()
    assert "settle" in block and "promote" in block and "never let a view fade for age" in block


def test_the_slot_line_collapses_to_todays_bytes():
    """The slot owns a whole LINE; replaced newline-included, an off organ
    leaves the review prompt exactly as it is today (the regret law)."""
    text = Path("prompts/stake_review.txt").read_text(encoding="utf-8")
    assert text.count("<<VIEW_LIFECYCLE_BLOCK>>") == 1
    assert ("is not done.\n<<REGRET_LIFECYCLE_BLOCK>>\n<<VIEW_LIFECYCLE_BLOCK>>\n"
            "- You cannot OPEN anything here") in text
    # ...and the waiting-views slot, same law, directly under the holdings
    assert text.count("<<DORMANT_VIEWS>>") == 1
    assert "<<OPEN_STAKES>>\n<<DORMANT_VIEWS>>\n\nTODAY'S DATE:" in text


@pytest.mark.asyncio
async def test_assembly_arms_the_law_on_and_leaves_no_orphan_off(brain, monkeypatch):
    monkeypatch.setattr(stake_review, "review_enabled", lambda: True)
    monkeypatch.setattr(stake_review, "_regret_enabled", lambda: False)
    await stake_ledger.apply_ops(
        [{"op": "open", "kind": "view", "stake": "the 1978 manuals are the honest ones",
          "evidence": "his 03-14 scan"}],
        brain_client=brain,
    )
    seen = {}

    async def capture(prompt):
        seen["prompt"] = prompt
        return '{"ops": []}'

    for flag_on in (False, True):
        monkeypatch.setattr(stake_review, "_opinions_enabled", lambda o=flag_on: o)
        await stake_review.run_nightly_stake_review(
            owner_id=1, day="2026-08-31", day_reflection="",
            llm_call=capture, brain_client=brain, now=1_700_000_000.0,
        )
        prompt = seen["prompt"]
        assert "<<VIEW_LIFECYCLE_BLOCK>>" not in prompt
        if flag_on:
            assert "is not done.\n- Views (your positions" in prompt
            assert "never let a view fade for age" in prompt
        else:
            assert "is not done.\n- You cannot OPEN anything here" in prompt
            assert "Views (your positions" not in prompt
        # data is data: the row renders its kind label either way
        assert "· view · open" in prompt


def test_the_block_never_tells_her_to_settle_a_dormant_view(monkeypatch):
    """Dormant is promote-first by design: "settle" speaks of what is on the
    board, and "promote" is the only verb aimed at a waiting view."""
    monkeypatch.setattr(stake_review, "_opinions_enabled", lambda: True)
    block = stake_review._view_block()
    assert "waiting view has earned it" in block
    assert "promote" in block.split("waiting view")[1]
    assert "dormant" not in block.lower()
    assert block.startswith("- ") and block.endswith("\n")   # one rule line


# ── the waiting views reach the night review: promote has an id to name ──


@pytest.mark.asyncio
async def test_dormant_views_is_empty_on_a_fresh_brain(brain):
    assert await stake_ledger.dormant_views(brain_client=brain) == []


@pytest.mark.asyncio
async def test_dormant_views_returns_the_waiting_ones_oldest_first(brain, monkeypatch):
    monkeypatch.setattr(stake_ledger, "_views_max_active", lambda: 1)
    for text in ("on the board", "waiting first", "waiting second"):
        await stake_ledger.apply_ops(
            [{"op": "open", "kind": "view", "stake": text, "evidence": "e"}],
            brain_client=brain,
        )
    waiting = await stake_ledger.dormant_views(brain_client=brain)
    assert [v.stake for v in waiting] == ["waiting first", "waiting second"]
    assert {v.status for v in waiting} == {"dormant"}
    assert {v.kind for v in waiting} == {"view"}
    # a hope filed dormant is impossible, but the guard is explicit anyway
    assert all(v.kind == "view" for v in waiting)


@pytest.mark.asyncio
async def test_the_review_shows_her_the_waiting_views_with_their_ids(brain, monkeypatch):
    """Without this, the block's `{"op": "promote", "id": n}` is unusable:
    open_stakes() renders only status='open', so no dormant view ever showed
    an id she could name."""
    monkeypatch.setattr(stake_review, "review_enabled", lambda: True)
    monkeypatch.setattr(stake_review, "_opinions_enabled", lambda: True)
    monkeypatch.setattr(stake_ledger, "_views_max_active", lambda: 1)
    await stake_ledger.apply_ops(
        [{"op": "open", "kind": "view", "stake": "on the board", "evidence": "e"}],
        brain_client=brain)
    parked = await stake_ledger.apply_ops(
        [{"op": "open", "kind": "view", "stake": "the one still waiting", "evidence": "e2"}],
        brain_client=brain)
    waiting_id = parked["dormant"][0]
    seen = {}

    async def capture(prompt):
        seen["prompt"] = prompt
        return '{"ops": []}'

    await stake_review.run_nightly_stake_review(
        owner_id=1, day="2026-08-31", day_reflection="",
        llm_call=capture, brain_client=brain, now=1_700_000_000.0,
    )
    prompt = seen["prompt"]
    assert "YOUR WAITING VIEWS" in prompt
    assert f"- id {waiting_id} · waiting " in prompt
    assert "the one still waiting" in prompt
    assert "promote one ONLY when a slot is free" in prompt
    # the waiting block sits under the holdings, above the date
    assert prompt.index("YOUR WAITING VIEWS") < prompt.index("TODAY'S DATE:")
    assert prompt.index("on the board") < prompt.index("YOUR WAITING VIEWS")


@pytest.mark.asyncio
async def test_the_waiting_block_collapses_to_todays_bytes(brain, monkeypatch):
    """Off, or on with nothing waiting: the newline-included replace leaves
    the holdings running straight into the date — no orphan blank line."""
    monkeypatch.setattr(stake_review, "review_enabled", lambda: True)
    monkeypatch.setattr(stake_ledger, "_views_max_active", lambda: 1)
    await stake_ledger.apply_ops(
        [{"op": "open", "kind": "view", "stake": "on the board", "evidence": "e"}],
        brain_client=brain)
    await stake_ledger.apply_ops(
        [{"op": "open", "kind": "view", "stake": "the one still waiting", "evidence": "e2"}],
        brain_client=brain)
    seen = {}

    async def capture(prompt):
        seen["prompt"] = prompt
        return '{"ops": []}'

    # (a) organ off — the dormant row exists but is never read
    monkeypatch.setattr(stake_review, "_opinions_enabled", lambda: False)
    await stake_review.run_nightly_stake_review(
        owner_id=1, day="2026-08-31", day_reflection="",
        llm_call=capture, brain_client=brain, now=1_700_000_000.0,
    )
    off = seen["prompt"]
    assert "<<DORMANT_VIEWS>>" not in off
    assert "YOUR WAITING VIEWS" not in off and "the one still waiting" not in off
    assert "on the board\n\nTODAY'S DATE:" in off        # today's bytes exactly

    # (b) organ on, nothing waiting — same collapse
    monkeypatch.setattr(stake_review, "_opinions_enabled", lambda: True)
    monkeypatch.setattr(stake_ledger, "dormant_views", _no_waiting_views)
    await stake_review.run_nightly_stake_review(
        owner_id=1, day="2026-08-31", day_reflection="",
        llm_call=capture, brain_client=brain, now=1_700_000_000.0,
    )
    empty = seen["prompt"]
    assert "<<DORMANT_VIEWS>>" not in empty and "YOUR WAITING VIEWS" not in empty
    assert "on the board\n\nTODAY'S DATE:" in empty


async def _no_waiting_views(**kwargs):
    return []


async def _one_waiting_view_and_an_empty_board(brain, monkeypatch) -> int:
    """A real board that emptied under her: cap 1, two views filed (the
    second parks dormant), then she settles the one on the board. Nothing
    open, one view waiting — the state that used to be a dead end."""
    monkeypatch.setattr(stake_ledger, "_views_max_active", lambda: 1)
    on_board = await stake_ledger.apply_ops(
        [{"op": "open", "kind": "view", "stake": "the one she settled", "evidence": "e"}],
        brain_client=brain)
    parked = await stake_ledger.apply_ops(
        [{"op": "open", "kind": "view", "stake": "the one still waiting", "evidence": "e2"}],
        brain_client=brain)
    await stake_ledger.apply_ops(
        [{"op": "settle", "id": on_board["opened"][0], "resolution": "part of me now"}],
        brain_client=brain)
    assert await stake_ledger.open_stakes(limit=8, brain_client=brain) == []
    return parked["dormant"][0]


@pytest.mark.asyncio
async def test_an_empty_board_still_reaches_her_waiting_views(brain, monkeypatch):
    """The `nothing_held` early return used to run BEFORE the dormant fetch,
    so an empty board made promote's only surface unreachable — permanently,
    until some unrelated stake happened to open."""
    monkeypatch.setattr(stake_review, "review_enabled", lambda: True)
    monkeypatch.setattr(stake_review, "_opinions_enabled", lambda: True)
    waiting_id = await _one_waiting_view_and_an_empty_board(brain, monkeypatch)
    seen = {}

    async def capture(prompt):
        seen["prompt"] = prompt
        return '{"ops": []}'

    out = await stake_review.run_nightly_stake_review(
        owner_id=1, day="2026-09-01", day_reflection="",
        llm_call=capture, brain_client=brain, now=1_700_000_000.0,
    )
    assert out.get("skipped") is None            # the pass RAN
    prompt = seen["prompt"]
    assert "YOUR WAITING VIEWS" in prompt
    assert f"- id {waiting_id} · waiting " in prompt
    # the rest of the assembly tolerates an empty board
    assert "YOUR HOPES AND WORRIES RIGHT NOW (0 of " in prompt
    assert "(nothing)" in prompt


@pytest.mark.asyncio
async def test_she_can_promote_off_an_empty_board(brain, monkeypatch):
    monkeypatch.setattr(stake_review, "review_enabled", lambda: True)
    monkeypatch.setattr(stake_review, "_opinions_enabled", lambda: True)
    waiting_id = await _one_waiting_view_and_an_empty_board(brain, monkeypatch)

    async def her_review(prompt):
        return '{"ops": [{"op": "promote", "id": %d}]}' % waiting_id

    out = await stake_review.run_nightly_stake_review(
        owner_id=1, day="2026-09-01", day_reflection="",
        llm_call=her_review, brain_client=brain, now=1_700_000_000.0,
    )
    assert out["applied"] == 1
    rows = await stake_ledger.open_stakes(limit=8, brain_client=brain)
    assert [(r.id, r.stake) for r in rows] == [(waiting_id, "the one still waiting")]
    assert await stake_ledger.dormant_views(brain_client=brain) == []


@pytest.mark.asyncio
async def test_a_truly_empty_ledger_still_skips_the_call(brain, monkeypatch):
    """Both empty is still `nothing_held` — the skip narrowed, not dropped."""
    monkeypatch.setattr(stake_review, "review_enabled", lambda: True)
    monkeypatch.setattr(stake_review, "_opinions_enabled", lambda: True)
    called = []

    async def spy(prompt):
        called.append(prompt)
        return '{"ops": []}'

    out = await stake_review.run_nightly_stake_review(
        owner_id=1, day="2026-09-01", day_reflection="",
        llm_call=spy, brain_client=brain, now=1_700_000_000.0,
    )
    assert out == {"skipped": "nothing_held"} and called == []


def test_the_waiting_block_bytes(monkeypatch):
    monkeypatch.setattr(stake_review, "_opinions_enabled", lambda: True)
    assert stake_review._dormant_views_block([], now=1_700_000_000.0) == ""
    parked = stake_ledger.Stake(
        id=9, kind="view", stake="the one still waiting", status="dormant",
        resolution="", horizon_at=None, source_channel_id="",
        last_surfaced_at=None, created_at=1_700_000_000.0 - 3 * 86400.0,
        updated_at=1_700_000_000.0, resolved_at=None, evidence="e",
    )
    block = stake_review._dormant_views_block([parked], now=1_700_000_000.0)
    assert block.startswith("YOUR WAITING VIEWS (formed at a full slate; promote one ONLY "
                            "when a slot is free and it has earned it):\n")
    assert "- id 9 · waiting 3 days: the one still waiting\n" in block
    assert block.endswith("\n") and not block.endswith("\n\n")
    monkeypatch.setattr(stake_review, "_opinions_enabled", lambda: False)
    assert stake_review._dormant_views_block([parked], now=1_700_000_000.0) == ""


# ── R1: the review's resolve/fade can never reach a view ────────────────


@pytest.mark.asyncio
async def test_resolve_and_fade_never_reach_a_view(brain):
    """Spec §3/§6: only reasons end a view — settle is its terminal. resolve
    and fade are for the forward kinds and must bounce off a view."""
    opened = await stake_ledger.apply_ops(
        [{"op": "open", "kind": "view", "stake": "a position", "evidence": "e"},
         {"op": "open", "kind": "hope", "stake": "a hope"}],
        brain_client=brain,
    )
    view_id, hope_id = opened["opened"]

    bounced = await stake_ledger.apply_ops(
        [{"op": "resolve", "id": view_id, "resolution": "no"},
         {"op": "fade", "id": view_id, "resolution": "also no"}],
        source_channel_id="night_review", brain_client=brain,
    )
    assert bounced["applied"] == 0 and bounced["rejected"] == 2
    assert bounced["closed"] == []
    still = await stake_ledger.open_stakes(limit=8, brain_client=brain)
    assert {s.id for s in still} == {view_id, hope_id}

    # ...and the same ops still work on a hope
    worked = await stake_ledger.apply_ops(
        [{"op": "resolve", "id": hope_id, "resolution": "it landed"}],
        source_channel_id="night_review", brain_client=brain,
    )
    assert worked["applied"] == 1 and worked["closed"] == [hope_id]


@pytest.mark.asyncio
async def test_fade_still_works_on_a_hope_after_a_view_bounces(brain):
    opened = await stake_ledger.apply_ops(
        [{"op": "open", "kind": "hope", "stake": "a hope"}], brain_client=brain,
    )
    faded = await stake_ledger.apply_ops(
        [{"op": "fade", "id": opened["opened"][0], "resolution": "it went quiet"}],
        brain_client=brain,
    )
    assert faded["applied"] == 1
    assert not await stake_ledger.open_stakes(limit=8, brain_client=brain)


# ── R2: revise only for reasons ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_view_revision_without_a_reason_is_rejected(brain):
    """Spec §3: a view's wording never moves because he repeated himself —
    new evidence or a stated reason, or the revision bounces."""
    opened = await stake_ledger.apply_ops(
        [{"op": "open", "kind": "view", "stake": "the first wording",
          "evidence": "his 03-14 scan"}],
        brain_client=brain,
    )
    vid = opened["opened"][0]

    bare = await stake_ledger.apply_ops(
        [{"op": "update", "id": vid, "stake": "a bare rewording"}],
        brain_client=brain,
    )
    assert bare["applied"] == 0 and bare["rejected"] == 1
    row = (await stake_ledger.open_stakes(limit=8, brain_client=brain))[0]
    assert row.stake == "the first wording"


@pytest.mark.asyncio
async def test_reasoned_evidence_only_and_horizon_only_revisions_all_pass(brain):
    opened = await stake_ledger.apply_ops(
        [{"op": "open", "kind": "view", "stake": "the first wording", "evidence": "e0"}],
        brain_client=brain,
    )
    vid = opened["opened"][0]

    with_reason = await stake_ledger.apply_ops(
        [{"op": "update", "id": vid, "stake": "the moved wording",
          "reason": "his 08-30 counter-example"}],
        brain_client=brain,
    )
    assert with_reason["applied"] == 1
    evidence_only = await stake_ledger.apply_ops(
        [{"op": "update", "id": vid, "evidence": "and the torque table"}],
        brain_client=brain,
    )
    assert evidence_only["applied"] == 1
    horizon_only = await stake_ledger.apply_ops(
        [{"op": "update", "id": vid, "horizon_at": None}], brain_client=brain,
    )
    assert horizon_only["applied"] == 1
    row = (await stake_ledger.open_stakes(limit=8, brain_client=brain))[0]
    assert row.stake == "the moved wording"


@pytest.mark.asyncio
async def test_non_view_updates_are_untouched_by_the_reasons_rail(brain):
    opened = await stake_ledger.apply_ops(
        [{"op": "open", "kind": "hope", "stake": "the survey comes back clean"}],
        brain_client=brain,
    )
    moved = await stake_ledger.apply_ops(
        [{"op": "update", "id": opened["opened"][0], "stake": "the survey slipped a week"}],
        brain_client=brain,
    )
    assert moved["applied"] == 1 and moved["rejected"] == 0
    row = (await stake_ledger.open_stakes(limit=8, brain_client=brain))[0]
    assert row.stake == "the survey slipped a week"
    assert row.evidence == ""      # evidence is view-only at the store


# ── R3/R4: the pressure line tells the truth ────────────────────────────


@pytest.mark.asyncio
async def test_the_reviews_open_count_counts_non_view_stakes_only(brain, monkeypatch):
    """Views live outside the shared cap (task 2) — counting them would tell
    her the ledger is fuller than it is, mirroring the appraiser's fix."""
    monkeypatch.setattr(stake_review, "review_enabled", lambda: True)
    monkeypatch.setattr(stake_review, "_opinions_enabled", lambda: True)
    await stake_ledger.apply_ops(
        [{"op": "open", "kind": "view", "stake": "a position", "evidence": "e"},
         {"op": "open", "kind": "hope", "stake": "a hope"}],
        brain_client=brain,
    )
    seen = {}

    async def capture(prompt):
        seen["prompt"] = prompt
        return '{"ops": []}'

    await stake_review.run_nightly_stake_review(
        owner_id=1, day="2026-08-31", day_reflection="",
        llm_call=capture, brain_client=brain, now=1_700_000_000.0,
    )
    assert "YOUR HOPES AND WORRIES RIGHT NOW (1 of " in seen["prompt"]
    assert "· view · open" in seen["prompt"]      # still rendered, just weightless


def test_both_prompts_say_a_view_holds_no_slot():
    """R4: unconditional template text — the pressure line must explain the
    weightlessness wherever it is read."""
    review = Path("prompts/stake_review.txt").read_text(encoding="utf-8")
    assert ("YOUR HOPES AND WORRIES RIGHT NOW (<<OPEN_COUNT>> of <<MAX_OPEN>> — each with how "
            "long since it last moved; your views hold no slot):") in review
    appraisal = Path("prompts/stake_appraisal.txt").read_text(encoding="utf-8")
    assert ("PALLI'S CURRENTLY OPEN STAKES ({open_count} of {max_open} slots used; "
            "views hold no slot):") in appraisal


# ── R5: unmute is reachable ─────────────────────────────────────────────


def _appraisal_prompt(monkeypatch, *, opinions: bool) -> str:
    monkeypatch.setattr(stake_appraiser, "_regret_enabled", lambda: False)
    monkeypatch.setattr(stake_appraiser, "_opinions_enabled", lambda: opinions)
    template = prompt_loader.load_prompt("stake_appraisal")
    return template.format(
        care_spine="c", user_message="u", palli_reply="p",
        recent_conversation="r", open_stakes="(none yet)",
        open_count=0, max_open=12, current_date="Monday, 2026-08-31",
        **{**stake_appraiser._regret_prompt_kwargs(),
           **stake_appraiser._view_prompt_kwargs()},
    )


def test_unmute_is_reachable_when_opinions_are_on(monkeypatch):
    """Task 4's review: mute had no way back. A dropped view he invites back
    must have a verb, or "held quietly" quietly becomes "deleted"."""
    on = _appraisal_prompt(monkeypatch, opinions=True)
    assert '    {"op": "unmute", "id": 4},\n' in on
    assert "invite a dropped view back" in on and "emit unmute" in on
    assert "{{" not in on and "}}" not in on      # the hint is a format VALUE


def test_unmute_is_absent_when_opinions_are_off(monkeypatch):
    off = _appraisal_prompt(monkeypatch, opinions=False)
    assert "unmute" not in off
    assert "inside a worry's prose.)\n- FIRST PERSON" in off   # the NEVER line's end
