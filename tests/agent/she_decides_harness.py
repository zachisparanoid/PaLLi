"""A harness for "she decides whether to answer" (Batch 8, task 2a).

``DmHarness`` sends a message through the REAL spine — ``dispatch_turn`` and
``run_turn`` — with every outside dependency replaced by a recording fake:
recall returns a fixed stand-in context, the quick vote (cadence) returns
the JSON a test asks for, and the reply model either answers or calls
``stay_quiet``. What a door would send is decided by the Discord client's
own ``_deliver_result`` with its send functions recorded, so "nothing was
sent" is the door's verdict, not the harness's.

Nothing here touches her live data: sessions, the quiet record and the
database all live under the test's tmp dir (or the suite's tmp root). Stand-in
names only: Robin is the owner, Sam a guest.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

OWNER_APP_ID = 1
OWNER_DISCORD_ID = 5550001
OWNER_TELEGRAM_ID = 777
GUEST_ID = 4242
DM_CHANNEL = 8800001
ROOM_CHANNEL = 8800002

ANSWER_VOTE = {
    "decision": "answer", "reason": "they asked me something",
    "should_respond": True, "register": "neutral", "length": "short",
    "phase": "settled_conversation", "target_beats": 1,
    "observation_text": "a plain message", "emotional_context": "neutral",
}


def vote(decision: str) -> str:
    body = dict(ANSWER_VOTE)
    body["decision"] = decision
    body["should_respond"] = decision == "answer"
    return json.dumps(body)


def first_look_says(decision: str = "answer", reason: str = "") -> str:
    """What her first look answers (R14): her decision, her one line, the
    shape, and her own texts (none of her own here)."""
    return json.dumps({
        "how_they_seem": "relaxed, chatting", "weight": 0.1, "needs_support": 0.0,
        "carries_forward": "", "memory_worthy": "", "for_me": "a line to me", "my_own": "",
        "decision": decision,
        "reason": reason or {"answer": "they asked me something", "wait": "more is coming",
                             "stay_quiet": "not mine to answer"}.get(decision, ""),
        "length": "short", "register": "neutral", "phase": "settled_conversation",
        "target_beats": 1, "emotional_context": "neutral",
    })


def guest_search_on(monkeypatch: Any) -> Any:
    """features.guest_web_search on, and her own web search on and configured
    (R14, T7): ``build_guest_registry`` then gives a friend's turn the REAL
    registry holding web_search and nothing else, behind its real gates. Exa
    is a recorder (returned): a search never leaves the test."""
    import core.agent.builtin_tools as builtin_tools
    from core.agent.builtin_tools.web_search import WebSearchTool

    exa = SimpleNamespace(search=AsyncMock(return_value={"results": [
        {"title": "Final score", "url": "https://example.org/score",
         "summary": "The home side won 2-1."}]}))
    monkeypatch.setattr(builtin_tools, "_guest_web_search_enabled", lambda: True)
    monkeypatch.setattr(builtin_tools, "_web_search_enabled", lambda: True)
    monkeypatch.setattr(WebSearchTool, "_api_key_present", staticmethod(lambda: True))
    monkeypatch.setattr("ai.web_search.web_search_engine", SimpleNamespace(exa_client=exa))
    return exa


@dataclass
class StandInCtx:
    turn: Any = None
    user_text: str = ""
    recent_history_text: str = ""
    identity_core_text: str = "I'm PaLLi."
    skills_text: str = ""
    runtime_text: str = "## Current Time\nWednesday 09:00"
    memory_text: str = ""
    facts_text: str = ""
    owner_memory_text: str = ""
    corrections_text: str = ""
    is_owner: bool = True


class StandInRegistry:
    """The owner's belt, reduced to one tool and a recorder."""

    def __init__(self) -> None:
        self.dispatched: list[str] = []

    def all_names(self) -> list[str]:
        return ["look_up"]

    def schemas_for(self, ctx: Any) -> list[dict[str, Any]]:
        return [{"name": "look_up", "description": "Look something up.",
                 "input_schema": {"type": "object", "properties": {}}}]

    def set_approval_hook(self, hook: Any) -> None:
        pass

    async def dispatch(self, tool_use: Any, ctx: Any) -> Any:
        from core.agent.session import ToolResultBlock

        self.dispatched.append(tool_use.name)
        return ToolResultBlock(tool_use_id=tool_use.id, content="found it")


@dataclass
class Model:
    """The reply model: what it does on its next call(s)."""

    plan: list[Any] = field(default_factory=list)

    def next(self) -> Any:
        return self.plan.pop(0) if self.plan else ("text", "ok")


class DmHarness:
    def __init__(self, monkeypatch: Any, tmp_path: Path, *, switch_on: bool = True,
                 first_look: bool = False, guest_search: bool = False) -> None:
        import core.agent.llm_client as llm_mod
        from core.agent import quiet_choice

        self.mp = monkeypatch
        self.tmp = tmp_path
        self.sessions = tmp_path / "sessions"
        self.quiet_dir = tmp_path / "quiet_choices"
        self.calls: list[dict[str, Any]] = []
        self.cadence_prompts: list[str] = []
        self.sent: list[str] = []
        self.sent_attachments: list[tuple[str, ...]] = []
        self.model = Model()
        self.cadence_reply = vote("answer")
        self.registry = StandInRegistry()

        monkeypatch.setenv("PALLI_AGENT_LOOP", "react")
        monkeypatch.setenv("PALLI_AGENT_SESSIONS_ROOT", str(self.sessions))
        monkeypatch.setenv("PALLI_QUIET_CHOICES_DIR", str(self.quiet_dir))
        monkeypatch.setattr(quiet_choice, "she_decides_enabled", lambda: switch_on)

        async def cadence_llm(prompt: str) -> str:
            self.cadence_prompts.append(prompt)
            return self.cadence_reply

        async def build_cadence() -> Any:
            return cadence_llm

        async def no_client() -> Any:
            return None

        async def refs() -> dict[str, Any]:
            return {"app_user_id": OWNER_APP_ID, "palli_user_id": OWNER_APP_ID,
                    "discord_user_id": OWNER_DISCORD_ID,
                    "telegram_user_id": OWNER_TELEGRAM_ID}

        async def recall(turn: Any) -> Any:
            return StandInCtx(turn=turn, user_text=str(turn.content),
                              is_owner=str(getattr(turn.metadata, "origin", "")) != "guest")

        async def noop(*a: Any, **k: Any) -> None:
            return None

        def registry_for(**kw: Any) -> Any:
            return self.registry

        async def fake_call(**kw: Any) -> Any:
            from core.agent.session import ToolUseBlock

            self.calls.append(kw)
            kind, *rest = self.model.next()
            if kind == "quiet":
                reason = rest[0] if rest else ""
                extra = rest[1] if len(rest) > 1 else []
                text = rest[2] if len(rest) > 2 else []
                uses = [ToolUseBlock(id=f"q{len(self.calls)}", name="stay_quiet",
                                     input=({"reason": reason} if reason is not None else {}))]
                uses = [*extra, *uses]
                return llm_mod.LLMResponse(text_blocks=list(text), tool_uses=uses,
                                           stop_reason="tool_use", prompt_tokens=1,
                                           completion_tokens=1, reasoning="thinking it over")
            if kind == "tool":
                return llm_mod.LLMResponse(text_blocks=list(rest[1] if len(rest) > 1 else []),
                                           tool_uses=[rest[0]], stop_reason="tool_use",
                                           prompt_tokens=1, completion_tokens=1)
            return llm_mod.LLMResponse(text_blocks=[rest[0]] if rest and rest[0] else [],
                                       tool_uses=[], stop_reason="end_turn",
                                       prompt_tokens=1, completion_tokens=1)

        monkeypatch.setattr("core.agent.dispatch._build_cadence_llm_callable", build_cadence)
        monkeypatch.setattr("core.agent.dispatch._get_openai_compat_client", no_client)
        monkeypatch.setattr("core.local_mode.get_local_owner_refs", refs)
        monkeypatch.setattr("core.turn.recall.build_turn_context", recall)
        monkeypatch.setattr("core.turn.persistence._persist_user_side_with_content", noop)
        monkeypatch.setattr("core.turn.persistence._persist_assistant_side_to_history", noop)
        # The doors' arrival record binds its own name for the DB half; its
        # detached write would outlive the test's loop (an aiosqlite thread
        # warning in the next test). The transcript half stays real.
        monkeypatch.setattr("bots.discord.recording._persist_user_side_with_content", noop)
        monkeypatch.setattr("core.turn.reflect.apply_reflection", noop)
        monkeypatch.setattr("core.agent.builtin_tools.build_default_registry", registry_for)
        # The guest twin (R14, T8; plan §2b.6): a friend's belt is the REAL
        # build_guest_registry — None with features.guest_web_search off (the
        # suite holds it off), web_search only with ``guest_search=True``.
        self.exa = guest_search_on(monkeypatch) if guest_search else None
        # Her first look (R14): on only when the test asks
        # (companion.first_look, held off by the suite); it answers from its
        # own queue (an exception there is a call that breaks), never a model.
        self.look_plan: list[Any] = []
        self.look_prompts: list[str] = []
        if first_look:
            monkeypatch.setattr("core.agent.first_look.enabled", lambda: True)

        async def look_llm(prompt: str) -> str:
            self.look_prompts.append(prompt)
            step = self.look_plan.pop(0) if self.look_plan else self.default_look()
            if isinstance(step, BaseException):
                raise step
            return step

        monkeypatch.setattr("core.agent.first_look.build_llm", lambda: look_llm)
        monkeypatch.setattr(llm_mod, "call", fake_call)

    def default_look(self) -> str:
        """What the look says when the test planned nothing: answer."""
        return first_look_says("answer")

    # ── what the model does ───────────────────────────────────────────────
    def model_answers(self, text: str) -> None:
        self.model.plan.append(("text", text))

    def model_chooses_quiet(self, reason: str | None = "", *, also: list[Any] | None = None,
                            text: list[str] | None = None) -> None:
        self.model.plan.append(("quiet", reason, list(also or []), list(text or [])))

    def model_calls(self, tool_use: Any, text: list[str] | None = None) -> None:
        self.model.plan.append(("tool", tool_use, list(text or [])))

    # ── a message through the spine and a door ─────────────────────────────
    def turn(self, text: str, *, who: str = "owner", channel: int = DM_CHANNEL,
             is_dm: bool = True, origin: str | None = None, surface: str = "discord_text") -> Any:
        from datetime import datetime

        from core.owner_gate import guest_content
        from core.turn.types import Turn, TurnMetadata

        guest = who != "owner"
        content = guest_content(who, text) if guest else text
        return Turn(
            surface=surface,
            owner_id=GUEST_ID if guest else OWNER_APP_ID,
            content=content,
            metadata=TurnMetadata(
                channel_id=channel, is_dm=is_dm,
                origin=(origin or ("guest" if guest else "user")),  # type: ignore[arg-type]
                discord_username=("sam_example" if guest else "robin_example"),
            ),
            received_at=datetime(2026, 10, 1, 9, 0, 0),
        )

    async def dispatch(self, turn: Any) -> Any:
        from core.agent.dispatch import dispatch_turn

        return await dispatch_turn(turn)

    async def deliver(self, result: Any, channel: int = DM_CHANNEL) -> None:
        """The Discord door's own delivery decision, with the sends recorded."""
        import discord

        from bots.discord.client import PalliDiscordClient

        client = PalliDiscordClient(bot_user_id=99999, owner_id=OWNER_APP_ID,
                                    intents=discord.Intents.default())
        client.get_channel = MagicMock(return_value=MagicMock(id=channel))
        client._record_delivery = AsyncMock(return_value=True)

        async def send(channel_obj: Any, text: str, attachments: Any = (),
                       waiting_since: Any = None) -> None:
            self.sent.append(text)
            self.sent_attachments.append(tuple(attachments or ()))

        async def send_cadenced(channel_obj: Any, reply: Any, waiting_since: Any = None) -> None:
            self.sent.append(reply.total_text)

        with patch("bots.discord.client.send_turn_reply", new=send), \
             patch("bots.discord.client.send_turn_reply_cadenced", new=send_cadenced):
            await client._deliver_result(channel, result)

    async def says(self, text: str, **kw: Any) -> Any:
        channel = kw.get("channel", DM_CHANNEL)
        result = await self.dispatch(self.turn(text, **kw))
        await self.deliver(result, channel=channel)
        return result

    # ── what is on the record ─────────────────────────────────────────────
    async def transcript(self, channel: int = DM_CHANNEL, *, surface: str = "discord_text",
                         owner_id: int = OWNER_APP_ID) -> list[Any]:
        from core.agent.session import Session
        from core.session_keys import session_key_for

        key = session_key_for(surface, channel, owner_id)
        return list((await Session.open_or_create(key, root=self.sessions)).messages)

    async def transcript_has(self, text: str, **kw: Any) -> bool:
        from core.agent.session import TextBlock

        for m in await self.transcript(**kw):
            for b in m.content:
                if isinstance(b, TextBlock) and text in b.text:
                    return True
        return False

    def quiet_record(self, days: int = 2) -> list[dict[str, Any]]:
        """Her record, from this harness's own folder. Two days, not one: a
        row written just before midnight is read just after it (the record
        files rows by the real clock's date), and the folder is this test's
        alone, so nothing older can be in it."""
        from core import quiet_record

        return quiet_record.read(days)
