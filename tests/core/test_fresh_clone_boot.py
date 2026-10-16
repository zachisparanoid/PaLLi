"""A stranger's first boot (launch gap §5.1.1): the example config plus an empty
.env must boot with warnings, and a hard failure must exit non-zero with a
numbered punch list telling them exactly which key to set and where.

Two shapes of test, deliberately:

  * in-process — `Config()` reads env at IMPORT time into class attributes, so
    monkeypatch.setenv can never reach it. Instance attributes are the seam
    every other config test uses (tests/core/test_config_validation_matrix.py).
  * subprocess — the exit code is the whole point, and it only exists in a real
    `main.py` run. Every deciding variable is passed explicitly, "" for absent,
    because core/config.py loads .env.local with override=False under tests: in
    the owner's checkout an unset variable is FILLED from his real file, and the
    "no keys at all" test would quietly run with his keys.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from core.config import Config
from core.trusted_networks import DEFAULT_ALLOWED_NETWORKS

REPO = Path(__file__).resolve().parents[2]
EXAMPLE_YAML = REPO / "data" / "palli.example.yaml"

# Every provider override Config.validate() looks at. Blank means "inherit the
# top-level LLM_PROVIDER" — the state a fresh clone is in.
TASK_PROVIDER_VARS = (
    "LLM_RESPONSE_PROVIDER",
    "LLM_ANALYSIS_PROVIDER",
    "LLM_MEMORY_PROVIDER",
    "LLM_DECISION_PROVIDER",
    "LLM_PROACTIVE_PROVIDER",
    "LLM_REFLECTION_PROVIDER",
    "LLM_WEB_SEARCH_PROVIDER",
    "LLM_VISION_PROVIDER",
)


def _fresh_clone_config() -> Config:
    """A Config in the shape a stranger's first run has: one brain, no extras."""
    cfg = Config()
    cfg.LLM_PROVIDER = "deepseek"
    cfg.DEEPSEEK_API_KEY = "sk-test"
    cfg.OPENAI_API_KEY = ""
    for name in TASK_PROVIDER_VARS:
        setattr(cfg, name, "")
    # API-only on purpose: these cases are about the OPTIONAL keys, so the
    # Discord door is closed by the flag rather than by a blank token — which
    # since week 2 task 7 is itself just one more optional key.
    cfg.ENABLE_DISCORD_BOT = False
    cfg.DISCORD_TOKEN = ""
    # Week 2 task 3: blank is the fresh-clone state, and the owner's real id
    # must never decide these cases from his .env.local.
    cfg.LOCAL_OWNER_DISCORD_USER_ID = ""
    # Week 2 task 4: the same premise pin. A checkout whose .env widens the
    # bind (the owner's does) would otherwise decide the loopback half of the
    # allowed-networks cases instead of the test.
    cfg.BACKEND_HOST = "127.0.0.1"
    cfg.BACKEND_ALLOWED_NETWORKS = DEFAULT_ALLOWED_NETWORKS
    # Week 2 task 7: the "Discord is off for this boot" line names the doors
    # that ARE open, so the port is a value a test can read — pin it rather
    # than let this checkout's .env decide the sentence.
    cfg.BACKEND_PORT = 8000
    cfg.ENVIRONMENT = "development"
    cfg.ALLOW_INSECURE_CORS = False
    cfg.FRONTEND_ORIGINS = []
    cfg.FRONTEND_ORIGIN_REGEX = None
    cfg.FRONTEND_URL = "http://localhost:3000"
    return cfg


def _subprocess_env(**overrides: str) -> dict[str, str]:
    """The child inherits the suite's live-data redirects (PALLI_TEST_DATA_ROOT,
    DATABASE_PATH, IDENTITY_ROOT_PATH) and NOTHING else that decides the boot."""
    env = dict(os.environ)
    env.update(
        {
            "PALLI_SETTINGS_PATH": str(EXAMPLE_YAML),
            "PALLI_PREFLIGHT_OFFLINE": "1",
            "ENABLE_DISCORD_BOT": "true",
            "DISCORD_TOKEN": "",
            # Pinned for the same reason as DISCORD_TOKEN: the parent's
            # .env.local is loaded into os.environ and the child inherits it,
            # so the owner's own Discord id would answer the week 2 task 3
            # question ("is the owner id set?") instead of the test.
            "LOCAL_OWNER_DISCORD_USER_ID": "",
            # Telegram door (2026-09-14, task 2), pinned for the same reason
            # as the three above: the parent's .env.local is loaded into
            # os.environ and the child inherits it, so the owner's own
            # Telegram settings would answer these tests instead of the code.
            # "" and "false" are the absent state — the door's flag defaults
            # off, so every test written before this one is unaffected.
            "ENABLE_TELEGRAM_BOT": "false",
            "TELEGRAM_BOT_TOKEN": "",
            "LOCAL_OWNER_TELEGRAM_USER_ID": "",
            # Week 2 task 4, pinned for the same reason: "" reads as unset, so
            # the bind falls back to 127.0.0.1 (core/config.py:182) instead of
            # inheriting the owner's BACKEND_HOST=0.0.0.0, and the allowed list
            # is the shipped default rather than whatever his .env holds.
            "BACKEND_HOST": "",
            "BACKEND_ALLOWED_NETWORKS": DEFAULT_ALLOWED_NETWORKS,
            # Week 2 task 7, pinned for the same reason: it is rendered into
            # the line that says where she is when Discord is off.
            "BACKEND_PORT": "8000",
            "LLM_PROVIDER": "deepseek",
            "DEEPSEEK_API_KEY": "",
            "OPENAI_API_KEY": "",
            "ENABLE_VOICE_FEATURES": "false",
            # Pinned for the same reason as the keys above, and with the same
            # values _valid_config() uses: core/config.py loads .env.local into
            # os.environ in the PARENT and the child inherits it, so a checkout
            # whose .env.local sets ENVIRONMENT=production (or ALLOW_INSECURE_CORS,
            # which the owner's does) would decide this test instead of the code.
            "ENVIRONMENT": "development",
            "ALLOW_INSECURE_CORS": "false",
            "FRONTEND_ORIGINS": "",
            "FRONTEND_URL": "https://trypalli.com/dashboard",
        }
    )
    for name in TASK_PROVIDER_VARS:
        env[name] = ""
    env.update(overrides)
    return env


def _preflight(env: dict[str, str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "main.py", "--preflight-only"],
        cwd=str(REPO),
        capture_output=True,
        text=True,
        env=env,
        timeout=180,
    )


def test_missing_optional_keys_warn_and_disable_instead_of_blocking():
    """Web search and voice are extras. Missing their keys turns the feature off
    for this boot and says so — it does not stop her starting."""
    cfg = _fresh_clone_config()
    cfg.ENABLE_WEB_SEARCH = True
    cfg.EXA_API_KEY = ""
    cfg.ENABLE_VOICE_FEATURES = True
    cfg.STT_PROVIDER = "deepgram"
    cfg.TTS_PROVIDER = "deepgram"
    cfg.DEEPGRAM_API_KEY = ""

    assert cfg.validate() is True

    text = cfg.punch_list.render()
    assert "EXA_API_KEY" in text and "web search" in text.lower()
    assert "DEEPGRAM_API_KEY" in text
    assert cfg.effective_web_search_enabled is False
    assert cfg.effective_stt_provider == "off"
    assert cfg.effective_tts_provider == "off"
    # The third Discord state (week 2 task 7): _fresh_clone_config runs
    # API-only, so the door is off by the flag and nothing is said about it.
    assert cfg.effective_discord_enabled is False
    assert "DISCORD_TOKEN" not in text


def test_a_missing_discord_token_degrades_discord_to_off_for_this_boot():
    """Week 2 task 7 REVERSES week 1's ruling that a blank token blocks boot.

    Owner ruling 2026-09-10 #1: the dashboard is a stranger's first door. A
    stranger who has not made a Discord application yet should still meet her,
    so a blank token turns Discord off for this boot and says so — the same
    warn-and-disable every other optional key gets. The line names the doors
    that ARE open, because "Discord is off" without them is only half the
    truth."""
    cfg = _fresh_clone_config()
    cfg.ENABLE_DISCORD_BOT = True
    cfg.DISCORD_TOKEN = ""

    assert cfg.validate() is True

    text = cfg.punch_list.render()
    assert "DISCORD_TOKEN" in text, text
    # A warning, never a numbered blocker.
    assert "1. DISCORD_TOKEN" not in text, text
    assert ".env.local" in text, text
    assert "dashboard" in text and "http://localhost:3000" in text, text
    assert "http://127.0.0.1:8000" in text, text
    assert cfg.effective_discord_enabled is False

    # A wildcard bind is how the socket is configured, not an address anyone
    # can open, and `http://::1:8000` is not a URL at all. The sentence says
    # where to GO, so both display as loopback.
    cfg.BACKEND_HOST = "0.0.0.0"
    assert cfg.validate() is True
    assert "http://127.0.0.1:8000" in cfg.punch_list.render(), cfg.punch_list.render()

    cfg.BACKEND_HOST = "::1"
    assert cfg.validate() is True
    assert "http://127.0.0.1:8000" in cfg.punch_list.render(), cfg.punch_list.render()


def test_a_token_leaves_discord_on_and_says_nothing():
    """The other half: nothing about a configured token reaches the list, and
    the runtime's flag says Discord is on for this boot."""
    cfg = _fresh_clone_config()
    cfg.ENABLE_DISCORD_BOT = True
    cfg.DISCORD_TOKEN = "x" * 40

    assert cfg.validate() is True
    assert "DISCORD_TOKEN" not in cfg.punch_list.render()
    assert cfg.effective_discord_enabled is True

    # And the flag off with the token still in the file: off is off.
    cfg.ENABLE_DISCORD_BOT = False
    assert cfg.validate() is True
    assert cfg.effective_discord_enabled is False


def test_a_soft_line_and_a_hard_line_land_in_one_report():
    """A punch list is a LIST. The messaging check is recorded rather than
    raised, and defined above every validator that raises, so a stranger with two
    problems fixes both in one pass instead of learning about the second on the
    next boot. Since week 2 task 7 the token's line is the SOFT half of that
    report; a raising validator must not swallow it."""
    cfg = _fresh_clone_config()
    cfg.ENABLE_DISCORD_BOT = True
    cfg.DISCORD_TOKEN = ""
    cfg.AGENTIC_MODE = "totally_invalid"

    assert cfg.validate() is False

    text = cfg.punch_list.render()
    assert "DISCORD_TOKEN" in text, text
    assert "AGENTIC_MODE" in text, text


def test_hard_failure_exits_two_with_the_punch_list():
    """Re-premised by week 2 task 7: a blank Discord token was the only hard
    item a bare `_subprocess_env()` hit, and it is soft now. The blocker here
    is task 4's lock — a wide bind with a list of who may reach her that
    parses to nothing."""
    proc = _preflight(
        _subprocess_env(
            DEEPSEEK_API_KEY="sk-test",
            BACKEND_ALLOWED_NETWORKS="nonsense",
            BACKEND_HOST="0.0.0.0",
        )
    )
    output = proc.stdout + proc.stderr

    assert proc.returncode == 2, output
    assert "1. BACKEND_ALLOWED_NETWORKS" in output, output
    # Once. With no log file configured, logging's last-resort handler writes the
    # same list to stderr as a log line, and a stranger reading it twice sees a
    # broken tool.
    assert output.count("PaLLi cannot start yet.") == 1, output


def test_a_first_boot_with_no_discord_token_still_starts_and_says_which_door():
    """The stranger's real first run: the shipped example yaml, a brain key,
    and no Discord application yet. She starts, and the one line she prints
    about it says where to find her instead of where to stop."""
    proc = _preflight(_subprocess_env(DEEPSEEK_API_KEY="sk-test"))
    output = proc.stdout + proc.stderr

    assert proc.returncode == 0, output
    assert "Preflight passed" in output, output
    # Soft lines go to stdout (main.py:337); only a BLOCKING list is stderr.
    assert "DISCORD_TOKEN" in proc.stdout, output
    assert "closed for this boot" in proc.stdout, output


def test_preflight_only_boots_clean_with_the_minimum():
    proc = _preflight(
        _subprocess_env(DISCORD_TOKEN="x" * 40, DEEPSEEK_API_KEY="sk-test")
    )
    output = proc.stdout + proc.stderr

    assert proc.returncode == 0, output
    # With a token there is nothing to say about Discord at all.
    assert "DISCORD_TOKEN" not in output, output


def test_palli_settings_path_points_the_boot_at_another_yaml(monkeypatch, tmp_path):
    """A stranger (and every test that must not touch the live file) needs a way
    to say which settings file to read without editing the repo."""
    from core.settings_boot import _resolve_default_yaml_path

    monkeypatch.setenv("PALLI_SETTINGS_PATH", str(tmp_path / "custom.yaml"))
    assert _resolve_default_yaml_path() == tmp_path / "custom.yaml"

    monkeypatch.delenv("PALLI_SETTINGS_PATH")
    assert _resolve_default_yaml_path().name == "palli.yaml"


def test_discord_on_with_no_owner_id_says_everyone_is_a_guest():
    """Week 2 task 3 (gap §5.1.5): a blank owner id is not a blocker — she
    runs, and everyone she meets is a guest. That is a real, surprising
    choice, so boot says it out loud instead of leaving a stranger to
    discover that her shell never answers him."""
    cfg = _fresh_clone_config()
    cfg.ENABLE_DISCORD_BOT = True
    cfg.DISCORD_TOKEN = "x" * 40
    cfg.LOCAL_OWNER_DISCORD_USER_ID = ""

    assert cfg.validate() is True

    text = cfg.punch_list.render()
    assert "LOCAL_OWNER_DISCORD_USER_ID" in text, text
    assert "guest" in text, text
    assert ".env.local" in text, text
    # A warning, never a numbered blocker.
    assert "1. LOCAL_OWNER_DISCORD_USER_ID" not in text, text


def test_the_blank_owner_id_line_says_boot_checks_the_stored_owner():
    """Batch 8 Task 11 A2: the config line can only read .env.local, but an
    owner id may be stored in the database (seeded once, or linked on the
    dashboard). Her own box said "everyone is a guest" every boot while the
    stored id made him the owner. The line now says what it can know — the
    env is blank — and that the stored owner is checked at boot (the
    local_owner stage logs the real warning)."""
    for door, enable, token, key in (
        ("Discord", "ENABLE_DISCORD_BOT", "DISCORD_TOKEN", "LOCAL_OWNER_DISCORD_USER_ID"),
        ("Telegram", "ENABLE_TELEGRAM_BOT", "TELEGRAM_BOT_TOKEN", "LOCAL_OWNER_TELEGRAM_USER_ID"),
    ):
        cfg = _fresh_clone_config()
        setattr(cfg, enable, True)
        setattr(cfg, token, "x" * 40)
        setattr(cfg, key, "")

        assert cfg.validate() is True

        line = next(
            (ln for ln in cfg.punch_list.render().splitlines() if ln.startswith(f"- {key}")),
            "",
        )
        assert line, (door, cfg.punch_list.render())
        assert "guest" in line, line
        assert ".env.local" in line, line
        assert "checked against the stored owner at boot" in line, line


def test_an_owner_id_that_is_set_says_nothing():
    cfg = _fresh_clone_config()
    cfg.ENABLE_DISCORD_BOT = True
    cfg.DISCORD_TOKEN = "x" * 40
    cfg.LOCAL_OWNER_DISCORD_USER_ID = "123456789012345678"  # gitleaks:allow

    assert cfg.validate() is True
    assert "LOCAL_OWNER_DISCORD_USER_ID" not in cfg.punch_list.render()


def test_preflight_with_no_owner_id_still_exits_zero_and_names_the_knob():
    proc = _preflight(
        _subprocess_env(
            DISCORD_TOKEN="x" * 40,
            DEEPSEEK_API_KEY="sk-test",
            LOCAL_OWNER_DISCORD_USER_ID="",
        )
    )
    output = proc.stdout + proc.stderr

    assert proc.returncode == 0, output
    assert "LOCAL_OWNER_DISCORD_USER_ID" in proc.stdout, output
    assert "guest" in proc.stdout, output


def test_no_template_comment_is_shipped_as_a_value():
    """A blank key with a trailing `# comment` is NOT blank: python-dotenv
    keeps the comment as the VALUE when the value itself is empty. Eight keys
    shipped that way, so a fresh clone read them as set — the owner id looked
    configured (local_mode dropped it as non-numeric, everyone was a guest,
    boot said nothing), and JWT_SECRET signed web sessions with the sentence
    "# web session signing; generate a long random string". Every comment
    lives above its key now, and this pin keeps the class from coming back."""
    from dotenv import dotenv_values

    values = dotenv_values(REPO / ".env.template")
    assert values["LOCAL_OWNER_DISCORD_USER_ID"] == ""
    commented = {
        key: value for key, value in values.items()
        if value is not None and value.lstrip().startswith("#")
    }
    assert commented == {}, commented


def test_an_owner_id_that_is_not_a_snowflake_says_so_and_says_the_value():
    """Week 2 task 3, review fix: unusable counts as unset. A placeholder
    ("your-id-here"), or the comment the template used to ship as the value,
    is dropped by local_mode with a log line nobody reads — the key LOOKS
    set and everyone is still a guest. Boot names the value back at them."""
    cfg = _fresh_clone_config()
    cfg.ENABLE_DISCORD_BOT = True
    cfg.DISCORD_TOKEN = "x" * 40
    cfg.LOCAL_OWNER_DISCORD_USER_ID = "your-id-here"

    assert cfg.validate() is True

    text = cfg.punch_list.render()
    assert "LOCAL_OWNER_DISCORD_USER_ID" in text, text
    assert "your-id-here" in text, text
    assert "guest" in text, text
    assert "1. LOCAL_OWNER_DISCORD_USER_ID" not in text, text


def test_an_allowed_networks_list_that_parses_to_nothing_is_hard():
    """Week 2 task 4: the setting that keeps strangers off the backend used to
    fail in their favour — every token dropped with a log line nobody reads, an
    empty list, and an empty list is OPEN. A list that parses to nothing now
    locks the door AND stops the boot: a wide bind with a list that gates
    nothing is the one combination she must not start in."""
    cfg = _fresh_clone_config()
    cfg.BACKEND_ALLOWED_NETWORKS = "nonsense"
    cfg.BACKEND_HOST = "0.0.0.0"

    assert cfg.validate() is False

    text = cfg.punch_list.render()
    assert "1. BACKEND_ALLOWED_NETWORKS" in text, text
    assert "nonsense" in text, text
    assert "is not a CIDR" in text, text
    assert "clear the setting to run open on purpose" in text, text


def test_one_bad_token_on_a_loopback_bind_is_soft_and_the_good_ones_are_used():
    """Only the machine itself can reach a loopback bind, so a typo among good
    tokens is a thing to fix, not a reason to refuse to start. The good tokens
    stay the list — a bad one never widens the door."""
    cfg = _fresh_clone_config()
    cfg.BACKEND_ALLOWED_NETWORKS = "garbage,10.0.0.0/8"
    cfg.BACKEND_HOST = "127.0.0.1"

    assert cfg.validate() is True

    text = cfg.punch_list.render()
    assert "BACKEND_ALLOWED_NETWORKS" in text, text
    assert "garbage" in text, text
    assert "1. BACKEND_ALLOWED_NETWORKS" not in text, text


def test_one_bad_token_on_a_wide_bind_is_hard():
    """The same typo with BACKEND_HOST=0.0.0.0: the bind is on every network
    the host sits on and the list of who may reach her is wrong. Refuse."""
    cfg = _fresh_clone_config()
    cfg.BACKEND_ALLOWED_NETWORKS = "garbage,10.0.0.0/8"
    cfg.BACKEND_HOST = "0.0.0.0"

    assert cfg.validate() is False

    text = cfg.punch_list.render()
    assert "1. BACKEND_ALLOWED_NETWORKS" in text, text
    assert "garbage" in text, text


def test_a_list_that_parses_cleanly_says_nothing():
    cfg = _fresh_clone_config()
    cfg.BACKEND_HOST = "0.0.0.0"

    assert cfg.validate() is True
    assert "BACKEND_ALLOWED_NETWORKS" not in cfg.punch_list.render()

    # And the blank input through the same door (week 2, task 4, review fix):
    # "" is a documented choice — run open on purpose — so boot says nothing
    # about it either, wide bind and all. Only a typo is worth a line.
    cfg.BACKEND_ALLOWED_NETWORKS = ""

    assert cfg.validate() is True
    assert "BACKEND_ALLOWED_NETWORKS" not in cfg.punch_list.render()


@pytest.mark.parametrize(
    "host, loopback",
    [
        ("127.0.0.1", True),
        ("127.0.1.5", True),            # all of 127/8 is loopback
        ("::1", True),
        ("0:0:0:0:0:0:0:1", True),      # the same address, written long
        ("[::1]", True),                # as a URL authority writes it
        ("::ffff:127.0.0.1", True),     # IPv4-mapped loopback
        ("localhost", True),
        ("", True),                     # "" reads as unset -> 127.0.0.1
        ("0.0.0.0", False),
        ("::", False),
        ("192.0.2.7", False),           # RFC 5737, a real address on a real net
        ("palli.example", False),       # a hostname we cannot judge: treat as wide
    ],
)
def test_the_bind_is_judged_by_address_not_by_spelling(host, loopback):
    """Week 2 task 4, review fix. Hard-or-soft turns on "can anyone else reach
    this socket", and that is a property of the ADDRESS. A string test read
    `0:0:0:0:0:0:0:1` and `::ffff:127.0.0.1` — the same loopback, spelled
    differently — as wide binds, and refused to start over a typo that could
    only ever have hurt the one machine."""
    cfg = _fresh_clone_config()
    cfg.BACKEND_ALLOWED_NETWORKS = "garbage,10.0.0.0/8"
    cfg.BACKEND_HOST = host

    # Loopback -> soft (she starts, the good token is the list); wide -> hard.
    assert cfg.validate() is loopback

    text = cfg.punch_list.render()
    assert "garbage" in text, text
    assert ("1. BACKEND_ALLOWED_NETWORKS" in text) is not loopback, text


def test_preflight_refuses_a_broken_allowed_networks_list():
    proc = _preflight(
        _subprocess_env(
            DISCORD_TOKEN="x" * 40,
            DEEPSEEK_API_KEY="sk-test",
            BACKEND_ALLOWED_NETWORKS="nonsense",
            BACKEND_HOST="0.0.0.0",
        )
    )
    # stdout+stderr, like the sibling hard-failure test above: a BLOCKING punch
    # list is printed to stderr by run_backend_startup_preflight()
    # (core/launcher_runtime.py:176 — both boot surfaces reach it, which is why
    # it prints there and not in main.py; main.py:310 says so, and
    # preflight_only() itself prints only the fallback line when the list came
    # out empty). Only the soft "turned off for this boot" lines go to stdout.
    output = proc.stdout + proc.stderr

    assert proc.returncode == 2, output
    assert "1. BACKEND_ALLOWED_NETWORKS" in output, output
    assert "nonsense" in output, output

def test_the_telegram_door_on_with_no_token_still_boots_and_says_which_door():
    """Telegram door (2026-09-14, task 2): the second door gets week 2 task
    7's ruling verbatim. A stranger who turned it on before making a bot with
    BotFather still meets her, and the one line about it says where she IS."""
    proc = _preflight(
        _subprocess_env(
            DEEPSEEK_API_KEY="sk-test",
            ENABLE_TELEGRAM_BOT="true",
            TELEGRAM_BOT_TOKEN="",
        )
    )
    output = proc.stdout + proc.stderr

    assert proc.returncode == 0, output
    assert "Preflight passed" in output, output
    assert "TELEGRAM_BOT_TOKEN" in proc.stdout, output
    assert "Telegram door is closed for this boot" in proc.stdout, output


def test_both_doors_off_is_still_a_clean_api_only_boot():
    """The other half: an install that has never heard of Telegram grows no
    new warning. ENABLE_TELEGRAM_BOT defaults FALSE for exactly this."""
    proc = _preflight(
        _subprocess_env(
            DEEPSEEK_API_KEY="sk-test",
            ENABLE_DISCORD_BOT="false",
            ENABLE_TELEGRAM_BOT="false",
        )
    )
    output = proc.stdout + proc.stderr

    assert proc.returncode == 0, output
    assert "TELEGRAM" not in output.upper(), output
    assert "DISCORD_TOKEN" not in output, output


def test_the_shipped_template_carries_the_telegram_door_closed():
    """Telegram door (2026-09-14, task 6): a stranger cannot set a key that is
    not in the file they copy. The three lines ship with the door OFF and both
    values blank, and — like every other blank key since week 2 task 3 — their
    comments live ABOVE them, so python-dotenv reads a blank, not a sentence.
    Non-vacuous on purpose: `dotenv_values` raises KeyError here the moment a
    line is dropped from the template."""
    from dotenv import dotenv_values

    values = dotenv_values(REPO / ".env.template")

    assert values["ENABLE_TELEGRAM_BOT"] == "false"
    assert values["TELEGRAM_BOT_TOKEN"] == ""
    assert values["LOCAL_OWNER_TELEGRAM_USER_ID"] == ""


def test_a_telegram_only_stranger_boots_clean():
    """Telegram door (2026-09-14, task 6): the other stranger. Discord off,
    Telegram on with a token and a numeric owner id — nothing about either
    door is wrong, so boot says nothing about either door. This is the run the
    fresh-clone proof's fourth run does against a real clone; here it is the
    in-suite twin, and it is also the only place that proves the library check
    passes on a tree where requirements.txt was installed."""
    proc = _preflight(
        _subprocess_env(
            DEEPSEEK_API_KEY="sk-test",
            ENABLE_DISCORD_BOT="false",
            ENABLE_TELEGRAM_BOT="true",
            TELEGRAM_BOT_TOKEN="123456:ABC-fictional-token",
            LOCAL_OWNER_TELEGRAM_USER_ID="424242424",
        )
    )
    output = proc.stdout + proc.stderr

    assert proc.returncode == 0, output
    assert "Preflight passed" in output, output
    assert "TELEGRAM" not in output.upper(), output


def test_a_telegram_owner_id_that_is_not_numeric_says_so_and_says_the_value():
    """The Telegram half of week 2 task 3's ruling, through a real boot:
    unusable counts as unset. `local_mode` drops a non-numeric id with a log
    line nobody reads, so the key LOOKS set while everyone on Telegram is
    still a guest. Soft, never a blocker — she starts, and says it."""
    proc = _preflight(
        _subprocess_env(
            DEEPSEEK_API_KEY="sk-test",
            ENABLE_DISCORD_BOT="false",
            ENABLE_TELEGRAM_BOT="true",
            TELEGRAM_BOT_TOKEN="123456:ABC-fictional-token",
            LOCAL_OWNER_TELEGRAM_USER_ID="your-id-here",
        )
    )
    output = proc.stdout + proc.stderr

    assert proc.returncode == 0, output
    assert "LOCAL_OWNER_TELEGRAM_USER_ID" in proc.stdout, output
    assert "is not a Telegram user id" in proc.stdout, output
    assert "your-id-here" in proc.stdout, output
    assert "1. LOCAL_OWNER_TELEGRAM_USER_ID" not in output, output


def test_a_first_boot_with_no_brain_key_at_all_boots_into_setup_mode():
    """First door, task 1 (2026-09-17): a provider that needs a key, with no
    key AT ALL, used to be the hard failure a stranger met before they could
    reach the door the owner's 2026-09-10 ruling gave them. It is setup mode
    now — she starts, and the one line about it names the address where a
    brain can be added. No live PONG is sent at a brain that is not there,
    and its absence is never reported as a probe that failed."""
    proc = _preflight(
        _subprocess_env(
            LLM_PROVIDER="deepseek",
            DEEPSEEK_API_KEY="",
            OPENAI_API_KEY="",
            ENABLE_DISCORD_BOT="false",
            # "" reads as unset, so the line falls back to the loopback default.
            FRONTEND_URL="",
        )
    )
    output = proc.stdout + proc.stderr

    assert proc.returncode == 0, output
    assert "Preflight passed" in output, output
    assert (
        "- LLM — no brain configured; open http://127.0.0.1:3000 to add one "
        "(or set DEEPSEEK_API_KEY in .env.local)"
    ) in proc.stdout, output
    assert "Brain: not asked — no brain is configured yet (setup mode)." in proc.stdout, output
    # Soft: never the numbered list, never a failed probe.
    assert "PaLLi cannot start yet." not in output, output
    assert "did not answer" not in output, output


def test_no_brain_keeps_both_doors_shut_even_with_their_tokens_and_says_so():
    """She must not answer on Discord with no brain (spec §2). A door whose
    token IS set gets its own line — and the brain line names the dashboard
    this install declares, not the default."""
    proc = _preflight(
        _subprocess_env(
            LLM_PROVIDER="openai",
            DEEPSEEK_API_KEY="",
            OPENAI_API_KEY="",
            DISCORD_TOKEN="x" * 40,
            LOCAL_OWNER_DISCORD_USER_ID="123456789012345678",  # gitleaks:allow
            ENABLE_TELEGRAM_BOT="true",
            TELEGRAM_BOT_TOKEN="123456:ABC-fictional-token",
            LOCAL_OWNER_TELEGRAM_USER_ID="424242424",
            FRONTEND_URL="http://192.0.2.7:3000",
        )
    )
    output = proc.stdout + proc.stderr

    assert proc.returncode == 0, output
    assert "Preflight passed" in output, output
    assert (
        "- LLM — no brain configured; open http://192.0.2.7:3000 to add one "
        "(or set OPENAI_API_KEY in .env.local)"
    ) in proc.stdout, output
    assert "- Discord — closed until a brain is configured" in proc.stdout, output
    assert "- Telegram — closed until a brain is configured" in proc.stdout, output
    # Neither token is missing, so neither "no token" line is said.
    assert "DISCORD_TOKEN" not in output, output
    assert "TELEGRAM_BOT_TOKEN" not in output, output


def test_the_shipped_template_opens_the_door_instead_of_pointing_at_a_server_they_may_not_have():
    """A stranger on the untouched template must land on the door, not on a dead brain.

    The template used to ship `LLM_PROVIDER=local_openai`, a model server on their
    own machine. Someone who has not installed one boots "ready": no key is needed,
    so setup mode never fires, the door's key step is never shown, and every message
    comes back as her provider timing out. The door built for that first hour was
    bypassed by the file that ships with it.

    The rule now: the shipped brain is a provider that NEEDS a key and ships with
    that key blank, so a fresh clone always stops at the door and the person picks
    for themselves — a cloud provider or their own server, both offered there.
    """
    from pathlib import Path
    import re

    template = Path(__file__).resolve().parents[2] / ".env.template"
    text = template.read_text(encoding="utf-8")

    def value_of(key: str) -> str:
        match = re.search(rf"(?m)^{key}=(.*)$", text)
        assert match is not None, f"{key} is missing from .env.template"
        return match.group(1).strip()

    provider = value_of("LLM_PROVIDER")
    key_for = {"deepseek": "DEEPSEEK_API_KEY", "openai": "OPENAI_API_KEY"}

    assert provider in key_for, (
        "the shipped LLM_PROVIDER must be one that needs a key, so setup mode fires "
        f"and the door opens for a stranger; it is {provider!r}"
    )
    assert value_of(key_for[provider]) == "", (
        "the shipped brain's key must be blank, or a fresh clone never reaches the door"
    )
