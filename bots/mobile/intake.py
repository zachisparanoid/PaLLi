"""What the owner sends from the phone, as a Turn (spec §6).

The phone app's sends arrive as multipart: a ``client_id`` (a UUID the app
made, so a retry never starts a second turn), optional text, and parts in
three fields — ``images`` (her eyes), ``files`` (sorted the way Telegram's
are) and one ``voice`` note (heard through the configured speech stack). The
Turn is built directly, as Telegram's is: ``normalize_turn`` drops
attachments (core/turn/ingest.py).
"""
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from bots.common.attachments import is_text_like
from bots.common.voice_note import VOICE_PLACEHOLDER_CONTENT, resolve_voice_content, transcribe_note
from bots.mobile import MOBILE_CHANNEL_ID, MOBILE_PLATFORM, MOBILE_SURFACE
from core.agent.speakers import make_speaker
from core.turn.inbox import safe_name
from core.turn.types import ImageRef, TextAttachmentRef, Turn, TurnMetadata

MAX_PART_BYTES = 20 * 1024 * 1024
MAX_TOTAL_BYTES = 60 * 1024 * 1024
# A send's text, in UTF-8 bytes (review M7). Generous for a phone's composer;
# a runaway client cannot hand her a megabyte as one message. Longer goes as a file.
MAX_TEXT_BYTES = 65_536
_IMAGE_ONLY = "[image attached]"
_FILE_ONLY = "[file attached]"
_UUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


@dataclass(frozen=True)
class Upload:
    field: str            # "images" | "files" | "voice"
    filename: str
    content_type: str
    data: bytes


class SendRefused(Exception):
    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status = int(status)
        self.detail = detail


def check_sizes(parts: Sequence[Tuple[str, int]]) -> None:
    """Refuse parts that are too big, by (filename, size) — so a route can
    refuse before it reads a byte of them into memory."""
    for filename, size in parts:
        if size > MAX_PART_BYTES:
            raise SendRefused(413, f"{filename} is over {MAX_PART_BYTES // (1024 * 1024)} MB.")
    if sum(size for _, size in parts) > MAX_TOTAL_BYTES:
        raise SendRefused(413, f"Together these are over {MAX_TOTAL_BYTES // (1024 * 1024)} MB.")


def check_send(client_id: str, text: str, uploads: Sequence[Upload]) -> None:
    """Refuse a send before anything is saved or started."""
    if not _UUID.match(str(client_id or "")):
        raise SendRefused(422, "client_id must be a UUID.")
    if len(str(text or "").encode("utf-8", "surrogatepass")) > MAX_TEXT_BYTES:
        raise SendRefused(422, "That message is too long to send — send it as a file instead.")
    if not str(text or "").strip() and not uploads:
        raise SendRefused(422, "There is nothing to send.")
    if sum(1 for u in uploads if u.field == "voice") > 1:
        raise SendRefused(422, "One voice note per message.")
    check_sizes([(u.filename, len(u.data)) for u in uploads])


def save_upload(upload: Upload, *, directory: Path) -> Path:
    """Write one part into ``directory`` under a safe name. A name with path
    parts in it (``../../etc/passwd``) keeps only its last part."""
    directory.mkdir(parents=True, exist_ok=True)
    name = safe_name(Path(str(upload.filename or "file")).name) or "file"
    path = directory / f"{uuid.uuid4().hex[:12]}-{name}"
    path.write_bytes(upload.data)
    return path


async def build_turn(*, owner_id: int, client_id: str, text: str, uploads: Sequence[Upload],
                     directory: Path, store: Any, transcribe: Any = None) -> Tuple[Turn, Dict[str, Any]]:
    """Save the parts, hear the voice note, and build the Turn. Returns the
    Turn and the ``user_message`` payload for the timeline."""
    hear = transcribe or transcribe_note
    content = str(text or "").strip()
    images: List[ImageRef] = []
    text_refs: List[TextAttachmentRef] = []
    file_refs: List[TextAttachmentRef] = []
    file_events: List[Dict[str, Any]] = []
    heard_as: Optional[str] = None
    is_voice = False
    for upload in uploads:
        path = save_upload(upload, directory=directory)
        shown = Path(str(upload.filename or "")).name or path.name
        ref = await store.register_file(owner_id=int(owner_id), path=str(path), filename=shown,
                                        content_type=upload.content_type)
        file_events.append(ref)
        url = f"file://{path}"
        if upload.field == "voice":
            is_voice = True
            heard_as = await hear(upload.data, mime=upload.content_type, filename=path.name)
            content = resolve_voice_content(heard_as, content or VOICE_PLACEHOLDER_CONTENT)
        elif upload.field == "images" or upload.content_type.startswith("image/"):
            images.append(ImageRef(url=None, local_path=url, mime=upload.content_type, description=shown))
        elif is_text_like(mime=upload.content_type, filename=shown):
            text_refs.append(TextAttachmentRef(url=url, filename=shown, mime=upload.content_type,
                                               size_bytes=len(upload.data)))
        else:
            file_refs.append(TextAttachmentRef(url=url, filename=shown, mime=upload.content_type,
                                               size_bytes=len(upload.data)))
    if not content:
        content = _IMAGE_ONLY if images else _FILE_ONLY
    metadata = TurnMetadata(
        speaker=make_speaker("owner", platform=MOBILE_PLATFORM, account_id=int(owner_id)),
        channel_id=MOBILE_CHANNEL_ID,
        is_dm=True,
        is_voice=is_voice,
        origin="user",
        text_attachments=tuple(text_refs),
        file_attachments=tuple(file_refs),
        extra={"mobile_client_id": client_id},
    )
    turn = Turn(surface=MOBILE_SURFACE, owner_id=int(owner_id), content=content, images=images,
                metadata=metadata)
    payload: Dict[str, Any] = {"text": str(text or "").strip(), "files": file_events}
    if heard_as:
        payload["heard_as"] = heard_as
    return turn, payload
