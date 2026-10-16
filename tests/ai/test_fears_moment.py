# tests/ai/test_fears_moment.py
"""The fears block rides the moment (step 24, spec §3): a flag-conditional
sense in the gather (health key only when on — the expectations pattern),
the {fears_block} slot in the two LIVE rungs with zero-byte collapse, a
contract-free prose append for the monologue's situation — and flag-off
AND empty-book prompts pinned byte-identical end-to-end (decision 5)."""
from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import patch

import pytest

import ai.present_moment as pm
import ai.present_moment_gather as pmg
from ai.present_moment import PresentMomentEngine


# ── the gather: flag-conditional sense ──────────────────────────────────

def _gather():
    return asyncio.run(pmg.gather_moment_inputs(owner_user_id=1))


def test_gather_on_carries_block_and_health_key():
    with patch("ai.fear_store.fears_enabled", return_value=True), \
         patch("ai.fear_store.render_fears_block",
               return_value="YOUR HELD FEARS — x:\n- a: b — you watch for: c\n\n"):
        out = _gather()
    assert out.fears_block.startswith("YOUR HELD FEARS")
    assert out.fears_block.endswith("\n\n")   # the zero-byte-collapse contract
    assert out.sense_health.get("fears") == "ok"


def test_gather_off_carries_neither():
    with patch("ai.fear_store.fears_enabled", return_value=False):
        out = _gather()
    assert out.fears_block == ""
    assert "fears" not in out.sense_health


def test_gather_on_empty_book_block_empty_health_ok():
    """The flag is on, the book is empty: the sense RAN (health key), the
    block is zero bytes — decision 5's prompt purity, end of the seam."""
    with patch("ai.fear_store.fears_enabled", return_value=True), \
         patch("ai.fear_store.render_fears_block", return_value=""):
        out = _gather()
    assert out.fears_block == ""
    assert out.sense_health.get("fears") == "ok"


# ── the templates: the slot, live rungs only ────────────────────────────

def test_templates_carry_fears_slot_with_zero_byte_collapse():
    for name in ("present_moment_appraisal_wanting.txt",
                 "present_moment_appraisal_arbiter.txt"):
        text = (Path("prompts") / name).read_text(encoding="utf-8")
        assert "{fears_block}" in text, name
        # zero-byte collapse: the slot is immediately followed by existing
        # text on the same line (no orphan blank line when it renders "")
        idx = text.index("{fears_block}")
        assert text[idx + len("{fears_block}")] not in ("\n",), name
    for name in ("present_moment_appraisal.txt",
                 "present_moment_appraisal_affect.txt"):
        assert "{fears_block}" not in (
            Path("prompts") / name
        ).read_text(encoding="utf-8"), name   # the variant ladder stands


# ── the renderer grows the monologue's contract-free mode ───────────────

def test_render_without_contract_for_the_monologue(tmp_path):
    import ai.fear_store as fs

    fs.plant(text="losing my own thread", watch="my record contradicting me",
             source="reflection", root=tmp_path)
    with patch.object(fs, "fears_enabled", lambda: True):
        prose = fs.render_fears_block(include_contract=False, root=tmp_path)
        full = fs.render_fears_block(include_contract=True, root=tmp_path)
    assert prose.startswith("YOUR FEARS RIGHT NOW")
    assert "- losing-my-own-thread" in prose.split("—")[0] or "losing" in prose
    assert fs._CONTRACT not in prose          # the touch contract is the moment's
    assert prose.endswith("\n\n")
    assert fs._CONTRACT in full


def test_monologue_situation_block_is_contract_free(monkeypatch):
    import ai.inner_monologue as im

    seen = {}

    def fake_render(*, include_contract=True, root=None):
        seen["include_contract"] = include_contract
        return "YOUR HELD FEARS — t:\n- a: b — you watch for: c\n\n"

    monkeypatch.setattr("ai.fear_store.render_fears_block", fake_render)
    out = im._fears_situation_block()
    assert seen["include_contract"] is False
    assert out.startswith("YOUR HELD FEARS")
    assert not out.endswith("\n")             # stripped for prose stacking

    def boom(**kw):
        raise RuntimeError("store gone")

    monkeypatch.setattr("ai.fear_store.render_fears_block", boom)
    assert im._fears_situation_block() == ""  # degrade to absence, never error


# ── end to end: the cycle's prompt bytes ────────────────────────────────

_FEED = ("YOUR HELD FEARS — test:\n"
         "- losing-him: him ending this — you watch for: a goodbye shaped "
         "like logistics\n\n")


@pytest.mark.asyncio
async def test_cycle_prompt_carries_fears_and_collapses_clean(monkeypatch):
    """The live arbiter rung renders her held fears; an empty block leaves
    the prompt byte-identical to the slotless days (decision 5). Harness:
    the rail-hook test's shape — LLM + gather + store stubbed, REAL
    template."""
    engine = PresentMomentEngine()
    monkeypatch.setattr(engine, "_affect_enabled", lambda: True)
    monkeypatch.setattr(engine, "_arbiter_enabled", lambda: True)
    monkeypatch.setattr(engine, "_wanting_enabled", lambda: False)

    captured = []

    async def _fake_llm(messages, **kw):
        captured.append(messages[0]["content"])
        return ('{"moment": "m", "impulse": {"kind": "none", "why": ""}, '
                '"undercurrent": "steady"}')

    class _L:
        chat_completion = staticmethod(_fake_llm)

    monkeypatch.setattr("ai.llm_client.llm", _L())
    monkeypatch.setattr(
        "ai.present_moment_store.write_moment",
        lambda *, text, meta: "x.jsonl",
    )

    blocks = {"fears": _FEED}

    async def _fake_gather(*, owner_user_id):
        return pmg.GatherInputs(fears_block=blocks["fears"])

    monkeypatch.setattr(pmg, "gather_moment_inputs", _fake_gather)

    await engine.run_cycle_once()
    prompt = captured[-1]
    assert "YOUR HELD FEARS — test:" in prompt
    # the slot sits flush against the next section — block bytes + heading
    assert _FEED + "WHAT YOU CARE ABOUT" in prompt

    blocks["fears"] = ""
    await engine.run_cycle_once()
    empty_prompt = captured[-1]
    assert "YOUR HELD FEARS" not in empty_prompt
    assert "(no senses readable)\n\nWHAT YOU CARE ABOUT" in empty_prompt


# ── the touch (Task 3, spec §4): parse fail-safe + bookkeeping ──────────

def test_parse_fear_shapes():
    assert pm._parse_fear(None) is None
    assert pm._parse_fear("nah") is None
    assert pm._parse_fear({"id": "", "line": "x", "weight": "real"}) is None
    assert pm._parse_fear({"id": "a-fear", "line": "", "weight": "real"}) is None
    out = pm._parse_fear({"id": "  losing-him ",
                          "line": "  a goodbye  shaped like logistics ",
                          "weight": "real"})
    assert out == {"id": "losing-him",
                   "line": "a goodbye shaped like logistics",
                   "weight": "real"}
    assert pm._parse_fear(
        {"id": "a", "line": "x", "weight": "odd"}
    )["weight"] == "slight"                       # unknown ⇒ slight


def _touch_harness(monkeypatch, llm_json):
    """The cycle harness with the REAL fear store riding the conftest
    tmp redirect (root=None everywhere — the live seam under test)."""
    engine = PresentMomentEngine()
    monkeypatch.setattr(engine, "_affect_enabled", lambda: True)
    monkeypatch.setattr(engine, "_arbiter_enabled", lambda: True)
    monkeypatch.setattr(engine, "_wanting_enabled", lambda: False)

    async def _fake_llm(messages, **kw):
        return llm_json

    class _L:
        chat_completion = staticmethod(_fake_llm)

    monkeypatch.setattr("ai.llm_client.llm", _L())
    written = {}
    monkeypatch.setattr(
        "ai.present_moment_store.write_moment",
        lambda *, text, meta: written.update(meta=meta) or "x.jsonl",
    )

    async def _fake_gather(*, owner_user_id):
        return pmg.GatherInputs()

    monkeypatch.setattr(pmg, "gather_moment_inputs", _fake_gather)
    return engine, written


_TOUCH_JSON = ('{"moment": "m", "impulse": {"kind": "none", "why": ""}, '
               '"undercurrent": "steady", '
               '"fear": {"id": "%s", "line": "it brushed me", '
               '"weight": "real"}}')


@pytest.mark.asyncio
async def test_cycle_touch_lands_in_meta_and_store(monkeypatch):
    import ai.fear_store as fs

    planted = fs.plant(text="losing him", watch="a goodbye shaped like "
                       "logistics", source="reflection")   # conftest tmp root
    monkeypatch.setattr(fs, "fears_enabled", lambda: True)
    engine, written = _touch_harness(monkeypatch, _TOUCH_JSON % planted.id)
    await engine.run_cycle_once()
    assert written["meta"]["fear"] == {
        "id": planted.id, "line": "it brushed me", "weight": "real",
    }
    folded = fs.load_fears()
    assert folded[planted.id].touches == 1


@pytest.mark.asyncio
async def test_cycle_phantom_id_touch_dies(monkeypatch):
    """A touch naming a fear she does not hold is a manufactured touch —
    no meta, no bookkeeping (the store is the truth)."""
    import ai.fear_store as fs

    monkeypatch.setattr(fs, "fears_enabled", lambda: True)
    engine, written = _touch_harness(
        monkeypatch, _TOUCH_JSON % "a-fear-she-never-held")
    await engine.run_cycle_once()
    assert "fear" not in written["meta"]
    assert fs.load_fears() == {}


@pytest.mark.asyncio
async def test_cycle_flag_off_ignores_fear_field(monkeypatch):
    """Flag off: even a well-formed fear in the JSON leaves meta and the
    book untouched (byte-identical-off — the surprise law)."""
    import ai.fear_store as fs

    planted = fs.plant(text="losing him", watch="w", source="reflection")
    monkeypatch.setattr(fs, "fears_enabled", lambda: False)
    engine, written = _touch_harness(monkeypatch, _TOUCH_JSON % planted.id)
    await engine.run_cycle_once()
    assert "fear" not in written["meta"]
    assert fs.load_fears()[planted.id].touches == 0


@pytest.mark.asyncio
async def test_cycle_garbled_fear_is_nothing(monkeypatch):
    import ai.fear_store as fs

    monkeypatch.setattr(fs, "fears_enabled", lambda: True)
    engine, written = _touch_harness(
        monkeypatch,
        '{"moment": "m", "impulse": {"kind": "none", "why": ""}, '
        '"undercurrent": "u", "fear": "yes very scary"}')
    await engine.run_cycle_once()
    assert "fear" not in written["meta"]


# ── the care rail, pinned by REGRESSION only (Task 3, spec §5) ──────────

@pytest.mark.asyncio
async def test_care_rail_regression_fear_say_traverses_unchanged(
    monkeypatch, tmp_path
):
    """Decision 2: NO arbiter change. A care say formed in a fear context
    is an ORDINARY care say — first look held, held_before sends, and
    today's ledger entry carries exactly the standard fields (no fear
    plumbing anywhere on the rail)."""
    import json
    import types

    import ai.arbiter as arbiter

    monkeypatch.setattr(arbiter, "ARBITER_DATA_DIR", tmp_path / "arbiter")
    comp = types.SimpleNamespace(
        arbiter=True, present_moment=True, arbiter_daily_send_ceiling=3,
        arbiter_quiet_minutes=5, arbiter_quiet_window="",
        arbiter_freshness_check=True, arbiter_learned_quiet=False,
        surprise_daily_named=1,
    )
    monkeypatch.setattr(arbiter, "_companion", lambda: comp)

    async def _fresh_ok(**kw):
        return (True, "fresh (fixture)")

    async def _chan(*, owner_id):
        return ("discord", "123")

    async def _last_msg(*, owner_id):
        return 1.0                                  # long quiet

    sent = []

    async def _record(**kw):
        sent.append(kw)
        return True

    monkeypatch.setattr(arbiter, "_freshness_check", _fresh_ok)
    monkeypatch.setattr(arbiter, "_pick_surface", _chan)
    monkeypatch.setattr(arbiter, "_last_user_message_at", _last_msg)
    monkeypatch.setattr(arbiter, "_record_outbound", _record)
    monkeypatch.setattr(arbiter, "_run_phase", lambda sk: "")
    monkeypatch.setattr(arbiter, "_is_working", lambda sk: False)

    say = {"kind": "say", "why": "a fear of mine was touched today",
           "framing": "today brushed against something I'd hate to lose — "
                      "can I say it out loud?",
           "sensitivity": "care"}
    out = await arbiter.consider_release(
        impulse=dict(say), owner_user_id=1, arbiter_live=True)
    assert out["disposition"] == "held_care_first_look"   # care law holds
    assert sent == []

    out = await arbiter.consider_release(
        impulse={**say, "held_before": True}, owner_user_id=1,
        arbiter_live=True)
    assert out["disposition"] == "sent"
    assert len(sent) == 1
    assert sent[0]["text"] == say["framing"]              # byte-identical

    lines = arbiter._ledger_path().read_text(encoding="utf-8").splitlines()
    entry = json.loads(lines[-1])
    assert entry["disposition"] == "sent"
    assert entry["sensitivity"] == "care"
    assert "fear" not in entry                            # no new plumbing
    assert "fear" not in out
