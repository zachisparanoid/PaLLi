"""The memory look-up behind the "Things I Know About This User" band
(Batch 5, task 4, memory that picks, 2026-09-28) — its own module since the
final review's fix wave, ruling R64.

Recall's wave 1 fetches the memory-of-you search results and the typed
user_memory facts; what happens to them after the gather lives here.

- ``plan_band`` — the entry point recall calls: the memory bullets the
  search returned past the hits' relevance floor (R40, through the SAME
  helper the hits use), minus the ones the memory core already shows
  (``_memory_core_ids``: the same core the adapter renders — same doc,
  budget helper, refs snapshot and day, R38), then the typed facts; eight
  lines at most, bullets first (``plan_facts_band``). Every bullet id that
  cleared the floor rides ``TurnContext.memory_lookup_ids`` BEFORE the
  dedup, for Task 5's reference stamps (R6: "use" = surfaced). A guest's
  turn plans no core and gets every returned bullet (R30). Wrapped whole
  (fix wave, T4 M12): a failure anywhere in it falls back to the facts
  feed exactly as wave 1 fetched it and to no ids, at debug — the module's
  contract, like recall's, is that a recall error never drops the reply.
- ``load_owner_refs`` — wave 1's ninth fetch: the owner's reference stamps,
  loaded ONCE per process into core.memory_refs's snapshot (R7). Owner turns
  only; a failed load is a debug line and the core then ranks by stamps
  alone.
- ``_passes_semantic_cutoff`` — Batch 3's similarity floor, shared with the
  hits merge in recall, which reads it from here.
- ``split_today`` — the today slot (R15, memory that reaches, T12): recall's
  facts fetch brings the owner's ``today`` rows back with the typed facts;
  they are split out here and ride after the band, newest first, for the
  adapter's "## Today, so far" section. recall.py itself is untouched.

Why a module (R64): recall.py stood at 27 in-repo imports against the
coupling budget of 25 once Task 4 added the compiler, memory_refs and the
owner gate to it. Those imports live here now; recall imports this. Pure
where it can be — only ``_memory_core_ids`` and ``load_owner_refs`` reach
outside their arguments.
"""
from __future__ import annotations

import logging

from core.memory_store import TODAY_TYPE
from core.owner_gate import is_guest_turn

logger = logging.getLogger(__name__)


def _passes_semantic_cutoff(r: dict, *, min_sim: float) -> bool:
    """Batch 3 (1.3): the similarity cutoff compares SIMILARITY.

    The memory-of-you search ranks by a blended ``score`` (0.55·lexical +
    0.35·semantic + bonuses) and reports each result's own cosine similarity
    as ``semantic_score``. ``semantic_recall_min_similarity`` is a similarity
    floor, so it is compared against ``semantic_score`` — never the blend,
    which let keyword coincidences through and dropped true hits. A result
    with no similarity known (missing, None, non-numeric — the lexical-only
    search reports None) is not a semantic hit. Ranking stays by ``score``.
    """
    try:
        similarity = float(r.get("semantic_score"))
    except (TypeError, ValueError):
        return False
    return similarity >= float(min_sim)


def _is_memory_bullet(result: object) -> bool:
    return isinstance(result, dict) and str(result.get("source_kind") or "") == "memory_bullet"


def memory_bullet_ids(search_results) -> tuple[str, ...]:
    """The ``mcb_`` id of every memory bullet among ``search_results``, in
    result order, each once (Batch 5, task 4, ruling R6) — recall hands it
    the bullets that cleared the hits' relevance floor (R40). BEFORE the
    dedup against the core: a bullet the core already shows was still
    surfaced for this turn, and Task 5 stamps every surfaced line."""
    ids: list[str] = []
    for r in search_results or ():
        if not _is_memory_bullet(r):
            continue
        item_id = str(r.get("item_id") or "").strip()
        if item_id and item_id not in ids:
            ids.append(item_id)
    return tuple(ids)


def _bullet_line(result: dict) -> str:
    """A memory-bullet search result as the band shows it (ruling R39):
    ``- [stamp] content``, or ``- content`` when its metadata stamp is None.
    The content is the bullet's text without its ``- `` and leading stamps
    (Task 2's item); a trailing ``[archived YYYY-MM-DD]`` marker is part of
    it and stays visible. "" when the result carries no text."""
    content = str(result.get("content") or result.get("snippet") or "").strip()
    if not content:
        return ""
    metadata = result.get("metadata")
    stamp = str(metadata.get("stamp") or "").strip() if isinstance(metadata, dict) else ""
    return f"- [{stamp}] {content}" if stamp else f"- {content}"


def plan_facts_band(search_results, *, core_ids, facts, limit: int = 8) -> list[dict]:
    """The entries of the "## Things I Know About This User" band (Batch 5,
    task 4, ruling R5), in render order: (a) the memory bullets the search
    returned, minus any whose id is in ``core_ids`` (the core already shows
    them), each as ``{"line", "item_id", "source_kind"}`` — the adapter
    prints ``line`` verbatim; then (b) the user_memory facts as ``_w_facts``
    shaped them, minus her own ``moment`` rows (the feed is asked to leave
    them out; this is the belt) and any without a value. At most ``limit``
    entries, bullets first. Pure: no I/O."""
    band: list[dict] = []
    seen: set[str] = set()
    for r in search_results or ():
        if len(band) >= limit:
            break
        if not _is_memory_bullet(r):
            continue
        item_id = str(r.get("item_id") or "").strip()
        if not item_id or item_id in core_ids or item_id in seen:
            continue
        line = _bullet_line(r)
        if not line:
            continue
        seen.add(item_id)
        band.append({"line": line, "item_id": item_id, "source_kind": "memory_bullet"})
    for fact in facts or ():
        if len(band) >= limit:
            break
        if not isinstance(fact, dict) or str(fact.get("memory_type") or "") == "moment":
            continue
        if not str(fact.get("value") or "").strip():
            continue
        band.append(fact)
    return band


def split_today(facts) -> tuple[list[dict], list[dict]]:
    """The facts feed split in two (R15, T12): ``(the band's facts, today
    rows newest first)``. A today row is a fact dict whose ``memory_type`` is
    ``today`` — what the reflection kept in the today slot; everything else
    stays with the band in the order it came. Newest first is by row id (the
    feed's dicts carry no stamp; ids only grow), a row without an int id
    last. Pure: no I/O."""
    band: list = []
    today: list[dict] = []
    for fact in facts or ():
        if isinstance(fact, dict) and str(fact.get("memory_type") or "") == TODAY_TYPE:
            today.append(fact)
        else:
            band.append(fact)

    def _newest(fact: dict) -> int:
        raw = fact.get("id")
        return raw if isinstance(raw, int) and not isinstance(raw, bool) else -1

    today.sort(key=_newest, reverse=True)
    return band, today


def _memory_core_ids(compiled_identity, owner_id) -> frozenset[str]:
    """The ids of the bullets the memory core shows for this owner, planned
    exactly as the adapter renders it (ruling R38: the same doc, the same
    budget helper, the same refs snapshot, the same day), so a line is in
    the core or in the band, never both. Empty when there is no compiled
    identity or the plan fails (a debug line): the band then carries every
    returned bullet — the safe side."""
    if compiled_identity is None:
        return frozenset()
    try:
        from core import memory_refs
        from core.identity.compiler import plan_memory_core
        return plan_memory_core(
            compiled_identity,
            budget_chars=memory_refs.memory_core_budget(),
            refs=memory_refs.snapshot(owner_id),
        ).ids
    except Exception as exc:
        logger.debug("recall: memory core plan failed: %s", exc)
        return frozenset()


async def load_owner_refs(turn) -> None:
    """Wave 1's refs load (Batch 5, task 4, ruling R7): the owner's reference
    stamps for their memory bullets, loaded ONCE per process into
    core.memory_refs's snapshot — the core plan after the gather reads it,
    and so does the adapter's render of the same core (R38). A guest's turn
    (keyed to the guest, origin "guest") loads nothing: none of the owner's
    bands render for it. A failed load is a debug line; the plan then runs
    on the empty snapshot and the core ranks by stamps alone."""
    if is_guest_turn(turn):
        return
    try:
        from core import memory_refs
        await memory_refs.ensure_loaded(turn.owner_id)
    except Exception as exc:
        logger.debug("recall: memory refs load failed: %s", exc)


def plan_band(turn, *, semantic_results, min_sim, compiled_identity, facts):
    """The band and the surfaced ids for this turn, as ``(relevant_facts,
    memory_lookup_ids)`` — what recall assigns after the gather. Rulings
    R5/R6/R39/R40: the memory bullets among ``semantic_results`` that pass
    the hits' relevance floor (``min_sim``, the same helper), their ids in
    result order (BEFORE the dedup — Task 5 stamps every surfaced line),
    then ``plan_facts_band`` over them minus the core's ids (none planned
    for a guest) and the typed ``facts``. Fix wave (T4 M12): wrapped whole —
    on any failure the reply keeps ``facts`` exactly as wave 1 fetched them
    and no ids, and the failure is a debug line.

    The today slot (R15, T12): the owner's ``today`` rows are split out of
    ``facts`` first (``split_today``) and appended after the band, newest
    first — never counted against its eight lines; a guest's turn gets none.
    No today rows: the answer is the band exactly as before."""
    try:
        relevant_bullets = [
            r for r in (semantic_results or ())
            if _is_memory_bullet(r) and _passes_semantic_cutoff(r, min_sim=min_sim)
        ]
        ids = memory_bullet_ids(relevant_bullets)
        guest = is_guest_turn(turn)
        core_ids = frozenset() if guest else _memory_core_ids(compiled_identity, turn.owner_id)
        band_facts, today = split_today(facts)
        band = plan_facts_band(relevant_bullets, core_ids=core_ids, facts=band_facts)
        return band + ([] if guest else today), ids
    except Exception as exc:
        logger.debug("recall: the memory look-up band failed (%s) — the facts feed stands as fetched", exc)
        return facts, ()
