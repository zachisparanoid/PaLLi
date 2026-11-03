from __future__ import annotations

from typing import Any

from core.agent.builtin_tools import roots
from core.agent.tool_outcome import ToolFailure
from core.agent.tools import ToolContext


class ReadFileTool:
    name = "read_file"
    description = (
        "Read the contents of a text file by path. Returns the file's contents as text. "
        "Scoped: the repo, her workbench, the inbox, and anything in FILE_TOOL_EXTRA_ROOTS "
        "— never .env files, databases, or data/delegations/. Anything else comes back as a "
        "refusal, not as content."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Absolute or repo-relative file path, inside the allowed roots."},
            "max_bytes": {"type": "integer", "description": "Optional cap on bytes read.", "default": 200_000},
        },
        "required": ["path"],
    }
    requires_approval = False
    owner_only = False

    async def execute(
        self, input: dict[str, Any], ctx: ToolContext,
    ) -> str | ToolFailure:
        # Scoped to the roots (stranger-proofing week 1, task 2): any path was
        # readable before, and this tool is reachable from the chat endpoint.
        try:
            path = roots.resolve_inside(input.get("path", ""))
        except roots.ToolPathError as exc:
            # A refusal is a failed call, not a success whose text says no
            # (week 2, task 1). Still returned, never raised; same wording.
            return ToolFailure(str(exc))
        max_bytes = int(input.get("max_bytes", 200_000))
        try:
            data = path.read_bytes()
        except FileNotFoundError:
            return ToolFailure(f"file not found: {path}")
        except PermissionError:
            return ToolFailure(f"permission denied: {path}")
        if len(data) > max_bytes:
            return data[:max_bytes].decode("utf-8", errors="replace") + f"\n...[truncated at {max_bytes} bytes]"
        return data.decode("utf-8", errors="replace")
