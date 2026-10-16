"""Owner resolution learns a second word (telegram door, task 1).

``get_local_owner_refs`` is the ONE place every door asks "which id is
him?". Discord reads ``discord_user_id`` from it; the Telegram door will
read ``telegram_user_id``. Until the key exists, everyone she meets on
Telegram is a guest — so the key, its seeding from
``LOCAL_OWNER_TELEGRAM_USER_ID``, and the owner-subject resolution are
pinned here beside the Discord cases that must not move.

Hermetic: a fake brain stands in for the database exactly as
tests/core/test_local_mode_discord_user_id.py does.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import pytest


def _make_owner_row(
    *,
    discord_user_id: Optional[str] = None,
    telegram_user_id: Optional[str] = None,
) -> Dict[str, Any]:
    return {
        "id": 1,
        "email": "local@palli.local",
        "palli_user_id": 1,
        "role": "admin",
        "has_access": True,
        "email_verified": True,
        "email_verified_at": 0.0,
        "age_confirmed": True,
        "age_confirmed_at": 0.0,
        "tos_accepted_at": 0.0,
        "privacy_accepted_at": 0.0,
        "tos_version": "local",
        "privacy_version": "local",
        "discord_user_id": discord_user_id,
        "telegram_user_id": telegram_user_id,
    }


class _FakeBrain:
    def __init__(self, owner_row: Dict[str, Any]) -> None:
        self._owner = dict(owner_row)
        self.update_calls: List[Dict[str, Any]] = []

    async def get_app_user_by_email(self, email: str) -> Optional[Dict[str, Any]]:
        return dict(self._owner)

    async def get_app_user_by_id(self, user_id: int) -> Optional[Dict[str, Any]]:
        return dict(self._owner)

    async def update_app_user(self, user_id: int, updates: Dict[str, Any]) -> bool:
        self.update_calls.append(dict(updates))
        self._owner.update(updates)
        return True

    async def get_user_profile(self, palli_user_id: int, platform: str) -> Dict[str, Any]:
        return {
            "display_name": "Local Operator",
            "username": "Local Operator",
            "preferences": {"voice_enabled": True, "proactive_consent": {"enabled": True}},
        }

    async def update_user_profile(self, palli_user_id: int, platform: str, updates) -> bool:
        return True


@pytest.fixture
def wired(monkeypatch):
    """Both knobs pinned blank by default so the owner's own .env.local can
    never decide a case."""
    from core import local_mode

    monkeypatch.setattr(local_mode.config, "LOCAL_OWNER_DISCORD_USER_ID", "")
    monkeypatch.setattr(
        local_mode.config, "LOCAL_OWNER_TELEGRAM_USER_ID", "", raising=False
    )
    return local_mode


# ── the refs dict ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_the_refs_name_both_doors(wired, monkeypatch):
    fake = _FakeBrain(
        _make_owner_row(discord_user_id="424242", telegram_user_id="777")
    )
    monkeypatch.setattr(wired, "brain", fake)

    refs = await wired.get_local_owner_refs()

    assert set(refs) == {
        "app_user_id", "palli_user_id", "discord_user_id", "telegram_user_id",
    }
    assert refs["discord_user_id"] == 424242          # the pin
    assert refs["telegram_user_id"] == 777


@pytest.mark.asyncio
async def test_an_unset_telegram_id_reads_as_none(wired, monkeypatch):
    fake = _FakeBrain(_make_owner_row(discord_user_id="424242"))
    monkeypatch.setattr(wired, "brain", fake)

    refs = await wired.get_local_owner_refs()

    assert refs["telegram_user_id"] is None
    assert refs["discord_user_id"] == 424242


@pytest.mark.asyncio
async def test_a_junk_stored_telegram_id_reads_as_none_not_a_crash(wired, monkeypatch):
    fake = _FakeBrain(_make_owner_row(telegram_user_id="not-an-id"))
    monkeypatch.setattr(wired, "brain", fake)

    refs = await wired.get_local_owner_refs()

    assert refs["telegram_user_id"] is None


@pytest.mark.asyncio
async def test_the_not_local_mode_dict_names_both_doors_too(wired, monkeypatch):
    """The early return is a separate dict literal; a caller that reads
    ``telegram_user_id`` must not KeyError just because local mode is off."""
    monkeypatch.setattr(wired.config, "PALLI_DEPLOYMENT_MODE", "cloud", raising=False)

    refs = await wired.get_local_owner_refs()

    assert set(refs) == {
        "app_user_id", "palli_user_id", "discord_user_id", "telegram_user_id",
    }
    assert refs["telegram_user_id"] is None
    assert refs["discord_user_id"] is None


# ── the seeding knob ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_numeric_knob_seeds_the_column_once(wired, monkeypatch):
    fake = _FakeBrain(_make_owner_row())
    monkeypatch.setattr(wired, "brain", fake)
    monkeypatch.setattr(
        wired.config, "LOCAL_OWNER_TELEGRAM_USER_ID", "777", raising=False
    )

    await wired.ensure_local_owner()

    seeded = [c for c in fake.update_calls if "telegram_user_id" in c]
    assert len(seeded) == 1, fake.update_calls
    assert seeded[0]["telegram_user_id"] == "777"


@pytest.mark.asyncio
async def test_a_blank_knob_writes_nothing(wired, monkeypatch):
    fake = _FakeBrain(_make_owner_row())
    monkeypatch.setattr(wired, "brain", fake)

    await wired.ensure_local_owner()

    for call in fake.update_calls:
        assert "telegram_user_id" not in call, call


@pytest.mark.asyncio
async def test_a_non_numeric_knob_is_ignored(wired, monkeypatch):
    fake = _FakeBrain(_make_owner_row())
    monkeypatch.setattr(wired, "brain", fake)
    monkeypatch.setattr(
        wired.config, "LOCAL_OWNER_TELEGRAM_USER_ID", "your-id-here", raising=False
    )

    await wired.ensure_local_owner()

    for call in fake.update_calls:
        assert "telegram_user_id" not in call, call


@pytest.mark.asyncio
async def test_an_existing_column_is_never_overwritten(wired, monkeypatch):
    fake = _FakeBrain(_make_owner_row(telegram_user_id="111"))
    monkeypatch.setattr(wired, "brain", fake)
    monkeypatch.setattr(
        wired.config, "LOCAL_OWNER_TELEGRAM_USER_ID", "777", raising=False
    )

    await wired.ensure_local_owner()

    for call in fake.update_calls:
        assert "telegram_user_id" not in call, call


@pytest.mark.asyncio
async def test_seeding_one_door_does_not_seed_the_other(wired, monkeypatch):
    """The Discord block is untouched by this task: a Telegram knob alone
    must not write a discord_user_id, and vice versa."""
    fake = _FakeBrain(_make_owner_row())
    monkeypatch.setattr(wired, "brain", fake)
    monkeypatch.setattr(
        wired.config, "LOCAL_OWNER_TELEGRAM_USER_ID", "777", raising=False
    )

    await wired.ensure_local_owner()

    for call in fake.update_calls:
        assert "discord_user_id" not in call, call


# ── owner resolution by subject ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_telegram_subject_resolves_to_the_owner(wired, monkeypatch):
    fake = _FakeBrain(
        _make_owner_row(discord_user_id="424242", telegram_user_id="777")
    )
    monkeypatch.setattr(wired, "brain", fake)

    resolved = await wired.resolve_local_owner_observer_user_id_for_subject(
        user_id=777, platform="telegram",
    )
    assert resolved == 1


@pytest.mark.asyncio
async def test_a_discord_subject_still_resolves_to_the_owner(wired, monkeypatch):
    fake = _FakeBrain(
        _make_owner_row(discord_user_id="424242", telegram_user_id="777")
    )
    monkeypatch.setattr(wired, "brain", fake)

    resolved = await wired.resolve_local_owner_observer_user_id_for_subject(
        user_id=424242, platform="discord",
    )
    assert resolved == 1


@pytest.mark.asyncio
async def test_a_stranger_on_telegram_is_not_the_owner(wired, monkeypatch):
    fake = _FakeBrain(_make_owner_row(telegram_user_id="777"))
    monkeypatch.setattr(wired, "brain", fake)

    resolved = await wired.resolve_local_owner_observer_user_id_for_subject(
        user_id=888, platform="telegram",
    )
    assert resolved is None


@pytest.mark.asyncio
async def test_the_doors_do_not_cross(wired, monkeypatch):
    """His Discord snowflake is not his Telegram id: the same number on the
    other door must not make a stranger the owner."""
    fake = _FakeBrain(_make_owner_row(discord_user_id="424242"))
    monkeypatch.setattr(wired, "brain", fake)

    resolved = await wired.resolve_local_owner_observer_user_id_for_subject(
        user_id=424242, platform="telegram",
    )
    assert resolved is None
