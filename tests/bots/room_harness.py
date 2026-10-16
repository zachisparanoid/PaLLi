"""A harness for the room first pass (Batch 8, task 2b — rulings T2b-1..T2b-9).

``RoomHarness`` puts a shared room in front of the REAL door — the Discord
client (or, with ``door="telegram"``, the Telegram client) — and the real spine
behind it (``dispatch_turn`` → ``run_turn``), with every outside dependency a
recording fake. It builds on the 2a ``DmHarness``: recall returns a stand-in
context, the reply model answers or calls ``stay_quiet`` as a test asks, and
the quick vote (the first pass, and the runner's in-run vote if it is ever
asked) answers from a queue the test fills (``first_pass=...``).

What the room shows:

- ``sent`` — every text the door sent into the room;
- ``full_runs`` — how many full turns were dispatched (the brief's
  ``full_runs`` means dispatches, ruling T2b-1);
- ``file_has(text)`` — the room's transcript (``discord:<room>``);
- ``last_signals`` — the signals the last first pass read;
- ``last_run_context`` — every word the last reply call was sent;
- ``quiet_record()`` — her record of quiet choices;
- ``look_prompts`` — every first look the room pass took (R14, T4), with
  ``first_look=True``: the look answers from its own queue (``look=...``).

The pause after a "wait" never sleeps for real: the room pass's sleep is a
``FakeSleep`` the test ends with ``pass_the_pause()``.

Stand-in names only: Robin is the owner; Sam, Alex and Jo are guests.
"""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from tests.agent.she_decides_harness import (
    OWNER_APP_ID,
    OWNER_DISCORD_ID,
    OWNER_TELEGRAM_ID,
    DmHarness,
)

ROOM = 8800002
TG_GROUP = -1009900001
BOT_ID = 99999
BOT_USERNAME = "palli_test_bot"
GUEST_IDS = {"sam": 4242, "alex": 4343, "jo": 4444}


def room_vote(decision: str, reason: str = "", *, length: str = "short") -> str:
    """What the quick read answers: a decision, her one line, and the shape."""
    return json.dumps({
        "decision": decision,
        "reason": reason or {"answer": "they're talking to me",
                             "wait": "more is coming",
                             "stay_quiet": "not mine to answer"}.get(decision, ""),
        "register": "neutral", "length": length, "phase": "settled_conversation",
        "target_beats": 1, "observation_text": "a room message",
        "emotional_context": "neutral",
    })


def room_look(decision: str = "answer", reason: str = "", *, length: str = "short",
              how_they_seem: str = "", for_me: str = "", my_own: str = "") -> str:
    """What her first look answers (R14, T4): her decision, her one line, the
    shape, and her own three texts."""
    return json.dumps({
        "how_they_seem": how_they_seem or "relaxed, chatting",
        "weight": 0.1, "needs_support": 0.0, "carries_forward": "", "memory_worthy": "",
        "for_me": for_me or "a line in the room",
        "my_own": my_own,
        "decision": decision,
        "reason": reason or {"answer": "they're talking to me",
                             "wait": "more is coming",
                             "stay_quiet": "not mine to answer"}.get(decision, ""),
        "length": length, "register": "neutral", "phase": "settled_conversation",
        "target_beats": 1, "emotional_context": "neutral",
    })


class FakeSleep:
    """The pause after a "wait": it ends when the test says so, never on a clock."""

    def __init__(self) -> None:
        self.calls: list[float] = []
        self._waiters: list[asyncio.Future] = []

    async def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)
        fut = asyncio.get_running_loop().create_future()
        self._waiters.append(fut)
        await fut

    def release(self) -> None:
        waiters, self._waiters = self._waiters, []
        for fut in waiters:
            if not fut.done():
                fut.set_result(None)


def _text_of(messages: Any) -> str:
    out: list[str] = []
    for m in messages or ():
        for b in getattr(m, "content", ()) or ():
            text = getattr(b, "text", None)
            if isinstance(text, str):
                out.append(text)
    return "\n".join(out)


class RoomHarness(DmHarness):
    def __init__(self, monkeypatch: Any, tmp_path: Any, *, switch_on: bool = True,
                 door: str = "discord", first_look: bool = False,
                 guest_search: bool = False) -> None:
        # Her first look (R14, T4) and a friend's web search (T7): on only
        # when the test asks; the look answers from the DM harness's queue
        # (``look_plan``), the room's own look when nothing is planned.
        super().__init__(monkeypatch, tmp_path, switch_on=switch_on, first_look=first_look,
                         guest_search=guest_search)
        self.door = door
        self.cadence_plan: list[str] = []
        self.dispatched: list[Any] = []
        self.results: list[tuple[Any, Any]] = []
        self.sleep = FakeSleep()
        # How long a quick read takes. 0 answers at once; a test that must
        # see reads overlap (a merge would show) sets a small real delay.
        self.read_delay = 0.0

        async def cadence_llm(prompt: str) -> str:
            self.cadence_prompts.append(prompt)
            if self.read_delay:
                await asyncio.sleep(self.read_delay)
            return self.cadence_plan.pop(0) if self.cadence_plan else room_vote("answer")

        async def build_cadence() -> Any:
            return cadence_llm

        monkeypatch.setattr("core.agent.dispatch._build_cadence_llm_callable", build_cadence)

        from core.agent.dispatch import dispatch_turn as real_dispatch

        async def counting_dispatch(turn: Any) -> Any:
            self.dispatched.append(turn)
            result = await real_dispatch(turn)
            self.results.append((turn, result))
            return result

        if door == "discord":
            self._build_discord(monkeypatch, counting_dispatch)
        else:
            self._build_telegram(monkeypatch, counting_dispatch)
        self.client._room_pass._sleep = self.sleep

    def default_look(self) -> str:
        return room_look("answer")

    # ── the doors ──────────────────────────────────────────────────────────
    def _build_discord(self, monkeypatch: Any, dispatch: Any) -> None:
        import discord

        from bots.common.guest_gate import GuestGate
        from bots.discord.client import PalliDiscordClient

        self.gate = GuestGate()
        self.room = ROOM
        client = PalliDiscordClient(bot_user_id=BOT_ID, owner_id=OWNER_APP_ID,
                                    intents=discord.Intents.default(), guest_gate=self.gate)
        client._sessions_root = self.sessions

        async def owner_did() -> int:
            return OWNER_DISCORD_ID

        client._local_owner_discord_id = owner_did
        channel = MagicMock()
        channel.id = ROOM
        channel.name = "general"
        channel.guild = SimpleNamespace(id=1, name="the den")
        self.channel = channel
        client.get_channel = lambda cid: channel
        client._record_delivery = AsyncMock(return_value=True)
        self._sent: list[str] = []

        async def send(channel_obj: Any, text: str, attachments: Any = (),
                       waiting_since: Any = None) -> None:
            self._sent.append(text)

        async def send_cadenced(channel_obj: Any, reply: Any, waiting_since: Any = None) -> None:
            self._sent.append(reply.total_text)

        monkeypatch.setattr("bots.discord.client.send_turn_reply", send)
        monkeypatch.setattr("bots.discord.client.send_turn_reply_cadenced", send_cadenced)
        monkeypatch.setattr("bots.discord.client.dispatch_turn", dispatch)
        self.client = client

    def _build_telegram(self, monkeypatch: Any, dispatch: Any) -> None:
        from bots.common.guest_gate import GuestGate
        from bots.telegram.client import PalliTelegramClient
        from tests.bots.test_telegram_client import FakeApplication, FakeHandlerFactory

        self.gate = GuestGate()
        self.room = TG_GROUP
        self.app = FakeApplication()
        client = PalliTelegramClient(application=self.app, bot_user_id=BOT_ID,
                                     bot_username=BOT_USERNAME, owner_id=OWNER_APP_ID,
                                     guest_gate=self.gate, sessions_root=self.sessions)
        client._handler_factory = FakeHandlerFactory

        async def owner_tg() -> int:
            return OWNER_TELEGRAM_ID

        client._owner_telegram_id = owner_tg
        client._record_delivery = AsyncMock(return_value=True)
        monkeypatch.setattr("bots.telegram.client.dispatch_turn", dispatch)
        self.client = client

    def use_the_real_room_note_tool(self) -> None:
        """The owner's belt in this room: the real ``room_note`` tool (owner-
        only), behind the real registry and dispatch's approval hook."""
        from core.agent.builtin_tools.room_note import RoomNoteTool
        from core.agent.tools import ToolRegistry

        reg = ToolRegistry()
        reg.register(RoomNoteTool())
        self.registry = reg

    # ── a message into the room ────────────────────────────────────────────
    def _discord_message(self, who: str, text: str) -> Any:
        author_id = OWNER_DISCORD_ID if who == "owner" else GUEST_IDS[who]
        msg = MagicMock()
        msg.author = SimpleNamespace(id=author_id, bot=False, name=f"{who}_example",
                                     display_name=("Robin" if who == "owner" else who.capitalize()))
        msg.content = text
        msg.attachments = []
        msg.mentions = []
        msg.reference = None
        msg.channel = self.channel
        return msg

    def _telegram_update(self, who: str, text: str) -> Any:
        from tests.bots.test_telegram_client import FakeMessage, FakeUpdate

        author_id = OWNER_TELEGRAM_ID if who == "owner" else GUEST_IDS[who]
        message = FakeMessage(text=text, author_id=author_id, chat_id=TG_GROUP,
                              chat_type="supergroup")
        message.from_user.username = f"{who}_example"
        return FakeUpdate(message)

    def plan(self, *, first_pass: str | None = None, model_chooses: str | None = None,
             reason: str = "", answer_text: str | None = None, who: str = "",
             look: str | None = None, my_own: str = "", for_me: str = "") -> None:
        if first_pass:
            self.cadence_plan.append(room_vote(first_pass, reason if first_pass != "answer" else ""))
        if look:
            self.look_plan.append(room_look(look, reason if look != "answer" else "",
                                            my_own=my_own, for_me=for_me))
        if model_chooses == "stay_quiet":
            self.model_chooses_quiet(reason or "they asked me not to")
        elif model_chooses == "answer":
            self.model_answers(answer_text or f"a reply for {who or 'the room'}")

    async def arrive(self, who: str, text: str) -> None:
        """The message reaches the door; for the owner, the burst settles."""
        if self.door == "discord":
            await self.client.on_message(self._discord_message(who, text))
        else:
            await self.client.on_message(self._telegram_update(who, text))
        if who == "owner":
            await self.client._fire_burst(self.room)
            self.client._coalescer.mark_replied(self.room)

    async def say(self, who: str, text: str, *, first_pass: str | None = None,
                  model_chooses: str | None = None, reason: str = "",
                  answer_text: str | None = None, look: str | None = None,
                  my_own: str = "", for_me: str = "") -> None:
        self.plan(first_pass=first_pass, model_chooses=model_chooses, reason=reason,
                  answer_text=answer_text, who=who, look=look, my_own=my_own, for_me=for_me)
        await self.arrive(who, text)
        await self.settle()

    async def settle(self) -> None:
        from core.agent.runner import flush_detached_tasks

        for _ in range(3):
            tasks = list(getattr(self.client, "_guest_tasks", ()) or ())
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            await asyncio.sleep(0)
        await flush_detached_tasks()

    async def pass_the_pause(self) -> None:
        """The pause after a "wait" ends; whatever she reads then runs out.
        Only the pauses already sleeping are awaited (a read that waits again
        arms a new one), and never for long: a test must fail, not hang."""
        sleeping = [t for t in list(self.client._room_pass._tasks) if not t.done()]
        self.sleep.release()
        if sleeping:
            await asyncio.wait_for(asyncio.gather(*sleeping, return_exceptions=True), timeout=5)
        await self.settle()

    # ── what the room shows ────────────────────────────────────────────────
    @property
    def sent(self) -> list[str]:
        if getattr(self, "door", None) is None:
            return self._dm_sent                    # the DM harness's own list
        if self.door == "discord":
            return list(self._sent)
        return [m["text"] for m in self.app.bot.messages]

    @sent.setter
    def sent(self, value: list[str]) -> None:
        self._dm_sent = value

    @property
    def full_runs(self) -> int:
        return len(self.dispatched)

    @property
    def last_signals(self) -> dict[str, Any]:
        return dict(self.client._room_pass.last_signals or {})

    @property
    def last_run_context(self) -> str:
        if not self.calls:
            return ""
        kw = self.calls[-1]
        return "\n".join([str(kw.get("system") or ""), str(kw.get("system_tail") or ""),
                          _text_of(kw.get("messages")),
                          str(kw.get("trailing_system_note") or "")])

    def sent_to(self, who: str) -> bool:
        for turn, result in self.results:
            owner = str(getattr(turn.metadata, "origin", "")) != "guest"
            if (who == "owner") != owner:
                continue
            if who != "owner" and turn.owner_id != GUEST_IDS.get(who):
                continue
            text = str(getattr(result, "response_text", "") or "")
            if result.responded and text and text in self.sent:
                return True
        return False

    def room_key(self) -> str:
        return f"{'discord' if self.door == 'discord' else 'telegram'}:{self.room}"

    async def room_messages(self) -> list[Any]:
        from core.agent.session import Session

        return list((await Session.open_or_create(self.room_key(), root=self.sessions)).messages)

    async def file_has(self, text: str) -> bool:
        return text in _text_of(await self.room_messages())

    async def file_count(self, text: str) -> int:
        return sum(1 for m in await self.room_messages()
                   for b in m.content if text in str(getattr(b, "text", "") or ""))
