"""Reversible masking of secret-shaped strings at the provider seam.

Batch 1 (2026-09-25). His denylist and her own credentials are redacted
IRREVERSIBLY at three chokepoints (core/secrets_redaction.py). This layer
is different: it covers what nobody typed on a list — an SSN a tool looked
up, a key he pasted to be filed — and it must stay USABLE by her tools. So a
secret-shaped string is swapped for `[MASKED:<8 hex>]` in every request to a
provider, and swapped back only where the real value is needed: her tool
arguments, and a reply to the owner. Disk keeps the real values; the
provider never sees them. Exact grammars only — never a guess (owner rule).
"""
from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import threading
from typing import Any, Callable

# Whitespace and case are tolerated on the way back in (a hand-retyped token,
# or one that picked up a line-wrap) — hex is normalized to lowercase before
# the put-back lookup, which always stores lowercase keys.
TOKEN_RE = re.compile(r"\[MASKED:\s*([0-9a-fA-F]{8})\s*\]")

# Fix round 1 (2026-09-26), item 3 (plan-mandated): a leading `\b` misses a
# key right after `_` (markdown `_sk-…_`) because `_` is a word character —
# there is no boundary between two word characters. It also misses a key
# right after a JSON-escaped newline written as the two literal characters
# `\` `n` (Task 3 masks tool-call arguments AFTER `json.dumps`, so that is
# exactly what a real payload looks like). Replaced with an explicit
# lookbehind: allowed right after `\n`/`\t`/`\r` (as literal escape text) or
# anywhere NOT immediately after an alnum character — so `_sk-…` and
# `\nsk-…` both start a match, but `task-management-and-follow-up-items`
# still doesn't (the `s` in its embedded "sk-" sits right after the alnum
# `a`).
# Final fix wave (2026-09-26): a preceding `-` is excluded too — in
# `my-sk-tips-and-tricks-…` the `sk-` is the tail of a hyphenated word, not
# a key starting.
# Surgical pass (2026-09-26): also allowed right after a JSON `\uXXXX`
# escape (json.dumps with ensure_ascii writes a curly quote as `\u201c`, whose
# last character is a hex digit — the key after it looked mid-word).
_KEY_BOUNDARY = r"(?:(?<=\\[ntr])|(?<=\\u[0-9a-fA-F]{4})|(?<![A-Za-z0-9-]))"

# (name, pattern, group) — group 0 masks the whole match; group 1 masks a part.
PATTERNS: tuple[tuple[str, re.Pattern[str], int], ...] = (
    ("pem", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"), 0),
    # The captured run must not end on a literal `.` — a sentence-final
    # period stays outside the token (`Bearer X.` and `Bearer X` mask to the
    # same token) while an internal dot (a JWT's two `.` separators) is
    # still part of the class and stays inside the run.
    ("bearer", re.compile(r"(?i)\bBearer\s+([A-Za-z0-9._~+/=\-]{19,}[A-Za-z0-9_~+/=\-])"), 1),
    # An `sk-` key's tail must carry at least one digit (final fix wave,
    # 2026-09-26): real keys always do, and a prose slug like
    # `sk-tips-and-tricks-for-the-weekend` does not. The digit must come
    # within 255 characters: an unbounded `*` rescanned the whole run at every
    # `sk-` candidate — quadratic (160 KB of `_sk-` took 64 s on her event
    # loop). Real keys are far shorter.
    ("key", re.compile(
        _KEY_BOUNDARY + r"(?:gsk_[A-Za-z0-9]{20,}|pmd_[A-Za-z0-9_\-]{40,}|sk-(?:ant-)?(?=[A-Za-z0-9_\-]{0,255}[0-9])[A-Za-z0-9_\-]{20,}|AKIA[0-9A-Z]{16}|"
        r"ghp_[A-Za-z0-9]{36}|github_pat_[A-Za-z0-9_]{22,}|xox[baprs]-[A-Za-z0-9\-]{10,}|"
        r"AIza[0-9A-Za-z_\-]{35})"), 0),
    # Fix round 1 (2026-09-26): `[0-9]` instead of `\d` — `\d` also matches
    # Arabic-Indic/fullwidth Unicode digits, which are not an SSN. Exact
    # grammars only (owner rule). Same change in scripts/rotate_vault_keys.py.
    ("ssn", re.compile(r"(?<![0-9-])[0-9]{3}-[0-9]{2}-[0-9]{4}(?![0-9-])"), 0),
    # Separators are `-`, `.` or a literal space only — NOT `\s` (tab/
    # newline). A numeric table with tab-separated columns is not a phone
    # number. `[0-9]`, not `\d` (final fix wave, 2026-09-26) — exact, like
    # the SSN grammar above.
    ("phone", re.compile(r"(?<![0-9-])(?:\+?1[-. ]?)?\(?[0-9]{3}\)?[-. ][0-9]{3}[-. ][0-9]{4}(?![0-9-])"), 0),
)

# A data: URL carrying base64 media, or a bare base64 blob with no data:
# prefix (Ollama sends images this way) — either can contain a run that
# coincidentally satisfies a key grammar (AKIA…, AIza…+35 chars), and
# masking mid-base64 would corrupt the bytes on put-back. Anything else that
# merely starts with the text "data:" (an SSE frame, a YAML file, a sentence)
# is ordinary text and IS masked — R2 revised (fix round 1, 2026-09-26) after
# the plain `startswith("data:")` guard was found to exempt those too.
_DATA_URL_RE = re.compile(
    r"data:[\w.+-]+/[\w.+-]+(?:;[\w.+-]+=[\w.+-]+)*;base64,[A-Za-z0-9+/=\s]+"
)
_RAW_BASE64_RE = re.compile(r"[A-Za-z0-9+/]+=*")


def _is_exempt_media(text: str) -> bool:
    if _DATA_URL_RE.fullmatch(text):
        return True
    if len(text) >= 256 and _RAW_BASE64_RE.fullmatch(text):
        return True
    return False


# Keys the process, not the value: without it, the token is a plain SHA-256
# prefix and a provider (or anyone who can guess a candidate, like every one
# of an SSN's ~10**9 values) can compute the token from the guess and confirm
# it by comparison — the token would be a stronger fingerprint than the
# value it hides. Set once at import, held only in memory, never written
# anywhere; stable for the life of the process, which is all "one stable
# token per value" ever needs. `reset_for_tests()` deliberately does not
# touch this — it only clears the put-back table.
_PROCESS_KEY: bytes = secrets.token_bytes(32)

_LOCK = threading.Lock()
_VALUES: dict[str, str] = {}   # hex -> value, process lifetime, bounded, LRU
_MAX_VALUES = 4096
# A real collision on an 8-hex HMAC prefix among <= 4096 values is already
# vanishingly rare; 64 in a row means something is broken. Never an
# unbounded loop under the lock (final fix wave, 2026-09-26).
_MAX_COLLISION_WALK = 64


def _digest(value: str, counter: int = 0) -> str:
    msg = value.encode("utf-8") if counter == 0 else f"{value}\x00{counter}".encode("utf-8")
    return hmac.new(_PROCESS_KEY, msg, hashlib.sha256).hexdigest()[:8]


def _token_for(value: str) -> str:
    with _LOCK:
        h = _digest(value)
        counter = 0
        # A different value landing on the same 8-hex prefix used to
        # silently overwrite the table entry, corrupting BOTH values' put-
        # back. On a real collision, walk to another token deterministically
        # (same value -> same token always; a colliding value gets pushed to
        # the next counter) instead.
        while h in _VALUES and _VALUES[h] != value:
            counter += 1
            if counter > _MAX_COLLISION_WALK:
                # Never the value in the message: this can reach a log.
                raise RuntimeError(
                    f"payload masking: no free token after {_MAX_COLLISION_WALK} attempts"
                )
            h = _digest(value, counter)
        is_new = h not in _VALUES
        # Touch-then-reinsert: a dict keeps insertion order, so popping and
        # re-adding moves this entry to the end — the least-recently-used
        # entry (evicted below) is always the one at the front, never a
        # value that keeps getting re-masked on every prompt.
        _VALUES.pop(h, None)
        if is_new and len(_VALUES) >= _MAX_VALUES:
            _VALUES.pop(next(iter(_VALUES)), None)
        _VALUES[h] = value
    return f"[MASKED:{h}]"


def mask_text(text: str) -> str:
    if not text:
        return text
    if _is_exempt_media(text):
        return text
    out = text
    for _name, pattern, group in PATTERNS:
        def _swap(m: re.Match[str], group=group) -> str:
            if group == 0:
                return _token_for(m.group(0))
            whole, part = m.group(0), m.group(group)
            return whole.replace(part, _token_for(part), 1)
        out = pattern.sub(_swap, out)
    return out


def unmask_text(text: str) -> str:
    if not text or "[MASKED:" not in text:
        return text
    def _back(m: re.Match[str]) -> str:
        key = m.group(1).lower()
        with _LOCK:
            return _VALUES.get(key, m.group(0))
    return TOKEN_RE.sub(_back, text)


def looks_secret(text: str) -> bool:
    return bool(text) and any(p.search(text) for _, p, _ in PATTERNS)


# Every grammar but the phone: what the memory belt refuses to SAVE (final
# fix wave, 2026-09-26). A phone number is masked on the wire like the rest,
# but a new memory line carrying one is kept — the spec stops a password or
# key from being saved and MOVES the phone; it never loses "mom's number".
_CREDENTIAL_PATTERNS = tuple(p for name, p, _ in PATTERNS if name != "phone")


def looks_like_credential(text: str) -> bool:
    return bool(text) and any(p.search(text) for p in _CREDENTIAL_PATTERNS)


def _walk(obj: Any, fn: Callable[[str], str]) -> Any:
    if isinstance(obj, str):
        return fn(obj)
    if isinstance(obj, dict):
        return {k: _walk(v, fn) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_walk(v, fn) for v in obj]
    if isinstance(obj, tuple):
        return tuple(_walk(v, fn) for v in obj)
    return obj


def mask_obj(obj: Any) -> Any:
    return _walk(obj, mask_text)


def unmask_obj(obj: Any) -> Any:
    return _walk(obj, unmask_text)


def redact_obj(obj: Any) -> Any:
    from core.secrets_redaction import redact
    return _walk(obj, redact)


def for_provider(obj: Any) -> Any:
    """Everything that leaves for a provider: known literals gone for good,
    then secret-shaped strings swapped for tokens."""
    return mask_obj(redact_obj(obj))


def reset_for_tests() -> None:
    with _LOCK:
        _VALUES.clear()
