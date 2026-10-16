from __future__ import annotations

from dataclasses import dataclass, field
import asyncio
import json
import logging
import math
import os
import re
import time
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional

import aiohttp

try:
    # Batch 3 (1.3): the memory-of-you cosine pass runs on numpy when it is
    # importable; ``_score_embeddings`` falls back to plain Python otherwise.
    import numpy as _numpy
except ImportError:  # pragma: no cover - the fallback is exercised by monkeypatching the handle
    _numpy = None

from core.agent.payload_masking import for_provider
from core.config import config
from core.database import brain
from core.identity import identity_service
from core.identity.frontmatter import dump_frontmatter, parse_frontmatter
from core.memory_bullets import (  # noqa: F401 — re-exported (ruling R35)
    _ARCHIVED_MARKER_RE,
    _BULLET_STAMP_RE,
    _TEMPLATE_PLACEHOLDER_BULLETS,
    _normalize_text,
    _sha,
    _split_bullet,
    _stamp_time,
    item_id_for,
)
from core.reminder_continuity import build_reminder_continuity_view

logger = logging.getLogger(__name__)

_MEANINGFUL_MEMORY_RE = re.compile(
    r"\b("
    r"remember that|call me|my name is|i prefer|i'd prefer|i like|i love|i hate|i don't like|"
    r"keep replies|keep responses|i'm trying to|my goal is|i want to|i need to|we decided|"
    r"use origin|use upstream|take the safer path|choose the safer path|pick the safer path"
    r")\b",
    re.IGNORECASE,
)
_MEMORY_QUERY_RE = re.compile(
    r"\b("
    r"remember|recall|what do you remember|what did we decide|where did we leave|"
    r"pick back up|pick this back up|pick that back up|pick it back up|"
    r"resume|continue where|continue from|"
    r"still open|still pending|what changed lately|what changed recently|"
    r"what happened lately|what happened recently|what did we finish|"
    r"what were we doing|what's next|what is next|"
    r"memory|continuity|left off|last time|last thing"
    r")\b",
    re.IGNORECASE,
)
_RECENT_MEMORY_QUERY_RE = re.compile(
    r"\b("
    r"what changed lately|what changed recently|what happened lately|what happened recently|"
    r"what did we finish|latest update|latest updates|recent updates|"
    r"since last time|since yesterday|lately|recently"
    r")\b",
    re.IGNORECASE,
)
_OPEN_THREAD_QUERY_RE = re.compile(
    r"\b("
    r"still open|still pending|pick back up|pick this back up|pick that back up|"
    r"pick it back up|resume|"
    r"continue where|continue from|what's next|what is next|unfinished|"
    r"follow up|follow-up|open loop|open loops|pending"
    r")\b",
    re.IGNORECASE,
)
_VAGUE_CONTINUITY_RE = re.compile(
    r"\b(that|this|it|those|them|earlier|before|last time|last thing|recently|lately)\b",
    re.IGNORECASE,
)
_TOP_LEVEL_SECTION_RE = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)


@dataclass(frozen=True)
class MemoryRetrievalPlan:
    should_retrieve: bool
    search_query: str = ""
    reason: str = ""
    prefer_recent_results: bool = False
    prefer_active_threads: bool = False
    preferred_related_refs: List[str] = field(default_factory=list)
    carry_forward_hint: str = ""


@dataclass(frozen=True)
class MemoryRetrievalBias:
    preferred_related_refs: List[str] = field(default_factory=list)
    carry_forward_hint: str = ""


def _compact_text(value: Any, *, limit: int = 240) -> str:
    text = _normalize_text(value)
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def _metadata_dict(value: Any) -> Dict[str, Any]:
    """An index row's ``metadata`` as a dict: already decoded (database.py's
    JSON columns), still JSON text, or empty/absent."""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError):
            return {}
    return dict(value) if isinstance(value, dict) else {}


# Batch 5, task 2 (memory that picks, 2026-09-28): every bullet of MEMORY.md
# and MEMORY_ARCHIVE.md is one search item, `mcb_<sha12>`. The id hashes the
# bullet's TEXT alone (ruling R4): no leading "- ", no leading
# `[YYYY-MM-DD]` stamps (58 live lines carry two), no trailing
# `[archived YYYY-MM-DD]` marker, whitespace collapsed — so the sweep's stamp
# write and its move to the archive never change an id, and a line keeps its
# reference stamps (core.memory_refs) wherever it sits. The grammar itself
# lives in core.memory_bullets (task 3, ruling R35: a leaf the identity
# compiler can import without this module's brain); the names are imported
# above and re-exported here for the callers and tests that know this
# module.


def _build_index_item(
    *,
    item_id: str,
    owner_user_id: int,
    source_kind: str,
    source_id: str,
    title: str,
    content: str,
    salience: str,
    tags: List[str],
    related_refs: List[str],
    created_at: float,
    updated_at: float,
    metadata: Dict[str, Any],
) -> Dict[str, Any]:
    """The one shape of a memory-index item (the service's ``_index_item``
    and the pure ``_bullet_index_items`` both build it here)."""
    body = _normalize_text(content)
    return {
        "item_id": item_id,
        "owner_user_id": int(owner_user_id),
        "source_kind": source_kind,
        "source_id": source_id,
        "title": _compact_text(title or body, limit=140),
        "content": body,
        "snippet": _compact_text(body, limit=220),
        "salience": str(salience or "low").strip().lower() or "low",
        "tags": [str(tag).strip().lower() for tag in tags if str(tag).strip()],
        "related_refs": [str(ref).strip() for ref in related_refs if str(ref).strip()],
        "content_hash": _sha(body),
        "created_at": float(created_at or time.time()),
        "updated_at": float(updated_at or time.time()),
        "metadata": metadata or {},
    }


def _bullet_index_items(
    doc_text: str,
    *,
    doc_key: str,
    owner_user_id: int,
    doc_mtime: Optional[float] = None,
) -> List[Dict[str, Any]]:
    """One ``memory_bullet`` index item per ``- `` line inside a ``## ``
    section of ``doc_text`` (the body of MEMORY.md or MEMORY_ARCHIVE.md).
    Pure: no I/O. Content is the bullet without its ``- `` and leading
    stamps; title is the section; metadata is ``{"section", "doc", "stamp"}``
    (the FIRST stamp, or None). The templates' placeholder bullets, empty
    bodies and a second line with the same text are skipped. A stamped
    bullet is dated by its stamp (created_at = updated_at = the stamp's
    local midnight) — the file's mtime would re-date every line at every
    write; an unstamped one takes ``doc_mtime`` (like the ``mcs_`` items) or
    now."""
    doc_time = float(doc_mtime) if doc_mtime else time.time()
    items: List[Dict[str, Any]] = []
    seen: set[str] = set()
    section: Optional[str] = None
    for raw_line in str(doc_text or "").splitlines():
        heading = _TOP_LEVEL_SECTION_RE.match(raw_line)
        if heading:
            section = heading.group(1).strip()
            continue
        if section is None or not raw_line.startswith("- "):
            continue
        stamp, body, key = _split_bullet(raw_line)
        if not key or key in _TEMPLATE_PLACEHOLDER_BULLETS:
            continue
        item_id = item_id_for(raw_line)
        if item_id in seen:
            continue
        seen.add(item_id)
        when = _stamp_time(stamp)
        if when is None:
            when = doc_time
        items.append(
            _build_index_item(
                item_id=item_id,
                owner_user_id=int(owner_user_id),
                source_kind="memory_bullet",
                source_id=doc_key,
                title=section,
                content=body,
                salience="medium",
                tags=["memory-bullet"],
                related_refs=[],
                created_at=when,
                updated_at=when,
                metadata={"section": section, "doc": doc_key, "stamp": stamp},
            )
        )
    return items


def _cosine_similarity(left: List[float], right: List[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    dot = sum(float(a) * float(b) for a, b in zip(left, right))
    left_norm = math.sqrt(sum(float(a) * float(a) for a in left))
    right_norm = math.sqrt(sum(float(b) * float(b) for b in right))
    if left_norm <= 0 or right_norm <= 0:
        return 0.0
    return dot / (left_norm * right_norm)


def _score_embeddings(query_vector: List[float], embeddings: Dict[str, Dict[str, Any]]) -> Dict[str, float]:
    """Cosine similarity of the query against every cached item vector,
    clamped at zero: ``{item_id: similarity}`` (Batch 3, 1.3).

    Synchronous on purpose — ``search()`` runs it via ``asyncio.to_thread`` so
    the pass over thousands of vectors never sits on the event loop. numpy
    when importable, the plain-Python ``_cosine_similarity`` otherwise; the
    two agree to 1e-9. A vector that is missing, empty or of another length
    than the query scores 0.0, as does everything when the query is empty.
    """
    scores: Dict[str, float] = {item_id: 0.0 for item_id in embeddings}
    if not query_vector:
        return scores
    if _numpy is None:
        for item_id, embedding in embeddings.items():
            vector = list((embedding or {}).get("vector") or [])
            scores[item_id] = max(0.0, float(_cosine_similarity(query_vector, vector)))
        return scores
    query = _numpy.asarray(query_vector, dtype=_numpy.float64)
    query_norm = float(_numpy.linalg.norm(query))
    if query_norm <= 0.0:
        return scores
    usable_ids: List[str] = []
    usable_vectors: List[List[float]] = []
    for item_id, embedding in embeddings.items():
        vector = list((embedding or {}).get("vector") or [])
        if len(vector) != len(query_vector):
            continue
        usable_ids.append(item_id)
        usable_vectors.append(vector)
    if not usable_vectors:
        return scores
    matrix = _numpy.asarray(usable_vectors, dtype=_numpy.float64)
    denominators = _numpy.linalg.norm(matrix, axis=1) * query_norm
    safe_denominators = _numpy.where(denominators > 0.0, denominators, 1.0)
    cosines = _numpy.where(denominators > 0.0, (matrix @ query) / safe_denominators, 0.0)
    for item_id, cosine in zip(usable_ids, cosines.tolist()):
        scores[item_id] = max(0.0, float(cosine))
    return scores


# Phase B / B4 step 5 (2026-05-09) — preferences-doc rebuilder.
# ---------------------------------------------------------------------------
# Marker stored in ``source_detail.source`` so rebuilt rows are
# distinguishable from rows written by other paths (legacy
# ``upsert_user_fact``, the parallel ``analyze_and_store_facts``
# pipeline, the ``last_session_closed_at`` migration, etc.). The DELETE
# scrub before each rebuild is scoped to this marker so a rebuild
# never touches rows it didn't write.
_REBUILD_SOURCE_MARKER = "preferences_doc"
# Doc-frontmatter prefix for user-volunteered preferences. Established
# in B4 step 4 (commit ad39f0e) for ``core/turn/reflect.py``'s
# ``_apply_user_facts``; the rebuilder mirrors the prefix on read so
# both producer and consumer agree on the namespace.
_PREF_FRONTMATTER_PREFIX = "pref_"
# The one identity document this pair of paths is coupled to: the
# rebuilder materializes rows FROM it and the forget path erases
# entries IN it. Named so the coupling is one constant, like the two
# above, rather than a literal repeated at every seam.
_PREF_DOC_KEY = "preferences"


async def rebuild_user_memory_from_documents(
    *,
    owner_user_id: int,
) -> Dict[str, Any]:
    """Materialize the ``user_memory`` cache from identity documents.

    Phase B / B4 step 5 (2026-05-09) — audit findings IM-CRIT-1,
    IM-CRIT-2, IM-HIGH-8: B4 step 4 closed the producer side by routing
    reflection's user-fact writes to the structured ``preferences``
    identity document. This function closes the consumer side: it
    materializes the ``user_memory`` cache from the ``preferences`` doc
    on demand, so the cache becomes a derived view of the doc rather
    than the canonical store.

    Architecture:
      * **Source documents** — only ``preferences`` for now. The doc's
        ``pref_<key>: <value>`` frontmatter entries become rows. Other
        identity docs (``personality_profile``, ``action_preferences``,
        ``relationship_state``, ``memory_summary``) are NOT mirrored
        because they have their own readers via ``compile_identity``;
        copying them into ``user_memory`` would double-count and
        re-introduce the kind of multi-pipeline confusion (IM-HIGH-1)
        that B4 is undoing.
      * **Full rebuild** — DELETE prior derived rows first, then INSERT
        fresh ones. Idempotent (running twice produces the same state)
        and correct under add/remove (a removed ``pref_*`` entry
        vanishes from the cache, not just gets shadowed by a new one).
        The DELETE is scoped via ``source_detail.source`` so only rows
        from a previous rebuild get scrubbed; pre-existing user_memory
        rows written by other paths are NOT touched.
      * **Per-row failure isolation** — a transient DB error on one
        ``store_user_memory`` doesn't short-circuit the rest of the
        batch. The result advertises ``status='partial'`` with a
        ``failed`` count so callers can detect and retry.
      * **Pre-write DELETE failure** — aborts the rebuild without
        writing fresh rows. Better to leave the cache in its current
        (possibly empty) state than to double up old + new rows.
      * **Mapping to user_memory shape**:
          - ``memory_type='user_fact'`` matches the legacy
            ``upsert_user_fact`` shape; existing readers (the
            ``_safe_retrieve_facts`` / ``get_decay_weighted_facts``
            chain) key on ``content`` shaped as ``"<key>: <value>"``.
          - ``content`` is ``"<bare-key>: <value>"`` — the ``pref_``
            prefix is stripped on the way in.
          - ``tags`` are ``[<bare-key>, "preferences_doc"]`` for
            quick filtering.
          - ``source_detail`` records ``{source, fact_key, fact_value}``
            so probes / diagnostics can identify rows that came from
            this rebuild.
          - ``importance`` is fixed at the default since the doc
            doesn't carry per-entry confidence (the producer-side
            ``ReflectionFactAdd.confidence`` lands in the changelog,
            not the frontmatter).

    Out of scope:
      * ``memory_index_items`` rebuild (IM-HIGH-8): handled by the
        existing ``MemoryWorkspaceService.rebuild_index`` for the
        FTS-feeder index. Kept as a sibling — running both is the
        operator's call.
      * Auto-trigger / scheduling: this function is the building block
        only. Operator-triggered or event-driven invocation is a
        follow-up; the plan calls for an "on demand" rebuilder, not
        an auto-runner.

    Args:
      owner_user_id: The owner whose preferences doc to read and whose
        ``user_memory`` rows to rebuild. Routed through
        ``identity_service.load_identity_bundle`` with
        ``platform="owner"`` so the storage target matches what
        ``silently_maintain_document`` writes from reflect.

    Returns:
      A status dict with keys:
        - ``status``: ``"ok"`` (clean rebuild), ``"partial"`` (some
          writes failed), or ``"error"`` (the prior-rows DELETE failed
          so no writes attempted).
        - ``owner_user_id``: echo of the input.
        - ``scrubbed``: rows deleted by the prior-rows scrub.
        - ``written``: rows successfully inserted.
        - ``failed``: rows where the INSERT raised (only > 0 in the
          ``"partial"`` case).
    """
    owner_id = int(owner_user_id)
    result: Dict[str, Any] = {
        "status": "ok",
        "owner_user_id": owner_id,
        "scrubbed": 0,
        "written": 0,
        "failed": 0,
    }

    # Step 1: scrub the prior derived rows. If this fails, do NOT
    # proceed to write — half-old half-new is worse than half-old.
    try:
        scrubbed = await brain.delete_user_memory_by_source(
            user_id=owner_id,
            source=_REBUILD_SOURCE_MARKER,
        )
        result["scrubbed"] = int(scrubbed or 0)
    except Exception as exc:
        logger.warning(
            "rebuild_user_memory_from_documents: scrub failed for owner=%s: %s",
            owner_id, exc,
        )
        result["status"] = "error"
        result["error"] = f"{type(exc).__name__}: {exc}"
        return result

    # Step 2: load the preferences doc. ``load_identity_bundle`` resolves
    # storage to ``(owner_id, "owner")`` and returns the bundle in its
    # current on-disk shape.
    try:
        bundle = await identity_service.load_identity_bundle(
            user_id=owner_id,
            platform="owner",
            identity_owner_id=owner_id,
        )
    except Exception as exc:
        logger.warning(
            "rebuild_user_memory_from_documents: bundle load failed for owner=%s: %s",
            owner_id, exc,
        )
        # Bundle load failure: the scrub already happened, so
        # user_memory is now empty for this user. That's the safe
        # state — better than half-fresh half-stale.
        result["status"] = "error"
        result["error"] = f"{type(exc).__name__}: {exc}"
        return result

    pref_doc = bundle.user_documents.get(_PREF_DOC_KEY) if bundle else None
    if pref_doc is None:
        # No preferences doc yet (user never reflected anything, or
        # bundle wasn't materialized). Empty cache is the correct
        # state. Scrub already ran defensively.
        return result

    # Step 3: extract pref_<key>: <value> entries. Anything that
    # doesn't start with ``pref_`` is policy-managed metadata
    # (``version``, ``platform``, ``user_id``, ``last_updated_by``,
    # ``preferences_summary``, etc.) and must NOT become user_memory
    # rows — copying them would re-introduce the IM-HIGH-2-style
    # bookkeeping pollution that B4 is undoing.
    frontmatter = dict(pref_doc.frontmatter or {})
    pref_entries: List[tuple[str, str]] = []
    for fm_key, fm_value in frontmatter.items():
        if not isinstance(fm_key, str):
            continue
        if not fm_key.startswith(_PREF_FRONTMATTER_PREFIX):
            continue
        bare_key = fm_key[len(_PREF_FRONTMATTER_PREFIX):]
        if not bare_key:
            # Defensive: ``pref_`` alone strips to empty; reflect's
            # writer guards against this but the rebuilder shouldn't
            # trust the doc to be perfectly shaped.
            continue
        clean_value = "" if fm_value is None else str(fm_value).strip()
        if not clean_value:
            continue
        pref_entries.append((bare_key, clean_value))

    # Step 4: write each entry. Per-row try/except so one failure
    # doesn't lose the rest of the batch.
    for bare_key, value in pref_entries:
        try:
            await brain.store_user_memory(
                user_id=owner_id,
                memory_type="user_fact",
                content=f"{bare_key}: {value}",
                importance=0.5,
                tags=[bare_key, _REBUILD_SOURCE_MARKER],
                salience_weight=1.0,
                source_detail={
                    "source": _REBUILD_SOURCE_MARKER,
                    "fact_key": bare_key,
                    "fact_value": value,
                },
            )
            result["written"] += 1
        except Exception as exc:
            logger.warning(
                "rebuild_user_memory_from_documents: write failed for owner=%s key=%r: %s",
                owner_id, bare_key, exc,
            )
            result["failed"] += 1

    if result["failed"] > 0:
        result["status"] = "partial"
    return result


class ForgetSourceWriteError(RuntimeError):
    """The identity document that was this memory's source could not be
    changed — the load, the removal write, or the check that the key
    really left it. Nothing has been deleted when this is raised, so the
    owner's memory is exactly as it was and the ask can simply be made
    again. Review fix 2 (2026-09-10): the caller must be able to tell
    THIS failure from any other, because only this one is honestly
    described as "the source document write failed".
    """


class ForgetRowDeleteError(RuntimeError):
    """The source WAS erased and then deleting the row raised. A real,
    partial mutation: the fact can no longer be rebuilt from the
    document, but the stale row is still sitting there until the next
    rebuild scrubs it. Its own class so the caller never reports it as a
    failed document write — the document write succeeded.
    """


async def forget_user_memory(
    *,
    user_id: int,
    memory_id: int,
) -> Dict[str, Any]:
    """Forget one remembered fact — the row AND the source it was made from.

    Week 2 task 6 (2026-09-10). Deleting the ``user_memory`` row alone
    was a promise with a timer on it: rows seeded by
    ``rebuild_user_memory_from_documents`` are a *derived view* of the
    ``preferences`` identity document, so the very next rebuild read
    ``pref_<key>`` back out of the doc and put the fact she had just
    said she'd forgotten straight back. "Erase that" has to reach the
    source of truth.

    Only the rebuild's own marker counts as a document source: a row
    whose ``source_detail.source`` is ``preferences_doc`` and which
    carries a ``fact_key``. That is exactly what the rebuilder writes
    (see Step 4 above), and nothing else is touched — a row written by
    any other path deletes as it always did and reports no source.

    This is the FORGET verb only. "That changed" is an update and keeps
    its own path (reflect's ``silently_maintain_document`` write); the
    two are not conflated here.

    Args:
      user_id: the owner the row must belong to. Scoping is the caller's
        (the route resolves the owner id); a row owned by anyone else
        reads as missing rather than as someone else's.
      memory_id: the row to forget.

    Returns:
      ``{"deleted_row": bool, "source_removed": str | None}`` —
      ``source_removed`` is the frontmatter key the write's own result
      proves is gone from the preferences document (e.g.
      ``"pref_coffee"``), or ``None`` when there was no document entry
      behind the row.

    Raises:
      ``ForgetSourceWriteError`` when the document load, the removal
      write, or the post-write check fails — the source is erased FIRST,
      so nothing has been deleted and the owner's memory is untouched.
      ``ForgetRowDeleteError`` when the key was erased and the row delete
      then raised — a real partial mutation, and never to be reported as
      a failed document write. Anything else (a failed row READ, say)
      propagates untouched rather than wearing a label that is not true.
      A failed forget must be a failed forget, never an "ok".
    """
    owner_id = int(user_id)
    row_id = int(memory_id)

    row = await brain.get_user_memory(user_id=owner_id, memory_id=row_id)
    if not row:
        return {"deleted_row": False, "source_removed": None}

    # Does this row name the rebuilder's own marker? Nothing else counts
    # as a document source.
    frontmatter_key: Optional[str] = None
    source_detail = row.get("source_detail")
    if (
        isinstance(source_detail, dict)
        and str(source_detail.get("source") or "") == _REBUILD_SOURCE_MARKER
    ):
        fact_key = str(source_detail.get("fact_key") or "").strip()
        if fact_key:
            frontmatter_key = f"{_PREF_FRONTMATTER_PREFIX}{fact_key}"

    # THE SOURCE GOES FIRST (review fix, 2026-09-10). Deleting the row
    # first and then failing to reach the document left the owner with a
    # 200 "ok" for a fact that the next rebuild would hand straight back.
    # In this order a failed document write means nothing has been
    # deleted yet, so it can simply RAISE: the route answers 500 and the
    # owner's memory is exactly as it was. (The reverse failure — the key
    # is gone but the row delete does not land — is harmless: the next
    # rebuild scrubs by source and finds no key to re-seed.)
    source_removed: Optional[str] = None
    if frontmatter_key is not None:
        # Only the document work wears ForgetSourceWriteError — review
        # fix 2: a blanket catch anywhere wider put "the source document
        # write failed" on failures that never touched the document.
        try:
            # Same storage target the rebuild reads from: the owner's own
            # (owner_id, "owner") preferences document.
            bundle = await identity_service.load_identity_bundle(
                user_id=owner_id,
                platform="owner",
                identity_owner_id=owner_id,
            )
            pref_doc = bundle.user_documents.get(_PREF_DOC_KEY) if bundle else None
            if pref_doc is not None and frontmatter_key in (pref_doc.frontmatter or {}):
                written = await identity_service.update_structured_document(
                    user_id=owner_id,
                    platform="owner",
                    doc_key=_PREF_DOC_KEY,
                    updates={},
                    remove_keys=(frontmatter_key,),
                    actor_type="user",
                    actor_id=str(owner_id),
                    reason="forget_user_memory",
                    identity_owner_id=owner_id,
                )
                # Claim the removal from the write's OWN result, never
                # from having asked for it.
                if frontmatter_key in dict(getattr(written, "frontmatter", None) or {}):
                    raise ForgetSourceWriteError(
                        f"forget_user_memory: {frontmatter_key!r} survived the "
                        f"removal write on the {_PREF_DOC_KEY} document for "
                        f"owner={owner_id}; the row was NOT deleted"
                    )
                source_removed = frontmatter_key
            # else: the row claimed a document source the document no
            # longer has. Nothing to remove, so nothing is reported.
        except ForgetSourceWriteError:
            raise
        except Exception as exc:
            raise ForgetSourceWriteError(
                f"forget_user_memory: could not erase {frontmatter_key!r} from the "
                f"{_PREF_DOC_KEY} document for owner={owner_id}; the row was NOT "
                f"deleted: {exc}"
            ) from exc

    try:
        deleted = await brain.delete_user_memory(user_id=owner_id, memory_id=row_id)
    except Exception as exc:
        if source_removed is None:
            # Nothing was mutated; this is just a failed delete.
            raise
        raise ForgetRowDeleteError(
            f"forget_user_memory: {source_removed!r} was erased from the "
            f"{_PREF_DOC_KEY} document for owner={owner_id} but row {row_id} could "
            f"not be deleted; the next rebuild clears it: {exc}"
        ) from exc

    return {"deleted_row": bool(deleted), "source_removed": source_removed}


def _forget_when_done(
    tasks: Dict[int, "asyncio.Task[None]"], owner_user_id: int, task: "asyncio.Task[None]"
) -> None:
    """Batch 8 Task 11 Extra-2: a finished background task leaves its dict.

    The per-owner dicts kept a finished task until the next schedule replaced
    it, so the module singleton held it — and anything a stored traceback
    references (DEFECT §49 named this as the Batch 8 cleanup). The callback
    pops the task only while it is still that owner's current one (a newer
    schedule is never dropped by an older task finishing), and reads its
    exception unless it was cancelled, so nothing is left "never retrieved".
    """

    def _done(finished: "asyncio.Task[None]") -> None:
        if tasks.get(owner_user_id) is finished:
            tasks.pop(owner_user_id, None)
        if not finished.cancelled():
            finished.exception()

    task.add_done_callback(_done)


class MemoryWorkspaceService:
    def __init__(self) -> None:
        self._local_model_name = ""
        self._local_model: Any = None
        # Serializes the (one-time, threaded) model load so concurrent turns
        # don't each kick off a cold import.
        self._local_model_lock = asyncio.Lock()
        # Per-owner background embedding-backfill tasks. The backlog drain runs
        # OFF the per-turn path so a search never blocks on it.
        self._backfill_tasks: Dict[int, "asyncio.Task[None]"] = {}
        # Per-owner background index-rebuild tasks + last-rebuild timestamps.
        # The full source re-index (~24s for ~130 items) must not run inline in
        # the per-turn search; it's scheduled here, debounced by TTL.
        self._index_rebuild_tasks: Dict[int, "asyncio.Task[None]"] = {}
        self._last_index_rebuild: Dict[int, float] = {}
        self._index_rebuild_ttl_seconds = 180.0

    def _thread_and_lane_from_refs(self, refs: Iterable[Any]) -> tuple[str, str]:
        thread_id = ""
        lane_id = ""
        for ref in refs:
            text = _normalize_text(ref)
            if not text or ":" not in text:
                continue
            prefix, value = text.split(":", 1)
            clean_value = _normalize_text(value)
            if not clean_value:
                continue
            normalized_prefix = prefix.strip().lower()
            if normalized_prefix == "thread" and not thread_id:
                thread_id = clean_value
            elif normalized_prefix == "lane" and not lane_id:
                lane_id = clean_value
            if thread_id and lane_id:
                break
        return thread_id, lane_id

    def memory_enabled(self) -> bool:
        return bool(getattr(config, "MEMORY_WORKSPACE_ENABLED", True))

    def index_enabled(self) -> bool:
        return bool(getattr(config, "MEMORY_INDEX_ENABLED", True))

    def should_retrieve_for_message(self, text: str) -> bool:
        return self.build_retrieval_plan(query=text).should_retrieve

    def build_retrieval_plan(
        self,
        *,
        query: str,
        history: Optional[List[Dict[str, Any]]] = None,
        reminder_context_note: Optional[str] = None,
        recent_continuity_context_note: Optional[str] = None,
        recent_agentic_context_note: Optional[str] = None,
    ) -> MemoryRetrievalPlan:
        if not self.memory_enabled():
            return MemoryRetrievalPlan(should_retrieve=False)

        clean_query = _normalize_text(query)
        if not clean_query or not _MEMORY_QUERY_RE.search(clean_query):
            return MemoryRetrievalPlan(should_retrieve=False)

        prefer_recent_results = bool(_RECENT_MEMORY_QUERY_RE.search(clean_query))
        prefer_active_threads = bool(_OPEN_THREAD_QUERY_RE.search(clean_query))
        reason = "explicit_recall"
        if prefer_recent_results:
            reason = "recent_continuity"
        elif prefer_active_threads:
            reason = "resume_open_threads"

        continuity_bias = self._build_retrieval_bias(history=history)
        return MemoryRetrievalPlan(
            should_retrieve=True,
            search_query=self._shape_retrieval_query(
                query=clean_query,
                carry_forward_hint=continuity_bias.carry_forward_hint,
                reminder_context_note=reminder_context_note,
                recent_continuity_context_note=recent_continuity_context_note,
                recent_agentic_context_note=recent_agentic_context_note,
                prefer_recent_results=prefer_recent_results,
                prefer_active_threads=prefer_active_threads,
            ),
            reason=reason,
            prefer_recent_results=prefer_recent_results,
            prefer_active_threads=prefer_active_threads,
            preferred_related_refs=list(continuity_bias.preferred_related_refs),
            carry_forward_hint=continuity_bias.carry_forward_hint,
        )

    def owner_memory_dir(self, owner_user_id: int) -> str:
        return os.path.join(identity_service.owner_dir(int(owner_user_id)), "memory")

    def owner_memory_doc_path(self, owner_user_id: int) -> str:
        return identity_service.owner_doc_path(int(owner_user_id), "memory")

    def owner_memory_summary_path(self, owner_user_id: int) -> str:
        return identity_service.owner_doc_path(int(owner_user_id), "memory_summary")

    def owner_memory_archive_path(self, owner_user_id: int) -> str:
        return identity_service.owner_doc_path(int(owner_user_id), "memory_archive")

    async def ensure_workspace(self, *, owner_user_id: int) -> None:
        if not self.memory_enabled():
            return
        await identity_service.ensure_user_identity_bundle(
            user_id=int(owner_user_id),
            platform="owner",
            identity_owner_id=int(owner_user_id),
        )
        os.makedirs(self.owner_memory_dir(int(owner_user_id)), exist_ok=True)

    async def capture_post_turn(
        self,
        *,
        owner_user_id: int,
        platform: str,
        user_message: str,
        assistant_reply: str,
        thread_id: str = "",
        thread_kind: str = "",
        thread_label: str = "",
        lane_id: str = "",
        lane_kind: str = "",
        lane_label: str = "",
        is_voice_message: bool = False,
    ) -> Optional[Dict[str, Any]]:
        message = _normalize_text(user_message)
        if not message or not _MEANINGFUL_MEMORY_RE.search(message):
            return None
        clean_thread_id = _normalize_text(thread_id)
        clean_thread_kind = _normalize_text(thread_kind)
        clean_thread_label = _normalize_text(thread_label)
        clean_lane_id = _normalize_text(lane_id)
        clean_lane_kind = _normalize_text(lane_kind)
        clean_lane_label = _normalize_text(lane_label)
        summary = _compact_text(message, limit=220)
        why_it_matters = "This looks like a user preference, goal, or decision that may matter later."
        if assistant_reply.strip():
            why_it_matters += " I acknowledged it in the same turn."
        tags: List[str] = []
        if "call me" in message.lower() or "my name is" in message.lower():
            tags.append("identity")
        if "prefer" in message.lower() or "keep replies" in message.lower():
            tags.append("preference")
        if "trying to" in message.lower() or "goal" in message.lower():
            tags.append("goal")
        if "safer path" in message.lower() or "use origin" in message.lower() or "use upstream" in message.lower():
            tags.append("decision")
        if is_voice_message:
            tags.append("voice")
        if clean_thread_kind:
            tags.append(f"thread_kind:{clean_thread_kind}")
        if clean_lane_kind:
            tags.append(f"lane_kind:{clean_lane_kind}")
        source_scope = clean_thread_id or clean_lane_id or platform
        related_refs = [f"surface:{platform}"]
        if clean_thread_id:
            related_refs.append(f"thread:{clean_thread_id}")
        if clean_lane_id:
            related_refs.append(f"lane:{clean_lane_id}")
        title_context = clean_thread_label or clean_lane_label
        return await self.capture_event(
            owner_user_id=int(owner_user_id),
            source_kind="conversation_turn",
            source_id=f"{source_scope}:{_sha(message)[:12]}",
            title=f"Conversation Memory: {title_context}" if title_context else "Conversation Memory",
            summary=summary,
            why_it_matters=why_it_matters,
            salience="medium" if "goal" in tags or "decision" in tags else "low",
            tags=tags,
            related_refs=related_refs,
            curate=("identity" in tags or "goal" in tags or "decision" in tags),
        )

    async def capture_event(
        self,
        *,
        owner_user_id: int,
        source_kind: str,
        source_id: str,
        title: str,
        summary: str,
        why_it_matters: str,
        salience: str = "medium",
        tags: Optional[List[str]] = None,
        related_refs: Optional[List[str]] = None,
        curate: bool = False,
    ) -> Optional[Dict[str, Any]]:
        if not self.memory_enabled():
            return None
        clean_source_kind = _normalize_text(source_kind).lower().replace(" ", "_")
        clean_source_id = _normalize_text(source_id)
        clean_summary = _compact_text(summary, limit=260)
        clean_why = _compact_text(why_it_matters, limit=220)
        if not clean_source_kind or not clean_summary:
            return None
        await self.ensure_workspace(owner_user_id=int(owner_user_id))
        now = datetime.now().astimezone()
        day_key = now.strftime("%Y-%m-%d")
        entry_id = f"mem_{_sha(f'{clean_source_kind}|{clean_source_id}|{clean_summary}')[:16]}"
        day_path = self._day_log_path(int(owner_user_id), day_key)
        existing_entries = self._parse_daily_entries(self._read_text(day_path) if os.path.exists(day_path) else "")
        if any(str(item.get("entry_id") or "") == entry_id for item in existing_entries):
            await self.rebuild_index(owner_user_id=int(owner_user_id))
            return {"entry_id": entry_id, "date": day_key, "deduped": True}

        if not os.path.exists(day_path):
            self._write_text(day_path, self._render_day_shell(day_key))
        markdown = self._read_text(day_path).rstrip()
        timestamp = now.strftime("%Y-%m-%d %H:%M:%S %Z")
        tag_text = ", ".join(sorted({t.strip().lower() for t in tags or [] if t and t.strip()})) or "none"
        related_text = ", ".join(sorted({r.strip() for r in related_refs or [] if r and r.strip()})) or "none"
        block = (
            f"\n\n## {timestamp}\n"
            f"- Entry ID: {entry_id}\n"
            f"- Source: {clean_source_kind}:{clean_source_id or entry_id}\n"
            f"- Salience: {str(salience or 'medium').strip().lower() or 'medium'}\n"
            f"- Tags: {tag_text}\n"
            f"- Related: {related_text}\n"
            f"- Summary: {clean_summary}\n"
            f"- Why It Matters: {clean_why or 'Continuity value recorded.'}\n"
        )
        self._write_text(day_path, markdown + block + "\n")
        if curate:
            await self._merge_curated_memory(
                owner_user_id=int(owner_user_id),
                summary=clean_summary,
                why_it_matters=clean_why,
                section=self._select_curated_section(
                    source_kind=clean_source_kind,
                    tags=tags or [],
                    salience=str(salience or "medium"),
                ),
            )
        # Wave 4: _refresh_memory_summary stub removed; core/turn/reflect.py
        # owns memory-summary writeback now (gated on
        # decision.notable_for_reflection).
        await self.rebuild_index(owner_user_id=int(owner_user_id))
        return {"entry_id": entry_id, "date": day_key, "deduped": False}

    async def rebuild_index(self, *, owner_user_id: int) -> Dict[str, Any]:
        if not self.index_enabled():
            return {"status": "disabled"}
        await self.ensure_workspace(owner_user_id=int(owner_user_id))
        items = await self._collect_index_items(owner_user_id=int(owner_user_id))
        existing = {
            str(item.get("item_id") or ""): item
            for item in await brain.list_memory_index_items(owner_user_id=int(owner_user_id), limit=4096)
        }
        current_ids = {str(item["item_id"]) for item in items}
        stale_ids = [item_id for item_id in existing if item_id and item_id not in current_ids]
        if stale_ids:
            await brain.delete_memory_index_items(item_ids=stale_ids)
        await brain.clear_memory_index_fts(owner_user_id=int(owner_user_id))
        for item in items:
            await brain.upsert_memory_index_item(**item)
            await brain.upsert_memory_index_fts(
                item_id=str(item["item_id"]),
                owner_user_id=int(owner_user_id),
                source_kind=str(item["source_kind"]),
                title=str(item.get("title") or ""),
                content=str(item.get("content") or ""),
                tags=list(item.get("tags") or []),
            )
        return {"status": "ok", "count": len(items)}

    async def build_overview(self, *, owner_user_id: int) -> Dict[str, Any]:
        await self.rebuild_index(owner_user_id=int(owner_user_id))
        items = await brain.list_memory_index_items(owner_user_id=int(owner_user_id), limit=512)
        counts: Dict[str, int] = {}
        for item in items:
            kind = str(item.get("source_kind") or "unknown")
            counts[kind] = counts.get(kind, 0) + 1
        capability = await self._search_capability_status()
        recent = [
            self._format_index_result(item, score=0.0, score_explanation="recent")
            for item in items[:12]
        ]
        days = await self.list_days(owner_user_id=int(owner_user_id), limit=30)
        return {
            "counts": counts,
            "capability": capability,
            "recent_captures": recent[:8],
            "days": days[:10],
        }

    async def list_days(self, *, owner_user_id: int, limit: int = 60) -> List[Dict[str, Any]]:
        await self.ensure_workspace(owner_user_id=int(owner_user_id))
        memory_dir = self.owner_memory_dir(int(owner_user_id))
        if not os.path.isdir(memory_dir):
            return []
        entries: List[Dict[str, Any]] = []
        for name in sorted(os.listdir(memory_dir), reverse=True):
            if not name.endswith(".md") or not re.fullmatch(r"\d{4}-\d{2}-\d{2}\.md", name):
                continue
            date_key = name[:-3]
            day_path = os.path.join(memory_dir, name)
            parsed = self._parse_daily_entries(self._read_text(day_path))
            entries.append(
                {
                    "date": date_key,
                    "path": day_path,
                    "entry_count": len(parsed),
                    "latest_summary": str(parsed[0].get("summary") or "") if parsed else "",
                }
            )
            if len(entries) >= max(1, int(limit)):
                break
        return entries

    async def get_day(self, *, owner_user_id: int, day: str) -> Dict[str, Any]:
        await self.ensure_workspace(owner_user_id=int(owner_user_id))
        clean_day = str(day or "").strip()
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", clean_day):
            raise ValueError("Invalid day")
        path = self._day_log_path(int(owner_user_id), clean_day)
        if not os.path.exists(path):
            return {"date": clean_day, "path": path, "entries": [], "markdown": ""}
        markdown = self._read_text(path)
        return {
            "date": clean_day,
            "path": path,
            "entries": self._parse_daily_entries(markdown),
            "markdown": markdown,
        }

    async def get_docs(self, *, owner_user_id: int) -> Dict[str, Any]:
        await self.ensure_workspace(owner_user_id=int(owner_user_id))
        memory_path = self.owner_memory_doc_path(int(owner_user_id))
        summary_path = self.owner_memory_summary_path(int(owner_user_id))
        archive_path = self.owner_memory_archive_path(int(owner_user_id))
        memory_markdown = self._read_text(memory_path)
        summary_markdown = self._read_text(summary_path)
        # Batch 5, task 2: the archive is materialised by ensure_workspace on
        # the first rebuild after the upgrade; until then (or if it is ever
        # missing) it is an empty document, not an error.
        archive_markdown = self._read_text(archive_path) if os.path.exists(archive_path) else ""
        memory_frontmatter, memory_content = parse_frontmatter(memory_markdown)
        summary_frontmatter, summary_content = parse_frontmatter(summary_markdown)
        archive_frontmatter, archive_content = parse_frontmatter(archive_markdown)
        summary_text = _normalize_text(summary_frontmatter.get("memory_summary_text") or summary_content)
        return {
            "memory": {
                "path": memory_path,
                "frontmatter": memory_frontmatter,
                "content": memory_content,
                "sections": self._parse_top_level_sections(memory_content),
            },
            "memory_summary": {
                "path": summary_path,
                "frontmatter": summary_frontmatter,
                "content": summary_text or summary_content,
                "sections": {"Memory Summary": summary_text or summary_content},
            },
            "memory_archive": {
                "path": archive_path,
                "frontmatter": archive_frontmatter,
                "content": archive_content,
                "sections": self._parse_top_level_sections(archive_content) if archive_content.strip() else {},
            },
        }

    async def search(
        self,
        *,
        owner_user_id: int,
        query: str,
        limit: Optional[int] = None,
        preferred_related_refs: Optional[List[str]] = None,
        carry_forward_hint: Optional[str] = None,
        prefer_active_threads: bool = False,
    ) -> Dict[str, Any]:
        # Refresh the source index in the BACKGROUND, debounced — a full
        # rebuild is ~24s for ~130 items and used to run inline here, which
        # blew recall's 5s timeout on every search even after embeddings were
        # fixed. The search scores against the current (eventually-fresh)
        # index; writes (capture_event) rebuild synchronously so new memories
        # are still indexed promptly.
        self._schedule_index_rebuild(int(owner_user_id))
        clean_query = _normalize_text(query)
        lexical_query = re.sub(r"[^\w\s-]", " ", clean_query)
        lexical_query = " ".join(token for token in lexical_query.split() if token)
        search_limit = max(1, int(limit or getattr(config, "MEMORY_SEARCH_TOP_K", 8) or 8))
        preferred_ref_set = self._normalized_ref_set(preferred_related_refs or [])
        carry_forward_tokens = self._carry_forward_tokens(carry_forward_hint)
        lexical = await brain.search_memory_index_fts(
            owner_user_id=int(owner_user_id),
            query=lexical_query or clean_query,
            limit=max(search_limit * 2, 8),
        )
        query_vector: List[float] = []
        mode = "lexical_only"
        try:
            query_vector = await self._embed_text(clean_query)
        except Exception:
            logger.debug("Failed to embed memory query", exc_info=True)
            query_vector = []
        if query_vector:
            mode = "hybrid"
            # Drain any missing embeddings OUT OF BAND. This used to be an
            # inline await over every un-embedded item (~0.4s each, blocking
            # the loop) — it froze the bot and blew recall's 5s timeout every
            # turn. Now it's a background task; the search scores against
            # whatever vectors are already cached.
            self._schedule_backfill(int(owner_user_id))
        embeddings = {
            str(item.get("item_id") or ""): item
            for item in (await brain.list_memory_index_embeddings(owner_user_id=int(owner_user_id), limit=4096))
        }
        combined: Dict[str, Dict[str, Any]] = {}
        best_bm25 = max((abs(float(row.get("lexical_score") or 0.0)) for row in lexical), default=0.0)
        for row in lexical:
            item_id = str(row.get("item_id") or "")
            lexical_score = self._normalize_fts_score(float(row.get("lexical_score") or 0.0), best_bm25)
            combined[item_id] = {
                "item": row,
                "lexical_score": lexical_score,
                "semantic_score": 0.0,
                "score": lexical_score,
            }
        if query_vector:
            # Batch 3 (1.3): the cosine pass runs off the event loop.
            semantic_scores = await asyncio.to_thread(_score_embeddings, query_vector, embeddings)
            # 2026-09-30: the owner's index items in ONE query. The per-embedding
            # get_memory_index_item loop opened a connection per item — ~1,100
            # round trips once Batch 5 indexed her memory lines, ~7 s a search,
            # so recall's 5 s budget timed out on every turn from 09-29.
            indexed_items = {
                str(row.get("item_id") or ""): row
                for row in await brain.list_memory_index_items(
                    owner_user_id=int(owner_user_id), limit=max(4096, len(embeddings)),
                )
            }
            for item_id in embeddings:
                semantic_score = float(semantic_scores.get(item_id) or 0.0)
                item = combined.get(item_id)
                if item is None:
                    indexed = indexed_items.get(item_id)
                    if not indexed:
                        continue
                    item = {"item": indexed, "lexical_score": 0.0, "semantic_score": 0.0, "score": 0.0}
                    combined[item_id] = item
                item["semantic_score"] = semantic_score
        for item in combined.values():
            continuity_ref_bonus = self._continuity_ref_bonus(
                item.get("item") or {},
                preferred_related_refs=preferred_ref_set,
            )
            carry_forward_bonus = self._carry_forward_bonus(
                item.get("item") or {},
                carry_forward_tokens=carry_forward_tokens,
            )
            current_lane_bonus = continuity_ref_bonus + carry_forward_bonus
            recency_bonus = self._recency_bonus(float((item.get("item") or {}).get("updated_at") or 0.0))
            salience_bonus = self._salience_bonus(str((item.get("item") or {}).get("salience") or "low"))
            active_followthrough_bonus = 0.0
            if current_lane_bonus > 0:
                active_followthrough_bonus = self._active_followthrough_priority(
                    item.get("item") or {},
                    prefer_active_threads=prefer_active_threads,
                )
            lexical_score = float(item.get("lexical_score") or 0.0)
            semantic_score = float(item.get("semantic_score") or 0.0)
            if query_vector:
                base_score = (lexical_score * 0.55) + (semantic_score * 0.35)
                explanation_parts = ["hybrid"]
            else:
                base_score = lexical_score
                explanation_parts = ["lexical"]
            item["score"] = (
                base_score
                + recency_bonus
                + salience_bonus
                + continuity_ref_bonus
                + carry_forward_bonus
                + active_followthrough_bonus
            )
            if continuity_ref_bonus > 0:
                explanation_parts.append("continuity_ref")
            if carry_forward_bonus > 0:
                explanation_parts.append("carry_forward")
            if active_followthrough_bonus > 0:
                explanation_parts.append("active_thread")
            item["score_explanation"] = "+".join(explanation_parts)
        ranked = sorted(combined.values(), key=lambda item: float(item.get("score") or 0.0), reverse=True)
        results = [
            self._format_index_result(
                item["item"],
                score=float(item.get("score") or 0.0),
                score_explanation=str(item.get("score_explanation") or ("hybrid" if query_vector else "lexical")),
                semantic_score=float(item.get("semantic_score") or 0.0) if query_vector else None,
            )
            for item in ranked[:search_limit]
            if str((item.get("item") or {}).get("content") or "").strip()
        ]
        return {
            "query": clean_query,
            "mode": mode,
            "results": results,
            "count": len(results),
            "capability": await self._search_capability_status(),
        }

    async def build_message_context(
        self,
        *,
        owner_user_id: int,
        query: str,
        history: Optional[List[Dict[str, Any]]] = None,
        reminder_context_note: Optional[str] = None,
        recent_continuity_context_note: Optional[str] = None,
        recent_agentic_context_note: Optional[str] = None,
    ) -> Optional[str]:
        plan = self.build_retrieval_plan(
            query=query,
            history=history,
            reminder_context_note=reminder_context_note,
            recent_continuity_context_note=recent_continuity_context_note,
            recent_agentic_context_note=recent_agentic_context_note,
        )
        if not plan.should_retrieve:
            return None
        context_limit = int(getattr(config, "MEMORY_SEARCH_MAX_CONTEXT_ITEMS", 4) or 4)
        results = await self.search(
            owner_user_id=int(owner_user_id),
            query=plan.search_query or _normalize_text(query),
            limit=context_limit,
            preferred_related_refs=plan.preferred_related_refs,
            carry_forward_hint=plan.carry_forward_hint,
            prefer_active_threads=plan.prefer_active_threads,
        )
        items = list(results.get("results") or [])
        supplemental_items: List[Dict[str, Any]] = []
        if plan.prefer_recent_results or plan.prefer_active_threads or not items:
            supplemental_items = await self._load_recent_context_results(
                owner_user_id=int(owner_user_id),
                prefer_active_threads=plan.prefer_active_threads,
                limit=context_limit,
                preferred_related_refs=plan.preferred_related_refs,
                carry_forward_hint=plan.carry_forward_hint,
            )
        if plan.prefer_recent_results or plan.prefer_active_threads:
            items = self._merge_context_results(supplemental_items, items, limit=context_limit)
        else:
            items = self._merge_context_results(items, supplemental_items, limit=context_limit)
        if not items:
            return None
        lines = ["Relevant Memory:"]
        for item in items[:context_limit]:
            summary = _compact_text(item.get("snippet") or item.get("title") or item.get("content") or "", limit=220)
            source = _compact_text(item.get("source_kind") or "memory", limit=40)
            lines.append(f"- [{source}] {summary}")
        return "\n".join(lines)

    async def get_preview(self, *, owner_user_id: int, limit: int = 4) -> Dict[str, Any]:
        overview = await self.build_overview(owner_user_id=int(owner_user_id))
        recent = list(overview.get("recent_captures") or [])[: max(1, int(limit))]
        return {
            "capability": overview.get("capability") or {},
            "recent_captures": recent,
            "counts": overview.get("counts") or {},
        }

    async def _merge_curated_memory(
        self,
        *,
        owner_user_id: int,
        summary: str,
        why_it_matters: str,
        section: str,
    ) -> None:
        frontmatter: Dict[str, Any] = {}
        content = ""
        bundle = await identity_service.ensure_user_identity_bundle(
            user_id=int(owner_user_id),
            platform="owner",
            identity_owner_id=int(owner_user_id),
        )
        if bundle is not None:
            doc = bundle.user_documents.get("memory")
            if doc is not None:
                frontmatter = dict(doc.frontmatter or {})
                content = doc.content or ""
        if not content:
            memory_path = self.owner_memory_doc_path(int(owner_user_id))
            if not os.path.exists(memory_path):
                return
            markdown = self._read_text(memory_path)
            frontmatter, content = parse_frontmatter(markdown)
        sections = self._parse_top_level_sections(content)
        existing_section = str(sections.get(section) or "")
        normalized_existing = existing_section.lower()
        if _normalize_text(summary).lower() in normalized_existing:
            return
        bullet = f"- {summary}"
        if why_it_matters and why_it_matters.lower() not in normalized_existing:
            bullet += f" ({why_it_matters})"
        new_content = self._append_to_section(content=content, section=section, bullet=bullet)
        rendered_markdown = self._render_identity_markdown(frontmatter=frontmatter, content=new_content)
        await identity_service.update_raw_document(
            user_id=int(owner_user_id),
            platform="owner",
            doc_key="memory",
            markdown=rendered_markdown,
            actor_type="system",
            actor_id="memory_workspace",
            reason="memory_capture",
            identity_owner_id=int(owner_user_id),
        )

    async def _collect_index_items(self, *, owner_user_id: int) -> List[Dict[str, Any]]:
        docs = await self.get_docs(owner_user_id=int(owner_user_id))
        days = await self.list_days(owner_user_id=int(owner_user_id), limit=180)
        items: List[Dict[str, Any]] = []
        for day in days:
            daily = await self.get_day(owner_user_id=int(owner_user_id), day=str(day.get("date") or ""))
            for entry in daily.get("entries") or []:
                item_id = f"mdl_{str(entry.get('entry_id') or _sha(str(entry))[:12])}"
                items.append(
                    self._index_item(
                        item_id=item_id,
                        owner_user_id=int(owner_user_id),
                        source_kind="daily_log_entry",
                        source_id=f"{str(day.get('date') or '')}:{str(entry.get('entry_id') or '')}",
                        title=str(entry.get("summary") or "Daily Memory Entry"),
                        content=f"{entry.get('summary') or ''}\n{entry.get('why_it_matters') or ''}",
                        salience=str(entry.get("salience") or "low"),
                        tags=list(entry.get("tags") or []),
                        related_refs=list(entry.get("related_refs") or []),
                        created_at=float(entry.get("created_at") or 0.0) or time.time(),
                        updated_at=float(entry.get("created_at") or 0.0) or time.time(),
                        metadata={"date": str(day.get("date") or "")},
                    )
                )
        memory_path = self.owner_memory_doc_path(int(owner_user_id))
        memory_doc_mtime = os.path.getmtime(memory_path) if os.path.exists(memory_path) else time.time()
        for section, text in (docs.get("memory", {}).get("sections") or {}).items():
            if _normalize_text(text):
                items.append(
                    self._index_item(
                        item_id=f"mcs_{_sha(section)[:12]}",
                        owner_user_id=int(owner_user_id),
                        source_kind="curated_memory_section",
                        source_id=section,
                        title=section,
                        content=text,
                        salience="medium",
                        tags=["curated-memory"],
                        related_refs=[],
                        created_at=memory_doc_mtime,
                        updated_at=memory_doc_mtime,
                        metadata={},
                    )
                )
        # Batch 5, task 2: one `mcb_` item per bullet, from MEMORY.md and then
        # MEMORY_ARCHIVE.md. The id is the text's hash, so the same line in
        # both documents is one item and the file's copy wins.
        archive_path = self.owner_memory_archive_path(int(owner_user_id))
        seen_bullet_ids: set[str] = set()
        for doc_key, doc_path in (("memory", memory_path), ("memory_archive", archive_path)):
            doc_content = str((docs.get(doc_key) or {}).get("content") or "")
            if not doc_content.strip():
                continue
            doc_mtime = os.path.getmtime(doc_path) if os.path.exists(doc_path) else time.time()
            for item in _bullet_index_items(
                doc_content, doc_key=doc_key, owner_user_id=int(owner_user_id), doc_mtime=doc_mtime,
            ):
                if item["item_id"] in seen_bullet_ids:
                    continue
                seen_bullet_ids.add(item["item_id"])
                items.append(item)
        summary_path = self.owner_memory_summary_path(int(owner_user_id))
        summary_doc_mtime = os.path.getmtime(summary_path) if os.path.exists(summary_path) else time.time()
        summary_sections = (docs.get("memory_summary", {}).get("sections") or {}) or {"Memory Summary": str((docs.get("memory_summary", {}) or {}).get("content") or "")}
        for section, text in summary_sections.items():
            if _normalize_text(text):
                items.append(
                    self._index_item(
                        item_id=f"mss_{_sha(section)[:12]}",
                        owner_user_id=int(owner_user_id),
                        source_kind="memory_summary_section",
                        source_id=section,
                        title=section,
                        content=text,
                        salience="medium",
                        tags=["memory-summary"],
                        related_refs=[],
                        created_at=summary_doc_mtime,
                        updated_at=summary_doc_mtime,
                        metadata={},
                    )
                )
        for fact in await brain.get_user_memories(user_id=int(owner_user_id), memory_type=None, min_importance=0.1):
            if str(fact.get("status") or "active") != "active":
                # Wave 1a hinge: superseded originals leave the ambient plane
                # entirely — their gist successor is the ambient face; the
                # original stays reachable only through introspect exact mode.
                continue
            items.append(
                self._index_item(
                    item_id=f"lfm_{int(fact.get('id') or 0)}",
                    owner_user_id=int(owner_user_id),
                    source_kind="legacy_fact",
                    source_id=str(fact.get("id") or ""),
                    title=str(fact.get("memory_type") or "Memory"),
                    content=str(fact.get("content") or ""),
                    salience="high" if float(fact.get("importance_score") or 0.0) >= 0.75 else "medium",
                    tags=list(fact.get("tags") or []),
                    related_refs=[],
                    created_at=float(fact.get("created_at") or time.time()),
                    updated_at=float(fact.get("last_accessed") or fact.get("created_at") or time.time()),
                    metadata={"importance_score": float(fact.get("importance_score") or 0.0)},
                )
            )
        items.sort(key=lambda item: float(item.get("updated_at") or 0.0), reverse=True)
        return items

    def _index_item(
        self,
        *,
        item_id: str,
        owner_user_id: int,
        source_kind: str,
        source_id: str,
        title: str,
        content: str,
        salience: str,
        tags: List[str],
        related_refs: List[str],
        created_at: float,
        updated_at: float,
        metadata: Dict[str, Any],
    ) -> Dict[str, Any]:
        return _build_index_item(
            item_id=item_id,
            owner_user_id=owner_user_id,
            source_kind=source_kind,
            source_id=source_id,
            title=title,
            content=content,
            salience=salience,
            tags=tags,
            related_refs=related_refs,
            created_at=created_at,
            updated_at=updated_at,
            metadata=metadata,
        )

    def _schedule_index_rebuild(self, owner_user_id: int) -> None:
        """Refresh the memory index in the BACKGROUND (debounced by TTL). The
        per-turn search must never block on the full source re-index (~24s)."""
        existing = self._index_rebuild_tasks.get(owner_user_id)
        if existing is not None and not existing.done():
            return
        last = self._last_index_rebuild.get(owner_user_id, 0.0)
        if (time.time() - last) < self._index_rebuild_ttl_seconds:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        task = loop.create_task(self._run_index_rebuild(owner_user_id))
        self._index_rebuild_tasks[owner_user_id] = task
        _forget_when_done(self._index_rebuild_tasks, owner_user_id, task)

    async def _run_index_rebuild(self, owner_user_id: int) -> None:
        try:
            await self.rebuild_index(owner_user_id=owner_user_id)
            self._last_index_rebuild[owner_user_id] = time.time()
        except Exception:
            logger.debug("memory index rebuild task failed", exc_info=True)

    def _schedule_backfill(self, owner_user_id: int) -> None:
        """Kick off a background embedding backfill for this owner (no-op if
        one is already running). NEVER blocks the caller — the per-turn search
        must not wait on the backlog drain."""
        existing = self._backfill_tasks.get(owner_user_id)
        if existing is not None and not existing.done():
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        task = loop.create_task(self._run_backfill(owner_user_id))
        self._backfill_tasks[owner_user_id] = task
        _forget_when_done(self._backfill_tasks, owner_user_id, task)

    async def _run_backfill(self, owner_user_id: int) -> None:
        try:
            await self.backfill_owner_embeddings(owner_user_id=owner_user_id)
        except Exception:
            logger.debug("memory embedding backfill task failed", exc_info=True)

    async def backfill_owner_embeddings(self, *, owner_user_id: int) -> None:
        items = await brain.list_memory_index_items(owner_user_id=int(owner_user_id), limit=4096)
        embeddings = {
            str(item.get("item_id") or ""): item
            for item in await brain.list_memory_index_embeddings(owner_user_id=int(owner_user_id), limit=4096)
        }
        for item in items:
            item_id = str(item.get("item_id") or "")
            if not item_id:
                continue
            content_hash = str(item.get("content_hash") or "")
            existing = embeddings.get(item_id)
            if existing and str(existing.get("content_hash") or "") == content_hash:
                continue
            vector = await self._embed_text(str(item.get("content") or ""))
            if not vector:
                continue
            provider = "local"
            model = str(getattr(config, "MEMORY_LOCAL_EMBEDDINGS_MODEL", "all-MiniLM-L6-v2") or "all-MiniLM-L6-v2")
            await brain.upsert_memory_index_embedding(
                item_id=item_id,
                owner_user_id=int(owner_user_id),
                provider=provider,
                model=model,
                vector=vector,
                content_hash=content_hash,
            )

    async def _embed_text(self, text: str) -> List[float]:
        clean_text = _normalize_text(text)
        if not clean_text:
            return []
        if getattr(config, "MEMORY_LOCAL_EMBEDDINGS_ENABLED", True):
            local = await self._embed_text_local(clean_text)
            if local:
                return local
        return await self._embed_text_remote(clean_text)

    async def _ensure_local_model(self, model_name: str) -> Any:
        """Load the sentence-transformers model once, OFF the event loop.

        The cold import (torch + transformers) is ~9s and the model load ~2s.
        Done synchronously on the loop this froze the whole bot and blew
        recall's 5s timeout every turn (2026-06-13 'PaLLi feels generic'
        incident). asyncio.to_thread keeps the loop responsive; the lock keeps
        concurrent turns from each kicking off a cold load."""
        if self._local_model is not None and self._local_model_name == model_name:
            return self._local_model
        async with self._local_model_lock:
            if self._local_model is not None and self._local_model_name == model_name:
                return self._local_model
            try:
                def _load_off_loop() -> Any:
                    # DEFECT_RECORD §12 (2026-08-28): the IMPORT is the cold
                    # part (torch, ~9 s) and it ran here on the loop before
                    # the load was handed to a thread — at boot that starved
                    # the gateway and his 12:08 message was lost. Import and
                    # construct together, on the worker thread.
                    from sentence_transformers import SentenceTransformer

                    return SentenceTransformer(model_name)

                model = await asyncio.to_thread(_load_off_loop)
            except Exception:
                logger.debug("Local sentence-transformers model unavailable", exc_info=True)
                self._local_model = None
                self._local_model_name = ""
                return None
            self._local_model = model
            self._local_model_name = model_name
            return model

    async def prewarm_local_embeddings(self) -> bool:
        """Load the local embedding model at startup (off the loop) so the
        first per-turn semantic recall doesn't pay the cold-import cost.
        Returns True when the model is ready to embed."""
        if not bool(getattr(config, "MEMORY_LOCAL_EMBEDDINGS_ENABLED", True)):
            return False
        model_name = str(getattr(config, "MEMORY_LOCAL_EMBEDDINGS_MODEL", "all-MiniLM-L6-v2") or "all-MiniLM-L6-v2").strip()
        if not model_name:
            return False
        return (await self._ensure_local_model(model_name)) is not None

    async def _embed_text_local(self, text: str) -> List[float]:
        model_name = str(getattr(config, "MEMORY_LOCAL_EMBEDDINGS_MODEL", "all-MiniLM-L6-v2") or "all-MiniLM-L6-v2").strip()
        if not model_name:
            return []
        model = await self._ensure_local_model(model_name)
        if model is None:
            return []
        try:
            # encode() is blocking CPU work — keep it off the event loop so a
            # single embed can never freeze every other coroutine.
            vector = await asyncio.to_thread(model.encode, text, normalize_embeddings=True)
            return [float(value) for value in list(vector)]
        except Exception:
            logger.debug("Local embedding generation failed", exc_info=True)
            return []

    async def _embed_text_remote(self, text: str) -> List[float]:
        api_key = str(getattr(config, "OPENAI_API_KEY", "") or "").strip()
        if not api_key:
            return []
        model_name = str(getattr(config, "MEMORY_REMOTE_EMBEDDINGS_MODEL", "text-embedding-3-small") or "text-embedding-3-small").strip()
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        # The fallback posts memory text to a provider: sealed like every
        # other request — known literals out, secret shapes masked (final
        # fix wave, 2026-09-26).
        payload = {"model": model_name, "input": for_provider(text)}
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post("https://api.openai.com/v1/embeddings", headers=headers, json=payload, timeout=30) as response:
                    if response.status >= 400:
                        logger.debug("Remote embedding request failed with status %s", response.status)
                        return []
                    data = await response.json()
                    embedding = (((data or {}).get("data") or [{}])[0] or {}).get("embedding") or []
                    return [float(value) for value in embedding if isinstance(value, (int, float))]
        except Exception:
            logger.debug("Remote embedding generation failed", exc_info=True)
            return []

    async def _search_capability_status(self) -> Dict[str, Any]:
        local_model_enabled = bool(getattr(config, "MEMORY_LOCAL_EMBEDDINGS_ENABLED", True))
        remote_enabled = bool(str(getattr(config, "OPENAI_API_KEY", "") or "").strip())
        local_ready = False
        if local_model_enabled:
            # §12: a readiness probe must not pay the cold import on the loop —
            # find_spec answers "installed?" without executing the package.
            import importlib.util

            local_ready = importlib.util.find_spec("sentence_transformers") is not None
        if local_ready or remote_enabled:
            return {
                "mode": "hybrid",
                "local_embeddings_enabled": local_model_enabled,
                "local_embeddings_ready": local_ready,
                "remote_fallback_enabled": remote_enabled,
            }
        return {
            "mode": "lexical_only",
            "local_embeddings_enabled": local_model_enabled,
            "local_embeddings_ready": False,
            "remote_fallback_enabled": False,
        }

    def _render_day_shell(self, day_key: str) -> str:
        return (
            f"# Daily Memory Log for {day_key}\n\n"
            "Append-only meaningful memory capture. This is not a raw transcript.\n"
        )

    def _day_log_path(self, owner_user_id: int, day_key: str) -> str:
        return os.path.join(self.owner_memory_dir(int(owner_user_id)), f"{day_key}.md")

    def _read_text(self, path: str) -> str:
        with open(path, "r", encoding="utf-8") as handle:
            return handle.read()

    def _write_text(self, path: str, content: str) -> None:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(content)

    def _parse_daily_entries(self, markdown: str) -> List[Dict[str, Any]]:
        entries: List[Dict[str, Any]] = []
        current: Dict[str, Any] | None = None
        for raw_line in (markdown or "").splitlines():
            line = raw_line.rstrip()
            if line.startswith("## "):
                if current and current.get("summary"):
                    entries.append(current)
                timestamp_text = line[3:].strip()
                created_at = time.time()
                try:
                    created_at = datetime.strptime(timestamp_text[:19], "%Y-%m-%d %H:%M:%S").timestamp()
                except Exception:
                    pass
                current = {"timestamp": timestamp_text, "tags": [], "related_refs": [], "created_at": created_at}
                continue
            if not current or not line.startswith("- "):
                continue
            label, _, value = line[2:].partition(":")
            key = label.strip().lower().replace(" ", "_")
            text = value.strip()
            if key == "entry_id":
                current["entry_id"] = text
            elif key == "source":
                current["source"] = text
            elif key == "salience":
                current["salience"] = text
            elif key == "tags":
                current["tags"] = [item.strip() for item in text.split(",") if item.strip() and item.strip().lower() != "none"]
            elif key == "related":
                current["related_refs"] = [item.strip() for item in text.split(",") if item.strip() and item.strip().lower() != "none"]
            elif key == "summary":
                current["summary"] = text
            elif key == "why_it_matters":
                current["why_it_matters"] = text
        if current and current.get("summary"):
            entries.append(current)
        return list(reversed(entries))

    def _parse_top_level_sections(self, content: str) -> Dict[str, str]:
        text = str(content or "")
        matches = list(_TOP_LEVEL_SECTION_RE.finditer(text))
        if not matches:
            return {"Memory": _normalize_text(text)}
        sections: Dict[str, str] = {}
        for idx, match in enumerate(matches):
            start = match.end()
            end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
            name = match.group(1).strip()
            body = text[start:end].strip()
            sections[name] = body
        return sections

    def _append_to_section(self, *, content: str, section: str, bullet: str) -> str:
        sections = self._parse_top_level_sections(content)
        if section in sections:
            existing = sections[section].rstrip()
            sections[section] = f"{existing}\n{bullet}".strip()
        else:
            sections[section] = bullet
        lines = ["# Memory", "", "This file is curated memory, not a transcript.", "", "It should hold durable context that PaLLi may need to retrieve later without rereading raw history.", ""]
        for name in ("Durable Context", "Active Threads", "Important Decisions", "Useful References"):
            body = sections.get(name) or ("- No curated entries yet." if not sections.get(name) else "")
            lines.append(f"## {name}")
            lines.append("")
            lines.append(body.strip() or "- No curated entries yet.")
            lines.append("")
        return "\n".join(lines).rstrip() + "\n"

    def _render_identity_markdown(self, *, frontmatter: Dict[str, Any], content: str) -> str:
        return dump_frontmatter(dict(frontmatter), content.rstrip())

    def _select_curated_section(self, *, source_kind: str, tags: Iterable[str], salience: str) -> str:
        tag_set = {str(tag).strip().lower() for tag in tags}
        if "decision" in tag_set or source_kind in {"standing_order_run", "reflection_run"}:
            return "Important Decisions"
        if source_kind in {"workflow_memory", "followthrough_item"}:
            return "Active Threads"
        if "identity" in tag_set or "preference" in tag_set or "goal" in tag_set:
            return "Durable Context"
        if str(salience or "").strip().lower() == "high":
            return "Durable Context"
        return "Useful References"

    def _normalize_fts_score(self, value: float, best: float) -> float:
        # FTS5 bm25() is MORE NEGATIVE for a stronger match, and its size swings
        # with corpus statistics. Score each candidate against the strongest one in
        # the same batch, as ai/inner_memory_index.py does: the best match is 1.0
        # and the order follows match strength. (Until 2026-10-08 this was
        # 1/(1+|bm25|), which ranked the best keyword match LAST of the candidates.)
        best = float(best or 0.0)
        if best <= 0.0:
            return 1.0
        return abs(float(value or 0.0)) / best

    def _salience_bonus(self, salience: str) -> float:
        normalized = str(salience or "").strip().lower()
        if normalized == "high":
            return 0.08
        if normalized == "medium":
            return 0.04
        return 0.0

    def _recency_bonus(self, updated_at: float) -> float:
        age_hours = max(0.0, (time.time() - float(updated_at or 0.0)) / 3600.0)
        if age_hours <= 24:
            return 0.06
        if age_hours <= 72:
            return 0.03
        return 0.0

    def _format_index_result(
        self,
        item: Dict[str, Any],
        *,
        score: float,
        score_explanation: str,
        semantic_score: Optional[float] = None,
    ) -> Dict[str, Any]:
        return {
            "item_id": str(item.get("item_id") or ""),
            "source_kind": str(item.get("source_kind") or ""),
            "source_id": str(item.get("source_id") or ""),
            "title": str(item.get("title") or ""),
            "snippet": _compact_text(item.get("snippet") or item.get("content") or "", limit=220),
            "content": str(item.get("content") or ""),
            "salience": str(item.get("salience") or "low"),
            "tags": list(item.get("tags") or []),
            "related_refs": list(item.get("related_refs") or []),
            "updated_at": float(item.get("updated_at") or 0.0) or None,
            "score": round(float(score or 0.0), 4),
            # Batch 3 (1.3): the item's own cosine similarity, rounded like
            # ``score``; None when no query embedding existed (lexical-only) —
            # "no similarity known" is a different fact from "similarity 0".
            "semantic_score": None if semantic_score is None else round(float(semantic_score), 4),
            "score_explanation": score_explanation,
            # Batch 5, task 2: the item's metadata rides along (a memory_bullet
            # carries {section, doc, stamp}); the column is JSON text, decoded
            # on read — a row that arrives undecoded is decoded here.
            "metadata": _metadata_dict(item.get("metadata")),
        }

    def _shape_retrieval_query(
        self,
        *,
        query: str,
        carry_forward_hint: Optional[str],
        reminder_context_note: Optional[str],
        recent_continuity_context_note: Optional[str],
        recent_agentic_context_note: Optional[str],
        prefer_recent_results: bool,
        prefer_active_threads: bool,
    ) -> str:
        parts = [_normalize_text(query)]
        if prefer_active_threads:
            parts.append("open followthrough active threads unfinished workflow carry forward")
        if prefer_recent_results:
            parts.append("recent updates latest finished work recent continuity changes")
        if carry_forward_hint and (prefer_active_threads or _VAGUE_CONTINUITY_RE.search(query)):
            parts.append(f"current carry forward {self._flatten_context_hint(carry_forward_hint, limit=180)}")
        continuity_hint = self._flatten_context_hint(recent_continuity_context_note, limit=220)
        if continuity_hint and (prefer_recent_results or prefer_active_threads or _VAGUE_CONTINUITY_RE.search(query)):
            parts.append(f"recent continuity {continuity_hint}")
        if _VAGUE_CONTINUITY_RE.search(query):
            reminder_hint = self._flatten_context_hint(reminder_context_note, limit=180)
            if reminder_hint:
                parts.append(f"reminder followup {reminder_hint}")
            agentic_hint = self._flatten_context_hint(recent_agentic_context_note, limit=220)
            if agentic_hint:
                parts.append(f"recent agentic context {agentic_hint}")
        return _compact_text(" ".join(part for part in parts if part), limit=480)

    def _flatten_context_hint(self, value: Any, *, limit: int = 220) -> str:
        text = str(value or "").strip()
        if not text:
            return ""
        text = re.sub(r"`([^`]*)`", r"\1", text)
        text = re.sub(r"[*>#]", " ", text)
        text = re.sub(r"\s+", " ", text)
        return _compact_text(text, limit=limit)

    async def _load_recent_context_results(
        self,
        *,
        owner_user_id: int,
        prefer_active_threads: bool,
        limit: int,
        preferred_related_refs: Optional[List[str]] = None,
        carry_forward_hint: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        try:
            indexed_items = list(
                await brain.list_memory_index_items(
                    owner_user_id=int(owner_user_id),
                    limit=max(limit * 4, 12),
                )
            )
        except Exception:
            logger.debug("Failed to load recent memory context items", exc_info=True)
            return []

        preferred_ref_set = self._normalized_ref_set(preferred_related_refs or [])
        carry_forward_tokens = self._carry_forward_tokens(carry_forward_hint)

        def recent_rank(item: Dict[str, Any]) -> tuple[float, float, float, float]:
            continuity_ref_bonus = self._continuity_ref_bonus(
                item,
                preferred_related_refs=preferred_ref_set,
            )
            carry_forward_bonus = self._carry_forward_bonus(
                item,
                carry_forward_tokens=carry_forward_tokens,
            )
            current_lane_bonus = continuity_ref_bonus + carry_forward_bonus
            recency_bonus = self._recency_bonus(float(item.get("updated_at") or 0.0))
            salience_bonus = self._salience_bonus(str(item.get("salience") or "low"))
            active_followthrough_priority = 0.0
            if current_lane_bonus > 0:
                active_followthrough_priority = self._active_followthrough_priority(
                    item,
                    prefer_active_threads=prefer_active_threads,
                )
            return (
                current_lane_bonus,
                active_followthrough_priority,
                recency_bonus + salience_bonus,
                float(item.get("updated_at") or 0.0),
            )

        indexed_items.sort(key=recent_rank, reverse=True)
        results: List[Dict[str, Any]] = []
        for item in indexed_items:
            source_kind = str(item.get("source_kind") or "").strip().lower()
            tags = {str(tag).strip().lower() for tag in item.get("tags") or [] if str(tag).strip()}
            if prefer_active_threads:
                if source_kind == "followthrough_item" and tags.isdisjoint({"open", "snoozed", "surfaced", "converted_to_reminder"}):
                    continue
                if source_kind not in {"followthrough_item", "workflow_memory"}:
                    continue
            if not _normalize_text(item.get("content") or item.get("snippet") or item.get("title") or ""):
                continue
            results.append(
                self._format_index_result(
                    item,
                    score=0.0,
                    score_explanation="recent_context",
                )
            )
            if len(results) >= max(limit * 2, limit):
                break
        if results or not prefer_active_threads:
            return results
        return [
            self._format_index_result(item, score=0.0, score_explanation="recent_context")
            for item in indexed_items[: max(limit * 2, limit)]
            if _normalize_text(item.get("content") or item.get("snippet") or item.get("title") or "")
        ]

    def _merge_context_results(
        self,
        primary: List[Dict[str, Any]],
        secondary: List[Dict[str, Any]],
        *,
        limit: int,
    ) -> List[Dict[str, Any]]:
        merged: List[Dict[str, Any]] = []
        seen: set[str] = set()
        for group in (primary, secondary):
            for item in group:
                item_id = str(item.get("item_id") or "").strip()
                dedupe_key = item_id or _sha(
                    "|".join(
                        [
                            str(item.get("source_kind") or ""),
                            str(item.get("source_id") or ""),
                            str(item.get("snippet") or item.get("content") or ""),
                        ]
                    )
                )
                if dedupe_key in seen:
                    continue
                seen.add(dedupe_key)
                merged.append(item)
                if len(merged) >= max(1, int(limit)):
                    return merged
        return merged

    def _build_retrieval_bias(
        self,
        *,
        history: Optional[List[Dict[str, Any]]],
    ) -> MemoryRetrievalBias:
        recent_history = list(history or [])[-8:]
        if not recent_history:
            return MemoryRetrievalBias()

        preferred_related_refs: List[str] = []
        seen_refs: set[str] = set()
        assistant_carry_forward: List[str] = []
        assistant_seen: set[str] = set()
        fallback_carry_forward: List[str] = []
        fallback_seen: set[str] = set()

        for item in reversed(recent_history):
            if not isinstance(item, dict):
                continue
            metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
            related_refs = self._history_item_related_refs(item=item, metadata=metadata)
            for ref in related_refs:
                normalized_ref = ref.lower()
                if normalized_ref in seen_refs:
                    continue
                seen_refs.add(normalized_ref)
                preferred_related_refs.append(ref)
            carry_forward_candidates = self._history_item_carry_forward_candidates(
                item=item,
                metadata=metadata,
            )
            if not carry_forward_candidates or not related_refs:
                continue
            role = str(item.get("role") or "").strip().lower()
            if role == "assistant":
                target_list = assistant_carry_forward
                target_seen = assistant_seen
            else:
                target_list = fallback_carry_forward
                target_seen = fallback_seen
            for candidate in carry_forward_candidates:
                normalized_candidate = candidate.lower()
                if normalized_candidate in target_seen:
                    continue
                target_seen.add(normalized_candidate)
                target_list.append(candidate)
                if len(target_list) >= 4:
                    break

        carry_forward_source = assistant_carry_forward or fallback_carry_forward
        carry_forward_hint = ""
        if carry_forward_source:
            carry_forward_hint = _compact_text(" ".join(carry_forward_source[:2]), limit=260)
        return MemoryRetrievalBias(
            preferred_related_refs=preferred_related_refs[:6],
            carry_forward_hint=carry_forward_hint,
        )

    def _history_item_carry_forward_candidates(
        self,
        *,
        item: Dict[str, Any],
        metadata: Dict[str, Any],
    ) -> List[str]:
        candidates: List[str] = []
        seen: set[str] = set()

        def add_candidate(value: Any, *, min_length: int = 1) -> None:
            text = _compact_text(value, limit=180)
            if not text or len(text) < min_length:
                return
            normalized_text = text.lower()
            if normalized_text in seen:
                return
            seen.add(normalized_text)
            candidates.append(text)

        add_candidate(metadata.get("operator_waiting_for_input_prompt"))
        add_candidate(metadata.get("operator_resume_brief"))
        add_candidate(metadata.get("operator_workflow_progress_summary"))
        add_candidate(metadata.get("operator_handoff_summary"))
        add_candidate(metadata.get("last_resume_brief"))
        add_candidate(metadata.get("progress_summary"))
        add_candidate(metadata.get("latest_handoff_summary"))
        add_candidate(metadata.get("latest_remaining_work"))
        add_candidate(metadata.get("next_step_preview"))
        add_candidate(metadata.get("operator_workflow_step_title"))

        next_action = _normalize_text(
            metadata.get("operator_recommended_next_action")
            or metadata.get("recommended_next_action")
        )
        if next_action and next_action.lower() != "none":
            add_candidate(f"Likely next move: {next_action.replace('_', ' ')}")

        role = str(item.get("role") or "").strip().lower()
        if role == "assistant":
            add_candidate(item.get("content") or "", min_length=24)
        else:
            add_candidate(item.get("content") or "")
        return candidates[:4]

    def _history_item_related_refs(
        self,
        *,
        item: Dict[str, Any],
        metadata: Dict[str, Any],
    ) -> List[str]:
        refs: List[str] = []
        continuity_view = build_reminder_continuity_view(
            {
                "metadata": metadata,
                **item,
            }
        )
        continuity_refs = continuity_view.get("continuity_refs") or []
        thread_id = _normalize_text(continuity_view.get("thread_id"))
        workflow_id = _normalize_text(continuity_view.get("workflow_id"))
        followthrough_id = _normalize_text(continuity_view.get("followthrough_id"))
        lane_id = _normalize_text(continuity_view.get("lane_id"))

        seen: set[str] = set()

        def add_ref(value: Any) -> None:
            text = _normalize_text(value)
            if not text:
                return
            normalized = text.lower()
            if normalized in seen:
                return
            seen.add(normalized)
            refs.append(text)

        if thread_id:
            add_ref(f"thread:{thread_id}")
        if workflow_id:
            add_ref(f"workflow:{workflow_id}")
        if followthrough_id:
            add_ref(f"followthrough:{followthrough_id}")
        if lane_id:
            add_ref(f"lane:{lane_id}")
        for ref in continuity_refs:
            add_ref(ref)
        return refs

    def _normalized_ref_set(self, refs: Iterable[str]) -> set[str]:
        return {
            _normalize_text(ref).lower()
            for ref in refs
            if _normalize_text(ref)
        }

    def _continuity_ref_bonus(
        self,
        item: Dict[str, Any],
        *,
        preferred_related_refs: set[str],
    ) -> float:
        if not preferred_related_refs:
            return 0.0
        item_refs = self._normalized_ref_set(item.get("related_refs") or [])
        if not item_refs:
            return 0.0
        overlap = item_refs.intersection(preferred_related_refs)
        if not overlap:
            return 0.0
        return 0.18 + min(0.04, 0.02 * max(0, len(overlap) - 1))

    def _carry_forward_tokens(self, value: Optional[str]) -> List[str]:
        text = _normalize_text(value).lower()
        if not text:
            return []
        stop_words = {
            "about",
            "after",
            "again",
            "back",
            "before",
            "continue",
            "from",
            "have",
            "into",
            "last",
            "later",
            "need",
            "needs",
            "next",
            "still",
            "that",
            "them",
            "this",
            "time",
            "want",
            "with",
            "which",
        }
        tokens: List[str] = []
        seen: set[str] = set()
        for token in re.findall(r"[a-z0-9_./:-]+", text):
            clean_token = token.strip(" .,:/-")
            if len(clean_token) < 4 or clean_token in stop_words or clean_token in seen:
                continue
            seen.add(clean_token)
            tokens.append(clean_token)
            if len(tokens) >= 12:
                break
        return tokens

    def _carry_forward_bonus(
        self,
        item: Dict[str, Any],
        *,
        carry_forward_tokens: List[str],
    ) -> float:
        if not carry_forward_tokens:
            return 0.0
        text = _normalize_text(
            " ".join(
                [
                    str(item.get("title") or ""),
                    str(item.get("snippet") or ""),
                    str(item.get("content") or ""),
                ]
            )
        ).lower()
        if not text:
            return 0.0
        matches = sum(1 for token in carry_forward_tokens if token and token in text)
        if matches <= 0:
            return 0.0
        return min(0.12, 0.03 * matches)

    def _active_followthrough_priority(
        self,
        item: Dict[str, Any],
        *,
        prefer_active_threads: bool,
    ) -> float:
        if not prefer_active_threads:
            return 0.0
        source_kind = str(item.get("source_kind") or "").strip().lower()
        if source_kind != "followthrough_item":
            return 0.0
        tags = {str(tag).strip().lower() for tag in item.get("tags") or [] if str(tag).strip()}
        if tags.isdisjoint({"open", "snoozed", "surfaced", "converted_to_reminder"}):
            return 0.0
        return 1.0


memory_workspace_service = MemoryWorkspaceService()
