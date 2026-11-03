"""The daydream lane (Wanting stage 2, re-spec §4) — her own tick, beside
the monologue, with the opposite charter: allowed to want, expected to
plan. The monologue notices HIM; this lane wants for HER. Its outputs are
a closed set — a private thought, a quest write, a curiosity seed, a
workbench intent — and none of them reach him by themselves.

This module half is the TICK (assembly → one LLM call → validated
writers). The engine (wake machinery, occasioned not metronomic) lives in
the DaydreamEngine class below it (Task 8).
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.setup_mode import lane_idle

logger = logging.getLogger(__name__)
essential_logger = logging.getLogger("palli.essential")

_TICK_TIMEOUT_S = 180.0  # patient-lane envelope: 2x60s ceiling + headroom (2026-08-09)
_VALID_OPS = ("plant", "progress", "done", "release", "promote")

# Deterministic occasion rendering — the events_block precedent
# (presentation of code-emitted kinds; the owner's exemption class).
_OCCASION_PHRASES: Dict[str, str] = {
    "wish_planted": "they planted or changed a wish in your book",
    "own_work_moved": "you just finished time at your workbench",
    "curiosity_returned": "one of your dives came back with something",
    "heartbeat": "a quiet check-in — nothing external moved",
    "manual": "a hand-run probe woke you",
}


def _occasion_block(woke_on: List[Dict[str, Any]]) -> str:
    lines: List[str] = []
    seen: set = set()
    for e in woke_on:
        kind = str(e.get("kind") or "")
        if kind in seen:
            continue
        seen.add(kind)
        lines.append(f"- {_OCCASION_PHRASES.get(kind) or kind.replace('_', ' ')}")
    return "\n".join(lines) or "- a quiet check-in — nothing external moved"


def _affordances_block() -> str:
    """What she has, in one deterministic map (re-spec §4.4). Two pinned
    laws: capabilities named ambiently, mechanics never (no paths, no
    shell verbs); states what is true, never what to want."""
    from core.config import nas_root

    # The NAS is one knob (PALLI_NAS_ROOT); an install may have none, and
    # this block states what IS true — so with no NAS neither her folder
    # nor the drive at large is claimed. The capability is named, never
    # the path (the pinned law above).
    territory = (
        "Your territory: your workspace, your own NAS folder, and the NAS "
        "at large — readable, and never yours to move or delete."
        if nas_root() else
        "Your territory: your workspace."
    )
    parts = [
        "Your hands: your workbench shell, your workspace and your git, "
        "the web, your own record through introspect, your pen "
        "(note_to_self).",
        territory,
        "Your machines: the household's printers and servers exist and can "
        "be seen; what to make of them is nobody's assignment.",
    ]
    try:
        from ai.workbench import sessions_spent_today
        from core.turn.companion_flags import companion_settings

        comp = companion_settings()
        cap = int(getattr(comp, "own_work_daily_sessions", 3)) if comp else 3
        if cap > 0:
            # The budget's own count, by its own day (Batch 6, ruling R59).
            done = sessions_spent_today(comp)
            parts.append(
                f"Your time: {max(0, cap - done)} workbench session(s) "
                "left today."
            )
        else:
            parts.append("Your time: as many workbench sessions as you want today.")
    except Exception:
        pass
    return "\n".join(f"- {p}" for p in parts)


async def _wonders_block() -> str:
    """Wonder+taste stakes ONLY (re-spec §4.3): the §2 door's seed
    material. Hopes and worries are him-shaped and stay in his lane."""
    try:
        from ai.stake_ledger import ledger_enabled, open_stakes

        if not ledger_enabled():
            return "(none right now)"
        stakes = await open_stakes(limit=12)
    except Exception:
        return "(none right now)"
    keep = [s for s in stakes if s.kind in ("wonder", "taste")]
    if not keep:
        return "(none right now)"
    return "\n".join(f"- ({s.kind}) {s.stake}" for s in keep)


def _deeds_block() -> str:
    try:
        from ai.recent_deeds import read_recent_deeds

        rows = read_recent_deeds(limit=8)
    except Exception:
        rows = []
    if not rows:
        return "(nothing on record)"
    now = time.time()
    lines = []
    for d in rows:
        mins = max(0, int((now - float(d.get("at") or 0.0)) // 60))
        when = f"~{mins}m ago" if mins < 120 else f"~{mins // 60}h ago"
        lines.append(f"- {when} — {d.get('detail')}")
    return "\n".join(lines)


def _recent_block() -> str:
    from ai.daydream_store import read_recent

    entries = read_recent(days_back=3, limit=4)
    if not entries:
        return "(none yet — this lane is new)"
    now = time.time()
    lines = []
    for e in entries:
        hours = max(0.0, (now - e.written_at) / 3600.0)
        when = f"~{int(hours)}h ago" if hours >= 1 else "just now"
        lines.append(f"- ({when}) {e.text[:240]}")
    return "\n".join(lines)


def _workbench_block() -> str:
    """Her logbook, newest sections first-kept (2026-08-18). Until today this
    was the FIRST 1,200 chars of an append-only file — July 28 forever — so
    the daydream kept asking for letters already written ("the daydream
    lags again", her 08-10/11/12/13/14 sign-offs). Same fix as the session
    prompt's read."""
    try:
        from ai.workbench import WORKSPACE_ROOT, _read_logbook_recent

        body = _read_logbook_recent(path=WORKSPACE_ROOT / "WORKBENCH.md", cap=1600)
    except Exception:
        return "(no workbench yet)"
    body = body or "(no workbench yet)"
    # The line you left for your next session — and whether a session took
    # it up (2026-08-18). Without this the lane rewrote the same intent
    # for a week ("the daydream still says the letter's unwritten").
    try:
        from ai.daydream_store import read_intent_record

        rec = read_intent_record()
        text = str(rec.get("text") or "")
        if text:
            now = time.time()
            wa = rec.get("written_at")
            ca = rec.get("consumed_at")
            age = ""
            if wa:
                hrs = (now - float(wa)) / 3600.0
                age = "just now" if hrs < 1 else f"~{int(hrs)}h ago"
            if ca:
                since = (now - float(ca)) / 3600.0
                status = ("taken up by a session " +
                          ("just now" if since < 1 else f"~{int(since)}h ago") +
                          " — leave it unless something NEW pulls")
            else:
                status = "not yet taken up — no need to restate it"
            body += (f"\n\nThe line you left for your next session"
                     f"{f' ({age})' if age else ''}: \"{text}\" — {status}.")
    except Exception:
        pass
    return body


def _interests_block() -> str:
    try:
        from ai.passions import passions_enabled, read_interests_text

        if not passions_enabled():
            return "(none written yet)"
        return read_interests_text() or "(none written yet)"
    except Exception:
        return "(none written yet)"


async def _presence_line() -> str:
    """The daydream's light timing signal. The machine's state is said only
    while something knows it: the gate is the present-moment gather's
    two-hour rule (``_machine_line`` — a fresh "ready" poller stamp or
    keyboard stamp), imported, not copied. A stale or absent reading says
    "no signal.", the words this line already used when it had nothing.
    Batch 3 item 1.5, ruling R17: this line read the raw ``machine_state``,
    so the same frozen snapshot that printed "Machine: idle." for six weeks
    reached her daydreams as "their machine is idle." too."""
    try:
        from ai.present_moment_gather import _machine_line
        from core.home_presence import home_presence_service
        from core.local_mode import get_local_owner_refs

        refs = await get_local_owner_refs()
        snap = await home_presence_service.get_context_snapshot(
            owner_user_id=int(refs.get("app_user_id") or 1)
        )
        if not _machine_line(snap, now=time.time()):
            return "no signal."
        return f"their machine is {str((snap or {}).get('machine_state') or 'unknown')}."
    except Exception:
        return "no signal."


async def _assemble_prompt(woke_on: List[Dict[str, Any]]) -> str:
    from ai.prompt_loader import prompt_loader
    from ai.quest_store import render_wishbook

    template = prompt_loader.load_prompt("daydream")
    if not template:
        raise RuntimeError("daydream prompt missing")
    return template.format(
        current_time=time.strftime("%A, %Y-%m-%d %I:%M %p"),
        occasion_block=_occasion_block(woke_on),
        quests_block=render_wishbook(),
        interests_block=_interests_block(),
        workbench_block=_workbench_block(),
        wonders_block=await _wonders_block(),
        deeds_block=_deeds_block(),
        recent_block=_recent_block(),
        affordances_block=_affordances_block(),
        presence_line=await _presence_line(),
    )


async def _llm_call(rendered: str) -> str:
    """Indirection seam (patched in tests) — one flash-class call."""
    from ai.llm_client import llm
    from ai.llm_routing import get_task_model

    return await asyncio.wait_for(
        llm.chat_completion(
            [{"role": "system", "content": rendered}],
            model=get_task_model(
                "daydream", fallback=get_task_model("reflection")
            ),
            temperature=0.7,
            max_tokens=1500,
            log_context="daydream_tick",
        ),
        timeout=_TICK_TIMEOUT_S,
    )


def _apply_quest_ops(ops: Any) -> List[str]:
    """Validate and apply, one bad op never blocking the rest (the stake
    ledger's per-op law). Returns human-short notes of what applied."""
    from ai import quest_store as qs

    applied: List[str] = []
    if not isinstance(ops, list):
        return applied
    for op in ops:
        if not isinstance(op, dict):
            continue
        kind = str(op.get("op") or "").strip().lower()
        if kind not in _VALID_OPS:
            logger.warning("daydream: op rejected (unknown %r)", kind)
            continue
        try:
            if kind == "plant":
                q, dormant = qs.plant(
                    text=str(op.get("text") or ""),
                    source="hers",
                    horizon=str(op.get("horizon") or "someday"),
                    provenance=str(op.get("provenance") or "a daydream"),
                )
                if q is not None:
                    applied.append(f"plant:{q.id}" + (":dormant" if dormant else ""))
            elif kind == "progress":
                if qs.add_progress(str(op.get("id") or ""), str(op.get("note") or "")):
                    applied.append(f"progress:{op.get('id')}")
            elif kind == "promote":
                if qs.set_status(str(op.get("id") or ""), "alive"):
                    applied.append(f"promote:{op.get('id')}")
            else:  # done | release
                status = "done" if kind == "done" else "released"
                if qs.set_status(
                    str(op.get("id") or ""), status,
                    note=str(op.get("note") or ""),
                ):
                    applied.append(f"{kind}:{op.get('id')}")
        except Exception as exc:
            logger.warning("daydream: op %s failed: %s", kind, exc)
    return applied


def _queue_curiosity(*, seed: str, owner_user_id: int) -> None:
    """Hand a topic to the existing research rail (re-spec §4.5). The
    'nas: ' prefix picks the NAS modality (Task 14). Never raises."""
    try:
        from ai.curiosity_research import maybe_queue_research

        topic = seed.strip()
        modality = "web"
        if topic.lower().startswith("nas:"):
            modality, topic = "nas", topic[4:].strip()
        maybe_queue_research(
            curiosity=topic, owner_user_id=owner_user_id, modality=modality,
        )
    except Exception as exc:
        logger.warning("daydream: curiosity seed dropped: %s", exc)


def _notice_moment() -> None:
    try:
        from ai.present_moment import notice

        notice("daydream_thought")
    except Exception:
        pass


async def run_daydream_tick(
    woke_on: List[Dict[str, Any]], *, owner_user_id: int
) -> Dict[str, Any]:
    """One full tick: sync his file, assemble the diet, one call, apply
    the closed output set. The empty tick writes NOTHING (the law)."""
    from ai import daydream_store
    from ai.quest_store import sync_wishes_file
    from core.llm_json import safe_parse_json_object

    started = time.time()
    try:
        sync_summary = sync_wishes_file()
    except Exception as exc:
        logger.warning("daydream: wishes sync failed: %s", exc)
        sync_summary = {}

    rendered = await _assemble_prompt(woke_on)
    try:
        response = await _llm_call(rendered)
    except Exception as exc:
        logger.warning("daydream: tick call failed: %s", exc)
        return {"wrote": False, "error": str(exc)[:120]}

    parsed = safe_parse_json_object(response or "") or {}
    thought = " ".join(str(parsed.get("daydream") or "").split())
    applied = _apply_quest_ops(parsed.get("quest_ops"))

    seed = str(parsed.get("curiosity_seed") or "").strip()
    if seed:
        _queue_curiosity(seed=seed, owner_user_id=owner_user_id)

    intent = " ".join(str(parsed.get("workbench_intent") or "").split())
    if intent:
        daydream_store.write_intent(intent)
        # 2026-08-18: a line for her bench is an occasion to sit at it — the
        # chain dive → daydream → intent → session, one thing at a time.
        try:
            from ai.workbench import notice as _bench_notice

            _bench_notice("daydream_intent")
        except Exception:
            pass

    wrote = False
    if thought:
        daydream_store.write_entry(text=thought, meta={
            "woke_on": sorted({str(e.get("kind")) for e in woke_on}),
            "quest_ops_applied": applied,
            "curiosity_seed": bool(seed),
            "workbench_intent": bool(intent),
            "wishes_sync": {k: len(v) for k, v in (sync_summary or {}).items()},
            "duration_seconds": round(time.time() - started, 2),
        })
        wrote = True
        _notice_moment()

    return {"wrote": wrote, "applied": applied, "seed": bool(seed),
            "intent": bool(intent)}


# ── the engine: occasions, not a metronome (re-spec §4.1/§4.6) ──────────

def _count_today() -> int:
    from ai.daydream_store import count_today

    return count_today()


def _wishes_file_path() -> Path:
    from ai.quest_store import _wishes_path

    return _wishes_path(None)


def notice(kind: str, **fields: Any) -> None:
    """Fire-and-forget occasion. MUST never raise into callers (the
    present-moment notice contract). Emitters: the workbench's session
    end, the curiosity rail's completion; the engine itself notices
    WISHES.md changes."""
    try:
        daydream_engine._ingest(str(kind), dict(fields))
    except Exception:  # noqa: BLE001 — caller safety is the whole point
        pass


class DaydreamEngine:
    def __init__(self) -> None:
        self.is_running = False
        self.last_tick_at: Optional[float] = None
        self.last_error: str = ""
        self._pending: List[Dict[str, Any]] = []
        self._wake = asyncio.Event()
        self._task: Optional[asyncio.Task] = None
        self._deferred_wake_at: Optional[float] = None
        self._last_heartbeat_at: float = 0.0
        self._wishes_mtime: Optional[float] = None

    def _settings(self):
        try:
            from core.turn.companion_flags import companion_settings

            return companion_settings()
        except Exception:
            return None

    def _enabled(self) -> bool:
        # Fail CLOSED (the present-moment divergence): an unbootable
        # settings snapshot must never start her lane.
        c = self._settings()
        return c is not None and bool(getattr(c, "wanting", False))

    def _knob(self, name: str, default: int) -> float:
        c = self._settings()
        try:
            return float(getattr(c, name, default)) if c is not None else float(default)
        except Exception:
            return float(default)

    # ── ingestion ────────────────────────────────────────────────────
    def _ingest(self, kind: str, fields: Dict[str, Any]) -> None:
        if not self.is_running:
            return
        self._pending.append({"kind": kind, "at": time.time(), **fields})
        if len(self._pending) > 50:
            self._pending = self._pending[-50:]
        self._wake.set()

    def _poll_wishes_mtime(self) -> None:
        """A stat costs nothing; a changed WISHES.md is an occasion. The
        FIRST sighting only sets the baseline — booting must not fire a
        phantom wish_planted for a file that predates the boot."""
        try:
            path = _wishes_file_path()
            mtime = path.stat().st_mtime if path.exists() else None
        except OSError:
            return
        if self._wishes_mtime is None:
            self._wishes_mtime = mtime if mtime is not None else -1.0
            return
        if mtime is not None and mtime != self._wishes_mtime:
            self._wishes_mtime = mtime
            self._ingest("wish_planted", {})
        elif mtime is None:
            self._wishes_mtime = -1.0

    # ── the wake decision (pure; the PM _loop_once shape) ────────────
    def _decide(self, now: float) -> tuple[bool, List[Dict[str, Any]]]:
        # Setup mode (first door, 2026-09-17) is the organ-off branch: no
        # brain, nothing to daydream with; the heartbeat refresh paces the loop.
        if not self._enabled() or lane_idle("daydream"):
            self._pending.clear()
            self._deferred_wake_at = None
            self._last_heartbeat_at = now
            return False, []
        gap_s = self._knob("daydream_min_gap_minutes", 45) * 60.0
        heartbeat_s = self._knob("daydream_heartbeat_minutes", 180) * 60.0
        cap = int(self._knob("daydream_daily_max", 8))
        since_last = now - (self.last_tick_at or 0.0)

        fire = False
        woke: List[Dict[str, Any]] = []
        if self._pending and since_last >= gap_s:
            fire, woke = True, list(self._pending)
            self._pending.clear()
            self._deferred_wake_at = None
            self._last_heartbeat_at = now
        elif self._pending:
            # THE DEFERRING GAP: suppressed wakes fire at gap expiry with
            # the coalesced bundle — never cancelled (the PM law).
            self._deferred_wake_at = (self.last_tick_at or now) + gap_s
            self._last_heartbeat_at = now
            return False, []
        elif now - self._last_heartbeat_at >= heartbeat_s:
            self._last_heartbeat_at = now
            fire, woke = True, [{"kind": "heartbeat", "at": now}]

        if not fire:
            return False, []
        # cap 0 = UNLIMITED (the 07c convention; the flag is the off switch).
        if cap > 0 and _count_today() >= cap:
            for e in woke:
                self._pending.append(e)   # lossless hold, the PM pattern
            logger.info("daydream: daily max reached; holding %d occasion(s)",
                        len(self._pending))
            return False, []
        return True, woke

    def _next_deadline(self, now: float) -> float:
        heartbeat_s = self._knob("daydream_heartbeat_minutes", 180) * 60.0
        candidates = [self._last_heartbeat_at + heartbeat_s]
        if self._deferred_wake_at:
            candidates.append(self._deferred_wake_at)
        return max(30.0, min(candidates) - now)

    # ── lifecycle ────────────────────────────────────────────────────
    async def start(self) -> None:
        if self.is_running:
            return
        self.is_running = True
        self._task = asyncio.create_task(self._loop(), name="daydream")
        essential_logger.info("Daydream engine started")

    async def stop(self) -> None:
        self.is_running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None
        essential_logger.info("Daydream engine stopped")

    def get_status(self) -> Dict[str, Any]:
        return {"is_running": self.is_running, "enabled": self._enabled(),
                "last_tick_at": self.last_tick_at,
                "pending": len(self._pending),
                "last_error": self.last_error}

    async def _loop(self) -> None:
        await asyncio.sleep(90)          # boot delay — never compete with startup
        self._last_heartbeat_at = time.time()
        while self.is_running:
            try:
                try:
                    await asyncio.wait_for(
                        self._wake.wait(),
                        timeout=self._next_deadline(time.time()),
                    )
                except asyncio.TimeoutError:
                    pass
                self._wake.clear()
                if not self.is_running:
                    return
                self._poll_wishes_mtime()
                fire, woke = self._decide(time.time())
                if fire:
                    from core.local_mode import get_local_owner_refs

                    refs = await get_local_owner_refs()
                    owner_id = int(refs.get("app_user_id")
                                   or refs.get("palli_user_id") or 1)
                    await run_daydream_tick(woke, owner_user_id=owner_id)
                    self.last_tick_at = time.time()
                    self.last_error = ""
            except asyncio.CancelledError:
                break
            except Exception as exc:
                self.last_error = str(exc)
                logger.error("daydream loop error: %s", exc, exc_info=True)
                await asyncio.sleep(300)


daydream_engine = DaydreamEngine()
