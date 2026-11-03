"""The mid-task fork: what happens to an owner message that arrives while a
run is already going (spec §4b, §5.1–§5.6).

Kept out of client.py so it can be tested without a gateway connection. The
client supplies a ``send`` callable; everything else is decided here.

Moved here from bots/discord (telegram door, 2026-09-14, task 1): none of
this is about Discord. It is about what happens to a message that arrives
while she is already working, which is true at every door. The old path
``bots.discord.steering_fork`` is a shim that aliases this module.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from pathlib import Path

from core import voice_replies as _voice_replies
from core.agent import steering
from core.agent.steering import SteerBundle
from core.agent.steering_reply import judge_mid_task

# Where session transcripts live when no caller-specific root is threaded in.
# Matches PalliDiscordClient._sessions_root; the client still passes its own
# explicitly so tests and future surfaces can redirect.
DEFAULT_SESSIONS_ROOT = Path("data/agent_sessions")


logger = logging.getLogger(__name__)

# Deterministic budget (spec §5.5): beyond this many interim replies in one
# run she auto-holds. A restraint budget in code, not a meaning decision.
MAX_INTERIM_REPLIES = 8

# Spec §4g: a run that wedges (hung tool, stalled stream) must not hold his
# words hostage. After this long undrained, the bundle becomes a normal turn.
DEADLINE_SECONDS = float(os.environ.get("PALLI_STEER_DEADLINE_SECONDS", "180") or 180)
# 2026-08-28: while the run is still ALIVE at the deadline, the watchdog
# re-arms instead of falling back — a long tool call is not a wedged run
# (step 27 made long silent work normal; a 7-minute local_shell at 13:03
# drew a competing turn written into its open round-trip). Bounded: past
# this many re-arms the run is treated as wedged after all.
DEADLINE_MAX_REARMS = int(os.environ.get("PALLI_STEER_DEADLINE_MAX_REARMS", "10") or 10)

_DEADLINE_TASKS: set[asyncio.Task] = set()

_STOP_WORDS = {"stop", "/stop"}
_REPLY_BUDGET: dict[str, int] = {}

# Per-session switches a command can throw (telegram door, task 3):
# session_key -> {flag name -> bool}. ``/voice on`` in one chat sets
# ``voice_replies`` for THAT chat and nowhere else.
#
# In memory, beside the reply budget above and for the same reason — it is a
# property of a live conversation, not of the account. A restart forgets it,
# which is the spec's choice: a setting that survives a restart belongs in
# his profile, and that is a separate decision with a separate surface.
#
# Except ``voice_replies`` while ``voice.voice_notes`` is on (Batch 8, task
# 10 — the owner's B8-5 overrides that choice for this one switch): it is
# then the remembered per-conversation switch in core/voice_replies.py, the
# same one her voice_replies tool sets, and it survives a restart. With the
# setting off it stays here, in memory, exactly as before.
_SESSION_FLAGS: dict[str, dict[str, bool]] = {}


def _remembered(name: str) -> bool:
    return str(name) == _voice_replies.FLAG and _voice_replies.voice_notes_enabled()


def set_session_flag(session_key: str, name: str, value: bool) -> None:
    """Set one boolean for one session."""
    if _remembered(name):
        _voice_replies.set_on(str(session_key), bool(value))
        return
    _SESSION_FLAGS.setdefault(str(session_key), {})[str(name)] = bool(value)


def get_session_flag(session_key: str, name: str, default: bool = False) -> bool:
    """Read one boolean for one session; ``default`` when it was never set."""
    if _remembered(name):
        return _voice_replies.VoiceRepliesStore().get(str(session_key), default)
    return bool(_SESSION_FLAGS.get(str(session_key), {}).get(str(name), default))


def is_stop_command(text: str) -> bool:
    """Exact-match kill switch — no model in the path, no settle window."""
    return str(text or "").strip().lower() in _STOP_WORDS


def reset_reply_budget(session_key: str) -> None:
    _REPLY_BUDGET.pop(session_key, None)


def _flag_on() -> bool:
    from core.turn.companion_flags import companion_settings

    companion = companion_settings()
    if companion is None:
        return False          # fail closed: unbooted settings keep today's behavior
    return bool(getattr(companion, "converse_while_working", False))


def _has_attachments(turn) -> bool:
    """Attachment-ish turns need full-turn recall (spec §5.2) — vision/file
    analysis doesn't run in the steer lane. Voice rides the same exit: the
    voice surface is an explicit non-goal of this slice, and declining keeps
    it on today's exact path."""
    md = getattr(turn, "metadata", None)
    return (
        bool(getattr(turn, "images", None))
        or bool(getattr(md, "text_attachments", None))
        or bool(getattr(md, "is_voice", False))
    )


def _notice(kind: str, **fields) -> None:
    """Present Moment sense A for mid-task input (spec §6). The steered path
    bypasses dispatch, where the sense normally fires — without this her
    moment would be blind to mid-task messages. Structure only, never
    content. Fire-and-forget; never raises."""
    try:
        from ai.present_moment import notice
        notice(kind, **fields)
    except Exception:
        pass


async def _persist_assistant(*, turn, response_text: str) -> None:
    from core.turn.persistence import _persist_assistant_side_to_history
    await _persist_assistant_side_to_history(turn=turn, response_text=response_text)


async def _transcribe_bundle(session_key: str, bundle, sessions_root) -> bool:
    """Custody repair (spec §4e exactly-once; the 2026-07-28 api-key
    incident): write a REPLIED-but-undrained bundle's exchange into the
    session transcript — his words, then her interim reply. The runner only
    records these at the NEXT model boundary, and a run whose final LLM call
    was already in flight never has one; before this helper, such bundles
    were silently dropped and she lost all memory of exchanges Discord and
    the DB both carried.

    Marks the bundle ``drained`` on success — its words are now on the
    record, so every downstream path (fallback strip, re-dispatch) already
    treats it correctly. Returns False and logs loudly on failure: the DB
    still holds both sides, so nothing is lost beyond this attempt."""
    try:
        from core.agent.runner import _open_session_with_exchange
        await _open_session_with_exchange(
            session_key, Path(sessions_root),
            str(getattr(bundle.turn, "content", "") or ""),
            str(bundle.reply_text or ""),
            speaker=getattr(getattr(bundle.turn, "metadata", None), "speaker", None),
        )
        bundle.drained = True
        return True
    except Exception:
        logger.exception(
            "steering: transcription of a replied bundle failed on %s — the "
            "exchange survives in the DB only", session_key)
        return False


# How long _fire_burst waits for a finishing plain reply before treating the
# run as wedged and falling back to today's branches (spec: the 2026-08-23
# seam — a reply's task outlives its generation by a second or two).
TAIL_WAIT_SECONDS = float(os.environ.get("PALLI_TAIL_WAIT_SECONDS", "20") or 20)


def route_mid_run(*, working: bool, phase: str, prior_done: bool) -> str:
    """Where a burst goes when a prior run is in flight (2026-08-23).

    - "normal"          — the prior is done: an ordinary turn.
    - "supersede_check" — a plain reply is still GENERATING: the one judged
                          binary (does this change what she is saying?).
    - "wait_then_normal"— a plain reply (zero tool calls) that is no longer
                          generating — in prep, or its task wrapping up after
                          the reply landed: NOT work. Wait for it, then an
                          ordinary turn. Never a steer, never a holding line
                          (02:49 08-23: "once the current task is wrapped").
    - "steer"           — a run that has actually worked (a real tool call):
                          the mid-task lane.
    """
    if prior_done:
        return "normal"
    if working:
        return "steer"
    if str(phase or "") == "generating":
        return "supersede_check"
    return "wait_then_normal"


def stop_mode(*, duplex_on: bool, working: bool) -> str:
    """How a stop command lands (step 27, spec §3). "cooperative" = ask the
    run to stop at its next tool boundary and arm a hard cancel as the
    backstop; "hard" = today's immediate cancel (a plain reply in flight has
    no boundary to wait for; a flag-off build never waits)."""
    return "cooperative" if (duplex_on and working) else "hard"


def sidecar_keys_for(parent_key: str, live: dict, *, kind: str | None = "task") -> list[str]:
    """Child keys with a still-running sidecar of ``kind`` (None = any) —
    for stop fan-out (spec §3). Correction 8: "forget it" kills side WORK;
    a conversation already in flight finishes."""
    out: list[str] = []
    for k, entry in live.items():
        if not k.startswith(parent_key + "#sidecar-"):
            continue
        ek, task = entry if isinstance(entry, tuple) else ("task", entry)
        if kind is not None and ek != kind:
            continue
        if not task.done():
            out.append(k)
    return out


def route_paused(*, action: str) -> str:
    """A message arriving while a run is PAUSED (step 27 §3): the judge's
    "resume" resumes; "stop" (forget it) closes the pause and its clock
    (build-session correction 2); anything else is an ordinary turn and the
    pause stands, its clock still running."""
    if action == "resume":
        return "resume"
    if action == "stop":
        return "stop"
    return "normal"


def arm_pause_expiry(session_key: str, *, ttl_minutes: int,
                     sleep=asyncio.sleep) -> "asyncio.Task":
    """After the TTL, if the run is still paused: close the record, and let
    her notice it the way she notices an overrun (the owner, 10-12: no
    scripted lines; whether and how to tell them is hers)."""
    async def _expire() -> None:
        await sleep(float(ttl_minutes) * 60.0)
        if steering.get_paused(session_key) is None:
            return                          # resumed or superseded in time
        steering.clear_paused(session_key)
        from ai import present_moment as _pm
        _pm.notice("task_pause_expired", minutes=int(ttl_minutes))
        logger.info("steering: a pause on %s ran out after %d min; noticed, not spoken",
                    session_key, int(ttl_minutes))
    return asyncio.create_task(_expire())


async def handle_mid_task(turn, *, session_key: str, is_owner: bool,
                          send, run_in_flight: bool, dispatch=None,
                          sessions_root=DEFAULT_SESSIONS_ROOT,
                          launch_sidecar=None) -> bool:
    """Try to handle ``turn`` as mid-task input.

    Returns True when the bundle was taken into the steering lane (the caller
    must NOT cancel the run and must NOT dispatch a turn). Returns False to
    mean "not my business" — the caller keeps today's behavior.
    """
    if not run_in_flight or not _flag_on():
        return False
    if not is_owner:
        # Spec §5.1: steering injects text into a run holding the owner's tool
        # privileges. Nobody else may reach it. Fail closed.
        return False
    if _has_attachments(turn):
        return False          # spec §5.2 — vision needs a full turn

    _notice(
        "owner_message",
        surface=str(getattr(turn, "surface", "") or ""),
        origin="user",
    )

    judgment = None
    budget_used = _REPLY_BUDGET.get(session_key, 0)
    if budget_used < MAX_INTERIM_REPLIES:
        judgment = await judge_mid_task(turn, session_key=session_key)

    if judgment is not None and judgment.stop_run:
        return False          # caller cancels; queue teardown happens there

    from core.agent import duplex as _dx
    _duplex = _dx.duplex_enabled()
    if _duplex and judgment is not None and judgment.action == "pause":
        steering.request_pause(session_key)          # spec §3: "hold on" lands at the boundary

    bundle = SteerBundle(turn=turn, enqueued_at=time.time(),
                         working_at_enqueue=steering.is_working(session_key))

    async def _on_sidecar_done(reply: str, *, quiet: bool = False) -> None:
        # Phase B (correction 3): the sidecar hands its reply back. While the
        # parent is alive the bundle stays queued — the parent's boundary
        # drain writes the exchange. If the parent is gone, its writer is
        # gone: put the exchange on the record here, once (the sweep's own
        # helper), and leave the queue.
        # Batch 8, task 2a (ruling T2-3): ``quiet`` — the sidecar's reply
        # chose to stay quiet. The bundle is answered (nothing was sent);
        # nothing re-asks it, and his words still go on the record.
        bundle.sidecar_running = False
        bundle.needs_full_turn = False
        bundle.reply_text = reply or None
        bundle.replied = bool(reply)
        bundle.quiet = bool(quiet)
        if steering.get_run_phase(session_key):
            return
        steering.remove(session_key, bundle)
        if (bundle.replied or bundle.quiet) and not getattr(bundle, "drained", False):
            await _transcribe_bundle(session_key, bundle, sessions_root)

    if _duplex and launch_sidecar is not None and judgment is not None:
        kind = None
        if judgment.action == "new_task":
            kind = "task"
        elif judgment.full_turn:
            kind = "talk"                                # the sidecar IS the reply — no holding line
        if kind is not None:
            handle = await launch_sidecar(turn, kind=kind, on_done=_on_sidecar_done)
            if handle is not None:
                bundle.sidecar_running = True
                _REPLY_BUDGET[session_key] = budget_used + 1
                if not steering.enqueue(session_key, bundle):
                    return False
                logger.info("steering: %s sidecar launched beside %s", kind, session_key)
                return True
            # lane busy → today's path: queue; the run folds it in

    if judgment is not None and judgment.reply and not (_duplex and judgment.full_turn):
        try:
            await send(judgment.reply)
        except Exception as exc:
            # Spec §5.6: never record words that did not reach him.
            logger.warning("steering: interim send failed (%s); bundle stays "
                           "unreplied so the run-end fallback answers it", exc)
        else:
            bundle.reply_text = judgment.reply
            bundle.replied = True
            if judgment.full_turn:
                bundle.needs_full_turn = True
            _REPLY_BUDGET[session_key] = budget_used + 1
            # Spec §6: judged replies persist as assistant rows on SUCCESSFUL
            # send. (The user side needs nothing here — record_user_fragment's
            # DB half already persisted each fragment at arrival.)
            try:
                await _persist_assistant(turn=turn, response_text=judgment.reply)
            except Exception as exc:
                logger.warning(
                    "steering: assistant-side persist failed: %s", exc)

    if not steering.enqueue(session_key, bundle):
        return False          # queue full — caller falls back to a normal turn
    if dispatch is not None:
        _arm_deadline(session_key, bundle, dispatch, sessions_root=sessions_root)
    return True


def _answered_in_hand(bundle) -> bool:
    """The judge answered him for real (replied, no full_turn promised, and
    the run it steered was real work): nothing is owed beyond the record.
    One rule for the post-run sweep and the deadline fallback (2026-08-28 —
    the watchdog used to re-dispatch such bundles anyway). Batch 8, task 2a
    (ruling T2-3): a bundle she chose to stay quiet for is answered too."""
    if getattr(bundle, "quiet", False):
        return True
    return bool(
        getattr(bundle, "replied", False)
        and not getattr(bundle, "needs_full_turn", False)
        and getattr(bundle, "working_at_enqueue", True)
    )


def _arm_deadline(session_key: str, bundle, dispatch, *,
                  sessions_root=DEFAULT_SESSIONS_ROOT) -> None:
    async def _watch() -> None:
        try:
            rearms = 0
            while True:
                await asyncio.sleep(DEADLINE_SECONDS)
                if bundle not in steering.peek(session_key):
                    return      # gone from the queue — drained or swept
                if getattr(bundle, "sidecar_running", False):
                    return      # Phase B: a sidecar is answering it — on_done's custody
                if steering.get_run_phase(session_key) and rearms < DEADLINE_MAX_REARMS:
                    # The run is alive — mid-tool, most likely. The drain
                    # seam will transcribe this at its next boundary; a write
                    # from here would land inside the open round-trip.
                    rearms += 1
                    logger.info(
                        "steering: bundle waited %.0fs on %s but the run is "
                        "still alive; waiting (%d/%d)",
                        DEADLINE_SECONDS * rearms, session_key, rearms,
                        DEADLINE_MAX_REARMS,
                    )
                    continue
                break
            if not steering.remove(session_key, bundle):
                return          # gone from the queue — drained or swept
            logger.warning(
                "steering: bundle waited past %.0fs on %s (wedged run?); "
                "falling back to a normal turn",
                DEADLINE_SECONDS * (rearms + 1), session_key,
            )
            if _answered_in_hand(bundle):
                # He was answered; only the record is owed (the sweep's rule).
                if not getattr(bundle, "drained", False):
                    await _transcribe_bundle(session_key, bundle, sessions_root)
                return
            if bundle.replied and bundle.reply_text and not getattr(
                    bundle, "drained", False):
                # Her interim reply reached him on Discord; put the exchange
                # on the record BEFORE re-dispatching, so the fresh answer
                # can't contradict words she has no memory of. Transcription
                # marks the bundle drained, so _fallback_turn keeps the
                # prerecorded flag and the user side never doubles.
                await _transcribe_bundle(session_key, bundle, sessions_root)
            await dispatch(_fallback_turn(bundle))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("steering: deadline fallback failed: %s", exc)

    task = asyncio.create_task(_watch())
    _DEADLINE_TASKS.add(task)
    task.add_done_callback(_DEADLINE_TASKS.discard)


# Public alias (2026-08-04): the client's settle-time supersede fork arms the
# same watchdog for its finish-then-follow-up bundles.
arm_deadline = _arm_deadline


def _fallback_turn(bundle):
    """The turn to dispatch when a bundle falls back to a normal turn.

    A bundle that never DRAINED has no transcript entry — its arrival-time
    append was skipped in the steer lane (spec §4e) — so clear
    ``user_side_prerecorded`` and let the normal dispatch path append and
    persist it. A drained bundle keeps the flag: its words are already on
    the record, and re-appending would double them.
    """
    turn = bundle.turn
    if getattr(bundle, "drained", False):
        return turn
    md = getattr(turn, "metadata", None)
    if md is None or not getattr(md, "user_side_prerecorded", False):
        return turn
    try:
        from dataclasses import replace as _dc_replace
        return _dc_replace(
            turn, metadata=_dc_replace(md, user_side_prerecorded=False))
    except Exception:
        return turn


async def sweep_after_run(session_key: str, *, dispatch,
                          sessions_root=DEFAULT_SESSIONS_ROOT) -> int:
    """After a run ends, settle every leftover bundle's custody.

    Unanswered bundles become real turns, oldest first. REPLIED bundles are
    never re-dispatched (she already answered) — but only the ones a model
    boundary drained are on the record; a replied bundle whose run ended
    before any boundary (every one-call reply, and the §5.4 fast-run race —
    the 2026-07-28 api-key incident) is TRANSCRIBED here instead of dropped,
    so his words and her interim reply reach the transcript exactly once.
    Returns how many turns were dispatched."""
    reset_reply_budget(session_key)
    leftovers = steering.clear(session_key)
    n = 0
    for bundle in leftovers:
        if getattr(bundle, "sidecar_running", False):
            # Phase B (correction 3): a sidecar is still answering this — it
            # transcribes the exchange itself when it finishes (on_done).
            # Keep it queued so on_done can find it.
            steering.enqueue(session_key, bundle)
            continue
        if _answered_in_hand(bundle):
            if not getattr(bundle, "drained", False):
                await _transcribe_bundle(session_key, bundle, sessions_root)
            continue
        if bundle.replied and not getattr(bundle, "working_at_enqueue", True):
            # 2026-08-23: her holding line went to a message that arrived on a
            # run that was never real work — it cannot be the answer. Put the
            # exchange on the record, then answer him for real.
            logger.info("steering: replied bundle never steered real work on %s — re-dispatching as a full turn", session_key)
        if bundle.replied and not getattr(bundle, "drained", False):
            # full_turn: her holding line reached him — put the exchange on
            # the record BEFORE the real answer, so the full turn can't
            # contradict words she has no memory of.
            await _transcribe_bundle(session_key, bundle, sessions_root)
        try:
            await dispatch(_fallback_turn(bundle))
            n += 1
        except Exception as exc:
            logger.warning("steering: fallback dispatch failed: %s", exc)
    return n
