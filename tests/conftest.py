from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

import pytest


# Ensure repository root is importable when running tests directly.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


# --- Global live-data isolation (must stay ABOVE any project import) -------
# Tests must never touch the owner's live state. Observed leaks before this
# guard: a 2026-07 suite run wrote 233 rows into data/palli-local.sqlite3,
# and integration turns appended REAL LLM conversations to the owner's live
# dashboard transcript (data/agent_sessions/dashboard_1.jsonl) and rewrote
# the live relationship-momentum file (data/companion/relationship_1.json).
#
# The DATABASE_PATH env var alone is NOT enough: core/config.py runs
# load_dotenv(override=True) and the owner's .env pins DATABASE_PATH to the
# live file, clobbering anything exported before import. So we do both: set
# env vars for direct-env readers, then import config and hard-set the
# attribute post-dotenv. Every runtime reader (core/database.py,
# plugin_runtime, legacy_residue, turn/proactive) resolves the path via
# getattr(config, "DATABASE_PATH") at call time, so the attribute patch wins
# everywhere. Tests that pass an explicit db_path are unaffected.
TEST_DATA_ROOT = tempfile.mkdtemp(prefix="palli-test-data-")
TEST_DATABASE_PATH = os.path.join(TEST_DATA_ROOT, "palli-test.sqlite3")
# Sentinel consumed by core/config.py: under it, load_dotenv stops overriding
# process env, so these redirects SURVIVE INTO SUBPROCESSES a test-driven
# turn may spawn. Without it, a spawned child re-imported config, dotenv
# clobbered the inherited DATABASE_PATH back to the live file, and the child
# wrote the owner's real database — invisible to every in-process guard
# below (this happened three suite runs in a row on 2026-07-09).
os.environ["PALLI_TEST_DATA_ROOT"] = TEST_DATA_ROOT
os.environ["DATABASE_PATH"] = TEST_DATABASE_PATH
# The default vault dir (~/.palli/vault) holds the owner's REAL vault keys on
# this host; the suite must never read them. Point it at a path that does not
# exist — the vault tests monkeypatch sr.VAULT_DIR themselves and are unaffected.
os.environ["PALLI_VAULT_DIR"] = os.path.join(TEST_DATA_ROOT, "no-vault")
# Identity documents are the FOURTH live-state door (found 2026-07-10 when a
# behavior-harness turn's reflect pass wrote a fake "jointers vs
# planers" thread into identity/owners/1/MEMORY.md — it landed in PaLLi's
# live system prompt and tripped the excision's golden-prompt check). Tests
# get a disposable COPY of the real tree: reads behave identically, writes
# land in tmp. Env-first so the identity_service singleton (which bakes its
# root at import) and any SUBPROCESS both resolve the copy; the
# PALLI_TEST_DATA_ROOT sentinel keeps load_dotenv from clobbering it.
TEST_IDENTITY_ROOT = os.path.join(TEST_DATA_ROOT, "identity")
shutil.copytree(
    os.path.join(str(ROOT), "identity"), TEST_IDENTITY_ROOT, dirs_exist_ok=True
)
os.environ["IDENTITY_ROOT_PATH"] = TEST_IDENTITY_ROOT


# Absolute backstop: refuse ANY sqlite connection to the live database from
# inside a test process, whatever code path builds it. Both 2026-07-09
# full-suite runs wrote a real reminder ("Call the co-op") into the owner's
# live scheduled_reminders through a path the attribute guards below never
# saw (config, Config, and brain.db_path stayed pointed at tmp for all 2900+
# tests — the per-test canary proved it). sys.addaudithook fires on every
# sqlite3.connect (aiosqlite included), so the offender now dies loudly WITH
# a stack trace instead of silently polluting PaLLi's memory of the owner.
_LIVE_DB_REAL = str((ROOT / "data" / "palli-local.sqlite3").resolve())
# The record of every message (R15, memory that reaches) is live state too:
# PALLI_MEMORY_REACH_PATH below is the redirect, this is its backstop.
_LIVE_MEMORY_REACH_REAL = str((ROOT / "data" / "memory_reach.sqlite3").resolve())
_LIVE_DBS_REAL = frozenset({_LIVE_DB_REAL, _LIVE_MEMORY_REACH_REAL})


def _deny_live_db_connections(event: str, args: tuple) -> None:
    if event != "sqlite3.connect" or not args:
        return
    raw = args[0]
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", "replace")
    path = str(raw or "")
    if path.startswith("file:"):
        path = path[5:].split("?", 1)[0]
    if not path or path == ":memory:":
        return
    try:
        resolved = str(Path(path).resolve())
    except Exception:
        return
    if resolved in _LIVE_DBS_REAL:
        raise RuntimeError(
            "BLOCKED by tests/conftest.py: a test attempted to open a LIVE "
            f"PaLLi database ({raw!r}). Tests must use the tmp test DB."
        )


sys.addaudithook(_deny_live_db_connections)
# Honored by core/agent/dispatch.py (session transcripts) and
# core/agent/tools.py (tool-result sidecars); live runs leave these unset.
os.environ["PALLI_AGENT_SESSIONS_ROOT"] = os.path.join(
    TEST_DATA_ROOT, "agent_sessions"
)
os.environ["TOOL_RESULT_SIDECAR_ROOT"] = os.path.join(TEST_DATA_ROOT, "tool_results")
# Honored by ai/forge_manifest.py (step 34): the forge's manifest, installed
# tools, and filings all resolve under this root — no test ever touches the
# live data/forge/ install base.
os.environ["FORGE_ROOT"] = os.path.join(TEST_DATA_ROOT, "forge")
# Honored by core/collaborators.py (Batch 8, task 1): the owner's list of
# collaborators for shared rooms. Env-first so a subprocess resolves it too;
# no test ever reads or writes the live data/collaborators.json. The
# _collaborators_store_hermetic fixture below empties it after every test.
TEST_COLLABORATORS_PATH = os.path.join(TEST_DATA_ROOT, "collaborators.json")
os.environ["PALLI_COLLABORATORS_PATH"] = TEST_COLLABORATORS_PATH
# Honored by core/quiet_record.py (Batch 8, task 2a — ruling T2-4, G3): her
# record of quiet choices. Env-first so a subprocess resolves it too; no test
# ever reads or writes the live data/quiet_choices/. The
# _quiet_record_hermetic fixture below empties it after every test.
TEST_QUIET_CHOICES_DIR = os.path.join(TEST_DATA_ROOT, "quiet_choices")
os.environ["PALLI_QUIET_CHOICES_DIR"] = TEST_QUIET_CHOICES_DIR
# Honored by core/room_notes.py (Batch 8, task 2b — ruling T2b-5, G3): the
# owner's room notes (INTERIM). Env-first so a subprocess resolves it too; no
# test ever reads or writes the live data/room_notes.json. The
# _room_notes_hermetic fixture below removes it after every test.
TEST_ROOM_NOTES_PATH = os.path.join(TEST_DATA_ROOT, "room_notes.json")
os.environ["PALLI_ROOM_NOTES_PATH"] = TEST_ROOM_NOTES_PATH
# Honored by core/room_people.py (the room-reading fix, 2026-10-12): the
# owner's corrections about names in a room. Same rules as the room notes
# above; the _room_notes_hermetic fixture removes it after every test.
TEST_ROOM_PEOPLE_PATH = os.path.join(TEST_DATA_ROOT, "room_people.json")
os.environ["PALLI_ROOM_PEOPLE_PATH"] = TEST_ROOM_PEOPLE_PATH
# Honored by core/turn/group_openers.py (the presence slice, 2026-10-13): the
# group openers' ledger and look state. No test reads or writes the live
# data/group_openers/.
os.environ["PALLI_GROUP_OPENERS_DIR"] = os.path.join(TEST_DATA_ROOT, "group_openers")
# Honored by core/voice_replies.py (Batch 8, task 10 — ruling T10-9, G3): the
# remembered voice-replies switches. Env-first so a subprocess resolves it
# too; no test ever reads or writes the live data/voice_replies.json. The
# _voice_replies_hermetic fixture below removes it after every test.
TEST_VOICE_REPLIES_PATH = os.path.join(TEST_DATA_ROOT, "voice_replies.json")
os.environ["PALLI_VOICE_REPLIES_PATH"] = TEST_VOICE_REPLIES_PATH
# Honored by core/spending.py (owner decision T2b-6, 2026-10-04): the
# spending check's balance readings and alerts. Env-first so a subprocess
# resolves it too; no test ever reads or writes the live data/spending/. The
# _spending_hermetic fixture below empties it after every test.
TEST_SPENDING_DIR = os.path.join(TEST_DATA_ROOT, "spending")
os.environ["PALLI_SPENDING_DIR"] = TEST_SPENDING_DIR
# Honored by ai/memory_reach_index.py (R15, memory that reaches): the record
# of every message. Env-first so a subprocess resolves it too; no test ever
# reads or writes the live data/memory_reach.sqlite3 (the audit hook above
# refuses it).
os.environ["PALLI_MEMORY_REACH_PATH"] = os.path.join(TEST_DATA_ROOT, "memory_reach.sqlite3")
# Honored by core/reply_sources.py (R15, memory that reaches, T13): what each
# reply drew on — the owner's private lines. Env-first so a subprocess (and
# the dashboards' collector) resolves it too; no test ever reads or writes
# the live data/reply_sources/. The _reply_sources_hermetic fixture below
# empties it after every test.
TEST_REPLY_SOURCES_DIR = os.path.join(TEST_DATA_ROOT, "reply_sources")
os.environ["PALLI_REPLY_SOURCES_DIR"] = TEST_REPLY_SOURCES_DIR
# The suite never PONGs a real brain and never reads a real account balance.
# Honored by core/launcher_support.py's live probes; setdefault so a test that
# wants the online path can still delenv it for its own scope.
os.environ.setdefault("PALLI_PREFLIGHT_OFFLINE", "1")


# --- No network in tests (fix plan 6.7, Batch 7 task 1) ---------------------
# The suite may talk to this machine (local test servers, a local model on
# 127.0.0.1) and to nothing else. Installed HERE, at import, like the doors
# above — not as a session fixture: monkeypatch is function-scoped, and the
# imports collection runs would go unguarded.
#
#   1. connect / connect_ex on every AF_INET / AF_INET6 socket refuse an
#      address that is not this machine with NetworkBlocked. It is a
#      ConnectionRefusedError, so httpx, requests, aiohttp and openai take
#      their offline paths, and a RuntimeError, so
#      pytest.raises(RuntimeError, match="network") still matches. Every other
#      family (AF_UNIX, netlink, ...) passes untouched.
#   2. socket.getaddrinfo refuses a name that is neither numeric nor
#      localhost / *.localhost with socket.gaierror: a connect-only guard
#      still lets DNS out.
#   3. Every refusal is RECORDED against the test that made it, listed at the
#      end of the run, and FAILS THE SESSION (pytest_sessionfinish below).
#      PaLLi degrades through `except Exception` almost everywhere, so a
#      blocked call rarely fails its own test; the record is what finds it.
#      Fix an offender by mocking its HTTP layer or setting the offline env it
#      honours — never by deleting the test.
#
# Opt out with PALLI_TESTS_ALLOW_NETWORK=1, read at every call. Two live runs
# need it:
#   - scripts/probe_battery.py sets it for its own pytest child: the battery
#     is the one suite that must reach DeepSeek;
#   - the companion harness's live opt-in, RUN_COMPANION_HARNESS=1
#     (tests/behavior/test_companion_regression_harness.py): whoever runs it
#     must set PALLI_TESTS_ALLOW_NETWORK=1 as well.
# Not covered: child processes a test spawns (they inherit the offline env
# below and PALLI_PREFLIGHT_OFFLINE, not this patch), and C-level clients that
# never call socket.socket.connect (uvloop, which nothing here selects).
import ipaddress as _ipaddress  # noqa: E402
import socket as _socket  # noqa: E402
import threading as _threading  # noqa: E402
from collections import Counter as _Counter  # noqa: E402
from typing import NamedTuple as _NamedTuple  # noqa: E402

_ALLOW_NETWORK_ENV = "PALLI_TESTS_ALLOW_NETWORK"


def _network_allowed() -> bool:
    return os.environ.get(_ALLOW_NETWORK_ENV, "").strip() == "1"


if not _network_allowed():
    # The model hubs' own offline switches, set before anything imports
    # huggingface_hub (it reads them at import): a cached sentence-transformers
    # model loads from disk instead of asking huggingface.co first, and an
    # uncached one (CI) fails fast and degrades exactly as it does when blocked.
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")


class NetworkBlocked(ConnectionRefusedError, RuntimeError):
    """A test tried to reach a machine other than this one."""


class _NetworkAttempt(_NamedTuple):
    kind: str      # "connect" or "dns"
    address: str
    test: str      # PYTEST_CURRENT_TEST when it happened: "<nodeid> (<phase>)"


_NETWORK_ATTEMPTS: list = []
_NETWORK_ATTEMPTS_LOCK = _threading.Lock()
_OUTSIDE_A_TEST = "(outside any test: collection, an import, or session teardown)"
_REAL_SOCKET_CONNECT = _socket.socket.connect
_REAL_SOCKET_CONNECT_EX = _socket.socket.connect_ex
_REAL_GETADDRINFO = _socket.getaddrinfo


def _bare_host(host) -> str:
    """A host as connect or getaddrinfo was given it, lowercased, without
    brackets, an IPv6 %scope, or a name's trailing dot."""
    if isinstance(host, (bytes, bytearray)):
        host = bytes(host).decode("ascii", "replace")
    name = str(host).strip().lower()
    if name.startswith("[") and name.endswith("]"):
        name = name[1:-1]
    name = name.split("%", 1)[0]
    if name.endswith(".") and not name.endswith(".."):
        name = name[:-1]
    return name


def _parse_ip(name: str):
    """An address in any spelling the C resolver accepts ("127.1" and
    "2130706433" included), or None for a name."""
    try:
        return _ipaddress.ip_address(name)
    except ValueError:
        pass
    try:
        return _ipaddress.IPv4Address(_socket.inet_aton(name))
    except (OSError, ValueError):
        return None


def _is_this_machine(host) -> bool:
    if host is None:              # getaddrinfo(None, ...): loopback / passive
        return True
    if not isinstance(host, (str, bytes, bytearray)):
        return False
    name = _bare_host(host)
    if name in ("", "localhost") or name.endswith(".localhost"):
        return True               # "" is INADDR_ANY, which reaches this host
    ip = _parse_ip(name)
    if ip is None:
        return False
    if ip.version == 6 and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return ip.is_loopback or ip.is_unspecified   # 0.0.0.0 / :: reach this host on Linux


def _blocked_address(family, address):
    """The off-box address this connect would reach, as "host:port", or None
    when it may go ahead (this machine, a non-IP family, or the opt-out)."""
    if _network_allowed():
        return None
    if family not in (_socket.AF_INET, _socket.AF_INET6):
        return None
    if not isinstance(address, tuple) or not address:
        return None               # the real connect raises its own TypeError
    host = address[0]
    if _is_this_machine(host):
        return None
    port = address[1] if len(address) > 1 else "?"
    shown = host.decode("ascii", "replace") if isinstance(host, (bytes, bytearray)) else str(host)
    return f"[{shown}]:{port}" if ":" in shown else f"{shown}:{port}"


def _blocked_lookup(host):
    """The name this getaddrinfo would ask DNS about, or None when it never
    leaves the machine (numeric, localhost) or the opt-out is set."""
    if _network_allowed() or _is_this_machine(host):
        return None
    if isinstance(host, (str, bytes, bytearray)) and _parse_ip(_bare_host(host)) is not None:
        return None               # numeric: no lookup happens; the connect guard judges it
    if isinstance(host, (bytes, bytearray)):
        return bytes(host).decode("ascii", "replace")
    return str(host)


def _record_network_attempt(kind: str, address: str) -> None:
    test = os.environ.get("PYTEST_CURRENT_TEST") or _OUTSIDE_A_TEST
    with _NETWORK_ATTEMPTS_LOCK:
        _NETWORK_ATTEMPTS.append(_NetworkAttempt(kind, address, test))


def _refuse_connect(address: str):
    _record_network_attempt("connect", address)
    raise NetworkBlocked(f"network access in tests: {address}")


def _guarded_connect(self, address):
    blocked = _blocked_address(self.family, address)
    if blocked is not None:
        _refuse_connect(blocked)
    return _REAL_SOCKET_CONNECT(self, address)


def _guarded_connect_ex(self, address):
    blocked = _blocked_address(self.family, address)
    if blocked is not None:
        _refuse_connect(blocked)
    return _REAL_SOCKET_CONNECT_EX(self, address)


def _guarded_getaddrinfo(host, port, *args, **kwargs):
    blocked = _blocked_lookup(host)
    if blocked is not None:
        shown = f"{blocked}:{port}" if port not in (None, "", b"") else blocked
        _record_network_attempt("dns", shown)
        raise _socket.gaierror(_socket.EAI_NONAME, f"network access in tests: {shown}")
    return _REAL_GETADDRINFO(host, port, *args, **kwargs)


# socket.socket is a Python subclass of _socket.socket, so assigning on the
# class covers every instance, existing ones and ssl.SSLSocket included;
# asyncio's sock_connect and socket.create_connection both go through it, and
# both look socket.getaddrinfo up at call time.
_socket.socket.connect = _guarded_connect
_socket.socket.connect_ex = _guarded_connect_ex
_socket.getaddrinfo = _guarded_getaddrinfo


def pytest_terminal_summary(terminalreporter, exitstatus, config):
    if not _NETWORK_ATTEMPTS:
        return
    with _NETWORK_ATTEMPTS_LOCK:
        attempts = list(_NETWORK_ATTEMPTS)
    by_test: dict = {}
    for attempt in attempts:
        by_test.setdefault(attempt.test, _Counter())[(attempt.kind, attempt.address)] += 1
    tr = terminalreporter
    tr.section(
        f"network guard: {len(attempts)} blocked attempt(s) in {len(by_test)} test(s) "
        "— the session fails (tests/conftest.py, fix plan 6.7)",
        sep="=", red=True, bold=True,
    )
    for test, counts in by_test.items():
        tr.line(test)
        for (kind, address), n in counts.items():
            tr.line(f"    {kind:<7} {address}" + (f"  (x{n})" if n > 1 else ""))
    tr.line(
        "Mock the test's HTTP layer, or set the offline env it honours; never delete "
        "the test. A live run opts out with PALLI_TESTS_ALLOW_NETWORK=1."
    )


def pytest_sessionfinish(session, exitstatus):
    if _NETWORK_ATTEMPTS and session.exitstatus == pytest.ExitCode.OK:
        session.exitstatus = pytest.ExitCode.TESTS_FAILED


class _NetworkGuardHandle:
    """The guard's handles for tests/test_no_network.py. ``attempts`` are the
    records this test made; ``release()`` (run at the fixture's teardown)
    removes them, so the guard's own tests never fail the session."""

    Blocked = NetworkBlocked
    blocked_address = staticmethod(_blocked_address)
    blocked_lookup = staticmethod(_blocked_lookup)

    def __init__(self, nodeid: str) -> None:
        self._nodeid = nodeid

    def _mine(self, attempt) -> bool:
        return attempt.test.rsplit(" (", 1)[0] == self._nodeid

    @property
    def attempts(self) -> list:
        with _NETWORK_ATTEMPTS_LOCK:
            return [a for a in _NETWORK_ATTEMPTS if self._mine(a)]

    def release(self) -> None:
        with _NETWORK_ATTEMPTS_LOCK:
            _NETWORK_ATTEMPTS[:] = [a for a in _NETWORK_ATTEMPTS if not self._mine(a)]


@pytest.fixture
def network_guard(request):
    """For a test that trips the network guard ON PURPOSE: it sees its own
    records, and they are removed when it ends."""
    handle = _NetworkGuardHandle(request.node.nodeid)
    yield handle
    handle.release()


from core.config import Config as _Config, config as _config  # noqa: E402

_Config.DATABASE_PATH = TEST_DATABASE_PATH
_config.DATABASE_PATH = TEST_DATABASE_PATH
_Config.IDENTITY_ROOT_PATH = TEST_IDENTITY_ROOT
_config.IDENTITY_ROOT_PATH = TEST_IDENTITY_ROOT

# Relationship momentum persists per-owner JSON under a module-level dir
# constant (no config/env knob) — point it at the tmp root. _path() reads
# the module global at call time, so this redirect covers load and save.
import ai.relationship_momentum as _relationship_momentum  # noqa: E402

_relationship_momentum._STATE_DIR = Path(TEST_DATA_ROOT) / "companion"

# Grounded Vulnerability's book (organ 4.9, 2026-09-01): same shape — a
# module-level dir constant with no config/env knob, and _resolve_root()
# reads it at call time, so this redirect covers plant, fold and fade.
# Rotated the day the organ's first WRITER landed (the night harvest):
# seven tests in tests/ai/test_night_cycle.py drive _run_once for real, and
# the harvest step inside it is not faked.
import ai.disclosure_store as _disclosure_store  # noqa: E402

_disclosure_store.DISCLOSURES_DIR = Path(TEST_DATA_ROOT) / "disclosures"

# Shared Idiolect's book (organ 4.12, 2026-09-02): the disclosure shape —
# a module-level dir constant read at call time by _resolve_root(), so this
# one redirect covers plant, fold and retire. Rotated from task 1, before
# the organ's first writer exists (the 4.9 lesson: register the day the
# store lands, not the day a writer does).
import ai.callbacks_store as _callbacks_store  # noqa: E402

_callbacks_store.CALLBACKS_DIR = Path(TEST_DATA_ROOT) / "callbacks"

# The settings yaml (step 28, 2026-09-06) — the NEWEST live-state door, and the first
# that is a WRITE target rather than only a read. core.settings_boot.boot_settings()
# now seeds a missing config from data/palli.example.yaml, and
# tests/core/test_settings_boot.py calls boot_settings() with no argument: that
# resolved the owner's real data/palli.yaml. With the seed's exists() guard removed
# (a mutant, a refactor, a bad merge) the call REWRITES his live config — it did,
# once, during this build's mutation round on 2026-09-06.
#
# Both halves are needed: the module-level `settings_service` is constructed AT IMPORT
# from the old constant, so redirecting the constant alone would leave the service
# still pointed at the real file (and boot_settings seeds service._yaml_path).
#
# _EXAMPLE_YAML_PATH is deliberately left at the repo's example: the first
# boot_settings() in a test process then seeds INTO the tmp root, which is also
# exactly how the suite runs in a public clone, where data/palli.yaml does not exist
# at all. Snapshot reads change only in the four hardware-bound flags the example
# turns off (outward_senses, lan_presence, curiosity_nas_dives, arbiter_voice_says) —
# every test that reads those already pins them itself.
import core.settings_boot as _settings_boot  # noqa: E402
from core.settings_service import SettingsService as _SettingsService  # noqa: E402

TEST_SETTINGS_YAML = Path(TEST_DATA_ROOT) / "palli.yaml"
_settings_boot._DEFAULT_YAML_PATH = TEST_SETTINGS_YAML
_settings_boot.settings_service = _SettingsService(yaml_path=TEST_SETTINGS_YAML)
# Seed it NOW, not at the first boot_settings(): snapshot() LAZY-loads
# (settings_service.py:121-124), and tests/core/test_config_*_yaml_canonical.py read the
# singleton without ever booting — they ran before test_settings_boot.py in the suite
# order and got "settings file not found". Seeding here restores exactly the pre-redirect
# world (a config that is simply there), through the shipped seed itself.
_settings_boot.seed_from_example(TEST_SETTINGS_YAML)

# The second half of that door, and the same shape as the live-DB backstop above: the
# redirects are the guard, this is the BACKSTOP. Whatever code path a test (or a mutant,
# or a refactor) builds, the owner's real data/palli.yaml may be READ — four test files
# open it as a read-only fixture — and may never be WRITTEN. The mutation round on
# 2026-09-06 rewrote it once; nothing in the harness said no.
#
# Cheap by construction: the basename check comes first, so the hook does nothing at all
# for the tens of thousands of other opens a suite run makes. Both doors are covered —
# a write-mode open, and a rename ONTO the file, which is how an atomic write lands.
_LIVE_SETTINGS_YAML_REAL = str((ROOT / "data" / "palli.yaml").resolve())
_WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND


def _is_live_settings_yaml(raw: object) -> bool:
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", "replace")
    if not isinstance(raw, str) or not raw:
        return False
    if os.path.basename(raw) != "palli.yaml":   # the fast gate: almost always False
        return False
    try:
        return str(Path(raw).resolve()) == _LIVE_SETTINGS_YAML_REAL
    except Exception:
        return False


def _deny_live_settings_writes(event: str, args: tuple) -> None:
    if event == "open":
        if not args or not _is_live_settings_yaml(args[0]):
            return
        mode = args[1] if len(args) > 1 else None
        if isinstance(mode, str):
            writing = any(ch in mode for ch in "wax+")
        else:
            try:
                writing = bool(int(args[2] if len(args) > 2 else 0) & _WRITE_FLAGS)
            except Exception:
                writing = True
        if not writing:
            return
        raise RuntimeError(
            "BLOCKED by tests/conftest.py: a test attempted to open the owner's LIVE "
            f"settings config for writing ({args[0]!r}, mode={mode!r}). Tests get a tmp "
            "copy (TEST_SETTINGS_YAML); the live yaml is read-only, always."
        )
    if event in ("os.rename", "os.replace") and len(args) > 1 and _is_live_settings_yaml(args[1]):
        raise RuntimeError(
            "BLOCKED by tests/conftest.py: a test attempted to rename onto the owner's "
            f"LIVE settings config ({args[1]!r}) — an atomic write is still a write."
        )


# A fresh clone has no data/palli.yaml — the public tree ships only the example, and
# `onboard` (or main.py's first boot) is what copies one into place. 38 call sites in
# this suite read data/palli.yaml by path and MEAN "the shipped configuration"
# (release-gate followups §1); on a clone that has not onboarded yet they raised
# FileNotFoundError, 45 failed / 132 errors on the assembled tree (2026-09-09). So the
# suite seeds the shipped example into place for its own run and removes it after —
# ONLY when the file is absent. On the owner's machine the file exists and nothing here
# runs; the write guard below then keeps it read-only for every test.
_SEEDED_SETTINGS_YAML = None
if not (ROOT / "data" / "palli.yaml").exists() and (ROOT / "data" / "palli.example.yaml").is_file():
    (ROOT / "data" / "palli.yaml").write_bytes((ROOT / "data" / "palli.example.yaml").read_bytes())
    _SEEDED_SETTINGS_YAML = ROOT / "data" / "palli.yaml"

    import atexit as _atexit

    def _remove_seeded_settings_yaml() -> None:
        try:
            os.remove(_SEEDED_SETTINGS_YAML)
        except FileNotFoundError:
            pass

    _atexit.register(_remove_seeded_settings_yaml)

sys.addaudithook(_deny_live_settings_writes)


@pytest.fixture(scope="session", autouse=True)
def _global_test_database_isolation():
    """Verify the live-data redirects held, and give the shared tmp DB the
    real schema once per session.

    Before this guard, tests that used the default brain silently found the
    LIVE database's schema (and data). The schema-initialized tmp DB mirrors
    that world safely: present tables, empty rows — a fresh install.
    """
    if _config.DATABASE_PATH != TEST_DATABASE_PATH:
        raise RuntimeError(
            "Test-DB guard breached: config.DATABASE_PATH is "
            f"{_config.DATABASE_PATH!r}; tests may write the live database."
        )
    import asyncio

    from core.database import brain

    if str(brain.db_path) != TEST_DATABASE_PATH:
        raise RuntimeError(
            "Test-DB guard breached: core.database.brain resolved "
            f"{brain.db_path!r} instead of the tmp test database."
        )
    asyncio.run(brain.initialize())
    yield


@pytest.fixture(autouse=True)
def _collaborators_store_hermetic():
    """Batch 8, task 1 (ruling G3): the collaborators list is pinned to the
    suite's tmp root above; this keeps one test's collaborator from becoming
    the next test's. No ``monkeypatch`` on purpose (the teardown-order note
    on _introspection_off_by_default): a test-local redirect restores first,
    then this removes the shared tmp file."""
    yield
    try:
        os.remove(TEST_COLLABORATORS_PATH)
    except FileNotFoundError:
        pass


@pytest.fixture(autouse=True)
def _session_parse_cache_hermetic():
    """2026-10-12 (fix/reply-speed): core.agent.session keeps parsed
    transcripts between opens, checked by inode, size and the end of what
    was parsed. The suite's transcripts share one tmp root and /tmp reuses
    inode numbers, so one test's rows must never meet the next test's
    file. Emptied after every test."""
    yield
    from core.agent import session as _session
    _session._parsed_files.clear()


@pytest.fixture(autouse=True)
def _quiet_record_hermetic():
    """Batch 8, task 2a (ruling G3): her record of quiet choices is pinned to
    the suite's tmp root above; this keeps one test's quiet choice out of the
    next test's record. No ``monkeypatch``, same teardown-order reason as
    _collaborators_store_hermetic."""
    yield
    shutil.rmtree(TEST_QUIET_CHOICES_DIR, ignore_errors=True)


@pytest.fixture(autouse=True)
def _reply_sources_hermetic():
    """R15, T13: the record of what each reply used is pinned to the suite's
    tmp root above; this keeps one test's line out of the next test's record
    (and out of the night's prune step). No ``monkeypatch``, the same
    teardown-order reason as _quiet_record_hermetic."""
    yield
    shutil.rmtree(TEST_REPLY_SOURCES_DIR, ignore_errors=True)


@pytest.fixture(autouse=True)
def _room_notes_hermetic():
    """Batch 8, task 2b (ruling G3): the owner's room notes are pinned to the
    suite's tmp root above; this keeps one test's note out of the next test's
    room. No ``monkeypatch``, same teardown-order reason as
    _collaborators_store_hermetic."""
    yield
    from core import room_people

    room_people.reset_for_tests()       # the member list kept for a minute
    for path in (TEST_ROOM_NOTES_PATH, TEST_ROOM_NOTES_PATH + ".tmp",
                 TEST_ROOM_PEOPLE_PATH, TEST_ROOM_PEOPLE_PATH + ".tmp"):
        try:
            os.remove(path)
        except FileNotFoundError:
            pass


@pytest.fixture(autouse=True)
def _voice_replies_hermetic():
    """Batch 8, task 10 (ruling G3): the voice-replies switches are pinned to
    the suite's tmp root above; this keeps one test's switch out of the next
    test's conversation. No ``monkeypatch``, same teardown-order reason as
    _collaborators_store_hermetic."""
    yield
    for path in (TEST_VOICE_REPLIES_PATH, TEST_VOICE_REPLIES_PATH + ".tmp"):
        try:
            os.remove(path)
        except FileNotFoundError:
            pass


@pytest.fixture(autouse=True)
def _spending_hermetic():
    """Owner decision T2b-6 (2026-10-04): the spending check's files are
    pinned to the suite's tmp root above; this keeps one test's readings out
    of the next test's normal day. No ``monkeypatch``, same teardown-order
    reason as _collaborators_store_hermetic."""
    yield
    shutil.rmtree(TEST_SPENDING_DIR, ignore_errors=True)


@pytest.fixture(autouse=True)
def _live_db_guard_canary():
    """Fail the offending test the moment the live-DB redirect is undone.

    The 2026-07-09 v2 baseline run proved a mid-suite breach is possible:
    a test un-patched the redirect and every later DB write (including a
    behavior-harness turn's real reminder) landed in the owner's LIVE
    database. Teardown-order note: test-local monkeypatches restore before
    this teardown runs, so legitimate per-test overrides never trip it.
    """
    yield
    breaches = []
    if _config.DATABASE_PATH != TEST_DATABASE_PATH:
        breaches.append(f"config.DATABASE_PATH={_config.DATABASE_PATH!r}")
    if getattr(_Config, "DATABASE_PATH", None) != TEST_DATABASE_PATH:
        breaches.append(f"Config.DATABASE_PATH={_Config.DATABASE_PATH!r}")
    try:
        from core.database import brain as _brain

        if str(_brain.db_path) != TEST_DATABASE_PATH:
            breaches.append(f"brain.db_path={str(_brain.db_path)!r}")
    except Exception:
        pass
    if breaches:
        raise RuntimeError(
            "Live-DB guard breached by this test: " + "; ".join(breaches)
        )


@pytest.fixture(autouse=True)
def _owner_picture_off_by_default(monkeypatch):
    """Force the nightly owner-picture faculty off for ALL tests (2026-08-19,
    same class as the stake-ledger guard below): the owner's live yaml
    enables companion.owner_picture, the settings snapshot lazy-loads that
    yaml in bare pytest processes, and the night-cycle _run_once tests reach
    the real step — whose first act is a REAL LLM call over the identity
    copy's MEMORY.md (the first run hung the suite for 4 minutes).
    picture_enabled() is the single gate; its own tests re-patch it."""
    monkeypatch.setattr("ai.owner_picture.picture_enabled", lambda: False)
    # The nightly stake review (2026-08-18) shares the exposure — it reads the
    # live yaml's stake_ledger flag directly and was saved only by the
    # hermetic ledger being empty ("nothing_held"). Same guard.
    monkeypatch.setattr("ai.stake_review.review_enabled", lambda: False)
    # Surprise + recall bias (2026-08-19): same class — the live yaml flips
    # them on and lazy-loads into bare pytest processes. surprise_enabled()
    # and the index's lean read are the single gates; their own tests
    # re-patch in-body. (Modules land in Tasks 3/9 of the surprise build —
    # guard with try/except so this fixture works before those tasks exist.)
    try:
        monkeypatch.setattr("ai.expectations.surprise_enabled", lambda: False)
    except Exception:
        pass
    try:
        monkeypatch.setattr("ai.inner_memory_index._recall_bias_lean", lambda: None)
    except Exception:
        pass
    # Fears (2026-08-21, step 24): same class — the live yaml ships
    # companion.fears TRUE and lazy-loads into bare pytest processes.
    # fears_enabled() is the single gate for the whole organ (block, touch,
    # boost, night markers, lanes); fear tests re-patch in their own bodies.
    try:
        monkeypatch.setattr("ai.fear_store.fears_enabled", lambda: False)
    except Exception:
        pass
    # Regret (2026-08-23, step 25): same class — the live yaml ships
    # companion.regret TRUE. _regret_enabled() gates the appraiser's prompt
    # block, the review's lifecycle law, and the reflect lane's repair line;
    # the regret tests re-patch in their own bodies. (Attributes land task by
    # task — try/except so this fixture works before each exists.)
    for _target in (
        "ai.stake_ledger.regret_enabled",
        "ai.stake_appraiser._regret_enabled",
        "ai.stake_review._regret_enabled",
        "core.turn.reflect._regret_repair_enabled",
        # Organ 4.13 · Opinions (2026-08-31): same class — the live yaml
        # ships companion.opinions TRUE; opinion tests re-patch in-body.
        "ai.stake_ledger.opinions_enabled",
        "ai.stake_appraiser._opinions_enabled",
        "ai.stake_review._opinions_enabled",
    ):
        try:
            monkeypatch.setattr(_target, lambda: False)
        except Exception:
            pass


@pytest.fixture(autouse=True)
def _stake_ledger_off_by_default(monkeypatch):
    """Force the stake-ledger faculty off for ALL tests (root-level so no test
    dir can be missed). The owner's live data/palli.yaml enables
    companion.stake_ledger and the settings snapshot lazy-loads that yaml even
    in bare pytest processes — so any unguarded test driving recall/dispatch/
    situation-context/proactive would read the LIVE stakes table and schedule
    REAL appraiser LLM calls (this actually happened on 2026-07-06: a suite
    run wrote 5 garbage stakes into the live ledger). ledger_enabled() is the
    single gate for the whole faculty; stake-feature tests re-patch it in
    their own bodies (a test-body monkeypatch lands after this one and wins).
    """
    monkeypatch.setattr("ai.stake_ledger.ledger_enabled", lambda: False)


@pytest.fixture(autouse=True)
def _introspection_off_by_default():
    """Force the introspect tool's registration gate off for ALL tests —
    same reason as the stake-ledger guard above: companion.introspection
    ships default-OFF, but once the owner flips it on in the LIVE
    data/palli.yaml the lazily-loaded settings snapshot would flip every
    unguarded build_default_registry() call in bare pytest too (tool-list
    assertions, prompt byte-purity, dispatch tests). Gate tests restore
    the real function via the module's _introspection_enabled_impl alias
    in their own bodies.

    Deliberately does NOT request the shared ``monkeypatch`` fixture:
    a later-defined autouse fixture that requests it reorders teardown so
    the live-DB canary above runs BEFORE test-local monkeypatch restores
    — which made the two launcher-support DATABASE_PATH tests error at
    teardown (found 2026-07-13). Manual save/restore keeps this guard
    invisible to fixture ordering.
    """
    import core.agent.builtin_tools as _bt

    _original = _bt._introspection_enabled
    _bt._introspection_enabled = lambda: False
    try:
        yield
    finally:
        _bt._introspection_enabled = _original


@pytest.fixture(autouse=True)
def _passions_off_by_default(monkeypatch):
    """Force the passions faculty off for ALL tests (root-level so no test
    dir can be missed). companion.passions went LIVE in the owner's
    data/palli.yaml on 2026-07-20 — and the settings snapshot lazy-loads
    that yaml even in bare pytest processes, so an unguarded test driving
    the night cycle would read the LIVE gate and fire real authoring LLM
    calls (the narrative flip bit exactly this way, c74389f).
    passions_enabled() is the single gate for the whole faculty; tests that
    need it re-patch in their own bodies (a test-body monkeypatch lands
    after this one and wins), and gate-logic tests restore the real
    function via passions_enabled_impl (the introspection alias pattern).
    """
    monkeypatch.setattr("ai.passions.passions_enabled", lambda: False)


@pytest.fixture(autouse=True)
def _vulnerability_off_by_default(monkeypatch):
    """Force Grounded Vulnerability (organ 4.9) off for ALL tests — the
    passions class exactly, and pulled forward the moment the organ gained
    a WRITER (the night harvest, task 2) rather than waiting for its own
    build task. Task 8 (2026-09-01) then armed the owner's live yaml, so
    this fixture is no longer a precaution: companion.vulnerability is TRUE
    there now and the settings snapshot lazy-loads it in bare pytest, which
    is exactly what would let the seven tests that drive
    NightCycleEngine._run_once for real read her live monologue / journal /
    night-notes / deeds, fire a real DEEP LLM call, and plant into her real
    data/disclosures/ — the leak the narrative flip bit for three weeks
    (c74389f). tests/ai/test_vulnerability_flags.py holds this fixture, and
    goes red the moment either line below is deleted.

    vulnerability_enabled() is the single gate for the whole organ;
    vulnerability tests re-patch in their own bodies (a test-body
    monkeypatch lands after this one and wins).
    """
    for _target in (
        # The root gate — every production reader (reflect, the arbiter, the
        # gather, the harvest, introspect) re-imports THIS name at call
        # time, so this one line is what actually darkens the organ.
        "ai.disclosure_store.vulnerability_enabled",
        # ...and the reflect lane's own gate on top of it, belt-and-braces:
        # the day it stops delegating to the store, the pin still holds.
        # Its cost, paid knowingly: arming the store in a test body no
        # longer arms this lane, so a reflect test must patch both (see
        # test_the_ids_actually_reach_the_filled_prompt). ai.arbiter's
        # `_vulnerability_on` is deliberately NOT pinned here — it is the
        # same one-line delegation, and pinning it cost eleven arbiter-leg
        # tests their honest wiring for no gate the store's pin misses.
        # Both delegations are proven past these pins, through import-time
        # captures, in tests/ai/test_vulnerability_flags.py.
        "core.turn.reflect._disclosure_filing_on",
    ):
        try:
            monkeypatch.setattr(_target, lambda: False)
        except Exception:
            pass
    # The hermetic-env seam, the twin of the CALLBACKS_ROOT one closed in
    # task 6: `disclosure_store._resolve_root` reads DISCLOSURES_ROOT at CALL
    # time and BEFORE the module constant, so an exported value outranks every
    # tmp_path a test hands it — the suite then reads and writes her real book.
    monkeypatch.delenv("DISCLOSURES_ROOT", raising=False)


@pytest.fixture(autouse=True)
def _idiolect_off_by_default():
    """Force Shared Idiolect (organ 4.12) off for ALL tests — the
    vulnerability class exactly, from task 1. The live yaml will carry
    companion.idiolect true after the wrap, and the settings snapshot
    lazy-loads it in bare pytest, so an unpinned suite would read her real
    book and file into it from every reflect/dispatch test.

    idiolect_enabled() is the root gate; reflect's `_callback_filing_on`
    (task 2) and the registry's `_idiolect_enabled` (task 3) are pinned
    beside it belt-and-braces — those two do not exist yet, and a name the
    module has not grown is skipped, exactly as the vulnerability loop's
    `except Exception: pass` skips one. Each is proven past this pin
    through an import-time capture in tests/ai/test_idiolect_flags.py
    (task 6). Tests that want the organ ON re-patch in their own bodies (a
    test-body monkeypatch lands after this one and wins) — and a reflect
    test must arm BOTH the store and reflect's gate.

    Deliberately does NOT request the shared ``monkeypatch`` fixture: same
    teardown-ordering reason as _introspection_off_by_default above, and
    re-proved here 2026-09-02 — the monkeypatch form of this guard errored
    the same two launcher-support DATABASE_PATH tests that docstring names
    (the live-DB canary ran before the test-local restores). Manual
    save/restore keeps this guard invisible to fixture ordering.

    It also pops an ambient ``CALLBACKS_ROOT`` for the test's duration (the
    task-2 review's I5 class): the store resolves its root at CALL time,
    env FIRST (ai/callbacks_store.py `_resolve_root`), so one exported
    variable in the operator's shell outranks both the module constant this
    conftest rotates at import and the per-scenario rotation inside
    engine.hermetic_stores. Proved 2026-09-02 — with `CALLBACKS_ROOT`
    exported, tests/probes/test_engine_guards.py's callbacks pin FAILED
    reading a book outside its own sandbox. A test that sets the variable
    itself (monkeypatch.setenv in the body) lands after this fixture and
    still wins.
    """
    import importlib
    import os

    _saved = []
    for _mod_name, _attr in (
        ("ai.callbacks_store", "idiolect_enabled"),
        ("core.turn.reflect", "_callback_filing_on"),
        ("core.agent.builtin_tools", "_idiolect_enabled"),
    ):
        try:
            _mod = importlib.import_module(_mod_name)
            _original = getattr(_mod, _attr)
        except Exception:
            continue
        _saved.append((_mod, _attr, _original))
        setattr(_mod, _attr, lambda: False)
    _env_had = "CALLBACKS_ROOT" in os.environ
    _env_was = os.environ.pop("CALLBACKS_ROOT", None)
    try:
        yield
    finally:
        for _mod, _attr, _original in _saved:
            setattr(_mod, _attr, _original)
        if _env_had:
            os.environ["CALLBACKS_ROOT"] = _env_was
        else:
            os.environ.pop("CALLBACKS_ROOT", None)


@pytest.fixture(autouse=True)
def _tonight_off_by_default():
    """Force "one thing on her mind" (Batch 6, task 1) off for ALL tests —
    the idiolect class exactly. The live yaml carries companion.tonight true
    after the wrap, and the settings snapshot lazy-loads it in bare pytest,
    so an unpinned suite would make a real DEEP model call from every test
    that drives NightCycleEngine._run_once, and a real said-check from every
    test that drives apply_reflection.

    tonight_enabled() is the one gate: the night step, the reply band,
    reflect, the moment's board and the arbiter all read it through the
    module at call time. Tests that want the organ ON re-patch it in their
    own bodies (a test-body monkeypatch lands after this one and wins); the
    real function is reached through tonight_enabled_impl. Her folder is
    redirected in _selfhood_state_paths_hermetic below.

    Deliberately does NOT request the shared ``monkeypatch`` fixture: same
    teardown-ordering reason as _introspection_off_by_default above.
    """
    import ai.tonight as _tonight

    _original = _tonight.tonight_enabled
    _tonight.tonight_enabled = lambda: False
    try:
        yield
    finally:
        _tonight.tonight_enabled = _original


@pytest.fixture(autouse=True)
def _workbench_off_peak_by_default():
    """Pin the workbench's clock to an off-peak moment for ALL tests (the
    owner, 10-05: her sessions wait out DeepSeek's peak). The suite's brain
    is DeepSeek by default (_a_brain_by_default), so an unpinned clock would
    make every test that runs a session go red on weekdays 01:00-04:00 and
    06:00-10:00 UTC — the clock-dependent fixture class (developer-notes,
    "red every night 00:00-04:00"). Tests of the hold re-pin it in their own
    bodies (a test-body monkeypatch lands after this one and wins).

    Manual save/restore, no ``monkeypatch`` — same teardown-ordering reason
    as the guards above.
    """
    from datetime import datetime, timezone

    import ai.workbench as _wb

    _original = _wb._utc_now
    # Saturday noon UTC: never peak.
    _wb._utc_now = lambda: datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc)
    try:
        yield
    finally:
        _wb._utc_now = _original


@pytest.fixture(autouse=True)
def _her_hands_off_by_default():
    """Force the her-hands toolset's registration gate off for ALL tests —
    same class of leak as the introspection guard above: features.her_hands
    ships default-OFF in the schema (core/settings_schema.py), but the live
    data/palli.yaml already ships it TRUE (step 33's ship note) and
    SettingsService.snapshot() lazy-loads that yaml even in bare pytest
    processes (core/settings_service.py:121-124) — so any unguarded
    build_default_registry() call would carry all six hands (voice
    join/leave/speak, list_members, send_message, set_presence) into
    tool-list assertions, prompt byte-purity, and dispatch tests that never
    meant to exercise them. Added at the step-33 final review after the
    whole-branch reviewer proved exactly this: the six tools already ride
    every unguarded build_default_registry() call in bare pytest, currently
    harmless only by containment. Gate tests monkeypatch the real function
    directly in their own bodies (test_registration_respects_flag,
    test_workbench_registry_never_has_hands — a test-body monkeypatch lands
    after this one and wins).

    Deliberately does NOT request the shared ``monkeypatch`` fixture: same
    reason as _introspection_off_by_default above — a later-defined autouse
    fixture that requests it reorders teardown so the live-DB canary runs
    BEFORE test-local monkeypatch restores. Manual save/restore keeps this
    guard invisible to fixture ordering.
    """
    import core.agent.builtin_tools as _bt

    _original = _bt._her_hands_enabled
    _bt._her_hands_enabled = lambda: False
    try:
        yield
    finally:
        _bt._her_hands_enabled = _original


@pytest.fixture(autouse=True)
def _read_page_off_by_default():
    """Force the page reader's registration gate off for ALL tests — the
    her-hands guard's class of leak: features.read_page is default-OFF in the
    schema, but the wrap sets it true in both tracked yamls (Batch 8, task 9;
    ruling T9-5) and SettingsService.snapshot() lazy-loads the tracked yaml in
    bare pytest, so every unguarded build_default_registry() would grow a
    tool. Tests of the gate restore the real one through
    ``_read_page_enabled_impl`` (the ``real_gate`` fixture in
    tests/agent/test_read_page.py).

    Manual save/restore, no ``monkeypatch`` — same teardown-ordering reason as
    the guards above.
    """
    import core.agent.builtin_tools as _bt

    _original = _bt._read_page_enabled
    _bt._read_page_enabled = lambda: False
    try:
        yield
    finally:
        _bt._read_page_enabled = _original


@pytest.fixture(autouse=True)
def _guest_web_search_off_by_default():
    """R14, T7 (plan §2b): hold features.guest_web_search OFF for every test,
    the read-page guard's class of leak — the wrap sets it true in the live
    yaml and SettingsService.snapshot() lazy-loads the tracked yaml in bare
    pytest, so every guest turn would grow a registry and the golden's guest
    turn would move. Tests of the gate restore the real one through
    ``_guest_web_search_enabled_impl`` or patch the gate in their own bodies.
    Each test also gets a fresh shared ledger of friends' searches.

    Manual save/restore, no ``monkeypatch`` — same teardown-ordering reason as
    the guards above.
    """
    import core.agent.builtin_tools as _bt
    from core.agent import guest_search_budget as _gsb

    _original = _bt._guest_web_search_enabled
    _original_ledger = _gsb._shared
    _bt._guest_web_search_enabled = lambda: False
    _gsb._shared = None
    try:
        yield
    finally:
        _bt._guest_web_search_enabled = _original
        _gsb._shared = _original_ledger


@pytest.fixture(autouse=True)
def _forged_tools_off_by_default():
    """Force the forge loader's registration gate off for ALL tests — the
    her-hands guard's exact class of leak: features.forged_tools ships
    default-OFF in the schema, but once the owner flips it true in the LIVE
    data/palli.yaml the lazily-loaded settings snapshot would flip every
    unguarded build_default_registry() call in bare pytest too (tool-list
    assertions, prompt byte-purity, dispatch tests) — and the loader would
    read whatever FORGE_ROOT holds. Gate tests monkeypatch
    _forged_tools_enabled directly in their own bodies (a test-body
    monkeypatch lands after this one and wins).

    Deliberately does NOT request the shared ``monkeypatch`` fixture: same
    teardown-ordering reason as _introspection_off_by_default above.
    """
    import core.agent.builtin_tools as _bt

    _original = _bt._forged_tools_enabled
    _bt._forged_tools_enabled = lambda: False
    try:
        yield
    finally:
        _bt._forged_tools_enabled = _original


@pytest.fixture(autouse=True)
def _duplex_off_by_default():
    """Step 27: hold companion.duplex OFF for every test — the her-hands
    guard's exact class of leak. The live data/palli.yaml flips it true at
    the Phase A wrap; bare pytest lazy-loads that yaml, so every unguarded
    gate (runner emission, _moment_gates, the registry, the presence hand,
    the prompt block, the stop branch) would flip in tests that never meant
    to exercise it. Tests that want it ON patch
    ``core.agent.duplex.duplex_enabled`` in their own bodies (a test-body
    patch lands after this one and wins). Manual save/restore, no
    ``monkeypatch`` request — same ordering reason as the guards above."""
    from core.agent import duplex as _dx
    _original = _dx.duplex_enabled
    _dx.duplex_enabled = lambda: False
    try:
        yield
    finally:
        _dx.duplex_enabled = _original


@pytest.fixture(autouse=True)
def _she_decides_off_by_default():
    """Batch 8, task 2a (ruling T2-1): hold companion.she_decides OFF for
    every test — the duplex guard's class of leak. The wrap turns it on in
    the live data/palli.yaml, and bare pytest would then flip every gate it
    covers (the quick vote's text, the stay_quiet control call, the reply
    instruction, the introspect source) in tests that never meant to
    exercise it; every existing cadence, Telegram and guest test is then a
    flag-off pin. Tests that want it ON patch
    ``core.agent.quiet_choice.she_decides_enabled`` in their own bodies (a
    test-body patch lands after this one and wins). Manual save/restore, no
    ``monkeypatch`` request — same ordering reason as the guards above."""
    from core.agent import quiet_choice as _qc
    _original = _qc.she_decides_enabled
    _qc.she_decides_enabled = lambda: False
    try:
        yield
    finally:
        _qc.she_decides_enabled = _original


@pytest.fixture(autouse=True)
def _first_look_off_by_default():
    """R14, T0 (plan §2.2): hold companion.first_look OFF for every test, the
    she_decides guard's twin. The wrap turns it on in the live data/palli.yaml,
    and bare pytest would then swap the quick vote and the first read for the
    look in tests that never meant to exercise it. Tests that want it ON patch
    ``core.agent.first_look.enabled`` in their own bodies (a test-body patch
    lands after this one and wins). Manual save/restore, no ``monkeypatch``
    request — same ordering reason as the guards above."""
    from core.agent import first_look as _fl
    _original = _fl.enabled
    _fl.enabled = lambda: False
    try:
        yield
    finally:
        _fl.enabled = _original


@pytest.fixture(autouse=True)
def _message_times_off_by_default():
    """Step 2 (2026-10-08): companion.message_times ships ON, and it stamps every
    user and assistant line of the payload with its time. Held OFF for every test
    so the existing exact-payload pins (the she-decides golden, the room payload
    boundary, every runner test that reads what was sent) stay pins; tests that
    want it ON patch ``core.agent.message_times.message_times_enabled`` in their
    own bodies (a test-body patch lands after this one and wins)."""
    from core.agent import message_times as _mt
    _original = _mt.message_times_enabled
    _mt.message_times_enabled = lambda: False
    try:
        yield
    finally:
        _mt.message_times_enabled = _original


@pytest.fixture(autouse=True)
def _voice_notes_off_by_default():
    """Batch 8, task 10 (ruling T10-2): hold voice.voice_notes OFF for every
    test — the duplex guard's class of leak. The wrap turns it on in both
    tracked yamls, and bare pytest lazy-loads the yaml, so every unguarded
    gate (her two voice tools, speak_line's pointer, the status line, the
    doors' voice path, /voice's remembered switch) would flip in tests that
    never meant to exercise it; every existing Telegram voice test is then a
    flag-off pin. Tests that want it ON patch
    ``core.voice_replies.voice_notes_enabled`` in their own bodies (a
    test-body patch lands after this one and wins). Manual save/restore, no
    ``monkeypatch`` request — same ordering reason as the guards above."""
    from core import voice_replies as _vr
    _original = _vr.voice_notes_enabled
    _vr.voice_notes_enabled = lambda: False
    try:
        yield
    finally:
        _vr.voice_notes_enabled = _original


@pytest.fixture(autouse=True)
def _memory_reach_off_by_default():
    """R15, T1: hold companion.memory_reach OFF for every test — the duplex
    guard's class of leak. The wrap turns it on in the live data/palli.yaml
    (and the example made from it seeds the suite's settings), and every gate
    that reads ``enabled()`` would then flip in tests that never meant to
    exercise it. Tests that want it ON patch ``ai.memory_reach_index.enabled``
    in their own bodies (a test-body patch lands after this one and wins);
    the real accessor stays reachable as ``_enabled_impl``. Manual
    save/restore, no ``monkeypatch`` request — same ordering reason as the
    guards above."""
    from ai import memory_reach_index as _mri
    _original = _mri.enabled
    _mri.enabled = lambda: False
    try:
        yield
    finally:
        _mri.enabled = _original


@pytest.fixture(autouse=True)
def _today_slot_off_by_default():
    """R15, T12: hold the today slot OFF for every test. Its gate,
    ``core.turn.companion_flags.today_slot_on``, reads companion.memory_reach
    directly (reflect is at the coupling cap and cannot reach
    ``ai.memory_reach_index.enabled``), so the pin above does not hold it:
    with the live yaml's switch on, every reflect test would write today
    rows, every rendered facts band could grow a "## Today, so far"
    section and every nightly-pass test would retire rows. Tests that want
    it ON patch ``core.turn.companion_flags.today_slot_on`` in their own
    bodies (a test-body patch lands after this one and wins); the real gate
    stays reachable as ``today_slot_on_impl``. Manual save/restore, no
    ``monkeypatch`` request — same ordering reason as the guards above."""
    from core.turn import companion_flags as _cf
    _original = _cf.today_slot_on
    _cf.today_slot_on = lambda: False
    try:
        yield
    finally:
        _cf.today_slot_on = _original


@pytest.fixture(autouse=True)
def _selfhood_state_paths_hermetic(monkeypatch, tmp_path):
    """Point the LIVE selfhood state markers at tmp for ALL tests.

    The narrative week marker (data/narrative_self/.state.json) has been
    poisoned by test runs on this box THREE times (the W29 marker, then W30
    twice — the last within ten minutes of a manual repair, by the
    2026-07-20 05:00 full-suite gate), each time silently skipping her
    weekly self-revision: narrative_self + night_cycle are ON in the live
    yaml and lazy-load into bare pytest processes, so any test that reaches
    the real ritual claims the real week. No test legitimately writes the
    live paths (marker tests all pass explicit state_path); this makes that
    structural. Same class, same guard: the night-cycle date-claim file —
    a poisoned date silently skips an entire night.
    """
    import ai.narrative_self as _ns
    import ai.night_cycle as _nc

    monkeypatch.setattr(_ns, "NARRATIVE_STATE", tmp_path / "narrative_state.json")
    monkeypatch.setattr(_nc, "NIGHT_CYCLE_STATE", tmp_path / "night_cycle_state.json")
    # 2026-09-29 (Batch 6, task 5): the night's record — same guard class.
    # Every _run_once writes one, so an unpinned test driving the night
    # would drop a false record into her LIVE data/night_cycle/.outcomes/,
    # and the health check would read it as a night that ran.
    monkeypatch.setattr(_nc, "NIGHT_OUTCOMES_DIR", tmp_path / "night_outcomes")

    # 2026-07-29: the same guard, extended to the two RECORD stores that
    # shipped after this fixture was written. Both read her live directory at
    # call time, and both were harmless only while their directories did not
    # exist — so nothing noticed. Then she wrote her first note (07-28 19:26)
    # and ran her first work session (20:36), and three tests went red across
    # two suites with no code change: an "every source is dead" gatherer test
    # rendered her real words, an introspect absence test lost its absence,
    # and a journal cap counted her note among fixture pages. Her live record
    # is ENVIRONMENT, never test state. Tests that genuinely exercise these
    # stores pass an explicit root or patch the reader in their own body,
    # which lands after this fixture and wins.
    import ai.self_notes_store as _sn
    import ai.workbench_store as _wb

    monkeypatch.setattr(_sn, "SELF_NOTES_DIR", tmp_path / "self_notes")
    monkeypatch.setattr(_wb, "WORKBENCH_DIR", tmp_path / "workbench")

    # 2026-08-14: the authorship trace reads her live WORKBENCH.md at call
    # time — same guard class as the two stores above (her live record is
    # ENVIRONMENT). Tests that exercise the trace pass an explicit path.
    import ai.workbench_trace as _wt

    monkeypatch.setattr(_wt, "WORKBENCH_MD", tmp_path / "WORKBENCH.md")

    # 2026-08-18: her inner monologue store — same guard class, and the
    # loudest leak of the day: workbench sessions now leave a monologue entry
    # (_remember_session), so every session test that did not pin the dir
    # wrote "At my workbench (0 min): quiet one." into her LIVE
    # data/inner_monologue/2026-08-18.jsonl (24 phantoms; cleaned by hand,
    # scripts/backfill_workbench_memory.py-style provenance filter). The
    # night's consolidation would have metabolized them at 04:00.
    import ai.inner_monologue_store as _ims

    monkeypatch.setattr(_ims, "MONOLOGUE_DIR", tmp_path / "inner_monologue")

    # 2026-08-18: the memory sweep's sidecar (data/memory_sweeps/) — same
    # guard class. The night-cycle _run_once tests exercise the real sweep
    # against the disposable identity COPY (correct), but its sidecar path
    # was repo-relative and the first full gate left a false "let go on
    # 2026-08-18" file in her LIVE data dir. Her live record is ENVIRONMENT.
    # 2026-09-28 (Batch 5): the sweep moved to ai/memory_sweep.py; same dir.
    import ai.memory_sweep as _memory_sweep

    monkeypatch.setattr(_memory_sweep, "MEMORY_SWEEP_DIR", tmp_path / "memory_sweeps")

    # 2026-08-17: the corrections ledger — same guard class. A test that
    # planted a correction into her LIVE ledger would outrank her real record.
    import ai.corrections_store as _cs

    monkeypatch.setattr(_cs, "CORRECTIONS_DIR", tmp_path / "corrections")

    # 2026-08-06 (arbiter build): same guard for the send ledger + sweep
    # claim — her live send record is environment, never test state, and a
    # test that read the live ledger could see real sends (or worse, a test
    # writing it could spend her real daily ceiling).
    import ai.arbiter as _arb

    monkeypatch.setattr(_arb, "ARBITER_DATA_DIR", tmp_path / "arbiter")

    # 2026-08-07 (impulse-freshness build): same guard for the deeds ring —
    # the runner's dispatch hook and record_outbound's success path write it,
    # so any test driving a deed-shaped tool call or an outbound send would
    # drop phantom deeds into her LIVE record (three landed in the first
    # writer-suite run: a snap.png delivery and two arbiter sends that never
    # happened). Deed tests pass an explicit root or repatch DEEDS_PATH in
    # their own bodies, which lands after this fixture and wins.
    import ai.recent_deeds as _rd

    monkeypatch.setattr(_rd, "DEEDS_PATH", tmp_path / "recent_deeds.jsonl")

    # 2026-08-19 (surprise build): the surprising-window state — same guard
    # class. index_entry reads it at write time; a test-opened window must
    # never bump her LIVE index.
    import ai.surprise_state as _sst

    monkeypatch.setattr(_sst, "STATE_PATH", tmp_path / "surprise_state.json")

    # 2026-08-19 (surprise build): her moment record — same guard class,
    # made structural the day a new READER opened the door: the night's
    # _gather_day_material now reads the day's moments for surprise markers
    # (before this, only writers were the risk and every engine test pinned
    # its own root). Her live present is ENVIRONMENT, never test state.
    import ai.present_moment_store as _pms

    monkeypatch.setattr(_pms, "PRESENT_MOMENT_DIR", tmp_path / "present_moment")

    # 2026-08 (wanting build): same guard for the wishbook — the quest
    # store is written by reflection filing, the rail's progress append
    # and the daydream tick, so any test driving those paths would plant
    # phantom wishes in her LIVE book (the deeds-ring lesson, 2026-08-07).
    # Quest tests pass an explicit root or re-patch in their own bodies.
    import ai.quest_store as _qs

    monkeypatch.setattr(_qs, "QUESTS_DIR", tmp_path / "quests")

    # Same build, same guard: the daydream store — written by the lane's
    # tick; a test driving a tick would drop phantom daydreams (and a
    # stray workbench intent) into her LIVE record.
    import ai.daydream_store as _dds

    monkeypatch.setattr(_dds, "DAYDREAM_DIR", tmp_path / "daydream")

    # 2026-09-28 (owner call 7): the rhythm learner's two paths — same guard
    # class, opened by a DEFAULT: the nightly refresh got its own switch,
    # on by default, and the seeded test settings carry no row for it, so
    # every test that reaches the night pass now reaches the writer (the
    # tick-loop tests did, the day it shipped). Unredirected it opens the
    # LIVE data/palli-local.sqlite3 (read-only; the audit backstop above
    # refused it, and the crash guard swallowed the refusal) and replaces
    # data/rhythm/owner_rhythm.json, the file the surprise organ reads.
    # The redirects are the guard; the backstop stays the backstop. The
    # writer resolves both at call time; tests that exercise it pass
    # explicit paths or re-patch these in their own bodies, which lands
    # after this fixture and wins.
    import ai.owner_rhythm as _rhythm

    monkeypatch.setattr(_rhythm, "DB_FILE", tmp_path / "palli-local.sqlite3")
    monkeypatch.setattr(_rhythm, "RHYTHM_FILE", tmp_path / "rhythm" / "owner_rhythm.json")

    # 2026-08-21 (fears build): her fearbook — same guard class as the
    # wishbook: written by the moment's touch, the reflect plant lane and
    # the weekly re-look, so an unpinned test driving those paths would
    # plant phantom fears in her LIVE book. Fear tests pass an explicit
    # root or re-patch in their own bodies.
    import ai.fear_store as _fs

    monkeypatch.setattr(_fs, "FEARS_DIR", tmp_path / "fears")
    # The store honors a FEARS_ROOT env at call time (the first-authorship
    # script's subprocess seam) BEFORE the module constant — an inherited
    # env var would silently beat the redirect above. Never in tests.
    monkeypatch.delenv("FEARS_ROOT", raising=False)

    # Same build: the fear window — the surprise-window guard's class.
    # index_entry reads it at write time; a test-opened window must never
    # bump her LIVE index.
    import ai.fear_state as _fst

    monkeypatch.setattr(_fst, "STATE_PATH", tmp_path / "fear_state.json")

    # 2026-09-29 (Batch 6, task 1): the nightly pick's folder — same guard
    # class. The night step writes it, and reflect and the moment stamp it
    # said, so an unpinned test driving those paths would write a pick into
    # her LIVE data/tonight/ or mark her real one said. Pick tests re-patch
    # in their own bodies.
    import ai.tonight as _tonight

    monkeypatch.setattr(_tonight, "TONIGHT_DIR", tmp_path / "tonight")

    # 2026-09-29 (Batch 6, task 2): the nightly views pass's once-a-night
    # record — same guard class. The night step writes it, so an unpinned
    # test driving the pass would mark her LIVE night as already done (and
    # her real pass would then skip it), or read her real record and skip
    # its own. Views tests re-patch in their own bodies. The weekly
    # shared-language harvest keeps its record beside her book of
    # callbacks, whose folder is rotated at import above.
    import ai.stake_appraiser as _appraiser

    monkeypatch.setattr(_appraiser, "VIEWS_DIR", tmp_path / "views")

    # 2026-09-29 (Batch 6, task 6): her book of reach-outs — same guard
    # class. The custody bookkeeping writes it for every message she starts
    # and the owner's stored message marks them answered, so an unpinned
    # test driving either path would write into her LIVE data/reachouts/ or
    # mark her real reach-outs answered. Tests re-patch in their own bodies.
    import core.turn.outbound as _outbound

    monkeypatch.setattr(_outbound, "REACHOUTS_DIR", tmp_path / "reachouts")


@pytest.fixture(autouse=True)
def _night_housekeeping_hermetic(monkeypatch):
    """Batch 8, task 3 (rulings G3, T3-3): the night's housekeeping
    (core/retention.py) deletes old copies of her identity documents from
    the database. Seven tests in tests/ai/test_night_cycle.py — and every
    night-record test — drive NightCycleEngine._run_once for real, and none
    stubs this step, so for ALL tests the prune step is a no-op. Tests of the
    prune put the real step back in their own bodies (against a temp
    database), which lands after this fixture and wins."""
    import core.retention as _retention

    async def _pinned(*, now, settings):
        return {"skipped": "pinned_by_tests"}

    monkeypatch.setattr(_retention, "_prune_identity_history", _pinned)
    # Batch 8, task 5 (rulings G3, T5-4, HIGH): the log sweep deletes and
    # zips files under the checkout's logs/ and data/forge/profiles/. The
    # night's step is a no-op for every test, and any other way in (the
    # launcher's boot sweep, a direct _sweep_once) finds an empty tmp tree —
    # never the checkout's own logs, whatever the yaml it lazy-loads says.
    # Tests of the sweep put the real step back and pass their own root.
    monkeypatch.setattr(_retention, "_sweep_logs", _pinned)
    monkeypatch.setattr(_retention, "REPO_ROOT", Path(TEST_DATA_ROOT) / "retention-root")


@pytest.fixture(autouse=True)
def _narrative_and_consolidation_off_by_default(monkeypatch):
    """Force the narrative-self + consolidation faculties off for ALL tests —
    the backlog item every organ-3 review named. Both are ON in the live
    yaml, which lazy-loads into bare pytest processes: before this guard,
    any test reaching NightCycleEngine._run_once without stubbing the steps
    ran the REAL weekly ritual (real authoring LLM calls, silently — and it
    claimed the real week marker for three weeks running until the
    state-path guard above). Tests that need a faculty re-patch in their own
    bodies; gate-logic tests restore via <gate>_impl (passions pattern).
    """
    import ai.consolidation as _cons
    import ai.narrative_self as _ns

    monkeypatch.setattr(_ns, "narrative_self_enabled", lambda: False)
    monkeypatch.setattr(_cons, "consolidation_enabled", lambda: False)

    # 2026-08-06 (arbiter build): same guard, same day it was proven needed —
    # the full suite went 4-red the moment companion.arbiter flipped true in
    # the live yaml (ambient arrival tests + the decide_surface routing test
    # suddenly saw the retired-under-flag paths stand down). The 3352e3d law:
    # owner flips are environment changes that arrive at any hour; the
    # default test world is arbiter-OFF and byte-identical pre-arbiter.
    # Arbiter tests opt in through their own seams (ai.arbiter._companion /
    # engine instance methods), which land after this fixture and win.
    import ai.arbiter as _arb
    import ai.present_moment as _pm

    monkeypatch.setattr(_arb, "_companion", lambda: None)
    monkeypatch.setattr(
        _pm.PresentMomentEngine, "_arbiter_enabled", lambda self: False
    )

    # 2026-08 (wanting build): the default test world is wanting-OFF (the
    # 3352e3d law — the live yaml ships wanting: true, and lazy settings
    # loads would flip the lane on inside bare pytest). Pin the SETTINGS
    # seam, not _enabled: daydream tests opt in by patching
    # DaydreamEngine._settings, which _enabled reads — pinning _enabled
    # itself would dead-end that documented seam (plan deviation, recorded).
    # The PresentMomentEngine pin mirrors its _arbiter_enabled sibling.
    import ai.daydream as _dd

    monkeypatch.setattr(_dd.DaydreamEngine, "_settings", lambda self: None)
    monkeypatch.setattr(
        _pm.PresentMomentEngine, "_wanting_enabled", lambda self: False
    )


@pytest.fixture(autouse=True)
def _a_brain_by_default():
    """The default test world HAS a brain (first door, task 1, 2026-09-17).

    `config.brain_ready` is boot's verdict, and validate() writes it on
    whatever object it is handed — the live singleton included. Several tests
    validate the singleton (build_doctor_report(), the startup preflight). In
    the owner's checkout .env.local fills the key and the verdict comes out
    True; on a keyless runner (CI, the assembled public tree) the code default
    is LLM_PROVIDER=deepseek with no key, so the same call leaves
    `brain_ready = False` on the singleton — and every later test in that
    process meets setup mode: dispatch_turn answers with the fixed sentence and
    every lane idles. Same class as the owner-flip guards above: what this
    machine's env holds must not decide a test about something else.

    Pinned True going in and put back coming out, so a verdict written inside
    one test never outlives it. Setup-mode tests set it False in their own
    bodies, which lands after this fixture and wins.

    The price, said plainly: no in-process test sees the keyless-runner default
    any more. The real keyless boot is still exercised where it is a real
    boot — the subprocess tests in tests/core/test_fresh_clone_boot.py, which
    run `main.py --preflight-only` with no key and never load this file.

    Manual save/restore, NOT the shared ``monkeypatch`` fixture — the
    _introspection_off_by_default note above: one more autouse fixture
    requesting it reorders teardown, and the live-DB canary then errors the
    two launcher-support DATABASE_PATH tests (it did, on this fixture's first
    draft).
    """
    _original = _config.brain_ready
    _config.brain_ready = True
    try:
        yield
    finally:
        _config.brain_ready = _original
