"""Her Own Work (roadmap 16-17) — engine, budgets, eligibility, wiring.
Spec: docs/superpowers/specs/2026-07-28-her-own-work-design.md
Law under test throughout: flag off ⇒ byte-identical absence."""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest


def test_own_work_knobs_exist_with_spec_defaults():
    from core.settings_schema import CompanionConfig

    c = CompanionConfig()
    assert c.own_work is False
    assert c.own_work_daily_sessions == 3
    assert c.own_work_session_rounds == 8
    assert c.own_work_session_minutes == 20
    assert c.own_work_active_projects_max == 2
    assert c.own_work_min_free_disk_gb == 5


def test_workspace_ready_checks(tmp_path):
    from ai.workbench import _workspace_ready

    assert _workspace_ready(root=tmp_path) is False       # nothing there
    (tmp_path / "WORKBENCH.md").write_text("# Workbench\n")
    (tmp_path / "projects").mkdir()
    assert _workspace_ready(root=tmp_path) is True


def test_session_prompt_renders_with_all_slots(tmp_path):
    from ai.workbench import _render_session_prompt

    (tmp_path / "WORKBENCH.md").write_text("## active\n- the essay")
    (tmp_path / "SUGGESTIONS.md").write_text("# Suggestions\n- maybe birds?")
    text = _render_session_prompt(root=tmp_path, sessions_left=2, active_max=2)
    assert "the essay" in text and "maybe birds?" in text
    assert "SHARE:" in text and "permission to want" in text


@pytest.mark.parametrize("mode,phrase", [
    ("message", "reaches them as a message from you"),
    ("thread", "may bring it up"),
    ("room", "stays in your room"),
])
def test_session_prompt_tells_her_where_a_share_goes(tmp_path, monkeypatch, mode, phrase):
    """She writes the SHARE line AS what he will read (message mode sends it
    verbatim), so the prompt says plainly where it goes under the knob."""
    import ai.workbench as wb
    from ai.workbench import _render_session_prompt

    (tmp_path / "WORKBENCH.md").write_text("# Workbench\n")
    (tmp_path / "SUGGESTIONS.md").write_text("# Suggestions\n")
    monkeypatch.setattr(wb, "companion_settings", lambda: _comp(own_work_share_delivery=mode))
    text = _render_session_prompt(root=tmp_path, sessions_left=2, active_max=2)
    assert phrase in text
    assert "Nothing you write here is sent to anyone" not in text or mode == "room"


def _comp(**kw):
    base = dict(own_work=True, own_work_daily_sessions=3,
                own_work_session_rounds=8, own_work_session_minutes=20,
                own_work_active_projects_max=2, own_work_min_free_disk_gb=0)
    base.update(kw)
    return SimpleNamespace(**base)


def _ready_workspace(tmp_path):
    (tmp_path / "WORKBENCH.md").write_text("# Workbench\n")
    (tmp_path / "SUGGESTIONS.md").write_text("# Suggestions\n")
    (tmp_path / "projects").mkdir()
    return tmp_path


@pytest.mark.asyncio
async def test_session_synthesizes_workbench_turn_and_discards_result(monkeypatch, tmp_path):
    """The §4b spine: Turn(surface='workbench') → dispatch_turn; the caller
    DISCARDS the result (caller-owned delivery = null delivery); the session
    records to the store; sense H fires; git commit runs via her user."""
    import ai.workbench as wb
    import ai.workbench_store as ws

    monkeypatch.setattr(wb, "companion_settings", lambda: _comp())
    monkeypatch.setattr(wb, "WORKSPACE_ROOT", _ready_workspace(tmp_path))
    store_root = tmp_path / "store"
    monkeypatch.setattr(ws, "WORKBENCH_DIR", store_root)
    # 2026-08-18: shares are MESSAGES by default now (own_work_share_delivery);
    # this test pins the legacy "thread" lane — see test_share_* below for the
    # delivery contract. Her monologue dir is redirected: a session end now
    # writes her a memory (test_session_end_becomes_a_searchable_memory).
    monkeypatch.setattr(wb, "companion_settings", lambda: _comp(own_work_share_delivery="thread"))
    monkeypatch.setattr(__import__("ai.inner_monologue_store", fromlist=["x"]), "MONOLOGUE_DIR", tmp_path / "mono")

    dispatched, noticed, commits, filed = [], [], [], []

    async def fake_dispatch(turn):
        dispatched.append(turn)
        return SimpleNamespace(responded=True,
                               response_text="worked on the essay.\nSHARE: I finally cracked the opening")

    async def fake_refs():
        return {"palli_user_id": 1, "app_user_id": 1}

    async def fake_file_share(text, owner_id):
        filed.append(text)

    monkeypatch.setattr(wb, "_dispatch", fake_dispatch)
    monkeypatch.setattr(wb, "_get_owner_refs", fake_refs)
    monkeypatch.setattr(wb, "_notice_own_work", lambda **kw: noticed.append(kw))
    monkeypatch.setattr(wb, "_commit_workspace", lambda msg: commits.append(msg) or True)
    monkeypatch.setattr(wb, "_file_share_thread", fake_file_share)

    result = await wb.workbench_engine.run_session_once()

    assert len(dispatched) == 1
    t = dispatched[0]
    assert getattr(t, "surface", "") == "workbench"
    assert "permission to want" in str(getattr(t, "content", ""))
    assert ws.count_today(root=store_root) == 1
    assert noticed and commits
    assert filed == ["I finally cracked the opening"]
    assert result.get("ran") is True


@pytest.mark.asyncio
async def test_workbench_turn_is_not_a_conversation_with_him(monkeypatch, tmp_path):
    """Two properties the dispatch spine reads off the synthesized turn:

    origin='workbench' — NOT the dataclass default 'user'. On 'user',
    dispatch persists the workbench PROMPT to conversations as a row he
    supposedly wrote, and fires stake appraisal over a conversation that
    never happened.

    owner_id=app_user_id — _resolve_is_owner_for_turn compares against
    app_user_id, and workbench_shell is owner_only. Carry the wrong id and
    her shell is silently filtered out of her own work session."""
    import ai.workbench as wb
    import ai.workbench_store as ws

    monkeypatch.setattr(wb, "companion_settings", lambda: _comp())
    monkeypatch.setattr(wb, "WORKSPACE_ROOT", _ready_workspace(tmp_path))
    monkeypatch.setattr(ws, "WORKBENCH_DIR", tmp_path / "store")

    dispatched = []

    async def fake_dispatch(turn):
        dispatched.append(turn)
        return SimpleNamespace(responded=True, response_text="quiet one.")

    async def fake_refs():
        return {"palli_user_id": 77, "app_user_id": 42}

    monkeypatch.setattr(wb, "_dispatch", fake_dispatch)
    monkeypatch.setattr(wb, "_get_owner_refs", fake_refs)
    monkeypatch.setattr(wb, "_notice_own_work", lambda **kw: None)
    monkeypatch.setattr(wb, "_commit_workspace", lambda msg: True)

    await wb.workbench_engine.run_session_once()

    t = dispatched[0]
    assert t.metadata.origin == "workbench"
    assert t.owner_id == 42


@pytest.mark.asyncio
async def test_flag_off_engine_never_runs(monkeypatch, tmp_path):
    import ai.workbench as wb

    monkeypatch.setattr(wb, "companion_settings", lambda: _comp(own_work=False))
    called = []

    async def fake_dispatch(turn):
        called.append(turn)

    monkeypatch.setattr(wb, "_dispatch", fake_dispatch)
    out = await wb.workbench_engine.run_session_once()
    assert called == [] and out.get("ran") is False


@pytest.mark.asyncio
async def test_daily_budget_blocks(monkeypatch, tmp_path):
    import ai.workbench as wb
    import ai.workbench_store as ws

    monkeypatch.setattr(wb, "companion_settings", lambda: _comp(own_work_daily_sessions=1))
    monkeypatch.setattr(wb, "WORKSPACE_ROOT", tmp_path)
    (tmp_path / "WORKBENCH.md").write_text("x")
    (tmp_path / "projects").mkdir()
    store_root = tmp_path / "s"
    monkeypatch.setattr(ws, "WORKBENCH_DIR", store_root)
    # started must be now-ish: the budget window (own_work_day_key_hour,
    # 2026-08-06) counts by timestamp, and a real session's record always
    # carries a real clock — an epoch-1970 fixture would be invisible to it.
    ws.write_session(record={"project": "a", "started": time.time()}, root=store_root)
    out = await wb.workbench_engine.run_session_once()
    assert out.get("ran") is False and "budget" in str(out.get("why", ""))


@pytest.mark.asyncio
async def test_wall_clock_timeout_still_records_and_commits(monkeypatch, tmp_path):
    """own_work_session_minutes is a code budget (spec §3), not decoration:
    a session that overruns is cancelled, recorded, and committed — her
    files keep whatever she finished, and tomorrow exists."""
    import ai.workbench as wb
    import ai.workbench_store as ws

    monkeypatch.setattr(wb, "companion_settings", lambda: _comp())
    monkeypatch.setattr(wb, "WORKSPACE_ROOT", _ready_workspace(tmp_path))
    store_root = tmp_path / "store"
    monkeypatch.setattr(ws, "WORKBENCH_DIR", store_root)
    monkeypatch.setattr(wb, "_session_timeout_s", lambda c: 0.05)

    commits = []

    async def slow_dispatch(turn):
        await asyncio.sleep(5)

    async def fake_refs():
        return {"palli_user_id": 1, "app_user_id": 1}

    monkeypatch.setattr(wb, "_dispatch", slow_dispatch)
    monkeypatch.setattr(wb, "_get_owner_refs", fake_refs)
    monkeypatch.setattr(wb, "_notice_own_work", lambda **kw: None)
    monkeypatch.setattr(wb, "_commit_workspace", lambda msg: commits.append(msg) or True)

    out = await wb.workbench_engine.run_session_once()
    assert out.get("ran") is False and "timeout" in str(out.get("why", "")).lower()
    assert ws.count_today(root=store_root) == 1
    assert commits


def test_own_work_moved_has_a_lived_phrase():
    from ai.present_moment import _EVENT_PHRASES

    assert "own_work_moved" in _EVENT_PHRASES
    phrase = _EVENT_PHRASES["own_work_moved"]
    assert "work" in phrase and "_" not in phrase


def test_workbench_turns_do_not_fire_generic_turn_senses():
    """Sense-A suppression (spec §4b): dispatch's owner_message/turn_completed
    notices skip surface='workbench' — sense H is the single truth."""
    import core.agent.dispatch as dispatch_mod
    from core.turn.types import Turn, TurnMetadata

    guard = dispatch_mod._pm_notice_allowed
    wb = Turn(surface="workbench", owner_id=1, content="x", images=[],
              metadata=TurnMetadata(origin="workbench"))
    normal = Turn(surface="discord_text", owner_id=1, content="x", images=[],
                  metadata=TurnMetadata())
    assert guard(wb) is False
    assert guard(normal) is True


def test_workbench_panel_section_honesty_ladder(monkeypatch, tmp_path):
    import ai.workbench_store as ws
    from api.web_interiority import _workbench_section

    out = _workbench_section(enabled=False)
    assert out == {"enabled": False, "recent": []}
    monkeypatch.setattr(ws, "WORKBENCH_DIR", tmp_path)
    out = _workbench_section(enabled=True)
    assert out["enabled"] is True and out["recent"] == []       # honest zero
    ws.write_session(record={"started": 1.0, "signoff": "moved the essay",
                             "shared": False}, root=tmp_path)
    out = _workbench_section(enabled=True)
    assert out["recent"][0]["signoff"] == "moved the essay"


class _FakePresence:
    def __init__(self, state):
        self._state = state

    async def get_context_snapshot(self, *, owner_user_id):
        if self._state == "raise":
            raise RuntimeError("home-assistant service is off in this install")
        return {"machine_state": self._state}


class _FakeBrain:
    def __init__(self, last_ts):
        self._last_ts = last_ts

    async def get_conversation_history(self, **kw):
        if self._last_ts is None:
            return []
        return [{"timestamp": self._last_ts}]


def _wire_eligibility(monkeypatch, *, machine, last_msg_ago_s, now):
    import ai.workbench as wb
    import core.database as db_mod
    import core.home_presence as hp_mod

    async def fake_refs():
        return {"palli_user_id": 1, "app_user_id": 1}

    monkeypatch.setattr(wb, "_get_owner_refs", fake_refs)
    monkeypatch.setattr(hp_mod, "home_presence_service", _FakePresence(machine))
    monkeypatch.setattr(db_mod, "brain", _FakeBrain(
        None if last_msg_ago_s is None else now - last_msg_ago_s))


@pytest.mark.asyncio
async def test_eligibility_downtime_combos(monkeypatch):
    """Downtime = the CONVERSATION is quiet. Machine activity is NOT
    consulted (changed 2026-08-03, second attempt).

    The first version also required him to be away from the keyboard. He is
    almost never away, so she logged ZERO self-directed sessions in 71 hours
    with the engine healthy the whole time — and that gate structurally
    guaranteed the failure he most feared, no continuity, because returning
    to a thread requires time she never got.

    This predicate still decides WHEN she works: wrong in one direction she
    never works, wrong in the other she starts a private session while he is
    mid-sentence. Conversational recency is the whole signal now.

    2026-08-18: the owner's call — her time is NOT rationed to his downtime
    ("she can do them whenever, even while talking to me") — makes 0 (no
    gate) the DEFAULT; this test pins the knob NONZERO to keep proving the
    gate's behavior for anyone who sets it. The 0 case is proven in
    tests/ai/test_workbench_her_time.py."""
    import ai.workbench as wb

    monkeypatch.setattr(wb.WorkbenchEngine, "_settings",
                        lambda self: _comp(own_work_quiet_minutes=30))
    now = 1_800_000_000.0

    # HIS HANDS ON THE MACHINE NO LONGER BLOCK HER. This is the change: he
    # is at the keyboard, but they have not spoken in a long time, so the
    # hour is hers. Under the old gate this was False and it is why she
    # starved.
    _wire_eligibility(monkeypatch, machine="active", last_msg_ago_s=99999, now=now)
    assert await wb.workbench_engine._eligible(now) is True

    # ...but a live conversation still wins, machine active or not. She
    # never starts a private session while he is mid-sentence.
    _wire_eligibility(monkeypatch, machine="active", last_msg_ago_s=300, now=now)
    assert await wb.workbench_engine._eligible(now) is False

    # machine quiet, but they were talking 5 minutes ago ⇒ still with him
    _wire_eligibility(monkeypatch, machine="idle", last_msg_ago_s=300, now=now)
    assert await wb.workbench_engine._eligible(now) is False

    # machine quiet and the conversation is hours old ⇒ her time
    _wire_eligibility(monkeypatch, machine="idle", last_msg_ago_s=3 * 3600, now=now)
    assert await wb.workbench_engine._eligible(now) is True

    # presence unknown (the Home-Assistant half is off here) must not BLOCK —
    # it degrades to the conversation-recency signal alone
    _wire_eligibility(monkeypatch, machine="raise", last_msg_ago_s=3 * 3600, now=now)
    assert await wb.workbench_engine._eligible(now) is True

    # ...and must not force a yes either, when they just spoke
    _wire_eligibility(monkeypatch, machine="raise", last_msg_ago_s=60, now=now)
    assert await wb.workbench_engine._eligible(now) is False

    # never-spoken install: nothing recent to defer to
    _wire_eligibility(monkeypatch, machine="idle", last_msg_ago_s=None, now=now)
    assert await wb.workbench_engine._eligible(now) is True


@pytest.mark.asyncio
async def test_quiet_window_is_an_owner_knob(monkeypatch):
    """``own_work_quiet_minutes`` decides how much silence opens her hour
    (owner tune 2026-08-03: 30 → 10 in the live yaml). The same
    fifteen-minute-old conversation is hers under a 10-minute window and
    still his under the spec-default 30 — pinned against the settings
    object, not the ambient yaml, so a future retune can't flip this."""
    import ai.workbench as wb

    now = 1_800_000_000.0
    _wire_eligibility(monkeypatch, machine="active", last_msg_ago_s=15 * 60, now=now)

    monkeypatch.setattr(
        wb.workbench_engine, "_settings",
        lambda: SimpleNamespace(own_work_quiet_minutes=10))
    assert await wb.workbench_engine._eligible(now) is True

    monkeypatch.setattr(
        wb.workbench_engine, "_settings",
        lambda: SimpleNamespace(own_work_quiet_minutes=30))
    assert await wb.workbench_engine._eligible(now) is False


def test_commit_workspace_really_commits(monkeypatch, tmp_path):
    """Spec §9 write-through, with the real subprocess path. Every other
    test mocks _commit_workspace, so without this the mechanism that makes
    her work durable — the git history that IS the story of it — would
    never have run once."""
    import subprocess

    import ai.workbench as wb

    monkeypatch.setenv("WORKBENCH_SHELL_DIRECT", "1")   # no sudo hop in tests
    monkeypatch.setattr(wb, "WORKSPACE_ROOT", tmp_path)
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    for k, v in (("user.name", "PaLLi"), ("user.email", "palli@local"),
                 ("commit.gpgsign", "false")):
        subprocess.run(["git", "-C", str(tmp_path), "config", k, v], check=True)
    (tmp_path / "WORKBENCH.md").write_text("## active\n- the essay\n")

    assert wb._commit_workspace("workbench: moved the essay") is True
    log = subprocess.run(["git", "-C", str(tmp_path), "log", "--oneline"],
                         capture_output=True, text=True).stdout
    assert "moved the essay" in log
    tracked = subprocess.run(["git", "-C", str(tmp_path), "ls-files"],
                             capture_output=True, text=True).stdout
    assert "WORKBENCH.md" in tracked


@pytest.mark.asyncio
async def test_share_files_a_structurally_valid_open_thread(monkeypatch):
    """The SHARE route is her work's ONLY door to him, so the row it files
    must actually be fileable: add_open_thread raises ValueError on a kind
    outside ThreadKind, and every other test fakes this function away."""
    import ai.workbench as wb
    import core.database as db_mod
    from core.open_threads import ThreadKind

    calls = []

    class _Brain:
        async def add_open_thread(self, **kw):
            calls.append(kw)
            return "thread-id"

    monkeypatch.setattr(db_mod, "brain", _Brain())
    await wb._file_share_thread("the essay opening finally works", 7)

    assert len(calls) == 1
    row = calls[0]
    assert row["owner_user_id"] == 7
    assert "the essay opening" in row["topic_summary"]
    ThreadKind(row["kind"])          # raises if the kind is not a real one
    assert row["urgency"] == "low"


def test_no_share_marker_files_nothing():
    from ai.workbench import _extract_share

    assert _extract_share("did things.\nsign-off: stopped at X") is None
    assert _extract_share("stuff\nSHARE: the essay opening finally works") == \
        "the essay opening finally works"


# ── The wall between her working self and her talking self (2026-08-18) ──
# 20 SHARE threads sat undelivered (surface_count 0) because her only door to
# him was a 3% appraisal lottery; her sessions wrote nothing her memory could
# search, so "what is claw?" became shell archaeology two days after she
# built it (the trace band is a 2-day window). Owner's answers (08-18): keep
# the SECURITY wall (palli-work can't touch her substrate), drop the MEMORY
# wall (she remembers her own work), and "a message when it's done" for what
# she makes — changeable by a setting (own_work_share_delivery).

def _wire(monkeypatch, tmp_path, *, response, verdicts=None, **comp_kw):
    """Wire one session: workspace, store, monologue dir, owner refs, and a
    share-message seam that returns the arbiter's verdicts in order."""
    import ai.workbench as wb
    import ai.workbench_store as ws
    import ai.inner_monologue_store as ims

    monkeypatch.setattr(wb, "companion_settings", lambda: _comp(**comp_kw))
    monkeypatch.setattr(wb, "WORKSPACE_ROOT", _ready_workspace(tmp_path))
    monkeypatch.setattr(ws, "WORKBENCH_DIR", tmp_path / "store")
    monkeypatch.setattr(ims, "MONOLOGUE_DIR", tmp_path / "mono")
    sent, filed = [], []
    queue = list(verdicts or [{"disposition": "sent"}])

    async def fake_send(text, owner_id):
        sent.append((text, owner_id))
        return queue.pop(0) if queue else {"disposition": "sent"}

    async def fake_file(text, owner_id):
        filed.append(text)

    async def fake_dispatch(turn):
        return SimpleNamespace(responded=True, response_text=response)

    async def fake_refs():
        return {"palli_user_id": 1, "app_user_id": 1}

    monkeypatch.setattr(wb, "_send_share_message", fake_send, raising=False)
    monkeypatch.setattr(wb, "_file_share_thread", fake_file)
    monkeypatch.setattr(wb, "_dispatch", fake_dispatch)
    monkeypatch.setattr(wb, "_get_owner_refs", fake_refs)
    monkeypatch.setattr(wb, "_notice_own_work", lambda **kw: None)
    monkeypatch.setattr(wb, "_notice_daydream", lambda **kw: None)
    monkeypatch.setattr(wb, "_commit_workspace", lambda msg: True)
    return wb, sent, filed


@pytest.mark.asyncio
async def test_session_end_becomes_a_searchable_memory(monkeypatch, tmp_path):
    """A session leaves a monologue entry (entry_kind=workbench_session) built
    from her sign-off AND the logbook lines she wrote — write_entry indexes it,
    so the reply path's inner-memory search can find "claw" a week later."""
    import asyncio
    import ai.inner_monologue_store as ims
    import ai.inner_memory_index as imi

    indexed = []
    async def fake_index(entry, **kw):
        indexed.append(entry)
    monkeypatch.setattr(imi, "index_entry", fake_index)

    wb, sent, filed = _wire(
        monkeypatch, tmp_path, own_work_share_delivery="room",
        response="Took the claw apart and wrote reading-7.")
    # the logbook she keeps by hand — the session appended one entry
    (wb.WORKSPACE_ROOT / "WORKBENCH.md").write_text(
        "# Workbench\n\n## 2026-08-18 — 4:10 AM session\n"
        "- **claw/** — puzzle box: layer 3 key in .rodata. Committed as 91aa314.\n"
        "- **Active:** claw — built, tested.\n", encoding="utf-8")

    await wb.workbench_engine.run_session_once()
    await asyncio.sleep(0)  # let write_entry's index task run

    entries = ims.read_recent_for_context(max_entries=5, max_age_hours=1.0)
    assert entries, "the session left no memory"
    e = entries[0]
    assert (e.tick_metadata or {}).get("entry_kind") == "workbench_session"
    assert "reading-7" in e.thought and "claw" in e.thought and "puzzle box" in e.thought
    assert indexed and indexed[0].thought == e.thought


@pytest.mark.asyncio
async def test_share_is_delivered_as_a_message_by_default(monkeypatch, tmp_path):
    wb, sent, filed = _wire(   # knob absent -> default "message"
        monkeypatch, tmp_path,
        response="done.\nSHARE: the lake page has a sky-history now")
    import ai.workbench_store as ws

    result = await wb.workbench_engine.run_session_once()
    assert sent == [("the lake page has a sky-history now", 1)]
    assert filed == []
    assert result.get("share") == "the lake page has a sky-history now"
    assert ws.read_pending_shares() == []
    assert ws.read_recent(limit=1)[0]["shared"] == "sent"


@pytest.mark.asyncio
async def test_a_held_share_waits_and_goes_when_the_moment_opens(monkeypatch, tmp_path):
    """The arbiter's timing judgment holds it (quiet window / he just spoke /
    no channel) -> it waits in the workbench store; the engine's loop drains
    it later. Never a thread, never lost."""
    import ai.workbench_store as ws
    wb, sent, filed = _wire(
        monkeypatch, tmp_path, response="SHARE: made a thing",
        verdicts=[{"disposition": "held_quiet_window"},
                  {"disposition": "held_conversation"},
                  {"disposition": "sent"}])

    await wb.workbench_engine.run_session_once()
    assert [t for t, _ in sent] == ["made a thing"]
    assert filed == []
    pending = ws.read_pending_shares()
    assert len(pending) == 1 and pending[0]["text"] == "made a thing"
    assert ws.read_recent(limit=1)[0]["shared"] == "pending"

    await wb.workbench_engine.drain_pending_shares()          # still held
    assert len(ws.read_pending_shares()) == 1
    await wb.workbench_engine.drain_pending_shares()          # moment opens
    assert [t for t, _ in sent] == ["made a thing"] * 3
    assert ws.read_pending_shares() == []


@pytest.mark.asyncio
async def test_a_share_she_already_told_him_is_let_go(monkeypatch, tmp_path):
    """stale_dropped from the freshness judge = she carried it in a reply while
    it waited; nothing to resend, nothing left pending."""
    import ai.workbench_store as ws
    wb, sent, filed = _wire(
        monkeypatch, tmp_path, response="SHARE: made a thing",
        verdicts=[{"disposition": "stale_dropped", "reason": "already said"}])
    await wb.workbench_engine.run_session_once()
    assert ws.read_pending_shares() == [] and filed == []


@pytest.mark.asyncio
async def test_a_pending_share_expires_after_the_ttl(monkeypatch, tmp_path):
    import ai.workbench_store as ws
    wb, sent, filed = _wire(monkeypatch, tmp_path, response="quiet.",
                            verdicts=[{"disposition": "held_quiet_window"}])
    ws.write_pending_share(text="an old thing", owner_id=1,
                           at=time.time() - wb._PENDING_SHARE_TTL_S - 60)
    await wb.workbench_engine.drain_pending_shares()
    assert sent == [] and ws.read_pending_shares() == []


@pytest.mark.asyncio
async def test_share_waits_in_her_room_when_delivery_is_room(monkeypatch, tmp_path):
    """own_work_share_delivery=room: no push at all — her memory and the trace
    band carry it; she mentions it when it fits."""
    import ai.workbench_store as ws
    wb, sent, filed = _wire(monkeypatch, tmp_path, own_work_share_delivery="room",
                            response="SHARE: made a thing")
    await wb.workbench_engine.run_session_once()
    assert sent == [] and filed == [] and ws.read_pending_shares() == []
    assert ws.read_recent(limit=1)[0]["shared"] == "room"


@pytest.mark.asyncio
async def test_drain_does_nothing_when_delivery_is_not_message(monkeypatch, tmp_path):
    import ai.workbench_store as ws
    wb, sent, filed = _wire(monkeypatch, tmp_path, own_work_share_delivery="thread",
                            response="quiet.")
    ws.write_pending_share(text="left over", owner_id=1)
    await wb.workbench_engine.drain_pending_shares()
    assert sent == [] and len(ws.read_pending_shares()) == 1


@pytest.mark.asyncio
async def test_the_record_keeps_her_signoff_not_her_opening_line(monkeypatch, tmp_path):
    """57 live rows carried her OPENING line as 'signoff' ("Let me get my
    bearings…") because the record took splitlines()[0]. The prompt asks her
    to END with a sign-off; the record — and her memory — keep that."""
    import ai.workbench_store as ws
    wb, sent, filed = _wire(
        monkeypatch, tmp_path, own_work_share_delivery="room",
        response=("Let me get my bearings first.\n\nBuilt the claw's third layer.\n\n"
                  "Sign-off: the claw holds; next I write reading-7.\n"
                  "SHARE: come take the claw apart"))
    await wb.workbench_engine.run_session_once()
    row = ws.read_recent(limit=1)[0]
    assert row["signoff"] == "Sign-off: the claw holds; next I write reading-7."


@pytest.mark.asyncio
async def test_a_quiet_session_is_remembered_but_not_carried(monkeypatch, tmp_path):
    """No logbook lines and no SHARE = she sat down and nothing moved. Still
    hers to find later (indexed), but not substantive — three of those a day
    must not crowd the recent-thoughts band her replies read."""
    import ai.inner_monologue_store as ims
    wb, sent, filed = _wire(monkeypatch, tmp_path, own_work_share_delivery="room",
                            response="Nothing new tonight; the room is whole.")
    await wb.workbench_engine.run_session_once()
    all_entries = ims.read_entries(days_back=1, substantive_only=False)
    assert len(all_entries) == 1
    assert (all_entries[0].tick_metadata or {}).get("entry_kind") == "workbench_session"
    assert all_entries[0].is_substantive is False
    assert ims.read_recent_for_context(max_entries=5, max_age_hours=1.0) == []


# ── Her time (item 5, 2026-08-18) ────────────────────────────────────────
# 5a. Her logbook, TRUTHFULLY: the session prompt showed the FIRST 4,000
# chars of an 18,000-char append-only file (July 28 → ~Aug 4, forever) and
# the daydream lane the first 1,200. She said so herself on 08-06: "The
# snapshot in this prompt is stale again — it stops at 12:25 AM, missing the
# whole day's work" — and 20+ sessions since were spent re-verifying stale
# claims ("same stale daydream, same true record"). Same class as the trace's
# head-keep bug (08-14). Recent sections first; the head note says what's
# omitted; the file is hers to read in full.

def _long_logbook(n_sections=12, per=600):
    parts = ["# Workbench", ""]
    for i in range(n_sections):
        parts.append(f"## 2026-08-{i+1:02d} — session {i}")
        parts.append(f"- **proj-{i}/** — " + ("x" * per))
        parts.append("")
    return "\n".join(parts)


def test_logbook_recent_read_keeps_the_newest_sections_whole():
    from ai.workbench import _read_logbook_recent
    body = _long_logbook()
    out = _read_logbook_recent(text=body, cap=2000)
    assert "session 11" in out and "proj-11/" in out          # the newest
    assert "session 0" not in out                            # the oldest is gone
    assert out.startswith("# Workbench")                     # the file's own title
    assert "earlier section" in out and "omitted" in out     # honest head note
    # snapped to a section boundary — never a half bullet
    first_section = out.split("## ", 1)[1]
    assert first_section.startswith("2026-08-")
    assert len(out) <= 2000 + 200


def test_logbook_recent_read_is_whole_when_it_fits():
    from ai.workbench import _read_logbook_recent
    body = _long_logbook(n_sections=2, per=50)
    assert _read_logbook_recent(text=body, cap=4000) == body.strip()


def test_session_prompt_shows_her_latest_work_not_her_first(tmp_path, monkeypatch):
    import ai.workbench as wb
    from ai.workbench import _render_session_prompt
    (tmp_path / "WORKBENCH.md").write_text(_long_logbook(), encoding="utf-8")
    (tmp_path / "SUGGESTIONS.md").write_text("# Suggestions\n", encoding="utf-8")
    monkeypatch.setattr(wb, "companion_settings", lambda: _comp())
    text = _render_session_prompt(root=tmp_path, sessions_left=2, active_max=2)
    assert "session 11" in text and "session 0" not in text


def test_daydream_sees_her_recent_workbench_too(tmp_path, monkeypatch):
    import ai.workbench as wb
    import ai.daydream as dd
    (tmp_path / "WORKBENCH.md").write_text(_long_logbook(), encoding="utf-8")
    monkeypatch.setattr(wb, "WORKSPACE_ROOT", tmp_path)
    block = dd._workbench_block()
    assert "session 11" in block and "session 0" not in block


# 5b. The daydream intent, dated + one-shot: the session sees how old it is,
# and a session that saw it marks it taken up, so the next sit-down is not
# handed the same line as if it were new.

def test_session_prompt_dates_the_intent(tmp_path, monkeypatch):
    import ai.workbench as wb
    import ai.daydream_store as dds
    from ai.workbench import _render_session_prompt
    (tmp_path / "WORKBENCH.md").write_text("# Workbench\n", encoding="utf-8")
    (tmp_path / "SUGGESTIONS.md").write_text("# Suggestions\n", encoding="utf-8")
    monkeypatch.setattr(dds, "DAYDREAM_DIR", tmp_path / "daydream")
    monkeypatch.setattr(wb, "companion_settings", lambda: _comp(wanting=True))
    dds.write_intent("pick the poem back up", at=time.time() - 2 * 3600)
    text = _render_session_prompt(root=tmp_path, sessions_left=2, active_max=2)
    assert "FROM YOUR LAST DAYDREAM" in text and "pick the poem back up" in text
    assert "2 hours ago" in text or "2h ago" in text


@pytest.mark.asyncio
async def test_a_session_that_saw_the_intent_takes_it_up(monkeypatch, tmp_path):
    import ai.daydream_store as dds
    monkeypatch.setattr(dds, "DAYDREAM_DIR", tmp_path / "daydream")
    dds.write_intent("pick the poem back up")
    wb, sent, filed = _wire(monkeypatch, tmp_path, own_work_share_delivery="room",
                            wanting=True, response="picked it up; done for now.")
    await wb.workbench_engine.run_session_once()
    assert dds.read_intent() == ""                       # taken up
    assert dds.read_intent_record()["consumed_at"] is not None


def test_daydream_sees_whether_its_last_intent_was_taken_up(tmp_path, monkeypatch):
    import ai.workbench as wb
    import ai.daydream as dd
    import ai.daydream_store as dds
    (tmp_path / "WORKBENCH.md").write_text("# Workbench\n", encoding="utf-8")
    monkeypatch.setattr(wb, "WORKSPACE_ROOT", tmp_path)
    monkeypatch.setattr(dds, "DAYDREAM_DIR", tmp_path / "daydream")
    dds.write_intent("pick the poem back up", at=time.time() - 3 * 3600)
    block = dd._workbench_block()
    assert "pick the poem back up" in block and "not yet taken up" in block
    dds.consume_intent(at=time.time() - 600)
    block = dd._workbench_block()
    assert "pick the poem back up" in block and "taken up" in block and "not yet" not in block
