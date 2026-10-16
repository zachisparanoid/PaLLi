"""Views form at night, from the whole day (Batch 6, task 2; item 3.3).

A view used to be judged seconds after each message, from a few lines,
where a stance that repeats across a day cannot be seen: not one view ever
formed. FORMING moved to a nightly pass over the day's whole conversation.
The model says whether a view formed; code owns the budgets and the gates:
at most one view a night, once per night, and the ledger's own evidence
gate and caps.

What stays in the turn: revising a view (with its reason), muting one and
bringing one back. An ``open`` of a view sent from a turn is dropped before
the ledger, whatever the model emits.

Every test runs against a tmp ledger and a tmp folder: the suite's autouse
pins keep the organ off and its folder redirected, and a body that wants
the organ on patches the two gates itself.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from datetime import datetime
from pathlib import Path

import pytest
import pytest_asyncio

from ai import stake_appraiser, stake_ledger
from core.database import PalliBrain

ROOT = Path(__file__).resolve().parents[2]

# The night of Tuesday 2026-09-29: the pass runs a little after 04:00 and
# looks back over Monday the 28th.
DAY = "2026-09-28"
NOW = datetime(2026, 9, 29, 4, 20).timestamp()

# Three exchanges, one stance said in each.
CONVERSATION = "\n".join([
    "09:12  them: I forgot the third step again, I was sure I knew it",
    "09:13  me: write the steps down, a list beats memory for a job you do twice a year",
    "13:40  them: should I just wing the backup tonight",
    "13:41  me: no, use the list. a list beats memory when the job is rare",
    "20:05  them: did it from the list, nothing missed",
    "20:06  me: that is the third time today the list won. a list beats memory",
])
VIEW = "I think a written list beats memory for any job done only a few times a year."
EVIDENCE = ('2026-09-28 09:13 "a list beats memory for a job you do twice a year"; '
            '13:41 "a list beats memory when the job is rare"; 20:06 "the list won" - '
            "three times in one day I said the same thing, and the third time it was shown")


def _answer(stake=VIEW, evidence=EVIDENCE) -> str:
    return json.dumps({"view": {"stake": stake, "evidence": evidence}})


def _llm(reply: str, seen: list | None = None):
    async def call(prompt: str) -> str:
        if seen is not None:
            seen.append(prompt)
        return reply
    return call


async def _must_not_call(prompt: str) -> str:
    raise AssertionError("the model was asked")


@pytest_asyncio.fixture
async def brain(tmp_path):
    db = PalliBrain(str(tmp_path / "stakes.sqlite3"))
    await db.initialize()
    try:
        yield db
    finally:
        await db.close()


@pytest.fixture
def folder(tmp_path, monkeypatch):
    monkeypatch.setattr(stake_appraiser, "VIEWS_DIR", tmp_path / "views")
    return tmp_path / "views"


@pytest.fixture
def on(monkeypatch):
    monkeypatch.setattr(stake_ledger, "ledger_enabled", lambda: True)
    monkeypatch.setattr(stake_ledger, "opinions_enabled", lambda: True)


def _state(folder: Path) -> dict:
    return json.loads((folder / ".state.json").read_text(encoding="utf-8"))


async def _rows(brain, *, status: str = "open") -> list:
    await stake_ledger.ensure_schema(brain_client=brain)
    conn = await brain.get_connection()
    try:
        rows = await conn.fetch(
            "SELECT * FROM stakes WHERE kind = 'view' AND status = $1 ORDER BY id", status)
    finally:
        await conn.close()
    return [stake_ledger._row_to_stake(r) for r in rows]


async def _open_view(brain, text: str, evidence: str = "an earlier day") -> int:
    out = await stake_ledger.apply_ops(
        [{"op": "open", "kind": "view", "stake": text, "evidence": evidence}],
        source_channel_id="t", brain_client=brain)
    return int(out["opened"][0])


async def _pass(brain, reply=None, *, llm_call=None, day=DAY, now=NOW,
                conversation=CONVERSATION, seen=None):
    return await stake_appraiser.appraise_views(
        day, llm_call=llm_call or _llm(reply, seen), now=now,
        day_conversation=conversation, brain_client=brain)


# ── the brief's tests ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_stance_that_repeats_forms_one_view_and_the_gate_keeps_it(brain, folder, on):
    out = await _pass(brain, _answer())
    views = await _rows(brain)
    assert len(views) == 1
    assert views[0].stake == VIEW
    assert views[0].evidence.startswith('2026-09-28 09:13 "a list beats memory')
    assert views[0].source_channel_id == "night_views"
    assert out["formed"] == 1 and out["opened"] == [views[0].id] and out["day"] == DAY
    assert "refused" not in out and "error" not in out


@pytest.mark.asyncio
async def test_a_day_with_no_view_writes_no_row_and_one_log_line(brain, folder, on, caplog):
    with caplog.at_level(logging.INFO):
        out = await _pass(brain, '{"view": null}')
    assert await _rows(brain) == [] and await _rows(brain, status="dormant") == []
    assert out == {"day": DAY, "formed": 0}
    lines = [r.getMessage() for r in caplog.records if "no view formed today" in r.getMessage()]
    assert len(lines) == 1 and DAY in lines[0]


# ── the evidence gate is the ledger's own (ruling R22) ──────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("evidence", ["", "   ", None])
async def test_a_view_without_evidence_is_refused_by_the_ledger(brain, folder, on, caplog, evidence):
    reply = json.dumps({"view": {"stake": VIEW, "evidence": evidence}})
    with caplog.at_level(logging.INFO):
        out = await _pass(brain, reply)
    assert await _rows(brain) == []
    assert out["formed"] == 0 and out["refused"] == "view without evidence"
    assert "stake_ledger: op rejected reason=view without evidence" in caplog.text
    assert any("no view formed today" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_a_view_with_no_evidence_key_at_all_is_refused_the_same_way(brain, folder, on):
    out = await _pass(brain, json.dumps({"view": {"stake": VIEW}}))
    assert await _rows(brain) == []
    assert out["refused"] == "view without evidence"


# ── at most one a night, in code ────────────────────────────────────────

@pytest.mark.asyncio
async def test_two_views_in_one_answer_form_one(brain, folder, on):
    reply = json.dumps({"view": [
        {"stake": VIEW, "evidence": EVIDENCE},
        {"stake": "I think short notes are read and long ones are not.",
         "evidence": '2026-09-28 11:02 "nobody read the long one"'},
    ]})
    out = await _pass(brain, reply)
    views = await _rows(brain)
    assert [v.stake for v in views] == [VIEW]
    assert out["formed"] == 1


@pytest.mark.asyncio
async def test_a_list_under_the_plural_key_is_capped_the_same_way(brain, folder, on):
    reply = json.dumps({"views": [
        {"stake": VIEW, "evidence": EVIDENCE},
        {"stake": "I think short notes are read and long ones are not.",
         "evidence": '2026-09-28 11:02 "nobody read the long one"'},
    ]})
    out = await _pass(brain, reply)
    assert [v.stake for v in await _rows(brain)] == [VIEW] and out["formed"] == 1


@pytest.mark.asyncio
async def test_the_first_valid_one_is_taken(brain, folder, on):
    """The first entry has no evidence, so the ledger would refuse it: the
    one view of the night is the first the ledger can keep."""
    second = "I think short notes are read and long ones are not."
    reply = json.dumps({"view": [
        {"stake": VIEW, "evidence": ""},
        "not a view at all",
        {"stake": second, "evidence": '2026-09-28 11:02 "nobody read the long one"'},
        {"stake": "a third", "evidence": "e"},
    ]})
    out = await _pass(brain, reply)
    assert [v.stake for v in await _rows(brain)] == [second]
    assert out["formed"] == 1 and "refused" not in out


# ── a view she already has is not formed twice (fix round 1, R66) ───────

# The same words as VIEW, in another case, with other punctuation and spacing.
VIEW_AGAIN = "i think a WRITTEN list beats memory -- for any job done only a few times a year!!"


@pytest.mark.asyncio
async def test_a_view_she_already_has_is_not_formed_twice(brain, folder, on, caplog):
    """The ledger does not know one view from its twin, so the pass keeps
    the exact-match rail the harvest and the fears plant keep: the same
    words, folded for case, punctuation and spacing, are refused before
    the ledger, by name, and the night is recorded as run with nothing
    formed."""
    first = await _open_view(brain, VIEW)
    with caplog.at_level(logging.INFO):
        out = await _pass(brain, _answer(stake=VIEW_AGAIN))
    assert [v.id for v in await _rows(brain)] == [first]
    assert await _rows(brain, status="dormant") == []
    assert out == {"day": DAY, "formed": 0, "refused": "already one of her views"}
    assert _state(folder) == {"last_night": DAY, "formed": 0, "at": NOW}
    refusals = [r.getMessage() for r in caplog.records if "view refused" in r.getMessage()]
    assert len(refusals) == 1 and "already one of her views" in refusals[0]
    assert "stake_ledger: op" not in caplog.text                 # the ledger was never asked
    assert any("no view formed today" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_a_waiting_view_is_not_formed_twice_either(brain, folder, on, monkeypatch):
    monkeypatch.setattr(stake_ledger, "_views_max_active", lambda: 1)
    await _open_view(brain, "I think the first draft is for finding the point.")
    await _open_view(brain, VIEW)                                # files waiting
    out = await _pass(brain, _answer(stake=VIEW_AGAIN))
    assert out["formed"] == 0 and out["refused"] == "already one of her views"
    assert [v.stake for v in await _rows(brain, status="dormant")] == [VIEW]


@pytest.mark.asyncio
async def test_a_view_they_asked_her_to_leave_alone_is_not_formed_again(brain, folder, on):
    muted = await _open_view(brain, VIEW)
    await stake_ledger.apply_ops([{"op": "mute", "id": muted}], brain_client=brain)
    out = await _pass(brain, _answer(stake=VIEW_AGAIN))
    assert out["formed"] == 0 and out["refused"] == "already one of her views"
    assert len(await _rows(brain)) == 1


@pytest.mark.asyncio
async def test_a_different_view_on_the_same_subject_is_formed(brain, folder, on):
    """Exact match only: what two views MEAN is the model's to judge, and
    the prompt asks it to. Code refuses only the same words."""
    await _open_view(brain, VIEW)
    other = "I think a list is only as good as the last time someone checked it."
    out = await _pass(brain, _answer(stake=other))
    assert sorted(v.stake for v in await _rows(brain)) == sorted([VIEW, other])
    assert out["formed"] == 1 and "refused" not in out


@pytest.mark.asyncio
async def test_a_twin_first_in_a_list_gives_way_to_the_next_good_one(brain, folder, on):
    await _open_view(brain, VIEW)
    other = "I think a list is only as good as the last time someone checked it."
    reply = json.dumps({"view": [{"stake": VIEW_AGAIN, "evidence": EVIDENCE},
                                 {"stake": other, "evidence": EVIDENCE}]})
    out = await _pass(brain, reply)
    assert sorted(v.stake for v in await _rows(brain)) == sorted([VIEW, other])
    assert out["formed"] == 1 and "refused" not in out


@pytest.mark.asyncio
async def test_a_twin_with_no_evidence_is_still_the_ledgers_to_refuse(brain, folder, on, caplog):
    """The ledger's own gate keeps its say and its log line."""
    await _open_view(brain, VIEW)
    with caplog.at_level(logging.INFO):
        out = await _pass(brain, _answer(stake=VIEW_AGAIN, evidence=""))
    assert out["refused"] == "view without evidence"
    assert "stake_ledger: op rejected reason=view without evidence" in caplog.text


# ── once per night, and the trace (ruling R24) ──────────────────────────

@pytest.mark.asyncio
async def test_the_pass_leaves_its_trace_formed_or_not(brain, folder, on):
    await _pass(brain, '{"view": null}')
    assert _state(folder) == {"last_night": DAY, "formed": 0, "at": NOW}
    await _pass(brain, _answer(), day="2026-09-29", now=NOW + 86400)
    assert _state(folder) == {"last_night": "2026-09-29", "formed": 1, "at": NOW + 86400}
    assert (folder / ".state.json").read_bytes().count(b"\r") == 0


@pytest.mark.asyncio
async def test_a_second_run_of_the_same_night_opens_nothing(brain, folder, on):
    """A failed reflection re-runs the whole night at the next boot, and the
    ledger does not know one view from its twin."""
    await _pass(brain, _answer())
    before = _state(folder)
    out = await _pass(brain, llm_call=_must_not_call, now=NOW + 3600)
    assert out == {"skipped": "already_ran", "day": DAY}
    assert len(await _rows(brain)) == 1
    assert _state(folder) == before


@pytest.mark.asyncio
async def test_a_quiet_night_is_not_asked_twice_either(brain, folder, on):
    await _pass(brain, '{"view": null}')
    out = await _pass(brain, llm_call=_must_not_call)
    assert out == {"skipped": "already_ran", "day": DAY}


@pytest.mark.asyncio
async def test_the_next_night_runs(brain, folder, on):
    await _pass(brain, '{"view": null}')
    out = await _pass(brain, _answer(), day="2026-09-29", now=NOW + 86400)
    assert out["formed"] == 1


# ── what the model is shown (rulings R21, R22) ──────────────────────────

@pytest.mark.asyncio
async def test_the_prompt_carries_the_day_and_the_views_she_already_has(brain, folder, on):
    await _open_view(brain, "I think a plan written down gets done.", "two earlier weeks")
    seen: list = []
    await _pass(brain, '{"view": null}', seen=seen)
    assert len(seen) == 1
    prompt = seen[0]
    assert CONVERSATION in prompt
    assert "I think a plan written down gets done." in prompt
    assert "two earlier weeks" in prompt
    assert "<<" not in prompt and ">>" not in prompt


@pytest.mark.asyncio
async def test_the_date_is_the_day_looked_back_on_never_now(brain, folder, on):
    seen: list = []
    await _pass(brain, '{"view": null}', seen=seen)
    assert "Monday, 2026-09-28" in seen[0]
    assert "2026-09-29" not in seen[0]


@pytest.mark.asyncio
async def test_a_waiting_view_and_a_muted_one_are_shown_as_such(brain, folder, on, monkeypatch):
    monkeypatch.setattr(stake_ledger, "_views_max_active", lambda: 1)
    first = await _open_view(brain, "I think the first draft is for finding the point.")
    await _open_view(brain, "I think a walk fixes more than a second coffee.")   # files waiting
    await stake_ledger.apply_ops([{"op": "mute", "id": first}], brain_client=brain)
    seen: list = []
    await _pass(brain, '{"view": null}', seen=seen)
    shown = seen[0]
    muted = next(l for l in shown.splitlines() if "first draft" in l)
    waiting = next(l for l in shown.splitlines() if "second coffee" in l)
    assert "they asked you to leave this one alone" in muted
    assert "waiting" in waiting


@pytest.mark.asyncio
async def test_no_views_yet_says_so_in_plain_words(brain, folder, on):
    seen: list = []
    await _pass(brain, '{"view": null}', seen=seen)
    assert "(none yet)" in seen[0]


@pytest.mark.asyncio
@pytest.mark.parametrize("regret", [False, True])
async def test_the_corrections_are_shown_whether_or_not_regret_is_on(
        brain, folder, on, monkeypatch, regret):
    from ai import corrections_store

    monkeypatch.setattr(stake_appraiser, "_regret_enabled", lambda: regret)
    corrections_store.record_correction(
        belief="the list was lost in the move", truth="the list was in the top drawer all along")
    seen: list = []
    await _pass(brain, '{"view": null}', seen=seen)
    assert "the list was in the top drawer all along" in seen[0]
    assert "CORRECTIONS OF RECORD" in seen[0]


@pytest.mark.asyncio
async def test_no_corrections_says_so_in_plain_words(brain, folder, on):
    seen: list = []
    await _pass(brain, '{"view": null}', seen=seen)
    assert "(nothing has been corrected)" in seen[0]


@pytest.mark.asyncio
async def test_a_slot_name_typed_in_the_day_is_left_as_typed(brain, folder, on):
    """The slots are filled in one pass, so nothing a slot's value carries
    is read as a slot."""
    await _open_view(brain, "I think <<DAY_CONVERSATION>> is an odd thing to type.")
    talk = "10:00  them: what does <<STANDING_VIEWS>> mean in your files {and} {{this}}"
    seen: list = []
    await _pass(brain, '{"view": null}', conversation=talk, seen=seen)
    assert talk in seen[0]
    assert "I think <<DAY_CONVERSATION>> is an odd thing to type." in seen[0]


# ── nights with nothing to ask ──────────────────────────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("conversation", ["", "   \n "])
async def test_a_day_with_no_conversation_asks_nothing(brain, folder, on, caplog, conversation):
    with caplog.at_level(logging.INFO):
        out = await _pass(brain, llm_call=_must_not_call, conversation=conversation)
    assert out == {"day": DAY, "formed": 0, "skipped": "no_conversation"}
    assert _state(folder) == {"last_night": DAY, "formed": 0, "at": NOW}
    assert any("no view formed today" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_a_full_board_and_a_full_waiting_list_ask_nothing(brain, folder, on, monkeypatch):
    monkeypatch.setattr(stake_ledger, "_views_max_active", lambda: 1)
    monkeypatch.setattr(stake_ledger, "_views_dormant_cap", lambda: 1)
    await _open_view(brain, "one on the board")
    await _open_view(brain, "one waiting")
    out = await _pass(brain, llm_call=_must_not_call)
    assert out == {"day": DAY, "formed": 0, "skipped": "cap_full"}
    assert _state(folder)["formed"] == 0
    assert len(await _rows(brain)) == 1 and len(await _rows(brain, status="dormant")) == 1


@pytest.mark.asyncio
async def test_a_full_board_files_the_new_view_waiting(brain, folder, on, monkeypatch):
    """The ledger's own caps apply: past the active cap a view is kept,
    waiting, and it still counts as formed."""
    monkeypatch.setattr(stake_ledger, "_views_max_active", lambda: 1)
    await _open_view(brain, "one on the board")
    out = await _pass(brain, _answer())
    waiting = await _rows(brain, status="dormant")
    assert [v.stake for v in waiting] == [VIEW]
    assert out["formed"] == 1 and out["dormant"] == [waiting[0].id]
    assert _state(folder)["formed"] == 1


@pytest.mark.asyncio
async def test_an_unlimited_waiting_list_is_never_full(brain, folder, on, monkeypatch):
    monkeypatch.setattr(stake_ledger, "_views_max_active", lambda: 1)
    monkeypatch.setattr(stake_ledger, "_views_dormant_cap", lambda: 0)
    await _open_view(brain, "one on the board")
    await _open_view(brain, "one waiting")
    out = await _pass(brain, _answer())
    assert out["formed"] == 1


# ── the flags ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("ledger,opinions", [(False, False), (True, False), (False, True)])
async def test_the_pass_is_dark_with_either_flag_off(brain, folder, monkeypatch, ledger, opinions):
    monkeypatch.setattr(stake_ledger, "ledger_enabled", lambda: ledger)
    monkeypatch.setattr(stake_ledger, "opinions_enabled", lambda: opinions)
    out = await _pass(brain, llm_call=_must_not_call)
    assert out == {"skipped": "flag_off"}
    assert not folder.exists()
    assert await _rows(brain) == []


@pytest.mark.asyncio
async def test_the_suite_pins_the_pass_dark(brain, folder):
    assert await _pass(brain, llm_call=_must_not_call) == {"skipped": "flag_off"}


def test_the_suite_never_points_at_her_real_folder():
    folder = Path(stake_appraiser.VIEWS_DIR)
    assert not folder.resolve().is_relative_to((ROOT / "data").resolve())


def test_the_folder_is_kept_out_of_the_repo():
    ignored = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "data/views/" in ignored


# ── answers outside the contract, and failures ──────────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("reply,reason", [
    ("", "unparseable"),
    ("I did form a view today, here it is", "unparseable"),
    ('{"ops": []}', "unparseable"),
    ('{"view": "yes"}', "malformed_view"),
    ('{"view": {"stake": {"a": 1}, "evidence": "e"}}', "malformed_view"),
    ('{"view": {"stake": "   ", "evidence": "e"}}', "malformed_view"),
    ('{"view": {"evidence": "e"}}', "malformed_view"),
    ('{"view": [7, "x"]}', "malformed_view"),
])
async def test_an_answer_outside_the_contract_writes_nothing(brain, folder, on, reply, reason):
    """It must never read like a quiet night: no row, no trace, and the
    reason by name."""
    out = await _pass(brain, reply)
    assert out == {"day": DAY, "error": reason}
    assert not (folder / ".state.json").exists()
    assert await _rows(brain) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("reply", ['{"view": []}', '{"views": []}', '{"views": null}'])
async def test_an_empty_list_is_a_quiet_night(brain, folder, on, reply):
    out = await _pass(brain, reply)
    assert out == {"day": DAY, "formed": 0}
    assert _state(folder)["formed"] == 0


@pytest.mark.asyncio
async def test_her_words_are_never_made_from_a_value_that_is_not_text(brain, folder, on):
    out = await _pass(brain, '{"view": {"stake": "s", "evidence": {"a": 1}}}')
    assert await _rows(brain) == []
    assert out["refused"] == "view without evidence"


@pytest.mark.asyncio
async def test_a_failed_call_never_raises_and_is_never_an_empty_error(brain, folder, on):
    async def boom(prompt):
        raise RuntimeError("the provider is down")

    async def silent(prompt):
        raise asyncio.TimeoutError()

    assert await _pass(brain, llm_call=boom) == {"day": DAY, "error": "the provider is down"}
    out = await _pass(brain, llm_call=silent)
    assert out == {"day": DAY, "error": "TimeoutError"}
    assert not (folder / ".state.json").exists()


@pytest.mark.asyncio
async def test_a_cancel_goes_through(brain, folder, on):
    async def cancelled(prompt):
        raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        await _pass(brain, llm_call=cancelled)


@pytest.mark.asyncio
@pytest.mark.parametrize("day", ["", "yesterday", "2026-9-28", "2026-09-28.json", "../x"])
async def test_a_day_that_is_not_a_date_is_refused(brain, folder, on, day):
    out = await _pass(brain, llm_call=_must_not_call, day=day)
    assert out == {"error": "bad_date"}
    assert not folder.exists()


@pytest.mark.asyncio
async def test_a_trace_that_cannot_be_written_names_itself(brain, folder, on, tmp_path, monkeypatch):
    blocker = tmp_path / "a-file-where-the-folder-should-be"
    blocker.write_text("x", encoding="utf-8")
    monkeypatch.setattr(stake_appraiser, "VIEWS_DIR", blocker)
    out = await _pass(brain, _answer())
    assert out["formed"] == 1
    assert out["error"].startswith("state_unwritable")


def test_the_envelope_clears_the_whole_deep_ladder():
    from ai.llm_openai_compatible import OpenAICompatibleProvider as P

    assert stake_appraiser._NIGHT_VIEWS_TIMEOUT_S >= P.deep_ladder_ceiling_seconds()


# ── the lane (ruling R21) ───────────────────────────────────────────────

def test_the_lane_is_deep_by_its_name():
    from ai.llm_openai_compatible import OpenAICompatibleProvider as P, is_deep_lane

    assert is_deep_lane("night_cycle_views")
    assert P.request_timeout_for_context("night_cycle_views") == P.DEEP_REQUEST_TIMEOUT_SECONDS


@pytest.mark.asyncio
async def test_the_real_call_names_its_lane(monkeypatch):
    import ai.llm_client as llm_client

    calls: list = []

    class _Fake:
        async def chat_completion(self, messages, **kw):
            calls.append((messages, kw))
            return '{"view": null}'

    monkeypatch.setattr(llm_client, "llm", _Fake())
    monkeypatch.setattr(llm_client, "get_task_model", lambda task: f"fake::{task}")
    await stake_appraiser.night_views_llm_call("VIEWS PROMPT")
    (messages, kw), = calls
    assert kw["log_context"] == "night_cycle_views"
    assert kw["model"] == "fake::appraisal"
    assert messages == [{"role": "user", "content": "VIEWS PROMPT"}]


# ── the prompt file (ruling R21) ────────────────────────────────────────

_FAMILY = re.compile(
    r"\b(holding|held|hold|holds|carrying|carried|carry|carries|sitting with|sits with|"
    r"sit with|landed|land|lands|the shape of|circling|circle|genuinely)\b", re.IGNORECASE)


def _prompt_text() -> str:
    return (ROOT / "prompts" / "night_cycle_views.txt").read_text(encoding="utf-8")


def test_the_prompt_has_the_four_slots_each_once():
    text = _prompt_text()
    assert set(re.findall(r"<<([A-Z_]+)>>", text)) == {
        "CURRENT_DATE", "DAY_CONVERSATION", "STANDING_VIEWS", "CORRECTIONS"}
    for slot in ("CURRENT_DATE", "DAY_CONVERSATION", "STANDING_VIEWS", "CORRECTIONS"):
        assert text.count(f"<<{slot}>>") == 1, slot


def test_the_prompt_states_the_contract():
    text = _prompt_text()
    assert '{"view": {"stake": "' in text and '"evidence": "' in text
    assert '{"view": null}' in text
    assert "STRICT JSON" in text


def test_the_prompt_carries_the_laws_the_turn_used_to_carry():
    text = _prompt_text()
    low = text.lower()
    # the scope law, in full
    assert "facts, taste, and approach" in low
    assert "never their personal choices, unless they asked for your opinion of one" in low
    # evidence, in quoted and dated words from the record
    assert "quote" in low and "date" in low and "evidence" in low
    # one exchange never forms a view; look across the day, or at what the day adds
    assert "one exchange never forms a view" in low
    assert "more than once across the day" in low
    assert "already on your record" in low
    # nothing the corrections of record have settled
    assert "corrections of record" in low and "settled" in low
    # a night with no view is honest, and never one made up to prove a self
    assert "a night with no view is an honest answer" in low
    assert "to prove you have a self" in low
    # a position, not a taste
    assert "a position" in low and "not a taste" in low


def test_the_prompt_speaks_to_her_and_names_the_two_speakers():
    text = _prompt_text()
    assert text.startswith("You are PaLLi")
    assert '"them:"' in text and '"me:"' in text


def test_the_prompt_uses_plain_words_and_lf():
    raw = (ROOT / "prompts" / "night_cycle_views.txt").read_bytes()
    assert b"\r" not in raw
    text = raw.decode("utf-8")
    assert _FAMILY.findall(text) == []
    assert not re.search(r"\b(he|him|his|himself)\b", text, re.IGNORECASE)


def test_the_prompt_is_watched_at_boot():
    from ai.prompt_loader import EXPECTED_PROMPTS

    assert "night_cycle_views" in EXPECTED_PROMPTS


# ── what stays in the turn, and what left it (ruling R25) ───────────────

def _appraisal_llm(monkeypatch, response: str) -> list:
    calls: list = []

    class _FakeLLM:
        async def chat_completion(self, **kwargs):
            calls.append(kwargs)
            return response

    import ai.llm_client as llm_client

    monkeypatch.setattr(llm_client, "llm", _FakeLLM())
    monkeypatch.setattr(llm_client, "get_task_model", lambda task: f"fake::{task}")
    return calls


@pytest.mark.asyncio
async def test_a_view_opened_from_a_turn_is_dropped_before_the_ledger(brain, monkeypatch, caplog):
    monkeypatch.setattr(stake_appraiser, "_opinions_enabled", lambda: True)
    _appraisal_llm(monkeypatch, json.dumps({"ops": [
        {"op": "open", "kind": "view", "stake": VIEW, "evidence": EVIDENCE, "horizon": None},
        {"op": "open", "kind": "VIEW ", "stake": "another", "evidence": "e"},
        {"op": "open", "kind": "hope", "stake": "I hope the backup runs clean tonight."},
    ]}))
    with caplog.at_level(logging.INFO):
        out = await stake_appraiser.appraise_exchange(
            user_message="should I wing the backup", palli_reply="use the list",
            brain_client=brain)
    assert await _rows(brain) == [] and await _rows(brain, status="dormant") == []
    kinds = [s.kind for s in await stake_ledger.open_stakes(limit=20, brain_client=brain)]
    assert kinds == ["hope"]
    assert out["applied"] == 1 and out["rejected"] == 0
    assert out["dropped_view_opens"] == 2
    drops = [r.getMessage() for r in caplog.records if "views form at night" in r.getMessage()]
    assert len(drops) == 1


@pytest.mark.asyncio
async def test_a_turn_that_only_tried_to_open_a_view_moves_nothing(brain, monkeypatch):
    _appraisal_llm(monkeypatch, json.dumps({"ops": [
        {"op": "open", "kind": "view", "stake": VIEW, "evidence": EVIDENCE}]}))
    out = await stake_appraiser.appraise_exchange(
        user_message="u", palli_reply="p", brain_client=brain)
    assert out == {"applied": 0, "rejected": 0, "opened": [], "closed": [], "dormant": [],
                   "dropped_view_opens": 1}
    assert await _rows(brain) == []


@pytest.mark.asyncio
async def test_a_turn_with_no_view_in_it_reports_exactly_what_it_did_before(brain, monkeypatch):
    _appraisal_llm(monkeypatch, '{"ops": []}')
    out = await stake_appraiser.appraise_exchange(
        user_message="u", palli_reply="p", brain_client=brain)
    assert out == {"applied": 0, "rejected": 0, "opened": [], "closed": [], "dormant": []}


@pytest.mark.asyncio
async def test_revise_mute_and_bring_back_still_work_in_the_turn(brain, monkeypatch):
    """The owner's "drop it" and "bring it back", and her own concession,
    take effect at once (owner, 2026-09-29)."""
    monkeypatch.setattr(stake_appraiser, "_opinions_enabled", lambda: True)
    vid = await _open_view(brain, "I think a list beats memory.", "the 09-28 talk")

    _appraisal_llm(monkeypatch, json.dumps({"ops": [{"op": "mute", "id": vid}]}))
    out = await stake_appraiser.appraise_exchange(
        user_message="drop it", palli_reply="dropped", brain_client=brain)
    assert out["applied"] == 1
    assert (await _rows(brain))[0].muted_at is not None

    _appraisal_llm(monkeypatch, json.dumps({"ops": [{"op": "unmute", "id": vid}]}))
    out = await stake_appraiser.appraise_exchange(
        user_message="bring it back", palli_reply="gladly", brain_client=brain)
    assert out["applied"] == 1
    assert (await _rows(brain))[0].muted_at is None

    _appraisal_llm(monkeypatch, json.dumps({"ops": [{
        "op": "update", "id": vid, "stake": "I think a list beats memory, for rare jobs only.",
        "evidence": "they showed me the daily job done from memory, clean",
        "reason": "the daily job needs no list"}]}))
    out = await stake_appraiser.appraise_exchange(
        user_message="the daily one I do from memory", palli_reply="fair, rare jobs only",
        brain_client=brain)
    assert out["applied"] == 1 and "dropped_view_opens" not in out
    row = (await _rows(brain))[0]
    assert row.stake == "I think a list beats memory, for rare jobs only."
    assert "the daily job needs no list" in row.evidence


def _turn_prompt(monkeypatch, *, regret: bool, opinions: bool) -> str:
    from ai.prompt_loader import prompt_loader

    monkeypatch.setattr(stake_appraiser, "_regret_enabled", lambda: regret)
    monkeypatch.setattr(stake_appraiser, "_opinions_enabled", lambda: opinions)
    monkeypatch.setattr(stake_appraiser, "_regret_corrections_block", lambda: "")
    return prompt_loader.load_prompt("stake_appraisal").format(
        care_spine="c", user_message="u", palli_reply="p", recent_conversation="r",
        open_stakes="(none yet)", open_count=0, max_open=12,
        current_date="Monday, 2026-09-28",
        **{**stake_appraiser._regret_prompt_kwargs(), **stake_appraiser._view_prompt_kwargs()},
    )


def test_the_turn_is_no_longer_offered_a_view_to_open(monkeypatch):
    on_text = _turn_prompt(monkeypatch, regret=False, opinions=True)
    assert '"kind": "hope|worry|taste|wonder"' in on_text
    assert '"kind": "view"' not in on_text and "|view" not in on_text
    assert "form at night" in on_text
    for verb in ('{"op": "mute", "id": 4}', '{"op": "unmute", "id": 4}',
                 '"reason": "why it moved me"'):
        assert verb in on_text


def test_the_turns_prompt_is_unchanged_with_the_organ_off(monkeypatch):
    """Byte for byte what it was before this batch: the template with its
    three view slots empty and the kinds the regret flag alone decides."""
    template = (ROOT / "prompts" / "stake_appraisal.txt").read_text(encoding="utf-8").strip()
    for regret in (False, True):
        off = _turn_prompt(monkeypatch, regret=regret, opinions=False)
        expected = template.format(
            care_spine="c", user_message="u", palli_reply="p", recent_conversation="r",
            open_stakes="(none yet)", open_count=0, max_open=12,
            current_date="Monday, 2026-09-28",
            regret_corrections_block="",
            regret_kind_block=stake_appraiser._REGRET_KIND_BLOCK if regret else "",
            regret_never_line=stake_appraiser._REGRET_NEVER_LINE if regret else "",
            open_kinds="hope|worry|taste|wonder" + ("|regret" if regret else ""),
            view_kind_block="", view_never_line="", view_ops_hint="",
        )
        assert off == expected


def test_the_turns_prompt_file_was_not_edited():
    """The slots stay where they were; only their code-held values moved."""
    text = (ROOT / "prompts" / "stake_appraisal.txt").read_text(encoding="utf-8")
    assert "{regret_kind_block}{view_kind_block}- NEVER keep:" in text
    assert "{regret_never_line}{view_never_line}\n" in text
    assert "{view_ops_hint}    {{\"op\": \"update\", \"id\": 3," in text
    assert "THE EXCHANGE JUST NOW:" in text


# ── the night's step (ruling R23) ───────────────────────────────────────

def _stub_the_night(monkeypatch, calls):
    """Every step of the night but the views pass, faked the way the
    sibling wiring tests in tests/ai/test_night_cycle.py fake them."""
    import ai.consolidation as cons
    import ai.disclosure_harvest as harvest
    import ai.narrative_self as ns
    import ai.night_cycle as nc
    import ai.passions as passions
    import ai.stake_review as review

    async def fake_resolve():
        return 1

    async def fake_gather(*, owner_id, date):
        return "material"

    async def fake_reflect(**kw):
        calls.append("reflect")
        return {"wrote": kw["date"]}

    async def fake_consolidation(**kw):
        calls.append("consolidation")
        return {"skipped": "flag_off"}

    async def fake_ritual(**kw):
        calls.append("narrative")
        return {"week": "2026-W40", "docs": {}}

    async def fake_review(**kw):
        calls.append("stake_review")
        return {"applied": 0}

    async def fake_passions_step(**kw):
        calls.append("passions")
        return {"journal": {"skipped": "flag_off"}, "interests": {"skipped": "flag_off"}}

    async def fake_harvest(**kw):
        calls.append("disclosure_harvest")
        return {"faded": 0, "planted": None}

    async def fake_load(owner_id, date):
        calls.append(("load", owner_id, date))
        return [(datetime(2026, 9, 28, 9, 12).timestamp(), "discord", "user", "a line of theirs"),
                (datetime(2026, 9, 28, 9, 13).timestamp(), "discord", "assistant", "a line of mine")]

    monkeypatch.setattr(nc, "_resolve_owner_id", fake_resolve)
    monkeypatch.setattr(nc, "_gather_day_material", fake_gather)
    monkeypatch.setattr(nc, "run_night_cycle", fake_reflect)
    monkeypatch.setattr(nc, "_load_day_conversation", fake_load)
    monkeypatch.setattr(cons, "run_nightly_consolidation", fake_consolidation)
    monkeypatch.setattr(ns, "run_weekly_ritual", fake_ritual)
    monkeypatch.setattr(review, "run_nightly_stake_review", fake_review)
    monkeypatch.setattr(passions, "run_passions_step", fake_passions_step)
    monkeypatch.setattr(harvest, "run_nightly_disclosure_harvest", fake_harvest)
    return nc


def test_the_views_pass_runs_right_after_the_stake_review(monkeypatch, on):
    """After the review, so the pass sees the board as the review left it.
    It is handed the day looked back on, the day's conversation as the
    night renders it, the real model call and the wall clock."""
    calls: list = []
    nc = _stub_the_night(monkeypatch, calls)
    before = time.time()

    async def fake_pass(date, **kw):
        calls.append("views")
        assert date == "2026-09-28"
        assert kw["llm_call"] is stake_appraiser.night_views_llm_call
        assert before <= kw["now"] <= time.time()
        assert kw["day_conversation"] == (
            "09:12  them: a line of theirs\n09:13  me: a line of mine")
        return {"day": date, "formed": 0}

    monkeypatch.setattr(stake_appraiser, "appraise_views", fake_pass)
    result = asyncio.run(nc.NightCycleEngine()._run_once("2026-09-28"))
    assert calls == ["reflect", "consolidation", "narrative", "stake_review",
                     ("load", 1, "2026-09-28"), "views", "passions", "disclosure_harvest"]
    assert result["views"] == {"day": "2026-09-28", "formed": 0}


def test_the_step_is_dark_and_reads_nothing_while_either_flag_is_off(monkeypatch):
    """The suite drives _run_once for real in a dozen places: with the
    organ pinned off the step loads no conversation and asks nothing."""
    calls: list = []
    nc = _stub_the_night(monkeypatch, calls)

    async def must_not_run(*a, **kw):
        raise AssertionError("the views pass ran with the organ off")

    monkeypatch.setattr(stake_appraiser, "appraise_views", must_not_run)
    result = asyncio.run(nc.NightCycleEngine()._run_once("2026-09-28"))
    assert result["views"] == {"skipped": "flag_off"}
    assert not any(isinstance(c, tuple) and c[0] == "load" for c in calls)


def test_a_failed_views_pass_never_costs_the_night(monkeypatch, on):
    calls: list = []
    nc = _stub_the_night(monkeypatch, calls)

    async def boom(date, **kw):
        raise RuntimeError("views exploded")

    monkeypatch.setattr(stake_appraiser, "appraise_views", boom)
    result = asyncio.run(nc.NightCycleEngine()._run_once("2026-09-28"))
    assert result["wrote"] == "2026-09-28"
    assert result["stake_review"] == {"applied": 0}
    assert result["views"] == {"error": "views exploded"}
    assert calls[-2:] == ["passions", "disclosure_harvest"]


def test_a_day_that_cannot_be_loaded_is_named_and_never_costs_the_night(monkeypatch, on):
    nc = _stub_the_night(monkeypatch, [])

    async def boom(owner_id, date):
        raise RuntimeError("")

    monkeypatch.setattr(nc, "_load_day_conversation", boom)
    result = asyncio.run(nc.NightCycleEngine()._run_once("2026-09-28"))
    assert result["wrote"] == "2026-09-28"
    assert result["views"] == {"error": "RuntimeError"}


def test_a_cancelled_views_pass_cancels_the_night(monkeypatch, on):
    nc = _stub_the_night(monkeypatch, [])

    async def cancelled(date, **kw):
        raise asyncio.CancelledError()

    monkeypatch.setattr(stake_appraiser, "appraise_views", cancelled)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(nc.NightCycleEngine()._run_once("2026-09-28"))


def test_the_health_line_names_a_broken_pass_and_not_a_quiet_one():
    from ai.night_cycle import _surface_errors

    assert _surface_errors(
        {"wrote": "x", "views": {"day": DAY, "error": "unparseable"}}
    ) == "views: unparseable"
    assert _surface_errors(
        {"wrote": "x", "views": {"day": DAY, "formed": 0, "refused": "view without evidence"}}
    ) == "views refused: view without evidence"
    for healthy in ({"day": DAY, "formed": 0},
                    {"day": DAY, "formed": 1, "opened": [7], "dormant": []},
                    {"day": DAY, "formed": 0, "skipped": "no_conversation"},
                    {"day": DAY, "formed": 0, "skipped": "cap_full"},
                    {"skipped": "flag_off"},
                    {"skipped": "already_ran", "day": DAY}):
        assert _surface_errors({"wrote": "x", "views": healthy}) == "", healthy


def test_the_health_line_leaves_a_twin_unnamed_and_names_the_ledgers_refusal():
    """Fix round 1 (R72): a view she already holds is the rails working —
    nothing new was needed — so the night's health line stays silent. A
    view the ledger refused still names itself."""
    from ai.night_cycle import _surface_errors
    from ai.stake_appraiser import ALREADY_HERS

    assert _surface_errors(
        {"wrote": "x", "views": {"day": DAY, "formed": 0, "refused": ALREADY_HERS}}
    ) == ""
    assert _surface_errors(
        {"wrote": "x", "views": {"day": DAY, "formed": 0, "refused": "ledger_refused"}}
    ) == "views refused: ledger_refused"
