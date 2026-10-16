"""Step 23 · LAN presence — parse and merge, pure (Task 4).

Spec §3 (amended 12:39 for §0.6, listen-don't-scan): the sender off a
tcpdump ARP line, nmap's mac+hostname, resolved neighbors only; edges only
on COMPLETE evidence past the absence threshold — a listener batch is
partial evidence (silence is not absence), so a wifi flap produces nothing.
Fixtures are the real formats captured 2026-08-24 12:41 on ens18 (MACs altered).
"""
from __future__ import annotations

from core import lan_presence as lp

NEIGH = """192.0.2.14 dev ens18 lladdr 02:00:00:00:00:02 STALE
192.0.2.21 dev ens18  FAILED
192.0.2.7 dev ens18 lladdr 02:00:00:00:00:03 REACHABLE"""

ARP_PROBE = "12:41:48.002151 80:05:1f:aa:bb:cc > ff:ff:ff:ff:ff:ff, ethertype ARP (0x0806), length 60: Request who-has 192.0.2.12 (ff:ff:ff:ff:ff:ff) tell 0.0.0.0, length 46"
ARP_REQ = "12:41:48.356184 6c:4c:bc:11:22:33 > 02:00:00:00:00:01, ethertype ARP (0x0806), length 60: Request who-has 192.0.2.64 tell 192.0.2.4, length 46"
ARP_REPLY = "12:41:48.705970 bc:24:11:44:55:66 > 6c:4c:bc:11:22:33, ethertype ARP (0x0806), length 42: Reply 192.0.2.28 is-at bc:24:11:44:55:66, length 28"

NMAP = """Starting Nmap 7.80 ( https://nmap.org ) at 2026-08-24 12:41 EDT
Nmap scan report for _gateway (192.0.2.1)
Host is up (0.0010s latency).
MAC Address: 3C:52:A1:00:11:22 (Unknown)
Nmap scan report for 192.0.2.5
Host is up (0.00086s latency).
MAC Address: DC:A6:32:33:44:55 (Raspberry Pi Trading)
Nmap scan report for 192.0.2.28
Host is up.
Nmap done: 256 IP addresses (3 hosts up) scanned in 3.65 seconds"""


# ── parsers ──────────────────────────────────────────────────────────────

def test_parse_ip_neigh_keeps_only_resolved_entries():
    out = lp.parse_ip_neigh(NEIGH)
    assert out == {"02:00:00:00:00:02": "192.0.2.14", "02:00:00:00:00:03": "192.0.2.7"}
    assert lp.parse_ip_neigh(NEIGH, states=("REACHABLE",)) == {"02:00:00:00:00:03": "192.0.2.7"}


def test_parse_tcpdump_arp_names_the_sender():
    assert lp.parse_tcpdump_arp(ARP_PROBE) == ("80:05:1f:aa:bb:cc", None)      # joining: no address yet
    assert lp.parse_tcpdump_arp(ARP_REQ) == ("6c:4c:bc:11:22:33", "192.0.2.4")
    assert lp.parse_tcpdump_arp(ARP_REPLY) == ("bc:24:11:44:55:66", "192.0.2.28")
    assert lp.parse_tcpdump_arp("tcpdump: verbose output suppressed") is None
    assert lp.parse_tcpdump_arp("") is None


def test_parse_tcpdump_arp_lowercases_and_ignores_the_broadcast_target():
    line = ARP_REQ.replace("6c:4c:bc:11:22:33", "6C:4C:BC:11:22:33")
    assert lp.parse_tcpdump_arp(line) == ("6c:4c:bc:11:22:33", "192.0.2.4")
    assert lp.parse_tcpdump_arp(ARP_PROBE)[0] != "ff:ff:ff:ff:ff:ff"


def test_parse_nmap_sn_reads_mac_hostname_and_skips_self():
    out = lp.parse_nmap_sn(NMAP)
    assert out == {
        "3c:52:a1:00:11:22": {"ip": "192.0.2.1", "hostname": "_gateway"},
        "dc:a6:32:33:44:55": {"ip": "192.0.2.5", "hostname": ""},
    }


# ── merge ────────────────────────────────────────────────────────────────

def test_merge_debounces_flapping_and_emits_real_edges():
    now = 10_000.0
    book, edges = lp.merge_scan({}, seen={"aa:bb": {"ip": "192.0.2.9"}}, now=now, absence_minutes=15)
    assert edges == []                                 # first sighting is never an "arrival"
    # gone 5 min (a wifi flap) then back: no edge
    book2, edges2 = lp.merge_scan(book, seen={}, now=now + 300, absence_minutes=15)
    book3, edges3 = lp.merge_scan(book2, seen={"aa:bb": {"ip": "192.0.2.9"}}, now=now + 600, absence_minutes=15)
    assert edges2 == [] and edges3 == []
    # gone 20 min then back: one departure (confirmed at threshold) and one arrival
    book4, edges4 = lp.merge_scan(book3, seen={}, now=now + 600 + 1_300, absence_minutes=15)
    book5, edges5 = lp.merge_scan(book4, seen={"aa:bb": {"ip": "192.0.2.9"}}, now=now + 600 + 1_300 + 60, absence_minutes=15)
    assert [e["kind"] for e in edges4 + edges5] == ["device_left", "device_arrived"]
    assert edges4[0]["mac"] == "aa:bb" and edges4[0]["absence_s"] >= 15 * 60
    assert book5["aa:bb"]["online"] is True and book4["aa:bb"]["online"] is False


def test_merge_never_mutates_its_input_and_keeps_first_seen():
    now = 10_000.0
    book, _ = lp.merge_scan({}, seen={"aa:bb": {"ip": "192.0.2.9", "hostname": "phone"}}, now=now, absence_minutes=15)
    frozen = {k: dict(v) for k, v in book.items()}
    book2, _ = lp.merge_scan(book, seen={"aa:bb": {"ip": "192.0.2.10"}}, now=now + 60, absence_minutes=15)
    assert book == frozen                              # the caller's book is untouched
    assert book2["aa:bb"]["first_seen"] == now and book2["aa:bb"]["last_seen"] == now + 60
    assert book2["aa:bb"]["ip"] == "192.0.2.10" and book2["aa:bb"]["hostname"] == "phone"


def test_partial_evidence_never_infers_a_departure():
    now = 10_000.0
    book, _ = lp.merge_scan({}, seen={"aa:bb": {"ip": "192.0.2.9"}}, now=now, absence_minutes=15)
    # a listener batch hours later that simply doesn't mention aa:bb: silence is not absence
    book2, edges = lp.merge_scan(book, seen={"cc:dd": {"ip": "192.0.2.3"}}, now=now + 7_200,
                                 absence_minutes=15, checked={"cc:dd"})
    assert edges == [] and book2["aa:bb"]["online"] is True
    # the quiet-check for aa:bb alone misses it: now it left
    book3, edges3 = lp.merge_scan(book2, seen={}, now=now + 7_260, absence_minutes=15, checked={"aa:bb"})
    assert [e["kind"] for e in edges3] == ["device_left"] and book3["aa:bb"]["online"] is False


def test_a_probe_with_no_address_keeps_the_known_ip():
    now = 10_000.0
    book, _ = lp.merge_scan({}, seen={"aa:bb": {"ip": "192.0.2.9"}}, now=now, absence_minutes=15)
    book2, _ = lp.merge_scan(book, seen={"aa:bb": {"ip": None}}, now=now + 5, absence_minutes=15, checked={"aa:bb"})
    assert book2["aa:bb"]["ip"] == "192.0.2.9"
    book3, _ = lp.merge_scan({}, seen={"ee:ff": {"ip": None}}, now=now, absence_minutes=15, checked={"ee:ff"})
    assert book3["ee:ff"]["ip"] == "" and book3["ee:ff"]["online"] is True


def test_device_display_prefers_label_then_hostname_then_mac_tail():
    now = 10_000.0
    seen = {
        "aa:bb:cc:dd:ee:01": {"ip": "192.0.2.1", "hostname": "router.lan"},
        "aa:bb:cc:dd:ee:02": {"ip": "192.0.2.2", "hostname": ""},
        "aa:bb:cc:dd:ee:03": {"ip": "192.0.2.3", "hostname": "pixel"},
    }
    book, _ = lp.merge_scan({}, seen=seen, now=now, absence_minutes=15)
    book2, edges = lp.merge_scan(book, seen={}, now=now + 3_600, absence_minutes=15,
                                 labels={"aa:bb:cc:dd:ee:03": "Sam's phone"})
    by_mac = {e["mac"]: e["device"] for e in edges}
    assert by_mac == {
        "aa:bb:cc:dd:ee:01": "router.lan",
        "aa:bb:cc:dd:ee:02": "…:ee:02",
        "aa:bb:cc:dd:ee:03": "Sam's phone",
    }


# ── online_since (Task 7): when a device came home ───────────────────────

def test_merge_tracks_online_since_across_refreshes_and_returns():
    now = 10_000.0
    book, _ = lp.merge_scan({}, seen={"aa:bb": {"ip": "192.0.2.9"}}, now=now, absence_minutes=15)
    assert book["aa:bb"]["online_since"] == now
    book2, _ = lp.merge_scan(book, seen={"aa:bb": {"ip": "192.0.2.9"}}, now=now + 600, absence_minutes=15)
    assert book2["aa:bb"]["online_since"] == now                    # a refresh keeps it
    book3, _ = lp.merge_scan(book2, seen={}, now=now + 600 + 1_300, absence_minutes=15)
    book4, _ = lp.merge_scan(book3, seen={"aa:bb": {}}, now=now + 2_000, absence_minutes=15)
    assert book4["aa:bb"]["online_since"] == now + 2_000            # home again: a new since


# ── the rendered line (Task 7) ───────────────────────────────────────────

import time
from types import SimpleNamespace
from unittest.mock import patch


def _hhmm(ts: float) -> str:
    return time.strftime("%H:%M", time.localtime(ts))


def _house(now: float):
    return {
        "aa:bb:cc:dd:ee:01": {"ip": "192.0.2.1", "hostname": "_gateway", "first_seen": now - 5 * 86400,
                              "last_seen": now - 60, "online": True, "online_since": now - 5 * 86400},
        "aa:bb:cc:dd:ee:03": {"ip": "192.0.2.3", "hostname": "pixel", "first_seen": now - 3 * 86400,
                              "last_seen": now - 30, "online": True, "online_since": now - 2 * 3600},
        "aa:bb:cc:dd:ee:09": {"ip": "192.0.2.9", "hostname": "", "first_seen": now - 600,
                              "last_seen": now - 600, "online": True, "online_since": now - 600},
        "aa:bb:cc:dd:ee:04": {"ip": "192.0.2.4", "hostname": "laptop", "first_seen": now - 9 * 86400,
                              "last_seen": now - 4 * 3600, "online": False, "offline_since": now - 4 * 3600},
    }


def _on():
    return patch.object(lp, "companion_settings", lambda: SimpleNamespace(lan_presence=True, lan_devices={}))


def test_render_line_is_zero_bytes_when_off_or_empty():
    now = 1_700_000_000.0
    with patch.object(lp, "companion_settings", lambda: SimpleNamespace(lan_presence=False, lan_devices={})):
        assert lp.render_line(_house(now), now=now) == ""
    with _on():
        assert lp.render_line({}, now=now) == ""


def test_render_line_names_the_house():
    now = 1_700_000_000.0
    labels = {"AA:BB:CC:DD:EE:03": "Sam's phone", "aa:bb:cc:dd:ee:04": "Sam's laptop"}
    with _on():
        line = lp.render_line(_house(now), now=now, labels=labels)
    assert line == (
        "the house right now: 3 devices online; "
        f"Sam's phone home since {_hhmm(now - 2 * 3600)}; "
        f"Sam's laptop away since {_hhmm(now - 4 * 3600)}; "
        "one device never seen before today"
    )


def test_render_line_singular_plain_and_new_counts():
    now = 1_700_000_000.0
    lone = {"aa:bb:cc:dd:ee:01": {"ip": "192.0.2.1", "hostname": "_gateway", "first_seen": now - 5 * 86400,
                                  "last_seen": now - 5, "online": True, "online_since": now - 5 * 86400}}
    with _on():
        assert lp.render_line(lone, now=now) == "the house right now: 1 device online"
        fresh = {m: {**r, "first_seen": now - 100} for m, r in _house(now).items()}
        assert lp.render_line(fresh, now=now).endswith("4 devices never seen before today")


def test_render_line_carries_an_age_only_when_stale():
    now = 1_700_000_000.0
    house = _house(now)
    with _on():
        assert "as of" not in lp.render_line(house, now=now)                       # heard 30 s ago
        stale = {m: {**r, "last_seen": r["last_seen"] - 45 * 60} for m, r in house.items()}
        assert lp.render_line(stale, now=now).endswith(" (as of 45 minutes ago)")
        older = {m: {**r, "last_seen": r["last_seen"] - 5 * 3600} for m, r in house.items()}
        assert lp.render_line(older, now=now).endswith(" (as of about 5 hours ago)")


def test_render_line_reads_the_engine_book_and_yaml_labels_by_default(tmp_path):
    now = 1_700_000_000.0
    lp.save_book(tmp_path / "lan_presence.json", _house(now))
    eng = lp.LanPresenceEngine(book_path=tmp_path / "lan_presence.json", clock=lambda: now)
    flags = SimpleNamespace(lan_presence=True, lan_devices={"aa:bb:cc:dd:ee:03": "Sam's phone"})
    with patch.object(lp, "companion_settings", lambda: flags), patch.object(lp, "lan_presence_engine", eng):
        line = lp.render_line(now=now)
    assert line.startswith("the house right now: 3 devices online; Sam's phone home since ")
