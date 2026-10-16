"""Secrets redaction from a denylist he controls (item 7c, 2026-08-19).

Why. A secret typed in Discord flows everywhere a message flows: his
desktop password sat in plaintext in her session transcript (37×), the DB
history, the verbose/debug logs (~190×), a present-moment record — and in
the live payload to DeepSeek on every turn. Scrubbing after the fact is
not a mechanism.

What. ``data/secrets_denylist.txt`` — one literal per line, his hand —
plus, since 2026-09-17, every credential her own config holds (any
non-empty ``*_TOKEN`` / ``*_API_KEY`` / ``*_SECRET`` value, 8+ chars) —
plus, since 2026-09-25 (Batch 1), the vault's own two key files
(``VAULT_DIR/.passphrase`` and ``VAULT_DIR/vault.key``; ``VAULT_DIR``
defaults to ``~/.palli/vault``, env override ``PALLI_VAULT_DIR``) —
``#`` comments allowed, case-insensitive, mode 600, gitignored — is
redacted to ``[REDACTED]`` at the three chokepoints: her transcript
(``Session.append``), the DB history rows (``core.turn.persistence``) and
every log record (``RedactingFilter`` on the root logger). Reloaded on
mtime (the vault files by mtime *and* size, so a same-tick rotation that
recreates a file empty then writes it is not missed — Task 8's rewrite
does exactly that). Empty or missing = nothing happens: this is HIS list,
not a heuristic (the owner rule: denylists are code; guessing what looks
like a password is not).

A credential that is not on the config yet — a brain key a stranger is
pasting on the setup page, before anything is saved — is covered for the
length of one call by ``covering()`` (first door, task 2, 2026-09-17). The
moment the same key IS put on the config, ``refresh()`` says so, because
nothing here watches the config (first door, final wave, 2026-09-17).
"""
from __future__ import annotations

import logging
import os
import re
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Pattern

DENYLIST_PATH = Path("data/secrets_denylist.txt")
REDACTED = "[REDACTED]"

_lock = threading.Lock()
_cache_key: Optional[tuple] = None
_cache_pattern: Optional[Pattern[str]] = None
# Bumped by `refresh()` and folded into the cache key. Nothing watches the
# config, so this is how a caller that just put a credential on it says so.
_generation: int = 0
# Credentials that are only a secret for the length of one call (see
# `covering`). Ref-counted, never persisted: literal -> how many callers are
# holding it open.
_covered: Dict[str, int] = {}


def _load_literals(path: Path) -> List[str]:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return []
    out: List[str] = []
    for line in raw.splitlines():
        lit = line.strip()
        if not lit or lit.startswith("#"):
            continue
        out.append(lit)
    # longest first so a literal that contains another is redacted whole
    return sorted(set(out), key=len, reverse=True)


# The secrets she holds herself (2026-09-17). The first Telegram boot logged
# her bot token in plain text twelve times: httpx logs every request URL at
# INFO and Telegram's API carries the token IN the URL. His denylist did not
# have it — nobody had typed it there. A credential the application itself
# holds is not a guess (the owner rule forbids guessing what looks like a
# password); it is a known literal, so every non-empty value on her config
# whose name says TOKEN, API_KEY or SECRET joins the set without anyone's
# hand. Short values (< 8 chars — placeholders, "x") are left alone so the
# filter can never shred ordinary text.
_SECRET_NAME_MARKERS = ("_TOKEN", "_API_KEY", "_SECRET", "SECRET_KEY")
_SECRET_MIN_LEN = 8


def _config_secret_literals() -> List[str]:
    try:
        from core.config import config as _cfg
    except Exception:
        return []
    out: List[str] = []
    for name in dir(_cfg):
        if not name.isupper() or not any(m in name for m in _SECRET_NAME_MARKERS):
            continue
        try:
            val = getattr(_cfg, name)
        except Exception:
            continue
        if isinstance(val, str):
            v = val.strip()
            if len(v) >= _SECRET_MIN_LEN:
                out.append(v)
    return out


# The vault's own keys (Batch 1, 2026-09-25). Both key files went to the
# provider: the passphrase rode MEMORY.md in every prompt; the key file's
# contents sat in her Discord transcript from the 09-24 repair on. They are
# known literals on this machine, not guesses — the same rule as config.
VAULT_DIR = Path(os.environ.get("PALLI_VAULT_DIR") or (Path.home() / ".palli" / "vault"))
VAULT_SECRET_FILES = (".passphrase", "vault.key")
# The vault itself, in the same folder. Named once here for the two places
# that keep it private: her saves (core/turn/local_shell.py) and the doctor.
VAULT_KDBX_NAME = "credentials.kdbx"


def _vault_secret_literals() -> List[str]:
    out: List[str] = []
    for name in VAULT_SECRET_FILES:
        try:
            text = (VAULT_DIR / name).read_text(encoding="utf-8", errors="ignore").strip()
        except OSError:
            continue
        if len(text) >= _SECRET_MIN_LEN:
            out.append(text)
    return out


def _vault_mtimes() -> tuple:
    """Same (mtime, size) shape as the denylist's own cache key, and for the
    same reason: mtime alone missed the rotation (Task 8), which moves the
    old ``.passphrase`` aside and recreates it empty before writing the new
    text. A `_pattern()` call landing between create and write reads an
    empty file (too short to count); a write that lands in the same kernel
    timestamp tick leaves mtime unchanged either way — so mtime alone can
    cache a stale or empty read across the entire rotation. Size, plus the
    directory path (so a `VAULT_DIR` swap between tests never reuses another
    vault's key), closes both holes."""
    stamps = []
    for name in VAULT_SECRET_FILES:
        try:
            st = (VAULT_DIR / name).stat()
            stamps.append((st.st_mtime_ns, st.st_size))
        except OSError:
            stamps.append(None)
    return (str(VAULT_DIR), tuple(stamps))


@contextmanager
def covering(*values: str) -> Iterator[None]:
    """Redact `values` for the duration of the block, then stop.

    The first door, task 2 (2026-09-17): a brain key a stranger pastes on the
    setup page is NOT on her config while it is being tested — that is the
    whole point, nothing is saved until the probe passes — so the "every
    credential her own config holds" rule above does not cover it at the one
    moment it is travelling through aiohttp, a provider and an error string.
    The request registers it here for the length of the call and releases it
    after, so the filter never grows a literal per request for the life of the
    process.

    Ref-counted, because two callers may cover the same value; short values are
    ignored for the same reason config's are, so this can never shred ordinary
    text.
    """
    literals = [
        text
        for text in (str(value or "").strip() for value in values)
        if len(text) >= _SECRET_MIN_LEN
    ]
    with _lock:
        for literal in literals:
            _covered[literal] = _covered.get(literal, 0) + 1
    try:
        yield
    finally:
        with _lock:
            for literal in literals:
                remaining = _covered.get(literal, 0) - 1
                if remaining > 0:
                    _covered[literal] = remaining
                else:
                    _covered.pop(literal, None)


def refresh() -> None:
    """Say that the credentials on her config have changed.

    The literal set is rebuilt lazily, and the only things that move the cache
    key are the denylist file's (path, mtime, size), the vault files' own
    (path, mtime, size) pairs, and the covered set — nothing watches `config`.
    So a credential put on it while the cache is warm (the setup API saving a
    brain key) was not redacted until the next unrelated key change happened
    along: in practice `covering()`'s exit, one line later. True by accident,
    and only for as long as the save stays inside a covering block (first
    door, final wave, 2026-09-17).

    A generation counter rather than a rebuild: the filter runs on every log
    record, so this must cost nothing. No file read, no config read, no nested
    lock — it takes `_lock` alone and bumps an int, and the next `_pattern()`
    does the work the caller already needed done.
    """
    global _generation
    with _lock:
        _generation += 1


def _pattern() -> Optional[Pattern[str]]:
    global _cache_key, _cache_pattern
    path = DENYLIST_PATH
    try:
        st = path.stat()
        key = (str(path), st.st_mtime_ns, st.st_size)
    except OSError:
        key = (str(path), None, None)
    # Stat'd outside the lock, same as the denylist stat above: the vault
    # part of the cache key is (dir, (mtime, size) per file) — see
    # `_vault_mtimes` for why mtime alone is not enough.
    vault_key = _vault_mtimes()
    with _lock:
        # The covered set is part of the key: entering or leaving a `covering`
        # block must rebuild the pattern, and nothing else is watching it.
        # The generation is the third part, for the config credentials nothing
        # is watching either (see `refresh`). The vault key is the fourth.
        cache_key = (key, tuple(sorted(_covered)), _generation, vault_key)
        if cache_key == _cache_key:
            return _cache_pattern
        lits = _load_literals(path) if key[1] is not None else []
        lits = sorted(
            set(lits) | set(_config_secret_literals()) | set(_vault_secret_literals()) | set(_covered),
            key=len,
            reverse=True,
        )
        _cache_pattern = (
            re.compile("|".join(re.escape(l) for l in lits), re.IGNORECASE) if lits else None
        )
        _cache_key = cache_key
        return _cache_pattern


def redact(text: str) -> str:
    """The text with every denylisted literal replaced. Identity when the
    list is empty or missing. Never raises."""
    if not text:
        return text
    try:
        pat = _pattern()
        if pat is None:
            return text
        return pat.sub(REDACTED, str(text))
    except Exception:
        return text


def _redact_arg(value):
    """A log arg is redacted whatever its type: httpx logs the request URL as an
    httpx.URL object, and str(url) carries the bot token (2026-09-17). Only a
    value whose text actually changes is replaced, so ints, floats and objects
    without secrets pass through untouched."""
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, (bytes, bytearray)):
        try:
            text = value.decode("utf-8", "replace")
        except Exception:
            return value
        red = redact(text)
        return red.encode("utf-8") if red != text else value
    try:
        text = str(value)
    except Exception:
        return value
    red = redact(text)
    return red if red != text else value


class RedactingFilter(logging.Filter):
    """Root-logger filter: redacts the message and its args before any
    handler formats the record (so every log file is covered at once)."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            if _pattern() is None:
                return True
            if isinstance(record.msg, str):
                record.msg = redact(record.msg)
            if record.args:
                if isinstance(record.args, dict):
                    record.args = {k: _redact_arg(v) for k, v in record.args.items()}
                else:
                    record.args = tuple(_redact_arg(a) for a in record.args)
        except Exception:
            pass
        return True


def reset_for_tests() -> None:
    global _cache_key, _cache_pattern
    with _lock:
        _cache_key = None
        _cache_pattern = None
        _covered.clear()
