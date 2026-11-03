"""Native Ollama `/api/chat` provider.

Why a dedicated provider instead of reusing LocalOpenAIProvider:
Ollama's OpenAI-compatible `/v1/chat/completions` endpoint works but
can't express Ollama-specific options we care about for fast classifier
work:

  - `think: false` — explicitly disable reasoning/thinking output on
    Qwen3 and other thinking-capable models. Via the OpenAI layer,
    `/no_think` soft switches don't propagate reliably.
  - `format: "json"` — grammar-enforced JSON generation. Small local
    models (Qwen3 8B in particular) can drift tokens mid-generation
    and produce corrupted JSON without this. Grammar enforcement
    eliminates that class of failure.
  - `keep_alive: -1` — pin the model in VRAM indefinitely so we don't
    pay model-load cost after the default 5-minute idle timeout.

Latency baseline on the 2026-04-23 test machine (Windows + consumer
GPU, 17.5k-char combined_decision prompt, Qwen3 8B, full 23-field JSON
output):

  native endpoint, think=false, format=json, keep_alive=-1, 5/5 trials:
    5.2s, 5.4s, 5.2s, 5.4s, 5.4s (avg 5.3s, all parseable)

Compared to:
  Claude Code CLI / Haiku (after tuning):  ~15s warm
  Codex CLI / gpt-5.3-codex:                55-80s

This is the fast-path for classification-style LLM calls on a machine
with a decent local model available.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Any, Awaitable, Callable, Dict, List, Optional

import aiohttp

from ai.llm_openai_compatible import BaseLLMProvider, messages_expect_json
from core.config import config


logger = logging.getLogger("ai.llm_client")

# Regex to strip any <think>...</think> blocks that leak through
# (defensive — with think=False they shouldn't appear, but handle the
# case where a model ignores the flag or a bad version is running).
_THINK_BLOCK_RE = re.compile(r"<think>.*?</think>\s*", re.DOTALL)
# Code-fence strip for JSON responses wrapped in ```json ... ```
_JSON_FENCE_RE = re.compile(r"^```(?:json)?\s*\n?(.*?)\n?```\s*$", re.DOTALL)


def _clean_ollama_response(text: str, *, expects_json: bool) -> str:
    """Strip <think> blocks and ```json fences. Idempotent; safe on clean text."""
    s = str(text or "").strip()
    if not s:
        return s
    # <think>…</think>
    s = _THINK_BLOCK_RE.sub("", s).strip()
    # ```json … ``` or ``` … ```
    if expects_json:
        m = _JSON_FENCE_RE.match(s)
        if m:
            s = m.group(1).strip()
    return s


class OllamaProvider(BaseLLMProvider):
    """Talks to Ollama via its native /api/chat endpoint."""

    CONFIG_CHECK_CACHE_SECONDS = 15.0

    def __init__(
        self,
        *,
        base_url: str = "",
        default_model: str = "",
        keep_alive: Any = -1,
        timeout_seconds: Optional[float] = None,
    ) -> None:
        super().__init__()
        self.provider_name = "ollama"
        self.log_label = "Ollama"
        # Ollama's native endpoint is <base>/api/... — the OpenAI-compat
        # is at <base>/v1/... . We take a bare base (no /v1 suffix) here.
        raw_base = str(
            base_url
            or getattr(config, "OLLAMA_BASE_URL", "")
            or "http://127.0.0.1:11434"
        ).rstrip("/")
        # Allow LOCAL_LLM_API_BASE to be reused (strip /v1 if present)
        if raw_base.endswith("/v1"):
            raw_base = raw_base[:-3]
        self.base_url = raw_base
        self.default_model = str(
            default_model
            or getattr(config, "OLLAMA_MODEL", "")
            or getattr(config, "LOCAL_LLM_MODEL", "")
            or "qwen3:8b"
        ).strip() or "qwen3:8b"
        # keep_alive: -1 means "forever until process exits / model unloaded
        # manually"; 0 means "unload immediately after this call"; a number
        # of seconds otherwise.
        self.keep_alive: Any = keep_alive if keep_alive is not None else -1
        self.timeout_seconds = float(
            timeout_seconds
            or getattr(config, "OLLAMA_TIMEOUT_SECONDS", 180.0)
            or 180.0
        )
        # The provider supports vision when routed at a vision-capable model
        # (e.g. gemma3:12b). Non-vision models on Ollama will simply ignore
        # the `images` payload — routing config is what ensures the right
        # model is selected per task lane.
        self.supports_vision = True
        self._last_configured_check_at = 0.0
        self._last_configured_ok = False
        self._last_diagnostics: Dict[str, Any] = {}

    async def _probe(self) -> Dict[str, Any]:
        """Ping /api/tags to check Ollama is reachable + list models."""
        url = f"{self.base_url}/api/tags"
        try:
            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=3.0),
            ) as session:
                async with session.get(url) as resp:
                    if resp.status != 200:
                        return {
                            "configured": False,
                            "login_status": f"http_{resp.status}",
                            "login_status_text": f"Ollama /api/tags returned {resp.status}",
                            "base_url": self.base_url,
                        }
                    data = await resp.json()
                    models = [m.get("name") for m in (data.get("models") or []) if m.get("name")]
                    return {
                        "configured": True,
                        "login_status": "reachable",
                        "login_status_text": f"Ollama at {self.base_url} ({len(models)} models loaded in registry)",
                        "base_url": self.base_url,
                        "available_models": models[:20],
                    }
        except Exception as exc:
            return {
                "configured": False,
                "login_status": "unreachable",
                "login_status_text": f"Ollama /api/tags unreachable: {type(exc).__name__}",
                "base_url": self.base_url,
            }

    def is_configured(self) -> bool:
        """Sync-friendly reachability check with short-lived cache.

        Per-call is_configured() is sync by the BaseLLMProvider contract,
        so we can't await /api/tags here. Instead we rely on the first
        async chat_completion() to surface any connectivity issues, and
        refresh the cached diagnostic block asynchronously via ensure_ready().
        """
        now = time.time()
        if now - self._last_configured_check_at <= self.CONFIG_CHECK_CACHE_SECONDS:
            return self._last_configured_ok
        # Optimistic default: assume reachable. The real check happens
        # on first chat_completion — any failure surfaces there.
        self._last_configured_ok = True
        self._last_configured_check_at = now
        return True

    def diagnostics(self) -> Dict[str, Any]:
        payload = super().diagnostics()
        payload.update(self._last_diagnostics or {})
        payload["base_url"] = self.base_url
        payload["default_model"] = self.default_model
        payload["keep_alive"] = self.keep_alive
        return payload

    async def ensure_ready(self) -> bool:
        """Refresh reachability diagnostics. Can be awaited by startup paths
        to pre-check Ollama health before the first real call."""
        self._last_diagnostics = await self._probe()
        self._last_configured_ok = bool(self._last_diagnostics.get("configured"))
        self._last_configured_check_at = time.time()
        return self._last_configured_ok

    async def warm_model(self, model: Optional[str] = None) -> float:
        """Send a trivial chat to load the model into memory with keep_alive=-1.

        Useful at PaLLi startup to front-load the model-load cost instead
        of paying it on the first user message. Returns elapsed seconds.
        """
        resolved = str(model or self.default_model).strip() or "qwen3:8b"
        url = f"{self.base_url}/api/chat"
        body = {
            "model": resolved,
            "messages": [{"role": "user", "content": "ok"}],
            "stream": False,
            "think": False,
            "keep_alive": self.keep_alive,
            "options": {"temperature": 0, "num_predict": 4},
        }
        start = time.time()
        try:
            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=self.timeout_seconds),
            ) as session:
                async with session.post(url, json=body) as resp:
                    _ = await resp.read()
            elapsed = time.time() - start
            logger.info(
                "🔥 [%s] Pre-warmed %s (keep_alive=%s) in %.1fs",
                self.log_label, resolved, self.keep_alive, elapsed,
            )
            return elapsed
        except Exception as exc:
            elapsed = time.time() - start
            logger.warning(
                "[%s] warm_model(%s) failed after %.1fs: %s",
                self.log_label, resolved, elapsed, exc,
            )
            return elapsed

    async def chat_completion(
        self,
        messages: List[Dict],
        model: str = None,
        temperature: float = 0.7,
        max_tokens: int = 2000,
        log_context: str = "general",
        telemetry_callback: Optional[Callable[[Dict[str, Any]], Awaitable[None]]] = None,
    ) -> Optional[str]:
        resolved_model = str(model or self.default_model or "qwen3:8b").strip()
        expects_json = messages_expect_json(messages or [])

        body: Dict[str, Any] = {
            "model": resolved_model,
            "messages": list(messages or []),
            "stream": False,
            # Explicitly disable reasoning/thinking on models that support
            # toggling (Qwen3, DeepSeek R1, etc.). Huge latency win for
            # classification tasks.
            "think": False,
            "keep_alive": self.keep_alive,
            "options": {
                "temperature": float(temperature),
                "num_predict": int(max_tokens or 2000),
            },
        }
        if expects_json:
            # Ollama's grammar-enforced JSON mode — eliminates mid-generation
            # token drift on small local models.
            body["format"] = "json"

        url = f"{self.base_url}/api/chat"
        logger.info(
            "🧠 [%s] Sending request (Model: %s, Context: %s)...",
            self.log_label, resolved_model, log_context,
        )
        start_time = time.time()
        try:
            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=self.timeout_seconds),
            ) as session:
                async with session.post(url, json=body) as resp:
                    raw = await resp.read()
                    elapsed = time.time() - start_time
                    if resp.status != 200:
                        logger.error(
                            "[%s] %s returned HTTP %s: %s",
                            log_context, self.log_label, resp.status,
                            raw[:500].decode("utf-8", errors="ignore"),
                        )
                        await self._emit_request_telemetry(
                            telemetry_callback,
                            model=resolved_model,
                            streamed=False,
                            log_context=log_context,
                            latency_seconds=elapsed,
                            provider_retry_count=0,
                            success=False,
                            error=f"http_{resp.status}",
                        )
                        return None
            data = json.loads(raw.decode("utf-8", errors="ignore"))
            content = (data.get("message") or {}).get("content") or ""
            cleaned = _clean_ollama_response(content, expects_json=expects_json)
            await self._emit_request_telemetry(
                telemetry_callback,
                model=resolved_model,
                streamed=False,
                log_context=log_context,
                latency_seconds=elapsed,
                provider_retry_count=0,
                success=True,
                usage={
                    "prompt_eval_count": data.get("prompt_eval_count"),
                    "eval_count": data.get("eval_count"),
                },
            )
            return cleaned
        except asyncio.TimeoutError:
            elapsed = time.time() - start_time
            logger.error(
                "[%s] %s request timeout after %.1fs",
                log_context, self.log_label, elapsed,
            )
            await self._emit_request_telemetry(
                telemetry_callback,
                model=resolved_model,
                streamed=False,
                log_context=log_context,
                latency_seconds=elapsed,
                provider_retry_count=0,
                success=False,
                error="timeout",
            )
            return None
        except Exception as exc:
            elapsed = time.time() - start_time
            logger.error(
                "[%s] %s request error: %s",
                log_context, self.log_label, exc,
            )
            await self._emit_request_telemetry(
                telemetry_callback,
                model=resolved_model,
                streamed=False,
                log_context=log_context,
                latency_seconds=elapsed,
                provider_retry_count=0,
                success=False,
                error=str(exc),
            )
            return None

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
        """Vision via Ollama's /api/chat with the `images` field.

        Differences from the OpenAI vision shape:
          - Ollama wants raw base64 in `images: [...]` on the user message,
            not a `data:image/...;base64,...` URL inside a content block.
          - content_type is sniffed from the bytes by the model server,
            so we ignore the parameter (kept for interface compatibility).

        Conversation history with OpenAI-style multimodal content blocks is
        flattened to plain text — Ollama's chat history doesn't carry image
        context across turns the same way; only the latest image is sent.
        """
        del content_type  # ollama auto-detects from image bytes

        resolved_model = str(model or self.default_model or "gemma3:12b").strip()
        # Vision returns a structured dict on the OpenAI side; match the shape
        # by asking Ollama to emit JSON-formatted output.
        from core.llm_json import safe_parse_json_object

        flat_history: List[Dict[str, Any]] = []
        for msg in conversation_history or []:
            role = msg.get("role", "user")
            content = msg.get("content")
            if isinstance(content, list):
                text_parts = [
                    str(b.get("text", ""))
                    for b in content
                    if isinstance(b, dict) and b.get("type") == "text"
                ]
                content = " ".join(p for p in text_parts if p).strip()
            flat_history.append({"role": role, "content": content or ""})

        flat_history.append({
            "role": "user",
            "content": prompt,
            "images": [image_base64],
        })

        body: Dict[str, Any] = {
            "model": resolved_model,
            "messages": flat_history,
            "stream": False,
            "think": False,
            "format": "json",
            "keep_alive": self.keep_alive,
            "options": {
                "temperature": float(temperature),
                "num_predict": int(max_tokens or 1000),
            },
        }

        url = f"{self.base_url}/api/chat"
        logger.info(
            "🖼️ [%s] Vision request (Model: %s)...",
            self.log_label, resolved_model,
        )
        start_time = time.time()
        try:
            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=self.timeout_seconds),
            ) as session:
                async with session.post(url, json=body) as resp:
                    raw = await resp.read()
                    elapsed = time.time() - start_time
                    if resp.status != 200:
                        logger.error(
                            "[vision] %s returned HTTP %s after %.1fs: %s",
                            self.log_label, resp.status, elapsed,
                            raw[:500].decode("utf-8", errors="ignore"),
                        )
                        return None
            data = json.loads(raw.decode("utf-8", errors="ignore"))
            content = (data.get("message") or {}).get("content") or ""
            cleaned = _clean_ollama_response(content, expects_json=True)
            parsed = safe_parse_json_object(cleaned)
            if isinstance(parsed, dict):
                return parsed
            # Caller expects a dict; wrap free-form text in the same default
            # shape OpenAIProvider uses when its model emits non-JSON.
            return {
                "description": cleaned,
                "contextual_understanding": cleaned,
                "suggested_response": "I see your image, thanks for sharing!",
                "humor_detected": False,
                "emotional_tone": "neutral",
            }
        except asyncio.TimeoutError:
            elapsed = time.time() - start_time
            logger.error(
                "[vision] %s timeout after %.1fs (model: %s)",
                self.log_label, elapsed, resolved_model,
            )
            return None
        except Exception as exc:
            elapsed = time.time() - start_time
            logger.error(
                "[vision] %s error after %.1fs: %s",
                self.log_label, elapsed, exc,
            )
            return None
