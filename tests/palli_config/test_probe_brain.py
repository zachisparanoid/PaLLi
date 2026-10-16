"""The live test on the door (first door, task 2, 2026-09-17).

`probe_brain` is the one PONG a stranger watches happen: it builds the very
class her voice will run on, with the key they just pasted, asks it one
question and reads the balance where the provider reports one.

Two rules this file exists to hold:

  * the pasted key is passed EXPLICITLY, never written onto the live `config`
    to make the probe work. Nothing is saved until the probe passes, and a
    failed probe must leave the running process exactly as it found it;
  * the probe's HTTP session is closed. It is a throwaway provider; leaving
    its connector open would leak one per attempt, and a stranger's first
    minute is several attempts.

No test here reaches the network: the provider is a stand-in injected the way
`validate_provider`'s `_provider_factory` already allows, and the balance read
is redirected at the module. Fictional keys only.
"""
from __future__ import annotations

import json
import random
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from palli_config import providers as providers_module
from palli_config.providers import BalanceInfo, ProbeResult, probe_brain, validate_provider

FICTIONAL_KEY = "sk-test-not-a-real-key"
OTHER_FICTIONAL_KEY = "sk-test-some-other-key"


class _FakeProvider:
    """A stand-in for the class the door would really build.

    Records what it was constructed with and what it was asked, so a test can
    pin that the pasted credential — not the config's — reached the wire.
    """

    def __init__(self, *, reply="PONG", error=None, default_model="the-model", **built_with):
        self.reply = reply
        self.error = error
        self.default_model = default_model
        self.built_with = built_with
        self.calls: list[dict] = []
        self.closed = False

    async def chat_completion(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.reply

    async def close(self):
        self.closed = True


def _factory(provider):
    def build(name, *, api_key="", base_url="", model=""):
        provider.built_with = {"name": name, "api_key": api_key, "base_url": base_url, "model": model}
        return provider

    return build


@pytest.fixture
def balance_asks(monkeypatch):
    """The balance read never leaves the process; the list records every ask."""
    asks: list[tuple[str, str]] = []

    async def _record(name, *, api_key="", timeout=5.0):
        asks.append((name, api_key))
        return None

    monkeypatch.setattr(providers_module, "fetch_balance", _record)
    return asks


@pytest.mark.asyncio
async def test_a_passing_probe_reports_the_model_and_the_balance(monkeypatch):
    fake = _FakeProvider(reply="PONG")

    async def _balance(name, *, api_key="", timeout=5.0):
        assert (name, api_key) == ("deepseek", FICTIONAL_KEY)
        return BalanceInfo(provider="deepseek", currency="USD", total_balance=1.96, available=True)

    monkeypatch.setattr(providers_module, "fetch_balance", _balance)

    result = await probe_brain(
        "deepseek", api_key=FICTIONAL_KEY, model="deepseek-v4-pro", _provider_factory=_factory(fake)
    )

    assert isinstance(result, ProbeResult)
    assert result.ok is True
    assert result.error is None
    assert result.model == "deepseek-v4-pro"
    assert result.balance == "1.96 USD"


@pytest.mark.asyncio
async def test_a_probe_with_no_model_named_reports_the_one_that_would_run(balance_asks):
    """The stranger typed no model, so the answer is the provider's own
    default — the model her voice will actually run, not a blank."""
    fake = _FakeProvider(default_model="qwen3:14b")

    result = await probe_brain(
        "local_openai", base_url="http://127.0.0.1:11434/v1", _provider_factory=_factory(fake)
    )

    assert result.ok is True
    assert result.model == "qwen3:14b"


@pytest.mark.asyncio
async def test_a_failing_probe_carries_the_providers_own_words(balance_asks):
    fake = _FakeProvider(error=RuntimeError("401 Unauthorized: invalid api key"))

    result = await probe_brain("openai", api_key=FICTIONAL_KEY, _provider_factory=_factory(fake))

    assert result.ok is False
    assert result.balance is None
    assert "401 Unauthorized: invalid api key" in (result.error or "")


@pytest.mark.asyncio
async def test_an_empty_answer_is_a_failure(balance_asks):
    """A reasoning model that burns its budget answers nothing — the same
    rule `validate_provider` has always kept."""
    fake = _FakeProvider(reply="   ")

    result = await probe_brain("openai", api_key=FICTIONAL_KEY, _provider_factory=_factory(fake))

    assert result.ok is False
    assert result.error == "empty response"


@pytest.mark.asyncio
async def test_the_probes_session_is_always_closed(balance_asks):
    passing, failing = _FakeProvider(), _FakeProvider(error=RuntimeError("no"))

    await probe_brain("openai", api_key=FICTIONAL_KEY, _provider_factory=_factory(passing))
    await probe_brain("openai", api_key=FICTIONAL_KEY, _provider_factory=_factory(failing))

    assert passing.closed is True
    assert failing.closed is True


@pytest.mark.asyncio
async def test_the_pasted_key_reaches_the_provider_and_never_the_config(balance_asks, monkeypatch):
    """The whole point: the key is not saved yet, so it is handed to the
    provider as an argument. Writing it onto `config` to make the probe work
    would leave it there when the probe failed."""
    from core.config import config

    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", OTHER_FICTIONAL_KEY, raising=False)
    fake = _FakeProvider()

    await probe_brain("deepseek", api_key=FICTIONAL_KEY, _provider_factory=_factory(fake))

    assert fake.built_with["api_key"] == FICTIONAL_KEY
    assert config.DEEPSEEK_API_KEY == OTHER_FICTIONAL_KEY


@pytest.mark.asyncio
async def test_the_pong_is_the_one_the_rest_of_the_runtime_sends(balance_asks):
    fake = _FakeProvider()

    await probe_brain("openai", api_key=FICTIONAL_KEY, _provider_factory=_factory(fake))

    assert fake.calls[0]["messages"] == [{"role": "user", "content": "Reply with exactly: PONG"}]
    assert fake.calls[0]["max_tokens"] == 200


@pytest.mark.asyncio
async def test_a_provider_with_no_balance_api_says_nothing_rather_than_guessing(balance_asks):
    """OpenAI and a local server do not report a balance. `None` is the page's
    cue to say "not reported by this provider" — never a number we invented."""
    result = await probe_brain(
        "openai", api_key=FICTIONAL_KEY, _provider_factory=_factory(_FakeProvider())
    )

    assert result.ok is True
    assert result.balance is None
    assert balance_asks == []  # not asked at all: there is nothing there to ask


@pytest.mark.asyncio
async def test_a_balance_that_cannot_be_read_is_silence_not_a_failure(monkeypatch):
    async def _unreadable(name, *, api_key="", timeout=5.0):
        return None

    monkeypatch.setattr(providers_module, "fetch_balance", _unreadable)

    result = await probe_brain(
        "deepseek", api_key=FICTIONAL_KEY, _provider_factory=_factory(_FakeProvider())
    )

    assert result.ok is True
    assert result.balance is None


@pytest.mark.asyncio
async def test_a_provider_the_door_cannot_build_is_refused_without_a_call(balance_asks):
    result = await probe_brain("ollama", api_key=FICTIONAL_KEY)

    assert result.ok is False
    assert "ollama" in (result.error or "")


# ── the real factory: the classes she will run on, built with what was pasted ──


def test_the_real_factory_builds_deepseek_with_the_pasted_key(monkeypatch):
    from core.config import config

    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", OTHER_FICTIONAL_KEY, raising=False)

    provider = providers_module.build_probe_provider(
        "deepseek", api_key=FICTIONAL_KEY, model="deepseek-v4-pro"
    )

    assert type(provider).__name__ == "DeepSeekProvider"
    assert provider.api_key == FICTIONAL_KEY
    assert provider.default_model == "deepseek-v4-pro"
    assert provider.is_configured() is True


def test_the_real_factory_builds_a_local_server_with_the_pasted_address(monkeypatch):
    from core.config import config

    monkeypatch.setattr(config, "LOCAL_LLM_API_BASE", "http://192.0.2.9:11434/v1", raising=False)

    provider = providers_module.build_probe_provider(
        "local_openai", base_url="http://198.51.100.4:1234/v1", model="qwen3:14b"
    )

    assert type(provider).__name__ == "LocalOpenAIProvider"
    assert provider.base_url == "http://198.51.100.4:1234/v1"
    assert provider.default_model == "qwen3:14b"


def test_the_real_factory_builds_openai_with_the_pasted_key(monkeypatch):
    from core.config import config

    monkeypatch.setattr(config, "OPENAI_API_KEY", OTHER_FICTIONAL_KEY, raising=False)

    provider = providers_module.build_probe_provider("openai", api_key=FICTIONAL_KEY, model="gpt-4o")

    assert type(provider).__name__ == "OpenAIProvider"
    assert provider.api_key == FICTIONAL_KEY
    assert provider.default_model == "gpt-4o"


def test_the_real_factory_refuses_a_provider_that_cannot_take_a_pasted_key():
    with pytest.raises(ValueError):
        providers_module.build_probe_provider("claude")


# ── the provider's own words, not "empty response" (fix round 2) ───────────
#
# `chat_completion` never raises and returns None on every failure, so a wrong
# key, a server that is not running and a genuinely empty answer all arrive at
# the probe looking identical. On 2026-09-17 a REAL DeepSeek 401 reached the
# door as "empty response" — the provider's text was in the backend log and
# nowhere else. These four run the REAL provider path against a local stub, so
# what they pin is the sentence a stranger actually sees.


@pytest.fixture
def brain_server():
    """A stub for POST /chat/completions that answers whatever it is handed."""
    state: dict = {"status": 200, "body": {}, "sleep": 0.0, "auth": []}

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"


        def do_POST(self):  # noqa: N802
            state["auth"].append(self.headers.get("Authorization", ""))
            self.rfile.read(int(self.headers.get("Content-Length") or 0))
            if state["sleep"]:
                time.sleep(state["sleep"])
            raw = json.dumps(state["body"]).encode("utf-8")
            self.send_response(state["status"])
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, *_args):  # keep pytest's output clean
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True   # a handler still sleeping must not hold teardown
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    server.state = state  # type: ignore[attr-defined]
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()


def _base_url(server) -> str:
    host, port = server.server_address[:2]
    return f"http://{host}:{port}/v1"


def _real_provider_at(base_url: str, *, request_timeout: float | None = None):
    """The factory `probe_brain` will use: a REAL LocalOpenAIProvider pointed at
    the stub, with the retry ladder off so a test is a test and not a wait."""

    def build(name, *, api_key="", base_url_unused="", model="", **_kwargs):
        provider = providers_module.build_probe_provider(
            "local_openai", api_key=api_key, base_url=base_url, model=model or "a-model"
        )
        provider.MAX_RETRIES = 0
        if request_timeout is not None:
            provider.request_timeout_for_context = lambda _ctx: request_timeout
        return provider

    def factory(name, *, api_key="", base_url="", model=""):
        return build(name, api_key=api_key, model=model)

    return factory


@pytest.mark.asyncio
async def test_a_wrong_key_carries_the_providers_own_401(balance_asks, brain_server):
    brain_server.state["status"] = 401
    brain_server.state["body"] = {
        "error": {"message": "Authentication Fails, Your api key is invalid", "code": "invalid_request_error"}
    }

    result = await probe_brain(
        "local_openai",
        api_key=FICTIONAL_KEY,
        _provider_factory=_real_provider_at(_base_url(brain_server)),
    )

    assert result.ok is False
    assert result.error == "401 Unauthorized: Authentication Fails, Your api key is invalid"


@pytest.mark.asyncio
async def test_a_server_that_is_not_running_says_so(balance_asks):
    """A local model server nobody started — the commonest local failure, and
    the one "empty response" told a stranger nothing about."""
    closed = ThreadingHTTPServer(("127.0.0.1", 0), BaseHTTPRequestHandler)
    address = f"http://127.0.0.1:{closed.server_address[1]}/v1"
    closed.server_close()   # the port is now nobody's

    result = await probe_brain(
        "local_openai", base_url=address, _provider_factory=_real_provider_at(address)
    )

    assert result.ok is False
    assert result.error.startswith(f"could not connect to {address} — is the server running?")


@pytest.mark.asyncio
async def test_a_server_that_never_answers_says_so(balance_asks, brain_server):
    brain_server.state["sleep"] = 1.5

    result = await probe_brain(
        "local_openai",
        api_key=FICTIONAL_KEY,
        _provider_factory=_real_provider_at(_base_url(brain_server), request_timeout=0.15),
    )

    assert result.ok is False
    assert result.error == f"no answer from {_base_url(brain_server)} within 0.15s"


@pytest.mark.asyncio
async def test_a_genuinely_empty_answer_keeps_its_own_words(balance_asks, brain_server):
    """A 200 with nothing in it is not an error anybody can act on — and it is
    the only case that still reads "empty response"."""
    brain_server.state["status"] = 200
    brain_server.state["body"] = {"choices": [{"message": {"content": "   "}}]}

    result = await probe_brain(
        "local_openai",
        api_key=FICTIONAL_KEY,
        _provider_factory=_real_provider_at(_base_url(brain_server)),
    )

    assert result.ok is False
    assert result.error == "empty response"


@pytest.mark.asyncio
async def test_a_provider_that_quotes_the_key_back_does_not_get_to_keep_it(
    balance_asks, brain_server
):
    """A 401 body that echoes what it was sent, whole or in part."""
    brain_server.state["status"] = 401
    brain_server.state["body"] = {
        "error": {"message": f"the key {FICTIONAL_KEY} was rejected; {FICTIONAL_KEY[:12]} is unknown"}
    }

    result = await probe_brain(
        "local_openai",
        api_key=FICTIONAL_KEY,
        _provider_factory=_real_provider_at(_base_url(brain_server)),
    )

    assert result.ok is False
    assert FICTIONAL_KEY not in result.error
    assert FICTIONAL_KEY[:12] not in result.error
    assert "[REDACTED]" in result.error
    assert "401 Unauthorized" in result.error


@pytest.mark.asyncio
async def test_her_live_path_still_never_raises_and_still_returns_empty(brain_server):
    """The rule the whole design rests on: `chat_completion` returns None on a
    failure and does not raise, for every existing caller. Recording the reason
    is a side slot, not a new control path."""
    provider = providers_module.build_probe_provider(
        "local_openai", api_key=FICTIONAL_KEY, base_url=_base_url(brain_server), model="a-model"
    )
    provider.MAX_RETRIES = 0
    brain_server.state["status"] = 401
    brain_server.state["body"] = {"error": {"message": "nope"}}
    try:
        reply = await provider.chat_completion(
            messages=[{"role": "user", "content": "hi"}], max_tokens=16, log_context="a_live_lane"
        )
        assert reply is None
        assert provider.last_failure.status == 401

        # And a success clears it, so a later empty answer is not blamed on it.
        brain_server.state["status"] = 200
        brain_server.state["body"] = {"choices": [{"message": {"content": "PONG"}}]}
        assert await provider.chat_completion(
            messages=[{"role": "user", "content": "hi"}], max_tokens=16, log_context="a_live_lane"
        ) == "PONG"
        assert provider.last_failure is None
    finally:
        await provider.close()


# ── the scrub takes EVERY prefix, and the CLI's words too (fix round 3) ────


def test_the_scrub_takes_every_truncation_the_body_quotes():
    """A provider body can quote the key at more than one truncation length —
    sixteen characters in one sentence, eleven in another. Stopping at the
    first size found left the shorter one on the page."""
    text = (
        f"key {FICTIONAL_KEY} was rejected; "
        f"{FICTIONAL_KEY[:16]} is unknown; "
        f"and {FICTIONAL_KEY[:11]} has no account"
    )

    scrubbed = providers_module.scrub_credential(text, FICTIONAL_KEY)

    assert FICTIONAL_KEY not in scrubbed
    assert FICTIONAL_KEY[:16] not in scrubbed
    assert FICTIONAL_KEY[:11] not in scrubbed
    assert scrubbed.count("[REDACTED]") == 3
    # Everything that was not the key is still there.
    assert "was rejected" in scrubbed and "has no account" in scrubbed


def test_the_scrub_leaves_a_prefix_under_the_floor_alone():
    """Eight characters, the same floor core.secrets_redaction keeps: below it
    this could shred ordinary text."""
    text = f"the key starts {FICTIONAL_KEY[:7]} and that is all we will say"

    assert providers_module.scrub_credential(text, FICTIONAL_KEY) == text


def test_a_key_shorter_than_the_floor_is_never_scrubbed():
    assert providers_module.scrub_credential("x marks the spot", "x") == "x marks the spot"


def test_the_configured_credentials_include_every_key_in_a_rotation():
    """A key env var may be a comma-separated rotation; all of them are the
    account's, so all of them come out of the words."""
    provider = providers_module.build_probe_provider(
        "openai", api_key=f"{FICTIONAL_KEY},{OTHER_FICTIONAL_KEY}", model="gpt-4o"
    )

    assert providers_module.configured_credentials(provider) == sorted(
        {FICTIONAL_KEY, OTHER_FICTIONAL_KEY}, key=len, reverse=True
    )


class _FailedProvider:
    """A provider that has already recorded a failure quoting its own key."""

    def __init__(self, sentence: str, *, api_key: str):
        from ai.llm_openai_compatible import ProviderFailure
        from ai.credential_pool import CredentialPool

        self.credential_pool = CredentialPool.from_env_value(api_key)
        self.api_key = self.credential_pool.first_key()
        self.last_failure = ProviderFailure(
            kind="http", status=401, message=sentence, sentence=sentence
        )

    async def chat_completion(self, **_kwargs):
        return ""


@pytest.mark.asyncio
async def test_the_clis_pong_words_are_scrubbed_with_the_configured_key():
    """`palli_config/skins/menu.py` renders `result.error` straight onto a
    Textual widget, which the log-based redaction filter never sees. Before
    2026-09-17 that branch could only ever say "empty response"."""
    sentence = f"401 Unauthorized: key {FICTIONAL_KEY} rejected ({FICTIONAL_KEY[:12]})"
    provider = _FailedProvider(sentence, api_key=FICTIONAL_KEY)

    result = await validate_provider("openai", _provider_factory=lambda _: provider)

    assert result.ok is False
    assert FICTIONAL_KEY not in (result.error or "")
    assert FICTIONAL_KEY[:12] not in (result.error or "")
    assert "401 Unauthorized" in (result.error or "")


@pytest.mark.asyncio
async def test_a_raising_provider_has_its_key_scrubbed_out_of_the_exception_too():
    class _Raises(_FailedProvider):
        async def chat_completion(self, **_kwargs):
            raise RuntimeError(f"auth failed for {FICTIONAL_KEY}")

    provider = _Raises("unused", api_key=FICTIONAL_KEY)

    result = await validate_provider("openai", _provider_factory=lambda _: provider)

    assert result.ok is False
    assert FICTIONAL_KEY not in (result.error or "")
    assert "auth failed" in (result.error or "")


# ── sibling keys cannot expose each other (fix round 4) ─────────────────────
#
# A rotation's keys usually share a prefix (same account, same `sk-…` shape).
# Scrubbing them one at a time let the FIRST key's prefix pass eat the start
# of the SECOND key's quote, so the second key's own whole-string match never
# fired and its unique suffix stayed on the page.

A_THIRD_FICTIONAL_KEY = "sk-test-a-third-key-here"      # shares "sk-test-" with the other two
A_LONGER_FICTIONAL_KEY = FICTIONAL_KEY + "-longer"       # FICTIONAL_KEY is a prefix of it


def _no_part_of(key: str, text: str) -> bool:
    """True when no run of the key at or above the 8-character floor survives
    anywhere in the text. Windows, not just prefixes: the leak this section
    pins is a SUFFIX ("some-other-key") left behind after a sibling's prefix
    pass ate the front of the quote."""
    return all(
        key[start:start + size] not in text
        for size in range(len(key), 7, -1)
        for start in range(0, len(key) - size + 1)
    )


def test_a_sibling_key_sharing_a_prefix_is_scrubbed_whole():
    """The re-review's case, verbatim: two keys sharing an 8-character prefix,
    the body quoting the second. Used to come back as
    "key [REDACTED]some-other-key rejected"."""
    text = f"401 Unauthorized: key {OTHER_FICTIONAL_KEY} rejected"

    scrubbed = providers_module.scrub_credentials(text, [FICTIONAL_KEY, OTHER_FICTIONAL_KEY])

    assert scrubbed == "401 Unauthorized: key [REDACTED] rejected"
    assert _no_part_of(FICTIONAL_KEY, scrubbed)
    assert _no_part_of(OTHER_FICTIONAL_KEY, scrubbed)


def test_a_key_that_is_a_prefix_of_another_leaves_no_tail_in_either_order():
    """One key a prefix of the other, both quoted in one body: the shorter one
    scrubbed first used to leave the longer one's tail ("-longer") behind."""
    text = f"first {FICTIONAL_KEY} then {A_LONGER_FICTIONAL_KEY} done"

    for order in ([FICTIONAL_KEY, A_LONGER_FICTIONAL_KEY], [A_LONGER_FICTIONAL_KEY, FICTIONAL_KEY]):
        scrubbed = providers_module.scrub_credentials(text, order)

        assert scrubbed == "first [REDACTED] then [REDACTED] done", order
        assert _no_part_of(A_LONGER_FICTIONAL_KEY, scrubbed), order


def test_three_sibling_keys_in_three_places_leave_three_marks_and_the_words():
    keys = [FICTIONAL_KEY, OTHER_FICTIONAL_KEY, A_THIRD_FICTIONAL_KEY]
    text = (
        f"first {FICTIONAL_KEY} rejected; "
        f"second {OTHER_FICTIONAL_KEY} unknown; "
        f"third {A_THIRD_FICTIONAL_KEY} expired"
    )

    scrubbed = providers_module.scrub_credentials(text, keys)

    assert scrubbed == "first [REDACTED] rejected; second [REDACTED] unknown; third [REDACTED] expired"
    assert scrubbed.count("[REDACTED]") == 3
    for key in keys:
        assert _no_part_of(key, scrubbed), key


@pytest.mark.asyncio
async def test_the_clis_pong_words_are_scrubbed_of_the_second_key_in_a_rotation():
    """`validate_provider` scrubs with every configured key. A rotation whose
    recorded failure quotes the SECOND key must not hand its tail to the CLI."""
    sentence = f"401 Unauthorized: key {OTHER_FICTIONAL_KEY} rejected"
    provider = _FailedProvider(sentence, api_key=f"{FICTIONAL_KEY},{OTHER_FICTIONAL_KEY}")

    result = await validate_provider("openai", _provider_factory=lambda _: provider)

    assert result.ok is False
    assert result.error == "401 Unauthorized: key [REDACTED] rejected"
    assert _no_part_of(FICTIONAL_KEY, result.error)
    assert _no_part_of(OTHER_FICTIONAL_KEY, result.error)


# ── every ≥8-character run of any key, wherever it sits (fix round 5) ───────
#
# Rounds 2–4 were built on PREFIXES: substrings anchored at the start of each
# key. Real provider text quotes keys TRUNCATED, and pooled siblings share
# their opening characters, so one key's whole-or-prefix match could eat the
# FRONT of another key's truncated quote and strand its TAIL — a substring of
# a credential that is never a prefix, which no prefix pass can find. The
# guarantee `_no_part_of` already states is the definition now: no run of
# eight or more contiguous characters of ANY configured credential survives,
# regardless of order, overlap, or which key it came from.

A_TAILED_FICTIONAL_KEY = FICTIONAL_KEY + "-a-much-longer-tail-xyz-123"   # FICTIONAL_KEY is its prefix


def test_a_truncated_quote_of_a_longer_sibling_leaves_no_tail():
    """The re-review's first construction, verbatim: pool a key and a longer
    key that begins with it; the body quotes the longer one cut at 48
    characters. The shorter key's whole-string match ate the front and left
    "[REDACTED]-a-much-longer-tail-xyz-12" — 26 real characters — behind."""
    text = "401 error: key " + A_TAILED_FICTIONAL_KEY[:48] + " was rejected by upstream"

    scrubbed = providers_module.scrub_credentials(text, [A_TAILED_FICTIONAL_KEY, FICTIONAL_KEY])

    assert scrubbed == "401 error: key [REDACTED] was rejected by upstream"
    assert _no_part_of(A_TAILED_FICTIONAL_KEY, scrubbed)
    assert _no_part_of(FICTIONAL_KEY, scrubbed)


def test_two_truncated_quotes_back_to_back_leave_nothing_between_them():
    """The re-review's second construction, verbatim: two sibling keys quoted
    truncated with no gap between them. The first quote's prefix match ran
    one character into the second and stranded "k-test-xvhjoaue"."""
    k1, k2 = "sk-test-xvhjoa5smqq3ct", "sk-test-xvhjoaue4"
    text = "pre sk-test-xvhjoa5sk-test-xvhjoaue post"

    scrubbed = providers_module.scrub_credentials(text, [k1, k2])

    assert scrubbed == "pre [REDACTED] post"
    assert _no_part_of(k1, scrubbed)
    assert _no_part_of(k2, scrubbed)


def test_a_fragment_from_the_middle_of_a_key_is_redacted_on_its_own():
    """Ten characters cut from the MIDDLE of a key — no prefix anywhere in the
    body. A prefix pass could not see it at all."""
    fragment = A_TAILED_FICTIONAL_KEY[20:30]
    assert not A_TAILED_FICTIONAL_KEY.startswith(fragment)
    text = f"upstream saw {fragment} and refused"

    scrubbed = providers_module.scrub_credential(text, A_TAILED_FICTIONAL_KEY)

    assert scrubbed == "upstream saw [REDACTED] and refused"
    assert _no_part_of(A_TAILED_FICTIONAL_KEY, scrubbed)


def test_a_seven_character_fragment_from_the_middle_is_left_alone():
    """The floor from the middle of the key, not just its start."""
    fragment = A_TAILED_FICTIONAL_KEY[20:27]
    text = f"upstream saw {fragment} and refused"

    assert providers_module.scrub_credential(text, A_TAILED_FICTIONAL_KEY) == text


def test_a_key_inside_another_quoted_whole_leaves_one_mark_in_either_order():
    """One key a SUBSTRING of the other — not a prefix — and the longer one
    quoted whole: one `[REDACTED]`, never two, whichever arrives first."""
    inner = "inner-real-key-part"
    outer = f"sk-test-outer-{inner}-end-42"
    text = f"key {outer} rejected"

    for order in ([inner, outer], [outer, inner]):
        scrubbed = providers_module.scrub_credentials(text, order)

        assert scrubbed == "key [REDACTED] rejected", order
        assert scrubbed.count("[REDACTED]") == 1, order
        assert _no_part_of(outer, scrubbed), order


def test_a_body_with_no_credential_material_comes_back_as_it_was():
    """Nothing to redact means nothing changes — and an ordinary word that
    merely shares seven characters with a key ("upstream" against a key
    holding "upstrea7") survives, because the floor is eight."""
    key = "sk-test-key-with-upstrea7-in-it"  # gitleaks:allow
    text = "401 Unauthorized: the request was rejected by upstream, not by us"

    scrubbed = providers_module.scrub_credentials(text, [key, FICTIONAL_KEY])

    assert scrubbed == text
    assert "upstream" in scrubbed


def test_a_long_key_against_a_large_body_finishes_promptly():
    """A 200-character key against a 20 KB body that quotes pieces of it
    throughout — the scan is linear in the body, not a replace loop per
    prefix. A generous wall-clock bound so CI never flakes on it."""
    rng = random.Random(20260917)   # seeded: the same fictional key every run
    key = "sk-test-" + "".join(rng.choices("abcdefghijklmnopqrstuvwxyz0123456789", k=192))
    assert len(key) == 200
    piece = "quoted " + key[37:141] + " and again " + key[150:170] + " then prose. "
    text = (piece * (20_000 // len(piece) + 1))[:20_000]
    assert len(text) == 20_000

    started = time.monotonic()
    scrubbed = providers_module.scrub_credential(text, key)
    elapsed = time.monotonic() - started

    assert elapsed < 2.0, elapsed
    assert _no_part_of(key, scrubbed)
    assert "then prose." in scrubbed and "[REDACTED]" in scrubbed
