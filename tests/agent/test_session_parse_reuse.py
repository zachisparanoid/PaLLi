"""A transcript parsed once is reused by the next open (2026-10-12).

One reply opened a 13,313-row conversation more than once and parsed the
whole file each time (0.44 s per open, on the event loop). Now the next open
reuses the parsed rows and parses only the lines appended since. Every other
change to the file takes the full read, which behaves exactly as before.
"""
from __future__ import annotations

import os

import pytest

from core.agent import session as S
from core.agent.session import AgentMessage, Session, TextBlock


@pytest.fixture(autouse=True)
def _fresh_cache(monkeypatch):
    # These transcripts are small: keep them, as the live channels are kept.
    monkeypatch.setattr(S, "_PARSE_CACHE_MIN_BYTES", 0)
    S._parsed_files.clear()
    yield
    S._parsed_files.clear()


def _user(text: str) -> AgentMessage:
    return AgentMessage(role="user", content=[TextBlock(text=text)])


def _words(session: Session) -> list[str]:
    return ["".join(b.text for b in m.content if isinstance(b, TextBlock)) for m in session.messages]


@pytest.fixture
def count_parses(monkeypatch):
    """How many rows were built from JSON since the fixture started."""
    real = S._msg_from_dict
    calls = {"n": 0}

    def counting(d):
        calls["n"] += 1
        return real(d)

    monkeypatch.setattr(S, "_msg_from_dict", counting)
    return calls


async def _seeded(tmp_path, *texts: str) -> Session:
    s = await Session.open_or_create("discord:1", root=tmp_path)
    for t in texts:
        await s.append(_user(t))
    return s


@pytest.mark.asyncio
async def test_a_second_open_reuses_the_parse(tmp_path, count_parses) -> None:
    await _seeded(tmp_path, "one", "two", "three")
    first = await Session.open_or_create("discord:1", root=tmp_path)
    count_parses["n"] = 0
    second = await Session.open_or_create("discord:1", root=tmp_path)
    assert count_parses["n"] == 0
    assert _words(second) == _words(first) == ["one", "two", "three"]


@pytest.mark.asyncio
async def test_only_the_appended_lines_are_parsed(tmp_path, count_parses) -> None:
    s = await _seeded(tmp_path, "one", "two", "three")
    await Session.open_or_create("discord:1", root=tmp_path)      # parsed and kept
    await s.append(_user("four"))
    await s.append(_user("five"))
    count_parses["n"] = 0
    again = await Session.open_or_create("discord:1", root=tmp_path)
    assert count_parses["n"] == 2
    assert _words(again) == ["one", "two", "three", "four", "five"]


@pytest.mark.asyncio
async def test_each_open_gets_its_own_list(tmp_path) -> None:
    await _seeded(tmp_path, "one", "two")
    a = await Session.open_or_create("discord:1", root=tmp_path)
    a.messages.append(_user("only in memory"))
    b = await Session.open_or_create("discord:1", root=tmp_path)
    assert _words(b) == ["one", "two"]


@pytest.mark.asyncio
async def test_an_atomic_rewrite_is_read_whole(tmp_path) -> None:
    s = await _seeded(tmp_path, "hello")
    await s.append(AgentMessage(role="assistant", content=[TextBlock(text="hi back")]))
    await Session.open_or_create("discord:1", root=tmp_path)      # parsed and kept
    assert await s.mark_undelivered("hi back") is True              # replaced beside, then swapped
    again = await Session.open_or_create("discord:1", root=tmp_path)
    assert again.messages[-1].delivery == "none"


@pytest.mark.asyncio
async def test_a_shorter_file_is_read_whole(tmp_path) -> None:
    s = await _seeded(tmp_path, "one", "two", "three")
    await Session.open_or_create("discord:1", root=tmp_path)
    lines = s.path.read_text(encoding="utf-8").splitlines(keepends=True)
    s.path.write_text("".join(lines[:1]), encoding="utf-8")
    again = await Session.open_or_create("discord:1", root=tmp_path)
    assert _words(again) == ["one"]


@pytest.mark.asyncio
async def test_a_changed_end_of_the_same_size_is_read_whole(tmp_path) -> None:
    s = await _seeded(tmp_path, "one", "two", "abc")
    await Session.open_or_create("discord:1", root=tmp_path)
    raw = s.path.read_bytes()
    s.path.write_bytes(raw.replace(b'"abc"', b'"xyz"'))      # same length, in place
    again = await Session.open_or_create("discord:1", root=tmp_path)
    assert _words(again) == ["one", "two", "xyz"]


@pytest.mark.asyncio
async def test_a_same_size_file_touched_since_is_read_whole(tmp_path, count_parses) -> None:
    s = await _seeded(tmp_path, "one", "two")
    await Session.open_or_create("discord:1", root=tmp_path)
    st = os.stat(s.path)
    os.utime(s.path, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000))
    count_parses["n"] = 0
    again = await Session.open_or_create("discord:1", root=tmp_path)
    assert count_parses["n"] == 2
    assert _words(again) == ["one", "two"]


@pytest.mark.asyncio
async def test_a_line_still_being_written_takes_the_full_read(tmp_path) -> None:
    s = await _seeded(tmp_path, "one", "two")
    await Session.open_or_create("discord:1", root=tmp_path)
    with s.path.open("a", encoding="utf-8") as f:
        f.write('{"role": "user", "content": [{"type": "te')      # no end of line yet
    again = await Session.open_or_create("discord:1", root=tmp_path)
    # The full read's rule, as before: the broken tail is cut and the file rewritten.
    assert _words(again) == ["one", "two"]
    assert s.path.read_text(encoding="utf-8").count("\n") == 2


@pytest.mark.asyncio
async def test_a_corrupt_appended_line_truncates_as_before(tmp_path) -> None:
    s = await _seeded(tmp_path, "one", "two")
    await Session.open_or_create("discord:1", root=tmp_path)
    with s.path.open("a", encoding="utf-8") as f:
        f.write("this is not json\n")
    again = await Session.open_or_create("discord:1", root=tmp_path)
    assert _words(again) == ["one", "two"]
    assert "this is not json" not in s.path.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_an_empty_file_then_appends(tmp_path) -> None:
    s = await Session.open_or_create("discord:1", root=tmp_path)
    s.path.write_text("", encoding="utf-8")
    empty = await Session.open_or_create("discord:1", root=tmp_path)
    assert empty.messages == []
    await s.append(_user("first words"))
    again = await Session.open_or_create("discord:1", root=tmp_path)
    assert _words(again) == ["first words"]


@pytest.mark.asyncio
async def test_only_the_conversations_in_use_are_kept(tmp_path) -> None:
    for i in range(S._PARSE_CACHE_FILES + 1):
        s = await Session.open_or_create(f"discord:{i}", root=tmp_path)
        await s.append(_user(f"hello {i}"))
        await Session.open_or_create(f"discord:{i}", root=tmp_path)
    kept = list(S._parsed_files)
    assert len(kept) == S._PARSE_CACHE_FILES
    assert not any(k.endswith("discord_0.jsonl") for k in kept)     # the oldest went first


@pytest.mark.asyncio
async def test_after_a_truncated_corrupt_line_a_clean_append_reuses_the_parse(tmp_path, count_parses) -> None:
    """Fable's review (10-12): the one path where an entry older than the
    file's last rewrite is reused. The rewrite puts back the same bytes, so
    the end of what was parsed still matches and only the new line is read."""
    s = await _seeded(tmp_path, "one", "two")
    await Session.open_or_create("discord:1", root=tmp_path)      # parsed and kept
    with s.path.open("a", encoding="utf-8") as f:
        f.write("this is not json\n")
    await Session.open_or_create("discord:1", root=tmp_path)      # truncated and rewritten
    await s.append(_user("three"))
    count_parses["n"] = 0
    again = await Session.open_or_create("discord:1", root=tmp_path)
    assert count_parses["n"] == 1
    assert _words(again) == ["one", "two", "three"]


@pytest.mark.asyncio
async def test_the_payload_passes_never_edit_a_kept_row(tmp_path) -> None:
    """The kept rows are shared between opens (Fable's review, 10-12): every
    pass that builds a reply's payload must make new rows, never edit one."""
    from core.agent.message_times import stamp_times_for_llm
    from core.agent.session import (
        ToolResultBlock, ToolUseBlock, _msg_to_jsonl, diet_for_llm, ensure_user_tail,
        fold_interims_for_thinking, mark_undelivered_for_llm, mark_working_notes_for_llm,
        normalize_for_llm, window_for_llm,
    )
    from core.agent.speakers import label_speakers_for_llm

    s = await Session.open_or_create("discord:1", root=tmp_path)
    await s.append(_user("check the disk"))
    await s.append(AgentMessage(role="assistant", content=[
        TextBlock(text="looking"),
        ToolUseBlock(id="t1", name="local_shell", input={"command": "df -h"})]))
    await s.append(AgentMessage(role="tool", content=[
        ToolResultBlock(tool_use_id="t1", content="/dev/sda1 40% used")]))
    await s.append(AgentMessage(role="assistant", content=[TextBlock(text="40% used.")],
                                delivery="none"))
    await s.append(_user("thanks"))
    rows = (await Session.open_or_create("discord:1", root=tmp_path)).messages
    before = [_msg_to_jsonl(m) for m in rows]

    payload = mark_undelivered_for_llm(mark_working_notes_for_llm(rows))
    payload = diet_for_llm(payload, keep_from=len(payload) - 1)
    payload = normalize_for_llm(window_for_llm(payload, 3), log_context="probe")
    payload = ensure_user_tail(fold_interims_for_thinking(payload), fallback_text="thanks")
    stamp_times_for_llm(label_speakers_for_llm(payload))

    reopened = await Session.open_or_create("discord:1", root=tmp_path)
    assert [_msg_to_jsonl(m) for m in reopened.messages] == before


@pytest.mark.asyncio
async def test_small_transcripts_are_read_whole_and_not_kept(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(S, "_PARSE_CACHE_MIN_BYTES", 10_000)
    s = await _seeded(tmp_path, "one", "two")
    await Session.open_or_create("discord:1", root=tmp_path)
    assert S._parsed_files == {}
    for i in range(80):                                   # grows past the floor
        await s.append(_user(f"line {i} " + "x" * 120))
    await Session.open_or_create("discord:1", root=tmp_path)
    assert len(S._parsed_files) == 1


@pytest.mark.asyncio
async def test_a_file_that_shrinks_below_the_floor_drops_its_kept_rows(tmp_path, monkeypatch) -> None:
    """Fable's final read (10-12): a channel reset to a small file must not
    leave the big file's rows behind."""
    monkeypatch.setattr(S, "_PARSE_CACHE_MIN_BYTES", 10_000)
    s = await Session.open_or_create("discord:1", root=tmp_path)
    for i in range(80):
        await s.append(_user(f"line {i} " + "x" * 120))
    await Session.open_or_create("discord:1", root=tmp_path)
    assert len(S._parsed_files) == 1
    s.path.write_text(s.path.read_text(encoding="utf-8").splitlines(keepends=True)[0],
                      encoding="utf-8")
    again = await Session.open_or_create("discord:1", root=tmp_path)
    assert _words(again) == ["line 0 " + "x" * 120]
    assert S._parsed_files == {}
