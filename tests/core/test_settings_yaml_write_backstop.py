"""The live settings yaml may be read by a test. It may never be written.

The final review's M13 (the Task 3 re-review's obs-1). The suite has an absolute
backstop against opening the owner's live sqlite database — added the day two full-suite
runs wrote a real reminder into it. The settings yaml had no such backstop, and it is now
a WRITE target: `core.settings_boot.boot_settings()` seeds a missing config from the
example, and during this build's own mutation round, with the seed's `exists()` guard
removed, that call rewrote the owner's live `data/palli.yaml`.

Redirecting the constant and the singleton (tests/conftest.py) is the first line. This is
the second: an audit hook that refuses a write-mode open — or a rename onto — the real
file, whatever code path builds it.

Two-sided on purpose: a temporary COPY of the same file, with the same name, must stay
writable, or the backstop would break every test that works on one.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
LIVE_YAML = REPO / "data" / "palli.yaml"

@pytest.fixture(scope="module")
def guard(pytestconfig):
    """The root conftest's hook function itself.

    Reached through the plugin manager because by the time a test in tests/core runs,
    `sys.modules["conftest"]` is the one in THIS directory — the root module object is
    gone from sys.modules while its audit hook is still installed. The cases that must
    never be performed for real (a rename onto his file) are checked against this
    function; that the hook is INSTALLED is checked by really trying to open it."""
    root = str(REPO / "tests" / "conftest.py")
    for plugin in pytestconfig.pluginmanager.get_plugins():
        if getattr(plugin, "__file__", None) == root:
            return plugin._deny_live_settings_writes
    raise AssertionError("the root conftest is not registered as a plugin")


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.mark.skipif(not LIVE_YAML.is_file(), reason="no live config on this install")
def test_a_real_write_open_of_the_live_config_is_refused_by_the_installed_hook():
    """The hook is INSTALLED, not merely defined. Append mode on purpose: if the guard
    were ever missing, this open would still leave the file byte-identical."""
    before = _digest(LIVE_YAML)
    with pytest.raises(RuntimeError, match="BLOCKED"):
        open(LIVE_YAML, "a").close()
    assert _digest(LIVE_YAML) == before


@pytest.mark.skipif(not LIVE_YAML.is_file(), reason="no live config on this install")
def test_reading_the_live_config_is_still_allowed():
    """Four test files open it read-only as a fixture; the backstop is about writes."""
    assert open(LIVE_YAML, "r", encoding="utf-8").read(1) != ""


@pytest.mark.parametrize("mode", ["w", "a", "r+", "wb", "x", "w+"])
def test_every_write_mode_is_refused(guard, mode):
    with pytest.raises(RuntimeError, match="BLOCKED"):
        guard("open", (str(LIVE_YAML), mode, 0))


def test_a_write_by_os_open_flags_is_refused(guard):
    """os.open passes no mode string — only flags."""
    with pytest.raises(RuntimeError, match="BLOCKED"):
        guard("open", (str(LIVE_YAML), None, os.O_WRONLY | os.O_CREAT))


def test_an_atomic_write_that_renames_onto_the_live_config_is_refused(guard):
    """The write-to-tmp-then-replace path never opens the target at all."""
    with pytest.raises(RuntimeError, match="BLOCKED"):
        guard("os.rename", ("/tmp/whatever.tmp", str(LIVE_YAML)))


def test_a_read_open_is_not_refused(guard):
    guard("open", (str(LIVE_YAML), "r", 0))
    guard("open", (str(LIVE_YAML), "rb", 0))
    guard("open", (str(LIVE_YAML), None, os.O_RDONLY))


@pytest.mark.skipif(not LIVE_YAML.is_file(), reason="no live config on this install")
def test_a_tmp_copy_with_the_same_name_stays_writable(tmp_path):
    """The other side: the suite's own settings yaml is a copy called palli.yaml."""
    copy = tmp_path / "palli.yaml"
    shutil.copyfile(LIVE_YAML, copy)
    with open(copy, "a", encoding="utf-8") as handle:
        handle.write("\n# a test wrote this\n")
    assert copy.read_text(encoding="utf-8").endswith("# a test wrote this\n")
    assert _digest(LIVE_YAML) != _digest(copy)


def test_an_unrelated_file_is_never_touched_by_the_guard(guard, tmp_path):
    other = tmp_path / "config.yaml"
    guard("open", (str(other), "w", 0))
    guard("open", (str(tmp_path / "palli.yaml"), "w", 0))
