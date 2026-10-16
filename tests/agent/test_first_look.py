"""Her first look, the module itself (R14, T3; plan docs/2026-10-12-one-first-look-plan.md
§2.1, §2.4–§2.6, Draft 2, Draft 5).

- ``parse`` is tolerant (fences, prose around the object, missing fields take
  the read's and the vote's defaults); an unknown decision is "answer"; bad
  JSON is no look. ``for_me`` is prose, never a yes or a no (the owner, 10-12
  20:43), and nothing in code reads it as a decision.
- ``take`` is one call on the lane ``first_look`` under a 12 s wall; a
  timeout, a failed call or bad JSON is ``None`` (the turn goes on as today).
- The slice is the owner's only: on a guest's turn, in this build, no slice
  at all (Q2) — no posture, no arc, no hopes or worries, no tonight pick, and
  not one of those reads is made.
- ``as_turn_read`` gives the reflection and the read section what the first
  read gives them today; ``as_cadence_config`` gives the length, register,
  phase, beats and emotional context what the vote gives them.
- The carry rule (FW-2 generalised): a guest's look texts never reach a
  guest's reply while the owner's room note stands; otherwise they travel.
- ``render_signals(voice="me")`` is today's line in her first person; the
  "her" line stays byte for byte for the switch-off vote.

Stand-in names only: Robin is the owner, Sam and Dana guests.
"""
from __future__ import annotations

import asyncio
import json
import logging
from types import SimpleNamespace

import pytest

from core.agent import first_look as fl

FULL = {
    "how_they_seem": "Sam sounds pleased, telling the room about the new job.",
    "weight": 0.3,
    "needs_support": 0.1,
    "carries_forward": "Sam starts the new job on Monday.",
    "memory_worthy": "Sam got a new job.",
    "for_me": "news to the room; it calls Dana by name, probably means Dana (a guess)",
    "my_own": "I'd like to hear what the work is",
    "my_own_from": "",
    "decision": "stay_quiet",
    "reason": "it's Dana's to answer",
    "length": "short",
    "register": "casual_greeting",
    "phase": "opening",
    "target_beats": 2,
    "emotional_context": "playful_banter",
}


def _look(**over) -> fl.FirstLook:
    data = dict(FULL)
    data.update(over)
    look = fl.parse(json.dumps(data))
    assert look is not None
    return look


# ── parse ──────────────────────────────────────────────────────────────────

def test_the_look_has_its_fields_in_order():
    from dataclasses import fields

    # the JSON's fifteen keys (my_own_from, 10-13), then the one field code
    # fills, never her: the stake her my_own_from names (the presence slice)
    assert [f.name for f in fields(fl.FirstLook)] == list(FULL) + ["drew_on", "own_life_seen"]


def test_parse_reads_every_field():
    look = fl.parse(json.dumps(FULL))
    assert look == fl.FirstLook(**FULL)


@pytest.mark.parametrize("wrap", [
    "```json\n{}\n```",
    "```\n{}\n```",
    "Here is my first look:\n{}\nThat's all.",
    "  {}  ",
])
def test_parse_tolerates_fences_and_prose(wrap):
    look = fl.parse(wrap.replace("{}", json.dumps(FULL)))
    assert look == fl.FirstLook(**FULL)


def test_missing_fields_take_the_defaults():
    look = fl.parse('{"decision": "answer"}')
    assert look == fl.FirstLook(
        how_they_seem="", weight=0.0, needs_support=0.0, carries_forward="",
        memory_worthy="", for_me="", my_own="", decision="answer", reason="",
        length="medium", register="neutral", phase="settled_conversation",
        target_beats=1, emotional_context="neutral")


@pytest.mark.parametrize("raw, expected", [
    ("maybe", "answer"), ("", "answer"), (None, "answer"), (3, "answer"),
    ("Stay_Quiet ", "stay_quiet"), ("WAIT", "wait"), ("answer", "answer"),
])
def test_an_unknown_decision_is_answer(raw, expected):
    assert _look(decision=raw).decision == expected


def test_the_numbers_are_clamped_and_the_beats_are_whole():
    look = _look(weight=4, needs_support=-1, target_beats="two")
    assert (look.weight, look.needs_support, look.target_beats) == (1.0, 0.0, 1)
    assert _look(target_beats=0).target_beats == 1
    assert _look(weight="heavy").weight == 0.0


@pytest.mark.parametrize("raw", [
    "", "no json here", "{not json}", "[1, 2]", '"just a string"', None,
])
def test_bad_json_is_no_look(raw):
    assert fl.parse(raw) is None


@pytest.mark.parametrize("value", [True, False, 1, None, ["Dana"], {"to": "Dana"}])
def test_for_me_is_prose_never_a_yes_or_a_no(value):
    """The owner, 10-12 20:43: "not ... a yes or a no". A boolean (or any
    non-text) for_me is dropped, and it never moves the decision."""
    look = _look(for_me=value, decision="answer")
    assert look.for_me == ""
    assert look.decision == "answer"


def test_the_decision_never_comes_from_for_me():
    for for_me in ("not for me", "for me", "yes", "no"):
        assert _look(for_me=for_me, decision="stay_quiet").decision == "stay_quiet"
        assert _look(for_me=for_me, decision="answer").decision == "answer"
        assert _look(for_me=for_me).for_me == for_me


def test_a_named_guess_stays_her_words():
    """Q10: the look may say "probably means Dana (a guess)" in its notes; it
    stays prose in her notes, never a fact anywhere."""
    look = _look(reason="not mine to answer")
    assert look.for_me.endswith("probably means Dana (a guess)")
    from core.agent import cadence

    assert "Dana" not in json.dumps(cadence.first_pass_fields(fl.as_cadence_config(look)))


# ── the call ───────────────────────────────────────────────────────────────

_FACTS = dict(message="[Mon 10:00] Sam: <the message>", recent_conversation="<the room>",
              in_room=True, room_block="This is a group chat on Discord: #general")


def test_take_sends_the_built_prompt_and_parses_the_answer():
    seen: list[str] = []

    async def llm(prompt: str) -> str:
        seen.append(prompt)
        return "```json\n" + json.dumps(FULL) + "\n```"

    look = asyncio.run(fl.take(llm=llm, **_FACTS))
    assert look == fl.FirstLook(**FULL)
    assert seen == [fl.build_prompt(**_FACTS)]


def test_take_without_an_llm_builds_the_lanes_own(monkeypatch):
    calls: list[str] = []

    async def llm(prompt: str) -> str:
        calls.append(prompt)
        return json.dumps(FULL)

    monkeypatch.setattr(fl, "build_llm", lambda: llm)
    assert asyncio.run(fl.take(**_FACTS)) is not None
    assert len(calls) == 1


def test_take_waits_twelve_seconds_at_most():
    assert fl.TAKE_TIMEOUT_SECONDS == 12.0


def test_a_look_that_runs_out_of_time_is_no_look(monkeypatch, caplog):
    monkeypatch.setattr(fl, "TAKE_TIMEOUT_SECONDS", 0.01)

    async def slow(prompt: str) -> str:
        await asyncio.sleep(1)
        return json.dumps(FULL)

    with caplog.at_level(logging.INFO, logger=fl.logger.name):
        assert asyncio.run(fl.take(llm=slow, **_FACTS)) is None
    line = " ".join(r.getMessage() for r in caplog.records if r.levelno == logging.INFO)
    assert "first_look" in line and "timed out" in line


@pytest.mark.parametrize("answer, why", [("no json", "unreadable"), ("", "unreadable")])
def test_bad_json_from_the_call_is_no_look(caplog, answer, why):
    async def llm(prompt: str) -> str:
        return answer

    with caplog.at_level(logging.INFO, logger=fl.logger.name):
        assert asyncio.run(fl.take(llm=llm, **_FACTS)) is None
    line = " ".join(r.getMessage() for r in caplog.records if r.levelno == logging.INFO)
    assert "first_look" in line and why in line


def test_a_failed_call_is_no_look(caplog):
    async def llm(prompt: str) -> str:
        raise ConnectionError("provider down")

    with caplog.at_level(logging.INFO, logger=fl.logger.name):
        assert asyncio.run(fl.take(llm=llm, **_FACTS)) is None
    line = " ".join(r.getMessage() for r in caplog.records if r.levelno == logging.INFO)
    assert "first_look" in line and "ConnectionError" in line


def test_a_missing_template_is_no_look(monkeypatch):
    from ai.prompt_loader import prompt_loader

    monkeypatch.setattr(prompt_loader, "load_prompt", lambda name: "")

    async def llm(prompt: str) -> str:  # pragma: no cover — never reached
        raise AssertionError("no prompt, no call")

    assert asyncio.run(fl.take(llm=llm, **_FACTS)) is None


def test_a_wrong_fact_name_is_a_code_error_not_a_quiet_none():
    async def llm(prompt: str) -> str:  # pragma: no cover — never reached
        return json.dumps(FULL)

    with pytest.raises(TypeError):
        asyncio.run(fl.take(llm=llm, mesage="typo"))


def test_the_lanes_callable_makes_one_call_with_the_fixed_shape(monkeypatch):
    import ai.llm_client as llm_client

    sent: list[dict] = []

    async def chat_completion(**kwargs):
        sent.append(kwargs)
        return None

    monkeypatch.setattr(llm_client.llm, "chat_completion", chat_completion)
    monkeypatch.delenv("PALLI_CADENCE_MODEL", raising=False)
    monkeypatch.delenv("CADENCE_MODEL", raising=False)
    monkeypatch.setattr("core.config.config.CADENCE_MODEL", "", raising=False)
    assert asyncio.run(fl.build_llm()("<the prompt>")) == ""
    assert sent == [{"messages": [{"role": "user", "content": "<the prompt>"}],
                     "temperature": 0.3, "max_tokens": 900, "log_context": "first_look"}]


def test_the_lanes_callable_takes_the_quick_reads_model(monkeypatch):
    import ai.llm_client as llm_client

    sent: list[dict] = []

    async def chat_completion(**kwargs):
        sent.append(kwargs)
        return "{}"

    monkeypatch.setattr(llm_client.llm, "chat_completion", chat_completion)
    monkeypatch.setattr("core.config.config.CADENCE_MODEL", "deepseek::fast", raising=False)
    asyncio.run(fl.build_llm()("p"))
    assert sent[0]["model"] == "deepseek::fast"


def test_the_looks_call_names_its_lane_and_never_thinks():
    """Through the lane scanner: the call site in this module names the lane,
    and the lane is no-think (a JSON look must not think, 08-23)."""
    from ai.llm_openai_compatible import is_no_think_lane
    from tests.ai.test_request_timeout_lanes import TestEveryLaneIsClassified

    unnamed, lanes = TestEveryLaneIsClassified._scan()
    assert lanes.get(fl.LANE, "").startswith("core/agent/first_look.py:")
    assert not [u for u in unnamed if u.startswith("core/agent/first_look.py")]
    assert is_no_think_lane(fl.LANE)


# ── the slice (plan §2.4, Q2) ──────────────────────────────────────────────

def _turn(origin: str = "user", surface: str = "discord_text"):
    return SimpleNamespace(owner_id=7, surface=surface, content="hi",
                           metadata=SimpleNamespace(origin=origin))


@pytest.fixture
def readers(monkeypatch):
    """Every read the slice can make, recorded; the companion flags all on."""
    calls: list[str] = []
    import ai.relationship_momentum as rm
    import ai.stake_ledger as sl
    import ai.tonight as tn
    import ai.turn_read as tr

    monkeypatch.setattr("core.turn.companion_flags.companion_settings",
                        lambda: SimpleNamespace(relationship_calibration=True, caring_memory=True))

    def load_state(owner_id):
        calls.append("posture")
        return "state"

    async def render_recent_arc(*, owner_id, **_):
        calls.append("arc")
        return "a" * 3000

    async def open_stakes(*, limit=8, **_):
        calls.append(f"stakes:{limit}")
        return ["s"]

    def load_current(now=None):
        calls.append("tonight")
        return tn.Pick(day="d", source="stake", source_id="1", text="t" * 500, why="w")

    monkeypatch.setattr(rm, "load_state", load_state)
    monkeypatch.setattr(rm, "render_posture", lambda state: "p" * 2000)
    monkeypatch.setattr(tr, "render_recent_arc", render_recent_arc)
    monkeypatch.setattr(sl, "ledger_enabled", lambda: True)
    monkeypatch.setattr(sl, "open_stakes", open_stakes)
    monkeypatch.setattr(sl, "render_stakes_block", lambda stakes, **kw: "- (view, open 2d) " + "s" * 3000)
    import core.agent.own_life as ol

    def gather_items(now=None):
        calls.append("own_life")
        return [ol.Item("want", "o2", "learn the sheet music")]

    monkeypatch.setattr(ol, "gather_items", gather_items)
    monkeypatch.setattr(ol, "check_in_background", lambda items: calls.append("checks"))
    monkeypatch.setattr(tn, "tonight_enabled", lambda: True)
    monkeypatch.setattr(tn, "is_conversation", lambda surface: True)
    monkeypatch.setattr(tn, "load_current", load_current)
    return calls


def test_a_guests_turn_has_no_slice_and_makes_no_read(readers):
    out = asyncio.run(fl.gather_slice(_turn("guest"), owner=False))
    assert out == {"relationship_posture": "", "emotional_arc": "", "stakes": "", "tonight": "",
                   "own_life": ""}
    assert readers == []


def test_a_guests_turn_has_no_slice_even_when_called_the_owners(readers):
    """A second wall: the turn itself says guest, whatever the caller says."""
    out = asyncio.run(fl.gather_slice(_turn("guest"), owner=True))
    assert set(out.values()) == {""}
    assert readers == []


def test_the_owners_slice_has_all_four_within_their_budgets(readers):
    out = asyncio.run(fl.gather_slice(_turn(), owner=True))
    assert out["relationship_posture"] == "p" * fl.POSTURE_CHARS
    assert out["emotional_arc"] == "a" * fl.ARC_CHARS
    assert out["stakes"].startswith("- (view, open 2d) ") and len(out["stakes"]) == fl.STAKES_CHARS
    assert out["tonight"].endswith("t" * 10)
    assert len(out["tonight"]) <= fl.TONIGHT_CHARS + len(fl.TONIGHT_LINE.format(text=""))
    assert sorted(readers) == ["arc", "own_life", "posture", "stakes:8", "tonight"]
    assert out["own_life"].endswith("- [o2] something I want to do, in my own time: learn the sheet music")
    # the slice is what build_prompt takes, as it is
    assert "HOW THINGS ARE BETWEEN US" in fl.build_prompt(message="m", recent_conversation="c",
                                                           owner=True, **out)


def test_tonights_pick_is_her_words_without_the_reply_prompts_law(readers):
    from ai import tonight

    out = asyncio.run(fl.gather_slice(_turn(), owner=True))
    assert tonight.BAND_LAW not in out["tonight"]
    assert out["tonight"] == fl.TONIGHT_LINE.format(text="t" * fl.TONIGHT_CHARS)


def test_a_said_pick_or_a_dark_organ_leaves_tonight_empty(readers, monkeypatch):
    import ai.tonight as tn

    monkeypatch.setattr(tn, "load_current", lambda now=None: tn.Pick(
        day="d", source="stake", source_id="1", text="x", why="w", said_at=1.0))
    assert asyncio.run(fl.gather_slice(_turn(), owner=True))["tonight"] == ""
    monkeypatch.setattr(tn, "tonight_enabled", lambda: False)
    assert asyncio.run(fl.gather_slice(_turn(), owner=True))["tonight"] == ""


def test_a_slow_stake_read_is_skipped_not_waited_on(readers, monkeypatch):
    import ai.stake_ledger as sl

    async def slow(*, limit=8, **_):
        await asyncio.sleep(1)
        return ["s"]

    monkeypatch.setattr(sl, "open_stakes", slow)
    out = asyncio.run(fl.gather_slice(_turn(), owner=True))
    assert out["stakes"] == "" and out["relationship_posture"]


def test_one_broken_read_costs_only_its_own_part(readers, monkeypatch):
    import ai.relationship_momentum as rm

    def boom(owner_id):
        raise RuntimeError("file gone")

    monkeypatch.setattr(rm, "load_state", boom)
    out = asyncio.run(fl.gather_slice(_turn(), owner=True))
    assert out["relationship_posture"] == ""
    assert out["emotional_arc"] and out["stakes"] and out["tonight"]


def test_the_companion_switches_gate_their_parts(readers, monkeypatch):
    import ai.stake_ledger as sl

    monkeypatch.setattr("core.turn.companion_flags.companion_settings",
                        lambda: SimpleNamespace(relationship_calibration=False, caring_memory=False))
    monkeypatch.setattr(sl, "ledger_enabled", lambda: False)
    out = asyncio.run(fl.gather_slice(_turn(), owner=True))
    assert (out["relationship_posture"], out["emotional_arc"], out["stakes"]) == ("", "", "")
    assert sorted(readers) == ["own_life", "tonight"]


# ── the prompt's order (plan §2.8) ─────────────────────────────────────────

def test_the_constant_text_comes_first_identical_across_turns():
    a = fl.build_prompt(message="[Mon 10:00] Sam: one thing", recent_conversation="room A",
                        in_room=True, room_block="This is a group chat on Discord: #general",
                        owner=False)
    b = fl.build_prompt(message="[Tue 11:00] Robin ★owner: another", recent_conversation="room B",
                        in_room=True, room_block="This is a Telegram group.", owner=True,
                        relationship_posture="close", emotional_arc="calm", stakes="- (view) x")
    same = 0
    while a[same] == b[same]:
        same += 1
    constant_end = a.index("neutral unless the signal is clear.") + len("neutral unless the signal is clear.")
    assert same >= constant_end
    assert a.endswith("one thing\n\nMy first look, as the JSON object above, nothing else.")
    assert b.endswith("another\n\nMy first look, as the JSON object above, nothing else.")


# ── what the reflection and the reply get (plan §2.1, §2.6, Draft 2) ───────

def test_render_body_is_draft_two():
    assert fl.render_body(_look()) == (
        "Sam sounds pleased, telling the room about the new job.\n"
        "Who it's for, as I read it: news to the room; it calls Dana by name, "
        "probably means Dana (a guess)\n"
        "Something of my own that belongs here: I'd like to hear what the work is")


def test_render_body_leaves_out_what_is_empty():
    assert fl.render_body(_look(my_own="")) == (
        "Sam sounds pleased, telling the room about the new job.\n"
        "Who it's for, as I read it: news to the room; it calls Dana by name, "
        "probably means Dana (a guess)")
    assert fl.render_body(_look(for_me="", my_own="")) == (
        "Sam sounds pleased, telling the room about the new job.")
    assert fl.render_body(_look(how_they_seem="", for_me="", my_own="")) == ""
    assert fl.render_body(None) == ""


def test_as_turn_read_round_trips_the_five_read_fields():
    from ai.turn_read import TurnRead

    look = _look()
    read = fl.as_turn_read(look)
    assert isinstance(read, TurnRead)
    # The coordinator's ruling (T4's note for T5): the read's words are how
    # they seem only, so the reflection's arc stays a mood record; Draft 2's
    # "Who it's for" and "Something of my own" lines are the reply's read
    # section's alone (render_body).
    assert read.read == look.how_they_seem
    assert fl.FOR_ME_LABEL not in read.read and fl.MY_OWN_LABEL not in read.read
    assert (read.weight, read.needs_support, read.carries_forward, read.memory_worthy) == (
        0.3, 0.1, "Sam starts the new job on Monday.", "Sam got a new job.")
    assert read.created_at > 0


def test_no_look_or_an_empty_look_is_no_read():
    """Downstream a read always has words (turn_read._parse drops an empty
    one), so an empty look gives none."""
    assert fl.as_turn_read(None) is None
    assert fl.as_turn_read(_look(how_they_seem="", for_me="", my_own="")) is None
    assert fl.as_turn_read(_look(how_they_seem="")) is None       # no mood words, no read


@pytest.mark.parametrize("decision", ["answer", "wait", "stay_quiet"])
def test_as_cadence_config_carries_what_the_vote_carried(decision):
    from core.agent import cadence

    cfg = fl.as_cadence_config(_look(decision=decision, length="long"))
    assert isinstance(cfg, cadence.CadenceConfig)
    assert cfg.should_respond is (decision == "answer")
    assert (cfg.decision, cfg.reason) == (decision, "it's Dana's to answer")
    assert (cfg.register, cfg.length, cfg.phase, cfg.target_beats, cfg.emotional_context) == (
        "casual_greeting", "long", "opening", 2, "playful_banter")
    # the look's words travel as the read, never as the vote's observation
    assert cfg.observation_text == ""
    assert cfg.directive == cadence.render_cadence_directive(cfg)
    assert cfg.directive == ("## This turn\nThis one can run long. This one reads better as two "
                             "or three messages, with a blank line between them.")


def test_a_plain_look_has_no_directive():
    assert fl.as_cadence_config(_look(length="short", target_beats=1)).directive == ""


# ── the carry rule (FW-2 generalised; plan §2.1) ───────────────────────────

TEXTS = ("how_they_seem", "for_me", "my_own")


def test_a_guests_look_texts_never_travel_while_a_note_stands():
    fields = fl.carry_fields(_look(decision="answer"), guest=True, note_stands=True)
    for name in TEXTS + ("reason", "observation_text"):
        assert fields[name] == "", name
    # the shape still rides, as the vote's did
    assert (fields["decision"], fields["length"], fields["register"]) == (
        "answer", "short", "casual_greeting")
    assert fl.render_body(fl.from_carry(fields)) == ""


@pytest.mark.parametrize("guest, note_stands", [(True, False), (False, True), (False, False)])
def test_otherwise_the_look_texts_travel(guest, note_stands):
    look = _look(decision="answer")
    fields = fl.carry_fields(look, guest=guest, note_stands=note_stands)
    assert {n: fields[n] for n in TEXTS} == {n: getattr(look, n) for n in TEXTS}
    assert fields["reason"] == look.reason
    assert fl.render_body(fl.from_carry(fields)) == fl.render_body(look)


def test_the_carry_is_todays_first_pass_plus_the_three_texts():
    from core.agent import cadence

    look = _look(decision="answer")
    fields = fl.carry_fields(look, guest=False, note_stands=False)
    # Moved deliberately (10-13): plus the mark that a look really ran.
    assert set(fields) == set(cadence._FIRST_PASS_FIELDS) | set(TEXTS) | {"look"}
    assert fields["look"] is True
    assert fl.carry_fields(look, guest=True, note_stands=True)["look"] is True
    json.dumps(fields)                       # plain values: it rides on metadata.extra
    # the runner's reader takes it back unchanged
    assert cadence.config_from_first_pass(fields) == fl.as_cadence_config(look)


def test_from_carry_reads_nothing_usable_as_no_look():
    assert fl.from_carry(None) is None
    assert fl.from_carry("first_pass") is None
    assert fl.from_carry({"decision": "answer"}) == fl.FirstLook(decision="answer")


# ── the signals line in her first person (Draft 5) ─────────────────────────

_SIGNALS = [
    {"is_dm": True},
    {"is_dm": False, "is_mention": True},
    {"is_dm": False, "is_mention": True, "mentions_others": ["Dana"]},
    {"is_dm": False, "mentions_others": ["Dana", "Sam"]},
    {"is_dm": False, "is_reply_to_her": True,
     "reply_to": {"text": "tell her I said hi", "at": "2026-10-12T17:56:00"}},
    {"is_dm": False, "is_reply_to_her": True, "reply_to": None},
    {"is_dm": False, "reply_to": {"text": "x", "to_her": None}},
    {"is_dm": False, "reply_to": {"text": "x", "to_her": False}},
    {"is_dm": False, "names_her": True},
    {"is_dm": False, "is_mention": True, "is_reply_to_her": True,
     "reply_to": {"text": "", "at": ""}, "names_her": True, "mentions_others": ["Jo"]},
]
_PERSON_SWAPS = {("her", "me"), ("her", "my"), ("she", "I")}


@pytest.mark.parametrize("signals", _SIGNALS)
def test_the_first_person_line_differs_only_in_the_person_words(signals):
    from core.agent.cadence import render_signals

    hers, mine = render_signals(signals), render_signals(signals, voice="me")
    assert render_signals(signals, voice="her") == hers
    a, b = hers.split(" "), mine.split(" ")
    assert len(a) == len(b)
    diffs = {(x.strip(".:;,()\""), y.strip(".:;,()\"")) for x, y in zip(a, b) if x != y}
    assert diffs <= _PERSON_SWAPS, diffs
    assert bool(diffs) is (hers != mine)
    assert mine.startswith("About the latest message (signals to weigh, never rules): ")


def test_the_first_person_words_are_draft_fives():
    from core.agent.cadence import render_signals

    line = render_signals({"is_dm": False, "is_mention": True, "mentions_others": ["Dana"],
                           "reply_to": {"text": "x", "to_her": None}, "names_her": True},
                          voice="me")
    assert line == ("About the latest message (signals to weigh, never rules): said in a shared "
                    "room; it @mentions me; it also @mentions Dana; it replies to an earlier "
                    "message I can't see; it says my name.")
    assert render_signals({"is_dm": True}, voice="me").endswith("a direct message to me.")
    assert render_signals({"is_dm": False, "mentions_others": ["Dana"]}, voice="me").endswith(
        "it @mentions someone else (Dana), not me.")
    assert render_signals({"is_dm": False, "is_reply_to_her": True}, voice="me").endswith(
        "it replies to one of my messages.")


def test_todays_line_keeps_its_bytes():
    """The switch-off vote's line, byte for byte (the approved words)."""
    from core.agent.cadence import render_signals

    assert render_signals({"is_dm": False, "is_mention": True, "mentions_others": ["Dana"],
                           "reply_to": {"text": "x", "to_her": None}, "names_her": True}) == (
        "About the latest message (signals to weigh, never rules): said in a shared room; "
        "it @mentions her; it also @mentions Dana; it replies to an earlier message she "
        "can't see; it says her name.")
    assert render_signals({"is_dm": False, "mentions_others": ["Dana"]}).endswith(
        "it @mentions someone else (Dana), not her.")
    assert render_signals({"is_dm": False, "is_reply_to_her": True}).endswith(
        "it replies to one of her messages.")
    assert render_signals({}) == "" and render_signals({}, voice="me") == ""


def test_in_a_group_the_owners_slice_leaves_out_his_world(readers):
    """The presence slice (the owner, 10-13: "Only its own life + that room"):
    on his own turn in a group, the look reads neither her stakes about his
    world nor tonight's pick; how things are between them still comes."""
    room = SimpleNamespace(owner_id=7, surface="discord_text", content="hi all",
                           metadata=SimpleNamespace(origin="user", channel_id=555, is_dm=False))
    out = asyncio.run(fl.gather_slice(room, owner=True))
    assert out["stakes"] == "" and out["tonight"] == ""
    assert out["relationship_posture"] and out["emotional_arc"]
    assert sorted(readers) == ["arc", "checks", "own_life", "posture"]
    assert out["own_life"] == ""          # not checked yet: nothing of its own reaches the room


def test_in_a_group_only_the_items_the_room_check_passed_come(readers, monkeypatch):
    import core.agent.own_life as ol

    monkeypatch.setattr(ol, "room_safe", lambda items: list(items))
    room = SimpleNamespace(owner_id=7, surface="discord_text", content="hi all",
                           metadata=SimpleNamespace(origin="user", channel_id=555, is_dm=False))
    out = asyncio.run(fl.gather_slice(room, owner=True))
    assert out["own_life"] == (ol.OWN_LIFE_HEADING + "\n"
                               "- [o2] something I want to do, in my own time: learn the sheet music")


def test_the_stakes_are_tagged_and_the_shown_list_is_kept(readers, monkeypatch):
    import ai.stake_ledger as sl

    seen = {}

    def render(stakes, **kw):
        seen.update(kw)
        return "- [s1] (hope, open 2d) x"

    monkeypatch.setattr(sl, "render_stakes_block", render)
    shown: list = []
    asyncio.run(fl.gather_slice(_turn(), owner=True, shown=shown))
    assert seen.get("tags") is True
    assert shown == ["s"]


def test_a_look_that_names_a_stake_carries_it_for_the_said_check():
    from ai.stake_ledger import Stake

    stake = Stake(id=4, kind="hope", stake="the survey", status="open", resolution="",
                  horizon_at=None, source_channel_id="", last_surfaced_at=None,
                  created_at=0.0, updated_at=0.0, resolved_at=None)
    look = fl.parse('{"my_own": "the survey", "my_own_from": "s1"}')
    assert fl.with_drawn_stake(look, [stake]).drew_on is stake
    other = fl.parse('{"my_own": "my sheet music", "my_own_from": "o2"}')
    assert fl.with_drawn_stake(other, [stake]).drew_on is None
    blank = fl.parse('{"my_own": "", "my_own_from": "s1"}')
    assert fl.with_drawn_stake(blank, [stake]).drew_on is None      # nothing of its own, nothing drawn
    assert fl.with_drawn_stake(None, [stake]) is None
