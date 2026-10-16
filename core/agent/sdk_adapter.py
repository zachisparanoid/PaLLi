"""SDK-shape adapter that fronts any PaLLi BaseLLMProvider behind the
`AsyncOpenAI` interface that core.agent.llm_client.call_openai_compat expects.

Why this exists
---------------
`core/agent/llm_client.py::call_openai_compat` does

    stream = await client.chat.completions.create(
        model=..., messages=..., tools=..., tool_choice=...,
        stream=True, stream_options={"include_usage": True},
    )
    async for chunk in stream: ...

— the OpenAI Python SDK v1+ shape (`AsyncOpenAI`). PaLLi's own provider
classes (`CodexCLIProvider`, `OpenAICompatibleProvider`, ...) expose
`async chat_completion(messages, model, ...) -> Optional[str]` instead.
For providers that DON'T have an HTTP API key we can hand to the real
SDK (notably `codex`, which uses the codex CLI's own login), we have to
bridge the two surfaces.

This adapter accepts the OpenAI-shaped kwargs, prompt-engineers tool
schemas + tool-result history into a flat string conversation, calls
`provider.chat_completion(...)`, then parses `<tool_call>` blocks back
out of the response and yields chunks in the OpenAI delta shape the
runner consumes via `_collect_openai_stream`.

There is no native streaming — the underlying provider is text-only and
single-shot, so the adapter yields all its chunks in one batch after
the provider returns. The runner's chunker still gets the same sequence
of deltas it expects; it just arrives all at once.

Tradeoffs vs. a real SDK client
-------------------------------
- Tools work by prompt-engineered convention, not native tool_use. The
  model has to comply with the `<tool_call name="..."><JSON></tool_call>`
  protocol; if it produces malformed JSON, we degrade to plain text and
  let the model self-correct on the next turn rather than crashing.
- Token counts in `usage` are estimated (chars / 4); the provider's
  underlying `chat_completion` doesn't always surface usage and the
  runner only uses the numbers for telemetry.
- No streaming — the user sees one big chunk at the end, not deltas.
"""
from __future__ import annotations

import json
import logging
import re
import uuid
from typing import Any, AsyncIterator, Awaitable, Callable, Optional

from core.agent import reply_governor as _reply_governor


logger = logging.getLogger(__name__)


# Matches `<tool_call name="...">...JSON...</tool_call>`. DOTALL so that
# the inner JSON can span newlines. The name attribute is captured as
# group(1) and the JSON body as group(2).
# Two shapes (2026-09-02): the instructed one, ``<tool_call name="x">{...}</tool_call>``,
# and the Hermes/Qwen-native one the models are actually trained on,
# ``<tool_call>{"name": "x", "arguments": {...}}</tool_call>`` — the name
# attribute is optional and, when absent, the JSON body carries the name.
TOOL_CALL_RE = re.compile(
    r'<tool_call(?:\s+name="([^"]+)")?\s*>(.*?)</tool_call>',
    re.DOTALL,
)


def _format_tool_for_prompt(tool: dict[str, Any]) -> str:
    """Render an OpenAI-shape tool dict as a single bullet for the prompt.

    The runner passes tools in OpenAI shape (already converted by
    `_tools_to_openai`): `{"type":"function","function":{"name":...,
    "description":...,"parameters":{...JSON-Schema...}}}`. We need to
    flatten that into something the model can read.
    """
    fn = tool.get("function") or {}
    name = str(fn.get("name") or tool.get("name") or "unknown_tool")
    description = str(fn.get("description") or tool.get("description") or "")
    schema = fn.get("parameters") or tool.get("input_schema") or {}
    try:
        schema_json = json.dumps(schema, ensure_ascii=False)
    except (TypeError, ValueError):
        schema_json = "{}"
    return f"- {name}: {description}\n  schema: {schema_json}"


def _build_tool_instruction_block(tools: list[dict[str, Any]]) -> str:
    """Compose the system-message snippet that explains the tool_call protocol.

    Inserted at the start of the system message so the model sees it
    before any conversational instructions. Iterates the tools list and
    formats each with name + description + schema.
    """
    if not tools:
        return ""
    lines = [
        "You have access to these tools. To call a tool, emit one or more <tool_call>",
        "blocks in your response, one per call. Each block must contain valid JSON:",
        "",
        '  <tool_call name="tool_name_here">',
        '  {"arg1": "value1", "arg2": 42}',
        "  </tool_call>",
        "",
        "Plain text outside <tool_call> blocks is your reply to the user. You may produce",
        "both text AND tool calls in the same response. After tool calls, the system will",
        "execute the tools and provide results in a subsequent turn.",
        "",
        "Available tools:",
        "",
    ]
    lines.extend(_format_tool_for_prompt(t) for t in tools)
    lines.append("")
    lines.append("When you have no further tool calls to make, respond with text only.")
    return "\n".join(lines)


def _translate_messages_for_provider(
    messages: list[dict[str, Any]],
    *,
    tool_instruction: str,
) -> list[dict[str, Any]]:
    """Translate OpenAI-shape messages into something a flat-text provider
    can consume.

    Specifically:
    - Inject the tool-protocol instructions into the system message
      (creating one if there isn't one).
    - Convert `role="tool"` messages to `role="user"` with the result
      wrapped in a `<tool_result tool_call_id="...">...</tool_result>`
      block, so the model sees its prior tool calls' outputs in context.
    - Re-render assistant `tool_calls` as `<tool_call name="...">{...}</tool_call>`
      blocks in the assistant's text content, so the conversation history
      looks like the model's expected protocol.
    """
    out: list[dict[str, Any]] = []
    system_seen = False

    for msg in messages:
        role = msg.get("role")
        if role == "system":
            system_seen = True
            base = msg.get("content") or ""
            if tool_instruction:
                combined = f"{tool_instruction}\n\n{base}" if base else tool_instruction
            else:
                combined = base
            out.append({"role": "system", "content": combined})
            continue

        if role == "tool":
            tool_call_id = msg.get("tool_call_id") or ""
            content = msg.get("content")
            if isinstance(content, list):
                # Some callers may pass list-of-blocks; flatten to text.
                text_parts: list[str] = []
                for item in content:
                    if isinstance(item, str):
                        text_parts.append(item)
                    elif isinstance(item, dict):
                        text_parts.append(str(item.get("text") or item.get("content") or ""))
                content_str = "".join(text_parts)
            else:
                content_str = str(content or "")
            wrapped = (
                f'<tool_result tool_call_id="{tool_call_id}">\n'
                f"{content_str}\n"
                f"</tool_result>"
            )
            out.append({"role": "user", "content": wrapped})
            continue

        if role == "assistant":
            text_part = msg.get("content") or ""
            tool_calls = msg.get("tool_calls") or []
            if not tool_calls:
                out.append({"role": "assistant", "content": text_part})
                continue
            # Re-render tool_calls into the protocol shape so the model sees
            # its own prior calls in the format it's being asked to emit.
            rendered_calls: list[str] = []
            for tc in tool_calls:
                fn = tc.get("function") or {}
                name = str(fn.get("name") or "")
                args_str = fn.get("arguments")
                if isinstance(args_str, str):
                    args_text = args_str
                else:
                    try:
                        args_text = json.dumps(args_str or {}, ensure_ascii=False)
                    except (TypeError, ValueError):
                        args_text = "{}"
                rendered_calls.append(
                    f'<tool_call name="{name}">\n{args_text}\n</tool_call>'
                )
            combined = ("\n".join(rendered_calls)
                        if not text_part
                        else f"{text_part}\n" + "\n".join(rendered_calls))
            out.append({"role": "assistant", "content": combined})
            continue

        # Pass through user / unknown roles unchanged.
        out.append(msg)

    if not system_seen and tool_instruction:
        # No prior system message — prepend a synthetic one so tools
        # protocol still gets explained.
        out.insert(0, {"role": "system", "content": tool_instruction})

    return out


def _parse_response_text(text: str) -> tuple[str, list[dict[str, Any]]]:
    """Walk a response string, separating plain text from <tool_call> blocks.

    Returns (text_outside_tool_calls, list_of_tool_call_dicts).
    Each tool call dict is `{"id": "call_...", "name": str,
    "arguments": str_json}`. Malformed JSON inside a <tool_call> falls
    back to leaving the whole block in the text portion so the model
    can self-correct on the next turn.
    """
    text_parts: list[str] = []
    tool_calls: list[dict[str, Any]] = []
    cursor = 0

    for match in TOOL_CALL_RE.finditer(text):
        # Text between previous match end and this match start is plain text
        if match.start() > cursor:
            text_parts.append(text[cursor:match.start()])

        name = (match.group(1) or "").strip()
        body = match.group(2).strip()
        try:
            parsed = json.loads(body) if body else {}
        except json.JSONDecodeError:
            # Malformed: keep the whole block in the text so the next
            # iteration's history shows what went wrong.
            text_parts.append(match.group(0))
            cursor = match.end()
            continue
        if not name:
            # Hermes/Qwen-native shape: {"name": ..., "arguments": ...}. A
            # block with no name anywhere is not a call — leave it in the text.
            if not isinstance(parsed, dict) or not str(parsed.get("name") or "").strip():
                text_parts.append(match.group(0))
                cursor = match.end()
                continue
            name = str(parsed["name"]).strip()
            args = parsed.get("arguments", {})
            if isinstance(args, str):
                try:
                    json.loads(args)
                    body = args
                except json.JSONDecodeError:
                    body = json.dumps({"_raw_arguments": args})
            else:
                body = json.dumps(args if args is not None else {})

        tool_calls.append({
            "id": f"call_{uuid.uuid4().hex[:8]}",
            "name": name,
            "arguments": body if body else "{}",
        })
        cursor = match.end()

    if cursor < len(text):
        text_parts.append(text[cursor:])

    return "".join(text_parts), tool_calls


def _estimate_tokens(text: str) -> int:
    """Rough char-based estimate for telemetry. Real usage data is not
    available from text-only providers."""
    if not text:
        return 0
    return max(1, len(text) // 4)


class _Completions:
    """Mimics `client.chat.completions` from `openai.AsyncOpenAI`."""

    def __init__(self, adapter: "PaLLiProviderSDKAdapter") -> None:
        self._adapter = adapter

    async def create(self, **kwargs: Any) -> AsyncIterator[dict[str, Any]]:
        """Match the SDK signature. Returns an async iterable of chunks.

        The runner does `stream = await client.chat.completions.create(...)`
        and then `async for chunk in stream:`, so this method must return
        something with `__aiter__`. We return an async generator object
        directly.
        """
        return self._adapter._stream_chunks(**kwargs)


class _Chat:
    """Mimics `client.chat` from `openai.AsyncOpenAI`."""

    def __init__(self, adapter: "PaLLiProviderSDKAdapter") -> None:
        self.completions = _Completions(adapter)


class PaLLiProviderSDKAdapter:
    """Adapts any PaLLi BaseLLMProvider to the OpenAI AsyncOpenAI SDK
    shape expected by core.agent.llm_client.call_openai_compat.

    Implements tool_use via prompt-engineered <tool_call> blocks.
    """

    def __init__(
        self,
        provider: Any,
        *,
        reply_governor_enabled: Optional[Callable[[], bool]] = None,
        reply_compressor: Optional[Callable[[str, int], Awaitable[Optional[str]]]] = None,
    ) -> None:
        self._provider = provider
        self.chat = _Chat(self)
        # The reply-length governor (2026-08-22): this adapter is the one
        # place her FINAL reply exists before her session, the Discord
        # chunks and the reflection see it — so it is governed here and all
        # four stay identical. Both seams are injectable for tests; the
        # defaults are the live flag and her own provider.
        self._reply_governor_enabled = reply_governor_enabled or _reply_governor.governor_enabled
        self._reply_compressor = reply_compressor or self._compress_with_provider

    async def _compress_with_provider(self, text: str, cap: int) -> Optional[str]:
        length = _reply_governor.current_reply_shape.get()
        return await self._provider.chat_completion(
            messages=[
                {"role": "system", "content": _reply_governor.compression_prompt(cap, length)},
                {"role": "user", "content": text},
            ],
            temperature=0.3,
            # 1,500 not 400: the live check (08-22 19:xx) emptied a 400-token
            # budget on the model's own thinking before it wrote a word; the
            # net recovered it at +5 s. Room to think + ≤110 words of content.
            max_tokens=1500,
            log_context="reply_govern",
        )

    async def _govern_final_reply(self, text_part: str) -> str:
        length = _reply_governor.current_reply_shape.get()
        if not length or not text_part:
            return text_part
        try:
            if not self._reply_governor_enabled():
                return text_part
            result = await _reply_governor.govern_reply(text_part, length, compress=self._reply_compressor)
        except Exception as exc:  # never let the governor cost her the reply
            logger.warning("reply_govern: skipped (%s)", exc)
            return text_part
        _reply_governor.log_governed(result)
        return result.text

    async def _stream_chunks(self, **kwargs: Any) -> AsyncIterator[dict[str, Any]]:
        """Run the underlying provider once, then yield chunks shaped like
        an OpenAI streaming response so `_collect_openai_stream` consumes
        them transparently."""
        model = str(kwargs.get("model") or "")
        messages = kwargs.get("messages") or []
        tools = kwargs.get("tools") or []
        # tool_choice / stream / stream_options are accepted but unused
        # — the underlying provider doesn't expose those knobs and we
        # always do a single non-streaming call internally.

        tool_instruction = _build_tool_instruction_block(tools) if tools else ""
        translated = _translate_messages_for_provider(
            messages, tool_instruction=tool_instruction,
        )

        prompt_chars = sum(len(str(m.get("content") or "")) for m in translated)

        try:
            text: Optional[str] = await self._provider.chat_completion(
                messages=translated,
                model=model or None,
                temperature=0.7,
                max_tokens=4096,
                log_context="agent_loop_main",
            )
        except Exception as exc:
            logger.warning(
                "PaLLiProviderSDKAdapter: provider raised %s: %s",
                type(exc).__name__, exc,
            )
            text = None

        async for chunk in self._yield_chunks(
            text or "", prompt_chars=prompt_chars,
        ):
            yield chunk

    async def _yield_chunks(
        self,
        response_text: str,
        *,
        prompt_chars: int,
    ) -> AsyncIterator[dict[str, Any]]:
        """Convert a single response string into the chunk sequence the
        runner expects."""
        text_part, tool_calls = _parse_response_text(response_text)

        # Strip trailing whitespace that's just left over from <tool_call>
        # block separation; keep meaningful content intact.
        text_part = text_part.strip("\n")

        # A final reply (no tool calls this iteration) passes the governor.
        if text_part and not tool_calls:
            text_part = await self._govern_final_reply(text_part)

        if text_part:
            yield {
                "choices": [{
                    "delta": {"content": text_part},
                    "finish_reason": None,
                }],
            }

        for index, call in enumerate(tool_calls):
            yield {
                "choices": [{
                    "delta": {
                        "tool_calls": [{
                            "index": index,
                            "id": call["id"],
                            "type": "function",
                            "function": {
                                "name": call["name"],
                                "arguments": call["arguments"],
                            },
                        }],
                    },
                    "finish_reason": None,
                }],
            }

        finish_reason = "tool_calls" if tool_calls else "stop"
        prompt_tokens = max(1, prompt_chars // 4) if prompt_chars else 0
        completion_tokens = _estimate_tokens(response_text)
        yield {
            "choices": [{
                "delta": {},
                "finish_reason": finish_reason,
            }],
            "usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
            },
        }
