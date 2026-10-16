"""The reply-length governor (2026-08-22) — the register fix.

Measured 08-03→08-22 (272 cadence votes matched to her replies): "short"
votes ran median 33 words but a third blew past 50 (p90 123); "medium"
("a few sentences, one small paragraph") ran median 119 and over half
blew past 110 (p90 266). The vote arrived with its definition and was
still a suggestion she read and ignored. Owner's rule: budgets are code.

Laws pinned here:
- the caps are code (short ≤ 50 words, medium ≤ 110, long uncapped);
- the meaning decision stays with the LLM: over the cap → ONE compression
  call in her voice; small overshoots (≤ TOLERANCE × cap) pass untouched and
  nothing is ever trimmed — a still-long compression is sent as the shorter text;
- a reply with a code block is never touched; an unvoted turn is untouched;
- the shape travels by context variable (the runner sets it per turn);
- flag-off is byte-identical (companion.reply_governor).
"""
from __future__ import annotations

import asyncio

import pytest

from core.agent import reply_governor as rg


SHORT_OVER = ("I think the honest answer is that the build landed cleanly and the tests are green, "
              "so there is nothing for you to do tonight except rest; the forge tool survived its first "
              "night without a single error, the reminder you set fired on time, and I kept an eye on the "
              "stake review as well, which finally ran the way it was supposed to — all good here.")  # 70 words


def test_caps_are_code():
    assert rg.cap_for("short") == 50
    assert rg.cap_for("medium") == 110
    assert rg.cap_for("long") is None
    assert rg.cap_for("") is None and rg.cap_for(None) is None


def test_word_count_and_code_block_exemption():
    assert rg.word_count("one two  three\nfour") == 4
    assert rg.has_code_block("here:\n```py\nx=1\n```") is True
    assert rg.has_code_block("plain") is False
    assert rg.needs_governing(SHORT_OVER, "short") is True
    assert rg.needs_governing("short and sweet.", "short") is False
    assert rg.needs_governing(SHORT_OVER + "\n```\ncode\n```", "short") is False
    assert rg.needs_governing(SHORT_OVER, "long") is False
    assert rg.needs_governing(SHORT_OVER, "") is False


def test_trim_keeps_whole_sentences_up_to_the_cap():
    text = "First sentence here. Second sentence is a bit longer than the first one. Third sentence closes it."
    out = rg.trim_to_cap(text, 10)
    assert out == "First sentence here."
    # the first sentence alone over the cap: hard cut at the cap, marked
    long_first = " ".join(f"w{i}" for i in range(30)) + ". Tail."
    cut = rg.trim_to_cap(long_first, 10)
    assert rg.word_count(cut) <= 11 and cut.endswith("…")


@pytest.mark.asyncio
async def test_over_cap_is_compressed_by_the_llm_first():
    calls = []

    async def compress(text, cap):
        calls.append((text, cap))
        return "Build landed, tests green — nothing for you tonight but rest."

    res = await rg.govern_reply(SHORT_OVER, "short", compress=compress)
    assert res.action == "compressed" and res.before_words > 50 and res.after_words <= 50
    assert calls and calls[0][1] == 50
    assert res.text.startswith("Build landed")


@pytest.mark.asyncio
async def test_a_failed_compression_sends_the_whole_reply_never_a_trim():
    """DEFECT_RECORD §21 (2026-09-02): three failed compression calls (an
    empty answer, two timeouts) ended in a sentence-boundary trim that cut
    a 310-word reply to 83 — the last-resort trim was the FIRST thing that
    happened to his content. When the meaning half fails, the presentation
    half does not get to guess: the reply goes out whole."""
    async def nothing(text, cap):
        return None

    res = await rg.govern_reply(SHORT_OVER, "short", compress=nothing)
    assert res.action == "kept_whole" and res.text == SHORT_OVER and res.after_words == res.before_words

    async def boom(text, cap):
        raise TimeoutError("compression lane timed out")

    res2 = await rg.govern_reply(SHORT_OVER, "short", compress=boom)
    assert res2.action == "kept_whole" and res2.text == SHORT_OVER


@pytest.mark.asyncio
async def test_a_still_long_compression_is_sent_as_the_shorter_text_never_trimmed():
    """2026-09-07 (his call): four trims since 09-03 each took the last sentence —
    the punchline — off a reply that was only somewhat over the cap. When
    compression answers but still overshoots, she sends whichever is shorter
    (the candidate, if it is shorter than what she wrote); nothing is ever cut
    at a sentence boundary any more."""
    shorter_but_over = " ".join(SHORT_OVER.split()[:60])          # 60 words: over the 50 cap, under the original 70

    async def still_long(text, cap):
        return shorter_but_over

    res = await rg.govern_reply(SHORT_OVER, "short", compress=still_long)
    assert res.action == "compressed" and res.text == shorter_but_over and res.after_words == 60

    async def longer(text, cap):
        return SHORT_OVER + " And one more clause for good measure."

    res2 = await rg.govern_reply(SHORT_OVER, "short", compress=longer)
    assert res2.action == "kept_whole" and res2.text == SHORT_OVER


def test_small_overshoots_pass_untouched_within_the_tolerance_band():
    """Up to TOLERANCE × cap needs no governing at all (121 words vs a 110 cap was
    the 09-03 16:55 trim); above it, compression still runs; the 3× floor stays."""
    medium_121 = " ".join(["word"] * 121)
    assert not rg.needs_governing(medium_121, "medium")            # 1.10× → untouched
    assert rg.needs_governing(" ".join(["word"] * 150), "medium")  # 1.36× → compress
    assert not rg.needs_governing(" ".join(["word"] * 340), "medium")   # 3.1× → the floor: the vote was wrong
    assert rg.TOLERANCE == 1.35


@pytest.mark.asyncio
async def test_the_pre_governed_text_is_kept_in_the_debug_log(caplog):
    """§21: whatever the governor does to a reply, the reply as she wrote it
    survives in the debug log — the trimmed 310 words on 09-01 were only
    recoverable from the verbose request log."""
    import logging

    async def compress(text, cap):
        return "Build landed, tests green — nothing for you tonight but rest."

    with caplog.at_level(logging.DEBUG, logger="core.agent.reply_governor"):
        await rg.govern_reply(SHORT_OVER, "short", compress=compress)
    assert any("before" in r.getMessage() and SHORT_OVER in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_under_cap_and_unvoted_are_kept_without_a_call():
    async def boom(text, cap):
        raise AssertionError("must not be called")

    kept = await rg.govern_reply("Short and sweet.", "short", compress=boom)
    assert kept.action == "kept" and kept.text == "Short and sweet."
    unvoted = await rg.govern_reply(SHORT_OVER, "", compress=boom)
    assert unvoted.action == "kept" and unvoted.text == SHORT_OVER
    uncapped = await rg.govern_reply(SHORT_OVER, "long", compress=boom)
    assert uncapped.action == "kept"


def test_the_shape_travels_by_context_variable_and_defaults_to_nothing():
    assert rg.current_reply_shape.get() == ""
    token = rg.current_reply_shape.set("medium")
    try:
        assert rg.current_reply_shape.get() == "medium"
    finally:
        rg.current_reply_shape.reset(token)
    assert rg.current_reply_shape.get() == ""


def test_the_trailing_shape_note_is_short_and_names_the_cap():
    note = rg.render_shape_note("short")
    assert "short" in note and "50" in note and len(note) < 200
    assert rg.render_shape_note("long") == ""
    assert rg.render_shape_note("") == ""


def test_the_flag_exists_and_defaults_off():
    import core.settings_schema as ss
    cls = next(c for n, c in vars(ss).items() if isinstance(c, type) and "fears" in getattr(c, "model_fields", {}))
    assert "reply_governor" in cls.model_fields
    assert cls.model_fields["reply_governor"].default is False


# ── DEFECT_RECORD §14 (2026-09-01): the shrink-ray ──────────────────────────
# A 490-word transcript compressed to 40 words; a 535-word correction
# compressed to 26 and inverted. The floor is code: a reply that overshoots
# its cap by 3× or more is proof the VOTE was wrong, not the reply.

def test_overshoot_floor_passes_a_wildly_long_reply_through_untouched():
    long_reply = " ".join(["word"] * 490)          # 490 words vs the short cap of 50
    assert rg.needs_governing(long_reply, "short") is False
    assert rg.needs_governing(long_reply, "medium") is False   # 490 > 110 × 3


def test_mild_overshoot_is_still_governed():
    mild = " ".join(["word"] * 120)                # 120 words: over 50, under 150
    assert rg.needs_governing(mild, "short") is True
    assert rg.needs_governing(" ".join(["word"] * 149), "short") is True
    assert rg.needs_governing(" ".join(["word"] * 150), "short") is False   # the floor is ≥ 3×


def test_compression_prompt_carries_the_meaning_laws():
    p = rg.compression_prompt(50, "short")
    for law in ("who said", "verbatim", "apolog"):
        assert law in p, f"missing law fragment: {law!r}"


def test_cadence_length_rule_names_content_delivery_as_long():
    from core.agent.cadence import SHAPE_RESPONSE_PROMPT
    assert "transcript" in SHAPE_RESPONSE_PROMPT
    assert "verbatim" in SHAPE_RESPONSE_PROMPT


def test_the_shape_note_governs_her_words_not_whether_she_acts():
    """2026-09-02 21:19, on the desktop's Qwen: his 'i didnt actually get that
    attachment' was voted short; the trailing 'Hard shape' note was the last
    thing she read, and she answered in one line with a fake '[Attachment: …]'
    instead of calling deliver_artifact — 23 tools offered, none used. A small
    model follows the last hard instruction. The note must say what it governs."""
    note = rg.render_shape_note("short")
    assert "words" in note and "tool" in note.lower()
    assert "always allowed" in note
    # REWRITTEN line (Batch 7, her voice — audit 4.9): was
    # `render_shape_note("medium").count("Hard shape") == 1`. The note is a
    # plain sentence now; the tools clause is what this incident needs, on
    # both capped lengths.
    medium = rg.render_shape_note("medium")
    assert "Hard shape" not in medium
    assert "words" in medium and "tool" in medium.lower() and "always allowed" in medium


# ── Batch 7, her voice (audit 4.9): the length note is plain words ─────────

def test_the_length_note_is_one_plain_sentence():
    for length, says in (("short", "short"), ("medium", "few sentences")):
        note = rg.render_shape_note(length)
        assert note.startswith("(") and note.endswith(")"), note
        assert "Hard shape" not in note and "register:" not in note, note
        assert "[reply shape" not in note, note
        assert says in note.lower(), note


def test_the_short_note_defines_short():
    """Fix round 1 (C15.6): with the directive's length line gone, the shape
    note is the only place "short" is defined. Pin the definition."""
    note = rg.render_shape_note("short")
    assert "a line or two" in note and "under 50 words" in note, note


def test_the_word_caps_still_hold_in_code(monkeypatch):
    """A guard, already green before this change: the caps are code, and the
    note's number is read from them, never typed into the sentence."""
    from core.agent.reply_governor import cap_for
    assert cap_for("short") == 50 and cap_for("medium") == 110
    monkeypatch.setitem(rg.WORD_CAPS, "short", 40)
    assert "40" in rg.render_shape_note("short") and "50" not in rg.render_shape_note("short")
