"""Mid-task steering state: the per-session bundle queue and the now-doing
descriptor.

Spec: docs/superpowers/specs/2026-07-26-converse-while-working-design.md §4a/§4d.

This module is deliberately the ONLY place that holds cross-module steering
state. The Discord client enqueues, the ReAct runner drains, and both read the
now-doing descriptor — none of them reach into each other. Pure and
synchronous: no I/O, no LLM, no asyncio primitives, so it is trivially testable
and cannot deadlock the reply path.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any


logger = logging.getLogger(__name__)

# Max bundles held for one session. Beyond this, enqueue() refuses and the
# caller falls back to a normal turn — we never drop the owner's words.
QUEUE_CAP = 20


@dataclass
class SteerBundle:
    """One coalesced burst of owner input that arrived mid-run.

    ``reply_text`` is her judged interim reply IF one was actually delivered;
    ``replied`` stays False for a judged hold, a failed send, or a bundle that
    was never judged at all. Run-end handling keys off ``replied``: replied
    bundles are already answered (record only), unreplied ones must still get a
    real turn (spec §4f).

    ``drained`` flips True the moment the runner writes the bundle into the
    transcript at a model boundary. Fallback dispatches key off it: a drained
    bundle's words are already on the record (keep the prerecorded flag), an
    undrained one's are nowhere yet (strip it so the normal turn appends).

    ``needs_full_turn`` marks a judged ``full_turn`` escalation (2026-08-04):
    her holding line reached him (``replied`` True), but the message deserves
    a real turn — the run-end sweep dispatches it instead of closing it out.
    """
    turn: Any
    reply_text: str | None = None
    enqueued_at: float = 0.0
    replied: bool = False
    drained: bool = False
    needs_full_turn: bool = False
    # Step 27 Phase B: a sidecar is answering this bundle right now — the
    # drain, the sweep and the watchdog leave it alone; the sidecar hands
    # its reply back through on_done.
    sidecar_running: bool = False
    # 2026-08-23: was the run actually WORKING (a real tool call) when this
    # bundle was taken? handle_mid_task records it; a replied bundle that
    # never steered real work is re-dispatched by the sweep — a holding line
    # cannot answer a request. Default True = "unknown, assume real work",
    # which keeps every older caller's contract (replied ⇒ transcribe only).
    working_at_enqueue: bool = True
    # Batch 8, task 2a (ruling T2-3): she chose to stay quiet for these words
    # (a talk sidecar's reply called stay_quiet). Answered, with nothing sent:
    # the drain, the sweep and the watchdog put the words on the record and
    # never ask them again. Only ever set with companion.she_decides on.
    quiet: bool = False


_QUEUES: dict[str, list[SteerBundle]] = {}
_NOW_DOING: dict[str, str] = {}


def enqueue(session_key: str, bundle: SteerBundle) -> bool:
    """Queue a bundle for the run on ``session_key``. False when the queue is
    full — the caller must then fall back to a normal turn rather than drop it."""
    q = _QUEUES.setdefault(session_key, [])
    if len(q) >= QUEUE_CAP:
        logger.warning(
            "steering: queue full for %s (cap=%d); caller must fall back",
            session_key, QUEUE_CAP,
        )
        return False
    q.append(bundle)
    return True


def drain(session_key: str) -> list[SteerBundle]:
    """Take everything queued, in arrival order, and empty the queue."""
    q = _QUEUES.pop(session_key, [])
    return list(q)


def peek(session_key: str) -> list[SteerBundle]:
    """Read the queue without consuming it."""
    return list(_QUEUES.get(session_key, []))


def has_pending(session_key: str) -> bool:
    return bool(_QUEUES.get(session_key))


def oldest_age(session_key: str, now: float) -> float:
    """Seconds the head bundle has been waiting; 0.0 when nothing is queued."""
    q = _QUEUES.get(session_key)
    if not q:
        return 0.0
    return max(0.0, now - q[0].enqueued_at)


def remove(session_key: str, bundle: SteerBundle) -> bool:
    """Pull one specific bundle out (deadline fallback). True if it was there."""
    q = _QUEUES.get(session_key)
    if not q:
        return False
    for i, b in enumerate(q):
        if b is bundle:
            q.pop(i)
            if not q:
                _QUEUES.pop(session_key, None)
            return True
    return False


def clear(session_key: str) -> list[SteerBundle]:
    """Empty the queue and return what was in it (stop / run-end paths)."""
    return drain(session_key)


def set_now_doing(session_key: str, text: str) -> None:
    """A plain-text stand-in for the work note. Since 10-12 the runner
    publishes a WorkProgress (set_work_progress), which get_now_doing
    prefers; this stays for callers and tests that only have a line."""
    _NOW_DOING[session_key] = text


# The owner, 10-12: the facts PaLLi reads when it talks mid-task
# (core/agent/work_progress.WorkProgress). The runner is the one writer;
# readers get it rendered at read time through get_now_doing.
_PROGRESS: dict[str, Any] = {}


def _now() -> float:
    return time.monotonic()


def set_work_progress(session_key: str, progress: Any) -> None:
    _PROGRESS[session_key] = progress


def get_work_progress(session_key: str) -> Any:
    return _PROGRESS.get(session_key)


def get_now_doing(session_key: str) -> str:
    progress = _PROGRESS.get(session_key)
    if progress is not None:
        return progress.render(_now())
    return _NOW_DOING.get(session_key, "")


def get_recent(session_key: str) -> str:
    return str(getattr(_PROGRESS.get(session_key), "recent", "") or "")


def clear_now_doing(session_key: str) -> None:
    _NOW_DOING.pop(session_key, None)
    _PROGRESS.pop(session_key, None)


# ── run phase (phase-owned routing, 2026-08-04) ─────────────────────────
#
# The runner is the sole writer; the Discord fork is the reader. "prep"
# covers everything before the first reply-model call (the ~15s window
# where the 08-04 incidents lived); "generating" covers model calls.
# is_working() — one real tool call — is the deterministic gate that
# separates a conversational reply from genuine work: only work earns the
# steer lane.

_RUN_PHASE: dict[str, str] = {}
_TOOL_CALLS: dict[str, int] = {}


def set_run_phase(session_key: str, phase: str) -> None:
    _RUN_PHASE[session_key] = phase


def get_run_phase(session_key: str) -> str:
    return _RUN_PHASE.get(session_key, "")


def note_tool_calls(session_key: str, n: int) -> None:
    _TOOL_CALLS[session_key] = _TOOL_CALLS.get(session_key, 0) + max(0, int(n))


def get_tool_call_count(session_key: str) -> int:
    return _TOOL_CALLS.get(session_key, 0)


def is_working(session_key: str) -> bool:
    return get_tool_call_count(session_key) >= 1


# Step 27 · Duplex (Phase A) — three more per-session facts, cleared with
# the run: the cooperative stop flag (spec §3), her time estimate for the
# overrun sense (§2), and the presence activity she set during the run so
# the runner can clear her signage when the work ends (§5).
_STOP: set[str] = set()
_EXPECT: dict[str, float] = {}
_PRESENCE: dict[str, str] = {}


def request_stop(session_key: str) -> None:
    _STOP.add(session_key)


def stop_requested(session_key: str) -> bool:
    return session_key in _STOP


def set_expectation(session_key: str, minutes: float) -> None:
    _EXPECT[session_key] = float(minutes)


def get_expectation(session_key: str) -> float | None:
    return _EXPECT.get(session_key)


def note_presence_activity(session_key: str, activity: str) -> None:
    _PRESENCE[session_key] = str(activity or "")


def pop_presence_activity(session_key: str) -> str | None:
    return _PRESENCE.pop(session_key, None)


# Step 27 Phase B (spec §3): "hold on" — a resumable stop. The transcript IS
# the state; this record just remembers that a run is waiting on him. The
# REQUEST is cleared with the run; the paused RECORD outlives it (a paused
# run has ended) and is closed by a resume, a stop, or the expiry.
from dataclasses import dataclass as _dc
import time as _time

_PAUSE_REQ: set[str] = set()


@_dc
class PausedRun:
    session_key: str
    now_doing: str
    paused_at: float


_PAUSED: dict[str, PausedRun] = {}


def request_pause(session_key: str) -> None:
    _PAUSE_REQ.add(session_key)


def pause_requested(session_key: str) -> bool:
    return session_key in _PAUSE_REQ


def set_paused(session_key: str, now_doing: str) -> None:
    _PAUSED[session_key] = PausedRun(session_key, str(now_doing or ""), _time.time())


def get_paused(session_key: str) -> PausedRun | None:
    return _PAUSED.get(session_key)


def clear_paused(session_key: str) -> None:
    _PAUSED.pop(session_key, None)


def clear_run_state(session_key: str) -> None:
    _RUN_PHASE.pop(session_key, None)
    _TOOL_CALLS.pop(session_key, None)
    clear_now_doing(session_key)
    _STOP.discard(session_key)
    _EXPECT.pop(session_key, None)
    _PRESENCE.pop(session_key, None)
    _PAUSE_REQ.discard(session_key)
