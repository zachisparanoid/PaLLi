"""The nightly picture of him (item 6 of the 08-18 program, 2026-08-19).

Why. Two owner documents render into every reply and had never been
real: MEMORY_SUMMARY.md ("memory_summary_text: - Interests: No explicit
interest list yet. - Preferred name if explicitly confirmed: Local
Operator …") and RELATIONSHIP_STATE.md ("Trusted companion baseline. I'm
warm, capable, and quietly reliable with them." — the factory template,
version 4). The multi-owner design meant them as her distilled picture of
the person; nothing ever maintained them. Placeholder text in the prompt
is misinformation she reads forty times a day.

What. One call at the night cycle, after the memory sweep, in her voice:
from her (reviewed) MEMORY.md, tonight's reflection and her last read,
she redraws the picture — memory_summary (≤900), relationship_summary
(≤300), current_posture, interests, preferred_name — and it is written
through the identity store as the system actor (versioned, like reflect
and the sweep). Never raises; empty/unparseable changes nothing; nothing
not in the memory is allowed in (the prompt's law). The personality
profile is HIS doc (user-owned) and is not touched here.
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime
from typing import Any, Awaitable, Callable, Dict, List, Optional

from core.identity.service import identity_service

logger = logging.getLogger(__name__)
essential_logger = logging.getLogger("palli.essential")

_TIMEOUT_S = 1000.0   # clears the provider's whole DEEP ladder (4×240 s + backoff = 970); pinned ≥ deep_ladder_ceiling_seconds
_SUMMARY_CAP = 900
_RELATIONSHIP_CAP = 300
_POSTURE_CAP = 40
_INTERESTS_MAX = 12


def _clean(text: Any, cap: int) -> str:
    return " ".join(str(text or "").split())[:cap]


def _body_section(content: str, heading: str) -> List[str]:
    out: List[str] = []
    on = False
    for ln in (content or "").splitlines():
        if ln.startswith("## "):
            on = ln[3:].strip() == heading
            continue
        if on and ln.strip().startswith("-"):
            out.append(ln.strip())
    return out


def _memory_body_for_prompt(content: str, cap: int = 12_000) -> str:
    body = (content or "").strip()
    return body if len(body) <= cap else body[:cap].rstrip() + "\n…"


def picture_enabled() -> bool:
    """THE gate (companion.owner_picture; ships False in the schema, the
    owner's yaml flips it on) — flag-off = the docs are never touched."""
    try:
        from core.turn.companion_flags import companion_settings

        comp = companion_settings()
        return comp is not None and bool(getattr(comp, "owner_picture", False))
    except Exception:
        return False


async def run_nightly_owner_picture(
    *,
    owner_id: int,
    day_reflection: str,
    llm_call: Callable[[str], Awaitable[str]],
    now: Optional[float] = None,
) -> Dict[str, Any]:
    if not picture_enabled():
        return {"skipped": "flag_off"}
    now = float(now if now is not None else time.time())
    try:
        bundle = identity_service.compile_for_owner(owner_id=owner_id)
        owner_docs = getattr(bundle, "owner", None) or {}
        memory_doc = owner_docs.get("memory")
        if memory_doc is None or not (memory_doc.content or "").strip():
            return {"skipped": "no_memory_doc"}
        summary_doc = owner_docs.get("memory_summary")
        rel_doc = owner_docs.get("relationship_state")
        profile = owner_docs.get("personality_profile")
        display_name = _clean(
            (getattr(profile, "frontmatter", {}) or {}).get("display_name") if profile is not None else "", 60
        ) or "the owner"
        rel_fm = (getattr(rel_doc, "frontmatter", {}) or {}) if rel_doc is not None else {}

        from ai.prompt_loader import prompt_loader

        template = prompt_loader.load_prompt("owner_picture") or ""
        if not template:
            return {"error": "prompt_missing"}
        prompt = (
            template
            .replace("<<DISPLAY_NAME>>", display_name)
            .replace("<<MEMORY_BODY>>", _memory_body_for_prompt(memory_doc.content))
            .replace("<<DAY_REFLECTION>>", (day_reflection or "").strip() or "(no note tonight)")
            .replace("<<RELATIONSHIP_SUMMARY>>", _clean(rel_fm.get("relationship_summary"), 400) or "(nothing yet)")
            .replace("<<CURRENT_POSTURE>>", _clean(rel_fm.get("current_posture"), 60) or "(unset)")
            .replace("<<CURRENT_DATE>>", datetime.fromtimestamp(now).strftime("%A, %Y-%m-%d"))
        )
        raw = await asyncio.wait_for(llm_call(prompt), timeout=_TIMEOUT_S)
        if not (raw or "").strip():
            return {"error": "no_content"}  # the provider gave up, not her JSON
        from core.llm_json import safe_parse_json_object

        data = safe_parse_json_object(raw or "")
        if not isinstance(data, dict):
            return {"error": "unparseable"}
        summary = _clean(data.get("memory_summary"), _SUMMARY_CAP)
        relationship = _clean(data.get("relationship_summary"), _RELATIONSHIP_CAP)
        posture = _clean(data.get("current_posture"), _POSTURE_CAP)
        raw_interests = data.get("interests") or []
        interests = [_clean(i, 60) for i in raw_interests if _clean(i, 60)][:_INTERESTS_MAX] if isinstance(raw_interests, list) else []
        preferred = _clean(data.get("preferred_name"), 40)
        if not summary:
            return {"error": "empty_summary"}

        wrote: List[str] = []
        from core.identity.frontmatter import dump_frontmatter

        if summary_doc is not None:
            fm = dict(getattr(summary_doc, "frontmatter", {}) or {})
            fm["memory_summary_text"] = summary
            fm["picture_refreshed_at"] = datetime.fromtimestamp(now).strftime("%Y-%m-%d")
            body_lines = [
                "# Memory Summary",
                "",
                "My distilled picture of them — redrawn every night from my memory of them, "
                "the day's reflection and where I last stood with them. The picture itself is "
                "the summary line above.",
                "",
            ]
            if interests:
                body_lines.append("What they're into right now: " + ", ".join(interests) + ".")
            if preferred:
                body_lines.append(f"They like to be called {preferred}.")
            await identity_service.update_raw_document(
                user_id=owner_id, platform="owner", doc_key="memory_summary",
                markdown=dump_frontmatter(fm, "\n".join(body_lines).rstrip() + "\n"),
                actor_type="system", actor_id="owner_picture",
                reason="nightly_owner_picture", identity_owner_id=owner_id,
            )
            wrote.append("memory_summary")
        if rel_doc is not None and (relationship or posture):
            updates: Dict[str, Any] = {}
            if relationship:
                updates["relationship_summary"] = relationship
            if posture:
                updates["current_posture"] = posture
            await identity_service.silently_maintain_document(
                user_id=owner_id, platform="owner", doc_key="relationship_state",
                updates=updates, actor_id="owner_picture",
                reason="nightly_owner_picture", identity_owner_id=owner_id,
            )
            wrote.append("relationship_state")
        essential_logger.info("owner_picture: redrawn (%s) — %d chars", ", ".join(wrote), len(summary))
        return {"wrote": wrote, "chars": len(summary)}
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.warning("owner_picture: failed: %s", exc)
        return {"error": " ".join(str(exc).split())[:160] or type(exc).__name__}


async def owner_picture_llm_call(prompt: str) -> str:
    """The real dependency the night cycle injects (the appraisal lane; the
    D1 budget — thinking models burn reasoning tokens)."""
    from ai.llm_client import get_task_model, llm

    return await llm.chat_completion(
        messages=[{"role": "user", "content": prompt}],
        model=get_task_model("appraisal"),
        max_tokens=8000,   # 08-22: the author-sized budget; deep doubling ladder above it
        temperature=0.4,
        log_context="owner_picture",
    )
