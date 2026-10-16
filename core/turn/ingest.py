from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Mapping, get_args

from core.agent.speakers import normalize_speaker
from core.turn.types import ImageRef, SurfaceName, Turn, TurnMetadata

_VALID_SURFACES = set(get_args(SurfaceName))
_DISCORD_MENTION_PATTERN = re.compile(r"<@!?\d+>")


def normalize_turn(raw: Mapping[str, Any]) -> Turn:
    """Take a surface-shaped dict and produce a canonical Turn.

    The boundary between surface adapters and the spine is here. Anything
    surface-specific (Discord message objects, voice transcripts, dashboard
    websocket payloads, synthesized proactive turns) gets translated to
    this canonical form before any spine code sees it.

    Raises:
        ValueError: if `surface` is missing or not in SurfaceName, or if
            owner_id is not a positive integer.
    """
    surface = str(raw.get("surface") or "").strip()
    if surface not in _VALID_SURFACES:
        raise ValueError(f"unknown surface: {surface!r}")

    owner_id = _coerce_owner_id(raw.get("owner_id"))
    if owner_id <= 0:
        raise ValueError(f"owner_id must be positive, got {raw.get('owner_id')!r}")

    content = _DISCORD_MENTION_PATTERN.sub("", str(raw.get("content") or "")).strip()

    images = _coerce_images(raw.get("images") or [])
    metadata = _coerce_metadata(raw.get("metadata") or {})
    received_at = _coerce_received_at(raw.get("received_at"))

    return Turn(
        surface=surface,  # type: ignore[arg-type]
        owner_id=owner_id,
        content=content,
        images=images,
        metadata=metadata,
        received_at=received_at,
    )


def _coerce_owner_id(value: Any) -> int:
    if value is None:
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _coerce_images(raw_images: Any) -> list[ImageRef]:
    images: list[ImageRef] = []
    if not isinstance(raw_images, (list, tuple)):
        return images
    for entry in raw_images:
        if isinstance(entry, ImageRef):
            images.append(entry)
            continue
        if isinstance(entry, Mapping):
            images.append(
                ImageRef(
                    url=entry.get("url"),
                    local_path=entry.get("local_path"),
                    mime=entry.get("mime"),
                    description=entry.get("description"),
                )
            )
    return images


def _coerce_metadata(raw_metadata: Mapping[str, Any]) -> TurnMetadata:
    origin = raw_metadata.get("origin", "user")
    if origin not in ("user", "monologue", "reminder", "dashboard_user", "workbench", "guest"):
        origin = "user"
    return TurnMetadata(
        channel_id=_optional_int(raw_metadata.get("channel_id")),
        is_dm=bool(raw_metadata.get("is_dm", False)),
        is_mention=bool(raw_metadata.get("is_mention", False)),
        is_reply_to_bot=bool(raw_metadata.get("is_reply_to_bot", False)),
        is_voice=bool(raw_metadata.get("is_voice", False)),
        batch_message_ids=tuple(int(x) for x in (raw_metadata.get("batch_message_ids") or ()) if _is_intlike(x)),
        origin=origin,  # type: ignore[arg-type]
        candidate_tick=_optional_datetime(raw_metadata.get("candidate_tick")),
        reminder_id=_optional_int(raw_metadata.get("reminder_id")),
        discord_username=_optional_str(raw_metadata.get("discord_username")),
        extra=dict(raw_metadata.get("extra") or {}),
        speaker=normalize_speaker(raw_metadata.get("speaker")),
    )


def _coerce_received_at(value: Any) -> datetime:
    if value is None:
        return datetime.utcnow()
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return datetime.utcnow()
    return datetime.utcnow()


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    s = str(value).strip()
    return s if s else None


def _optional_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    return None


def _is_intlike(value: Any) -> bool:
    try:
        int(value)
        return True
    except (TypeError, ValueError):
        return False
