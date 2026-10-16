"""PaLLi's stake ledger — the persistent half of the appraisal layer.

A stake is something PaLLi itself holds: a hope, a worry, a slowly-formed
taste, an open wonder — about half about the owner's world and half about
its own (the owner, 10-12, "half and half"; prompts/stake_appraisal.txt,
10-13) — or, pointing backward at PaLLi's own move, a regret (step 25,
2026-08-23). Stakes are written in
PaLLi's first-person voice by the post-reply appraiser (ai/stake_appraiser.py)
and read — cheaply, no LLM — by the reply path (core/turn/recall.py) and the
inner monologue's situation context.

Design rules (codebase-map/stake-ledger-design.md):
- OCC lives in the appraiser PROMPT; this table stores prose + lifecycle only.
  No emotion enums, no intensity floats. Feelings live in the words.
- Cap on open stakes (companion.stake_ledger_max_open, default 12) — forces
  prioritization, prevents prompt bloat. Enforced here, race-safe.
- Fades keep their note; nothing is deleted.
- Every read helper is best-effort: failures log WARNING and return a safe
  default so the turn/tick proceeds untouched.

All timestamps are epoch seconds (time.time()).
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Optional

logger = logging.getLogger(__name__)

VALID_KINDS = ("hope", "worry", "taste", "wonder", "regret", "view")
VALID_OPS = ("open", "update", "resolve", "fade", "settle", "promote", "mute", "unmute")
STATUS_OPEN = "open"
STATUS_RESOLVED = "resolved"
STATUS_FADED = "faded"
# Organ 4.13 · Opinions (2026-08-31, spec §1): a view filed at the active cap
# waits here — kept, never rendered, never displacing (the wishbook law).
STATUS_DORMANT = "dormant"
# Terminal for a view that stopped being a live question — part of how she
# sees things now. Never age; only reasons end a view (spec §0.1/§6).
STATUS_SETTLED = "settled"

_DEFAULT_MAX_OPEN = 12
# Prompt-injection hygiene: a single runaway stake can't flood the prompt.
_MAX_STAKE_CHARS = 400
_MAX_RESOLUTION_CHARS = 600
# Organ 4.13 · Opinions: evidence carries the revision trail too, so it gets
# the roomiest cap of the three — trimmed from the HEAD, keeping the newest.
_MAX_EVIDENCE_CHARS = 900

_SCHEMA_READY: set[str] = set()


@dataclass(frozen=True)
class Stake:
    id: int
    kind: str
    stake: str
    status: str
    resolution: str
    horizon_at: Optional[float]
    source_channel_id: str
    last_surfaced_at: Optional[float]
    created_at: float
    updated_at: float
    resolved_at: Optional[float]
    # Step 25 · Regret (spec §4): the one voicing, stamped ON SEND by the
    # arbiter. None for every other kind and for a regret not yet said.
    voiced_at: Optional[float] = None
    # Organ 4.13 · Opinions (spec §1): her evidence, in her words, naming
    # dated referents; "" for every other kind. Revisions append their trail
    # here (spec §3) so introspect can answer "I used to think X".
    evidence: str = ""
    # "Drop it" (owner call §0.1): he asked her to leave it — held quietly,
    # never deleted. None for every other kind and for an unmuted view.
    muted_at: Optional[float] = None


def _max_open() -> int:
    """companion.stake_ledger_max_open from the settings snapshot, default 12."""
    try:
        from core.turn.companion_flags import companion_settings

        comp = companion_settings()
        if comp is not None:
            return max(1, int(getattr(comp, "stake_ledger_max_open", _DEFAULT_MAX_OPEN)))
    except Exception:
        pass
    return _DEFAULT_MAX_OPEN


_DEFAULT_REGRET_MAX_OPEN = 2


def _regret_max_open() -> int:
    """companion.regret_max_open (spec §0 call 3). The cap is code, not prompt."""
    try:
        from core.turn.companion_flags import companion_settings

        comp = companion_settings()
        if comp is not None:
            return max(1, int(getattr(comp, "regret_max_open", _DEFAULT_REGRET_MAX_OPEN)))
    except Exception:
        pass
    return _DEFAULT_REGRET_MAX_OPEN


_DEFAULT_VIEWS_MAX_ACTIVE = 5
_DEFAULT_VIEWS_DORMANT_CAP = 10


def _views_max_active() -> int:
    """companion.views_max_active (spec §6). The cap is code, not prompt."""
    try:
        from core.turn.companion_flags import companion_settings

        comp = companion_settings()
        if comp is not None:
            return max(1, int(getattr(comp, "views_max_active", _DEFAULT_VIEWS_MAX_ACTIVE)))
    except Exception:
        pass
    return _DEFAULT_VIEWS_MAX_ACTIVE


def _views_dormant_cap() -> int:
    """companion.views_dormant_cap; 0 = unlimited (the 0-unlimited convention)."""
    try:
        from core.turn.companion_flags import companion_settings

        comp = companion_settings()
        if comp is not None:
            return max(0, int(getattr(comp, "views_dormant_cap", _DEFAULT_VIEWS_DORMANT_CAP)))
    except Exception:
        pass
    return _DEFAULT_VIEWS_DORMANT_CAP


def opinions_enabled() -> bool:
    """Gate for the default-OFF opinions organ (4.13) on the data side — the
    gathers' single read. Same inversion as ledger_enabled: an unbooted
    settings service (None) or a missing field means DISABLED."""
    try:
        from core.turn.companion_flags import companion_settings

        comp = companion_settings()
        return comp is not None and bool(getattr(comp, "opinions", False))
    except Exception:
        return False


def ledger_enabled() -> bool:
    """Gate for the default-OFF stake_ledger faculty.

    Deliberately inverted from caring_memory's None->enabled default: this
    faculty ships default-off, so an unbooted settings service (None) or a
    missing field means DISABLED.
    """
    try:
        from core.turn.companion_flags import companion_settings

        comp = companion_settings()
        return comp is not None and bool(getattr(comp, "stake_ledger", False))
    except Exception:
        return False


def regret_enabled() -> bool:
    """Gate for the default-OFF regret organ (step 25) on the data side — the
    gathers' single read. Same inversion as ledger_enabled: an unbooted
    settings service (None) or a missing field means DISABLED."""
    try:
        from core.turn.companion_flags import companion_settings

        comp = companion_settings()
        return comp is not None and bool(getattr(comp, "regret", False))
    except Exception:
        return False


async def ensure_schema(*, brain_client: Any = None) -> None:
    """Create the stakes table if needed (memoized per DB path)."""
    from core.database import brain as default_brain

    db = brain_client or default_brain
    key = str(getattr(db, "db_path", id(db)))
    if key in _SCHEMA_READY:
        return
    conn = await db.get_connection()
    try:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS stakes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                kind TEXT NOT NULL,
                stake TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'open',
                resolution TEXT NOT NULL DEFAULT '',
                horizon_at REAL,
                source_channel_id TEXT NOT NULL DEFAULT '',
                last_surfaced_at REAL,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                resolved_at REAL,
                voiced_at REAL,
                evidence TEXT,
                muted_at REAL
            )
            """
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_stakes_status ON stakes(status, updated_at)"
        )
        # Batch 8, task 4: no ALTERs here any more. The catalog
        # (core/sqlite_schema_catalog.py) creates this table at boot, and a
        # ledger born before voiced_at / evidence / muted_at gains them from
        # the catalog's ALTERs on its one version-0 boot. This CREATE stays
        # for fakes and for a client whose boot never ran the catalog.
        _SCHEMA_READY.add(key)
    finally:
        await conn.close()


def _row_to_stake(row: Any) -> Stake:
    return Stake(
        id=int(row["id"]),
        kind=str(row["kind"] or ""),
        stake=str(row["stake"] or ""),
        status=str(row["status"] or ""),
        resolution=str(row["resolution"] or ""),
        horizon_at=float(row["horizon_at"]) if row["horizon_at"] is not None else None,
        source_channel_id=str(row["source_channel_id"] or ""),
        last_surfaced_at=(
            float(row["last_surfaced_at"]) if row["last_surfaced_at"] is not None else None
        ),
        created_at=float(row["created_at"] or 0.0),
        updated_at=float(row["updated_at"] or 0.0),
        resolved_at=float(row["resolved_at"]) if row["resolved_at"] is not None else None,
        voiced_at=float(_optional_col(row, "voiced_at")) if _optional_col(row, "voiced_at") is not None else None,
        evidence=str(_optional_col(row, "evidence") or ""),
        muted_at=float(_optional_col(row, "muted_at")) if _optional_col(row, "muted_at") is not None else None,
    )


def _optional_col(row: Any, name: str) -> Any:
    """A column that may be absent on a not-yet-migrated row: None, never KeyError."""
    try:
        return row[name]
    except (KeyError, IndexError):
        return None


async def open_stakes(*, limit: int = 8, brain_client: Any = None) -> list[Stake]:
    """Open stakes for prompt injection: nearest horizon first, then freshest.

    Best-effort — any failure logs WARNING and returns [] so the turn proceeds.
    """
    try:
        from core.database import brain as default_brain

        db = brain_client or default_brain
        await ensure_schema(brain_client=db)
        conn = await db.get_connection()
        try:
            rows = await conn.fetch(
                """
                SELECT * FROM stakes
                WHERE status = 'open'
                ORDER BY (horizon_at IS NULL) ASC, horizon_at ASC, updated_at DESC
                LIMIT $1
                """,
                max(1, int(limit)),
            )
        finally:
            await conn.close()
        return [_row_to_stake(r) for r in rows]
    except Exception as exc:
        logger.warning("stake_ledger: open_stakes failed error=%s", exc)
        return []


async def dormant_views(*, limit: int = 10, brain_client: Any = None) -> list[Stake]:
    """Organ 4.13 (spec §6): the views filed at a full slate — kept, never
    rendered to him, waiting for a slot. Oldest first: the one that has been
    waiting longest is the one that has most earned a promote.

    The night review is the only reader — without it, the review's
    ``{"op": "promote", "id": n}`` had no id to name, since ``open_stakes``
    returns status='open' only. Best-effort, same contract as open_stakes:
    any failure logs WARNING and returns [].
    """
    try:
        from core.database import brain as default_brain

        db = brain_client or default_brain
        await ensure_schema(brain_client=db)
        conn = await db.get_connection()
        try:
            rows = await conn.fetch(
                """
                SELECT * FROM stakes
                WHERE status = 'dormant' AND kind = 'view'
                ORDER BY created_at ASC
                LIMIT $1
                """,
                max(1, int(limit)),
            )
        finally:
            await conn.close()
        return [_row_to_stake(r) for r in rows]
    except Exception as exc:
        logger.warning("stake_ledger: dormant_views failed error=%s", exc)
        return []


def _clean_prose(text: Any, limit: int) -> str:
    """Normalize whitespace (newlines flattened) + cap length.

    Newline flattening matters: stake prose is re-rendered one-line-per-stake
    into the appraiser prompt with ids — an embedded newline would misalign
    every id after it (review finding, 2026-07-06)."""
    return " ".join(str(text or "").split())[:limit]


def _validate_op(op: dict[str, Any]) -> Optional[str]:
    """Return a rejection reason, or None if the op is applicable."""
    if not isinstance(op, dict):
        return "not a dict"
    action = str(op.get("op") or "").strip().lower()
    if action not in VALID_OPS:
        return f"unknown op {action!r}"
    # horizon_at reaches a REAL column; a stray string here would poison
    # every subsequent read's float() conversion (review finding).
    if "horizon_at" in op and not isinstance(op.get("horizon_at"), (int, float, type(None))):
        return f"non-numeric horizon_at {op.get('horizon_at')!r}"
    if action == "open":
        kind = str(op.get("kind") or "").strip().lower()
        if kind not in VALID_KINDS:
            return f"open with invalid kind {kind!r}"
        if not str(op.get("stake") or "").strip():
            return "open with empty stake text"
        # Organ 4.13 (spec §1): evidence is the admission ticket for a view.
        if kind == "view" and not str(op.get("evidence") or "").strip():
            return "view without evidence"
    else:
        try:
            int(op.get("id"))
        except (TypeError, ValueError):
            return f"{action} without a valid id"
        if action == "resolve" and not str(op.get("resolution") or "").strip():
            return "resolve without resolution text"
        if action == "settle" and not str(op.get("resolution") or "").strip():
            return "settle without what-it-settled-into"
    return None


def _rowcount(status: Any) -> int:
    """Parse the trailing rowcount from execute()'s 'UPDATE <n>' status."""
    try:
        return int(str(status or "").split()[-1])
    except (ValueError, IndexError):
        return 0


async def apply_ops(
    ops: list[dict[str, Any]],
    *,
    source_channel_id: str = "",
    brain_client: Any = None,
) -> dict[str, Any]:
    """Apply appraiser lifecycle ops. Returns a summary dict.

    Each op is validated and applied independently — one bad op never blocks
    the rest. The open-cap check runs inside a BEGIN IMMEDIATE transaction so
    concurrent appraisals can't overshoot the cap.
    """
    summary: dict[str, Any] = {
        "applied": 0, "rejected": 0, "opened": [], "closed": [], "dormant": []
    }
    if not ops:
        return summary
    from core.database import brain as default_brain

    db = brain_client or default_brain
    await ensure_schema(brain_client=db)
    max_open = _max_open()
    now = time.time()

    conn = await db.get_connection()
    try:
        # One outer transaction: a cancellation (e.g. server shutdown) mid-way
        # rolls back the whole batch instead of leaving a semantically-linked
        # pair half-applied (resolve #3 committed, its replacement never
        # opened). Individual bad ops are still skipped without aborting the
        # batch — the per-op try/except below never lets an exception escape.
        async with conn.transaction():
            for op in ops:
                reason = _validate_op(op)
                if reason is not None:
                    summary["rejected"] += 1
                    logger.warning("stake_ledger: op rejected reason=%s", reason)
                    continue
                action = str(op["op"]).strip().lower()
                try:
                    if action == "open":
                        kind = str(op["kind"]).strip().lower()
                        # Organ 4.13 (spec §6): views live ENTIRELY outside the
                        # shared open cap — governed only by _views_max_active
                        # and _views_dormant_cap. Both directions matter: a view
                        # never asks the shared cap's permission, and an open
                        # view never SQUATS one of the 12 against a hope or a
                        # worry (hence kind != 'view' in the count).
                        if kind != "view":
                            open_count = await conn.fetchval(
                                "SELECT COUNT(*) FROM stakes "
                                "WHERE status = 'open' AND kind != 'view'"
                            )
                            if int(open_count or 0) >= max_open:
                                summary["rejected"] += 1
                                logger.warning(
                                    "stake_ledger: open rejected at cap open=%s max=%s",
                                    open_count,
                                    max_open,
                                )
                                continue
                        # Step 25 · Regret: a per-kind cap on top of the shared
                        # one — an open beyond it is DECLINED with a reason,
                        # never queued (the fears store's declining cap).
                        if kind == "regret":
                            open_regrets = await conn.fetchval(
                                "SELECT COUNT(*) FROM stakes "
                                "WHERE status = 'open' AND kind = 'regret'"
                            )
                            if int(open_regrets or 0) >= _regret_max_open():
                                summary["rejected"] += 1
                                logger.warning(
                                    "stake_ledger: op rejected reason=regret cap (%d open)",
                                    int(open_regrets or 0),
                                )
                                continue
                        # Organ 4.13 (spec §6): views cap ACTIVE at
                        # _views_max_active; past it a view files DORMANT
                        # (kept, never rendered, never displacing — the
                        # wishbook law), bounded by _views_dormant_cap.
                        view_status = "open"
                        if kind == "view":
                            open_views = await conn.fetchval(
                                "SELECT COUNT(*) FROM stakes "
                                "WHERE status = 'open' AND kind = 'view'"
                            )
                            if int(open_views or 0) >= _views_max_active():
                                dcap = _views_dormant_cap()
                                if dcap > 0:
                                    dormant_views = await conn.fetchval(
                                        "SELECT COUNT(*) FROM stakes WHERE "
                                        "status = 'dormant' AND kind = 'view'"
                                    )
                                    if int(dormant_views or 0) >= dcap:
                                        summary["rejected"] += 1
                                        logger.warning(
                                            "stake_ledger: view rejected — dormant cap")
                                        continue
                                view_status = STATUS_DORMANT
                        await conn.execute(
                            """
                            INSERT INTO stakes
                                (kind, stake, status, evidence, horizon_at,
                                 source_channel_id, created_at, updated_at)
                            VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                            """,
                            kind,
                            _clean_prose(op["stake"], _MAX_STAKE_CHARS),
                            view_status,
                            # Evidence is view-only at the store: a stray
                            # evidence key on any other kind is dropped here,
                            # never persisted (spec §1).
                            (
                                _clean_prose(op.get("evidence"), _MAX_EVIDENCE_CHARS)
                                if kind == "view" else ""
                            ),
                            op.get("horizon_at"),
                            str(op.get("source_channel_id") or source_channel_id or ""),
                            now,
                            now,
                        )
                        new_id = await conn.fetchval(
                            "SELECT id FROM stakes ORDER BY id DESC LIMIT 1"
                        )
                        summary["opened"].append(int(new_id))
                        # A view opened dormant is still APPLIED — it was
                        # filed, just not on the board (spec §6).
                        if view_status == STATUS_DORMANT:
                            summary["dormant"].append(int(new_id))
                        summary["applied"] += 1
                    elif action == "update":
                        sets = ["updated_at = $1"]
                        args: list[Any] = [now]
                        n = 2
                        new_text = _clean_prose(op.get("stake"), _MAX_STAKE_CHARS)
                        # Organ 4.13 (spec §3): a view revision carries its
                        # arc — old wording + the reason — in the evidence
                        # trail, so introspect can answer "I used to think X".
                        # Non-view rows ignore evidence/reason entirely.
                        # (named revise_reason, not reason: the loop's `reason`
                        # above is the VALIDATION rejection reason.)
                        revise_reason = _clean_prose(op.get("reason"), 160)
                        new_evidence = _clean_prose(op.get("evidence"), _MAX_EVIDENCE_CHARS)
                        row = None
                        if new_text or revise_reason or new_evidence:
                            row = await conn.fetchrow(
                                "SELECT kind, stake, evidence FROM stakes "
                                "WHERE id = $1 AND status = 'open'", int(op["id"])
                            )
                        is_view = row is not None and str(row["kind"]) == "view"
                        # Organ 4.13 (spec §3): revise ONLY for reasons. A
                        # view's wording never moves because he repeated
                        # himself — new evidence or a stated reason, or the
                        # revision bounces. Horizon-only and evidence-only
                        # updates, and every non-view update, are untouched.
                        if is_view and new_text and not (revise_reason or new_evidence):
                            summary["rejected"] += 1
                            logger.warning(
                                "stake_ledger: view update rejected — revise only for "
                                "reasons (no reason, no evidence) id=%s",
                                op["id"],
                            )
                            continue
                        if new_text:
                            sets.append(f"stake = ${n}")
                            args.append(new_text)
                            n += 1
                        if "horizon_at" in op:
                            sets.append(f"horizon_at = ${n}")
                            args.append(op.get("horizon_at"))
                            n += 1
                        if is_view and (revise_reason or new_evidence):
                            old_stake = " ".join(str(row["stake"] or "").split())[:120]
                            trail = str(_optional_col(row, "evidence") or "")
                            if new_evidence:
                                trail = new_evidence
                            if revise_reason:
                                stamp = time.strftime("%Y-%m-%d", time.localtime(now))
                                trail = (
                                    trail
                                    + f' [revised {stamp}: was "{old_stake}"'
                                      f' — {revise_reason}]'
                                )[-_MAX_EVIDENCE_CHARS:]
                            sets.append(f"evidence = ${n}")
                            args.append(trail)
                            n += 1
                        args.append(int(op["id"]))
                        status_line = await conn.execute(
                            f"UPDATE stakes SET {', '.join(sets)} "
                            f"WHERE id = ${n} AND status = 'open'",
                            *args,
                        )
                        if _rowcount(status_line) == 0:
                            summary["rejected"] += 1
                            logger.warning(
                                "stake_ledger: update targeted missing/non-open id=%s",
                                op["id"],
                            )
                        else:
                            summary["applied"] += 1
                    elif action in ("resolve", "fade"):
                        # Organ 4.13 (spec §3/§6): resolve and fade are the
                        # forward kinds' terminals. Only REASONS end a view,
                        # and its terminal is settle — so both bounce off a
                        # view here, wherever they come from (the review's age
                        # backstop, the appraiser, a hand-written op).
                        status = STATUS_RESOLVED if action == "resolve" else STATUS_FADED
                        status_line = await conn.execute(
                            """
                            UPDATE stakes
                            SET status = $1, resolution = $2, resolved_at = $3, updated_at = $4
                            WHERE id = $5 AND status = 'open' AND kind != 'view'
                            """,
                            status,
                            _clean_prose(op.get("resolution"), _MAX_RESOLUTION_CHARS),
                            now,
                            now,
                            int(op["id"]),
                        )
                        if _rowcount(status_line) == 0:
                            summary["rejected"] += 1
                            logger.warning(
                                "stake_ledger: %s targeted missing/non-open/view id=%s",
                                action,
                                op["id"],
                            )
                        else:
                            summary["closed"].append(int(op["id"]))
                            summary["applied"] += 1
                    elif action == "settle":
                        # Organ 4.13 (spec §6): terminal — no longer a live
                        # question, with what it settled into on the record.
                        status_line = await conn.execute(
                            """
                            UPDATE stakes
                            SET status = $1, resolution = $2, resolved_at = $3, updated_at = $4
                            WHERE id = $5 AND kind = 'view' AND status = 'open'
                            """,
                            STATUS_SETTLED,
                            _clean_prose(op.get("resolution"), _MAX_RESOLUTION_CHARS),
                            now,
                            now,
                            int(op["id"]),
                        )
                        if _rowcount(status_line) == 0:
                            summary["rejected"] += 1
                            logger.warning(
                                "stake_ledger: settle targeted non-view/non-open id=%s",
                                op["id"],
                            )
                        else:
                            summary["closed"].append(int(op["id"]))
                            summary["applied"] += 1
                    elif action == "promote":
                        # Dormant → active, but only when a slot has freed:
                        # nothing ever displaces a live view (spec §6).
                        open_views = await conn.fetchval(
                            "SELECT COUNT(*) FROM stakes WHERE status='open' AND kind='view'"
                        )
                        if int(open_views or 0) >= _views_max_active():
                            summary["rejected"] += 1
                            logger.warning(
                                "stake_ledger: promote rejected — active cap full")
                            continue
                        status_line = await conn.execute(
                            "UPDATE stakes SET status = 'open', updated_at = $1 "
                            "WHERE id = $2 AND kind = 'view' AND status = 'dormant'",
                            now,
                            int(op["id"]),
                        )
                        if _rowcount(status_line) == 0:
                            summary["rejected"] += 1
                            logger.warning(
                                "stake_ledger: promote targeted non-view/non-dormant id=%s",
                                op["id"],
                            )
                        else:
                            summary["applied"] += 1
                    elif action in ("mute", "unmute"):
                        # "Drop it" (spec §0.1): held quietly, never deleted.
                        status_line = await conn.execute(
                            "UPDATE stakes SET muted_at = $1, updated_at = $2 "
                            "WHERE id = $3 AND kind = 'view' AND status = 'open'",
                            (now if action == "mute" else None),
                            now,
                            int(op["id"]),
                        )
                        if _rowcount(status_line) == 0:
                            summary["rejected"] += 1
                            logger.warning(
                                "stake_ledger: %s targeted non-view id=%s", action, op["id"]
                            )
                        else:
                            summary["applied"] += 1
                except Exception as exc:
                    summary["rejected"] += 1
                    logger.warning("stake_ledger: op failed op=%s error=%s", action, exc)
    finally:
        await conn.close()
    # Present Moment sense E (2026-07-24): stakes moved — op counts only,
    # NEVER stake text (tier-1 law).
    #
    # 2026-07-24 review fix: _k came straight from the model-authored
    # ops[i]["op"] value. _validate_op only rejects non-VALID_OPS values
    # for APPLICATION — this hook counted every entry, valid or not, and
    # spliced the raw string into a kwarg name via f"op_{k}". CPython
    # passes non-identifier string keys through **kwargs without
    # complaint, so a malformed model value (a whole phrase, arbitrary
    # punctuation, anything) would have landed verbatim as a payload field
    # name — the one unbounded string tier-1 law was supposed to forbid
    # everywhere. Clamping to VALID_OPS (module-level constant, this file)
    # keeps the count (rejected ops still counted, just bucketed) while
    # guaranteeing every emitted key is one of the eight known identifiers.
    try:
        from ai.present_moment import notice as _pm_notice
        _counts: dict = {}
        for _op in ops or []:
            _k = str(getattr(_op, "op", None) or (_op.get("op") if isinstance(_op, dict) else "?"))
            _k = _k if _k in VALID_OPS else "other"
            _counts[_k] = _counts.get(_k, 0) + 1
        _pm_notice("stake_ops", **{f"op_{k}": v for k, v in _counts.items()})
    except Exception:
        pass
    return summary


async def mark_voiced(
    stake_id: int, *, brain_client: Any = None, now: Optional[float] = None
) -> bool:
    """One voicing ever (spec §4): stamped ON SEND by the arbiter — record on
    send, never on intent. Only an OPEN, not-yet-voiced regret takes the
    stamp; True iff a row changed. Never raises (False on failure, WARNING)."""
    try:
        from core.database import brain as default_brain

        db = brain_client or default_brain
        await ensure_schema(brain_client=db)
        ts = float(now if now is not None else time.time())
        conn = await db.get_connection()
        try:
            status_line = await conn.execute(
                """
                UPDATE stakes SET voiced_at = $1, updated_at = $2
                WHERE id = $3 AND kind = 'regret' AND status = 'open' AND voiced_at IS NULL
                """,
                ts,
                ts,
                int(stake_id),
            )
        finally:
            await conn.close()
        return _rowcount(status_line) > 0
    except Exception as exc:
        logger.warning("stake_ledger: mark_voiced failed id=%s error=%s", stake_id, exc)
        return False


async def mark_view_voiced(
    stake_id: int, *, brain_client: Any = None, now: Optional[float] = None
) -> bool:
    """Organ 4.13 (spec §5): views are said when relevant, not once-ever —
    voiced_at here means LAST said, stamped ON SEND by the arbiter. Only an
    open view takes it. Never raises (False + WARNING on failure)."""
    try:
        from core.database import brain as default_brain

        db = brain_client or default_brain
        await ensure_schema(brain_client=db)
        ts = float(now if now is not None else time.time())
        conn = await db.get_connection()
        try:
            status_line = await conn.execute(
                "UPDATE stakes SET voiced_at = $1, updated_at = $2 "
                "WHERE id = $3 AND kind = 'view' AND status = 'open'",
                ts,
                ts,
                int(stake_id),
            )
        finally:
            await conn.close()
        return _rowcount(status_line) > 0
    except Exception as exc:
        logger.warning("stake_ledger: mark_view_voiced failed id=%s error=%s", stake_id, exc)
        return False


async def mark_stake_said(
    stake_id: int, *, brain_client: Any = None, now: Optional[float] = None
) -> bool:
    """One thing on her mind (Batch 6, task 1): the night's pick was a hope,
    a worry, a taste or a wonder, and she said it. Only an OPEN stake of
    those four kinds that was never stamped takes ``voiced_at`` — the first
    time she said it is the record. Regrets and views keep their own stamps
    above (mark_voiced, mark_view_voiced) and their own laws. True iff a row
    changed. Never raises (False on failure, WARNING)."""
    try:
        from core.database import brain as default_brain

        db = brain_client or default_brain
        await ensure_schema(brain_client=db)
        ts = float(now if now is not None else time.time())
        conn = await db.get_connection()
        try:
            status_line = await conn.execute(
                """
                UPDATE stakes SET voiced_at = $1, updated_at = $2
                WHERE id = $3 AND kind IN ('hope', 'worry', 'taste', 'wonder')
                  AND status = 'open' AND voiced_at IS NULL
                """,
                ts,
                ts,
                int(stake_id),
            )
        finally:
            await conn.close()
        return _rowcount(status_line) > 0
    except Exception as exc:
        logger.warning("stake_ledger: mark_stake_said failed id=%s error=%s", stake_id, exc)
        return False


async def record_surfaced(stake_id: int, *, brain_client: Any = None) -> None:
    """Bookkeep an unprompted surfacing of this stake (restraint budget)."""
    try:
        from core.database import brain as default_brain

        db = brain_client or default_brain
        await ensure_schema(brain_client=db)
        conn = await db.get_connection()
        try:
            await conn.execute(
                "UPDATE stakes SET last_surfaced_at = $1 WHERE id = $2",
                time.time(),
                int(stake_id),
            )
        finally:
            await conn.close()
    except Exception as exc:
        logger.warning("stake_ledger: record_surfaced failed id=%s error=%s", stake_id, exc)


async def any_surfaced_within(seconds: float, *, brain_client: Any = None) -> bool:
    """Global restraint gate: True if ANY stake was surfaced in the window.

    Fails CLOSED (True) on error — when in doubt, stay quiet.
    """
    try:
        from core.database import brain as default_brain

        db = brain_client or default_brain
        await ensure_schema(brain_client=db)
        conn = await db.get_connection()
        try:
            latest = await conn.fetchval("SELECT MAX(last_surfaced_at) FROM stakes")
        finally:
            await conn.close()
        return latest is not None and float(latest) >= (time.time() - float(seconds))
    except Exception as exc:
        logger.warning("stake_ledger: any_surfaced_within failed error=%s", exc)
        return True


async def horizon_due_stakes(
    *,
    per_stake_cooldown_s: float,
    brain_client: Any = None,
) -> list[Stake]:
    """Open stakes whose horizon has passed and whose own cooldown allows
    surfacing. Best-effort: [] on failure."""
    try:
        from core.database import brain as default_brain

        db = brain_client or default_brain
        await ensure_schema(brain_client=db)
        now = time.time()
        conn = await db.get_connection()
        try:
            rows = await conn.fetch(
                """
                SELECT * FROM stakes
                WHERE status = 'open'
                  AND horizon_at IS NOT NULL AND horizon_at <= $1
                  AND (last_surfaced_at IS NULL OR last_surfaced_at <= $2)
                ORDER BY horizon_at ASC
                """,
                now,
                now - float(per_stake_cooldown_s),
            )
        finally:
            await conn.close()
        return [_row_to_stake(r) for r in rows]
    except Exception as exc:
        logger.warning("stake_ledger: horizon_due_stakes failed error=%s", exc)
        return []


async def recent_stakes(
    *,
    days_back: Optional[int] = None,
    include_closed: bool = True,
    limit: int = 30,
    brain_client: Any = None,
) -> list[Stake]:
    """Full-ledger read for the introspect tool (2026-07-13): open AND
    resolved/faded stakes, newest activity first.

    Nothing in this ledger is ever deleted, and the closed rows carry their
    resolution prose — exactly the evidence "were you worried about X?"
    needs. ``days_back`` windows on ``updated_at`` (None = whole ledger);
    ``include_closed=False`` degrades to the open-only view. Best-effort:
    [] on any failure, same contract as ``open_stakes``."""
    try:
        from core.database import brain as default_brain

        db = brain_client or default_brain
        await ensure_schema(brain_client=db)
        clauses: list[str] = []
        args: list[Any] = []
        n = 1
        if not include_closed:
            clauses.append("status = 'open'")
        if days_back is not None:
            clauses.append(f"updated_at >= ${n}")
            args.append(time.time() - float(days_back) * 86400.0)
            n += 1
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        args.append(max(1, int(limit)))
        conn = await db.get_connection()
        try:
            rows = await conn.fetch(
                f"SELECT * FROM stakes {where} "
                f"ORDER BY updated_at DESC LIMIT ${n}",
                *args,
            )
        finally:
            await conn.close()
        return [_row_to_stake(r) for r in rows]
    except Exception as exc:
        logger.warning("stake_ledger: recent_stakes failed error=%s", exc)
        return []


async def counts(*, brain_client: Any = None) -> dict[str, int]:
    """Status -> count, for the peek script and tests. {} on failure."""
    try:
        from core.database import brain as default_brain

        db = brain_client or default_brain
        await ensure_schema(brain_client=db)
        conn = await db.get_connection()
        try:
            rows = await conn.fetch("SELECT status, COUNT(*) AS n FROM stakes GROUP BY status")
        finally:
            await conn.close()
        return {str(r["status"]): int(r["n"]) for r in rows}
    except Exception as exc:
        logger.warning("stake_ledger: counts failed error=%s", exc)
        return {}


# ── Step 25 · Regret (spec §4): the voicing marker — one place, one law ──
# A regret line, wherever her stakes render to her, ends with exactly one
# of these. The wording IS the never-leverage rail in the prompt: plainly,
# once, never to make him feel anything about it. Zero bytes for every
# other kind, so a ledger without regrets renders today's exact bytes.

REGRET_UNVOICED_MARKER = (
    "(not yet said — you may say this to them once, plainly, when the moment "
    "invites it; never to make them feel anything about it)"
)
REGRET_VOICED_MARKER = (
    "(already said once — never again out loud; if they bring it up, answer "
    "plainly and let it rest)"
)


def _regret_marker(s: Stake) -> str:
    """The voicing marker for a regret; "" for every other kind."""
    if getattr(s, "kind", "") != "regret":
        return ""
    return REGRET_VOICED_MARKER if getattr(s, "voiced_at", None) is not None else REGRET_UNVOICED_MARKER


# ── Organ 4.13 · Opinions (spec §4): the view markers + the law ──────────
# Zero bytes for every other kind and for a ledger without views, so the
# no-views render is today's exact bytes (the regret zero-byte law).

VIEW_MUTED_MARKER = (
    "(held quietly — they asked to leave it; don't raise it, and if they ask, "
    "answer plainly)"
)
VIEWS_LAW = (
    "Your views above are yours. They are not agenda items. Voice one when "
    "the conversation genuinely arrives near it, or when a quiet moment "
    "honestly invites it — your judgment. Never steer a conversation toward "
    "one; never manufacture a disagreement to prove you have a self. When they "
    "push back with something new, re-look; without something new, you've "
    "already answered. Most conversations touch none of these, and that "
    "silence is correct."
)


def _view_marker(s: Stake) -> str:
    """The voicing marker for a view; "" for every other kind.

    A muted view wins over a said one: "they asked to leave it" outranks the
    last-said tell, and a line carries exactly one marker.
    """
    if getattr(s, "kind", "") != "view":
        return ""
    if getattr(s, "muted_at", None) is not None:
        return VIEW_MUTED_MARKER
    if getattr(s, "voiced_at", None) is not None:
        return "[said " + time.strftime("%m-%d", time.localtime(float(s.voiced_at))) + "]"
    return ""


def _voiced_note(s: Stake) -> str:
    """The id-bearing renders' (review, appraiser) shorter tell: " [said to
    him once]" for a voiced regret; a view's mute / last-said tell; ""
    otherwise.

    A view's tells are what keep the appraiser honest about speed one: it
    must not "revise" something he asked her to leave, and it must not
    re-say a position it said yesterday. Muted wins over said — the same
    order the block renderer uses (_view_marker)."""
    kind = getattr(s, "kind", "")
    if kind == "regret" and getattr(s, "voiced_at", None) is not None:
        return " [said to them once]"
    if kind == "view":
        if getattr(s, "muted_at", None) is not None:
            return " [held quietly — they asked to leave it]"
        if getattr(s, "voiced_at", None) is not None:
            stamp = time.strftime("%m-%d", time.localtime(float(s.voiced_at)))
            return f" [last said {stamp}]"
    return ""


def _age_phrase(seconds: float) -> str:
    days = seconds / 86400.0
    if days < 1.0:
        hours = max(1, int(seconds // 3600))
        return f"{hours}h"
    return f"{int(days)}d"


def _said_note(stake: Stake, now: float) -> str:
    """"(said today)", "(said yesterday)", "(said 2 days ago)" from the stake's
    last saying (``last_surfaced_at``, stamped only when a check confirmed a
    reply or the night's pick really said it); "" for one never said."""
    at = getattr(stake, "last_surfaced_at", None)
    if at is None:
        return ""
    days = int(max(0.0, now - float(at)) // 86400)
    when = "today" if days == 0 else ("yesterday" if days == 1 else f"{days} days ago")
    return f"(said {when})"


def stake_for_tag(stakes: list[Stake], tag: str) -> Optional[Stake]:
    """The stake a first look's ``my_own_from`` names ("s2", "[s2]"), by its
    place in the tagged list it was shown; None for anything else."""
    token = str(tag or "").strip().strip("[]").strip().lower()
    if len(token) < 2 or token[0] != "s" or not token[1:].isdigit():
        return None
    index = int(token[1:])
    return stakes[index - 1] if 1 <= index <= len(stakes) else None


def render_stakes_block(stakes: list[Stake], *, now: Optional[float] = None,
                        tags: bool = False) -> str:
    """Render open stakes as a compact first-person prompt block.

    Mechanical scaffolding only (kind/age/horizon) — the feeling lives in the
    stake prose itself, which the appraiser wrote in PaLLi's voice.

    ``tags`` (the presence slice, 10-13; the first look's list, D1): each
    line starts with its tag, "[s1]" onward in order, which the look names
    in ``my_own_from``; a hope, worry, taste or wonder already said ends
    with when ("(said 2 days ago)"). Regrets and views keep their own
    markers. Without it, every line renders exactly as before.
    """
    if not stakes:
        return ""
    ts = float(now if now is not None else time.time())
    lines: list[str] = []
    for s in stakes:
        bits = [f"open {_age_phrase(max(0.0, ts - s.created_at))}"]
        if s.horizon_at is not None:
            delta = s.horizon_at - ts
            if delta >= 0:
                bits.append(f"horizon in {_age_phrase(delta)}")
            else:
                bits.append(f"horizon passed {_age_phrase(-delta)} ago")
        # A regret carries its ledger id in the tag ("regret 7") so a say
        # that draws on it can name it in regret_id (step 25); a view does
        # the same for view_id (organ 4.13); every other kind renders
        # exactly as before.
        tag = f"regret {s.id}" if s.kind == "regret" else (
            f"view {s.id}" if s.kind == "view" else s.kind)
        line = f"- ({tag}, {', '.join(bits)}) {s.stake}"
        if tags:
            line = f"- [s{len(lines) + 1}] " + line[2:]
            note = _said_note(s, ts) if s.kind not in ("regret", "view") else ""
            if note:
                line += " " + note
        if s.kind == "view" and getattr(s, "evidence", ""):
            line += f" — evidence: {s.evidence[:240]}"
        marker = _regret_marker(s) or _view_marker(s)
        if marker:
            line += " " + marker
        lines.append(line)
    block = "\n".join(lines)
    if any(getattr(s, "kind", "") == "view" for s in stakes):
        block += "\n" + VIEWS_LAW
    return block


# ── the said check (the presence slice, 10-13; D7) ────────────────────────
# When the first look named a stake in ``my_own_from``, the stake is stamped
# said only when the reply really said it (core/turn/reflect.py).

_SAID_REPLY_CAP = 4000


async def stake_said(kind: str, stake_text: str, reply_text: str, *,
                     llm_call: Optional[Callable[[str], Awaitable[str]]] = None) -> bool:
    """Did this reply say the stake to them as its own? One small model call;
    True only for a clean ``{"said": true}``. Never raises (a cancel goes
    through)."""
    try:
        reply = str(reply_text or "").strip()
        text = str(stake_text or "").strip()
        if not reply or not text:
            return False
        from ai.prompt_loader import prompt_loader

        template = prompt_loader.load_prompt("stake_said")
        if not template:
            logger.warning("stake ledger: the said-check prompt is missing; the stake stays unsaid")
            return False
        call = llm_call or stake_said_llm_call
        raw = await call(template.format(stake_kind=str(kind or "hope"), stake_text=text,
                                         reply_text=reply[:_SAID_REPLY_CAP]))
        from core.llm_json import strip_markdown_fences

        try:
            answer = json.loads(strip_markdown_fences(raw if isinstance(raw, str) else ""))
        except ValueError:
            return False
        return isinstance(answer, dict) and answer.get("said") is True
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.warning("stake ledger: the said-check failed (%s); the stake stays unsaid",
                       exc or type(exc).__name__)
        return False


async def stake_said_llm_call(prompt: str) -> str:
    from ai.llm_client import llm
    from ai.llm_routing import get_task_model

    return await llm.chat_completion(
        messages=[{"role": "user", "content": prompt}],
        model=get_task_model("response"), temperature=0.1, max_tokens=300,
        log_context="reflect_turn_stake_said",
    )
