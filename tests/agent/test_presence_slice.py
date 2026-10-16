"""The presence slice (2026-10-13; plan docs/2026-10-13-presence-slice-plan.md).

The owner's rulings this session, each proved by a test that failed first:
- On the owner's own turn in a group, what PaLLi volunteers comes from its own
  life and that room: its hopes and worries about the owner, tonight's
  thought and its small truths stay for the private chat ("Only its own life
  + that room"). Its memory, for answering, is unchanged.

Stand-in names only: Robin is the owner, Sam a guest.
"""
from __future__ import annotations

from datetime import datetime

from core.agent.context_adapter import build_agent_loop_context
from core.turn.types import Turn, TurnContext, TurnMetadata

_PRIVATE_VOLUNTEERED = {
    "held_stakes_text": "- (hope, open 3d) Robin's job application goes through",
    "tonight_text": "One thing on my mind tonight: Robin's sister's visit",
    "disclosures_text": "- a small truth of mine",
}


def _turn(*, is_dm: bool, channel_id: int | None = 555) -> Turn:
    return Turn(surface="discord_text", owner_id=1, content="hi all",
                metadata=TurnMetadata(channel_id=channel_id, is_dm=is_dm),
                received_at=datetime(2026, 10, 13, 22, 0, 0))


def _ctx(turn: Turn) -> TurnContext:
    return TurnContext(turn=turn, **_PRIVATE_VOLUNTEERED,
                       present_moment_text="my moment")


def test_in_a_group_the_owners_turn_volunteers_none_of_the_private_bands():
    turn = _turn(is_dm=False)
    out = build_agent_loop_context(turn, _ctx(turn), is_owner=True)
    for field in _PRIVATE_VOLUNTEERED:
        assert getattr(out, field) == "", field


def test_in_a_private_chat_the_owners_turn_keeps_them():
    turn = _turn(is_dm=True)
    out = build_agent_loop_context(turn, _ctx(turn), is_owner=True)
    for field, text in _PRIVATE_VOLUNTEERED.items():
        assert getattr(out, field) == text, field


def test_in_a_group_the_owners_memory_for_answering_is_unchanged():
    turn = _turn(is_dm=False)
    ctx = TurnContext(turn=turn, relevant_facts=({"key": "pet", "value": "a grey cat"},))
    out = build_agent_loop_context(turn, ctx, is_owner=True)
    assert "a grey cat" in out.facts_text


# ── the first look's stakes carry tags and a said note (D1, 10-13) ───────

def _stake(id_, kind="hope", text="the survey comes back clean", *, surfaced=None, now=1_000_000.0):
    from ai.stake_ledger import Stake

    return Stake(id=id_, kind=kind, stake=text, status="open", resolution="", horizon_at=None,
                 source_channel_id="", last_surfaced_at=surfaced, created_at=now - 3 * 86400,
                 updated_at=now, resolved_at=None)


def test_tagged_stakes_carry_s_tags_in_order():
    from ai.stake_ledger import render_stakes_block

    now = 1_000_000.0
    out = render_stakes_block([_stake(7, now=now), _stake(9, "worry", "the trip", now=now)],
                              now=now, tags=True)
    lines = out.splitlines()
    assert lines[0].startswith("- [s1] (hope, open 3d) ")
    assert lines[1].startswith("- [s2] (worry, open 3d) ")


def test_a_said_stake_says_when():
    from ai.stake_ledger import render_stakes_block

    now = 1_000_000.0
    for surfaced, note in ((now - 3600, "(said today)"), (now - 30 * 3600, "(said yesterday)"),
                           (now - 2.5 * 86400, "(said 2 days ago)")):
        out = render_stakes_block([_stake(7, surfaced=surfaced, now=now)], now=now, tags=True)
        assert out.endswith("the survey comes back clean " + note), out


def test_untagged_rendering_is_unchanged():
    from ai.stake_ledger import render_stakes_block

    now = 1_000_000.0
    out = render_stakes_block([_stake(7, surfaced=now - 3600, now=now)], now=now)
    assert out == "- (hope, open 3d) the survey comes back clean"


def test_stake_tags_map_back_to_ids():
    from ai.stake_ledger import stake_for_tag

    stakes = [_stake(7), _stake(9)]
    assert stake_for_tag(stakes, "s2").id == 9
    assert stake_for_tag(stakes, "[s1]").id == 7
    assert stake_for_tag(stakes, " S1 ").id == 7
    for bad in ("s3", "s0", "o1", "", "s", "sx"):
        assert stake_for_tag(stakes, bad) is None


# ── a stake the look drew on is marked said only when the reply said it ──

def test_the_look_hands_its_drawn_stake_to_the_reflection():
    from dataclasses import replace

    from core.agent import first_look as fl

    turn = _turn(is_dm=True)
    stake = _stake(4)
    look = replace(fl.parse('{"my_own": "the survey", "my_own_from": "s1"}'), drew_on=stake)
    ctx = fl.into_turn_context(TurnContext(turn=turn), look, arc="")
    assert ctx.own_stake_drawn is stake
    assert fl.into_turn_context(TurnContext(turn=turn), None, arc="").own_stake_drawn is None


def _reflect_stubs(monkeypatch, *, said: bool):
    import ai.stake_ledger as sl

    calls = {"said": [], "surfaced": [], "voiced": []}

    async def stake_said(kind, text, reply, **_):
        calls["said"].append((kind, text, reply))
        return said

    async def record_surfaced(stake_id, **_):
        calls["surfaced"].append(stake_id)

    async def mark_stake_said(stake_id, **_):
        calls["voiced"].append(stake_id)
        return True

    monkeypatch.setattr(sl, "stake_said", stake_said)
    monkeypatch.setattr(sl, "record_surfaced", record_surfaced)
    monkeypatch.setattr(sl, "mark_stake_said", mark_stake_said)
    return calls


def test_a_reply_that_said_the_stake_stamps_it(monkeypatch):
    import asyncio

    from core.turn import reflect

    calls = _reflect_stubs(monkeypatch, said=True)
    ctx = TurnContext(turn=_turn(is_dm=True), own_stake_drawn=_stake(4))
    asyncio.run(reflect._record_stake_said(ctx, "fingers crossed on the survey"))
    assert calls["said"] == [("hope", "the survey comes back clean", "fingers crossed on the survey")]
    assert calls["surfaced"] == [4] and calls["voiced"] == [4]


def test_a_reply_that_did_not_say_it_stamps_nothing(monkeypatch):
    import asyncio

    from core.turn import reflect

    calls = _reflect_stubs(monkeypatch, said=False)
    asyncio.run(reflect._record_stake_said(
        TurnContext(turn=_turn(is_dm=True), own_stake_drawn=_stake(4)), "hi"))
    assert calls["surfaced"] == [] and calls["voiced"] == []


def test_no_drawn_stake_asks_nothing(monkeypatch):
    import asyncio

    from core.turn import reflect

    calls = _reflect_stubs(monkeypatch, said=True)
    asyncio.run(reflect._record_stake_said(TurnContext(turn=_turn(is_dm=True)), "hi"))
    assert calls["said"] == []


# ── the reply sees its own life too (the owner, 23:08: "Show its own life
# to the reply"; D9) ──────────────────────────────────────────────────────

_OWN_BLOCK = ("MY OWN LIFE LATELY (mine, not theirs; each with its tag):\n"
              "- [o1] something I'm afraid of: forgetting what mattered\n"
              "- [o3] what I was daydreaming about, yesterday: a lighthouse diary")


def test_the_look_hands_what_it_saw_of_its_own_life_to_the_reply():
    from dataclasses import replace

    from core.agent import first_look as fl

    look = replace(fl.parse('{"how_they_seem": "fine"}'), own_life_seen=_OWN_BLOCK)
    ctx = fl.into_turn_context(TurnContext(turn=_turn(is_dm=True)), look, arc="")
    assert ctx.own_life_text == ("- something I'm afraid of: forgetting what mattered\n"
                                 "- what I was daydreaming about, yesterday: a lighthouse diary")
    assert fl.into_turn_context(TurnContext(turn=_turn(is_dm=True)), None, arc="").own_life_text == ""


def test_take_for_turn_keeps_the_slices_own_life_on_the_look(monkeypatch):
    import asyncio

    from core.agent import first_look as fl

    async def gather_slice(turn, *, owner, shown=None):
        return {"relationship_posture": "", "emotional_arc": "", "stakes": "", "tonight": "",
                "own_life": _OWN_BLOCK}

    async def llm(prompt):
        return '{"how_they_seem": "fine", "decision": "answer"}'

    monkeypatch.setattr(fl, "gather_slice", gather_slice)
    look, _ = asyncio.run(fl.take_for_turn(_turn(is_dm=True), llm=llm, key="k", in_room=False,
                                           room_read=None, room_rows=(), owner=True))
    assert look.own_life_seen == _OWN_BLOCK


def test_the_owners_reply_gets_the_section_and_a_guests_never_does():
    from core.agent.system_prompt import OWN_LIFE_SECTION_HEADING, build_system_prompt

    turn = _turn(is_dm=True)
    ctx = TurnContext(turn=turn, own_life_text="- something I'm afraid of: forgetting")
    owner = build_system_prompt(build_agent_loop_context(turn, ctx, is_owner=True), cadence_directive="")
    assert OWN_LIFE_SECTION_HEADING in owner and "forgetting" in owner
    guest = build_system_prompt(build_agent_loop_context(turn, ctx, is_owner=False), cadence_directive="")
    assert OWN_LIFE_SECTION_HEADING not in guest and "forgetting" not in guest


def test_no_own_life_no_section():
    from core.agent.system_prompt import OWN_LIFE_SECTION_HEADING, build_system_prompt

    turn = _turn(is_dm=True)
    out = build_system_prompt(build_agent_loop_context(turn, TurnContext(turn=turn)), cadence_directive="")
    assert OWN_LIFE_SECTION_HEADING not in out


# ── the owner, 10-14 00:0x: "Yes, keep them private too" ─────────────────

def test_in_a_group_the_owners_other_private_bands_stay_out_too():
    turn = _turn(is_dm=False)
    private = {"present_moment_text": "my moment about Robin's week",
               "callbacks_text": "'the claw'",
               "night_cycle_reflection": "today meant a lot",
               "narrative_story_of_us": "we met in April"}
    ctx = TurnContext(turn=turn, **private)
    out = build_agent_loop_context(turn, ctx, is_owner=True)
    for field in ("present_moment_text", "callbacks_text", "night_cycle_reflection_text",
                  "narrative_story_of_us_text"):
        assert getattr(out, field) == "", field
    dm = _turn(is_dm=True)
    kept = build_agent_loop_context(dm, TurnContext(turn=dm, **private), is_owner=True)
    assert kept.present_moment_text and kept.callbacks_text
    assert kept.night_cycle_reflection_text and kept.narrative_story_of_us_text
