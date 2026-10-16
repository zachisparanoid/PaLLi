"""What is left on the account (stranger-proofing week 1, task 1b).

The balance ran to zero twice in one week without a word — every reply failed and
nothing anywhere said why. `fetch_balance` is the readout the preflight and the
doctor print. It is a courtesy, never a gate: no balance API, no key, a refusing
endpoint or a shape we do not recognise all return None and cost nobody a boot.

No test here reaches the internet: DeepSeek's endpoint is redirected at
`_BALANCE_ENDPOINT` to a local stub that answers the documented shape.
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from palli_config import providers as providers_module
from palli_config.providers import BalanceInfo, fetch_balance


@pytest.fixture
def balance_server():
    """A stub for GET /user/balance that answers whatever it is handed."""
    state: dict = {"status": 200, "body": {}, "auth": []}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            state["auth"].append(self.headers.get("Authorization", ""))
            if state["status"] != 200:
                self.send_error(state["status"])
                return
            raw = json.dumps(state["body"]).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, *_args):  # keep pytest's output clean
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    server.state = state  # type: ignore[attr-defined]
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()


@pytest.fixture
def deepseek_at(balance_server, monkeypatch):
    """Point the deepseek balance door at the stub, with a key set."""
    host, port = balance_server.server_address[:2]
    monkeypatch.setitem(
        providers_module._BALANCE_ENDPOINT,
        "deepseek",
        f"http://{host}:{port}/user/balance",
    )
    monkeypatch.setattr(
        providers_module.config, "DEEPSEEK_API_KEY", "sk-test", raising=False
    )
    return balance_server


@pytest.mark.asyncio
async def test_a_provider_with_no_balance_api_answers_none():
    assert await fetch_balance("openai") is None


@pytest.mark.asyncio
async def test_the_usd_entry_wins_over_the_others(deepseek_at):
    deepseek_at.state["body"] = {
        "is_available": True,
        "balance_infos": [
            {"currency": "CNY", "total_balance": "88.00"},
            {"currency": "USD", "total_balance": "12.34"},
        ],
    }

    balance = await fetch_balance("deepseek")

    assert balance == BalanceInfo(
        provider="deepseek", currency="USD", total_balance=12.34, available=True
    )


@pytest.mark.asyncio
async def test_the_first_entry_is_used_when_there_is_no_usd(deepseek_at):
    deepseek_at.state["body"] = {
        "is_available": False,
        "balance_infos": [{"currency": "CNY", "total_balance": "0.00"}],
    }

    balance = await fetch_balance("deepseek")

    assert balance is not None
    assert balance.currency == "CNY"
    assert balance.total_balance == 0.0
    assert balance.available is False


@pytest.mark.asyncio
async def test_a_missing_availability_field_is_not_a_shut_off_account(deepseek_at):
    """Final review: silence is not "marked unavailable". A payload with no
    `is_available` key used to read as False, and the preflight then told the
    reader their perfectly good account was shut off. Only an explicit false
    means that."""
    deepseek_at.state["body"] = {
        "balance_infos": [{"currency": "USD", "total_balance": "12.34"}],
    }

    balance = await fetch_balance("deepseek")

    assert balance is not None
    assert balance.available is True


@pytest.mark.asyncio
async def test_only_the_first_of_several_keys_is_sent(deepseek_at, monkeypatch):
    """DEEPSEEK_API_KEY may be a comma-separated rotation; the header takes one."""
    monkeypatch.setattr(
        providers_module.config,
        "DEEPSEEK_API_KEY",
        "sk-first,sk-second",
        raising=False,
    )
    deepseek_at.state["body"] = {
        "is_available": True,
        "balance_infos": [{"currency": "USD", "total_balance": "5.00"}],
    }

    await fetch_balance("deepseek")

    assert deepseek_at.state["auth"] == ["Bearer sk-first"]


@pytest.mark.asyncio
async def test_a_refused_call_is_none_not_an_exception(deepseek_at):
    deepseek_at.state["status"] = 401

    assert await fetch_balance("deepseek") is None


@pytest.mark.asyncio
async def test_an_unrecognised_shape_is_none(deepseek_at):
    deepseek_at.state["body"] = {"is_available": True, "balance_infos": []}

    assert await fetch_balance("deepseek") is None


@pytest.mark.asyncio
async def test_no_key_means_no_call(deepseek_at, monkeypatch):
    monkeypatch.setattr(
        providers_module.config, "DEEPSEEK_API_KEY", "", raising=False
    )

    assert await fetch_balance("deepseek") is None
    assert deepseek_at.state["auth"] == []
