# tests/ai/test_expectations.py
"""The expectations layer (spec §2): deterministic lines about what's USUAL
— his timing (rhythm file), his register (her own past reads), her own
cadence — every line a count or an age, never a verdict. Floors omit thin
lines; all-empty ⇒ "" (byte-absent); the meaning stays in her appraisal."""
import json
import time
from types import SimpleNamespace
from unittest.mock import patch

import ai.expectations as ex


def _pin_on(monkeypatch):
    monkeypatch.setattr(ex, "surprise_enabled", lambda: True)


def _rhythm(tmp_path, monkeypatch, active, observed, quiet=False, when=None):
    import ai.owner_rhythm as orh
    when = when or time.time()
    from datetime import datetime
    key = orh.bucket_key(datetime.fromtimestamp(when))
    buckets = {key: {"active_days": active, "observed_days": observed, "quiet": quiet}}
    p = tmp_path / "owner_rhythm.json"
    p.write_text(json.dumps({"generated_at": "x", "buckets": buckets}), encoding="utf-8")
    monkeypatch.setattr(ex, "RHYTHM_FILE", p)
    return when


def test_timing_line_usually_around_vs_usually_dark(monkeypatch, tmp_path):
    _pin_on(monkeypatch)
    now = _rhythm(tmp_path, monkeypatch, active=31, observed=40)
    with patch("core.owner_presence.render_line", return_value="Discord: online"):
        line = ex._timing_line(now=now)
    assert "31 of 40" in line and "usually around" in line and "Discord: online" in line
    now = _rhythm(tmp_path, monkeypatch, active=2, observed=40, quiet=True)
    with patch("core.owner_presence.render_line", return_value=""):
        line = ex._timing_line(now=now)
    assert "usually dark" in line


def test_timing_line_omitted_below_floor_or_no_file(monkeypatch, tmp_path):
    _pin_on(monkeypatch)
    # observed=4 is under BOTH observation floors (weekday 15, weekend 6),
    # so the line is omitted whichever day kind "now" falls on.
    now = _rhythm(tmp_path, monkeypatch, active=1, observed=4)
    assert ex._timing_line(now=now) == ""
    monkeypatch.setattr(ex, "RHYTHM_FILE", tmp_path / "missing.json")
    assert ex._timing_line(now=time.time()) == ""


def test_mood_lines_baseline_and_run(monkeypatch):
    _pin_on(monkeypatch)
    tags = ["content"] * 12 + ["curious"] * 9 + ["tired", "tired", "tired"]  # newest LAST 3 = tired run
    entries = [SimpleNamespace(sentiment={"tag": t}, timestamp=i) for i, t in enumerate(tags)]
    monkeypatch.setattr(ex, "_read_sentiment_entries", lambda: entries)
    lines = ex._mood_lines()
    joined = " ".join(lines)
    assert "content" in joined and "curious" in joined
    assert "3 in a row" in joined and "tired" in joined


def test_mood_lines_omitted_below_floor(monkeypatch):
    _pin_on(monkeypatch)
    monkeypatch.setattr(ex, "_read_sentiment_entries",
                        lambda: [SimpleNamespace(sentiment={"tag": "content"}, timestamp=1)] * 5)
    assert ex._mood_lines() == []   # < 20 tagged reads


def test_block_carries_contract_only_when_asked(monkeypatch, tmp_path):
    _pin_on(monkeypatch)
    now = _rhythm(tmp_path, monkeypatch, active=31, observed=40)
    monkeypatch.setattr(ex, "_mood_lines", lambda: [])
    monkeypatch.setattr(ex, "_cadence_lines", lambda now: [])
    with patch("core.owner_presence.render_line", return_value=""):
        block = ex.render_expectations_block(now=now, include_contract=True)
        prose = ex.render_expectations_block(now=now, include_contract=False)
    assert block.startswith("EXPECTATIONS") and block.endswith("\n\n")
    assert '"surprise"' in block and "never manufacture" in block.lower()
    assert '"surprise"' not in prose and prose.startswith("EXPECTATIONS")


def test_flag_off_or_all_empty_is_byte_absent(monkeypatch, tmp_path):
    monkeypatch.setattr(ex, "surprise_enabled", lambda: False)
    assert ex.render_expectations_block() == ""
    _pin_on(monkeypatch)
    monkeypatch.setattr(ex, "RHYTHM_FILE", tmp_path / "missing.json")
    monkeypatch.setattr(ex, "_mood_lines", lambda: [])
    monkeypatch.setattr(ex, "_cadence_lines", lambda now: [])
    assert ex.render_expectations_block() == ""
