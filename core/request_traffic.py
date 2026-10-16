from __future__ import annotations

import threading
import time
from collections import deque
from typing import Any, Deque, Dict, List, Tuple


class APIRequestTrafficService:
    RETENTION_SECONDS = 15 * 60.0
    MAX_STORED_EVENTS = 4096
    WINDOW_SPECS: Tuple[Tuple[str, float], ...] = (
        ("last_10_seconds", 10.0),
        ("last_60_seconds", 60.0),
        ("last_5_minutes", 5 * 60.0),
    )

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._events: Deque[Dict[str, Any]] = deque()
        self._boot_started_at = time.time()
        self._in_flight = 0
        self._total_requests = 0
        self._method_totals: Dict[str, int] = {}
        self._status_totals = self._blank_status_counts()

    @staticmethod
    def _blank_status_counts() -> Dict[str, int]:
        return {
            "1xx": 0,
            "2xx": 0,
            "3xx": 0,
            "4xx": 0,
            "5xx": 0,
            "other": 0,
        }

    @staticmethod
    def _status_family(status_code: int) -> str:
        if 100 <= int(status_code) < 600:
            return f"{int(status_code) // 100}xx"
        return "other"

    @staticmethod
    def _safe_status_code(status_code: Any) -> int:
        try:
            return max(int(status_code or 0), 0)
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _safe_duration_ms(duration_seconds: Any) -> float:
        try:
            return max(float(duration_seconds or 0.0) * 1000.0, 0.0)
        except (TypeError, ValueError):
            return 0.0

    def _prune_locked(self, *, now: float) -> None:
        cutoff = float(now) - self.RETENTION_SECONDS
        while self._events and (
            float(self._events[-1].get("recorded_at") or 0.0) < cutoff
            or len(self._events) > self.MAX_STORED_EVENTS
        ):
            self._events.pop()

    def record_request_started(self) -> None:
        with self._lock:
            self._in_flight += 1

    def record_request_finished(
        self,
        *,
        method: str,
        route: str,
        path: str,
        status_code: int,
        duration_seconds: float,
    ) -> None:
        now = time.time()
        normalized_method = str(method or "GET").strip().upper() or "GET"
        normalized_route = str(route or path or "/").strip() or "/"
        normalized_path = str(path or normalized_route).strip() or normalized_route
        safe_status_code = self._safe_status_code(status_code)
        duration_ms = round(self._safe_duration_ms(duration_seconds), 2)
        status_family = self._status_family(safe_status_code)

        event = {
            "recorded_at": now,
            "method": normalized_method,
            "route": normalized_route,
            "path": normalized_path,
            "status_code": safe_status_code,
            "status_family": status_family,
            "duration_ms": duration_ms,
        }

        with self._lock:
            self._in_flight = max(self._in_flight - 1, 0)
            self._prune_locked(now=now)
            self._events.appendleft(event)
            while len(self._events) > self.MAX_STORED_EVENTS:
                self._events.pop()
            self._total_requests += 1
            self._method_totals[normalized_method] = self._method_totals.get(normalized_method, 0) + 1
            self._status_totals[status_family] = self._status_totals.get(status_family, 0) + 1

    def reset_for_tests(self) -> None:
        with self._lock:
            self._events.clear()
            self._boot_started_at = time.time()
            self._in_flight = 0
            self._total_requests = 0
            self._method_totals = {}
            self._status_totals = self._blank_status_counts()

    def _build_window_summary(self, *, events: List[Dict[str, Any]], now: float, seconds: float) -> Dict[str, Any]:
        status_counts = self._blank_status_counts()
        method_counts: Dict[str, int] = {}
        count = 0
        for event in events:
            if float(now - float(event.get("recorded_at") or 0.0)) > seconds:
                continue
            count += 1
            family = str(event.get("status_family") or "other")
            status_counts[family] = status_counts.get(family, 0) + 1
            method = str(event.get("method") or "GET")
            method_counts[method] = method_counts.get(method, 0) + 1
        return {
            "seconds": seconds,
            "count": count,
            "approx_requests_per_minute": round((count * 60.0) / max(seconds, 1.0), 2),
            "client_error_count": int(status_counts.get("4xx") or 0),
            "server_error_count": int(status_counts.get("5xx") or 0),
            "status_counts": status_counts,
            "method_counts": dict(sorted(method_counts.items())),
        }

    def _build_top_routes(
        self,
        *,
        events: List[Dict[str, Any]],
        now: float,
        route_limit: int,
    ) -> List[Dict[str, Any]]:
        grouped: Dict[Tuple[str, str], Dict[str, Any]] = {}
        for event in events:
            age_seconds = float(now - float(event.get("recorded_at") or 0.0))
            if age_seconds > 5 * 60.0:
                continue

            method = str(event.get("method") or "GET")
            route = str(event.get("route") or event.get("path") or "/")
            key = (method, route)
            bucket = grouped.get(key)
            if bucket is None:
                bucket = {
                    "method": method,
                    "route": route,
                    "count_last_60_seconds": 0,
                    "count_last_5_minutes": 0,
                    "client_error_count": 0,
                    "server_error_count": 0,
                    "last_status_code": int(event.get("status_code") or 0),
                    "last_seen_at": float(event.get("recorded_at") or now),
                    "_duration_total_ms": 0.0,
                }
                grouped[key] = bucket

            bucket["count_last_5_minutes"] += 1
            if age_seconds <= 60.0:
                bucket["count_last_60_seconds"] += 1

            family = str(event.get("status_family") or "other")
            if family == "4xx":
                bucket["client_error_count"] += 1
            elif family == "5xx":
                bucket["server_error_count"] += 1

            bucket["_duration_total_ms"] += float(event.get("duration_ms") or 0.0)

        items = sorted(
            grouped.values(),
            key=lambda item: (
                -int(item.get("count_last_60_seconds") or 0),
                -int(item.get("count_last_5_minutes") or 0),
                -float(item.get("last_seen_at") or 0.0),
                str(item.get("route") or ""),
            ),
        )

        result: List[Dict[str, Any]] = []
        for item in items[: max(int(route_limit or 0), 1)]:
            count_last_5_minutes = max(int(item.get("count_last_5_minutes") or 0), 1)
            result.append(
                {
                    "method": str(item.get("method") or "GET"),
                    "route": str(item.get("route") or "/"),
                    "count_last_60_seconds": int(item.get("count_last_60_seconds") or 0),
                    "count_last_5_minutes": count_last_5_minutes,
                    "avg_duration_ms": round(float(item.get("_duration_total_ms") or 0.0) / count_last_5_minutes, 2),
                    "client_error_count": int(item.get("client_error_count") or 0),
                    "server_error_count": int(item.get("server_error_count") or 0),
                    "last_status_code": int(item.get("last_status_code") or 0),
                    "last_seen_at": float(item.get("last_seen_at") or now),
                }
            )
        return result

    def get_snapshot(self, *, route_limit: int = 10, recent_limit: int = 20) -> Dict[str, Any]:
        now = time.time()
        with self._lock:
            self._prune_locked(now=now)
            events = [dict(event) for event in self._events]
            in_flight = int(self._in_flight)
            total_requests = int(self._total_requests)
            method_totals = dict(self._method_totals)
            status_totals = dict(self._status_totals)
            boot_started_at = float(self._boot_started_at)

        windows = {
            key: self._build_window_summary(events=events, now=now, seconds=seconds)
            for key, seconds in self.WINDOW_SPECS
        }
        last_60_seconds = dict(windows.get("last_60_seconds") or {})
        last_5_minutes = dict(windows.get("last_5_minutes") or {})

        status = "idle"
        if int(last_5_minutes.get("server_error_count") or 0) > 0:
            status = "degraded"
        elif in_flight > 0 or int(last_60_seconds.get("count") or 0) > 0:
            status = "active"

        summary = "No recent API traffic."
        if total_requests > 0:
            summary = (
                f"{int(last_60_seconds.get('count') or 0)} requests in the last minute, "
                f"{int(last_5_minutes.get('count') or 0)} in the last 5 minutes."
            )

        attention_bits: List[str] = []
        if in_flight > 0:
            attention_bits.append(f"{in_flight} in flight")
        if int(last_5_minutes.get("server_error_count") or 0) > 0:
            attention_bits.append(f"{int(last_5_minutes.get('server_error_count') or 0)} server errors in the last 5 minutes")
        elif int(last_5_minutes.get("client_error_count") or 0) > 0:
            attention_bits.append(f"{int(last_5_minutes.get('client_error_count') or 0)} client errors in the last 5 minutes")

        top_routes = self._build_top_routes(
            events=events,
            now=now,
            route_limit=route_limit,
        )
        recent_requests = events[: max(int(recent_limit or 0), 1)]

        return {
            "status": status,
            "updated_at": now,
            "boot_started_at": boot_started_at,
            "uptime_seconds": round(max(now - boot_started_at, 0.0), 2),
            "in_flight": in_flight,
            "summary": summary,
            "attention_summary": "; ".join(attention_bits),
            "totals": {
                "requests": total_requests,
                "method_counts": dict(sorted(method_totals.items())),
                "status_counts": status_totals,
            },
            "windows": windows,
            "top_routes": top_routes,
            "recent_requests": recent_requests,
        }


api_request_traffic_service = APIRequestTrafficService()
