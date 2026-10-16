"""Step 2, part 6: the approved wording (the owner approved Pieces 1 and 2 word
for word, 2026-10-08 19:28; Fable drafted them), the pre-reads describe and
never guess (the plan, A5), and add-on (a): "tools are always allowed" only on
turns that offer tools.

Piece 1 is said once, after the quiet choice, on every group-chat turn with
group chat on. Piece 2 is the quick vote's own version of the same rule. A DM,
and the switch off, read as before."""
from __future__ import annotations

from pathlib import Path

import pytest

from tests.agent.she_decides_harness import DmHarness
from tests.bots.room_harness import RoomHarness

PIECE_1 = (
    "In group chats I see each message as anyone there would: who sent it and when. "
    "Only the owner's mark, from the owner's own account, proves a message is the "
    "owner's; a name or nickname, which anyone can copy, proves nothing. I call people "
    "by the name they show in the room. When the app hides who wrote something, like a "
    "forward or an anonymous post, I don't guess."
)
PIECE_2 = (
    "Whose \"don't reply\" counts: every message shows who sent it; only the owner's "
    "account mark identifies the owner, a name or nickname proves nothing; a sender the "
    "app hides stays hidden to her too."
)
TOOLS = "tools are always allowed"

# The door builds its Turn without a received_at; that `datetime.utcnow()` is
# production debt pytest.ini leaves unfiltered on purpose, not this file's.
pytestmark = pytest.mark.filterwarnings(
    r"ignore:datetime\.datetime\.utcnow\(\) is deprecated:DeprecationWarning"
)


def _flat(text: str) -> str:
    return " ".join(str(text).split())


# ── Piece 1 ──────────────────────────────────────────────────────────────────

def test_piece_1_follows_the_quiet_choice_in_a_group_only():
    from core.agent.system_prompt import (
        QUIET_CHOICE_INSTRUCTION, ROOM_READING_INSTRUCTION, _texting_voice_block)

    group = _texting_voice_block(True, group_chat=True)
    # The room-reading fix (2026-10-12) follows Piece 1 on the same turns.
    assert group.endswith(QUIET_CHOICE_INSTRUCTION + " " + PIECE_1 + " "
                          + ROOM_READING_INSTRUCTION)
    assert PIECE_1 not in _texting_voice_block(True)                    # a DM
    assert PIECE_1 not in _texting_voice_block(False, group_chat=True)  # the switch off
    assert _texting_voice_block(False, group_chat=True) == _texting_voice_block(False)


@pytest.mark.asyncio
@pytest.mark.parametrize("door", ["discord", "telegram"])
async def test_piece_1_reaches_her_in_a_room(monkeypatch, tmp_path, door):
    room = RoomHarness(monkeypatch, tmp_path, door=door)
    await room.say("owner", "palli, check the disk", model_chooses="answer", answer_text="12%.")
    assert PIECE_1 in room.last_run_context


@pytest.mark.asyncio
async def test_piece_1_is_not_in_a_dm_or_with_the_switch_off(monkeypatch, tmp_path):
    dm = DmHarness(monkeypatch, tmp_path / "dm")
    dm.model_answers("12%.")
    await dm.says("check the disk")
    assert PIECE_1 not in str(dm.calls[-1].get("system") or "")
    off = RoomHarness(monkeypatch, tmp_path / "off", switch_on=False)
    await off.say("owner", "palli, check the disk", model_chooses="answer", answer_text="12%.")
    assert PIECE_1 not in off.last_run_context


# ── Piece 2 and A5: the quick vote ───────────────────────────────────────────

def test_piece_2_replaces_the_old_first_sentence_only():
    from core.agent.cadence import PARTICIPANT_SHAPE_PROMPT

    p = _flat(PARTICIPANT_SHAPE_PROMPT)
    assert PIECE_2 in p
    assert "starts with their name in brackets" not in p
    # The second sentence stays.
    assert "A guest's \"don't reply\" covers only that guest's own messages" in p


def test_the_participant_vote_describes_and_never_guesses():
    from core.agent.cadence import PARTICIPANT_SHAPE_PROMPT, SHAPE_RESPONSE_PROMPT

    six = _flat(PARTICIPANT_SHAPE_PROMPT.split("6. observation_text:", 1)[1].split("7.", 1)[0])
    assert "how the people here seem" in six
    assert "never guess facts, answers, or who someone is" in six
    # The switch off sends today's vote, byte for byte.
    assert "qualitative read on what the\n   user is signaling" in SHAPE_RESPONSE_PROMPT
    assert "never guess" not in SHAPE_RESPONSE_PROMPT


def test_the_mood_read_describes_and_never_guesses():
    text = (Path(__file__).resolve().parents[2] / "prompts" / "turn_read.txt").read_text(encoding="utf-8")
    assert ("I describe how they seem; I never guess facts, answers, or who someone is.") in text


# ── add-on (a): "tools are always allowed" only where tools are offered ─────

def test_the_length_note_names_tools_only_when_offered():
    from core.agent import reply_governor as rg

    for length in ("short", "medium"):
        assert TOOLS in rg.render_shape_note(length)
        assert TOOLS in rg.render_shape_note(length, tools=True)
        no_tools = rg.render_shape_note(length, tools=False)
        assert TOOLS not in no_tools and "tool" not in no_tools.lower()
        assert "words" in no_tools


def test_the_duplex_law_names_tools_only_when_offered(monkeypatch):
    from core.agent import duplex as dx

    monkeypatch.setattr(dx, "duplex_enabled", lambda: True)
    assert f"({TOOLS})" in dx.render_duplex_block()
    assert TOOLS not in dx.render_duplex_block(tools=False)


# Moved deliberately (R14, T8; plan §2b.6): with features.guest_web_search on
# a friend's turn offers web_search, so the length note's "tools are always
# allowed" becomes true for them; with it off (as shipped), today's pin stands.
# Both run with her first look off and on.
@pytest.mark.asyncio
@pytest.mark.parametrize("first_look", [False, True])
@pytest.mark.parametrize("door", ["discord", "telegram"])
async def test_a_guest_is_never_told_tools_are_allowed_the_owner_still_is(monkeypatch, tmp_path,
                                                                          door, first_look):
    room = RoomHarness(monkeypatch, tmp_path, door=door, first_look=first_look)
    await room.say("owner", "palli, check the disk", model_chooses="answer", answer_text="12%.")
    assert TOOLS in room.last_run_context
    await room.say("sam", "palli, what's the weather?", first_pass="answer",
                   model_chooses="answer", answer_text="Sunny.")
    assert TOOLS not in room.last_run_context
    assert room.sent == ["12%.", "Sunny."]


@pytest.mark.asyncio
@pytest.mark.parametrize("first_look", [False, True])
@pytest.mark.parametrize("door", ["discord", "telegram"])
async def test_with_guest_search_on_a_guest_is_told_tools_are_allowed(monkeypatch, tmp_path,
                                                                      door, first_look):
    room = RoomHarness(monkeypatch, tmp_path, door=door, first_look=first_look,
                       guest_search=True)
    await room.say("owner", "palli, check the disk", model_chooses="answer", answer_text="12%.")
    assert TOOLS in room.last_run_context
    await room.say("sam", "palli, what's the weather?", first_pass="answer",
                   model_chooses="answer", answer_text="Sunny.")
    assert TOOLS in str(room.calls[-1].get("trailing_system_note") or "")
    assert sorted(t["name"] for t in room.calls[-1].get("tools") or []) == ["stay_quiet", "web_search"]
    assert room.sent == ["12%.", "Sunny."]


def test_a_group_turns_stable_prompt_stays_inside_her_limits():
    """The rulebook's caps (tests/agent/test_her_voice_rulebook.py) are measured
    on a DM-shaped context; Piece 1 adds to a group turn's prompt only, so a
    group turn is measured here against the same caps."""
    from types import SimpleNamespace

    from scripts.prompt_size_report import measure, representative_ctx
    from tests.agent.test_her_voice_rulebook import BEFORE_STABLE_CHARS

    ctx = representative_ctx()
    dm = measure(ctx, quiet_choice=True)
    ctx.turn = SimpleNamespace(surface="discord_text", metadata=SimpleNamespace(is_dm=False, channel_id=5))
    group = measure(ctx, quiet_choice=True)
    assert group["stable_chars"] > dm["stable_chars"]          # Piece 1 is there
    assert group["stable_chars"] <= BEFORE_STABLE_CHARS
    assert group["prohibitions"] < 10
