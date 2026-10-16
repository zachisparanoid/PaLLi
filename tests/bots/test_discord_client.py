"""Wave 3: tests for bots.discord.client — slim discord.py client on the new spine."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _channel():
    channel = MagicMock()
    channel.id = 67890
    channel.send = AsyncMock()
    channel.typing = MagicMock()
    channel.typing.return_value.__aenter__ = AsyncMock()
    channel.typing.return_value.__aexit__ = AsyncMock()
    return channel


def _msg(*, content: str = "hi", author_id: int = 12345, is_bot: bool = False, mentions=None):
    msg = MagicMock()
    msg.author = MagicMock()
    msg.author.bot = is_bot
    msg.author.id = author_id
    msg.author.name = "user"
    msg.content = content
    msg.attachments = []
    msg.mentions = mentions or []
    msg.channel = _channel()
    return msg


# --- Controllable clock for burst-coalescer timing tests -------------------
# The BurstCoalescer keys all timing off an injectable clock. Timing tests set
# ``client._coalescer._clock = _clock`` and drive time with ``_advance_clock``
# so the 3s settle / 10s typing-hold windows resolve deterministically instead
# of sleeping in real time. Each clock-using test calls ``_reset_clock()`` first
# (module-level state is shared, so reset then advance forward).
_CLOCK_NOW = [1000.0]


def _clock() -> float:
    return _CLOCK_NOW[0]


def _advance_clock(delta: float) -> None:
    _CLOCK_NOW[0] += delta


def _reset_clock(value: float = 1000.0) -> None:
    _CLOCK_NOW[0] = value


async def _async_none():
    """Awaitable no-op — stands in for record_user_fragment in tests."""
    return None


def _the_author_is_the_owner(client, author_id: int = 12345) -> None:
    """Say out loud what a test about HIS lane assumes: this author is him.

    Week 2 task 3 (gap §5.1.5): an unresolved owner Discord id is a GUEST
    now, and under the suite's throwaway database nothing resolves one — so
    a test about the owner's own path (bursts, barge-in, attachments) has to
    name the owner instead of inheriting him from the old fallback.
    """
    async def owner_did():
        return author_id

    client._local_owner_discord_id = owner_did



@pytest.mark.asyncio
async def test_on_message_skips_bot_authored_messages() -> None:
    """The self-reply guard (client.py on_message) must return BEFORE any
    recording/buffering happens — otherwise PaLLi could loop on its own
    messages. dispatch_turn.assert_not_called() alone is tautological now
    that dispatch moved to _fire_burst (the only call site): it would pass
    for ANY message, guarded or not. The real probe is that the guard short-
    circuits before record_user_fragment is awaited and before the fragment
    is buffered / fed to the coalescer."""
    from bots.discord.client import PalliDiscordClient

    import discord
    client = PalliDiscordClient(bot_user_id=99999, owner_id=1, intents=discord.Intents.default())
    msg = _msg(is_bot=True, author_id=12345)
    with patch("bots.discord.client.dispatch_turn", new=AsyncMock()) as p, \
         patch("bots.discord.client.record_user_fragment", new=AsyncMock()) as rec:
        await client.on_message(msg)
        p.assert_not_called()
        rec.assert_not_awaited()
        assert msg.channel.id not in client._burst_fragments
        assert msg.channel.id not in client._coalescer._state


@pytest.mark.asyncio
async def test_on_message_skips_self_messages() -> None:
    """Same real-effect probe as the bot-authored guard test, above, but for
    the author.id == self._bot_user_id branch (PaLLi's own messages echoed
    back by Discord)."""
    from bots.discord.client import PalliDiscordClient

    import discord
    client = PalliDiscordClient(bot_user_id=99999, owner_id=1, intents=discord.Intents.default())
    msg = _msg(is_bot=False, author_id=99999)
    with patch("bots.discord.client.dispatch_turn", new=AsyncMock()) as p, \
         patch("bots.discord.client.record_user_fragment", new=AsyncMock()) as rec:
        await client.on_message(msg)
        p.assert_not_called()
        rec.assert_not_awaited()
        assert msg.channel.id not in client._burst_fragments
        assert msg.channel.id not in client._coalescer._state


@pytest.mark.asyncio
async def test_on_message_voice_message_dispatches_transcript() -> None:
    """End-to-end seam (2026-06-11), re-pointed to burst coalescing: a Discord
    voice message must reach dispatch_turn with the TRANSCRIPT as the turn
    content — not the empty string that poisoned the agent session and 400'd
    every later turn. Dispatch is now deferred to the settle: on_message
    resolves the transcript + records the fragment; _fire_burst dispatches the
    coalesced turn (still carrying the transcript + is_voice)."""
    from bots.discord.client import PalliDiscordClient
    from core.turn.types import TurnResult

    import discord
    client = PalliDiscordClient(bot_user_id=99999, owner_id=1, intents=discord.Intents.default())
    _the_author_is_the_owner(client)
    msg = _msg(content="")
    att = MagicMock()
    att.content_type = "audio/ogg"
    att.filename = "voice-message.ogg"  # real str — a bare MagicMock's .endswith() is truthy and misclassifies as text attachment
    att.read = AsyncMock(return_value=b"OggS-fake")
    msg.attachments = [att]

    fake_result = TurnResult(
        responded=False, response_text="", decision=None,
        reflection_applied=False, delegation_tier_used=0,
    )
    with patch("bots.discord.client.dispatch_turn", new=AsyncMock(return_value=fake_result)) as p, \
         patch("bots.discord.client.record_user_fragment", new=AsyncMock()), \
         patch("bots.discord.voice_attachment.transcribe_voice_message",
               new=AsyncMock(return_value="hey, did the deploy finish?")):
        await client.on_message(msg)              # resolve transcript, record + buffer
        await client._fire_burst(msg.channel.id)  # settle -> coalesced dispatch
        p.assert_awaited_once()
        sent_turn = p.await_args.args[0]
        assert sent_turn.content == "hey, did the deploy finish?"
        assert sent_turn.metadata.is_voice is True
        assert sent_turn.metadata.user_side_prerecorded is True


@pytest.mark.asyncio
async def test_on_message_calls_process_turn_with_built_turn() -> None:
    """Re-pointed to burst coalescing: on_message records + buffers; the settle
    (_fire_burst) dispatches ONE coalesced turn built from the fragment and
    sends the reply through the resolved channel."""
    from bots.discord.client import PalliDiscordClient
    from core.turn.types import Turn, TurnResult

    import discord
    client = PalliDiscordClient(bot_user_id=99999, owner_id=1, intents=discord.Intents.default())
    _the_author_is_the_owner(client)
    msg = _msg(content="hello")
    # _fire_burst resolves the channel via get_channel; stub it to the
    # message's channel so send_turn_reply -> channel.send is observable
    # (there is no live gateway to fetch_channel from in tests).
    client.get_channel = lambda cid: msg.channel
    fake_result = TurnResult(
        responded=True,
        response_text="hi back",
        decision=None,
        reflection_applied=False,
        delegation_tier_used=0,
    )
    with patch("bots.discord.client.dispatch_turn", new=AsyncMock(return_value=fake_result)) as p, \
         patch("bots.discord.client.record_user_fragment", new=AsyncMock()):
        await client.on_message(msg)
        await client._fire_burst(msg.channel.id)
        p.assert_awaited_once()
        sent_turn = p.call_args.args[0]
        assert isinstance(sent_turn, Turn)
        assert sent_turn.surface == "discord_text"
        assert sent_turn.content == "hello"
        assert sent_turn.metadata.user_side_prerecorded is True
    msg.channel.send.assert_called_once_with("hi back")


@pytest.mark.asyncio
async def test_on_message_does_not_send_when_responded_false() -> None:
    """Re-pointed to burst coalescing: when the coalesced dispatch says
    responded=False, _fire_burst sends nothing."""
    from bots.discord.client import PalliDiscordClient
    from core.turn.types import TurnResult

    import discord
    client = PalliDiscordClient(bot_user_id=99999, owner_id=1, intents=discord.Intents.default())
    msg = _msg(content="ambient chatter")
    client.get_channel = lambda cid: msg.channel
    fake_result = TurnResult(
        responded=False, response_text="", decision=None,
        reflection_applied=False, delegation_tier_used=0,
    )
    with patch("bots.discord.client.dispatch_turn", new=AsyncMock(return_value=fake_result)), \
         patch("bots.discord.client.record_user_fragment", new=AsyncMock()):
        await client.on_message(msg)
        await client._fire_burst(msg.channel.id)
    msg.channel.send.assert_not_called()


@pytest.mark.asyncio
async def test_fire_burst_swallows_processing_errors() -> None:
    """If dispatch_turn raises, the coalesced fire path must not propagate —
    discord.py bubbles unhandled exceptions in the gateway loop and they can
    sever the connection. (Re-pointed: the dispatch now happens inside
    _fire_burst, run as a coalescer task, not inline in on_message.)"""
    from bots.discord.client import PalliDiscordClient

    import discord
    client = PalliDiscordClient(bot_user_id=99999, owner_id=1, intents=discord.Intents.default())
    msg = _msg(content="hi")
    with patch(
        "bots.discord.client.dispatch_turn",
        new=AsyncMock(side_effect=RuntimeError("spine boom")),
    ), patch("bots.discord.client.record_user_fragment", new=AsyncMock()):
        await client.on_message(msg)  # records + buffers
        # Should NOT raise even though dispatch raises inside the fire path.
        await client._fire_burst(msg.channel.id)
    msg.channel.send.assert_not_called()


@pytest.mark.asyncio
async def test_on_typing_does_not_cancel_in_flight_reply() -> None:
    """New contract (2026-07-07): typing HOLDS the settle window; it must NOT
    cancel an in-flight reply. The pre-2026-07-07 7s typing-cancel is gone.
    One exception since 2026-10-12: the owner's typing cancels their own
    burst's reply while it is still being prepared
    (tests/bots/test_discord_typing_rule.py). A task with no burst in
    preparation, like this one, is never cancelled by typing."""
    import asyncio
    from bots.discord.client import PalliDiscordClient
    import discord

    client = PalliDiscordClient(bot_user_id=99999, owner_id=1, intents=discord.Intents.default())

    # Simulate an in-flight coalesced reply for the channel.
    inflight = asyncio.create_task(asyncio.sleep(0.5))
    client._inflight_tasks[67890] = inflight

    typing_user = MagicMock()
    typing_user.id = 12345  # owner
    typing_user.bot = False
    channel = MagicMock()
    channel.id = 67890

    await client.on_typing(channel, typing_user, None)

    # Task.cancel() is asynchronous — it only delivers CancelledError the
    # next time the task is resumed. Checking .done() immediately would pass
    # even if on_typing had (wrongly) called prior.cancel(), because the
    # cancellation hasn't been delivered yet. Yield a few event-loop turns
    # first so a reintroduced cancel would actually surface here.
    for _ in range(5):
        await asyncio.sleep(0)
    assert not inflight.done()  # typing did NOT cancel the in-flight reply
    # ...and the typing was recorded on the coalescer (extends the settle).
    assert client._coalescer._state[67890].typing_active_until > 0

    inflight.cancel()
    try:
        await inflight
    except asyncio.CancelledError:
        pass
    client._coalescer.mark_replied(67890)  # cancel the coalescer's settle task


@pytest.mark.asyncio
async def test_on_typing_ignores_self_typing() -> None:
    """When PaLLi shows its own typing indicator, on_typing must ignore it:
    it must neither disturb an in-flight reply nor feed the coalescer (self
    typing is not a 'user still composing' signal)."""
    import asyncio
    from bots.discord.client import PalliDiscordClient
    import discord

    client = PalliDiscordClient(bot_user_id=99999, owner_id=1, intents=discord.Intents.default())

    fake_task = asyncio.create_task(asyncio.sleep(0.5))
    client._inflight_tasks[67890] = fake_task

    self_user = MagicMock()
    self_user.id = 99999  # bot's own user id
    self_user.bot = True
    channel = MagicMock()
    channel.id = 67890

    await client.on_typing(channel, self_user, None)
    assert not fake_task.done()  # in-flight reply untouched
    assert 67890 not in client._coalescer._state  # self typing not recorded
    fake_task.cancel()
    try:
        await fake_task
    except asyncio.CancelledError:
        pass


@pytest.mark.asyncio
async def test_on_typing_without_pending_message_records_but_does_not_fire() -> None:
    """Re-pointed: typing with nothing buffered records the hold on the
    coalescer but must not raise or fire a dispatch — there is no un-answered
    message yet, so the coalescer's evaluator stays idle."""
    import asyncio
    from bots.discord.client import PalliDiscordClient
    import discord

    client = PalliDiscordClient(bot_user_id=99999, owner_id=1, intents=discord.Intents.default())
    channel = MagicMock()
    channel.id = 67890
    user = MagicMock()
    user.id = 12345
    user.bot = False

    fired = {"hit": False}

    async def _spy_fire(cid):
        fired["hit"] = True

    client._coalescer._on_fire = _spy_fire

    # Should not raise even with nothing buffered.
    await client.on_typing(channel, user, None)
    # Give the coalescer's settle task a few cycles; with no un-answered
    # message the evaluator returns idle and never fires.
    for _ in range(5):
        await asyncio.sleep(0.02)
    assert fired["hit"] is False
    client._coalescer.mark_replied(67890)


@pytest.mark.asyncio
async def test_on_message_barges_in_on_in_flight_reply() -> None:
    """Barge-in (kept, mechanism re-pointed): a message arriving while PaLLi is
    mid-reply cancels the in-flight coalesced reply. The in-flight task is now
    the _fire_burst task registered in _inflight_tasks; on_message cancels it
    before recording the new fragment. Typing no longer participates."""
    import asyncio
    from bots.discord.client import PalliDiscordClient
    import discord

    client = PalliDiscordClient(bot_user_id=99999, owner_id=1, intents=discord.Intents.default())
    _the_author_is_the_owner(client)

    cancelled = {"hit": False}

    async def slow_reply():
        try:
            await asyncio.sleep(5.0)
        except asyncio.CancelledError:
            cancelled["hit"] = True
            raise

    # Simulate an in-flight coalesced reply for the channel.
    inflight = asyncio.create_task(slow_reply())
    await asyncio.sleep(0)  # let it park at the sleep
    client._inflight_tasks[67890] = inflight

    msg = _msg(content="barge in")
    msg.channel.id = 67890

    with patch("bots.discord.client.record_user_fragment", new=AsyncMock()):
        await client.on_message(msg)

    try:
        await inflight
    except asyncio.CancelledError:
        pass

    assert cancelled["hit"] is True
    # The new message started a fresh burst (its fragment is buffered).
    assert len(client._burst_fragments.get(67890, [])) == 1
    client._coalescer.mark_replied(67890)  # cancel the fresh settle task


@pytest.mark.asyncio
async def test_typing_holds_settle_until_window_expires(monkeypatch) -> None:
    """Re-points the deleted _pending_typing_cancels race test. Typing now
    HOLDS the burst instead of arming a cancel: a buffered message will NOT
    fire while typing keeps the window open, and fires once the typing window
    lapses. This is the new-world guarantee that a message composed across
    typing pauses is neither dropped nor prematurely answered."""
    import asyncio
    import discord
    from bots.discord import client as client_mod
    from core.turn.types import TurnResult

    dispatched: list = []

    async def fake_dispatch(turn):
        dispatched.append(turn)
        return TurnResult(
            responded=False, response_text="", decision=None,
            reflection_applied=False, delegation_tier_used=0,
        )

    monkeypatch.setattr(client_mod, "dispatch_turn", fake_dispatch)
    monkeypatch.setattr(
        client_mod, "record_user_fragment", lambda *a, **k: _async_none(),
    )

    client = client_mod.PalliDiscordClient(
        owner_id=1, intents=discord.Intents.default(), bot_user_id=42,
    )
    _the_author_is_the_owner(client)
    _reset_clock()
    client._coalescer._clock = _clock  # deterministic timing

    # Message at t0, then a typing event extending the hold to t0 + 10.
    await client.on_message(_msg(content="hang on, still typing"))
    typing_user = MagicMock()
    typing_user.id = 1
    typing_user.bot = False
    channel = MagicMock()
    channel.id = 67890  # same channel as _msg()
    await client.on_typing(channel, typing_user, None)

    _advance_clock(3.5)  # past settle(3.0) but within the typing window(10.0)
    for _ in range(8):
        await asyncio.sleep(0.02)
    assert dispatched == []  # typing HELD the settle open

    _advance_clock(7.0)  # now past the typing window (t0 + 10.5)
    for _ in range(50):
        await asyncio.sleep(0.02)
        if dispatched:
            break
    assert len(dispatched) == 1
    assert dispatched[0].content == "hang on, still typing"


@pytest.mark.asyncio
async def test_start_discord_text_client_uses_resolved_owner_id(monkeypatch) -> None:
    """The Discord launcher must resolve owner_id via the canonical
    local_mode resolver instead of hardcoding 1.

    Decision 2 of the 2026-05-08 completion refactor: every surface that
    needs an owner_id must go through `core.local_mode.get_local_owner_refs`.
    """
    from bots.discord import client as client_mod
    from core.config import config

    monkeypatch.setattr(config, "DISCORD_TOKEN", "test_token_xyz")

    async def fake_refs():
        return {"app_user_id": 42, "palli_user_id": 42, "discord_user_id": None}

    monkeypatch.setattr(client_mod, "get_local_owner_refs", fake_refs)

    captured: dict[str, int] = {}

    class _SentinelStop(Exception):
        pass

    def _capture_init(self, *, owner_id, intents, bot_user_id=0):
        captured["owner_id"] = owner_id
        raise _SentinelStop("stop after capture")

    monkeypatch.setattr(client_mod.PalliDiscordClient, "__init__", _capture_init)

    with pytest.raises(_SentinelStop):
        await client_mod.start_discord_text_client()

    assert captured["owner_id"] == 42


@pytest.mark.asyncio
async def test_start_discord_text_client_aborts_when_local_owner_unresolved(
    monkeypatch,
) -> None:
    """If the canonical resolver returns None, the launcher must abort
    rather than silently fall back to user_id=1."""
    from bots.discord import client as client_mod
    from core.config import config

    monkeypatch.setattr(config, "DISCORD_TOKEN", "test_token_xyz")

    async def fake_refs_unresolved():
        return {"app_user_id": None, "palli_user_id": None, "discord_user_id": None}

    monkeypatch.setattr(client_mod, "get_local_owner_refs", fake_refs_unresolved)

    sentinel_hit = {"hit": False}

    def _should_not_run(self, **_kwargs):
        sentinel_hit["hit"] = True
        raise AssertionError("PalliDiscordClient must not be constructed when owner unresolved")

    monkeypatch.setattr(client_mod.PalliDiscordClient, "__init__", _should_not_run)

    # Returns without raising.
    await client_mod.start_discord_text_client()
    assert sentinel_hit["hit"] is False


@pytest.mark.asyncio
async def test_rapid_burst_dispatches_one_coalesced_turn(monkeypatch) -> None:
    """Burst coalescing (2026-07-07): three rapid messages in one channel
    settle into a SINGLE dispatched turn whose content is the fragments joined
    by newlines and whose metadata is flagged user_side_prerecorded (the
    fragments were recorded on arrival, so the engine must not re-record)."""
    import asyncio
    from types import SimpleNamespace

    import discord
    from bots.discord import client as client_mod

    dispatched: list = []

    async def fake_dispatch(turn):
        dispatched.append(turn)
        return SimpleNamespace(
            responded=True, response_text="ok", attachments=(), cadenced_reply=None,
        )

    monkeypatch.setattr(client_mod, "dispatch_turn", fake_dispatch)
    monkeypatch.setattr(
        client_mod, "record_user_fragment", lambda *a, **k: _async_none(),
    )
    # Neutralize the reply-send path: there is no live gateway in tests, so a
    # real send_turn_reply / fetch_channel would fail or hang. Patch the send
    # helpers to no-op AsyncMocks and stub get_channel to a truthy fake so
    # _fire_burst never reaches fetch_channel (HTTP). The test then exercises
    # ONLY the coalesce -> dispatch contract.
    monkeypatch.setattr(client_mod, "send_turn_reply", AsyncMock())
    monkeypatch.setattr(client_mod, "send_turn_reply_cadenced", AsyncMock())

    client = client_mod.PalliDiscordClient(
        owner_id=1, intents=discord.Intents.default(), bot_user_id=42,
    )
    _the_author_is_the_owner(client)
    monkeypatch.setattr(client, "get_channel", lambda cid: _channel())
    _reset_clock()
    client._coalescer._clock = _clock  # deterministic settle timing

    for txt in ("one", "two", "three"):
        await client.on_message(_msg(content=txt))

    _advance_clock(3.5)  # past the 3.0s settle
    for _ in range(50):
        await asyncio.sleep(0.02)
        if dispatched:
            break

    assert len(dispatched) == 1
    assert dispatched[0].content == "one\ntwo\nthree"
    assert dispatched[0].metadata.user_side_prerecorded is True


@pytest.mark.asyncio
async def test_rapid_burst_recollects_text_attachments_from_non_last_fragment(
    monkeypatch,
) -> None:
    """Important-1 regression fix (2026-07-07 final review): _fire_burst
    re-collected `images` across ALL fragments but not `text_attachments`, so
    the coalesced turn only inherited metadata.text_attachments from
    base = fragments[-1]. A file attachment dropped in a NON-last fragment of
    a burst was silently lost — recall never read it. This drives a burst
    where the file-attachment fragment arrives FIRST (not last) and asserts
    it still surfaces on the coalesced turn's metadata.text_attachments."""
    import asyncio
    from types import SimpleNamespace

    import discord
    from bots.discord import client as client_mod

    dispatched: list = []

    async def fake_dispatch(turn):
        dispatched.append(turn)
        return SimpleNamespace(
            responded=True, response_text="ok", attachments=(), cadenced_reply=None,
        )

    monkeypatch.setattr(client_mod, "dispatch_turn", fake_dispatch)
    monkeypatch.setattr(
        client_mod, "record_user_fragment", lambda *a, **k: _async_none(),
    )
    monkeypatch.setattr(client_mod, "send_turn_reply", AsyncMock())
    monkeypatch.setattr(client_mod, "send_turn_reply_cadenced", AsyncMock())

    client = client_mod.PalliDiscordClient(
        owner_id=1, intents=discord.Intents.default(), bot_user_id=42,
    )
    _the_author_is_the_owner(client)
    monkeypatch.setattr(client, "get_channel", lambda cid: _channel())
    _reset_clock()
    client._coalescer._clock = _clock  # deterministic settle timing

    # Fragment 1 (NOT last): a bare file attachment, no caption -> intake
    # synthesizes "[file attached]" and populates metadata.text_attachments.
    file_msg = _msg(content="")
    file_att = MagicMock()
    file_att.content_type = "text/plain"
    file_att.filename = "notes.txt"
    file_att.url = "https://cdn.example.com/notes.txt"
    file_att.size = 42
    file_msg.attachments = [file_att]

    # Fragment 2 (last -> base = fragments[-1]): plain text, no attachments.
    text_msg = _msg(content="here's the file")
    text_msg.channel = file_msg.channel  # same channel -> same burst

    await client.on_message(file_msg)
    await client.on_message(text_msg)

    _advance_clock(3.5)  # past the 3.0s settle
    for _ in range(50):
        await asyncio.sleep(0.02)
        if dispatched:
            break

    assert len(dispatched) == 1
    coalesced = dispatched[0]
    # Base (fragments[-1]) carried NO text_attachments of its own — this only
    # passes because _fire_burst re-collects across every fragment.
    assert len(coalesced.metadata.text_attachments) == 1
    assert coalesced.metadata.text_attachments[0].filename == "notes.txt"


def test_sessions_root_honors_the_env_pin(monkeypatch, tmp_path):
    """2026-08-18: the client hardcoded data/agent_sessions, so its tests
    appended to a LIVE data/agent_sessions/discord_67890.jsonl since 07-26
    while dispatch/outbound/proactive honored PALLI_AGENT_SESSIONS_ROOT.
    Live runs leave the env unset → identical behavior."""
    from bots.discord.client import PalliDiscordClient

    import discord
    monkeypatch.setenv("PALLI_AGENT_SESSIONS_ROOT", str(tmp_path / "sessions"))
    client = PalliDiscordClient(bot_user_id=99999, owner_id=1, intents=discord.Intents.default())
    assert client._sessions_root == tmp_path / "sessions"
    monkeypatch.delenv("PALLI_AGENT_SESSIONS_ROOT")
    client = PalliDiscordClient(bot_user_id=99999, owner_id=1, intents=discord.Intents.default())
    assert str(client._sessions_root) == "data/agent_sessions"


# ── the owner gate (item 7a, 2026-08-19) ──────────────────────────────────

@pytest.mark.asyncio
async def test_guest_message_gets_a_plain_reply_and_never_touches_his_lane() -> None:
    """A non-owner author: ONE guest turn (owner_id = their id, origin
    'guest', content tagged with their name), dispatched directly and
    answered in-channel — no burst buffer, no barge-in on his in-flight
    reply, no record into his history."""
    from bots.discord.client import PalliDiscordClient
    from core.turn.types import TurnResult
    import discord
    client = PalliDiscordClient(bot_user_id=99999, owner_id=1, intents=discord.Intents.default())

    async def owner_did():
        return 777                       # HIS Discord id; the author below is 12345
    client._local_owner_discord_id = owner_did
    msg = _msg(content="hey palli, what are you?", author_id=12345)
    msg.author.display_name = "Dana"
    client.get_channel = lambda cid: msg.channel
    # his reply is in flight on this channel — a guest must not cancel it
    import asyncio
    inflight = asyncio.get_running_loop().create_future()
    client._inflight_tasks[msg.channel.id] = inflight
    fake_result = TurnResult(responded=True, response_text="I'm PaLLi — hi Dana.",
                             decision=None, reflection_applied=False, delegation_tier_used=0)
    with patch("bots.discord.client.dispatch_turn", new=AsyncMock(return_value=fake_result)) as p, \
         patch("bots.discord.client.record_user_fragment", new=AsyncMock()) as rec:
        await client.on_message(msg)
        p.assert_awaited_once()
        t = p.call_args.args[0]
        assert t.owner_id == 12345
        assert t.metadata.origin == "guest"
        assert t.content == "[Dana]: hey palli, what are you?"
        rec.assert_not_awaited()                     # not his history
    msg.channel.send.assert_called_once_with("I'm PaLLi — hi Dana.")
    assert not inflight.cancelled()                  # his reply survives
    assert client._burst_fragments.get(msg.channel.id) in (None, [])
    inflight.cancel()


# ── a guest's voice note, heard (the owner, 10-12) ────────────────────────
# A friend's voice note in the shared room reached her as "[Dana]:
# [VOICE_MESSAGE]": the room read had no words to judge and stayed quiet.
# Guests' messages are "normal messages, minus permissions" — so their
# voice notes are transcribed BEFORE the room read, as his are.

class _FakeSTT:
    """voice.deepgram_client.deepgram_client's one method, recorded."""

    def __init__(self, transcript):
        self.transcript = transcript
        self.calls: list[bytes] = []

    async def transcribe_audio(self, data, *, content_type):
        self.calls.append(bytes(data))
        return self.transcript


def _guest_voice_msg(*, content: str = "", size: int = 13863):
    msg = _msg(content=content, author_id=12345)
    msg.author.display_name = "Dana"
    att = MagicMock()
    att.content_type = "audio/ogg"
    att.filename = "voice-message.ogg"
    att.size = size
    att.read = AsyncMock(return_value=b"OggS-fake-voice")
    msg.attachments = [att]
    return msg, att


async def _guest_turn_for(msg, monkeypatch, stt):
    """Run one guest message through the door; return the turn dispatched."""
    import discord
    import voice.deepgram_client as dg
    from bots.discord.client import PalliDiscordClient
    from core.turn.types import TurnResult

    monkeypatch.setattr(dg, "deepgram_client", stt)
    client = PalliDiscordClient(bot_user_id=99999, owner_id=1, intents=discord.Intents.default())

    async def owner_did():
        return 777                       # HIS Discord id; the author is 12345
    client._local_owner_discord_id = owner_did
    client.get_channel = lambda cid: msg.channel
    fake_result = TurnResult(responded=True, response_text="hi Dana", decision=None,
                             reflection_applied=False, delegation_tier_used=0)
    with patch("bots.discord.client.dispatch_turn",
               new=AsyncMock(return_value=fake_result)) as p, \
         patch("bots.discord.client.record_user_fragment", new=AsyncMock()) as rec:
        await client.on_message(msg)
        rec.assert_not_awaited()                     # never his history
    p.assert_awaited_once()
    return p.call_args.args[0]


@pytest.mark.asyncio
async def test_a_guests_voice_note_is_heard_and_reaches_her_as_their_words(monkeypatch) -> None:
    stt = _FakeSTT("palli, can you hear me?")
    msg, _ = _guest_voice_msg()

    turn = await _guest_turn_for(msg, monkeypatch, stt)

    assert turn.content == "[Dana]: palli, can you hear me?"
    assert turn.metadata.origin == "guest"
    assert turn.metadata.is_voice is True
    assert stt.calls == [b"OggS-fake-voice"]


@pytest.mark.asyncio
async def test_a_guests_caption_stays_and_the_transcript_follows_it(monkeypatch) -> None:
    stt = _FakeSTT("it's about saturday")
    msg, _ = _guest_voice_msg(content="listen to this")

    turn = await _guest_turn_for(msg, monkeypatch, stt)

    assert turn.content == (
        "[Dana]: listen to this\n\n[voice message transcript] it's about saturday")


@pytest.mark.asyncio
async def test_a_guests_note_she_cannot_hear_is_the_honest_marker(monkeypatch) -> None:
    """Never the internal "[VOICE_MESSAGE]" token: the marker says what
    happened, inside their name tag (the Telegram door's fix, now here)."""
    from bots.common.voice_note import TRANSCRIPTION_UNAVAILABLE_CONTENT

    msg, _ = _guest_voice_msg()

    turn = await _guest_turn_for(msg, monkeypatch, _FakeSTT(None))

    assert turn.content == f"[Dana]: {TRANSCRIPTION_UNAVAILABLE_CONTENT}"
    assert "[VOICE_MESSAGE]" not in turn.content


@pytest.mark.asyncio
async def test_a_guests_audio_over_the_wall_is_never_fetched(monkeypatch) -> None:
    """A guest's audio meets the same 20 MB wall on both doors (Telegram's
    bot API puts it on every download): nothing over it is read into memory
    or sent to the paid STT, and the marker says which reason it was."""
    from bots.common.voice_note import GUEST_VOICE_MAX_BYTES, TRANSCRIPTION_TOO_LARGE_CONTENT

    stt = _FakeSTT("never reached")
    msg, att = _guest_voice_msg(size=GUEST_VOICE_MAX_BYTES + 1)

    turn = await _guest_turn_for(msg, monkeypatch, stt)

    assert turn.content == f"[Dana]: {TRANSCRIPTION_TOO_LARGE_CONTENT}"
    att.read.assert_not_awaited()
    assert stt.calls == []


@pytest.mark.asyncio
async def test_the_room_read_gets_a_guests_words_not_the_token(monkeypatch) -> None:
    """The order that caused 10-12 17:20: the room read (answer / wait / stay
    quiet) must judge the guest's WORDS, so the note is heard before it."""
    import discord
    import voice.deepgram_client as dg
    from bots.discord.client import PalliDiscordClient

    monkeypatch.setattr(dg, "deepgram_client", _FakeSTT("palli, can you hear me?"))
    client = PalliDiscordClient(bot_user_id=99999, owner_id=1, intents=discord.Intents.default())

    async def owner_did():
        return 777
    client._local_owner_discord_id = owner_did
    client._room_pass.guest_message = AsyncMock()
    msg, _ = _guest_voice_msg()

    await client.on_message(msg)

    read = client._room_pass.guest_message.await_args.args[0]
    assert read.content == "[Dana]: palli, can you hear me?"


class _SlowSTT:
    """Counts how many hearings run at once."""

    def __init__(self):
        self.now = 0
        self.most = 0
        self.calls = 0

    async def transcribe_audio(self, data, *, content_type):
        import asyncio
        self.calls += 1
        self.now += 1
        self.most = max(self.most, self.now)
        await asyncio.sleep(0.02)
        self.now -= 1
        return "hi"


@pytest.mark.asyncio
async def test_guests_voice_notes_are_heard_a_few_at_a_time(monkeypatch) -> None:
    """A resource floor like the guest queue (Fable's review, 10-12): many
    guests' notes at once never start that many paid STT calls at once."""
    import asyncio
    import discord
    import bots.common.voice_note as vn
    import voice.deepgram_client as dg
    from bots.discord.client import PalliDiscordClient

    stt = _SlowSTT()
    monkeypatch.setattr(dg, "deepgram_client", stt)
    monkeypatch.setattr(vn, "GUEST_HEARINGS_AT_ONCE", 1)
    client = PalliDiscordClient(bot_user_id=99999, owner_id=1, intents=discord.Intents.default())

    async def owner_did():
        return 777
    client._local_owner_discord_id = owner_did
    client._room_pass.guest_message = AsyncMock()
    msgs = [_guest_voice_msg()[0] for _ in range(3)]

    await asyncio.gather(*(client.on_message(m) for m in msgs))

    assert stt.calls == 3
    assert stt.most == 1


@pytest.mark.asyncio
async def test_a_hearing_that_breaks_still_brings_the_guests_message_in(monkeypatch) -> None:
    """Anything inside the hearing that raises (the voice stack failing to
    load, say) must not cost the guest their message: it arrives, marked —
    the Telegram door's rule (test_a_voice_stack_that_will_not_import_is_the_marker)."""
    import bots.discord.voice_attachment as va
    from bots.common.voice_note import TRANSCRIPTION_UNAVAILABLE_CONTENT

    async def broken(*a, **kw):
        raise RuntimeError("voice stack would not load")
    monkeypatch.setattr(va, "transcribe_bytes", broken)
    msg, _ = _guest_voice_msg()

    turn = await _guest_turn_for(msg, monkeypatch, _FakeSTT("never reached"))

    assert turn.content == f"[Dana]: {TRANSCRIPTION_UNAVAILABLE_CONTENT}"


@pytest.mark.asyncio
async def test_his_own_long_recording_is_still_heard(monkeypatch) -> None:
    """The wall is a guest's resource floor, not his: a long recording he
    sends her is transcribed as before."""
    import discord
    import voice.deepgram_client as dg
    from bots.common.voice_note import GUEST_VOICE_MAX_BYTES
    from bots.discord.client import PalliDiscordClient

    stt = _FakeSTT("the whole meeting")
    monkeypatch.setattr(dg, "deepgram_client", stt)
    client = PalliDiscordClient(bot_user_id=99999, owner_id=1, intents=discord.Intents.default())

    async def owner_did():
        return 12345
    client._local_owner_discord_id = owner_did
    msg, _ = _guest_voice_msg(size=GUEST_VOICE_MAX_BYTES + 1)
    with patch("bots.discord.client.dispatch_turn", new=AsyncMock()), \
         patch("bots.discord.client.record_user_fragment", new=AsyncMock()) as rec:
        await client.on_message(msg)
    assert rec.await_args.args[0].content == "the whole meeting"


@pytest.mark.asyncio
async def test_owner_message_still_takes_his_lane() -> None:
    from bots.discord.client import PalliDiscordClient
    import discord
    client = PalliDiscordClient(bot_user_id=99999, owner_id=1, intents=discord.Intents.default())

    async def owner_did():
        return 12345
    client._local_owner_discord_id = owner_did
    msg = _msg(content="hello", author_id=12345)
    with patch("bots.discord.client.dispatch_turn", new=AsyncMock()) as p, \
         patch("bots.discord.client.record_user_fragment", new=AsyncMock()) as rec:
        await client.on_message(msg)
        p.assert_not_awaited()                       # buffered for the burst, as today
        rec.assert_awaited_once()
    assert len(client._burst_fragments.get(msg.channel.id, [])) == 1


@pytest.mark.asyncio
async def test_fire_burst_waits_for_a_finishing_reply_then_dispatches_a_normal_turn() -> None:
    """The 2026-08-23 02:49 seam: the prior run has made no tool calls and is
    no longer generating (its task is wrapping up). The burst must NOT enter
    the mid-task lane (no judge, no holding line) — it waits for the prior
    task and dispatches the message as a normal turn."""
    import asyncio as _aio
    from bots.discord.client import PalliDiscordClient
    from bots.discord import steering_fork
    from core.agent import steering as _st
    from core.turn.types import TurnResult
    import discord

    client = PalliDiscordClient(bot_user_id=99999, owner_id=1, intents=discord.Intents.default())
    msg = _msg(content="firstly, check your max turns")
    client.get_channel = lambda cid: msg.channel
    sk = f"discord:{msg.channel.id}"
    _st.clear(sk); _st.clear_run_state(sk)   # phase cleared, zero tool calls = the tail of a plain reply

    async def _tail():
        await _aio.sleep(0.2)
    prior = _aio.get_running_loop().create_task(_tail())
    client._inflight_tasks[msg.channel.id] = prior
    client._burst_fragments[msg.channel.id] = [__import__("bots.discord.client", fromlist=["build_turn_from_message"]).build_turn_from_message(msg, bot_user_id=99999, owner_id=1)]
    client._burst_authors[msg.channel.id] = {msg.author.id}
    client._burst_unappended[msg.channel.id] = []
    client._local_owner_discord_id = AsyncMock(return_value=msg.author.id)
    fake_result = TurnResult(responded=True, response_text="max turns is 12", decision=None, reflection_applied=False, delegation_tier_used=0)
    judged = []

    async def fake_judge(turn, **kw):
        judged.append(turn)
        from core.agent.steering_reply import SteerJudgment
        return SteerJudgment(stop_run=False, reply="once the current task is wrapped")

    with patch("bots.discord.client.dispatch_turn", new=AsyncMock(return_value=fake_result)) as p, \
         patch.object(steering_fork, "_flag_on", lambda: True), \
         patch.object(steering_fork, "judge_mid_task", fake_judge), \
         patch("bots.discord.client.judge_supersede", new=AsyncMock(return_value=False)) as sup:
        await client._fire_burst(msg.channel.id)
        assert judged == [], "a finishing plain reply is not work — no mid-task judge"
        sup.assert_not_awaited()
        p.assert_awaited_once()
        assert p.call_args.args[0].content == "firstly, check your max turns"
    assert prior.done()
    msg.channel.send.assert_called_once_with("max turns is 12")
    _st.clear(sk); _st.clear_run_state(sk)


# --- Step 27 Phase B (task 8): the delivery block, relocated verbatim -------

@pytest.mark.asyncio
async def test_deliver_result_plain_cadenced_and_silent() -> None:
    """Correction 9: the largest untested move in the plan. The relocated
    method must send a plain reply through send_turn_reply, fan out a
    multi-beat cadenced reply through send_turn_reply_cadenced, and send
    nothing for an unresponded result."""
    from types import SimpleNamespace
    from bots.discord.client import PalliDiscordClient

    import discord
    client = PalliDiscordClient(bot_user_id=99999, owner_id=1, intents=discord.Intents.default())
    chan = SimpleNamespace(id=555)
    client.get_channel = lambda cid: chan
    with patch("bots.discord.client.send_turn_reply", new=AsyncMock()) as plain, \
         patch("bots.discord.client.send_turn_reply_cadenced", new=AsyncMock()) as cad:
        await client._deliver_result(555, SimpleNamespace(
            responded=True, response_text="hi there", attachments=(), cadenced_reply=None))
        # No burst behind a bare result (a sidecar's): paced as before (2026-10-12).
        plain.assert_awaited_once_with(chan, "hi there", attachments=(), waiting_since=None)
        cad.assert_not_awaited()

        beats = SimpleNamespace(beats=["one", "two"])
        await client._deliver_result(555, SimpleNamespace(
            responded=True, response_text="one two", attachments=(), cadenced_reply=beats))
        cad.assert_awaited_once_with(chan, beats, waiting_since=None)

        plain.reset_mock()
        await client._deliver_result(555, SimpleNamespace(
            responded=False, response_text="", attachments=(), cadenced_reply=None))
        plain.assert_not_awaited()


@pytest.mark.asyncio
async def test_rapid_burst_recollects_file_attachments_from_non_last_fragment(monkeypatch) -> None:
    """DEFECT_RECORD §19 (2026-09-02): the new file bucket rides the coalescer
    the way text_attachments do — a PDF dropped in a NON-last fragment of a
    burst still surfaces on the coalesced turn."""
    import asyncio
    from types import SimpleNamespace

    import discord
    from bots.discord import client as client_mod

    dispatched: list = []

    async def fake_dispatch(turn):
        dispatched.append(turn)
        return SimpleNamespace(responded=True, response_text="ok", attachments=(), cadenced_reply=None)

    monkeypatch.setattr(client_mod, "dispatch_turn", fake_dispatch)
    monkeypatch.setattr(client_mod, "record_user_fragment", lambda *a, **k: _async_none())
    monkeypatch.setattr(client_mod, "send_turn_reply", AsyncMock())
    monkeypatch.setattr(client_mod, "send_turn_reply_cadenced", AsyncMock())

    client = client_mod.PalliDiscordClient(owner_id=1, intents=discord.Intents.default(), bot_user_id=42)
    _the_author_is_the_owner(client)
    monkeypatch.setattr(client, "get_channel", lambda cid: _channel())
    _reset_clock()
    client._coalescer._clock = _clock

    file_msg = _msg(content="")
    file_att = MagicMock()
    file_att.content_type = "application/pdf"
    file_att.filename = "scan.pdf"
    file_att.url = "https://cdn.example.com/scan.pdf"
    file_att.size = 4242
    file_msg.attachments = [file_att]
    text_msg = _msg(content="here's the scan")
    text_msg.channel = file_msg.channel

    await client.on_message(file_msg)
    await client.on_message(text_msg)
    _advance_clock(3.5)
    for _ in range(50):
        await asyncio.sleep(0.02)
        if dispatched:
            break

    assert len(dispatched) == 1
    coalesced = dispatched[0]
    assert [r.filename for r in coalesced.metadata.file_attachments] == ["scan.pdf"]
    assert coalesced.metadata.text_attachments == ()


@pytest.mark.asyncio
async def test_an_unresolved_owner_id_makes_the_author_a_guest() -> None:
    """Week 2 task 3 (gap §5.1.5): when the owner's Discord id cannot be
    resolved — LOCAL_OWNER_DISCORD_USER_ID never set, a DB hiccup — nobody in
    the room can be proved to be him, so nobody is. The message takes the
    GUEST path: one turn dispatched with THEIR id and origin 'guest' (a plain
    reply, no tools, none of his memory), never his lane. Before this, an
    unresolved id fell through to the owner turn with the full tool belt.
    """
    import discord
    from bots.discord.client import PalliDiscordClient
    from core.owner_gate import is_guest_turn
    from core.turn.types import TurnResult

    client = PalliDiscordClient(
        bot_user_id=99999, owner_id=1, intents=discord.Intents.default(),
    )

    async def unresolved_owner_did():
        return None

    client._local_owner_discord_id = unresolved_owner_did
    msg = _msg(content="hello", author_id=12345)
    msg.author.display_name = "Dana"
    client.get_channel = lambda cid: msg.channel

    result = TurnResult(
        responded=True, response_text="hi there", decision=None,
        reflection_applied=False, delegation_tier_used=0,
    )
    with patch("bots.discord.client.dispatch_turn",
               new=AsyncMock(return_value=result)) as disp, \
         patch("bots.discord.client.send_turn_reply", new=AsyncMock()) as send, \
         patch("bots.discord.client.record_user_fragment", new=AsyncMock()) as rec:
        await client.on_message(msg)

    disp.assert_awaited_once()
    turn = disp.call_args.args[0]
    assert is_guest_turn(turn) is True
    assert turn.owner_id == 12345
    assert turn.content.startswith("[Dana]:")
    send.assert_awaited_once()
    rec.assert_not_awaited()          # never recorded into his history
    assert client._inflight_tasks == {}   # never his burst/steer lane


@pytest.mark.asyncio
async def test_a_collaborator_still_takes_the_guest_path(tmp_path, monkeypatch) -> None:
    """Batch 8, task 1 (B8-1): naming someone a collaborator changes only how
    much of the room's work their turn reads (dispatch resolves that from
    their own id). It never moves them onto the owner's lane: their message
    is still a guest turn under THEIR id, never the owner's history, never
    the owner's burst or steer lane."""
    import discord
    from bots.discord.client import PalliDiscordClient
    from core import collaborators
    from core.owner_gate import is_guest_turn
    from core.turn.types import TurnResult

    monkeypatch.setenv("PALLI_COLLABORATORS_PATH", str(tmp_path / "collaborators.json"))
    collaborators.add_collaborator("discord", 12345, name="Sam")
    client = PalliDiscordClient(bot_user_id=99999, owner_id=1, intents=discord.Intents.default())

    async def owner_did():
        return 777

    client._local_owner_discord_id = owner_did
    msg = _msg(content="what are you two working on?", author_id=12345)
    msg.author.display_name = "Sam"
    client.get_channel = lambda cid: msg.channel
    result = TurnResult(responded=True, response_text="the disk", decision=None,
                        reflection_applied=False, delegation_tier_used=0)
    with patch("bots.discord.client.dispatch_turn", new=AsyncMock(return_value=result)) as disp, \
         patch("bots.discord.client.send_turn_reply", new=AsyncMock()), \
         patch("bots.discord.client.record_user_fragment", new=AsyncMock()) as rec:
        await client.on_message(msg)

    turn = disp.call_args.args[0]
    assert is_guest_turn(turn) is True and turn.owner_id == 12345
    assert turn.content.startswith("[Sam]:")
    rec.assert_not_awaited()
    assert client._inflight_tasks == {}
