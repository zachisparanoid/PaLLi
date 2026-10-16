"""DEFECT_RECORD §19 (2026-09-02): the inbox — where a non-text file he sends
her lands so her hands can reach it.

Intake used to drop anything that was not an image, a voice note, or a
text-by-suffix file; the row went empty and she never knew. Now every such
file is downloaded once into data/inbox/ and the turn carries a line that
names the file, its path, its size and type — the same first-line-is-the-
filename shape persistence already bakes into history, so a later turn
still knows where the file is.
"""
from __future__ import annotations

import asyncio
from datetime import datetime

import pytest

from core.turn import inbox
from core.turn.types import TextAttachmentRef


def _ref(filename="budget.xlsx", mime="application/vnd.ms-excel", size=12345, url="https://cdn/x"):
    return TextAttachmentRef(url=url, filename=filename, mime=mime, size_bytes=size)


async def _fetch_ok(url):
    return b"PK\x03\x04 fake xlsx bytes"


async def _fetch_none(url):
    return None


def test_a_file_is_saved_under_the_inbox_and_the_line_names_where_it_went(tmp_path):
    line = asyncio.run(inbox.receive_file(_ref(), fetch=_fetch_ok, root=tmp_path,
                                          now=lambda: datetime(2026, 9, 2, 16, 4, 5)))
    first, rest = line.split("\n", 1)
    assert first == "budget.xlsx"                                   # persistence's first-line convention
    saved = list(tmp_path.iterdir())
    assert len(saved) == 1 and saved[0].read_bytes().startswith(b"PK")
    assert str(saved[0]) in rest and "budget.xlsx" in saved[0].name
    assert "application/vnd.ms-excel" in rest and "12345" in rest
    assert "local_shell" in rest                                    # she is told which hand reaches it


def test_two_files_with_the_same_name_never_overwrite_each_other(tmp_path):
    fixed = lambda: datetime(2026, 9, 2, 16, 4, 5)
    a = asyncio.run(inbox.receive_file(_ref(), fetch=_fetch_ok, root=tmp_path, now=fixed))
    b = asyncio.run(inbox.receive_file(_ref(), fetch=_fetch_ok, root=tmp_path, now=fixed))
    assert a != b and len(list(tmp_path.iterdir())) == 2


def test_a_hostile_filename_cannot_escape_the_inbox(tmp_path):
    asyncio.run(inbox.receive_file(_ref(filename="../../etc/passwd"), fetch=_fetch_ok, root=tmp_path))
    assert list(tmp_path.iterdir()) and all(p.parent == tmp_path for p in tmp_path.rglob("*"))
    assert not (tmp_path.parent.parent / "etc" / "passwd").exists()


def test_an_oversized_file_is_not_downloaded_and_the_line_says_so(tmp_path, monkeypatch):
    monkeypatch.setattr(inbox, "INBOX_MAX_BYTES", 1000)
    calls = []

    async def fetch(url):
        calls.append(url)
        return b"x"

    line = asyncio.run(inbox.receive_file(_ref(size=5000), fetch=fetch, root=tmp_path))
    assert not calls and not list(tmp_path.iterdir())
    assert line.startswith("budget.xlsx\n") and "not downloaded" in line and "5000" in line


def test_a_failed_download_is_an_honest_line_not_a_silence(tmp_path):
    line = asyncio.run(inbox.receive_file(_ref(), fetch=_fetch_none, root=tmp_path))
    assert line.startswith("budget.xlsx\n") and "failed" in line and not list(tmp_path.iterdir())


def test_the_inbox_root_is_data_inbox_unless_overridden(monkeypatch):
    monkeypatch.delenv("PALLI_INBOX_DIR", raising=False)
    assert inbox.inbox_root().as_posix().endswith("data/inbox")
    monkeypatch.setenv("PALLI_INBOX_DIR", "/tmp/elsewhere")
    assert inbox.inbox_root().as_posix() == "/tmp/elsewhere"
