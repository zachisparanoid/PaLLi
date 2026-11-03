"""The arbiter (roadmap step 18, spec 2026-08-06): the moment's `say`
impulse becomes a real reach-out on ONE rail. The gate lives INSIDE the
appraisal — she decides and composes in the same call; this module owns
only RELEASE: deterministic checks in a fixed order, a hard daily ceiling,
and the custody primitive. Meaning-decisions (sensitivity, held_before,
what to say) are hers; gates and budgets are code (owner rule 2026-07-16).

Release order (spec §4): flag → say+framing → conversation-state → quiet
window → learned quiet (step 22) → care persistence → ceiling →
record_outbound. Release errors fail CLOSED. The impulse persists only as she re-asserts it — the moment IS the
pending queue; there is no scheduler here and no retry state.

Also home (2026-08-18) to the SHARE LANE — deliver_share: her workbench's
SHARE line as a real message, riding the same read of the moment (the
factored _moment_gates) and the freshness judge, exempt from the ceiling
(his standing request, not one of her rationed impulses); ledger
disposition ``sent_share`` so sends_today() stays the impulse count.

Also home to the expiry sweep (spec §7): a tiered TTL *backstop* under her
judgment, date-claimed ON SUCCESS with retry at the next tick (the D1
claim-before-call lesson, inverted correctly).
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from core.agent import duplex as _dx

logger = logging.getLogger(__name__)

ARBITER_DATA_DIR = Path("data/arbiter")

_SENSITIVITIES = ("routine", "care", "surprise")


# ── indirection seams (monkeypatch points; each resolves lazily) ─────────

def _companion():
    try:
        from core.turn.companion_flags import companion_settings

        return companion_settings()
    except Exception:
        return None


async def _pick_surface(*, owner_id: int) -> Optional[tuple[str, str]]:
    """WHERE a reach-out goes: ``(platform, surface_id)``, across every door.

    Telegram door (2026-09-14, task 2). This asked for a bare Discord
    channel id until now, so every reach-out she decided to make went out of
    Discord whatever door the conversation had actually been on — an answer
    in a room he had not opened for a week. The picker (task 1) already looks
    across doors and returns the pair; this carries it.
    """
    from core.turn import proactive

    return await proactive._pick_proactive_surface(owner_id=owner_id)


async def _last_user_message_at(*, owner_id: int) -> float:
    from core.turn.proactive import _safe_last_user_message_at

    return await _safe_last_user_message_at(owner_id=owner_id)


async def _record_outbound(**kwargs) -> bool:
    from core.turn.outbound import record_outbound

    return await record_outbound(**kwargs)


async def _record_outbound_spoken(**kwargs) -> bool:
    from core.turn.outbound import record_outbound_spoken

    return await record_outbound_spoken(**kwargs)


async def _voice_moment():
    from core.agent.hands_runtime import active_voice_moment

    return await active_voice_moment()


async def _probe_voice_moment() -> Optional[Dict[str, Any]]:
    """Step 32: is he in a voice channel with her, right now? Knob-gated
    (arbiter_voice_says) and contained — off, absent, or a probe failure
    all mean None, and the text path proceeds byte-identical. Probed ONCE
    per release consideration; the result feeds both the medium decision
    (spec §4) and the learned-quiet presence override (spec §5)."""
    if not bool(_knob("arbiter_voice_says", False)):
        return None
    try:
        return await _voice_moment()
    except Exception as exc:
        logger.warning("arbiter: voice-moment probe failed: %s", exc)
        return None


def _spoken_only_alone(vm: Optional[Dict[str, Any]], *, lane: str) -> Optional[Dict[str, Any]]:
    """The voice moment a say or a share may be SPOKEN in: only a call that
    is him and her alone (the owner, 2026-10-13: a message for him alone
    never reaches a group, and friends on the call would hear it). A shared
    call, or one whose members could not be read (``alone_with_her`` not
    True), is None, so the release leg sends it as text to the private chat
    already picked. His presence (``voice_present``) is read from the moment
    itself, so learned quiet's presence override is unchanged. Her mid-task
    chimes and asks (core/agent/duplex.py) belong to their job and do not
    pass here."""
    if not vm:
        return None
    if vm.get("alone_with_her") is True:
        return vm
    logger.info("arbiter: he is on a voice call that is not him and her alone; "
                "the %s goes as text to his private chat", lane)
    return None


def _vulnerability_on() -> bool:
    """Organ 4.9's single gate, read at call time (the store owns it — the
    reflect lane's `_disclosure_filing_on` posture). A read failure means
    dark: with the organ off, her token is never even read, so no resolve,
    no row key, no ledger key, and above all no stamp — a truth planted
    while it was on stays unspent and offerable the day he turns it back
    on. Said once is forever; the safe side of an unreadable flag is the
    side that spends nothing."""
    try:
        from ai.disclosure_store import vulnerability_enabled

        return bool(vulnerability_enabled())
    except Exception as exc:
        logger.warning("arbiter: vulnerability flag unreadable (dark): %s", exc)
        return False


async def _release_outbound(
    *, owner_user_id: int, channel_id: int, text: str, origin: str,
    extra: Dict[str, Any], vm: Optional[Dict[str, Any]],
    platform: str = "discord",
) -> Dict[str, Any]:
    """The one release leg, medium decided here (spec §4): a live voice
    moment speaks; a speak failure FALLS BACK to the text send (decision 5
    — the say survived every gate; a medium hiccup never kills it). Only a
    failed TEXT send raises (the existing error_closed path). Returns
    {"medium": ...} plus voice_fallback_text when the fallback fired.

    ``platform`` (telegram door, 2026-09-14, task 1) names the door the TEXT
    leg goes out of; defaulted, so every existing caller is unchanged. The
    spoken leg is a Discord voice session and is never handed it — a caller
    that is not on Discord passes ``vm=None`` (core/agent/duplex.py)."""
    fallback: Dict[str, Any] = {}
    platform = str(platform or "discord").strip().lower() or "discord"
    # A voice moment is a Discord voice session. Off that door there is
    # nothing to speak into, and reaching for it would record a spoken line
    # against a session that does not exist — so another door is text, always.
    if vm and platform == "discord":
        try:
            await _record_outbound_spoken(
                owner_id=int(owner_user_id),
                channel_id=int(channel_id),
                runtime_session_key=str(vm.get("runtime_session_key") or ""),
                text=text,
                origin=origin,
                extra=extra,
            )
            return {"medium": "voice"}
        except Exception as exc:
            reason = " ".join(str(exc).split())[:120]
            logger.warning(
                "arbiter: spoken release failed (%s); falling back to text",
                reason,
            )
            fallback = {"voice_fallback_text": reason}
    await _record_outbound(
        owner_id=int(owner_user_id),
        channel_id=int(channel_id),
        text=text,
        origin=origin,
        extra=extra,
        platform=platform,
        surface_id=str(channel_id),
    )
    return {"medium": "text", **fallback}


async def _mark_surfaced(*, thread_id: str) -> None:
    from core.database import brain

    await brain.mark_open_thread_surfaced(thread_id=thread_id)


async def _list_active(*, owner_user_id: int):
    from core.database import brain

    return await brain.list_active_open_threads(owner_user_id=owner_user_id)


async def _expire_stale(**kwargs):
    from core.database import brain

    return await brain.expire_stale_open_threads(**kwargs)


def _run_phase(session_key: str) -> str:
    from core.agent.steering import get_run_phase

    return get_run_phase(session_key)


def _is_working(session_key: str) -> bool:
    from core.agent.steering import is_working

    return is_working(session_key)


def _today() -> str:
    return time.strftime("%Y-%m-%d")


# ── flags and knobs (fail CLOSED — the present_moment pattern) ───────────

def arbiter_enabled() -> bool:
    c = _companion()
    if c is None:
        return False
    return bool(getattr(c, "arbiter", False)) and bool(
        getattr(c, "present_moment", False)
    )


def _knob(name: str, default):
    c = _companion()
    if c is None:
        return default
    try:
        return type(default)(getattr(c, name, default))
    except Exception:
        return default


def _ceiling() -> int:
    """Her own reach-outs a day (knob ``arbiter_daily_send_ceiling``, default
    3). 0 = rail dark — deliberately OUTSIDE the 0-means-unlimited convention:
    a send ceiling must never fat-finger to infinity. With
    ``features.proactive_conversations`` off it reads 0 (2026-10-14)."""
    from core.turn.companion_flags import speaks_first_on

    if not speaks_first_on():
        return 0
    return int(_knob("arbiter_daily_send_ceiling", 3))


# ── the send ledger (ceiling source of truth; record-on-send) ────────────

def _ledger_path(day: str | None = None) -> Path:
    return ARBITER_DATA_DIR / f"{day or _today()}.jsonl"


# The day's ledger (one file a day) already warned about as unreadable.
_UNREADABLE_WARNED: Optional[str] = None


def sends_today() -> int:
    """Her own reach-outs sent today (ledger rows with disposition 'sent';
    a share's 'sent_share' is not one).

    Fails CLOSED (Batch 6, task 6): a missing ledger is the normal state
    before the day's first send and is 0, but a ledger that exists and cannot
    be read or decoded counts as a full day — it used to count as an empty
    one, so a read error opened the ceiling. A single line that cannot be
    read as a row counts as one send: a damaged line costs her at most one
    reach-out that day. The moment asks on every cycle, so the warning for
    an unreadable ledger is said once per ledger day (Batch 6 fix wave, M4);
    every call still counts the day as full.

    Read with ``errors="replace"`` (second read F5, 2026-10-04): a row cut
    inside a multi-byte character — what a crash mid-append leaves, and her
    words carry em dashes and curly quotes on every row — is one bad line,
    one send, the way the reach-out book reads (core/turn/outbound.py). A
    strict decode turned that one torn row into a full day of silence. A
    real read failure (OSError) still counts the day as full."""
    global _UNREADABLE_WARNED
    path = _ledger_path()
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return 0
    except Exception as exc:
        if _UNREADABLE_WARNED != str(path):
            _UNREADABLE_WARNED = str(path)
            logger.warning("arbiter: ledger unreadable, the day counts as full: %s", exc)
        return _ceiling()
    n = 0
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            row = None
        if not isinstance(row, dict) or row.get("disposition") == "sent":
            n += 1
    return n


def admit_or_hold() -> Optional[Dict[str, Any]]:
    """The daily ceiling as one named gate: None when there is room under
    today's ceiling for one more of her own reach-outs, else the held
    verdict. The say lane asks it before the freshness judge, so a held
    reach-out never spends a judge call; every organic leg rides that lane
    (a plain say, surprise naming, regret voicing, a view, a small truth,
    tonight's pick, the arrival greeting). A workbench share never asks it:
    the owner's standing request is not rationed (2026-08-18, confirmed
    2026-09-29)."""
    ceiling = _ceiling()
    if sends_today() >= ceiling:
        return {"disposition": "ceiling_blocked", "ceiling": ceiling}
    return None


def surprise_named_today() -> int:
    """Named surprises sent today (ledger rows: disposition 'sent' with
    sensitivity 'surprise'). Read errors count as budget SPENT (fail closed
    — a naming budget must never fail open)."""
    path = _ledger_path()
    if not path.exists():
        return 0
    n = 0
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
                if row.get("disposition") == "sent" and row.get("sensitivity") == "surprise":
                    n += 1
            except Exception:
                continue
    except Exception:
        return 10 ** 6   # unreadable ledger ⇒ budget spent
    return n


STALE_WAIT_SECONDS = 24 * 3600


def _dropped_stale_since(thread_key: str, *, after: float = 0.0) -> bool:
    """True when the judge dropped a message on this thread as stale within
    the last day, and after ``after`` (the thread's last change). Reads
    today's and yesterday's ledgers; an unreadable line is skipped (this
    wait only saves a judge call, it guards nothing)."""
    now = time.time()
    since = max(now - STALE_WAIT_SECONDS, float(after or 0.0))
    days = {time.strftime("%Y-%m-%d", time.localtime(now - d)) for d in (0, STALE_WAIT_SECONDS)}
    for day in days:
        try:
            lines = _ledger_path(day).read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if (row.get("disposition") == "stale_dropped" and row.get("thread_id") == thread_key
                    and float(row.get("at") or 0.0) > since):
                return True
    return False


def _ledger_append(entry: Dict[str, Any]) -> None:
    try:
        ARBITER_DATA_DIR.mkdir(parents=True, exist_ok=True)
        with _ledger_path().open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as exc:
        logger.warning("arbiter: ledger append failed: %s", exc)


# ── quiet window (pure; "HH:MM-HH:MM", may wrap midnight; "" = off) ──────

def _in_quiet_window(now_struct: time.struct_time, window: str) -> bool:
    window = str(window or "").strip()
    if not window:
        return False
    try:
        start_s, end_s = window.split("-", 1)
        sh, sm = (int(x) for x in start_s.strip().split(":", 1))
        eh, em = (int(x) for x in end_s.strip().split(":", 1))
    except Exception:
        return False  # malformed window = off (the schema validates shape)
    now_m = now_struct.tm_hour * 60 + now_struct.tm_min
    start_m, end_m = sh * 60 + sm, eh * 60 + em
    if start_m <= end_m:
        return start_m <= now_m <= end_m
    return now_m >= start_m or now_m <= end_m  # wraps midnight


# ── the freshness judge (impulse-freshness spec §4, 2026-08-07) ──────────

# Retry-envelope floor (3b18846): 2x the PATIENT per-request ceiling (60s)
# since the 2026-08-09 split; 150 adds headroom. Fail-closed on timeout —
# a held send just waits for the next cycle, so patience costs nothing.
_FRESHNESS_TIMEOUT_S = 150.0


# ── learned quiet hours (step 22, spec 2026-08-10) ───────────────────────
# Her learned model of his absence rhythm. RHYTHM_FILE is module-level so
# tests repoint it (the ARBITER_DATA_DIR pattern). The loader import is
# wrapped so tests can spy/replace it.
from ai.owner_rhythm import RHYTHM_FILE  # noqa: F401  (re-exported for tests)


def _load_rhythm_verdict(path, *, now):
    from ai.owner_rhythm import load_current_verdict

    return load_current_verdict(path, now=now)


async def _untrimmed_tail(owner_user_id: int, *, limit: int = 10) -> str:
    """The last ~10 conversation messages, FULL text — the judge earns the
    untrimmed read (spec §4): the glance's 300-char trim is exactly what hid
    the completion evidence on 08-06."""
    from core.database import brain
    from core.local_mode import get_local_owner_refs

    refs = await get_local_owner_refs()
    uid = int(refs.get("palli_user_id") or owner_user_id)
    rows = await brain.get_conversation_history(
        user_id=uid, limit=limit, include_metadata=True
    )
    lines = []
    for r in rows or []:
        content = " ".join(str(r.get("content") or "").split())
        if not content:
            continue
        speaker = str(r.get("role") or "").lower()
        # A ``system`` row (a reply the door could not send) is the record's
        # note, never hers.
        if speaker in ("user", "human"):
            role = "them"
        elif speaker == "system":
            role = "system"
        else:
            role = "me"
        lines.append(f"[{role}] {content}")
    return "\n".join(lines) or "(no conversation on record)"


# ── what the judge sees of her own reach-outs (Batch 6, task 6) ──────────
# Her last few reach-outs and whether the owner wrote back, so the judge can
# tell which kinds were welcome. Whether this one is worth sending stays the
# judge's call; the window and the count are code.
#
# The whole section — header, list, closing line — is the value of the
# template's {recent_reachouts} slot, and the slot sits flush against the
# question's first line, so while the book is shut (the arbiter off) it is
# zero bytes and the judge's prompt is the pre-batch prompt, byte for byte.
# The share lane runs this judge with the arbiter off (fix round 1, I1).

_REACHOUTS_SHOWN = 5
_NO_REACHOUTS = "(none on record yet)"
# The book's answer states (core/turn/outbound.py: answer_state) as the
# judge reads them. "answered" stays out: the judge's question already uses
# it for a thing taken care of (fix round 1, M3).
_REPLY_LABELS = {
    "answered": "got a reply",
    "unanswered": "no reply",
    "no answer yet": "no reply yet",
}
_REACHOUTS_HEADER = (
    "The last messages you sent the owner on your own, newest first.\n"
    '"got a reply" means they wrote back in the same chat within six hours;\n'
    '"no reply" means they did not; "no reply yet" means it went out less\n'
    "than six hours ago:\n"
)
_REACHOUTS_CLOSING = (
    "Your last messages show which kinds of message got a reply and which\n"
    "did not. Take that into account too.\n"
)


async def _recent_reachouts() -> list:
    """Her last five TEXT reach-outs, newest first, from her book of
    reach-outs (core/turn/outbound.py). A spoken one is left out: a spoken
    answer is filed under the voice channel and can never mark it answered,
    so it could only ever read as unanswered. The file work runs off the
    event loop. A monkeypatch point."""
    from core.turn import outbound

    return await asyncio.to_thread(
        outbound.recent_reachouts, limit=_REACHOUTS_SHOWN, medium="text"
    )


def _ago(seconds: float) -> str:
    s = max(0.0, float(seconds))
    if s < 3600:
        n, unit = max(1, int(s // 60)), "minute"
    elif s < 48 * 3600:
        n, unit = int(s // 3600), "hour"
    else:
        n, unit = int(s // 86400), "day"
    return f"{n} {unit}{'' if n == 1 else 's'} ago"


def _render_reachouts(rows: list, *, now: float) -> str:
    """The judge's whole section: the header, one line per reach-out — when,
    the head, and got a reply / no reply / no reply yet — then the closing
    line and a blank line, so the question that follows starts its own
    paragraph. None yet is one plain line in the list's place."""
    from core.turn.outbound import answer_state

    lines = [
        f'- {_ago(now - float(r["at"]))}: "{r["head"]}" '
        f'({_REPLY_LABELS[answer_state(r, now=now)]})'
        for r in rows or []
    ]
    listing = "\n".join(lines) or _NO_REACHOUTS
    return f"{_REACHOUTS_HEADER}{listing}\n\n{_REACHOUTS_CLOSING}\n"


async def _reachouts_section() -> str:
    """The value of the judge's {recent_reachouts} slot. Empty — zero bytes
    — while the book is shut (outbound.reachouts_enabled(): the arbiter's
    flag): nothing is written or marked then, so a list would be false as
    well as new, and the prompt stays the pre-batch one. A list that cannot
    be read is shown as none — never a failed judge."""
    from core.turn import outbound

    if not outbound.reachouts_enabled():
        return ""
    try:
        return _render_reachouts(await _recent_reachouts(), now=time.time())
    except Exception as exc:
        logger.warning("arbiter: reach-out list unreadable (shown as none): %s", exc)
        return _render_reachouts([], now=time.time())


async def _freshness_check(
    *, framing: str, owner_user_id: int, held_before: bool
) -> Tuple[bool, str]:
    """Fresh/stale verdict for a release-ready impulse. A GATE, NEVER A
    REWRITER (the 07-30 law): it may answer yes or no; it may not touch one
    word of the framing. Raises to the caller on any failure — the release
    path fails closed."""
    from ai.llm_client import llm
    from ai.llm_routing import get_task_model
    from ai.prompt_loader import prompt_loader
    from ai.recent_deeds import read_recent_deeds
    from core.llm_json import safe_parse_json_object

    template = prompt_loader.load_prompt("arbiter_freshness")
    if not template:
        raise RuntimeError("freshness prompt missing")
    deeds_rows = read_recent_deeds(limit=8)
    deeds = ""
    if deeds_rows:
        deeds = "What you have DONE recently (actions, not words):\n" + "\n".join(
            f"- {d.get('detail')}" for d in deeds_rows
        )
    recent_reachouts = await _reachouts_section()
    rendered = template.format(
        framing=framing,
        held_note=(
            "This impulse was held earlier and has survived at least one "
            "quiet re-look — it is not brand new." if held_before else
            "This impulse formed within the last few minutes."
        ),
        tail=await _untrimmed_tail(owner_user_id),
        deeds=deeds,
        recent_reachouts=recent_reachouts,
    )
    raw = await asyncio.wait_for(
        llm.chat_completion(
            [{"role": "system", "content": rendered}],
            model=get_task_model(
                "arbiter_freshness",
                fallback=get_task_model(
                    "steering_judgment", fallback=get_task_model("response")
                ),
            ),
            temperature=0.2,
            # 2026-08-13 06:01: 700 died to finish_reason=length with
            # reasoning_len=2953 — and the 4x retry (2800) was STILL under
            # the appetite, so the verdict came back empty twice and a live
            # impulse dropped stale_check_failed. The budget must clear the
            # reasoning burn with the verdict's room on top (D1 class).
            max_tokens=3000,
            log_context="arbiter_freshness",
        ),
        timeout=_FRESHNESS_TIMEOUT_S,
    )
    parsed = safe_parse_json_object(raw or "")
    if not isinstance(parsed, dict) or "fresh" not in parsed:
        raise RuntimeError("freshness verdict unparseable")
    return bool(parsed.get("fresh")), " ".join(
        str(parsed.get("reason") or "").split()
    )[:200]


# ── the moment gates (shared by the say-rail and the share lane) ─────────

async def _moment_gates(
    *, owner_user_id: int, channel_id: int, voice_present: bool = False,
    platform: str = "discord",
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Is this a moment she may reach him in? Conversation-state → his quiet
    window → learned quiet hours (step 22, presence-overridable). Returns
    (held_verdict, learned_override_note): held_verdict is None when the
    moment is open. Deterministic reads only — the meaning of the moment
    stayed hers, upstream; this is timing.

    Factored out 2026-08-18 so the share lane (deliver_share) rides the same
    read of the moment as the say-impulse without re-implementing it.
    """
    # Conversation-state: mid-conversation her REPLY carries the moment
    # (the Slice-2 band) — the rail is for quiet. Deterministic reads of
    # the phase machinery; replaces ambient's 30-min recent-conversation
    # gate with one clean rule at one point. The run is keyed on the chat's
    # own door (2026-10-14): ``discord:`` for every chat never matched a
    # reply in flight in the owner's Telegram chat.
    from core.session_keys import room_key

    session_key = room_key(str(platform or "discord"), int(channel_id))
    _phase = _run_phase(session_key)
    _working = _is_working(session_key)
    if _dx.duplex_enabled():
        # Step 27 (spec §2): a run that has WORKED no longer holds her — the
        # work and her own voice are two channels. Only a plain reply in
        # flight (prep/generating with no tool call yet) still carries the
        # moment, the Slice-2 band.
        if _phase in ("prep", "generating") and not _working:
            return {"disposition": "held_conversation", "detail": "reply in flight"}, None
    elif _phase in ("prep", "generating") or _working:
        return {"disposition": "held_conversation", "detail": "run in flight"}, None
    quiet_minutes = float(_knob("arbiter_quiet_minutes", 5))
    last_msg = await _last_user_message_at(owner_id=owner_user_id)
    if last_msg and (time.time() - float(last_msg)) < quiet_minutes * 60.0:
        return {"disposition": "held_conversation", "detail": "they just spoke"}, None

    if _in_quiet_window(
        time.localtime(), str(_knob("arbiter_quiet_window", ""))
    ):
        return {"disposition": "held_quiet_window"}, None

    # Learned quiet hours (step 22): confidently-quiet bucket holds the
    # send — unless he is demonstrably present (the real him beats the
    # statistic). Fail-open everywhere: no file / corrupt / stale = today's
    # behavior. Flag off = the gate does not even look (byte-identical).
    # learned_override_note carries forward to the SENT ledger entry
    # (spec §2/§4: "disposition says so") — an INFO log line alone left no
    # durable trace of the override.
    learned_override_note: Optional[str] = None
    if bool(_knob("arbiter_learned_quiet", False)):
        verdict = _load_rhythm_verdict(RHYTHM_FILE, now=time.time())
        if verdict is None:
            logger.warning(
                "arbiter: learned-quiet stood down (rhythm file missing/"
                "corrupt/stale) — sends behave as if the flag were off"
            )
        else:
            bucket_quiet, evidence = verdict
            override_min = float(_knob("rhythm_present_override_minutes", 90))
            he_is_present = bool(last_msg) and (
                time.time() - float(last_msg)
            ) < override_min * 60.0
            # Step 32 (spec §5): him sitting in her voice channel is the
            # realest presence there is — it beats the statistic outright,
            # with its own note so the ledger evidence stays distinguishable
            # from the message-recency override.
            if bucket_quiet and voice_present:
                learned_override_note = "in her voice channel"
                logger.info(
                    "arbiter: learned_quiet_overridden_by_presence "
                    "(in her voice channel)"
                )
            elif bucket_quiet and he_is_present:
                learned_override_note = evidence
                logger.info(
                    "arbiter: learned_quiet_overridden_by_presence (%s)",
                    evidence,
                )
            elif bucket_quiet:
                return {
                    "disposition": "held_learned_quiet",
                    "evidence": evidence,
                }, None
    return None, learned_override_note


# ── the share lane (2026-08-18) ──────────────────────────────────────────

async def deliver_share(*, text: str, owner_user_id: int) -> Dict[str, Any]:
    """Her workbench's SHARE line as a real message to him.

    Why here: the wall between her working self and her talking self was
    total — a session's share became an open-thread row that only a ~3%
    appraisal lottery could raise (20 shares, zero delivered). His call
    (2026-08-18): "a message when it's done." So a share rides THIS
    module's read of the moment (conversation-state → quiet window →
    learned quiet) and the freshness judge (she may have carried it in a
    reply while it waited — the judge is exactly the did-I-already-say-this
    check), then the one custody primitive, origin ``workbench_share``.

    What it does NOT do: spend or obey the daily ceiling. The ceiling
    rations HER impulses; a share is his standing request. Ceiling 0 (rail
    DARK) still darkens it — the emergency brake means "she does not reach
    out on her own", full stop. Ledger disposition ``sent_share`` (not
    ``sent``) so sends_today() stays the impulse count.

    No retry state here (the arbiter's law): a held verdict is returned and
    the workbench keeps the share pending. Never raises: any unexpected
    error → error_closed (no send).
    """
    try:
        return await _deliver_share_inner(text=text, owner_user_id=owner_user_id)
    except Exception as exc:
        logger.warning("arbiter: share lane error (fail closed): %s", exc)
        return {
            "disposition": "error_closed",
            "error": " ".join(str(exc).split())[:120],
        }


async def _deliver_share_inner(*, text: str, owner_user_id: int) -> Dict[str, Any]:
    share = str(text or "").strip()
    if not share:
        return {"disposition": "held_no_framing"}
    if _ceiling() <= 0:
        return {"disposition": "ceiling_blocked", "ceiling": 0}

    picked = await _pick_surface(owner_id=owner_user_id)
    if picked is None:
        return {"disposition": "held_no_channel"}
    platform, surface_id = picked
    channel_id = int(surface_id)

    # Step 32: one voice-moment probe per consideration — feeds the medium
    # decision at release (and, spec §5, the presence override).
    vm = await _probe_voice_moment()

    held, learned_override_note = await _moment_gates(
        owner_user_id=owner_user_id, channel_id=int(channel_id),
        voice_present=bool(vm), platform=platform,
    )
    if held:
        return held

    if bool(_knob("arbiter_freshness_check", True)):
        try:
            fresh, reason = await _freshness_check(
                framing=share, owner_user_id=int(owner_user_id), held_before=False,
            )
        except Exception as exc:
            logger.warning("arbiter: share freshness check failed (fail closed): %s", exc)
            _ledger_append({
                "at": time.time(), "disposition": "stale_check_failed",
                "lane": "share", "error": " ".join(str(exc).split())[:120],
            })
            return {"disposition": "stale_check_failed"}
        if not fresh:
            logger.info("arbiter: stale share dropped (%s)", reason)
            _ledger_append({
                "at": time.time(), "disposition": "stale_dropped",
                "lane": "share", "reason": reason, "chars": len(share),
            })
            return {"disposition": "stale_dropped", "reason": reason}

    # RELEASE. A failed TEXT send raises → error_closed via the outer
    # catch, with zero bookkeeping (the share stays pending). Medium is
    # decided here (step 32): he's in the room, alone with her (10-13) ⇒
    # spoken; speak failure ⇒ the text fallback inside _release_outbound.
    released = await _release_outbound(
        owner_user_id=int(owner_user_id),
        channel_id=int(channel_id),
        text=share,
        origin="workbench_share",
        extra={"workbench_share": True},
        vm=_spoken_only_alone(vm, lane="share"),
        platform=platform,
    )
    _ledger_append({
        "at": time.time(),
        "disposition": "sent_share",
        "channel_id": int(channel_id),
        "chars": len(share),
        "medium": released["medium"],
        **({"voice_fallback_text": released["voice_fallback_text"]}
           if "voice_fallback_text" in released else {}),
        **({"learned_quiet_overridden_by_presence": learned_override_note}
           if learned_override_note else {}),
    })
    return {
        "disposition": "sent",
        "channel_id": int(channel_id),
        "medium": released["medium"],
    }


def _names_tonights_pick(thread_id: str) -> bool:
    """True when the organ is on and ``thread_id`` is the pick's board id.
    Off, the id is any other unknown id — the rail is as it was."""
    try:
        from ai import tonight

        return thread_id == tonight.THREAD_ID and tonight.tonight_enabled()
    except Exception:
        return False


async def _stamp_tonights_pick(sent_at: float) -> None:
    """Second read F10 (2026-10-04): a sent say that named tonight's pick is
    stamped said where the send is recorded. The pick is read and its file
    stamped with no await in between (load_current, then stamp_said's first
    step, mark_said — both synchronous), so the reply leg can never read it
    as unsaid after this say went out; the stake ledger's stamps follow. Said
    once holds: an already-said pick takes no stamp. Never raises (a cancel
    goes through) and never un-sends."""
    try:
        from ai import tonight

        pick = tonight.load_current()
        if pick is None or pick.said_at is not None:
            return
        await tonight.stamp_said(pick, at=sent_at)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.warning("arbiter: tonight's pick was not stamped at the send (%s) — the "
                       "moment's record tries again", exc or type(exc).__name__)


# ── the release gate ─────────────────────────────────────────────────────

async def consider_release(
    *, impulse: Dict[str, Any], owner_user_id: int, arbiter_live: bool
) -> Dict[str, Any]:
    """The rail verdict for one cycle's impulse. Returns {} when the rail
    has nothing to say about this impulse (flag off / not live / none /
    hold); otherwise {"disposition": ...} destined for meta.impulse.rail.
    Never raises: any unexpected error returns error_closed (no send)."""
    try:
        return await _consider_release_inner(
            impulse=impulse, owner_user_id=owner_user_id,
            arbiter_live=arbiter_live,
        )
    except Exception as exc:
        logger.warning("arbiter: release gate error (fail closed): %s", exc)
        return {
            "disposition": "error_closed",
            "error": " ".join(str(exc).split())[:120],
        }


async def _consider_release_inner(
    *, impulse: Dict[str, Any], owner_user_id: int, arbiter_live: bool
) -> Dict[str, Any]:
    if not arbiter_live or not arbiter_enabled():
        return {}
    kind = str(impulse.get("kind") or "none")
    if kind in ("none", "hold"):
        return {}
    if kind == "do":
        # Recorded, never acted: autonomous ACTION is the owner's separate
        # deliberate door (features.proactive_actions, spec law 3).
        return {"disposition": "do_recorded_only"}

    framing = str(impulse.get("framing") or "").strip()
    if not framing:
        return {"disposition": "held_no_framing"}

    picked = await _pick_surface(owner_id=owner_user_id)
    if picked is None:
        return {"disposition": "held_no_channel"}
    platform, surface_id = picked
    channel_id = int(surface_id)

    # Step 32: one voice-moment probe per consideration — feeds the medium
    # decision at release (and, spec §5, the presence override).
    vm = await _probe_voice_moment()

    held, learned_override_note = await _moment_gates(
        owner_user_id=owner_user_id, channel_id=int(channel_id),
        voice_present=bool(vm), platform=platform,
    )
    if held:
        return held

    # Care persistence (spec §0): confidence expressed her machinery's way —
    # a care-shaped reach must have survived at least one earlier moment.
    # held_before is HER declaration; the meaning stays in the LLM.
    sensitivity = str(impulse.get("sensitivity") or "routine")
    if sensitivity not in _SENSITIVITIES:
        sensitivity = "routine"
    if sensitivity == "care" and not bool(impulse.get("held_before")):
        return {"disposition": "held_care_first_look"}

    # The daily ceiling (ceiling 0 = rail dark). Before the judge: a held
    # reach-out never spends a paid call.
    held = admit_or_hold()
    if held:
        return held

    # Surprise naming budget (organ 8, spec §5): on top of the ceiling,
    # ≤ surprise_daily_named surprise-says per day; 0 = never named (the
    # ceiling's own outside-0-unlimited class). Named surprises still
    # spend the ordinary ceiling above — they are reaches.
    if sensitivity == "surprise":
        if surprise_named_today() >= int(_knob("surprise_daily_named", 1)):
            return {"disposition": "held_surprise_budget"}

    # Resolve her small thread id (the digest shows id[:8]) to a real row.
    thread_id_short = str(impulse.get("thread_id") or "").strip()
    thread_full_id = ""
    thread_updated_at = 0.0
    thread_unmatched = False
    if thread_id_short:
        try:
            for t in await _list_active(owner_user_id=owner_user_id):
                if str(t.id).startswith(thread_id_short):
                    thread_full_id = str(t.id)
                    thread_updated_at = float(getattr(t, "updated_at", 0.0) or 0.0)
                    break
        except Exception as exc:
            logger.warning("arbiter: thread resolve failed: %s", exc)
        if not thread_full_id:
            # The night's pick rides the board under a fixed id that is no
            # thread row (ai/tonight.py): named, it is not a lost thread.
            thread_unmatched = not _names_tonights_pick(thread_id_short)

    # Resolve her want id (re-spec §5.3) — exact slug, then prefix, over
    # open quests. Unmatched is recorded, never blocking: the ledger keeps
    # her raw token so the claim stays auditable either way.
    want_token = str(impulse.get("want_id") or "").strip()
    want_row = None
    want_unmatched = False
    if want_token:
        try:
            from ai.quest_store import resolve_want_id

            want_row = resolve_want_id(want_token)
        except Exception as exc:
            logger.warning("arbiter: want resolve failed: %s", exc)
        if want_row is None:
            want_unmatched = True

    # Step 25 · Regret (spec §4): her regret id — the ledger's integer id,
    # as the gathers tag it ("regret 7"). Resolved here, STAMPED only after
    # a successful send (record on send, never on intent). Unmatched is
    # recorded, never blocking — the want_id shape.
    regret_token = str(impulse.get("regret_id") or "").strip()
    regret_id: Optional[int] = None
    regret_unmatched = False
    if regret_token:
        m = re.search(r"\d+", regret_token)
        regret_id = int(m.group()) if m else None
        if regret_id is None:
            regret_unmatched = True

    # Organ 4.13 · Opinions (spec §5): her view id — resolved here, stamped
    # as LAST-SAID only after a successful send. Unmatched recorded, never
    # blocking — the want_id shape.
    view_token = str(impulse.get("view_id") or "").strip()
    view_id: Optional[int] = None
    view_unmatched = False
    if view_token:
        m = re.search(r"\d+", view_token)
        view_id = int(m.group()) if m else None
        if view_id is None:
            view_unmatched = True

    # Organ 4.9 · Grounded Vulnerability (spec §4): the small truth her say
    # offers — resolved here over the UNSAID book (exact slug, then a unique
    # prefix: the want_id shape, NEVER the digits shape beside it), and
    # STAMPED said only after a successful send. Unmatched is recorded,
    # never blocking — the view_id shape. Flag off ⇒ the leg is dark, token
    # and all.
    disclosure_token = str(impulse.get("disclosure_id") or "").strip()
    if disclosure_token and not _vulnerability_on():
        disclosure_token = ""      # dark: her token leaves no trace anywhere
    disclosure_row = None
    disclosure_unmatched = False
    if disclosure_token:
        try:
            from ai.disclosure_store import resolve_disclosure_id

            disclosure_row = resolve_disclosure_id(disclosure_token)
        except Exception as exc:
            logger.warning("arbiter: disclosure resolve failed: %s", exc)
        if disclosure_row is None:
            disclosure_unmatched = True

    # The presence slice (10-13): a thread the judge called stale carries no
    # message for a day, unless the thread itself changed since; the judge
    # is not asked again (10-12: one unanswered question, four drafts, four
    # stale drops). The night's pick waits under its own fixed id.
    stale_key = thread_full_id or (thread_id_short if not thread_unmatched else "")
    if stale_key and _dropped_stale_since(stale_key, after=thread_updated_at):
        logger.info("arbiter: thread %s was dropped stale in the last day; it waits", stale_key[:8])
        # a row, so `palli_ctl presence` can count the judge calls the wait
        # saved (Fable's final review); never a send, never an attempt
        _ledger_append({"at": time.time(), "disposition": "held_stale_wait",
                        "thread_id": stale_key, "chars": len(framing)})
        return {"disposition": "held_stale_wait"}

    # Freshness (spec §4) — the LAST gate, and the only paid one: it runs
    # only when every deterministic gate above already passed (≤ ceiling
    # calls/day). Gate, never rewriter: on fresh, `framing` proceeds
    # byte-identical; on stale, no send, reason recorded; on error, closed.
    if bool(_knob("arbiter_freshness_check", True)):
        try:
            fresh, reason = await _freshness_check(
                framing=framing, owner_user_id=int(owner_user_id),
                held_before=bool(impulse.get("held_before")),
            )
        except Exception as exc:
            logger.warning("arbiter: freshness check failed (fail closed): %s", exc)
            _ledger_append({
                "at": time.time(), "disposition": "stale_check_failed",
                "error": " ".join(str(exc).split())[:120],
            })
            return {"disposition": "stale_check_failed"}
        if not fresh:
            logger.info("arbiter: stale impulse dropped (%s)", reason)
            _ledger_append({
                "at": time.time(), "disposition": "stale_dropped",
                "reason": reason, "chars": len(framing),
                "sensitivity": sensitivity,
                **({"thread_id": stale_key} if stale_key else {}),
            })
            return {"disposition": "stale_dropped", "reason": reason}

    # RELEASE. A failed TEXT send raises → error_closed via the outer
    # catch, with zero bookkeeping (thread stays eligible). Medium is
    # decided here (step 32): he's in the room, alone with her (10-13) ⇒
    # spoken; speak failure ⇒ the text fallback inside _release_outbound.
    released = await _release_outbound(
        owner_user_id=int(owner_user_id),
        channel_id=int(channel_id),
        text=framing,
        origin="arbiter",
        extra={
            "arbiter": True,
            "sensitivity": sensitivity,
            **({"thread_id": thread_full_id} if thread_full_id else {}),
            **({"want_id": want_row.id} if want_row else {}),
            **({"regret_id": regret_id} if regret_id is not None else {}),
            **({"view_id": view_id} if view_id is not None else {}),
            **({"disclosure_id": disclosure_row.id} if disclosure_row else {}),
        },
        vm=_spoken_only_alone(vm, lane="say"),
        platform=platform,
    )

    sent_at = time.time()
    _ledger_append(
        {
            "at": sent_at,
            "disposition": "sent",
            "channel_id": int(channel_id),
            "chars": len(framing),
            "sensitivity": sensitivity,
            "medium": released["medium"],
            **({"voice_fallback_text": released["voice_fallback_text"]}
               if "voice_fallback_text" in released else {}),
            "thread_id": thread_full_id or thread_id_short,
            **({"want_id": (want_row.id if want_row else want_token)}
               if want_token else {}),
            **({"regret_id": (regret_id if regret_id is not None else regret_token)}
               if regret_token else {}),
            **({"view_id": (view_id if view_id is not None else view_token)}
               if view_token else {}),
            **({"disclosure_id": (disclosure_row.id if disclosure_row
                                  else disclosure_token)}
               if disclosure_token else {}),
            **({"learned_quiet_overridden_by_presence": learned_override_note}
               if learned_override_note else {}),
            "why": str(impulse.get("why") or "")[:200],
        }
    )
    if not thread_full_id and _names_tonights_pick(thread_id_short):
        # Second read F10: stamped here, at the ledger append — not after
        # the moment gets the verdict back, a few awaits later.
        await _stamp_tonights_pick(sent_at)
    if thread_full_id:
        try:
            await _mark_surfaced(thread_id=thread_full_id)
        except Exception as exc:
            logger.warning("arbiter: mark_surfaced failed: %s", exc)
    # Post-send bookkeeping (re-spec §5.4): the wishbook learns its want
    # was served. Fail-open like _mark_surfaced — a failed append logs
    # and never un-sends.
    if want_row:
        try:
            from ai.quest_store import add_progress

            add_progress(want_row.id, f"reached out: {framing[:60]}")
        except Exception as exc:
            logger.warning("arbiter: want progress append failed: %s", exc)
    # Step 25 · Regret: the one voicing is recorded ON SEND. Fail-open like
    # the want progress append — a failed stamp logs and never un-sends
    # (the voicing marker and the freshness judge are the other rails).
    regret_voiced = False
    if regret_id is not None:
        try:
            from ai.stake_ledger import mark_voiced

            regret_voiced = bool(await mark_voiced(regret_id))
            if not regret_voiced:
                logger.info(
                    "arbiter: regret_id %s not stamped (not an open, unvoiced regret)",
                    regret_id,
                )
        except Exception as exc:
            logger.warning("arbiter: regret voicing stamp failed: %s", exc)
    # Organ 4.13 · Opinions: a view is LAST-SAID, not once-ever — the stamp
    # lands on every send that names it. Fail-open like the regret stamp
    # beside it: a failed stamp logs and never un-sends.
    view_voiced = False
    if view_id is not None:
        try:
            from ai.stake_ledger import mark_view_voiced

            view_voiced = bool(await mark_view_voiced(view_id))
            if not view_voiced:
                logger.info("arbiter: view_id %s not stamped (not an open view)", view_id)
        except Exception as exc:
            logger.warning("arbiter: view voicing stamp failed: %s", exc)
    # Organ 4.9 · Grounded Vulnerability: the small truth is spent HERE and
    # only here on this door — after the send, never on the intent, because
    # said-once is forever and a truth stamped by a say that never landed
    # would be lost without ever being told. The store owns every refusal
    # (unknown / said / faded); the rail does not re-check the board.
    # Fail-open like the stamps above it: a failed stamp logs and never
    # un-sends.
    disclosure_shared = False
    if disclosure_row is not None:
        try:
            from ai.disclosure_store import mark_said

            disclosure_shared = bool(mark_said(disclosure_row.id, via="say"))
            if not disclosure_shared:
                logger.info(
                    "arbiter: disclosure %r not stamped (unknown, said, or faded)",
                    disclosure_row.id,
                )
        except Exception as exc:
            logger.warning("arbiter: disclosure stamp failed: %s", exc)

    logger.info(
        "arbiter: released a %s say to channel %s as %s (%d chars, thread=%s)",
        sensitivity, channel_id, released["medium"], len(framing),
        thread_full_id or "-",
    )
    verdict: Dict[str, Any] = {
        "disposition": "sent",
        "channel_id": int(channel_id),
        "medium": released["medium"],
        "at": sent_at,
    }
    if thread_unmatched:
        verdict["thread_unmatched"] = True
    if want_unmatched:
        verdict["want_unmatched"] = True
    if regret_unmatched:
        verdict["regret_unmatched"] = True
    if regret_id is not None:
        verdict["regret_voiced"] = regret_voiced
    if view_unmatched:
        verdict["view_unmatched"] = True
    if view_id is not None:
        verdict["view_voiced"] = view_voiced
    if disclosure_unmatched:
        verdict["disclosure_unmatched"] = True
    if disclosure_row is not None:
        verdict["disclosure_shared"] = disclosure_shared
    return verdict


# ── the expiry sweep (spec §7) ───────────────────────────────────────────

def _sweep_state_path() -> Path:
    return ARBITER_DATA_DIR / "sweep_state.json"


async def maybe_run_daily_sweep(*, owner_user_id: int) -> None:
    """Once per day, expire what her judgment never touched. Claim-on-
    success: a failed sweep retries at the next tick instead of losing the
    day (the D1 lesson). Never raises."""
    try:
        if not arbiter_enabled():
            return
        today = _today()
        try:
            state = json.loads(_sweep_state_path().read_text(encoding="utf-8"))
        except Exception:
            state = {}
        if state.get("last_swept") == today:
            return
        expired = await _expire_stale(
            owner_user_id=int(owner_user_id),
            followup_ttl_days=float(_knob("arbiter_followup_ttl_days", 7)),
            thread_ttl_days=float(_knob("arbiter_thread_ttl_days", 45)),
        )
        for tid, kind, age_days in expired or []:
            logger.info(
                "arbiter sweep: expired thread %s (kind=%s, held %dd)",
                str(tid)[:8], kind, age_days,
            )
        ARBITER_DATA_DIR.mkdir(parents=True, exist_ok=True)
        _sweep_state_path().write_text(
            json.dumps({"last_swept": today}), encoding="utf-8"
        )
    except Exception as exc:
        logger.warning("arbiter: sweep failed (will retry next tick): %s", exc)
