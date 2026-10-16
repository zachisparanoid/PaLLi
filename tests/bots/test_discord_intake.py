"""Wave 3: tests for bots.discord.intake — discord.Message -> spine Turn."""
from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock

import pytest


def _make_fake_message(
    *,
    content: str,
    is_dm: bool = True,
    is_mention: bool = False,
    author_id: int = 12345,
    channel_id: int = 67890,
    attachments: list | None = None,
    bot_user_id: int = 99999,
    is_bot_author: bool = False,
):
    msg = MagicMock()
    msg.content = content
    msg.author = MagicMock()
    msg.author.id = author_id
    msg.author.bot = is_bot_author
    msg.author.name = "test_user"
    msg.attachments = attachments or []
    msg.channel = MagicMock()
    msg.channel.id = channel_id
    msg.created_at = datetime.utcnow()
    if is_dm:
        from discord import DMChannel
        msg.channel.__class__ = DMChannel
    msg.mentions = [MagicMock(id=bot_user_id)] if is_mention else []
    return msg


def test_build_turn_from_dm_text_message() -> None:
    from bots.discord.intake import build_turn_from_message

    msg = _make_fake_message(content="hey palli", is_dm=True)
    turn = build_turn_from_message(msg, bot_user_id=99999, owner_id=1)
    assert turn.surface == "discord_text"
    assert turn.owner_id == 1
    assert turn.content == "hey palli"
    assert turn.metadata.is_dm is True
    assert turn.metadata.is_mention is False
    assert turn.metadata.channel_id == 67890


def test_build_turn_detects_mention() -> None:
    from bots.discord.intake import build_turn_from_message

    msg = _make_fake_message(content="hey @palli look at this", is_dm=False, is_mention=True)
    turn = build_turn_from_message(msg, bot_user_id=99999, owner_id=1)
    assert turn.metadata.is_mention is True


def test_build_turn_strips_self_mention_token() -> None:
    """Discord mention tokens (<@1234> or <@!1234>) should be stripped from content."""
    from bots.discord.intake import build_turn_from_message

    msg = _make_fake_message(content="<@99999> hello there", is_dm=False, is_mention=True)
    turn = build_turn_from_message(msg, bot_user_id=99999, owner_id=1)
    assert "<@99999>" not in turn.content
    assert turn.content.strip() == "hello there"


def test_build_turn_strips_self_mention_with_nick_form() -> None:
    """The <@!ID> nickname form should also be stripped."""
    from bots.discord.intake import build_turn_from_message

    msg = _make_fake_message(content="<@!99999> hi", is_dm=False, is_mention=True)
    turn = build_turn_from_message(msg, bot_user_id=99999, owner_id=1)
    assert "<@!99999>" not in turn.content
    assert turn.content.strip() == "hi"


def test_build_turn_captures_image_attachments() -> None:
    from bots.discord.intake import build_turn_from_message

    image_att = MagicMock()
    image_att.url = "https://cdn.discordapp.com/x.png"
    image_att.content_type = "image/png"
    image_att.filename = "x.png"
    msg = _make_fake_message(content="look", attachments=[image_att])
    turn = build_turn_from_message(msg, bot_user_id=99999, owner_id=1)
    assert len(turn.images) == 1
    assert turn.images[0].url == "https://cdn.discordapp.com/x.png"
    assert turn.images[0].mime == "image/png"


def test_build_turn_marks_voice_when_voice_attachment_present() -> None:
    from bots.discord.intake import build_turn_from_message

    voice_att = MagicMock()
    voice_att.url = "https://cdn.discordapp.com/voice.ogg"
    voice_att.content_type = "audio/ogg"
    voice_att.filename = "voice.ogg"
    msg = _make_fake_message(content="", attachments=[voice_att])
    turn = build_turn_from_message(msg, bot_user_id=99999, owner_id=1)
    assert turn.metadata.is_voice is True


def test_build_turn_passes_through_username() -> None:
    from bots.discord.intake import build_turn_from_message

    msg = _make_fake_message(content="hi", is_dm=True)
    msg.author.name = "sam"
    turn = build_turn_from_message(msg, bot_user_id=99999, owner_id=1)
    assert turn.metadata.discord_username == "sam"


def test_build_turn_origin_is_user() -> None:
    """Discord intake always produces a user-origin turn (proactive/reminder
    fires synthesize their own turns elsewhere)."""
    from bots.discord.intake import build_turn_from_message

    msg = _make_fake_message(content="hi", is_dm=True)
    turn = build_turn_from_message(msg, bot_user_id=99999, owner_id=1)
    assert turn.metadata.origin == "user"


# DEFECT_RECORD §19 (2026-09-02): the vanishing spreadsheet. Intake had three
# buckets — image, voice, text-by-suffix — and everything else fell through:
# an .xlsx or a .pdf arrived, matched nothing, the content stayed empty,
# normalize_for_llm dropped the empty row, and she never knew a file came.
# Every attachment now lands somewhere, and every arrival is logged.

def _file_att(filename: str, mime: str, size: int = 12345):
    att = MagicMock()
    att.url = f"https://cdn.discordapp.com/{filename}"
    att.content_type = mime
    att.filename = filename
    att.size = size
    return att


def test_build_turn_captures_unknown_file_types_as_file_refs() -> None:
    from bots.discord.intake import build_turn_from_message

    xlsx = _file_att("budget.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", 12345)
    pdf = _file_att("scan.pdf", "application/pdf", 777)
    msg = _make_fake_message(content="", attachments=[xlsx, pdf])
    turn = build_turn_from_message(msg, bot_user_id=99999, owner_id=1)
    assert turn.content == "[file attached]"                      # never an empty row again
    assert turn.metadata.text_attachments == ()
    assert turn.images == []
    names = [(r.filename, r.mime, r.size_bytes) for r in turn.metadata.file_attachments]
    assert names == [
        ("budget.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", 12345),
        ("scan.pdf", "application/pdf", 777),
    ]
    assert turn.metadata.file_attachments[0].url.endswith("budget.xlsx")


def test_a_text_file_still_lands_in_the_text_bucket_not_the_file_bucket() -> None:
    from bots.discord.intake import build_turn_from_message

    msg = _make_fake_message(content="", attachments=[_file_att("notes.md", "text/markdown", 10)])
    turn = build_turn_from_message(msg, bot_user_id=99999, owner_id=1)
    assert [r.filename for r in turn.metadata.text_attachments] == ["notes.md"]
    assert turn.metadata.file_attachments == ()


def test_every_attachment_arrival_is_logged_with_its_bucket(caplog) -> None:
    import logging
    from bots.discord.intake import build_turn_from_message

    image = _file_att("x.png", "image/png", 5)
    xlsx = _file_att("budget.xlsx", "application/octet-stream", 12345)
    msg = _make_fake_message(content="here", attachments=[image, xlsx])
    with caplog.at_level(logging.INFO, logger="bots.discord.intake"):
        build_turn_from_message(msg, bot_user_id=99999, owner_id=1)
    lines = [r.getMessage() for r in caplog.records if "attachment" in r.getMessage()]
    assert any("x.png" in l and "image" in l for l in lines)
    assert any("budget.xlsx" in l and "file" in l and "12345" in l for l in lines)
