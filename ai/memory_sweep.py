"""The nightly memory sweep (Batch 5, memory that picks, 2026-09-28 — spec 2.2).

MEMORY.md only ever grew. Every night, after the reflection, this pass reads
her memory of them by age and use and MOVES what has gone unused to
MEMORY_ARCHIVE.md — the same section, the same text, plus a
``[archived YYYY-MM-DD]`` marker — where recall can still look a line up.
Nothing is deleted: both documents are written through the identity store,
which keeps every version, and ``palli_ctl memory restore <mcb_id>`` brings
a line back (``palli_ctl memory archived`` lists the ids).

The rules (the plan, rulings R4, R11, R12, R13, R42):

- A bullet under Durable Context or Useful References whose age —
  ``max(its first [YYYY-MM-DD] stamp, its last reference)`` — is more than
  ``memory.durable_archive_days`` days moves; an Active Threads bullet
  likewise after ``memory.thread_archive_days``. 0 days turns that class of
  move off.
- A bullet with no stamp and no reference is not moved: it gets today's stamp
  written into it (``- [YYYY-MM-DD] body``), so from now on its age is known
  (Review Focus 2). A bullet with no stamp but a reference is aged by the
  reference alone.
- Important Decisions and the templates' placeholder bullets are never
  stamped nor moved.
- The id of a line (``core.memory_bullets.item_id_for``) ignores stamps and
  the archived marker, so a line keeps its reference stamps through both
  edits.
- The archive section's placeholder ("- Nothing archived yet.") gives way to
  the first line that lands there; a MEMORY.md section emptied by moves stays
  empty.

``plan_sweep`` and ``apply_sweep`` are pure; ``sweep_memory`` does the I/O in
the night cycle's pattern (never raises, returns a status dict) and writes a
sidecar under ``data/memory_sweeps/`` naming every moved and stamped line
with its id. It replaced the 08-18 pass that dropped old threads outright.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

from core import memory_refs
from core.identity.service import identity_service
from core.memory_bullets import (
    _ARCHIVED_MARKER_RE,
    _TEMPLATE_PLACEHOLDER_BULLETS,
    _split_bullet,
    _stamp_time,
    item_id_for,
)

logger = logging.getLogger(__name__)
essential_logger = logging.getLogger("palli.essential")

MEMORY_SWEEP_DIR = Path("data/memory_sweeps")

DURABLE_SECTIONS: Tuple[str, ...] = ("## Durable Context", "## Useful References")
THREAD_SECTION = "## Active Threads"
ARCHIVE_PLACEHOLDER = "Nothing archived yet."
_ARCHIVED_DAY_RE = re.compile(r"\[archived (\d{4}-\d{2}-\d{2})\]\s*$")
_DAY_SECONDS = 86400.0


@dataclass(frozen=True)
class SweepMove:
    """One line the sweep moves: its ORIGINAL text, the ``## `` heading it
    sits under (the archive's same section), and its ``mcb_`` id."""

    text: str
    section: str
    item_id: str


@dataclass
class SweepPlan:
    """What one night's sweep does: the lines that move (file order), the
    ORIGINAL texts of the lines that get today's stamp, and the day."""

    day: str
    move: List[SweepMove] = field(default_factory=list)
    stamp: List[str] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not self.move and not self.stamp


def _day_of(now: float) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(now))


def _reference_time(refs: Any, item_id: str) -> Optional[float]:
    """``refs[item_id]["last_referenced_at"]`` as a float, or None when the
    shape is not the refs table's (core.memory_refs, ruling R31)."""
    entry = refs.get(item_id) if isinstance(refs, Mapping) else None
    value = entry.get("last_referenced_at") if isinstance(entry, Mapping) else None
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _is_bullet(line: str) -> bool:
    return line.startswith("- ")


def _is_placeholder(line: str) -> bool:
    return _split_bullet(line)[2] in _TEMPLATE_PLACEHOLDER_BULLETS


def _sections(lines: List[str]) -> List[Tuple[str, int, int]]:
    """Every ``## `` section as ``(heading, heading index, end index)``; the
    end is the next heading's index or the end of the document."""
    found: List[Tuple[str, int]] = [
        (line.rstrip(), idx) for idx, line in enumerate(lines) if line.startswith("## ")
    ]
    out: List[Tuple[str, int, int]] = []
    for pos, (heading, start) in enumerate(found):
        end = found[pos + 1][1] if pos + 1 < len(found) else len(lines)
        out.append((heading, start, end))
    return out


def _days_for(section: str, durable_days: int, thread_days: int) -> Optional[int]:
    """How many days a line under ``section`` may go unused before it moves;
    None for a section the sweep does not touch."""
    if section in DURABLE_SECTIONS:
        return int(durable_days)
    if section == THREAD_SECTION:
        return int(thread_days)
    return None


def plan_sweep(
    doc_text: str,
    *,
    refs: Any,
    now: float,
    durable_days: int,
    thread_days: int,
) -> SweepPlan:
    """Pure: read MEMORY.md's body and say what tonight's sweep does. ``refs``
    is the refs table's shape ({item_id: {"last_referenced_at", "count"}});
    anything else counts as no references. Lines outside Durable Context,
    Active Threads and Useful References are never touched."""
    now = float(now)
    plan = SweepPlan(day=_day_of(now))
    lines = doc_text.split("\n")
    for heading, start, end in _sections(lines):
        days = _days_for(heading, durable_days, thread_days)
        if days is None:
            continue
        for line in lines[start + 1:end]:
            if not _is_bullet(line) or _is_placeholder(line):
                continue
            stamp, _body, _key = _split_bullet(line)
            stamped_at = _stamp_time(stamp)
            item_id = item_id_for(line)
            referenced_at = _reference_time(refs, item_id)
            known = [t for t in (stamped_at, referenced_at) if t is not None]
            if not known:
                plan.stamp.append(line)
                continue
            if days > 0 and now - max(known) > days * _DAY_SECONDS:
                plan.move.append(SweepMove(text=line, section=heading, item_id=item_id))
    return plan


def stamped_line(line: str, day: str) -> str:
    """``- text`` -> ``- [day] text``; the id is unchanged (it ignores stamps)."""
    return f"- [{day}] {line[2:].lstrip()}"


def archived_line(line: str, day: str) -> str:
    """The line as it sits in the archive: the original text plus the day it
    moved; the id is unchanged (it ignores the marker)."""
    return f"{line.rstrip()} [archived {day}]"


def restored_line(line: str) -> str:
    """The archived line as it goes back into MEMORY.md: the marker removed."""
    return _ARCHIVED_MARKER_RE.sub("", line).rstrip()


def _remove_lines(lines: List[str], wanted: Dict[Tuple[str, str], int]) -> List[str]:
    """Drop, per section, as many copies of each planned line as were planned
    (a text repeated in one section is planned once per copy)."""
    out: List[str] = []
    section = ""
    for line in lines:
        if line.startswith("## "):
            section = line.rstrip()
        key = (section, line)
        if wanted.get(key, 0) > 0:
            wanted[key] -= 1
            continue
        out.append(line)
    return out


def _stamp_lines(lines: List[str], texts: List[str], day: str) -> List[str]:
    """Write today's stamp into every planned line (the swept sections only)."""
    wanted: Dict[Tuple[str, str], int] = {}
    stamped_in = tuple(DURABLE_SECTIONS) + (THREAD_SECTION,)
    for text in texts:
        for section in stamped_in:
            wanted[(section, text)] = wanted.get((section, text), 0) + 1
    out: List[str] = []
    section = ""
    for line in lines:
        if line.startswith("## "):
            section = line.rstrip()
        key = (section, line)
        if wanted.get(key, 0) > 0:
            wanted[key] -= 1
            out.append(stamped_line(line, day))
            continue
        out.append(line)
    return out


def _insert_into_section(
    lines: List[str],
    heading: str,
    new_lines: List[str],
    *,
    replace_placeholder: Optional[str] = None,
) -> List[str]:
    """Add ``new_lines`` to the end of ``heading``'s bullets. A section whose
    only content is the template placeholder ``replace_placeholder`` gives
    that line's place to them; a missing section is appended at the end."""
    if not new_lines:
        return lines
    for found, start, end in _sections(lines):
        if found != heading:
            continue
        body = lines[start + 1:end]
        placeholder_at = [
            i for i, line in enumerate(body)
            if replace_placeholder is not None and _is_bullet(line)
            and _split_bullet(line)[2] == replace_placeholder
        ]
        if placeholder_at:
            first = placeholder_at[0]
            body = [line for i, line in enumerate(body) if i not in placeholder_at]
            body = body[:first] + new_lines + body[first:]
        else:
            content_at = [i for i, line in enumerate(body) if line.strip()]
            if content_at:
                last = content_at[-1]
                body = body[:last + 1] + new_lines + body[last + 1:]
            else:
                body = [""] + new_lines + body
        return lines[:start + 1] + body + lines[end:]
    out = list(lines)
    final_newline = bool(out) and out[-1] == ""
    if final_newline:
        out.pop()
    while out and out[-1] == "":
        out.pop()
    out.extend(["", heading, ""] + new_lines)
    if final_newline:
        out.append("")
    return out


def _section_placeholder(lines: List[str], heading: str) -> Optional[str]:
    """The template placeholder bullet's text under ``heading``, if the
    section shows one; None otherwise."""
    for found, start, end in _sections(lines):
        if found != heading:
            continue
        for line in lines[start + 1:end]:
            if _is_bullet(line) and _is_placeholder(line):
                return _split_bullet(line)[2]
    return None


def _section_ids(lines: List[str], heading: str) -> set:
    """The ids of the bullets under ``heading`` (placeholders left out)."""
    ids: set = set()
    for found, start, end in _sections(lines):
        if found != heading:
            continue
        for line in lines[start + 1:end]:
            if _is_bullet(line) and not _is_placeholder(line):
                ids.add(item_id_for(line))
    return ids


def _remove_from_sections(lines: List[str], moves: List[SweepMove]) -> List[str]:
    wanted: Dict[Tuple[str, str], int] = {}
    for move in moves:
        key = (move.section, move.text)
        wanted[key] = wanted.get(key, 0) + 1
    return _remove_lines(lines, wanted)


def apply_sweep(doc_text: str, archive_text: str, plan: SweepPlan) -> Tuple[str, str]:
    """Pure: the two new bodies — MEMORY.md without the moved lines and with
    the stamped ones, MEMORY_ARCHIVE.md with the moved lines appended to their
    sections. A line whose id already sits in that archive section is not
    appended again (a night that wrote the archive and then failed on
    MEMORY.md is retried the next night). An empty plan gives both texts back
    byte for byte."""
    if plan.empty:
        return doc_text, archive_text
    memory_lines = doc_text.split("\n")
    memory_lines = _remove_from_sections(memory_lines, plan.move)
    memory_lines = _stamp_lines(memory_lines, plan.stamp, plan.day)

    archive_lines = archive_text.split("\n")
    by_section: Dict[str, List[str]] = {}
    already: Dict[str, set] = {}
    for move in plan.move:
        present = already.setdefault(move.section, _section_ids(archive_lines, move.section))
        if move.item_id in present:
            continue
        present.add(move.item_id)
        by_section.setdefault(move.section, []).append(archived_line(move.text, plan.day))
    for section, new_lines in by_section.items():
        archive_lines = _insert_into_section(
            archive_lines, section, new_lines, replace_placeholder=ARCHIVE_PLACEHOLDER,
        )
    return "\n".join(memory_lines), "\n".join(archive_lines)


def _int_setting(value: Any, default: int) -> int:
    """The setting as an int; the default when it is missing or not a number
    (0 is a real value here — it turns a class of move off)."""
    if value is None or isinstance(value, bool):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _archive_days() -> Tuple[int, int]:
    """``(memory.durable_archive_days, memory.thread_archive_days)`` from the
    settings snapshot; the schema's defaults (60, 45) when settings cannot be
    read or the block is missing (the core.memory_refs.memory_core_budget
    pattern)."""
    try:
        from core.settings_boot import settings_service

        memory = settings_service.snapshot().memory
    except Exception:
        memory = None
    return (
        _int_setting(getattr(memory, "durable_archive_days", None), 60),
        _int_setting(getattr(memory, "thread_archive_days", None), 45),
    )


def _write_sidecar(plan: SweepPlan, *, durable_days: int, thread_days: int) -> None:
    """``data/memory_sweeps/<day>-memory-sweep.md``: every moved and every
    stamped line with its ``mcb_`` id, so the owner can find the id
    ``palli_ctl memory restore`` takes. Appends, so a re-run the same day
    loses nothing. A failed write is logged, never fatal."""
    try:
        MEMORY_SWEEP_DIR.mkdir(parents=True, exist_ok=True)
        side = MEMORY_SWEEP_DIR / f"{plan.day}-memory-sweep.md"
        parts = [
            f"# Memory sweep on {plan.day} (durable {durable_days} d, threads {thread_days} d)",
            "",
            f"## Moved to MEMORY_ARCHIVE.md ({len(plan.move)})",
            "",
        ]
        parts.extend(f"- {m.item_id}  {m.section}  {m.text}" for m in plan.move)
        parts.extend(["", f"## Stamped [{plan.day}] ({len(plan.stamp)})", ""])
        parts.extend(f"- {item_id_for(text)}  {text}" for text in plan.stamp)
        with side.open("a", encoding="utf-8") as fh:
            fh.write("\n".join(parts) + "\n\n")
    except OSError as exc:
        logger.warning("memory_sweep: sidecar write failed: %s", exc)


async def _write_document(*, owner_id: int, doc: Any, doc_key: str, body: str, actor_id: str, reason: str) -> None:
    from core.identity.frontmatter import dump_frontmatter

    await identity_service.update_raw_document(
        user_id=owner_id,
        platform="owner",
        doc_key=doc_key,
        markdown=dump_frontmatter(dict(getattr(doc, "frontmatter", {}) or {}), body),
        actor_type="system",
        actor_id=actor_id,
        reason=reason,
        identity_owner_id=owner_id,
    )


async def sweep_memory(owner_id: int, now: Optional[float] = None) -> Dict[str, Any]:
    """The night cycle's memory pass. Materialise the archive (a first night
    has none), read the refs FRESH from the table (not the day's snapshot) and
    only THEN the documents — the refs read is the DB wait, and a reflection
    that lands during it must not be overwritten — plan, write each changed
    document once through the store with reason ``memory_sweep: moved=N
    stamped=M``, the ARCHIVE FIRST: the store writes the file and then awaits
    the brain for the version row, so a failure between the two writes must
    leave a moved line in both documents (harmless; the next night skips the
    archive copy), never in neither. Write the sidecar, then refresh the
    in-process refs snapshot (ruling R7: this is the one place it reloads).
    Never raises: ``{"moved": N, "stamped": M}``, ``{"skipped": why}`` or
    ``{"error": text}``."""
    now = float(time.time() if now is None else now)
    try:
        durable_days, thread_days = _archive_days()
        await identity_service.ensure_user_identity_bundle(
            user_id=owner_id, platform="owner", actor_id="memory_sweep",
            reason="memory_sweep: materialize the archive",
        )
        refs = await memory_refs.load_refs(owner_id)
        bundle = identity_service.compile_for_owner(owner_id=owner_id)
        owner_docs = getattr(bundle, "owner", None) or {}
        memory_doc = owner_docs.get("memory")
        if memory_doc is None:
            return {"skipped": "no_memory_doc"}
        archive_doc = owner_docs.get("memory_archive")
        if archive_doc is None:
            return {"skipped": "no_archive_doc"}
        plan = plan_sweep(
            memory_doc.content or "", refs=refs, now=now,
            durable_days=durable_days, thread_days=thread_days,
        )
        if not plan.empty:
            memory_body, archive_body = apply_sweep(memory_doc.content or "", archive_doc.content or "", plan)
            _write_sidecar(plan, durable_days=durable_days, thread_days=thread_days)
            reason = f"memory_sweep: moved={len(plan.move)} stamped={len(plan.stamp)}"
            if archive_body != (archive_doc.content or ""):
                await _write_document(
                    owner_id=owner_id, doc=archive_doc, doc_key="memory_archive", body=archive_body,
                    actor_id="memory_sweep", reason=reason,
                )
            if memory_body != (memory_doc.content or ""):
                await _write_document(
                    owner_id=owner_id, doc=memory_doc, doc_key="memory", body=memory_body,
                    actor_id="memory_sweep", reason=reason,
                )
            essential_logger.info(
                "memory_sweep: moved %d line(s) to MEMORY_ARCHIVE.md, stamped %d (durable %d d, threads %d d)",
                len(plan.move), len(plan.stamp), durable_days, thread_days,
            )
        await memory_refs.refresh(owner_id)
        return {"moved": len(plan.move), "stamped": len(plan.stamp)}
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.warning("memory_sweep: failed: %s", exc)
        return {"error": str(exc) or type(exc).__name__}


# ── the owner's levers (palli_ctl memory archived / restore, ruling R43) ─────


@dataclass(frozen=True)
class ArchivedLine:
    """One line of MEMORY_ARCHIVE.md: its id, its ``## `` heading, the day it
    moved ("" when the line carries no marker) and its text without the
    ``- `` and the marker (stamps kept)."""

    item_id: str
    section: str
    day: str
    text: str


def archived_lines(archive_text: str) -> List[ArchivedLine]:
    """Pure: every bullet of the archive in file order, placeholders left out."""
    out: List[ArchivedLine] = []
    lines = archive_text.split("\n")
    for heading, start, end in _sections(lines):
        for line in lines[start + 1:end]:
            if not _is_bullet(line) or _is_placeholder(line):
                continue
            match = _ARCHIVED_DAY_RE.search(line)
            out.append(ArchivedLine(
                item_id=item_id_for(line),
                section=heading,
                day=match.group(1) if match else "",
                text=restored_line(line)[2:].lstrip(),
            ))
    return out


async def list_archived(owner_id: int) -> List[ArchivedLine]:
    """The owner's archived lines; empty when no archive exists yet."""
    bundle = identity_service.compile_for_owner(owner_id=owner_id)
    archive_doc = (getattr(bundle, "owner", None) or {}).get("memory_archive")
    if archive_doc is None:
        return []
    return archived_lines(archive_doc.content or "")


@dataclass(frozen=True)
class RestorePlan:
    """What one restore does: the two new bodies, the section, the line as it
    goes back, and whether it was inserted (False when MEMORY.md already
    held a line with that id — the reflection re-added it — so only the
    archive copy goes)."""

    memory_body: str
    archive_body: str
    section: str
    line: str
    inserted: bool


def plan_restore(doc_text: str, archive_text: str, item_id: str) -> RestorePlan:
    """Pure: move the archived line with ``item_id`` back to the same section
    of MEMORY.md without its marker, and out of the archive. Raises
    LookupError when no archived line has that id."""
    archive_lines_ = archive_text.split("\n")
    found: Optional[Tuple[str, str]] = None
    for heading, start, end in _sections(archive_lines_):
        for line in archive_lines_[start + 1:end]:
            if _is_bullet(line) and not _is_placeholder(line) and item_id_for(line) == item_id:
                found = (heading, line)
                break
        if found:
            break
    if found is None:
        raise LookupError(item_id)
    section, line = found
    new_archive = _remove_lines(archive_lines_, {(section, line): 1})
    restored = restored_line(line)
    memory_lines = doc_text.split("\n")
    inserted = item_id not in _section_ids(memory_lines, section)
    if inserted:
        # an empty section shows the template's placeholder bullet; the
        # restored line takes its place (the reflection's appender does the same)
        memory_lines = _insert_into_section(
            memory_lines, section, [restored],
            replace_placeholder=_section_placeholder(memory_lines, section),
        )
    return RestorePlan(
        memory_body="\n".join(memory_lines), archive_body="\n".join(new_archive),
        section=section, line=restored, inserted=inserted,
    )


async def restore_line(owner_id: int, item_id: str) -> Dict[str, Any]:
    """``palli_ctl memory restore <mcb_id>``: the archived line goes back to
    the same section of MEMORY.md without its marker and leaves the archive;
    both writes through the store with reason ``memory_restore:<id>``. The
    restore counts as a use (a reference bump), or tonight's sweep would move
    the line straight back. Raises LookupError for an unknown id."""
    bundle = identity_service.compile_for_owner(owner_id=owner_id)
    owner_docs = getattr(bundle, "owner", None) or {}
    memory_doc = owner_docs.get("memory")
    archive_doc = owner_docs.get("memory_archive")
    if memory_doc is None or archive_doc is None:
        raise LookupError(item_id)
    plan = plan_restore(memory_doc.content or "", archive_doc.content or "", item_id)
    reason = f"memory_restore:{item_id}"
    if plan.inserted:
        await _write_document(
            owner_id=owner_id, doc=memory_doc, doc_key="memory", body=plan.memory_body,
            actor_id="memory_restore", reason=reason,
        )
    await _write_document(
        owner_id=owner_id, doc=archive_doc, doc_key="memory_archive", body=plan.archive_body,
        actor_id="memory_restore", reason=reason,
    )
    try:
        await memory_refs.bump(owner_id, [item_id])
    except Exception as exc:
        logger.warning("memory_restore: reference bump failed for %s: %s", item_id, exc)
    return {"restored": item_id, "section": plan.section, "line": plan.line, "inserted": plan.inserted}


__all__ = [
    "ARCHIVE_PLACEHOLDER",
    "ArchivedLine",
    "DURABLE_SECTIONS",
    "MEMORY_SWEEP_DIR",
    "RestorePlan",
    "SweepMove",
    "SweepPlan",
    "THREAD_SECTION",
    "apply_sweep",
    "archived_line",
    "archived_lines",
    "list_archived",
    "plan_restore",
    "plan_sweep",
    "restore_line",
    "restored_line",
    "stamped_line",
    "sweep_memory",
]
