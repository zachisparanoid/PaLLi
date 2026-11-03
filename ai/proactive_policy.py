from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Awaitable, Callable, Mapping

from core.identity.policy_access import (
    policy_bool,
    policy_effective_permission,
    policy_has_effective_permissions,
    policy_value,
)


logger = logging.getLogger(__name__)

ConnectionFactory = Callable[[], Awaitable[Any]]


@dataclass(frozen=True)
class ProactiveTimingEvaluation:
    allowed: bool
    reason: str
    daily_count: int
    daily_cap: int
    daily_min: int
    daily_min_due: bool
    min_gap_seconds: float


def get_proactive_consent(profile: Mapping[str, Any] | None) -> bool:
    """Check whether the user allows proactive outreach (default off)."""
    prefs = profile.get("preferences", {}) if isinstance(profile, Mapping) else {}

    consent = prefs.get("proactive_consent")
    if isinstance(consent, Mapping):
        enabled = consent.get("enabled")
        if isinstance(enabled, bool):
            return enabled
    if isinstance(consent, bool):
        return consent

    reminder_prefs = prefs.get("reminder_preferences") or {}
    if isinstance(reminder_prefs, Mapping):
        allow_proactive = reminder_prefs.get("allow_proactive_reminders")
        if allow_proactive is False:
            return False

    return False


def allow_proactive_outreach(
    profile: Mapping[str, Any] | None,
    action_policy: Any,
) -> bool:
    if action_policy is None:
        return get_proactive_consent(profile)
    materialized = policy_bool(action_policy, "_materialized", True)
    if not materialized:
        return get_proactive_consent(profile)
    if policy_has_effective_permissions(action_policy):
        return policy_effective_permission(
            action_policy,
            "proactive_outreach",
            default=False,
        )
    if not policy_bool(action_policy, "allow_proactive_followups", False):
        return False
    watch_mode = str(policy_value(action_policy, "background_watch_mode", "off")).strip().lower()
    return watch_mode in {"selected_topics", "enabled"}


async def get_today_proactive_count(
    *,
    user_id: int,
    brain: Any,
    logger: logging.Logger,
    fallback_count: int,
) -> int:
    """Count proactive messages sent in the last 24 hours for this user.

    C1.5: backed by open_threads with source_kind='proactive_record',
    filtered to legacy ``action_type IN ('proactive_message',
    'followup_message')`` via the source_payload->>'action_type' JSON
    extraction. Replaces the legacy
    ``SELECT COUNT(*) FROM proactive_actions WHERE …`` query.
    """
    try:
        today_start = time.time() - (24 * 3600)
        count = await brain.count_open_threads_by_source_kind_since(
            owner_user_id=int(user_id),
            source_kind="proactive_record",
            since=today_start,
            payload_action_types=["proactive_message", "followup_message"],
        )
        return int(count or 0)
    except Exception as exc:
        logger.error("Error counting daily proactive messages: %s", exc)
        return int(fallback_count)


async def check_daily_limits(
    *,
    user_id: int,
    brain: Any,
    logger: logging.Logger,
    max_daily: int,
    fallback_count: int,
) -> bool:
    try:
        count = await get_today_proactive_count(
            user_id=user_id,
            brain=brain,
            logger=logger,
            fallback_count=fallback_count,
        )
        return count < int(max_daily)
    except Exception as exc:
        logger.error("Error checking daily limits: %s", exc)
        return False


async def check_frequency_limits(
    *,
    user_id: int,
    min_gap_seconds: float,
    brain: Any,
    logger: logging.Logger,
) -> bool:
    """Check if enough time has passed since the last proactive message.

    C1.5: backed by open_threads's
    ``max_last_surfaced_open_thread_by_source_kind`` brain wrapper. The
    legacy ``SELECT MAX(executed_time) FROM proactive_actions WHERE …``
    becomes a MAX over ``COALESCE(last_surfaced_at, created_at)`` filtered
    by source_kind + the same action_type whitelist.
    """
    try:
        current_time = time.time()
        last_proactive_time = await brain.max_last_surfaced_open_thread_by_source_kind(
            owner_user_id=int(user_id),
            source_kind="proactive_record",
            since=current_time - (7 * 24 * 3600),
            payload_action_types=["proactive_message", "followup_message"],
        )
        last_proactive_time = float(last_proactive_time or 0.0)
        time_since_last = current_time - last_proactive_time
        return time_since_last >= float(min_gap_seconds)
    except Exception as exc:
        logger.error("Error checking frequency limits: %s", exc)
        return False


def get_user_frequency_settings(
    profile: Mapping[str, Any] | None,
    action_policy: Any,
    *,
    default_max_frequency_hours: float,
    default_daily_cap: int,
    default_daily_min: int,
    emotional_state: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return proactive frequency settings based on user preference."""
    prefs = profile.get("preferences", {}) if isinstance(profile, Mapping) else {}
    freq = str(prefs.get("check_in_frequency") or "normal").strip().lower()

    settings = {
        "testing": {"min_gap_seconds": 5 * 60, "daily_cap": 12, "daily_min": 1},
        "high": {"min_gap_seconds": 4 * 3600, "daily_cap": 5, "daily_min": 1},
        "normal": {"min_gap_seconds": 12 * 3600, "daily_cap": 2, "daily_min": 1},
        "low": {"min_gap_seconds": 36 * 3600, "daily_cap": 1, "daily_min": 0},
    }

    if freq in settings:
        base = {"frequency": freq, **settings[freq]}
    else:
        base = {
            "frequency": freq or "normal",
            "min_gap_seconds": float(default_max_frequency_hours) * 3600,
            "daily_cap": int(default_daily_cap),
            "daily_min": int(default_daily_min),
        }

    if action_policy is None:
        return _apply_emotional_state_frequency_adjustment(base, emotional_state)

    initiative_level = str(policy_value(action_policy, "initiative_level", "medium")).strip().lower()
    followthrough_intensity = str(
        policy_value(action_policy, "followthrough_intensity", "steady")
    ).strip().lower()
    standing_orders_policy = str(
        policy_value(action_policy, "standing_orders_policy", "explicit_only")
    ).strip().lower()

    if initiative_level == "low":
        base["min_gap_seconds"] = max(float(base["min_gap_seconds"]), 24 * 3600)
        base["daily_cap"] = min(int(base["daily_cap"]), 1)
    elif initiative_level == "high":
        base["min_gap_seconds"] = min(float(base["min_gap_seconds"]), 8 * 3600)
        base["daily_cap"] = max(int(base["daily_cap"]), 2)

    if followthrough_intensity == "light":
        base["min_gap_seconds"] = max(float(base["min_gap_seconds"]), 24 * 3600)
        base["daily_cap"] = min(int(base["daily_cap"]), 1)
        base["daily_min"] = 0
    elif followthrough_intensity == "persistent":
        base["min_gap_seconds"] = min(float(base["min_gap_seconds"]), 6 * 3600)
        base["daily_cap"] = max(int(base["daily_cap"]), 3)
        base["daily_min"] = max(int(base["daily_min"]), 1)

    if standing_orders_policy != "enabled":
        base["daily_min"] = 0

    return _apply_emotional_state_frequency_adjustment(base, emotional_state)


def _apply_emotional_state_frequency_adjustment(
    settings: dict[str, Any],
    emotional_state: Mapping[str, Any] | None,
) -> dict[str, Any]:
    if _baseline_stress_high(emotional_state):
        settings["min_gap_seconds"] = max(float(settings["min_gap_seconds"]), 24 * 3600)
        settings["daily_cap"] = min(int(settings["daily_cap"]), 1)
        settings["daily_min"] = 0
    return settings


def _baseline_stress_high(emotional_state: Mapping[str, Any] | None) -> bool:
    if not isinstance(emotional_state, Mapping):
        return False
    try:
        confidence = float(emotional_state.get("confidence") or 0.0)
    except (TypeError, ValueError):
        confidence = 0.0
    if confidence < 0.55:
        return False
    baseline = emotional_state.get("baseline")
    if not isinstance(baseline, Mapping):
        return False
    try:
        stress = float(baseline.get("stress") or 0.0)
    except (TypeError, ValueError):
        stress = 0.0
    return stress >= 0.7


def can_surface_proactive_topic(
    boundary: Mapping[str, Any] | None,
    *,
    reason: str,
) -> bool:
    if not isinstance(boundary, Mapping):
        return True
    state = str(boundary.get("state") or "safe").strip().lower()
    if state == "taboo":
        return reason == "user_reopened"
    if state == "sensitive":
        return reason in {"user_reopened", "direct_answer"}
    if state == "exhausted":
        return reason in {"user_reopened", "required_context"}
    return True


def can_surface_proactive_strategy(strategy: Mapping[str, Any] | None) -> bool:
    if not isinstance(strategy, Mapping):
        return True
    action = str(strategy.get("action") or "").strip().lower()
    return action in {"soft_probe", "resume_thread", "propose_next_step"}


def is_quiet_hours(
    profile: Mapping[str, Any] | None,
    action_policy: Any,
    *,
    now: datetime | None = None,
) -> bool:
    if action_policy is None:
        return False
    mode = str(policy_value(action_policy, "quiet_hours_mode", "adaptive")).strip().lower()
    if mode == "off":
        return False

    reference_time = now if isinstance(now, datetime) else datetime.now().astimezone()
    tz = reference_time.tzinfo or datetime.now().astimezone().tzinfo

    prefs = profile.get("preferences", {}) if isinstance(profile, Mapping) else {}
    tz_info = prefs.get("time_zone")
    tz_name = (
        tz_info.get("value")
        if isinstance(tz_info, Mapping)
        else tz_info
        if isinstance(tz_info, str)
        else None
    )
    if isinstance(tz_name, str) and tz_name.strip():
        try:
            import pytz

            tz = pytz.timezone(tz_name.strip())
        except Exception:
            pass

    if tz is not None and reference_time.tzinfo is not None:
        current_hour = reference_time.astimezone(tz).hour
    else:
        current_hour = reference_time.hour
    quiet_start, quiet_end = (22, 9) if mode == "gentle" else (23, 8)
    return current_hour >= quiet_start or current_hour < quiet_end


async def outreach_allowed(*, owner_id: int) -> bool:
    """Check whether proactive outreach is permitted right now for this owner.

    `core/turn/proactive.py:_outreach_allowed` does
    `from ai.proactive_policy import outreach_allowed` and falls back to
    True on ImportError. This function was missing — the silent fallback
    bypassed every quiet-hours, daily-cap, and frequency-limit check
    defined in this module. Adding it restores real policy enforcement.

    Loads the owner's profile + compiled action policy and delegates to
    `evaluate_outreach_timing`. Returns False on any internal error
    (safer than True; a missing-policy fail-open created the original
    proactive-spam class of bugs).
    """
    from core.config import config as palli_config
    from core.database import brain

    profile: Mapping[str, Any] | None = None
    for platform in ("discord", "dashboard", "owner"):
        try:
            row = await brain.get_user_profile(int(owner_id), platform)
        except Exception:
            row = None
        if row:
            profile = row
            break

    action_policy: Any = None
    try:
        from core.identity.service import identity_service  # type: ignore[import]
        compiled = identity_service.compile_for_owner(owner_id=int(owner_id))
        if compiled is not None:
            action_policy = getattr(compiled, "action_policy", None)
    except Exception:
        action_policy = None

    try:
        evaluation = await evaluate_outreach_timing(
            user_id=int(owner_id),
            profile=profile,
            action_policy=action_policy,
            brain=brain,
            logger=logger,
            default_max_frequency_hours=float(
                getattr(palli_config, "PROACTIVE_MAX_FREQUENCY_HOURS", 12.0)
            ),
            default_daily_cap=int(
                getattr(palli_config, "PROACTIVE_DAILY_CAP", 2)
            ),
            default_daily_min=int(
                getattr(palli_config, "PROACTIVE_DAILY_MIN", 0)
            ),
        )
    except Exception as exc:
        logger.warning("outreach_allowed: evaluate_outreach_timing failed: %s", exc)
        return False

    if not evaluation.allowed:
        logger.info(
            "proactive: outreach_blocked owner=%s reason=%s daily=%s/%s",
            owner_id, evaluation.reason, evaluation.daily_count, evaluation.daily_cap,
        )
    return evaluation.allowed


async def evaluate_outreach_timing(
    *,
    user_id: int,
    profile: Mapping[str, Any] | None,
    action_policy: Any,
    brain: Any,
    logger: logging.Logger,
    default_max_frequency_hours: float,
    default_daily_cap: int,
    default_daily_min: int,
    now: datetime | None = None,
    emotional_state: Mapping[str, Any] | None = None,
) -> ProactiveTimingEvaluation:
    frequency_settings = get_user_frequency_settings(
        profile,
        action_policy,
        default_max_frequency_hours=default_max_frequency_hours,
        default_daily_cap=default_daily_cap,
        default_daily_min=default_daily_min,
        emotional_state=emotional_state,
    )
    daily_cap = int(frequency_settings["daily_cap"])
    daily_min = int(frequency_settings["daily_min"])
    min_gap_seconds = float(frequency_settings["min_gap_seconds"])

    if not allow_proactive_outreach(profile, action_policy):
        return ProactiveTimingEvaluation(
            allowed=False,
            reason="action_policy_block",
            daily_count=0,
            daily_cap=daily_cap,
            daily_min=daily_min,
            daily_min_due=False,
            min_gap_seconds=min_gap_seconds,
        )

    if is_quiet_hours(profile, action_policy, now=now):
        return ProactiveTimingEvaluation(
            allowed=False,
            reason="quiet_hours",
            daily_count=0,
            daily_cap=daily_cap,
            daily_min=daily_min,
            daily_min_due=False,
            min_gap_seconds=min_gap_seconds,
        )

    daily_count = await get_today_proactive_count(
        user_id=user_id,
        brain=brain,
        logger=logger,
        fallback_count=default_daily_cap,
    )
    daily_min_due = daily_min > 0 and daily_count < daily_min

    if daily_count >= daily_cap:
        return ProactiveTimingEvaluation(
            allowed=False,
            reason="daily_cap_reached",
            daily_count=daily_count,
            daily_cap=daily_cap,
            daily_min=daily_min,
            daily_min_due=daily_min_due,
            min_gap_seconds=min_gap_seconds,
        )

    if not await check_frequency_limits(
        user_id=user_id,
        min_gap_seconds=min_gap_seconds,
        brain=brain,
        logger=logger,
    ):
        return ProactiveTimingEvaluation(
            allowed=False,
            reason="frequency_limit",
            daily_count=daily_count,
            daily_cap=daily_cap,
            daily_min=daily_min,
            daily_min_due=daily_min_due,
            min_gap_seconds=min_gap_seconds,
        )

    return ProactiveTimingEvaluation(
        allowed=True,
        reason="allowed",
        daily_count=daily_count,
        daily_cap=daily_cap,
        daily_min=daily_min,
        daily_min_due=daily_min_due,
        min_gap_seconds=min_gap_seconds,
    )
