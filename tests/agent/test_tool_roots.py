"""Her two file tools are scoped to a small allowlist of roots
(stranger-proofing week 1, task 2).

Before this, ``read_file`` took any absolute path the model named and handed
back the bytes; ``deliver_artifact`` handed the file itself to the surface.
On the owner's machine that is a convenience. On a stranger's machine — where
the chat endpoint is reachable by anyone the bind address lets in — a first
message could ask for a private key and get it.

Both tools now resolve through ``core.agent.builtin_tools.roots``: symlinks
are resolved FIRST (a link inside a root that points out of it is still out),
then the path must sit under one of the allowed roots, then a short denylist
refuses the secret-shaped files that live INSIDE those roots (``.env*``, the
sqlite brain, ``data/delegations/``). Neither tool ever raises: the refusal is
the return value.

Premise updated 2026-09-10 (week 2, task 1): a refusal is a call that ran and
did not work, so it comes back as ``ToolFailure`` rather than a bare string —
same wording (``str()`` is the text), and still returned, never raised.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest


def _read_file_tool():
    from core.agent.builtin_tools.read_file import ReadFileTool
    return ReadFileTool()


def _deliver_tool():
    from core.agent.builtin_tools.deliver_artifact import DeliverArtifactTool
    return DeliverArtifactTool()


def _ctx():
    """A real ToolContext: week 2 task 2 made the delivery an entry the tool
    hands over on it, so a refusal is now visible as an empty list."""
    from core.agent.tools import ToolContext
    return ToolContext(turn_owner_id=1, is_owner=True)


def test_read_file_refuses_paths_outside_the_roots(tmp_path, monkeypatch):
    from core.agent.builtin_tools import roots

    monkeypatch.setattr(roots, "allowed_roots", lambda: (tmp_path / "ws",))
    (tmp_path / "ws").mkdir()
    (tmp_path / "ws" / "ok.txt").write_text("fine", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("no", encoding="utf-8")

    tool = _read_file_tool()
    assert asyncio.run(tool.execute({"path": str(tmp_path / "ws" / "ok.txt")}, None)) == "fine"

    out = str(asyncio.run(tool.execute({"path": str(tmp_path / "secret.txt")}, None)))
    assert "outside" in out
    assert "no" not in out                      # the contents never leak

    link = tmp_path / "ws" / "link.txt"
    link.symlink_to(tmp_path / "secret.txt")    # a real symlink out of the root
    assert "outside" in str(asyncio.run(tool.execute({"path": str(link)}, None)))


def test_deliver_artifact_refuses_paths_outside_the_roots(tmp_path, monkeypatch):
    from core.agent.builtin_tools import roots

    monkeypatch.setattr(roots, "allowed_roots", lambda: (tmp_path / "ws",))
    (tmp_path / "ws").mkdir()
    inside = tmp_path / "ws" / "shot.png"
    inside.write_bytes(b"\x89PNG")
    outside = tmp_path / "id_ed25519"
    outside.write_text("private", encoding="utf-8")

    tool = _deliver_tool()
    ctx = _ctx()
    ok = asyncio.run(tool.execute({"path": str(inside)}, ctx))
    assert ok.startswith("queued for delivery:")
    assert str(inside) in ok
    assert ctx.deliveries == [str(inside)]

    refused = str(asyncio.run(tool.execute({"path": str(outside)}, ctx)))
    assert "outside" in refused
    assert ctx.deliveries == [str(inside)]      # the refusal handed nothing over


@pytest.mark.parametrize(
    "relative",
    [".env", ".env.local", "brain.sqlite3", "brain.sqlite3-wal",
     "brain.sqlite3-shm", "brain.sqlite3-journal", "brain.sqlite3.bak",
     "data/delegations/creds.txt"],
)
def test_the_denylist_refuses_secrets_that_live_inside_the_roots(
    tmp_path, monkeypatch, relative,
):
    """Being under a root is not enough. The repo root holds ``.env.local``
    (every key she has), the sqlite brain (every word he ever said) and
    ``data/delegations/`` (host credentials) — none of those are readable
    file content, they are the crown jewels."""
    from core.agent.builtin_tools import roots

    monkeypatch.setattr(roots, "allowed_roots", lambda: (tmp_path,))
    target = tmp_path / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("SECRETVALUE", encoding="utf-8")

    out = str(asyncio.run(_read_file_tool().execute({"path": str(target)}, None)))
    assert "refused" in out
    assert "SECRETVALUE" not in out

    ctx = _ctx()
    out = str(asyncio.run(_deliver_tool().execute({"path": str(target)}, ctx)))
    assert "refused" in out
    assert ctx.deliveries == []                 # a refusal hands nothing over


@pytest.mark.parametrize("relative", ["notes.txt", "sqlite3_notes.md"])
def test_the_memory_denylist_is_a_name_shape_not_a_word_ban(
    tmp_path, monkeypatch, relative,
):
    """``*.sqlite3*`` is the shape of the store, not a ban on the word. A note
    ABOUT sqlite3 is an ordinary file inside a root and she may read it — the
    glob has to be wide enough for `.bak` and narrow enough for this."""
    from core.agent.builtin_tools import roots

    monkeypatch.setattr(roots, "allowed_roots", lambda: (tmp_path,))
    target = tmp_path / relative
    target.write_text("ordinary", encoding="utf-8")

    assert asyncio.run(
        _read_file_tool().execute({"path": str(target)}, None)
    ) == "ordinary"


def test_a_repo_relative_path_resolves_against_the_repo_root():
    """read_file's schema promises "Absolute or repo-relative", so a relative
    path must land in the repo and not in whatever cwd the process happens to
    have."""
    from core.agent.builtin_tools import roots

    assert roots.resolve_inside("core/config.py") == roots.REPO_ROOT / "core" / "config.py"


def test_allowed_roots_carries_the_repo_the_workbench_and_the_inbox(monkeypatch, tmp_path):
    from core.agent.builtin_tools import roots

    monkeypatch.setenv("WORKBENCH_ROOT", str(tmp_path / "workspace"))
    monkeypatch.setenv("PALLI_INBOX_DIR", str(tmp_path / "inbox"))
    monkeypatch.setenv("FILE_TOOL_EXTRA_ROOTS", f"{tmp_path / 'extra1'}:{tmp_path / 'extra2'}")

    got = roots.allowed_roots()
    assert roots.REPO_ROOT in got
    assert (tmp_path / "workspace") in got
    assert (tmp_path / "inbox") in got
    assert (tmp_path / "extra1") in got
    assert (tmp_path / "extra2") in got
    # A root that does not exist yet is still a root (nothing is created here).
    assert not (tmp_path / "workspace").exists()


def test_a_relative_inbox_dir_hangs_off_the_repo_root(monkeypatch):
    from core.agent.builtin_tools import roots

    monkeypatch.setenv("PALLI_INBOX_DIR", "data/inbox")
    monkeypatch.delenv("FILE_TOOL_EXTRA_ROOTS", raising=False)
    assert roots.REPO_ROOT / "data" / "inbox" in roots.allowed_roots()


def test_an_empty_path_is_refused_not_resolved():
    from core.agent.builtin_tools import roots

    with pytest.raises(roots.ToolPathError):
        roots.resolve_inside("")


@pytest.mark.parametrize("hostile", ["~nosuchuser/x", "a\x00b", "~nosuchuser"])
def test_a_path_that_cannot_even_be_resolved_is_a_refusal_not_a_crash(hostile):
    """Review round 1: ``expanduser()`` raises RuntimeError for an unknown user
    and ``resolve()`` raises ValueError on an embedded NUL — neither is an
    OSError, so both used to escape past the tools' ``except ToolPathError``
    and crash the turn. resolve_inside raises ONE exception type; the tools
    return its message (as a ToolFailure since week 2 task 1 — returned, so
    the turn survives, and flagged, so the result is not read as a success)."""
    from core.agent.builtin_tools import roots
    from core.agent.tool_outcome import ToolFailure

    with pytest.raises(roots.ToolPathError):
        roots.resolve_inside(hostile)

    out = asyncio.run(_read_file_tool().execute({"path": hostile}, None))
    assert isinstance(out, ToolFailure) and "refused" in str(out)
    out = asyncio.run(_deliver_tool().execute({"path": hostile}, None))
    assert isinstance(out, ToolFailure) and "refused" in str(out)


def test_an_unusable_extra_root_is_dropped_not_fatal(monkeypatch):
    """The same three exception types, on the way in through the knob."""
    from core.agent.builtin_tools import roots

    monkeypatch.setenv("FILE_TOOL_EXTRA_ROOTS", "~nosuchuser/ws:/srv/real")
    got = roots.allowed_roots()
    assert roots.REPO_ROOT in got
    assert Path("/srv/real") in got
