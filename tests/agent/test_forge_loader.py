"""The forge loader (step 34, spec §5): install-by-write IS hot install.

Pins: a manifest row + tool file under FORGE_ROOT loads into the very next
``build_default_registry()`` call (the per-turn registry makes it live with
no restart); sha256 mismatch refuses loudly and disables the entry on disk;
a broken module is contained alone — the others still load; flag off means
the loader NEVER runs and the registry is byte-identical to a build without
the forge; the workbench registry never carries forged tools (no recursive
forging from inside the wall).
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging

import pytest

from ai import forge_manifest
from core.agent.builtin_tools import forge_loader
from core.agent.session import ToolUseBlock
from core.agent.tools import ToolContext, ToolRegistry


VALID_TOOL_SRC = '''\
def build_tool():
    class ForgedEcho:
        name = "forged_echo"
        description = "echo back the word (test fixture)"
        input_schema = {
            "type": "object",
            "properties": {"word": {"type": "string"}},
            "required": ["word"],
        }
        requires_approval = False
        owner_only = True

        async def execute(self, input, ctx):
            return "echo: " + str(input.get("word", ""))

    return ForgedEcho()
'''

BROKEN_TOOL_SRC = 'raise RuntimeError("forged module broken at import")\n'


def _sha256(path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _entry(name: str, file: str, sha256: str, **overrides) -> dict:
    row = {
        "name": name,
        "file": file,
        "sha256": sha256,
        "created_at": "2026-08-21T04:30:00+00:00",
        "original_ask": "test ask",
        "test_report": "2 passed",
        "enabled": True,
        "consecutive_errors": 0,
    }
    row.update(overrides)
    return row


@pytest.fixture()
def forge_root(tmp_path, monkeypatch):
    root = tmp_path / "forge"
    (root / "tools").mkdir(parents=True)
    monkeypatch.setenv("FORGE_ROOT", str(root))
    return root


def _install_fixture_tool(forge_root, name="forged_echo", src=VALID_TOOL_SRC,
                          **entry_overrides):
    tool_file = forge_root / "tools" / f"{name}.py"
    tool_file.write_text(src, encoding="utf-8")
    sha = entry_overrides.pop("sha256", _sha256(tool_file))
    entries = forge_manifest.read_manifest()
    entries.append(_entry(name, f"{name}.py", sha, **entry_overrides))
    forge_manifest.write_manifest(entries)
    return tool_file


def _owner_ctx() -> ToolContext:
    return ToolContext(turn_owner_id=1, is_owner=True)


# --- loading + dispatch round-trip -----------------------------------------

def test_valid_forged_tool_loads_and_dispatches(forge_root):
    _install_fixture_tool(forge_root)

    tools = forge_loader.load_forged_tools()
    assert [t.name for t in tools] == ["forged_echo"]
    tool = tools[0]
    assert tool.owner_only is True
    assert tool.requires_approval is False

    reg = ToolRegistry()
    reg.register(tool)
    tu = ToolUseBlock(id="tu1", name="forged_echo", input={"word": "anvil"})
    result = asyncio.run(reg.dispatch(tu, _owner_ctx()))
    assert result.is_error is False
    assert "echo: anvil" in result.content


def test_forged_tool_owner_only_enforced(forge_root):
    _install_fixture_tool(forge_root)
    (tool,) = forge_loader.load_forged_tools()

    reg = ToolRegistry()
    reg.register(tool)
    tu = ToolUseBlock(id="tu2", name="forged_echo", input={"word": "x"})
    guest = ToolContext(turn_owner_id=1, is_owner=False)
    result = asyncio.run(reg.dispatch(tu, guest))
    assert result.is_error is True
    assert "owner-only" in result.content


# --- integrity + containment ------------------------------------------------

def test_sha256_mismatch_refuses_loudly(forge_root, caplog):
    _install_fixture_tool(forge_root, sha256="0" * 64)

    with caplog.at_level(logging.ERROR):
        tools = forge_loader.load_forged_tools()
    assert tools == []
    assert any("forged_echo" in r.getMessage() and "sha256" in r.getMessage().lower()
               for r in caplog.records if r.levelno >= logging.ERROR)

    on_disk = json.loads((forge_root / "manifest.json").read_text())
    assert on_disk[0]["enabled"] is False


def test_broken_import_contained_others_still_load(forge_root, caplog):
    _install_fixture_tool(forge_root, name="forged_broken", src=BROKEN_TOOL_SRC)
    _install_fixture_tool(forge_root)

    with caplog.at_level(logging.ERROR):
        tools = forge_loader.load_forged_tools()
    assert [t.name for t in tools] == ["forged_echo"]

    by_name = {e["name"]: e for e in forge_manifest.read_manifest()}
    assert by_name["forged_broken"]["enabled"] is False
    assert by_name["forged_echo"]["enabled"] is True


def test_disabled_entry_not_loaded(forge_root):
    _install_fixture_tool(forge_root, enabled=False)
    assert forge_loader.load_forged_tools() == []


# --- the flag gate: off = the forge never existed ---------------------------

def test_flag_off_loader_never_runs_registry_unchanged(forge_root, monkeypatch):
    _install_fixture_tool(forge_root)

    def _boom():
        raise AssertionError("load_forged_tools ran with the flag OFF")

    monkeypatch.setattr(forge_loader, "load_forged_tools", _boom)

    from core.agent.builtin_tools import build_default_registry
    names = build_default_registry().all_names()
    assert "forged_echo" not in names


# --- the hot-install pin: next turn's registry carries it --------------------

def test_hot_install_next_registry_carries_it(forge_root, monkeypatch):
    import core.agent.builtin_tools as bt

    monkeypatch.setattr(bt, "_forged_tools_enabled", lambda: True)

    before = bt.build_default_registry().all_names()
    assert "forged_echo" not in before

    _install_fixture_tool(forge_root)

    after = bt.build_default_registry().all_names()
    assert "forged_echo" in after


def test_workbench_registry_never_carries_forged(forge_root, monkeypatch):
    import core.agent.builtin_tools as bt

    monkeypatch.setattr(bt, "_forged_tools_enabled", lambda: True)
    _install_fixture_tool(forge_root)

    names = bt.build_default_registry(surface="workbench").all_names()
    assert "forged_echo" not in names


# --- the bug-belts (spec §4/§6): per-call timeout + three strikes -----------

FLAKY_TOOL_SRC = '''\
import asyncio


def build_tool():
    class ForgedFlaky:
        name = "forged_flaky"
        description = "raises or sleeps on demand (test fixture)"
        input_schema = {"type": "object", "properties": {"mode": {"type": "string"}}}
        requires_approval = False
        owner_only = True

        async def execute(self, input, ctx):
            mode = input.get("mode", "ok")
            if mode == "boom":
                raise RuntimeError("kaboom")
            if mode == "sleep":
                await asyncio.sleep(30)
            return "ok"

    return ForgedFlaky()
'''


def _flaky_registry(forge_root):
    _install_fixture_tool(forge_root, name="forged_flaky", src=FLAKY_TOOL_SRC)
    (tool,) = forge_loader.load_forged_tools()
    reg = ToolRegistry()
    reg.register(tool)
    return reg


def _dispatch(reg, mode: str):
    tu = ToolUseBlock(id=f"tu-{mode}", name="forged_flaky", input={"mode": mode})
    return asyncio.run(reg.dispatch(tu, _owner_ctx()))


def _flaky_entry(forge_root):
    return json.loads((forge_root / "manifest.json").read_text())[0]


def test_timeout_belt_returns_error_not_hang(forge_root, monkeypatch):
    from core.config import config

    monkeypatch.setattr(config, "FORGE_TOOL_TIMEOUT_SECONDS", 0.05)
    reg = _flaky_registry(forge_root)

    result = _dispatch(reg, "sleep")
    assert result.is_error is True
    assert "timed out" in result.content


def test_three_strikes_disable_hook_and_deed(forge_root, monkeypatch):
    fired: list[tuple[str, int]] = []
    deeds: list[tuple[str, str]] = []
    monkeypatch.setattr(forge_loader, "auto_disable_hook",
                        lambda name, count: fired.append((name, count)))
    monkeypatch.setattr(forge_loader, "record_deed",
                        lambda kind, detail, **kw: deeds.append((kind, detail)))
    reg = _flaky_registry(forge_root)

    for _ in range(3):
        result = _dispatch(reg, "boom")
        assert result.is_error is True

    entry = _flaky_entry(forge_root)
    assert entry["enabled"] is False
    assert entry["consecutive_errors"] == 3
    assert fired == [("forged_flaky", 3)]
    assert any(kind == "forge_auto_disable" and "forged_flaky" in detail
               for kind, detail in deeds)
    # a fourth error keeps counting but never refires the hook
    _dispatch(reg, "boom")
    assert fired == [("forged_flaky", 3)]


def test_success_between_errors_resets_counter(forge_root, monkeypatch):
    fired: list[tuple[str, int]] = []
    monkeypatch.setattr(forge_loader, "auto_disable_hook",
                        lambda name, count: fired.append((name, count)))
    reg = _flaky_registry(forge_root)

    _dispatch(reg, "boom")
    _dispatch(reg, "boom")
    ok = _dispatch(reg, "ok")
    assert ok.is_error is False
    _dispatch(reg, "boom")

    entry = _flaky_entry(forge_root)
    assert entry["enabled"] is True
    assert entry["consecutive_errors"] == 1
    assert fired == []


def test_timeout_counts_as_a_strike(forge_root, monkeypatch):
    from core.config import config

    monkeypatch.setattr(config, "FORGE_TOOL_TIMEOUT_SECONDS", 0.05)
    reg = _flaky_registry(forge_root)

    for _ in range(3):
        _dispatch(reg, "sleep")

    entry = _flaky_entry(forge_root)
    assert entry["enabled"] is False
    assert entry["consecutive_errors"] == 3


# --- trust with receipts (spec §6): whoami, the share note, his levers ------

def test_whoami_lists_forged_tools_with_birthdates(forge_root, monkeypatch):
    import core.agent.builtin_tools as bt
    from core.agent.builtin_tools.whoami import WhoamiTool

    monkeypatch.setattr(bt, "_forged_tools_enabled", lambda: True)
    _install_fixture_tool(forge_root)

    tool = WhoamiTool(loop_description="ReAct",
                      available_tool_names=["forged_echo", "whoami"])
    out = asyncio.run(tool.execute({}, _owner_ctx()))
    assert "forged" in out.lower()
    assert "forged_echo" in out
    assert "2026-08-21" in out  # the birthdate from created_at


def test_whoami_forged_section_absent_when_flag_off(forge_root, monkeypatch):
    import core.agent.builtin_tools as bt
    from core.agent.builtin_tools.whoami import WhoamiTool

    monkeypatch.setattr(bt, "_forged_tools_enabled", lambda: False)
    _install_fixture_tool(forge_root)

    tool = WhoamiTool(loop_description="ReAct",
                      available_tool_names=["forged_echo", "whoami"])
    out = asyncio.run(tool.execute({}, _owner_ctx()))
    assert "2026-08-21" not in out


def test_whoami_guest_never_hears_about_the_forge(forge_root, monkeypatch):
    import core.agent.builtin_tools as bt
    from core.agent.builtin_tools.whoami import WhoamiTool
    from core.agent.tools import ToolContext

    monkeypatch.setattr(bt, "_forged_tools_enabled", lambda: True)
    _install_fixture_tool(forge_root)

    tool = WhoamiTool(loop_description="ReAct",
                      available_tool_names=["forged_echo", "whoami"])
    guest = ToolContext(turn_owner_id=1, is_owner=False)
    out = asyncio.run(tool.execute({}, guest))
    assert "2026-08-21" not in out


def test_auto_disable_default_hook_reaches_share_lane(forge_root, monkeypatch):
    """The default hook (no engine wiring needed): three strikes ⇒ a plain
    note rides the share lane so HE hears it — never silent benching."""
    shares: list[dict] = []

    async def _fake_share(**kwargs):
        shares.append(kwargs)
        return {"disposition": "sent"}

    async def _fake_refs():
        return {"app_user_id": 42}

    monkeypatch.setattr("ai.arbiter.deliver_share", _fake_share)
    monkeypatch.setattr("core.local_mode.get_local_owner_refs", _fake_refs)

    _install_fixture_tool(forge_root, name="forged_flaky", src=FLAKY_TOOL_SRC)
    (tool,) = forge_loader.load_forged_tools()
    reg = ToolRegistry()
    reg.register(tool)

    async def _three_strikes():
        for _ in range(3):
            tu = ToolUseBlock(id="tu", name="forged_flaky",
                              input={"mode": "boom"})
            await reg.dispatch(tu, _owner_ctx())
        # let the fire-and-forget note task run before the loop closes
        await asyncio.sleep(0)
        await asyncio.sleep(0)

    asyncio.run(_three_strikes())

    assert len(shares) == 1
    assert "forged_flaky" in shares[0]["text"]
    assert shares[0]["owner_user_id"] == 42


def _load_ctl():
    import importlib.util
    from pathlib import Path

    module_path = (Path(__file__).resolve().parents[2] / "scripts"
                   / "palli_ctl.py")
    spec = importlib.util.spec_from_file_location(
        "palli_ctl_forge_test_module", module_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_ctl_forge_list_revoke_restore(forge_root, capsys):
    module = _load_ctl()
    _install_fixture_tool(forge_root)

    assert module.main(["forge", "list"]) == 0
    out = capsys.readouterr().out
    assert "forged_echo" in out
    assert "enabled" in out.lower()

    assert module.main(["forge", "revoke", "forged_echo"]) == 0
    assert forge_manifest.get_entry("forged_echo")["enabled"] is False

    assert module.main(["forge", "list"]) == 0
    assert "revoked" in capsys.readouterr().out.lower()

    assert module.main(["forge", "restore", "forged_echo"]) == 0
    entry = forge_manifest.get_entry("forged_echo")
    assert entry["enabled"] is True
    assert entry["consecutive_errors"] == 0

    assert module.main(["forge", "revoke", "no_such_tool"]) == 1


# ── the browser_hand freeze (2026-08-24 10:48) ────────────────────────────
# Her forged browser did its waiting with time.sleep and blocking sockets
# inside an async execute — ON the brain loop. The whole process froze ~40 s
# (Discord heartbeat blocked 10 s/20 s, gateway RESUMED), and the per-call
# ceiling could not fire: wait_for cannot preempt a blocked loop. Laws:
# a forged tool runs on its OWN worker thread with its own loop — blocking
# code blocks only itself — and the ceiling actually fires on a blocked tool.

BLOCKING_TOOL_SRC = '''\
import time


def build_tool():
    class ForgedBlocker:
        name = "forged_blocker"
        description = "blocks the way browser_hand did (test fixture)"
        input_schema = {"type": "object", "properties": {}}
        requires_approval = False
        owner_only = True

        async def execute(self, input, ctx):
            time.sleep(float(input.get("sleep", 0.6)))   # the sin itself
            return "done"

    return ForgedBlocker()
'''


@pytest.mark.asyncio
async def test_a_blocking_forged_tool_no_longer_stalls_the_brain_loop(forge_root, monkeypatch):
    monkeypatch.setattr("core.config.config.FORGE_TOOL_TIMEOUT_SECONDS", 10, raising=False)
    _install_fixture_tool(forge_root, name="forged_blocker", src=BLOCKING_TOOL_SRC)
    belt = {t.name: t for t in forge_loader.load_forged_tools()}["forged_blocker"]

    ticks = 0

    async def ticker():
        nonlocal ticks
        while True:
            await asyncio.sleep(0.05)
            ticks += 1

    t = asyncio.get_running_loop().create_task(ticker())
    try:
        result = await belt.execute({"sleep": 0.6}, None)
    finally:
        t.cancel()
    assert result == "done"
    assert ticks >= 5, f"the loop was frozen while the tool blocked (ticks={ticks})"


@pytest.mark.asyncio
async def test_the_ceiling_actually_fires_on_a_blocked_tool_and_strikes(forge_root, monkeypatch):
    monkeypatch.setattr("core.config.config.FORGE_TOOL_TIMEOUT_SECONDS", 0.3, raising=False)
    _install_fixture_tool(forge_root, name="forged_blocker", src=BLOCKING_TOOL_SRC)
    belt = {t.name: t for t in forge_loader.load_forged_tools()}["forged_blocker"]

    started = asyncio.get_running_loop().time()
    with pytest.raises(TimeoutError):
        await belt.execute({"sleep": 6.0}, None)
    elapsed = asyncio.get_running_loop().time() - started
    assert elapsed < 4.0, f"the ceiling took {elapsed:.1f}s — it must fire while the tool is still blocked"
    entry = json.loads((forge_root / "manifest.json").read_text())[0]
    assert int(entry.get("consecutive_errors", 0)) == 1
