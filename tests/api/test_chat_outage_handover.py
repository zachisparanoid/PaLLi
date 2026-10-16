"""The dashboard chat endpoint hands over HER words, even on an errored turn.

First door, final wave round 2 (2026-09-17). A stranger's first hour runs on
the shipped template, which points at a local model server. With no server
running, the provider call raises, `core/agent/dispatch.py::_apply_outage_fallback`
turns that error-silence into her own honest sentence — `responded=True`, the
outage line in `response_text` — and carries the error onward, because the
error is true and the log wants it.

`api/chat.py::_dispatch_to_spine` used to read `result.error` alone, so the
door page rendered "(turn loop error: agent loop error: …)" as HER bubble: an
internal string in her voice, on the one surface a stranger meets her first,
and the exact thing SETUP.md §3 promises does not happen. The condition is now
"she said nothing" — with words, they are handed over; with none, the error
text is still the only thing there is to show, so it is still shown.

Hermetic: `dispatch_turn` is replaced, the adapter registration and the owner
resolver are stubbed, and nothing here touches a provider, a database or the
network.
"""
from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI

from api import chat as chat_mod
from core.agent.dispatch import _PROVIDER_OUTAGE_LINE
from core.turn.types import TurnResult

# api.chat builds its Turn through core/turn/ingest.py, whose `datetime.utcnow()`
# is production debt pytest.ini leaves unfiltered on purpose. It is not this
# module's finding, so these tests do not add one to the gate's warning count.
pytestmark = pytest.mark.filterwarnings(
    r"ignore:datetime\.datetime\.utcnow\(\) is deprecated:DeprecationWarning"
)


@pytest.fixture
def client(monkeypatch):
    """A bare app with only the chat router on it, the owner resolved, and the
    two gates that are not what these tests are about stood down (they have
    their own tests in tests/api/test_chat_message_auth.py)."""
    app = FastAPI()
    app.include_router(chat_mod.router, prefix="/api/chat")

    async def _owner(request):
        return {"id": 1, "email": "owner@example.com"}

    async def _adapter_registered() -> None:
        return None

    async def _owner_id() -> int:
        return 42

    monkeypatch.setattr(chat_mod, "_require_user", _owner)
    monkeypatch.setattr(chat_mod, "require_csrf", lambda request: None)
    monkeypatch.setattr(chat_mod, "_ensure_dashboard_adapter_registered", _adapter_registered)
    monkeypatch.setattr(chat_mod, "_resolve_local_owner_id", _owner_id)

    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://testserver")


def _returns(result: TurnResult):
    async def _dispatch(turn):
        return result

    return _dispatch


@pytest.mark.asyncio
async def test_an_outage_turn_answers_in_her_voice_not_in_the_log_s(client, monkeypatch):
    """The shipped template with no local server running: the provider call
    failed, and she still said something. That sentence is the reply."""
    monkeypatch.setattr(
        chat_mod,
        "dispatch_turn",
        _returns(
            TurnResult(
                responded=True,
                response_text=_PROVIDER_OUTAGE_LINE,
                error="agent loop error: [Errno 111] Connection refused",
                decision=None,
                reflection_applied=False,
                delegation_tier_used=0,
            )
        ),
    )

    async with client as http:
        response = await http.post("/api/chat/message", json={"message": "hello?"})

    assert response.status_code == 200
    body = response.json()
    assert body["response"] == _PROVIDER_OUTAGE_LINE
    assert "(turn loop error" not in body["response"]
    # And the error text itself never reaches the page in any field.
    assert "Connection refused" not in response.text


@pytest.mark.asyncio
async def test_an_errored_turn_that_said_nothing_still_shows_the_error(client, monkeypatch):
    """The other half, unchanged: with no words of hers, the error text is the
    only thing there is to show, and dropping it would leave a stranger staring
    at an empty bubble."""
    monkeypatch.setattr(
        chat_mod,
        "dispatch_turn",
        _returns(
            TurnResult(
                responded=False,
                response_text="",
                error="agent loop error: the runner fell over",
                decision=None,
                reflection_applied=False,
                delegation_tier_used=0,
            )
        ),
    )

    async with client as http:
        response = await http.post("/api/chat/message", json={"message": "hello?"})

    assert response.status_code == 200
    body = response.json()
    assert body["response"] == "(turn loop error: agent loop error: the runner fell over)"
    assert body["delegation_text"] == ""
    assert body["attachments"] == []
    assert body["beats"] == []


@pytest.mark.asyncio
async def test_a_responded_turn_with_an_empty_text_and_an_error_shows_the_error(
    client, monkeypatch
):
    """`responded` alone would not have been the truthful condition: a turn can
    claim it responded and carry no text, and answering "" would drop the one
    clue there is."""
    monkeypatch.setattr(
        chat_mod,
        "dispatch_turn",
        _returns(
            TurnResult(
                responded=True,
                response_text="   ",
                error="agent loop error: nothing came back",
                decision=None,
                reflection_applied=False,
                delegation_tier_used=0,
            )
        ),
    )

    async with client as http:
        response = await http.post("/api/chat/message", json={"message": "hello?"})

    assert response.json()["response"] == "(turn loop error: agent loop error: nothing came back)"


@pytest.mark.asyncio
async def test_an_ordinary_reply_is_untouched(client, monkeypatch):
    """The common case, pinned so the new condition cannot swallow it."""
    monkeypatch.setattr(
        chat_mod,
        "dispatch_turn",
        _returns(
            TurnResult(
                responded=True,
                response_text="hi back",
                error=None,
                decision=None,
                reflection_applied=False,
                delegation_tier_used=0,
            )
        ),
    )

    async with client as http:
        response = await http.post("/api/chat/message", json={"message": "hello?"})

    assert response.json()["response"] == "hi back"
