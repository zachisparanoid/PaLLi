# tests/ai/test_daydream_engine.py
"""The engine's wake machinery (re-spec §4.1/§4.6): occasions wake it,
the gap defers-never-cancels, the heartbeat fires only in quiet, the
daily max holds, 0 = unlimited, WISHES.md mtime is an occasion."""
from __future__ import annotations

import os
import time
from types import SimpleNamespace
from unittest.mock import patch

import ai.daydream as dd


def _comp(**kw):
    base = {"wanting": True, "daydream_heartbeat_minutes": 180,
            "daydream_min_gap_minutes": 45, "daydream_daily_max": 8}
    base.update(kw)
    return SimpleNamespace(**base)


def _engine(comp):
    eng = dd.DaydreamEngine()
    eng.is_running = True
    patcher = patch.object(dd.DaydreamEngine, "_settings", lambda self: comp)
    patcher.start()
    return eng, patcher


def test_notice_is_safe_when_engine_down():
    dd.daydream_engine.is_running = False
    dd.notice("wish_planted")            # must not raise, must not queue
    assert len(dd.daydream_engine._pending) == 0


def test_event_fires_after_gap_and_defers_inside_it(tmp_path):
    comp = _comp()
    eng, p = _engine(comp)
    try:
        eng.last_tick_at = time.time()            # inside the gap
        eng._ingest("wish_planted", {})
        fire, woke = eng._decide(time.time())
        assert fire is False
        assert eng._deferred_wake_at is not None  # deferred, never cancelled
        eng.last_tick_at = time.time() - 46 * 60  # gap expired
        fire, woke = eng._decide(time.time())
        assert fire is True
        assert woke and woke[0]["kind"] == "wish_planted"
    finally:
        p.stop()


def test_heartbeat_fires_only_in_quiet(tmp_path):
    comp = _comp(daydream_heartbeat_minutes=1)
    eng, p = _engine(comp)
    try:
        eng.last_tick_at = 0.0
        eng._last_heartbeat_at = time.time() - 120
        fire, woke = eng._decide(time.time())
        assert fire is True and woke[0]["kind"] == "heartbeat"
        eng._last_heartbeat_at = time.time()
        fire, _ = eng._decide(time.time())
        assert fire is False
    finally:
        p.stop()


def test_daily_max_holds_and_zero_is_unlimited(tmp_path):
    eng, p = _engine(_comp(daydream_daily_max=2))
    try:
        with patch.object(dd, "_count_today", return_value=2):
            eng.last_tick_at = 0.0
            eng._ingest("wish_planted", {})
            fire, _ = eng._decide(time.time())
            assert fire is False                   # cap holds the bundle
            assert len(eng._pending) == 1          # lossless
    finally:
        p.stop()
    eng2, p2 = _engine(_comp(daydream_daily_max=0))
    try:
        with patch.object(dd, "_count_today", return_value=999):
            eng2.last_tick_at = 0.0
            eng2._ingest("wish_planted", {})
            fire, _ = eng2._decide(time.time())
            assert fire is True                    # 0 = unlimited
    finally:
        p2.stop()


def test_wishes_mtime_poll_notices_change(tmp_path):
    eng, p = _engine(_comp())
    try:
        wishes = tmp_path / "WISHES.md"
        wishes.write_text("- a wish\n", encoding="utf-8")
        with patch.object(dd, "_wishes_file_path", return_value=wishes):
            eng._poll_wishes_mtime()               # first sight: baseline
            assert len(eng._pending) == 0
            wishes.write_text("- a wish\n- another\n", encoding="utf-8")
            # Force a distinct mtime: under full-suite load two writes can
            # land inside the clock's granularity and compare equal (one
            # flake observed 2026-08-08). The poll compares mtimes, not
            # contents — make the timestamp change explicit.
            baseline = wishes.stat().st_mtime
            os.utime(wishes, (baseline + 2, baseline + 2))
            eng._poll_wishes_mtime()
            assert [e["kind"] for e in eng._pending] == ["wish_planted"]
    finally:
        p.stop()


def test_flag_off_engine_decides_nothing(tmp_path):
    eng, p = _engine(_comp(wanting=False))
    try:
        eng._ingest("wish_planted", {})
        fire, _ = eng._decide(time.time())
        assert fire is False
        assert len(eng._pending) == 0              # drained quietly
    finally:
        p.stop()
