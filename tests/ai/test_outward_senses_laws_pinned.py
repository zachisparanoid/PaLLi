"""Step 23 · The law pins (Task 9).

The owner's calls (spec §0) and the standing laws, pinned so a later edit
cannot quietly loosen them: listen-don't-scan vocabulary only; root-only ARP
sweeps; no five-minute clock; the denylist EMPTY by his call; no new
outbound doors (the say rail is the only one); the look is not a deed; the
peek is read-only.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
LAN_SRC = (REPO / "core" / "lan_presence.py").read_text(encoding="utf-8")
NAS_SRC = (REPO / "ai" / "curiosity_research.py").read_text(encoding="utf-8")
HOUSE_SRC = (REPO / "core" / "agent" / "builtin_tools" / "the_house.py").read_text(encoding="utf-8")


def test_listen_dont_scan_is_the_whole_vocabulary():
    assert '"-sn"' in LAN_SRC and '"arp"' in LAN_SRC and '"tcpdump"' in LAN_SRC
    assert '"sudo", "-n"' in LAN_SRC                       # root-only ARP; unprivileged nmap forbidden
    for forbidden in ('"-p"', '"-sV"', '"-sS"', '"-sT"', '"--script"', '"-A"', '"-O"',
                      '"--top-ports"', 'port 80', 'port 443', '"-w"', '"-Pn"'):
        assert forbidden not in LAN_SRC, forbidden


def test_the_capture_is_bounded_and_reaped_as_root():
    """The 2026-08-24 defect class, pinned so it cannot come back: an
    unprivileged terminate() cannot touch a root capture (it orphaned the
    process and hung wait() forever), and a capture with no wall clock can
    outlive the process that started it."""
    from core import lan_presence as lp

    assert '"timeout"' in LAN_SRC and "LISTENER_WINDOW_S" in LAN_SRC
    assert lp.LISTENER_WINDOW_S <= 3600
    assert "start_new_session=True" in LAN_SRC          # its own group, so root can reap it
    assert '"kill"' in LAN_SRC and "reap_argv" in LAN_SRC
    assert "proc.terminate()" not in LAN_SRC and "proc.kill()" not in LAN_SRC


def test_the_test_harness_interlock_guards_every_network_door():
    """Never a real capture or scan in a test — mechanism, not intent:
    tests/core/test_main.py boots the app, which really did start this
    engine, spawn a root capture, write the live book and hang the suite."""
    from core import lan_presence as lp

    assert LAN_SRC.count("under_test()") >= 4          # the definition + three doors
    for door in ("async def _spawn_listener", "async def run_sweep", "async def run_probe"):
        body = LAN_SRC.split(door, 1)[1][:900]
        assert "under_test()" in body, door
    assert lp.under_test() is True                     # we are one


def test_the_listener_sits_out_startup():
    from core import lan_presence as lp

    assert lp.LISTENER_BOOT_DELAY_S >= 45
    assert "await asyncio.sleep(LISTENER_BOOT_DELAY_S)" in LAN_SRC


def test_the_device_book_never_reaches_git():
    """The book records the household's devices — runtime state, and not
    ours to commit (the launcher-state precedent)."""
    ignore = (REPO / ".gitignore").read_text(encoding="utf-8")
    assert "data/lan_presence.json" in ignore


def test_no_five_minute_clock():
    from core.settings_schema import CompanionConfig

    f = CompanionConfig.model_fields["lan_sweep_interval_minutes"]
    assert f.default == 480
    assert any(getattr(m, "ge", None) == 30 for m in f.metadata)
    assert "300" not in LAN_SRC.split("DEFAULT_SWEEP_INTERVAL_MINUTES")[1].split("\n")[0]


def test_the_denylist_is_empty_by_his_call():
    from core.settings_schema import CompanionConfig

    assert CompanionConfig.model_fields["nas_deny"].default_factory() == []
    import yaml
    comp = yaml.safe_load((REPO / "data" / "palli.yaml").read_text(encoding="utf-8"))["companion"]
    assert comp["nas_deny"] == []


def test_no_new_outbound_doors():
    for name, src in (("lan_presence", LAN_SRC), ("curiosity_research", NAS_SRC), ("the_house", HOUSE_SRC)):
        for forbidden in ("record_outbound", "import discord", "from discord", "from bots",
                          "send_message", "SendMessageTool", "channel.send"):
            assert forbidden not in src, f"{name}: {forbidden}"


def test_the_look_is_not_a_deed():
    from ai.recent_deeds import DEED_TOOLS

    assert "look_at_the_house" not in DEED_TOOLS
    assert "record_deed" not in HOUSE_SRC


def test_tests_never_capture_or_scan():
    for p in (REPO / "tests" / "core").glob("test_lan_*.py"):
        src = p.read_text(encoding="utf-8")
        assert "create_subprocess_exec" not in src and "subprocess.run" not in src, p.name


def test_lan_peek_is_read_only_and_renders_the_book(tmp_path):
    now = 1_700_000_000.0
    book = {
        "aa:bb:cc:dd:ee:03": {"ip": "192.0.2.3", "hostname": "pixel", "first_seen": now - 86400,
                              "last_seen": now - 30, "online": True, "online_since": now - 3600},
        "aa:bb:cc:dd:ee:04": {"ip": "192.0.2.4", "hostname": "", "first_seen": now - 9 * 86400,
                              "last_seen": now - 4 * 3600, "online": False, "offline_since": now - 4 * 3600},
    }
    p = tmp_path / "lan_presence.json"
    p.write_text(json.dumps(book), encoding="utf-8")
    before = p.read_text(encoding="utf-8")
    cp = subprocess.run([sys.executable, str(REPO / "scripts" / "lan_peek.py"), "--book", str(p)],
                        capture_output=True, text=True, timeout=60, cwd=str(REPO))
    assert cp.returncode == 0, cp.stderr
    assert "pixel" in cp.stdout and "…:ee:04" in cp.stdout and "192.0.2.3" in cp.stdout
    assert "online" in cp.stdout.lower()
    assert p.read_text(encoding="utf-8") == before                  # never writes
    missing = subprocess.run([sys.executable, str(REPO / "scripts" / "lan_peek.py"), "--book", str(tmp_path / "none.json")],
                             capture_output=True, text=True, timeout=60, cwd=str(REPO))
    assert missing.returncode == 0 and "empty" in missing.stdout.lower()   # the honest empty book


def test_the_book_is_never_written_on_the_loop_thread():
    """2026-08-24 19:5x: save_book ran on the event-loop thread and a rename
    waited 30–120 s on this VM's ext4 journal — her whole brain froze with
    it. Law: the engine's ONE call to save_book is handed to a worker
    thread; nothing else in the module may call it."""
    import re

    src = (REPO / "core" / "lan_presence.py").read_text(encoding="utf-8")
    refs = [line for line in src.splitlines()
            if re.search(r"\bsave_book\b", line) and not line.lstrip().startswith("def ")]
    assert len(refs) == 1 and "asyncio.to_thread(save_book," in refs[0], refs
    assert "SAVE_THROTTLE_S" not in src                       # the 30-second throttle is gone
    assert "BOOK_FLUSH_INTERVAL_S = 12 * 3600" in src          # twice a day regardless (his 20:57 call)
