"""The voice-moment predicate (step 32, spec §2): is he in the room with her?

Deterministic, getattr-guarded, never raises: her connected voice client →
its channel → a LIVE listening session (non-empty runtime_session_key) →
HIM among the channel's members. Any miss → None → the text path,
byte-identical to today.

Since 2026-10-13 (the owner: a message for him alone never reaches a group)
the moment also says whether the call is HIM AND HER ALONE
(``alone_with_her``). A friend on the call, or a member who cannot be read,
makes it shared: his presence still counts, but her own words to him go as
text to his private chat (ai/arbiter.py).
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from core.agent import hands_runtime


OWNER_DID = 555001
HER_DID = 900900          # her own account, in the call with him
FRIEND_DID = 123123       # a friend of his on the call (Dana)


@pytest.fixture(autouse=True)
def _clean_client():
    original = hands_runtime.get_client()
    yield
    hands_runtime.register_client(original)


@pytest.fixture()
def seams(monkeypatch):
    state = SimpleNamespace(
        snapshot={"runtime_session_key": "voice:1"},
        refs={"discord_user_id": OWNER_DID},
    )

    monkeypatch.setattr(
        "voice.always_listening.voice_processor.get_session_snapshot",
        lambda channel_id: state.snapshot,
    )

    async def _refs():
        if isinstance(state.refs, Exception):
            raise state.refs
        return state.refs

    monkeypatch.setattr("core.local_mode.get_local_owner_refs", _refs)
    return state


def _client(member_ids=(OWNER_DID, HER_DID), with_vc=True, her_id=HER_DID):
    members = [SimpleNamespace(id=i) for i in member_ids]
    channel = SimpleNamespace(id=777, members=members)
    vc = SimpleNamespace(channel=channel) if with_vc else None
    guild = SimpleNamespace(voice_client=vc)
    user = SimpleNamespace(id=her_id) if her_id is not None else None
    return SimpleNamespace(guilds=[guild], user=user)


def _probe():
    return asyncio.run(hands_runtime.active_voice_moment())


def test_happy_path_returns_the_moment(seams):
    hands_runtime.register_client(_client())
    vm = _probe()
    assert vm == {"voice_channel_id": 777, "runtime_session_key": "voice:1",
                  "alone_with_her": True}


# ── him and her alone, or a shared call (the owner, 10-13) ──────────────

def test_a_friend_on_the_call_makes_it_shared(seams):
    hands_runtime.register_client(_client(member_ids=(OWNER_DID, HER_DID, FRIEND_DID)))
    vm = _probe()
    assert vm is not None and vm["alone_with_her"] is False


def test_her_account_unknown_makes_it_shared(seams):
    """She cannot tell her own account from a friend's: fail closed."""
    hands_runtime.register_client(_client(her_id=None))
    vm = _probe()
    assert vm is not None and vm["alone_with_her"] is False


def test_a_member_who_cannot_be_read_makes_it_shared(seams):
    client = _client()
    client.guilds[0].voice_client.channel.members.append(SimpleNamespace())
    hands_runtime.register_client(client)
    vm = _probe()
    assert vm is not None and vm["alone_with_her"] is False


def test_him_alone_without_her_listed_is_still_alone(seams):
    hands_runtime.register_client(_client(member_ids=(OWNER_DID,)))
    assert _probe()["alone_with_her"] is True


def test_no_client_is_none(seams):
    hands_runtime.register_client(None)
    assert _probe() is None


def test_not_connected_is_none(seams):
    hands_runtime.register_client(_client(with_vc=False))
    assert _probe() is None


def test_no_live_session_is_none(seams):
    seams.snapshot = {}
    hands_runtime.register_client(_client())
    assert _probe() is None


def test_owner_not_in_channel_is_none(seams):
    hands_runtime.register_client(_client(member_ids=(123, 456)))
    assert _probe() is None


def test_refs_failure_is_none_never_raises(seams):
    seams.refs = RuntimeError("refs down")
    hands_runtime.register_client(_client())
    assert _probe() is None
