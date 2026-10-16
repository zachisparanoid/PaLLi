from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass

import pytest


def _load():
    from tests.agent.conftest import load_module
    return load_module("core/agent/cadence.py", "agent_cadence_under_test")


@dataclass
class FakeCtx:
    surface: str = "discord_text"
    turn_origin: str = "user"
    user_text: str = "hey"
    recent_history_text: str = ""


def test_cadence_config_directive_renders_its_sentences_not_its_fields():
    """REWRITTEN (was test_cadence_config_directive_renders_all_fields, which
    pinned every field label). Batch 7, her voice (audit 4.9): the directive
    says what the vote means in plain sentences; the fields stay on the
    config for the code that reads them."""
    mod = _load()
    cfg = mod.CadenceConfig(
        should_respond=True, register="depth_inquiry", length="medium",
        phase="settled_conversation", target_beats=2,
        observation_text="user processing tough news",
        directive="",
    )
    rendered = mod.render_cadence_directive(cfg)
    assert "two or three messages" in rendered         # the beats, as a sentence
    assert "tough news" in rendered                    # the observation
    for label in ("depth_inquiry", "settled_conversation", "register:", "phase:"):
        assert label not in rendered, label
    assert (cfg.register, cfg.phase, cfg.target_beats) == ("depth_inquiry", "settled_conversation", 2)


def test_shape_response_calls_llm_and_parses_json():
    mod = _load()
    captured = {}

    async def fake_llm(prompt: str) -> str:
        captured["prompt"] = prompt
        return json.dumps({
            "should_respond": True,
            "register": "casual_greeting",
            "length": "short",
            "phase": "opening",
            "target_beats": 1,
            "observation_text": "user said hi"
        })

    cfg = asyncio.run(mod.shape_response(FakeCtx(user_text="hello"), llm=fake_llm))
    assert cfg.should_respond is True
    assert cfg.register == "casual_greeting"
    assert cfg.length == "short"
    assert cfg.target_beats == 1
    assert "hello" in captured["prompt"]


def test_shape_response_falls_back_on_llm_error():
    mod = _load()

    async def broken_llm(prompt): raise RuntimeError("boom")

    cfg = asyncio.run(mod.shape_response(FakeCtx(user_text="hi"), llm=broken_llm))
    assert cfg.should_respond is True
    assert cfg.register == "neutral"
    assert cfg.target_beats == 1


def test_shape_response_falls_back_on_invalid_json():
    mod = _load()

    async def bad_json_llm(prompt): return "not json at all"

    cfg = asyncio.run(mod.shape_response(FakeCtx(user_text="hi"), llm=bad_json_llm))
    assert cfg.should_respond is True
    assert cfg.register == "neutral"


def test_judgment_pass_returns_cancel_for_stop_words():
    mod = _load()
    async def llm(prompt):
        return json.dumps({"decision": "cancel", "acknowledgment": "got it, dropping it"})

    decision = asyncio.run(mod.judgment_pass(
        in_flight_summary="researching the weather",
        new_input="stop",
        llm=llm,
    ))
    assert decision.mode == "cancel"
    assert "got it" in decision.acknowledgment


def test_judgment_pass_returns_sidebar():
    mod = _load()
    async def llm(prompt):
        return json.dumps({"decision": "sidebar", "acknowledgment": "real quick: 4."})

    decision = asyncio.run(mod.judgment_pass(
        in_flight_summary="writing a poem",
        new_input="what's 2 plus 2?",
        llm=llm,
    ))
    assert decision.mode == "sidebar"
    assert "4" in decision.acknowledgment


def test_judgment_pass_falls_back_on_error():
    mod = _load()
    async def broken(prompt): raise RuntimeError("nope")
    decision = asyncio.run(mod.judgment_pass(
        in_flight_summary="x", new_input="y", llm=broken,
    ))
    assert decision.mode == "ack_silent"


def test_judgment_pass_invalid_mode_falls_back():
    mod = _load()
    async def llm(prompt): return json.dumps({"decision": "foobar", "acknowledgment": ""})
    decision = asyncio.run(mod.judgment_pass(
        in_flight_summary="x", new_input="y", llm=llm,
    ))
    assert decision.mode == "ack_silent"


def test_single_flow_chunker_emits_on_paragraph():
    mod = _load()
    sent = []
    async def on_chunk(s): sent.append(s)
    chunker = mod.SingleFlowChunker(on_chunk)
    asyncio.run(chunker.feed("Hello there. "))
    asyncio.run(chunker.feed("How are you?\n\n"))
    asyncio.run(chunker.feed("Tell me more."))
    asyncio.run(chunker.flush())
    joined = "".join(sent)
    assert "Hello there" in joined
    assert "Tell me more" in joined


def test_cadenced_beat_chunker_emits_per_beat():
    mod = _load()
    sent = []
    async def on_chunk(s):
        sent.append(s)
    chunker = mod.CadencedBeatChunker(on_chunk, target_beats=2, beat_pause_ms=10)
    # First beat: short statement.
    asyncio.run(chunker.feed("I hear you. "))
    asyncio.run(chunker.feed("That's a lot to carry.\n\n"))
    # Second beat
    asyncio.run(chunker.feed("Want to sit with it for a moment?"))
    asyncio.run(chunker.flush())
    assert len(sent) >= 2


def test_voice_flow_chunker_yields_one_continuous_stream():
    mod = _load()
    sent = []
    async def on_chunk(s): sent.append(s)
    chunker = mod.VoiceFlowChunker(on_chunk)
    asyncio.run(chunker.feed("Word by word "))
    asyncio.run(chunker.feed("we go."))
    asyncio.run(chunker.flush())
    # Voice chunker passes through everything without splitting into beats
    assert "".join(sent) == "Word by word we go."


def test_build_chunker_picks_correct_class():
    mod = _load()
    async def cb(s): pass
    c1 = mod.build_chunker(target_beats=1, surface="discord_text", on_chunk=cb)
    c2 = mod.build_chunker(target_beats=2, surface="discord_text", on_chunk=cb)
    cv = mod.build_chunker(target_beats=2, surface="voice_local", on_chunk=cb)
    assert isinstance(c1, mod.SingleFlowChunker)
    assert isinstance(c2, mod.CadencedBeatChunker)
    assert isinstance(cv, mod.VoiceFlowChunker)


def test_shape_response_parses_emotional_context():
    mod = _load()

    async def with_tag(prompt: str) -> str:
        return json.dumps({
            "should_respond": True, "register": "support", "length": "short",
            "phase": "settled_conversation", "target_beats": 1,
            "observation_text": "x", "emotional_context": "playful_banter",
        })

    cfg = asyncio.run(mod.shape_response(FakeCtx(), llm=with_tag))
    assert cfg.emotional_context == "playful_banter"


def test_shape_response_emotional_context_defaults_neutral_when_absent():
    mod = _load()

    async def without_tag(prompt: str) -> str:
        return json.dumps({
            "should_respond": True, "register": "neutral", "length": "short",
            "phase": "opening", "target_beats": 1, "observation_text": "x",
        })

    cfg = asyncio.run(mod.shape_response(FakeCtx(), llm=without_tag))
    assert cfg.emotional_context == "neutral"


# ---------------------------------------------------------------------------
# 2026-07-11 brevity work: the length vote must arrive at the responder
# DEFINED and PRIORITIZED. Measured before this change: replies ran a median
# 5.7x the owner's message length because the directive was a bare
# "length: short" with no definition, no default, and no enforcement.
# ---------------------------------------------------------------------------


def _cfg(mod, **overrides):
    base = dict(
        should_respond=True, register="neutral", length="short",
        phase="settled_conversation", target_beats=1,
        observation_text="quick check-in", directive="",
    )
    base.update(overrides)
    return mod.CadenceConfig(**base)


def test_short_and_medium_live_in_the_shape_note_not_the_directive():
    """REWRITTEN (was test_directive_defines_short_as_a_hard_shape and
    test_directive_defines_medium_and_long). Batch 7, her voice (audit 4.9):
    short and medium are said once, in the trailing shape note
    (reply_governor.render_shape_note, which names the cap); the directive
    carries no second copy of them. Fix round 1 (C15.6): both halves of the
    name are asserted — the note defines each length, the directive never
    does."""
    from core.agent import reply_governor

    mod = _load()
    definitions = {"short": ("a line or two", "under 50 words"),
                   "medium": ("a few sentences", "under 110 words")}
    for length, (definition, cap) in definitions.items():
        note = reply_governor.render_shape_note(length)            # lives in the shape note
        assert definition in note.lower() and cap in note, (length, note)
        rendered = mod.render_cadence_directive(_cfg(mod, length=length, observation_text=""))
        assert rendered == "", (length, rendered)                  # not in the directive
        rendered = mod.render_cadence_directive(_cfg(mod, length=length))
        assert rendered == "## This turn\nquick check-in", (length, rendered)
        for word in (definition, "sentence", "words", length):
            assert word not in rendered.lower(), (length, word, rendered)


def test_long_is_one_plain_sentence():
    """REWRITTEN (the "long" half of test_directive_defines_medium_and_long).
    A long vote gets no shape note (no cap), so the directive's sentence is
    the only permission she has to run long (a transcript, a file's text:
    DEFECT §14). Fix round 1 (C15.1): no "they asked for it" — the vote also
    runs on workbench, monologue and proactive turns, where nobody asked."""
    mod = _load()
    rendered = mod.render_cadence_directive(_cfg(mod, length="long", observation_text=""))
    assert rendered == "## This turn\nThis one can run long."


def test_directive_unknown_length_adds_nothing():
    """REWRITTEN (was test_directive_unknown_length_degrades_to_bare_line,
    which pinned "length: verbose"). An unknown length: no crash, no label,
    no invented prose."""
    mod = _load()
    rendered = mod.render_cadence_directive(_cfg(mod, length="verbose", observation_text=""))
    assert rendered == ""
    assert mod.render_cadence_directive(_cfg(mod, length="verbose")) == "## This turn\nquick check-in"


# ── Batch 7, her voice (audit 4.9): plain sentences, never a machine form ──

def test_the_cadence_directive_is_not_a_machine_form():
    mod = _load()
    cfg = _cfg(mod, length="long", target_beats=2,
               observation_text="They sound tired and want it quick.")
    d = mod.render_cadence_directive(cfg)
    for label in ("register:", "target_beats:", "phase:", "length:", "observation:",
                  "[response shape"):
        assert label not in d, label
    assert cfg.observation_text in d     # the observation, as a sentence, when it says something
    assert "run long" in d
    assert "two or three messages" in d and "blank line" in d
    # Fix round 1 (C15.3, C15.4): a long vote with beats no longer says
    # "long … short messages"; a plain heading keeps the observation from
    # reading as part of the band before it.
    assert "short" not in d
    assert d.startswith("## This turn\n") and d.count("## This turn") == 1


def test_the_fallback_observation_never_reads_as_one():
    """A failed vote's stand-in ("(default — cadence shape failed; …)") is a
    log line, not something she noticed; any observation wholly in
    parentheses is treated the same. A failed vote's directive is empty."""
    mod = _load()
    assert mod._with_directive(mod._DEFAULT_CONFIG).directive == ""
    fallback = mod._DEFAULT_CONFIG.observation_text
    assert mod.render_cadence_directive(_cfg(mod, observation_text=fallback)) == ""
    assert mod.render_cadence_directive(_cfg(mod, observation_text="(nothing much)")) == ""
    assert mod.render_cadence_directive(_cfg(mod, observation_text="   ")) == ""


def test_the_observation_is_rendered_whole():
    mod = _load()
    long_obs = " ".join(["They are working through a hard week and want company more than advice."] * 6)
    assert mod.render_cadence_directive(_cfg(mod, observation_text=long_obs)) == "## This turn\n" + long_obs


def test_every_cadence_field_stays_on_the_config():
    """Downstream code reads the attributes, never the text (the runner's log
    line, the chunker, the first-pass carry, the momentum tag)."""
    mod = _load()
    names = set(mod.CadenceConfig.__dataclass_fields__)
    assert {"should_respond", "register", "length", "phase", "target_beats",
            "observation_text", "directive", "emotional_context", "decision",
            "reason"} <= names


def test_shape_prompt_biases_short_as_default():
    """The classifier must be told short is the DEFAULT and long requires an
    explicit ask — otherwise the options read as equal-weighted."""
    mod = _load()
    p = mod.SHAPE_RESPONSE_PROMPT
    assert "DEFAULT" in p
    assert "explicitly asked" in p or "explicitly asks" in p
    assert "choose the shorter" in p


# ── Iteration boundary (2026-08-18): narration must not weld across tool
# rounds. The runner reuses ONE chunker for the whole ReAct turn; text from
# model call N and model call N+1 sat back-to-back in the same buffer, and
# ``total_text`` was a bare join — so her replies read
# "...instead of guessing.Source is right there — let me read it.It's a
# puzzle..." (95 of her last 628 Discord messages, 15%, every worst case a
# multi-tool turn). The owner's 08-18 read: "thinking out loud" walls are
# not her. Presentation seam, deterministic on purpose.

def _feed(chunker, *texts):
    for t in texts:
        asyncio.run(chunker.feed(t))


def test_iteration_boundary_puts_a_paragraph_break_between_model_calls():
    mod = _load()
    sent = []
    async def on_chunk(s): sent.append(s)
    chunker = mod.SingleFlowChunker(on_chunk)
    _feed(chunker, "Let me actually go look at it instead of guessing.")
    asyncio.run(chunker.iteration_boundary())        # a tool round happened here
    _feed(chunker, "Source is right there — let me read it.")
    asyncio.run(chunker.iteration_boundary())
    _feed(chunker, "It's a puzzle — and it's mine.")
    asyncio.run(chunker.flush())
    assert chunker.total_text == (
        "Let me actually go look at it instead of guessing.\n\n"
        "Source is right there — let me read it.\n\n"
        "It's a puzzle — and it's mine."
    )
    # No delivered chunk carries the welded seam either.
    assert all("guessing.Source" not in s and "it.It's" not in s for s in sent)


def test_iteration_boundary_is_idempotent_and_never_stacks_blank_lines():
    mod = _load()
    async def on_chunk(s): pass
    chunker = mod.SingleFlowChunker(on_chunk)
    asyncio.run(chunker.iteration_boundary())        # nothing said yet: no-op
    _feed(chunker, "Checking that now.\n\n")           # already ends a paragraph
    asyncio.run(chunker.iteration_boundary())
    asyncio.run(chunker.iteration_boundary())        # a pure tool-call round
    _feed(chunker, "Done — it's set.")
    asyncio.run(chunker.flush())
    assert chunker.total_text == "Checking that now.\n\nDone — it's set."


def test_iteration_boundary_holds_for_beat_and_voice_chunkers_too():
    mod = _load()
    for make in (
        lambda cb: mod.CadencedBeatChunker(cb, target_beats=2, beat_pause_ms=1),
        lambda cb: mod.VoiceFlowChunker(cb),
    ):
        async def on_chunk(s): pass
        chunker = make(on_chunk)
        _feed(chunker, "On it — let me find the file first.")
        asyncio.run(chunker.iteration_boundary())
        _feed(chunker, "Found it. Forty-four minutes of audio.")
        asyncio.run(chunker.flush())
        assert chunker.total_text == (
            "On it — let me find the file first.\n\nFound it. Forty-four minutes of audio."
        ), type(chunker).__name__


def test_the_subtext_note_pitches_to_their_level_and_never_hunts_for_a_test():
    """The owner, 10-13 (the presence slice): the note's first half ("name the
    UNSPOKEN thing ... deflection, a bid for reassurance, testing") made her
    tense with a new friend, and the first look already reads how people seem
    in her own voice. It goes; the second half (pitch it to their level, never
    withhold) stays."""
    import core.turn.companion_flags as flags

    mod = _load()
    captured = {}

    async def fake_llm(prompt: str) -> str:
        captured["prompt"] = prompt
        return "{}"

    mp = pytest.MonkeyPatch()
    mp.setattr(flags, "faculty_enabled", lambda name: name == "subtext_read")
    try:
        asyncio.run(mod.shape_response(FakeCtx(user_text="hello"), llm=fake_llm))
    finally:
        mp.undo()
    prompt = captured["prompt"]
    assert "gauge their expertise" in prompt
    assert "NEVER withholding the answer" in prompt
    for gone in ("UNSPOKEN", "testing", "deflection", "reassurance"):
        assert gone not in prompt, gone
