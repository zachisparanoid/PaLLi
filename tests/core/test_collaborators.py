"""Collaborators (Batch 8, task 1 — B8-1; rulings R8, R9, R10, G3).

The owner names who counts as a collaborator in shared rooms. A
collaborator also sees the work she did in that room (the receipts); anyone
else there is a stranger and sees only what was said out loud. The list is
owner-managed, outside the repo's tracked files: ``data/collaborators.json``
(gitignored), re-read on every call, written atomically. Three ways in:
her owner-only ``collaborators`` tool, ``palli_ctl collaborators``, and
this module. Stand-in names only (Sam, Robin).
"""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from core import collaborators

REPO_ROOT = Path(__file__).resolve().parents[2]
SAM_DISCORD = 223344556677889900  # gitleaks:allow


@pytest.fixture
def store(tmp_path, monkeypatch) -> Path:
    path = tmp_path / "collaborators.json"
    monkeypatch.setenv("PALLI_COLLABORATORS_PATH", str(path))
    return path


# ── the store ─────────────────────────────────────────────────────────────

def test_nobody_is_a_collaborator_until_the_owner_names_them(store):
    assert not store.exists()
    assert collaborators.list_collaborators() == []
    assert collaborators.is_collaborator("discord", SAM_DISCORD) is False


def test_add_list_and_remove(store):
    assert collaborators.add_collaborator("discord", SAM_DISCORD, name="Sam") is True
    assert collaborators.is_collaborator("discord", SAM_DISCORD) is True
    assert collaborators.is_collaborator("discord", str(SAM_DISCORD)) is True   # ids compare as ids
    [entry] = collaborators.list_collaborators()
    assert entry["platform"] == "discord" and entry["user_id"] == str(SAM_DISCORD)
    assert entry["name"] == "Sam" and entry["added_at"]
    assert collaborators.remove_collaborator("discord", SAM_DISCORD) is True
    assert collaborators.is_collaborator("discord", SAM_DISCORD) is False
    assert collaborators.remove_collaborator("discord", SAM_DISCORD) is False   # already gone


def test_adding_twice_keeps_one_entry_and_refreshes_the_name(store):
    assert collaborators.add_collaborator("telegram", 5551234, name="Sam") is True
    assert collaborators.add_collaborator("telegram", 5551234, name="Sam B.") is False
    [entry] = collaborators.list_collaborators()
    assert entry["name"] == "Sam B."


def test_a_collaborator_is_named_per_door(store):
    collaborators.add_collaborator("telegram", 42)
    assert collaborators.is_collaborator("telegram", 42) is True
    assert collaborators.is_collaborator("discord", 42) is False


def test_platforms_are_the_two_chat_doors_and_ids_are_numbers(store):
    collaborators.add_collaborator(" Discord ", SAM_DISCORD)
    assert collaborators.is_collaborator("discord", SAM_DISCORD)
    with pytest.raises(ValueError):
        collaborators.add_collaborator("dashboard", 1)
    with pytest.raises(ValueError):
        collaborators.add_collaborator("discord", "Sam")
    # A question is never an error: an unknown door or a bad id is "no".
    assert collaborators.is_collaborator("voice", SAM_DISCORD) is False
    assert collaborators.is_collaborator("discord", "not-a-number") is False
    assert collaborators.is_collaborator(None, None) is False


def test_every_call_rereads_the_file(store):
    """Two writers share it (her tool and palli_ctl): a change made by one
    is seen by the other on its very next call — no cache, no restart."""
    collaborators.add_collaborator("discord", SAM_DISCORD, name="Sam")
    store.write_text(json.dumps([]), encoding="utf-8")       # the other writer removed Sam
    assert collaborators.is_collaborator("discord", SAM_DISCORD) is False


def test_the_write_is_atomic_and_leaves_no_temp_file(store):
    collaborators.add_collaborator("discord", SAM_DISCORD, name="Sam")
    assert json.loads(store.read_text(encoding="utf-8"))[0]["name"] == "Sam"
    assert [p.name for p in store.parent.iterdir()] == ["collaborators.json"]


def test_the_write_keeps_the_files_permissions(store):
    collaborators.add_collaborator("discord", SAM_DISCORD)
    os.chmod(store, 0o600)
    collaborators.add_collaborator("telegram", 42)
    assert (store.stat().st_mode & 0o777) == 0o600


def test_a_corrupt_file_makes_everyone_a_stranger_and_refuses_to_overwrite(store, caplog):
    store.write_text("{not json", encoding="utf-8")
    assert collaborators.is_collaborator("discord", SAM_DISCORD) is False   # fail closed
    with pytest.raises(collaborators.CollaboratorStoreError):
        collaborators.add_collaborator("discord", SAM_DISCORD)
    assert store.read_text(encoding="utf-8") == "{not json"                 # nothing lost
    assert "collaborators" in caplog.text


def test_the_suite_never_reads_or_writes_the_live_list():
    """G3: the conftest pins the store to the suite's tmp root (subprocesses
    included), never the repo's data/collaborators.json."""
    path = collaborators.store_path()
    assert path.resolve() != (REPO_ROOT / "data" / "collaborators.json").resolve()
    assert os.environ.get("PALLI_TEST_DATA_ROOT") and str(path).startswith(os.environ["PALLI_TEST_DATA_ROOT"])


def test_the_live_list_is_gitignored():
    lines = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "data/collaborators.json" in lines


# ── her tool ──────────────────────────────────────────────────────────────

def _ctx(surface: str = "discord_text", *, is_owner: bool = True):
    from core.agent.tools import ToolContext

    return ToolContext(turn_owner_id=1, is_owner=is_owner, surface=surface, channel_id=555)


def _tool():
    from core.agent.builtin_tools.collaborators import CollaboratorsTool

    return CollaboratorsTool()


def test_the_tool_is_owner_only_and_on_every_owner_belt():
    from core.agent.builtin_tools import build_default_registry

    tool = _tool()
    assert tool.name == "collaborators" and tool.owner_only is True
    reg = build_default_registry(surface="discord_text")
    assert "collaborators" in reg.all_names()
    assert "collaborators" in [s["name"] for s in reg.schemas_for(_ctx())]
    assert "collaborators" not in [s["name"] for s in reg.schemas_for(_ctx(is_owner=False))]
    assert "collaborators" not in build_default_registry(surface="workbench").all_names()


def test_the_description_frames_her_as_an_ai_participant_and_never_he():
    import re

    text = _tool().description
    assert "stranger" in text and "collaborator" in text
    assert not re.search(r"\b(he|him|his|himself)\b", text, re.IGNORECASE)
    assert "a person" not in text.lower()


@pytest.mark.asyncio
async def test_add_by_id_on_telegram_and_list(store):
    tool = _tool()
    out = await tool.execute({"action": "add", "who": "5551234"}, _ctx("telegram_text"))
    assert "5551234" in out
    assert collaborators.is_collaborator("telegram", 5551234)
    listed = await tool.execute({"action": "list"}, _ctx("telegram_text"))
    assert "5551234" in listed and "telegram" in listed


@pytest.mark.asyncio
async def test_a_name_on_telegram_asks_for_the_numeric_id(store):
    out = await _tool().execute({"action": "add", "who": "Sam"}, _ctx("telegram_text"))
    assert "numeric id" in str(out)
    assert collaborators.list_collaborators() == []


@pytest.mark.asyncio
async def test_a_name_on_discord_is_looked_up_among_the_members(store, monkeypatch):
    import core.agent.hands_runtime as hr

    sam = SimpleNamespace(id=SAM_DISCORD, name="sam_b", display_name="Sam")
    monkeypatch.setattr(hr, "resolve_member", lambda name: (sam, []) if name == "Sam" else (None, ["Sammy", "Samira"]))
    out = await _tool().execute({"action": "add", "who": "Sam"}, _ctx("discord_text"))
    assert "Sam" in out
    [entry] = collaborators.list_collaborators()
    assert entry == {**entry, "platform": "discord", "user_id": str(SAM_DISCORD), "name": "Sam"}
    unsure = await _tool().execute({"action": "add", "who": "Sa"}, _ctx("discord_text"))
    assert "Sammy" in str(unsure) and len(collaborators.list_collaborators()) == 1


@pytest.mark.asyncio
async def test_a_discord_mention_or_id_needs_no_lookup(store, monkeypatch):
    import core.agent.hands_runtime as hr

    monkeypatch.setattr(hr, "resolve_member", lambda name: pytest.fail("no lookup for an id"))
    await _tool().execute({"action": "add", "who": f"<@{SAM_DISCORD}>"}, _ctx("discord_text"))
    assert collaborators.is_collaborator("discord", SAM_DISCORD)


@pytest.mark.asyncio
async def test_remove_by_the_name_she_stored(store, monkeypatch):
    """Someone who left the server cannot be looked up any more; the name
    on the list still finds them."""
    import core.agent.hands_runtime as hr

    monkeypatch.setattr(hr, "resolve_member", lambda name: (None, []))
    collaborators.add_collaborator("discord", SAM_DISCORD, name="Sam")
    out = await _tool().execute({"action": "remove", "who": "sam"}, _ctx("discord_text"))
    assert "stranger" in out
    assert collaborators.list_collaborators() == []


@pytest.mark.asyncio
async def test_off_the_chat_doors_the_platform_must_be_named(store):
    out = await _tool().execute({"action": "add", "who": "5551234"}, _ctx("dashboard"))
    assert "platform" in str(out) and collaborators.list_collaborators() == []
    await _tool().execute({"action": "add", "who": "5551234", "platform": "telegram"}, _ctx("dashboard"))
    assert collaborators.is_collaborator("telegram", 5551234)


@pytest.mark.asyncio
async def test_the_registry_refuses_a_guest(store):
    from core.agent.session import ToolUseBlock
    from core.agent.tools import ToolRegistry

    reg = ToolRegistry()
    reg.register(_tool())
    res = await reg.dispatch(ToolUseBlock(id="c1", name="collaborators",
                                          input={"action": "add", "who": "5551234"}),
                             _ctx("telegram_text", is_owner=False))
    assert res.is_error and "owner-only" in res.content
    assert collaborators.list_collaborators() == []


# ── palli_ctl collaborators ───────────────────────────────────────────────

def _ctl():
    spec = importlib.util.spec_from_file_location(
        "palli_ctl_collaborators_test_module", REPO_ROOT / "scripts" / "palli_ctl.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(module, argv):
    args = module.build_parser().parse_args(["collaborators", *argv])
    lines: list[str] = []
    code = module.run_collaborators_command(args, out=lines.append)
    return code, lines


def test_palli_ctl_lists_adds_and_removes(store):
    module = _ctl()
    code, lines = _run(module, ["list"])
    assert code == 0 and "No collaborators" in lines[0]
    code, lines = _run(module, ["add", "discord", str(SAM_DISCORD), "Sam"])
    assert code == 0 and collaborators.is_collaborator("discord", SAM_DISCORD)
    code, lines = _run(module, ["list"])
    assert code == 0 and any("Sam" in line and str(SAM_DISCORD) in line for line in lines)
    code, lines = _run(module, ["remove", "discord", str(SAM_DISCORD)])
    assert code == 0 and not collaborators.is_collaborator("discord", SAM_DISCORD)
    code, lines = _run(module, ["remove", "discord", str(SAM_DISCORD)])
    assert code == 1


def test_palli_ctl_refuses_a_bad_door_or_id(store):
    module = _ctl()
    with pytest.raises(SystemExit):           # argparse: only the two chat doors
        _run(module, ["add", "dashboard", "1"])
    code, lines = _run(module, ["add", "telegram", "Sam"])
    assert code == 1 and collaborators.list_collaborators() == []


def test_palli_ctl_is_still_crlf_throughout():
    raw = (REPO_ROOT / "scripts" / "palli_ctl.py").read_bytes()
    assert raw.count(b"\n") == raw.count(b"\r\n")
