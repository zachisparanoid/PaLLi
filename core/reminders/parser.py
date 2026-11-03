"""Canonical home for natural-language reminder time parsing.

Promoted from ``ai/reminder_time_parser.py`` in C1.1 of the Phase C
deletion sweep. Until C1.4 rewires the remaining consumers and C1.6
deletes the shim, ``ai.reminder_time_parser`` re-exports from here.

The parser handles relative durations ("in 30 minutes"), natural-language
phrases ("tonight", "tomorrow morning"), explicit clock readings with or
without AM/PM, weekday references ("next Monday at 7pm"), and a
``dateutil`` fuzzy-parse fallback. Ambiguous bare hours and past-time
inputs surface explicit ``reason_code`` values so the caller can ask a
follow-up question rather than silently scheduling the wrong time.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone, tzinfo
from typing import Any, Awaitable, Callable, Dict, NamedTuple, Optional, Type

from dateutil import parser as date_parser

logger = logging.getLogger(__name__)

ResolveUserTimezone = Callable[[int, str], Awaitable[Optional[tzinfo]]]
DateTimeClass = Type[datetime]

_CLOCK_AMPM_RE = re.compile(
    r"\b(?P<hour>\d{1,2})(?::(?P<minute>\d{2}))?(?::(?P<second>\d{2}))?\s*(?P<ampm>am|pm)\b"
)
_CLOCK_24H_RE = re.compile(r"\b(?P<hour>\d{1,2}):(?P<minute>\d{2})(?::(?P<second>\d{2}))?\b")
_CLOCK_BARE_HOUR_RE = re.compile(r"\b(?P<hour>\d{1,2})\b")
_RELATIVE_DURATION_RE = re.compile(
    r"\b(?:in|for)?\s*(?P<quantity>(?:\d+(?:\.\d+)?(?:\s+and\s+a\s+(?:half|quarter))?)|(?:[a-z][a-z\s-]*?))\s+"
    r"(?P<unit>second|seconds|sec|secs|minute|minutes|min|mins|hour|hours|hr|hrs|day|days|week|weeks)\b"
)
_LONG_WAIT_UNIT_SECONDS = {
    "second": 1.0,
    "seconds": 1.0,
    "sec": 1.0,
    "secs": 1.0,
    "minute": 60.0,
    "minutes": 60.0,
    "min": 60.0,
    "mins": 60.0,
    "hour": 3600.0,
    "hours": 3600.0,
    "hr": 3600.0,
    "hrs": 3600.0,
    "day": 86400.0,
    "days": 86400.0,
    "week": 604800.0,
    "weeks": 604800.0,
}
_WORD_NUMBER_UNITS = {
    "zero": 0,
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "thirteen": 13,
    "fourteen": 14,
    "fifteen": 15,
    "sixteen": 16,
    "seventeen": 17,
    "eighteen": 18,
    "nineteen": 19,
}
_WORD_NUMBER_TENS = {
    "twenty": 20,
    "thirty": 30,
    "forty": 40,
    "fifty": 50,
    "sixty": 60,
    "seventy": 70,
    "eighty": 80,
    "ninety": 90,
}


class ReminderTimeParseResult(NamedTuple):
    """Structured result for reminder time parsing."""

    status: str
    timestamp: Optional[float]
    reason_code: str


class ReminderTimeParser:
    """Pure-ish helper for reminder time parsing and clarification text."""

    def parse_result(
        self,
        *,
        status: str,
        reason_code: str = "",
        timestamp: Optional[float] = None,
    ) -> ReminderTimeParseResult:
        return ReminderTimeParseResult(
            status=str(status),
            timestamp=float(timestamp) if timestamp is not None else None,
            reason_code=str(reason_code or ""),
        )

    def default_deferred_content(self, kind: str) -> str:
        normalized_kind = str(kind or "reminder").strip().lower()
        if normalized_kind == "timer":
            return "Timer finished."
        if normalized_kind == "alarm":
            return "Alarm time."
        return "Reminder"

    def format_duration_words(self, seconds: float) -> str:
        remaining = max(int(round(seconds)), 1)
        parts: list[str] = []
        hours, remaining = divmod(remaining, 3600)
        minutes, remaining = divmod(remaining, 60)
        if hours:
            parts.append(f"{hours} hour{'s' if hours != 1 else ''}")
        if minutes:
            parts.append(f"{minutes} minute{'s' if minutes != 1 else ''}")
        if remaining and not hours:
            parts.append(f"{remaining} second{'s' if remaining != 1 else ''}")
        return " ".join(parts) if parts else "0 seconds"

    def parse_spoken_number(self, text: str) -> Optional[float]:
        value = " ".join(str(text or "").strip().lower().replace("-", " ").split())
        if not value:
            return None
        if re.fullmatch(r"\d+(?:\.\d+)?", value):
            return float(value)
        if value in {"half", "a half"}:
            return 0.5
        if value in {"quarter", "a quarter"}:
            return 0.25
        if value in {"a", "an"}:
            return 1.0

        half_match = re.fullmatch(r"(.+?)\s+and\s+a\s+half", value)
        if half_match:
            base = self.parse_spoken_number(half_match.group(1))
            if base is not None:
                return base + 0.5

        quarter_match = re.fullmatch(r"(.+?)\s+and\s+a\s+quarter", value)
        if quarter_match:
            base = self.parse_spoken_number(quarter_match.group(1))
            if base is not None:
                return base + 0.25

        tokens = [token for token in value.split() if token not in {"and"}]
        total = 0
        current = 0
        for token in tokens:
            if token in {"a", "an"}:
                current += 1
            elif token in _WORD_NUMBER_UNITS:
                current += _WORD_NUMBER_UNITS[token]
            elif token in _WORD_NUMBER_TENS:
                current += _WORD_NUMBER_TENS[token]
            elif token == "hundred":
                current = max(current, 1) * 100
            else:
                return None
        total += current
        return float(total) if total or value in {"zero"} else None

    def parse_relative_duration_seconds(self, text: str) -> Optional[float]:
        value = " ".join(str(text or "").strip().lower().replace("-", " ").split())
        if not value:
            return None

        special_cases = (
            (r"\bhalf\s+an?\s+hour\b", 1800.0),
            (r"\ban?\s+hour\s+and\s+a\s+half\b", 5400.0),
            (r"\ban?\s+hour\s+and\s+a\s+quarter\b", 4500.0),
        )
        for pattern, seconds in special_cases:
            if re.search(pattern, value):
                return seconds

        match = _RELATIVE_DURATION_RE.search(value)
        if not match:
            return None
        quantity = self.parse_spoken_number(match.group("quantity"))
        if quantity is None or quantity <= 0:
            return None
        unit = str(match.group("unit") or "").strip().lower()
        multiplier = _LONG_WAIT_UNIT_SECONDS.get(unit)
        if multiplier is None:
            return None
        return float(quantity * multiplier)

    def extract_clock_components(self, text: str) -> Optional[Dict[str, Any]]:
        """Extract a clock time from text without inheriting the current clock."""
        value = (text or "").strip().lower()
        if not value:
            return None

        ampm_match = _CLOCK_AMPM_RE.search(value)
        if ampm_match:
            hour = int(ampm_match.group("hour"))
            minute = int(ampm_match.group("minute") or 0)
            second = int(ampm_match.group("second") or 0)
            ampm = ampm_match.group("ampm")
            if hour < 1 or hour > 12 or minute > 59 or second > 59:
                return None
            if ampm == "am":
                hour = 0 if hour == 12 else hour
            else:
                hour = 12 if hour == 12 else hour + 12
            return {
                "kind": "explicit",
                "hour": hour,
                "minute": minute,
                "second": second,
            }

        h24_match = _CLOCK_24H_RE.search(value)
        if h24_match:
            hour = int(h24_match.group("hour"))
            minute = int(h24_match.group("minute") or 0)
            second = int(h24_match.group("second") or 0)
            if hour > 23 or minute > 59 or second > 59:
                return None
            if hour > 12:
                return {
                    "kind": "explicit",
                    "hour": hour,
                    "minute": minute,
                    "second": second,
                }
            return {
                "kind": "ambiguous",
                "hour": hour,
                "minute": minute,
                "second": second,
            }

        bare_match = _CLOCK_BARE_HOUR_RE.fullmatch(value.replace("at ", "", 1).strip())
        if bare_match:
            hour = int(bare_match.group("hour"))
            if hour > 23:
                return None
            if hour > 12:
                return {
                    "kind": "explicit",
                    "hour": hour,
                    "minute": 0,
                    "second": 0,
                }
            return {
                "kind": "ambiguous",
                "hour": hour,
                "minute": 0,
                "second": 0,
            }

        return None

    def resolve_clock_phrase(self, text: str, *, allow_bare_search: bool = False) -> Optional[Dict[str, Any]]:
        value = (text or "").strip().lower()
        if not value:
            return None

        direct = self.extract_clock_components(value)
        if direct is not None:
            return direct

        if allow_bare_search and " at " in f" {value} ":
            trailing = value.split(" at ")[-1].strip()
            return self.extract_clock_components(trailing)

        return None

    def build_timestamp(
        self,
        *,
        base_date,
        components: Dict[str, Any],
        tz: tzinfo,
        datetime_cls: DateTimeClass = datetime,
    ) -> float:
        naive_datetime = datetime_cls(
            base_date.year,
            base_date.month,
            base_date.day,
            int(components.get("hour") or 0),
            int(components.get("minute") or 0),
            int(components.get("second") or 0),
        )
        return self._attach_timezone(naive_datetime, tz).timestamp()

    @staticmethod
    def _attach_timezone(value: datetime, tz: tzinfo) -> datetime:
        if value.tzinfo is not None:
            try:
                return value.astimezone(tz)
            except Exception:
                return value
        localize = getattr(tz, "localize", None)
        if callable(localize):
            try:
                return localize(value)
            except Exception:
                pass
        return value.replace(tzinfo=tz)

    def build_time_parse_feedback(
        self,
        *,
        time_expression: str,
        parse_result: ReminderTimeParseResult,
    ) -> str:
        expr = (time_expression or "").strip()
        reason = str(parse_result.reason_code or "")
        if reason == "past_same_day":
            return (
                f'The time "{expr}" looks like it has already passed today. '
                'Say something like "later today at 6pm" or "tomorrow at 2pm".'
            )
        if reason == "ambiguous_bare_hour":
            return (
                f'I could not tell whether "{expr}" is AM or PM. '
                'Try something like "8am" or "8pm".'
            )
        if reason == "missing_time":
            return 'Try including when you want the reminder (e.g., "in 2 hours", "tomorrow at 3pm").'
        return (
            f'I could not understand the time "{expr}". '
            'Try something like "in 30 minutes" or "tomorrow at 3pm".'
        )

    def build_reminder_clarification_instruction(
        self,
        *,
        time_expression: str,
        parse_result: ReminderTimeParseResult,
        kind: str = "reminder",
    ) -> str:
        expr = (time_expression or "").strip()
        reason = str(parse_result.reason_code or "")
        label = {"timer": "timer", "alarm": "alarm"}.get(str(kind or "reminder").strip().lower(), "reminder")
        if reason == "past_same_day":
            return (
                f"**{label.title()} Action (internal):**\n"
                f'The requested {label} time "{expr}" appears to have already passed today.\n'
                f"Do not claim the {label} was scheduled. Ask ONE short follow-up asking whether they mean later today or tomorrow."
            )
        if reason == "ambiguous_bare_hour":
            return (
                f"**{label.title()} Action (internal):**\n"
                f'The requested {label} time "{expr}" is ambiguous because it does not specify AM or PM.\n'
                f"Do not claim the {label} was scheduled. Ask ONE short follow-up asking whether they mean AM or PM."
            )
        if reason == "missing_time":
            return (
                f"**{label.title()} Action (internal):**\n"
                f"The user seems to want a {label}, but the time is missing.\n"
                f"Do not claim the {label} was scheduled. Ask ONE short clarifying question about when it should happen."
            )
        return (
            f"**{label.title()} Action (internal):**\n"
            f"The user seems to want a {label}, but the time could not be understood.\n"
            f"Do not claim the {label} was scheduled. Ask ONE short follow-up asking them to restate the time clearly."
        )

    async def parse_time_expression_result(
        self,
        time_expression: str,
        *,
        user_id: Optional[int] = None,
        platform: Optional[str] = None,
        resolve_user_timezone: Optional[ResolveUserTimezone] = None,
        datetime_cls: DateTimeClass = datetime,
    ) -> ReminderTimeParseResult:
        """Parse time expression into a future Unix timestamp with explicit ambiguity handling."""
        try:
            if not time_expression:
                return self.parse_result(status="invalid", reason_code="missing_time")

            time_expr = time_expression.strip()
            if not time_expr:
                return self.parse_result(status="invalid", reason_code="missing_time")

            user_tz = None
            if user_id is not None and platform and resolve_user_timezone is not None:
                user_tz = await resolve_user_timezone(user_id, platform)

            system_tz = datetime_cls.now().astimezone().tzinfo or timezone.utc
            tz = user_tz or system_tz
            now = datetime_cls.now(tz)
            lower = time_expr.lower()

            relative_seconds = self.parse_relative_duration_seconds(lower)
            if relative_seconds is not None:
                return self.parse_result(
                    status="ok",
                    timestamp=(now + timedelta(seconds=relative_seconds)).timestamp(),
                )

            if "later" in lower:
                return self.parse_result(
                    status="ok",
                    timestamp=(now + timedelta(hours=2)).timestamp(),
                )

            in_match = re.search(
                r"\bin\s+(\d+)\s*(minute|minutes|min|hour|hours|hr|day|days|week|weeks)\b",
                lower,
            )
            if in_match:
                qty = int(in_match.group(1))
                unit = in_match.group(2)
                if unit.startswith(("minute", "min")):
                    return self.parse_result(
                        status="ok",
                        timestamp=(now + timedelta(minutes=qty)).timestamp(),
                    )
                if unit.startswith(("hour", "hr")):
                    return self.parse_result(
                        status="ok",
                        timestamp=(now + timedelta(hours=qty)).timestamp(),
                    )
                if unit.startswith("day"):
                    return self.parse_result(
                        status="ok",
                        timestamp=(now + timedelta(days=qty)).timestamp(),
                    )
                if unit.startswith("week"):
                    return self.parse_result(
                        status="ok",
                        timestamp=(now + timedelta(weeks=qty)).timestamp(),
                    )

            # 2026-05-06: handle common natural-language times that aren't
            # explicit clock readings - "tonight", "this evening", "tomorrow
            # morning", etc. Without these, dateutil errors with "String does
            # not contain a date" and the reminder is silently dropped.
            natural = self._resolve_natural_time_phrase(lower, now=now)
            if natural is not None:
                if natural <= now:
                    return self.parse_result(status="ambiguous", reason_code="past_same_day")
                return self.parse_result(status="ok", timestamp=natural.timestamp())

            if re.search(r"\btoday\b", lower):
                remainder = re.sub(r"\btoday\b", "", lower).strip()
                remainder = remainder.replace("at ", "", 1).strip()
                if not remainder:
                    return self.parse_result(status="invalid", reason_code="missing_time")
                components = self.extract_clock_components(remainder)
                if components is None:
                    return self.parse_result(status="invalid", reason_code="parse_failed")
                if components.get("kind") == "ambiguous":
                    return self.parse_result(status="ambiguous", reason_code="ambiguous_bare_hour")
                candidate = self.build_timestamp(
                    base_date=now.date(),
                    components=components,
                    tz=tz,
                    datetime_cls=datetime_cls,
                )
                if candidate <= now.timestamp():
                    return self.parse_result(status="ambiguous", reason_code="past_same_day")
                return self.parse_result(status="ok", timestamp=candidate)

            if "tomorrow" in lower:
                base_date = (now + timedelta(days=1)).date()
                remainder = re.sub(r"\btomorrow\b", "", lower).strip()
                remainder = remainder.replace("at ", "", 1).strip()
                if not remainder:
                    return self.parse_result(
                        status="ok",
                        timestamp=(now + timedelta(days=1)).timestamp(),
                    )
                components = self.extract_clock_components(remainder)
                if components is None:
                    return self.parse_result(status="invalid", reason_code="parse_failed")
                if components.get("kind") == "ambiguous":
                    return self.parse_result(status="ambiguous", reason_code="ambiguous_bare_hour")
                return self.parse_result(
                    status="ok",
                    timestamp=self.build_timestamp(
                        base_date=base_date,
                        components=components,
                        tz=tz,
                        datetime_cls=datetime_cls,
                    ),
                )

            weekdays = {
                "monday": 0,
                "tuesday": 1,
                "wednesday": 2,
                "thursday": 3,
                "friday": 4,
                "saturday": 5,
                "sunday": 6,
            }
            weekday_hit = None
            for name, idx in weekdays.items():
                if re.search(rf"\b{name}\b", lower):
                    weekday_hit = (name, idx)
                    break

            if weekday_hit:
                name, target_idx = weekday_hit
                remainder = re.sub(r"\bnext\b", "", lower)
                remainder = re.sub(rf"\b{name}\b", "", remainder).strip()
                remainder = remainder.replace("at ", "", 1).strip()
                if not remainder:
                    return self.parse_result(status="invalid", reason_code="missing_time")
                components = self.extract_clock_components(remainder)
                if components is None:
                    return self.parse_result(status="invalid", reason_code="parse_failed")
                if components.get("kind") == "ambiguous":
                    return self.parse_result(status="ambiguous", reason_code="ambiguous_bare_hour")

                days_ahead = (target_idx - now.weekday()) % 7
                candidate_date = (now + timedelta(days=days_ahead)).date()
                candidate = self.build_timestamp(
                    base_date=candidate_date,
                    components=components,
                    tz=tz,
                    datetime_cls=datetime_cls,
                )
                is_explicit_next = bool(re.search(rf"\bnext\s+{name}\b", lower))
                if is_explicit_next or days_ahead == 0 and candidate <= now.timestamp():
                    candidate_date = (now + timedelta(days=days_ahead or 7)).date()
                    if is_explicit_next:
                        candidate_date = candidate_date + timedelta(days=7 if days_ahead != 0 else 0)
                    candidate = self.build_timestamp(
                        base_date=candidate_date,
                        components=components,
                        tz=tz,
                        datetime_cls=datetime_cls,
                    )
                return self.parse_result(status="ok", timestamp=candidate)

            has_date_markers = bool(
                re.search(
                    r"\b\d{4}\b|\b(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)\b|\b\d{1,2}/\d{1,2}\b|\b\d{1,2}-\d{1,2}\b",
                    lower,
                )
            )
            time_only = self.resolve_clock_phrase(lower) if not has_date_markers else None
            if time_only is not None:
                if time_only.get("kind") == "ambiguous":
                    return self.parse_result(status="ambiguous", reason_code="ambiguous_bare_hour")
                candidate = self.build_timestamp(
                    base_date=now.date(),
                    components=time_only,
                    tz=tz,
                    datetime_cls=datetime_cls,
                )
                if candidate <= now.timestamp():
                    return self.parse_result(status="ambiguous", reason_code="past_same_day")
                return self.parse_result(status="ok", timestamp=candidate)

            parsed = date_parser.parse(time_expr, fuzzy=True, default=now)
            if parsed.tzinfo is None:
                parsed = self._attach_timezone(parsed, tz)

            fallback_clock = self.resolve_clock_phrase(lower, allow_bare_search=True)
            if fallback_clock is not None:
                if fallback_clock.get("kind") == "ambiguous":
                    return self.parse_result(status="ambiguous", reason_code="ambiguous_bare_hour")
                parsed = parsed.replace(
                    hour=int(fallback_clock.get("hour") or 0),
                    minute=int(fallback_clock.get("minute") or 0),
                    second=int(fallback_clock.get("second") or 0),
                    microsecond=0,
                )

            if parsed <= now:
                if "today" in lower or (fallback_clock is not None and not has_date_markers):
                    return self.parse_result(status="ambiguous", reason_code="past_same_day")
                if not re.search(r"\b\d{4}\b", lower):
                    parsed = parsed.replace(year=parsed.year + 1)

            if parsed <= now:
                return self.parse_result(status="invalid", reason_code="parse_failed")

            return self.parse_result(status="ok", timestamp=parsed.timestamp())

        except Exception as exc:
            logger.error(f"Error parsing time expression: {exc}")
            return self.parse_result(status="invalid", reason_code="parse_failed")

    def _resolve_natural_time_phrase(
        self,
        lower_text: str,
        *,
        now: datetime,
    ) -> Optional[datetime]:
        """Map common natural-language time phrases to concrete datetimes.

        Returns None if no phrase matched (caller falls back to dateutil
        parsing). Returns a datetime in the same tz as `now` when a
        phrase is recognized.

        Defaults used (configurable later if needed):
          morning   -> 09:00
          afternoon -> 14:00
          evening   -> 19:00
          night/    -> 21:00
          tonight   -> 21:00
        """
        # 2026-07-10: an explicit clock reading in the text must win over the
        # phrase defaults. This resolver runs BEFORE the clock-aware branches
        # in parse_time_expression_result, and bare "tomorrow" matches by
        # substring — so "tomorrow at 12:00 PM" was silently scheduled for
        # 09:00 (the bare-"tomorrow" default) with the asked-for time thrown
        # away. Defer to the clock branches whenever an AM/PM or HH:MM
        # reading is present.
        if _CLOCK_AMPM_RE.search(lower_text) or _CLOCK_24H_RE.search(lower_text):
            return None
        # Map of phrase -> (day_offset_from_today, hour, minute)
        phrase_map: list[tuple[str, int, int, int]] = [
            ("tomorrow morning", 1, 9, 0),
            ("tomorrow afternoon", 1, 14, 0),
            ("tomorrow evening", 1, 19, 0),
            ("tomorrow night", 1, 21, 0),
            ("tomorrow", 1, 9, 0),
            ("tonight", 0, 21, 0),
            ("this evening", 0, 19, 0),
            ("this afternoon", 0, 14, 0),
            ("this morning", 0, 9, 0),
            ("later tonight", 0, 22, 0),
            ("later this evening", 0, 20, 0),
            ("end of day", 0, 17, 0),
            ("eod", 0, 17, 0),
            ("noon", 0, 12, 0),
            ("midnight", 1, 0, 0),  # midnight -> tomorrow at 0:00
        ]
        # Match longest phrase first so "tomorrow morning" wins over "tomorrow"
        phrase_map.sort(key=lambda item: -len(item[0]))
        for phrase, day_offset, hour, minute in phrase_map:
            if phrase not in lower_text:
                continue
            target_date = now.date() + timedelta(days=day_offset)
            target = datetime(
                year=target_date.year,
                month=target_date.month,
                day=target_date.day,
                hour=hour,
                minute=minute,
                tzinfo=now.tzinfo,
            )
            return target
        return None

    async def parse_time_expression(
        self,
        time_expression: str,
        *,
        user_id: Optional[int] = None,
        platform: Optional[str] = None,
        resolve_user_timezone: Optional[ResolveUserTimezone] = None,
        datetime_cls: DateTimeClass = datetime,
    ) -> Optional[float]:
        """Backward-compatible helper that returns only the parsed timestamp."""
        result = await self.parse_time_expression_result(
            time_expression,
            user_id=user_id,
            platform=platform,
            resolve_user_timezone=resolve_user_timezone,
            datetime_cls=datetime_cls,
        )
        return result.timestamp if result.status == "ok" else None
