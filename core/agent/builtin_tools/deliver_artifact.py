from __future__ import annotations

from typing import Any

from core.agent.builtin_tools import roots
from core.agent.tool_outcome import ToolFailure
from core.agent.tools import ToolContext


class DeliverArtifactTool:
    name = "deliver_artifact"
    description = (
        "Mark a local file path as an artifact to deliver to the user (image, log, etc.). "
        "Hands the path to the turn; the surface attaches the file to the reply. "
        "Scoped: the repo, her workbench, the inbox, and anything in "
        "FILE_TOOL_EXTRA_ROOTS — never .env files, databases, or data/delegations/."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Absolute path of the file to deliver, inside the allowed roots."},
            "caption": {"type": "string", "description": "Optional caption text.", "default": ""},
        },
        "required": ["path"],
    }
    requires_approval = False
    owner_only = False

    async def execute(
        self, input: dict[str, Any], ctx: ToolContext,
    ) -> str | ToolFailure:
        # Scoped to the roots (stranger-proofing week 1, task 2) — this tool
        # hands the file itself to the surface, so it was the wider hole.
        try:
            path = roots.resolve_inside(input.get("path", ""))
        except roots.ToolPathError as exc:
            # A refusal is a failed call, not a success whose text says no
            # (week 2, task 1). Still returned, never raised; same wording.
            return ToolFailure(str(exc))
        if not path.exists():
            return ToolFailure(f"artifact not found: {path}")
        caption = input.get("caption", "")
        # Week 2 task 2: THIS is the delivery — the path handed over on the
        # turn's ToolContext, which the runner reads. The sentence below is
        # for her to read, not for anything to parse, so no tool can counter-
        # feit a delivery by printing the right words. And a context with
        # nowhere to hand the path is a call that ran and did not work: to
        # answer "queued" while queueing nothing would be the very lie this
        # tool was rewritten to stop telling.
        deliveries = getattr(ctx, "deliveries", None)
        if not isinstance(deliveries, list):
            return ToolFailure("no delivery channel on this turn")
        deliveries.append(str(path))
        sentence = f"queued for delivery: {path}"
        if caption:
            sentence += f"\n{caption}"
        return sentence
