"""The four shared pieces move to bots/common (telegram door, task 1).

The steering fork, the burst coalescer, the guest budget and the voice-note
transcriber were written inside bots/discord because Discord was the only
door. None of them is about Discord: they are about a person typing at her.
A second door that re-implemented any of them would drift from it within a
week — and the guest budget re-implemented would be TWO budgets, which is
not a budget at all, because the money is one account.

The Discord modules keep their old paths. The shim makes the old name BE
the moved module (``sys.modules[__name__] = _m``), not a copy of its
attributes: the client reads ``steering_fork._flag_on`` through the module
object, and tests monkeypatch it there. Two module objects would mean the
patch lands on one and the client reads the other.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest


# ── the moves are aliases, not copies ───────────────────────────────────

def test_the_steering_fork_is_the_moved_module_itself():
    import bots.common.steering
    import bots.discord.steering_fork

    assert bots.discord.steering_fork is bots.common.steering


def test_the_burst_coalescer_is_the_moved_module_itself():
    import bots.common.burst
    import bots.discord.burst

    assert bots.discord.burst is bots.common.burst


def test_a_patch_through_the_old_path_is_seen_through_the_new_one(monkeypatch):
    """The whole reason the shim aliases the module object."""
    import bots.common.steering as new
    import bots.discord.steering_fork as old

    monkeypatch.setattr(old, "_flag_on", lambda: "patched")
    assert new._flag_on() == "patched"


def test_the_moved_modules_keep_their_public_names():
    import bots.common.burst as burst
    import bots.common.steering as steering

    assert steering.MAX_INTERIM_REPLIES == 8
    assert steering.DEFAULT_SESSIONS_ROOT.name == "agent_sessions"
    assert hasattr(steering, "handle_mid_task") and hasattr(steering, "route_mid_run")
    assert hasattr(burst, "BurstCoalescer")


def test_the_client_still_imports_the_old_paths():
    """Nothing else moves in this task (the plan's rule). Anchored at the
    repo root, not the CWD, so it means the same from anywhere."""
    from pathlib import Path

    repo = Path(__file__).resolve().parents[2]
    text = (repo / "bots" / "discord" / "client.py").read_text(encoding="utf-8")
    assert "from bots.discord.burst import BurstCoalescer" in text
    assert "from bots.discord import steering_fork" in text


# ── the voice note ──────────────────────────────────────────────────────

def test_the_markers_are_one_object_on_both_paths():
    import bots.common.voice_note as vn
    import bots.discord.voice_attachment as va

    assert va.VOICE_PLACEHOLDER_CONTENT is vn.VOICE_PLACEHOLDER_CONTENT
    assert va.TRANSCRIPTION_UNAVAILABLE_CONTENT is vn.TRANSCRIPTION_UNAVAILABLE_CONTENT
    assert vn.VOICE_PLACEHOLDER_CONTENT == "[VOICE_MESSAGE]"


def _stt(transcript):
    stt = MagicMock()
    stt.transcribe_audio = AsyncMock(return_value=transcript)
    return stt


@pytest.mark.asyncio
async def test_transcribe_bytes_takes_audio_from_any_door():
    """Telegram hands over bytes it downloaded itself; Discord hands over
    bytes it read off an attachment. The transcriber sees only bytes."""
    import bots.common.voice_note as vn

    stt = _stt("hey palli, you there?")
    out = await vn.transcribe_bytes(b"OggS-fake", mime="audio/ogg", stt=stt)

    assert out == "hey palli, you there?"
    _, kwargs = stt.transcribe_audio.await_args
    assert kwargs.get("content_type") == "audio/ogg"


@pytest.mark.asyncio
async def test_transcribe_bytes_maps_video_ogg_to_audio_ogg():
    import bots.common.voice_note as vn

    stt = _stt("yo")
    assert await vn.transcribe_bytes(b"x", mime="video/ogg", stt=stt) == "yo"
    _, kwargs = stt.transcribe_audio.await_args
    assert kwargs.get("content_type") == "audio/ogg"


@pytest.mark.asyncio
async def test_transcribe_bytes_is_none_on_empty_audio_or_empty_transcript():
    import bots.common.voice_note as vn

    stt = _stt("never called")
    assert await vn.transcribe_bytes(b"", mime="audio/ogg", stt=stt) is None
    stt.transcribe_audio.assert_not_awaited()
    assert await vn.transcribe_bytes(b"x", mime="audio/ogg", stt=_stt(None)) is None
    assert await vn.transcribe_bytes(b"x", mime="audio/ogg", stt=_stt("  ")) is None


@pytest.mark.asyncio
async def test_transcribe_bytes_swallows_an_stt_failure():
    import bots.common.voice_note as vn

    stt = MagicMock()
    stt.transcribe_audio = AsyncMock(side_effect=RuntimeError("deepgram down"))
    assert await vn.transcribe_bytes(b"x", mime="audio/ogg", stt=stt) is None


@pytest.mark.asyncio
async def test_the_discord_module_still_transcribes_an_attachment():
    """The pin tests/bots/test_discord_voice_attachment.py owns."""
    from bots.discord.voice_attachment import transcribe_voice_message

    att = MagicMock()
    att.content_type = "audio/ogg"
    att.read = AsyncMock(return_value=b"OggS-fake")
    msg = MagicMock()
    msg.attachments = [att]

    assert await transcribe_voice_message(msg, stt=_stt("the deploy failed")) == (
        "the deploy failed"
    )


# ── the guest budget ────────────────────────────────────────────────────
# Batch 8, task 2b (ruling T2b-6; rewritten under the kickoff's exception
# (a)): the per-guest rate limit is gone (the owner, 09-30: "Drop the
# limit."), and the in-flight cap is a queue for message turns. ``admit`` /
# ``release`` stay, without the window, for a stranger's commands.

@pytest.fixture
def knobs(monkeypatch):
    from core.config import config

    def _set(*, in_flight=2):
        monkeypatch.setattr(config, "GUEST_MAX_IN_FLIGHT", in_flight, raising=False)

    _set()
    return _set


def test_there_is_no_per_guest_limit_any_more(knobs):
    from bots.common.guest_gate import GuestGate

    knobs(in_flight=99)
    gate = GuestGate()
    admitted = []
    for _ in range(10):
        admitted.append(gate.admit(424242))
        gate.release(424242)

    assert admitted == [True] * 10
    assert not hasattr(gate, "stamps") and not hasattr(GuestGate, "WINDOW_SECONDS")


def test_the_in_flight_cap_is_global_across_guests(knobs):
    from bots.common.guest_gate import GuestGate

    knobs(in_flight=2)
    gate = GuestGate()
    assert gate.admit(301) is True
    assert gate.admit(302) is True
    assert gate.admit(303) is False


def test_release_gives_the_slot_back(knobs):
    from bots.common.guest_gate import GuestGate

    knobs(in_flight=1)
    gate = GuestGate()
    assert gate.admit(301) is True
    assert gate.admit(302) is False
    gate.release(301)
    assert gate.admit(302) is True


def test_release_never_goes_below_zero(knobs):
    from bots.common.guest_gate import GuestGate

    gate = GuestGate()
    gate.release(1)
    gate.release(1)
    assert gate.in_flight == 0


def test_a_refused_command_never_takes_a_slot(knobs):
    from bots.common.guest_gate import GuestGate

    knobs(in_flight=1)
    gate = GuestGate()
    assert gate.admit(1) is True
    assert gate.admit(424242) is False
    assert gate.in_flight == 1


def test_one_budget_across_doors():
    """Two doors, one account: ``shared_guest_gate()`` is the single
    process-wide instance both of them spend from."""
    from bots.common.guest_gate import shared_guest_gate

    assert shared_guest_gate() is shared_guest_gate()


def test_the_at_capacity_line_is_one_short_line():
    from bots.common.guest_gate import GuestGate

    line = GuestGate.AT_CAPACITY_LINE
    assert isinstance(line, str) and line.strip()
    assert len(line.splitlines()) == 1
    assert "limit" in line.lower()


# ── the Discord client over the gate ────────────────────────────────────

def _result(text: str = "hi there"):
    from core.turn.types import TurnResult

    return TurnResult(responded=True, response_text=text, decision=None,
                      reflection_applied=False, delegation_tier_used=0)


def _client():
    import discord
    from bots.discord.client import PalliDiscordClient

    return PalliDiscordClient(
        bot_user_id=99999, owner_id=1, intents=discord.Intents.default(),
    )


def test_a_client_built_directly_gets_its_own_budget(knobs):
    """Every test that constructs a client keeps today's isolation."""
    a, b = _client(), _client()
    assert a._guest_gate is not b._guest_gate


def test_the_client_reads_the_gates_live_state(knobs):
    from bots.common.guest_gate import GuestGate

    gate = GuestGate()
    import discord
    from bots.discord.client import PalliDiscordClient

    client = PalliDiscordClient(
        bot_user_id=99999, owner_id=1, intents=discord.Intents.default(),
        guest_gate=gate,
    )

    assert client._guests_in_flight == 0
    gate.admit(777)
    assert client._guests_in_flight == 1
    for gone in ("_guest_stamps", "_GUEST_WINDOW_SECONDS", "_GUEST_AT_CAPACITY",
                 "_guest_turn_admitted"):
        assert not hasattr(client, gone), gone        # the rate limit went with them


@pytest.mark.asyncio
async def test_the_clients_guest_turn_takes_its_slot_from_the_gate(knobs):
    from unittest.mock import AsyncMock, patch

    from bots.common.guest_gate import GuestGate

    knobs(in_flight=99)
    gate = GuestGate()
    import discord
    from bots.discord.client import PalliDiscordClient

    client = PalliDiscordClient(
        bot_user_id=99999, owner_id=1, intents=discord.Intents.default(),
        guest_gate=gate,
    )
    seen: list[int] = []

    async def dispatch(turn):
        seen.append(gate.in_flight)
        return _result()

    channel = MagicMock()
    channel.id = 67890
    client.get_channel = lambda cid: channel
    msg = MagicMock()
    msg.author = MagicMock()
    msg.author.bot = False
    msg.author.id = 424242
    msg.author.name = msg.author.display_name = "Dana"
    msg.content = "hey"
    msg.attachments = []
    msg.mentions = []
    msg.channel = channel
    with patch("bots.discord.client.dispatch_turn", new=dispatch), \
         patch("bots.discord.client.send_turn_reply", new=AsyncMock()):
        await client._handle_guest_message(msg, channel.id)

    assert seen == [1]                       # the turn ran holding one slot
    assert gate.in_flight == 0               # and gave it back


def test_attaching_the_shared_gate_makes_the_client_spend_from_it(knobs):
    """``start_discord_text_client`` calls ``attach_guest_gate(shared_guest_gate())``,
    so the Telegram door built in task 2 shares ONE cap with Discord. Tested
    through the seam it uses, not by reading the source."""
    from bots.common.guest_gate import shared_guest_gate

    client = _client()
    shared = shared_guest_gate()
    assert client._guest_gate is not shared          # built alone, budgeted alone

    client.attach_guest_gate(shared)

    assert client._guest_gate is shared
    before = shared.in_flight
    admitted = shared.admit(424242)
    try:
        assert admitted is True
        assert client._guests_in_flight == before + 1 == shared.in_flight
    finally:
        # try/finally, because this test spends from the PROCESS-WIDE gate:
        # a failing assertion above must not leave a slot taken for every
        # later test that touches the shared budget.
        if admitted:
            shared.release(424242)


@pytest.mark.asyncio
async def test_a_guest_turn_that_dies_before_dispatch_still_gives_its_slot_back(knobs):
    """The slot is taken by the admission, so EVERY path after it must reach
    the finally. Building the turn is not obviously fallible, which is
    exactly why it must not sit outside: discord.py swallows whatever
    on_message raises, so a slot lost here is lost for the life of the
    process, and at the default cap of 2 two of these shut the guest door
    until a restart."""
    from unittest.mock import AsyncMock, patch

    knobs(in_flight=2)
    channel = MagicMock()
    channel.id = 67890
    channel.send = AsyncMock()

    client = _client()
    client.get_channel = lambda cid: channel

    msg = MagicMock()
    msg.author = MagicMock()
    msg.author.bot = False
    msg.author.id = 424242
    msg.author.name = msg.author.display_name = "Dana"
    msg.content = "hey"
    msg.attachments = []
    msg.mentions = []
    msg.channel = channel

    def _boom(*a, **kw):
        raise RuntimeError("intake blew up before the turn existed")

    with patch("bots.discord.client.build_turn_from_message", _boom), \
         patch("bots.discord.client.dispatch_turn", new=AsyncMock()) as disp, \
         patch("bots.discord.client.send_turn_reply", new=AsyncMock()):
        await client._handle_guest_message(msg, channel.id)

    disp.assert_not_awaited()
    assert client._guests_in_flight == 0

    # And the door is still open afterwards — the real damage of a leak.
    with patch("bots.discord.client.dispatch_turn",
               new=AsyncMock(return_value=_result())) as disp2, \
         patch("bots.discord.client.send_turn_reply", new=AsyncMock()):
        await client._handle_guest_message(msg, channel.id)
    disp2.assert_awaited_once()
    assert client._guests_in_flight == 0


@pytest.mark.asyncio
async def test_two_such_failures_do_not_shut_the_guest_door(knobs):
    """At the default cap of 2 a leaking slot is not a slow drift — it is a
    closed door after two failures."""
    from unittest.mock import AsyncMock, patch

    knobs(in_flight=2)
    channel = MagicMock()
    channel.id = 67890
    channel.send = AsyncMock()
    client = _client()
    client.get_channel = lambda cid: channel

    def _msg(author_id):
        m = MagicMock()
        m.author = MagicMock()
        m.author.bot = False
        m.author.id = author_id
        m.author.name = m.author.display_name = f"guest{author_id}"
        m.content = "hey"
        m.attachments = []
        m.mentions = []
        m.channel = channel
        return m

    def _boom(*a, **kw):
        raise RuntimeError("intake blew up")

    with patch("bots.discord.client.build_turn_from_message", _boom), \
         patch("bots.discord.client.dispatch_turn", new=AsyncMock()), \
         patch("bots.discord.client.send_turn_reply", new=AsyncMock()):
        await client._handle_guest_message(_msg(301), channel.id)
        await client._handle_guest_message(_msg(302), channel.id)

    assert client._guests_in_flight == 0

    with patch("bots.discord.client.dispatch_turn",
               new=AsyncMock(return_value=_result())) as disp, \
         patch("bots.discord.client.send_turn_reply", new=AsyncMock()):
        await client._handle_guest_message(_msg(303), channel.id)
    disp.assert_awaited_once()
