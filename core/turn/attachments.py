"""What he attached — one wave (split out of recall.py 2026-09-02 under rule 6's
coupling budget, the way the organ bands were the day before).

Three readers of the same turn: the vision read of his images, the contents
of his text/code files, and — DEFECT_RECORD §19 — the inbox lines for every
other kind of file (a spreadsheet, a PDF), each downloaded once into
data/inbox/ so her hands can reach it. Recall's thin `_w_*` wrappers call
these; the `fetch` seam is passed in so recall keeps `_fetch_image_bytes`
as the one name tests patch.
"""
from __future__ import annotations

import logging
from pathlib import PurePosixPath
from typing import Any, Awaitable, Callable, Optional
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)

Fetch = Callable[[str], Awaitable[Optional[bytes]]]

_IMAGE_EXT = {
    "image/jpeg": "jpg",
    "image/jpg": "jpg",
    "image/png": "png",
    "image/gif": "gif",
    "image/webp": "webp",
}


def _image_ext(content_type: str, ref: str) -> str:
    """The saved image's extension: from the content type, else the ref's
    own suffix (query string dropped), else ``img``."""
    ext = _IMAGE_EXT.get(str(content_type or "").split(";")[0].strip().lower())
    if ext:
        return ext
    suffix = PurePosixPath(urlsplit(str(ref or "")).path).suffix.lstrip(".").lower()
    return suffix if suffix.isalnum() else "img"


def _compose_image_text(result: dict) -> str:
    """What she is handed for one image (Batch 3, 1.1): the text the vision
    model read off it first, exactly as written, then what the picture
    shows. The model's guess at what the image MEANS in the conversation
    (``contextual_understanding``) is never handed to her as fact — that
    judgment is hers. A missing, None or non-string ``visible_text`` (the
    providers' non-JSON fallback dicts carry no such key) is no text."""
    visible = result.get("visible_text")
    visible = visible.strip() if isinstance(visible, str) else ""
    shows = str(result.get("description") or "").strip()
    parts: list[str] = []
    if visible:
        parts.append(f"Text in the image:\n{visible}")
    if shows:
        parts.append(f"What it shows: {shows}")
    return "\n".join(parts)


def _save_image_to_inbox(data: bytes, content_type: str, ref: str) -> Optional[str]:
    """Keep the image under data/inbox/images/ so she can look at it again
    (her hands reach the inbox). Returns the saved path, or None after one
    warning — over the cap or any failure, the description still goes
    through without it."""
    try:
        from core.turn.inbox import inbox_root, save_bytes

        path = save_bytes(data, f"image.{_image_ext(content_type, ref)}", root=inbox_root() / "images")
        return str(path)
    except Exception as exc:
        logger.warning("recall: image not saved to the inbox: %s", exc)
        return None


def _local_path_for(url: str) -> "Optional[str]":
    """The on-disk path this reference names, or None if it is a real URL.

    Telegram door (2026-09-14, task 2): a Telegram file URL carries her bot
    token in its path, so that door materializes its attachments at intake
    and puts a ``file://`` reference on the turn instead. A bare path counts
    too — ``describe_images`` passes ``ImageRef.local_path`` to this function
    as the "url".
    """
    value = str(url or "").strip()
    if not value:
        return None
    if value.startswith("file://"):
        return value[len("file://"):]
    if "://" in value:
        return None
    return value


async def fetch_attachment_bytes(url: str) -> bytes | None:
    """Fetch the bytes behind an image/text-attachment reference.

    An http(s) URL is fetched (Discord's path, unchanged); a ``file://``
    reference or a bare path is read off disk.

    Returns None on any failure rather than raising — callers degrade
    gracefully when one attachment can't be fetched.
    """
    local = _local_path_for(url)
    if local is not None:
        try:
            from pathlib import Path as _Path

            path = _Path(local)
            if not path.is_file():
                logger.debug("attachments: no local file at %s", local)
                return None
            return path.read_bytes()
        except Exception as exc:
            logger.debug("attachments: local read failed for %s: %s", local, exc)
            return None
    try:
        import aiohttp
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=20)) as resp:
                if resp.status != 200:
                    return None
                return await resp.read()
    except Exception as exc:
        logger.debug("attachments: fetch failed for %s: %s", url, exc)
        return None


async def describe_images(turn: Any, recent_history: Any, *, fetch: Fetch) -> tuple[str, ...]:
    # vision analysis on attached images. analyze_image_with_context is a
    # METHOD on the vision_analyzer singleton returning a DICT. Failures
    # log at WARNING (this path silently rotted once — a real photo went undescribed).
    # She is handed the text in the image and what it shows (never the
    # model's guess at meaning), and the image is kept in her inbox.
    image_descriptions: tuple[str, ...] = ()
    if turn.images:
        descriptions: list[str] = []
        try:
            from ai.vision_analyzer import vision_analyzer
            history_for_vision = [
                {"role": getattr(h, "role", ""), "content": getattr(h, "content", "")}
                for h in recent_history[-4:]
            ]
            for image in turn.images:
                url = str(
                    getattr(image, "url", "") or getattr(image, "local_path", "") or ""
                ).strip()
                if not url:
                    continue
                try:
                    data = await fetch(url)
                    if not data:
                        continue
                    content_type = str(getattr(image, "mime", "") or "image/jpeg")
                    result = await vision_analyzer.analyze_image_with_context(
                        image_data=data,
                        content_type=content_type,
                        conversation_history=history_for_vision,
                        user_message=str(turn.content or "").strip(),
                        user_id=int(turn.owner_id),
                    )
                    if isinstance(result, dict):
                        text = _compose_image_text(result)
                    else:
                        text = str(result or "").strip()
                    if text:
                        saved = _save_image_to_inbox(data, content_type, url)
                        if saved:
                            text = f"{text}\n(saved at {saved})"
                        descriptions.append(text)
                except Exception as exc:
                    logger.warning("recall: image analysis failed: %s", exc)
                    continue
        except Exception as exc:
            logger.warning("recall: vision analysis unavailable: %s", exc)
        image_descriptions = tuple(descriptions)
        # Honesty guard: an image was attached but nothing came back. Tell
        # PaLLi it genuinely cannot see it so it asks instead of guessing.
        if not image_descriptions:
            image_descriptions = (
                "[An image was attached, but it could not be analyzed — its "
                "visual contents are unavailable to you. Do NOT describe or "
                "guess what the image shows; acknowledge you can't see it "
                "clearly and ask the user what it is.]",
            )
    return image_descriptions


async def text_attachment_contents(turn: Any, *, fetch: Fetch) -> tuple[str, ...]:
    # text/code attachment contents.
    refs = getattr(turn.metadata, "text_attachments", None) or ()
    if not refs:
        return ()
    out: list[str] = []
    for ref in refs:
        url = str(getattr(ref, "url", "") or "").strip()
        filename = str(getattr(ref, "filename", "") or "attachment")
        if not url:
            continue
        try:
            data = await fetch(url)
            if not data:
                continue
            try:
                text = data.decode("utf-8", errors="replace")
            except Exception:
                text = ""
            text = (text or "").strip()
            if not text:
                continue
            out.append(f"{filename}\n{text}")
        except Exception as exc:
            logger.warning("recall: text attachment load failed: %s", exc)
            continue
    return tuple(out)


async def file_attachment_lines(turn: Any, *, fetch: Fetch) -> tuple[str, ...]:
    # DEFECT_RECORD §19 (2026-09-02): non-text files (a spreadsheet, a
    # PDF) land in the inbox; each becomes a line naming the file and
    # where it went, in the same slot the text attachments use.
    refs = getattr(turn.metadata, "file_attachments", None) or ()
    if not refs:
        return ()
    from core.turn.inbox import receive_file
    out: list[str] = []
    for ref in refs:
        try:
            out.append(await receive_file(ref, fetch=fetch))
        except Exception as exc:
            logger.warning("recall: file attachment receive failed: %s", exc)
    return tuple(out)
