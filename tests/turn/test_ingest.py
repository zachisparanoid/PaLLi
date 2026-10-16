"""Wave 2: assert ingest normalizes every surface into the same Turn shape."""
from __future__ import annotations

from datetime import datetime

import pytest

from core.turn.ingest import normalize_turn
from core.turn.types import ImageRef, Turn


def test_normalize_discord_text_dm() -> None:
    raw = {
        "surface": "discord_text",
        "owner_id": 1,
        "content": "hey",
        "metadata": {
            "channel_id": 12345,
            "is_dm": True,
            "is_mention": False,
            "discord_username": "sam",
        },
    }
    t = normalize_turn(raw)
    assert isinstance(t, Turn)
    assert t.surface == "discord_text"
    assert t.owner_id == 1
    assert t.content == "hey"
    assert t.metadata.is_dm is True
    assert t.metadata.channel_id == 12345
    assert t.metadata.discord_username == "sam"


def test_normalize_voice_local_with_transcript() -> None:
    raw = {
        "surface": "voice_local",
        "owner_id": 1,
        "content": "what's the weather",
        "metadata": {"is_voice": True},
    }
    t = normalize_turn(raw)
    assert t.surface == "voice_local"
    assert t.metadata.is_voice is True


def test_normalize_proactive_synthesized() -> None:
    raw = {
        "surface": "proactive",
        "owner_id": 1,
        "content": "wanted to follow up about your atlas project",
        "metadata": {"origin": "monologue"},
    }
    t = normalize_turn(raw)
    assert t.surface == "proactive"
    assert t.metadata.origin == "monologue"


def test_normalize_reminder_fire_with_reminder_id() -> None:
    raw = {
        "surface": "reminder_fire",
        "owner_id": 1,
        "content": "[REMINDER FIRE] call dentist",
        "metadata": {"origin": "reminder", "reminder_id": 42},
    }
    t = normalize_turn(raw)
    assert t.surface == "reminder_fire"
    assert t.metadata.origin == "reminder"
    assert t.metadata.reminder_id == 42


def test_normalize_rejects_unknown_surface() -> None:
    with pytest.raises(ValueError, match="unknown surface"):
        normalize_turn({"surface": "telepathy", "owner_id": 1, "content": "x"})


def test_normalize_rejects_missing_surface() -> None:
    with pytest.raises(ValueError, match="unknown surface"):
        normalize_turn({"owner_id": 1, "content": "x"})


def test_normalize_rejects_bad_owner_id() -> None:
    with pytest.raises(ValueError, match="owner_id must be positive"):
        normalize_turn({"surface": "discord_text", "owner_id": 0, "content": "x"})
    with pytest.raises(ValueError, match="owner_id must be positive"):
        normalize_turn({"surface": "discord_text", "owner_id": -1, "content": "x"})
    with pytest.raises(ValueError, match="owner_id must be positive"):
        normalize_turn({"surface": "discord_text", "owner_id": "not-a-number", "content": "x"})


def test_normalize_strips_palli_self_mention() -> None:
    """Defensive: if Discord intake forgets to strip the bot's own mention,
    ingest should still hand the model a clean string."""
    raw = {
        "surface": "discord_text",
        "owner_id": 1,
        "content": "<@123456789> hey there",
        "metadata": {"is_mention": True, "channel_id": 1},
    }
    t = normalize_turn(raw)
    assert "<@" not in t.content
    assert "hey there" in t.content


def test_normalize_strips_nicknamed_mention() -> None:
    """Discord nicknamed mentions look like <@!12345> — also strip these."""
    raw = {
        "surface": "discord_text",
        "owner_id": 1,
        "content": "<@!987654321> what's up",
        "metadata": {"is_mention": True, "channel_id": 1},
    }
    t = normalize_turn(raw)
    assert "<@" not in t.content
    assert "what's up" in t.content


def test_normalize_handles_image_dicts() -> None:
    raw = {
        "surface": "discord_text",
        "owner_id": 1,
        "content": "look at this",
        "images": [
            {"url": "https://example.com/a.png", "mime": "image/png"},
            {"local_path": "/tmp/b.jpg", "mime": "image/jpeg"},
        ],
        "metadata": {"channel_id": 1, "is_dm": True},
    }
    t = normalize_turn(raw)
    assert len(t.images) == 2
    assert t.images[0].url == "https://example.com/a.png"
    assert t.images[1].local_path == "/tmp/b.jpg"


def test_normalize_passes_imageref_through_unchanged() -> None:
    img = ImageRef(url="https://example.com/x.png", local_path=None, mime="image/png")
    raw = {
        "surface": "discord_text",
        "owner_id": 1,
        "content": "hi",
        "images": [img],
        "metadata": {"channel_id": 1, "is_dm": True},
    }
    t = normalize_turn(raw)
    assert t.images[0] is img


def test_normalize_empty_content_acceptable() -> None:
    """Empty content is allowed — voice transcript may produce empty string
    after VAD trim. Downstream stages decide whether to handle it."""
    raw = {
        "surface": "voice_local",
        "owner_id": 1,
        "content": "",
        "metadata": {"is_voice": True},
    }
    t = normalize_turn(raw)
    assert t.content == ""


def test_normalize_isoformat_received_at() -> None:
    raw = {
        "surface": "dashboard",
        "owner_id": 1,
        "content": "hi",
        "received_at": "2026-04-27T18:00:00",
    }
    t = normalize_turn(raw)
    assert t.received_at.year == 2026
    assert t.received_at.month == 4
