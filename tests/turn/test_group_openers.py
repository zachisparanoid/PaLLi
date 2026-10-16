"""Group openers (the presence slice, item 4; the owner, 10-13).

PaLLi, on its own, starting something in a group chat: at most one a day per
group, any time of day, never in a private chat, never in a room with a
standing owner's room note, never mid-conversation, never after an opener
nobody answered. What it sees is the room's own lines, its own earlier
openers there, and its own-life items that passed the room check: nothing of
the owner's private life. Stand-in names only (Robin is the owner).
"""
from __future__ import annotations

import json
import re
import sys
import time
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import core.turn.group_openers as go

REPO = Path(__file__).resolve().parents[2]
NOW = time.mktime((2026, 10, 13, 15, 0, 0, 0, 0, -1))
TG_ROOM = -1001234           # a Telegram group: below zero
DC_ROOM = 555000111          # a Discord server channel
DC_DM = 555000999            # a Discord DM


# ── helpers ────────────────────────────────────────────────────────────────

def _ts(seconds_ago: float) -> str:
    return datetime.fromtimestamp(NOW - seconds_ago, tz=timezone.utc).isoformat()


def _row(role: str, text: str, ago: float, *, who: str = "Sam", kind: str = "guest") -> dict:
    row = {"role": role, "content": [{"type": "text", "text": text}], "timestamp": _ts(ago)}
    if role == "user":
        row["speaker"] = {"kind": kind, "platform": "telegram", "id": "42", "name": who,
                          "handle": who.lower()}
    return row


def _write_room(root: Path, platform: str, channel: int, rows: list[dict]) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{platform}_{channel}.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return path


def _ledger(tmp: Path, day: str, rows: list[dict]) -> None:
    d = tmp / "group_openers"
    d.mkdir(parents=True, exist_ok=True)
    with (d / f"{day}.jsonl").open("a", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")


def _ledger_rows(tmp: Path) -> list[dict]:
    d = tmp / "group_openers"
    out: list[dict] = []
    for p in sorted(d.glob("*.jsonl")):
        out += [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]
    return out


def _answer(start: bool, message: str = "", why: str = "a reason") -> str:
    return json.dumps({"start": start, "message": message, "why": why})


class _LLM:
    def __init__(self, *answers: str):
        self.answers = list(answers) or [_answer(False, why="nothing new")]
        self.prompts: list[str] = []

    async def __call__(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self.answers[min(len(self.prompts) - 1, len(self.answers) - 1)]


@pytest.fixture
def env(tmp_path, monkeypatch):
    sessions = tmp_path / "agent_sessions"
    monkeypatch.setenv("PALLI_AGENT_SESSIONS_ROOT", str(sessions))
    monkeypatch.setenv("PALLI_GROUP_OPENERS_DIR", str(tmp_path / "group_openers"))
    monkeypatch.setenv("PALLI_ROOM_NOTES_PATH", str(tmp_path / "room_notes.json"))
    settings = SimpleNamespace(group_openers=True, group_openers_per_day=1)
    monkeypatch.setattr(go, "_settings", lambda: settings)
    monkeypatch.setattr(go, "_idle", lambda: False)

    class _Guild:
        name = "Book Club"

    channels = {DC_ROOM: SimpleNamespace(id=DC_ROOM, name="general", guild=_Guild(), parent=None),
                DC_DM: SimpleNamespace(id=DC_DM, name=None, guild=None)}
    monkeypatch.setattr(go, "_discord_channel", lambda cid: channels.get(int(cid)))

    async def _not_private(platform, channel_id):
        return platform == "telegram" and int(channel_id) > 0

    monkeypatch.setattr(go, "_is_private", _not_private)

    async def _own_life():
        return ""

    monkeypatch.setattr(go, "_own_life", _own_life)
    deliver = AsyncMock(return_value={"medium": "text"})
    monkeypatch.setattr(go, "_deliver", deliver)
    return SimpleNamespace(tmp=tmp_path, sessions=sessions, settings=settings, deliver=deliver,
                           channels=channels)


def _quiet_room(env, platform="telegram", channel=TG_ROOM, ago=3600.0):
    return _write_room(env.sessions, platform, channel, [
        _row("user", "anyone finished the book yet?", ago + 600, who="Sam"),
        _row("assistant", "Halfway. The ending is close.", ago + 500),
        _row("user", "no spoilers please", ago, who="Alex"),
    ])


# ── the switch ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_switch_off_does_nothing(env):
    _quiet_room(env)
    env.settings.group_openers = False
    llm = _LLM(_answer(True, "hi all"))
    assert await go.run_pass(owner_id=1, now=NOW, llm=llm) == "off"
    assert llm.prompts == [] and not env.deliver.called


@pytest.mark.asyncio
async def test_per_day_zero_does_nothing(env):
    _quiet_room(env)
    env.settings.group_openers_per_day = 0
    llm = _LLM(_answer(True, "hi all"))
    assert await go.run_pass(owner_id=1, now=NOW, llm=llm) == "off"
    assert llm.prompts == [] and not env.deliver.called


@pytest.mark.asyncio
async def test_unbooted_settings_count_as_off(env, monkeypatch):
    _quiet_room(env)
    monkeypatch.setattr(go, "_settings", lambda: None)
    llm = _LLM(_answer(True, "hi all"))
    assert await go.run_pass(owner_id=1, now=NOW, llm=llm) == "off"
    assert llm.prompts == []


# ── a send ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_start_is_sent_to_that_room_and_written_down(env):
    _quiet_room(env)
    llm = _LLM(_answer(True, "Finished it last night. That last chapter, though. Who's close?"))
    assert await go.run_pass(owner_id=7, now=NOW, llm=llm) == "sent"
    env.deliver.assert_awaited_once()
    kw = env.deliver.await_args.kwargs
    assert kw["platform"] == "telegram" and int(kw["channel_id"]) == TG_ROOM
    assert kw["origin"] == "group_opener" and kw["vm"] is None and kw["owner_user_id"] == 7
    assert kw["text"].startswith("Finished it last night")
    rows = _ledger_rows(env.tmp)
    assert len(rows) == 1
    row = rows[0]
    assert row["platform"] == "telegram" and int(row["channel_id"]) == TG_ROOM
    assert row["text"] == kw["text"] and row["chars"] == len(kw["text"])
    assert abs(row["at"] - NOW) < 1


@pytest.mark.asyncio
async def test_a_send_never_counts_toward_the_owners_daily_ceiling(env, monkeypatch, tmp_path):
    import ai.arbiter as arb

    arb_dir = tmp_path / "arbiter"
    arb_dir.mkdir()
    monkeypatch.setattr(arb, "ARBITER_DATA_DIR", arb_dir)
    (arb_dir / f"{arb._today()}.jsonl").write_text(json.dumps({"disposition": "sent"}) + "\n")
    before = arb.sends_today()
    _quiet_room(env)
    assert await go.run_pass(owner_id=1, now=time.time(), llm=_LLM(_answer(True, "hey all"))) == "sent"
    assert arb.sends_today() == before == 1
    assert list(arb_dir.iterdir()) == [arb_dir / f"{arb._today()}.jsonl"]


@pytest.mark.asyncio
async def test_a_failed_send_writes_no_send_row(env):
    _quiet_room(env)
    env.deliver.side_effect = RuntimeError("door down")
    assert await go.run_pass(owner_id=1, now=NOW, llm=_LLM(_answer(True, "hey all"))) == "failed"
    assert [r for r in _ledger_rows(env.tmp) if r.get("text")] == []


@pytest.mark.asyncio
async def test_a_no_start_is_written_down_with_its_why(env):
    _quiet_room(env)
    llm = _LLM(_answer(False, "", "they are mid-book and asked for no spoilers"))
    assert await go.run_pass(owner_id=1, now=NOW, llm=llm) == "no_start"
    rows = _ledger_rows(env.tmp)
    assert rows == [{"at": pytest.approx(NOW, abs=1), "platform": "telegram", "channel_id": TG_ROOM,
                     "disposition": "no_start", "why": "they are mid-book and asked for no spoilers"}]
    assert not env.deliver.called


# ── the model's answer ─────────────────────────────────────────────────────

@pytest.mark.parametrize("raw, expected", [
    ('{"start": true, "message": "hey all", "why": "w"}', (True, "hey all", "w")),
    ('```json\n{"start": true, "message": "hey all", "why": "w"}\n```', (True, "hey all", "w")),
    ('{"start": false, "message": "", "why": "nothing new"}', (False, "", "nothing new")),
    ('{"start": "true", "message": "hey all", "why": "w"}', None),       # not literally true
    ('{"start": 1, "message": "hey all", "why": "w"}', None),
    ('{"start": true, "message": "   ", "why": "w"}', None),            # empty message
    ('{"start": true, "message": 5, "why": "w"}', None),
    ('{"start": true, "why": "w"}', None),
    ('Sure! {"start": true, "message": "hey", "why": "w"}', None),      # not strict JSON
    ('["start", true]', None),
    ('', None),
    (None, None),
])
def test_parse(raw, expected):
    assert go.parse(raw) == expected


@pytest.mark.asyncio
async def test_a_message_over_400_characters_is_not_sent(env):
    _quiet_room(env)
    assert await go.run_pass(owner_id=1, now=NOW, llm=_LLM(_answer(True, "x" * 401))) == "too_long"
    assert not env.deliver.called
    assert [r for r in _ledger_rows(env.tmp) if r.get("text")] == []


@pytest.mark.asyncio
async def test_exactly_400_characters_is_sent(env):
    _quiet_room(env)
    assert await go.run_pass(owner_id=1, now=NOW, llm=_LLM(_answer(True, "x" * 400))) == "sent"


@pytest.mark.asyncio
async def test_an_unreadable_answer_sends_nothing(env):
    _quiet_room(env)
    assert await go.run_pass(owner_id=1, now=NOW, llm=_LLM("I think I'd say hi")) == "unreadable"
    assert not env.deliver.called


@pytest.mark.asyncio
async def test_a_failed_call_sends_nothing(env):
    _quiet_room(env)

    async def boom(prompt):
        raise RuntimeError("provider down")

    assert await go.run_pass(owner_id=1, now=NOW, llm=boom) == "call_failed"
    assert not env.deliver.called


@pytest.mark.asyncio
async def test_a_call_that_runs_late_sends_nothing(env, monkeypatch):
    import asyncio

    _quiet_room(env)
    monkeypatch.setattr(go, "CALL_TIMEOUT_SECONDS", 0.01)

    async def slow(prompt):
        await asyncio.sleep(1)
        return _answer(True, "hey")

    assert await go.run_pass(owner_id=1, now=NOW, llm=slow) == "call_failed"
    assert not env.deliver.called


# ── the cap: one a day per group ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_one_a_day_per_group(env):
    _quiet_room(env)
    _ledger(env.tmp, go.day_of(NOW), [{"at": NOW - 7200, "platform": "telegram",
                                       "channel_id": TG_ROOM, "text": "hey", "chars": 3}])
    # the room has answered since, so only the cap stands in the way
    _write_room(env.sessions, "telegram", TG_ROOM, [
        _row("assistant", "hey", 7200), _row("user", "hi!", 3600, who="Jo")])
    llm = _LLM(_answer(True, "another"))
    assert await go.run_pass(owner_id=1, now=NOW, llm=llm) == "skipped:cap"
    assert llm.prompts == []


@pytest.mark.asyncio
async def test_the_cap_is_per_group(env):
    _ledger(env.tmp, go.day_of(NOW), [{"at": NOW - 7200, "platform": "telegram",
                                       "channel_id": -999, "text": "hey", "chars": 3}])
    _quiet_room(env)
    assert await go.run_pass(owner_id=1, now=NOW, llm=_LLM(_answer(True, "hey"))) == "sent"


@pytest.mark.asyncio
async def test_two_a_day_when_the_setting_says_two(env):
    env.settings.group_openers_per_day = 2
    _ledger(env.tmp, go.day_of(NOW), [{"at": NOW - 7200, "platform": "telegram",
                                       "channel_id": TG_ROOM, "text": "hey", "chars": 3}])
    _write_room(env.sessions, "telegram", TG_ROOM, [
        _row("assistant", "hey", 7200), _row("user", "hi!", 3600, who="Jo")])
    assert await go.run_pass(owner_id=1, now=NOW, llm=_LLM(_answer(True, "more"))) == "sent"


@pytest.mark.asyncio
async def test_an_unreadable_ledger_counts_as_full(env):
    _quiet_room(env)
    (env.tmp / "group_openers" / f"{go.day_of(NOW)}.jsonl").mkdir(parents=True)
    llm = _LLM(_answer(True, "hey"))
    assert await go.run_pass(owner_id=1, now=NOW, llm=llm) == "skipped:cap"
    assert llm.prompts == []


def test_a_damaged_ledger_line_counts_as_a_send(env):
    d = env.tmp / "group_openers"
    d.mkdir(parents=True)
    (d / f"{go.day_of(NOW)}.jsonl").write_text('{"at": 1, "platform": "tele\n', encoding="utf-8")
    assert go.sends_today_in("telegram", TG_ROOM, now=NOW) >= 1


# ── not after an opener nobody answered ────────────────────────────────────

@pytest.mark.asyncio
async def test_not_after_an_opener_nobody_answered(env):
    yesterday = NOW - 86400
    _ledger(env.tmp, go.day_of(yesterday), [{"at": yesterday, "platform": "telegram",
                                             "channel_id": TG_ROOM, "text": "how's everyone?",
                                             "chars": 15}])
    _write_room(env.sessions, "telegram", TG_ROOM, [
        _row("user", "anyone around", 86400 + 3600, who="Sam"),
        _row("assistant", "how's everyone?", 86400),
        _row("assistant", "a later line of mine", 3600),     # her own lines don't answer her
    ])
    llm = _LLM(_answer(True, "hello?"))
    assert await go.run_pass(owner_id=1, now=NOW, llm=llm) == "skipped:unanswered"
    assert llm.prompts == []


@pytest.mark.asyncio
async def test_an_answered_opener_shows_as_answered(env):
    yesterday = NOW - 86400
    _ledger(env.tmp, go.day_of(yesterday), [{"at": yesterday, "platform": "telegram",
                                             "channel_id": TG_ROOM, "text": "how's everyone?",
                                             "chars": 15}])
    _write_room(env.sessions, "telegram", TG_ROOM, [
        _row("assistant", "how's everyone?", 86400),
        _row("user", "good! busy week", 80000, who="Dana"),
    ])
    llm = _LLM()
    assert await go.run_pass(owner_id=1, now=NOW, llm=llm) == "no_start"
    prompt = llm.prompts[0]
    assert '"how\'s everyone?"' in prompt
    assert "someone wrote in the room after it" in prompt
    assert "24 hours ago" in prompt


def test_openers_render_newest_last_at_most_three():
    rows = [{"at": NOW - 86400 * (5 - i), "text": f"opener {i}"} for i in range(5)]
    msgs = []
    text = go.render_openers(rows, msgs, now=NOW)
    assert "opener 0" not in text and "opener 1" not in text
    assert text.index("opener 2") < text.index("opener 3") < text.index("opener 4")
    assert text.count("nobody wrote in the room after it") == 3
    assert go.render_openers([], msgs, now=NOW) == "(none yet)"


# ── which rooms ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_never_a_telegram_private_chat(env):
    _quiet_room(env, channel=4242)
    llm = _LLM(_answer(True, "hey"))
    assert await go.run_pass(owner_id=1, now=NOW, llm=llm) == "skipped:not_a_group"
    assert llm.prompts == []


@pytest.mark.asyncio
async def test_never_a_discord_dm(env):
    _quiet_room(env, platform="discord", channel=DC_DM)
    llm = _LLM(_answer(True, "hey"))
    assert await go.run_pass(owner_id=1, now=NOW, llm=llm) == "skipped:not_a_group"
    assert llm.prompts == []


@pytest.mark.asyncio
async def test_an_unknown_discord_channel_is_skipped(env):
    _quiet_room(env, platform="discord", channel=777)
    llm = _LLM(_answer(True, "hey"))
    assert await go.run_pass(owner_id=1, now=NOW, llm=llm) == "skipped:not_a_group"


@pytest.mark.asyncio
async def test_a_chat_the_door_shows_private_is_skipped(env, monkeypatch):
    _quiet_room(env, platform="discord", channel=DC_ROOM)

    async def _private(platform, channel_id):
        return True

    monkeypatch.setattr(go, "_is_private", _private)
    assert await go.run_pass(owner_id=1, now=NOW, llm=_LLM(_answer(True, "hey"))) == "skipped:not_a_group"


@pytest.mark.asyncio
async def test_a_discord_server_channel_gets_its_room_card(env):
    _quiet_room(env, platform="discord", channel=DC_ROOM)
    llm = _LLM()
    assert await go.run_pass(owner_id=1, now=NOW, llm=llm) == "no_start"
    prompt = llm.prompts[0]
    assert "#general" in prompt and "Book Club" in prompt
    assert "## This Room" not in prompt


@pytest.mark.asyncio
async def test_never_with_a_standing_room_note(env):
    from core import room_notes

    _quiet_room(env)
    room_notes.set_note(f"telegram:{TG_ROOM}", "quiet in here please",
                        datetime.now() + timedelta(days=1))
    llm = _LLM(_answer(True, "hey"))
    assert await go.run_pass(owner_id=1, now=NOW, llm=llm) == "skipped:note"
    assert llm.prompts == []


@pytest.mark.asyncio
async def test_an_unreadable_notes_file_skips(env):
    _quiet_room(env)
    (env.tmp / "room_notes.json").write_text("{not json", encoding="utf-8")
    assert await go.run_pass(owner_id=1, now=NOW, llm=_LLM(_answer(True, "hey"))) == "skipped:note"


@pytest.mark.asyncio
async def test_never_mid_conversation(env):
    _quiet_room(env, ago=300)
    llm = _LLM(_answer(True, "hey"))
    assert await go.run_pass(owner_id=1, now=NOW, llm=llm) == "skipped:mid_conversation"
    assert llm.prompts == []


@pytest.mark.asyncio
async def test_her_own_recent_line_counts_as_mid_conversation(env):
    _write_room(env.sessions, "telegram", TG_ROOM, [
        _row("user", "hey", 7200, who="Sam"), _row("assistant", "hi Sam", 120)])
    assert await go.run_pass(owner_id=1, now=NOW, llm=_LLM()) == "skipped:mid_conversation"


@pytest.mark.asyncio
async def test_a_room_quiet_for_over_14_days_is_not_a_candidate(env):
    _quiet_room(env, ago=15 * 86400)
    llm = _LLM(_answer(True, "hey"))
    assert await go.run_pass(owner_id=1, now=NOW, llm=llm) == "none"
    assert llm.prompts == []


@pytest.mark.asyncio
async def test_a_room_where_only_she_spoke_lately_is_not_a_candidate(env):
    _write_room(env.sessions, "telegram", TG_ROOM, [
        _row("user", "hey", 20 * 86400, who="Sam"), _row("assistant", "anyone?", 2 * 86400)])
    assert await go.run_pass(owner_id=1, now=NOW, llm=_LLM()) == "none"


@pytest.mark.asyncio
async def test_a_quiet_room_is_fine_to_look_at(env):
    _quiet_room(env, ago=5 * 86400)
    llm = _LLM()
    assert await go.run_pass(owner_id=1, now=NOW, llm=llm) == "no_start"
    assert "5 days ago" in llm.prompts[0]


@pytest.mark.asyncio
async def test_other_session_files_are_never_rooms(env):
    for name in ("voice_1.jsonl", "dashboard_1.jsonl", "discord_voice_1.jsonl",
                 "proactive_1.jsonl", "telegram_-100_guest_5.jsonl"):
        (env.sessions).mkdir(parents=True, exist_ok=True)
        (env.sessions / name).write_text(json.dumps(_row("user", "hey", 3600)) + "\n")
    assert await go.run_pass(owner_id=1, now=NOW, llm=_LLM()) == "none"


# ── the loop: one room a pass, each room every 3 hours at most ─────────────

@pytest.mark.asyncio
async def test_one_room_per_pass(env):
    _quiet_room(env)
    _quiet_room(env, platform="discord", channel=DC_ROOM)
    llm = _LLM()
    await go.run_pass(owner_id=1, now=NOW, llm=llm)
    assert len(llm.prompts) == 1


@pytest.mark.asyncio
async def test_a_room_is_looked_at_every_three_hours_at_most(env):
    _quiet_room(env, ago=600 + 1)
    llm = _LLM()
    assert await go.run_pass(owner_id=1, now=NOW, llm=llm) == "no_start"
    assert await go.run_pass(owner_id=1, now=NOW + 3600, llm=llm) == "none"
    assert await go.run_pass(owner_id=1, now=NOW + 3 * 3600 - 5, llm=llm) == "none"
    assert len(llm.prompts) == 1
    assert await go.run_pass(owner_id=1, now=NOW + 3 * 3600 + 5, llm=llm) == "no_start"
    assert len(llm.prompts) == 2


@pytest.mark.asyncio
async def test_a_skip_counts_as_a_look(env):
    _quiet_room(env, ago=300)
    assert await go.run_pass(owner_id=1, now=NOW, llm=_LLM()) == "skipped:mid_conversation"
    assert await go.run_pass(owner_id=1, now=NOW + 600, llm=_LLM()) == "none"


@pytest.mark.asyncio
async def test_the_room_least_recently_looked_at_goes_first(env):
    _quiet_room(env)
    _quiet_room(env, platform="discord", channel=DC_ROOM)
    state = env.tmp / "group_openers" / "state.json"
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text(json.dumps({"looked": {f"telegram:{TG_ROOM}": NOW - 4 * 3600,
                                            f"discord:{DC_ROOM}": NOW - 5 * 3600}}))
    llm = _LLM()
    await go.run_pass(owner_id=1, now=NOW, llm=llm)
    assert "#general" in llm.prompts[0]
    llm2 = _LLM()
    await go.run_pass(owner_id=1, now=NOW + 60, llm=llm2)
    assert "Telegram group" in llm2.prompts[0]


@pytest.mark.asyncio
async def test_an_unreadable_state_file_looks_at_nothing_and_is_kept(env):
    _quiet_room(env)
    state = env.tmp / "group_openers" / "state.json"
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text("{oops", encoding="utf-8")
    llm = _LLM(_answer(True, "hey"))
    assert await go.run_pass(owner_id=1, now=NOW, llm=llm) == "state_unreadable"
    assert llm.prompts == []
    assert state.read_text(encoding="utf-8") == "{oops"


# ── what it sees ───────────────────────────────────────────────────────────

def _d6b_text() -> str:
    path = REPO / "docs" / "2026-10-13-presence-slice-words.md"
    if not path.is_file():   # docs/ doesn't ship in the public copy
        pytest.skip("the approved words file is not in this tree")
    words = path.read_text(encoding="utf-8")
    section = words.split("## D6b.", 1)[1]
    return re.search(r"```\n(.*?)\n```", section, re.S).group(1)


def test_the_prompt_is_d6b_word_for_word():
    text = (REPO / "prompts" / "group_opener.txt").read_text(encoding="utf-8")
    assert text.strip() == _d6b_text().strip()


def test_the_prompt_fills_with_its_six_slots():
    out = go.build_prompt(current_time="T", room_card="C", room_lines="L", since_last="S",
                          my_openers="O", my_own_life="M")
    assert out.startswith("It's T. This is me on my own")
    assert '{"start": true|false, "message":' in out
    for slot in ("{current_time}", "{room_card}", "{room_lines}", "{since_last}",
                 "{my_openers}", "{my_own_life}"):
        assert slot not in out


@pytest.mark.asyncio
async def test_own_life_lines_reach_the_prompt(env, monkeypatch):
    _quiet_room(env)
    fake = types.ModuleType("core.agent.own_life")

    async def room_safe_lines():
        return "- [o1] I've been reading about tide pools."

    fake.room_safe_lines = room_safe_lines
    monkeypatch.setitem(sys.modules, "core.agent.own_life", fake)
    monkeypatch.setattr(go, "_own_life", go.__dict__["_own_life_lines"])
    llm = _LLM()
    await go.run_pass(owner_id=1, now=NOW, llm=llm)
    assert "- [o1] I've been reading about tide pools." in llm.prompts[0]


@pytest.mark.asyncio
@pytest.mark.parametrize("module", ["missing", "raises", "empty"])
async def test_own_life_falls_back_to_nothing_right_now(env, monkeypatch, module):
    if module == "missing":
        monkeypatch.setitem(sys.modules, "core.agent.own_life", None)
    else:
        fake = types.ModuleType("core.agent.own_life")

        async def room_safe_lines():
            if module == "raises":
                raise RuntimeError("store broke")
            return ""

        fake.room_safe_lines = room_safe_lines
        monkeypatch.setitem(sys.modules, "core.agent.own_life", fake)
    assert await go._own_life_lines() == "(nothing right now)"


@pytest.mark.asyncio
async def test_the_room_lines_carry_who_said_them(env):
    _write_room(env.sessions, "telegram", TG_ROOM, [
        _row("user", "anyone finished the book yet?", 7200, who="Sam"),
        _row("user", "not yet, chapter 9", 3700, who="Robin", kind="owner"),
        _row("assistant", "Halfway myself.", 3600),
    ])
    llm = _LLM()
    await go.run_pass(owner_id=1, now=NOW, llm=llm)
    prompt = llm.prompts[0]
    assert "anyone finished the book yet?" in prompt and "Sam" in prompt
    assert "★owner" in prompt
    assert "PaLLi: Halfway myself." in prompt
    assert "Last anyone spoke here: 1 hour ago." in prompt
    assert "MY OWN LAST OPENERS HERE, AND WHETHER ANYONE ANSWERED:\n(none yet)" in prompt
    assert "(nothing right now)" in prompt


MARK = "ZQX-PRIVATE-"


@pytest.mark.asyncio
async def test_nothing_of_the_owners_private_life_reaches_the_prompt(env, monkeypatch):
    """A unique marker planted in every private store this code could reach:
    the call is built from the room's session alone, so none may appear, and
    no private store is even read."""
    reached: list[str] = []

    def plant(module_name: str, attr: str, *, is_async: bool = False, value=None):
        mod = __import__(module_name, fromlist=[attr])
        assert hasattr(mod, attr), f"{module_name}.{attr} moved: update the planting list"
        marker = f"{MARK}{module_name}.{attr}"
        result = marker if value is None else value(marker)

        if is_async:
            async def fake(*a, **k):
                reached.append(marker)
                return result
        else:
            def fake(*a, **k):
                reached.append(marker)
                return result
        monkeypatch.setattr(mod, attr, fake)

    pick = lambda m: SimpleNamespace(text=m, said_at=None, day="2026-10-13")  # noqa: E731
    # stakes (hopes, worries, views about the owner's world)
    plant("ai.stake_ledger", "open_stakes", is_async=True, value=lambda m: [m])
    plant("ai.stake_ledger", "recent_stakes", is_async=True, value=lambda m: [m])
    plant("ai.stake_ledger", "render_stakes_block")
    # tonight's pick
    plant("ai.tonight", "load_current", value=pick)
    plant("ai.tonight", "render_band")
    # small truths (disclosures)
    plant("ai.disclosure_store", "unsaid_disclosures", value=lambda m: [m])
    plant("ai.disclosure_store", "render_disclosures_band")
    plant("ai.disclosure_store", "render_disclosures_block")
    # fears and daydreams the room check did NOT pass
    plant("ai.fear_store", "held_fears", value=lambda m: [m])
    plant("ai.fear_store", "render_fears_block")
    plant("ai.daydream_store", "read_recent", value=lambda m: [m])
    plant("ai.daydream_store", "read_intent")
    # the present moment, moods, relationship, the recent arc
    plant("ai.present_moment_store", "read_current", value=lambda m: SimpleNamespace(text=m))
    plant("ai.present_moment_store", "render_reply_band")
    plant("ai.relationship_momentum", "render_posture")
    plant("ai.turn_read", "render_recent_arc", is_async=True)
    # wants, her notes
    plant("ai.quest_store", "render_board")
    plant("ai.self_notes_store", "read_recent", value=lambda m: [m])
    # memory / recall / the reply's bands
    plant("core.turn.recall", "build_turn_context", is_async=True)
    plant("core.turn.recall", "fetch_recent_history", is_async=True, value=lambda m: [m])
    plant("core.turn.recall", "_read_present_moment_band")
    plant("core.turn.recall", "_read_disclosures_band")
    plant("core.turn.recall", "_read_tonight_band")
    plant("core.turn.recall", "_read_recent_deeds_band")
    # the owner's profile
    from core.database import brain

    async def profile(*a, **k):
        reached.append(MARK + "profile")
        return {"name": MARK + "profile"}

    monkeypatch.setattr(brain, "get_user_profile", profile, raising=False)
    # the owner's private chat and another room, on disk beside this one
    _write_room(env.sessions, "discord", DC_DM, [
        _row("user", MARK + "dm: my private worry", 20 * 86400, who="Robin", kind="owner")])
    _write_room(env.sessions, "telegram", -555, [
        _row("user", MARK + "another room", 30 * 86400, who="Jo")])

    # only own-life lines that passed the room check
    fake = types.ModuleType("core.agent.own_life")

    async def room_safe_lines():
        return "- [o1] SAFE-OWN-LIFE"

    fake.room_safe_lines = room_safe_lines
    monkeypatch.setitem(sys.modules, "core.agent.own_life", fake)
    monkeypatch.setattr(go, "_own_life", go.__dict__["_own_life_lines"])

    _quiet_room(env)
    llm = _LLM()
    assert await go.run_pass(owner_id=1, now=NOW, llm=llm) == "no_start"
    prompt = llm.prompts[0]
    assert "SAFE-OWN-LIFE" in prompt
    assert MARK not in prompt
    assert reached == []


def test_the_module_imports_no_private_store():
    import ast

    tree = ast.parse((REPO / "core" / "turn" / "group_openers.py").read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            names.add(node.module or "")
            names.update(f"{node.module}.{a.name}" for a in node.names)
    banned = ("ai.stake_ledger", "ai.tonight", "ai.disclosure_store", "ai.fear_store",
              "ai.daydream_store", "ai.present_moment", "ai.relationship_momentum",
              "ai.turn_read", "ai.quest_store", "ai.self_notes_store", "core.turn.recall",
              "core.memory_store", "core.database", "ai.owner_picture",
              "core.agent.first_look", "core.agent.context_adapter")
    hits = sorted(n for n in names for b in banned if n == b or n.startswith(b + "."))
    assert hits == []


# ── delivery: the opener lands in the room's own transcript ────────────────

@pytest.mark.asyncio
async def test_the_real_release_leg_writes_the_opener_into_the_rooms_transcript(env, monkeypatch):
    import core.channel_runtime as cr
    import core.turn.persistence as persistence
    from core.agent import room_tail

    monkeypatch.setattr(go, "_deliver", go.__dict__["_release"])
    sent = AsyncMock(return_value={})
    monkeypatch.setattr(cr.channel_runtime, "send_envelope", sent)
    monkeypatch.setattr(persistence, "_persist_assistant_side_to_history", AsyncMock())
    import ai.recent_deeds as deeds

    monkeypatch.setattr(deeds, "record_deed", lambda *a, **k: None)
    _quiet_room(env)
    assert await go.run_pass(owner_id=1, now=NOW, llm=_LLM(_answer(True, "Who's close to the end?"))) == "sent"
    assert sent.await_args.kwargs["platform"] == "telegram"
    assert sent.await_args.kwargs["surface_id"] == str(TG_ROOM)
    messages = await room_tail.load(f"telegram:{TG_ROOM}", root=env.sessions)
    assert messages[-1].role == "assistant"
    assert room_tail._words(messages[-1]) == "Who's close to the end?"


# ── where it rides ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_it_rides_the_outreach_residuals_individually_guarded(monkeypatch):
    import core.turn.proactive as pro

    calls = []

    async def ok(**k):
        calls.append("ran")

    monkeypatch.setattr(pro, "_ensure_stake_threads", AsyncMock(side_effect=ok))
    monkeypatch.setattr(pro, "_maybe_run_arbiter_sweep", AsyncMock(side_effect=ok))
    monkeypatch.setattr(pro, "_consider_candidate_rail", AsyncMock(side_effect=RuntimeError("x")))
    opener = AsyncMock(return_value="none")
    monkeypatch.setattr(go, "run_pass", opener)
    await pro.run_outreach_residuals(owner_id=3)
    opener.assert_awaited_once()
    assert opener.await_args.kwargs["owner_id"] == 3

    monkeypatch.setattr(go, "run_pass", AsyncMock(side_effect=RuntimeError("boom")))
    await pro.run_outreach_residuals(owner_id=3)       # never raises


# ── settings, lane ─────────────────────────────────────────────────────────

def test_the_settings_ship_on():
    import yaml

    from core.settings_schema import CompanionConfig

    c = CompanionConfig()
    assert c.group_openers is True and c.group_openers_per_day == 1
    for name in ("palli.yaml", "palli.example.yaml"):
        data = yaml.safe_load((REPO / "data" / name).read_text(encoding="utf-8"))
        assert data["companion"]["group_openers"] is True, name
        assert data["companion"]["group_openers_per_day"] == 1, name


def test_the_lane_is_patient_and_does_not_think():
    from ai.llm_openai_compatible import OpenAICompatibleProvider, is_no_think_lane

    assert go.CALL_ARGS["log_context"] == "group_opener"
    assert is_no_think_lane("group_opener")
    assert (OpenAICompatibleProvider.request_timeout_for_context("group_opener")
            == float(OpenAICompatibleProvider.PATIENT_REQUEST_TIMEOUT_SECONDS))


def test_a_naive_line_time_reads_as_utc_like_the_rest_of_her_payload():
    # Fable's final review (10-13): message_times.stamp_label reads a naive
    # stamp as UTC; the opener's clock must agree.
    from datetime import datetime, timezone
    from types import SimpleNamespace

    from core.turn import group_openers as go

    naive = SimpleNamespace(timestamp=datetime(2026, 10, 13, 12, 0, 0))
    aware = SimpleNamespace(timestamp=datetime(2026, 10, 13, 12, 0, 0, tzinfo=timezone.utc))
    assert go._when(naive) == go._when(aware)
