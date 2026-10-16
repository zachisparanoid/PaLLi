"""scripts/setup_workbench_user.sh — the one script whose output is a security claim.

It creates the palli-work user, her workspace and the sudoers line, and then it
PROVES the wall: "substrate write correctly DENIED", "substrate read correctly
DENIED". Those two lines are the only evidence anyone has that the OS wall holds.

Until the final review's I3 the probes tested one machine's literal repository path.
On any other host that path does not exist, both probes fail because there is nothing
there — and the script prints the two green lines anyway. A security check that
passes when it could not run is worse than no check: it is a false green.

These tests do not run the script (it needs sudo and a real user). They pin the two
properties that make its probes mean something: the substrate is DERIVED from where
the script actually lives, and the derivation lands on the repository root.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "setup_workbench_user.sh"
TEXT = SCRIPT.read_text(encoding="utf-8")

DERIVATION = 'SUBSTRATE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"'


def test_the_script_still_parses():
    assert subprocess.run(["bash", "-n", str(SCRIPT)]).returncode == 0


def test_the_substrate_is_derived_from_where_the_script_lives():
    assert DERIVATION in TEXT


def test_the_derivation_lands_on_the_repository_root():
    """Non-vacuous: the same expression, run against the script's real path."""
    done = subprocess.run(
        ["bash", "-c", 'cd "$(dirname "$1")/.." && pwd -P', "_", str(SCRIPT)],
        capture_output=True, text=True, check=True,
    )
    assert Path(done.stdout.strip()) == REPO.resolve()


def test_every_substrate_probe_uses_the_derived_path():
    """The three lines that touch the substrate — two probes and the cleanup rm."""
    probes = [ln for ln in TEXT.splitlines() if "substrate" in ln.lower() or "SUBSTRATE" in ln]
    touching = [ln for ln in TEXT.splitlines()
                if re.search(r"(touch|rm -f|ls) ", ln) and "SUBSTRATE" in ln]
    assert len(touching) == 3, probes
    assert all("$SUBSTRATE" in ln for ln in touching), touching


# Assembled from fragments, the way tests/scripts/test_make_example_yaml.py plants its
# markers: this file ships, and a shipped test that spells the build machine's directory
# names out is the same leak it exists to prevent.
FORBIDDEN = (
    "/home/" + "ubuntu/projects",
    "palli-" + "local-refactor",
    "historical-" + "palli",
    "projects/" + "chatbot",
)


def test_no_absolute_path_into_a_particular_machines_project_tree():
    """The probes, and the comments that narrated one host's directory layout and
    permissions, are gone: nothing here describes a machine that is not the reader's."""
    for forbidden in FORBIDDEN:
        assert forbidden not in TEXT, forbidden


# --- Batch 4 fix round 1 (R14a): her NAS folder is a dedicated, empty one ----
# The owner's ruling (2026-09-28, "New empty subfolder"): the folder the app
# promises her by default can hold his own files, and the script used to
# `chown -R` it to her work account. On a NAS that honours chown that hands
# his files to her. The default is now <root>/palli/workbench, and a folder
# that already holds anything is never re-owned.
#
# The NAS block runs here for real, with a stand-in `sudo` first on PATH that
# only logs what it was asked (and lets `find` look): no real sudo, ever.

import os
import stat as _stat

import pytest


def _block(start: str, end: str) -> str:
    """The script's lines from the one starting with `start` to the next `end` line."""
    lines = TEXT.splitlines()
    first = next(i for i, ln in enumerate(lines) if ln.startswith(start))
    last = next(i for i in range(first + 1, len(lines)) if lines[i] == end)
    return "\n".join(lines[first:last + 1])


@pytest.fixture
def fake_sudo(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "sudo.log"
    shim = bin_dir / "sudo"
    shim.write_text(
        '#!/bin/sh\n'
        'echo "$*" >> "$SUDO_LOG"\n'
        'if [ "$1" = "find" ]; then exec "$@"; fi\n'
        'exit 0\n'
    )
    shim.chmod(0o755)
    # No BASH_ENV / ENV: a startup file the shell reads before the script
    # could put a real sudo back in front of the stand-in.
    base = {k: v for k, v in os.environ.items() if k not in ("BASH_ENV", "ENV")}
    env = {**base, "PATH": f"{bin_dir}:{os.environ['PATH']}", "SUDO_LOG": str(log)}
    return env, log


# The stand-in again, as a shell FUNCTION at the head of every snippet: a
# function wins over anything on PATH, so a real sudo can never run (final
# review, deferred minor L138). It logs what it was asked, lets `find` look,
# and answers yes to everything else.
STAND_IN_SUDO = (
    'sudo() { printf \'%s\\n\' "$*" >> "$SUDO_LOG"; '
    'if [ "$1" = find ]; then "$@"; return; fi; return 0; }'
)


def _run_nas_block(nas_dir, env):
    script = "\n".join([
        STAND_IN_SUDO,
        _block("folder_holds_files() {", "}"),
        f'NAS_DIR="{nas_dir}"; WORK_USER=palli-work; APP_USER=app',
        _block('if [ -n "$NAS_DIR" ]; then', "fi"),
    ])
    return subprocess.run(["bash", "-c", script], env=env, capture_output=True, text=True)


def test_the_default_nas_folder_is_the_dedicated_subfolder():
    line = next(ln for ln in TEXT.splitlines() if ln.startswith("NAS_DIR="))
    done = subprocess.run(
        ["bash", "-c", f'PALLI_NAS_ROOT=/srv/example-nas; unset NAS_DIR; {line}; echo "$NAS_DIR"'],
        capture_output=True, text=True, check=True,
    )
    assert done.stdout.strip() == "/srv/example-nas/palli/workbench"
    empty = subprocess.run(["bash", "-c", f'unset PALLI_NAS_ROOT NAS_DIR; {line}; echo "[$NAS_DIR]"'],
                           capture_output=True, text=True, check=True)
    assert empty.stdout.strip() == "[]"  # no NAS knob, no NAS folder


def test_nothing_recursive_is_ever_handed_to_her_on_the_nas():
    assert not [ln for ln in TEXT.splitlines() if "chown -R" in ln and "NAS_DIR" in ln]


def test_a_folder_that_already_holds_files_is_never_re_owned(tmp_path, fake_sudo):
    env, log = fake_sudo
    folder = tmp_path / "palli"
    folder.mkdir()
    (folder / "an-archive.7z").write_text("not hers")

    done = _run_nas_block(folder, env)

    assert done.returncode == 0, done.stderr
    asked = log.read_text() if log.exists() else ""
    assert "chown" not in asked and "chmod" not in asked and "mkdir" not in asked
    assert "already holds files" in done.stdout


@pytest.mark.parametrize("exists", [True, False])
def test_a_new_or_empty_folder_is_made_hers(tmp_path, fake_sudo, exists):
    env, log = fake_sudo
    folder = tmp_path / "palli" / "workbench"
    if exists:
        folder.mkdir(parents=True)

    done = _run_nas_block(folder, env)

    assert done.returncode == 0, done.stderr
    asked = log.read_text()
    assert f"mkdir -p {folder}" in asked
    assert f"chown palli-work:app {folder}" in asked
    assert f"chmod 2770 {folder}" in asked


@pytest.mark.skipif(os.geteuid() == 0, reason="root can list any folder")
def test_a_folder_that_cannot_be_listed_is_never_assumed_empty(tmp_path, fake_sudo):
    env, log = fake_sudo
    folder = tmp_path / "locked"
    folder.mkdir()
    (folder / "something").write_text("x")
    folder.chmod(0)
    try:
        done = _run_nas_block(folder, env)
    finally:
        folder.chmod(_stat.S_IRWXU)
    asked = log.read_text() if log.exists() else ""
    assert "chown" not in asked and "chmod" not in asked


def test_the_probe_success_line_names_the_folder_and_warns_about_a_shared_one():
    probe = _block('if [ -z "$NAS_DIR" ]; then', "fi")
    assert 'NAS folder $NAS_DIR writable by $WORK_USER' in probe
    assert "WORKBENCH_NAS_DIR=$NAS_DIR" in probe
    assert "NAS_DIR_HOLDS_FILES" in probe



# --- final review, deferred minor (T10/11 L137): never through a link ---------
# A symlink at NAS_DIR was treated as an empty folder — `find` does not look
# through a link given as its start point — and then chowned and chmodded
# THROUGH the link, into whatever folder it names. A regular file there read
# as empty too. Either is now "not ours to touch": said, and skipped.


def _asked(log):
    return log.read_text() if log.exists() else ""


@pytest.mark.parametrize("target_holds_files", [True, False])
def test_a_linked_nas_dir_is_never_followed(tmp_path, fake_sudo, target_holds_files):
    env, log = fake_sudo
    real = tmp_path / "someone-elses-folder"
    real.mkdir()
    if target_holds_files:
        (real / "their-archive.7z").write_text("not hers")
    link = tmp_path / "palli" / "workbench"
    link.parent.mkdir()
    link.symlink_to(real)

    done = _run_nas_block(link, env)

    assert done.returncode == 0, done.stderr
    asked = _asked(log)
    assert "chown" not in asked and "chmod" not in asked and "mkdir" not in asked, asked
    assert "is a link or not a folder" in done.stdout
    assert "NOT touching it" in done.stdout


def test_a_dangling_link_at_nas_dir_is_never_followed(tmp_path, fake_sudo):
    env, log = fake_sudo
    link = tmp_path / "workbench"
    link.symlink_to(tmp_path / "does-not-exist-yet")

    done = _run_nas_block(link, env)

    assert done.returncode == 0, done.stderr
    assert "mkdir" not in _asked(log) and "chown" not in _asked(log)
    assert not (tmp_path / "does-not-exist-yet").exists()
    assert "is a link or not a folder" in done.stdout


def test_a_regular_file_at_nas_dir_is_never_touched(tmp_path, fake_sudo):
    env, log = fake_sudo
    stray = tmp_path / "workbench"
    stray.write_text("a file, not a folder")

    done = _run_nas_block(stray, env)

    assert done.returncode == 0, done.stderr
    asked = _asked(log)
    assert "chown" not in asked and "chmod" not in asked and "mkdir" not in asked, asked
    assert "is a link or not a folder" in done.stdout


def test_the_emptiness_check_looks_through_a_link(tmp_path, fake_sudo):
    """Belt and braces: even called on a link, the listing sees what the
    linked folder holds (find -H), so it is never "empty" by accident."""
    env, _log = fake_sudo
    real = tmp_path / "full"
    real.mkdir()
    (real / "something").write_text("x")
    link = tmp_path / "link"
    link.symlink_to(real)
    script = "\n".join([
        STAND_IN_SUDO,
        _block("folder_holds_files() {", "}"),
        f'if folder_holds_files "{link}"; then echo HOLDS; else echo EMPTY; fi',
    ])
    done = subprocess.run(["bash", "-c", script], env=env, capture_output=True, text=True)
    assert done.stdout.strip() == "HOLDS", done.stdout + done.stderr


def test_the_probe_gives_no_green_line_for_a_folder_it_would_not_touch(tmp_path, fake_sudo):
    env, _log = fake_sudo
    script = "\n".join([
        STAND_IN_SUDO,
        f'NAS_DIR="{tmp_path / "workbench"}"; WORK_USER=palli-work; '
        'NAS_DIR_HOLDS_FILES=""; NAS_DIR_NOT_OURS=1',
        _block('if [ -z "$NAS_DIR" ]; then', "fi"),
    ])
    done = subprocess.run(["bash", "-c", script], env=env, capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    assert "writable by" not in done.stdout
    assert "probe skipped" in done.stdout
