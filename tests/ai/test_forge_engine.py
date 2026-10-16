"""The forge engine (step 34, spec §3/§5): walled draft, mechanical green,
harvest — and the honest null.

Pins (session runner + wall test run stubbed; no live LLM, no sudo, tmp
roots): the green path installs (file copied, manifest row correct, sha256
matches bytes, filing archived) and fires the follow-up seam with
fulfill-context; the red path installs NOTHING, archives the filing, fires
the honest-null follow-up; both budgets are enforced (minutes pre-checked,
attempts bounded); the active marker clears on every exit — including a
mid-flight exception (never a stuck "forging forever"); her_time is held
while the forge runs; forge sessions ride their own ReAct round cap, never
her own-work knob.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
from types import SimpleNamespace

import pytest

from ai import forge_manifest, her_time


DRAFT_TOOL_SRC = '''\
def build_tool():
    class EchoWord:
        name = "echo_word"
        description = "echo a word back"
        input_schema = {"type": "object", "properties": {"word": {"type": "string"}}}
        requires_approval = False
        owner_only = True

        async def execute(self, input, ctx):
            return str(input.get("word", ""))

    return EchoWord()
'''

DRAFT_TEST_SRC = '''\
import asyncio
from echo_word import build_tool

def main():
    tool = build_tool()
    out = asyncio.run(tool.execute({"word": "hi"}, None))
    assert out == "hi"
    print("1 passed")

if __name__ == "__main__":
    main()
'''


@pytest.fixture(autouse=True)
def _fresh_her_time():
    her_time.reset_for_tests()
    yield
    her_time.reset_for_tests()


@pytest.fixture()
def forge_root(tmp_path, monkeypatch):
    root = tmp_path / "forge-install"
    (root / "tools").mkdir(parents=True)
    monkeypatch.setenv("FORGE_ROOT", str(root))
    return root


@pytest.fixture()
def workspace(tmp_path, monkeypatch):
    ws = tmp_path / "workspace"
    (ws / "forge").mkdir(parents=True)
    monkeypatch.setenv("WORKBENCH_ROOT", str(ws))
    return ws


@pytest.fixture()
def engine(monkeypatch, forge_root, workspace):
    """ai.forge with its seams stubbed; returns a namespace of recorders."""
    import ai.forge as forge

    rec = SimpleNamespace(
        dispatches=[], followups=[], deeds=[], busy_during_dispatch=[],
        wall_runs=[],
        draft_on_first_dispatch=True,
        wall_result=(0, "1 passed"),
        dispatch_error=None,
    )

    async def _fake_dispatch(turn):
        rec.dispatches.append(turn)
        rec.busy_during_dispatch.append(her_time.busy())
        if rec.dispatch_error is not None:
            raise rec.dispatch_error
        if rec.draft_on_first_dispatch and len(rec.dispatches) == 1:
            proj = workspace / "forge" / "echo_word"
            proj.mkdir(parents=True, exist_ok=True)
            (proj / "echo_word.py").write_text(DRAFT_TOOL_SRC, encoding="utf-8")
            (proj / "test_echo_word.py").write_text(DRAFT_TEST_SRC, encoding="utf-8")
        return SimpleNamespace(response_text="drafted; signing off")

    async def _fake_wall_run(name):
        rec.wall_runs.append(name)
        return rec.wall_result

    async def _fake_followup(**kwargs):
        rec.followups.append(kwargs)

    async def _fake_refs():
        return {"app_user_id": 42, "palli_user_id": 7}

    monkeypatch.setattr(forge, "_dispatch", _fake_dispatch)
    monkeypatch.setattr(forge, "_wall_test_run", _fake_wall_run)
    monkeypatch.setattr(forge, "_followup", _fake_followup)
    monkeypatch.setattr(forge, "_get_owner_refs", _fake_refs)
    monkeypatch.setattr(
        forge, "record_deed", lambda kind, detail, **kw: rec.deeds.append((kind, detail))
    )
    rec.forge = forge
    return rec


def _run(engine, **overrides):
    kwargs = dict(
        name="echo_word", purpose="echo a word",
        original_ask="echo hi back to me", origin_channel_id=777,
        surface="discord_text",
    )
    kwargs.update(overrides)
    forge_manifest.claim_active(kwargs["name"], kwargs["original_ask"])
    asyncio.run(engine.forge._run_forge_project(**kwargs))


# --- green: install + fulfill-context --------------------------------------

def test_green_path_installs_and_fires_followup(engine, forge_root, workspace):
    _run(engine)

    installed = forge_root / "tools" / "echo_word.py"
    assert installed.read_text(encoding="utf-8") == DRAFT_TOOL_SRC

    (row,) = forge_manifest.read_manifest()
    assert row["name"] == "echo_word"
    assert row["file"] == "echo_word.py"
    assert row["sha256"] == hashlib.sha256(installed.read_bytes()).hexdigest()
    assert row["enabled"] is True
    assert row["consecutive_errors"] == 0
    assert row["original_ask"] == "echo hi back to me"
    assert "1 passed" in row["test_report"]

    filing = forge_root / "filings" / "echo_word"
    assert (filing / "echo_word.py").exists()
    assert (filing / "test_echo_word.py").exists()
    assert "1 passed" in (filing / "test_report.txt").read_text(encoding="utf-8")
    assert "echo hi back to me" in (filing / "ask.txt").read_text(encoding="utf-8")

    (fu,) = engine.followups
    assert fu["green"] is True
    assert fu["name"] == "echo_word"
    assert fu["original_ask"] == "echo hi back to me"
    assert fu["origin_channel_id"] == 777

    assert forge_manifest.active_project() is None
    kinds = [k for k, _ in engine.deeds]
    assert "forge_started" in kinds
    assert "forge_installed" in kinds
    # her time was held while the session ran
    assert engine.busy_during_dispatch and all(engine.busy_during_dispatch)


def test_reforge_replaces_manifest_row(engine, forge_root):
    _run(engine)
    engine.dispatches.clear()
    _run(engine)
    rows = [e for e in forge_manifest.read_manifest() if e["name"] == "echo_word"]
    assert len(rows) == 1


# --- red: the honest null ----------------------------------------------------

def test_red_path_archives_without_install(engine, forge_root):
    engine.wall_result = (1, "AssertionError: nope")
    _run(engine)

    assert not (forge_root / "tools" / "echo_word.py").exists()
    assert forge_manifest.read_manifest() == []

    filing = forge_root / "filings" / "echo_word"
    assert (filing / "echo_word.py").exists()  # her draft, archived anyway
    assert "nope" in (filing / "test_report.txt").read_text(encoding="utf-8")

    (fu,) = engine.followups
    assert fu["green"] is False
    assert fu["why"]
    assert forge_manifest.active_project() is None
    assert any(k == "forge_failed" for k, _ in engine.deeds)


# --- budgets -----------------------------------------------------------------

def test_attempt_budget_bounds_the_iterate_loop(engine):
    engine.wall_result = (1, "still red")
    _run(engine)
    assert len(engine.dispatches) == engine.forge._MAX_VERIFY_ATTEMPTS
    (fu,) = engine.followups
    assert fu["green"] is False


def test_minutes_budget_enforced_before_any_session(engine, monkeypatch):
    from core.config import config

    monkeypatch.setattr(config, "FORGE_SESSION_MAX_MINUTES", 0)
    _run(engine)
    assert engine.dispatches == []
    (fu,) = engine.followups
    assert fu["green"] is False
    assert "clock" in fu["why"].lower()
    assert forge_manifest.active_project() is None


# --- mid-flight exception: never a stuck forge -------------------------------

def test_midflight_exception_clears_marker_and_reports(engine, forge_root):
    engine.dispatch_error = RuntimeError("provider died")
    _run(engine)
    assert forge_manifest.active_project() is None
    assert not (forge_root / "tools" / "echo_word.py").exists()
    (fu,) = engine.followups
    assert fu["green"] is False


# --- fulfill-after-forge (spec §7): the answer on the ask's own channel ------

@pytest.fixture()
def followup(monkeypatch, forge_root, workspace):
    """The REAL _followup with its dispatch/outbound/refs seams stubbed."""
    import ai.forge as forge

    rec = SimpleNamespace(dispatches=[], outbound=[], response_text="here it is")

    async def _fake_dispatch(turn):
        rec.dispatches.append(turn)
        return SimpleNamespace(response_text=rec.response_text)

    async def _fake_outbound(**kwargs):
        rec.outbound.append(kwargs)
        return True

    async def _fake_refs():
        return {"app_user_id": 42, "palli_user_id": 7}

    monkeypatch.setattr(forge, "_dispatch", _fake_dispatch)
    monkeypatch.setattr(forge, "_record_outbound", _fake_outbound)
    monkeypatch.setattr(forge, "_get_owner_refs", _fake_refs)
    rec.forge = forge
    return rec


def _run_followup(rec, **overrides):
    kwargs = dict(
        green=True, name="echo_word", original_ask="echo hi back to me",
        origin_channel_id=777, surface="discord_text", why="",
    )
    kwargs.update(overrides)
    asyncio.run(rec.forge._followup(**kwargs))


def test_followup_green_dispatches_and_sends(followup):
    _run_followup(followup)

    (turn,) = followup.dispatches
    assert turn.surface == "forge_followup"
    assert turn.metadata.origin == "forge"
    assert turn.metadata.channel_id == 777
    assert turn.owner_id == 42  # app_user_id — the owner-gate lesson
    assert "echo_word" in turn.content
    assert "echo hi back to me" in turn.content  # his ask, verbatim
    assert "belt" in turn.content  # the tool is live NOW — she is told so

    (out,) = followup.outbound
    assert out["channel_id"] == 777
    assert out["text"] == "here it is"
    assert out["origin"] == "forge"
    assert out["owner_id"] == 42


def test_followup_goes_out_of_the_door_the_ask_came_in(followup):
    """2026-10-14: an ask made in the owner's Telegram chat was answered as a
    Discord send to the Telegram chat's id."""
    _run_followup(followup, surface="telegram_text", origin_channel_id=-1005)

    (out,) = followup.outbound
    assert out["platform"] == "telegram"
    assert out["channel_id"] == -1005


def test_followup_from_discord_names_discord(followup):
    _run_followup(followup)
    (out,) = followup.outbound
    assert out["platform"] == "discord"


def test_followup_red_carries_the_honest_null(followup):
    _run_followup(followup, green=False, why="tests kept failing")

    (turn,) = followup.dispatches
    assert "didn't come together" in turn.content
    assert "tests kept failing" in turn.content
    assert "echo hi back to me" in turn.content
    # never a manufactured success
    assert "tests passed" not in turn.content
    (out,) = followup.outbound
    assert out["text"] == "here it is"


def test_followup_without_channel_never_sends(followup):
    _run_followup(followup, origin_channel_id=0)
    assert len(followup.dispatches) == 1
    assert followup.outbound == []


def test_followup_empty_response_sends_nothing(followup):
    followup.response_text = ""
    _run_followup(followup)
    assert followup.outbound == []


def test_forge_module_never_imports_the_arbiter():
    """Spec §7: the follow-up is CONVERSATION on his open ask — the arbiter
    and its lanes are untouched. Pinned at the source level."""
    from pathlib import Path

    import ai.forge as forge

    src = Path(forge.__file__).read_text(encoding="utf-8")
    assert "from ai.arbiter" not in src
    assert "import arbiter" not in src
    assert "deliver_share" not in src


# --- the forge session's round cap (dispatch seam) ---------------------------

def test_forge_rounds_cap_rides_forge_origin():
    from core.agent import dispatch
    from core.config import config

    forge_turn = SimpleNamespace(
        surface="workbench", metadata=SimpleNamespace(origin="forge")
    )
    assert dispatch._workbench_round_cap(forge_turn) == int(
        config.FORGE_SESSION_MAX_ROUNDS
    )

    own_turn = SimpleNamespace(
        surface="workbench", metadata=SimpleNamespace(origin="workbench")
    )
    cap = dispatch._workbench_round_cap(own_turn)
    assert cap is not None and cap != int(config.FORGE_SESSION_MAX_ROUNDS)
