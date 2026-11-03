"""
Voice runtime dependency/bootstrap diagnostics.
"""

from __future__ import annotations

import ctypes.util
import importlib
import logging
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Any

from core.config import config
from voice.types import VoiceBootstrapStatus

logger = logging.getLogger(__name__)
_DECODE_GUARD_WARNINGS: dict[int | None, float] = {}
_DAVE_DECRYPT_WARNINGS: dict[int | None, float] = {}
_DAVE_DECRYPT_LOGGED: set[tuple[int | None, int | None]] = set()
_DAVE_PASSTHROUGH_LOGGED: set[tuple[int | None, int | None]] = set()


def _safe_import(module_name: str) -> tuple[bool, str]:
    try:
        importlib.import_module(module_name)
        return True, "ok"
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


def _candidate_opus_library_names() -> tuple[str, ...]:
    pointer_bits = 64 if sys.maxsize > 2**32 else 32
    windows_names = (
        "libopus-0.x64.dll",
        "libopus-0.x86.dll",
    )
    if pointer_bits == 32:
        windows_names = tuple(reversed(windows_names))
    return windows_names + (
        "opus.dll",
        "libopus.dll",
        "libopus.so.0",
        "libopus.dylib",
    )


def _candidate_opus_search_roots() -> list[Path]:
    roots: list[Path] = []
    seen: set[str] = set()

    def _add_root(path: Path | None) -> None:
        if path is None:
            return
        try:
            normalized = str(path.resolve())
        except Exception:
            normalized = str(path)
        if not normalized or normalized in seen:
            return
        seen.add(normalized)
        roots.append(path)

    try:
        import discord

        discord_root = Path(getattr(discord, "__file__", "")).resolve().parent
        _add_root(discord_root / "bin")
    except Exception:
        pass

    for prefix in (os.environ.get("VIRTUAL_ENV"), sys.prefix, sys.base_prefix):
        if not prefix:
            continue
        prefix_path = Path(prefix)
        _add_root(prefix_path / "Lib" / "site-packages" / "discord" / "bin")
        _add_root(prefix_path / "lib" / "site-packages" / "discord" / "bin")

    return roots


def _candidate_opus_library_paths() -> list[str]:
    candidates: list[str] = []
    seen: set[str] = set()
    for root in _candidate_opus_search_roots():
        for library_name in _candidate_opus_library_names():
            candidate = root / library_name
            if not candidate.exists():
                continue
            resolved = str(candidate.resolve())
            if resolved in seen:
                continue
            seen.add(resolved)
            candidates.append(resolved)
    return candidates


def ensure_opus_loaded() -> tuple[bool, str]:
    try:
        import discord.opus as opus
    except Exception as exc:
        return False, f"discord.opus import failed: {exc}"

    try:
        if opus.is_loaded():
            return True, "opus already loaded"
        library_name = str(getattr(config, "VOICE_OPUS_LIBRARY_PATH", "") or "").strip()
        if library_name:
            opus.load_opus(library_name)
            return opus.is_loaded(), f"loaded explicit opus library: {library_name}"
        for bundled_candidate in _candidate_opus_library_paths():
            try:
                opus.load_opus(bundled_candidate)
                return opus.is_loaded(), f"loaded bundled opus library: {bundled_candidate}"
            except Exception:
                continue
        for detected_name in ("opus", "libopus"):
            detected = ctypes.util.find_library(detected_name)
            if not detected:
                continue
            opus.load_opus(detected)
            return opus.is_loaded(), f"loaded detected opus library: {detected}"
        return False, "opus library not found"
    except Exception as exc:
        return False, f"failed to load opus: {exc}"


def _get_davey_module():
    try:
        return importlib.import_module("davey")
    except Exception:
        return None


def _get_voice_client_from_decoder(packet_decoder) -> Any | None:
    try:
        sink = getattr(packet_decoder, "sink", None)
        return getattr(sink, "voice_client", None)
    except Exception:
        return None


def _resolve_user_id_for_decoder(packet_decoder, voice_client: Any | None) -> int | None:
    user_id = getattr(packet_decoder, "_cached_id", None)
    if user_id:
        return int(user_id)

    if voice_client is None:
        return None

    try:
        user_id = voice_client._get_id_from_ssrc(getattr(packet_decoder, "ssrc", None))
    except Exception:
        return None

    if user_id:
        setattr(packet_decoder, "_cached_id", user_id)
        return int(user_id)
    return None


def _log_dave_decrypt_active(*, ssrc: int | None, user_id: int | None) -> None:
    stream_key = (ssrc, user_id)
    if stream_key in _DAVE_DECRYPT_LOGGED:
        return
    _DAVE_DECRYPT_LOGGED.add(stream_key)
    logger.info("Inbound DAVE decryption active for user_id=%s ssrc=%s", user_id, ssrc)


def _log_dave_passthrough_active(*, ssrc: int | None, user_id: int | None) -> None:
    stream_key = (ssrc, user_id)
    if stream_key in _DAVE_PASSTHROUGH_LOGGED:
        return
    _DAVE_PASSTHROUGH_LOGGED.add(stream_key)
    logger.info("Inbound DAVE passthrough active for user_id=%s ssrc=%s", user_id, ssrc)


def _log_dave_decrypt_warning(*, ssrc: int | None, packet_seq: int | None, error: str) -> None:
    now = time.monotonic()
    last_warning_at = _DAVE_DECRYPT_WARNINGS.get(ssrc, 0.0)
    if now - last_warning_at >= 1.0:
        logger.warning("Inbound DAVE decryption failed for ssrc=%s seq=%s: %s", ssrc, packet_seq, error)
        _DAVE_DECRYPT_WARNINGS[ssrc] = now
    else:
        logger.debug("Additional inbound DAVE decryption failure for ssrc=%s seq=%s", ssrc, packet_seq)


def _is_unencrypted_passthrough_error(error: Exception) -> bool:
    return "UnencryptedWhenPassthroughDisabled" in str(error)


def _attempt_dave_decrypt(packet_decoder, packet) -> tuple[bool, str | None]:
    if not packet:
        return False, None

    encrypted_payload = getattr(packet, "decrypted_data", None)
    if not encrypted_payload:
        return False, None

    voice_client = _get_voice_client_from_decoder(packet_decoder)
    connection = getattr(voice_client, "_connection", None) if voice_client is not None else None
    dave_session = getattr(connection, "dave_session", None) if connection is not None else None
    if dave_session is None or not getattr(dave_session, "ready", False):
        return False, None

    user_id = _resolve_user_id_for_decoder(packet_decoder, voice_client)
    if user_id is None:
        return False, "missing user mapping for DAVE packet"

    try:
        if dave_session.can_passthrough(user_id):
            return False, None
    except Exception:
        pass

    davey = _get_davey_module()
    if davey is None:
        return False, "davey unavailable"

    try:
        decrypted_payload = dave_session.decrypt(user_id, davey.MediaType.audio, encrypted_payload)
    except Exception as exc:
        if _is_unencrypted_passthrough_error(exc):
            _log_dave_passthrough_active(ssrc=getattr(packet_decoder, "ssrc", None), user_id=user_id)
            return False, None
        return False, f"{type(exc).__name__}: {exc}"

    if not decrypted_payload:
        return False, "empty decrypted payload"

    packet.decrypted_data = bytes(decrypted_payload)
    _log_dave_decrypt_active(ssrc=getattr(packet_decoder, "ssrc", None), user_id=user_id)
    return True, None


def _build_concealment_pcm(packet_decoder, *, decrypt_error: str | None = None) -> bytes:
    decoder = getattr(packet_decoder, "_decoder", None)
    if decoder is not None:
        try:
            next_buffer = getattr(packet_decoder, "_buffer", None)
            next_packet = next_buffer.peek_next() if next_buffer is not None else None
            if next_packet is not None:
                _, next_packet_error = _attempt_dave_decrypt(packet_decoder, next_packet)
                next_data = getattr(next_packet, "decrypted_data", None)
                if next_packet_error is None and next_data:
                    return decoder.decode(next_data, fec=True)
            return decoder.decode(None, fec=False)
        except Exception:
            pass

    channels = int(getattr(getattr(decoder, "__class__", None), "CHANNELS", 2) or 2)
    samples_per_frame = int(getattr(getattr(decoder, "__class__", None), "SAMPLES_PER_FRAME", 960) or 960)
    return b"\x00" * (samples_per_frame * channels * 2)


def _patch_packet_decoder_errors(packet_decoder_cls, opus_error_cls) -> tuple[bool, str]:
    current = getattr(packet_decoder_cls, "_decode_packet", None)
    if current is None:
        return False, "packet decoder missing _decode_packet"
    if getattr(current, "__palli_decode_guard__", False):
        return True, "decode guard already applied"

    original = current

    def _guarded_decode(self, packet):
        ssrc = getattr(self, "ssrc", None)
        packet_seq = getattr(packet, "sequence", None)

        _, dave_error = _attempt_dave_decrypt(self, packet)
        if dave_error is not None:
            _log_dave_decrypt_warning(ssrc=ssrc, packet_seq=packet_seq, error=dave_error)
            return packet, _build_concealment_pcm(self, decrypt_error=dave_error)

        try:
            return original(self, packet)
        except opus_error_cls as exc:
            now = time.monotonic()
            last_warning_at = _DECODE_GUARD_WARNINGS.get(ssrc, 0.0)
            pcm = _build_concealment_pcm(self)

            if now - last_warning_at >= 1.0:
                logger.warning(
                    "Recovered corrupt Discord voice packet for ssrc=%s seq=%s using concealment: %s",
                    ssrc,
                    packet_seq,
                    exc,
                )
                _DECODE_GUARD_WARNINGS[ssrc] = now
            else:
                logger.debug(
                    "Recovered additional corrupt Discord voice packet for ssrc=%s seq=%s",
                    ssrc,
                    packet_seq,
                )
            return packet, pcm

    _guarded_decode.__palli_decode_guard__ = True
    packet_decoder_cls._palli_original_decode_packet = original
    packet_decoder_cls._decode_packet = _guarded_decode
    return True, "decode guard with concealment applied"


def ensure_voice_recv_decode_guard() -> tuple[bool, str]:
    try:
        from discord.ext.voice_recv.opus import PacketDecoder
        from discord.opus import OpusError
    except Exception as exc:
        return False, f"decode guard import failed: {exc}"

    try:
        return _patch_packet_decoder_errors(PacketDecoder, OpusError)
    except Exception as exc:
        return False, f"failed to apply decode guard: {exc}"


def describe_voice_connection_state(voice_client: Any) -> dict[str, Any]:
    """Summarize the live Discord voice connection's DAVE-related state."""
    details: dict[str, Any] = {
        "connected": bool(getattr(voice_client, "is_connected", lambda: False)()),
        "connection_present": False,
        "max_dave_protocol_version": None,
        "dave_protocol_version": None,
        "dave_session_present": False,
        "dave_session_ready": False,
        "can_encrypt": None,
    }

    connection = getattr(voice_client, "_connection", None)
    if connection is None:
        return details

    details["connection_present"] = True

    for field in ("max_dave_protocol_version", "dave_protocol_version", "can_encrypt"):
        try:
            details[field] = getattr(connection, field, None)
        except Exception:
            details[field] = None

    dave_session = getattr(connection, "dave_session", None)
    details["dave_session_present"] = dave_session is not None
    if dave_session is not None:
        try:
            details["dave_session_ready"] = bool(getattr(dave_session, "ready", False))
        except Exception as exc:
            details["dave_session_ready"] = f"error:{type(exc).__name__}"

    return details


def voice_bootstrap_status() -> VoiceBootstrapStatus:
    messages: list[str] = []
    details: dict[str, Any] = {}

    if not config.ENABLE_VOICE_FEATURES:
        messages.append("voice features disabled by config")
        return VoiceBootstrapStatus(ok=False, messages=messages, details={"enabled": False})

    details["enabled"] = True

    ok_voice_recv, msg_voice_recv = _safe_import("discord.ext.voice_recv")
    details["discord_ext_voice_recv"] = msg_voice_recv
    messages.append(f"discord-ext-voice-recv: {msg_voice_recv}")

    ok_davey, msg_davey = _safe_import("davey")
    details["davey_available"] = ok_davey
    details["davey"] = msg_davey
    messages.append(f"davey: {msg_davey}")

    ok_nacl, msg_nacl = _safe_import("nacl")
    details["pynacl"] = msg_nacl
    messages.append(f"PyNaCl: {msg_nacl}")

    ffmpeg_path = shutil.which("ffmpeg")
    details["ffmpeg"] = ffmpeg_path or "missing"
    messages.append(f"ffmpeg: {ffmpeg_path or 'missing'}")

    opus_ok, opus_message = ensure_opus_loaded()
    details["opus"] = opus_message
    messages.append(f"opus: {opus_message}")

    decode_guard_ok, decode_guard_message = ensure_voice_recv_decode_guard()
    details["voice_recv_decode_guard"] = decode_guard_message
    messages.append(f"voice-recv decode guard: {decode_guard_message}")

    if not config.DEEPGRAM_API_KEY:
        details["deepgram"] = "missing API key"
        messages.append("deepgram: missing API key")
    else:
        details["deepgram"] = "configured"
        messages.append("deepgram: configured")

    ok = all([ok_voice_recv, ok_davey, ok_nacl, bool(ffmpeg_path), opus_ok, decode_guard_ok, bool(config.DEEPGRAM_API_KEY)])
    return VoiceBootstrapStatus(ok=ok, messages=messages, details=details)


def require_voice_runtime_ready() -> VoiceBootstrapStatus:
    status = voice_bootstrap_status()
    if not status.ok:
        logger.error("Voice runtime bootstrap failed: %s", "; ".join(status.messages))
    return status
