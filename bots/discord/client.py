"""Wave 3: slim Discord client wired to the new core/turn spine.

Compared to the legacy bots/discord/bot.py this client does NOT do:
  * SaaS access gating (legacy bot.py:1100-1165) — local mode is single-owner
  * Follow-up timer scheduling (proactive owns outreach in Wave 4)

Voice messages (2026-06-11): a message carrying an audio attachment gets its
content resolved through voice_attachment.resolve_voice_turn_content before
dispatch — transcript on success, honest unavailable-marker on failure, never
empty content (an empty voice turn poisoned the agent session and 400'd the
whole channel).

The legacy bot stays on disk through Wave 4; Wave 5 deletes it. The
launcher (Task E1) selects this client when
`turn_loop.surfaces.discord_text=true` in data/palli.yaml.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import replace as _dc_replace
from pathlib import Path
from typing import Any, Dict

import discord

from bots.common.guest_gate import GuestGate, shared_guest_gate
from bots.common import reply_wait
from bots.common.room_pass import RoomPass
from bots.discord.burst import BurstCoalescer
from bots.discord.intake import build_turn_from_message, is_system_message
from bots.discord.recording import record_user_fragment
from bots.discord.reply import (
    last_send_error,
    last_send_parts,
    last_send_typing,
    send_turn_reply,
    send_turn_reply_cadenced,
)
from bots.discord import steering_fork
from bots.discord import voice_reply
from bots.discord.voice_attachment import hear_guest_voice, resolve_voice_turn_content
from core.channel_runtime import (
    ChannelDeliveryEnvelope,
    DoorNotReady,
    channel_runtime,
)
from core.agent import steering as _steering
from core.agent.dispatch import dispatch_turn
from core.agent.runner import _derive_session_key, _open_session_with_user_message
from core.agent.steering_reply import judge_mid_task, judge_supersede
from core.config import config
from core.local_mode import get_local_owner_refs
from core.owner_notice import register_owner_notice_sender, unregister_owner_notice_sender
from core.turn.persistence import persist_delivery_failure

logger = logging.getLogger(__name__)

# Strong refs to the shielded not-delivered marks (the Bug-1 retention
# pattern, like dispatch._BACKGROUND_PERSIST_TASKS): a barge-in cancels the
# task that awaits the shield, and a task nothing references can be collected
# mid-write.
_DELIVERY_MARK_TASKS: set[asyncio.Task] = set()

# Module-level handle to the currently-running client. Set by
# start_discord_text_client at construction time; consumers like
# core.ambient_service read it to send DMs without owning a Discord
# connection of their own.
_active_client: "PalliDiscordClient | None" = None


def get_active_client() -> "PalliDiscordClient | None":
    """Return the running PalliDiscordClient, or None if not started."""
    return _active_client


async def _shutdown_voice_sessions() -> None:
    """Best-effort: stop live voice sessions so Deepgram sockets and the
    voice gateway close cleanly on client shutdown. Nothing did this before
    the v1 rewire — sessions leaked across restarts. Lazy import keeps the
    voice stack off the module import path."""
    try:
        from voice.always_listening import voice_processor
        if voice_processor.get_session_count() > 0:
            await voice_processor.stop_all_sessions()
            logger.info("voice sessions stopped for shutdown")
    except Exception:
        logger.exception("voice session shutdown failed (continuing)")


class DiscordChannelAdapter:
    """Bridge between core.channel_runtime and PalliDiscordClient.

    The delegation bridge (ai/delegation/bridge.py) and other legacy
    delivery paths reach Discord via channel_runtime.send_envelope. The
    legacy bot.py used to register a similar adapter; with bot.py
    deleted in Wave 5, the new client now provides this adapter.
    """

    def __init__(self, client: "PalliDiscordClient") -> None:
        self._client = client

    def is_ready(self) -> bool:
        """Whether the gateway is up. The adapter is registered before
        ``client.start()``, so for the first seconds after a restart the door
        exists and cannot send yet; the reminder loop asks this before it
        spends a fire turn (final review, Important 3)."""
        return bool(self._client.is_ready())

    async def is_owner_private_chat(self, surface_id: str) -> bool:
        """True when ``surface_id`` is the owner's DM with her (the owner,
        2026-10-13: her own messages to him go to a private chat, never a
        server channel). core.channel_runtime asks this about a chat whose
        rows were written before they said whether it is private; nothing in
        such a row tells his DM from a server channel.

        Discord keeps one DM between him and her. It is opened the way the
        owner notice reaches him (``send_owner_notice``) and its id compared;
        Discord answers an open with the DM that already exists. False
        whenever it cannot be shown: the gateway not up, the owner unknown,
        Discord refusing. Never raises."""
        client = self._client
        try:
            channel_id = int(surface_id)
        except (TypeError, ValueError):
            return False
        if not channel_id or not client.is_ready():
            return False
        owner_discord_id = await client._local_owner_discord_id()
        if owner_discord_id is None:
            return False
        try:
            owner = client.get_user(owner_discord_id) or await client.fetch_user(owner_discord_id)
            if owner is None:
                return False
            dm = getattr(owner, "dm_channel", None) or await owner.create_dm()
        except Exception as exc:  # noqa: BLE001 — not shown is not private
            logger.warning("discord client: the owner's DM could not be opened (%s)", exc)
            return False
        return int(getattr(dm, "id", 0) or 0) == channel_id

    async def send_envelope(
        self, *, surface_id: str, envelope: ChannelDeliveryEnvelope,
    ) -> Dict[str, Any]:
        if not self._client.is_ready():
            # Not a refused send: nothing was tried. Its own type, so the
            # reminder loop does not count it toward the dead-letter cap.
            raise DoorNotReady("DiscordChannelAdapter: client not ready yet")
        try:
            channel_id = int(surface_id)
        except (TypeError, ValueError) as exc:
            raise RuntimeError(
                f"DiscordChannelAdapter: invalid surface_id {surface_id!r}"
            ) from exc
        channel = self._client.get_channel(channel_id)
        if channel is None:
            channel = await self._client.fetch_channel(channel_id)
        if channel is None:
            raise RuntimeError(
                f"DiscordChannelAdapter: channel {channel_id} not found"
            )
        text = envelope.text or ""
        if not text.strip():
            # Nothing to send is not a failed send (the Telegram adapter's rule).
            return {"sent": False, "channel_id": channel_id}
        delivered = await send_turn_reply(channel, text)
        if not delivered:
            # Fix plan 5.8: send_turn_reply swallows a failed send and says so
            # only by returning False. Answering sent=True here filed every
            # Discord reminder that never reached him as said — logged,
            # transcribed into his chat and retired as delivered. Raising is
            # how the reminder binding (and every other caller) hears a
            # refusal: sent stays False and the reminder is still owed.
            raise RuntimeError(
                f"DiscordChannelAdapter: nothing was delivered to channel "
                f"{channel_id}: {last_send_error() or 'no send succeeded'}"
            )
        # Wave 6 polish target: attachments + reply_to threading.
        return {"sent": True, "channel_id": channel_id}


class PalliDiscordClient(discord.Client):
    """A discord.Client that coalesces a rapid burst of messages into one turn.

    Burst coalescing (2026-07-07): instead of dispatching a turn per message
    (and cancelling on every follow-up), each inbound fragment is recorded on
    arrival, buffered per channel, and fed to a BurstCoalescer that owns the
    settle *timing*. When a burst settles the coalescer calls ``_fire_burst``
    once, which joins the buffered fragments into a single turn (flagged
    ``user_side_prerecorded=True``) and dispatches it.

    Two "barge-in" style behaviors, split apart:
      * typing HOLDS the settle open (``on_typing`` -> ``note_typing``) so
        PaLLi waits for the follow-up instead of replying to a half-typed
        thought. Typing no longer cancels an in-flight reply.
      * a new message arriving while PaLLi is mid-reply CANCELS that reply
        (``on_message`` cancels the in-flight ``_fire_burst`` task) — the
        message-driven barge-in is kept.
    """

    def __init__(
        self,
        *,
        owner_id: int,
        intents: discord.Intents,
        bot_user_id: int = 0,
        guest_gate: GuestGate | None = None,
    ):
        super().__init__(intents=intents)
        # bot_user_id may be 0 at construction (the production wrapper
        # below doesn't know it until after gateway login); on_ready
        # refreshes it from self.user.id once connected.
        self._bot_user_id = bot_user_id
        self._owner_id = owner_id
        # channel_id -> the asyncio task currently dispatching a coalesced
        # reply for that channel (set inside _fire_burst). on_message reads
        # this map to barge-in cancel when a new message arrives mid-reply;
        # _fire_burst registers itself on entry and clears it on exit.
        self._inflight_tasks: dict[int, asyncio.Task] = {}
        # Step 27 Phase B (spec §3): a paused run's expiry clock, per channel.
        self._pause_timers: dict[int, asyncio.Task] = {}
        # Step 27 Phase B (spec §1/§4): live sidecars per channel —
        # child_key → (kind, task). Stop fans out to the task ones.
        self._sidecars: dict[int, dict[str, tuple[str, asyncio.Task]]] = {}
        # channel_id -> [Turn, ...] fragments recorded-on-arrival since the
        # last settle. _fire_burst pops this and coalesces it into one turn.
        self._burst_fragments: dict[int, list] = {}
        # channel_id -> {author discord ids} for the fragments buffered since
        # the last settle. The steering fork's owner-only gate (spec §5.1)
        # reads this at fire time: Turn.owner_id is the CONFIGURED app owner
        # stamped on every turn (intake.py), so it cannot distinguish authors
        # — the raw Discord author id can. Fail-closed: empty or mixed
        # authorship never enters the steer lane.
        self._burst_authors: dict[int, set[int]] = {}
        # channel_id -> [Turn, ...] fragments whose arrival-time transcript
        # append was SKIPPED because they took the steer lane (spec §4e: the
        # runner is the sole transcript writer during a run). If the fork
        # takes the burst, the boundary drain writes the coalesced text; if
        # it declines (or the run ended before fire), _fire_burst repairs by
        # appending exactly these before the normal dispatch, so no word of
        # his can vanish from the record.
        self._burst_unappended: dict[int, list] = {}
        # channel_id -> (first, last) arrival of the buffered fragments
        # (time.monotonic(), 2026-10-12): the wait behind the reply. Its typing
        # pause is credited with it, and the real wait is logged.
        self._burst_times: dict[int, reply_wait.BurstTimes] = {}
        # channel_id -> how many of the owner's messages have come in (Fable's
        # review, 10-12): a run that more came into while it worked says so
        # in its wait line.
        self._owner_messages_in: dict[int, int] = {}
        # channel_id -> (fragments, authors, times, session key) of the burst
        # whose reply is still being prepared (2026-10-12): set when it fires,
        # gone once the run returns. The owner's typing can cancel that
        # preparation and put the burst back (_typing_while_preparing), never
        # a reply that is being written or sent.
        self._preparing: dict[int, tuple] = {}
        # Guest budgets (stranger-proofing week 1, task 2; moved to
        # bots/common/guest_gate.py for the telegram door, 2026-09-14). The
        # owner gate settled what a guest gets; this settles how many, so no
        # stranger can spend his balance for him. The live client is handed
        # the process-wide gate (start_discord_text_client below), so every
        # door spends from ONE budget — the money is one account. Absent, a
        # fresh gate: a client built directly is isolated.
        self._guest_gate = guest_gate if guest_gate is not None else GuestGate()
        # Honors PALLI_AGENT_SESSIONS_ROOT like dispatch/outbound/proactive do
        # (2026-08-18) — the hardcoded path let the client's tests append to a
        # LIVE data/agent_sessions/discord_67890.jsonl. Live runs leave it unset.
        self._sessions_root = Path(
            os.environ.get("PALLI_AGENT_SESSIONS_ROOT") or "data/agent_sessions"
        )
        # She decides whether to answer (Batch 8, task 2b): the room first
        # pass and its waits. A pass-through unless companion.she_decides is
        # on; reads the sessions root at call time (tests repoint it).
        self._room_pass = RoomPass(sessions_root=lambda: self._sessions_root)
        # The coalescer owns settle *timing* only; this client records the
        # fragments and dispatches the coalesced turn in _fire_burst. Windows
        # are env-tunable. See
        # docs/superpowers/specs/2026-07-07-burst-coalescing-design.md.
        self._coalescer = BurstCoalescer(
            on_fire=self._fire_burst,
            settle=float(
                os.environ.get("DISCORD_BURST_SETTLE_SECONDS", "3.0") or "3.0"
            ),
            typing_window=float(
                os.environ.get("DISCORD_BURST_TYPING_WINDOW_SECONDS", "10.0")
                or "10.0"
            ),
            max_hold=float(
                os.environ.get("DISCORD_BURST_MAX_HOLD_SECONDS", "45.0") or "45.0"
            ),
        )
        # Voice v1 rewire (2026-07-11): the slash-command tree. Always
        # constructed; voice commands register only behind the flag, so
        # flag-off leaves the three shared commands and nothing else, and the
        # guild sync clears any stale voice commands.
        self.tree = discord.app_commands.CommandTree(self)
        self._voice_commands_synced = False
        # Telegram door (2026-09-14, task 3): insights / link_account /
        # proactive_consent, ALWAYS. They are not voice commands — a host
        # with no voice runtime still gets to link an account, read what she
        # has learned, and decide whether she may speak first. The same three
        # exist on Telegram and share one implementation
        # (bots/common/commands.py).
        from bots.discord.discord_commands import register_common_commands
        register_common_commands(self.tree)
        from core.config import config as _config
        if bool(getattr(_config, "ENABLE_VOICE_FEATURES", False)):
            from bots.discord.voice_commands import register_voice_commands
            register_voice_commands(self.tree)

    async def on_ready(self) -> None:
        if getattr(self, "user", None) is not None:
            self._bot_user_id = getattr(self.user, "id", self._bot_user_id) or self._bot_user_id
        logger.info(
            "discord client ready: user=%s id=%s",
            getattr(self.user, "name", "?"),
            getattr(self.user, "id", "?"),
        )
        # Her Hands (step 33): publish the live client for her tools.
        try:
            from core.agent.hands_runtime import register_client
            register_client(self)
        except Exception:
            logger.exception("hands_runtime registration failed (tools stay calm)")
        # The owner-presence sense (2026-08-18): his Discord status is the
        # first REAL presence signal on this host (the machine/desktop senses
        # read the server, not him). Install the moment's notice as the edge
        # hook, then reconcile once from the member cache — on_presence_update
        # keeps it current from here.
        await self._reconcile_owner_presence()
        await self._sync_voice_commands(list(self.guilds))

    # ── owner presence (2026-08-18) ────────────────────────────────────
    async def _reconcile_owner_presence(self) -> None:
        """Install the present-moment hook and read his current status once."""
        try:
            from core import owner_presence as _op
            try:
                from ai.present_moment import notice as _pm_notice
                _op.set_transition_hook(_pm_notice)
            except Exception:
                logger.debug("owner_presence: present-moment hook unavailable")
            owner_did = await self._local_owner_discord_id()
            if owner_did is None:
                return
            for guild in list(getattr(self, "guilds", []) or []):
                member = guild.get_member(owner_did) if hasattr(guild, "get_member") else None
                if member is not None:
                    self._record_owner_presence(member)
                    return
        except Exception:
            logger.exception("owner_presence: reconcile failed (sense stays quiet)")

    @staticmethod
    def _record_owner_presence(member: Any) -> None:
        from core import owner_presence as _op

        status = getattr(getattr(member, "status", None), "value", None) or str(getattr(member, "status", "") or "")
        # The first activity that is a real thing he is doing (a game, an
        # app, a stream) — a custom status is a typed mood line, not an
        # activity, and is never rendered as "playing …".
        name = ""
        for act in list(getattr(member, "activities", None) or []):
            if act is None or getattr(act, "type", None) == discord.ActivityType.custom:
                continue
            name = str(getattr(act, "name", "") or "")
            if name:
                break
        _op.record_discord_presence(status=str(status), activity=name)

    async def on_presence_update(self, before, after) -> None:
        """Only HIS presence is recorded; everyone else's changes nothing."""
        try:
            owner_did = await self._local_owner_discord_id()
            if owner_did is None or after is None:
                return
            if getattr(after, "id", None) != owner_did:
                return
            self._record_owner_presence(after)
        except Exception:
            logger.debug("owner_presence: presence update ignored", exc_info=True)

    async def on_voice_state_update(self, member, before, after) -> None:
        """Step 31 (spec §6): the arrival sense — her moment feels people
        entering/leaving the channel SHE is in; him joining = priority wake.
        Structural only (is_owner — names are hers to look up with her own
        hands); NO speech here — whether she greets is her judgment through
        speak_line. Never raises into discord.py's event loop."""
        try:
            if getattr(member, "bot", False):
                return
            if getattr(member, "id", None) == self._bot_user_id:
                return  # a stale cache must never let her feel her own steps
            voice_client = getattr(getattr(member, "guild", None), "voice_client", None)
            my_channel = getattr(voice_client, "channel", None)
            if my_channel is None:
                return  # she is not sitting in any channel of this guild
            before_chan = getattr(before, "channel", None)
            after_chan = getattr(after, "channel", None)
            before_id = getattr(before_chan, "id", None)
            after_id = getattr(after_chan, "id", None)
            if before_id == after_id:
                return  # mute/deafen flaps are not arrivals
            owner_did = await self._local_owner_discord_id()
            is_owner = owner_did is not None and getattr(member, "id", None) == owner_did
            from ai.present_moment import notice

            my_id = getattr(my_channel, "id", None)
            if after_id == my_id:
                notice(
                    "owner_joined_voice" if is_owner else "voice_channel_joined",
                    is_owner=is_owner,
                )
            elif before_id == my_id:
                notice(
                    "owner_left_voice" if is_owner else "voice_channel_left",
                    is_owner=is_owner,
                )
        except Exception:
            logger.debug("on_voice_state_update: sense dropped", exc_info=True)

    async def _sync_voice_commands(self, guilds) -> None:
        """Guild-scoped slash-command sync, once per process.

        Guild sync propagates instantly (global sync can take up to an
        hour); copy_global_to publishes the tree's commands per guild.
        Idempotent because on_ready can re-fire on reconnect. Failures log
        and never block text chat.
        """
        if self._voice_commands_synced:
            return
        try:
            for guild in guilds:
                self.tree.copy_global_to(guild=guild)
                await self.tree.sync(guild=guild)
            self._voice_commands_synced = True
            logger.info(
                "slash commands synced to %d guild(s) (%d commands)",
                len(guilds), len(self.tree.get_commands()),
            )
        except Exception:
            logger.exception("slash-command sync failed (text chat unaffected)")

    async def on_typing(self, channel, user, when) -> None:
        """Feed a typing event to the burst coalescer.

        Typing HOLDS the settle window open (note_typing extends
        ``typing_active_until``) so PaLLi waits for the follow-up instead
        of replying to a half-finished thought. It no longer cancels an
        in-flight reply — the pre-2026-07-07 7s typing-cancel is gone;
        barge-in is message-driven (see on_message), with one exception
        since 2026-10-12: the owner typing again while their own reply is
        still being prepared (_typing_while_preparing).
        """
        if user is None or getattr(user, "bot", False):
            return
        if getattr(user, "id", None) == self._bot_user_id:
            return
        channel_id = getattr(channel, "id", None)
        if channel_id is None:
            return
        # Per person (2026-10-12): their own message ends it.
        self._coalescer.note_typing(channel_id, getattr(user, "id", None))
        await self._typing_while_preparing(channel_id, user)

    async def _typing_while_preparing(self, channel_id: int, user) -> None:
        """The owner typing again while their reply is still being prepared
        (2026-10-12, the owner: "yes, add the typing rule to fix 2").

        Nothing has been written or shown yet, so the preparation is
        cancelled and the burst goes back to wait for what they are typing:
        one reply to both messages, as when the message itself arrives
        during preparation (on_message, the 08-04 phase rule). If no message
        comes, the burst fires again once their typing lapses.

        Only the owner's own burst, and only while the runner says "prep"
        (Fable's review, 10-12). An empty phase is never enough: it is also
        the second after a run ends, with the reply written and saved but
        not yet sent, and a cancel there lost the reply and answered twice.
        A reply being written, a tool at work or a reply on its way out is
        never cancelled by typing; nor is a burst that has already waited
        the max hold (45 s by default), which still promises an answer.
        """
        preparing = self._preparing.get(channel_id)
        prior = self._inflight_tasks.get(channel_id)
        if preparing is None or prior is None or prior.done():
            return
        fragments, authors, times, session_key = preparing
        owner_did = await self._local_owner_discord_id()
        if (owner_did is None or getattr(user, "id", None) != owner_did
                or authors != {owner_did}):
            return
        # The await above let the run move on: look again.
        if self._preparing.get(channel_id) is not preparing or prior.done():
            return
        if (_steering.is_working(session_key)
                or _steering.get_run_phase(session_key) != "prep"
                or not self._coalescer.may_hold_again(channel_id)):
            return
        self._preparing.pop(channel_id, None)
        self._inflight_tasks.pop(channel_id, None)
        logger.info(
            "discord client: owner typing again while the reply was being "
            "prepared — cancelling it and waiting for the next message, "
            "channel %s", channel_id,
        )
        prior.cancel()
        _steering.clear(session_key)
        # The runner clears its phase only in the tool loop's finally, and
        # this cancel lands before the loop: leave no stale "prep" behind.
        _steering.clear_run_state(session_key)
        steering_fork.reset_reply_budget(session_key)
        self._requeue_burst(channel_id, fragments, authors, times)
        self._coalescer.rearm(channel_id)

    async def on_message(self, message) -> None:
        author = getattr(message, "author", None)
        if author is None or getattr(author, "bot", False):
            return
        if getattr(author, "id", None) == self._bot_user_id:
            return
        channel = getattr(message, "channel", None)
        channel_id = getattr(channel, "id", None) if channel is not None else None
        if channel_id is None:
            return
        # Step 2, part 7: Discord's own notices (a join, a pin, a thread
        # started) are nobody's words, so they never become a turn.
        if is_system_message(message):
            return

        # The owner gate (item 7a, 2026-08-19): only HIS Discord identity is
        # the owner. Anyone else is a guest — a plain reply, no tools, none
        # of his memory — and never touches his lane (no burst buffer, no
        # barge-in on his in-flight reply, no record into his history).
        # Week 2 task 3 (gap §5.1.5): an UNRESOLVED owner ref is a guest too.
        # It used to fall through to the owner turn, so a fresh clone with
        # LOCAL_OWNER_DISCORD_USER_ID unset gave the first stranger to type
        # here his shell. The owner path is proved into, never defaulted
        # into; boot names the missing id on the punch list.
        from core.owner_gate import classify_author, is_owner_verdict
        owner_did = await self._local_owner_discord_id()
        if not is_owner_verdict(classify_author(getattr(author, "id", None), owner_did)):
            # Their line is in: their typing no longer holds a burst open.
            if getattr(author, "id", None) is not None:
                self._coalescer.note_sent(channel_id, author.id)
            await self._handle_guest_message(message, channel_id)
            return

        # Barge-in vs steer (spec §4b): a message arriving while PaLLi is
        # mid-reply used to always cancel the in-flight coalesced reply. With
        # companion.converse_while_working ON, an OWNER-authored non-stop
        # message lets the run keep going — the coalescer routes the burst
        # through the judged fork at fire time. Only he reaches this far (the
        # gate above sends everyone else, unresolved ids included, down the
        # guest path); an explicit stop still cancels, and the second owner
        # check below stays as the fail-closed backstop. Flag OFF is the
        # legacy branch below, unchanged.
        prior = self._inflight_tasks.get(channel_id)
        run_in_flight = prior is not None and not prior.done()
        steer_lane = False
        if run_in_flight and steering_fork._flag_on():
            content = str(getattr(message, "content", "") or "")
            is_stop = steering_fork.is_stop_command(content)
            # Attachment-carrying messages (images, files, voice) never take
            # the steer lane: the fork declines them at fire time (spec §5.2),
            # so skipping their arrival append here would only orphan them.
            if not is_stop and not getattr(message, "attachments", None):
                owner_did = await self._local_owner_discord_id()
                if owner_did is not None and getattr(author, "id", None) == owner_did:
                    # Phase-owned routing (2026-08-04): only a run that has
                    # actually spoken (generating) or actually worked (a real
                    # tool call) earns the deferred lane. A run still in PREP
                    # has shown him nothing — cancelling and recoalescing is
                    # strictly better than steering it (the 15:48 flash-lane
                    # incident: his correction arrived 6s before the reply
                    # model was even invoked and got a 700-token one-liner).
                    _sk = f"discord:{channel_id}"
                    steer_lane = (
                        _steering.is_working(_sk)
                        or _steering.get_run_phase(_sk) == "generating"
                    )
            if not steer_lane:
                self._inflight_tasks.pop(channel_id, None)
                logger.info(
                    "discord client: cancelling in-flight reply for "
                    "channel %s (%s)",
                    channel_id,
                    "stop command" if is_stop else "barge-in",
                )
                from core.agent import duplex as _dx
                _sk = f"discord:{channel_id}"
                if is_stop and steering_fork.stop_mode(
                    duplex_on=_dx.duplex_enabled(),
                    working=_steering.is_working(_sk),
                ) == "cooperative":
                    # Step 27 (spec §3): stop at the next tool boundary; the
                    # hard cancel stays as the backstop after the grace.
                    _steering.request_stop(_sk)
                    # Phase B (spec §3, correction 8): the stop reaches the
                    # TASK sidecars too — a conversation in flight finishes.
                    for _ck in steering_fork.sidecar_keys_for(_sk, self._sidecars.get(channel_id, {})):
                        _steering.request_stop(_ck)
                    _grace = float(_dx._knob("duplex_stop_grace_seconds", 30))

                    async def _hard_cancel_after(task=prior, grace=_grace):
                        await asyncio.sleep(grace)
                        if not task.done():
                            logger.warning(
                                "discord client: cooperative stop overran %.0fs; hard cancel", grace)
                            task.cancel()
                    # Hold the reference: an unreferenced task can be
                    # garbage-collected mid-sleep and never fire.
                    _bg = getattr(self, "_duplex_stop_backstops", None)
                    if _bg is None:
                        self._duplex_stop_backstops = _bg = set()
                    _t = asyncio.create_task(_hard_cancel_after())
                    _bg.add(_t)
                    _t.add_done_callback(_bg.discard)
                else:
                    prior.cancel()
                # Queued steers were about work that is now dead (spec §5.3).
                # The runner's own cancel path drops drained-but-unanswered
                # bundles for the same reason.
                _steering.clear(f"discord:{channel_id}")
                steering_fork.reset_reply_budget(f"discord:{channel_id}")
        elif run_in_flight:
            self._inflight_tasks.pop(channel_id, None)
            logger.info(
                "discord client: barge-in — cancelling in-flight reply for "
                "channel %s",
                channel_id,
            )
            prior.cancel()

        turn = build_turn_from_message(
            message, bot_user_id=self._bot_user_id, owner_id=self._owner_id,
            speaker_kind="owner",
        )
        if turn.metadata.is_voice:
            resolved = await resolve_voice_turn_content(
                message, current_content=turn.content,
            )
            if resolved != turn.content:
                turn = _dc_replace(turn, content=resolved)

        # Record each fragment on arrival (transcript shielded, DB detached),
        # then buffer it and feed the coalescer. The single coalesced turn
        # dispatched on settle carries user_side_prerecorded=True so the
        # engine doesn't re-append/re-persist the user side.
        # Steered fragments skip the transcript half only (spec §4e): during a
        # run the runner is the sole transcript writer, and it appends the
        # coalesced bundle at its next model boundary. The DB half still
        # fires. Everything else records exactly as before.
        await record_user_fragment(
            turn, sessions_root=self._sessions_root, transcript=not steer_lane,
        )
        self._burst_fragments.setdefault(channel_id, []).append(turn)
        if steer_lane:
            self._burst_unappended.setdefault(channel_id, []).append(turn)
        self._burst_authors.setdefault(channel_id, set()).add(
            int(getattr(author, "id", 0) or 0))
        self._burst_times[channel_id] = reply_wait.stamp(self._burst_times.get(channel_id))
        self._owner_messages_in[channel_id] = self._owner_messages_in.get(channel_id, 0) + 1
        self._coalescer.note_message(channel_id, getattr(author, "id", None))

    async def _local_owner_discord_id(self) -> int | None:
        """The owner's raw Discord user id from local-mode refs, or None.

        None means "could not resolve" and every caller treats it as
        fail-closed (the steer lane stays shut; legacy behavior applies).
        """
        try:
            refs = await get_local_owner_refs()
            raw = refs.get("discord_user_id")
            return int(raw) if raw else None
        except Exception as exc:
            logger.warning(
                "discord client: owner ref resolution failed (%s); steering "
                "lane stays closed", exc,
            )
            return None

    async def send_owner_notice(self, text: str) -> bool:
        """A plain notice to the owner's DMs — not a turn, not her words
        (core/owner_notice.py; the spending check, 2026-10-04). Nothing is
        recorded in her history: she did not say it. False whenever it could
        not go out (gateway not up, owner unknown, Discord refused), so the
        next door is asked; never raises."""
        clean = str(text or "").strip()
        if not clean or not self.is_ready():
            return False
        owner_discord_id = await self._local_owner_discord_id()
        if owner_discord_id is None:
            return False
        try:
            user = self.get_user(owner_discord_id) or await self.fetch_user(owner_discord_id)
            if user is None:
                return False
            await user.send(clean)
            return True
        except Exception as exc:  # noqa: BLE001 — the next door is asked
            logger.warning("discord client: the owner notice was not delivered (%s)", exc)
            return False

    # The guest budget (stranger-proofing week 1, task 2; a queue since
    # Batch 8, task 2b — ruling T2b-6). Deterministic on purpose: a resource
    # floor, never a messaging rule. The per-guest rate limit is gone (the
    # owner, 09-30: "Drop the limit."); GUEST_MAX_IN_FLIGHT is a queue — a
    # guest turn over it waits for a free slot, with no canned line. The
    # rules live in bots/common/guest_gate.py.
    @property
    def _guests_in_flight(self) -> int:
        return self._guest_gate.in_flight

    def attach_guest_gate(self, gate: GuestGate) -> None:
        """Spend from THIS budget from now on.

        The live start path hands the process-wide gate over here rather than
        through the constructor, whose argument list is pinned by
        tests/bots/test_discord_client.py:460. A door built for a test keeps
        the fresh gate it was constructed with."""
        self._guest_gate = gate

    async def _handle_guest_message(self, message, channel_id: int) -> None:
        """A non-owner author: one guest turn. owner_id = THEIR Discord id
        (so the identity store compiles their own empty template docs, never
        his), origin 'guest' (dispatch: no tools, no reflection; the context
        adapter renders none of his private bands), content tagged with their
        name so the shared channel transcript stays legible about who spoke.

        Batch 8, task 2b: the turn goes through the room pass — in a shared
        room with companion.she_decides on she reads it first (answer, wait
        or stay quiet); anywhere else it is today's path. Either way it waits
        for a slot of the guest queue, in this event's own task (discord.py
        runs each on_message as its own task, so nothing else waits on it),
        and nothing is ever said for her.

        Their voice note is heard first (10-12), so the room read and her
        reply both have their words."""
        from core.owner_gate import GUEST_ORIGIN, guest_content

        author = getattr(message, "author", None)
        author_id = int(getattr(author, "id", 0) or 0)
        name = (getattr(author, "display_name", None)
                or getattr(author, "name", None) or "guest")
        try:
            turn = build_turn_from_message(
                message, bot_user_id=self._bot_user_id, owner_id=author_id,
                speaker_kind="guest",
            )
            if turn.metadata.is_voice:
                # 10-12 (the owner: guests' messages are "normal messages,
                # minus permissions"): a guest's voice note is HEARD, as his
                # is, before the room read — a friend's first message reached
                # it as "[VOICE_MESSAGE]", it had no words to judge, and it
                # stayed quiet. Their audio meets the guest wall, a few are
                # heard at once, and a note she cannot hear is the honest
                # marker, never the internal token (never raises).
                resolved = await hear_guest_voice(message, current_content=turn.content)
                if resolved != turn.content:
                    turn = _dc_replace(turn, content=resolved)
            turn = _dc_replace(
                turn,
                content=guest_content(name, turn.content),
                metadata=_dc_replace(turn.metadata, origin=GUEST_ORIGIN),
            )
            logger.info(
                "discord client: guest message from %s in channel %s", name, channel_id,
            )

            async def _answer(t):
                await self._answer_guest(t, channel_id=channel_id, author_id=author_id)

            await self._room_pass.guest_message(
                turn, answer=_answer, slot=self._guest_gate.slot)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("discord client: guest turn failed")

    async def _answer_guest(self, turn, *, channel_id: int, author_id: int) -> None:
        """A guest's full turn, inside its slot: dispatch, then the reply —
        through the same delivery as his (10-12), so where voice replies are
        on in this room her reply to them is spoken too. 5.10: a reply that
        died on the way is marked in THEIR record (their id, this channel),
        the same way the owner's is."""
        result = await dispatch_turn(turn)
        await self._deliver_result(channel_id, result, record_as=author_id)

    async def close(self) -> None:
        """Close the door. The room reads' pending pauses go first (final fix
        wave, ruling FW-6), so none fires a read against a closed client."""
        try:
            room_pass = getattr(self, "_room_pass", None)
            if room_pass is not None:
                await room_pass.close()
        finally:
            await super().close()

    def _requeue_burst(self, channel_id: int, fragments: list, authors: set,
                       times: reply_wait.BurstTimes | None = None) -> None:
        """Batch 8, task 2b (ruling T2b-2): a room read said "wait" — the
        owner's fragments go back into the burst (they are already on the record),
        ahead of anything that arrived meanwhile, for the next read. Their
        arrival times go back with them (2026-10-12)."""
        self._burst_fragments[channel_id] = list(fragments) + self._burst_fragments.get(
            channel_id, [])
        self._burst_authors.setdefault(channel_id, set()).update(authors)
        merged = reply_wait.merge(times, self._burst_times.get(channel_id))
        if merged is not None:
            self._burst_times[channel_id] = merged

    async def _deliver_result(self, channel_id: int, result, *,
                              waiting_since: reply_wait.BurstTimes | None = None,
                              during_run: int = 0, wait_kind: str = "",
                              record_as: int | None = None) -> None:
        """Send a turn's result to its channel (Step 27 Phase B, task 8: the
        delivery block relocated verbatim so sidecar results ride the same
        path as the run's own).

        ``waiting_since`` is when the burst this result answers arrived
        (2026-10-12): the typing pause is credited with that wait and the
        real wait is logged. None is paced as before (a reminder or a
        self-started message answers no burst). ``wait_kind`` labels a reply
        given while PaLLi works (10-12).
        ``during_run``: the owner's messages that came in while the run
        worked, for the wait line.
        ``record_as``: whose record a reply that did not arrive marks — the
        owner's (None), or a guest's own id on their reply (10-12).
        """
        whose = self._owner_id if record_as is None else record_as
        attachments = tuple(getattr(result, "attachments", ()) or ())
        voice_notes = tuple(getattr(result, "voice_notes", ()) or ())
        if result.responded and ((result.response_text or "").strip() or attachments
                                 or voice_notes):
            # Cadenced-delivery: a multi-beat CadencedReply fans out as
            # separate Discord messages; single-beat replies use the plain
            # send path. Attachments (screenshot/webcam file paths from
            # tier-1 tools) flow through so files reach the user.
            channel = self.get_channel(channel_id) or await self.fetch_channel(
                channel_id
            )
            # Batch 8, task 10 (B8-5): her voice notes, or — while voice
            # replies are on in this conversation — her whole reply spoken.
            # None: not voice, and the text path below is today's.
            since = reply_wait.last_arrival(waiting_since)
            voiced = await voice_reply.deliver_if_voice(
                channel, channel_id, result, owner_id=whose,
                waiting_since=since)
            if voiced is not None:
                landed, total, reason = voiced
                reply_wait.log_reply_wait("discord", channel_id, waiting_since,
                                          typing=last_send_typing(),
                                          during_run=during_run, kind=wait_kind)
                await self._record_delivery(
                    owner_id=whose, channel_id=channel_id,
                    text=result.response_text, parts=(landed, total), reason=reason)
                return
            if (
                getattr(result, "cadenced_reply", None) is not None
                and len(result.cadenced_reply.beats) > 1
            ):
                await send_turn_reply_cadenced(channel, result.cadenced_reply,
                                               waiting_since=since)
            else:
                await send_turn_reply(
                    channel, result.response_text, attachments=attachments,
                    waiting_since=since,
                )
            reply_wait.log_reply_wait("discord", channel_id, waiting_since,
                                      typing=last_send_typing(),
                                      during_run=during_run, kind=wait_kind)
            await self._record_delivery(
                owner_id=whose, channel_id=channel_id, text=result.response_text)

    async def _record_delivery(self, *, owner_id: int, channel_id: int, text: str,
                               parts: tuple[int, int] | None = None,
                               reason: str | None = None) -> bool:
        """After a reply's send: True when every part reached him. Otherwise
        mark her record (fix plan 5.10) and return False.

        Dispatch wrote the reply to her record before the send, so a send
        that died must mark it. With nothing landed it gets a not-delivered
        row and a full stamp. With the start landed and the rest dead it gets
        an only-partly-delivered row and a partial stamp (R9d). turn_failed
        was already felt inside reply.py. Shielded, with a strong reference:
        his "hello?" right after a dead reply is a barge-in cancel of this
        very task, and it must not cost her the mark.
        """
        # ``parts``/``reason``: a voice reply counts its own parts (Batch 8,
        # task 10); every other caller reads the last send, as before.
        landed, total = parts if parts is not None else last_send_parts()
        if landed >= total:
            return True
        why = reason if parts is not None else last_send_error()
        task = asyncio.ensure_future(persist_delivery_failure(
            owner_id=owner_id, platform="discord", channel_id=channel_id,
            reason=why or "send failed", failed_text=text,
            landed=landed, total=total, sessions_root=self._sessions_root,
        ))
        _DELIVERY_MARK_TASKS.add(task)
        task.add_done_callback(_DELIVERY_MARK_TASKS.discard)
        await asyncio.shield(task)
        return False

    async def _fire_burst(self, channel_id: int) -> None:
        """Coalesce a settled burst's buffered fragments into ONE turn and
        dispatch it as an owned in-flight task (so on_message can barge-in
        cancel it). No-op when the buffer is empty.

        Runs inside the coalescer's fire path, so asyncio.current_task() here
        is the task on_message cancels for barge-in. The coalescer already
        cleared its own per-channel state before calling us, so a post-fire
        note_message starts a fresh burst — we do not touch coalescer state.
        """
        fragments = self._burst_fragments.pop(channel_id, [])
        authors = self._burst_authors.pop(channel_id, set())
        unappended = self._burst_unappended.pop(channel_id, [])
        times = self._burst_times.pop(channel_id, None)
        if not fragments:
            return
        base = fragments[-1]
        joined = "\n".join(
            str(f.content or "") for f in fragments if str(f.content or "").strip()
        )
        images = [
            img for f in fragments for img in (getattr(f, "images", []) or [])
        ]
        # File/code attachments (Important-1 fix, 2026-07-07 review): mirror
        # the images re-collection above. Pre-fix, only base = fragments[-1]'s
        # metadata.text_attachments survived the coalesce, so a file dropped
        # in a non-last fragment was silently lost — recall never saw it.
        text_attachments = tuple(
            r for f in fragments
            for r in (getattr(f.metadata, "text_attachments", ()) or ())
        )
        # DEFECT_RECORD §19 (2026-09-02): the file bucket rides the same way.
        file_attachments = tuple(
            r for f in fragments
            for r in (getattr(f.metadata, "file_attachments", ()) or ())
        )
        # A6 (step 2, part 7): an @mention of someone else in any fragment
        # is the burst's; the base keeps only the last fragment's extras.
        extra = dict(getattr(base.metadata, "extra", None) or {})
        others = [n for n in dict.fromkeys(
            n for f in fragments
            for n in ((getattr(f.metadata, "extra", None) or {}).get("mentions_others") or ()))]
        if others:
            extra["mentions_others"] = others
        # The room-reading fix (2026-10-12): in a group, her name, a reply and
        # an @mention in any fragment are the burst's too; a reply to her is
        # named first. A DM's burst is today's (the owner's word: private
        # chats stay exactly as they are).
        burst_flags: dict = {}
        if not getattr(base.metadata, "is_dm", False):
            frag_extras = [getattr(f.metadata, "extra", None) or {} for f in fragments]
            if any(e.get("names_her") for e in frag_extras):
                extra["names_her"] = True
            replies = [e["reply_to"] for e in frag_extras if isinstance(e.get("reply_to"), dict)]
            if replies:
                extra["reply_to"] = next((r for r in replies if r.get("to_her") is True),
                                         replies[0])
            burst_flags = {
                "is_mention": any(bool(getattr(f.metadata, "is_mention", False))
                                  for f in fragments),
                "is_reply_to_bot": any(bool(getattr(f.metadata, "is_reply_to_bot", False))
                                       for f in fragments),
            }
        md = _dc_replace(
            base.metadata, user_side_prerecorded=True, text_attachments=text_attachments,
            file_attachments=file_attachments, extra=extra, **burst_flags,
        )
        coalesced = _dc_replace(base, content=joined, images=images, metadata=md)

        # Mid-task fork (spec §4b): with a run still in flight at fire time,
        # this burst is steering input, not a fresh turn. handle_mid_task
        # judges it (reply / hold / stop verdict) and enqueues; the run drains
        # it at its next model boundary.
        superseded = False
        prior = self._inflight_tasks.get(channel_id)
        if prior is not None and not prior.done():
            # The 2026-08-23 seam: a plain reply's task outlives its
            # generation by a second or two (delivery, the sweep). A burst
            # that fires in that tail is NOT mid-task input — it waits for
            # the task and becomes an ordinary turn. Only real work (a tool
            # call) or a reply still generating earns a judged lane.
            _sk = _derive_session_key(coalesced)
            if steering_fork.route_mid_run(
                working=_steering.is_working(_sk),
                phase=_steering.get_run_phase(_sk),
                prior_done=prior.done(),
            ) == "wait_then_normal":
                logger.info(
                    "discord client: prior reply finishing (no tool calls, not "
                    "generating) — waiting, then a normal turn for channel %s",
                    channel_id,
                )
                await asyncio.wait({prior}, timeout=steering_fork.TAIL_WAIT_SECONDS)
        if prior is not None and not prior.done():
            session_key = _derive_session_key(coalesced)
            owner_did = await self._local_owner_discord_id()
            owner_ok = (
                owner_did is not None
                and bool(authors)
                and authors == {owner_did}
            )
            channel = self.get_channel(channel_id) or await self.fetch_channel(
                channel_id)

            async def _send(text: str) -> None:
                # A quick reply while she works is timed too (10-12).
                await send_turn_reply(channel, text,
                                      waiting_since=reply_wait.last_arrival(times))
                reply_wait.log_reply_wait("discord", channel_id, times,
                                          typing=last_send_typing(), kind="mid-task reply")

            # Phase-owned routing (2026-08-04): a conversational run that is
            # mid-generation (zero tool calls) is not work — steering's
            # one-liners are the wrong tool for it (the 15:48 incident). One
            # judged binary instead: does this change what she is saying
            # right now? Supersede cancels and regenerates from the full
            # bundle; finish lets the reply land and the run-end sweep
            # guarantees the follow-up turn. The judge fails open to finish.
            if (
                steering_fork._flag_on()
                and owner_ok
                and not _steering.is_working(session_key)
                and _steering.get_run_phase(session_key) == "generating"
            ):
                if await judge_supersede(coalesced, session_key=session_key):
                    superseded = True
                    self._inflight_tasks.pop(channel_id, None)
                    logger.info(
                        "discord client: supersede — cancelling in-flight "
                        "reply for channel %s", channel_id,
                    )
                    prior.cancel()
                    _steering.clear(session_key)
                    steering_fork.reset_reply_budget(session_key)
                    # Fall through to the transcript repair + normal dispatch
                    # below: one combined turn from the full bundle.
                else:
                    bundle = _steering.SteerBundle(
                        turn=coalesced, enqueued_at=time.time(),
                    )
                    if _steering.enqueue(session_key, bundle):
                        steering_fork.arm_deadline(
                            session_key, bundle, dispatch_turn,
                            sessions_root=self._sessions_root,
                        )
                        if prior.done():
                            # Fast-run race (spec §5.4 shape): the reply
                            # finished while we judged — sweep now so the
                            # follow-up fires immediately.
                            try:
                                await steering_fork.sweep_after_run(
                                    session_key, dispatch=dispatch_turn,
                                    sessions_root=self._sessions_root)
                            except Exception:
                                logger.exception(
                                    "discord client: steering sweep failed")
                        return
                    # Queue full: fall through — handle_mid_task declines a
                    # full queue too, and the decline-cancel below applies.

            if not superseded:
                async def _launch_sidecar(turn, *, kind, on_done):
                    # Step 27 Phase B (spec §1/§4): a full turn of her beside
                    # the run, on a child session; its result rides the same
                    # delivery path as the run's own.
                    from core.agent import sidecar as _sc
                    _reg = self._sidecars.setdefault(channel_id, {})

                    async def _deliver(result):
                        await self._deliver_result(
                            channel_id, result, waiting_since=times,
                            wait_kind="side conversation" if kind == "talk" else "side task")

                    _h = _sc.run_sidecar(parent_key=session_key, turn=turn, kind=kind,
                                         dispatch=dispatch_turn, deliver=_deliver,
                                         sessions_root=self._sessions_root, on_done=on_done)
                    if _h is not None:
                        _reg[_h.key] = (kind, _h.task)
                        logger.info("discord client: %s sidecar %s launched beside %s",
                                    kind, _h.key, session_key)
                    return _h

                taken = await steering_fork.handle_mid_task(
                    coalesced, session_key=session_key, is_owner=owner_ok,
                    send=_send, run_in_flight=True, dispatch=dispatch_turn,
                    sessions_root=self._sessions_root,
                    launch_sidecar=_launch_sidecar,
                )
                if taken:
                    if prior.done():
                        # Fast-run race (spec §5.4): the run finished while
                        # the judgment was in flight, so nothing will ever
                        # drain this bundle. Sweep it into a normal turn now
                        # rather than waiting out the deadline watchdog.
                        try:
                            await steering_fork.sweep_after_run(
                                session_key, dispatch=dispatch_turn,
                                sessions_root=self._sessions_root)
                        except Exception:
                            logger.exception(
                                "discord client: steering sweep failed")
                    # Otherwise the running turn owns this input now; it
                    # drains at the next model boundary and the post-run
                    # sweep covers what she held.
                    return
                if not prior.done():
                    # Fork declined (judged stop, non-owner, attachments,
                    # queue full): keep today's contract — cancel the run,
                    # then dispatch this burst as a normal turn below.
                    # Without this cancel the old run and the new dispatch
                    # would interleave writes on ONE session (per-key turns
                    # are strictly sequential; the runner relies on it).
                    # Queued steers die with the work (spec §5.3).
                    self._inflight_tasks.pop(channel_id, None)
                    logger.info(
                        "discord client: steering fork declined — cancelling "
                        "in-flight reply for channel %s",
                        channel_id,
                    )
                    prior.cancel()
                    _steering.clear(session_key)
                    steering_fork.reset_reply_budget(session_key)

        # Transcript repair for the non-taken paths: fragments that skipped
        # their arrival append (steer lane, spec §4e) are about to dispatch as
        # a NORMAL prerecorded turn, whose runner path appends nothing. Write
        # them now, in arrival order, so his words reach the record exactly
        # once whichever lane the burst ends up on.
        for _t in unappended:
            _text = str(getattr(_t, "content", "") or "")
            if not _text.strip():
                continue
            try:
                await asyncio.shield(_open_session_with_user_message(
                    _derive_session_key(_t), self._sessions_root, _text,
                    speaker=getattr(getattr(_t, "metadata", None), "speaker", None),
                ))
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception(
                    "discord client: steer-lane transcript repair failed")

        # Step 27 Phase B (spec §3): a paused run waits on his word. The
        # judge (non-thinking, cheap) reads his message against the paused
        # work: "resume" continues it — the same session, the loop picks up
        # from its own transcript; "stop" closes the pause and its clock
        # (correction 2); anything else is an ordinary turn and the pause
        # stands. Either way the message dispatches as a normal turn below,
        # carrying a trailing note the runner renders for the model.
        from core.agent import duplex as _dx
        _sk_p = _derive_session_key(coalesced)
        _paused = _steering.get_paused(_sk_p) if _dx.duplex_enabled() else None
        if _paused is not None:
            _pj = await judge_mid_task(coalesced, session_key=_sk_p, paused_doing=_paused.now_doing)
            _route = steering_fork.route_paused(action=_pj.action)
            _pb_extra = dict(getattr(coalesced.metadata, "extra", None) or {})
            if _route == "resume":
                _steering.clear_paused(_sk_p)
                _pt = self._pause_timers.pop(channel_id, None)
                if _pt is not None:
                    _pt.cancel()
                _pb_extra["resume_of"] = _paused.now_doing
                logger.info("discord client: resuming paused run on channel %s", channel_id)
            elif _route == "stop":
                _steering.clear_paused(_sk_p)
                _pt = self._pause_timers.pop(channel_id, None)
                if _pt is not None:
                    _pt.cancel()
                _steering.clear(_sk_p)
                logger.info("discord client: paused run dropped on his word, channel %s", channel_id)
            else:
                _pb_extra["paused_task"] = _paused.now_doing
            coalesced = _dc_replace(coalesced, metadata=_dc_replace(coalesced.metadata, extra=_pb_extra))

        current = asyncio.current_task()
        self._inflight_tasks[channel_id] = current
        _prep_entry = (fragments, authors, times, _derive_session_key(coalesced))
        self._preparing[channel_id] = _prep_entry
        _messages_before = self._owner_messages_in.get(channel_id, 0)
        _terminated_by = ""
        try:
            # She decides (Batch 8, task 2b — rulings T2b-1, T2b-2): in a
            # shared room with companion.she_decides on, the owner's burst is
            # read first — answer runs the turn below, stay quiet or wait
            # runs nothing (the words are already on the record). Inside the
            # in-flight registration, so the next message barge-ins the read
            # like any reply. A pass-through otherwise.
            _turn = await self._room_pass.owner_burst(
                coalesced, fragments=fragments, who_id=next(iter(authors), None),
                requeue=lambda: self._requeue_burst(channel_id, fragments, authors, times),
                refire=lambda: self._fire_burst(channel_id),
            )
            if _turn is None:
                return
            result = await dispatch_turn(_turn)
            if self._preparing.get(channel_id) is _prep_entry:
                self._preparing.pop(channel_id, None)   # written: typing can't cancel it now
            _terminated_by = str(getattr(result, "terminated_by", "") or "")
            await self._deliver_result(
                channel_id, result, waiting_since=times,
                during_run=self._owner_messages_in.get(channel_id, 0) - _messages_before)
        except asyncio.CancelledError:
            logger.info(
                "discord client: coalesced reply cancelled (barge-in) "
                "channel %s",
                channel_id,
            )
            raise
        except Exception:
            logger.exception("discord client: coalesced turn failed")
        finally:
            if self._inflight_tasks.get(channel_id) is current:
                self._inflight_tasks.pop(channel_id, None)
            if self._preparing.get(channel_id) is _prep_entry:
                self._preparing.pop(channel_id, None)
            if channel_id in self._sidecars:
                self._sidecars[channel_id] = {
                    k: v for k, v in self._sidecars[channel_id].items() if not v[1].done()}
            if _terminated_by == "paused":
                # Step 27 Phase B (spec §3): the run set its work down on his
                # word. No sweep — leftovers drain at the next run on this
                # session (correction 2). Arm the honest expiry: a pause
                # nobody returns to is told once, never left silent.
                try:
                    from core.agent import duplex as _dxx
                    _old = self._pause_timers.pop(channel_id, None)
                    if _old is not None:
                        _old.cancel()
                    self._pause_timers[channel_id] = steering_fork.arm_pause_expiry(
                        _derive_session_key(coalesced),
                        ttl_minutes=int(_dxx._knob("duplex_pause_ttl_minutes", 120)),
                    )
                    logger.info("discord client: run paused on channel %s; expiry armed", channel_id)
                except Exception:
                    logger.exception("discord client: pause expiry arming failed")
            elif steering_fork._flag_on():
                # Post-run sweep (spec §4f): whatever this run never answered
                # becomes a real turn now, oldest first. No-op when the queue
                # is empty (the overwhelmingly common case).
                try:
                    await steering_fork.sweep_after_run(
                        _derive_session_key(coalesced), dispatch=dispatch_turn,
                        sessions_root=self._sessions_root)
                except Exception:
                    logger.exception("discord client: steering sweep failed")


async def start_discord_text_client() -> None:
    """Wave 3 entry point — Discord text on the new spine.

    Parameterless to match the legacy `bots.discord.bot.start_discord_bot`
    contract that core.plugin_runtime expects. Reads token + owner_id
    from the same config the legacy bot does. Single-owner local mode
    means owner_id is the well-known local owner; the launcher's owner
    resolution is the canonical place to centralize this in a future
    polish pass (Wave 6).

    Wave 4 wired the proactive consideration loop in alongside this
    client; step 19 (2026-08-19) deleted that loop — the arbiter owns
    self-initiated speech, and the residual jobs ride the moment engine.
    """
    from core.config import config

    token = (config.DISCORD_TOKEN or "").strip()
    if not token:
        logger.error("PalliDiscordClient cannot start — DISCORD_TOKEN missing")
        return

    # Match the legacy bot.py's intent set verbatim. Intents.all() turns
    # ON every intent including the three privileged ones (members,
    # presences, message_content) — they're already enabled in the
    # Discord Developer Portal because the legacy bot relied on them.
    # Intents.default() excludes those + appears to also drop typing
    # event delivery in our environment, so on_typing never fires
    # under the default set.
    intents = discord.Intents.all()
    intents.message_content = True

    refs = await get_local_owner_refs()
    owner_id = refs.get("app_user_id")
    if owner_id is None:
        logger.error("PalliDiscordClient cannot start — local owner unresolved")
        return

    client = PalliDiscordClient(owner_id=owner_id, intents=intents)
    # The process-wide guest budget: one cap across every door she has, so a
    # stranger cannot spend twice by walking round to the other one. (The
    # constructor takes a gate too — that is the seam a second door's client
    # uses — but this call site keeps the exact argument list
    # tests/bots/test_discord_client.py:460 fakes.)
    client.attach_guest_gate(shared_guest_gate())
    # Wave 5: expose the active client so callers (ambient_service, etc.)
    # can reach it without importing from the deleted bots.discord.bot.
    global _active_client
    _active_client = client

    # Register the Discord adapter with channel_runtime so the delegation
    # bridge's _send_immediate_ack and other legacy delivery paths can
    # reach Discord. (Legacy bots/discord/bot.py used to do this; deleted
    # in Wave 5.) Adapter holds a reference to the client; send_envelope
    # waits for is_ready() before resolving channels.
    try:
        await channel_runtime.register_adapter(
            "discord",
            DiscordChannelAdapter(client),
            metadata={"label": "Discord (new spine)"},
        )
    except Exception:
        logger.exception("Failed to register Discord channel adapter")

    # The owner's DMs as a road for plain notices that are not hers (the
    # spending check, 2026-10-04). Not through channel_runtime: that road
    # tells her Present Moment she reached out, and wants a channel id the
    # owner's DM does not have until Discord opens it (core/owner_notice.py).
    register_owner_notice_sender("discord", client.send_owner_notice)

    try:
        await client.start(token)
    except (KeyboardInterrupt, asyncio.CancelledError):
        await client.close()
        raise
    except Exception:
        logger.exception("PalliDiscordClient run failed")
        try:
            await client.close()
        except Exception:
            logger.exception("PalliDiscordClient close after error also failed")
        raise
    finally:
        unregister_owner_notice_sender("discord", client.send_owner_notice)
        await _shutdown_voice_sessions()
