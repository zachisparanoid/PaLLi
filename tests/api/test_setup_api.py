"""The setup API — probe, write, hot swap, status (first door, task 2, 2026-09-17).

This is the small HTTP surface the door page calls: `GET /api/setup/status`
tells it what to show, `POST /api/setup/brain` tests a pasted key live, saves
it and brings her brain up on the running process.

What is pinned here:

  * nothing is written until the probe passes, and a failed probe leaves the
    config, the registry and `.env.local` exactly as they were;
  * a passing probe writes her voice's lines, flips `brain_ready`, and the
    running client's default provider stops being the setup-mode stand-in;
  * a save that works with a swap that fails is honest: the key stays on disk,
    `restart_required` says so, and the process stays in setup mode rather
    than in some half-state;
  * the capability matrix refuses a provider her voice cannot run on — out
    loud, before anything is probed or written;
  * the pasted key never reaches a log record or a response body.

`.env.local` in this checkout is a SYMLINK to the owner's live file, so the
autouse fixture below makes the API's own path function raise: only a test
that points it at `tmp_path` can write anything at all. The probe is always
faked — no test here touches the network — and every key is fictional.
"""
from __future__ import annotations

import logging
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from api import setup as setup_api
from core import secrets_redaction
from core.config import config

FICTIONAL_KEY = "sk-test-not-a-real-key"
FICTIONAL_DISCORD_TOKEN = "x" * 40
FICTIONAL_TELEGRAM_TOKEN = "123456:ABC-fictional-token"


# ── the guards every test in this module runs under ────────────────────────


@pytest.fixture(autouse=True)
def never_the_real_env_local(monkeypatch):
    """The checkout's `.env.local` is a symlink to the owner's live file.

    A test that forgets to redirect the writer must not be able to reach it,
    so the real path function is replaced by one that raises. Only
    `env_local` (below) hands the API somewhere it may write.
    """

    def _refuse() -> Path:
        raise AssertionError("a setup-API test reached the real .env.local")

    monkeypatch.setattr(setup_api, "_env_local_path", _refuse)


@pytest.fixture
def env_local(tmp_path, monkeypatch) -> Path:
    path = tmp_path / ".env.local"
    monkeypatch.setattr(setup_api, "_env_local_path", lambda: path)
    return path


@pytest.fixture(autouse=True)
def a_setup_mode_config(monkeypatch):
    """The stranger's first boot: no brain, both doors shut, CSRF enforced.

    EVERY value an assertion in this file depends on is pinned here. `Config`
    reads env at import and python-dotenv fills anything a test leaves unset
    from THIS checkout's `.env.local` — the owner's live file — so a value read
    and not pinned is a test that passes or fails on his install. That is the
    class that went red on this branch on 2026-09-14 (2173c0f, 91d5aaf).
    """
    monkeypatch.setattr(config, "brain_ready", False, raising=False)
    monkeypatch.setattr(config, "LLM_PROVIDER", "deepseek", raising=False)
    monkeypatch.setattr(config, "LLM_RESPONSE_MODEL", "", raising=False)
    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "", raising=False)
    monkeypatch.setattr(config, "OPENAI_API_KEY", "", raising=False)
    monkeypatch.setattr(config, "DEEPSEEK_MODEL", "deepseek-v4-pro", raising=False)
    # Everything a successful POST writes onto the live singleton: pinned here
    # so monkeypatch puts the process back afterwards. The API's job IS to
    # mutate these, so a test that did not pin them would leak into the next.
    monkeypatch.setattr(config, "OPENAI_MODEL", "gpt-4o", raising=False)
    monkeypatch.setattr(config, "LOCAL_LLM_API_BASE", "http://127.0.0.1:11434/v1", raising=False)
    monkeypatch.setattr(config, "LOCAL_LLM_API_KEY", "", raising=False)
    monkeypatch.setattr(config, "LOCAL_LLM_MODEL", "qwen3:14b", raising=False)
    monkeypatch.setattr(config, "FRONTEND_URL", "", raising=False)   # behind `address`
    # Behind `doors`: both tokens, both boot verdicts, and the two flags the
    # verdict falls back to on a config that never went through validate().
    monkeypatch.setattr(config, "DISCORD_TOKEN", "", raising=False)
    monkeypatch.setattr(config, "TELEGRAM_BOT_TOKEN", "", raising=False)
    monkeypatch.setattr(config, "ENABLE_DISCORD_BOT", False, raising=False)
    monkeypatch.setattr(config, "ENABLE_TELEGRAM_BOT", False, raising=False)
    monkeypatch.setattr(config, "effective_discord_enabled", False, raising=False)
    monkeypatch.setattr(config, "effective_telegram_enabled", False, raising=False)
    monkeypatch.setattr(config, "ENABLE_MOBILE_DOOR", False, raising=False)
    monkeypatch.setattr(config, "effective_mobile_enabled", False, raising=False)
    monkeypatch.setattr(config, "punch_list", None, raising=False)   # behind doors_need_restart
    # Behind `local_mode`, and behind `require_csrf`'s bypass branch. Pinned
    # local + no-auth + CSRF-on: the owner's own install, as a stranger's is.
    monkeypatch.setattr(config, "PALLI_DEPLOYMENT_MODE", "local", raising=False)
    monkeypatch.setattr(config, "LOCAL_DASHBOARD_NO_AUTH", True, raising=False)
    monkeypatch.setattr(config, "CSRF_PROTECT_LOCAL", True, raising=False)
    monkeypatch.setattr(config, "CSRF_COOKIE_NAME", "palli_csrf_for_this_test", raising=False)


@pytest.fixture
def client(monkeypatch):
    """A bare app with only this router on it, and the local owner resolved."""
    app = FastAPI()
    app.include_router(setup_api.router)

    async def fake_require_user(request):
        return {"id": 1, "email": "owner@example.com"}

    monkeypatch.setattr(setup_api, "_require_user", fake_require_user)
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://testserver")


def _csrf() -> dict:
    """The double-submit pair `GET /auth/me` hands the dashboard: the same
    value in the cookie and in the header."""
    token = "csrf-token-for-this-test"
    return {
        "X-CSRF-Token": token,
        "Cookie": f"{config.CSRF_COOKIE_NAME}={token}",
    }


class _Probe:
    """A stand-in for the live PONG. Records every call; never a network."""

    def __init__(self, *, ok=True, model="deepseek-v4-pro", balance=None, error=None, also_log=""):
        self.result = setup_api.ProbeResult(ok=ok, model=model, balance=balance, error=error)
        self.calls: list[dict] = []
        self.also_log = also_log

    async def __call__(self, provider, *, api_key="", base_url="", model=""):
        self.calls.append(
            {"provider": provider, "api_key": api_key, "base_url": base_url, "model": model}
        )
        if self.also_log:
            # A provider that logs its own request URL — the shape that put her
            # Telegram token in the log twelve times on 2026-09-17. Nobody
            # sanitizes this record; the filter has to.
            logging.getLogger("tests.a_talkative_provider").error("POST %s", self.also_log)
        return self.result


@pytest.fixture(autouse=True)
def _never_the_live_settings_file(monkeypatch):
    """No test here may reach the real `data/palli.yaml`.

    `POST /api/setup/brain` moves her background lanes on the first brain, and
    the live settings service writes the owner's own file. This module only
    survived that by accident once — the proposed routing happened to match
    what his file already said, so nothing was written. A test whose provider
    differed would have rewritten his settings. The stand-in makes it
    impossible; a test that wants to watch the proposal replaces it with its
    own recorder.
    """
    from types import SimpleNamespace as _NS

    inert = _NS(
        propose=lambda changes, reason="": _NS(change_id="autouse", diff=[]),
        apply=lambda change_id: _NS(rollback_token=""),
    )
    monkeypatch.setattr(setup_api, "_settings_service", lambda: inert)


@pytest.fixture
def a_brainless_client(monkeypatch):
    """A real LLMClient in setup mode, standing in for the process singleton.

    Not a mock: the swap under test is the real one, so what a test reads back
    is the registry the running process would actually be holding.
    """
    from ai import llm_client as llm_client_module

    client = llm_client_module.LLMClient()
    monkeypatch.setattr(llm_client_module, "llm", client)
    yield client


# ── (a) status ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_status_in_setup_mode_names_the_door_and_says_the_brain_is_not_ready(client):
    async with client as http:
        response = await http.get("/api/setup/status")

    assert response.status_code == 200
    body = response.json()
    assert body["brain_ready"] is False
    assert body["provider"] == "deepseek"
    assert body["address"] == "http://127.0.0.1:3000"
    assert body["local_mode"] is True
    assert body["doors"] == {"discord": "unset", "telegram": "unset", "mobile": "unset"}
    assert body["doors_need_restart"] is False


@pytest.mark.asyncio
async def test_status_with_a_key_reports_the_model_her_voice_will_run(client, monkeypatch):
    monkeypatch.setattr(config, "brain_ready", True, raising=False)
    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", FICTIONAL_KEY, raising=False)
    monkeypatch.setattr(config, "LLM_RESPONSE_MODEL", "deepseek-v4-pro", raising=False)

    async with client as http:
        body = (await http.get("/api/setup/status")).json()

    assert body["brain_ready"] is True
    assert body["model"] == "deepseek-v4-pro"


@pytest.mark.asyncio
async def test_status_reports_each_door_open_closed_or_unset(client, monkeypatch):
    monkeypatch.setattr(config, "DISCORD_TOKEN", FICTIONAL_DISCORD_TOKEN, raising=False)
    monkeypatch.setattr(config, "effective_discord_enabled", True, raising=False)
    monkeypatch.setattr(config, "TELEGRAM_BOT_TOKEN", FICTIONAL_TELEGRAM_TOKEN, raising=False)
    monkeypatch.setattr(config, "effective_telegram_enabled", False, raising=False)

    async with client as http:
        body = (await http.get("/api/setup/status")).json()

    assert body["doors"] == {"discord": "open", "telegram": "closed", "mobile": "unset"}


@pytest.mark.asyncio
async def test_status_offers_the_four_brains_with_the_matrixs_own_words(client):
    async with client as http:
        body = (await http.get("/api/setup/status")).json()

    menu = {row["name"]: row for row in body["providers"]}
    assert list(menu) == ["deepseek", "openai", "anthropic", "local_openai"]
    assert menu["deepseek"]["label"] == "DeepSeek V4"
    assert menu["deepseek"]["available"] is True
    assert menu["deepseek"]["reason"] is None
    assert menu["deepseek"]["needs"] == ["api_key"]
    assert menu["local_openai"]["needs"] == ["base_url", "model"]
    # The refusal is the matrix's sentence, never one written here.
    assert menu["anthropic"]["available"] is False
    # wording updated 2026-09-18 (release sweep): the reason a stranger reads is
    # plain English now, not a source path and an internal build document.
    assert "cannot be her voice yet" in menu["anthropic"]["reason"]
    assert "core/agent" not in menu["anthropic"]["reason"]
    assert "KICKOFF" not in menu["anthropic"]["reason"].upper()


@pytest.mark.asyncio
async def test_status_says_a_door_needs_the_restart_after_a_brain_arrives(client, monkeypatch):
    """The page's doors card has to say this after a reload too, so it is read
    off the punch list boot wrote rather than from the POST's own answer."""
    from core.punch_list import PunchList

    punch = PunchList()
    punch.add("Discord", "closed until a brain is configured", "its token is set", hard=False)
    monkeypatch.setattr(config, "punch_list", punch, raising=False)
    monkeypatch.setattr(config, "DISCORD_TOKEN", FICTIONAL_DISCORD_TOKEN, raising=False)
    monkeypatch.setattr(config, "brain_ready", True, raising=False)

    async with client as http:
        body = (await http.get("/api/setup/status")).json()

    assert body["doors_need_restart"] is True
    assert body["doors"]["discord"] == "closed"


# ── (b) a failing probe ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_failing_probe_writes_nothing_and_returns_the_providers_words(
    client, monkeypatch, env_local
):
    env_local.write_bytes(b"LLM_PROVIDER=deepseek\r\n")
    probe = _Probe(ok=False, error="401 Unauthorized: invalid api key")
    monkeypatch.setattr(setup_api, "probe_brain", probe)

    async with client as http:
        response = await http.post(
            "/api/setup/brain",
            json={"provider": "deepseek", "api_key": FICTIONAL_KEY},
            headers=_csrf(),
        )

    assert response.status_code == 200
    assert response.json() == {"ok": False, "error": "401 Unauthorized: invalid api key"}
    # Nothing written, nothing on the config, nothing swapped.
    assert env_local.read_bytes() == b"LLM_PROVIDER=deepseek\r\n"
    assert config.DEEPSEEK_API_KEY == ""
    assert config.brain_ready is False


# ── (c) a passing probe ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_passing_probe_saves_the_key_and_brings_her_brain_up(
    client, monkeypatch, env_local, a_brainless_client
):
    from ai.llm_provider_registry import NullProvider
    from ai.llm_providers import DeepSeekProvider

    env_local.write_bytes(b"# her env\r\nLLM_PROVIDER=openai\r\nLLM_RESPONSE_MODEL=gpt-4o\r\n")
    assert isinstance(a_brainless_client.provider, NullProvider)
    monkeypatch.setattr(
        setup_api, "probe_brain", _Probe(ok=True, model="deepseek-v4-pro", balance="1.96 USD")
    )

    async with client as http:
        response = await http.post(
            "/api/setup/brain",
            json={"provider": "deepseek", "api_key": FICTIONAL_KEY},
            headers=_csrf(),
        )

    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "provider": "deepseek",
        "model": "deepseek-v4-pro",
        "balance": "1.96 USD",
        "lanes": "moved",
        "doors_need_restart": False,
        "restart_required": False,
    }

    written = env_local.read_bytes().decode("utf-8")
    assert "LLM_PROVIDER=deepseek" in written
    assert "LLM_RESPONSE_MODEL=deepseek-v4-pro" in written
    assert "DEEPSEEK_MODEL=deepseek-v4-pro" in written
    assert f"DEEPSEEK_API_KEY={FICTIONAL_KEY}" in written
    assert "# her env" in written  # every untouched line kept
    assert written.count("\r\n") == len(written.splitlines())  # still CRLF

    # The running process, not just the file.
    assert config.brain_ready is True
    assert config.LLM_PROVIDER == "deepseek"
    assert config.DEEPSEEK_API_KEY == FICTIONAL_KEY
    assert isinstance(a_brainless_client.provider, DeepSeekProvider)
    assert not any(isinstance(p, NullProvider) for p in a_brainless_client.providers.values())
    await a_brainless_client.close()


@pytest.mark.asyncio
async def test_a_local_server_saves_its_address_and_model(
    client, monkeypatch, env_local, a_brainless_client
):
    monkeypatch.setattr(setup_api, "probe_brain", _Probe(ok=True, model="qwen3:14b"))

    async with client as http:
        response = await http.post(
            "/api/setup/brain",
            json={
                "provider": "local_openai",
                "base_url": "http://192.0.2.9:11434/v1",
                "model": "qwen3:14b",
            },
            headers=_csrf(),
        )

    assert response.json()["ok"] is True
    written = env_local.read_text(encoding="utf-8")
    assert "LLM_PROVIDER=local_openai" in written
    assert "LOCAL_LLM_API_BASE=http://192.0.2.9:11434/v1" in written
    assert "LOCAL_LLM_MODEL=qwen3:14b" in written
    assert "LLM_RESPONSE_MODEL=qwen3:14b" in written
    assert "LOCAL_LLM_API_KEY" not in written  # none was given, so none is written
    await a_brainless_client.close()


@pytest.mark.asyncio
async def test_a_key_pasted_with_spaces_around_it_is_stripped(
    client, monkeypatch, env_local, a_brainless_client
):
    probe = _Probe(ok=True)
    monkeypatch.setattr(setup_api, "probe_brain", probe)

    async with client as http:
        await http.post(
            "/api/setup/brain",
            json={"provider": "deepseek", "api_key": f"  {FICTIONAL_KEY}\n"},
            headers=_csrf(),
        )

    assert probe.calls[0]["api_key"] == FICTIONAL_KEY
    assert f"DEEPSEEK_API_KEY={FICTIONAL_KEY}" in env_local.read_text(encoding="utf-8")
    await a_brainless_client.close()


@pytest.mark.asyncio
async def test_a_second_brain_is_allowed_when_one_already_works(
    client, monkeypatch, env_local, a_brainless_client
):
    """Ruling P11: this is how someone whose local model server is not running
    picks a different brain. The matrix check is the safety refusal, not a
    "you already have one" rule."""
    monkeypatch.setattr(config, "brain_ready", True, raising=False)
    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "sk-test-an-older-key", raising=False)
    probe = _Probe(ok=True, model="gpt-4o")
    monkeypatch.setattr(setup_api, "probe_brain", probe)

    async with client as http:
        response = await http.post(
            "/api/setup/brain",
            json={"provider": "openai", "api_key": FICTIONAL_KEY, "model": "gpt-4o"},
            headers=_csrf(),
        )

    assert response.json()["ok"] is True
    assert probe.calls[0]["provider"] == "openai"
    assert config.LLM_PROVIDER == "openai"
    assert f"OPENAI_API_KEY={FICTIONAL_KEY}" in env_local.read_text(encoding="utf-8")
    await a_brainless_client.close()


@pytest.mark.asyncio
async def test_the_answer_says_which_doors_still_need_the_restart(
    client, monkeypatch, env_local, a_brainless_client
):
    from core.punch_list import PunchList

    punch = PunchList()
    punch.add("Telegram", "closed until a brain is configured", "its token is set", hard=False)
    monkeypatch.setattr(config, "punch_list", punch, raising=False)
    monkeypatch.setattr(setup_api, "probe_brain", _Probe(ok=True))

    async with client as http:
        response = await http.post(
            "/api/setup/brain",
            json={"provider": "deepseek", "api_key": FICTIONAL_KEY},
            headers=_csrf(),
        )

    assert response.json()["doors_need_restart"] is True
    await a_brainless_client.close()


# ── the swap that fails (spec §3) ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_save_that_works_with_a_swap_that_fails_stays_in_setup_mode(
    client, monkeypatch, env_local, a_brainless_client
):
    """Posted as OPENAI on purpose: the fixture's provider is deepseek, so the
    rollback of `LLM_PROVIDER` and of the client's default provider is a real
    assertion and not a value that never moved."""
    from ai import llm_client as llm_client_module
    from ai.llm_provider_registry import NullProvider
    from core.punch_list import PunchList

    punch = PunchList()
    punch.add("Telegram", "closed until a brain is configured", "its token is set", hard=False)
    monkeypatch.setattr(config, "punch_list", punch, raising=False)

    def _explode():
        raise RuntimeError("a provider blew up while being built")

    monkeypatch.setattr(llm_client_module, "build_default_provider_registry", _explode)
    monkeypatch.setattr(setup_api, "probe_brain", _Probe(ok=True, model="gpt-4o"))

    async with client as http:
        response = await http.post(
            "/api/setup/brain",
            json={"provider": "openai", "api_key": FICTIONAL_KEY, "model": "gpt-4o"},
            headers=_csrf(),
        )

    body = response.json()
    assert body["ok"] is True
    assert body["restart_required"] is True
    # The two fields describe the same world: the restart this answer asks for
    # is the one the waiting door wants too.
    assert body["doors_need_restart"] is True
    # The key is on disk — that is why a restart is enough.
    assert f"OPENAI_API_KEY={FICTIONAL_KEY}" in env_local.read_text(encoding="utf-8")
    # And the running process is honestly still in setup mode: no half-state.
    assert config.brain_ready is False
    assert config.OPENAI_API_KEY == ""
    assert config.LLM_PROVIDER == "deepseek"           # rolled back, not left on openai
    assert a_brainless_client.default_provider_name == "deepseek"
    assert isinstance(a_brainless_client.provider, NullProvider)
    await a_brainless_client.close()


@pytest.mark.asyncio
async def test_a_wrong_key_shows_the_providers_own_words_with_the_key_scrubbed(
    client, monkeypatch, env_local, every_log_record
):
    """Spec §3's first line: "Wrong key: the provider's own error text under the
    field". Until 2026-09-17 the page got "empty response" for a real DeepSeek
    401, because the provider swallowed the HTTP error before the probe saw it
    (fix round 2). The probe scrubs the key out of those words; the API's
    `covering()` block is the second pair of hands on the same rope."""
    monkeypatch.setattr(
        setup_api,
        "probe_brain",
        _Probe(
            ok=False,
            error="401 Unauthorized: Authentication Fails, Your api key is invalid",
            also_log=f"POST https://api.deepseek.com/chat/completions key={FICTIONAL_KEY}",
        ),
    )

    async with client as http:
        response = await http.post(
            "/api/setup/brain",
            json={"provider": "deepseek", "api_key": FICTIONAL_KEY},
            headers=_csrf(),
        )

    body = response.json()
    assert body["ok"] is False
    assert body["error"] == "401 Unauthorized: Authentication Fails, Your api key is invalid"
    assert FICTIONAL_KEY not in response.text
    assert not [line for line in every_log_record if FICTIONAL_KEY in line]
    assert not env_local.exists()          # a failed probe still writes nothing
    assert config.brain_ready is False


# ── the write that will not take (spec §3's other half) ────────────────────


@pytest.mark.asyncio
async def test_a_write_that_fails_after_a_good_probe_says_so_instead_of_a_500(
    client, monkeypatch, a_brainless_client
):
    """A read-only or wrong-owned `.env.local`, or a value with a line break in
    it: the probe PASSED and then the file would not take it. That is a
    sentence a stranger can act on, not a bare 500 — and nothing is on the
    config and nothing is swapped, so the process is where it was."""
    from ai.llm_provider_registry import NullProvider

    def _read_only(updates):
        raise PermissionError("[Errno 13] Permission denied: '.env.local'")

    monkeypatch.setattr(setup_api, "_write_env_local", _read_only)
    monkeypatch.setattr(setup_api, "probe_brain", _Probe(ok=True))

    async with client as http:
        response = await http.post(
            "/api/setup/brain",
            json={"provider": "deepseek", "api_key": FICTIONAL_KEY},
            headers=_csrf(),
        )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert ".env.local could not be written" in body["error"]
    assert "Permission denied" in body["error"]
    assert FICTIONAL_KEY not in response.text
    assert config.brain_ready is False
    assert config.DEEPSEEK_API_KEY == ""
    assert isinstance(a_brainless_client.provider, NullProvider)
    await a_brainless_client.close()


# ── the model that will run is never blank ─────────────────────────────────


@pytest.mark.asyncio
async def test_a_probe_that_names_no_model_falls_back_to_the_providers_own(
    client, monkeypatch, env_local, a_brainless_client
):
    """Writing `LLM_RESPONSE_MODEL=` would also blank DEEPSEEK_MODEL and leave
    her voice on PaLLi's built-in default."""
    monkeypatch.setattr(setup_api, "probe_brain", _Probe(ok=True, model=""))

    async with client as http:
        response = await http.post(
            "/api/setup/brain",
            json={"provider": "deepseek", "api_key": FICTIONAL_KEY},
            headers=_csrf(),
        )

    assert response.json()["model"] == "deepseek-v4-pro"   # config.DEEPSEEK_MODEL
    written = env_local.read_text(encoding="utf-8")
    assert "LLM_RESPONSE_MODEL=deepseek-v4-pro" in written
    assert "DEEPSEEK_MODEL=deepseek-v4-pro" in written
    await a_brainless_client.close()


@pytest.mark.asyncio
async def test_no_model_anywhere_is_said_out_loud_rather_than_written_blank(
    client, monkeypatch, env_local
):
    monkeypatch.setattr(config, "DEEPSEEK_MODEL", "", raising=False)
    monkeypatch.setattr(setup_api, "probe_brain", _Probe(ok=True, model=""))

    async with client as http:
        response = await http.post(
            "/api/setup/brain",
            json={"provider": "deepseek", "api_key": FICTIONAL_KEY},
            headers=_csrf(),
        )

    body = response.json()
    assert body["ok"] is False
    assert "DEEPSEEK_MODEL" in body["error"]
    assert not env_local.exists()          # nothing was written at all
    assert config.brain_ready is False


# ── the refusal the matrix owns (ruling P1) ────────────────────────────────


@pytest.mark.asyncio
async def test_anthropic_is_refused_out_loud_with_nothing_probed_or_written(
    client, monkeypatch, env_local
):
    """A PONG through AnthropicProvider would pass and the key would be saved —
    and her first reply would break, because dispatch never selects the native
    Anthropic path. So the matrix is asked BEFORE the probe."""
    env_local.write_bytes(b"LLM_PROVIDER=deepseek\r\n")

    async def _never(*args, **kwargs):
        raise AssertionError("anthropic was probed")

    monkeypatch.setattr(setup_api, "probe_brain", _never)

    async with client as http:
        response = await http.post(
            "/api/setup/brain",
            json={"provider": "anthropic", "api_key": FICTIONAL_KEY},
            headers=_csrf(),
        )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert "cannot be her voice yet" in body["error"]
    assert env_local.read_bytes() == b"LLM_PROVIDER=deepseek\r\n"
    assert config.brain_ready is False


@pytest.mark.asyncio
async def test_a_provider_name_nobody_knows_is_a_bad_request(client, monkeypatch):
    async def _never(*args, **kwargs):
        raise AssertionError("an unknown provider was probed")

    monkeypatch.setattr(setup_api, "probe_brain", _never)

    async with client as http:
        response = await http.post(
            "/api/setup/brain",
            json={"provider": "a-brain-i-invented", "api_key": FICTIONAL_KEY},
            headers=_csrf(),
        )

    assert response.status_code == 400
    assert "deepseek" in response.json()["detail"]
    # The sentence echoes nothing the caller sent — not the name, not the key.
    assert "a-brain-i-invented" not in response.text
    assert FICTIONAL_KEY not in response.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body, expected_status",
    [
        ({"api_key": FICTIONAL_KEY}, 400),                        # no provider at all
        ({"provider": "", "api_key": FICTIONAL_KEY}, 400),        # blank
        ({"provider": "deepseek", "api_key": 123}, 422),          # wrong type
        ({"provider": "deepseek", "api_key": {"k": FICTIONAL_KEY}}, 422),  # a dict where a str goes
    ],
)
async def test_a_malformed_request_never_quotes_the_key_back(
    client, monkeypatch, body, expected_status
):
    """FastAPI's validation error carries `input`, and for a `missing` error
    that input is the WHOLE body — the pasted key with it. The request model
    has no required field so `missing` cannot happen, and the router's route
    class drops `input` from whatever else pydantic raises."""

    async def _never(*args, **kwargs):
        raise AssertionError("a malformed request was probed")

    monkeypatch.setattr(setup_api, "probe_brain", _never)

    async with client as http:
        response = await http.post("/api/setup/brain", json=body, headers=_csrf())

    assert response.status_code == expected_status
    assert FICTIONAL_KEY not in response.text, response.text
    assert '"input"' not in response.text, response.text


@pytest.mark.asyncio
async def test_a_key_needing_provider_with_no_key_is_refused_before_the_probe(client, monkeypatch):
    async def _never(*args, **kwargs):
        raise AssertionError("a keyless provider was probed")

    monkeypatch.setattr(setup_api, "probe_brain", _never)

    async with client as http:
        response = await http.post(
            "/api/setup/brain",
            json={"provider": "deepseek", "api_key": "   "},
            headers=_csrf(),
        )

    assert response.status_code == 400
    assert "key" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_a_local_server_with_no_address_is_refused_before_the_probe(client, monkeypatch):
    async def _never(*args, **kwargs):
        raise AssertionError("a local server with no address was probed")

    monkeypatch.setattr(setup_api, "probe_brain", _never)

    async with client as http:
        response = await http.post(
            "/api/setup/brain",
            json={"provider": "local_openai", "model": "qwen3:14b"},
            headers=_csrf(),
        )

    assert response.status_code == 400


# ── (d) the key never reaches a log record or a response ───────────────────


@pytest.fixture
def every_log_record(monkeypatch, tmp_path):
    """A capturing handler under the filter boot installs (main.py:45).

    The denylist is pointed at an empty tmp file, so what these tests prove is
    `covering()` doing the work and never the owner's own `data/secrets_denylist.txt`
    happening to hold something.
    """
    monkeypatch.setattr(secrets_redaction, "DENYLIST_PATH", tmp_path / "denylist.txt")
    secrets_redaction.reset_for_tests()
    records: list[str] = []

    class _Capture(logging.Handler):
        def emit(self, record):
            records.append(self.format(record))

    handler = _Capture()
    handler.setFormatter(logging.Formatter("%(name)s %(message)s"))
    handler.addFilter(secrets_redaction.RedactingFilter())
    root = logging.getLogger()
    root.addHandler(handler)
    root.addFilter(secrets_redaction.RedactingFilter())
    previous_level, root.level = root.level, logging.DEBUG
    try:
        yield records
    finally:
        root.level = previous_level
        root.removeHandler(handler)
        root.filters = [f for f in root.filters if not isinstance(f, secrets_redaction.RedactingFilter)]
        secrets_redaction.reset_for_tests()


@pytest.mark.asyncio
async def test_the_pasted_key_is_in_no_log_record_and_in_no_response_body(
    client, monkeypatch, env_local, every_log_record
):
    """During the call the key is NOT on the config yet, so the redaction that
    covers her own credentials by construction does not cover it. It has to be
    registered for the duration of the request — and the provider's own error
    text has to be scrubbed on the way out."""
    monkeypatch.setattr(
        setup_api,
        "probe_brain",
        _Probe(
            ok=False,
            error=f"401 Unauthorized for key {FICTIONAL_KEY}",
            also_log=f"https://api.deepseek.com/chat?key={FICTIONAL_KEY}",
        ),
    )

    async with client as http:
        response = await http.post(
            "/api/setup/brain",
            json={"provider": "deepseek", "api_key": FICTIONAL_KEY},
            headers=_csrf(),
        )

    assert FICTIONAL_KEY not in response.text
    assert response.json()["ok"] is False
    assert "[REDACTED]" in response.json()["error"]
    assert every_log_record, "the call emitted no log records at all"
    assert not [line for line in every_log_record if FICTIONAL_KEY in line], every_log_record
    assert any("[REDACTED]" in line for line in every_log_record)


@pytest.mark.asyncio
async def test_the_cover_is_lifted_once_the_request_is_over(
    client, monkeypatch, env_local, a_brainless_client, every_log_record
):
    """Registered for the duration of the call, and released after it — the
    filter must not grow a literal per request for the life of the process."""
    monkeypatch.setattr(setup_api, "probe_brain", _Probe(ok=False, error="no"))

    async with client as http:
        await http.post(
            "/api/setup/brain",
            json={"provider": "deepseek", "api_key": FICTIONAL_KEY},
            headers=_csrf(),
        )

    assert secrets_redaction.redact(FICTIONAL_KEY) == FICTIONAL_KEY


@pytest.mark.asyncio
async def test_a_saved_key_stays_redacted_after_the_request(
    client, monkeypatch, env_local, a_brainless_client, every_log_record
):
    """The cover is lifted at the end of the request — but a SAVED key is on
    her config by then, and the rule that redacts every credential the config
    holds takes over. The two must meet without a gap: the log filter rebuilds
    its literals lazily and nothing watches the config, so `_activate` says so
    with `refresh()` rather than leaning on `covering()`'s exit happening to
    move the same cache key (final wave, minor 5)."""
    monkeypatch.setattr(setup_api, "probe_brain", _Probe(ok=True))

    async with client as http:
        response = await http.post(
            "/api/setup/brain",
            json={"provider": "deepseek", "api_key": FICTIONAL_KEY},
            headers=_csrf(),
        )

    assert response.json()["ok"] is True
    assert config.DEEPSEEK_API_KEY == FICTIONAL_KEY       # it really was saved
    assert not secrets_redaction._covered                 # no covering block is open
    assert secrets_redaction.redact(FICTIONAL_KEY) == "[REDACTED]"
    await a_brainless_client.close()


# ── (e) CSRF — the real rule, not a stand-in ───────────────────────────────


@pytest.mark.asyncio
async def test_a_post_without_the_csrf_pair_is_refused(client, monkeypatch, env_local):
    """`require_csrf` itself, unpatched: the double-submit cookie is the only
    thing between a malicious page on this LAN and a brain swap."""

    async def _never(*args, **kwargs):
        raise AssertionError("a request with no CSRF token was probed")

    monkeypatch.setattr(setup_api, "probe_brain", _never)

    async with client as http:
        response = await http.post(
            "/api/setup/brain", json={"provider": "deepseek", "api_key": FICTIONAL_KEY}
        )

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_status_is_readable_before_the_csrf_cookie_exists(client):
    """The page calls this first, to decide what to render. It changes
    nothing, so it takes the owner rule and not the CSRF pair."""
    async with client as http:
        response = await http.get("/api/setup/status")

    assert response.status_code == 200


@pytest.mark.asyncio
async def test_an_anonymous_caller_gets_a_clean_401(monkeypatch):
    """Auth runs before CSRF, the order the chat route keeps."""
    from fastapi import HTTPException

    app = FastAPI()
    app.include_router(setup_api.router)

    async def _anonymous(request):
        raise HTTPException(status_code=401, detail="Not authenticated")

    monkeypatch.setattr(setup_api, "_require_user", _anonymous)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as http:
        status = await http.get("/api/setup/status")
        brain = await http.post(
            "/api/setup/brain", json={"provider": "deepseek", "api_key": FICTIONAL_KEY}
        )

    assert status.status_code == 401
    assert brain.status_code == 401


# ── (g) her lanes follow the brain picked at the door ───────────────────────


class _RecordingSettings:
    """Stands in for the settings service: records what the door proposes."""

    def __init__(self, *, fail: bool = False):
        self.proposed: dict = {}
        self.applied: list[str] = []
        self.fail = fail

    def propose(self, changes, *, reason=""):
        if self.fail:
            raise RuntimeError("the settings file is read-only")
        self.proposed = dict(changes)
        self.reason = reason
        return SimpleNamespace(change_id="c1", diff=[])

    def apply(self, change_id):
        self.applied.append(change_id)
        return SimpleNamespace(rollback_token="t1")


@pytest.mark.asyncio
async def test_the_first_brain_moves_her_background_lanes_too(
    client, monkeypatch, env_local, a_brainless_client
):
    """Her voice reads .env.local; her nine other lanes read data/palli.yaml.

    Move only her voice and a stranger who pastes a cloud key gets a companion
    that talks perfectly and remembers nothing — no memory, no reflection, no
    appraisal — because those lanes are still pointed at a local server they
    never had. The door's first brain moves both.
    """
    settings = _RecordingSettings()
    monkeypatch.setattr(setup_api, "_settings_service", lambda: settings)
    monkeypatch.setattr(setup_api, "probe_brain", _Probe(ok=True, model="deepseek-chat"))

    async with client as http:
        response = await http.post(
            "/api/setup/brain",
            json={"provider": "deepseek", "api_key": FICTIONAL_KEY},
            headers=_csrf(),
        )

    body = response.json()
    assert body["ok"] is True
    assert body["lanes"] == "moved"
    assert settings.applied == ["c1"]
    assert settings.proposed["llm.default_provider"] == "deepseek"
    # every text lane, on the model the probe just proved — not a preset's guess
    assert settings.proposed["llm.routing.memory"] == {
        "provider": "deepseek",
        "model": "deepseek-chat",
    }
    assert settings.proposed["llm.routing.reflection"]["provider"] == "deepseek"
    assert settings.proposed["llm.routing.narrative_author"]["provider"] == "deepseek"
    await a_brainless_client.close()


@pytest.mark.asyncio
async def test_a_brain_that_cannot_see_leaves_the_vision_lane_where_it_is(
    client, monkeypatch, env_local, a_brainless_client
):
    """DeepSeek reads no images. Pointing her vision lane at it would trade a
    lane that might work for one that certainly cannot."""
    settings = _RecordingSettings()
    monkeypatch.setattr(setup_api, "_settings_service", lambda: settings)
    monkeypatch.setattr(setup_api, "probe_brain", _Probe(ok=True, model="deepseek-chat"))

    async with client as http:
        await http.post(
            "/api/setup/brain",
            json={"provider": "deepseek", "api_key": FICTIONAL_KEY},
            headers=_csrf(),
        )

    assert "llm.routing.vision" not in settings.proposed
    await a_brainless_client.close()


@pytest.mark.asyncio
async def test_a_brain_that_can_see_takes_the_vision_lane_with_it(
    client, monkeypatch, env_local, a_brainless_client
):
    settings = _RecordingSettings()
    monkeypatch.setattr(setup_api, "_settings_service", lambda: settings)
    monkeypatch.setattr(setup_api, "probe_brain", _Probe(ok=True, model="gpt-4o"))

    async with client as http:
        await http.post(
            "/api/setup/brain",
            json={"provider": "openai", "api_key": FICTIONAL_KEY},
            headers=_csrf(),
        )

    assert settings.proposed["llm.routing.vision"] == {
        "provider": "openai",
        "model": "gpt-4o",
    }
    await a_brainless_client.close()


@pytest.mark.asyncio
async def test_changing_an_existing_brain_leaves_the_lanes_alone(
    client, monkeypatch, env_local
):
    """Only the FIRST brain moves the lanes. Somebody who already has one has
    had every chance to tune their routing, and the door must not flatten it."""
    settings = _RecordingSettings()
    monkeypatch.setattr(setup_api, "_settings_service", lambda: settings)
    monkeypatch.setattr(setup_api, "probe_brain", _Probe(ok=True, model="gpt-4o"))
    monkeypatch.setattr(setup_api.config, "brain_ready", True, raising=False)

    async with client as http:
        response = await http.post(
            "/api/setup/brain",
            json={"provider": "openai", "api_key": FICTIONAL_KEY},
            headers=_csrf(),
        )

    assert response.json()["ok"] is True
    assert response.json()["lanes"] == "kept"
    assert settings.proposed == {}
    assert settings.applied == []


@pytest.mark.asyncio
async def test_a_brain_still_arrives_when_the_lanes_cannot_be_moved(
    client, monkeypatch, env_local, a_brainless_client
):
    """The key passed and is saved. A settings file that will not take the
    routing must not undo that — it is reported, and she can still talk."""
    settings = _RecordingSettings(fail=True)
    monkeypatch.setattr(setup_api, "_settings_service", lambda: settings)
    monkeypatch.setattr(setup_api, "probe_brain", _Probe(ok=True, model="deepseek-chat"))

    async with client as http:
        response = await http.post(
            "/api/setup/brain",
            json={"provider": "deepseek", "api_key": FICTIONAL_KEY},
            headers=_csrf(),
        )

    body = response.json()
    assert body["ok"] is True
    assert body["lanes"].startswith("not moved")
    assert "LLM_PROVIDER=deepseek" in env_local.read_text(encoding="utf-8")
    await a_brainless_client.close()


@pytest.mark.asyncio
async def test_the_phone_app_door_is_unset_until_switched_on_then_closed_or_open(client, monkeypatch):
    async with client as http:
        unset = (await http.get("/api/setup/status")).json()["doors"]["mobile"]
        monkeypatch.setattr(config, "ENABLE_MOBILE_DOOR", True, raising=False)
        closed = (await http.get("/api/setup/status")).json()["doors"]["mobile"]
        monkeypatch.setattr(config, "effective_mobile_enabled", True, raising=False)
        opened = (await http.get("/api/setup/status")).json()["doors"]["mobile"]
    assert (unset, closed, opened) == ("unset", "closed", "open")
