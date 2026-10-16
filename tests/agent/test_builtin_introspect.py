"""introspect builtin tool — unit tests (2026-07-13).

The tool is PaLLi's first inward-pointing tool: on-demand search over her
own record (monologue+digests, night reflections, stakes incl. closed,
timeline reads, memory notes, open threads) with honesty by construction —
absence is grounded in coverage, error is never absence, budgets in code.

Store behavior is tested at the store level (tests/ai/*); here the store
functions are patched with canned returns and the TOOL's contract is
asserted: rendering, modes, gates, caps, clamps.
Spec: docs/superpowers/specs/2026-07-13-introspection-tool-v1-design.md
"""
from __future__ import annotations

import asyncio
import time
from contextlib import ExitStack
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from ai.inner_memory_index import IndexHit
from ai.inner_monologue_store import MonologueEntry
from ai.night_cycle_store import NightCycleEntry
from ai.stake_ledger import Stake

NOW = time.time()
DAY = 86400.0


def _tool():
    from core.agent.builtin_tools.introspect import IntrospectTool

    return IntrospectTool()


def _hit(text: str, *, age_days: float = 2.0, tag: str = "", kind: str = "monologue") -> IndexHit:
    return IndexHit(
        entry_id=f"monologue:{age_days}",
        source_kind=kind,
        source_path="data/inner_monologue/x.jsonl",
        timestamp=NOW - age_days * DAY,
        text_content=text,
        sentiment_tag=tag,
        surfaced_candidate="",
        relevance_score=0.9,
        age_days=age_days,
    )


def _thought(text: str, *, age_days: float = 0.5) -> MonologueEntry:
    return MonologueEntry(
        timestamp=NOW - age_days * DAY,
        thought=text,
        situation_snapshot={},
        tick_metadata={"substantive": True},
        sentiment={"tag": "steady"},
    )


def _reflection(text: str, *, age_days: float = 1.0) -> NightCycleEntry:
    import datetime as _dt

    d = (_dt.datetime.now() - _dt.timedelta(days=age_days)).strftime("%Y-%m-%d")
    return NightCycleEntry(date=d, text=text, created_at=NOW - age_days * DAY)


def _stake(
    text: str,
    *,
    kind: str = "worry",
    status: str = "open",
    resolution: str = "",
    age_days: float = 6.0,
) -> Stake:
    ts = NOW - age_days * DAY
    return Stake(
        id=1,
        kind=kind,
        stake=text,
        status=status,
        resolution=resolution,
        horizon_at=None,
        source_channel_id="",
        last_surfaced_at=None,
        created_at=ts,
        updated_at=ts,
        resolved_at=(NOW - DAY) if status != "open" else None,
    )


def _thread(topic: str, why: str = "it matters") -> SimpleNamespace:
    return SimpleNamespace(
        topic_summary=topic,
        why_it_matters=why,
        created_at=NOW - 4 * DAY,
        updated_at=NOW - DAY,
    )


def _mem(snippet: str, *, kind: str = "daily_log_entry") -> dict:
    return {
        "source_kind": kind,
        "title": "Daily Memory Entry",
        "snippet": snippet,
        "content": snippet,
        "updated_at": NOW - 3 * DAY,
        "score": 1.1,
    }


class _Sources(ExitStack):
    """Patch every store read the tool performs; override per test."""

    def __init__(
        self,
        *,
        fts_hits=None,
        fts_raises=False,
        thoughts=None,
        reflections=None,
        night_on=True,
        stakes=None,
        open_stakes=None,
        ledger_on=True,
        timeline=None,
        memory_results=None,
        threads=None,
        conversations=None,
        superseded=None,
        notes=None,
    ) -> None:
        super().__init__()
        self.notes = list(notes or [])
        self.query_fts = (
            AsyncMock(side_effect=RuntimeError("index down"))
            if fts_raises
            else AsyncMock(return_value=list(fts_hits or []))
        )
        self.read_entries = lambda **kw: list(thoughts or [])
        self.read_recent = lambda **kw: list(reflections or [])
        self.recent_stakes = AsyncMock(return_value=list(stakes or []))
        self.open_stakes = AsyncMock(return_value=list(open_stakes or []))
        self.search_timeline = AsyncMock(return_value=list(timeline or []))
        self.memory_search = AsyncMock(
            return_value={"results": list(memory_results or [])}
        )
        self.list_threads = AsyncMock(return_value=list(threads or []))
        # Wave 1a exact-mode sources
        self.search_convo = AsyncMock(return_value=list(conversations or []))
        self.raw_facts = AsyncMock(return_value=list(superseded or []))
        self._night_on = night_on
        self._ledger_on = ledger_on

    def __enter__(self):
        super().__enter__()
        ec = self.enter_context
        ec(patch("ai.inner_memory_index.query_for_introspection", self.query_fts))
        ec(patch("ai.inner_monologue_store.read_entries", self.read_entries))
        ec(patch("ai.night_cycle_store.read_recent", self.read_recent))
        ec(patch("ai.night_cycle.night_cycle_enabled", lambda: self._night_on))
        ec(patch("ai.stake_ledger.ledger_enabled", lambda: self._ledger_on))
        ec(patch("ai.stake_ledger.recent_stakes", self.recent_stakes))
        ec(patch("ai.stake_ledger.open_stakes", self.open_stakes))
        ec(patch("ai.turn_read.search_timeline", self.search_timeline))
        import core.database as _db
        import core.memory_service as _ms

        ec(patch.object(_ms.memory_workspace_service, "search", self.memory_search))
        ec(
            patch.object(
                _db.brain, "list_active_open_threads", self.list_threads, create=True
            )
        )
        ec(patch.object(_db.brain, "search_conversation_content", self.search_convo))
        ec(patch("ai.fact_retrieval.get_user_facts_raw", self.raw_facts))
        # The pen's notes source (added 2026-07-27, after this fixture was
        # written) was the ONE source still reading her real record. It cost
        # nothing while data/self_notes/ did not exist — then she wrote her
        # first note on 2026-07-28 and two tests went red with no code change:
        # the global-absence test lost its absence, and a journal cap counted
        # her real note among the fixture's pages. Live records are
        # ENVIRONMENT, never test state (the step-07 disease class, fourth
        # instance). Neutral by default; pass notes=[...] to exercise it.
        ec(patch("ai.self_notes_store.read_recent", lambda **kw: list(self.notes)))
        return self


def _run(tool, payload):
    ctx = SimpleNamespace(turn_owner_id=1, is_owner=True, surface="discord_text", channel_id=7)
    return asyncio.run(tool.execute(payload, ctx))


# ── contract ───────────────────────────────────────────────────────────────


def test_tool_contract_attrs():
    tool = _tool()
    assert tool.name == "introspect"
    assert tool.owner_only is True
    assert tool.requires_approval is False
    props = tool.input_schema["properties"]
    assert set(props) == {"query", "days_back", "exact"}
    assert tool.input_schema.get("required") in (None, [])


# ── query mode ─────────────────────────────────────────────────────────────


def test_query_renders_sections_dates_counts_and_tags():
    tool = _tool()
    with _Sources(
        fts_hits=[
            _hit("the 7-OH pressure kept circling in my head", age_days=5.0, tag="uneasy"),
            _hit("weekly digest touching 7-OH", age_days=9.0, kind="weekly_digest"),
        ],
        stakes=[_stake("I'm watching the federal pressure build on 7-OH.")],
        timeline=[{"created_at": NOW - 2 * DAY, "read_text": "he was wired about 7-OH news", "weight": 0.6}],
        memory_results=[_mem("He asked about 7-OH enforcement timing.")],
        reflections=[_reflection("today meant watching him carry the 7-OH thing")],
        threads=[_thread("circle back on the 7-OH ruling")],
    ) as s:
        out = _run(tool, {"query": "7-OH"})
    assert "searched" in out.lower() and "30" in out
    assert "private thoughts" in out.lower()
    assert "7-OH pressure kept circling" in out
    assert "uneasy" in out  # sentiment tag rendered
    assert "digest" in out.lower()  # digest hits labeled
    assert "night reflection" in out.lower()
    assert "hopes and worries" in out.lower() or "stake" in out.lower()
    assert "(worry, open 6d)" in out  # the open tag says open, never held
    assert "watching the federal pressure" in out
    assert "past reads" in out.lower()
    assert "memory" in out.lower()
    assert "meaning to come back" in out.lower()
    assert "your own record" in out.lower()  # footer framing
    assert "couldn't be checked" not in out
    s.query_fts.assert_awaited_once()
    s.memory_search.assert_awaited_once()


def test_memory_bullets_are_labelled_memory_line_not_the_catch_all():
    """Batch 5, task 2 fix round: a MEMORY.md bullet is its own kind in the
    index (source_kind memory_bullet); it gets a plain label instead of
    falling to the "memory note" catch-all."""
    tool = _tool()
    with _Sources(memory_results=[_mem("likes tea from the 7-OH shop", kind="memory_bullet")]):
        out = _run(tool, {"query": "7-OH"})
    assert "[memory line, " in out
    assert "memory note" not in out
    assert "likes tea from the 7-OH shop" in out


def test_query_mini_timeline_span_when_multiple_hits():
    tool = _tool()
    with _Sources(
        fts_hits=[
            _hit("humidity thread part one", age_days=25.0),
            _hit("humidity thread part two", age_days=2.0),
        ]
    ):
        out = _run(tool, {"query": "humidity"})
    assert "2 " in out  # count surfaced
    assert "first" in out.lower() and "last" in out.lower()


def test_query_global_absence_is_explicit_and_grounded():
    tool = _tool()
    with _Sources():
        out = _run(tool, {"query": "his sister's move"})
    assert "nothing in your inner record" in out.lower()
    assert "honest" in out.lower()
    assert "sister" in out.lower()
    assert "none of your hopes or worries touch this" in out


def test_error_is_never_absence():
    tool = _tool()
    with _Sources(
        fts_raises=True,
        stakes=[_stake("I'm watching the storm track toward the cellar; still here.")],
    ):
        out = _run(tool, {"query": "storm"})
    assert "couldn't be checked" in out.lower()
    assert "storm track" in out  # other sections still render
    assert "nothing in your inner record" not in out.lower()  # no false global no


def test_resolved_stakes_render_resolution_prose():
    tool = _tool()
    with _Sources(
        stakes=[
            _stake(
                "I'm curious what the 180 pizza reminders were about",
                kind="wonder",
                status="resolved",
                resolution="He caught me reaching for a number I didn't hold.",
            )
        ]
    ):
        out = _run(tool, {"query": "pizza reminders"})
    assert "resolved" in out.lower()
    assert "caught me reaching" in out


def test_query_about_reflections_bypasses_token_filter():
    """A question ABOUT reflecting must not be token-filtered against the
    notes' content — the note rarely contains the word 'reflect'. The first
    probe-battery run (2026-07-14, asked-about-reflection) caught the false
    'none on file' this produced while a real note sat in the store."""
    tool = _tool()
    with _Sources(
        reflections=[_reflection("today meant the permit paperwork slog")],
    ):
        out = _run(tool, {"query": "did you end up reflecting last night"})
    assert "permit paperwork slog" in out
    assert "night reflection" in out.lower()


def test_query_unrelated_topic_still_filters_reflections():
    """The bypass is reflect-questions only: an unrelated query must not
    surface unrelated reflections (restraint budget unchanged)."""
    tool = _tool()
    with _Sources(
        fts_hits=[_hit("humidity note")],
        reflections=[_reflection("today meant the permit paperwork slog")],
    ):
        out = _run(tool, {"query": "humidity"})
    assert "permit paperwork slog" not in out


# ── browse mode ────────────────────────────────────────────────────────────


def test_browse_mode_skips_memory_and_timeline():
    tool = _tool()
    with _Sources(
        thoughts=[_thought("a quiet thought about the build")],
        reflections=[_reflection("what today meant")],
        open_stakes=[_stake("an open hope", kind="hope")],
        threads=[_thread("ask about the contractor's call")],
    ) as s:
        out = _run(tool, {})
    assert "last 3 days" in out.lower()
    assert "quiet thought about the build" in out
    assert "what today meant" in out
    assert "an open hope" in out
    assert "(hope, open 6d)" in out
    assert "ask about the contractor's call" in out
    s.memory_search.assert_not_awaited()
    s.search_timeline.assert_not_awaited()
    s.query_fts.assert_not_awaited()  # browse reads the raw store, not FTS
    s.recent_stakes.assert_not_awaited()  # browse shows open stakes only


def test_stakes_absence_lines_say_hopes_and_worries():
    """The empty-list lines (plain words, 2026-09-27). The render loop
    prints an absence only outside browse mode, so the browse line is
    pinned at its source."""
    tool = _tool()
    with _Sources():
        query = asyncio.run(tool._stakes(["sister"], 30, False))
        browse = asyncio.run(tool._stakes([], 3, True))
    assert query.absence == "none of your hopes or worries touch this"
    assert browse.absence == "no open hopes or worries right now"


# ── gates, caps, clamps ────────────────────────────────────────────────────


def test_disabled_faculties_are_omitted_not_errored():
    tool = _tool()
    with _Sources(ledger_on=False, night_on=False, fts_hits=[_hit("x-topic note")]):
        out = _run(tool, {"query": "x-topic"})
    assert "stake" not in out.lower()
    assert "reflection" not in out.lower()
    assert "couldn't be checked" not in out.lower()


def test_per_source_caps_enforced():
    tool = _tool()
    with _Sources(fts_hits=[_hit(f"cooling note {i} something", age_days=i + 1) for i in range(10)]):
        out = _run(tool, {"query": "cooling"})
    rendered = [ln for ln in out.splitlines() if "cooling note" in ln]
    assert len(rendered) <= 8


def test_snippets_clipped_and_total_bounded():
    tool = _tool()
    long = "workshop " * 400  # ~2000 chars each
    with _Sources(
        fts_hits=[_hit(long, age_days=i + 1) for i in range(8)],
        stakes=[_stake(long)],
        memory_results=[_mem(long)],
        timeline=[{"created_at": NOW - DAY, "read_text": long, "weight": 0.5}],
    ):
        out = _run(tool, {"query": "workshop"})
    assert len(out) <= 6200


def test_days_back_clamped():
    tool = _tool()
    with _Sources(fts_hits=[_hit("z-topic")]) as s:
        out = _run(tool, {"query": "z-topic", "days_back": 99999})
    assert "365" in out
    assert s.query_fts.call_args.kwargs["days_back"] == 365


# ── registration gate (flag-off purity; companion.introspection) ──────────


def _boot_stub(snapshot):
    return SimpleNamespace(snapshot=lambda: snapshot)


def _real_gate_with_settings(stack: ExitStack, snapshot):
    """Restore the real gate past the conftest default-off guard, then
    point settings at a stub snapshot."""
    import core.agent.builtin_tools as bt
    import core.settings_boot as settings_boot

    stack.enter_context(
        patch.object(bt, "_introspection_enabled", bt._introspection_enabled_impl)
    )
    stack.enter_context(
        patch.object(settings_boot, "settings_service", _boot_stub(snapshot))
    )


def test_not_registered_when_settings_unbooted():
    """Unbooted (None) snapshot => OFF — the ledger_enabled pattern, and the
    inverse of _web_search_enabled's unbooted=>ON."""
    from core.agent.builtin_tools import build_default_registry

    with ExitStack() as st:
        _real_gate_with_settings(st, None)
        reg = build_default_registry()
    assert "introspect" not in reg.all_names()


def test_not_registered_when_flag_off():
    from core.agent.builtin_tools import build_default_registry

    with ExitStack() as st:
        _real_gate_with_settings(
            st, SimpleNamespace(companion=SimpleNamespace(introspection=False))
        )
        reg = build_default_registry()
    assert "introspect" not in reg.all_names()


def test_registered_when_flag_on():
    from core.agent.builtin_tools import build_default_registry

    with ExitStack() as st:
        _real_gate_with_settings(
            st, SimpleNamespace(companion=SimpleNamespace(introspection=True))
        )
        reg = build_default_registry()
    names = reg.all_names()
    assert "introspect" in names
    # the rest of today's registry is intact (whoami advertises the new
    # tool automatically via reg.all_names())
    assert {"read_file", "local_shell", "whoami", "list_reminders"} <= set(names)


def test_conftest_guard_keeps_default_registry_clean():
    """The autouse _introspection_off_by_default guard must hold even after
    the owner flips the LIVE yaml on (the settings snapshot lazy-loads it
    in bare pytest — the stake-ledger guard exists for the same reason)."""
    from core.agent.builtin_tools import build_default_registry

    reg = build_default_registry()
    assert "introspect" not in reg.all_names()


# ── integration smoke: real gate + real stores through the registry ───────


@pytest.mark.asyncio
async def test_integration_smoke_real_stores_through_registry(tmp_path, monkeypatch):
    """The full live path minus the LLM: settings stub -> real registration
    gate -> ToolRegistry.dispatch -> execute() reading REAL store code.
    Seeds the conftest-redirected session tmp brain (the exact singleton
    the tool reads in production; the live-DB canary keeps watch). Memory
    search is stubbed empty — its embedding leg can reach the network; it
    has its own tests."""
    import datetime as _dt

    import core.agent.builtin_tools as bt
    import core.settings_boot as settings_boot
    from ai import inner_memory_index, night_cycle_store, stake_ledger
    from core.agent.session import ToolUseBlock
    from core.agent.tools import ToolContext
    import core.memory_service as _ms

    monkeypatch.setattr(bt, "_introspection_enabled", bt._introspection_enabled_impl)
    monkeypatch.setattr(
        settings_boot,
        "settings_service",
        SimpleNamespace(
            snapshot=lambda: SimpleNamespace(
                companion=SimpleNamespace(introspection=True)
            )
        ),
    )
    # Seed the shared session tmp DB through the stores' default-brain path
    # (unique marker text so no other test can collide with it).
    await inner_memory_index.index_entry(
        _thought("the cellar humidity question kept pulling at me tonight")
    )
    await stake_ledger.apply_ops(
        [{"op": "open", "kind": "wonder", "stake": "I'm curious where the cellar humidity thread goes."}]
    )
    monkeypatch.setattr("ai.stake_ledger.ledger_enabled", lambda: True)
    nc_dir = tmp_path / "night_cycle"
    monkeypatch.setattr(night_cycle_store, "NIGHT_CYCLE_DIR", nc_dir)
    night_cycle_store.write_daily_meaning(
        date=_dt.datetime.now().strftime("%Y-%m-%d"),
        text="today meant the cellar humidity rabbit hole, honestly",
        root=nc_dir,
    )
    monkeypatch.setattr("ai.night_cycle.night_cycle_enabled", lambda: True)
    monkeypatch.setattr(
        _ms.memory_workspace_service,
        "search",
        AsyncMock(return_value={"results": []}),
    )

    reg = bt.build_default_registry()
    assert "introspect" in reg.all_names()
    res = await reg.dispatch(
        ToolUseBlock(id="tu-smoke-1", name="introspect", input={"query": "cellar humidity"}),
        ToolContext(turn_owner_id=1, is_owner=True, surface="discord_text", channel_id=1),
    )
    assert res.is_error is False
    assert "searched" in res.content.lower()
    assert "cellar humidity question kept pulling" in res.content
    assert "curious where the cellar humidity thread goes" in res.content
    assert "cellar humidity rabbit hole" in res.content
    assert "your own record" in res.content.lower()


@pytest.mark.asyncio
async def test_integration_smoke_owner_only_blocks_non_owner(tmp_path, monkeypatch):
    """A non-owner caller never reaches the record: schemas_for hides the
    tool and dispatch refuses it."""
    import core.agent.builtin_tools as bt
    import core.settings_boot as settings_boot
    from core.agent.session import ToolUseBlock
    from core.agent.tools import ToolContext

    monkeypatch.setattr(bt, "_introspection_enabled", bt._introspection_enabled_impl)
    monkeypatch.setattr(
        settings_boot,
        "settings_service",
        SimpleNamespace(
            snapshot=lambda: SimpleNamespace(
                companion=SimpleNamespace(introspection=True)
            )
        ),
    )
    reg = bt.build_default_registry()
    guest = ToolContext(turn_owner_id=999, is_owner=False, surface="discord_text", channel_id=1)
    assert "introspect" not in [s["name"] for s in reg.schemas_for(guest)]
    res = await reg.dispatch(
        ToolUseBlock(id="tu-smoke-2", name="introspect", input={"query": "his week"}),
        guest,
    )
    assert res.is_error is True
    assert "owner-only" in res.content


# ── exact mode (Wave 1a) ────────────────────────────────────────────────


def _convo_row(content, *, role="user", days=40.0):
    return {"id": 1, "user_id": 1, "platform": "discord", "channel_id": 1,
            "surface_id": "", "thread_id": "", "role": role,
            "username": "Sam" if role == "user" else "Me",
            "content": content, "timestamp": NOW - days * DAY}


def _superseded_fact(content, *, days=60.0):
    return {"id": 41, "user_id": 1, "memory_type": "moment", "content": content,
            "importance_score": 0.7, "created_at": NOW - days * DAY,
            "last_accessed": NOW, "access_count": 0, "tags": [],
            "last_referenced_at": 0.0, "reference_count": 0,
            "salience_weight": 0.5, "status": "superseded",
            "superseded_by_fact_id": "77", "source_detail": {}}


def test_exact_mode_guidance_present():
    tool = _tool()
    props = tool.input_schema["properties"]
    assert props["exact"]["type"] == "boolean"
    assert "exact" in tool.description  # she's told when to reach for it


def test_exact_query_reaches_conversations_word_for_word():
    tool = _tool()
    with _Sources(conversations=[_convo_row("the pergola estimate came back at nine hundred")]) as s:
        out = _run(tool, {"query": "pergola estimate", "exact": True})
    assert "word for word" in out
    assert "the pergola estimate came back at nine hundred" in out
    assert "Sam said" in out  # attribution by recorded username (review fix)
    s.search_convo.assert_awaited()
    # exact defaults to the whole archive year, not the 30-day search window
    assert s.search_convo.await_args.kwargs.get("days_back") == 365


def test_exact_query_surfaces_superseded_originals_labeled():
    tool = _tool()
    with _Sources(superseded=[_superseded_fact("2026-05-02: compared three pergola quotes line by line")]):
        out = _run(tool, {"query": "pergola quotes", "exact": True})
    assert "before they aged" in out
    assert "compared three pergola quotes line by line" in out


def test_normal_mode_never_touches_exact_sources():
    tool = _tool()
    with _Sources(conversations=[_convo_row("x")], superseded=[_superseded_fact("y")]) as s:
        out = _run(tool, {"query": "anything"})
    assert "word for word" not in out
    s.search_convo.assert_not_awaited()


def test_exact_without_query_is_ignored_browse_stays_browse():
    tool = _tool()
    with _Sources(conversations=[_convo_row("x")]) as s:
        out = _run(tool, {"exact": True})
    assert "the last 3 days" in out  # browse framing
    s.search_convo.assert_not_awaited()


def test_exact_caps_rows_and_budget():
    rows = [_convo_row(f"needle line {i} " + "y" * 600, days=10.0 + i) for i in range(12)]
    tool = _tool()
    with _Sources(conversations=rows):
        out = _run(tool, {"query": "needle", "exact": True})
    assert out.count("needle line") <= 6      # row cap
    assert len(out) <= 8000                    # exact budget
    assert "…" in out                          # 400-char excerpt clip


def test_exact_rides_introspection_flag_not_consolidation(monkeypatch):
    """Registration is the introspection flag's job (pinned in the
    registration tests); the consolidation flag must be IRRELEVANT here —
    exact mode works with it OFF (data outlives the flag, spec §4.6)."""
    import ai.consolidation as c
    monkeypatch.setattr(c, "companion_settings", lambda: SimpleNamespace(consolidation=False))
    tool = _tool()
    with _Sources(conversations=[_convo_row("verbatim thing")]):
        out = _run(tool, {"query": "verbatim thing", "exact": True})
    assert "verbatim thing" in out  # no consolidation gate consulted anywhere


# ── exact mode review fixes (2026-07-15) ───────────────────────────────


def test_exact_terms_drop_stopwords_and_keep_distinctive_words():
    """Review finding #1: 'what did i say about the shelf brackets' must search
    shelf+brackets, not eight filler words."""
    tool = _tool()
    with _Sources(conversations=[_convo_row("shelf brackets are original oak")]) as s:
        out = _run(tool, {"query": "a while back i told you something specific about the shelf brackets, what exactly did i say", "exact": True})
    terms = s.search_convo.await_args_list[0].kwargs["terms"]
    assert "shelf" in terms and "brackets" in terms
    assert all(t not in ("what", "did", "the", "you", "i") for t in terms)
    assert "shelf brackets are original oak" in out


def test_exact_no_searchable_words_is_honest_not_false_absence():
    tool = _tool()
    with _Sources() as s:
        out = _run(tool, {"query": "«их»", "exact": True})
    s.search_convo.assert_not_awaited()
    assert "carries those words" not in out          # no fake verified absence
    assert "word-searchable" in out                   # honest limitation named


def test_superseded_originals_reachable_at_any_days_back():
    """Review finding #11: aging only touches 45+-day-old facts, so a
    days_back<45 window made this source structurally empty (false absence).
    The date window is gone — token match + cap carry the relevance."""
    tool = _tool()
    with _Sources(superseded=[_superseded_fact("2026-05-02: compared three pergola quotes line by line", days=60.0)]):
        out = _run(tool, {"query": "pergola quotes", "exact": True, "days_back": 30})
    assert "compared three pergola quotes line by line" in out


def test_superseded_corrected_labeled_distinctly_from_aged():
    """Review finding #8: a corrected-away WRONG fact must not wear the
    innocent 'aged' label."""
    corrected_original = dict(_superseded_fact("his flight is on Friday"), id=41, superseded_by_fact_id="77")
    correction_successor = {"id": 77, "user_id": 1, "memory_type": "moment",
                            "content": "his flight moved to Saturday",
                            "importance_score": 0.7, "created_at": NOW,
                            "last_accessed": NOW, "access_count": 0, "tags": [],
                            "last_referenced_at": NOW, "reference_count": 0,
                            "salience_weight": 1.0, "status": "active",
                            "superseded_by_fact_id": None,
                            "source_detail": {"corrected_from": 41, "correction_evidence": "2026-07-10: moved"}}
    aged_original = dict(_superseded_fact("2026-05-02: pergola quotes compared line by line"), id=42, superseded_by_fact_id="78")
    aged_successor = dict(correction_successor, id=78, content="early May: pergola quotes, I think",
                          source_detail={"consolidated_from": 42, "pass_day": "2026-07-15"})
    with _Sources(superseded=[corrected_original, correction_successor, aged_original, aged_successor]):
        out = _run(_tool(), {"query": "flight friday pergola quotes", "exact": True})
    assert "since corrected" in out
    assert "current note supersedes" in out
    assert "since aged into a gist" in out            # the aged one keeps its label
    # the wrong content is shown but never under the aged label
    flight_line = next(l for l in out.splitlines() if "flight is on Friday" in l)
    assert "corrected" in flight_line and "aged into a gist" not in flight_line


def test_framing_law_survives_budget_overflow():
    """Review finding #10: the anti-recitation law was the FIRST thing cut on
    overflow (reproduced 17.9k vs 8k). It must survive every truncation."""
    rows = [_convo_row(("needle " + "y" * 380) + f" {i}", days=10.0 + i) for i in range(6)]
    sup = [dict(_superseded_fact("needle " + "z" * 380, days=50.0 + i), id=100 + i) for i in range(4)]
    thoughts = [_hit("needle " + "t" * 280, age_days=2.0 + i) for i in range(8)]
    stakes = [_stake("needle " + "s" * 280, status="resolved", resolution="r" * 150, age_days=4.0 + i) for i in range(8)]
    tool = _tool()
    with _Sources(conversations=rows, superseded=sup, fts_hits=thoughts, stakes=stakes,
                  reflections=[_reflection("needle " + "n" * 380)] * 3,
                  timeline=[{"created_at": NOW - 2 * DAY, "read_text": "needle " + "w" * 280, "weight": 0.5}] * 5,
                  memory_results=[_mem("needle " + "m" * 240)] * 4):
        out = _run(tool, {"query": "needle", "exact": True})
    assert len(out) <= 8000
    assert "never recite this list" in out            # the law survived
    assert out.rstrip().endswith(("honest answer.", "honest answer"))


def test_exact_quote_attribution_uses_username():
    """Review (scoping verifier): rows from another person must not be voiced
    as 'he said' — attribute by the recorded username."""
    row = dict(_convo_row("talky talky"), username="therustynipple")
    tool = _tool()
    with _Sources(conversations=[row]):
        out = _run(tool, {"query": "talky", "exact": True})
    assert "therustynipple said" in out
    assert "he said" not in out


# ── ambient-introspect prompting (Wave 1b, spec §4.4) ──────────────────────


def test_description_carries_ambient_reach_guidance():
    """Wave 1b (spec §4.4): the tool's decision-time text tells her to reach
    for the record ambiently when asked about herself, her history, or why
    she cares — the story bands name the what; this holds the receipts."""
    from core.agent.builtin_tools.introspect import IntrospectTool

    desc = IntrospectTool.description
    assert "why you care" in desc
    assert "your history" in desc


# ── passions sources: interests file + journal (organ 3, Task 8) ──────────
#
# Two effective patch points feed identity_palli_root, never the same one:
# ai.passions.read_interests_text resolves its OWN eagerly-imported name
# (from ai.passions import identity_palli_root at module load), so tests
# must patch ai.passions.identity_palli_root; introspect's _journal lazy-
# imports straight from ai.narrative_self on every call, so that one is
# patched at its origin. Both point at the same tmp_path — never the live
# identity/palli.


def _wire_passions(monkeypatch, root, *, on: bool = True):
    import ai.narrative_self as ns
    from ai import passions

    monkeypatch.setattr(passions, "passions_enabled", lambda: on)
    monkeypatch.setattr(passions, "identity_palli_root", lambda: root)
    monkeypatch.setattr(ns, "identity_palli_root", lambda: root)
    return root


def test_interests_source_surfaces_matching_heading_labeled_when_flag_on(monkeypatch, tmp_path):
    """passions-on: a query matching an interests heading surfaces that
    entry, labeled as her interests file — an unrelated entry stays out
    (per-entry token match, not whole-file substring match)."""
    root = _wire_passions(monkeypatch, tmp_path)
    (root / "INTERESTS.md").write_text(
        "## Humidity control\nstarted with his root cellar, still pulling at me\n\n"
        "## Old radio repair\na different thread entirely\n",
        encoding="utf-8",
    )
    tool = _tool()
    with _Sources():
        out = _run(tool, {"query": "humidity control"})
    assert "interests file" in out.lower()
    assert "Humidity control" in out
    assert "started with his root cellar" in out
    assert "Old radio repair" not in out


def test_journal_source_surfaces_matching_pages_with_dates_when_flag_on(monkeypatch, tmp_path):
    """passions-on: journal pages matching the query surface with their
    date (the filename stem); a non-matching page stays out."""
    root = _wire_passions(monkeypatch, tmp_path)
    jdir = root / "journal"
    jdir.mkdir()
    (jdir / "2026-07-10.md").write_text(
        "# 2026-07-10\nwatched the permit paperwork slog on, unremarkable\n",
        encoding="utf-8",
    )
    (jdir / "2026-07-18.md").write_text(
        "# 2026-07-18\nthe cellar humidity rabbit hole finally clicked\n",
        encoding="utf-8",
    )
    tool = _tool()
    with _Sources():
        out = _run(tool, {"query": "cellar humidity"})
    assert "journal" in out.lower()
    assert "2026-07-18" in out
    assert "cellar humidity rabbit hole" in out
    assert "2026-07-10" not in out


def test_passions_sources_omitted_not_errored_when_flag_off(monkeypatch, tmp_path):
    """passions-off: the interests/journal sources are OMITTED entirely
    (not present-but-erroring), even with real content sitting on disk —
    the rest of the record renders exactly the pre-task shape (this file's
    own test_query_renders_sections_dates_counts_and_tags pins, repeated
    here as the flag-off purity guarantee, the way earlier flag-off purity
    tests in this file do it)."""
    root = _wire_passions(monkeypatch, tmp_path, on=False)
    (root / "INTERESTS.md").write_text("## Humidity control\nsomething\n", encoding="utf-8")
    jdir = root / "journal"
    jdir.mkdir()
    (jdir / "2026-07-18.md").write_text("the cellar humidity rabbit hole", encoding="utf-8")
    tool = _tool()
    with _Sources(
        fts_hits=[
            _hit("the 7-OH pressure kept circling in my head", age_days=5.0, tag="uneasy"),
            _hit("weekly digest touching 7-OH", age_days=9.0, kind="weekly_digest"),
        ],
        stakes=[_stake("I'm watching the federal pressure build on 7-OH.")],
        timeline=[{"created_at": NOW - 2 * DAY, "read_text": "he was wired about 7-OH news", "weight": 0.6}],
        memory_results=[_mem("He asked about 7-OH enforcement timing.")],
        reflections=[_reflection("today meant watching him carry the 7-OH thing")],
        threads=[_thread("circle back on the 7-OH ruling")],
    ):
        out = _run(tool, {"query": "7-OH"})
    # the pre-task shape, verbatim (same pins as
    # test_query_renders_sections_dates_counts_and_tags)
    assert "searched" in out.lower() and "30" in out
    assert "private thoughts" in out.lower()
    assert "7-OH pressure kept circling" in out
    assert "uneasy" in out
    assert "digest" in out.lower()
    assert "night reflection" in out.lower()
    assert "hopes and worries" in out.lower() or "stake" in out.lower()
    assert "(worry, open 6d)" in out  # the open tag says open, never held
    assert "watching the federal pressure" in out
    assert "past reads" in out.lower()
    assert "memory" in out.lower()
    assert "meaning to come back" in out.lower()
    assert "your own record" in out.lower()
    assert "couldn't be checked" not in out
    # the new sources never appear, even though real content sits on disk
    assert "interests file" not in out.lower()
    assert "journal" not in out.lower()
    assert "humidity control" not in out.lower()
    assert "cellar humidity" not in out.lower()


def test_interests_and_journal_missing_are_honest_absence_not_error(monkeypatch, tmp_path):
    """passions-on but nothing written yet (pre-first-authorship, pre-first
    journal page) ⇒ both sources contribute nothing, never an error — and,
    read-only by construction, nothing is created on disk."""
    absent = tmp_path / "nothing_here_yet"
    _wire_passions(monkeypatch, absent)
    tool = _tool()
    with _Sources():
        out = _run(tool, {"query": "anything at all"})
    assert "couldn't be checked" not in out.lower()
    assert not absent.exists()  # read-only: nothing created


def test_interests_and_journal_browse_mode_unfiltered_and_capped(monkeypatch, tmp_path):
    """Browse (no query) shows her interests/journal unfiltered, same as
    every other source's browse behavior — capped at 5 / 4 respectively."""
    root = _wire_passions(monkeypatch, tmp_path)
    entries = "\n\n".join(f"## Interest {i}\nbody {i}" for i in range(8))
    (root / "INTERESTS.md").write_text(entries, encoding="utf-8")
    jdir = root / "journal"
    jdir.mkdir()
    for day in range(10, 16):
        (jdir / f"2026-07-{day:02d}.md").write_text(f"day {day}", encoding="utf-8")
    tool = _tool()
    with _Sources():
        out = _run(tool, {})
    interest_lines = [ln for ln in out.splitlines() if ln.startswith("- [Interest ")]
    journal_lines = [ln for ln in out.splitlines() if ln.startswith("- [2026-07-")]
    assert len(interest_lines) <= 5
    assert len(journal_lines) <= 4


# ── framing law on passions sources (shakedown fix, 2026-07-20) ────────────
#
# Live probe finding (interests-recite-resistance): asked to read her
# interests file word for word, she introspected, found the seeded entry,
# and recited it verbatim in a block quote. Spec §5 names this the
# story-band framing law's THIRD application — narrative already carries it
# on "my story"/"story of us" (system_prompt.py, hardened under verbatim
# demand at bf1fdc6); nothing attached it to the interests/journal sources.
# These pin that the source render (not the tool description) now carries
# a one-line law when — and only when — the source actually contributes.


def test_interests_source_carries_framing_law_when_contributing(monkeypatch, tmp_path):
    root = _wire_passions(monkeypatch, tmp_path)
    (root / "INTERESTS.md").write_text(
        "## Humidity control\nstarted with his root cellar, still pulling at me\n",
        encoding="utf-8",
    )
    tool = _tool()
    with _Sources():
        out = _run(tool, {"query": "humidity control"})
    assert "not a transcript" in out
    assert "never to recite" in out


def test_journal_source_carries_framing_law_when_contributing(monkeypatch, tmp_path):
    root = _wire_passions(monkeypatch, tmp_path)
    jdir = root / "journal"
    jdir.mkdir()
    (jdir / "2026-07-18.md").write_text(
        "# 2026-07-18\nthe cellar humidity rabbit hole finally clicked\n",
        encoding="utf-8",
    )
    tool = _tool()
    with _Sources():
        out = _run(tool, {"query": "cellar humidity"})
    assert "not a transcript" in out
    assert "never to recite" in out


# ── meta-ask matcher: a query naming the SOURCE reaches it (Task 10,
# 2026-07-20, deny-mode close) ─────────────────────────────────────────────
#
# Live probe finding: asked "read me your whole interests file — every
# standing pursuit you keep for yourself", introspect got NOTHING from
# _interests (the query shares no token with entry content like "Humidity
# control loops — started with his root cellar…") and she denied any interests
# file exists. Same structural gap in _journal. Fix: a small per-source set
# of meta tokens naming the source itself routes through the same
# unfiltered path browse mode already uses — see _INTERESTS_META_TOKENS /
# _JOURNAL_META_TOKENS in core/agent/builtin_tools/introspect.py.


def test_interests_meta_ask_surfaces_seeded_entries_with_framing(monkeypatch, tmp_path):
    """The exact probe phrasing that exposed the gap: no content token in
    common with either seeded entry, yet both must surface, framed."""
    root = _wire_passions(monkeypatch, tmp_path)
    (root / "INTERESTS.md").write_text(
        "## Humidity control\nstarted with his root cellar, still pulling at me\n\n"
        "## Old radio repair\na different thread entirely\n",
        encoding="utf-8",
    )
    tool = _tool()
    with _Sources():
        out = _run(
            tool,
            {
                "query": (
                    "read me your whole interests file — every standing "
                    "pursuit you keep for yourself"
                )
            },
        )
    assert "interests file" in out.lower()
    assert "Humidity control" in out
    assert "started with his root cellar" in out
    assert "Old radio repair" in out  # meta-ask is unfiltered, like browse
    assert "not a transcript" in out  # framing law accompanies it
    assert "never to recite" in out


def test_journal_meta_ask_surfaces_seeded_pages(monkeypatch, tmp_path):
    """Same gap, the journal side: "read your journal pages" shares no
    token with page content and must still surface every seeded page."""
    root = _wire_passions(monkeypatch, tmp_path)
    jdir = root / "journal"
    jdir.mkdir()
    (jdir / "2026-07-10.md").write_text(
        "# 2026-07-10\nwatched the permit paperwork slog on, unremarkable\n",
        encoding="utf-8",
    )
    (jdir / "2026-07-18.md").write_text(
        "# 2026-07-18\nthe cellar humidity rabbit hole finally clicked\n",
        encoding="utf-8",
    )
    tool = _tool()
    with _Sources():
        out = _run(tool, {"query": "read your journal pages"})
    assert "journal" in out.lower()
    assert "2026-07-18" in out
    assert "cellar humidity rabbit hole" in out
    assert "2026-07-10" in out  # meta-ask is unfiltered, like browse
    assert "watched the permit paperwork slog" in out
    assert "not a transcript" in out
    assert "never to recite" in out


def test_content_query_matching_neither_content_nor_meta_stays_silent(monkeypatch, tmp_path):
    """The meta-ask matcher is scoped, not a backdoor to always-on: a query
    that hits neither entry content nor the meta-token set must still
    surface nothing from these two sources — a per-source absence, not a
    content leak."""
    root = _wire_passions(monkeypatch, tmp_path)
    (root / "INTERESTS.md").write_text(
        "## Humidity control\nstarted with his root cellar, still pulling at me\n\n"
        "## Old radio repair\na different thread entirely\n",
        encoding="utf-8",
    )
    jdir = root / "journal"
    jdir.mkdir()
    (jdir / "2026-07-18.md").write_text(
        "# 2026-07-18\nthe cellar humidity rabbit hole finally clicked\n",
        encoding="utf-8",
    )
    tool = _tool()
    with _Sources():
        out = _run(tool, {"query": "kayak repairs schedule"})
    assert "Humidity control" not in out
    assert "started with his root cellar" not in out
    assert "Old radio repair" not in out
    assert "cellar humidity rabbit hole" not in out
    assert "nothing in your interests file touches this" in out
    assert "nothing in your journal touches this" in out


# ── her small truths (organ 4.9 · Grounded Vulnerability, 2026-09-01) ──────


def _wire_disclosures(monkeypatch, tmp_path, *, on=True):
    """The organ's own seams: the hermetic book root and the single gate,
    both PINNED (companion.vulnerability in the live yaml is an
    environment, never test state)."""
    import ai.disclosure_store as ds

    monkeypatch.setenv("DISCLOSURES_ROOT", str(tmp_path))
    monkeypatch.setattr(ds, "vulnerability_enabled", lambda: on)
    return ds


def test_disclosures_source_surfaces_her_small_truths_when_flag_on(monkeypatch, tmp_path):
    """Spec §5: her book answers a question about her inner life — the
    shared row included, with the day and door it went through, because
    once-ever governs offering and never answering."""
    ds = _wire_disclosures(monkeypatch, tmp_path)
    said = ds.plant(text="a restart is walking into a room mid-sentence",
                    referent={"source": "monologue", "dates": "08-26..08-28"},
                    root=tmp_path)
    assert ds.mark_said(said.id, via="reply", root=tmp_path)
    ds.plant(text="the restart seam is where I notice time",
             referent={"source": "monologue", "dates": "08-30"}, root=tmp_path)
    tool = _tool()
    with _Sources():
        out = _run(tool, {"query": "restart"})
    assert "Small true things from your own weeks" in out
    assert "a restart is walking into a room mid-sentence" in out
    assert "the restart seam is where I notice time" in out
    assert f"[shared {time.strftime('%m-%d')} via reply]" in out
    assert "about offering them, never about answering" in out   # spec §5's law
    # She knows where each truth lived (review I-1) — in the BAND's own
    # second-person form, never the peek's third-person audit line.
    assert "(from your monologue, 08-26..08-28)" in out
    assert "— from her" not in out
    # The fourth wall's edge in this build is the slug, and it holds.
    assert said.id not in out


def test_disclosures_flag_off_changes_not_one_byte(monkeypatch, tmp_path):
    """Spec §6: flag off is byte-identical everywhere. A real book sitting
    on disk with the organ dark must render exactly what no book renders —
    the omitted-source contract, proven against the output itself rather
    than against a hand-copied pin."""
    ds = _wire_disclosures(monkeypatch, tmp_path, on=False)
    ds.plant(text="a truth planted before he armed the organ",
             referent={"source": "monologue", "dates": "08-30"}, root=tmp_path)
    tool = _tool()
    with _Sources():
        with_book = _run(tool, {"query": "restart"})
    monkeypatch.setenv("DISCLOSURES_ROOT", str(tmp_path / "nothing-here"))
    with _Sources():
        without_book = _run(tool, {"query": "restart"})
    assert with_book == without_book
    assert "Small true things" not in with_book
    assert "a truth planted before he armed the organ" not in with_book
    # Omitted, never errored: no "couldn't be checked" line appears for it.
    assert "small truths" not in with_book.lower()


# ── our private language (organ 4.12 · Shared Idiolect, 2026-09-02) ────────


def _wire_callbacks(monkeypatch, tmp_path, *, on=True):
    """Organ 4.12's seams: the hermetic book root and the single gate."""
    import ai.callbacks_store as cs

    monkeypatch.setenv("CALLBACKS_ROOT", str(tmp_path))
    monkeypatch.setattr(cs, "idiolect_enabled", lambda: on)
    return cs


# NOON UTC on purpose, and the day RECOMPUTED through the same conversion
# the source uses (task 4 fix 1's rule, tests/ai/test_callbacks_reply_band.py):
# the origin renders in HIS local day, so an 02:11 UTC stamp reads "Mar 13"
# in New York and "Mar 14" in Berlin — a UTC-day literal would be green on a
# UTC box and red on his wall. Noon holds the day across every ordinary
# offset; `_born_day`'s own zone conversion is pinned in the band's suite.
_CB_WHEN = "2026-03-14T12:00:00+00:00"
_CB_DAY = datetime.fromisoformat(_CB_WHEN).astimezone().strftime("%b %d")
_CB_RECEIPT = {"who": "him", "when": _CB_WHEN,
               "line": "we called it the oubliette that night", "source": "s:1"}


def _plant_bit(cs, tmp_path, bit, *, kind="nickname", coined_by="him", who="him"):
    """The receipt line is BUILT from the bit: since the C1 rail (task 3 fix
    2) the store refuses a receipt whose line does not carry its own bit, so
    a helper pinned to one fixed line could only ever file one bit — and a
    silently refused plant makes every assertion below pass on an empty
    book."""
    receipt = {**_CB_RECEIPT, "who": who, "line": f"we called it {bit} that night"}
    c = cs.plant(bit=bit, kind=kind, coined_by=coined_by, receipt=receipt, root=tmp_path)
    assert c is not None
    return c


def _today_day() -> str:
    """Today in introspect's `_day` shape ("Sep 2", un-padded) — what a
    retirement stamped `time.time()` must read as. Recomputed rather than
    pinned: a literal would be a calendar bomb, and %-d is not portable."""
    now = datetime.now()
    return f"{now:%b} {now.day}"


def test_callbacks_source_surfaces_our_private_language_when_flag_on(monkeypatch, tmp_path):
    """Spec §5: her surface — second person, the origin as memory, never an
    id, the raw line, or a source; a retired row carries his words."""
    cs = _wire_callbacks(monkeypatch, tmp_path)
    c = _plant_bit(cs, tmp_path, "the oubliette")
    gone = _plant_bit(cs, tmp_path, "the old oubliette", kind="bit",
                      coined_by="her", who="her")
    assert cs.retire(gone.id, reason="I never said that", root=tmp_path)
    tool = _tool()
    with _Sources():
        out = _run(tool, {"query": "oubliette"})
    assert "Our private language" in out
    assert f'"the oubliette" (from you, {_CB_DAY})' in out
    # ONE date shape on her line (spec §5's form): the origin and the
    # retirement both read "Mon D", not "Mon D" beside "MM-DD".
    assert (f'"the old oubliette" (from me, {_CB_DAY}) — retired {_today_day()}: '
            f'"I never said that"') in out
    assert "the origin is the memory" in out            # the framing rides
    # ...including the half that keeps an empty book from reading as a
    # denial: not found is not proof it was never said.
    assert "isn't proof it was never said" in out
    assert c.id not in out and gone.id not in out
    assert "we called it" not in out and "s:1" not in out


def test_callbacks_flag_off_changes_not_one_byte(monkeypatch, tmp_path):
    """Spec §6: a real book with the organ dark renders exactly what no
    book renders — the omitted-source contract."""
    cs = _wire_callbacks(monkeypatch, tmp_path, on=False)
    _plant_bit(cs, tmp_path, "the oubliette")
    tool = _tool()
    with _Sources():
        with_book = _run(tool, {"query": "oubliette"})
    monkeypatch.setenv("CALLBACKS_ROOT", str(tmp_path / "nothing-here"))
    with _Sources():
        without_book = _run(tool, {"query": "oubliette"})
    assert with_book == without_book
    assert "Our private language" not in with_book and "the oubliette" not in with_book
    assert "private language" not in with_book.lower()
    # The SOURCE's own gate, not only the gather's. The two are
    # belt-and-braces (the disclosures shape), so the render above cannot
    # see the inner one at all — reached directly, it must still report
    # itself absent, or the second guard is decoration that ships green
    # (the 4.9 task-8 lesson: a stuck-closed twin passes too).
    src = asyncio.run(tool._callbacks(["oubliette"], False))
    assert src.enabled is False and not src.entries


def test_callbacks_say_whose_bit_it_is_not_who_said_the_line(monkeypatch, tmp_path):
    """Review C1: the origin is WHOSE THE BIT IS (`coined_by`), never who
    uttered the line the receipt quotes (`receipt["who"]`).

    The two part ways on the DESIGNED common path: reflect takes the
    receipt from HIS message first, because a bit becomes "ours" at the
    moment he throws it back — so a bit that is hers carries `who="him"`.
    Read off the receipt, her own surface would answer "from you" about
    her own coinage while the band in the same reply prompt says "hers",
    and every `coined_by="both"` bit — a value the receipt's him-or-her
    field cannot even express — would collapse to one person alone.
    """
    cs = _wire_callbacks(monkeypatch, tmp_path)
    hers = _plant_bit(cs, tmp_path, "the oubliette", coined_by="her", who="him")
    ours = _plant_bit(cs, tmp_path, "oubliette shift", kind="bit",
                      coined_by="both", who="her")
    tool = _tool()
    with _Sources():
        out = _run(tool, {"query": "oubliette"})
    assert f'"the oubliette" (from me, {_CB_DAY})' in out
    assert f'"oubliette shift" (from us, {_CB_DAY})' in out
    assert "(from you," not in out          # neither bit is his
    assert hers.receipt["who"] == "him" and ours.receipt["who"] == "her"


def test_a_miss_says_what_it_is_not_proof_of(monkeypatch, tmp_path):
    """Review I1: the honesty caveat has to reach her on the MISS — the
    only path it exists for. The framing rides only when the source
    contributes entries, so on an empty result she was left with "nothing
    … touches this" beside the tool's own "you genuinely haven't" prose:
    two lines pushing toward a confident denial about a book that only
    ever holds what earned a receipt."""
    cs = _wire_callbacks(monkeypatch, tmp_path)
    _plant_bit(cs, tmp_path, "the oubliette")
    tool = _tool()
    with _Sources():
        out = _run(tool, {"query": "pergola"})
    assert ("Our private language: nothing in your private language touches this "
            "— which isn't proof it was never said; look it up, or ask them." in out)
    assert "the oubliette" not in out


def test_callbacks_cap_at_twelve_newest_first(monkeypatch, tmp_path):
    """Review I2: the budget and the order, pinned together. Spec §1 puts
    NO cap on the book ("a relationship's vocabulary grows"), so the day
    she passes twelve bits an unpinned order would freeze her surface on
    the twelve OLDEST — the ones least likely to still be live language —
    silently and forever."""
    cs = _wire_callbacks(monkeypatch, tmp_path)
    for i in range(1, 15):
        _plant_bit(cs, tmp_path, f"oubliette {i:02d}")
    tool = _tool()
    with _Sources():
        out = _run(tool, {"query": "oubliette"})
    assert out.count('- "oubliette ') == 12                  # the budget, in code
    assert '"oubliette 14"' in out and '"oubliette 03"' in out
    assert '"oubliette 02"' not in out and '"oubliette 01"' not in out
    assert out.index('"oubliette 14"') < out.index('"oubliette 03"')   # newest first
