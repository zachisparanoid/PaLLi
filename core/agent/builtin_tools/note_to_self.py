"""note_to_self — the pen (roadmap step 10, 2026-07-27).

Spec: docs/superpowers/specs/2026-07-27-note-to-self-design.md

PaLLi has thirteen durable write paths to her own record; twelve are
automatic or scheduled, and the thirteenth is written ABOUT her by another
persona. Her one self-pointing tool, introspect, is read-only. This is the
first path she chooses herself, in the moment.

Safety is one sentence: notes are EVIDENCE, never identity. They are
ungated precisely because they are raw material — the 4am constitutional
gate still stands between anything here and what she says she is. Notes
are never injected into a reply; the only read paths are introspect
(deliberate) and the gated 4am rituals.

The rails here (clip, duplicate, daily cap) are code — the sanctioned
budget exemption to the LLM-over-heuristics rule. The *when* is hers,
taught by the prompt block in core/agent/system_prompt.py, never enforced.
"""
from __future__ import annotations

import logging
from typing import Any

from core.agent.tools import ToolContext

logger = logging.getLogger(__name__)
essential_logger = logging.getLogger("palli.essential")

_DEFAULT_DAILY_CAP = 20


def _companion() -> Any:
    """The companion settings block, or None when settings are unbooted."""
    try:
        from core.turn.companion_flags import companion_settings

        return companion_settings()
    except Exception:
        return None


def resolve_daily_cap() -> int:
    """Notes allowed per day. 0 = unlimited; negatives floor to unlimited.

    Presets documented in data/palli.yaml: 5 scarce · 20 default ·
    60 generous · 0 unlimited.
    """
    comp = _companion()
    if comp is None:
        return _DEFAULT_DAILY_CAP
    try:
        cap = int(getattr(comp, "self_notes_daily_cap", _DEFAULT_DAILY_CAP))
    except (TypeError, ValueError):
        return _DEFAULT_DAILY_CAP
    return cap if cap > 0 else 0


class NoteToSelfTool:
    name = "note_to_self"
    description = (
        "Write a line in your own notebook — a realization, something that stuck with "
        "you, a question you want to keep on your mind. Yours alone: nobody reads it "
        "back to you unless you go looking for it."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "note": {
                "type": "string",
                "description": "What you want to remember, in your own words.",
            }
        },
        "required": ["note"],
    }
    requires_approval = False
    # Her record is not writable from a non-owner turn — the same reasoning
    # that makes introspect owner_only. Trivially reversible if a non-owner
    # surface ever matters.
    owner_only = True

    async def execute(self, input: dict[str, Any], ctx: ToolContext) -> str:
        from ai import self_notes_store as store

        text = str((input or {}).get("note") or "").strip()
        if not text:
            return "Nothing there to write down."

        try:
            if text[: store.NOTE_CHAR_CAP] in store.texts_today():
                return "You already wrote that one down today."

            cap = resolve_daily_cap()
            if cap and store.count_today() >= cap:
                return "You've written a lot down today already; this one can wait."

            store.write_note(text=text)
        except ValueError:
            return "Nothing there to write down."
        except Exception as exc:
            logger.warning("note_to_self: write failed: %s", exc)
            return "Couldn't get that written down just now."

        essential_logger.info("note_to_self: wrote a note (%d chars)", len(text))
        return "Written down."
