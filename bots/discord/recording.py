# bots/discord/recording.py
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from core.agent.runner import _derive_session_key, _open_session_with_user_message
from core.turn.persistence import _persist_user_side_with_content

logger = logging.getLogger(__name__)

# Strong refs so detached DB-persist tasks aren't GC'd mid-write (mirrors
# dispatch._BACKGROUND_PERSIST_TASKS, the Bug-1 retention pattern).
_BACKGROUND_PERSIST_TASKS: set[asyncio.Task] = set()


async def record_user_fragment(
    turn: Any, *, sessions_root: Path, transcript: bool = True,
) -> None:
    """Record one inbound fragment on arrival: transcript (shielded, so a
    later barge-in can't erase it) + DB (detached, best-effort). Text turns
    record turn.content; image/file turns record the '[image attached]' or
    '[file attached]' placeholder that intake already put on turn.content.

    ``transcript=False`` skips the transcript half only (converse-while-
    working, spec §4e): a steered mid-run fragment must NOT be written into
    the session file at arrival — during a run the RUNNER is the sole
    transcript writer, and it appends the coalesced bundle at the next model
    boundary instead. The DB half below still fires either way (spec §6:
    bundle user-rows persist at enqueue via this existing helper).

    The detached DB persist is SKIPPED for the exact '[image attached]' or
    '[file attached]' placeholder: the coalesced turn's enrich (runner.run_turn)
    persists a RICH user row with the vision/file analysis baked in, and recall
    has no dedup — a bare placeholder row here would double it. The shielded
    transcript append still happens (that's the barge-in loss protection).
    Captioned images/files are out of scope; only the exact bare placeholder
    is skipped."""
    session_key = _derive_session_key(turn)
    user_text = str(getattr(turn, "content", "") or "")
    if transcript:
        try:
            await asyncio.shield(
                _open_session_with_user_message(
                    session_key, sessions_root, user_text,
                    speaker=getattr(getattr(turn, "metadata", None), "speaker", None),
                )
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("record_user_fragment: transcript append failed: %s", exc)

    if user_text.strip() in ("[image attached]", "[file attached]"):
        return

    try:
        task = asyncio.create_task(_persist_user_side_with_content(turn, user_text))
        _BACKGROUND_PERSIST_TASKS.add(task)
        task.add_done_callback(_BACKGROUND_PERSIST_TASKS.discard)
    except Exception as exc:
        logger.warning("record_user_fragment: DB persist scheduling failed: %s", exc)
