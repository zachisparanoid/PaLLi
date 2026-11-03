from __future__ import annotations

import asyncio
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


def build_codex_cli_prompt(
    *,
    messages: List[Dict[str, Any]],
    max_tokens: int,
) -> str:
    transcript_parts: List[str] = []
    for index, message in enumerate(messages or []):
        role = str((message or {}).get("role") or "user").strip().lower() or "user"
        content = _coerce_prompt_content((message or {}).get("content"))
        transcript_parts.append(
            f"<message index=\"{index}\" role=\"{role}\">\n{content}\n</message>"
        )

    json_only = messages_expect_json(messages or [])
    json_instruction = (
        "\n- The transcript requests structured JSON. Return strict JSON only with no preface."
        if json_only
        else ""
    )
    normalized_max_tokens = max(int(max_tokens or 0), 1)

    return (
        "You are serving as the language-model backend for a local companion application.\n"
        "Return only the next assistant reply for the conversation transcript below.\n"
        "Rules:\n"
        "- Treat system messages as highest priority.\n"
        "- Do not mention Codex, CLI internals, tool execution, approvals, or hidden reasoning unless the transcript explicitly asks.\n"
        "- Do not claim to have taken actions. Produce only the assistant's reply text.\n"
        f"- Aim to stay within approximately {normalized_max_tokens} output tokens."
        f"{json_instruction}\n\n"
        "Conversation transcript:\n"
        f"{os.linesep.join(transcript_parts)}\n\n"
        "Return only the assistant reply."
    )


class CodexCLIProvider(BaseLLMProvider):
    """Bridge provider that uses the official local Codex CLI login/runtime."""

    LOGIN_STATUS_CACHE_SECONDS = 30.0

    def __init__(
        self,
        *,
        codex_path: str = "",
        default_model: str = "",
        profile: str = "",
        workspace_dir: str = "",
        timeout_seconds: Optional[float] = None,
    ) -> None:
        super().__init__()
        self.provider_name = "codex"
        self.log_label = "Codex CLI"
        self.base_url = "local:codex-cli"
        self.default_model = str(default_model or getattr(config, "CODEX_MODEL", "") or "gpt-5.5").strip()
        self.profile = str(profile or getattr(config, "CODEX_PROFILE", "") or "").strip()
        self.codex_path = str(codex_path or getattr(config, "CODEX_CLI_PATH", "") or "codex").strip() or "codex"
        self.workspace_dir = self._resolve_workspace_dir(
            workspace_dir or getattr(config, "CODEX_WORKDIR", "")
        )
        self.timeout_seconds = float(
            timeout_seconds
            or getattr(config, "CODEX_TIMEOUT_SECONDS", 120.0)
            or 120.0
        )
        self.supports_vision = False
        self._last_login_status_check_at = 0.0
        self._last_login_status_ok = False
        self._last_login_status_details: Dict[str, Any] = {}

    @staticmethod
    def _resolve_workspace_dir(raw_value: str) -> str:
        candidate = str(raw_value or "").strip()
        if candidate:
            normalized = os.path.abspath(os.path.expanduser(candidate))
        else:
            normalized = os.path.join(tempfile.gettempdir(), "palli-codex-cli-provider")
        os.makedirs(normalized, exist_ok=True)
        return normalized

    def _resolved_codex_executable(self) -> Optional[str]:
        raw_path = str(self.codex_path or "").strip()
        if not raw_path:
            return None
        if os.path.isabs(raw_path):
            return raw_path if os.path.exists(raw_path) else None
        return shutil.which(raw_path)

    @staticmethod
    def _parse_login_status(
        *,
        returncode: int,
        stdout_text: str,
        stderr_text: str,
        executable_path: str,
    ) -> Dict[str, Any]:
        combined = str(stdout_text or stderr_text or "").strip()
        first_line = next((line.strip() for line in combined.splitlines() if line.strip()), "")
        lowered = first_line.lower()
        auth_mode = "unknown"
        login_status_text = first_line or "Codex CLI login status is unavailable."
        if "chatgpt" in lowered:
            auth_mode = "chatgpt"
            login_status_text = "Logged in using ChatGPT"
        elif "api key" in lowered:
            auth_mode = "apikey"
            login_status_text = "Logged in using API key"
        elif int(returncode or 0) != 0:
            auth_mode = "unauthenticated"
            login_status_text = first_line or "Codex CLI is not authenticated."
        elif first_line:
            login_status_text = re.sub(r"sk-[A-Za-z0-9_\\-*]+", "[redacted-api-key]", first_line)
        return {
            "configured": int(returncode or 0) == 0,
            "login_status": (
                "authenticated"
                if int(returncode or 0) == 0
                else "unauthenticated"
            ),
            "auth_mode": auth_mode,
            "login_status_text": login_status_text,
            "codex_executable": str(executable_path or "").strip(),
        }

    def _login_status_details(self) -> Dict[str, Any]:
        executable = self._resolved_codex_executable()
        if not executable:
            return {
                "configured": False,
                "login_status": "unavailable",
                "auth_mode": "unavailable",
                "login_status_text": "Codex CLI executable was not found.",
                "codex_executable": "",
            }
        try:
            result = subprocess.run(
                [executable, "login", "status"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
        except Exception as exc:
            return {
                "configured": False,
                "login_status": "error",
                "auth_mode": "error",
                "login_status_text": f"Codex CLI login status check failed: {type(exc).__name__}.",
                "codex_executable": str(executable or "").strip(),
            }
        return self._parse_login_status(
            returncode=int(result.returncode or 0),
            stdout_text=str(result.stdout or ""),
            stderr_text=str(result.stderr or ""),
            executable_path=str(executable or "").strip(),
        )

    def is_configured(self) -> bool:
        now = time.time()
        if now - self._last_login_status_check_at <= self.LOGIN_STATUS_CACHE_SECONDS:
            return self._last_login_status_ok
        self._last_login_status_details = self._login_status_details()
        self._last_login_status_ok = bool(self._last_login_status_details.get("configured"))
        self._last_login_status_check_at = now
        return self._last_login_status_ok

    def diagnostics(self) -> Dict[str, Any]:
        payload = super().diagnostics()
        details = dict(self._last_login_status_details or {})
        if not details:
            self.is_configured()
            details = dict(self._last_login_status_details or {})
        payload["workspace_dir"] = self.workspace_dir
        payload["profile"] = self.profile
        payload.update(details)
        return payload

    def _build_exec_args(self, *, model: str, output_path: str) -> List[str]:
        executable = self._resolved_codex_executable() or self.codex_path
        args = [
            executable,
            "exec",
            "--json",
            "--ephemeral",
            "--skip-git-repo-check",
            "--sandbox",
            "read-only",
            "-C",
            self.workspace_dir,
            "--output-last-message",
            output_path,
        ]
        if self.profile:
            args.extend(["--profile", self.profile])
        if model:
            args.extend(["--model", model])
        args.append("-")
        return args

    @staticmethod
    def _extract_response(stdout_text: str) -> tuple[str, Dict[str, Any]]:
        content = ""
        usage: Dict[str, Any] = {}
        for raw_line in str(stdout_text or "").splitlines():
            line = raw_line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            payload_type = str(payload.get("type") or "").strip().lower()
            if payload_type == "item.completed":
                item = payload.get("item") or {}
                if str(item.get("type") or "").strip().lower() == "agent_message":
                    text = item.get("text")
                    if isinstance(text, str):
                        content = text
            elif payload_type == "turn.completed":
                usage = dict(payload.get("usage") or {})
        return content, usage

    async def chat_completion(
        self,
        messages: List[Dict],
        model: str = None,
        temperature: float = 0.7,
        max_tokens: int = 2000,
        log_context: str = "general",
        telemetry_callback: Optional[Callable[[Dict[str, Any]], Awaitable[None]]] = None,
    ) -> Optional[str]:
        del temperature
        resolved_model = str(model or self.default_model or "gpt-5.5").strip()
        prompt = build_codex_cli_prompt(messages=messages, max_tokens=max_tokens)
        fd, output_path = tempfile.mkstemp(prefix="palli-codex-last-message-", suffix=".txt")
        os.close(fd)
        args = self._build_exec_args(model=resolved_model, output_path=output_path)

        logger.info("🧠 [%s] Sending request (Model: %s, Context: %s)...", self.log_label, resolved_model, log_context)
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
            content, usage = self._extract_response(stdout_text)
            if os.path.exists(output_path):
                try:
                    with open(output_path, "r", encoding="utf-8") as handle:
                        file_content = handle.read().strip()
                except Exception:
                    file_content = ""
                if file_content:
                    content = file_content

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
                usage=usage,
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
            logger.error("[%s] %s request timeout after %.1fs", log_context, self.log_label, elapsed)
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
            logger.error("[%s] %s request error: %s", log_context, self.log_label, exc)
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
        finally:
            try:
                os.remove(output_path)
            except OSError:
                pass

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
        del image_base64, prompt, conversation_history, model, temperature, max_tokens, content_type
        logger.warning("%s provider does not support vision completion - returning None", self.log_label)
        return None
