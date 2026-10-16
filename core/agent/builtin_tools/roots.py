"""The roots her two file tools may reach (stranger-proofing week 1, task 2).

``read_file`` took any absolute path the model named and returned the bytes;
``deliver_artifact`` handed the file itself to the surface. On the owner's own
machine that is a convenience he chose. On a stranger's machine it is the
whole disk, reachable from a first message — and the chat endpoint can reach
these tools without a login.

So: one allowlist, resolved at call time (nothing is baked at import, and a
root that does not exist yet is still a root — the workbench user may not be
created on this host).

  * the repo — her substrate and her working files
  * her workbench root (``WORKBENCH_ROOT``, the same knob workbench_shell reads)
  * the inbox, where a file he sends her lands (``PALLI_INBOX_DIR``)
  * ``FILE_TOOL_EXTRA_ROOTS`` — colon-separated, for anyone who wants to widen
    it deliberately rather than by accident

Symlinks resolve FIRST: a link that sits inside a root but points out of it is
out. And being inside a root is not sufficient — the repo also holds
``.env.local`` (every key she has), the sqlite brain (every word he ever said)
and ``data/delegations/`` (host credentials), so those are refused by name.

Both tools RETURN ``str(ToolPathError)``; they never raise. Tools return
strings — that contract is older than this module and it stands.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[3]

# Refused even inside a root. Names, not paths, so a copy of the file
# somewhere else in the tree is refused too.
#
# The spec's shape is the glob ``*.sqlite3*``, and a suffix tuple is narrower
# than that: this box also produces ``*.sqlite3-journal`` and ``*.sqlite3*.bak``
# (.gitignore), and a ``.bak`` is a COMPLETE copy of her memory store. So the
# test is "the name contains ``.sqlite3``", which the whole family satisfies
# and an ordinary file named ``sqlite3_notes.md`` does not.
_DENIED_NAME_FRAGMENT = ".sqlite3"
_DENIED_SUBPATH = "/data/delegations/"


class ToolPathError(Exception):
    """A path a file tool must not touch."""


def _extra_roots_spec() -> str:
    """Env first, config as the import-time snapshot — the shape ``nas_root()``
    already uses, so a test can set the env and be believed."""
    env = os.getenv("FILE_TOOL_EXTRA_ROOTS")
    if env is not None:
        return env
    try:
        from core.config import config
        return str(getattr(config, "FILE_TOOL_EXTRA_ROOTS", "") or "")
    except Exception:  # noqa: BLE001 — a broken config never widens the roots
        return ""


# expanduser() and resolve() both throw on input a model can invent:
# ``~nosuchuser/x`` raises RuntimeError, an embedded NUL raises ValueError, a
# broken mount raises OSError. Every call site catches all three, because
# resolve_inside must never raise anything but ToolPathError — the tools catch
# that one and nothing else.
_PATH_ERRORS = (OSError, ValueError, RuntimeError)


def _absolute(path: Path) -> Path:
    """A relative root/path hangs off the repo, never off the process cwd
    (read_file's schema says "Absolute or repo-relative")."""
    path = path.expanduser()
    return path if path.is_absolute() else REPO_ROOT / path


def allowed_roots() -> tuple[Path, ...]:
    """The roots, resolved, in a stable order and without duplicates."""
    candidates: list[Path] = [REPO_ROOT]

    try:
        from ai.workbench import WORKBENCH_ROOT_DEFAULT
        workbench = os.environ.get("WORKBENCH_ROOT") or WORKBENCH_ROOT_DEFAULT
    except Exception:  # noqa: BLE001 — the workbench is optional on a fresh host
        workbench = os.environ.get("WORKBENCH_ROOT") or ""
    if workbench:
        candidates.append(Path(workbench))

    try:
        # inbox_root() returns a RELATIVE "data/inbox" unless PALLI_INBOX_DIR
        # pins it, and the inbox writer resolves that against the process cwd.
        # We anchor it to the repo instead. Same folder for every supported
        # launch (main.py is run from the repo root — SETUP.md, the systemd
        # unit and palli_ctl all do); they diverge only if someone starts her
        # from elsewhere, and then the inbox writer, not this list, is the one
        # that moved.
        from core.turn.inbox import inbox_root
        candidates.append(Path(inbox_root()))
    except Exception:  # noqa: BLE001
        candidates.append(Path("data/inbox"))

    for token in _extra_roots_spec().split(":"):
        token = token.strip()
        if token:
            candidates.append(Path(token))

    out: list[Path] = []
    for candidate in candidates:
        try:
            resolved = _absolute(candidate).resolve()
        except _PATH_ERRORS:                  # an unusable root is not a root
            continue
        if resolved not in out:
            out.append(resolved)
    return tuple(out)


def _denied_reason(path: Path) -> str | None:
    name = path.name
    if name == ".env" or name.startswith(".env."):
        return "that file holds her keys"
    if _DENIED_NAME_FRAGMENT in name:
        return "that file is her memory store"
    if _DENIED_SUBPATH in path.as_posix() + "/":
        return "that folder holds host credentials"
    return None


def resolve_inside(path: Any) -> Path:
    """Resolve ``path`` and prove it is one she may touch, or raise.

    Symlinks are resolved before the check, so a link inside a root that
    points out of it does not smuggle anything back in.
    """
    raw = str(path or "").strip()
    if not raw:
        raise ToolPathError("no path given")
    try:
        resolved = _absolute(Path(raw)).resolve()
    except _PATH_ERRORS as exc:
        raise ToolPathError(f"refused: could not resolve {raw!r} ({exc})") from exc

    resolved_roots: list[Path] = []
    for root in allowed_roots():
        try:
            resolved_roots.append(_absolute(Path(root)).resolve())
        except _PATH_ERRORS:
            continue
    if not any(resolved.is_relative_to(root) for root in resolved_roots):
        listed = ", ".join(str(root) for root in resolved_roots) or "(none configured)"
        raise ToolPathError(
            f"{resolved} is outside the roots she may read ({listed})"
        )

    reason = _denied_reason(resolved)
    if reason is not None:
        raise ToolPathError(f"refused: {reason} — {resolved}")
    return resolved
