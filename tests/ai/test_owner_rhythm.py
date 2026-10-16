"""Learned quiet hours (step 22) — the learner and the verdict loader.

Spec: docs/superpowers/specs/2026-08-10-learned-quiet-hours-design.md
All clocks frozen; the learner is pure code and must be exactly
reproducible from its inputs.
"""
import json
from datetime import datetime, timedelta
from pathlib import Path

from ai.owner_rhythm import bucket_key, compute_rhythm

# Frozen anchor: Monday 2026-08-10 12:00 local. 56-day lookback covers
# 2026-06-16 .. 2026-08-10 = 40 weekdays + 16 weekend day exactly.
NOW = datetime(2026, 8, 10, 12, 0, 0)
NOW_TS = NOW.timestamp()


def ts(y, m, d, h):
    return datetime(y, m, d, h, 30, 0).timestamp()


def test_bucket_key_splits_weekday_weekend():
    assert bucket_key(datetime(2026, 8, 10, 9, 5)) == "weekday-09"   # Monday
    assert bucket_key(datetime(2026, 8, 9, 2, 59)) == "weekend-02"   # Sunday
    assert bucket_key(datetime(2026, 8, 8, 23, 0)) == "weekend-23"   # Saturday


def test_observed_days_split_exactly():
    r = compute_rhythm([], now=NOW_TS)
    assert r["lookback_days"] == 56
    assert r["buckets"]["weekday-09"]["observed_days"] == 40
    assert r["buckets"]["weekend-09"]["observed_days"] == 16


def test_active_days_count_distinct_days_not_messages():
    # Three messages the SAME Tuesday 21:xx hour = one active day.
    stamps = [ts(2026, 8, 4, 21)] * 3
    r = compute_rhythm(stamps, now=NOW_TS)
    assert r["buckets"]["weekday-21"]["active_days"] == 1


def test_quiet_requires_low_ratio_AND_enough_observation():
    # weekday-09 active on 2 of 40 days -> ratio 0.05 <= 0.08 -> quiet.
    stamps = [ts(2026, 7, 7, 9), ts(2026, 7, 14, 9)]
    r = compute_rhythm(stamps, now=NOW_TS)
    b = r["buckets"]["weekday-09"]
    assert (b["active_days"], b["observed_days"], b["quiet"]) == (2, 40, True)
    # 4 of 40 -> ratio 0.10 > 0.08 -> not quiet.
    stamps += [ts(2026, 7, 21, 9), ts(2026, 7, 28, 9)]
    r = compute_rhythm(stamps, now=NOW_TS)
    assert r["buckets"]["weekday-09"]["quiet"] is False


def test_quiet_at_exactly_the_0_08_ratio_boundary():
    # lookback_days=35 from the frozen NOW anchor -> 25 observed weekdays
    # (2026-07-07 .. 2026-08-10 inclusive). 2 active weekdays in one hour
    # -> 2/25 = 0.08 exactly -> the <= boundary reads quiet True (spec §5.1).
    stamps = [ts(2026, 7, 7, 9), ts(2026, 7, 8, 9)]
    r = compute_rhythm(stamps, now=NOW_TS, lookback_days=35)
    b = r["buckets"]["weekday-09"]
    assert (b["active_days"], b["observed_days"]) == (2, 25)
    assert b["quiet"] is True


def test_insufficient_observation_is_never_quiet():
    # min_weekday_days above the 40 observed -> confident-only rule bites.
    r = compute_rhythm([], now=NOW_TS, min_weekday_days=41)
    assert r["buckets"]["weekday-04"]["quiet"] is False
    # weekend floor likewise (16 observed < 17).
    r = compute_rhythm([], now=NOW_TS, min_weekend_days=17)
    assert r["buckets"]["weekend-04"]["quiet"] is False


def test_stamps_outside_lookback_are_ignored():
    old = ts(2026, 6, 1, 9)          # before the 56-day window
    r = compute_rhythm([old], now=NOW_TS)
    assert r["buckets"]["weekday-09"]["active_days"] == 0


def test_output_is_deterministic_and_json_shaped():
    stamps = [ts(2026, 8, 4, 21), ts(2026, 8, 5, 3)]
    a = compute_rhythm(stamps, now=NOW_TS)
    b = compute_rhythm(list(reversed(stamps)), now=NOW_TS)
    assert a == b
    assert a["source"] == "conversations.role=user"
    assert set(a["buckets"]) == {
        f"{kind}-{h:02d}" for kind in ("weekday", "weekend") for h in range(24)
    }
    json.dumps(a)  # must be serializable as-is


import sqlite3

from ai.owner_rhythm import (
    gather_his_timestamps,
    load_current_verdict,
    refresh_rhythm_file,
)


def _seed_db(path, rows):
    conn = sqlite3.connect(str(path))
    conn.execute(
        "CREATE TABLE conversations (id INTEGER PRIMARY KEY, role TEXT,"
        " timestamp REAL, content TEXT)"
    )
    conn.executemany(
        "INSERT INTO conversations (role, timestamp, content) VALUES (?, ?, ?)",
        rows,
    )
    conn.commit()
    conn.close()


def test_gather_reads_only_his_rows_inside_lookback(tmp_path):
    db = tmp_path / "t.sqlite3"
    _seed_db(db, [
        ("user", ts(2026, 8, 4, 21), "hey"),
        ("assistant", ts(2026, 8, 4, 22), "hi"),          # not his
        ("user", ts(2026, 5, 1, 9), "ancient"),           # outside lookback
    ])
    got = gather_his_timestamps(db, now=NOW_TS, lookback_days=56)
    assert got == [ts(2026, 8, 4, 21)]


def test_refresh_writes_whole_file_idempotently(tmp_path):
    db = tmp_path / "t.sqlite3"
    _seed_db(db, [("user", ts(2026, 8, 4, 21), "hey")])
    out = tmp_path / "rhythm" / "owner_rhythm.json"
    r1 = refresh_rhythm_file(now=NOW_TS, db_path=db, out_path=out)
    r2 = refresh_rhythm_file(now=NOW_TS, db_path=db, out_path=out)
    assert r1 == r2
    on_disk = json.loads(out.read_text(encoding="utf-8"))
    assert on_disk == r1
    assert on_disk["buckets"]["weekday-21"]["active_days"] == 1


def _write_rhythm(tmp_path, *, quiet_key="weekday-09", generated=NOW):
    r = compute_rhythm([], now=NOW_TS)  # every bucket quiet=... default evidence
    r["buckets"][quiet_key] = {"active_days": 2, "observed_days": 40, "quiet": True}
    r["generated_at"] = generated.isoformat(timespec="seconds")
    p = tmp_path / "owner_rhythm.json"
    p.write_text(json.dumps(r), encoding="utf-8")
    return p


def test_verdict_quiet_with_evidence(tmp_path):
    p = _write_rhythm(tmp_path)
    # NOW is Monday 12:00 -> bucket weekday-12; force the quiet bucket instead:
    monday_9am = datetime(2026, 8, 10, 9, 30).timestamp()
    quiet, evidence = load_current_verdict(p, now=monday_9am)
    assert quiet is True
    assert evidence == "weekday-09: active 2/40 days"


def test_verdict_not_quiet_bucket(tmp_path):
    p = _write_rhythm(tmp_path)
    quiet, evidence = load_current_verdict(p, now=NOW_TS)  # weekday-12
    assert quiet is False


def test_verdict_stands_down_on_missing_corrupt_stale(tmp_path):
    missing = tmp_path / "nope.json"
    assert load_current_verdict(missing, now=NOW_TS) is None

    corrupt = tmp_path / "bad.json"
    corrupt.write_text("{not json", encoding="utf-8")
    assert load_current_verdict(corrupt, now=NOW_TS) is None

    stale = _write_rhythm(tmp_path, generated=NOW - timedelta(days=9))
    assert load_current_verdict(stale, now=NOW_TS) is None


def test_data_in_one_kind_does_not_unlock_quiet_in_the_other():
    """One weekday message must not let WEEKEND buckets go quiet — the
    no-evidence-no-beliefs guard is scoped per day kind (spec §4)."""
    stamps = [ts(2026, 8, 4, 21)]          # a single Tuesday message
    r = compute_rhythm(stamps, now=NOW_TS)
    assert r["buckets"]["weekend-09"]["quiet"] is False       # no weekend data
    assert r["buckets"]["weekday-09"]["quiet"] is True        # weekday data exists


def test_knobs_exist_in_schema_with_spec_defaults():
    from core.settings_schema import CompanionConfig

    c = CompanionConfig()
    assert c.arbiter_learned_quiet is False        # toggleable, DEFAULT OFF
    assert c.rhythm_refresh is True                # its own switch (owner call 7), DEFAULT ON
    assert c.rhythm_lookback_days == 56
    assert c.rhythm_quiet_max_active_ratio == 0.08
    assert c.rhythm_min_observed_weekdays == 15
    assert c.rhythm_min_observed_weekend_days == 6
    assert c.rhythm_present_override_minutes == 90


def test_knobs_exist_in_yaml_too():
    """Schema uses extra='forbid': a yaml-only key breaks her boot, and a
    schema-only key silently never reaches her yaml. Same-commit lockstep."""
    text = Path("data/palli.yaml").read_text(encoding="utf-8")
    for key in (
        "arbiter_learned_quiet",
        "rhythm_lookback_days",
        "rhythm_quiet_max_active_ratio",
        "rhythm_min_observed_weekdays",
        "rhythm_min_observed_weekend_days",
        "rhythm_present_override_minutes",
    ):
        assert key in text, key
