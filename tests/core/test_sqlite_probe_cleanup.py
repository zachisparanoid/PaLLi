"""Regression tests for SQLite probe-file leak cleanup.

History: ``_check_writable_sqlite_path()`` (now ``inspect_sqlite_database_path``)
leaked 806 stranded ``.palli-sqlite-probe-*.sqlite3`` files because the
cleanup-on-close path swallowed ``Path.unlink()`` exceptions on Windows when WAL
sidecar files were still open. The startup sweep added in E.6 step 1 globs the
data directory and removes stale probe files older than ~5 minutes (so we don't
race with active probes from concurrent processes).
"""

from __future__ import annotations

import os
import time
from pathlib import Path

from core.sqlite_database_bootstrap import _sweep_stale_sqlite_probes


def _touch(path: Path, *, age_seconds: float) -> Path:
    path.write_text("")
    if age_seconds > 0:
        mtime = time.time() - age_seconds
        os.utime(path, (mtime, mtime))
    return path


def test_sweep_removes_stale_probe_files(tmp_path: Path) -> None:
    stale_main = _touch(tmp_path / ".palli-sqlite-probe-abc.sqlite3", age_seconds=600.0)
    stale_wal = _touch(tmp_path / ".palli-sqlite-probe-abc.sqlite3-wal", age_seconds=600.0)
    stale_shm = _touch(tmp_path / ".palli-sqlite-probe-abc.sqlite3-shm", age_seconds=600.0)
    stale_journal = _touch(
        tmp_path / ".palli-sqlite-probe-abc.sqlite3-journal", age_seconds=600.0
    )

    removed = _sweep_stale_sqlite_probes(tmp_path)

    assert removed == 4
    assert not stale_main.exists()
    assert not stale_wal.exists()
    assert not stale_shm.exists()
    assert not stale_journal.exists()


def test_sweep_preserves_recent_probe_files(tmp_path: Path) -> None:
    """Active probes from concurrent processes must not be touched."""
    fresh = _touch(tmp_path / ".palli-sqlite-probe-xyz.sqlite3", age_seconds=10.0)
    fresh_wal = _touch(tmp_path / ".palli-sqlite-probe-xyz.sqlite3-wal", age_seconds=10.0)

    removed = _sweep_stale_sqlite_probes(tmp_path)

    assert removed == 0
    assert fresh.exists()
    assert fresh_wal.exists()


def test_sweep_ignores_unrelated_files(tmp_path: Path) -> None:
    db = _touch(tmp_path / "palli-local.sqlite3", age_seconds=600.0)
    backup = _touch(tmp_path / "palli-local.sqlite3.pre-completion.bak", age_seconds=600.0)
    other = _touch(tmp_path / "some-other-file.sqlite3", age_seconds=600.0)

    removed = _sweep_stale_sqlite_probes(tmp_path)

    assert removed == 0
    assert db.exists()
    assert backup.exists()
    assert other.exists()


def test_sweep_handles_missing_data_dir(tmp_path: Path) -> None:
    nonexistent = tmp_path / "does-not-exist"

    removed = _sweep_stale_sqlite_probes(nonexistent)

    assert removed == 0


def test_sweep_handles_partial_failures(tmp_path: Path, monkeypatch) -> None:
    """If unlink fails for one file we continue with the rest."""
    keep = _touch(tmp_path / ".palli-sqlite-probe-keep.sqlite3", age_seconds=600.0)
    drop = _touch(tmp_path / ".palli-sqlite-probe-drop.sqlite3", age_seconds=600.0)

    real_unlink = Path.unlink

    def flaky_unlink(self: Path) -> None:
        if self.name.endswith("keep.sqlite3"):
            raise PermissionError("simulated lock")
        real_unlink(self)

    monkeypatch.setattr(Path, "unlink", flaky_unlink)

    removed = _sweep_stale_sqlite_probes(tmp_path)

    assert removed == 1
    assert keep.exists()
    assert not drop.exists()


def test_sweep_called_during_initialize(tmp_path, monkeypatch) -> None:
    """Bootstrap must invoke the sweep so leaked probes get cleaned on startup."""
    import asyncio

    from core.database import PalliBrain

    db_path = tmp_path / "data" / "palli-local.sqlite3"
    db_path.parent.mkdir(parents=True, exist_ok=True)

    stale = _touch(
        db_path.parent / ".palli-sqlite-probe-stale.sqlite3", age_seconds=600.0
    )

    async def _run() -> None:
        brain = PalliBrain(str(db_path))
        try:
            await brain.initialize()
        finally:
            await brain.close()

    asyncio.run(_run())

    assert not stale.exists()
