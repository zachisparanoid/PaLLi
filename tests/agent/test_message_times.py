"""Times on every line (step 2 of the release order, 2026-10-08).

The owner: "time on all messages. theirs, hers, all of them. this is to give her
total awareness just like a real person would have over information within
conversations." Every transcript row already records when it was said; only the
text reached the model. Now each user and assistant row's time is rendered in
front of it, on the payload copy only, and an imitated stamp at the start of her
reply is removed before delivery (she copies what she is shown: DEFECT §20)."""
from __future__ import annotations

from datetime import datetime, timezone

from core.agent import message_times as mt
from core.agent.session import (
    AgentMessage, TextBlock, ToolResultBlock, ToolUseBlock, ensure_user_tail,
    fold_interims_for_thinking, strip_working_note_mark,
)

T1 = datetime(2026, 10, 8, 2, 35, tzinfo=timezone.utc)
T2 = datetime(2026, 10, 8, 3, 5, tzinfo=timezone.utc)
NOW = datetime(2026, 10, 9, 12, 0, tzinfo=timezone.utc)


def _local(ts: datetime) -> datetime:
    return ts.astimezone()


def _expected(ts: datetime, *, with_year: bool = False) -> str:
    loc = _local(ts)
    year = f" {loc.year}" if with_year else ""
    return f"[{mt._MONTHS[loc.month - 1]} {loc.day}{year} {loc:%H:%M}]"


def test_the_label_is_month_day_and_time_in_the_servers_zone():
    assert mt.stamp_label(T1, now=NOW) == _expected(T1)
    assert mt.STAMP_RE.fullmatch(mt.stamp_label(T1, now=NOW))


def test_another_year_is_named():
    old = datetime(2025, 3, 3, 15, 0, tzinfo=timezone.utc)
    assert mt.stamp_label(old, now=NOW) == _expected(old, with_year=True)
    assert mt.STAMP_RE.fullmatch(mt.stamp_label(old, now=NOW))


def test_a_naive_time_is_read_as_utc():
    naive = datetime(2026, 10, 8, 2, 35)
    assert mt.stamp_label(naive, now=NOW) == mt.stamp_label(T1, now=NOW)


def _rows() -> list[AgentMessage]:
    return [
        AgentMessage(role="user", content=[TextBlock(text="is the disk ok?")], timestamp=T1),
        AgentMessage(role="assistant", content=[ToolUseBlock(id="t1", name="local_shell", input={})], timestamp=T1),
        AgentMessage(role="tool", content=[ToolResultBlock(tool_use_id="t1", content="12% used")], timestamp=T1),
        AgentMessage(role="assistant", content=[TextBlock(text="It's fine: 12% used.")], timestamp=T2),
    ]


def test_every_user_and_assistant_line_gets_its_own_time():
    rows = _rows()
    out = mt.stamp_times_for_llm(rows, now=NOW)
    assert out[0].content[0].text == f"{_expected(T1)} is the disk ok?"
    assert out[3].content[0].text == f"{_expected(T2)} It's fine: 12% used."
    # a tool call and a tool result are not lines anyone said
    assert out[1] is rows[1] and out[2] is rows[2]


def test_the_pass_is_pure_and_idempotent():
    rows = _rows()
    before = [m.content[0] for m in rows]
    once = mt.stamp_times_for_llm(rows, now=NOW)
    twice = mt.stamp_times_for_llm(once, now=NOW)
    assert [m.content[0] for m in rows] == before          # the session's rows never change
    assert [m.content for m in twice] == [m.content for m in once]
    assert mt.stamp_times_for_llm([], now=NOW) == []


def test_only_the_first_text_block_is_stamped():
    row = AgentMessage(role="assistant", timestamp=T2, content=[
        TextBlock(text="first part"), ToolUseBlock(id="t2", name="x", input={}), TextBlock(text="second part")])
    out = mt.stamp_times_for_llm([row], now=NOW)[0]
    assert out.content[0].text.startswith(_expected(T2)) and out.content[2].text == "second part"


def test_an_imitated_stamp_never_reaches_them():
    label = mt.stamp_label(T1, now=NOW)
    assert strip_working_note_mark(f"{label} Done, it went through.") == "Done, it went through."
    assert strip_working_note_mark("[Oct 8 2025 22:35] hi") == "hi"
    assert strip_working_note_mark(
        f"{label} [system: working note — never delivered; they have not seen this] hi") == "hi"
    # ordinary text is untouched
    assert strip_working_note_mark("[Sam]: hi") == "[Sam]: hi"
    assert strip_working_note_mark("It's 22:35 now.") == "It's 22:35 now."


def test_rows_rebuilt_for_the_payload_keep_their_own_time():
    """ensure_user_tail re-presents his last words and the interim fold rebuilds a
    row: both must keep the original row's time, or her stamp would say 'now'."""
    user = AgentMessage(role="user", content=[TextBlock(text="check it")], timestamp=T1)
    her = AgentMessage(role="assistant", content=[TextBlock(text="on it")], timestamp=T2)
    tail = ensure_user_tail([user, her])
    assert tail[-1].timestamp == T1

    rows = [
        AgentMessage(role="user", content=[TextBlock(text="is it done?")], timestamp=T1),
        AgentMessage(role="assistant", content=[TextBlock(text="one sec")], timestamp=T1),
        AgentMessage(role="assistant", content=[ToolUseBlock(id="t9", name="x", input={})], timestamp=T2),
        AgentMessage(role="tool", content=[ToolResultBlock(tool_use_id="t9", content="ok")], timestamp=T2),
    ]
    folded = fold_interims_for_thinking(rows)
    assert folded[0].role == "user" and folded[0].timestamp == T1


def test_the_setting_ships_on():
    from core.settings_schema import CompanionConfig
    assert CompanionConfig().message_times is True


def test_the_runner_sends_every_line_with_its_time(tmp_path, monkeypatch):
    """Through the real runner: with the setting on, the payload's user and
    assistant lines carry their times; with it off, nothing changes."""
    import core.agent.llm_client as llm_mod
    import core.agent.session as sess_mod
    from tests.agent.test_promise_gate import KEPT, _judge_saying, _load_runner, _run, _scripted_llm
    from core.agent import promise_gate as pg

    quiet = pg.PromiseVerdict(empty_promise=False)
    mod = _load_runner()

    monkeypatch.setattr(mt, "message_times_enabled", lambda: True)
    on_call, on_calls = _scripted_llm(llm_mod, sess_mod, [KEPT])
    _run(mod, tmp_path / "on", on_call, monkeypatch, judge=_judge_saying(quiet))
    sent = [m for m in on_calls[0]["messages"] if m.role in ("user", "assistant")]
    assert sent and all(mt.STAMP_RE.match(m.content[0].text) for m in sent)

    monkeypatch.setattr(mt, "message_times_enabled", lambda: False)
    off_call, off_calls = _scripted_llm(llm_mod, sess_mod, [KEPT])
    _run(mod, tmp_path / "off", off_call, monkeypatch, judge=_judge_saying(quiet))
    sent_off = [m for m in off_calls[0]["messages"] if m.role in ("user", "assistant")]
    assert sent_off and not any(mt.STAMP_RE.match(m.content[0].text) for m in sent_off)
