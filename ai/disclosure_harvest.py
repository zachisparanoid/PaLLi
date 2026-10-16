"""The nightly harvest — Grounded Vulnerability's writer (spec §2).

One question over her week's own record: "what happened to me this week
that's worth sharing?" Zero most nights. At most ONE candidate, planted
with its receipt; the store refuses anything unreceipted. The fade sweep
runs first so the book breathes. Sources are the §0.3 named list — a
future organ that holds lived events adds its source here (the house
maintenance rule).

De-duplication belongs to this module, not the store: plant() slugs from
text+time, so the same sentence harvested twice would get two distinct
ids and nothing downstream would ever notice. Two rails hold it — the
book she already has rides the prompt (she is told not to write one she
already holds), and a verbatim repeat is refused here in code.
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)
essential_logger = logging.getLogger("palli.essential")

_HARVEST_TIMEOUT_S = 1000.0   # clears the provider's whole DEEP ladder (4×240 s + backoff = 970); pinned ≥ deep_ladder_ceiling_seconds by test_the_harvest_envelope_holds_the_whole_deep_ladder — the stake_review/owner_picture precedent
_SECTION_CAP = 2600
_BOOK_ROWS = 20               # what she already holds, newest-first, for the de-dup rail


def _clip(text: str, cap: int = _SECTION_CAP) -> str:
    return str(text or "")[:cap]


def _gather_week_material(root: Optional[Path] = None) -> str:
    """The §0.3 named sources, best-effort each: monologue (7d) · journal
    pages (7d) · night notes (7d) · her recent deeds (7d).

    Every signature here is the real one, verified against the module:
    the monologue and night-note stores hand back DATACLASSES (attribute
    access), the deeds ring hands back dicts. ``root`` is the DISCLOSURE
    book's root — her record has exactly one home, so it is deliberately
    not forwarded to the readers; it stays in the signature because the
    caller passes it and a future source may need it.

    Each source is wrapped because one unreadable book must not cost the
    other three — never because a call was left unchecked."""
    parts: List[str] = []
    try:
        from ai.inner_monologue_store import read_entries

        rows = read_entries(days_back=7) or []       # newest-first
        recent = list(reversed(rows[:40]))            # the newest 40, read forward in time
        parts.append("HER WEEK'S PRIVATE THOUGHTS:\n" + _clip("\n".join(
            f"- {str(getattr(r, 'thought', '') or '')[:200]}" for r in recent)))
    except Exception as exc:
        logger.debug("disclosure_harvest: monologue unreadable: %s", exc)
    try:
        from ai.narrative_self import identity_palli_root

        jdir = identity_palli_root() / "journal"
        pages = sorted(jdir.glob("*.md"))[-7:]
        parts.append("HER JOURNAL, THIS WEEK:\n" + _clip("\n\n".join(
            f"[{p.stem}]\n{p.read_text(encoding='utf-8')[:600]}" for p in pages)))
    except Exception as exc:
        logger.debug("disclosure_harvest: journal unreadable: %s", exc)
    try:
        from ai.night_cycle_store import read_recent

        notes = read_recent(days_back=7, limit=7) or []   # NightCycleEntry rows
        parts.append("HER NIGHT NOTES:\n" + _clip("\n".join(
            f"- [{getattr(n, 'date', '') or '?'}] {str(getattr(n, 'text', '') or '')[:220]}"
            for n in notes)))
    except Exception as exc:
        logger.debug("disclosure_harvest: night notes unreadable: %s", exc)
    try:
        from ai.recent_deeds import read_recent_deeds

        deeds = read_recent_deeds(limit=20, max_age_hours=168.0) or []   # dict rows
        parts.append("THINGS SHE DID:\n" + _clip("\n".join(
            f"- {str(d.get('detail', ''))[:160]}" for d in deeds)))
    except Exception as exc:
        logger.debug("disclosure_harvest: deeds unreadable: %s", exc)
    return "\n\n".join(p for p in parts if p) or "(a quiet week — nothing readable)"


def _normalize(text: str) -> str:
    """The de-dup key: her words, case- and whitespace-blind, clipped the
    way the store clips them so both sides of the comparison line up."""
    from ai import disclosure_store as ds

    return " ".join(str(text or "").split())[:ds.TEXT_CAP].lower()


def _book_section(root: Optional[Path] = None) -> str:
    """What she already holds — every state, not just the unsaid ones: a
    shared truth is said-once forever and a faded one aged out; neither
    should come back around tonight wearing the same words."""
    from ai import disclosure_store as ds

    try:
        rows = ds.recent_disclosures(limit=_BOOK_ROWS, root=root)
    except Exception as exc:      # a corrupt book must not cost the harvest
        logger.debug("disclosure_harvest: book unreadable: %s", exc)
        return "(unreadable tonight — write nothing you might already have)"
    if not rows:
        return "(nothing yet — this book is empty)"
    def _state(d) -> str:
        if d.said_at is not None:
            return "already shared"
        return "faded" if d.faded_at is not None else "unsaid"
    return _clip("\n".join(f"- [{_state(d)}] {d.text}" for d in rows))


def _already_in_the_book(text: str, root: Optional[Path] = None) -> bool:
    from ai import disclosure_store as ds

    key = _normalize(text)
    if not key:
        return False
    return any(_normalize(d.text) == key for d in ds.load_disclosures(root=root).values())


def _parse_candidate(raw: str) -> tuple[Optional[dict], str]:
    """→ (candidate, refusal).

    ``(None, "")`` is the QUIET WEEK — she answered inside the contract and
    had nothing, the expected common case. A non-empty refusal means she
    answered OUTSIDE it, and that must never read like a quiet week: a
    model swap or prompt drift that stops producing contract JSON would
    otherwise be a permanent run of quiet weeks on the health line, which
    is the whole class the passions fix (2026-07-20) exists to close."""
    from core.llm_json import safe_parse_json_object

    obj = safe_parse_json_object(raw or "")
    if not isinstance(obj, dict):
        return None, "unparseable"      # garbage, prose, an empty reply
    if "candidate" not in obj:
        return None, "unparseable"      # {"candidates": [...]} is not the contract
    cand = obj["candidate"]
    if cand is None:
        return None, ""                 # {"candidate": null} — most weeks
    if not isinstance(cand, dict):
        return None, "unparseable"
    text = cand.get("text")
    if not isinstance(text, str):
        # NEVER str()-coerce her words. A reply of {"text": {"a": 1}} would
        # plant the literal "{'a': 1}" as her lived language on the most
        # intimate surface she has — and the store cannot catch it, because
        # its fold-time isinstance guard sees an already-stringified value.
        return None, "malformed_text"
    ref = cand.get("referent")
    ref = ref if isinstance(ref, dict) else {}
    # referent is a DICT by construction — disclosure_store.plant() reads
    # .get() off it and a bare string would raise on the way in.
    return {"text": text,
            "referent": {"source": str(ref.get("source") or ""),
                         "dates": str(ref.get("dates") or "")}}, ""


async def run_nightly_disclosure_harvest(
    *, day: str, llm_call: Callable[[str], Awaitable[str]],
    now: Optional[float] = None, root: Optional[Path] = None,
) -> Dict[str, Any]:
    """Containment note (honest about its own edge): the sweep and the cap
    read sit OUTSIDE the try below, so an OSError from either — a read-only
    fs, an unreadable book — propagates instead of becoming result["error"],
    and the `faded` count is lost with it. The night step's own except still
    names it on the health line (OSErrors carry messages, and since the I-1
    fix even a nameless one does), so nothing goes silent; it is the count,
    not the signal, that is lost."""
    from ai import disclosure_store as ds

    if not ds.vulnerability_enabled():
        return {"skipped": "flag_off"}
    result: Dict[str, Any] = {"faded": ds.fade_stale(now=now, root=root), "planted": None}
    if len(ds.unsaid_disclosures(root=root)) >= ds._max_open():
        result["skipped"] = "cap_full"
        return result
    try:
        from ai.prompt_loader import prompt_loader

        template = prompt_loader.load_prompt("night_cycle_disclosure_harvest") or ""
        if not template:
            raise RuntimeError("harvest prompt missing")
        prompt = (template
                  .replace("<<WEEK_MATERIAL>>", _gather_week_material(root=root))
                  .replace("<<ALREADY_IN_THE_BOOK>>", _book_section(root=root))
                  .replace("<<CURRENT_DATE>>", day))
        raw = await asyncio.wait_for(llm_call(prompt), timeout=_HARVEST_TIMEOUT_S)
        cand, refusal = _parse_candidate(raw)
        if refusal:
            logger.warning("disclosure_harvest: %s answered outside the contract (%s)",
                           day, refusal)
            result["skipped"] = refusal
        elif cand is not None:
            if _already_in_the_book(cand["text"], root=root):
                logger.info("disclosure_harvest: candidate already in the book — not planted")
                result["skipped"] = "already_in_the_book"
            else:
                # The cap was checked above and only one plant ever runs, so
                # a None here is the receipt law refusing: no words, or a
                # missing/half receipt. That is a contract failure, not a
                # quiet week, and the health line must be able to say so.
                d = ds.plant(text=cand["text"], referent=cand["referent"], root=root)
                if d is None:
                    result["skipped"] = "no_receipt"
                else:
                    result["planted"] = d.id
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        # The type name is the FALLBACK, never the decoration: asyncio's
        # TimeoutError — the very exception _HARVEST_TIMEOUT_S exists to
        # raise — has str() == "", and an empty error reads as healthy on
        # both designed surfaces (the essential log's suffix and
        # _surface_errors both gate on truthiness). A nameless failure was a
        # permanent run of quiet weeks.
        logger.warning("disclosure_harvest: pass failed for %s: %s", day,
                       exc or type(exc).__name__)
        result["error"] = (" ".join(str(exc).split()) or type(exc).__name__)[:160]
    essential_logger.info(
        "disclosure_harvest: %s — planted=%s faded=%s%s", day,
        result.get("planted"), result.get("faded"),
        f" error={result['error']}" if result.get("error") else "")
    return result


async def disclosure_harvest_llm_call(prompt: str) -> str:
    from ai.llm_client import llm
    from ai.llm_routing import get_task_model

    return await llm.chat_completion(
        messages=[{"role": "user", "content": prompt}],
        model=get_task_model("appraisal"), max_tokens=2000, temperature=0.6,
        log_context="night_cycle_disclosure_harvest",
    )
