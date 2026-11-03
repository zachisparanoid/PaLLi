"""Step 27 · Duplex (Phase A) — the two mid-task acts (spec §2).

chime_in: say something to him while you work — a find, a fork, a problem,
something you'd say across the room. ask_him: you need something only he
can give; ask, and the run goes on (what you do next is yours). Both ride
duplex.speak_mid_task → the arbiter's release leg (spoken in voice, text
otherwise). Owner-only; registered only when companion.duplex is on;
never at the workbench (the wall drafts, it does not talk to him).
"""
from __future__ import annotations

import logging
from typing import Any

from core.agent.tools import ToolContext

logger = logging.getLogger(__name__)


def enabled() -> bool:
    """companion.duplex gate (step 27) — the registry reads it through here.

    Asks ``core.agent.duplex.duplex_enabled`` each time it is called (never
    cached, never bound at import), so the tests/conftest.py off-guard that
    patches that name holds here too; fail-closed."""
    try:
        from core.agent import duplex as _dx
        return bool(_dx.duplex_enabled())
    except Exception:
        return False


def _door(ctx: ToolContext) -> dict[str, str]:
    """The ``platform=`` keyword for the door this run is behind, or nothing
    (telegram door, 2026-09-14, task 1).

    Discord is ``speak_mid_task``'s default, and a call site does not name a
    default: on Discord the call stays the four arguments it has always
    been, and only another door says which one it is. An unknown or blank
    surface is Discord's old behaviour, so it says nothing either."""
    from core.turn.recall import _platform_for_surface

    platform = _platform_for_surface(getattr(ctx, "surface", "") or "")
    return {"platform": platform} if platform and platform != "discord" else {}


class ChimeInTool:
    name = "chime_in"
    description = (
        "Say something to them while you are in the middle of a task: "
        "whatever you want them to hear, whenever you choose. It goes out "
        "as-is (spoken if they're in voice with you). Under 300 characters."
    )
    input_schema = {
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
    }
    requires_approval = False
    owner_only = True

    async def execute(self, input: dict[str, Any], ctx: ToolContext) -> str:
        from core.agent import duplex as _dx
        text = " ".join(str(input.get("text") or "").split())
        if not text:
            return "Nothing to say — that's fine; keep working."
        if len(text) > _dx.CHIME_MAX_CHARS:
            return (f"That's longer than a chime — keep it under "
                    f"{_dx.CHIME_MAX_CHARS} characters, or say it when the work is done.")
        out = await _dx.speak_mid_task(
            owner_user_id=int(ctx.turn_owner_id),
            channel_id=int(getattr(ctx, "channel_id", 0) or 0),
            text=text, origin="duplex_chime", **_door(ctx),
        )
        medium = str(out.get("medium") or "text")
        return f"Said ({medium}): {text[:80]}"


class AskHimTool:
    name = "ask_him"
    description = (
        "You need something only they can give — a captcha, a decision, "
        "credentials, a preference. Ask them now, mid-task; the question goes "
        "out as-is. This returns immediately: keep going on whatever doesn't "
        "depend on their answer, or finish here saying what you're waiting on "
        "— your call."
    )
    input_schema = {
        "type": "object",
        "properties": {"question": {"type": "string"}},
        "required": ["question"],
    }
    requires_approval = False
    owner_only = True

    async def execute(self, input: dict[str, Any], ctx: ToolContext) -> str:
        from core.agent import duplex as _dx
        question = " ".join(str(input.get("question") or "").split())
        if not question:
            return "Ask them what? Say the question itself."
        if len(question) > _dx.CHIME_MAX_CHARS:
            return (f"Keep the question under {_dx.CHIME_MAX_CHARS} characters "
                    f"— one thing you need from them.")
        door = _door(ctx)
        await _dx.speak_mid_task(
            owner_user_id=int(ctx.turn_owner_id),
            channel_id=int(getattr(ctx, "channel_id", 0) or 0),
            text=question, origin="duplex_ask", **door,
        )
        try:
            from ai import present_moment as _pm
            _pm.notice("task_problem", what="asked_him")
        except Exception:  # noqa: BLE001
            pass
        if _dx.duplex_enabled():
            # Phase B (spec §2, his call 1): park the blocked step on her
            # thread board so it resurfaces through the board's own machinery
            # if he answers after the run has ended. While the run is alive,
            # his answer is an ordinary steer bundle — nothing else to do.
            try:
                from core.database import brain
                from core.open_threads import ThreadKind
                # The parked FOLLOWUP's source_id IS the session key: a
                # Telegram question parked under discord:<chat> resurfaces
                # in a room it was never asked in.
                _platform = door.get("platform", "discord")
                _sk = f"{_platform}:{int(getattr(ctx, 'channel_id', 0) or 0)}"
                await brain.add_open_thread(
                    owner_user_id=int(ctx.turn_owner_id),
                    topic_summary=question[:200],
                    why_it_matters="I asked them mid-task and kept going; the step waits on their answer",
                    kind=ThreadKind.FOLLOWUP.value,
                    urgency="normal",
                    source_id=f"task:{_sk}",
                )
            except Exception as exc:  # noqa: BLE001 — parking is a nicety, never the run's problem
                logger.warning("ask_him: parking failed (%s)", exc)
        return ("Asked. Keep going on what doesn't depend on their answer, or "
                "finish here saying what you're waiting on — your call.")


def build_duplex_tools() -> list:
    return [ChimeInTool(), AskHimTool()]
