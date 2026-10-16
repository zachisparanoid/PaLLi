"""The reflection's request is cacheable from turn to turn (the owner, 10-05:
"make the reflection cacheable").

DeepSeek bills a request's leading tokens at the cached price when they
match an earlier request's leading tokens exactly. Measured from her logs on
10-05: every reflection sent ~60-66k input tokens and ~58k of them missed —
``[reflect_turn] cache_hit≈6k cache_miss≈58k`` — because the template opened
its user message with this turn's own words (``User message:`` then
``PaLLi's reply:``), so the first differing byte sat a few hundred characters
into a ~234,000-character message. Only the system message (identity core,
memory core, corrections, ~25,000 characters) ever hit.

Now the template puts the static instructions first, then the blocks that
change only when her memory changes (rarest first), and this turn's own
words, her inner read of it and the clock last. Pinned here, with the real
template and the real system-message builder: two consecutive reflections
of a moving conversation share at least 85% of their characters as a common
prefix, and the output contract (every JSON key the parser reads) is
unchanged.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from ai.turn_read import TurnRead
from core.identity.models import CompiledIdentity, IdentityDocument
from core.open_threads import OpenThread, ThreadKind, ThreadStatus
from core.turn import reflect as reflect_module
from core.turn.types import Turn, TurnContext, TurnMetadata

ROOT = Path(__file__).resolve().parents[2]
TEMPLATE_PATH = ROOT / "prompts" / "turn_reflect.txt"


# ── A realistic, moving conversation ────────────────────────────────────
# Sizes are scaled down from the live 10-05 request (durable context ~40k
# chars, curated threads ~45k, open threads ~126k) but keep its proportions
# roughly: the memory blocks dwarf one turn's words.

def _memory_doc() -> str:
    durable = "\n".join(
        f"- They told me about project {i}: a long-running build with its own quirks "
        f"and a deadline that keeps moving ({2026}-09-{(i % 28) + 1:02d})."
        for i in range(60)
    )
    threads = "\n".join(
        f"- [2026-09-{(i % 28) + 1:02d}] Follow up on the garden bed {i} and whether "
        f"the drip line held through the cold snap."
        for i in range(40)
    )
    return (
        "## Durable Context\n"
        f"{durable}\n\n"
        "## Active Threads\n"
        f"{threads}\n\n"
        "## Important Decisions\n"
        "- Sticking with Postgres for the side project.\n\n"
        "## Useful References\n"
        "- No durable references recorded yet.\n"
    )


def _identity() -> CompiledIdentity:
    return CompiledIdentity(
        core={
            "core_identity": IdentityDocument(
                doc_key="core_identity", path="x", frontmatter={},
                content="I am PaLLi. I'm an AI, and I say so plainly.",
            )
        },
        owner={
            "memory": IdentityDocument(
                doc_key="memory", path="y", frontmatter={}, content=_memory_doc(),
            ),
            "relationship_state": IdentityDocument(
                doc_key="relationship_state", path="z",
                frontmatter={
                    "relationship_phase": "established",
                    "trust_level": "high",
                    "current_posture": "warm",
                    "relationship_summary": "Long nights, shared builds, plain talk.",
                },
                content="",
            ),
        },
    )


def _open_threads() -> list[OpenThread]:
    return [
        OpenThread(
            id=f"thr-{i:04d}", owner_user_id=1, status=ThreadStatus.ACTIVE,
            created_at=1_700_000_000.0 + i, updated_at=1_700_000_000.0 + i,
            topic_summary=f"They are waiting on part {i} for the printer rebuild",
            why_it_matters="They asked twice and want to know when it ships.",
            kind=ThreadKind.WORK, urgency="no urgency",
            last_surfaced_at=None, surface_count=0,
            resolved_at=None, resolution_reason=None,
        )
        for i in range(80)
    ]


# Three consecutive exchanges, a minute or two apart, each with its own
# words, reply and inner read: the conversation moves every turn.
_EXCHANGES = [
    (
        "ok the drip line held, but the north bed is still soggy. thinking of raising it",
        "Good news on the drip line. Raising the north bed would help it drain; "
        "a few inches of gravel under the soil does a lot of the work.",
        TurnRead(read="Relieved, a little tired, planning out loud.", weight=0.3,
                 needs_support=0.1, carries_forward="the north bed plan",
                 memory_worthy="", created_at=0.0),
    ),
    (
        "yeah gravel makes sense. also the printer part finally shipped!!",
        "That's the one you've been waiting on for weeks. Want me to keep an eye "
        "on the tracking and tell you when it's out for delivery?",
        TurnRead(read="Happy, the long wait is over.", weight=0.4,
                 needs_support=0.0, carries_forward="", memory_worthy="",
                 created_at=0.0),
    ),
    (
        "sure. ok going to bed, talk tomorrow",
        "Sleep well. I'll watch the tracking.",
        TurnRead(read="Winding down for the night.", weight=0.2,
                 needs_support=0.0, carries_forward="", memory_worthy="",
                 created_at=0.0),
    ),
]


def _ctx(identity: CompiledIdentity, content: str, when: datetime, read: TurnRead) -> TurnContext:
    turn = Turn(
        surface="discord_text",
        owner_id="1",
        content=content,
        metadata=TurnMetadata(origin="user", channel_id="123"),
        received_at=when,
    )
    return TurnContext(turn=turn, compiled_identity=identity, turn_read=read)


class _FakeLLM:
    def __init__(self):
        self.calls: list[dict] = []

    async def chat_completion(self, **kw):
        self.calls.append(kw)
        return '{"skip_reason": "small_talk"}'


async def render_reflection_requests() -> list[list[dict]]:
    """The full request (every message) the reflection sends for each of
    the three exchanges above, with the real template and the real
    system-message builder; only the provider, the model name and the
    open-threads store are fakes."""
    identity = _identity()
    threads = _open_threads()
    fake = _FakeLLM()
    start = datetime(2026, 10, 5, 22, 14, 3)
    with (
        patch.object(reflect_module, "brain",
                     SimpleNamespace(list_active_open_threads=AsyncMock(return_value=threads))),
        patch.object(reflect_module, "_resolve_reflect_model", lambda: "fake-model"),
        patch.object(reflect_module, "llm", fake),
    ):
        for i, (said, reply, read) in enumerate(_EXCHANGES):
            ctx = _ctx(identity, said, start + timedelta(minutes=2 * i, seconds=17 * i), read)
            await reflect_module._extract_plan(ctx, reply)
    return [call["messages"] for call in fake.calls]


def _flat(messages: list[dict]) -> str:
    """One request as the provider tokenizes it, in order: role, then content."""
    return "".join(f"<{m['role']}>{m['content']}" for m in messages)


def _common_prefix(a: str, b: str) -> int:
    n = min(len(a), len(b))
    i = 0
    while i < n and a[i] == b[i]:
        i += 1
    return i


# Why 85%. Measured 10-05 on this fixture: 23.5% before the reorder (the
# first difference was this turn's words, right after the intro) and 99.2%
# / 99.5% after (the first difference is the clock line under "## This
# turn"). At her live sizes the same reorder takes the share from 9.7% to
# ~99.5%. 85% leaves room for longer turns in this fixture, and still fails
# if any per-turn line moves back above a memory block: one per-turn line
# just above the open threads drops this fixture to ~80%, one above the
# inputs to ~55%.
_MIN_SHARED_PREFIX = 0.85


@pytest.mark.asyncio
async def test_consecutive_reflections_share_most_of_their_prefix():
    requests = await render_reflection_requests()
    assert len(requests) == 3
    for n in range(1, len(requests)):
        a, b = _flat(requests[n - 1]), _flat(requests[n])
        assert a != b  # the conversation really moved
        share = _common_prefix(a, b) / len(b)
        assert share >= _MIN_SHARED_PREFIX, f"turn {n} -> {n + 1}: {share:.1%}"


@pytest.mark.asyncio
async def test_the_system_message_does_not_change_between_turns():
    requests = await render_reflection_requests()
    systems = [msgs[0] for msgs in requests]
    assert all(m["role"] == "system" for m in systems)
    assert systems[0]["content"] and len({m["content"] for m in systems}) == 1


@pytest.mark.asyncio
async def test_this_turn_comes_after_everything_that_does_not_move():
    """The static instructions open the user message; this turn's words,
    her reply, her inner read and the clock all sit under "## This turn",
    after the last memory block."""
    requests = await render_reflection_requests()
    for (said, reply, read), msgs in zip(_EXCHANGES, requests):
        user = msgs[-1]["content"]
        assert user.startswith("You are PaLLi's reflective writer.")
        turn_at = user.index("\n## This turn\n")
        last_thread_at = user.index("thr-0079")
        assert user.index("## Output Schema") < user.index("\n## Inputs\n") < last_thread_at < turn_at
        for per_turn in (said, reply, read.read, "Current date and time:"):
            assert user.index(per_turn) > turn_at, per_turn
        assert user.rstrip().endswith("Respond with STRICT JSON only, as the Output Schema above describes.")


@pytest.mark.asyncio
async def test_the_inner_read_left_the_relationship_block():
    """It used to be appended to the relationship state, which made that
    block differ every turn. Same words, now in the turn block."""
    requests = await render_reflection_requests()
    blocks = []
    for msgs in requests:
        user = msgs[-1]["content"]
        start = user.index("Current relationship state:\n")
        blocks.append(user[start:user.index("\n\n", start + len("Current relationship state:\n"))])
        assert "inner read of THIS turn" not in blocks[-1]
    assert len(set(blocks)) == 1
    first = requests[0][-1]["content"]
    assert ("PaLLi's inner read of THIS turn (emotional weight 0.30, needs_support 0.10): "
            "Relieved, a little tired, planning out loud.\n"
            "Worth holding onto / circling back on later: the north bed plan "
            "(consider an open thread if no active thread covers it)\n") in first


@pytest.mark.asyncio
async def test_no_inner_read_leaves_no_bytes_of_its_own():
    identity = _identity()
    fake = _FakeLLM()
    ctx = TurnContext(
        turn=Turn(surface="discord_text", owner_id="1", content="hi",
                  metadata=TurnMetadata(origin="user", channel_id="123"),
                  received_at=datetime(2026, 10, 5, 22, 0)),
        compiled_identity=identity,
    )
    with (
        patch.object(reflect_module, "brain",
                     SimpleNamespace(list_active_open_threads=AsyncMock(return_value=[]))),
        patch.object(reflect_module, "_resolve_reflect_model", lambda: "fake-model"),
        patch.object(reflect_module, "llm", fake),
    ):
        await reflect_module._extract_plan(ctx, "hello")
    user = fake.calls[0]["messages"][-1]["content"]
    # (the prompt loader trims the file's last newline)
    assert user.endswith(
        'PaLLi\'s reply:\n"hello"\n\n'
        "Respond with STRICT JSON only, as the Output Schema above describes."
    )


# The output contract, in order, exactly as the template carried it before
# the reorder: every key the parser (_parse_json_payload/_plan_from_payload)
# reads from the template's own schema. Moving blocks must never touch it.
_SCHEMA_KEYS = [
    "memory_updates", "durable_context_add", "durable_context_retire",
    "active_threads_add", "active_threads_resolve", "important_decisions_add",
    "useful_references_add", "personality_observations", "relationship_delta",
    "trust_shift", "posture_shift", "summary_revision", "user_facts_add", "key",
    "value", "confidence", "monologue_note", "first_contact_resolutions",
    "user_name", "companion_style", "autonomy_preset", "checkin_posture",
    "voice_posture", "relationship_style", "owner_pronouns", "skip_reason",
    "open_threads_to_add", "topic_summary", "why_it_matters", "kind", "urgency",
    "open_threads_to_close", "thread_id", "resolution_reason",
    "session_close_signal", "session_close_reason",
]


def test_the_output_contract_is_unchanged():
    text = TEMPLATE_PATH.read_text(encoding="utf-8")
    schema = text[text.index("## Output Schema"):text.index("## Session close detection")]
    assert re.findall(r'"(\w+)":', schema) == _SCHEMA_KEYS
    assert "Respond with STRICT JSON only (no markdown, no code fences, no extra text):" in schema


def test_the_contract_round_trips_through_the_parser():
    """A payload built from the pinned keys parses into the plan fields."""
    payload = {
        "memory_updates": {"durable_context_add": ["They grow tomatoes."]},
        "relationship_delta": {"trust_shift": "+1", "posture_shift": None, "summary_revision": None},
        "user_facts_add": [{"key": "has_garden", "value": "yes", "confidence": 0.9}],
        "monologue_note": "ask about the north bed",
        "first_contact_resolutions": {"user_name": "Sam"},
        "skip_reason": None,
        "open_threads_to_add": [{"topic_summary": "north bed", "why_it_matters": "soggy",
                                 "kind": "personal", "urgency": "no urgency"}],
        "open_threads_to_close": [{"thread_id": "thr-0001", "resolution_reason": "shipped"}],
        "session_close_signal": True,
        "session_close_reason": "going_to_bed",
    }
    import json

    plan = reflect_module._plan_from_payload(reflect_module._parse_json_payload(json.dumps(payload)))
    assert plan.memory_updates.durable_context_add == ["They grow tomatoes."]
    assert plan.relationship_delta.trust_shift == "+1"
    assert plan.user_facts_add[0].key == "has_garden"
    assert plan.monologue_note == "ask about the north bed"
    assert plan.first_contact_resolutions == {"user_name": "Sam"}
    assert plan.open_threads_to_add and plan.open_threads_to_close
    assert plan.session_close_signal is True and plan.session_close_reason == "going_to_bed"


