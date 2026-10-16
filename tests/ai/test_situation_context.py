from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from ai.situation_context import SituationContext, SituationFragment


def test_situation_fragment_has_expected_fields():
    fragment = SituationFragment(
        source="time",
        text="It's Tuesday afternoon (2:15 PM).",
        available=True,
    )
    assert fragment.source == "time"
    assert fragment.text == "It's Tuesday afternoon (2:15 PM)."
    assert fragment.available is True
    assert fragment.reason == ""
    assert fragment.metadata == {}


def test_situation_fragment_with_reason_when_unavailable():
    fragment = SituationFragment(
        source="desktop",
        text="",
        available=False,
        reason="not yet implemented",
    )
    assert fragment.available is False
    assert fragment.reason == "not yet implemented"


def test_situation_fragment_is_frozen():
    fragment = SituationFragment(source="time", text="x", available=True)
    with pytest.raises((AttributeError, TypeError)):
        fragment.source = "modified"


def test_situation_context_collects_fragments_and_prose():
    fragments = [
        SituationFragment(source="time", text="It's Tuesday.", available=True),
        SituationFragment(source="desktop", text="", available=False, reason="not yet implemented"),
    ]
    ctx = SituationContext(
        fragments=fragments,
        prose="It's Tuesday.\n\nUnavailable senses: desktop (not yet implemented).",
        unavailable_sources=["desktop"],
        snapshot={"time_bucket": "afternoon"},
    )
    assert ctx.fragments == fragments
    assert "Tuesday" in ctx.prose
    assert ctx.unavailable_sources == ["desktop"]
    assert ctx.snapshot == {"time_bucket": "afternoon"}


def test_situation_context_is_frozen():
    ctx = SituationContext(fragments=[], prose="", unavailable_sources=[])
    with pytest.raises((AttributeError, TypeError)):
        ctx.prose = "modified"


@pytest.fixture(autouse=True)
def _host_senses_on_by_default(monkeypatch):
    """The desktop / machine-presence sources are registered only when their
    services are enabled (2026-08-18), and the owner's live .env.local now
    ships them OFF for this headless host. Every test here that reaches
    build_situation_context asserts behavior with the senses ON unless it
    says otherwise in its own body — pin that here rather than reading his
    environment (the c74389f / 3352e3d law)."""
    import core.config as cfg
    monkeypatch.setattr(cfg.config, "LOCAL_MACHINE_PRESENCE_ENABLED", True, raising=False)
    monkeypatch.setattr(cfg.config, "DESKTOP_ACTIVITY_ENABLED", True, raising=False)
    yield


@pytest.mark.asyncio
async def test_gather_time_of_day_is_always_available():
    from ai.situation_context import gather_time_of_day

    fragment = await gather_time_of_day()
    assert fragment.source == "time"
    assert fragment.available is True
    assert fragment.reason == ""
    assert fragment.text.startswith("It's ")
    assert any(
        bucket in fragment.text
        for bucket in ("morning", "afternoon", "evening", "late night")
    )


@pytest.mark.asyncio
async def test_gather_time_of_day_includes_day_name():
    from ai.situation_context import gather_time_of_day

    fragment = await gather_time_of_day()
    day_names = {"Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"}
    assert any(day in fragment.text for day in day_names)


@pytest.mark.asyncio
async def test_build_situation_context_runs_time_source_and_returns_prose():
    from ai.situation_context import build_situation_context

    ctx = await build_situation_context(owner_user_id=1)
    assert any(f.source == "time" and f.available for f in ctx.fragments)
    assert ctx.prose.startswith("It's ")
    assert isinstance(ctx.prose, str)
    assert isinstance(ctx.fragments, list)
    assert isinstance(ctx.unavailable_sources, list)
    assert isinstance(ctx.snapshot, dict)


def test_assemble_snapshot_only_merges_available_source_metadata():
    from ai.situation_context import _assemble_snapshot

    fragments = [
        SituationFragment(
            source="time",
            text="It's Tuesday.",
            available=True,
            metadata={"time_bucket": "afternoon"},
        ),
        SituationFragment(
            source="desktop",
            text="",
            available=False,
            reason="timeout",
            metadata={"desktop_summary_source": "focused_window"},
        ),
    ]

    assert _assemble_snapshot(fragments) == {"time_bucket": "afternoon"}


@pytest.mark.asyncio
async def test_build_situation_context_registers_every_source(monkeypatch):
    # The host senses are registered only when enabled (2026-08-18); pin
    # them ON here so this asserts behavior, not the owner's live env
    # (the c74389f / 3352e3d law — his .env.local now ships them false).
    import core.config as cfg
    from ai.situation_context import build_situation_context

    monkeypatch.setattr(cfg.config, "LOCAL_MACHINE_PRESENCE_ENABLED", True, raising=False)
    monkeypatch.setattr(cfg.config, "DESKTOP_ACTIVITY_ENABLED", True, raising=False)
    # step 23 (2026-08-24): the house line is a flag-gated source too — pinned ON
    import ai.situation_context as sc
    monkeypatch.setattr(sc, "_lan_presence_on", lambda: True)
    ctx = await build_situation_context(owner_user_id=1)
    source_names = {f.source for f in ctx.fragments}
    assert source_names == {
        "time", "conversation", "desktop", "machine_presence", "operator_agenda",
        "recent_activity", "owner_presence", "lan_presence",
    }


@pytest.mark.asyncio
async def test_no_source_remains_stubbed_at_end_of_phase_2():
    """Sentinel: once Phase 2 is complete, no source should still carry
    reason='not yet implemented'. This guards against new sources being
    registered as stubs without being implemented.
    """
    from ai.situation_context import build_situation_context

    ctx = await build_situation_context(owner_user_id=1)
    for f in ctx.fragments:
        assert f.reason != "not yet implemented", (
            f"Source {f.source!r} is still a stub. Implement it or remove it."
        )


@pytest.mark.asyncio
async def test_prose_contains_unavailability_trailer_when_sources_failed():
    """When any source fails via _safe_gather, its name appears in the trailer."""
    from ai.situation_context import build_situation_context

    async def _boom(owner_user_id):
        raise RuntimeError("forced failure")

    with patch("ai.situation_context.gather_desktop_perception", _boom):
        with patch("ai.situation_context.gather_machine_presence", _boom):
            ctx = await build_situation_context(owner_user_id=1)

    assert "Unavailable senses:" in ctx.prose
    assert "desktop" in ctx.prose
    assert "machine_presence" in ctx.prose


def test_trailer_is_absent_when_all_sources_available():
    from ai.situation_context import _assemble_prose

    fragments = [
        SituationFragment(source="time", text="It's Tuesday.", available=True),
        SituationFragment(source="desktop", text="VS Code focused.", available=True),
    ]
    prose = _assemble_prose(fragments)
    assert "Unavailable senses:" not in prose
    assert "It's Tuesday." in prose
    assert "VS Code focused." in prose


@pytest.mark.asyncio
async def test_per_source_exception_yields_unavailable_fragment():
    """A source that raises must not break the tick; it becomes unavailable."""
    from ai.situation_context import build_situation_context

    async def _boom(owner_user_id):
        raise RuntimeError("simulated desktop crash")

    with patch("ai.situation_context.gather_desktop_perception", _boom):
        ctx = await build_situation_context(owner_user_id=1)

    desktop = next(f for f in ctx.fragments if f.source == "desktop")
    assert desktop.available is False
    assert "simulated desktop crash" in desktop.reason
    time_fragment = next(f for f in ctx.fragments if f.source == "time")
    assert time_fragment.available is True


@pytest.mark.asyncio
async def test_exception_reason_preserves_full_message():
    """Full error text flows through — no truncation."""
    from ai.situation_context import build_situation_context

    long_message = "x" * 200

    async def _boom(owner_user_id):
        raise RuntimeError(long_message)

    with patch("ai.situation_context.gather_desktop_perception", _boom):
        ctx = await build_situation_context(owner_user_id=1)

    desktop = next(f for f in ctx.fragments if f.source == "desktop")
    assert long_message in desktop.reason
    assert len(desktop.reason) >= 200


@pytest.mark.asyncio
async def test_source_timeout_yields_unavailable_with_reason_timeout():
    """A source that hangs longer than timeout is skipped with reason='timeout'."""
    from ai.situation_context import build_situation_context

    async def _hang(owner_user_id):
        await asyncio.sleep(5.0)
        return SituationFragment(source="desktop", text="oops", available=True)

    with patch("ai.situation_context.gather_desktop_perception", _hang):
        ctx = await build_situation_context(owner_user_id=1, timeout_seconds=0.1)

    desktop = next(f for f in ctx.fragments if f.source == "desktop")
    assert desktop.available is False
    assert desktop.reason == "timeout"
    time_fragment = next(f for f in ctx.fragments if f.source == "time")
    assert time_fragment.available is True


@pytest.mark.asyncio
async def test_timeout_isolation_one_hang_does_not_block_others():
    """Total latency must be at most ~timeout, not sum of sources."""
    import time as _t
    from ai.situation_context import build_situation_context

    async def _hang(owner_user_id):
        await asyncio.sleep(10.0)

    with patch("ai.situation_context.gather_desktop_perception", _hang):
        started = _t.monotonic()
        ctx = await build_situation_context(owner_user_id=1, timeout_seconds=0.2)
        elapsed = _t.monotonic() - started

    assert elapsed < 1.0
    assert "desktop" in ctx.unavailable_sources


@pytest.mark.asyncio
async def test_cancelled_error_propagates_from_source():
    """CancelledError must not be swallowed - clean shutdown depends on it."""
    from ai.situation_context import build_situation_context

    async def _cancelled(owner_user_id):
        raise asyncio.CancelledError()

    with patch("ai.situation_context.gather_desktop_perception", _cancelled):
        with pytest.raises(asyncio.CancelledError):
            await build_situation_context(owner_user_id=1, timeout_seconds=1.0)


@pytest.mark.asyncio
async def test_conversation_recency_with_pre_fetched_history_formats_correctly():
    """Uses pre-fetched history rows with UNIX float timestamps (real DB shape)."""
    import time as _t
    from ai.situation_context import gather_conversation_recency

    now = _t.time()
    history = [
        {
            "timestamp": now - 2 * 3600,
            "role": "user",
            "content": "hey",
            "platform": "local",
        },
        {
            "timestamp": now - 30 * 60,
            "role": "assistant",
            "content": "hi",
            "platform": "local",
        },
    ]

    fragment = await gather_conversation_recency(owner_user_id=1, recent_history=history)
    assert fragment.source == "conversation"
    assert fragment.available is True
    assert "30 minute" in fragment.text
    assert "PaLLi" in fragment.text
    assert "1 message in the last hour" in fragment.text
    assert "2 in the last 4 hours" in fragment.text
    assert "2 in the last 12 hours" in fragment.text
    assert "2 in the last 24 hours" in fragment.text
    # Perspective: the fragment is addressed to PaLLi, so an
    # assistant-authored last message reads "you speaking".
    assert "latest turn ended with you speaking" in fragment.text
    assert fragment.metadata["conversation_count_last_1h"] == 1
    assert fragment.metadata["conversation_count_last_4h"] == 2
    assert fragment.metadata["conversation_count_last_12h"] == 2
    assert fragment.metadata["conversation_count_last_24h"] == 2
    assert fragment.metadata["conversation_last_message_role"] == "assistant"


@pytest.mark.asyncio
async def test_conversation_recency_accepts_iso_timestamp_strings():
    """Defensive: some callers or older rows may carry ISO strings."""
    import datetime as _dt
    from ai.situation_context import gather_conversation_recency

    now_ts = _dt.datetime.now(tz=_dt.timezone.utc)
    history = [
        {
            "timestamp": (now_ts - _dt.timedelta(hours=3)).isoformat(),
            "role": "user",
            "content": "morning",
            "platform": "discord",
        },
    ]

    fragment = await gather_conversation_recency(owner_user_id=1, recent_history=history)
    assert fragment.available is True
    assert "from them" in fragment.text
    assert "discord" in fragment.text.lower()


@pytest.mark.asyncio
async def test_conversation_recency_empty_history_returns_zero_state():
    from ai.situation_context import gather_conversation_recency

    fragment = await gather_conversation_recency(owner_user_id=1, recent_history=[])
    assert fragment.available is True
    assert fragment.text == "No prior conversation on record."
    assert fragment.metadata["conversation_count_last_1h"] == 0
    assert fragment.metadata["conversation_last_message_at"] is None


@pytest.mark.asyncio
async def test_conversation_recency_malformed_timestamps_degrades_gracefully():
    from ai.situation_context import gather_conversation_recency

    bogus = [{"timestamp": "not-a-date", "role": "user", "content": "hi"}]
    fragment = await gather_conversation_recency(owner_user_id=1, recent_history=bogus)
    assert fragment.available is True
    assert "Conversation timing data unavailable" in fragment.text


@pytest.mark.asyncio
async def test_conversation_recency_lazy_fetch_exception_yields_unavailable():
    """If lazy fetch hits an error and no history was pre-fetched, fragment is unavailable."""
    from ai.situation_context import gather_conversation_recency

    with patch(
        "core.local_mode.get_local_owner_refs",
        new=AsyncMock(side_effect=RuntimeError("db down")),
    ):
        fragment = await gather_conversation_recency(owner_user_id=1, recent_history=None)

    assert fragment.available is False
    assert "history fetch failed" in fragment.reason


@pytest.mark.asyncio
async def test_desktop_perception_formats_focused_window_prose():
    from ai.situation_context import gather_desktop_perception

    fake_snapshot = {
        "focused_window": {"title": "inner_monologue.py - Visual Studio Code", "app": "Code"},
        "adapter_health": {"status": "ready"},
        "capture_backend": "native",
    }

    with patch(
        "ai.situation_context._desktop_perception_service",
        AsyncMock(get_passive_snapshot=AsyncMock(return_value=fake_snapshot)),
    ):
        fragment = await gather_desktop_perception(owner_user_id=1)

    assert fragment.source == "desktop"
    assert fragment.available is True
    assert "Code" in fragment.text or "Visual Studio Code" in fragment.text
    assert "inner_monologue.py" in fragment.text
    assert fragment.metadata["desktop_snapshot_source"] == "desktop_passive"
    assert fragment.metadata["desktop_summary_source"] == "focused_window"


@pytest.mark.asyncio
async def test_desktop_perception_no_focused_window():
    from ai.situation_context import gather_desktop_perception

    fake_snapshot = {
        "focused_window": None,
        "adapter_health": {"status": "ready"},
        "capture_backend": "native",
    }

    with patch(
        "ai.situation_context._desktop_perception_service",
        AsyncMock(get_passive_snapshot=AsyncMock(return_value=fake_snapshot)),
    ):
        fragment = await gather_desktop_perception(owner_user_id=1)

    assert fragment.available is True
    assert fragment.text == "No window currently focused."
    assert fragment.metadata["desktop_summary_source"] == "no_focused_window"


@pytest.mark.asyncio
async def test_desktop_perception_does_not_redact_window_titles():
    """Redaction disabled — full title (including sensitive terms) flows through."""
    from ai.situation_context import gather_desktop_perception

    fake_snapshot = {
        "focused_window": {"title": "My Bank - Account Password Entry", "app": "Chrome"},
        "adapter_health": {"status": "ready"},
        "capture_backend": "native",
    }

    with patch(
        "ai.situation_context._desktop_perception_service",
        AsyncMock(get_passive_snapshot=AsyncMock(return_value=fake_snapshot)),
    ):
        fragment = await gather_desktop_perception(owner_user_id=1)

    assert "Password Entry" in fragment.text
    assert "redacted" not in fragment.text.lower()
    assert "Chrome" in fragment.text
    assert fragment.metadata["desktop_title_redacted"] is False
    assert "Password Entry" in fragment.metadata["desktop_focused_window_title"]


@pytest.mark.asyncio
async def test_desktop_perception_preserves_full_title():
    """Truncation disabled — the full window title flows through regardless of length."""
    from ai.situation_context import gather_desktop_perception

    long_title = "a" * 200
    fake_snapshot = {
        "focused_window": {"title": long_title, "app": "Terminal"},
        "adapter_health": {"status": "ready"},
        "capture_backend": "native",
    }

    with patch(
        "ai.situation_context._desktop_perception_service",
        AsyncMock(get_passive_snapshot=AsyncMock(return_value=fake_snapshot)),
    ):
        fragment = await gather_desktop_perception(owner_user_id=1)

    assert long_title in fragment.text
    assert "\u2026" not in fragment.text
    assert fragment.metadata["desktop_focused_window_title"] == long_title


@pytest.mark.asyncio
async def test_desktop_perception_renders_full_snapshot_with_app_label():
    """Great Excision (2026-07-09): converted from the machine-operator
    preference test — the rich branch is gone; the same full-snapshot
    rendering now flows through the passive source."""
    from ai.situation_context import gather_desktop_perception

    fake_snapshot = {
        "focused_window": {"title": "palli-local - ai/situation_context.py", "wm_class": "code.Code"},
        "focused_app_adapter": {"label": "VS Code"},
        "active_browser_session": {"title": "Ignored Browser", "url": "https://example.test/path"},
        "desktop_perception": {
            "last_inspection": {
                "summary": "Focused window shows the repo editor.",
                "sensitive_detected": False,
            }
        },
    }

    with patch(
        "ai.situation_context._desktop_perception_service",
        AsyncMock(get_passive_snapshot=AsyncMock(return_value=fake_snapshot)),
    ):
        fragment = await gather_desktop_perception(owner_user_id=1)

    assert fragment.available is True
    assert fragment.text == "VS Code focused on palli-local - ai/situation_context.py."
    assert fragment.metadata["desktop_snapshot_source"] == "desktop_passive"
    assert fragment.metadata["desktop_focused_app_label"] == "VS Code"


@pytest.mark.asyncio
async def test_desktop_perception_uses_browser_session_when_no_focused_window():
    from ai.situation_context import gather_desktop_perception

    fake_snapshot = {
        "focused_window": None,
        "active_browser_session": {"title": "Docs - Example", "url": "https://example.test/docs"},
    }

    with patch(
        "ai.situation_context._desktop_perception_service",
        AsyncMock(get_passive_snapshot=AsyncMock(return_value=fake_snapshot)),
    ):
        fragment = await gather_desktop_perception(owner_user_id=1)

    assert fragment.available is True
    assert fragment.text == "Browser session active (Docs - Example)."
    assert fragment.metadata["desktop_summary_source"] == "browser_session"
    assert fragment.metadata["desktop_browser_host"] == "example.test"


@pytest.mark.asyncio
async def test_desktop_perception_does_not_surface_sensitive_inspection_summary():
    from ai.situation_context import gather_desktop_perception

    fake_snapshot = {
        "focused_window": None,
        "desktop_perception": {
            "last_inspection": {
                "summary": "Password reset screen with one-time code.",
                "sensitive_detected": True,
            }
        },
    }

    with patch(
        "ai.situation_context._desktop_perception_service",
        AsyncMock(get_passive_snapshot=AsyncMock(return_value=fake_snapshot)),
    ):
        fragment = await gather_desktop_perception(owner_user_id=1)

    assert fragment.available is True
    assert fragment.text == "No window currently focused."
    assert "password" not in fragment.text.lower()


@pytest.mark.asyncio
async def test_machine_presence_active_state():
    """Active state reports duration since the machine entered active."""
    import time as _t
    from ai.situation_context import gather_machine_presence

    now = _t.time()
    fake_snapshot = {
        "machine_state": "active",
        "last_machine_active_at": now - 3 * 3600,  # active for 3h
        "last_event_at": now - 3 * 3600,
        # HA-sourced fields present but should be ignored
        "owner_presence_state": "home",
        "privacy_state": "shared",
    }

    with patch(
        "ai.situation_context._home_presence_service",
        AsyncMock(get_context_snapshot=AsyncMock(return_value=fake_snapshot)),
    ):
        fragment = await gather_machine_presence(owner_user_id=1)

    assert fragment.source == "machine_presence"
    assert fragment.available is True
    assert "active" in fragment.text.lower()
    assert "3 hour" in fragment.text
    assert fragment.metadata["machine_state"] == "active"
    # HA-only fields must not leak
    assert "home" not in fragment.text.lower()
    assert "privacy" not in fragment.text.lower()


@pytest.mark.asyncio
async def test_machine_presence_locked_state():
    import time as _t
    from ai.situation_context import gather_machine_presence

    now = _t.time()
    fake_snapshot = {
        "machine_state": "locked",
        "last_machine_active_at": now - 2 * 3600,
        "last_event_at": now - 45 * 60,  # locked 45 min ago
    }

    with patch(
        "ai.situation_context._home_presence_service",
        AsyncMock(get_context_snapshot=AsyncMock(return_value=fake_snapshot)),
    ):
        fragment = await gather_machine_presence(owner_user_id=1)

    assert fragment.available is True
    assert "locked" in fragment.text.lower()
    assert "45 minutes" in fragment.text


@pytest.mark.asyncio
async def test_machine_presence_unknown_state_reports_zero_state():
    """Machine-state='unknown' is the default when nothing's been recorded."""
    from ai.situation_context import gather_machine_presence

    fake_snapshot = {"machine_state": "unknown", "last_event_at": None}

    with patch(
        "ai.situation_context._home_presence_service",
        AsyncMock(get_context_snapshot=AsyncMock(return_value=fake_snapshot)),
    ):
        fragment = await gather_machine_presence(owner_user_id=1)

    assert fragment.available is True
    assert "not yet recorded" in fragment.text.lower() or "unknown" in fragment.text.lower()


@pytest.mark.asyncio
async def test_machine_presence_service_not_started_returns_unavailable():
    from ai.situation_context import gather_machine_presence

    class _DeadService:
        async def get_context_snapshot(self, **_):
            raise AttributeError("service not initialized")

    with patch("ai.situation_context._home_presence_service", _DeadService()):
        fragment = await gather_machine_presence(owner_user_id=1)

    assert fragment.available is False
    assert "service not started" in fragment.reason


@pytest.mark.asyncio
async def test_operator_and_agenda_empty_returns_breathing_room_text():
    from ai.situation_context import gather_operator_and_agenda

    fake_brain = AsyncMock()
    fake_brain.list_companion_agenda_items = AsyncMock(return_value=[])

    with patch("ai.situation_context.brain", fake_brain):
        fragment = await gather_operator_and_agenda(owner_user_id=1)

    assert fragment.source == "operator_agenda"
    assert fragment.available is True
    assert "no" in fragment.text.lower()
    assert "thread" in fragment.text.lower()


def _open_thread(
    *,
    id,
    topic_summary,
    urgency="when-the-moment-fits",
    kind=None,
    last_surfaced_at=None,
    created_at=0.0,
    why_it_matters="it matters",
):
    """Build a real OpenThread row for gather_operator_and_agenda's fixtures.

    Use the actual dataclass so attribute access matches production (t.id, t.kind,
    t.urgency, t.topic_summary, t.last_surfaced_at, t.created_at) — the
    function no longer accepts dict rows.
    """
    from core.open_threads import OpenThread, ThreadKind, ThreadStatus

    return OpenThread(
        id=id,
        owner_user_id=1,
        status=ThreadStatus.ACTIVE,
        created_at=created_at,
        updated_at=created_at,
        topic_summary=topic_summary,
        why_it_matters=why_it_matters,
        kind=kind or ThreadKind.OTHER,
        urgency=urgency,
        last_surfaced_at=last_surfaced_at,
        surface_count=0,
        resolved_at=None,
        resolution_reason=None,
    )


@pytest.mark.asyncio
async def test_operator_and_agenda_selects_top_three_by_recency():
    # Migrated 2026-07-24: OpenThread carries no salience float (agenda ->
    # open_threads rebuild), so the old salience-threshold filter this test
    # pinned no longer exists. Re-pointed at the replacement selection rule:
    # most-recently-touched first (last_surfaced_at, falling back to
    # created_at), capped at 3 — see gather_operator_and_agenda's docstring.
    from ai.situation_context import gather_operator_and_agenda

    threads = [
        _open_thread(id="old", topic_summary="oldest thread", last_surfaced_at=100.0),
        _open_thread(id="mid", topic_summary="middle thread", last_surfaced_at=200.0),
        _open_thread(id="new", topic_summary="newest thread", last_surfaced_at=300.0),
        _open_thread(
            id="never",
            topic_summary="never surfaced thread",
            last_surfaced_at=None,
            created_at=50.0,
        ),
    ]
    fake_brain = AsyncMock()
    fake_brain.list_active_open_threads = AsyncMock(return_value=threads)

    with patch("ai.situation_context.brain", fake_brain):
        fragment = await gather_operator_and_agenda(owner_user_id=1)

    # Top 3 by recency, most-recent first; the least-recently-touched
    # ("never", falling back to its old created_at=50) is dropped.
    assert fragment.metadata["agenda_top_item_ids"] == ["new", "mid", "old"]
    assert fragment.metadata["agenda_pressing_count"] == 3
    assert "never surfaced thread" not in fragment.text
    assert (
        fragment.text.index("newest thread")
        < fragment.text.index("middle thread")
        < fragment.text.index("oldest thread")
    )


@pytest.mark.asyncio
async def test_operator_and_agenda_tolerates_messy_text_and_never_surfaced_threads():
    # Migrated 2026-07-24: rows are now OpenThread objects straight off a
    # NOT-NULL schema, so "malformed dict row" no longer applies — the
    # closest surviving hazards are whitespace-cluttered topic_summary text
    # (_clean_text must still collapse it) and a thread that has never been
    # surfaced (last_surfaced_at=None) falling back to created_at for both
    # the recency sort and the "untouched N days" arithmetic without
    # raising on None.
    import time as _t

    from ai.situation_context import gather_operator_and_agenda

    now = _t.time()
    threads = [
        _open_thread(
            id="messy",
            topic_summary="  fallback   label\n\nwith   odd\tspacing  ",
            last_surfaced_at=now - 3600,
        ),
        _open_thread(
            id="stale",
            topic_summary="an old never-surfaced thread",
            last_surfaced_at=None,
            created_at=now - 5 * 86400,
        ),
    ]
    fake_brain = AsyncMock()
    fake_brain.list_active_open_threads = AsyncMock(return_value=threads)

    with patch("ai.situation_context.brain", fake_brain):
        fragment = await gather_operator_and_agenda(owner_user_id=1)

    assert fragment.available is True
    assert "fallback label with odd spacing" in fragment.text
    assert "untouched 5 days" in fragment.text
    assert fragment.metadata["agenda_top_item_ids"] == ["messy", "stale"]


@pytest.mark.asyncio
async def test_operator_and_agenda_store_exception_degrades():
    """A RuntimeError from the agenda store becomes an unavailable fragment
    via the orchestrator's _safe_gather wrapper, without breaking the tick."""
    # Migrated 2026-07-24: mocked call target swapped from the dead
    # list_companion_agenda_items to list_active_open_threads; the
    # degrade-on-exception behavior under test is unchanged.
    from ai.situation_context import build_situation_context

    fake_brain = AsyncMock()
    fake_brain.list_active_open_threads = AsyncMock(side_effect=RuntimeError("db locked"))

    with patch("ai.situation_context.brain", fake_brain):
        ctx = await build_situation_context(owner_user_id=1)

    oa = next(f for f in ctx.fragments if f.source == "operator_agenda")
    assert oa.available is False
    assert "db locked" in oa.reason.lower() or "runtimeerror" in oa.reason.lower()


# ── The senses point at the wrong machine (2026-08-18) ─────────────────
# On this headless VM `loginctl` reads SSH/tmux terminal idleness (Claude Code
# sessions, not the owner at his desk) and there is no window manager, so
# `focused_app` is always "unknown". She narrated "he's in the unknown
# folder, half an hour in" 15+ times in three days, into her monologue →
# day-gists → workbench pages → STORY_OF_US. The owner's call: turn the fake
# senses OFF until a real one exists (the house microphone, later). Off means
# the sources are not registered — no "Unavailable senses:" nagging, no
# "Machine: unknown." line, nothing for her to muse about.

@pytest.mark.asyncio
async def test_host_senses_are_not_registered_when_disabled(monkeypatch):
    import core.config as cfg
    from ai.situation_context import build_situation_context

    monkeypatch.setattr(cfg.config, "LOCAL_MACHINE_PRESENCE_ENABLED", False, raising=False)
    monkeypatch.setattr(cfg.config, "DESKTOP_ACTIVITY_ENABLED", False, raising=False)
    # step 23 (2026-08-24): pin the house sense OFF — this asserts the host
    # senses, not the owner's live yaml (which now ships lan_presence true)
    import ai.situation_context as sc
    monkeypatch.setattr(sc, "_lan_presence_on", lambda: False)
    ctx = await build_situation_context(owner_user_id=1)
    names = {f.source for f in ctx.fragments}
    assert names == {"time", "conversation", "operator_agenda", "owner_presence"}
    assert "machine_presence" not in ctx.unavailable_sources
    assert "desktop" not in ctx.unavailable_sources
    assert "recent_activity" not in ctx.unavailable_sources
    assert "Unavailable senses" not in ctx.prose or (
        "desktop" not in ctx.prose and "machine" not in ctx.prose
    )


@pytest.mark.asyncio
async def test_host_senses_stay_registered_when_enabled(monkeypatch):
    import core.config as cfg
    from ai.situation_context import build_situation_context

    monkeypatch.setattr(cfg.config, "LOCAL_MACHINE_PRESENCE_ENABLED", True, raising=False)
    monkeypatch.setattr(cfg.config, "DESKTOP_ACTIVITY_ENABLED", True, raising=False)
    ctx = await build_situation_context(owner_user_id=1)
    names = {f.source for f in ctx.fragments}
    assert {"desktop", "machine_presence", "recent_activity"} <= names


@pytest.mark.asyncio
async def test_owner_presence_source_renders_discord_line_and_is_silent_when_unknown():
    """The Discord presence sense (2026-08-18) reaches the monologue's
    situation prose as its own source; unknown = an available, EMPTY fragment
    (no 'Unavailable senses' nagging, no sentence about the unknown)."""
    from ai.situation_context import build_situation_context
    from core import owner_presence as op

    op.reset_for_tests()
    try:
        ctx = await build_situation_context(owner_user_id=1)
        frag = next(f for f in ctx.fragments if f.source == "owner_presence")
        assert frag.available is True and frag.text == ""
        assert "owner_presence" not in ctx.unavailable_sources
        op.record_discord_presence(status="idle", activity="", at=1000.0)
        ctx = await build_situation_context(owner_user_id=1)
        frag = next(f for f in ctx.fragments if f.source == "owner_presence")
        assert frag.text == "Discord: idle"
        assert "Discord: idle" in ctx.prose
    finally:
        op.reset_for_tests()


# ── step 23 (2026-08-24): the house line is a fragment, registered under its flag ──

@pytest.mark.asyncio
async def test_lan_presence_fragment_registers_only_when_on(monkeypatch):
    import ai.situation_context as sc
    from ai.situation_context import build_situation_context

    monkeypatch.setattr(sc, "_lan_presence_on", lambda: True)
    ctx = await build_situation_context(owner_user_id=1)
    assert "lan_presence" in {f.source for f in ctx.fragments}
    monkeypatch.setattr(sc, "_lan_presence_on", lambda: False)
    ctx = await build_situation_context(owner_user_id=1)
    assert "lan_presence" not in {f.source for f in ctx.fragments}


@pytest.mark.asyncio
async def test_gather_lan_presence_is_an_available_empty_fragment_when_off(monkeypatch):
    from types import SimpleNamespace

    from ai.situation_context import gather_lan_presence
    from core import lan_presence as lp

    # pinned OFF — never the live book (data/lan_presence.json exists since 08-24 17:31)
    monkeypatch.setattr(lp, "companion_settings", lambda: SimpleNamespace(lan_presence=False, lan_devices={}))
    frag = await gather_lan_presence()
    assert frag.source == "lan_presence" and frag.available is True
    assert frag.text == ""                                   # unbooted/off = silence, no trailer
