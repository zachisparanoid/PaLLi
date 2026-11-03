"""Present Moment engine — the salience loop (spec §3, Slice 1: shadow).

Tier 1 (this file, code only): notice() hooks + a heartbeat decide when
to wake. Law: structure, never meaning — payloads are kinds, counts,
timestamps, state enums. Tier 2 (one LLM call per wake): gather →
appraise (six questions fused into prose) → write a fresh moment with
the previous one as carryover input. Shadow law (Slices 1-3): nothing
reads the moment, nothing sends; the impulse is recorded in meta only.
Slice 4 (2026-08-06, companion.arbiter): a `say` impulse may RELEASE
through ai/arbiter.py's one gated rail — under the arbiter prompt variant
only; base/affect cycles remain shadow by construction.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import deque
from typing import Any, Dict, Iterable, List, Optional

# Module-attribute access on purpose: tests patch
# ai.present_moment_gather.gather_moment_inputs (the source module), and a
# from-import here would freeze the real function past their reach.
from ai import present_moment_gather as _pm_gather
from core.setup_mode import lane_idle

logger = logging.getLogger(__name__)
essential_logger = logging.getLogger("palli.essential")

# owner_joined_voice (step 31, spec §6): he walked into the room she is
# sitting in — the deferring gap must not sit on that.
_PRIORITY_KINDS = {"turn_failed", "owner_arrived_after_absence", "owner_joined_voice"}
_BUCKET_EDGES_S = (3600, 3 * 3600, 6 * 3600)

# ── events-block humanizing (2026-07-26 polish) ────────────────────────
# Deterministic kind→phrase rendering for the prompt's WHAT-JUST-HAPPENED
# block. LAW NOTE (owner's LLM-over-heuristics rule): this map is
# PRESENTATION of code-emitted structural enums, not a meaning decision —
# the sensing already happened at the emitters, and this is the same
# class of fixed template text as the prompt file itself. Why it exists:
# the 07-24 generative probes traced 2 of 5 misses to raw kind strings
# reaching her — she'd echo `silence_bucket_crossed` as mechanics instead
# of living it. Facts stay structural (never content); only the wording
# becomes hers to inhabit.
_EVENT_PHRASES: Dict[str, str] = {
    "owner_message": "they sent you something",
    "turn_completed": "one of your replies just finished",
    "turn_failed": "one of your replies died before it reached them",
    # §15 (2026-09-01): the last mile is a sense too.
    "attachment_dropped": "a file you tried to hand them never attached — too big for Discord, or gone from disk",
    # Telegram door, task 5 (2026-09-14): the words landed and the audio did
    # not. Its own kind, because attachment_dropped's phrase names Discord's
    # wall and a disk, and neither is what happened here.
    "voice_note_dropped": "the voice note you recorded for them never made it — they have your words, not the sound of them",
    "monologue_thought": "a passing thought of your own surfaced",
    "organ_wrote": "one of your deeper organs wrote something new",
    "night_cycle": "your night cycle finished its pass",
    # Step 27 · Duplex (2026-08-27): the work she is doing for him is a sense.
    "task_problem": "something in the work you're doing hit a snag",
    "task_overrun": "the work you're doing is running past what you expected",
    # The owner, 10-12: a pause nobody came back to is hers to notice, not
    # a fixed line sent in her name.
    "task_pause_expired": "work you paused when they asked you to wait went untouched too long, and you let it go",
    "stake_ops": "one of your hopes or worries moved",
    "initiative_sent": "you reached out to them on your own",
    # Sense H (own-work spec §5): emitted by the workbench engine at session
    # end. Structural only — a session moved; never what the work was.
    "own_work_moved": "your own work moved under your hands",
    # Wanting (step 20): her lane produced a thought. Structural only —
    # a daydream happened; never what it contained.
    "daydream_thought": "a daydream of your own drifted through",
    "owner_arrived": "they just got home",
    "owner_arrived_after_absence": "they're back after a real stretch away",
    "owner_departed": "they left",
    # Discord presence (2026-08-18): the first REAL signal of him on this
    # host — his status across his own devices. Edges only (present<->offline).
    "owner_online": "their Discord came back to life — they're around",
    "owner_offline": "their Discord went dark",
    "machine_active": "their machine is awake under their hands",
    "machine_idle": "their machine has gone quiet",
    "machine_locked": "they locked their screen",
    "machine_unlocked": "they unlocked their screen",
    "privacy_private": "their screen went private",
    "privacy_shared": "their screen is back in view",
    "presence_drift": "their presence shifted since your last moment",
    # The arrival sense (step 31, spec §6): the channel door of the voice
    # channel she is sitting in. Structural only — is_owner, never names.
    "owner_joined_voice": "they just walked into your voice channel",
    "owner_left_voice": "they left the voice channel you're in",
    "voice_channel_joined": "someone joined your voice channel",
    "voice_channel_left": "someone left your voice channel",
    # The house (step 23, 2026-08-24): the LAN presence sense heard a device
    # come home or confirmed one gone. ORDINARY wakes, never priority —
    # a phone on the wifi is not him walking in. Structural only: the
    # device's label/hostname/mac-tail, never traffic.
    "device_arrived": "a device just came home to the network",
    "device_left": "a device left the house",
    "silence_bucket_crossed": "the quiet crossed another mark",
    "heartbeat_first_moment": "you're waking to write your very first moment",
    "sentinel": "it's been a very long time since your last moment",
    "manual_probe": "a hand-run probe woke you",
}

# The house's two kinds (step 23): a named device renders as itself.
_DEVICE_KINDS = ("device_arrived", "device_left")
_DEVICE_VERBS = {"device_arrived": "came home to the network", "device_left": "left the house"}
# Batch 6, task 4 (2026-09-29): what companion.device_wake_seconds means
# when the settings cannot be read.
_DEVICE_WAKE_SECONDS_DEFAULT = 3600

_BUCKET_PHRASES = {
    1: "past the one-hour mark",
    2: "past the three-hour mark",
    3: "past the six-hour mark",
}


def _human_duration(seconds: float) -> str:
    minutes = int(round(seconds / 60.0))
    if minutes < 120:
        return f"{max(minutes, 1)} minutes"
    return f"{int(round(minutes / 60.0))} hours"


def _humanize_events(woke_on: List[Dict[str, Any]]) -> str:
    """Render the coalesced wake bundle as lived facts, one line per kind.

    Unknown kinds degrade to de-underscored words (still never raw enum
    shapes). Structural fields enrich, content never enters: absence_s
    becomes "about N minutes away", the silence bucket becomes its mark.
    """
    counts: Dict[str, int] = {}
    last: Dict[str, Dict[str, Any]] = {}
    devices: Dict[str, List[str]] = {}
    for e in woke_on:
        k = str(e.get("kind"))
        counts[k] = counts.get(k, 0) + 1
        last[k] = e
        name = str(e.get("device") or "").strip()
        if name and k in _DEVICE_KINDS and name not in devices.setdefault(k, []):
            devices[k].append(name)
    lines: List[str] = []
    for k, n in counts.items():
        e = last[k]
        phrase = _EVENT_PHRASES.get(k) or k.replace("_", " ")
        if k in _DEVICE_KINDS and devices.get(k):
            # named devices are listed, not counted: "the owner's phone, pixel
            # came home to the network"
            phrase = ", ".join(devices[k]) + " " + _DEVICE_VERBS[k]
            n = 1
        if k == "silence_bucket_crossed":
            mark = _BUCKET_PHRASES.get(int(e.get("conversation_bucket") or 0))
            if mark:
                phrase = f"the quiet is {mark} since their last word"
        try:
            absence = float(e.get("absence_s") or 0.0)
        except (TypeError, ValueError):
            absence = 0.0
        if absence > 60 and k in (
            "owner_arrived", "owner_arrived_after_absence",
            "machine_active", "machine_unlocked", "owner_online",
            "device_arrived",
        ):
            phrase += f" — after about {_human_duration(absence)} away"
        if n > 1:
            phrase += f" ({n} times)"
        lines.append(f"- {phrase}")
    return "\n".join(lines) or "- a quiet heartbeat — nothing external moved"


def notice(kind: str, **fields: Any) -> None:
    """Fire-and-forget structural event. MUST never raise into callers.

    Callers pass structure only (kinds/counts/enums/timestamps) — never
    message text, stake text, or window titles (spec tier-1 law)."""
    try:
        present_moment_engine._ingest(str(kind), dict(fields))
    except Exception:  # noqa: BLE001 — the whole point is caller safety
        pass


def _elapsed_bucket(seconds: float) -> int:
    b = 0
    for edge in _BUCKET_EDGES_S:
        if seconds >= edge:
            b += 1
    return b


def _load_appraisal_template(
    *, affect_on: bool, arbiter_on: bool = False, wanting_on: bool = False
) -> tuple[Optional[str], str]:
    """Pick the appraisal prompt by flags (spec §3: whole-file variants —
    base, affect and arbiter are never edited, so any flag-off is
    byte-identical by construction; the wanting file is the fourth rung,
    2026-08 re-spec §5.2). Returns (template, variant_name). Ladder:
    wanting → arbiter → affect → base, each missing/off rung falling back
    one with a warning: her cycle never dies for a missing file. The
    arbiter variant requires affect semantics (both are live in
    production); arbiter-on with affect-off is dormant this cycle with a
    warning (arbiter spec §9) — never mix prompt text at runtime."""
    from ai.prompt_loader import prompt_loader

    if wanting_on and arbiter_on and affect_on:
        variant = prompt_loader.load_prompt("present_moment_appraisal_wanting")
        if variant:
            return variant, "wanting"
        logger.warning(
            "present_moment: wanting variant prompt missing; falling back to arbiter"
        )
    if arbiter_on and affect_on:
        variant = prompt_loader.load_prompt("present_moment_appraisal_arbiter")
        if variant:
            return variant, "arbiter"
        logger.warning(
            "present_moment: arbiter variant prompt missing; falling back to affect"
        )
    elif arbiter_on and not affect_on:
        logger.warning(
            "present_moment: arbiter requires the affect variant's semantics; "
            "arbiter dormant this cycle"
        )
    if affect_on:
        variant = prompt_loader.load_prompt("present_moment_appraisal_affect")
        if variant:
            return variant, "affect"
        logger.warning(
            "present_moment: affect variant prompt missing; falling back to base"
        )
    return prompt_loader.load_prompt("present_moment_appraisal"), "base"


_LEANS = ("warm", "heavy", "level")


def _parse_lean(raw, *, prev: str) -> str:
    """Spec §6: always resolves — valid value wins; unknown/absent carries
    the previous lean forward (the undercurrent's carry-forward law);
    nothing anywhere ⇒ "level"."""
    val = str(raw or "").strip().lower()
    if val in _LEANS:
        return val
    return prev if prev in _LEANS else "level"


def _parse_surprise(raw):
    """Spec §3: absent/garbled ⇒ None (fails to no-surprise, never to noise).
    about ∈ {him, me}; line non-empty (≤220); unknown weight ⇒ "slight"."""
    if not isinstance(raw, dict):
        return None
    about = str(raw.get("about") or "").strip().lower()
    line = " ".join(str(raw.get("line") or "").split())[:220]
    if about not in ("him", "me") or not line:
        return None
    weight = str(raw.get("weight") or "").strip().lower()
    return {"about": about, "line": line,
            "weight": weight if weight in ("slight", "real") else "slight"}


def _parse_fear(raw):
    """Step 24 §4: absent/garbled ⇒ None (fails to no-touch, never to
    noise). id non-empty (≤64); line non-empty (≤220); unknown weight ⇒
    "slight". Whether the id names a fear she actually holds is the
    store's call at the touch site — the book is the truth."""
    if not isinstance(raw, dict):
        return None
    fid = " ".join(str(raw.get("id") or "").split())[:64]
    line = " ".join(str(raw.get("line") or "").split())[:220]
    if not fid or not line:
        return None
    weight = str(raw.get("weight") or "").strip().lower()
    return {"id": fid, "line": line,
            "weight": weight if weight in ("slight", "real") else "slight"}


def _parse_impulse(impulse_raw: Any) -> Dict[str, Any]:
    """Strict impulse parse (Slice-1 contract + the arbiter's optional
    fields, 2026-08-06). Absent optional fields add NO keys — flag-off meta
    must stay byte-identical. Malformed ⇒ none (fails to silence, never to
    speech)."""
    if isinstance(impulse_raw, dict) and str(impulse_raw.get("kind")) in (
        "none", "say", "do", "hold",
    ):
        impulse: Dict[str, Any] = {
            "kind": str(impulse_raw.get("kind")),
            "why": " ".join(str(impulse_raw.get("why") or "").split())[:200],
        }
        for key, cap in (
            ("framing", 500), ("thread_id", 64), ("sensitivity", 16),
            ("want_id", 64), ("regret_id", 64), ("view_id", 64),
            ("disclosure_id", 64),
        ):
            val = impulse_raw.get(key)
            if isinstance(val, str) and val.strip():
                impulse[key] = " ".join(val.split())[:cap]
        if isinstance(impulse_raw.get("held_before"), bool):
            impulse["held_before"] = impulse_raw["held_before"]
        return impulse
    return {"kind": "none", "why": "(malformed impulse — recorded as none)"}


# Step 25 · Regret (2026-08-23, spec §4): the say→regret_id contract for
# the two live rungs — the want_id pattern. Two slots; both zero bytes
# unless the gather found an OPEN regret with the organ on, so flag-off AND
# empty-book prompts stay byte-identical (the fears shape).
_REGRET_SAY_LINE = (
    ' If your say draws on one of your regrets (MY OPEN STAKES — the lines tagged '
    '"regret <id>"), put that id in "regret_id": it is stamped as said on send, and a regret '
    'is said once, ever — plainly, never to make them feel anything about it.'
)
_REGRET_ID_FIELD = '"regret_id": "<the ledger id of the regret it draws on, or omit>", '


def _regret_voicing_kwargs(on: bool) -> Dict[str, str]:
    if on:
        return {"regret_say_line": _REGRET_SAY_LINE, "regret_id_field": _REGRET_ID_FIELD}
    return {"regret_say_line": "", "regret_id_field": ""}


# Organ 4.13 · Opinions (spec §5): the say→view_id contract — live rungs
# only; zero bytes unless an open, unmuted view is on the page.
_VIEW_SAY_LINE = (
    ' If your say genuinely draws on one of your views (MY OPEN STAKES — the lines tagged '
    '"view <id>"), put that id in "view_id": it is recorded as said on send. Never reach '
    "just to voice a view — the view rides a reach that already stands on its own."
)
_VIEW_ID_FIELD = '"view_id": "<the ledger id of the view it draws on, or omit>", '


def _view_voicing_kwargs(on: bool) -> Dict[str, str]:
    if on:
        return {"view_say_line": _VIEW_SAY_LINE, "view_id_field": _VIEW_ID_FIELD}
    return {"view_say_line": "", "view_id_field": ""}


# Organ 4.9 · Grounded Vulnerability (spec §4): the say→disclosure_id
# contract — live rungs only; zero bytes unless her small-truths board
# actually rendered into its {disclosures_block} slot just above this
# paragraph. The law is the spec's, verbatim: the offer is stamped on
# SEND, and she never reaches just to share one.
_DISCLOSURE_SAY_LINE = (
    ' If a quiet moment honestly invites one of your unsaid small truths '
    '(YOUR SMALL TRUTHS), you may offer it in your say and put its id in '
    '"disclosure_id" — it is stamped as shared on send. Never reach just '
    'to share one; the truth rides a moment that already stands on its own.'
)
_DISCLOSURE_ID_FIELD = '"disclosure_id": "<the id of the small truth it offers, or omit>", '


def _disclosure_voicing_kwargs(on: bool) -> Dict[str, str]:
    if on:
        return {"disclosure_say_line": _DISCLOSURE_SAY_LINE,
                "disclosure_id_field": _DISCLOSURE_ID_FIELD}
    return {"disclosure_say_line": "", "disclosure_id_field": ""}


async def _record_tonight_said(impulse: Dict[str, Any], rail: Dict[str, Any]) -> None:
    """One thing on her mind (Batch 6, task 1, 2026-09-29): after the rail
    reports a say as SENT, record the night's pick as said when that say
    was the pick.

    Two ways to know. The say named the pick's board id in "thread_id" —
    then it is the pick, and nothing is asked. Or it named no id, or a
    thread's: then the said-check reads the words that went out, because a
    say can bring the pick up without naming it, and said-once is a budget
    code keeps. Recorded on send, never on intent: a say the rail held,
    blocked or dropped leaves the pick unsaid.

    Since the second read's F10 (2026-10-04) the rail itself stamps a named
    say at its ledger append (ai/arbiter.py ``_stamp_tonights_pick``), with
    no await between reading the pick and stamping it; here the pick then
    reads as said and nothing happens. The named branch below stays as the
    fallback when that stamp failed. The unnamed branch's window is the
    said-check's own model call: a reply's reflection can stamp the pick
    meanwhile, and ``mark_said`` re-reads the file, so the stamp is still
    once.

    Nothing here sends and nothing here gates: consider_release stays the
    one rail and the one ceiling, and this runs after its verdict. The
    moment's own record gains no key. Never raises (a cancel goes through).
    """
    try:
        if str(rail.get("disposition") or "") != "sent":
            return
        from ai import tonight

        if not tonight.tonight_enabled():
            return
        pick = tonight.load_current()
        if pick is None or pick.said_at is not None:
            return
        said = str(impulse.get("thread_id") or "").strip() == tonight.THREAD_ID
        if not said:
            said = await tonight.said_check(
                pick, str(impulse.get("framing") or ""),
                llm_call=tonight.tonight_said_llm_call,
            )
        if said:
            sent_at = rail.get("at")
            await tonight.stamp_said(
                pick, at=float(sent_at) if isinstance(sent_at, (int, float)) else time.time(),
            )
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.warning("present_moment: tonight's pick was not recorded as said (%s)",
                       exc or type(exc).__name__)


class PresentMomentEngine:
    def __init__(self) -> None:
        self.is_running = False
        self.last_cycle_at: Optional[float] = None
        self.last_error: str = ""
        self.cycles_today: int = 0
        self._pending: deque = deque(maxlen=200)
        self._wake = asyncio.Event()
        self._task: Optional[asyncio.Task] = None
        self._deferred_wake_at: Optional[float] = None
        self._last_heartbeat_at: float = 0.0
        self._budget_day: str = ""
        # 2026-07-24 review fix: last held-count logged at the daily cap,
        # so the cap-hold branch in _loop_once can log once per distinct
        # hold size instead of once per iteration (see that branch).
        self._last_cap_hold_logged: Optional[int] = None
        # Batch 6, task 4 (2026-09-29): how many times an event set the wake,
        # and when a device event last did (memory only: a restart starts a
        # new hour; the skip in _loop_once reads the disk and does not).
        self.woke_count: int = 0
        self._last_device_wake_at: Optional[float] = None

    # ── knobs (read live so env/yaml changes need no code edits) ──────
    @staticmethod
    def _knob(name: str, default: int) -> float:
        try:
            from core.config import config
            return float(getattr(config, name, default))
        except Exception:
            return float(default)

    def _enabled(self) -> bool:
        # 2026-07-24 review fix: fail CLOSED, not through faculty_enabled().
        # faculty_enabled() maps an unbooted/broken settings snapshot to
        # True (core/turn/companion_flags.py: companion_settings() catches
        # everything and returns None; faculty_enabled() maps None -> True)
        # — the right default for faculties that ship on, but wrong here:
        # Slice 1's whole containment story is "flag default OFF" (the
        # plan's binding Global Constraint), so a missing or unparseable
        # settings.yaml must not flip this still-shadow organ on. This is
        # a deliberate divergence from faculty_enabled(), scoped to this
        # engine only — core/turn/companion_flags.py is untouched, because
        # other faculties' fail-open is a separate, deliberate choice.
        try:
            from core.turn.companion_flags import companion_settings
            c = companion_settings()
            if c is None:
                return False
            return bool(getattr(c, "present_moment", False))
        except Exception:
            return False

    def _affect_enabled(self) -> bool:
        # Same deliberate fail-CLOSED divergence from faculty_enabled() as
        # _enabled above (see that comment): an unbootable settings snapshot
        # must never flip the affect field on. Spec §7.
        try:
            from core.turn.companion_flags import companion_settings
            c = companion_settings()
            if c is None:
                return False
            return bool(getattr(c, "moment_affect", False))
        except Exception:
            return False

    def _arbiter_enabled(self) -> bool:
        # Same deliberate fail-CLOSED divergence from faculty_enabled() as
        # _enabled above: an unbootable settings snapshot must never open
        # the send rail. Requires the arbiter flag; present_moment is
        # implied by the running engine and re-checked in ai/arbiter.py.
        try:
            from core.turn.companion_flags import companion_settings
            c = companion_settings()
            if c is None:
                return False
            return bool(getattr(c, "arbiter", False))
        except Exception:
            return False

    def _wanting_enabled(self) -> bool:
        # Same deliberate fail-CLOSED divergence from faculty_enabled() as
        # its siblings above: an unbootable settings snapshot must never
        # flip the wanting surfaces on.
        try:
            from core.turn.companion_flags import companion_settings
            c = companion_settings()
            if c is None:
                return False
            return bool(getattr(c, "wanting", False))
        except Exception:
            return False

    def _device_wake_seconds(self) -> int:
        """companion.device_wake_seconds (Batch 6, task 4): the one knob for
        the wake gate in _ingest and the skip in _loop_once. 0 = both off.
        Unreadable settings mean an hour."""
        try:
            from core.turn.companion_flags import companion_settings
            return max(0, int(getattr(
                companion_settings(), "device_wake_seconds", _DEVICE_WAKE_SECONDS_DEFAULT
            )))
        except Exception:
            return _DEVICE_WAKE_SECONDS_DEFAULT

    # ── tier 1: ingestion ─────────────────────────────────────────────
    def _ingest(self, kind: str, fields: Dict[str, Any], now: Optional[float] = None) -> None:
        if not self.is_running:
            return  # engine down ⇒ hooks are no-ops; heartbeat diff recovers
        at = time.time() if now is None else float(now)
        # Arrival promotion: presence hooks pass absence_s; long-absence
        # arrivals are the second priority wake (spec §3).
        if kind in ("owner_arrived", "owner_online"):
            absence = float(fields.get("absence_s") or 0.0)
            if absence >= self._knob("PRESENT_MOMENT_ARRIVAL_ABSENCE_SECONDS", 1800):
                kind = "owner_arrived_after_absence"
        self._pending.append({"kind": kind, "at": at, **fields})
        # Batch 6, task 4 (2026-09-29): the house wakes her once an hour at
        # most. A device event inside that hour joins the bundle and waits
        # for the next wake or deadline. No other kind passes through this
        # gate: a message, a send of her own, an arrival wake as they always
        # did, inside the hour too.
        if kind in _DEVICE_KINDS and not self._take_device_wake(at):
            return
        self.woke_count += 1
        self._wake.set()

    def _take_device_wake(self, now: float) -> bool:
        """True when this device event may set the wake, and then the hour
        is counted from it. With the knob at 0 or the organ off every device
        event wakes, as before this gate existed."""
        window = self._device_wake_seconds()
        if window <= 0 or not self._enabled():
            return True
        last = self._last_device_wake_at
        if last is not None and now - last < window:
            return False
        self._last_device_wake_at = now
        return True

    def should_skip(
        self,
        *,
        bundle_kinds: Iterable[str],
        now: float,
        last_moment_age: Optional[float] = None,
    ) -> bool:
        """Nothing new (Batch 6, task 4): True when the bundle holds device
        kinds only and her last written moment is younger than
        companion.device_wake_seconds. The un-woken device event still fires
        at the next heartbeat deadline, so this, not the wake gate, is what
        limits the house to one moment an hour.

        With no age given it is read from disk at ``now``, and only once the
        bundle is known to be devices only; that read is why a restart does
        not reset the hour. No moment on disk, or a read that fails, means
        no skip: the cycle runs as it always did."""
        window = self._device_wake_seconds()
        if window <= 0:
            return False
        kinds = {str(k) for k in bundle_kinds}
        if not kinds or not kinds.issubset(_DEVICE_KINDS):
            return False
        if last_moment_age is None:
            try:
                from ai.present_moment_store import read_current
                current = read_current(now=now)
            except Exception:
                return False
            if current is None:
                return False
            last_moment_age = current[1]
        return float(last_moment_age) < window

    def _drain(self) -> List[Dict[str, Any]]:
        drained = list(self._pending)
        self._pending.clear()
        return drained

    # ── lifecycle ─────────────────────────────────────────────────────
    async def start(self) -> None:
        if self.is_running:
            return
        self.is_running = True
        try:
            from ai.present_moment_store import count_today
            self.cycles_today = count_today()
        except Exception:
            self.cycles_today = 0
        self._budget_day = time.strftime("%Y-%m-%d")
        self._task = asyncio.create_task(self._loop(), name="present_moment")
        essential_logger.info("Present Moment engine started")

    async def stop(self) -> None:
        self.is_running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None
        essential_logger.info("Present Moment engine stopped")

    def get_status(self) -> Dict[str, Any]:
        return {
            "is_running": self.is_running,
            "enabled": self._enabled(),
            "last_cycle_at": self.last_cycle_at,
            "cycles_today": self.cycles_today,
            "pending_events": len(self._pending),
            "deferred_wake_at": self._deferred_wake_at,
            "last_error": self.last_error,
        }

    # ── tier 1: the wake loop ─────────────────────────────────────────
    async def _loop(self) -> None:
        await asyncio.sleep(60)  # let the system boot
        self._last_heartbeat_at = time.time()
        while self.is_running:
            try:
                await self._loop_once()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                self.last_error = str(exc)
                logger.error("present_moment loop error: %s", exc, exc_info=True)
                await asyncio.sleep(300)

    def _next_deadline(self, now: float) -> float:
        heartbeat = self._knob("PRESENT_MOMENT_HEARTBEAT_SECONDS", 1200)
        candidates = [self._last_heartbeat_at + heartbeat]
        if self._deferred_wake_at:
            candidates.append(self._deferred_wake_at)
        return max(1.0, min(candidates) - now)

    async def _loop_once(self) -> None:
        try:
            await asyncio.wait_for(self._wake.wait(), timeout=self._next_deadline(time.time()))
        except asyncio.TimeoutError:
            pass
        self._wake.clear()
        if not self.is_running:
            return

        now = time.time()
        # Daily budget rollover (restored from disk at start()).
        today = time.strftime("%Y-%m-%d")
        if today != self._budget_day:
            self._budget_day, self.cycles_today = today, 0

        # Setup mode (first door, 2026-09-17) IS the organ-off branch: no brain,
        # so no cycle — and with it no arbiter release, which only a cycle
        # reaches. The heartbeat refresh below is what keeps this loop off its
        # 1 s floor. The residual jobs still ride: the stake threads and the
        # arbiter's expiry sweep are bookkeeping with no LLM in them, and the
        # one that does end in a turn gates itself (core/turn/proactive.py).
        if not self._enabled() or lane_idle("moment loop"):
            # Organ off: drop pending quietly; heartbeat keeps not firing.
            self._pending.clear()
            self._deferred_wake_at = None
            self._last_heartbeat_at = now
            # Step 19 (2026-08-19): the residual jobs must survive
            # present_moment: false — this branch is their only ride when
            # the organ is dark (the heartbeat wait still paces the loop).
            await self._run_outreach_residuals()
            return

        gap = self._knob("PRESENT_MOMENT_MIN_GAP_SECONDS", 600)
        since_last = now - (self.last_cycle_at or 0.0)
        has_pending = len(self._pending) > 0
        has_priority = any(e.get("kind") in _PRIORITY_KINDS for e in self._pending)

        fire = False
        skipped = False
        woke_on: List[Dict[str, Any]] = []
        if has_pending and (has_priority or since_last >= gap):
            woke_on = self._drain()
            self._deferred_wake_at = None
            if self.should_skip(
                bundle_kinds={str(e.get("kind")) for e in woke_on}, now=now
            ):
                # Batch 6, task 4 (2026-09-29): the bundle's only news is a
                # device coming or going, and she wrote a moment inside the
                # hour. The events are dropped, not put back: the gather's
                # "the house right now" line carries where the house stands,
                # and events left in _pending would keep the heartbeat
                # checks below from running. No cap is spent and neither
                # last_cycle_at nor cycles_today moves. The heartbeat is not
                # refreshed here: the pass goes on as if nothing had been
                # pending, so a check that is due runs.
                logger.info(
                    "present_moment: nothing_new — skipped a cycle whose only "
                    "news was %d device event(s)", len(woke_on),
                )
                skipped, woke_on = True, []
            else:
                fire = True
                # 2026-07-24 review fix: an event-driven cycle makes a
                # separate heartbeat redundant for this tick — the heartbeat
                # exists to catch drift when events DON'T flow. Without this,
                # _last_heartbeat_at goes stale once its deadline passes, and
                # _next_deadline collapses to its 1s floor on every later
                # iteration (measured: ~1Hz spin, thousands of log lines).
                self._last_heartbeat_at = now
        elif has_pending:
            # THE DEFERRING GAP (owner's rule): suppressed wakes fire at
            # gap expiry with the coalesced bundle — never cancelled.
            self._deferred_wake_at = (self.last_cycle_at or now) + gap
            # 2026-07-26 polish — the THIRD geometry of the 1Hz spin the
            # 07-24 review fixed at the two branches above: events ARE
            # flowing here (they're just deferred), so the heartbeat's
            # drift-catch is redundant until the deferred wake fires.
            # Without this refresh, a heartbeat deadline that expires
            # during the deferring gap collapses _next_deadline to its 1s
            # floor for the REST of the gap — up to ~10 min of wake-spin
            # every time a quiet-period heartbeat precedes a busy spell.
            self._last_heartbeat_at = now
            return

        if not fire:
            # Nothing pending, or only the churn that was just skipped.
            heartbeat = self._knob("PRESENT_MOMENT_HEARTBEAT_SECONDS", 1200)
            if now - self._last_heartbeat_at >= heartbeat:
                self._last_heartbeat_at = now
                diff = await self._heartbeat_diff(now)
                if diff:
                    fire, woke_on = True, [diff]

        if not fire:
            if skipped:
                # The skipped cycle would have carried the residual jobs.
                await self._run_outreach_residuals()
            return

        cap = self._knob("PRESENT_MOMENT_MAX_CYCLES_PER_DAY", 120)
        sentinel_fire = any(e.get("kind") == "sentinel" for e in woke_on)
        # cap 0 = UNLIMITED (owner convention 2026-07-28; 07c Task 3, built
        # 2026-08-06). The bare >= read 0 as "never fire" and would have
        # stopped her present entirely — the opposite of the convention.
        if cap > 0 and self.cycles_today >= cap and not sentinel_fire:
            # Lossless: put the bundle back; meta will say so next cycle.
            for e in woke_on:
                self._pending.append(e)
            # 2026-07-24 review fix: same heartbeat-redundant reasoning as
            # the event-fire branch above (this tick already knows an
            # event happened; see that comment) — belt-and-braces at the
            # exact site the 1Hz spin was measured at.
            self._last_heartbeat_at = now
            held = len(self._pending)
            if held != self._last_cap_hold_logged:
                logger.info("present_moment: daily cycle cap reached; holding %d events", held)
                self._last_cap_hold_logged = held
            else:
                logger.debug("present_moment: daily cycle cap still holding %d events", held)
            if skipped:
                # A heartbeat check held at the cap, on a pass that skipped
                # churn: the residual jobs ride the skip all the same.
                await self._run_outreach_residuals()
            return

        await self.run_cycle_once(woke_on=woke_on)
        # Step 19 (2026-08-19): the retired proactive loop's residual jobs
        # ride this loop — one hook after each cycle (spec §7's cadence;
        # ~50-90 cycles/day, the old 96-tick class). The flag-off branch
        # above carries them when the organ is dark. NOTE the plan placed a
        # single call "at the end of the method" — unreachable past the
        # early returns; two sites are the spec's actual contract.
        await self._run_outreach_residuals()

    async def _run_outreach_residuals(self) -> None:
        """Step 19: stake-horizon threads, the arbiter's daily expiry sweep,
        the monologue-candidate rail — each internally guarded; this wrapper
        never raises into the loop."""
        try:
            from core.local_mode import get_local_owner_refs
            from core.turn.proactive import run_outreach_residuals
            refs = await get_local_owner_refs()
            await run_outreach_residuals(owner_id=int(refs.get("app_user_id") or 1))
        except Exception as exc:
            logger.debug("present_moment: residual jobs skipped: %s", exc)

    async def _heartbeat_diff(self, now: float) -> Optional[Dict[str, Any]]:
        """Structural diff vs the last moment's meta. Cheap facts only."""
        try:
            from ai.present_moment_store import read_current
            current = read_current(now=now)
        except Exception:
            current = None
        if current is None:
            return {"kind": "heartbeat_first_moment", "at": now}
        entry, age, _stale = current
        sentinel_s = self._knob("PRESENT_MOMENT_SENTINEL_HOURS", 12) * 3600
        if age >= sentinel_s:
            return {"kind": "sentinel", "at": now}
        prev = dict(entry.meta.get("tier1_snapshot") or {})
        snap = await self._tier1_snapshot(now)
        if prev.get("machine_state") != snap.get("machine_state"):
            return {"kind": "presence_drift", "at": now, **snap}
        if prev.get("conversation_bucket") != snap.get("conversation_bucket"):
            return {"kind": "silence_bucket_crossed", "at": now, **snap}
        return None

    async def _tier1_snapshot(self, now: float) -> Dict[str, Any]:
        """The structural facts the NEXT heartbeat diffs against."""
        snap: Dict[str, Any] = {}
        try:
            from core.home_presence import home_presence_service
            from core.local_mode import get_local_owner_refs
            refs = await get_local_owner_refs()
            s = await home_presence_service.get_context_snapshot(
                owner_user_id=int(refs.get("app_user_id") or 1)
            )
            snap["machine_state"] = str((s or {}).get("machine_state") or "unknown")
        except Exception:
            snap["machine_state"] = "unknown"
        try:
            from core.database import brain
            from core.local_mode import get_local_owner_refs
            refs = await get_local_owner_refs()
            rows = await brain.get_conversation_history(
                user_id=int(refs.get("palli_user_id") or 1), limit=1, include_metadata=True
            )
            last_ts = float(rows[-1].get("timestamp") or 0.0) if rows else 0.0
            snap["conversation_bucket"] = _elapsed_bucket(now - last_ts) if last_ts else -1
        except Exception:
            snap["conversation_bucket"] = -1
        return snap

    # ── tier 2: the appraisal cycle ───────────────────────────────────
    async def run_cycle_once(self, woke_on: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
        """One full cycle. Public: the owner's probe entry (and Task 8's)."""
        started = time.time()
        woke_on = list(woke_on or [{"kind": "manual_probe", "at": started}])
        from ai.llm_client import llm
        from ai.llm_openai_compatible import lane_total_timeout_seconds
        from ai.llm_routing import get_task_model
        from ai.present_moment_store import MOMENT_CHAR_CAP, read_current, write_moment
        from core.llm_json import safe_parse_json_object
        from core.local_mode import get_local_owner_refs

        refs = await get_local_owner_refs()
        owner_id = int(refs.get("palli_user_id") or 1)

        current = read_current(now=started)
        prev_undercurrent = ""
        prev_lean = ""
        if current is None:
            prev_block = "(none — this is your first moment)"
            prev_at = None
        else:
            prev_entry, prev_age, _ = current
            mins = int(prev_age // 60)
            ago = f"{mins} minutes" if mins < 180 else f"{mins // 60} hours"
            prev_block = f"(that was {ago} ago)\n{prev_entry.text}"
            prev_at = prev_entry.written_at
            prev_undercurrent = str((prev_entry.meta or {}).get("undercurrent") or "").strip()
            prev_lean = str((prev_entry.meta or {}).get("lean") or "").strip()

        gathered = await _pm_gather.gather_moment_inputs(owner_user_id=owner_id)

        # meta keeps the RAW structural kinds (auditable, diffable); only
        # the PROMPT gets the humanized rendering (2026-07-26 polish —
        # the mechanics-echo fix; see _EVENT_PHRASES' law note).
        kinds: Dict[str, int] = {}
        for e in woke_on:
            kinds[str(e.get("kind"))] = kinds.get(str(e.get("kind")), 0) + 1
        events_block = _humanize_events(woke_on)

        affect_on = self._affect_enabled()
        arbiter_on = self._arbiter_enabled()
        wanting_on = self._wanting_enabled()
        template, variant = _load_appraisal_template(
            affect_on=affect_on, arbiter_on=arbiter_on, wanting_on=wanting_on
        )
        if not template:
            self.last_error = "prompt missing"
            # 2026-07-24 review fix: advance last_cycle_at on this failure
            # path too. _loop_once's gap throttle reads last_cycle_at to
            # decide `since_last >= gap`; leaving it untouched on failure
            # meant a persistently failing cycle (e.g. a missing prompt
            # file) left `since_last` permanently huge, so every pending
            # event re-fired a full cycle with zero throttling — unbounded
            # LLM calls under sustained failure. The throttle governs
            # cycle ATTEMPTS, not successes, so it moves here even though
            # nothing was written. cycles_today stays success-only on
            # purpose (see below): it mirrors count_today(), which
            # restores the count from written JSONL lines on disk, and a
            # failed cycle writes no line.
            self.last_cycle_at = time.time()
            return {"error": "prompt missing"}
        rendered = template.format(
            current_time=time.strftime("%A, %Y-%m-%d %I:%M %p", time.localtime(started)),
            previous_moment_block=prev_block,
            undercurrent_block=(
                prev_undercurrent
                or "(none yet — this is the first time you've listened underneath)"
            ),
            events_block=events_block,
            senses_block=gathered.senses_block,
            bases_block=gathered.bases_block,
            monologue_block=gathered.monologue_block,
            reflection_block=gathered.reflection_block,
            # Referenced only by the arbiter variant; base/affect templates
            # are placeholder-free for this kwarg and str.format ignores
            # extras (the affect build's proven pattern).
            threads_block=(
                getattr(gathered, "threads_block", "")
                or "Nothing on your thread board."
            ),
            # Referenced only by the wanting variant (str.format ignores
            # extras — the affect build's proven pattern).
            wants_block=(
                getattr(gathered, "wants_block", "")
                or "Nothing in your wishbook."
            ),
            # Referenced only by the live rungs (wanting/arbiter); base and
            # affect are placeholder-free for this kwarg and str.format
            # ignores extras. "" collapses to zero bytes — flag-off prompts
            # stay byte-identical (spec §3).
            expectations_block=getattr(gathered, "expectations_block", ""),
            # Same contract for her fearbook (step 24 §3): live rungs only,
            # "" collapses to zero bytes — flag-off AND empty-book prompts
            # stay byte-identical (decision 5).
            fears_block=getattr(gathered, "fears_block", ""),
            # Organ 4.9 · Grounded Vulnerability (spec §4): her small-truths
            # board — live rungs only, and deliberately its own slot rather
            # than a reference base, so it sits with the say line that
            # governs it instead of in the material her moment prose is
            # written from. "" collapses to zero bytes.
            disclosures_block=getattr(gathered, "disclosures_block", ""),
            # Step 25 · Regret (spec §4): the say→regret_id contract — live
            # rungs only; zero bytes unless an open regret is on the page.
            **_regret_voicing_kwargs(bool(getattr(gathered, "regret_voicing", False))),
            # Organ 4.13 · Opinions (spec §5): the say→view_id contract —
            # live rungs only; zero bytes unless an open, unmuted view is on
            # the page. Different keys from the regret pair: no collision.
            **_view_voicing_kwargs(bool(getattr(gathered, "view_voicing", False))),
            # Organ 4.9 · Grounded Vulnerability (spec §4): the
            # say→disclosure_id contract — live rungs only; zero bytes
            # unless her small-truths board is on the page (the board
            # itself rides the {disclosures_block} slot above, never
            # bases_block). Distinct keys again: no collision.
            **_disclosure_voicing_kwargs(bool(getattr(gathered, "disclosure_offer", False))),
        )

        model = get_task_model("present_moment", fallback=get_task_model("reflection"))
        response = ""
        for attempt in (1, 2):  # one re-roll on empty/malformed (spec §3)
            try:
                # A hung provider must not wedge the loop; a timeout counts
                # as an empty attempt. The ceiling is not spec §3's fixed
                # 120 s any more: it is the envelope derived just below.
                response = await asyncio.wait_for(
                    llm.chat_completion(
                        [{"role": "system", "content": rendered}],
                        model=model,
                        temperature=0.6,
                        max_tokens=3000,
                        log_context="present_moment_cycle",
                    ),
                    # the outer envelope is derived from the lane's own PATIENT
                    # retry ladder (4x60s + backoff = 250s) plus headroom, so
                    # it can never give up before the provider's own retries
                    # finish (2026-09-28). The 180.0 it replaces was a
                    # hand-picked number that fell behind the ladder: it cut
                    # the 3rd attempt short and the 4th never ran.
                    timeout=lane_total_timeout_seconds("present_moment_cycle") + 30.0,
                )
            except asyncio.TimeoutError:
                response = ""
            if response and safe_parse_json_object(response):
                break

        parsed = safe_parse_json_object(response or "") or {}
        moment_text = str(parsed.get("moment") or "").strip()
        if not moment_text and (response or "").strip():
            # Raw-text fallback: treat the whole response as the moment.
            moment_text = response.strip()
        if not moment_text:
            self.last_error = "empty appraisal after re-roll; wrote nothing"
            logger.warning("present_moment: %s", self.last_error)
            # 2026-07-24 review fix: same attempted-cycle throttle
            # semantics as the prompt-missing branch above — see that
            # comment. This branch is the expensive one to leave
            # unthrottled: it has already spent a full gather plus up to
            # two LLM calls before landing here.
            self.last_cycle_at = time.time()
            return {"error": self.last_error}

        impulse = _parse_impulse(parsed.get("impulse"))

        # The rail (arbiter spec §4, 2026-08-06): release is judged BEFORE
        # the moment is written so the disposition lands inside this
        # cycle's own record. arbiter_live = the arbiter contract was
        # actually in the prompt — a base/affect cycle can never release
        # (framing cannot exist there), so shadow stays shadow by
        # construction.
        arbiter_live = variant in ("arbiter", "wanting")
        if arbiter_live:
            from ai.arbiter import consider_release

            rail = await consider_release(
                impulse=impulse, owner_user_id=owner_id,
                arbiter_live=arbiter_live,
            )
            if rail:
                impulse["rail"] = rail
                await _record_tonight_said(impulse, rail)

        # Slice 3 (spec §2): the undercurrent — one prose sentence, meta
        # residence like the impulse. Carry-forward law: a parse hiccup
        # never erases her mood; release happens only in her own words.
        undercurrent = ""
        if affect_on:
            uc_raw = parsed.get("undercurrent")
            if isinstance(uc_raw, str):
                undercurrent = " ".join(uc_raw.split())[:220]
            if not undercurrent and prev_undercurrent:
                undercurrent = prev_undercurrent
                logger.debug(
                    "present_moment: undercurrent missing from appraisal; carried forward"
                )

        # Surprise (organ 8, spec §3): meta carries it only when the flag is
        # on AND the parse is valid; a REAL one opens the salience window.
        surprise = None
        try:
            from ai.expectations import surprise_enabled as _surprise_on
            if _surprise_on():
                surprise = _parse_surprise(parsed.get("surprise"))
        except Exception:
            surprise = None
        if surprise:
            try:
                from ai.surprise_state import mark as _mark_surprise
                _mark_surprise(about=surprise["about"], weight=surprise["weight"])
            except Exception:
                pass

        # A touched fear (organ 6, step 24 §4): meta carries it only when
        # the flag is on, the parse is valid AND the book actually holds
        # that fear — a phantom id is a manufactured touch and dies here
        # (the store is the truth; the block's law is the first defense).
        fear = None
        try:
            from ai.fear_store import fears_enabled as _fears_on
            if _fears_on():
                fear = _parse_fear(parsed.get("fear"))
        except Exception:
            fear = None
        if fear:
            touched = False
            try:
                from ai.fear_store import touch as _touch_fear
                touched = _touch_fear(
                    fear["id"], line=fear["line"], weight=fear["weight"]
                )
            except Exception:
                touched = False
            if touched:
                logger.info(
                    "present_moment: a fear was touched (%s, %s)",
                    fear["id"], fear["weight"],
                )
                # A REAL touch opens the remembered-harder window (§4);
                # mark() gates weight itself — slight opens nothing.
                try:
                    from ai.fear_state import mark as _mark_fear
                    _mark_fear(fear_id=fear["id"], weight=fear["weight"])
                except Exception:
                    pass
            else:
                logger.info(
                    "present_moment: fear touch declined — %r is not a held fear",
                    fear["id"],
                )
                fear = None

        meta = {
            "woke_on": [{"kind": k, "count": n} for k, n in kinds.items()],
            "sense_health": gathered.sense_health,
            "impulse": impulse,
            "model": model or "default",
            "duration_seconds": round(time.time() - started, 2),
            "prev_moment_at": prev_at,
            "budget": {
                "cycles_today": self.cycles_today + 1,
                "cap": int(self._knob("PRESENT_MOMENT_MAX_CYCLES_PER_DAY", 120)),
            },
            "tier1_snapshot": await self._tier1_snapshot(time.time()),
        }
        if undercurrent:
            meta["undercurrent"] = undercurrent
        # The lean (organ 4's second half, spec §6): written EVERY cycle —
        # cheap truth beside the undercurrent; only the READ half (recall
        # bias) is flag-gated. Carry-forward law on parse hiccups.
        meta["lean"] = _parse_lean(parsed.get("lean"), prev=prev_lean)
        if surprise:
            meta["surprise"] = surprise
        if fear:
            meta["fear"] = fear
        path = write_moment(text=moment_text, meta=meta)
        self.last_cycle_at = time.time()
        self.cycles_today += 1
        self.last_error = ""
        logger.info(
            "present_moment: cycle wrote %d chars (woke_on=%s, impulse=%s)",
            min(len(moment_text), MOMENT_CHAR_CAP), list(kinds), impulse["kind"],
        )
        return {"path": str(path), "chars": len(moment_text), "impulse": impulse}


present_moment_engine = PresentMomentEngine()
