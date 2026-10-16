"""The owner's room note (Batch 8, task 2b — owner O1, ruling T2b-5). INTERIM.

The owner asked for a small room note: a standing instruction for one room
("stay quiet in here until tomorrow") that she saves with an owner-only tool,
and that her decisions in that room see as one line until it runs out or the
owner lifts it. She still decides. It is interim — the owner revisits it after
the batches — so it is one store (``core/room_notes.py``), one tool
(``core/agent/builtin_tools/room_note.py``) and one line, shown only with
``companion.she_decides`` on.

Stand-in names only: Robin is the owner, Sam a guest.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 10, 1, 9, 0, 0)
ROOM_KEY = "discord:8800002"

pytestmark = pytest.mark.filterwarnings(
    r"ignore:datetime\.datetime\.utcnow\(\) is deprecated:DeprecationWarning"
)


@pytest.fixture
def notes(tmp_path, monkeypatch):
    from core import room_notes

    monkeypatch.setenv("PALLI_ROOM_NOTES_PATH", str(tmp_path / "room_notes.json"))
    return room_notes


# ── the store ─────────────────────────────────────────────────────────────

def test_a_note_is_active_until_it_runs_out(notes):
    notes.set_note(ROOM_KEY, "stay quiet in here until tomorrow", NOW + timedelta(hours=24),
                   now=NOW)

    got = notes.active(ROOM_KEY, now=NOW + timedelta(hours=1))
    assert got["words"] == "stay quiet in here until tomorrow"
    assert got["set_at"].startswith("2026-10-01T09:00")
    assert got["until"].startswith("2026-10-02T09:00")
    assert notes.active(ROOM_KEY, now=NOW + timedelta(hours=25)) is None   # a time limit
    assert notes.active("discord:1", now=NOW) is None                       # one room only


def test_lift_and_list(notes):
    notes.set_note(ROOM_KEY, "quiet please", NOW + timedelta(hours=2), now=NOW)
    notes.set_note("telegram:-100", "no small talk", NOW + timedelta(hours=1), now=NOW)
    assert set(notes.list_active(now=NOW)) == {ROOM_KEY, "telegram:-100"}
    assert set(notes.list_active(now=NOW + timedelta(minutes=90))) == {ROOM_KEY}

    # The fixed clock everywhere (fix round 2): against the real one, a note
    # set at NOW to last two hours reads as run out from NOW + 2h on.
    assert notes.lift(ROOM_KEY, now=NOW) is True
    assert notes.lift(ROOM_KEY, now=NOW) is False
    assert notes.active(ROOM_KEY, now=NOW) is None


def test_a_note_in_the_past_or_without_words_is_refused(notes):
    with pytest.raises(ValueError):
        notes.set_note(ROOM_KEY, "quiet", NOW - timedelta(minutes=1), now=NOW)
    with pytest.raises(ValueError):
        notes.set_note(ROOM_KEY, "   ", NOW + timedelta(hours=1), now=NOW)


def test_the_file_is_written_whole_and_a_bad_file_reads_as_no_notes(notes, tmp_path):
    notes.set_note(ROOM_KEY, "quiet", NOW + timedelta(hours=1), now=NOW)
    data = json.loads((tmp_path / "room_notes.json").read_text(encoding="utf-8"))
    assert data[ROOM_KEY]["words"] == "quiet"
    assert not list(tmp_path.glob("*.tmp"))

    (tmp_path / "room_notes.json").write_text("{not json", encoding="utf-8")
    assert notes.active(ROOM_KEY, now=NOW) is None
    assert notes.note_line(ROOM_KEY, now=NOW) == ""


def test_the_line_names_the_words_the_times_and_the_one_exception(notes):
    notes.set_note(ROOM_KEY, "stay quiet in here until tomorrow", NOW + timedelta(hours=24),
                   now=NOW)
    line = notes.note_line(ROOM_KEY, now=NOW + timedelta(hours=1))

    assert "\n" not in line
    assert '"stay quiet in here until tomorrow"' in line
    assert "Oct 1, 09:00" in line and "Oct 2, 09:00" in line
    assert "real safety reason" in line
    assert notes.note_line("discord:1", now=NOW) == ""


def test_the_new_texts_avoid_the_plain_words_family_and_never_call_her_a_person(notes):
    from core.agent.builtin_tools.room_note import RoomNoteTool
    from tests.prompts.test_plain_words import PHRASE_FAMILY

    notes.set_note(ROOM_KEY, "quiet", NOW + timedelta(hours=1), now=NOW)
    for text in (notes.note_line(ROOM_KEY, now=NOW), RoomNoteTool.description,
                 json.dumps(RoomNoteTool.input_schema)):
        assert not PHRASE_FAMILY.search(text), text
        assert "a person" not in text.lower()


def test_the_suite_never_touches_her_live_notes():
    import os

    from core import room_notes

    root = Path(os.environ["PALLI_TEST_DATA_ROOT"])
    assert room_notes.store_path().resolve().is_relative_to(root.resolve())
    gitignore = (REPO / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "data/room_notes.json" in gitignore


def test_it_says_it_is_interim():
    import core.agent.builtin_tools.room_note as tool_module
    from core import room_notes

    for module in (room_notes, tool_module):
        assert "interim" in (module.__doc__ or "").lower()
        assert "after the batches" in (module.__doc__ or "").lower()


# ── the tool ──────────────────────────────────────────────────────────────

def _ctx(*, surface="discord_text", channel_id=8800002, is_owner=True, owner_words=()):
    """``owner_words``: the owner's own message(s) this turn — second read F9:
    a note's words must come from them."""
    from core.agent.tools import ToolContext

    return ToolContext(turn_owner_id=1, is_owner=is_owner, surface=surface,
                       channel_id=channel_id, owner_words=list(owner_words))


@pytest.mark.asyncio
async def test_she_saves_lists_and_lifts_a_note_for_the_room_she_is_in(notes):
    from core.agent.builtin_tools.room_note import RoomNoteTool
    from core.agent.tool_outcome import ToolFailure

    tool = RoomNoteTool()
    assert tool.owner_only is True

    out = await tool.execute({"action": "set", "words": "stay quiet in here until tomorrow",
                              "hours": 20},
                             _ctx(owner_words=["polly, stay quiet in here until tomorrow"]))
    assert not isinstance(out, ToolFailure), out
    assert notes.active(ROOM_KEY)["words"] == "stay quiet in here until tomorrow"

    listed = await tool.execute({"action": "list"}, _ctx())
    assert "stay quiet in here until tomorrow" in listed and ROOM_KEY in listed

    lifted = await tool.execute({"action": "lift"}, _ctx())
    assert not isinstance(lifted, ToolFailure)
    assert notes.active(ROOM_KEY) is None


@pytest.mark.asyncio
async def test_the_tool_takes_an_end_time_or_hours_and_names_other_rooms(notes):
    from core.agent.builtin_tools.room_note import RoomNoteTool
    from core.agent.tool_outcome import ToolFailure

    tool = RoomNoteTool()
    asked = ["no small talk in the group till tonight", "quiet in #general for an hour"]
    until = (datetime.now() + timedelta(hours=3)).replace(microsecond=0).isoformat()
    out = await tool.execute({"action": "set", "words": "no small talk", "until": until,
                              "room": "telegram:-1009900001"},
                             _ctx(surface="dashboard", owner_words=asked))
    assert not isinstance(out, ToolFailure), out
    assert notes.active("telegram:-1009900001")["until"] == until

    mention = await tool.execute({"action": "set", "words": "quiet", "hours": 1,
                                  "room": "<#123456>"}, _ctx(owner_words=asked))
    assert not isinstance(mention, ToolFailure)
    assert notes.active("discord:123456") is not None

    for bad in ({"action": "set", "words": "quiet"},                         # no end
                {"action": "set", "hours": 2},                               # no words
                {"action": "set", "words": "quiet", "hours": 24 * 31},       # too long
                {"action": "dance"}):
        assert isinstance(await tool.execute(bad, _ctx(owner_words=asked)), ToolFailure), bad
    no_room = await tool.execute({"action": "set", "words": "quiet", "hours": 1},
                                 _ctx(surface="dashboard", channel_id=0, owner_words=asked))
    assert isinstance(no_room, ToolFailure)


# ── second read F9 (2026-10-04): the note's words are the owner's own ─────

@pytest.mark.asyncio
async def test_a_note_whose_words_are_not_in_the_owners_message_is_refused(notes):
    from core.agent.builtin_tools.room_note import RoomNoteTool
    from core.agent.tool_outcome import ToolFailure

    tool = RoomNoteTool()
    out = await tool.execute({"action": "set", "words": "stay quiet in here till tomorrow",
                              "hours": 20}, _ctx(owner_words=["what do you think, polly?"]))
    assert isinstance(out, ToolFailure)
    assert "the owner has to ask for this in their own words" in str(out)
    assert notes.active(ROOM_KEY) is None

    nothing = await tool.execute({"action": "set", "words": "stay quiet", "hours": 2}, _ctx())
    assert isinstance(nothing, ToolFailure) and notes.active(ROOM_KEY) is None


@pytest.mark.asyncio
async def test_the_owners_words_match_whatever_the_case_and_punctuation(notes):
    from core.agent.builtin_tools.room_note import RoomNoteTool
    from core.agent.tool_outcome import ToolFailure

    tool = RoomNoteTool()
    out = await tool.execute({"action": "set", "words": "stay quiet in here until tomorrow",
                              "hours": 20},
                             _ctx(owner_words=["Hey Polly — STAY QUIET, in here, until "
                                               "tomorrow!! I'm in surgery"]))
    assert not isinstance(out, ToolFailure), out
    # Not a match: the same words in another order, or only some of them.
    for words in ("until tomorrow stay quiet in here", "stay quiet in here until monday"):
        got = await tool.execute({"action": "set", "words": words, "hours": 2},
                                 _ctx(owner_words=["stay quiet in here until tomorrow"]))
        assert isinstance(got, ToolFailure), words


@pytest.mark.asyncio
async def test_the_note_records_who_set_it_and_what_they_said(notes):
    from core.agent.builtin_tools.room_note import RoomNoteTool

    message = "polly, stay quiet in here until tomorrow please, it's my surgery day"
    await RoomNoteTool().execute(
        {"action": "set", "words": "stay quiet in here until tomorrow", "hours": 20},
        _ctx(owner_words=[message]))
    note = notes.active(ROOM_KEY)
    assert note["set_by"] == {"who": "owner", "id": 1, "door": "discord_text"}
    assert "stay quiet in here until tomorrow" in note["excerpt"]
    assert note["excerpt"] in message


@pytest.mark.asyncio
async def test_a_guests_line_in_the_room_cannot_become_the_owners_note(monkeypatch, tmp_path):
    """Second read F9: in a shared room the owner's reply call sees a guest's
    "[Sam]: stay quiet in here till tomorrow" in history. If she saves those
    words as a room note on the owner's turn, it is refused — the owner never
    said them — and no note is saved (a note would be read as the owner's
    for up to 30 days)."""
    from core import room_notes
    from core.agent.builtin_tools.room_note import RoomNoteTool
    from core.agent.session import ToolResultBlock, ToolUseBlock
    from core.agent.tools import ToolRegistry
    from core.session_keys import session_key_for
    from tests.agent.she_decides_harness import ROOM_CHANNEL, DmHarness

    monkeypatch.setenv("PALLI_ROOM_NOTES_PATH", str(tmp_path / "room_notes.json"))
    h = DmHarness(monkeypatch, tmp_path, switch_on=True)
    reg = ToolRegistry()
    reg.register(RoomNoteTool())
    h.registry = reg
    room_key = session_key_for("discord_text", ROOM_CHANNEL, 1)

    h.model_answers("got it, Sam")
    await h.says("stay quiet in here till tomorrow", who="Sam", channel=ROOM_CHANNEL,
                 is_dm=False)
    assert await h.transcript_has("[Sam]: stay quiet in here till tomorrow",
                                  channel=ROOM_CHANNEL)

    h.model_calls(ToolUseBlock(id="n1", name="room_note", input={
        "action": "set", "words": "stay quiet in here till tomorrow", "hours": 20}))
    h.model_answers("Sam asked for quiet — that's for you to decide.")
    await h.says("what do you think, polly?", channel=ROOM_CHANNEL, is_dm=False)

    assert room_notes.active(room_key) is None
    results = {b.tool_use_id: b for m in await h.transcript(channel=ROOM_CHANNEL)
               if m.role == "tool" for b in m.content if isinstance(b, ToolResultBlock)}
    assert results["n1"].is_error
    assert "the owner has to ask for this in their own words" in results["n1"].content

    # The owner's own words, on the owner's own turn, still set it.
    h.model_calls(ToolUseBlock(id="n2", name="room_note", input={
        "action": "set", "words": "stay quiet in here till tomorrow", "hours": 20}))
    h.model_answers("Done.")
    await h.says("yes — stay quiet in here till tomorrow", channel=ROOM_CHANNEL, is_dm=False)
    note = room_notes.active(room_key)
    assert note is not None and note["words"] == "stay quiet in here till tomorrow"
    assert note["excerpt"].startswith("yes — stay quiet in here till tomorrow")


def test_the_tool_is_on_the_owners_belt_only_with_the_switch_on(monkeypatch):
    from core.agent import quiet_choice
    from core.agent.builtin_tools import build_default_registry
    from core.agent.tools import ToolContext

    assert "room_note" not in build_default_registry().all_names()       # switch off: as 2a

    monkeypatch.setattr(quiet_choice, "she_decides_enabled", lambda: True)
    reg = build_default_registry()
    assert "room_note" in reg.all_names()
    guest = ToolContext(turn_owner_id=4242, is_owner=False, surface="discord_text", channel_id=1)
    assert "room_note" not in [s["name"] for s in reg.schemas_for(guest)]
    assert "room_note" not in build_default_registry(surface="workbench").all_names()


# ── the one line, where it shows ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_the_line_is_in_the_rooms_first_read_and_her_reply_call(monkeypatch, tmp_path):
    """The note's words reach the room's first reads and the owner's own reply
    calls. A guest's reply call sees only a neutral line (ruling T2b-5b)."""
    import json as _json

    from core import room_notes
    from tests.bots.room_harness import RoomHarness

    room = RoomHarness(monkeypatch, tmp_path)
    room_notes.set_note(room.room_key(), "stay quiet in here until tomorrow",
                        datetime.now() + timedelta(hours=20))

    # A guest: the first read has the words; the reply call does not.
    await room.say("sam", "polly, you around?", first_pass="answer", model_chooses="answer",
                   answer_text="I'm here")
    assert '"stay quiet in here until tomorrow"' in room.cadence_prompts[-1]
    guest_call = room.calls[-1]
    assert "The owner has a standing note for this room until" in (
        guest_call.get("trailing_system_note") or "")
    assert "real safety reason" in guest_call.get("trailing_system_note")
    assert "stay quiet in here until tomorrow" not in _json.dumps(
        {k: str(v) for k, v in guest_call.items()})
    # She answered while the note stood: her record (owner-only) notes the break.
    row = room.quiet_record()[-1]
    assert row["source"] == "spoke_during_note" and row["excerpt"] == "I'm here"
    assert row["conversation"] == room.room_key() and row["who"]["owner"] is False

    # The owner: the reply call has the words.
    await room.say("owner", "polly, quick question", first_pass="answer", model_chooses="answer",
                   answer_text="sure")
    assert '"stay quiet in here until tomorrow"' in (
        room.calls[-1].get("trailing_system_note") or "")
    assert room.quiet_record()[-1]["who"]["owner"] is True


def test_a_guests_line_names_no_words_and_the_tool_says_where_the_words_go(notes):
    from core.agent.builtin_tools.room_note import RoomNoteTool

    notes.set_note(ROOM_KEY, "stay quiet in here until tomorrow", NOW + timedelta(hours=24),
                   now=NOW)
    neutral = notes.note_line(ROOM_KEY, words=False, now=NOW)
    # Final fix wave (ruling FW-8): neutral about what the note asks — a note
    # that asks for something other than quiet must not read as a silence.
    # Reworded 10-13 on the owner's word (Fable's text): "a silence it asks
    # for" still named a silence; now nothing in it says what the note asks.
    assert neutral == ("The owner has a standing note for this room until Oct 2, 09:00; its "
                       "words are the owner's, not shown here. Whatever it asks, the one thing "
                       "that comes before it is a real safety reason (someone says they are in "
                       "danger).")
    assert "quiet" not in neutral.lower() and "stay quiet" not in neutral
    assert notes.note_line(ROOM_KEY, words=True, now=NOW) == notes.note_line(ROOM_KEY, now=NOW)
    d = RoomNoteTool.description
    assert "seen in that room's decisions" in d and "a guest's reply there sees only" in d


@pytest.mark.asyncio
async def test_with_no_note_nothing_changes_in_her_reply_call(monkeypatch, tmp_path):
    """The owner's payload with no active note is 2a's: the trailing note is
    the shape note alone, and nothing of a note is anywhere."""
    from core import room_notes
    from core.agent import reply_governor
    from tests.agent.she_decides_harness import DmHarness

    h = DmHarness(monkeypatch, tmp_path)
    room_notes.set_note("discord:1", "quiet elsewhere", datetime.now() + timedelta(hours=1))
    h.model_answers("hey")
    await h.says("hey there")

    call = h.calls[-1]
    assert call.get("trailing_system_note") == reply_governor.render_shape_note("short")
    blob = json.dumps({k: str(v) for k, v in call.items()})
    assert "quiet elsewhere" not in blob and "note for this room" not in blob
    assert [r for r in h.quiet_record() if r["source"] == "spoke_during_note"] == []


@pytest.mark.asyncio
async def test_with_the_switch_off_a_note_shows_nowhere(monkeypatch, tmp_path):
    from core import room_notes
    from tests.agent.she_decides_harness import ROOM_CHANNEL, DmHarness

    h = DmHarness(monkeypatch, tmp_path, switch_on=False)
    room_notes.set_note(f"discord:{ROOM_CHANNEL}", "stay quiet", datetime.now() + timedelta(hours=1))
    h.model_answers("hi")
    await h.says("hello", channel=ROOM_CHANNEL, is_dm=False)

    blob = json.dumps({k: str(v) for k, v in h.calls[-1].items()})
    assert "stay quiet" not in blob and "stay quiet" not in "".join(h.cadence_prompts)
    assert h.quiet_record() == []


@pytest.mark.asyncio
async def test_a_reminder_fire_in_a_noted_room_carries_no_note(monkeypatch, tmp_path):
    from core import room_notes
    from tests.agent.she_decides_harness import ROOM_CHANNEL, DmHarness

    h = DmHarness(monkeypatch, tmp_path)
    room_notes.set_note(f"discord:{ROOM_CHANNEL}", "stay quiet", datetime.now() + timedelta(hours=1))
    h.model_answers("time to stretch")
    result = await h.dispatch(h.turn("[REMINDER FIRE] stretch", channel=ROOM_CHANNEL,
                                     is_dm=False, origin="reminder"))

    assert result.responded and result.response_text == "time to stretch"
    blob = json.dumps({k: str(v) for k, v in h.calls[-1].items()})
    assert "stay quiet" not in blob
    assert [t["name"] for t in h.calls[-1].get("tools") or []] == ["look_up"]   # no stay_quiet
    assert h.quiet_record() == []
