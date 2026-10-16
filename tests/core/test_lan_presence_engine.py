"""Step 23 · LAN presence — the book, the sweeper, the quiet-check, the
engine (Task 5) and the listener (Task 6).

Every collaborator is injected: NO test here captures or scans anything
(the law). Flags are pinned by patching companion_settings where the module
looks it up; the book lives under tmp_path only.
"""
from __future__ import annotations

import asyncio
import json
import os
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from core import lan_presence as lp


def _flags(**kw):
    base = {"lan_presence": True, "lan_listener": True, "lan_absence_minutes": 15,
            "lan_sweep_interval_minutes": 480, "lan_devices": {}}
    base.update(kw)
    return SimpleNamespace(**base)


class Clock:
    def __init__(self, t: float = 10_000.0):
        self.t = t

    def __call__(self) -> float:
        return self.t


class Scripted:
    """A fake sweeper/prober: pops canned results; an Exception result raises."""

    def __init__(self, results):
        self.results = list(results)
        self.calls = []

    async def __call__(self, target):
        self.calls.append(target)
        r = self.results.pop(0) if self.results else {}
        if isinstance(r, Exception):
            raise r
        return r


def _engine(tmp_path, *, sweeper, prober=None, clock=None, listener=None):
    notices = []
    eng = lp.LanPresenceEngine(
        book_path=tmp_path / "lan_presence.json", subnet="192.0.2.0/24", iface="ens18",
        sweeper=sweeper, prober=prober or Scripted([]), listener=listener,
        notice=lambda kind, **f: notices.append((kind, f)), clock=clock or Clock(),
    )
    return eng, notices


PHONE = {"aa:bb:cc:dd:ee:03": {"ip": "192.0.2.3", "hostname": "pixel"}}
ROUTER = {"3c:52:a1:00:11:22": {"ip": "192.0.2.1", "hostname": "_gateway"}}


# ── the argv law: root ARP sweeps, one host for a quiet-check ────────────

def test_sweep_and_probe_argv_are_root_nmap_sn_only():
    assert lp.sweep_argv("192.0.2.0/24") == ["sudo", "-n", "nmap", "-sn", "192.0.2.0/24"]
    assert lp.probe_argv("192.0.2.3") == ["sudo", "-n", "nmap", "-sn", "192.0.2.3"]


# ── subnet detection (pure on the real formats) ──────────────────────────

ROUTE = "default via 192.0.2.1 dev ens18 proto static metric 100 \n"
ADDR = "2: ens18    inet 192.0.2.28/24 brd 192.0.2.255 scope global noprefixroute ens18\\       valid_lft forever preferred_lft forever\n"


def test_detect_iface_and_subnet_from_the_default_route(monkeypatch):
    monkeypatch.delenv("LAN_PRESENCE_IFACE", raising=False)
    monkeypatch.delenv("LAN_PRESENCE_SUBNET", raising=False)
    assert lp.detect_iface_and_subnet(route_text=ROUTE, addr_text=ADDR) == ("ens18", "192.0.2.0/24")


def test_env_overrides_win_without_looking(monkeypatch):
    monkeypatch.setenv("LAN_PRESENCE_IFACE", "eth9")
    monkeypatch.setenv("LAN_PRESENCE_SUBNET", "10.0.0.0/24")
    assert lp.detect_iface_and_subnet(route_text="", addr_text="") == ("eth9", "10.0.0.0/24")


def test_detect_degrades_to_none_on_garbage(monkeypatch):
    monkeypatch.delenv("LAN_PRESENCE_IFACE", raising=False)
    monkeypatch.delenv("LAN_PRESENCE_SUBNET", raising=False)
    assert lp.detect_iface_and_subnet(route_text="garbage", addr_text="") == (None, None)


# ── the sweep ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_boot_sweep_fills_the_book_silently_and_persists(tmp_path):
    with patch.object(lp, "companion_settings", lambda: _flags()):
        eng, notices = _engine(tmp_path, sweeper=Scripted([{**PHONE, **ROUTER}]))
        edges = await eng.tick_sweep()
    assert edges == [] and notices == []                       # first sightings: silent
    on_disk = json.loads((tmp_path / "lan_presence.json").read_text())
    assert set(on_disk) == {"aa:bb:cc:dd:ee:03", "3c:52:a1:00:11:22"}
    assert on_disk["aa:bb:cc:dd:ee:03"]["online"] is True
    assert on_disk["aa:bb:cc:dd:ee:03"]["source"] == "sweep"
    assert lp.load_book(tmp_path / "lan_presence.json") == on_disk


@pytest.mark.asyncio
async def test_a_sweep_missing_a_device_past_the_threshold_notices_left_once(tmp_path):
    clock = Clock()
    with patch.object(lp, "companion_settings", lambda: _flags()):
        eng, notices = _engine(tmp_path, sweeper=Scripted([{**PHONE, **ROUTER}, dict(ROUTER), dict(ROUTER)]), clock=clock)
        await eng.tick_sweep()
        clock.t += 5 * 60
        await eng.tick_sweep()                                  # 5 min: below the threshold
        assert notices == []
        clock.t += 20 * 60
        await eng.tick_sweep()                                  # 25 min silent: left
    assert [(k, f["device"], f["mac"]) for k, f in notices] == [("device_left", "pixel", "aa:bb:cc:dd:ee:03")]
    assert eng.book["aa:bb:cc:dd:ee:03"]["online"] is False


@pytest.mark.asyncio
async def test_sweeper_failure_leaves_the_book_intact_and_says_nothing(tmp_path):
    clock = Clock()
    with patch.object(lp, "companion_settings", lambda: _flags()):
        eng, notices = _engine(tmp_path, sweeper=Scripted([dict(PHONE), RuntimeError("nmap died")]), clock=clock)
        await eng.tick_sweep()
        before = (tmp_path / "lan_presence.json").read_text()
        clock.t += 3600
        edges = await eng.tick_sweep()                          # raises inside → swallowed
    assert edges == [] and notices == []
    assert (tmp_path / "lan_presence.json").read_text() == before
    assert eng.book["aa:bb:cc:dd:ee:03"]["online"] is True      # a failed sweep is not evidence


@pytest.mark.asyncio
async def test_a_sweep_that_observed_nothing_is_not_an_observation(tmp_path):
    """run_sweep returns ({}, False) when it could not look at all (no nmap,
    sudo refused, the test interlock). That is NOT "the house is empty" and
    NOT a fresh look: it must leave the book, the file and the freshness
    clock exactly as they were — otherwise render_line would drop its honest
    "as of ..." tail on the strength of a sweep that never happened."""
    clock = Clock()
    with patch.object(lp, "companion_settings", lambda: _flags()):
        eng, notices = _engine(tmp_path, sweeper=Scripted([dict(PHONE), ({}, False)]), clock=clock)
        await eng.tick_sweep()
        before_file = (tmp_path / "lan_presence.json").read_text()
        before_obs = eng.last_observation_at
        clock.t += 3600
        edges = await eng.tick_sweep()
    assert edges == [] and notices == []
    assert (tmp_path / "lan_presence.json").read_text() == before_file
    assert eng.last_observation_at == before_obs          # nothing was observed
    assert eng.book["aa:bb:cc:dd:ee:03"]["online"] is True


@pytest.mark.asyncio
async def test_flag_off_never_sweeps_and_never_creates_the_file(tmp_path):
    sweeper = Scripted([dict(PHONE)])
    with patch.object(lp, "companion_settings", lambda: _flags(lan_presence=False)):
        eng, notices = _engine(tmp_path, sweeper=sweeper)
        edges = await eng.tick_sweep()
        await eng.tick_quiet_checks()
    assert edges == [] and notices == [] and sweeper.calls == []
    assert not (tmp_path / "lan_presence.json").exists()


# ── the quiet-check ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_quiet_check_answered_refreshes_and_says_nothing(tmp_path):
    clock = Clock()
    prober = Scripted([True])
    with patch.object(lp, "companion_settings", lambda: _flags()):
        eng, notices = _engine(tmp_path, sweeper=Scripted([dict(PHONE)]), prober=prober, clock=clock)
        await eng.tick_sweep()
        clock.t += 16 * 60                                      # silent past the threshold
        edges = await eng.tick_quiet_checks()
    assert prober.calls == ["192.0.2.3"] and edges == [] and notices == []
    assert eng.book["aa:bb:cc:dd:ee:03"]["last_seen"] == clock.t
    assert eng.book["aa:bb:cc:dd:ee:03"]["online"] is True


@pytest.mark.asyncio
async def test_quiet_check_missed_notices_left_and_is_not_asked_again(tmp_path):
    clock = Clock()
    prober = Scripted([False, True, True])
    with patch.object(lp, "companion_settings", lambda: _flags()):
        eng, notices = _engine(tmp_path, sweeper=Scripted([dict(PHONE)]), prober=prober, clock=clock)
        await eng.tick_sweep()
        clock.t += 16 * 60
        await eng.tick_quiet_checks()                           # one ask, no answer → left
        clock.t += 60
        await eng.tick_quiet_checks()                           # offline devices are not asked
    assert prober.calls == ["192.0.2.3"]
    assert [(k, f["device"]) for k, f in notices] == [("device_left", "pixel")]
    assert eng.book["aa:bb:cc:dd:ee:03"]["online"] is False


@pytest.mark.asyncio
async def test_quiet_check_skips_devices_below_threshold_or_without_an_ip(tmp_path):
    clock = Clock()
    prober = Scripted([True, True])
    with patch.object(lp, "companion_settings", lambda: _flags()):
        eng, notices = _engine(tmp_path, sweeper=Scripted([{**PHONE, "ee:ff:00:00:00:01": {"ip": ""}}]), prober=prober, clock=clock)
        await eng.tick_sweep()
        clock.t += 5 * 60
        await eng.tick_quiet_checks()                           # nobody silent long enough
        clock.t += 11 * 60
        await eng.tick_quiet_checks()                           # the phone yes; the ip-less device cannot be asked
    assert prober.calls == ["192.0.2.3"] and notices == []


# ── labels come from the yaml, at the edge ───────────────────────────────

@pytest.mark.asyncio
async def test_edge_names_the_device_by_the_owners_label(tmp_path):
    clock = Clock()
    flags = _flags(lan_devices={"AA:BB:CC:DD:EE:03": "Sam's phone"})
    with patch.object(lp, "companion_settings", lambda: flags):
        eng, notices = _engine(tmp_path, sweeper=Scripted([dict(PHONE), {}]), clock=clock)
        await eng.tick_sweep()
        clock.t += 30 * 60
        await eng.tick_sweep()
    assert [(k, f["device"]) for k, f in notices] == [("device_left", "Sam's phone")]


# ── the boot wiring: started and stopped with the other engines ──────────

def test_main_starts_and_stops_the_engine_beside_the_others():
    src = open("core/main.py", encoding="utf-8").read()
    assert "lan_presence_engine.start()" in src and "lan_presence_engine.stop()" in src
    assert src.index("daydream_engine.start()") < src.index("lan_presence_engine.start()")


@pytest.mark.asyncio
async def test_start_and_stop_are_clean_when_the_flag_is_off(tmp_path):
    with patch.object(lp, "companion_settings", lambda: _flags(lan_presence=False)):
        eng, _ = _engine(tmp_path, sweeper=Scripted([]))
        await eng.start()
        await asyncio.sleep(0)
        await eng.stop()
    assert not (tmp_path / "lan_presence.json").exists()


# ── the book on disk: written when the house changes, twice a day regardless,
#    and NEVER on the loop thread. Found 2026-08-24 19:5x: the 60-second
#    on-loop rewrite waited 30–120 s on this VM's ext4 journal, and every one
#    of those seconds froze her replies, her voice and the Discord heartbeat.
#    His 20:57 call: "my network does not change anywhere near that often —
#    twice a day". ──────────────────────────────────────────────────────────

import threading

from tests.core.test_lan_presence import ARP_REQ, ARP_REPLY


class RecordingSaver:
    """Stands in for save_book: counts writes, keeps every snapshot, and can
    hold each write open until released (a disk that has gone away).
    Construct it BEFORE patching save_book — it delegates to the real one."""

    def __init__(self, *, gate=None, write_through=True):
        self.gate = gate
        self.write_through = write_through
        self.calls = 0
        self.snapshots = []
        self._real = lp.save_book

    def __call__(self, path, book):
        self.calls += 1
        if self.gate is not None:
            self.gate.wait(timeout=2.0)
        self.snapshots.append(json.loads(json.dumps(book)))
        if self.write_through:
            self._real(path, book)


@pytest.mark.asyncio
async def test_a_stalled_disk_never_blocks_the_loop(tmp_path):
    """The defect itself: the write must leave the loop free while the disk
    hangs. A heartbeat task keeps beating through a write that is held open."""
    gate = threading.Event()
    saver = RecordingSaver(gate=gate)
    beats = 0

    async def heartbeat():
        nonlocal beats
        while True:
            beats += 1
            await asyncio.sleep(0.01)

    with patch.object(lp, "companion_settings", lambda: _flags()), patch.object(lp, "save_book", saver):
        eng, _ = _engine(tmp_path, sweeper=Scripted([dict(PHONE)]))
        hb = asyncio.create_task(heartbeat())
        sweep = asyncio.create_task(eng.tick_sweep())          # a new device: this sweep must write
        await asyncio.sleep(0.25)                              # ...while the disk hangs the whole time
        still_writing, beating = not sweep.done(), beats
        gate.set()                                             # the disk comes back
        await sweep
        hb.cancel()
    assert still_writing and beating >= 10, f"the loop froze behind the write (beats={beating})"
    assert saver.calls == 1
    assert set(json.loads((tmp_path / "lan_presence.json").read_text())) == set(PHONE)


@pytest.mark.asyncio
async def test_the_book_is_written_when_the_house_changes_and_twice_a_day_regardless(tmp_path):
    """A refresh-only sweep writes nothing; a new device, a departure or an
    arrival writes at once; an untouched book still lands every 12 h."""
    clock = Clock()
    saver = RecordingSaver()
    house = {**PHONE, **ROUTER}
    newcomer = {**house, "ee:ff:00:00:00:09": {"ip": "192.0.2.9"}}
    sweeps = Scripted([dict(house), dict(house), dict(house), dict(house), dict(ROUTER), dict(house), newcomer])
    with patch.object(lp, "companion_settings", lambda: _flags()), patch.object(lp, "save_book", saver):
        eng, notices = _engine(tmp_path, sweeper=sweeps, clock=clock)
        await eng.tick_sweep()
        assert saver.calls == 1                                # the boot sweep: new devices → written
        clock.t += 3600
        await eng.tick_sweep()
        assert saver.calls == 1                                # an hour on, same house: nothing
        clock.t += 6 * 3600
        await eng.tick_sweep()
        assert saver.calls == 1                                # seven hours: still nothing
        clock.t += 5 * 3600
        await eng.tick_sweep()
        assert saver.calls == 2                                # twelve hours since the write: the flush
        clock.t += 25 * 60
        await eng.tick_sweep()
        assert saver.calls == 3                                # the phone left (past the threshold): at once
        clock.t += 60
        await eng.tick_sweep()
        assert saver.calls == 4                                # it came home: at once
        clock.t += 60
        await eng.tick_sweep()
        assert saver.calls == 5                                # a device never seen before: at once
    assert [k for k, _ in notices] == ["device_left", "device_arrived"]
    assert lp.BOOK_FLUSH_INTERVAL_S == 12 * 3600


@pytest.mark.asyncio
async def test_a_quiet_check_that_is_answered_writes_nothing(tmp_path):
    clock = Clock()
    saver = RecordingSaver()
    with patch.object(lp, "companion_settings", lambda: _flags()), patch.object(lp, "save_book", saver):
        eng, _ = _engine(tmp_path, sweeper=Scripted([dict(PHONE)]), prober=Scripted([True]), clock=clock)
        await eng.tick_sweep()
        clock.t += 16 * 60
        await eng.tick_quiet_checks()                          # asked, answered: a refreshed last_seen, no write
    assert saver.calls == 1


@pytest.mark.asyncio
async def test_stop_writes_a_dirty_book_but_never_hangs_on_a_dead_disk(tmp_path, caplog):
    """From the moment the house changes to the moment she is stopped, a disk
    that never answers costs at most SAVE_SHUTDOWN_TIMEOUT_S — the sudo-child
    lesson again: never an unbounded wait on the way out."""
    import logging
    import time as _time

    gate = threading.Event()                                   # never set: the disk is gone
    saver = RecordingSaver(gate=gate, write_through=False)
    with patch.object(lp, "companion_settings", lambda: _flags()), patch.object(lp, "save_book", saver), \
         patch.object(lp, "SAVE_SHUTDOWN_TIMEOUT_S", 0.2), caplog.at_level(logging.WARNING, logger="core.lan_presence"):
        eng, _ = _engine(tmp_path, sweeper=Scripted([]))
        t0 = _time.monotonic()
        eng.ingest_line(ARP_REPLY)                             # a device never seen before: worth a write
        await eng.stop()
        elapsed = _time.monotonic() - t0
    assert elapsed < 1.0, f"shutdown hung behind the disk for {elapsed:.1f}s"
    assert saver.calls == 1                                    # the write was attempted...
    assert any("did not finish" in r.getMessage() for r in caplog.records)   # ...and given up on, out loud


@pytest.mark.asyncio
async def test_stop_writes_the_latest_state_of_a_dirty_book(tmp_path):
    clock = Clock()
    saver = RecordingSaver()
    with patch.object(lp, "companion_settings", lambda: _flags()), patch.object(lp, "save_book", saver):
        eng, _ = _engine(tmp_path, sweeper=Scripted([{"6c:4c:bc:11:22:33": {"ip": "192.0.2.4"}}]), clock=clock)
        await eng.tick_sweep()                                 # written (new)
        clock.t += 60
        eng.ingest_line(ARP_REQ)                               # a refresh: dirty, not worth a write
        await eng.stop()                                       # ...until she stops
    assert saver.calls == 2
    assert saver.snapshots[-1]["6c:4c:bc:11:22:33"]["last_seen"] == clock.t
