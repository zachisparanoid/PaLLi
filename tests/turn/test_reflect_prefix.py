"""The reflection's system message (batch 2, task 4, 2026-09-27; trimmed by
batch 5, task 11, 2026-09-28).

After every reply her post-turn reflection used to send ONE user message with
her whole identity rendering (~63k tokens) pasted into the template's
``{identity_excerpt}`` slot — uncached, every turn. Task 3 split the reply
lane's system prompt into a stable prefix and a volatile tail; batch 2 sent
that SAME stable prefix as the reflection's ``system`` message so the provider
could serve it from cache, and the identity slot became a one-line pointer.
Measured, only 26% of it hit — so batch 5, task 11 (ruling R53) sends three
fields of the reply's own AgentLoopContext instead: the identity core, the
memory core with the preferences, and the corrections of record. Not the
constitution, not ``## How I talk``, not the skills, not the person line.

Pinned here: the system message is exactly those three fields joined; the
pointer replaces the inline identity exactly once; and every path that cannot
build the system message (no identity, a builder that raises, an empty
result) keeps today's one-message call, byte for byte.
"""
from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from core.identity.models import CompiledIdentity, IdentityDocument
from core.turn import reflect as reflect_module
from core.turn.types import Turn, TurnContext, TurnMetadata

POINTER = "(Your identity, your pages and your memory of them are in the system message above.)"
TEMPLATE = "Current identity state (excerpts):\n{identity_excerpt}\n\nUser message:\n{user_message}"


def _real_ctx() -> TurnContext:
    turn = Turn(
        surface="discord_text",
        owner_id="1",
        content="hey",
        metadata=TurnMetadata(origin="user", channel_id="123"),
        received_at=datetime(2026, 9, 27, 12, 0),
    )
    identity = CompiledIdentity(
        core={
            "core_identity": IdentityDocument(
                doc_key="core_identity", path="x", frontmatter={}, content="I am PaLLi."
            )
        },
        owner={
            "memory": IdentityDocument(
                doc_key="memory",
                path="y",
                frontmatter={},
                content="## Durable Context\n- a\n\n## Active Threads\n- b\n",
            )
        },
    )
    return TurnContext(turn=turn, compiled_identity=identity)


def _no_identity_ctx():
    return SimpleNamespace(
        compiled_identity=None,
        turn=SimpleNamespace(owner_id=42, content="hey", received_at=datetime(2026, 9, 27, 12, 0)),
        unfulfilled_first_contact_targets=[],
        turn_read=None,
    )


class _FakeLLM:
    def __init__(self, reply):
        self.reply, self.calls = reply, []

    async def chat_completion(self, **kw):
        self.calls.append(kw)
        return self.reply


def _harness(fake):
    return (
        patch.object(reflect_module.prompt_loader, "load_prompt", return_value=TEMPLATE),
        patch.object(reflect_module, "brain", SimpleNamespace(list_active_open_threads=AsyncMock(return_value=[]))),
        patch.object(reflect_module, "_resolve_reflect_model", lambda: "fake-model"),
        patch.object(reflect_module, "llm", fake),
    )


@pytest.mark.asyncio
async def test_reflection_sends_the_stable_prefix_as_system():
    ctx = _real_ctx()
    fake = _FakeLLM(reply="{}")
    a, b, c, d = _harness(fake)
    with a, b, c, d:
        await reflect_module._extract_plan(ctx, "some reply")
    assert len(fake.calls) == 1
    msgs = fake.calls[0]["messages"]
    assert [m["role"] for m in msgs] == ["system", "user"]
    prefix = reflect_module._reflection_system_for(ctx)
    assert prefix and "### Core Identity" in prefix and "## Active Threads" in prefix
    assert msgs[0]["content"] == prefix
    # Batch 5, task 11 (ruling R28): the memory core's footer rides along, the
    # voice rule does not, and on this fixture the whole message is small.
    assert "is looked up when a moment calls for it" in msgs[0]["content"]
    assert "## How I talk" not in msgs[0]["content"]
    assert len(msgs[0]["content"]) < 20_000
    assert "### Core Identity" not in msgs[1]["content"]
    assert msgs[1]["content"].count(POINTER) == 1
    # The rest of the call is untouched: same lane, model, temperature, budget.
    call = fake.calls[0]
    assert call["log_context"] == "reflect_turn"
    assert call["model"] == "fake-model"
    assert call["temperature"] == 0.3
    assert call["max_tokens"] == 4000


def test_reflection_system_is_the_three_fields_of_the_reply_context():
    """Ruling R53 (batch 5, task 11): the reflection builds the reply's own
    AgentLoopContext and joins exactly ``identity_core_text``,
    ``owner_memory_text`` (the memory core plus the preferences) and
    ``corrections_text`` with blank lines, dropping empties — nothing else
    of the stable prefix. Re-premised from "equals the reply lane's whole
    stable prefix" (batch 2, task 4), which this task ends."""
    from core.agent.context_adapter import build_agent_loop_context

    ctx = _real_ctx()
    loop_ctx = build_agent_loop_context(ctx.turn, ctx, is_owner=True)
    parts = (loop_ctx.identity_core_text, loop_ctx.owner_memory_text, loop_ctx.corrections_text)
    expected = "\n\n".join(p for p in parts if p and p.strip())
    assert expected
    assert reflect_module._reflection_system_for(ctx) == expected


def test_reflection_system_leaves_out_the_person_line_and_skills():
    """Ruling R53: the point of the trim. The reply's stable prefix carries
    her person line and the skills catalog (and the voice rule, pinned
    above); the reflection's system message carries none of them. The
    markers are proven on the fixture's own reply prefix first, so this
    can never pass by asserting the absence of something that was never
    there."""
    from core.agent.context_adapter import build_agent_loop_context
    from core.agent.system_prompt import build_system_prompt_parts

    ctx = _real_ctx()
    loop_ctx = build_agent_loop_context(ctx.turn, ctx, is_owner=True)
    reply_stable = build_system_prompt_parts(loop_ctx, cadence_directive="")[0]
    assert loop_ctx.person_line and loop_ctx.person_line in reply_stable
    assert loop_ctx.skills_text and loop_ctx.skills_text in reply_stable
    system = reflect_module._reflection_system_for(ctx)
    assert system
    assert loop_ctx.person_line not in system
    assert loop_ctx.skills_text not in system
    assert "Your person:" not in system
    assert "<capabilities>" not in system


@pytest.mark.asyncio
async def test_no_identity_keeps_todays_single_message():
    fake = _FakeLLM(reply="{}")
    a, b, c, d = _harness(fake)
    with a, b, c, d:
        await reflect_module._extract_plan(_no_identity_ctx(), "some reply")
    msgs = fake.calls[0]["messages"]
    assert msgs == [
        {"role": "user", "content": TEMPLATE.format(identity_excerpt="(no identity loaded)", user_message="hey")}
    ]


@pytest.mark.asyncio
async def test_a_prefix_that_raises_falls_back_to_todays_shape():
    ctx = _real_ctx()
    fake = _FakeLLM(reply="{}")
    a, b, c, d = _harness(fake)
    boom = patch("core.agent.context_adapter.build_agent_loop_context", side_effect=RuntimeError("boom"))
    with a, b, c, d, boom:
        assert reflect_module._reflection_system_for(ctx) == ""
        await reflect_module._extract_plan(ctx, "some reply")
    msgs = fake.calls[0]["messages"]
    assert msgs == [
        {"role": "user", "content": TEMPLATE.format(identity_excerpt=ctx.compiled_identity.text_prompt(), user_message="hey")}
    ]


def test_a_prefix_that_raises_says_so_at_info(caplog):
    """The fall back is visible: INFO, and the exception by repr so one with
    no message still names itself (whole-branch review, 2026-09-27)."""
    import logging

    ctx = _real_ctx()
    boom = patch("core.agent.context_adapter.build_agent_loop_context", side_effect=RuntimeError())
    with boom, caplog.at_level(logging.INFO, logger=reflect_module.logger.name):
        assert reflect_module._reflection_system_for(ctx) == ""
    lines = [r for r in caplog.records if "stable prefix unavailable" in r.getMessage()]
    assert len(lines) == 1
    assert lines[0].levelno == logging.INFO
    assert lines[0].getMessage().endswith("RuntimeError()")


@pytest.mark.asyncio
async def test_an_empty_prefix_falls_back_to_todays_shape():
    ctx = _real_ctx()
    fake = _FakeLLM(reply="{}")
    a, b, c, d = _harness(fake)
    empty = patch.object(reflect_module, "_reflection_system_for", lambda _ctx: "")
    with a, b, c, d, empty:
        await reflect_module._extract_plan(ctx, "some reply")
    msgs = fake.calls[0]["messages"]
    assert msgs == [
        {"role": "user", "content": TEMPLATE.format(identity_excerpt=ctx.compiled_identity.text_prompt(), user_message="hey")}
    ]


def test_reflection_system_carries_the_corrections_band_after_the_memory_band():
    """Task 11 (fix wave, M1): the third field is pinned, not only counted.
    With corrections standing, the reflection's system message carries the
    corrections block the adapter renders, and it comes after the memory
    band — the same order the reply's stable prefix keeps."""
    block = '## CORRECTIONS OF RECORD — these outrank anything older\n- since 2026-09-28: NOT "a" — TRUE: b'
    ctx = _real_ctx()
    with patch("core.agent.context_adapter._render_corrections", lambda _ctx, *, is_owner=True: block):
        system = reflect_module._reflection_system_for(ctx)
    assert system.endswith(block)
    assert "### Core Identity" in system
    assert system.index("## Active Threads") < system.index(block)
    # and without one standing, the message ends on the memory band
    assert reflect_module._reflection_system_for(ctx).endswith("is looked up when a moment calls for it.)")
