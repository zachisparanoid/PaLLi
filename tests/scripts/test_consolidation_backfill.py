"""Backfill driver (Wave 1a): dry-run writes NOTHING, apply is gated, resume
skips done days, reconciliation NEVER runs in backfill."""
from __future__ import annotations

import json
import time
import uuid
from datetime import datetime
from pathlib import Path

import pytest

from scripts.consolidation_backfill import (
    BackfillDeps,
    backup_is_fresh,
    load_checkpoint,
    run_backfill,
)


def _mk_deps(tmp_path):
    from core.database import PalliBrain
    brain = PalliBrain(str(tmp_path / f"bf_{uuid.uuid4().hex}.sqlite3"))

    async def llm(prompt):
        return "a small honest gist"

    return BackfillDeps(
        brain=brain,
        owner_id=1,
        day_llm=llm,
        fact_llm=llm,
        out_dir=tmp_path / "evals",
        state_path=tmp_path / "state.json",
        gist_root=tmp_path / "day_gists",
    )


async def _seed_day(brain, day, content):
    day_ts = datetime.strptime(day, "%Y-%m-%d").timestamp() + 3600
    cid = await brain.store_conversation(
        user_id=1, platform="dashboard", channel_id=1, thread_id=None,
        role="user", content=content, username="Sam")
    conn = await brain.get_connection()
    try:
        await conn.execute("UPDATE conversations SET timestamp=$1 WHERE id=$2", day_ts, cid)
    finally:
        await conn.close()


async def _seed_old_fact(brain):
    fid = await brain.store_user_memory(
        user_id=1, memory_type="moment", content="old moment", importance=0.6)
    conn = await brain.get_connection()
    try:
        await conn.execute("UPDATE user_memory SET created_at=$1 WHERE id=$2",
                           time.time() - 60 * 86400, fid)
    finally:
        await conn.close()
    return fid


@pytest.mark.asyncio
async def test_dry_run_writes_nothing_but_reports_everything(tmp_path):
    deps = _mk_deps(tmp_path)
    await deps.brain.initialize()
    try:
        await _seed_day(deps.brain, "2026-05-02", "the pergola estimate came back")
        await _seed_old_fact(deps.brain)

        summary = await run_backfill(deps, from_day="2026-05-02", to_day="2026-05-03", apply=False)
        assert summary["mode"] == "dry_run"
        assert summary["day_gists_proposed"] == 1
        assert summary["aging_proposed"] == 1
        assert not (tmp_path / "day_gists").exists()          # zero store writes
        from ai.fact_retrieval import get_user_facts_raw
        rows = await get_user_facts_raw(user_id=1, brain_client=deps.brain)
        assert all(r["status"] == "active" for r in rows)     # zero DB writes
        report_md = list((tmp_path / "evals").glob("*-dryrun.md"))
        report_json = list((tmp_path / "evals").glob("*-dryrun.json"))
        assert report_md and report_json
        text = report_md[0].read_text(encoding="utf-8")
        assert "a small honest gist" in text                  # full would-be diff
        assert "old moment" in text                           # old content shown
        state = load_checkpoint(deps.state_path)
        assert state["dry_run_done_for"] == "2026-05-02..2026-05-03"
    finally:
        await deps.brain.close()


@pytest.mark.asyncio
async def test_apply_requires_fresh_backup_and_prior_dry_run(tmp_path):
    import os
    deps = _mk_deps(tmp_path)
    await deps.brain.initialize()
    try:
        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()
        # no backup at all
        with pytest.raises(SystemExit):
            await run_backfill(deps, from_day="2026-05-02", to_day="2026-05-03",
                               apply=True, live_target=True, backup_dir=backup_dir)
        # stale backup
        stale = backup_dir / "palli-local-old.sqlite3"
        stale.write_bytes(b"x")
        old = time.time() - 24 * 3600
        os.utime(stale, (old, old))
        with pytest.raises(SystemExit):
            await run_backfill(deps, from_day="2026-05-02", to_day="2026-05-03",
                               apply=True, live_target=True, backup_dir=backup_dir)
        # fresh backup but NO prior dry-run for the window
        fresh = backup_dir / "palli-local-new.sqlite3"
        fresh.write_bytes(b"x")
        with pytest.raises(SystemExit):
            await run_backfill(deps, from_day="2026-05-02", to_day="2026-05-03",
                               apply=True, live_target=True, backup_dir=backup_dir)
    finally:
        await deps.brain.close()


@pytest.mark.asyncio
async def test_apply_writes_gists_supersedes_and_emits_manifest(tmp_path):
    deps = _mk_deps(tmp_path)
    await deps.brain.initialize()
    try:
        await _seed_day(deps.brain, "2026-05-02", "the pergola estimate came back")
        fid = await _seed_old_fact(deps.brain)

        await run_backfill(deps, from_day="2026-05-02", to_day="2026-05-03", apply=False)
        summary = await run_backfill(deps, from_day="2026-05-02", to_day="2026-05-03",
                                     apply=True, live_target=False)  # sandbox target: gates skipped
        assert summary["mode"] == "apply"
        from ai.day_gist_store import read_day_gist
        assert read_day_gist(day="2026-05-02", root=deps.gist_root) is not None
        from ai.fact_retrieval import get_user_facts_raw
        rows = {f["id"]: f for f in await get_user_facts_raw(user_id=1, brain_client=deps.brain)}
        assert rows[fid]["status"] == "superseded"
        manifest = list((tmp_path / "evals").glob("*-apply-manifest.json"))
        assert manifest
        data = json.loads(manifest[0].read_text(encoding="utf-8"))
        assert data["day_gists"][0]["day"] == "2026-05-02"
        assert data["supersedes"][0]["old_id"] == fid
        # resume: a second apply skips the done day
        summary2 = await run_backfill(deps, from_day="2026-05-02", to_day="2026-05-03",
                                      apply=True, live_target=False, resume=True)
        assert summary2["days_skipped_resume"] >= 1
    finally:
        await deps.brain.close()


def test_backup_freshness_check(tmp_path):
    import os
    d = tmp_path / "backups"
    d.mkdir()
    assert backup_is_fresh(d, max_age_hours=12) is False
    f = d / "b.sqlite3"
    f.write_bytes(b"x")
    assert backup_is_fresh(d, max_age_hours=12) is True
    old = time.time() - 13 * 3600
    os.utime(f, (old, old))
    assert backup_is_fresh(d, max_age_hours=12) is False


@pytest.mark.asyncio
async def test_dry_run_with_errors_does_not_stamp_the_gate(tmp_path):
    """Review fix: an all-error dry-run (LLM outage) must NOT satisfy the
    apply gate — the owner would be 'approving' a report that was never
    generated."""
    deps = _mk_deps(tmp_path)
    await deps.brain.initialize()
    try:
        await _seed_day(deps.brain, "2026-05-02", "the pergola estimate came back")

        async def boom(prompt):
            raise RuntimeError("llm down")

        deps.day_llm = boom
        deps.fact_llm = boom
        summary = await run_backfill(deps, from_day="2026-05-02", to_day="2026-05-02", apply=False)
        assert summary["errors"]
        state = load_checkpoint(deps.state_path)
        assert "dry_run_done_for" not in state
    finally:
        await deps.brain.close()


def test_sandbox_paths_derived_from_db_target():
    from scripts.consolidation_backfill import sandbox_paths
    gist_root, state_path = sandbox_paths("/tmp/rehearsal/copy.sqlite3")
    assert str(gist_root).endswith("copy.day_gists")
    assert str(state_path).endswith("copy.backfill_state.json")
    assert "/tmp/rehearsal/" in str(gist_root) and "/tmp/rehearsal/" in str(state_path)


@pytest.mark.asyncio
async def test_sandbox_run_refuses_live_file_paths(tmp_path):
    """Review fix: a --db rehearsal must never write the live gist store or
    the live checkpoint — refuse unless isolated paths are wired."""
    from scripts.consolidation_backfill import DEFAULT_STATE
    deps = _mk_deps(tmp_path)
    deps.gist_root = None                      # live day_gists dir
    await deps.brain.initialize()
    try:
        with pytest.raises(SystemExit):
            await run_backfill(deps, from_day="2026-05-02", to_day="2026-05-02",
                               apply=True, live_target=False)
        deps2 = _mk_deps(tmp_path)
        deps2.state_path = DEFAULT_STATE       # live checkpoint
        await deps2.brain.initialize()
        try:
            with pytest.raises(SystemExit):
                await run_backfill(deps2, from_day="2026-05-02", to_day="2026-05-02",
                                   apply=False, live_target=False)
        finally:
            await deps2.brain.close()
    finally:
        await deps.brain.close()


@pytest.mark.asyncio
async def test_replay_applies_exactly_the_reviewed_texts_and_skips_drift(tmp_path):
    """Review fix: apply must land what the owner READ, not a fresh sample.
    Replay consumes the dry-run JSON verbatim; rows whose content changed
    since the dry-run are skipped as drift."""
    from scripts.consolidation_backfill import run_replay
    deps = _mk_deps(tmp_path)
    await deps.brain.initialize()
    try:
        await _seed_day(deps.brain, "2026-05-02", "the pergola estimate came back")
        fid_keep = await _seed_old_fact(deps.brain)
        fid_drift = await deps.brain.store_user_memory(
            user_id=1, memory_type="moment", content="drifting moment", importance=0.6)
        conn = await deps.brain.get_connection()
        try:
            await conn.execute("UPDATE user_memory SET created_at=$1 WHERE id=$2",
                               time.time() - 70 * 86400, fid_drift)
        finally:
            await conn.close()

        await run_backfill(deps, from_day="2026-05-02", to_day="2026-05-03", apply=False)
        dryrun_json = sorted((tmp_path / "evals").glob("*-dryrun.json"))[-1]

        # content drifts AFTER the owner reviewed the report
        conn = await deps.brain.get_connection()
        try:
            await conn.execute("UPDATE user_memory SET content=$1 WHERE id=$2",
                               "drifting moment, edited since review", fid_drift)
        finally:
            await conn.close()

        summary = await run_replay(deps, dryrun_json=dryrun_json, live_target=False)
        assert summary["mode"] == "replay"
        from ai.day_gist_store import read_day_gist
        gist = read_day_gist(day="2026-05-02", root=deps.gist_root)
        assert gist is not None and gist.text == "a small honest gist"  # the REVIEWED text
        from ai.fact_retrieval import get_user_facts_raw
        rows = {f["id"]: f for f in await get_user_facts_raw(user_id=1, brain_client=deps.brain)}
        assert rows[fid_keep]["status"] == "superseded"          # clean row applied
        assert rows[fid_drift]["status"] == "active"             # drifted row SKIPPED
        assert summary["aging_skipped_drift"] == 1
        manifest = sorted((tmp_path / "evals").glob("*-apply-manifest.json"))
        assert manifest and "replay" in manifest[-1].read_text(encoding="utf-8")
    finally:
        await deps.brain.close()


@pytest.mark.asyncio
async def test_replay_live_requires_fresh_backup(tmp_path):
    from scripts.consolidation_backfill import run_replay
    deps = _mk_deps(tmp_path)
    await deps.brain.initialize()
    try:
        payload = tmp_path / "fake-dryrun.json"
        payload.write_text(json.dumps({"window": "2026-05-02..2026-05-03",
                                       "day_gists": [], "aging_proposals": []}), encoding="utf-8")
        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()
        with pytest.raises(SystemExit):
            await run_replay(deps, dryrun_json=payload, live_target=True, backup_dir=backup_dir)
    finally:
        await deps.brain.close()
