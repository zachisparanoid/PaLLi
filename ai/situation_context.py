"""
Situation context enrichment for the inner monologue.

Gathers data from five sources (time, conversation recency, desktop
focus, machine presence, operator/agenda work), runs each source in
parallel with a per-source timeout, catches exceptions individually,
and assembles a natural-language paragraph the monologue prompt
consumes via the {situation_context} placeholder.

Sources:
    - gather_time_of_day: zero deps, always available
    - gather_conversation_recency: rolls up timestamps from recent history
    - gather_desktop_perception: focused window via passive snapshot
    - gather_machine_presence: machine-side lock/idle/active state
    - gather_operator_and_agenda: most-recently-touched open threads (operator/agenda)

Per-source failures degrade gracefully: the fragment is marked
unavailable with a short reason, the source name is appended to
SituationContext.unavailable_sources, and an "Unavailable senses:"
trailer surfaces in the assembled prose. CancelledError always
propagates so clean shutdown works.
"""

from __future__ import annotations

import asyncio
import datetime as _dt
import logging
import math
import time as _time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional
from urllib.parse import urlparse

from core.config import config as _config
from core.database import brain
from core.desktop_activity_watcher import desktop_activity_watcher as _desktop_activity_watcher
from core.desktop_perception import desktop_perception_service as _desktop_perception_service
from core.home_presence import home_presence_service as _home_presence_service

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class SituationFragment:
    """A single piece of situation context contributed by one source."""

    source: str
    text: str
    available: bool
    reason: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class SituationContext:
    """The full assembled situation context for one monologue tick."""

    fragments: List[SituationFragment]
    prose: str
    unavailable_sources: List[str]
    snapshot: Dict[str, Any] = field(default_factory=dict)


async def gather_time_of_day() -> SituationFragment:
    """Compute the time-of-day fragment. Zero dependencies, always available."""
    now = _time.localtime()
    hour = now.tm_hour
    day_name = _time.strftime("%A", now)

    if 5 <= hour < 12:
        time_of_day = "morning"
    elif 12 <= hour < 17:
        time_of_day = "afternoon"
    elif 17 <= hour < 21:
        time_of_day = "evening"
    else:
        time_of_day = "late night"

    formatted_time = _time.strftime("%I:%M %p", now).lstrip("0")
    text = f"It's {day_name} {time_of_day} ({formatted_time})."
    return SituationFragment(
        source="time",
        text=text,
        available=True,
        metadata={
            "time_day_name": day_name,
            "time_bucket": time_of_day,
            "time_local_clock": formatted_time,
        },
    )


def _parse_timestamp(raw: Any) -> Optional[float]:
    """Parse a history-row timestamp into a UNIX float. Accepts float, int, or ISO string."""
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        parsed = float(raw)
        return parsed if math.isfinite(parsed) else None
    if isinstance(raw, str):
        try:
            parsed = _dt.datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp()
            return parsed if math.isfinite(parsed) else None
        except ValueError:
            return None
    return None


def _safe_float(raw: Any, default: float = 0.0) -> float:
    try:
        parsed = float(raw)
    except (TypeError, ValueError):
        return default
    return parsed if math.isfinite(parsed) else default


def _format_delta(seconds: float) -> str:
    """Bucket a time delta into human-readable language."""
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        minutes = int(seconds // 60)
        return f"{minutes} minute{'s' if minutes != 1 else ''} ago"
    if seconds < 6 * 3600:
        hours = int(seconds // 3600)
        return f"{hours} hour{'s' if hours != 1 else ''} ago"
    if seconds < 24 * 3600:
        return "earlier today"
    if seconds < 48 * 3600:
        return "yesterday"
    days = int(seconds // 86400)
    return f"{days} days ago"


def _role_label(role: str) -> str:
    """Map conversation role to human-language label."""
    role_lower = (role or "").lower()
    if role_lower in ("user", "human"):
        return "from them"
    if role_lower in ("assistant", "palli", "ai"):
        return "from PaLLi"
    return f"from {role or 'unknown'}"


def _message_count_phrase(count: int, window: str) -> str:
    noun = "message" if count == 1 else "messages"
    return f"{count} {noun} in the last {window}"


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").strip().split())


def _as_mapping(value: Any) -> Dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


async def gather_conversation_recency(
    owner_user_id: int,
    recent_history: Optional[list] = None,
) -> SituationFragment:
    """Compute recency signals from conversation history timestamps."""
    rows = recent_history
    if rows is None:
        try:
            from core.database import brain
            from core.local_mode import get_local_owner_refs

            refs = await get_local_owner_refs()
            palli_uid = refs.get("palli_user_id")
            rows = []
            if palli_uid is not None:
                # No platform filter: the owner's messages live under
                # palli_user_id across every surface, never under a 'local'
                # platform or the raw Discord snowflake. The old
                # ('local', palli)+('discord', snowflake) loop returned 0 rows,
                # so this fragment misfired ("No prior conversation on record.")
                # while the owner had chatted all day (2026-07-23, same class as
                # the inner_monologue fix). One cross-surface read by owner id.
                rows = await brain.get_conversation_history(
                    user_id=int(palli_uid),
                    limit=60,
                    include_metadata=True,
                )
        except Exception:
            return SituationFragment(
                source="conversation",
                text="",
                available=False,
                reason="history fetch failed",
            )

    if not rows:
        return SituationFragment(
            source="conversation",
            text="No prior conversation on record.",
            available=True,
            metadata={
                "conversation_message_count": 0,
                "conversation_count_last_1h": 0,
                "conversation_count_last_4h": 0,
                "conversation_count_last_12h": 0,
                "conversation_count_last_24h": 0,
                "conversation_last_message_at": None,
                "conversation_last_message_role": "",
                "conversation_last_message_platform": "",
            },
        )

    now = _time.time()
    parsed: List[tuple] = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        ts = _parse_timestamp(r.get("timestamp"))
        if ts is not None:
            parsed.append((ts, r))

    if not parsed:
        return SituationFragment(
            source="conversation",
            text="Conversation timing data unavailable.",
            available=True,
            metadata={
                "conversation_message_count": 0,
                "conversation_count_last_1h": 0,
                "conversation_count_last_4h": 0,
                "conversation_count_last_12h": 0,
                "conversation_count_last_24h": 0,
                "conversation_last_message_at": None,
                "conversation_last_message_role": "",
                "conversation_last_message_platform": "",
            },
        )

    parsed.sort(key=lambda x: x[0])
    last_ts, last_row = parsed[-1]
    delta = now - last_ts
    delta_text = _format_delta(delta)
    who = _role_label(str(last_row.get("role", "")))
    last_role = str(last_row.get("role", "")).strip().lower()
    surface = str(last_row.get("platform") or "").strip().lower()
    surface_clause = f" ({surface} chat)" if surface else ""

    count_1h = sum(1 for ts, _ in parsed if now - ts <= 3600)
    count_4h = sum(1 for ts, _ in parsed if now - ts <= 4 * 3600)
    count_12h = sum(1 for ts, _ in parsed if now - ts <= 12 * 3600)
    count_24h = sum(1 for ts, _ in parsed if now - ts <= 24 * 3600)
    ending = (
        "The latest turn ended with you speaking."
        if last_role in {"assistant", "palli", "ai"}
        else "The latest turn ended with the user speaking."
        if last_role in {"user", "human"}
        else "The latest turn ended with an unknown speaker."
    )

    text = (
        f"Last message was {delta_text}, {who}{surface_clause}; "
        f"{_message_count_phrase(count_1h, 'hour')}, "
        f"{count_4h} in the last 4 hours, "
        f"{count_12h} in the last 12 hours, "
        f"{count_24h} in the last 24 hours. "
        f"{ending}"
    )
    return SituationFragment(
        source="conversation",
        text=text,
        available=True,
        metadata={
            "conversation_message_count": len(parsed),
            "conversation_count_last_1h": count_1h,
            "conversation_count_last_4h": count_4h,
            "conversation_count_last_12h": count_12h,
            "conversation_count_last_24h": count_24h,
            "conversation_last_message_at": last_ts,
            "conversation_last_message_role": last_role,
            "conversation_last_message_platform": surface,
        },
    )


_SENSITIVE_TITLE_TERMS = (
    "password", "passcode", "authentication", "keyring",
    "wallet", "security", "payment", "checkout", "purchase",
    "buy now", "subscribe", "billing", "card number", "cvv",
)


def _redact_title_if_sensitive(title: str) -> tuple[str, bool]:
    """Return (title, was_redacted). Redaction disabled — full titles always flow through."""
    return (title, False)


def _truncate_title(title: str, max_chars: int = 80) -> str:
    """Truncation disabled — full titles always flow through. max_chars retained for caller
    compatibility but ignored.
    """
    del max_chars  # intentionally unused
    return title


def _safe_window_title(raw_title: Any) -> tuple[str, bool]:
    if isinstance(raw_title, bytes):
        raw_title = raw_title.decode("utf-8", errors="replace")
    title = _clean_text(raw_title)
    title, redacted = _redact_title_if_sensitive(title)
    if not redacted:
        title = _truncate_title(title)
    return title, redacted


def _safe_browser_host(raw_url: Any) -> str:
    url = _clean_text(raw_url)
    if not url:
        return ""
    parsed = urlparse(url)
    return _clean_text(parsed.netloc or parsed.path or url)


async def _get_desktop_snapshot(owner_user_id: int) -> tuple[Dict[str, Any], str]:
    """Read the passive desktop snapshot.

    Great Excision (2026-07-09): the machine-operator "rich" branch was
    removed with the operator engine. Verified before cutting: the rich
    snapshot was truthy-but-hollow live (focused_window/app/browser all
    empty — only host metadata populated), so it was SHADOWING this passive
    read while contributing identical empty window data. Prose output is
    byte-identical; only the snapshot-source metadata string changes."""
    passive_snapshot = await _desktop_perception_service.get_passive_snapshot()
    return _as_mapping(passive_snapshot), "desktop_passive"


def _focused_app_label(*, focused_window: Dict[str, Any], focused_app: Dict[str, Any]) -> str:
    return (
        _clean_text(focused_app.get("label"))
        or _clean_text(focused_window.get("app"))
        or _clean_text(focused_window.get("process_name"))
        or _clean_text(focused_window.get("wm_class"))
    )


def _desktop_metadata(
    *,
    source: str,
    summary_source: str,
    title: str = "",
    title_redacted: bool = False,
    app_label: str = "",
    browser_host: str = "",
) -> Dict[str, Any]:
    return {
        "desktop_snapshot_source": source,
        "desktop_summary_source": summary_source,
        "desktop_focused_window_title": title,
        "desktop_title_redacted": title_redacted,
        "desktop_focused_app_label": app_label,
        "desktop_browser_host": browser_host,
    }


async def gather_desktop_perception(owner_user_id: int) -> SituationFragment:
    """Read current focused-window state, preferring richer passive runtime context."""
    snapshot, snapshot_source = await _get_desktop_snapshot(owner_user_id)

    focused = _as_mapping(snapshot.get("focused_window"))
    focused_app = _as_mapping(snapshot.get("focused_app_adapter"))
    active_browser = _as_mapping(snapshot.get("active_browser_session"))
    desktop_perception = _as_mapping(snapshot.get("desktop_perception"))
    last_inspection = _as_mapping(desktop_perception.get("last_inspection"))

    if not focused:
        browser_title, browser_redacted = _safe_window_title(active_browser.get("title"))
        browser_host = _safe_browser_host(active_browser.get("url"))
        browser_detail = browser_title or browser_host
        if browser_detail:
            return SituationFragment(
                source="desktop",
                text=f"Browser session active ({browser_detail}).",
                available=True,
                metadata=_desktop_metadata(
                    source=snapshot_source,
                    summary_source="browser_session",
                    title=browser_title,
                    title_redacted=browser_redacted,
                    browser_host=browser_host,
                ),
            )

        if last_inspection and last_inspection.get("sensitive_detected") is False:
            summary = _truncate_title(_clean_text(last_inspection.get("summary")), max_chars=120)
            if summary:
                return SituationFragment(
                    source="desktop",
                    text=f"Last desktop inspection noted {summary}.",
                    available=True,
                    metadata=_desktop_metadata(
                        source=snapshot_source,
                        summary_source="inspection_summary",
                    ),
                )

        return SituationFragment(
            source="desktop",
            text="No window currently focused.",
            available=True,
            metadata=_desktop_metadata(
                source=snapshot_source,
                summary_source="no_focused_window",
            ),
        )

    title, redacted = _safe_window_title(focused.get("title"))
    app_name = _focused_app_label(focused_window=focused, focused_app=focused_app)

    if app_name and title:
        text = f"{app_name} focused on {title}."
    elif title:
        text = f"Focused window: {title}."
    elif app_name:
        text = f"{app_name} focused."
    else:
        text = "A window is focused but details are unavailable."

    return SituationFragment(
        source="desktop",
        text=text,
        available=True,
        metadata=_desktop_metadata(
            source=snapshot_source,
            summary_source="focused_window",
            title=title,
            title_redacted=redacted,
            app_label=app_name,
        ),
    )


def _format_duration(seconds: float) -> str:
    """Human-readable duration for machine-presence prose."""
    if seconds < 60:
        s = max(int(seconds), 0)
        return f"{s} second{'s' if s != 1 else ''}"
    if seconds < 3600:
        minutes = int(seconds // 60)
        return f"{minutes} minute{'s' if minutes != 1 else ''}"
    hours = int(seconds // 3600)
    return f"{hours} hour{'s' if hours != 1 else ''}"


async def gather_machine_presence(owner_user_id: int) -> SituationFragment:
    """Read machine-side presence state. HA-sourced fields are deliberately ignored.

    Machine-side signals: machine_state in {active, idle, locked, unknown},
    last_machine_active_at, last_event_at.
    """
    try:
        snapshot = await _home_presence_service.get_context_snapshot(
            owner_user_id=int(owner_user_id)
        )
    except (AttributeError, RuntimeError):
        return SituationFragment(
            source="machine_presence",
            text="",
            available=False,
            reason="service not started",
        )

    state = str((snapshot or {}).get("machine_state") or "").strip().lower()
    now = _time.time()
    last_active = (snapshot or {}).get("last_machine_active_at")
    last_event = (snapshot or {}).get("last_event_at")

    if state == "active":
        parsed_last_active = _safe_float(last_active)
        if parsed_last_active > 0:
            text = f"Machine has been active for {_format_duration(now - parsed_last_active)}."
        else:
            text = "Machine active."
    elif state == "locked":
        parsed_last_event = _safe_float(last_event)
        if parsed_last_event > 0:
            text = f"Machine locked for {_format_duration(now - parsed_last_event)}."
        else:
            text = "Machine locked."
    elif state == "idle":
        parsed_last_event = _safe_float(last_event)
        if parsed_last_event > 0:
            text = f"Idle for {_format_duration(now - parsed_last_event)}."
        else:
            text = "Idle."
    elif state and state != "unknown":
        text = f"Machine state: {state}."
    else:
        return SituationFragment(
            source="machine_presence",
            text="Machine presence state not yet recorded.",
            available=True,
        )

    return SituationFragment(
        source="machine_presence",
        text=text,
        available=True,
        metadata={
            "machine_state": state or "unknown",
            "machine_last_active_at": _safe_float(last_active) or None,
            "machine_last_event_at": _safe_float(last_event) or None,
        },
    )


async def gather_operator_and_agenda(owner_user_id: int) -> SituationFragment:
    """Most-recently-touched open threads (the operator/agenda fragment).

    Great Excision (2026-07-09): the operator-workflow half was removed with
    the operator engine. BYTE-DISCIPLINE: operator_workflows had been 0-row
    forever, so live prose only ever rendered the two branches kept below —
    the empty-case literal (including its historical "No active operator
    work" phrasing) and the agenda clause — and both are preserved verbatim,
    as are the fragment source name and the metadata keys (constant zeros
    now). Reword only with an owner-approved prompt change.

    Live-bug fix (2026-07-24): brain.list_companion_agenda_items was deleted
    under the agenda -> open_threads rebuild (T5, 2026-05-10); every call
    here had raised AttributeError ever since, so this fragment had been
    permanently dark (caught by _safe_gather, landing in
    unavailable_sources). Swapped in brain.list_active_open_threads (see the
    working caller in the retired surface_decision lane; the arbiter's
    digest is the raise path now). Returned rows are
    OpenThread objects (core/open_threads.py), not dicts, and carry no
    salience float, so "top 3" is re-derived mechanically: most-recently
    touched first (last_surfaced_at, falling back to created_at for threads
    never yet surfaced) — a plain timestamp sort, mirroring the
    COALESCE(last_surfaced_at, created_at) ordering OpenThreadsService
    already uses elsewhere (list_by_source_kind). Urgency is a free-form
    LLM-written descriptor (see prompts/turn_reflect.txt), not a rankable
    enum, so it is rendered as prose per item rather than used as a sort
    key — ranking free text would mean inventing a keyword-scored
    heuristic, which is exactly what this fix must not do.
    """
    threads = await brain.list_active_open_threads(owner_user_id=int(owner_user_id))

    def _last_touch(t: Any) -> float:
        return t.last_surfaced_at if t.last_surfaced_at is not None else t.created_at

    pressing = sorted(threads, key=_last_touch, reverse=True)[:3]

    if not pressing:
        return SituationFragment(
            source="operator_agenda",
            text="No active operator work; no pressing open threads.",
            available=True,
            metadata={
                "operator_workflow_count": 0,
                "operator_top_workflow_id": "",
                "operator_top_workflow_status": "",
                "operator_stalled_workflow_count": 0,
                "agenda_pressing_count": 0,
                "agenda_top_item_ids": [],
            },
        )

    parts: List[str] = []

    now = _time.time()
    item_clauses: List[str] = []
    for t in pressing:
        title = _truncate_title(_clean_text(t.topic_summary) or "an open thread")
        urgency = _clean_text(t.urgency) or "no urgency noted"
        last_touch = _last_touch(t)
        days_since = int((now - last_touch) // 86400) if last_touch else 0
        if days_since >= 1:
            item_clauses.append(
                f"{title} ({urgency}, untouched {days_since} day{'s' if days_since != 1 else ''})"
            )
        else:
            item_clauses.append(f"{title} ({urgency})")
    parts.append("Top open threads: " + ", ".join(item_clauses) + ".")

    return SituationFragment(
        source="operator_agenda",
        text=" ".join(parts),
        available=True,
        metadata={
            "operator_workflow_count": 0,
            "operator_top_workflow_id": "",
            "operator_top_workflow_status": "",
            "operator_stalled_workflow_count": 0,
            "agenda_pressing_count": len(pressing),
            "agenda_top_item_ids": [t.id for t in pressing],
        },
    )


def _short_error(exc: BaseException) -> str:
    """Produce a single-line reason from an exception. Full message retained — no truncation."""
    raw = str(exc) or exc.__class__.__name__
    return " ".join(raw.split())


async def _safe_gather(
    name: str,
    coro_factory: Callable[[], Awaitable[SituationFragment]],
    *,
    timeout: float,
) -> SituationFragment:
    """Run one source with exception and timeout protection.

    CancelledError always propagates - we must honor clean shutdown.
    """
    try:
        return await asyncio.wait_for(coro_factory(), timeout=timeout)
    except asyncio.CancelledError:
        raise
    except asyncio.TimeoutError:
        logger.debug(
            "situation_context source timed out",
            extra={"source": name, "timeout": timeout},
        )
        return SituationFragment(source=name, text="", available=False, reason="timeout")
    except Exception as exc:
        logger.debug(
            "situation_context source failed",
            extra={"source": name, "error": repr(exc)},
        )
        return SituationFragment(source=name, text="", available=False, reason=_short_error(exc))


async def gather_owner_presence() -> SituationFragment:
    """His Discord status/activity (core.owner_presence) — the first real
    presence signal on this host (2026-08-18). Unknown renders as an
    available, empty fragment: no sentence about the unknown, no trailer."""
    from core.owner_presence import render_line, snapshot

    line = render_line()
    snap = snapshot()
    return SituationFragment(
        source="owner_presence",
        text=line,
        available=True,
        metadata={
            "discord_status": str(snap.get("status") or "unknown"),
            "discord_activity": str(snap.get("activity") or ""),
        },
    )


async def gather_lan_presence() -> SituationFragment:
    """"The house right now" (core.lan_presence, step 23, 2026-08-24): who's
    home on her network. Off, or an empty book, renders as an available
    EMPTY fragment — no sentence about the unknown, no trailer."""
    from core.lan_presence import lan_presence_engine, render_line

    line = render_line()
    online = 0
    if line:
        try:
            online = sum(1 for r in lan_presence_engine.book.values() if r.get("online"))
        except Exception:
            online = 0
    return SituationFragment(
        source="lan_presence",
        text=line,
        available=True,
        metadata={"devices_online": online},
    )


async def gather_recent_activity(owner_user_id: int) -> SituationFragment:
    """Summarize the rolling activity timeline (last ~30 min of focus + machine + files).

    Reads from core.desktop_activity_watcher's in-memory rolling buffer.
    No DB queries, no LLM calls — pure in-process read. Degrades gracefully
    when the watcher hasn't accumulated samples yet (just-booted runs)
    by returning an unavailable fragment with reason='no_samples_yet'.

    The fragment text is intentionally compact so it doesn't crowd the
    other senses. The richer payload lives in metadata for downstream
    consumers (proactive layer, dashboards) to read structurally.
    """
    summary = await _desktop_activity_watcher.get_summary()
    if summary.sample_count == 0:
        return SituationFragment(
            source="recent_activity",
            text="",
            available=False,
            reason="no_samples_yet",
        )
    return SituationFragment(
        source="recent_activity",
        text=summary.to_prose(),
        available=True,
        metadata={
            "activity_sample_count": summary.sample_count,
            "activity_span_seconds": summary.span_seconds,
            "activity_primary_app": summary.primary_app,
            "activity_primary_app_minutes": summary.primary_app_minutes,
            "activity_app_switches": summary.app_switches,
            "activity_machine_state_now": summary.machine_state_now,
            "activity_recent_files": list(summary.recent_files),
            "activity_idle_seconds": (
                summary.last_sample.idle_seconds if summary.last_sample else 0.0
            ),
        },
    )


async def gather_held_stakes(owner_user_id: int) -> SituationFragment:
    """Stake ledger (companion.stake_ledger, 2026-07-06): what PaLLi itself
    is holding — its own open hopes/worries/tastes/wonders, with horizon
    staleness — so the monologue's thinking can notice them and, when one is
    genuinely timely, surface it organically as a proactive candidate.

    Only ever called when the faculty is on (see build_situation_context's
    conditional gather list — an always-present-but-unavailable fragment
    would leak into the 'Unavailable senses' prose trailer and change the
    monologue prompt even with the flag off)."""
    from ai.stake_ledger import any_surfaced_within, open_stakes, render_stakes_block

    stakes = await open_stakes(limit=8)
    if not stakes:
        return SituationFragment(
            source="held_stakes",
            text="I'm not carrying any particular stakes of my own right now.",
            available=True,
            metadata={"held_stakes_count": 0},
        )
    now = _time.time()
    budget_closed = await any_surfaced_within(24 * 3600)
    surfaced_recently = [
        s.id
        for s in stakes
        if s.last_surfaced_at is not None and (now - s.last_surfaced_at) < 72 * 3600
    ]
    # Render per-stake, stripping only the leading "- " marker (a global
    # replace corrupted any prose containing "hyphen space" — review finding),
    # and annotate recently-raised stakes so the restraint rule the prompt
    # states is backed by data the model can actually see (review finding).
    lines = []
    for s, line in zip(stakes, render_stakes_block(stakes, now=now).split("\n")):
        entry = line[2:] if line.startswith("- ") else line
        if s.id in surfaced_recently:
            age_h = max(1, int((now - float(s.last_surfaced_at)) // 3600))
            entry += f" (I already raised this {age_h}h ago — context only, do not bring it up)"
        lines.append(entry)
    restraint_note = (
        "I've already reached out about one of these within the last day, so "
        "none of them are surfaceable right now — they're context only. "
        if budget_closed
        else "Bring one up only if this moment genuinely calls for it. "
    )
    text = (
        "Things I've been holding (my own stakes). "
        + restraint_note
        + "; ".join(lines)
    )
    return SituationFragment(
        source="held_stakes",
        text=text,
        available=True,
        metadata={
            "held_stakes_count": len(stakes),
            "held_stakes_surfaced_recently": surfaced_recently,
            "held_stakes_budget_closed": bool(budget_closed),
        },
    )


def _desktop_senses_on() -> bool:
    """DESKTOP_ACTIVITY_ENABLED (env, default true): the focused-window /
    activity-watcher senses. False on hosts with no desktop to sense."""
    try:
        from core.config import config as _cfg
        return bool(getattr(_cfg, "DESKTOP_ACTIVITY_ENABLED", True))
    except Exception:
        return True


def _lan_presence_on() -> bool:
    """companion.lan_presence (step 23): the house line joins the gathers
    only when the sense is on — unbooted or off is byte-identical."""
    try:
        from core.turn.companion_flags import companion_settings
        comp = companion_settings()
        return bool(comp is not None and getattr(comp, "lan_presence", False))
    except Exception:
        return False


def _machine_presence_on() -> bool:
    """LOCAL_MACHINE_PRESENCE_ENABLED (env, default true): the loginctl /
    lock-idle-active sense. False when the login session is not his."""
    try:
        from core.config import config as _cfg
        return bool(getattr(_cfg, "LOCAL_MACHINE_PRESENCE_ENABLED", True))
    except Exception:
        return True


def _stake_ledger_on() -> bool:
    try:
        from ai.stake_ledger import ledger_enabled

        return ledger_enabled()
    except Exception:
        return False


async def build_situation_context(
    owner_user_id: int,
    recent_history: Optional[list] = None,
    *,
    timeout_seconds: float = 3.0,
) -> SituationContext:
    """Run all sources in parallel with per-source timeout + exception protection."""
    gathers = [
        _safe_gather("time", gather_time_of_day, timeout=timeout_seconds),
        _safe_gather(
            "conversation",
            lambda: gather_conversation_recency(owner_user_id, recent_history),
            timeout=timeout_seconds,
        ),
    ]
    # Host senses (2026-08-18): the desktop and machine-presence sources are
    # registered ONLY when their services are enabled. On a headless host
    # they read terminal idleness and a window that does not exist — she
    # narrated "he's in the unknown folder" for three days from exactly this
    # noise. Off means NOT registered: no "Unavailable senses:" nagging, no
    # sentence for her to wonder about; the prose is byte-identical to a
    # world where the senses never existed.
    if _desktop_senses_on():
        gathers.append(_safe_gather(
            "desktop",
            lambda: gather_desktop_perception(owner_user_id),
            timeout=timeout_seconds,
        ))
    if _machine_presence_on():
        gathers.append(_safe_gather(
            "machine_presence",
            lambda: gather_machine_presence(owner_user_id),
            timeout=timeout_seconds,
        ))
    # Discord presence (2026-08-18): the first real signal of him on this
    # host. Always registered; unknown = an available EMPTY fragment, so a
    # bot that has not reported yet leaves the prose byte-identical.
    gathers.append(_safe_gather(
        "owner_presence",
        gather_owner_presence,
        timeout=timeout_seconds,
    ))
    # The house (step 23, 2026-08-24): registered only while
    # companion.lan_presence is on, so flag-off prose and the source set
    # stay byte-identical to today's.
    if _lan_presence_on():
        gathers.append(_safe_gather(
            "lan_presence",
            gather_lan_presence,
            timeout=timeout_seconds,
        ))
    gathers.append(_safe_gather(
        "operator_agenda",
        lambda: gather_operator_and_agenda(owner_user_id),
        timeout=timeout_seconds,
    ))
    if _desktop_senses_on():
        gathers.append(_safe_gather(
            "recent_activity",
            lambda: gather_recent_activity(owner_user_id),
            timeout=timeout_seconds,
        ))
    # Stake ledger (2026-07-06): included only when the faculty is on, so a
    # disabled flag leaves the assembled prose byte-identical to before.
    if _stake_ledger_on():
        gathers.append(
            _safe_gather(
                "held_stakes",
                lambda: gather_held_stakes(owner_user_id),
                timeout=timeout_seconds,
            )
        )
    fragments = await asyncio.gather(*gathers)

    fragment_list = list(fragments)
    unavailable = [f.source for f in fragment_list if not f.available]
    prose = _assemble_prose(fragment_list)
    snapshot = _assemble_snapshot(fragment_list)
    return SituationContext(
        fragments=fragment_list,
        prose=prose,
        unavailable_sources=unavailable,
        snapshot=snapshot,
    )


def _assemble_snapshot(fragments: List[SituationFragment]) -> Dict[str, Any]:
    """Merge available source metadata into a flat JSON-safe snapshot."""
    snapshot: Dict[str, Any] = {}
    for fragment in fragments:
        if not fragment.available:
            continue
        if not isinstance(fragment.metadata, dict):
            continue
        for key, value in fragment.metadata.items():
            if key:
                snapshot[str(key)] = value
    return snapshot


def _assemble_prose(fragments: List[SituationFragment]) -> str:
    """Concatenate available fragment texts into a paragraph; append
    an 'Unavailable senses:' trailer if any source failed.
    """
    available_texts = [f.text for f in fragments if f.available and f.text]
    body = " ".join(available_texts) if available_texts else "(no situation data available)"

    unavailable = [(f.source, f.reason or "unavailable") for f in fragments if not f.available]
    if not unavailable:
        return body

    trailer_parts = [f"{name} ({reason})" for name, reason in unavailable]
    trailer = "Unavailable senses: " + ", ".join(trailer_parts) + "."
    return f"{body}\n\n{trailer}"
