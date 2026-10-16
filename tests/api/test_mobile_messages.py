"""POST /api/mobile/messages (spec §5, §6): 202 at once, one turn per
client_id, photos to the vision lane, a voice note heard."""
from __future__ import annotations

import asyncio
import logging

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from starlette.requests import Request

from api import mobile
from bots.common.voice_note import TRANSCRIPTION_UNAVAILABLE_CONTENT
from bots.mobile import client as door_mod
from bots.mobile import intake
from core.config import config
from core.mobile_device_store import MobileDeviceStore
from core.mobile_events_store import MobileEventsStore

CID = "9f1b2c3d-0000-4000-8000-0000000000a1"


class FakeDoor:
    owner_id = 1

    def __init__(self) -> None:
        self.accepted: list = []
        self.typing = 0

    async def accept(self, turn):
        self.accepted.append(turn)

    def note_typing(self):
        self.typing += 1


@pytest_asyncio.fixture
async def env(tmp_path, monkeypatch):
    from core.database import PalliBrain

    brain = PalliBrain(db_path=str(tmp_path / "messages.sqlite3"))
    await brain.initialize()
    monkeypatch.setattr("core.database.brain", brain)
    monkeypatch.setattr(config, "effective_mobile_enabled", True, raising=False)
    monkeypatch.setattr(config, "brain_ready", True, raising=False)
    monkeypatch.setattr("bots.mobile.reply.inbox_root", lambda: str(tmp_path / "inbox"))
    door = FakeDoor()
    monkeypatch.setattr(door_mod, "_door", door)
    devices = MobileDeviceStore(brain)
    await devices.create_pair_code(owner_id=1, code="MMMM0001")
    await devices.redeem(code="MMMM0001", name="Phone", token="pmd_messages")
    try:
        yield brain, door
    finally:
        await brain.close()


def _client() -> httpx.AsyncClient:
    app = FastAPI()
    app.include_router(mobile.router, prefix="/api/mobile")
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver")


AUTH = {"Authorization": "Bearer pmd_messages"}


@pytest.mark.asyncio
async def test_a_send_is_202_and_one_turn_per_client_id(env):
    brain, door = env
    async with _client() as http:
        first = await http.post("/api/mobile/messages", data={"client_id": CID, "text": "hi"}, headers=AUTH)
        await mobile.drain_intake()
        again = await http.post("/api/mobile/messages", data={"client_id": CID, "text": "hi"}, headers=AUTH)
        await mobile.drain_intake()
    assert first.status_code == 202 and first.json() == {"client_id": CID}
    assert again.status_code == 202
    assert len(door.accepted) == 1
    page, _ = await MobileEventsStore(brain).after(owner_id=1, after=0, limit=10)
    assert [(e["kind"], e["client_id"], e["text"]) for e in page] == [("user_message", CID, "hi")]


@pytest.mark.asyncio
async def test_two_retries_racing_each_other_start_one_turn(env):
    brain, door = env
    async with _client() as http:
        both = await asyncio.gather(
            http.post("/api/mobile/messages", data={"client_id": CID, "text": "hi"}, headers=AUTH),
            http.post("/api/mobile/messages", data={"client_id": CID, "text": "hi"}, headers=AUTH))
        await mobile.drain_intake()
    assert [r.status_code for r in both] == [202, 202]
    assert len(door.accepted) == 1
    page, _ = await MobileEventsStore(brain).after(owner_id=1, after=0, limit=10)
    assert [e["kind"] for e in page] == ["user_message"]


@pytest.mark.asyncio
async def test_a_send_that_cannot_be_taken_in_is_a_plain_notice_and_can_be_sent_again(env, monkeypatch, caplog):
    brain, door = env
    caplog.set_level(logging.ERROR, logger=mobile.logger.name)

    async def broken(**kwargs):
        raise OSError("disk full at /srv/palli/inbox")

    monkeypatch.setattr(intake, "build_turn", broken)
    async with _client() as http:
        first = await http.post("/api/mobile/messages", data={"client_id": CID, "text": "hi"}, headers=AUTH)
        await mobile.drain_intake()
    assert first.status_code == 202 and door.accepted == []
    page, _ = await MobileEventsStore(brain).after(owner_id=1, after=0, limit=10)
    assert [(e["kind"], e["level"]) for e in page] == [("notice", "trouble")]
    assert page[0]["text"] == mobile._NOT_TAKEN_IN and "disk full" not in page[0]["text"]
    assert "disk full at /srv/palli/inbox" in caplog.text    # the detail is in the log
    assert mobile._ACCEPTING == set()                       # a retry is taken in, not swallowed


@pytest.mark.asyncio
async def test_a_send_recorded_but_not_started_is_202_with_a_notice_and_no_second_turn(env, caplog):
    brain, door = env
    caplog.set_level(logging.ERROR, logger=mobile.logger.name)

    async def refuse(turn):
        raise RuntimeError("lane is jammed")

    door.accept = refuse
    async with _client() as http:
        first = await http.post("/api/mobile/messages", data={"client_id": CID, "text": "hi"}, headers=AUTH)
        await mobile.drain_intake()
        again = await http.post("/api/mobile/messages", data={"client_id": CID, "text": "hi"}, headers=AUTH)
        await mobile.drain_intake()
    assert (first.status_code, again.status_code) == (202, 202)
    page, _ = await MobileEventsStore(brain).after(owner_id=1, after=0, limit=10)
    assert [(e["kind"], e.get("text")) for e in page] == [
        ("user_message", "hi"), ("notice", mobile._NOT_STARTED)]      # recorded once; the phone is told
    assert "lane is jammed" in caplog.text
    assert mobile._ACCEPTING == set()


@pytest.mark.asyncio
async def test_a_device_paired_to_another_owner_is_refused_and_starts_nothing(env):
    brain, door = env
    devices = MobileDeviceStore(brain)
    await devices.create_pair_code(owner_id=2, code="MMMM0002")
    await devices.redeem(code="MMMM0002", name="Stranger", token="pmd_stranger")
    async with _client() as http:
        refused = await http.post("/api/mobile/messages", data={"client_id": CID, "text": "hi"},
                                  headers={"Authorization": "Bearer pmd_stranger"})
        await mobile.drain_intake()
    assert refused.status_code == 403 and door.accepted == []
    page, _ = await MobileEventsStore(brain).after(owner_id=2, after=0, limit=10)
    assert page == []


@pytest.mark.asyncio
async def test_no_token_is_401_and_a_send_with_a_bad_token_starts_nothing(env):
    brain, door = env
    async with _client() as http:
        bare = await http.post("/api/mobile/messages", data={"client_id": CID, "text": "hi"})
        bad = await http.post("/api/mobile/messages", data={"client_id": CID, "text": "hi"},
                              headers={"Authorization": "Bearer pmd_wrong"})
        await mobile.drain_intake()
    assert (bare.status_code, bad.status_code) == (401, 401) and door.accepted == []


def _watch_the_body(monkeypatch):
    """Count the times the request body is parsed (the real parse still runs)."""
    parsed = []
    real_form = Request.form

    def counting_form(self, **kwargs):
        parsed.append(kwargs)
        return real_form(self, **kwargs)

    monkeypatch.setattr(Request, "form", counting_form)
    return parsed


@pytest.mark.asyncio
async def test_an_unpaired_post_is_401_before_its_body_is_parsed(env, monkeypatch):
    parsed = _watch_the_body(monkeypatch)
    multipart = {"files": ("big.bin", b"0" * 4096, "application/octet-stream")}
    async with _client() as http:
        bare = await http.post("/api/mobile/messages", data={"client_id": CID}, files=multipart)
        wrong = await http.post("/api/mobile/messages", data={"client_id": CID}, files=multipart,
                                headers={"Authorization": "Bearer pmd_wrong"})
        assert parsed == []                                  # nothing was read, let alone spooled
        paired = await http.post("/api/mobile/messages", data={"client_id": CID}, files=multipart,
                                 headers=AUTH)                # the same request, paired, is parsed
        await mobile.drain_intake()
    assert (bare.status_code, wrong.status_code, paired.status_code) == (401, 401, 202)
    assert len(parsed) == 1 and parsed[0]["max_files"] == mobile.MAX_FILE_PARTS


@pytest.mark.asyncio
async def test_a_send_that_says_it_is_too_big_or_not_how_big_is_413_before_its_body_is_parsed(env, monkeypatch):
    parsed = _watch_the_body(monkeypatch)
    boundary = {"Content-Type": "multipart/form-data; boundary=x"}
    too_big = str(intake.MAX_TOTAL_BYTES + mobile.FORM_OVERHEAD_BYTES + 1)

    async def chunks():
        yield b"--x--\r\n"

    async with _client() as http:
        huge = await http.post("/api/mobile/messages", content=b"--x--\r\n", headers={
            **AUTH, **boundary, "Content-Length": too_big})
        chunked = await http.post("/api/mobile/messages", content=chunks(), headers={**AUTH, **boundary})
        garbled = await http.post("/api/mobile/messages", content=b"--x--\r\n", headers={
            **AUTH, **boundary, "Content-Length": "lots"})
    assert (huge.status_code, chunked.status_code, garbled.status_code) == (413, 413, 413)
    assert parsed == []


@pytest.mark.asyncio
async def test_a_part_over_its_limit_is_refused_before_it_is_read_into_memory(env, monkeypatch):
    monkeypatch.setattr(intake, "MAX_PART_BYTES", 8)

    async def never(field, part):
        raise AssertionError("a refused part was read")

    monkeypatch.setattr(mobile, "_read", never)
    async with _client() as http:
        too_big = await http.post("/api/mobile/messages", data={"client_id": CID},
                                  files={"files": ("big.bin", b"0123456789", "application/octet-stream")},
                                  headers=AUTH)
    assert too_big.status_code == 413


@pytest.mark.asyncio
async def test_more_parts_than_a_send_may_have_is_refused(env, monkeypatch):
    brain, door = env
    monkeypatch.setattr(mobile, "MAX_FILE_PARTS", 2)
    async with _client() as http:
        crowded = await http.post(
            "/api/mobile/messages", data={"client_id": CID},
            files=[("images", (f"{n}.jpg", b"\xff\xd8", "image/jpeg")) for n in range(3)], headers=AUTH)
    assert crowded.status_code == 400 and door.accepted == []


@pytest.mark.asyncio
async def test_a_bad_client_id_or_an_empty_send_is_422_and_starts_nothing(env):
    brain, door = env
    async with _client() as http:
        bad = await http.post("/api/mobile/messages", data={"client_id": "x", "text": "hi"}, headers=AUTH)
        empty = await http.post("/api/mobile/messages", data={"client_id": CID, "text": " "}, headers=AUTH)
        await mobile.drain_intake()
    assert (bad.status_code, empty.status_code) == (422, 422)
    assert door.accepted == []


def _multipart(fields: dict) -> tuple[bytes, dict]:
    """A multipart body of plain fields only (httpx sends ``data=`` alone urlencoded)."""
    boundary = "palli-text-cap"
    body = b"".join(
        f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n'.encode()
        + value.encode("utf-8") + b"\r\n"
        for name, value in fields.items()) + f"--{boundary}--\r\n".encode()
    return body, {"Content-Type": f"multipart/form-data; boundary={boundary}"}


@pytest.mark.asyncio
async def test_a_text_over_its_cap_is_422_and_one_at_the_cap_is_taken_in(env):
    """Review M7 (ledger T6 L95): a send's text is at most MAX_TEXT_BYTES of
    UTF-8 — bytes, not characters (each "é" is two)."""
    brain, door = env
    at_cap = "é" * (intake.MAX_TEXT_BYTES // 2)
    assert len(at_cap.encode("utf-8")) == intake.MAX_TEXT_BYTES == 65_536
    over, over_headers = _multipart({"client_id": CID, "text": at_cap + "!"})
    fits, fits_headers = _multipart({"client_id": CID, "text": at_cap})
    async with _client() as http:
        refused = await http.post("/api/mobile/messages", content=over, headers={**AUTH, **over_headers})
        await mobile.drain_intake()
        assert refused.status_code == 422
        assert refused.json() == {"detail": "That message is too long to send — send it as a file instead."}
        assert door.accepted == []
        taken = await http.post("/api/mobile/messages", content=fits, headers={**AUTH, **fits_headers})
        await mobile.drain_intake()
    assert taken.status_code == 202
    (turn,) = door.accepted
    assert turn.content == at_cap


@pytest.mark.asyncio
async def test_a_urlencoded_text_over_its_cap_is_refused_not_taken_in(env):
    """A urlencoded body skips Starlette's multipart part limit, so the cap is
    what stops a runaway client handing her megabytes."""
    brain, door = env
    async with _client() as http:
        refused = await http.post("/api/mobile/messages", headers=AUTH, data={
            "client_id": CID, "text": "a" * (intake.MAX_TEXT_BYTES + 1)})
        await mobile.drain_intake()
    assert refused.status_code == 422 and door.accepted == []
    page, _ = await MobileEventsStore(brain).after(owner_id=1, after=0, limit=10)
    assert page == []


@pytest.mark.asyncio
async def test_a_part_over_the_limit_is_413(env, monkeypatch):
    monkeypatch.setattr(intake, "MAX_PART_BYTES", 8)
    async with _client() as http:
        too_big = await http.post("/api/mobile/messages", data={"client_id": CID},
                                  files={"files": ("big.bin", b"0123456789", "application/octet-stream")},
                                  headers=AUTH)
    assert too_big.status_code == 413


@pytest.mark.asyncio
async def test_a_closed_door_says_why(env, monkeypatch):
    async with _client() as http:
        monkeypatch.setattr(config, "effective_mobile_enabled", False, raising=False)
        closed = await http.post("/api/mobile/messages", data={"client_id": CID, "text": "hi"}, headers=AUTH)
        monkeypatch.setattr(config, "brain_ready", False, raising=False)
        no_brain = await http.post("/api/mobile/messages", data={"client_id": CID, "text": "hi"}, headers=AUTH)
    assert closed.status_code == 503 and closed.json()["detail"] == {"reason": "door_closed"}
    assert no_brain.status_code == 503 and no_brain.json()["detail"] == {"reason": "no_brain"}


@pytest.mark.asyncio
async def test_a_photo_goes_to_her_eyes_and_a_voice_note_is_heard(env, monkeypatch):
    brain, door = env

    async def transcribe(data, *, mime, filename="voice.ogg", service=None):
        assert mime == "audio/mp4"
        return "hello there"

    monkeypatch.setattr(intake, "transcribe_note", transcribe)
    async with _client() as http:
        sent = await http.post(
            "/api/mobile/messages", data={"client_id": CID},
            files=[("images", ("cat.jpg", b"\xff\xd8jpeg", "image/jpeg")),
                   ("voice", ("note.m4a", b"m4a-bytes", "audio/mp4"))],
            headers=AUTH)
        await mobile.drain_intake()
    assert sent.status_code == 202
    (turn,) = door.accepted
    assert turn.content == "hello there" and turn.metadata.is_voice is True
    assert turn.images[0].local_path.startswith("file://") and turn.images[0].mime == "image/jpeg"
    page, _ = await MobileEventsStore(brain).after(owner_id=1, after=0, limit=10)
    (mine,) = page
    assert mine["heard_as"] == "hello there" and [f["filename"] for f in mine["files"]] == ["cat.jpg", "note.m4a"]


@pytest.mark.asyncio
async def test_a_voice_note_she_could_not_hear_is_the_honest_marker(env, monkeypatch):
    brain, door = env

    async def deaf(data, *, mime, filename="voice.ogg", service=None):
        return None

    monkeypatch.setattr(intake, "transcribe_note", deaf)
    async with _client() as http:
        await http.post("/api/mobile/messages", data={"client_id": CID},
                        files={"voice": ("note.m4a", b"m4a", "audio/mp4")}, headers=AUTH)
        await mobile.drain_intake()
    (turn,) = door.accepted
    assert turn.content == TRANSCRIPTION_UNAVAILABLE_CONTENT
    page, _ = await MobileEventsStore(brain).after(owner_id=1, after=0, limit=10)
    assert "heard_as" not in page[0]
