# core/agent/tools.py
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Protocol, runtime_checkable

from core.agent.payload_masking import unmask_obj
from core.agent.session import ToolResultBlock, ToolUseBlock
from core.agent.tool_outcome import ToolFailure


logger = logging.getLogger(__name__)


_REPO_ROOT = Path(__file__).resolve().parents[2]


def _resolve_max_bytes() -> int:
    raw = os.environ.get("TOOL_RESULT_MAX_BYTES")
    if not raw:
        return 10_240
    try:
        n = int(raw)
        return n if n > 0 else 10_240
    except ValueError:
        return 10_240


def _resolve_sidecar_root() -> Path:
    raw = os.environ.get("TOOL_RESULT_SIDECAR_ROOT")
    if raw:
        return Path(raw).resolve()
    return _REPO_ROOT / "data" / "agent_sessions" / "tool_results"


def _maybe_truncate(content: str, tool_use_id: str) -> str:
    max_bytes = _resolve_max_bytes()
    encoded = content.encode("utf-8")
    if len(encoded) <= max_bytes:
        return content

    sidecar_root = _resolve_sidecar_root()
    try:
        sidecar_path = (sidecar_root / f"{tool_use_id}.txt").resolve()
        sidecar_path.parent.mkdir(parents=True, exist_ok=True)
        sidecar_path.write_text(content, encoding="utf-8")
        sidecar_loc = str(sidecar_path)
    except OSError as exc:
        logger.warning("sidecar write failed for %s: %s", tool_use_id, exc)
        sidecar_loc = "<sidecar write failed>"

    head = encoded[:max_bytes].decode("utf-8", errors="replace")
    return (
        f"{head}\n"
        f"...\n"
        f"[result truncated; full output saved to {sidecar_loc}]"
    )


@dataclass(frozen=True)
class ToolContext:
    turn_owner_id: int
    is_owner: bool
    # Phase B / B3 step 3 (2026-05-09): the surface the user is on for
    # this turn (e.g. "discord_text", "voice_local", "dashboard"). Tools
    # that schedule or query reminders map this through
    # ``core.turn.recall.platform_for_surface`` so reminder rows land
    # under the same platform string ``list_user_scheduled_reminders``
    # later queries with. Defaults to "" so existing call sites that
    # haven't been updated still construct ToolContext successfully;
    # tools treat an empty surface as the dashboard fallback to preserve
    # pre-fix behavior. Resolves TURN-MED-4, RA-MED-6, RA-MED-7,
    # QUE-CRIT-2. create_reminder no longer files by surface (fix plan
    # 5.8): a chat surface keeps its chat, any other goes to the private
    # chat he used last (create_reminder._resolve_door), else (since
    # 2026-10-10) to the web page.
    surface: str = ""
    # 2026-07-10: the channel the turn arrived on (turn.metadata.channel_id).
    # create_reminder threads it into the reminder row (channel_id +
    # surface_id) so delivery has an address — rows written with
    # channel_id=0/surface_id="" were undeliverable. Defaults to 0 so
    # existing ToolContext call sites keep constructing.
    channel_id: int = 0
    # Week 2 task 2 (2026-09-10): the turn's delivery hand-off. The runner
    # builds ONE ToolContext per turn (core/agent/runner.py:487) and passes
    # it to every dispatch, so a tool that means to send a file appends its
    # resolved path here and the runner reads the list — a delivery is a
    # fact a tool hands over, never a phrase spotted in tool output. Frozen
    # freezes the binding, not the list. Defaulted, and last, so every
    # existing call site still constructs. THE CONTRACT for any tool that
    # joins this: append ONLY after every check that can fail has passed —
    # the runner delivers what is in the list, so a path appended and then
    # failed on would still be sent.
    deliveries: list[str] = field(default_factory=list)
    # Batch 8, task 10 (B8-5, ruling T10-3): the turn's voice messages —
    # OGG/Opus bytes her voice_note tool made, handed to the door the same
    # way as ``deliveries``: the door sends them WITH her reply (a Discord
    # audio attachment, a Telegram voice note), and a run that chooses quiet
    # sends none of them. Same contract: append only after every check.
    voice_notes: list[bytes] = field(default_factory=list)
    # Second read F8 (2026-10-04): the words each of those voice messages
    # speaks, in the same order, so the runner can put them in her
    # transcript when a voice message is all she sent (beside a quiet choice).
    # R15, Group V: the words come first now. voice_note sets one entry (the
    # last call wins) and makes nothing; the runner makes the one note from
    # it when the reply is finished (core/agent/voice_note_final.py). The
    # same contract: set only after every check.
    voice_note_words: list[str] = field(default_factory=list)
    # Second read F9 (2026-10-04): the owner's own words in this run — the
    # turn's message on an owner's conversation turn, and any words of theirs
    # drained in mid-run — never a guest's line, never history. A tool that
    # saves something as the owner's (room_note) checks its words are here.
    # Empty on every other run.
    owner_words: list[str] = field(default_factory=list)
    # R15, memory that reaches (T13, W3): what search_what_we_said gave
    # this reply, for the runner's line of what the reply drew on
    # (core/reply_sources.py; the owner's record, never a chat): the dated
    # lines it returned, the query of each search it ran, and each search's
    # counts ({hits, thin, rounds, calls, seconds}, as its log line says).
    # The ``deliveries`` contract: append only once the result stands.
    # Empty on every run that never searched.
    record_lines: list[str] = field(default_factory=list)
    record_queries: list[str] = field(default_factory=list)
    record_looks: list[dict[str, Any]] = field(default_factory=list)


@runtime_checkable
class Tool(Protocol):
    name: str
    description: str
    input_schema: dict[str, Any]
    requires_approval: bool
    owner_only: bool

    async def execute(
        self, input: dict[str, Any], ctx: ToolContext,
    ) -> str | ToolFailure:
        ...


ApprovalDecision = tuple[str, str]  # ("allow", "") | ("deny", "<reason>")
ApprovalHook = Callable[[Tool, dict[str, Any], ToolContext], Awaitable[ApprovalDecision]]


# Telegram door (2026-09-14, task 1): a tool may name the doors it works
# behind, as a ``surfaces`` tuple of PLATFORMS ("discord", "telegram", …).
# Five of her hands reach for a Discord object — a voice channel, a guild
# member list, her own presence — and offered on a door where that object
# does not exist they are a promise she cannot keep. A tool WITHOUT the
# attribute is offered everywhere, which is every tool's behaviour today.
def _platform_for_ctx(ctx: ToolContext) -> str | None:
    """The ctx surface's platform, or None when there is nothing to judge by
    (a blank or unknown surface filters nothing — today's behaviour)."""
    try:
        from core.turn.recall import _platform_for_surface

        return _platform_for_surface(getattr(ctx, "surface", "") or "")
    except Exception:  # noqa: BLE001 — a belt is never withheld over this
        return None


def _tool_works_here(tool: Tool, platform: str | None) -> bool:
    surfaces = tuple(getattr(tool, "surfaces", ()) or ())
    if not surfaces or platform is None:
        return True
    return platform in surfaces


# Second read F3 (2026-10-04): a tool may also say it is not wanted on THIS
# turn, with an ``offered(ctx)`` method. voice_note uses it: while voice
# replies are on in a chat she has no use for it (her reply is spoken), and
# answering it there kept a looping run going (DEFECT §56, §57) — so it is
# left off her belt for that turn instead. A tool without the method is
# offered as today; an error reads as offered (a belt is never withheld over
# this). The registry's dispatch does not consult it: a call to a tool left
# off the belt still gets the tool's own answer.
def _offered_here(tool: Tool, ctx: ToolContext) -> bool:
    offered = getattr(tool, "offered", None)
    if offered is None:
        return True
    try:
        return bool(offered(ctx))
    except Exception:  # noqa: BLE001 — a belt is never withheld over this
        return True


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}
        self._approval_hook: ApprovalHook | None = None

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            logger.warning("tool %r already registered; overwriting", tool.name)
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def all_names(self) -> list[str]:
        return sorted(self._tools.keys())

    def set_approval_hook(self, hook: ApprovalHook | None) -> None:
        self._approval_hook = hook

    def schemas_for(self, ctx: ToolContext) -> list[dict[str, Any]]:
        platform = _platform_for_ctx(ctx)
        return [
            {
                "name": t.name,
                "description": t.description,
                "input_schema": t.input_schema,
            }
            for t in self._tools.values()
            if not (t.owner_only and not ctx.is_owner)
            and _tool_works_here(t, platform)
            and _offered_here(t, ctx)
        ]

    async def dispatch(self, tool_use: ToolUseBlock, ctx: ToolContext) -> ToolResultBlock:
        tool = self._tools.get(tool_use.name)
        if tool is None:
            logger.warning("unknown tool %r requested", tool_use.name)
            return ToolResultBlock(
                tool_use_id=tool_use.id,
                content=f"Unknown tool: {tool_use.name!r}. Available: {', '.join(self.all_names())}",
                is_error=True,
            )

        if tool.owner_only and not ctx.is_owner:
            return ToolResultBlock(
                tool_use_id=tool_use.id,
                content=f"Tool {tool.name!r} is owner-only; caller is not the owner.",
                is_error=True,
            )

        if not _tool_works_here(tool, _platform_for_ctx(ctx)):
            return ToolResultBlock(
                tool_use_id=tool_use.id,
                content=f"Tool {tool.name!r} is not available on this surface.",
                is_error=True,
            )

        if tool.requires_approval and self._approval_hook is not None:
            decision, reason = await self._approval_hook(tool, tool_use.input, ctx)
            if decision == "deny":
                return ToolResultBlock(
                    tool_use_id=tool_use.id,
                    content=f"Tool {tool.name!r} denied: {reason}",
                    is_error=True,
                )

        try:
            # Batch 1 (2026-09-26): the put-back. The model only ever saw
            # [MASKED:<hex>]; the tool gets the REAL value, swapped on the
            # PARSED input (a copy — the ToolUseBlock her transcript and the
            # next request carry keeps the token). The RESULT is never put
            # back: it stays as the tool produced it, and the provider seam
            # masks it again on the next request. Guests reach no tool at
            # all (dispatch builds no registry for a guest turn).
            raw = await tool.execute(unmask_obj(tool_use.input), ctx)
            content_str = _maybe_truncate(str(raw), tool_use.id)
            # Week 2 task 1: a tool that ran and failed says so by RETURNING
            # ToolFailure. Its text is unchanged (str(raw) above); only the
            # flag differs, so the error streak and the record of her deeds
            # stop reading a non-zero exit as a success.
            if isinstance(raw, ToolFailure):
                return ToolResultBlock(tool_use_id=tool_use.id, content=content_str, is_error=True)
            return ToolResultBlock(tool_use_id=tool_use.id, content=content_str, is_error=False)
        except Exception as exc:
            logger.exception("tool %s dispatch raised", tool_use.name)
            return ToolResultBlock(
                tool_use_id=tool_use.id,
                content=f"{tool_use.name} failed: {type(exc).__name__}: {exc}",
                is_error=True,
            )
