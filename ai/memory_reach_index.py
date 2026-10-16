"""Memory that reaches: the record (R15; plan docs/2026-10-13-memory-that-reaches-plan.md).

One searchable record of every message, from the ``conversations`` table and
the chat doors' room files, in its own SQLite file,
``data/memory_reach.sqlite3`` (gitignored; ``PALLI_MEMORY_REACH_PATH``
overrides it, which is how the test suite stays off the live record).

This module holds the switch and the record's path (T1), the store (T2) and
the sources, the catch-up and the build (T3).

The store: ``record`` holds one row per message (``source_ref`` unique, so
adding a row twice is a no-op); ``record_fts`` holds its words (FTS5, porter
stemming, the FTS rowid is the record id); ``record_vec`` holds its meaning
(one float32 vector per record id, filled later for rows that came without
one); ``state`` holds the catch-up's watermarks and the name of the model
that made the vectors (``embed_model``). Two triggers keep the words
and the vectors on their row: a new row's words go in with it, and deleting
a row takes its words and its vector with it.

The sources (T3), one rule and no fuzzy matching: the ``conversations``
table is the record of every owner line and every PaLLi line (user rows from
the chat doors, the phone app and the dashboard, assistant rows from
everywhere); the room files add guests' lines only, read and never written.
The phone app is the owner's alone (``mobile:1``, no guests), so its words
come from the table alone and ``mobile_1.jsonl`` is never read. Both are kept up by
watermark (``db_last_id``; ``file:<name>``), at the start of every search
(``catch_up``, within a time budget, the rest detached) or all at once
(``build``). Meaning comes from the local MiniLM model memory already uses,
only while the owner's local-embeddings setting is on.

What was said aloud keeps a room of its own (W2, fix round 1): a database
row marked as spoken, and her reply to one, is filed under
``discord_voice:<channel>``, never under the chat room a Discord voice
channel's text chat shares its id with. Voice turns write no room file the
record reads (their sessions are keyed ``discord_voice:<owner>``).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import sqlite3
import time
import weakref
from array import array
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable

# Leaves, safe from any layer: none imports anything of the app back.
from core.agent.speakers import normalize_speaker
from core.owner_gate import is_guest_line
from core.session_keys import room_key

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[1]


def enabled() -> bool:
    """companion.memory_reach, fail-closed: unbooted settings, a missing
    field or any error read as OFF."""
    try:
        from core.turn.companion_flags import companion_settings

        comp = companion_settings()
        return comp is not None and bool(getattr(comp, "memory_reach", False))
    except Exception:
        return False


# Stable alias: tests reach the real accessor past the suite's autouse pin.
_enabled_impl = enabled


def record_path() -> Path:
    raw = os.environ.get("PALLI_MEMORY_REACH_PATH")
    if raw:
        return Path(raw)
    return _REPO_ROOT / "data" / "memory_reach.sqlite3"


# --- the store (T2) ----------------------------------------------------------

_SPEAKERS = ("owner", "palli", "guest")


@dataclass(frozen=True)
class Row:
    """One message in the record.

    ``source_ref`` names where it came from and is unique across the record.
    ``day`` is the local day of ``ts``
    (``datetime.fromtimestamp(ts).astimezone().strftime("%Y-%m-%d")``),
    worked out by whoever makes the row. ``speaker`` is "owner", "palli" or
    "guest".
    """

    source: str
    source_ref: str
    room: str
    ts: float
    day: str
    speaker: str
    name: str
    text: str

    def __post_init__(self) -> None:
        if self.speaker not in _SPEAKERS:
            raise ValueError(f"speaker must be one of {_SPEAKERS}, not {self.speaker!r}")


_SCHEMA = """
CREATE TABLE IF NOT EXISTS record (
    id INTEGER PRIMARY KEY,
    source TEXT,
    source_ref TEXT UNIQUE,
    room TEXT,
    ts REAL,
    day TEXT,
    speaker TEXT,
    name TEXT,
    text TEXT
);
CREATE INDEX IF NOT EXISTS record_room_ts ON record(room, ts);
CREATE INDEX IF NOT EXISTS record_ts ON record(ts);
CREATE VIRTUAL TABLE IF NOT EXISTS record_fts USING fts5(text, tokenize='porter unicode61');
CREATE TABLE IF NOT EXISTS record_vec (id INTEGER PRIMARY KEY, vec BLOB);
CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT);
CREATE TRIGGER IF NOT EXISTS record_words_in AFTER INSERT ON record BEGIN
    INSERT INTO record_fts(rowid, text) VALUES (new.id, new.text);
END;
CREATE TRIGGER IF NOT EXISTS record_words_out AFTER DELETE ON record BEGIN
    DELETE FROM record_fts WHERE rowid = old.id;
    DELETE FROM record_vec WHERE id = old.id;
END;
"""

_NO_VECTOR = (
    "FROM record r LEFT JOIN record_vec v ON v.id = r.id WHERE v.id IS NULL"
)


def connect(path: Path | None = None) -> sqlite3.Connection:
    """Open the record (``record_path()`` unless a path is given), making
    the file and its folder if need be: WAL, a 5 s busy timeout, the schema."""
    target = Path(path) if path is not None else record_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(target))
    try:
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA journal_mode=WAL")
        ensure_schema(conn)
    except BaseException:
        conn.close()
        raise
    return conn


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(_SCHEMA)


def add_rows(conn: sqlite3.Connection, rows: list[Row], *,
             vectors: dict[str, bytes] | None) -> int:
    """Add the rows not already in the record (by ``source_ref``), with
    their vectors where ``vectors`` has one for that ``source_ref``. A row
    already there is left as it is, vector and all. Returns how many were
    added."""
    added = 0
    with conn:
        for row in rows:
            cur = conn.execute(
                "INSERT OR IGNORE INTO record"
                "(source, source_ref, room, ts, day, speaker, name, text)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (row.source, row.source_ref, row.room, row.ts, row.day,
                 row.speaker, row.name, row.text),
            )
            if cur.rowcount != 1:
                continue
            added += 1
            vec = vectors.get(row.source_ref) if vectors else None
            if vec is not None:
                conn.execute("INSERT OR REPLACE INTO record_vec(id, vec) VALUES (?, ?)",
                             (cur.lastrowid, vec))
    return added


def pending_vectors(conn: sqlite3.Connection, limit: int) -> list[tuple[int, str]]:
    """``(id, text)`` of up to ``limit`` rows with no vector yet, oldest id first."""
    return list(conn.execute(
        f"SELECT r.id, r.text {_NO_VECTOR} ORDER BY r.id LIMIT ?", (int(limit),)))


def put_vectors(conn: sqlite3.Connection, pairs: list[tuple[int, bytes]]) -> None:
    """Write (or replace) the vectors for these record ids. An id the record
    no longer holds is skipped, so no vector waits for the id's next owner."""
    with conn:
        conn.executemany(
            "INSERT OR REPLACE INTO record_vec(id, vec)"
            " SELECT ?1, ?2 WHERE EXISTS (SELECT 1 FROM record WHERE id = ?1)",
            pairs,
        )


def get_state(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM state WHERE key = ?", (key,)).fetchone()
    return None if row is None else row[0]


def set_state(conn: sqlite3.Connection, key: str, value: str) -> None:
    with conn:
        conn.execute(
            "INSERT INTO state(key, value) VALUES (?, ?)"
            " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )


def status(conn: sqlite3.Connection) -> dict:
    """rows, rooms, first_day, last_day (None while empty), vectors_missing."""
    rows, rooms, first_day, last_day = conn.execute(
        "SELECT COUNT(*), COUNT(DISTINCT room), MIN(day), MAX(day) FROM record").fetchone()
    missing = conn.execute(f"SELECT COUNT(*) {_NO_VECTOR}").fetchone()[0]
    return {"rows": rows, "rooms": rooms, "first_day": first_day,
            "last_day": last_day, "vectors_missing": missing}


def encode_vector(vec: list[float]) -> bytes:
    """A vector as float32 bytes, the form ``record_vec`` keeps."""
    return array("f", vec).tobytes()


# --- the sources (T3) --------------------------------------------------------

TRANSCRIPT_RE = re.compile(r"^(discord|telegram)_(-?\d+)\.jsonl$")

# The database's user rows the record takes: the chat doors', the phone
# app's and the dashboard's. A user row on any other platform (proactive,
# reminder_fire and the rest) is no one's words; her rows count from every
# platform.
_DB_USER_PLATFORMS = ("discord", "telegram", "mobile", "dashboard")
# A message with no words of its own: the doors' stand-ins for a bare
# attachment or a voice note not yet heard.
_PLACEHOLDERS = frozenset({"[image attached]", "[file attached]", "[VOICE_MESSAGE]"})
# The door's guest tag, "[Name]: " (core.owner_gate's pattern), name captured.
_TAG = re.compile(r"^\[([^\n]+?)\]: ")
PALLI_NAME = "PaLLi"

_DB_PAGE = 2000        # database rows read per page
_INLINE_EMBED = 50     # rows a catch-up embeds itself, within its budget
_EMBED_BATCH = 64      # rows per embedding call after that

Embed = Callable[[list[str]], Awaitable[list[list[float]]]]


def _day(ts: float) -> str:
    return datetime.fromtimestamp(ts).astimezone().strftime("%Y-%m-%d")


def _as_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def transcript_files(root: Path) -> list[Path]:
    """The chat doors' room files directly under ``root``, sorted. Sidecars,
    backups, other surfaces' sessions, images and folders never match."""
    try:
        entries = list(Path(root).iterdir())
    except OSError:
        return []
    return sorted(p for p in entries if TRANSCRIPT_RE.match(p.name) and p.is_file())


def room_key_for_file(path: Path) -> str:
    """``discord_123.jsonl`` -> ``discord:123``: the room's session key."""
    m = TRANSCRIPT_RE.match(Path(path).name)
    if m is None:
        raise ValueError(f"not a room file: {Path(path).name!r}")
    return room_key(m.group(1), m.group(2))


# A spoken row: a voice turn's (core.turn.persistence marks its surface) or
# the voice runtime's own (voice_session) — the marks core.turn.proactive's
# _pick_proactive_surface skips spoken rows by.
_VOICE_SURFACES = ("discord_voice", "voice_local")


def _marks(metadata: Any) -> dict:
    """A row's metadata as a dict: {} for none, bad JSON or a non-object."""
    if isinstance(metadata, dict):
        return metadata
    if not metadata:
        return {}
    try:
        value = json.loads(metadata)
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def is_spoken(metadata: Any) -> bool:
    """Whether a row's metadata marks it as said aloud."""
    marks = _marks(metadata)
    return str(marks.get("surface") or "") in _VOICE_SURFACES or (
        marks.get("voice_session") is True or marks.get("voice_session") == 1)


def room_key_for_db(platform: str, channel_id: int, user_id: int, *,
                    spoken: bool = False) -> str:
    """A database row's room, named the way a turn's session key names it.
    A spoken row gets a room of its own, ``discord_voice:<channel>`` (W2,
    fix round 1): a Discord voice channel's text chat shares its channel id,
    and a friend typing there is scoped to ``discord:<channel>``, which must
    never hold what the owner said aloud. Every-room scope still reaches it."""
    if spoken and platform not in ("voice", ""):
        return f"{platform}_voice:{channel_id}"
    if platform in ("discord", "telegram"):
        return room_key(platform, channel_id)
    if platform == "dashboard":
        return f"dashboard:{user_id}"
    return f"{platform}:{channel_id}"


# --- the database ------------------------------------------------------------

def _field(rec: Any, key: str) -> Any:
    try:
        return rec[key]
    except (KeyError, IndexError, TypeError):
        return None


def _db_row(rec: Any, owner_ids: frozenset[int]) -> Row | None:
    """One ``conversations`` row as the record keeps it, or None. The speaker
    comes from the role and the owner's ids, never from ``user_id`` alone:
    her replies to a guest carry the guest's id.

    A spoken row goes to its voice room (``room_key_for_db(spoken=True)``):
    its own marks say so, or, for her reply, which carries no mark of its
    own, the user row it follows in that channel does
    (``answered_metadata``, read by ``db_rows_since``)."""
    role = str(rec["role"] or "").strip().lower()
    platform = str(rec["platform"] or "").strip().lower()
    text = str(rec["content"] or "").strip()
    if not text or text in _PLACEHOLDERS:
        return None
    user_id = _as_int(rec["user_id"])
    if role == "assistant":
        speaker, name = "palli", PALLI_NAME
    elif role == "user" and platform in _DB_USER_PLATFORMS:
        if user_id is not None and user_id in owner_ids:
            speaker, name = "owner", str(rec["username"] or "").strip()
        else:
            tag = _TAG.match(text)
            if tag:
                text = text[tag.end():].strip()
                if not text or text in _PLACEHOLDERS:
                    return None
            speaker = "guest"
            name = (str(rec["username"] or "").strip()
                    or (tag.group(1).strip() if tag else "") or "someone")
    else:
        return None
    try:
        ts = float(rec["timestamp"])
    except (TypeError, ValueError):
        return None
    own = _field(rec, "metadata")
    spoken = is_spoken(own) or (role == "assistant" and not _marks(own)
                                and is_spoken(_field(rec, "answered_metadata")))
    return Row(source="db", source_ref=f"db:{int(rec['id'])}",
               room=room_key_for_db(platform, rec["channel_id"], user_id, spoken=spoken),
               ts=ts, day=_day(ts), speaker=speaker, name=name, text=text)


async def db_rows_since(brain: Any, *, last_id: int, owner_ids: frozenset[int],
                        limit: int = 2000) -> tuple[list[Row], int]:
    """The ``conversations`` rows after ``last_id`` (at most ``limit``, by
    id) that the record keeps, and the last id read. The watermark moves
    past the rows it leaves out too, so none is read twice. Each row comes
    with its metadata and, for her reply, the metadata of the latest user
    row before it in the same channel (whether it answered something said
    aloud: her spoken replies carry no mark of their own)."""
    conn = await brain.get_connection()
    try:
        fetched = await conn.fetch(
            "SELECT c.id, c.user_id, c.platform, c.channel_id, c.role, c.username,"
            " c.content, c.timestamp, c.metadata,"
            " CASE WHEN c.role = 'assistant' THEN ("
            "SELECT u.metadata FROM conversations u"
            " WHERE u.platform = c.platform AND u.channel_id = c.channel_id"
            " AND u.role = 'user' AND u.timestamp <= c.timestamp AND u.id < c.id"
            " ORDER BY u.timestamp DESC, u.id DESC LIMIT 1) END AS answered_metadata"
            " FROM conversations c WHERE c.id > $1 ORDER BY c.id LIMIT $2",
            int(last_id), int(limit))
    finally:
        await conn.close()
    rows: list[Row] = []
    top = int(last_id)
    for rec in fetched:
        top = max(top, int(rec["id"]))
        row = _db_row(rec, owner_ids)
        if row is not None:
            rows.append(row)
    return rows, top


# --- the room files ----------------------------------------------------------

class _BadLine(ValueError):
    """A room-file line the record can't read: skipped and counted."""


def _line_text(content: Any) -> str:
    """A line's words: a string, or the text parts of a list of parts."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            b["text"] for b in content
            if isinstance(b, dict) and b.get("type") == "text" and isinstance(b.get("text"), str))
    return ""


def _epoch(raw: Any) -> float:
    if not isinstance(raw, str):
        raise _BadLine("no timestamp")
    try:
        when = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise _BadLine("bad timestamp") from exc
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return when.timestamp()


def _file_row(obj: Any, *, name: str, lineno: int, room: str) -> Row | None:
    """A guest's line from a room file, or None for anyone else's. Guests'
    lines carry a guest or hidden speaker; before speakers (10-08) only the
    door's ``[Name]: `` tag marked them. The tag goes into ``name``."""
    if not isinstance(obj, dict):
        raise _BadLine("not an object")
    if obj.get("role") != "user":
        return None
    text = _line_text(obj.get("content")).strip()
    speaker = normalize_speaker(obj.get("speaker"))
    if speaker is not None:
        if speaker["kind"] not in ("guest", "hidden"):
            return None
    elif not is_guest_line(text):
        return None
    tag = _TAG.match(text)
    body = (text[tag.end():] if tag else text).strip()
    if not body or body in _PLACEHOLDERS:
        return None
    shown = speaker or {}
    who = (shown.get("name") or (tag.group(1).strip() if tag else "")
           or shown.get("handle") or "someone")
    ts = _epoch(obj.get("timestamp"))
    return Row(source="room_file", source_ref=f"{name}:{lineno}", room=room, ts=ts,
               day=_day(ts), speaker="guest", name=who, text=body)


def room_file_rows(path: Path, *, offset: int, lineno: int) -> tuple[list[Row], int, int, int]:
    """The guests' lines of a room file from byte ``offset`` on (``lineno``
    lines read before it): the rows, the new offset, the new line count and
    how many bad lines were skipped. The file is only ever read. A last line
    with no newline yet is being written: it waits for the next look."""
    path = Path(path)
    with path.open("rb") as f:
        f.seek(offset)
        data = f.read()
    end = data.rfind(b"\n") + 1
    if end == 0:
        return [], offset, lineno, 0
    room = room_key_for_file(path)
    rows: list[Row] = []
    skipped = 0
    for raw in data[:end].split(b"\n")[:-1]:
        lineno += 1
        line = raw.strip()
        if not line:
            continue
        try:
            row = _file_row(json.loads(line), name=path.name, lineno=lineno, room=room)
        except ValueError:          # bad JSON, bad UTF-8, or a _BadLine
            skipped += 1
            continue
        if row is not None:
            rows.append(row)
    return rows, offset + end, lineno, skipped


def _read_mark(raw: str | None) -> dict | None:
    if raw is None:
        return None
    try:
        mark = json.loads(raw)
        return {k: int(mark[k]) for k in ("size", "ino", "offset", "lineno")}
    except (ValueError, TypeError, KeyError):
        return None


def _starts_a_line(path: Path, offset: int) -> bool:
    if offset <= 0:
        return True
    try:
        with path.open("rb") as f:
            f.seek(offset - 1)
            return f.read(1) == b"\n"
    except OSError:
        return False


def _like_file(name: str) -> str:
    """A LIKE pattern for every line of one room file (``_`` and ``%`` in
    the name taken literally, with ESCAPE '\\')."""
    return re.sub(r"([\\%_])", r"\\\1", name) + ":%"


def _vectors_of_file(conn: sqlite3.Connection, name: str) -> dict[str, bytes]:
    """The vectors a room file's lines have now, by their text: a line that
    comes back word for word after a re-read keeps its vector."""
    return dict(conn.execute(
        "SELECT r.text, v.vec FROM record r JOIN record_vec v ON v.id = r.id"
        " WHERE r.source_ref LIKE ? ESCAPE '\\'", (_like_file(name),)))


def _forget_file(conn: sqlite3.Connection, name: str) -> None:
    """Every line of one room file out of the record (the store's trigger
    takes their words and vectors), and its watermark."""
    with conn:
        conn.execute("DELETE FROM record WHERE source_ref LIKE ? ESCAPE '\\'",
                     (_like_file(name),))
        conn.execute("DELETE FROM state WHERE key = ?", (f"file:{name}",))


def _ingest_file(conn: sqlite3.Connection, path: Path) -> tuple[int, int, bool, bool]:
    """One room file's new lines into the record: (added, skipped, reread,
    moved on: its watermark advanced or it was read again). A file that
    shrank, was replaced (another inode), or whose offset no longer starts a
    line was rewritten: it is read again whole, and its unchanged lines keep
    their vectors."""
    name = path.name
    key = f"file:{name}"
    st = path.stat()
    raw = get_state(conn, key)
    mark = _read_mark(raw)
    offset = lineno = 0
    reread = False
    kept: dict[str, bytes] = {}
    if raw is not None:
        if (mark is None or st.st_ino != mark["ino"] or st.st_size < mark["size"]
                or not _starts_a_line(path, mark["offset"])):
            kept = _vectors_of_file(conn, name)
            _forget_file(conn, name)
            reread = True
        elif st.st_size == mark["offset"]:
            return 0, 0, False, False
        else:
            offset, lineno = mark["offset"], mark["lineno"]
    start = offset
    rows, offset, lineno, skipped = room_file_rows(path, offset=offset, lineno=lineno)
    vectors = {row.source_ref: kept[row.text] for row in rows if row.text in kept}
    added = add_rows(conn, rows, vectors=vectors or None)
    set_state(conn, key, json.dumps({"size": max(st.st_size, offset), "ino": st.st_ino,
                                     "offset": offset, "lineno": lineno}))
    return added, skipped, reread, reread or offset != start


def _ingest_files(target: Path, root: Path, deadline: float | None = None, *,
                  at_least_one: bool = False,
                  chunk_s: float | None = None) -> tuple[int, int, int, bool]:
    """The room files' new lines, file by file until ``deadline`` (a
    ``time.monotonic()`` time; None = all): (added, skipped lines, files
    re-read, every file done). What is left resumes from the watermarks.
    ``chunk_s`` sets the deadline here, in the worker thread, once the
    record is open, so a thread that starts late still gets its time.
    ``at_least_one`` holds the deadline off until a file has moved on, so
    files already caught up are walked past and a late call still reads
    the first file with something new."""
    added = skipped = reread = 0
    moved = False
    conn = connect(target)
    try:
        if chunk_s is not None:
            deadline = time.monotonic() + chunk_s
        for path in transcript_files(root):
            if (deadline is not None and time.monotonic() >= deadline
                    and (moved or not at_least_one)):
                return added, skipped, reread, False
            try:
                a, s, r, m = _ingest_file(conn, path)
            except OSError as exc:
                logger.warning("memory reach: room file %s not read this time (%s)",
                               path.name, type(exc).__name__)
                continue
            added, skipped, reread = added + a, skipped + s, reread + int(r)
            moved = moved or m
    finally:
        conn.close()
    return added, skipped, reread, True


# --- keeping up --------------------------------------------------------------
# Every record operation below runs in a worker thread with its own
# connection (a sqlite3 connection belongs to the thread that opened it).
# One asyncio lock serialises the writes in this process: it is held for a
# step of reading the sources (one budget's worth, or one chunk), around
# reading the rows that need vectors and around writing them, and while a
# rebuild is put in place; never while the model works.

_CHUNK_S = 0.5            # the most a detached step holds the lock reading sources
_MIN_INLINE_S = 0.5       # a catch-up tries embedding inline only with this much left
_EMBED_BACKOFF_S = 300.0  # after the embedder fails, no retry for five minutes
_DETACHED_EMBED_S = 120.0 # the most one detached (or build) batch may take the model
_SETTLE_TRIES = 3         # a rebuild looks this many times for the old record to be free
_SETTLE_WAIT_S = 0.25     # between those looks

_LOCKS: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Lock]" = (
    weakref.WeakKeyDictionary())
# The rest of a catch-up, detached: one task per record path, kept here so it
# is never collected mid-run.
_BACKLOG: dict[str, asyncio.Task] = {}
# Record ids whose vectors are being made right now, per record path, so two
# embedders never take the same rows.
_IN_FLIGHT: dict[str, set[int]] = {}
# The embedder failed: no retry before this ``time.monotonic()`` time.
_EMBED_PAUSE: dict[str, float] = {"until": 0.0}
# Things said once per process ("owner").
_NOTED: set[str] = set()


def _lock() -> asyncio.Lock:
    """The record's lock for the running loop (an asyncio lock belongs to
    the loop it first waits on; the bot has one loop, a test or a script
    its own)."""
    loop = asyncio.get_running_loop()
    lock = _LOCKS.get(loop)
    if lock is None:
        lock = _LOCKS[loop] = asyncio.Lock()
    return lock


async def _acquire(lock: asyncio.Lock, deadline: float | None) -> bool:
    """Take the lock, waiting no later than ``deadline`` (None: as long as
    it takes). False when the time ran out."""
    if deadline is None:
        await lock.acquire()
        return True
    try:
        # A spent budget still takes a free lock (a timeout of 0 would not).
        await asyncio.wait_for(lock.acquire(),
                               timeout=max(deadline - time.monotonic(), 0.001))
    except TimeoutError:
        return False
    return True


def _note_once(key: str, message: str) -> None:
    if key not in _NOTED:
        _NOTED.add(key)
        logger.warning(message)


def _failed(step: str, exc: BaseException) -> str:
    """``step: ErrorType``, with SQLite's own message (it never carries a
    message's words); other errors by their type only."""
    why = type(exc).__name__
    if isinstance(exc, sqlite3.Error) and str(exc):
        why += f" ({exc})"
    return f"{step}: {why}"


def _get_state_at(target: Path, key: str) -> str | None:
    conn = connect(target)
    try:
        return get_state(conn, key)
    finally:
        conn.close()


def _status_at(target: Path) -> dict:
    conn = connect(target)
    try:
        return status(conn)
    finally:
        conn.close()


def _add_db_page(target: Path, rows: list[Row], top: int) -> int:
    conn = connect(target)
    try:
        added = add_rows(conn, rows, vectors=None)
        set_state(conn, "db_last_id", str(top))
        return added
    finally:
        conn.close()


async def _ingest_db(target: Path, brain: Any, owner_ids: frozenset[int], *,
                     deadline: float | None = None, at_least_one: bool = False,
                     chunk_s: float | None = None,
                     say: Callable[[str], Any] | None = None) -> tuple[int, bool]:
    """Database pages from the watermark until the end or ``deadline`` (a
    ``time.monotonic()`` time; None = to the end; ``chunk_s`` starts one
    once the watermark is read; ``at_least_one`` reads the first page
    whatever the time, and every page moves the watermark on): (added,
    reached the end). The caller holds the lock."""
    last = _as_int(await asyncio.to_thread(_get_state_at, target, "db_last_id")) or 0
    if chunk_s is not None:
        deadline = time.monotonic() + chunk_s
    added = 0
    first = True
    while True:
        if (deadline is not None and time.monotonic() >= deadline
                and not (at_least_one and first)):
            return added, False
        first = False
        rows, top = await db_rows_since(brain, last_id=last, owner_ids=owner_ids,
                                        limit=_DB_PAGE)
        if top <= last:
            return added, True
        added += await asyncio.to_thread(_add_db_page, target, rows, top)
        last = top
        if say is not None:
            say(f"database: read to id {top}, {added} lines added")


async def _ingest_chunks(target: Path, brain: Any, owner_ids: frozenset[int],
                         root: Path, say: Callable[[str], Any] | None = None) -> dict:
    """Both sources to the end, a chunk at a time under the lock (each
    chunk reads at least one page or file, so it always moves on), so a
    search's catch-up waits one chunk at most. With the owner's ids unknown
    the database waits."""
    totals = {"db_rows": 0, "file_rows": 0, "skipped_lines": 0, "reread_files": 0}
    lock = _lock()
    db_done = not owner_ids
    while not db_done:
        async with lock:
            added, db_done = await _ingest_db(target, brain, owner_ids, chunk_s=_CHUNK_S,
                                              at_least_one=True, say=say)
        totals["db_rows"] += added
    files_done = False
    while not files_done:
        async with lock:
            added, skipped, reread, files_done = await asyncio.to_thread(
                _ingest_files, target, root, chunk_s=_CHUNK_S, at_least_one=True)
        totals["file_rows"] += added
        totals["skipped_lines"] += skipped
        totals["reread_files"] += reread
    return totals


# The model that made the record's vectors, kept in ``state``: vectors from
# two models are never mixed in one record.
EMBED_MODEL_KEY = "embed_model"
_MODEL_DEFAULT = "all-MiniLM-L6-v2"


def embed_model_name() -> str:
    """The local model the owner's settings name now
    (MEMORY_LOCAL_EMBEDDINGS_MODEL), read as ``core.memory_service`` reads it."""
    try:
        from core.config import config

        raw = getattr(config, "MEMORY_LOCAL_EMBEDDINGS_MODEL", _MODEL_DEFAULT)
    except Exception:
        raw = _MODEL_DEFAULT
    return str(raw or _MODEL_DEFAULT).strip() or _MODEL_DEFAULT


def _settle_model(conn: sqlite3.Connection, model: str) -> str | None:
    """One model's vectors per record. When the record names another model
    than ``model``, its vectors go (every line is embedded again) and it
    names ``model`` from now on: returns the name it had. Else None."""
    stored = get_state(conn, EMBED_MODEL_KEY)
    if stored is None or stored == model:
        return None
    with conn:
        conn.execute("DELETE FROM record_vec")
        conn.execute("INSERT INTO state(key, value) VALUES (?, ?)"
                     " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                     (EMBED_MODEL_KEY, model))
    return stored


def _settle_model_at(target: Path, model: str) -> str | None:
    conn = connect(target)
    try:
        return _settle_model(conn, model)
    finally:
        conn.close()


def _pending_at(target: Path, limit: int,
                model: str) -> tuple[list[tuple[int, str]], str | None]:
    """The rows still without a vector (after ``_settle_model``), and the
    model name the record had if it changed."""
    conn = connect(target)
    try:
        replaced = _settle_model(conn, model)
        return pending_vectors(conn, limit), replaced
    finally:
        conn.close()


def _put_vectors_checked(conn: sqlite3.Connection,
                         items: list[tuple[int, str, bytes]], *,
                         model: str | None = None) -> int:
    """Write each ``(id, text, vector)`` only where the id still holds the
    text the vector was made from: a re-read file, or a rebuild in another
    process, can hand a deleted line's id to another line meanwhile. The
    vectors are ``model``'s (default: the one the settings name now): none
    is written into a record that names another model, and a record that
    names none names this one from its first vector on. Returns how many
    were written."""
    if not items:
        return 0
    model = model or embed_model_name()
    ids = [rid for rid, _, _ in items]
    conn.execute("BEGIN IMMEDIATE")
    try:
        stored = get_state(conn, EMBED_MODEL_KEY)
        if stored is not None and stored != model:
            conn.rollback()
            return 0
        marks = ",".join("?" * len(ids))
        now = dict(conn.execute(f"SELECT id, text FROM record WHERE id IN ({marks})", ids))
        pairs = [(rid, vec) for rid, text, vec in items if now.get(rid) == text]
        if pairs and stored is None:
            conn.execute("INSERT INTO state(key, value) VALUES (?, ?)",
                         (EMBED_MODEL_KEY, model))
        put_vectors(conn, pairs)                    # commits
    except BaseException:
        conn.rollback()
        raise
    return len(pairs)


def _put_at(target: Path, items: list[tuple[int, str, bytes]], model: str) -> int:
    conn = connect(target)
    try:
        return _put_vectors_checked(conn, items, model=model)
    finally:
        conn.close()


async def _embed_some(target: Path, embed: Embed, limit: int, *,
                      deadline: float | None = None,
                      model: str | None = None) -> tuple[int, int]:
    """Vectors for up to ``limit`` rows that have none and that no other
    embedder has taken: (taken, written). The lock is held to read the rows
    and to write the vectors, not while the model works. ``deadline`` bounds
    the wait for the lock and for the model (``TimeoutError``). A failing
    embedder pauses embedding for ``_EMBED_BACKOFF_S``. ``model`` (default:
    the one the settings name now) is the model's name: a record that names
    another model loses its vectors first, so every line is embedded again."""
    key = str(target)
    model = model or embed_model_name()
    lock = _lock()
    if not await _acquire(lock, deadline):
        raise TimeoutError
    try:
        taken_ids = _IN_FLIGHT.setdefault(key, set())
        rows, replaced = await asyncio.to_thread(_pending_at, target,
                                                 limit + len(taken_ids), model)
        if replaced is not None:
            logger.warning("memory reach: the record's meaning was made by %s and the "
                           "settings now name %s; every line's meaning is made again",
                           replaced, model)
        pending = [(rid, text) for rid, text in rows if rid not in taken_ids][:limit]
        taken_ids.update(rid for rid, _ in pending)
    finally:
        lock.release()
    if not pending:
        return 0, 0
    try:
        try:
            made = embed([text for _, text in pending])
            if deadline is None:
                vectors = await made
            else:
                vectors = await asyncio.wait_for(made, timeout=deadline - time.monotonic())
            if len(vectors) != len(pending):
                raise ValueError(
                    f"the embedder gave {len(vectors)} vectors for {len(pending)} lines")
        except (TimeoutError, asyncio.CancelledError):
            raise
        except Exception:
            pause_embedding()
            raise
        items = [(rid, text, encode_vector(vec))
                 for (rid, text), vec in zip(pending, vectors, strict=True)]
        async with lock:
            written = await asyncio.to_thread(_put_at, target, items, model)
    finally:
        taken_ids.difference_update(rid for rid, _ in pending)
    return len(pending), written


async def _default_embed(texts: list[str]) -> list[list[float]]:
    """The local MiniLM model memory already loads (``core.memory_service``),
    one call for the batch, off the loop."""
    from core.memory_service import memory_workspace_service

    model = await memory_workspace_service._ensure_local_model(embed_model_name())
    if model is None:
        raise RuntimeError("the local embedding model is not available")
    vectors = await asyncio.to_thread(model.encode, list(texts), batch_size=64,
                                      normalize_embeddings=True)
    return [[float(x) for x in vec] for vec in vectors]


def local_embeddings_on() -> bool:
    """The owner's setting (MEMORY_LOCAL_EMBEDDINGS_ENABLED), as memory
    reads it: off means the model is never loaded."""
    try:
        from core.config import config

        return bool(getattr(config, "MEMORY_LOCAL_EMBEDDINGS_ENABLED", True))
    except Exception:
        return False


def _embedder(embed: Embed | None) -> Embed | None:
    """The caller's embedder; else the local model while the setting is on;
    None means words only."""
    if embed is not None:
        return embed
    return _default_embed if local_embeddings_on() else None


def _meaning(embed: Embed | None) -> str:
    """"on", "off" (words only, by the setting) or "backing off" (the
    embedder failed a few minutes ago)."""
    if embed is None:
        return "off"
    return "backing off" if time.monotonic() < _EMBED_PAUSE["until"] else "on"


# The back-off, for every reader of the record (the search too): one rule,
# kept here.

def embedder_for(embed: Embed | None) -> Embed | None:
    """The embedder to use now: the caller's, else the local model while the
    owner's setting is on; None (words only) while the setting is off or the
    embedder is backing off."""
    chosen = _embedder(embed)
    return chosen if _meaning(chosen) == "on" else None


def pause_embedding() -> float:
    """The embedder failed: no embedding, by the catch-up or by a search,
    for the next ``_EMBED_BACKOFF_S`` seconds. Returns that pause."""
    _EMBED_PAUSE["until"] = time.monotonic() + _EMBED_BACKOFF_S
    return _EMBED_BACKOFF_S


def _running(target: Path) -> bool:
    task = _BACKLOG.get(str(target))
    return (task is not None and not task.done()
            and task.get_loop() is asyncio.get_running_loop())


async def _finish(target: Path, brain: Any, root: Path, embed: Embed | None) -> None:
    """The rest of a catch-up, detached: the sources to the end a chunk at
    a time, then the missing vectors a batch at a time."""
    try:
        owner_ids = await _owner_ids()
        await _ingest_chunks(target, brain, owner_ids, root)
        while _meaning(embed) == "on":
            try:
                taken, written = await _embed_some(
                    target, embed, _EMBED_BATCH,
                    deadline=time.monotonic() + _DETACHED_EMBED_S)
            except TimeoutError:
                # A model that never answers: its rows are free again (the
                # reservation ends with the call), and embedding pauses.
                pause_embedding()
                logger.warning("memory reach: the embedder gave no answer in %.0f s "
                               "(TimeoutError); embedding pauses for %.0f minutes",
                               _DETACHED_EMBED_S, _EMBED_BACKOFF_S / 60)
                return
            if taken == 0 or written == 0:
                return
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.warning("memory reach: the detached catch-up stopped (%s); "
                       "the rest waits for the next look", _failed("step", exc))


def _spawn_finish(target: Path, brain: Any, root: Path, embed: Embed | None) -> bool:
    key = str(target)
    if _running(target):
        return True                # already running: it reaches these rows too
    task = asyncio.get_running_loop().create_task(
        _finish(target, brain, root, embed), name="memory-reach-catch-up")
    _BACKLOG[key] = task
    task.add_done_callback(
        lambda t, k=key: _BACKLOG.pop(k, None) if _BACKLOG.get(k) is t else None)
    return True


def _sessions_root() -> Path:
    """Where the doors keep the room files (the dispatcher's own setting)."""
    raw = os.environ.get("PALLI_AGENT_SESSIONS_ROOT")
    return Path(raw) if raw else _REPO_ROOT / "data" / "agent_sessions"


def _default_brain() -> Any:
    from core.database import brain

    return brain


async def _owner_ids() -> frozenset[int]:
    """The owner's ids on every surface (``core.local_mode``); none when they
    can't be read, and then no database line can be told apart."""
    try:
        from core.local_mode import get_local_owner_refs

        refs = await get_local_owner_refs()
    except Exception as exc:
        logger.warning("memory reach: the owner's ids could not be read (%s)",
                       _failed("owner", exc))
        return frozenset()
    return frozenset(int(v) for v in refs.values() if v)


_OWNER_UNKNOWN = "db: owner unknown"


async def catch_up(*, brain: Any = None, sessions_root: Path | None = None,
                   embed: Embed | None = None, budget_s: float = 1.5,
                   path: Path | None = None) -> dict:
    """Bring the record up to now, at the start of a search or a guest's
    step, within ``budget_s``: new database rows page by page and new
    room-file lines file by file, by their words, until the budget runs
    out; then, with time left, up to 50 rows' meaning inline. What is left
    (sources or vectors) goes on in one detached task and resumes from the
    watermarks. Embedding is off while the owner's local-embeddings setting
    is off, and pauses for a few minutes after the embedder fails. Never
    raises: a failed step is named in ``errors`` and the search goes on with
    the record as it is. ``busy`` when the lock stayed taken all budget."""
    started = time.monotonic()
    deadline = started + budget_s
    target = Path(path) if path is not None else record_path()
    root = Path(sessions_root) if sessions_root is not None else _sessions_root()
    embed = _embedder(embed)
    out: dict[str, Any] = {
        "added": 0, "db_rows": 0, "file_rows": 0, "skipped_lines": 0, "reread_files": 0,
        "embedded": 0, "vectors_missing": 0, "complete": False, "detached": False,
        "busy": False, "meaning": _meaning(embed), "errors": [], "seconds": 0.0,
    }
    lock = _lock()
    if not await _acquire(lock, deadline):
        out["busy"] = True
        out["seconds"] = round(time.monotonic() - started, 3)
        return out
    db_left = files_left = False        # stopped by the budget, not by an error
    db_done = files_done = False
    try:
        try:
            brain = brain or _default_brain()
            owner_ids = await _owner_ids()
            if owner_ids:
                out["db_rows"], db_done = await _ingest_db(target, brain, owner_ids,
                                                           deadline=deadline)
                db_left = not db_done
            else:
                out["errors"].append(_OWNER_UNKNOWN)
                _note_once("owner", "memory reach: the owner's ids are unknown, so the "
                                    "database's lines wait until they are (said once)")
        except Exception as exc:
            out["errors"].append(_failed("db", exc))
        try:
            added, skipped, reread, files_done = await asyncio.to_thread(
                _ingest_files, target, root, deadline)
            out.update(file_rows=added, skipped_lines=skipped, reread_files=reread)
            files_left = not files_done
        except Exception as exc:
            out["errors"].append(_failed("files", exc))
    finally:
        lock.release()
    out["added"] = out["db_rows"] + out["file_rows"]
    out["complete"] = db_done and files_done
    if (out["meaning"] == "on" and not _running(target)
            and deadline - time.monotonic() >= _MIN_INLINE_S):
        try:
            _, out["embedded"] = await _embed_some(target, embed, _INLINE_EMBED,
                                                   deadline=deadline)
        except TimeoutError:
            pass                                     # the detached task takes them
        except Exception as exc:
            out["errors"].append(_failed("embed", exc))
    try:
        out["vectors_missing"] = (await asyncio.to_thread(_status_at, target))[
            "vectors_missing"]
    except Exception as exc:
        out["errors"].append(_failed("status", exc))
    if db_left or files_left or (out["vectors_missing"] > 0 and _meaning(embed) == "on"):
        out["detached"] = _spawn_finish(target, brain, root, embed)
    out["seconds"] = round(time.monotonic() - started, 3)
    errors = [e for e in out["errors"] if e != _OWNER_UNKNOWN]
    if errors or out["added"] or out["reread_files"]:
        logger.log(
            logging.WARNING if errors else logging.INFO,
            "memory reach: catch-up added=%d db=%d files=%d skipped=%d reread=%d "
            "embedded=%d missing=%d complete=%s meaning=%s errors=%s seconds=%.2f",
            out["added"], out["db_rows"], out["file_rows"], out["skipped_lines"],
            out["reread_files"], out["embedded"], out["vectors_missing"], out["complete"],
            out["meaning"], "; ".join(errors) or "none", out["seconds"])
    return out


# --- the one-time build ------------------------------------------------------

def _building_path(target: Path) -> Path:
    return target.with_name(target.name + ".building")


def _sidecars(path: Path) -> tuple[Path, ...]:
    return tuple(Path(f"{path}{s}") for s in ("-wal", "-shm", "-journal"))


def _unlink_sidecars(path: Path) -> None:
    for p in _sidecars(path):
        try:
            p.unlink()
        except FileNotFoundError:
            pass


def _remove_db(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    _unlink_sidecars(path)


class RecordInUse(RuntimeError):
    """A rebuild found the old record open elsewhere (PaLLi, most likely)
    and left it as it was."""


def _settle_old(target: Path) -> None:
    """Fold the old record's -wal into it and let the last close remove the
    -wal and -shm. Left beside the new file (a crash leaves them), SQLite
    would read them as its own and the old lines would come back. With no
    old record, its leftovers belong to nothing and go. Refuses
    (``RecordInUse``, a ``RuntimeError``) while another connection has the
    old record open: the checkpoint reports busy, or the -wal outlives this
    close."""
    if not target.exists():
        _unlink_sidecars(target)
        return
    for attempt in range(_SETTLE_TRIES):
        if attempt:
            time.sleep(_SETTLE_WAIT_S)
        try:
            conn = sqlite3.connect(str(target), timeout=_SETTLE_WAIT_S)
            try:
                busy = conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()[0]
            finally:
                conn.close()
        except sqlite3.OperationalError:
            busy = 1                         # locked: someone is writing it
        except sqlite3.DatabaseError:
            _unlink_sidecars(target)         # not a database: its sidecars are nobody's
            return
        if not busy and not any(Path(f"{target}{s}").exists() for s in ("-wal", "-shm")):
            return
    logger.warning("memory reach: the record is open elsewhere; the rebuild was not put "
                   "in place (it would have left that connection's -wal under the new file)")
    raise RecordInUse("memory reach: the record is open elsewhere (PaLLi, most likely), so "
                       "the rebuild was not put in place; run it again when PaLLi is quiet "
                       "or stopped")


def _put_in_place(work: Path, target: Path) -> None:
    """The new record in place in one rename: in rollback-journal mode
    first, so no -wal or -shm of its own goes with it, and the old record's
    sidecars settled, so none of theirs is left for it. Refuses while the
    old record is open elsewhere (``_settle_old``)."""
    conn = sqlite3.connect(str(work))
    try:
        mode = conn.execute("PRAGMA journal_mode=DELETE").fetchone()[0]
    finally:
        conn.close()
    if str(mode).lower() != "delete":
        raise RuntimeError(f"memory reach: the new record stayed in {mode} mode")
    target.parent.mkdir(parents=True, exist_ok=True)
    _settle_old(target)
    os.replace(work, target)


async def _embed_all(target: Path, embed: Embed,
                     say: Callable[[str], Any]) -> tuple[int, str]:
    """Every missing vector, a batch at a time: (written, error name or "").
    A record whose vectors another model made is embedded again whole."""
    model = embed_model_name()
    async with _lock():
        replaced = await asyncio.to_thread(_settle_model_at, target, model)
    if replaced is not None:
        say(f"meaning: made by {replaced} before; the settings now name {model}, "
            "so every line is embedded again")
    total = (await asyncio.to_thread(_status_at, target))["vectors_missing"]
    done = 0
    next_say = _EMBED_BATCH * 16
    try:
        while True:
            taken, written = await _embed_some(target, embed, _EMBED_BATCH,
                                               deadline=time.monotonic() + _DETACHED_EMBED_S,
                                               model=model)
            done += written
            if taken == 0 or written == 0:
                break
            if done >= next_say:
                say(f"meaning: {done} of {total} lines")
                next_say += _EMBED_BATCH * 16
    except Exception as exc:
        logger.warning("memory reach: the build's embedding stopped (%s)",
                       _failed("embed", exc))
        say(f"meaning: stopped after {done} of {total} lines ({type(exc).__name__}); "
            "the rest fill in at the next catch-up")
        return done, type(exc).__name__
    say(f"meaning: {done} of {total} lines")
    return done, ""


async def build(*, brain: Any = None, sessions_root: Path | None = None,
                embed: Embed | None = None, rebuild: bool = False,
                path: Path | None = None,
                progress: Callable[[str], Any] | None = None) -> dict:
    """Fill the record from both sources, words and meaning, with no time
    budget (``palli_ctl memory reach-build``). With ``rebuild`` it starts
    from nothing in ``<path>.building`` beside the record and puts that in
    place with one ``os.replace``: a reader opens the old record or the new
    one, never half of either. Words only while the owner's local-embeddings
    setting is off. ``progress`` gets short count lines, never a message's
    words. Returns ``status()``'s counts with the sources' (``db_rows``,
    ``file_rows``, ``skipped_lines``), ``embedded``, ``embed_model`` (the
    model that made the record's vectors; None while it has none),
    ``seconds``, and ``embed_error`` when embedding stopped part-way.
    Raises when the owner's ids are unknown, before anything is written, and
    (``RecordInUse``) when a rebuild finds the old record open elsewhere,
    which it leaves as it was."""
    started = time.monotonic()
    target = Path(path) if path is not None else record_path()
    root = Path(sessions_root) if sessions_root is not None else _sessions_root()
    embed = _embedder(embed)
    say: Callable[[str], Any] = progress or (lambda _line: None)
    owner_ids = await _owner_ids()
    if not owner_ids:
        raise RuntimeError("memory reach: the owner's ids are unknown, so no database "
                           "line can be told apart; nothing was built")
    brain = brain or _default_brain()
    work = _building_path(target) if rebuild else target
    if rebuild:
        await asyncio.to_thread(_remove_db, work)
    try:
        totals = await _ingest_chunks(work, brain, owner_ids, root, say)
        say(f"room files: {totals['file_rows']} guests' lines added, "
            f"{totals['skipped_lines']} bad lines skipped")
        if embed is None:
            embedded, embed_error = 0, ""
            say("meaning: local embeddings are off in the settings; words only")
        else:
            embedded, embed_error = await _embed_all(work, embed, say)
        if rebuild:
            async with _lock():
                await asyncio.to_thread(_put_in_place, work, target)
    except BaseException:
        if rebuild:
            _remove_db(work)
        raise
    result = dict(await asyncio.to_thread(_status_at, target))
    result.update(db_rows=totals["db_rows"], file_rows=totals["file_rows"],
                  skipped_lines=totals["skipped_lines"], embedded=embedded,
                  embed_model=await asyncio.to_thread(_get_state_at, target,
                                                      EMBED_MODEL_KEY),
                  seconds=round(time.monotonic() - started, 1))
    if embed_error:
        result["embed_error"] = embed_error
    return result
