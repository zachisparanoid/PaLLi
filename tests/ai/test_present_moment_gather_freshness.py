"""Impulse-freshness layer 1 (spec §2): the glance keeps HER proof.

The 08-06 first-send incident: _conversation_tail kept the FIRST 300 chars
of every message. Her replies are wind-up-first, delivery-last, so the
surviving fragment read as intent — she asked for inputs to a task she had
delivered 14 minutes earlier. Her messages now keep the END, marked as a
fragment; his keep the head (no observed failure mode); short messages are
byte-identical both roles.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch


def _fake_history(rows):
    async def fake(*, user_id, limit, include_metadata):
        return rows
    return fake


def _run_tail(rows):
    import ai.present_moment_gather as g

    async def fake_refs():
        return {"palli_user_id": 1}

    with patch("core.local_mode.get_local_owner_refs",
               new=AsyncMock(side_effect=fake_refs)), \
         patch("core.database.brain.get_conversation_history",
               new=AsyncMock(side_effect=_fake_history(rows))):
        return asyncio.run(g._conversation_tail(1))


def test_her_long_message_keeps_the_end_with_elision_mark():
    windup = "Let me grab the freshest state before I record — " * 8
    proof = "Here's your update: Step 18 shipped overnight, D1 closed, suite green."
    rows = [{"role": "assistant", "content": windup + proof, "timestamp": 0.0}]
    tail = _run_tail(rows)
    assert proof in tail, "the delivery end must survive the trim"
    assert "[me] …" in tail, "an elided fragment of herself must be MARKED"
    # Head-keep would open the line with the wind-up; end-keep must not.
    assert "[me] Let me grab" not in tail


def test_his_long_message_unchanged_head_keep():
    head = "Hey can you check the drive health on the PVE box right quick — "
    rows = [{"role": "user", "content": head + ("x" * 400), "timestamp": 0.0}]
    tail = _run_tail(rows)
    assert head in tail
    assert "[them] …" not in tail


def test_short_messages_byte_identical_both_roles():
    rows = [
        {"role": "user", "content": "thankee", "timestamp": 0.0},
        {"role": "assistant", "content": "Welcome! 🦉⚡", "timestamp": 0.0},
    ]
    tail = _run_tail(rows)
    assert "[them] thankee" in tail and "[me] Welcome! 🦉⚡" in tail
    assert "…" not in tail


# ── layer 2's sense (spec §3): WHAT YOU'VE DONE RECENTLY ──────────────────

def test_deeds_section_renders_with_ages(tmp_path, monkeypatch):
    import ai.present_moment_gather as g
    from ai import recent_deeds as rd

    monkeypatch.setattr(rd, "DEEDS_PATH", tmp_path / "recent_deeds.jsonl")
    rd.record_deed("deliver_artifact", "delivered a file: roadmap.mp3")
    out = asyncio.run(g._deeds())
    assert "WHAT YOU'VE DONE RECENTLY" in out
    assert "delivered a file: roadmap.mp3" in out
    assert "ago" in out


def test_deeds_section_absent_when_empty(tmp_path, monkeypatch):
    import ai.present_moment_gather as g
    from ai import recent_deeds as rd

    monkeypatch.setattr(rd, "DEEDS_PATH", tmp_path / "absent.jsonl")
    assert asyncio.run(g._deeds()) == ""


def test_deeds_actually_join_the_gather(monkeypatch):
    """The own_work lesson: a source that exists but is never assembled is a
    silent no-op — the health key's presence proves the wire."""
    import ai.present_moment_gather as g

    out = asyncio.run(g.gather_moment_inputs(owner_user_id=1))
    assert "deeds" in out.sense_health


# ── final review, Important 1: the not-delivered note is the system's ─────────

def test_a_not_delivered_note_is_a_system_line_never_hers():
    """persist_delivery_failure writes a ``system`` row after a reply the door
    could not send. The glance labelled every non-user row "[me]", so the
    note read as her own words."""
    rows = [
        {"role": "assistant", "content": "the plan is X", "timestamp": 0.0},
        {"role": "system", "content": "[not delivered to them: HTTPException 50013]", "timestamp": 0.0},
        {"role": "system",
         "content": "[only partly delivered to them: HTTPException 50013 after 1 of 2 parts]",
         "timestamp": 0.0},
    ]
    tail = _run_tail(rows)
    assert "[system] [not delivered to them: HTTPException 50013]" in tail
    assert "[system] [only partly delivered to them: HTTPException 50013 after 1 of 2 parts]" in tail
    assert "[me] [not delivered" not in tail and "[me] [only partly" not in tail
    assert "[me] the plan is X" in tail
