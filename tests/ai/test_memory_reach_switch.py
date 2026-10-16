"""Memory that reaches: the switch, the record's path, the suite's pin (R15, T1;
plan docs/2026-10-13-memory-that-reaches-plan.md).

- ``companion.memory_reach`` (schema default OFF, dark until the wrap) turns
  on the searchable record of every message and everything that reads it.
  Fail-closed: unbooted settings, a missing field or any error read as OFF.
- The record lives in its own file, ``data/memory_reach.sqlite3``
  (gitignored); ``PALLI_MEMORY_REACH_PATH`` overrides it, which is how the
  suite stays off the live record.
- The suite holds the switch off, and never opens the live record.
"""
from __future__ import annotations

import os
import sqlite3
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO = Path(__file__).resolve().parents[2]


def test_the_switch_ships_dark():
    from core.settings_schema import CompanionConfig

    assert CompanionConfig.model_fields["memory_reach"].default is False
    assert CompanionConfig().memory_reach is False


def test_the_switch_fails_closed_on_unbooted_settings(monkeypatch):
    from ai import memory_reach_index

    real = memory_reach_index._enabled_impl
    monkeypatch.setattr("core.turn.companion_flags.companion_settings", lambda: None)
    assert real() is False

    def boom():
        raise RuntimeError("settings down")

    monkeypatch.setattr("core.turn.companion_flags.companion_settings", boom)
    assert real() is False
    monkeypatch.setattr("core.turn.companion_flags.companion_settings",
                        lambda: SimpleNamespace())
    assert real() is False


def test_the_switch_reads_the_companion_block(monkeypatch):
    from ai import memory_reach_index

    real = memory_reach_index._enabled_impl
    monkeypatch.setattr("core.turn.companion_flags.companion_settings",
                        lambda: SimpleNamespace(memory_reach=True))
    assert real() is True
    monkeypatch.setattr("core.turn.companion_flags.companion_settings",
                        lambda: SimpleNamespace(memory_reach=False))
    assert real() is False


def test_the_suite_holds_the_switch_off(monkeypatch):
    """Its own pin: with the setting on, ``enabled()`` is still off and the
    real accessor, reached through ``_enabled_impl``, is on."""
    from ai import memory_reach_index

    monkeypatch.setattr("core.turn.companion_flags.companion_settings",
                        lambda: SimpleNamespace(memory_reach=True))
    assert memory_reach_index.enabled() is False
    assert memory_reach_index._enabled_impl() is True


def test_the_record_path_follows_the_env_var(monkeypatch, tmp_path):
    from ai import memory_reach_index

    monkeypatch.setenv("PALLI_MEMORY_REACH_PATH", str(tmp_path / "record.sqlite3"))
    assert memory_reach_index.record_path() == tmp_path / "record.sqlite3"
    monkeypatch.delenv("PALLI_MEMORY_REACH_PATH")
    assert memory_reach_index.record_path() == REPO / "data" / "memory_reach.sqlite3"


def test_the_suite_never_touches_the_live_record():
    from ai import memory_reach_index

    # (Never ``from tests.conftest import …``: a second import of the conftest
    # re-runs its redirects with a fresh tmp root.)
    root = Path(os.environ["PALLI_TEST_DATA_ROOT"])
    assert memory_reach_index.record_path().resolve().is_relative_to(root.resolve())
    ignored = subprocess.run(["git", "check-ignore", "-q", "data/memory_reach.sqlite3"],
                             cwd=REPO, check=False)
    assert ignored.returncode == 0


def test_a_crashed_rebuilds_leftover_is_git_ignored():
    """M2 (Fable's whole-branch review): ``build(rebuild=True)`` writes
    ``data/memory_reach.sqlite3.building`` (and its -wal and -shm) beside the
    record; a hard kill mid-build leaves them, and they hold the owner's
    lines, so none of them is ever tracked."""
    assert "data/*.sqlite3.building*" in (REPO / ".gitignore").read_text(
        encoding="utf-8").splitlines()
    for leftover in ("data/memory_reach.sqlite3.building",
                     "data/memory_reach.sqlite3.building-wal",
                     "data/memory_reach.sqlite3.building-shm"):
        ignored = subprocess.run(["git", "check-ignore", "-q", leftover], cwd=REPO, check=False)
        assert ignored.returncode == 0, leftover


def test_the_audit_hook_refuses_the_live_record():
    """The backstop under the redirect. Read-only URI: were the hook ever to
    let it through, sqlite fails to open a missing file instead of making one."""
    live = REPO / "data" / "memory_reach.sqlite3"
    with pytest.raises(RuntimeError, match="BLOCKED by tests/conftest.py"):
        sqlite3.connect(f"file:{live}?mode=ro", uri=True)
