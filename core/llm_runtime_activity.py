from __future__ import annotations

import threading
import time
from typing import Any, Awaitable, Callable, Dict, List, Optional


LLMRuntimeActivityListener = Callable[[Dict[str, Any]], Awaitable[None] | None]


class LLMRuntimeActivityService:
    MAX_EVENTS_PER_SCOPE = 48
    RETENTION_SECONDS = 6 * 60 * 60.0
    RESPONSE_LANE_ORDER = {
        "chat": 0,
        "voice": 1,
        "operator": 2,
        "other": 4,
    }

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._events_by_owner: Dict[int, List[Dict[str, Any]]] = {}
        self._global_events: List[Dict[str, Any]] = []
        self._listeners: List[LLMRuntimeActivityListener] = []

    @staticmethod
    def _provider_label(provider_name: str) -> str:
        normalized = str(provider_name or "").strip().lower()
        if normalized == "openai":
            return "OpenAI"
        if normalized == "local_openai":
            return "Local OpenAI"
        if normalized == "deepseek":
            return "DeepSeek"
        clean = normalized.replace("_", " ").strip()
        return clean.title() if clean else "Unknown"

    @staticmethod
    def _parse_model_reference(model_reference: Optional[str]) -> tuple[Optional[str], Optional[str]]:
        raw = str(model_reference or "").strip()
        if not raw:
            return None, None
        if "::" not in raw:
            return None, raw
        provider_name, model_name = raw.split("::", 1)
        provider = provider_name.strip().lower() or None
        model = model_name.strip() or None
        return provider, model

    @staticmethod
    def _model_reference(provider_name: str, model_name: str) -> str:
        clean_provider = str(provider_name or "").strip().lower()
        clean_model = str(model_name or "").strip()
        if clean_provider and clean_model:
            return f"{clean_provider}::{clean_model}"
        return clean_model

    @staticmethod
    def _safe_int(value: Any) -> int:
        try:
            return max(int(value or 0), 0)
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _safe_float(value: Any) -> float:
        try:
            return max(float(value or 0.0), 0.0)
        except (TypeError, ValueError):
            return 0.0

    @classmethod
    def _usage_snapshot(cls, payload: Dict[str, Any]) -> Dict[str, int]:
        usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
        return {
            "prompt_tokens": cls._safe_int(usage.get("prompt_tokens")),
            "completion_tokens": cls._safe_int(usage.get("completion_tokens")),
            "total_tokens": cls._safe_int(usage.get("total_tokens")),
        }

    @staticmethod
    def _humanize_log_context(log_context: str) -> str:
        clean = str(log_context or "").strip().replace("_", " ")
        return clean or "Other"

    @staticmethod
    def _classify_log_context(log_context: str) -> Dict[str, Any]:
        normalized = str(log_context or "").strip().lower()

        if normalized.startswith("operator_voice_reply_generation_"):
            return {
                "activity_kind": "response",
                "activity_label": "Operator voice reply",
                "lane": "operator",
                "lane_label": "Operator",
                "is_response": True,
            }
        if normalized.startswith("operator_response_generation_"):
            return {
                "activity_kind": "response",
                "activity_label": "Operator reply",
                "lane": "operator",
                "lane_label": "Operator",
                "is_response": True,
            }
        if normalized.startswith("voice_reply_generation_"):
            return {
                "activity_kind": "response",
                "activity_label": "Voice reply",
                "lane": "voice",
                "lane_label": "Voice",
                "is_response": True,
            }
        if normalized.startswith("response_generation_"):
            return {
                "activity_kind": "response",
                "activity_label": "Chat reply",
                "lane": "chat",
                "lane_label": "Chat",
                "is_response": True,
            }
        if normalized in {"respond_decision", "combined_decision"} or normalized.startswith("proactive_decision_"):
            return {
                "activity_kind": "decision",
                "activity_label": "Decision",
                "lane": "decision",
                "lane_label": "Decision",
                "is_response": False,
            }
        if normalized.startswith("proactive_timing_guard_"):
            return {
                "activity_kind": "decision",
                "activity_label": "Timing guard",
                "lane": "proactive",
                "lane_label": "Proactive",
                "is_response": False,
            }
        if normalized.startswith("proactive_generation_"):
            return {
                "activity_kind": "proactive",
                "activity_label": "Proactive generation",
                "lane": "proactive",
                "lane_label": "Proactive",
                "is_response": False,
            }
        if normalized.startswith("web_search_summarization") or normalized.startswith("url_browse_summarization"):
            return {
                "activity_kind": "analysis",
                "activity_label": "Research summary",
                "lane": "research",
                "lane_label": "Research",
                "is_response": False,
            }
        if (
            normalized == "message_analysis"
            or normalized.startswith("response_style_planner_")
            or normalized.startswith("thread_extraction_")
        ):
            return {
                "activity_kind": "analysis",
                "activity_label": "Analysis",
                "lane": "analysis",
                "lane_label": "Analysis",
                "is_response": False,
            }
        if normalized.startswith("identity_memory_"):
            return {
                "activity_kind": "memory",
                "activity_label": "Memory",
                "lane": "memory",
                "lane_label": "Memory",
                "is_response": False,
            }
        if normalized.startswith("workflow_") or normalized.startswith("operator_workflow_resume_"):
            return {
                "activity_kind": "operator",
                "activity_label": "Operator workflow",
                "lane": "operator",
                "lane_label": "Operator",
                "is_response": False,
            }
        return {
            "activity_kind": "other",
            "activity_label": LLMRuntimeActivityService._humanize_log_context(log_context),
            "lane": "other",
            "lane_label": "Other",
            "is_response": False,
        }

    @staticmethod
    def _infer_owner_user_id(log_context: str) -> Optional[int]:
        normalized = str(log_context or "").strip().lower()
        owner_prefixes = (
            "response_generation_",
            "voice_reply_generation_",
            "operator_response_generation_",
            "operator_voice_reply_generation_",
            "response_style_planner_",
            "identity_memory_",
            "thread_extraction_",
            "web_search_summarization_",
            "url_browse_summarization_",
            "ambient_arrival_generation_",
            "interest_question_",
            "proactive_decision_",
            "proactive_timing_guard_",
            "proactive_generation_",
            "proactive_content_audit_",
        )
        for prefix in owner_prefixes:
            if not normalized.startswith(prefix):
                continue
            suffix = normalized[len(prefix):]
            digits: List[str] = []
            for char in suffix:
                if char.isdigit():
                    digits.append(char)
                    continue
                break
            if digits:
                return int("".join(digits))
        return None

    def _prune_locked(self, *, owner_user_id: Optional[int] = None, now: Optional[float] = None) -> None:
        current_time = float(now or time.time())
        cutoff = current_time - self.RETENTION_SECONDS

        self._global_events = [
            dict(event)
            for event in self._global_events
            if float(event.get("recorded_at") or 0.0) >= cutoff
        ][: self.MAX_EVENTS_PER_SCOPE]

        owner_ids = [int(owner_user_id)] if owner_user_id is not None else list(self._events_by_owner.keys())
        for current_owner_id in owner_ids:
            bucket = self._events_by_owner.get(int(current_owner_id)) or []
            filtered = [
                dict(event)
                for event in bucket
                if float(event.get("recorded_at") or 0.0) >= cutoff
            ][: self.MAX_EVENTS_PER_SCOPE]
            if filtered:
                self._events_by_owner[int(current_owner_id)] = filtered
            else:
                self._events_by_owner.pop(int(current_owner_id), None)

    def reset(self) -> None:
        with self._lock:
            self._events_by_owner = {}
            self._global_events = []

    def subscribe(self, listener: LLMRuntimeActivityListener) -> None:
        with self._lock:
            if listener not in self._listeners:
                self._listeners.append(listener)

    def unsubscribe(self, listener: LLMRuntimeActivityListener) -> None:
        with self._lock:
            self._listeners = [current for current in self._listeners if current != listener]

    def record_call(
        self,
        *,
        payload: Dict[str, Any],
        requested_model_reference: str = "",
        resolved_provider: str = "",
        resolved_model: str = "",
        owner_user_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        recorded_at = time.time()
        provider_name = str(payload.get("provider") or resolved_provider or "").strip().lower()
        model_name = str(payload.get("model") or resolved_model or "").strip()
        requested_provider_name, requested_model_name = self._parse_model_reference(requested_model_reference)
        log_context = str(payload.get("log_context") or "").strip()
        event_owner_user_id = owner_user_id if owner_user_id is not None else self._infer_owner_user_id(log_context)
        classification = self._classify_log_context(log_context)
        effective_model_reference = self._model_reference(provider_name, model_name)
        requested_provider = str(requested_provider_name or "").strip().lower()
        requested_model = str(requested_model_name or "").strip()
        requested_model_reference_value = str(requested_model_reference or "").strip()
        route_changed = bool(
            requested_model_reference_value
            and effective_model_reference
            and requested_model_reference_value != effective_model_reference
        )
        event = {
            "recorded_at": recorded_at,
            "owner_user_id": int(event_owner_user_id) if event_owner_user_id is not None else None,
            "provider": provider_name,
            "provider_label": self._provider_label(provider_name),
            "effective_provider": provider_name,
            "effective_provider_label": self._provider_label(provider_name),
            "model": model_name,
            "effective_model": model_name,
            "requested_provider": requested_provider,
            "requested_provider_label": self._provider_label(requested_provider),
            "requested_model": requested_model,
            "requested_model_reference": requested_model_reference_value,
            "effective_model_reference": effective_model_reference,
            "route_changed": route_changed,
            "log_context": log_context,
            "streamed": bool(payload.get("streamed")),
            "success": bool(payload.get("success")),
            "latency_seconds": self._safe_float(payload.get("latency_seconds")),
            "provider_retry_count": self._safe_int(payload.get("provider_retry_count")),
            "finish_reason": str(payload.get("finish_reason") or "").strip(),
            "error": str(payload.get("error") or "").strip(),
            "usage": self._usage_snapshot(payload),
            **classification,
        }

        with self._lock:
            self._prune_locked(now=recorded_at)
            self._global_events.insert(0, dict(event))
            self._global_events = self._global_events[: self.MAX_EVENTS_PER_SCOPE]
            if event["owner_user_id"] is not None:
                owner_bucket = self._events_by_owner.setdefault(int(event["owner_user_id"]), [])
                owner_bucket.insert(0, dict(event))
                self._events_by_owner[int(event["owner_user_id"])] = owner_bucket[: self.MAX_EVENTS_PER_SCOPE]

        return dict(event)

    async def notify_listeners(self, event: Dict[str, Any]) -> None:
        with self._lock:
            listeners = list(self._listeners)
        for listener in listeners:
            try:
                result = listener(dict(event))
                if hasattr(result, "__await__"):
                    await result  # type: ignore[misc]
            except Exception:
                continue

    def _events_for_summary(
        self,
        *,
        owner_user_id: Optional[int],
        fallback_to_global: bool,
    ) -> tuple[str, List[Dict[str, Any]]]:
        with self._lock:
            self._prune_locked(owner_user_id=owner_user_id)
            if owner_user_id is not None:
                owner_events = [dict(event) for event in self._events_by_owner.get(int(owner_user_id), [])]
                if owner_events:
                    return "owner", owner_events
            if fallback_to_global and self._global_events:
                return "global", [dict(event) for event in self._global_events]
        return "none", []

    @staticmethod
    def _build_response_lane_summaries(response_calls: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        lane_order: List[str] = []
        for event in response_calls:
            lane = str(event.get("lane") or "").strip().lower() or "other"
            if lane not in lane_order:
                lane_order.append(lane)
        lane_order.sort(key=lambda lane: (LLMRuntimeActivityService.RESPONSE_LANE_ORDER.get(lane, 99), lane))

        summaries: List[Dict[str, Any]] = []
        for lane in lane_order:
            lane_calls = [
                dict(event)
                for event in response_calls
                if str(event.get("lane") or "").strip().lower() == lane
            ]
            successful_lane_calls = [dict(event) for event in lane_calls if bool(event.get("success"))]
            failed_lane_calls = [dict(event) for event in lane_calls if not bool(event.get("success"))]
            last_response = dict(lane_calls[0]) if lane_calls else None
            summaries.append(
                {
                    "lane": lane,
                    "lane_label": str((last_response or {}).get("lane_label") or lane.title() or "Other"),
                    "call_count": len(lane_calls),
                    "success_count": len(successful_lane_calls),
                    "failure_count": len(failed_lane_calls),
                    "last_response": last_response,
                    "last_successful_response": dict(successful_lane_calls[0]) if successful_lane_calls else None,
                    "last_failed_response": dict(failed_lane_calls[0]) if failed_lane_calls else None,
                }
            )
        return summaries

    def get_summary(
        self,
        *,
        owner_user_id: Optional[int] = None,
        fallback_to_global: bool = True,
        limit: int = 6,
    ) -> Dict[str, Any]:
        scope, events = self._events_for_summary(
            owner_user_id=owner_user_id,
            fallback_to_global=fallback_to_global,
        )
        ownerless_global_events: List[Dict[str, Any]] = []
        if owner_user_id is not None:
            with self._lock:
                self._prune_locked(owner_user_id=owner_user_id)
                ownerless_global_events = [
                    dict(event)
                    for event in self._global_events
                    if event.get("owner_user_id") is None
                ]
        recent_calls = [dict(event) for event in events[: max(int(limit or 6), 1)]]
        response_calls = [event for event in events if bool(event.get("is_response"))]
        successful_calls = [event for event in events if bool(event.get("success"))]
        successful_response_calls = [event for event in response_calls if bool(event.get("success"))]
        failed_calls = [event for event in events if not bool(event.get("success"))]
        failed_response_calls = [event for event in response_calls if not bool(event.get("success"))]
        ownerless_global_failed_response_calls = [
            event
            for event in ownerless_global_events
            if bool(event.get("is_response")) and not bool(event.get("success"))
        ]
        voice_response_calls = [
            event
            for event in successful_response_calls
            if str(event.get("lane") or "").strip().lower() == "voice"
        ]
        chat_response_calls = [
            event
            for event in successful_response_calls
            if str(event.get("lane") or "").strip().lower() == "chat"
        ]
        failed_voice_response_calls = [
            event
            for event in failed_response_calls
            if str(event.get("lane") or "").strip().lower() == "voice"
        ]
        failed_chat_response_calls = [
            event
            for event in failed_response_calls
            if str(event.get("lane") or "").strip().lower() == "chat"
        ]
        response_lane_summaries = self._build_response_lane_summaries(response_calls)
        return {
            "scope": scope,
            "call_count": len(events),
            "success_count": sum(1 for event in events if bool(event.get("success"))),
            "failure_count": sum(1 for event in events if not bool(event.get("success"))),
            "last_call": dict(events[0]) if events else None,
            "last_response": dict(response_calls[0]) if response_calls else None,
            "last_successful_call": dict(successful_calls[0]) if successful_calls else None,
            "last_successful_response": dict(successful_response_calls[0]) if successful_response_calls else None,
            "last_failed_call": dict(failed_calls[0]) if failed_calls else None,
            "last_failed_response": dict(failed_response_calls[0]) if failed_response_calls else None,
            "last_successful_chat_response": dict(chat_response_calls[0]) if chat_response_calls else None,
            "last_successful_voice_response": dict(voice_response_calls[0]) if voice_response_calls else None,
            "last_failed_chat_response": dict(failed_chat_response_calls[0]) if failed_chat_response_calls else None,
            "last_failed_voice_response": dict(failed_voice_response_calls[0]) if failed_voice_response_calls else None,
            "global_last_failed_response": (
                dict(ownerless_global_failed_response_calls[0])
                if ownerless_global_failed_response_calls
                else None
            ),
            "response_lanes": response_lane_summaries,
            "recent_calls": recent_calls,
        }


llm_runtime_activity_service = LLMRuntimeActivityService()
