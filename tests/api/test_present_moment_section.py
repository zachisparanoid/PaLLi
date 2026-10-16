"""Interiority Moment panel — the honesty ladder (off / empty / error).

``_moment_section`` lazily imports the store readers inside the function,
so patching ``ai.present_moment_store.read_current``/``read_recent``
intercepts them. Every patched reader forces ``root=tmp_path``: the live
``data/present_moment/`` tree is never read or written.
"""
from __future__ import annotations

import json
import time

import pytest

from api.web_interiority import _moment_section


def _redirect_readers(monkeypatch, root):
    import ai.present_moment_store as store

    real_current, real_recent = store.read_current, store.read_recent
    monkeypatch.setattr(store, "read_current", lambda **kw: real_current(**kw, root=root))
    monkeypatch.setattr(store, "read_recent", lambda **kw: real_recent(**kw, root=root))


def test_moment_section_flag_off_reads_nothing(monkeypatch):
    import ai.present_moment_store as store

    def boom(**kw):
        raise AssertionError("store read while the organ is disabled")

    monkeypatch.setattr(store, "read_current", boom)
    monkeypatch.setattr(store, "read_recent", boom)

    now = time.time()
    section = _moment_section(since=now - 86400.0, now=now, enabled=False)
    assert section == {"enabled": False, "current": None, "recent": []}


def test_moment_section_empty_store_is_an_honest_zero(monkeypatch, tmp_path):
    _redirect_readers(monkeypatch, tmp_path)
    now = time.time()
    section = _moment_section(since=now - 86400.0, now=now, enabled=True)
    assert section["enabled"] is True
    assert section["current"] is None
    assert section["recent"] == []
    assert "error" not in section  # absence, not failure


def test_moment_section_reports_error_when_a_reader_raises(monkeypatch):
    import ai.present_moment_store as store

    def raiser(**kw):
        raise OSError("store unreadable")

    monkeypatch.setattr(store, "read_current", raiser)
    now = time.time()
    section = _moment_section(since=now - 86400.0, now=now, enabled=True)
    assert section["error"] is True
    assert section["current"] is None  # error is never dressed up as absence


def test_moment_section_serializes_current_and_windows_recent(monkeypatch, tmp_path):
    from ai.present_moment_store import write_moment

    now = time.time()
    day = time.strftime("%Y-%m-%d", time.localtime(now))
    # An out-of-window entry, hand-placed so its timestamp is exact.
    with (tmp_path / f"{day}.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(
            json.dumps(
                {"text": "ten days ago", "written_at": now - 10 * 86400.0, "meta": {}}
            )
            + "\n"
        )
    write_moment(
        text="Standing here, aware he stepped away.",
        meta={
            "woke_on": [{"kind": "owner_arrived_after_absence", "count": 1}],
            "impulse": {"kind": "say", "why": "he has been gone a while"},
            "sense_health": {"conversation": "ok"},
        },
        root=tmp_path,
    )
    _redirect_readers(monkeypatch, tmp_path)

    section = _moment_section(since=now - 86400.0, now=now, enabled=True)

    current = section["current"]
    assert current["text"] == "Standing here, aware he stepped away."
    assert current["stale"] is False
    assert current["age_seconds"] == pytest.approx(0.0, abs=60.0)
    assert current["impulse"]["kind"] == "say"
    assert current["woke_on"][0]["kind"] == "owner_arrived_after_absence"
    assert current["sense_health"] == {"conversation": "ok"}
    # The ten-days-ago entry exists on disk but falls outside `since`.
    assert [e["text"] for e in section["recent"]] == [
        "Standing here, aware he stepped away."
    ]
    assert section["recent"][0]["impulse"] == "say"
    assert section["recent"][0]["impulse_why"] == "he has been gone a while"
