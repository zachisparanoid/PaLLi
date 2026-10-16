"""The scrub's file half is safe for her records (second read F6, 2026-10-04).

``scrub-old`` rewrote each matched file in place (``read_bytes`` then
``write_bytes``): a crash or a full disk between the truncate and the write
left a transcript, a present-moment or an inner-monologue file truncated or
empty, and the wrap ran it while she was running — two writers on a session
file. Now:

- each file is rewritten atomically: a temporary file in the same folder,
  flushed and synced, then ``os.replace`` over the original, its mode kept;
  a failure leaves the original whole and no temporary file behind;
- a real run refuses while her process holds :8000 (A3's
  ``her_listener_pid``, read from the stdlib-only ``core/her_listener.py`` so
  the script still runs under system python3); a dry run, which writes
  nothing, still counts.

These tests need no pykeepass (the vault half is tests/scripts/
test_rotate_vault_keys.py, which runs under system python3 only), so they run
in the venv gate. Fake tokens and tmp dirs only.
"""
from __future__ import annotations

import os
import stat
import sys
from pathlib import Path

import pytest

import scripts.rotate_vault_keys as rvk

TOKEN = "Bearer " + "a1B2c3D4e5F6g7H8i9J0kLmN"


def _session(repo: Path) -> Path:
    p = repo / "data" / "agent_sessions" / "discord_1.jsonl"
    p.parent.mkdir(parents=True)
    p.write_text('{"role": "tool", "content": "' + TOKEN + ' — and more"}\n', encoding="utf-8")
    return p


def _leftovers(folder: Path, keep: Path) -> list[str]:
    return sorted(p.name for p in folder.iterdir() if p != keep)


def test_a_file_is_rewritten_by_replace_with_its_mode_kept(tmp_path):
    p = _session(tmp_path)
    os.chmod(p, 0o640)
    inode = p.stat().st_ino

    counts = rvk.scrub([p], [], ssn=False, db=None, bearer=True)

    assert counts["files"] == 1 and counts["bearer"] == 1
    text = p.read_text(encoding="utf-8")
    assert TOKEN not in text and rvk.BEARER_REDACTED in text and "— and more" in text
    assert stat.S_IMODE(p.stat().st_mode) == 0o640
    assert p.stat().st_ino != inode                      # a new file put in place
    assert _leftovers(p.parent, p) == []


def test_a_failed_rewrite_leaves_the_original_whole(tmp_path, monkeypatch):
    p = _session(tmp_path)
    before = p.read_bytes()

    def boom(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(rvk.os, "replace", boom)
    with pytest.raises(OSError):
        rvk.scrub([p], [], ssn=False, db=None, bearer=True)

    assert p.read_bytes() == before
    assert _leftovers(p.parent, p) == []


def test_a_dry_run_writes_nothing(tmp_path):
    p = _session(tmp_path)
    before = p.read_bytes()
    counts = rvk.scrub([p], [], ssn=False, db=None, bearer=True, dry_run=True)
    assert counts["files"] == 1 and p.read_bytes() == before


def _main(monkeypatch, repo: Path, *extra: str) -> int:
    monkeypatch.setattr(sys, "argv", ["rotate_vault_keys.py", "scrub-old", "--bearer",
                                      "--no-db", "--repo", str(repo), *extra])
    return rvk.main()


def test_it_refuses_while_her_process_holds_the_port(tmp_path, monkeypatch):
    p = _session(tmp_path)
    before = p.read_bytes()
    monkeypatch.setattr(rvk, "_her_pid", lambda repo, port: 4242)

    with pytest.raises(SystemExit) as stop:
        _main(monkeypatch, tmp_path)

    assert "4242" in str(stop.value) and "8000" in str(stop.value)
    assert p.read_bytes() == before


def test_a_dry_run_still_counts_while_she_runs(tmp_path, monkeypatch, capsys):
    p = _session(tmp_path)
    before = p.read_bytes()
    monkeypatch.setattr(rvk, "_her_pid", lambda repo, port: 4242)

    assert _main(monkeypatch, tmp_path, "--dry-run") == 0
    assert p.read_bytes() == before
    assert "would change 1 file(s)" in capsys.readouterr().out


def test_with_her_stopped_it_scrubs(tmp_path, monkeypatch):
    p = _session(tmp_path)
    monkeypatch.setattr(rvk, "_her_pid", lambda repo, port: None)

    assert _main(monkeypatch, tmp_path) == 0
    assert TOKEN not in p.read_text(encoding="utf-8")


def test_her_pid_is_a3s_reader_for_that_checkout(tmp_path, monkeypatch):
    """The script asks A3's own reader — whose listener is on the port, and is
    it her main.py from THIS checkout — with --repo as the checkout."""
    import core.her_listener as her_listener

    asked: list = []

    def reader(port, **kw):
        asked.append((port, kw.get("project_root")))
        return 777

    monkeypatch.setattr(her_listener, "her_listener_pid", reader)
    assert rvk._her_pid(tmp_path, 8000) == 777
    assert asked == [(8000, tmp_path)]


def test_launcher_support_reads_the_same_reader():
    import core.her_listener as her_listener
    import core.launcher_support as launcher_support

    assert launcher_support.her_listener_pid is her_listener.her_listener_pid


def test_the_reader_module_needs_only_the_standard_library():
    """System python3 has no venv packages: the module the script imports
    must stand on the standard library alone."""
    import ast

    src = (Path(rvk.__file__).resolve().parents[1] / "core" / "her_listener.py").read_text(
        encoding="utf-8")
    roots = set()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Import):
            roots.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            roots.add(node.module.split(".")[0])
    assert roots <= set(sys.stdlib_module_names) | {"__future__"}, roots
