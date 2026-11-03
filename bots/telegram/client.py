"""Her second door, on the same spine (telegram door, 2026-09-14, task 2).

A PORT of bots/discord/client.py's orchestration onto another transport,
kept deliberately parallel to it — same order, same names, same comments
where the reasoning is the same — so the follow-up that unifies the two into
``bots/common/lane.py`` is a diff rather than an archaeology. The Discord
client is untouched: sixty-odd tests patch ``bots.discord.client.*``, and
extracting the lane under them is a separate piece of work with its own
risk. The duplication is a recorded cost, not an oversight. Telegram's half
of that unification is done: its owner lane moved to
``bots/common/owner_lane.py`` for the mobile door (2026-10-13), and the phone
app shares it. Discord keeps its own copy for now, a recorded follow-up.

What is genuinely different, and why:

  * **The lifecycle is driven by hand.** ``Application.run_polling()`` takes
    over the event loop and installs signal handlers. She already HAS a loop
    — the backend, the moment engine, the scheduler and the Discord client
    all live on it — so this client does initialize / start_polling / start
    itself and waits on a stop event.
  * **The library is imported lazily, inside functions.** The package must
    import cleanly where python-telegram-bot is absent (the test suite, a
    fresh clone that has not installed it yet). A door that cannot open says
    so; it never crashes the boot.
  * **Her own id is learned at runtime.** ``get_me`` after initialize is the
    only way to know the bot's user id and @handle, and intake needs the
    handle to tell a mention of her from a mention of somebody else.
  * **There are no typing events from the other side.** Telegram does not
    deliver "the user is typing" to bots, so the burst coalescer only ever
    sees messages here. The settle window does the whole job.
"""
from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Any, Callable

from bots.common.delivery_notice import delivery_notice
from bots.common.guest_gate import GuestGate, shared_guest_gate
from bots.common import reply_wait
from bots.common.owner_lane import LaneHooks, OwnerLane
from bots.common.room_pass import RoomPass
from bots.common import steering as steering_fork
from bots.common.voice_note import plan_voice_reply, synthesize_voice_note
from bots.discord.recording import record_user_fragment
from bots.telegram import intake as _intake
from bots.telegram.adapter import TelegramChannelAdapter
from bots.telegram.commands import VOICE_FLAG
from bots.telegram.reply import (
    first_chunk,
    last_send_error,
    last_send_parts,
    send_turn_reply,
    send_turn_reply_cadenced,
    typing_while,
)
from core import voice_replies
from core.agent import hands_runtime as _hands_runtime
from core.agent import room_card as _room_card
from core.agent.dispatch import dispatch_turn
from core.channel_runtime import channel_runtime
from core.local_mode import get_local_owner_refs
from core.owner_notice import register_owner_notice_sender, unregister_owner_notice_sender
from core.turn.persistence import persist_delivery_failure

logger = logging.getLogger(__name__)


class PollingNetworkBlipFilter(logging.Filter):
    """A long-poll that drops on the network becomes one WARNING line.

    python-telegram-bot logs "Exception happened while polling for updates."
    at ERROR with a full traceback when a getUpdates read drops (httpx.ReadError,
    wrapped as ``telegram.error.NetworkError``), then retries on its own; on
    2026-09-29 21:13 and 09-30 09:27 the next poll succeeded 12 s later. Only a
    network drop or a timeout is quietened. ``BadRequest`` is a NetworkError
    subclass in the library but a real error, so it keeps its traceback, as do
    ``Conflict``, ``InvalidToken`` and every other Telegram error."""

    def filter(self, record: logging.LogRecord) -> bool:
        exc = record.exc_info[1] if record.exc_info else None
        if exc is None or record.levelno < logging.ERROR:
            return True
        try:
            from telegram.error import BadRequest, NetworkError
        except ImportError:
            return True
        if isinstance(exc, NetworkError) and not isinstance(exc, BadRequest):
            record.levelno = logging.WARNING
            record.levelname = "WARNING"
            record.msg = ("telegram: a poll for updates dropped on the network "
                          "(%s: %s); the library retries on its own")
            record.args = (type(exc).__name__, str(exc) or "no message")
            record.exc_info = None
            record.exc_text = None
        return True


def install_polling_blip_filter() -> None:
    """Attach the filter to the library's updater logger, once."""
    updater_log = logging.getLogger("telegram.ext.Updater")
    if not any(isinstance(f, PollingNetworkBlipFilter) for f in updater_log.filters):
        updater_log.addFilter(PollingNetworkBlipFilter())


# Strong refs to the shielded not-delivered marks (the Bug-1 retention
# pattern, like dispatch._BACKGROUND_PERSIST_TASKS): a barge-in cancels the
# task that awaits the shield, and a task nothing references can be collected
# mid-write.
_DELIVERY_MARK_TASKS: set[asyncio.Task] = set()

# How long ``stop()`` lets a guest turn already in flight finish before it
# cancels it — the shape of the duplex stop's backstop (a grace, then the
# hard cancel), because a detached task nobody waits for is a task that
# leaks past shutdown and holds a slot of the guest budget with it.
GUEST_STOP_GRACE_SECONDS = 5.0

# Module-level handle to the currently-running client, the way
# bots/discord/client.py publishes its own: consumers reach the live door
# without owning a connection of their own.
_active_client: "PalliTelegramClient | None" = None


def get_active_client() -> "PalliTelegramClient | None":
    """Return the running PalliTelegramClient, or None if not started."""
    return _active_client


class _HandlerFactory:
    """Builds the library's handler objects — the ONE place this module
    touches ``telegram.ext``.

    It is a seam because ``MessageHandler`` and ``CommandHandler`` cannot be
    constructed where the library is absent, and a test has to be able to
    watch what got registered. Production instantiates this; a test hands
    the client something with the same four methods.
    """

    def __init__(self) -> None:
        from telegram.ext import CommandHandler, MessageHandler, filters

        self._CommandHandler = CommandHandler
        self._MessageHandler = MessageHandler
        self._filters = filters

    def command(self, name: str, callback):
        return self._CommandHandler(name, callback)

    def text(self, callback):
        f = self._filters
        return self._MessageHandler(f.TEXT & ~f.COMMAND, callback)

    def voice(self, callback):
        f = self._filters
        return self._MessageHandler(f.VOICE | f.AUDIO, callback)

    def file(self, callback):
        f = self._filters
        return self._MessageHandler(f.Document.ALL | f.PHOTO, callback)

    def other(self, callback):
        # The catch-all, registered LAST (review round 1, important 2). Safe
        # beside the three above because the library runs at most ONE handler
        # per group and takes the first that matches in REGISTRATION order:
        # Application.add_handler appends to self.handlers[group]
        # (_application.py:1427) and process_update breaks after the first
        # match in a group (_application.py:1344). So a video reaches this
        # one and a text message never does.
        return self._MessageHandler(self._filters.ALL, callback)



def privacy_mode_from(me: Any) -> bool | None:
    """Telegram's privacy setting for her bot, from getMe: True when it is ON
    (in groups she is sent only what mentions her, replies to her, or is a
    command), False when off, None when the answer doesn't say. Only an
    explicit False from ``can_read_all_group_messages`` means on."""
    value = getattr(me, "can_read_all_group_messages", None)
    if value is False:
        return True
    if value is True:
        return False
    return None


class _LaneState:
    """A client attribute that lives on its owner lane now (the mobile door, 2026-10-13);
    reading or assigning it on the client reads or assigns the lane's."""

    def __set_name__(self, owner, name):
        self._attr = name.lstrip("_")

    def __get__(self, obj, objtype=None):
        return self if obj is None else getattr(obj._lane, self._attr)

    def __set__(self, obj, value):
        setattr(obj._lane, self._attr, value)


class PalliTelegramClient:
    """Coalesces a rapid burst of Telegram messages into one turn.

    Same two behaviours the Discord client has, for the same reasons:
    fragments are recorded on ARRIVAL (so a barge-in can never erase words
    he actually sent), buffered per chat, and fed to a BurstCoalescer that
    owns the settle timing; and a message arriving while she is mid-reply
    either steers the running turn or cancels it, judged at fire time. Both
    live in the owner lane (bots/common/owner_lane.py) since the mobile door
    (2026-10-13), which the phone app shares; this client is its hooks.
    """

    # The lane's per-chat state, under the names this client always had.
    _inflight_tasks = _LaneState()
    _pause_timers = _LaneState()
    _sidecars = _LaneState()
    _burst_fragments = _LaneState()
    _burst_authors = _LaneState()
    _burst_unappended = _LaneState()
    _burst_times = _LaneState()
    _owner_messages_in = _LaneState()
    _duplex_stop_backstops = _LaneState()
    _coalescer = _LaneState()

    def __init__(
        self,
        *,
        application: Any,
        owner_id: int,
        bot_user_id: int = 0,
        bot_username: str = "",
        guest_gate: GuestGate | None = None,
        sessions_root: Path | None = None,
    ) -> None:
        self._application = application
        self._owner_id = int(owner_id)
        # Both are 0/"" until get_me answers inside run_polling — the API is
        # the only thing that knows them.
        self._bot_user_id = int(bot_user_id or 0)
        self._bot_username = str(bot_username or "")
        self._stop_event = asyncio.Event()

        # Guest budgets: the live door is handed the process-wide gate, so
        # every door spends from ONE budget — the money is one account. A
        # client built directly gets a fresh gate and is isolated.
        self._guest_gate = guest_gate if guest_gate is not None else GuestGate()
        # The guest turns running in the background right now (final review,
        # I2). HELD: a task referenced by nothing can be garbage-collected
        # mid-await and simply stop, which on this path would mean a stranger
        # spoken to and a slot never given back. The done-callback discards.
        self._guest_tasks: set[asyncio.Task] = set()
        # Honors PALLI_AGENT_SESSIONS_ROOT like dispatch/outbound/proactive do.
        self._sessions_root = Path(
            sessions_root or os.environ.get("PALLI_AGENT_SESSIONS_ROOT")
            or "data/agent_sessions"
        )
        # She decides whether to answer (Batch 8, task 2b): the room first
        # pass and its waits. A pass-through unless companion.she_decides is on.
        self._room_pass = RoomPass(sessions_root=lambda: self._sessions_root)
        # The owner's lane is shared with the phone app (bots/common/owner_lane.py, the mobile
        # door, 2026-10-13). Every hook looks its target up when it runs, so a test that
        # patches bots.telegram.client.dispatch_turn (or assigns client._deliver_result)
        # patches what runs. Telegram does not deliver "the user is typing" to bots, so the
        # lane's coalescer only ever sees messages here: the settle window does the whole job.
        self._lane = OwnerLane(
            LaneHooks(
                door="telegram", log_name="telegram client",
                dispatch=lambda turn: dispatch_turn(turn),
                record=lambda turn, **kw: record_user_fragment(turn, **kw),
                send_text=lambda chat_id, text: send_turn_reply(self.bot, chat_id, text),
                deliver=lambda chat_id, result, **kw: self._deliver_result(chat_id, result, **kw),
                typing=lambda chat_id: typing_while(self.bot, chat_id),
                owner_ok=self._owner_alone,
                owner_burst=lambda turn, **kw: self._room_pass.owner_burst(turn, **kw),
                sessions_root=lambda: self._sessions_root,
            ),
            settle=float(os.environ.get("TELEGRAM_BURST_SETTLE_SECONDS", "3.0") or "3.0"),
            typing_window=float(os.environ.get("TELEGRAM_BURST_TYPING_WINDOW_SECONDS", "10.0") or "10.0"),
            max_hold=float(os.environ.get("TELEGRAM_BURST_MAX_HOLD_SECONDS", "45.0") or "45.0"),
            on_fire=lambda chat_id: self._fire_burst(chat_id),
        )
        # The commands seam (task 3 fills it from bots/telegram/commands.py
        # in start_telegram_client): (name, callback) pairs registered
        # BEFORE the message handlers, because python-telegram-bot
        # runs the first matching handler in a group and a command must be
        # offered the update before the catch-all text handler.
        self.command_specs: list[tuple[str, Callable]] = []
        self._handler_factory: Callable[[], Any] = _HandlerFactory

    # ── the transport ────────────────────────────────────────────────────
    @property
    def bot(self) -> Any:
        return getattr(self._application, "bot", None)

    def register_handlers(self) -> None:
        factory = self._handler_factory()
        for name, callback in list(self.command_specs):
            self._application.add_handler(factory.command(name, callback))
        self._application.add_handler(factory.text(self.on_text))
        self._application.add_handler(factory.voice(self.on_voice))
        self._application.add_handler(factory.file(self.on_file))
        # Last: everything else he can send — a video, a sticker, a pinned
        # location. She cannot read any of them yet, and intake gives each
        # an honest line, but a thing he sent must never simply not happen.
        self._application.add_handler(factory.other(self.on_other))

    async def on_text(self, update: Any, context: Any = None) -> None:
        await self.on_message(update)

    async def on_voice(self, update: Any, context: Any = None) -> None:
        # A voice note takes the same lane as text: intake marks it, and
        # on_message resolves the transcript after materialization and
        # before the record (task 4).
        await self.on_message(update)

    async def on_file(self, update: Any, context: Any = None) -> None:
        await self.on_message(update)

    async def on_other(self, update: Any, context: Any = None) -> None:
        # A kind she cannot read (video, sticker, location, …). It still
        # becomes a turn: intake names what arrived, so she can answer him
        # about it instead of ignoring it.
        await self.on_message(update)

    async def on_error(self, update: Any, context: Any = None) -> None:
        """Whatever a handler raised, FELT (review round 1, minor 6).

        python-telegram-bot catches a raising handler and keeps polling
        (_application.py:1351) — which is right, one bad update must not
        close the door, but it meant a failure nothing and nobody noticed.
        Never raises: an error handler that throws is the one thing the
        library cannot help with.
        """
        exc = getattr(context, "error", None)
        try:
            logger.error("telegram client: handler failed: %r", exc,
                         exc_info=exc if isinstance(exc, BaseException) else None)
            delivery_notice("turn_failed", error_class=type(exc).__name__,
                            stage="telegram_handler")
        except Exception:  # noqa: BLE001
            logger.exception("telegram client: error handler itself failed")

    # ── the lane ─────────────────────────────────────────────────────────
    async def on_message(self, update: Any) -> None:
        message = _intake._message_of(update)
        if message is None:
            return
        author = getattr(message, "from_user", None)
        # An anonymous admin or a channel posting into its group arrives "from"
        # Telegram's placeholder bot with sender_chat set: it goes on to intake,
        # which makes it a guest (step 2, part 8; Fable's final review, 1).
        # Every other bot stays invisible.
        if author is None or (bool(getattr(author, "is_bot", False))
                              and _intake._hidden_sender(message) is None):
            return
        if int(getattr(author, "id", 0) or 0) == int(self._bot_user_id or 0):
            return
        chat = getattr(message, "chat", None)
        chat_id = int(getattr(chat, "id", 0) or 0) if chat is not None else 0
        if not chat_id:
            return

        # The owner gate (item 7a) runs inside intake on this door: one
        # classification decides the whole shape of the turn, and the verdict
        # comes back with it. Week 2 task 3 (gap §5.1.5): an UNRESOLVED owner
        # ref is a guest too — the owner path is proved into, never defaulted
        # into, and boot names the missing id on the punch list.
        owner_telegram_id = await self._owner_telegram_id()
        result = _intake.build_turn(
            update,
            bot_username=self._bot_username,
            bot_user_id=self._bot_user_id,
            owner_ref={"app_user_id": self._owner_id,
                       "telegram_user_id": owner_telegram_id},
        )
        if result is None:
            return                     # not hers to answer — silence is right
        if not result.is_owner:
            await self._handle_guest_message(result, chat_id)
            return

        # His message takes the owner lane (bots/common/owner_lane.py, the
        # mobile door, 2026-10-13): barge-in or steer for a reply in flight,
        # then the files down and the voice heard, the record on arrival, and
        # the burst.
        await self._lane.arrive(
            chat_id, result.turn,
            author_id=int(getattr(author, "id", 0) or 0),
            has_attachment=_carries_attachment(message),
            resolve=self._resolve_owner_turn,
        )

    async def _resolve_owner_turn(self, turn: Any) -> Any:
        """The lane's ``resolve`` hook: his turn made ready for the record."""
        # The files come down HERE, after intake and before the record: the
        # ref that reaches the transcript must already point at a local file,
        # because a Telegram file URL carries her bot token.
        turn = await _intake.materialize_attachments(turn, self.bot)
        # And the voice note is HEARD here, in the same window and for the
        # same reason bots/discord/client.py awaits resolve_voice_turn_content
        # before its record: the fragment written to his transcript on
        # arrival must carry what he SAID, not "[VOICE_MESSAGE]". No
        # transcript is the honest marker, never an empty turn (2026-06-11).
        return await _intake.resolve_voice_turn(turn, self.bot)

    async def _owner_alone(self, chat_id: int, authors: set) -> bool:
        """The lane's ``owner_ok`` hook: the burst is his and his alone.
        Fail-closed — an unresolved owner id or mixed authorship is not."""
        owner_tid = await self._owner_telegram_id()
        return owner_tid is not None and bool(authors) and authors == {owner_tid}

    async def _owner_telegram_id(self) -> int | None:
        """The owner's raw Telegram user id from local-mode refs, or None.

        None means "could not resolve" and every caller treats it as
        fail-closed: the steer lane stays shut and the owner path is not
        taken, so an outage makes his own turn a plain guest reply rather
        than handing a stranger his shell.
        """
        try:
            refs = await get_local_owner_refs()
            raw = refs.get("telegram_user_id")
            return int(raw) if raw else None
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "telegram client: owner ref resolution failed (%s); the owner "
                "path stays closed", exc,
            )
            return None

    async def send_owner_notice(self, text: str) -> bool:
        """A plain notice to the owner's private chat — not a turn, not her
        words (core/owner_notice.py; the spending check, 2026-10-04). A
        private chat's id is the user's id. Nothing is recorded in her
        history: she did not say it. False whenever it could not go out, so
        the caller knows; never raises."""
        clean = str(text or "").strip()
        bot = self.bot
        if not clean or bot is None:
            return False
        owner_telegram_id = await self._owner_telegram_id()
        if owner_telegram_id is None:
            return False
        try:
            await bot.send_message(chat_id=owner_telegram_id, text=clean)
            return True
        except Exception as exc:  # noqa: BLE001 — the caller hears False
            logger.warning("telegram client: the owner notice was not delivered (%s)", exc)
            return False

    async def _handle_guest_message(self, result: Any, chat_id: int) -> None:
        """A non-owner author: one guest turn, answered in the chat. Intake
        already built the guest shape — THEIR id (so the identity store
        compiles their own empty template docs, never his), origin 'guest'
        (no tools, none of his private bands), and their name on the content
        so a shared group transcript stays legible.

        Batch 8, task 2b (ruling T2b-6): no "at capacity" line any more. The
        turn runs in the background and waits there for a slot of the guest
        queue; in a group with companion.she_decides on she reads it first
        (answer, wait or stay quiet)."""
        turn = result.turn
        author_id = int(getattr(turn, "owner_id", 0) or 0)
        # The numeric id as well as the @username (task 6 review, minor):
        # a Telegram account need not have a username, so the line could
        # read "from None", and this is the line SETUP.md section 2b
        # points at for finding the number LOCAL_OWNER_TELEGRAM_USER_ID
        # wants when @userinfobot is not to hand. Logged HERE, on arrival,
        # not inside the task: the arrival is what the line is about.
        logger.info("telegram client: guest message from %s (id %s) in chat %s",
                    (turn.metadata.extra or {}).get("telegram_username"),
                    author_id, chat_id)
        # And the rest of the turn runs in the BACKGROUND (final review, I2).
        # python-telegram-bot processes updates SEQUENTIALLY by default —
        # ApplicationBuilder installs SimpleUpdateProcessor(
        # max_concurrent_updates=1) (_applicationbuilder.py:214) and
        # Application.__update_fetcher awaits each update inline
        # (_application.py:1254-1262) — so awaiting a stranger's 20-60 s turn
        # here held the WHOLE door: his own next message, in any chat, was
        # not even RECORDED until the guest had an answer. His lane is
        # already quick for the same reason (record the fragment, feed the
        # coalescer, return; the coalescer's fire is its own task), so this
        # is the one place the pipeline could stall. The guest queue's wait
        # lives in that task too, never here.
        #
        # Not concurrent_updates(True): that would let a slow download in one
        # update overtake another and reorder HIS burst fragments, and the
        # burst is recorded in arrival order on purpose.
        task = asyncio.create_task(
            self._run_guest_turn(turn, chat_id=chat_id, author_id=author_id))
        self._guest_tasks.add(task)
        task.add_done_callback(self._guest_task_finished)

    def _guest_task_finished(self, task: asyncio.Task) -> None:
        """Let the finished guest turn go — and SAY what killed it if
        anything did. An exception inside a task nobody awaits is silent by
        construction, and silence is the one thing this door does not do."""
        self._guest_tasks.discard(task)
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            logger.error("telegram client: guest task failed: %r", exc,
                         exc_info=exc)

    async def _run_guest_turn(self, turn: Any, *, chat_id: int,
                              author_id: int) -> None:
        """The guest turn itself, in its own task: their voice note heard
        (10-12), then the room pass (a pass-through unless she decides in a
        group), which waits for a slot of the guest queue and gives it back
        whatever happens."""
        try:
            # Heard HERE, in their own task — the same window his own lane
            # hears his in, before anything records or reads the line — so a
            # slow download never holds the door and the room read judges
            # their words, not "[VOICE_MESSAGE]".
            turn = await _intake.hear_guest_voice(turn, self.bot)

            async def _answer(t: Any) -> None:
                await self._answer_guest(t, chat_id=chat_id, author_id=author_id)

            await self._room_pass.guest_message(
                turn, answer=_answer, slot=self._guest_gate.slot)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("telegram client: guest turn failed")

    async def _answer_guest(self, turn: Any, *, chat_id: int, author_id: int) -> None:
        """A guest's full turn, inside its slot: materialize → dispatch → reply,
        through the same delivery as his (10-12), so where voice replies are
        on in this chat her reply to them is spoken too. 5.10: a reply that
        did not reach them is marked in THEIR record (their id, this chat)."""
        turn = await _intake.materialize_attachments(turn, self.bot)
        dispatched = await dispatch_turn(turn)
        await self._deliver_result(
            chat_id, dispatched,
            is_voice=bool(getattr(getattr(turn, "metadata", None), "is_voice", False)),
            record_as=author_id)

    def _requeue_burst(self, chat_id: int, fragments: list, authors: set,
                       times: reply_wait.BurstTimes | None = None) -> None:
        """Batch 8, task 2b (ruling T2b-2): a room read said "wait" — the
        owner's fragments go back into the burst, on the lane (the mobile
        door, 2026-10-13)."""
        self._lane.requeue_burst(chat_id, fragments, authors, times)

    async def _voice_note_for(self, chat_id: int, result, *,
                              is_voice: bool) -> bytes | None:
        """Her reply spoken aloud, or None — the whole decision, in one place.

        Three conditions, all his (task 5): the message she is answering WAS
        a voice note (his, or since 10-12 a guest's), ``/voice on`` is set for
        THIS chat, and there are words to speak. Anything the synthesis cannot do comes back None and the
        caller says nothing about it.

        Called AFTER the words have gone out, so everything slow in here —
        the TTS round trip, the conversion — costs the audio and never the
        reply (review round 1, important 1). A barge-in cancel raises
        through it for the same reason: the words have landed.
        """
        if not is_voice:
            return None
        # Batch 8, task 10: with voice messages on, ``plan_voice_reply`` in
        # _deliver_result has already decided — this lane is today's
        # flag-off behaviour and stands down (no second synthesis).
        if voice_replies.voice_notes_enabled():
            return None
        # The key ``/voice`` writes (bots/telegram/commands.py): one chat's
        # switch, read nowhere else. It resets when she restarts.
        if not steering_fork.get_session_flag(f"telegram:{chat_id}", VOICE_FLAG):
            return None
        # The first chunk of the RAW reply. What he READS may carry a
        # trailing delivery note (_with_notes appends "(not delivered — …)"
        # when a file could not go), and she speaks her words, not the
        # machinery around them — so the two diverge by that note alone.
        spoken = first_chunk(str(getattr(result, "response_text", "") or ""))
        if not spoken:
            return None
        # Seconds of work with nothing on the screen otherwise: the chat
        # says she is still busy while the audio is made.
        async with typing_while(self.bot, chat_id):
            return await synthesize_voice_note(spoken)

    async def _deliver_result(self, chat_id: int, result, *,
                              is_voice: bool = False,
                              waiting_since: reply_wait.BurstTimes | None = None,
                              during_run: int = 0, wait_kind: str = "",
                              record_as: int | None = None) -> None:
        """Send a turn's result to its chat (the delivery block kept separate
        so sidecar results ride the same path as the run's own).

        ``is_voice``: the message this result answers was a voice note, so
        her words may also go out as one. Defaulted False so the sidecar
        path — whose results answer no message of his — stays text, and so
        every other caller is unchanged.

        ``waiting_since``: when the burst this result answers arrived
        (2026-10-12), for the line that logs the real wait. None logs
        nothing. ``wait_kind`` labels a reply given while she works (10-12). ``during_run``: the
        owner's messages that came in while the run worked.

        ``record_as``: whose record a reply that did not arrive marks — the
        owner's (None), or a guest's own id on their reply (10-12).
        """
        whose = self._owner_id if record_as is None else record_as
        attachments = tuple(getattr(result, "attachments", ()) or ())
        voice_notes = tuple(getattr(result, "voice_notes", ()) or ())
        if not (result.responded
                and ((result.response_text or "").strip() or attachments or voice_notes)):
            return

        # Batch 8, task 10 (B8-5): her voice notes, or — while voice replies
        # are on in this chat — her whole reply spoken. None: today's door.
        voice = await plan_voice_reply(
            "telegram", chat_id, result, owner_id=whose,
            typing=lambda: typing_while(self.bot, chat_id))
        if voice is not None:
            await self._deliver_voice_reply(chat_id, result, voice, record_as=whose)
            reply_wait.log_reply_wait("telegram", chat_id, waiting_since,
                                      during_run=during_run, kind=wait_kind)
            return

        # The words FIRST, exactly as they went before this task existed.
        if (
            getattr(result, "cadenced_reply", None) is not None
            and len(result.cadenced_reply.beats) > 1
        ):
            await send_turn_reply_cadenced(self.bot, chat_id, result.cadenced_reply)
        else:
            await send_turn_reply(self.bot, chat_id, result.response_text,
                                  attachments=attachments)
        reply_wait.log_reply_wait("telegram", chat_id, waiting_since,
                                  during_run=during_run, kind=wait_kind)
        if not await self._record_delivery(owner_id=whose, chat_id=chat_id,
                                           text=result.response_text):
            # R9c: the words did not all reach him, and her record now says
            # so. The same words must not then arrive as a voice note, or he
            # would hear what her record says he never got.
            return

        # Then, and only then, the same reply out loud. Nothing below this
        # line can cost him a word of it.
        voice_note = await self._voice_note_for(chat_id, result,
                                                is_voice=is_voice)
        if voice_note:
            await send_turn_reply(self.bot, chat_id, "", voice_note=voice_note)

    async def _deliver_voice_reply(self, chat_id: int, result, voice, *,
                                   record_as: int | None = None) -> None:
        """A reply that goes out as voice (Batch 8, task 10 — B8-5).

        Her text line first (when there is one, with any files), exactly as
        words go today; then each voice message through ``send_voice``. A
        voice message is a delivery part like a message of her words (ruling
        T10-3): one that did not arrive marks her record — not delivered, or
        only partly — through the same ``_record_delivery`` as failed text;
        ``record_as`` is whose record that is (``_deliver_result``).
        """
        attachments = tuple(getattr(result, "attachments", ()) or ())
        landed, total, reason = 0, 0, ""
        cadenced = getattr(result, "cadenced_reply", None)
        if (voice.text.strip() and voice.text == result.response_text
                and cadenced is not None and len(cadenced.beats) > 1):
            await send_turn_reply_cadenced(self.bot, chat_id, cadenced)
            (landed, total), reason = last_send_parts(), last_send_error()
        elif voice.text.strip() or attachments:
            await send_turn_reply(self.bot, chat_id, voice.text, attachments=attachments)
            (landed, total), reason = last_send_parts(), last_send_error()
        for note in voice.notes:
            total += 1
            if await send_turn_reply(self.bot, chat_id, "", voice_note=note):
                landed += 1
            else:
                reason = reason or last_send_error() or "voice send failed"
        await self._record_delivery(
            owner_id=self._owner_id if record_as is None else record_as,
            chat_id=chat_id, text=result.response_text, parts=(landed, total),
            reason=reason)

    async def _record_delivery(self, *, owner_id: int, chat_id: int, text: str,
                               parts: tuple[int, int] | None = None,
                               reason: str | None = None) -> bool:
        """After a reply's send: True when every part of her words reached
        him. Otherwise mark her record (fix plan 5.10) and return False.

        Dispatch wrote the reply to her record before the send, so a send
        that died must mark it. With nothing landed it gets a not-delivered
        row and a full stamp. With the start landed and the rest dead it gets
        an only-partly-delivered row and a partial stamp (R9d). turn_failed
        was already felt inside reply.py. Shielded, with a strong reference:
        a barge-in cancel of this task (his "hello?") must not cost her the
        mark.
        """
        # ``parts``/``reason``: a voice reply counts its own parts (Batch 8,
        # task 10); every other caller reads the last send, as before.
        landed, total = parts if parts is not None else last_send_parts()
        if landed >= total:
            return True
        why = reason if parts is not None else last_send_error()
        task = asyncio.ensure_future(persist_delivery_failure(
            owner_id=owner_id, platform="telegram", channel_id=chat_id,
            reason=why or "send failed", failed_text=text,
            landed=landed, total=total, sessions_root=self._sessions_root,
        ))
        _DELIVERY_MARK_TASKS.add(task)
        task.add_done_callback(_DELIVERY_MARK_TASKS.discard)
        await asyncio.shield(task)
        return False

    async def _fire_burst(self, chat_id: int) -> None:
        """Coalesce a settled burst into ONE turn and dispatch it — the lane's
        (bots/common/owner_lane.py, the mobile door, 2026-10-13). Kept as the
        client's own so the coalescer and a room read's refire go through it."""
        await self._lane.fire_burst(chat_id)

    # ── the lifecycle, by hand ───────────────────────────────────────────
    async def run_polling(self) -> None:
        """Long polling on HER event loop.

        ``Application.run_polling()`` is the library's convenience wrapper —
        it creates a loop, installs signal handlers and blocks. Everything
        she is lives on one loop already, so this is the sequence that
        wrapper performs, performed here instead.
        """
        app = self._application
        await app.initialize()
        try:
            me = await app.bot.get_me()
            self._bot_user_id = int(getattr(me, "id", 0) or 0)
            self._bot_username = str(getattr(me, "username", "") or "")
            logger.info("telegram client ready: @%s id=%s",
                        self._bot_username or "?", self._bot_user_id or "?")
            # Step 2, part 8: what Telegram's privacy setting lets her see in
            # groups, said once here and in every Telegram room's card.
            privacy = privacy_mode_from(me)
            _room_card.set_telegram_privacy(privacy)
            if privacy:
                logger.warning(
                    "telegram: privacy mode is ON for @%s: in groups she sees only messages "
                    "that mention her, reply to her or are commands, unless she is an admin "
                    "there (BotFather: /setprivacy -> Disable, then add her to the group "
                    "again)", self._bot_username or "?")
            _hands_runtime.register_telegram_bot(app.bot)
        except Exception:
            # Without her own id and handle she cannot tell a mention of her
            # from a mention of anyone else, and cannot recognise her own
            # messages. Closing the door beats answering the wrong things.
            logger.exception("telegram client: get_me failed — door stays shut")
            await app.shutdown()
            return

        # The "/" menu (task 3). After initialize because it is an API call,
        # and never blocking: publish_commands swallows its own failure, so a
        # menu Telegram refuses costs the menu and not the door.
        from bots.telegram.commands import publish_commands
        await publish_commands(app.bot)

        # drop_pending_updates=False, owner's call 2026-09-18: a message sent
        # while she was restarting is not noise to be thrown away — it is the
        # thing he said, and losing it silently is worse than answering it a
        # little late. Telegram holds them; she reads them when she comes back.
        await app.updater.start_polling(drop_pending_updates=False)
        await app.start()
        try:
            await self._stop_event.wait()
        finally:
            if _hands_runtime.get_telegram_bot() is app.bot:
                _hands_runtime.register_telegram_bot(None)
            await app.updater.stop()
            await app.stop()
            await app.shutdown()

    async def stop(self) -> None:
        self._stop_event.set()
        await self._drain_guest_tasks()
        # The room reads' pending pauses go with the door (final fix wave,
        # ruling FW-6): none may fire a read against a stopped door.
        await self._room_pass.close()

    async def _drain_guest_tasks(self) -> None:
        """Shutdown fans out to the guest turns still in flight: a short
        grace to finish what they were saying, then the hard cancel. Their
        own ``finally`` gives each slot back either way."""
        pending = [task for task in list(self._guest_tasks) if not task.done()]
        if not pending:
            return
        logger.info("telegram client: %d guest turn(s) in flight at stop; "
                    "%.0fs grace", len(pending), GUEST_STOP_GRACE_SECONDS)
        _done, still_running = await asyncio.wait(
            pending, timeout=GUEST_STOP_GRACE_SECONDS)
        for task in still_running:
            task.cancel()
        if still_running:
            await asyncio.gather(*still_running, return_exceptions=True)


def _carries_attachment(message: Any) -> bool:
    """True when this message brought a file of any kind. Such a message
    never takes the steer lane (spec §5.2): vision and file analysis need a
    full turn, and the fork would decline it at fire time anyway."""
    return any(
        getattr(message, name, None)
        for name in ("document", "photo", "voice", "audio")
    )


async def start_telegram_client() -> None:
    """Entry point — parameterless, to match the contract
    core.plugin_runtime expects of every channel integration.

    Three ways this returns without a door, each said out loud once: no
    token, no library, no resolvable owner. None of them is a crash — she
    still has every other door.
    """
    from core.config import config

    token = (getattr(config, "TELEGRAM_BOT_TOKEN", "") or "").strip()
    if not token:
        logger.error("PalliTelegramClient cannot start — TELEGRAM_BOT_TOKEN missing")
        return

    try:
        from telegram.ext import ApplicationBuilder
    except ImportError:
        logger.error(
            "python-telegram-bot is not installed — the Telegram door stays "
            "closed (pip install -r requirements.txt)")
        return

    refs = await get_local_owner_refs()
    owner_id = refs.get("app_user_id")
    if owner_id is None:
        logger.error("PalliTelegramClient cannot start — local owner unresolved")
        return

    install_polling_blip_filter()
    application = ApplicationBuilder().token(token).build()
    client = PalliTelegramClient(
        application=application,
        owner_id=int(owner_id),
        # The process-wide guest budget: one cap across every door she has,
        # so a stranger cannot spend twice by walking round to the other one.
        guest_gate=shared_guest_gate(),
    )
    # The commands seam, filled (task 3) — BEFORE register_handlers, which
    # puts every CommandHandler ahead of the message handlers.
    from bots.telegram.commands import build_handlers
    client.command_specs = build_handlers(client)
    client.register_handlers()
    try:
        application.add_error_handler(client.on_error)
    except Exception:
        logger.exception("Failed to register the Telegram error handler")

    global _active_client
    _active_client = client

    try:
        await channel_runtime.register_adapter(
            "telegram",
            TelegramChannelAdapter(application.bot),
            metadata={"label": "Telegram"},
        )
    except Exception:
        logger.exception("Failed to register Telegram channel adapter")

    # The owner's private chat as the second road for plain notices that are
    # not hers (the spending check, 2026-10-04): asked when Discord cannot
    # take one (core/owner_notice.py).
    register_owner_notice_sender("telegram", client.send_owner_notice)

    try:
        await client.run_polling()
    except (KeyboardInterrupt, asyncio.CancelledError):
        raise
    except Exception:
        logger.exception("PalliTelegramClient run failed")
        raise
    finally:
        unregister_owner_notice_sender("telegram", client.send_owner_notice)
        try:
            await channel_runtime.unregister_adapter("telegram", reason="stopped")
        except Exception:
            logger.exception("Failed to unregister Telegram channel adapter")
        if _active_client is client:
            _active_client = None
