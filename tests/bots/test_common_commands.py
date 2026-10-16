"""One word, one meaning, at every door (telegram door, task 3).

``/insights`` on Telegram and ``/insights`` on Discord must not be two
implementations that drift. So the LOGIC lives here — in bots/common/ — and
each door is glue: resolve who is asking, call the function, render the text
the way that transport renders text.

Three things this file exists to pin, each of them a bug the SaaS-era code
actually had:

  * **Who is asking is proved, not assumed.** ``resolve_caller`` asks the
    owner gate, per door: his Telegram id is not his Discord snowflake, and
    an unresolved owner ref makes everybody a guest (fail closed).
  * **Consent is written where the arbiter READS it.** The dead Discord
    handler wrote ``(interaction.user.id, "discord")`` — the SNOWFLAKE row.
    ai/proactive_policy.py:331 reads ``(owner_id, …)`` — the app-user row
    core/local_mode.py seeds. Every toggle he ever made on Discord went into
    a row nothing consults. It goes in the owner's row now.
  * **A pairing code cannot steal a link.** A platform id already on another
    account is refused, not silently moved.

Real sqlite throughout, on a temp file: these functions are database
behaviour, and a mock of the database would prove nothing about the row that
matters. Nothing here imports discord or telegram.
"""
from __future__ import annotations

import time

import pytest
import pytest_asyncio

OWNER_APP_ID = 1
OWNER_TELEGRAM_ID = 777
OWNER_DISCORD_ID = 424242
STRANGER_ID = 909090


@pytest_asyncio.fixture
async def db(tmp_path):
    from core.database import PalliBrain

    brain = PalliBrain(db_path=str(tmp_path / "commands.sqlite3"))
    await brain.initialize()
    try:
        yield brain
    finally:
        await brain.close()


@pytest.fixture
def wired(db, monkeypatch):
    """The shared module, talking to the temp database."""
    from bots.common import commands

    monkeypatch.setattr(commands, "brain", db)
    return commands


def _refs(**overrides):
    base = {
        "app_user_id": OWNER_APP_ID,
        "palli_user_id": OWNER_APP_ID,
        "discord_user_id": OWNER_DISCORD_ID,
        "telegram_user_id": OWNER_TELEGRAM_ID,
    }
    base.update(overrides)

    async def _get():
        return base

    return _get


# ── who is asking ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_his_telegram_id_resolves_to_the_owner(wired, monkeypatch):
    monkeypatch.setattr(wired, "get_local_owner_refs", _refs())

    caller = await wired.resolve_caller(
        platform="telegram", platform_user_id=OWNER_TELEGRAM_ID)

    assert caller.is_owner is True
    assert caller.user_id == OWNER_APP_ID
    assert caller.platform_user_id == OWNER_TELEGRAM_ID


@pytest.mark.asyncio
async def test_his_discord_snowflake_is_a_stranger_on_telegram(wired, monkeypatch):
    """Per-door, never pooled: an id that is his on one door proves nothing
    on the other, and a stranger who happens to carry it is still a
    stranger."""
    monkeypatch.setattr(wired, "get_local_owner_refs", _refs())

    caller = await wired.resolve_caller(
        platform="telegram", platform_user_id=OWNER_DISCORD_ID)

    assert caller.is_owner is False
    assert caller.user_id == OWNER_DISCORD_ID


@pytest.mark.asyncio
async def test_a_guest_reads_the_id_space_their_own_turns_write(wired, monkeypatch):
    monkeypatch.setattr(wired, "get_local_owner_refs", _refs())

    caller = await wired.resolve_caller(
        platform="discord", platform_user_id=STRANGER_ID)

    assert caller.is_owner is False
    assert caller.user_id == STRANGER_ID


@pytest.mark.asyncio
async def test_an_unresolved_owner_ref_makes_everyone_a_guest(wired, monkeypatch):
    monkeypatch.setattr(wired, "get_local_owner_refs",
                        _refs(telegram_user_id=None))

    caller = await wired.resolve_caller(
        platform="telegram", platform_user_id=OWNER_TELEGRAM_ID)

    assert caller.is_owner is False


@pytest.mark.asyncio
async def test_a_refs_outage_fails_closed(wired, monkeypatch):
    async def _boom():
        raise RuntimeError("database down")

    monkeypatch.setattr(wired, "get_local_owner_refs", _boom)

    caller = await wired.resolve_caller(
        platform="telegram", platform_user_id=OWNER_TELEGRAM_ID)

    assert caller.is_owner is False


# ── reach-outs ───────────────────────────────────────────────────────────

async def _policy_lookup(brain, owner_id: int):
    """ai/proactive_policy.py:331's exact triple — the read that decides
    whether she is allowed to speak first."""
    for platform in ("discord", "dashboard", "owner"):
        row = await brain.get_user_profile(int(owner_id), platform)
        if row:
            return row
    return None


@pytest.mark.asyncio
async def test_his_consent_lands_in_the_row_the_arbiter_reads(wired, db, monkeypatch):
    monkeypatch.setattr(wired, "get_local_owner_refs", _refs())
    caller = await wired.resolve_caller(
        platform="telegram", platform_user_id=OWNER_TELEGRAM_ID)

    line = await wired.set_proactive_consent(caller, False)

    assert "off" in line.lower()
    profile = await _policy_lookup(db, OWNER_APP_ID)
    consent = profile["preferences"]["proactive_consent"]
    assert consent["enabled"] is False
    assert consent["source"] == "telegram_command"
    assert isinstance(consent["updated_at"], float)


@pytest.mark.asyncio
async def test_the_snowflake_row_is_not_the_one_written(wired, db, monkeypatch):
    """The SaaS-era bug, pinned so it cannot come back: the toggle used to
    land on ``(the platform id, "discord")``, which nothing ever reads."""
    monkeypatch.setattr(wired, "get_local_owner_refs", _refs())
    caller = await wired.resolve_caller(
        platform="discord", platform_user_id=OWNER_DISCORD_ID)

    await wired.set_proactive_consent(caller, False)

    stray = await db.get_user_profile(OWNER_DISCORD_ID, "discord")
    assert "proactive_consent" not in (stray.get("preferences") or {})


@pytest.mark.asyncio
async def test_an_explicit_none_still_toggles(wired, db, monkeypatch):
    """The toggle behaviour the dead Discord handler had, kept on the
    function. Renamed in review round 1 (minor 4/6): NEITHER door passes
    None any more — a bare /proactive and a bare /proactive_consent both
    READ — so the old name ("no argument toggles") claimed something that
    is no longer true of either door.
    """
    monkeypatch.setattr(wired, "get_local_owner_refs", _refs())
    caller = await wired.resolve_caller(
        platform="telegram", platform_user_id=OWNER_TELEGRAM_ID)
    await db.update_user_profile(OWNER_APP_ID, "discord", {
        "preferences": {"proactive_consent": {"enabled": True,
                                              "source": "local_default",
                                              "updated_at": time.time()}}})

    await wired.set_proactive_consent(caller, None)

    profile = await _policy_lookup(db, OWNER_APP_ID)
    assert profile["preferences"]["proactive_consent"]["enabled"] is False


@pytest.mark.asyncio
async def test_status_reads_without_writing(wired, db, monkeypatch):
    monkeypatch.setattr(wired, "get_local_owner_refs", _refs())
    caller = await wired.resolve_caller(
        platform="telegram", platform_user_id=OWNER_TELEGRAM_ID)
    await wired.set_proactive_consent(caller, True)
    before = await _policy_lookup(db, OWNER_APP_ID)

    line = await wired.proactive_status(caller)

    after = await _policy_lookup(db, OWNER_APP_ID)
    assert "on" in line.lower()
    assert (after["preferences"]["proactive_consent"]
            == before["preferences"]["proactive_consent"])


@pytest.mark.asyncio
async def test_a_guest_cannot_set_his_reach_outs(wired, db, monkeypatch):
    monkeypatch.setattr(wired, "get_local_owner_refs", _refs())
    caller = await wired.resolve_caller(
        platform="telegram", platform_user_id=STRANGER_ID)

    line = await wired.set_proactive_consent(caller, True)

    assert line == "Reach-outs are a setting for the person I live with."
    stray = await db.get_user_profile(STRANGER_ID, "discord")
    assert "proactive_consent" not in (stray.get("preferences") or {})


@pytest.mark.asyncio
async def test_a_guest_asking_for_status_gets_the_same_line(wired, monkeypatch):
    monkeypatch.setattr(wired, "get_local_owner_refs", _refs())
    caller = await wired.resolve_caller(
        platform="telegram", platform_user_id=STRANGER_ID)

    assert await wired.proactive_status(caller) == (
        "Reach-outs are a setting for the person I live with.")


# ── insights ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_too_early_says_so_instead_of_inventing(wired, db, monkeypatch):
    monkeypatch.setattr(wired, "get_local_owner_refs", _refs())
    caller = await wired.resolve_caller(
        platform="telegram", platform_user_id=OWNER_TELEGRAM_ID)
    await db.update_user_profile(OWNER_APP_ID, "discord", {"total_interactions": 2})

    text = await wired.insights_text(caller)

    assert "haven't chatted enough" in text
    assert "Your Personality" not in text


@pytest.mark.asyncio
async def test_the_full_shape_is_plain_text_with_every_section(wired, db, monkeypatch):
    monkeypatch.setattr(wired, "get_local_owner_refs", _refs())
    caller = await wired.resolve_caller(
        platform="telegram", platform_user_id=OWNER_TELEGRAM_ID)
    await db.update_user_profile(OWNER_APP_ID, "discord", {
        "total_interactions": 41,
        "learning_progress": 0.62,
        "personality_data": {"traits": {"dry_humour": 0.9, "direct": 0.8,
                                        "curious": 0.7, "patient": 0.1}},
        "communication_style": {"terse": 0.8, "formal": 0.1},
        "preferences": {"interests": ["woodwork", "sqlite", "bread"]},
        "created_at": 1_700_000_000.0,
    })
    await db.store_user_memory(
        user_id=OWNER_APP_ID, memory_type="fact",
        content="He is building a workshop bench out of ash.", importance=0.9)

    text = await wired.insights_text(caller)

    assert "41 conversations" in text
    assert "Dry Humour" in text and "90%" in text
    assert "workshop bench" in text
    assert "woodwork" in text
    assert "Terse" in text
    assert "62%" in text
    # Plain text, both doors: no embeds and no markdown tables.
    assert "|" not in text
    assert "**" not in text


@pytest.mark.asyncio
async def test_a_long_memory_is_trimmed_not_dumped(wired, db, monkeypatch):
    monkeypatch.setattr(wired, "get_local_owner_refs", _refs())
    caller = await wired.resolve_caller(
        platform="telegram", platform_user_id=OWNER_TELEGRAM_ID)
    await db.update_user_profile(OWNER_APP_ID, "discord", {"total_interactions": 9})
    await db.store_user_memory(
        user_id=OWNER_APP_ID, memory_type="fact",
        content="x" * 200, importance=0.9)

    text = await wired.insights_text(caller)

    assert "..." in text
    assert "x" * 200 not in text


# ── linking ──────────────────────────────────────────────────────────────

@pytest.fixture
def local_owner(db, monkeypatch):
    """The real ``ensure_local_owner`` path, on the temp database."""
    from core import local_mode

    monkeypatch.setattr(local_mode, "brain", db)
    monkeypatch.setattr(local_mode.config, "PALLI_DEPLOYMENT_MODE", "local")
    monkeypatch.setattr(local_mode.config, "LOCAL_OWNER_EMAIL", "local@palli.local")
    monkeypatch.setattr(local_mode.config, "LOCAL_OWNER_DISCORD_USER_ID", "")
    monkeypatch.setattr(local_mode.config, "LOCAL_OWNER_TELEGRAM_USER_ID", "",
                        raising=False)
    return local_mode


@pytest.mark.asyncio
async def test_a_good_code_links_this_telegram_id_to_that_account(
        wired, db, local_owner):
    owner = await local_owner.ensure_local_owner()
    await db.store_pairing_code("TG12AB", int(owner["id"]), time.time() + 600)

    line = await wired.link_platform_account(
        platform="telegram", platform_user_id=OWNER_TELEGRAM_ID, code="tg12ab")

    assert "Telegram" in line
    refs = await local_owner.get_local_owner_refs()
    assert refs["telegram_user_id"] == OWNER_TELEGRAM_ID


@pytest.mark.asyncio
async def test_the_discord_door_writes_the_discord_column(wired, db, local_owner):
    owner = await local_owner.ensure_local_owner()
    await db.store_pairing_code("DC99ZZ", int(owner["id"]), time.time() + 600)

    await wired.link_platform_account(
        platform="discord", platform_user_id=OWNER_DISCORD_ID, code="DC99ZZ")

    refs = await local_owner.get_local_owner_refs()
    assert refs["discord_user_id"] == OWNER_DISCORD_ID
    assert refs["telegram_user_id"] is None


@pytest.mark.asyncio
async def test_an_unknown_code_is_refused_with_a_way_forward(wired, db):
    line = await wired.link_platform_account(
        platform="telegram", platform_user_id=OWNER_TELEGRAM_ID, code="NOPE00")

    assert "Invalid or expired pairing code" in line
    assert "dashboard" in line


@pytest.mark.asyncio
async def test_a_code_cannot_move_a_telegram_id_to_another_account(
        wired, db, local_owner):
    owner = await local_owner.ensure_local_owner()
    await db.store_pairing_code("FIRST1", int(owner["id"]), time.time() + 600)
    await wired.link_platform_account(
        platform="telegram", platform_user_id=OWNER_TELEGRAM_ID, code="FIRST1")
    other = await db.create_app_user(
        email="other@palli.local", password_hash=None, google_sub=None, role="user")
    await db.store_pairing_code("SECND2", int(other), time.time() + 600)

    line = await wired.link_platform_account(
        platform="telegram", platform_user_id=OWNER_TELEGRAM_ID, code="SECND2")

    assert "already linked" in line
    refs = await local_owner.get_local_owner_refs()
    assert refs["telegram_user_id"] == OWNER_TELEGRAM_ID


@pytest.mark.asyncio
async def test_the_same_code_twice_is_spent(wired, db, local_owner):
    """The pairing store deletes a redeemed code; the second attempt must
    read as an expired code, not as a crash."""
    owner = await local_owner.ensure_local_owner()
    await db.store_pairing_code("ONCE11", int(owner["id"]), time.time() + 600)
    await wired.link_platform_account(
        platform="telegram", platform_user_id=OWNER_TELEGRAM_ID, code="ONCE11")

    line = await wired.link_platform_account(
        platform="telegram", platform_user_id=OWNER_TELEGRAM_ID, code="ONCE11")

    assert "Invalid or expired pairing code" in line


@pytest.mark.asyncio
async def test_a_door_she_has_no_column_for_is_refused(wired, db):
    line = await wired.link_platform_account(
        platform="signal", platform_user_id=5, code="ABC123")

    assert "can't link" in line.lower()


# ── one sentence, one place ──────────────────────────────────────────────

def test_both_doors_say_the_same_thing_when_a_shared_call_fails():
    """Review round 1, minor 7: the honest-failure line lived twice, once
    per door. It is the same failure — both libraries swallow what a command
    callback raises — so it is one sentence, imported."""
    from bots.common.commands import TROUBLE_LINE
    from bots.discord import discord_commands
    from bots.telegram import commands as telegram_commands

    assert discord_commands.TROUBLE_LINE is TROUBLE_LINE
    assert telegram_commands.TROUBLE_LINE is TROUBLE_LINE
    assert TROUBLE_LINE.strip()


def test_every_owner_only_knob_is_refused_the_same_way():
    from bots.common.commands import _GUEST_CONSENT_LINE, owner_only_line

    assert owner_only_line("Reach-outs") == _GUEST_CONSENT_LINE
    assert owner_only_line("Voice replies") == (
        "Voice replies are a setting for the person I live with.")


# ── the boundary ─────────────────────────────────────────────────────────

def test_the_shared_module_knows_about_no_transport():
    from pathlib import Path

    source = (Path(__file__).resolve().parents[2]
              / "bots" / "common" / "commands.py").read_text("utf-8")

    assert "import discord" not in source
    assert "import telegram" not in source
    assert "bots.discord" not in source
    assert "bots.telegram" not in source
