"""Owner-private tools never become receipts for a guest (second read F1 + F4,
2026-10-04).

A shared room keeps one conversation, and a collaborator (someone the owner
named) reads the work she did in it as tool receipts (Batch 8, ruling R2).
The second read found that the receipt channel also carried what B8-1 keeps
owner-only "for everyone": the owner's room note in the owner's own words
(``room_note`` set and list), the house right now (``look_at_the_house``),
her private pages (``introspect``, ``where_did_that_come_from``,
``note_to_self``), and the text of a message she sent to another
conversation (``send_message``; ``speak_line``'s text fallback goes to the
owner's last chat too). For a non-owner audience those calls are now
name-only receipts; a stranger gets no receipt at all, as before; the
owner's payload is byte-identical.

The room is rendered the way the runner renders it (mark_working_notes →
mark_undelivered → diet), with the REAL room_note result string. Sentinels
only; stand-in names (Robin the owner, Sam a guest).
"""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from core.agent.session import (
    RECEIPT_MARK,
    AgentMessage,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    _receipt_text,
    diet_for_llm,
    mark_undelivered_for_llm,
    mark_working_notes_for_llm,
    normalize_for_llm,
    window_for_llm,
)

NOTE_WORDS = "NOTE-SECRET stay quiet about my surgery"
SECRETS = ("NOTE-SECRET", "HOUSE-SECRET", "PAGE-SECRET", "ORIGIN-SECRET", "SELF-SECRET",
           "DM-SECRET", "SPOKE-SECRET")


@pytest.fixture
def note_results(tmp_path, monkeypatch):
    """The REAL room_note tool's results for a set and a list."""
    from core.agent.builtin_tools.room_note import RoomNoteTool

    monkeypatch.setenv("PALLI_ROOM_NOTES_PATH", str(tmp_path / "room_notes.json"))
    ctx = SimpleNamespace(surface="discord_text", channel_id=123, turn_owner_id=1,
                          owner_words=[NOTE_WORDS])
    tool = RoomNoteTool()
    set_out = asyncio.run(tool.execute({"action": "set", "words": NOTE_WORDS, "hours": 2}, ctx))
    list_out = asyncio.run(tool.execute({"action": "list"}, ctx))
    assert isinstance(set_out, str) and NOTE_WORDS in set_out, set_out
    assert isinstance(list_out, str) and NOTE_WORDS in list_out, list_out
    return set_out, list_out


def _calls(note_results):
    set_out, list_out = note_results
    return [
        (ToolUseBlock(id="t1", name="local_shell", input={"command": "df -h"}),
         "/dev/sda1 91% used"),
        (ToolUseBlock(id="t2", name="look_at_the_house", input={}),
         "HOUSE-SECRET the owner's phone home since 14:02"),
        (ToolUseBlock(id="t3", name="introspect", input={"what": "my_story"}),
         "PAGE-SECRET I've been thinking about the boat"),
        (ToolUseBlock(id="t4", name="where_did_that_come_from", input={"line": "x"}),
         "ORIGIN-SECRET from my journal of 09-12"),
        (ToolUseBlock(id="t5", name="note_to_self", input={"text": "SELF-SECRET remember"}),
         "Noted: SELF-SECRET remember"),
        (ToolUseBlock(id="t6", name="send_message",
                      input={"to": "robin", "text": "DM-SECRET your pin is 4321"}),
         "Sent to Robin (DM): DM-SECRET your pin is 4321"),
        (ToolUseBlock(id="t7", name="room_note",
                      input={"action": "set", "words": NOTE_WORDS, "hours": 2}),
         set_out),
        (ToolUseBlock(id="t8", name="room_note", input={"action": "list"}), list_out),
    ]


def _room(note_results, *, speak_fallback: bool = False):
    calls = _calls(note_results)
    if speak_fallback:
        calls = [(ToolUseBlock(id="s1", name="speak_line", input={"text": "SPOKE-SECRET hi"}),
                  "Not spoken — I'm not in a voice session, so it went to them as text "
                  "on discord:555: SPOKE-SECRET hi")]
    return [
        AgentMessage(role="user", content=[TextBlock(text="check the disk and the house")]),
        AgentMessage(role="assistant", content=[
            TextBlock(text="let me look"), *[tu for tu, _ in calls]]),
        AgentMessage(role="tool", content=[
            ToolResultBlock(tool_use_id=tu.id, content=res) for tu, res in calls]),
        AgentMessage(role="assistant", content=[TextBlock(text="The main disk is 91% full.")]),
        AgentMessage(role="user", content=[TextBlock(text="[Sam]: what did that show?")]),
    ]


def _render(rows, audience, keep_from):
    rows = mark_undelivered_for_llm(mark_working_notes_for_llm(rows))
    out = diet_for_llm(rows, keep_from=keep_from, audience=audience)
    return normalize_for_llm(window_for_llm(out, 0), log_context="t")


def _text(msgs) -> str:
    parts = []
    for m in msgs:
        for b in m.content:
            if isinstance(b, TextBlock):
                parts.append(b.text)
            elif isinstance(b, ToolUseBlock):
                parts.append(f"{b.name} {json.dumps(b.input)}")
            elif isinstance(b, ToolResultBlock):
                parts.append(str(b.content))
    return "\n".join(parts)


def _receipts(msgs) -> list[str]:
    return [line for line in _text(msgs).splitlines() if line.startswith(RECEIPT_MARK)]


@pytest.fixture(autouse=True)
def _duplex_on(monkeypatch):
    monkeypatch.setattr("core.agent.duplex.duplex_enabled", lambda: True)


def test_a_collaborator_never_reads_an_owner_private_receipt(note_results):
    rows = _room(note_results)
    text = _text(_render(rows, "collaborator", keep_from=len(rows)))

    leaked = [s for s in SECRETS if s in text]
    assert not leaked, leaked
    assert "surgery" not in text and "4321" not in text


def test_a_collaborator_gets_those_calls_by_name_only(note_results):
    rows = _room(note_results)
    receipts = _receipts(_render(rows, "collaborator", keep_from=len(rows)))

    by_name = {r.split()[len(RECEIPT_MARK.split())].split("(")[0]: r for r in receipts}
    for name in ("look_at_the_house", "introspect", "where_did_that_come_from",
                 "note_to_self", "send_message", "room_note"):
        assert any(r == f"{RECEIPT_MARK} {name} → (owner-only)" for r in receipts), (name, by_name)
    # R2 holds for everything else: the work done in front of them.
    assert any(r.startswith(f"{RECEIPT_MARK} local_shell(") and "91% used" in r
               for r in receipts)


def test_a_speak_line_that_went_to_another_chat_is_name_only_for_a_collaborator(note_results):
    rows = _room(note_results, speak_fallback=True)
    out = _render(rows, "collaborator", keep_from=len(rows))

    assert "SPOKE-SECRET" not in _text(out)
    assert f"{RECEIPT_MARK} speak_line → (owner-only)" in _receipts(out)


def test_a_stranger_gets_none_of_it(note_results):
    rows = _room(note_results)
    text = _text(_render(rows, "stranger", keep_from=len(rows)))

    leaked = [s for s in SECRETS if s in text]
    assert not leaked, leaked
    assert RECEIPT_MARK not in text
    assert "room_note" not in text and "look_at_the_house" not in text
    assert "91% full" in text                       # what was said out loud stays


def test_the_owners_payload_is_byte_identical(note_results):
    """Every receipt in the owner's payload is the full one, exactly as the
    receipt builder writes it — name, input and result."""
    rows = _room(note_results)
    out = _render(rows, "owner", keep_from=len(rows) - 1)

    expected = [_receipt_text(tu.name, tu.input, res, False) for tu, res in _calls(note_results)]
    assert _receipts(out) == expected
    text = _text(out)
    assert all(s in text for s in ("NOTE-SECRET", "HOUSE-SECRET", "PAGE-SECRET", "DM-SECRET"))
    # And the diet is the identity on the owner path's rows it does not strip.
    assert out[-1].content[0].text == "[Sam]: what did that show?"
