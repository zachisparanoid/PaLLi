"""The one judged LLM call for a mid-task message (spec §3, §4c).

Routing is mechanical; responding is judged. This module makes the single call
that decides BOTH what she says right now (or that she holds) and whether the
message means stop. It never touches the queue, never sends anything, and never
raises: on any failure it fails open to "keep working, say nothing", so a
provider hiccup can neither kill her work nor lose his words.
"""
from __future__ import annotations


import asyncio
import logging
import os
from dataclasses import dataclass


logger = logging.getLogger(__name__)

# Batch 7, her voice (4.5): both mid-task judges open on her voice page, not on
# a bare stand-in. The page is the `voice_identity` core doc, resolved by the
# identity store, and only a page she wrote herself (identity/palli/) counts:
# when the store falls back to the creator's copy in identity/core/ (a fresh
# install, before her first page), the reader returns "" (ruling F1, the final
# review's I1: that copy is the creator's, and its words are not hers). With
# no page of her own, the judges open on this neutral line.
_IDENTITY_FALLBACK = "I'm PaLLi."
_voice_page_cache: dict = {"key": None, "text": ""}


def voice_identity_text() -> str:
    """Her own voice page for the mid-task judges: the body only (frontmatter
    stripped), cached on (path, mtime) so a rewrite of the page is read on the
    next call. "" when the store resolves to the creator's copy in
    identity/core/ (compared as resolved paths, so a link to that copy is
    still that copy). Never raises: any problem returns ""."""
    try:
        from core.identity.constants import CORE_DOCS
        from core.identity.frontmatter import parse_frontmatter
        from core.identity.service import identity_service

        store = identity_service.document_store
        path = store.core_doc_path("voice_identity")
        creators_copy = os.path.join(store.core_root, CORE_DOCS["voice_identity"])
        if os.path.realpath(path) == os.path.realpath(creators_copy):
            return ""
        key = (path, os.stat(path).st_mtime)
        if _voice_page_cache["key"] == key:
            return _voice_page_cache["text"]
        with open(path, "r", encoding="utf-8") as handle:
            _frontmatter, body = parse_frontmatter(handle.read())
        text = body.strip()
        _voice_page_cache["key"], _voice_page_cache["text"] = key, text
        return text
    except Exception as exc:
        logger.debug("steering_reply: voice page unreadable, judges use the neutral line: %s", exc)
        return ""


# 25 → 60 (2026-08-04): same retry-envelope repair as the appraiser. This
# judge burns its whole 700-token budget on chain-of-thought often enough to
# matter (it did at 13:35 on 08-04, returning empty), and the budget-retry
# that now rescues it costs a second provider request — two requests against a
# 30s per-request ceiling means 60s is the floor for the retry to survive.
# Affordable here because phase-owned routing (2026-08-04) confines this judge
# to genuine work runs, which last minutes, and the run-end sweep answers
# anything it never gets to.
_TIMEOUT_SECONDS = 60.0
_MAX_TOKENS = 700


@dataclass(frozen=True)
class SteerJudgment:
    stop_run: bool = False
    reply: str | None = None
    # 2026-08-04: "this deserves a real turn" — send the holding reply now,
    # and the run-end sweep dispatches a full turn for it. Closes the mode
    # where an identity-significant message got closed out by a one-liner
    # (the 15:48 flag-trap correction incident).
    full_turn: bool = False
    # Step 27 Phase B (spec §3): what his words mean for the work —
    # fold_in (today's behavior) | pause | stop | new_task | resume
    # (resume only when a paused run exists for the channel).
    action: str = "fold_in"


_ACTIONS = {"fold_in", "pause", "stop", "new_task", "resume"}


async def judge_mid_task(
    turn,
    *,
    session_key: str,
    recent_text: str = "",
    identity_text: str = "",
    paused_doing: str = "",
) -> SteerJudgment:
    """Judge one coalesced mid-task bundle. Never raises."""
    from core.agent import steering

    message = str(getattr(turn, "content", "") or "").strip()
    if not message:
        return SteerJudgment()

    try:
        from ai.llm_client import llm
        from ai.llm_routing import get_task_model
        from ai.prompt_loader import prompt_loader
        from core.llm_json import safe_parse_json_object

        template = prompt_loader.load_prompt("steering_judgment")
        if not template:
            logger.warning(
                "steering: judgment prompt missing; failing open to steer",
            )
            return SteerJudgment()
        rendered = template.format(
            identity=identity_text or voice_identity_text() or _IDENTITY_FALLBACK,
            now_doing=(
                f"(PAUSED — waiting on them) {paused_doing}" if paused_doing
                else (steering.get_now_doing(session_key) or "(work in progress)")
            ),
            recent=recent_text or steering.get_recent(session_key) or "(nothing recent)",
            message=message,
        )
        raw = await asyncio.wait_for(
            llm.chat_completion(
                [{"role": "system", "content": rendered}],
                model=get_task_model(
                    "steering_judgment", fallback=get_task_model("response"),
                ),
                temperature=0.7,
                max_tokens=_MAX_TOKENS,
                log_context="steering_judgment",
            ),
            timeout=_TIMEOUT_SECONDS,
        )
        parsed = safe_parse_json_object(raw or "") or {}
    except Exception as exc:  # provider error, timeout, prompt/render problem
        logger.warning(
            "steering: judgment failed for %s (%s); failing open to steer",
            session_key, exc,
        )
        return SteerJudgment()

    reply = parsed.get("reply")
    if not isinstance(reply, str) or not reply.strip():
        reply = None
    action = str(parsed.get("action") or "").strip().lower()
    if action not in _ACTIONS:
        action = "fold_in"
    if action == "resume" and not paused_doing:
        action = "fold_in"
    if bool(parsed.get("stop_run")) and action == "fold_in":
        action = "stop"                       # the flag-off / older-output shape
    # With duplex ON, full_turn means "her full self answers beside the work"
    # and the prompt asks for reply null — no holding line — so it stands on
    # its own. Off: today's rule — full_turn without a holding reply degrades
    # to a plain hold (the sweep answers it anyway; never escalate silently).
    from core.agent import duplex as _dx
    full_turn = bool(parsed.get("full_turn")) and (reply is not None or _dx.duplex_enabled())
    return SteerJudgment(
        stop_run=(action == "stop"),
        reply=reply,
        full_turn=full_turn,
        action=action,
    )

_SUPERSEDE_TIMEOUT_SECONDS = 15.0


async def judge_supersede(turn, *, session_key: str, identity_text: str = "") -> bool:
    """Mid-generation fork (phase-owned routing, 2026-08-04): does the new
    message change what she should be saying right now? True = cancel and
    regenerate; False = let the reply finish and answer in a follow-up
    turn. Never raises; every failure path returns False (fail-open —
    a judgment failure must never cost a completion)."""
    from core.agent import steering

    message = str(getattr(turn, "content", "") or "").strip()
    if not message:
        return False
    try:
        from ai.llm_client import llm
        from ai.llm_routing import get_task_model
        from ai.prompt_loader import prompt_loader
        from core.llm_json import safe_parse_json_object

        template = prompt_loader.load_prompt("steering_supersede")
        if not template:
            logger.warning(
                "steering: supersede prompt missing; failing open to finish",
            )
            return False
        rendered = template.format(
            identity=identity_text or voice_identity_text() or _IDENTITY_FALLBACK,
            answering=steering.get_now_doing(session_key)
            or "(your reply in progress)",
            message=message,
        )
        raw = await asyncio.wait_for(
            llm.chat_completion(
                [{"role": "system", "content": rendered}],
                model=get_task_model(
                    "steering_supersede",
                    fallback=get_task_model(
                        "steering_judgment", fallback=get_task_model("response"),
                    ),
                ),
                temperature=0.3,
                max_tokens=_MAX_TOKENS,
                log_context="steering_supersede",
            ),
            timeout=_SUPERSEDE_TIMEOUT_SECONDS,
        )
        parsed = safe_parse_json_object(raw or "") or {}
    except Exception as exc:  # provider error, timeout, prompt/render problem
        logger.warning(
            "steering: supersede judgment failed for %s (%s); failing open "
            "to finish", session_key, exc,
        )
        return False
    return bool(parsed.get("supersede"))
