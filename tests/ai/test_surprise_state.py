# tests/ai/test_surprise_state.py
"""The surprising window (spec §4): a REAL surprise opens a window; entries
written inside it are remembered harder. File-backed (restart-safe), tiny,
never raises."""
from types import SimpleNamespace

import ai.surprise_state as ss


def _pin(monkeypatch, tmp_path, minutes=60):
    monkeypatch.setattr(ss, "STATE_PATH", tmp_path / "surprise_state.json")
    monkeypatch.setattr(ss, "_companion", lambda: SimpleNamespace(surprise_salience_minutes=minutes))


def test_real_surprise_opens_a_window_and_it_expires(monkeypatch, tmp_path):
    _pin(monkeypatch, tmp_path)
    assert ss.active_window(now=1000.0) is None
    ss.mark(about="him", weight="real", at=1000.0)
    w = ss.active_window(now=1000.0 + 59 * 60)
    assert w and w["about"] == "him" and w["at"] == 1000.0
    assert ss.active_window(now=1000.0 + 61 * 60) is None


def test_slight_marks_nothing(monkeypatch, tmp_path):
    _pin(monkeypatch, tmp_path)
    ss.mark(about="me", weight="slight", at=1000.0)
    assert ss.active_window(now=1000.0) is None


def test_survives_reload_and_corrupt_file(monkeypatch, tmp_path):
    _pin(monkeypatch, tmp_path)
    ss.mark(about="me", weight="real", at=2000.0)
    assert ss.active_window(now=2000.0)["about"] == "me"
    ss.STATE_PATH.write_text("not json", encoding="utf-8")
    assert ss.active_window(now=2000.0) is None   # corrupt = no window, never a raise
