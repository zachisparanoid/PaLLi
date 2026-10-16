"""Initiative learns (Batch 6, task 6; item 3.9 of the fix plan).

Three things, all on the one rail:

* The daily ceiling fails CLOSED. A ledger that exists but cannot be read
  is a full day, never an empty one; one damaged line counts as one send. A
  missing ledger is the normal state before the day's first send and stays 0.
* The ceiling is one named gate, ``admit_or_hold()``, asked by the say lane
  before the freshness judge, and every organic leg (the say, surprise
  naming, regret voicing, a view, a small truth, tonight's pick, the arrival
  greeting) rides that lane. The share lane does not: workbench shares stay
  exempt from the ceiling (the owner, 2026-09-29).
* The judge sees her last five text reach-outs, newest first, each marked
  got a reply / no reply / no reply yet — but only while the arbiter is on.
  The share lane runs the same judge with the arbiter off, and then its
  prompt is the pre-batch prompt, byte for byte (fix round 1).

Every test pins its own flags: owner flips are environment (3352e3d). The
release path below is the real one down to the door, which is a stand-in.
"""
from __future__ import annotations

import threading
import time
import types
from unittest.mock import AsyncMock, patch

import pytest

import ai.arbiter as arbiter
from core.turn import outbound
from core.turn.types import Turn, TurnMetadata
from tests.ai.test_arbiter import _Comp

# Captured at import time, before any fixture swaps the seam.
_REAL_FRESHNESS_CHECK = arbiter._freshness_check
_HOUR = 3600.0


# ── R39: the ceiling fails closed ────────────────────────────────────────

def test_an_undecodable_line_is_one_send_not_a_full_day(monkeypatch, tmp_path):
    """Second read F5 (2026-10-04): bytes that are not UTF-8 are one bad line
    — one send charged — never a decode error that fills the whole day. (This
    test used to pin the full day; R39 says one damaged line costs her at
    most one reach-out.)"""
    from ai import arbiter
    monkeypatch.setattr(arbiter, "_ledger_path", lambda: tmp_path / "missing-dir" / "ledger.jsonl")
    monkeypatch.setattr(arbiter, "_ceiling", lambda: 3)
    (tmp_path / "missing-dir").mkdir()
    (tmp_path / "missing-dir" / "ledger.jsonl").write_bytes(b"\xff\xfe not json")
    assert arbiter.sends_today() == 1


def test_a_row_torn_inside_an_em_dash_costs_one_send(monkeypatch, tmp_path, caplog):
    """Second read F5: a crash mid-append cuts the last row inside a
    multi-byte character — her words carry em dashes and curly quotes on
    every row. One real send plus the torn row is two, not the ceiling, and
    nothing is logged as an unreadable ledger."""
    import json

    monkeypatch.setattr(arbiter, "_ceiling", lambda: 3)
    monkeypatch.setattr(arbiter, "_UNREADABLE_WARNED", None)
    ledger = tmp_path / "ledger.jsonl"
    monkeypatch.setattr(arbiter, "_ledger_path", lambda: ledger)
    good = json.dumps({"at": 1.0, "disposition": "sent", "why": "a line with an em dash — here"},
                      ensure_ascii=False) + "\n"
    torn = json.dumps({"at": 2.0, "disposition": "stale_dropped", "reason": "— torn"},
                      ensure_ascii=False).encode("utf-8")
    cut = torn.index("—".encode("utf-8")) + 1
    ledger.write_bytes(good.encode("utf-8") + torn[:cut])

    with caplog.at_level("WARNING", logger="ai.arbiter"):
        assert arbiter.sends_today() == 2
    assert not any("ledger unreadable" in r.getMessage() for r in caplog.records)
    assert arbiter.admit_or_hold() is None              # room for one more today


def test_a_missing_ledger_is_an_empty_day(monkeypatch, tmp_path):
    monkeypatch.setattr(arbiter, "_ceiling", lambda: 3)
    monkeypatch.setattr(arbiter, "_ledger_path", lambda: tmp_path / "ledger.jsonl")
    assert arbiter.sends_today() == 0
    monkeypatch.setattr(arbiter, "_ledger_path",
                        lambda: tmp_path / "no-such-dir" / "ledger.jsonl")
    assert arbiter.sends_today() == 0


def test_a_ledger_that_cannot_be_opened_is_a_full_day(monkeypatch, tmp_path):
    monkeypatch.setattr(arbiter, "_companion", lambda: _Comp(ceiling=2))
    monkeypatch.setattr(arbiter, "_ledger_path", lambda: tmp_path)   # a directory
    assert arbiter.sends_today() == 2


def test_one_damaged_line_counts_as_one_send(monkeypatch, tmp_path):
    ledger = tmp_path / "ledger.jsonl"
    ledger.write_text(
        '{"disposition": "sent"}\n'
        '{"disposition": "sent_share"}\n'
        '{"disposition": "stale_dropped"}\n'
        '{not json\n'
        '\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(arbiter, "_ledger_path", lambda: ledger)
    assert arbiter.sends_today() == 2


def test_an_unreadable_ledger_warns_once_a_day_and_still_counts_a_full_day(
        monkeypatch, tmp_path, caplog):
    """Fix wave (R74 M4): the moment asks on every cycle, so the warning is
    latched once per ledger day — never the ceiling, which every call keeps."""
    monkeypatch.setattr(arbiter, "_companion", lambda: _Comp(ceiling=2))
    today, tomorrow = tmp_path / "2026-08-12.jsonl", tmp_path / "2026-08-13.jsonl"
    today.mkdir()                                          # a directory: unreadable
    tomorrow.mkdir()
    monkeypatch.setattr(arbiter, "_ledger_path", lambda: today)

    def warnings() -> int:
        return sum(1 for r in caplog.records
                   if r.levelname == "WARNING" and "ledger unreadable" in r.getMessage())

    with caplog.at_level("WARNING", logger="ai.arbiter"):
        assert arbiter.sends_today() == 2
        assert arbiter.sends_today() == 2
        assert warnings() == 1
        # The next day's ledger, unreadable too: said again, once.
        monkeypatch.setattr(arbiter, "_ledger_path", lambda: tomorrow)
        assert arbiter.sends_today() == 2
        assert arbiter.sends_today() == 2
        assert warnings() == 2


def test_the_ceiling_is_the_knob(monkeypatch):
    monkeypatch.setattr(arbiter, "_companion", lambda: _Comp(ceiling=5))
    assert arbiter._ceiling() == 5
    monkeypatch.setattr(arbiter, "_companion", lambda: None)
    assert arbiter._ceiling() == 3


# ── the rail, real down to the door ──────────────────────────────────────

class _Door:
    def __init__(self):
        self.sent = []

    async def send_envelope(self, *, platform, surface_id, envelope):
        self.sent.append((platform, surface_id, envelope.text))


@pytest.fixture()
def rail(monkeypatch, tmp_path):
    """Arbiter on, quiet everywhere, the owner's last chat is Discord room
    123. The judge says fresh and counts its calls. Everything from the gate
    to the custody legs is real; the door and the conversations store are
    stand-ins."""
    monkeypatch.setattr(arbiter, "ARBITER_DATA_DIR", tmp_path / "arbiter")
    comp = _Comp(surprise_daily_named=0)
    monkeypatch.setattr(arbiter, "_companion", lambda: comp)

    judged = []

    async def _judge(**kw):
        judged.append(kw)
        return (True, "fresh (fixture)")

    async def _chan(*, owner_id):
        return ("discord", "123")

    async def _last_msg(*, owner_id):
        return time.time() - _HOUR

    async def _actives(*, owner_user_id):
        return []

    monkeypatch.setattr(arbiter, "_freshness_check", _judge)
    monkeypatch.setattr(arbiter, "_pick_surface", _chan)
    monkeypatch.setattr(arbiter, "_last_user_message_at", _last_msg)
    monkeypatch.setattr(arbiter, "_list_active", _actives)
    monkeypatch.setattr(arbiter, "_run_phase", lambda sk: "")
    monkeypatch.setattr(arbiter, "_is_working", lambda sk: False)

    door = _Door()
    monkeypatch.setattr("core.channel_runtime.channel_runtime", door)

    async def _assistant_row(*, turn, response_text):
        return None

    monkeypatch.setattr(
        "core.turn.persistence._persist_assistant_side_to_history", _assistant_row
    )
    monkeypatch.setenv("PALLI_AGENT_SESSIONS_ROOT", str(tmp_path / "sessions"))

    import core.conversation_history_store as chs

    stored = []

    class _Store:
        def __init__(self, brain):
            pass

        async def store_conversation(self, **kw):
            stored.append(kw)

    monkeypatch.setattr(chs, "ConversationHistoryStore", _Store)
    return types.SimpleNamespace(comp=comp, judged=judged, sent=door.sent,
                                 stored=stored)


def _say(**kw):
    d = {"kind": "say", "why": "w", "framing": "the page you asked about is finished",
         "sensitivity": "routine"}
    d.update(kw)
    return d


def _fill_the_day(n=3):
    for _ in range(n):
        arbiter._ledger_append({"at": time.time(), "disposition": "sent",
                                "channel_id": 123})


# ── R40: admit_or_hold, the one named gate ───────────────────────────────

def test_admit_or_hold_admits_under_the_ceiling_and_holds_at_it(rail):
    assert arbiter.admit_or_hold() is None
    _fill_the_day(2)
    assert arbiter.admit_or_hold() is None
    _fill_the_day(1)
    assert arbiter.admit_or_hold() == {"disposition": "ceiling_blocked", "ceiling": 3}
    rail.comp.arbiter_daily_send_ceiling = 0                 # rail dark
    assert arbiter.admit_or_hold() == {"disposition": "ceiling_blocked", "ceiling": 0}


@pytest.mark.asyncio
async def test_the_say_lane_asks_admit_or_hold_before_the_judge(rail, monkeypatch):
    held = {"disposition": "ceiling_blocked", "ceiling": 99}
    monkeypatch.setattr(arbiter, "admit_or_hold", lambda: held)
    out = await arbiter.consider_release(impulse=_say(), owner_user_id=1,
                                         arbiter_live=True)
    assert out is held
    assert rail.judged == [] and rail.sent == []


@pytest.mark.asyncio
async def test_an_unreadable_ledger_holds_the_say_before_the_judge(rail):
    ledger = arbiter._ledger_path()
    ledger.parent.mkdir(parents=True, exist_ok=True)
    # Unreadable = it cannot be read at all (a directory where the file
    # goes). Second read F5: bytes that are not UTF-8 are one bad line now,
    # so they no longer stand in for an unreadable ledger here.
    ledger.mkdir()
    out = await arbiter.consider_release(impulse=_say(), owner_user_id=1,
                                         arbiter_live=True)
    assert out == {"disposition": "ceiling_blocked", "ceiling": 3}
    assert rail.judged == [] and rail.sent == []


_ORGANIC_LEGS = {
    "the say": {},
    "surprise naming": {"sensitivity": "surprise"},
    "regret voicing": {"regret_id": "regret 7"},
    "a view": {"view_id": "view 3"},
    "a small truth": {"disclosure_id": "a-small-truth"},
    "tonight's pick": {"thread_id": "tonight"},
}


@pytest.mark.asyncio
@pytest.mark.parametrize("leg", list(_ORGANIC_LEGS))
async def test_every_organic_leg_passes_the_ceiling(rail, monkeypatch, leg):
    """A full day holds each leg at the ceiling: no judge call, no send, no
    stamp. Surprise naming would otherwise meet its own budget (0 here) —
    the ceiling comes first."""
    touched = []

    def _no(name):
        def _inner(*a, **kw):
            touched.append(name)
            raise AssertionError(f"{name} ran past a full ceiling")
        return _inner

    monkeypatch.setattr(arbiter, "_vulnerability_on", lambda: True)
    monkeypatch.setattr("ai.disclosure_store.resolve_disclosure_id", _no("resolve"))
    monkeypatch.setattr("ai.disclosure_store.mark_said", _no("mark_said"))
    monkeypatch.setattr("ai.stake_ledger.mark_voiced", _no("mark_voiced"))
    monkeypatch.setattr("ai.stake_ledger.mark_view_voiced", _no("mark_view_voiced"))
    _fill_the_day()

    out = await arbiter.consider_release(impulse=_say(**_ORGANIC_LEGS[leg]),
                                         owner_user_id=1, arbiter_live=True)
    assert out == {"disposition": "ceiling_blocked", "ceiling": 3}
    assert rail.judged == [] and rail.sent == [] and touched == []


@pytest.mark.asyncio
async def test_the_arrival_greeting_is_the_arbiters_own_say_and_a_say_passes_the_ceiling_gate(rail):
    """Two things, as named. With the arbiter on, the arrival greeting is
    the arbiter's own say: the ambient planner reports that the arbiter owns
    arrivals (the planner's yield itself is pinned in
    tests/core/test_arbiter_retirements.py). And a say passes the ceiling
    gate: on a full day the gate stops it, before the judge, and it is not
    sent."""
    import core.ambient_service as amb

    assert amb._arbiter_owns_arrivals() is True
    _fill_the_day()
    out = await arbiter.consider_release(impulse=_say(framing="welcome back"),
                                         owner_user_id=1, arbiter_live=True)
    assert out == {"disposition": "ceiling_blocked", "ceiling": 3}
    assert rail.judged == [] and rail.sent == []


@pytest.mark.asyncio
async def test_a_share_never_asks_admit_or_hold(rail, monkeypatch):
    """The owner's answer (2026-09-29): workbench shares stay exempt from the
    ceiling. Rail dark still darkens them (test_arbiter_share_lane.py)."""
    def _never():
        raise AssertionError("a share must not ask the daily ceiling")

    monkeypatch.setattr(arbiter, "admit_or_hold", _never)
    _fill_the_day()
    v = await arbiter.deliver_share(text="made a thing", owner_user_id=1)
    assert v["disposition"] == "sent"
    assert rail.sent == [("discord", "123", "made a thing")]


@pytest.mark.asyncio
async def test_the_arbiter_off_is_still_silent(rail):
    rail.comp.arbiter = False
    assert await arbiter.consider_release(impulse=_say(), owner_user_id=1,
                                          arbiter_live=True) == {}
    assert not outbound._reachouts_path().exists()


# ── R46: what the judge sees ─────────────────────────────────────────────

async def _judge_prompt(*, history=(), deeds=()):
    """The real judge, its model a stand-in that keeps the prompt. The
    conversation and her deeds are fixed stand-ins, so a whole prompt can be
    compared byte for byte."""
    seen = {}

    async def _judge_llm(messages, **kw):
        seen["content"] = str((messages or [{}])[0].get("content") or "")
        return '{"fresh": true, "reason": "fine"}'

    class _L:
        chat_completion = staticmethod(_judge_llm)

    async def _refs():
        return {"palli_user_id": 1}

    async def _history(*, user_id, limit, include_metadata):
        return [dict(r) for r in history]

    with patch("core.local_mode.get_local_owner_refs",
               new=AsyncMock(side_effect=_refs)), \
         patch("core.database.brain.get_conversation_history",
               new=AsyncMock(side_effect=_history)), \
         patch("ai.recent_deeds.read_recent_deeds", lambda **kw: list(deeds)), \
         patch("ai.llm_client.llm", _L()):
        verdict = await _REAL_FRESHNESS_CHECK(
            framing="a new message", owner_user_id=1, held_before=False)
    return seen["content"], verdict


def _arbiter_on(monkeypatch):
    """The book is the arbiter's: the judge shows the list only while it is
    on. (The default test world is arbiter-off, tests/conftest.py.)"""
    monkeypatch.setattr(arbiter, "_companion", lambda: _Comp())


# ── fix round 1 (I1): the list sits behind the arbiter's flag ────────────
# The judge's template as it was before Batch 6 —
#     git show 1ee0e6b:prompts/arbiter_freshness.txt
# — with the ONE word changed since then on purpose: the sibling fix round
# d429ec0 turned "It is FRESH if it still lands as new" into "It is FRESH if
# it still reads as new" (the owner's plain-words rule). Every other byte is
# the pre-batch file's.
_PRE_BATCH_TEMPLATE = """\
You are PaLLi, about to send the owner a message you composed a little
while ago. Before it goes out, look at the conversation as it stands RIGHT
NOW and answer one question honestly.

The message you are about to send (verbatim — you may not change it):
{framing}

{held_note}

The conversation as it stands now (newest last, full text):
{tail}

{deeds}

One judgment only: is this message STILL TRUE and STILL WORTH SENDING,
right now? It is STALE if the conversation shows the thing was already
answered, already done (check your deeds), already said by you, or the
moment for it has plainly passed. It is FRESH if it still reads as new
and worth their attention.

Reply with JSON only:
{{"fresh": true, "reason": "one short sentence"}}
"""

_HISTORY = ({"role": "user", "content": "is the page done?"},
            {"role": "assistant", "content": "nearly"})
_DEEDS = ({"detail": 'sent a message (arbiter): "the page is finished…"'},)


def _pre_batch_prompt() -> str:
    """The pre-batch template rendered the way the judge renders its own:
    the loader strips the file, then str.format fills the slots."""
    return _PRE_BATCH_TEMPLATE.strip().format(
        framing="a new message",
        held_note="This impulse formed within the last few minutes.",
        tail="[them] is the page done?\n[me] nearly",
        deeds="What you have DONE recently (actions, not words):\n"
              '- sent a message (arbiter): "the page is finished…"',
    )


@pytest.mark.asyncio
async def test_with_the_arbiter_off_the_judge_reads_the_pre_batch_prompt_byte_for_byte(
        monkeypatch):
    """The share lane runs this judge even with the arbiter off, and then the
    book is shut — nothing written, nothing marked — so a list would be false
    as well as new. With the arbiter off the prompt is the pre-batch prompt,
    byte for byte (the one deliberate word aside, see above), even with a
    reach-out on the book; and the book is not read at all."""
    monkeypatch.setattr(arbiter, "_companion", lambda: _Comp(arbiter=False))
    outbound.record_reachout(owner_id=1, platform="discord", channel_id=123,
                             text="on the book", origin="arbiter",
                             at=time.time() - 60)
    asked = []

    async def _spy():
        asked.append(True)
        return []

    monkeypatch.setattr(arbiter, "_recent_reachouts", _spy)
    prompt, verdict = await _judge_prompt(history=_HISTORY, deeds=_DEEDS)
    assert prompt == _pre_batch_prompt()
    assert asked == []
    assert verdict[0] is True


_SECTION_ONE_OF_EACH = (
    'The last messages you sent the owner on your own, newest first.\n'
    '"got a reply" means they wrote back in the same chat within six hours;\n'
    '"no reply" means they did not; "no reply yet" means it went out less\n'
    'than six hours ago:\n'
    '- 2 minutes ago: "just now" (no reply yet)\n'
    '- 20 hours ago: "none came" (no reply)\n'
    '- 30 hours ago: "a reply came" (got a reply)\n'
    '\n'
    'Your last messages show which kinds of message got a reply and which\n'
    'did not. Take that into account too.\n'
    '\n'
)


@pytest.mark.asyncio
async def test_with_the_arbiter_on_the_section_sits_between_the_deeds_and_the_question(
        monkeypatch):
    """With the arbiter on the section renders as before, in the same place
    — after her deeds, before the one question — and every byte this task
    added lives inside it: the pre-batch prompt plus the section, nothing
    else. The labels are got a reply / no reply / no reply yet, so
    "answered" keeps its one meaning in the question (a thing taken care of)."""
    _arbiter_on(monkeypatch)
    now = time.time()
    outbound.record_reachout(owner_id=1, platform="discord", channel_id=123,
                             text="a reply came", origin="arbiter",
                             at=now - 30 * _HOUR)
    outbound.mark_answered(owner_id=1, platform="discord", channel_id=123,
                           at=now - 30 * _HOUR + 600)
    outbound.record_reachout(owner_id=1, platform="discord", channel_id=123,
                             text="none came", origin="workbench_share",
                             at=now - 20 * _HOUR)
    outbound.record_reachout(owner_id=1, platform="discord", channel_id=123,
                             text="just now", origin="arbiter", at=now - 150)

    prompt, _ = await _judge_prompt(history=_HISTORY, deeds=_DEEDS)
    expected = _pre_batch_prompt().replace(
        "One judgment only:", _SECTION_ONE_OF_EACH + "One judgment only:")
    assert expected.count(_SECTION_ONE_OF_EACH) == 1
    assert prompt == expected


@pytest.mark.asyncio
async def test_before_any_reachout_the_judge_reads_one_plain_line(monkeypatch):
    _arbiter_on(monkeypatch)
    prompt, verdict = await _judge_prompt()
    assert "(none on record yet)" in prompt
    assert "STILL TRUE and STILL WORTH SENDING" in prompt
    assert "{" not in prompt.replace('{"fresh"', "")      # every slot filled
    assert verdict[0] is True


@pytest.mark.asyncio
async def test_the_judge_sees_the_last_five_text_reachouts_newest_first(monkeypatch):
    _arbiter_on(monkeypatch)
    now = time.time()

    def sent(head, ago, *, medium="text", origin="arbiter"):
        outbound.record_reachout(owner_id=1, platform="discord", channel_id=123,
                                 text=head, origin=origin, at=now - ago,
                                 medium=medium)

    sent("the oldest one", 80 * _HOUR)
    sent("too old to show", 72 * _HOUR)
    sent("never answered", 50 * _HOUR)
    sent("answered one", 30 * _HOUR)
    outbound.mark_answered(owner_id=1, platform="discord", channel_id=123,
                           at=now - 30 * _HOUR + 600)
    sent("a share", 20 * _HOUR, origin="workbench_share")
    sent("still waiting", 3 * _HOUR + 60)
    sent("said aloud", 2 * _HOUR, medium="voice")
    sent("just now", 150)

    prompt, _ = await _judge_prompt()
    lines = [
        '- 2 minutes ago: "just now" (no reply yet)',
        '- 3 hours ago: "still waiting" (no reply yet)',
        '- 20 hours ago: "a share" (no reply)',
        '- 30 hours ago: "answered one" (got a reply)',
        '- 2 days ago: "never answered" (no reply)',
    ]
    at = [prompt.index(line) for line in lines]
    assert at == sorted(at)
    for left_out in ("said aloud", "too old to show", "the oldest one",
                     "(none on record yet)"):
        assert left_out not in prompt


@pytest.mark.asyncio
async def test_a_list_that_cannot_be_read_shows_the_plain_line(monkeypatch):
    _arbiter_on(monkeypatch)

    async def _boom():
        raise RuntimeError("the book is unreadable")

    monkeypatch.setattr(arbiter, "_recent_reachouts", _boom)
    prompt, verdict = await _judge_prompt()
    assert "(none on record yet)" in prompt
    assert verdict[0] is True


@pytest.mark.asyncio
async def test_an_undecodable_book_shows_the_plain_line(monkeypatch):
    """A book whose only line is not UTF-8 has no reach-out to show."""
    _arbiter_on(monkeypatch)
    book = outbound._reachouts_path()
    book.parent.mkdir(parents=True, exist_ok=True)
    book.write_bytes(b"\xff\xfe not json")
    prompt, verdict = await _judge_prompt()
    assert "(none on record yet)" in prompt
    assert verdict[0] is True


@pytest.mark.asyncio
async def test_one_bad_byte_in_the_book_still_shows_the_rest(monkeypatch):
    """Fix round 1 (M1): a torn write spoils its own line, not the list."""
    _arbiter_on(monkeypatch)
    outbound.record_reachout(owner_id=1, platform="discord", channel_id=123,
                             text="still shown", origin="arbiter",
                             at=time.time() - 60)
    with outbound._reachouts_path().open("ab") as fh:
        fh.write(b'{"op": "sent", "id": "torn\xff\n')
    prompt, _ = await _judge_prompt()
    assert '- 1 minute ago: "still shown" (no reply yet)' in prompt
    assert "(none on record yet)" not in prompt


@pytest.mark.asyncio
async def test_the_list_is_read_off_the_event_loop(monkeypatch):
    loop_thread = threading.get_ident()
    seen = []

    def _spy(**kw):
        seen.append((threading.get_ident(), kw))
        return []

    monkeypatch.setattr(outbound, "recent_reachouts", _spy)
    assert await arbiter._recent_reachouts() == []
    [(thread, kw)] = seen
    assert thread != loop_thread
    assert kw == {"limit": 5, "medium": "text"}


# ── the whole loop: sent, answered, seen ─────────────────────────────────

@pytest.mark.asyncio
async def test_a_say_that_was_answered_reaches_the_next_judge_as_answered(rail):
    out = await arbiter.consider_release(impulse=_say(), owner_user_id=1,
                                         arbiter_live=True)
    assert out["disposition"] == "sent"
    assert rail.sent == [("discord", "123", "the page you asked about is finished")]
    [row] = outbound.read_reachouts()
    assert (row["origin"], row["platform"], row["channel_id"], row["medium"]) == (
        "arbiter", "discord", 123, "text")
    assert row["answered_at"] is None

    import core.turn.persistence as per

    reply = Turn(surface="discord_text", owner_id=1, content="nice, thanks",
                 metadata=TurnMetadata(origin="user", channel_id=123))
    await per._persist_user_side_with_content(reply, "nice, thanks")

    assert outbound.read_reachouts()[0]["answered_at"] is not None
    prompt, _ = await _judge_prompt()
    assert '"the page you asked about is finished" (got a reply)' in prompt
