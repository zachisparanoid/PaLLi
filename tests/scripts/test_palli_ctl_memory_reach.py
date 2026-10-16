"""`palli_ctl memory reach-build [--rebuild]` and `palli_ctl memory reach-status`
(R15, T5; plan docs/2026-10-13-memory-that-reaches-plan.md): the owner's two
commands over the searchable record of every message. The build is handed in
(a fake, or the real ``memory_reach_index.build`` on tmp paths with a fake
embedder); the status reads a tmp record through ``PALLI_MEMORY_REACH_PATH``.
Never the live record, never the network."""
from __future__ import annotations

import argparse
import functools
import importlib.util
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from ai import memory_reach_index as mri
from core.agent.speakers import make_speaker
from core.agent.session import AgentMessage, TextBlock, _msg_to_jsonl
from core.config import config
from core.database import PalliBrain
from core.owner_gate import guest_content
from core.sqlite_schema_catalog import ensure_catalog_schema_sync

REPO_ROOT = Path(__file__).resolve().parents[2]
CTL = REPO_ROOT / "scripts" / "palli_ctl.py"

ROBIN_APP = 7
DANA = 2222
T0 = 1_760_000_000.0


def _day(ts: float) -> str:
    return datetime.fromtimestamp(ts).astimezone().strftime("%Y-%m-%d")


def _load_module():
    spec = importlib.util.spec_from_file_location("palli_ctl_memory_reach_test_module", CTL)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def ctl():
    return _load_module()


@pytest.fixture
def record(tmp_path, monkeypatch) -> Path:
    path = tmp_path / "reach" / "memory_reach.sqlite3"
    monkeypatch.setenv("PALLI_MEMORY_REACH_PATH", str(path))
    return path


@pytest.fixture(autouse=True)
def _a_known_model(monkeypatch):
    monkeypatch.setattr(config, "MEMORY_LOCAL_EMBEDDINGS_MODEL", "model-now", raising=False)
    monkeypatch.setattr(config, "MEMORY_LOCAL_EMBEDDINGS_ENABLED", True, raising=False)


class FakeBuild:
    """Stands in for ``memory_reach_index.build``: says one progress line and
    hands back the counts it was given."""

    def __init__(self, result: dict | None = None, error: BaseException | None = None):
        self.calls: list[dict] = []
        self.error = error
        self.result = result or {
            "rows": 7075, "rooms": 12, "first_day": "2026-04-26", "last_day": "2026-10-13",
            "vectors_missing": 0, "db_rows": 7040, "file_rows": 35, "skipped_lines": 0,
            "embedded": 7075, "seconds": 95.2, "embed_model": "model-now",
        }

    async def __call__(self, *, rebuild: bool, progress) -> dict:
        self.calls.append({"rebuild": rebuild, "progress": progress})
        progress("database: read to id 7075, 7040 lines added")
        if self.error is not None:
            raise self.error
        return dict(self.result)


def _build(ctl, *, rebuild=False, builder, running=False):
    lines: list[str] = []
    code = ctl.run_memory_command(
        argparse.Namespace(memory_action="reach-build", rebuild=rebuild),
        out=lines.append, builder=builder, palli_running=lambda: running)
    return code, lines


def _status(ctl):
    lines: list[str] = []
    code = ctl.run_memory_command(argparse.Namespace(memory_action="reach-status"),
                                  out=lines.append)
    return code, lines


# --- the parser --------------------------------------------------------------

def test_parser_knows_reach_build_and_reach_status(ctl):
    args = ctl.build_parser().parse_args(["memory", "reach-build"])
    assert args.memory_action == "reach-build" and args.rebuild is False
    args = ctl.build_parser().parse_args(["memory", "reach-build", "--rebuild"])
    assert args.rebuild is True
    args = ctl.build_parser().parse_args(["memory", "reach-status"])
    assert args.memory_action == "reach-status"


def test_memory_without_an_action_names_the_reach_commands_too(ctl):
    with pytest.raises(RuntimeError) as excinfo:
        ctl.run_memory_command(argparse.Namespace(memory_action=None), out=lambda _l: None)
    assert "memory archived" in str(excinfo.value)
    assert "memory reach-build" in str(excinfo.value)


# --- reach-build -------------------------------------------------------------

def test_reach_build_calls_the_builder_and_prints_its_counts(ctl):
    fake = FakeBuild()

    code, lines = _build(ctl, builder=fake)

    assert code == 0
    assert len(fake.calls) == 1 and fake.calls[0]["rebuild"] is False
    assert lines[0] == "database: read to id 7075, 7040 lines added"     # progress, as it comes
    assert ("rows=7075 rooms=12 first=2026-04-26 last=2026-10-13 vectors_missing=0 "
            "seconds=95.2") in lines
    assert any(line.startswith("meaning:") and "model-now" in line for line in lines)


def test_reach_build_hands_rebuild_through(ctl):
    fake = FakeBuild()

    code, _ = _build(ctl, rebuild=True, builder=fake)

    assert code == 0 and fake.calls[0]["rebuild"] is True


def test_a_rebuild_warns_first_while_palli_is_running(ctl):
    fake = FakeBuild()

    code, lines = _build(ctl, rebuild=True, builder=fake, running=True)

    assert code == 0
    assert "PaLLi is running" in lines[0] and "refused" in lines[0]
    assert lines[1] == "database: read to id 7075, 7040 lines added"     # then the build


def test_no_warning_while_palli_is_stopped_or_without_rebuild(ctl):
    _, stopped = _build(ctl, rebuild=True, builder=FakeBuild(), running=False)
    _, plain = _build(ctl, rebuild=False, builder=FakeBuild(), running=True)

    assert not any("PaLLi is running" in line for line in stopped + plain)


def test_the_running_check_never_stops_the_build(ctl, monkeypatch):
    """A launcher look that breaks reads as "not running": the rebuild's own
    refusal is the real guard."""
    def _broken(_name):
        raise OSError("no launcher state")

    monkeypatch.setattr(ctl, "inspect_launcher_service", _broken)
    fake = FakeBuild()
    lines: list[str] = []

    code = ctl.run_memory_command(argparse.Namespace(memory_action="reach-build", rebuild=True),
                                  out=lines.append, builder=fake)

    assert code == 0 and len(fake.calls) == 1


@pytest.mark.parametrize("ownership,running", [
    ("launcher", True), ("self", True), ("none", False), ("stale", False), ("external", False),
])
def test_palli_is_running_by_the_launchers_own_look(ctl, monkeypatch, ownership, running):
    monkeypatch.setattr(ctl, "inspect_launcher_service",
                        lambda name: {"service": name, "ownership": ownership})
    assert ctl._palli_running() is running


def test_an_embedding_error_is_printed_plainly_and_exits_non_zero(ctl):
    fake = FakeBuild(result={"rows": 100, "rooms": 2, "first_day": "2026-04-26",
                             "last_day": "2026-10-13", "vectors_missing": 36, "db_rows": 100,
                             "file_rows": 0, "skipped_lines": 0, "embedded": 64,
                             "seconds": 130.4, "embed_model": "model-now",
                             "embed_error": "TimeoutError"})

    code, lines = _build(ctl, builder=fake)

    assert code == 1
    assert ("rows=100 rooms=2 first=2026-04-26 last=2026-10-13 vectors_missing=36 "
            "seconds=130.4") in lines
    stopped = [line for line in lines if "TimeoutError" in line]
    assert len(stopped) == 1 and "36" in stopped[0] and "reach-build" in stopped[0]


def test_words_only_when_local_embeddings_are_off(ctl, monkeypatch):
    monkeypatch.setattr(config, "MEMORY_LOCAL_EMBEDDINGS_ENABLED", False, raising=False)
    fake = FakeBuild(result={"rows": 3, "rooms": 1, "first_day": "2026-04-26",
                             "last_day": "2026-04-27", "vectors_missing": 3, "db_rows": 3,
                             "file_rows": 0, "skipped_lines": 0, "embedded": 0,
                             "seconds": 0.4, "embed_model": None})

    code, lines = _build(ctl, builder=fake)

    assert code == 0
    assert any(line.startswith("meaning:") and "off" in line for line in lines)


def test_a_refused_rebuild_is_one_plain_line(ctl):
    fake = FakeBuild(error=mri.RecordInUse("memory reach: the record is open elsewhere"))

    with pytest.raises(RuntimeError) as excinfo:
        _build(ctl, rebuild=True, builder=fake)

    message = str(excinfo.value)
    assert type(excinfo.value) is RuntimeError                # not the index's own error
    assert "in use" in message and "Stop PaLLi" in message and "\n" not in message


def test_main_prints_the_refusal_without_a_traceback_and_exits_1(ctl, monkeypatch, capsys):
    async def _refused(**_kwargs):
        raise mri.RecordInUse("memory reach: the record is open elsewhere")

    monkeypatch.setattr(mri, "build", _refused)
    monkeypatch.setattr(ctl, "_palli_running", lambda: False)
    logged: list[str] = []
    monkeypatch.setattr(ctl, "append_launcher_log", logged.append)    # never the real log

    assert ctl.main(["memory", "reach-build", "--rebuild"]) == 1

    captured = capsys.readouterr()
    assert "Traceback" not in captured.err + captured.out
    assert len(captured.err.strip().splitlines()) == 1 and "in use" in captured.err
    assert len(logged) == 1


# --- the real build, end to end through the command (tmp paths, fake embedder) ---

@pytest.fixture
def brain(tmp_path):
    db_path = tmp_path / "palli.sqlite3"
    conn = sqlite3.connect(str(db_path))
    conn.isolation_level = None
    try:
        ensure_catalog_schema_sync(conn)
    finally:
        conn.close()
    return PalliBrain(db_path=str(db_path))


@pytest.fixture
def sessions(tmp_path) -> Path:
    root = tmp_path / "agent_sessions"
    root.mkdir()
    return root


class FakeEmbed:
    """Instant, deterministic, no network."""

    async def __call__(self, texts: list[str]) -> list[list[float]]:
        return [[float(len(t)), 1.0, 0.5] for t in texts]


async def _owners() -> frozenset[int]:
    return frozenset({ROBIN_APP})


def _store(brain, rows: list[tuple[str, str, float]]) -> None:
    """(role, content, ts) rows of the owner's on the dashboard, the columns
    ``store_conversation`` writes."""
    conn = sqlite3.connect(str(brain.db_path))
    try:
        with conn:
            conn.executemany(
                "INSERT INTO conversations (user_id, platform, channel_id, role, username,"
                " content, timestamp) VALUES (?, 'dashboard', 0, ?, ?, ?, ?)",
                [(ROBIN_APP, role, "Me" if role == "assistant" else "Robin", text, ts)
                 for role, text, ts in rows])
    finally:
        conn.close()


def _guest_line(path: Path, text: str, ts: float) -> None:
    msg = AgentMessage(role="user", content=[TextBlock(text=guest_content("Dana", text))],
                       timestamp=datetime.fromtimestamp(ts, tz=timezone.utc),
                       speaker=make_speaker("guest", platform="discord", account_id=DANA,
                                            name="Dana", handle="dana"))
    with path.open("a", encoding="utf-8") as f:
        f.write(_msg_to_jsonl(msg) + "\n")


def test_reach_build_runs_the_real_build_and_reach_status_reads_it(
        ctl, brain, sessions, record, monkeypatch):
    monkeypatch.setattr(mri, "_owner_ids", _owners)
    _store(brain, [("user", "the cellar needs a dehumidifier", T0),
                   ("assistant", "a small one would do", T0 + 60)])
    _guest_line(sessions / "discord_1.jsonl", "hello from the server", T0 + 86400)
    builder = functools.partial(mri.build, brain=brain, sessions_root=sessions,
                                embed=FakeEmbed())

    code, lines = _build(ctl, builder=builder)

    assert code == 0
    summary = [line for line in lines if line.startswith("rows=")]
    assert len(summary) == 1
    assert summary[0].startswith(f"rows=3 rooms=2 first={_day(T0)} last={_day(T0 + 86400)} "
                                 "vectors_missing=0 seconds=")
    assert not any("hello from the server" in line or "cellar" in line for line in lines)

    code, lines = _status(ctl)

    assert code == 0
    assert f"rows=3 rooms=2 first={_day(T0)} last={_day(T0 + 86400)} vectors_missing=0" in lines
    assert any(line.startswith("meaning:") and "model-now" in line for line in lines)


def test_a_rebuild_refused_by_the_real_build_leaves_the_old_record(
        ctl, brain, sessions, record, monkeypatch):
    """PaLLi's own connection on the old record: the real ``_settle_old``
    refuses, the command says so in one line, the old record stands."""
    monkeypatch.setattr(mri, "_owner_ids", _owners)
    monkeypatch.setattr(mri, "_SETTLE_WAIT_S", 0.01)
    _store(brain, [("user", "a real line", T0)])
    held = mri.connect(record)
    mri.add_rows(held, [mri.Row(source="db", source_ref="db:999", room="dashboard:7", ts=T0,
                                day=_day(T0), speaker="owner", name="Robin",
                                text="the old line")], vectors=None)
    old_ino = record.stat().st_ino
    builder = functools.partial(mri.build, brain=brain, sessions_root=sessions,
                                embed=FakeEmbed())
    try:
        with pytest.raises(RuntimeError) as excinfo:
            _build(ctl, rebuild=True, builder=builder)
    finally:
        held.close()

    assert "in use" in str(excinfo.value)
    assert record.stat().st_ino == old_ino
    assert not record.with_name(record.name + ".building").exists()


# --- reach-status ------------------------------------------------------------

def test_reach_status_without_a_record_says_so_and_creates_nothing(ctl, record):
    code, lines = _status(ctl)

    assert code == 0
    assert any("hasn't been built yet" in line and "reach-build" in line for line in lines)
    assert not record.exists() and not record.parent.exists()


def _a_record(path: Path, *, model: str | None) -> None:
    conn = mri.connect(path)
    try:
        mri.add_rows(conn, [
            mri.Row(source="db", source_ref="db:1", room="discord:1", ts=T0, day=_day(T0),
                    speaker="owner", name="Robin", text="first line"),
            mri.Row(source="db", source_ref="db:2", room="dashboard:7", ts=T0 + 86400,
                    day=_day(T0 + 86400), speaker="palli", name="PaLLi", text="second line"),
        ], vectors={"db:1": mri.encode_vector([1.0, 0.0])})
        if model is not None:
            mri.set_state(conn, mri.EMBED_MODEL_KEY, model)
    finally:
        conn.close()


def _files(path: Path) -> dict[str, bytes]:
    return {p.name: p.read_bytes() for p in path.parent.iterdir()
            if p.name == path.name}


def test_reach_status_prints_the_records_status_and_writes_nothing(ctl, record):
    _a_record(record, model="model-now")
    before = _files(record)

    code, lines = _status(ctl)

    assert code == 0
    assert f"rows=2 rooms=2 first={_day(T0)} last={_day(T0 + 86400)} vectors_missing=1" in lines
    meaning = [line for line in lines if line.startswith("meaning:")]
    assert len(meaning) == 1 and "model-now" in meaning[0] and "again" not in meaning[0]
    assert _files(record) == before


def test_reach_status_flags_a_model_the_settings_no_longer_name(ctl, record):
    _a_record(record, model="model-before")

    _, lines = _status(ctl)

    meaning = [line for line in lines if line.startswith("meaning:")]
    assert len(meaning) == 1
    assert "model-before" in meaning[0] and "model-now" in meaning[0]
    assert "again" in meaning[0] and "reach-build" in meaning[0]


def test_reach_status_with_meaning_off_never_promises_a_re_embed(ctl, record, monkeypatch):
    """Off in the settings, no build makes meaning, so a stale model name is
    shown as held, not as something the next build fixes."""
    monkeypatch.setattr(config, "MEMORY_LOCAL_EMBEDDINGS_ENABLED", False, raising=False)
    _a_record(record, model="model-before")

    _, lines = _status(ctl)

    meaning = [line for line in lines if line.startswith("meaning:")]
    assert len(meaning) == 1
    assert "off" in meaning[0] and "model-before" in meaning[0] and "again" not in meaning[0]


def test_reach_status_with_no_meaning_yet(ctl, record):
    _a_record(record, model=None)

    _, lines = _status(ctl)

    meaning = [line for line in lines if line.startswith("meaning:")]
    assert len(meaning) == 1 and "none yet" in meaning[0]


def test_an_unreadable_record_is_one_plain_line(ctl, record):
    record.parent.mkdir(parents=True)
    record.write_bytes(b"this is not a database at all, just some bytes" * 40)

    with pytest.raises(RuntimeError) as excinfo:
        _status(ctl)

    assert "can't be read" in str(excinfo.value) and "--rebuild" in str(excinfo.value)


# --- the file ----------------------------------------------------------------

def test_palli_ctl_stays_crlf():
    data = CTL.read_bytes()
    assert b"\r\n" in data
    assert data.count(b"\n") == data.count(b"\r\n")          # no bare \n line
