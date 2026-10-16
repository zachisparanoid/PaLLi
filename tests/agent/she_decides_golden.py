"""The switch-off golden for "she decides" (Batch 8, task 2a — ruling T2a-3).

With ``companion.she_decides`` OFF, her decision behaviour must be today's,
byte for byte. This module drives two turns through the real runner — a
stranger's turn in a shared room and the owner's turn in a DM, where the quick
vote says "don't respond" — and captures everything the model is sent: the
cadence prompt and the full keyword arguments of every ``llm_client.call``.

The capture in ``golden/she_decides_off.json`` was taken by running THIS
module at the Task 1 head (369f35d), in a scratch clone, before any of task
2a existed — never "before Batch 8": Task 1 changes guest payloads on
purpose. ``tests/agent/test_quiet_record_and_switch.py`` runs it again on the
current tree with the switch off and compares.

Regenerated on purpose, one line, 2026-10-08 (step 2, part 6; the owner's
add-on a): the guest turn's length note no longer says "tools are always
allowed" (a guest's turn offers no tools). Nothing else changed.

Regenerated on purpose again, 2026-10-10 ("I'm not sure", the owner: "yes,
build it"): her "When I'm wrong" rule gains the two sentences on a look that
missed, in both turns' system prompts. Nothing else changed.

Regenerated on purpose a third time, 2026-10-13 (Draft A, the permission to
disagree, approved word for word 10-12 15:50, riding with the first look,
Q3): one sentence of "I'm myself with them", in both turns' system prompts.
Nothing else changed.

Regenerated on purpose a fourth time, 2026-10-13 (the presence slice; the
owner: "Drop half 1, keep half 2"): the subtext note in the cadence prompt
loses its first half ("name the UNSPOKEN thing ... testing"), in both turns'
cadence prompts. Nothing else changed.

So this module uses only what existed at that head: no import from the
quiet-choice code, nothing a later task added. Stand-in names only: Robin is
the owner, Sam a guest. Nothing here reads her live data: the context is a
fixed stand-in, the model is a recording fake, and the sessions root is the
caller's tmp dir.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

GOLDEN_PATH = Path(__file__).with_name("golden") / "she_decides_off.json"

ROOM_ID = 9100001       # a shared room (a Discord server channel)
DM_ID = 9100002         # the owner's DM channel
GUEST_ID = 4242         # Sam's platform id (the doors put it on owner_id)
OWNER_APP_ID = 1        # the owner's app user id


@dataclass
class GoldenCtx:
    """A fixed, stand-in context: every string the system prompt reads."""

    turn: Any = None
    user_text: str = ""
    recent_history_text: str = "(Robin and PaLLi talked about the weekend)"
    identity_core_text: str = "I'm PaLLi."
    skills_text: str = ""
    runtime_text: str = "## Current Time\nWednesday 09:00"
    memory_text: str = "- Robin likes plain answers"
    facts_text: str = ""
    owner_memory_text: str = ""
    corrections_text: str = ""
    is_owner: bool = True


class GoldenRegistry:
    """The owner's belt, reduced to one fixed tool so the schema list is stable."""

    _SCHEMA = {
        "name": "look_up",
        "description": "Look something up in your notes.",
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    }

    def all_names(self) -> list[str]:
        return ["look_up"]

    def schemas_for(self, ctx: Any) -> list[dict[str, Any]]:
        return [dict(self._SCHEMA)]

    async def dispatch(self, tool_use: Any, ctx: Any) -> Any:  # pragma: no cover - never called
        raise AssertionError("the golden turns never call a tool")


_GUEST_VOTE = (
    '{"should_respond": true, "register": "casual_greeting", "length": "short", '
    '"phase": "settled_conversation", "target_beats": 1, '
    '"observation_text": "Sam is making small talk", "emotional_context": "neutral"}'
)
_OWNER_VOTE = (
    '{"should_respond": false, "register": "neutral", "length": "short", '
    '"phase": "settled_conversation", "target_beats": 1, '
    '"observation_text": "Robin asked for no reply", "emotional_context": "neutral"}'
)


def _block(b: Any) -> dict[str, Any]:
    from core.agent.session import _block_to_dict

    return _block_to_dict(b)


def _message(m: Any) -> dict[str, Any]:
    return {
        "role": m.role,
        "content": [_block(b) for b in m.content],
        "reasoning": getattr(m, "reasoning", ""),
        "delivery": getattr(m, "delivery", ""),
    }


def _call(kw: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key in sorted(kw):
        value = kw[key]
        if key == "messages":
            out[key] = [_message(m) for m in value]
        elif key == "on_chunk":
            out[key] = None if value is None else "callable"
        elif key == "client":
            out[key] = None if value is None else type(value).__name__
        else:
            out[key] = value
    return out


async def _seed(root: Path, key: str, messages: list[Any]) -> None:
    from core.agent.session import Session

    s = await Session.open_or_create(key, root=root)
    for m in messages:
        await s.append(m)


def _room_history() -> list[Any]:
    from core.agent.session import AgentMessage, TextBlock, ToolResultBlock, ToolUseBlock

    return [
        AgentMessage(role="user", content=[TextBlock(text="[Sam]: morning all")]),
        AgentMessage(role="assistant", content=[TextBlock(text="morning Sam!")]),
        AgentMessage(role="user", content=[TextBlock(text="can you check the disk?")]),
        AgentMessage(role="assistant", content=[
            TextBlock(text="checking the disk now"),
            ToolUseBlock(id="t1", name="local_shell", input={"command": "df -h"}),
        ]),
        AgentMessage(role="tool", content=[ToolResultBlock(
            tool_use_id="t1", content="/dev/sda1 91% used /home/robin/projects")]),
        AgentMessage(role="assistant", content=[TextBlock(text="The main disk is 91% full.")]),
    ]


def _dm_history() -> list[Any]:
    from core.agent.session import AgentMessage, TextBlock

    return [
        AgentMessage(role="user", content=[TextBlock(text="hey")]),
        AgentMessage(role="assistant", content=[TextBlock(text="hey Robin")]),
    ]


async def capture(root: Path, monkeypatch: Any) -> dict[str, Any]:
    """Run both turns under ``root`` and return what the model was sent."""
    import core.agent.llm_client as llm_mod
    from core.agent.runner import RunnerDeps, run_turn
    from core.turn.types import Turn, TurnMetadata

    calls: list[dict[str, Any]] = []
    replies: list[list[str]] = []

    async def fake_call(**kw: Any) -> Any:
        calls.append(_call(kw))
        text = replies.pop(0) if replies else []
        return llm_mod.LLMResponse(text_blocks=list(text), tool_uses=[],
                                   stop_reason="end_turn",
                                   prompt_tokens=1, completion_tokens=1)

    monkeypatch.setattr(llm_mod, "call", fake_call)

    def _deps(root_: Path, *, vote: str, prompts: list[str], is_owner: bool,
              audience: str | None, registry: Any) -> Any:
        async def recall(turn: Any) -> Any:
            return GoldenCtx(turn=turn, user_text=str(turn.content), is_owner=is_owner)

        async def cadence(prompt: str) -> str:
            prompts.append(prompt)
            return vote

        kw: dict[str, Any] = {}
        if audience is not None:
            kw["audience"] = audience
        return RunnerDeps(
            build_turn_context=recall, cadence_llm=cadence, sessions_root=root_,
            provider="openai_compat", model="golden-model", client=None,
            tool_registry=registry, polling_tools=set(), is_owner=is_owner, **kw,
        )

    out: dict[str, Any] = {}

    # 1. A stranger in a shared room: Sam says something to the room.
    await _seed(root, f"discord:{ROOM_ID}", _room_history())
    guest_prompts: list[str] = []
    calls.clear()
    replies[:] = [["nice, it is!"]]
    guest_turn = Turn(
        surface="discord_text", owner_id=GUEST_ID, content="[Sam]: nice weather today",
        metadata=TurnMetadata(channel_id=ROOM_ID, origin="guest", is_dm=False,
                              discord_username="sam_example"),
        received_at=datetime(2026, 10, 1, 9, 0, 0),
    )
    guest_result = await run_turn(guest_turn, deps=_deps(
        root, vote=_GUEST_VOTE, prompts=guest_prompts, is_owner=False,
        audience="stranger", registry=None))
    out["guest_room_turn"] = {
        "cadence_prompts": guest_prompts,
        "llm_calls": list(calls),
        "responded": bool(guest_result.responded),
        "response_text": guest_result.response_text,
    }

    # 2. The owner in a DM, asking for no reply; the quick vote says false.
    await _seed(root, f"discord:{DM_ID}", _dm_history())
    owner_prompts: list[str] = []
    calls.clear()
    replies[:] = [[]]
    owner_turn = Turn(
        surface="discord_text", owner_id=OWNER_APP_ID, content="do not respond to this message",
        metadata=TurnMetadata(channel_id=DM_ID, origin="user", is_dm=True,
                              discord_username="robin_example"),
        received_at=datetime(2026, 10, 1, 9, 1, 0),
    )
    owner_result = await run_turn(owner_turn, deps=_deps(
        root, vote=_OWNER_VOTE, prompts=owner_prompts, is_owner=True,
        audience=None, registry=GoldenRegistry()))
    out["owner_dm_turn"] = {
        "cadence_prompts": owner_prompts,
        "llm_calls": list(calls),
        "responded": bool(owner_result.responded),
        "response_text": owner_result.response_text,
    }
    return out


def dumps(capture_: dict[str, Any]) -> str:
    return json.dumps(capture_, indent=1, sort_keys=True, ensure_ascii=False) + "\n"
