"""Memory that reaches: the record's store (R15, T2; plan
docs/2026-10-13-memory-that-reaches-plan.md).

One SQLite file holds every message once: the words (``record_fts``, FTS5
with porter stemming, its rowid the record's id), the meaning
(``record_vec``, one float32 vector per row) and the catch-up's watermarks
(``state``). Every test here runs on a tmp path, never the live record.
"""
from __future__ import annotations

import sqlite3
import struct
from array import array
from datetime import datetime
from pathlib import Path

import pytest

from ai import memory_reach_index as mri


def _day(ts: float) -> str:
    return datetime.fromtimestamp(ts).astimezone().strftime("%Y-%m-%d")


def _row(ref: str, text: str, *, room: str = "discord:1", ts: float = 1_760_000_000.0,
         speaker: str = "owner", name: str = "Robin", source: str = "db") -> mri.Row:
    return mri.Row(source=source, source_ref=ref, room=room, ts=ts, day=_day(ts),
                   speaker=speaker, name=name, text=text)


@pytest.fixture
def conn(tmp_path):
    c = mri.connect(tmp_path / "record.sqlite3")
    yield c
    c.close()


def _count(conn, table: str) -> int:
    return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


# --- connect -----------------------------------------------------------------

def test_connect_on_a_tmp_path_creates_the_file_in_wal(tmp_path):
    path = tmp_path / "nested" / "record.sqlite3"
    c = mri.connect(path)
    try:
        assert path.is_file()
        assert c.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert c.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
        names = {r[0] for r in c.execute("SELECT name FROM sqlite_master")}
        assert {"record", "record_fts", "record_vec", "state"} <= names
    finally:
        c.close()


def test_connect_without_a_path_opens_the_record_path(tmp_path, monkeypatch):
    target = tmp_path / "from-env.sqlite3"
    monkeypatch.setenv("PALLI_MEMORY_REACH_PATH", str(target))
    c = mri.connect()
    try:
        assert target.is_file()
        assert c.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    finally:
        c.close()


def test_the_schema_is_the_plans_and_ensure_schema_runs_twice(conn):
    mri.ensure_schema(conn)
    mri.ensure_schema(conn)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(record)")]
    assert cols == ["id", "source", "source_ref", "room", "ts", "day", "speaker", "name", "text"]
    assert [r[1] for r in conn.execute("PRAGMA table_info(record_vec)")] == ["id", "vec"]
    assert [r[1] for r in conn.execute("PRAGMA table_info(state)")] == ["key", "value"]
    fts_sql = conn.execute(
        "SELECT sql FROM sqlite_master WHERE name = 'record_fts'").fetchone()[0]
    assert "fts5" in fts_sql and "porter unicode61" in fts_sql
    indexed = {
        tuple(r[2] for r in conn.execute(f"PRAGMA index_info({idx[1]})"))
        for idx in conn.execute("PRAGMA index_list(record)")
    }
    assert ("room", "ts") in indexed
    assert ("ts",) in indexed
    assert ("source_ref",) in indexed  # the UNIQUE constraint's own index


# --- rows and words ----------------------------------------------------------

def test_add_rows_is_idempotent_on_source_ref(conn):
    first = [_row("db:1", "we should paint the fence blue"),
             _row("db:2", "blue is a strong choice", speaker="palli", name="PaLLi")]
    assert mri.add_rows(conn, first, vectors=None) == 2
    again = first + [_row("db:3", "or green, Dana said", speaker="guest", name="Dana")]
    assert mri.add_rows(conn, again, vectors=None) == 1
    assert mri.add_rows(conn, again, vectors=None) == 0
    assert _count(conn, "record") == 3
    assert _count(conn, "record_fts") == 3
    assert [r[0] for r in conn.execute("SELECT source_ref FROM record ORDER BY id")] == [
        "db:1", "db:2", "db:3"]


def test_add_rows_stores_every_field(conn):
    row = _row("discord_9.jsonl:4", "hello there", room="discord:9", ts=1_759_999_000.5,
               speaker="guest", name="Sam", source="room_file")
    assert mri.add_rows(conn, [row], vectors=None) == 1
    got = conn.execute(
        "SELECT source, source_ref, room, ts, day, speaker, name, text FROM record").fetchone()
    assert got == ("room_file", "discord_9.jsonl:4", "discord:9", 1_759_999_000.5,
                   _day(1_759_999_000.5), "guest", "Sam", "hello there")


def test_a_row_names_one_of_three_speakers():
    for speaker in ("owner", "palli", "guest"):
        _row("db:1", "x", speaker=speaker)
    with pytest.raises(ValueError):
        _row("db:1", "x", speaker="someone")


def test_the_fts_rowid_is_the_record_id(conn):
    mri.add_rows(conn, [_row("db:1", "the ferry leaves at noon")], vectors=None)
    # A duplicate first in the batch: the ignored row must not shift the ids.
    mri.add_rows(conn, [
        _row("db:1", "the ferry leaves at noon"),
        _row("db:2", "Jo baked bread on Sunday morning", speaker="guest", name="Jo"),
        _row("db:3", "the bread recipe is in the blue notebook", speaker="palli", name="PaLLi"),
    ], vectors=None)
    hits = conn.execute(
        "SELECT rowid FROM record_fts WHERE record_fts MATCH ?",
        ('"bread on sunday"',)).fetchall()
    assert len(hits) == 1
    rid = hits[0][0]
    assert conn.execute("SELECT source_ref FROM record WHERE id = ?", (rid,)).fetchone()[0] == "db:2"
    # Every FTS row sits on its record row, with the same words.
    joined = conn.execute(
        "SELECT COUNT(*) FROM record r JOIN record_fts f ON f.rowid = r.id "
        "WHERE f.text = r.text").fetchone()[0]
    assert joined == _count(conn, "record") == _count(conn, "record_fts") == 3
    # Porter stemming: "baking" finds "baked".
    stemmed = conn.execute(
        "SELECT rowid FROM record_fts WHERE record_fts MATCH 'baking'").fetchall()
    assert [r[0] for r in stemmed] == [rid]


def test_the_fts_rowid_follows_an_explicit_high_id(conn):
    # Ids and FTS rowids both number 1..n by themselves, so only a row whose
    # id is set out of step shows the trigger keys the words on the id.
    with conn:
        conn.execute(
            "INSERT INTO record(id, source, source_ref, room, ts, day, speaker, name, text)"
            " VALUES (100, 'db', 'db:100', 'discord:1', 0, '2026-10-13', 'guest', 'Alex',"
            " 'the kayak is blue')")
    assert conn.execute(
        "SELECT rowid FROM record_fts WHERE record_fts MATCH 'kayak'").fetchall() == [(100,)]


def test_deleting_a_record_row_takes_its_words_and_vector_with_it(conn):
    mri.add_rows(conn, [_row("discord_9.jsonl:1", "first line"),
                        _row("discord_9.jsonl:2", "second line"),
                        _row("db:7", "kept line")],
                 vectors={"discord_9.jsonl:1": mri.encode_vector([1.0, 0.0])})
    with conn:
        conn.execute("DELETE FROM record WHERE source_ref LIKE ?", ("discord_9.jsonl:%",))
    assert _count(conn, "record") == 1
    assert _count(conn, "record_fts") == 1
    assert _count(conn, "record_vec") == 0
    assert conn.execute(
        "SELECT COUNT(*) FROM record_fts WHERE record_fts MATCH 'first'").fetchone()[0] == 0


# --- meaning -----------------------------------------------------------------

def _decode(blob: bytes) -> list[float]:
    out = array("f")
    out.frombytes(blob)
    return out.tolist()


def _f32(values: list[float]) -> list[float]:
    return [struct.unpack("f", struct.pack("f", v))[0] for v in values]


def test_encode_vector_is_float32_bytes():
    vec = [0.1, -2.5, 3.0, 1e-3]
    blob = mri.encode_vector(vec)
    assert isinstance(blob, bytes)
    assert len(blob) == 4 * len(vec)
    assert blob == array("f", vec).tobytes()
    assert _decode(blob) == _f32(vec)


def test_vectors_round_trip_as_float32(conn):
    a, b = [0.25, -0.5, 0.125], [0.1, 0.2, 0.3]
    mri.add_rows(conn, [_row("db:1", "one"), _row("db:2", "two")],
                 vectors={"db:1": mri.encode_vector(a)})
    id1 = conn.execute("SELECT id FROM record WHERE source_ref = 'db:1'").fetchone()[0]
    id2 = conn.execute("SELECT id FROM record WHERE source_ref = 'db:2'").fetchone()[0]
    mri.put_vectors(conn, [(id2, mri.encode_vector(b))])
    stored = dict(conn.execute("SELECT id, vec FROM record_vec"))
    assert _decode(stored[id1]) == _f32(a)
    assert _decode(stored[id2]) == _f32(b)


def test_a_duplicate_row_does_not_replace_its_vector(conn):
    mri.add_rows(conn, [_row("db:1", "one")], vectors={"db:1": mri.encode_vector([1.0])})
    assert mri.add_rows(conn, [_row("db:1", "one")],
                        vectors={"db:1": mri.encode_vector([9.0])}) == 0
    assert _decode(conn.execute("SELECT vec FROM record_vec").fetchone()[0]) == [1.0]


def test_pending_vectors_lists_only_rows_without_a_vector(conn):
    mri.add_rows(conn, [_row("db:1", "one"), _row("db:2", "two"),
                        _row("db:3", "three"), _row("db:4", "four")],
                 vectors={"db:2": mri.encode_vector([0.5])})
    ids = {ref: i for i, ref in conn.execute("SELECT id, source_ref FROM record")}
    assert mri.pending_vectors(conn, 10) == [
        (ids["db:1"], "one"), (ids["db:3"], "three"), (ids["db:4"], "four")]
    assert mri.pending_vectors(conn, 2) == [(ids["db:1"], "one"), (ids["db:3"], "three")]
    mri.put_vectors(conn, [(ids["db:1"], mri.encode_vector([0.1])),
                           (ids["db:3"], mri.encode_vector([0.3]))])
    assert mri.pending_vectors(conn, 10) == [(ids["db:4"], "four")]
    mri.put_vectors(conn, [(ids["db:4"], mri.encode_vector([0.4]))])
    assert mri.pending_vectors(conn, 10) == []
    # Writing a vector again replaces it, never a second row.
    mri.put_vectors(conn, [(ids["db:4"], mri.encode_vector([0.8]))])
    assert _count(conn, "record_vec") == 4
    assert _decode(conn.execute(
        "SELECT vec FROM record_vec WHERE id = ?", (ids["db:4"],)).fetchone()[0]) == _f32([0.8])


def test_put_vectors_skips_an_id_the_record_no_longer_holds(conn):
    """A vector computed for a row that was deleted meanwhile (a shrunk room
    file re-read) is dropped: ids can be reused, and a stray vector would
    sit on the next row to take that id."""
    mri.add_rows(conn, [_row("discord_9.jsonl:1", "gone soon")], vectors=None)
    [(rid, _text)] = mri.pending_vectors(conn, 10)
    with conn:
        conn.execute("DELETE FROM record WHERE id = ?", (rid,))
    mri.put_vectors(conn, [(rid, mri.encode_vector([0.5]))])
    assert _count(conn, "record_vec") == 0
    mri.add_rows(conn, [_row("discord_9.jsonl:1", "back again")], vectors=None)
    assert [t for _i, t in mri.pending_vectors(conn, 10)] == ["back again"]


# --- state and status --------------------------------------------------------

def test_state_round_trips(conn):
    assert mri.get_state(conn, "db_last_id") is None
    mri.set_state(conn, "db_last_id", "41")
    assert mri.get_state(conn, "db_last_id") == "41"
    mri.set_state(conn, "db_last_id", "7075")
    assert mri.get_state(conn, "db_last_id") == "7075"
    mri.set_state(conn, "file:discord_2.jsonl", '{"size": 10, "ino": 1, "offset": 10, "lineno": 2}')
    assert mri.get_state(conn, "file:discord_2.jsonl").startswith('{"size"')
    assert _count(conn, "state") == 2


def test_state_survives_a_reconnect(tmp_path):
    path = tmp_path / "record.sqlite3"
    c = mri.connect(path)
    mri.set_state(c, "db_last_id", "12")
    mri.add_rows(c, [_row("db:1", "kept")], vectors=None)
    c.close()
    c = mri.connect(path)
    try:
        assert mri.get_state(c, "db_last_id") == "12"
        assert _count(c, "record") == 1
    finally:
        c.close()


def test_status_on_an_empty_record(conn):
    assert mri.status(conn) == {"rows": 0, "rooms": 0, "first_day": None,
                                "last_day": None, "vectors_missing": 0}


def test_status_counts(conn):
    early, late = 1_745_700_000.0, 1_760_300_000.0  # late April, mid October
    mri.add_rows(conn, [
        _row("db:1", "first", room="discord:1", ts=late),
        _row("db:2", "second", room="discord:2", ts=early),
        _row("db:3", "third", room="discord:2", ts=late, speaker="palli", name="PaLLi"),
        _row("telegram_-100.jsonl:1", "fourth", room="telegram:-100", ts=late,
             speaker="guest", name="Alex", source="room_file"),
    ], vectors={"db:1": mri.encode_vector([1.0])})
    assert mri.status(conn) == {"rows": 4, "rooms": 3, "first_day": _day(early),
                                "last_day": _day(late), "vectors_missing": 3}
