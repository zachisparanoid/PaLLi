"""The nightly memory sweep (Batch 5, memory that picks, task 6 — spec 2.2).

Every night, a line of MEMORY.md nobody has used for a long time MOVES to the
same section of MEMORY_ARCHIVE.md with a ``[archived YYYY-MM-DD]`` marker;
nothing is deleted, and ``palli_ctl memory restore <mcb_id>`` brings a line
back. A line with no stamp and no reference is stamped with today instead
(Review Focus 2), so its age is known from now on. Important Decisions and
the templates' placeholder bullets are never stamped nor moved (R12).

``plan_sweep`` and ``apply_sweep`` are pure; ``sweep_memory`` does the I/O
in the night cycle's pattern and never raises.
"""
from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

import ai.memory_sweep as ms
from ai.memory_sweep import apply_sweep, plan_sweep, sweep_memory
from core.memory_bullets import item_id_for

# R27: the brief's test names a `now` fixture that does not exist; the house
# style passes `now` explicitly. Noon on 2026-09-28, local time.
now = time.mktime(time.strptime("2026-09-28 12:00", "%Y-%m-%d %H:%M"))
DAY = "2026-09-28"


def test_old_unused_lines_move_and_used_lines_stay():
    doc = "## Durable Context\n- [2026-01-01] old unused\n- [2026-01-01] old used\n- [2026-09-20] new\n- no stamp\n\n## Important Decisions\n- [2025-01-01] ancient decision\n"
    refs = {item_id_for("- [2026-01-01] old used"): {"last_referenced_at": now - 5 * 86400, "count": 1}}
    plan = plan_sweep(doc, refs=refs, now=now, durable_days=60, thread_days=45)
    assert [b.text for b in plan.move] == ["- [2026-01-01] old unused"]
    assert plan.stamp == ["- no stamp"]           # gets today's stamp, does not move
    assert "ancient decision" not in str(plan.move)


def test_a_move_names_its_section_and_id():
    doc = "## Useful References\n- [2026-01-01] the router's admin page\n"
    plan = plan_sweep(doc, refs={}, now=now, durable_days=60, thread_days=45)
    assert len(plan.move) == 1
    move = plan.move[0]
    assert move.section == "## Useful References"
    assert move.item_id == item_id_for("- [2026-01-01] the router's admin page")
    assert plan.day == DAY


def test_threads_older_than_thread_days_move_and_younger_stay():
    doc = (
        "## Active Threads\n"
        "- [2026-08-01] fifty-eight days old\n"
        "- [2026-08-20] thirty-nine days old\n"
        "- [2026-09-27] yesterday\n"
    )
    plan = plan_sweep(doc, refs={}, now=now, durable_days=60, thread_days=45)
    assert [b.text for b in plan.move] == ["- [2026-08-01] fifty-eight days old"]
    assert plan.stamp == []
    # the same lines under Durable Context are judged by durable_days (60): none moves
    durable = doc.replace("## Active Threads", "## Durable Context")
    assert plan_sweep(durable, refs={}, now=now, durable_days=60, thread_days=45).move == []


def test_zero_days_turns_that_class_of_move_off():
    doc = "## Active Threads\n- [2026-01-01] ancient thread\n\n## Durable Context\n- [2026-01-01] ancient fact\n"
    plan = plan_sweep(doc, refs={}, now=now, durable_days=60, thread_days=0)
    assert [b.text for b in plan.move] == ["- [2026-01-01] ancient fact"]
    plan = plan_sweep(doc, refs={}, now=now, durable_days=0, thread_days=45)
    assert [b.text for b in plan.move] == ["- [2026-01-01] ancient thread"]


def test_a_recent_reference_keeps_an_old_line():
    line = "- [2026-01-01] old but she used it last week"
    doc = f"## Durable Context\n{line}\n"
    refs = {item_id_for(line): {"last_referenced_at": now - 7 * 86400, "count": 3}}
    assert plan_sweep(doc, refs=refs, now=now, durable_days=60, thread_days=45).move == []
    # an old reference does not save an old line: age = max(stamp, reference)
    refs = {item_id_for(line): {"last_referenced_at": now - 61 * 86400, "count": 3}}
    assert [b.text for b in plan_sweep(doc, refs=refs, now=now, durable_days=60, thread_days=45).move] == [line]


def test_a_no_stamp_line_with_a_reference_is_aged_by_the_reference_and_not_stamped():
    line = "- no stamp, but referenced"
    doc = f"## Durable Context\n{line}\n"
    fresh = {item_id_for(line): {"last_referenced_at": now - 3 * 86400, "count": 1}}
    plan = plan_sweep(doc, refs=fresh, now=now, durable_days=60, thread_days=45)
    assert plan.move == [] and plan.stamp == []
    stale = {item_id_for(line): {"last_referenced_at": now - 90 * 86400, "count": 1}}
    plan = plan_sweep(doc, refs=stale, now=now, durable_days=60, thread_days=45)
    assert [b.text for b in plan.move] == [line] and plan.stamp == []


def test_decisions_and_placeholders_are_never_stamped_nor_moved():
    doc = (
        "## Durable Context\n"
        "- Interests: No explicit interest list yet.\n"
        "- a real fact\n\n"
        "## Active Threads\n"
        "- No active curated threads yet.\n\n"
        "## Important Decisions\n"
        "- [2020-01-01] an ancient decision\n"
        "- an undated decision\n"
        "- No important decisions recorded yet.\n\n"
        "## Useful References\n"
        "- No durable references recorded yet.\n"
    )
    plan = plan_sweep(doc, refs={}, now=now, durable_days=60, thread_days=45)
    assert plan.move == []
    assert plan.stamp == ["- a real fact"]


def test_a_malformed_stamp_reads_as_no_stamp():
    line = "- [2026-13-45] not a real date"
    doc = f"## Durable Context\n{line}\n"
    plan = plan_sweep(doc, refs={}, now=now, durable_days=60, thread_days=45)
    assert plan.move == [] and plan.stamp == [line]


def test_lines_outside_the_swept_sections_are_left_alone():
    doc = (
        "# Memory\n\n"
        "- a bullet before any heading\n\n"
        "## Something Else\n"
        "- [2020-01-01] ancient but not ours\n\n"
        "## Durable Context\n"
        "- [2020-01-01] ancient and ours\n"
    )
    plan = plan_sweep(doc, refs={}, now=now, durable_days=60, thread_days=45)
    assert [b.text for b in plan.move] == ["- [2020-01-01] ancient and ours"]
    assert plan.stamp == []


def test_refs_in_an_unexpected_shape_are_ignored():
    line = "- [2020-01-01] ancient"
    doc = f"## Durable Context\n{line}\n"
    for refs in ({item_id_for(line): None}, {item_id_for(line): {"last_referenced_at": "soon"}}, None):
        assert [b.text for b in plan_sweep(doc, refs=refs, now=now, durable_days=60, thread_days=45).move] == [line]


# ── apply: the two new bodies ────────────────────────────────────────────────

MEMORY = (
    "# Memory\n\n"
    "This file is my curated memory of them, not a transcript.\n\n"
    "## Durable Context\n\n"
    "- [2026-01-01] old unused\n"
    "- [2026-01-01] [2026-02-02] double-stamped and old\n"
    "- no stamp\n\n"
    "## Active Threads\n\n"
    "- [2026-07-01] an old thread\n"
    "- [2026-09-27] a fresh thread\n\n"
    "## Important Decisions\n\n"
    "- [2025-01-01] ancient decision\n\n"
    "## Useful References\n\n"
    "- [2026-01-01] the only reference\n"
)

ARCHIVE = (
    "# Memory Archive\n\n"
    "Lines the nightly sweep moved out of MEMORY.md by age and use, each with the day it moved.\n\n"
    "Nothing here is deleted; any line can be restored.\n\n"
    "## Durable Context\n\n"
    "- Nothing archived yet.\n\n"
    "## Active Threads\n\n"
    "- Nothing archived yet.\n\n"
    "## Important Decisions\n\n"
    "- Nothing archived yet.\n\n"
    "## Useful References\n\n"
    "- Nothing archived yet.\n"
)


def _plan():
    return plan_sweep(MEMORY, refs={}, now=now, durable_days=60, thread_days=45)


def test_apply_moves_stamps_and_leaves_the_rest_byte_for_byte():
    plan = _plan()
    new_memory, new_archive = apply_sweep(MEMORY, ARCHIVE, plan)
    assert new_memory == (
        "# Memory\n\n"
        "This file is my curated memory of them, not a transcript.\n\n"
        "## Durable Context\n\n"
        f"- [{DAY}] no stamp\n\n"
        "## Active Threads\n\n"
        "- [2026-09-27] a fresh thread\n\n"
        "## Important Decisions\n\n"
        "- [2025-01-01] ancient decision\n\n"
        "## Useful References\n\n"
    )
    assert new_archive == (
        "# Memory Archive\n\n"
        "Lines the nightly sweep moved out of MEMORY.md by age and use, each with the day it moved.\n\n"
        "Nothing here is deleted; any line can be restored.\n\n"
        "## Durable Context\n\n"
        f"- [2026-01-01] old unused [archived {DAY}]\n"
        f"- [2026-01-01] [2026-02-02] double-stamped and old [archived {DAY}]\n\n"
        "## Active Threads\n\n"
        f"- [2026-07-01] an old thread [archived {DAY}]\n\n"
        "## Important Decisions\n\n"
        "- Nothing archived yet.\n\n"
        "## Useful References\n\n"
        f"- [2026-01-01] the only reference [archived {DAY}]\n"
    )


def test_the_archived_line_keeps_its_text_and_its_id():
    plan = _plan()
    _, new_archive = apply_sweep(MEMORY, ARCHIVE, plan)
    archived = [ln for ln in new_archive.splitlines() if ln.endswith(f"[archived {DAY}]")]
    assert len(archived) == len(plan.move) == 4
    for move, line in zip(plan.move, archived):
        assert line == f"{move.text} [archived {DAY}]"
        assert item_id_for(line) == move.item_id == item_id_for(move.text)


def test_the_stamped_line_keeps_its_id():
    plan = _plan()
    new_memory, _ = apply_sweep(MEMORY, ARCHIVE, plan)
    assert item_id_for(f"- [{DAY}] no stamp") == item_id_for("- no stamp")
    assert f"- [{DAY}] no stamp" in new_memory.splitlines()


def test_a_second_night_appends_after_the_first_nights_lines():
    first_memory, first_archive = apply_sweep(MEMORY, ARCHIVE, _plan())
    later = now + 100 * 86400
    plan = plan_sweep(first_memory, refs={}, now=later, durable_days=60, thread_days=45)
    assert [b.text for b in plan.move] == [f"- [{DAY}] no stamp", "- [2026-09-27] a fresh thread"]
    second_memory, second_archive = apply_sweep(first_memory, first_archive, plan)
    lines = second_archive.splitlines()
    durable = lines.index("## Durable Context")
    assert lines[durable + 2 : durable + 5] == [
        f"- [2026-01-01] old unused [archived {DAY}]",
        f"- [2026-01-01] [2026-02-02] double-stamped and old [archived {DAY}]",
        f"- [{DAY}] no stamp [archived {plan.day}]",
    ]
    assert second_memory.endswith("## Useful References\n\n")  # the emptied section stays, empty


def test_an_empty_plan_returns_both_bodies_byte_identical():
    body = MEMORY.replace("- no stamp\n", "")
    early = time.mktime(time.strptime("2026-02-03", "%Y-%m-%d")) + 3600  # every stamp is young that day
    plan = plan_sweep(body, refs={}, now=early, durable_days=60, thread_days=45)
    assert plan.move == [] and plan.stamp == []
    assert apply_sweep(body, ARCHIVE, plan) == (body, ARCHIVE)


def test_a_retried_night_does_not_duplicate_a_line_already_in_the_archive_section():
    """Fix round 1 (I1): the archive is written before MEMORY.md, so a night
    that failed between the two writes leaves the moved lines in BOTH
    documents; the retry must not append them to the archive twice."""
    plan = _plan()
    first_memory, first_archive = apply_sweep(MEMORY, ARCHIVE, plan)
    again_memory, again_archive = apply_sweep(MEMORY, first_archive, plan)
    assert again_archive == first_archive
    assert again_memory == first_memory
    assert again_archive.count("old unused [archived") == 1


def test_apply_adds_a_missing_archive_section_at_the_end():
    plan = plan_sweep("## Useful References\n- [2026-01-01] a reference\n", refs={}, now=now, durable_days=60, thread_days=45)
    _, new_archive = apply_sweep("## Useful References\n- [2026-01-01] a reference\n", "# Memory Archive\n\n## Durable Context\n\n- Nothing archived yet.\n", plan)
    assert new_archive == (
        "# Memory Archive\n\n## Durable Context\n\n- Nothing archived yet.\n\n"
        f"## Useful References\n\n- [2026-01-01] a reference [archived {DAY}]\n"
    )


# ── sweep_memory: the I/O, never raising ─────────────────────────────────────


def _doc(doc_key, body, version=3):
    return SimpleNamespace(content=body, frontmatter={"doc_type": doc_key, "version": version})


@pytest.fixture
def fake_identity(monkeypatch, tmp_path):
    """The identity service, the refs table and the sidecar dir, all fake or
    in tmp; returns the recorder the assertions read."""
    monkeypatch.setattr(ms, "MEMORY_SWEEP_DIR", tmp_path / "sweeps")
    monkeypatch.setattr(ms, "_archive_days", lambda: (60, 45))
    bundle = SimpleNamespace(owner={"memory": _doc("memory", MEMORY), "memory_archive": _doc("memory_archive", ARCHIVE)})
    written = []
    refreshed = []

    async def fake_update(**kw):
        written.append(kw)
        return None

    async def fake_load_refs(owner_id):
        return {}

    async def fake_refresh(owner_id):
        refreshed.append(owner_id)
        return {}

    ensure = AsyncMock(return_value=None)
    monkeypatch.setattr(ms.memory_refs, "load_refs", fake_load_refs)
    monkeypatch.setattr(ms.memory_refs, "refresh", fake_refresh)
    with patch.object(ms.identity_service, "ensure_user_identity_bundle", ensure), \
         patch.object(ms.identity_service, "compile_for_owner", return_value=bundle), \
         patch.object(ms.identity_service, "update_raw_document", side_effect=fake_update):
        yield SimpleNamespace(written=written, refreshed=refreshed, ensure=ensure, sweeps=tmp_path / "sweeps", bundle=bundle)


@pytest.mark.asyncio
async def test_sweep_memory_writes_both_documents_the_sidecar_and_refreshes_the_refs(fake_identity):
    result = await sweep_memory(owner_id=7, now=now)
    assert result == {"moved": 4, "stamped": 1}
    # the archive was materialised first, for the owner
    fake_identity.ensure.assert_awaited_once()
    assert fake_identity.ensure.await_args.kwargs["user_id"] == 7
    assert fake_identity.ensure.await_args.kwargs["platform"] == "owner"
    # one write per document, through the store, with the reason
    by_key = {w["doc_key"]: w for w in fake_identity.written}
    assert set(by_key) == {"memory", "memory_archive"}
    for w in by_key.values():
        assert w["reason"] == "memory_sweep: moved=4 stamped=1"
        assert w["actor_type"] == "system" and w["actor_id"] == "memory_sweep"
        assert w["user_id"] == 7 and w["platform"] == "owner" and w["identity_owner_id"] == 7
        assert w["markdown"].startswith("---\n") and "version: 3" in w["markdown"]
    memory_md = by_key["memory"]["markdown"]
    assert "old unused" not in memory_md and "an old thread" not in memory_md
    assert f"- [{DAY}] no stamp" in memory_md and "a fresh thread" in memory_md
    archive_md = by_key["memory_archive"]["markdown"]
    assert f"- [2026-07-01] an old thread [archived {DAY}]" in archive_md
    assert archive_md.count("Nothing archived yet.") == 1  # only Important Decisions keeps its placeholder
    # the refs snapshot is refreshed once the sweep has written (R7)
    assert fake_identity.refreshed == [7]
    # the sidecar names every moved and stamped line with its id
    sidecars = list(fake_identity.sweeps.glob("*.md"))
    assert [p.name for p in sidecars] == [f"{DAY}-memory-sweep.md"]
    text = sidecars[0].read_text(encoding="utf-8")
    for line in ("- [2026-01-01] old unused", "- [2026-07-01] an old thread", "- no stamp"):
        assert line in text and item_id_for(line) in text


@pytest.mark.asyncio
async def test_sweep_memory_with_nothing_to_do_writes_nothing_but_still_refreshes(fake_identity):
    quiet = MEMORY.replace("- no stamp\n", "")
    fake_identity.bundle.owner["memory"] = _doc("memory", quiet)
    early = time.mktime(time.strptime("2026-02-03", "%Y-%m-%d")) + 3600
    result = await sweep_memory(owner_id=7, now=early)
    assert result == {"moved": 0, "stamped": 0}
    assert fake_identity.written == []
    assert not fake_identity.sweeps.exists()
    assert fake_identity.refreshed == [7]


@pytest.mark.asyncio
async def test_sweep_memory_reads_the_refs_fresh_from_the_table_not_the_snapshot(fake_identity, monkeypatch):
    seen = []

    async def fresh(owner_id):
        seen.append(owner_id)
        # every old line was used yesterday: nothing moves
        return {item_id_for(ln): {"last_referenced_at": now - 86400, "count": 1}
                for ln in MEMORY.splitlines() if ln.startswith("- ")}

    monkeypatch.setattr(ms.memory_refs, "load_refs", fresh)
    monkeypatch.setattr(ms.memory_refs, "snapshot", lambda owner_id: pytest.fail("the sweep read the snapshot"))
    result = await sweep_memory(owner_id=7, now=now)
    assert seen == [7]
    assert result == {"moved": 0, "stamped": 0}  # "- no stamp" is referenced, so it is not stamped either


@pytest.mark.asyncio
async def test_sweep_memory_skips_when_a_document_is_missing(fake_identity):
    fake_identity.bundle.owner.pop("memory_archive")
    assert await sweep_memory(owner_id=7, now=now) == {"skipped": "no_archive_doc"}
    fake_identity.bundle.owner.pop("memory")
    assert await sweep_memory(owner_id=7, now=now) == {"skipped": "no_memory_doc"}
    assert fake_identity.written == []


@pytest.mark.asyncio
async def test_sweep_memory_returns_an_error_and_never_raises(fake_identity):
    async def boom(**kw):
        raise OSError("disk full")

    with patch.object(ms.identity_service, "update_raw_document", side_effect=boom):
        result = await sweep_memory(owner_id=7, now=now)
    assert result == {"error": "disk full"}
    assert fake_identity.refreshed == []


@pytest.mark.asyncio
async def test_sweep_memory_writes_the_archive_first_so_a_failed_second_write_loses_nothing(fake_identity):
    """Fix round 1 (I1): the store writes the file, THEN records the version
    in the brain; a DB hiccup there raises after the file is on disk. Written
    memory-first, a failure between the writes dropped the moved lines from
    both documents. Archive-first, the same failure leaves them in both."""
    calls = []

    async def second_write_fails(**kw):
        calls.append(kw)
        if len(calls) == 2:
            raise RuntimeError("database is locked")

    with patch.object(ms.identity_service, "update_raw_document", side_effect=second_write_fails):
        result = await sweep_memory(owner_id=7, now=now)
    assert result == {"error": "database is locked"}
    assert [c["doc_key"] for c in calls] == ["memory_archive", "memory"]
    # the archive already holds every moved line; MEMORY.md was never rewritten
    assert f"- [2026-07-01] an old thread [archived {DAY}]" in calls[0]["markdown"]
    assert fake_identity.refreshed == []


@pytest.mark.asyncio
async def test_sweep_memory_reads_the_refs_before_the_document(fake_identity, monkeypatch):
    """Fix round 1 (M1): the refs round-trip is the DB wait; the document is
    read after it, so a reflection landing during the wait is not overwritten."""
    order = []

    async def load_refs(owner_id):
        order.append("refs")
        return {}

    def compile_for_owner(*, owner_id):
        order.append("doc")
        return fake_identity.bundle

    monkeypatch.setattr(ms.memory_refs, "load_refs", load_refs)
    with patch.object(ms.identity_service, "compile_for_owner", side_effect=compile_for_owner):
        await sweep_memory(owner_id=7, now=now)
    assert order == ["refs", "doc"]


@pytest.mark.asyncio
async def test_sweep_memory_defaults_now_to_the_clock(fake_identity):
    before = time.time()
    result = await sweep_memory(owner_id=7)
    assert result["stamped"] == 1
    today = time.strftime("%Y-%m-%d", time.localtime(before))
    assert f"- [{today}] no stamp" in {w["doc_key"]: w for w in fake_identity.written}["memory"]["markdown"]


def test_archive_days_read_the_settings_with_the_schema_defaults_as_fallback(monkeypatch):
    from core.settings_boot import settings_service

    monkeypatch.setattr(settings_service, "snapshot",
                        lambda: SimpleNamespace(memory=SimpleNamespace(durable_archive_days=10, thread_archive_days=0)))
    assert ms._archive_days() == (10, 0)

    def unbooted():
        raise RuntimeError("settings not booted")

    monkeypatch.setattr(settings_service, "snapshot", unbooted)
    assert ms._archive_days() == (60, 45)
    monkeypatch.setattr(settings_service, "snapshot", lambda: SimpleNamespace(memory=None))
    assert ms._archive_days() == (60, 45)


# ── restore (the pure step; the lever's tests drive palli_ctl) ───────────────


def test_plan_restore_skips_the_insert_when_memory_already_holds_the_line():
    """Fix round 1 (M4): the reflection may re-add a line after the sweep
    archived it (same text, so the same id). A restore then clears the
    archive copy and adds nothing."""
    from ai.memory_sweep import plan_restore

    memory = "## Active Threads\n\n- Investigate the router dropping overnight\n"
    archive = "## Active Threads\n\n- [2026-06-01] Investigate the router dropping overnight [archived 2026-09-10]\n"
    item_id = item_id_for("- Investigate the router dropping overnight")
    plan = plan_restore(memory, archive, item_id)
    assert plan.inserted is False
    assert plan.memory_body == memory
    assert plan.archive_body == "## Active Threads\n\n"
    assert plan.section == "## Active Threads"
    # the ordinary case still inserts
    plan = plan_restore("## Active Threads\n\n- something else\n", archive, item_id)
    assert plan.inserted is True
    assert plan.memory_body == "## Active Threads\n\n- something else\n- [2026-06-01] Investigate the router dropping overnight\n"


# ── the night cycle's slot ───────────────────────────────────────────────────


def _night(monkeypatch):
    import ai.night_cycle as nc

    async def fake_resolve():
        return 1

    async def fake_gather(**k):
        return ""

    async def fake_pass(**k):
        return {"wrote": "2026-09-28"}

    monkeypatch.setattr(nc, "_resolve_owner_id", fake_resolve)
    monkeypatch.setattr(nc, "_gather_day_material", fake_gather)
    monkeypatch.setattr(nc, "run_night_cycle", fake_pass)
    return nc


def test_run_once_runs_sweep_memory_in_the_memory_pass_slot(monkeypatch, tmp_path):
    nc = _night(monkeypatch)
    calls = []

    async def fake_sweep(*, owner_id, now):
        calls.append((owner_id, now))
        return {"moved": 2, "stamped": 5}

    monkeypatch.setattr(ms, "sweep_memory", fake_sweep)
    before = time.time()
    engine = nc.NightCycleEngine(state_path=tmp_path / "s.json")
    result = asyncio.run(engine._run_once("2026-09-28"))
    assert result["memory_sweep"] == {"moved": 2, "stamped": 5}
    assert len(calls) == 1 and calls[0][0] == 1 and before <= calls[0][1] <= time.time()


def test_run_once_survives_a_sweep_that_explodes(monkeypatch, tmp_path):
    nc = _night(monkeypatch)

    async def boom(**k):
        raise RuntimeError("sweep exploded")

    monkeypatch.setattr(ms, "sweep_memory", boom)
    engine = nc.NightCycleEngine(state_path=tmp_path / "s.json")
    result = asyncio.run(engine._run_once("2026-09-28"))
    assert result.get("wrote") == "2026-09-28"
    assert result["memory_sweep"] == {"error": "sweep exploded"}


def test_surface_errors_names_a_failed_sweep():
    from ai.night_cycle import _surface_errors

    assert "memory_sweep: disk full" in _surface_errors({"wrote": "x", "memory_sweep": {"error": "disk full"}})
    assert _surface_errors({"wrote": "x", "memory_sweep": {"moved": 0, "stamped": 3}}) == ""
    assert _surface_errors({"wrote": "x", "memory_sweep": {"skipped": "no_memory_doc"}}) == ""


def test_the_old_deleting_sweep_is_gone():
    import core.turn.reflect as reflect

    for name in ("sweep_active_threads", "run_nightly_memory_sweep", "MEMORY_SWEEP_DIR"):
        assert not hasattr(reflect, name), name
