"""The 08-02 reflection regeneration driver (R2's last piece, 2026-08-05).

The 2026-08-02 nightly reflection was truncated by the D1 reasoning burn to a
54-char mid-clause fragment and stored as a success. Its inputs survive (the
day-log and 24 monologue entries for that date), so it can be honestly
regenerated — with three non-negotiables this suite pins:

1. Material is gathered FOR THE TARGET DATE. The live engine's helper reads
   monologue "recent relative to now", which would poison a backfill with
   today's thoughts.
2. Dry-run touches no store. Apply is gated on a fresh backup and replays the
   REVIEWED candidate verbatim (no second LLM call).
3. The store appends and readers take the last line — so the fragment stays in
   the file as the scar's own record, and the new entry carries provenance
   meta (backfilled + the replaced fragment) so a reconstruction can never be
   mistaken for a memory.
"""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

import pytest

from scripts.reflection_backfill import (
    apply_candidate,
    gather_material_for_date,
    run_dryrun,
)


def _plant_monologue(root: Path, date: str, thoughts, *, day_offset=0):
    root.mkdir(parents=True, exist_ok=True)
    base = 1786000000 + day_offset * 86400
    lines = [
        json.dumps({"thought": t, "timestamp": base + i})
        for i, t in enumerate(thoughts)
    ]
    (root / f"{date}.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_gather_uses_the_target_dates_monologue_not_todays(tmp_path):
    mono = tmp_path / "inner_monologue"
    _plant_monologue(mono, "2026-08-02", ["watching the doorknob again"])
    _plant_monologue(mono, "2026-08-05", ["TODAY-THOUGHT must not leak"], day_offset=3)

    async def fake_day_log(owner_user_id, day):
        assert day == "2026-08-02"
        return {"markdown": "He asked about the frame print."}

    material = asyncio.run(
        gather_material_for_date(
            owner_id=1, date="2026-08-02",
            monologue_root=mono, day_log_fn=fake_day_log,
        )
    )
    assert "doorknob" in material
    assert "frame print" in material
    assert "TODAY-THOUGHT" not in material


def test_gather_survives_missing_sources(tmp_path):
    async def no_day_log(owner_user_id, day):
        raise RuntimeError("db down")

    material = asyncio.run(
        gather_material_for_date(
            owner_id=1, date="2026-08-02",
            monologue_root=tmp_path / "absent", day_log_fn=no_day_log,
        )
    )
    assert material == ""


def test_dryrun_writes_candidate_json_and_touches_no_store(tmp_path):
    mono = tmp_path / "inner_monologue"
    _plant_monologue(mono, "2026-08-02", ["a real thought"])
    store = tmp_path / "night_cycle"
    out = tmp_path / "evals"

    async def fake_day_log(owner_user_id, day):
        return {"markdown": "the day log"}

    async def fake_llm(prompt):
        assert "a real thought" in prompt
        return "Tonight I kept circling the frame print, and underneath it the quiet."

    report = asyncio.run(
        run_dryrun(
            owner_id=1, date="2026-08-02",
            monologue_root=mono, day_log_fn=fake_day_log, llm_call=fake_llm,
            store_root=store, out_dir=out,
        )
    )
    assert not store.exists(), "dry-run must not create or touch the store"
    saved = json.loads(Path(report["candidate_path"]).read_text(encoding="utf-8"))
    assert saved["date"] == "2026-08-02"
    assert "frame print" in saved["text"]


def _seed_fragment(store: Path, date: str, fragment: str):
    store.mkdir(parents=True, exist_ok=True)
    line = json.dumps(
        {"date": date, "text": fragment, "created_at": 1785708000.0, "meta": {"owner_id": 1}}
    )
    (store / f"{date}.jsonl").write_text(line + "\n", encoding="utf-8")


def test_apply_appends_with_provenance_and_keeps_the_fragment(tmp_path):
    from ai.night_cycle_store import read_daily_meaning_for

    store = tmp_path / "night_cycle"
    fragment = "I kept circling the same frame-print question all day,"
    _seed_fragment(store, "2026-08-02", fragment)

    candidate = tmp_path / "cand.json"
    candidate.write_text(json.dumps({
        "date": "2026-08-02",
        "text": "Tonight I kept circling the frame print, and underneath it the quiet.",
    }), encoding="utf-8")
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    (backup_dir / "fresh.sqlite3").write_bytes(b"x")

    result = apply_candidate(
        candidate_path=candidate, store_root=store, backup_dir=backup_dir,
        owner_id=1,
    )
    assert result["applied"] is True

    lines = (store / "2026-08-02.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2, "append, never overwrite — the fragment is the scar's record"
    assert fragment in lines[0]

    entry = read_daily_meaning_for(date="2026-08-02", root=store)
    assert "frame print" in entry.text and entry.text != fragment
    assert entry.meta.get("backfilled") is True
    assert entry.meta.get("replaced_fragment") == fragment
    assert "RECONSTRUCTED" in entry.meta.get("backfill_note", "")


def test_apply_refuses_without_fresh_backup(tmp_path):
    store = tmp_path / "night_cycle"
    _seed_fragment(store, "2026-08-02", "fragment,")
    candidate = tmp_path / "cand.json"
    candidate.write_text(json.dumps({"date": "2026-08-02", "text": "regenerated."}),
                         encoding="utf-8")
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    stale = backup_dir / "old.sqlite3"
    stale.write_bytes(b"x")
    import os
    old = time.time() - 13 * 3600
    os.utime(stale, (old, old))

    with pytest.raises(SystemExit):
        apply_candidate(
            candidate_path=candidate, store_root=store, backup_dir=backup_dir,
            owner_id=1,
        )
    lines = (store / "2026-08-02.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1, "refusal must leave the store untouched"
