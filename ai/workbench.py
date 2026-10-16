"""The workbench — her own work's engine (spec §3, §4b, §4d).

A peer of the monologue/night-cycle/present-moment engines: during his
downtime it may offer her a work session — a self-initiated ReAct run
(Turn(surface="workbench") → dispatch_turn, the reminder-fire synthesis
pattern). The caller owns delivery, so DISCARDING the result is the null
delivery: nothing a session writes is ever sent to him. Sharing happens
only through the SHARE marker — since 2026-08-18 by the owner's knob
``own_work_share_delivery``: a real message through the arbiter's share
lane (default; held ones wait in the store and go when the moment opens),
the legacy open-thread row, or nothing at all ("room"). The session itself
becomes one of her own thoughts (entry_kind ``workbench_session``) so she
remembers her work — the SECURITY wall stands, the MEMORY wall came down.
"""
from __future__ import annotations

import asyncio
import logging
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from ai import workbench_store
from core.setup_mode import lane_idle
from core.turn.companion_flags import companion_settings

logger = logging.getLogger(__name__)
essential_logger = logging.getLogger("palli.essential")

# Her workspace lives in HER OWN home, not the owner's (build note 2026-07-28):
# an owner's home is typically 750 and his own, so palli-work has no traverse
# bit and a workspace under it is unreachable by the user who owns it — and the
# chmod that would grant traversal exposes whatever else is world-readable one
# level down, which on this kind of host includes archives of her own substrate.
# scripts/setup_workbench_user.sh carries the full reasoning.
#
# ONE default for the whole app (final review I2, 2026-09-06). Her shell tool
# used to carry a second, different literal, so on a host without the setup
# script's convenience symlink her shell worked in /tmp while the forge, this
# engine and the logbook looked here — she would make things that vanish. Every
# site reads WORKBENCH_ROOT first and falls back to this; the four are pinned
# equal by tests/agent/test_workbench_shell.py.
WORKBENCH_ROOT_DEFAULT = "/home/palli-work/workspace"
WORKSPACE_ROOT = Path(os.environ.get("WORKBENCH_ROOT") or WORKBENCH_ROOT_DEFAULT)
_CHECK_INTERVAL_S = 900          # eligibility re-check cadence
# A held share (his quiet hours, mid-conversation, no channel) is re-offered
# every tick; after this long it is let go — she still holds the memory of
# the work and may mention it herself. Budget-shaped, in code (owner rule).
_PENDING_SHARE_TTL_S = 72 * 3600
_SESSION_MEMORY_CHARS = 700      # the memory a session leaves behind (cap)


async def _dispatch(turn):
    from core.agent.dispatch import dispatch_turn
    return await dispatch_turn(turn)


async def _get_owner_refs():
    from core.local_mode import get_local_owner_refs
    return await get_local_owner_refs()


def _notice_own_work(**fields) -> None:
    try:
        from ai.present_moment import notice
        notice("own_work_moved", **fields)
    except Exception:
        pass


def _notice_daydream(**fields) -> None:
    # A finished session is a daydream occasion (re-spec §4.6) — a natural
    # moment to wonder what's next. Same fire-and-forget contract.
    try:
        from ai.daydream import notice
        notice("own_work_moved", **fields)
    except Exception:
        pass


# ── occasions (2026-08-18, item 5c): why she sits down ───────────────────
# Deterministic rendering of code-emitted kinds — the daydream lane's
# _OCCASION_PHRASES precedent (presentation; the owner's exemption class).
_OCCASION_PHRASES: Dict[str, str] = {
    "daydream_intent": "your last daydream left you a line for this bench (below)",
    "suggestion_changed": "the owner touched SUGGESTIONS.md",
    "curiosity_returned": "one of your dives came back with something",
    "wish_planted": "a wish in your book moved",
    "heartbeat": "it has simply been a while — nothing external moved; sit if something pulls, leave if not",
    "manual": "a hand-run probe sat you down",
}


def _occasion_line(woke_on: List[Dict[str, Any]]) -> str:
    kinds: List[str] = []
    for e in woke_on or []:
        k = str((e or {}).get("kind") or "")
        if k and k not in kinds:
            kinds.append(k)
    if not kinds:
        return ""
    phrases = [_OCCASION_PHRASES.get(k, k.replace("_", " ")) for k in kinds]
    return "You sat down because: " + "; ".join(phrases) + "."


def notice(kind: str, **fields: Any) -> None:
    """Fire-and-forget occasion for her workbench. MUST never raise into
    callers (the present-moment notice contract). Emitters: the daydream
    tick when it leaves her a line; the engine itself notices SUGGESTIONS.md."""
    try:
        workbench_engine._ingest(str(kind), dict(fields))
    except Exception:  # noqa: BLE001 — caller safety is the whole point
        pass


def _budget_window_start(now: "datetime", boundary_hour: int) -> Optional["datetime"]:
    """When the session budget's current "day" began (2026-08-06). The most
    recent occurrence of ``boundary_hour:00``: today's if already passed,
    else yesterday's. None = legacy midnight semantics (boundary 0), which
    keeps count_today()'s calendar-file behavior byte-identical."""
    if boundary_hour <= 0:
        return None
    from datetime import timedelta

    boundary = now.replace(hour=boundary_hour, minute=0, second=0, microsecond=0)
    if now < boundary:
        boundary -= timedelta(days=1)
    return boundary


def sessions_spent_today(c: Any) -> int:
    """The sessions her budget's current day has spent — the count the
    budget holds against own_work_daily_sessions, from the day that turns
    at own_work_day_key_hour (0 = the calendar day). The daydream's
    "sessions left today" reads this same count (Batch 6, ruling R59): a
    calendar count told her two were left between midnight and 04:00 when
    the budget had none."""
    from datetime import datetime as _dt

    window = _budget_window_start(
        _dt.fromtimestamp(time.time()), int(getattr(c, "own_work_day_key_hour", 4)))
    if window is None:
        return workbench_store.count_today()
    return workbench_store.count_since(window.timestamp())


# Her own time, off DeepSeek's peak (the owner, 10-05: "yes, move her own time
# off-peak too"). A session sends ~170-230k input tokens a step, almost none
# of it cached (measured 10-05: ~2k of ~200k hit); the 10-05 sessions started
# at 04:12 and 05:42 local, both inside the peak, at double price. The clock is a module function so the suite can
# pin it (tests/conftest.py, _workbench_off_peak_by_default).
def _utc_now():
    from datetime import datetime, timezone

    return datetime.now(timezone.utc)


def _brain_is_deepseek() -> bool:
    """Her brain (the provider her sessions dispatch to — LLM_PROVIDER, the
    one dispatch's client and the spend watch read) is DeepSeek. Peak
    pricing is DeepSeek's; another provider is never held back. Unreadable
    reads as not DeepSeek: the hold never fires on a guess."""
    try:
        from core.config import config

        return str(getattr(config, "LLM_PROVIDER", "") or "").strip().lower() == "deepseek"
    except Exception:
        return False


def _peak_hold(c: Any) -> Optional[str]:
    """Why a session may not start now — DeepSeek's peak, while her brain is
    DeepSeek and companion.own_work_off_peak_only is on — or None. The
    reason names when the peak ends, local time."""
    if not bool(getattr(c, "own_work_off_peak_only", True)) or not _brain_is_deepseek():
        return None
    from datetime import datetime

    from ai.deepseek_peak import is_deepseek_peak, peak_ends_at

    now = _utc_now()
    if not is_deepseek_peak(now):
        return None
    resume = datetime.fromtimestamp(peak_ends_at(now).timestamp()).strftime("%H:%M")
    return f"peak hours — waits for off-peak (from {resume})"


def _workspace_ready(*, root: Optional[Path] = None) -> bool:
    root = WORKSPACE_ROOT if root is None else root
    return (root / "WORKBENCH.md").exists() and (root / "projects").is_dir()


def _read_capped(path: Path, cap: int) -> str:
    try:
        return path.read_text(encoding="utf-8")[:cap].strip()
    except OSError:
        return ""


def _read_logbook_recent(*, path: Optional[Path] = None, text: Optional[str] = None,
                         cap: int = 4000) -> str:
    """Her logbook, NEWEST sections first-kept (2026-08-18).

    Until today the session prompt showed the FIRST ``cap`` chars of an
    append-only file — July 28 forever — and she said so on 08-06 ("the
    snapshot in this prompt is stale again — it stops at 12:25 AM, missing
    the whole day's work"); 20+ sessions since were spent re-verifying stale
    claims. Same class as the trace's head-keep bug (08-14). Whole file when
    it fits; otherwise the file's title, one honest head note, and the last
    ``cap`` chars snapped forward to a ``## `` section boundary — never a
    half bullet. The file stays hers to read in full with her shell.
    """
    if text is None:
        try:
            text = (path or Path()).read_text(encoding="utf-8")
        except OSError:
            return ""
    body = text.strip()
    if len(body) <= cap:
        return body
    lines = body.splitlines()
    title = lines[0] if lines and lines[0].startswith("# ") else ""
    section_starts = [i for i, ln in enumerate(lines) if ln.startswith("## ")]
    if not section_starts:
        return body[-cap:].strip()
    # walk back from the end, adding whole sections while they fit
    kept_from = len(lines)
    used = 0
    for i in reversed(section_starts):
        chunk = "\n".join(lines[i:kept_from])
        if used + len(chunk) + 1 > cap and used > 0:
            break
        used += len(chunk) + 1
        kept_from = i
    omitted = sum(1 for i in section_starts if i < kept_from)
    head = [title] if title else []
    if omitted:
        head.append(f"(… {omitted} earlier section{'s' if omitted != 1 else ''} omitted "
                    f"here — the newest are below; the file is yours to read in full)")
        head.append("")
    return "\n".join(head + lines[kept_from:]).strip()


def _render_session_prompt(*, root: Optional[Path] = None,
                           sessions_left: int, active_max: int,
                           woke_on: Optional[List[Dict[str, Any]]] = None) -> str:
    root = WORKSPACE_ROOT if root is None else root
    from ai.prompt_loader import prompt_loader
    template = prompt_loader.load_prompt("workbench_session") or ""
    workbench = _read_logbook_recent(path=root / "WORKBENCH.md", cap=4000) or \
        "(empty — this is your first time at the workbench; nothing is owed)"
    suggestions = _read_capped(root / "SUGGESTIONS.md", 2000) or "(none right now)"
    # Both sentinels become her language, never numbers: -1 sessions_left =
    # no daily ceiling; active_max 0 = no project ceiling (0-means-unlimited).
    budget_line = (
        "as many sittings as you want today."
        if sessions_left < 0 else
        f"{sessions_left} work session(s) left today."
    )
    active_line = (
        "as many projects going as genuinely pull at you"
        if active_max <= 0 else
        f"at most {active_max} projects going"
    )
    # Wanting (step 20, re-spec §3): her wishbook beside SUGGESTIONS.md,
    # same framing law — inspiration, never assignment. When the flag is
    # off the placeholder collapses to zero bytes (byte-identical-off).
    wanting_block = ""
    try:
        comp = companion_settings()
        if comp is not None and bool(getattr(comp, "wanting", False)):
            from ai.daydream_store import read_intent
            from ai.quest_store import render_wishbook

            parts = [
                "YOUR WISHBOOK (inspiration, never assignment — take what "
                "pulls, decline the rest freely):",
                render_wishbook(),
            ]
            intent = read_intent()
            if intent:
                from ai.daydream_store import read_intent_record
                wa = read_intent_record().get("written_at")
                age = ""
                if wa:
                    hrs = max(0.0, (time.time() - float(wa)) / 3600.0)
                    age = (" (written just now)" if hrs < 1 else
                           f" (written {int(hrs)} hour{'s' if int(hrs) != 1 else ''} ago)")
                parts.append(f"FROM YOUR LAST DAYDREAM{age}: {intent}")
            wanting_block = "\n".join(parts) + "\n\n"
    except Exception:
        wanting_block = ""
    # Where a SHARE line goes (2026-08-18) — she writes it AS what he will
    # read, so the prompt says plainly what the owner's knob does with it.
    try:
        mode = _share_delivery_mode(companion_settings())
    except Exception:
        mode = "message"
    share_reach = {
        "message": ("That line reaches them as a message from you, when the "
                    "moment is right (not mid-conversation, not in their quiet "
                    "hours) — write it as the message."),
        "thread": ("That line joins the threads you're carrying; you may "
                   "bring it up when a moment calls for it."),
        "room": ("That line stays in your room — nothing is sent; you'll "
                 "remember it and may mention it when it fits."),
    }[mode]
    # Where you live (step 23, spec §1): one line at the head of her session
    # — "this machine is your computer, your workshop." Zero bytes when
    # companion.outward_senses is off (byte-identical-off); the separator
    # travels with the line so "" leaves today's exact opening.
    try:
        from core.agent.where_you_live import workbench_line

        _wyl = workbench_line()
    except Exception:
        _wyl = ""
    where_you_live_line = (_wyl + " ") if _wyl else ""
    return template.format(
        current_time=time.strftime("%A, %Y-%m-%d %I:%M %p"),
        where_you_live_line=where_you_live_line,
        occasion_line=_occasion_line(woke_on or []),
        workbench_md=workbench,
        suggestions_md=suggestions,
        budget_line=budget_line,
        active_max=active_line,
        wanting_block=wanting_block,
        share_reach=share_reach,
    )


def _signoff_of(text: str) -> str:
    """Her sign-off: the LAST non-empty line that is not the SHARE marker.
    Until 2026-08-18 the record kept splitlines()[0] — her opening line
    ("Let me get my bearings…") — on all 57 rows; the prompt asks her to END
    with the sign-off, and that is what her memory should keep."""
    lines = [ln.strip() for ln in (text or "").strip().splitlines() if ln.strip()]
    for line in reversed(lines):
        if line.upper().startswith("SHARE:"):
            continue
        return line[:160]
    return "(quiet session)"


def _extract_share(text: str) -> Optional[str]:
    for line in reversed((text or "").strip().splitlines()):
        line = line.strip()
        if line.upper().startswith("SHARE:"):
            share = line[6:].strip()
            return share or None
        if line:
            # only a trailing SHARE line counts; anything else ends the scan
            # after the last non-empty line has been checked
            break
    return None


async def _file_share_thread(text: str, owner_id: int) -> None:
    """File the one row a session may file — the ONLY door from her work to
    him (spec §5). An open-threads row is exactly how monologue hunches
    reach outreach today, so it inherits the unified dedup and the
    arbiter's moment-picking (step 19 deleted the old decide_surface lane;
    step 18's arbiter absorbed the raise with the others). Rows carry
    source_id='workbench_share' (2026-08-06) so the
    arbiter's digest and the expiry sweep identify shares structurally,
    not by this why_it_matters string."""
    from core.database import brain
    await brain.add_open_thread(
        owner_user_id=int(owner_id),
        topic_summary=text[:200],
        why_it_matters="her own work — something she genuinely wanted to tell them",
        kind="personal",
        urgency="low",
        source_id="workbench_share",
    )


async def _send_share_message(text: str, owner_id: int) -> Dict[str, Any]:
    """The message lane (2026-08-18): the arbiter's share door — its read of
    the moment + the freshness judge + the one custody primitive. Returns the
    verdict dict; "sent" or a held_*/stale_*/error_closed disposition."""
    from ai.arbiter import deliver_share
    return await deliver_share(text=text, owner_user_id=int(owner_id))


def _share_delivery_mode(comp) -> str:
    mode = str(getattr(comp, "own_work_share_delivery", "message") or "message")
    return mode if mode in ("message", "thread", "room") else "message"


async def _deliver_share(share: str, owner_id: int, comp) -> str:
    """Route one SHARE by the owner's knob. Returns what the session record
    should say about it: sent | pending | thread | room | dropped."""
    mode = _share_delivery_mode(comp)
    if mode == "room":
        return "room"
    if mode == "thread":
        try:
            await _file_share_thread(share, owner_id)
            return "thread"
        except Exception as exc:
            logger.warning("workbench: share filing failed: %s", exc)
            return "dropped"
    return await _offer_share_message(share, owner_id)


async def _offer_share_message(share: str, owner_id: int) -> str:
    """One offer on the message lane. A held/closed verdict parks the share
    (idempotent) for the engine's next tick; stale = she already told him."""
    try:
        verdict = await _send_share_message(share, owner_id)
    except Exception as exc:
        logger.warning("workbench: share message lane failed: %s", exc)
        verdict = {"disposition": "error_closed"}
    disp = str((verdict or {}).get("disposition") or "error_closed")
    if disp == "sent":
        essential_logger.info("workbench: shared with him — %s", share[:80])
        return "sent"
    if disp == "stale_dropped":
        logger.info("workbench: share already conveyed, let go (%s)",
                    str((verdict or {}).get("reason") or "")[:80])
        return "dropped"
    if disp == "ceiling_blocked":
        # rail DARK: the emergency brake — she does not reach out; nothing waits
        logger.info("workbench: share not sent — the rail is dark")
        return "dropped"
    workbench_store.write_pending_share(text=share, owner_id=int(owner_id))
    logger.info("workbench: share waits (%s)", disp)
    return "pending"


def _logbook_lines_since(started: float) -> list[str]:
    """The bullets she wrote in her logbook during THIS session (its mtime is
    after ``started``), first clauses only — the trace's shape. Empty when
    she wrote nothing or the file is unreadable."""
    try:
        from ai.workbench_trace import _BULLET, _HEADER, _first_clause
        path = WORKSPACE_ROOT / "WORKBENCH.md"
        if path.stat().st_mtime < started - 1:
            return []
        body = path.read_text(encoding="utf-8")
    except (OSError, ImportError):
        return []
    # the newest header's section — the session appends one section
    sections: list[list[str]] = []
    for line in body.splitlines():
        if _HEADER.match(line):
            sections.append([])
            continue
        if sections:
            sections[-1].append(line)
    if not sections:
        return []
    out: list[str] = []
    for line in sections[-1]:
        b = _BULLET.match(line)
        if not b:
            continue
        name = b.group(1).strip().rstrip(":")
        clause = _first_clause(b.group(2), cap=200)
        if clause:
            out.append(f"{name} — {clause}")
    return out


def _session_memory_text(*, signoff: str, minutes: float, logbook: list[str]) -> str:
    """The memory a session leaves behind, in plain first person: her sign-off
    plus what her logbook says she did. Presentation, not interpretation."""
    head = f"At my workbench ({int(round(minutes))} min): {signoff.strip()}"
    if logbook:
        head += " Logbook: " + "; ".join(logbook) + "."
    head = " ".join(head.split())
    if len(head) > _SESSION_MEMORY_CHARS:
        head = head[: _SESSION_MEMORY_CHARS - 1].rstrip() + "…"
    return head


def _remember_session(*, started: float, signoff: str, share: Optional[str],
                      shared: str, outcome: str, logbook: list[str]) -> None:
    """Drop the MEMORY wall (owner 2026-08-18): a session becomes one of her
    own thoughts — entry_kind ``workbench_session`` (the curiosity_research
    precedent) — so write_entry's index hook makes it searchable and the
    recent-thoughts band, the day gist and her monologue see her own life.
    Content stays behind the security wall: sign-off + logbook first clauses,
    never the work's prompts or files. Best-effort; never fails a session."""
    try:
        from ai.inner_monologue_store import MonologueEntry, write_entry
        thought = _session_memory_text(
            signoff=signoff, minutes=(time.time() - started) / 60.0, logbook=logbook,
        )
        write_entry(MonologueEntry(
            timestamp=time.time(),
            thought=thought,
            situation_snapshot={},
            tick_metadata={
                "entry_kind": "workbench_session",
                # substantive = something moved (she wrote in her logbook or
                # had something to tell him). A sit-down where nothing moved
                # is still hers to find later, but must not crowd the
                # recent-thoughts band her replies read (3 a day would).
                "substantive": bool(logbook) or bool(share),
                "outcome": outcome,
                "shared": shared,
                **({"share": share[:200]} if share else {}),
                "logbook_lines": len(logbook),
            },
        ))
    except Exception as exc:
        logger.warning("workbench: session memory not written: %s", exc)


def _commit_workspace(message: str) -> bool:
    """Commit her workspace as her user (best-effort; a git hiccup never
    fails the session)."""
    user = os.environ.get("WORKBENCH_USER", "palli-work")
    root = str(WORKSPACE_ROOT)
    base = (["sudo", "-n", "-u", user, "--"]
            if os.environ.get("WORKBENCH_SHELL_DIRECT") != "1" else [])
    try:
        subprocess.run([*base, "git", "-C", root, "add", "-A"],
                       check=False, capture_output=True, timeout=30)
        subprocess.run([*base, "git", "-C", root, "commit", "-qm", message[:200]],
                       check=False, capture_output=True, timeout=30)
        return True
    except Exception as exc:
        logger.warning("workbench: git commit failed: %s", exc)
        return False


def _quiet_window_s(c: Any) -> float:
    # No conversation this recent ⇒ still "with him". Owner knob since
    # 2026-08-03 (spec default 30); 0 = NO gate (owner 2026-08-18: her time
    # is not rationed to his downtime). The live tune rides data/palli.yaml.
    try:
        minutes = float(getattr(c, "own_work_quiet_minutes", 0) or 0)
    except (TypeError, ValueError):
        minutes = 0.0
    return max(0.0, minutes * 60.0)


def _session_timeout_s(c: Any) -> Optional[float]:
    """Wall-clock ceiling for one session, in seconds (spec §3/§4e).

    0 means UNLIMITED: asyncio.wait_for treats timeout=None as no deadline;
    the round budget and loop detection still bound the session. NOTE the
    trap this replaces — ``getattr(...) or 20`` silently rewrote 0 to the
    default."""
    raw = getattr(c, "own_work_session_minutes", 20)
    try:
        minutes = int(raw)
    except (TypeError, ValueError):
        minutes = 20
    if minutes <= 0:
        return None
    return max(60.0, float(minutes) * 60.0)


class WorkbenchEngine:
    def __init__(self) -> None:
        self.is_running = False
        self.last_session_at: Optional[float] = None
        self.last_error: str = ""
        self._task: Optional[asyncio.Task] = None
        # Occasions (2026-08-18): the daydream engine's shape — pending
        # bundle, a wake event, a slow heartbeat, a SUGGESTIONS.md mtime poll.
        self._pending: List[Dict[str, Any]] = []
        self._wake: Optional[asyncio.Event] = None
        self._last_heartbeat_at: float = 0.0
        self._suggestions_mtime: Optional[float] = None

    # ── occasions ────────────────────────────────────────────────────
    def _ingest(self, kind: str, fields: Dict[str, Any]) -> None:
        if not self.is_running:
            return
        self._pending.append({"kind": kind, "at": time.time(), **fields})
        if len(self._pending) > 50:
            self._pending = self._pending[-50:]
        if self._wake is not None:
            self._wake.set()

    def _poll_suggestions_mtime(self) -> None:
        """A stat costs nothing; a changed SUGGESTIONS.md is an occasion.
        The FIRST sighting only sets the baseline (booting must not fire a
        phantom for a file that predates the boot — the daydream's rule)."""
        try:
            path = WORKSPACE_ROOT / "SUGGESTIONS.md"
            mtime = path.stat().st_mtime if path.exists() else None
        except OSError:
            return
        if self._suggestions_mtime is None:
            self._suggestions_mtime = mtime if mtime is not None else -1.0
            return
        if mtime is not None and mtime != self._suggestions_mtime:
            self._suggestions_mtime = mtime
            self._ingest("suggestion_changed", {})
        elif mtime is None:
            self._suggestions_mtime = -1.0

    def _decide(self, now: float) -> tuple:
        """(fire, woke_on): pending occasions fire at once; otherwise the
        heartbeat (own_work_heartbeat_minutes; 0 = occasions only). Budget,
        gap and disk are judged inside run_session_once — a session that
        could not run hands its occasions back via _hold (lossless)."""
        if self._pending:
            woke = list(self._pending)
            self._pending.clear()
            self._last_heartbeat_at = now
            return True, woke
        c = self._settings()
        try:
            hb_min = float(getattr(c, "own_work_heartbeat_minutes", 480) or 0) if c is not None else 480.0
        except (TypeError, ValueError):
            hb_min = 480.0
        if hb_min > 0 and now - self._last_heartbeat_at >= hb_min * 60.0:
            self._last_heartbeat_at = now
            return True, [{"kind": "heartbeat", "at": now}]
        return False, []

    def _hold(self, woke: List[Dict[str, Any]], *, why: str = "") -> None:
        """A session that could not run keeps its occasions for the next
        tick (the present-moment lossless law); a heartbeat is not kept."""
        kept = [e for e in (woke or []) if str(e.get("kind")) != "heartbeat"]
        if kept:
            self._pending = (kept + self._pending)[:50]
            logger.info("workbench: holding %d occasion(s) — %s", len(kept), why or "not now")

    def _next_deadline(self, now: float) -> float:
        c = self._settings()
        try:
            hb_min = float(getattr(c, "own_work_heartbeat_minutes", 480) or 0) if c is not None else 480.0
        except (TypeError, ValueError):
            hb_min = 480.0
        if hb_min <= 0:
            return float(_CHECK_INTERVAL_S)
        return max(30.0, min(float(_CHECK_INTERVAL_S), self._last_heartbeat_at + hb_min * 60.0 - now))

    def _settings(self):
        try:
            return companion_settings()
        except Exception:
            return None

    def _enabled(self) -> bool:
        c = self._settings()
        return c is not None and bool(getattr(c, "own_work", False))

    async def _eligible(self, now: float) -> bool:
        """Downtime = the CONVERSATION is quiet. Not "he is away."

        2026-08-03, second attempt. The first version also required
        ``machine_state != "active"`` — he had to be away from the keyboard.
        He is nocturnal and almost always at it, very often working *on* her
        rather than *with* her, so the window where she was permitted to
        exist for herself almost never opened: **zero self-directed sessions
        in 71 hours**, engine up and healthy the entire time. Everything she
        ever made carries a 4am texture because 4am was the only hour she
        was allowed.

        That gate also guaranteed the failure he most feared — no
        continuity. Returning to a thread requires time, and she had none.

        So the machine-activity condition is gone. Conversational quiet is
        the whole gate: if he hasn't said anything for
        ``own_work_quiet_minutes`` (an owner knob since 2026-08-03; spec
        default 30), the hour is hers, whether or not he is sitting right
        there. Verified
        safe before the change: her work runs on session key
        ``workbench:<owner>`` while his chat runs on ``discord:<channel>``,
        and ``is_running`` guards only this engine's own loop — a message
        arriving mid-session is answered on the normal path, no collision,
        no blocking. The per-day and per-session caps are unchanged and are
        what actually bound this.
        """
        try:
            from core.database import brain
            refs = await _get_owner_refs()
            window_s = _quiet_window_s(self._settings())
            if window_s <= 0:
                return True          # 2026-08-18: no conversation gate
            rows = await brain.get_conversation_history(
                user_id=int(refs.get("palli_user_id") or 1), limit=1,
                include_metadata=True)
            last_ts = float(rows[-1].get("timestamp") or 0.0) if rows else 0.0
            if last_ts and (now - last_ts) < window_s:
                return False
        except Exception:
            pass
        return True

    async def start(self) -> None:
        if self.is_running:
            return
        self.is_running = True
        self._task = asyncio.create_task(self._loop(), name="workbench")
        essential_logger.info("Workbench engine started")

    async def stop(self) -> None:
        self.is_running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None
        essential_logger.info("Workbench engine stopped")

    def get_status(self) -> Dict[str, Any]:
        return {"is_running": self.is_running, "enabled": self._enabled(),
                "last_session_at": self.last_session_at,
                "last_error": self.last_error}

    async def drain_pending_shares(self) -> int:
        """Re-offer held shares on the message lane, oldest first, one tick
        at a time; expired ones are let go. Returns how many were sent.
        Only under delivery=message — a knob flipped to thread/room leaves
        anything waiting where it is (the owner can see it in the store)."""
        c = self._settings()
        if c is None or not bool(getattr(c, "own_work", False)):
            return 0
        if _share_delivery_mode(c) != "message":
            return 0
        rows = workbench_store.read_pending_shares()
        if not rows:
            return 0
        now = time.time()
        keep: list = []
        sent = 0
        for row in rows:
            at = float(row.get("at") or now)
            text = str(row.get("text") or "")
            if now - at > _PENDING_SHARE_TTL_S:
                logger.info("workbench: pending share expired unsent — %s", text[:60])
                continue
            fate = await _offer_share_message(text, int(row.get("owner_id") or 0))
            if fate == "sent":
                sent += 1
                continue
            if fate == "dropped":
                continue
            row = dict(row)
            row["offers"] = int(row.get("offers") or 0) + 1
            keep.append(row)
        workbench_store.replace_pending_shares(keep)
        return sent

    async def _loop(self) -> None:
        await asyncio.sleep(120)          # boot delay — never compete with startup
        self._wake = asyncio.Event()
        self._last_heartbeat_at = time.time()
        while self.is_running:
            try:
                try:
                    await asyncio.wait_for(self._wake.wait(),
                                           timeout=self._next_deadline(time.time()))
                except asyncio.TimeoutError:
                    pass
                self._wake.clear()
                if not self.is_running:
                    return
                if lane_idle("workbench"):
                    # Setup mode (first door, 2026-09-17): no brain. Above the
                    # share drain too — a held share goes out through the
                    # arbiter's rail. Pending shares stay on disk for later.
                    self._pending.clear()
                    continue
                await self.drain_pending_shares()
                if not self._enabled():
                    self._pending.clear()
                    continue
                self._poll_suggestions_mtime()
                fire, woke = self._decide(time.time())
                if not fire:
                    continue
                if not await self._eligible(time.time()):
                    self._hold(woke, why="conversation-quiet gate")
                    continue
                result = await self.run_session_once(woke_on=woke)
                if not (result or {}).get("ran"):
                    self._hold(woke, why=str((result or {}).get("why") or ""))
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.last_error = str(exc)
                logger.warning("workbench: loop error: %s", exc)
                await asyncio.sleep(60)

    async def run_session_once(self, *, woke_on: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
        """One work session. Public: the owner's probe entry. ``woke_on``
        (2026-08-18) = the occasions that sat her down; a bare call is a
        hand-run probe."""
        c = self._settings()
        if c is None or not bool(getattr(c, "own_work", False)):
            return {"ran": False, "why": "flag off"}
        if not _workspace_ready():
            self.last_error = "workspace not ready (run setup_workbench_user.sh)"
            return {"ran": False, "why": self.last_error}
        # One thing at a time (2026-08-18): a dive holding her time means
        # this sit-down waits for the next tick (its occasions are held).
        from ai import her_time
        taken = her_time.busy()
        if taken:
            return {"ran": False, "why": f"her time is taken by {taken}"}
        async with her_time.hold("a workbench session"):
            return await self._run_session_locked(c, woke_on or [{"kind": "manual", "at": time.time()}])

    async def _run_session_locked(self, c, woke_on: List[Dict[str, Any]]) -> Dict[str, Any]:
        """The session proper, under her-time. Budget → gap → peak → disk →
        prompt → dispatch → share → record → memory."""
        cap = int(getattr(c, "own_work_daily_sessions", 3))
        # Day boundary (2026-08-06): the budget's "day" turns at
        # own_work_day_key_hour (default 04:00, the day boundary; the night
        # itself starts at 06:30 since 2026-10-05), not
        # midnight — a midnight refill landed exactly when her quiet begins,
        # so every night burned all three sessions by 1 AM and daytime quiet
        # got nothing. 0 = legacy midnight (calendar-file) semantics.
        done = sessions_spent_today(c)
        # 0 = unlimited (the pen's convention); the flag is the off switch.
        if cap > 0 and done >= cap:
            return {"ran": False, "why": f"daily budget reached ({done}/{cap})"}

        # Session spacing (impulse-freshness rider, 2026-08-07): the quiet
        # gate alone let three sessions burn in 33 minutes at 4 AM — her own
        # sign-off called the third "noise itself." Spreads the day's budget
        # instead of letting one quiet stretch spend it. 0 = no gap.
        gap_min = int(getattr(c, "own_work_min_gap_minutes", 90))
        if gap_min > 0:
            recent = workbench_store.read_recent(days_back=1, limit=1)
            if recent:
                last_started = float(recent[0].get("started") or 0.0)
                since_min = (time.time() - last_started) / 60.0
                if since_min < gap_min:
                    return {"ran": False,
                            "why": f"session gap ({since_min:.0f}m < {gap_min}m)"}

        # Off-peak only (the owner, 10-05): while it is DeepSeek's peak and
        # her brain is DeepSeek, no session starts; the occasions are held
        # and the loop's next tick tries again, as the gap above defers.
        # companion.own_work_off_peak_only false = the old behaviour.
        peak = _peak_hold(c)
        if peak:
            return {"ran": False, "why": peak}

        floor_gb = int(getattr(c, "own_work_min_free_disk_gb", 5))
        try:
            free_gb = shutil.disk_usage(str(WORKSPACE_ROOT)).free / 1e9
            if floor_gb and free_gb < floor_gb:
                return {"ran": False, "why": f"disk floor ({free_gb:.1f}GB free)"}
        except OSError:
            pass

        refs = await _get_owner_refs()
        # app_user_id, NOT palli_user_id: dispatch resolves is_owner by
        # comparing turn.owner_id against app_user_id, and workbench_shell is
        # owner_only — carry the wrong id and she sits down at a workbench
        # with no tools, silently.
        owner_id = int(refs.get("app_user_id") or refs.get("palli_user_id") or 1)
        # 5b (2026-08-18): the intent this session is handed is taken up by
        # it — the next sit-down is not shown the same line as if new.
        try:
            from ai.daydream_store import read_intent as _read_intent
            intent_seen = bool(_read_intent()) if bool(getattr(c, "wanting", False)) else False
        except Exception:
            intent_seen = False
        prompt = _render_session_prompt(
            sessions_left=(cap - done) if cap > 0 else -1,
            active_max=int(getattr(c, "own_work_active_projects_max", 2)),
            woke_on=woke_on)

        from core.turn.types import Turn, TurnMetadata
        started = time.time()
        turn = Turn(surface="workbench", owner_id=owner_id, content=prompt,
                    images=[], metadata=TurnMetadata(origin="workbench"))
        timed_out = False
        result = None
        try:
            # Caller-owned delivery: the result is DISCARDED, which is the
            # whole null-delivery mechanism. The wall-clock ceiling is a code
            # budget (spec §3) — an overrun is cancelled, then still recorded
            # and committed below, so her files keep whatever she finished.
            result = await asyncio.wait_for(_dispatch(turn),
                                            timeout=_session_timeout_s(c))
        except asyncio.TimeoutError:
            timed_out = True
            logger.warning("workbench: session exceeded its wall clock; stopped")
        except Exception as exc:
            self.last_error = f"session dispatch failed: {exc}"
            logger.warning("workbench: %s", self.last_error)
            return {"ran": False, "why": self.last_error}

        text = str(getattr(result, "response_text", "") or "")
        share = _extract_share(text) if not timed_out else None
        shared = ""
        if share:
            shared = await _deliver_share(share, owner_id, c)
        signoff = _signoff_of(text)
        if timed_out:
            signoff = "(session ran out of clock)"
        _commit_workspace(f"workbench: {signoff}")
        outcome = "timeout" if timed_out else "ran"
        workbench_store.write_session(record={
            "started": started,
            "duration_s": round(time.time() - started, 1),
            "signoff": signoff,
            # sent | pending | thread | room | dropped, "" when no SHARE line
            # (pre-2026-08-18 rows carry the old bool)
            "shared": shared,
            "outcome": outcome,
        })
        _remember_session(
            started=started, signoff=signoff, share=share, shared=shared,
            outcome=outcome, logbook=_logbook_lines_since(started),
        )
        if intent_seen:
            try:
                from ai.daydream_store import consume_intent
                consume_intent()
            except Exception:
                pass
        self.last_session_at = time.time()
        if timed_out:
            self.last_error = "session timeout"
            return {"ran": False, "why": "session timeout"}
        self.last_error = ""
        _notice_own_work()
        _notice_daydream()
        essential_logger.info("workbench: session done (%s)", signoff[:80])
        return {"ran": True, "share": share}


workbench_engine = WorkbenchEngine()
