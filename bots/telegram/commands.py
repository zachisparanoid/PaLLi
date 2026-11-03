"""The six commands on her Telegram door (telegram door, 2026-09-14, task 3).

Telegram has something Discord does not: a MENU. ``set_my_commands``
publishes the list behind the "/" button beside the text box, so what the
door offers is visible rather than guessable. Six of them —
``/start /help /link /proactive /insights /voice``.

Glue only. ``/link``, ``/proactive`` and ``/insights`` are the same three
commands Discord now offers, and the words they say come from
``bots/common/commands.py``; the same word means the same thing at both
doors because it IS the same function. What lives here is Telegram's half:
pull the message off an Update, read the argument, send the answer with
``send_turn_reply``.

Two deliberate shapes:

  * **``/start`` is fixed text and makes no model call.** Telegram sends it
    by itself the first time a chat is opened, so it is the first thing she
    ever says to a stranger — and a generated greeting is a greeting that
    can promise something she is not. First contact proper is the reflect
    lane's job here exactly as it is on Discord; this is a door sign.
  * **``/stop`` is deliberately NOT here.** It is not a command she answers;
    it is a word that stops a running turn, and the client's catch-all
    handler already carries it into the owner lane where the cooperative
    stop lives (bots/telegram/client.py:register_handlers). A CommandHandler
    for it would intercept the update and never reach that lane.

The library is imported INSIDE ``publish_commands`` only: the package must
import cleanly on a host where python-telegram-bot is absent.
"""
from __future__ import annotations

import logging
from typing import Any, Callable

from bots.common.commands import (
    TROUBLE_LINE,
    insights_text,
    link_platform_account,
    owner_only_line,
    proactive_status,
    resolve_caller,
    set_proactive_consent,
)
from bots.common.steering import get_session_flag, set_session_flag
from bots.telegram import intake as _intake
from bots.telegram.reply import send_turn_reply
from core import voice_replies as _voice_replies

logger = logging.getLogger(__name__)

PLATFORM = "telegram"

# Name, and the one line Telegram shows beside it in the menu. The order is
# the order of the menu and of /help.
COMMAND_SPECS: tuple[tuple[str, str], ...] = (
    ("start", "who I am"),
    ("help", "what I can do here"),
    ("link", "pair this chat with your dashboard account: /link <code>"),
    ("proactive", "reach-outs: /proactive on|off|status"),
    ("insights", "what I've learned about you"),
    ("voice", "voice-note replies here: /voice on|off"),
)

# The per-chat switch ``/voice`` throws; task 5 reads it when it decides
# whether to answer with a voice note.
VOICE_FLAG = "voice_replies"

_START_DM = (
    "I'm PaLLi — a companion that runs on one person's own machine instead "
    "of somebody's cloud. This chat is yours with me: talk to me about "
    "whatever you like, send files or voice notes, and I'll keep track of "
    "what matters to you. Type /help to see what else I can do here."
)

_YES = {"on", "yes", "true", "enable", "enabled"}
_NO = {"off", "no", "false", "disable", "disabled"}
_STATUS = {"status", "state", "?"}

# Telegram has no ephemeral message: whatever a command answers, the whole
# room reads. So the one command that prints what she has LEARNED about
# somebody only answers where the room is one person (review round 1).
_DM_ONLY = "I'll only say that in a DM — message me directly."

# Which commands cost a stranger anything (review round 1, addendum). These
# four reach the database, or are the owner's to begin with, so a non-owner
# passes the guest gate for them. ``/start`` and ``/help`` are NOT here:
# they are fixed text, no read and no model call, so they cost nothing to
# serve — and "I'm at my limit for guests right now" is the wrong first
# thing a stranger ever hears, given that Telegram sends ``/start`` by
# itself the moment a chat opens.
BUDGETED_COMMANDS = frozenset({"link", "proactive", "insights", "voice"})


# ── the menu ─────────────────────────────────────────────────────────────

def _bot_command_factory(name: str, description: str) -> Any:
    """Build one ``telegram.BotCommand``.

    A module-level seam for the same reason ``_HandlerFactory`` is one in
    client.py: this is the only place the library's constructor is touched,
    so a test can watch what would be published without importing it.
    """
    from telegram import BotCommand

    return BotCommand(name, description)


async def publish_commands(bot: Any) -> None:
    """Publish the "/" menu. Never raises.

    A menu that would not publish is a cosmetic failure; a door that will
    not open because of one is not. Logged, and polling carries on.
    """
    try:
        payload = [_bot_command_factory(name, description)
                   for name, description in COMMAND_SPECS]
        await bot.set_my_commands(payload)
        logger.info("telegram commands: published %d menu entries", len(payload))
    except Exception:  # noqa: BLE001
        logger.exception(
            "telegram commands: publishing the menu failed (the commands "
            "still work; only the menu is missing)")


# ── the plumbing ─────────────────────────────────────────────────────────

def _command_args(message: Any, context: Any) -> list[str]:
    """The words after the command.

    ``context.args`` is python-telegram-bot's own parse and is preferred
    when it is there; the text split is the fallback that lets every one of
    these callbacks be driven without the library present.
    """
    args = getattr(context, "args", None)
    if isinstance(args, (list, tuple)):
        return [str(arg) for arg in args]
    parts = str(getattr(message, "text", "") or "").split()
    return parts[1:]


async def _answer(client: Any, chat_id: int, text: str) -> None:
    await send_turn_reply(getattr(client, "bot", None), chat_id, text)


def _help_text() -> str:
    return "\n".join(
        ["What I can do here:"]
        + [f"/{name} — {description}" for name, description in COMMAND_SPECS])


def _is_dm(message: Any) -> bool:
    chat = getattr(message, "chat", None)
    return str(getattr(chat, "type", "") or "") == "private"


def _start_text(client: Any, chat_type: str) -> str:
    if str(chat_type or "") == "private":
        return _START_DM
    handle = str(getattr(client, "_bot_username", "") or "").strip().lstrip("@")
    mention = f"@{handle}" if handle else "@mention"
    # Step 2, part 8: with group chat on she reads along and decides, so
    # the old line is untrue, unless Telegram's privacy setting is on, which
    # makes it true again (she then only sees mentions and replies).
    from core.agent import quiet_choice as _qc
    from core.agent import room_card as _room_card

    if _qc.she_decides_enabled() and not _room_card.telegram_privacy_on():
        return (
            "I'm PaLLi. In a group I read along and answer when something's for "
            f"me, the way anyone here would. To be sure I answer, {mention} me or "
            "reply to one of my messages."
        )
    return (
        f"I'm PaLLi. In a group I only answer when you {mention} me or reply "
        "to one of my messages — the rest of your conversation stays yours."
    )


# ── the six ──────────────────────────────────────────────────────────────

async def _start(client, caller, message, args) -> str:
    return _start_text(client, getattr(getattr(message, "chat", None), "type", ""))


async def _help(client, caller, message, args) -> str:
    return _help_text()


async def _link(client, caller, message, args) -> str:
    if not args:
        return ("Send it with the code from your dashboard, like this: "
                "/link ABC123")
    return await link_platform_account(
        platform=PLATFORM, platform_user_id=caller.platform_user_id,
        code=args[0])


async def _proactive(client, caller, message, args) -> str:
    word = (args[0].strip().lower() if args else "")
    if not word or word in _STATUS:
        return await proactive_status(caller)
    if word in _YES:
        return await set_proactive_consent(caller, True)
    if word in _NO:
        return await set_proactive_consent(caller, False)
    return "I know /proactive on, /proactive off and /proactive status."


async def _insights(client, caller, message, args) -> str:
    # DM ONLY (review round 1, important 1). Discord answers this in an
    # ephemeral followup that only the asker sees; Telegram has no such
    # thing, so in a supergroup this would print somebody's traits,
    # remembered facts and interests to the whole room. The read does not
    # even happen out here — refusing after fetching would still have pulled
    # it into a process that is about to write to a group.
    if not _is_dm(message):
        return _DM_ONLY
    return await insights_text(caller)


def _no_voice_on_this_host() -> bool:
    """Did boot find no TTS for this run?

    The one answer lives in ``core.voice_replies.no_voice_on_this_host``
    (final fix wave, ruling FW-15), so her status line and her
    ``voice_replies`` tool read the same one this command does: the field
    the speech facade itself reads (``_voice_lane_off`` in voice/speech.py),
    "off" when the configured provider has no key — never the voice stack.
    Empty means validate() has not spoken, and nothing is claimed.
    """
    return _voice_replies.no_voice_on_this_host()


async def _voice(client, caller, message, args) -> str:
    # OWNER ONLY (review round 1, minor 3). Once task 5 reads this flag, a
    # guest in a group could otherwise make her answer the room out loud.
    if not caller.is_owner:
        return owner_only_line("Voice replies")
    chat_id = int(getattr(getattr(message, "chat", None), "id", 0) or 0)
    session_key = f"{PLATFORM}:{chat_id}"
    word = (args[0].strip().lower() if args else "")
    if word in _YES:
        set_session_flag(session_key, VOICE_FLAG, True)
    elif word in _NO:
        set_session_flag(session_key, VOICE_FLAG, False)
    elif word and word not in _STATUS:
        return "I know /voice on and /voice off."
    state = "on" if get_session_flag(session_key, VOICE_FLAG) else "off"
    if _voice_replies.voice_notes_enabled():
        # Batch 8, task 10 (B8-5, ruling T10-6): with voice messages on, this
        # is the remembered switch her voice_replies tool also sets — her
        # replies here go as voice, for ANY message, until it is turned off.
        return _voice_switch_line(state)
    # The CONDITION, said out loud (task 6 review, important 1): the switch
    # only speaks her answer to a VOICE NOTE — `_voice_note_for` in
    # bots/telegram/client.py returns None for anything else. "Voice replies
    # are on" on its own sent a reader off to type a sentence and wait for
    # audio that was never coming.
    if _no_voice_on_this_host():
        # The switch still flips — it works the moment a key is added — but
        # answering a bare "on" where nothing can speak promises something
        # this host cannot do (review round 1, important 2).
        return (f"Voice replies are {state} in this chat: send me a voice "
                "note and my answer would come back as one — though no voice "
                "is configured on this host, so I'll keep answering in text "
                "until it is. It resets when I restart.")
    return (f"Voice replies are {state} in this chat: send me a voice note "
            "and my answer comes back as one too. It resets when I restart.")


def _voice_switch_line(state: str) -> str:
    """``/voice``'s answer while ``voice.voice_notes`` is on (Batch 8, task
    10): what the switch now does, and that a restart keeps it."""
    if _no_voice_on_this_host():
        return (f"Voice replies are {state} in this chat. While they're on, my "
                "replies here come as voice messages — though no voice is "
                "configured on this host, so I'll keep answering in text until it "
                "is. The switch stays as you set it — a restart doesn't change it.")
    return (f"Voice replies are {state} in this chat. While they're on, my replies "
            "here come as voice messages, with a short text line when there's "
            "something to read. The switch stays as you set it — a restart "
            "doesn't change it.")


_COMMANDS: dict[str, Callable] = {
    "start": _start,
    "help": _help,
    "link": _link,
    "proactive": _proactive,
    "insights": _insights,
    "voice": _voice,
}


def build_handlers(client: Any) -> list[tuple[str, Callable]]:
    """(name, async callback(update, context)) for every spec, in order.

    ``bots/telegram/client.py::start_telegram_client`` puts these in
    ``command_specs`` before ``register_handlers()``, which registers them
    AHEAD of the message handlers — the library runs the first matching
    handler in a group, so a command offered after the catch-all would
    never be offered at all.
    """
    handlers: list[tuple[str, Callable]] = []
    for name, _description in COMMAND_SPECS:
        handlers.append((name, _make_callback(client, name, _COMMANDS[name])))
    return handlers


def _make_callback(client: Any, name: str, body: Callable) -> Callable:
    async def _callback(update: Any, context: Any = None) -> None:
        message = _intake._message_of(update)
        if message is None:
            return
        author = getattr(message, "from_user", None)
        chat = getattr(message, "chat", None)
        chat_id = int(getattr(chat, "id", 0) or 0) if chat is not None else 0
        if author is None or not chat_id:
            return

        author_id = int(getattr(author, "id", 0) or 0)
        gate = getattr(client, "_guest_gate", None)
        admitted = False
        try:
            caller = await resolve_caller(
                platform=PLATFORM, platform_user_id=author_id)
            if (name in BUDGETED_COMMANDS
                    and not caller.is_owner and gate is not None):
                # A stranger's command is a database round-trip like their
                # turn, so it spends the same budget (review round 1, minor
                # 2). ONE cap across every door — the money is one account
                # — and his own commands are never counted against it.
                if not gate.admit(author_id):
                    await _answer(client, chat_id, gate.AT_CAPACITY_LINE)
                    return
                admitted = True
            text = await body(client, caller, message,
                              _command_args(message, context))
        except Exception:  # noqa: BLE001
            # python-telegram-bot swallows what a handler raises, so an
            # unanswered command is a thing he asked for that simply never
            # happened (DEFECT §19's shape). Say so instead.
            logger.exception("telegram commands: /%s failed", name)
            text = TROUBLE_LINE
        finally:
            # A slot taken and not given back is taken for the life of the
            # process; at the default cap of 2, two such failures shut the
            # guest door for good.
            if admitted:
                gate.release(author_id)

        await _answer(client, chat_id, text)

    _callback.__name__ = f"telegram_command_{name}"
    return _callback
