"""Batch 5, task 4 (memory that picks, 2026-09-28): the look-up into the
"## Things I Know About This User" band, deduped against the memory core.

Spec 2.8: the band was mostly her own moods. Live, 154 of 186 active
``user_memory`` rows are ``memory_type="moment"`` — reflect's emotional
reads of them — and the search that finds a MEMORY.md bullet for a turn fed
only "Background memory hits". Under rulings R5/R6/R7/R38/R39 the band is:
(a) the memory bullets recall's search returned (``source_kind ==
"memory_bullet"``), minus any the core already shows, rendered ``- [stamp]
content``; then (b) the decay-weighted ``user_memory`` facts that are not
moments, in today's ``- key: value`` shape; eight lines at most, bullets
first. A bullet never reaches the hits: it is in the band, or already in
the core. A bullet obeys the same relevance floor the hits obey (R40):
below it, it is neither shown nor counted. Every bullet id that cleared the
floor rides ``TurnContext.memory_lookup_ids`` BEFORE the dedup — a bullet
the core shows was still surfaced — for Task 5's reference stamps. Recall loads the
owner's refs snapshot once (``memory_refs.ensure_loaded``) and plans the
core with the same budget helper the adapter uses, so its dedup and the
adapter's render are the same core.

House style: the canonical module is exercised through
``build_turn_context`` with every other dependency stubbed clean
(``tests.turn.test_recall_parallel._stub_clean``); the search and facts
feeds are the fakes here, and the band is read back off the adapter so the
rendered bytes are what is asserted.
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

import core.turn.memory_lookup as memory_lookup
import core.turn.recall as recall
from core.agent.context_adapter import build_agent_loop_context
from core.identity.models import CompiledIdentity, IdentityDocument
from core.memory_bullets import _split_bullet, item_id_for
from core.turn.types import Turn, TurnMetadata
from tests.turn.test_recall_parallel import _flags, _stub_clean

OWNER = 7
BAND_HEADER = "## Things I Know About This User"

IN_CORE_LINE = "- [2026-09-01] they keep bees on the roof"
LOOKED_UP_LINE = "- [2026-03-04] their sister Mara lives in Leeds"
IN_CORE_ID = item_id_for(IN_CORE_LINE)
LOOKED_UP_ID = item_id_for(LOOKED_UP_LINE)

MOMENT_TEXT = "They seemed worn thin tonight, deflecting with jokes."
CAT_TEXT = "User's cat is named Mochi."
SECTION_SNIPPET = "the boat thread, in brief"


@pytest.fixture(autouse=True)
def _forget_refs():
    from core import memory_refs

    memory_refs.forget()
    yield
    memory_refs.forget()


def _turn(content: str = "how is Mara doing?", *, origin: str = "user") -> Turn:
    # An explicit naive-UTC stamp: the field's default factory is the
    # deprecated ``datetime.utcnow`` and would warn.
    return Turn(
        surface="dashboard",
        owner_id=OWNER,
        content=content,
        images=[],
        metadata=TurnMetadata(origin=origin),  # type: ignore[arg-type]
        received_at=datetime.now(timezone.utc).replace(tzinfo=None),
    )


def _memory_identity(body: str) -> CompiledIdentity:
    return CompiledIdentity(owner={"memory": IdentityDocument(
        doc_key="memory", path="m", frontmatter={}, content=body,
    )})


def _bullet(line: str, *, score: float = 0.8) -> dict:
    """A search result shaped like memory_service._format_index_result gives
    a ``memory_bullet`` item (Task 2): content without the ``- `` and the
    leading stamps; metadata carries the first stamp (or None)."""
    stamp, body, _key = _split_bullet(line)
    return {
        "item_id": item_id_for(line),
        "source_kind": "memory_bullet",
        "source_id": "memory",
        "title": "Durable Context",
        "snippet": body[:220],
        "content": body,
        "score": score,
        "semantic_score": score,
        "metadata": {"section": "Durable Context", "doc": "memory", "stamp": stamp},
    }


def _section(snippet: str = SECTION_SNIPPET, *, score: float = 0.7) -> dict:
    return {
        "item_id": "mcs_000000000abc",
        "source_kind": "memory_section",
        "source_id": "memory",
        "title": "Active Threads",
        "snippet": snippet,
        "content": snippet,
        "score": score,
        "semantic_score": score,
        "metadata": {},
    }


class _Weighted:
    """What ai.fact_retrieval.get_decay_weighted_facts yields: an object
    with a ``fact`` row dict."""

    def __init__(self, fact: dict):
        self.fact = fact


def _moment(fact_id: int = 11, text: str = MOMENT_TEXT) -> _Weighted:
    return _Weighted({"id": fact_id, "memory_type": "moment", "content": text, "importance_score": 0.8})


def _typed(fact_id: int = 12, text: str = CAT_TEXT, memory_type: str = "user_fact") -> _Weighted:
    return _Weighted({"id": fact_id, "memory_type": memory_type, "content": text, "importance_score": 0.8})


def _wire(monkeypatch, *, results, facts, compiled, loader=None):
    """Stub recall clean, then hand it the two feeds and the identity. The
    refs loader is a mock so the brain is never touched; the snapshot stays
    whatever the test seeded (empty unless it did)."""
    _stub_clean(monkeypatch, flags=_flags(semantic_recall=True))
    import core.identity.service as ids
    monkeypatch.setattr(ids.identity_service, "compile_for_owner", lambda owner_id: compiled)
    import core.memory_service as ms
    monkeypatch.setattr(
        ms.memory_workspace_service, "search", AsyncMock(return_value={"results": list(results)}),
    )
    import ai.fact_retrieval as fr
    captured: dict = {}

    async def fake_facts(**kwargs):
        captured.update(kwargs)
        return list(facts)

    monkeypatch.setattr(fr, "get_decay_weighted_facts", fake_facts)
    from core import memory_refs
    loader = loader or AsyncMock(return_value={})
    monkeypatch.setattr(memory_refs, "ensure_loaded", loader)
    return captured, loader


_STANDARD_RESULTS = [_bullet(IN_CORE_LINE, score=0.9), _bullet(LOOKED_UP_LINE, score=0.8), _section()]
_STANDARD_FACTS = [_moment(), _typed()]
_STANDARD_IDENTITY = _memory_identity("## Durable Context\n" + IN_CORE_LINE + "\n")


# ── the band (R5, R39) ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_band_carries_the_looked_up_bullet_and_the_typed_fact(monkeypatch):
    """Two bullets came back from the search, one of them already in the
    core; the facts feed gave one of her moods and one typed fact. The
    band: the other bullet as ``- [stamp] content``, then the fact as
    ``- key: value``. Not the mood, not the core's line — which the memory
    band shows instead."""
    _wire(monkeypatch, results=_STANDARD_RESULTS, facts=_STANDARD_FACTS, compiled=_STANDARD_IDENTITY)
    turn = _turn()
    ctx = await recall.build_turn_context(turn)

    assert [e.get("line") or e.get("value") for e in ctx.relevant_facts] == [LOOKED_UP_LINE, CAT_TEXT]
    bullet, fact = ctx.relevant_facts
    assert bullet["item_id"] == LOOKED_UP_ID and bullet["source_kind"] == "memory_bullet"
    # Task 5 bumps the rendered facts by row id; the type says what it was.
    assert {k: fact[k] for k in ("key", "value", "id", "memory_type")} == {
        "key": "fact", "value": CAT_TEXT, "id": 12, "memory_type": "user_fact",
    }

    out = build_agent_loop_context(turn, ctx)
    assert out.facts_text == f"{BAND_HEADER}\n{LOOKED_UP_LINE}\n- fact: {CAT_TEXT}"
    assert MOMENT_TEXT not in out.facts_text
    assert "bees" not in out.facts_text and "bees on the roof" in out.owner_memory_text


@pytest.mark.asyncio
async def test_recall_asks_the_facts_feed_to_leave_out_her_moments(monkeypatch):
    """The eight slots must be able to hold eight typed facts: the feed is
    asked to exclude ``moment`` rows (154 of 186 live) rather than recall
    asking for eight and filtering what the moods left."""
    captured, _ = _wire(monkeypatch, results=[], facts=[], compiled=None)
    await recall.build_turn_context(_turn())
    assert captured["exclude_memory_types"] == ("moment",)
    assert captured["max_results"] == 8
    assert captured["user_id"] == OWNER and captured["query"] == "how is Mara doing?"


@pytest.mark.asyncio
async def test_an_unstamped_bullet_renders_bare_and_an_archived_marker_stays_visible(monkeypatch):
    results = [
        _bullet("- no stamp on this one", score=0.9),
        _bullet("- [2026-01-01] the old boat [archived 2026-09-10]", score=0.8),
    ]
    _wire(monkeypatch, results=results, facts=[], compiled=None)
    turn = _turn()
    ctx = await recall.build_turn_context(turn)
    assert build_agent_loop_context(turn, ctx).facts_text == (
        f"{BAND_HEADER}\n- no stamp on this one\n- [2026-01-01] the old boat [archived 2026-09-10]"
    )


@pytest.mark.asyncio
async def test_the_band_caps_at_eight_lines_bullets_first(monkeypatch):
    six = [_bullet(f"- [2026-02-{i + 1:02d}] looked-up line {i}", score=0.9 - i * 0.01) for i in range(6)]
    five = [_typed(fact_id=100 + i, text=f"typed fact {i}") for i in range(5)]
    _wire(monkeypatch, results=six, facts=five, compiled=None)
    turn = _turn()
    ctx = await recall.build_turn_context(turn)
    lines = build_agent_loop_context(turn, ctx).facts_text.split("\n")
    assert lines[0] == BAND_HEADER and len(lines) == 1 + 8
    assert lines[1:7] == [f"- [2026-02-{i + 1:02d}] looked-up line {i}" for i in range(6)]
    assert lines[7:] == ["- fact: typed fact 0", "- fact: typed fact 1"]

    ten = [_bullet(f"- [2026-03-{i + 1:02d}] looked-up line {i}", score=0.9 - i * 0.01) for i in range(10)]
    _wire(monkeypatch, results=ten, facts=five, compiled=None)
    ctx = await recall.build_turn_context(turn)
    lines = build_agent_loop_context(turn, ctx).facts_text.split("\n")
    assert len(lines) == 1 + 8 and all(line.startswith("- [2026-03-") for line in lines[1:])


# ── the ids ride the context (R6) ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_every_returned_bullet_id_rides_the_context_before_the_dedup(monkeypatch):
    """Both ids, in result order — the core's line too: recall surfaced it,
    and Task 5 stamps every surfaced line."""
    _wire(monkeypatch, results=_STANDARD_RESULTS, facts=_STANDARD_FACTS, compiled=_STANDARD_IDENTITY)
    ctx = await recall.build_turn_context(_turn())
    assert ctx.memory_lookup_ids == (IN_CORE_ID, LOOKED_UP_ID)


@pytest.mark.asyncio
async def test_no_bullets_means_no_ids_and_no_band_lines_from_the_search(monkeypatch):
    _wire(monkeypatch, results=[_section()], facts=[_typed()], compiled=None)
    ctx = await recall.build_turn_context(_turn())
    assert ctx.memory_lookup_ids == ()
    assert [e["value"] for e in ctx.relevant_facts] == [CAT_TEXT]


@pytest.mark.asyncio
async def test_a_bullet_below_the_relevance_floor_is_neither_shown_nor_counted(monkeypatch):
    """Ruling R40: a bullet obeys the SAME relevance floor as the background
    hits, through the same helper — ``semantic_recall_min_similarity`` (0.3
    here) against the result's own ``semantic_score``. Below it, or with no
    similarity known (the hits path admits no keyword-only result either —
    ``_passes_semantic_cutoff`` is False when no similarity is reported),
    the bullet reaches neither the band nor ``memory_lookup_ids``; at the
    floor it reaches both."""
    below = {**_bullet("- [2026-05-01] a keyword coincidence", score=0.9), "semantic_score": 0.10}
    unknown = {**_bullet("- [2026-05-02] no similarity known", score=0.9), "semantic_score": None}
    at_floor = {**_bullet("- [2026-05-03] right at the floor", score=0.4), "semantic_score": 0.30}
    _wire(monkeypatch, results=[below, unknown, at_floor], facts=[], compiled=None)
    turn = _turn()
    ctx = await recall.build_turn_context(turn)
    assert ctx.memory_lookup_ids == (item_id_for("- [2026-05-03] right at the floor"),)
    assert build_agent_loop_context(turn, ctx).facts_text == f"{BAND_HEADER}\n- [2026-05-03] right at the floor"
    assert ctx.recent_memory_hits == ()


# ── the hits (R39) ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_bullets_never_reach_the_background_hits_and_the_other_results_still_do(monkeypatch):
    _wire(monkeypatch, results=_STANDARD_RESULTS, facts=_STANDARD_FACTS, compiled=_STANDARD_IDENTITY)
    turn = _turn()
    ctx = await recall.build_turn_context(turn)
    assert ctx.recent_memory_hits == (SECTION_SNIPPET,)
    memory_text = build_agent_loop_context(turn, ctx).memory_text
    assert "Background memory hits" in memory_text and SECTION_SNIPPET in memory_text
    assert "Mara" not in memory_text and "bees" not in memory_text


@pytest.mark.asyncio
async def test_a_bullet_does_not_take_a_hit_slot_from_the_other_results(monkeypatch):
    """max_hits (3) counts what reaches the hits: three bullets ranked first
    leave the three section results their places."""
    results = [_bullet(f"- [2026-04-0{i + 1}] bullet {i}", score=0.95) for i in range(3)] + [
        {**_section(f"section {i}", score=0.9 - i * 0.01), "item_id": f"mcs_00000000000{i}"} for i in range(3)
    ]
    _wire(monkeypatch, results=results, facts=[], compiled=None)
    ctx = await recall.build_turn_context(_turn())
    assert ctx.recent_memory_hits == ("section 0", "section 1", "section 2")


# ── the same core as the adapter (R7, R38) ──────────────────────────────────


@pytest.mark.asyncio
async def test_recall_loads_the_owners_refs_once(monkeypatch):
    _, loader = _wire(monkeypatch, results=_STANDARD_RESULTS, facts=_STANDARD_FACTS, compiled=_STANDARD_IDENTITY)
    await recall.build_turn_context(_turn())
    loader.assert_awaited_once_with(OWNER)


@pytest.mark.asyncio
async def test_recalls_dedup_and_the_adapters_render_are_the_same_core(monkeypatch):
    """The core is planned from the same doc, the same budget helper and the
    same refs snapshot in both places. Shrink the budget to one thread and
    reference the OLDER one: the adapter renders the old thread in the
    core, so recall's band carries the newer one and not the old."""
    import time as _time

    from core import memory_refs

    newer, older = "- [2026-01-02] the newer boat", "- [2026-01-01] the old boat"
    compiled = _memory_identity(f"## Active Threads\n{newer}\n{older}\n")
    _wire(monkeypatch, results=[_bullet(newer), _bullet(older)], facts=[], compiled=compiled)
    monkeypatch.setattr(memory_refs, "memory_core_budget", lambda: 50)
    monkeypatch.setitem(
        memory_refs._snapshots, OWNER,
        {item_id_for(older): {"last_referenced_at": _time.time(), "count": 1}},
    )
    turn = _turn()
    ctx = await recall.build_turn_context(turn)
    out = build_agent_loop_context(turn, ctx)
    assert older in out.owner_memory_text and "the newer boat" not in out.owner_memory_text
    assert out.facts_text == f"{BAND_HEADER}\n{newer}"
    assert ctx.memory_lookup_ids == (item_id_for(newer), item_id_for(older))


@pytest.mark.asyncio
async def test_a_failing_refs_load_never_breaks_recall(monkeypatch):
    """The brain is down for the refs read: recall logs it and plans the
    core on the empty snapshot — the stamps alone still put the core's line
    in the core, so the band is the same as on a good day."""
    loader = AsyncMock(side_effect=RuntimeError("brain down"))
    _wire(monkeypatch, results=_STANDARD_RESULTS, facts=_STANDARD_FACTS, compiled=_STANDARD_IDENTITY, loader=loader)
    turn = _turn()
    ctx = await recall.build_turn_context(turn)
    loader.assert_awaited_once_with(OWNER)
    assert build_agent_loop_context(turn, ctx).facts_text == f"{BAND_HEADER}\n{LOOKED_UP_LINE}\n- fact: {CAT_TEXT}"
    assert ctx.memory_lookup_ids == (IN_CORE_ID, LOOKED_UP_ID)


@pytest.mark.asyncio
async def test_a_failing_band_plan_leaves_the_facts_feed_in_place(monkeypatch):
    """Fix wave (T4 M12): the band block is wrapped in core.turn.memory_lookup
    — when planning it fails, recall keeps the facts feed exactly as wave 1
    fetched it, records no look-up ids, logs at debug, and the reply still
    goes out with its typed fact."""
    import logging

    _wire(monkeypatch, results=_STANDARD_RESULTS, facts=_STANDARD_FACTS, compiled=_STANDARD_IDENTITY)

    def boom(*_a, **_kw):
        raise RuntimeError("band planning down")

    monkeypatch.setattr(memory_lookup, "plan_facts_band", boom)
    turn = _turn()
    with _caplog_at(logging.DEBUG, memory_lookup.logger) as records:
        ctx = await recall.build_turn_context(turn)
    assert ctx.memory_lookup_ids == ()
    facts_text = build_agent_loop_context(turn, ctx).facts_text
    assert f"- fact: {CAT_TEXT}" in facts_text                       # the feed, as fetched
    assert LOOKED_UP_LINE not in facts_text and IN_CORE_LINE not in facts_text
    assert any("band planning down" in r.getMessage() and r.levelno == logging.DEBUG for r in records)


class _caplog_at:
    """A tiny handler on one logger, so the debug line can be asserted
    without the fixture (this file drives recall with monkeypatch only)."""

    def __init__(self, level, logger):
        import logging

        self.logger, self.level, self.records = logger, level, []
        self.handler = logging.Handler()
        self.handler.emit = self.records.append

    def __enter__(self):
        self.previous = self.logger.level
        self.logger.setLevel(self.level)
        self.logger.addHandler(self.handler)
        return self.records

    def __exit__(self, *exc):
        self.logger.removeHandler(self.handler)
        self.logger.setLevel(self.previous)
        return False


# ── guests (R30) ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_guest_turn_renders_no_band_and_loads_no_refs(monkeypatch):
    """A guest's turn is keyed to the guest (origin "guest"): recall does not
    load the owner's refs for it, and the adapter's P() gate keeps the band
    off the prompt exactly as before."""
    _, loader = _wire(monkeypatch, results=_STANDARD_RESULTS, facts=_STANDARD_FACTS, compiled=_STANDARD_IDENTITY)
    turn = _turn("[Sam]: what do they keep on the roof?", origin="guest")
    ctx = await recall.build_turn_context(turn)
    loader.assert_not_awaited()
    assert build_agent_loop_context(turn, ctx, is_owner=False).facts_text == ""


# ── the pure pieces ─────────────────────────────────────────────────────────


def test_plan_facts_band_is_bullets_minus_the_core_then_typed_facts_capped():
    results = [_bullet(IN_CORE_LINE), _bullet(LOOKED_UP_LINE), _section(), _bullet(LOOKED_UP_LINE)]
    facts = [
        {"key": "fact", "value": MOMENT_TEXT, "confidence": 0.0, "id": 11, "memory_type": "moment"},
        {"key": "fact", "value": CAT_TEXT, "confidence": 0.0, "id": 12, "memory_type": "user_fact"},
        {"key": "fact", "value": "   ", "confidence": 0.0, "id": 13, "memory_type": "preference"},
        "not a dict",
    ]
    band = memory_lookup.plan_facts_band(results, core_ids=frozenset({IN_CORE_ID}), facts=facts)
    assert band == [
        {"line": LOOKED_UP_LINE, "item_id": LOOKED_UP_ID, "source_kind": "memory_bullet"},
        {"key": "fact", "value": CAT_TEXT, "confidence": 0.0, "id": 12, "memory_type": "user_fact"},
    ]
    # the repeated bullet rendered once; a bullet with no text is nothing
    empty = {**_bullet("- [2026-01-01] x"), "content": "", "snippet": ""}
    assert memory_lookup.plan_facts_band([empty], core_ids=frozenset(), facts=[]) == []
    # the cap counts every line, bullets first
    many = [_bullet(f"- line {i}") for i in range(5)]
    typed = [{"key": "fact", "value": f"fact {i}", "id": i, "memory_type": "user_fact"} for i in range(5)]
    band = memory_lookup.plan_facts_band(many, core_ids=frozenset(), facts=typed, limit=8)
    assert [e.get("line") or e["value"] for e in band] == [f"- line {i}" for i in range(5)] + ["fact 0", "fact 1", "fact 2"]


def test_memory_bullet_ids_are_every_bullets_id_in_result_order_once():
    results = [_bullet(IN_CORE_LINE), _section(), _bullet(LOOKED_UP_LINE), _bullet(IN_CORE_LINE), {"source_kind": "memory_bullet", "item_id": ""}]
    assert memory_lookup.memory_bullet_ids(results) == (IN_CORE_ID, LOOKED_UP_ID)
    assert memory_lookup.memory_bullet_ids([]) == ()
