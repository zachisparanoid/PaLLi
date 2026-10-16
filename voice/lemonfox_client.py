"""Lemonfox TTS client (2026-07-14; provider of record since 2026-08-20).

Added when Cartesia hit its credit limit mid voice smoke-test: an
OpenAI-compatible /v1/audio/speech provider with stock (Kokoro) voices.
Small async client, bytes out, best-effort logging, module singleton.
Step 31 (owner's call, 2026-08-20) cut Cartesia entirely — this is now
PaLLi's TTS provider of record, and LEMONFOX_TTS_VOICE is her chosen
voice (olivia, owner-picked 2026-08-21).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

import aiohttp

from core.activity_logger import activity_logger
from core.config import config

logger = logging.getLogger(__name__)


class LemonfoxClient:
    """Small async client for Lemonfox TTS bytes generation."""

    def __init__(self) -> None:
        self.api_key = str(getattr(config, "LEMONFOX_API_KEY", "") or "")
        self.base_url = "https://api.lemonfox.ai"
        self.session: aiohttp.ClientSession | None = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self.session is None or self.session.closed:
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            }
            self.session = aiohttp.ClientSession(headers=headers)
        return self.session

    async def synthesize_speech(
        self,
        *,
        text: str,
        voice: str | None = None,
        response_format: str = "mp3",
    ) -> Optional[bytes]:
        if not self.api_key:
            activity_logger.log_warning("lemonfox", "API key not configured")
            return None

        if not text.strip():
            return None

        session = await self._get_session()
        payload = {
            "input": text,
            "voice": voice
            or str(getattr(config, "LEMONFOX_TTS_VOICE", "") or "sarah"),
            "response_format": response_format,
        }
        url = f"{self.base_url}/v1/audio/speech"

        try:
            async with session.post(
                url,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as response:
                if response.status == 200:
                    return await response.read()
                error_text = await response.text()
                activity_logger.log_error(
                    "lemonfox", f"TTS error {response.status}: {error_text}"
                )
                return None
        except asyncio.TimeoutError:
            activity_logger.log_error("lemonfox", "TTS request timeout")
            return None
        except Exception as exc:
            activity_logger.log_error("lemonfox", f"TTS error: {exc}")
            return None

    async def close(self) -> None:
        if self.session and not self.session.closed:
            await self.session.close()


lemonfox_client = LemonfoxClient()
