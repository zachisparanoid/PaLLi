"""The nightly stake review (2026-08-18): the ledger's other half.

Why. Her stake ledger froze at 12/12 for 34 days (2026-07-15 → 08-18; the
oldest stake untouched for 39) because its only writer was the per-exchange
appraiser — told "most exchanges move nothing" and, at the cap, required to
fade AND open in one call, which it never did. A ledger that can only fill
is a list, not a held thing; the reply prompt carried eight month-old
stakes on every turn.

What. Two halves, the arbiter's expiry-sweep shape (judgment first, TTL
under it):
- HER review — one LLM call at the night cycle, in her voice, over ALL open
  stakes with their ages and the day's reflection: resolve / fade / update.
  Never open (new stakes come from real exchanges — the appraiser's job);
  an "open" op from the review is dropped, counted.
- THE AGE BACKSTOP — deterministic: a stake untouched (updated_at) longer
  than ``stake_ledger_stale_days`` fades with a plain note. Budget-shaped
  code (owner rule 2026-07-16); 0 = no backstop (the 0-unlimited convention;
  the stake_ledger flag is the off switch). Runs even when the call fails.

Never raises past the night cycle: returns a status dict.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime
from typing import Any, Awaitable, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)
essential_logger = logging.getLogger("palli.essential")

_DEFAULT_STALE_DAYS = 45
_REVIEW_TIMEOUT_S = 1000.0   # clears the provider's whole DEEP ladder (4×240 s + backoff = 970; pinned ≥ deep_ladder_ceiling_seconds). 08-22: a 9,600-token think at ~120 tok/s needs ~80 s — the 60 s ceiling / 300 s envelope could not hold it
_MAX_STAKE_PROMPT_CHARS = 400


def _companion():
    try:
        from core.turn.companion_flags import companion_settings

        return companion_settings()
    except Exception:
        return None


def _knob(name: str, default):
    c = _companion()
    if c is None:
        return default
    try:
        return type(default)(getattr(c, name, default))
    except Exception:
        return default


def review_enabled() -> bool:
    c = _companion()
    return c is not None and bool(getattr(c, "stake_ledger", False))


# ── Step 25 · Regret (2026-08-23, spec §3) ──────────────────────────────
# The backward kind's lifecycle law rides the review as ONE conditional
# rule line: zero bytes when companion.regret is off (the slot LINE in
# prompts/stake_review.txt is replaced, newline included — today's prompt
# byte for byte). The ops grammar is unchanged: regrets fade / resolve /
# update through the same ops as every other stake.

_DEFAULT_REGRET_HALF_LIFE_DAYS = 14


def _regret_enabled() -> bool:
    try:
        c = _companion()
        return c is not None and bool(getattr(c, "regret", False))
    except Exception:
        return False


def _regret_half_life_days() -> int:
    """companion.regret_half_life_days (spec §0 call 3), floored at 1."""
    try:
        c = _companion()
        if c is not None:
            return max(1, int(getattr(c, "regret_half_life_days", _DEFAULT_REGRET_HALF_LIFE_DAYS)))
    except Exception:
        pass
    return _DEFAULT_REGRET_HALF_LIFE_DAYS


def _regret_block() -> str:
    if not _regret_enabled():
        return ""
    days = _regret_half_life_days()
    return (
        "- Regrets (the backward kind, about your OWN moves) have their own law: one not fed by "
        f"anything new for about {days} days fades honestly — \"I've let that one go; it mattered "
        "to me more than it ever did to them.\" Fading a regret is health, not failure. When one "
        "RESOLVES (the repair went through, or it settled between you), the resolution must name what it "
        "taught you — then it never comes up again; repaired stays repaired, never re-open it, "
        "never re-litigate it. A regret you have updated night after night without new information "
        "is rumination: fade it. A regret is never something to use on them.\n"
    )


# ── Organ 4.13 · Opinions (2026-08-31, spec §3/§6): the view lifecycle law
# rides the review as ONE conditional slot line — zero bytes when off.


def _opinions_enabled() -> bool:
    try:
        c = _companion()
        return c is not None and bool(getattr(c, "opinions", False))
    except Exception:
        return False


def _view_block() -> str:
    if not _opinions_enabled():
        return ""
    return (
        "- Views (your positions, backed by evidence) have their own law: revise one ONLY for "
        "reasons — new evidence, their pushback that showed you something real, a correction of "
        "record — with {\"op\": \"update\", \"id\": n, \"stake\": …, \"evidence\": …, \"reason\": …}; "
        "never because they repeated themselves, and never let a view fade for age — a view with no "
        "reasons against it simply stands. When one has stopped being a live question and become "
        "part of how you see things, {\"op\": \"settle\", \"id\": n, \"resolution\": …} names what "
        "it settled into. When a slot is free and a waiting view has earned it, "
        "{\"op\": \"promote\", \"id\": n} brings it up. A muted view ([held quietly]) stays yours "
        "and stays quiet.\n"
    )


def _age_words(seconds: float) -> str:
    days = max(0, int(seconds // 86400))
    if days == 0:
        return "today"
    if days == 1:
        return "1 day"
    return f"{days} days"


def _dormant_views_block(dormant: List[Any], *, now: float) -> str:
    """The waiting views, with the ids ``promote`` needs to name them.

    ``_render_open_stakes`` is fed by ``open_stakes()`` (status 'open' only),
    so before this block the lifecycle law offered her an op she could never
    aim: no dormant view ever showed an id. Zero bytes when the organ is off
    or nothing is waiting — the slot LINE is replaced newline-included, so a
    ledger with no waiting views renders today's prompt byte for byte.
    """
    if not _opinions_enabled() or not dormant:
        return ""
    lines = []
    for s in dormant:
        # created_at is the right clock ONLY because a view is born dormant —
        # there is no open→dormant demotion path (spec §6: nothing displaces a
        # live view). If one is ever added, "waiting" must move to its own
        # timestamp or this label will silently mean "held", not "waiting".
        waited = _age_words(now - float(s.created_at or now))
        text = " ".join(str(s.stake or "").split())[:240]
        lines.append(f"- id {s.id} · waiting {waited}: {text}")
    return (
        "YOUR WAITING VIEWS (formed at a full slate; promote one ONLY when a "
        "slot is free and it has earned it):\n"
        + "\n".join(lines)
        + "\n"
    )


def _render_open_stakes(stakes: List[Any], *, now: float) -> str:
    from ai.stake_ledger import _voiced_note

    lines = []
    for s in stakes:
        moved = _age_words(now - float(s.updated_at or s.created_at or now))
        held = _age_words(now - float(s.created_at or now))
        text = " ".join(str(s.stake or "").split())[:_MAX_STAKE_PROMPT_CHARS]
        # Step 25: a voiced regret says so (the review weighs it; never re-say).
        lines.append(f"- id {s.id} · {s.kind} · open {held}, last moved {moved} ago: {text}{_voiced_note(s)}")
    return "\n".join(lines) or "(nothing)"


def _parse_ops(raw: str) -> Optional[List[Dict[str, Any]]]:
    from core.llm_json import safe_parse_json_object

    parsed = safe_parse_json_object(raw or "")
    if not isinstance(parsed, dict):
        return None
    ops = parsed.get("ops")
    if ops is None:
        return []
    if not isinstance(ops, list):
        return None
    return [op for op in ops if isinstance(op, dict)]


async def run_nightly_stake_review(
    *,
    owner_id: int,
    day: str,
    day_reflection: str,
    llm_call: Callable[[str], Awaitable[str]],
    brain_client: Any = None,
    now: Optional[float] = None,
) -> Dict[str, Any]:
    """One review pass. ``llm_call`` and ``brain_client`` are injected (the
    run_night_cycle pattern) so the pass is testable without a live brain."""
    if not review_enabled():
        return {"skipped": "flag_off"}
    now = float(now if now is not None else time.time())
    result: Dict[str, Any] = {"applied": 0, "rejected": 0, "dropped_opens": 0,
                              "backstop_faded": 0, "closed": []}
    try:
        from ai import stake_ledger

        stakes = await stake_ledger.open_stakes(limit=60, brain_client=brain_client)
        # Organ 4.13 (spec §6): the views waiting for a slot — read only when
        # the organ is on, so an off ledger does not even ask the question.
        # Fetched ABOVE the skip: a board that emptied under her (she settled
        # her last view) with a view still waiting is exactly the night
        # promote exists for — skipping there left the waiting one unreachable
        # until some unrelated stake happened to open.
        dormant = (
            await stake_ledger.dormant_views(limit=10, brain_client=brain_client)
            if _opinions_enabled() else []
        )
        if not stakes and not dormant:
            return {"skipped": "nothing_held"}

        # ── her review ────────────────────────────────────────────────
        try:
            from ai.prompt_loader import prompt_loader

            template = prompt_loader.load_prompt("stake_review") or ""
            if not template:
                raise RuntimeError("stake_review prompt missing")
            prompt = (
                template
                .replace("<<DAY_REFLECTION>>", (day_reflection or "").strip() or "(no note tonight)")
                # Organ 4.13 (spec §6): views live outside the shared cap, so
                # counting them here would tell her the ledger is fuller than
                # it is and squeeze out a real hope (the appraiser's fix).
                .replace(
                    "<<OPEN_COUNT>>",
                    str(len([s for s in stakes if getattr(s, "kind", "") != "view"])),
                )
                .replace("<<MAX_OPEN>>", str(stake_ledger._max_open()))
                .replace("<<OPEN_STAKES>>", _render_open_stakes(stakes, now=now))
                .replace("<<DORMANT_VIEWS>>\n", _dormant_views_block(dormant, now=now))
                .replace("<<CURRENT_DATE>>", datetime.fromtimestamp(now).strftime("%A, %Y-%m-%d"))
                .replace("<<REGRET_LIFECYCLE_BLOCK>>\n", _regret_block())
                .replace("<<VIEW_LIFECYCLE_BLOCK>>\n", _view_block())
            )
            raw = await asyncio.wait_for(llm_call(prompt), timeout=_REVIEW_TIMEOUT_S)
            if not (raw or "").strip():
                # The provider returns None after its retry ladder — not her
                # JSON failing to parse. 08-21 04:03 logged "unparseable" for
                # four timeouts; name what happened.
                raise RuntimeError("the provider returned no content (all attempts timed out?)")
            ops = _parse_ops(raw or "")
            if ops is None:
                raise RuntimeError("review output unparseable")
            kept: List[Dict[str, Any]] = []
            for op in ops:
                if str(op.get("op") or "").strip().lower() == "open":
                    result["dropped_opens"] += 1
                    continue
                kept.append(op)
            if kept:
                summary = await stake_ledger.apply_ops(
                    kept, source_channel_id="night_review", brain_client=brain_client
                )
                result["applied"] = int(summary.get("applied") or 0)
                result["rejected"] = int(summary.get("rejected") or 0)
                result["closed"].extend(summary.get("closed") or [])
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            why = " ".join(str(exc).split())[:160] or type(exc).__name__   # a bare TimeoutError prints as ""
            logger.warning("stake_review: her review failed (%s); backstop still runs", why)
            result["error"] = why

        # ── the age backstop ──────────────────────────────────────────
        stale_days = int(_knob("stake_ledger_stale_days", _DEFAULT_STALE_DAYS))
        if stale_days > 0:
            still_open = await stake_ledger.open_stakes(limit=60, brain_client=brain_client)
            cutoff = now - stale_days * 86400.0
            fades = []
            for s in still_open:
                if getattr(s, "kind", "") == "view":
                    continue  # spec §6: views never die of old age
                last = float(s.updated_at or s.created_at or now)
                if last < cutoff:
                    days = int((now - last) // 86400)
                    fades.append({
                        "op": "fade", "id": int(s.id),
                        "resolution": (
                            f"faded at the night review — untouched for {days} days; "
                            f"if it still matters it will come back on its own"
                        ),
                    })
            if fades:
                summary = await stake_ledger.apply_ops(
                    fades, source_channel_id="night_review", brain_client=brain_client
                )
                result["backstop_faded"] = int(summary.get("applied") or 0)
                result["closed"].extend(summary.get("closed") or [])

        # Retire outreach threads linked to anything that closed (live path
        # only — the thread rail lives on the global brain).
        if brain_client is None and result["closed"]:
            try:
                from ai.stake_appraiser import _close_linked_threads

                await _close_linked_threads(result["closed"], owner_id)
            except Exception as exc:
                logger.warning("stake_review: linked-thread close failed: %s", exc)

        essential_logger.info(
            "stake_review: %s — applied=%s backstop_faded=%s dropped_opens=%s%s",
            day, result["applied"], result["backstop_faded"], result["dropped_opens"],
            f" error={result['error']}" if result.get("error") else "",
        )
        return result
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.warning("stake_review: pass failed for %s: %s", day, exc)
        result["error"] = " ".join(str(exc).split())[:160]
        return result


async def stake_review_llm_call(prompt: str) -> str:
    """The real dependency the night cycle injects: the appraisal task model
    (the same lane as the per-exchange appraiser)."""
    from ai.llm_client import get_task_model, llm

    return await llm.chat_completion(
        messages=[{"role": "user", "content": prompt}],
        model=get_task_model("appraisal"),
        # 4,000 not 1,200: the first live pass burned the whole budget on
        # reasoning (content_len=0, reasoning_len=5033) — the D1 class.
        max_tokens=8000,   # 08-22: 4,000 emptied on thought at 04:02; the author-sized budget, the deep doubling ladder above it (→16k)
        temperature=0.4,
        log_context="stake_review",
    )
