"""Stake-ledger read path: TurnContext -> adapter flattening -> system prompt,
plus the flag-off purity guarantee and the co-celebration register text.

The render chain has a known scar (fields computed but never flattened reach
nobody — the caring-memory 2026-06-13 blank-fields bug), so these tests pin
every hop.
"""
from __future__ import annotations

import pytest

from core.agent.context_adapter import build_agent_loop_context
from core.agent.system_prompt import build_system_prompt
from core.turn.types import Turn, TurnContext, TurnMetadata


def _turn(content: str = "hey") -> Turn:
    return Turn(
        surface="discord_text",
        owner_id="1",
        content=content,
        # the owner's private chat: in a group his stakes stay out (the
        # presence slice, 10-13; tests/agent/test_presence_slice.py)
        metadata=TurnMetadata(origin="user", channel_id="123", is_dm=True),
    )


def _ctx(**overrides) -> TurnContext:
    base = dict(turn=_turn())
    base.update(overrides)
    return TurnContext(**base)


def test_turncontext_accepts_held_stakes_with_default():
    """Legacy constructors (pipeline path) that don't pass the field keep working."""
    ctx = _ctx()
    assert ctx.held_stakes_text == ""
    ctx2 = _ctx(held_stakes_text="- (hope, held 2d) I'm hoping the survey lands clean.")
    assert "survey" in ctx2.held_stakes_text


def test_adapter_flattens_held_stakes_text():
    """The scar test: a TurnContext field that isn't flattened by the adapter
    never reaches the system prompt. Pin the flattening hop explicitly."""
    stakes = "- (hope, held 2d) I'm hoping the survey lands clean."
    loop_ctx = build_agent_loop_context(_turn(), _ctx(held_stakes_text=stakes))
    assert loop_ctx.held_stakes_text == stakes


def test_system_prompt_renders_first_person_stakes_block():
    stakes = "- (hope, held 2d, horizon in 1d) I'm hoping the survey lands clean."
    loop_ctx = build_agent_loop_context(_turn(), _ctx(held_stakes_text=stakes))
    prompt = build_system_prompt(loop_ctx, cadence_directive="")
    assert "## What's on your mind (your own hopes and worries)" in prompt
    assert "I'm hoping the survey lands clean." in prompt
    # The restraint instruction rides with the block.
    assert "Never recite the list" in prompt


def test_system_prompt_omits_stakes_section_when_empty():
    """Flag-off purity at the prompt layer: no stakes -> no section header,
    prompt identical to a context without the field at all."""
    loop_no_field = build_agent_loop_context(_turn(), _ctx())
    loop_empty = build_agent_loop_context(_turn(), _ctx(held_stakes_text=""))
    p1 = build_system_prompt(loop_no_field, cadence_directive="")
    p2 = build_system_prompt(loop_empty, cadence_directive="")
    assert "## What's on your mind (your own hopes and worries)" not in p1
    assert p1 == p2


def test_ledger_enabled_is_default_off(monkeypatch):
    """ledger_enabled() is THE single gate for the faculty (recall, dispatch,
    monologue, proactive all import it at call time). Default-OFF semantics:
    unbooted settings (None) or a missing field mean DISABLED — deliberately
    inverted from caring_memory's None->enabled."""
    from types import SimpleNamespace

    import core.turn.companion_flags as flags

    # The autouse _stake_ledger_off_by_default fixture patches
    # ai.stake_ledger.ledger_enabled itself — load a FRESH copy of the module
    # so we exercise the real gate implementation, not the test stub.
    from tests.agent.conftest import load_module

    ledger_enabled = load_module(
        "ai/stake_ledger.py", "stake_ledger_gate_under_test"
    ).ledger_enabled

    # Unbooted settings -> off.
    monkeypatch.setattr(flags, "companion_settings", lambda: None)
    assert ledger_enabled() is False

    # Booted, flag explicitly off -> off.
    monkeypatch.setattr(
        flags, "companion_settings", lambda: SimpleNamespace(stake_ledger=False)
    )
    assert ledger_enabled() is False

    # Field missing entirely (old snapshot shape) -> off.
    monkeypatch.setattr(flags, "companion_settings", lambda: SimpleNamespace())
    assert ledger_enabled() is False

    # Booted and on -> on.
    monkeypatch.setattr(
        flags, "companion_settings", lambda: SimpleNamespace(stake_ledger=True)
    )
    assert ledger_enabled() is True

    # Flag-reader failure -> off (fail closed).
    def _boom():
        raise RuntimeError("settings exploded")

    monkeypatch.setattr(flags, "companion_settings", _boom)
    assert ledger_enabled() is False


@pytest.mark.asyncio
async def test_turn_read_template_and_kwarg_in_lockstep():
    """The str.format KeyError trap: prompts/turn_read.txt and take_turn_read's
    kwargs must stay in sync or the read dies silently. Render the real
    template with the real kwargs."""
    from ai.prompt_loader import prompt_loader

    template = prompt_loader.load_prompt("turn_read")
    assert template, "prompts/turn_read.txt missing"
    assert "{held_stakes}" in template
    rendered = template.format(
        message="m",
        recent_conversation="rc",
        relationship_posture="rp",
        emotional_arc="ea",
        held_stakes="- (hope, held 1d) I'm hoping.",
    )
    assert "I'm hoping." in rendered
    # Output contract unchanged: same five JSON fields, no stake fields.
    for field in ("read", "weight", "needs_support", "carries_forward", "memory_worthy"):
        assert f'"{field}"' in rendered
    assert '"stake' not in rendered


def test_register_allows_co_celebration_keeps_de_mirror():
    """Owner preference (2026-07-06): match his upward excitement; never
    chameleon personality or absorb lows."""
    loop_ctx = build_agent_loop_context(
        _turn(), _ctx(register_directive="He sounds worn down — go gentle.")
    )
    text = loop_ctx.register_directive
    assert "excited WITH" in text
    assert "co-celebration" in text
    assert "Don't chameleon" in text
    assert "Don't adopt their mood, slang, or energy." not in text
    # The care directive itself still rides along.
    assert "go gentle" in text
