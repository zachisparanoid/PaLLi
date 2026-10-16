"""Batch 8, task 5 (6.3, B8-4): limits on logs and throwaway folders, aged by
the date in each file's own name — and her records are never touched.

Rulings T5-1..T5-7 (docs: .superpowers/sdd/2026-09-30-before-strangers-plan):
the LAST date in a name is its age (``palli_verbose_2026-09-18.log._2026-09-21``
holds 09-21); a file this process's logging handlers hold open is never
touched; ``.gz`` files are only ever deleted; a browser profile is one unit,
aged by the newest mtime inside; PROTECTED folders and anything outside the
sweepable roots are refused and counted; the boot sweep runs after settings
boot, off the event loop; with ``retention.enabled`` off, today's boot sweep
in ``main.py`` runs exactly as before.

Every tree here is a tmp tree; nothing reads or writes the live ``logs/`` or
``data/``.
"""
from __future__ import annotations

import asyncio
import gzip
import logging
import logging.handlers
import os
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

import core.retention as retention
from core.retention import PROTECTED, Policy, sweep
from core.retention import REPO_ROOT as _REAL_REPO_ROOT
from core.retention import _sweep_logs as _REAL_SWEEP_STEP
from core.settings_schema import RetentionConfig

DAY = 86400.0
NOW = 1_790_000_000.0  # a fixed clock
MB = 1024 * 1024


def _touch(p, text="x", mtime=NOW):
    p.parent.mkdir(parents=True, exist_ok=True); p.write_text(text); os.utime(p, (mtime, mtime)); return p


def _at(y, m, d, hh=12):
    """A local wall-clock time, the way the night and the names count days."""
    return time.mktime((y, m, d, hh, 0, 0, 0, 0, -1))


def _gz(p, text="zipped", mtime=NOW):
    p.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(p, "wt", encoding="utf-8") as fh:
        fh.write(text)
    os.utime(p, (mtime, mtime))
    return p


def _snapshot(root: Path) -> dict:
    """Every file under ``root`` with its bytes — links as their target text."""
    out = {}
    for dirpath, dirnames, filenames in os.walk(root):
        for name in filenames:
            p = Path(dirpath) / name
            out[str(p.relative_to(root))] = ("link:" + os.readlink(p)) if p.is_symlink() else p.read_bytes()
    return out


# ── the brief's three (Step 1) ──────────────────────────────────────────────

def test_age_comes_from_the_filename_not_the_mtime(tmp_path):
    old = _touch(tmp_path / "logs/palli_verbose_2026-09-18.log", mtime=NOW)   # scrub reset its mtime today
    new = _touch(tmp_path / "logs/palli_verbose_2026-09-29.log", mtime=NOW)
    sweep(tmp_path, now=time.mktime((2026, 9, 30, 12, 0, 0, 0, 0, -1)),
          policy=[Policy("logs/palli_verbose_*", days=5, action="delete")])
    assert not old.exists() and new.exists()


def test_her_records_are_refused_even_when_a_glob_matches(tmp_path):
    rec = _touch(tmp_path / "data/agent_sessions/discord_1.jsonl")
    thought = _touch(tmp_path / "data/inner_monologue/2026-05-05.jsonl")
    result = sweep(tmp_path, now=NOW + 400 * DAY, policy=[Policy("data/**/*", days=1, action="delete")])
    assert rec.exists() and thought.exists()
    assert result.refused >= 2


def test_old_logs_are_gzipped_after_a_week_and_still_readable(tmp_path):
    f = _touch(tmp_path / "logs/palli_2026-09-01.log", text="hello")
    sweep(tmp_path, now=time.mktime((2026, 9, 30, 12, 0, 0, 0, 0, -1)),
          policy=[Policy("logs/palli_2*.log", days=7, action="gzip")])
    assert not f.exists()
    assert gzip.open(str(f) + ".gz", "rt").read() == "hello"


# ── T5-1: the last date wins; an open file is never touched; .gz only goes ──

def test_the_last_date_in_the_name_is_the_files_age(tmp_path):
    """A rotated chunk carries two dates: the process start in its base name
    and the day it holds in its suffix. The day it holds is its age — with
    the first date, a process that ran for days would lose fresh chunks."""
    logs = tmp_path / "logs"
    fresh = _touch(logs / "palli_verbose_2026-09-18.log._2026-09-29", mtime=_at(2026, 8, 1))
    stale = _touch(logs / "palli_verbose_2026-09-18.log._2026-09-21", mtime=_at(2026, 9, 30))
    fresh_gz = _gz(logs / "palli_verbose_2026-09-18.log._2026-09-28.gz", mtime=_at(2026, 8, 1))
    stale_gz = _gz(logs / "palli_verbose_2026-09-18.log._2026-09-20.gz", mtime=_at(2026, 9, 30))

    result = sweep(tmp_path, now=_at(2026, 9, 30), open_paths=(),
                   policy=[Policy("logs/palli_verbose_*", days=5, action="delete")])

    assert fresh.exists() and fresh_gz.exists()
    assert not stale.exists() and not stale_gz.exists()
    assert result.deleted == 2


def test_a_compressed_file_is_only_ever_deleted(tmp_path):
    gz = _gz(tmp_path / "logs/palli_2026-08-01.log._2026-08-29.gz", text="old day")
    before = gz.read_bytes()
    policy = [Policy("logs/palli_2*.log*", days=7, action="gzip"),
              Policy("logs/palli_2*.log*", days=0, action="trim", keep_bytes=4)]

    result = sweep(tmp_path, now=_at(2026, 9, 20), policy=policy, open_paths=())
    assert gz.read_bytes() == before                       # never re-zipped, never trimmed
    assert not Path(str(gz) + ".gz").exists()
    assert (result.gzipped, result.trimmed) == (0, 0)

    result = sweep(tmp_path, now=_at(2026, 10, 1), open_paths=(),
                   policy=[Policy("logs/palli_2*.log*", days=30, action="delete")])
    assert not gz.exists() and result.deleted == 1       # aged by 08-29, its last date


@pytest.mark.parametrize("where", ["root", "named"])
def test_an_open_log_file_is_never_touched(tmp_path, where):
    """The running process writes its start-dated file for its whole life, so
    by name it is days old (T5-1, HIGH). main.py hangs the main and verbose
    handlers on the root logger and the debug handler on named loggers only;
    both are found."""
    # Written to right now (its mtime), named for the day the process started.
    path = _touch(tmp_path / "logs/palli_verbose_2026-09-18.log", text="first line\n", mtime=time.time())
    if where == "root":
        handler = logging.handlers.TimedRotatingFileHandler(path, when="midnight", encoding="utf-8")
        logger = logging.getLogger()
    else:
        handler = logging.FileHandler(path, encoding="utf-8")
        logger = logging.getLogger("palli.test.retention.open_handler")
    handler.setLevel(logging.WARNING)
    logger.addHandler(handler)
    try:
        result = sweep(tmp_path, now=_at(2026, 9, 30), policy=[
            Policy("logs/palli_verbose_*", days=14, action="delete"),
            Policy("logs/palli_verbose_*", days=2, action="gzip"),
            Policy("logs/palli_verbose_*", days=0, action="trim", keep_bytes=3),
        ])
        assert path.exists() and not Path(str(path) + ".gz").exists()
        assert (result.deleted, result.gzipped, result.trimmed) == (0, 0, 0)
        assert result.held_open == 1
        logging.getLogger("palli.test.retention.open_handler").warning("still writing")
        handler.flush()
        assert path.read_text().startswith("first line\n") and "still writing" in path.read_text()
    finally:
        logger.removeHandler(handler)
        handler.close()


def test_the_open_files_are_every_file_handler_in_the_process(tmp_path):
    path = tmp_path / "logs/palli_debug_2026-09-18.log"
    path.parent.mkdir(parents=True)
    handler = logging.FileHandler(path, encoding="utf-8")
    logger = logging.getLogger("palli.test.retention.debug_twin")
    logger.addHandler(handler)
    try:
        assert os.path.realpath(path) in retention.open_log_paths()
    finally:
        logger.removeHandler(handler)
        handler.close()


# ── T5-6: her records, and anything outside the sweepable folders ───────────

def test_protected_is_her_five_record_folders_and_the_sweepable_roots_are_two():
    assert PROTECTED == ("data/agent_sessions", "data/present_moment", "data/inner_monologue",
                         "data/quiet_choices", "identity")
    assert retention.SWEEPABLE_ROOTS == ("logs", "data/forge/profiles")


def test_her_records_and_anything_outside_the_sweepable_folders_are_refused_and_counted(tmp_path):
    old = _at(2026, 1, 1)
    records = [
        _touch(tmp_path / "data/agent_sessions/discord_1.jsonl", mtime=old),
        _touch(tmp_path / "data/agent_sessions/s1/tool_results/out.txt", mtime=old),
        _touch(tmp_path / "data/present_moment/2026-01-01.jsonl", mtime=old),
        _touch(tmp_path / "data/inner_monologue/2026-01-01.jsonl", mtime=old),
        _touch(tmp_path / "data/quiet_choices/2026-01-01.jsonl", mtime=old),
        _touch(tmp_path / "identity/palli/MY_STORY.md", mtime=old),
    ]
    outside = [
        _touch(tmp_path / "data/memory_sweeps/2026-01-01.json", mtime=old),
        _touch(tmp_path / "data/night_cycle/.outcomes/2026-01-01.json", mtime=old),
        _touch(tmp_path / "data/inner_monologue.pre-2026-05-05-wipe.bak/2026-01-01.jsonl", mtime=old),
        _touch(tmp_path / "data/forge/tools/2026-01-01.py", mtime=old),
        _touch(tmp_path / "data/palli.yaml", mtime=old),
    ]
    before = _snapshot(tmp_path)
    policy = []
    for f in records + outside:
        rel = f.relative_to(tmp_path).as_posix()
        policy += [Policy(rel, days=1, action="delete"), Policy(rel, days=1, action="gzip"),
                   Policy(rel, days=0, action="trim", keep_bytes=0)]
    policy += [Policy("data/*", days=1, action="delete_dir"), Policy("*", days=1, action="delete_dir"),
               Policy("data/forge/profiles", days=1, action="delete_dir")]

    result = sweep(tmp_path, now=_at(2026, 9, 30), policy=policy, open_paths=())

    assert _snapshot(tmp_path) == before
    assert (result.deleted, result.gzipped, result.trimmed) == (0, 0, 0)
    assert result.refused >= len(records) + len(outside)


def test_her_records_hold_even_if_the_sweepable_folders_were_widened(tmp_path, monkeypatch):
    """Two guards, each enough: were the allow-list ever widened to all of
    data/ and identity/, PROTECTED alone still refuses her records."""
    monkeypatch.setattr(retention, "SWEEPABLE_ROOTS", ("logs", "data/forge/profiles", "data", "identity"))
    old = _at(2026, 1, 1)
    hers = [
        _touch(tmp_path / "data/agent_sessions/discord_1.jsonl", mtime=old),
        _touch(tmp_path / "data/present_moment/2026-01-01.jsonl", mtime=old),
        _touch(tmp_path / "data/inner_monologue/2026-01-01.jsonl", mtime=old),
        _touch(tmp_path / "data/quiet_choices/2026-01-01.jsonl", mtime=old),
        _touch(tmp_path / "identity/palli/MY_STORY.md", mtime=old),
    ]
    widened = _touch(tmp_path / "data/scratch/2026-01-01.txt", mtime=old)     # proves the widening took
    result = sweep(tmp_path, now=_at(2026, 9, 30), open_paths=(), policy=[
        Policy("data/**/*", days=1, action="delete"), Policy("identity/**/*", days=1, action="delete"),
        Policy("data/*", days=1, action="delete_dir"), Policy("identity/*", days=1, action="delete_dir"),
    ])
    assert all(p.exists() for p in hers)
    assert not widened.exists()
    assert result.refused >= len(hers)


def test_a_link_is_never_followed(tmp_path):
    hers = _touch(tmp_path / "data/agent_sessions/discord_1.jsonl", text="her words\n", mtime=_at(2026, 1, 1))
    logs = tmp_path / "logs"
    logs.mkdir()
    link = logs / "palli_2026-01-01.log"
    link.symlink_to(hers)
    os.utime(link, (_at(2026, 1, 1),) * 2, follow_symlinks=False)
    (logs / "palli_debug_2026-01-01.log.d").symlink_to(tmp_path / "data/agent_sessions", target_is_directory=True)
    (logs / "sub").symlink_to(tmp_path / "data/agent_sessions", target_is_directory=True)

    result = sweep(tmp_path, now=_at(2026, 9, 30), open_paths=(), policy=[
        Policy("logs/palli_2*.log*", days=1, action="gzip"),
        Policy("logs/palli_2*.log*", days=0, action="trim", keep_bytes=1),
        Policy("logs/palli_2*.log*", days=1, action="delete"),
        Policy("logs/palli_debug_*", days=1, action="delete_dir"),
        Policy("logs/sub/*", days=1, action="delete"),
        Policy("logs/sub/*", days=0, action="trim", keep_bytes=1),
    ])

    assert hers.read_text() == "her words\n"
    assert link.is_symlink() and (logs / "sub").is_symlink() and (logs / "palli_debug_2026-01-01.log.d").is_symlink()
    assert (result.deleted, result.gzipped, result.trimmed) == (0, 0, 0)
    assert result.refused == 3


def test_a_policy_that_reaches_outside_its_tree_is_refused_at_birth():
    with pytest.raises(ValueError):
        Policy("logs/*", days=1, action="shred")
    with pytest.raises(ValueError):
        Policy("/var/log/*", days=1, action="delete")
    with pytest.raises(ValueError):
        Policy("logs/../data/agent_sessions/*", days=1, action="delete")
    with pytest.raises(ValueError):
        Policy("logs/backup.log", days=0, action="trim")          # trim needs keep_bytes


# ── T5-2: delete_dir by the newest mtime inside; trim; gzip; mtime fallback ─

def _profile(root: Path, name: str, *, newest: float, old: float) -> Path:
    prof = root / "data/forge/profiles" / name
    _touch(prof / "Default/Cookies", mtime=old)
    _touch(prof / "Local State", mtime=old)
    deep = _touch(prof / "Default/Session Storage/000003.log", mtime=newest)
    for d in (prof / "Default/Session Storage", prof / "Default", prof):
        os.utime(d, (old, old))
    return prof


def test_a_profile_dir_is_deleted_whole_by_its_newest_mtime(tmp_path):
    now = _at(2026, 10, 1, 4)
    gone = _profile(tmp_path, "old_one", newest=now - 40 * DAY, old=now - 60 * DAY)
    (gone / "SingletonLock").symlink_to(tmp_path / "data/agent_sessions/discord_1.jsonl")
    os.utime(gone / "SingletonLock", (now - 60 * DAY,) * 2, follow_symlinks=False)
    os.utime(gone, (now - 60 * DAY,) * 2)            # the link's creation touched the folder
    hers = _touch(tmp_path / "data/agent_sessions/discord_1.jsonl", text="her words\n")
    kept = _profile(tmp_path, "browser_hand", newest=now - 1 * DAY, old=now - 60 * DAY)
    stray = _touch(tmp_path / "data/forge/profiles/stray.txt", mtime=now - 90 * DAY)

    result = sweep(tmp_path, now=now, open_paths=(),
                   policy=[Policy("data/forge/profiles/*", days=30, action="delete_dir")])

    assert not gone.exists()
    assert hers.read_text() == "her words\n"                     # the link inside went, not its target
    assert (kept / "Default/Cookies").exists() and (kept / "Local State").exists()   # never file by file
    assert stray.exists()                                        # delete_dir never takes a file
    assert result.deleted == 1 and result.freed_bytes > 0


def test_trim_keeps_the_last_n_bytes_from_a_line_start(tmp_path):
    lines = [f"line {i:04d}\n" for i in range(1000)]            # ten bytes a line
    big = _touch(tmp_path / "logs/backup.log", text="".join(lines))
    small = _touch(tmp_path / "logs/bounce.log", text="short\n", mtime=_at(2026, 9, 1))

    result = sweep(tmp_path, now=_at(2026, 9, 30), open_paths=(), policy=[
        Policy("logs/backup.log", days=0, action="trim", keep_bytes=1005),
        Policy("logs/bounce.log", days=0, action="trim", keep_bytes=1005),
    ])

    assert big.read_text() == "".join(lines[-100:])              # 1,000 bytes, a whole first line
    assert small.read_text() == "short\n" and small.stat().st_mtime == _at(2026, 9, 1)
    assert result.trimmed == 1 and result.freed_bytes == 9000


def test_trim_on_an_exact_line_boundary_keeps_every_byte_asked_for(tmp_path):
    lines = [f"line {i:04d}\n" for i in range(1000)]
    big = _touch(tmp_path / "logs/backup.log", text="".join(lines))
    sweep(tmp_path, now=NOW, open_paths=(),
          policy=[Policy("logs/backup.log", days=0, action="trim", keep_bytes=1000)])
    assert big.read_text() == "".join(lines[-100:])


def test_gzip_keeps_the_mtime_and_happens_once(tmp_path):
    f = _touch(tmp_path / "logs/palli_debug_2026-09-18.log._2026-09-02", text="abc\n", mtime=_at(2026, 9, 3, 0))
    os.chmod(f, 0o640)
    policy = [Policy("logs/palli_debug_*", days=2, action="gzip")]

    first = sweep(tmp_path, now=_at(2026, 9, 30), policy=policy, open_paths=())
    gz = Path(str(f) + ".gz")
    assert not f.exists() and gzip.open(gz, "rt").read() == "abc\n"
    assert gz.stat().st_mtime == _at(2026, 9, 3, 0)               # the dashboards pick by mtime
    assert gz.stat().st_mode & 0o777 == 0o640
    assert [p.name for p in (tmp_path / "logs").iterdir()] == [gz.name]   # no partial left behind
    assert first.gzipped == 1

    again = sweep(tmp_path, now=_at(2026, 9, 30), policy=policy, open_paths=())
    assert again.gzipped == 0 and [p.name for p in (tmp_path / "logs").iterdir()] == [gz.name]


# ── T5-1a: a base file holds its process's LAST day ─────────────────────────
# A base log (palli_verbose_<start>.log) carries only its start date, but once
# the process is gone it holds that process's last day — the lines before a
# crash or restart. Aged by names only: no older than the day after its newest
# rotated sibling (<base>._<D> or <base>._<D>.gz); no siblings, its own date.

def _crashed_process(logs: Path, *, start=(2026, 9, 10), last_chunk=(2026, 9, 24)) -> Path:
    """A process started on ``start`` whose midnight rotations ran up to
    ``last_chunk``; it died the next morning. mtimes are 'now' on purpose
    (a scrub reset them) — they never decide."""
    y, m, d = start
    base = logs / f"palli_verbose_{y:04d}-{m:02d}-{d:02d}.log"
    day = time.mktime((y, m, d, 12, 0, 0, 0, 0, -1))
    end = time.mktime((*last_chunk, 12, 0, 0, 0, 0, -1))
    while day <= end + 1:
        stamp = time.strftime("%Y-%m-%d", time.localtime(day))
        _touch(logs / f"{base.name}._{stamp}", text=f"lines of {stamp}\n")
        day += DAY
    return _touch(base, text="the lines before the crash\n")


def test_a_crashed_processs_base_log_is_aged_by_the_day_after_its_newest_chunk(tmp_path):
    """The reviewer's repro: started 09-10, crashed 09-25 08:55, booted again —
    the boot sweep must keep the crash lead-up, not delete it first."""
    base = _crashed_process(tmp_path / "logs")
    result = sweep(tmp_path, now=_at(2026, 9, 25, 9), open_paths=(),
                   policy=retention.policies_from_config(RetentionConfig()))
    assert base.read_text() == "the lines before the crash\n"          # derived day 09-25: fresh
    assert not (tmp_path / "logs/palli_verbose_2026-09-10.log._2026-09-10").exists()   # 15 days: gone
    assert (tmp_path / "logs/palli_verbose_2026-09-10.log._2026-09-24").exists()       # yesterday: plain
    assert Path(str(tmp_path / "logs/palli_verbose_2026-09-10.log._2026-09-20") + ".gz").exists()
    assert result.errors == 0


def test_a_base_log_is_zipped_by_its_derived_age_and_deleted_only_when_that_passes(tmp_path):
    logs = tmp_path / "logs"
    base = _crashed_process(logs)
    policy = retention.policies_from_config(RetentionConfig())
    zipped = Path(str(base) + ".gz")

    sweep(tmp_path, now=_at(2026, 10, 5), open_paths=(), policy=policy)      # derived 09-25: 10 days
    assert not base.exists() and gzip.open(zipped, "rt").read() == "the lines before the crash\n"

    sweep(tmp_path, now=_at(2026, 10, 8), open_paths=(), policy=policy)      # 13 days: kept, though its
    assert zipped.exists()                                                     # last siblings go tonight

    sweep(tmp_path, now=_at(2026, 10, 10), open_paths=(), policy=policy)     # past 14 days by any reading
    assert not zipped.exists()
    assert list(logs.iterdir()) == []


def test_a_base_log_with_no_siblings_keeps_its_own_date(tmp_path):
    lone = _touch(tmp_path / "logs/palli_verbose_2026-09-10.log", mtime=_at(2026, 9, 30))
    other_base_chunk = _touch(tmp_path / "logs/palli_verbose_2026-09-11.log._2026-09-29")  # not its sibling
    sweep(tmp_path, now=_at(2026, 9, 30), open_paths=(),
          policy=[Policy("logs/palli_verbose_*", days=14, action="delete")])
    assert not lone.exists()                                       # 20 days by its own name; mtime never saves it
    assert other_base_chunk.exists()


def test_a_main_log_base_is_aged_the_same_way(tmp_path):
    logs = tmp_path / "logs"
    base = _touch(logs / "palli_2026-08-01.log", text="last day\n")
    _gz(logs / "palli_2026-08-01.log._2026-09-20.gz")             # its newest chunk, already zipped
    sweep(tmp_path, now=_at(2026, 9, 25), open_paths=(),
          policy=[Policy("logs/palli_2*.log*", days=30, action="delete"),
                  Policy("logs/palli_2*.log*", days=7, action="gzip")])
    assert base.read_text() == "last day\n"                         # derived 09-21: 4 days, not 55


def test_a_name_with_no_dashed_date_is_aged_by_its_mtime(tmp_path):
    """Voice recordings carry a compact stamp (%Y%m%d-%H%M%S) — mtime it is."""
    rec = tmp_path / "logs/voice-debug-recordings"
    old_wav = _touch(rec / "sam_123_20260810-210925.wav", mtime=_at(2026, 8, 10))
    old_json = _touch(rec / "sam_123_20260810-210925.json", mtime=_at(2026, 8, 10))
    new_wav = _touch(rec / "sam_123_20260928-101010.wav", mtime=_at(2026, 9, 28))

    result = sweep(tmp_path, now=_at(2026, 9, 30), open_paths=(),
                   policy=[Policy("logs/voice-debug-recordings/*", days=14, action="delete")])

    assert not old_wav.exists() and not old_json.exists() and new_wav.exists()
    assert result.deleted == 2


def test_a_stop_request_ends_the_sweep_between_files(tmp_path):
    for day in range(1, 6):
        _touch(tmp_path / f"logs/palli_2026-08-0{day}.log")
    asks = []

    def stop():
        asks.append(1)
        return len(asks) > 2

    result = sweep(tmp_path, now=_at(2026, 9, 30), open_paths=(), should_stop=stop,
                   policy=[Policy("logs/palli_2*.log*", days=30, action="delete")])
    assert result.deleted == 2
    assert len(list((tmp_path / "logs").iterdir())) == 3


# ── the owner's limits (B8-4) as the default policy (T5-3) ──────────────────

def test_the_owners_limits_are_the_settings_defaults_and_ship_dark():
    cfg = RetentionConfig()
    assert cfg.enabled is False
    assert retention.policies_from_config(cfg) == [
        Policy("logs/palli_2*.log*", days=30, action="delete"),
        Policy("logs/palli_2*.log*", days=7, action="gzip"),
        Policy("logs/palli_verbose_*", days=14, action="delete"),
        Policy("logs/palli_verbose_*", days=2, action="gzip"),
        Policy("logs/palli_debug_*", days=14, action="delete"),
        Policy("logs/palli_debug_*", days=2, action="gzip"),
        Policy("logs/backup.log", days=0, action="trim", keep_bytes=5 * MB),
        Policy("logs/bounce.log", days=0, action="trim", keep_bytes=5 * MB),
        Policy("data/forge/profiles/*", days=30, action="delete_dir"),
        Policy("logs/voice-debug-recordings/*", days=14, action="delete"),
    ]


def test_a_zero_turns_that_one_limit_off():
    policy = retention.policies_from_config(RetentionConfig(verbose_log_gzip_days=0, trimmed_log_mb=0,
                                                            forge_profile_days=0))
    assert Policy("logs/palli_verbose_*", days=2, action="gzip") not in policy
    assert Policy("logs/palli_verbose_*", days=14, action="delete") in policy
    assert not [p for p in policy if p.action in ("trim", "delete_dir")]


def test_the_settings_refuse_negatives():
    with pytest.raises(Exception):
        RetentionConfig(main_log_days=-1)


def test_the_owners_policy_on_a_tree_like_hers(tmp_path):
    """The whole default policy at 04:00 on 10-01, on a tree shaped like her
    logs/ and data/: what goes, what is zipped, what stays."""
    now = _at(2026, 10, 1, 4)
    L = tmp_path / "logs"
    open_main = _touch(L / "palli_2026-09-18.log", mtime=now)                  # this process, by name 13 days old
    open_verbose = _touch(L / "palli_verbose_2026-09-18.log", mtime=now)
    main_gone = _touch(L / "palli_2026-08-20.log", mtime=_at(2026, 8, 21))
    main_gz_gone = _gz(L / "palli_2026-08-01.log._2026-08-29.gz")
    main_zip = _touch(L / "palli_2026-09-18.log._2026-09-20", text="main 09-20\n")
    main_plain = _touch(L / "palli_2026-09-18.log._2026-09-30")
    verbose_gone = _touch(L / "palli_verbose_2026-09-10.log._2026-09-15")
    verbose_zip = _touch(L / "palli_verbose_2026-09-18.log._2026-09-29", text="sent 200\n")
    verbose_plain = _touch(L / "palli_verbose_2026-09-18.log._2026-09-30")
    debug_gz_gone = _gz(L / "palli_debug_2026-09-18.log._2026-09-16.gz")
    debug_zip = _touch(L / "palli_debug_2026-09-18.log._2026-09-25")
    backup = _touch(L / "backup.log", text="b" * (6 * MB - 1) + "\n")
    bounce = _touch(L / "bounce.log", text="bounced\n")
    launcher = _touch(L / "launcher/launcher.log", mtime=_at(2026, 1, 1))
    rec_gone = _touch(L / "voice-debug-recordings/sam_1_20260810-210925.wav", mtime=_at(2026, 8, 10))
    profile = _profile(tmp_path, "browser_hand", newest=_at(2026, 8, 25), old=_at(2026, 8, 1))
    hers = [
        _touch(tmp_path / "data/agent_sessions/discord_1.jsonl", mtime=_at(2026, 5, 1)),
        _touch(tmp_path / "data/inner_monologue/2026-05-05.jsonl", mtime=_at(2026, 5, 5)),
        _touch(tmp_path / "data/present_moment/2026-05-05.jsonl", mtime=_at(2026, 5, 5)),
        _touch(tmp_path / "data/quiet_choices/2026-09-01.jsonl", mtime=_at(2026, 9, 1)),
        _touch(tmp_path / "identity/palli/MY_STORY.md", mtime=_at(2026, 1, 1)),
        _touch(tmp_path / "data/day_gists/2026-05-01.md", mtime=_at(2026, 5, 1)),
    ]
    hers_before = {p: p.read_bytes() for p in hers}

    result = sweep(tmp_path, now=now, policy=retention.policies_from_config(RetentionConfig()),
                   open_paths={str(open_main), str(open_verbose)})

    for gone in (main_gone, main_gz_gone, verbose_gone, debug_gz_gone, rec_gone, profile):
        assert not gone.exists(), gone
    for zipped, text in ((main_zip, "main 09-20\n"), (verbose_zip, "sent 200\n"), (debug_zip, "x")):
        assert not zipped.exists() and gzip.open(str(zipped) + ".gz", "rt").read() == text
    for stays in (open_main, open_verbose, main_plain, verbose_plain, bounce, launcher):
        assert stays.exists() and not Path(str(stays) + ".gz").exists(), stays
    assert backup.stat().st_size <= 5 * MB
    assert {p: p.read_bytes() for p in hers} == hers_before
    assert (result.deleted, result.gzipped, result.trimmed, result.refused, result.held_open, result.errors) \
        == (6, 3, 1, 0, 2, 0)


# ── the night's step (T3-3, T5-4) ───────────────────────────────────────────

def test_the_night_step_and_the_root_are_pinned_in_tests():
    """G3/T5-4: seven night-cycle tests run _run_once for real; the sweep
    step is a no-op for every test, and any other path to the sweep finds
    a tmp tree, never the checkout's logs/."""
    assert retention._sweep_logs is not _REAL_SWEEP_STEP
    assert Path(retention.REPO_ROOT) != _REAL_REPO_ROOT
    assert _REAL_REPO_ROOT == Path(retention.__file__).resolve().parent.parent


def _on(**kw):
    return SimpleNamespace(retention=RetentionConfig(enabled=True, **kw))


@pytest.mark.asyncio
async def test_the_night_sweeps_with_the_owners_limits_when_retention_is_on(monkeypatch, tmp_path):
    monkeypatch.setattr(retention, "_sweep_logs", _REAL_SWEEP_STEP)
    monkeypatch.setattr(retention, "REPO_ROOT", tmp_path)
    gone = _touch(tmp_path / "logs/palli_debug_2026-09-18.log._2026-09-10")
    zipped = _touch(tmp_path / "logs/palli_debug_2026-09-18.log._2026-09-25")
    threads = []
    real = retention._sweep_once

    def spy(**kw):
        threads.append(threading.current_thread())
        return real(**kw)

    monkeypatch.setattr(retention, "_sweep_once", spy)

    out = await retention.run_nightly_housekeeping(now=_at(2026, 9, 30), settings=_on())

    assert not gone.exists() and not zipped.exists() and Path(str(zipped) + ".gz").exists()
    sweep_out = out["retention"]
    assert {k: sweep_out[k] for k in ("deleted", "gzipped", "trimmed", "refused", "held_open", "errors")} \
        == {"deleted": 1, "gzipped": 1, "trimmed": 0, "refused": 0, "held_open": 0, "errors": 0}
    assert "error" not in sweep_out
    assert threads and threads[0] is not threading.main_thread()          # off the event loop
    assert "history_prune" in out                                            # task 3's step still runs


@pytest.mark.asyncio
async def test_the_night_leaves_the_logs_alone_while_retention_is_off(monkeypatch, tmp_path):
    monkeypatch.setattr(retention, "_sweep_logs", _REAL_SWEEP_STEP)
    monkeypatch.setattr(retention, "REPO_ROOT", tmp_path)
    old = _touch(tmp_path / "logs/palli_debug_2026-01-01.log")
    for settings in (SimpleNamespace(retention=RetentionConfig()), SimpleNamespace()):
        out = await retention.run_nightly_housekeeping(now=_at(2026, 9, 30), settings=settings)
        assert out["retention"] == {"skipped": "flag_off"}
    assert old.exists()


@pytest.mark.asyncio
async def test_a_sweep_that_breaks_is_an_error_in_the_record_never_a_raise(monkeypatch, tmp_path):
    monkeypatch.setattr(retention, "_sweep_logs", _REAL_SWEEP_STEP)
    monkeypatch.setattr(retention, "REPO_ROOT", tmp_path)

    def boom(*_a, **_kw):
        raise PermissionError("logs is read-only")

    monkeypatch.setattr(retention, "sweep", boom)
    out = await retention.run_nightly_housekeeping(now=_at(2026, 9, 30), settings=_on())
    assert out["retention"] == {"error": "logs is read-only"}


@pytest.mark.asyncio
async def test_a_file_that_cannot_be_removed_is_counted_and_named_and_the_rest_still_go(monkeypatch, tmp_path):
    monkeypatch.setattr(retention, "_sweep_logs", _REAL_SWEEP_STEP)
    monkeypatch.setattr(retention, "REPO_ROOT", tmp_path)
    stuck = _touch(tmp_path / "logs/palli_debug_2026-09-18.log._2026-09-01")
    goes = _touch(tmp_path / "logs/palli_debug_2026-09-18.log._2026-09-02")
    real_delete = retention._act_delete

    def picky(path):
        if Path(path).name == stuck.name:
            raise PermissionError("Operation not permitted")
        return real_delete(path)

    monkeypatch.setattr(retention, "_act_delete", picky)
    out = await retention.run_nightly_housekeeping(now=_at(2026, 9, 30), settings=_on(debug_log_gzip_days=0))

    assert stuck.exists() and not goes.exists()
    assert out["retention"]["errors"] == 1 and out["retention"]["deleted"] == 1
    assert "Operation not permitted" in out["retention"]["error"]


def test_two_sweeps_never_run_at_once(tmp_path):
    assert retention._SWEEP_LOCK.acquire(blocking=False)
    try:
        assert retention._sweep_once(now=NOW, cfg=RetentionConfig(enabled=True), root=tmp_path) \
            == {"skipped": "already_running"}
    finally:
        retention._SWEEP_LOCK.release()


# ── the boot sweep: after settings boot, off the loop (T5-3) ────────────────

@pytest.mark.asyncio
async def test_the_boot_sweep_runs_off_the_loop_when_retention_is_on(monkeypatch, tmp_path):
    monkeypatch.setattr(retention, "REPO_ROOT", tmp_path)
    old = _touch(tmp_path / "logs/palli_verbose_2026-09-01.log._2026-09-02")
    threads = []
    real = retention._sweep_once

    def spy(**kw):
        threads.append(threading.current_thread())
        return real(**kw)

    monkeypatch.setattr(retention, "_sweep_once", spy)
    task = retention.start_boot_sweep(settings=_on(), now=_at(2026, 9, 30))
    assert isinstance(task, asyncio.Task)
    out = await task
    assert not old.exists() and out["deleted"] == 1
    assert threads and threads[0] is not threading.main_thread()


def test_the_boot_sweep_is_nothing_while_retention_is_off():
    assert retention.start_boot_sweep(settings=SimpleNamespace(retention=RetentionConfig())) is None
    assert retention.start_boot_sweep(settings=SimpleNamespace()) is None


@pytest.mark.asyncio
async def test_a_cancelled_boot_sweep_asks_the_sweep_to_stop(monkeypatch, tmp_path):
    monkeypatch.setattr(retention, "REPO_ROOT", tmp_path)
    started, stopped = threading.Event(), threading.Event()

    def slow_sweep(root, *, now, policy, open_paths=None, should_stop=None):
        started.set()
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if should_stop and should_stop():
                stopped.set()
                break
            time.sleep(0.01)
        return retention.SweepResult()

    monkeypatch.setattr(retention, "sweep", slow_sweep)
    task = retention.start_boot_sweep(settings=_on(), now=NOW)
    assert await asyncio.to_thread(started.wait, 5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert await asyncio.to_thread(stopped.wait, 5)


@pytest.mark.asyncio
async def test_the_launcher_starts_the_boot_sweep_after_settings_boot_and_the_preflight(monkeypatch):
    import core.launcher_runtime as lr
    import core.settings_boot as sb

    order = []
    monkeypatch.setattr(sb, "boot_settings", lambda *a, **k: order.append("settings"))
    monkeypatch.setattr(lr, "run_backend_startup_preflight", lambda **kw: order.append("preflight") or True)
    monkeypatch.setattr(retention, "start_boot_sweep", lambda **kw: order.append("sweep"))
    launcher = lr.PalliLauncher(
        config_obj=SimpleNamespace(validate=lambda: True),
        plugin_runtime_obj=SimpleNamespace(prepare_startup_catalog=lambda force=False: [],
                                           get_startup_channel_integrations=lambda: []),
        cleanup_runner=lambda: asyncio.sleep(0),
    )
    monkeypatch.setattr(launcher, "_setup_signal_handlers", lambda: order.append("signals"))
    monkeypatch.setattr(launcher, "_build_component_tasks", lambda: order.append("tasks") or [])

    assert await launcher.start() is False                  # no components: the sweep still began first
    assert order == ["settings", "preflight", "signals", "sweep", "tasks"]


@pytest.mark.asyncio
async def test_the_launcher_starts_no_sweep_when_the_preflight_says_no(monkeypatch):
    import core.launcher_runtime as lr
    import core.settings_boot as sb

    order = []
    monkeypatch.setattr(sb, "boot_settings", lambda *a, **k: order.append("settings"))
    monkeypatch.setattr(lr, "run_backend_startup_preflight", lambda **kw: order.append("preflight") or False)
    monkeypatch.setattr(retention, "start_boot_sweep", lambda **kw: order.append("sweep"))
    launcher = lr.PalliLauncher(
        config_obj=SimpleNamespace(validate=lambda: False),
        plugin_runtime_obj=SimpleNamespace(prepare_startup_catalog=lambda force=False: [],
                                           get_startup_channel_integrations=lambda: []),
        cleanup_runner=lambda: asyncio.sleep(0),
    )
    assert await launcher.start() is False
    assert order == ["settings", "preflight"]


@pytest.mark.asyncio
async def test_a_boot_sweep_that_will_not_start_never_stops_her_boot(monkeypatch):
    import core.launcher_runtime as lr
    import core.settings_boot as sb

    def broken(**_kw):
        raise RuntimeError("no loop for you")

    monkeypatch.setattr(sb, "boot_settings", lambda *a, **k: None)
    monkeypatch.setattr(lr, "run_backend_startup_preflight", lambda **kw: True)
    monkeypatch.setattr(retention, "start_boot_sweep", broken)
    launcher = lr.PalliLauncher(
        config_obj=SimpleNamespace(validate=lambda: True),
        plugin_runtime_obj=SimpleNamespace(prepare_startup_catalog=lambda force=False: [],
                                           get_startup_channel_integrations=lambda: []),
        cleanup_runner=lambda: asyncio.sleep(0),
    )
    built = []
    monkeypatch.setattr(launcher, "_setup_signal_handlers", lambda: None)
    monkeypatch.setattr(launcher, "_build_component_tasks", lambda: built.append(1) or [])
    await launcher.start()
    assert built == [1]


# ── before settings boot: main.py's old mtime sweep (law 4, T5-1/T5-3) ──────

def _settings_file(path: Path, **blocks) -> Path:
    """A whole settings file the boot would accept (the tracked example),
    with ``blocks`` merged over its top-level sections."""
    import yaml

    raw = yaml.safe_load((_REAL_REPO_ROOT / "data" / "palli.example.yaml").read_text(encoding="utf-8"))
    for name, block in blocks.items():
        if block is None:
            raw.pop(name, None)
        else:
            raw[name] = {**(raw.get(name) or {}), **block}
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    return path


def test_the_settings_file_says_whether_retention_is_on(tmp_path, monkeypatch):
    on = _settings_file(tmp_path / "on.yaml", retention={"enabled": True})
    off = _settings_file(tmp_path / "off.yaml", retention={"enabled": False})
    absent = _settings_file(tmp_path / "absent.yaml", retention=None)
    broken = tmp_path / "broken.yaml"
    broken.write_text("retention: [::\n")
    wrong = _settings_file(tmp_path / "wrong.yaml", retention={"enabled": True, "main_log_days": -3})
    # Final fix wave (ruling FW-9): the whole file is validated, as the boot
    # validates it — a bad block anywhere means the boot falls back to off,
    # and the peek must agree, or neither sweep runs.
    elsewhere = _settings_file(tmp_path / "elsewhere.yaml", retention={"enabled": True},
                               companion={"room_wait_seconds": 1})
    only_the_block = tmp_path / "only_the_block.yaml"
    only_the_block.write_text("version: 1\nretention:\n  enabled: true\n")

    assert retention.enabled_in_settings_file(on) is True
    for path in (off, absent, broken, wrong, elsewhere, only_the_block,
                 tmp_path / "missing.yaml"):
        assert retention.enabled_in_settings_file(path) is False, path

    import core.settings_boot as sb
    monkeypatch.setattr(sb.settings_service, "_yaml_path", on)
    assert retention.enabled_in_settings_file() is True       # the file this process boots from


@pytest.fixture
def isolated_logging(monkeypatch, tmp_path):
    """main.setup_logging() rewires the root logger; give it a tmp cwd and put
    every logger it touches back afterwards."""
    import main as main_module

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(main_module.config, "ENABLE_DEBUG_LOGGING", False, raising=False)
    monkeypatch.setattr(main_module.config, "LOG_FULL_DISCORD_MESSAGES", False, raising=False)
    root = logging.getLogger()
    saved_root = (root.handlers[:], root.level, root.filters[:])
    names = ("aiosqlite", "discord.ext.voice_recv.reader", "palli.debug.llm", "palli.debug.proactive",
             "palli.ideation", "palli.debug.discord", "uvicorn.access", "uvicorn.error")
    saved = {n: (logging.getLogger(n).level, logging.getLogger(n).filters[:],
                 logging.getLogger(n).handlers[:], logging.getLogger(n).propagate) for n in names}
    try:
        yield main_module
    finally:
        for h in root.handlers:
            if h not in saved_root[0]:
                h.close()
        root.handlers[:] = saved_root[0]
        root.setLevel(saved_root[1])
        root.filters[:] = saved_root[2]
        for n, (level, filters, handlers, propagate) in saved.items():
            lg = logging.getLogger(n)
            for h in lg.handlers:
                if h not in handlers:
                    h.close()
            lg.setLevel(level); lg.filters[:] = filters; lg.handlers[:] = handlers; lg.propagate = propagate


def _boot_tree(tmp_path):
    t = time.time()
    return {
        "old_verbose": _touch(tmp_path / "logs/palli_verbose_2026-01-01.log", mtime=t - 6 * DAY),
        "young_verbose": _touch(tmp_path / "logs/palli_verbose_2020-01-02.log", mtime=t - 4 * DAY),
        "old_debug": _touch(tmp_path / "logs/palli_debug_2026-01-01.log", mtime=t - 15 * DAY),
        "young_debug": _touch(tmp_path / "logs/palli_debug_2020-01-02.log", mtime=t - 13 * DAY),
        "old_main": _touch(tmp_path / "logs/palli_2020-01-01.log", mtime=t - 400 * DAY),
    }


def test_with_retention_off_the_boot_mtime_sweep_runs_exactly_as_before(isolated_logging, monkeypatch, tmp_path):
    """Law 4: flag off is today's behaviour — verbose over 5 days and debug
    over 14 by mtime go at boot; main logs are never swept."""
    monkeypatch.setattr(retention, "enabled_in_settings_file", lambda *a, **k: False)
    tree = _boot_tree(tmp_path)
    isolated_logging.setup_logging()
    assert not tree["old_verbose"].exists() and not tree["old_debug"].exists()
    assert tree["young_verbose"].exists() and tree["young_debug"].exists() and tree["old_main"].exists()


def test_with_retention_on_the_old_mtime_sweep_stands_down(isolated_logging, monkeypatch, tmp_path):
    """On, the sweep by the date in each name owns the limits (verbose 14
    days, not 5 by mtime) and runs after settings boot instead."""
    monkeypatch.setattr(retention, "enabled_in_settings_file", lambda *a, **k: True)
    tree = _boot_tree(tmp_path)
    isolated_logging.setup_logging()
    assert all(p.exists() for p in tree.values())


# ── what a reply used (R15, memory that reaches, T13) ───────────────────────
# Its day files (data/reply_sources/, core/reply_sources.py) hold the owner's
# private lines, so the night removes each once it is more than fourteen days
# old, by the date in its name — whether or not the switch is still on: a
# record written while it was on still goes. Never written: no step, no key.

@pytest.mark.asyncio
async def test_the_night_prunes_reply_sources_older_than_fourteen_days(monkeypatch, tmp_path):
    folder = tmp_path / "reply_sources"
    monkeypatch.setenv("PALLI_REPLY_SOURCES_DIR", str(folder))
    out = await retention.run_nightly_housekeeping(now=_at(2026, 10, 13), settings=SimpleNamespace())
    assert "reply_sources_prune" not in out
    old = _touch(folder / "2026-09-28.jsonl")
    kept = [_touch(folder / f"{d}.jsonl") for d in ("2026-09-29", "2026-10-13")]
    out = await retention.run_nightly_housekeeping(now=_at(2026, 10, 13), settings=SimpleNamespace())
    assert out["reply_sources_prune"] == {"removed": 1}
    assert not old.exists() and all(p.exists() for p in kept)
    assert {"history_prune", "retention"} <= set(out)          # the other steps still run


@pytest.mark.asyncio
async def test_a_broken_reply_sources_prune_is_an_error_in_the_record_never_a_raise(monkeypatch, tmp_path):
    from core import reply_sources

    folder = tmp_path / "reply_sources"
    folder.mkdir()
    monkeypatch.setenv("PALLI_REPLY_SOURCES_DIR", str(folder))

    def boom(**_kw):
        raise OSError("disk went away")

    monkeypatch.setattr(reply_sources, "prune", boom)
    out = await retention.run_nightly_housekeeping(now=_at(2026, 10, 13), settings=SimpleNamespace())
    assert out["reply_sources_prune"] == {"error": "disk went away"}
    assert {"history_prune", "retention"} <= set(out)


def test_a_failed_reply_sources_prune_speaks_in_the_nights_health_line():
    from ai.night_cycle import _surface_errors

    assert _surface_errors({"reply_sources_prune": {"removed": 1, "error": "1 could not be removed"}}) == (
        "reply_sources_prune: 1 could not be removed")
    assert _surface_errors({"reply_sources_prune": {"removed": 3}}) == ""
