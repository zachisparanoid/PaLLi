"""The LAN presence sense — "the house right now" (step 23 · Outward senses).

Spec: docs/superpowers/specs/2026-08-24-outward-senses-design.md, §3, under
the owner's §0.2 (the WHOLE LAN — every device's come-and-go), §0.5 (the
network is her home's network: she lives here too and can see who's home)
and §0.6 (LISTEN, DON'T SCAN — his 12:39 call "C").

What this is. The house announces its own arrivals: a joining device sends
an ARP probe (``who-has … tell 0.0.0.0``) that reaches this VM without
anything being sent. So the sense is a LISTENER on the segment's ARP
chatter, plus a confirmation sweep a few times a day (root ``nmap -sn`` on
the local segment = pure ARP who-has) and one targeted quiet-check for a
device that has gone silent. Never a port scan; never a connection to a
device. The device book is a small JSON record; the edges — a device
came home, a device left — are ordinary present-moment wakes, debounced
past an absence threshold so a wifi flap produces nothing.

This module head is PURE: parsers for the three real formats and the
merge that turns observations into a new book plus edge events. No I/O,
no clock, no subprocess — the engine half (below, Task 5+) injects those.

Evidence law (the heart of the debounce): an observation is either
COMPLETE for a set of devices (a sweep, or the quiet-check for one MAC —
absence from it is meaningful) or PARTIAL (a listener batch: silence is
not absence). Only complete evidence, past the threshold, confirms a
departure; the first sighting of a MAC is never an arrival.

Architecture: core ← ai (her senses read here; the moment's ``notice`` is
injected). Nothing here imports bots or discord.
"""
from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional, Tuple

DEFAULT_ABSENCE_MINUTES = 15

_MAC = r"[0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5}"
_IP = r"\d{1,3}(?:\.\d{1,3}){3}"

_NEIGH_RE = re.compile(rf"^\s*({_IP})\s+dev\s+\S+\s+lladdr\s+({_MAC})\s+(\S+)")
_TCPDUMP_SRC_RE = re.compile(rf"^\S+\s+({_MAC})\s+>\s+{_MAC},")
_TCPDUMP_TELL_RE = re.compile(rf"\btell\s+({_IP})")
_TCPDUMP_REPLY_RE = re.compile(rf"\bReply\s+({_IP})\s+is-at\s+({_MAC})")
_NMAP_REPORT_RE = re.compile(rf"^Nmap scan report for\s+(?:(\S+)\s+\(({_IP})\)|({_IP}))\s*$")
_NMAP_MAC_RE = re.compile(rf"^MAC Address:\s+({_MAC})")


# ── parsers (fixtures: the real formats, 2026-08-24 12:41, ens18) ────────

def _norm_mac(mac: Any) -> str:
    return str(mac or "").strip().lower()


def parse_ip_neigh(text: str, states: Optional[Iterable[str]] = None) -> Dict[str, str]:
    """``ip neigh show`` → {mac: ip} for entries that carry a link address
    (FAILED/INCOMPLETE rows have none and are dropped). ``states`` narrows
    to the kernel states named — the engine passes REACHABLE/DELAY/PROBE
    when it needs *positive* evidence; STALE is ambiguous."""
    wanted = None if states is None else {str(s).upper() for s in states}
    out: Dict[str, str] = {}
    for line in str(text or "").splitlines():
        m = _NEIGH_RE.match(line)
        if not m:
            continue
        ip, mac, state = m.group(1), _norm_mac(m.group(2)), m.group(3).upper()
        if wanted is not None and state not in wanted:
            continue
        out[mac] = ip
    return out


def parse_tcpdump_arp(line: str) -> Optional[Tuple[str, Optional[str]]]:
    """One ``tcpdump -l -n -e … arp`` line → (sender mac, sender ip | None).

    The sender is the first MAC on the line (the frame's source). For a
    request, ``tell <ip>`` is its address — ``tell 0.0.0.0`` is an ARP probe,
    a device JOINING the network, which has no address yet (ip None). For a
    reply, ``<ip> is-at <mac>`` names the sender. Anything else → None."""
    s = str(line or "")
    if "ARP" not in s:
        return None
    src = _TCPDUMP_SRC_RE.match(s)
    if not src:
        return None
    mac = _norm_mac(src.group(1))
    if "Request who-has" in s:
        tell = _TCPDUMP_TELL_RE.search(s)
        if not tell:
            return None
        ip = tell.group(1)
        return (mac, None if ip == "0.0.0.0" else ip)
    reply = _TCPDUMP_REPLY_RE.search(s)
    if reply:
        return (_norm_mac(reply.group(2)), reply.group(1))
    return None


def parse_nmap_sn(text: str) -> Dict[str, Dict[str, str]]:
    """Root ``nmap -sn`` stdout → {mac: {"ip", "hostname"}}. A report with
    no ``MAC Address:`` line is the scanning host itself and is skipped —
    she knows she is home."""
    out: Dict[str, Dict[str, str]] = {}
    current: Optional[Dict[str, str]] = None
    for raw in str(text or "").splitlines():
        line = raw.strip()
        rep = _NMAP_REPORT_RE.match(line)
        if rep:
            hostname = rep.group(1) or ""
            ip = rep.group(2) or rep.group(3) or ""
            current = {"ip": ip, "hostname": hostname}
            continue
        macm = _NMAP_MAC_RE.match(line)
        if macm and current is not None:
            out[_norm_mac(macm.group(1))] = dict(current)
            current = None
    return out


# ── display ──────────────────────────────────────────────────────────────

def mac_tail(mac: str) -> str:
    """The last two octets, ellipsized: ``…:2c:ba`` — enough to tell
    devices apart in a line, never the whole address."""
    parts = _norm_mac(mac).split(":")
    return "…:" + ":".join(parts[-2:]) if len(parts) >= 2 else _norm_mac(mac)


def device_display(mac: str, rec: Optional[Dict[str, Any]] = None,
                   labels: Optional[Dict[str, str]] = None) -> str:
    """How a device is named to her: the owner's yaml label > the hostname
    > the mac tail."""
    mac = _norm_mac(mac)
    for k, v in (labels or {}).items():
        if _norm_mac(k) == mac and str(v or "").strip():
            return str(v).strip()
    hostname = str((rec or {}).get("hostname") or "").strip()
    return hostname or mac_tail(mac)


# ── merge ────────────────────────────────────────────────────────────────

def merge_scan(book: Optional[Dict[str, Dict[str, Any]]], *,
               seen: Optional[Dict[str, Dict[str, Any]]], now: float,
               absence_minutes: int,
               checked: Optional[Iterable[str]] = None,
               labels: Optional[Dict[str, str]] = None,
               ) -> Tuple[Dict[str, Dict[str, Any]], List[Dict[str, Any]]]:
    """Fold one observation into the book. Pure: returns a NEW book and the
    debounced edge events; the caller's book is never mutated.

    ``seen`` — positive evidence: {mac: {"ip"?, "hostname"?, "source"?}}.
    ``checked`` — the MACs whose ABSENCE from ``seen`` is meaningful:
    ``None`` = every known MAC (a full sweep); a listener batch passes
    ``set(seen)`` (nothing beyond what was heard); a quiet-check passes
    ``{mac}``. Departures need complete evidence past the threshold;
    arrivals need a device the book had confirmed gone; the first sighting
    of a MAC is silent."""
    threshold_s = max(1, int(absence_minutes)) * 60.0
    now = float(now)
    new_book: Dict[str, Dict[str, Any]] = {
        _norm_mac(m): dict(rec or {}) for m, rec in (book or {}).items()
    }
    edges: List[Dict[str, Any]] = []
    seen_macs = set()
    for raw_mac, info in (seen or {}).items():
        mac = _norm_mac(raw_mac)
        if not mac:
            continue
        seen_macs.add(mac)
        info = info or {}
        ip = str(info.get("ip") or "").strip()
        hostname = str(info.get("hostname") or "").strip()
        source = str(info.get("source") or "").strip()
        rec = new_book.get(mac)
        if rec is None:
            new_book[mac] = {
                "ip": ip, "hostname": hostname,
                "first_seen": now, "last_seen": now, "online_since": now,
                "online": True, "source": source,
            }
            continue                                  # first sighting: never an arrival
        was_online = bool(rec.get("online"))
        last_seen = float(rec.get("last_seen") or now)
        if ip:
            rec["ip"] = ip
        if hostname:
            rec["hostname"] = hostname
        if source:
            rec["source"] = source
        rec["last_seen"] = now
        if not was_online:
            rec["online"] = True
            rec["online_since"] = now
            rec.pop("offline_since", None)
            edges.append({
                "kind": "device_arrived",
                "device": device_display(mac, rec, labels),
                "mac": mac,
                "absence_s": max(0.0, now - last_seen),
            })
    check = set(new_book) if checked is None else {_norm_mac(m) for m in checked}
    for mac in sorted(check - seen_macs):
        rec = new_book.get(mac)
        if rec is None or not rec.get("online"):
            continue
        gap = now - float(rec.get("last_seen") or now)
        if gap >= threshold_s:
            rec["online"] = False
            rec["offline_since"] = now
            edges.append({
                "kind": "device_left",
                "device": device_display(mac, rec, labels),
                "mac": mac,
                "absence_s": gap,
            })
    return new_book, edges


# ═════════════════════════════════════════════════════════════════════════
# The I/O half — the book, the sweeper, the quiet-check, the engine (Task 5)
# ═════════════════════════════════════════════════════════════════════════

import asyncio  # noqa: E402
import copy  # noqa: E402
import ipaddress  # noqa: E402
import json  # noqa: E402
import logging  # noqa: E402
import os  # noqa: E402
import subprocess  # noqa: E402
import time  # noqa: E402
from pathlib import Path  # noqa: E402
from typing import Awaitable, Callable  # noqa: E402

from core.turn.companion_flags import companion_settings  # noqa: E402

logger = logging.getLogger(__name__)
essential_logger = logging.getLogger("palli.essential")

_REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BOOK_PATH = _REPO_ROOT / "data" / "lan_presence.json"

DEFAULT_SWEEP_INTERVAL_MINUTES = 480      # three a day (§0.6)
SWEEP_TIMEOUT_S = 60
PROBE_TIMEOUT_S = 15
QUIET_CHECK_EVERY_S = 60
BOOT_DELAY_S = 45                          # never compete with startup
# The book on disk is written when the HOUSE changes — a device never seen
# before, an arrival, a departure — and twice a day regardless: his 08-24
# 20:57 call ("my network does not change anywhere near that often"). A
# heard refresh, a sweep that confirms the same house, an answered
# quiet-check and the hourly close of a capture window change memory only.
# And no write ever runs on the loop thread: 2026-08-24 19:5x the old
# 60-second on-loop rewrite waited 30–120 s on this VM's ext4 journal and
# froze her whole brain with it — replies, voice, the Discord heartbeat.
BOOK_FLUSH_INTERVAL_S = 12 * 3600          # twice a day regardless
SAVE_SHUTDOWN_TIMEOUT_S = 10.0             # the last write at shutdown gets this long, then she walks away
_POSITIVE_NEIGH_STATES = ("REACHABLE", "DELAY", "PROBE")


# ── the argv law: every process this sense ever starts ───────────────────

def sweep_argv(subnet: str) -> List[str]:
    """Root ``nmap -sn`` on the local segment = pure ARP who-has (every
    device answers ARP by necessity; nothing is connected to). Root via
    ``sudo -n`` is the point: unprivileged nmap -sn TCP-connects to ports
    80/443 on every host — forbidden (spec §3)."""
    return ["sudo", "-n", "nmap", "-sn", str(subnet)]


def probe_argv(ip: str) -> List[str]:
    """One host, one ARP who-has — the quiet-check."""
    return ["sudo", "-n", "nmap", "-sn", str(ip)]


# ── where she lives on the wire ──────────────────────────────────────────

def _run_sync(argv: List[str], timeout_s: float = 5.0) -> str:
    try:
        cp = subprocess.run(argv, capture_output=True, text=True, timeout=timeout_s, check=False)
        return cp.stdout or ""
    except Exception:
        return ""


def detect_iface_and_subnet(*, route_text: Optional[str] = None,
                            addr_text: Optional[str] = None,
                            ) -> Tuple[Optional[str], Optional[str]]:
    """The primary interface and its /prefix network, from the default
    route (``ip -4 route show default`` → ``dev ens18``) and that
    interface's address (``ip -4 -o addr show dev ens18`` → ``192.0.2.28/24``
    → ``192.0.2.0/24``). Env ``LAN_PRESENCE_IFACE`` / ``LAN_PRESENCE_SUBNET``
    override without looking (the CURIOSITY_NAS_ROOT precedent). Pure when
    the texts are given; garbage degrades to (None, None)."""
    env_iface = (os.environ.get("LAN_PRESENCE_IFACE") or "").strip() or None
    env_subnet = (os.environ.get("LAN_PRESENCE_SUBNET") or "").strip() or None
    if env_iface and env_subnet:
        return env_iface, env_subnet
    if route_text is None:
        route_text = _run_sync(["ip", "-4", "route", "show", "default"])
    m = re.search(r"\bdev\s+(\S+)", route_text or "")
    iface = env_iface or (m.group(1) if m else None)
    if not iface:
        return None, env_subnet
    if env_subnet:
        return iface, env_subnet
    if addr_text is None:
        addr_text = _run_sync(["ip", "-4", "-o", "addr", "show", "dev", iface])
    m2 = re.search(rf"\binet\s+({_IP}/\d{{1,2}})", addr_text or "")
    if not m2:
        return iface, None
    try:
        return iface, str(ipaddress.ip_interface(m2.group(1)).network)
    except ValueError:
        return iface, None


# ── the device book ──────────────────────────────────────────────────────

def load_book(path: Optional[Path] = None) -> Dict[str, Dict[str, Any]]:
    """The book on disk, or the honest empty book. Garbage reads as empty."""
    p = Path(path) if path else DEFAULT_BOOK_PATH
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(raw, dict):
        return {}
    return {_norm_mac(k): dict(v) for k, v in raw.items() if isinstance(v, dict) and _norm_mac(k)}


def save_book(path: Optional[Path], book: Dict[str, Dict[str, Any]]) -> None:
    """Atomic: write beside, then replace. Never raises to the caller."""
    p = Path(path) if path else DEFAULT_BOOK_PATH
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_text(json.dumps(book, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(tmp, p)
    except OSError as exc:
        logger.warning("lan_presence: could not save the book (%s)", exc)


async def _reap(proc) -> None:
    """Take the capture down and NEVER block on it. Root TERM to the
    process group, then root KILL, each waited briefly; if both are
    refused (no passwordless sudo on this host) the capture's own
    ``timeout`` window is the backstop and we walk away rather than hang."""
    if proc.returncode is not None:
        return
    for sig in ("TERM", "KILL"):
        try:
            killer = await asyncio.create_subprocess_exec(
                *reap_argv(proc.pid, sig=sig), stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.wait_for(killer.wait(), timeout=REAP_WAIT_S)
        except Exception as exc:  # noqa: BLE001
            logger.debug("lan_presence: reap (%s) failed: %s", sig, exc)
        try:
            await asyncio.wait_for(asyncio.shield(proc.wait()), timeout=REAP_WAIT_S)
            return
        except (asyncio.TimeoutError, Exception):  # noqa: BLE001
            continue
    logger.warning("lan_presence: could not reap the capture (pid %s); its %ds window will end it",
                   proc.pid, LISTENER_WINDOW_S)




# ── the processes (only the engine's defaults; tests inject fakes) ───────

async def _run_argv(argv: List[str], timeout_s: float) -> Tuple[Optional[int], str, str]:
    """Run one bounded command and read it. Its own session, so a root
    child (every ``sudo -n`` here) can be reaped as root on timeout — an
    unprivileged kill would be EPERM and the wait would hang (the
    2026-08-24 defect; see _reap)."""
    proc = await asyncio.create_subprocess_exec(
        *argv, stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        start_new_session=True,
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
    except asyncio.TimeoutError:
        await _reap(proc)
        return None, "", "timeout"
    return proc.returncode, out.decode("utf-8", "replace"), err.decode("utf-8", "replace")


async def run_sweep(subnet: str) -> Tuple[Dict[str, Dict[str, Any]], bool]:
    """The confirmation sweep → (seen, complete). Root nmap = complete
    evidence. When nmap or sudo is unavailable, the kernel's neighbor table
    (positive states only) is PARTIAL evidence — it never confirms a
    departure. A sweep that finds nobody is a failed sweep, not an empty
    house."""
    if under_test():
        return {}, False                      # the interlock: never a real scan in a test
    try:
        rc, out, err = await _run_argv(sweep_argv(subnet), SWEEP_TIMEOUT_S)
    except Exception as exc:
        rc, out, err = None, "", str(exc)
    if rc == 0:
        seen = parse_nmap_sn(out)
        if seen:
            return seen, True
    else:
        logger.warning("lan_presence: sweep unavailable (rc=%s: %s) — falling back to the neighbor table",
                       rc, (err or "").strip()[:120])
    try:
        _, out, _ = await _run_argv(["ip", "neigh", "show"], 5)
        neigh = parse_ip_neigh(out, states=_POSITIVE_NEIGH_STATES)
        return {mac: {"ip": ip} for mac, ip in neigh.items()}, False
    except Exception:
        return {}, False


async def run_probe(ip: str) -> Optional[bool]:
    """One ARP who-has for one host: True = answered, False = silent,
    None = could not ask (never evidence)."""
    if under_test():
        return None                           # the interlock: never a real probe in a test
    try:
        rc, out, _ = await _run_argv(probe_argv(ip), PROBE_TIMEOUT_S)
    except Exception:
        return None
    if rc != 0:
        return None
    return any(str(v.get("ip")) == str(ip) for v in parse_nmap_sn(out).values())


def _notice_moment(kind: str, **fields: Any) -> None:
    """The default edge sink: the present moment's fire-and-forget notice."""
    try:
        from ai.present_moment import notice

        notice(kind, **fields)
    except Exception:
        pass


# ── the engine ───────────────────────────────────────────────────────────

class LanPresenceEngine:
    """Scan → merge → notice → persist, with every collaborator injectable.
    ``tick_sweep`` and ``tick_quiet_checks`` are the testable units; the
    loop only schedules them. Nothing raises out of a tick."""

    def __init__(self, *, book_path: Optional[Path] = None,
                 iface: Optional[str] = None, subnet: Optional[str] = None,
                 sweep_interval_minutes: Optional[int] = None,
                 absence_minutes: Optional[int] = None,
                 sweeper: Optional[Callable[[str], Awaitable[Any]]] = None,
                 prober: Optional[Callable[[str], Awaitable[Optional[bool]]]] = None,
                 listener: Any = None,
                 notice: Optional[Callable[..., Any]] = None,
                 clock: Optional[Callable[[], float]] = None,
                 labels: Optional[Dict[str, str]] = None) -> None:
        self._book_path = Path(book_path) if book_path else DEFAULT_BOOK_PATH
        self._iface = iface
        self._subnet = subnet
        self._sweep_interval_override = sweep_interval_minutes
        self._absence_override = absence_minutes
        self._sweeper = sweeper or run_sweep
        self._prober = prober or run_probe
        self._listener = listener
        self._notice = notice or _notice_moment
        self._clock = clock or time.time
        self._labels_override = labels
        self._book: Dict[str, Dict[str, Any]] = {}
        self._loaded = False
        self._last_sweep_at: float = 0.0
        self._last_observation_at: float = 0.0
        self._last_save_at: float = 0.0
        self._dirty = False                # the book differs from the disk
        self._urgent = False               # ...because the house itself changed
        self._save_lock = asyncio.Lock()   # two loops never race the same file
        self._target_warned = False
        self._task: Optional[asyncio.Task] = None
        self._listener_task: Optional[asyncio.Task] = None
        self.is_running = False

    # ── flags and knobs (read live; the owner's yaml is the source) ──────
    def enabled(self) -> bool:
        try:
            comp = companion_settings()
            return bool(comp is not None and getattr(comp, "lan_presence", False))
        except Exception:
            return False

    def listener_enabled(self) -> bool:
        try:
            comp = companion_settings()
            return self.enabled() and bool(getattr(comp, "lan_listener", True))
        except Exception:
            return False

    def absence_minutes(self) -> int:
        if self._absence_override is not None:
            return max(1, int(self._absence_override))
        try:
            return max(1, int(getattr(companion_settings(), "lan_absence_minutes", DEFAULT_ABSENCE_MINUTES)))
        except Exception:
            return DEFAULT_ABSENCE_MINUTES

    def sweep_interval_s(self) -> float:
        if self._sweep_interval_override is not None:
            return max(30, int(self._sweep_interval_override)) * 60.0
        try:
            minutes = int(getattr(companion_settings(), "lan_sweep_interval_minutes",
                                  DEFAULT_SWEEP_INTERVAL_MINUTES))
        except Exception:
            minutes = DEFAULT_SWEEP_INTERVAL_MINUTES
        return max(30, minutes) * 60.0

    def labels(self) -> Dict[str, str]:
        if self._labels_override is not None:
            return dict(self._labels_override)
        try:
            raw = getattr(companion_settings(), "lan_devices", None) or {}
            return {str(k): str(v) for k, v in dict(raw).items()}
        except Exception:
            return {}

    # ── the book ─────────────────────────────────────────────────────────
    @property
    def book(self) -> Dict[str, Dict[str, Any]]:
        self._ensure_loaded()
        return self._book

    @property
    def book_path(self) -> Path:
        return self._book_path

    @property
    def last_observation_at(self) -> float:
        return self._last_observation_at

    def _ensure_loaded(self) -> None:
        if not self._loaded:
            self._book = load_book(self._book_path)
            self._loaded = True

    async def _flush(self, *, force: bool = False,
                     timeout_s: Optional[float] = None) -> bool:
        """The ONE road to the disk. Writes the book on a worker thread —
        when the house changed (``_urgent``), when the twice-a-day flush is
        due, or when forced (shutdown) — and only if anything changed at
        all. Serialized, so the sweep loop and the listener never race the
        same file. ``timeout_s`` bounds the wait (shutdown): a disk that
        has gone away costs that long and no more. Returns True when a
        write landed. Never raises."""
        if not (self._loaded and self._dirty):
            return False
        now = self._clock()
        if not (force or self._urgent or (now - self._last_save_at) >= BOOK_FLUSH_INTERVAL_S):
            return False
        async with self._save_lock:
            if not self._dirty:                # the other loop just wrote it
                return False
            snapshot = copy.deepcopy(self._book)   # the thread reads a copy; the loop keeps the original
            self._dirty = False
            self._urgent = False
            self._last_save_at = self._clock()
            write = asyncio.to_thread(save_book, self._book_path, snapshot)
            try:
                if timeout_s is None:
                    await write
                else:
                    await asyncio.wait_for(write, timeout_s)
            except asyncio.TimeoutError:
                logger.warning("lan_presence: the book did not finish saving within %.0f s — walking away",
                               timeout_s)
                return False
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                self._dirty = True
                logger.warning("lan_presence: could not save the book (%s)", exc)
                return False
        return True

    def _apply(self, seen: Dict[str, Dict[str, Any]], *, checked: Optional[Iterable[str]],
               source: str) -> List[Dict[str, Any]]:
        """One observation into the book; edges out to the moment. Marks the
        book dirty — and urgent when the house itself changed (a device never
        seen before, an arrival, a departure). Never touches the disk: that is
        ``_flush``, awaited by the async callers, off the loop thread."""
        self._ensure_loaded()
        stamped = {m: {**(info or {}), "source": source} for m, info in (seen or {}).items()}
        newcomers = [m for m in stamped if _norm_mac(m) not in self._book]
        new_book, edges = merge_scan(
            self._book, seen=stamped, now=self._clock(),
            absence_minutes=self.absence_minutes(), checked=checked, labels=self.labels(),
        )
        self._book = new_book
        self._last_observation_at = self._clock()
        self._dirty = True
        if edges or newcomers:
            self._urgent = True
        for e in edges:
            self._emit(e)
        return edges

    def _emit(self, edge: Dict[str, Any]) -> None:
        try:
            self._notice(edge["kind"], device=edge.get("device", ""), mac=edge.get("mac", ""),
                         absence_s=float(edge.get("absence_s") or 0.0))
        except Exception as exc:  # the book is the truth; the wake is a courtesy
            logger.debug("lan_presence: notice failed: %s", exc)

    # ── where she lives on the wire ──────────────────────────────────────
    async def _target(self) -> Tuple[Optional[str], Optional[str]]:
        if self._iface and self._subnet:
            return self._iface, self._subnet
        try:
            iface, subnet = await asyncio.to_thread(detect_iface_and_subnet)
        except Exception:
            iface, subnet = None, None
        self._iface = self._iface or iface
        self._subnet = self._subnet or subnet
        if not self._subnet and not self._target_warned:
            self._target_warned = True
            logger.warning("lan_presence: no primary subnet found (set LAN_PRESENCE_SUBNET) — the sense is inert")
        return self._iface, self._subnet

    # ── ticks ────────────────────────────────────────────────────────────
    async def tick_sweep(self) -> List[Dict[str, Any]]:
        """The confirmation sweep: complete evidence over the whole segment."""
        if not self.enabled():
            return []
        _, subnet = await self._target()
        if not subnet:
            return []
        try:
            res = await self._sweeper(subnet)
        except Exception as exc:
            logger.warning("lan_presence: sweep failed (%s); the book stands", exc)
            return []
        if isinstance(res, tuple):
            seen, complete = res[0], bool(res[1])
        else:
            seen, complete = res, True
        if seen is None:
            return []
        self._last_sweep_at = self._clock()
        if not seen and not complete:
            # The sweep could not look at all (no nmap, sudo refused, the
            # test interlock). That is not evidence and it is not a look:
            # never touch the book, the file, or the freshness clock —
            # render_line's honest "as of ..." tail depends on it.
            return []
        edges = self._apply(dict(seen), checked=None if complete else set(seen), source="sweep")
        await self._flush()
        return edges

    async def request_sweep(self, *, min_gap_s: float = 120.0) -> bool:
        """A sweep on purpose (the look): at most one per ``min_gap_s``."""
        if not self.enabled():
            return False
        if self._last_sweep_at > 0 and (self._clock() - self._last_sweep_at) < min_gap_s:
            return False
        await self.tick_sweep()
        return True

    async def tick_quiet_checks(self) -> List[Dict[str, Any]]:
        """Ask once after each device that has gone silent past the
        threshold; a miss is a departure, an answer is nothing at all,
        'could not ask' is not evidence. Offline devices are never asked."""
        if not self.enabled():
            return []
        self._ensure_loaded()
        now = self._clock()
        threshold_s = self.absence_minutes() * 60.0
        edges: List[Dict[str, Any]] = []
        for mac, rec in list(self._book.items()):
            if not rec.get("online"):
                continue
            if (now - float(rec.get("last_seen") or now)) < threshold_s:
                continue
            ip = str(rec.get("ip") or "").strip()
            if not ip:
                continue
            try:
                answered = await self._prober(ip)
            except Exception as exc:
                logger.debug("lan_presence: quiet-check of %s failed: %s", ip, exc)
                continue
            if answered is None:
                continue
            if answered:
                edges.extend(self._apply({mac: {"ip": ip}}, checked={mac}, source="probe"))
            else:
                edges.extend(self._apply({}, checked={mac}, source="probe"))
        await self._flush()
        return edges

    # ── lifecycle (the daydream shape) ───────────────────────────────────
    async def start(self) -> None:
        if self.is_running:
            return
        self.is_running = True
        self._task = asyncio.create_task(self._loop(), name="lan_presence")
        self._listener_task = asyncio.create_task(self._listener_loop(), name="lan_presence_listener")
        essential_logger.info("LAN presence engine started")

    async def stop(self) -> None:
        self.is_running = False
        for attr in ("_listener_task", "_task"):
            task = getattr(self, attr, None)
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
            setattr(self, attr, None)
        await self._flush(force=True, timeout_s=SAVE_SHUTDOWN_TIMEOUT_S)
        essential_logger.info("LAN presence engine stopped")

    async def _loop(self) -> None:
        await asyncio.sleep(BOOT_DELAY_S)
        while self.is_running:
            try:
                if not self.enabled():
                    await asyncio.sleep(60)
                    continue
                now = self._clock()
                if self._last_sweep_at <= 0 or (now - self._last_sweep_at) >= self.sweep_interval_s():
                    await self.tick_sweep()
                await self.tick_quiet_checks()
                await self._flush()            # twice a day regardless; nothing else routine touches the disk
                await asyncio.sleep(QUIET_CHECK_EVERY_S)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 — the loop must outlive any tick
                logger.warning("lan_presence: loop error (%s); backing off", exc)
                await asyncio.sleep(60)

    def get_status(self) -> Dict[str, Any]:
        return {
            "is_running": self.is_running, "enabled": self.enabled(),
            "iface": self._iface, "subnet": self._subnet,
            "devices": len(self._book) if self._loaded else None,
            "last_sweep_at": self._last_sweep_at, "last_observation_at": self._last_observation_at,
        }


lan_presence_engine = LanPresenceEngine()


# ═════════════════════════════════════════════════════════════════════════
# The listener — the sense itself (Task 6, spec §0.6: listen, don't scan)
# ═════════════════════════════════════════════════════════════════════════

LISTENER_BACKOFF_LADDER_S = (5, 10, 20, 40, 60)
DEAD_CAPTURE_RETRY_S = 900         # a capture that dies on arrival (sudo refused, no tcpdump)
DEAD_CAPTURE_GRACE_S = 5.0
LISTENER_BOOT_DELAY_S = 45         # sit out startup, exactly like the sweep loop
# The capture's own wall clock. `timeout` runs as root INSIDE sudo, so it
# can always kill its own tcpdump — the backstop that bounds an orphan's
# life even when her process dies violently and reaps nothing.
LISTENER_WINDOW_S = 3600
REAP_WAIT_S = 5.0                  # never block shutdown on a reap
LONG_CAPTURE_S = 60.0              # a capture that lived this long respawns at once


def listener_argv(iface: str) -> List[str]:
    """The capture, verbatim: bounded by a root ``timeout``, line-buffered,
    numeric, with link-level headers (the sender's MAC is the first on the
    line), ARP only. Nothing is ever sent; no capture file is ever written
    (-w is forbidden). The window is the orphan backstop — see
    LISTENER_WINDOW_S."""
    return ["sudo", "-n", "timeout", "-s", "TERM", str(LISTENER_WINDOW_S),
            "tcpdump", "-l", "-n", "-e", "-i", str(iface), "arp"]


def reap_argv(pid: int, *, sig: str = "TERM") -> List[str]:
    """Kill the capture's whole process group, as root.

    Why not the child's own terminate/kill: her process runs as ``ubuntu``
    and the capture runs as root, so an unprivileged signal is EPERM — it
    did nothing, the capture was orphaned, and ``proc.wait()`` then hung
    forever (2026-08-24: it hung a full test suite for 1h47m and would
    have hung her shutdown). Root signalling the negative pgid takes sudo,
    timeout and tcpdump down together."""
    return ["sudo", "-n", "kill", f"-{sig}", "--", f"-{int(pid)}"]


def under_test() -> bool:
    """True inside a pytest run. THE interlock for the house law "never a
    real capture or scan in a test": tests inject fakes, and the three
    functions that touch the network refuse outright regardless. Intent
    was not enough — tests/core/test_main.py boots the app, which started
    this engine for real (2026-08-24)."""
    return bool(os.environ.get("PYTEST_CURRENT_TEST") or os.environ.get("PYTEST_VERSION"))


def listener_backoff_s(attempt: int) -> int:
    """5 → 10 → 20 → 40 → 60, then a minute forever: restarted, never multiplied."""
    idx = max(0, min(int(attempt), len(LISTENER_BACKOFF_LADDER_S) - 1))
    return LISTENER_BACKOFF_LADDER_S[idx]
async def _spawn_listener(iface: str):
    """The default stream: one root tcpdump inside a root ``timeout``, its
    stdout line by line. The child gets its OWN session (start_new_session)
    so the whole group — sudo, timeout, tcpdump — can be reaped with one
    root signal; see reap_argv. stderr (the banner, or sudo's refusal) is
    dropped; a refusal shows as a capture that ends at once, which the
    supervisor backs off from."""
    if under_test():
        logger.debug("lan_presence: the listener never captures under a test harness")
        return                                # the interlock (house law)
    proc = await asyncio.create_subprocess_exec(
        *listener_argv(iface), stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        start_new_session=True,
    )
    try:
        assert proc.stdout is not None
        while True:
            raw = await proc.stdout.readline()
            if not raw:
                break
            yield raw.decode("utf-8", "replace").rstrip("\r\n")
    finally:
        await _reap(proc)


def _ingest_line(self: "LanPresenceEngine", line: str) -> List[Dict[str, Any]]:
    """One heard line → a PARTIAL observation of its sender (silence is
    not absence): a never-seen MAC is a silent new entry; a MAC the book
    had confirmed gone is an arrival; an online MAC is a refreshed
    last_seen. The disk is not touched here — the supervisor flushes after
    each line, and only real change (or the twice-a-day flush) lands. Never
    raises."""
    try:
        if not self.enabled():
            return []
        parsed = parse_tcpdump_arp(line)
        if not parsed:
            return []
        mac, ip = parsed
        info: Dict[str, Any] = {"ip": ip} if ip else {}
        return self._apply({mac: info}, checked={mac}, source="heard")
    except Exception as exc:  # noqa: BLE001 — the wire must never break the engine
        logger.debug("lan_presence: ingest failed: %s", exc)
        return []


async def _run_listener_once(self: "LanPresenceEngine") -> int:
    """Consume one capture stream to its end; returns the number of lines
    heard. Spawns nothing when the sense or its sub-switch is off. A
    stream that raises is swallowed; whatever was heard stays in the book
    (in memory — the close of a window is not a change worth a write)."""
    if not self.listener_enabled():
        return 0
    iface, _ = await self._target()
    if not iface:
        return 0
    stream = self._listener or _spawn_listener
    heard = 0
    try:
        async for line in stream(iface):
            heard += 1
            self.ingest_line(line)
            await self._flush()        # an arrival lands at once; a refresh waits for the twice-a-day flush
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.warning("lan_presence: the capture ended with an error (%s)", exc)
    return heard


async def _listener_loop(self: "LanPresenceEngine") -> None:
    """Supervise the capture: restart with backoff, never multiply; a
    capture that dies on arrival (sudo refused, tcpdump missing) is retried
    every 15 minutes and logged once."""
    attempt = 0
    dead = 0
    warned = False
    # Sit out startup exactly like the sweep loop — never compete with
    # boot, and never let a short-lived process (a test that boots the
    # app) reach a spawn at all.
    await asyncio.sleep(LISTENER_BOOT_DELAY_S)
    while self.is_running:
        if not self.listener_enabled():
            await asyncio.sleep(60)
            continue
        started = self._clock()
        heard = await self.run_listener_once()
        if not self.is_running:
            return
        lived = self._clock() - started
        if heard == 0 and lived < DEAD_CAPTURE_GRACE_S:
            dead += 1
            if dead >= 3 and not warned:
                warned = True
                logger.warning("lan_presence: the capture keeps dying on arrival (sudo -n tcpdump refused?) "
                               "— retrying every %d s; sweeps still run", DEAD_CAPTURE_RETRY_S)
            delay = DEAD_CAPTURE_RETRY_S if dead >= 3 else listener_backoff_s(attempt)
        elif lived >= LONG_CAPTURE_S:
            # a full window closed cleanly: respawn at once so the house's
            # chatter is never unheard for a backoff's length
            dead = 0
            delay = 0
        else:
            dead = 0
            delay = listener_backoff_s(attempt)
        attempt = attempt + 1 if heard == 0 else 0
        if delay:
            await asyncio.sleep(delay)


LanPresenceEngine.ingest_line = _ingest_line
LanPresenceEngine.run_listener_once = _run_listener_once
LanPresenceEngine._listener_loop = _listener_loop


# ═════════════════════════════════════════════════════════════════════════
# The rendered line — "the house right now" (Task 7)
# ═════════════════════════════════════════════════════════════════════════

STALE_AFTER_S = 30 * 60
NEW_WITHIN_S = 24 * 3600


def _sense_enabled() -> bool:
    try:
        comp = companion_settings()
        return bool(comp is not None and getattr(comp, "lan_presence", False))
    except Exception:
        return False


def _hhmm(ts: Any) -> str:
    try:
        return time.strftime("%H:%M", time.localtime(float(ts)))
    except (TypeError, ValueError, OSError):
        return "?"


def _human_ago(seconds: float) -> str:
    s = max(0.0, float(seconds))
    if s < 90 * 60:
        return f"{max(1, int(s // 60))} minutes ago"
    hours = s / 3600.0
    if hours < 1.5:
        return "about an hour ago"
    if hours < 24:
        return f"about {int(round(hours))} hours ago"
    days = hours / 24.0
    return "about a day ago" if days < 1.5 else f"about {int(round(days))} days ago"


def _label_for(mac: str, labels: Optional[Dict[str, str]]) -> str:
    mac = _norm_mac(mac)
    for k, v in (labels or {}).items():
        if _norm_mac(k) == mac and str(v or "").strip():
            return str(v).strip()
    return ""


def _count_word(n: int) -> str:
    return "one" if n == 1 else str(n)


def render_line(book: Optional[Dict[str, Dict[str, Any]]] = None, *,
                now: Optional[float] = None,
                labels: Optional[Dict[str, str]] = None,
                observed_at: Optional[float] = None) -> str:
    """One plain line for her senses — "the house right now: 9 devices
    online; the owner's phone home since 14:02; one device never seen before
    today" — or "" when the sense is off or the book is empty. The owner's
    labels name devices; unlabeled ones are only counted. An "as of" tail
    appears only when the last observation is older than 30 minutes."""
    if not _sense_enabled():
        return ""
    from_engine = book is None
    if from_engine:
        book = lan_presence_engine.book
    if not book:
        return ""
    if labels is None:
        labels = lan_presence_engine.labels()
    now = time.time() if now is None else float(now)
    online = {m: r for m, r in book.items() if r.get("online")}
    n = len(online)
    parts: List[str] = [f"{n} device{'s' if n != 1 else ''} online" if n else "no devices online"]
    home: List[str] = []
    away: List[str] = []
    for mac, rec in book.items():
        label = _label_for(mac, labels)
        if not label:
            continue
        if rec.get("online"):
            home.append(f"{label} home since {_hhmm(rec.get('online_since') or rec.get('first_seen'))}")
        else:
            away.append(f"{label} away since {_hhmm(rec.get('offline_since') or rec.get('last_seen'))}")
    parts.extend(sorted(home))
    parts.extend(sorted(away))
    new = [m for m, r in book.items() if float(r.get("first_seen") or 0.0) >= now - NEW_WITHIN_S]
    if new:
        parts.append(f"{_count_word(len(new))} device{'s' if len(new) != 1 else ''} never seen before today")
    line = "the house right now: " + "; ".join(parts)
    if observed_at is None:
        seen_times = [float(r.get("last_seen") or 0.0) for r in book.values()]
        observed_at = max(seen_times) if seen_times else 0.0
        if from_engine:
            observed_at = max(observed_at, float(lan_presence_engine.last_observation_at or 0.0))
    age = now - float(observed_at)
    if age > STALE_AFTER_S:
        line += f" (as of {_human_ago(age)})"
    return line
