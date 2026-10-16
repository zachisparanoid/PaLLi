"""Stake appraiser: ops parsing, horizon dates, LLM-output resilience, and
end-to-end apply against a tmp-path ledger with a mocked LLM."""
from __future__ import annotations

import shutil
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path

import pytest
import pytest_asyncio

from ai import stake_appraiser, stake_ledger
from core.database import PalliBrain


@pytest.fixture
def local_tmp_dir(tmp_path: Path):
    directory = tmp_path / f"appraiser_test_{uuid.uuid4().hex}"
    directory.mkdir(parents=True)
    try:
        yield directory
    finally:
        shutil.rmtree(directory, ignore_errors=True)


@pytest_asyncio.fixture
async def brain(local_tmp_dir: Path):
    db = PalliBrain(str(local_tmp_dir / "appraiser.sqlite3"))
    await db.initialize()
    try:
        yield db
    finally:
        await db.close()


# ── _parse_ops ─────────────────────────────────────────────────────────


def test_parse_ops_clean_json():
    raw = '{"ops": [{"op": "open", "kind": "hope", "stake": "I am hoping.", "horizon": null}]}'
    ops = stake_appraiser._parse_ops(raw)
    assert len(ops) == 1
    assert ops[0]["op"] == "open"
    assert ops[0]["horizon_at"] is None  # renamed + parsed


def test_parse_ops_tolerates_fences_and_prose():
    raw = 'Sure! Here is the ledger move:\n```json\n{"ops": []}\n```\nDone.'
    assert stake_appraiser._parse_ops(raw) == []


def test_parse_ops_unparseable_returns_none():
    assert stake_appraiser._parse_ops("no json here") is None
    assert stake_appraiser._parse_ops('{"ops": "not a list"}') is None
    assert stake_appraiser._parse_ops('{"no_ops_key": []}') is None
    assert stake_appraiser._parse_ops("") is None


def test_parse_ops_drops_non_dict_entries(caplog):
    raw = '{"ops": [{"op": "open", "kind": "hope", "stake": "x"}, "junk", 42]}'
    with caplog.at_level("WARNING"):
        ops = stake_appraiser._parse_ops(raw)
    assert len(ops) == 1


def test_parse_horizon_date_means_end_of_day():
    tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
    ts = stake_appraiser._parse_horizon(tomorrow)
    dt = datetime.fromtimestamp(ts)
    assert dt.strftime("%Y-%m-%d") == tomorrow
    assert (dt.hour, dt.minute) == (23, 59)


def test_parse_horizon_invalid_and_null(caplog):
    assert stake_appraiser._parse_horizon(None) is None
    assert stake_appraiser._parse_horizon("null") is None
    with caplog.at_level("WARNING"):
        assert stake_appraiser._parse_horizon("next Thursday-ish") is None
    assert any("bad horizon" in r.message for r in caplog.records)


# ── appraise_exchange end-to-end (mock LLM) ───────────────────────────


def _mock_llm(monkeypatch, response: str, calls: list):
    class _FakeLLM:
        async def chat_completion(self, **kwargs):
            calls.append(kwargs)
            return response

    import ai.llm_client as llm_client

    monkeypatch.setattr(llm_client, "llm", _FakeLLM())
    monkeypatch.setattr(llm_client, "get_task_model", lambda task: f"fake::{task}")


@pytest.mark.asyncio
async def test_appraise_exchange_opens_and_resolves(monkeypatch, brain):
    tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
    calls: list = []
    _mock_llm(
        monkeypatch,
        '{"ops": [{"op": "open", "kind": "hope", '
        '"stake": "I\'m hoping the survey comes back clean — he wants this bad.", '
        f'"horizon": "{tomorrow}"}}]}}',
        calls,
    )
    summary = await stake_appraiser.appraise_exchange(
        user_message="survey got scheduled for tomorrow!",
        palli_reply="That's huge — fingers crossed it comes back clean.",
        channel_id="chan-9",
        brain_client=brain,
    )
    assert summary["applied"] == 1
    assert calls[0]["log_context"] == "stake_appraisal"
    assert calls[0]["model"] == "fake::appraisal"  # the new routing lane

    stakes = await stake_ledger.open_stakes(brain_client=brain)
    assert len(stakes) == 1
    assert stakes[0].stake.startswith("I'm hoping")
    assert stakes[0].source_channel_id == "chan-9"
    assert stakes[0].horizon_at is not None

    # Second exchange resolves it with felt prose.
    sid = stakes[0].id
    calls.clear()
    _mock_llm(
        monkeypatch,
        f'{{"ops": [{{"op": "resolve", "id": {sid}, '
        f'"resolution": "Clean! I\'ve been holding my breath about that one."}}]}}',
        calls,
    )
    summary = await stake_appraiser.appraise_exchange(
        user_message="survey came back CLEAN baby",
        palli_reply="LET'S GO. I've been quietly holding my breath about that.",
        brain_client=brain,
    )
    assert summary["applied"] == 1
    assert await stake_ledger.open_stakes(brain_client=brain) == []
    # The prompt showed the appraiser its open ledger with the id.
    assert f"[id {sid}]" in calls[0]["messages"][0]["content"]


@pytest.mark.asyncio
async def test_appraise_exchange_no_movement_is_normal(monkeypatch, brain):
    calls: list = []
    _mock_llm(monkeypatch, '{"ops": []}', calls)
    summary = await stake_appraiser.appraise_exchange(
        user_message="what's 2+2?",
        palli_reply="4 :)",
        brain_client=brain,
    )
    # same shape as apply_ops' summary, dormant included (organ 4.13)
    assert summary == {"applied": 0, "rejected": 0, "opened": [], "closed": [],
                       "dormant": []}


@pytest.mark.asyncio
async def test_appraise_exchange_unparseable_drops_whole_appraisal(monkeypatch, brain, caplog):
    calls: list = []
    _mock_llm(monkeypatch, "I feel like opening seventeen stakes about everything!", calls)
    with caplog.at_level("WARNING"):
        summary = await stake_appraiser.appraise_exchange(
            user_message="hey", palli_reply="hey yourself", brain_client=brain
        )
    assert summary == {"error": "unparseable"}
    assert (await stake_ledger.counts(brain_client=brain)) == {}


@pytest.mark.asyncio
async def test_appraise_exchange_llm_failure_never_raises(monkeypatch, brain, caplog):
    class _ExplodingLLM:
        async def chat_completion(self, **kwargs):
            raise RuntimeError("provider down")

    import ai.llm_client as llm_client

    monkeypatch.setattr(llm_client, "llm", _ExplodingLLM())
    monkeypatch.setattr(llm_client, "get_task_model", lambda task: "fake::appraisal")
    with caplog.at_level("WARNING"):
        summary = await stake_appraiser.appraise_exchange(
            user_message="hi", palli_reply="hello", brain_client=brain
        )
    assert "error" in summary


@pytest.mark.asyncio
async def test_appraise_exchange_skips_empty_exchange(brain):
    assert (
        await stake_appraiser.appraise_exchange(
            user_message="", palli_reply="x", brain_client=brain
        )
    )["skipped"] == "empty exchange"


def test_care_spine_falls_back_to_template_then_generic(tmp_path, monkeypatch):
    """Fix wave B (review #1): a fresh checkout has no live spine file — the
    appraiser must fall back to the SHIPPED template seed, and only when even
    that is gone to the generic stub. The live file always wins when present."""
    from core.config import config

    palli = tmp_path / "palli"
    palli.mkdir(parents=True)
    monkeypatch.setattr(config, "IDENTITY_ROOT_PATH", str(tmp_path), raising=False)

    # neither file ⇒ the generic stub (the last resort, never the norm)
    assert stake_appraiser._load_care_spine() == stake_appraiser._CARE_SPINE_FALLBACK

    # template only (the fresh-checkout shape) ⇒ the shipped seed body
    (palli / "WHAT_I_CARE_ABOUT.template.md").write_text(
        "---\ntitle: seed\n---\nSeed care line one.\nSeed care line two.", encoding="utf-8"
    )
    assert stake_appraiser._load_care_spine() == "Seed care line one.\nSeed care line two."

    # live file present ⇒ it wins over the template
    (palli / "WHAT_I_CARE_ABOUT.md").write_text("Her own line.", encoding="utf-8")
    assert stake_appraiser._load_care_spine() == "Her own line."


def test_care_spine_loads_and_strips_frontmatter():
    """Review finding: a naive split('---') broke on punctuation inside the
    header and leaked meta-text into every appraisal. The spine must START at
    the body — zero frontmatter residue."""
    spine = stake_appraiser._load_care_spine()
    assert spine
    assert spine.startswith("I care about")  # body starts clean
    assert "PLACEHOLDER" not in spine
    assert "injected verbatim" not in spine  # the exact leaked phrase
    assert "owner_note" not in spine
    assert "---" not in spine


def test_prompt_template_and_kwargs_in_lockstep(monkeypatch):
    """Same str.format trap as turn_read: template vars must match the call."""
    from ai.prompt_loader import prompt_loader

    # Step 25 · Regret: the template carries four flag-gated slots; pinned
    # off here (3352e3d — never the live yaml), they render today's bytes.
    monkeypatch.setattr(stake_appraiser, "_regret_enabled", lambda: False)
    # Organ 4.13 · Opinions: three more flag-gated slots on the same template,
    # pinned off here too. Both kwarg dicts carry open_kinds, so they merge
    # into ONE mapping (view last) exactly as the live format call does.
    monkeypatch.setattr(stake_appraiser, "_opinions_enabled", lambda: False)
    template = prompt_loader.load_prompt("stake_appraisal")
    assert template, "prompts/stake_appraisal.txt missing"
    rendered = template.format(
        **{**stake_appraiser._regret_prompt_kwargs(),
           **stake_appraiser._view_prompt_kwargs()},
        care_spine="cs",
        user_message="um",
        palli_reply="pr",
        recent_conversation="(no earlier context)",
        open_stakes="(none yet)",
        open_count=0,
        max_open=12,
        current_date="Monday, 2026-07-06",
    )
    assert '"ops"' in rendered
    assert "FIRST PERSON" in rendered
    assert "NEVER keep" in rendered  # the Tier-3 exclusions made it in
    # Horizon contract: null must be safe on update (never clears).
    assert '"clear" to remove' in rendered


@pytest.mark.asyncio
async def test_appraise_exchange_hanging_llm_times_out(monkeypatch, brain):
    """Review finding: a hung provider call must be bounded — otherwise every
    exchange pins a background task forever and the ledger quietly dies."""
    import asyncio as _asyncio

    class _HangingLLM:
        async def chat_completion(self, **kwargs):
            await _asyncio.Event().wait()  # never returns

    import ai.llm_client as llm_client

    monkeypatch.setattr(llm_client, "llm", _HangingLLM())
    monkeypatch.setattr(llm_client, "get_task_model", lambda task: "fake::appraisal")
    monkeypatch.setattr(stake_appraiser, "APPRAISAL_TIMEOUT_SECONDS", 0.05)

    summary = await stake_appraiser.appraise_exchange(
        user_message="hi", palli_reply="hello", brain_client=brain
    )
    assert summary == {"error": "timeout"}
    assert (await stake_ledger.counts(brain_client=brain)) == {}
