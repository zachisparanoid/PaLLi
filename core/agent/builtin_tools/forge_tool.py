"""forge_tool (step 34, spec §2): her judgment starts the forge.

SHE decides when a capability gap warrants building — a meaning-decision,
hers (owner rule 2026-07-16); there are no keyword heuristics here and no
approval surface anywhere downstream (spec §0, owner 04:11 verbatim: "no
confirmation necessary"). execute() claims the one-project budget, spawns
the background forge session, and returns immediately with an honest
receipt she can speak from — "can't yet, building it now" is true the
moment she says it.
"""
from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

from ai import forge_manifest
from core.agent.tools import ToolContext

logger = logging.getLogger(__name__)

_NAME_MAX = 40


def _sanitize_name(raw: str) -> str:
    """Tool names become file/module names — mechanical safety, not a gate.
    Lowercase, [a-z0-9_], collapsed; empty result = unusable."""
    slug = re.sub(r"[^a-z0-9_]+", "_", str(raw or "").strip().lower())
    return slug.strip("_")[:_NAME_MAX]


class ForgeTool:
    name = "forge_tool"
    description = (
        "Forge a new tool for yourself when the owner asks for something you "
        "genuinely can't do with the tools on your belt. Check your belt "
        "first — never forge what an existing tool already covers. This "
        "starts a background build (your workbench drafts it, tests must "
        "pass, then it installs itself onto your belt and the answer "
        "follows); it takes minutes, and you should tell them so plainly. "
        "One forge at a time."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": (
                    "short snake_case name for the new tool, e.g. "
                    "'image_convert'"
                ),
            },
            "purpose": {
                "type": "string",
                "description": "one plain sentence: what the tool will do",
            },
            "original_ask": {
                "type": "string",
                "description": (
                    "their request, verbatim — the forge fulfills exactly this "
                    "once the tool is live"
                ),
            },
        },
        "required": ["name", "purpose", "original_ask"],
    }
    requires_approval = False
    owner_only = True

    async def execute(self, input: dict[str, Any], ctx: ToolContext) -> str:
        raw_name = str(input.get("name", "") or "")
        purpose = str(input.get("purpose", "") or "").strip()
        original_ask = str(input.get("original_ask", "") or "")

        name = _sanitize_name(raw_name)
        if not name:
            return (
                "forge not started: the tool needs a usable snake_case name "
                "(letters, digits, underscores). Pick one and call again."
            )
        if not original_ask.strip():
            return (
                "forge not started: pass their ask verbatim in original_ask — "
                "the forge fulfills exactly that once the tool is live."
            )

        # One project at a time — a budget in code (spec §2.5), not a queue
        # engine: the second ask waits as conversation, not machinery.
        if not forge_manifest.claim_active(name, original_ask):
            active = forge_manifest.active_project() or {}
            busy_name = str(active.get("name", "another tool"))
            return (
                f"forge busy: {busy_name!r} is still on the anvil — one "
                "forge at a time. Tell them this one queues up next; start "
                "it again when the current forge finishes."
            )

        try:
            from ai.forge import start_forge_project

            start_forge_project(
                name=name,
                purpose=purpose,
                original_ask=original_ask,
                origin_channel_id=int(getattr(ctx, "channel_id", 0) or 0),
                surface=str(getattr(ctx, "surface", "") or ""),
            )
        except Exception:
            # Never leave a stuck marker behind a failed spawn — the anvil
            # frees itself and the failure is loud in the log AND the reply.
            forge_manifest.clear_active()
            logger.exception("forge spawn failed for %r", name)
            return (
                f"forge failed to start for {name!r} — the build session "
                "couldn't spawn (this is a bug, and it's in the log). "
                "Tell them plainly it didn't start."
            )

        logger.info("forge started: %r (ask: %.120s)", name, original_ask)
        return (
            f"forge started: {name} — building it in the background now. "
            "Minutes, not seconds: say you can't do this YET and that "
            "you're building the tool for it; the answer follows on this "
            "channel once tests pass and it installs itself onto your belt "
            "(or you'll say plainly that it didn't come together)."
        )
