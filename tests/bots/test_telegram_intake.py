"""A Telegram update becomes a Turn (telegram door, task 2).

Pure conversion, like bots/discord/intake.py: no network, no library. Every
object here is a fake with the attribute names python-telegram-bot's own
objects carry, so the suite runs where the library is not installed.

The three questions this file holds:

  * WHO is speaking — the owner gate's verdict decides the whole shape of
    the Turn (his lane, or the guest one with their own id on it).
  * WHETHER she was spoken to — a group message that is neither a mention
    nor a reply to her is not hers to answer. Privacy mode is the first
    lock on that door; this is the second.
  * WHAT came with it — text, files, photos, voice notes, each into the
    bucket recall already knows how to read, and never with a URL that
    carries her bot token.
"""
from __future__ import annotations

import pytest

OWNER_TELEGRAM_ID = 777
APP_OWNER_ID = 1
STRANGER_ID = 4242
BOT_ID = 999
BOT_USERNAME = "palli_test_bot"
DM_CHAT = 424242
GROUP_CHAT = -1001234567890


# ── fakes (never `import telegram`) ───────────────────────────────────────
class FakeUser:
    def __init__(self, id, *, username=None, full_name=None, is_bot=False):
        self.id = id
        self.username = username
        self.full_name = full_name
        self.is_bot = is_bot


class FakeChat:
    def __init__(self, id, type, *, title=None):
        self.id = id
        self.type = type
        self.title = title


class FakeEntity:
    def __init__(self, type, offset, length):
        self.type = type
        self.offset = offset
        self.length = length


class FakeDocument:
    def __init__(self, *, file_id="doc-1", file_unique_id="u-doc-1",
                 file_name="notes.py", mime_type="text/x-python", file_size=120):
        self.file_id = file_id
        self.file_unique_id = file_unique_id
        self.file_name = file_name
        self.mime_type = mime_type
        self.file_size = file_size


class FakePhotoSize:
    def __init__(self, *, file_id, file_unique_id, width, height, file_size):
        self.file_id = file_id
        self.file_unique_id = file_unique_id
        self.width = width
        self.height = height
        self.file_size = file_size


class FakeVoice:
    def __init__(self, *, file_id="voice-1", file_unique_id="u-voice-1",
                 mime_type="audio/ogg", file_size=4096, duration=3):
        self.file_id = file_id
        self.file_unique_id = file_unique_id
        self.mime_type = mime_type
        self.file_size = file_size
        self.duration = duration


class FakeMessage:
    def __init__(self, *, chat, from_user, text=None, caption=None,
                 entities=(), caption_entities=(), reply_to_message=None,
                 document=None, photo=(), voice=None, audio=None,
                 message_id=17):
        self.chat = chat
        self.from_user = from_user
        self.text = text
        self.caption = caption
        self.entities = list(entities)
        self.caption_entities = list(caption_entities)
        self.reply_to_message = reply_to_message
        self.document = document
        self.photo = list(photo)
        self.voice = voice
        self.audio = audio
        self.message_id = message_id


class RealShapedMessage(FakeMessage):
    """A message that answers the way telegram.Message really does.

    The plain FakeMessage above has no parse_entities, so every test using it
    takes intake's FALLBACK scan — which reads caption_entities and would
    have hidden review round 1's important 1 forever. This one implements
    both accessors with the real split: parse_entities reads text/entities
    ONLY (telegram/_message.py:4613), parse_caption_entities reads
    caption/caption_entities (:4640). On a photo with a caption the first
    therefore returns {} — which is exactly the bug.
    """

    def _parse(self, text, entities, types):
        wanted = set(types or ())
        out = {}
        for entity in entities or ():
            if wanted and entity.type not in wanted:
                continue
            offset, length = entity.offset, entity.length
            out[entity] = str(text or "")[offset:offset + length]
        return out

    def parse_entities(self, types=None):
        return self._parse(self.text, self.entities, types)

    def parse_caption_entities(self, types=None):
        return self._parse(self.caption, self.caption_entities, types)


class FakeUpdate:
    def __init__(self, *, message=None, edited_message=None, channel_post=None):
        self.message = message
        self.edited_message = edited_message
        self.channel_post = channel_post
        self.effective_message = message or edited_message or channel_post


def _owner_ref(telegram_user_id=OWNER_TELEGRAM_ID):
    return {"app_user_id": APP_OWNER_ID, "telegram_user_id": telegram_user_id}


def _dm(text="hey", *, author_id=OWNER_TELEGRAM_ID, **kw):
    return FakeUpdate(message=FakeMessage(
        chat=FakeChat(DM_CHAT, "private"),
        from_user=FakeUser(author_id, username="dana_example", full_name="Dana"),
        text=text, **kw))


def _group(text="hey", *, author_id=OWNER_TELEGRAM_ID, entities=(), **kw):
    return FakeUpdate(message=FakeMessage(
        chat=FakeChat(GROUP_CHAT, "supergroup", title="the shop"),
        from_user=FakeUser(author_id, username="dana_example", full_name="Dana"),
        text=text, entities=entities, **kw))


def _build(update, *, owner_ref=None):
    from bots.telegram import intake

    return intake.build_turn(
        update,
        bot_username=BOT_USERNAME,
        bot_user_id=BOT_ID,
        owner_ref=owner_ref if owner_ref is not None else _owner_ref(),
    )


# ── the owner's own lane ──────────────────────────────────────────────────
def test_a_dm_from_him_builds_his_turn():
    result = _build(_dm("what's the plan"))

    assert result is not None and result.is_owner is True
    turn = result.turn
    assert turn.surface == "telegram_text"
    assert turn.owner_id == APP_OWNER_ID
    assert turn.content == "what's the plan"
    assert turn.metadata.channel_id == DM_CHAT
    assert turn.metadata.is_dm is True
    assert turn.metadata.origin == "user"
    assert turn.metadata.extra["telegram_username"] == "dana_example"
    assert turn.metadata.extra["telegram_chat_type"] == "private"


def test_a_negative_group_id_keeps_its_sign():
    """Telegram supergroup ids are negative. An abs() anywhere in this path
    would file the chat's history under a room that does not exist."""
    result = _build(_group("@palli_test_bot hi", entities=[FakeEntity("mention", 0, 15)]))

    assert result is not None
    assert result.turn.metadata.channel_id == GROUP_CHAT
    assert result.turn.metadata.channel_id < 0


# ── the second lock on the group door ─────────────────────────────────────
def test_a_group_message_that_is_not_for_her_is_not_hers_to_answer():
    assert _build(_group("anyone got a 10mm socket")) is None


def test_an_at_mention_in_a_group_builds_a_turn():
    result = _build(_group(
        "@palli_test_bot what's the torque spec",
        entities=[FakeEntity("mention", 0, len("@palli_test_bot"))]))

    assert result is not None
    assert result.turn.metadata.is_mention is True
    # Her own handle is stripped the way Discord strips its mention token.
    assert result.turn.content == "what's the torque spec"


def test_a_mention_of_someone_else_is_still_not_for_her():
    result = _build(_group(
        "@someone_else what's the torque spec",
        entities=[FakeEntity("mention", 0, len("@someone_else"))]))

    assert result is None


def test_a_reply_to_her_in_a_group_builds_a_turn():
    her_message = FakeMessage(
        chat=FakeChat(GROUP_CHAT, "supergroup"),
        from_user=FakeUser(BOT_ID, username=BOT_USERNAME, is_bot=True),
        text="12 newton metres")
    result = _build(_group("are you sure", reply_to_message=her_message))

    assert result is not None
    assert result.turn.metadata.is_reply_to_bot is True
    assert result.turn.metadata.is_mention is True


def test_a_reply_to_someone_else_in_a_group_is_not_for_her():
    theirs = FakeMessage(
        chat=FakeChat(GROUP_CHAT, "supergroup"),
        from_user=FakeUser(STRANGER_ID), text="mine")
    assert _build(_group("nice", reply_to_message=theirs)) is None


# ── the owner gate ────────────────────────────────────────────────────────
def test_a_stranger_takes_the_guest_lane():
    from core.owner_gate import GUEST_ORIGIN

    result = _build(_dm("hello?", author_id=STRANGER_ID))

    assert result is not None and result.is_owner is False
    turn = result.turn
    assert turn.owner_id == STRANGER_ID          # never his id
    assert turn.metadata.origin == GUEST_ORIGIN
    assert turn.content.startswith("[Dana]:")       # the shown name (Fable's checkpoint, 8)


def test_an_unresolved_owner_ref_makes_everyone_a_guest():
    """Week 2 task 3's law, on the second door: a person nobody can PROVE is
    him is a guest. The owner path is proved into, never defaulted into."""
    result = _build(_dm("it's me"), owner_ref={"app_user_id": APP_OWNER_ID,
                                               "telegram_user_id": None})

    assert result is not None and result.is_owner is False
    assert result.turn.owner_id == OWNER_TELEGRAM_ID     # their raw id, not his app id
    assert result.turn.metadata.origin == "guest"


# ── what came with it ─────────────────────────────────────────────────────
def test_a_photo_becomes_an_image_ref_with_no_url_anywhere():
    """The file URL is https://api.telegram.org/file/bot<TOKEN>/… — it must
    never be built here, never be put on a ref, never be persisted."""
    update = _dm(None, caption="what is this")
    update.message.photo = [
        FakePhotoSize(file_id="small", file_unique_id="u-small", width=90,
                      height=90, file_size=900),
        FakePhotoSize(file_id="big", file_unique_id="u-big", width=1280,
                      height=1280, file_size=90000),
    ]
    result = _build(update)

    assert result is not None
    turn = result.turn
    assert len(turn.images) == 1
    assert turn.images[0].url is None
    assert turn.content == "what is this"
    files = turn.metadata.extra["telegram_files"]
    assert [f["bucket"] for f in files] == ["image"]
    assert files[0]["file_id"] == "big"           # the largest size
    assert files[0]["file_unique_id"] == "u-big"
    assert "api.telegram.org" not in repr(turn)
    assert BOT_USERNAME not in str(files)


def test_a_code_file_lands_in_the_text_bucket():
    update = _dm(None)
    update.message.document = FakeDocument(file_name="binding.py",
                                           mime_type="text/x-python")
    result = _build(update)

    turn = result.turn
    assert [r.filename for r in turn.metadata.text_attachments] == ["binding.py"]
    assert turn.metadata.file_attachments == ()
    assert turn.metadata.text_attachments[0].url is None
    assert turn.content == "[file attached]"


def test_a_spreadsheet_lands_in_the_file_bucket():
    """DEFECT §19's bucket: anything that is not an image, a voice note or a
    text file still arrives — she is never left not knowing a file came."""
    update = _dm(None)
    update.message.document = FakeDocument(
        file_name="quotes.xlsx",
        mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    result = _build(update)

    turn = result.turn
    assert [r.filename for r in turn.metadata.file_attachments] == ["quotes.xlsx"]
    assert turn.metadata.text_attachments == ()
    assert turn.content == "[file attached]"


def test_a_voice_note_is_marked_and_never_empty():
    """The 2026-06-11 outage rule: an empty voice turn poisoned the session
    and 400'd the whole channel. Task 4 replaces the placeholder with the
    transcript; this task only marks it."""
    from bots.common.voice_note import VOICE_PLACEHOLDER_CONTENT

    update = _dm(None)
    update.message.voice = FakeVoice()
    result = _build(update)

    turn = result.turn
    assert turn.metadata.is_voice is True
    assert turn.content == VOICE_PLACEHOLDER_CONTENT
    assert [f["bucket"] for f in turn.metadata.extra["telegram_files"]] == ["voice"]


def test_an_image_with_no_caption_says_so():
    update = _dm(None)
    update.message.photo = [FakePhotoSize(file_id="p", file_unique_id="u-p",
                                          width=10, height=10, file_size=10)]
    assert _build(update).turn.content == "[image attached]"


# ── what is not a turn at all ─────────────────────────────────────────────
@pytest.mark.parametrize("update", [
    FakeUpdate(),
    FakeUpdate(edited_message=FakeMessage(
        chat=FakeChat(DM_CHAT, "private"),
        from_user=FakeUser(OWNER_TELEGRAM_ID), text="fixed a typo")),
    FakeUpdate(channel_post=FakeMessage(
        chat=FakeChat(-100999, "channel"),
        from_user=None, text="broadcast")),
])
def test_updates_she_has_no_business_answering_return_none(update):
    assert _build(update) is None


def test_a_message_from_another_bot_is_not_a_turn():
    update = _dm("beep", author_id=BOT_ID + 1)
    update.message.from_user.is_bot = True
    assert _build(update) is None


# ── the real accessors, not the fallback (review round 1, important 1) ────
def _real_group(**kw):
    """A group message that answers like telegram.Message does."""
    return FakeUpdate(message=RealShapedMessage(
        chat=FakeChat(GROUP_CHAT, "supergroup", title="the shop"),
        from_user=FakeUser(OWNER_TELEGRAM_ID, username="dana_example", full_name="Dana"),
        **kw))


def test_a_group_text_mention_is_seen_through_the_real_accessor():
    """The half that always worked, pinned through the REAL branch."""
    result = _build(_real_group(
        text="@palli_test_bot what's the torque spec",
        entities=[FakeEntity("mention", 0, len(BOT_USERNAME) + 1)]))

    assert result is not None
    assert result.turn.metadata.is_mention is True
    assert result.turn.content == "what's the torque spec"


def test_a_group_photo_caption_mention_reaches_her():
    """The bug: parse_entities reads message.TEXT, and a photo's words are
    its CAPTION — so she was never mentioned, and "@palli look at this"
    with a photo in a group vanished without a line or a log."""
    update = _real_group(
        caption="@palli_test_bot look at this",
        caption_entities=[FakeEntity("mention", 0, len(BOT_USERNAME) + 1)])
    update.message.photo = [FakePhotoSize(file_id="big", file_unique_id="u-big",
                                          width=1280, height=1280, file_size=90000)]

    result = _build(update)

    assert result is not None, "a caption mention in a group must reach her"
    assert result.turn.metadata.is_mention is True
    assert result.turn.content == "look at this"
    assert len(result.turn.images) == 1


def test_a_group_document_caption_mention_reaches_her():
    update = _real_group(
        caption="@palli_test_bot the traceback",
        caption_entities=[FakeEntity("mention", 0, len(BOT_USERNAME) + 1)])
    update.message.document = FakeDocument(file_name="crash.log",
                                           mime_type="text/plain")

    result = _build(update)

    assert result is not None
    assert result.turn.metadata.is_mention is True
    assert [r.filename for r in result.turn.metadata.text_attachments] == ["crash.log"]


def test_a_group_caption_that_mentions_someone_else_is_still_not_for_her():
    """The other half of the same branch: the union must not make everything
    a mention."""
    update = _real_group(
        caption="@someone_else look at this",
        caption_entities=[FakeEntity("mention", 0, len("@someone_else"))])
    update.message.photo = [FakePhotoSize(file_id="p", file_unique_id="u-p",
                                          width=10, height=10, file_size=10)]

    assert _build(update) is None


# ── her handle comes off on a BOUNDARY (review round 1, minor 5) ──────────
def test_another_bots_handle_is_not_hers_to_strip():
    """"@palli_test_bot2 hi" is somebody else; stripping her own handle off
    the front of it left "2 hi"."""
    result = _build(_dm("@palli_test_bot2 hi"))

    assert result.turn.content == "@palli_test_bot2 hi"


def test_her_own_handle_still_comes_off():
    assert _build(_dm("@palli_test_bot hi")).turn.content == "hi"
    assert _build(_dm("@PALLI_TEST_BOT hi")).turn.content == "hi"


# ── every kind gets a line (review round 1, important 2 / minor 10) ───────
@pytest.mark.parametrize("attribute,fragment", [
    ("video", "a video arrived"),
    ("video_note", "a video note arrived"),
    ("animation", "an animated GIF arrived"),
    ("sticker", "a sticker arrived"),
    ("location", "a location arrived"),
    ("contact", "a contact card arrived"),
    ("poll", "a poll arrived"),
])
def test_a_kind_she_cannot_read_still_says_what_arrived(attribute, fragment):
    """§19's law at the door: a thing he sent must never simply not happen.
    She cannot read any of these yet — so she says so."""
    update = _dm(None)
    setattr(update.message, attribute, object())

    result = _build(update)

    assert result is not None
    assert fragment in result.turn.content
    assert "can't" in result.turn.content


def test_his_caption_comes_before_the_line():
    update = _dm(None)
    update.message.caption = "watch this bit"
    update.message.video = object()

    content = _build(update).turn.content

    assert content.splitlines()[0] == "watch this bit"
    assert "a video arrived" in content.splitlines()[1]


def test_a_message_of_no_known_kind_is_never_empty():
    """Minor 10: an empty user row poisoned a whole session once. The last
    branch makes empty content unreachable by construction."""
    result = _build(_dm(None))

    assert result is not None
    assert result.turn.content.strip() == "[something arrived that I can't read yet]"


def test_a_group_video_without_a_mention_is_still_not_hers():
    update = _group(None)
    update.message.video = object()

    assert _build(update) is None


# ── the caps agree (review round 1, minor 4) ──────────────────────────────
def test_the_file_cap_is_clamped_to_the_inbox_cap():
    """Unclamped, a 30 MB file passed intake, landed on disk, and was then
    refused by the inbox — she would have told him it never arrived while it
    sat in data/inbox/telegram/."""
    from bots.telegram import intake
    from core.turn.inbox import INBOX_MAX_BYTES

    assert intake.TELEGRAM_MAX_FILE_BYTES <= INBOX_MAX_BYTES
