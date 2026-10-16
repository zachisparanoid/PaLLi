"""scripts/organ_liveness.py — the smoke detector for cron and hands
(Batch 6, item 3.8). It prints one line per organ that is on and exits 2
when one should have written and did not. The report is the install's, read
the doctor's way (ai.organ_liveness.install_report); here it is stood in
for, so nothing of a real install is read."""
from __future__ import annotations

import importlib.util
from datetime import datetime
from pathlib import Path

from ai.organ_liveness import LivenessEntry, LivenessReport

REPO = Path(__file__).resolve().parents[2]


def _load():
    spec = importlib.util.spec_from_file_location(
        "organ_liveness_cli_test_module", REPO / "scripts" / "organ_liveness.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _report(*rows):
    return LivenessReport(checked_at=datetime(2026, 8, 12, 10, 0),
                          entries=[LivenessEntry(*row) for row in rows])


def test_the_marks_are_exactly_the_three_status_words():
    assert set(_load().MARKS) == {"wrote", "quiet", "stale"}


def test_each_organ_gets_a_line_and_a_stale_one_exits_two(monkeypatch, capsys, tmp_path):
    cli = _load()
    asked = {}

    def report(root, *, now, data_root):
        asked.update(root=root, data_root=data_root)
        return _report(("night_cycle", "wrote", "newest 2026-08-11"),
                       ("views", "quiet", "the pass ran for 2026-08-11: no view formed"),
                       ("tonight", "stale", "no record of the night of 2026-08-11"))

    monkeypatch.setattr(cli, "install_report", report)
    assert cli.main(["--data-root", str(tmp_path)]) == 2

    out = capsys.readouterr().out.splitlines()
    assert out[0] == "  night_cycle  ok     newest 2026-08-11"
    assert out[1] == "  views        quiet  the pass ran for 2026-08-11: no view formed"
    assert out[2] == "  tonight      STALE  no record of the night of 2026-08-11"
    assert "STALE [tonight]" in out[-1]
    assert asked == {"root": REPO, "data_root": tmp_path}


def test_nothing_stale_exits_zero(monkeypatch, capsys):
    cli = _load()
    monkeypatch.setattr(cli, "install_report", lambda root, **kw: _report(
        ("night_cycle", "wrote", "newest 2026-08-11"), ("forge", "quiet", "nothing since 2026-08-11")))
    assert cli.main([]) == 0
    assert "organ_liveness: OK" in capsys.readouterr().out


def test_an_install_where_no_night_has_run_says_so(monkeypatch, capsys, tmp_path):
    cli = _load()
    monkeypatch.setattr(cli, "install_report", lambda root, **kw: None)
    assert cli.main(["--data-root", str(tmp_path)]) == 0     # no settings file there
    assert "no night has run on this install yet" in capsys.readouterr().out


def test_a_night_cycle_switched_off_says_so(monkeypatch, capsys, tmp_path):
    """Fix round 1 (R72): not "no night has run" forever — it is off."""
    import yaml

    raw = yaml.safe_load((REPO / "data" / "palli.example.yaml").read_text(encoding="utf-8"))
    raw["companion"]["night_cycle"] = False
    (tmp_path / "palli.yaml").write_text(yaml.safe_dump(raw), encoding="utf-8")
    cli = _load()
    monkeypatch.setattr(cli, "install_report", lambda root, **kw: None)
    assert cli.main(["--data-root", str(tmp_path)]) == 0
    assert ("the night cycle is switched off, so no night runs and nothing nightly is due"
            in capsys.readouterr().out)


def test_with_no_data_root_it_reads_the_data_of_the_repo_it_lives_in(monkeypatch, capsys, tmp_path):
    """Fix round 1 (R72): the default is the repo's data/, not ./data — run
    from anywhere else it used to find nothing and say nothing was due. The
    repo root here is a tmp install, never a real one."""
    import json

    from core.config import config

    root = tmp_path / "install"
    night = root / "data" / "night_cycle"
    (night / ".outcomes").mkdir(parents=True)
    (night / "2026-08-11.jsonl").write_text('{"ok": true}\n', encoding="utf-8")
    (night / ".outcomes" / "2026-08-11.json").write_text(
        json.dumps({"night": "2026-08-11", "at": 0.0, "steps": {}, "failed": {}}), encoding="utf-8")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    cli = _load()
    monkeypatch.setattr(cli, "ROOT", root)
    monkeypatch.setattr(config, "DATABASE_PATH", "data/palli-local.sqlite3")
    monkeypatch.setattr(config, "IDENTITY_ROOT_PATH", "identity")
    monkeypatch.chdir(elsewhere)

    assert cli.main([]) in (0, 2)

    out = capsys.readouterr().out
    assert "no night has run" not in out
    assert any(line.startswith("  night_cycle ") for line in out.splitlines())
