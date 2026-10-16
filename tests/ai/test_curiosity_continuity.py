"""Curiosity continuity (2026-07-19 spec) — flag, reader, prompt forks.

Every flag assertion FORCES the gate (monkeypatch), never reads the owner's
live yaml — c74389f's lesson: a flag test that consults owner config asserts
his config, not behavior.

Spec: docs/superpowers/specs/2026-07-19-curiosity-continuity-design.md
Plan: docs/superpowers/plans/2026-07-19-curiosity-continuity-v1.md
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest


# ---------------------------------------------------------------- Task 1: gate

def test_gate_off_when_unbooted(monkeypatch):
    from ai import curiosity_research as cr
    monkeypatch.setattr("ai.curiosity_research.companion_settings", lambda: None)
    assert cr.curiosity_continuity_enabled() is False


def test_gate_off_without_curiosity_research(monkeypatch):
    """continuity=True but the research faculty off => OFF (half-on reads as
    off, the narrative_self_enabled precedent)."""
    from ai import curiosity_research as cr
    monkeypatch.setattr(
        "ai.curiosity_research.companion_settings",
        lambda: SimpleNamespace(curiosity_continuity=True),
    )
    monkeypatch.setattr("ai.curiosity_research.faculty_enabled", lambda name: False)
    assert cr.curiosity_continuity_enabled() is False


def test_gate_on_requires_both(monkeypatch):
    from ai import curiosity_research as cr
    monkeypatch.setattr(
        "ai.curiosity_research.companion_settings",
        lambda: SimpleNamespace(curiosity_continuity=True),
    )
    monkeypatch.setattr("ai.curiosity_research.faculty_enabled", lambda name: True)
    assert cr.curiosity_continuity_enabled() is True


def test_flag_defaults_false_in_schema():
    from core.settings_schema import CompanionConfig
    assert CompanionConfig().curiosity_continuity is False


# ---------------------------------------------------------------- Task 2: reader

def _dive(thought: str, topic: str, ts: float):
    from ai.inner_monologue_store import MonologueEntry
    return MonologueEntry(
        timestamp=ts,
        thought=thought,
        situation_snapshot={},
        tick_metadata={"entry_kind": "curiosity_research", "topic": topic, "substantive": True},
        sentiment=None,
    )


def _plain(thought: str, ts: float):
    from ai.inner_monologue_store import MonologueEntry
    return MonologueEntry(
        timestamp=ts, thought=thought, situation_snapshot={},
        tick_metadata={"substantive": True}, sentiment=None,
    )


def test_reader_filters_to_dives_and_caps(monkeypatch):
    from ai import curiosity_research as cr
    entries = [
        _dive("note d", "topic d", 4000.0),
        _plain("relational thought", 3500.0),
        _dive("note c", "topic c", 3000.0),
        _dive("note b", "topic b", 2000.0),
        _dive("note a", "topic a", 1000.0),
    ]
    monkeypatch.setattr("ai.curiosity_research.read_entries", lambda **kw: entries)
    got = cr.recent_research_entries(limit=3)
    assert [e.thought for e in got] == ["note d", "note c", "note b"]

    topics = cr.recent_research_topics(limit=2)
    assert topics == [("topic d", 4000.0), ("topic c", 3000.0)]


def test_reader_skips_dives_missing_topic(monkeypatch):
    from ai import curiosity_research as cr
    from ai.inner_monologue_store import MonologueEntry
    no_topic = MonologueEntry(
        timestamp=5000.0, thought="dive with no topic key", situation_snapshot={},
        tick_metadata={"entry_kind": "curiosity_research", "substantive": True}, sentiment=None,
    )
    monkeypatch.setattr("ai.curiosity_research.read_entries", lambda **kw: [no_topic])
    assert cr.recent_research_topics() == []
    # ...but it still counts as an entry (notes without topics are still notes)
    assert len(cr.recent_research_entries()) == 1


def test_reader_never_raises(monkeypatch):
    from ai import curiosity_research as cr

    def boom(**kw):
        raise OSError("store unreadable")
    monkeypatch.setattr("ai.curiosity_research.read_entries", boom)
    assert cr.recent_research_entries() == []
    assert cr.recent_research_topics() == []


# ------------------------------------------------------- Task 3: gist template

@pytest.mark.asyncio
async def test_gist_template_pick_off_and_on(monkeypatch):
    """Flag off => the ORIGINAL template name (byte-identity by construction:
    that file is never edited). Flag on => the continuity file."""
    from ai import consolidation

    picked: list[str] = []

    class _Loader:
        def load_prompt(self, name):
            picked.append(name)
            return "<<DAY_DATE>> <<DAY_MATERIAL>>"

    async def fake_sections(**kw):
        return {"conversations": "material"}

    async def fake_llm(prompt):
        return "a small day, honestly named"

    monkeypatch.setattr("ai.prompt_loader.prompt_loader", _Loader())
    monkeypatch.setattr(consolidation, "_gather_day_sections", fake_sections)

    stored: list = []
    monkeypatch.setattr(
        "ai.curiosity_research.curiosity_continuity_enabled", lambda: False
    )
    await consolidation.consolidate_day(
        owner_id=1, day="2026-07-01", llm_call=fake_llm, now=1784500000.0,
        store_write=lambda **kw: stored.append(kw) or "p",
        index_write=None,
    )
    monkeypatch.setattr(
        "ai.curiosity_research.curiosity_continuity_enabled", lambda: True
    )
    await consolidation.consolidate_day(
        owner_id=1, day="2026-07-01", llm_call=fake_llm, now=1784500000.0,
        store_write=lambda **kw: stored.append(kw) or "p",
        index_write=None,
    )
    assert picked == ["consolidation_day_gist", "consolidation_day_gist_continuity"]


def test_continuity_prompt_file_invariants():
    """The 6146914 anti-drift law and the restraint law must exist in the new
    file; both render placeholders present; the original file is untouched."""
    from pathlib import Path

    cont = Path("prompts/consolidation_day_gist_continuity.txt").read_text(encoding="utf-8")
    orig = Path("prompts/consolidation_day_gist.txt").read_text(encoding="utf-8")

    for needle in ("<<DAY_DATE>>", "<<DAY_MATERIAL>>"):
        assert needle in cont and needle in orig
    assert 'Do NOT open with "That was the day' in cont
    assert "only when they actually mattered that day" in cont      # restraint law
    assert "quota, not a life" in cont
    # the original keeps its relational frame — proof it was not edited
    assert "one of your days with them" in orig
    assert "only when they actually mattered that day" not in orig


# ------------------------------------------------ Task 4: monologue guidance

async def _async_noop(*a, **kw):
    return None


def test_monologue_template_has_placeholder_once():
    from pathlib import Path
    text = Path("prompts/inner_monologue.txt").read_text(encoding="utf-8")
    assert text.count("{curiosity_guidance}") == 1
    # the static paragraphs moved into code — the template must not still
    # carry them, or the render would duplicate
    assert "Don't re-research" not in text


def test_off_guidance_is_the_original_paragraphs():
    """Byte-identity: flag-off rendering equals the pre-change file because
    the off-fill IS the original text. Pin its exact boundaries, its
    load-bearing phrases, and its shape (two paragraphs, one blank line)."""
    from ai.curiosity_research import CURIOSITY_GUIDANCE_OFF as off
    assert off.startswith("The time between conversations is yours")
    assert off.endswith("(check your recent thoughts above).")
    assert "Don't re-research something you've already gone down" in off
    assert '"history of cursive handwriting decline"' in off
    assert off.count("\n\n") == 1


def test_on_guidance_permits_continuation_and_lists_topics():
    from ai.curiosity_research import continuity_guidance
    g = continuity_guidance(recent_topics=[("humidity control loops", 1784000000.0)])
    assert "humidity control loops" in g
    assert "Don't re-research" not in g          # the old blanket ban is gone
    assert "spark, not a quota" in g             # the budget law survives
    g_empty = continuity_guidance(recent_topics=[])
    assert "(none lately)" in g_empty


@pytest.mark.asyncio
async def test_tick_renders_off_guidance_when_flag_off(monkeypatch):
    """The wiring half: _run_tick must pass curiosity_guidance= (str.format
    KeyErrors otherwise) and must pass the OFF text when the flag is off."""
    from ai import inner_monologue as im
    from ai.curiosity_research import CURIOSITY_GUIDANCE_OFF

    captured = {}

    def fake_format(template, **kw):
        captured.update(kw)
        return "rendered"

    async def fake_context(self):
        return {
            "identity_snapshot": "i", "situation_context": "s",
            "history_text": "h", "had_recent_conversation": False,
            "history_turn_count": 0, "situation_snapshot": {},
            "situation_sources": [], "unavailable_sources": [],
            "owner_user_id": 1,
        }

    async def fake_llm(messages, **kw):
        return '{"thought": "quiet tick", "substantive": false}'

    monkeypatch.setattr(im, "format_prompt", fake_format)
    monkeypatch.setattr(im.InnerMonologueEngine, "_gather_context", fake_context)
    monkeypatch.setattr("ai.inner_monologue.llm.chat_completion", fake_llm)
    monkeypatch.setattr(
        "ai.curiosity_research.curiosity_continuity_enabled", lambda: False
    )
    monkeypatch.setattr(im, "write_entry", lambda e: None)

    engine = im.InnerMonologueEngine()
    monkeypatch.setattr(engine, "_persist_sentiment", _async_noop, raising=False)
    await engine._run_tick()
    assert captured.get("curiosity_guidance") == CURIOSITY_GUIDANCE_OFF


# ------------------------------------------------- Task 5: research continuity

def test_research_template_prior_notes_empty_fill_is_byte_identical():
    """The placeholder sits where empty-fill reproduces today's spacing:
    '{topic}\\n{prior_notes}\\nSo you went' with prior_notes='' renders as
    '{topic}\\n\\nSo you went' — the original bytes."""
    from pathlib import Path

    from core.prompt_format import format_prompt
    template = Path("prompts/curiosity_research.txt").read_text(encoding="utf-8")
    assert "{prior_notes}" in template
    rendered = format_prompt(
        template, topic="TOPIC_X", prior_notes="", sources="SRC", current_time="NOW",
        round_status="",  # multi-round (2026-07-21) always fills this; "" at this seam
    )
    assert "TOPIC_X\n\nSo you went and looked." in rendered


@pytest.mark.asyncio
async def test_research_includes_prior_notes_when_flag_on(monkeypatch):
    from ai import curiosity_research as cr

    rendered_prompts: list[str] = []

    class _Exa:
        async def search(self, **kw):
            return {"results": [{"title": "t", "url": "u", "summary": "s"}]}

    async def fake_llm(messages, **kw):
        rendered_prompts.append(messages[0]["content"])
        return '{"note": "built on what I had", "worth_sharing": false, "share_framing": null}'

    monkeypatch.setattr(cr, "_exa_client", lambda: _Exa())
    monkeypatch.setattr("ai.curiosity_research.llm.chat_completion", fake_llm)
    monkeypatch.setattr(cr, "write_entry", lambda e: None)
    monkeypatch.setattr(cr, "curiosity_continuity_enabled", lambda: True)
    monkeypatch.setattr(
        cr, "recent_research_entries",
        lambda **kw: [_dive("vent motors stall where the vapour barrier sweats", "humidity control", 1784000000.0)],
    )

    entry = await cr.research_curiosity(topic="humidity control part 2", owner_user_id=1)
    assert entry is not None
    assert "vent motors stall where the vapour barrier sweats" in rendered_prompts[0]
    assert "may or may not relate" in rendered_prompts[0]   # the LLM judges, not code


@pytest.mark.asyncio
async def test_research_flag_off_has_no_prior_notes(monkeypatch):
    from ai import curiosity_research as cr

    rendered_prompts: list[str] = []

    class _Exa:
        async def search(self, **kw):
            return {"results": [{"title": "t", "url": "u", "summary": "s"}]}

    async def fake_llm(messages, **kw):
        rendered_prompts.append(messages[0]["content"])
        return '{"note": "fresh note", "worth_sharing": false, "share_framing": null}'

    monkeypatch.setattr(cr, "_exa_client", lambda: _Exa())
    monkeypatch.setattr("ai.curiosity_research.llm.chat_completion", fake_llm)
    monkeypatch.setattr(cr, "write_entry", lambda e: None)
    monkeypatch.setattr(cr, "curiosity_continuity_enabled", lambda: False)

    def _fail(**kw):
        raise AssertionError("reader must not be called flag-off")
    monkeypatch.setattr(cr, "recent_research_entries", _fail)

    entry = await cr.research_curiosity(topic="anything fresh", owner_user_id=1)
    assert entry is not None
    assert "earlier research notes" not in rendered_prompts[0]


# --------------------------------------------- Task 7: interest-aware pursuit
#
# `interests` rides the continuity fork's ON branch exclusively — the
# cultivating block can only ever exist inside continuity_guidance's return,
# and continuity_guidance is only ever called from _run_tick's ON branch.
# The byte pin (b) follows the file's established method (see
# test_off_guidance_is_the_original_paragraphs above): pin the new surface's
# absence-shape precisely, rather than a full literal-string fixture, because
# this function's output is time/topic-dependent and was never a frozen
# constant the way CURIOSITY_GUIDANCE_OFF is. Here the "default must not
# shift a byte" claim is checked two ways: (1) omitting the kwarg vs passing
# interests=None must render identically, and (2) neither rendering may ever
# contain the cultivating block's marker text — closing the gap that (1)
# alone would miss (two calls could drift together and still agree).


def test_cultivating_block_present_when_interests_given():
    """(a) Naming an interest surfaces a 'what you're cultivating' block,
    positioned between the intro and the threads block. recent_topics=[]
    here doubles as a check that the two blocks are independent variables:
    a variable-shadowing bug (reusing the threads block's local name for the
    interests lines) would silently replace '(none lately)' with the
    interest text under the Threads heading."""
    from ai.curiosity_research import continuity_guidance
    g = continuity_guidance(recent_topics=[], interests=["Humidity control"])
    assert "What you're cultivating" in g
    assert "· Humidity control" in g
    assert "(none lately)" in g
    assert g.index("What you're cultivating") < g.index("Threads you've been down lately")


def test_cultivating_block_caps_at_five():
    from ai.curiosity_research import continuity_guidance
    g = continuity_guidance(recent_topics=[], interests=[f"Interest {i}" for i in range(8)])
    assert "· Interest 0" in g and "· Interest 4" in g
    assert "Interest 5" not in g and "Interest 7" not in g


def test_interests_default_is_byte_identical_to_omitted():
    """THE BYTE PIN: the new keyword's default must not shift one byte of
    tonight's ON text. Two independently-constructed calls — one omitting
    the param, one passing interests=None explicitly — must render
    byte-for-byte identical strings, and neither may carry the cultivating
    block (the composition law: it exists only when interests is truthy)."""
    from ai.curiosity_research import continuity_guidance
    topics = [("humidity control loops", 1784000000.0)]
    omitted = continuity_guidance(recent_topics=topics)
    explicit_none = continuity_guidance(recent_topics=topics, interests=None)
    assert omitted == explicit_none
    assert "What you're cultivating" not in omitted
    assert "What you're cultivating" not in explicit_none


def test_interests_empty_list_also_renders_nothing():
    """Falsy-but-not-None also produces no cultivating block — the brief's
    contract is 'when truthy', not 'when not None'."""
    from ai.curiosity_research import continuity_guidance
    g = continuity_guidance(recent_topics=[], interests=[])
    assert "What you're cultivating" not in g


def test_read_interests_headings_parses_and_caps(tmp_path):
    from ai import passions
    (tmp_path / "INTERESTS.md").write_text(
        "\n\n".join(f"## Interest {i}\nsome body text about {i}" for i in range(8)),
        encoding="utf-8",
    )
    assert passions.read_interests_headings(repo_root=tmp_path) == [
        f"Interest {i}" for i in range(5)
    ]


def test_read_interests_headings_ignores_body_text(tmp_path):
    """Only lines that themselves start with '## ' count — prose beneath a
    heading (even prose that happens to contain '## ' mid-line) does not."""
    from ai import passions
    (tmp_path / "INTERESTS.md").write_text(
        "## Humidity control\nstarted with his root cellar, ## not a heading\nmore body\n",
        encoding="utf-8",
    )
    assert passions.read_interests_headings(repo_root=tmp_path) == ["Humidity control"]


def test_read_interests_headings_missing_file_is_empty(tmp_path):
    from ai import passions
    assert passions.read_interests_headings(repo_root=tmp_path) == []


def _tick_harness(monkeypatch):
    """Shared _run_tick capture rig for the interest-wiring tests below —
    the same shape as Task 4's test_tick_renders_off_guidance_when_flag_off,
    factored out since four tests here need it instead of one."""
    from ai import inner_monologue as im

    captured: dict = {}

    def fake_format(template, **kw):
        captured.update(kw)
        return "rendered"

    async def fake_context(self):
        return {
            "identity_snapshot": "i", "situation_context": "s",
            "history_text": "h", "had_recent_conversation": False,
            "history_turn_count": 0, "situation_snapshot": {},
            "situation_sources": [], "unavailable_sources": [],
            "owner_user_id": 1,
        }

    async def fake_llm(messages, **kw):
        return '{"thought": "quiet tick", "substantive": false}'

    monkeypatch.setattr(im, "format_prompt", fake_format)
    monkeypatch.setattr(im.InnerMonologueEngine, "_gather_context", fake_context)
    monkeypatch.setattr("ai.inner_monologue.llm.chat_completion", fake_llm)
    monkeypatch.setattr(im, "write_entry", lambda e: None)

    engine = im.InnerMonologueEngine()
    monkeypatch.setattr(engine, "_persist_sentiment", _async_noop, raising=False)
    return engine, captured


@pytest.mark.asyncio
async def test_tick_interests_flow_through_when_passions_and_continuity_on(monkeypatch):
    """(c) Tick wiring: continuity-on + passions-on ⇒ her interest headings
    reach continuity_guidance and land in the rendered prompt kwarg."""
    engine, captured = _tick_harness(monkeypatch)
    monkeypatch.setattr("ai.curiosity_research.curiosity_continuity_enabled", lambda: True)
    monkeypatch.setattr("ai.curiosity_research.recent_research_topics", lambda: [])
    monkeypatch.setattr("ai.passions.passions_enabled", lambda: True)
    monkeypatch.setattr("ai.passions.read_interests_headings", lambda: ["Humidity control"])

    await engine._run_tick()
    guidance = captured.get("curiosity_guidance", "")
    assert "What you're cultivating" in guidance
    assert "Humidity control" in guidance


@pytest.mark.asyncio
async def test_tick_interests_absent_when_passions_off(monkeypatch):
    """continuity-on + passions-off ⇒ read_interests_headings is never even
    consulted (the passions_enabled() gate short-circuits it inside the
    try), and the rendered guidance carries no cultivating block."""
    engine, captured = _tick_harness(monkeypatch)

    def _must_not_be_called():
        raise AssertionError("read_interests_headings must not run with passions off")

    monkeypatch.setattr("ai.curiosity_research.curiosity_continuity_enabled", lambda: True)
    monkeypatch.setattr("ai.curiosity_research.recent_research_topics", lambda: [])
    monkeypatch.setattr("ai.passions.passions_enabled", lambda: False)
    monkeypatch.setattr("ai.passions.read_interests_headings", _must_not_be_called)

    await engine._run_tick()
    assert "What you're cultivating" not in captured.get("curiosity_guidance", "")


@pytest.mark.asyncio
async def test_tick_interests_read_failure_is_swallowed(monkeypatch):
    """A broken interests read is best-effort (the brief's try/except): the
    tick still completes and still renders continuity guidance, minus the
    cultivating block, never an exception."""
    engine, captured = _tick_harness(monkeypatch)

    def _boom():
        raise OSError("disk gone")

    monkeypatch.setattr("ai.curiosity_research.curiosity_continuity_enabled", lambda: True)
    monkeypatch.setattr("ai.curiosity_research.recent_research_topics", lambda: [])
    monkeypatch.setattr("ai.passions.passions_enabled", lambda: True)
    monkeypatch.setattr("ai.passions.read_interests_headings", _boom)

    await engine._run_tick()
    guidance = captured.get("curiosity_guidance", "")
    assert "What you're cultivating" not in guidance
    assert "Threads you've been down lately" in guidance  # the tick still rendered


@pytest.mark.asyncio
async def test_tick_off_guidance_regardless_of_passions_on(monkeypatch):
    """continuity-off ⇒ CURIOSITY_GUIDANCE_OFF even with passions ON — the
    composition law: the interests block never reaches the OFF branch, so
    passions is never even consulted."""
    from ai.curiosity_research import CURIOSITY_GUIDANCE_OFF
    engine, captured = _tick_harness(monkeypatch)

    def _must_not_be_called(*a, **kw):
        raise AssertionError("passions must not be consulted when continuity is off")

    monkeypatch.setattr("ai.curiosity_research.curiosity_continuity_enabled", lambda: False)
    monkeypatch.setattr("ai.passions.passions_enabled", _must_not_be_called)
    monkeypatch.setattr("ai.passions.read_interests_headings", _must_not_be_called)

    await engine._run_tick()
    assert captured.get("curiosity_guidance") == CURIOSITY_GUIDANCE_OFF


@pytest.mark.asyncio
async def test_tick_off_guidance_regardless_of_passions_off(monkeypatch):
    """Same law, passions OFF this time — both states of the passions flag
    are inert while continuity is off."""
    from ai.curiosity_research import CURIOSITY_GUIDANCE_OFF
    engine, captured = _tick_harness(monkeypatch)

    monkeypatch.setattr("ai.curiosity_research.curiosity_continuity_enabled", lambda: False)
    monkeypatch.setattr("ai.passions.passions_enabled", lambda: False)

    await engine._run_tick()
    assert captured.get("curiosity_guidance") == CURIOSITY_GUIDANCE_OFF
