"""The outreach residuals + the arbiter's delivery seams (step 19, 2026-08-19).

This module USED to be the pre-arbiter proactive lane: a 15-minute loop
(``start_proactive_loop`` → ``consider_outreach``) whose ``decide_surface``
pass picked an open thread and synthesized a message. Step 18 (the arbiter,
2026-08-06) made the moment's appraisal + the one gated rail own all
self-initiated speech; the lane stood down under the flag and step 19
deleted it outright (spec: docs/superpowers/specs/2026-08-19-surprise-
organ-design.md §7). What remains, and why:

- ``run_outreach_residuals`` — the old tick's three residual jobs
  (stake-horizon threads, the arbiter's daily expiry sweep, the
  monologue-candidate rail), now riding the moment engine's loop.
- ``_pick_proactive_surface`` / ``_pick_proactive_channel_id`` /
  ``_safe_last_user_message_at`` — the arbiter's (and workbench's, and her
  hands') delivery seams. The ``_channel_id`` one is the Discord-only
  wrapper, kept for ``_deliver_proactive_to_discord`` and the tests that
  patch it; the arbiter moved to the cross-door ``_surface`` one when the
  Telegram door opened (2026-09-14, task 2).
- ``consider_proactive_action`` + ``_deliver_proactive`` — the
  action-candidate path, still gated by ``features.proactive_actions``
  (owner's separate deliberate door) and the outreach policy caps.

Surface selection (2026-05-06; cross-door since 2026-09-14; private chats
only since 2026-10-13): the most recent PRIVATE chat the owner has had with
her on any door she has — a Discord DM, a Telegram private chat or the phone
app, never a server channel or a group (the owner's ``policy.proactive.door``
setting, when he names a door, is asked first); PROACTIVE_ALLOWED_CHANNEL_IDS
is a strict allowlist over DISCORD rows; no usable surface means no delivery,
never a random one and never a group.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time as _time
from collections import OrderedDict
from datetime import datetime
from typing import Any

from core.agent.dispatch import dispatch_turn
from core.config import config
from core.database import brain
from core.doors import CHAT_DOORS
from core.setup_mode import lane_idle
from core.turn.types import Turn, TurnMetadata


# 2026-05-06: in-memory ring of recently-fired candidate norms. Acts as
# a fallback rumination guard when the unified open_threads dedup
# (source_kind='proactive_record', see C1.5 / B5 Decision 5) can't
# reach — the row is only persisted on successful delivery, but a
# candidate that fires the synthesized turn AND fails to deliver should
# still count as 'this hunch was tried'.
# Keyed by candidate norm → fired_at timestamp; trimmed by age.
_RECENTLY_FIRED_NORMS: "OrderedDict[str, float]" = OrderedDict()
_RECENTLY_FIRED_MAX_ENTRIES = 100
_RECENTLY_FIRED_KEEP_SECONDS = 6 * 3600  # 6h matches default staleness window

logger = logging.getLogger(__name__)


async def _maybe_run_arbiter_sweep(*, owner_user_id: int) -> None:
    """Expiry sweep host (arbiter spec §7): rode the old 15-min tick; since
    step 19 it rides run_outreach_residuals on the moment engine's loop —
    still no clock of its own. Never raises; no-op when the flag is off."""
    try:
        from ai.arbiter import maybe_run_daily_sweep

        await maybe_run_daily_sweep(owner_user_id=owner_user_id)
    except Exception as exc:
        logger.warning("arbiter sweep host: %s", exc)


async def run_outreach_residuals(*, owner_id: int = 1) -> None:
    """Step 19 (2026-08-19): the retired 15-min loop's three residual jobs,
    now riding the moment engine's loop (its cycles + flag-off heartbeats —
    the old tick's cadence class). Each job individually guarded: one
    failure never costs the others. Reminders: not here, untouchable,
    forever. A fourth since 2026-10-13: the group openers (the presence
    slice, item 4)."""
    for name, job in (
        ("stake_threads", lambda: _ensure_stake_threads(owner_id=owner_id)),
        ("arbiter_sweep", lambda: _maybe_run_arbiter_sweep(owner_user_id=owner_id)),
        ("candidate_rail", lambda: _consider_candidate_rail(owner_id=owner_id)),
        ("group_openers", lambda: _run_group_openers(owner_id=owner_id)),
    ):
        try:
            await job()
        except Exception as exc:
            logger.warning("outreach residuals: %s failed: %s", name, exc)


async def _run_group_openers(*, owner_id: int) -> None:
    """The presence slice, item 4 (2026-10-13): PaLLi starting something in a
    group chat, at most one room a pass (core/turn/group_openers.py)."""
    from core.turn import group_openers

    await group_openers.run_pass(owner_id=owner_id)


async def _ensure_stake_threads(*, owner_id: int) -> None:
    """Queue at most ONE horizon-due stake as an open thread (never raises).

    Restraint is code-enforced, not prompt-hoped:
    - global gate: no stake thread while ANY stake was surfaced <24h ago
      (ai.stake_ledger.any_surfaced_within fails CLOSED — quiet on doubt);
    - per-stake gate: 72h cooldown via horizon_due_stakes;
    - one deterministic surfacing per stake lifetime: the source_id is
      stable (``stake:<id>``), so once a thread exists for a stake — active,
      surfaced, or resolved — this path never re-queues it. If a stake stays
      open after its one deterministic raise, only the monologue's organic
      awareness (situation-context fragment) can bring it up again.
    """
    try:
        from ai.stake_ledger import (
            any_surfaced_within,
            horizon_due_stakes,
            ledger_enabled,
        )

        if not ledger_enabled():
            return
        if await any_surfaced_within(24 * 3600):
            return
        due = await horizon_due_stakes(per_stake_cooldown_s=72 * 3600)
        if not due:
            return
        from core.open_threads import ThreadSourceKind, ThreadStatus

        # Single in-flight stake thread: the budget is spent at DELIVERY, so
        # without this gate two due stakes could each queue a thread across
        # ticks and both deliver the same day, 2h apart (review finding). One
        # queued-and-undelivered stake thread at a time, period.
        active_threads = await brain.list_active_open_threads(owner_user_id=int(owner_id))
        for t in active_threads:
            payload = getattr(t, "source_payload", None) or {}
            if isinstance(payload, dict) and payload.get("origin") == "stake_ledger":
                return

        for stake in due:
            source_id = f"stake:{stake.id}"
            existing = await brain.get_open_thread_by_source_id(
                owner_user_id=int(owner_id),
                source_kind=ThreadSourceKind.TOPIC,
                source_id=source_id,
            )
            if existing is not None:
                continue  # already had its one deterministic raise
            # Mechanical case-note register on purpose: this text is stored
            # DATA that the surface synthesizer consumes — ready-made vivid
            # first-person lines here would be lifted verbatim into outreach
            # (the canned-phrase failure). Facts only; the voice happens live.
            horizon_note = ""
            if stake.horizon_at is not None:
                days_past = max(0, int((_time.time() - stake.horizon_at) // 86400))
                horizon_note = (
                    f" Horizon passed {f'{days_past}d ago' if days_past else 'today'}, "
                    "no signal since."
                )
            thread_id = await brain.add_source_open_thread(
                owner_user_id=int(owner_id),
                topic_summary=stake.stake,
                why_it_matters=(
                    f"One of PaLLi's own held stakes ({stake.kind})."
                    f"{horizon_note} Raise once, lightly, in fresh words, "
                    "only if the moment fits."
                ),
                kind="personal",
                urgency="when-the-moment-fits",
                source_kind=ThreadSourceKind.TOPIC,
                status=ThreadStatus.ACTIVE,
                source_id=source_id,
                source_payload={
                    "origin": "stake_ledger",
                    "stake_id": int(stake.id),
                    "stake_kind": stake.kind,
                },
            )
            logger.info(
                "stake_ledger: queued horizon-due stake id=%s as thread %s",
                stake.id,
                thread_id,
            )
            return  # at most one per tick
    except Exception as exc:
        logger.warning("_ensure_stake_threads failed: %s", exc)


async def _consider_candidate_rail(*, owner_id: int) -> bool:
    """Route the freshest unconsumed monologue hunch, if any.

    Observation candidates — and action candidates while
    ``features.proactive_actions`` is off — are ingested into the unified
    ``open_threads`` queue as ACTIVE topic rows with monologue provenance
    in ``source_id``. From there the arbiter's thread-board digest raises
    them alongside every other thread (step 19: decide_surface is gone —
    one ranked queue, one door). A disabled action hunch therefore
    degrades to something worth *saying* instead of doing, rather than
    dying silently.

    Enabled action candidates run ``consider_proactive_action`` (gated by
    the outreach policy caps) and return True — the caller skips the
    open-threads surface pass this tick.

    Every routed candidate writes a consumption record via
    ``brain.create_proactive_action`` whose trigger_reason cites
    ``candidate_tick_timestamp`` (same %.6f form as
    ``ai.proactive_candidate_source._format_tick_timestamp``) and
    ``candidate_text`` — keeping the consumed-check and rumination guard
    working unchanged. Never raises; failures fall through to the
    surface pass. With ``features.proactive_conversations`` off it routes
    nothing (2026-10-14).
    """
    from core.turn.companion_flags import speaks_first_on

    if not speaks_first_on():
        return False
    try:
        candidate = await _load_unconsumed_candidate(owner_id=owner_id)
        if candidate is None:
            return False
        kind = str(getattr(candidate, "kind", "") or "observation").strip().lower()
        text = str(getattr(candidate, "text", "") or "").strip()
        if not text:
            return False

        actions_enabled = False
        try:
            from core.settings_boot import settings_service
            snap = settings_service.snapshot()
            actions_enabled = bool(snap and snap.features.proactive_actions)
        except Exception:
            actions_enabled = False

        if kind == "action" and actions_enabled:
            if lane_idle("proactive action"):
                # Setup mode (first door, 2026-09-17): the action's report is
                # voiced through dispatch_turn, and with no brain that answers
                # with the setup sentence — which would then be SENT as her
                # outreach. Left unconsumed for a later tick, like the policy
                # block below. An observation hunch still files its thread:
                # that path has no LLM in it.
                return False
            if not await _outreach_allowed(owner_id=owner_id):
                logger.debug(
                    "candidate rail: action blocked by outreach policy; "
                    "leaving candidate unconsumed for a later tick",
                )
                return False
            try:
                await consider_proactive_action(owner_id=owner_id, candidate=candidate)
            finally:
                # Consume even on failure — "this hunch was tried" (matches
                # the in-memory dedup ring's intent for failed deliveries).
                await _record_candidate_consumption(
                    owner_id=owner_id,
                    candidate=candidate,
                    routed="action",
                    action_type="proactive_message",
                )
            return True

        thread_kind = "followup" if kind == "action" else "personal"
        from core.open_threads import ThreadSourceKind, ThreadStatus
        thread_id = await brain.add_source_open_thread(
            owner_user_id=int(owner_id),
            topic_summary=text,
            why_it_matters=(
                "A hunch from my own background thinking — worth raising "
                "when the moment fits."
            ),
            kind=thread_kind,
            urgency="when-the-moment-fits",
            source_kind=ThreadSourceKind.TOPIC,
            status=ThreadStatus.ACTIVE,
            source_id=f"monologue:{float(getattr(candidate, 'timestamp', 0.0) or 0.0):.6f}",
            source_payload={
                "origin": "inner_monologue",
                "candidate_kind": kind,
                "candidate_action_hint": str(getattr(candidate, "action_hint", "") or ""),
            },
        )
        await _record_candidate_consumption(
            owner_id=owner_id,
            candidate=candidate,
            routed="open_thread",
            action_type="candidate_ingested",
            thread_id=thread_id,
        )
        logger.info(
            "candidate rail: monologue hunch ingested as open thread %s (%r)",
            thread_id, text[:80],
        )
        return False
    except Exception as exc:
        logger.warning(
            "candidate rail failed; continuing to open-threads surface: %s", exc,
        )
        return False


async def _record_candidate_consumption(
    *,
    owner_id: int,
    candidate: Any,
    routed: str,
    action_type: str,
    thread_id: str | None = None,
) -> None:
    """Mark a monologue tick consumed (proactive_record row).

    ``action_type='proactive_message'`` rows count against the daily cap
    in ``ai/proactive_policy``; ``'candidate_ingested'`` rows do not —
    ingestion isn't a send, the eventual raise rides the arbiter's own
    gates (ceiling, freshness, quiet).
    """
    tick_ts = float(getattr(candidate, "timestamp", 0.0) or 0.0)
    text = str(getattr(candidate, "text", "") or "")
    now = _time.time()
    trigger: dict[str, Any] = {
        "candidate_tick_timestamp": f"{tick_ts:.6f}",
        "candidate_text": text,
        "routed": routed,
    }
    if thread_id:
        trigger["open_thread_id"] = str(thread_id)
    await brain.create_proactive_action(
        user_id=int(owner_id),
        action_type=action_type,
        content=text,
        trigger_reason=json.dumps(trigger),
        scheduled_time=now,
        executed_time=now,
        success=True,
    )


async def _safe_last_user_message_at(*, owner_id: int) -> float:
    """Best-effort fetch of the unix ts of the user's most recent message.

    Inline implementation: pulls the most-recent ``role='user'`` row
    from the conversations table via ``brain.get_conversation_history``
    (an existing wrapper) and reads its ``timestamp`` field. Returns
    ``0.0`` on any error so the arbiter's conversation-state gate treats
    us as "not active" rather than crashing.
    """
    try:
        # Pull a WINDOW of recent rows, not a single row. The newest row is
        # almost always PaLLi's own reply (she answers last), so limit=1 made
        # the user-row walk below always fall through to 0.0 — which the
        # activity gate read as "user inactive 4h+", suppressing proactive
        # outreach on nearly every 15-min tick regardless of how many threads
        # were open (2026-07-23). No platform filter: the last user message may
        # be on any surface. (If a >50-row assistant/proactive burst with no
        # user reply ever becomes real, switch this to a role='user' query.)
        rows = await brain.get_conversation_history(
            user_id=int(owner_id),
            limit=50,
            include_metadata=True,
        )
    except Exception as exc:
        logger.warning("_safe_last_user_message_at: %s", exc)
        return 0.0

    if not rows:
        return 0.0

    # Walk newest → oldest looking for a user-authored row. The history
    # store typically returns rows in chronological order with the most
    # recent last; reverse-iterate so we pick the latest user message.
    for row in reversed(rows):
        if str(row.get("role") or "").lower() == "user":
            ts = row.get("timestamp")
            if ts is None:
                continue
            try:
                return float(ts)
            except (TypeError, ValueError):
                continue
    return 0.0


async def _transcribe_proactive_utterance(*, owner_id: int, text: str) -> None:
    """Put what she just said, unprompted, into her own transcript.

    Assistant-only: nobody spoke to her, so no user turn is written — which
    is the entire point. The old path appended her framing as role="user"
    and let the reply loop answer it; writing a synthetic user turn here
    would rebuild the exact bug one layer down.

    Reuses the post-hoc transcriber from the steering custody fix (it skips
    an empty user side by design). Session key mirrors runner.py's mapping
    for surface="proactive" so this lands in the same transcript her next
    proactive turn reads.
    """
    import os
    from pathlib import Path

    from core.agent.runner import _open_session_with_exchange

    root = Path(os.environ.get("PALLI_AGENT_SESSIONS_ROOT") or "data/agent_sessions")
    await _open_session_with_exchange(
        f"proactive:{int(owner_id)}", root, "", str(text or "")
    )


async def _deliver_proactive(
    *,
    owner_id: int,
    result: Any,
    platform: str | None = None,
    surface_id: str | None = None,
) -> None:
    """Send the proactive turn's reply to the private chat he used last.

    ``platform`` / ``surface_id`` may be supplied by a caller that already
    picked one, so the record of the message can name the same place it was
    sent to (2026-07-30). Omitted, it picks its own across every door
    (telegram door, 2026-09-14, task 1) — and no eligible door means she
    does not send, never that she guesses (the 2026-05-06 law).
    """
    if platform is None or surface_id is None:
        picked = await _pick_proactive_surface(owner_id=owner_id)
        if picked is None:
            logger.info(
                "_deliver_proactive: no eligible surface (owner_id=%s); "
                "reply was generated but not sent",
                owner_id,
            )
            return
        platform, surface_id = picked
    try:
        from core.channel_runtime import (
            ChannelDeliveryEnvelope,
            channel_runtime,
        )
    except Exception as exc:
        logger.warning(
            "_deliver_proactive: channel_runtime import failed: %s; "
            "proactive reply not delivered",
            exc,
        )
        return

    text = (result.response_text or "").strip()
    if not text:
        return

    try:
        await channel_runtime.send_envelope(
            platform=str(platform),
            surface_id=str(surface_id),
            envelope=ChannelDeliveryEnvelope(text=text),
        )
        logger.info(
            "_deliver_proactive: delivered to %s surface %s "
            "(owner_id=%s, %d chars)",
            platform, surface_id, owner_id, len(text),
        )
    except Exception as exc:
        logger.warning(
            "_deliver_proactive: delivery to %s surface %s failed: %s",
            platform, surface_id, exc,
        )


async def _deliver_proactive_to_discord(
    *, owner_id: int, result: Any, channel_id: int | None = None,
) -> None:
    """The Discord-only wrapper, kept because callers name it and tests
    patch ``_pick_proactive_channel_id`` under it. Reads that name as a
    MODULE attribute so the patch takes."""
    if channel_id is None:
        channel_id = await _pick_proactive_channel_id(owner_id=owner_id)
    if channel_id is None:
        logger.info(
            "_deliver_proactive_to_discord: no eligible channel "
            "(owner_id=%s); reply was generated but not sent",
            owner_id,
        )
        return
    await _deliver_proactive(
        owner_id=owner_id, result=result,
        platform="discord", surface_id=str(channel_id),
    )


async def _pick_proactive_surface(
    *, owner_id: int, platforms: tuple[str, ...] = CHAT_DOORS,
) -> tuple[str, str] | None:
    """Pick WHERE her own message to the owner goes, as ``(platform, surface_id)``.

    The owner's setting ``policy.proactive.door`` (the mobile door, spec §7,
    2026-10-13) is asked first: a named door among ``platforms`` that is up
    and has a private chat with him wins. Otherwise — the default
    "last_used", a door that is shut, or one with no private chat — the
    private chat he used most recently (``_last_used_private_chat``). Either
    way, only ever a chat shown private with him (the owner, 10-13)."""
    preferred = await _preferred_door(owner_id=owner_id, platforms=platforms)
    if preferred is not None:
        return preferred
    return await _last_used_private_chat(owner_id=owner_id, platforms=platforms)


async def _preferred_door(*, owner_id: int, platforms: tuple[str, ...]) -> tuple[str, str] | None:
    from core import doors as _doors
    from core.channel_runtime import channel_runtime

    door = _doors.proactive_door_setting()
    if door == "last_used" or door not in platforms:
        return None
    if door == "mobile":
        # The phone app is his alone: private from the moment a phone is
        # paired, before he has written a word there — when the door is up
        # and a phone is paired (core/doors.py::phone_reachable).
        return ("mobile", "1") if await _doors.phone_reachable(owner_id) else None
    if not await channel_runtime.door_ready(door):
        return None
    return await _last_used_private_chat(owner_id=owner_id, platforms=(door,))


async def _last_used_private_chat(
    *, owner_id: int, platforms: tuple[str, ...] = CHAT_DOORS,
) -> tuple[str, str] | None:
    """The private chat with him he used most recently across the named
    doors, as ``(platform, surface_id)``.

    Logic:
    1. If PROACTIVE_ALLOWED_CHANNEL_IDS is set in config, DISCORD rows are
       restricted to it. (Strict allowlist — a Discord channel outside the
       list is never picked.) It is a list of Discord channel ids and was
       never meant to judge another door's rooms, so it does not. It only
       narrows: a group in it is still never picked.
    2. Of the chats left, newest first, the first one shown to be a private
       chat with him wins (``core/doors.py::is_private_chat``): a Discord DM,
       a Telegram private chat or the phone app (his alone, channel 1; the
       mobile door, 2026-10-13). A server channel or a Telegram group never
       does (the owner, 2026-10-13: on 10-12 at 08:10 a note meant for him
       went to a server channel his friends could see), and neither does a
       chat whose privacy cannot be shown.
    3. No private chat → None. Never a room picked at random (2026-05-06),
       never a group.

    Each of his rows from a Discord or Telegram chat says whether that chat
    is private (``metadata.is_dm``, written by core/turn/persistence.py since
    10-13). For a chat whose rows predate the mark, a Telegram chat is shown
    private by its id and a Discord one by asking the Discord door whether it
    is his DM, so right after the restart that brings the mark in his DM is
    still found.

    Only HIS OWN WORDS IN A TEXT CHAT count (fix plan 5.8, round 1). Her own
    rows are filed under his id too: her send to a friend (her hands'
    send_message → record_outbound) is an assistant row under his id with
    the FRIEND's channel, and was picked as "his" last chat. So only
    ``role = 'user'`` rows count. And his voice turns are filed as
    "discord" with the voice channel, so a row marked as spoken is skipped:
    ``metadata.surface`` a voice surface (core/turn/persistence.py marks
    them), or ``metadata.voice_session`` (the voice runtime's own row).
    Voice rows written before the mark existed carry nothing to tell them
    apart; they carry no private mark either, and a voice channel is never
    his DM, so the Discord door never shows one private.
    """
    try:
        import sqlite3

        from core.config import config as _cfg
    except Exception:
        return None

    platforms = tuple(str(p) for p in platforms if str(p or "").strip())
    if not platforms:
        return None

    allowed_channels = list(getattr(_cfg, "PROACTIVE_ALLOWED_CHANNEL_IDS", []) or [])

    db_path = str(getattr(_cfg, "DATABASE_PATH", "data/palli-local.sqlite3") or "")
    if not db_path:
        return None
    try:
        from pathlib import Path

        if not Path(db_path).is_absolute():
            db_path = str(Path(__file__).resolve().parents[2] / db_path)
    except Exception:
        pass

    # The private mark on a row, read only when its metadata is valid JSON
    # (CASE keeps json_type off a value that is not): "true" when the row says
    # its chat is private with him, any other value when it says otherwise,
    # NULL when the row carries no mark (written before 10-13).
    dm_mark = (
        "(CASE WHEN metadata IS NOT NULL AND json_valid(metadata) "
        "THEN json_type(metadata, '$.is_dm') END)"
    )

    def _query() -> list[tuple[str, int, bool, bool]]:
        try:
            con = sqlite3.connect(db_path)
            cur = con.cursor()
        except Exception:
            return []
        try:
            # Every chat the owner has used on a named door, newest first, with
            # whether any of his rows there says it is private, or says not.
            params: list[Any] = [int(owner_id), *platforms]
            placeholders = ",".join("?" for _ in platforms)
            base_sql = (
                "SELECT platform, channel_id, MAX(timestamp) AS last_at, "
                f"MAX(CASE WHEN {dm_mark} = 'true' THEN 1 ELSE 0 END), "
                f"MAX(CASE WHEN {dm_mark} IS NOT NULL AND {dm_mark} != 'true' "
                "THEN 1 ELSE 0 END) "
                "FROM conversations "
                f"WHERE user_id = ? AND platform IN ({placeholders}) "
                "AND channel_id IS NOT NULL AND channel_id != 0 "
                # His own words only — never her send filed under his id.
                "AND role = 'user' "
                # Never a row marked as spoken. CASE keeps json_extract off
                # a metadata value that is not valid JSON.
                "AND NOT (CASE WHEN metadata IS NOT NULL AND json_valid(metadata) THEN "
                "COALESCE(json_extract(metadata, '$.surface') "
                "IN ('discord_voice', 'voice_local'), 0) "
                "OR COALESCE(json_extract(metadata, '$.voice_session'), 0) = 1 "
                "ELSE 0 END) "
            )
            if allowed_channels and "discord" in platforms:
                channel_placeholders = ",".join("?" for _ in allowed_channels)
                base_sql += (
                    "AND (platform != 'discord' OR channel_id IN "
                    f"({channel_placeholders})) "
                )
                params.extend(int(cid) for cid in allowed_channels)
            base_sql += "GROUP BY platform, channel_id ORDER BY last_at DESC"
            cur.execute(base_sql, params)
            return [
                (str(row[0]), row[1], bool(row[3]), bool(row[4]))
                for row in cur.fetchall() if row and row[1]
            ]
        except Exception as exc:
            logger.debug("proactive surface query failed: %s", exc)
        finally:
            con.close()
        return []

    from core import doors as _doors

    chats = await asyncio.to_thread(_query)
    phone: bool | None = None
    for platform, channel_id, marked_private, marked_group in chats:
        if platform == "mobile":
            # His phone rows outlive his phones: with the door shut or no
            # phone paired, the phone is not where he is (review I1), so the
            # chat he used before it is asked. Asked once per scan.
            if phone is None:
                phone = await _doors.phone_reachable(owner_id)
            if not phone:
                continue
        if await _doors.is_private_chat(platform, channel_id, marked_private=marked_private,
                                        marked_group=marked_group):
            return (platform, str(channel_id))
    if chats:
        logger.debug(
            "proactive surface: none of the owner's %d chats is shown to be a "
            "private chat with him; nothing picked (owner_id=%s)",
            len(chats), owner_id,
        )
    return None


async def _pick_proactive_channel_id(*, owner_id: int) -> int | None:
    """The Discord-only wrapper over ``_pick_proactive_surface``: his DM with
    her, never a server channel. ai/arbiter.py calls it for a Discord channel
    id; a Telegram row must never come back through it."""
    picked = await _pick_proactive_surface(owner_id=owner_id, platforms=("discord",))
    if picked is None:
        return None
    try:
        return int(picked[1])
    except (TypeError, ValueError):
        return None


def _candidate_norm_for_inmemory(text: str) -> str:
    """Mirror of proactive_candidate_source._candidate_norm so the
    in-memory dedup uses the same normalization as the DB dedup."""
    try:
        from ai.proactive_candidate_source import _candidate_norm

        return _candidate_norm(text)
    except Exception:
        return (text or "").strip().lower()


def _trim_recently_fired() -> None:
    """Drop entries older than _RECENTLY_FIRED_KEEP_SECONDS, then cap size."""
    cutoff = _time.time() - _RECENTLY_FIRED_KEEP_SECONDS
    stale = [k for k, ts in _RECENTLY_FIRED_NORMS.items() if ts < cutoff]
    for k in stale:
        del _RECENTLY_FIRED_NORMS[k]
    while len(_RECENTLY_FIRED_NORMS) > _RECENTLY_FIRED_MAX_ENTRIES:
        _RECENTLY_FIRED_NORMS.popitem(last=False)


async def consider_proactive_action(*, owner_id: int, candidate: Any) -> None:
    """Run an autonomous action suggested by the inner monologue.

    Honors the user's autonomy policy via the underlying tool's safety
    classifier (local_shell rejects destructive commands; read_file
    rejects out-of-scope paths). When the action succeeds and produces
    output worth surfacing, synthesizes a brief Discord/voice report
    via dispatch_turn (routed through pipeline or ReAct per
    PALLI_AGENT_LOOP) so the user knows what happened.

    Failure modes (refused, error, empty output) are also surfaced as
    short turns rather than silently swallowed — Jarvis tells you when
    he tried something and couldn't.
    """
    from core.turn.local_read_file import execute_local_read_file
    from core.turn.local_shell import execute_local_shell

    hint = (str(getattr(candidate, "action_hint", "") or "")).strip()
    fallback_text = str(getattr(candidate, "text", "") or "").strip()
    payload = hint or fallback_text
    if not payload:
        logger.info("consider_proactive_action: empty hint; nothing to do")
        return

    # Decide which tool. If the hint looks file-shaped (path with /, \, or .ext),
    # default to read_file. Otherwise default to local_shell.
    use_read_file = False
    lowered = payload.lower()
    if (
        lowered.startswith(("read ", "show ", "open ", "look at "))
        or "/" in payload
        or "\\" in payload
        or any(payload.endswith(ext) for ext in (".py", ".md", ".txt", ".json", ".yaml", ".yml", ".log", ".env", ".cfg", ".ini", ".toml"))
    ):
        use_read_file = True

    summary_payload: str = ""
    success: bool
    if use_read_file:
        path = payload
        for prefix in ("read ", "show ", "open ", "look at "):
            if path.lower().startswith(prefix):
                path = path[len(prefix):].strip()
                break
        result = await asyncio.to_thread(execute_local_read_file, path=path)
        success = result.allowed and not result.is_binary
        head = result.to_text_for_voicer().splitlines()
        # Cap surfaced output at a few lines for the proactive report.
        summary_payload = "\n".join(head[:6]) + ("\n…" if len(head) > 6 else "")
    else:
        result = await execute_local_shell(
            command=payload,
            owner_user_id=int(owner_id),
        )
        success = result.allowed and result.return_code == 0
        head = result.to_text_for_voicer().splitlines()
        summary_payload = "\n".join(head[:8]) + ("\n…" if len(head) > 8 else "")

    if not summary_payload.strip():
        logger.info("consider_proactive_action: empty result; not surfacing to user")
        return

    # Synthesize a proactive Turn whose CONTENT already contains the
    # tool result. respond_tier0 will voice it back in PaLLi's voice
    # (e.g. "I went and ran git status while you were away — ...").
    proactive_payload = (
        "[autonomous_action_report]\n"
        f"observation_seed: {fallback_text or '(none)'}\n"
        f"action_taken: {hint or payload}\n"
        f"tool: {'read_file' if use_read_file else 'local_shell'}\n"
        f"success: {success}\n"
        f"result:\n{summary_payload}"
    )
    synthesized = Turn(
        surface="proactive",
        owner_id=owner_id,
        content=proactive_payload,
        metadata=TurnMetadata(
            origin="proactive_action",
            candidate_tick=_candidate_timestamp(candidate),
        ),
    )
    try:
        # D.4 step 4b: dispatch_turn honors PALLI_AGENT_LOOP and falls back
        # to process_turn in pipeline mode — same TurnResult shape either way.
        result = await dispatch_turn(synthesized)
    except Exception as exc:
        logger.warning("consider_proactive_action: dispatch_turn raised: %s", exc)
        return

    # Same delivery gate the retired surfacing lane used. Without this the LLM
    # voices the action-report just fine but it never reaches Discord —
    # the user sees an autonomous read happen with no feedback.
    if (
        result
        and result.responded
        and (result.response_text or result.attachments)
        and bool(getattr(config, "PROACTIVE_TURN_LOOP_DELIVERY_ENABLED", True))
    ):
        await _deliver_proactive(
            owner_id=owner_id,
            result=result,
        )


def _candidate_timestamp(candidate: Any) -> datetime | None:
    ts = getattr(candidate, "timestamp", None)
    if ts is None:
        return None
    try:
        return datetime.fromtimestamp(float(ts))
    except (TypeError, ValueError):
        return None


async def _load_unconsumed_candidate(*, owner_id: int) -> Any:
    """Best-effort fetch of the latest unused proactive candidate.

    Reads the monologue-fed candidate source built in Phase 4. Logs at
    WARNING (not DEBUG) when the call fails so silent regressions are
    visible in the daily log.
    """
    try:
        from ai.proactive_candidate_source import get_eligible_candidate
        return await get_eligible_candidate(owner_user_id=owner_id)
    except Exception as exc:
        logger.warning("candidate rail: candidate load failed: %s", exc)
        return None


async def _outreach_allowed(*, owner_id: int) -> bool:
    """Check that policy permits outreach right now.

    Fails CLOSED: if the policy module can't be imported or raises, the
    daily-cap/quiet-hours limits are unenforceable, so outreach is
    blocked rather than uncapped — a broken policy module silences
    outreach instead of removing the spam guard it exists to provide.
    The warning names the failure so the regression is visible in the
    daily log.
    """
    try:
        from ai.proactive_policy import outreach_allowed  # type: ignore[attr-defined]
        result = outreach_allowed(owner_id=owner_id)
        if hasattr(result, "__await__"):
            result = await result  # type: ignore[misc]
        return bool(result)
    except Exception as exc:
        logger.warning(
            "_outreach_allowed: ai.proactive_policy unavailable or raised "
            "(%s: %s); failing closed — outreach blocked", type(exc).__name__, exc,
        )
        return False
