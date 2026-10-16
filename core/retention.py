"""The night's housekeeping (Batch 8): one entry point the night cycle calls
(``ai/night_cycle.py``, right after the memory sweep), so the night gains one
import however many housekeeping steps join it (ruling T3-3; the night is at
its coupling budget).

Each step is its own key in what this returns, and the night merges them
into its result, so each lands in the night's record (``data/night_cycle/
.outcomes/<night>.json``) as a step of its own: an outcome, a skip reason or
an ``error`` — read by the health check's rest-of-the-night line. A step that
breaks returns its error and never costs another step or the night.

Steps today:

- ``history_prune`` (task 3): old copies of her identity documents go; the
  newest ten, one a day for a month, one a week before that and the first
  ever stay (``core/identity/history_prune.py``). Behind
  ``memory.history_keep_last`` (0 = off).
- ``retention`` (task 5): limits on logs and throwaway folders, by the date
  in each file's own name (``sweep`` below, the owner's limits in the
  ``retention:`` settings block). Behind ``retention.enabled`` (off = today:
  only ``main.py``'s boot-time sweep by mtime). The same sweep also runs
  once at boot, after settings boot and off the event loop
  (``start_boot_sweep``, called by the launcher).
- ``today_prune`` (R15, memory that reaches, T12; fix round 1): the today
  slot's rows the night retired are deleted once they are
  ``TODAY_RETIRED_KEEP_DAYS`` old. Behind the slot's own switch
  (``companion.memory_reach``; off = no step, no key).
- ``reply_sources_prune`` (R15, T13): the record of what each reply used
  (``data/reply_sources/``, the owner's private lines) loses each day's file
  once it is more than fourteen days old (``core.reply_sources.prune``).
  Whenever the record exists, switch on or off; never written = no step,
  no key.

Her records — her conversations, thoughts and moments, and the first version
of every identity document — are never removed here: the sweep refuses any
path under ``PROTECTED`` and any path outside ``SWEEPABLE_ROOTS``, whatever a
policy's glob says.
"""
from __future__ import annotations

import asyncio
import gzip
import logging
import os
import re
import shutil
import stat
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional

logger = logging.getLogger(__name__)

# The checkout the sweep works in (main.py writes logs/ relative to the
# process's working directory, which the launcher sets to this root).
# Pinned to a tmp tree for every test (tests/conftest.py, ruling T5-4).
REPO_ROOT = Path(__file__).resolve().parent.parent

# Her records (Global Constraints; plan Review Focus 5): her conversations
# and tool results, her moments, her thoughts, her record of quiet choices and
# her identity tree. A sweep refuses any path under these, whatever a policy
# says. Matched as a plain string prefix on purpose, so a sibling such as
# data/inner_monologue.pre-2026-05-05-wipe.bak is covered too.
PROTECTED = (
    "data/agent_sessions",
    "data/present_moment",
    "data/inner_monologue",
    "data/quiet_choices",
    "identity",
)

# The only folders a sweep may act inside (ruling T5-6): the stronger guard,
# since at least twenty other data/ folders hold her records too. A target
# must sit strictly inside one of these, by its lexical path and its real one.
SWEEPABLE_ROOTS = ("logs", "data/forge/profiles")

ACTIONS = ("delete", "gzip", "trim", "delete_dir")

_DAY = 86400.0
_MB = 1024 * 1024
# A dashed date anywhere in a name. The LAST one is the file's day (ruling
# T5-1): a rotated chunk is <base named for the process start>._<its day>.
_NAMED_DATE = re.compile(r"(?<!\d)(\d{4})-(\d{2})-(\d{2})(?!\d)")
# A midnight-rotated chunk of a base log: <base>._<YYYY-MM-DD>, maybe zipped.
_ROTATED_CHUNK = re.compile(r"^(?P<base>.+)\._(?P<y>\d{4})-(?P<m>\d{2})-(?P<d>\d{2})(?:\.gz)?$")


def _settings_snapshot() -> Any:
    try:
        from core.settings_boot import settings_service

        return settings_service.snapshot()
    except Exception:
        return None


async def _prune_identity_history(*, now: float, settings: Any) -> Dict[str, Any]:
    """The identity-history prune on her database. Pinned to a no-op for
    every test (tests/conftest.py, ruling G3); its tests put it back."""
    try:
        from core.identity import history_prune

        memory = getattr(settings, "memory", None) if settings is not None else None
        keep_last, daily_days = history_prune.history_prune_settings(memory)
        if keep_last <= 0:
            return {"skipped": "flag_off"}
        from core.database import brain

        result = await history_prune.prune_history(
            brain, keep_last=keep_last, daily_days=daily_days, now=now
        )
        return {"kept": result.kept, "removed": result.removed, "bytes": result.bytes}
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.warning("housekeeping: the identity-history prune failed: %s", exc or type(exc).__name__)
        return {"error": str(exc) or type(exc).__name__}


# Retired today-slot rows are kept this long, then the housekeeping deletes
# them (R15, T12; fix round 1).
TODAY_RETIRED_KEEP_DAYS = 3


async def _prune_retired_today(*, now: float) -> Dict[str, Any]:
    """The today slot's retired rows, deleted once ``TODAY_RETIRED_KEEP_DAYS``
    old (the night retires them; a few days on they have nothing left to
    tell). Only rows already retired — never an active one, never another
    type."""
    try:
        from core.database import brain

        removed = await brain.prune_retired_today_facts(
            before_ts=float(now) - TODAY_RETIRED_KEEP_DAYS * 86400.0
        )
        return {"removed": int(removed or 0)}
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.warning("housekeeping: the today slot's prune failed: %s", exc or type(exc).__name__)
        return {"error": str(exc) or type(exc).__name__}


async def _prune_reply_sources(*, now: float) -> Dict[str, Any]:
    """The record of what each reply used (R15, T13): a day's file is
    removed once it is more than ``core.reply_sources.KEEP_DAYS`` days old,
    by the date in its name (``reply_sources.prune``: only its own day
    files, never a link)."""
    try:
        from core import reply_sources

        return reply_sources.prune(now=now)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.warning("housekeeping: the reply-sources prune failed: %s", type(exc).__name__)
        return {"error": str(exc) or type(exc).__name__}


# ── the sweep ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Policy:
    """One limit: files (or, for ``delete_dir``, folders) matching ``glob``
    under the sweep's root, older than ``days``, get ``action``.

    - ``delete``: the file goes.
    - ``gzip``: the file becomes ``<name>.gz`` (same mtime and mode) and the
      plain file goes. A ``.gz`` is never zipped again.
    - ``trim``: the file keeps only its last ``keep_bytes`` bytes, from the
      start of a line; ``days`` is not read. A ``.gz`` is never trimmed.
    - ``delete_dir``: the folder goes whole, aged by the newest mtime of
      anything inside it (a browser profile is one unit — never file by file).

    A file's age is the last dashed date in its name (local midnight), or its
    mtime when its name carries none. A base log with rotated siblings
    (``<base>._<D>``, ``<base>._<D>.gz``) is no older than the day after its
    newest sibling — it holds its process's last day (ruling T5-1a). ``.gz``
    files are aged the same way and are only ever deleted.
    """

    glob: str
    days: float
    action: str
    keep_bytes: Optional[int] = None

    def __post_init__(self) -> None:
        if self.action not in ACTIONS:
            raise ValueError(f"unknown retention action {self.action!r}; one of {ACTIONS}")
        if not self.glob or self.glob.startswith(("/", "\\")) or os.path.isabs(self.glob):
            raise ValueError(f"a retention glob is relative to the sweep's root: {self.glob!r}")
        if ".." in Path(self.glob).parts:
            raise ValueError(f"a retention glob never climbs out of its root: {self.glob!r}")
        if self.days < 0:
            raise ValueError("retention days are never negative")
        if self.action == "trim" and (self.keep_bytes is None or self.keep_bytes < 0):
            raise ValueError("a trim policy says how many bytes to keep (keep_bytes >= 0)")


@dataclass
class SweepResult:
    """What one sweep did. ``refused`` and ``held_open`` count distinct paths:
    refused = under PROTECTED, outside SWEEPABLE_ROOTS, or a link; held_open =
    a file this process's logging handlers write to (or one written to while
    it was being zipped)."""

    deleted: int = 0
    gzipped: int = 0
    trimmed: int = 0
    refused: int = 0
    held_open: int = 0
    errors: int = 0
    freed_bytes: int = 0
    # The first failure's words, for the night's record (not a count).
    first_error: str = field(default="", repr=False, compare=False)

    def as_dict(self) -> Dict[str, int]:
        out = asdict(self)
        out.pop("first_error", None)
        return out


def open_log_paths() -> frozenset:
    """The real paths of every file a logging handler in this process writes
    to: the root logger's and every named logger's (main.py hangs its debug
    file on named loggers only)."""
    loggers: List[Any] = [logging.getLogger()]
    loggers.extend(
        lg for lg in list(logging.Logger.manager.loggerDict.values()) if isinstance(lg, logging.Logger)
    )
    paths = set()
    for lg in loggers:
        for handler in list(getattr(lg, "handlers", ()) or ()):
            base = getattr(handler, "baseFilename", None)
            if base:
                paths.add(os.path.realpath(base))
    return frozenset(paths)


def _within(path: str, folder: str) -> bool:
    return path.startswith(folder.rstrip(os.sep) + os.sep)


def _refusal(root: Path, real_root: str, path: Path) -> Optional[str]:
    """Why ``path`` may not be swept, or None. Checked by the lexical path
    and by the real one (a link or a linked folder never leads a sweep into
    her records)."""
    try:
        rel = os.path.normpath(path.relative_to(root).as_posix()).replace(os.sep, "/")
    except ValueError:
        return "outside the root"
    if any(rel.startswith(p) for p in PROTECTED):
        return "her records"
    allowed = [a for a in SWEEPABLE_ROOTS if rel.startswith(a + "/")]
    if not allowed:
        return "outside the sweepable folders"
    if path.is_symlink():
        return "a link"
    real = os.path.realpath(path)
    if any(real.startswith(os.path.realpath(os.path.join(real_root, p))) for p in PROTECTED):
        return "her records"
    if not any(_within(real, os.path.realpath(os.path.join(real_root, a))) for a in allowed):
        return "outside the sweepable folders"
    return None


def _named_day(name: str) -> Optional[float]:
    """Local midnight of the last valid dashed date in ``name``."""
    for match in reversed(_NAMED_DATE.findall(name)):
        try:
            return datetime(int(match[0]), int(match[1]), int(match[2])).timestamp()
        except ValueError:
            continue
    return None


def _newest_chunk_days(folder: Path) -> Dict[str, float]:
    """For each base log in ``folder``: local midnight of the day AFTER its
    newest rotated chunk (ruling T5-1a). A base file (``palli_verbose_<start>
    .log``) names only its process's start, but once the process is gone it
    holds that process's last day — the day after its newest chunk at the
    earliest. Names only; read once per folder per sweep, before anything in
    it is removed, so a base never loses its age to the same sweep."""
    out: Dict[str, float] = {}
    try:
        names = os.listdir(folder)
    except OSError:
        return out
    for name in names:
        match = _ROTATED_CHUNK.match(name)
        if not match:
            continue
        try:
            after = (datetime(int(match["y"]), int(match["m"]), int(match["d"])) + timedelta(days=1)).timestamp()
        except ValueError:
            continue
        base = match["base"]
        if after > out.get(base, float("-inf")):
            out[base] = after
    return out


def _named_age_from(path: Path, chunks: Dict[str, float]) -> Optional[float]:
    """The moment a file's age is counted from, by names only: the last date
    in its own name, or — for a base log with rotated siblings — the day after
    its newest sibling, whichever is later (T5-1, T5-1a). A zipped base is
    the same base (``<base>.gz``)."""
    own = _named_day(path.name)
    stem = path.name[:-3] if path.name.endswith(".gz") else path.name
    sibling = chunks.get(stem)
    candidates = [d for d in (own, sibling) if d is not None]
    return max(candidates) if candidates else None


def _newest_mtime(folder: Path) -> float:
    newest = os.lstat(folder).st_mtime
    for dirpath, dirnames, filenames in os.walk(folder, followlinks=False):
        for name in dirnames + filenames:
            try:
                newest = max(newest, os.lstat(os.path.join(dirpath, name)).st_mtime)
            except OSError:
                continue
    return newest


def _tree_bytes(folder: Path) -> int:
    total = 0
    for dirpath, _dirnames, filenames in os.walk(folder, followlinks=False):
        for name in filenames:
            try:
                total += os.lstat(os.path.join(dirpath, name)).st_size
            except OSError:
                continue
    return total


def _act_delete(path: Path) -> int:
    size = os.lstat(path).st_size
    path.unlink()
    return size


def _act_delete_dir(path: Path) -> int:
    size = _tree_bytes(path)
    shutil.rmtree(path)
    return size


def _act_gzip(path: Path) -> Optional[int]:
    """Zip ``path`` beside itself with its mtime and mode, then remove it.
    None when the file changed while it was being zipped (someone is still
    writing it): the zip is dropped and the file stays."""
    before = os.stat(path)
    target = path.with_name(path.name + ".gz")
    # A leading dot: no policy glob can match a half-written zip.
    partial = path.with_name("." + path.name + ".gz.partial")
    try:
        with open(path, "rb") as src, open(partial, "wb") as raw:
            with gzip.GzipFile(filename=path.name, mode="wb", fileobj=raw, compresslevel=6,
                               mtime=int(before.st_mtime)) as out:
                shutil.copyfileobj(src, out, _MB)
            raw.flush()
            os.fsync(raw.fileno())
        after = os.stat(path)
        if (after.st_size, after.st_mtime_ns) != (before.st_size, before.st_mtime_ns):
            partial.unlink(missing_ok=True)
            return None
        os.chmod(partial, stat.S_IMODE(before.st_mode))
        os.utime(partial, ns=(before.st_atime_ns, before.st_mtime_ns))
        os.replace(partial, target)
    except BaseException:
        partial.unlink(missing_ok=True)
        raise
    path.unlink()
    return before.st_size - os.stat(target).st_size


def _act_trim(path: Path, keep_bytes: int) -> int:
    """Keep the last ``keep_bytes`` bytes, from the start of a line, in
    place (a writer appending with O_APPEND, as cron's ``>>`` and ``tee -a``
    do, keeps writing to the same file). 0 when the file is small enough."""
    with open(path, "r+b") as fh:
        size = os.fstat(fh.fileno()).st_size
        if size <= keep_bytes:
            return 0
        start = size - keep_bytes
        fh.seek(start - 1)
        tail = fh.read()
        if tail[:1] == b"\n":
            tail = tail[1:]
        else:
            tail = tail[1:]
            newline = tail.find(b"\n")
            if 0 <= newline < len(tail) - 1:
                tail = tail[newline + 1:]
        fh.seek(0)
        fh.write(tail)
        fh.truncate(len(tail))
    return size - len(tail)


def sweep(
    root: Any,
    *,
    now: float,
    policy: Iterable[Policy],
    open_paths: Optional[Iterable[str]] = None,
    should_stop: Optional[Callable[[], bool]] = None,
) -> SweepResult:
    """Apply ``policy`` under ``root`` at time ``now``; never raises for one
    file (an error is counted and logged, and the sweep goes on).

    ``open_paths`` are files that must not be touched; None = every file this
    process's logging handlers hold open. ``should_stop`` is asked before each
    path, so a cancelled sweep ends between files.
    """
    root = Path(root)
    real_root = os.path.realpath(root)
    held = {os.path.realpath(p) for p in (open_log_paths() if open_paths is None else open_paths)}
    result = SweepResult()
    refused: set = set()
    held_open: set = set()
    chunk_days: Dict[str, Dict[str, float]] = {}

    def _chunks(folder: Path) -> Dict[str, float]:
        key = str(folder)
        if key not in chunk_days:
            chunk_days[key] = _newest_chunk_days(folder)
        return chunk_days[key]

    def _failed(what: str, exc: BaseException) -> None:
        result.errors += 1
        if not result.first_error:
            result.first_error = f"{what}: {exc or type(exc).__name__}"

    def _done() -> SweepResult:
        result.refused, result.held_open = len(refused), len(held_open)
        return result

    for rule in policy:
        try:
            matches = sorted(root.glob(rule.glob))
        except (OSError, ValueError, NotImplementedError) as exc:
            _failed(rule.glob, exc)
            continue
        for path in matches:
            if should_stop is not None and should_stop():
                return _done()
            key = str(path)
            why = _refusal(root, real_root, path)
            if why is not None:
                if key not in refused:
                    refused.add(key)
                    logger.warning("retention: refused %s (%s)", key, why)
                continue
            try:
                chunks = _chunks(path.parent)  # read before anything here is removed
                real = os.path.realpath(path)
                if real in held:
                    held_open.add(key)
                    continue
                info = os.lstat(path)
                if rule.action == "delete_dir":
                    if not stat.S_ISDIR(info.st_mode):
                        continue
                    if any(_within(h, real) for h in held):
                        held_open.add(key)
                        continue
                    if now - _newest_mtime(path) > rule.days * _DAY:
                        result.freed_bytes += _act_delete_dir(path)
                        result.deleted += 1
                    continue
                if not stat.S_ISREG(info.st_mode):
                    continue
                zipped = path.name.endswith(".gz")
                if rule.action == "trim":
                    if not zipped:
                        freed = _act_trim(path, int(rule.keep_bytes or 0))
                        if freed:
                            result.trimmed += 1
                            result.freed_bytes += freed
                    continue
                if rule.action == "gzip" and (zipped or path.name.endswith(".partial")):
                    continue
                day = _named_age_from(path, chunks)
                since = day if day is not None else info.st_mtime
                if now - since <= rule.days * _DAY:
                    continue
                if rule.action == "delete":
                    result.freed_bytes += _act_delete(path)
                    result.deleted += 1
                else:
                    freed = _act_gzip(path)
                    if freed is None:
                        held_open.add(key)
                    else:
                        result.gzipped += 1
                        result.freed_bytes += freed
            except FileNotFoundError:
                continue  # gone since the glob (another rule, or its owner)
            except Exception as exc:
                _failed(path.name, exc)
                logger.warning("retention: %s on %s failed: %s", rule.action, key, exc or type(exc).__name__)
    return _done()


def policies_from_config(cfg: Any) -> List[Policy]:
    """The owner's limits (B8-4) as a policy, in order: each family's delete
    before its gzip, so nothing is zipped on its way out. A 0 turns that one
    limit off."""
    out: List[Policy] = []
    for glob, delete_days, gzip_days in (
        ("logs/palli_2*.log*", cfg.main_log_days, cfg.main_log_gzip_days),
        ("logs/palli_verbose_*", cfg.verbose_log_days, cfg.verbose_log_gzip_days),
        ("logs/palli_debug_*", cfg.debug_log_days, cfg.debug_log_gzip_days),
    ):
        if delete_days > 0:
            out.append(Policy(glob, days=delete_days, action="delete"))
        if gzip_days > 0:
            out.append(Policy(glob, days=gzip_days, action="gzip"))
    if cfg.trimmed_log_mb > 0:
        for name in ("backup.log", "bounce.log"):
            out.append(Policy(f"logs/{name}", days=0, action="trim", keep_bytes=cfg.trimmed_log_mb * _MB))
    if cfg.forge_profile_days > 0:
        out.append(Policy("data/forge/profiles/*", days=cfg.forge_profile_days, action="delete_dir"))
    if cfg.voice_recording_days > 0:
        out.append(Policy("logs/voice-debug-recordings/*", days=cfg.voice_recording_days, action="delete"))
    return out


def retention_config(settings: Any = None) -> Any:
    """The ``retention:`` block of ``settings`` (the snapshot when None); the
    schema defaults — off — when there is none."""
    from core.settings_schema import RetentionConfig

    snapshot = settings if settings is not None else _settings_snapshot()
    cfg = getattr(snapshot, "retention", None)
    return cfg if cfg is not None else RetentionConfig()


def enabled_in_settings_file(yaml_path: Optional[Any] = None) -> bool:
    """Whether the settings file says ``retention.enabled`` — read straight
    from the file, for main.py's ``setup_logging``, which runs before settings
    boot. Anything unreadable or invalid reads as off (today's behaviour).

    The WHOLE file is validated, the way the boot validates it (final fix
    wave, ruling FW-9): a bad block anywhere makes the boot fall back to off,
    so this must say off too — else the old boot sweep stands down for a
    retention the boot never turns on, and neither sweep runs."""
    try:
        import yaml

        from core.settings_schema import PalliSettings

        if yaml_path is None:
            from core.settings_boot import settings_service

            yaml_path = settings_service._yaml_path
        raw = yaml.safe_load(Path(yaml_path).read_text(encoding="utf-8")) or {}
        return bool(PalliSettings.model_validate(raw).retention.enabled)
    except Exception:
        return False


_SWEEP_LOCK = threading.Lock()


def _sweep_once(*, now: float, cfg: Any, root: Optional[Any] = None,
                should_stop: Optional[Callable[[], bool]] = None) -> Dict[str, Any]:
    """One sweep with ``cfg``'s limits (on a worker thread); its outcome for
    the night's record. Never two at once (the boot's and the night's)."""
    if not _SWEEP_LOCK.acquire(blocking=False):
        return {"skipped": "already_running"}
    try:
        result = sweep(REPO_ROOT if root is None else root, now=now,
                       policy=policies_from_config(cfg), should_stop=should_stop)
    finally:
        _SWEEP_LOCK.release()
    outcome: Dict[str, Any] = result.as_dict()
    logger.info(
        "retention: deleted %d, gzipped %d, trimmed %d, refused %d, held open %d, errors %d, freed %.1f MB",
        result.deleted, result.gzipped, result.trimmed, result.refused, result.held_open,
        result.errors, result.freed_bytes / _MB,
    )
    if result.errors:
        outcome["error"] = f"{result.errors} could not be swept; first: {result.first_error}"
    return outcome


async def _off_loop_sweep(*, now: float, cfg: Any) -> Dict[str, Any]:
    stop = threading.Event()
    try:
        return await asyncio.to_thread(_sweep_once, now=now, cfg=cfg, should_stop=stop.is_set)
    except asyncio.CancelledError:
        stop.set()  # the worker ends between files
        raise


async def _sweep_logs(*, now: float, settings: Any) -> Dict[str, Any]:
    """The night's log sweep. Pinned to a no-op for every test
    (tests/conftest.py, rulings G3, T5-4); its tests put it back."""
    try:
        cfg = retention_config(settings)
        if not cfg.enabled:
            return {"skipped": "flag_off"}
        return await _off_loop_sweep(now=now, cfg=cfg)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.warning("housekeeping: the log sweep failed: %s", exc or type(exc).__name__)
        return {"error": str(exc) or type(exc).__name__}


_BOOT_TASKS: set = set()


async def _boot_sweep(*, now: Optional[float], cfg: Any) -> Dict[str, Any]:
    try:
        return await _off_loop_sweep(now=time.time() if now is None else now, cfg=cfg)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.warning("retention: the boot sweep failed: %s", exc or type(exc).__name__)
        return {"error": str(exc) or type(exc).__name__}


def start_boot_sweep(*, settings: Any = None, now: Optional[float] = None) -> Optional["asyncio.Task[Dict[str, Any]]"]:
    """At boot, after settings boot (ruling T5-3): one sweep in the
    background, off the event loop — the first live one zips some 650 MB.
    Nothing at all while ``retention.enabled`` is off."""
    try:
        cfg = retention_config(settings)
    except Exception:
        return None
    if not cfg.enabled:
        return None
    task = asyncio.get_running_loop().create_task(_boot_sweep(now=now, cfg=cfg), name="retention:boot-sweep")
    _BOOT_TASKS.add(task)
    task.add_done_callback(_BOOT_TASKS.discard)
    return task


async def run_nightly_housekeeping(*, now: float, settings: Optional[Any] = None) -> Dict[str, Any]:
    """Run every housekeeping step; ``{step: outcome}``. ``settings`` is the
    settings snapshot (read here when not given)."""
    snapshot = settings if settings is not None else _settings_snapshot()
    outcomes: Dict[str, Any] = {}
    outcomes["history_prune"] = await _prune_identity_history(now=now, settings=snapshot)
    outcomes["retention"] = await _sweep_logs(now=now, settings=snapshot)
    from core.turn.companion_flags import today_slot_on

    if today_slot_on():
        outcomes["today_prune"] = await _prune_retired_today(now=now)
    from core import reply_sources

    # Whenever the record exists, switch on or off: a day written while it
    # was on still goes after its fourteen days. Never written: no step.
    if reply_sources.record_dir().is_dir():
        outcomes["reply_sources_prune"] = await _prune_reply_sources(now=now)
    return outcomes
