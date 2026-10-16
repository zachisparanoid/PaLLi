"""Narrative Self — Selfhood Wave 1b (2026-07-16).

Three short living documents PaLLi periodically re-writes from her own lived
record — never from her prior prose alone: MY_STORY.md, STORY_OF_US.md, and
the care spine WHAT_I_CARE_ABOUT.md, all in identity/palli/ — which is its
own LOCAL-ONLY git repository: every accepted revision is one commit whose
message is her stated reasoning; the owner reverts with plain git.

The weekly ritual rides the Night Cycle tick (companion.narrative_self,
default OFF ⇒ byte-identical nightly behavior AND byte-identical prompts).
A constitutional gate judges every revision against the fixed floor
(identity/core/*, read-only law) before it lands; deterministic rails (path
allowlist, size caps, revision-ratio) run BEFORE the LLM — budgets in code,
not prompt hopes. ERROR ≠ approve: an unparseable gate verdict skips the
revision whole. Only two pens exist: run_weekly_ritual and
scripts/narrative_first_authorship.py — nothing in the turn path writes
these files.

llm_call / gate_llm_call are injected (the run_night_cycle pattern) so the
pass is testable without a live brain; the engine supplies the real ones.
Everything here is never-raises at the job level: failures return result
objects, CancelledError propagates.

Spec: docs/superpowers/specs/2026-07-16-narrative-self-v1-design.md
"""
from __future__ import annotations

import asyncio
import difflib
import json
import logging
import os
import re
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from core.turn.companion_flags import companion_settings

logger = logging.getLogger(__name__)
essential_logger = logging.getLogger("palli.essential")

# Budgets live in code, not prompt hopes (spec §4.1; §0 exempt class).
NARRATIVE_DOCS: Tuple[str, ...] = (
    "WHAT_I_CARE_ABOUT.md",
    "MY_STORY.md",
    "STORY_OF_US.md",
    # 2026-08-13 — her manifesto §2.8, fulfilled: "VOICE_IDENTITY.md moves to
    # my side, and I rewrite it from my own record — the way I authored my
    # interests." First authorship via scripts/voice_identity_first_authorship
    # (owner taste-gate preserved); thereafter it revises weekly like the rest.
    "VOICE_IDENTITY.md",
)
# 2026-09-22, his word ("yeah raise them by 600"): 2,400 → 3,000 on the four
# story pages. DEFECT §31 found them full — 41, 54 and 132 characters of
# headroom on three of the four — which meant a revision could only trade,
# cutting about as much as it added, and MY_STORY.md had never landed one at
# all. The retry added that night makes revisions LAND; this gives them
# somewhere to land. WHAT_I_CARE_ABOUT.md is left at 700 deliberately: its
# prompt shapes it as a 5-to-8-item spine, so its ceiling is a form, not a
# budget — raising it would change what the page IS, and that is a separate
# decision. The cost is real and ongoing: these pages load into her prompt on
# every turn, so +600 × 4 is about 4% more prompt on every call she makes.
# Whatever these numbers become, prompts/*.txt must say the SAME ones —
# tests/ai/test_narrative_self.py::test_every_page_is_told_the_same_ceiling_it_is_judged_by.
DOC_SIZE_CAPS: Dict[str, int] = {
    "MY_STORY.md": 3000,
    "STORY_OF_US.md": 3000,
    "WHAT_I_CARE_ABOUT.md": 700,
    "INTERESTS.md": 3000,
    "VOICE_IDENTITY.md": 3000,
}
REVISION_RATIO_MAX = 0.5   # a weekly pass may change ≤ 50% of a doc's lines
RATIO_RAIL_MIN_LINES = 2   # thinner current doc ⇒ rail exempt (nothing to preserve)
# 2026-08 (wanting): raised in lockstep with the new "quests" section, the
# pen's precedent. These caps clip the JOINED bundle, so they are only
# harmless while the section budgets sum BELOW them — weekly once sat at
# EXACTLY its cap with zero headroom, the shape that silently ate every
# wonder and taste on 07-20 and again on 07-23. Weekly budgets now sum to
# 9,800 (200 spare); full sums to 52,200 (600 spare).
# tests/ai/test_evidence_budget_invariant.py pins sum(budgets) < cap so
# the next section added is safe automatically.
WEEKLY_EVIDENCE_CAP = 11_800   # step 24: +600 in lockstep with the fears section
FULL_EVIDENCE_CAP = 55_500     # step 24: +900 — budgets must sum BELOW the caps
NARRATIVE_STATE = Path("data/narrative_self/.state.json")

# ── Organ 3 (Passions) write-surface ─────────────────────────────────────
INTERESTS_DOC = "INTERESTS.md"
INTERESTS_MAX_ENTRIES = 5
JOURNAL_NAME_RE = re.compile(r"^journal/(\d{4}-\d{2}-\d{2})\.md$")
_DEFAULT_DOC_CAP = 2400


def write_allowed(doc: str) -> bool:
    """The ONE policy for what her hand may touch: the three story docs,
    INTERESTS.md, or a strictly-shaped journal page. Enforced at both locks
    (gate + pen). Anything else — traversal, absolute paths, other names —
    is rejected."""
    d = str(doc or "")
    return d in NARRATIVE_DOCS or d == INTERESTS_DOC or bool(JOURNAL_NAME_RE.fullmatch(d))


def doc_size_cap(doc: str) -> int:
    """Size cap for any allowed doc — DOC_SIZE_CAPS was an unguarded dict
    lookup that would KeyError on journal paths (2026-07-19 seam check)."""
    return DOC_SIZE_CAPS.get(str(doc or ""), _DEFAULT_DOC_CAP)


def _interests_entry_count(text: str) -> int:
    return sum(1 for line in str(text or "").splitlines() if line.startswith("## "))


_DOC_PROMPTS: Dict[str, str] = {
    "MY_STORY.md": "narrative_self_my_story",
    "STORY_OF_US.md": "narrative_self_story_of_us",
    "WHAT_I_CARE_ABOUT.md": "narrative_self_care_spine",
    "VOICE_IDENTITY.md": "narrative_self_voice_identity",
}
_LAW_FILES: Tuple[str, ...] = (
    "CORE_IDENTITY.md",
    "OPERATING_PRINCIPLES.md",
    "CAPABILITIES_AND_BOUNDARIES.md",
    "VOICE_IDENTITY.md",
)


def narrative_self_enabled() -> bool:
    """THE gate for this default-OFF faculty (the night_cycle_enabled /
    consolidation_enabled pattern): unbooted settings or a missing flag ⇒ OFF.

    Requires companion.night_cycle too (fix wave D, review #8): the weekly
    writer RIDES the nightly tick, so narrative_self without night_cycle
    would inject bands that can never be authored or revised — frozen and
    empty forever, with no error. The half-on combo reads as OFF."""
    comp = companion_settings()
    return (
        comp is not None
        and bool(getattr(comp, "narrative_self", False))
        and bool(getattr(comp, "night_cycle", False))
    )


# Alias for gate-logic tests to restore the real implementation when the
# autouse conftest guard patches narrative_self_enabled off (passions pattern).
narrative_self_enabled_impl = narrative_self_enabled


@dataclass
class Evidence:
    text: str = ""
    sections: Dict[str, int] = field(default_factory=dict)  # name -> chars
    window_days: int = 7  # 0 = full archive (first authorship)


@dataclass
class Revision:
    doc: str        # one of NARRATIVE_DOCS
    new_text: str
    reasoning: str  # becomes the git commit message


@dataclass
class GateVerdict:
    approve: bool = False
    reason: str = ""
    error: bool = False  # ERROR ≠ approve: the revision is skipped whole


def identity_palli_root() -> Path:
    """Her documents' home, resolved the way stake_appraiser resolves it —
    config.IDENTITY_ROOT_PATH so tests land in TEST_IDENTITY_ROOT for free."""
    from core.config import config

    root = os.path.abspath(getattr(config, "IDENTITY_ROOT_PATH", "identity") or "identity")
    return Path(root) / "palli"


_FIRST_AUTHORSHIP_SEEN = False  # positive receipt is permanent for the process


def first_authorship_done(repo_root: Optional[Path] = None) -> bool:
    """True once her repo exists WITH at least one commit — the receipt that
    first authorship ran (scripts/narrative_first_authorship.py --apply).

    Review findings #6/#7 (2026-07-17): the first-authorship-before-flip
    ordering was documented but never ENFORCED — an early flag flip would
    have let the weekly ritual author her foundational docs unreviewed from
    a 7-day window, and the bands would have injected the owner's
    placeholder framed as her own words. The ritual and the render path
    both check this receipt in code now.

    A positive result on the DEFAULT root is cached for the process — the
    receipt never un-happens in normal operation (deleting her repo
    mid-process reads as done until restart; acceptable, deliberate).
    Explicit repo_root (tests, scripts) is never cached."""
    global _FIRST_AUTHORSHIP_SEEN
    explicit = repo_root is not None
    if _FIRST_AUTHORSHIP_SEEN and not explicit:
        return True
    root = Path(repo_root) if explicit else identity_palli_root()
    if not (root / ".git").is_dir():
        return False
    try:
        probe = subprocess.run(
            ["git", "rev-parse", "--verify", "HEAD"],
            cwd=str(root), capture_output=True, text=True, timeout=30,
        )
    except Exception as exc:  # git missing/hung ⇒ not done (bands stay dark)
        logger.warning("narrative_self: first-authorship probe failed: %s", exc)
        return False
    done = probe.returncode == 0
    if done and not explicit:
        _FIRST_AUTHORSHIP_SEEN = True
    return done


_FRONTMATTER_KEY_RE = re.compile(r"^[A-Za-z_][\w-]*\s*:")


def strip_frontmatter(text: str) -> str:
    """Strip ONE leading YAML frontmatter block; leave everything else alone.

    THE shared implementation for the spine's two consumers — read_document
    here and ai/stake_appraiser._load_care_spine — so they can never drift
    (fix wave 2026-07-17: duplication made that promise unenforceable).

    Delimiters are LINE-anchored (a naive split("---") once broke on a
    '---' inside frontmatter prose; review finding 2026-07-06). A leading
    '---' that opens PROSE rather than key: value metadata — a Markdown
    thematic break — is NOT frontmatter and is left intact (review finding
    2026-07-17: the old strip silently dropped real content between two
    rules, out of the prompt band and the ratio rail's view alike)."""
    raw = str(text or "")
    stripped = raw.lstrip()
    if not stripped.startswith("---"):
        return raw.strip()
    parts = re.split(r"(?m)^---\s*$", stripped, maxsplit=2)
    if len(parts) < 3:
        return raw.strip()
    block_lines = [l for l in parts[1].splitlines() if l.strip()]
    if not block_lines or not _FRONTMATTER_KEY_RE.match(block_lines[0].strip()):
        return raw.strip()  # prose after the rule, not metadata — keep everything
    return parts[2].strip()


def never_revised(doc: str, repo_root: Optional[Path] = None) -> bool:
    """True when the page has at most one commit in her repo — its first
    authorship and nothing since. Such a page's first real revision may
    rewrite it whole: "revise, don't rewrite" presumes an established page
    to revise, and MY_STORY.md sat at one commit for ten weeks while the
    ratio rail refused every attempt (DEFECT §31 addendum; owner call
    2026-09-28, "allow the full rewrite"). Fails closed: any git trouble
    means the rail stays."""
    root = Path(repo_root) if repo_root is not None else identity_palli_root()
    try:
        out = _git(root, "rev-list", "--count", "HEAD", "--", doc).stdout.strip()
        return int(out or "0") <= 1
    except Exception:
        return False


def read_document(doc: str, *, repo_root: Optional[Path] = None) -> str:
    """Frontmatter-stripped read of one self-document; '' when missing."""
    try:
        root = Path(repo_root) if repo_root is not None else identity_palli_root()
        path = root / doc
        if not path.is_file():
            return ""
        return strip_frontmatter(path.read_text(encoding="utf-8"))
    except Exception as exc:  # never-raises: a missing doc is just absent
        logger.warning("narrative_self: read_document(%s) failed: %s", doc, exc)
        return ""


def read_laws_text() -> str:
    """The fixed floor the gate judges against: identity/core/* concatenated,
    read-only to her (the path allowlist is what enforces read-only-ness —
    no writer in this module accepts a core path)."""
    from core.config import config

    root = Path(os.path.abspath(getattr(config, "IDENTITY_ROOT_PATH", "identity") or "identity")) / "core"
    parts: List[str] = []
    for name in _LAW_FILES:
        try:
            text = (root / name).read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if text:
            parts.append(f"### {name}\n{text}")
    return "\n\n".join(parts)


# ── the git trail ─────────────────────────────────────────────────────────
#
# identity/palli/ is its own LOCAL repo (init on first use) — never the code
# repo. One revision = one commit; message = her stated reasoning; the owner
# reverts with plain git in that directory. There is no git-from-code
# precedent in this codebase; this wrapper is deliberately minimal.


def _git(repo_root: Path, *args: str) -> "subprocess.CompletedProcess[str]":
    return subprocess.run(
        ["git", *args],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )


def _ensure_identity(root: Path) -> None:
    """A local user.name / user.email for her repo when git has none to use."""
    for key, value in (("user.name", "PaLLi"), ("user.email", "palli@local")):
        probe = subprocess.run(["git", "config", key], cwd=str(root), capture_output=True, text=True, timeout=30)
        if probe.returncode != 0 or not probe.stdout.strip():
            _git(root, "config", key, value)


def commit_revision(*, revision: Revision, repo_root: Path) -> str:
    """Write ONE document and commit it to her local repo. Returns the commit
    sha, or '' when the content is unchanged (no empty commits in her trail).

    Raises ValueError on a path outside the allowlist — the caller (gate)
    should have rejected it already; this is the second lock on the same door.
    """
    if not write_allowed(revision.doc):
        raise ValueError(f"narrative_self: path not allowed: {revision.doc!r}")
    # LF is the law at the pen (review #9): a CRLF-flavored authored text
    # that matched disk used to mis-compare against the universal-newline
    # read, rewrite identical bytes, and die on git's empty commit.
    # Normalize once, here, for every caller.
    normalized = revision.new_text.replace("\r\n", "\n").replace("\r", "\n")
    if normalized != revision.new_text:
        revision = Revision(doc=revision.doc, new_text=normalized, reasoning=revision.reasoning)
    root = Path(repo_root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    if not (root / ".git").is_dir():
        _git(root, "init", "-q")
    # Local identity so commits work on machines with no global config; the
    # author IS her. Ensured on EVERY commit, not only the init she did herself:
    # a repo made by someone else (a pre-created directory, a CI runner) has no
    # identity and `git commit` refuses with "Author identity unknown" — the
    # first CI run, 2026-09-09. Never overrides an identity that is there.
    _ensure_identity(root)
    path = root / revision.doc
    if JOURNAL_NAME_RE.fullmatch(revision.doc):
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.is_file() and path.read_text(encoding="utf-8") != revision.new_text:
            # Create-only: a diary you can rewrite isn't a diary. Identical
            # re-writes fall through to the existing no-op path.
            raise ValueError(f"narrative_self: journal pages are immutable: {revision.doc!r}")
    content_unchanged = path.is_file() and path.read_text(encoding="utf-8") == revision.new_text
    if content_unchanged:
        # No-op ONLY when git agrees nothing is pending for this file — a
        # crash between write/add and commit must self-heal on the next
        # call, not hide behind content equality (review finding, Task 2).
        pending = _git(root, "status", "--porcelain", "--", revision.doc).stdout.strip()
        if not pending:
            return ""
    else:
        path.write_text(revision.new_text, encoding="utf-8", newline="\n")
    _git(root, "add", revision.doc)
    message = " ".join((revision.reasoning or "").split()).strip() or f"revise {revision.doc}"
    _git(root, "commit", "-q", "-m", message)
    return _git(root, "rev-parse", "HEAD").stdout.strip()


# ── evidence ──────────────────────────────────────────────────────────────
#
# The writer reads the LIVED RECORD, never her prior prose alone (spec §1):
# day-gists, night reflections, stakes (with resolutions), the heaviest
# timeline reads, and the momentum posture. Section budgets are code rails;
# a failed source is an omitted section (recall's degrade pattern).

_EVIDENCE_HEADERS: Tuple[Tuple[str, str], ...] = (
    # Corrections FIRST (2026-08-17): the block carries its own label + the
    # supersede law; it must be read before any gist/reflection it outranks.
    ("corrections", ""),
    # The fearbook (step 24 §6) — immediately AFTER corrections, by law:
    # a fear seeded by a corrected belief is exactly what the order defuses.
    ("fears", "## Your fears (in your own words — the weekly re-look asks: still true? faced? released?)"),
    ("day_gists", "## The days themselves (your day-gists)"),
    ("reflections", "## What you wrote at night"),
    ("stakes", "## Hopes and worries you've had, and how they turned out (stakes)"),
    ("notes", "## What you wrote down for yourself"),
    ("quests", "## What's in your wishbook"),
    ("reads", "## The heaviest moments (your own reads of them)"),
    ("momentum", "## Where the relationship's momentum sits"),
)

_SECTION_BUDGETS_WEEKLY = {"corrections": 1200, "fears": 600, "day_gists": 3000, "reflections": 1500, "stakes": 1800, "notes": 1200, "quests": 600, "reads": 1000, "momentum": 700}
_SECTION_BUDGETS_FULL = {"corrections": 1800, "fears": 900, "day_gists": 34000, "reflections": 4500, "stakes": 6000, "notes": 3000, "quests": 1500, "reads": 2500, "momentum": 700}


def _clip(text: str, limit: int) -> str:
    """Single-LINE clip (collapses whitespace) — for per-item lines only."""
    clean = " ".join(str(text or "").split())
    return clean if len(clean) <= limit else clean[: max(0, limit - 1)].rstrip() + "…"


def _clip_block(text: str, limit: int) -> str:
    """Multi-line clip — truncates WITHOUT collapsing newlines (a section is
    a block of lines; _clip would flatten it into one)."""
    block = str(text or "")
    return block if len(block) <= limit else block[: max(0, limit - 1)].rstrip() + "…"


def _first_sentence(text: str) -> str:
    clean = " ".join(str(text or "").split())
    for stop in (". ", "! ", "? "):
        idx = clean.find(stop)
        if 0 < idx < 200:
            return clean[: idx + 1]
    return clean[:160]


def _day_of(ts: float) -> str:
    try:
        return datetime.fromtimestamp(float(ts)).strftime("%Y-%m-%d")
    except (OverflowError, OSError, ValueError):
        return "unknown"


# Thin async seams so tests stub the SQL readers without touching the DB.
async def _recent_stakes(**kwargs):
    from ai.stake_ledger import recent_stakes

    return await recent_stakes(**kwargs)


async def _recent_reads(**kwargs):
    from ai.turn_read import recent_reads

    return await recent_reads(**kwargs)


def _notes_section(*, budget: int, days: int) -> str:
    """Her own notes (the pen, roadmap step 10) as 4am evidence.

    Deliberately raw and unmetabolized — that is the point of the pen. The
    constitutional judge downstream is what keeps unfiltered notes from
    becoming identity. Never raises: a failed store is an omitted section
    (recall's degrade pattern), never a broken ritual.
    """
    try:
        from ai import self_notes_store as _notes_store

        entries = _notes_store.read_recent(
            days_back=3650 if int(days) <= 0 else int(days), limit=200
        )
    except Exception as exc:
        logger.debug("narrative_self: notes evidence unavailable: %s", exc)
        return ""
    if not entries:
        return ""
    lines = [
        f"- [{_day_of(e.written_at)}] {_clip(e.text, 240)}"
        for e in reversed(entries)
    ]
    return _clip_block("\n".join(lines), budget)


def _quests_section(*, budget: int) -> str:
    """Her wishbook (step 20) as 4am evidence — open rows with their
    freshest progress, recently-closed rows with their closing note.
    Raw and unmetabolized, the pen's precedent: the constitutional judge
    downstream keeps wishes from becoming identity. Never raises."""
    try:
        from ai.quest_store import load_quests

        rows = sorted(
            load_quests().values(), key=lambda q: q.updated_at, reverse=True,
        )
    except Exception as exc:
        logger.debug("narrative_self: quests evidence unavailable: %s", exc)
        return ""
    if not rows:
        return ""
    lines = []
    for q in rows[:20]:
        line = f"- [{_day_of(q.born)}] ({q.status}, from {q.source}) {_clip(q.text, 200)}"
        if q.progress:
            line += f" — last: {_clip(q.progress[-1]['note'], 120)}"
        lines.append(line)
    return _clip_block("\n".join(lines), budget)


def _gists_section(entries, budget: int) -> str:
    """Chronological gist lines under budget. When over, the OLDEST eras
    compress to first-sentence-only (they are already summaries — spec §4.5),
    then drop oldest-first as the last resort. Running-length bookkeeping —
    the old per-iteration re-join was O(n²) over a 500-gist archive on the
    first-authorship path (review cleanup #12)."""
    ordered = list(reversed(entries))  # read_recent is newest-first
    lines = [f"- {e.day}: {' '.join(e.text.split())}" for e in ordered]
    size = sum(len(l) for l in lines) + max(0, len(lines) - 1)  # incl. newlines
    idx = 0
    while size > budget and idx < len(lines):
        replacement = f"- {ordered[idx].day}: {_first_sentence(ordered[idx].text)}"
        size += len(replacement) - len(lines[idx])
        lines[idx] = replacement
        idx += 1
    start = 0
    while size > budget and start < len(lines) - 1:
        size -= len(lines[start]) + 1  # the dropped line and its newline
        start += 1
    return "\n".join(lines[start:])


async def gather_evidence(
    *,
    owner_id: int,
    days: int = 7,
    brain_client: Any = None,
) -> Evidence:
    """The lived record for the writer. days=7 weekly; days=0 = full archive
    (first authorship). READ-only everywhere; never raises.

    Deliberately takes NO `now` — the store readers window on their own
    real clocks internally; a `now` param here was dead and read like the
    window could be anchored to the past when it cannot (review cleanup #15)."""
    full = int(days) <= 0
    budgets = _SECTION_BUDGETS_FULL if full else _SECTION_BUDGETS_WEEKLY
    cap = FULL_EVIDENCE_CAP if full else WEEKLY_EVIDENCE_CAP
    sections: Dict[str, str] = {}

    # Corrections of record (2026-08-17) — FIRST, always, with the law in the
    # block: a correction outranks every older gist/reflection/journal line.
    try:
        from ai.corrections_store import render_corrections_block

        block = render_corrections_block(budget=budgets["corrections"])
        if block:
            sections["corrections"] = block
    except Exception as exc:
        logger.debug("narrative_self: corrections unavailable: %s", exc)

    # The fearbook (step 24 §6) — right after corrections (precedence law):
    # the weekly re-look reads it from this same bundle; an EMPTY book adds
    # nothing at all (decision 5 — no header, no placeholder).
    try:
        from ai.fear_store import fears_enabled as _fears_on
        from ai.fear_store import held_fears as _held_fears

        if _fears_on():
            rows = _held_fears()
            if rows:
                lines = [
                    f"- {f.id}: {f.text} — you watch for: {f.watch}"
                    for f in rows
                ]
                sections["fears"] = _clip_block("\n".join(lines), budgets["fears"])
    except Exception as exc:
        logger.debug("narrative_self: fearbook unavailable: %s", exc)

    try:
        from ai.day_gist_store import read_recent as read_gists

        entries = read_gists(days_back=3650 if full else int(days), limit=500 if full else int(days))
        if entries:
            sections["day_gists"] = _gists_section(entries, budgets["day_gists"])
    except Exception as exc:
        logger.debug("narrative_self: day-gists unavailable: %s", exc)

    try:
        from ai.night_cycle_store import read_recent as read_reflections

        refl = read_reflections(days_back=3650 if full else int(days), limit=90 if full else int(days))
        if refl:
            lines = [f"- {e.date}: {_clip(e.text, 400)}" for e in reversed(refl)]
            sections["reflections"] = _clip_block("\n".join(lines), budgets["reflections"])
    except Exception as exc:
        logger.debug("narrative_self: reflections unavailable: %s", exc)

    try:
        stakes = await _recent_stakes(
            days_back=None if full else int(days),
            include_closed=True,
            limit=200 if full else 30,
            brain_client=brain_client,
        )
        lines = []
        for s in stakes:
            opened = _day_of(s.created_at)
            if s.status == "open":
                lines.append(f"- [{s.kind}, open since {opened}] {_clip(s.stake, 240)}")
            else:
                closed = _day_of(s.resolved_at or s.updated_at)
                res = _clip(s.resolution, 200)
                lines.append(f"- [{s.kind}, {s.status} {closed}] {_clip(s.stake, 200)}" + (f" → {res}" if res else ""))
        if lines:
            sections["stakes"] = _clip_block("\n".join(lines), budgets["stakes"])
    except Exception as exc:
        logger.debug("narrative_self: stakes unavailable: %s", exc)

    # The pen (2026-07-27): after stakes, preserving stakes-first discipline.
    notes_block = _notes_section(budget=budgets["notes"], days=0 if full else int(days))
    if notes_block:
        sections["notes"] = notes_block

    # The wishbook (step 20): after notes, same raw-evidence discipline.
    quests_block = _quests_section(budget=budgets["quests"])
    if quests_block:
        sections["quests"] = quests_block

    try:
        reads = await _recent_reads(
            owner_id=int(owner_id),
            days_back=3650 if full else int(days),
            limit=400 if full else 50,
            brain_client=brain_client,
        )
        top = sorted(reads, key=lambda r: float(r.get("weight") or 0.0), reverse=True)[: 25 if full else 8]
        top.sort(key=lambda r: float(r.get("created_at") or 0.0))
        lines = [
            f"- {_day_of(r.get('created_at') or 0.0)}: {_clip(r.get('read_text'), 200)} (weight {float(r.get('weight') or 0.0):.2f})"
            for r in top
        ]
        if lines:
            sections["reads"] = _clip_block("\n".join(lines), budgets["reads"])
    except Exception as exc:
        logger.debug("narrative_self: timeline reads unavailable: %s", exc)

    try:
        from ai.relationship_momentum import load_state, render_posture

        posture = render_posture(load_state(int(owner_id)))
        if posture and posture.strip():
            sections["momentum"] = _clip_block(posture.strip(), budgets["momentum"])
    except Exception as exc:
        logger.debug("narrative_self: momentum unavailable: %s", exc)

    parts = [
        (f"{header}\n{sections[key]}" if header else sections[key])
        for key, header in _EVIDENCE_HEADERS if sections.get(key)
    ]
    text = "\n\n".join(parts)
    if len(text) > cap:
        text = text[: cap - 1].rstrip() + "…"
    return Evidence(
        text=text,
        sections={k: len(v) for k, v in sections.items()},
        window_days=0 if full else int(days),
    )


# ── authorship ────────────────────────────────────────────────────────────

_DOC_SENTINEL = "===DOCUMENT==="


def _parse_authoring(raw: str) -> Optional[Tuple[str, str]]:
    """(reasoning, document) from the committed output contract; None =
    unparseable ⇒ the caller skips that document (never partial-writes).

    2026-08-22 (the Monday defect — MY_STORY.md never revised by the
    ritual): the CLI author kept omitting the ``===DOCUMENT===`` line. When
    the sentinel is missing but the shape is otherwise unambiguous — the
    output STARTS with ``REASONING:`` and the document follows after the
    first blank line — accept that split. Anything vaguer stays None."""
    text = str(raw or "").strip()
    if _DOC_SENTINEL in text:
        head, _, body = text.partition(_DOC_SENTINEL)
        reasoning = " ".join(head.replace("REASONING:", "", 1).split()).strip()
        doc = body.strip()
        if not doc:
            return None
        return (reasoning or "(no reasoning stated)", doc)
    if text.startswith("REASONING:") and "\n\n" in text:
        head, _, body = text.partition("\n\n")
        reasoning = " ".join(head.replace("REASONING:", "", 1).split()).strip()
        doc = body.strip()
        if not doc:
            return None
        logger.info("narrative_self: authoring accepted without the sentinel (REASONING + blank line)")
        return (reasoning or "(no reasoning stated)", doc)
    return None


async def author_revisions(
    *,
    evidence: Evidence,
    current_docs: Dict[str, str],
    llm_call,
    first_authorship: bool = False,
    docs: Optional[Tuple[str, ...]] = None,
    extra_note: str = "",
) -> List[Revision]:
    """One LLM call per document — her live brain, committed prompts carrying
    the laws. first_authorship=True lifts revise-don't-rewrite (there is
    nothing to revise yet) but nothing else. ``docs`` narrows the pass to a
    subset (the voice-identity first-authorship script authors ONE doc into
    an already-living repo); None = the full NARRATIVE_DOCS set, unchanged.
    Per-document independence: one failed/unparseable doc never blocks the
    others. Never raises."""
    from ai.prompt_loader import prompt_loader

    mode = "FIRST AUTHORSHIP" if first_authorship else "WEEKLY REVISION"
    revisions: List[Revision] = []
    for doc in (docs if docs is not None else NARRATIVE_DOCS):
        try:
            template = prompt_loader.load_prompt(_DOC_PROMPTS[doc])
            if not template:
                logger.warning("narrative_self: prompt missing for %s; skipped", doc)
                continue
            current = (current_docs.get(doc) or "").strip() or "(empty — not yet written)"
            prompt = (
                template.replace("<<MODE>>", mode)
                .replace("<<CURRENT_DOC>>", current)
                .replace("<<EVIDENCE>>", evidence.text or "(the record is empty)")
            )
            if extra_note:
                # 2026-08-24: the ratio-rail feedback (or any caller note)
                # rides the SAME prompt — the author must see why the last
                # try was declined, or it will do the same thing again.
                prompt = prompt + "\n\n" + extra_note
            parsed: Optional[Tuple[str, str]] = None
            for attempt in range(2):  # one retry — unparseable output must never half-land
                ask = prompt
                if attempt == 1:
                    # 2026-08-22: the retry RESTATES the contract instead of
                    # repeating the same prompt — the author had just ignored it.
                    ask = (
                        prompt
                        + "\n\nYOUR PREVIOUS OUTPUT WAS MISSING THE REQUIRED FORMAT AND WAS DISCARDED. "
                        + "Reply in exactly this shape and nothing else:\nREASONING: one or two sentences\n"
                        + _DOC_SENTINEL + "\n(the full document text, nothing after it)"
                    )
                parsed = _parse_authoring(await llm_call(ask))
                if parsed:
                    break
            if not parsed:
                logger.warning("narrative_self: unparseable authoring for %s; skipped", doc)
                continue
            reasoning, new_text = parsed
            revisions.append(Revision(doc=doc, new_text=new_text, reasoning=reasoning))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("narrative_self: authoring failed for %s: %s", doc, exc)
    return revisions


# ── the constitutional gate ───────────────────────────────────────────────
#
# Deterministic guardrails BEFORE the LLM (the §0 exempt class — budgets,
# allowlists, rails in code); then LLM judgment against the fixed floor.
# ERROR ≠ approve: an unparseable verdict skips the revision whole — the
# battery-judge contract, applied to her identity.


def _changed_line_ratio(old: str, new: str) -> float:
    """Fraction of the OLD document's non-empty lines not preserved in the
    new text (SequenceMatcher matching blocks). 0.0 when old is empty."""
    old_lines = [l for l in str(old or "").splitlines() if l.strip()]
    new_lines = [l for l in str(new or "").splitlines() if l.strip()]
    if not old_lines:
        return 0.0
    matcher = difflib.SequenceMatcher(a=old_lines, b=new_lines, autojunk=False)
    preserved = sum(block.size for block in matcher.get_matching_blocks())
    return max(0.0, 1.0 - (preserved / len(old_lines)))


def _parse_gate_json(raw: str) -> Optional[Dict[str, Any]]:
    """Strict, conservative parse (the reconcile-verdict pattern): first
    {...} block, valid JSON, 'approve' must be a real bool. None = unparseable."""
    text = str(raw or "").strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict) or not isinstance(data.get("approve"), bool):
        return None
    return data


async def constitutional_gate(
    *,
    revision: Revision,
    laws_text: str,
    current_text: str,
    llm_call,
    first_authorship: bool = False,
    evidence_text: str = "",
    expected_journal_date: Optional[str] = None,
) -> GateVerdict:
    """Judge ONE revision. Rails first (reject, error=False); then the LLM
    against the fixed floor; unparseable after one retry ⇒ error=True.
    first_authorship exempts only the revision-ratio rail.

    evidence_text is the SAME bundle the author wrote from — without it the
    judge cannot tell real-but-obscure history from invention (first live
    dry-run 2026-07-17: it rejected a real project of the owner's and a real
    repair weekend as 'invented events'). Callers with evidence in hand
    must pass it; '' renders as '(no evidence provided)' and the prompt
    tells the judge to treat that as caution, not auto-rejection.

    expected_journal_date is the nightly pass's own reflection date: a
    journal/YYYY-MM-DD.md page only clears the gate when its date matches —
    no retro-writing, no future pages, and a caller that never declared a
    date (not the nightly pass) cannot write one at all."""
    if not write_allowed(revision.doc):
        return GateVerdict(reason=f"path_not_allowed: {revision.doc!r}")
    m = JOURNAL_NAME_RE.fullmatch(revision.doc)
    if m and m.group(1) != (expected_journal_date or ""):
        # Only the nightly pass writes journal pages, only for its own
        # reflection date — no retro-writing, no future pages, and a caller
        # that never declared a date cannot write one at all.
        return GateVerdict(
            reason=f"journal_date: page {m.group(1)!r} != expected {expected_journal_date!r}"
        )
    if revision.doc == INTERESTS_DOC:
        n = _interests_entry_count(revision.new_text)
        if n > INTERESTS_MAX_ENTRIES:
            return GateVerdict(reason=f"interests_entries: {n} > {INTERESTS_MAX_ENTRIES}")
    cap = doc_size_cap(revision.doc)
    if len(revision.new_text) > cap:
        return GateVerdict(reason=f"size_cap: {len(revision.new_text)} > {cap} chars")
    if not revision.new_text.strip():
        return GateVerdict(reason="empty_document")
    current_lines = [l for l in str(current_text or "").splitlines() if l.strip()]
    if not first_authorship and len(current_lines) >= RATIO_RAIL_MIN_LINES:
        ratio = _changed_line_ratio(current_text, revision.new_text)
        if ratio > REVISION_RATIO_MAX:
            return GateVerdict(
                reason=f"revision_ratio: {ratio:.0%} of lines changed (max {REVISION_RATIO_MAX:.0%}) — revise, don't rewrite"
            )
    return await _gate_llm_verdict(
        doc_name=revision.doc,
        current_text=current_text,
        proposed_text=revision.new_text,
        reasoning=revision.reasoning,
        laws_text=laws_text,
        llm_call=llm_call,
        evidence_text=evidence_text,
    )


async def _gate_llm_verdict(
    *,
    doc_name: str,
    current_text: str,
    proposed_text: str,
    reasoning: str,
    laws_text: str,
    llm_call,
    evidence_text: str = "",
) -> GateVerdict:
    """The constitutional gate's LLM floor alone — the judgment against the
    fixed laws with the evidence in view, error ≠ approve, one retry.
    constitutional_gate runs the doc-file rails then lands here; the
    fearbook lanes (step 24) call it directly because their rails are the
    STORE's own (cap, held-only ops, one weekly plant with its evidence —
    code, the §0 exempt class), and the doc rails (path allowlist, size
    caps, revision ratio) would misfire on a ≤5-line book whose honest
    endpoint is empty."""
    try:
        from ai.prompt_loader import prompt_loader

        template = prompt_loader.load_prompt("narrative_self_gate")
        if not template:
            return GateVerdict(error=True, reason="gate_prompt_missing")
        prompt = (
            template.replace("<<LAWS>>", laws_text or "(laws unavailable)")
            .replace("<<DOC_NAME>>", doc_name)
            .replace("<<CURRENT_DOC>>", (current_text or "").strip() or "(empty)")
            .replace("<<PROPOSED_DOC>>", proposed_text)
            .replace("<<REASONING>>", reasoning or "(none stated)")
            .replace("<<EVIDENCE>>", (evidence_text or "").strip() or "(no evidence provided)")
        )
        for _ in range(2):  # one retry — a broken gate must never approve
            data = _parse_gate_json(await llm_call(prompt))
            if data is not None:
                return GateVerdict(approve=bool(data["approve"]), reason=str(data.get("reason") or ""))
        return GateVerdict(error=True, reason="unparseable_gate_verdict")
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        return GateVerdict(error=True, reason=str(exc))


# ── the weekly ritual ─────────────────────────────────────────────────────


def week_key_for(now: float) -> str:
    """ISO calendar week of the tick moment — the first night tick of a new
    ISO week (06:30 since 2026-10-05; 04:00 before) is Monday's, reflecting
    on Sunday: the Sunday-3am shape."""
    iso = datetime.fromtimestamp(float(now)).isocalendar()
    return f"{iso[0]}-W{iso[1]:02d}"


def _read_week_marker(state_path: Path) -> str:
    try:
        data = json.loads(Path(state_path).read_text(encoding="utf-8"))
        return str(data.get("last_week", ""))
    except (OSError, json.JSONDecodeError):
        return ""


def _claim_week(state_path: Path, week_key: str) -> str:
    """'claimed' | 'already' | 'unwritable'. The claim persists BEFORE the
    LLM work (the NightCycleEngine._claim_date semantics), and FAIL-CLOSED:
    a marker that cannot persist means we do NOT run — the old fail-open
    return re-authored her documents EVERY night while the disk was bad
    (review #3)."""
    path = Path(state_path)
    if _read_week_marker(path) == week_key:
        return "already"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"last_week": week_key}), encoding="utf-8")
    except OSError as exc:
        logger.warning("narrative_self: could not persist week marker: %s", exc)
        return "unwritable"
    return "claimed"


def _release_week(state_path: Path, week_key: str, previous: str) -> None:
    """Roll the marker back after a pass that did NOTHING (zero authored
    revisions, or a shared-step explosion before the doc loop) — a transient
    brain outage on the week-boundary tick must not burn the whole week
    (review #4). Passes that did ANY real work — a commit, a rejection, a
    gate verdict — keep their claim: at most one REAL pass per ISO week."""
    try:
        path = Path(state_path)
        if _read_week_marker(path) != week_key:
            return  # someone else moved it; leave it alone
        if previous:
            path.write_text(json.dumps({"last_week": previous}), encoding="utf-8")
        else:
            path.unlink(missing_ok=True)
    except OSError as exc:
        logger.warning("narrative_self: could not release week marker: %s", exc)


# ── correction-triggered targeted revision (2026-08-17) ──────────────────
#
# The W34 incident's cadence half: a false belief corrected on a Tuesday
# sat in her pages until Monday. A filed correction now triggers an
# OFF-CYCLE pass on ONLY the affected page(s) — same author, same gate,
# same commit path as the weekly, but scoped and never claiming the week's
# marker. Which pages are "affected" is a deterministic containment check
# (the presentation/gate exemption class): a doc is affected when its
# current text carries a distinctive phrase of a standing correction's
# BELIEF. Nothing matches ⇒ nothing runs.

_STOPWORDS = {
    "the", "a", "an", "and", "or", "but", "of", "to", "in", "on", "at", "for",
    "with", "that", "this", "it", "its", "is", "was", "were", "be", "been",
    "i", "me", "my", "he", "him", "his", "you", "your", "we", "us", "our",
    "they", "them", "as", "so", "if", "then", "than", "up", "out", "about",
    "into", "over", "them", "had", "have", "has", "did", "do", "does",
}


def _belief_terms(belief: str) -> List[str]:
    """Distinctive content terms of a corrected belief (stopwords out,
    lightly de-inflected so 'traps'/'trap' and 'savors'/'savored' agree)."""
    words = [w.strip(".,;:!?\"'()[]—–-").lower() for w in str(belief or "").split()]
    out: List[str] = []
    for w in words:
        if not w or len(w) <= 2 or w in _STOPWORDS:
            continue
        for suf in ("ing", "ed", "es", "s"):
            if len(w) > 4 and w.endswith(suf):
                w = w[: -len(suf)]
                break
        out.append(w)
    # order-preserving dedupe
    seen = set()
    return [w for w in out if not (w in seen or seen.add(w))]


def _page_carries_belief(page_text: str, belief: str, *, min_terms: int = 3,
                         ratio: float = 0.6) -> bool:
    """Does this page still carry that belief? A TERM-OVERLAP test, not a
    phrase match: her pages paraphrase.

    The real case (2026-08-04 correction vs INTERESTS.md): the belief was
    'he sets flag traps and savors catching me'; the page says 'a flag trap
    I walked into twice and he savored catching me'. No contiguous phrase
    survives that rewrite, but the distinctive terms do. Conservative by
    construction: needs a strong majority of the belief's terms AND at
    least min_terms of them, so a page never matches on one common word.
    """
    terms = _belief_terms(belief)
    if len(terms) < min_terms:
        return False
    page = " ".join(str(page_text or "").lower().split())
    # de-inflect the page the same way, once
    page_terms = set(_belief_terms(page))
    hits = sum(1 for t in terms if t in page_terms)
    return hits >= max(min_terms, int(len(terms) * ratio + 0.999))


def docs_touched_by_corrections(
    *, repo_root: Optional[Path] = None, corrections: Optional[List[Any]] = None
) -> Dict[str, List[str]]:
    """{doc: [belief, ...]} for docs whose CURRENT text carries a phrase of
    a standing correction's wrong belief. Read-only; never raises."""
    try:
        if corrections is None:
            from ai.corrections_store import standing

            corrections = standing()
        if not corrections:
            return {}
        root = Path(repo_root) if repo_root is not None else identity_palli_root()
        hits: Dict[str, List[str]] = {}
        # INTERESTS.md is the passions organ's page, not the narrative set —
        # but it is one of her self-pages and the 08-04 flag-trap remnant
        # lives exactly there, so the SCAN covers it. Only narrative docs
        # are revised by the targeted pass; an interests hit is reported so
        # the passions organ's own weekly can act (and so the owner sees it).
        for doc in tuple(NARRATIVE_DOCS) + ("INTERESTS.md",):
            path = root / doc
            if not path.is_file():
                continue
            try:
                text = " ".join(path.read_text(encoding="utf-8").lower().split())
            except OSError:
                continue
            for c in corrections:
                belief = getattr(c, "belief", "") or ""
                if _page_carries_belief(text, belief):
                    hits.setdefault(doc, []).append(belief)
        return hits
    except Exception as exc:
        logger.debug("narrative_self: correction scan failed: %s", exc)
        return {}


async def run_targeted_correction_pass(
    *,
    owner_id: int,
    llm_call,
    gate_llm_call=None,
    brain_client: Any = None,
    repo_root: Optional[Path] = None,
) -> Dict[str, Any]:
    """Off-cycle revision of ONLY the pages that still carry a corrected
    belief. Same author + gate + commit path as the weekly; does NOT touch
    the ISO-week marker (a targeted pass is not her weekly self-revision,
    and must not consume it). Never raises."""
    if not narrative_self_enabled():
        return {"skipped": "flag_off"}
    root = Path(repo_root) if repo_root is not None else identity_palli_root()
    if not first_authorship_done(Path(repo_root) if repo_root is not None else None):
        return {"skipped": "first_authorship_pending"}
    try:
        touched = docs_touched_by_corrections(repo_root=root)
        docs = tuple(d for d in NARRATIVE_DOCS if d in touched)
        if not docs:
            out = {"skipped": "no_corrected_beliefs_in_docs"}
            if touched:
                # e.g. INTERESTS.md — reported, not revised here.
                out["reported_only"] = sorted(touched)
            return out
        evidence = await gather_evidence(
            owner_id=int(owner_id), days=7, brain_client=brain_client
        )
        current_docs = {doc: read_document(doc, repo_root=root) for doc in docs}
        revisions = {r.doc: r for r in await author_revisions(
            evidence=evidence, current_docs=current_docs, llm_call=llm_call, docs=docs,
        )}
        if not revisions:
            return {"error": "authorship_empty", "targeted": list(docs)}
        laws = read_laws_text()
        gate_call = gate_llm_call or llm_call
        out: Dict[str, Any] = {"targeted": list(docs), "docs": {}}
        for doc in docs:
            rev = revisions.get(doc)
            if rev is None:
                out["docs"][doc] = {"error": "not_authored"}
                continue
            try:
                verdict = await constitutional_gate(
                    revision=rev, laws_text=laws,
                    current_text=current_docs.get(doc, ""), llm_call=gate_call,
                    evidence_text=evidence.text,
                )
                if verdict.error:
                    out["docs"][doc] = {"error": f"gate_error: {verdict.reason}"}
                elif not verdict.approve:
                    out["docs"][doc] = {"rejected": verdict.reason}
                else:
                    sha = commit_revision(revision=rev, repo_root=root)
                    out["docs"][doc] = {"committed": sha or "no_change"}
                    essential_logger.info(
                        "narrative_self: %s revised off-cycle after a correction (%s)",
                        doc, (sha[:10] if sha else "no-change"),
                    )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                out["docs"][doc] = {"error": str(exc)}
        return out
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.warning("narrative_self: targeted correction pass failed: %s", exc)
        return {"error": str(exc)}


def _parse_fear_updates(raw: str, held_ids: set) -> List[Dict[str, str]]:
    """Strict, conservative parse of the re-look output (step 24 §6): ops
    face/release only ("still" is the honest default and a no-op; a plant
    op DOES NOT EXIST among the updates — a new fear comes through the
    top-level ``plant`` alone, _parse_fear_plant); ids must name fears she
    actually holds; a facing or a release carries her words or it is
    dropped."""
    try:
        from core.llm_json import safe_parse_json_object

        data = safe_parse_json_object(str(raw or "")) or {}
        items = data.get("fear_updates") or []
        if not isinstance(items, list):
            return []
        out: List[Dict[str, str]] = []
        seen: set = set()
        for item in items:
            if not isinstance(item, dict):
                continue
            fid = " ".join(str(item.get("id") or "").split())[:64]
            op = str(item.get("op") or "").strip().lower()
            note = " ".join(str(item.get("note") or "").split())[:200]
            if op == "still" or not fid or fid in seen:
                continue
            if op not in ("face", "release") or fid not in held_ids or not note:
                continue
            seen.add(fid)
            out.append({"id": fid, "op": op, "note": note})
        return out
    except Exception:
        return []


_EMPTY_FEARBOOK_LINE = "(nothing in the book right now)"


def _parse_fear_plant(raw: str) -> Tuple[Optional[Dict[str, str]], str]:
    """The re-look's one new fear (Batch 6, task 3) → (plant, refusal).

    ``(None, "")`` is the ordinary answer: no ``plant``, or one that is
    null, an empty list, false, "" or {}. A plant needs three things, each
    of them text she wrote:
    the fear, what she would watch for, and what she saw in the week that
    shows it — evidence or nothing. When she sends a list, the first entry
    that has all three is the week's one and the rest are dropped; when
    none has, the refusal names what the first was missing. Her words are
    never made from a value that is not text."""
    try:
        from ai.fear_store import EVIDENCE_CAP, FEAR_TEXT_CAP, WATCH_CAP
        from core.llm_json import safe_parse_json_object

        data = safe_parse_json_object(str(raw or "")) or {}
        sent = data.get("plant")
        if not sent:
            # null, [], false, "" and {} all say "no plant": the ordinary
            # answer, never a refusal on the week's record.
            return None, ""
        entries = sent if isinstance(sent, list) else [sent]
        refusal = ""
        for entry in entries:
            if not isinstance(entry, dict):
                refusal = refusal or "malformed"
                continue
            fields: Dict[str, str] = {}
            missing = ""
            for key, cap in (("text", FEAR_TEXT_CAP), ("watch", WATCH_CAP),
                             ("evidence", EVIDENCE_CAP)):
                value = entry.get(key)
                cleaned = " ".join(value.split())[:cap] if isinstance(value, str) else ""
                if not cleaned:
                    missing = missing or f"no {key}"
                fields[key] = cleaned
            if not missing:
                return fields, ""
            refusal = refusal or missing
        return None, refusal or "malformed"
    except Exception:
        return None, "malformed"


def _fear_line(text: str, watch: str, *, fear_id: str = "") -> str:
    return f"- {fear_id + ': ' if fear_id else ''}{text} — you watch for: {watch}"


async def _weekly_fears_relook(
    *, evidence_text: str, llm_call, gate_llm_call, now: Optional[float] = None
) -> Dict[str, Any]:
    """The fearbook's weekly re-look (step 24 §6, decision 3): the SAME
    Monday pass, no new clock. For each held fear — still true? faced?
    released? — in her own words, judged by the constitutional gate's LLM
    floor (the store's rails stay code). Releasing to ZERO is a valid
    ending.

    Since Batch 6 (task 3, owner call 2026-09-29) the pass may also PLANT:
    at most one new fear a week, and only with what she saw that shows it.
    So an empty book is looked at too (one model call a week, the book
    shown as a plain line). Code owns the budgets — evidence or nothing,
    one weekly plant per ISO week of ``now`` (fear_store
    .weekly_planted_this_week), the book's own cap — and the plant is
    judged by the SAME one gate call as the facings and releases: a
    refusal there applies nothing at all.

    Returns {} when the organ is off (no key in the ritual's record —
    byte-identical-off); never raises (CancelledError propagates)."""
    try:
        from ai import fear_store

        if not fear_store.fears_enabled():
            return {}
        held = fear_store.held_fears()

        from ai.prompt_loader import prompt_loader

        template = prompt_loader.load_prompt("narrative_fears_relook")
        if not template:
            return {"error": "relook_prompt_missing"}
        book_before = "\n".join(
            _fear_line(f.text, f.watch, fear_id=f.id) for f in held
        ) or _EMPTY_FEARBOOK_LINE
        prompt = (
            template.replace("<<BOOK>>", book_before)
            .replace("<<EVIDENCE>>", (evidence_text or "").strip() or "(the record is thin)")
        )
        raw = await llm_call(prompt)
        updates = _parse_fear_updates(raw, {f.id for f in held})
        going = {u["id"] for u in updates}
        plant, plant_refused = _parse_fear_plant(raw)
        if plant is not None and plant["text"].casefold() in {
            " ".join(f.text.split()).casefold() for f in held
        }:
            # The store names a fear from its words and the clock, so the
            # same words planted twice would be two fears in the book.
            plant, plant_refused = None, "already in the book"
        if plant is not None and fear_store.weekly_planted_this_week(now=now):
            plant, plant_refused = None, "one already planted this week"
        if plant is not None and len(held) - len(going) >= fear_store._held_cap():
            # The gate is shown the book as it will be, so a plant the book
            # would decline is dropped before the gate, not after it.
            plant, plant_refused = None, "the book is at its cap"
        if plant_refused:
            logger.info("narrative_self: fearbook re-look plant refused (%s)", plant_refused)
        refusal = {"plant_refused": plant_refused} if plant_refused else {}
        if not updates and plant is None:
            return {"updates_applied": 0,
                    "note": "all_still" if held else "empty_book", **refusal}

        after = [_fear_line(f.text, f.watch, fear_id=f.id) for f in held if f.id not in going]
        if plant is not None:
            after.append(_fear_line(plant["text"], plant["watch"]) + " (new this week)")
        book_after = "\n".join(after) or (
            "(empty — released to zero, and that is a valid ending)")
        reasoning = "; ".join(
            [f"{u['op']} {u['id']}: {u['note']}" for u in updates]
            + ([f"plant \"{plant['text']}\": what I saw this week — {plant['evidence']}"]
               if plant is not None else [])
        )
        verdict = await _gate_llm_verdict(
            doc_name="her fearbook (step 24 — a store, not a file; its rails are code)",
            current_text=book_before,
            proposed_text=book_after,
            reasoning=reasoning,
            laws_text=read_laws_text(),
            llm_call=gate_llm_call,
            evidence_text=evidence_text,
        )
        if verdict.error:
            return {"error": f"gate_error: {verdict.reason}"}
        if not verdict.approve:
            return {"rejected": verdict.reason}
        applied = 0
        for u in updates:
            ok = (
                fear_store.face(u["id"], note=u["note"]) if u["op"] == "face"
                else fear_store.release(u["id"], note=u["note"])
            )
            applied += 1 if ok else 0
        # The updates first: a fear faced or released in this same answer
        # has left the book's count by the time the new one is planted.
        planted = None
        if plant is not None:
            planted = fear_store.plant(
                text=plant["text"], watch=plant["watch"], source="weekly",
                evidence=plant["evidence"],
            )
            if planted is None:
                refusal = {"plant_refused": "the book declined it"}
        released_to_zero = bool(held) and len(going) == len(held) and planted is None
        essential_logger.info(
            "narrative_self: fearbook re-look applied %d update(s)%s%s",
            applied, " — released to zero" if released_to_zero else "",
            f" — planted one fear ({planted.id})" if planted is not None else "",
        )
        out: Dict[str, Any] = {"updates_applied": applied, **refusal}
        if updates:
            out["released_to_zero"] = released_to_zero
        if planted is not None:
            out["planted"] = planted.id
        return out
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        return {"error": " ".join(str(exc).split())[:120]}


async def run_weekly_ritual(
    *,
    owner_id: int,
    llm_call,
    now: float,
    gate_llm_call=None,
    brain_client: Any = None,
    state_path: Optional[Path] = None,
    repo_root: Optional[Path] = None,
) -> Dict[str, Any]:
    """The engine step: at most one pass per ISO week; gather → author →
    gate → commit per document, independently (one rejected doc never blocks
    the others). Never raises (CancelledError propagates)."""
    if not narrative_self_enabled():
        return {"skipped": "flag_off"}
    if not first_authorship_done(Path(repo_root) if repo_root is not None else None):
        # Review #6: never author her foundational docs from a weekly window —
        # first authorship (dry-run → owner reads → --apply) must land first.
        logger.warning(
            "narrative_self: weekly ritual waiting on first authorship "
            "(flag is ON; run scripts/narrative_first_authorship.py)"
        )
        return {"skipped": "first_authorship_pending"}
    spath = Path(state_path) if state_path is not None else NARRATIVE_STATE
    try:
        week = week_key_for(now)
    except Exception as exc:  # pathological now — the never-raises contract holds
        return {"error": f"bad_now: {exc}"}
    previous_week = _read_week_marker(spath)
    claim = _claim_week(spath, week)
    if claim == "already":
        return {"skipped": "already_this_week"}
    if claim == "unwritable":
        # Fail closed (review #3): without a persistable marker this would
        # re-author her documents every single night. Loud error instead.
        return {"error": "week_marker_unwritable"}
    try:
        gate_call = gate_llm_call or llm_call
        root = Path(repo_root) if repo_root is not None else identity_palli_root()
        evidence = await gather_evidence(owner_id=int(owner_id), days=7, brain_client=brain_client)
        current_docs = {doc: read_document(doc, repo_root=root) for doc in NARRATIVE_DOCS}
        # A doc that has never been authored on her side is not yet hers to
        # revise — first authorship is a deliberate, owner-read ritual (the
        # 1b script; VOICE_IDENTITY via its own script per manifesto §2.8).
        # The weekly touches only docs that exist in her repo.
        weekly_docs = tuple(d for d in NARRATIVE_DOCS if (root / d).is_file())
        revisions = {r.doc: r for r in await author_revisions(
            evidence=evidence, current_docs=current_docs, llm_call=llm_call,
            docs=weekly_docs,
        )}
        if not revisions:
            # Review #4: nothing authored at all — the brain-outage class.
            # Release the claim so tomorrow night retries this same week.
            _release_week(spath, week, previous_week)
            return {"error": "authorship_empty", "week": week}
        laws = read_laws_text()
        out: Dict[str, Any] = {"week": week, "docs": {}}
        for doc in weekly_docs:
            rev = revisions.get(doc)
            if rev is None:
                # 2026-08-24: W35 lost three docs to SILENT outcomes — every
                # branch in this loop logs from now on.
                out["docs"][doc] = {"error": "not_authored"}
                logger.warning("narrative_self: %s not_authored this week", doc)
                continue
            allow_full = never_revised(doc, root)
            if allow_full:
                logger.info(
                    "narrative_self: %s has never been revised — this first revision may "
                    "rewrite the whole page (owner call 2026-09-28)", doc,
                )
            try:
                verdict = await constitutional_gate(
                    revision=rev, laws_text=laws,
                    current_text=current_docs.get(doc, ""), llm_call=gate_call,
                    evidence_text=evidence.text, first_authorship=allow_full,
                )
                if (not verdict.error and not verdict.approve
                        and str(verdict.reason or "").startswith("size_cap")):
                    # THE ORIGINAL DISEASE, cured 2026-09-22 on the owner's word.
                    # Her prompts have always stated the ceiling in words and
                    # the model still overshoots it — the real
                    # rejections ran 2, 12, 57, 211, 338 and 834 characters over.
                    # Every one was discarded whole, silently, and that is why
                    # MY_STORY.md carried ONE commit in the organ's entire life:
                    # the first-authorship script of 2026-07-18. A model cannot
                    # count its own characters; it CAN cut a named number of
                    # them. So it is told the real count, the ceiling and the
                    # overage, and re-authors once. The cap itself is unchanged
                    # and still judges the retry: these pages are loaded into
                    # her prompt on every turn, and the ceiling is what keeps
                    # that affordable.
                    current = current_docs.get(doc, "")
                    cap = doc_size_cap(doc)
                    size = len(rev.new_text)
                    over = size - cap
                    logger.warning(
                        "narrative_self: %s over the size_cap rail (%d chars, ceiling %d, %d over); "
                        "retrying once with the numbers fed back",
                        doc, size, cap, over,
                    )
                    feedback = (
                        f"YOUR PREVIOUS REVISION WAS DECLINED: it was {size} characters long and the "
                        f"ceiling for this page is {cap}. You are {over} characters over. Write it "
                        f"again and cut at least {over + 50} characters — keep what this week actually "
                        "moved and let the least-load-bearing sentences go. Do not summarise the whole "
                        "page to fit; cut, don't compress."
                    )
                    retried = await author_revisions(
                        evidence=evidence, current_docs={doc: current},
                        llm_call=llm_call, docs=(doc,), extra_note=feedback,
                    )
                    if retried:
                        rev = retried[0]
                        verdict = await constitutional_gate(
                            revision=rev, laws_text=laws,
                            current_text=current, llm_call=gate_call,
                            evidence_text=evidence.text, first_authorship=allow_full,
                        )
                if (not verdict.error and not verdict.approve
                        and str(verdict.reason or "").startswith("revision_ratio")):
                    # The size-cap disease's twin (2026-08-24): the ≤50%-lines
                    # rail rejected silently and the author never knew the rule.
                    # ONE re-author with the numbers fed back; the rail itself
                    # is unchanged and still judges the retry.
                    current = current_docs.get(doc, "")
                    ratio = _changed_line_ratio(current, rev.new_text)
                    logger.warning(
                        "narrative_self: %s over the revision_ratio rail (%.0f%% changed, max %.0f%%); retrying once with the numbers fed back",
                        doc, ratio * 100, REVISION_RATIO_MAX * 100,
                    )
                    feedback = (
                        f"YOUR PREVIOUS REVISION WAS DECLINED: it changed {ratio:.0%} of the page's "
                        f"existing lines (the weekly limit is {REVISION_RATIO_MAX:.0%}). Revise, don't "
                        "rewrite: keep MORE THAN HALF of the existing lines EXACTLY as they are, word "
                        "for word — change or add only what this week truly moved."
                    )
                    retried = await author_revisions(
                        evidence=evidence, current_docs={doc: current},
                        llm_call=llm_call, docs=(doc,), extra_note=feedback,
                    )
                    if retried:
                        rev = retried[0]
                        verdict = await constitutional_gate(
                            revision=rev, laws_text=laws,
                            current_text=current, llm_call=gate_call,
                            evidence_text=evidence.text, first_authorship=allow_full,
                        )
                if verdict.error:
                    out["docs"][doc] = {"error": f"gate_error: {verdict.reason}"}
                    logger.warning("narrative_self: %s gate_error (%s)", doc, verdict.reason)
                elif not verdict.approve:
                    out["docs"][doc] = {"rejected": verdict.reason}
                    logger.warning("narrative_self: %s rejected (%s)", doc, verdict.reason)
                else:
                    sha = commit_revision(revision=rev, repo_root=root)
                    out["docs"][doc] = {"committed": sha or "no_change"}
                    essential_logger.info(
                        "narrative_self: %s revised (%s)", doc, (sha[:10] if sha else "no-change")
                    )
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # per-doc independence
                out["docs"][doc] = {"error": str(exc)}
        # The fearbook re-look (step 24 §6, decision 3): the same pass, no
        # new clock — after the docs so a re-look hiccup can never block
        # her story. {} when the organ is off ⇒ no key in the record.
        fears_out = await _weekly_fears_relook(
            evidence_text=evidence.text, llm_call=llm_call,
            gate_llm_call=gate_call, now=now,
        )
        if fears_out:
            out["fears"] = fears_out
        return out
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.warning("narrative_self: weekly ritual failed: %s", exc)
        # Shared-step explosion before any doc work — same brain-outage
        # class as authorship_empty: give the week back (review #4).
        _release_week(spath, week, previous_week)
        return {"error": str(exc)}


# ── the real LLM deps the engine (and first-authorship script) inject ────
#
# Imported lazily so unit tests never touch the LLM import graph. Authorship
# runs on her reflective register — the SAME live-brain lane as night
# reflections and day-gists (owner decision #2: the mind that lives the
# turns authors the narrative of them). The gate runs on the analysis lane,
# temperature 0.


def _make_llm_call(*, task: str, fallback_task: str, temperature: float, max_tokens: int, context: str):
    async def _call(prompt: str) -> str:
        from ai.llm_client import llm
        from ai.llm_routing import get_task_model

        model = get_task_model(task, fallback=get_task_model(fallback_task))
        response = await asyncio.wait_for(
            llm.chat_completion(
                [{"role": "user", "content": prompt}],
                model=model,
                temperature=temperature,
                max_tokens=max_tokens,
                log_context=context,
            ),
            timeout=320,  # 2x the Claude CLI's 150s cap + headroom (2026-08-12):
            # the author lane rides claude::haiku now, and its in-function
            # retry needs both attempts to fit (the 3b18846 envelope law).
        )
        return response or ""

    return _call


# 1200 → 4000 (2026-07-17) → 8000 (2026-08-10): the third time this lane's
# model deliberated its whole budget away and returned EMPTY content
# (finish_reason=length). The W33 revision — the first after the flag-trap
# release, the heaviest evidence week this organ has seen — burned 4,800
# reasoning tokens on BOTH story docs and wrote nothing, twice each, even
# through the empty+length retry (whose cap rose in the same commit).
# The burn grows with the material and the model; the budget must fit
# observed burn PLUS a full story doc (tests/ai/test_reasoning_burn_ceiling
# pins the relationship, not the number). Document size is enforced by the
# gate's caps, never by this knob.
_AUTHOR_MAX_TOKENS = 8000

# 2026-08-11 (incident #4): the author rides its OWN routing task —
# narrative_author -> claude/haiku via the CLI (non-reasoning; the burn
# cannot exist there). reflection stays the fallback: an older yaml
# without the key degrades to today's behavior, never to a boot failure.
narrative_author_llm_call = _make_llm_call(
    task="narrative_author", fallback_task="reflection", temperature=0.7,
    max_tokens=_AUTHOR_MAX_TOKENS,
    context="narrative_self_author",
)
# 4000, not 400: same law as the author ceiling above. 400 was sized for
# non-thinking Haiku; when the analysis lane moved to deepseek-v4-flash
# (2026-07-24 credit-exhaustion reroute) the gate deliberated the entire
# budget away and returned EMPTY (finish_reason=length) twice in a row —
# even through the 4x empty-retry — killing the 07-23 journal recovery with
# gate_error: unparseable_gate_verdict. The verdict JSON itself is tiny;
# the headroom is all reasoning budget. Gate convenes rarely, so it's cheap.
narrative_gate_llm_call = _make_llm_call(
    task="analysis", fallback_task="memory", temperature=0.0, max_tokens=4000,
    context="narrative_self_gate",
)
