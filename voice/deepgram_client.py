"""
Deepgram client for TTS and STT services
"""

import asyncio
import logging
import tempfile
import json
import io
from typing import Optional, Dict, Any
import aiohttp
import aiofiles

from core.config import config
from core.activity_logger import activity_logger

logger = logging.getLogger(__name__)


class DeepgramClient:
    """Client for Deepgram TTS and STT services"""

    def __init__(self):
        self.api_key = config.DEEPGRAM_API_KEY
        self.base_url = "https://api.deepgram.com/v1"
        self.session = None

    async def _get_session(self):
        """Get or create aiohttp session"""
        if self.session is None or self.session.closed:
            headers = {
                "Authorization": f"Token {self.api_key}",
                "Content-Type": "application/json",
            }
            connector = aiohttp.TCPConnector(enable_cleanup_closed=True)
            self.session = aiohttp.ClientSession(headers=headers, connector=connector)
        return self.session

    async def _reset_session(self) -> None:
        if self.session and not self.session.closed:
            await self.session.close()
        self.session = None

    @staticmethod
    def _is_retryable_stt_exception(error: Exception) -> bool:
        if isinstance(error, (asyncio.TimeoutError, aiohttp.ClientError, OSError)):
            return True
        return False

    async def transcribe_audio(
        self,
        audio_data: bytes,
        content_type: str = "audio/wav",
        model: str = None,
        *,
        encoding: str | None = None,
        sample_rate: int | None = None,
    ) -> Optional[str]:
        """
        Transcribe audio using Deepgram STT

        Args:
            audio_data: Raw audio bytes
            content_type: MIME type of audio
            model: Deepgram model to use (defaults to config)

        Returns:
            Transcribed text or None if failed
        """
        if not self.api_key:
            activity_logger.log_warning("deepgram", "API key not configured")
            return None

        # Use configured model or override
        stt_model = model or getattr(config, 'DEEPGRAM_STT_MODEL', 'nova-2')

        # Deepgram STT parameters. Deepgram infers encoding/sample rate for containerized formats
        # like WAV/OGG/MP4; raw audio formats need them explicitly.
        params = {
            "model": stt_model,
            "smart_format": "true",
            "punctuate": "true",
            "diarize": "false",
            "language": "en-US",
        }
        if not self._is_containerized_audio(content_type):
            params["encoding"] = encoding or "linear16"
            if sample_rate:
                params["sample_rate"] = str(sample_rate)

        url = f"{self.base_url}/listen"
        request_headers = {
            "Authorization": f"Token {self.api_key}",
            "Content-Type": content_type,
        }
        timeout = aiohttp.ClientTimeout(total=45, connect=10, sock_connect=10, sock_read=35)
        max_attempts = 2

        for attempt in range(1, max_attempts + 1):
            try:
                session = await self._get_session()
                async with session.post(
                    url,
                    params=params,
                    data=audio_data,
                    headers=request_headers,
                    timeout=timeout,
                ) as response:
                    if response.status == 200:
                        result = await response.json()
                        transcript = self._extract_transcript(result)

                        if transcript:
                            return transcript
                        activity_logger.log_warning("deepgram", "STT returned empty transcript")
                        return None

                    error_text = await response.text()
                    activity_logger.log_error("deepgram", f"STT error {response.status}: {error_text}")
                    return None
            except Exception as e:
                retryable = self._is_retryable_stt_exception(e)
                if retryable and attempt < max_attempts:
                    logger.warning(
                        "Deepgram STT attempt %s/%s failed with a retryable error; recreating session: %s",
                        attempt,
                        max_attempts,
                        e,
                    )
                    await self._reset_session()
                    await asyncio.sleep(0.15)
                    continue
                if isinstance(e, asyncio.TimeoutError):
                    activity_logger.log_error("deepgram", "STT request timeout")
                else:
                    activity_logger.log_error("deepgram", f"STT error: {e}")
                return None

    async def synthesize_speech(
        self,
        text: str,
        model: str = None,
        encoding: str = "linear16",
        sample_rate: int = 48000,
        container: str | None = None,
    ) -> Optional[bytes]:
        """
        Synthesize speech using Deepgram TTS

        Args:
            text: Text to synthesize
            model: Deepgram TTS model (defaults to config)
            encoding: Audio encoding format
            sample_rate: Audio sample rate

        Returns:
            Audio bytes or None if failed
        """
        try:
            if not self.api_key:
                activity_logger.log_warning("deepgram", "API key not configured")
                return None

            session = await self._get_session()

            # Use configured model or override
            tts_model = model or getattr(config, 'DEEPGRAM_TTS_MODEL', 'aura-asteria-en')

            # Deepgram TTS parameters
            params = {
                "model": tts_model,
                "encoding": encoding,
                "sample_rate": str(sample_rate),
            }
            if container:
                params["container"] = container

            # Request payload
            payload = {"text": text}

            url = f"{self.base_url}/speak"

            # Prepare headers for this request
            request_headers = {
                "Authorization": f"Token {self.api_key}",
                "Content-Type": "application/json",
            }

            async with session.post(
                url,
                params=params,
                json=payload,
                headers=request_headers,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as response:

                if response.status == 200:
                    audio_data = await response.read()
                    return audio_data
                else:
                    error_text = await response.text()
                    activity_logger.log_error("deepgram", f"TTS error {response.status}: {error_text}")
                    return None

        except asyncio.TimeoutError:
            activity_logger.log_error("deepgram", "TTS request timeout")
            return None
        except Exception as e:
            activity_logger.log_error("deepgram", f"TTS error: {e}")
            return None

    async def transcribe_file(self, file_path: str, model: str = None) -> Optional[str]:
        """
        Transcribe audio file using Deepgram STT

        Args:
            file_path: Path to audio file
            model: Deepgram model to use

        Returns:
            Transcribed text or None if failed
        """
        try:
            # Read file
            async with aiofiles.open(file_path, "rb") as f:
                audio_data = await f.read()

            # Determine content type from file extension
            if file_path.endswith(".wav"):
                content_type = "audio/wav"
            elif file_path.endswith(".mp3"):
                content_type = "audio/mpeg"
            elif file_path.endswith(".ogg"):
                content_type = "audio/ogg"
            elif file_path.endswith(".m4a"):
                content_type = "audio/mp4"
            else:
                content_type = "audio/wav"  # Default

            return await self.transcribe_audio(audio_data, content_type, model)

        except Exception as e:
            logger.error(f"Error transcribing file {file_path}: {e}")
            return None

    async def synthesize_to_file(
        self,
        text: str,
        output_path: str,
        model: str = None,
        encoding: str = "linear16",
        sample_rate: int = 48000,
    ) -> bool:
        """
        Synthesize speech and save to file

        Args:
            text: Text to synthesize
            output_path: Path to save audio file
            model: Deepgram TTS model
            encoding: Audio encoding
            sample_rate: Audio sample rate

        Returns:
            True if successful, False otherwise
        """
        try:
            audio_data = await self.synthesize_speech(
                text, model, encoding, sample_rate
            )

            if audio_data:
                async with aiofiles.open(output_path, "wb") as f:
                    await f.write(audio_data)
                logger.debug(f"Saved TTS audio to {output_path}")
                return True

            return False

        except Exception as e:
            logger.error(f"Error saving TTS to file {output_path}: {e}")
            return False

    def _extract_transcript(self, deepgram_response: Dict[str, Any]) -> Optional[str]:
        """
        Extract transcript text from Deepgram response

        Args:
            deepgram_response: JSON response from Deepgram

        Returns:
            Extracted transcript or None
        """
        try:
            # Navigate Deepgram response structure
            results = deepgram_response.get("results", {})
            channels = results.get("channels", [])

            if not channels:
                return None

            # Get first channel
            channel = channels[0]
            alternatives = channel.get("alternatives", [])

            if not alternatives:
                return None

            # Get best alternative (first one)
            alternative = alternatives[0]
            transcript = alternative.get("transcript", "").strip()

            return transcript if transcript else None

        except Exception as e:
            logger.error(f"Error extracting transcript from Deepgram response: {e}")
            return None

    def _is_containerized_audio(self, content_type: str) -> bool:
        normalized = (content_type or "").split(";")[0].strip().lower()
        return normalized in {"audio/wav", "audio/x-wav", "audio/mpeg", "audio/ogg", "audio/mp4", "audio/webm"}

    async def close(self):
        """Close the aiohttp session"""
        await self._reset_session()


# Global Deepgram client instance
deepgram_client = DeepgramClient()
