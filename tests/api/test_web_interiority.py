"""Tests for the Wave 0b interiority router + its additive timeline reader.

Idioms match the sibling web-router tests: bare FastAPI() + ASGITransport,
_require_user monkeypatched on the module under test for happy paths, the
real guard + local_auth_bypass_enabled=False for the 401 path, and the
introspect tests' store patch points for the organ stores.
"""

from __future__ import annotations

import time

import pytest


def _now() -> float:
    return time.time()


@pytest.mark.asyncio
async def test_recent_reads_returns_window_newest_first_with_needs_support():
    from core.database import brain

    from ai.turn_read import recent_reads

    owner_id = 970001  # unique per test: the test DB is shared session-wide
    now = _now()
    conn = await brain.get_connection()
    try:
        for days_ago, text in ((40.0, "too old"), (2.0, "recent read"), (1.0, "newest read")):
            await conn.execute(
                "INSERT INTO emotional_timeline "
                "(owner_user_id, created_at, read_text, weight, needs_support, carries_forward) "
                "VALUES ($1, $2, $3, $4, $5, $6)",
                owner_id,
                now - days_ago * 86400.0,
                text,
                0.5,
                0.25,
                "",
            )
    finally:
        await conn.close()

    rows = await recent_reads(owner_id=owner_id, days_back=30, limit=50)

    assert [r["read_text"] for r in rows] == ["newest read", "recent read"]
    assert all(set(r) == {"created_at", "read_text", "weight", "needs_support"} for r in rows)
    assert rows[0]["needs_support"] == pytest.approx(0.25)


@pytest.mark.asyncio
async def test_recent_reads_respects_limit_and_quiet_degrades():
    from core.database import brain

    from ai.turn_read import recent_reads

    owner_id = 970002
    now = _now()
    conn = await brain.get_connection()
    try:
        for i in range(5):
            await conn.execute(
                "INSERT INTO emotional_timeline "
                "(owner_user_id, created_at, read_text, weight, needs_support, carries_forward) "
                "VALUES ($1, $2, $3, $4, $5, $6)",
                owner_id,
                now - float(i),
                f"read {i}",
                0.1,
                0.0,
                "",
            )
    finally:
        await conn.close()

    rows = await recent_reads(owner_id=owner_id, days_back=30, limit=3)
    assert len(rows) == 3
    assert rows[0]["read_text"] == "read 0"

    class _BrokenBrain:
        async def get_connection(self):
            raise RuntimeError("db down")

    assert await recent_reads(owner_id=owner_id, brain_client=_BrokenBrain()) == []


import httpx
from fastapi import FastAPI

from api import web_auth, web_interiority


def _build_app() -> FastAPI:
    app = FastAPI()
    app.include_router(web_interiority.router, prefix="/api/web")
    return app


async def _fake_require_user(request):
    return {"id": 7, "email": "owner@example.com"}


def _wire_owner(monkeypatch, owner_id: int = 7):
    async def _fake_refs():
        return {"app_user_id": owner_id, "palli_user_id": None, "discord_user_id": None}

    monkeypatch.setattr(web_interiority, "_require_user", _fake_require_user)
    monkeypatch.setattr(web_interiority, "get_local_owner_refs", _fake_refs)


async def _get(path: str):
    transport = httpx.ASGITransport(app=_build_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        return await client.get(path)


def test_router_registers_only_get_methods():
    methods: set[str] = set()
    for route in web_interiority.router.routes:
        methods.update(getattr(route, "methods", None) or set())
    assert methods == {"GET"}


@pytest.mark.asyncio
async def test_overview_anonymous_is_401(monkeypatch):
    monkeypatch.setattr(web_auth, "local_auth_bypass_enabled", lambda: False)
    resp = await _get("/api/web/interiority/overview")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_overview_since_clamps_to_365_days(monkeypatch):
    _wire_owner(monkeypatch)
    before = _now()
    resp = await _get("/api/web/interiority/overview?since=0")
    after = _now()
    assert resp.status_code == 200
    body = resp.json()
    assert body["since"] >= before - 365 * 86400.0 - 5.0
    assert body["since"] <= after - 365 * 86400.0 + 5.0

    resp = await _get(f"/api/web/interiority/overview?since={_now() + 10 * 86400.0}")
    assert resp.json()["since"] <= _now() + 5.0


@pytest.mark.asyncio
async def test_overview_default_window_is_30_days(monkeypatch):
    _wire_owner(monkeypatch)
    before = _now()
    resp = await _get("/api/web/interiority/overview")
    body = resp.json()
    assert body["since"] == pytest.approx(before - 30 * 86400.0, abs=10.0)


@pytest.mark.asyncio
async def test_overview_flag_off_organs_render_off_not_empty(monkeypatch):
    """Default test env: night_cycle + stake_ledger + introspection all OFF
    (conftest forces ledger off; the others default False)."""
    from unittest.mock import patch

    _wire_owner(monkeypatch)
    # Force every gate, never read the owner's live yaml — c74389f's lesson:
    # a flag-off test that consults owner config asserts his config, not
    # behavior, and goes green or red on a line he edits.
    with (
        patch("ai.night_cycle.night_cycle_enabled", lambda: False),
        patch("ai.consolidation.consolidation_enabled", lambda: False),
        patch("ai.narrative_self.narrative_self_enabled", lambda: False),
        patch("ai.passions.passions_enabled", lambda: False),
        # present_moment has no dedicated enabler of its own — it reads the
        # shared companion-faculty accessor (same one caring_memory reads),
        # so the name check keeps caring_memory's own default untouched.
        patch("core.turn.companion_flags.faculty_enabled", lambda name: name != "present_moment"),
        # own_work has its own reader (_own_work_on -> companion_settings())
        # rather than faculty_enabled, so the line above never covered it.
        # This test passed for the WRONG REASON while the flag shipped false —
        # the ambient yaml happened to agree — and went red the moment the
        # owner flipped it true on 2026-07-28. Force it, per this block's own
        # c74389f lesson: never assert his config, assert behavior.
        patch("api.web_interiority._own_work_on", lambda: False),
    ):
        resp = await _get("/api/web/interiority/overview")
    body = resp.json()
    assert body["reflections"] == {"enabled": False, "items": []}
    assert body["stakes"] == {"enabled": False, "open": [], "resolved": []}
    organs = {o["key"]: o for o in body["organs"]}
    assert set(organs) == {
        "night_cycle",
        "consolidation",
        "narrative_self",
        "stake_ledger",
        "caring_memory",
        "introspection",
        "passions",
        "present_moment",
        "own_work",
    }
    assert organs["night_cycle"]["enabled"] is False
    assert organs["stake_ledger"]["enabled"] is False
    assert "error" not in organs["stake_ledger"]
    assert "error" not in organs["caring_memory"]
    assert organs["introspection"]["first_artifact_ts"] is None
    assert organs["introspection"]["last_artifact_ts"] is None
    assert organs["introspection"]["count_since"] == 0
    # The organs that shipped after the page did. Flag-off reads as an
    # honest off, never an error (the introspect honesty contract).
    for key in ("consolidation", "narrative_self", "passions", "present_moment",
                "own_work"):
        assert organs[key]["enabled"] is False
        assert "error" not in organs[key]
    # Her workbench, flag-off: an honest off, never an error or an absence.
    assert body["workbench"] == {"enabled": False, "recent": []}


@pytest.mark.asyncio
async def test_consolidation_chip_counts_day_gists(monkeypatch, tmp_path):
    """The consolidation chip dates off her day-gist files, same as the
    night-cycle chip dates off her reflections."""
    import ai.day_gist_store as gist_store

    _wire_owner(monkeypatch)
    gist_dir = tmp_path / "day_gists"
    monkeypatch.setattr(gist_store, "DAY_GIST_DIR", gist_dir)
    monkeypatch.setattr("ai.consolidation.consolidation_enabled", lambda: True)
    gist_store.write_day_gist(day="2026-07-16", text="that day held", root=gist_dir)
    gist_store.write_day_gist(day="2026-07-17", text="that day turned", root=gist_dir)

    body = (await _get("/api/web/interiority/overview?since=0")).json()
    chip = {o["key"]: o for o in body["organs"]}["consolidation"]
    assert chip["enabled"] is True
    assert chip["count_since"] == 2
    assert chip["first_artifact_ts"] is not None
    assert chip["last_artifact_ts"] > chip["first_artifact_ts"]
    assert "error" not in chip


@pytest.mark.asyncio
async def test_narrative_self_chip_dates_off_her_commits(monkeypatch, tmp_path):
    """Her self-revisions ARE her commits, so the chip dates off git. A
    last_artifact_ts that stops advancing is the signal a weekly pass died —
    which is exactly how the W29 marker bug hid (no chip existed)."""
    import subprocess

    import ai.narrative_self as ns

    _wire_owner(monkeypatch)
    repo = tmp_path / "palli"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "p@l.li"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "PaLLi"], cwd=repo, check=True)
    (repo / "MY_STORY.md").write_text("the arc so far\n", encoding="utf-8")
    subprocess.run(["git", "add", "MY_STORY.md"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "first authorship"], cwd=repo, check=True)

    monkeypatch.setattr(ns, "identity_palli_root", lambda: repo)
    monkeypatch.setattr(ns, "narrative_self_enabled", lambda: True)

    body = (await _get("/api/web/interiority/overview?since=0")).json()
    chip = {o["key"]: o for o in body["organs"]}["narrative_self"]
    assert chip["enabled"] is True
    assert chip["count_since"] == 1
    assert chip["first_artifact_ts"] == chip["last_artifact_ts"]
    assert "error" not in chip


@pytest.mark.asyncio
async def test_broken_store_import_degrades_one_chip_not_the_page(monkeypatch):
    """A store that cannot be imported marks its own chip error:true and
    leaves the rest of the overview intact.

    Pinned because extracting the shared _dated_files_chip helper moved the
    store import out of the try block, which would have turned a degraded
    chip into a 500 on the whole page — invisible in a green run, since no
    other test breaks the import.
    """
    import api.web_interiority as wi

    _wire_owner(monkeypatch)

    def _boom():
        raise ImportError("night_cycle_store is unavailable")

    monkeypatch.setattr(wi, "_night_cycle_dir", _boom)

    resp = await _get("/api/web/interiority/overview")
    assert resp.status_code == 200
    organs = {o["key"]: o for o in resp.json()["organs"]}
    assert organs["night_cycle"]["error"] is True
    assert "error" not in organs["consolidation"]
    assert "error" not in organs["caring_memory"]


@pytest.mark.asyncio
async def test_narrative_self_chip_never_creates_her_repo(monkeypatch, tmp_path):
    """The GET path can never create state — the interiority page's standing
    law, here applied to git instead of ledger schema."""
    import ai.narrative_self as ns

    _wire_owner(monkeypatch)
    absent = tmp_path / "no_repo_here"
    monkeypatch.setattr(ns, "identity_palli_root", lambda: absent)
    monkeypatch.setattr(ns, "narrative_self_enabled", lambda: True)

    body = (await _get("/api/web/interiority/overview?since=0")).json()
    chip = {o["key"]: o for o in body["organs"]}["narrative_self"]
    assert chip["count_since"] == 0
    assert chip["first_artifact_ts"] is None
    assert chip["last_artifact_ts"] is None
    assert "error" not in chip  # absence is honest, not a read failure
    assert not absent.exists()


@pytest.mark.asyncio
async def test_passions_chip_counts_journal_pages(monkeypatch, tmp_path):
    """The passions chip dates off her journal pages, same shape as the
    night-cycle/consolidation dated-file chips — just the '*.md' pattern
    (Task 8's pattern param on _dated_files_chip) instead of '*.jsonl'."""
    import ai.narrative_self as ns

    _wire_owner(monkeypatch)
    root = tmp_path / "palli"
    jdir = root / "journal"
    jdir.mkdir(parents=True)
    monkeypatch.setattr(ns, "identity_palli_root", lambda: root)
    monkeypatch.setattr("ai.passions.passions_enabled", lambda: True)
    (jdir / "2026-07-16.md").write_text("that day held\n", encoding="utf-8")
    (jdir / "2026-07-17.md").write_text("that day turned\n", encoding="utf-8")

    body = (await _get("/api/web/interiority/overview?since=0")).json()
    chip = {o["key"]: o for o in body["organs"]}["passions"]
    assert chip["enabled"] is True
    assert chip["count_since"] == 2
    assert chip["first_artifact_ts"] is not None
    assert chip["last_artifact_ts"] > chip["first_artifact_ts"]
    assert "error" not in chip


@pytest.mark.asyncio
async def test_passions_chip_missing_journal_dir_is_honest_zero_and_creates_nothing(
    monkeypatch, tmp_path
):
    """The GET path can never create state — the interiority page's standing
    law, here applied to organ 3's journal directory (pre-first journal
    page, or pre-first-authorship entirely)."""
    import ai.narrative_self as ns

    _wire_owner(monkeypatch)
    absent = tmp_path / "no_journal_here"
    monkeypatch.setattr(ns, "identity_palli_root", lambda: absent)
    monkeypatch.setattr("ai.passions.passions_enabled", lambda: True)

    body = (await _get("/api/web/interiority/overview?since=0")).json()
    chip = {o["key"]: o for o in body["organs"]}["passions"]
    assert chip["enabled"] is True
    assert chip["count_since"] == 0
    assert chip["first_artifact_ts"] is None
    assert chip["last_artifact_ts"] is None
    assert "error" not in chip  # absence is honest, not a read failure
    assert not absent.exists()
    assert not (absent / "journal").exists()


@pytest.mark.asyncio
async def test_overview_flag_on_empty_store_is_honest_zero(monkeypatch):
    from unittest.mock import patch

    _wire_owner(monkeypatch)
    with (
        patch("ai.night_cycle.night_cycle_enabled", lambda: True),
        patch("ai.night_cycle_store.read_recent", lambda **kw: []),
    ):
        resp = await _get("/api/web/interiority/overview")
    body = resp.json()
    assert body["reflections"]["enabled"] is True
    assert body["reflections"]["items"] == []
    assert "error" not in body["reflections"]


@pytest.mark.asyncio
async def test_overview_reader_error_is_error_not_absence(monkeypatch):
    from unittest.mock import patch

    def _boom(**kw):
        raise RuntimeError("store on fire")

    _wire_owner(monkeypatch)
    with (
        patch("ai.night_cycle.night_cycle_enabled", lambda: True),
        patch("ai.night_cycle_store.read_recent", _boom),
    ):
        resp = await _get("/api/web/interiority/overview")
    body = resp.json()
    assert body["reflections"]["enabled"] is True
    assert body["reflections"]["error"] is True
    assert body["reflections"]["items"] == []


@pytest.mark.asyncio
async def test_overview_reflections_capped_at_30_newest_first(monkeypatch):
    """Dates are anchored to today (not hardcoded literals — this task's F4
    added an exact date-epoch >= since filter, and a fixed-calendar fixture
    would drift out of the default 30-day window as real time passes).
    All 30 "meaning" entries share yesterday's date (comfortably inside any
    reasonable window, unlike a 30-day-spanning fixture which would ride the
    window's exact boundary depending on time-of-day); "newest meaning" alone
    carries today's strictly-later date so it sorts first unambiguously."""
    import datetime as _dt
    from unittest.mock import patch

    from ai.night_cycle_store import NightCycleEntry

    today = _dt.date.today()
    yesterday = (today - _dt.timedelta(days=1)).isoformat()
    entries = [
        NightCycleEntry(date=yesterday, text=f"meaning {day}", created_at=_now() - day * 86400.0)
        for day in range(1, 31)
    ] + [NightCycleEntry(date=today.isoformat(), text="newest meaning", created_at=_now() - 1.0)]

    _wire_owner(monkeypatch)
    with (
        patch("ai.night_cycle.night_cycle_enabled", lambda: True),
        patch("ai.night_cycle_store.read_recent", lambda **kw: list(entries)),
    ):
        resp = await _get("/api/web/interiority/overview")
    items = resp.json()["reflections"]["items"]
    assert len(items) == 30
    assert items[0]["date"] == today.isoformat()
    assert items[0]["text"] == "newest meaning"


@pytest.mark.asyncio
async def test_overview_stakes_open_first_within_total_cap(monkeypatch):
    from unittest.mock import AsyncMock, patch

    from ai import stake_ledger
    from ai.stake_ledger import Stake

    def _stake(i: int, status: str) -> Stake:
        return Stake(
            id=i,
            kind="worry",
            stake=f"stake {i}",
            status=status,
            resolution="settled fine" if status != "open" else "",
            horizon_at=None,
            source_channel_id="",
            last_surfaced_at=None,
            created_at=_now() - i * 60.0,
            updated_at=_now() - i * 30.0,
            resolved_at=(_now() - i * 10.0) if status != "open" else None,
        )

    open_rows = [_stake(i, "open") for i in range(1, 56)]           # 55 open
    closed_rows = [_stake(100 + i, "resolved") for i in range(1, 21)]  # 20 resolved

    # The section's table-existence guard (this task's Fix 1) needs the real
    # table to be present; open_stakes/recent_stakes are mocked below to
    # avoid inserting 75 rows, but the table itself must genuinely exist.
    await stake_ledger.ensure_schema()

    _wire_owner(monkeypatch)
    with (
        patch("ai.stake_ledger.ledger_enabled", lambda: True),
        patch("ai.stake_ledger.open_stakes", AsyncMock(return_value=open_rows)),
        patch("ai.stake_ledger.recent_stakes", AsyncMock(return_value=open_rows[:3] + closed_rows)),
    ):
        resp = await _get("/api/web/interiority/overview")
    stakes = resp.json()["stakes"]
    assert stakes["enabled"] is True
    assert len(stakes["open"]) == 55
    assert len(stakes["open"]) + len(stakes["resolved"]) <= 60
    assert stakes["open"][0]["stake"] == "stake 1"           # newest open first
    assert all(s["resolution"] for s in stakes["resolved"])  # resolution prose present
    assert all("resolved_at" in s for s in stakes["resolved"])


@pytest.mark.asyncio
async def test_overview_timeline_and_threads_sections(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, patch

    reads = [
        {"created_at": _now() - 60.0, "read_text": "steady tonight", "weight": 0.6, "needs_support": 0.2},
    ]

    def _thread(i: int) -> SimpleNamespace:
        return SimpleNamespace(
            topic_summary=f"thread {i}",
            why_it_matters="it matters",
            created_at=_now() - i * 3600.0,
            updated_at=_now() - i * 60.0,
        )

    threads = [_thread(i) for i in range(1, 26)]  # 25 active, oldest-update last

    import core.database as _db

    _wire_owner(monkeypatch)
    with (
        patch("ai.turn_read.recent_reads", AsyncMock(return_value=reads)),
        patch.object(_db.brain, "list_active_open_threads", AsyncMock(return_value=threads), create=True),
    ):
        resp = await _get("/api/web/interiority/overview")
    body = resp.json()
    assert body["timeline"]["enabled"] is True
    assert body["timeline"]["items"][0]["read_text"] == "steady tonight"
    assert body["timeline"]["items"][0]["needs_support"] == pytest.approx(0.2)
    assert len(body["threads"]["items"]) == 20                       # capped
    assert body["threads"]["items"][0]["topic_summary"] == "thread 1"  # newest updated_at first
    assert body["threads"]["items"][0]["why_it_matters"] == "it matters"


@pytest.mark.asyncio
async def test_stakes_section_missing_table_is_honest_zero_and_never_calls_stores(monkeypatch):
    """Read-only by construction: with no stakes table, the GET path must not
    invoke the ledger readers (whose ensure_schema would CREATE the table)."""
    from unittest.mock import AsyncMock, patch

    _wire_owner(monkeypatch)

    async def _no_table(conn, name):
        return False

    open_probe = AsyncMock(side_effect=AssertionError("open_stakes must not be called"))
    recent_probe = AsyncMock(side_effect=AssertionError("recent_stakes must not be called"))

    monkeypatch.setattr(web_interiority, "_table_exists", _no_table)
    with (
        patch("ai.stake_ledger.ledger_enabled", lambda: True),
        patch("ai.stake_ledger.open_stakes", open_probe),
        patch("ai.stake_ledger.recent_stakes", recent_probe),
    ):
        resp = await _get("/api/web/interiority/overview")
    body = resp.json()
    assert body["stakes"] == {"enabled": True, "open": [], "resolved": []}
    open_probe.assert_not_awaited()
    recent_probe.assert_not_awaited()


def _mono_entry(
    *,
    ts: float,
    thought: str,
    tag: str | None = "reflective",
    snapshot_text: str = "SNAPSHOT_MARKER_do_not_leak",
    candidate: str | None = "CANDIDATE_MARKER_do_not_leak",
):
    from ai.inner_monologue_store import MonologueEntry

    return MonologueEntry(
        timestamp=ts,
        thought=thought,
        situation_snapshot={"scene": snapshot_text},
        tick_metadata={"substantive": True, "TICK_MARKER_do_not_leak": True},
        sentiment={"tag": tag, "read": "READ_MARKER_do_not_leak", "confidence": 0.7}
        if tag
        else None,
        proactive_candidate=candidate,
        candidate_kind="KIND_MARKER_do_not_leak",
        candidate_action_hint="HINT_MARKER_do_not_leak",
    )


@pytest.mark.asyncio
async def test_monologue_default_is_presence_only_no_private_substrings(monkeypatch):
    from unittest.mock import patch

    _wire_owner(monkeypatch)
    entries = [
        _mono_entry(ts=_now() - 120.0, thought="PRIVATE_THOUGHT_alpha kept circling"),
        _mono_entry(ts=_now() - 60.0, thought="PRIVATE_THOUGHT_beta about the storm", tag="uneasy"),
    ]
    with patch("ai.inner_monologue_store.read_entries", lambda **kw: list(entries)):
        resp = await _get("/api/web/interiority/monologue")
    assert resp.status_code == 200
    body = resp.json()
    raw_payload = resp.text
    for marker in (
        "PRIVATE_THOUGHT_alpha",
        "PRIVATE_THOUGHT_beta",
        "SNAPSHOT_MARKER_do_not_leak",
        "CANDIDATE_MARKER_do_not_leak",
        "TICK_MARKER_do_not_leak",
        "READ_MARKER_do_not_leak",
        "HINT_MARKER_do_not_leak",
        "KIND_MARKER_do_not_leak",
    ):
        assert marker not in raw_payload
    assert body["raw"] is False
    assert body["count"] == 2
    assert len(body["entries"]) == 2
    assert set(body["entries"][0]) == {"ts", "tag", "chars"}
    assert body["entries"][0]["tag"] == "uneasy"          # newest first
    assert body["entries"][0]["chars"] == len("PRIVATE_THOUGHT_beta about the storm")


@pytest.mark.asyncio
async def test_monologue_raw_carries_thought_and_nothing_else_private(monkeypatch):
    from unittest.mock import patch

    _wire_owner(monkeypatch)
    entries = [_mono_entry(ts=_now() - 60.0, thought="PRIVATE_THOUGHT_gamma, held gently")]
    with patch("ai.inner_monologue_store.read_entries", lambda **kw: list(entries)):
        resp = await _get("/api/web/interiority/monologue?raw=1")
    body = resp.json()
    raw_payload = resp.text
    assert body["raw"] is True
    assert body["entries"][0]["thought"] == "PRIVATE_THOUGHT_gamma, held gently"
    assert set(body["entries"][0]) == {"ts", "tag", "chars", "thought"}
    for marker in (
        "SNAPSHOT_MARKER_do_not_leak",
        "CANDIDATE_MARKER_do_not_leak",
        "TICK_MARKER_do_not_leak",
        "READ_MARKER_do_not_leak",
        "HINT_MARKER_do_not_leak",
        "KIND_MARKER_do_not_leak",
    ):
        assert marker not in raw_payload


@pytest.mark.asyncio
async def test_monologue_caps_presence_200_raw_60(monkeypatch):
    from unittest.mock import patch

    _wire_owner(monkeypatch)
    entries = [
        _mono_entry(ts=_now() - float(i), thought=f"thought {i}") for i in range(250)
    ]
    with patch("ai.inner_monologue_store.read_entries", lambda **kw: list(entries)):
        presence = await _get("/api/web/interiority/monologue")
        raw = await _get("/api/web/interiority/monologue?raw=1")
    assert presence.json()["count"] == 250
    assert len(presence.json()["entries"]) == 200
    assert raw.json()["count"] == 250
    assert len(raw.json()["entries"]) == 60


@pytest.mark.asyncio
async def test_monologue_digest_counts_from_tier_dirs(monkeypatch, tmp_path):
    from unittest.mock import patch

    import ai.inner_monologue_store as mono_store

    _wire_owner(monkeypatch)
    weekly = tmp_path / "digests" / "weekly"
    monthly = tmp_path / "digests" / "monthly"
    weekly.mkdir(parents=True)
    monthly.mkdir(parents=True)
    (weekly / "2026-W27.jsonl").write_text("{}\n", encoding="utf-8")
    (weekly / "2026-W28.jsonl").write_text("{}\n", encoding="utf-8")
    (monthly / "2026-06.jsonl").write_text("{}\n", encoding="utf-8")

    with (
        patch.object(mono_store, "MONOLOGUE_DIR", tmp_path),
        patch("ai.inner_monologue_store.read_entries", lambda **kw: []),
    ):
        resp = await _get("/api/web/interiority/monologue")
    body = resp.json()
    assert body["digests"] == {"weekly": 2, "monthly": 1}
    assert body["count"] == 0
    assert body["entries"] == []


@pytest.mark.asyncio
async def test_monologue_anonymous_is_401(monkeypatch):
    monkeypatch.setattr(web_auth, "local_auth_bypass_enabled", lambda: False)
    resp = await _get("/api/web/interiority/monologue")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_monologue_reader_error_is_error_not_absence(monkeypatch):
    from unittest.mock import patch

    def _boom(**kw):
        raise RuntimeError("monologue store on fire")

    _wire_owner(monkeypatch)
    with patch("ai.inner_monologue_store.read_entries", _boom):
        resp = await _get("/api/web/interiority/monologue")
    body = resp.json()
    assert body["error"] is True
    assert body["entries"] == []


@pytest.mark.asyncio
async def test_integration_seeded_stores_round_trip(monkeypatch, tmp_path):
    """Seed the REAL stores (probe-engine seeding APIs), read through the
    REAL endpoints. Monologue must stay presence-only by default."""
    import datetime as _dt

    import ai.inner_monologue_store as mono_store
    import ai.night_cycle_store as night_store
    from ai import stake_ledger
    from core.database import brain

    owner_id = 970100
    now = _now()
    _wire_owner(monkeypatch, owner_id=owner_id)

    # night cycle -> tmp dir (file store is NOT redirected by conftest)
    nc_dir = tmp_path / "night_cycle"
    monkeypatch.setattr(night_store, "NIGHT_CYCLE_DIR", nc_dir)
    yesterday = (_dt.date.today() - _dt.timedelta(days=1)).isoformat()
    night_store.write_daily_meaning(
        date=yesterday, text="yesterday meant the quiet build", root=nc_dir
    )

    # monologue -> tmp dir
    mono_dir = tmp_path / "inner_monologue"
    monkeypatch.setattr(mono_store, "MONOLOGUE_DIR", mono_dir)
    mono_store.write_entry(
        mono_store.MonologueEntry(
            timestamp=now - 3600.0,
            thought="PRIVATE_ROUND_TRIP_thought about the humidity thread",
            situation_snapshot={"scene": "idle tick"},
            tick_metadata={"substantive": True},
            sentiment={"tag": "curious"},
        )
    )

    # stakes -> real ledger (conftest forces the gate off; turn it on)
    monkeypatch.setattr("ai.stake_ledger.ledger_enabled", lambda: True)
    monkeypatch.setattr("ai.night_cycle.night_cycle_enabled", lambda: True)
    res = await stake_ledger.apply_ops(
        [{"op": "open", "kind": "worry", "stake": "the round-trip stake holds"}],
        source_channel_id="interiority_test_seed",
    )
    assert res.get("applied") == 1

    # emotional timeline -> direct INSERT (the probe engine's idiom)
    conn = await brain.get_connection()
    try:
        await conn.execute(
            "INSERT INTO emotional_timeline "
            "(owner_user_id, created_at, read_text, weight, needs_support, carries_forward) "
            "VALUES ($1, $2, $3, $4, $5, $6)",
            owner_id,
            now - 7200.0,
            "he sounded lighter tonight",
            0.6,
            0.1,
            "",
        )
    finally:
        await conn.close()

    overview = await _get("/api/web/interiority/overview")
    assert overview.status_code == 200
    body = overview.json()

    assert any(
        "quiet build" in item["text"] for item in body["reflections"]["items"]
    )
    assert any(
        "round-trip stake holds" in s["stake"] for s in body["stakes"]["open"]
    )
    assert any(
        "lighter tonight" in item["read_text"] for item in body["timeline"]["items"]
    )
    organs = {o["key"]: o for o in body["organs"]}
    assert organs["night_cycle"]["count_since"] == 1
    assert organs["night_cycle"]["first_artifact_ts"] is not None
    assert organs["caring_memory"]["count_since"] >= 1
    assert organs["stake_ledger"]["last_artifact_ts"] is not None

    monologue = await _get("/api/web/interiority/monologue")
    assert monologue.status_code == 200
    assert "PRIVATE_ROUND_TRIP_thought" not in monologue.text
    assert monologue.json()["count"] == 1
    assert monologue.json()["entries"][0]["tag"] == "curious"

    raw = await _get("/api/web/interiority/monologue?raw=1")
    assert "PRIVATE_ROUND_TRIP_thought" in raw.json()["entries"][0]["thought"]


@pytest.mark.asyncio
async def test_timeline_section_db_down_is_error_not_absence(monkeypatch):
    """Hard DB failure must render error:true for the timeline, never an honest zero."""
    from unittest.mock import patch

    _wire_owner(monkeypatch)

    class _DownBrain:
        async def get_connection(self):
            raise RuntimeError("db down")

    import core.database as _db

    with patch.object(_db, "brain", _DownBrain(), create=True):
        resp = await _get("/api/web/interiority/overview")
    body = resp.json()
    assert body["timeline"]["error"] is True
    assert body["timeline"]["items"] == []
