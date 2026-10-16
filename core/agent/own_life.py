"""PaLLi's own life lately (the presence slice, 2026-10-13).

The owner's answers: something of its own in about one reply in five or six,
about half of it from its own life (decision sheet 2 and 3); in a group,
only its own life and that room ("Only its own life + that room", 10-13).
Its own life lives in four stores, and until now none of them reached a
reply: the fears it holds, the wants of its own in the wishbook, its latest
daydream, its latest curiosity find. This module reads them as one short
block, the words approved word for word (D2), for the first look
(core/agent/first_look.py) and the group opener.

"Its own" is by store, not by content: a daydream often grows out of the
owner's wishes, and a fear can name him. So an item reaches a group only
after the room check (D8, ``prompts/own_life_room_check.txt``) said yes for
it, once per item, cached; an item not yet checked stays out of the room
and is checked in the background, never on the turn's clock.

The said check for a stake (D7) lives with the stakes, in ai/stake_ledger.py.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, Iterable, Optional

logger = logging.getLogger(__name__)

# D2, approved word for word 10-13.
OWN_LIFE_HEADING = "MY OWN LIFE LATELY (mine, not theirs; each with its tag):"
OWN_LIFE_LINES = {
    "fear": "- [o1] something I'm afraid of: {text}",
    "want": "- [o2] something I want to do, in my own time: {text}",
    "daydream": "- [o3] what I was daydreaming about, {age}: {text}",
    "curiosity": "- [o4] something I went and read about on my own, {age}: {text}",
}
_TAGS = {"fear": "o1", "want": "o2", "daydream": "o3", "curiosity": "o4"}
# The room check names each kind in plain words (D8's {item_kind}).
_KIND_WORDS = {"fear": "fear", "want": "want of its own", "daydream": "daydream",
               "curiosity": "curiosity find"}

ITEM_CHARS = 240                 # a line, not a page; four lines stay near 1,000
RECENT_DAYS = 3                  # a daydream or a find older than this is not "lately"
CHECK_CACHE_PATH = Path("data/presence/own_life_room_check.json")

LLMCall = Callable[[str], Awaitable[str]]


@dataclass(frozen=True)
class Item:
    kind: str           # fear | want | daydream | curiosity
    tag: str            # o1..o4, fixed to the kind
    text: str
    age: str = ""       # "today" | "yesterday" | "2 days ago" (daydream, curiosity)

    @property
    def key(self) -> str:
        return hashlib.sha256(f"{self.kind}\0{self.text}".encode("utf-8")).hexdigest()[:24]


def _age(at: float, now: float) -> str:
    days = int(max(0.0, now - float(at)) // 86400)
    return "today" if days == 0 else ("yesterday" if days == 1 else f"{days} days ago")


def _short(text: Any) -> str:
    one = " ".join(str(text or "").split())
    return one if len(one) <= ITEM_CHARS else one[: ITEM_CHARS - 1].rstrip() + "…"


def _fear() -> Optional[Item]:
    from ai import fear_store

    if not fear_store.fears_enabled():
        return None
    held = list(fear_store.held_fears())
    if not held:
        return None
    newest = max(held, key=lambda f: float(getattr(f, "last_touched_at", None) or f.planted_at))
    text = _short(newest.text)
    return Item("fear", _TAGS["fear"], text) if text else None


def _want() -> Optional[Item]:
    from ai import quest_store

    mine = [q for q in quest_store.load_quests().values()
            if getattr(q, "source", "") == "hers" and getattr(q, "status", "") == "alive"]
    if not mine:
        return None
    newest = max(mine, key=lambda q: float(getattr(q, "updated_at", 0.0) or q.born))
    text = _short(newest.text)
    return Item("want", _TAGS["want"], text) if text else None


def _daydream(now: float) -> Optional[Item]:
    from ai import daydream_store

    for entry in daydream_store.read_recent(days_back=RECENT_DAYS, limit=1):
        at = float(getattr(entry, "written_at", 0.0) or 0.0)
        text = _short(getattr(entry, "text", ""))
        if text and now - at < RECENT_DAYS * 86400:
            return Item("daydream", _TAGS["daydream"], text, _age(at, now))
    return None


def _curiosity(now: float) -> Optional[Item]:
    from ai import curiosity_research

    for entry in curiosity_research.recent_research_entries(days_back=RECENT_DAYS, limit=1):
        at = float(getattr(entry, "timestamp", 0.0) or 0.0)
        text = _short(getattr(entry, "thought", ""))
        if text and now - at < RECENT_DAYS * 86400:
            return Item("curiosity", _TAGS["curiosity"], text, _age(at, now))
    return None


def gather_items(*, now: Optional[float] = None) -> list[Item]:
    """Its own life lately, one item per store at most, in tag order. Cheap
    file reads; one that breaks costs only its own line."""
    ts = float(now if now is not None else time.time())
    out: list[Item] = []
    for name, read in (("fear", _fear), ("want", _want),
                       ("daydream", lambda: _daydream(ts)), ("curiosity", lambda: _curiosity(ts))):
        try:
            item = read()
        except Exception as exc:
            logger.debug("own life: the %s read failed (%s); left out", name, type(exc).__name__)
            continue
        if item is not None:
            out.append(item)
    return out


def render_lines(items: Iterable[Item]) -> str:
    return "\n".join(OWN_LIFE_LINES[i.kind].format(text=i.text, age=i.age) for i in items)


_TAG_RE = __import__("re").compile(r"^- \[o\d\] ")


def plain_lines(block: str) -> str:
    """The block for the reply (D9): its lines without the heading and the
    look's tags, "- something I'm afraid of: …"."""
    lines = [ln for ln in str(block or "").splitlines() if ln.startswith("- ")]
    return "\n".join(_TAG_RE.sub("- ", ln) for ln in lines)


def own_life_block(items: Iterable[Item]) -> str:
    """D2's heading and lines, or "" when there is nothing of its own."""
    lines = render_lines(items)
    return f"{OWN_LIFE_HEADING}\n{lines}" if lines else ""


# ── the room check (D8) ──────────────────────────────────────────────────

def _load_cache() -> dict[str, bool]:
    try:
        data = json.loads(CHECK_CACHE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {str(k): v for k, v in data.items() if isinstance(v, bool)} if isinstance(data, dict) else {}


def _save_cache(cache: dict[str, bool]) -> None:
    try:
        CHECK_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = CHECK_CACHE_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(cache, sort_keys=True), encoding="utf-8")
        tmp.replace(CHECK_CACHE_PATH)
    except OSError as exc:
        logger.warning("own life: the room check's record could not be saved (%s)", exc)


def room_safe(items: Iterable[Item]) -> list[Item]:
    """The items the room check already said yes to. Never asks: an item not
    yet checked, or checked no, stays out of the room."""
    cache = _load_cache()
    return [i for i in items if cache.get(i.key) is True]


def _yes(raw: Any, field: str) -> bool:
    """True only for a JSON object (a code fence allowed) whose ``field`` is
    the literal true; anything else is a no."""
    from core.llm_json import strip_markdown_fences

    try:
        answer = json.loads(strip_markdown_fences(raw if isinstance(raw, str) else ""))
    except ValueError:
        return False
    return isinstance(answer, dict) and answer.get(field) is True


async def check_unchecked(items: Iterable[Item], *, llm_call: Optional[LLMCall] = None) -> None:
    """Ask the room check for every item it has not answered yet, and record
    each answer. A call that fails records nothing: the item stays out of the
    room and is asked again next time. Never raises (a cancel goes through)."""
    from ai.prompt_loader import prompt_loader

    call = llm_call or room_check_llm_call
    try:
        pending = [i for i in items if i.key not in _load_cache()]
        template = prompt_loader.load_prompt("own_life_room_check") if pending else ""
    except Exception as exc:   # a fire-and-forget task: nothing may escape it
        logger.warning("own life: the room check could not start (%s)", exc or type(exc).__name__)
        return
    if not pending:
        return
    if not template:
        logger.warning("own life: the room check's prompt is missing; nothing of its own reaches a room")
        return
    for item in pending:
        try:
            raw = await call(template.format(item_kind=_KIND_WORDS[item.kind], item_text=item.text))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("own life: the room check failed for %s (%s); asked again later",
                           item.tag, exc or type(exc).__name__)
            continue
        ok = _yes(raw, "room_ok")
        cache = _load_cache()
        cache[item.key] = ok
        _save_cache(cache)
        logger.info("own life: room check %s for %s", "yes" if ok else "no", item.tag)


_BACKGROUND: set[asyncio.Task] = set()
# Items a background check is asking about right now (Fable's final review,
# 10-13): every owner group turn and opener pass would otherwise start
# another check of the same item while the first is still out.
_IN_FLIGHT: set[str] = set()


def check_in_background(items: Iterable[Item]) -> None:
    """Check the unchecked items off the turn's clock, each by one task at a
    time. A no-op outside a running loop or when nothing is unchecked."""
    cache = _load_cache()
    pending = [i for i in items if i.key not in cache and i.key not in _IN_FLIGHT]
    if not pending:
        return
    try:
        task = asyncio.get_running_loop().create_task(check_unchecked(pending))
    except RuntimeError:
        return
    keys = {i.key for i in pending}
    _IN_FLIGHT.update(keys)
    _BACKGROUND.add(task)

    def _done(t: asyncio.Task) -> None:
        _BACKGROUND.discard(t)
        _IN_FLIGHT.difference_update(keys)

    task.add_done_callback(_done)


async def room_safe_lines(*, now: Optional[float] = None, start_checks: bool = True) -> str:
    """For a group: the D2 lines (no heading) of the items the room check
    passed, or "". Unchecked items are sent to the check in the background."""
    items = gather_items(now=now)
    if start_checks:
        check_in_background(items)
    return render_lines(room_safe(items))


async def room_check_llm_call(prompt: str) -> str:
    """The room check's call: the reply model, low temperature, a small
    budget, on the patient no-think lane the said checks use."""
    from ai.llm_client import llm
    from ai.llm_routing import get_task_model

    return await llm.chat_completion(
        messages=[{"role": "user", "content": prompt}],
        model=get_task_model("response"), temperature=0.1, max_tokens=300,
        log_context="reflect_turn_own_life_room_check",
    )
