"""A connection cancelled while it opens must never outlive its task.

History (2026-09-30): GitHub CI printed "6940 passed" and then hung until its
45-minute limit, every push. The process could not exit because one aiosqlite
worker thread (non-daemon) was still alive. It came from the memory search's
background embedding backfill (``core/memory_service.py`` ``_run_backfill``):
a test's event loop closed while that task was opening a connection, the task
was cancelled after ``aiosqlite.connect`` had started the thread but before
``configure_sqlite_connection`` finished, and nothing closed the connection.
Its cleanup was left to the garbage collector, which may not run before the
interpreter waits on non-daemon threads at exit. The fix closes the connection
in ``_SQLiteConnection.create`` itself when opening fails or is cancelled.
"""
from __future__ import annotations

import asyncio
import gc
import threading

import pytest

from core import database


def _worker_threads() -> set[int]:
    return {
        t.ident for t in threading.enumerate()
        if "_connection_worker_thread" in t.name and t.is_alive()
    }


async def _wait_until_gone(idents: set[int], *, seconds: float = 2.0) -> set[int]:
    deadline = asyncio.get_running_loop().time() + seconds
    while True:
        alive = _worker_threads() & idents
        if not alive or asyncio.get_running_loop().time() > deadline:
            return alive
        await asyncio.sleep(0.02)


@pytest.mark.asyncio
async def test_a_connection_cancelled_while_opening_stops_its_thread(tmp_path, monkeypatch):
    """The live case: the task is cancelled mid-setup and then kept (as the
    memory service keeps its backfill tasks) without anyone reading its
    result, so the stored CancelledError's traceback keeps the half-open
    connection referenced. The thread must stop anyway."""
    brain = database.PalliBrain(db_path=str(tmp_path / "cancel.db"))
    configuring = asyncio.Event()

    async def slow_configure(raw):
        configuring.set()
        await asyncio.sleep(30)

    monkeypatch.setattr(database, "configure_sqlite_connection", slow_configure)
    before = _worker_threads()
    kept_tasks = {}  # like MemoryWorkspaceService._backfill_tasks
    gc.disable()  # the fix must not depend on the garbage collector
    try:
        task = asyncio.create_task(database._SQLiteConnection.create(brain))
        kept_tasks[7] = task
        await configuring.wait()
        new = _worker_threads() - before
        assert new, "the connection's worker thread should be running by now"
        task.cancel()
        await asyncio.wait({task})  # finished, but its exception never read
        assert task.cancelled()
        assert await _wait_until_gone(new) == set()
    finally:
        gc.enable()


@pytest.mark.asyncio
async def test_a_connection_whose_setup_fails_stops_its_thread(tmp_path, monkeypatch):
    brain = database.PalliBrain(db_path=str(tmp_path / "fail.db"))

    async def broken_configure(raw):
        raise RuntimeError("pragma failed")

    monkeypatch.setattr(database, "configure_sqlite_connection", broken_configure)
    before = _worker_threads()
    kept_tasks = {}
    gc.disable()
    try:
        task = asyncio.create_task(database._SQLiteConnection.create(brain))
        kept_tasks[7] = task
        await asyncio.wait({task})  # failed, its exception never read
        assert isinstance(task.exception(), RuntimeError)
        assert await _wait_until_gone(_worker_threads() - before) == set()
    finally:
        gc.enable()


@pytest.mark.asyncio
async def test_a_normal_open_still_works_and_closes(tmp_path):
    brain = database.PalliBrain(db_path=str(tmp_path / "ok.db"))
    before = _worker_threads()
    conn = await database._SQLiteConnection.create(brain)
    try:
        rows = await conn.fetch("SELECT 1 AS one")
        assert rows[0]["one"] == 1
    finally:
        await conn.close()
    assert await _wait_until_gone(_worker_threads() - before) == set()


@pytest.mark.parametrize("phase", ["connect", "configure"])
def test_a_connection_cancelled_mid_open_is_drained_before_its_loop_closes(tmp_path, monkeypatch, phase):
    """Batch 8 Task 11 Extra-1: CI's "Exception in thread … RuntimeError: Event
    loop is closed", reproduced. A fire-and-forget task (the inner-memory
    index hook a workbench session fires) is still inside aiosqlite.connect
    when its loop ends: asyncio.run cancels it, aiosqlite queues a stop that
    nobody waits for, the loop closes, and the worker then delivers the
    finished connect to a closed loop and dies with the error — blamed on
    whichever test is running. The open now waits for its worker while the
    loop is still alive, so nothing is left to deliver late."""
    import sqlite3
    import time

    real_connect = sqlite3.connect
    connecting = threading.Event()

    def slow_connect(*args, **kwargs):
        connecting.set()
        time.sleep(0.3)  # a slow runner's open, made deterministic
        return real_connect(*args, **kwargs)

    async def slow_configure(raw):
        # The backfill case (10-01 tracker run): cancelled while the worker
        # runs a configure PRAGMA — the stop() path, which did not wait either.
        connecting.set()
        await raw._execute(time.sleep, 0.3)

    if phase == "connect":
        monkeypatch.setattr(sqlite3, "connect", slow_connect)
    else:
        monkeypatch.setattr(database, "configure_sqlite_connection", slow_configure)
    brain = database.PalliBrain(db_path=str(tmp_path / "late.db"))
    errors: list[BaseException] = []
    hook = threading.excepthook
    threading.excepthook = lambda args: errors.append(args.exc_value)
    before = _worker_threads()
    try:
        async def a_session_that_fires_and_forgets():
            asyncio.get_running_loop().create_task(database._SQLiteConnection.create(brain))
            while not connecting.is_set():
                await asyncio.sleep(0.005)
            # returns with the open still in flight, like run_session_once

        asyncio.run(a_session_that_fires_and_forgets())
        left_running = _worker_threads() - before
        for t in threading.enumerate():
            if t.ident in left_running:
                t.join(2)
    finally:
        threading.excepthook = hook

    assert left_running == set(), "the worker outlived its loop"
    assert errors == [], errors
