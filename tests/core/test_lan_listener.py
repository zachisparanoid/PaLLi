"""Step 23 · LAN presence — the listener, the sense itself (Task 6).

Spec §3/§0.6: she hears the house's own ARP chatter; a join is an arrival
(only after a confirmed departure — the first sighting is silent); silence
is not absence; the disk hears only real change; the capture restarts with backoff and
never multiplies. NO test here captures anything: the stream is injected.
"""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from core import lan_presence as lp
from tests.core.test_lan_presence import ARP_PROBE, ARP_REPLY, ARP_REQ
from tests.core.test_lan_presence_engine import Clock, Scripted, _flags


def _engine(tmp_path, *, clock=None, listener=None, sweeper=None):
    notices = []
    eng = lp.LanPresenceEngine(
        book_path=tmp_path / "lan_presence.json", subnet="192.0.2.0/24", iface="ens18",
        sweeper=sweeper or Scripted([]), prober=Scripted([]), listener=listener,
        notice=lambda kind, **f: notices.append((kind, f)), clock=clock or Clock(),
    )
    return eng, notices


REQ_MAC = "6c:4c:bc:11:22:33"       # the sender of ARP_REQ (tell 192.0.2.4)


def test_listener_argv_is_bounded_and_pinned_verbatim():
    """The window is the orphan backstop: `timeout` runs as root INSIDE
    sudo, so it can always kill its own tcpdump — even if her process dies
    violently and never reaps anything (2026-08-24 defect)."""
    assert lp.listener_argv("ens18") == [
        "sudo", "-n", "timeout", "-s", "TERM", str(lp.LISTENER_WINDOW_S),
        "tcpdump", "-l", "-n", "-e", "-i", "ens18", "arp",
    ]
    assert lp.LISTENER_WINDOW_S <= 3600


def test_reap_argv_signals_the_whole_process_group_as_root():
    """Her process runs as ubuntu; the capture runs as root. An
    unprivileged TERM/KILL to a root sudo is EPERM — it did nothing, the
    capture was orphaned, and proc.wait() hung forever (it hung a full
    test suite for 1h47m on 2026-08-24). Root kills the GROUP instead:
    sudo, timeout and tcpdump all die together."""
    assert lp.reap_argv(4242) == ["sudo", "-n", "kill", "-TERM", "--", "-4242"]
    assert lp.reap_argv(4242, sig="KILL") == ["sudo", "-n", "kill", "-KILL", "--", "-4242"]


# ── the interlock: no real capture or scan may EVER start under pytest ──

def test_under_test_is_true_in_here():
    assert lp.under_test() is True


@pytest.mark.asyncio
async def test_the_real_spawner_refuses_under_a_test_harness():
    """The law ('never a real capture in a test') is now mechanism, not
    intent: tests inject fakes, and the real spawner refuses regardless."""
    assert [line async for line in lp._spawn_listener("ens18")] == []


@pytest.mark.asyncio
async def test_the_real_sweep_and_probe_refuse_under_a_test_harness():
    assert await lp.run_sweep("192.0.2.0/24") == ({}, False)   # no evidence, never a scan
    assert await lp.run_probe("192.0.2.1") is None             # "could not ask"


@pytest.mark.asyncio
async def test_the_listener_waits_out_startup_before_it_ever_spawns(tmp_path):
    """A test that boots the app (tests/core/test_main.py) must never reach
    a spawn: the listener sits out startup exactly like the sweep loop."""
    import asyncio

    stream = FakeStream([ARP_REPLY])
    slept = []

    async def fake_sleep(seconds):
        slept.append(seconds)
        raise asyncio.CancelledError

    with patch.object(lp, "companion_settings", lambda: _flags()), \
         patch.object(asyncio, "sleep", fake_sleep):
        eng, _ = _engine(tmp_path, listener=stream)
        eng.is_running = True
        with pytest.raises(asyncio.CancelledError):
            await eng._listener_loop()
    assert slept and slept[0] >= 45 and stream.spawned == []


def test_backoff_ladder_caps_at_a_minute():
    assert [lp.listener_backoff_s(n) for n in range(6)] == [5, 10, 20, 40, 60, 60]


def test_a_probe_from_a_never_seen_mac_creates_a_silent_entry(tmp_path):
    with patch.object(lp, "companion_settings", lambda: _flags()):
        eng, notices = _engine(tmp_path)
        edges = eng.ingest_line(ARP_PROBE)
    assert edges == [] and notices == []
    rec = eng.book["80:05:1f:aa:bb:cc"]
    assert rec["online"] is True and rec["ip"] == "" and rec["source"] == "heard"


def test_a_request_from_a_known_offline_mac_is_exactly_one_arrival(tmp_path):
    clock = Clock()
    with patch.object(lp, "companion_settings", lambda: _flags()):
        eng, notices = _engine(tmp_path, clock=clock,
                               sweeper=Scripted([{REQ_MAC: {"ip": "192.0.2.4", "hostname": "router"}}, {}]))
        pytest.importorskip("asyncio")
        import asyncio
        asyncio.run(eng.tick_sweep())
        clock.t += 20 * 60
        asyncio.run(eng.tick_sweep())                          # confirmed gone
        assert [k for k, _ in notices] == ["device_left"]
        clock.t += 60
        edges = eng.ingest_line(ARP_REQ)                        # it speaks again: home
        again = eng.ingest_line(ARP_REQ)
    assert [e["kind"] for e in edges] == ["device_arrived"] and again == []
    assert [(k, f["device"]) for k, f in notices] == [("device_left", "router"), ("device_arrived", "router")]
    assert eng.book[REQ_MAC]["online"] is True and eng.book[REQ_MAC]["ip"] == "192.0.2.4"


def test_a_line_for_an_online_device_refreshes_and_says_nothing(tmp_path):
    clock = Clock()
    with patch.object(lp, "companion_settings", lambda: _flags()):
        eng, notices = _engine(tmp_path, clock=clock)
        eng.ingest_line(ARP_REPLY)
        clock.t += 600
        edges = eng.ingest_line(ARP_REPLY)
    assert edges == [] and notices == []
    assert eng.book["bc:24:11:44:55:66"]["last_seen"] == clock.t


def test_silence_on_the_wire_is_never_a_departure(tmp_path):
    clock = Clock()
    with patch.object(lp, "companion_settings", lambda: _flags()):
        eng, notices = _engine(tmp_path, clock=clock)
        eng.ingest_line(ARP_REPLY)
        clock.t += 6 * 3600                                     # six silent hours
        eng.ingest_line(ARP_REQ)                                # someone ELSE speaks
    assert notices == [] and eng.book["bc:24:11:44:55:66"]["online"] is True


def test_garbage_and_non_arp_lines_are_ignored(tmp_path):
    with patch.object(lp, "companion_settings", lambda: _flags()):
        eng, notices = _engine(tmp_path)
        for line in ("", "tcpdump: verbose output suppressed, use -v[v]... for full protocol decode",
                     "listening on ens18, link-type EN10MB (Ethernet), snapshot length 262144 bytes",
                     "12:41:48.0 6c:4c:bc:11:22:33 > ff:ff:ff:ff:ff:ff, ethertype IPv4 (0x0800), length 60: something"):
            assert eng.ingest_line(line) == []
    assert eng.book == {} and notices == []


@pytest.mark.asyncio
async def test_heard_refreshes_never_touch_the_disk_but_an_arrival_lands_at_once(tmp_path):
    """The router ARP-walks the house all day: every heard line refreshes
    last_seen in memory and none of them is worth a disk write — nor is the
    end of a capture window (his 20:57 call: twice a day). A device the
    book had confirmed gone speaking again IS worth one, at once, from the
    listener's own path."""
    from tests.core.test_lan_presence_engine import RecordingSaver

    clock = Clock()
    saver = RecordingSaver()
    stream = FakeStream([ARP_REQ] * 5)
    with patch.object(lp, "companion_settings", lambda: _flags()), patch.object(lp, "save_book", saver):
        eng, notices = _engine(tmp_path, clock=clock, listener=stream,
                               sweeper=Scripted([{REQ_MAC: {"ip": "192.0.2.4", "hostname": "router"}}, {}]))
        await eng.tick_sweep()                                  # the router is home: written (new)
        assert saver.calls == 1
        clock.t += 60
        await eng.run_listener_once()                           # five refreshes, then the window closes
        assert saver.calls == 1 and eng.book[REQ_MAC]["last_seen"] == clock.t
        clock.t += 20 * 60
        await eng.tick_sweep()                                  # confirmed gone: written
        assert saver.calls == 2
        clock.t += 60
        stream.lines = [ARP_REQ]
        await eng.run_listener_once()                           # it speaks again: home, written at once
        assert saver.calls == 3
    assert [k for k, _ in notices] == ["device_left", "device_arrived"]
    assert saver.snapshots[-1][REQ_MAC]["online"] is True


def test_flag_off_ignores_the_wire_entirely(tmp_path):
    with patch.object(lp, "companion_settings", lambda: _flags(lan_presence=False)):
        eng, notices = _engine(tmp_path)
        assert eng.ingest_line(ARP_REPLY) == []
    assert eng.book == {} and not (tmp_path / "lan_presence.json").exists()


# ── the injected stream: consumed to its end, spawned only when allowed ──

class FakeStream:
    def __init__(self, lines):
        self.lines = list(lines)
        self.spawned = []

    async def __call__(self, iface):
        self.spawned.append(iface)
        for line in self.lines:
            yield line


@pytest.mark.asyncio
async def test_run_listener_consumes_the_stream_and_returns_when_it_ends(tmp_path):
    stream = FakeStream([ARP_PROBE, "garbage", ARP_REQ])
    with patch.object(lp, "companion_settings", lambda: _flags()):
        eng, notices = _engine(tmp_path, listener=stream)
        await eng.run_listener_once()
    assert stream.spawned == ["ens18"]
    assert set(eng.book) == {"80:05:1f:aa:bb:cc", REQ_MAC} and notices == []
    assert (tmp_path / "lan_presence.json").exists()           # two devices never seen before: written


@pytest.mark.asyncio
async def test_listener_sub_switch_off_never_spawns(tmp_path):
    stream = FakeStream([ARP_PROBE])
    with patch.object(lp, "companion_settings", lambda: _flags(lan_listener=False)):
        eng, _ = _engine(tmp_path, listener=stream)
        await eng.run_listener_once()
    assert stream.spawned == [] and eng.book == {}


@pytest.mark.asyncio
async def test_a_stream_that_raises_leaves_the_book_intact(tmp_path):
    async def broken(iface):
        yield ARP_REPLY
        raise RuntimeError("capture died")

    with patch.object(lp, "companion_settings", lambda: _flags()):
        eng, notices = _engine(tmp_path, listener=broken)
        await eng.run_listener_once()                           # swallowed
    assert eng.book["bc:24:11:44:55:66"]["online"] is True and notices == []
