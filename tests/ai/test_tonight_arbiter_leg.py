"""One thing on her mind: the arbiter's leg (Batch 6, task 1; ruling R10).

The arbiter stays the one rail for speaking first, and its ceiling is
untouched: nothing here sends. While the night's pick is unsaid, her
thread board carries one line for it under the fixed id ``tonight``. After
the rail reports a say as SENT, the pick is recorded as said when the say
named that id; when it did not, the said-check reads the words that went
out, because a say can carry the pick without naming it and "said once" is
a budget code has to keep.

No moment prompt file changes: the line rides the ``{threads_block}`` slot
the two live rungs already have.
"""
from __future__ import annotations

import json
import re
import time
import types
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

import ai.arbiter as arb
import ai.present_moment as pm
import ai.present_moment_gather as gather
from ai import stake_ledger, tonight

ROOT = Path(__file__).resolve().parents[2]
PICK_TEXT = "I keep thinking about the concert on Friday."
PICK_LINE = (
    "- [tonight] [one thing on your mind | when-the-moment-fits | bring it up only if "
    "you have not already brought it up with them; never repeat it once it's said] "
    + PICK_TEXT
)


class _Comp:
    def __init__(self, arbiter=True):
        self.arbiter = arbiter
        self.present_moment = True


def _thread(i, summary="s"):
    return types.SimpleNamespace(
        id=f"{i:0>8x}" + "0" * 24, kind=types.SimpleNamespace(value="curiosity"),
        urgency="low", created_at=time.time() - 3 * 86400,
        topic_summary=summary, why_it_matters="w",
    )


@pytest.fixture
def folder(tmp_path, monkeypatch):
    monkeypatch.setattr(tonight, "TONIGHT_DIR", tmp_path / "tonight")
    return tmp_path / "tonight"


@pytest.fixture
def ledger(monkeypatch):
    """The two ledger stamps, recorded instead of run."""
    seen = types.SimpleNamespace(voiced=[], surfaced=[])

    async def mark_stake_said(stake_id, *, brain_client=None, now=None):
        seen.voiced.append((stake_id, now))
        return True

    async def record_surfaced(stake_id, *, brain_client=None):
        seen.surfaced.append(stake_id)

    monkeypatch.setattr(stake_ledger, "mark_stake_said", mark_stake_said)
    monkeypatch.setattr(stake_ledger, "record_surfaced", record_surfaced)
    return seen


def _on(monkeypatch):
    monkeypatch.setattr(tonight, "tonight_enabled", lambda: True)


async def _write_pick(*, null: bool = False) -> str:
    reply = '{"pick": null}' if null else json.dumps({"pick": {
        "source": "stake", "source_id": "42", "text": PICK_TEXT, "why": "PRIVATE_REASON"}})

    async def llm(prompt):
        return reply

    now = time.time()
    await tonight.pick_tonight(1, llm_call=llm, now=now, stakes=[{"id": 42}],
                               journal_pages=[], daydreams=[], recent_conversation="")
    return tonight.day_for(now)


def _said_llm(monkeypatch, reply):
    calls: list = []

    async def fake(prompt):
        calls.append(prompt)
        if isinstance(reply, Exception):
            raise reply
        return reply

    monkeypatch.setattr(tonight, "tonight_said_llm_call", fake)
    return calls


# ── the board line (ai/present_moment_gather.py) ────────────────────────

async def _gathered(monkeypatch, rows, *, arbiter=True):
    monkeypatch.setattr(gather, "_companion", lambda: _Comp(arbiter))

    async def _actives(*, owner_user_id):
        return rows

    monkeypatch.setattr(gather, "_list_active_threads", _actives)
    return await gather.gather_moment_inputs(owner_user_id=1)


@pytest.mark.asyncio
async def test_the_unsaid_pick_is_one_line_at_the_top_of_the_board(folder, monkeypatch):
    _on(monkeypatch)
    await _write_pick()
    out = await _gathered(monkeypatch, [_thread(1, "thread one"), _thread(2, "thread two")])
    lines = out.threads_block.splitlines()
    assert lines[0] == PICK_LINE
    assert len(lines) == 3 and "thread one" in out.threads_block
    assert out.threads_block.count("[tonight]") == 1
    assert out.sense_health.get("tonight") == "ok"
    assert "PRIVATE_REASON" not in out.threads_block


@pytest.mark.asyncio
async def test_with_no_threads_the_board_is_the_pick_alone(folder, monkeypatch):
    _on(monkeypatch)
    await _write_pick()
    out = await _gathered(monkeypatch, [])
    assert out.threads_block == PICK_LINE


def test_the_board_line_carries_the_bands_own_law():
    """Fix wave (R74 M3): the reply's said-check is strict on purpose, so a
    false no leaves the pick on the board. The line does not say "not said
    yet" — it carries the band's own law, in plain words, and whether it
    was already brought up stays the model's to read."""
    line = tonight.render_board_line(tonight.Pick(
        day="2026-08-12", source="stake", source_id="42", text=PICK_TEXT, why="PRIVATE_REASON"))
    assert "not said yet" not in line
    assert "bring it up only if you have not already brought it up with them" in line
    assert "never repeat it once it's said" in line
    assert "never repeat it once it's said" in tonight.BAND_LAW          # the band's own words
    assert "PRIVATE_REASON" not in line
    assert not re.search(r"\b(he|him|his|himself)\b", line, re.IGNORECASE)


@pytest.mark.asyncio
async def test_a_dark_organ_leaves_the_board_and_the_health_byte_identical(folder, monkeypatch):
    await _write_pick()
    rows = [_thread(1, "thread one")]
    dark = await _gathered(monkeypatch, rows)
    assert "tonight" not in dark.threads_block
    assert "tonight" not in dark.sense_health
    # the same board a build without the organ renders
    assert dark.threads_block == await gather._threads(1)


@pytest.mark.asyncio
async def test_a_said_pick_a_null_night_and_no_file_leave_the_board_alone(folder, monkeypatch):
    _on(monkeypatch)
    rows = [_thread(1, "thread one")]
    plain = await gather_plain(monkeypatch, rows)
    assert (await _gathered(monkeypatch, rows)).threads_block == plain      # no file
    day = await _write_pick()
    tonight.mark_said(day, at=time.time())
    assert (await _gathered(monkeypatch, rows)).threads_block == plain      # said
    (folder / f"{day}.json").unlink()
    await _write_pick(null=True)
    assert (await _gathered(monkeypatch, rows)).threads_block == plain      # null night
    assert (await _gathered(monkeypatch, [])).threads_block == "Nothing on your thread board."


async def gather_plain(monkeypatch, rows) -> str:
    async def _actives(*, owner_user_id):
        return rows

    monkeypatch.setattr(gather, "_list_active_threads", _actives)
    return await gather._threads(1)


@pytest.mark.asyncio
async def test_the_pick_never_reaches_a_moment_with_no_rail(folder, monkeypatch):
    """With the arbiter off there is no thread board and no say, so the
    pick has no place in the moment at all."""
    _on(monkeypatch)
    await _write_pick()
    out = await _gathered(monkeypatch, [_thread(1)], arbiter=False)
    assert out.threads_block == ""
    assert "tonight" not in out.sense_health
    assert PICK_TEXT not in out.bases_block and PICK_TEXT not in out.senses_block


@pytest.mark.asyncio
async def test_the_pick_is_never_one_of_her_reference_bases(folder, monkeypatch):
    """The ruling behind tests/ai/test_disclosure_board.py: a once-only
    offer never rides bases_block, where it would flow into the moment's
    prose and the reply band without a stamp."""
    _on(monkeypatch)
    await _write_pick()
    out = await _gathered(monkeypatch, [_thread(1)])
    assert PICK_TEXT not in out.bases_block
    assert PICK_TEXT not in out.senses_block
    assert PICK_TEXT not in out.monologue_block and PICK_TEXT not in out.reflection_block


@pytest.mark.asyncio
async def test_a_broken_pick_file_costs_the_line_and_never_the_board(folder, monkeypatch):
    _on(monkeypatch)
    await _write_pick()

    def boom(*a, **kw):
        raise RuntimeError("disk")

    monkeypatch.setattr(tonight, "load_current", boom)
    out = await _gathered(monkeypatch, [_thread(1, "thread one")])
    assert "thread one" in out.threads_block and "[tonight]" not in out.threads_block
    assert out.sense_health.get("tonight") not in (None, "ok")


def test_no_moment_prompt_file_carries_the_pick():
    for path in sorted((ROOT / "prompts").glob("present_moment_appraisal*.txt")):
        text = path.read_text(encoding="utf-8")
        assert "tonight" not in text.lower(), path.name


# ── after the rail says SENT (ai/present_moment.py) ─────────────────────

def _impulse(**kw):
    base = {"kind": "say", "why": "w", "framing": "The concert on Friday has been on my mind all day."}
    base.update(kw)
    return base


@pytest.mark.asyncio
async def test_a_sent_say_that_names_the_pick_marks_it_said(folder, monkeypatch, ledger):
    _on(monkeypatch)
    day = await _write_pick()
    calls = _said_llm(monkeypatch, '{"said": false}')
    await pm._record_tonight_said(_impulse(thread_id="tonight"),
                                  {"disposition": "sent", "at": 1234.5})
    assert tonight.load_pick(day).said_at == 1234.5
    assert ledger.voiced == [(42, 1234.5)] and ledger.surfaced == [42]
    assert calls == []          # the id is the answer; nothing is asked


@pytest.mark.asyncio
@pytest.mark.parametrize("thread_id", [None, "abcd1234"])
async def test_a_sent_say_that_carries_the_pick_without_the_id_is_caught(
        folder, monkeypatch, ledger, thread_id):
    _on(monkeypatch)
    day = await _write_pick()
    calls = _said_llm(monkeypatch, '{"said": true}')
    impulse = _impulse(**({"thread_id": thread_id} if thread_id else {}))
    await pm._record_tonight_said(impulse, {"disposition": "sent", "at": 1234.5})
    assert len(calls) == 1
    assert PICK_TEXT in calls[0] and impulse["framing"] in calls[0]
    assert tonight.load_pick(day).said_at == 1234.5
    assert ledger.voiced == [(42, 1234.5)]


@pytest.mark.asyncio
async def test_a_sent_say_about_something_else_leaves_the_pick_unsaid(folder, monkeypatch, ledger):
    _on(monkeypatch)
    day = await _write_pick()
    calls = _said_llm(monkeypatch, '{"said": false}')
    await pm._record_tonight_said(_impulse(framing="Did you eat today?"),
                                  {"disposition": "sent", "at": 1234.5})
    assert len(calls) == 1
    assert tonight.load_pick(day).said_at is None
    assert ledger.voiced == [] and ledger.surfaced == []


@pytest.mark.asyncio
@pytest.mark.parametrize("rail", [
    {}, {"disposition": "ceiling_blocked", "ceiling": 3}, {"disposition": "stale_dropped"},
    {"disposition": "held_no_channel"}, {"disposition": "error_closed"},
    {"disposition": "do_recorded_only"}, {"disposition": "held_care_first_look"},
])
async def test_a_say_that_did_not_go_out_records_nothing(folder, monkeypatch, ledger, rail):
    """Recorded on send, never on intent: a say the ceiling or the
    freshness judge stopped leaves the pick unsaid and the band in place."""
    _on(monkeypatch)
    day = await _write_pick()
    calls = _said_llm(monkeypatch, '{"said": true}')
    await pm._record_tonight_said(_impulse(thread_id="tonight"), rail)
    assert calls == []
    assert tonight.load_pick(day).said_at is None and ledger.voiced == []


@pytest.mark.asyncio
async def test_the_flag_off_records_nothing_and_asks_nothing(folder, monkeypatch, ledger):
    day = await _write_pick()
    calls = _said_llm(monkeypatch, '{"said": true}')
    await pm._record_tonight_said(_impulse(thread_id="tonight"),
                                  {"disposition": "sent", "at": 1234.5})
    assert calls == []
    assert tonight.load_pick(day).said_at is None and ledger.voiced == []


@pytest.mark.asyncio
async def test_no_pick_or_a_said_pick_asks_nothing(folder, monkeypatch, ledger):
    _on(monkeypatch)
    calls = _said_llm(monkeypatch, '{"said": true}')
    await pm._record_tonight_said(_impulse(), {"disposition": "sent", "at": 1.0})
    day = await _write_pick()
    tonight.mark_said(day, at=99.0)
    await pm._record_tonight_said(_impulse(thread_id="tonight"), {"disposition": "sent", "at": 2.0})
    assert calls == []
    assert tonight.load_pick(day).said_at == 99.0 and ledger.voiced == []


@pytest.mark.asyncio
async def test_the_record_never_raises(folder, monkeypatch, ledger):
    _on(monkeypatch)
    day = await _write_pick()
    _said_llm(monkeypatch, RuntimeError("provider down"))
    await pm._record_tonight_said(_impulse(), {"disposition": "sent", "at": 1.0})
    assert tonight.load_pick(day).said_at is None

    def boom(*a, **kw):
        raise RuntimeError("disk")

    monkeypatch.setattr(tonight, "load_current", boom)
    await pm._record_tonight_said(_impulse(thread_id="tonight"), {"disposition": "sent", "at": 1.0})


async def _run_cycle(monkeypatch, *, rail, impulse_json):
    engine = pm.PresentMomentEngine()
    released = []

    async def _fake_release(*, impulse, owner_user_id, arbiter_live):
        released.append(dict(impulse))
        return dict(rail)

    monkeypatch.setattr("ai.arbiter.consider_release", _fake_release)
    monkeypatch.setattr(engine, "_affect_enabled", lambda: True)
    monkeypatch.setattr(engine, "_arbiter_enabled", lambda: True)

    async def _fake_gather(*, owner_user_id):
        return gather.GatherInputs(threads_block=PICK_LINE)

    monkeypatch.setattr(gather, "gather_moment_inputs", _fake_gather)
    prompts = []

    async def _fake_llm(messages, **kw):
        prompts.append(messages[0]["content"])
        return ('{"moment": "m", "impulse": ' + impulse_json + ', "undercurrent": "steady"}')

    class _L:
        chat_completion = staticmethod(_fake_llm)

    monkeypatch.setattr("ai.llm_client.llm", _L())
    written = {}

    def _fake_write(*, text, meta):
        written.update({"text": text, "meta": meta})
        return "x.jsonl"

    monkeypatch.setattr("ai.present_moment_store.write_moment", _fake_write)
    out = await engine.run_cycle_once()
    return out, written, released, prompts


@pytest.mark.asyncio
async def test_the_cycle_records_the_pick_after_a_sent_say(folder, monkeypatch, ledger):
    """The wiring, through the real cycle: the board line reaches the
    prompt, the say names the id, the rail reports sent, the pick is said."""
    _on(monkeypatch)
    day = await _write_pick()
    calls = _said_llm(monkeypatch, '{"said": false}')
    out, written, released, prompts = await _run_cycle(
        monkeypatch, rail={"disposition": "sent", "channel_id": 5, "at": 4321.0},
        impulse_json=('{"kind": "say", "why": "w", "framing": "the concert on Friday", '
                      '"sensitivity": "routine", "thread_id": "tonight"}'))
    assert PICK_LINE in prompts[0]
    assert released[0]["thread_id"] == "tonight"
    assert written["meta"]["impulse"]["rail"]["disposition"] == "sent"
    assert tonight.load_pick(day).said_at == 4321.0
    assert ledger.voiced == [(42, 4321.0)] and calls == []


@pytest.mark.asyncio
async def test_the_cycle_records_nothing_when_the_rail_blocks(folder, monkeypatch, ledger):
    _on(monkeypatch)
    day = await _write_pick()
    calls = _said_llm(monkeypatch, '{"said": true}')
    out, written, released, _ = await _run_cycle(
        monkeypatch, rail={"disposition": "ceiling_blocked", "ceiling": 3},
        impulse_json=('{"kind": "say", "why": "w", "framing": "the concert on Friday", '
                      '"sensitivity": "routine", "thread_id": "tonight"}'))
    assert written["meta"]["impulse"]["rail"]["disposition"] == "ceiling_blocked"
    assert tonight.load_pick(day).said_at is None and ledger.voiced == [] and calls == []


@pytest.mark.asyncio
async def test_the_moments_own_record_is_the_same_with_the_organ_on(folder, monkeypatch, ledger):
    """The pick adds no key to the moment's meta: what is written for a
    sent say is byte-for-byte what a build without the organ writes."""
    rail = {"disposition": "sent", "channel_id": 5, "at": 4321.0}
    impulse_json = ('{"kind": "say", "why": "w", "framing": "the concert on Friday", '
                    '"sensitivity": "routine", "thread_id": "tonight"}')
    _, dark, _, _ = await _run_cycle(monkeypatch, rail=rail, impulse_json=impulse_json)
    _on(monkeypatch)
    await _write_pick()
    _said_llm(monkeypatch, '{"said": false}')
    _, lit, _, _ = await _run_cycle(monkeypatch, rail=rail, impulse_json=impulse_json)
    assert dark["meta"]["impulse"] == lit["meta"]["impulse"]


# ── the rail itself (ai/arbiter.py): the reserved id is not a lost thread ──

async def _release(impulse, tmp_path, *, freshness=True):
    calls = {}

    async def fake_outbound(**kwargs):
        calls["outbound"] = kwargs
        return True

    with patch.object(arb, "arbiter_enabled", return_value=True), \
         patch.object(arb, "_pick_surface", new=AsyncMock(return_value=("discord", "42"))), \
         patch.object(arb, "_run_phase", return_value="idle"), \
         patch.object(arb, "_is_working", return_value=False), \
         patch.object(arb, "_last_user_message_at", new=AsyncMock(return_value=0.0)), \
         patch.object(arb, "_list_active", new=AsyncMock(return_value=[])), \
         patch.object(arb, "_record_outbound", new=fake_outbound), \
         patch.object(arb, "_freshness_check", new=AsyncMock(return_value=(freshness, "fine"))), \
         patch.object(arb, "ARBITER_DATA_DIR", tmp_path / "arbiter"):
        verdict = await arb.consider_release(impulse=impulse, owner_user_id=1,
                                             arbiter_live=True)
    return verdict, calls


def _sent_lines(tmp_path):
    files = list((tmp_path / "arbiter").glob("*.jsonl"))
    if not files:
        return []
    return [json.loads(l) for l in files[0].read_text().splitlines()
            if l.strip() and json.loads(l).get("disposition") == "sent"]


@pytest.mark.asyncio
async def test_the_reserved_id_is_not_logged_as_an_unmatched_thread(folder, tmp_path, monkeypatch):
    _on(monkeypatch)
    verdict, calls = await _release(_impulse(thread_id="tonight"), tmp_path)
    assert verdict["disposition"] == "sent"
    assert "thread_unmatched" not in verdict
    # the words go out as she wrote them, and the ledger keeps the id
    assert calls["outbound"]["text"] == "The concert on Friday has been on my mind all day."
    assert "thread_id" not in calls["outbound"]["extra"]
    assert _sent_lines(tmp_path)[0]["thread_id"] == "tonight"


@pytest.mark.asyncio
async def test_any_other_unknown_id_is_still_unmatched(folder, tmp_path, monkeypatch):
    _on(monkeypatch)
    verdict, _ = await _release(_impulse(thread_id="abcd1234"), tmp_path)
    assert verdict["disposition"] == "sent" and verdict["thread_unmatched"] is True


@pytest.mark.asyncio
async def test_with_the_organ_off_the_rail_is_as_it_was(folder, tmp_path):
    verdict, _ = await _release(_impulse(thread_id="tonight"), tmp_path)
    assert verdict["disposition"] == "sent" and verdict["thread_unmatched"] is True


@pytest.mark.asyncio
async def test_the_rail_stamps_a_say_that_names_the_pick_where_it_records_the_send(
        folder, tmp_path, monkeypatch, ledger):
    """Second read F10 (2026-10-04): a sent say that names ``tonight`` is
    stamped said at the ledger-append point — the same moment the send is
    recorded, with no model call (no await at all) between reading the pick
    and stamping it — so the reply leg's said-check can no longer read it as
    unsaid in the seconds after. (This test used to pin the opposite: the
    moment stamped it after the rail returned, a few awaits later.) The
    moment's own record afterwards finds it said and stamps nothing twice."""
    _on(monkeypatch)
    day = await _write_pick()
    verdict, _ = await _release(_impulse(thread_id="tonight"), tmp_path)

    assert verdict["disposition"] == "sent"
    sent_at = _sent_lines(tmp_path)[0]["at"]
    assert tonight.load_pick(day).said_at == sent_at == verdict["at"]
    assert ledger.voiced == [(42, sent_at)] and ledger.surfaced == [42]

    calls = _said_llm(monkeypatch, '{"said": true}')
    await pm._record_tonight_said(_impulse(thread_id="tonight"), verdict)
    assert calls == [] and ledger.voiced == [(42, sent_at)]


@pytest.mark.asyncio
async def test_the_rail_stamps_nothing_for_a_say_that_does_not_name_the_pick(
        folder, tmp_path, monkeypatch, ledger):
    """Unnamed, the pick's record is the moment's said-check after the rail
    (a model's judgment, and its window is documented there); a stake id or
    the organ off stamps nothing here either."""
    _on(monkeypatch)
    day = await _write_pick()
    for impulse in (_impulse(), _impulse(thread_id="abcd1234")):
        verdict, _ = await _release(impulse, tmp_path)
        assert verdict["disposition"] == "sent"
    assert tonight.load_pick(day).said_at is None and ledger.voiced == []


@pytest.mark.asyncio
async def test_with_the_organ_off_the_rail_stamps_nothing(folder, tmp_path, monkeypatch, ledger):
    day = await _write_pick()
    verdict, _ = await _release(_impulse(thread_id="tonight"), tmp_path)
    assert verdict["disposition"] == "sent"
    assert tonight.load_pick(day).said_at is None and ledger.voiced == []


@pytest.mark.asyncio
async def test_a_pick_said_already_is_not_stamped_again_by_the_rail(
        folder, tmp_path, monkeypatch, ledger):
    _on(monkeypatch)
    day = await _write_pick()
    tonight.mark_said(day, at=99.0)
    verdict, _ = await _release(_impulse(thread_id="tonight"), tmp_path)
    assert verdict["disposition"] == "sent"
    assert tonight.load_pick(day).said_at == 99.0 and ledger.voiced == []


@pytest.mark.asyncio
async def test_a_failed_stamp_at_the_rail_never_unsends(folder, tmp_path, monkeypatch, ledger):
    _on(monkeypatch)
    await _write_pick()

    def boom(*a, **kw):
        raise RuntimeError("disk")

    monkeypatch.setattr(tonight, "load_current", boom)
    verdict, calls = await _release(_impulse(thread_id="tonight"), tmp_path)
    assert verdict["disposition"] == "sent" and calls["outbound"]
    assert _sent_lines(tmp_path)


@pytest.mark.asyncio
async def test_the_pick_spends_the_ceiling_like_any_other_say(folder, tmp_path, monkeypatch):
    _on(monkeypatch)
    real_knob = arb._knob
    monkeypatch.setattr(arb, "_knob",
                        lambda name, default=None: 1 if name == "arbiter_daily_send_ceiling"
                        else real_knob(name, default))
    first, _ = await _release(_impulse(thread_id="tonight"), tmp_path)
    second, _ = await _release(_impulse(thread_id="tonight"), tmp_path)
    assert first["disposition"] == "sent"
    assert second == {"disposition": "ceiling_blocked", "ceiling": 1}
