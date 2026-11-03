from __future__ import annotations

import asyncio
import logging
import os
import sys
from contextvars import ContextVar
from dataclasses import replace as _dc_replace
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Literal
from core.config import config  # 2026-09-02: the runner-model / call-kwargs helpers read it at module scope

if TYPE_CHECKING:  # the annotation on _reply_client_timeout; httpx loads lazily
    import httpx


logger = logging.getLogger(__name__)


_VALID_MODES = {"react"}  # tier pipeline removed 2026-07-22

# Bug 1 fix (2026-07-04): hold strong references to fire-and-forget user-side
# persist tasks so the event loop can't garbage-collect them before they write.
# These are scheduled BEFORE the cancellable run_turn so a barge-in / typing
# cancel (bots/discord/client.py) can never drop the user's message. Each task
# removes itself via add_done_callback when it finishes.
_BACKGROUND_PERSIST_TASKS: set[asyncio.Task] = set()


def _resolve_loop_mode() -> Literal["pipeline", "react"]:
    # Default is REACT — the live brain. The tier pipeline survives only as an
    # explicit opt-in (PALLI_AGENT_LOOP=pipeline); before 2026-07-08 the default
    # was pipeline, so losing one .env.local line silently swapped brains.
    raw = (os.environ.get("PALLI_AGENT_LOOP") or "react").lower()
    if raw not in _VALID_MODES:
        logger.warning("PALLI_AGENT_LOOP=%r unrecognized; falling back to react", raw)
        return "react"
    return raw  # type: ignore[return-value]


def _is_workbench_turn(turn: Any) -> bool:
    """Her own work session, not a conversation with him (own-work §4b).

    The seam this predicate guards is wider than the plan assumed. Beyond
    channel delivery — which needs no guard, since the workbench engine
    discards the TurnResult — the spine would otherwise persist her private
    sign-off into `conversations` (that write is not origin-gated) and fire
    reflection, whose open-threads output is a rail into proactive outreach
    outside the single deliberate SHARE line §5 calls the only door. Spec
    §6b#1 defers her work entering the nightly metabolism "to keep v1's
    surface honest"; this is where that deferral is enforced.
    """
    return str(getattr(turn, "surface", "") or "") == "workbench"


def _is_late_digest_turn(turn: Any) -> bool:
    """A reminder fire that carries every reminder which came due while she
    was down (Batch 4 task 2). Each of those rows is marked fired once the
    message is sent, so the message must name all of them — and the
    reply-length governor, which compresses to 50/110 words, could drop
    lines. Only the reminder fire path sets the flag."""
    if str(getattr(turn, "surface", "") or "") != "reminder_fire":
        return False
    extra = getattr(getattr(turn, "metadata", None), "extra", None)
    return isinstance(extra, dict) and bool(extra.get("late_digest"))


# 2026-08-11: the reply lane's terminal failure used to be SILENCE — the
# provider timed out 4x on his 10:13 message and the turn ended with
# responded=False, nothing sent, nothing said. For a companion, silent
# failure to a waiting human is the worst shape. When the loop dies on a
# human-origin turn, she now says so honestly instead. Canned by design:
# the failure path must not depend on the thing that just failed.
_PROVIDER_OUTAGE_LINE = (
    "my brain's provider just timed out on me mid-thought — give me a "
    "minute and send that again?"
)
_HUMAN_ORIGINS = ("user", "dashboard_user")


def _apply_outage_fallback(run_result: Any, origin: str) -> Any:
    """Swap error-silence for the honest outage line — ONLY when a human is
    waiting (user/dashboard origins) and the turn FAILED (error set, nothing
    said). Chosen silence (responded False, no error) stays silence; reminder
    turns keep their dead-letter retry semantics; her own workbench sessions
    and monologue ticks have nobody waiting."""
    try:
        if (
            origin in _HUMAN_ORIGINS
            and not bool(getattr(run_result, "responded", False))
            and str(getattr(run_result, "error", "") or "").strip()
            and not str(getattr(run_result, "response_text", "") or "").strip()
        ):
            return _dc_replace(
                run_result, responded=True, response_text=_PROVIDER_OUTAGE_LINE
            )
    except Exception:  # never let the fallback break the turn it saves
        pass
    return run_result


def _put_back_for_owner(run_result: Any, captured_chunks: list[str], turn: Any) -> Any:
    """A reply to the OWNER carries the real values back (a key she filed, a
    number she looked up); a GUEST's never does — the token stays, even when
    the model echoed one (Batch 1, 2026-09-26; ruling R3).

    Runs right after ``run_turn`` returns, before the history row is
    persisted and before the cadenced beats are built from
    ``captured_chunks`` — so the row, the beats and the TurnResult all carry
    the same text. ``captured_chunks`` is rewritten IN PLACE (the caller's
    list); ``RunResult`` is frozen, so a new one is returned. Tool results
    are never touched here or anywhere: put-back is tool INPUT (tools.py)
    and this reply, nothing else.
    """
    from core.agent.payload_masking import unmask_text
    from core.owner_gate import is_guest_turn

    if is_guest_turn(turn):
        return run_result
    try:
        for i, chunk in enumerate(captured_chunks):
            if isinstance(chunk, str):
                captured_chunks[i] = unmask_text(chunk)
        text = getattr(run_result, "response_text", None)
        if isinstance(text, str):
            restored = unmask_text(text)
            if restored != text:
                run_result = _dc_replace(run_result, response_text=restored)
    except Exception as exc:  # never let the put-back break the turn; tokens are the safe side
        logger.warning("dispatch react: owner put-back skipped: %s", type(exc).__name__)
    return run_result


def _pm_notice_allowed(turn: Any) -> bool:
    """Workbench sessions carry their own sense (own_work_moved, emitted by
    the engine at session end); the generic conversation senses would
    mislabel her work as replies (spec §4b sense-dedup)."""
    return not _is_workbench_turn(turn)


def _workbench_round_cap(turn: Any) -> int | None:
    """Her per-session round budget (spec §4e own_work_session_rounds).

    None for every other turn, which leaves the env-resolved global default
    exactly as it was — the two runs coexist by design, so this ceiling can
    never be process-wide.

    0 = UNLIMITED (owner's 2026-08-03 call, deviating from the parked
    zero-means-unlimited plan's ledger). sys.maxsize rather than None
    because the runner resolves ``deps.max_iterations or <global default>``
    — a falsy sentinel would silently become the global 50. Loop detection,
    the wall clock (when nonzero), and stop remain the backstops.
    """
    if not _is_workbench_turn(turn):
        return None
    # The forge (step 34): a forge drafting session rides the wall but is
    # HIS ask's build, not her own-work sitting — it takes the forge's own
    # code budget (FORGE_SESSION_MAX_ROUNDS), never the own_work_session_
    # rounds knob, so tuning her sittings can't strangle a forge (or the
    # reverse).
    md = getattr(turn, "metadata", None)
    if md is not None and str(getattr(md, "origin", "") or "") == "forge":
        try:
            from core.config import config as _config

            return max(1, int(_config.FORGE_SESSION_MAX_ROUNDS))
        except Exception:
            return 40
    try:
        from core.turn.companion_flags import companion_settings

        comp = companion_settings()
        raw = getattr(comp, "own_work_session_rounds", None)
        rounds = 8 if raw is None else int(raw)
        return rounds if rounds > 0 else sys.maxsize
    except Exception:
        return None


async def dispatch_turn(turn: Any) -> Any:
    """Route a Turn through either the existing pipeline or the new ReAct loop.

    Surfaces (Discord, voice, dashboard) call this instead of process_turn.
    Reads PALLI_AGENT_LOOP at call time (not module load) so tests and
    integration suites can flip the mode without restarting the process.
    Production sets the env var once and restarts.
    """
    # Setup mode (first door, task 1, 2026-09-17): she booted with no brain
    # key. Both chat doors are shut and every lane is idle, but a turn can
    # still arrive — the dashboard's chat panel is open by design. It gets the
    # one honest sentence. ABOVE the mode split and everything below it: no
    # provider, no owner resolution, no recall, nothing persisted, nothing
    # reflected on — there is no brain to have had the exchange with. Canned
    # for the reason the outage line is: the failure path must not depend on
    # the thing that is missing. Read fresh every turn, so the first message
    # after the setup API saves a key is a real turn, no restart.
    from core.setup_mode import brain_ready, no_brain_sentence

    if not brain_ready():
        from core.turn.types import TurnResult

        logger.info("setup mode: a turn arrived with no brain — answered with the fixed sentence")
        return TurnResult(
            responded=True,
            response_text=no_brain_sentence(),
            decision=None,
            reflection_applied=False,
            delegation_tier_used=0,
        )

    mode = _resolve_loop_mode()
    if mode == "react":
        from pathlib import Path

        from core.agent import first_look as _first_look
        from core.agent.builtin_tools import build_default_registry, build_guest_registry
        from core.agent.runner import RunnerDeps, run_turn
        from core.turn.persistence import (
            _expand_content_with_attachments,
            _persist_assistant_side_to_history,
            _persist_user_side_with_content,
        )
        from core.turn.recall import _platform_for_surface, build_turn_context, fetch_recent_history
        from core.turn.reflect import apply_reflection
        from core.turn.types import TurnResult

        # Build the cadence_llm callback by wrapping the existing OpenAI-compat client
        cadence_llm = await _build_cadence_llm_callable()
        provider = "openai_compat"  # default; Anthropic path can override here later

        # D.3 wave 2 step 2: resolve is_owner against the local-mode
        # owner so owner-only tools (local_shell at builtin_tools/
        # local_shell.py:21) are correctly gated for ReAct mode. Pre-fix,
        # runner.py read is_owner via getattr default True for everyone —
        # any surface (Discord guild channel, voice with non-owner
        # speaker) could invoke owner-only tools. Best-effort: if the
        # resolver fails (DB down, owner not yet provisioned), default
        # to False so the security posture stays restrictive on error.
        _owner_refs_read.set(None)
        is_owner_flag = await _resolve_is_owner_for_turn(turn)
        # The owner gate (item 7a, 2026-08-19): a GUEST turn — a non-owner
        # Discord author or voice speaker — gets a plain reply: no tool
        # registry at all, no reflection, never owner. The surfaces set
        # origin="guest"; the context adapter renders none of his bands.
        from core.owner_gate import is_guest_turn as _is_guest_turn
        _guest = _is_guest_turn(turn)
        if _guest:
            is_owner_flag = False
        # Step 2, part 5 (add-on b): the owner's account ids, for the section
        # that says who the owner is on each app. Owner's turns only. The ids
        # the owner check just read (Fable's checkpoint, 4: no second read);
        # a stand-in check that read none (the tests' seam) means a read here.
        _owner_refs = None
        if is_owner_flag:
            _owner_refs = _owner_refs_read.get() or await _owner_refs_or_none()
        # Shared rooms, two guest levels (Batch 8, task 1 — B8-1, ruling R6):
        # who reads the room's past turns. The owner reads it all; a guest the
        # owner named a collaborator (on this door, by their own id — the
        # doors put the guest's platform id on turn.owner_id) also reads the
        # work she did in the room; every other guest is a stranger and reads
        # only what was said out loud. Personal bands stay blank for both.
        _audience = "owner"
        if _guest:
            from core.collaborators import is_collaborator
            _audience = "collaborator" if is_collaborator(
                _platform_for_surface(str(getattr(turn, "surface", "") or "")),
                getattr(turn, "owner_id", None),
            ) else "stranger"

        # Own-work spec §4b: the registry is surface-scoped. Workbench turns
        # (her self-initiated work sessions) get the guarded shell and lose
        # every delivery/messaging tool; every other surface resolves to the
        # same registry it built before the surface argument existed.
        # Friends get web search (R14, T7; the owner, 10-12 21:20): a guest's
        # turn gets the guest belt — web_search and nothing else — while
        # features.guest_web_search is on; off, None, as before. The approval
        # hook below is still set with is_owner=False, a second wall.
        registry = build_guest_registry() if _guest else build_default_registry(
            surface=str(getattr(turn, "surface", "") or "")
        )

        # D.4 step 3: wire an approval hook that gates tools marked
        # ``requires_approval=True``. Pre-D.4 the hook slot at
        # tools.py:110/141 existed but no caller wired it, and no
        # builtin had requires_approval=True — so the security path
        # was inert. local_shell now carries requires_approval=True;
        # this hook is the second-layer gate. Auto-approves for the
        # resolved owner; non-owner callers are denied. A user-prompting
        # approval flow can replace ``_approve_owner_only`` later
        # without touching dispatch wiring.
        if registry is not None:
            registry.set_approval_hook(_build_approval_hook(is_owner=is_owner_flag))

        # Resolve model from the configured response-task model rather than
        # hardcoding DEEPSEEK_MODEL. The OpenAI-compat client built by
        # _get_openai_compat_client() inherits whatever provider LLM_PROVIDER
        # selects (openai, deepseek, local_openai, codex via SDK adapter); the
        # model string must match that provider's accepted names. Read order:
        #   1. LLM_RESPONSE_MODEL (explicit per-task override)
        #   2. provider-specific default (OPENAI_MODEL / DEEPSEEK_MODEL / etc.)
        #   3. OPENAI_MODEL (final fallback)
        # This closes the Phase D scaffold leak where the agent loop always
        # picked deepseek-chat regardless of LLM_PROVIDER.
        from ai.llm_client import llm as llm_client_singleton
        active_provider_name = (
            getattr(llm_client_singleton.provider, "provider_name", "") or ""
        ).lower()
        runner_model = (
            getattr(config, "LLM_RESPONSE_MODEL", "")
            or _default_runner_model(active_provider_name)
            or getattr(config, "OPENAI_MODEL", "gpt-4o")
        )

        # Env override exists for test isolation ONLY (the suite must
        # never append to the owner's live session transcripts — see
        # tests/conftest.py); live runs leave it unset.
        _sessions_root = Path(
            os.environ.get("PALLI_AGENT_SESSIONS_ROOT") or "data/agent_sessions"
        )
        # Step 27 Phase B (spec §1): a sidecar turn sees a read-only snapshot
        # of the parent it runs beside. Flag-gated; a plain turn is untouched.
        _ctx_builder = build_turn_context
        _md_extra = getattr(getattr(turn, "metadata", None), "extra", None)
        if isinstance(_md_extra, dict) and _md_extra.get("sidecar_of"):
            from core.agent import duplex as _dx
            if _dx.duplex_enabled():
                from core.agent.sidecar import wrap_context_builder
                _ctx_builder = wrap_context_builder(
                    build_turn_context, parent_key=str(_md_extra["sidecar_of"]),
                    sessions_root=_sessions_root,
                )
        deps = RunnerDeps(
            build_turn_context=_ctx_builder,
            cadence_llm=cadence_llm,
            fetch_recent_history=fetch_recent_history,   # fix 6: the vote runs beside recall
            # One first look (R14, T5): the look's call on its own lane.
            first_look_llm=_first_look.build_llm(),
            sessions_root=_sessions_root,
            provider=provider,
            model=runner_model,
            client=await _get_openai_compat_client(),
            tool_registry=registry,
            polling_tools=set(),
            call_kwargs=_runner_call_kwargs(active_provider_name),
            # Own-work §6b#1: a work session metabolizes nothing. Reflection
            # writes memories, user facts and open threads — the last of
            # which is an outreach rail, and §5 gives her work exactly one
            # door to him (the SHARE marker). Not merely unused here: unwired.
            apply_reflection=None if (_is_workbench_turn(turn) or _guest) else apply_reflection,
            is_owner=is_owner_flag,
            owner_refs=_owner_refs,
            audience=_audience,
            max_iterations=_workbench_round_cap(turn),
            # The reply-length governor (2026-08-22) shapes conversation only,
            # and never a late-reminder digest, which must name every line.
            govern_replies=not (_is_workbench_turn(turn) or _is_late_digest_turn(turn)),
        )

        # D.3 wave 2 step 4: capture chunker emissions so we can
        # assemble a CadencedReply for the surface. Pre-Wave-2 this
        # callback was None and the runner's CadencedBeatChunker
        # streamed beats to nothing — so cadenced_reply ended up None
        # in TurnResult and surfaces fell back to plain response_text.
        # Each call here corresponds to one chunker emission (one beat
        # for CadencedBeatChunker, one paragraph for SingleFlowChunker,
        # one fragment for VoiceFlowChunker). dispatch then builds the
        # CadencedReply from the captured strings.
        # Bug 1 fix (2026-07-04): persist the user's message BEFORE the
        # cancellable run_turn, fire-and-forget, so a follow-up message or
        # typing-debounce that cancels this turn (barge-in in
        # bots/discord/client.py) can't delete their words. Pre-fix the
        # user-side write ran only AFTER run_turn, so a CancelledError skipped
        # it and the message vanished from history — PaLLi never received it.
        # Mirrors core/turn/turn.py:71-81. Text turns only: attachment turns
        # defer to the post-run persist (they need the vision analysis; Bug 5).
        # Synthesized origins (monologue/reminder/proactive) are skipped so we
        # don't write bot-generated content as user history.
        _early_md = getattr(turn, "metadata", None)
        _early_origin = (
            getattr(_early_md, "origin", "user") if _early_md is not None else "user"
        )
        _early_content = (getattr(turn, "content", "") or "").strip()

        # Present Moment sense A (2026-07-24): structural only — origin
        # and surface, never content. Fire-and-forget; cannot raise.
        try:
            from ai.present_moment import notice as _pm_notice
            if _early_origin in ("user", "dashboard_user") and _pm_notice_allowed(turn):
                _pm_notice(
                    "owner_message",
                    surface=str(getattr(turn, "surface", "") or ""),
                    origin=_early_origin,
                )
        except Exception:
            pass

        _early_has_attachments = bool(getattr(turn, "images", None)) or bool(
            getattr(_early_md, "text_attachments", None)
        )
        _early_prerecorded = bool(getattr(_early_md, "user_side_prerecorded", False))
        persisted_user_side_early = False
        if (
            _early_content
            and _early_origin in ("user", "dashboard_user")
            and not _early_has_attachments
            and not _early_prerecorded
        ):
            try:
                _persist_task = asyncio.create_task(
                    _persist_user_side_with_content(turn, _early_content)
                )
                _BACKGROUND_PERSIST_TASKS.add(_persist_task)
                _persist_task.add_done_callback(_BACKGROUND_PERSIST_TASKS.discard)
                persisted_user_side_early = True
            except Exception as exc:
                logger.warning(
                    "dispatch react: early user-side persist scheduling failed: %s",
                    exc,
                )

        captured_chunks: list[str] = []

        async def _capture_chunk(text: str) -> None:
            if text:
                captured_chunks.append(text)

        try:
            run_result = await run_turn(turn, deps=deps, on_chunk=_capture_chunk)
        except Exception as exc:
            # Reliability boundary: a raised run_turn (e.g. the LLM call failing
            # after retries, an unexpected tool/registry error) must not crash
            # the surface adapter. Degrade to a clean no-response TurnResult with
            # the error recorded so Discord/voice stay silent and the dashboard
            # shows a tidy message instead of a 500.
            logger.exception("dispatch react: run_turn raised; degrading turn: %s", exc)
            # Present Moment sense B: the turn died for real (barge-in
            # cancels never reach here — CancelledError isn't Exception's
            # concern in this catch; asyncio cancels propagate past it).
            try:
                from ai.present_moment import notice as _pm_notice
                _pm_notice("turn_failed", error_class=type(exc).__name__)
            except Exception:
                pass
            _outage = _early_origin in _HUMAN_ORIGINS
            return TurnResult(
                responded=_outage,
                response_text=_PROVIDER_OUTAGE_LINE if _outage else "",
                decision=None,
                reflection_applied=False,
                delegation_tier_used=0,
                error=f"agent loop error: {exc}",
            )

        # Batch 1 (2026-09-26): the model saw only [MASKED:<hex>] tokens; an
        # owner's reply gets the real values back HERE — before the history
        # row, the cadenced beats and the TurnResult are built from it. A
        # guest's keeps the tokens.
        run_result = _put_back_for_owner(run_result, captured_chunks, turn)

        # Error-silence becomes the honest outage line for a waiting human
        # (2026-08-11; see _apply_outage_fallback). Before the moment notice
        # so sense A sees the truth: she DID respond, with the outage line.
        run_result = _apply_outage_fallback(run_result, _early_origin)

        # She chose to stay quiet (Batch 8, task 2a — B8-2, rulings T2-3,
        # T2-4): nothing is sent — not even words the chunker gathered on
        # the way — and the message is already in her transcript. The choice
        # and her reason go to her record of quiet choices.
        if bool(getattr(run_result, "quiet", False)):
            captured_chunks.clear()
            if tuple(getattr(run_result, "voice_notes", ()) or ()):
                # Second read F8 (2026-10-04): a voice message she made still
                # goes out beside the quiet choice (ruling FW-3) — she
                # answered aloud, and the runner wrote its words as her reply.
                # A "quiet" row would make her record say she stayed quiet.
                logger.info(
                    "dispatch react: quiet choice on %s (origin=%s) with a voice message — "
                    "no text sent, the voice message goes; not recorded as quiet",
                    str(getattr(turn, "surface", "") or ""), _early_origin,
                )
            else:
                logger.info(
                    "dispatch react: chose quiet on %s (origin=%s) — nothing sent; "
                    "the message stays in her transcript",
                    str(getattr(turn, "surface", "") or ""), _early_origin,
                )
                await _record_quiet_choice(
                    turn, str(getattr(run_result, "quiet_reason", "") or ""), guest=_guest)

        # Present Moment sense A: the turn finished (incl. chosen silence).
        try:
            from ai.present_moment import notice as _pm_notice
            if _pm_notice_allowed(turn):
                _pm_notice(
                    "turn_completed",
                    responded=bool(run_result.responded),
                    origin=_early_origin,
                    surface=str(getattr(turn, "surface", "") or ""),
                )
        except Exception:
            pass

        # D.3 wave 2 step 1: persist conversation rows so subsequent
        # ReAct turns can see this turn in their recent_history. Pre-Wave-2
        # the ReAct path skipped persistence entirely; turns vanished from
        # conversation_history. Pipeline mode (core/turn/turn.py) writes
        # the same two rows via the shared helpers in core/turn/persistence.
        # User-side write fires regardless of whether the assistant
        # responded — the user spoke, so their content belongs in history.
        # Skip synthesized origins (monologue/reminder/proactive) so
        # we don't pollute user history with bot-generated content.
        try:
            md = getattr(turn, "metadata", None)
            origin = getattr(md, "origin", "user") if md is not None else "user"
            user_content = (getattr(turn, "content", "") or "").strip()
            # Skip when the early (pre-run_turn) persist already wrote this row —
            # only attachment turns (which the early path defers) reach here.
            if (
                user_content
                and origin in ("user", "dashboard_user")
                and not persisted_user_side_early
                and not bool(getattr(md, "user_side_prerecorded", False))
            ):
                # Bug 5 fix (2026-07-04): attachment turns reach here (the early
                # persist defers them). Bake recall's vision / text-attachment
                # analysis into the row so future turns remember what the
                # image/file showed — pre-fix the ReAct path stored only the
                # "[image attached]" placeholder. Mirrors the pipeline engine.
                _img = tuple(getattr(run_result, "image_descriptions", ()) or ())
                _txt = tuple(getattr(run_result, "text_attachment_contents", ()) or ())
                if _img or _txt:
                    user_content = _expand_content_with_attachments(
                        user_content, _img, _txt
                    )
                await _persist_user_side_with_content(turn, user_content)
        except Exception as exc:
            logger.warning("dispatch react: user-side persist failed: %s", exc)

        # Own-work §4b null delivery: her sign-off belongs to her session
        # transcript, not to `conversations` — persisting it would put her
        # private work into the history her next reply to him recalls.
        if (
            run_result.responded
            and run_result.response_text
            and not _is_workbench_turn(turn)
        ):
            try:
                await _persist_assistant_side_to_history(
                    turn=turn, response_text=run_result.response_text,
                )
            except Exception as exc:
                logger.warning("dispatch react: assistant-side persist failed: %s", exc)

        # ── stake appraisal (companion.stake_ledger, 2026-07-06) ──────
        # Fire-and-forget appraisal of the completed exchange against PaLLi's
        # stake ledger (ai/stake_appraiser.py). Detached + strongly referenced
        # (the Bug-1 retention pattern) so it survives a later barge-in cancel
        # of this handler task and can never delay or break the reply.
        # Genuine user exchanges only — synthesized origins (monologue/
        # reminder/proactive_action) never appraise.
        try:
            _ap_md = getattr(turn, "metadata", None)
            _ap_origin = getattr(_ap_md, "origin", "user") if _ap_md is not None else "user"
            if (
                run_result.responded
                and run_result.response_text
                and _ap_origin in ("user", "dashboard_user")
            ):
                from ai.stake_ledger import ledger_enabled

                if ledger_enabled():
                    # Same rich content the history row got: vision/text-
                    # attachment analysis baked in, so the appraiser sees what
                    # the exchange was actually about.
                    _ap_content = (getattr(turn, "content", "") or "").strip()
                    _ap_img = tuple(getattr(run_result, "image_descriptions", ()) or ())
                    _ap_txt = tuple(getattr(run_result, "text_attachment_contents", ()) or ())
                    if _ap_img or _ap_txt:
                        _ap_content = _expand_content_with_attachments(
                            _ap_content, _ap_img, _ap_txt
                        )
                    _ap_channel = (
                        str(getattr(_ap_md, "channel_id", "") or "") if _ap_md else ""
                    )
                    from ai.stake_appraiser import appraise_exchange

                    _appraisal_task = asyncio.create_task(
                        appraise_exchange(
                            user_message=_ap_content,
                            palli_reply=run_result.response_text,
                            channel_id=_ap_channel,
                        )
                    )
                    _BACKGROUND_PERSIST_TASKS.add(_appraisal_task)
                    _appraisal_task.add_done_callback(_BACKGROUND_PERSIST_TASKS.discard)
        except Exception as exc:
            logger.warning("dispatch react: stake appraisal scheduling failed: %s", exc)

        # Adapt RunResult -> TurnResult so surfaces (Discord, dashboard,
        # voice) consume the same shape regardless of loop mode.
        # Reflection is SPAWNED inside run_turn as a detached background task
        # (2026-07-08) — reflection_applied=True means the wiring was present
        # and the task was scheduled, not that it already completed.
        cadenced_reply = _build_cadenced_reply_from_chunks(
            chunks=captured_chunks,
            response_text=run_result.response_text,
            attachments=tuple(run_result.attachments or ()),
        ) if run_result.responded else None

        return TurnResult(
            responded=run_result.responded,
            response_text=run_result.response_text,
            terminated_by=str(getattr(run_result, "terminated_by", "natural") or "natural"),
            decision=None,
            reflection_applied=(
                deps.apply_reflection is not None and run_result.responded
            ),
            delegation_tier_used=0,
            error=run_result.error,
            # D.3 wave 2 step 4: cadenced_reply is now a real
            # CadencedReply assembled from the chunker's emissions
            # rather than None. Single-beat replies (the common case)
            # still render exactly as before via response_text; the
            # surface only fan-outs when len(beats) > 1, which now
            # actually happens when CadencedBeatChunker emitted multiple
            # paragraph-separated beats.
            cadenced_reply=cadenced_reply,
            # D.2 wave 1: pass through attachments from the runner so
            # tier-1 file outputs (deliver_artifact, screenshots, webcam
            # snaps) reach the surface adapters. Wave 2 step 3 wired the
            # runner-side accumulation; this carries the result.
            attachments=tuple(run_result.attachments or ()),
            # Batch 8, task 2a: her quiet choice travels to the doors.
            quiet=bool(getattr(run_result, "quiet", False)),
            quiet_reason=str(getattr(run_result, "quiet_reason", "") or ""),
            # Batch 8, task 10 (ruling T10-3): her voice notes travel to the
            # doors too — on a quiet choice as well: a note she made is a
            # delivery, like a file (final fix wave, ruling FW-3).
            voice_notes=tuple(getattr(run_result, "voice_notes", ()) or ()),
        )

    # ReAct is the only loop; the tier pipeline was removed 2026-07-22.
    raise RuntimeError(f"non-react loop mode {mode!r} is no longer supported")


# Which owner ref names the owner on each door, for her record of quiet
# choices: the record keeps a platform user id where the door has one.
_OWNER_REF_BY_SURFACE = {
    "discord_text": "discord_user_id",
    "discord_voice": "discord_user_id",
    "telegram_text": "telegram_user_id",
}


async def _record_quiet_choice(turn: Any, reason: str, *, guest: bool) -> None:
    """One line in her record of quiet choices (Batch 8, task 2a — ruling
    T2-4): when, which door, which conversation (``session_key_for`` — a
    sidecar's choice is recorded on the room it answers), who (a guest's
    platform id is the turn's owner_id; the owner's comes from the stored
    owner refs, else the app user id), the message's first 120 characters
    and her reason. Best-effort: the choice binds whether or not this line
    is written."""
    try:
        from core import quiet_record
        from core.session_keys import session_key_for

        md = getattr(turn, "metadata", None)
        surface = str(getattr(turn, "surface", "") or "")
        owner_id = getattr(turn, "owner_id", None)
        who_id: Any = owner_id
        if not guest:
            ref = _OWNER_REF_BY_SURFACE.get(surface)
            if ref:
                try:
                    from core import local_mode
                    who_id = (await local_mode.get_local_owner_refs()).get(ref) or owner_id
                except Exception:
                    who_id = owner_id
        extra = getattr(md, "extra", None) if md is not None else None
        name = (getattr(md, "discord_username", None) if md is not None else None) or (
            extra.get("telegram_username") if isinstance(extra, dict) else None) or ""
        quiet_record.record(
            door=surface,
            conversation=session_key_for(
                surface, getattr(md, "channel_id", None) if md is not None else None, owner_id),
            who_id=who_id,
            who_name=name,
            owner=not guest,
            text=str(getattr(turn, "content", "") or ""),
            reason=reason,
            source="reply",
        )
    except Exception as exc:  # never let the record break the turn it records
        logger.warning("dispatch react: quiet record skipped: %s", type(exc).__name__)


def _build_cadenced_reply_from_chunks(
    *,
    chunks: list[str],
    response_text: str,
    attachments: tuple[str, ...],
) -> Any:
    """Assemble a CadencedReply from chunker emissions captured in dispatch.

    D.3 wave 2 step 4: pre-Wave-2 the ReAct branch passed
    ``on_chunk=None`` to the runner, so CadencedBeatChunker's beat
    emissions streamed to nothing and the surface received
    ``cadenced_reply=None`` — falling back to a single send of
    response_text. Now dispatch captures each chunk and reassembles
    them into a CadencedReply mirroring how core/turn/cadence.py
    builds the type in pipeline mode (beats=tuple of Beat, total_text
    joined).

    When the chunker emitted ZERO chunks (non-streaming provider, or a
    test mock that bypassed the chunker), fall back to a single beat
    derived from response_text — the exact pattern
    ``CadencedReply.from_single`` uses.

    Single-beat replies are still the common case; the surface adapter
    (bots/discord/client.py) only fan-outs when ``len(beats) > 1``, so
    one-chunk replies render exactly as the legacy single-message path.
    """
    from core.turn.types import Beat, CadencedReply

    cleaned = [c.strip() for c in chunks if c and c.strip()]

    if not cleaned:
        # No chunker output (non-streaming provider, mocked LLM, etc).
        # Mirror CadencedReply.from_single with our attachments.
        return CadencedReply(
            beats=(Beat(text=response_text),),
            total_text=response_text,
            attachments=attachments,
        )

    if len(cleaned) == 1:
        # Single chunk: surface treats it as a single-beat send.
        return CadencedReply(
            beats=(Beat(text=cleaned[0]),),
            total_text=response_text,
            attachments=attachments,
        )

    # Multi-chunk: each chunk becomes a Beat. Surface fan-outs.
    beats = tuple(Beat(text=text) for text in cleaned)
    return CadencedReply(
        beats=beats,
        total_text=response_text,
        attachments=attachments,
    )


def _build_approval_hook(*, is_owner: bool):
    """Build the approval hook installed on ToolRegistry in ReAct mode.

    D.4 step 3: tools.py:141 dispatches each tool with
    ``requires_approval=True`` through the registered hook. The hook
    returns ``("allow", "")`` to permit the call or ``("deny", reason)``
    to block it. Pre-D.4 no hook was ever installed, so the
    requires_approval flag was inert.

    Current policy (v1):

    - **Owner-only tools (e.g. local_shell)** auto-approve when the
      caller is the resolved local owner, deny otherwise. The
      owner_only flag at tools.py:134 already blocks non-owner *callers*
      with an "owner-only" error, so this gate is effectively a
      defense-in-depth layer for owner-tagged calls. We keep the
      explicit deny here so a future un-flagging of owner_only doesn't
      silently open the tool to non-owners.

    - **Non-owner-only tools** with ``requires_approval=True`` (none
      today; future work) auto-approve unconditionally. A user-prompting
      approval flow can replace this branch when the threat model needs
      it — e.g. a deliver_artifact path-allowlist check.

    The hook signature matches ``ApprovalHook`` at tools.py:91:
    ``async (tool, input_dict, ctx) -> (decision, reason)``. ``ctx``
    carries the same ``is_owner`` we resolved earlier; the closure
    over ``is_owner`` here is the canonical value for the whole turn.
    """
    async def _approval_hook(tool: Any, _input: dict, _ctx: Any) -> tuple[str, str]:
        if not getattr(tool, "owner_only", False):
            # Non-owner-only tools requiring approval auto-approve for now.
            return ("allow", "")
        if is_owner:
            return ("allow", "")
        return ("deny", f"{tool.name!r} requires owner approval; caller is not the local owner.")

    return _approval_hook


# The owner's account ids as ``_resolve_is_owner_for_turn`` read them for this
# turn, so the owner's-accounts section reuses that read (Fable's checkpoint
# review, 4). Dispatch clears it before the check; None: nothing read.
_owner_refs_read: ContextVar[dict[str, Any] | None] = ContextVar(
    "palli_owner_refs_read", default=None)


async def _owner_refs_or_none() -> dict[str, Any] | None:
    """The owner's account ids on each app, or None when they can't be read
    (the section that names them is left out; nothing else depends on it)."""
    try:
        from core import local_mode
        return dict(await local_mode.get_local_owner_refs())
    except Exception as exc:
        logger.debug("dispatch: the owner's account ids unavailable: %s", exc)
        return None


async def _resolve_is_owner_for_turn(turn: Any) -> bool:
    """Resolve whether the turn's owner_id matches the local owner.

    D.3 wave 2 step 2: ReAct mode pre-fix defaulted is_owner=True for
    everyone via runner.py's getattr lookup, which combined with
    local_shell's owner_only=True flag let any surface invoke
    operator-class tools. This helper consults
    ``local_mode.get_local_owner_refs()`` for the resolved owner
    app_user_id and compares it against ``turn.owner_id``.

    On any failure (local_mode not enabled, DB transient error, owner
    not yet provisioned), returns False so the security posture stays
    restrictive — better to wrongly deny than wrongly allow operator
    tools. The owner_id field on Turn is itself an app_user_id-shaped
    value populated by surface adapters (bots/discord/turn_builder).
    The ids it read are kept in ``_owner_refs_read`` for the turn's
    owner's-accounts section (Fable's checkpoint review, 4).
    """
    try:
        from core import local_mode
        refs = await local_mode.get_local_owner_refs()
    except Exception as exc:
        logger.warning("dispatch: local_mode.get_local_owner_refs failed: %s", exc)
        return False

    _owner_refs_read.set(dict(refs))
    resolved_owner_id = refs.get("app_user_id")
    if resolved_owner_id is None:
        return False
    try:
        return int(getattr(turn, "owner_id", 0)) == int(resolved_owner_id)
    except Exception:
        return False


# Cadence path notes:
# The plan referenced `from ai.llm_openai_compatible import chat_completion`,
# but no such module-scope function exists. PaLLi's OpenAI-compat surface
# is the BaseLLMProvider / OpenAICompatibleProvider class hierarchy reached
# via `ai.llm_client.llm` (the module-level `LLMClient()` singleton) with
# provider lookup at `llm.providers[name]` (e.g. `deepseek`, `openai`,
# `local_openai`). The cadence pass uses that surface directly because it
# only needs a single non-streaming completion — it does NOT go through
# the runner's `client.chat.completions.create` path, so it can keep
# leveraging PaLLi's existing aiohttp/credential-pool/retry machinery.
#
# v1 model choice: cadence shaping currently uses DEEPSEEK_MODEL (set in
# RunnerDeps above). The plan specified "small fast model" — DEEPSEEK_MODEL
# is a fine default for the v1 ship since deepseek-chat is already a
# small/fast model. A dedicated CADENCE_MODEL env var can be added in
# Task 27 (config plumbing) if telemetry shows a need to split.
async def _build_cadence_llm_callable() -> Callable[[str], Awaitable[str]]:
    """Build a (prompt -> str) callable that wraps PaLLi's existing
    LLM client for cadence-shaping calls. The cadence pass uses a small
    fast model — same client, smaller request.

    D.4 step 2: when ``CADENCE_MODEL`` (or ``PALLI_CADENCE_MODEL``) is
    set, pass it through to ``chat_completion`` so the cadence pass can
    target a cheaper/faster model than the main runner. Empty/unset
    falls through to provider default — preserves pre-D.4 behavior
    where DEEPSEEK_MODEL (or whatever the active provider's default is)
    handled cadence. Read at call time, not module load, so the env
    knob takes effect without process restart in tests and dev.
    """
    from ai.llm_client import llm as llm_client_singleton
    from core.config import config

    # Config reads PALLI_CADENCE_MODEL then CADENCE_MODEL at module load;
    # also check the live env in case it was set after import (tests).
    cadence_model = (
        getattr(config, "CADENCE_MODEL", "")
        or os.environ.get("PALLI_CADENCE_MODEL", "")
        or os.environ.get("CADENCE_MODEL", "")
    )

    async def cadence_llm(prompt: str) -> str:
        # Single-shot, no tools, low temperature, small max_tokens.
        # Bug 3 fix (2026-07-04): route via the routing-aware
        # llm.chat_completion (NOT provider.chat_completion) so a
        # "provider::model" CADENCE_MODEL reference can cross providers —
        # e.g. to a fast Anthropic Haiku, so the pre-reply cadence pass
        # stops inheriting DeepSeek's per-call / peak-hour latency.
        # provider.chat_completion is provider-specific and can't do that.
        kwargs: dict[str, Any] = {
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.2,
            "max_tokens": 600,
            "log_context": "agent_cadence_shape",
        }
        if cadence_model:
            kwargs["model"] = cadence_model
        result = await llm_client_singleton.chat_completion(**kwargs)
        return result or ""
    return cadence_llm


def _default_runner_model(provider_name: str) -> str:
    """The runner's model when LLM_RESPONSE_MODEL is unset, by active provider.
    (2026-09-02: local_openai used to fall back to OPENAI_MODEL — gpt-4o on a
    local Ollama endpoint.)"""
    name = (provider_name or "").lower()
    table = {
        "openai": getattr(config, "OPENAI_MODEL", "gpt-4o"),
        "deepseek": getattr(config, "DEEPSEEK_MODEL", "deepseek-chat"),
        "local_openai": getattr(config, "LOCAL_LLM_MODEL", "") or getattr(config, "OPENAI_MODEL", "gpt-4o"),
        "ollama": getattr(config, "OLLAMA_MODEL", ""),
        "codex": getattr(config, "CODEX_MODEL", "gpt-5.5"),
        "anthropic": getattr(config, "ANTHROPIC_MODEL", "claude-sonnet-4-6"),
        # 2026-09-03 (configurator R5): the Claude CLI's own model, so a claude brain
        # with LLM_RESPONSE_MODEL unset stops falling through to OPENAI_MODEL.
        "claude": getattr(config, "CLAUDE_CLI_MODEL", ""),
    }
    return str(table.get(name, "") or "")


def _runner_call_kwargs(provider_name: str) -> dict[str, Any]:
    """Extra keyword args for llm_client.call, by active provider. 2026-09-02:
    a local Ollama brain through its OpenAI-style endpoint takes
    `reasoning_effort` per request (LOCAL_LLM_REASONING_EFFORT) — thinking
    off is what makes Qwen actually call tools on that path."""
    if (provider_name or "").lower() == "local_openai":
        effort = str(getattr(config, "LOCAL_LLM_REASONING_EFFORT", "") or "").strip().lower()
        if effort:
            return {"extra_body": {"reasoning_effort": effort}}
    return {}


# Batch 4 task 7 (spec 5.12): her reply client stops waiting on a provider
# that sends nothing. The read seconds (90; 300 for local_openai; the
# LLM_REPLY_READ_TIMEOUT_SECONDS knob) come from ONE helper in llm_client,
# which also reads them back off this client as the per-chunk content
# deadline — so keep-alive bytes cannot stretch the wait (R11a).
_REPLY_CONNECT_TIMEOUT_SECONDS = 10.0


def _reply_client_timeout(provider: str = "") -> "httpx.Timeout":
    """The ``httpx.Timeout`` for her reply client to this provider."""
    import httpx

    from core.agent.llm_client import reply_read_seconds

    return httpx.Timeout(reply_read_seconds(provider), connect=_REPLY_CONNECT_TIMEOUT_SECONDS)


async def _get_openai_compat_client() -> Any:
    """Return a client object that exposes the OpenAI AsyncOpenAI shape
    expected by the runner's `core/agent/llm_client.py::call_openai_compat`.

    The runner does
    `await client.chat.completions.create(stream=True, stream_options=...)`
    and `async for chunk in stream:` — the OpenAI Python SDK v1+ surface.
    Two paths cover the supported providers:

    1. **API-key SDK path** (deepseek, openai, openai-compat HTTP, local_openai):
       Build a real `AsyncOpenAI(base_url=..., api_key=...)`. Faster, true
       streaming, exact usage numbers, but only viable when the active
       provider exposes an HTTP API key.

    2. **PaLLi-provider adapter path** (codex CLI, claude CLI, anything else
       without `api_key`): Wrap the active PaLLi provider in
       `PaLLiProviderSDKAdapter`, which prompt-engineers tool calls via
       `<tool_call>` blocks and yields chunks in the SDK delta shape.
       No native streaming, estimated usage, but lets ReAct mode work
       with CLI-auth providers.

    Tradeoffs of the SDK path vs. PaLLi's native aiohttp path:
    - The SDK client doesn't honor PaLLi's CredentialPool cooldown
      rotation or the 400-max-tokens-bump-and-retry recovery (those
      live in OpenAICompatibleProvider.chat_completion and are
      aiohttp-bound). Acceptable for v1 — the runner has its own
      retry/backoff via the loop-detection layer.
    """
    from ai.llm_client import llm as llm_client_singleton

    provider = llm_client_singleton.provider
    provider_name = (getattr(provider, "provider_name", "") or "").lower()
    api_key = getattr(provider, "api_key", "") or None

    # Path 2: Providers without an HTTP api_key go through the adapter.
    # codex CLI authenticates via codex's own login (no api_key attr).
    # claude CLI is similar. Any other future provider that doesn't have
    # `api_key` set falls into the same bucket. The adapter exposes the
    # SDK shape the runner wants by wrapping `provider.chat_completion`.
    if not api_key and provider_name != "local_openai":
        from core.agent.sdk_adapter import PaLLiProviderSDKAdapter
        return PaLLiProviderSDKAdapter(provider)

    # Path 1: SDK client for API-key providers (and local_openai
    # placeholder shortcut for keyless local endpoints).
    from openai import AsyncOpenAI

    base_url = (getattr(provider, "base_url", "") or "").rstrip("/") or None
    if not api_key and provider_name == "local_openai":
        # local_openai/Ollama-style endpoints don't require auth, but the
        # SDK rejects an empty api_key. Use a benign placeholder so those
        # backends still work.
        api_key = "not-required"

    # max_retries=0: llm_client's loop is the one place a failed reply call is
    # retried (a stall once, a quick transient twice). The SDK's own two
    # retries underneath it would make one 90 s stall cost 3 x 3 x 90 = 810 s.
    return AsyncOpenAI(
        base_url=base_url, api_key=api_key,
        timeout=_reply_client_timeout(provider_name), max_retries=0,
    )
