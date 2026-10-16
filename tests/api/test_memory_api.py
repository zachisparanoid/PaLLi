from __future__ import annotations

from fastapi import FastAPI
import httpx
import pytest

from api import web_account


@pytest.mark.asyncio
async def test_memory_workspace_routes_return_new_surfaces(monkeypatch):
    app = FastAPI()
    app.include_router(web_account.router, prefix="/api/web")

    async def fake_require_user(request):
        return {"id": 7, "discord_user_id": "42", "email": "user@example.com"}

    async def fake_resolve_owner(user):
        return 7

    async def fake_overview(*, owner_user_id: int):
        return {
            "counts": {"daily_log_entry": 2},
            "capability": {"mode": "hybrid"},
            "recent_captures": [{"item_id": "item_1", "source_kind": "workflow_memory", "snippet": "Fixed the XML mapping."}],
            "days": [{"date": "2026-03-27", "entry_count": 2}],
        }

    async def fake_search(*, owner_user_id: int, query: str, limit: int):
        return {
            "query": query,
            "mode": "hybrid",
            "count": 1,
            "results": [{"item_id": "item_1", "source_kind": "workflow_memory", "snippet": "Fixed the XML mapping."}],
            "capability": {"mode": "hybrid"},
        }

    async def fake_days(*, owner_user_id: int, limit: int):
        return [{"date": "2026-03-27", "entry_count": 2}]

    async def fake_day(*, owner_user_id: int, day: str):
        return {"date": day, "entries": [{"entry_id": "mem_1", "summary": "Fixed the XML mapping.", "why_it_matters": "Needed later.", "salience": "high", "tags": ["xml"], "related_refs": []}]}

    async def fake_docs(*, owner_user_id: int):
        return {
            "memory": {"path": "identity/owners/7/MEMORY.md", "sections": {"Important Decisions": "- Fixed the XML mapping."}},
            "memory_summary": {"path": "identity/owners/7/MEMORY_SUMMARY.md", "frontmatter": {"memory_summary_text": "Fixed XML mapping."}, "sections": {"Memory Summary": "Fixed XML mapping."}},
        }

    monkeypatch.setattr(web_account, "_require_user", fake_require_user)
    monkeypatch.setattr(web_account, "_resolve_memory_owner_user_id", fake_resolve_owner)
    monkeypatch.setattr(web_account.memory_workspace_service, "build_overview", fake_overview)
    monkeypatch.setattr(web_account.memory_workspace_service, "search", fake_search)
    monkeypatch.setattr(web_account.memory_workspace_service, "list_days", fake_days)
    monkeypatch.setattr(web_account.memory_workspace_service, "get_day", fake_day)
    monkeypatch.setattr(web_account.memory_workspace_service, "get_docs", fake_docs)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        overview = await client.get("/api/web/account/memory/overview")
        search = await client.get("/api/web/account/memory/search?q=xml")
        days = await client.get("/api/web/account/memory/days")
        day = await client.get("/api/web/account/memory/day/2026-03-27")
        docs = await client.get("/api/web/account/memory/docs")

    assert overview.status_code == 200
    assert overview.json()["counts"]["daily_log_entry"] == 2
    assert search.status_code == 200
    assert search.json()["results"][0]["item_id"] == "item_1"
    assert days.status_code == 200
    assert days.json()["days"][0]["date"] == "2026-03-27"
    assert day.status_code == 200
    assert day.json()["entries"][0]["entry_id"] == "mem_1"
    assert docs.status_code == 200
    assert "Important Decisions" in docs.json()["memory"]["sections"]


@pytest.mark.asyncio
async def test_memory_day_rejects_invalid_day(monkeypatch):
    app = FastAPI()
    app.include_router(web_account.router, prefix="/api/web")

    async def fake_require_user(request):
        return {"id": 7, "discord_user_id": "42", "email": "user@example.com"}

    async def fake_resolve_owner(user):
        return 7

    async def fake_day(*, owner_user_id: int, day: str):
        raise ValueError("Invalid day")

    monkeypatch.setattr(web_account, "_require_user", fake_require_user)
    monkeypatch.setattr(web_account, "_resolve_memory_owner_user_id", fake_resolve_owner)
    monkeypatch.setattr(web_account.memory_workspace_service, "get_day", fake_day)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/api/web/account/memory/day/not-a-day")

    assert response.status_code == 400
    assert response.json()["detail"] == "Invalid day"


@pytest.mark.asyncio
async def test_delete_memory_returns_the_forget_result(monkeypatch):
    """Week 2, task 6 — the delete route answers with what forgetting did:
    the row went, and the document entry behind it went with it."""
    app = FastAPI()
    app.include_router(web_account.router, prefix="/api/web")

    async def fake_require_user(request):
        return {"id": 7, "palli_user_id": 9007, "email": "user@example.com"}

    seen = {}

    async def fake_forget(*, user_id: int, memory_id: int):
        seen["user_id"] = user_id
        seen["memory_id"] = memory_id
        return {"deleted_row": True, "source_removed": "pref_coffee"}

    monkeypatch.setattr(web_account, "_require_user", fake_require_user)
    monkeypatch.setattr(web_account, "require_csrf", lambda request: None)
    monkeypatch.setattr(web_account, "forget_user_memory", fake_forget)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.delete("/api/web/account/memory/31")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "deleted_row": True,
        "source_removed": "pref_coffee",
    }
    # 9007, not 7: the route must resolve the owner through
    # _get_memory_owner_id, not reach for the account row id.
    assert seen == {"user_id": 9007, "memory_id": 31}


@pytest.mark.asyncio
async def test_delete_memory_404s_when_nothing_was_deleted(monkeypatch):
    """Week 2, task 6 — an id that owns no row is still a 404."""
    app = FastAPI()
    app.include_router(web_account.router, prefix="/api/web")

    async def fake_require_user(request):
        return {"id": 7, "palli_user_id": 9007, "email": "user@example.com"}

    async def fake_forget(*, user_id: int, memory_id: int):
        return {"deleted_row": False, "source_removed": None}

    monkeypatch.setattr(web_account, "_require_user", fake_require_user)
    monkeypatch.setattr(web_account, "require_csrf", lambda request: None)
    monkeypatch.setattr(web_account, "forget_user_memory", fake_forget)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.delete("/api/web/account/memory/31")

    assert response.status_code == 404
    assert response.json()["detail"] == "Memory not found or not owned by user"


@pytest.mark.asyncio
async def test_delete_memory_500s_when_the_forget_fails(monkeypatch):
    """Week 2, task 6 review fix — the source is erased before the row, so a
    raising forget means nothing was deleted. That is a 500, never an "ok"."""
    app = FastAPI()
    app.include_router(web_account.router, prefix="/api/web")

    async def fake_require_user(request):
        return {"id": 7, "palli_user_id": 9007, "email": "user@example.com"}

    async def fake_forget(*, user_id: int, memory_id: int):
        raise web_account.ForgetSourceWriteError("the identity root is read-only")

    monkeypatch.setattr(web_account, "_require_user", fake_require_user)
    monkeypatch.setattr(web_account, "require_csrf", lambda request: None)
    monkeypatch.setattr(web_account, "forget_user_memory", fake_forget)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.delete("/api/web/account/memory/31")

    assert response.status_code == 500
    assert response.json()["detail"] == "could not forget: the source document write failed"


@pytest.mark.asyncio
async def test_delete_memory_500s_differently_when_the_row_outlived_its_erased_source(monkeypatch):
    """Week 2, task 6 review fix 2 — the source went and the row did not. A
    real partial mutation, and never labelled a failed document write."""
    app = FastAPI()
    app.include_router(web_account.router, prefix="/api/web")

    async def fake_require_user(request):
        return {"id": 7, "palli_user_id": 9007, "email": "user@example.com"}

    async def fake_forget(*, user_id: int, memory_id: int):
        raise web_account.ForgetRowDeleteError("the database went away")

    monkeypatch.setattr(web_account, "_require_user", fake_require_user)
    monkeypatch.setattr(web_account, "require_csrf", lambda request: None)
    monkeypatch.setattr(web_account, "forget_user_memory", fake_forget)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.delete("/api/web/account/memory/31")

    assert response.status_code == 500
    assert response.json()["detail"] == (
        "the memory's source was erased but the row could not be deleted; "
        "the next rebuild clears it"
    )


@pytest.mark.asyncio
async def test_delete_memory_is_ok_when_the_row_vanished_but_the_source_was_erased(monkeypatch):
    """Week 2, task 6 review fix 2 — the row lost a race and was already
    gone, but its source was erased on the way. The forget holds: the fact
    cannot be rebuilt, so this is not a 404 "not found"."""
    app = FastAPI()
    app.include_router(web_account.router, prefix="/api/web")

    async def fake_require_user(request):
        return {"id": 7, "palli_user_id": 9007, "email": "user@example.com"}

    async def fake_forget(*, user_id: int, memory_id: int):
        return {"deleted_row": False, "source_removed": "pref_coffee"}

    monkeypatch.setattr(web_account, "_require_user", fake_require_user)
    monkeypatch.setattr(web_account, "require_csrf", lambda request: None)
    monkeypatch.setattr(web_account, "forget_user_memory", fake_forget)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.delete("/api/web/account/memory/31")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "deleted_row": False,
        "source_removed": "pref_coffee",
    }
