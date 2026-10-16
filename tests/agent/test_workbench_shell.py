"""The guarded autonomous shell (spec §4/§4c). The OS wall (palli-work) is
activation infrastructure — tests exercise the wrapper: belt denylist,
timeout + process-group kill, output clipping, logging. WORKBENCH_SHELL_DIRECT=1
runs commands as the test user so no sudo/user setup is needed."""
from __future__ import annotations

import asyncio

import pytest

from core.agent.tool_outcome import ToolFailure
from core.agent.tools import ToolContext


def _ctx():
    return ToolContext(turn_owner_id=1, is_owner=True, surface="workbench")


def _tool(monkeypatch, tmp_path):
    monkeypatch.setenv("WORKBENCH_SHELL_DIRECT", "1")
    monkeypatch.setenv("WORKBENCH_ROOT", str(tmp_path))
    from core.agent.builtin_tools.workbench_shell import WorkbenchShellTool

    return WorkbenchShellTool()


@pytest.mark.asyncio
async def test_runs_a_command_in_the_workspace(monkeypatch, tmp_path):
    tool = _tool(monkeypatch, tmp_path)
    out = await tool.execute({"command": "pwd && echo hi > note.txt && cat note.txt"}, _ctx())
    assert str(tmp_path) in out and "hi" in out
    assert (tmp_path / "note.txt").read_text().strip() == "hi"


@pytest.mark.asyncio
async def test_belt_denies_substrate_paths(monkeypatch, tmp_path):
    tool = _tool(monkeypatch, tmp_path)
    for cmd in (
        "cat /home/ubuntu/projects/chatbot/palli-local-refactor/.env",
        "rm -rf /home/ubuntu/projects/chatbot/palli-local-refactor/data",
        "ls ~/projects/chatbot/palli-local-refactor/identity",
    ):
        out = str(await tool.execute({"command": cmd}, _ctx()))
        assert "declined" in out.lower()


@pytest.mark.asyncio
async def test_belt_denies_privilege_and_nas_writes_outside_her_folder(monkeypatch, tmp_path):
    monkeypatch.setenv("PALLI_NAS_ROOT", "/mnt/example-nas")
    monkeypatch.delenv("WORKBENCH_NAS_DIR", raising=False)
    tool = _tool(monkeypatch, tmp_path)
    out = str(await tool.execute({"command": "sudo whoami"}, _ctx()))
    assert "declined" in out.lower()
    out = str(await tool.execute(
        {"command": "mv /mnt/example-nas/audio/x /tmp/x"}, _ctx()))
    assert "declined" in out.lower()
    # her own NAS folder is NOT belted (the wall + mount perms govern there)
    ok = await tool.execute(
        {"command": "echo would-write /mnt/example-nas/palli/thing"}, _ctx())
    assert "declined" not in ok.lower()


@pytest.mark.asyncio
async def test_no_nas_configured_leaves_the_nas_rung_inert(monkeypatch, tmp_path):
    """Step 28 task 2 — PALLI_NAS_ROOT empty = there is no NAS. The belt then
    has nothing to guard, so the NAS rung stays silent; critically it must not
    fire on EVERY command, which an unguarded `"" in command` would do. Her NAS
    folder is nothing, and the decline line stops offering her one."""
    monkeypatch.setenv("PALLI_NAS_ROOT", "")
    monkeypatch.delenv("WORKBENCH_NAS_DIR", raising=False)
    from core.agent.builtin_tools import workbench_shell as m

    assert m._her_nas_folder() == ""
    assert m._belt_reason("mv /mnt/anything/x /tmp/x") == ""
    tool = _tool(monkeypatch, tmp_path)
    out = str(await tool.execute({"command": "sudo whoami"}, _ctx()))
    assert "declined" in out.lower() and "NAS folder" not in out


def test_the_description_promises_a_nas_folder_only_when_one_exists(monkeypatch):
    """I1 — the tool description is the schema text the MODEL reads. On a
    no-NAS install it must not tell her she can work "in your NAS folder";
    with a root configured it must, exactly as before. Built per instance,
    so a session picks up the knob without a code change."""
    from core.agent.builtin_tools.workbench_shell import WorkbenchShellTool

    monkeypatch.delenv("WORKBENCH_NAS_DIR", raising=False)
    monkeypatch.setenv("PALLI_NAS_ROOT", "")
    assert "NAS" not in WorkbenchShellTool().description
    monkeypatch.setenv("PALLI_NAS_ROOT", "/mnt/example-nas")
    described = WorkbenchShellTool().description
    assert "and in your NAS folder" in described
    assert "/mnt/example-nas" not in described      # capability, never the path


@pytest.mark.asyncio
async def test_timeout_kills_the_process_group(monkeypatch, tmp_path):
    monkeypatch.setenv("WORKBENCH_SHELL_TIMEOUT_S", "1")
    tool = _tool(monkeypatch, tmp_path)
    raw = await tool.execute({"command": "sleep 30 & sleep 30"}, _ctx())
    # The TYPE as well as the wording (the final review's I3): a timeout handed
    # back as a plain string reads as a SUCCESS at dispatch, and str() alone
    # would not notice the reversion.
    assert isinstance(raw, ToolFailure)
    assert "timed out" in str(raw).lower()


@pytest.mark.asyncio
async def test_output_clipped(monkeypatch, tmp_path):
    tool = _tool(monkeypatch, tmp_path)
    out = await tool.execute({"command": "yes x | head -c 100000"}, _ctx())
    assert len(out) < 20000


def test_argv_crosses_the_user_boundary_in_production(monkeypatch, tmp_path):
    """The wall's plumbing (spec §4b): production execs through
    `sudo -n -u palli-work` so the OS enforces the body guard and sudo's
    env_reset keeps the app's keys out of her shell. Never exercised by
    running it (the build has no sudo) — pinned by shape instead."""
    monkeypatch.delenv("WORKBENCH_SHELL_DIRECT", raising=False)
    from core.agent.builtin_tools import workbench_shell as m

    argv = m._build_argv("ls", root=str(tmp_path), user="palli-work", direct=False)
    assert argv[:5] == ["sudo", "-n", "-u", "palli-work", "--"]
    assert argv[5] == "bash"
    script = argv[-1]
    # The dedicated-user path caps her process count (spec §4c runaway
    # containment); the shared-UID test path must NOT — see below.
    assert "ulimit -u" in script
    assert str(tmp_path) in script and script.endswith("ls")


def test_direct_mode_omits_the_per_user_process_cap(monkeypatch, tmp_path):
    """RLIMIT_NPROC counts THREADS per uid, system-wide. Under
    WORKBENCH_SHELL_DIRECT the uid is the app's own (hundreds of threads),
    so a 256-process cap would succeed silently and then make every fork
    in her shell fail. A per-uid cap is only coherent on a dedicated uid."""
    from core.agent.builtin_tools import workbench_shell as m

    argv = m._build_argv("ls", root=str(tmp_path), user="palli-work", direct=True)
    assert argv[:2] == ["bash", "-c"]
    script = argv[-1]
    assert "ulimit -u" not in script
    # the per-PROCESS limits are safe on any uid and stay
    assert "ulimit -f" in script and "ulimit -t" in script


# --- The substrate root is DERIVED, not hardcoded (2026-07-29, her-own-user
# spec §4). Her app is about to move from /home/ubuntu/projects/chatbot/... to
# /srv/palli/..., and a hardcoded belt would then guard a path that no longer
# exists while ignoring the real one. Deriving it from this module's own
# location makes the belt correct at ANY install path, forever.


def test_substrate_root_is_derived_from_the_module_location():
    from pathlib import Path

    from core.agent.builtin_tools.workbench_shell import _substrate_root

    # This file lives at <root>/core/agent/builtin_tools/workbench_shell.py
    expected = Path(__file__).resolve().parents[2]
    assert _substrate_root() == expected
    assert (expected / "core" / "agent").is_dir()      # it really is the app root


def test_substrate_root_env_override(monkeypatch):
    from pathlib import Path

    from core.agent.builtin_tools.workbench_shell import _substrate_root

    monkeypatch.setenv("PALLI_SUBSTRATE_ROOT", "/srv/palli/palli-local-refactor")
    assert _substrate_root() == Path("/srv/palli/palli-local-refactor")


@pytest.mark.asyncio
async def test_belt_denies_the_derived_substrate_at_any_path(monkeypatch, tmp_path):
    """The post-migration shape: with her app at /srv/palli, commands reaching
    THERE are declined and the stale /home/ubuntu literal is irrelevant."""
    monkeypatch.setenv("PALLI_SUBSTRATE_ROOT", "/srv/palli/palli-local-refactor")
    tool = _tool(monkeypatch, tmp_path)
    for cmd in (
        "cat /srv/palli/palli-local-refactor/.env",
        "rm -rf /srv/palli/palli-local-refactor/data",
    ):
        out = str(await tool.execute({"command": cmd}, _ctx()))
        assert "declined" in out.lower(), cmd


@pytest.mark.asyncio
async def test_belt_still_denies_the_repo_name_anywhere(monkeypatch, tmp_path):
    """The name-based rung survives the move: any path form of the app repo
    stays declined even if the derived root is elsewhere."""
    monkeypatch.setenv("PALLI_SUBSTRATE_ROOT", "/srv/palli/palli-local-refactor")
    tool = _tool(monkeypatch, tmp_path)
    out = str(await tool.execute(
        {"command": "ls ~/projects/chatbot/palli-local-refactor/identity"}, _ctx()))
    assert "declined" in out.lower()


def test_her_shell_falls_back_to_the_same_workspace_every_other_site_uses(monkeypatch):
    """The final review's I2. Her shell defaulted to a workspace under the OWNER's home
    — a path that exists on the machine this was built on only because the setup script
    leaves a convenience symlink there. The forge, the workbench engine and the
    logbook all defaulted to `/home/palli-work/workspace` instead, so on any host
    without that symlink her shell ran in `/tmp` (`_build_argv`'s fallback) while the
    three of them looked in her real workspace: she would make things that vanish.

    One constant now, imported by all four, so they cannot drift apart again."""
    import ai.forge as forge
    import ai.workbench as wb
    import ai.workbench_trace as trace
    from core.agent.builtin_tools import workbench_shell as shell

    monkeypatch.delenv("WORKBENCH_ROOT", raising=False)
    assert wb.WORKBENCH_ROOT_DEFAULT == "/home/palli-work/workspace"
    assert shell.WORKBENCH_ROOT_DEFAULT is wb.WORKBENCH_ROOT_DEFAULT
    assert forge.WORKBENCH_ROOT_DEFAULT is wb.WORKBENCH_ROOT_DEFAULT
    assert trace.WORKBENCH_ROOT_DEFAULT is wb.WORKBENCH_ROOT_DEFAULT
    # and each site's own resolution lands there with the knob unset
    assert shell._workspace_root() == wb.WORKBENCH_ROOT_DEFAULT
    assert str(forge._workspace_root()) == wb.WORKBENCH_ROOT_DEFAULT


def test_the_shell_still_honours_the_workbench_root_knob(monkeypatch, tmp_path):
    """The knob is what a stranger (and his own .env.local) sets; the default is only
    what happens when nobody has."""
    from core.agent.builtin_tools import workbench_shell as shell

    monkeypatch.setenv("WORKBENCH_ROOT", str(tmp_path))
    assert shell._workspace_root() == str(tmp_path)
