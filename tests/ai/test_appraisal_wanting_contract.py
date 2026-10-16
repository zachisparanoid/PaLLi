# tests/ai/test_appraisal_wanting_contract.py
"""want_id joins the impulse contract; the prompt ladder gains its fourth
rung; the wants board assembles ONLY under wanting+arbiter (no block, no
health key otherwise — the byte-identical-off law, the threads pattern)."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import patch

import ai.present_moment as pm
import ai.present_moment_gather as gather


# ── _parse_impulse ──────────────────────────────────────────────────────

def test_want_id_parses_like_thread_id():
    impulse = pm._parse_impulse({
        "kind": "say", "why": "serving a wish", "framing": "made you a thing",
        "want_id": "surprise-me-abc123",
    })
    assert impulse["want_id"] == "surprise-me-abc123"


def test_absent_want_id_adds_no_key():
    impulse = pm._parse_impulse({"kind": "say", "why": "w", "framing": "f"})
    assert "want_id" not in impulse


def test_want_id_is_whitespace_collapsed_and_capped():
    impulse = pm._parse_impulse({
        "kind": "say", "why": "w", "framing": "f", "want_id": "  a   b" + "x" * 100,
    })
    assert impulse["want_id"].startswith("a b")
    assert len(impulse["want_id"]) <= 64


# ── the prompt ladder ───────────────────────────────────────────────────

def _load(**kw):
    return pm._load_appraisal_template(**kw)


def test_ladder_tops_out_at_wanting_when_all_three_on():
    tmpl, variant = _load(affect_on=True, arbiter_on=True, wanting_on=True)
    assert variant == "wanting"
    assert tmpl and "{wants_block}" in tmpl


def test_ladder_stays_arbiter_when_wanting_off():
    tmpl, variant = _load(affect_on=True, arbiter_on=True, wanting_on=False)
    assert variant == "arbiter"
    assert tmpl and "{wants_block}" not in tmpl


def test_missing_wanting_file_falls_back_to_arbiter():
    from ai.prompt_loader import prompt_loader

    orig = prompt_loader.load_prompt

    def fake(name):
        if name == "present_moment_appraisal_wanting":
            return ""
        return orig(name)

    with patch.object(prompt_loader, "load_prompt", side_effect=fake):
        _, variant = _load(affect_on=True, arbiter_on=True, wanting_on=True)
    assert variant == "arbiter"


# ── the wants board (gather) ────────────────────────────────────────────

def _comp(**kw):
    base = {"present_moment": True, "arbiter": False, "wanting": False,
            "self_notes": False, "own_work": False}
    base.update(kw)
    return SimpleNamespace(**base)


def _gather(comp):
    # The gather sources are async defs, so patch.object auto-creates
    # AsyncMock: its await RESOLVES to return_value — plain strings here,
    # never pre-built coroutines (the plan's _now() wrapper handed the
    # await a coroutine as its result; deviation recorded in spec §-notes).
    async def _run():
        with patch.object(gather, "_companion", return_value=comp), \
             patch.object(gather, "_conversation_tail", return_value="tail"), \
             patch.object(gather, "_presence", return_value="Machine: idle."), \
             patch.object(gather, "_activity", return_value=""), \
             patch.object(gather, "_deeds", return_value=""), \
             patch.object(gather, "_stakes", return_value=""), \
             patch.object(gather, "_care_spine", return_value=""), \
             patch.object(gather, "_interests", return_value=""), \
             patch.object(gather, "_monologue", return_value=""), \
             patch.object(gather, "_reflection", return_value=""), \
             patch.object(gather, "_threads", return_value="no threads"), \
             patch.object(gather, "_wants", return_value="- [abc] a wish"):
            return await gather.gather_moment_inputs(owner_user_id=1)
    return asyncio.run(_run())


def test_wants_board_present_only_under_wanting_and_arbiter():
    on = _gather(_comp(arbiter=True, wanting=True))
    assert on.wants_block == "- [abc] a wish"
    assert on.sense_health.get("wants") == "ok"


def test_wants_board_absent_when_wanting_off_no_health_key():
    off = _gather(_comp(arbiter=True, wanting=False))
    assert off.wants_block == ""
    assert "wants" not in off.sense_health
