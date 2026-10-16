from __future__ import annotations

import aiohttp
import asyncio
import json
import logging
import os
import random
import time
import traceback
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, List, Optional, Set

from ai import call_ledger
from ai.credential_pool import CredentialPool
from core.config import config
from core.llm_json import safe_parse_json_object

logger = logging.getLogger("ai.llm_client")
debug_logger = logging.getLogger("palli.debug.llm")


# Status codes that signal a credential should be cooled down rather than
# just retried with the same key. 429 is rate-limit (per-minute bucket
# exhaustion); 401 is auth failure (revoked or invalid). Other retryable
# 5xx codes don't indicate a credential problem, so they don't trigger
# rotation — they're just transient server errors.
CREDENTIAL_FAILURE_STATUS_CODES: frozenset[int] = frozenset({401, 429})


def is_reasoning_prone_model(model_reference: Optional[str]) -> bool:
    """Best-effort detection for models that may emit separate reasoning and exhaust small budgets."""
    provider = None
    model = model_reference
    if isinstance(model_reference, str) and "::" in model_reference:
        provider, model = model_reference.split("::", 1)
    parsed_model = str(model or provider or "").strip().lower()
    if not parsed_model:
        return False
    return parsed_model.startswith(
        (
            "gpt-5",
            "o1",
            "o3",
            "o4",
            "qwen3",
            "qwq",
            "deepseek-r1",
            # deepseek-v4-flash is a thinking model: it emits reasoning_content
            # and can burn a small completion budget entirely on chain-of-thought
            # (2026-07-23 incident: 42 tokens, all reasoning, empty visible
            # reply). Listed so the small-budget aux lanes (constitutional gate
            # 400, cadence 600, appraisal 700) get the empty-completion retry
            # now that they ride DeepSeek (2026-07-24 Anthropic→DeepSeek
            # reroute after credit exhaustion).
            "deepseek-v4-flash",
            "r1",
        )
    )


def messages_expect_json(messages: List[Dict[str, Any]]) -> bool:
    for message in messages or []:
        content = str((message or {}).get("content") or "").lower()
        if not content:
            continue
        if (
            "strict json" in content
            or "json only" in content
            or "return json" in content
            or "respond with json" in content
        ):
            return True
    return False


def is_text_response_log_context(log_context: str) -> bool:
    return isinstance(log_context, str) and log_context.startswith(
        ("response_generation_", "operator_response_generation_")
    )


# 2400 (orig) → 4800 (2026-05-05, burns of 400-1500) → 9600 (2026-08-10):
# reasoning burns grow with the models. deepseek-v4-flash burned 4,800
# tokens of pure chain-of-thought on the W33 narrative authoring and
# returned EMPTY content twice per story doc — the retry fired correctly
# and its own cap was the limit. The cap must clear the observed burn
# class WITH room to write, and must sit ABOVE the largest initial budget
# that leans on this net (narrative author, 8000) or the guard at the
# retry site (bumped > current) silently disables the net for exactly the
# heaviest lane. Pinned in tests/ai/test_reasoning_burn_ceiling.py.
RETRY_BUDGET_CAP = 9600

# 2026-08-22 — the 04:00 family ("deep" lanes): fire-and-forget, heavy prompts,
# a reasoning model whose burn is the variable. At 04:02 the stake review spent
# its whole 4,000-token budget thinking (33.6 s ≈ 120 tok/s), the net bumped it
# ONCE to 9,600 — and a 9,600-token think needs ~80 s, so the 60 s patient
# ceiling killed all three retries. Two laws follow for this family only:
#   1. time grows with the budget: DEEP_REQUEST_TIMEOUT_SECONDS (240 s per
#      attempt ≈ 16k+ tokens even on a slow night) — see the class below;
#   2. the budget keeps DOUBLING until the answer lands, within reason:
#      4000 → 8000 → 16000, then it stops (DEEP_BUDGET_BUMPS_MAX bumps, capped).
# Every other lane keeps the single ×4 bump — byte-identical.
_DEEP_LANE_PREFIXES = (
    "night_cycle_",
    "consolidation_",
    "monologue_weekly_digest_",
    "monologue_monthly_digest_",
    "narrative_self_",
    "passions_",
    "stake_review",
    "owner_picture",
)
DEEP_RETRY_BUDGET_CAP = 16000
DEEP_BUDGET_BUMPS_MAX = 2


def is_deep_lane(log_context) -> bool:
    return isinstance(log_context, str) and log_context.startswith(_DEEP_LANE_PREFIXES)


# 2026-08-23 — lanes that must NEVER think. deepseek-v4-flash reasons by
# default (17 reasoning tokens for "is 7 prime?"); with
# ``thinking: {"type": "disabled"}`` on the request it answers in ~0.7 s with
# zero reasoning (probed live 03:06). The two steering judges are yes/no
# decisions on the path he is waiting on — 02:49 the mid-task judge burned
# its whole 700-token budget on thought, the net bumped it, and the holding
# line took 38 s. Providers that honour the field opt in
# (SUPPORTS_THINKING_TOGGLE); everything else is byte-identical.
# 03:4x the same night — the side-by-side (docs/2026-08-23-side-by-side-
# thinking.md): cadence with thinking = 5.9 s median and ELEVEN of 24 calls
# empty at its 600-token cap (the live "[agent_cadence_shape] Truncated"
# warnings); without = 2.1 s, 0 empty, votes at least as good. turn_read
# 4.7 → 1.5 s, the same read. Both join: ~7 s off every reply's pre-phase.
# 2026-09-03: + reply_govern — the reply-length compressor. On DeepSeek it spent
# 5,966 reasoning tokens on a 203-word compression, returned nothing, timed out
# twice, and after 110 s the sentence-boundary trim cut his architecture answer
# in half. A rewrite to a word cap is mechanical; thinking there is waste.
# 2026-09-07 (his call) + the six background lanes: reflection, stake appraisal,
# present moment, daydream, curiosity, the inner monologue. Thu→Mon on DeepSeek they
# hit the reasoning ceiling 40+ times (reflection 17k tokens then two timeouts;
# present moment 14 empties in one day) and that spend emptied the balance on
# Thu 04:07 mid-rail. The LLM still makes each judgment; it skips the scratch
# work, as the judges have since 08-23. Her reply and the 04:00 deep lanes keep
# thinking.
_NO_THINK_LANE_PREFIXES = (
    "steering_judgment", "steering_supersede", "agent_cadence_shape", "turn_read", "reply_govern",
    # 2026-09-11: the promise gate's judge — he is waiting on the reply it guards.
    "promise_check",
    "reflect_turn", "stake_appraisal", "present_moment_cycle", "daydream_tick", "curiosity_research",
    "inner_monologue_tick",
    # 2026-09-08 (his call): the nightly disclosure harvest — empty on reasoning burn
    # (8k → 16k tokens) every night 09-03 → 09-07, planted nothing. Still a DEEP lane
    # for its clock and budget; it just stops thinking.
    "night_cycle_disclosure_harvest",
    # 2026-09-30 (the owner's "let's resolve them", on the 09-08 precedent): three more
    # night lanes empty on reasoning burn — the memory reconcile 4 of 5 nights
    # (failed outright 09-30), the interests author 3 of 5, the shared-language
    # harvest ~30k reasoning tokens before a retry saved it. Found by Batch 6's
    # outcome health check on its first night. Still DEEP lanes; they stop thinking.
    "consolidation_reconcile", "passions_author", "night_cycle_callbacks_harvest",
    # 2026-10-05 (the owner: "yes, turn off thinking for that"): three more night
    # lanes spent their whole thinking budget at 04:00 and came back empty before a
    # bigger-budget retry saved them, with the balance at $0.66 — the self-pages
    # gate, the stake review and tonight's pick. They still judge and pick.
    "narrative_self_gate", "stake_review", "night_cycle_tonight_pick",
    # 2026-10-05 06:58 (the owner: "yes, go ahead"): three summaries that kept running
    # out of thinking budget since 09-06 (the fact gist 11 times, the weekly monologue
    # digest 5, the monthly 1) and the arbiter's freshness check, a yes/no judge.
    "consolidation_fact_gist", "monologue_weekly_digest", "monologue_monthly_digest",
    "arbiter_freshness",
    # 2026-10-12: the presence report's judge (palli_ctl presence --judge), a yes/no
    # with a quote on a 200-token budget: 10 of 24 came back empty with thinking on.
    "presence_judge",
    # 2026-10-12 (R14, the one first look): one JSON look in her own voice takes
    # the place of the quick vote and the first read, both no-think since 08-23.
    "first_look",
    # 2026-10-13 (the presence slice, item 4): a group opener is one JSON answer on its
    # own, nobody waiting; like the first look it must not think.
    "group_opener",
    # 2026-10-13 (R15): the second look inside a reply, and its judge
    "memory_reach_widen", "memory_reach_keep", "memory_reach_judge",
)


def is_no_think_lane(log_context) -> bool:
    return isinstance(log_context, str) and log_context.startswith(_NO_THINK_LANE_PREFIXES)


def budget_bumps_allowed(log_context) -> int:
    """How many times the empty+length / max_tokens-400 net may raise the
    budget on one call: the 04:00 family keeps doubling (2), everyone else
    bumps once."""
    return DEEP_BUDGET_BUMPS_MAX if is_deep_lane(log_context) else 1


def retry_completion_budget(
    current_max_tokens: int,
    *,
    log_context: str,
    expects_json: bool,
) -> int:
    current = max(int(current_max_tokens or 0), 1)
    if is_deep_lane(log_context):
        return min(max(current * 2, 4000), DEEP_RETRY_BUDGET_CAP)
    if is_text_response_log_context(log_context):
        return min(max(current * 6, 1500), RETRY_BUDGET_CAP)
    if expects_json:
        return min(max(current * 4, 1500), RETRY_BUDGET_CAP)
    return min(max(current * 4, 1200), RETRY_BUDGET_CAP)


def is_max_tokens_400_error(error_text: str) -> bool:
    """Detect OpenAI's pre-flight 400 that fires when max_tokens is too low
    for a reasoning model to fit any visible output past its chain-of-thought.

    The body shape is JSON with an "error.message" field; rather than parsing
    we substring-match the canonical message text. Matches both the literal
    English message OpenAI currently returns and a couple of well-known
    paraphrases that have shipped over time.
    """
    if not error_text:
        return False
    haystack = error_text.lower()
    return any(
        marker in haystack
        for marker in (
            "max_tokens or model output limit was reached",
            "model output limit was reached",
            "could not finish the message because max_tokens",
            "increase max_tokens to a sufficient value",
        )
    )


def _should_log_response_call(log_context: str) -> bool:
    return bool(getattr(config, "LOG_LLM_RESPONSE_CALLS", False)) and is_text_response_log_context(log_context)


def write_response_call_log(payload: Dict[str, Any], log_context: str) -> None:
    if not _should_log_response_call(log_context):
        return

    try:
        os.makedirs("LLM-response-logs", exist_ok=True)
        timestamp = time.time_ns()
        path = os.path.join("LLM-response-logs", f"LLM_CALL_{timestamp}.log")
        with open(path, "w", encoding="utf-8") as log_file:
            log_file.write(json.dumps(payload, indent=2, ensure_ascii=True))
    except Exception as exc:
        logger.error("Failed to write LLM response log: %s", exc)


def extract_stream_delta_text(choice: Any) -> str:
    if not isinstance(choice, dict):
        return ""

    delta = choice.get("delta")
    if not isinstance(delta, dict):
        return ""

    content = delta.get("content")
    if isinstance(content, str):
        return content

    if isinstance(content, list):
        parts: List[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
                continue
            if not isinstance(item, dict):
                continue
            text = item.get("text")
            if isinstance(text, str):
                parts.append(text)
        return "".join(parts)

    return ""


@dataclass
class CompletionSuccessResult:
    content: str
    usage: Dict[str, Any]
    finish_reason: Optional[str] = None
    retry_payload: Optional[Dict[str, Any]] = None


# ---------------------------------------------------------------------------
# Why the last call came back with nothing (first door, task 2, fix round 2)
#
# `chat_completion` returns None on every failure and NEVER raises — her live
# turns depend on that and it has not changed. But "None" is not a sentence
# anybody can act on, and it is the only thing the caller gets: the branches
# below know the status code and the provider's own message, log them, and
# then throw them away. On 2026-09-17 that turned a real DeepSeek 401 into
# "empty response" on the setup door, where spec §3's first line is "Wrong
# key: the provider's own error text under the field".
#
# So the branches that already LOG the reason also record it here, and a
# caller that wants it reads `provider.last_failure` after an empty reply.
# Cleared at the top of every call, so a stale one can never be reported for a
# later reply. One slot per provider object: it is for a caller that owns the
# provider for the length of one call (the setup probe builds a throwaway), not
# for reading off the shared singleton mid-flight.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProviderFailure:
    kind: str       # http | timeout | network | credentials | error
    status: int     # the HTTP status, or 0
    message: str    # the provider's own words, as far as they were given
    sentence: str   # the whole thing, ready to show a person


def provider_error_message(body_text: str) -> str:
    """The message out of an OpenAI-shaped error body, or the body itself.

    Every provider the door offers answers a bad key with
    `{"error": {"message": "...", ...}}`; a local server may answer with
    anything at all, so the raw text is the fallback, trimmed to a sentence
    rather than a page.
    """
    text = str(body_text or "").strip()
    if not text:
        return ""
    try:
        payload = json.loads(text)
    except Exception:
        payload = None
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict) and str(error.get("message") or "").strip():
            return str(error["message"]).strip()
        if isinstance(error, str) and error.strip():
            return error.strip()
        for key in ("message", "detail"):
            if str(payload.get(key) or "").strip():
                return str(payload[key]).strip()
    return text[:400]


def _count(value: Any) -> int:
    """A usage count as an int; ``None`` or a non-numeric value is 0 -- a
    counter never fails a billed request (whole-branch review, 2026-09-27).
    The twin of ``core.agent.llm_client._count``; the stacks do not import
    each other."""
    try:
        return int(value or 0)
    except (TypeError, ValueError, OverflowError):
        return 0


def build_openai_completion_success_result(
    *,
    content: str,
    finish_reason: Optional[str],
    usage: Dict[str, Any],
    message: Dict[str, Any],
    model: str,
    messages: List[Dict[str, Any]],
    payload: Dict[str, Any],
    log_context: str,
    budget_retry_used: bool = False,
    budget_bumps: Optional[int] = None,
) -> CompletionSuccessResult:
    # Providers disagree on the field name for visible chain-of-thought:
    # OpenAI-style reasoners use "reasoning", DeepSeek uses "reasoning_content".
    # Read both — this value feeds the RUNTIME half of the reasoning check
    # below, which is what catches models the prefix list doesn't name. Reading
    # only "reasoning" left reasoning_chars permanently 0 for deepseek-v4-flash
    # (the default on the proactive, reflection AND memory lanes), so the
    # empty+length retry never fired for the model that trips it most: 1,110
    # "reasoning_content" vs 2 "reasoning" in the logs, and the same bug
    # hand-fixed three times (87cc2d6, 6146914, 17ea054).
    reasoning_text = message.get("reasoning") or message.get("reasoning_content")
    if not isinstance(reasoning_text, str):
        reasoning_text = ""

    current_budget = int(payload.get("max_completion_tokens") or payload.get("max_tokens") or 0)
    is_empty_text = isinstance(content, str) and not content.strip()
    reasoning_chars = len(reasoning_text.strip())
    is_reasoning_model = is_reasoning_prone_model(model) or reasoning_chars > 0
    expects_json = messages_expect_json(messages)
    truncated_json = bool(
        finish_reason == "length"
        and expects_json
        and isinstance(content, str)
        and content.strip()
        and safe_parse_json_object(content) is None
    )
    # finish_reason=length with NONEMPTY non-JSON prose from a reasoning model
    # is a truncation, not an answer: on 2026-08-02 the night lane spent 387 of
    # its 400 tokens on chain-of-thought and the surviving 54-char mid-clause
    # fragment was stored as a success and logged as one. The empty-check above
    # cannot see that mode.
    truncated_prose = bool(
        finish_reason == "length"
        and not expects_json
        and isinstance(content, str)
        and content.strip()
        and is_reasoning_model
    )

    bumps_so_far = int(budget_bumps) if budget_bumps is not None else (1 if budget_retry_used else 0)
    bumps_allowed = budget_bumps_allowed(log_context)
    if (
        finish_reason == "length"
        and bumps_so_far < bumps_allowed
        and ((is_empty_text and is_reasoning_model) or truncated_json or truncated_prose)
    ):
        bumped = retry_completion_budget(
            current_budget,
            log_context=log_context,
            expects_json=expects_json,
        )
        if bumped > current_budget:
            retry_payload = dict(payload)
            if "max_completion_tokens" in retry_payload:
                retry_payload["max_completion_tokens"] = bumped
            else:
                retry_payload["max_tokens"] = bumped
            logger.warning(
                "[%s] Truncated completion due to length (content_len=%s, reasoning_len=%s, expects_json=%s); retrying with max_tokens=%s (budget bump %s of %s)",
                log_context,
                len(content or ""),
                reasoning_chars,
                expects_json,
                bumped,
                bumps_so_far + 1,
                bumps_allowed,
            )
            return CompletionSuccessResult(
                content="",
                usage=usage,
                finish_reason=finish_reason,
                retry_payload=retry_payload,
            )

    return CompletionSuccessResult(
        content=content,
        usage=usage,
        finish_reason=finish_reason,
    )


class BaseLLMProvider(ABC):
    """Base class for LLM providers."""

    MAX_RETRIES = 3
    BASE_DELAY = 1.0
    MAX_DELAY = 30.0
    RETRYABLE_STATUS_CODES: Set[int] = {429, 500, 502, 503, 504}

    def __init__(self):
        self.session = None
        self.provider_name = "base"
        self.log_label = "LLM"
        self.base_url = ""
        self.default_model = ""
        self.supports_vision = False
        # Phase 8c: optional credential pool for cooldown-aware key
        # rotation. Subclasses populate this in their __init__ when they
        # have one or more API keys; providers that authenticate via
        # external CLIs (Codex, Claude Code) leave it empty.
        self.credential_pool: CredentialPool = CredentialPool()
        # Snapshot of self.headers from before per-request mutation.
        # Subclasses set this once during __init__ for the non-Auth bits
        # (Content-Type, etc.) and we layer Authorization on top per
        # request via `_headers_with_credential`.
        self._base_headers: Dict[str, str] = {"Content-Type": "application/json"}
        # Why the last chat_completion came back with nothing (see
        # ProviderFailure). None means "nothing has failed since the last
        # call started" — a reply that is empty with this still None is a
        # genuinely empty answer, not a swallowed error.
        self.last_failure: Optional[ProviderFailure] = None

    def _note_failure(
        self, kind: str, *, sentence: str, status: int = 0, message: str = ""
    ) -> None:
        """Record why this call is about to return None. Never raises: a
        bookkeeping slot must not be able to break a live turn."""
        try:
            self.last_failure = ProviderFailure(
                kind=str(kind), status=int(status or 0),
                message=str(message or ""), sentence=str(sentence or ""),
            )
        except Exception:  # pragma: no cover - defensive
            self.last_failure = None

    async def _get_session(self):
        # 2026-05-06: handle event-loop boundary correctly. The aiohttp
        # ClientSession is bound to whichever loop created it. When the
        # caller is on a DIFFERENT loop (test harnesses, subprocess
        # event-loop creation), the session can be `not closed` but its
        # internal connector belongs to a dead loop, and the next request
        # raises "Event loop is closed". Detect that and recreate.
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession()
            return self.session
        # Cross-loop check
        try:
            import asyncio as _asyncio

            current_loop = _asyncio.get_running_loop()
            session_loop = getattr(
                getattr(self.session, "_connector", None),
                "_loop",
                None,
            )
            if session_loop is not None and session_loop is not current_loop:
                # Session's loop is not the one we're running in. Try to
                # close the old session, then make a fresh one for this loop.
                try:
                    await self.session.close()
                except Exception:
                    pass
                self.session = aiohttp.ClientSession()
        except RuntimeError:
            # No running loop — let aiohttp deal with it on actual call
            pass
        return self.session

    async def _iter_sse_payloads(self, response: aiohttp.ClientResponse):
        data_lines: List[str] = []
        while True:
            raw_line = await response.content.readline()
            if not raw_line:
                if data_lines:
                    yield "\n".join(data_lines)
                return

            line = raw_line.decode("utf-8", errors="ignore").rstrip("\r\n")
            if not line:
                if data_lines:
                    yield "\n".join(data_lines)
                    data_lines = []
                continue

            if line.startswith(":"):
                continue

            if line.startswith("data:"):
                data_lines.append(line[5:].lstrip())

    def _calculate_retry_delay(self, attempt: int, retry_after: Optional[float] = None) -> float:
        if retry_after is not None:
            return min(retry_after + random.uniform(0, 1), self.MAX_DELAY)

        delay = self.BASE_DELAY * (2 ** attempt) + random.uniform(0, 1)
        return min(delay, self.MAX_DELAY)

    def _is_retryable_error(self, status_code: int) -> bool:
        return status_code in self.RETRYABLE_STATUS_CODES

    def _parse_retry_after(self, headers: dict) -> Optional[float]:
        retry_after = headers.get("Retry-After") or headers.get("retry-after")
        if retry_after:
            try:
                return float(retry_after)
            except (ValueError, TypeError):
                pass
        return None

    async def _emit_telemetry(
        self,
        callback: Optional[Callable[[Dict[str, Any]], Awaitable[None]]],
        payload: Dict[str, Any],
    ) -> None:
        if callback is None:
            return
        try:
            result = callback(payload)
            if asyncio.iscoroutine(result):
                await result
        except Exception as exc:
            logger.debug("Failed to deliver LLM telemetry callback: %s", exc)

    async def _emit_request_telemetry(
        self,
        callback: Optional[Callable[[Dict[str, Any]], Awaitable[None]]],
        *,
        model: Optional[str],
        streamed: bool,
        log_context: str,
        latency_seconds: float,
        provider_retry_count: int,
        success: bool,
        usage: Optional[Dict[str, Any]] = None,
        error: Optional[str] = None,
        finish_reason: Optional[str] = None,
    ) -> None:
        payload: Dict[str, Any] = {
            "provider": self.provider_name,
            "model": model,
            "streamed": streamed,
            "log_context": log_context,
            "latency_seconds": latency_seconds,
            "usage": usage or {},
            "provider_retry_count": provider_retry_count,
            "success": success,
        }
        if error is not None:
            payload["error"] = error
        if finish_reason is not None:
            payload["finish_reason"] = finish_reason
        await self._emit_telemetry(callback, payload)

    @abstractmethod
    async def chat_completion(
        self,
        messages: List[Dict],
        model: str = None,
        temperature: float = 0.7,
        max_tokens: int = 2000,
        log_context: str = "general",
        telemetry_callback: Optional[Callable[[Dict[str, Any]], Awaitable[None]]] = None,
    ) -> Optional[str]:
        pass

    async def chat_completion_stream(
        self,
        messages: List[Dict],
        model: str = None,
        temperature: float = 0.7,
        max_tokens: int = 2000,
        log_context: str = "general",
        on_delta: Optional[Callable[[str], Awaitable[None]]] = None,
        telemetry_callback: Optional[Callable[[Dict[str, Any]], Awaitable[None]]] = None,
    ) -> Optional[str]:
        content = await self.chat_completion(
            messages=messages,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            log_context=log_context,
            telemetry_callback=telemetry_callback,
        )
        if content and on_delta is not None:
            await on_delta(content)
        return content

    @abstractmethod
    async def vision_completion(
        self,
        image_base64: str,
        prompt: str,
        conversation_history: List[Dict] = None,
        model: str = None,
        temperature: float = 0.7,
        max_tokens: int = 1000,
        content_type: str = "image/jpeg",
    ) -> Optional[Dict]:
        pass

    async def close(self):
        if self.session and not self.session.closed:
            await self.session.close()

    def is_configured(self) -> bool:
        return True

    def diagnostics(self) -> Dict[str, Any]:
        result: Dict[str, Any] = {
            "provider": self.provider_name,
            "label": self.log_label,
            "configured": self.is_configured(),
            "default_model": self.default_model,
            "base_url": self.base_url,
            "supports_vision": self.supports_vision,
        }
        # Phase 8c: surface pool state when there's >1 key. The pool
        # never includes raw key values in its diagnostics output —
        # only fingerprints and counters — so this is log-safe.
        if self.credential_pool.is_multi_key:
            result["credential_pool"] = self.credential_pool.diagnostics()
        return result

    # =========================================================================
    # Phase 8c — credential-pool helpers shared by all OpenAI-compatible providers.
    # Providers without a pool (e.g. CLI-based Codex, Claude Code) just
    # never populate self.credential_pool; the helpers no-op for them.
    # =========================================================================

    async def _acquire_credential(self) -> Optional[str]:
        """Get the next available key from the pool.

        Returns None when every key is cooling down — the caller should
        fail the request. Returns the (possibly empty) `self.api_key` when
        no pool is configured, preserving pre-Phase-8c behavior for
        providers that haven't been migrated.
        """
        if self.credential_pool.is_empty:
            return getattr(self, "api_key", "") or None
        return await self.credential_pool.acquire()

    def _headers_with_credential(self, credential: Optional[str]) -> Dict[str, str]:
        """Build per-request headers from the base headers + Authorization.

        Per-request construction (rather than mutating `self.headers`)
        keeps concurrent requests from stepping on each other's
        Authorization values during key rotation.
        """
        headers = dict(self._base_headers)
        if credential:
            headers["Authorization"] = f"Bearer {credential}"
        return headers

    async def _record_credential_outcome(
        self,
        credential: Optional[str],
        *,
        status_code: Optional[int],
        success: bool,
    ) -> None:
        """Inform the pool how a request went, so it can rotate appropriately.

        - Success → reset the key's cooldown (it's healthy).
        - 401/429 → cool the key down so the next attempt rotates away.
        - Other 5xx / network errors → leave the pool unchanged. Those
          are server-side problems, not credential problems; rotating
          to a fresh key wouldn't help.
        """
        if not credential or self.credential_pool.is_empty:
            return
        if success:
            await self.credential_pool.mark_success(credential)
        elif status_code in CREDENTIAL_FAILURE_STATUS_CODES:
            await self.credential_pool.mark_failure(credential, status_code=status_code)


class OpenAICompatibleProvider(BaseLLMProvider):
    """Shared execution path for OpenAI-compatible chat completion providers."""

    REQUEST_TIMEOUT_SECONDS = 30
    # 2026-08-09 patient/snappy split: background organ lanes legitimately
    # think for 30-60s on reasoning models; giving up at 30s four times in a
    # row was the dominant hard-failure family in her logs (~daily). Lanes a
    # human is waiting on keep the 30s ceiling.
    PATIENT_REQUEST_TIMEOUT_SECONDS = 60
    DEEP_REQUEST_TIMEOUT_SECONDS = 240   # the 04:00 family — see _DEEP_LANE_PREFIXES
    # Honours ``thinking: {"type": "disabled"}`` on the request (DeepSeek). OpenAI
    # rejects unknown request arguments, so the base stays False.
    SUPPORTS_THINKING_TOGGLE = False
    STREAM_TIMEOUT_SECONDS = 60

    # Explicit allowlist — SNAPPY is the safe default. A new lane nobody
    # classifies stays fast and fails fast; nothing a human waits on can
    # silently inherit the patient ceiling. Add prefixes here deliberately.
    # The daytime background lanes: nobody waits on them, but they tick every
    # few minutes and must not pile up — 60 s. (The 04:00 family moved to the
    # DEEP tier 2026-08-22: see DEEP_LANE_PREFIXES.)
    PATIENT_LANE_PREFIXES = (
        "inner_monologue_",
        "present_moment_cycle",
        "stake_appraisal",
        "daydream_",
        "curiosity_research",
        "arbiter_freshness",
        # 2026-08-21 — her post-turn reflect lane had never named itself, ran as
        # `general` at the 30 s human ceiling for four months (25 exchanges
        # reflected on by nobody, 08-09→08-21). Named `reflect_turn`, patient.
        # tests/ai/test_request_timeout_lanes.py scans every provider call
        # and refuses an unnamed or unclassified lane.
        "reflect_",
        "presence_judge",       # palli_ctl presence --judge, by hand (2026-10-12)
        "group_opener",         # PaLLi starting something in a group, on its own (2026-10-13)
    )
    # The 04:00 family: 240 s per attempt, and the budget keeps doubling
    # (module-level _DEEP_LANE_PREFIXES carries the list; the story is there).
    DEEP_LANE_PREFIXES = _DEEP_LANE_PREFIXES

    @classmethod
    def _ladder_ceiling_seconds(cls, per_attempt: float) -> float:
        backoff = sum(
            min(cls.BASE_DELAY * (2 ** attempt) + 1.0, cls.MAX_DELAY)
            for attempt in range(cls.MAX_RETRIES)
        )
        return float((cls.MAX_RETRIES + 1) * per_attempt + backoff)

    @classmethod
    def deep_ladder_ceiling_seconds(cls) -> float:
        """The wall-clock a DEEP lane can legitimately take before the provider
        gives up: 4 × 240 + backoff (2+3+5) = 970. The 04:00 organs' outer
        envelopes (`asyncio.wait_for`) must clear this."""
        return cls._ladder_ceiling_seconds(cls.DEEP_REQUEST_TIMEOUT_SECONDS)

    @classmethod
    def patient_ladder_ceiling_seconds(cls) -> float:
        """The wall-clock a patient lane can legitimately take before the
        provider itself gives up: (MAX_RETRIES + 1) attempts at the patient
        ceiling plus the worst-case backoff between them. An organ's OUTER
        envelope (`asyncio.wait_for`) must clear this or it kills the last
        attempt the provider was about to win — the 240 s the stake review
        and the owner picture shipped with could not (4×60 + 2+3+5 = 250)."""
        return cls._ladder_ceiling_seconds(cls.PATIENT_REQUEST_TIMEOUT_SECONDS)

    @classmethod
    def request_timeout_for_context(cls, log_context) -> float:
        if isinstance(log_context, str) and log_context.startswith(cls.DEEP_LANE_PREFIXES):
            return float(cls.DEEP_REQUEST_TIMEOUT_SECONDS)
        if isinstance(log_context, str) and log_context.startswith(
            cls.PATIENT_LANE_PREFIXES
        ):
            return float(cls.PATIENT_REQUEST_TIMEOUT_SECONDS)
        return float(cls.REQUEST_TIMEOUT_SECONDS)

    @classmethod
    def ladder_ceiling_for_context(cls, log_context: str) -> float:
        """The wall-clock ceiling of the WHOLE retry ladder this lane's
        per-attempt timeout belongs to (DEEP, PATIENT, or the default
        SNAPPY tier) so an organ's outer envelope can be derived from
        its own lane's ladder instead of a hand-picked number that can
        fall behind it if the ladder ever changes (2026-09-28)."""
        return cls._ladder_ceiling_seconds(cls.request_timeout_for_context(log_context))

    @abstractmethod
    def _build_chat_payload(
        self,
        *,
        model: str,
        messages: List[Dict],
        temperature: float,
        max_tokens: int,
        stream: bool,
    ) -> Dict[str, Any]:
        pass

    def _initial_completion_state(self) -> Dict[str, Any]:
        return {"budget_retry_used": False, "budget_bumps": 0}

    def _completion_endpoint(self) -> str:
        return f"{self.base_url}/chat/completions"

    def _extract_text_completion_parts(
        self,
        data: Dict[str, Any],
        *,
        log_context: str,
    ) -> tuple[str, Optional[str], Dict[str, Any], Dict[str, Any]]:
        choice = (data.get("choices") or [{}])[0] if isinstance(data, dict) else {}
        message = choice.get("message") or {}
        finish_reason = choice.get("finish_reason")
        content = message.get("content")

        if content is None:
            logger.warning(
                "[%s] %s returned null content (finish_reason=%s)",
                log_context,
                self.log_label,
                finish_reason,
            )
            normalized_content = ""
        elif isinstance(content, str):
            normalized_content = content
            if not normalized_content.strip():
                logger.warning(
                    "[%s] %s returned empty content (finish_reason=%s)",
                    log_context,
                    self.log_label,
                    finish_reason,
                )
        else:
            logger.warning(
                "[%s] %s returned non-string content type=%s (finish_reason=%s); coercing to string",
                log_context,
                self.log_label,
                type(content).__name__,
                finish_reason,
            )
            normalized_content = str(content)

        usage = data.get("usage", {}) if isinstance(data, dict) else {}
        return normalized_content, finish_reason, usage, message

    def _parse_completion_success(
        self,
        *,
        data: Dict[str, Any],
        model: str,
        messages: List[Dict],
        payload: Dict[str, Any],
        log_context: str,
        state: Dict[str, Any],
    ) -> CompletionSuccessResult:
        # The budget-retry fallback lives HERE, on the shared path, on purpose.
        # It used to be an OpenAIProvider override, which meant DeepSeekProvider
        # — the class every 4 AM organ actually runs on — inherited a naive
        # parse that warned and returned "" with no retry. Measured cost over
        # Aug 2-4: inner_monologue_tick lost 64% of its calls, stake_appraisal
        # 47%, open_threads_surface_decision 25%, and the nights of 07-30/07-31
        # were lost outright.
        content, finish_reason, usage, message = self._extract_text_completion_parts(
            data,
            log_context=log_context,
        )
        result = build_openai_completion_success_result(
            content=content,
            finish_reason=finish_reason,
            usage=usage,
            message=message,
            model=model,
            messages=messages,
            payload=payload,
            log_context=log_context,
            budget_retry_used=bool(state.get("budget_retry_used")),
            budget_bumps=int(state.get("budget_bumps") or 0),
        )
        if result.retry_payload is not None:
            state["budget_retry_used"] = True
            state["budget_bumps"] = int(state.get("budget_bumps") or 0) + 1
        return result

    async def chat_completion(
        self,
        messages: List[Dict],
        model: str = None,
        temperature: float = 0.7,
        max_tokens: int = 2000,
        log_context: str = "general",
        telemetry_callback: Optional[Callable[[Dict[str, Any]], Awaitable[None]]] = None,
    ) -> Optional[str]:
        if model is None:
            model = self.default_model

        logger.info("🧠 [%s] Sending request (Model: %s, Context: %s)...", self.log_label, model, log_context)
        debug_logger.debug(
            "[%s] %s Request - Model: %s, Temp: %s, MaxTokens: %s",
            log_context,
            self.log_label,
            model,
            temperature,
            max_tokens,
        )
        debug_logger.debug("[%s] %s Messages: %s", log_context, self.log_label, json.dumps(messages, indent=2))

        session = await self._get_session()
        payload = self._build_chat_payload(
            model=model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            stream=False,
        )
        if self.SUPPORTS_THINKING_TOGGLE and is_no_think_lane(log_context):
            payload["thinking"] = {"type": "disabled"}   # a yes/no judge never thinks (2026-08-23)
        write_response_call_log(payload, log_context)

        last_error = None
        # Cleared here, so what a caller reads afterwards is about THIS call.
        self.last_failure = None
        state = self._initial_completion_state()
        request_timeout = self.request_timeout_for_context(log_context)
        for attempt in range(self.MAX_RETRIES + 1):
            # Phase 8c: acquire a credential per attempt. If the previous
            # attempt cooled the key down (401/429), the pool rotates to
            # the next available key here.
            current_credential = await self._acquire_credential()
            if self.credential_pool.is_multi_key and not current_credential:
                logger.error(
                    "[%s] %s all credentials cooling down — cannot make request",
                    log_context,
                    self.log_label,
                )
                await self._emit_request_telemetry(
                    telemetry_callback,
                    model=model,
                    streamed=False,
                    log_context=log_context,
                    latency_seconds=0.0,
                    provider_retry_count=attempt,
                    success=False,
                    error="all_credentials_cooling",
                )
                self._note_failure(
                    "credentials",
                    sentence=(
                        f"every {self.log_label} key is cooling down after an earlier "
                        f"failure — none could be used for this request"
                    ),
                )
                return None
            request_headers = self._headers_with_credential(current_credential)

            try:
                start_time = time.time()
                async with session.post(
                    self._completion_endpoint(),
                    headers=request_headers,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=request_timeout),
                ) as response:
                    elapsed = time.time() - start_time

                    if response.status == 200:
                        data = await response.json()
                        result = self._parse_completion_success(
                            data=data,
                            model=model,
                            messages=messages,
                            payload=payload,
                            log_context=log_context,
                            state=state,
                        )
                        # The provider's prompt cache, per lane (Cut the Cost, Task 1):
                        # every billed 200 counts, a budget-retried one too.
                        usage = result.usage if isinstance(result.usage, dict) else {}
                        logger.info(
                            "[%s] cache_hit=%d cache_miss=%d",
                            log_context,
                            _count(usage.get("prompt_cache_hit_tokens")),
                            _count(usage.get("prompt_cache_miss_tokens")),
                        )
                        # every billed call, one line (the owner, 10-05: "call by call")
                        call_ledger.record_call(
                            lane=log_context, provider=self.provider_name, model=model,
                            prompt=_count(usage.get("prompt_tokens")),
                            hit=call_ledger.cached_tokens(usage),
                            out=_count(usage.get("completion_tokens")),
                        )
                        if result.retry_payload is not None:
                            payload = result.retry_payload
                            write_response_call_log(payload, log_context)
                            continue

                        await self._record_credential_outcome(
                            current_credential, status_code=200, success=True,
                        )
                        debug_logger.debug("[%s] %s Response (%.2fs):", log_context, self.log_label, elapsed)
                        debug_logger.debug("[%s] Content: %s", log_context, result.content)
                        debug_logger.debug("[%s] Full Response: %s", log_context, json.dumps(data, indent=2))
                        debug_logger.debug("[%s] Usage: %s", log_context, result.usage)
                        await self._emit_request_telemetry(
                            telemetry_callback,
                            model=model,
                            streamed=False,
                            log_context=log_context,
                            latency_seconds=elapsed,
                            provider_retry_count=attempt,
                            success=True,
                            usage=result.usage,
                            finish_reason=result.finish_reason,
                        )
                        return result.content

                    error_text = await response.text()
                    # Cool the key down if 401/429 — the next iteration
                    # will rotate to a different key (when the pool has one).
                    await self._record_credential_outcome(
                        current_credential,
                        status_code=response.status,
                        success=False,
                    )

                    # 400 with the "max_tokens too low for reasoning" signature
                    # is recoverable: bump the budget once and retry. This
                    # mirrors the retry-on-empty-content path that handles the
                    # equivalent post-200 failure mode (finish_reason=length
                    # with no visible output). gpt-5-mini and other reasoning
                    # models can burn the entire output budget on chain-of-
                    # thought; OpenAI's pre-flight check rejects with 400 when
                    # it judges the budget too small to produce visible content.
                    # Without this, callers like inner_monologue_tick that pass
                    # a small max_tokens would silently fail every tick.
                    if (
                        response.status == 400
                        and int(state.get("budget_bumps") or 0) < budget_bumps_allowed(log_context)
                        and is_max_tokens_400_error(error_text)
                        and is_reasoning_prone_model(model)
                        and attempt < self.MAX_RETRIES
                    ):
                        current_budget = int(
                            payload.get("max_completion_tokens")
                            or payload.get("max_tokens")
                            or 0
                        )
                        expects_json = messages_expect_json(messages)
                        bumped = retry_completion_budget(
                            current_budget,
                            log_context=log_context,
                            expects_json=expects_json,
                        )
                        if bumped > current_budget:
                            retry_payload = dict(payload)
                            if "max_completion_tokens" in retry_payload:
                                retry_payload["max_completion_tokens"] = bumped
                            else:
                                retry_payload["max_tokens"] = bumped
                            payload = retry_payload
                            state["budget_retry_used"] = True
                            state["budget_bumps"] = int(state.get("budget_bumps") or 0) + 1
                            logger.warning(
                                "[%s] %s API 400 (max_tokens too low for "
                                "reasoning model %s); retrying with "
                                "max_tokens=%s",
                                log_context,
                                self.log_label,
                                model,
                                bumped,
                            )
                            write_response_call_log(payload, log_context)
                            continue

                    if self._is_retryable_error(response.status) and attempt < self.MAX_RETRIES:
                        retry_after = self._parse_retry_after(dict(response.headers))
                        delay = self._calculate_retry_delay(attempt, retry_after)
                        logger.warning(
                            "[%s] %s API error %s, retrying in %.1fs (attempt %s/%s)",
                            log_context,
                            self.log_label,
                            response.status,
                            delay,
                            attempt + 1,
                            self.MAX_RETRIES + 1,
                        )
                        await asyncio.sleep(delay)
                        continue

                    logger.error("[%s] %s API error %s: %s", log_context, self.log_label, response.status, error_text)
                    debug_logger.error("[%s] Request took %.2fs before failing", log_context, elapsed)
                    await self._emit_request_telemetry(
                        telemetry_callback,
                        model=model,
                        streamed=False,
                        log_context=log_context,
                        latency_seconds=elapsed,
                        provider_retry_count=attempt,
                        success=False,
                        error=f"http_{response.status}",
                    )
                    # The words a person needs (first door, task 2, fix round 2):
                    # the status, its reason phrase, and the provider's own
                    # message out of the error body — the three things this
                    # branch already logs and then throws away.
                    provider_message = provider_error_message(error_text)
                    headline = f"{response.status} {str(response.reason or '').strip()}".strip()
                    self._note_failure(
                        "http",
                        status=int(response.status),
                        message=provider_message,
                        sentence=(
                            f"{headline}: {provider_message}" if provider_message else headline
                        ),
                    )
                    return None

            except asyncio.TimeoutError:
                last_error = "timeout"
                if attempt < self.MAX_RETRIES:
                    delay = self._calculate_retry_delay(attempt)
                    logger.warning(
                        "[%s] %s request timeout, retrying in %.1fs (attempt %s/%s)",
                        log_context,
                        self.log_label,
                        delay,
                        attempt + 1,
                        self.MAX_RETRIES + 1,
                    )
                    await asyncio.sleep(delay)
                    continue
                logger.error("[%s] %s request timeout after %s attempts", log_context, self.log_label, self.MAX_RETRIES + 1)
                await self._emit_request_telemetry(
                    telemetry_callback,
                    model=model,
                    streamed=False,
                    log_context=log_context,
                    latency_seconds=request_timeout,
                    provider_retry_count=attempt,
                    success=False,
                    error="timeout",
                )
                self._note_failure(
                    "timeout",
                    sentence=(
                        f"no answer from {self.base_url or self.log_label} within "
                        f"{request_timeout:g}s"
                    ),
                )
                return None

            except (aiohttp.ClientError, OSError) as exc:
                last_error = str(exc)
                if attempt < self.MAX_RETRIES:
                    delay = self._calculate_retry_delay(attempt)
                    logger.warning(
                        "[%s] %s network error: %s, retrying in %.1fs (attempt %s/%s)",
                        log_context,
                        self.log_label,
                        exc,
                        delay,
                        attempt + 1,
                        self.MAX_RETRIES + 1,
                    )
                    await asyncio.sleep(delay)
                    continue
                logger.error(
                    "[%s] %s request failed after %s attempts: %s",
                    log_context,
                    self.log_label,
                    self.MAX_RETRIES + 1,
                    exc,
                )
                await self._emit_request_telemetry(
                    telemetry_callback,
                    model=model,
                    streamed=False,
                    log_context=log_context,
                    latency_seconds=0.0,
                    provider_retry_count=attempt,
                    success=False,
                    error=str(exc),
                )
                self._note_failure(
                    "network",
                    message=str(exc),
                    sentence=(
                        f"could not connect to {self.base_url or self.log_label} — "
                        f"is the server running? ({exc})"
                    ),
                )
                return None

            except Exception as exc:
                # 2026-05-06: aiohttp "Session is closed" can hit on a fresh
                # process startup if the ClientSession was bound to a stale
                # event loop or constructed before the runtime loop started.
                # Self-heal: force-recreate the session and continue the
                # retry loop instead of failing the entire request.
                exc_msg = str(exc)
                if "Session is closed" in exc_msg and attempt < self.MAX_RETRIES:
                    logger.warning(
                        "[%s] %s aiohttp session was closed; recreating and retrying (attempt %d)",
                        log_context,
                        self.log_label,
                        attempt + 1,
                    )
                    try:
                        if self.session is not None and not self.session.closed:
                            await self.session.close()
                    except Exception:
                        pass
                    self.session = None
                    session = await self._get_session()
                    last_error = exc
                    continue

                logger.error("[%s] %s request error: %s", log_context, self.log_label, exc)
                debug_logger.error(
                    "[%s] Exception details: %s: %s",
                    log_context,
                    type(exc).__name__,
                    str(exc),
                )
                debug_logger.error("[%s] Traceback: %s", log_context, traceback.format_exc())
                await self._emit_request_telemetry(
                    telemetry_callback,
                    model=model,
                    streamed=False,
                    log_context=log_context,
                    latency_seconds=0.0,
                    provider_retry_count=attempt,
                    success=False,
                    error=str(exc),
                )
                self._note_failure(
                    "error", message=str(exc), sentence=f"{type(exc).__name__}: {exc}",
                )
                return None

        logger.error("[%s] %s request failed: %s", log_context, self.log_label, last_error)
        if self.last_failure is None:
            self._note_failure(
                "error", message=str(last_error or ""),
                sentence=str(last_error or "the request could not be completed"),
            )
        return None

    async def chat_completion_stream(
        self,
        messages: List[Dict],
        model: str = None,
        temperature: float = 0.7,
        max_tokens: int = 2000,
        log_context: str = "general",
        on_delta: Optional[Callable[[str], Awaitable[None]]] = None,
        telemetry_callback: Optional[Callable[[Dict[str, Any]], Awaitable[None]]] = None,
    ) -> Optional[str]:
        if model is None:
            model = self.default_model

        logger.info("🧠 [%s] Streaming request (Model: %s, Context: %s)...", self.log_label, model, log_context)
        debug_logger.debug(
            "[%s] %s Streaming Request - Model: %s, Temp: %s, MaxTokens: %s",
            log_context,
            self.log_label,
            model,
            temperature,
            max_tokens,
        )
        debug_logger.debug("[%s] %s Streaming Messages: %s", log_context, self.log_label, json.dumps(messages, indent=2))

        session = await self._get_session()
        payload = self._build_chat_payload(
            model=model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            stream=True,
        )
        write_response_call_log(payload, log_context)

        last_error = None
        for attempt in range(self.MAX_RETRIES + 1):
            current_credential = await self._acquire_credential()
            if self.credential_pool.is_multi_key and not current_credential:
                logger.error(
                    "[%s] %s all credentials cooling down — cannot stream",
                    log_context,
                    self.log_label,
                )
                await self._emit_request_telemetry(
                    telemetry_callback,
                    model=model,
                    streamed=True,
                    log_context=log_context,
                    latency_seconds=0.0,
                    provider_retry_count=attempt,
                    success=False,
                    error="all_credentials_cooling",
                )
                return None
            request_headers = self._headers_with_credential(current_credential)

            try:
                start_time = time.time()
                async with session.post(
                    self._completion_endpoint(),
                    headers=request_headers,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=self.STREAM_TIMEOUT_SECONDS),
                ) as response:
                    elapsed = time.time() - start_time

                    if response.status == 200:
                        chunks: List[str] = []
                        async for payload_text in self._iter_sse_payloads(response):
                            if not payload_text or payload_text == "[DONE]":
                                continue
                            try:
                                data = json.loads(payload_text)
                            except json.JSONDecodeError:
                                debug_logger.debug(
                                    "[%s] Ignoring non-JSON %s stream payload: %s",
                                    log_context,
                                    self.log_label,
                                    payload_text[:160],
                                )
                                continue

                            choice = (data.get("choices") or [{}])[0] if isinstance(data, dict) else {}
                            delta_text = extract_stream_delta_text(choice)
                            if not delta_text:
                                continue
                            chunks.append(delta_text)
                            if on_delta is not None:
                                await on_delta(delta_text)

                        content = "".join(chunks)
                        await self._record_credential_outcome(
                            current_credential, status_code=200, success=True,
                        )
                        debug_logger.debug("[%s] %s Streaming Response (%.2fs)", log_context, self.log_label, elapsed)
                        debug_logger.debug("[%s] Streamed content: %s", log_context, content)
                        await self._emit_request_telemetry(
                            telemetry_callback,
                            model=model,
                            streamed=True,
                            log_context=log_context,
                            latency_seconds=elapsed,
                            provider_retry_count=attempt,
                            success=True,
                            usage={},
                        )
                        return content

                    error_text = await response.text()
                    await self._record_credential_outcome(
                        current_credential,
                        status_code=response.status,
                        success=False,
                    )
                    if self._is_retryable_error(response.status) and attempt < self.MAX_RETRIES:
                        retry_after = self._parse_retry_after(dict(response.headers))
                        delay = self._calculate_retry_delay(attempt, retry_after)
                        logger.warning(
                            "[%s] %s streaming API error %s, retrying in %.1fs (attempt %s/%s)",
                            log_context,
                            self.log_label,
                            response.status,
                            delay,
                            attempt + 1,
                            self.MAX_RETRIES + 1,
                        )
                        await asyncio.sleep(delay)
                        continue

                    logger.error(
                        "[%s] %s streaming API error %s: %s",
                        log_context,
                        self.log_label,
                        response.status,
                        error_text,
                    )
                    debug_logger.error("[%s] Streaming request took %.2fs before failing", log_context, elapsed)
                    await self._emit_request_telemetry(
                        telemetry_callback,
                        model=model,
                        streamed=True,
                        log_context=log_context,
                        latency_seconds=elapsed,
                        provider_retry_count=attempt,
                        success=False,
                        error=f"http_{response.status}",
                    )
                    return None

            except asyncio.TimeoutError:
                last_error = "timeout"
                if attempt < self.MAX_RETRIES:
                    delay = self._calculate_retry_delay(attempt)
                    logger.warning(
                        "[%s] %s streaming request timeout, retrying in %.1fs (attempt %s/%s)",
                        log_context,
                        self.log_label,
                        delay,
                        attempt + 1,
                        self.MAX_RETRIES + 1,
                    )
                    await asyncio.sleep(delay)
                    continue
                logger.error(
                    "[%s] %s streaming request timeout after %s attempts",
                    log_context,
                    self.log_label,
                    self.MAX_RETRIES + 1,
                )
                await self._emit_request_telemetry(
                    telemetry_callback,
                    model=model,
                    streamed=True,
                    log_context=log_context,
                    latency_seconds=float(self.STREAM_TIMEOUT_SECONDS),
                    provider_retry_count=attempt,
                    success=False,
                    error="timeout",
                )
                return None

            except (aiohttp.ClientError, OSError) as exc:
                last_error = str(exc)
                if attempt < self.MAX_RETRIES:
                    delay = self._calculate_retry_delay(attempt)
                    logger.warning(
                        "[%s] %s streaming network error: %s, retrying in %.1fs (attempt %s/%s)",
                        log_context,
                        self.log_label,
                        exc,
                        delay,
                        attempt + 1,
                        self.MAX_RETRIES + 1,
                    )
                    await asyncio.sleep(delay)
                    continue
                logger.error(
                    "[%s] %s streaming request failed after %s attempts: %s",
                    log_context,
                    self.log_label,
                    self.MAX_RETRIES + 1,
                    exc,
                )
                await self._emit_request_telemetry(
                    telemetry_callback,
                    model=model,
                    streamed=True,
                    log_context=log_context,
                    latency_seconds=0.0,
                    provider_retry_count=attempt,
                    success=False,
                    error=str(exc),
                )
                return None

            except Exception as exc:
                logger.error("[%s] %s streaming request error: %s", log_context, self.log_label, exc)
                debug_logger.error(
                    "[%s] Streaming exception details: %s: %s",
                    log_context,
                    type(exc).__name__,
                    str(exc),
                )
                await self._emit_request_telemetry(
                    telemetry_callback,
                    model=model,
                    streamed=True,
                    log_context=log_context,
                    latency_seconds=0.0,
                    provider_retry_count=attempt,
                    success=False,
                    error=str(exc),
                )
                return None

        logger.error("[%s] %s streaming request failed: %s", log_context, self.log_label, last_error)
        return None


def lane_total_timeout_seconds(log_context: str) -> float:
    """Module-level convenience wrapper around
    OpenAICompatibleProvider.ladder_ceiling_for_context, for callers
    that just want to know how long a lane's whole retry ladder can
    legitimately take without importing the provider class.
    """
    return OpenAICompatibleProvider.ladder_ceiling_for_context(log_context)
