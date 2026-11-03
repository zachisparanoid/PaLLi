"""Set keys in a dotenv file and change nothing else.

The brain switch's env writer (configurator spec 2026-09-03 §3.2 step 5).
`.env.local` is CRLF; the two older writers in this repo re-join with "\\n"
and silently convert it. This one keeps the newline style, every untouched
line, and the trailing-newline state, and writes bytes so Windows text mode
can never double a "\\r\\n". A value containing a line break is refused,
never written: a record must stay on one line.
"""
from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path
from typing import Mapping

# An inline comment, dotenv's way: a `#` that FOLLOWS whitespace inside the
# value. One that opens the value (`KEY=   # note`) or follows a non-space
# (`KEY=ff#00aa`) is data, and truncating there would quietly eat a password,
# a URL fragment or a colour.
_INLINE_COMMENT = re.compile(r"[ \t]#")


def _split_assignment(line: str) -> tuple[str | None, str | None]:
    """KEY and VALUE, read the way python-dotenv reads them.

    Until 2026-09-06 this kept an inline `# comment` inside the value, and the
    shipped `.env.template` has fourteen lines of that shape — six of them
    carrying values (`PRESENT_MOMENT_MIN_GAP_SECONDS=600   # min gap ...`).
    dotenv, which is what the app itself boots through, read those right; this
    parser did not, so the same file meant two things depending on who asked.
    Three env parsers with three comment rules was the class (final review M5);
    tests/palli_config/test_envfile.py pins this one to dotenv's answers over
    the whole template.
    """
    stripped = line.strip()
    if not stripped or stripped.startswith("#") or "=" not in stripped:
        return None, None
    key, _, value = stripped.partition("=")
    key = key.strip()
    if not key:
        return None, None
    value = value.strip()
    if value[:1] in ("'", '"'):
        end = value.find(value[0], 1)
        if end > 0:                      # quoted: the quotes decide, and any
            return key, value[1:end]     # trailing comment is outside them
    comment = _INLINE_COMMENT.search(value)
    if comment is not None:
        value = value[:comment.start()].rstrip()
    return key, value


def parse_env(text: str) -> dict[str, str]:
    """Uncommented KEY=VALUE lines; the last occurrence wins (python-dotenv's rule)."""
    out: dict[str, str] = {}
    for line in text.replace("\r\n", "\n").split("\n"):
        key, value = _split_assignment(line)
        if key is not None:
            out[key] = value or ""
    return out


def _render_value(value: str) -> str:
    text = str(value)
    if not text:
        return ""
    if any(ch.isspace() for ch in text) or any(ch in text for ch in "#\"'"):
        escaped = text.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return text


def set_env_keys(text: str, updates: Mapping[str, str | None]) -> str:
    """Return `text` with each key in `updates` set (None = deleted).

    First uncommented occurrence is replaced in place; later duplicates of a
    touched key are dropped; missing keys are appended. Everything else is
    byte-identical, including the newline style and the trailing newline.
    """
    newline = "\r\n" if "\r\n" in text else "\n"
    had_trailing = text.endswith(("\n", "\r\n"))
    body = text.replace("\r\n", "\n")
    lines = body.split("\n") if body else []
    if had_trailing and lines and lines[-1] == "":
        lines.pop()

    pending = {str(k): v for k, v in updates.items()}
    for key, value in pending.items():
        if value is not None and ("\n" in str(value) or "\r" in str(value)):
            raise ValueError(
                f"the value for {key} contains a line break; an env record must stay on one line"
            )
    seen: set[str] = set()
    out: list[str] = []
    for line in lines:
        key, _ = _split_assignment(line)
        if key is None or key not in pending:
            out.append(line)
            continue
        if key in seen:
            continue  # a later duplicate of a key we already wrote
        seen.add(key)
        value = pending[key]
        if value is None:
            continue
        out.append(f"{key}={_render_value(value)}")

    for key, value in pending.items():
        if key in seen or value is None:
            continue
        out.append(f"{key}={_render_value(value)}")
        seen.add(key)

    if not out:
        return ""
    rendered = newline.join(out)
    appended = any(k not in parse_env(text) for k in pending if pending[k] is not None)
    if had_trailing or appended or not text:
        rendered += newline
    return rendered


def atomic_write_text(path: Path, text: str) -> None:
    """Temp file beside the target, fsync, os.replace — bytes, never text mode."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(text.encode("utf-8"))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
