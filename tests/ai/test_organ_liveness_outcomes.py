"""Liveness reads outcomes (Batch 6, item 3.8; rulings R49–R56).

Every organ is watched, and its status comes from what it WROTE — rows,
events, dated files, commits in her repo — never from a job marker alone.
Three answers, and they mean different things:

- wrote: an outcome inside the window;
- quiet: nothing written, and a trace says it ran without error — it had
  nothing to do. An occasioned organ with nothing in the window is quiet
  too, and never anything else;
- stale: it should have written and did not — a recorded step error, or
  no trace at all when the run was due.

Everything here is real on disk: a SQLite file opened read-only, a git
repo for her pages, the organs' own books and records, and the night
cycle's record of each night (data/night_cycle/.outcomes/<night>.json,
ruling R51).
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import sqlite3
import subprocess
import threading
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

import ai.organ_liveness as ol
from ai.organ_liveness import (
    ORGANS,
    LivenessReport,
    check_liveness,
    flags_from_yaml,
    liveness_flags,
)
from core.settings_schema import CompanionConfig, FeaturesConfig

REPO = Path(__file__).resolve().parents[2]

# A Wednesday in ISO 2026-W33. The 04:00 pass on the 12th looked back on the
# 11th (the due night) and picked for the 12th; the Monday pass (04:00 on
# the 10th) ran the weekly ritual, looking back on Sunday the 9th.
NOW = datetime(2026, 8, 12, 10, 0)
DUE_NIGHT = "2026-08-11"
TODAY = "2026-08-12"
THIS_WEEK = "2026-W33"
LAST_WEEK = "2026-W32"
RITUAL_NIGHT = "2026-08-09"


def _at(*parts: int) -> float:
    return datetime(*parts).timestamp()


def _write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _events(path: Path, *events: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        for ev in events:
            fh.write(json.dumps(ev) + "\n")


def _night(data: Path, night: str, *, steps=None, failed=None, weekly=None) -> None:
    """One night's record, in the shape the night cycle saves it."""
    record = {"night": night, "at": 0.0, "steps": steps or {}, "failed": failed or {}}
    if weekly is not None:
        record["weekly"] = weekly
    _write_json(data / "night_cycle" / ".outcomes" / f"{night}.json", record)


def _stakes_table(db: Path, rows) -> None:
    """A real SQLite file with the ledger's table and ``(kind, updated_at)`` rows."""
    conn = sqlite3.connect(db)
    try:
        conn.execute(
            "CREATE TABLE stakes (id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, "
            "stake TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'open', "
            "created_at REAL NOT NULL, updated_at REAL NOT NULL)")
        for kind, updated in rows:
            conn.execute(
                "INSERT INTO stakes (kind, stake, created_at, updated_at) VALUES (?, ?, ?, ?)",
                (kind, f"a {kind} of hers", updated, updated))
        conn.commit()
    finally:
        conn.close()


def _git(repo: Path, *args: str, when: datetime | None = None) -> None:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(GIT_AUTHOR_NAME="Tester", GIT_AUTHOR_EMAIL="tester@example.invalid",
               GIT_COMMITTER_NAME="Tester", GIT_COMMITTER_EMAIL="tester@example.invalid")
    if when is not None:
        env["GIT_AUTHOR_DATE"] = env["GIT_COMMITTER_DATE"] = when.isoformat()
    subprocess.run(["git", "-c", "commit.gpgsign=false", *args], cwd=repo, env=env,
                   check=True, capture_output=True)


def _commit(repo: Path, page: str, when: datetime) -> None:
    """One page committed to her repo at ``when`` (a real commit, dated)."""
    if not (repo / ".git").exists():
        repo.mkdir(parents=True, exist_ok=True)
        _git(repo, "init", "-q")
    path = repo / page
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{page} as of {when.isoformat()}\n", encoding="utf-8")
    _git(repo, "add", page)
    _git(repo, "commit", "-q", "-m", f"revise {page}", when=when)


def _only(*organs: str) -> dict:
    return {o: o in organs for o in ORGANS}


@pytest.fixture
def home(tmp_path: Path) -> SimpleNamespace:
    data = tmp_path / "data"
    data.mkdir()
    return SimpleNamespace(data=data, db=tmp_path / "palli.sqlite3", repo=tmp_path / "her_repo")


def _check(home: SimpleNamespace, *organs: str, now: datetime = NOW) -> dict:
    report = check_liveness(now=now, data_root=home.data, flags=_only(*organs),
                            db_path=home.db, identity_root=home.repo)
    return {e.name: e for e in report.entries}


# ── the status words ─────────────────────────────────────────────────────

def test_the_report_speaks_three_words_and_no_fourth(home):
    """R49: scripts/organ_liveness.py maps exactly wrote/quiet/stale; a
    fourth would crash it. Nothing on disk at all, every organ on."""
    report = check_liveness(now=NOW, data_root=home.data, db_path=home.db,
                            identity_root=home.repo)
    assert {e.status for e in report.entries} <= {"wrote", "quiet", "stale"}
    # Every organ has a line, except surprise: it rides the present moment,
    # which is stale here.
    assert [e.name for e in report.entries] == [o for o in ORGANS if o != "surprise"]


# ── the stakes, the views, her regrets: rows of one table ────────────────

def test_stakes_with_a_row_updated_today_wrote(home):
    _stakes_table(home.db, [("hope", _at(2026, 8, 12, 9, 0))])
    entry = _check(home, "stakes")["stakes"]
    assert entry.status == "wrote"
    assert "1 row" in entry.detail


def test_stakes_the_review_looked_over_and_left_alone_are_quiet(home):
    _stakes_table(home.db, [("worry", _at(2026, 8, 1, 12, 0))])   # older than the window
    _night(home.data, DUE_NIGHT, steps={"stake_review": {"applied": 0}})
    entry = _check(home, "stakes")["stakes"]
    assert entry.status == "quiet"
    assert DUE_NIGHT in entry.detail


def test_stakes_whose_review_failed_are_stale(home):
    _stakes_table(home.db, [])
    _night(home.data, DUE_NIGHT, steps={"stake_review": {"error": "the call timed out"}})
    entry = _check(home, "stakes")["stakes"]
    assert entry.status == "stale"
    assert "the call timed out" in entry.detail


def test_stakes_with_no_record_of_the_due_night_are_stale(home):
    _stakes_table(home.db, [])
    _night(home.data, "2026-08-10", steps={"stake_review": {"applied": 0}})   # the night before
    entry = _check(home, "stakes")["stakes"]
    assert entry.status == "stale"
    assert DUE_NIGHT in entry.detail


def test_views_and_regrets_have_lines_of_their_own(home):
    _stakes_table(home.db, [("view", _at(2026, 8, 12, 4, 20)), ("regret", _at(2026, 8, 11, 21, 0))])
    _night(home.data, DUE_NIGHT, steps={"stake_review": {"applied": 0}})
    lines = _check(home, "stakes", "views", "regret")
    assert lines["stakes"].status == "quiet"          # a view is not a hope or a worry
    assert lines["views"].status == "wrote"
    assert lines["regret"].status == "wrote"


def test_views_with_zero_rows_and_a_no_view_night_are_quiet(home):
    """The pass ran for the due night and formed nothing: its own record
    (data/views/.state.json, written every night the pass completes) says
    so, and the ledger holds no view."""
    _stakes_table(home.db, [])
    _write_json(home.data / "views" / ".state.json",
                {"last_night": DUE_NIGHT, "formed": 0, "at": _at(2026, 8, 12, 4, 12)})
    entry = _check(home, "views")["views"]
    assert entry.status == "quiet"
    assert "no view formed" in entry.detail


def test_a_view_refused_on_the_due_night_is_stale(home):
    _stakes_table(home.db, [])
    _write_json(home.data / "views" / ".state.json",
                {"last_night": DUE_NIGHT, "formed": 0, "at": _at(2026, 8, 12, 4, 12)})
    _night(home.data, DUE_NIGHT,
           steps={"views": {"day": DUE_NIGHT, "formed": 0, "refused": "view without evidence"}},
           failed={"views": "views refused: view without evidence"})
    entry = _check(home, "views")["views"]
    assert entry.status == "stale"
    assert "view without evidence" in entry.detail


def test_a_twin_view_is_the_rails_working_quiet_in_both_places(home):
    """Fix round 1 (R72): a view refused as one she already holds is the
    rails working — nothing new was needed. The night's health line does
    not name it, so the night's record does not count it as a failure,
    and the table reads quiet with a detail naming the twin. A refusal
    that breaks the contract stays named and stale (the test above)."""
    from ai.night_cycle import _night_record, _surface_errors
    from ai.stake_appraiser import ALREADY_HERS

    twin = {"day": DUE_NIGHT, "formed": 0, "refused": ALREADY_HERS}
    assert _surface_errors({"wrote": DUE_NIGHT, "views": twin}) == ""
    record = _night_record(DUE_NIGHT, {"wrote": DUE_NIGHT, "views": twin}, at=0.0, ritual_now=0.0)
    assert "views" not in record["failed"]

    _stakes_table(home.db, [])
    _write_json(home.data / "views" / ".state.json",
                {"last_night": DUE_NIGHT, "formed": 0, "at": _at(2026, 8, 12, 4, 12)})
    _write_json(home.data / "night_cycle" / ".outcomes" / f"{DUE_NIGHT}.json", record)
    entry = _check(home, "views")["views"]
    assert entry.status == "quiet"
    assert "already one of her views" in entry.detail


def test_a_views_pass_that_has_not_run_since_is_stale(home):
    _stakes_table(home.db, [])
    _write_json(home.data / "views" / ".state.json",
                {"last_night": "2026-08-08", "formed": 0, "at": _at(2026, 8, 9, 4, 12)})
    assert _check(home, "views")["views"].status == "stale"


def test_the_database_is_only_ever_read(home, tmp_path):
    """R54: a plain read-only connection, never stake_ledger's readers,
    which create and alter tables. A database the ledger never touched
    still has no stakes table after the check, its bytes are the same, and
    a database that is not there is not created."""
    conn = sqlite3.connect(home.db)
    conn.execute("CREATE TABLE something_else (x TEXT)")
    conn.commit()
    conn.close()
    before = home.db.read_bytes()
    _night(home.data, DUE_NIGHT, steps={"stake_review": {"applied": 0}})

    lines = _check(home, "stakes", "views", "regret")

    assert lines["stakes"].status == "quiet"          # no table yet = no rows
    assert home.db.read_bytes() == before
    conn = sqlite3.connect(home.db)
    tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]
    conn.close()
    assert tables == ["something_else"]
    missing = tmp_path / "never_made.sqlite3"
    check_liveness(now=NOW, data_root=home.data, flags=_only("stakes"), db_path=missing)
    assert not missing.exists()


def test_an_unreadable_database_is_named_and_never_a_crash(home):
    home.db.write_bytes(b"not a database, only words " * 64)
    _night(home.data, DUE_NIGHT, steps={"stake_review": {"applied": 0}})
    lines = _check(home, "stakes", "views", "regret")
    for name in ("stakes", "views", "regret"):
        assert "database unreadable" in lines[name].detail, name
    assert lines["regret"].status == "quiet"          # occasioned: never stale


# ── tonight's pick ───────────────────────────────────────────────────────

def test_a_pick_for_today_wrote(home):
    _write_json(home.data / "tonight" / f"{TODAY}.json",
                {"day": TODAY, "pick": {"source": "stake", "source_id": "7",
                                        "text": "the thing on my mind", "why": "it came back"},
                 "said_at": None})
    entry = _check(home, "tonight")["tonight"]
    assert entry.status == "wrote"
    assert TODAY in entry.detail


def test_a_night_with_no_pick_is_quiet(home):
    """A null night writes the day's file with "pick": null (Task 1, R4) —
    the trace that the step ran and had nothing to bring up."""
    _write_json(home.data / "tonight" / f"{TODAY}.json", {"day": TODAY, "pick": None})
    entry = _check(home, "tonight")["tonight"]
    assert entry.status == "quiet"
    assert "no pick" in entry.detail


def test_a_pick_that_failed_is_stale(home):
    _night(home.data, DUE_NIGHT, steps={"tonight": {"day": TODAY, "error": "unparseable"}},
           failed={"tonight": "tonight: unparseable"})
    entry = _check(home, "tonight")["tonight"]
    assert entry.status == "stale"
    assert "unparseable" in entry.detail


def test_yesterdays_pick_is_not_todays(home):
    _write_json(home.data / "tonight" / "2026-08-11.json", {"day": "2026-08-11", "pick": None})
    assert _check(home, "tonight")["tonight"].status == "stale"


# ── her small truths ─────────────────────────────────────────────────────

def test_a_truth_planted_since_the_due_night_wrote(home):
    _events(home.data / "disclosures" / "disclosures.jsonl",
            {"at": _at(2026, 8, 12, 4, 15), "op": "plant", "id": "d1", "text": "a small truth"})
    assert _check(home, "disclosures")["disclosures"].status == "wrote"


def test_a_harvest_with_nothing_tonight_is_quiet(home):
    _night(home.data, DUE_NIGHT, steps={"disclosure_harvest": {"faded": 0, "planted": None}})
    assert _check(home, "disclosures")["disclosures"].status == "quiet"


def test_a_harvest_answer_outside_the_contract_is_stale(home):
    _night(home.data, DUE_NIGHT, steps={"disclosure_harvest": {"skipped": "no_receipt"}},
           failed={"disclosure_harvest": "disclosure_harvest refused: no_receipt"})
    entry = _check(home, "disclosures")["disclosures"]
    assert entry.status == "stale"
    assert "no_receipt" in entry.detail


# ── the shared language, weekly ──────────────────────────────────────────

def test_a_bit_planted_this_week_wrote(home):
    _events(home.data / "callbacks" / "callbacks.jsonl",
            {"at": _at(2026, 8, 10, 4, 20), "op": "plant", "id": "c1", "bit": "a bit of theirs"})
    assert _check(home, "callbacks")["callbacks"].status == "wrote"


def test_a_harvest_that_found_no_new_bit_this_week_is_quiet(home):
    _write_json(home.data / "callbacks" / ".state.json",
                {"last_week": THIS_WEEK, "planted": 0, "at": _at(2026, 8, 10, 4, 20)})
    entry = _check(home, "callbacks")["callbacks"]
    assert entry.status == "quiet"
    assert THIS_WEEK in entry.detail


def test_a_harvest_that_failed_on_the_due_night_is_stale(home):
    _write_json(home.data / "callbacks" / ".state.json",
                {"last_week": LAST_WEEK, "planted": 0, "at": _at(2026, 8, 3, 4, 20)})
    _night(home.data, DUE_NIGHT, steps={"callbacks_harvest": {"week": THIS_WEEK, "error": "TimeoutError"}},
           failed={"callbacks_harvest": "callbacks_harvest: TimeoutError"})
    entry = _check(home, "callbacks")["callbacks"]
    assert entry.status == "stale"
    assert "TimeoutError" in entry.detail


def test_no_harvest_this_week_or_last_is_stale(home):
    _write_json(home.data / "callbacks" / ".state.json",
                {"last_week": "2026-W30", "planted": 0, "at": _at(2026, 7, 20, 4, 20)})
    assert _check(home, "callbacks")["callbacks"].status == "stale"


# ── the fears book, weekly ───────────────────────────────────────────────

def test_a_fear_faced_this_week_wrote(home):
    _events(home.data / "fears" / "fears.jsonl",
            {"at": _at(2026, 8, 10, 4, 30), "op": "face", "id": "f1", "note": "faced it"})
    assert _check(home, "fears")["fears"].status == "wrote"


def test_a_relook_that_found_every_fear_still_true_is_quiet(home):
    _night(home.data, RITUAL_NIGHT, weekly={
        "week": THIS_WEEK, "pages": {}, "fears": {"updates_applied": 0, "note": "all_still"}})
    entry = _check(home, "fears")["fears"]
    assert entry.status == "quiet"
    assert THIS_WEEK in entry.detail


def test_a_plant_the_rails_refused_is_still_a_quiet_week(home):
    _night(home.data, RITUAL_NIGHT, weekly={
        "week": THIS_WEEK, "pages": {},
        "fears": {"updates_applied": 0, "note": "empty_book",
                  "plant_refused": "one already planted this week"}})
    entry = _check(home, "fears")["fears"]
    assert entry.status == "quiet"
    assert "one already planted this week" in entry.detail


def test_a_ritual_that_never_reached_the_relook_is_stale(home):
    """Task 3's review: "the weekly ritual never reached the re-look" and
    "the re-look ran and had nothing to plant" must read differently."""
    _night(home.data, RITUAL_NIGHT, weekly={
        "week": THIS_WEEK, "pages": {}, "fears": {"not_reached": "authorship_empty"}})
    entry = _check(home, "fears")["fears"]
    assert entry.status == "stale"
    assert "never reached" in entry.detail and "authorship_empty" in entry.detail


def test_a_relook_the_gate_refused_is_stale(home):
    _night(home.data, RITUAL_NIGHT, weekly={
        "week": THIS_WEEK, "pages": {}, "fears": {"rejected": "not grounded in the week"}})
    entry = _check(home, "fears")["fears"]
    assert entry.status == "stale"
    assert "not grounded in the week" in entry.detail


def test_no_record_of_the_relook_is_stale(home):
    assert _check(home, "fears")["fears"].status == "stale"


def test_a_touch_is_the_moments_noticing_not_the_book_written(home):
    _events(home.data / "fears" / "fears.jsonl",
            {"at": _at(2026, 8, 11, 22, 0), "op": "touch", "id": "f1", "line": "it brushed past"})
    assert _check(home, "fears")["fears"].status == "stale"


# ── her story pages: commits, never the marker alone (R53) ───────────────

def _marker(home, organ: str, week: str) -> None:
    _write_json(home.data / organ / ".state.json", {"last_week": week})


def test_narrative_with_a_commit_this_week_wrote(home):
    _marker(home, "narrative_self", THIS_WEEK)
    _commit(home.repo, "MY_STORY.md", datetime(2026, 8, 10, 4, 5))
    entry = _check(home, "narrative_self")["narrative_self"]
    assert entry.status == "wrote"
    assert "committed" in entry.detail


def test_narrative_with_a_job_marker_but_no_commit_this_week_is_stale(home):
    """The owner's answer, 09-29: the marker isn't proof. It is written
    before the work; a week whose every page was refused kept it (W36)."""
    _marker(home, "narrative_self", THIS_WEEK)
    _commit(home.repo, "MY_STORY.md", datetime(2026, 8, 3, 4, 5))   # last week's
    entry = _check(home, "narrative_self")["narrative_self"]
    assert entry.status == "stale"
    assert THIS_WEEK in entry.detail


def test_narrative_whose_pages_all_came_back_unchanged_is_quiet(home):
    _marker(home, "narrative_self", THIS_WEEK)
    _commit(home.repo, "MY_STORY.md", datetime(2026, 8, 3, 4, 5))
    _night(home.data, RITUAL_NIGHT, weekly={
        "week": THIS_WEEK,
        "pages": {"MY_STORY.md": {"committed": "no_change"},
                  "STORY_OF_US.md": {"committed": "no_change"}},
        "fears": {"updates_applied": 0, "note": "all_still"}})
    entry = _check(home, "narrative_self")["narrative_self"]
    assert entry.status == "quiet"
    assert "unchanged" in entry.detail


def test_narrative_with_a_page_refused_and_the_rest_unchanged_is_stale(home):
    _marker(home, "narrative_self", THIS_WEEK)
    _commit(home.repo, "MY_STORY.md", datetime(2026, 8, 3, 4, 5))
    _night(home.data, RITUAL_NIGHT, weekly={
        "week": THIS_WEEK,
        "pages": {"MY_STORY.md": {"rejected": "size_cap: 3400 > 3000"},
                  "STORY_OF_US.md": {"committed": "no_change"}},
        "fears": {"updates_applied": 0, "note": "all_still"}})
    assert _check(home, "narrative_self")["narrative_self"].status == "stale"


def test_a_journal_or_interests_commit_is_not_her_story_pages(home):
    """The log is filtered by path: the journal and the interests page
    commit to the same repo as her story pages."""
    _marker(home, "narrative_self", THIS_WEEK)
    _marker(home, "passions", THIS_WEEK)
    _commit(home.repo, "MY_STORY.md", datetime(2026, 8, 3, 4, 5))
    _commit(home.repo, "journal/2026-08-10.md", datetime(2026, 8, 11, 4, 6))
    _commit(home.repo, "INTERESTS.md", datetime(2026, 8, 10, 4, 7))
    lines = _check(home, "narrative_self", "passions")
    assert lines["narrative_self"].status == "stale"
    assert lines["passions"].status == "wrote"


def test_a_marker_two_weeks_behind_is_stale_even_with_a_later_commit(home):
    """An off-cycle correction pass commits story pages without claiming
    the week; the weekly ritual itself has still not run in two weeks."""
    _marker(home, "narrative_self", "2026-W31")
    _commit(home.repo, "MY_STORY.md", datetime(2026, 8, 11, 4, 5))
    assert _check(home, "narrative_self")["narrative_self"].status == "stale"


def test_an_unreadable_repo_is_named_and_never_a_crash(home):
    _marker(home, "narrative_self", THIS_WEEK)
    home.repo.mkdir()
    (home.repo / ".git").write_text("gitdir: /nowhere/at/all\n", encoding="utf-8")
    entry = _check(home, "narrative_self")["narrative_self"]
    assert entry.status == "stale"
    assert "her repo unreadable" in entry.detail


def test_a_missing_repo_is_named_not_read_as_no_page(home):
    """Fix round 1 (R72): a wrong IDENTITY_ROOT_PATH is not "no page
    committed" — the line names the path where no repo was found."""
    import ai.organ_liveness_sources as src

    _marker(home, "narrative_self", THIS_WEEK)
    assert not home.repo.exists()
    entry = _check(home, "narrative_self")["narrative_self"]
    assert entry.status == "stale"
    assert f"no repo at {home.repo}" in entry.detail
    home.repo.mkdir()                                   # a folder, but not a repo
    assert src.last_commit_at(home.repo, ["MY_STORY.md"]) == (None, f"no repo at {home.repo}")


def test_passions_claimed_this_week_with_no_page_committed_is_stale(home):
    _marker(home, "passions", THIS_WEEK)
    _commit(home.repo, "INTERESTS.md", datetime(2026, 8, 3, 4, 7))
    assert _check(home, "passions")["passions"].status == "stale"


def test_passions_that_found_nothing_to_change_are_quiet(home):
    """A week of no change gives the interests claim back, so the marker
    falls behind; the night's record says the pass ran and chose no change."""
    _marker(home, "passions", "2026-W30")
    _night(home.data, DUE_NIGHT, steps={"passions": {
        "journal": {"skipped": "quiet_day"}, "interests": {"skipped": "no_change"}}})
    assert _check(home, "passions")["passions"].status == "quiet"


# ── surprise rides the present moment ────────────────────────────────────

def _moments(home, day: str, *metas: dict) -> None:
    _events(home.data / "present_moment" / f"{day}.jsonl",
            *[{"text": "the moment", "written_at": 0.0, "meta": m} for m in metas])


def test_a_moment_that_carried_a_surprise_wrote(home):
    _moments(home, TODAY, {}, {"surprise": {"about": "them", "line": "up early", "weight": "real"}})
    assert _check(home, "present_moment", "surprise")["surprise"].status == "wrote"


def test_moments_without_a_surprise_are_quiet(home):
    _moments(home, TODAY, {}, {})
    entry = _check(home, "present_moment", "surprise")["surprise"]
    assert entry.status == "quiet"
    assert "2 moment" in entry.detail


def test_surprise_takes_no_line_of_its_own_while_the_present_moment_is_stale(home):
    lines = _check(home, "present_moment", "surprise")
    assert lines["present_moment"].status == "stale"
    assert "surprise" not in lines


# ── the day's gist ───────────────────────────────────────────────────────

def test_a_day_with_no_gist_to_write_is_quiet(home):
    _night(home.data, DUE_NIGHT, steps={"consolidation": {"day_gist": {
        "day": DUE_NIGHT, "wrote": False, "skipped": True, "reason": "no_material"}}})
    entry = _check(home, "day_gists")["day_gists"]
    assert entry.status == "quiet"
    assert "no_material" in entry.detail


def test_a_gist_that_failed_is_stale(home):
    _night(home.data, DUE_NIGHT, steps={"consolidation": {"day_gist": {"error": "no brain"}}},
           failed={"consolidation": "day_gist: no brain"})
    assert _check(home, "day_gists")["day_gists"].status == "stale"


# ── nights before the record began (fix round 1, R72) ────────────────────
# The night's record starts with Batch 6: a night run by the code before it
# left none. Records began on the oldest night on file; a night older than
# that has no evidence either way and is not blamed. No record at all keeps
# the fresh-install answer (the tests above with no record: stale when due).

def test_a_due_night_older_than_the_first_record_is_quiet_and_says_when_it_began(home):
    _stakes_table(home.db, [])
    _night(home.data, TODAY, steps={"memory_sweep": {"moved": 0, "stamped": 0}})   # the first on file
    entry = _check(home, "stakes")["stakes"]
    assert entry.status == "quiet"
    assert f"the night of {DUE_NIGHT}: not recorded" in entry.detail
    assert f"the night's record began {TODAY}" in entry.detail


def test_a_due_night_on_or_after_the_first_record_with_no_entry_is_still_stale(home):
    """The first record is the due night's own, and the harvest is not in
    it: the pass should have run it and left a trace."""
    _night(home.data, DUE_NIGHT, steps={"stake_review": {"applied": 0}})
    entry = _check(home, "disclosures")["disclosures"]
    assert entry.status == "stale"
    assert f"no record of the night of {DUE_NIGHT}" in entry.detail


def test_fears_in_the_week_the_record_began_are_quiet_until_its_first_recorded_ritual(home):
    # The record began on the due night (the Tuesday of W33). W33's ritual
    # ran on the Monday pass, before it, and left no record.
    _night(home.data, DUE_NIGHT, steps={"narrative": {"skipped": "already_this_week"}})
    entry = _check(home, "fears")["fears"]
    assert entry.status == "quiet"
    assert f"the night's record began {DUE_NIGHT}" in entry.detail

    # W34's ritual ran after the record began: judged normally from then on.
    next_week = datetime(2026, 8, 19, 10, 0)            # the Wednesday of W34
    missing = _check(home, "fears", now=next_week)["fears"]
    assert missing.status == "stale"
    assert "no record of the weekly re-look" in missing.detail
    _night(home.data, "2026-08-16", weekly={"week": "2026-W34", "pages": {},
                                            "fears": {"rejected": "not grounded in the week"}})
    assert _check(home, "fears", now=next_week)["fears"].status == "stale"
    _night(home.data, "2026-08-16", weekly={"week": "2026-W34", "pages": {},
                                            "fears": {"updates_applied": 0, "note": "all_still"}})
    ran = _check(home, "fears", now=next_week)["fears"]
    assert ran.status == "quiet"
    assert "2026-W34" in ran.detail


def test_a_claimed_week_whose_ritual_ran_before_the_record_began_is_quiet(home):
    """Its every-page-unchanged answer lives in the record; the ritual that
    claimed the week ran before there was one."""
    _marker(home, "narrative_self", THIS_WEEK)
    _commit(home.repo, "MY_STORY.md", datetime(2026, 8, 3, 4, 5))    # last week's
    _night(home.data, DUE_NIGHT, steps={"narrative": {"skipped": "already_this_week"}})
    entry = _check(home, "narrative_self")["narrative_self"]
    assert entry.status == "quiet"
    assert f"the night's record began {DUE_NIGHT}" in entry.detail

    # The ritual's own night on record, with no weekly section: stale.
    _night(home.data, RITUAL_NIGHT, steps={"narrative": {"skipped": "flag_off"}})
    assert _check(home, "narrative_self")["narrative_self"].status == "stale"


# ── a record that cannot be read (fix round 1, R72) ──────────────────────

def _garbled(home, night: str) -> None:
    path = home.data / "night_cycle" / ".outcomes" / f"{night}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"night": "cut off mid-wri', encoding="utf-8")


def test_an_unreadable_record_of_the_due_night_says_so_and_is_stale(home):
    """The pass wrote a record it cannot prove: not "no record", and not
    quiet on the organ's own trace either — the record might have named a
    failure."""
    _stakes_table(home.db, [])
    _write_json(home.data / "views" / ".state.json",
                {"last_night": DUE_NIGHT, "formed": 0, "at": _at(2026, 8, 12, 4, 12)})
    _garbled(home, DUE_NIGHT)
    lines = _check(home, "stakes", "views")
    for name in ("stakes", "views"):
        assert lines[name].status == "stale", name
        assert f"the record of the night of {DUE_NIGHT} could not be read" in lines[name].detail, name


def test_an_unreadable_record_of_the_ritual_night_says_so(home):
    _night(home.data, "2026-08-02", steps={})            # the record began before W33
    _garbled(home, RITUAL_NIGHT)
    entry = _check(home, "fears")["fears"]
    assert entry.status == "stale"
    assert f"the record of the night of {RITUAL_NIGHT} could not be read" in entry.detail


# ── a failed step is never hidden by other writers (fix wave, R74 I1) ────
# Stakes, views and fears have writers outside their scheduled step: a
# turn's appraisal updates hopes and worries, a view is updated or muted,
# a reflection plants a fear. A failure the record names for the step is
# read FIRST, so those writes never hide a step that fails every night; the
# detail names what was written all the same.

def test_stakes_whose_review_failed_are_stale_even_with_rows_a_turn_updated(home):
    _stakes_table(home.db, [("hope", _at(2026, 8, 12, 9, 0))])
    _night(home.data, DUE_NIGHT, steps={"stake_review": {"error": "the call timed out"}},
           failed={"stake_review": "stake_review: the call timed out"})
    entry = _check(home, "stakes")["stakes"]
    assert entry.status == "stale"
    assert "stake_review: the call timed out" in entry.detail
    assert f"1 row(s) updated since {DUE_NIGHT}" in entry.detail
    # The same row and a review that ran without a failure: wrote, as before.
    _night(home.data, DUE_NIGHT, steps={"stake_review": {"applied": 0}})
    entry = _check(home, "stakes")["stakes"]
    assert entry.status == "wrote"
    assert entry.detail == f"1 row(s) updated since {DUE_NIGHT}"


def test_views_whose_pass_failed_are_stale_even_with_a_view_a_turn_updated(home):
    _stakes_table(home.db, [("view", _at(2026, 8, 12, 9, 0))])
    _night(home.data, DUE_NIGHT, steps={"views": {"error": "TimeoutError"}},
           failed={"views": "views: TimeoutError"})
    entry = _check(home, "views")["views"]
    assert entry.status == "stale"
    assert "views: TimeoutError" in entry.detail
    assert f"1 view(s) updated since {DUE_NIGHT}" in entry.detail
    _night(home.data, DUE_NIGHT, steps={"views": {"day": DUE_NIGHT, "formed": 0}})
    entry = _check(home, "views")["views"]
    assert entry.status == "wrote"
    assert entry.detail == f"1 view(s) updated since {DUE_NIGHT}"


def test_fears_whose_relook_failed_are_stale_even_with_a_fear_a_reflection_planted(home):
    _events(home.data / "fears" / "fears.jsonl",
            {"at": _at(2026, 8, 11, 21, 0), "op": "plant", "id": "f9", "source": "reflection",
             "text": "a fear of hers"})
    _night(home.data, RITUAL_NIGHT, weekly={
        "week": THIS_WEEK, "pages": {}, "fears": {"error": "the call timed out"}})
    entry = _check(home, "fears")["fears"]
    assert entry.status == "stale"
    assert "the call timed out" in entry.detail and "1 plant" in entry.detail
    # A ritual that ended in an error before it reached the re-look.
    _night(home.data, RITUAL_NIGHT, weekly={
        "week": THIS_WEEK, "pages": {}, "fears": {"not_reached": "week_marker_unwritable"}})
    entry = _check(home, "fears")["fears"]
    assert entry.status == "stale"
    assert "never reached the re-look: week_marker_unwritable" in entry.detail
    assert "1 plant" in entry.detail
    # A re-look the gate refused: the fears line's third recorded failure.
    _night(home.data, RITUAL_NIGHT, weekly={
        "week": THIS_WEEK, "pages": {}, "fears": {"rejected": "not grounded in the week"}})
    assert _check(home, "fears")["fears"].status == "stale"
    # A re-look that ran without a failure: wrote, as before.
    _night(home.data, RITUAL_NIGHT, weekly={
        "week": THIS_WEEK, "pages": {}, "fears": {"updates_applied": 0, "note": "all_still"}})
    entry = _check(home, "fears")["fears"]
    assert entry.status == "wrote"
    assert "1 plant" in entry.detail


def test_a_ritual_skipped_for_a_lawful_reason_is_no_failure(home):
    """The fears organ off inside the ritual, the first authorship still
    pending, the week already done: none of them is a failure, so the
    fear a reflection planted still reads wrote."""
    _events(home.data / "fears" / "fears.jsonl",
            {"at": _at(2026, 8, 11, 21, 0), "op": "plant", "id": "f9", "source": "reflection",
             "text": "a fear of hers"})
    for skipped in ("first_authorship_pending", "already_this_week", "flag_off"):
        _night(home.data, DUE_NIGHT, steps={"narrative": {"skipped": skipped}})
        assert _check(home, "fears")["fears"].status == "wrote", skipped
    _night(home.data, RITUAL_NIGHT, weekly={
        "week": THIS_WEEK, "pages": {}, "fears": {"skipped": "flag_off"}})
    assert _check(home, "fears")["fears"].status == "wrote"


def test_a_harvest_that_failed_is_stale_even_with_last_weeks_bit_in_the_window(home):
    """The same rule for the shared language: its window reaches back to
    the earlier lawful week, so last week's plant would hide this week's
    broken harvest."""
    _events(home.data / "callbacks" / "callbacks.jsonl",
            {"at": _at(2026, 8, 3, 4, 20), "op": "plant", "id": "c1", "bit": "a bit of theirs"})
    _night(home.data, DUE_NIGHT, steps={"callbacks_harvest": {"week": THIS_WEEK, "error": "TimeoutError"}},
           failed={"callbacks_harvest": "callbacks_harvest: TimeoutError"})
    entry = _check(home, "callbacks")["callbacks"]
    assert entry.status == "stale"
    assert "TimeoutError" in entry.detail and "1 plant" in entry.detail


def test_a_truth_harvest_that_failed_is_stale_even_with_the_night_befores_truth(home):
    """And for her small truths: the window opens at midnight of the due
    night, so the plant of the pass that ran that morning is inside it."""
    _events(home.data / "disclosures" / "disclosures.jsonl",
            {"at": _at(2026, 8, 11, 4, 15), "op": "plant", "id": "d1", "text": "a small truth"})
    _night(home.data, DUE_NIGHT, steps={"disclosure_harvest": {"error": "no brain"}},
           failed={"disclosure_harvest": "disclosure_harvest: no brain"})
    entry = _check(home, "disclosures")["disclosures"]
    assert entry.status == "stale"
    assert "no brain" in entry.detail and "1 plant" in entry.detail


def test_a_pick_whose_step_failed_is_stale_even_with_the_days_file(home):
    _write_json(home.data / "tonight" / f"{TODAY}.json",
                {"day": TODAY, "pick": {"source": "stake", "source_id": "7",
                                        "text": "the thing on my mind", "why": "it came back"}})
    _night(home.data, DUE_NIGHT, steps={"tonight": {"error": "the pen broke"}},
           failed={"tonight": "tonight: the pen broke"})
    entry = _check(home, "tonight")["tonight"]
    assert entry.status == "stale"
    assert "the pen broke" in entry.detail and f"a pick for {TODAY}" in entry.detail


# ── nights have run, and the record has not begun (fix wave, R73) ────────
# Between the restart onto this code and its first night there is no
# night_cycle/.outcomes/ folder, while the night files of the code before
# are there. Every scheduled organ the record would judge reads quiet and
# says the record begins with the next night; the night cycle's own line
# still judges the pass itself. A fresh install — no night has run — keeps
# its answer.

BEGINS = "the night's record begins with the next night"


def _night_file(home, night: str) -> None:
    """A reflection the night cycle wrote for ``night``: a night has run."""
    path = home.data / "night_cycle" / f"{night}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"ok": true}\n', encoding="utf-8")


def test_before_the_first_record_every_scheduled_organ_is_quiet_and_says_so(home):
    _night_file(home, DUE_NIGHT)
    _stakes_table(home.db, [])
    _marker(home, "narrative_self", THIS_WEEK)
    _commit(home.repo, "MY_STORY.md", datetime(2026, 8, 3, 4, 5))     # last week's
    scheduled = ("stakes", "views", "disclosures", "tonight", "rest_of_night",
                 "narrative_self", "callbacks", "fears")
    lines = _check(home, "night_cycle", *scheduled)
    assert lines["night_cycle"].status == "wrote"
    for name in scheduled:
        assert lines[name].status == "quiet", (name, lines[name].detail)
        assert BEGINS in lines[name].detail, name
    assert not (home.data / "night_cycle" / ".outcomes").exists()      # only read


def test_before_the_first_record_the_night_cycles_own_line_still_judges_the_pass(home):
    _night_file(home, "2026-08-01")                     # the pass stopped long ago
    lines = _check(home, "night_cycle", "stakes", "callbacks", "fears")
    assert lines["night_cycle"].status == "stale"
    assert {lines[n].status for n in ("stakes", "callbacks", "fears")} == {"quiet"}


def test_a_night_engine_claim_file_is_a_night_run_too(home):
    _write_json(home.data / "night_cycle" / ".engine_state.json", {"last_run_date": DUE_NIGHT})
    entry = _check(home, "stakes")["stakes"]
    assert entry.status == "quiet"
    assert BEGINS in entry.detail


def test_a_fresh_install_where_no_night_has_run_keeps_its_answer(home):
    """No night file and no record: nothing was ever begun, so the check
    itself keeps the stale answer when due, and the doctor and the CLI say
    no night has run (install_report is None)."""
    lines = _check(home, "stakes", "callbacks", "fears", "rest_of_night")
    assert {e.status for e in lines.values()} == {"stale"}
    assert all(BEGINS not in e.detail for e in lines.values())
    assert ol.install_report(home.data.parent, now=NOW) is None


def test_a_record_folder_with_nothing_in_it_is_not_a_record_not_yet_begun(home):
    """The folder is made by the first write under this code; one with no
    record in it means a pass under this code could not leave one."""
    _night_file(home, DUE_NIGHT)
    (home.data / "night_cycle" / ".outcomes").mkdir()
    entry = _check(home, "stakes")["stakes"]
    assert entry.status == "stale"
    assert BEGINS not in entry.detail


# ── the rest of the night (fix wave, R74 M7) ─────────────────────────────
# The night's steps with no line of their own — the memory sweep, the
# preferences rebuild, the thread retirement, the owner picture, the
# targeted correction pass, the consolidation's aging and reconciling, and
# any step added later — share one line, judged by the record alone.

def test_the_rest_of_the_nights_steps_that_failed_are_stale_and_named(home):
    _night(home.data, DUE_NIGHT,
           steps={"memory_sweep": {"error": "the store refused"},
                  "preferences_rebuild": {"status": "partial", "failed": 2},
                  "thread_retirement": {"retired": 0, "older_than_days": 30},
                  "owner_picture": {"error": "timeout"},
                  "narrative_targeted": {"error": "the gate broke"}},
           failed={"memory_sweep": "memory_sweep: the store refused",
                   "preferences_rebuild": "preferences_rebuild partial: 2 rows failed",
                   "owner_picture": "owner_picture: timeout",
                   "narrative_targeted": "narrative_targeted: the gate broke"})
    entry = _check(home, "rest_of_night")["rest_of_night"]
    assert entry.status == "stale"
    for named in ("memory_sweep: the store refused", "preferences_rebuild partial: 2 rows failed",
                  "owner_picture: timeout", "narrative_targeted: the gate broke"):
        assert named in entry.detail
    assert "thread_retirement" not in entry.detail
    assert entry.detail.startswith(f"the night of {DUE_NIGHT}: ")


def test_a_step_added_later_without_a_line_is_watched_too(home):
    _night(home.data, DUE_NIGHT, steps={"something_new": {"error": "it broke"}},
           failed={"something_new": "something_new: it broke"})
    entry = _check(home, "rest_of_night")["rest_of_night"]
    assert entry.status == "stale"
    assert "something_new: it broke" in entry.detail


def test_the_rest_of_the_night_with_no_failure_is_quiet(home):
    _night(home.data, DUE_NIGHT, steps={
        "memory_sweep": {"moved": 0, "stamped": 0}, "preferences_rebuild": {"status": "ok"},
        "thread_retirement": {"retired": 0, "older_than_days": 30},
        "owner_picture": {"skipped": "flag_off"},
        "narrative_targeted": {"skipped": "no_corrected_beliefs_in_docs"}})
    entry = _check(home, "rest_of_night")["rest_of_night"]
    assert entry.status == "quiet"
    assert DUE_NIGHT in entry.detail


def test_a_step_with_a_line_of_its_own_is_not_named_again(home):
    _night(home.data, DUE_NIGHT,
           steps={"reflection": {"error": "empty_reflection"},
                  "consolidation": {"day_gist": {"error": "no brain"}},
                  "stake_review": {"error": "boom"}, "tonight": {"error": "unparseable"},
                  "memory_sweep": {"moved": 0, "stamped": 0}},
           failed={"reflection": "reflection: empty_reflection",
                   "consolidation": "day_gist: no brain",
                   "stake_review": "stake_review: boom", "tonight": "tonight: unparseable"})
    assert _check(home, "rest_of_night")["rest_of_night"].status == "quiet"


def test_the_consolidations_aging_and_reconciling_have_no_line_but_this_one(home):
    """day_gists judges the day's gist; the aging and the reconciling that
    ride the same step have no line of their own."""
    _night(home.data, DUE_NIGHT,
           steps={"consolidation": {"day_gist": {"wrote": True}, "aging": {"error": "disk full"},
                                    "reconcile": {"corrections": 0}}},
           failed={"consolidation": "aging: disk full"})
    entry = _check(home, "rest_of_night")["rest_of_night"]
    assert entry.status == "stale"
    assert "consolidation aging: disk full" in entry.detail
    _night(home.data, DUE_NIGHT,
           steps={"consolidation": {"error": "the store is locked"}},
           failed={"consolidation": "consolidation: the store is locked"})
    entry = _check(home, "rest_of_night")["rest_of_night"]
    assert entry.status == "stale"
    assert "consolidation: the store is locked" in entry.detail


def test_the_rest_of_the_night_keeps_the_records_floors(home):
    # No record of the due night once the record began: stale, like any
    # scheduled organ whose run was due.
    _night(home.data, "2026-08-10", steps={})
    entry = _check(home, "rest_of_night")["rest_of_night"]
    assert entry.status == "stale"
    assert f"no record of the night of {DUE_NIGHT}" in entry.detail
    # A record of the due night that cannot be read: stale, and says so.
    _garbled(home, DUE_NIGHT)
    entry = _check(home, "rest_of_night")["rest_of_night"]
    assert entry.status == "stale"
    assert f"the record of the night of {DUE_NIGHT} could not be read" in entry.detail


def test_the_rest_of_the_night_before_the_record_began_is_quiet(home):
    _night(home.data, TODAY, steps={"memory_sweep": {"moved": 0, "stamped": 0}})   # the first on file
    entry = _check(home, "rest_of_night")["rest_of_night"]
    assert entry.status == "quiet"
    assert f"the night of {DUE_NIGHT}: not recorded" in entry.detail
    assert f"the night's record began {TODAY}" in entry.detail


def test_the_rest_of_the_night_rides_the_night_cycle():
    def flags(night_cycle: bool) -> dict:
        return liveness_flags(SimpleNamespace(companion=CompanionConfig(night_cycle=night_cycle),
                                              features=FeaturesConfig()))

    assert flags(True)["rest_of_night"] is True
    assert flags(False)["rest_of_night"] is False


def test_the_rest_of_the_night_is_left_out_while_the_night_cycle_is_off(home):
    off = liveness_flags(SimpleNamespace(companion=CompanionConfig(night_cycle=False),
                                         features=FeaturesConfig()))
    report = check_liveness(now=NOW, data_root=home.data, flags=off, db_path=home.db,
                            identity_root=home.repo)
    assert "rest_of_night" not in [e.name for e in report.entries]


# ── the occasioned organs: wrote or quiet, never stale ───────────────────

OCCASIONED = ("daydream", "workbench", "arbiter", "corrections", "forge", "regret", "hands")


def test_occasioned_organs_with_nothing_are_quiet_never_stale(home, monkeypatch):
    monkeypatch.setattr(ol, "_read_deeds", lambda root=None: [])
    report = check_liveness(now=NOW, data_root=home.data, flags=_only(*OCCASIONED),
                            db_path=home.db, identity_root=home.repo)
    assert {e.name: e.status for e in report.entries} == {o: "quiet" for o in OCCASIONED}
    assert report.healthy


def test_a_correction_made_today_wrote(home):
    _events(home.data / "corrections" / "corrections.jsonl",
            {"at": _at(2026, 8, 12, 9, 30), "op": "plant", "id": "k1",
             "belief": "an old belief", "truth": "what is true"})
    assert _check(home, "corrections")["corrections"].status == "wrote"


def test_a_tool_forged_today_wrote_and_an_old_one_is_quiet(home):
    """The manifest's created_at is an ISO string (R54), in UTC."""
    manifest = home.data / "forge" / "manifest.json"
    _write_json(manifest, [{"name": "old_tool", "created_at": "2026-08-01T00:00:00+00:00"}])
    assert _check(home, "forge")["forge"].status == "quiet"
    _write_json(manifest, [
        {"name": "old_tool", "created_at": "2026-08-01T00:00:00+00:00"},
        {"name": "new_tool", "created_at": datetime(2026, 8, 12, 6, 0, tzinfo=timezone.utc).isoformat()},
    ])
    assert _check(home, "forge")["forge"].status == "wrote"


# ── flags (R49) ──────────────────────────────────────────────────────────

def test_an_organ_whose_flag_is_off_is_left_out_never_stale(home):
    report = check_liveness(now=NOW, data_root=home.data, flags={"views": False, "fears": False},
                            db_path=home.db, identity_root=home.repo)
    names = [e.name for e in report.entries]
    assert "views" not in names and "fears" not in names
    assert "stakes" in names                            # the rest are still judged


def test_the_flags_follow_the_code_that_writes_each_organ():
    settings = SimpleNamespace(
        companion=CompanionConfig(night_cycle=True, stake_ledger=True, opinions=True,
                                  present_moment=True, surprise=True, narrative_self=True,
                                  fears=True, tonight=False),
        features=FeaturesConfig(forged_tools=True, her_hands=False))
    flags = liveness_flags(settings)
    assert set(flags) == set(ORGANS)
    assert flags["stakes"] and flags["views"] and flags["surprise"] and flags["fears"]
    assert flags["forge"] and not flags["hands"] and not flags["tonight"]
    assert flags["inner_monologue"] and flags["corrections"]        # no flag: always on


def test_every_organ_that_rides_the_night_is_off_with_the_night():
    everything = dict(stake_ledger=True, opinions=True, vulnerability=True, tonight=True,
                      consolidation=True, narrative_self=True, passions=True, idiolect=True,
                      fears=True, own_work=True, wanting=True, present_moment=True)
    flags = liveness_flags(SimpleNamespace(companion=CompanionConfig(night_cycle=False, **everything),
                                           features=FeaturesConfig()))
    for organ in ("night_cycle", "day_gists", "stakes", "views", "disclosures", "tonight",
                  "narrative_self", "passions", "callbacks", "fears"):
        assert flags[organ] is False, organ
    assert flags["workbench"] and flags["daydream"] and flags["present_moment"]


def test_the_fears_relook_rides_the_story_pages():
    flags = liveness_flags(SimpleNamespace(
        companion=CompanionConfig(night_cycle=True, fears=True, narrative_self=False),
        features=FeaturesConfig()))
    assert flags["fears"] is False


def test_no_settings_means_every_organ_is_expected():
    assert liveness_flags(None) is None


def test_flags_are_read_from_the_settings_file(tmp_path):
    raw = yaml.safe_load((REPO / "data" / "palli.example.yaml").read_text(encoding="utf-8"))
    raw["companion"]["night_cycle"] = True
    raw["companion"]["tonight"] = False
    path = tmp_path / "palli.yaml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    flags = flags_from_yaml(path)
    assert flags["night_cycle"] is True and flags["tonight"] is False
    assert flags_from_yaml(tmp_path / "not_there.yaml") is None


# ── the night's record (R51) ─────────────────────────────────────────────

def _stub_the_night(monkeypatch, **results):
    """Every step of the night faked, each returning ``results[step]`` (or an
    ordinary nothing-to-do answer), the way the sibling wiring tests fake them."""
    import ai.callbacks_harvest as callbacks_harvest
    import ai.consolidation as cons
    import ai.disclosure_harvest as disclosure_harvest
    import ai.memory_sweep as memory_sweep
    import ai.narrative_self as ns
    import ai.night_cycle as nc
    import ai.owner_picture as owner_picture
    import ai.passions as passions
    import ai.stake_ledger as stake_ledger
    import ai.stake_review as stake_review
    import ai.tonight as tonight
    import core.database as database
    import core.memory_service as memory_service

    def answer(step, default):
        async def fake(*_args, **_kw):
            value = results.get(step, default)
            if isinstance(value, Exception):
                raise value
            return value
        return fake

    async def owner():
        return 1

    async def material(*, owner_id, date):
        return "material"

    monkeypatch.setattr(nc, "_resolve_owner_id", owner)
    monkeypatch.setattr(nc, "_gather_day_material", material)
    monkeypatch.setattr(nc, "run_night_cycle", answer("reflection", {"wrote": "2026-08-09"}))
    monkeypatch.setattr(cons, "run_nightly_consolidation", answer("consolidation", {"skipped": "flag_off"}))
    monkeypatch.setattr(ns, "run_weekly_ritual", answer("narrative", {"skipped": "already_this_week"}))
    monkeypatch.setattr(callbacks_harvest, "run_weekly_callbacks_harvest",
                        answer("callbacks_harvest", {"skipped": "already_ran", "week": THIS_WEEK}))
    monkeypatch.setattr(ns, "run_targeted_correction_pass",
                        answer("narrative_targeted", {"skipped": "no_corrected_beliefs_in_docs"}))
    monkeypatch.setattr(stake_review, "run_nightly_stake_review", answer("stake_review", {"applied": 0}))
    monkeypatch.setattr(stake_ledger, "ledger_enabled", lambda: False)   # the views pass: flag_off
    monkeypatch.setattr(memory_sweep, "sweep_memory", answer("memory_sweep", {"moved": 0, "stamped": 0}))
    monkeypatch.setattr(memory_service, "rebuild_user_memory_from_documents",
                        answer("preferences_rebuild", {"status": "ok"}))
    monkeypatch.setattr(database.brain, "retire_unsurfaced_open_threads", answer("retire", 0))
    monkeypatch.setattr(owner_picture, "run_nightly_owner_picture", answer("owner_picture", {"skipped": "flag_off"}))
    monkeypatch.setattr(passions, "run_passions_step", answer("passions", {
        "journal": {"skipped": "quiet_day"}, "interests": {"skipped": "already_this_week"}}))
    monkeypatch.setattr(disclosure_harvest, "run_nightly_disclosure_harvest",
                        answer("disclosure_harvest", {"faded": 0, "planted": None}))
    monkeypatch.setattr(tonight, "run_nightly_pick", answer("tonight", {"day": "2026-08-10", "picked": None}))
    return nc


def _record(nc, night: str) -> dict:
    return json.loads((nc.NIGHT_OUTCOMES_DIR / f"{night}.json").read_text(encoding="utf-8"))


def test_the_night_saves_what_every_step_returned(monkeypatch):
    nc = _stub_the_night(monkeypatch,
                         stake_review={"error": "the call timed out"},
                         narrative={"week": THIS_WEEK, "docs": {"MY_STORY.md": {"committed": "abc123"}},
                                    "fears": {"updates_applied": 0, "note": "all_still"}})
    result = asyncio.run(nc.NightCycleEngine()._run_once("2026-08-09"))

    record = _record(nc, "2026-08-09")
    assert record["night"] == "2026-08-09"
    assert record["steps"]["reflection"] == {"wrote": "2026-08-09"}
    for step in ("consolidation", "narrative", "callbacks_harvest", "narrative_targeted",
                 "stake_review", "views", "memory_sweep", "preferences_rebuild",
                 "thread_retirement", "owner_picture", "passions", "disclosure_harvest", "tonight"):
        assert record["steps"][step] == json.loads(json.dumps(result[step], default=str)), step
    assert record["failed"] == {"stake_review": "stake_review: the call timed out"}
    assert record["weekly"] == {"week": THIS_WEEK,
                                "pages": {"MY_STORY.md": {"committed": "abc123"}},
                                "fears": {"updates_applied": 0, "note": "all_still"}}


def test_a_ritual_that_stopped_before_the_relook_says_so(monkeypatch):
    nc = _stub_the_night(monkeypatch, narrative={"error": "authorship_empty", "week": THIS_WEEK})
    asyncio.run(nc.NightCycleEngine()._run_once("2026-08-09"))
    assert _record(nc, "2026-08-09")["weekly"] == {
        "week": THIS_WEEK, "pages": {}, "fears": {"not_reached": "authorship_empty"}}


def test_a_ritual_that_broke_names_the_week_it_was_running_for(monkeypatch):
    import ai.narrative_self as ns

    nc = _stub_the_night(monkeypatch, narrative=RuntimeError("the pen broke"))
    monkeypatch.setattr(nc.time, "time", lambda: _at(2026, 8, 10, 4, 1))
    asyncio.run(nc.NightCycleEngine()._run_once("2026-08-09"))
    weekly = _record(nc, "2026-08-09")["weekly"]
    assert weekly["week"] == ns.week_key_for(_at(2026, 8, 10, 4, 1)) == THIS_WEEK
    assert weekly["fears"] == {"not_reached": "the pen broke"}


def test_a_night_without_the_ritual_has_no_weekly_section(monkeypatch):
    nc = _stub_the_night(monkeypatch)
    asyncio.run(nc.NightCycleEngine()._run_once("2026-08-11"))
    assert "weekly" not in _record(nc, "2026-08-11")


def test_a_rerun_of_the_same_night_keeps_the_weeks_record(monkeypatch):
    """A failed reflection leaves the night unclaimed and the boot re-runs
    it; the ritual, already done for the week, answers already_this_week.
    Its record of the week must survive the rerun — it is the only one."""
    weekly = {"week": THIS_WEEK, "docs": {"MY_STORY.md": {"committed": "no_change"}},
              "fears": {"updates_applied": 0, "note": "empty_book"}}
    nc = _stub_the_night(monkeypatch, reflection={"error": "empty_reflection"}, narrative=weekly)
    asyncio.run(nc.NightCycleEngine()._run_once("2026-08-09"))
    _stub_the_night(monkeypatch)
    asyncio.run(nc.NightCycleEngine()._run_once("2026-08-09"))
    record = _record(nc, "2026-08-09")
    assert record["steps"]["reflection"] == {"wrote": "2026-08-09"}      # the rerun's night
    assert record["weekly"]["pages"] == {"MY_STORY.md": {"committed": "no_change"}}


def test_the_record_is_written_off_the_event_loop(monkeypatch):
    nc = _stub_the_night(monkeypatch)
    seen = []
    real = nc._save_night_record

    def spy(night, record):
        seen.append(threading.current_thread())
        return real(night, record)

    monkeypatch.setattr(nc, "_save_night_record", spy)
    asyncio.run(nc.NightCycleEngine()._run_once("2026-08-11"))
    assert seen and seen[0] is not threading.main_thread()


def test_a_record_that_cannot_be_written_never_fails_the_night(monkeypatch, caplog):
    nc = _stub_the_night(monkeypatch)

    def refuse(night, record):
        raise OSError("the disk is full")

    monkeypatch.setattr(nc, "_save_night_record", refuse)
    result = asyncio.run(nc.NightCycleEngine()._run_once("2026-08-11"))
    assert result["wrote"] == "2026-08-09"
    assert "the disk is full" in caplog.text


# ── the night's housekeeping (Batch 8, task 3, ruling T3-3) ─────────────
# One entry point (core/retention.py run_nightly_housekeeping), one import
# in the night; each housekeeping step is its own key in the night's record,
# so a failure is named in `failed` and read by rest_of_night. The prune step
# is pinned to a no-op for every test (tests/conftest.py); these patch it.

def _order_spy(monkeypatch, nc, prune_answer):
    import ai.memory_sweep as memory_sweep
    import core.memory_service as memory_service
    import core.retention as retention

    order = []

    async def sweep(*_a, **_kw):
        order.append("memory_sweep")
        return {"moved": 0, "stamped": 0}

    async def prune(*, now, settings):
        order.append("history_prune")
        if isinstance(prune_answer, Exception):
            raise prune_answer
        return prune_answer

    async def rebuild(*_a, **_kw):
        order.append("preferences_rebuild")
        return {"status": "ok"}

    monkeypatch.setattr(memory_sweep, "sweep_memory", sweep)
    monkeypatch.setattr(retention, "_prune_identity_history", prune)
    monkeypatch.setattr(memory_service, "rebuild_user_memory_from_documents", rebuild)
    return order


def test_the_history_prune_runs_after_the_memory_sweep_and_is_recorded(monkeypatch):
    nc = _stub_the_night(monkeypatch)
    order = _order_spy(monkeypatch, nc, {"kept": 380, "removed": 1321, "bytes": 44_600_000})
    result = asyncio.run(nc.NightCycleEngine()._run_once("2026-08-11"))
    assert order == ["memory_sweep", "history_prune", "preferences_rebuild"]
    assert result["history_prune"] == {"kept": 380, "removed": 1321, "bytes": 44_600_000}
    record = _record(nc, "2026-08-11")
    assert record["steps"]["history_prune"] == {"kept": 380, "removed": 1321, "bytes": 44_600_000}
    assert record["failed"] == {}


def test_a_failed_prune_is_named_and_never_costs_the_night(monkeypatch):
    nc = _stub_the_night(monkeypatch)
    _order_spy(monkeypatch, nc, {"error": "database is locked"})
    result = asyncio.run(nc.NightCycleEngine()._run_once("2026-08-11"))
    assert result["tonight"] == {"day": "2026-08-10", "picked": None}      # the later steps ran
    record = _record(nc, "2026-08-11")
    assert record["failed"] == {"history_prune": "history_prune: database is locked"}
    assert "history_prune: database is locked" in nc._surface_errors(result)


def test_a_housekeeping_that_raises_never_costs_the_night(monkeypatch):
    import core.retention as retention

    nc = _stub_the_night(monkeypatch)

    async def broken(**_kw):
        raise RuntimeError("the housekeeping broke")

    monkeypatch.setattr(retention, "run_nightly_housekeeping", broken)
    result = asyncio.run(nc.NightCycleEngine()._run_once("2026-08-11"))
    assert result["housekeeping"] == {"error": "the housekeeping broke"}
    assert result["tonight"] == {"day": "2026-08-10", "picked": None}
    assert _record(nc, "2026-08-11")["failed"] == {"housekeeping": "housekeeping: the housekeeping broke"}
    assert "housekeeping: the housekeeping broke" in nc._surface_errors(result)


def test_the_log_sweep_runs_right_after_the_prune_and_is_recorded(monkeypatch):
    """Batch 8, task 5: the log sweep is the housekeeping's second step —
    its own key in the night's record; a quiet night is counts, no failure."""
    import core.retention as retention

    nc = _stub_the_night(monkeypatch)
    order = _order_spy(monkeypatch, nc, {"kept": 1, "removed": 0, "bytes": 0})
    counts = {"deleted": 2, "gzipped": 1, "trimmed": 0, "refused": 0, "held_open": 1, "errors": 0,
              "freed_bytes": 1024}

    async def logs(*, now, settings):
        order.append("retention")
        return dict(counts)

    monkeypatch.setattr(retention, "_sweep_logs", logs)
    result = asyncio.run(nc.NightCycleEngine()._run_once("2026-08-11"))
    assert order == ["memory_sweep", "history_prune", "retention", "preferences_rebuild"]
    assert result["retention"] == counts
    record = _record(nc, "2026-08-11")
    assert record["steps"]["retention"] == counts
    assert record["failed"] == {}
    assert nc._surface_errors(result) == ""


def test_a_failed_log_sweep_is_named_and_never_costs_the_night(monkeypatch):
    import core.retention as retention

    nc = _stub_the_night(monkeypatch)
    _order_spy(monkeypatch, nc, {"kept": 1, "removed": 0, "bytes": 0})

    async def logs(*, now, settings):
        return {"error": "logs is read-only"}

    monkeypatch.setattr(retention, "_sweep_logs", logs)
    result = asyncio.run(nc.NightCycleEngine()._run_once("2026-08-11"))
    assert result["tonight"] == {"day": "2026-08-10", "picked": None}      # the later steps ran
    assert _record(nc, "2026-08-11")["failed"] == {"retention": "retention: logs is read-only"}
    assert "retention: logs is read-only" in nc._surface_errors(result)


def test_a_prune_failure_is_read_by_the_rest_of_the_nights_line(home):
    _night(home.data, DUE_NIGHT, steps={"history_prune": {"error": "database is locked"}},
           failed={"history_prune": "history_prune: database is locked"})
    entry = _check(home, "rest_of_night")["rest_of_night"]
    assert entry.status == "stale"
    assert "history_prune: database is locked" in entry.detail


# ── the night's own check, off the loop, with the install's flags (R49, R55) ──

def test_the_nightly_check_runs_off_the_event_loop_with_the_installs_flags(monkeypatch):
    import ai.night_cycle as nc
    from ai.narrative_self import identity_palli_root
    from core.database import resolve_database_path
    from core.settings_boot import settings_service

    seen = {}

    def spy(**kwargs):
        seen.update(kwargs, thread=threading.current_thread())
        return LivenessReport(checked_at=NOW)

    snapshot = SimpleNamespace(companion=CompanionConfig(night_cycle=True, tonight=False),
                               features=FeaturesConfig())
    monkeypatch.setattr(ol, "check_liveness", spy)
    monkeypatch.setattr(settings_service, "snapshot", lambda: snapshot)

    asyncio.run(nc.NightCycleEngine()._report_liveness())

    assert seen["thread"] is not threading.main_thread()
    assert seen["flags"]["night_cycle"] is True and seen["flags"]["tonight"] is False
    assert seen["db_path"] == resolve_database_path()
    assert seen["identity_root"] == identity_palli_root()
    # The due night turns over at the night's RUN time (06:30 -> 6.5), not
    # the 04:00 day boundary (the owner, 10-05).
    assert seen["boundary_hour"] == 6.5


def test_a_check_that_breaks_never_kills_the_night_task(monkeypatch, caplog):
    import ai.night_cycle as nc

    def boom(**kwargs):
        raise RuntimeError("the checker broke")

    monkeypatch.setattr(ol, "check_liveness", boom)
    asyncio.run(nc.NightCycleEngine()._report_liveness())     # must not raise
    assert "the checker broke" in caplog.text


# ── an install as a separate process reads it (the doctor, the CLI) ──────

def test_an_install_where_no_night_has_run_has_nothing_due(tmp_path):
    (tmp_path / "data").mkdir()
    assert ol.install_report(tmp_path, now=NOW) is None


def test_an_install_anchors_every_path_at_its_repo_root(tmp_path, monkeypatch):
    """The doctor never changes directory: data/, the database and her repo
    are found under the repo root, wherever the process was started."""
    from core.config import config

    root = tmp_path / "install"
    data = root / "data"
    _night(data, DUE_NIGHT, steps={"stake_review": {"applied": 0}})
    _stakes_table(root / "data" / "palli-local.sqlite3", [("hope", _at(2026, 8, 12, 9, 0))])
    _write_json(data / "narrative_self" / ".state.json", {"last_week": THIS_WEEK})
    _commit(root / "identity" / "palli", "MY_STORY.md", datetime(2026, 8, 10, 4, 5))
    monkeypatch.setattr(config, "DATABASE_PATH", "data/palli-local.sqlite3")
    monkeypatch.setattr(config, "IDENTITY_ROOT_PATH", "identity")
    monkeypatch.chdir(tmp_path)

    report = ol.install_report(root, now=NOW)

    lines = {e.name: e for e in report.entries}
    assert lines["stakes"].status == "wrote"
    assert lines["narrative_self"].status == "wrote"
