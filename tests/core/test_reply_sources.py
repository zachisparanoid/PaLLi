"""What a reply used (R15, memory that reaches, T13 and W3's reply-sources
part): one line per reply in ``data/reply_sources/<YYYY-MM-DD>.jsonl``, for
the owner's dashboards page and ``palli_ctl replies`` — never a chat.

Every folder here is a tmp folder (``PALLI_REPLY_SOURCES_DIR``); nothing
reads or writes the live record. Stand-in names only: Robin is the owner,
Dana a friend.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from core import reply_sources as rs

NOW = datetime(2026, 10, 13, 19, 2, 0)


@pytest.fixture
def folder(tmp_path, monkeypatch) -> Path:
    path = tmp_path / "reply_sources"
    monkeypatch.setenv("PALLI_REPLY_SOURCES_DIR", str(path))
    return path


def _write(now=NOW, **over):
    kwargs = dict(
        door="discord_text", conversation="discord:111", who_name="Robin", owner=True,
        reply_excerpt="The kite shop on Elm opens at nine on Saturdays.",
        record_lines=["[Oct 5 19:02] them: the kite shop on Elm opens at nine on Saturdays"],
        notes=["Robin likes kites"], facts=["hobby: kites"], today=["plan: kite shop Saturday"],
        room_lines=[], queries=["kite shop"],
        look={"rounds": 0, "calls": 0, "seconds": 0.4, "thin": False}, now=now,
    )
    kwargs.update(over)
    return rs.record(**kwargs)


# ── the record ────────────────────────────────────────────────────────────

def test_the_folder_is_data_reply_sources_unless_the_setting_moves_it(monkeypatch, tmp_path):
    monkeypatch.delenv("PALLI_REPLY_SOURCES_DIR", raising=False)
    repo = Path(rs.__file__).resolve().parents[1]
    assert rs.record_dir() == repo / "data" / "reply_sources"
    monkeypatch.setenv("PALLI_REPLY_SOURCES_DIR", str(tmp_path / "x"))
    assert rs.record_dir() == tmp_path / "x"


def test_the_suite_never_points_at_the_live_record():
    repo = Path(rs.__file__).resolve().parents[1]
    assert rs.record_dir().resolve() != (repo / "data" / "reply_sources").resolve()


def test_one_json_line_per_reply_in_the_days_file(folder):
    assert _write() is True
    assert _write(reply_excerpt="second") is True
    lines = (folder / "2026-10-13.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    row = json.loads(lines[0])
    assert row == {
        "ts": "2026-10-13T19:02:00",
        "door": "discord_text",
        "conversation": "discord:111",
        "who": {"name": "Robin", "owner": True},
        "reply": "The kite shop on Elm opens at nine on Saturdays.",
        "record_lines": ["[Oct 5 19:02] them: the kite shop on Elm opens at nine on Saturdays"],
        "notes": ["Robin likes kites"],
        "facts": ["hobby: kites"],
        "today": ["plan: kite shop Saturday"],
        "room_lines": [],
        "queries": ["kite shop"],
        "look": {"rounds": 0, "calls": 0, "seconds": 0.4, "thin": False},
    }


def test_the_file_is_lf_and_one_line_per_reply_whatever_the_text(folder):
    _write(reply_excerpt="one\r\ntwo\nthree", record_lines=["a\nb"], notes=["c\r\nd"])
    raw = (folder / "2026-10-13.jsonl").read_bytes()
    assert raw.count(b"\n") == 1 and b"\r" not in raw
    row = json.loads(raw)
    assert row["reply"] == "one two three"
    assert row["record_lines"] == ["a b"] and row["notes"] == ["c d"]


def test_each_text_is_cut_to_its_size(folder):
    _write(reply_excerpt="r" * 500, record_lines=["l" * 500], notes=["n" * 500],
           facts=["f" * 500], today=["t" * 500], room_lines=["g" * 500], queries=["q" * 500])
    row = rs.read(1, now=NOW)[0]
    assert len(row["reply"]) == rs.REPLY_CHARS == 120
    assert len(row["record_lines"][0]) == rs.LINE_CHARS == 160
    assert len(row["notes"][0]) == rs.NOTE_CHARS == 120
    for key in ("facts", "today", "room_lines", "queries"):
        assert len(row[key][0]) == rs.LINE_CHARS
    assert row["reply"].endswith("…") and row["record_lines"][0].endswith("…")


def test_blank_entries_go_and_a_line_found_twice_is_one_line(folder):
    line = "[Oct 5 19:02] them: the kite shop"
    _write(record_lines=[line, "  ", line, "[Oct 6 08:00] me: noted"], notes=["", "x"], facts=[" "])
    row = rs.read(1, now=NOW)[0]
    assert row["record_lines"] == [line, "[Oct 6 08:00] me: noted"]
    assert row["notes"] == ["x"] and row["facts"] == []


def test_no_look_is_null_and_a_look_keeps_only_its_four_counts(folder):
    _write(look=None)
    _write(look={"rounds": "2", "calls": 3, "seconds": 6.234, "thin": 1, "words": "never"})
    first, second = rs.read(1, now=NOW)
    assert first["look"] is None
    assert second["look"] == {"rounds": 2, "calls": 3, "seconds": 6.2, "thin": True}


def test_a_secret_on_the_denylist_never_reaches_the_record(folder, monkeypatch):
    from core import secrets_redaction

    monkeypatch.setattr(secrets_redaction, "redact",
                        lambda text: str(text).replace("hunter2-example", "[REDACTED]"))
    _write(reply_excerpt="the code is hunter2-example", record_lines=["them: hunter2-example"],
           queries=["hunter2-example"])
    raw = (folder / "2026-10-13.jsonl").read_text(encoding="utf-8")
    assert "hunter2-example" not in raw and raw.count("[REDACTED]") == 3


def test_a_write_that_fails_is_false_and_logged_with_no_words(folder, caplog):
    folder.parent.mkdir(parents=True, exist_ok=True)
    folder.write_text("a file where the folder should be", encoding="utf-8")
    with caplog.at_level(logging.WARNING, logger="core.reply_sources"):
        assert _write(reply_excerpt="private words here") is False
    text = caplog.text
    assert "reply_sources" in text and "private words here" not in text


def test_the_log_line_carries_counts_only(folder, caplog):
    with caplog.at_level(logging.INFO, logger="core.reply_sources"):
        _write()
    text = caplog.text
    assert "lines=1" in text and "notes=1" in text and "facts=1" in text and "queries=1" in text
    for private in ("kite", "Robin", "Elm", "Saturday"):
        assert private not in text


# ── reading ───────────────────────────────────────────────────────────────

def test_read_returns_the_last_days_oldest_first(folder):
    _write(now=NOW - timedelta(days=2), reply_excerpt="two days ago")
    _write(now=NOW - timedelta(days=1), reply_excerpt="yesterday")
    _write(now=NOW, reply_excerpt="today")
    assert [r["reply"] for r in rs.read(2, now=NOW)] == ["yesterday", "today"]
    assert [r["reply"] for r in rs.read(3, now=NOW)] == ["two days ago", "yesterday", "today"]
    assert [r["reply"] for r in rs.read(0, now=NOW)] == ["today"]       # at least today


def test_a_missing_folder_is_an_empty_record(folder):
    assert not folder.exists()
    assert rs.read(3, now=NOW) == []


def test_an_unreadable_line_is_skipped(folder):
    _write()
    with (folder / "2026-10-13.jsonl").open("a", encoding="utf-8") as fh:
        fh.write("{not json\n[1, 2]\n\n")
    _write(reply_excerpt="after")
    assert [r["reply"] for r in rs.read(1, now=NOW)] == [
        "The kite shop on Elm opens at nine on Saturdays.", "after"]


def test_describe_is_ts_who_reply_and_the_counts():
    # W4 (Fable's whole-branch review): "N record lines", as the page says
    # "lines from the record".
    _row = {"ts": "2026-10-13T19:02:00", "who": {"name": "Robin", "owner": True},
            "reply": "The kite shop opens at nine.", "record_lines": ["a", "b", "c"],
            "notes": ["n"], "facts": ["f1", "f2"]}
    assert rs.describe(_row) == (
        '2026-10-13 19:02 · Robin · "The kite shop opens at nine." · 3 record lines · 1 note · '
        '2 facts')
    bare = rs.describe({"ts": "2026-10-13T19:02:00", "who": {"name": "", "owner": True}})
    assert bare == '2026-10-13 19:02 · the owner · "" · 0 record lines · 0 notes · 0 facts'
    one = rs.describe({"ts": "x", "who": {"name": "Robin"}, "record_lines": ["a"]})
    assert " · 1 record line · " in one
    guest = rs.describe({"ts": "x", "who": {"name": "", "owner": False}})
    assert " · a guest · " in guest


def test_details_list_what_the_reply_drew_on_under_plain_labels():
    # W4: the page's own groups, shortened ("lines from the record" is
    # "record:", "this room's earlier lines" is "this room:").
    row = {"record_lines": ["[Oct 5 19:02] them: kites"], "notes": ["n"], "facts": ["f"],
           "today": ["t"], "room_lines": ["[Oct 6 08:00] Dana: hi"], "queries": ["kites", "Elm"],
           "look": {"rounds": 1, "calls": 3, "seconds": 6.2, "thin": True}}
    assert rs.details(row) == [
        'searched: "kites" · "Elm"',
        "look: 1 round, 3 calls, 6.2 s, thin",
        "record: [Oct 5 19:02] them: kites",
        "note: n",
        "fact: f",
        "today: t",
        "this room: [Oct 6 08:00] Dana: hi",
    ]
    assert rs.details({}) == []


# ── the switch, and what the runner hands over ────────────────────────────

def test_the_switch_is_companion_memory_reach_read_through_the_records_accessor(monkeypatch):
    import ai.memory_reach_index as mri

    assert rs.enabled() is False                       # the suite's pin: off
    monkeypatch.setattr(mri, "enabled", lambda: True)
    assert rs.enabled() is True

    def boom():
        raise RuntimeError("settings broke")

    monkeypatch.setattr(mri, "enabled", boom)
    assert rs.enabled() is False                       # fail-closed


def test_look_of_sums_the_searches_and_is_none_without_one():
    assert rs.look_of([]) is None
    assert rs.look_of(None) is None
    one = [{"hits": 3, "thin": False, "rounds": 0, "calls": 0, "seconds": 0.41}]
    assert rs.look_of(one) == {"rounds": 0, "calls": 0, "seconds": 0.4, "thin": False}
    two = one + [{"hits": 0, "thin": True, "rounds": 2, "calls": 3, "seconds": 6.0}]
    assert rs.look_of(two) == {"rounds": 2, "calls": 3, "seconds": 6.4, "thin": False}
    assert rs.look_of([{"thin": True}, {"thin": True}])["thin"] is True   # every search thin


def _turn_ctx(*, facts=(), hits=()):
    return SimpleNamespace(relevant_facts=list(facts), recent_memory_hits=list(hits))


BAND = [
    {"line": "- [Oct 1] Robin flies kites on Saturdays", "item_id": "m1", "source_kind": "memory_bullet"},
    {"key": "hobby", "value": "kites", "memory_type": "user_fact", "id": 4},
    {"key": "empty", "value": "  ", "memory_type": "user_fact", "id": 5},
]
TODAY = [
    {"key": "plan", "value": "plan: kite shop Saturday", "memory_type": "today", "id": 9},
    {"key": "mood", "value": "tired after work", "memory_type": "today", "id": 12},
]


def test_context_sources_are_what_her_prompt_carried(monkeypatch):
    from core.turn import companion_flags

    monkeypatch.setattr(companion_flags, "today_slot_on", lambda: True)
    ctx = _turn_ctx(facts=BAND + TODAY, hits=["Robin likes kites", " ", "kite shop on Elm", "a", "b"])
    got = rs.context_sources(ctx, shown=True)
    assert got["notes"] == ["Robin likes kites", "kite shop on Elm"]            # the first three hits, as rendered
    assert got["facts"] == ["[Oct 1] Robin flies kites on Saturdays", "hobby: kites"]
    assert got["today"] == ["tired after work", "plan: kite shop Saturday"]     # newest first


def test_today_rows_are_left_out_while_the_slot_is_off():
    got = rs.context_sources(_turn_ctx(facts=BAND + TODAY), shown=True)
    assert got["today"] == [] and got["facts"] == ["[Oct 1] Robin flies kites on Saturdays", "hobby: kites"]


def test_a_guests_turn_shows_none_of_the_owners_bands(monkeypatch):
    from core.turn import companion_flags

    monkeypatch.setattr(companion_flags, "today_slot_on", lambda: True)
    got = rs.context_sources(_turn_ctx(facts=BAND + TODAY, hits=["Robin likes kites"]), shown=False)
    assert got == {"notes": [], "facts": [], "today": []}


def test_context_sources_take_a_context_with_nothing():
    assert rs.context_sources(SimpleNamespace(), shown=True) == {"notes": [], "facts": [], "today": []}
    assert rs.context_sources(None, shown=True) == {"notes": [], "facts": [], "today": []}


def test_who_name_is_the_doors_display_name():
    dc = SimpleNamespace(metadata=SimpleNamespace(discord_username="robin_example", extra={}))
    tg = SimpleNamespace(metadata=SimpleNamespace(discord_username=None,
                                                  extra={"telegram_username": "dana_example"}))
    assert rs.who_name(dc) == "robin_example"
    assert rs.who_name(tg) == "dana_example"
    assert rs.who_name(SimpleNamespace(metadata=None)) == ""


# ── retention ─────────────────────────────────────────────────────────────

def _day_file(folder: Path, day: str, text: str = "{}\n") -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{day}.jsonl"
    path.write_text(text, encoding="utf-8")
    return path


def test_prune_removes_day_files_more_than_fourteen_days_old(folder):
    assert rs.KEEP_DAYS == 14
    gone = [_day_file(folder, d) for d in ("2026-09-01", "2026-09-28")]
    kept = [_day_file(folder, d) for d in ("2026-09-29", "2026-10-12", "2026-10-13")]
    out = rs.prune(now=NOW.timestamp())
    assert out == {"removed": 2}
    assert not any(p.exists() for p in gone) and all(p.exists() for p in kept)


def test_prune_leaves_anything_that_is_not_a_day_file_alone(folder):
    other = [_day_file(folder, "notes"), _day_file(folder, "2026-01-01.bak")]
    (folder / "2026-01-02.jsonl").mkdir()                       # a folder with a day's name
    target = _day_file(folder.parent, "2026-01-03")
    os.symlink(target, folder / "2026-01-03.jsonl")             # a link with a day's name
    assert rs.prune(now=NOW.timestamp()) == {"removed": 0}
    assert all(p.exists() for p in other)
    assert (folder / "2026-01-02.jsonl").is_dir()
    assert (folder / "2026-01-03.jsonl").is_symlink() and target.exists()


def test_prune_with_no_folder_removes_nothing(folder):
    assert rs.prune(now=NOW.timestamp()) == {"removed": 0}
    assert not folder.exists()


def test_a_file_that_cannot_be_removed_is_named_and_the_rest_still_go(folder, monkeypatch):
    stuck = _day_file(folder, "2026-09-01")
    goes = _day_file(folder, "2026-09-02")
    real_unlink = Path.unlink

    def picky(self, *a, **kw):
        if self.name == stuck.name:
            raise PermissionError("Operation not permitted")
        return real_unlink(self, *a, **kw)

    monkeypatch.setattr(Path, "unlink", picky)
    out = rs.prune(now=NOW.timestamp())
    assert stuck.exists() and not goes.exists()
    assert out["removed"] == 1 and "1 could not be removed" in out["error"]
    assert "PermissionError" in out["error"]


def test_the_record_folder_is_git_ignored():
    """It holds the owner's private lines; nothing of it is ever tracked or shipped."""
    repo = Path(__file__).resolve().parents[2]
    assert "data/reply_sources/" in (repo / ".gitignore").read_text(encoding="utf-8").splitlines()
