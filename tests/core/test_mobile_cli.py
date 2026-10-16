"""`palli_ctl phone pair | list | revoke` (spec §9)."""
from __future__ import annotations

import asyncio
import re

import pytest

from core import mobile_cli
from core.config import config
from core.mobile_device_store import MobileDeviceStore


@pytest.fixture
def db(tmp_path):
    from core.database import PalliBrain

    brain = PalliBrain(db_path=str(tmp_path / "cli.sqlite3"))
    asyncio.run(brain.initialize())
    return brain


def _run(db, action, **kwargs):
    lines: list[str] = []
    code = mobile_cli.run_phone_command(action, out=lines.append, db=db, owner_id=1, **kwargs)
    return code, "\n".join(lines)


def test_pair_prints_a_qr_and_a_code_that_pairs(db, monkeypatch):
    monkeypatch.setattr(config, "ENABLE_MOBILE_DOOR", True, raising=False)
    code, text = _run(db, "pair", base_url="http://100.64.0.7:8000")
    assert code == 0
    assert "Address: http://100.64.0.7:8000" in text
    shown = re.search(r"Code:\s+([0-9A-Z]{4}-[0-9A-Z]{4})", text).group(1)
    assert len(text.splitlines()) > 15                               # the QR
    device = asyncio.run(MobileDeviceStore(db).redeem(code=shown.replace("-", ""), name="P", token="pmd_x"))
    assert device["owner_id"] == 1


def test_pair_without_an_address_says_to_type_it_and_warns_when_the_door_is_off(db, monkeypatch):
    monkeypatch.setattr(config, "ENABLE_MOBILE_DOOR", False, raising=False)
    code, text = _run(db, "pair", base_url="")
    assert code == 0
    assert "MOBILE_PUBLIC_URL is not set" in text and "ENABLE_MOBILE_DOOR=false" in text


def test_list_and_revoke(db):
    store = MobileDeviceStore(db)

    async def pair():
        await store.create_pair_code(owner_id=1, code="CLIC0001")
        return await store.redeem(code="CLIC0001", name="Pixel 9", token="pmd_cli")

    device = asyncio.run(pair())
    assert "[%d] Pixel 9" % device["id"] in _run(db, "list")[1]
    code, text = _run(db, "revoke", device_id=device["id"])
    assert code == 0 and "Unpaired" in text
    assert _run(db, "list")[1] == "No phones are paired."
    with pytest.raises(RuntimeError, match="No paired phone"):
        _run(db, "revoke", device_id=device["id"])


def test_an_unknown_action_says_what_there_is(db):
    with pytest.raises(RuntimeError, match="phone pair"):
        _run(db, "dance")
