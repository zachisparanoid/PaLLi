"""The address guard for reading the web (Batch 8, task 9; rulings T9-1, T9-6..T9-8).

``read_page`` fetches pages from addresses it is handed: by the owner, by a
search result, or by a page's own redirect. Pointed inward, the same request
would reach what runs on this machine and its network: her API on :8000, the
dashboards, file sharing, the router, a cloud metadata address. So a fetch
goes out only over IPv4, only to an address that is truly global and is not
one of this machine's own, and everything else is refused:

- loopback, private, link-local, multicast, reserved, unspecified, and anything
  ``ipaddress`` does not call global;
- the tailnet / carrier-grade NAT range ``100.64.0.0/10``, named explicitly
  (``is_private`` is False for it);
- **every IPv4 address this machine has** (all interfaces, read fresh on every
  check because addresses change; ruling T9-6). A box with a public address
  would otherwise be reachable through it;
- **IPv6** (ruling T9-6): a machine with a global IPv6 address is reachable on
  it, and so is every device in its /64 (the router at ``<prefix>::1``), and
  none of them look internal to ``ipaddress``. So an IPv6 address is refused,
  and a name's IPv6 (AAAA) answers are dropped. The one exception: IPv4-mapped
  (``::ffff:a.b.c.d``) and NAT64 (``64:ff9b::/96``) addresses have an IPv4
  address inside, and are judged by it;
- ``localhost`` and ``*.localhost`` by name, before any lookup;
- a name with even one inside IPv4 address among its answers.

A host is one of three things (``check_host``, ruling T9-7): ``PUBLIC``,
``INTERNAL``, or ``UNRESOLVABLE`` — a name with no IPv4 answer at all, which
is a mistyped or dead address (or an IPv6-only site), not an internal one.

Two layers use it:

- ``check_host(host)`` / ``is_public_address(host)``, the plain check, run on
  the first URL and on every redirect (``read_page`` follows redirects by
  hand, at most 5);
- ``GuardingResolver``, the aiohttp resolver on the reader's connector. It
  resolves, keeps the IPv4 answers, checks them, and hands aiohttp only those,
  so the address a connection is made to is always one that passed. A name
  that answers "public" to the plain check and "inside" a moment later (DNS
  rebinding) is refused here, before any socket opens. aiohttp skips the
  resolver for anything it takes for an address literal (any host with a
  colon, or only digits and dots); a literal cannot change between the two
  looks, so the plain check covers those, and ``read_page`` refuses any such
  host the guard cannot read as an address (ruling T9-8).

No port rule (ruling T9-2). What it does not cover: a router that forwards a
port on its own public address back to this machine. That address is public
by every rule here (recorded in the Batch 8 ledger, not hidden).
"""
from __future__ import annotations

import asyncio
import ipaddress
import logging
import re
import socket
import struct
from typing import FrozenSet, Iterable, List, Optional, Union

from aiohttp.abc import AbstractResolver, ResolveResult

try:  # POSIX only; elsewhere the interface read falls back (logged once)
    import fcntl
except ImportError:  # pragma: no cover - not on Linux
    fcntl = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

_Address = Union[ipaddress.IPv4Address, ipaddress.IPv6Address]

PUBLIC = "public"
INTERNAL = "internal"
UNRESOLVABLE = "unresolvable"

_TAILNET = ipaddress.ip_network("100.64.0.0/10")
_NAT64 = ipaddress.ip_network("64:ff9b::/96")
# The old numeric IPv4 spellings getaddrinfo still accepts ("127.1",
# "2130706433", "0x7f.1"): read as the address they name, never sent to DNS.
_LEGACY_IPV4 = re.compile(r"^[0-9a-fA-FxX.]+$")
_SIOCGIFADDR = 0x8915            # Linux: an interface's IPv4 address
_FIB_TRIE = "/proc/net/fib_trie"  # Linux: every local address, secondaries too
_warned_interfaces = False
_warned_empty = False


class InternalAddressRefused(OSError):
    """The connect-time guard refused a name: it resolved to an address on
    this machine or a private network. An ``OSError`` so aiohttp treats it as
    a failed lookup (it arrives wrapped in ``ClientConnectorDNSError``)."""

    def __init__(self, host: str, addresses: List[str]) -> None:
        super().__init__(f"{host} points to an internal address")
        self.host = host
        self.addresses = list(addresses)


class HostNotFound(OSError):
    """The connect-time guard found no IPv4 address for a name (it does not
    resolve, or it has IPv6 answers only) — the reader says it couldn't find
    that address, never that it is internal (ruling T9-7)."""

    def __init__(self, host: str) -> None:
        super().__init__(f"couldn't find {host}: it has no IPv4 address")
        self.host = host


def _literal(text: str) -> Optional[_Address]:
    """The address ``text`` spells, or None when it is a name."""
    try:
        return ipaddress.ip_address(text)
    except ValueError:
        pass
    if _LEGACY_IPV4.match(text):
        try:
            return ipaddress.IPv4Address(socket.inet_aton(text))
        except OSError:
            return None
    return None


def literal_address(host: str) -> Optional[_Address]:
    """The address a URL host spells (brackets allowed), or None for a name."""
    return _literal((host or "").strip().strip("[]"))


def ipv4_inside(ip: _Address) -> Optional[ipaddress.IPv4Address]:
    """The IPv4 address ``ip`` stands for: itself, or the one inside an
    IPv4-mapped or NAT64 address. None for any other IPv6 address."""
    if isinstance(ip, ipaddress.IPv4Address):
        return ip
    if ip.ipv4_mapped is not None:
        return ip.ipv4_mapped
    if ip in _NAT64:
        return ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)
    return None


def _parse_fib_trie(text: str) -> set:
    """The local addresses in /proc/net/fib_trie: each ``|-- a.b.c.d`` whose
    next line says ``/32 host LOCAL``."""
    found: set = set()
    last = None
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("|--"):
            last = stripped[3:].strip()
        elif last and "/32 host LOCAL" in stripped:
            found.add(last)
    return found


def _interface_ipv4_addresses() -> set:
    """Each interface's IPv4 address (Linux ioctl). Raises where it can't run."""
    if fcntl is None:
        raise OSError("fcntl is not available on this platform")
    found: set = set()
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        for _index, name in socket.if_nameindex():
            request = struct.pack("256s", name.encode("utf-8")[:15])
            try:
                reply = fcntl.ioctl(sock.fileno(), _SIOCGIFADDR, request)
            except OSError:
                continue                # this interface has no IPv4 address
            found.add(socket.inet_ntoa(reply[20:24]))
    return found


def _own_ipv4_addresses() -> FrozenSet[str]:
    """Every IPv4 address this machine has, read now (tests replace this).

    Linux: each interface (``SIOCGIFADDR``) plus every local address in
    /proc/net/fib_trie (which also lists secondary addresses). Elsewhere, best
    effort: the addresses the machine's own name resolves to (plus fib_trie's,
    when it can be read), with a warning logged once that the full interface
    list could not be read — naming what was read instead.

    An empty table still fails open (the reader runs, refusing every private,
    loopback and tailnet address as always), but never silently: that is
    said once too (final fix wave, ruling FW-14)."""
    global _warned_interfaces, _warned_empty
    found: set = set()
    no_interfaces: Optional[Exception] = None
    try:
        found |= _interface_ipv4_addresses()
    except Exception as exc:  # noqa: BLE001 — best effort, said once
        no_interfaces = exc
        try:
            found |= set(socket.gethostbyname_ex(socket.gethostname())[2])
        except OSError:
            pass
    fib_read = False
    try:
        with open(_FIB_TRIE, encoding="utf-8", errors="replace") as handle:
            found |= _parse_fib_trie(handle.read())
        fib_read = True
    except OSError:
        pass
    if no_interfaces is not None and not _warned_interfaces:
        _warned_interfaces = True
        logger.warning(
            "read_page: could not list this machine's interface addresses (%s); "
            "refusing %s", no_interfaces,
            ("the local addresses /proc/net/fib_trie lists and the ones its own "
             "name resolves to") if fib_read else "only the ones its own name resolves to",
        )
    if not found and not _warned_empty:
        _warned_empty = True
        logger.warning(
            "read_page: found none of this machine's own addresses; a page at one of "
            "its public addresses would not be refused (private, loopback and tailnet "
            "addresses still are)",
        )
    return frozenset(found)


def address_is_public(
    address: Union[str, _Address], *, own: Optional[Iterable[str]] = None,
) -> bool:
    """True only for a truly global IPv4 address (or an IPv4-mapped / NAT64
    form of one) that is not one of this machine's own. ``own`` is the
    machine's address table; read fresh when not given."""
    ip = literal_address(address) if isinstance(address, str) else address
    if ip is None:
        return False
    v4 = ipv4_inside(ip)
    if v4 is None or v4 in _TAILNET:
        return False
    if (
        not v4.is_global
        or v4.is_multicast
        or v4.is_reserved
        or v4.is_unspecified
        or v4.is_loopback
        or v4.is_link_local
        or v4.is_private
    ):
        return False
    own_table = _own_ipv4_addresses() if own is None else own
    return str(v4) not in set(own_table)


def _is_localhost_name(name: str) -> bool:
    lowered = name.rstrip(".").lower()
    return lowered == "localhost" or lowered.endswith(".localhost")


def _ipv4_answers(addresses: Iterable[str]) -> List[str]:
    """A name's IPv4 answers; its IPv6 (AAAA) answers are dropped."""
    kept: List[str] = []
    for address in addresses:
        try:
            if ipaddress.ip_address(str(address).split("%", 1)[0]).version == 4:
                kept.append(str(address))
        except ValueError:
            continue
    return kept


def _resolve(host: str) -> List[str]:
    """Every address ``host`` resolves to (tests replace this)."""
    infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    found: List[str] = []
    for _family, _type, _proto, _canon, sockaddr in infos:
        address = str(sockaddr[0])
        if address not in found:
            found.append(address)
    return found


def check_host(host: str) -> str:
    """PUBLIC, INTERNAL or UNRESOLVABLE. A literal is judged as itself; a
    name by its IPv4 answers (all must be public; none at all is
    UNRESOLVABLE)."""
    name = (host or "").strip().strip("[]")
    if not name:
        return UNRESOLVABLE
    ip = _literal(name)
    if ip is not None:
        return PUBLIC if address_is_public(ip) else INTERNAL
    if _is_localhost_name(name):
        return INTERNAL
    try:
        answers = _ipv4_answers(_resolve(name))
    except Exception:  # noqa: BLE001 — no answer is an outcome, never a raise
        return UNRESOLVABLE
    if not answers:
        return UNRESOLVABLE
    own = _own_ipv4_addresses()
    return PUBLIC if all(address_is_public(a, own=own) for a in answers) else INTERNAL


def is_public_address(host: str) -> bool:
    """The plain check as a yes/no: may a fetch go to ``host``?"""
    return check_host(host) == PUBLIC


class GuardingResolver(AbstractResolver):
    """aiohttp's resolver for the page reader: resolve, keep the IPv4
    answers, refuse unless every one is public and not this machine's, and
    hand back only the checked addresses."""

    async def resolve(
        self, host: str, port: int = 0, family: socket.AddressFamily = socket.AF_INET,
    ) -> List[ResolveResult]:
        name = (host or "").strip().strip("[]")
        ip = _literal(name)
        if ip is not None:
            answers = [str(ip)]
        elif _is_localhost_name(name):
            raise InternalAddressRefused(host, [name])
        else:
            try:
                answers = await asyncio.to_thread(_resolve, name)
            except Exception as exc:  # noqa: BLE001 — aiohttp expects OSError here
                raise HostNotFound(host) from exc
        if ip is None:
            answers = _ipv4_answers(answers)
        if not answers:
            raise HostNotFound(host)
        own = await asyncio.to_thread(_own_ipv4_addresses)
        refused = [a for a in answers if not address_is_public(a, own=own)]
        if refused:
            raise InternalAddressRefused(host, refused)

        results: List[ResolveResult] = []
        for address in answers:
            parsed = ipaddress.ip_address(address)
            fam = socket.AF_INET6 if parsed.version == 6 else socket.AF_INET
            if family not in (0, socket.AF_UNSPEC) and fam != family:
                continue
            results.append(ResolveResult(
                hostname=host,
                host=str(parsed),
                port=port,
                family=fam,
                proto=0,
                flags=socket.AI_NUMERICHOST | socket.AI_NUMERICSERV,
            ))
        if not results:
            raise HostNotFound(host)
        return results

    async def close(self) -> None:
        return None
