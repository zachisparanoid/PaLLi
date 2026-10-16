"""The post-reply stake appraiser — the write half of the appraisal layer.

Runs AFTER PaLLi's reply is out (fire-and-forget from core/agent/dispatch.py),
so it is off the latency-critical path entirely. It sees the actual exchange
(user message + the reply PaLLi really sent — downstream of reality, never a
parallel reader), PaLLi's open stakes, and the owner-editable character spine
(identity/palli/WHAT_I_CARE_ABOUT.md), and emits lifecycle ops that
ai/stake_ledger.py applies.

Character rules (the OCC taxonomy, operationalized) live in
prompts/stake_appraisal.txt — the PROMPT carries the taxonomy, the DATA stays
prose. See codebase-map/stake-ledger-design.md.

Never raises: any failure logs WARNING and returns a summary dict; the turn
this rode behind has already been delivered either way.

Views are the one kind this writer no longer OPENS (Batch 6, task 2,
2026-09-29). A view is a stance that shows up across a day, and seconds
after one message that cannot be seen: not one view ever formed here. They
form at the nightly pass at the foot of this module (appraise_views), from
the whole day. Revising a view, muting one and bringing one back stay
here, in the turn, and take effect at once.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

logger = logging.getLogger(__name__)
essential_logger = logging.getLogger("palli.essential")

# 25 → 90 (2026-08-04), 90 → 150 (2026-08-09). This deadline has to fit the
# provider's budget-retry, which costs a SECOND request: at 25s it cut a live
# retry off mid-flight and the appraisal of the owner's flag-trap correction —
# the exchange that mattered most that day — was skipped instead of recorded.
# Since the patient/snappy split, this lane's per-request ceiling is 60s
# (OpenAICompatibleProvider.PATIENT_REQUEST_TIMEOUT_SECONDS), so a
# retry-capable caller needs at least 120s; 150 leaves headroom for the
# bumped budget's longer generation. Nothing waits on this lane — it is
# fire-and-forget off the reply path — so the extra patience costs nothing.
APPRAISAL_TIMEOUT_SECONDS = 150.0
# Serialize appraisals: two rapid turns would otherwise snapshot the same
# ledger, both wait ~seconds on their LLM calls, and both open the same stake
# (duplicate-hope race — review finding). Appraisal is off the latency path,
# so serialization costs nothing the owner can feel.
_APPRAISAL_LOCK = asyncio.Lock()
_CARE_SPINE_FALLBACK = (
    "I care about the things they're building toward, whether they're resting, and "
    "how the stories they start turn out."
)


def _load_care_spine() -> str:
    """identity/palli/WHAT_I_CARE_ABOUT.md, frontmatter stripped.

    Owner-editable; read per appraisal (tiny file, post-reply path) so edits
    take effect without a restart. Best-effort with a neutral fallback.
    """
    try:
        from core.config import config

        identity_root = os.path.abspath(getattr(config, "IDENTITY_ROOT_PATH", "identity"))
        # Her live spine first (self-authored after Wave 1b first authorship;
        # owner-editable before it). A fresh checkout carries no live file —
        # fall back to the SHIPPED template seed, and only then to the
        # generic stub (fix wave 2026-07-17, review #1: the silent generic
        # fallback flattened this live faculty on any clean clone).
        for name in ("WHAT_I_CARE_ABOUT.md", "WHAT_I_CARE_ABOUT.template.md"):
            path = os.path.join(identity_root, "palli", name)
            if not os.path.isfile(path):
                continue
            with open(path, "r", encoding="utf-8") as fh:
                text = fh.read()
            # ONE canonical frontmatter strip, shared with ai/narrative_self —
            # two private copies let the spine's consumers drift (fix wave
            # 2026-07-17; the line-anchored-delimiter lesson of 2026-07-06
            # lives inside the shared helper now).
            from ai.narrative_self import strip_frontmatter

            body = strip_frontmatter(text)
            if body:
                return body
        return _CARE_SPINE_FALLBACK
    except Exception as exc:
        logger.warning("stake_appraisal: care spine load failed error=%s", exc)
        return _CARE_SPINE_FALLBACK


def _parse_horizon(value: Any, *, now: Optional[float] = None) -> Optional[float]:
    """ISO date/datetime -> epoch seconds. Date-only means end of that day
    (a 'Thursday' horizon should trip after Thursday, not at midnight).
    None/null/invalid -> None (with a warning for invalid)."""
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() in ("null", "none"):
        return None
    try:
        if len(text) == 10:  # YYYY-MM-DD
            dt = datetime.fromisoformat(text).replace(hour=23, minute=59, second=0)
        else:
            dt = datetime.fromisoformat(text)
        return dt.timestamp()
    except (ValueError, TypeError) as exc:
        logger.warning("stake_appraisal: bad horizon %r (%s); dropping", text, exc)
        return None


def _parse_ops(raw: str) -> Optional[list[dict[str, Any]]]:
    """Extract the ops list from the LLM output. None = unparseable (drop the
    whole appraisal — never partial-apply garbage). [] is a valid, common
    result. Tolerates code fences/prose around the JSON object.

    Keys are WHITELISTED: everything else the model invents is dropped, so a
    stray raw "horizon_at" string can never reach the REAL column and poison
    ledger reads (review finding).

    Horizon semantics (review finding — null used to CLEAR real horizons):
    - open:   "YYYY-MM-DD" sets it; null/omit means no horizon.
    - update: "YYYY-MM-DD" sets it; the literal "clear" removes it;
              null/omit means UNCHANGED (the model can't reliably re-emit a
              date it was never shown, so absence must be safe).
    """
    match = re.search(r"\{.*\}", raw or "", re.DOTALL)
    if not match:
        return None
    try:
        obj = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    if not isinstance(obj, dict):
        return None
    ops = obj.get("ops")
    if ops is None:
        return None
    if not isinstance(ops, list):
        return None
    cleaned: list[dict[str, Any]] = []
    for op in ops:
        if not isinstance(op, dict):
            logger.warning("stake_appraisal: non-dict op dropped")
            continue
        out: dict[str, Any] = {}
        for key in ("op", "id", "kind", "stake", "resolution", "evidence", "reason"):
            if key in op:
                out[key] = op[key]
        action = str(op.get("op") or "").strip().lower()
        raw_horizon = op.get("horizon")
        raw_horizon_text = str(raw_horizon or "").strip().lower()
        if action == "open":
            horizon_at = _parse_horizon(raw_horizon)
            out["horizon_at"] = horizon_at  # None = no horizon, explicit
        elif action == "update" and "horizon" in op:
            if raw_horizon_text == "clear":
                out["horizon_at"] = None
            else:
                horizon_at = _parse_horizon(raw_horizon)
                if horizon_at is not None:
                    out["horizon_at"] = horizon_at
                # null/unparseable on update -> key omitted -> unchanged
        cleaned.append(out)
    return cleaned


# ── Step 25 · Regret (2026-08-23, spec §2) ──────────────────────────────
# The fifth kind is offered to the appraiser ONLY when companion.regret is
# on. Four format slots in prompts/stake_appraisal.txt carry it; off, every
# slot renders "" / today's value and the prompt is byte-identical to the
# pre-step-25 file (the Fears zero-byte-collapse law).

_REGRET_KIND_BLOCK = (
    '- "regret" points BACKWARD and only at PaLLi: a specific move of its own that misfired — '
    "something said, done, or left undone (the question not asked, the thing called finished that "
    "wasn't). The bar is \"that was mine, and it misfired\": named, concrete, first person. Not a "
    "mood, not general self-criticism. A relational fear is about what might go wrong between us; "
    "a regret is about what I did. Most exchanges produce none.\n"
)
_REGRET_NEVER_LINE = (
    " Never a regret about THEIR choices or feelings; never a regret about anything the corrections "
    "of record above already settled; never a second regret about the same move; never use a "
    "regret to get anything from them."
)
_REGRET_KINDS_UNION = "hope|worry|taste|wonder|regret"
_TODAYS_KINDS_UNION = "hope|worry|taste|wonder"
_REGRET_CORRECTIONS_BUDGET = 600


def _regret_enabled() -> bool:
    """companion.regret — the organ's off switch for this writer."""
    try:
        from core.turn.companion_flags import companion_settings

        comp = companion_settings()
        return comp is not None and bool(getattr(comp, "regret", False))
    except Exception:
        return False


def _regret_corrections_block() -> str:
    """Corrections of record, rendered FIRST — before the ledger — so the
    "never from anything already settled" veto has something to read (the
    24 pattern: every self-writing gather puts this block first). Read-only
    per turn (sync=False: his hand-file syncs at the night cycle). "" when
    nothing stands, so the slot collapses to zero bytes."""
    try:
        from ai.corrections_store import render_corrections_block

        block = render_corrections_block(budget=_REGRET_CORRECTIONS_BUDGET, sync=False)
    except Exception as exc:
        logger.debug("stake_appraisal: corrections block failed error=%s", exc)
        return ""
    return (block + "\n\n") if block else ""


def _regret_prompt_kwargs() -> dict[str, str]:
    """The four regret slots for stake_appraisal.txt: today's bytes when off."""
    if _regret_enabled():
        return {
            "regret_corrections_block": _regret_corrections_block(),
            "regret_kind_block": _REGRET_KIND_BLOCK,
            "open_kinds": _REGRET_KINDS_UNION,
            "regret_never_line": _REGRET_NEVER_LINE,
        }
    return {
        "regret_corrections_block": "",
        "regret_kind_block": "",
        "open_kinds": _TODAYS_KINDS_UNION,
        "regret_never_line": "",
    }


# ── Organ 4.13 · Opinions (2026-08-31, spec §2/§3) ──────────────────────
# The sixth kind + speed-one revision. Slots render "" when off so the
# prompt is byte-identical for BOTH regret states (the regret law, squared).
#
# Batch 6, task 2 (2026-09-29, rulings R25/R26): FORMING left the turn.
# The kind block now says where views form, the ops hint no longer offers
# an open, and open_kinds no longer gains the kind. The bar a new view has
# to clear is the nightly pass's prompt (prompts/night_cycle_views.txt).
# The never line is word for word what it was, but for two plain words
# (Batch 7's fix wave, the final review's M1: "held quietly" -> "kept
# quietly", "GENUINELY" -> "REALLY"): revising, muting and bringing back a
# view that already exists are the turn's to do.

_VIEW_KIND_BLOCK = (
    '- "view" is a POSITION of mine, not a taste, kept with its evidence. New views are not '
    "formed here: they form at night, from the whole day, so never open one from an exchange. "
    "A view already on the ledger above can still move here, by the rules at the end of the "
    "NEVER line below. Scope: facts, taste, and approach — NEVER their personal choices unless "
    "they asked my opinion of one.\n"
)
_VIEW_NEVER_LINE = (
    " Never a view manufactured to prove I have a self; never a view about their personal choices "
    "unasked; never revise a view because they repeated themselves — only for something new. If THEY "
    "ask me to drop a view, emit mute (kept quietly, never deleted); if this exchange REALLY "
    "moved one of my views, emit update with the new stake, evidence, and a reason — the record "
    "must match what I said in the room. If they explicitly invite a dropped view back, emit unmute."
)
# SINGLE braces: this string is a format VALUE, and values are never
# re-scanned — doubling them would render literal {{ }} into her live prompt.
_VIEW_OPS_HINT = (
    '    {"op": "update", "id": 4, "stake": "the revised position", '
    '"evidence": "what changed", "reason": "why it moved me"},\n'
    '    {"op": "mute", "id": 4},\n'
    '    {"op": "unmute", "id": 4},\n'
)


def _opinions_enabled() -> bool:
    """companion.opinions — the organ's off switch for this writer."""
    try:
        from core.turn.companion_flags import companion_settings

        comp = companion_settings()
        return comp is not None and bool(getattr(comp, "opinions", False))
    except Exception:
        return False


def _view_prompt_kwargs() -> dict[str, str]:
    """The view slots + the kinds a turn may open. Merged AFTER
    _regret_prompt_kwargs() in the format call so this open_kinds wins. A
    view is never among them: the union follows the regret flag alone."""
    base = _REGRET_KINDS_UNION if _regret_enabled() else _TODAYS_KINDS_UNION
    if _opinions_enabled():
        return {
            "view_kind_block": _VIEW_KIND_BLOCK,
            "view_never_line": _VIEW_NEVER_LINE,
            "view_ops_hint": _VIEW_OPS_HINT,
            "open_kinds": base,
        }
    return {
        "view_kind_block": "",
        "view_never_line": "",
        "view_ops_hint": "",
        "open_kinds": base,
    }


def _render_open_stakes_for_prompt(stakes: list[Any]) -> str:
    """Ledger rows with ids so ops can target them — built directly from the
    Stake objects (never by re-splitting a rendered block, which misaligned
    ids on multi-line prose — review finding). Horizons are shown as REAL ISO
    dates so an update can re-emit them accurately, and each line is capped
    individually (a flat block slice hid tail stakes from the appraiser and
    made them unresolvable — review finding)."""
    if not stakes:
        return "(none yet)"
    from ai.stake_ledger import _voiced_note

    lines = []
    for s in stakes:
        horizon = ""
        if s.horizon_at is not None:
            horizon = f", horizon {datetime.fromtimestamp(s.horizon_at).strftime('%Y-%m-%d')}"
        prose = " ".join(str(s.stake or "").split())[:300]
        # Step 25: a voiced regret says so — never a second regret about
        # the same move, and what was said stays said.
        lines.append(f"[id {s.id}] ({s.kind}{horizon}) {prose}{_voiced_note(s)}")
    return "\n".join(lines)


async def _recent_conversation(owner_id: Any, surface: str) -> str:
    """A few trailing turns for pronoun grounding ('it passed!!' — which 'it'?).
    Best-effort; '' on any failure. Post-reply path, so the extra read is free."""
    try:
        from core.database import brain

        # No platform filter: the owner's recent turns (for pronoun grounding)
        # may be on any surface, and they all live under user_id=owner_id
        # (=palli_user_id). The old surface-guess fell through to the dead
        # 'local' surface for the common surface='' case (dispatch never passes
        # surface), so this read ALWAYS returned 0 rows and pronoun grounding
        # was permanently blank (2026-07-23). Dropping the platform filter
        # returns the owner's actual newest turns across surfaces (the
        # no-platform-filter pattern the old proactive fetchers proved).
        rows = await brain.get_conversation_history(
            user_id=int(owner_id), limit=8
        )
        lines = []
        for r in rows or []:
            role = str((r.get("role") if isinstance(r, dict) else getattr(r, "role", "")) or "")
            content = str(
                (r.get("content") if isinstance(r, dict) else getattr(r, "content", "")) or ""
            )[:240]
            if role in ("user", "assistant") and content:
                lines.append(f"{role}: {content}")
        return "\n".join(lines[-8:])
    except Exception as exc:
        logger.warning("stake_appraisal: recent-conversation fetch failed error=%s", exc)
        return ""


def _is_view_open(op: Any) -> bool:
    """An ``open`` of kind ``view``, read the way the ledger reads an op
    (stake_ledger._validate_op): trimmed, any case."""
    if not isinstance(op, dict):
        return False
    return (str(op.get("op") or "").strip().lower() == "open"
            and str(op.get("kind") or "").strip().lower() == "view")


def _drop_view_opens(ops: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    """The turn never forms a view, whatever the model emits and whichever
    way the opinions flag reads (Batch 6, ruling R25): every ``open`` of a
    view is taken out before the ledger sees the ops. Ops aimed at a view
    that already exists (update, mute, unmute) pass untouched.
    → (the ops that remain, how many were dropped)."""
    kept = [op for op in ops if not _is_view_open(op)]
    return kept, len(ops) - len(kept)


async def _close_linked_threads(closed_ids: list, owner_id: Any) -> None:
    """A resolved/faded stake must also retire its queued outreach thread, or
    PaLLi asks 'how did it land?' days after being told (review finding)."""
    if not closed_ids:
        return
    try:
        from core.database import brain
        from core.open_threads import ThreadSourceKind, ThreadStatus

        for sid in closed_ids:
            try:
                thread = await brain.get_open_thread_by_source_id(
                    owner_user_id=int(owner_id),
                    source_kind=ThreadSourceKind.TOPIC,
                    source_id=f"stake:{int(sid)}",
                )
                if thread is not None and getattr(thread, "resolved_at", None) is None:
                    await brain.close_open_thread(
                        thread_id=str(thread.id),
                        resolution_reason="stake closed by appraiser",
                        status=ThreadStatus.RESOLVED,
                    )
                    logger.info(
                        "stake_appraisal: closed outreach thread for stake id=%s", sid
                    )
            except Exception as exc:
                logger.warning(
                    "stake_appraisal: thread close failed stake_id=%s error=%s", sid, exc
                )
    except Exception as exc:
        logger.warning("stake_appraisal: thread reconciliation failed error=%s", exc)


async def appraise_exchange(
    *,
    user_message: str,
    palli_reply: str,
    channel_id: str = "",
    owner_id: Any = 1,
    surface: str = "",
    brain_client: Any = None,
) -> dict[str, Any]:
    """Appraise one delivered exchange against the ledger. Returns a summary
    dict ({"applied": n, ...}, {"skipped": reason} or {"error": ...}).

    Serialized via a module lock: snapshot -> LLM -> apply is not atomic and
    two overlapping appraisals would double-open the same stake."""
    started = time.time()
    try:
        from ai.stake_ledger import ledger_enabled, open_stakes, apply_ops, _max_open

        if brain_client is None and not ledger_enabled():
            return {"skipped": "companion.stake_ledger off"}
        if not str(user_message or "").strip() or not str(palli_reply or "").strip():
            return {"skipped": "empty exchange"}

        async with _APPRAISAL_LOCK:
            stakes = await open_stakes(limit=30, brain_client=brain_client)
            max_open = _max_open()

            from ai.llm_client import get_task_model, llm
            from ai.prompt_loader import prompt_loader

            recent = ""
            if brain_client is None:  # live path only; tests inject tmp brains
                recent = await _recent_conversation(owner_id, surface)

            template = prompt_loader.load_prompt("stake_appraisal")
            rendered = template.format(
                care_spine=_load_care_spine()[:1200],
                user_message=str(user_message)[:2000],
                palli_reply=str(palli_reply)[:2000],
                recent_conversation=(recent or "(no earlier context)")[:2000],
                open_stakes=_render_open_stakes_for_prompt(stakes),
                # Views are weightless against the shared cap (organ 4.13,
                # spec §6) — counting them would tell her the ledger is
                # fuller than it is and squeeze out a real hope.
                open_count=len([s for s in stakes if getattr(s, "kind", "") != "view"]),
                max_open=max_open,
                current_date=datetime.now().strftime("%A, %Y-%m-%d"),
                # ONE mapping: two ** unpackings sharing open_kinds is a
                # TypeError. View last — its union already folds regret's.
                **{**_regret_prompt_kwargs(), **_view_prompt_kwargs()},
            )
            model = get_task_model("appraisal")
            raw = await asyncio.wait_for(
                llm.chat_completion(
                    messages=[{"role": "user", "content": rendered}],
                    model=model,
                    max_tokens=700,
                    temperature=0.4,
                    log_context="stake_appraisal",
                ),
                timeout=APPRAISAL_TIMEOUT_SECONDS,
            )
            ops = _parse_ops(raw or "")
            if ops is None:
                logger.warning("stake_appraisal: unparseable LLM output; dropping appraisal")
                return {"error": "unparseable"}
            ops, dropped_view_opens = _drop_view_opens(ops)
            if dropped_view_opens:
                logger.info(
                    "stake_appraisal: dropped %d open op(s) of kind view — views form at "
                    "night, from the whole day, never in a turn", dropped_view_opens,
                )
            if not ops:
                logger.debug(
                    "stake_appraisal: no ledger movement (%.1fs)", time.time() - started
                )
                summary = {
                    "applied": 0, "rejected": 0, "opened": [], "closed": [],
                    "dormant": [],
                }
                if dropped_view_opens:
                    summary["dropped_view_opens"] = dropped_view_opens
                return summary

            summary = await apply_ops(
                ops, source_channel_id=channel_id, brain_client=brain_client
            )
            if dropped_view_opens:
                summary["dropped_view_opens"] = dropped_view_opens
        # Outside the lock: retire outreach threads for stakes that just
        # closed (live path only — the thread rail lives on the global brain).
        if brain_client is None:
            await _close_linked_threads(summary.get("closed") or [], owner_id)
        logger.info(
            "stake_appraisal: applied=%s rejected=%s opened=%s closed=%s (%.1fs)",
            summary.get("applied"),
            summary.get("rejected"),
            summary.get("opened"),
            summary.get("closed"),
            time.time() - started,
        )
        return summary
    except asyncio.TimeoutError:
        logger.warning(
            "stake_appraisal: timed out after %.0fs; skipping", APPRAISAL_TIMEOUT_SECONDS
        )
        return {"error": "timeout"}
    except asyncio.CancelledError:
        # Shutdown-time cancellation of the background task: bow out quietly.
        raise
    except Exception as exc:
        logger.warning("stake_appraisal: failed error=%s", exc)
        return {"error": str(exc)}


# ── Views at night (Batch 6, task 2, 2026-09-29; rulings R21–R24) ────────
#
# The nightly pass FORMS views and does nothing else. Who decides what
# (owner rule 07-16): the model says whether a view formed today, and
# writes it with its evidence; code keeps the budgets and the gates — at
# most one view a night, once per night, the ledger's own evidence gate
# ("view without evidence") and its own active and waiting caps.

VIEWS_DIR = Path("data/views")
_VIEWS_STATE_NAME = ".state.json"
NIGHT_VIEWS_SOURCE = "night_views"
_NIGHT_VIEWS_PROPOSALS_READ = 10
# Clears the provider's whole DEEP ladder (4 x 240 s + backoff = 970 s), the
# stake review's and the harvest's envelope; pinned by
# test_the_envelope_clears_the_whole_deep_ladder.
_NIGHT_VIEWS_TIMEOUT_S = 1000.0
_NIGHT_VIEWS_CORRECTIONS_BUDGET = 1200
_NIGHT_VIEW_TEXT_CAP = 400
_NIGHT_VIEW_EVIDENCE_CAP = 240
_NIGHT_VIEWS_READ_LIMIT = 60
_DAY_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
_SLOT_RE = re.compile(r"<<([A-Z_]+)>>")
_NO_VIEWS_YET = "(none yet)"
_NOTHING_CORRECTED = "(nothing has been corrected)"
# The refusal named when the night's view is one she already has (_twin_of).
# The night's health line reads it as the rails working, not a failure
# (ai/night_cycle.py, _surface_errors).
ALREADY_HERS = "already one of her views"


class _ViewAnswerRefused(ValueError):
    """The model answered outside the contract. Nothing is written — no
    row and no trace — so the night reads as "never ran", and the health
    line names the reason."""


def _views_state_path() -> Path:
    """Call-time resolution, so a redirect of VIEWS_DIR takes effect."""
    return VIEWS_DIR / _VIEWS_STATE_NAME


def _read_views_state() -> dict[str, Any]:
    """The last night's trace, or {} when there is no readable one."""
    try:
        state = json.loads(_views_state_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return state if isinstance(state, dict) else {}


def _write_views_state(day: str, *, formed: int, now: float) -> None:
    path = _views_state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(
        json.dumps({"last_night": day, "formed": int(formed), "at": float(now)}) + "\n",
        encoding="utf-8", newline="\n")
    os.replace(tmp, path)


def _render_standing_views(open_views: list[Any], waiting: list[Any]) -> str:
    """The views she already has, so the pass forms none of them twice (the
    ledger does not know one view from its twin). No ids: nothing here can
    be aimed at. Her words and her evidence, one view a line."""
    lines = []
    for s, is_waiting in [(v, False) for v in open_views] + [(v, True) for v in waiting]:
        text = " ".join(str(getattr(s, "stake", "") or "").split())[:_NIGHT_VIEW_TEXT_CAP]
        if not text:
            continue
        line = f"- {text}"
        evidence = " ".join(str(getattr(s, "evidence", "") or "").split())
        if evidence:
            line += f" (evidence: {evidence[:_NIGHT_VIEW_EVIDENCE_CAP]})"
        if is_waiting:
            line += " (waiting)"
        elif getattr(s, "muted_at", None) is not None:
            line += " (they asked you to leave this one alone)"
        lines.append(line)
    return "\n".join(lines) or _NO_VIEWS_YET


def _render_corrections_for_views() -> str:
    """The corrections of record, whether or not regret is on (ruling R22):
    "nothing the corrections have settled" needs something to read. Never
    raises; an unreadable ledger reads as none."""
    try:
        from ai.corrections_store import render_corrections_block

        block = render_corrections_block(budget=_NIGHT_VIEWS_CORRECTIONS_BUDGET, sync=False)
    except Exception as exc:
        logger.warning("night_views: corrections unreadable: %s", exc or type(exc).__name__)
        block = ""
    return block or _NOTHING_CORRECTED


def _fill_slots(template: str, values: dict[str, str]) -> str:
    """Every ``<<NAME>>`` slot in ONE pass: a slot name typed inside the
    day's conversation, or inside a view, is never read as a slot."""
    return _SLOT_RE.sub(lambda m: values.get(m.group(1), m.group(0)), template)


def _parse_night_views(raw: Any) -> list[dict[str, str]]:
    """Her answer → the views she sent, in her order ([] is the honest
    "no view today"). Raises _ViewAnswerRefused outside the contract.

    The contract is one view or null; a list, under ``view`` or ``views``,
    is read as far as _NIGHT_VIEWS_PROPOSALS_READ so the one-a-night cap
    has something to cap. A view is her words: a stake that is not text is
    never made into text, and evidence that is not text is no evidence."""
    from core.llm_json import safe_parse_json_object

    obj = safe_parse_json_object(raw if isinstance(raw, str) else "")
    if not isinstance(obj, dict) or not ("view" in obj or "views" in obj):
        raise _ViewAnswerRefused("unparseable")
    sent = obj["view"] if "view" in obj else obj["views"]
    if sent is None or sent == []:
        return []
    if isinstance(sent, dict):
        sent = [sent]
    if not isinstance(sent, list):
        raise _ViewAnswerRefused("malformed_view")
    views: list[dict[str, str]] = []
    for item in sent[:_NIGHT_VIEWS_PROPOSALS_READ]:
        if not isinstance(item, dict):
            continue
        stake = item.get("stake")
        if not isinstance(stake, str) or not stake.strip():
            continue
        evidence = item.get("evidence")
        views.append({"stake": stake, "evidence": evidence if isinstance(evidence, str) else ""})
    if not views:
        raise _ViewAnswerRefused("malformed_view")
    return views


def _twin_of(op: dict[str, Any], standing: list[Any]) -> Any:
    """The standing view (open, muted or waiting) whose words are the
    proposal's, or None. Exact match only, after folding case, punctuation
    and spacing the way the shared-language harvest folds a bit
    (callbacks_store.normalize): what two views MEAN is the model's to
    judge, and the prompt asks it to. The ledger does not know one view
    from its twin, so without this the same view could stand twice."""
    from ai.callbacks_store import normalize

    key = normalize(op.get("stake") or "")
    if not key:
        return None
    return next((s for s in standing
                 if normalize(getattr(s, "stake", "") or "") == key), None)


def _waiting_list_is_full(open_views: list[Any], waiting: list[Any]) -> bool:
    """True when the ledger could keep no new view at all: the board is at
    its cap and so is the waiting list (0 there means no limit)."""
    from ai import stake_ledger

    if len(open_views) < stake_ledger._views_max_active():
        return False
    cap = stake_ledger._views_dormant_cap()
    return cap > 0 and len(waiting) >= cap


async def appraise_views(
    date: str,
    *,
    llm_call: Callable[[str], Awaitable[str]],
    now: float,
    day_conversation: str,
    brain_client: Any = None,
) -> dict[str, Any]:
    """The nightly views pass: look over the whole of ``date`` and form at
    most ONE new view. Never raises (a cancel goes through).

    ``date`` is the day looked back on, and the date the prompt shows —
    never the clock's. ``day_conversation`` is the day as the night cycle
    renders it (ai/night_cycle.py, ``HH:MM  them:`` / ``HH:MM  me:``).

    The proposal becomes an ``open`` op of kind ``view`` and goes through
    stake_ledger.apply_ops, so the ledger's evidence gate and caps are the
    gate. Before it, one code rail the ledger lacks: a view in the same
    words as one she already has (open, muted or waiting) is refused by
    name (_twin_of). When the pass completes — a view formed or not — it writes
    ``data/views/.state.json``; a night already recorded there is not run
    again (a failed reflection re-runs the whole night at the next boot,
    and the ledger does not know one view from its twin). A failed call
    and an answer outside the contract write nothing.
    """
    from ai import stake_ledger

    if not (stake_ledger.ledger_enabled() and stake_ledger.opinions_enabled()):
        return {"skipped": "flag_off"}
    day = str(date or "")
    if not _DAY_RE.fullmatch(day):
        return {"error": "bad_date"}
    try:
        shown_date = datetime.strptime(day, "%Y-%m-%d").strftime("%A, %Y-%m-%d")
    except ValueError:
        return {"error": "bad_date"}
    if _read_views_state().get("last_night") == day:
        return {"skipped": "already_ran", "day": day}

    result: dict[str, Any] = {"day": day, "formed": 0}
    try:
        conversation = str(day_conversation or "").strip()
        rows = await stake_ledger.open_stakes(
            limit=_NIGHT_VIEWS_READ_LIMIT, brain_client=brain_client)
        open_views = [s for s in rows if getattr(s, "kind", "") == "view"]
        waiting = await stake_ledger.dormant_views(
            limit=_NIGHT_VIEWS_READ_LIMIT, brain_client=brain_client)
        if not conversation:
            result["skipped"] = "no_conversation"
        elif _waiting_list_is_full(open_views, waiting):
            result["skipped"] = "cap_full"
        else:
            from ai.prompt_loader import prompt_loader

            template = prompt_loader.load_prompt("night_cycle_views")
            if not template:
                raise RuntimeError("night_cycle_views prompt missing")
            prompt = _fill_slots(template, {
                "CURRENT_DATE": shown_date,
                "DAY_CONVERSATION": conversation,
                "STANDING_VIEWS": _render_standing_views(open_views, waiting),
                "CORRECTIONS": _render_corrections_for_views(),
            })
            raw = await asyncio.wait_for(llm_call(prompt), timeout=_NIGHT_VIEWS_TIMEOUT_S)
            sent = _parse_night_views(raw)
            if sent:
                ops = [{"op": "open", "kind": "view", "stake": v["stake"],
                        "evidence": v["evidence"], "horizon_at": None} for v in sent]
                standing = open_views + waiting
                # ONE a night: the first the ledger can keep that she does
                # not already have; when there is none, the first, so its
                # refusal is named.
                op = next((o for o in ops if stake_ledger._validate_op(o) is None
                           and _twin_of(o, standing) is None), ops[0])
                # A view the ledger would refuse anyway is still the
                # ledger's to refuse (its gate, its log line).
                twin = (_twin_of(op, standing)
                        if stake_ledger._validate_op(op) is None else None)
                if twin is not None:
                    result["refused"] = ALREADY_HERS
                    logger.info("night_views: %s — view refused: %s (the same words as #%s)",
                                day, ALREADY_HERS, getattr(twin, "id", "?"))
                else:
                    summary = await stake_ledger.apply_ops(
                        [op], source_channel_id=NIGHT_VIEWS_SOURCE, brain_client=brain_client)
                    opened = [int(i) for i in summary.get("opened") or []]
                    result["formed"] = len(opened)
                    if opened:
                        result["opened"] = opened
                        result["dormant"] = [int(i) for i in summary.get("dormant") or []]
                    else:
                        result["refused"] = stake_ledger._validate_op(op) or "ledger_refused"
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        # The type name is the fallback: a timeout's str() is "", and an
        # empty error reads as a healthy night on the health line.
        why = (" ".join(str(exc).split()) or type(exc).__name__)[:160]
        logger.warning("night_views: the pass failed for %s: %s", day, why)
        return {"day": day, "error": why}

    try:
        _write_views_state(day, formed=result["formed"], now=float(now))
    except OSError as exc:
        logger.warning("night_views: could not write the night's record for %s: %s", day, exc)
        result["error"] = f"state_unwritable: {' '.join(str(exc).split())}"[:160]
    if result["formed"]:
        essential_logger.info(
            "night_views: %s — formed=%d id=%s%s", day, result["formed"],
            ",".join(str(i) for i in result["opened"]),
            " (waiting)" if result.get("dormant") else "")
    else:
        why = result.get("refused") or result.get("skipped")
        essential_logger.info(
            "night_views: %s — no view formed today%s", day, f" ({why})" if why else "")
    return result


async def night_views_llm_call(prompt: str) -> str:
    """The real dependency the night cycle injects: the appraisal task
    model, on a lane of its own (DEEP by its name)."""
    from ai.llm_client import get_task_model, llm

    return await llm.chat_completion(
        messages=[{"role": "user", "content": prompt}],
        model=get_task_model("appraisal"),
        # Room to think as well as to answer (the stake review's number):
        # this lane thinks over a whole day, and the answer is short.
        max_tokens=8000,
        temperature=0.4,
        log_context="night_cycle_views",
    )
