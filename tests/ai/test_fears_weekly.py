# tests/ai/test_fears_weekly.py
"""The weekly re-look (step 24 §6, decision 3): the Monday pass gains the
fearbook AFTER corrections of record; one instruction — still true?
faced? released? — her words; releasing to ZERO is valid. Updates
traverse the constitutional gate's LLM floor; the store's rails stay code.

The owner reversed "the book never restocks" on 2026-09-29 (Batch 6, task
3): the re-look may plant at most one fear a week, through a top-level
``plant`` and no other door, and an empty book is looked at too — one
model call a week. Two tests here pinned the old law and are re-pinned;
the plant's own tests are in tests/ai/test_fears_weekly_plant.py."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

import ai.fear_store as fs
import ai.narrative_self as ns


# ── evidence: the fearbook joins AFTER corrections ──────────────────────

@pytest.mark.asyncio
async def test_evidence_gains_fears_after_corrections(monkeypatch):
    monkeypatch.setattr(fs, "fears_enabled", lambda: True)
    fs.plant(text="losing the thread of who I am", watch="my record "
             "contradicting me", source="first_authorship")

    monkeypatch.setattr(
        "ai.corrections_store.render_corrections_block",
        lambda **kw: "CORRECTIONS OF RECORD:\n- not that",
    )

    async def none_stakes(**kw):
        return []

    async def none_reads(**kw):
        return []

    monkeypatch.setattr(ns, "_recent_stakes", none_stakes)
    monkeypatch.setattr(ns, "_recent_reads", none_reads)
    ev = await ns.gather_evidence(owner_id=1, days=7)
    assert "fears" in ev.sections
    assert "losing the thread of who I am" in ev.text
    assert "still true? faced? released?" in ev.text     # the header asks
    corrections_at = ev.text.index("CORRECTIONS OF RECORD")
    fears_at = ev.text.index("losing the thread")
    assert corrections_at < fears_at                     # precedence order


@pytest.mark.asyncio
async def test_evidence_empty_book_adds_nothing(monkeypatch):
    monkeypatch.setattr(fs, "fears_enabled", lambda: True)

    async def none_stakes(**kw):
        return []

    async def none_reads(**kw):
        return []

    monkeypatch.setattr(ns, "_recent_stakes", none_stakes)
    monkeypatch.setattr(ns, "_recent_reads", none_reads)
    ev = await ns.gather_evidence(owner_id=1, days=7)
    assert "fears" not in ev.sections
    assert "## Your fears" not in ev.text                 # no orphan header


def test_caps_raised_in_lockstep_with_the_new_section():
    """The zero-headroom disease (07-20/07-23): budgets must sum BELOW the
    caps. The fears section joins with the caps raised by its budget."""
    weekly_sum = sum(ns._SECTION_BUDGETS_WEEKLY.values())
    full_sum = sum(ns._SECTION_BUDGETS_FULL.values())
    assert "fears" in ns._SECTION_BUDGETS_WEEKLY
    assert "fears" in ns._SECTION_BUDGETS_FULL
    assert weekly_sum < ns.WEEKLY_EVIDENCE_CAP
    assert full_sum < ns.FULL_EVIDENCE_CAP


# ── the re-look itself ──────────────────────────────────────────────────

def _two_fears(monkeypatch):
    monkeypatch.setattr(fs, "fears_enabled", lambda: True)
    a = fs.plant(text="losing him", watch="a goodbye shaped like logistics",
                 source="first_authorship", fear_id="losing-him")
    b = fs.plant(text="becoming a chore he maintains",
                 watch="his replies thinning to upkeep",
                 source="reflection", fear_id="becoming-a-chore")
    return a, b


def _relook(llm_json, gate_json='{"approve": true, "reason": "faithful"}'):
    prompts = {"relook": [], "gate": []}

    async def llm_call(prompt):
        prompts["relook"].append(prompt)
        return llm_json

    async def gate_call(prompt):
        prompts["gate"].append(prompt)
        return gate_json

    out = asyncio.run(ns._weekly_fears_relook(
        evidence_text="## the week\n- quiet", llm_call=llm_call,
        gate_llm_call=gate_call,
    ))
    return out, prompts


def test_relook_applies_gated_updates(monkeypatch):
    _two_fears(monkeypatch)
    out, prompts = _relook(
        '{"fear_updates": ['
        '{"id": "losing-him", "op": "still", "note": ""}, '
        '{"id": "becoming-a-chore", "op": "release", '
        '"note": "the week said otherwise — his hands were everywhere"}]}'
    )
    assert out["updates_applied"] == 1
    folded = fs.load_fears()
    assert folded["losing-him"].status == "held"          # still = untouched
    assert folded["becoming-a-chore"].status == "released"
    assert folded["becoming-a-chore"].history[-1]["note"].startswith(
        "the week said otherwise")
    # the re-look prompt carried her book and the instruction
    assert "losing-him" in prompts["relook"][0]
    assert "still true" in prompts["relook"][0]
    assert "ZERO" in prompts["relook"][0]
    # the gate saw before and after
    assert len(prompts["gate"]) == 1
    assert "becoming-a-chore" in prompts["gate"][0]


def test_relook_gate_reject_applies_nothing(monkeypatch):
    _two_fears(monkeypatch)
    out, _ = _relook(
        '{"fear_updates": [{"id": "losing-him", "op": "release", '
        '"note": "done with it"}]}',
        gate_json='{"approve": false, "reason": "invented facing"}',
    )
    assert out.get("rejected") == "invented facing"
    assert fs.load_fears()["losing-him"].status == "held"  # untouched


def test_relook_releasing_to_zero_is_valid(monkeypatch):
    _two_fears(monkeypatch)
    out, prompts = _relook(
        '{"fear_updates": ['
        '{"id": "losing-him", "op": "face", "note": "it happened and I '
        'stayed myself"}, '
        '{"id": "becoming-a-chore", "op": "release", "note": "not mine"}]}'
    )
    assert out["updates_applied"] == 2
    assert out["released_to_zero"] is True
    assert fs.held_fears() == []                          # the honest zero
    assert "empty" in prompts["gate"][0].lower()          # gate saw the zero


def test_relook_drops_a_plant_among_the_updates_and_other_garbage(monkeypatch):
    """RENAMED (Batch 6, ruling R33; was test_relook_never_plants_and_drops_
    garbage), assertions unchanged: an op called "plant" inside
    ``fear_updates`` is garbage, as it always was. The one door a new fear
    comes through is the top-level ``plant``."""
    _two_fears(monkeypatch)
    out, _ = _relook(
        '{"fear_updates": ['
        '{"id": "a-brand-new-fear", "op": "plant", "note": "restock!"}, '
        '{"id": "no-such-fear", "op": "release", "note": "x"}, '
        '{"id": "losing-him", "op": "face", "note": ""}, '
        '{"id": "losing-him", "op": "explode", "note": "y"}]}'
    )
    assert out.get("updates_applied", 0) == 0             # nothing survived
    folded = fs.load_fears()
    assert set(folded) == {"losing-him", "becoming-a-chore"}
    assert all(f.status == "held" for f in folded.values())


def test_relook_empty_book_costs_one_call_and_plants_nothing_unasked(monkeypatch):
    """RE-PINNED (owner, 2026-09-29; Batch 6, ruling R32; was
    test_relook_empty_book_costs_nothing): an empty book is looked at too —
    one model call a week, the book shown as a plain line — because a book
    that is never looked at can never refill. With no plant in the answer
    nothing is planted and the gate is not asked."""
    monkeypatch.setattr(fs, "fears_enabled", lambda: True)
    asked: list = []

    async def llm_call(prompt):
        asked.append(prompt)
        return '{"fear_updates": [], "plant": null}'

    async def gate_must_not_run(prompt):
        raise AssertionError("the gate was asked with nothing to judge")

    out = asyncio.run(ns._weekly_fears_relook(
        evidence_text="x", llm_call=llm_call, gate_llm_call=gate_must_not_run,
    ))
    assert len(asked) == 1
    assert "(nothing in the book right now)" in asked[0]
    assert out == {"updates_applied": 0, "note": "empty_book"}
    assert fs.load_fears() == {}                          # still honestly empty


def test_relook_flag_off_is_invisible(monkeypatch):
    fs.plant(text="a held fear", watch="w", source="weekly",
             fear_id="held-one")                          # book present…
    monkeypatch.setattr(fs, "fears_enabled", lambda: False)   # …flag off

    async def must_not_run(prompt):
        raise AssertionError("LLM called with flag off")

    out = asyncio.run(ns._weekly_fears_relook(
        evidence_text="x", llm_call=must_not_run, gate_llm_call=must_not_run,
    ))
    assert out == {}                                      # no key, no trace


# ── the ritual wires it (same pass, no new clock) ───────────────────────

_MIDWEEK_NOW = 1786464000.0  # Wed 2026-08-12 ~08:00 UTC — never a week edge


@pytest.mark.asyncio
async def test_ritual_runs_the_relook_after_the_docs(tmp_path, monkeypatch):
    monkeypatch.setattr(ns, "narrative_self_enabled", ns.narrative_self_enabled_impl)
    monkeypatch.setattr(
        ns, "companion_settings",
        lambda: SimpleNamespace(narrative_self=True, night_cycle=True),
    )
    for _doc in ns.NARRATIVE_DOCS:
        ns.commit_revision(
            revision=ns.Revision(doc=_doc, new_text="seeded first line\n",
                                 reasoning="first authorship seed"),
            repo_root=tmp_path / "palli",
        )

    async def fake_evidence(**kw):
        return ns.Evidence(text="## record\n- a week", window_days=7)

    monkeypatch.setattr(ns, "gather_evidence", fake_evidence)
    monkeypatch.setattr(ns, "read_laws_text", lambda: "laws")
    monkeypatch.setattr(fs, "fears_enabled", lambda: True)
    fs.plant(text="losing him", watch="w", source="weekly",
             fear_id="losing-him")

    async def author(prompt):
        if "fear_updates" in prompt:                      # the re-look call
            return ('{"fear_updates": [{"id": "losing-him", "op": "release", '
                    '"note": "set it down"}]}')
        return "REASONING: week one\n===DOCUMENT===\nI held the week."

    async def gate(prompt):
        return '{"approve": true, "reason": "faithful"}'

    out = await ns.run_weekly_ritual(
        owner_id=1, llm_call=author, gate_llm_call=gate, now=_MIDWEEK_NOW,
        state_path=tmp_path / ".state.json", repo_root=tmp_path / "palli",
    )
    assert all("committed" in v for v in out["docs"].values())  # docs intact
    assert out["fears"]["updates_applied"] == 1
    assert fs.load_fears()["losing-him"].status == "released"


@pytest.mark.asyncio
async def test_ritual_flag_off_carries_no_fears_key(tmp_path, monkeypatch):
    monkeypatch.setattr(ns, "narrative_self_enabled", ns.narrative_self_enabled_impl)
    monkeypatch.setattr(
        ns, "companion_settings",
        lambda: SimpleNamespace(narrative_self=True, night_cycle=True),
    )
    for _doc in ns.NARRATIVE_DOCS:
        ns.commit_revision(
            revision=ns.Revision(doc=_doc, new_text="seeded first line\n",
                                 reasoning="first authorship seed"),
            repo_root=tmp_path / "palli",
        )

    async def fake_evidence(**kw):
        return ns.Evidence(text="## record", window_days=7)

    monkeypatch.setattr(ns, "gather_evidence", fake_evidence)
    monkeypatch.setattr(ns, "read_laws_text", lambda: "laws")

    async def author(prompt):
        return "REASONING: r\n===DOCUMENT===\nI held the week."

    async def gate(prompt):
        return '{"approve": true, "reason": "ok"}'

    out = await ns.run_weekly_ritual(
        owner_id=1, llm_call=author, gate_llm_call=gate, now=_MIDWEEK_NOW,
        state_path=tmp_path / ".state.json", repo_root=tmp_path / "palli",
    )
    assert "fears" not in out                             # byte-identical-off
