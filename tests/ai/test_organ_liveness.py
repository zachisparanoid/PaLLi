"""The smoke detector (2026-08-09).

Every organ fails silent by design — a dead night cycle just stops writing,
and nothing notices until a human greps. The liveness check turns yesterday's
absence into one loud line. It runs two ways: a nightly in-engine self-report
after the night pass (06:30 since 2026-10-05; 04:00 before), and
scripts/organ_liveness.py for cron/manual use.

Philosophy: "wrote, OR had a lawful reason." Occasioned organs (daydreams,
workbench, arbiter sends) can be legitimately silent, so they are reported
but never stale. Dailies must produce their file; weeklies must have run
this ISO week or the one before — and, since the owner's answer of
2026-09-29 ("marker isn't proof"), must have COMMITTED in her repo in the
week they claim. tests/ai/test_organ_liveness_outcomes.py covers every
organ watched since Batch 6 and the quiet/stale line.
"""

import json
import os
import subprocess
from datetime import datetime
from pathlib import Path

import pytest

from ai.organ_liveness import check_liveness

# Frozen clocks per the fixture law. 2026-08-09 was a Sunday (ISO 2026-W32);
# Monday 2026-08-10 starts W33.
SUNDAY_EVENING = datetime(2026, 8, 9, 23, 0, 0)
MONDAY_BEFORE_PASS = datetime(2026, 8, 10, 2, 0, 0)
# The pass runs at 06:30 since 2026-10-05 (04:00 before; the owner, 10-05:
# off DeepSeek's peak), so "after the pass" is 07:00, and 05:00 is now
# between the 04:00 day boundary and the run.
MONDAY_BETWEEN_BOUNDARY_AND_RUN = datetime(2026, 8, 10, 5, 0, 0)
MONDAY_AFTER_PASS = datetime(2026, 8, 10, 7, 0, 0)
# The W32 ritual ran on the pass of Monday 2026-08-03 (looking back on the 2nd).
W32_RITUAL = datetime(2026, 8, 3, 4, 5, 0)


def _commit(repo: Path, page: str, when: datetime) -> None:
    """One page committed to her repo at ``when`` — a real, dated commit."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(GIT_AUTHOR_NAME="Tester", GIT_AUTHOR_EMAIL="tester@example.invalid",
               GIT_COMMITTER_NAME="Tester", GIT_COMMITTER_EMAIL="tester@example.invalid",
               GIT_AUTHOR_DATE=when.isoformat(), GIT_COMMITTER_DATE=when.isoformat())

    def git(*args):
        subprocess.run(["git", "-c", "commit.gpgsign=false", *args], cwd=repo, env=env,
                       check=True, capture_output=True)

    if not (repo / ".git").exists():
        repo.mkdir(parents=True, exist_ok=True)
        git("init", "-q")
    (repo / page).write_text(f"{page} as of {when.isoformat()}\n", encoding="utf-8")
    git("add", page)
    git("commit", "-q", "-m", f"revise {page}")


@pytest.fixture
def data_root(tmp_path: Path) -> Path:
    """A healthy organ layout as of Sunday evening 2026-08-09, with her repo
    beside the data folder (tmp_path/her_repo)."""
    root = tmp_path / "data"
    root.mkdir()
    for d, day in (
        ("night_cycle", "2026-08-08"),
        ("day_gists", "2026-08-08"),
        ("present_moment", "2026-08-09"),
        ("inner_monologue", "2026-08-09"),
    ):
        (root / d).mkdir()
        (root / d / f"{day}.jsonl").write_text('{"ok": true}\n')
    for d in ("narrative_self", "passions"):
        (root / d).mkdir()
        (root / d / ".state.json").write_text(json.dumps({"last_week": "2026-W32"}))
    # The W32 ritual committed her pages; the passions pass its interests.
    _commit(tmp_path / "her_repo", "MY_STORY.md", W32_RITUAL)
    _commit(tmp_path / "her_repo", "INTERESTS.md", W32_RITUAL)
    # The night cycle's record of each night (Batch 6, R51): the due night
    # (the 8th, looked back on at 04:00 on the 9th), every step with nothing
    # to do; and the W32 ritual's night, with its pages and the re-look.
    outcomes = root / "night_cycle" / ".outcomes"
    outcomes.mkdir()
    (outcomes / "2026-08-08.json").write_text(json.dumps({
        "night": "2026-08-08", "at": 0.0, "failed": {},
        "steps": {
            "reflection": {"wrote": "2026-08-08"},
            "consolidation": {"day_gist": {"day": "2026-08-08", "wrote": True}},
            "narrative": {"skipped": "already_this_week"},
            "callbacks_harvest": {"skipped": "already_ran", "week": "2026-W32"},
            "stake_review": {"applied": 0},
            "views": {"day": "2026-08-08", "formed": 0},
            "passions": {"journal": {"skipped": "quiet_day"},
                         "interests": {"skipped": "already_this_week"}},
            "disclosure_harvest": {"faded": 0, "planted": None},
            "tonight": {"day": "2026-08-09", "picked": None},
        },
    }))
    (outcomes / "2026-08-02.json").write_text(json.dumps({
        "night": "2026-08-02", "at": 0.0, "failed": {}, "steps": {},
        "weekly": {"week": "2026-W32", "pages": {"MY_STORY.md": {"committed": "abc1234"}},
                   "fears": {"updates_applied": 0, "note": "all_still"}},
    }))
    # The organs' own records of a pass that ran and had nothing to do.
    (root / "tonight").mkdir()
    (root / "tonight" / "2026-08-09.json").write_text(json.dumps({"day": "2026-08-09", "pick": None}))
    (root / "views").mkdir()
    (root / "views" / ".state.json").write_text(
        json.dumps({"last_night": "2026-08-08", "formed": 0, "at": 0.0}))
    (root / "callbacks").mkdir()
    (root / "callbacks" / ".state.json").write_text(
        json.dumps({"last_week": "2026-W32", "planted": 0, "at": 0.0}))
    return root


def liveness(data_root: Path, now: datetime, **kwargs):
    """check_liveness on the fixture's install: her repo beside the data."""
    kwargs.setdefault("identity_root", data_root.parent / "her_repo")
    return check_liveness(now=now, data_root=data_root, **kwargs)


def by_name(report):
    return {e.name: e for e in report.entries}


def test_all_healthy(data_root):
    report = liveness(data_root, SUNDAY_EVENING)
    assert report.healthy
    assert report.stale_names == []
    statuses = {e.name: e.status for e in report.entries}
    for organ in ("night_cycle", "day_gists", "present_moment", "inner_monologue"):
        assert statuses[organ] == "wrote", organ
    # Re-pinned 2026-09-29 (the owner's answer "marker isn't proof"): the two
    # weekly organs wrote because their pages were COMMITTED in the week
    # their marker claims — the marker alone no longer proves it.
    entries = by_name(report)
    for organ in ("narrative_self", "passions"):
        assert entries[organ].status == "wrote", organ
        assert "committed" in entries[organ].detail and "2026-W32" in entries[organ].detail


def test_missing_night_file_is_stale(data_root):
    (data_root / "night_cycle" / "2026-08-08.jsonl").unlink()
    report = liveness(data_root, SUNDAY_EVENING)
    assert not report.healthy
    assert report.stale_names == ["night_cycle"]


def test_before_boundary_hour_yesterday_is_not_due_yet(data_root):
    """At Monday 02:00 the night pass hasn't run, so Sunday's night file
    cannot exist yet — the freshest lawful file is still Saturday's (08-08).
    Same boundary shift the night cycle itself uses."""
    report = liveness(data_root, MONDAY_BEFORE_PASS)
    assert report.healthy, report.stale_names


def test_between_the_day_boundary_and_the_run_yesterday_is_not_due_yet(data_root):
    """At Monday 05:00 — past the 04:00 day boundary, before the 06:30 run
    — the pass has not run either: the doctor must not call Sunday's night
    stale in the hours before it runs (the owner, 10-05)."""
    report = liveness(data_root, MONDAY_BETWEEN_BOUNDARY_AND_RUN)
    assert report.healthy, report.stale_names


def test_the_default_boundary_is_the_night_run_time():
    from ai.night_cycle import NIGHT_RUN_AT_DEFAULT
    from ai.organ_liveness import DEFAULT_BOUNDARY_HOUR

    hour, minute = NIGHT_RUN_AT_DEFAULT.split(":")
    assert DEFAULT_BOUNDARY_HOUR == int(hour) + int(minute) / 60


def test_the_doctor_reads_the_installs_run_time(tmp_path):
    """install_report's due night follows the run time in the install's
    own data/palli.yaml; no file, or one that cannot be read, is 06:30."""
    import yaml

    from ai.organ_liveness import DEFAULT_BOUNDARY_HOUR, run_hours_from_yaml

    repo = Path(__file__).resolve().parents[2]
    raw = yaml.safe_load((repo / "data" / "palli.example.yaml").read_text(encoding="utf-8"))
    raw["companion"]["night_cycle_run_at"] = "08:15"
    path = tmp_path / "palli.yaml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    assert run_hours_from_yaml(path) == 8.25
    assert run_hours_from_yaml(tmp_path / "absent.yaml") == DEFAULT_BOUNDARY_HOUR
    (tmp_path / "broken.yaml").write_text("companion: [not, a, map\n", encoding="utf-8")
    assert run_hours_from_yaml(tmp_path / "broken.yaml") == DEFAULT_BOUNDARY_HOUR


def test_after_boundary_hour_yesterday_is_due(data_root):
    """At Monday 07:00 the pass has had its window: Sunday's file (08-09)
    is now the expectation, and its absence is stale."""
    report = liveness(data_root, MONDAY_AFTER_PASS)
    assert "night_cycle" in report.stale_names
    assert "day_gists" in report.stale_names


def test_dailies_accept_the_newer_file_too(data_root):
    """A moment file dated today obviously counts — but so does one dated
    yesterday at Monday 02:00 (she may not have spoken yet today)."""
    (data_root / "present_moment" / "2026-08-09.jsonl").rename(
        data_root / "present_moment" / "2026-08-10.jsonl"
    )
    report = liveness(data_root, MONDAY_BEFORE_PASS)
    assert report.healthy, report.stale_names


def test_weekly_previous_week_is_healthy_in_new_week(data_root, tmp_path):
    """Monday of W33 with state still at W32: the weekly runs early in the
    new week; last week's stamp is lawful — and, re-pinned 2026-09-29 (the
    owner's answer "marker isn't proof"), only because W32's pages were
    committed in W32. The same W32 marker over a repo whose last story
    commit is from W31 is stale."""
    report = liveness(data_root, MONDAY_AFTER_PASS)
    assert by_name(report)["narrative_self"].status == "wrote"
    assert "2026-W32" in by_name(report)["narrative_self"].detail

    older = tmp_path / "older_repo"
    _commit(older, "MY_STORY.md", datetime(2026, 7, 28, 4, 5))       # W31
    report = liveness(data_root, MONDAY_AFTER_PASS, identity_root=older)
    assert by_name(report)["narrative_self"].status == "stale"


def test_weekly_two_weeks_behind_is_stale(data_root):
    (data_root / "narrative_self" / ".state.json").write_text(
        json.dumps({"last_week": "2026-W30"})
    )
    report = liveness(data_root, SUNDAY_EVENING)
    assert "narrative_self" in report.stale_names


def test_missing_state_file_is_stale(data_root):
    (data_root / "passions" / ".state.json").unlink()
    report = liveness(data_root, SUNDAY_EVENING)
    assert "passions" in report.stale_names


def test_occasioned_organs_are_reported_but_never_stale(data_root):
    """No daydreams, no workbench records, no arbiter sends — all lawful
    silence. They appear in the report as quiet, never in stale_names."""
    report = liveness(data_root, SUNDAY_EVENING)
    entries = by_name(report)
    for organ in ("daydream", "workbench", "arbiter"):
        assert organ in entries
        assert entries[organ].status in ("wrote", "quiet")
    assert report.healthy


def test_excluded_organs_are_skipped(data_root):
    """expected= narrows the required set; an organ deliberately off must
    not false-alarm (the night cycle, the CLI and the doctor pass flags=
    from the install's settings — tests/ai/test_organ_liveness_outcomes.py)."""
    (data_root / "night_cycle" / "2026-08-08.jsonl").unlink()
    report = liveness(
        data_root,
        SUNDAY_EVENING,
        expected={"day_gists", "present_moment", "inner_monologue"},
    )
    assert report.healthy
    assert "night_cycle" not in by_name(report)


def test_summary_line_is_one_line(data_root):
    healthy = liveness(data_root, SUNDAY_EVENING)
    assert "\n" not in healthy.summary()
    assert "OK" in healthy.summary()
    (data_root / "night_cycle" / "2026-08-08.jsonl").unlink()
    stale = liveness(data_root, SUNDAY_EVENING)
    assert "night_cycle" in stale.summary()
    assert "STALE" in stale.summary()


def test_hands_entry_counts_recent_acts(data_root, monkeypatch):
    import ai.organ_liveness as ol

    deeds = [
        {"at": 0, "kind": "hands", "detail": "joined voice channel General"},
        {"at": 0, "kind": "sent", "detail": 'sent a message (her_hands): "yo…"'},
        {"at": 0, "kind": "sent", "detail": 'sent a message (arbiter): "hey…"'},
        {"at": 0, "kind": "quest_filed", "detail": "x"},
    ]
    monkeypatch.setattr(ol, "_read_deeds", lambda root=None: deeds)
    report = liveness(data_root, SUNDAY_EVENING)
    entry = {e.name: e for e in report.entries}["hands"]
    assert entry.status == "wrote"
    assert "2 acts" in entry.detail          # hands deed + her_hands send; arbiter send NOT counted
    assert report.healthy


def test_hands_quiet_is_lawful(data_root, monkeypatch):
    import ai.organ_liveness as ol

    monkeypatch.setattr(ol, "_read_deeds", lambda root=None: [])
    report = liveness(data_root, SUNDAY_EVENING)
    entry = {e.name: e for e in report.entries}["hands"]
    assert entry.status == "quiet"
    assert report.healthy


def test_hands_count_reads_the_callers_data_root(data_root):
    # The parked step-33 item: _read_deeds ignored check_liveness's
    # data_root and read the cwd-relative live ring. Now the caller's root
    # is the one that's read — proven with a REAL ring under data_root,
    # no monkeypatching.
    from ai.recent_deeds import record_deed

    record_deed("hands", "joined voice channel General", root=data_root)
    report = liveness(data_root, SUNDAY_EVENING)
    entry = {e.name: e for e in report.entries}["hands"]
    assert entry.status == "wrote"
    assert "1 acts" in entry.detail
