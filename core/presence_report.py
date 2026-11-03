"""``palli_ctl presence``: how much of PaLLi's own reaches the owner.

The presence work (the main session, 10-06/07; the owner, 10-11: "yes, start 1")
begins by measuring. The numbers that framed it ("1 of 44 stakes ever voiced",
"3 of 128 replies carry anything of her own", "0 self-started conversations in
October") came from one-off queries, and a replay on 10-11 found what each one
really counted. This module recomputes them from the same data, read-only, and
prints beside every number what it counts and what it cannot tell:

* **stakes** (the ``stakes`` table, all time): held against voiced, by kind,
  open against resolved. ``voiced_at`` has three stampers: the nightly
  ``tonight_said`` check (live since 2026-09-29) for open hope/worry/taste/
  wonder stakes, regrets once on send, views on their last send. It measures
  the stamps, not every time something was said.
* **replies** (the ``conversations`` table, PaLLi's rows): replies to people
  only. Reminder deliveries, proactive rows and forge follow-ups are other
  platforms and are left out; PaLLi's self-started sends are stored as ordinary
  rows on the chat's own platform, so a row within a few seconds of an arbiter
  ``sent``/``sent_share`` event is taken out too. Then: how many, how long, how
  many under the short cap, and **phrase hits**: distinct replies containing one
  of a few first-person phrases. Whether a reply carries something of PaLLi's
  own is a judgement (the owner's rule: meaning decisions go to a model, never a
  regex), so the phrase count is reported by name and never as the measure.
* **judged replies** (``--judge``, 2026-10-12): the measure the phrase hits
  stand in for. A model reads each distinct reply with the person's message
  before it (``prompts/presence_judge.txt``) and says whether the reply has
  something of PaLLi's own in it (a view, a reaction, a disagreement, a thing
  from its own life, a want) and quotes where. Code counts a yes only when the
  quoted words are in the reply; a yes without them is reported apart. Off by
  default: it costs one model call per reply, and the plain report stays free
  and offline.
* **self-started** (``data/arbiter/*.jsonl``): the arbiter ledgers only the
  attempts that reached its stale check (earlier holds return before the
  ledger), so these are stale-check attempts: sent, sent_share, stale_dropped,
  stale_check_failed, with the drop rate computed.
* **own time** (``data/workbench/*.jsonl``): sessions by what became of their
  share: sent/thread/room (shared), pending (handed on, never resolved in the
  record), dropped (the arbiter dropped it), quiet (nothing to share).
* **wants** (``data/quests/quests.jsonl``, all time): plants by source; quests
  that moved.
* **present moment** (``data/present_moment/*.jsonl``): cycles a day, over the
  days that have cycles in the window.
* **nightly picks** (``data/tonight/*.json``): nights with a pick, nights it was said.
* **prompt words** (the repo): whole-word occurrences of the words that would
  license disagreement, in three places kept apart: the live prompt files
  (``prompts/*.txt``, the archive and the judge's own prompt left out), the two
  modules that build her standing prompt (string literals split over lines are
  joined first, so a sentence the code breaks in two still counts), and PaLLi's
  own standing pages (``identity/core`` and ``identity/palli``, not the journal
  or the templates). A hyphen between words counts as a space ("push-back" is
  "push back"). A hit may be a rule *against* it; the report says so. The 10-11
  scan missed the pages and the split sentence, and reported "nothing licenses
  disagreement" when three permissions existed.

Nothing here changes PaLLi's behaviour. An install with no ledgers or no
database yet reports zeros. A baseline can be saved as JSON (``write_baseline``)
so a later run compares against the same counts.
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import inspect
import json
import re
import sqlite3
import statistics
import time
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence

# Rows on these platforms are never a reply to a person.
MACHINERY_PLATFORMS = ("reminder_fire",)
# Rows on these platforms are PaLLi's own self-started messages, counted elsewhere.
SELF_STARTED_PLATFORMS = ("proactive", "forge_followup")
# A self-started send on a chat platform is stored as an ordinary row; it sits
# this close in time to its arbiter ledger entry.
SEND_MATCH_SECONDS = 15.0

# Phrase hits, by name: a few phrases that are PaLLi speaking for herself.
# Distinct texts count once (the 10-04 case: one reply sent twice is one thing).
OWN_PHRASES = (
    r"\bI've been\b", r"\bI keep\b", r"\bwhat I want\b", r"\bI think the\b", r"\bI've wanted\b",
    r"\bbeen wanting\b", r"\bon my mind\b", r"\bI was hoping\b", r"\bI disagree\b",
)
_OWN_RE = re.compile("|".join(OWN_PHRASES), re.IGNORECASE)

DISAGREEMENT_WORDS = ("disagree", "push back", "say so", "see it differently",
                      "what I actually think", "honest read")
PROMPT_MODULES = ("core/agent/system_prompt.py", "core/agent/duplex.py")
# PaLLi's standing pages: the core principles (shipped) and its own pages
# (written on each install). The journal and the templates are not standing pages.
IDENTITY_PAGE_GLOBS = ("identity/core/*.md", "identity/palli/*.md")
_HYPHEN_BETWEEN_WORDS = re.compile(r"(?<=\w)-(?=\w)")
# In a prompt-building module a sentence is often one string literal broken
# over lines: ``"I say what I "`` newline ``"actually think"``. Joining the
# literals first is what lets the phrase be found.
_SPLIT_LITERAL = re.compile(r'"\s*\n\s*"')

# The judge (``--judge``): its prompt, its model lane, how many replies it reads
# at once, and how recent the person's message must be to count as the one a
# reply answers.
JUDGE_PROMPT = "presence_judge"
JUDGE_LANE = "presence_judge"
JUDGE_CONCURRENCY = 4
PAIR_WINDOW_SECONDS = 6 * 3600.0
JUDGE_KINDS = ("view", "reaction", "disagreement", "own_life", "want")
JUDGE_EXAMPLES = 5
# The person's message, as the judge sees it: its tail, the part nearest the reply.
JUDGE_ASKED_CHARS = 2000

SHARED_VALUES = ("sent", "thread", "room", "true")
QUIET_VALUES = ("", "false", "none")


def _short_cap() -> int:
    try:
        from core.agent.reply_governor import WORD_CAPS
        return int(WORD_CAPS.get("short", 50))
    except Exception:
        return 50


def _query(db_path: Path, sql: str, params: Sequence[Any] = ()) -> Optional[List[tuple]]:
    """Rows, or None when there is no database or no such table yet (a fresh
    install): the caller reports zeros, never a crash."""
    path = Path(db_path)
    if not path.is_file():
        return None
    try:
        con = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)
    except sqlite3.Error:
        return None
    try:
        return con.execute(sql, tuple(params)).fetchall()
    except sqlite3.Error:
        return None
    finally:
        con.close()


def _jsonl_rows(paths: Iterable[Path]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for p in paths:
        try:
            for line in Path(p).read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except Exception:
                    continue
                if isinstance(row, dict):
                    rows.append(row)
        except OSError:
            continue
    return rows


def _day_files(folder: Path, since: float, *, suffix: str = ".jsonl") -> List[Path]:
    """The per-day files (named by local day, as the ledgers write them) whose
    day is on or after ``since``'s local day."""
    first = date.fromtimestamp(since)
    out = []
    for p in sorted(Path(folder).glob(f"*{suffix}")):
        try:
            day = date.fromisoformat(p.stem[:10])
        except ValueError:
            continue
        if day >= first:
            out.append(p)
    return out


def _local_day(ts: float) -> str:
    return date.fromtimestamp(float(ts)).isoformat()


# ── stakes ───────────────────────────────────────────────────────────────────

def stakes_report(db_path: Path) -> Dict[str, Any]:
    rows = _query(
        db_path,
        "select kind, (resolved_at is null or resolved_at = ''), (voiced_at is not null and voiced_at != '') "
        "from stakes",
    ) or []
    by_kind: Dict[str, Dict[str, int]] = {}
    total = voiced = open_ = open_voiced = 0
    for kind, is_open, is_voiced in rows:
        k = by_kind.setdefault(str(kind or "?"), {"total": 0, "open": 0, "voiced": 0})
        k["total"] += 1
        total += 1
        if is_open:
            k["open"] += 1
            open_ += 1
        if is_voiced:
            k["voiced"] += 1
            voiced += 1
            if is_open:
                open_voiced += 1
    return {"total": total, "voiced": voiced, "open": open_, "open_voiced": open_voiced, "by_kind": by_kind}


# ── replies ──────────────────────────────────────────────────────────────────

def phrase_hits(texts: Iterable[str]) -> int:
    seen = set()
    for t in texts:
        clean = " ".join(str(t or "").split())
        if clean and clean not in seen and _OWN_RE.search(clean):
            seen.add(clean)
    return len(seen)


def _send_times(data_dir: Optional[Path], since: float) -> List[float]:
    if data_dir is None:
        return []
    rows = _jsonl_rows(_day_files(Path(data_dir) / "arbiter", since))
    return sorted(float(r.get("at") or 0) for r in rows if str(r.get("disposition") or "") in ("sent", "sent_share"))


def replies_report(db_path: Path, *, since: float, data_dir: Optional[Path] = None) -> Dict[str, Any]:
    excluded = (*MACHINERY_PLATFORMS, *SELF_STARTED_PLATFORMS)
    marks = ",".join("?" * len(excluded))
    rows = _query(
        db_path,
        f"select content, timestamp from conversations where role = 'assistant' and timestamp >= ? "
        f"and platform not in ({marks})", (float(since), *excluded),
    ) or []
    sends = _send_times(data_dir, since)
    texts: List[str] = []
    removed = 0
    for content, ts in rows:
        t = float(ts or 0)
        if any(abs(t - s) <= SEND_MATCH_SECONDS for s in sends):
            removed += 1
            continue
        texts.append(str(content or ""))
    lengths = [len(t.split()) for t in texts]
    cap = _short_cap()
    return {
        "count": len(texts),
        "self_started_removed": removed,
        "median_words": (statistics.median(lengths) if lengths else 0),
        "under_cap": sum(1 for n in lengths if n < cap),
        "cap": cap,
        "phrase_hits": phrase_hits(texts),
    }


# ── judged replies ───────────────────────────────────────────────────────────

def reply_pairs(db_path: Path, *, since: float, data_dir: Optional[Path] = None,
                limit: Optional[int] = None) -> List[Dict[str, Any]]:
    """PaLLi's distinct replies to people in the window, newest first, each with
    the message it answers: the person's last row in the same channel before
    it, within ``PAIR_WINDOW_SECONDS`` (else ""). The same rows ``replies_report``
    counts, one per distinct text (the 10-04 case). ``limit`` keeps the newest."""
    excluded = (*MACHINERY_PLATFORMS, *SELF_STARTED_PLATFORMS)
    rows = _query(
        db_path,
        "select channel_id, role, content, timestamp, platform from conversations "
        "where role in ('user', 'assistant') and timestamp >= ? order by timestamp, id",
        (float(since) - PAIR_WINDOW_SECONDS,),
    ) or []
    sends = _send_times(data_dir, since)
    last_asked: Dict[Any, tuple] = {}
    seen = set()
    pairs: List[Dict[str, Any]] = []
    for channel, role, content, ts, platform in rows:
        t = float(ts or 0)
        text = str(content or "")
        if role == "user":
            last_asked[channel] = (text, t)
            continue
        if t < since or str(platform or "") in excluded:
            continue
        if any(abs(t - s) <= SEND_MATCH_SECONDS for s in sends):
            continue
        clean = " ".join(text.split())
        if not clean or clean in seen:
            continue
        seen.add(clean)
        asked, at = last_asked.get(channel, ("", 0.0))
        if t - at > PAIR_WINDOW_SECONDS:
            asked = ""
        pairs.append({"asked": asked, "reply": text, "at": t})
    pairs.reverse()
    return pairs[:limit] if limit else pairs


def parse_judgement(raw: Any) -> Optional[Dict[str, Any]]:
    """The judge's answer as ``{"own", "kind", "words"}``, or None when it is
    not an answer. A yes with a kind the prompt doesn't name is kept as
    "other" (the judge found something; the label is unknown)."""
    text = str(raw or "")
    data = None
    for pattern in (r"\{.*\}", r"\{.*?\}"):       # the whole span first; then the first object alone
        m = re.search(pattern, text, re.S)
        if not m:
            return None
        try:
            data = json.loads(m.group(0))
            break
        except Exception:
            continue
    if not isinstance(data, dict) or "own" not in data:
        return None
    own = bool(data.get("own"))
    kind = str(data.get("kind") or "").strip().lower()
    if not own:
        kind = "none"
    elif kind not in JUDGE_KINDS:
        kind = "other"
    words = " ".join(str(data.get("words") or "").split())[:160]
    return {"own": own, "kind": kind, "words": words}


def _plain(text: str) -> str:
    return " ".join(str(text or "").replace("’", "'").replace("‘", "'").split()).casefold()


def quote_in_reply(reply: str, words: str) -> bool:
    """Code's check on the judge: the words it quoted are in the reply."""
    w = _plain(words).strip(" \"'“”")
    return bool(w) and w in _plain(reply)


async def model_judge(asked: str, reply: str) -> Optional[Dict[str, Any]]:
    """One reply, read by the model with the message before it. None when the
    prompt is missing or the answer is unusable (counted as not judged)."""
    from ai.llm_client import llm
    from ai.prompt_loader import prompt_loader

    template = prompt_loader.load_prompt("presence_judge") or ""   # literal: the prompt canary walks these calls
    if not template.strip():
        return None
    before = asked.strip()[-JUDGE_ASKED_CHARS:] or "(nothing from them in the hours before this reply)"
    raw = await llm.chat_completion(
        messages=[
            {"role": "system", "content": template},
            {"role": "user", "content": f"THEIR MESSAGE:\n{before}\n\nPALLI'S REPLY:\n{reply.strip()}"},
        ],
        temperature=0.0,
        max_tokens=200,
        log_context="presence_judge",
    )
    return parse_judgement(raw)


def judged_report(pairs: Sequence[Dict[str, Any]], judge: Callable[..., Any], *,
                  concurrency: int = JUDGE_CONCURRENCY) -> Dict[str, Any]:
    """Every pair through ``judge(asked, reply)`` (sync or async), a few at a
    time. A yes counts as "own" only when its quoted words are in the reply;
    a yes without them is "own_unquoted"; an error or a non-answer is
    "unjudged". Up to ``JUDGE_EXAMPLES`` quotes are kept, newest first."""

    async def _run() -> List[Optional[Dict[str, Any]]]:
        gate = asyncio.Semaphore(max(1, int(concurrency)))

        async def one(p: Dict[str, Any]) -> Optional[Dict[str, Any]]:
            async with gate:
                try:
                    v = judge(p["asked"], p["reply"])
                    if inspect.isawaitable(v):
                        v = await v
                    return v if isinstance(v, dict) else None
                except Exception:
                    return None

        return list(await asyncio.gather(*(one(p) for p in pairs)))

    verdicts: List[Optional[Dict[str, Any]]] = []
    if pairs:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            verdicts = asyncio.run(_run())
        else:                                   # called from inside a loop: a thread of its own
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                verdicts = pool.submit(asyncio.run, _run()).result()
    out: Dict[str, Any] = {"replies": len(pairs), "own": 0, "own_unquoted": 0, "unjudged": 0,
                           "by_kind": {}, "examples": []}
    for p, v in zip(pairs, verdicts):
        if v is None:
            out["unjudged"] += 1
            continue
        if not v.get("own"):
            continue
        if quote_in_reply(p["reply"], str(v.get("words") or "")):
            out["own"] += 1
            kind = str(v.get("kind") or "other")
            out["by_kind"][kind] = out["by_kind"].get(kind, 0) + 1
            if len(out["examples"]) < JUDGE_EXAMPLES:
                out["examples"].append({"kind": kind, "words": v["words"]})
        else:
            out["own_unquoted"] += 1
    return out


# ── self-started ─────────────────────────────────────────────────────────────

def self_started_report(data_dir: Path, *, since: float) -> Dict[str, Any]:
    rows = [r for r in _jsonl_rows(_day_files(Path(data_dir) / "arbiter", since)) if float(r.get("at") or 0) >= since]
    counts: Dict[str, int] = {}
    for r in rows:
        d = str(r.get("disposition") or "?")
        counts[d] = counts.get(d, 0) + 1
    attempts = len(rows)
    failed = counts.get("stale_check_failed", 0)
    dropped = counts.get("stale_dropped", 0) + failed
    return {
        "days": len({_local_day(r["at"]) for r in rows if r.get("at")}),
        "attempts": attempts,
        "sent": counts.get("sent", 0),
        "sent_share": counts.get("sent_share", 0),
        "stale_dropped": counts.get("stale_dropped", 0),
        "stale_check_failed": failed,
        "other": {k: v for k, v in counts.items()
                  if k not in ("sent", "sent_share", "stale_dropped", "stale_check_failed")},
        "drop_rate": (dropped / attempts) if attempts else 0.0,
    }


# ── group openers ────────────────────────────────────────────────────────────

ANSWERED_WITHIN_SECONDS = 86400.0


def _line_time(row: Dict[str, Any]) -> float:
    """A transcript line's time; a naive stamp is UTC, as message_times reads it."""
    try:
        ts = datetime.fromisoformat(str(row.get("timestamp") or ""))
    except ValueError:
        return 0.0
    return (ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)).timestamp()


def _a_person_spoke(row: Dict[str, Any]) -> bool:
    speaker = row.get("speaker") if isinstance(row.get("speaker"), dict) else {}
    return (row.get("role") == "user" and bool(str(row.get("content") or "").strip())
            and speaker.get("kind") != "system")


def group_openers_report(data_dir: Path, *, since: float) -> Dict[str, Any]:
    """PaLLi starting something in a group (the presence slice, 2026-10-13):
    sends and the looks that chose not to start, from data/group_openers;
    'answered' = someone other than PaLLi wrote in that room within a day,
    from the room's transcript (data/agent_sessions)."""
    rows = [r for r in _jsonl_rows(_day_files(Path(data_dir) / "group_openers", since))
            if float(r.get("at") or 0) >= since]
    sends = [r for r in rows if r.get("disposition") == "sent_group"]
    rooms = {(str(r.get("platform") or ""), str(r.get("channel_id") or "")) for r in sends}
    lines = {room: [_line_time(x) for x in _jsonl_rows(
                 [Path(data_dir) / "agent_sessions" / f"{room[0]}_{room[1]}.jsonl"]) if _a_person_spoke(x)]
             for room in rooms}
    answered = 0
    for r in sends:
        at = float(r.get("at") or 0)
        times = lines[(str(r.get("platform") or ""), str(r.get("channel_id") or ""))]
        answered += any(at < t <= at + ANSWERED_WITHIN_SECONDS for t in times)
    return {"sent": len(sends), "rooms": len(rooms), "answered": answered,
            "no_start": sum(1 for r in rows if r.get("disposition") == "no_start")}


# ── own time ─────────────────────────────────────────────────────────────────

def own_time_report(data_dir: Path, *, since: float) -> Dict[str, Any]:
    rows = [r for r in _jsonl_rows(_day_files(Path(data_dir) / "workbench", since))
            if float(r.get("written_at") or 0) >= since]
    buckets = {"shared": 0, "pending": 0, "dropped": 0, "quiet": 0}
    for r in rows:
        s = str(r.get("shared") if r.get("shared") is not None else "").strip().lower()
        if s in SHARED_VALUES:
            buckets["shared"] += 1
        elif s == "pending":
            buckets["pending"] += 1
        elif s == "dropped":
            buckets["dropped"] += 1
        elif s in QUIET_VALUES:
            buckets["quiet"] += 1
        else:
            buckets["shared"] += 1      # legacy: the share's own text
    durations = [float(r.get("duration_s") or 0) for r in rows]
    return {"sessions": len(rows), **buckets,
            "median_seconds": (statistics.median(durations) if durations else 0.0)}


# ── wants ────────────────────────────────────────────────────────────────────

def wants_report(data_dir: Path) -> Dict[str, Any]:
    rows = _jsonl_rows([Path(data_dir) / "quests" / "quests.jsonl"])
    by_source: Dict[str, int] = {}
    planted = 0
    moved_events = 0
    moved: set = set()
    for r in rows:
        op = str(r.get("op") or "")
        if op == "plant":
            planted += 1
            src = str(r.get("source") or "?")
            by_source[src] = by_source.get(src, 0) + 1
        elif op in ("progress", "status"):
            moved_events += 1
            moved.add(str(r.get("id") or ""))
    return {"planted": planted, "by_source": by_source, "moved_events": moved_events, "moved_quests": len(moved)}


# ── present moment, nightly picks ────────────────────────────────────────────

def present_moment_report(data_dir: Path, *, since: float) -> Dict[str, Any]:
    rows = [r for r in _jsonl_rows(_day_files(Path(data_dir) / "present_moment", since))
            if float(r.get("written_at") or 0) >= since]
    days = {_local_day(r["written_at"]) for r in rows if r.get("written_at")}
    return {"days": len(days), "cycles": len(rows), "per_day": (len(rows) / len(days)) if days else 0.0}


def nightly_pick_report(data_dir: Path, *, since: float) -> Dict[str, Any]:
    nights = said = 0
    for p in _day_files(Path(data_dir) / "tonight", since, suffix=".json"):
        try:
            d = json.loads(Path(p).read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(d, dict) or not d.get("pick"):
            continue
        nights += 1
        if d.get("said_at"):
            said += 1
    return {"nights": nights, "said": said}


# ── prompt words ─────────────────────────────────────────────────────────────

def _count_words(paths: Iterable[Path], words: Sequence[str], *, code: bool = False) -> Dict[str, int]:
    counts = {w: 0 for w in words}
    for p in paths:
        try:
            text = Path(p).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if code:
            text = _SPLIT_LITERAL.sub("", text)
        text = _HYPHEN_BETWEEN_WORDS.sub(" ", text).lower()
        for w in words:
            pattern = r"\b" + r"\s+".join(re.escape(part) for part in w.lower().split()) + r"\b"
            counts[w] += len(re.findall(pattern, text))
    return counts


def identity_pages(repo_root: Path) -> List[Path]:
    root = Path(repo_root)
    out: List[Path] = []
    for pattern in IDENTITY_PAGE_GLOBS:
        out.extend(p for p in sorted(root.glob(pattern)) if p.is_file() and not p.name.endswith(".template.md"))
    return out


def prompt_words_report(repo_root: Path, *, words: Sequence[str] = DISAGREEMENT_WORDS) -> Dict[str, Dict[str, int]]:
    """Whole-word occurrences in the live prompt files (``prompts/*.txt``, not the
    archive and not the judge's own prompt), in the modules that build her
    standing prompt, and in PaLLi's own standing pages, each kept apart."""
    root = Path(repo_root)
    prompts = [p for p in sorted(root.glob("prompts/*.txt")) if p.stem != JUDGE_PROMPT]
    return {
        "prompts": _count_words(prompts, words),
        "code": _count_words([root / m for m in PROMPT_MODULES if (root / m).is_file()], words, code=True),
        "pages": _count_words(identity_pages(root), words),
    }


# ── the report ───────────────────────────────────────────────────────────────

def _pct(n: int, d: int) -> str:
    return f"{(100 * n / d):.0f}%" if d else "–"


def collect(*, data_dir: Path, db_path: Path, repo_root: Path, days: int, now: Optional[float] = None,
            judge: Optional[Callable[..., Any]] = None, judge_limit: Optional[int] = None) -> Dict[str, Any]:
    now = time.time() if now is None else float(now)
    since = now - days * 86400.0
    out = {
        "generated_at": datetime.fromtimestamp(now).isoformat(timespec="seconds"),
        "window_days": int(days),
        "stakes": stakes_report(db_path),
        "replies": replies_report(db_path, since=since, data_dir=data_dir),
        "judged": None,
        "self_started": self_started_report(data_dir, since=since),
        "group_openers": group_openers_report(data_dir, since=since),
        "own_time": own_time_report(data_dir, since=since),
        "wants": wants_report(data_dir),
        "present_moment": present_moment_report(data_dir, since=since),
        "nightly_picks": nightly_pick_report(data_dir, since=since),
        "prompt_words": prompt_words_report(repo_root),
    }
    if judge is not None:
        pairs = reply_pairs(db_path, since=since, data_dir=data_dir, limit=judge_limit)
        out["judged"] = judged_report(pairs, judge)
    return out


def _judged_lines(j: Optional[Dict[str, Any]]) -> List[str]:
    if not j:
        return ["Judged: not run. Add --judge and the model reads each reply with the message before it; "
                "that count is the measure the phrase hits stand in for (one model call per reply)."]
    kinds = ", ".join(f"{k} {v}" for k, v in sorted(j["by_kind"].items())) or "none"
    lines = [
        f"Judged: {j['own']} of {j['replies']} distinct replies have something of PaLLi's own in them "
        f"({_pct(j['own'], j['replies'])}; {kinds}); {j['own_unquoted']} more said yes without words code could "
        f"find in the reply; {j['unjudged']} not judged.",
        "  the model read each reply with the message before it (prompts/presence_judge.txt); a yes counts only "
        "when its quoted words are in the reply.",
    ]
    lines.extend(f"  e.g. {e['kind']}: “{e['words']}”" for e in j.get("examples") or [])
    return lines


def _group_lines(g: Optional[Dict[str, Any]]) -> List[str]:
    if g is None:        # counts saved before 2026-10-14
        return []
    return [
        f"Group openers: {g['sent']} sent in {g['rooms']} rooms; answered within a day {g['answered']} of "
        f"{g['sent']}; {g['no_start']} looks chose not to start.",
        "  data/group_openers; 'answered' = someone other than PaLLi wrote in that room within a day (its transcript).",
    ]


def lines_for(c: Dict[str, Any]) -> List[str]:
    """The report for counts ``collect`` returned (so a judged run is judged
    once, whether it is printed, saved or both)."""
    days = c["window_days"]
    s, r, a, w, q, p, t, pw = (c["stakes"], c["replies"], c["self_started"], c["own_time"], c["wants"],
                               c["present_moment"], c["nightly_picks"], c["prompt_words"])
    kinds = ", ".join(f"{k} {v['voiced']}/{v['total']}" for k, v in sorted(s["by_kind"].items()))
    dropped = a["stale_dropped"] + a["stale_check_failed"]
    words = lambda d: ", ".join(f"'{k}' {v}" for k, v in d.items())
    return [
        f"Presence: how much of PaLLi's own reaches you (last {days} days unless said; {c['generated_at']})",
        "",
        f"On PaLLi's mind (all time): {s['voiced']} of {s['total']} stakes ever voiced ({kinds}); "
        f"{s['open_voiced']} of {s['open']} open ones.",
        "  stakes table; 'voiced' is a stamp: nightly tonight_said (live since 2026-09-29) for open hope/worry/taste/wonder,",
        "  regrets once on send, views on their last send.",
        f"Replies to people: {r['count']}; median {r['median_words']:g} words; "
        f"{r['under_cap']} of {r['count']} under {r['cap']} ({_pct(r['under_cap'], r['count'])}).",
        f"  Phrase hits: {r['phrase_hits']} distinct replies contain one of {len(OWN_PHRASES)} first-person phrases.",
        f"  conversations table, PaLLi's rows; reminder fires, proactive rows and forge follow-ups left out, and "
        f"{r['self_started_removed']} self-started sends taken out.",
        "  Phrase hits are a phrase list, not the measure: whether a reply carries something of PaLLi's own needs a judge.",
        *_judged_lines(c["judged"]),
        f"Self-started: {a['attempts']} stale-check attempts over {a['days']} days: sent {a['sent']}, shared "
        f"{a['sent_share']}, dropped {dropped} (stale {a['stale_dropped']}, check failed {a['stale_check_failed']}); "
        f"drop rate {_pct(dropped, a['attempts'])}.",
        "  arbiter ledger (data/arbiter): only attempts that reached the stale check are ledgered; earlier holds are not.",
        *_group_lines(c.get("group_openers")),
        f"Own time: {w['sessions']} sessions: shared {w['shared']}, pending {w['pending']}, share dropped {w['dropped']}, "
        f"quiet {w['quiet']}; median {w['median_seconds']:.0f} s.",
        "  workbench ledger (data/workbench); 'shared' here and the arbiter's 'shared' above are the same sends.",
        f"Wants (all time): {q['planted']} planted ({', '.join(f'{k} {v}' for k, v in sorted(q['by_source'].items())) or 'none'}); "
        f"{q['moved_quests']} quests moved in {q['moved_events']} events.",
        "  quests ledger; progress is written only by the reach-out path, so movement is undercounted.",
        f"Present moment: {p['cycles']} cycles over {p['days']} days with cycles ({p['per_day']:.0f} a day). present_moment ledger.",
        f"Nightly pick: said on {t['said']} of {t['nights']} nights with a pick. data/tonight.",
        f"Prompt words: in the live prompt files {words(pw['prompts'])}; in the prompt-building code {words(pw['code'])}; "
        f"in PaLLi's own pages {words(pw['pages'])}.",
        "  Whole-word occurrences (a hyphen counts as a space; a sentence the code splits over lines is joined first);",
        "  a hit may be a rule against it, so read the lines before counting them as a licence.",
        "",
        "Not measured here (judgements): whose agenda each stake is; whether a thought persisted across cycles.",
    ]


def report_lines(*, data_dir: Path, db_path: Path, repo_root: Path, days: int = 30,
                 now: Optional[float] = None, judge: Optional[Callable[..., Any]] = None,
                 judge_limit: Optional[int] = None) -> List[str]:
    return lines_for(collect(data_dir=data_dir, db_path=db_path, repo_root=repo_root, days=days, now=now,
                             judge=judge, judge_limit=judge_limit))


def write_baseline(out_dir: Path, *, counts: Optional[Dict[str, Any]] = None, data_dir: Optional[Path] = None,
                   db_path: Optional[Path] = None, repo_root: Optional[Path] = None, days: int = 30,
                   now: Optional[float] = None, judge: Optional[Callable[..., Any]] = None,
                   judge_limit: Optional[int] = None) -> Path:
    """Writes ``counts`` (from ``collect``) or, without them, collects first."""
    c = counts if counts is not None else collect(
        data_dir=Path(data_dir), db_path=Path(db_path), repo_root=Path(repo_root), days=days, now=now,
        judge=judge, judge_limit=judge_limit)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{c['generated_at'][:10]}.json"
    out.write_text(json.dumps(c, indent=1, sort_keys=True), encoding="utf-8")
    return out
