"""`palli_ctl threads retired [--grep TEXT]` and `palli_ctl threads unretire
<id>` (Batch 5, task 8, rulings R14/R50). The nightly retirement dismisses
threads never surfaced past memory.thread_archive_days, and the arbiter's
backstop dismisses old ones; these are the owner's two levers: see what
was set down (with the id each is known by) and put one back on the board.
Against the tmp test database under a throwaway owner id; never the
owner's rows."""
from __future__ import annotations

import asyncio
import importlib.util
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
OWNER = 827_364
DAY = 86400.0


def _load_module():
    module_path = REPO_ROOT / "scripts" / "palli_ctl.py"
    spec = importlib.util.spec_from_file_location("palli_ctl_threads_test_module", module_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def board():
    """Three threads for a throwaway owner: one retired by the nightly
    retirement, one dismissed by the arbiter's backstop, one active."""
    from core.database import brain

    asyncio.run(brain.initialize())

    def _purge(svc):
        svc._conn.execute("DELETE FROM open_threads WHERE owner_user_id = ?", (OWNER,))

    brain._run_open_threads_sync(_purge)

    def _seed(svc):
        ids = {}
        for name, age, surfaced in (("retired", 60, False), ("backstop", 50, True), ("active", 3, False)):
            tid = svc.add_thread(
                owner_user_id=OWNER, topic_summary=f"the {name} thread", why_it_matters="w",
                kind="personal", urgency="when-the-moment-fits",
            )
            then = time.time() - age * DAY
            svc._conn.execute("UPDATE open_threads SET created_at = ?, updated_at = ? WHERE id = ?", (then, then, tid))
            if surfaced:
                svc._conn.execute("UPDATE open_threads SET last_surfaced_at = ? WHERE id = ?", (then + DAY, tid))
            ids[name] = tid
        assert svc.retire_unsurfaced(owner_user_id=OWNER, older_than_days=45, now=time.time()) == 1
        expired = svc.expire_stale(owner_user_id=OWNER, now=time.time(), followup_ttl_days=7, thread_ttl_days=45)
        assert [e[0] for e in expired] == [ids["backstop"]]
        # The listing ages a thread the way the two age rules do — from its
        # last touch, MAX(created_at, COALESCE(updated_at, created_at)) — not
        # from its birth (fix wave): born 60 days ago, set down 10 days ago
        # reads 10d; the backstop row was touched just now and reads 0d.
        svc._conn.execute("UPDATE open_threads SET updated_at = ? WHERE id = ?", (time.time() - 10 * DAY, ids["retired"]))
        return ids

    ids = brain._run_open_threads_sync(_seed)
    try:
        yield ids
    finally:
        brain._run_open_threads_sync(_purge)


def _run(module, argv):
    args = module.build_parser().parse_args(["threads", *argv])
    lines: list[str] = []
    code = module.run_threads_command(args, owner_id=OWNER, out=lines.append)
    return code, lines


def test_parser_knows_the_threads_group():
    module = _load_module()
    args = module.build_parser().parse_args(["threads", "retired", "--grep", "boat"])
    assert args.command == "threads" and args.threads_action == "retired" and args.grep == "boat"
    args = module.build_parser().parse_args(["threads", "retired"])
    assert args.grep is None
    args = module.build_parser().parse_args(["threads", "unretire", "abc123"])
    assert args.threads_action == "unretire" and args.thread_id == "abc123"


def test_retired_lists_every_dismissed_thread_with_id_kind_age_reason_and_topic(board):
    module = _load_module()
    code, lines = _run(module, ["retired"])
    assert code == 0
    assert len(lines) == 2
    by_id = {ln.split("  ")[0]: ln for ln in lines}
    assert set(by_id) == {board["retired"], board["backstop"]}
    retired = by_id[board["retired"]].split("  ")
    assert retired[1] == "topic"
    assert retired[2] == "10d"   # last touch (updated_at), the age rules' own reading — not 60d from birth
    assert retired[3] == "retired_unsurfaced (never surfaced, held 60d > 45d)"
    assert retired[4] == "the retired thread"
    backstop = by_id[board["backstop"]].split("  ")
    assert backstop[2] == "0d"
    assert backstop[3].startswith("backstop_ttl") and backstop[4] == "the backstop thread"
    assert not any("the active thread" in ln for ln in lines)


def test_retired_grep_filters_case_insensitively(board):
    module = _load_module()
    code, lines = _run(module, ["retired", "--grep", "BACKSTOP"])
    assert code == 0 and len(lines) == 1 and lines[0].startswith(board["backstop"])
    code, lines = _run(module, ["retired", "--grep", "nothing like this"])
    assert code == 0 and lines == ["No retired threads match 'nothing like this'."]


def test_retired_with_nothing_dismissed_says_so(board):
    from core.database import brain

    module = _load_module()
    for name in ("retired", "backstop"):
        assert asyncio.run(brain.unretire_open_thread(thread_id=board[name])) is True
    code, lines = _run(module, ["retired"])
    assert code == 0 and lines == ["No retired threads."]


def test_unretire_puts_the_thread_back_on_the_board(board):
    from core.database import brain

    module = _load_module()
    code, lines = _run(module, ["unretire", board["retired"]])
    assert code == 0
    assert lines == [f"Back on the board: [{board['retired'][:8]}] the retired thread"]
    active = asyncio.run(brain.list_active_open_threads(owner_user_id=OWNER))
    assert {t.id for t in active} == {board["retired"], board["active"]}
    back = next(t for t in active if t.id == board["retired"])
    assert back.resolution_reason is None and back.resolved_at is None
    # the listing no longer shows it
    code, listed = _run(module, ["retired"])
    assert code == 0 and [ln.split("  ")[0] for ln in listed] == [board["backstop"]]


def test_unretire_of_an_unknown_or_active_id_is_a_runtime_error(board):
    module = _load_module()
    for bad in ("no-such-thread", board["active"]):
        with pytest.raises(RuntimeError) as excinfo:
            _run(module, ["unretire", bad])
        assert bad in str(excinfo.value) and "threads retired" in str(excinfo.value)


def test_main_exits_1_on_an_unknown_id(board, monkeypatch, capsys):
    module = _load_module()
    monkeypatch.setattr(module, "_memory_owner_id", lambda: OWNER)
    logged: list[str] = []
    monkeypatch.setattr(module, "append_launcher_log", logged.append)
    assert module.main(["threads", "unretire", "no-such-thread"]) == 1
    assert "no-such-thread" in capsys.readouterr().err
    assert len(logged) == 1 and "no-such-thread" in logged[0]


def test_threads_without_an_action_is_a_runtime_error(board):
    module = _load_module()
    with pytest.raises(RuntimeError) as excinfo:
        _run(module, [])
    assert "threads retired" in str(excinfo.value) and "threads unretire" in str(excinfo.value)
