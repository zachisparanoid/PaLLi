"""Who's who in a room, and the names a message calls (the room-reading fix;
the owner's design and words, 2026-10-12).

10-11: the owner typed a friend's name in a group ("hey <friend>, you
around?"); the friend is someone there. Only @mentions and replies told her who
a message was for, so the quick vote guessed "a nickname or a slip" and she
answered. The owner's rule: she
reads a room the way anyone there would, and when she can't tell, she stays
out. This module gives her the facts, as one line, and never a decision:

    Names in this message: "palli" (me) · "Alex" (someone in this room, not
    me) · "Jordan" (someone else; not anyone I know here)

Code finds the names; what the message means stays the model's call. The line
reaches the room's first read and the quick vote in the third person ("her")
and her reply call in her own ("me"), in shared rooms with
``companion.she_decides`` on only: a DM is never touched.

Who counts as someone in this room: the people seen talking in the room's
transcript (as they are labeled there), on Discord the people who can see the
channel (its member list; Telegram shows only admins, so it isn't asked), and
the names the owner taught for the room. Her own names: "PaLLi" and "Polly"
everywhere, plus the nicknames the owner taught for the room.

A name nobody here goes by is reported only where a message plainly calls
someone, capitalized ("hey Jordan", "thanks Jordan", "you around, Jordan?"):
anywhere else a capitalized word is too often not a name, and a wrong "someone
else" would keep her out of a message meant for her. An @mention is a name too
(the signals line says the same).

The owner's corrections live in ``data/room_people.json`` (gitignored;
``PALLI_ROOM_PEOPLE_PATH`` overrides it, which is how the test suite stays off
the live file), keyed by conversation key (``core.session_keys``) →
``{"for_me": [...], "people": [...]}``. They are saved only from the owner's
own words, through the owner-only ``room_note`` tool. Writes are atomic (a
temporary file, then a rename); a file that cannot be read is no corrections,
logged and never overwritten.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any, Iterable, Sequence

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[1]
NAME_MAX_CHARS = 40
MAX_TAUGHT = 50                 # per room, per list
MAX_SHOWN = 6                   # names in one line
SEEN_ROWS = 400                 # how far back "seen talking" looks
HER_NAMES = ("palli", "polly")
LINE_START = "Names in this message: "


class RoomPeopleStoreError(RuntimeError):
    """The file exists but cannot be read, so it is not rewritten."""


# ── the owner's corrections ─────────────────────────────────────────────────

def store_path() -> Path:
    raw = os.environ.get("PALLI_ROOM_PEOPLE_PATH")
    if raw:
        return Path(raw)
    return _REPO_ROOT / "data" / "room_people.json"


def _load(*, strict: bool) -> dict[str, dict[str, list[str]]]:
    path = store_path()
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    except OSError as exc:
        if strict:
            raise RoomPeopleStoreError(f"can't read {path.name}: {exc}") from exc
        logger.warning("room_people: could not read %s (%s)", path.name, exc)
        return {}
    try:
        data = json.loads(raw) if raw.strip() else {}
        if not isinstance(data, dict):
            raise ValueError("not an object")
    except ValueError as exc:
        if strict:
            raise RoomPeopleStoreError(f"{path.name} is not readable: {exc}") from exc
        logger.warning("room_people: %s is unreadable; reading it as no corrections", path.name)
        return {}
    out: dict[str, dict[str, list[str]]] = {}
    for key, entry in data.items():
        if not isinstance(entry, dict):
            continue
        out[str(key)] = {
            kind: [n for n in (clean(x) for x in (entry.get(kind) or [])
                               if isinstance(x, str)) if n]
            for kind in ("for_me", "people")
        }
    return out


def _save(rooms: dict[str, dict[str, list[str]]]) -> None:
    path = store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(rooms, indent=1, sort_keys=True, ensure_ascii=False) + "\n",
                   encoding="utf-8")
    os.replace(tmp, path)


def clean(name: Any) -> str:
    """A name as it is kept and shown: as she shows names everywhere
    (``speakers.clean_name``: no symbol or emoji, so "Alex 🌙" is Alex), one
    line, no quotes or brackets, at most NAME_MAX_CHARS; "" when nothing
    name-like is left."""
    from core.agent.speakers import clean_name

    text = " ".join(clean_name(name).replace('"', "'").split())
    text = text.strip(" @#[]():;,.!?")[:NAME_MAX_CHARS].strip()
    return text if any(ch.isalpha() for ch in text) else ""


def _same(a: str, b: str) -> bool:
    return a.casefold() == b.casefold()


def teach(key: str, name: str, *, means_me: bool) -> dict[str, list[str]]:
    """Save one of the owner's corrections for a room: ``name`` means her
    (``means_me``) or someone in this room. Moves the name out of the other
    list. ValueError for no usable name, or for making one of her own names
    someone else's."""
    shown = clean(name)
    if not shown:
        raise ValueError("a name needs at least one letter")
    if not means_me and shown.casefold() in HER_NAMES:
        raise ValueError(f'"{shown}" is always her own name')
    rooms = _load(strict=True)
    entry = rooms.setdefault(str(key), {"for_me": [], "people": []})
    keep, drop = ("for_me", "people") if means_me else ("people", "for_me")
    entry[drop] = [n for n in entry.get(drop, []) if not _same(n, shown)]
    names = [n for n in entry.get(keep, []) if not _same(n, shown)]
    entry[keep] = (names + [shown])[-MAX_TAUGHT:]
    _save(rooms)
    return {k: list(v) for k, v in entry.items()}


def forget(key: str, name: str) -> bool:
    """Drop a name the owner taught for this room. True when there was one."""
    shown = clean(name)
    rooms = _load(strict=True)
    entry = rooms.get(str(key))
    if not shown or entry is None:
        return False
    before = sum(len(v) for v in entry.values())
    for kind in ("for_me", "people"):
        entry[kind] = [n for n in entry.get(kind, []) if not _same(n, shown)]
    if sum(len(v) for v in entry.values()) == before:
        return False
    if not any(entry.values()):
        rooms.pop(str(key))
    _save(rooms)
    return True


def taught(key: str) -> dict[str, list[str]]:
    """The owner's corrections for one room ({} lists when none)."""
    entry = _load(strict=False).get(str(key)) or {}
    return {"for_me": list(entry.get("for_me", [])), "people": list(entry.get("people", []))}


def taught_everywhere() -> dict[str, dict[str, list[str]]]:
    return _load(strict=False)


# ── who is in the room ──────────────────────────────────────────────────────

def _discord_channel(key: str) -> int | None:
    platform, _, raw = str(key).partition(":")
    if platform != "discord":
        return None
    try:
        return int(raw)
    except ValueError:
        return None


# Fable's review (2026-10-12): Discord works out who can see a channel by
# checking every member of the server, so the answer is kept for a minute
# rather than rebuilt for every message (and twice per turn).
CACHE_SECONDS = 60.0
_cache: dict[tuple[str, str, bool], tuple[float, list[str]]] = {}


def reset_for_tests() -> None:
    _cache.clear()


def _kept(kind: str, key: str, handles: bool, build: Any) -> list[str]:
    now = time.monotonic()
    hit = _cache.get((kind, key, handles))
    if hit is not None and now - hit[0] < CACHE_SECONDS:
        return list(hit[1])
    value = build()
    _cache[(kind, key, handles)] = (now, list(value))
    return list(value)


def channel_members(key: str, *, handles: bool = False) -> list[str]:
    """On Discord, the names of the people who can see this channel (display
    names; account names too with ``handles``), bots left out; [] anywhere
    else, or when the list isn't for this one channel. Kept for a minute."""
    return _kept("members", key, handles, lambda: _channel_members(key, handles))


def _channel_members(key: str, handles: bool) -> list[str]:
    channel_id = _discord_channel(key)
    if channel_id is None:
        return []
    try:
        from core.agent import hands_runtime as _hr

        members, scoped = _hr.members_here(channel_id=channel_id)
    except Exception:
        return []
    if not scoped:
        return []
    out: list[str] = []
    for m in members:
        if m.get("bot"):
            continue
        for raw in (m.get("display_name"), m.get("name") if handles else None):
            name = clean(raw)
            if name and not any(_same(name, n) for n in out):
                out.append(name)
    return out


def seen_talking(rows: Sequence[Any]) -> list[str]:
    """The people seen talking lately in the room's transcript (its last
    SEEN_ROWS rows: a long room's whole history would be read every turn),
    as each is labeled there, without the owner's mark. The rows before them
    still say whether the room had records (``label_names(last=)``): a row
    without one after they began is "someone", never the owner."""
    try:
        from core.agent.speakers import OWNER_MARK, label_names

        labels = label_names(list(rows or []), last=SEEN_ROWS)
    except Exception:
        return []
    out: list[str] = []
    for label in labels:
        name = clean(str(label or "").replace(OWNER_MARK, ""))
        if name and not any(_same(name, n) for n in out):
            out.append(name)
    return out


def people_here(key: str, rows: Sequence[Any] = ()) -> list[str]:
    """Everyone she knows of in this room, by the names they show, each once.
    Account names are left out (Fable's review): they are often plain words.
    Her own lines and her bot account are never here, so one of her names in
    this list is someone else who goes by it ("both" in the line)."""
    out: list[str] = []
    for name in [*taught(key)["people"], *seen_talking(rows), *channel_members(key)]:
        if not any(_same(name, n) for n in out):
            out.append(name)
    return out


def _attr(obj: Any, name: str) -> Any:
    try:
        return getattr(obj, name, None)
    except Exception:  # noqa: BLE001 — a Telegram bot not yet started raises
        return None


def account_names(key: str) -> list[str]:
    """Her own names on the app this room is on (Fable's review, 2026-10-12):
    her Discord account's name and display name and her nickname in this
    server, or her Telegram bot's username and name. Only real strings count;
    kept for a minute."""
    return _kept("account", key, False, lambda: _account_names(key))


def _account_names(key: str) -> list[str]:
    platform = str(key).partition(":")[0]
    raw: list[Any] = []
    try:
        from core.agent import hands_runtime as _hr

        if platform == "discord":
            client = _hr.get_client()
            user = _attr(client, "user")
            raw += [_attr(user, a) for a in ("name", "display_name", "global_name")]
            get = _attr(client, "get_channel")
            channel_id = _discord_channel(key)
            channel = get(channel_id) if callable(get) and channel_id else None
            me = _attr(_attr(channel, "guild"), "me")
            raw += [_attr(me, "display_name"), _attr(me, "nick")]
        elif platform == "telegram":
            bot = _hr.get_telegram_bot()
            raw += [_attr(bot, "username"), _attr(bot, "first_name")]
    except Exception:  # noqa: BLE001 — her own names are a help, never a failure
        pass
    out: list[str] = []
    for value in raw:
        name = clean(value) if isinstance(value, str) else ""
        if name and name.casefold() not in HER_NAMES and not any(_same(name, n) for n in out):
            out.append(name)
    return out


def her_names(key: str) -> list[str]:
    """"PaLLi" and "Polly", her own account names on this app, and the
    nicknames the owner taught for this room."""
    names: list[str] = [*HER_NAMES]
    for name in [*account_names(key), *taught(key)["for_me"]]:
        if not any(_same(name, n) for n in names):
            names.append(name)
    return names


# ── the names a message calls ───────────────────────────────────────────────

_URL = re.compile(r"\bhttps?://\S+|\bwww\.\S+", re.IGNORECASE)
_TAG = re.compile(r"^\[[^\]\n]+?\]: ", re.MULTILINE)   # a door's "[Name]: " before a guest's line
_GREETING = (r"(?:hey+|hi+|hello|hiya|heya|howdy|yo+|oi|sup|morning|good morning|evening|"
             r"good evening|night|good night|gm|gn|thanks|thank you|thx|ty|bye|cya|later)")
# Fable's review (2026-10-12): every pattern compiled once, not per name.
_AFTER_GREETING = re.compile(rf"(?:^|\W){_GREETING}[^\w\n]+@?$", re.IGNORECASE)
_LINE_START = re.compile(r"(?:^|\n)[^\w\n]*$")
_COMMA_NEXT = re.compile(r"\s*,")
_CALL_NEXT = re.compile(r"\s*(?:[,?!.…]|$)")
_TAIL_BEFORE = re.compile(r",\s*@?$")
_TO_THE_END = re.compile(r"[^\w]*")      # punctuation, emoji, spaces: nothing else said
_GREETED_WORD = re.compile(rf"(?:^|\W){_GREETING}[^\w\n]+@?(\w[\w'’-]*)", re.IGNORECASE)
_TAIL_WORD = re.compile(r",\s*@?(\w[\w'’-]*)[^\w]*$")
# A word that often follows a greeting or ends a message but names nobody;
# and, since Fable's review, the words that are also people's names or
# handles ("Will you be around later?" with a "Will" in the room): those
# count only where a message plainly calls someone, never by a capital alone.
_NOT_A_NAME = frozenset("""
    you u ya yall y'all all everyone everybody anyone anybody someone somebody guys gals folks
    people peeps team gang fam chat friends friend there here man men dude dudes bro bros bruh
    brother sis sister mate mates buddy bud pal pals boss sir maam ma'am babe baby honey love dear
    girl girls boy boys kid kids champ chief homie homies fellas ladies gents world again back
    then now today tonight tomorrow yet too though right ok okay yes yeah yep yup no nah nope sure
    well so oh ah uh um hmm lol lmao haha wow damn dang please thanks sorry also anyway actually
    honestly seriously basically literally wait look listen alright cool nice great good fine true
    same fr ngl tbh idk imo btw omg ugh what why how when where who which is are am was were do
    does did can could would should will i im i'm me my we us our he she they them it its this
    that these those and but or if just quick real guess maybe think everyone's lads mister miss
    missus son bot bots ai may might must shall max hey hi hello hiya heya howdy yo oi sup thank
    thx ty bye cya gm gn morning evening night later mark bill pat sue ray art rose grace hope
    faith joy dawn june april august summer sky star ace chase frank drew jack page reed rich
    grant gray grey hunter mason sunny lucky happy angel king queen doc coach new old big little
    best first last next top echo nova luna storm shadow ghost fox wolf bear
""".split())


def _plain(text: str) -> str:
    text = _TAG.sub("", str(text or ""))
    return _URL.sub(" ", text)


def _find(name: str, text: str) -> list[re.Match]:
    pattern = re.compile(r"(?<![\w#])" + re.escape(name) + r"(?![\w])", re.IGNORECASE)
    return list(pattern.finditer(text))


def _counts(m: re.Match, text: str, name: str) -> bool:
    """Whether a known name, found at ``m``, is a name here and not a word.

    An @mention, a greeting ("hey Sam"), the start of a line before a comma
    ("Sam, you there?") or the end after a comma ("you around, Sam?") call
    someone. Anywhere else a name counts when it is written like one:
    capitalized, or a handle with a digit or an underscore. A name that is
    also a common word counts only where a message calls someone, and after a
    greeting only when the call ends there ("hey Will," but not "hey will you
    help")."""
    before, end = text[:m.start()], m.end()
    written = m.group(0)
    if before.endswith("@"):
        return True
    greeted = _AFTER_GREETING.search(before) is not None
    tail = _TAIL_BEFORE.search(before) is not None and _TO_THE_END.fullmatch(text, end) is not None
    if name.casefold() in _NOT_A_NAME:
        return tail or (greeted and _CALL_NEXT.match(text, end) is not None)
    if greeted or tail:
        return True
    if _LINE_START.search(before) and _COMMA_NEXT.match(text, end):
        return True
    return written[:1].isupper() or any(ch.isdigit() or ch == "_" for ch in name)


def _hers(word: str) -> bool:
    folded = word.casefold()
    return any(n in folded for n in HER_NAMES)


def _unknown_called(text: str) -> list[tuple[int, str]]:
    """Names nobody here goes by, only where a message plainly calls someone,
    and only capitalized: after a greeting ("hey Jordan") or at the end after
    a comma ("you around, Jordan?"). Lower case there is too often a word
    ("yo wtf!"), and the model reads the message either way. A word holding
    one of her names ("PaLLiBot") is never someone else's."""
    found: list[tuple[int, str]] = []
    for m in [*_GREETED_WORD.finditer(text), *_TAIL_WORD.finditer(text)]:
        word = m.group(1)
        if (len(word) >= 2 and word[:1].isalpha() and word[:1].isupper()
                and word.casefold() not in _NOT_A_NAME and not _hers(word)):
            found.append((m.start(1), word))
    return found


def names_in(text: str, *, mine: Iterable[str], here: Iterable[str]) -> list[tuple[str, str]]:
    """The names ``text`` calls or mentions, in the order they appear, each
    once, as (name as written, whose): "me", "here", "both" (one of her names
    that someone here also goes by) or "unknown"."""
    plain = _plain(text)
    folded = plain.casefold()
    mine = [n for n in mine if n]
    here = [n for n in here if n]
    hits: list[tuple[int, str, str]] = []
    taken: list[tuple[int, int]] = []

    def free(start: int, end: int) -> bool:
        return all(end <= s or start >= e for s, e in taken)

    # Only the names the text holds at all (Fable's review: a server's whole
    # member list, tried name by name, stalled every turn); longer names
    # first, so "Alex Smith" is found before "Alex".
    present = {n for n in (*mine, *here) if n.casefold() in folded}
    for name in sorted(present, key=len, reverse=True):
        is_mine = any(_same(name, n) for n in mine)
        is_here = any(_same(name, n) for n in here)
        whose = "both" if is_mine and is_here else ("me" if is_mine else "here")
        always = whose != "here" and name.casefold() in HER_NAMES
        for m in _find(name, plain):
            if not free(m.start(), m.end()):
                continue
            if not always and not _counts(m, plain, name):
                continue
            hits.append((m.start(), m.group(0), whose))
            taken.append((m.start(), m.end()))
    for start, word in _unknown_called(plain):
        if free(start, start + len(word)):
            hits.append((start, word, "unknown"))
            taken.append((start, start + len(word)))
    out: list[tuple[str, str]] = []
    for _, written, whose in sorted(hits):
        if not any(_same(written, w) for w, _ in out):
            out.append((written, whose))
    return out[:MAX_SHOWN]


_LABELS = {
    "me": {"me": "me", "here": "someone in this room, not me",
           "both": "me, or someone in this room by the same name",
           "unknown": "someone else; not anyone I know here"},
    "her": {"me": "her", "here": "someone in this room, not her",
            "both": "her, or someone in this room by the same name",
            "unknown": "someone else; not anyone she knows here"},
}


def render(found: Sequence[tuple[str, str]], *, voice: str) -> str:
    """The one line: her own ("me") for her reply call, "her" for the quick
    reads; "Names in this message: none" when no name is called."""
    labels = _LABELS["her" if voice == "her" else "me"]
    if not found:
        return LINE_START + "none"
    return LINE_START + " · ".join(f'"{clean(w) or w}" ({labels[whose]})' for w, whose in found)


def names_line(text: str, *, key: str, rows: Sequence[Any] = (), voice: str = "me") -> str:
    """The line for one message (or a burst read as one) in one room. Never
    raises: a line that can't be built is "", and the turn goes on without."""
    try:
        return render(names_in(text, mine=her_names(key), here=people_here(key, rows)),
                      voice=voice)
    except Exception as exc:  # noqa: BLE001 — a fact line is never worth the turn
        logger.warning("room_people: could not read the names in a message (%s)",
                       type(exc).__name__)
        return ""
