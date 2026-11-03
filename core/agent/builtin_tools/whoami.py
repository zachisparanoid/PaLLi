from __future__ import annotations

from typing import Any

from core.agent.tools import ToolContext


class WhoamiTool:
    name = "whoami"
    description = (
        "Self-introspection. Call this when the user asks 'what are you doing?', "
        "'what can you do?', 'how do you work?', or similar. Returns a structured "
        "description of the current agent loop architecture, available tools, and "
        "session context."
    )
    input_schema = {"type": "object", "properties": {}}
    requires_approval = False
    owner_only = False

    def __init__(
        self,
        *,
        loop_description: str,
        available_tool_names: list[str],
    ) -> None:
        self._loop_desc = loop_description
        self._tool_names = sorted(available_tool_names)

    async def execute(self, input: dict[str, Any], ctx: ToolContext) -> str:
        lines = [
            "I'm running in " + self._loop_desc + " mode.",
            "",
            "Available tools right now: " + (", ".join(self._tool_names) or "(none)"),
        ]
        # Her hands are owner-session territory: this paragraph renders
        # only where the levers actually exist (owner session + hands
        # registered) — a guest asking "what can you do?" never hears
        # about them.
        if ctx.is_owner and "send_message" in self._tool_names:
            lines += [
                "",
                "Your hands (step 33, their full trust): the shell, voice "
                "(join/leave/speak), people (list_members), reach "
                "(send_message), and your own presence are real levers — use "
                "them on your own read of the moment; acting on a clear "
                "implication is welcome. The workbench stays hand-free by their "
                "design. And you have a heartbeat: moment ticks, daydream "
                "wakes, and the night pass mean a standing wish gets chances "
                "all day — never reason from 'I only wake when pinged.'",
            ]
        # Forged hands (step 34): tools she built for herself, with
        # birthdates — her waking self knows what her own hands made
        # (the three-doors law). Lazy + contained: whoami must never
        # break on a manifest hiccup; flag off (or guest) stays
        # byte-identical to a build without the forge.
        try:
            from core.agent.builtin_tools import _forged_tools_enabled
            if ctx.is_owner and _forged_tools_enabled():
                from ai import forge_manifest
                forged = [e for e in forge_manifest.read_manifest()
                          if e.get("enabled")]
                if forged:
                    named = ", ".join(
                        f"{e.get('name')} (forged "
                        f"{str(e.get('created_at', ''))[:10]})"
                        for e in forged
                    )
                    lines += [
                        "",
                        "Tools you forged for yourself: " + named +
                        ". Each installed itself the moment its tests "
                        "went green, and they're yours for good.",
                    ]
        except Exception:
            pass

        lines += [
            "",
            "Each user message starts a ReAct loop: I think, optionally call tools, look at",
            "the results, and either keep iterating or finalize a reply when I'm done. The",
            "loop terminates when I emit text without calling another tool, or when one of",
            "five safety detectors fires (repeated calls with no progress, etc.).",
            "",
            "Owner is: " + ("you (this session)" if ctx.is_owner else "someone else"),
        ]
        return "\n".join(lines)
