"""Step 27 · Duplex — she works and talks at the same time (Phase A).

Spec: docs/superpowers/specs/2026-08-27-duplex-design.md. §0 is the
owner's law: silence by default while working; speaking mid-task is an
ACT (chime_in / ask_him); her moment loop may reach him while a run is
in flight; a problem or an overrun is a SENSE; a stop lands at a safe
tool boundary. Everything reads the flag through ``duplex_enabled()`` —
call it via the module attribute (``_dx.duplex_enabled()``) so the test
guard in tests/conftest.py can hold it off.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Awaitable, Callable, Dict, Optional

logger = logging.getLogger(__name__)

CHIME_MAX_CHARS = 300


def duplex_enabled() -> bool:
    """companion.duplex, fail-closed (no snapshot / any error = off)."""
    try:
        from core.turn.companion_flags import companion_settings
        c = companion_settings()
        return c is not None and bool(getattr(c, "duplex", False))
    except Exception:
        return False


def _knob(name: str, default: Any) -> Any:
    try:
        from core.turn.companion_flags import companion_settings
        c = companion_settings()
        return getattr(c, name, default) if c is not None else default
    except Exception:
        return default


def render_duplex_block(tools: bool = True) -> str:
    """The law of working alongside them — a system-prompt block (spec §2).
    "" when the flag is off: a flag-off prompt is byte-identical.

    Batch 7 (4.3): said as her, in the first person. What code enforces is
    stated once as a plain fact, never as a ban: a working note never
    reaches them (the runner delivers only the final reply), the stamp is
    the system's (an imitated one is stripped at the model boundary,
    DEFECT_RECORD §20), and an empty promise is stopped by the promise
    gate (2026-09-11), which shares this block's flag.

    ``tools`` False (step 2, add-on a): a turn that offers no tools (a
    guest's) is never told they are allowed.

    The owner, 2026-10-12: whether, when and how she speaks while she
    works is hers. The block says how a word reaches them mid-task and
    lists no reasons, bans no phrases and hands her no lines ("palli is
    its own entity and should be able to converse and acknowledge with
    whatever language it sees best fit")."""
    if not duplex_enabled():
        return ""
    return (
        "## When I'm working\n"
        "What I write between tool calls is a working note: it stays in my "
        "record, unseen by them, and the system stamps older ones "
        "`[system: working note …]`. While I work I speak to them with "
        "`chime_in`, whenever and however I choose; `ask_him` for what only "
        "they can give, then I go on with what doesn't depend on them, or "
        "finish by saying what I'm waiting on. I say something is happening "
        "only if I started it this turn; otherwise I do it now"
        + (" (tools are always allowed)" if tools else "")
        + ", say what I need, or say I can't. They "
        "can talk to me about anything while I work, and I answer as myself. "
        "For long work I set my presence with `set_presence` and a rough "
        "length with `expect_minutes`, which is how I notice it running "
        "over. My answer goes in my final message, after the tools, in full, "
        "even if a note drafted it; they haven't seen the note.\n"
    )


async def overrun_watch(
    session_key: str,
    *,
    started_at: float,
    factor: float,
    default_minutes: float,
    now: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    tick_seconds: float = 5.0,
) -> None:
    """The time sense (spec §2, the owner's call 3): fire ``task_overrun``
    once elapsed passes expected × factor, and again at each further
    multiple. Her estimate is read every tick (she may set it late, via
    set_presence's expect_minutes); none → ``default_minutes``. Structure
    only into notice(): minutes and the multiple, never text. Runs until
    the runner's finally cancels it. ``now``/``sleep`` are injectable so
    the test clock is not the wall clock."""
    from core.agent import steering as _st
    from ai import present_moment as _pm
    fired = 0
    while True:
        await sleep(tick_seconds)
        expected = _st.get_expectation(session_key) or float(default_minutes)
        elapsed_min = (now() - started_at) / 60.0
        threshold = expected * float(factor) * (fired + 1)
        if elapsed_min >= threshold:
            fired += 1
            try:
                _pm.notice("task_overrun", expected_min=expected,
                           elapsed_min=round(elapsed_min, 1), multiple=fired)
            except Exception:  # noqa: BLE001 — never into the runner
                pass


async def speak_mid_task(
    *, owner_user_id: int, channel_id: int, text: str, origin: str,
    mid_task: bool = True, platform: str = "discord",
) -> Dict[str, Any]:
    """Her mid-task word to him, through the ONE release leg (spec §5): the
    arbiter's medium rule — spoken when he's in her voice channel, text
    otherwise, written trace either way (step 32 verbatim). No gate but her
    judgment; custody is record_outbound's, as for every word she sends.

    ``mid_task=True`` (a chime/ask from INSIDE a run): the tool round-trip
    is the transcript record, so the custody leg skips its own line.
    ``mid_task=False`` (Phase B: the pause-expiry line — no run alive): the
    line goes on the record like any word of hers.

    ``platform`` (telegram door, 2026-09-14, task 1) is the door the run is
    behind. The spoken medium IS a Discord voice session (step 32), so only
    a Discord-door act is offered it — a live voice moment is not a reason
    to say a Telegram chime aloud into a room it was never addressed to."""
    from ai import arbiter as _arb
    platform = str(platform or "discord").strip().lower() or "discord"
    vm = await _arb._probe_voice_moment() if platform == "discord" else None
    return await _arb._release_outbound(
        owner_user_id=int(owner_user_id), channel_id=int(channel_id),
        text=str(text), origin=str(origin), extra={"mid_task": bool(mid_task)}, vm=vm,
        platform=platform,
    )
