"""Learned quiet hours (step 22): her model of his absence rhythm.

Deterministic code, no LLM — a send-blocking gate is budget/gate territory
(owner law 2026-07-16). The learner folds HIS message timestamps into 48
LOCAL-time buckets (hour x weekday/weekend) and marks a bucket quiet only
with overwhelming evidence: almost never active there, across enough
observed days. Mislearning degrades to "no change", never to silence.

Spec: docs/superpowers/specs/2026-08-10-learned-quiet-hours-design.md
Owner decisions 2026-08-10: arbiter sends only; asleep-or-gone only;
confident-only; toggleable, default OFF ("approve, but make it toggleable").
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Tuple

RHYTHM_FILE = Path("data/rhythm/owner_rhythm.json")
DB_FILE = Path("data/palli-local.sqlite3")
STALE_AFTER_DAYS = 8  # regenerates nightly; 8 days silent = broken hook


def bucket_key(dt: datetime) -> str:
    kind = "weekday" if dt.weekday() < 5 else "weekend"
    return f"{kind}-{dt.hour:02d}"


def compute_rhythm(
    timestamps,
    *,
    now: float,
    lookback_days: int = 56,
    max_active_ratio: float = 0.08,
    min_weekday_days: int = 15,
    min_weekend_days: int = 6,
) -> dict:
    """Fold his message timestamps into the 48-bucket belief table.

    Local time throughout (same clock the manual quiet window uses).
    Observed days = whole calendar days in the lookback ending today.
    """
    end_day = datetime.fromtimestamp(float(now)).date()
    start_day = end_day - timedelta(days=int(lookback_days) - 1)

    observed = {"weekday": 0, "weekend": 0}
    day = start_day
    while day <= end_day:
        observed["weekday" if day.weekday() < 5 else "weekend"] += 1
        day += timedelta(days=1)

    active: dict = {}
    kinds_with_data = {"weekday": False, "weekend": False}
    for t in timestamps:
        dt = datetime.fromtimestamp(float(t))
        if not (start_day <= dt.date() <= end_day):
            continue
        active.setdefault(bucket_key(dt), set()).add(dt.date())
        kinds_with_data["weekday" if dt.weekday() < 5 else "weekend"] = True

    floors = {"weekday": int(min_weekday_days), "weekend": int(min_weekend_days)}
    buckets = {}
    for kind in ("weekday", "weekend"):
        for hour in range(24):
            key = f"{kind}-{hour:02d}"
            a = len(active.get(key, ()))
            o = observed[kind]
            quiet = (
                # No evidence stream for this day kind = no beliefs for it:
                # an all-zero kind means the pipe is dry (fresh install, DB
                # reset, gather bug), never "confidently asleep all day".
                kinds_with_data[kind]
                and o >= floors[kind]
                and (a / o if o else 1.0) <= float(max_active_ratio)
            )
            buckets[key] = {"active_days": a, "observed_days": o, "quiet": quiet}

    return {
        "generated_at": datetime.fromtimestamp(float(now)).isoformat(
            timespec="seconds"
        ),
        "lookback_days": int(lookback_days),
        "source": "conversations.role=user",
        "buckets": buckets,
    }


def gather_his_timestamps(
    db_path: Path, *, now: float, lookback_days: int
) -> list:
    """HIS message timestamps inside the lookback, read-only.

    Opens its own short-lived read connection (URI mode=ro) so it works
    from the night loop (via asyncio.to_thread) and the preview script
    alike, without touching the brain's connection.
    """
    cutoff = (
        datetime.fromtimestamp(float(now)).date()
        - timedelta(days=int(lookback_days) - 1)
    )
    cutoff_ts = datetime(cutoff.year, cutoff.month, cutoff.day).timestamp()
    conn = sqlite3.connect(f"file:{Path(db_path).as_posix()}?mode=ro", uri=True)
    try:
        rows = conn.execute(
            "SELECT timestamp FROM conversations"
            " WHERE role = 'user' AND timestamp >= ? AND timestamp <= ?"
            " ORDER BY timestamp",
            (cutoff_ts, float(now)),
        ).fetchall()
    finally:
        conn.close()
    return [float(r[0]) for r in rows]


def refresh_rhythm_file(
    *,
    now: float,
    db_path: Optional[Path] = None,
    out_path: Optional[Path] = None,
    lookback_days: int = 56,
    max_active_ratio: float = 0.08,
    min_weekday_days: int = 15,
    min_weekend_days: int = 6,
) -> dict:
    """Gather -> compute -> replace the whole file. Idempotent; no partial
    states (write to a temp name, then atomic replace).

    db_path/out_path default to None and are resolved against the module
    constants HERE, at call time — not as parameter defaults, which Python
    binds once at def time and would freeze whatever DB_FILE/RHYTHM_FILE
    pointed to at import. That froze out hermetic rotation: a test (or the
    probe battery) monkeypatching owner_rhythm.DB_FILE/RHYTHM_FILE had no
    effect on a caller (e.g. night_cycle's nightly hook) that omits the
    kwargs and leans on the defaults."""
    db_path = Path(db_path) if db_path is not None else DB_FILE
    out_path = Path(out_path) if out_path is not None else RHYTHM_FILE
    stamps = gather_his_timestamps(db_path, now=now, lookback_days=lookback_days)
    rhythm = compute_rhythm(
        stamps,
        now=now,
        lookback_days=lookback_days,
        max_active_ratio=max_active_ratio,
        min_weekday_days=min_weekday_days,
        min_weekend_days=min_weekend_days,
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(rhythm, indent=1), encoding="utf-8")
    tmp.replace(out_path)
    return rhythm


def load_current_verdict(
    path: Path, *, now: float
) -> Optional[Tuple[bool, str]]:
    """(quiet, evidence) for the CURRENT local hour, or None = stand down.

    None on missing, corrupt, or stale (spec §2: fail-open toward current
    behavior — a broken learner must never silence her)."""
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        generated = datetime.fromisoformat(str(payload["generated_at"]))
        if datetime.fromtimestamp(float(now)) - generated > timedelta(
            days=STALE_AFTER_DAYS
        ):
            return None
        key = bucket_key(datetime.fromtimestamp(float(now)))
        b = payload["buckets"][key]
        evidence = (
            f"{key}: active {int(b['active_days'])}/{int(b['observed_days'])} days"
        )
        return (bool(b["quiet"]), evidence)
    except Exception:
        return None
