"""`palli_ctl history prune [--dry-run]` and `palli_ctl db shrink` (Batch 8,
task 3; rulings T3-4, T3-6). The wrap's two commands: see (then do) what the
nightly identity-history prune removes, and give the freed space back to the
disk afterwards. Everything runs against temp databases, never hers; the
real prune and the shrink are wrap commands, run by hand."""
from __future__ import annotations

import asyncio
import importlib.util
import sqlite3
from datetime import datetime
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
DAY = 86400.0


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "palli_ctl_history_db_test_module", REPO_ROOT / "scripts" / "palli_ctl.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def ctl():
    return _load_module()


# ── history prune ────────────────────────────────────────────────────────

async def _seed(brain) -> None:
    await brain.initialize()
    rows = []
    for user_id, doc_key, days in ((1, "changelog", 100), (1, "memory", 100), (2, "preferences", 3)):
        for d in range(1, days + 1):
            for i in range(3):
                rows.append((user_id, doc_key, (d - 1) * 3 + i + 1, d * DAY + i))
    conn = await brain.get_connection()
    try:
        async with conn.transaction():
            for user_id, doc_key, version, created_at in rows:
                await conn.execute(
                    "INSERT INTO identity_document_versions (user_id, platform, doc_key, file_path, "
                    "version, actor_type, actor_id, reason, content, frontmatter, created_at) "
                    "VALUES ($1, 'owner', $2, 'X.md', $3, 'system', 'test', 'save', $4, '{}', $5)",
                    user_id, doc_key, version, "y" * 1000, created_at,
                )
    finally:
        await conn.close()


@pytest.fixture
def db(tmp_path):
    """A temp database with 600 + 9 copies; each command runs its own loop."""
    from core.database import PalliBrain

    brain = PalliBrain(db_path=str(tmp_path / "ctl-history.sqlite3"))
    asyncio.run(_seed(brain))
    return brain


def _count(db_path: Path) -> int:
    conn = sqlite3.connect(str(db_path))
    try:
        return int(conn.execute("SELECT COUNT(*) FROM identity_document_versions").fetchone()[0])
    finally:
        conn.close()


def _knobs(monkeypatch, ctl, keep_last, daily_days=30):
    """The memory.history_* rows, without reading any palli.yaml."""
    monkeypatch.setattr(ctl, "_history_knobs", lambda: (keep_last, daily_days))


def test_a_dry_run_prints_each_document_and_removes_nothing(ctl, db, monkeypatch):
    _knobs(monkeypatch, ctl, 10)
    before = _count(db.db_path)
    lines: list[str] = []
    args = ctl.build_parser().parse_args(["history", "prune", "--dry-run"])
    assert ctl.run_history_command(args, db=db, now=101 * DAY, out=lines.append) == 0
    assert _count(db.db_path) == before
    text = "\n".join(lines)
    for doc_key in ("changelog", "memory", "preferences"):
        assert doc_key in text, text
    changelog = next(line for line in lines if "changelog" in line)
    assert "keep" in changelog and "remove" in changelog and "KB" in changelog, changelog
    preferences = next(line for line in lines if "preferences" in line)
    assert "remove 0" in preferences, preferences            # 9 copies over 3 days: all kept
    assert "dry run" in lines[-1].lower() and "nothing was removed" in lines[-1].lower()


def test_the_real_prune_removes_and_points_at_the_shrink(ctl, db, monkeypatch):
    _knobs(monkeypatch, ctl, 10)
    before = _count(db.db_path)
    lines: list[str] = []
    args = ctl.build_parser().parse_args(["history", "prune"])
    assert ctl.run_history_command(args, db=db, now=101 * DAY, out=lines.append) == 0
    after = _count(db.db_path)
    assert 0 < after < before
    assert f"removed {before - after}" in "\n".join(lines)
    assert "db shrink" in lines[-1]


def test_the_prune_says_so_when_it_is_off(ctl, db, monkeypatch):
    _knobs(monkeypatch, ctl, 0)
    before = _count(db.db_path)
    lines: list[str] = []
    args = ctl.build_parser().parse_args(["history", "prune"])
    assert ctl.run_history_command(args, db=db, now=101 * DAY, out=lines.append) == 0
    assert _count(db.db_path) == before
    assert "off" in lines[0] and "history_keep_last" in lines[0]


def test_settings_that_cannot_be_read_stop_the_prune(ctl, db, monkeypatch):
    from core import settings_boot

    def broken():
        raise ValueError("palli.yaml failed validation")

    monkeypatch.setattr(settings_boot.settings_service, "snapshot", broken)
    before = _count(db.db_path)
    with pytest.raises(RuntimeError, match="Cannot read the settings"):
        ctl.run_history_command(ctl.build_parser().parse_args(["history", "prune"]), db=db, now=101 * DAY)
    assert _count(db.db_path) == before


def test_the_knobs_are_the_memory_rows(ctl, monkeypatch):
    from types import SimpleNamespace

    from core import settings_boot

    snapshot = SimpleNamespace(memory=SimpleNamespace(history_keep_last=10, history_daily_days=21))
    monkeypatch.setattr(settings_boot.settings_service, "snapshot", lambda: snapshot)
    assert ctl._history_knobs() == (10, 21)


def test_history_needs_an_action(ctl):
    with pytest.raises(RuntimeError, match="history prune"):
        ctl.run_history_command(ctl.build_parser().parse_args(["history"]))


# ── db shrink ────────────────────────────────────────────────────────────

AFTERNOON = datetime(2026, 10, 1, 14, 0)


def _proc(tmp_path: Path, processes: dict[int, bytes]) -> Path:
    root = tmp_path / "proc"
    for pid, cmdline in processes.items():
        (root / str(pid)).mkdir(parents=True)
        (root / str(pid) / "cmdline").write_bytes(cmdline)
    (root / "self").mkdir(parents=True, exist_ok=True)       # not a pid: ignored
    return root


def _bloated(tmp_path: Path) -> Path:
    """A WAL database that held ~4 MB and now holds ~40 KB."""
    path = tmp_path / "bloated.sqlite3"
    conn = sqlite3.connect(str(path))
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, body TEXT)")
        conn.executemany("INSERT INTO t (body) VALUES (?)", [("z" * 4000,) for _ in range(1000)])
        conn.commit()
        conn.execute("DELETE FROM t WHERE id > 10")
        conn.commit()
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        conn.close()
    return path


def test_the_shrink_refuses_while_the_nightly_backup_runs(ctl, tmp_path):
    path = _bloated(tmp_path)
    size = path.stat().st_size
    proc = _proc(tmp_path, {
        4242: b"/bin/sh\0-c\0cd /srv/palli && venv/bin/python scripts/palli_ctl.py backup --out "
              b"/mnt/nas/_backups/palli --include-home --encrypt --keep 14 >> logs/backup.log 2>&1\0",
    })
    args = ctl.build_parser().parse_args(["db", "shrink"])
    with pytest.raises(RuntimeError, match=r"backup is running \(pid 4242\)"):
        ctl.run_db_command(args, db_path=path, now=AFTERNOON, proc_root=proc)
    assert path.stat().st_size == size


def test_other_palli_ctl_runs_and_backup_readers_do_not_block_it(ctl, tmp_path):
    proc = _proc(tmp_path, {
        11: b"venv/bin/python\0scripts/palli_ctl.py\0status\0",
        12: b"tail\0-f\0logs/backup.log\0",
    })
    assert ctl._backup_process(proc) is None


@pytest.mark.parametrize("hour, minute", [(3, 0), (3, 20), (4, 29)])
def test_the_shrink_refuses_between_three_and_half_past_four(ctl, tmp_path, hour, minute):
    path = _bloated(tmp_path)
    size = path.stat().st_size
    args = ctl.build_parser().parse_args(["db", "shrink"])
    with pytest.raises(RuntimeError, match="03:00"):
        ctl.run_db_command(args, db_path=path, now=datetime(2026, 10, 1, hour, minute),
                           proc_root=_proc(tmp_path, {}))
    assert path.stat().st_size == size


@pytest.mark.parametrize("hour, minute", [(6, 30), (6, 45), (6, 59)])
def test_the_shrink_refuses_in_the_first_half_hour_of_the_night(ctl, tmp_path, hour, minute):
    """The night pass moved from 04:00 (inside the backup's window) to its
    run time, 06:30 by default (the owner, 10-05: off DeepSeek's peak); the
    shrink keeps away from its first half hour, read from her settings."""
    path = _bloated(tmp_path)
    size = path.stat().st_size
    args = ctl.build_parser().parse_args(["db", "shrink"])
    with pytest.raises(RuntimeError, match="06:30 and 07:00: her night pass starts at 06:30"):
        ctl.run_db_command(args, db_path=path, now=datetime(2026, 10, 1, hour, minute),
                           proc_root=_proc(tmp_path, {}))
    assert path.stat().st_size == size


def test_the_shrink_follows_the_night_run_time(ctl, tmp_path):
    path = _bloated(tmp_path)
    args = ctl.build_parser().parse_args(["db", "shrink"])
    with pytest.raises(RuntimeError, match="08:00 and 08:30"):
        ctl.run_db_command(args, db_path=path, now=datetime(2026, 10, 1, 8, 10),
                           proc_root=_proc(tmp_path, {}), night_run_at=(8, 0))


def test_the_shrink_vacuums_securely_and_truncates_the_wal(ctl, tmp_path, monkeypatch):
    path = _bloated(tmp_path)
    before = path.stat().st_size
    statements: list[str] = []
    real_connect = sqlite3.connect

    def traced(*a, **kw):
        conn = real_connect(*a, **kw)
        conn.set_trace_callback(statements.append)
        return conn

    monkeypatch.setattr(sqlite3, "connect", traced)
    lines: list[str] = []
    args = ctl.build_parser().parse_args(["db", "shrink"])
    for allowed in (datetime(2026, 10, 1, 2, 59), datetime(2026, 10, 1, 4, 30),
                    datetime(2026, 10, 1, 7, 0)):
        assert ctl.run_db_command(args, db_path=path, now=allowed,
                                  proc_root=_proc(tmp_path / str(allowed.hour), {}), out=lines.append) == 0
    monkeypatch.setattr(sqlite3, "connect", real_connect)

    assert path.stat().st_size < before / 4
    wal = path.with_name(path.name + "-wal")
    assert not wal.exists() or wal.stat().st_size == 0
    upper = [s.upper().replace(" ", "") for s in statements]
    secure = upper.index("PRAGMASECURE_DELETE=ON")
    vacuum = upper.index("VACUUM")
    checkpoint = upper.index("PRAGMAWAL_CHECKPOINT(TRUNCATE)")
    assert secure < vacuum < checkpoint
    assert "MB" in lines[0] and path.name in lines[0]
    assert _count_rows(path) == 10


def _count_rows(path: Path) -> int:
    conn = sqlite3.connect(str(path))
    try:
        return int(conn.execute("SELECT COUNT(*) FROM t").fetchone()[0])
    finally:
        conn.close()


def test_the_shrink_refuses_a_missing_database(ctl, tmp_path):
    args = ctl.build_parser().parse_args(["db", "shrink"])
    with pytest.raises(RuntimeError, match="no database"):
        ctl.run_db_command(args, db_path=tmp_path / "absent.sqlite3", now=AFTERNOON,
                           proc_root=_proc(tmp_path, {}))
    assert not (tmp_path / "absent.sqlite3").exists()


def test_db_needs_an_action(ctl):
    with pytest.raises(RuntimeError, match="db shrink"):
        ctl.run_db_command(ctl.build_parser().parse_args(["db"]))


def test_the_shrink_help_asks_for_a_fresh_backup_first(ctl, capsys):
    with pytest.raises(SystemExit):
        ctl.build_parser().parse_args(["db", "shrink", "--help"])
    text = capsys.readouterr().out.lower()
    assert "fresh backup" in text and "03:00" in text


def test_palli_ctl_stays_crlf_throughout():
    raw = (REPO_ROOT / "scripts" / "palli_ctl.py").read_bytes()
    assert raw.count(b"\n") == raw.count(b"\r\n")
