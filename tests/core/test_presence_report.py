"""``palli_ctl presence``: how much of PaLLi's own reaches the owner (the presence
work, step one: measure first; the owner, 2026-10-11: "yes, start 1 and 2 now").

The main session's numbers of 10-06/07 came from one-off queries, and a replay on
10-11 found what each really counted: "1 of 44 stakes voiced" counts a nightly
stamp that exists only since 09-29; "3 of 128 replies carry her own" was a phrase
list, two of the three hits one message sent twice; "self-started 5–9 a day" was
arbiter attempts, not sends. This report recomputes each number from the same
data, read-only, and says beside every number what it counts and what it cannot
tell. Judgements (does a reply carry something of PaLLi's own; whose agenda a
stake is) are not scripted: the phrase count is reported as a floor, by name.

Every test builds its own data dir and database; nothing here reads the install.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from core import presence_report as pr

NOW = datetime(2026, 10, 11, 2, 0, 0).timestamp()
DAY = 86400.0


def _db(path: Path, *, stakes=(), replies=()) -> Path:
    con = sqlite3.connect(path)
    con.executescript(
        "create table stakes (id integer primary key, kind text, stake text, status text, "
        "created_at real, resolved_at real, voiced_at real);"
        "create table conversations (id integer primary key, user_id integer, platform text, "
        "channel_id integer, role text, content text, timestamp real);"
    )
    for i, (kind, resolved, voiced) in enumerate(stakes, 1):
        con.execute("insert into stakes values (?,?,?,?,?,?,?)",
                    (i, kind, f"stake {i}", "open", NOW - 10 * DAY, resolved or None, voiced or None))
    for i, (platform, role, text, ago) in enumerate(replies, 1):
        con.execute("insert into conversations values (?,?,?,?,?,?,?)",
                    (i, 1, platform, 0, role, text, NOW - ago))
    con.commit(); con.close()
    return path


def _jsonl(path: Path, rows) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


@pytest.fixture
def install(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    db = _db(
        data / "palli-local.sqlite3",
        stakes=[("hope", None, None), ("hope", None, NOW - 2 * DAY), ("worry", NOW - 10 * DAY, None),
                ("view", None, None), ("regret", NOW - 11 * DAY, None)],
        replies=[
            ("discord", "assistant", "Sure, the file is on the NAS.", 1.2 * DAY),
            ("discord", "assistant", "I've been wanting to try that recipe myself, honestly.", 1.5 * DAY),
            ("discord", "assistant", " ".join(["word"] * 70), 2 * DAY),
            ("reminder_fire", "assistant", "Time to stretch.", 2 * DAY),       # machinery, never a reply
            ("discord", "user", "I keep thinking about it", 1 * DAY),          # the owner's words, not hers
            ("discord", "assistant", "Old one.", 40 * DAY),                     # outside a 30-day window
            ("discord", "assistant", "Hey, thought of you: the bridge is back up.", 1 * DAY - 3),   # a send
            ("proactive", "assistant", "a proactive row", 1 * DAY),              # self-started platform
        ],
    )
    day = lambda n: (datetime(2026, 10, 11) - timedelta(days=n)).strftime("%Y-%m-%d")
    _jsonl(data / "arbiter" / f"{day(1)}.jsonl",
           [{"at": NOW - DAY, "disposition": "stale_dropped"}, {"at": NOW - DAY, "disposition": "sent"},
            {"at": NOW - DAY, "disposition": "sent_share"}])
    _jsonl(data / "arbiter" / f"{day(2)}.jsonl", [{"at": NOW - 2 * DAY, "disposition": "stale_dropped"}])
    _jsonl(data / "arbiter" / f"{day(45)}.jsonl", [{"at": NOW - 45 * DAY, "disposition": "sent"}])   # old
    _jsonl(data / "workbench" / f"{day(1)}.jsonl",
           [{"written_at": NOW - DAY, "duration_s": 60.0, "shared": "sent"},
            {"written_at": NOW - DAY, "duration_s": 120.0, "shared": ""},
            {"written_at": NOW - DAY, "duration_s": 30.0, "shared": "dropped"},
            {"written_at": NOW - DAY, "duration_s": 90.0, "shared": "pending"},
            {"written_at": NOW - DAY, "duration_s": 45.0, "shared": False}])
    _jsonl(data / "quests" / "quests.jsonl",
           [{"op": "plant", "id": "q1", "source": "him-spoken"}, {"op": "plant", "id": "q2", "source": "her-own"},
            {"op": "progress", "id": "q2"}, {"op": "status", "id": "q2"}, {"op": "plant", "id": "q3", "source": "her-own"}])
    _jsonl(data / "present_moment" / f"{day(1)}.jsonl", [{"text": "x", "written_at": NOW - DAY}] * 5)
    _jsonl(data / "present_moment" / f"{day(2)}.jsonl",
           [{"text": "x", "written_at": NOW - 2 * DAY}] * 7 + [{"text": "old", "written_at": NOW - 60 * DAY}])
    (data / "tonight").mkdir()
    (data / "tonight" / f"{day(1)}.json").write_text(json.dumps({"pick": "p", "said_at": NOW - DAY}))
    (data / "tonight" / f"{day(2)}.json").write_text(json.dumps({"pick": "p", "said_at": None}))
    return data


def test_stakes_count_held_against_voiced_by_kind_and_openness(install):
    s = pr.stakes_report(install / "palli-local.sqlite3")
    assert s["total"] == 5 and s["voiced"] == 1
    assert s["open"] == 3 and s["open_voiced"] == 1
    assert s["by_kind"]["hope"] == {"total": 2, "open": 2, "voiced": 1}
    assert s["by_kind"]["regret"] == {"total": 1, "open": 0, "voiced": 0}


def test_replies_report_excludes_machinery_the_owner_old_rows_and_her_own_sends(install):
    r = pr.replies_report(install / "palli-local.sqlite3", since=NOW - 30 * DAY, data_dir=install)
    assert r["count"] == 3                      # reminder fire, the owner's row, the old row, the proactive row: out
    assert r["self_started_removed"] == 1       # the send 3 s after its arbiter entry, on the chat's own platform
    assert r["phrase_hits"] == 1                # "I've been wanting"
    assert r["median_words"] == 9 and r["under_cap"] == 2 and r["cap"] == 50


def test_without_a_ledger_dir_no_send_is_removed(install):
    r = pr.replies_report(install / "palli-local.sqlite3", since=NOW - 30 * DAY)
    assert r["count"] == 4 and r["self_started_removed"] == 0


def test_phrase_hits_count_distinct_texts_once():
    """Two identical sends are one thing of PaLLi's own, not two (the 10-04 case)."""
    assert pr.phrase_hits(["I've been wanting to say this.", "I've been wanting to say this.", "Sure."]) == 1


def test_self_started_counts_dispositions_not_rows(install):
    a = pr.self_started_report(install, since=NOW - 30 * DAY)
    assert a["days"] == 2
    assert a["sent"] == 1 and a["sent_share"] == 1 and a["stale_dropped"] == 2 and a["stale_check_failed"] == 0
    assert a["attempts"] == 4 and a["drop_rate"] == 0.5


def test_own_time_separates_shared_quiet_and_dropped(install):
    w = pr.own_time_report(install, since=NOW - 30 * DAY)
    assert w == {"sessions": 5, "shared": 1, "pending": 1, "dropped": 1, "quiet": 2, "median_seconds": 60.0}


def test_wants_count_plants_by_source_and_quests_that_moved(install):
    q = pr.wants_report(install)
    assert q["planted"] == 3 and q["by_source"] == {"him-spoken": 1, "her-own": 2}
    assert q["moved_events"] == 2 and q["moved_quests"] == 1


def test_present_moment_cycles_per_day_and_nightly_picks_said(install):
    p = pr.present_moment_report(install, since=NOW - 30 * DAY)
    assert p["days"] == 2 and p["cycles"] == 12 and p["per_day"] == 6.0   # the 60-day-old row is out
    t = pr.nightly_pick_report(install, since=NOW - 30 * DAY)
    assert t == {"nights": 2, "said": 1}


def test_prompt_words_count_whole_words_in_live_prompts_and_prompt_code_separately(tmp_path):
    root = tmp_path / "repo"
    (root / "prompts" / "archive").mkdir(parents=True)
    (root / "prompts" / "a.txt").write_text("I may disagree. When they push back, I re-look. disagree again.")
    (root / "prompts" / "b.txt").write_text("nothing here")
    (root / "prompts" / "archive" / "old.txt").write_text("disagree disagree disagree")     # retired: not counted
    (root / "core" / "agent").mkdir(parents=True)
    (root / "core" / "agent" / "system_prompt.py").write_text('x = "I never manufacture a disagreement; I disagree"')
    (root / "core" / "agent" / "other.py").write_text("# providers disagree on the field name")   # not a prompt module
    c = pr.prompt_words_report(root, words=("disagree", "push back"))
    assert c["prompts"] == {"disagree": 2, "push back": 1}     # whole words: 'disagreement' is not 'disagree'
    assert c["code"] == {"disagree": 1, "push back": 0}
    assert c["pages"] == {"disagree": 0, "push back": 0}       # no pages in this repo


# ── the scan's scope (2026-10-12): the pages, a split sentence, a hyphen ──────

def test_prompt_words_count_palli_s_own_pages_but_not_the_journal_or_the_templates(tmp_path):
    root = tmp_path / "repo"
    (root / "identity" / "core").mkdir(parents=True)
    (root / "identity" / "palli" / "journal").mkdir(parents=True)
    (root / "identity" / "core" / "OPERATING_PRINCIPLES.md").write_text("When they need push-back, I give the honest read.")
    (root / "identity" / "palli" / "VOICE_IDENTITY.md").write_text("I'll push back on something that doesn't fit.")
    (root / "identity" / "palli" / "WHAT_I_CARE_ABOUT.template.md").write_text("push back push back")
    (root / "identity" / "palli" / "journal" / "2026-10-01.md").write_text("I disagree with myself today.")
    c = pr.prompt_words_report(root, words=("push back", "honest read", "disagree"))
    assert c["pages"] == {"push back": 2, "honest read": 1, "disagree": 0}   # "push-back" is "push back"
    assert [p.name for p in pr.identity_pages(root)] == ["OPERATING_PRINCIPLES.md", "VOICE_IDENTITY.md"]


def test_a_sentence_the_code_splits_over_two_literals_still_counts(tmp_path):
    """The live self_possession block: "I say what I " newline "actually think" —
    the 10-11 scan read it as two strings and missed it."""
    root = tmp_path / "repo"
    (root / "core" / "agent").mkdir(parents=True)
    (root / "core" / "agent" / "system_prompt.py").write_text(
        'x = (\n    "I say what I "\n    "actually think, even when agreeing would be easier"\n)\n')
    c = pr.prompt_words_report(root, words=("what I actually think",))
    assert c["code"] == {"what I actually think": 1}


def test_the_judge_s_own_prompt_is_not_a_prompt_word_source(tmp_path):
    root = tmp_path / "repo"
    (root / "prompts").mkdir(parents=True)
    (root / "prompts" / "presence_judge.txt").write_text("disagree disagree disagreement: it says it sees it differently")
    (root / "prompts" / "a.txt").write_text("I disagree.")
    c = pr.prompt_words_report(root, words=("disagree", "see it differently"))
    assert c["prompts"] == {"disagree": 1, "see it differently": 0}


def test_the_real_judge_prompt_exists_and_asks_for_json():
    text = Path(__file__).resolve().parents[2].joinpath("prompts", pr.JUDGE_PROMPT + ".txt").read_text(encoding="utf-8")
    assert '"own"' in text and '"kind"' in text and '"words"' in text
    for kind in pr.JUDGE_KINDS:
        assert kind in text, kind


# ── the judged count (2026-10-12) ────────────────────────────────────────────

def _pairs_db(path: Path, rows) -> Path:
    con = sqlite3.connect(path)
    con.executescript(
        "create table conversations (id integer primary key, user_id integer, platform text, "
        "channel_id integer, role text, content text, timestamp real);"
    )
    for i, (channel, platform, role, text, ago) in enumerate(rows, 1):
        con.execute("insert into conversations values (?,?,?,?,?,?,?)", (i, 1, platform, channel, role, text, NOW - ago))
    con.commit(); con.close()
    return path


def test_reply_pairs_pair_each_distinct_reply_with_the_message_before_it_in_its_channel(tmp_path):
    db = _pairs_db(tmp_path / "db.sqlite3", [
        (1, "discord", "user", "how's the port?", 3 * DAY + 60),
        (1, "discord", "assistant", "Port 22 is open.", 3 * DAY),
        (2, "telegram", "user", "any thoughts on the plan?", 2 * DAY + 30),
        (1, "discord", "assistant", "Same here.", 3 * DAY - 600),         # channel 1, 10 min on: still answers "how's the port?"
        (2, "telegram", "assistant", "I'd wait a week; the data isn't in.", 2 * DAY - 10),
        (2, "telegram", "assistant", "I'd wait a week; the data isn't in.", 1 * DAY),   # the same text twice: one
        (1, "reminder_fire", "assistant", "Time to stretch.", 1 * DAY),  # machinery
        (3, "discord", "user", "old question", 20 * DAY),
        (3, "discord", "assistant", "Late answer.", 1 * DAY),            # the question is days old: no pairing
        (1, "discord", "assistant", "Before the window.", 40 * DAY),
    ])
    pairs = pr.reply_pairs(db, since=NOW - 30 * DAY)
    assert [(p["asked"], p["reply"]) for p in pairs] == [
        ("", "Late answer."),
        ("any thoughts on the plan?", "I'd wait a week; the data isn't in."),
        ("how's the port?", "Same here."),
        ("how's the port?", "Port 22 is open."),
    ]
    assert [p["reply"] for p in pr.reply_pairs(db, since=NOW - 30 * DAY, limit=2)] == [
        "Late answer.", "I'd wait a week; the data isn't in."]


def test_reply_pairs_leave_out_her_own_sends(install):
    pairs = pr.reply_pairs(install / "palli-local.sqlite3", since=NOW - 30 * DAY, data_dir=install)
    assert "Hey, thought of you: the bridge is back up." not in [p["reply"] for p in pairs]
    assert len(pairs) == 3


@pytest.mark.parametrize("raw, expected", [
    ('{"own": true, "kind": "view", "words": "I\'d pick the second"}', {"own": True, "kind": "view", "words": "I'd pick the second"}),
    ('Sure. {"own": false, "kind": "none", "words": ""} done', {"own": False, "kind": "none", "words": ""}),
    ('{"own": true, "kind": "banter", "words": "ha"}', {"own": True, "kind": "other", "words": "ha"}),
    ('{"own": false, "kind": "view", "words": "x"}', {"own": False, "kind": "none", "words": "x"}),
    ("no json here", None),
    ('{"kind": "view"}', None),
    ("", None),
    ('{"own": true, "kind": "view", "words": "I think"} {"note": "ignored"}', {"own": True, "kind": "view", "words": "I think"}),
    ('{"own": false, "kind": "none", "words": ""}\n(the reply has no view in it {or so})', {"own": False, "kind": "none", "words": ""}),
])
def test_parse_judgement_reads_the_answer_and_nothing_else(raw, expected):
    assert pr.parse_judgement(raw) == expected


def test_judged_report_counts_a_yes_only_when_its_words_are_in_the_reply():
    pairs = [
        {"asked": "thoughts?", "reply": "I'd wait a week; the data isn't in.", "at": 4.0},
        {"asked": "thanks", "reply": "Anytime.", "at": 3.0},
        {"asked": "how was it", "reply": "Long day; I've been chewing on the backup plan.", "at": 2.0},
        {"asked": "and?", "reply": "The port is open.", "at": 1.0},
        {"asked": "ok", "reply": "Right.", "at": 0.0},
    ]

    async def judge(asked, reply):
        if reply.startswith("I'd wait"):
            return {"own": True, "kind": "disagreement", "words": "I’d wait a week"}   # curly apostrophe: still found
        if reply.startswith("Long day"):
            return {"own": True, "kind": "own_life", "words": "I have been chewing"}    # a paraphrase: not found
        if reply == "The port is open.":
            raise RuntimeError("provider down")
        if reply == "Right.":
            return None
        return {"own": False, "kind": "none", "words": ""}

    j = pr.judged_report(pairs, judge)
    assert j == {"replies": 5, "own": 1, "own_unquoted": 1, "unjudged": 2,
                 "by_kind": {"disagreement": 1},
                 "examples": [{"kind": "disagreement", "words": "I’d wait a week"}]}


def test_judged_report_takes_a_plain_function_too_and_keeps_few_examples():
    pairs = [{"asked": "", "reply": f"I think the {i} one.", "at": float(i)} for i in range(8)]
    j = pr.judged_report(pairs, lambda asked, reply: {"own": True, "kind": "view", "words": reply[:11]})
    assert j["own"] == 8 and len(j["examples"]) == pr.JUDGE_EXAMPLES


def test_collect_judges_only_when_asked_and_the_report_says_which(install):
    kw = dict(data_dir=install, db_path=install / "palli-local.sqlite3", repo_root=install.parent, days=30, now=NOW)
    plain = pr.collect(**kw)
    assert plain["judged"] is None
    assert "Judged: not run" in "\n".join(pr.lines_for(plain))
    judged = pr.collect(**kw, judge=lambda a, r: {"own": "wanting" in r, "kind": "own_life", "words": "I've been wanting"})
    assert judged["judged"]["replies"] == 3 and judged["judged"]["own"] == 1
    text = "\n".join(pr.lines_for(judged))
    assert "Judged: 1 of 3 distinct replies" in text and "own_life 1" in text and "I've been wanting" in text


def test_write_baseline_takes_the_counts_it_is_given(install, tmp_path):
    counts = pr.collect(data_dir=install, db_path=install / "palli-local.sqlite3", repo_root=install.parent,
                        days=30, now=NOW, judge=lambda a, r: {"own": True, "kind": "view", "words": r[:4]})
    out = pr.write_baseline(tmp_path / "presence", counts=counts)
    assert json.loads(out.read_text())["judged"]["own"] == 3


@pytest.mark.asyncio
async def test_model_judge_sends_the_prompt_on_its_own_lane(monkeypatch):
    import ai.llm_client as llm_mod

    seen = {}

    async def fake(**kw):
        seen.update(kw)
        return '{"own": true, "kind": "view", "words": "I would pick"}'

    monkeypatch.setattr(llm_mod.llm, "chat_completion", fake)
    v = await pr.model_judge("which one?", "I would pick the second.")
    assert v == {"own": True, "kind": "view", "words": "I would pick"}
    assert seen["log_context"] == "presence_judge" and seen["temperature"] == 0.0
    assert "PALLI'S REPLY:\nI would pick the second." in seen["messages"][1]["content"]
    assert "which one?" in seen["messages"][1]["content"]
    seen.clear()
    await pr.model_judge("x" * 5000 + " the end", "Fine.")
    asked = seen["messages"][1]["content"].split("PALLI'S REPLY:", 1)[0]
    assert "the end" in asked and len(asked) < 2100          # the tail of a long message, capped
    assert '"own"' in seen["messages"][0]["content"]           # the real prompt file


@pytest.mark.asyncio
async def test_model_judge_without_its_prompt_judges_nothing(monkeypatch):
    import ai.prompt_loader as pl

    monkeypatch.setattr(pl.prompt_loader, "load_prompt", lambda name: "")
    assert await pr.model_judge("x", "y") is None


def test_report_lines_say_what_each_number_counts(install, monkeypatch):
    lines = pr.report_lines(data_dir=install, db_path=install / "palli-local.sqlite3",
                            repo_root=install.parent, days=30, now=NOW)
    text = "\n".join(lines)
    assert "1 of 5 stakes" in text and "voiced" in text and "all time" in text
    assert "Replies to people: 3" in text and "Phrase hits: 1" in text and "not the measure" in text
    assert "1 self-started sends taken out" in text
    assert "median 9 words" in text and "2 of 3 under 50" in text
    assert "sent 1, shared 1" in text and "drop rate 50%" in text and "stale-check attempts" in text
    assert "pending 1" in text and "a rule against it" in text
    # every section names its source, so the reader knows what the number is
    for source in ("stakes table", "conversations", "arbiter ledger", "workbench", "quests", "present_moment"):
        assert source in text, source
    assert "judge" in text.lower()               # the phrase count is not the measure; it says so


def test_a_fresh_install_with_no_database_or_ledgers_reports_zeros(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    (data / "palli.yaml").write_text("x: 1")
    lines = pr.report_lines(data_dir=data, db_path=data / "palli-local.sqlite3", repo_root=tmp_path, days=30, now=NOW)
    text = "\n".join(lines)
    assert "0 of 0 stakes" in text and "Replies to people: 0" in text and "0 stale-check attempts" in text


def test_a_database_without_the_tables_yet_reports_zeros(tmp_path):
    db = tmp_path / "palli-local.sqlite3"
    sqlite3.connect(db).close()                 # a file, but no tables
    assert pr.stakes_report(db)["total"] == 0
    assert pr.replies_report(db, since=0)["count"] == 0


def test_baseline_is_written_as_json_with_a_date(install, tmp_path):
    out = pr.write_baseline(tmp_path / "presence", data_dir=install, db_path=install / "palli-local.sqlite3",
                            repo_root=install.parent, days=30, now=NOW)
    assert out.name == "2026-10-11.json"
    saved = json.loads(out.read_text())
    assert saved["stakes"]["total"] == 5 and saved["replies"]["count"] == 3 and saved["window_days"] == 30


# ── group openers (the presence slice, 2026-10-14) ──────────────────────────

def _iso(ts: float) -> str:
    from datetime import timezone
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


@pytest.fixture
def rooms(tmp_path):
    data = tmp_path / "data"
    day = lambda n: (datetime(2026, 10, 11) - timedelta(days=n)).strftime("%Y-%m-%d")
    _jsonl(data / "group_openers" / f"{day(1)}.jsonl", [
        {"at": NOW - DAY, "platform": "discord", "channel_id": 11, "disposition": "sent_group",
         "text": "x", "chars": 1},
        {"at": NOW - DAY, "platform": "telegram", "channel_id": -22, "disposition": "no_start", "why": "y"},
    ])
    _jsonl(data / "group_openers" / f"{day(2)}.jsonl", [
        {"at": NOW - 2 * DAY, "platform": "telegram", "channel_id": -22, "disposition": "sent_group",
         "text": "x", "chars": 1},
    ])
    _jsonl(data / "group_openers" / f"{day(45)}.jsonl", [
        {"at": NOW - 45 * DAY, "platform": "discord", "channel_id": 11, "disposition": "sent_group"},
    ])
    # Discord: a friend answered an hour later. Telegram: only PaLLi and a
    # system note after the opener, then a person two days later (too late).
    _jsonl(data / "agent_sessions" / "discord_11.jsonl", [
        {"role": "assistant", "content": "x", "timestamp": _iso(NOW - DAY)},
        {"role": "user", "content": "nice one", "timestamp": _iso(NOW - DAY + 3600),
         "speaker": {"kind": "guest"}},
    ])
    _jsonl(data / "agent_sessions" / "telegram_-22.jsonl", [
        {"role": "assistant", "content": "x", "timestamp": _iso(NOW - 2 * DAY)},
        {"role": "user", "content": "joined", "timestamp": _iso(NOW - 2 * DAY + 60), "speaker": {"kind": "system"}},
        {"role": "user", "content": "", "timestamp": _iso(NOW - 2 * DAY + 90)},
        {"role": "user", "content": "late", "timestamp": _iso(NOW - 2 * DAY + 2 * DAY)},
    ])
    return data


def test_group_openers_count_sends_rooms_answers_and_looks_that_chose_quiet(rooms):
    g = pr.group_openers_report(rooms, since=NOW - 30 * DAY)
    assert g["sent"] == 2 and g["rooms"] == 2
    assert g["answered"] == 1                     # a person within a day, not PaLLi or a system note
    assert g["no_start"] == 1


def test_group_openers_have_their_own_line_in_the_report(rooms):
    text = "\n".join(pr.report_lines(data_dir=rooms, db_path=rooms / "palli-local.sqlite3",
                                     repo_root=rooms.parent, days=30, now=NOW))
    assert "Group openers: 2 sent in 2 rooms; answered within a day 1 of 2" in text
    assert "1 looks chose not to start" in text and "data/group_openers" in text


def test_no_group_openers_yet_reports_zeros(tmp_path):
    g = pr.group_openers_report(tmp_path, since=0)
    assert g == {"sent": 0, "rooms": 0, "answered": 0, "no_start": 0}
