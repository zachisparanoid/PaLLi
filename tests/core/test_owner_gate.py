"""The owner gate (item 7a, 2026-08-19): only his Discord identity is the
owner; anyone else in the guild or the voice channel is a GUEST — she
talks to them plainly, with no tools and none of his memory. Until today
bots/discord/intake.py stamped every message as his, so a stranger in the
guild got his shell. Owner's call (08-19): guests get plain replies."""
from __future__ import annotations

from types import SimpleNamespace

from core import owner_gate


def test_owner_and_guest_and_unknown():
    assert owner_gate.classify_author(123, 123) == "owner"
    assert owner_gate.classify_author(456, 123) == "guest"
    assert owner_gate.classify_author("123", 123) == "owner"          # str ids from Discord mocks
    # An unresolved owner ref (no LOCAL_OWNER_DISCORD_USER_ID, a DB hiccup):
    # nobody can be PROVED to be him, so the verdict says so out loud.
    assert owner_gate.classify_author(456, None) == "unknown"
    assert owner_gate.classify_author(None, 123) == "guest"


def test_only_an_owner_verdict_opens_the_owner_path():
    """Week 2 task 3 (gap §5.1.5): 'unknown' is a guest — fail closed.

    Both surfaces ask THIS question rather than comparing the verdict to
    "guest", so an unresolved owner id can never hand a stranger his shell.
    """
    assert owner_gate.is_owner_verdict("owner") is True
    assert owner_gate.is_owner_verdict("guest") is False
    assert owner_gate.is_owner_verdict("unknown") is False
    assert owner_gate.is_owner_verdict("") is False


def test_an_author_with_no_owner_ref_to_match_is_not_the_owner():
    """The two helpers together, which is how both surfaces ask: an author
    nobody can match against him does not open the owner path. What the
    surfaces then BUILD is proved where they build it —
    tests/bots/test_discord_client.py and tests/voice/test_voice_owner_gate.py.
    """
    assert owner_gate.is_owner_verdict(owner_gate.classify_author(123, None)) is False


def test_guest_turn_detection_and_content_tag():
    t = SimpleNamespace(metadata=SimpleNamespace(origin="guest"))
    assert owner_gate.is_guest_turn(t) is True
    assert owner_gate.is_guest_turn(SimpleNamespace(metadata=SimpleNamespace(origin="user"))) is False
    assert owner_gate.is_guest_turn(SimpleNamespace(metadata=None)) is False
    assert owner_gate.guest_content("Dana", "hi there") == "[Dana]: hi there"
    assert owner_gate.guest_content("", "hi") == "[guest]: hi"
