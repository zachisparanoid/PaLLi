"""Memory that reaches: search with scope (R15, T4; plan
docs/2026-10-13-memory-that-reaches-plan.md).

The read side of the record (``ai/memory_reach_index.py``): lines found by
their words (FTS5, bm25) and by their meaning (cosine over the record's
float32 vectors), inside a room scope that comes from the turn, never from
the model. No model is asked anything here. The one model touched is the
local embedder, for the query's own vector, and only when the record's
catch-up would use it (``memory_reach_index.embedder_for``: the owner's
setting and the back-off, one rule for both).

The scope. ``Scope(None)`` is every room, for a turn proven to be the
owner's; ``Scope(key)`` is that one room. One clause (``_scope_sql``)
reaches every leg: the words, the meaning, the lines around a hit
(``neighbours``) and the lines after it (``thread``). A turn that can't be
proven the owner's is scoped to its room; a non-owner turn with no room is
scoped to ``NO_ROOM``, which no line is in.

The score is absolute, never a share of the batch's best, so "thin" can
mean thin (ruled 10-13 before review: the design widens "when the first
search finds nothing strong", and a batch's best is always strong). Words:
the share of the query's content words (stopwords out,
``ai.fact_retrieval.STOPWORDS``) the line holds, each word matched by the
index itself, porter stems and all.
Meaning: the cosine. Blend ``WORDS_WEIGHT`` words + ``MEANING_WEIGHT``
meaning; a line with no vector (or a search with no meaning) scores its
words alone. A line holding the exact phrase scores 1.0. The phrase is the
query's quoted part(s), or with ``exact`` the whole query when nothing is
quoted; ``exact`` returns those lines only, by words alone. A line with no
content word and a cosine under ``MEANING_FLOOR`` is dropped, so a search
never fills its limit with unrelated lines. bm25 only orders lines whose
scores tie.

Reading only. The record is opened read-only, so a read can never create
or change it; nothing here takes the record's lock (reads under WAL are
fine) and nothing catches up: the callers decide when to.

A guest's turn (W2). A guest's turn has no tools, so the runner takes one
step before the reply: ``room_section_for`` catches the record up and
searches that turn's own room (``scope_for_turn``: never a transcript key,
never everything), keeps the strong hits older than what the reply's
history already shows, and renders them for the guest's prompt with the
owner by the room's own name and mark. ``with_room_memory`` puts them on the reply's
context. That step is the one place in this module that catches the record
up: it is its own caller.
"""
from __future__ import annotations

import asyncio
import heapq
import logging
import re
import sqlite3
import time
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import numpy as np

from ai import memory_reach_index as mri
from ai.fact_retrieval import STOPWORDS
from core.agent.message_times import stamp_label
from core.agent.speakers import OWNER_MARK, clean_name, defuse
from core.owner_gate import is_guest_turn
from core.session_keys import session_key_for

logger = logging.getLogger(__name__)

# The blend and the floors: T6 tunes them against the real question set.
WORDS_WEIGHT = 0.55
MEANING_WEIGHT = 0.45
STRONG = 0.45            # is_thin: a hit at or above this is not weak
MAX_LIMIT = 50           # the most lines one search returns, whatever is asked
# A line found by meaning alone needs at least this cosine. MiniLM-L6 (the
# record's model, unit vectors) puts most unrelated sentence pairs between
# about 0 and 0.2 and lines on the same subject from about 0.3 up, so 0.3
# keeps a near paraphrase and drops the noise every line has.
MEANING_FLOOR = 0.30
NO_ROOM = "<no room>"    # a room key no line has: a non-owner turn with no room
_CANDIDATES = 200        # lines each leg brings to the blend, at least
_QUERY_EMBED_S = 5.0     # the most the query's own vector may take
_MAX_TOKENS = 12         # content words read from a query (ai/inner_memory_index.py's cap)
_MAX_PHRASES = 4         # quoted phrases read from one query

# The origins a turn on the owner's own path carries (core.turn.types). A
# guest's is "guest"; anything else can't be read as the owner's. The last
# three are turns she makes herself: no one else's account is in them.
_OWNER_ORIGINS = frozenset({"user", "dashboard_user", "monologue", "reminder", "workbench"})
_HER_OWN_ORIGINS = frozenset({"monologue", "reminder", "workbench"})
# Letters and digits of any script, an inner apostrophe kept ("don't"); the
# underscore splits, as it does for the index's unicode61 tokenizer.
_WORD = re.compile(r"[^\W_]+(?:'[^\W_]+)*")
_QUOTED = re.compile(r'["“”]([^"“”]+)["“”]')
_COLUMNS = "r.id, r.room, r.ts, r.day, r.speaker, r.name, r.text"


@dataclass(frozen=True)
class Scope:
    """Which lines a search may reach: ``None`` is every room (the owner's
    turn only); a room key (``discord:123``) is that room alone."""

    room: str | None

    def __post_init__(self) -> None:
        if self.room is not None and not isinstance(self.room, str):
            raise TypeError(f"a scope's room is a room key or None, not {self.room!r}")

    def reaches(self, room: str) -> bool:
        """Whether a line of ``room`` is inside this scope, for lines already
        in hand: ``_scope_sql``'s rule (every room for None, else that one
        room), so the two never differ."""
        return self.room is None or room == self.room


@dataclass(frozen=True)
class Hit:
    """One line of the record. ``words``: the share of the query's content
    words the line holds (0..1). ``meaning``: the cosine (0..1; 0 with no
    vector). ``score``: their blend, the words alone when the line has no
    meaning, or 1.0 for an exact hit. A line read around a hit
    (``neighbours``, ``thread``) scores 0."""

    id: int
    room: str
    ts: float
    day: str
    speaker: str
    name: str
    text: str
    score: float
    words: float
    meaning: float
    exact: bool


class Hits(list):
    """What ``search`` gives back: a plain list of ``Hit``, best first, plus
    one fact about how it was found. ``words_only``: the search ran without
    its meaning leg (an exact-phrase search, or no meaning: the owner's
    setting, the embedder backing off, a failed or over-time embed). False
    when meaning ran, and when the search could not tell (no record, no
    query). Callers that only read the list never see the difference."""

    words_only: bool = False


def _found(hits: Iterable[Hit], *, words_only: bool) -> Hits:
    found = Hits(hits)
    found.words_only = words_only
    return found


# --- the scope ---------------------------------------------------------------

def _scope_sql(scope: Scope) -> tuple[str, tuple]:
    """The one scope clause every leg uses, on the record aliased ``r``
    (``Scope.reaches`` is the same rule, for lines in hand)."""
    if not isinstance(scope, Scope):
        raise TypeError(f"a search needs a Scope, not {scope!r}")
    if scope.room is None:
        return "1", ()
    return "r.room = ?", (scope.room,)


def _proven_owner(turn: Any) -> bool:
    if is_guest_turn(turn):
        return False
    md = getattr(turn, "metadata", None)
    origin = getattr(md, "origin", None) if md is not None else None
    if not isinstance(origin, str) or origin not in _OWNER_ORIGINS:
        return False
    speaker = getattr(md, "speaker", None)
    if speaker is not None:
        return isinstance(speaker, dict) and speaker.get("kind") == "owner"
    # No speaker on record. Off the chat doors, or on a turn she made
    # herself, the origin is proof enough; in a room, where guests speak
    # too, only the account's own record proves the owner.
    return origin in _HER_OWN_ORIGINS or _room_of(turn) == NO_ROOM


def _room_of(turn: Any) -> str:
    """The turn's room key, or ``NO_ROOM`` when the turn is not in a room:
    no surface, no channel, or a surface whose key is not the channel's."""
    md = getattr(turn, "metadata", None)
    surface = getattr(turn, "surface", None)
    channel_id = getattr(md, "channel_id", None) if md is not None else None
    owner_id = getattr(turn, "owner_id", None)
    if not isinstance(surface, str) or not surface or isinstance(channel_id, bool):
        return NO_ROOM
    try:
        if int(channel_id) == 0:
            return NO_ROOM
    except (TypeError, ValueError):
        return NO_ROOM
    key = session_key_for(surface, channel_id, owner_id)
    # A surface keyed on the owner, not on the channel, has no room.
    if key == session_key_for(surface, None, owner_id):
        return NO_ROOM
    return key


def scope_for_turn(turn: Any) -> Scope:
    """Everything for a turn proven to be the owner's; its room for any
    other (a guest's, or one whose origin can't be read); ``NO_ROOM`` for a
    non-owner turn with no room. Never ``Scope(None)`` by default."""
    if _proven_owner(turn):
        return Scope(None)
    return Scope(_room_of(turn))


# --- the query -----------------------------------------------------------------

def _tokens(text: str) -> list[str]:
    """Every word, in order, lowercased: what a phrase is made of."""
    return _WORD.findall(str(text or "").lower())


def _terms(text: str) -> list[str]:
    """The query's content words: up to 12 distinct words, stopwords and
    one-letter words out (digits kept), a possessive's 's dropped ("Dana's"
    looks for "Dana", which the index also finds in "Dana's")."""
    picked: list[str] = []
    for token in _tokens(text):
        if token.endswith("'s"):
            token = token[:-2]
        if (not token or token in picked or token in STOPWORDS
                or (len(token) < 2 and not token.isdigit())):
            continue
        picked.append(token)
        if len(picked) >= _MAX_TOKENS:
            break
    return picked


def _fts_query(text: str) -> str:
    """An OR of the content words (``ai/inner_memory_index.py``'s
    ``_fts_query``, with stopwords out and any script's letters)."""
    return " OR ".join(f'"{t}"' for t in _terms(text))


def _phrase_query(text: str, *, whole: bool) -> str:
    """The exact phrase(s): each quoted part of the query (all of them must
    be there), or the whole query with ``whole`` when nothing is quoted.
    "" when there is none."""
    parts = _QUOTED.findall(text)[:_MAX_PHRASES]
    if not parts and whole:
        parts = [text]
    phrases = [" ".join(t) for t in (_tokens(p) for p in parts) if t]
    return " AND ".join(f'"{p}"' for p in phrases)


def _day_bound(value: Any, label: str) -> str | None:
    """A local day, ``YYYY-MM-DD``, from a date or its text; None for none."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if isinstance(value, datetime):
        return value.astimezone().date().isoformat()     # the day it is here
    if isinstance(value, date):
        return value.isoformat()
    try:
        return date.fromisoformat(str(value).strip()).isoformat()
    except ValueError:
        raise ValueError(f"{label} must be a day, YYYY-MM-DD: {value!r}") from None


# Letters that show as nothing: the Hangul fillers and the blank braille cell.
_FILLERS = frozenset("\u3164\u115f\u1160\u2800")


def _shown_name(name: Any) -> str:
    """A guest's name as it is read: compatibility forms folded (NFKC:
    fullwidth and enclosed letters become plain ones), every control,
    format, private-use and combining character and every filler letter
    dropped (so "th\u200bem" is "them"), then ``core.agent.speakers.clean_name``
    (one line, no brackets, colons or symbols). Look-alike letters from
    another script ("m" + Cyrillic "е") are left as they are: the name is
    shown as it is written; only the them/me check folds them
    (``_reads_as``)."""
    text = unicodedata.normalize("NFKC", str(name or ""))
    text = "".join(" " if ch.isspace() else ch for ch in text
                   if ch.isspace() or not (ch in _FILLERS
                                           or unicodedata.category(ch)[0] in "CM"))
    return clean_name(text)


# M6 (Fable's whole-branch review; ledger T4): the Cyrillic and Greek letters
# that look like Latin ones, by their Unicode names, each with the Latin
# letter its capital and its small form read as (no second letter where the
# small one looks like no Latin letter). Folded for the them/me check only,
# so "th" + Cyrillic "е" + "m" reads as "them" there; over-folding only adds
# "(guest)".
_LOOK_ALIKE_NAMES = {
    "CYRILLIC": {"A": "Aa", "VE": "Bb", "IE": "Ee", "KA": "Kk", "EM": "Mm", "EN": "Hh",
                 "O": "Oo", "ER": "Pp", "ES": "Cc", "TE": "Tt", "U": "Yy", "HA": "Xx",
                 "DZE": "Ss", "BYELORUSSIAN-UKRAINIAN I": "Ii", "JE": "Jj", "SHHA": "Hh",
                 "KOMI DE": "Dd", "QA": "Qq", "WE": "Ww"},
    "GREEK": {"ALPHA": "Aa", "BETA": "Bb", "EPSILON": "Ee", "ZETA": "Z", "ETA": "Hn",
              "IOTA": "Ii", "KAPPA": "Kk", "MU": "Mm", "NU": "Nv", "OMICRON": "Oo",
              "RHO": "Pp", "TAU": "Tt", "UPSILON": "Yu", "CHI": "Xx"},
}
_LOOK_ALIKES = {
    ord(unicodedata.lookup(f"{script} {case} LETTER {name}")): latin
    for script, letters in _LOOK_ALIKE_NAMES.items()
    for name, reads in letters.items()
    for case, latin in zip(("CAPITAL", "SMALL"), reads)
}


def _reads_as(shown: str) -> str:
    """A shown name as the them/me check reads it: each look-alike letter of
    another script as the Latin letter it looks like (M6)."""
    return shown.translate(_LOOK_ALIKES)


def _name_is(name: Any, wanted: Any) -> int:
    """A guest's name against the one asked for, both as they are read
    (``_shown_name``), casefolded: the whole name or one word of it."""
    have = _shown_name(name).casefold()
    return int(bool(have) and (have == wanted or wanted in have.split()))


def _who_sql(who: Any) -> tuple[str, tuple] | None:
    wanted = _shown_name(who).casefold()
    if wanted in ("", "anyone"):
        return None
    if wanted == "them":
        return "r.speaker = 'owner'", ()
    if wanted == "me":
        return "r.speaker = 'palli'", ()
    return "r.speaker = 'guest' AND reach_name_is(r.name, ?)", (wanted,)


def _where(scope: Scope, since: str | None, until: str | None, who: Any) -> tuple[str, tuple]:
    clause, params = _scope_sql(scope)
    parts, args = [clause], list(params)
    if since:
        parts.append("r.day >= ?")
        args.append(since)
    if until:
        parts.append("r.day <= ?")
        args.append(until)
    by = _who_sql(who)
    if by is not None:
        parts.append(by[0])
        args.extend(by[1])
    return " AND ".join(parts), tuple(args)


# --- the record, read --------------------------------------------------------

def open_record(path: Path | None = None) -> sqlite3.Connection | None:
    """The record, read-only (``record_path()`` unless a path is given), or
    None when there is no record: no file, or a file with no record in it.
    Read-only, a read can never create the file or change it. The caller
    closes it, in the thread that opened it."""
    target = Path(path) if path is not None else mri.record_path()
    if not target.is_file():
        return None
    try:
        conn = sqlite3.connect(f"{target.resolve().as_uri()}?mode=ro", uri=True)
    except sqlite3.OperationalError:
        return None                           # gone between the look and the open
    try:
        conn.execute("PRAGMA busy_timeout=5000")
        if conn.execute("SELECT 1 FROM sqlite_master"
                        " WHERE type = 'table' AND name = 'record'").fetchone() is None:
            conn.close()
            return None
        conn.create_function("reach_name_is", 2, _name_is, deterministic=True)
    except BaseException:
        conn.close()
        raise
    return conn


def _hit(rec: tuple, *, score: float = 0.0, words: float = 0.0, meaning: float = 0.0,
         exact: bool = False) -> Hit:
    rid, room, ts, day, speaker, name, text = rec
    return Hit(id=int(rid), room=str(room), ts=float(ts), day=str(day), speaker=str(speaker),
               name=str(name or ""), text=str(text or ""), score=float(score),
               words=float(words), meaning=float(meaning), exact=bool(exact))


_FTS_FROM = "FROM record_fts JOIN record r ON r.id = record_fts.rowid"


def _word_strengths(conn: sqlite3.Connection, match: str, where: str, args: tuple,
                    cap: int | None = None) -> dict[int, float]:
    """id -> bm25 strength (bigger is stronger) of the lines ``match``
    finds, the strongest ``cap`` (None: all). bm25 only orders lines; it
    never makes a score."""
    if not match:
        return {}
    rows = conn.execute(
        f"SELECT r.id, bm25(record_fts) {_FTS_FROM}"
        f" WHERE record_fts MATCH ? AND {where} ORDER BY bm25(record_fts) LIMIT ?",
        (match, *args, -1 if cap is None else cap)).fetchall()
    return {int(rid): abs(float(b or 0.0)) for rid, b in rows}


def _coverage(conn: sqlite3.Connection, terms: list[str], where: str,
              args: tuple) -> dict[int, float]:
    """id -> the share of ``terms`` the line holds, for every line in reach
    holding at least one. Each term is matched by the index itself, so a
    term counts the way the index stems it ("boats" finds "boat")."""
    if not terms:
        return {}
    counts: dict[int, int] = {}
    sql = f"SELECT r.id {_FTS_FROM} WHERE record_fts MATCH ? AND {where}"
    for term in terms:
        for (rid,) in conn.execute(sql, (f'"{term}"', *args)):
            counts[int(rid)] = counts.get(int(rid), 0) + 1
    return {rid: n / len(terms) for rid, n in counts.items()}


def _meanings(conn: sqlite3.Connection, query: np.ndarray, where: str,
              args: tuple) -> dict[int, float]:
    """id -> cosine (0..1) for every line in reach with a vector as wide as
    the query's (a stored zero vector is an entry of 0); a line with none,
    or another width, has no entry. A query with no direction (a zero or
    non-finite norm) gives {}: every line then scores its words alone."""
    width = int(query.shape[0])
    rows = conn.execute(
        f"SELECT v.id, v.vec FROM record_vec v JOIN record r ON r.id = v.id WHERE {where}",
        args).fetchall()
    usable = [(rid, blob) for rid, blob in rows
              if isinstance(blob, bytes) and len(blob) == 4 * width]
    q_norm = float(np.linalg.norm(query))
    if not usable or not np.isfinite(q_norm) or q_norm == 0.0:
        return {}
    matrix = np.frombuffer(b"".join(blob for _, blob in usable),
                           dtype=np.float32).reshape(len(usable), width)
    norms = np.linalg.norm(matrix, axis=1) * q_norm
    with np.errstate(invalid="ignore", divide="ignore"):
        cos = np.where(norms > 0.0, (matrix @ query) / np.where(norms > 0.0, norms, 1.0), 0.0)
    cos = np.clip(np.nan_to_num(cos, nan=0.0, posinf=0.0, neginf=0.0), 0.0, 1.0)
    return {int(rid): float(c) for (rid, _), c in zip(usable, cos.tolist())}


def _rows(conn: sqlite3.Connection, ids: Iterable[int], scope: Scope) -> dict[int, tuple]:
    wanted = sorted(set(ids))
    if not wanted:
        return {}
    clause, params = _scope_sql(scope)
    marks = ",".join("?" * len(wanted))
    return {int(rec[0]): rec for rec in conn.execute(
        f"SELECT {_COLUMNS} FROM record r WHERE r.id IN ({marks}) AND {clause}",
        (*wanted, *params))}


def _search_at(target: Path, *, scope: Scope, terms: list[str], words_q: str,
               phrase_q: str, exact: bool, where: str, args: tuple,
               query: np.ndarray | None, limit: int) -> list[Hit]:
    """The whole search on one connection, opened and closed in this thread."""
    conn = open_record(target)
    if conn is None:
        return []
    cap = max(_CANDIDATES, limit)
    try:
        coverage = _coverage(conn, terms, where, args)
        if exact:
            strength = _word_strengths(conn, phrase_q, where, args, cap)
            exact_ids = set(strength)
            found: list[int] = []
        else:
            strength = _word_strengths(conn, words_q, where, args)
            exact_ids = set(_word_strengths(conn, phrase_q, where, args, cap))
            found = heapq.nlargest(cap, coverage,
                                   key=lambda rid: (coverage[rid], strength.get(rid, 0.0)))
        meaning = _meanings(conn, query, where, args) if query is not None else {}
        # Lines come in by a content word, the exact phrase, or a cosine at
        # the floor or above; nothing else, so no limit is filled with noise.
        near = heapq.nlargest(cap, (rid for rid, c in meaning.items() if c >= MEANING_FLOOR),
                              key=meaning.__getitem__)
        rows = _rows(conn, [*found, *exact_ids, *near], scope)
    finally:
        conn.close()
    ranked = []
    for rid, rec in rows.items():
        w, is_exact = coverage.get(rid, 0.0), rid in exact_ids
        if rid in meaning:
            m = meaning[rid]
            blend = WORDS_WEIGHT * w + MEANING_WEIGHT * m
        else:
            m, blend = 0.0, w                 # no meaning for this line: its words alone
        hit = _hit(rec, score=1.0 if is_exact else blend, words=w, meaning=m, exact=is_exact)
        ranked.append(((hit.score, is_exact, strength.get(rid, 0.0), m, hit.ts, hit.id), hit))
    ranked.sort(key=lambda pair: pair[0], reverse=True)
    return [hit for _, hit in ranked[:limit]]


# Query embeds still running after their search stopped waiting (a cold
# model load), kept here so none is collected mid-run.
_EMBEDDING: set[asyncio.Future] = set()


def _settle(task: asyncio.Future) -> None:
    """A query embed ended: let go of it, and a failure (seen by its search
    or after it) pauses embedding, as it does for the catch-up."""
    _EMBEDDING.discard(task)
    if not task.cancelled() and task.exception() is not None:
        mri.pause_embedding()
        logger.debug("memory reach: a query embed failed (%s)", type(task.exception()).__name__)


async def _query_vector(text: str, embed: mri.Embed | None) -> np.ndarray | None:
    """The query's meaning, or None for words only: no embedder (the
    owner's setting), the embedder backing off, or a failed embed. A failed
    embed starts the back-off, as it does for the catch-up; a slow one
    (over ``_QUERY_EMBED_S``) just costs this search its meaning, and is
    shielded, so a cold model load goes on for the next search."""
    embedder = mri.embedder_for(embed)
    if embedder is None:
        return None
    try:
        task = asyncio.ensure_future(embedder([text]))
        _EMBEDDING.add(task)
        task.add_done_callback(_settle)
        vectors = await asyncio.wait_for(asyncio.shield(task), timeout=_QUERY_EMBED_S)
        if len(vectors) != 1:
            raise ValueError(f"the embedder gave {len(vectors)} vectors for 1 query")
        vec = np.asarray(vectors[0], dtype=np.float32)
        if vec.ndim != 1 or vec.size == 0 or not bool(np.all(np.isfinite(vec))):
            raise ValueError("the embedder gave no usable vector")
    except TimeoutError:
        logger.warning("memory reach: the query's meaning took over %.0f s; this search "
                       "is by words only", _QUERY_EMBED_S)
        return None
    except Exception as exc:
        paused = mri.pause_embedding()
        logger.warning("memory reach: the query's meaning failed (%s); words only, and "
                       "embedding pauses for %.0f minutes", type(exc).__name__, paused / 60)
        return None
    return vec


async def search(query: str, *, scope: Scope, exact: bool = False, since: Any = None,
                 until: Any = None, who: Any = None, limit: int = 12,
                 embed: mri.Embed | None = None, path: Path | None = None) -> Hits:
    """The record's lines for ``query`` inside ``scope``, best first, at
    most ``limit``. ``since``/``until``: local days, inclusive (a date or
    ``YYYY-MM-DD``; anything else is a ``ValueError``). ``who``: "them" (the
    owner), "me" (PaLLi), "anyone" or None, else a guest's name. ``exact``:
    only the lines holding the phrase. No catch-up, no lock, no model call;
    a missing record gives [] and is not created. At most ``MAX_LIMIT``
    lines; ``NO_ROOM`` gives [] before anything is read or embedded. The
    list says whether meaning ran (``Hits.words_only``)."""
    where, args = _where(scope, _day_bound(since, "since"), _day_bound(until, "until"), who)
    text = str(query or "").strip()
    limit = min(int(limit), MAX_LIMIT)
    if not text or limit <= 0 or scope.room == NO_ROOM:
        return []
    target = Path(path) if path is not None else mri.record_path()
    if not target.is_file():
        return _found([], words_only=exact)
    terms = _terms(text)
    phrase_q = _phrase_query(text, whole=exact)
    if exact and not phrase_q:
        return _found([], words_only=True)
    vector = None if exact else await _query_vector(text, embed)
    hits = await asyncio.to_thread(
        _search_at, target, scope=scope, terms=terms, words_q=_fts_query(text),
        phrase_q=phrase_q, exact=exact, where=where, args=args, query=vector, limit=limit)
    return _found(hits, words_only=vector is None)


# --- around a hit ------------------------------------------------------------

def _around(conn: sqlite3.Connection | None, hit_id: int, scope: Scope, before: int,
            after: int) -> list[Hit]:
    """The hit and the lines just before and after it in its own room, in
    order, all inside ``scope``; [] when the hit is not."""
    if conn is None:
        return []
    clause, params = _scope_sql(scope)
    anchor = conn.execute(f"SELECT {_COLUMNS} FROM record r WHERE r.id = ? AND {clause}",
                          (int(hit_id), *params)).fetchone()
    if anchor is None:
        return []
    rid, room, ts = anchor[0], anchor[1], anchor[2]
    earlier = conn.execute(
        f"SELECT {_COLUMNS} FROM record r WHERE r.room = ? AND {clause}"
        " AND (r.ts < ? OR (r.ts = ? AND r.id < ?)) ORDER BY r.ts DESC, r.id DESC LIMIT ?",
        (room, *params, ts, ts, rid, max(0, int(before)))).fetchall()
    later = conn.execute(
        f"SELECT {_COLUMNS} FROM record r WHERE r.room = ? AND {clause}"
        " AND (r.ts > ? OR (r.ts = ? AND r.id > ?)) ORDER BY r.ts, r.id LIMIT ?",
        (room, *params, ts, ts, rid, max(0, int(after)))).fetchall()
    return [_hit(rec) for rec in (*reversed(earlier), anchor, *later)]


def neighbours(conn: sqlite3.Connection | None, hit_id: int, *, scope: Scope,
               before: int = 6, after: int = 6) -> list[Hit]:
    """The lines around a hit in its room (``before`` earlier, the hit,
    ``after`` later), oldest first. ``conn`` from ``open_record``; None (no
    record) gives []."""
    return _around(conn, hit_id, scope, before, after)


def thread(conn: sqlite3.Connection | None, hit_id: int, *, scope: Scope,
           after: int = 12) -> list[Hit]:
    """The hit and the ``after`` lines that followed it in its room."""
    return _around(conn, hit_id, scope, 0, after)


# --- reading the hits --------------------------------------------------------

def is_thin(hits: list[Hit]) -> bool:
    """Zero or weak hits, a resource fact: no exact hit, and either fewer
    than two hits or none scoring at least ``STRONG``."""
    if any(h.exact for h in hits):
        return False
    return len(hits) < 2 or not any(h.score >= STRONG for h in hits)


def room_label(key: str) -> str:
    """A room key as words: "the Discord chat 123", "the Telegram group
    -100…" (a chat with a negative id), "the phone app" (``mobile:1``, his
    private door), "the dashboard", "the Discord voice channel 123" (what
    was said aloud there, its own room)."""
    platform, _, rest = str(key or "").partition(":")
    if platform == "discord":
        return f"the Discord chat {rest}"
    if platform == "discord_voice":
        return f"the Discord voice channel {rest}"
    if platform == "telegram":
        return f"the Telegram {'group' if rest.startswith('-') else 'chat'} {rest}"
    if platform == "mobile":
        return "the phone app"
    if platform == "dashboard":
        return "the dashboard"
    return f"the {platform.replace('_', ' ')} chat {rest}".rstrip()


# The guest labels, in the room's own words (Fable's ruling W2, the
# whole-branch review): a guest whose name, as it is read (``_shown_name``,
# look-alike letters folded by ``_reads_as``), would read as the owner
# ("them"), as PaLLi ("me") or as a line id ("#…") is "{name} (guest)", the
# room's form (``core.agent.speakers._guest_name``); one who shows no letter
# or digit at all is "someone", the room's word for a sender with no usable
# name (``speakers._label_line``).
_NO_NAME = "someone"
_FIRST_WORD = re.compile(r"\W*([^\W_]+)")
_OWNERISH = re.compile(r"\bowner\b", re.IGNORECASE)


def _speaker_label(hit: Hit, *, as_room: bool = False, owner_name: str = "") -> str:
    if hit.speaker == "owner":
        if as_room:
            # The name the room's own labels show for the owner on this
            # reply (W1), never the record's: for a database line that is
            # the account handle. No name shown: the mark alone.
            name = clean_name(owner_name)
            return f"{name} {OWNER_MARK}" if name else OWNER_MARK
        return "them"
    if hit.speaker == "palli":
        return "me"
    shown = _shown_name(hit.name)
    if not any(ch.isalnum() for ch in shown):
        return _NO_NAME
    first = _FIRST_WORD.match(_reads_as(shown))
    if shown.startswith("#") or (first is not None
                                 and first.group(1).casefold() in ("them", "me")) or (
            as_room and _OWNERISH.search(shown)):
        return f"{shown} (guest)"
    return shown


def render_lines(hits: list[Hit], *, now: datetime | float | None = None,
                 ids: bool = False, as_room: bool = False, owner_name: str = "") -> str:
    """The hits as dated lines, "[Oct 5 19:02] them: …" ("me" for PaLLi, a
    guest by their name), grouped under "In {room_label}:" in the order the
    rooms first appear, oldest first within a room, each line once. With
    ``ids`` each line starts "#<id> ", for a look that names lines back.
    A guest's words are defused as the room's are, in every view
    (``core.agent.speakers.defuse``; Fable's whole-branch review, I1): the
    record carries a shared room's lines into the owner's own result too.
    With ``as_room`` (a guest's section, W2) the owner reads as the room
    shows them, by the name the room's labels show on this reply
    (``owner_name``) and the room's mark ("Robin ★owner"), or the mark alone
    when the room shows no name (W1): in a guest's prompt "them" would read
    as the guest; a guest whose name reads "owner" is then marked a guest."""
    if isinstance(now, (int, float)):
        now = datetime.fromtimestamp(now, tz=timezone.utc)
    rooms: dict[str, dict[int, Hit]] = {}
    for hit in hits:
        rooms.setdefault(hit.room, {}).setdefault(hit.id, hit)
    blocks = []
    for room, members in rooms.items():
        lines = [f"In {room_label(room)}:"]
        for hit in sorted(members.values(), key=lambda h: (h.ts, h.id)):
            when = stamp_label(datetime.fromtimestamp(hit.ts, tz=timezone.utc), now=now)
            tag = f"#{hit.id} " if ids else ""
            # In every view a guest's words go through the room's own rule
            # (core.agent.speakers.defuse), so a line a guest typed can never
            # carry the owner's mark (W2, fix round 1; I1, the whole-branch
            # review). The owner's and PaLLi's own lines are as they were said.
            words = defuse(hit.text) if hit.speaker == "guest" else hit.text
            label = _speaker_label(hit, as_room=as_room, owner_name=owner_name)
            lines.append(f"{tag}{when} {label}: {' '.join(words.split())}")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


# --- a guest's own room, before the reply (W2) --------------------------------

ROOM_FLOOR = 0.35            # a guest's section keeps a hit scoring at least this (Hit.score)
ROOM_LIMIT = 6               # the lines the step's search asks for
ROOM_BUDGET_S = 1.5          # the whole step: the catch-up and the search
_ROOM_CATCH_UP_SHARE = 0.6   # of the budget, the most the step waits for the catch-up
# The door's "[Name]: " on a guest's line (core.owner_gate's pattern).
_DOOR_TAG = re.compile(r"^\[[^\n]+?\]: ")
_ROOM_HEADING = re.compile(r"^In .+:$")

# Catch-ups a guest's step stopped waiting for, kept here until they finish
# (each holds the record's lock; cancelling one mid-write is never done).
_CATCHING: set[asyncio.Future] = set()


def _caught(task: asyncio.Future) -> None:
    _CATCHING.discard(task)
    if not task.cancelled():
        task.exception()            # retrieved: the catch-up logs its own failures


def _their_words(text: Any) -> str:
    """A guest's words as the step searches them: each line without the
    door's "[Name]: " tag (their own name is not what they asked about),
    on one line."""
    lines = (_DOOR_TAG.sub("", line, count=1) for line in str(text or "").splitlines())
    return " ".join(" ".join(lines).split())


async def room_section_for(turn: Any, *, text: str, shown_since_ts: float | None,
                           embed: mri.Embed | None = None, path: Path | None = None,
                           budget_s: float = ROOM_BUDGET_S,
                           now: datetime | float | None = None, owner_name: str = "") -> str:
    """A guest's turn: the lines of its own room's history that bear on
    ``text``, rendered for the reply's prompt (``render_lines`` with the
    owner by the room's mark and ``owner_name``, the name the room's labels
    show for the owner on this reply, W1), or "".

    The scope is ``scope_for_turn(turn)``: the guest's room (a friend's DM
    is a room of its own), never a transcript key (a guest's can carry
    ``:guest:<id>``, which no record room has) and never the model's. A turn
    proven the owner's gets "" (the search tool is theirs), as does a turn
    with no room or no words. The record catches up first, waited for at
    most ``_ROOM_CATCH_UP_SHARE`` of the budget (shielded: a slow catch-up
    goes on by itself, lock and all); the search (``ROOM_LIMIT`` lines) gets
    what is left, and past the budget the section is "" for this turn. Kept:
    hits inside the scope, scoring at least ``ROOM_FLOOR``, said before
    ``shown_since_ts`` (the reply's history already shows the rest; None:
    it shows nothing). Any other failure is the caller's to catch. One log
    line, counts only."""
    started = time.monotonic()
    scope = scope_for_turn(turn)
    query = _their_words(text)
    if scope.room is None or scope.room == NO_ROOM or not query:
        return ""
    share = budget_s * _ROOM_CATCH_UP_SHARE
    catching = asyncio.ensure_future(mri.catch_up(budget_s=share, embed=embed, path=path))
    _CATCHING.add(catching)
    catching.add_done_callback(_caught)
    try:
        await asyncio.wait_for(asyncio.shield(catching), timeout=share)
    except TimeoutError:
        pass                         # it goes on; the search reads the record as it is
    except Exception as exc:         # the search goes on regardless, as the tool's does
        logger.warning("memory reach: a guest step's catch-up failed (%s)",
                       type(exc).__name__)
    left = budget_s - (time.monotonic() - started)
    hits: list[Hit] = []
    over = left <= 0
    if not over:
        try:
            hits = await asyncio.wait_for(
                search(query, scope=scope, limit=ROOM_LIMIT, embed=embed, path=path),
                timeout=left)
        except TimeoutError:
            over = True
    seconds = time.monotonic() - started
    if over:
        logger.warning("memory reach: guest room step ran past its %.1f s budget; no section "
                       "this turn (query_chars=%d seconds=%.2f)", budget_s, len(query), seconds)
        return ""
    kept = [h for h in hits if scope.reaches(h.room) and h.score >= ROOM_FLOOR
            and (shown_since_ts is None or h.ts < shown_since_ts)]
    logger.info("memory reach: guest room step query_chars=%d hits=%d kept=%d seconds=%.2f",
                len(query), len(hits), len(kept), seconds)
    return render_lines(kept, now=now, as_room=True, owner_name=owner_name) if kept else ""


def dated_lines(text: str) -> list[str]:
    """A rendered section's dated lines, the room headings and blank lines
    left out (what the owner's record of a reply keeps)."""
    return [line for line in str(text or "").splitlines()
            if line.strip() and not _ROOM_HEADING.match(line)]


def with_room_memory(ctx: Any, body: str) -> Any:
    """The reply's flat context with a guest's section on it
    (``AgentLoopContext.room_memory_text``); anything else, or no body, is
    returned as it is."""
    from dataclasses import replace

    from core.agent.context_adapter import AgentLoopContext

    if not str(body or "").strip() or not isinstance(ctx, AgentLoopContext):
        return ctx
    return replace(ctx, room_memory_text=str(body).strip())
