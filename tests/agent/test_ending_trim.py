"""The tacked-on ending (the owner, 2026-10-12: "is there any way to get palli
to not send weird follow ups like that at the end of its messages? its really
unneccessary" ... "yes, build the trim into the check").

Rewording her prompt made no measurable difference in the replay; a judge
that marks where a tacked-on ending starts, and code that cuts there, did. The
judge is the existing pre-send check (core/agent/promise_gate.py, one call,
a third question); the cut is code, at the end only, and small.
"""
from __future__ import annotations

import pytest

from core.agent import promise_gate as pg


# ── the cut ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("draft, cut_from, kept", [
    ("Good. Whenever you want the full text of any of those, say the word — the Cyber one "
     "especially. No rush from me.", "Whenever you want the full text", "Good."),
    ("Good. Four live doors and one of them's your bullseye — the Cyber req. Say the word if "
     "you want a read on which to hit first.", "Say the word if you want", "Good. Four live doors "
     "and one of them's your bullseye — the Cyber req."),
    ("Fair enough is a good place to leave it. I'll be here if you start that application.",
     "I'll be here if you start", "Fair enough is a good place to leave it."),
    ("That Cyber req is the one shaped like your resume — say the word and I'll help you sharpen it.",
     "say the word and I'll help", "That Cyber req is the one shaped like your resume."),
])
def test_the_ending_is_cut_where_the_judge_says(draft, cut_from, kept):
    out = pg.trim_ending(draft, cut_from)
    assert out is not None and out[0] == kept
    assert draft.startswith(out[0].rstrip("."))


def test_spacing_case_and_apostrophes_do_not_hide_the_ending():
    draft = "Good. I’ll be here   if you start that application."
    assert pg.trim_ending(draft, "i'll be here if you start")[0] == "Good."


def test_the_last_occurrence_is_the_one_cut():
    draft = "Say the word to her, I said. Good. Say the word if you want more."
    assert pg.trim_ending(draft, "Say the word")[0] == "Say the word to her, I said. Good."


@pytest.mark.parametrize("draft, cut_from", [
    ("Right here. What's up?", ""),                                   # nothing marked
    ("Right here. What's up?", "Right here. What's up?"),               # the whole reply
    ("Right here. What's up?", "not in the reply at all"),             # words she never wrote
    ("Here we go again.", "go again"[:2]),                             # too short to trust
    ("Okay. A long ending " + "that keeps going and going " * 12 + "until the end.",
     "A long ending"),                                                  # too much
    ("Okay. One. Two. Three.", "One."),                                 # more than two sentences
    ("Reading this carefully", "ading this"),                           # mid-word
])
def test_an_unsafe_cut_is_refused(draft, cut_from):
    assert pg.trim_ending(draft, cut_from) is None


# ── Fable's review (2026-10-12) ─────────────────────────────────────────────

@pytest.mark.parametrize("draft, mark", [
    ("There's none left in the queue, but the mirror has a copy. Grab it from there.", "none"),
    ("I looked and found nothing wrong with the config. Port 22 is open.", "found nothing"),
    ("I looked and found nothing wrong with the config. Port 22 is open.", "nothing wrong with"),
    ("The field came back null after the reset. It works now.", "null"),
    ("The folder is empty. Want me to fill it?", "empty"),
])
def test_a_placeholder_or_a_mark_inside_a_sentence_cuts_nothing(draft, mark):
    assert pg.trim_ending(draft, mark) is None


@pytest.mark.parametrize("draft, kept", [
    ("The firewall allows it.\n\n*Say the word and I'll pull the logs.*", "The firewall allows it."),
    ("Port 22 is open.\n\n**Want me to restart it?**", "Port 22 is open."),
    ('Port 22 is open. "Say the word and I\'ll pull the logs."', "Port 22 is open."),
    ("Port 22 is open.\n> Say the word and I'll pull the logs.", "Port 22 is open."),
    ("Port 22 is open.\n• Say the word and I'll pull the logs.", "Port 22 is open."),
    ("1. Port 22 is open.\n2. Say the word and I'll pull the logs.", "1. Port 22 is open."),
])
def test_an_ending_s_own_opening_marks_go_with_it(draft, kept):
    mark = "Want me to restart" if "Want me" in draft else "Say the word and I'll"
    out = pg.trim_ending(draft, mark)
    assert out is not None and out[0] == kept


def test_a_mark_inside_a_code_block_is_refused():
    draft = "Run this:\n```\necho hi\nsay the word\n```"
    assert pg.trim_ending(draft, "say the word") is None


@pytest.mark.asyncio
async def test_with_grounding_off_the_mark_still_comes_back(monkeypatch):
    import ai.llm_client as llm_mod

    async def fake(**kw):
        return ('{"empty_promise": false, "ungrounded": true, "unsupported": "x", '
                '"tacked_on_from": "Say the word", "why": "y"}')

    monkeypatch.setattr(llm_mod.llm, "chat_completion", fake)
    v = await pg.check_empty_promise(user_text="hi", draft="Good. Say the word.", tool_names=[],
                                     grounding=False)
    assert v is not None and v.ungrounded is False and v.tacked_on_from == "Say the word"


def test_the_prompt_keeps_why_for_the_first_two_questions():
    from pathlib import Path

    text = Path(__file__).resolve().parents[2].joinpath(
        "prompts", "promise_check.txt").read_text(encoding="utf-8")
    assert '"why" below is about questions 1 and 2 only' in text


# ── the judge's answer ──────────────────────────────────────────────────────

def test_the_judge_answer_carries_the_ending():
    v = pg._parse('{"empty_promise": false, "ungrounded": false, "failed_look": false, '
                  '"tacked_on_from": "Say the word", "why": "x"}')
    assert v is not None and v.tacked_on_from == "Say the word" and not v.flagged


def test_an_older_answer_without_the_field_still_parses():
    v = pg._parse('{"empty_promise": false, "ungrounded": false, "why": "x"}')
    assert v is not None and v.tacked_on_from == ""


def test_the_judge_is_asked_the_third_question():
    from pathlib import Path

    text = Path(__file__).resolve().parents[2].joinpath(
        "prompts", "promise_check.txt").read_text(encoding="utf-8")
    assert "QUESTION 3" in text and '"tacked_on_from"' in text


def test_the_switch_reads_on_unless_turned_off(monkeypatch):
    from types import SimpleNamespace as NS

    import core.settings_boot as sb

    monkeypatch.setattr(sb.settings_service, "snapshot",
                        lambda: NS(features=NS(ending_trim=False)))
    assert pg.trim_enabled() is False
    monkeypatch.setattr(sb.settings_service, "snapshot", lambda: NS(features=NS()))
    assert pg.trim_enabled() is True

    def broken():
        raise RuntimeError("unreadable")

    monkeypatch.setattr(sb.settings_service, "snapshot", broken)
    assert pg.trim_enabled() is True


def test_the_setting_exists_and_defaults_on():
    from core.settings_schema import FeaturesConfig

    assert FeaturesConfig().ending_trim is True


# ── the runner sends the trimmed reply ──────────────────────────────────────

def _harness():
    from tests.agent import test_promise_gate as tpg

    return tpg


def test_the_runner_sends_and_keeps_the_trimmed_reply(tmp_path, monkeypatch):
    import core.agent.llm_client as llm_mod
    import core.agent.session as sess_mod

    tpg = _harness()
    mod = tpg._load_runner()
    draft = "Good. Say the word if you want the full posting."
    fake_call, calls = tpg._scripted_llm(llm_mod, sess_mod, [draft])
    verdict = pg.PromiseVerdict(empty_promise=False, tacked_on_from="Say the word if you want")
    result, delivered = tpg._run(mod, tmp_path, fake_call, monkeypatch, judge=tpg._judge_saying(verdict))
    assert delivered == "Good."
    assert result.response_text == "Good."
    assert tpg._assistant_texts(sess_mod, tmp_path) == ["Good."]     # her record matches what was sent
    assert len(calls) == 1                                            # no extra round


def test_with_the_switch_off_the_reply_is_untouched(tmp_path, monkeypatch):
    import core.agent.llm_client as llm_mod
    import core.agent.session as sess_mod

    tpg = _harness()
    mod = tpg._load_runner()
    monkeypatch.setattr(pg, "trim_enabled", lambda: False)
    draft = "Good. Say the word if you want the full posting."
    fake_call, _ = tpg._scripted_llm(llm_mod, sess_mod, [draft])
    verdict = pg.PromiseVerdict(empty_promise=False, tacked_on_from="Say the word if you want")
    _, delivered = tpg._run(mod, tmp_path, fake_call, monkeypatch, judge=tpg._judge_saying(verdict))
    assert delivered == draft


def test_a_held_back_draft_is_not_trimmed_its_second_try_goes_out(tmp_path, monkeypatch):
    """A flagged draft is withheld as before; the trim never runs on it."""
    import core.agent.llm_client as llm_mod
    import core.agent.session as sess_mod

    tpg = _harness()
    mod = tpg._load_runner()
    fake_call, calls = tpg._scripted_llm(llm_mod, sess_mod, [tpg.PROMISE, ("tool", "building it"), tpg.KEPT])
    verdict = pg.PromiseVerdict(empty_promise=True, promised="the link", why="nothing ran",
                                tacked_on_from="the link")
    _, delivered = tpg._run(mod, tmp_path, fake_call, monkeypatch, judge=tpg._judge_saying(verdict))
    assert delivered == tpg.KEPT and len(calls) == 3


def test_an_unsafe_mark_leaves_the_reply_alone(tmp_path, monkeypatch):
    import core.agent.llm_client as llm_mod
    import core.agent.session as sess_mod

    tpg = _harness()
    mod = tpg._load_runner()
    draft = "Right here. What's up?"
    fake_call, _ = tpg._scripted_llm(llm_mod, sess_mod, [draft])
    verdict = pg.PromiseVerdict(empty_promise=False, tacked_on_from="Right here. What's up?")
    _, delivered = tpg._run(mod, tmp_path, fake_call, monkeypatch, judge=tpg._judge_saying(verdict))
    assert delivered == draft


# ── a promise that is only a tacked-on offer ────────────────────────────────

def test_a_promise_only_in_the_ending_is_cut_not_held_back(tmp_path, monkeypatch):
    import core.agent.llm_client as llm_mod
    import core.agent.session as sess_mod

    tpg = _harness()
    mod = tpg._load_runner()
    draft = "Anytime. Let me know which one you're going for and I'll pull the req details."
    fake_call, calls = tpg._scripted_llm(llm_mod, sess_mod, [draft])
    verdict = pg.PromiseVerdict(empty_promise=True, promised="the req details", why="nothing ran",
                                tacked_on_from="Let me know which one", promise_in_ending=True,
                                promise_from="Let me know which one you're")
    result, delivered = tpg._run(mod, tmp_path, fake_call, monkeypatch, judge=tpg._judge_saying(verdict))
    assert delivered == "Anytime." and len(calls) == 1


@pytest.mark.parametrize("extra", [
    {"promise_in_ending": False},                                   # the promise is elsewhere
    {"promise_in_ending": True, "ungrounded": True, "unsupported": "x"},   # a claim too
])
def test_any_other_promise_or_a_claim_is_still_held_back(tmp_path, monkeypatch, extra):
    import core.agent.llm_client as llm_mod
    import core.agent.session as sess_mod

    tpg = _harness()
    mod = tpg._load_runner()
    fake_call, calls = tpg._scripted_llm(llm_mod, sess_mod, [tpg.PROMISE, ("tool", "building it"), tpg.KEPT])
    verdict = pg.PromiseVerdict(empty_promise=True, promised="the link", why="nothing ran",
                                tacked_on_from="the link", **extra)
    _, delivered = tpg._run(mod, tmp_path, fake_call, monkeypatch, judge=tpg._judge_saying(verdict))
    assert delivered == tpg.KEPT and len(calls) == 3


def test_promise_in_ending_means_nothing_without_a_promise():
    v = pg._parse('{"empty_promise": false, "ungrounded": false, "promise_in_ending": true, '
                  '"tacked_on_from": "Say the word", "why": "x"}')
    assert v.promise_in_ending is False


# ── Fable's final read: the promise must be shown inside the cut ────────────

@pytest.mark.parametrize("draft, mark, promise_from", [
    ("I'll pull the req details once you pick one. Onward and upward!",
     "Onward and upward", "I'll pull the req details"),                  # promise in the body
    ("I'll ping you when it's done. Let me know which one and I'll pull the req details.",
     "Let me know which", "I'll ping you when"),                         # two promises
    ("Done. Let me know which one and I'll pull it. Let me know if that works.",
     "Let me know", "Let me know which one"),                            # the mark recurs
    ("I'll pull the req details as soon as you say the word — just tell me which one.",
     "just tell me which", "I'll pull the req details"),                 # mid-promise mark
    ("Anytime. Let me know which one and I'll pull the req details.",
     "Let me know which one", ""),                                       # no quote at all
    ("Anytime. Let me know which one and I'll pull the req details.",
     "Let me know which one", "I will fetch them"),                      # a misquote
])
def test_a_flag_the_code_cannot_confirm_is_still_held_back(tmp_path, monkeypatch, draft, mark,
                                                           promise_from):
    import core.agent.llm_client as llm_mod
    import core.agent.session as sess_mod

    tpg = _harness()
    mod = tpg._load_runner()
    fake_call, calls = tpg._scripted_llm(llm_mod, sess_mod, [draft, ("tool", "working"), tpg.KEPT])
    verdict = pg.PromiseVerdict(empty_promise=True, promised="the req details", why="nothing ran",
                                tacked_on_from=mark, promise_in_ending=True,
                                promise_from=promise_from)
    _, delivered = tpg._run(mod, tmp_path, fake_call, monkeypatch, judge=tpg._judge_saying(verdict))
    assert delivered == tpg.KEPT and len(calls) == 3


def test_promise_only_in_ending_checks_both_sides():
    assert pg.promise_only_in_ending("Anytime.", "Let me know which one and I'll pull it.",
                                     "Let me know which")
    assert not pg.promise_only_in_ending("I'll pull it. Anytime.", "Let me know which one.",
                                         "I'll pull it")
    assert not pg.promise_only_in_ending("Anytime.", "Let me know.", "")


@pytest.mark.parametrize("draft, kept", [
    ("**Port 22 is open.** Say the word and I'll pull the logs.", "**Port 22 is open.**"),
    ("*Port 22 is open.* Say the word and I'll pull the logs.", "*Port 22 is open.*"),
    ('She said "go." Say the word and I\'ll pull the logs.', 'She said "go."'),
    ('He said "fine" — say the word and I\'ll pull the logs.', 'He said "fine".'),
])
def test_closing_marks_stay(draft, kept):
    out = pg.trim_ending(draft, "say the word and I'll")
    assert out is not None and out[0] == kept


def test_with_grounding_off_the_promise_fields_come_back_too():
    v = pg.PromiseVerdict(empty_promise=True, promise_in_ending=True, promise_from="Let me know",
                          tacked_on_from="Let me know")
    assert v.promise_from == "Let me know" and v.promise_in_ending
