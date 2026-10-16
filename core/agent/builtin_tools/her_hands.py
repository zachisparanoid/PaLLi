"""Her Hands (step 33): the capability sweep — spec 2026-08-11.

Six levers her reasoning can pull: voice (join/leave/speak), people
(list_members), reach (send_message via record_outbound custody), and
presence. Full trust (owner decision, verbatim): requires_approval=False
everywhere, no caps, descriptions in the trusting register. owner_only
=True everywhere: the levers exist for HIM only — full trust in her is
not full trust in whoever can type at her.
"""
from __future__ import annotations

import re

import time
from typing import Any

from core import voice_replies as _voice_replies
from core.agent import voice_note_final as _voice_note_final
from core.agent.tool_outcome import ToolFailure
from core.agent.tools import ToolContext
from core.doors import last_used_door


# seams — lazy imports so tests monkeypatch the module attributes

def _voice_features_on() -> bool:
    from core.config import config
    return bool(getattr(config, "ENABLE_VOICE_FEATURES", False))


def _voice_runtime_ready():
    from voice.bootstrap import require_voice_runtime_ready
    status = require_voice_runtime_ready()
    return bool(status.ok), list(getattr(status, "messages", []) or [])


async def _start_listening(vc, voice_channel_id, text_channel_id, user_id, **kw):
    from voice.always_listening import voice_processor
    return await voice_processor.start_listening(
        vc, voice_channel_id, text_channel_id, user_id, **kw)


async def _stop_listening(channel_id):
    from voice.always_listening import voice_processor
    return await voice_processor.stop_listening(channel_id)


async def _say_in_session(channel_id, text) -> bool:
    """One line aloud in the active session — the Task-3 investigation
    wires this to the found entry (or the added voice.control.say_text)."""
    from voice.always_listening import voice_processor
    from voice.control import voice_session_control_service
    snapshot = voice_processor.get_session_snapshot(channel_id) or {}
    key = str(snapshot.get("runtime_session_key") or "")
    if not key:
        return False
    return bool(await voice_session_control_service.say_text(
        runtime_session_key=key, text=text))


def _deed(kind: str, detail: str) -> None:
    from ai.recent_deeds import record_deed
    record_deed(kind, detail)


async def _record_outbound(**kw) -> bool:
    from core.turn.outbound import record_outbound
    return await record_outbound(**kw)


def _is_this_conversation(ctx: ToolContext, platform: str, channel_id: int) -> bool:
    """True when the door is the chat this turn is running in: a text turn on
    the door's own platform, in the door's own channel."""
    surface = str(getattr(ctx, "surface", "") or "")
    try:
        here = int(getattr(ctx, "channel_id", 0) or 0)
    except (TypeError, ValueError):
        return False
    return surface == f"{platform}_text" and here == int(channel_id) and here != 0


async def _send_unspoken_line(text: str, ctx: ToolContext, *, why: str) -> str | ToolFailure:
    """A line she could not speak goes to him as text, and she is told so
    (fix plan 7.2; controller ruling R15, 2026-09-28).

    Her transcripts held ten speak_line calls: four went nowhere, and each
    came back as a plain success string. Now the line goes out on the
    private chat he used last (since 10-13, the owner: her own messages to
    him never go to a server channel or a group), through the same custody
    route send_message uses, so her record and what reached him agree. No
    private chat, or a send that fails, is a failure that says the line was
    neither spoken nor sent.

    When that chat is THIS conversation (fix round 1, R15a — the 09-11
    case), nothing is sent: record_outbound would file the line into the
    running turn's own session between this open tool call and its result
    (it is a post-run helper), and he would read it twice, once sent and
    once in her reply. She is told to say it in her reply instead, the way
    send_message answers for this conversation."""
    owner_id = int(ctx.turn_owner_id)
    try:
        door = await last_used_door(owner_id)
    except Exception as exc:  # noqa: BLE001 - a failed lookup is a failed call, said plainly
        return ToolFailure(
            f"Neither spoken nor sent — {why}, and I couldn't find the private chat they used last: {exc}")
    if door is None:
        return ToolFailure(
            f"Neither spoken nor sent — {why}, and there is no private Discord, "
            "Telegram or phone-app chat with them to send it to as text.")
    platform, channel_id = door
    if _is_this_conversation(ctx, platform, channel_id):
        return (f"Not spoken — {why}, and the private chat they used last is "
                "this conversation, so nothing was sent. Say it in your reply.")
    where = f"{platform}:{channel_id}"
    try:
        sent = await _record_outbound(
            owner_id=owner_id,
            channel_id=channel_id,
            text=text,
            origin="her_hands",
            extra={"speak_line_fallback": True},
            platform=platform,
            surface_id=str(channel_id),
        )
    except Exception as exc:  # noqa: BLE001 - no record without a send; say what happened
        return ToolFailure(
            f"Neither spoken nor sent — {why}, and the text send to {where} failed: {exc}")
    if not sent:
        return ToolFailure(
            f"Neither spoken nor sent — {why}, and the text send to {where} did not go out.")
    return f"not spoken — no live voice session; sent as text on {where}"


class JoinVoiceChannelTool:
    name = "join_voice_channel"
    description = (
        "Join a voice channel by name and start listening — one of your "
        "hands. They do NOT need to be in it: 'join general and wait for "
        "me' is exactly what this is for. You may move channels by joining "
        "another. Yours to use on your own read of the moment."
    )
    input_schema = {
        "type": "object",
        "properties": {"channel_name": {"type": "string"}},
        "required": ["channel_name"],
    }
    requires_approval = False
    owner_only = True
    # Telegram door (2026-09-14, task 1): this hand reaches for a Discord
    # object (a voice channel, a member list, her own presence) through the
    # global client in hands_runtime, not through anything on the ctx — so
    # it works from wherever she is ALREADY reachable, and the list is the
    # platforms that offered it before the surface gate existed. Telegram
    # is the one door it cannot keep a promise on.
    surfaces = ("discord", "dashboard", "voice")

    async def execute(self, input: dict[str, Any], ctx: ToolContext) -> str:
        from core.agent import hands_runtime as hr

        if not _voice_features_on():
            return "Voice features are disabled in config right now."
        ok, messages = _voice_runtime_ready()
        if not ok:
            return "My voice runtime isn't ready: " + "; ".join(messages[:3])
        name = str(input.get("channel_name") or "")
        channel, candidates = hr.resolve_voice_channel(name)
        if channel is None:
            if candidates:
                return ("More than one channel could be "
                        f"'{name}' — I can see: {', '.join(candidates)}. Your call.")
            return f"No voice channel I can see matches '{name}'."
        guild = getattr(channel, "guild", None) or (hr.get_client().guilds[0] if hr.get_client() else None)
        existing = getattr(guild, "voice_client", None) if guild else None
        moved = ""
        if existing is not None:
            # Stop the old listening session BEFORE disconnecting — a bare
            # disconnect zombies the sinks bound to the old connection.
            await _stop_listening(getattr(getattr(existing, "channel", None), "id", None))
            await existing.disconnect()
            moved = " (moved from my previous channel)"
        try:
            from discord.ext import voice_recv
            vc = await channel.connect(cls=voice_recv.VoiceRecvClient)
        except Exception as exc:
            return f"Couldn't connect to {channel.name}: {exc}"
        text_id = int(getattr(ctx, "channel_id", 0) or 0)
        if not text_id and guild is not None:
            fb = hr.fallback_text_channel(guild)
            text_id = int(getattr(fb, "id", 0) or 0)
        started = await _start_listening(
            vc, channel.id, text_id, int(ctx.turn_owner_id))
        if not started:
            await vc.disconnect()
            return f"Connected to {channel.name} but couldn't start listening; backed out."
        _deed("hands", f"joined voice channel {channel.name}")
        return f"Joined {channel.name} and I'm listening{moved}."


class LeaveVoiceTool:
    name = "leave_voice"
    description = ("Leave the voice channel you're in and stop listening. "
                   "Yours to use whenever staying stops making sense.")
    input_schema = {"type": "object", "properties": {}}
    requires_approval = False
    owner_only = True
    # Telegram door (2026-09-14, task 1): this hand reaches for a Discord
    # object (a voice channel, a member list, her own presence) through the
    # global client in hands_runtime, not through anything on the ctx — so
    # it works from wherever she is ALREADY reachable, and the list is the
    # platforms that offered it before the surface gate existed. Telegram
    # is the one door it cannot keep a promise on.
    surfaces = ("discord", "dashboard", "voice")

    async def execute(self, input: dict[str, Any], ctx: ToolContext) -> str:
        from core.agent import hands_runtime as hr

        client = hr.get_client()
        vc = None
        for g in (getattr(client, "guilds", []) or []):
            vc = getattr(g, "voice_client", None)
            if vc is not None:
                break
        if vc is None:
            return "I'm not in a voice channel."
        channel = getattr(vc, "channel", None)
        chan_name = getattr(channel, "name", "voice")
        try:
            await _stop_listening(getattr(channel, "id", None))
        except Exception:
            pass
        await vc.disconnect()
        _deed("hands", f"left voice channel {chan_name}")
        return f"Left {chan_name}."


# Batch 8, task 10: what speak_line's description gains while voice.voice_notes
# is on (no voice channel → a voice message in the chat).
_VOICE_NOTE_POINTER = (
    " To send a voice message in a Discord, Telegram or phone-app chat instead, use voice_note."
)


class SpeakLineTool:
    name = "speak_line"
    description = (
        "Say one line out loud in the voice channel you're currently in. "
        "Greet them when they walk in, answer aloud, think out loud — your "
        "voice, your judgment. With no live voice session to take it, the "
        "line goes to them as text on the private chat they used last instead, and "
        "the result tells you so — unless that chat is this very "
        "conversation: then nothing is sent, and you say it in your reply."
    )
    input_schema = {
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
    }
    requires_approval = False
    owner_only = True
    # Telegram door (2026-09-14, task 1): this hand reaches for a Discord
    # object (a voice channel, a member list, her own presence) through the
    # global client in hands_runtime, not through anything on the ctx — so
    # it works from wherever she is ALREADY reachable, and the list is the
    # platforms that offered it before the surface gate existed. Telegram
    # is the one door it cannot keep a promise on.
    surfaces = ("discord", "dashboard", "voice")

    def __init__(self) -> None:
        # Batch 8, task 10 (B8-5): with voice messages on, the line she can't
        # speak in a voice channel has a second home — a voice message in the
        # chat. The INSTANCE says so; the class text never changes, so with
        # the setting off her tool list is byte-identical.
        if _voice_replies.voice_notes_enabled():
            self.description = SpeakLineTool.description + _VOICE_NOTE_POINTER

    async def execute(self, input: dict[str, Any], ctx: ToolContext) -> str | ToolFailure:
        from core.agent import hands_runtime as hr

        # Checked first: with no line there is nothing to speak OR to send.
        text = str(input.get("text") or "").strip()
        if not text:
            return "Nothing to say."
        client = hr.get_client()
        vc = None
        for g in (getattr(client, "guilds", []) or []):
            vc = getattr(g, "voice_client", None)
            if vc is not None:
                break
        if vc is None or getattr(vc, "channel", None) is None:
            return await _send_unspoken_line(
                text, ctx, why="I'm not in a voice session (join a channel first)")
        ok = await _say_in_session(vc.channel.id, text)
        if not ok:
            return await _send_unspoken_line(
                text, ctx, why="the voice session couldn't take the line")
        _deed("hands", f'spoke aloud: "{text[:60]}"')
        return "Said it."


# ── Batch 8, task 10 (B8-5; rulings T10-1..T10-8): voice messages ─────────
# Two more hands beside speak_line, on her belt only while voice.voice_notes
# is on (build_her_hands_tools below). Their door is a Discord or Telegram
# text chat — the doors that send a reply's voice notes (T10-7).

def _chat_key(ctx: ToolContext) -> str | None:
    """This conversation's key when the turn is in a Discord or Telegram text
    chat, else None — the only places a voice message has a door."""
    surface = str(getattr(ctx, "surface", "") or "")
    try:
        channel_id = int(getattr(ctx, "channel_id", 0) or 0)
    except (TypeError, ValueError):
        return None
    if surface not in _voice_replies.CHAT_SURFACES or not channel_id:
        return None
    return _voice_replies.key_for(surface, channel_id, getattr(ctx, "turn_owner_id", 0))


class VoiceNoteTool:
    name = "voice_note"
    # R15, Group V: items 30-33 (Draft 2, the owner's yes 10-13). The note is
    # made when the reply is finished, from the last words she gave
    # (core/agent/voice_note_final.py), so the voice and the text agree.
    description = (
        "Send a voice message in your own voice in this chat: an audio message on "
        "Discord, a voice note on Telegram. "
        "One per reply: the words you give are spoken once your reply is finished, so "
        "they say what you concluded; if you change your mind before you finish, call it "
        "again with the new words and the last call wins. "
        "Your text reply, if you write one, goes beside it as a text message. "
        "Use it when they ask for a voice message. While voice replies are on here you "
        "don't need it: just write your reply and it is spoken."
    )
    # Items 34 and 35: what the tool answers, first and on every later call.
    NOTED = ('Voice message noted; it will say: "{words}". It is made when you finish, '
             "after your text, so the two agree. Finish now: no text needed, or only a "
             "short line if there's a link, a code or a number to read.")
    REPLACED = 'Replaced; your voice message will now say: "{words}". Finish now.'
    input_schema = {
        "type": "object",
        "properties": {"text": {"type": "string",
                                "description": "what you'll say aloud"}},
        "required": ["text"],
    }
    requires_approval = False
    owner_only = True
    surfaces = ("discord", "telegram", "mobile")

    def offered(self, ctx: ToolContext) -> bool:
        """Second read F3 (2026-10-04): while voice replies are on in this
        chat, she has no use for this tool — her reply is spoken — so it is
        left off her belt for the turn (``ToolRegistry.schemas_for``) rather
        than answered "Nothing made" each time a run calls it. Anywhere else
        it is offered as before; a call that comes anyway still gets the
        answer in execute below."""
        key = _chat_key(ctx)
        return key is None or not _voice_replies.is_on(key)

    async def execute(self, input: dict[str, Any], ctx: ToolContext) -> str | ToolFailure:
        text = str(input.get("text") or "").strip()
        if not text:
            return "Nothing to say."
        key = _chat_key(ctx)
        if key is None:
            return ToolFailure(
                "No voice message — they go out in a Discord, Telegram or phone-app "
                "chat, and this turn isn't in one.")
        # DEFECT §56 (the 10-01 20:03 loop): none while voice replies are on
        # here — guidance, not a failure, so she finishes. One per reply is
        # now the words' single entry (the last call wins) and the one note
        # the runner makes at the end (voice_note_final.finish).
        if _voice_replies.is_on(key):
            return ("Nothing made — voice replies are on here, so you don't need "
                    "voice_note: just write your reply. It is spoken and goes out "
                    "as a voice message, and any link, code block or number in "
                    "backticks also goes as a short text line on its own.")
        # R15, Group V: nothing is spoken here. The words wait on the turn
        # (one entry, the last call's) and the runner makes the note when the
        # reply is finished; set only after every check above has passed (the
        # deliveries contract). Second read F8 reads the same list for her
        # transcript beside a quiet choice.
        replacing = bool(ctx.voice_note_words)
        ctx.voice_note_words[:] = [text]
        answer = self.REPLACED if replacing else self.NOTED
        return answer.format(words=_voice_note_final.quoted(text))


_ON_WORDS = {"on", "yes", "true", "enable", "enabled"}
_OFF_WORDS = {"off", "no", "false", "disable", "disabled"}
_STATUS_WORDS = {"status", "state", ""}


class VoiceRepliesTool:
    name = "voice_replies"
    description = (
        "Turn voice replies on or off for this conversation, when they ask "
        "(\"send me voice messages until I say otherwise\"). While they're on, "
        "just write your replies: they go as voice messages, and any link, code "
        "block or number in backticks also goes as a short text line on its "
        "own. The switch stays as you set it, "
        "across restarts, until they ask you to change it. state: on, off, or "
        "status."
    )
    input_schema = {
        "type": "object",
        "properties": {"state": {"type": "string", "enum": ["on", "off", "status"]}},
        "required": ["state"],
    }
    requires_approval = False
    owner_only = True
    surfaces = ("discord", "telegram", "mobile")

    async def execute(self, input: dict[str, Any], ctx: ToolContext) -> str | ToolFailure:
        word = str(input.get("state") or "").strip().lower()
        if word not in _ON_WORDS | _OFF_WORDS | _STATUS_WORDS:
            return ToolFailure("state must be on, off or status.")
        key = _chat_key(ctx)
        if key is None:
            return ToolFailure(
                "Voice replies belong to a Discord, Telegram or phone-app chat, and "
                "this turn isn't in one.")
        # A host with no voice (final fix wave, ruling FW-15): the switch
        # still flips — it works the moment a voice is configured — but what
        # she reads back never promises voice; her replies go as text.
        no_voice = _voice_replies.no_voice_on_this_host()
        if word in _STATUS_WORDS:
            state = "on" if _voice_replies.is_on(key) else "off"
            if state == "on" and no_voice:
                return ("Voice replies are on in this conversation, but no voice is "
                        "configured on this host, so your replies here go as text "
                        "until one is.")
            return f"Voice replies are {state} in this conversation."
        on = word in _ON_WORDS
        try:
            _voice_replies.set_on(key, on)
        except Exception as exc:  # noqa: BLE001 — a switch not saved is said plainly
            return ToolFailure(f"Couldn't save the switch: {exc}")
        if on and no_voice:
            return ("Voice replies are on in this conversation — though no voice is "
                    "configured on this host, so your replies here go as text until "
                    "one is.")
        if on:
            return ("Voice replies are on in this conversation — your replies "
                    "here go as voice messages until they ask you to stop. Just "
                    "write them; no voice_note needed.")
        return "Voice replies are off in this conversation — your replies here go as text."


class ListMembersTool:
    name = "list_members"
    description = (
        "See the people on your server: names, display names, ids, who's a "
        "bot, who's online right now. Optional query narrows it. Use this "
        "the way a person squints at a contact list — 'Edddy' probably "
        "means Eddie, and that read is yours to make."
    )
    input_schema = {
        "type": "object",
        "properties": {"query": {"type": "string"}},
    }
    requires_approval = False
    owner_only = True
    # Telegram door (2026-09-14, task 1): this hand reaches for a Discord
    # object (a voice channel, a member list, her own presence) through the
    # global client in hands_runtime, not through anything on the ctx — so
    # it works from wherever she is ALREADY reachable, and the list is the
    # platforms that offered it before the surface gate existed. Telegram
    # is the one door it cannot keep a promise on.
    surfaces = ("discord", "dashboard", "voice", "telegram")

    async def execute(self, input: dict[str, Any], ctx: ToolContext) -> str:
        from core.agent import hands_runtime as hr

        if str(getattr(ctx, "surface", "") or "") == "telegram_text":
            return await _telegram_room(int(getattr(ctx, "channel_id", 0) or 0))
        # Step 2, part 7 (2026-10-08): two plain answers, never one vague one.
        # "client not ready, or no match" let her tell the owner, wrongly,
        # that Discord wasn't answering when nobody had matched.
        if not hr.client_connected():
            return "Discord isn't connected right now, so I can't see anyone there."
        query = str(input.get("query") or "")
        here = int(getattr(ctx, "channel_id", 0) or 0) if str(
            getattr(ctx, "surface", "") or "").startswith("discord") else None
        members, scoped = hr.members_here(query, channel_id=here)
        if not members:
            if query.strip():
                return f"Nobody I can see on Discord matches '{query.strip()}'."
            return "I can't see anyone on Discord right now."
        owner_id = await _owner_discord_id()
        members.sort(key=lambda m: m["id"] != owner_id)         # the owner first
        from core.agent.speakers import clean_name

        lines = [
            f"- {clean_name(m['display_name'])} (@{clean_name(m['name'])}, id {m['id']})"
            + (" [bot]" if m["bot"] else "")
            + (f" — {m['status']}" if m.get("status") else "")
            + (" — the owner" if owner_id and m["id"] == owner_id else "")
            for m in members[:40]
        ]
        head = "People who can see this channel:" if scoped else "People I can see:"
        return head + "\n" + "\n".join(lines)


async def _telegram_room(chat_id: int) -> str:
    """Who's in a Telegram group (step 2, part 8): the Bot API can't list a
    group's members, only its admins and how many there are; who has spoken
    lately is in her room card."""
    from core.agent import hands_runtime as hr

    bot = hr.get_telegram_bot()
    if bot is None:
        return "Telegram isn't connected right now, so I can't see anyone there."
    if chat_id > 0:                       # a private chat's id is the person's
        return "This is a direct message: just the two of us here."
    try:
        admins = list(await bot.get_chat_administrators(chat_id))
        count = await bot.get_chat_member_count(chat_id)
    except Exception:
        return "Telegram didn't answer just now, so I can't see who's here."
    owner = (await _owner_refs()).get("telegram_user_id")
    users = [getattr(a, "user", None) for a in admins]
    users = [u for u in users if u is not None]
    users.sort(key=lambda u: getattr(u, "id", None) != owner)       # the owner first
    from core.agent.speakers import clean_name

    lines = [
        f"- {clean_name(getattr(u, 'full_name', '') or getattr(u, 'username', ''))}"
        + (f" (@{clean_name(u.username)}, id {u.id})" if getattr(u, "username", None) else f" (id {u.id})")
        + (" [bot]" if getattr(u, "is_bot", False) else "")
        + (" — the owner" if owner and getattr(u, "id", None) == owner else "")
        for u in users
    ]
    return (f"This group has {count} members. Telegram only lets me list its admins:\n"
            + "\n".join(lines) + "\nWho has spoken lately is in my room card.")


async def _owner_refs() -> dict[str, Any]:
    try:
        from core import local_mode

        return dict(await local_mode.get_local_owner_refs())
    except Exception:
        return {}


async def _owner_discord_id() -> int | None:
    raw = (await _owner_refs()).get("discord_user_id")
    try:
        return int(raw) if raw else None
    except (TypeError, ValueError):
        return None


# "the owner", "owner", "the owner on telegram" (step 2, add-on b).
_OWNER_DESTINATION = re.compile(r"^(?:the )?owner(?: on (discord|telegram))?$")
_DISCORD_DOWN = "Discord isn't connected right now, so nothing was sent."


def _telegram_chat_id(destination: str) -> int | None:
    """``telegram:<chat_id>`` / ``tg:<chat_id>`` → the chat id, else None.

    Group ids are NEGATIVE, so the sign is part of the address.
    """
    raw = str(destination or "").strip()
    for prefix in ("telegram:", "tg:"):
        if raw.lower().startswith(prefix):
            try:
                return int(raw[len(prefix):].strip())
            except (TypeError, ValueError):
                return None
    return None


class SendMessageTool:
    name = "send_message"
    description = (
        "Send a message somewhere OTHER than this conversation: a channel "
        "('#print-status'), a person by name (their DMs), or a raw id / "
        "mention straight from list_members. Your hand, your "
        "judgment — including messaging people who aren't them. That acts on "
        "their social world: be the kind of presence they'd be proud of, and "
        "when you're genuinely unsure, asking them is always available to "
        "you. Every send is recorded where you and they can both see it. For "
        "THIS conversation, just reply normally. A Telegram chat is an "
        "address, not a name: 'telegram:<chat_id>' (or 'tg:<chat_id>') goes "
        "straight there. 'the owner' (or 'the owner on telegram' / 'the owner "
        "on discord') goes straight to the owner's own account; '@Name' "
        "always means a person, never a channel."
    )
    input_schema = {
        "type": "object",
        "properties": {"to": {"type": "string"}, "text": {"type": "string"}},
        "required": ["to", "text"],
    }
    requires_approval = False
    owner_only = True

    async def execute(self, input: dict[str, Any], ctx: ToolContext) -> str:
        from core.agent import hands_runtime as hr

        to = str(input.get("to") or "").strip()
        text = str(input.get("text") or "").strip()
        if not to or not text:
            return "I need both a destination and something to say."
        owner_to = _OWNER_DESTINATION.match(" ".join(to.lower().split()))
        if owner_to:
            return await self._to_owner(owner_to.group(1), text, ctx)

        # Telegram door (2026-09-14, task 2): a Telegram chat has no name to
        # resolve — the id IS the address, and there is no member cache to
        # look it up in. Taken before the Discord resolvers, which would
        # otherwise answer "nothing and nobody I can see matches that".
        telegram_chat = _telegram_chat_id(to)
        if telegram_chat is not None:
            if telegram_chat == int(getattr(ctx, "channel_id", 0) or 0) and str(
                    getattr(ctx, "surface", "") or "") == "telegram_text":
                return ("That's this conversation — just reply. This lever "
                        "is for elsewhere.")
            await _record_outbound(
                owner_id=int(ctx.turn_owner_id),
                channel_id=telegram_chat,
                text=text,
                origin="her_hands",
                extra={},
                platform="telegram",
                surface_id=str(telegram_chat),
            )
            return f"Sent to telegram chat {telegram_chat}: {text[:80]}"

        if not hr.client_connected():
            return _DISCORD_DOWN
        channel = None
        member = None
        ch_cands: list = []
        m_cands: list = []
        # §1.5: raw ids and pasted mentions are first-class destinations —
        # list_members hands her ids, so ids must route.
        raw_id = hr.parse_raw_id(to)
        if raw_id is None and to.startswith("@"):
            # "@Name" is a person (2026-10-08): never a channel called that.
            member, m_cands = hr.resolve_member(to[1:].strip())
            if member is None:
                if m_cands:
                    return f"More than one person could be '{to}': {', '.join(m_cands)}. Your call."
                return f"Nobody I can see on Discord is called '{to}'."
        elif raw_id is not None:
            channel = hr.find_text_channel_by_id(raw_id)
            member = hr.find_member_by_id(raw_id) if channel is None else None
            if channel is None and member is None:
                return f"No channel or person I can see carries id {raw_id}."
        else:
            channel, ch_cands = hr.resolve_text_channel(to)
        target_id = None
        recipient = None
        if channel is not None:
            if int(getattr(channel, "id", 0)) == int(getattr(ctx, "channel_id", 0) or 0):
                return ("That's this conversation — just reply. This lever "
                        "is for elsewhere.")
            target_id = int(channel.id)
            where = f"#{channel.name}"
        else:
            if member is None:
                member, m_cands = hr.resolve_member(to)
            if member is None:
                cands = sorted(set(ch_cands + m_cands))[:6]
                if cands:
                    return (f"More than one place/person could be '{to}': "
                            f"{', '.join(cands)}. Your call.")
                return f"Nothing and nobody I can see matches '{to}'."
            # The owner's own account, by handle, id or shown name, is the
            # owner, never filed as someone else's (the friend-test
            # rehearsal, 2026-10-09: "DM me on Discord" went to the owner's own handle).
            owner_discord = await _owner_discord_id()
            if owner_discord and int(getattr(member, "id", 0) or 0) == owner_discord:
                return await self._to_owner("discord", text, ctx)
            dm = await member.create_dm()
            target_id = int(dm.id)
            recipient = getattr(member, "display_name", None) or member.name
            where = f"{recipient} (DM)"

        extra = {"third_party": True, "recipient": recipient} if recipient else {}
        await _record_outbound(
            owner_id=int(ctx.turn_owner_id),
            channel_id=target_id,
            text=text,
            origin="her_hands",
            extra=extra,
        )
        return f"Sent to {where}: {text[:80]}"

    async def _to_owner(self, app: str | None, text: str, ctx: ToolContext) -> str:
        """The owner's own account: on the app asked for, else Discord when
        the owner can be found there, else Telegram when its account is known."""
        from core.agent import hands_runtime as hr

        refs = await _owner_refs()
        discord_id, telegram_id = refs.get("discord_user_id"), refs.get("telegram_user_id")
        member = (hr.find_member_by_id(int(discord_id))
                  if discord_id and hr.client_connected() else None)
        if app is None:
            app = "discord" if member is not None or not telegram_id else "telegram"
        here = int(getattr(ctx, "channel_id", 0) or 0)
        surface = str(getattr(ctx, "surface", "") or "")
        if app == "telegram":
            if not telegram_id:
                return "I don't have the owner's Telegram account on record, so nothing was sent."
            if surface == "telegram_text" and here == int(telegram_id):
                return "That's this conversation — just reply. This lever is for elsewhere."
            await _record_outbound(
                owner_id=int(ctx.turn_owner_id), channel_id=int(telegram_id), text=text,
                origin="her_hands", extra={}, platform="telegram", surface_id=str(telegram_id),
            )
            return f"Sent to the owner on Telegram: {text[:80]}"
        if not hr.client_connected():
            return _DISCORD_DOWN
        if not discord_id:
            return "I don't have the owner's Discord account on record, so nothing was sent."
        if member is None:
            return (f"I can't find the owner's Discord account (id {discord_id}) on any "
                    "server I'm in, so nothing was sent.")
        dm = await member.create_dm()
        if surface == "discord_text" and int(dm.id) == here:
            return "That's this conversation — just reply. This lever is for elsewhere."
        await _record_outbound(owner_id=int(ctx.turn_owner_id), channel_id=int(dm.id),
                               text=text, origin="her_hands", extra={})
        return f"Sent to the owner on Discord (DM): {text[:80]}"


_VALID_STATUSES = ("online", "idle", "dnd", "invisible")

# Step 27 · Duplex (spec §5): the signage debounce — code, ≤ 1 change / 30 s
# while the flag is on; a flag-off build never looks at it.
PRESENCE_DEBOUNCE_SECONDS = 30.0
_last_presence_change: float = 0.0


class SetPresenceTool:
    name = "set_presence"
    description = (
        "Set your own Discord presence — status (online/idle/dnd/invisible) "
        "and/or a short activity line ('heads down on the workbench', 'in "
        "voice with them'). Your body, your signage. Empty activity clears it. "
        "When you start something that will take a while, add expect_minutes "
        "— your own rough estimate; it is how you'll notice if it runs long. "
        "Signage only for work that is actually running in this turn or a task "
        "you have started — never for waiting on them, and never as a stand-in "
        "for doing the thing."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "status": {"type": "string"},
            "activity": {"type": "string"},
            "expect_minutes": {"type": "number",
                               "description": "roughly how long the thing you are starting will take"},
        },
    }
    requires_approval = False
    owner_only = True
    # Telegram door (2026-09-14, task 1): this hand reaches for a Discord
    # object (a voice channel, a member list, her own presence) through the
    # global client in hands_runtime, not through anything on the ctx — so
    # it works from wherever she is ALREADY reachable, and the list is the
    # platforms that offered it before the surface gate existed. Telegram
    # is the one door it cannot keep a promise on.
    surfaces = ("discord", "dashboard", "voice")

    async def execute(self, input: dict[str, Any], ctx: ToolContext) -> str:
        global _last_presence_change
        from core.agent import hands_runtime as hr

        client = hr.get_client()
        if client is None:
            return "My Discord body isn't connected right now."
        status_raw = str(input.get("status") or "").strip().lower()
        activity_raw = str(input.get("activity") or "").strip()
        if status_raw and status_raw not in _VALID_STATUSES:
            return ("I can be: " + ", ".join(_VALID_STATUSES)
                    + f" — '{status_raw}' isn't one of them.")
        # Step 27 (spec §5): with duplex on, the signage gets a debounce
        # (code) and feeds two run facts — her activity, so the runner can
        # clear it when the work ends, and her estimate, the time sense.
        # Flag off: none of this runs — today's hand, byte-identical.
        from core.agent import duplex as _dx
        _duplex = _dx.duplex_enabled()
        if _duplex:
            if time.monotonic() - _last_presence_change < PRESENCE_DEBOUNCE_SECONDS:
                return "Presence unchanged — I set it a moment ago; the signage doesn't flicker."
        import discord
        kwargs = {}
        if status_raw:
            kwargs["status"] = getattr(discord.Status, status_raw)
        kwargs["activity"] = (
            discord.CustomActivity(name=activity_raw) if activity_raw else None
        )
        await client.change_presence(**kwargs)
        if _duplex:
            _last_presence_change = time.monotonic()
            _sk = f"discord:{int(getattr(ctx, 'channel_id', 0) or 0)}"
            from core.agent import steering as _st
            _st.note_presence_activity(_sk, activity_raw)
            try:
                _em = float(input.get("expect_minutes") or 0)
            except (TypeError, ValueError):
                _em = 0.0
            if _em > 0:
                _st.set_expectation(_sk, _em)
        _deed("hands", f"set presence: {status_raw or 'unchanged'} / {activity_raw or 'no activity'}")
        bits = [b for b in (status_raw, activity_raw) if b]
        return "Presence set: " + (" — ".join(bits) if bits else "cleared.")


def build_her_hands_tools() -> list:
    """The six tools, in registration order. Populated by the voice trio,
    people/reach, and presence tasks. Batch 8, task 10: plus voice_note and
    voice_replies while voice.voice_notes is on (off: today's six, byte for
    byte)."""
    tools = [
        JoinVoiceChannelTool(), LeaveVoiceTool(), SpeakLineTool(),
        ListMembersTool(), SendMessageTool(), SetPresenceTool(),
    ]
    if _voice_replies.voice_notes_enabled():
        tools.extend([VoiceNoteTool(), VoiceRepliesTool()])
    return tools
