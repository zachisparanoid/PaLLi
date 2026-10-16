"""Reference stamps for memory bullets (Batch 5, memory that picks, 2026-09-28).

Every bullet of MEMORY.md is a search item with an id
(``core.memory_bullets.item_id_for``). When recall surfaces one for a turn,
the bullet is REFERENCED: its row in ``memory_bullet_refs`` takes the time
and counts up (``bump``; recall's turn hook calls it). The core render
(``core.identity.compiler.plan_memory_core``) ranks bullets by
``max(last_referenced_at, stamp)``, and the nightly sweep moves the
long-unreferenced ones to MEMORY_ARCHIVE.md.

The render is sync and runs on every reply, so it reads an IN-PROCESS
SNAPSHOT per owner (ruling R7): ``ensure_loaded`` fills it once per process
(recall calls it), ``refresh`` reloads it (the nightly sweep calls it when it
finishes), and ``bump`` writes the table but never the snapshot. So the
core's ranking inputs change once a day, on purpose: a bullet used today
enters the core tomorrow, and the reply prompt's stable prefix — which the
core ends — stays byte-identical across the day's turns for the provider's
cache. Until the first load the snapshot is ``{}`` and the core ranks by
stamps alone.

Shape (ruling R31): ``{item_id: {"last_referenced_at": float, "count": int}}``.
"""
from __future__ import annotations

import logging
import time
from types import MappingProxyType
from typing import Any, Dict, Iterable, Mapping, Optional

logger = logging.getLogger(__name__)

RefsSnapshot = Dict[str, Dict[str, Any]]
# What callers get: a read-only view over the process's snapshot, so nobody
# can corrupt it by mutating what snapshot() returned (fix round 1, M3).
RefsView = Mapping[str, Mapping[str, Any]]

_snapshots: Dict[int, RefsSnapshot] = {}
_EMPTY: RefsView = MappingProxyType({})


def _owner_key(owner_id: Any) -> Optional[int]:
    try:
        return int(owner_id)
    except (TypeError, ValueError):
        return None


def snapshot(owner_id: Any) -> RefsView:
    """The owner's refs as last loaded, as a read-only view; empty when never
    loaded (or when ``owner_id`` is not an id). Sync, no I/O — the reply
    path's read. It compares equal to the plain dict it mirrors."""
    key = _owner_key(owner_id)
    if key is None or key not in _snapshots:
        return _EMPTY
    return MappingProxyType(_snapshots[key])


async def load_refs(owner_id: Any) -> RefsSnapshot:
    """Read the owner's rows from the brain, in the snapshot's shape. Raises
    when the brain does; ``refresh`` is the forgiving caller."""
    from core.database import brain

    rows = await brain.load_memory_bullet_refs(owner_user_id=int(owner_id))
    refs: RefsSnapshot = {}
    for row in rows:
        item_id = str(row.get("item_id") or "")
        if not item_id:
            continue
        refs[item_id] = {
            "last_referenced_at": float(row.get("last_referenced_at") or 0.0),
            "count": int(row.get("reference_count") or 0),
        }
    return refs


async def refresh(owner_id: Any) -> RefsView:
    """Reload the owner's snapshot from the brain (the nightly sweep's call).
    When the brain fails, say so at INFO and keep what was there."""
    key = _owner_key(owner_id)
    if key is None:
        return _EMPTY
    try:
        _snapshots[key] = await load_refs(key)
    except Exception as exc:
        logger.info("memory_refs: could not load reference stamps for owner %s: %r", key, exc)
    return snapshot(key)


async def ensure_loaded(owner_id: Any) -> RefsView:
    """Load the owner's snapshot once per process (recall's call); a no-op
    afterwards. A failed load leaves the owner unloaded, so the next call
    tries again."""
    key = _owner_key(owner_id)
    if key is None:
        return _EMPTY
    if key in _snapshots:
        return snapshot(key)
    return await refresh(key)


async def bump(owner_id: Any, item_ids: Iterable[Any], now: Optional[float] = None) -> None:
    """Stamp these bullets as referenced: ``last_referenced_at = now`` and
    ``reference_count += 1`` in the table. Blank and repeated ids are one bump
    each; nothing to bump is a no-op. The snapshot is NOT touched (R7) — the
    core sees these stamps after the next ``refresh``."""
    ids: list[str] = []
    for raw in item_ids or ():
        item_id = str(raw or "").strip()
        if item_id and item_id not in ids:
            ids.append(item_id)
    if not ids:
        return
    from core.database import brain

    await brain.bump_memory_bullet_refs(
        owner_user_id=int(owner_id),
        item_ids=ids,
        now=float(time.time() if now is None else now),
    )


def memory_core_budget() -> int:
    """``memory.core_budget_chars`` from the settings snapshot; the schema's
    default (12000) when settings cannot be read or the value is missing or
    zero. ONE helper (Batch 5, task 4, ruling R38) for the two places that
    plan the core — recall's dedup of its look-up against it and the
    adapter's render — so they plan the same core; the snapshot above makes
    their other input identical. Sync, like the render."""
    try:
        from core.settings_boot import settings_service

        memory = settings_service.snapshot().memory
    except Exception:
        memory = None
    return int(getattr(memory, "core_budget_chars", 12000) or 12000)


def corrections_budget() -> int:
    """``memory.corrections_budget_chars`` from the settings snapshot — the
    budget of the reply's corrections-of-record band (Batch 5, task 7 fix
    round 1, ruling R56: a setting, so the owner turns it without a build).
    The schema's default (1500) when settings cannot be read or the value
    is missing. Unlike the core budget, 0 is kept: it turns the band off.
    Sync, like the render; memory_core_budget's sibling."""
    try:
        from core.settings_boot import settings_service

        memory = settings_service.snapshot().memory
    except Exception:
        memory = None
    try:
        return int(getattr(memory, "corrections_budget_chars", 1500))
    except (TypeError, ValueError):
        return 1500


def forget(owner_id: Any = None) -> None:
    """Drop the in-process snapshot for one owner, or every owner: the next
    ``ensure_loaded`` loads again. Tests use it; so can a reload lever."""
    if owner_id is None:
        _snapshots.clear()
        return
    key = _owner_key(owner_id)
    if key is not None:
        _snapshots.pop(key, None)


__all__ = ["bump", "corrections_budget", "ensure_loaded", "forget", "load_refs", "memory_core_budget", "refresh", "snapshot"]
