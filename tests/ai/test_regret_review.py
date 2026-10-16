"""Step 25 · Regret — the nightly review lives and ends it (spec §3).

The review's lifecycle law for the backward kind: a regret unfed for about
``regret_half_life_days`` fades honestly; a resolution names what it taught;
repaired stays repaired — never re-opened, never re-litigated; the same
regret updated night after night without new information is rumination and
should fade. The law is a conditional block in prompts/stake_review.txt:
zero bytes when companion.regret is off (today's prompt, byte for byte).

The code half of "repaired stays repaired" already exists in the ledger —
update/resolve/fade address OPEN rows only — and is pinned here for the
regret kind. DBs under tmp_path; flags pinned in-body (3352e3d)."""
from __future__ import annotations

import shutil
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest
import pytest_asyncio

from ai import stake_ledger
from ai import stake_review
from core.database import PalliBrain


@pytest.fixture
def local_tmp_dir(tmp_path: Path):
    d = tmp_path / f"regret_review_{uuid.uuid4().hex}"
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


@pytest.fixture(autouse=True)
def _review_on(monkeypatch):
    # pinned ON here (the root conftest pins it OFF for everyone else)
    monkeypatch.setattr(stake_review, "review_enabled", lambda: True)


# ── the block ───────────────────────────────────────────────────────────

def test_flag_off_review_prompt_is_todays_bytes():
    with patch.object(stake_review, "_regret_enabled", lambda: False):
        assert stake_review._regret_block() == ""


def test_flag_on_review_prompt_carries_the_lifecycle_law():
    with patch.object(stake_review, "_regret_enabled", lambda: True), \
         patch.object(stake_review, "_regret_half_life_days", lambda: 14):
        block = stake_review._regret_block()
    assert "14" in block
    assert "let that one go" in block or "fades" in block.lower()
    assert "what it taught" in block.lower()
    assert "never re-open" in block.lower() or "stays repaired" in block.lower()
    assert "rumination" in block.lower()
    assert block.startswith("- ") and block.endswith("\n")   # one rule line, newline-terminated


def test_half_life_reads_the_knob_and_floors_at_one(monkeypatch):
    from types import SimpleNamespace

    monkeypatch.setattr(stake_review, "_companion", lambda: SimpleNamespace(regret_half_life_days=21))
    assert stake_review._regret_half_life_days() == 21
    monkeypatch.setattr(stake_review, "_companion", lambda: SimpleNamespace(regret_half_life_days=0))
    assert stake_review._regret_half_life_days() == 1
    monkeypatch.setattr(stake_review, "_companion", lambda: None)
    assert stake_review._regret_half_life_days() == 14
    with patch.object(stake_review, "_regret_enabled", lambda: True), \
         patch.object(stake_review, "_regret_half_life_days", lambda: 21):
        assert "21 days" in stake_review._regret_block()


def test_template_carries_the_slot_line_for_zero_byte_collapse():
    text = Path("prompts/stake_review.txt").read_text(encoding="utf-8")
    assert text.count("<<REGRET_LIFECYCLE_BLOCK>>") == 1
    # the slot owns a whole line, right after the UPDATE rule; replacing the
    # LINE (newline included) with "" leaves today's bytes
    # (organ 4.13 added its own slot line directly below; both collapse)
    assert ("is not done.\n<<REGRET_LIFECYCLE_BLOCK>>\n<<VIEW_LIFECYCLE_BLOCK>>\n"
            "- You cannot OPEN anything here") in text


# ── the assembled prompt, through the real review path ──────────────────

async def _assembled_prompt(brain, *, flag_on: bool) -> str:
    await stake_ledger.apply_ops(
        [{"op": "open", "kind": "regret", "stake": "I called the lake page finished when it wasn't", "horizon": None}],
        source_channel_id="t", brain_client=brain,
    )
    seen = {}

    async def capture(prompt: str) -> str:
        seen["prompt"] = prompt
        return '{"ops": []}'

    with patch.object(stake_review, "_regret_enabled", lambda: flag_on), \
         patch.object(stake_review, "_regret_half_life_days", lambda: 14), \
         patch.object(stake_review, "_knob", lambda name, default: default):
        result = await stake_review.run_nightly_stake_review(
            owner_id=1, day="2026-08-23", day_reflection="a quiet day",
            llm_call=capture, brain_client=brain, now=1_700_000_000.0,
        )
    assert "error" not in result
    return seen["prompt"]


@pytest.mark.asyncio
async def test_flag_off_assembly_leaves_no_slot_and_no_orphan_line(brain):
    prompt = await _assembled_prompt(brain, flag_on=False)
    assert "<<REGRET_LIFECYCLE_BLOCK>>" not in prompt
    assert "is not done.\n- You cannot OPEN anything here" in prompt      # today's bytes
    # the law block is absent; the seeded ROW still renders its kind label —
    # data is data, the flag gates the law
    assert "Regrets (the backward kind" not in prompt and "what it taught" not in prompt
    assert "· regret · open today" in prompt


@pytest.mark.asyncio
async def test_flag_on_assembly_puts_the_law_after_the_update_rule(brain):
    prompt = await _assembled_prompt(brain, flag_on=True)
    assert "<<REGRET_LIFECYCLE_BLOCK>>" not in prompt
    assert "is not done.\n- Regrets (" in prompt
    assert "what it taught" in prompt
    assert "\n- You cannot OPEN anything here" in prompt                     # the open ban stands
    assert "· regret · open today" in prompt                                 # the stake renders like any other


# ── repaired stays repaired: the code rail, pinned for the kind ─────────

@pytest.mark.asyncio
async def test_a_closed_regret_can_never_be_reopened_or_relitigated(brain):
    await stake_ledger.apply_ops(
        [{"op": "open", "kind": "regret", "stake": "I buried him in settings", "horizon": None}],
        source_channel_id="t", brain_client=brain,
    )
    rid = (await stake_ledger.open_stakes(limit=5, brain_client=brain))[0].id
    closed = await stake_ledger.apply_ops(
        [{"op": "resolve", "id": rid, "resolution": "it taught me to ask before I arrange"}],
        source_channel_id="night_review", brain_client=brain,
    )
    assert closed["closed"] == [rid]
    # every later op against that id is rejected — update, resolve, fade
    later = await stake_ledger.apply_ops(
        [{"op": "update", "id": rid, "stake": "I STILL buried him in settings"},
         {"op": "resolve", "id": rid, "resolution": "again"},
         {"op": "fade", "id": rid, "resolution": "again"}],
        source_channel_id="night_review", brain_client=brain,
    )
    assert later == {"applied": 0, "rejected": 3, "opened": [], "closed": [], "dormant": []}
    rows = await stake_ledger.recent_stakes(brain_client=brain)
    row = next(r for r in rows if r.id == rid)
    assert row.status == "resolved"
    assert row.resolution == "it taught me to ask before I arrange"
    assert not await stake_ledger.open_stakes(limit=5, brain_client=brain)
