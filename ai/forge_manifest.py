"""The forge manifest (step 34, spec §5) — shared by the forge engine (writer)
and the registry loader (reader).

``data/forge/`` layout (root overridable via ``FORGE_ROOT`` for tests — the
TOOL_RESULT_SIDECAR_ROOT pattern):

    manifest.json        one list of entries, the loader's whole truth
    tools/<name>.py      installed forged tools (each defines build_tool())
    filings/<name>/      the receipt archive: code + tests + transcript + the ask

A manifest entry:
    {name, file, sha256, created_at, original_ask, test_report,
     enabled, consecutive_errors}

The sha256 is PROVENANCE/integrity — it catches corruption or drift and a
mismatch refuses to load, loudly. It is not a permission mechanism; nothing
in this design asks permission (spec §0, owner 2026-08-21 04:11).
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[1]


def forge_root() -> Path:
    raw = os.environ.get("FORGE_ROOT")
    if raw:
        return Path(raw).resolve()
    return _REPO_ROOT / "data" / "forge"


def manifest_path() -> Path:
    return forge_root() / "manifest.json"


def tools_dir() -> Path:
    return forge_root() / "tools"


def filings_dir() -> Path:
    return forge_root() / "filings"


def sha256_of(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read_manifest() -> list[dict[str, Any]]:
    """Missing file = no forged tools yet (the pre-first-forge normal).
    A corrupt manifest is LOUD but never breaks a turn: log ERROR, load
    nothing (spec §2.5 — silence made visible, failures contained)."""
    path = manifest_path()
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return []
    except OSError as exc:
        logger.error("forge manifest unreadable at %s: %s", path, exc)
        return []
    try:
        entries = json.loads(raw)
    except ValueError as exc:
        logger.error("forge manifest corrupt at %s: %s", path, exc)
        return []
    if not isinstance(entries, list):
        logger.error("forge manifest at %s is not a list; ignoring", path)
        return []
    return entries


def write_manifest(entries: list[dict[str, Any]]) -> None:
    """Atomic replace so a crash mid-write never leaves a half manifest
    (the loader runs on every turn's registry build)."""
    path = manifest_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(entries, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def active_marker_path() -> Path:
    return forge_root() / "active.json"


def claim_active(name: str, original_ask: str = "") -> bool:
    """One forge project at a time (spec §2.5): exclusive-create the marker.
    False = the anvil is occupied. The engine clears it on every exit path
    (green, red, exception) — a stuck marker is a bug, not a state."""
    path = active_marker_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    import time

    try:
        with open(path, "x", encoding="utf-8") as fh:
            json.dump(
                {"name": name, "original_ask": original_ask, "started_at": time.time()},
                fh,
            )
        return True
    except FileExistsError:
        return False


def active_project() -> dict[str, Any] | None:
    try:
        return json.loads(active_marker_path().read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        logger.error("forge active-marker unreadable: %s", exc)
        return {"name": "(unreadable marker)"}


def clear_active() -> None:
    try:
        active_marker_path().unlink(missing_ok=True)
    except OSError as exc:
        logger.error("forge active-marker unlink failed: %s", exc)


def get_entry(name: str) -> dict[str, Any] | None:
    for entry in read_manifest():
        if entry.get("name") == name:
            return entry
    return None


def update_entry(name: str, **fields: Any) -> None:
    """Set fields on one entry by name and persist. Unknown name is a no-op
    (the entry may have been removed underneath us; never raise here — this
    is called from error paths)."""
    entries = read_manifest()
    changed = False
    for entry in entries:
        if entry.get("name") == name:
            entry.update(fields)
            changed = True
    if changed:
        write_manifest(entries)
