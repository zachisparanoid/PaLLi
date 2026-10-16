"""Voice replies, remembered (Batch 8, task 10 — B8-5; rulings T10-1..T10-6).

Two things live here, both behind ONE setting, ``voice.voice_notes`` (schema
default off; with it off nothing here is read, and every door, tool list and
payload is today's):

1. **The switch.** One per conversation, keyed by that conversation's session
   key (``core.session_keys.session_key_for`` — ``discord:<channel>``,
   ``telegram:<chat>``). She sets it with her ``voice_replies`` tool when
   asked ("send me voice messages until I say otherwise"); Telegram's
   ``/voice`` flips the same one (``bots/common/steering.py`` hands the
   ``voice_replies`` flag here while the setting is on). It survives a
   restart — the owner's B8-5 overrides the old "it resets when I restart".
   While it is on, her replies in that conversation go as voice
   (``bots/common/voice_note.plan_voice_reply``).

   The file is ``data/voice_replies.json`` (gitignored;
   ``PALLI_VOICE_REPLIES_PATH`` overrides it, which is how the test suite
   stays off the live file): ``{"<conversation key>": true|false}``. It is
   re-read on every call, written atomically (a temporary file, then a
   rename), and a file that cannot be read is no switches — logged, and
   never overwritten.

2. **The status line** (owner O2 — INTERIM: the owner revisits it after the
   batches, beside the room note and the old standing orders). While the
   switch is on for a conversation, her reply call there carries ONE line
   saying so — on the owner's turns, and since 10-12 on a guest's too (the
   owner: guests' messages are "normal messages, minus permissions"; their
   replies are spoken where the switch is on, so she is told). One seam
   (``status_line_for``, read once by ``core/agent/runner.py``), easy to
   replace. With the switch off the payload is byte-identical. The line is
   shown only where it can come true (final fix wave, rulings FW-15 and
   FW-16): never on a host with no voice (her reply goes as text there), and
   the owner's only with ``features.her_hands`` on — it points at
   ``voice_note``. A guest's turn has no tools, so their line
   (``GUEST_STATUS_LINE``) names none and needs no hands.

3. **No tool while the switch is on** (DEFECT §56, 2026-10-02 — the 10-01
   20:03 loop: with the switch on she made a voice note, could not tell how
   to finish, and made 51 more; the door sent all 51). She just writes her
   reply: the door speaks it, and ``split_for_voice`` sends what she'd need
   them to READ (links, code, inline code) as one short text line beside it.
   ``voice_note`` is left off her tool list while the switch is on here
   (second read F3, 2026-10-04), makes nothing if called anyway, makes at
   most one per reply, and the door sends at most
   ``MAX_VOICE_NOTES_PER_REPLY``; the runner's brake ends a run whose tool
   rounds keep giving the same answer (DEFECT §57). Her quick private read is told the switch
   is on (``read_note_for``), so it never says "text is fine" against it.
"""
from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any

from core.session_keys import session_key_for

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[1]

# The flag's name where the doors keep per-conversation switches
# (``bots/common/steering.py``; Telegram's ``/voice`` writes it).
FLAG = "voice_replies"
# The doors that send a reply's voice notes: a Discord or Telegram text chat
# (ruling T10-7 — dashboard voice is out of scope).
CHAT_SURFACES = ("discord_text", "telegram_text", "mobile_text")

# INTERIM (owner O2) — the owner revisits it after the batches. One line, in
# her reply call's trailing note, while the switch is on in this
# conversation: the owner's turns get STATUS_LINE, a guest's (10-12) the same
# words without the tool a guest's turn does not have.
_SPOKEN_HERE = (
    "[Voice replies are on here: just write your reply — it will be spoken and "
    "go out as a voice message. Anything they'd need to read goes out as a short "
    "text line beside it on its own: a link, a code block, or a number you put "
    "in backticks."
)
STATUS_LINE = _SPOKEN_HERE + " You don't need voice_note while this is on.]"
GUEST_STATUS_LINE = _SPOKEN_HERE + "]"

# One voice message per reply (DEFECT §56): the door sends at most this many,
# whatever a run queued, so a run that loops can never flood the chat.
MAX_VOICE_NOTES_PER_REPLY = 1

# What her quick private read (``ai/turn_read``) is told while the switch is
# on, so its read never contradicts it (10-01 19:57: "No TTS requested, so
# text is fine" while every reply was being spoken).
READ_NOTE = "(right now: voice replies are on in this chat — her replies here are spoken, as voice messages)"

# What a spoken reply says where the text line has the thing to read.
LINK_SAID = "(link in the text)"
CODE_SAID = "(it's in the text)"

_FENCE = re.compile(r"```[^\n`]*\n.*?```", re.S)
_INLINE = re.compile(r"`([^`\n]+)`")
_MD_LINK = re.compile(r"\[([^\]\n]+)\]\((https?://[^\s)]+)\)")
_URL = re.compile(r"https?://[^\s<>()\[\]`\"']+")
_URL_TRAIL = ".,;:!?"


def split_for_voice(text: str) -> tuple[str, str]:
    """(spoken, text_line): what the door says aloud, and the short text line
    that goes beside it ("" for none) — DEFECT §56.

    A code block is never read aloud (it is "in the text"); an inline code
    span is spoken AND shown (how a number gets seen); a link is said as "in
    the text", a markdown link keeps its label aloud. The text line lists each
    thing once, in order, one per line."""
    shown: list[str] = []

    def show(item: str) -> None:
        item = item.strip()
        if item and item not in shown:
            shown.append(item)

    def fence(m: re.Match) -> str:
        show(m.group(0))
        return f" {CODE_SAID} "

    def inline(m: re.Match) -> str:
        show(m.group(1))
        return m.group(1)

    def md_link(m: re.Match) -> str:
        show(m.group(2))
        return f"{m.group(1)} {LINK_SAID}"

    def url(m: re.Match) -> str:
        raw = m.group(0)
        address = raw.rstrip(_URL_TRAIL)
        show(address)
        return LINK_SAID + raw[len(address):]

    spoken = _FENCE.sub(fence, str(text or ""))
    spoken = _INLINE.sub(inline, spoken)
    spoken = _MD_LINK.sub(md_link, spoken)
    spoken = _URL.sub(url, spoken)
    spoken = re.sub(r"[ \t]+", " ", spoken)
    spoken = re.sub(r" *\n *", "\n", spoken).strip()
    # Text line items that are themselves links were shown as found; a link
    # first met inside backticks was shown as the span's text, which IS it.
    return spoken, "\n".join(shown)


def voice_notes_enabled() -> bool:
    """``voice.voice_notes``, fail-closed: unbooted settings, a missing field
    or any error read as OFF — today's behaviour."""
    try:
        from core.settings_boot import settings_service

        snap = settings_service.snapshot()
        if snap is None:
            return False
        return bool(getattr(getattr(snap, "voice", None), "voice_notes", False))
    except Exception:
        return False


# Stable alias: tests reach the real accessor past the suite's autouse pin.
_voice_notes_enabled_impl = voice_notes_enabled


def no_voice_on_this_host() -> bool:
    """Did boot find no TTS for this run? Telegram's ``/voice`` asks the same
    (``bots/telegram/commands.py`` reads it here), and so do the status line
    and the ``voice_replies`` tool, so none of them promises a voice the host
    can't make (final fix wave, ruling FW-15).

    The same field the speech facade itself reads (``_voice_lane_off`` in
    voice/speech.py): "off" when the configured provider has no key. Read off
    the config object, never through the voice stack — a line must not import
    the TTS providers. Empty means validate() has not spoken, and nothing is
    claimed."""
    try:
        from core.config import config

        return str(getattr(config, "effective_tts_provider", "") or "").strip(
            ).lower() == "off"
    except Exception:
        return False


def _her_hands_on() -> bool:
    """``features.her_hands``, read through the tool registry's own gate (so
    the suite's pin reaches it): the status line points at ``voice_note``,
    which is on her belt only with her hands (final fix wave, ruling FW-16)."""
    try:
        from core.agent import builtin_tools

        return bool(builtin_tools._her_hands_enabled())
    except Exception:
        return False


def store_path() -> Path:
    raw = os.environ.get("PALLI_VOICE_REPLIES_PATH")
    if raw:
        return Path(raw)
    return _REPO_ROOT / "data" / "voice_replies.json"


class VoiceRepliesStoreError(RuntimeError):
    """The switches file exists but cannot be read, so it is not rewritten."""


class VoiceRepliesStore:
    """The switches file. Holds nothing in memory: every call reads the file,
    so a fresh instance — after a restart, or in another process — sees
    exactly what the last write left."""

    def __init__(self, path: Path | str | None = None) -> None:
        self.path = Path(path) if path is not None else store_path()

    def _load(self, *, strict: bool) -> dict[str, bool]:
        try:
            raw = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return {}
        except OSError as exc:
            if strict:
                raise VoiceRepliesStoreError(f"can't read {self.path.name}: {exc}") from exc
            logger.warning("voice_replies: could not read %s (%s)", self.path.name, exc)
            return {}
        try:
            data = json.loads(raw) if raw.strip() else {}
            if not isinstance(data, dict):
                raise ValueError("not an object")
        except ValueError as exc:
            if strict:
                raise VoiceRepliesStoreError(
                    f"{self.path.name} is not readable switches: {exc}") from exc
            logger.warning("voice_replies: %s is unreadable; reading it as no switches",
                           self.path.name)
            return {}
        return {str(k): bool(v) for k, v in data.items() if isinstance(v, bool)}

    def is_on(self, key: str) -> bool:
        return bool(self._load(strict=False).get(str(key), False))

    def get(self, key: str, default: bool = False) -> bool:
        return bool(self._load(strict=False).get(str(key), default))

    def set(self, key: str, on: bool) -> None:
        """Set one conversation's switch. VoiceRepliesStoreError when the file
        exists but cannot be read — it is never overwritten."""
        switches = self._load(strict=True)
        switches[str(key)] = bool(on)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(switches, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(tmp, self.path)

    def conversations_on(self) -> list[str]:
        return sorted(k for k, v in self._load(strict=False).items() if v)


def key_for(surface: str, channel_id: Any, owner_id: Any) -> str:
    """The conversation's key: its session key (ruling T10-6)."""
    return session_key_for(surface, channel_id, owner_id)


def is_on(key: str) -> bool:
    return VoiceRepliesStore().is_on(key)


def set_on(key: str, on: bool) -> None:
    VoiceRepliesStore().set(key, on)


def status_line_for(turn: Any, *, owner: bool) -> str:
    """INTERIM (owner O2 — the owner revisits it after the batches): the one
    status line for her reply call, or "".

    Present only when the setting is on, the turn is a person's own message
    in a Discord or Telegram text chat — the owner's (``owner``), or since
    10-12 a guest's, whose reply the door now speaks too — and voice replies
    are on for that conversation; and only where it can come true: the host
    has a voice (FW-15), and for the owner's line her hands are on, because
    it points at ``voice_note`` (FW-16). A guest's turn is a guest's however
    the caller names it, and gets ``GUEST_STATUS_LINE``. Anything else — a
    reminder fire, her workbench, an unreadable switches file — is "" and
    the payload is untouched."""
    from core.owner_gate import is_guest_turn

    guest = is_guest_turn(turn)
    if not (owner or guest) or not voice_notes_enabled():
        return ""
    if no_voice_on_this_host() or (not guest and not _her_hands_on()):
        return ""
    try:
        surface = str(getattr(turn, "surface", "") or "")
        if surface not in CHAT_SURFACES:
            return ""
        md = getattr(turn, "metadata", None)
        if not guest and str(getattr(md, "origin", "user") or "user") != "user":
            return ""
        channel_id = getattr(md, "channel_id", 0) or 0
        if not channel_id:
            return ""
        on = is_on(key_for(surface, channel_id, getattr(turn, "owner_id", 0)))
    except Exception as exc:  # noqa: BLE001 — a status line is never worth a turn
        logger.warning("voice_replies: status line skipped: %s", type(exc).__name__)
        return ""
    if not on:
        return ""
    return GUEST_STATUS_LINE if guest else STATUS_LINE


def read_note_for(turn: Any) -> str:
    """The line her quick private read gets while voice replies are on for
    this turn's conversation, or "" (DEFECT §56). Exactly where the status
    line would show: a person's own message — the owner's, or a guest's
    (10-12) — in a chat with the switch on."""
    return READ_NOTE if status_line_for(turn, owner=True) else ""


def has_words_to_speak(spoken: str) -> bool:
    """False when a spoken reply is only "(link in the text)" and the like —
    a reply that is just a link goes as text, not as a voice saying "link"."""
    rest = spoken.replace(LINK_SAID, " ").replace(CODE_SAID, " ")
    return bool(re.sub(r"[\W_]+", "", rest))
