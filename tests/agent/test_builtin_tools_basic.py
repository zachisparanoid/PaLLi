# tests/agent/test_builtin_tools_basic.py
from __future__ import annotations

import asyncio
import pytest


def _load(name):
    from tests.agent.conftest import load_module
    return load_module(f"core/agent/builtin_tools/{name}.py", f"{name}_tool_under_test")


def _ctx():
    tools_mod = __import__("tests.agent.conftest", fromlist=["load_module"]).load_module("core/agent/tools.py", "tools_for_builtin_test")
    return tools_mod.ToolContext(turn_owner_id=1, is_owner=True)


def test_read_file_tool_has_correct_shape():
    mod = _load("read_file")
    tool = mod.ReadFileTool()
    assert tool.name == "read_file"
    assert "path" in tool.input_schema["properties"]
    assert tool.requires_approval is False


def test_read_file_tool_reads_text(tmp_path, monkeypatch):
    # Premise updated 2026-09-08 (stranger-proofing week 1, task 2): the tool
    # is scoped to allowed roots now and tmp_path is outside every one of
    # them, so the fixture widens the roots the documented way rather than the
    # assertion getting weaker.
    monkeypatch.setenv("FILE_TOOL_EXTRA_ROOTS", str(tmp_path))
    mod = _load("read_file")
    p = tmp_path / "x.txt"
    p.write_text("hello", encoding="utf-8")
    tool = mod.ReadFileTool()
    out = asyncio.run(tool.execute({"path": str(p)}, _ctx()))
    assert "hello" in out


def test_local_shell_tool_runs_command():
    mod = _load("local_shell")
    tool = mod.LocalShellTool()
    assert tool.name == "local_shell"
    out = asyncio.run(tool.execute({"command": "echo hello"}, _ctx()))
    assert "hello" in out


def test_deliver_artifact_tool_hands_the_path_over(tmp_path, monkeypatch):
    """Week 2 task 2: the delivery is the entry the tool puts on the turn's
    ToolContext. Its result text is a plain sentence for her to read — no
    marker for anyone to parse, and nothing to counterfeit."""
    # Same premise update as read_file above: tmp_path is not a root by default.
    monkeypatch.setenv("FILE_TOOL_EXTRA_ROOTS", str(tmp_path))
    mod = _load("deliver_artifact")
    f = tmp_path / "art.txt"
    f.write_text("art", encoding="utf-8")
    tool = mod.DeliverArtifactTool()
    ctx = _ctx()
    out = asyncio.run(tool.execute({"path": str(f)}, ctx))
    assert out == f"queued for delivery: {f}"
    assert ctx.deliveries == [str(f)]


def test_deliver_artifact_without_a_delivery_channel_is_a_failure(tmp_path, monkeypatch):
    """Review fix: a context with nowhere to hand the path is not a quiet
    success. Answering "queued for delivery" while queueing nothing is the
    exact lie this task exists to remove, so it comes back as ToolFailure."""
    from core.agent.tool_outcome import ToolFailure

    monkeypatch.setenv("FILE_TOOL_EXTRA_ROOTS", str(tmp_path))
    mod = _load("deliver_artifact")
    f = tmp_path / "art.txt"
    f.write_text("art", encoding="utf-8")

    class NoChannel:                  # a stand-in context: no deliveries list
        turn_owner_id = 1
        is_owner = True

    raw = asyncio.run(mod.DeliverArtifactTool().execute({"path": str(f)}, NoChannel()))
    assert isinstance(raw, ToolFailure)
    assert str(raw) == "no delivery channel on this turn"
    assert "queued" not in str(raw)          # nothing was queued, so nothing says so


def test_read_file_refusal_is_a_failure_not_content(tmp_path, monkeypatch):
    """Week 2 task 1: a refusal is a tool that ran and did not work, so it
    comes back as ToolFailure and the dispatcher marks the result is_error.
    The wording is unchanged — only the type."""
    from core.agent.builtin_tools import roots
    from core.agent.tool_outcome import ToolFailure

    monkeypatch.setattr(roots, "allowed_roots", lambda: (tmp_path / "ws",))
    (tmp_path / "ws").mkdir()
    (tmp_path / "secret.txt").write_text("classified", encoding="utf-8")

    mod = _load("read_file")
    out = asyncio.run(mod.ReadFileTool().execute({"path": str(tmp_path / "secret.txt")}, _ctx()))
    assert isinstance(out, ToolFailure)
    assert "outside" in str(out)
    assert "classified" not in str(out)


def test_read_file_missing_file_is_a_failure(tmp_path, monkeypatch):
    """The other way a read fails: the path is allowed and there is nothing
    there. Same rule — an error result, not a success whose text says so."""
    from core.agent.builtin_tools import roots
    from core.agent.tool_outcome import ToolFailure

    monkeypatch.setattr(roots, "allowed_roots", lambda: (tmp_path,))

    mod = _load("read_file")
    out = asyncio.run(mod.ReadFileTool().execute({"path": str(tmp_path / "gone.txt")}, _ctx()))
    assert isinstance(out, ToolFailure)
    assert "file not found" in str(out)
