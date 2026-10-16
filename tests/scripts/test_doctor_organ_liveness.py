"""`palli_ctl doctor` prints her organs' table (Batch 6, item 3.8; ruling R56).

One line — a note when every organ that is on wrote or had nothing to do, a
warning when one should have written and did not — with one row per organ
under it (name, status, detail). Never a blocker: the exit code is the
core blockers' alone. The doctor is a separate process that never changes
directory, so the install is read from its repo root: data/, the database
and her repo where the install's settings name them, the flags from
data/palli.yaml. Every test here points it at a tmp install; nothing of a
real one is read.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sqlite3
import subprocess
from datetime import datetime
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 8, 12, 10, 0)          # the pass of the 12th looked back on the 11th
# Every organ switch the liveness flags read, all off; a test turns on its own.
ORGAN_SWITCHES = ("night_cycle", "consolidation", "present_moment", "stake_ledger", "opinions",
                  "vulnerability", "tonight", "surprise", "narrative_self", "passions",
                  "idiolect", "fears", "wanting", "own_work", "arbiter", "regret")


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    """The doctor reads ~/.palli and probes her NAS folder; never here."""
    monkeypatch.setenv("HOME", str(tmp_path / "isolated-home"))
    monkeypatch.setenv("WORKBENCH_NAS_DIR", "")


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "palli_ctl_liveness_test_module", REPO / "scripts" / "palli_ctl.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def install(tmp_path, monkeypatch) -> Path:
    """A tmp install root whose settings name the database and her identity
    folder relatively, the way a fresh .env does — anchored at this root."""
    from core.config import config

    root = tmp_path / "install"
    (root / "data").mkdir(parents=True)
    monkeypatch.setattr(config, "DATABASE_PATH", "data/palli-local.sqlite3")
    monkeypatch.setattr(config, "IDENTITY_ROOT_PATH", "identity")
    return root


def _settings(root: Path, **on: bool) -> None:
    """data/palli.yaml: the shipped example with every organ switch off but ``on``."""
    raw = yaml.safe_load((REPO / "data" / "palli.example.yaml").read_text(encoding="utf-8"))
    raw["companion"].update({name: False for name in ORGAN_SWITCHES})
    raw["companion"].update(on)
    raw.setdefault("features", {}).update(forged_tools=False, her_hands=False)
    (root / "data" / "palli.yaml").write_text(yaml.safe_dump(raw), encoding="utf-8")


def _file(path: Path, text: str = '{"ok": true}\n') -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _night(root: Path, night: str, steps: dict) -> None:
    _file(root / "data" / "night_cycle" / ".outcomes" / f"{night}.json",
          json.dumps({"night": night, "at": 0.0, "steps": steps, "failed": {}}))


def _stakes_db(root: Path) -> None:
    conn = sqlite3.connect(root / "data" / "palli-local.sqlite3")
    conn.execute("CREATE TABLE stakes (id INTEGER PRIMARY KEY, kind TEXT NOT NULL, "
                 "stake TEXT NOT NULL, created_at REAL NOT NULL, updated_at REAL NOT NULL)")
    conn.execute("INSERT INTO stakes (kind, stake, created_at, updated_at) VALUES "
                 "('hope', 'a hope of hers', ?, ?)", (NOW.timestamp(), NOW.timestamp()))
    conn.commit()
    conn.close()


def _rows(check: dict) -> dict:
    """{organ: status} from the check's table rows."""
    return {row.split()[0]: row.split()[1] for row in check["details"]["reasons"]}


def _check(root: Path, now: datetime = NOW) -> dict:
    return _load_module().LauncherController()._organ_liveness_check(root_dir=root, now=now)


def test_an_install_where_no_night_has_run_is_a_note_that_says_so(install):
    _settings(install, night_cycle=True)
    check = _check(install)
    assert check["code"] == "organ_liveness_no_night_yet"
    assert check["severity"] == "info"
    assert "No night has run on this install yet" in check["message"]


def test_every_organ_that_wrote_or_had_nothing_to_do_is_a_note_with_the_table(install):
    _settings(install, night_cycle=True, stake_ledger=True)
    _file(install / "data" / "night_cycle" / "2026-08-11.jsonl")
    _file(install / "data" / "inner_monologue" / "2026-08-12.jsonl")
    _night(install, "2026-08-11", {"stake_review": {"applied": 0}})
    _stakes_db(install)

    check = _check(install)

    assert check["code"] == "organ_liveness_ok"
    assert check["severity"] == "info"
    # One row per organ that is on: the night cycle, the rest of the
    # night's steps and the stakes by their switches, the monologue and the
    # corrections always.
    assert _rows(check) == {"night_cycle": "wrote", "inner_monologue": "wrote",
                            "stakes": "wrote", "rest_of_night": "quiet", "corrections": "quiet"}
    first = check["details"]["reasons"][0]
    assert first.startswith("night_cycle      wrote  newest 2026-08-11")


def test_an_organ_switched_off_in_the_settings_file_has_no_row(install):
    _settings(install, night_cycle=True, tonight=False)
    _file(install / "data" / "night_cycle" / "2026-08-11.jsonl")
    # The night's record, without the pick: its step did not run (R73 —
    # with no record at all, the record has not begun and nothing is blamed).
    _night(install, "2026-08-11", {"reflection": {"wrote": "2026-08-11"}})
    assert "tonight" not in _rows(_check(install))
    _settings(install, night_cycle=True, tonight=True)
    assert _rows(_check(install))["tonight"] == "stale"


def _stale_install(root: Path) -> None:
    """A night ran once, long ago, and left its record: the night cycle and
    her stakes are both overdue whatever the clock says. (Without the
    record, the record has not begun, and nothing it judges is blamed —
    R73.)"""
    _settings(root, night_cycle=True, stake_ledger=True)
    _file(root / "data" / "night_cycle" / "2026-01-01.jsonl")
    _night(root, "2026-01-01", {"reflection": {"wrote": "2026-01-01"}})
    _file(root / "data" / "inner_monologue" / "2026-01-01.jsonl")


def _doctor(module, controller, monkeypatch, root: Path) -> None:
    report = {"summary": "Environment is ready.", "status": "ok",
              "counts": {"core_blockers": 0, "warnings": 0, "ok": 1},
              "checks": [{"code": "config_valid", "severity": "ok",
                          "message": "Config validation passed.", "details": {}}]}
    monkeypatch.setattr(module, "build_doctor_report", lambda **_kwargs: report)
    monkeypatch.setattr(controller, "_save_last_doctor", lambda report: None)
    controller.root_dir = root


def test_a_stale_organ_is_a_warning_and_never_changes_the_exit_code(install, monkeypatch, capsys):
    _stale_install(install)
    module = _load_module()
    controller = module.LauncherController()
    _doctor(module, controller, monkeypatch, install)

    assert controller.doctor(as_json=False) == 0

    out = capsys.readouterr().out
    line = next(ln for ln in out.splitlines() if "organ_liveness" in ln)
    assert line.startswith("[WARNING] organ_liveness_stale:")
    assert "night_cycle" in line and "stakes" in line
    assert "    - night_cycle      stale  newest 2026-01-01" in out
    assert "    - corrections      quiet" in out
    assert "Ready with 1 warning(s)." in out


def test_the_json_form_carries_the_table(install, monkeypatch, capsys):
    _stale_install(install)
    module = _load_module()
    controller = module.LauncherController()
    _doctor(module, controller, monkeypatch, install)

    assert controller.doctor(as_json=True) == 0

    payload = json.loads(capsys.readouterr().out)
    check = next(c for c in payload["checks"] if c["code"] == "organ_liveness_stale")
    assert check["severity"] == "warning"
    assert _rows(check) == {"night_cycle": "stale", "inner_monologue": "stale",
                            "stakes": "stale", "rest_of_night": "stale", "corrections": "quiet"}
    assert payload["counts"]["warnings"] == 1 and payload["counts"]["core_blockers"] == 0


def test_the_doctor_only_reads_the_install(install):
    _settings(install, night_cycle=True, stake_ledger=True, narrative_self=True)
    _file(install / "data" / "night_cycle" / "2026-08-11.jsonl")
    _file(install / "data" / "narrative_self" / ".state.json", json.dumps({"last_week": "2026-W33"}))
    _night(install, "2026-08-11", {"stake_review": {"applied": 0}})
    _stakes_db(install)
    repo = install / "identity" / "palli"
    repo.mkdir(parents=True)
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(GIT_AUTHOR_NAME="Tester", GIT_AUTHOR_EMAIL="tester@example.invalid",
               GIT_COMMITTER_NAME="Tester", GIT_COMMITTER_EMAIL="tester@example.invalid",
               GIT_AUTHOR_DATE="2026-08-10T04:05:00", GIT_COMMITTER_DATE="2026-08-10T04:05:00")
    (repo / "MY_STORY.md").write_text("her page\n", encoding="utf-8")
    for args in (("init", "-q"), ("add", "MY_STORY.md"), ("commit", "-q", "-m", "revise")):
        subprocess.run(["git", "-c", "commit.gpgsign=false", *args], cwd=repo, env=env,
                       check=True, capture_output=True)

    def snapshot():
        return {p: p.read_bytes() for p in sorted(install.rglob("*")) if p.is_file()}

    before = snapshot()
    check = _check(install)
    assert _rows(check)["narrative_self"] == "wrote"       # her repo was read
    assert snapshot() == before


def _her_repo(root: Path, page: str, when: str) -> None:
    """Her identity repo under the install, one page committed at ``when``."""
    repo = root / "identity" / "palli"
    repo.mkdir(parents=True, exist_ok=True)
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(GIT_AUTHOR_NAME="Tester", GIT_AUTHOR_EMAIL="tester@example.invalid",
               GIT_COMMITTER_NAME="Tester", GIT_COMMITTER_EMAIL="tester@example.invalid",
               GIT_AUTHOR_DATE=when, GIT_COMMITTER_DATE=when)
    (repo / page).write_text("her page\n", encoding="utf-8")
    for args in (("init", "-q"), ("add", page), ("commit", "-q", "-m", "revise")):
        subprocess.run(["git", "-c", "commit.gpgsign=false", *args], cwd=repo, env=env,
                       check=True, capture_output=True)


def _record_began_last_night(root: Path) -> None:
    """The install the day after the merge's restart: the night's record
    began with last night's pass (the 11th). This week's ritual (W33) ran
    on Monday's pass, under the code before, and left no record; it
    claimed the week and found every page unchanged."""
    _settings(root, night_cycle=True, stake_ledger=True, opinions=True, vulnerability=True,
              tonight=True, narrative_self=True, fears=True, idiolect=True)
    _file(root / "data" / "night_cycle" / "2026-08-11.jsonl")
    _file(root / "data" / "inner_monologue" / "2026-08-12.jsonl")
    _night(root, "2026-08-11", {
        "reflection": {"wrote": "2026-08-11"},
        "narrative": {"skipped": "already_this_week"},
        "callbacks_harvest": {"week": "2026-W33", "planted": 0},
        "stake_review": {"applied": 0},
        "views": {"day": "2026-08-11", "formed": 0},
        "disclosure_harvest": {"faded": 0, "planted": None},
        "tonight": {"day": "2026-08-12", "picked": None},
    })
    _stakes_db(root)
    _file(root / "data" / "views" / ".state.json",
          json.dumps({"last_night": "2026-08-11", "formed": 0, "at": 0.0}))
    _file(root / "data" / "tonight" / "2026-08-12.json", json.dumps({"day": "2026-08-12", "pick": None}))
    _file(root / "data" / "callbacks" / ".state.json",
          json.dumps({"last_week": "2026-W33", "planted": 0, "at": 0.0}))
    _file(root / "data" / "narrative_self" / ".state.json", json.dumps({"last_week": "2026-W33"}))
    _her_repo(root, "MY_STORY.md", "2026-08-03T04:05:00")          # last week's commit


def test_an_install_whose_record_began_last_night_shows_no_false_warning(install):
    """Fix round 1 (R72): the nights before the record began have no
    evidence either way; the ritual that ran before it is not blamed."""
    _record_began_last_night(install)

    check = _check(install)

    assert check["code"] == "organ_liveness_ok", check["details"]["reasons"]
    assert check["severity"] == "info"
    rows = _rows(check)
    assert rows["fears"] == "quiet" and rows["narrative_self"] == "quiet"
    began = [r for r in check["details"]["reasons"] if "the night's record began 2026-08-11" in r]
    assert [r.split()[0] for r in began] == ["narrative_self", "fears"]


def _nights_ran_before_the_record(root: Path) -> None:
    """The install between the merge's restart and its first night: the
    code before ran every night (its reflections are there), and no night
    has run under this code yet, so there is no night_cycle/.outcomes/.
    Nothing new was written: no stake updated, no view, no truth, no pick,
    no bit, no fear; this week's story pages were claimed and none changed."""
    _settings(root, night_cycle=True, stake_ledger=True, opinions=True, vulnerability=True,
              tonight=True, narrative_self=True, fears=True, idiolect=True)
    _file(root / "data" / "night_cycle" / "2026-08-11.jsonl")
    _file(root / "data" / "inner_monologue" / "2026-08-12.jsonl")
    _file(root / "data" / "narrative_self" / ".state.json", json.dumps({"last_week": "2026-W33"}))
    conn = sqlite3.connect(root / "data" / "palli-local.sqlite3")
    conn.execute("CREATE TABLE stakes (id INTEGER PRIMARY KEY, kind TEXT NOT NULL, "
                 "stake TEXT NOT NULL, created_at REAL NOT NULL, updated_at REAL NOT NULL)")
    conn.commit()
    conn.close()
    _her_repo(root, "MY_STORY.md", "2026-08-03T04:05:00")          # last week's commit


def test_an_install_whose_record_has_not_begun_shows_no_false_warning(install):
    """Fix wave (R73): nights have run, but under the code before, so there
    is no record yet. Every organ the record would judge is quiet and says
    the record begins with the next night; the night cycle's own line still
    judges the pass (it wrote last night)."""
    _nights_ran_before_the_record(install)

    check = _check(install)

    assert check["code"] == "organ_liveness_ok", check["details"]["reasons"]
    assert check["severity"] == "info"
    rows = _rows(check)
    assert rows["night_cycle"] == "wrote"
    begins = [r.split()[0] for r in check["details"]["reasons"]
              if "the night's record begins with the next night" in r]
    assert begins == ["stakes", "views", "disclosures", "tonight", "rest_of_night",
                      "narrative_self", "callbacks", "fears"]
    assert all(rows[name] == "quiet" for name in begins)
    assert not (install / "data" / "night_cycle" / ".outcomes").exists()


def test_a_night_cycle_switched_off_says_so(install):
    """Fix round 1 (R72): not "no night has run" forever — it is off."""
    _settings(install, night_cycle=False, present_moment=True)
    check = _check(install)
    assert check["code"] == "organ_liveness_night_cycle_off"
    assert check["severity"] == "info"
    assert check["message"] == ("The night cycle is switched off, so no night runs "
                                "and nothing nightly is due.")


def test_a_checker_that_cannot_be_imported_is_a_line_not_a_crash(install, monkeypatch):
    """Fix round 1 (R72): the import sits inside the check's guard."""
    import sys

    import ai

    controller = _load_module().LauncherController()
    monkeypatch.setitem(sys.modules, "ai.organ_liveness", None)
    monkeypatch.delattr(ai, "organ_liveness", raising=False)

    check = controller._organ_liveness_check(root_dir=install, now=NOW)

    assert check["code"] == "organ_liveness_unreadable"
    assert check["severity"] == "warning"
    assert "organ_liveness" in check["message"]


def test_a_check_that_cannot_run_is_a_line_not_a_crash(install, monkeypatch):
    import ai.organ_liveness as organ_liveness

    def broken(*args, **kwargs):
        raise RuntimeError("the checker tripped")

    monkeypatch.setattr(organ_liveness, "install_report", broken)
    check = _check(install)
    assert check["code"] == "organ_liveness_unreadable"
    assert check["severity"] == "warning"
    assert "the checker tripped" in check["message"]
