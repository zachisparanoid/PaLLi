"""Organ 4.13 — the appraiser's view slots: byte-identical off, armed on.

The regret law, squared: with companion.opinions off the rendered appraisal
prompt is byte-for-byte what it is today for BOTH regret states — the three
slots collapse to zero bytes and the kinds union stays the regret-state one.
On, the turn keeps the speed-one grammar for views that already exist (mute
for "drop it", unmute for "bring it back", update+reason for a genuine
move) under the scope law. FORMING a view left the turn on 2026-09-29
(Batch 6, task 2, rulings R25/R26): views form at the nightly pass, from the
whole day (prompts/night_cycle_views.txt, tests/ai/test_views_nightly.py),
so the turn is no longer offered the kind to open.

One exception, added by task 5's rider R4: the pressure line's "; views hold
no slot" is unconditional template text — the count it explains is everyone's
— so it is read even with the organ off. The collapse law pinned here is the
one that matters: the flag-gated SLOTS render zero bytes.
"""
from __future__ import annotations

import time
from pathlib import Path

import pytest

from ai import stake_appraiser, stake_ledger
from ai.prompt_loader import prompt_loader


def _render(monkeypatch, *, regret: bool, opinions: bool) -> str:
    monkeypatch.setattr(stake_appraiser, "_regret_enabled", lambda: regret)
    monkeypatch.setattr(stake_appraiser, "_opinions_enabled", lambda: opinions)
    if regret:
        monkeypatch.setattr(stake_appraiser, "_regret_corrections_block", lambda: "")
    template = prompt_loader.load_prompt("stake_appraisal")
    # Both kwarg dicts carry open_kinds, and two ** unpackings with the same
    # key is a TypeError at the call — so they are merged into ONE mapping,
    # view LAST, which is exactly "the view union wins" (the live format call
    # in appraise_exchange does the same).
    return template.format(
        care_spine="c", user_message="u", palli_reply="p",
        recent_conversation="r", open_stakes="(none yet)",
        open_count=0, max_open=12, current_date="Monday, 2026-08-31",
        **{**stake_appraiser._regret_prompt_kwargs(),
           **stake_appraiser._view_prompt_kwargs()},
    )


def _stake(**kw):
    base = dict(id=4, kind="view", stake="I side with the 1978 manuals",
                status="open", resolution="", horizon_at=None,
                source_channel_id="", last_surfaced_at=None,
                created_at=1000.0, updated_at=1000.0, resolved_at=None,
                voiced_at=None, evidence="his 03-14 scan; the torque table",
                muted_at=None)
    base.update(kw)
    return stake_ledger.Stake(**base)


def _mock_llm(monkeypatch, response: str) -> list:
    calls: list = []

    class _FakeLLM:
        async def chat_completion(self, **kwargs):
            calls.append(kwargs)
            return response

    import ai.llm_client as llm_client

    monkeypatch.setattr(llm_client, "llm", _FakeLLM())
    monkeypatch.setattr(llm_client, "get_task_model", lambda task: f"fake::{task}")
    return calls


# ── the slots: off is today's bytes, on arms the contract ───────────────


def test_opinions_off_is_byte_identical_regardless_of_regret(monkeypatch):
    for regret in (False, True):
        off = _render(monkeypatch, regret=regret, opinions=False)
        # The pressure line's "; views hold no slot" is UNCONDITIONAL template
        # text (task 5 rider R4) — everyone reads it, because the count it
        # explains is everyone's. What stays pinned here is the collapse law
        # for the three FLAG-GATED slots: zero bytes when the organ is off.
        assert "view" not in (
            off.lower().replace("interview", "").replace("; views hold no slot", "")
        )
        expected_union = "hope|worry|taste|wonder" + ("|regret" if regret else "")
        assert expected_union in off


def test_opinions_off_leaves_the_ops_grammar_untouched(monkeypatch):
    """The hint slot sits at column 0 of the update line: empty, that line
    renders exactly as it does today — no orphan blank line in her JSON."""
    off = _render(monkeypatch, regret=False, opinions=False)
    assert ('"horizon": "YYYY-MM-DD or null"},\n'
            '    {"op": "update", "id": 3,') in off
    # and the NEVER line still ends where it ended before this organ
    assert "inside a worry's prose.)\n- FIRST PERSON" in off


def test_opinions_on_arms_the_contract(monkeypatch):
    """RE-PINNED (Batch 6, ruling R26): the kinds a turn may open are the
    regret-state ones — a view is never opened from a turn."""
    on = _render(monkeypatch, regret=True, opinions=True)
    assert '"kind": "hope|worry|taste|wonder|regret"' in on
    assert "|view" not in on
    assert '"view"' in on and "evidence" in on
    assert "their personal choices" in on        # the scope law
    assert '"mute"' in on and '"reason"' in on   # drop-it + revise grammar


def test_opinions_on_carries_the_bar_the_never_line_and_single_braces(monkeypatch):
    """RE-PINNED (Batch 6, ruling R26): the formation bar left the turn for
    the nightly pass's own prompt; the turn says where views form, and
    keeps the never line word for word. UPDATED (Batch 7's fix wave, M1):
    two plain words in it, "held quietly" -> "kept quietly" and
    "GENUINELY" -> "REALLY"; the meaning and the pins are the same."""
    on = _render(monkeypatch, regret=True, opinions=True)
    # (the template's own taste line says "repeated genuine contact" too,
    # with the organ on or off, so the view's bar is read off its slot)
    kind_block = stake_appraiser._view_prompt_kwargs()["view_kind_block"]
    assert "repeated genuine contact" not in kind_block   # the formation bar moved…
    assert "one exchange never births one" not in on
    assert "form at night" in on and "never open one" in on
    nightly = Path("prompts/night_cycle_views.txt").read_text(encoding="utf-8").lower()
    assert "one exchange never forms a view" in nightly   # …to the nightly pass
    assert "more than once across the day" in nightly
    assert "facts, taste, and approach" in nightly
    assert "facts, taste, and approach" in on             # the scope law, in full
    assert "prove I have a self" in on                    # the manufactured-view veto
    assert "because they repeated themselves" in on       # only something NEW moves one
    assert "kept quietly, never deleted" in on            # drop-it is a mute
    assert "if this exchange REALLY moved one of my views, emit update" in on
    assert "held quietly" not in on and "GENUINELY" not in on   # plain words (M1)
    # the ops hint is a format VALUE — values are never re-scanned, so it must
    # be written with SINGLE braces or literal {{ }} reach her live prompt.
    assert '    {"op": "mute", "id": 4},\n' in on
    assert '"kind": "view"' not in on and '"reason": "why it moved me"' in on
    assert "{{" not in on and "}}" not in on
    # the hint lands directly under the open example, and begins at update
    assert (' "horizon": "YYYY-MM-DD or null"},\n'
            '    {"op": "update", "id": 4, "stake": "the revised position"') in on


def test_view_kwargs_are_the_four_slots_and_the_union_honors_both_flags(monkeypatch):
    """RE-PINNED (Batch 6, ruling R26): the union follows the regret flag
    in both opinion states — opinions on no longer adds a kind to open."""
    keys = {"view_kind_block", "view_never_line", "view_ops_hint", "open_kinds"}
    for regret in (False, True):
        for opinions in (False, True):
            monkeypatch.setattr(stake_appraiser, "_regret_enabled", lambda r=regret: r)
            monkeypatch.setattr(stake_appraiser, "_opinions_enabled",
                                lambda o=opinions: o)
            kw = stake_appraiser._view_prompt_kwargs()
            assert set(kw) == keys, (regret, opinions)
            expected = "hope|worry|taste|wonder" + ("|regret" if regret else "")
            assert kw["open_kinds"] == expected, (regret, opinions)
            if not opinions:
                assert kw["view_kind_block"] == kw["view_never_line"] == ""
                assert kw["view_ops_hint"] == ""
            else:
                assert kw["view_kind_block"].endswith("\n")
                assert not kw["view_kind_block"].startswith("\n")
                assert kw["view_never_line"].startswith(" ")
                assert not kw["view_never_line"].endswith("\n")
                assert kw["view_ops_hint"].endswith("\n")


def test_regret_kwargs_are_untouched_by_this_organ(monkeypatch):
    """_regret_prompt_kwargs stays byte-exact — the opinions flag never
    reaches it (only the merge order decides which open_kinds survives)."""
    monkeypatch.setattr(stake_appraiser, "_opinions_enabled", lambda: True)
    monkeypatch.setattr(stake_appraiser, "_regret_enabled", lambda: False)
    assert stake_appraiser._regret_prompt_kwargs() == {
        "regret_corrections_block": "", "regret_kind_block": "",
        "open_kinds": "hope|worry|taste|wonder", "regret_never_line": "",
    }


def test_template_carries_the_view_slots_with_zero_byte_collapse():
    text = Path("prompts/stake_appraisal.txt").read_text(encoding="utf-8")
    for slot in ("{view_kind_block}", "{view_never_line}", "{view_ops_hint}"):
        assert text.count(slot) == 1, slot
    # exact placement: each view slot rides its regret twin, same line
    assert "{regret_kind_block}{view_kind_block}- NEVER keep:" in text
    assert "{regret_never_line}{view_never_line}\n" in text
    # the hint owns column 0 of the update line (empty = today's bytes)
    assert ('"horizon": "YYYY-MM-DD or null"}},\n'
            '{view_ops_hint}    {{"op": "update", "id": 3,') in text


# ── _parse_ops carries the two new keys ────────────────────────────────


def test_parse_ops_passes_evidence_and_reason_through():
    raw = ('{"ops": [{"op": "open", "kind": "view", "stake": "s", '
           '"evidence": "e", "horizon": null}, '
           '{"op": "update", "id": 3, "stake": "s2", "reason": "he showed me"}]}')
    ops = stake_appraiser._parse_ops(raw)
    assert ops[0]["evidence"] == "e"
    assert ops[1]["reason"] == "he showed me"


def test_parse_ops_still_drops_everything_off_the_whitelist():
    raw = ('{"ops": [{"op": "open", "kind": "view", "stake": "s", "evidence": "e", '
           '"horizon_at": "poison", "agenda": "raise it tomorrow"}]}')
    ops = stake_appraiser._parse_ops(raw)
    assert "agenda" not in ops[0]
    assert ops[0]["horizon_at"] is None      # the parsed one, never the raw string


# ── the pressure line tells the truth: views are weightless ────────────


@pytest.mark.asyncio
async def test_open_count_counts_only_non_view_stakes(monkeypatch):
    """Views live outside the shared cap (task 2), so counting them would
    tell her the ledger is fuller than it is and squeeze out a real hope."""
    async def _fake_open_stakes(**kwargs):
        return [_stake(id=4), _stake(id=2, kind="hope", evidence="",
                                     stake="the survey comes back clean")]

    monkeypatch.setattr(stake_ledger, "open_stakes", _fake_open_stakes)
    monkeypatch.setattr(stake_ledger, "_max_open", lambda: 12)
    calls = _mock_llm(monkeypatch, '{"ops": []}')
    summary = await stake_appraiser.appraise_exchange(
        user_message="u", palli_reply="p", brain_client=object(),
    )
    prompt = calls[0]["messages"][0]["content"]
    assert "PALLI'S CURRENTLY OPEN STAKES (1 of 12 slots used; views hold no slot):" in prompt
    assert "[id 4] (view)" in prompt and "[id 2] (hope)" in prompt
    # and the empty-ops early return matches apply_ops' shape (dormant too)
    assert summary == {"applied": 0, "rejected": 0, "opened": [],
                       "closed": [], "dormant": []}


# ── the view tells ride the id-bearing render for free ─────────────────


def test_view_tells_ride_the_appraisers_render():
    said_at = 1756600000.0
    lines = stake_appraiser._render_open_stakes_for_prompt([
        _stake(id=4, muted_at=1000.0, voiced_at=said_at),
        _stake(id=5, stake="the second supplier's quote is the honest one",
               voiced_at=said_at),
        _stake(id=6, stake="a view she has never said"),
    ]).split("\n")
    assert lines[0].endswith(" [held quietly — they asked to leave it]")  # mute wins
    stamp = time.strftime("%m-%d", time.localtime(said_at))
    assert lines[1].endswith(f" [last said {stamp}]")
    assert lines[2].endswith("a view she has never said")
