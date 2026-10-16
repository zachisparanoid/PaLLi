"""forge_tool (step 34, spec §2): HER judgment starts a forge project.

Pins: registration rides the features.forged_tools gate and never reaches
the workbench registry (no recursive forging from inside the wall); the
spawn seam receives his ask VERBATIM plus the origin channel; one active
project at a time (the second ask gets a queued-refusal receipt — a budget
in code, §2.5 one-thing-at-a-time); the receipt is honest about minutes.
No approval surface exists here — that is the design (spec §0, 04:11).
"""
from __future__ import annotations

import asyncio
import sys
import types

import pytest

from ai import forge_manifest
from core.agent.tools import ToolContext


@pytest.fixture()
def forge_root(tmp_path, monkeypatch):
    root = tmp_path / "forge"
    (root / "tools").mkdir(parents=True)
    monkeypatch.setenv("FORGE_ROOT", str(root))
    return root


@pytest.fixture()
def spawn_seam(monkeypatch):
    """Stub ai.forge.start_forge_project (the engine lands in Task 4)."""
    calls: list[dict] = []

    def _start(**kwargs):
        calls.append(kwargs)
        return None

    fake = types.ModuleType("ai.forge")
    fake.start_forge_project = _start
    monkeypatch.setitem(sys.modules, "ai.forge", fake)
    return calls


def _ctx(channel_id=777, surface="discord_text"):
    return ToolContext(
        turn_owner_id=1, is_owner=True, surface=surface, channel_id=channel_id
    )


def _execute(tool, input):
    return asyncio.run(tool.execute(input, _ctx()))


# --- registration gating ----------------------------------------------------

def test_registered_iff_flag_on(forge_root, monkeypatch):
    import core.agent.builtin_tools as bt

    assert "forge_tool" not in bt.build_default_registry().all_names()

    monkeypatch.setattr(bt, "_forged_tools_enabled", lambda: True)
    assert "forge_tool" in bt.build_default_registry().all_names()


def test_absent_from_workbench_registry(forge_root, monkeypatch):
    import core.agent.builtin_tools as bt

    monkeypatch.setattr(bt, "_forged_tools_enabled", lambda: True)
    names = bt.build_default_registry(surface="workbench").all_names()
    assert "forge_tool" not in names


# --- the spawn seam ---------------------------------------------------------

def test_spawn_called_with_his_ask_verbatim(forge_root, spawn_seam):
    from core.agent.builtin_tools.forge_tool import ForgeTool

    tool = ForgeTool()
    ask = "can you convert this webp to png for me?"
    receipt = _execute(tool, {
        "name": "image_convert",
        "purpose": "convert images between formats",
        "original_ask": ask,
    })

    assert len(spawn_seam) == 1
    call = spawn_seam[0]
    assert call["name"] == "image_convert"
    assert call["original_ask"] == ask
    assert call["origin_channel_id"] == 777
    assert call["surface"] == "discord_text"
    assert "image_convert" in receipt
    assert "minute" in receipt.lower()


def test_owner_only_and_no_approval(forge_root):
    from core.agent.builtin_tools.forge_tool import ForgeTool

    tool = ForgeTool()
    assert tool.owner_only is True
    assert tool.requires_approval is False


# --- one active project at a time -------------------------------------------

def test_second_concurrent_ask_gets_queued_refusal(forge_root, spawn_seam):
    from core.agent.builtin_tools.forge_tool import ForgeTool

    tool = ForgeTool()
    first = _execute(tool, {
        "name": "tool_one", "purpose": "p1", "original_ask": "ask one",
    })
    second = _execute(tool, {
        "name": "tool_two", "purpose": "p2", "original_ask": "ask two",
    })

    assert len(spawn_seam) == 1  # only the first spawned
    assert "tool_one" in second  # names what is on the anvil
    assert "one" in second.lower()  # one-at-a-time, said plainly
    assert forge_manifest.active_project()["name"] == "tool_one"


def test_cleared_marker_frees_the_forge(forge_root, spawn_seam):
    from core.agent.builtin_tools.forge_tool import ForgeTool

    tool = ForgeTool()
    _execute(tool, {"name": "tool_one", "purpose": "p", "original_ask": "a"})
    forge_manifest.clear_active()
    _execute(tool, {"name": "tool_two", "purpose": "p", "original_ask": "a"})
    assert len(spawn_seam) == 2


def test_unusable_name_is_sanitized_not_fatal(forge_root, spawn_seam):
    from core.agent.builtin_tools.forge_tool import ForgeTool

    tool = ForgeTool()
    receipt = _execute(tool, {
        "name": "Convert Images!", "purpose": "p", "original_ask": "a",
    })
    assert len(spawn_seam) == 1
    assert spawn_seam[0]["name"] == "convert_images"
    assert "convert_images" in receipt
