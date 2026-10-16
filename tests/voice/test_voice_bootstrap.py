from __future__ import annotations

import sys
import types

from voice.bootstrap import (
    _patch_packet_decoder_errors,
    describe_voice_connection_state,
    ensure_opus_loaded,
    voice_bootstrap_status,
)


def test_voice_bootstrap_reports_failures(monkeypatch):
    monkeypatch.setattr("voice.bootstrap.config.ENABLE_VOICE_FEATURES", True, raising=False)
    monkeypatch.setattr("voice.bootstrap.config.DEEPGRAM_API_KEY", "", raising=False)
    monkeypatch.setattr("voice.bootstrap._safe_import", lambda module_name: (False, f"{module_name} missing"))
    monkeypatch.setattr("voice.bootstrap.ensure_opus_loaded", lambda: (False, "opus missing"))
    monkeypatch.setattr("voice.bootstrap.ensure_voice_recv_decode_guard", lambda: (False, "decode guard missing"))
    monkeypatch.setattr("voice.bootstrap.shutil.which", lambda _: None)

    status = voice_bootstrap_status()

    assert status.ok is False
    assert status.details["deepgram"] == "missing API key"
    assert status.details["ffmpeg"] == "missing"


def test_voice_bootstrap_reports_success(monkeypatch):
    monkeypatch.setattr("voice.bootstrap.config.ENABLE_VOICE_FEATURES", True, raising=False)
    monkeypatch.setattr("voice.bootstrap.config.DEEPGRAM_API_KEY", "dg-test", raising=False)
    monkeypatch.setattr("voice.bootstrap._safe_import", lambda module_name: (True, "ok"))
    monkeypatch.setattr("voice.bootstrap.ensure_opus_loaded", lambda: (True, "opus ok"))
    monkeypatch.setattr("voice.bootstrap.ensure_voice_recv_decode_guard", lambda: (True, "decode guard applied"))
    monkeypatch.setattr("voice.bootstrap.shutil.which", lambda _: "/usr/bin/ffmpeg")

    status = voice_bootstrap_status()

    assert status.ok is True
    assert status.details["deepgram"] == "configured"
    assert status.details["davey_available"] is True


def test_voice_bootstrap_reports_failure_without_davey(monkeypatch):
    monkeypatch.setattr("voice.bootstrap.config.ENABLE_VOICE_FEATURES", True, raising=False)
    monkeypatch.setattr("voice.bootstrap.config.DEEPGRAM_API_KEY", "dg-test", raising=False)

    def _fake_safe_import(module_name: str):
        if module_name == "davey":
            return False, "davey missing"
        return True, "ok"

    monkeypatch.setattr("voice.bootstrap._safe_import", _fake_safe_import)
    monkeypatch.setattr("voice.bootstrap.ensure_opus_loaded", lambda: (True, "opus ok"))
    monkeypatch.setattr("voice.bootstrap.ensure_voice_recv_decode_guard", lambda: (True, "decode guard applied"))
    monkeypatch.setattr("voice.bootstrap.shutil.which", lambda _: "/usr/bin/ffmpeg")

    status = voice_bootstrap_status()

    assert status.ok is False
    assert status.details["davey_available"] is False
    assert "missing" in status.details["davey"]


def test_ensure_opus_loaded_uses_bundled_discord_library(monkeypatch, tmp_path):
    fake_discord_root = tmp_path / "discord"
    fake_bin_root = fake_discord_root / "bin"
    fake_bin_root.mkdir(parents=True)

    bundled_paths = []
    for library_name in ("libopus-0.x64.dll", "libopus-0.x86.dll"):
        candidate = fake_bin_root / library_name
        candidate.write_bytes(b"stub")
        bundled_paths.append(str(candidate.resolve()))

    fake_discord = types.ModuleType("discord")
    fake_discord.__file__ = str((fake_discord_root / "__init__.py").resolve())
    fake_discord.__path__ = [str(fake_discord_root.resolve())]

    loaded = {"path": None}
    fake_opus = types.ModuleType("discord.opus")

    def _is_loaded():
        return loaded["path"] is not None

    def _load_opus(path: str):
        normalized = str(path)
        if normalized not in bundled_paths:
            raise OSError(f"unexpected path: {normalized}")
        loaded["path"] = normalized

    fake_opus.is_loaded = _is_loaded
    fake_opus.load_opus = _load_opus
    fake_discord.opus = fake_opus

    monkeypatch.setitem(sys.modules, "discord", fake_discord)
    monkeypatch.setitem(sys.modules, "discord.opus", fake_opus)
    monkeypatch.setattr("voice.bootstrap.config.VOICE_OPUS_LIBRARY_PATH", "", raising=False)
    monkeypatch.setattr("voice.bootstrap.ctypes.util.find_library", lambda _: None)

    ok, message = ensure_opus_loaded()

    assert ok is True
    assert loaded["path"] in bundled_paths
    assert loaded["path"] in message


def test_describe_voice_connection_state_handles_legacy_connection_without_dave_fields():
    class _LegacyConnection:
        pass

    class _VoiceClient:
        _connection = _LegacyConnection()

        @staticmethod
        def is_connected():
            return True

    details = describe_voice_connection_state(_VoiceClient())

    assert details["connected"] is True
    assert details["connection_present"] is True
    assert details["max_dave_protocol_version"] is None
    assert details["dave_protocol_version"] is None
    assert details["dave_session_present"] is False
    assert details["dave_session_ready"] is False
    assert details["can_encrypt"] is None


def test_patch_packet_decoder_errors_recovers_corrupt_packets_with_concealment():
    class _FakeOpusError(Exception):
        pass

    class _FakeDecoder:
        CHANNELS = 2
        SAMPLES_PER_FRAME = 960

        def decode(self, data, fec=False):
            if data is None and fec is False:
                return b"p" * (self.SAMPLES_PER_FRAME * self.CHANNELS * 2)
            raise AssertionError("unexpected concealment path")

    class _FakePacketDecoder:
        ssrc = 42

        def __init__(self):
            self._decoder = _FakeDecoder()
            self._buffer = None

        def _decode_packet(self, packet):
            raise _FakeOpusError("corrupt")

    ok, message = _patch_packet_decoder_errors(_FakePacketDecoder, _FakeOpusError)

    assert ok is True
    assert "decode guard" in message

    decoder = _FakePacketDecoder()
    packet = type("Packet", (), {"sequence": 7})()
    returned_packet, pcm = decoder._decode_packet(packet)

    assert returned_packet is packet
    assert pcm == b"p" * (_FakeDecoder.SAMPLES_PER_FRAME * _FakeDecoder.CHANNELS * 2)


def test_patch_packet_decoder_errors_falls_back_to_silence_frame():
    class _FakeOpusError(Exception):
        pass

    class _BrokenDecoder:
        CHANNELS = 2
        SAMPLES_PER_FRAME = 960

        def decode(self, data, fec=False):
            raise RuntimeError("decoder unavailable")

    class _FakePacketDecoder:
        ssrc = 42

        def __init__(self):
            self._decoder = _BrokenDecoder()
            self._buffer = None

        def _decode_packet(self, packet):
            raise _FakeOpusError("corrupt")

    ok, _ = _patch_packet_decoder_errors(_FakePacketDecoder, _FakeOpusError)
    assert ok is True

    decoder = _FakePacketDecoder()
    packet = type("Packet", (), {"sequence": 7})()
    returned_packet, pcm = decoder._decode_packet(packet)

    assert returned_packet is packet
    assert len(pcm) == _BrokenDecoder.SAMPLES_PER_FRAME * _BrokenDecoder.CHANNELS * 2
    assert pcm == b"\x00" * len(pcm)


def test_patch_packet_decoder_errors_decrypts_dave_audio_before_decode(monkeypatch):
    class _FakeOpusError(Exception):
        pass

    class _FakeDSession:
        def __init__(self):
            self.ready = True
            self.calls = []

        def decrypt(self, user_id, media_type, packet):
            self.calls.append((user_id, media_type, packet))
            return b"opus-plain"

    class _FakeDavey:
        class MediaType:
            audio = "audio"

    class _FakeVoiceClient:
        def __init__(self, session):
            self._connection = type("Conn", (), {"dave_session": session})()

        @staticmethod
        def _get_id_from_ssrc(ssrc):
            return 123

    class _FakeSink:
        def __init__(self, voice_client):
            self.voice_client = voice_client

    class _Packet:
        sequence = 7

        def __init__(self):
            self.decrypted_data = b"ciphertext"

        def __bool__(self):
            return True

    class _FakePacketDecoder:
        ssrc = 42

        def __init__(self):
            self._cached_id = None
            self.sink = _FakeSink(_FakeVoiceClient(_FakeDSession()))

        def _decode_packet(self, packet):
            return packet, packet.decrypted_data

    monkeypatch.setattr("voice.bootstrap._get_davey_module", lambda: _FakeDavey)

    ok, _ = _patch_packet_decoder_errors(_FakePacketDecoder, _FakeOpusError)
    assert ok is True

    decoder = _FakePacketDecoder()
    packet = _Packet()
    returned_packet, pcm = decoder._decode_packet(packet)

    assert returned_packet is packet
    assert pcm == b"opus-plain"
    session = decoder.sink.voice_client._connection.dave_session
    assert session.calls == [(123, "audio", b"ciphertext")]


def test_patch_packet_decoder_errors_conceals_when_dave_decrypt_fails(monkeypatch):
    class _FakeOpusError(Exception):
        pass

    class _FakeDecoder:
        CHANNELS = 2
        SAMPLES_PER_FRAME = 960

        def decode(self, data, fec=False):
            if data is None and fec is False:
                return b"c" * (self.SAMPLES_PER_FRAME * self.CHANNELS * 2)
            raise AssertionError("unexpected decode path")

    class _FailingDSession:
        ready = True

        @staticmethod
        def decrypt(user_id, media_type, packet):
            raise RuntimeError("boom")

    class _FakeDavey:
        class MediaType:
            audio = "audio"

    class _FakeVoiceClient:
        _connection = type("Conn", (), {"dave_session": _FailingDSession()})()

        @staticmethod
        def _get_id_from_ssrc(ssrc):
            return 321

    class _FakeSink:
        voice_client = _FakeVoiceClient()

    class _Packet:
        sequence = 9

        def __init__(self):
            self.decrypted_data = b"ciphertext"

        def __bool__(self):
            return True

    class _FakePacketDecoder:
        ssrc = 77

        def __init__(self):
            self._decoder = _FakeDecoder()
            self._buffer = None
            self._cached_id = None
            self.sink = _FakeSink()

        def _decode_packet(self, packet):
            raise AssertionError("ciphertext should not reach original decoder")

    monkeypatch.setattr("voice.bootstrap._get_davey_module", lambda: _FakeDavey)

    ok, _ = _patch_packet_decoder_errors(_FakePacketDecoder, _FakeOpusError)
    assert ok is True

    decoder = _FakePacketDecoder()
    packet = _Packet()
    returned_packet, pcm = decoder._decode_packet(packet)

    assert returned_packet is packet
    assert pcm == b"c" * (_FakeDecoder.SAMPLES_PER_FRAME * _FakeDecoder.CHANNELS * 2)


def test_patch_packet_decoder_errors_skips_dave_decrypt_for_fake_packets(monkeypatch):
    class _FakeOpusError(Exception):
        pass

    class _ExplodingDSession:
        ready = True

        @staticmethod
        def decrypt(user_id, media_type, packet):
            raise AssertionError("fake packets should bypass DAVE decrypt")

    class _FakeDavey:
        class MediaType:
            audio = "audio"

    class _FakeVoiceClient:
        _connection = type("Conn", (), {"dave_session": _ExplodingDSession()})()

        @staticmethod
        def _get_id_from_ssrc(ssrc):
            return 123

    class _FakeSink:
        voice_client = _FakeVoiceClient()

    class _FakePacket:
        sequence = 11
        decrypted_data = b""

        def __bool__(self):
            return False

    class _FakePacketDecoder:
        ssrc = 42

        def __init__(self):
            self.sink = _FakeSink()

        def _decode_packet(self, packet):
            return packet, b"plc"

    monkeypatch.setattr("voice.bootstrap._get_davey_module", lambda: _FakeDavey)

    ok, _ = _patch_packet_decoder_errors(_FakePacketDecoder, _FakeOpusError)
    assert ok is True

    decoder = _FakePacketDecoder()
    packet = _FakePacket()
    returned_packet, pcm = decoder._decode_packet(packet)

    assert returned_packet is packet
    assert pcm == b"plc"


def test_patch_packet_decoder_errors_allows_unencrypted_dave_passthrough(monkeypatch):
    class _FakeOpusError(Exception):
        pass

    class _PassthroughDSession:
        ready = True

        @staticmethod
        def decrypt(user_id, media_type, packet):
            raise ValueError("Failed to decrypt: DecryptionFailed(UnencryptedWhenPassthroughDisabled)")

    class _FakeDavey:
        class MediaType:
            audio = "audio"

    class _FakeVoiceClient:
        _connection = type("Conn", (), {"dave_session": _PassthroughDSession()})()

        @staticmethod
        def _get_id_from_ssrc(ssrc):
            return 555

    class _FakeSink:
        voice_client = _FakeVoiceClient()

    class _Packet:
        sequence = 12

        def __init__(self):
            self.decrypted_data = b"plain-opus"

        def __bool__(self):
            return True

    class _FakePacketDecoder:
        ssrc = 81

        def __init__(self):
            self._cached_id = None
            self.sink = _FakeSink()

        def _decode_packet(self, packet):
            return packet, packet.decrypted_data

    monkeypatch.setattr("voice.bootstrap._get_davey_module", lambda: _FakeDavey)

    ok, _ = _patch_packet_decoder_errors(_FakePacketDecoder, _FakeOpusError)
    assert ok is True

    decoder = _FakePacketDecoder()
    packet = _Packet()
    returned_packet, pcm = decoder._decode_packet(packet)

    assert returned_packet is packet
    assert pcm == b"plain-opus"
