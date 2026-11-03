from __future__ import annotations

import hashlib
import json
import logging
from collections import deque
from dataclasses import dataclass
from typing import Any, Literal


logger = logging.getLogger(__name__)


TOOL_CALL_HISTORY_SIZE = 30
WARNING_THRESHOLD = 10
UNKNOWN_TOOL_THRESHOLD = 10
CRITICAL_THRESHOLD = 20
GLOBAL_CIRCUIT_BREAKER_THRESHOLD = 30


DetectorKind = Literal[
    "generic_repeat",
    "unknown_tool_repeat",
    "known_poll_no_progress",
    "global_circuit_breaker",
    "ping_pong",
]


@dataclass(frozen=True)
class LoopDetectionResult:
    stuck: bool
    detector: DetectorKind | None = None
    level: Literal["warning", "critical"] | None = None
    count: int = 0
    message: str = ""


@dataclass
class _CallRecord:
    tool_name: str
    args_hash: str
    result_hash: str | None


def _args_hash(args: dict[str, Any]) -> str:
    payload = json.dumps(args, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


class ToolLoopDetector:
    def __init__(
        self,
        *,
        known_tool_names: set[str],
        polling_tools: set[str] | None = None,
    ) -> None:
        self._known = set(known_tool_names)
        self._polling = set(polling_tools or set())
        self._history: deque[_CallRecord] = deque(maxlen=TOOL_CALL_HISTORY_SIZE)

    def check(
        self,
        tool_name: str,
        args: dict[str, Any],
        *,
        result_hash: str | None = None,
    ) -> LoopDetectionResult:
        rec = _CallRecord(tool_name=tool_name, args_hash=_args_hash(args), result_hash=result_hash)
        self._history.append(rec)

        # 1. unknown_tool_repeat (always enforced)
        if tool_name not in self._known:
            consecutive = 0
            for r in reversed(self._history):
                if r.tool_name == tool_name:
                    consecutive += 1
                else:
                    break
            if consecutive >= UNKNOWN_TOOL_THRESHOLD:
                return LoopDetectionResult(
                    stuck=True,
                    detector="unknown_tool_repeat",
                    level="critical",
                    count=consecutive,
                    message=(
                        f"CRITICAL: tool {tool_name!r} is not registered and has been called "
                        f"{consecutive} times consecutively. Stop calling it; answer without it."
                    ),
                )

        # 2. global_circuit_breaker
        no_progress_run = 0
        for r in reversed(self._history):
            if r.args_hash == rec.args_hash and r.result_hash == rec.result_hash and r.tool_name == tool_name:
                no_progress_run += 1
            else:
                break
        if no_progress_run >= GLOBAL_CIRCUIT_BREAKER_THRESHOLD:
            return LoopDetectionResult(
                stuck=True,
                detector="global_circuit_breaker",
                level="critical",
                count=no_progress_run,
                message=(
                    f"CRITICAL: tool {tool_name!r} called {no_progress_run} times with same "
                    f"args and no progress. Hard stop - give up and report failure."
                ),
            )

        # 3. known_poll_no_progress
        if tool_name in self._polling and result_hash is not None:
            poll_run = 0
            for r in reversed(self._history):
                if r.tool_name == tool_name and r.args_hash == rec.args_hash and r.result_hash == result_hash:
                    poll_run += 1
                else:
                    break
            if poll_run >= CRITICAL_THRESHOLD:
                return LoopDetectionResult(
                    stuck=True,
                    detector="known_poll_no_progress",
                    level="critical",
                    count=poll_run,
                    message=f"CRITICAL: polling {tool_name!r} ({poll_run}x same result). Stop polling.",
                )
            if poll_run >= WARNING_THRESHOLD:
                return LoopDetectionResult(
                    stuck=True,
                    detector="known_poll_no_progress",
                    level="warning",
                    count=poll_run,
                    message=f"WARNING: polling {tool_name!r} ({poll_run}x same result). Consider giving up.",
                )

        # 4. ping_pong (alternating two distinct signatures)
        if len(self._history) >= 4:
            sig = (rec.tool_name, rec.args_hash)
            alt_count = 0
            other_sig = None
            for r in reversed(self._history):
                cur_sig = (r.tool_name, r.args_hash)
                if alt_count == 0:
                    expected = sig
                else:
                    expected = sig if alt_count % 2 == 0 else other_sig
                if alt_count == 0:
                    if cur_sig != sig:
                        break
                    alt_count += 1
                elif alt_count == 1:
                    if cur_sig == sig:
                        break
                    other_sig = cur_sig
                    alt_count += 1
                else:
                    if cur_sig == expected:
                        alt_count += 1
                    else:
                        break
            if other_sig is not None and alt_count >= CRITICAL_THRESHOLD:
                return LoopDetectionResult(
                    stuck=True, detector="ping_pong", level="critical", count=alt_count,
                    message=f"CRITICAL: ping-pong between two tool calls ({alt_count} alternations). Stop.",
                )
            if other_sig is not None and alt_count >= WARNING_THRESHOLD:
                return LoopDetectionResult(
                    stuck=True, detector="ping_pong", level="warning", count=alt_count,
                    message=f"WARNING: alternating tool calls ({alt_count}). Try a different approach.",
                )

        # 5. generic_repeat (consecutive-only — same pattern as ping_pong above).
        # D.4 step 1: was summing across the entire 30-record window, which
        # fired on legitimate "search-then-search" patterns separated by
        # other tools. Now counts only the trailing consecutive run of
        # (tool_name, args_hash) matches, mirroring the reversed-iteration-
        # early-break style used by unknown_tool_repeat (above) and
        # ping_pong (below).
        count = 0
        for r in reversed(self._history):
            if r.tool_name == tool_name and r.args_hash == rec.args_hash:
                count += 1
            else:
                break
        if count >= WARNING_THRESHOLD:
            return LoopDetectionResult(
                stuck=True, detector="generic_repeat", level="warning", count=count,
                message=(
                    f"WARNING: tool {tool_name!r} called {count} times consecutively with same args. "
                    f"Stop retrying and report the task as failed."
                ),
            )

        return LoopDetectionResult(stuck=False)
