"""A reminder turn replays only a small window of its own past fires
(Batch 4 task 1, audit item 1.7).

A reminder fire runs on its own session (``reminder_fire:<owner>``), which
holds nothing but her earlier fires. Under the chat cap (the owner's .env.local: 400)
plus the Batch 2 step (50), every fire replayed 400-449 messages of old
reminders. It now has its own cap, ``PALLI_AGENT_HISTORY_MAX_MESSAGES_REMINDER``
(default 40), and no step: fires are hours apart, so a stepped start buys no
cache reuse there — the window is exactly the cap.
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

from core.agent.runner import _resolve_history_max_messages


def test_reminder_sessions_get_a_small_window(monkeypatch):
    monkeypatch.delenv("PALLI_AGENT_HISTORY_MAX_MESSAGES_REMINDER", raising=False)
    assert _resolve_history_max_messages(surface="reminder_fire") == 40
    monkeypatch.setenv("PALLI_AGENT_HISTORY_MAX_MESSAGES_REMINDER", "12")
    assert _resolve_history_max_messages(surface="reminder_fire") == 12
    assert _resolve_history_max_messages(surface="discord_text") == _resolve_history_max_messages()


def test_the_reminder_cap_falls_back_the_way_the_chat_cap_does(monkeypatch):
    """Blank or garbage → the default, like PALLI_AGENT_HISTORY_MAX_MESSAGES;
    and the reminder knob never moves a chat turn's cap."""
    monkeypatch.setenv("PALLI_AGENT_HISTORY_MAX_MESSAGES", "400")
    for raw in ("", "forty"):
        monkeypatch.setenv("PALLI_AGENT_HISTORY_MAX_MESSAGES_REMINDER", raw)
        assert _resolve_history_max_messages(surface="reminder_fire") == 40
    monkeypatch.setenv("PALLI_AGENT_HISTORY_MAX_MESSAGES_REMINDER", "12")
    assert _resolve_history_max_messages(surface="discord_text") == 400
    assert _resolve_history_max_messages(surface="telegram_text") == 400
    assert _resolve_history_max_messages() == 400


# ── the runner's payload: exactly the cap for a fire, stepped for a chat ─────

def _load():
    from tests.agent.conftest import load_module
    return load_module("core/agent/runner.py", "agent_runner_under_test")


@dataclass
class _Turn:
    surface: str = "discord_text"
    owner_id: int = 1
    content: str = "hello"
    metadata: Any = None
    received_at: Any = None


@dataclass
class _Ctx:
    turn: Any = None
    user_text: str = "hello"
    recent_history_text: str = ""
    identity_core_text: str = "I'm PaLLi."
    skills_text: str = ""
    runtime_text: str = ""
    memory_text: str = ""
    facts_text: str = ""
    is_owner: bool = True


def _payload_for(turn, session_key, tmp_path) -> list:
    """Run one turn over a 64-message session and return the LLM payload."""
    mod = _load()
    import core.agent.session as sess_mod
    import core.agent.llm_client as llm_mod_real

    pre = asyncio.run(sess_mod.Session.open_or_create(session_key, root=tmp_path))
    for i in range(64):
        role = "user" if i % 2 == 0 else "assistant"
        asyncio.run(pre.append(sess_mod.AgentMessage(
            role=role, content=[sess_mod.TextBlock(text=f"m{i}")],
        )))

    seen: dict = {}

    async def fake_call(**kw):
        seen["messages"] = kw["messages"]
        return llm_mod_real.LLMResponse(
            text_blocks=["ok"], tool_uses=[], stop_reason="end_turn",
            prompt_tokens=1, completion_tokens=1,
        )

    async def fake_recall(t): return _Ctx(turn=t)
    async def fake_cadence(prompt):
        return json.dumps({"should_respond": True, "register": "neutral", "length": "short",
                           "phase": "opening", "target_beats": 1, "observation_text": "ok"})

    orig_call = llm_mod_real.call
    llm_mod_real.call = fake_call
    try:
        deps = mod.RunnerDeps(
            build_turn_context=fake_recall, cadence_llm=fake_cadence,
            sessions_root=tmp_path, provider="openai_compat", model="m",
            client=None, tool_registry=None, polling_tools=set(),
        )
        result = asyncio.run(mod.run_turn(turn, deps=deps, on_chunk=None))
    finally:
        llm_mod_real.call = orig_call
    assert result.responded is True
    return seen["messages"]


def test_a_reminder_fire_window_is_exactly_the_cap_while_a_chat_window_still_steps(
        tmp_path, monkeypatch):
    # 64 pre-existing + the turn's own user message = 65; cap 11 → start 54,
    # which is a user message, so no snap moves it.
    monkeypatch.setenv("PALLI_AGENT_HISTORY_MAX_MESSAGES", "11")
    monkeypatch.setenv("PALLI_AGENT_HISTORY_MAX_MESSAGES_REMINDER", "11")
    monkeypatch.delenv("PALLI_AGENT_HISTORY_STEP", raising=False)  # the default step, 50

    fire = _Turn(surface="reminder_fire", content="[REMINDER FIRE] stretch",
                 metadata=SimpleNamespace(origin="reminder", reminder_id=42))
    payload = _payload_for(fire, "reminder_fire:1", tmp_path / "fire")
    assert payload[0].content[0].text == "m54"
    assert len(payload) == 11

    # The same session shape on a chat door keeps the Batch 2 step: the
    # start floors to the 50-message boundary.
    chat = _payload_for(_Turn(), "discord_text:1", tmp_path / "chat")
    assert chat[0].content[0].text == "m50"
    assert len(chat) == 15
