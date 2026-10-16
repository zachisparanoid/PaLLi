"""Telegram attachments reach disk without the token ever touching a ref
(telegram door, task 2).

A Telegram file URL is ``https://api.telegram.org/file/bot<TOKEN>/<path>``.
Putting one on a Turn would persist her bot token into the history, the
transcript and every log line that renders a turn — so intake builds refs
with NO url at all, and the CLIENT materializes them afterwards: download
once through the authenticated Bot object, write into the inbox, and point
the ref at the local file.

``core.turn.attachments.fetch_attachment_bytes`` then reads a ``file://``
ref off disk instead of over HTTP, which is what lets recall's vision and
text-attachment readers work unchanged on this door.
"""
from __future__ import annotations

import pytest

from core.turn.types import ImageRef, TextAttachmentRef, Turn, TurnMetadata


class FakeFile:
    def __init__(self, data: bytes, *, boom: bool = False):
        self._data = data
        self._boom = boom

    async def download_as_bytearray(self):
        if self._boom:
            raise RuntimeError("telegram hiccup")
        return bytearray(self._data)


class FakeBot:
    """Records get_file calls; never touches the network."""

    def __init__(self, files: dict):
        self.files = files
        self.asked: list[str] = []

    async def get_file(self, file_id):
        self.asked.append(str(file_id))
        try:
            return self.files[str(file_id)]
        except KeyError:
            raise RuntimeError(f"no such file {file_id}")


def _turn(*, files, text_attachments=(), file_attachments=(), images=(),
          content="here you go", origin="user"):
    return Turn(
        surface="telegram_text",
        owner_id=1,
        content=content,
        images=list(images),
        metadata=TurnMetadata(
            channel_id=424242,
            origin=origin,
            text_attachments=tuple(text_attachments),
            file_attachments=tuple(file_attachments),
            extra={"telegram_files": list(files)},
        ),
    )


@pytest.fixture
def inbox(tmp_path, monkeypatch):
    monkeypatch.setenv("PALLI_INBOX_DIR", str(tmp_path / "inbox"))
    return tmp_path / "inbox"


@pytest.mark.asyncio
async def test_a_text_file_lands_on_disk_and_the_ref_points_at_it(inbox):
    from bots.telegram import intake

    turn = _turn(
        files=[{"bucket": "text", "file_id": "doc-1", "file_unique_id": "u-1",
                "filename": "binding.py", "mime": "text/x-python", "size": 11}],
        text_attachments=[TextAttachmentRef(url=None, filename="binding.py",
                                            mime="text/x-python", size_bytes=11)],
    )
    bot = FakeBot({"doc-1": FakeFile(b"print(1)\n")})

    out = await intake.materialize_attachments(turn, bot)

    assert bot.asked == ["doc-1"]
    ref = out.metadata.text_attachments[0]
    assert ref.url.startswith("file://")
    assert ref.filename == "binding.py"
    landed = list((inbox / "telegram").glob("*"))
    assert [p.name for p in landed] == ["u-1-binding.py"]
    assert landed[0].read_bytes() == b"print(1)\n"
    assert "api.telegram.org" not in repr(out)


@pytest.mark.asyncio
async def test_fetch_attachment_bytes_reads_the_materialized_ref_back(inbox):
    from bots.telegram import intake
    from core.turn.attachments import fetch_attachment_bytes

    turn = _turn(
        files=[{"bucket": "text", "file_id": "doc-1", "file_unique_id": "u-1",
                "filename": "notes.txt", "mime": "text/plain", "size": 5}],
        text_attachments=[TextAttachmentRef(url=None, filename="notes.txt",
                                            mime="text/plain", size_bytes=5)],
    )
    out = await intake.materialize_attachments(
        turn, FakeBot({"doc-1": FakeFile(b"hello")}))

    assert await fetch_attachment_bytes(out.metadata.text_attachments[0].url) == b"hello"


@pytest.mark.asyncio
async def test_an_image_materializes_onto_local_path(inbox):
    from bots.telegram import intake

    turn = _turn(
        files=[{"bucket": "image", "file_id": "photo-1", "file_unique_id": "u-p",
                "filename": "photo.jpg", "mime": "image/jpeg", "size": 4}],
        images=[ImageRef(url=None, local_path=None, mime="image/jpeg",
                         description="photo.jpg")],
        content="what is this",
    )
    out = await intake.materialize_attachments(
        turn, FakeBot({"photo-1": FakeFile(b"\xff\xd8\xff\xd9")}))

    assert len(out.images) == 1
    assert out.images[0].url is None
    assert out.images[0].local_path.startswith("file://")
    assert out.images[0].mime == "image/jpeg"


@pytest.mark.asyncio
async def test_a_file_the_inbox_would_refuse_is_refused_here_instead(inbox):
    """Review round 1, minor 4: 30 MB passed Telegram's 50 MB wall, landed on
    disk, and was then refused by the inbox's 25 MB one — so she told him the
    file never arrived while it sat in data/inbox/telegram/. One cap now, and
    the line she says names it."""
    from bots.telegram import intake
    from core.turn.inbox import INBOX_MAX_BYTES

    assert intake.TELEGRAM_MAX_FILE_BYTES <= INBOX_MAX_BYTES

    turn = _turn(
        files=[{"bucket": "file", "file_id": "mid", "file_unique_id": "u-m",
                "filename": "scan.tif", "mime": "image/tiff",
                "size": 30 * 1024 * 1024}],
        file_attachments=[TextAttachmentRef(url=None, filename="scan.tif",
                                            mime="image/tiff",
                                            size_bytes=30 * 1024 * 1024)],
    )
    bot = FakeBot({})
    out = await intake.materialize_attachments(turn, bot)

    assert bot.asked == []
    assert out.metadata.file_attachments == ()
    assert "scan.tif" in out.content
    cap_mb = int(intake.TELEGRAM_MAX_FILE_BYTES / (1024 * 1024))
    assert f"{cap_mb} MB limit on what I can take in" in out.content, out.content
    assert "Telegram's" not in out.content, out.content


@pytest.mark.asyncio
async def test_a_file_over_the_cap_is_refused_out_loud(inbox):
    from bots.telegram import intake

    turn = _turn(
        files=[{"bucket": "file", "file_id": "huge", "file_unique_id": "u-h",
                "filename": "dump.iso", "mime": "application/octet-stream",
                "size": 60 * 1024 * 1024}],
        file_attachments=[TextAttachmentRef(url=None, filename="dump.iso",
                                            mime="application/octet-stream",
                                            size_bytes=60 * 1024 * 1024)],
    )
    bot = FakeBot({})
    out = await intake.materialize_attachments(turn, bot)

    assert bot.asked == []                       # never even attempted
    assert out.metadata.file_attachments == ()   # no ref pointing at nothing
    assert "dump.iso" in out.content
    # The EFFECTIVE cap, named — the smaller of Telegram's wall and the
    # inbox's (review round 1, minor 4), never a number she cannot honour.
    cap_mb = int(intake.TELEGRAM_MAX_FILE_BYTES / (1024 * 1024))
    assert f"{cap_mb} MB limit on what I can take in" in out.content, out.content
    assert not (inbox / "telegram").exists() or not list((inbox / "telegram").glob("*"))


@pytest.mark.asyncio
async def test_a_failed_download_is_a_line_never_a_silence(inbox):
    """§19's law: a file that did not arrive is something she is TOLD, not
    something that quietly is not there."""
    from bots.telegram import intake

    turn = _turn(
        files=[{"bucket": "file", "file_id": "doc-1", "file_unique_id": "u-1",
                "filename": "quotes.xlsx", "mime": "application/vnd.ms-excel",
                "size": 900}],
        file_attachments=[TextAttachmentRef(url=None, filename="quotes.xlsx",
                                            mime="application/vnd.ms-excel",
                                            size_bytes=900)],
    )
    out = await intake.materialize_attachments(
        turn, FakeBot({"doc-1": FakeFile(b"", boom=True)}))

    assert out.metadata.file_attachments == ()
    assert "[attachment quotes.xlsx could not be fetched]" in out.content


@pytest.mark.asyncio
async def test_a_turn_with_nothing_attached_comes_back_untouched(inbox):
    from bots.telegram import intake

    turn = _turn(files=[], content="just words")
    out = await intake.materialize_attachments(turn, FakeBot({}))

    assert out is turn


# ── a guest's files are not hers to hold (final wave, I4) ─────────────────
@pytest.mark.asyncio
async def test_a_guests_file_is_never_taken_in(inbox):
    """A guest turn is dispatched with NO tool registry at all
    (core/agent/dispatch.py:174-182), so a file in the inbox can never be
    read for them — the download bought
    nothing and cost 25 MB of his disk per stranger, five times per ten
    minutes, with no eviction. §19 still holds: it is SAID, not silently
    not-there."""
    from bots.telegram import intake

    turn = _turn(
        files=[{"bucket": "file", "file_id": "doc-1", "file_unique_id": "u-1",
                "filename": "resume.pdf", "mime": "application/pdf",
                "size": 900}],
        file_attachments=[TextAttachmentRef(url=None, filename="resume.pdf",
                                            mime="application/pdf",
                                            size_bytes=900)],
        origin="guest",
    )
    bot = FakeBot({"doc-1": FakeFile(b"%PDF-1.4")})

    out = await intake.materialize_attachments(turn, bot)

    assert bot.asked == []                          # never even attempted
    assert out.metadata.file_attachments == ()      # no ref pointing at nothing
    assert ("[attachment resume.pdf was not taken in — I don't hold files "
            "for guests]") in out.content
    assert not (inbox / "telegram").exists() or not list(
        (inbox / "telegram").glob("*"))


@pytest.mark.asyncio
async def test_a_guests_photo_and_text_file_still_come_down(inbox):
    """Only the FILE bucket goes. Recall reads images and text attachments
    on a guest turn as it does on his, so refusing those would cost her the
    thing in front of her — the point of the door."""
    from bots.telegram import intake

    turn = _turn(
        files=[
            {"bucket": "image", "file_id": "photo-1", "file_unique_id": "u-p",
             "filename": "photo.jpg", "mime": "image/jpeg", "size": 4},
            {"bucket": "text", "file_id": "doc-1", "file_unique_id": "u-1",
             "filename": "notes.txt", "mime": "text/plain", "size": 5},
        ],
        images=[ImageRef(url=None, local_path=None, mime="image/jpeg",
                         description="photo.jpg")],
        text_attachments=[TextAttachmentRef(url=None, filename="notes.txt",
                                            mime="text/plain", size_bytes=5)],
        origin="guest",
        content="what is this",
    )
    bot = FakeBot({"photo-1": FakeFile(b"\xff\xd8\xff\xd9"),
                   "doc-1": FakeFile(b"hello")})

    out = await intake.materialize_attachments(turn, bot)

    assert sorted(bot.asked) == ["doc-1", "photo-1"]
    assert out.images[0].local_path.startswith("file://")
    assert out.metadata.text_attachments[0].url.startswith("file://")
    assert "was not taken in" not in out.content


@pytest.mark.asyncio
async def test_his_own_file_still_comes_down(inbox):
    """The owner's file bucket is untouched: origin 'user' downloads."""
    from bots.telegram import intake

    turn = _turn(
        files=[{"bucket": "file", "file_id": "doc-1", "file_unique_id": "u-1",
                "filename": "quotes.xlsx", "mime": "application/vnd.ms-excel",
                "size": 900}],
        file_attachments=[TextAttachmentRef(url=None, filename="quotes.xlsx",
                                            mime="application/vnd.ms-excel",
                                            size_bytes=900)],
    )
    bot = FakeBot({"doc-1": FakeFile(b"xlsx")})

    out = await intake.materialize_attachments(turn, bot)

    assert bot.asked == ["doc-1"]
    assert out.metadata.file_attachments[0].url.startswith("file://")
