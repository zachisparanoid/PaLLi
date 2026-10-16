from __future__ import annotations

import asyncio
import math
from pathlib import Path
import sqlite3
import time
import uuid

import pytest
import yaml

from ai.fact_retrieval import (
    DecayWeightedFact,
    get_decay_weighted_facts,
    get_user_facts_raw,
    queue_reference_bump,
    queue_reference_bump_ids,
    score_fact,
)
from core.database import PalliBrain
from core.settings_service import SettingsService


def _db_path(tmp_path: Path) -> Path:
    # Must stay under pytest's tmp dir — building this in the repo's real
    # data/ dir littered it with orphaned sqlite files whenever the finally
    # cleanup was skipped (interrupt, close() raising, PermissionError).
    return tmp_path / f"phase7d_test_{uuid.uuid4().hex}.sqlite3"


def test_db_path_helper_stays_out_of_repo_data_dir(tmp_path: Path) -> None:
    """Hygiene regression guard: test dbs must live under pytest's tmp dir,
    never the repo's real data/ directory (which accumulated ~1.5GB of
    phase7d_test_*.sqlite3 litter when finally-block cleanup was skipped)."""
    path = _db_path(tmp_path)
    repo_data = Path(__file__).resolve().parents[2] / "data"
    assert repo_data not in path.resolve().parents


def _cleanup(path: Path) -> None:
    for candidate in [path, path.with_name(f"{path.name}-wal"), path.with_name(f"{path.name}-shm"), path.with_name(f"{path.name}-journal")]:
        try:
            candidate.unlink()
        except (FileNotFoundError, PermissionError):
            pass


@pytest.mark.asyncio
async def test_fact_schema_created_on_fresh_db(tmp_path: Path) -> None:
    path = _db_path(tmp_path)
    brain = PalliBrain(str(path))
    try:
        await brain.initialize()
        with sqlite3.connect(path) as conn:
            columns = {row[1] for row in conn.execute("PRAGMA table_info(user_memory)").fetchall()}
            tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        assert {"last_referenced_at", "reference_count", "salience_weight", "status", "superseded_by_fact_id", "source_detail"} <= columns
    finally:
        await brain.close()
        _cleanup(path)


@pytest.mark.asyncio
async def test_existing_user_memory_schema_migrates_without_data_loss(tmp_path: Path) -> None:
    path = _db_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.execute(
            """
            CREATE TABLE user_memory (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id BIGINT NOT NULL,
                memory_type TEXT NOT NULL,
                content TEXT NOT NULL,
                importance_score REAL DEFAULT 0.5,
                created_at REAL NOT NULL,
                last_accessed REAL,
                access_count BIGINT DEFAULT 0,
                tags TEXT
            )
            """
        )
        conn.execute(
            "INSERT INTO user_memory (user_id, memory_type, content, importance_score, created_at) VALUES (7, 'people', 'User lives in Seattle.', 0.8, 1000.0)"
        )
        conn.commit()
    brain = PalliBrain(str(path))
    try:
        await brain.initialize()
        raw = await get_user_facts_raw(user_id=7, brain_client=brain)
        assert raw[0]["content"] == "User lives in Seattle."
        assert raw[0]["status"] == "active"
        with sqlite3.connect(path) as conn:
            columns = {row[1] for row in conn.execute("PRAGMA table_info(user_memory)").fetchall()}
        assert "salience_weight" in columns
    finally:
        await brain.close()
        _cleanup(path)


@pytest.mark.asyncio
async def test_new_fact_insert_defaults_phase7d_fields(tmp_path: Path) -> None:
    path = _db_path(tmp_path)
    brain = PalliBrain(str(path))
    try:
        await brain.initialize()
        memory_id = await brain.store_user_memory(user_id=7, memory_type="preference", content="Keep replies concise.", importance=0.9, tags=["style"])
        raw = await get_user_facts_raw(user_id=7, brain_client=brain)
        fact = raw[0]
        assert fact["id"] == memory_id
        assert fact["last_referenced_at"] == fact["created_at"]
        assert fact["reference_count"] == 0
        assert fact["salience_weight"] == 1.0
        assert fact["status"] == "active"
    finally:
        await brain.close()
        _cleanup(path)


def test_decay_formula_and_boosts() -> None:
    now = time.time()
    base = {
        "importance_score": 0.8,
        "created_at": now - 90 * 86400,
        "last_referenced_at": now - 30 * 86400,
        "reference_count": 0,
        "salience_weight": 1.0,
    }
    thirty = score_fact(base, now=now, half_life_days=30.0)
    assert thirty.effective_score == pytest.approx(0.8 * math.exp(-1), rel=0.02)
    sixty = score_fact({**base, "last_referenced_at": now - 60 * 86400}, now=now, half_life_days=30.0)
    assert sixty.effective_score == pytest.approx(0.8 * math.exp(-2), rel=0.02)
    assert sixty.effective_score < 0.15
    revisit = score_fact({**base, "reference_count": 5}, now=now, half_life_days=30.0)
    salient = score_fact({**base, "salience_weight": 1.5}, now=now, half_life_days=30.0)
    assert revisit.effective_score == pytest.approx(thirty.effective_score * 1.5)
    assert salient.effective_score == pytest.approx(thirty.effective_score * 1.5)


@pytest.mark.asyncio
async def test_decay_retrieval_filters_and_raw_returns_all(tmp_path: Path) -> None:
    path = _db_path(tmp_path)
    brain = PalliBrain(str(path))
    now = time.time()
    try:
        await brain.initialize()
        fresh = await brain.store_user_memory(user_id=7, memory_type="people", content="User lives in Portland.", importance=0.9)
        stale = await brain.store_user_memory(user_id=7, memory_type="moment", content="User was traveling this week.", importance=0.8)
        superseded = await brain.store_user_memory(user_id=7, memory_type="people", content="User lives in Seattle.", importance=0.9)
        conn = await brain.get_connection()
        try:
            await conn.execute("UPDATE user_memory SET last_referenced_at = $1 WHERE id = $2", now - 90 * 86400, stale)
            await conn.execute("UPDATE user_memory SET status = 'superseded', superseded_by_fact_id = $1 WHERE id = $2", str(fresh), superseded)
        finally:
            await conn.close()
        weighted = await get_decay_weighted_facts(user_id=7, max_results=8, min_score=0.15, now=now, brain_client=brain)
        raw = await get_user_facts_raw(user_id=7, brain_client=brain)
        assert [item.fact["id"] for item in weighted] == [fresh]
        assert {fact["id"] for fact in raw} == {fresh, stale, superseded}
    finally:
        await brain.close()
        _cleanup(path)


@pytest.mark.asyncio
async def test_gist_successor_surfaces_where_original_does_not(tmp_path: Path) -> None:
    """Wave 1a supersede contract, ambient half: after aging, the gist is the
    ambient face of the memory and the original is exact-mode-only."""
    path = _db_path(tmp_path)
    brain = PalliBrain(str(path))
    now = time.time()
    try:
        await brain.initialize()
        original = await brain.store_user_memory(
            user_id=7, memory_type="moment", content="2026-05-02: compared three pergola quotes line by line", importance=0.8)
        gist = await brain.store_user_memory(
            user_id=7, memory_type="moment", content="early May: he was deep in pergola quotes, I think", importance=0.8)
        conn = await brain.get_connection()
        try:
            await conn.execute(
                "UPDATE user_memory SET status='superseded', superseded_by_fact_id=$1, salience_weight=0.5 WHERE id=$2",
                str(gist), original)
        finally:
            await conn.close()
        weighted = await get_decay_weighted_facts(user_id=7, max_results=8, now=now, brain_client=brain)
        ids = [item.fact["id"] for item in weighted]
        assert gist in ids and original not in ids
        raw = await get_user_facts_raw(user_id=7, brain_client=brain)
        assert {f["id"] for f in raw} == {original, gist}  # exact path still sees everything
    finally:
        await brain.close()
        _cleanup(path)


@pytest.mark.asyncio
async def test_fact_memory_settings_hot_reload_changes_decay(monkeypatch, tmp_path: Path) -> None:
    path = _db_path(tmp_path)
    settings_path = tmp_path / f"phase7d_settings_{uuid.uuid4().hex}.yaml"
    brain = PalliBrain(str(path))
    try:
        await brain.initialize()
        memory_id = await brain.store_user_memory(user_id=7, memory_type="people", content="User lives in Portland.", importance=0.8)
        conn = await brain.get_connection()
        try:
            now = time.time()
            await conn.execute("UPDATE user_memory SET last_referenced_at = $1 WHERE id = $2", now - 30 * 86400, memory_id)
        finally:
            await conn.close()

        raw_settings = yaml.safe_load(Path("data/palli.yaml").read_text(encoding="utf-8"))
        raw_settings["fact_memory"]["half_life_days"] = 5.0
        settings_path.write_text(yaml.safe_dump(raw_settings), encoding="utf-8")
        service = SettingsService(settings_path)
        service.load()
        monkeypatch.setattr("ai.fact_retrieval.settings_service", service)
        fast_decay = await get_decay_weighted_facts(user_id=7, max_results=1, min_score=0.0, now=now, brain_client=brain)

        raw_settings["fact_memory"]["half_life_days"] = 60.0
        settings_path.write_text(yaml.safe_dump(raw_settings), encoding="utf-8")
        service.load()
        slow_decay = await get_decay_weighted_facts(user_id=7, max_results=1, min_score=0.0, now=now, brain_client=brain)

        assert slow_decay[0].effective_score > fast_decay[0].effective_score
    finally:
        await brain.close()
        _cleanup(path)
        try:
            settings_path.unlink()
        except (FileNotFoundError, PermissionError):
            pass


# IM-CRIT-3: get_decay_weighted_facts must respect a query argument so that
# top-k facts are re-ranked toward the user's current message topic instead of
# being identical for every turn. Empty/None query preserves the historical
# decay-only ordering exactly.


@pytest.mark.asyncio
async def test_query_argument_reranks_facts_toward_topic_match(tmp_path: Path) -> None:
    """When `query` is a non-empty string, facts whose content overlaps query
    tokens should rank ahead of equally-decayed facts that don't overlap."""
    path = _db_path(tmp_path)
    brain = PalliBrain(str(path))
    now = time.time()
    try:
        await brain.initialize()
        # Three facts with similar decay scores — same importance, same age.
        portland = await brain.store_user_memory(
            user_id=7, memory_type="people", content="User lives in Portland.", importance=0.8,
        )
        cat_named_otis = await brain.store_user_memory(
            user_id=7, memory_type="people", content="User has a cat named Otis.", importance=0.8,
        )
        likes_jazz = await brain.store_user_memory(
            user_id=7, memory_type="preference", content="User likes jazz music.", importance=0.8,
        )

        # No query: ordering follows decay/recency only — no topic awareness.
        decay_only = await get_decay_weighted_facts(
            user_id=7, max_results=3, min_score=0.0, now=now, brain_client=brain,
        )
        decay_ids = [item.fact["id"] for item in decay_only]
        assert set(decay_ids) == {portland, cat_named_otis, likes_jazz}

        # Cat-related query: the cat fact must rank first.
        cat_query = await get_decay_weighted_facts(
            user_id=7, max_results=3, min_score=0.0, now=now, brain_client=brain,
            query="how is your cat doing today?",
        )
        assert cat_query[0].fact["id"] == cat_named_otis

        # Music-related query: the jazz fact must rank first.
        music_query = await get_decay_weighted_facts(
            user_id=7, max_results=3, min_score=0.0, now=now, brain_client=brain,
            query="any new music recommendations?",
        )
        assert music_query[0].fact["id"] == likes_jazz

        # City-related query: the Portland fact must rank first.
        place_query = await get_decay_weighted_facts(
            user_id=7, max_results=3, min_score=0.0, now=now, brain_client=brain,
            query="what's the weather like in Portland?",
        )
        assert place_query[0].fact["id"] == portland
    finally:
        await brain.close()
        _cleanup(path)


@pytest.mark.asyncio
async def test_empty_or_none_query_preserves_decay_only_ordering(tmp_path: Path) -> None:
    """Regression preservation: when query is None or whitespace, the result
    must match the historical decay-only ordering exactly."""
    path = _db_path(tmp_path)
    brain = PalliBrain(str(path))
    now = time.time()
    try:
        await brain.initialize()
        recent = await brain.store_user_memory(
            user_id=7, memory_type="people", content="User adopted a dog last week.", importance=0.8,
        )
        old_but_loved = await brain.store_user_memory(
            user_id=7, memory_type="preference", content="User has favorite tea.", importance=0.9,
        )
        # Make `recent` more recent and `old_but_loved` older, so decay favors recent.
        conn = await brain.get_connection()
        try:
            await conn.execute(
                "UPDATE user_memory SET last_referenced_at = $1 WHERE id = $2",
                now - 1 * 86400, recent,
            )
            await conn.execute(
                "UPDATE user_memory SET last_referenced_at = $1 WHERE id = $2",
                now - 20 * 86400, old_but_loved,
            )
        finally:
            await conn.close()

        baseline = await get_decay_weighted_facts(
            user_id=7, max_results=2, min_score=0.0, now=now, brain_client=brain,
        )
        none_query = await get_decay_weighted_facts(
            user_id=7, max_results=2, min_score=0.0, now=now, brain_client=brain,
            query=None,
        )
        empty_query = await get_decay_weighted_facts(
            user_id=7, max_results=2, min_score=0.0, now=now, brain_client=brain,
            query="",
        )
        whitespace_query = await get_decay_weighted_facts(
            user_id=7, max_results=2, min_score=0.0, now=now, brain_client=brain,
            query="   \t\n  ",
        )
        baseline_ids = [item.fact["id"] for item in baseline]
        assert [item.fact["id"] for item in none_query] == baseline_ids
        assert [item.fact["id"] for item in empty_query] == baseline_ids
        assert [item.fact["id"] for item in whitespace_query] == baseline_ids
    finally:
        await brain.close()
        _cleanup(path)


@pytest.mark.asyncio
async def test_query_with_only_stopwords_falls_back_to_decay_only(tmp_path: Path) -> None:
    """Queries that tokenize to only stopwords or single-character tokens have
    no meaningful signal — must not perturb decay ordering."""
    path = _db_path(tmp_path)
    brain = PalliBrain(str(path))
    now = time.time()
    try:
        await brain.initialize()
        first = await brain.store_user_memory(
            user_id=7, memory_type="people", content="User lives in Tokyo.", importance=0.9,
        )
        second = await brain.store_user_memory(
            user_id=7, memory_type="preference", content="User prefers green tea.", importance=0.6,
        )
        baseline = await get_decay_weighted_facts(
            user_id=7, max_results=2, min_score=0.0, now=now, brain_client=brain,
        )
        baseline_ids = [item.fact["id"] for item in baseline]
        # Stopword-only / single-char-only queries should not re-rank.
        for query in ("the in is a", "ok", "?", "!!!"):
            result = await get_decay_weighted_facts(
                user_id=7, max_results=2, min_score=0.0, now=now, brain_client=brain,
                query=query,
            )
            assert [item.fact["id"] for item in result] == baseline_ids, (
                f"query={query!r} should not change ordering"
            )
        # Sanity: confirm both facts exist.
        assert {first, second} == set(baseline_ids)
    finally:
        await brain.close()
        _cleanup(path)


@pytest.mark.asyncio
async def test_query_with_special_characters_is_safe(tmp_path: Path) -> None:
    """Query strings containing punctuation, quotes, or SQL-ish syntax must
    not crash and must still token-match the alphanumeric portions."""
    path = _db_path(tmp_path)
    brain = PalliBrain(str(path))
    now = time.time()
    try:
        await brain.initialize()
        portland = await brain.store_user_memory(
            user_id=7, memory_type="people", content="User lives in Portland.", importance=0.8,
        )
        seattle = await brain.store_user_memory(
            user_id=7, memory_type="people", content="User has friends in Seattle.", importance=0.8,
        )
        # Punctuation, quotes, SQL-ish syntax — must extract token "Portland".
        result = await get_decay_weighted_facts(
            user_id=7, max_results=2, min_score=0.0, now=now, brain_client=brain,
            query="What's it like in 'Portland'? -- you said it'd rain.",
        )
        assert result[0].fact["id"] == portland
        # Sanity: both facts still in pool.
        assert {item.fact["id"] for item in result} == {portland, seattle}
    finally:
        await brain.close()
        _cleanup(path)


@pytest.mark.asyncio
async def test_query_matching_no_facts_falls_back_to_decay_only(tmp_path: Path) -> None:
    """Query whose tokens overlap zero facts must still return decay-ranked
    top-k — never starve the prompt of context just because no facts matched."""
    path = _db_path(tmp_path)
    brain = PalliBrain(str(path))
    now = time.time()
    try:
        await brain.initialize()
        a = await brain.store_user_memory(
            user_id=7, memory_type="people", content="User lives in Portland.", importance=0.9,
        )
        b = await brain.store_user_memory(
            user_id=7, memory_type="preference", content="User prefers green tea.", importance=0.7,
        )
        baseline = await get_decay_weighted_facts(
            user_id=7, max_results=2, min_score=0.0, now=now, brain_client=brain,
        )
        baseline_ids = [item.fact["id"] for item in baseline]

        # Query mentions nothing in any fact's content.
        result = await get_decay_weighted_facts(
            user_id=7, max_results=2, min_score=0.0, now=now, brain_client=brain,
            query="quantum entanglement asteroid mining",
        )
        assert [item.fact["id"] for item in result] == baseline_ids
        assert {a, b} == set(baseline_ids)
    finally:
        await brain.close()
        _cleanup(path)


@pytest.mark.asyncio
async def test_query_matches_fact_with_possessive_apostrophe(tmp_path: Path) -> None:
    """LLM-generated fact text uses possessives heavily ("user's dad",
    "user's job", "user's mom"). User queries are short and rarely use
    the possessive form ("how is mom?"). The token normalizer must drop
    the trailing 's so possessive forms in fact content match bare-noun
    query tokens. Code-review issue #1.

    The fact text uses the noun *only* in possessive form ("Mom's
    birthday"), so without normalization the tokens "mom's" and "mom"
    don't match. With possessive normalization, "mom's" -> "mom" and
    overlap fires.
    """
    path = _db_path(tmp_path)
    brain = PalliBrain(str(path))
    now = time.time()
    try:
        await brain.initialize()
        # Decay-equal facts: same importance, same age. The possessive
        # one should still win when query mentions the bare noun.
        # Note: the bare noun "Mom" appears NOWHERE in this fact —
        # only the possessive "Mom's". This is what forces the test to
        # actually exercise possessive normalization.
        mom_fact = await brain.store_user_memory(
            user_id=7, memory_type="people", content="Mom's birthday is in June.", importance=0.8,
        )
        unrelated = await brain.store_user_memory(
            user_id=7, memory_type="preference", content="User likes jazz.", importance=0.8,
        )
        result = await get_decay_weighted_facts(
            user_id=7, max_results=2, min_score=0.0, now=now, brain_client=brain,
            query="should I get something for mom?",
        )
        assert result[0].fact["id"] == mom_fact, (
            "fact 'Mom's birthday ...' should match query token 'mom' after "
            "possessive-strip normalization"
        )
        assert {mom_fact, unrelated} == {item.fact["id"] for item in result}
    finally:
        await brain.close()
        _cleanup(path)


def test_normalize_token_handles_possessive_and_apostrophe_edges() -> None:
    """Pin the contract for `_normalize_token`. Code-review issue #1.

    - Possessive 's stripped from end: dad's -> dad, user's -> user.
    - Surrounding apostrophes stripped: 'portland' -> portland.
    - Apostrophes inside contractions are NOT split, but the trailing 's
      branch matches contractions like it's -> it. That's an acceptable
      collision: the alternative (a real contraction parser) is overkill
      for an in-memory rerank, and 'it' is a stopword anyway so the it's
      -> it case never earns a fact a boost in practice.
    - don't -> don (no trailing 's, leading/trailing apostrophe strip
      doesn't apply to medial). Pin this so a future refactor that
      tries to clean up don't doesn't silently change behaviour.
    """
    from ai.fact_retrieval import _normalize_token

    assert _normalize_token("dad's") == "dad"
    assert _normalize_token("User's") == "user"
    assert _normalize_token("'portland'") == "portland"
    assert _normalize_token("it's") == "it"
    # don't has a medial apostrophe with no trailing 's' — the normalizer
    # leaves the apostrophe in place. This token then fails the
    # post-tokenization stopword/length filter the same as any other
    # noisy artifact, so it doesn't cause spurious matches in production.
    assert _normalize_token("don't") == "don't"


@pytest.mark.asyncio
async def test_exclude_memory_types_leaves_the_slots_to_typed_facts(tmp_path: Path) -> None:
    """Batch 5, task 4 (ruling R5): the reply's "Things I Know About This
    User" band asks for the owner's facts WITHOUT her own ``moment`` rows
    (154 of 186 live rows are her emotional reads), so ``max_results``
    counts typed facts instead of being eaten by moods. A caller that does
    not ask gets what it always got."""
    path = _db_path(tmp_path)
    brain = PalliBrain(str(path))
    now = time.time()
    try:
        await brain.initialize()
        for text in ("They seemed tired.", "They were glad about the STL.", "They deflected with a joke."):
            await brain.store_user_memory(user_id=7, memory_type="moment", content=text, importance=0.95)
        cat = await brain.store_user_memory(user_id=7, memory_type="user_fact", content="User's cat is named Mochi.", importance=0.6)
        tea = await brain.store_user_memory(user_id=7, memory_type="preference", content="User takes tea, not coffee.", importance=0.6)

        typed = await get_decay_weighted_facts(
            user_id=7, max_results=2, now=now, brain_client=brain, exclude_memory_types=("moment",),
        )
        assert sorted(item.fact["id"] for item in typed) == sorted([cat, tea])
        assert all(item.fact["memory_type"] != "moment" for item in typed)

        everything = await get_decay_weighted_facts(user_id=7, max_results=2, now=now, brain_client=brain)
        assert all(item.fact["memory_type"] == "moment" for item in everything)  # the moods win both slots when nobody asks
        assert len(await get_decay_weighted_facts(user_id=7, max_results=8, now=now, brain_client=brain, exclude_memory_types=())) == 5
    finally:
        await brain.close()
        _cleanup(path)


# ── Batch 5, task 5 (rulings R24/R41): the old bump gets its caller ──────────
# ``queue_reference_bump`` kept its signature and became a thin wrapper over an
# id-taking sibling, ``queue_reference_bump_ids``, which the reflection calls
# with the user_memory ids of the facts the "Things I Know" band rendered.
# Both share ``_bump_reference_counts``.


class _RecordingBrain:
    def __init__(self) -> None:
        self.calls: list[tuple[int, list[int]]] = []

    async def bump_user_memory_references(self, *, user_id: int, memory_ids: list[int]) -> None:
        self.calls.append((int(user_id), list(memory_ids)))


def _weighted(fact_id) -> DecayWeightedFact:
    return DecayWeightedFact(fact={"id": fact_id, "content": "x"}, effective_score=1.0, recency_factor=1.0, revisit_factor=1.0)


@pytest.mark.asyncio
async def test_queue_reference_bump_ids_bumps_each_id_once_and_skips_what_is_not_an_id() -> None:
    """The sibling: ints (and int-shaped text) reach the brain once each, in
    order; None and non-numeric values are skipped; it returns the task so a
    caller that wants to can wait for it."""
    recorder = _RecordingBrain()
    task = queue_reference_bump_ids(user_id=7, fact_ids=[3, None, "9", 3, "not-an-id"], brain_client=recorder)
    assert task is not None
    await task
    assert recorder.calls == [(7, [3, 9])]


@pytest.mark.asyncio
async def test_queue_reference_bump_ids_with_nothing_to_bump_is_a_no_op() -> None:
    recorder = _RecordingBrain()
    assert queue_reference_bump_ids(user_id=7, fact_ids=[], brain_client=recorder) is None
    assert queue_reference_bump_ids(user_id=7, fact_ids=[None, ""], brain_client=recorder) is None
    await asyncio.sleep(0)
    assert recorder.calls == []


def test_queue_reference_bump_ids_without_a_running_loop_is_skipped() -> None:
    """Sync caller, no loop: skipped at debug, nothing raised — the shape the
    original always had."""
    recorder = _RecordingBrain()
    assert queue_reference_bump_ids(user_id=7, fact_ids=[3], brain_client=recorder) is None
    assert recorder.calls == []


@pytest.mark.asyncio
async def test_queue_reference_bump_is_a_thin_wrapper_over_the_id_sibling(monkeypatch) -> None:
    """The original keeps its signature and its ``-> None``; it extracts the
    ids from the weighted facts (skipping a missing id) and hands them on."""
    import ai.fact_retrieval as fact_retrieval

    handed: list[dict] = []
    monkeypatch.setattr(fact_retrieval, "queue_reference_bump_ids", lambda **kw: handed.append(kw))
    recorder = _RecordingBrain()
    assert queue_reference_bump(user_id=7, facts=[_weighted(3), _weighted(None), _weighted(9)], brain_client=recorder) is None
    assert handed == [{"user_id": 7, "fact_ids": [3, 9], "brain_client": recorder}]


@pytest.mark.asyncio
async def test_queue_reference_bump_still_reaches_the_brain_end_to_end() -> None:
    recorder = _RecordingBrain()
    queue_reference_bump(user_id=7, facts=[_weighted(3), _weighted(None)], brain_client=recorder)
    await asyncio.sleep(0)
    assert recorder.calls == [(7, [3])]
