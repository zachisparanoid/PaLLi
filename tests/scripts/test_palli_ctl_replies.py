"""`palli_ctl replies [--days N] [--last N]` (R15, memory that reaches, T13):
what each reply drew on, read from ``data/reply_sources/`` with nothing
running — the same record as the dashboards' /replies page. Plain lines:
``ts · who · reply · N record lines · N notes · N facts`` per reply, then what it
drew on, one item a line.

Every record here is a tmp folder (``PALLI_REPLY_SOURCES_DIR``). Stand-in
names only."""
from __future__ import annotations

import importlib.util
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from core import reply_sources as rs

REPO_ROOT = Path(__file__).resolve().parents[2]
CTL = REPO_ROOT / "scripts" / "palli_ctl.py"


@pytest.fixture(scope="module")
def ctl():
    spec = importlib.util.spec_from_file_location("palli_ctl_replies_test_module", CTL)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def folder(tmp_path, monkeypatch) -> Path:
    path = tmp_path / "reply_sources"
    monkeypatch.setenv("PALLI_REPLY_SOURCES_DIR", str(path))
    return path


def _seed(now: datetime) -> None:
    rows = (
        (2, "Robin", True, "two days ago", ["[Oct 5 19:02] them: kites"], [], [], ["kites"]),
        (0, "Robin", True, "The kite shop opens at nine.",
         ["[Oct 5 19:02] them: the kite shop on Elm opens at nine", "[Oct 5 19:03] me: noted"],
         ["Robin likes kites"], ["hobby: kites", "town: Elmside"], ["kite shop", "Elm"]),
        (0, "dana_example", False, "Hi Dana!", [], [], [], []),
    )
    for back, who, owner, reply, lines, notes, facts, queries in rows:
        assert rs.record(
            door="discord_text", conversation="discord:111", who_name=who, owner=owner,
            reply_excerpt=reply, record_lines=lines, notes=notes, facts=facts, today=[],
            room_lines=[], queries=queries,
            look={"rounds": 1, "calls": 3, "seconds": 6.2, "thin": True} if queries else None,
            now=now - timedelta(days=back, seconds=len(reply)))


def _run(ctl, *argv) -> list[str]:
    lines: list[str] = []
    args = ctl.build_parser().parse_args(["replies", *argv])
    assert ctl.run_replies_command(args, out=lines.append) == 0
    return lines


def test_the_parser_takes_days_and_last(ctl):
    args = ctl.build_parser().parse_args(["replies"])
    assert args.command == "replies" and args.days == 3 and args.last is None
    args = ctl.build_parser().parse_args(["replies", "--days", "7", "--last", "2"])
    assert args.days == 7 and args.last == 2


def test_each_reply_is_one_summary_line_then_what_it_drew_on(ctl, folder):
    _seed(datetime.now())
    lines = _run(ctl)
    summaries = [line for line in lines if not line.startswith("    ")]
    assert len(summaries) == 3
    assert summaries[1].endswith(
        ' · Robin · "The kite shop opens at nine." · 2 record lines · 1 note · 2 facts')
    assert summaries[2].endswith(' · dana_example · "Hi Dana!" · 0 record lines · 0 notes · 0 facts')
    start = lines.index(summaries[1])
    assert lines[start + 1:start + 8] == [
        '    searched: "kite shop" · "Elm"',
        "    look: 1 round, 3 calls, 6.2 s, thin",
        "    record: [Oct 5 19:02] them: the kite shop on Elm opens at nine",
        "    record: [Oct 5 19:03] me: noted",
        "    note: Robin likes kites",
        "    fact: hobby: kites",
        "    fact: town: Elmside",
    ]


def test_days_narrows_and_last_keeps_the_newest(ctl, folder):
    _seed(datetime.now())
    one_day = [line for line in _run(ctl, "--days", "1") if not line.startswith("    ")]
    assert len(one_day) == 2 and "two days ago" not in "\n".join(one_day)
    last = [line for line in _run(ctl, "--last", "1") if not line.startswith("    ")]
    assert len(last) == 1 and '"Hi Dana!"' in last[0]


def test_an_empty_record_says_so(ctl, folder):
    assert _run(ctl) == ["No replies recorded in the last 3 days."]
    assert _run(ctl, "--days", "1") == ["No replies recorded in the last 1 day."]


def test_the_dispatch_reaches_the_command(ctl, folder, monkeypatch, capsys):
    _seed(datetime.now())
    assert ctl.main(["replies", "--last", "1"]) == 0
    assert '"Hi Dana!"' in capsys.readouterr().out


def test_palli_ctl_stays_crlf_and_keeps_its_memory_commands():
    data = CTL.read_bytes()
    assert b"\r\n" in data
    assert data.count(b"\n") == data.count(b"\r\n")          # no bare \n line
    assert b'"reach-build",' in data and b'"reach-status",' in data
