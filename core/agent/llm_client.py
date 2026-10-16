from __future__ import annotations

import asyncio
import dataclasses
import inspect
import json
import logging
import math
import os
from dataclasses import dataclass, field
from typing import Any, Awaitable, AsyncIterator, Callable, Literal

from core.agent.payload_masking import for_provider
from core.agent.session import (
    AgentMessage, ContentBlock, TextBlock, ToolResultBlock, ToolUseBlock, strip_working_note_mark,
)


logger = logging.getLogger(__name__)

# Bounded retry for transient LLM failures (timeouts, connection drops, 429,
# 5xx). Deterministic errors (400/401/403/404/422) are NOT retried — retrying
# won't help and just delays the failure. Backoff is per-attempt; the final
# attempt re-raises so the dispatch boundary can degrade the turn gracefully.
_LLM_RETRY_BACKOFF: tuple[float, ...] = (0.6, 1.8)  # sleeps between 3 attempts
_NON_RETRYABLE_STATUS = {400, 401, 403, 404, 422}
_RETRYABLE_STATUS = {408, 409, 429, 500, 502, 503, 504}
_RETRYABLE_SUBSTRINGS = (
    "timeout", "timed out", "connection", "read error", "temporarily",
    "rate limit", "overloaded", "unavailable", "econnreset",
)
# Batch 4 task 7 (spec 5.12): "if the provider sends nothing for about 90
# seconds, stop and retry once, then tell him". A stall costs a whole read
# timeout per attempt, so a TIMEOUT gets one retry here, not the two a quick
# transient gets: at most two read windows, then the error reaches dispatch's
# outage line. The SDK's own retries are off (dispatch: max_retries=0).
_LLM_TIMEOUT_RETRIES = 1

# How long her reply call waits for the provider to send ANYTHING real. Before
# task 7 the client took the SDK's 600 s. A local server gets longer: Ollama
# sends no byte while it loads a model or reads a long prompt, and a small
# brain on a 60-80k-token prompt can pass 90 s before its first token — every
# such turn would end on the outage line. LLM_REPLY_READ_TIMEOUT_SECONDS
# overrides both. dispatch builds the reply client's httpx read timeout from
# this; the content deadline below reads the same number back off the client.
_REPLY_READ_TIMEOUT_SECONDS = 90.0
_REPLY_READ_TIMEOUT_LOCAL_SECONDS = 300.0


def reply_read_seconds(provider: str = "") -> float:
    """The read seconds for her reply call to this provider: the ONE place
    the number is computed. Read from ``os.environ`` at call time (a client
    is built per turn, so a changed value applies to the next turn).
    Anything but a positive finite number means the default."""
    seconds = (
        _REPLY_READ_TIMEOUT_LOCAL_SECONDS
        if (provider or "").lower() == "local_openai"
        else _REPLY_READ_TIMEOUT_SECONDS
    )
    raw = (os.environ.get("LLM_REPLY_READ_TIMEOUT_SECONDS") or "").strip()
    if raw:
        try:
            value = float(raw)
        except ValueError:
            value = math.nan
        if math.isfinite(value) and value > 0:
            return value
        logger.warning(
            "LLM_REPLY_READ_TIMEOUT_SECONDS is not a positive number; using %.0f s", seconds,
        )
    return seconds


def _content_deadline(client: Any) -> float | None:
    """The per-chunk content deadline for this client: the read seconds
    dispatch put on it (``AsyncOpenAI(timeout=httpx.Timeout(reply_read_seconds(…)))``),
    so the byte wait and the content wait are one number. ``None`` for a
    client with no read timeout — the CLI brains' adapter, which makes one
    whole CLI call (its own timeout governs it) and only then yields."""
    read = getattr(getattr(client, "timeout", None), "read", None)
    if isinstance(read, (int, float)) and not isinstance(read, bool):
        if math.isfinite(read) and read > 0:
            return float(read)
    return None


# Reflex against silent dropped replies. A reasoning-prone model can burn its
# whole completion budget on chain-of-thought and return no visible text and no
# tool calls, so the turn silently produces no reply (2026-07-23 incident:
# completion_tokens=42, entirely reasoning). That is never an acceptable outcome
# for a user-facing turn, so a "fumble-empty" result is re-rolled a bounded
# number of times. The re-roll carries an explicit, generous max_tokens (room
# for a genuine length cutoff) and otherwise relies on sampling nondeterminism
# to get past a premature stop. Bounded so a persistently-empty model can't spin.
_EMPTY_COMPLETION_RETRIES = 2
_EMPTY_COMPLETION_RETRY_MAX_TOKENS = 4096


def _is_llm_timeout(exc: BaseException) -> bool:
    """The provider sent nothing for a whole read window. httpx raises this
    raw while the stream is read — often with an empty message, which the
    substring check below cannot see — and the openai SDK wraps one that
    hits before the headers as ``APITimeoutError``."""
    import httpx
    import openai

    return isinstance(exc, (httpx.TimeoutException, openai.APITimeoutError))


def _is_network_drop(exc: BaseException) -> bool:
    """The connection failed or dropped (a read, write or connect error, or the
    server closing mid-response), judged by TYPE along the cause chain. A drop
    mid-stream raises ``httpx.ReadError`` with an EMPTY message and no status
    (2026-09-29 18:27: her reply died on the first attempt because the
    substring check below saw nothing). A request built wrong
    (``LocalProtocolError``, ``UnsupportedProtocol``) is not a drop."""
    import httpx
    import openai

    seen: set[int] = set()
    cur: BaseException | None = exc
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        if isinstance(cur, (httpx.NetworkError, httpx.RemoteProtocolError,
                            openai.APIConnectionError)):
            return True
        cur = cur.__cause__ or cur.__context__
    return False


def _is_retryable_llm_error(exc: Exception) -> bool:
    """Heuristic: is this LLM error worth retrying? SDK-agnostic so it works for
    the openai SDK (which sets ``status_code``) and raw httpx/network errors."""
    if _is_llm_timeout(exc):
        return True
    if _is_network_drop(exc):
        return True
    status = getattr(exc, "status_code", None) or getattr(exc, "status", None)
    if status in _NON_RETRYABLE_STATUS:
        return False
    if status in _RETRYABLE_STATUS:
        return True
    # Every 5xx is the server's trouble. The openai SDK used to retry them all
    # itself; with its retries off (Batch 4 task 7) this loop is the only one.
    if isinstance(status, int) and 500 <= status < 600:
        return True
    msg = str(exc).lower()
    return any(s in msg for s in _RETRYABLE_SUBSTRINGS)


OnChunk = Callable[[str], Awaitable[None]]
StopReason = Literal["end_turn", "tool_use", "max_tokens", "error"]


@dataclass
class LLMResponse:
    text_blocks: list[str] = field(default_factory=list)
    tool_uses: list[ToolUseBlock] = field(default_factory=list)
    stop_reason: StopReason = "end_turn"
    prompt_tokens: int = 0
    completion_tokens: int = 0
    # The provider's prompt cache (DeepSeek ``usage.prompt_cache_hit_tokens`` /
    # ``prompt_cache_miss_tokens``): how much of the prompt was billed at the
    # cached price. Logged on the ``llm_call`` line; 0 where a provider does
    # not report them (Cut the Cost, Task 1, 2026-09-27).
    cache_hit_tokens: int = 0
    cache_miss_tokens: int = 0
    # Thinking-model chain-of-thought (DeepSeek ``reasoning_content``). Captured
    # so the runner can stash it on the assistant message and replay it on the
    # post-tool-call request (required by DeepSeek thinking mode). Empty for
    # non-thinking providers.
    reasoning: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


def _count(value: Any) -> int:
    """A provider's usage count as an int: ``None`` or anything ``int()``
    refuses (``"n/a"``, a nested dict, an infinity) is 0. A counter must
    never fail a billed request (whole-branch review, 2026-09-27)."""
    try:
        return int(value or 0)
    except (TypeError, ValueError, OverflowError):
        return 0


def _response_from_usage(usage: dict[str, Any], **fields: Any) -> LLMResponse:
    """An ``LLMResponse`` whose four token counts come from a provider usage
    dict; every other field is passed through. A missing, ``None`` or
    non-numeric count is 0, never an error."""
    return LLMResponse(
        prompt_tokens=_count(usage.get("prompt_tokens")),
        completion_tokens=_count(usage.get("completion_tokens")),
        cache_hit_tokens=_count(usage.get("prompt_cache_hit_tokens")),
        cache_miss_tokens=_count(usage.get("prompt_cache_miss_tokens")),
        **fields,
    )


def _agent_messages_to_openai(messages: list[AgentMessage]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for m in messages:
        if m.role == "tool":
            for b in m.content:
                if isinstance(b, ToolResultBlock):
                    out.append({
                        "role": "tool",
                        "tool_call_id": b.tool_use_id,
                        "content": b.content,
                    })
            continue

        text_parts: list[str] = []
        tool_calls: list[dict[str, Any]] = []
        for b in m.content:
            if isinstance(b, TextBlock):
                text_parts.append(b.text)
            elif isinstance(b, ToolUseBlock):
                tool_calls.append({
                    "id": b.id,
                    "type": "function",
                    "function": {"name": b.name, "arguments": json.dumps(b.input)},
                })
        content_text = "".join(text_parts) or None
        # Defensive: drop ANY message with content=None and no tool_calls.
        # OpenAI/DeepSeek/etc. reject these, and the rejection differs by role:
        #   - assistant: 400 "content or tool_calls must be set" (a reasoning
        #     model can burn its whole budget on chain-of-thought and return
        #     nothing visible).
        #   - user/tool: 400 "messages[N]: content should be a string or a
        #     list". 2026-06-11 incident: an untranscribed voice message was
        #     persisted as a user turn with an empty text block; "".join([""])
        #     or None is None, and that one record re-sent on every turn 400'd
        #     the entire channel indefinitely (no reply to voice OR text).
        # A contentless, tool-less turn carries nothing, so dropping it is
        # always safe. The guard previously only covered role=="assistant";
        # it must cover every role. (The runner also avoids saving empty
        # assistant messages, but historical sessions may already hold bad
        # records — this filter handles those too.)
        if content_text is None and not tool_calls:
            continue
        msg: dict[str, Any] = {"role": m.role, "content": content_text}
        if tool_calls:
            msg["tool_calls"] = tool_calls
            # DeepSeek thinking mode requires the reasoning that produced a tool
            # call to be replayed on the follow-up request, or it 400s. We only
            # have reasoning on this-turn assistant messages (it's not persisted),
            # so this naturally scopes to the active tool round-trip.
            reasoning = getattr(m, "reasoning", "") or ""
            if reasoning:
                msg["reasoning_content"] = reasoning
        out.append(msg)
    return out


def _tools_to_openai(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t["description"],
                "parameters": t["input_schema"],
            },
        }
        for t in tools
    ]


async def _close_stream_quietly(stream: Any) -> None:
    """Let go of a stream we gave up on (the SDK's ``AsyncStream.close``
    releases the connection). Bounded and silent: the timeout it follows is
    what the caller needs to hear about, not a failure to close."""
    close = getattr(stream, "close", None) or getattr(stream, "aclose", None)
    if not callable(close):
        return
    try:
        result = close()
        if inspect.isawaitable(result):
            await asyncio.wait_for(result, 5.0)
    except Exception as exc:  # noqa: BLE001 - never mask the timeout
        logger.debug("closing a silent llm stream failed: %s", exc)


async def _chunks_within(stream: Any, seconds: float | None) -> AsyncIterator[Any]:
    """Yield the stream's chunks, but give up when ``seconds`` pass with no
    chunk (R11a). httpx's read timer restarts on every BYTE, and a queued
    DeepSeek request gets ": keep-alive" SSE comments for up to ten minutes —
    bytes the SDK's decoder drops, so nothing reaches her while the read
    timeout never fires. A miss raises ``httpx.ReadTimeout``: a timeout like
    any other, so the one-retry cap and dispatch's outage line take it.
    Per chunk, not per reply: a long answer that keeps coming is never cut."""
    if not seconds:
        async for chunk in stream:
            yield chunk
        return
    chunks = stream.__aiter__()
    while True:
        try:
            async with asyncio.timeout(seconds) as window:
                chunk = await chunks.__anext__()
        except StopAsyncIteration:
            return
        except TimeoutError:
            if not window.expired():
                raise          # the stream's own TimeoutError, not our deadline
            await _close_stream_quietly(stream)
            import httpx

            raise httpx.ReadTimeout(
                f"no content from the provider for {seconds:g} s"
            ) from None
        yield chunk


async def _collect_openai_stream(
    stream: AsyncIterator[dict[str, Any]],
    on_chunk: OnChunk | None,
    *,
    chunk_timeout: float | None = None,
) -> LLMResponse:
    text_buffer: list[str] = []
    reasoning_buffer: list[str] = []
    tool_call_accum: dict[int, dict[str, Any]] = {}
    stop_reason: StopReason = "end_turn"
    usage: dict[str, int] = {"prompt_tokens": 0, "completion_tokens": 0}

    async for chunk in _chunks_within(stream, chunk_timeout):
        # OpenAI SDK >=1.0 yields Pydantic ChatCompletionChunk objects;
        # PaLLiProviderSDKAdapter yields plain dicts. Normalize to dict
        # at the boundary so the rest of the parser is shape-uniform.
        # model_dump() preserves provider extra fields like DeepSeek's
        # ``reasoning_content`` (the OpenAI SDK BaseModel allows extras).
        if not isinstance(chunk, dict) and hasattr(chunk, "model_dump"):
            chunk = chunk.model_dump()
        if not chunk.get("choices"):
            if chunk.get("usage"):
                usage = chunk["usage"]
            continue
        choice = chunk["choices"][0]
        delta = choice.get("delta", {}) or {}
        # Thinking-model chain-of-thought — buffer it but NEVER stream it to
        # on_chunk; it's internal reasoning, not part of the visible reply.
        reasoning = delta.get("reasoning_content")
        if reasoning:
            reasoning_buffer.append(reasoning)
        text = delta.get("content")
        if text:
            text_buffer.append(text)
            if on_chunk:
                await on_chunk(text)
        for tc in delta.get("tool_calls") or []:
            idx = tc.get("index", 0)
            slot = tool_call_accum.setdefault(idx, {"id": "", "name": "", "arguments": ""})
            if tc.get("id"):
                slot["id"] = tc["id"]
            fn = tc.get("function") or {}
            if fn.get("name"):
                slot["name"] += fn["name"]
            if fn.get("arguments") is not None:
                slot["arguments"] += fn["arguments"]
        finish = choice.get("finish_reason")
        if finish == "stop":
            stop_reason = "end_turn"
        elif finish == "tool_calls":
            stop_reason = "tool_use"
        elif finish == "length":
            stop_reason = "max_tokens"
        if chunk.get("usage"):
            usage = chunk["usage"]

    tool_uses: list[ToolUseBlock] = []
    for slot in tool_call_accum.values():
        try:
            args = json.loads(slot["arguments"]) if slot["arguments"] else {}
        except json.JSONDecodeError:
            args = {"_raw_arguments": slot["arguments"]}
        tool_uses.append(ToolUseBlock(id=slot["id"], name=slot["name"], input=args))

    return _response_from_usage(
        usage,
        text_blocks=["".join(text_buffer)] if text_buffer else [],
        tool_uses=tool_uses,
        stop_reason=stop_reason if not tool_uses else "tool_use",
        reasoning="".join(reasoning_buffer),
    )


def _sampling_kwargs(temperature: float | None, model: str = "") -> dict[str, Any]:
    """The sampling keywords for her reply call (Batch 4 task 6, owner call 1).

    ``temperature`` is ``config.LLM_REPLY_TEMPERATURE``: 0.9 unless he changes
    it. ``None`` (the setting left blank) means the provider's own default, so
    the keyword is left out — never sent as 0.0. A brain that refuses the
    keyword (gpt-5*, OpenAI's o-series) never gets it; the rule is the
    analysis-lane payload builder's own, imported so the two cannot drift
    apart."""
    if temperature is None:
        return {}
    if model:
        from ai.llm_providers import model_rejects_temperature

        if model_rejects_temperature(model):
            return {}
    return {"temperature": temperature}


async def call_openai_compat(
    *,
    client: Any,
    model: str,
    system: str,
    messages: list[AgentMessage],
    tools: list[dict[str, Any]],
    on_chunk: OnChunk | None = None,
    trailing_system_note: str | None = None,
    system_tail: str | None = None,
    extra_body: dict[str, Any] | None = None,
    tool_choice: str | None = None,
) -> LLMResponse:
    # Batch 1 (2026-09-26): the provider seam. The WHOLE request — system,
    # his words, her tool-call arguments (JSON text; the key grammar handles
    # the escaped form), tool results — is redacted (known literals, gone for
    # good) then masked (secret-shaped strings -> [MASKED:<hex>]). A copy:
    # her session keeps the real values. Put-back happens only in tool
    # arguments (tools.py) and an owner's reply (dispatch.py).
    payload_messages = for_provider(
        [{"role": "system", "content": system}, *_agent_messages_to_openai(messages)]
    )
    # The register fix (2026-08-22): the reply-shape note rides LAST — right
    # before she generates — payload-only, never in her session. Batch 2,
    # task 3 (2026-09-27): the prompt's volatile tail (clock, bands,
    # directive) rides in the SAME trailing message, ahead of the note, so
    # the payload stays [system: stable prefix] + history + ONE system
    # message — the bytes before it are identical turn to turn and the
    # provider's prefix cache covers the history. Note alone: today's bytes.
    trailing = "\n\n".join(
        p.strip() for p in (system_tail, trailing_system_note) if p and p.strip()
    )
    if trailing:
        payload_messages.append({"role": "system", "content": for_provider(trailing)})
    # Read at call time, not import time: the setting lives on the shared
    # config object, and one reading per reply keeps every attempt below
    # (retries, empty re-rolls) on the same temperature.
    from core.config import config

    sampling = _sampling_kwargs(config.LLM_REPLY_TEMPERATURE, model)
    chunk_timeout = _content_deadline(client)   # R11a: the same seconds as the byte wait
    max_transient_retries = len(_LLM_RETRY_BACKOFF)
    transient_retries = 0
    timeout_retries = 0   # a subset of transient_retries (task 7)
    empty_retries = 0
    while True:
        # Track whether this attempt emitted any chunk. We must NOT retry after
        # output has started, or the caller's buffer/stream gets duplicated
        # content. Transient failures happen at create() time (before any chunk)
        # and a fumble-empty by definition streamed nothing, so both retry paths
        # below stay safe under this guard.
        # 2026-08-24: collection now BUFFERS — nothing reaches on_chunk until
        # the final (possibly governed) text is settled below. Nothing was
        # ever user-visible mid-stream (the chunks are captured, not sent),
        # and retry-after-partial-output becomes structurally impossible.
        emitted = False

        # Only empty-completion re-rolls carry an explicit budget; the first
        # attempt keeps the provider default so normal turns are unchanged.
        create_kwargs: dict[str, Any] = {}
        if empty_retries:
            create_kwargs["max_tokens"] = _EMPTY_COMPLETION_RETRY_MAX_TOKENS
        if extra_body:
            create_kwargs["extra_body"] = dict(extra_body)   # e.g. reasoning_effort for a local Ollama brain (2026-09-02)

        try:
            stream = await client.chat.completions.create(
                model=model,
                messages=payload_messages,
                **sampling,
                tools=_tools_to_openai(tools) if tools else None,
                # Second read F3 (2026-10-04): the runner's brake asks for
                # "none" on the reply step after a run stopped moving; the
                # tool list itself stays, so the cached prefix does too.
                tool_choice=(tool_choice or "auto") if tools else None,
                stream=True,
                stream_options={"include_usage": True},
                **create_kwargs,
            )
            response = await _collect_openai_stream(stream, None, chunk_timeout=chunk_timeout)
        except Exception as exc:
            timed_out = _is_llm_timeout(exc)
            can_retry = (
                transient_retries < max_transient_retries
                and not emitted
                and _is_retryable_llm_error(exc)
                # A stall gets one more try, never a third read window.
                and not (timed_out and timeout_retries >= _LLM_TIMEOUT_RETRIES)
            )
            if not can_retry:
                raise
            backoff = _LLM_RETRY_BACKOFF[transient_retries]
            transient_retries += 1
            timeout_retries += int(timed_out)
            logger.warning(
                "agent llm call failed (transient retry %d/%d), retrying in %.1fs: %s",
                transient_retries, max_transient_retries, backoff,
                str(exc) or type(exc).__name__,   # a mid-stream ReadTimeout has no message
            )
            await asyncio.sleep(backoff)
            continue

        # Fumble-empty reflex: the model surfaced no text and no tool calls, but
        # there's evidence it *tried* — it emitted chain-of-thought, or it hit
        # the length cap. Re-roll (bounded) with a generous budget rather than
        # let the turn silently produce no reply. A clean empty (no reasoning,
        # natural stop) is left untouched — it may be an intentional silence.
        produced_nothing_visible = not response.text_blocks and not response.tool_uses
        tried_but_surfaced_nothing = (
            response.stop_reason == "max_tokens" or bool(response.reasoning)
        )
        if (
            produced_nothing_visible
            and tried_but_surfaced_nothing
            and not emitted
            and empty_retries < _EMPTY_COMPLETION_RETRIES
        ):
            empty_retries += 1
            logger.warning(
                "agent llm returned empty visible output (reasoning-model fumble: "
                "stop_reason=%s reasoning=%dch prompt_tokens=%d completion_tokens=%d); "
                "re-rolling with max_tokens=%d (empty-retry %d/%d)",
                response.stop_reason, len(response.reasoning),
                response.prompt_tokens, response.completion_tokens,
                _EMPTY_COMPLETION_RETRY_MAX_TOKENS, empty_retries, _EMPTY_COMPLETION_RETRIES,
            )
            continue

        # The register governor on the LIVE path (2026-08-24 21:5x): the 08-22
        # build hung it on the SDK adapter, but api-key providers ride the raw
        # AsyncOpenAI client (dispatch Path 1) — it never fired (0 engagements,
        # 9/31 replies over cap since "live"). BOTH client kinds flow through
        # here. Emission was deferred above so the chunker, the session and
        # Discord all see only the governed text — the custody law.
        final = response
        # DEFECT_RECORD §20 (2026-09-02): the working-note stamp is the
        # system's. If the model wrote one, it leaves here — before the
        # governor reads the text and before on_chunk delivers it.
        _cleaned = [strip_working_note_mark(t) for t in final.text_blocks]
        if _cleaned != list(final.text_blocks):
            final = dataclasses.replace(final, text_blocks=[t for t in _cleaned if t.strip()])
        joined = "".join(final.text_blocks)
        if joined and not final.tool_uses:
            try:
                from core.agent import reply_governor as _rg

                shape = _rg.current_reply_shape.get()
                if shape and _rg.governor_enabled() and _rg.needs_governing(joined, shape):
                    outcome = await _rg.govern_reply(joined, shape, compress=_reply_compressor)
                    _rg.log_governed(outcome)
                    if outcome.text != joined:
                        final = dataclasses.replace(final, text_blocks=[outcome.text])
            except Exception as exc:
                logger.warning("reply_govern: skipped on the live path (%s)", exc)
        if on_chunk:
            for _t in final.text_blocks:
                if _t:
                    await on_chunk(_t)
        return final



async def _reply_compressor(text: str, cap: int) -> str | None:
    """The governor's meaning half on the live path: her own provider, the
    committed prompt, the human-facing `reply_govern` lane (he is waiting).
    1,500 tokens: room to think plus ≤110 words of content (the 08-22 live
    check emptied a 400-token budget on thought)."""
    from ai.llm_client import llm
    from core.agent import reply_governor as _rg

    shape = _rg.current_reply_shape.get()
    return await llm.chat_completion(
        messages=[
            {"role": "system", "content": _rg.compression_prompt(cap, shape)},
            {"role": "user", "content": text},
        ],
        temperature=0.3,
        max_tokens=1500,
        log_context="reply_govern",
    )


def _agent_messages_to_anthropic(messages: list[AgentMessage]) -> list[dict[str, Any]]:
    """Translate AgentMessage history to Anthropic's content-block shape.

    Synthetic ToolResultBlocks injected by the runner (loop-detection-N,
    max-iter-N) carry tool_use_ids that have no matching prior tool_use.
    Anthropic's API 400s on `tool_result.tool_use_id` without a paired
    `tool_use.id` in the same conversation. We track the set of known
    tool_use ids emitted by prior assistant messages and downgrade orphan
    tool_results to plain user-text observations so they still influence
    the next turn (e.g. delivering the CRITICAL signal) without tripping
    the API's pairing check.
    """
    out: list[dict[str, Any]] = []
    known_tool_use_ids: set[str] = set()

    for m in messages:
        if m.role == "tool":
            matched_blocks: list[dict[str, Any]] = []
            orphan_texts: list[str] = []
            for b in m.content:
                if isinstance(b, ToolResultBlock):
                    if b.tool_use_id in known_tool_use_ids:
                        matched_blocks.append({
                            "type": "tool_result",
                            "tool_use_id": b.tool_use_id,
                            "content": b.content,
                            "is_error": b.is_error,
                        })
                    else:
                        # Orphan synthetic injection — convert to user text so
                        # the model still sees the observation (e.g. CRITICAL
                        # stop signal) without violating Anthropic's
                        # tool_use/tool_result pairing requirement.
                        orphan_texts.append(f"[system observation] {b.content}")
            if matched_blocks:
                out.append({"role": "user", "content": matched_blocks})
            for text in orphan_texts:
                out.append({"role": "user", "content": [{"type": "text", "text": text}]})
            continue

        content = []
        for b in m.content:
            if isinstance(b, TextBlock):
                content.append({"type": "text", "text": b.text})
            elif isinstance(b, ToolUseBlock):
                content.append({"type": "tool_use", "id": b.id, "name": b.name, "input": b.input})
                known_tool_use_ids.add(b.id)
        out.append({"role": m.role, "content": content})
    return out


def _tools_to_anthropic(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {"name": t["name"], "description": t["description"], "input_schema": t["input_schema"]}
        for t in tools
    ]


async def _collect_anthropic_stream(
    stream: AsyncIterator[dict[str, Any]],
    on_chunk: OnChunk | None,
    *,
    prompt_tokens_hint: int = 0,
) -> LLMResponse:
    text_blocks: list[str] = []
    current_text: list[str] = []
    pending_tool_uses: dict[int, dict[str, Any]] = {}
    stop_reason: StopReason = "end_turn"
    completion_tokens = 0
    prompt_tokens = prompt_tokens_hint

    async for ev in stream:
        t = ev.get("type")
        if t == "content_block_start":
            block = ev.get("content_block", {})
            idx = ev.get("index", 0)
            if block.get("type") == "tool_use":
                pending_tool_uses[idx] = {
                    "id": block.get("id", ""),
                    "name": block.get("name", ""),
                    "input_json": "",
                }
        elif t == "content_block_delta":
            idx = ev.get("index", 0)
            delta = ev.get("delta", {})
            dt = delta.get("type")
            if dt == "text_delta":
                txt = delta.get("text", "")
                current_text.append(txt)
                if on_chunk and txt:
                    await on_chunk(txt)
            elif dt == "input_json_delta":
                if idx in pending_tool_uses:
                    pending_tool_uses[idx]["input_json"] += delta.get("partial_json", "")
        elif t == "content_block_stop":
            if current_text:
                text_blocks.append("".join(current_text))
                current_text = []
        elif t == "message_delta":
            sr = ev.get("delta", {}).get("stop_reason")
            if sr == "end_turn":
                stop_reason = "end_turn"
            elif sr == "tool_use":
                stop_reason = "tool_use"
            elif sr == "max_tokens":
                stop_reason = "max_tokens"
            usage = ev.get("usage") or {}
            if "output_tokens" in usage:
                completion_tokens = usage["output_tokens"]
            if "input_tokens" in usage:
                prompt_tokens = usage["input_tokens"]
        elif t == "message_start":
            usage = (ev.get("message") or {}).get("usage") or {}
            if "input_tokens" in usage:
                prompt_tokens = usage["input_tokens"]

    tool_uses: list[ToolUseBlock] = []
    for slot in pending_tool_uses.values():
        try:
            args = json.loads(slot["input_json"]) if slot["input_json"] else {}
        except json.JSONDecodeError:
            args = {"_raw_arguments": slot["input_json"]}
        tool_uses.append(ToolUseBlock(id=slot["id"], name=slot["name"], input=args))

    return LLMResponse(
        text_blocks=text_blocks,
        tool_uses=tool_uses,
        stop_reason=stop_reason if not tool_uses else "tool_use",
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
    )


async def call_anthropic(
    *,
    client: Any,
    model: str,
    system: str,
    messages: list[AgentMessage],
    tools: list[dict[str, Any]],
    on_chunk: OnChunk | None = None,
    max_tokens: int = 4096,
    system_tail: str | None = None,
) -> LLMResponse:
    """Make a streaming Anthropic messages.create call with tools.

    `client` is duck-typed; client.messages.stream(...) must return an async
    context manager that yields events. The events are passed through
    _collect_anthropic_stream as an async iterator.
    """
    # Batch 2, task 3 (2026-09-27): the prompt's volatile tail folds back onto
    # `system` here — Anthropic takes one system string, and the shape note
    # is not sent on this path (as before) — so the string is byte-identical
    # to the whole prompt apart from the memory doc's position.
    system = "\n\n".join(p for p in (system, system_tail) if p and p.strip())
    # Batch 1 (2026-09-26): the same provider seam as call_openai_compat —
    # system and the converted messages redacted then masked; a copy.
    async with client.messages.stream(
        model=model,
        system=for_provider(system),
        messages=for_provider(_agent_messages_to_anthropic(messages)),
        tools=_tools_to_anthropic(tools) if tools else None,
        max_tokens=max_tokens,
    ) as stream:
        async def _events():
            async for event in stream:
                yield event
        return await _collect_anthropic_stream(_events(), on_chunk)


async def call(
    *,
    provider: Literal["openai_compat", "anthropic"],
    client: Any,
    model: str,
    system: str,
    messages: list[AgentMessage],
    tools: list[dict[str, Any]],
    on_chunk: OnChunk | None = None,
    max_tokens: int = 4096,
    trailing_system_note: str | None = None,
    system_tail: str | None = None,
    extra_body: dict[str, Any] | None = None,
    tool_choice: str | None = None,
) -> LLMResponse:
    """Provider-agnostic streaming LLM call. Routes by provider name.

    ``system_tail`` (batch 2, task 3, 2026-09-27) is the prompt's volatile
    tail: openai_compat rides it in the one trailing system message ahead
    of ``trailing_system_note``; anthropic folds it onto ``system`` and,
    as before, does not send the note.

    ``tool_choice`` (second read F3, 2026-10-04): None is today's request
    ("auto" whenever tools are offered); the runner passes "none" for the
    one reply step after its brake. The openai_compat path only: the
    anthropic path is not the runner's (dispatch always asks openai_compat).
    """
    if provider == "openai_compat":
        return await call_openai_compat(
            client=client, model=model, system=system,
            messages=messages, tools=tools, on_chunk=on_chunk,
            trailing_system_note=trailing_system_note,
            system_tail=system_tail,
            extra_body=extra_body,
            **({"tool_choice": tool_choice} if tool_choice else {}),
        )
    if provider == "anthropic":
        return await call_anthropic(
            client=client, model=model, system=system,
            messages=messages, tools=tools, on_chunk=on_chunk,
            max_tokens=max_tokens, system_tail=system_tail,
        )
    raise ValueError(f"unknown provider: {provider!r}")
