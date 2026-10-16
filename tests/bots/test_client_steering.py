"""Client-side steering fork (spec §4b, §5.1, §5.5, §5.6, §8)."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@dataclass
class FakeTurn:
    surface: str = "discord_text"
    owner_id: int = 1
    content: str = "actually check the other file first"
    metadata: Any = None
    images: Any = None


class Recorder:
    """Stands in for the client's collaborators."""

    def __init__(self):
        self.sent = []
        self.dispatched = []
        self.cancelled = False

    async def send(self, text):
        self.sent.append(text)
        return True

    async def dispatch(self, turn):
        self.dispatched.append(turn)


def test_flag_off_keeps_the_legacy_cancel_path(monkeypatch):
    from bots.discord import steering_fork

    monkeypatch.setattr(steering_fork, "_flag_on", lambda: False)
    rec = Recorder()
    handled = asyncio.run(steering_fork.handle_mid_task(
        FakeTurn(), session_key="s", is_owner=True,
        send=rec.send, run_in_flight=True))
    assert handled is False          # caller falls through to cancel
    assert rec.sent == []


def test_owner_message_is_enqueued_with_her_reply(monkeypatch):
    from bots.discord import steering_fork
    from core.agent import steering, steering_reply

    steering.clear("s")
    monkeypatch.setattr(steering_fork, "_flag_on", lambda: True)

    async def fake_judge(turn, **kw):
        return steering_reply.SteerJudgment(stop_run=False, reply="good call")

    monkeypatch.setattr(steering_fork, "judge_mid_task", fake_judge)
    rec = Recorder()
    handled = asyncio.run(steering_fork.handle_mid_task(
        FakeTurn(), session_key="s", is_owner=True,
        send=rec.send, run_in_flight=True))

    assert handled is True
    assert rec.sent == ["good call"]
    queued = steering.peek("s")
    assert len(queued) == 1 and queued[0].replied is True
    steering.clear("s")


def test_hold_enqueues_without_sending(monkeypatch):
    from bots.discord import steering_fork
    from core.agent import steering, steering_reply

    steering.clear("s")
    monkeypatch.setattr(steering_fork, "_flag_on", lambda: True)

    async def fake_judge(turn, **kw):
        return steering_reply.SteerJudgment(stop_run=False, reply=None)

    monkeypatch.setattr(steering_fork, "judge_mid_task", fake_judge)
    rec = Recorder()
    asyncio.run(steering_fork.handle_mid_task(
        FakeTurn(), session_key="s", is_owner=True,
        send=rec.send, run_in_flight=True))

    assert rec.sent == []
    assert steering.peek("s")[0].replied is False
    steering.clear("s")


def test_non_owner_never_enters_the_lane(monkeypatch):
    """Spec §5.1: steering injects into a run holding owner tool privileges."""
    from bots.discord import steering_fork
    from core.agent import steering

    steering.clear("s")
    monkeypatch.setattr(steering_fork, "_flag_on", lambda: True)
    rec = Recorder()
    handled = asyncio.run(steering_fork.handle_mid_task(
        FakeTurn(), session_key="s", is_owner=False,
        send=rec.send, run_in_flight=True))

    assert handled is False
    assert steering.peek("s") == []


def test_send_failure_leaves_the_bundle_unreplied(monkeypatch):
    """Spec §5.6: words that never reached him are never recorded as said."""
    from bots.discord import steering_fork
    from core.agent import steering, steering_reply

    steering.clear("s")
    monkeypatch.setattr(steering_fork, "_flag_on", lambda: True)

    async def fake_judge(turn, **kw):
        return steering_reply.SteerJudgment(stop_run=False, reply="hi")

    async def failing_send(text):
        raise RuntimeError("discord hiccup")

    monkeypatch.setattr(steering_fork, "judge_mid_task", fake_judge)
    asyncio.run(steering_fork.handle_mid_task(
        FakeTurn(), session_key="s", is_owner=True,
        send=failing_send, run_in_flight=True))

    q = steering.peek("s")
    assert len(q) == 1 and q[0].replied is False
    steering.clear("s")


def test_attachment_bundle_falls_back_to_a_normal_turn(monkeypatch):
    """Spec §5.2: vision needs full-turn recall."""
    from bots.discord import steering_fork
    from core.agent import steering

    steering.clear("s")
    monkeypatch.setattr(steering_fork, "_flag_on", lambda: True)
    rec = Recorder()
    handled = asyncio.run(steering_fork.handle_mid_task(
        FakeTurn(images=["img"]), session_key="s", is_owner=True,
        send=rec.send, run_in_flight=True))

    assert handled is False
    assert steering.peek("s") == []


def test_command_stop_is_recognised_without_an_llm():
    from bots.discord import steering_fork

    assert steering_fork.is_stop_command("stop") is True
    assert steering_fork.is_stop_command("  /stop ") is True
    assert steering_fork.is_stop_command("STOP") is True
    assert steering_fork.is_stop_command("stop the scan and check logs") is False
    assert steering_fork.is_stop_command("") is False


def test_flood_cap_forces_hold_after_the_limit(monkeypatch):
    """Spec §5.5: a sanctioned deterministic budget, not a meaning decision."""
    from bots.discord import steering_fork
    from core.agent import steering, steering_reply

    steering.clear("s")
    steering_fork.reset_reply_budget("s")
    monkeypatch.setattr(steering_fork, "_flag_on", lambda: True)

    async def fake_judge(turn, **kw):
        return steering_reply.SteerJudgment(stop_run=False, reply="sure")

    monkeypatch.setattr(steering_fork, "judge_mid_task", fake_judge)
    rec = Recorder()
    for _ in range(steering_fork.MAX_INTERIM_REPLIES + 2):
        asyncio.run(steering_fork.handle_mid_task(
            FakeTurn(), session_key="s", is_owner=True,
            send=rec.send, run_in_flight=True))

    assert len(rec.sent) == steering_fork.MAX_INTERIM_REPLIES
    steering.clear("s")
    steering_fork.reset_reply_budget("s")


def test_post_run_sweep_dispatches_unanswered_bundles_in_order(tmp_path):
    """Spec §4f: whatever she never answered becomes a real turn once the run
    is over, oldest first. The replied bundle is never re-dispatched — since
    the 2026-07-28 custody fix it is TRANSCRIBED instead of dropped (see the
    incident tests below). ``sessions_root`` is pinned to tmp_path: with the
    default root this test would write into the REAL data/agent_sessions/
    (it did, once — the step-07 ambient-environment disease class)."""
    from bots.discord import steering_fork
    from core.agent import steering

    steering.clear("s")
    rec = Recorder()
    steering.enqueue("s", steering.SteerBundle(
        turn=FakeTurn(content="first thing"), enqueued_at=1.0, replied=False))
    steering.enqueue("s", steering.SteerBundle(
        turn=FakeTurn(content="second thing"), enqueued_at=2.0, replied=False))
    steering.enqueue("s", steering.SteerBundle(
        turn=FakeTurn(content="already answered"), enqueued_at=3.0,
        reply_text="yep", replied=True))

    asyncio.run(steering_fork.sweep_after_run(
        "s", dispatch=rec.dispatch, sessions_root=tmp_path))

    assert [t.content for t in rec.dispatched] == ["first thing", "second thing"]
    assert steering.has_pending("s") is False
    text = (tmp_path / "s.jsonl").read_text(encoding="utf-8")
    assert "already answered" in text and "yep" in text


def test_enqueue_notices_the_moment_and_persists_her_sent_reply(monkeypatch):
    """Spec §6: the steered path bypasses dispatch, where sense A lives —
    without an explicit notice her present would be blind to mid-task
    messages. Her judged reply persists as an assistant row on SUCCESSFUL
    send. The USER side needs no extra persist here: record_user_fragment's
    DB half already wrote each fragment row at arrival (spec §4e/§6) — a
    second write would duplicate his rows."""
    from bots.discord import steering_fork
    from core.agent import steering, steering_reply

    steering.clear("s")
    steering_fork.reset_reply_budget("s")
    monkeypatch.setattr(steering_fork, "_flag_on", lambda: True)
    noticed, persisted = [], []

    async def fake_judge(turn, **kw):
        return steering_reply.SteerJudgment(stop_run=False, reply="sure thing")

    def fake_notice(kind, **kw):
        noticed.append(kind)

    async def fake_persist_assistant(*, turn, response_text):
        persisted.append(("assistant", response_text))

    monkeypatch.setattr(steering_fork, "judge_mid_task", fake_judge)
    monkeypatch.setattr(steering_fork, "_notice", fake_notice)
    monkeypatch.setattr(steering_fork, "_persist_assistant", fake_persist_assistant)

    rec = Recorder()
    asyncio.run(steering_fork.handle_mid_task(
        FakeTurn(content="one more thing"), session_key="s", is_owner=True,
        send=rec.send, run_in_flight=True))

    assert "owner_message" in noticed
    assert persisted == [("assistant", "sure thing")]
    steering.clear("s")
    steering_fork.reset_reply_budget("s")


def test_hold_persists_no_assistant_row(monkeypatch):
    """Spec §6's contrapositive: no send, no assistant row."""
    from bots.discord import steering_fork
    from core.agent import steering, steering_reply

    steering.clear("s")
    monkeypatch.setattr(steering_fork, "_flag_on", lambda: True)
    persisted = []

    async def fake_judge(turn, **kw):
        return steering_reply.SteerJudgment(stop_run=False, reply=None)

    async def fake_persist_assistant(*, turn, response_text):
        persisted.append(response_text)

    monkeypatch.setattr(steering_fork, "judge_mid_task", fake_judge)
    monkeypatch.setattr(steering_fork, "_persist_assistant", fake_persist_assistant)
    rec = Recorder()
    asyncio.run(steering_fork.handle_mid_task(
        FakeTurn(), session_key="s", is_owner=True,
        send=rec.send, run_in_flight=True))

    assert persisted == []
    steering.clear("s")


def test_voice_turns_never_enter_the_lane(monkeypatch):
    """Voice is a non-goal of this slice: a voice-marked turn falls back to
    today's path so its transcript custody stays exactly as it was."""
    from bots.discord import steering_fork
    from core.agent import steering

    @dataclass
    class VoiceMd:
        is_voice: bool = True
        text_attachments: Any = None

    steering.clear("s")
    monkeypatch.setattr(steering_fork, "_flag_on", lambda: True)
    rec = Recorder()
    handled = asyncio.run(steering_fork.handle_mid_task(
        FakeTurn(metadata=VoiceMd()), session_key="s", is_owner=True,
        send=rec.send, run_in_flight=True))

    assert handled is False
    assert steering.peek("s") == []


def test_fallback_dispatch_strips_prerecorded_only_for_undrained_bundles():
    """Spec §4e custody, fallback edition: an UNDRAINED bundle has no
    transcript entry (its arrival append was skipped in the steer lane), so
    its fallback turn must lose user_side_prerecorded and be appended +
    persisted by the normal dispatch path. A DRAINED bundle's words are
    already on the record — the flag stays, nothing doubles."""
    from bots.discord import steering_fork
    from core.agent import steering

    @dataclass
    class FakeMd:
        user_side_prerecorded: bool = True
        is_voice: bool = False

    rec = Recorder()
    steering.clear("s")
    steering.enqueue("s", steering.SteerBundle(
        turn=FakeTurn(content="undrained", metadata=FakeMd()),
        enqueued_at=1.0, replied=False, drained=False))
    steering.enqueue("s", steering.SteerBundle(
        turn=FakeTurn(content="drained", metadata=FakeMd()),
        enqueued_at=2.0, replied=False, drained=True))

    asyncio.run(steering_fork.sweep_after_run("s", dispatch=rec.dispatch))

    by_content = {t.content: t for t in rec.dispatched}
    assert by_content["undrained"].metadata.user_side_prerecorded is False
    assert by_content["drained"].metadata.user_side_prerecorded is True


def test_deadline_falls_back_when_a_run_never_drains(monkeypatch):
    """Spec §4g: a wedged run must not hold his message hostage."""
    from bots.discord import steering_fork
    from core.agent import steering, steering_reply

    steering.clear("s")
    monkeypatch.setattr(steering_fork, "_flag_on", lambda: True)
    monkeypatch.setattr(steering_fork, "DEADLINE_SECONDS", 0.1)

    async def fake_judge(turn, **kw):
        return steering_reply.SteerJudgment(stop_run=False, reply=None)

    monkeypatch.setattr(steering_fork, "judge_mid_task", fake_judge)
    rec = Recorder()

    async def _run():
        await steering_fork.handle_mid_task(
            FakeTurn(content="are you stuck"), session_key="s", is_owner=True,
            send=rec.send, run_in_flight=True, dispatch=rec.dispatch)
        await asyncio.sleep(0.25)

    asyncio.run(_run())

    assert [t.content for t in rec.dispatched] == ["are you stuck"]
    assert steering.has_pending("s") is False
    steering.clear("s")


# --- Client wiring (bots/discord/client.py) --------------------------------
# These drive the real PalliDiscordClient handlers with mock messages, the
# same harness style as test_discord_client.py. They pin the wiring decisions
# the fork itself cannot see: who gets cancelled, when the queue is torn
# down, and that the steer lane is gated on the AUTHOR's Discord id (the
# Turn.owner_id field is the configured app owner on every message, so it
# can never distinguish authors — spec §5.1 needs the raw author id).

OWNER_DISCORD_ID = 555


def _steer_client(monkeypatch):
    import discord
    from bots.discord.client import PalliDiscordClient
    from bots.discord import steering_fork

    client = PalliDiscordClient(
        bot_user_id=99999, owner_id=1, intents=discord.Intents.default())
    monkeypatch.setattr(steering_fork, "_flag_on", lambda: True)

    async def _owner_did():
        return OWNER_DISCORD_ID

    monkeypatch.setattr(client, "_local_owner_discord_id", _owner_did)
    monkeypatch.setattr(client, "get_channel", lambda cid: _mock_channel(cid))
    return client


def _mock_channel(cid=67890):
    ch = MagicMock()
    ch.id = cid
    ch.send = AsyncMock()
    return ch


def _mock_msg(*, content, author_id, channel_id=67890):
    msg = MagicMock()
    msg.author = MagicMock()
    msg.author.bot = False
    msg.author.id = author_id
    msg.author.name = "user"
    msg.content = content
    msg.attachments = []
    msg.mentions = []
    msg.channel = _mock_channel(channel_id)
    return msg


async def _idle_prior():
    return asyncio.get_running_loop().create_task(asyncio.sleep(30))


@pytest.mark.asyncio
async def test_owner_message_mid_run_does_not_cancel(monkeypatch):
    """Spec §4b: flag on + owner author + run in flight -> no barge-in cancel;
    the fragment buffers for the fork instead."""
    from core.agent import steering

    steering.clear("discord:67890")
    # Phase-owned routing (2026-08-04): the deferred lane needs a run that is
    # actually working — a bare in-flight task no longer qualifies.
    steering.clear_run_state("discord:67890")
    steering.set_run_phase("discord:67890", "generating")
    steering.note_tool_calls("discord:67890", 1)
    client = _steer_client(monkeypatch)
    prior = await _idle_prior()
    client._inflight_tasks[67890] = prior

    with patch("bots.discord.client.record_user_fragment", new=AsyncMock()) as rec:
        await client.on_message(
            _mock_msg(content="also grab the error count", author_id=OWNER_DISCORD_ID))

    assert not prior.cancelled()
    assert client._inflight_tasks.get(67890) is prior
    assert len(client._burst_fragments.get(67890, [])) == 1
    assert client._burst_authors.get(67890) == {OWNER_DISCORD_ID}
    # Spec §4e: a steer-lane fragment skips the arrival transcript append
    # (the boundary drain writes it instead) and is tracked for repair.
    assert rec.await_args.kwargs.get("transcript") is False
    assert len(client._burst_unappended.get(67890, [])) == 1
    prior.cancel()
    steering.clear_run_state("discord:67890")


@pytest.mark.asyncio
async def test_stop_command_cancels_and_clears_queue(monkeypatch):
    """Spec §3 lane 1 + §5.3: 'stop' cancels instantly, no model in the path,
    and queued steers die with the run."""
    from core.agent import steering

    steering.clear("discord:67890")
    client = _steer_client(monkeypatch)
    prior = await _idle_prior()
    client._inflight_tasks[67890] = prior
    steering.enqueue("discord:67890", steering.SteerBundle(
        turn=FakeTurn(content="pending steer"), enqueued_at=0.0))

    with patch("bots.discord.client.record_user_fragment", new=AsyncMock()):
        await client.on_message(
            _mock_msg(content="stop", author_id=OWNER_DISCORD_ID))

    assert prior.cancelling() or prior.cancelled() or prior.done()
    assert client._inflight_tasks.get(67890) is None
    assert steering.has_pending("discord:67890") is False


@pytest.mark.asyncio
async def test_non_owner_author_mid_run_is_a_guest_and_never_cancels_him(monkeypatch):
    """Spec §5.1 said a different author mid-run got the barge-in cancel.
    Superseded by the owner gate (item 7a, 2026-08-19, owner's call): a
    non-owner author is a GUEST — their message becomes one plain guest turn
    (no tools, none of his bands), answered in-channel, and it never touches
    his lane: his in-flight reply keeps running, nothing is recorded into
    his history, the burst buffer stays his."""
    from core.agent import steering
    from core.turn.types import TurnResult

    steering.clear("discord:67890")
    client = _steer_client(monkeypatch)
    prior = await _idle_prior()
    client._inflight_tasks[67890] = prior
    msg = _mock_msg(content="hey palli do something", author_id=777)
    client.get_channel = lambda cid: msg.channel
    guest_reply = TurnResult(responded=True, response_text="hi — I'm PaLLi.", decision=None,
                             reflection_applied=False, delegation_tier_used=0)

    with patch("bots.discord.client.record_user_fragment", new=AsyncMock()) as rec, \
         patch("bots.discord.client.dispatch_turn", new=AsyncMock(return_value=guest_reply)) as disp:
        await client.on_message(msg)

    assert not (prior.cancelling() or prior.cancelled())
    assert client._inflight_tasks.get(67890) is prior
    rec.assert_not_awaited()
    disp.assert_awaited_once()
    assert disp.call_args.args[0].metadata.origin == "guest"
    assert client._burst_unappended.get(67890) is None
    prior.cancel()


@pytest.mark.asyncio
async def test_fire_burst_routes_mid_run_burst_into_the_fork(monkeypatch):
    """Spec §4b at fire time: run still alive -> handle_mid_task owns the
    bundle; no dispatch, no cancel, the run keeps its registration."""
    from bots.discord import steering_fork
    from core.agent import steering

    steering.clear_run_state("discord:67890")
    steering.set_run_phase("discord:67890", "generating")
    steering.note_tool_calls("discord:67890", 1)
    client = _steer_client(monkeypatch)
    prior = await _idle_prior()
    client._inflight_tasks[67890] = prior
    taken = AsyncMock(return_value=True)
    monkeypatch.setattr(steering_fork, "handle_mid_task", taken)

    with patch("bots.discord.client.record_user_fragment", new=AsyncMock()), \
         patch("bots.discord.client.dispatch_turn", new=AsyncMock()) as disp:
        await client.on_message(
            _mock_msg(content="one more thing", author_id=OWNER_DISCORD_ID))
        await client._fire_burst(67890)

    taken.assert_awaited_once()
    assert taken.await_args.kwargs["is_owner"] is True
    assert taken.await_args.kwargs["session_key"] == "discord:67890"
    disp.assert_not_awaited()
    assert not prior.cancelled()
    assert client._inflight_tasks.get(67890) is prior
    prior.cancel()
    steering.clear_run_state("discord:67890")


@pytest.mark.asyncio
async def test_fire_burst_fork_decline_cancels_then_dispatches(monkeypatch):
    """The fork's contract: returns False -> 'the caller keeps today's
    behavior' — cancel the prior run FIRST, then dispatch normally. Without
    the cancel, two runs would interleave writes on one session (the runner's
    per-key sequencing law)."""
    from bots.discord import steering_fork
    from core.agent import steering
    from core.turn.types import TurnResult

    steering.clear("discord:67890")
    steering.clear_run_state("discord:67890")
    steering.set_run_phase("discord:67890", "generating")
    steering.note_tool_calls("discord:67890", 1)
    client = _steer_client(monkeypatch)
    prior = await _idle_prior()
    client._inflight_tasks[67890] = prior
    monkeypatch.setattr(
        steering_fork, "handle_mid_task", AsyncMock(return_value=False))
    steering.enqueue("discord:67890", steering.SteerBundle(
        turn=FakeTurn(content="stale steer"), enqueued_at=0.0))
    fake_result = TurnResult(
        responded=False, response_text="", decision=None,
        reflection_applied=False, delegation_tier_used=0,
    )

    with patch("bots.discord.client.record_user_fragment", new=AsyncMock()), \
         patch("bots.discord.client.dispatch_turn",
               new=AsyncMock(return_value=fake_result)) as disp:
        await client.on_message(
            _mock_msg(content="no wait wrong one", author_id=OWNER_DISCORD_ID))
        await client._fire_burst(67890)

    for _ in range(3):        # let the loop deliver the cancellation
        await asyncio.sleep(0)
    assert prior.cancelled()
    disp.assert_awaited_once()
    assert steering.has_pending("discord:67890") is False
    steering.clear_run_state("discord:67890")


@pytest.mark.asyncio
async def test_record_user_fragment_transcript_false_skips_the_session_file(
        tmp_path):
    """Spec §4e at the recording seam: transcript=False writes nothing to the
    session file but still fires the DB half."""
    from bots.discord.recording import record_user_fragment

    persisted = AsyncMock()
    with patch("bots.discord.recording._persist_user_side_with_content",
               new=persisted):
        await record_user_fragment(
            FakeTurn(content="steered words"), sessions_root=tmp_path,
            transcript=False)
        await asyncio.sleep(0)      # let the detached DB task run
    assert list(tmp_path.iterdir()) == []
    persisted.assert_awaited_once()

    with patch("bots.discord.recording._persist_user_side_with_content",
               new=AsyncMock()):
        await record_user_fragment(
            FakeTurn(content="normal words"), sessions_root=tmp_path)
    assert list(tmp_path.iterdir()) != []


@pytest.mark.asyncio
async def test_fork_decline_repairs_skipped_transcript_appends(
        monkeypatch, tmp_path):
    """Spec §4e custody on the decline path: a fragment whose arrival append
    was skipped must reach the session file exactly once before the burst
    dispatches as a normal prerecorded turn."""
    import core.agent.session as sess_mod
    from bots.discord import steering_fork
    from core.agent import steering
    from core.turn.types import TurnResult

    steering.clear("discord:67890")
    steering.clear_run_state("discord:67890")
    steering.set_run_phase("discord:67890", "generating")
    steering.note_tool_calls("discord:67890", 1)
    client = _steer_client(monkeypatch)
    client._sessions_root = tmp_path
    prior = await _idle_prior()
    client._inflight_tasks[67890] = prior
    monkeypatch.setattr(
        steering_fork, "handle_mid_task", AsyncMock(return_value=False))
    fake_result = TurnResult(
        responded=False, response_text="", decision=None,
        reflection_applied=False, delegation_tier_used=0,
    )

    with patch("bots.discord.recording._persist_user_side_with_content",
               new=AsyncMock()), \
         patch("bots.discord.client.dispatch_turn",
               new=AsyncMock(return_value=fake_result)):
        await client.on_message(
            _mock_msg(content="scrap that idea", author_id=OWNER_DISCORD_ID))
        assert list(tmp_path.iterdir()) == []      # arrival append skipped
        await client._fire_burst(67890)

    reopened = await sess_mod.Session.open_or_create(
        "discord:67890", root=tmp_path)
    texts = [b.text for m in reopened.messages for b in m.content
             if getattr(b, "type", "") == "text"]
    assert texts.count("scrap that idea") == 1
    prior.cancel()
    steering.clear_run_state("discord:67890")


# --- Arrival routing by run phase (phase-owned routing, 2026-08-04) ---------
# Only a run that has actually spoken (phase "generating") or actually worked
# (>=1 tool call) earns the deferred lane at arrival. A run still in PREP has
# shown him nothing — cancelling and recoalescing is strictly better (the
# 15:48 flash-lane incident: the owner's correction arrived 6s before the
# reply model was even invoked and got a 700-token one-liner).


@pytest.mark.asyncio
async def test_prep_phase_arrival_cancels_instead_of_steering(monkeypatch):
    """Phase A: a message during another turn's PREP must barge-in cancel
    (rejoin via recoalesce), never take the steer lane."""
    from core.agent import steering

    key = "discord:67890"
    steering.clear(key)
    steering.clear_run_state(key)
    steering.set_run_phase(key, "prep")
    client = _steer_client(monkeypatch)
    prior = await _idle_prior()
    client._inflight_tasks[67890] = prior

    with patch("bots.discord.client.record_user_fragment", new=AsyncMock()) as rec:
        await client.on_message(
            _mock_msg(content="anyways. can you check the roadmap?",
                      author_id=OWNER_DISCORD_ID))

    for _ in range(3):        # let the loop deliver the cancellation
        await asyncio.sleep(0)
    assert prior.cancelled()                      # legacy barge-in fired
    assert client._inflight_tasks.get(67890) is None
    assert steering.peek(key) == []               # nothing entered the lane
    # Not steer lane -> arrival transcript append stays on (normal custody).
    assert rec.await_args.kwargs.get("transcript") is True
    assert client._burst_unappended.get(67890) is None
    steering.clear_run_state(key)


@pytest.mark.asyncio
async def test_generating_phase_arrival_defers_no_cancel(monkeypatch):
    """Phase B at arrival: buffer + transcript-skip, NO cancel — the settle
    fork decides supersede-vs-finish at fire time."""
    from core.agent import steering

    key = "discord:67890"
    steering.clear(key)
    steering.clear_run_state(key)
    steering.set_run_phase(key, "generating")
    client = _steer_client(monkeypatch)
    prior = await _idle_prior()
    client._inflight_tasks[67890] = prior

    with patch("bots.discord.client.record_user_fragment", new=AsyncMock()) as rec:
        await client.on_message(
            _mock_msg(content="also — one more thing",
                      author_id=OWNER_DISCORD_ID))

    assert not prior.cancelled()
    assert client._inflight_tasks.get(67890) is prior
    assert rec.await_args.kwargs.get("transcript") is False
    assert len(client._burst_unappended.get(67890, [])) == 1
    prior.cancel()
    steering.clear_run_state(key)


@pytest.mark.asyncio
async def test_working_run_arrival_keeps_steer_lane(monkeypatch):
    """Phase C unchanged: a run with >=1 real tool call steers exactly as
    today."""
    from core.agent import steering

    key = "discord:67890"
    steering.clear(key)
    steering.clear_run_state(key)
    steering.set_run_phase(key, "generating")
    steering.note_tool_calls(key, 1)
    client = _steer_client(monkeypatch)
    prior = await _idle_prior()
    client._inflight_tasks[67890] = prior

    with patch("bots.discord.client.record_user_fragment", new=AsyncMock()):
        await client.on_message(
            _mock_msg(content="how's it going in there?",
                      author_id=OWNER_DISCORD_ID))

    assert not prior.cancelled()
    assert client._inflight_tasks.get(67890) is prior
    prior.cancel()
    steering.clear_run_state(key)


# --- Settle-time supersede fork (phase-owned routing, 2026-08-04) -----------
# A conversational run that is mid-GENERATION (zero tool calls) is not work —
# steering's one-liners are the wrong tool. One judged binary at fire time:
# does the new message change what she is saying right now?


@pytest.mark.asyncio
async def test_fire_burst_supersede_true_cancels_and_dispatches(
        monkeypatch, tmp_path):
    """Phase B, judge says supersede: in-flight reply cancelled, burst
    dispatches as one normal combined turn."""
    from core.agent import steering
    from core.turn.types import TurnResult

    key = "discord:67890"
    steering.clear(key)
    steering.clear_run_state(key)
    steering.set_run_phase(key, "generating")
    client = _steer_client(monkeypatch)
    client._sessions_root = tmp_path
    prior = await _idle_prior()
    client._inflight_tasks[67890] = prior

    async def fake_supersede(turn, *, session_key):
        return True

    monkeypatch.setattr("bots.discord.client.judge_supersede", fake_supersede)
    fake_result = TurnResult(
        responded=False, response_text="", decision=None,
        reflection_applied=False, delegation_tier_used=0,
    )

    with patch("bots.discord.client.record_user_fragment", new=AsyncMock()), \
         patch("bots.discord.client.dispatch_turn",
               new=AsyncMock(return_value=fake_result)) as disp:
        await client.on_message(
            _mock_msg(content="wait — make it about the gist lane",
                      author_id=OWNER_DISCORD_ID))
        await client._fire_burst(67890)

    for _ in range(3):        # let the loop deliver the cancellation
        await asyncio.sleep(0)
    assert prior.cancelled()
    disp.assert_awaited_once()
    assert "gist lane" in str(disp.await_args.args[0].content)
    assert steering.peek(key) == []
    steering.clear_run_state(key)


@pytest.mark.asyncio
async def test_fire_burst_supersede_false_enqueues_for_sweep(
        monkeypatch, tmp_path):
    """Phase B, judge says finish: no cancel, bundle queued UNREPLIED so the
    run-end sweep dispatches the guaranteed follow-up turn."""
    from core.agent import steering

    key = "discord:67890"
    steering.clear(key)
    steering.clear_run_state(key)
    steering.set_run_phase(key, "generating")
    client = _steer_client(monkeypatch)
    client._sessions_root = tmp_path
    prior = await _idle_prior()
    client._inflight_tasks[67890] = prior

    async def fake_supersede(turn, *, session_key):
        return False

    monkeypatch.setattr("bots.discord.client.judge_supersede", fake_supersede)
    armed = []
    monkeypatch.setattr(
        "bots.discord.steering_fork.arm_deadline",
        lambda *a, **kw: armed.append(a))

    with patch("bots.discord.client.record_user_fragment", new=AsyncMock()), \
         patch("bots.discord.client.dispatch_turn", new=AsyncMock()) as disp:
        await client.on_message(
            _mock_msg(content="also, one more thing",
                      author_id=OWNER_DISCORD_ID))
        await client._fire_burst(67890)

    assert not prior.cancelled()
    disp.assert_not_awaited()
    q = steering.peek(key)
    assert len(q) == 1 and q[0].replied is False
    assert "one more thing" in str(q[0].turn.content)
    assert len(armed) == 1          # watchdog armed for the wedged-run case
    prior.cancel()
    steering.clear(key)
    steering.clear_run_state(key)


# --- Regression: the 2026-08-04 incidents ------------------------------------
# 15:48 — three messages in 10s during another turn's PREP were routed to the
# steer lane (built for mid-WORK arrivals) and got 700-token flash one-liners;
# the third was the owner's flag-trap correction, closed out by "Good to know.
# Thanks for telling me." 13:35 — a message arriving mid-run was claimed by
# neither regime and sat unanswered until the owner re-sent it 25s later.


@pytest.mark.asyncio
async def test_regression_1548_burst_prep_phase_one_considered_turn(
        monkeypatch, tmp_path):
    """15:48: messages arriving during PREP barge-in cancel (rejoin); the
    re-coalesced burst dispatches as ONE normal considered turn. No steer
    queue, no flash one-liners, no out-of-order posts."""
    from core.agent import steering
    from core.turn.types import TurnResult

    key = "discord:67890"
    steering.clear(key)
    steering.clear_run_state(key)
    steering.set_run_phase(key, "prep")
    client = _steer_client(monkeypatch)
    client._sessions_root = tmp_path
    prior = await _idle_prior()
    client._inflight_tasks[67890] = prior

    fake_result = TurnResult(
        responded=False, response_text="", decision=None,
        reflection_applied=False, delegation_tier_used=0,
    )

    with patch("bots.discord.client.record_user_fragment", new=AsyncMock()), \
         patch("bots.discord.client.dispatch_turn",
               new=AsyncMock(return_value=fake_result)) as disp:
        for text in [
            "Hey yo",
            "claude instructed me to tell you this btw",
            "The flag-trap thing from July? There was never any trap.",
        ]:
            await client.on_message(
                _mock_msg(content=text, author_id=OWNER_DISCORD_ID))
        for _ in range(3):        # let the loop deliver the cancellation
            await asyncio.sleep(0)
        assert prior.cancelled()          # the prep-phase turn was rejoined
        assert steering.peek(key) == []   # nothing entered the steer lane
        await client._fire_burst(67890)

    disp.assert_awaited_once()
    combined = str(disp.await_args.args[0].content)
    assert "Hey yo" in combined and "flag-trap" in combined
    steering.clear_run_state(key)


@pytest.mark.asyncio
async def test_regression_1335_no_message_waits_for_a_resend(
        monkeypatch, tmp_path):
    """13:35: a mid-generation arrival whose judge FAILS must still be
    answered without any re-send — fail-open enqueues unreplied and the
    run-end sweep dispatches it. The REAL judge_supersede runs; only the
    LLM underneath it is broken (the reasoning-burn shape)."""
    import ai.llm_client as llm_mod
    from bots.discord import steering_fork
    from core.agent import steering

    key = "discord:67890"
    steering.clear(key)
    steering.clear_run_state(key)
    steering.set_run_phase(key, "generating")
    client = _steer_client(monkeypatch)
    client._sessions_root = tmp_path
    prior = await _idle_prior()
    client._inflight_tasks[67890] = prior

    async def burned(*a, **kw):
        raise RuntimeError("burned 700/700 on reasoning")

    monkeypatch.setattr(llm_mod.llm, "chat_completion", burned)
    armed = []
    monkeypatch.setattr(
        "bots.discord.steering_fork.arm_deadline",
        lambda *a, **kw: armed.append(a))

    with patch("bots.discord.client.record_user_fragment", new=AsyncMock()), \
         patch("bots.discord.client.dispatch_turn", new=AsyncMock()) as disp:
        await client.on_message(_mock_msg(
            content=("anyways. can you check your current project directory "
                     "and see whats next on the roadmap for ya/"),
            author_id=OWNER_DISCORD_ID))
        await client._fire_burst(67890)

    disp.assert_not_awaited()         # the in-flight reply kept its life
    assert not prior.cancelled()
    q = steering.peek(key)
    assert len(q) == 1 and q[0].replied is False

    rec = Recorder()
    n = await steering_fork.sweep_after_run(
        key, dispatch=rec.dispatch, sessions_root=tmp_path)
    assert n == 1
    assert any("roadmap" in str(t.content) for t in rec.dispatched)
    prior.cancel()
    steering.clear_run_state(key)


# --- Custody repair (2026-07-28 incident: the api-key loss) -----------------
# A bundle that got a judged interim reply but whose run never reached
# another model boundary was DROPPED by the sweep — its user fragments and
# her interim reply existed on Discord and in the DB, and never reached the
# session transcript she thinks with. She acknowledged an API key and then
# had no memory of it. The sweep's old docstring ("replied bundles are
# already recorded by the runner") is false exactly here: the runner records
# only at the NEXT boundary, and a run whose final LLM call was already in
# flight never has one. The §5.4 fast-run race funnels into this same sweep,
# so one transcription point heals both entrances.


def test_post_run_sweep_transcribes_replied_undrained_bundles(tmp_path):
    from bots.discord import steering_fork
    from core.agent import steering

    steering.clear("s")
    rec = Recorder()
    steering.enqueue("s", steering.SteerBundle(
        turn=FakeTurn(content="here's the api key\nKEY-XYZ"), enqueued_at=1.0,
        reply_text="Got it, I'll take a look once the print finishes.",
        replied=True, drained=False))

    asyncio.run(steering_fork.sweep_after_run(
        "s", dispatch=rec.dispatch, sessions_root=tmp_path))

    assert rec.dispatched == []      # she already answered — never re-answer
    text = (tmp_path / "s.jsonl").read_text(encoding="utf-8")
    assert "here's the api key" in text
    assert "once the print finishes" in text
    # his words precede her interim reply — the boundary drain's own order
    assert text.index("api key") < text.index("print finishes")
    steering.clear("s")


def test_full_turn_verdict_sends_holding_line_and_sweep_dispatches(
        monkeypatch, tmp_path):
    """Phase-owned routing (2026-08-04): a WEIGHTY mid-work message gets an
    honest holding line now and a GUARANTEED full turn at run end — never
    closed out by a one-liner (the 15:48 flag-trap incident)."""
    from bots.discord import steering_fork
    from core.agent import steering
    from core.agent.steering_reply import SteerJudgment

    key = "discord:42"
    steering.clear(key)
    steering_fork.reset_reply_budget(key)
    monkeypatch.setattr(steering_fork, "_flag_on", lambda: True)

    async def fake_judge(turn, *, session_key, **kw):
        return SteerJudgment(
            stop_run=False,
            reply="give me a minute — I want to answer that properly",
            full_turn=True)

    sent, persisted, dispatched = [], [], []

    async def fake_send(text):
        sent.append(text)

    async def fake_persist(*, turn, response_text):
        persisted.append(response_text)

    async def fake_dispatch(turn):
        dispatched.append(str(getattr(turn, "content", "")))

    async def fake_transcribe(session_key, bundle, sessions_root):
        bundle.drained = True
        return True

    monkeypatch.setattr(steering_fork, "judge_mid_task", fake_judge)
    monkeypatch.setattr(steering_fork, "_persist_assistant", fake_persist)
    monkeypatch.setattr(steering_fork, "_transcribe_bundle", fake_transcribe)

    turn = FakeTurn(content="that flag-trap thing — there was never any trap")
    taken = asyncio.run(steering_fork.handle_mid_task(
        turn, session_key=key, is_owner=True, send=fake_send,
        run_in_flight=True, dispatch=fake_dispatch, sessions_root=tmp_path))

    assert taken is True
    assert sent == ["give me a minute — I want to answer that properly"]
    assert persisted == ["give me a minute — I want to answer that properly"]
    q = steering.peek(key)
    assert len(q) == 1 and q[0].replied is True and q[0].needs_full_turn is True

    n = asyncio.run(steering_fork.sweep_after_run(
        key, dispatch=fake_dispatch, sessions_root=tmp_path))
    assert n == 1
    assert dispatched and "flag-trap" in dispatched[0]
    steering_fork.reset_reply_budget(key)


def test_plain_replied_bundle_still_not_redispatched(monkeypatch, tmp_path):
    """Pin: replied-without-full_turn keeps today's behavior (record only)."""
    from bots.discord import steering_fork
    from core.agent import steering
    from core.agent.steering import SteerBundle

    dispatched = []

    async def fake_dispatch(turn):
        dispatched.append(turn)

    async def fake_transcribe(session_key, bundle, sessions_root):
        bundle.drained = True
        return True

    monkeypatch.setattr(steering_fork, "_transcribe_bundle", fake_transcribe)

    key = "discord:43"
    steering.clear(key)
    b = SteerBundle(turn=FakeTurn(content="quick q"), enqueued_at=0.0)
    b.replied, b.reply_text = True, "on it"
    steering.enqueue(key, b)
    n = asyncio.run(steering_fork.sweep_after_run(
        key, dispatch=fake_dispatch, sessions_root=tmp_path))
    assert n == 0 and dispatched == []


def test_deadline_fallback_transcribes_the_interim_reply_first(tmp_path, monkeypatch):
    """The deadline cousin: a wedged run's replied bundle falls back to a
    normal turn — her interim reply must reach the transcript BEFORE the
    re-dispatch, and the user side must not double (prerecorded stays set
    because the transcription already wrote his words)."""
    from bots.discord import steering_fork
    from core.agent import steering, steering_reply

    @dataclass
    class FakeMd:
        user_side_prerecorded: bool = True
        is_voice: bool = False

    steering.clear("s")
    steering_fork.reset_reply_budget("s")
    monkeypatch.setattr(steering_fork, "_flag_on", lambda: True)
    monkeypatch.setattr(steering_fork, "DEADLINE_SECONDS", 0.1)

    async def fake_judge(turn, **kw):
        # A holding line that promised her full self (full_turn): the real
        # answer is still owed, so the fallback re-dispatches. An in-hand
        # answer would not be (2026-08-28, see the in-hand test below).
        return steering_reply.SteerJudgment(stop_run=False, reply="hold on, mid-thing",
                                            full_turn=True)

    async def fake_persist_assistant(*, turn, response_text):
        return None

    monkeypatch.setattr(steering_fork, "judge_mid_task", fake_judge)
    monkeypatch.setattr(steering_fork, "_persist_assistant", fake_persist_assistant)

    rec = Recorder()

    async def _run():
        await steering_fork.handle_mid_task(
            FakeTurn(content="you stuck?", metadata=FakeMd()), session_key="s",
            is_owner=True, send=rec.send, run_in_flight=True,
            dispatch=rec.dispatch, sessions_root=tmp_path)
        await asyncio.sleep(0.3)

    asyncio.run(_run())

    text = (tmp_path / "s.jsonl").read_text(encoding="utf-8")
    assert "you stuck?" in text and "hold on, mid-thing" in text
    assert [t.content for t in rec.dispatched] == ["you stuck?"]
    # transcription marked it drained -> fallback keeps prerecorded, no double
    assert rec.dispatched[0].metadata.user_side_prerecorded is True
    steering.clear("s")


@pytest.mark.asyncio
async def test_supersede_branch_respects_flag_off(monkeypatch, tmp_path):
    """Spec §10: flag OFF is byte-identical legacy. Even when a run appears
    between arrival and settle (a reminder-origin run starting on the same
    session), the supersede judge must NOT fire — the burst takes the
    decline-cancel contract exactly as pre-13-15."""
    from core.agent import steering
    from bots.discord import steering_fork

    key = "discord:67890"
    steering.clear(key)
    steering.clear_run_state(key)
    steering.set_run_phase(key, "generating")
    client = _steer_client(monkeypatch)
    client._sessions_root = tmp_path
    monkeypatch.setattr(steering_fork, "_flag_on", lambda: False)

    judge = AsyncMock(return_value=True)
    monkeypatch.setattr("bots.discord.client.judge_supersede", judge)

    with patch("bots.discord.client.record_user_fragment", new=AsyncMock()), \
         patch("bots.discord.client.dispatch_turn", new=AsyncMock()) as disp:
        # No run in flight at arrival — the message simply buffers.
        await client.on_message(
            _mock_msg(content="hey, quick thought", author_id=OWNER_DISCORD_ID))
        # A run appears before settle (reminder fire on the same session).
        prior = await _idle_prior()
        client._inflight_tasks[67890] = prior
        await client._fire_burst(67890)

    judge.assert_not_awaited()                    # flag off: no new machinery
    for _ in range(3):
        await asyncio.sleep(0)
    assert prior.cancelled()                      # legacy decline-cancel
    disp.assert_awaited()                         # burst dispatched normally
    assert steering.peek(key) == []
    steering.clear_run_state(key)


# ── the barge-in seam (2026-08-23 02:49) ───────────────────────────────────
# A message that arrived one second into generation of a 3-second reply was
# routed, at fire time, into the mid-task lane because the run's phase had
# already been cleared but its task was still wrapping up: the judge was
# told she was "still working", she invented "the current task", and the
# request itself was never answered. Three laws:
#   1. at fire time, a run with ZERO tool calls that is no longer generating
#      is not work — wait for it, then a normal turn; never a steer;
#   2. a bundle remembers whether it was steering real work;
#   3. a replied bundle that was never steering real work is re-dispatched
#      as a full turn by the sweep — a holding line cannot answer a request.

def test_route_mid_run_decides_by_work_and_phase():
    from bots.discord import steering_fork as sf
    assert sf.route_mid_run(working=False, phase="generating", prior_done=False) == "supersede_check"
    assert sf.route_mid_run(working=False, phase="", prior_done=False) == "wait_then_normal"
    assert sf.route_mid_run(working=False, phase="prep", prior_done=False) == "wait_then_normal"
    assert sf.route_mid_run(working=True, phase="generating", prior_done=False) == "steer"
    assert sf.route_mid_run(working=True, phase="", prior_done=False) == "steer"
    assert sf.route_mid_run(working=False, phase="", prior_done=True) == "normal"
    assert sf.route_mid_run(working=True, phase="generating", prior_done=True) == "normal"


def test_bundle_remembers_whether_it_steered_real_work(monkeypatch):
    from bots.discord import steering_fork
    from core.agent import steering, steering_reply

    steering.clear("s"); steering.clear_run_state("s")
    monkeypatch.setattr(steering_fork, "_flag_on", lambda: True)

    async def fake_judge(turn, **kw):
        return steering_reply.SteerJudgment(stop_run=False, reply="I'll check once this lands.")

    monkeypatch.setattr(steering_fork, "judge_mid_task", fake_judge)
    rec = Recorder()
    asyncio.run(steering_fork.handle_mid_task(FakeTurn(), session_key="s", is_owner=True, send=rec.send, run_in_flight=True))
    assert steering.peek("s")[0].working_at_enqueue is False
    steering.clear("s")
    steering.note_tool_calls("s", 2)   # a working run
    asyncio.run(steering_fork.handle_mid_task(FakeTurn(), session_key="s", is_owner=True, send=rec.send, run_in_flight=True))
    assert steering.peek("s")[0].working_at_enqueue is True
    steering.clear("s"); steering.clear_run_state("s")


def test_sweep_redispatches_a_replied_bundle_that_never_steered_real_work(tmp_path):
    from bots.discord import steering_fork
    from core.agent import steering

    steering.clear("s")
    rec = Recorder()
    steering.enqueue("s", steering.SteerBundle(turn=FakeTurn(content="check your max turns please"), enqueued_at=1.0,
                                               reply_text="I'll check once the current task is wrapped.", replied=True,
                                               working_at_enqueue=False))
    steering.enqueue("s", steering.SteerBundle(turn=FakeTurn(content="also grab the file"), enqueued_at=2.0,
                                               reply_text="on that file now", replied=True, working_at_enqueue=True))
    asyncio.run(steering_fork.sweep_after_run("s", dispatch=rec.dispatch, sessions_root=tmp_path))
    # the no-work holding line is re-dispatched as a real turn; the real-work one is transcribed only
    assert [t.content for t in rec.dispatched] == ["check your max turns please"]
    text = (tmp_path / "s.jsonl").read_text(encoding="utf-8")
    assert "also grab the file" in text and "on that file now" in text
    assert steering.has_pending("s") is False


def test_stop_mode_is_cooperative_only_when_duplex_on_and_working():
    from bots.discord.steering_fork import stop_mode
    assert stop_mode(duplex_on=True, working=True) == "cooperative"
    assert stop_mode(duplex_on=True, working=False) == "hard"      # a plain reply: cancel as today
    assert stop_mode(duplex_on=False, working=True) == "hard"
    assert stop_mode(duplex_on=False, working=False) == "hard"


# --- 2026-08-28: the deadline watchdog under long tool calls ---------------

def _deadline_harness(monkeypatch, *, reply=None, full_turn=False, seconds=0.05):
    from bots.discord import steering_fork
    from core.agent import steering, steering_reply
    steering.clear("s")
    steering.clear_run_state("s")
    steering_fork.reset_reply_budget("s")
    monkeypatch.setattr(steering_fork, "_flag_on", lambda: True)
    monkeypatch.setattr(steering_fork, "DEADLINE_SECONDS", seconds)

    async def fake_judge(turn, **kw):
        return steering_reply.SteerJudgment(stop_run=False, reply=reply, full_turn=full_turn)

    async def fake_persist_assistant(*, turn, response_text):
        return None

    monkeypatch.setattr(steering_fork, "judge_mid_task", fake_judge)
    monkeypatch.setattr(steering_fork, "_persist_assistant", fake_persist_assistant)
    return steering_fork, steering, Recorder()


def test_deadline_waits_while_the_run_is_alive_then_falls_back_when_it_is_gone(monkeypatch):
    """A 7-minute tool call is not a wedged run (step 27 made long silent
    work normal). While the run is alive the bundle stays queued for the
    drain seam — the watchdog must not write into an open round-trip nor
    dispatch a competing turn (08-28 13:03). Once the run is gone without a
    sweep, the fallback fires as before."""
    steering_fork, steering, rec = _deadline_harness(monkeypatch)
    steering.set_run_phase("s", "generating")
    steering.note_tool_calls("s", 1)

    async def _run():
        await steering_fork.handle_mid_task(
            FakeTurn(content="are you stuck"), session_key="s", is_owner=True,
            send=rec.send, run_in_flight=True, dispatch=rec.dispatch)
        await asyncio.sleep(0.25)
        assert rec.dispatched == []
        assert steering.has_pending("s") is True
        steering.clear_run_state("s")            # the run ended without a sweep
        await asyncio.sleep(0.25)

    asyncio.run(_run())
    assert [t.content for t in rec.dispatched] == ["are you stuck"]
    assert steering.has_pending("s") is False
    steering.clear("s")


def test_deadline_ceiling_falls_back_even_if_the_run_never_ends(monkeypatch):
    """Bounded: a run that stays 'alive' past the ceiling is treated as
    wedged after all."""
    steering_fork, steering, rec = _deadline_harness(monkeypatch)
    monkeypatch.setattr(steering_fork, "DEADLINE_MAX_REARMS", 2)
    steering.set_run_phase("s", "generating")
    steering.note_tool_calls("s", 1)

    async def _run():
        await steering_fork.handle_mid_task(
            FakeTurn(content="are you stuck"), session_key="s", is_owner=True,
            send=rec.send, run_in_flight=True, dispatch=rec.dispatch)
        await asyncio.sleep(0.5)

    asyncio.run(_run())
    assert [t.content for t in rec.dispatched] == ["are you stuck"]
    steering.clear("s"); steering.clear_run_state("s")


def test_deadline_fallback_does_not_redispatch_a_bundle_answered_in_hand(tmp_path, monkeypatch):
    """The judge answered him in-hand (no full_turn): nothing is owed. The
    fallback puts the exchange on the record and stops — the sweep's rule,
    which the watchdog ignored (08-28 13:03: a redundant turn on an
    already-answered question)."""
    steering_fork, steering, rec = _deadline_harness(monkeypatch, reply="it's a 9225 fan")
    steering.set_run_phase("s", "generating")
    steering.note_tool_calls("s", 1)              # real work: the in-hand answer counts

    async def _run():
        await steering_fork.handle_mid_task(
            FakeTurn(content="which fan was it?"), session_key="s", is_owner=True,
            send=rec.send, run_in_flight=True, dispatch=rec.dispatch,
            sessions_root=tmp_path)
        steering.clear_run_state("s")             # the run ended without a sweep
        await asyncio.sleep(0.3)

    asyncio.run(_run())
    text = (tmp_path / "s.jsonl").read_text(encoding="utf-8")
    assert "which fan was it?" in text and "it's a 9225 fan" in text
    assert rec.dispatched == []
    assert steering.has_pending("s") is False
    steering.clear("s")


# --- Step 27 Phase B: resume and the honest expiry (task 5) ----------------

def test_route_paused_resume_or_normal():
    from bots.discord.steering_fork import route_paused
    assert route_paused(action="resume") == "resume"
    for a in ("fold_in", "pause", "new_task", "dance"):
        assert route_paused(action=a) == "normal"
    assert route_paused(action="stop") == "stop"          # correction 2: forget it while paused


def test_the_pause_expiry_line_is_gone():
    """The owner, 10-12: no scripted lines in PaLLi's name."""
    from bots.discord import steering_fork as sf
    assert not hasattr(sf, "render_pause_expiry_line")


def test_arm_pause_expiry_lets_her_notice_once_and_clears(monkeypatch):
    import asyncio
    import core.agent.steering as st
    from ai import present_moment as pm
    from bots.discord import steering_fork as sf
    st.set_paused("discord:44", "the adapter")
    spoken, noticed = [], []

    async def speak(**kw):
        spoken.append(kw)

    async def instant(s):
        await asyncio.sleep(0)

    monkeypatch.setattr(pm, "notice", lambda kind, **f: noticed.append((kind, f)))

    async def run():
        await sf.arm_pause_expiry("discord:44", ttl_minutes=120, sleep=instant)
    asyncio.run(run())
    assert st.get_paused("discord:44") is None
    assert spoken == []                                   # no fixed line in her name
    assert noticed == [("task_pause_expired", {"minutes": 120})]
    assert "task_pause_expired" in pm._EVENT_PHRASES


def test_the_doors_arm_the_pause_with_only_what_it_takes():
    """Fable's review (10-12, deferred minor): the expiry no longer speaks, so
    it takes no speaker, owner or channel; both doors call it that way (a
    stray argument would only fail when a pause actually runs out)."""
    import inspect
    from pathlib import Path
    from bots.discord import steering_fork as sf
    assert set(inspect.signature(sf.arm_pause_expiry).parameters) == {"session_key", "ttl_minutes", "sleep"}
    # Telegram's lane moved to bots/common/owner_lane.py (the mobile door, 2026-10-13).
    for door in ("bots/discord/client.py", "bots/common/owner_lane.py"):
        src = Path(door).read_text(encoding="utf-8")
        call = src[src.index("arm_pause_expiry("):][:300]
        call = call[:call.index(")\n") + 1]
        assert "speak=" not in call and "owner_user_id=" not in call and "channel_id=" not in call, door


def test_arm_pause_expiry_does_nothing_if_resumed_first(monkeypatch):
    import asyncio
    import core.agent.steering as st
    from ai import present_moment as pm
    from bots.discord import steering_fork as sf
    st.set_paused("discord:45", "the adapter")
    spoken, noticed = [], []

    async def speak(**kw):
        spoken.append(kw)

    async def fake_sleep(s):
        st.clear_paused("discord:45")       # they said "go on" before the clock ran out
        await asyncio.sleep(0)

    monkeypatch.setattr(pm, "notice", lambda kind, **f: noticed.append((kind, f)))

    async def run():
        await sf.arm_pause_expiry("discord:45", ttl_minutes=1, sleep=fake_sleep)
    asyncio.run(run())
    assert spoken == [] and noticed == []


# --- Step 27 Phase B: the steer lane launches sidecars (task 7) ------------

class TestSidecarLane:
    def _setup(self, monkeypatch, judgment):
        import core.agent.steering as st
        from bots.discord import steering_fork as sf
        st.clear("discord:8"); st.clear_run_state("discord:8")
        sf.reset_reply_budget("discord:8")
        monkeypatch.setattr(sf, "_flag_on", lambda: True)
        async def judge(turn, **kw): return judgment
        monkeypatch.setattr(sf, "judge_mid_task", judge)
        from core.agent import duplex as dx
        monkeypatch.setattr(dx, "duplex_enabled", lambda: True)
        return st, sf

    @staticmethod
    def _turn(text):
        return type("T", (), {"content": text, "surface": "discord_text", "metadata": None})()

    def test_full_turn_launches_a_talk_sidecar_and_hands_the_reply_to_the_bundle(self, monkeypatch, tmp_path):
        import asyncio
        from core.agent.steering_reply import SteerJudgment
        st, sf = self._setup(monkeypatch, SteerJudgment(reply=None, full_turn=True))
        st.set_run_phase("discord:8", "generating")                 # the parent is alive
        sent, launched = [], {}
        async def send(t): sent.append(t)
        async def launch_sidecar(turn, *, kind, on_done):
            launched["kind"] = kind
            async def _fake(): await on_done("the full answer")
            return asyncio.create_task(_fake())
        async def go():
            taken = await sf.handle_mid_task(self._turn("does what I said earlier change anything?"),
                                             session_key="discord:8", is_owner=True, send=send,
                                             run_in_flight=True, dispatch=None, sessions_root=tmp_path,
                                             launch_sidecar=launch_sidecar)
            await asyncio.sleep(0); await asyncio.sleep(0)
            return taken
        assert asyncio.run(go()) is True
        assert launched["kind"] == "talk" and sent == []              # no holding line; the sidecar answers
        [b] = st.peek("discord:8")                                     # parent alive → left for its boundary
        assert b.replied and b.reply_text == "the full answer" and b.sidecar_running is False and b.needs_full_turn is False
        st.clear_run_state("discord:8")

    def test_new_task_launches_a_task_sidecar(self, monkeypatch, tmp_path):
        import asyncio
        from core.agent.steering_reply import SteerJudgment
        st, sf = self._setup(monkeypatch, SteerJudgment(reply=None, action="new_task"))
        launched = {}
        async def send(t): pass
        async def launch_sidecar(turn, *, kind, on_done):
            launched["kind"] = kind
            async def _fake(): await on_done("adapter printed — file's in the channel")
            return asyncio.create_task(_fake())
        asyncio.run(sf.handle_mid_task(self._turn("also, separately, print the adapter"), session_key="discord:8",
                                       is_owner=True, send=send, run_in_flight=True, dispatch=None,
                                       sessions_root=tmp_path, launch_sidecar=launch_sidecar))
        assert launched["kind"] == "task"

    def test_slots_full_falls_back_to_the_ordinary_queue(self, monkeypatch, tmp_path):
        import asyncio
        from core.agent.steering_reply import SteerJudgment
        st, sf = self._setup(monkeypatch, SteerJudgment(reply=None, action="new_task"))
        async def send(t): pass
        async def launch_sidecar(turn, *, kind, on_done): return None      # full house
        taken = asyncio.run(sf.handle_mid_task(self._turn("also do X"), session_key="discord:8", is_owner=True,
                                               send=send, run_in_flight=True, dispatch=None,
                                               sessions_root=tmp_path, launch_sidecar=launch_sidecar))
        assert taken is True
        [b] = st.peek("discord:8")
        assert b.sidecar_running is False and b.replied is False           # drains into the run as today

    def test_pause_action_requests_pause_and_enqueues(self, monkeypatch, tmp_path):
        import asyncio
        from core.agent.steering_reply import SteerJudgment
        st, sf = self._setup(monkeypatch, SteerJudgment(reply=None, action="pause"))
        async def send(t): pass
        assert asyncio.run(sf.handle_mid_task(self._turn("hold on a sec"), session_key="discord:8", is_owner=True,
                                              send=send, run_in_flight=True, dispatch=None,
                                              sessions_root=tmp_path)) is True
        assert st.pause_requested("discord:8") is True
        st.clear_run_state("discord:8")

    def test_sweep_leaves_a_running_sidecar_bundle_alone(self, monkeypatch, tmp_path):
        import asyncio
        import core.agent.steering as st
        from bots.discord import steering_fork as sf
        st.clear("discord:8")
        b = st.SteerBundle(turn=self._turn("x"), enqueued_at=0.0, sidecar_running=True)
        st.enqueue("discord:8", b)
        dispatched = []
        async def dispatch(t): dispatched.append(t)
        n = asyncio.run(sf.sweep_after_run("discord:8", dispatch=dispatch, sessions_root=tmp_path))
        assert n == 0 and dispatched == []
        assert st.peek("discord:8") == [b]                                  # still queued for on_done
        st.clear("discord:8")

    def test_watchdog_leaves_a_running_sidecar_bundle_alone(self, monkeypatch, tmp_path):
        """Correction 3: the third hand. A bundle a sidecar is answering is
        not the watchdog's to transcribe or re-dispatch."""
        import asyncio
        import core.agent.steering as st
        from bots.discord import steering_fork as sf
        st.clear("discord:8"); st.clear_run_state("discord:8")
        monkeypatch.setattr(sf, "DEADLINE_SECONDS", 0.05)
        b = st.SteerBundle(turn=self._turn("x"), enqueued_at=0.0, sidecar_running=True)
        st.enqueue("discord:8", b)
        dispatched = []
        async def dispatch(t): dispatched.append(t)
        async def go():
            sf._arm_deadline("discord:8", b, dispatch, sessions_root=tmp_path)
            await asyncio.sleep(0.2)
        asyncio.run(go())
        assert dispatched == [] and st.peek("discord:8") == [b]
        st.clear("discord:8")

    def test_on_done_after_the_parent_ended_transcribes_and_dequeues(self, monkeypatch, tmp_path):
        """Correction 3: the parent's writer is gone — on_done puts the
        exchange on the record itself (the sweep's helper), once."""
        import asyncio
        from core.agent.steering_reply import SteerJudgment
        st, sf = self._setup(monkeypatch, SteerJudgment(reply=None, full_turn=True))
        st.clear_run_state("discord:8")                                    # no parent alive
        captured = {}
        async def send(t): pass
        async def launch_sidecar(turn, *, kind, on_done):
            captured["on_done"] = on_done
            async def _hold(): await asyncio.sleep(3600)
            return asyncio.create_task(_hold())
        async def go():
            await sf.handle_mid_task(self._turn("what's the wifi again?"), session_key="discord:8",
                                     is_owner=True, send=send, run_in_flight=True, dispatch=None,
                                     sessions_root=tmp_path, launch_sidecar=launch_sidecar)
            [b] = st.peek("discord:8"); assert b.sidecar_running
            await captured["on_done"]("on the fridge")
        asyncio.run(go())
        assert st.peek("discord:8") == []
        text = (tmp_path / "discord_8.jsonl").read_text(encoding="utf-8")
        assert "what's the wifi again?" in text and "on the fridge" in text


# --- Step 27 Phase B: the client tracks sidecars; stop reaches task sidecars (task 8) ---

def test_sidecar_keys_for_lists_only_live_task_children_by_default():
    import asyncio
    from bots.discord.steering_fork import sidecar_keys_for
    async def go():
        done = asyncio.create_task(asyncio.sleep(0)); await done
        live_task = asyncio.create_task(asyncio.sleep(10))
        live_talk = asyncio.create_task(asyncio.sleep(10))
        live = {"discord:1#sidecar-1": ("task", done),
                "discord:1#sidecar-2": ("task", live_task),
                "discord:1#sidecar-3": ("talk", live_talk),
                "discord:2#sidecar-1": ("task", live_task)}
        keys = sidecar_keys_for("discord:1", live)                  # correction 8: task only
        all_keys = sidecar_keys_for("discord:1", live, kind=None)
        live_task.cancel(); live_talk.cancel()
        return keys, all_keys
    keys, all_keys = asyncio.run(go())
    assert keys == ["discord:1#sidecar-2"]
    assert all_keys == ["discord:1#sidecar-2", "discord:1#sidecar-3"]


@pytest.mark.asyncio
async def test_mid_task_replies_and_side_conversations_log_their_wait(monkeypatch, caplog):
    """10-12: a quick reply sent mid-task and a side conversation's reply are
    credited with the wait behind them and logged, each with its kind."""
    import logging
    from types import SimpleNamespace
    from bots.discord import steering_fork
    from core.agent import sidecar as _sc
    from core.agent import steering
    from core.turn.types import TurnResult

    steering.clear_run_state("discord:67890")
    steering.set_run_phase("discord:67890", "generating")
    steering.note_tool_calls("discord:67890", 1)
    client = _steer_client(monkeypatch)
    prior = await _idle_prior()
    client._inflight_tasks[67890] = prior

    def fake_run_sidecar(*, parent_key, turn, kind, dispatch, deliver, sessions_root, on_done):
        result = TurnResult(responded=True, response_text="side answer", decision=None,
                            reflection_applied=False, delegation_tier_used=0)
        return SimpleNamespace(key="discord:67890#sidecar-1",
                               task=asyncio.get_running_loop().create_task(deliver(result)))

    monkeypatch.setattr(_sc, "run_sidecar", fake_run_sidecar)

    async def fake_mid_task(turn, *, send, launch_sidecar, **kw):
        await send("quick answer")
        handle = await launch_sidecar(turn, kind="talk", on_done=None)
        await handle.task
        return True

    monkeypatch.setattr(steering_fork, "handle_mid_task", fake_mid_task)
    with patch("bots.discord.client.record_user_fragment", new=AsyncMock()), \
         patch("bots.discord.client.dispatch_turn", new=AsyncMock()), \
         patch("bots.discord.client.send_turn_reply", new=AsyncMock(return_value=True)) as send, \
         caplog.at_level(logging.INFO, logger="bots.common.reply_wait"):
        await client.on_message(_mock_msg(content="how's it going", author_id=OWNER_DISCORD_ID))
        await client._fire_burst(67890)

    assert all(c.kwargs.get("waiting_since") is not None for c in send.await_args_list)
    waits = [r.getMessage() for r in caplog.records if r.getMessage().startswith("reply wait: ")]
    assert any(w.endswith("(discord 67890, mid-task reply)") for w in waits), waits
    assert any(w.endswith("(discord 67890, side conversation)") for w in waits), waits
    prior.cancel()
    steering.clear_run_state("discord:67890")
