"""Claude Code CLI provider — uses the local `claude` CLI via its
OAuth-authenticated print mode (`claude -p ...`) for one-shot LLM
completions.

Mirrors the CodexCLIProvider pattern: spawns the CLI as a subprocess,
pipes the prompt over stdin, captures stdout, returns the response.

Why this provider exists: PaLLi's delegation bridge already uses the
same Claude Max OAuth auth (via `claude-agent-sdk` + the bundled
`claude` binary). Reusing that auth for cheap fast classification
tasks — especially `combined_decision` — costs nothing beyond what's
already on the subscription AND skips the pay-per-token API path
entirely.

Pricing note (2026-04-23): on a Claude Max subscription, `claude -p`
calls count against the same weekly quota as the interactive CLI
and the delegation bridge. No per-token dollar cost. Haiku is
particularly cheap on-subscription because it's small.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import time
from typing import Any, Awaitable, Callable, Dict, List, Optional

from ai.llm_openai_compatible import BaseLLMProvider, messages_expect_json
from core.config import config


logger = logging.getLogger("ai.llm_client")


def _coerce_prompt_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    if content is None:
        return ""
    try:
        return json.dumps(content, ensure_ascii=True, indent=2)
    except Exception:
        return str(content)


def build_claude_cli_prompt(
    *,
    messages: List[Dict[str, Any]],
    max_tokens: int,
) -> str:
    """Flatten a messages list into a single prompt string for `claude -p`.

    Uses explicit role-tagged blocks so the CLI's single-string input
    preserves speaker context. Matches the Codex provider's format so
    both CLIs see structurally-equivalent prompts.
    """
    transcript_parts: List[str] = []
    for index, message in enumerate(messages or []):
        role = str((message or {}).get("role") or "user").strip().lower() or "user"
        content = _coerce_prompt_content((message or {}).get("content"))
        transcript_parts.append(
            f"<message index=\"{index}\" role=\"{role}\">\n{content}\n</message>"
        )

    json_only = messages_expect_json(messages or [])
    json_instruction = (
        "\n- The transcript requests structured JSON. Return strict JSON only "
        "with no preface, no markdown fences, no prose."
        if json_only
        else ""
    )
    normalized_max_tokens = max(int(max_tokens or 0), 1)

    return (
        "You are serving as the language-model backend for a local companion "
        "application.\n"
        "Return only the next assistant reply for the conversation transcript "
        "below.\n"
        "Rules:\n"
        "- Treat system messages as highest priority.\n"
        "- Do not mention Claude, CLI internals, tool execution, approvals, or "
        "hidden reasoning unless the transcript explicitly asks.\n"
        "- Do not claim to have taken actions. Produce only the assistant's "
        "reply text.\n"
        f"- Aim to stay within approximately {normalized_max_tokens} output "
        "tokens."
        f"{json_instruction}\n\n"
        "Conversation transcript:\n"
        f"{os.linesep.join(transcript_parts)}\n\n"
        "Return only the assistant reply."
    )


def _suffix_for_content_type(content_type: str) -> str:
    """Map an image MIME type to a temp-file suffix the CLI's Read tool
    recognizes. Defaults to .png for unknown/blank types."""
    ct = str(content_type or "").lower().strip()
    return {
        "image/png": ".png",
        "image/jpeg": ".jpg",
        "image/jpg": ".jpg",
        "image/webp": ".webp",
        "image/gif": ".gif",
    }.get(ct, ".png")


def _strip_markdown_json_fence(text: str) -> str:
    """Remove ```json ... ``` or ``` ... ``` wrappers from `claude -p` output.

    Claude often wraps JSON replies in code fences even when the prompt
    says not to. We strip defensively so safe_parse_json_object gets
    clean input.
    """
    s = str(text or "").strip()
    if not s:
        return s
    # ```json ... ```
    m = re.match(r"^```(?:json)?\s*\n?(.*?)\n?```\s*$", s, re.DOTALL)
    if m:
        return m.group(1).strip()
    return s


class ClaudeCLIProvider(BaseLLMProvider):
    """Bridge provider that uses the local Claude Code CLI (`claude -p`)."""

    CLI_CHECK_CACHE_SECONDS = 30.0

    def __init__(
        self,
        *,
        claude_path: str = "",
        default_model: str = "",
        timeout_seconds: Optional[float] = None,
    ) -> None:
        super().__init__()
        self.provider_name = "claude"
        self.log_label = "Claude CLI"
        self.base_url = "local:claude-cli"
        # Default to Haiku — fast, cheap-on-subscription, good at structured
        # output for classification tasks like combined_decision.
        self.default_model = str(
            default_model or getattr(config, "CLAUDE_CLI_MODEL", "") or "haiku"
        ).strip() or "haiku"
        self.claude_path = str(
            claude_path or getattr(config, "CLAUDE_CLI_PATH", "") or "claude"
        ).strip() or "claude"
        # 60 -> 150 (2026-08-12): the narrative author now routes here, and
        # its real prompt (~10k chars) measures 53.7s through the CLI in
        # daytime — inches under 60; the 04:00 cold start tipped it over,
        # twice per story doc (W33 attempt three). A cap is not a wait:
        # quick classification and vision calls (~13s) are unaffected.
        self.timeout_seconds = float(
            timeout_seconds
            or getattr(config, "CLAUDE_CLI_TIMEOUT_SECONDS", 150.0)
            or 150.0
        )
        # Vision is served via the CLI's Read tool on a temp image file
        # (see vision_completion). True so the vision router selects this
        # provider instead of falling back to another (e.g. OpenAI).
        self.supports_vision = True
        self._last_configured_check_at = 0.0
        self._last_configured_ok = False
        self._last_diagnostics: Dict[str, Any] = {}

    def _resolved_executable(self) -> Optional[str]:
        raw_path = str(self.claude_path or "").strip()
        if not raw_path:
            return None
        if os.path.isabs(raw_path):
            return raw_path if os.path.exists(raw_path) else None
        return shutil.which(raw_path)

    def _probe_cli(self) -> Dict[str, Any]:
        """Run `claude --version` to verify the CLI is installed + callable.

        Intentionally does NOT exercise OAuth — that would cost a call
        per is_configured() poll. We assume auth is working if the
        delegation bridge is working; first real chat_completion() will
        surface any OAuth issues.
        """
        executable = self._resolved_executable()
        if not executable:
            return {
                "configured": False,
                "login_status": "unavailable",
                "login_status_text": "Claude CLI executable was not found.",
                "claude_executable": "",
                "cli_version": "",
            }
        try:
            result = subprocess.run(
                [executable, "--version"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
        except Exception as exc:
            return {
                "configured": False,
                "login_status": "error",
                "login_status_text": f"Claude CLI --version failed: {type(exc).__name__}.",
                "claude_executable": str(executable or ""),
                "cli_version": "",
            }
        version_text = str(result.stdout or result.stderr or "").strip().splitlines()
        cli_version = version_text[0] if version_text else ""
        ok = int(result.returncode or 0) == 0 and bool(cli_version)
        return {
            "configured": ok,
            "login_status": "installed" if ok else "unavailable",
            "login_status_text": (
                f"Claude CLI {cli_version}" if ok else (cli_version or "Claude CLI unavailable")
            ),
            "claude_executable": str(executable or "").strip(),
            "cli_version": cli_version,
        }

    def is_configured(self) -> bool:
        now = time.time()
        if now - self._last_configured_check_at <= self.CLI_CHECK_CACHE_SECONDS:
            return self._last_configured_ok
        self._last_diagnostics = self._probe_cli()
        self._last_configured_ok = bool(self._last_diagnostics.get("configured"))
        self._last_configured_check_at = now
        return self._last_configured_ok

    def diagnostics(self) -> Dict[str, Any]:
        payload = super().diagnostics()
        details = dict(self._last_diagnostics or {})
        if not details:
            self.is_configured()
            details = dict(self._last_diagnostics or {})
        payload.update(details)
        return payload

    def _build_exec_args(self, *, model: str) -> List[str]:
        executable = self._resolved_executable() or self.claude_path
        args = [
            executable,
            "-p",  # print mode — non-interactive, prompt via arg/stdin, exit
            "--no-session-persistence",  # no session file churn
            "--output-format", "text",  # plain response, we strip fences
            # Performance tuning (2026-04-23): combined_decision on a bare
            # `claude -p` took 48s on Haiku. The CLI defaults include
            # extended-thinking effort and the full Claude Code tool
            # schemas / CLAUDE.md auto-discovery baked into the system
            # prompt — all dead weight for a one-shot classifier. These
            # flags strip that scaffolding and bring the same call down
            # to ~15s:
            #   --effort low     → no extended-thinking tokens
            #   --tools ""       → empty tool allowlist; no schemas in ctx
            #   --system-prompt  → replace Claude Code's default preamble
            #                      with a minimal classifier directive
            "--effort", "low",
            "--tools", "",
            "--system-prompt", (
                "You are the language-model backend for a local companion "
                "app. Respond to the single user message below. "
                "When the transcript asks for strict JSON, emit only the "
                "JSON object — no markdown fences, no preface, no prose."
            ),
            "--exclude-dynamic-system-prompt-sections",
        ]
        if model:
            args.extend(["--model", model])
        return args

    async def chat_completion(
        self,
        messages: List[Dict],
        model: str = None,
        temperature: float = 0.7,
        max_tokens: int = 2000,
        log_context: str = "general",
        telemetry_callback: Optional[Callable[[Dict[str, Any]], Awaitable[None]]] = None,
    ) -> Optional[str]:
        del temperature  # Claude CLI doesn't expose temperature via -p
        resolved_model = str(model or self.default_model or "haiku").strip()
        prompt = build_claude_cli_prompt(messages=messages, max_tokens=max_tokens)
        args = self._build_exec_args(model=resolved_model)

        logger.info(
            "🧠 [%s] Sending request (Model: %s, Context: %s)...",
            self.log_label, resolved_model, log_context,
        )
        start_time = time.time()
        process = None
        try:
            process = await asyncio.create_subprocess_exec(
                *args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                process.communicate(prompt.encode("utf-8")),
                timeout=self.timeout_seconds,
            )
            elapsed = time.time() - start_time
            stdout_text = stdout_bytes.decode("utf-8", errors="ignore")
            stderr_text = stderr_bytes.decode("utf-8", errors="ignore").strip()
            content = _strip_markdown_json_fence(stdout_text)

            if process.returncode != 0:
                logger.error(
                    "[%s] %s exited with code %s: %s",
                    log_context,
                    self.log_label,
                    process.returncode,
                    stderr_text or stdout_text[-500:],
                )
                await self._emit_request_telemetry(
                    telemetry_callback,
                    model=resolved_model,
                    streamed=False,
                    log_context=log_context,
                    latency_seconds=elapsed,
                    provider_retry_count=0,
                    success=False,
                    error=f"exit_{process.returncode}",
                )
                return None

            await self._emit_request_telemetry(
                telemetry_callback,
                model=resolved_model,
                streamed=False,
                log_context=log_context,
                latency_seconds=elapsed,
                provider_retry_count=0,
                success=True,
            )
            return content
        except asyncio.TimeoutError:
            elapsed = time.time() - start_time
            if process is not None and process.returncode is None:
                process.kill()
                try:
                    await process.wait()
                except Exception:
                    pass
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

    def _build_vision_exec_args(self, *, model: str, image_dir: str) -> List[str]:
        """CLI args for a one-shot vision read.

        Deliberately does NOT reuse the classifier args from
        ``_build_exec_args`` — vision needs the Read tool (to open the image
        file), so ``--tools ""`` must NOT be set. Matches the invocation
        verified working against ``claude -p`` directly.
        """
        executable = self._resolved_executable() or self.claude_path
        return [
            executable,
            "-p",
            "--no-session-persistence",
            "--output-format", "text",
            "--model", model or self.default_model or "haiku",
            "--allowedTools", "Read",
            # One-shot, non-interactive: bypass the permission prompt so the
            # Read call doesn't block waiting for approval that can't come.
            "--permission-mode", "bypassPermissions",
            "--add-dir", image_dir,
        ]

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
        """Analyze an image via `claude -p` reading a temp image file.

        Claude Code (on the Max/OAuth login) can read images through its
        Read tool. We decode the base64 image to a temp file, ask the CLI
        to Read it and complete the vision-analysis prompt, then parse the
        JSON response into the dict shape the vision pipeline expects. No
        per-token API cost — this rides the same subscription auth the
        delegation bridge uses.
        """
        del temperature, conversation_history  # not exposed via `claude -p`
        resolved_model = str(model or self.default_model or "haiku").strip()
        try:
            raw = base64.b64decode(image_base64)
        except Exception as exc:
            logger.error("[vision] %s: could not decode image (%s)", self.log_label, exc)
            return None

        tmp_path = ""
        process = None
        start_time = time.time()
        try:
            fd, tmp_path = tempfile.mkstemp(
                suffix=_suffix_for_content_type(content_type), prefix="palli_vision_"
            )
            with os.fdopen(fd, "wb") as handle:
                handle.write(raw)

            cli_prompt = (
                f"Use the Read tool to open the image file at {tmp_path}. "
                "Then, based on what is actually visible in that image, complete "
                "the task below. Return ONLY the requested JSON object — no "
                "markdown fences, no preface, no prose.\n\n"
                f"{prompt}"
            )
            args = self._build_vision_exec_args(
                model=resolved_model, image_dir=os.path.dirname(tmp_path)
            )
            logger.info(
                "🧠 [%s] Vision request (Model: %s)...", self.log_label, resolved_model
            )
            process = await asyncio.create_subprocess_exec(
                *args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            # Vision reads run slower than text classification; give headroom.
            vision_timeout = max(self.timeout_seconds, 120.0)
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                process.communicate(cli_prompt.encode("utf-8")),
                timeout=vision_timeout,
            )
            elapsed = time.time() - start_time
            stdout_text = stdout_bytes.decode("utf-8", errors="ignore")
            stderr_text = stderr_bytes.decode("utf-8", errors="ignore").strip()

            if process.returncode != 0:
                logger.error(
                    "[vision] %s exited with code %s: %s",
                    self.log_label,
                    process.returncode,
                    stderr_text or stdout_text[-300:],
                )
                return None

            content = _strip_markdown_json_fence(stdout_text)
            from ai.llm_providers import parse_openai_vision_response_content
            result = parse_openai_vision_response_content(content, log_label=self.log_label)
            logger.info("[vision] %s complete in %.1fs", self.log_label, elapsed)
            return result
        except asyncio.TimeoutError:
            if process is not None and process.returncode is None:
                process.kill()
                try:
                    await process.wait()
                except Exception:
                    pass
            logger.error("[vision] %s request timeout", self.log_label)
            return None
        except Exception as exc:
            logger.error("[vision] %s request error: %s", self.log_label, exc)
            return None
        finally:
            if tmp_path:
                try:
                    os.unlink(tmp_path)
                except Exception:
                    pass
