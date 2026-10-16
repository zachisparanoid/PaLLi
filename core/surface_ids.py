from __future__ import annotations

from typing import Any, Optional


def normalize_surface_id(
    surface_id: Optional[Any] = None,
    *,
    channel_id: Optional[Any] = None,
) -> str:
    clean = str(surface_id or "").strip()
    if clean:
        return clean
    if channel_id is None:
        return ""
    return str(channel_id).strip()


def coerce_legacy_channel_id(
    surface_id: Optional[Any] = None,
    *,
    channel_id: Optional[Any] = None,
) -> Optional[int]:
    if channel_id is not None:
        try:
            return int(channel_id)
        except Exception:
            return None
    clean = str(surface_id or "").strip()
    if not clean:
        return None
    try:
        return int(clean)
    except Exception:
        return None


def session_surface_suffix(
    surface_id: Optional[Any] = None,
    *,
    channel_id: Optional[Any] = None,
) -> str:
    clean = normalize_surface_id(surface_id, channel_id=channel_id)
    return clean or "0"
