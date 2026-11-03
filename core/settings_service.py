"""Service that loads, validates, and mutates PaLLi structured settings."""

from __future__ import annotations

import copy
import datetime as _datetime
import logging
import secrets
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import yaml
from pydantic import ValidationError

from core.settings_schema import PalliSettings

logger = logging.getLogger(__name__)

PROPOSAL_TTL_SECONDS = 60.0
ROLLBACK_TTL_SECONDS = 300.0


@dataclass
class DiffEntry:
    path: str
    old: Any
    new: Any

    def as_dict(self) -> Dict[str, Any]:
        return {"path": self.path, "old": self.old, "new": self.new}


@dataclass
class ProposalResult:
    change_id: str
    diff: List[Dict[str, Any]]
    warnings: List[str] = field(default_factory=list)


@dataclass
class _PendingChange:
    change_id: str
    changes: Dict[str, Any]
    diff: List[DiffEntry]
    new_snapshot: PalliSettings
    reason: str
    created_at: float


@dataclass
class _RollbackRecord:
    token: str
    previous_snapshot: PalliSettings
    created_at: float


@dataclass
class ApplyResult:
    applied: bool
    rollback_token: str


@dataclass
class SettingsChanged:
    change_id: str
    diff: List[Dict[str, Any]]
    old: PalliSettings
    new: PalliSettings


class SettingsServiceError(RuntimeError):
    """Raised when the settings service cannot fulfil a request."""


class SettingsService:
    """Loads, validates, and mutates data/palli.yaml.

    Phase 1 scope: load + atomic write + propose/apply protocol + audit log
    + event emission. No external callers yet; Phase 2 redirects config
    reads through this service.
    """

    def __init__(
        self,
        yaml_path: Path,
        *,
        history_path: Optional[Path] = None,
    ) -> None:
        self._yaml_path = Path(yaml_path)
        self._history_path = Path(history_path) if history_path else self._yaml_path.with_name(
            "palli-config-history.jsonl"
        )
        self._snapshot: Optional[PalliSettings] = None
        self._pending: Dict[str, _PendingChange] = {}
        self._rollbacks: Dict[str, _RollbackRecord] = {}
        self._listeners: List[Callable[[SettingsChanged], None]] = []
        self._clock: Callable[[], float] = time.monotonic

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------
    def load(self) -> PalliSettings:
        if not self._yaml_path.exists():
            raise SettingsServiceError(
                f"settings file not found at {self._yaml_path}"
            )
        try:
            raw = yaml.safe_load(self._yaml_path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:
            raise SettingsServiceError(f"could not parse {self._yaml_path}: {exc}") from exc
        try:
            self._snapshot = PalliSettings.model_validate(raw)
        except ValidationError as exc:
            raise SettingsServiceError(
                f"settings at {self._yaml_path} failed validation: {exc}"
            ) from exc
        return self._snapshot

    def snapshot(self) -> PalliSettings:
        if self._snapshot is None:
            return self.load()
        return self._snapshot

    # ------------------------------------------------------------------
    # Atomic write
    # ------------------------------------------------------------------
    def _atomic_write(
        self, settings: PalliSettings, *, previous: Optional[PalliSettings] = None
    ) -> None:
        import os
        import tempfile

        yaml_text = self._render_yaml_text(settings, previous=previous)
        parent = self._yaml_path.parent
        parent.mkdir(parents=True, exist_ok=True)

        # NamedTemporaryFile with delete=False so we can os.replace to target
        fd = tempfile.NamedTemporaryFile(
            mode="w",
            prefix=self._yaml_path.name + ".tmp.",
            dir=str(parent),
            delete=False,
            encoding="utf-8",
        )
        tmp_path = Path(fd.name)
        try:
            fd.write(yaml_text)
            fd.flush()
            os.fsync(fd.fileno())
            fd.close()
            # Windows silently ignores 0o600 for some configs; suppress if unsupported.
            try:
                os.chmod(tmp_path, 0o600)
            except (NotImplementedError, PermissionError, OSError):
                pass
            os.replace(tmp_path, self._yaml_path)
        except Exception:
            try:
                fd.close()
            except Exception:
                pass
            tmp_path.unlink(missing_ok=True)
            raise

    def _render_yaml_text(
        self, settings: PalliSettings, *, previous: Optional[PalliSettings] = None
    ) -> str:
        """Comment-preserving when the file exists: load it round-trip (ruamel),
        set only the leaves that differ between `previous` and `settings`, dump.
        A fresh or unreadable file, or no `previous`, gets a full round-trip dump.

        2026-09-03 (configurator spec §3.3 R1): `yaml.safe_dump` erased every
        comment in data/palli.yaml — 375 of 826 lines. Lists are compared whole
        (a changed model catalogue loses the comments inside that one list;
        everything else survives).
        """
        from io import StringIO

        from ruamel.yaml import YAML

        rt = YAML(typ="rt")
        rt.preserve_quotes = True
        rt.indent(mapping=2, sequence=4, offset=2)
        rt.width = 4096  # never fold a long row (Ruling 8, 2026-09-04): a diff after an apply shows only the change
        new_data = settings.model_dump(mode="json", exclude_none=True)
        doc: Any = None
        if previous is not None and self._yaml_path.exists():
            try:
                doc = rt.load(self._yaml_path.read_text(encoding="utf-8"))
            except Exception as exc:
                logger.warning(
                    "round-trip load of %s failed (%s: %s); writing a full dump — its comments will be lost",
                    self._yaml_path, type(exc).__name__, exc,
                )
                doc = None
        buf = StringIO()
        if not isinstance(doc, dict):
            rt.dump(new_data, buf)
            return buf.getvalue()
        old_data = previous.model_dump(mode="json", exclude_none=True)
        for parts in _changed_leaf_paths(old_data, new_data):
            _set_leaf(doc, parts, _get_leaf(new_data, parts))
        rt.dump(doc, buf)
        return buf.getvalue()

    # ------------------------------------------------------------------
    # Propose / apply protocol
    # ------------------------------------------------------------------
    def propose(self, changes: Dict[str, Any], *, reason: str = "") -> ProposalResult:
        """Validate a proposed change set and stash it under a short-lived change_id."""
        current = self.snapshot()
        merged = _apply_dot_paths(current.model_dump(mode="json"), changes)
        try:
            new_snapshot = PalliSettings.model_validate(merged)
        except ValidationError as exc:
            raise SettingsServiceError(f"proposed changes failed validation: {exc}") from exc

        diff = _compute_diff(
            current.model_dump(mode="json"),
            new_snapshot.model_dump(mode="json"),
            scope_keys=changes.keys(),
        )
        if not diff:
            # No-op: don't store a pending change, don't issue a change_id.
            # The caller gets an empty ProposalResult to signal "nothing to apply".
            return ProposalResult(change_id="", diff=[])
        change_id = "chg_" + secrets.token_hex(12)
        self._pending[change_id] = _PendingChange(
            change_id=change_id,
            changes=changes,
            diff=diff,
            new_snapshot=new_snapshot,
            reason=reason,
            created_at=self._clock(),
        )
        self._prune_expired()
        return ProposalResult(
            change_id=change_id,
            diff=[entry.as_dict() for entry in diff],
        )

    def _prune_expired(self) -> None:
        now = self._clock()
        expired = [
            cid
            for cid, pending in self._pending.items()
            if now - pending.created_at > PROPOSAL_TTL_SECONDS
        ]
        for cid in expired:
            self._pending.pop(cid, None)

    def apply(self, change_id: str) -> ApplyResult:
        pending = self._pending.get(change_id)
        if pending is None:
            raise SettingsServiceError(f"change_id {change_id!r} is unknown")
        now = self._clock()
        if now - pending.created_at > PROPOSAL_TTL_SECONDS:
            self._pending.pop(change_id, None)
            raise SettingsServiceError(
                f"change_id {change_id!r} has expired; re-propose the change"
            )

        previous = self.snapshot()
        self._atomic_write(pending.new_snapshot, previous=previous)
        self._snapshot = pending.new_snapshot
        self._pending.pop(change_id, None)

        rollback_token = "rb_" + secrets.token_hex(12)
        self._rollbacks[rollback_token] = _RollbackRecord(
            token=rollback_token,
            previous_snapshot=previous,
            created_at=now,
        )
        self._prune_expired_rollbacks(now=now)

        self._broadcast(
            SettingsChanged(
                change_id=change_id,
                diff=[entry.as_dict() for entry in pending.diff],
                old=previous,
                new=pending.new_snapshot,
            )
        )

        try:
            self._append_audit(
                {
                    "timestamp": _datetime.datetime.now(_datetime.timezone.utc).isoformat(),
                    "change_id": change_id,
                    "diff": [entry.as_dict() for entry in pending.diff],
                    "reason": pending.reason,
                    "rollback_token": rollback_token,
                }
            )
        except OSError:
            logger.warning("audit write failed for change_id %r; change was applied", change_id)
        return ApplyResult(applied=True, rollback_token=rollback_token)

    def _append_audit(self, entry: Dict[str, Any]) -> None:
        import json

        self._history_path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(entry, ensure_ascii=False) + "\n"
        with self._history_path.open("a", encoding="utf-8") as handle:
            handle.write(line)

    def _prune_expired_rollbacks(self, *, now: float) -> None:
        stale = [
            token
            for token, record in self._rollbacks.items()
            if now - record.created_at > ROLLBACK_TTL_SECONDS
        ]
        for token in stale:
            self._rollbacks.pop(token, None)

    # ------------------------------------------------------------------
    # Rollback + events
    # ------------------------------------------------------------------
    def rollback(self, rollback_token: str) -> PalliSettings:
        now = self._clock()
        record = self._rollbacks.get(rollback_token)
        if record is None:
            raise SettingsServiceError(f"rollback_token {rollback_token!r} is unknown")
        if now - record.created_at > ROLLBACK_TTL_SECONDS:
            self._rollbacks.pop(rollback_token, None)
            raise SettingsServiceError(
                f"rollback_token {rollback_token!r} has expired"
            )

        old_snapshot = self.snapshot()
        self._atomic_write(record.previous_snapshot, previous=old_snapshot)
        self._snapshot = record.previous_snapshot
        self._rollbacks.pop(rollback_token, None)

        rollback_diff = _compute_diff(
            old_snapshot.model_dump(mode="json"),
            record.previous_snapshot.model_dump(mode="json"),
            scope_keys=_collect_changed_paths(
                old_snapshot.model_dump(mode="json"),
                record.previous_snapshot.model_dump(mode="json"),
            ),
        )

        try:
            self._append_audit(
                {
                    "timestamp": _datetime.datetime.now(_datetime.timezone.utc).isoformat(),
                    "change_id": f"rollback:{rollback_token}",
                    "diff": [entry.as_dict() for entry in rollback_diff],
                    "reason": "rollback",
                    "rollback_token": rollback_token,
                }
            )
        except OSError:
            logger.warning("audit write failed for rollback %r; rollback was applied", rollback_token)

        self._broadcast(
            SettingsChanged(
                change_id=f"rollback:{rollback_token}",
                diff=[entry.as_dict() for entry in rollback_diff],
                old=old_snapshot,
                new=record.previous_snapshot,
            )
        )
        return record.previous_snapshot

    def subscribe(self, listener: Callable[[SettingsChanged], None]) -> None:
        self._listeners.append(listener)

    def _broadcast(self, event: SettingsChanged) -> None:
        for listener in list(self._listeners):
            try:
                listener(event)
            except Exception:
                logger.exception("settings listener raised; continuing")


def _apply_dot_paths(root: Dict[str, Any], changes: Dict[str, Any]) -> Dict[str, Any]:
    result = copy.deepcopy(root)
    for path, value in changes.items():
        parts = path.split(".")
        cursor: Dict[str, Any] = result
        for part in parts[:-1]:
            nxt = cursor.get(part)
            if nxt is None:
                nxt = {}
                cursor[part] = nxt
            elif not isinstance(nxt, dict):
                raise SettingsServiceError(
                    f"cannot descend into {path!r}: segment {part!r} is a "
                    f"{type(nxt).__name__}, not a dict"
                )
            cursor = nxt
        cursor[parts[-1]] = copy.deepcopy(value)
    return result


def _compute_diff(
    old: Dict[str, Any],
    new: Dict[str, Any],
    *,
    scope_keys,
) -> List[DiffEntry]:
    entries: List[DiffEntry] = []
    for key in scope_keys:
        old_value = _lookup_path(old, key)
        new_value = _lookup_path(new, key)
        if old_value != new_value:
            entries.append(DiffEntry(path=key, old=old_value, new=new_value))
    return entries


def _lookup_path(root: Dict[str, Any], path: str) -> Any:
    cursor: Any = root
    for part in path.split("."):
        if not isinstance(cursor, dict) or part not in cursor:
            return None
        cursor = cursor[part]
    return cursor


def _changed_leaf_paths(old: Any, new: Any, prefix: tuple = ()) -> List[tuple]:
    """Leaf paths (as key tuples, so dotted keys inside presets survive) where
    `old` and `new` differ. Dicts recurse; anything else is a leaf."""
    if isinstance(old, dict) and isinstance(new, dict):
        out: List[tuple] = []
        for key in sorted(set(old) | set(new), key=str):
            out.extend(_changed_leaf_paths(old.get(key), new.get(key), prefix + (key,)))
        return out
    return [prefix] if old != new else []


def _get_leaf(root: Any, parts: tuple) -> Any:
    cursor: Any = root
    for part in parts:
        if not isinstance(cursor, dict) or part not in cursor:
            return None
        cursor = cursor[part]
    return cursor


def _set_leaf(doc: Any, parts: tuple, value: Any) -> None:
    """Set (or, for None, delete) one leaf inside a ruamel round-trip document,
    creating intermediate maps, so the comments around every sibling survive."""
    from ruamel.yaml.comments import CommentedMap

    cursor: Any = doc
    for part in parts[:-1]:
        nxt = cursor.get(part)
        if not isinstance(nxt, dict):
            nxt = CommentedMap()
            cursor[part] = nxt
        cursor = nxt
    if value is None:
        cursor.pop(parts[-1], None)
    else:
        cursor[parts[-1]] = value


def _collect_changed_paths(old: Dict[str, Any], new: Dict[str, Any], *, prefix: str = "") -> List[str]:
    paths: List[str] = []
    keys = set(old.keys()) | set(new.keys()) if isinstance(old, dict) and isinstance(new, dict) else set()
    for key in keys:
        path = f"{prefix}.{key}" if prefix else key
        old_value = old.get(key) if isinstance(old, dict) else None
        new_value = new.get(key) if isinstance(new, dict) else None
        if isinstance(old_value, dict) and isinstance(new_value, dict):
            paths.extend(_collect_changed_paths(old_value, new_value, prefix=path))
        elif old_value != new_value:
            paths.append(path)
    return paths
