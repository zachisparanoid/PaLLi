"""The owner gate in voice (item 7a, 2026-08-19): a speaker who is not him
is a guest — the spoken turn carries THEIR id and origin 'guest' (plain
reply, no tools, none of his bands); his own utterances are unchanged."""
from __future__ import annotations

import inspect

from voice import runtime as vr


def test_owner_speaker_is_the_owner_turn():
    refs = {"app_user_id": 1, "palli_user_id": 1, "discord_user_id": 777}
    assert vr._voice_turn_identity(user_id=777, refs=refs) == (1, "user")


def test_other_speaker_is_a_guest_turn():
    refs = {"app_user_id": 1, "palli_user_id": 1, "discord_user_id": 777}
    assert vr._voice_turn_identity(user_id=4242, refs=refs) == (4242, "guest")


def test_unresolved_owner_ref_makes_the_speaker_a_guest():
    """Week 2 task 3 (gap §5.1.5): with no owner discord id to compare
    against, nobody in the channel can be proved to be him — so the spoken
    turn is a GUEST turn (their id, origin 'guest'), not the owner path with
    his shell. It used to return the owner turn for everyone."""
    refs = {"app_user_id": 1, "palli_user_id": 1, "discord_user_id": None}
    assert vr._voice_turn_identity(user_id=4242, refs=refs) == (4242, "guest")


def test_respond_to_message_uses_the_gate():
    src = inspect.getsource(vr.VoiceSessionRuntime._respond_to_message) if hasattr(vr, "VoiceSessionRuntime") else ""
    if not src:
        # find the class that owns _respond_to_message
        for name, obj in vars(vr).items():
            if inspect.isclass(obj) and hasattr(obj, "_respond_to_message"):
                src = inspect.getsource(obj._respond_to_message); break
    assert "_voice_turn_identity" in src and "guest_content" in src


def test_ingest_keeps_the_guest_origin():
    from core.turn.ingest import normalize_turn
    t = normalize_turn({"surface": "discord_voice", "owner_id": 4242, "content": "[Dana]: hi",
                        "metadata": {"is_voice": True, "channel_id": 1, "origin": "guest"}})
    assert t.metadata.origin == "guest" and t.owner_id == 4242
    t2 = normalize_turn({"surface": "discord_voice", "owner_id": 1, "content": "hi",
                         "metadata": {"origin": "bogus"}})
    assert t2.metadata.origin == "user"
