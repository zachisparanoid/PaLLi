from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal


@dataclass
class TokenTracker:
    total_prompt: int = 0
    total_completion: int = 0

    def add(self, *, prompt: int, completion: int) -> None:
        self.total_prompt += prompt
        self.total_completion += completion

    @property
    def total(self) -> int:
        return self.total_prompt + self.total_completion


def log_loop_start(
    *,
    session_key: str,
    surface: str,
    turn_origin: str,
    cadence_summary: str,
    tool_names: list[str],
) -> None:
    logging.getLogger("core.agent.runner").info(
        "loop_start session=%s surface=%s origin=%s cadence=%r tools=%s",
        session_key, surface, turn_origin, cadence_summary, ",".join(tool_names),
    )


def log_iteration_start(*, session_key: str, iteration_n: int) -> None:
    logging.getLogger("core.agent.runner").debug(
        "iteration_start session=%s iter=%d", session_key, iteration_n,
    )


def log_llm_call(
    *,
    session_key: str,
    iteration_n: int,
    provider: str,
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    latency_ms: int,
    cache_hit_tokens: int = 0,
    cache_miss_tokens: int = 0,
) -> None:
    logging.getLogger("core.agent.llm_client").info(
        "llm_call session=%s iter=%d provider=%s model=%s prompt_tokens=%d completion_tokens=%d latency_ms=%d"
        " cache_hit=%d cache_miss=%d",
        session_key, iteration_n, provider, model, prompt_tokens, completion_tokens, latency_ms,
        cache_hit_tokens, cache_miss_tokens,
    )


def log_tool_dispatch(
    *,
    session_key: str,
    iteration_n: int,
    tool_name: str,
    tool_use_id: str,
    args_size_bytes: int,
) -> None:
    logging.getLogger("core.agent.tools").info(
        "tool_dispatch session=%s iter=%d tool=%s id=%s args_size=%d",
        session_key, iteration_n, tool_name, tool_use_id, args_size_bytes,
    )


def log_tool_result(
    *,
    session_key: str,
    iteration_n: int,
    tool_use_id: str,
    success: bool,
    result_size_bytes: int,
    truncated: bool,
    latency_ms: int,
) -> None:
    logging.getLogger("core.agent.tools").info(
        "tool_result session=%s iter=%d id=%s success=%s size=%d truncated=%s latency_ms=%d",
        session_key, iteration_n, tool_use_id, success, result_size_bytes, truncated, latency_ms,
    )


def log_detection_trigger(
    *,
    session_key: str,
    detector: str,
    level: Literal["warning", "critical"],
    count: int,
    paired_tool_name: str | None,
) -> None:
    log_level = logging.CRITICAL if level == "critical" else logging.WARNING
    logging.getLogger("core.agent.detection").log(
        log_level,
        "detection_trigger session=%s detector=%s level=%s count=%d paired=%s",
        session_key, detector, level, count, paired_tool_name or "-",
    )


def log_loop_end(
    *,
    session_key: str,
    iterations: int,
    tracker: TokenTracker,
    tool_calls: int,
    terminated_by: str,
    latency_ms: int,
) -> None:
    logging.getLogger("core.agent.runner").info(
        "loop_end session=%s iters=%d prompt_tokens=%d completion_tokens=%d tool_calls=%d terminated_by=%s latency_ms=%d",
        session_key, iterations, tracker.total_prompt, tracker.total_completion,
        tool_calls, terminated_by, latency_ms,
    )


def log_judgment_pass(
    *,
    session_key: str,
    decision: str,
    acknowledgment_text: str,
) -> None:
    logging.getLogger("core.agent.cadence").info(
        "judgment_pass session=%s decision=%s ack=%r",
        session_key, decision, acknowledgment_text,
    )


def log_cancellation(
    *,
    session_key: str,
    in_flight_iteration_n: int,
    reason: str,
) -> None:
    logging.getLogger("core.agent.runner").info(
        "cancellation session=%s in_flight_iter=%d reason=%s",
        session_key, in_flight_iteration_n, reason,
    )
