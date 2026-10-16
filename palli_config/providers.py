"""Provider catalog and model lists, read from data/palli.yaml.

All seven of the runtime's providers, from core.llm_capabilities (2026-09-03).
"""
from __future__ import annotations

import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

import aiohttp
import yaml

from core.config import config
from core.llm_capabilities import PROVIDERS as _CAPABILITIES, Auth
from core.secrets_redaction import REDACTED

# 2026-09-03 (configurator R4): all seven providers the runtime has, from the matrix.
STAGE_1_PROVIDERS = tuple(_CAPABILITIES)
PROVIDERS_REQUIRING_API_KEY = frozenset(
    name for name, cap in _CAPABILITIES.items() if cap.auth is Auth.API_KEY
)


@dataclass(frozen=True)
class ModelInfo:
    id: str
    display_name: str
    supports_vision: bool = False
    supports_reasoning: bool = False


@dataclass(frozen=True)
class ProviderInfo:
    name: str
    enabled: bool
    base_url: str
    models: tuple[ModelInfo, ...]


class ProviderCatalog:
    """Read-only view of the providers section of data/palli.yaml."""

    def __init__(self, providers: dict[str, ProviderInfo]) -> None:
        self._providers = providers

    @classmethod
    def load(cls, palli_yaml_path: Path) -> "ProviderCatalog":
        with open(palli_yaml_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        raw_providers = data.get("providers", {}) or {}
        providers: dict[str, ProviderInfo] = {}
        for name in STAGE_1_PROVIDERS:
            entry = raw_providers.get(name)
            if entry is None:
                continue
            models = tuple(
                ModelInfo(
                    id=str(m.get("id", "")),
                    display_name=str(m.get("display_name", m.get("id", ""))),
                    supports_vision=bool(m.get("supports_vision", False)),
                    supports_reasoning=bool(m.get("supports_reasoning", False)),
                )
                for m in (entry.get("models") or [])
                if m.get("id")
            )
            providers[name] = ProviderInfo(
                name=name,
                enabled=bool(entry.get("enabled", True)),
                base_url=str(entry.get("base_url", "")),
                models=models,
            )
        return cls(providers)

    def provider_names(self) -> tuple[str, ...]:
        return tuple(self._providers.keys())

    def models_for(self, provider: str) -> list[ModelInfo]:
        info = self._providers.get(provider)
        return list(info.models) if info else []

    def requires_api_key(self, provider: str) -> bool:
        return provider in PROVIDERS_REQUIRING_API_KEY

    def get(self, provider: str) -> ProviderInfo | None:
        return self._providers.get(provider)


# The one question every liveness check asks, in one place: the doctor's probe
# below and the door's (`probe_brain`) send exactly this.
PONG_PROMPT = "Reply with exactly: PONG"
PONG_MAX_TOKENS = 200  # reasoning models need budget — see DeepSeek V4 lesson
_SCRUB_MIN_LEN = 8       # core.secrets_redaction's floor, kept in step

# A reply that is empty because nothing went wrong. Kept for exactly that case.
EMPTY_REPLY = "empty response"


def describe_empty_reply(provider: object) -> str:
    """Why a PONG came back with nothing, in the provider's own words.

    `chat_completion` never raises and returns None on every failure (her live
    turns depend on that), so a wrong key, a server that is not running and a
    genuinely empty answer all arrive here looking identical. On 2026-09-17
    that put "empty response" under the key field on the setup door where a
    real DeepSeek 401 belonged (first door, task 2, fix round 2).

    The provider now records the reason it already logs; this reads it. The
    isinstance check is the point: a stand-in provider in a test has an
    attribute for every name asked of it, and a mock's word is not a reason.
    """
    from ai.llm_openai_compatible import ProviderFailure

    failure = getattr(provider, "last_failure", None)
    if isinstance(failure, ProviderFailure) and failure.sentence.strip():
        return failure.sentence.strip()
    return EMPTY_REPLY


@dataclass(frozen=True)
class ValidationResult:
    ok: bool
    latency_ms: int
    error: str | None


def _default_provider_factory(provider_name: str):
    """Return a real PaLLi provider instance for live validation."""
    if provider_name == "openai":
        from ai.llm_providers import OpenAIProvider
        return OpenAIProvider()
    if provider_name == "anthropic":
        from ai.llm_providers import AnthropicProvider
        return AnthropicProvider()
    if provider_name == "deepseek":
        from ai.llm_providers import DeepSeekProvider
        return DeepSeekProvider()
    if provider_name == "ollama":
        from ai.llm_ollama import OllamaProvider
        return OllamaProvider()
    if provider_name == "codex":
        from ai.llm_codex_cli import CodexCLIProvider
        return CodexCLIProvider()
    if provider_name == "local_openai":
        from ai.llm_providers import LocalOpenAIProvider
        return LocalOpenAIProvider()
    if provider_name == "claude":
        from ai.llm_claude_cli import ClaudeCLIProvider
        return ClaudeCLIProvider()
    raise ValueError(f"unknown provider: {provider_name!r}")


def _scrubbed(text: str, provider: object) -> str:
    """Provider text with that provider's own configured credentials removed.

    `scrub_credentials` and `configured_credentials` live with the door's probe,
    below — one scrubber, both liveness checks."""
    return scrub_credentials(text, configured_credentials(provider))


async def validate_provider(
    provider_name: str,
    *,
    _provider_factory: Callable[[str], object] = _default_provider_factory,
) -> ValidationResult:
    """Ping a provider with a tiny PONG request. Returns ValidationResult."""
    try:
        provider = _provider_factory(provider_name)
    except Exception as exc:
        return ValidationResult(ok=False, latency_ms=0, error=f"factory: {exc}")

    started = time.monotonic()
    try:
        response = await provider.chat_completion(
            messages=[{"role": "user", "content": PONG_PROMPT}],
            max_tokens=PONG_MAX_TOKENS,
            log_context="palli_configure_validate",
        )
    except Exception as exc:
        elapsed_ms = int((time.monotonic() - started) * 1000)
        return ValidationResult(
            ok=False, latency_ms=elapsed_ms, error=_scrubbed(str(exc), provider)
        )

    elapsed_ms = int((time.monotonic() - started) * 1000)
    if not response or not response.strip():
        # Same path, same words (fix round 2): `palli_ctl doctor` used to report
        # a revoked key as "The brain did not answer: empty response" too.
        #
        # Scrubbed with the CONFIGURED credential (fix round 3). This branch
        # could only say "empty response" before round 2; now it carries the
        # provider's own sentence, and `palli_config/skins/menu.py` renders that
        # straight onto a Textual widget, where the log-based redaction filter
        # never sees it.
        return ValidationResult(
            ok=False, latency_ms=elapsed_ms,
            error=_scrubbed(describe_empty_reply(provider), provider),
        )
    return ValidationResult(ok=True, latency_ms=elapsed_ms, error=None)


@dataclass(frozen=True)
class BalanceInfo:
    provider: str
    currency: str
    total_balance: float
    available: bool


# The one provider that will tell us what is left. Everything else answers None
# and the caller says nothing (week 1 task 1b).
_BALANCE_ENDPOINT: dict[str, str] = {
    "deepseek": "https://api.deepseek.com/user/balance",
}
_BALANCE_KEY_ATTR: dict[str, str] = {"deepseek": "DEEPSEEK_API_KEY"}


def has_balance_api(provider_name: str) -> bool:
    """True when this provider will tell us what is left on the account.

    The caller needs the difference between "there is nothing to read here" and
    "the read failed": the first is silence, the second is worth saying.
    """
    return provider_name in _BALANCE_ENDPOINT


async def fetch_balance(
    provider_name: str, *, api_key: str = "", timeout: float = 5.0
) -> BalanceInfo | None:
    """What is left on the account, or None.

    None means "no answer worth printing": the provider has no balance API, the
    key is missing, the call failed, or the payload was a shape we do not know.
    The balance ran to zero twice in one week without a word — this is the
    readout that ends that — but it is a courtesy, never a gate, so it never
    raises and never blocks a boot.

    `api_key` reads the account of a key that is not the config's (first door,
    task 2, 2026-09-17): the door shows a stranger the balance behind the key
    they just pasted, and nothing is saved until the probe passes. Omitted —
    every existing caller — the configured key is read exactly as before.
    """
    url = _BALANCE_ENDPOINT.get(provider_name)
    if not url:
        return None
    key_attr = _BALANCE_KEY_ATTR.get(provider_name, "")
    # The key may be a comma-separated rotation; any one of them can read the
    # account, so take the first.
    api_key = str(api_key or getattr(config, key_attr, "") or "").split(",")[0].strip()
    if not api_key:
        return None

    try:
        session_timeout = aiohttp.ClientTimeout(total=timeout)
        async with aiohttp.ClientSession(timeout=session_timeout) as session:
            headers = {"Authorization": f"Bearer {api_key}"}
            async with session.get(url, headers=headers) as resp:
                resp.raise_for_status()
                payload = await resp.json(content_type=None)
    except Exception:
        return None

    infos = list((payload or {}).get("balance_infos") or [])
    chosen = None
    for entry in infos:
        if str((entry or {}).get("currency") or "").upper() == "USD":
            chosen = entry
            break
    if chosen is None and infos:
        chosen = infos[0]
    if not chosen:
        return None
    try:
        total = float(chosen.get("total_balance"))
    except (TypeError, ValueError):
        return None
    return BalanceInfo(
        provider=provider_name,
        currency=str(chosen.get("currency") or "").upper() or "?",
        total_balance=total,
        # Default True: a provider that simply does not send the field is
        # not "marked unavailable", and a bare .get() turned that silence
        # into a warning telling the reader their working account is shut
        # off. Only an explicit false says so.
        available=bool((payload or {}).get("is_available", True)),
    )


# ---------------------------------------------------------------------------
# The live test on the door (first door, task 2, 2026-09-17)
#
# `validate_provider` above builds its provider from `config` — right for the
# doctor and the punch list, which ask about the brain she is already running
# on. The door asks a different question: does THIS key, typed a second ago
# and saved nowhere, work? Nothing may be written until the answer is yes, so
# the credential is passed explicitly, all the way down to the balance read.
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ProbeResult:
    ok: bool
    model: str            # the model that would actually run
    balance: str | None   # "1.96 USD", or None where the provider reports none
    error: str | None


def scrub_credential(text: str, api_key: str) -> str:
    """The pasted credential out of the provider's own words — the one-key case
    of `scrub_credentials`, below, which holds the rules."""
    return scrub_credentials(text, (api_key,))


def configured_credentials(provider: object) -> list[str]:
    """Every credential the provider in hand read from `config` when it was built.

    The pool first, because a key env var may be a comma-separated rotation and
    every one of them is the account's; then the single `api_key` surface older
    code reads. Longest first, so a key that contains another is taken whole.

    Guarded the whole way down: a stand-in provider in a test has an attribute
    for every name asked of it, and nothing on the path to a courtesy may raise.
    """
    keys: list[str] = []
    entries = getattr(getattr(provider, "credential_pool", None), "entries", None)
    if isinstance(entries, list):
        for entry in entries:
            value = getattr(entry, "key", "")
            if isinstance(value, str) and value.strip():
                keys.append(value.strip())
    single = getattr(provider, "api_key", "")
    if isinstance(single, str) and single.strip():
        keys.append(single.strip())
    return sorted(set(keys), key=len, reverse=True)


def scrub_credentials(text: str, api_keys: "Iterable[str]") -> str:
    """Every one of several credentials out of the provider's own words.

    The guarantee: any run of eight or more contiguous characters of the text
    that is a substring of ANY of the credentials is replaced by `[REDACTED]`
    — wherever it sits in the key, whichever key it came from, whatever
    order the keys arrive in, and however the quotes overlap. Providers quote
    what they were sent, whole or truncated, and a 401 body that says "your
    key sk-abcd1234... is invalid" must not carry those characters back to
    the page (first door, task 2, fix round 2).

    Rounds 2–4 built this on PREFIXES (substrings anchored at the start of a
    key) and were broken by construction each time: keys are quoted
    truncated, pooled siblings share their opening characters, so one key's
    whole-or-prefix match could eat the FRONT of another key's quote and
    strand its TAIL — a substring of a credential that is never a prefix,
    which no prefix pass can find ("[REDACTED]-a-much-longer-tail-xyz-12").
    Round 5 changed the definition rather than the patch: a scan, not a
    replace loop.

    How: W is every eight-character window of every key. Walking the text,
    a position whose next eight characters are in W starts a run, and the
    run's right edge moves on while the text under it is still a substring
    of some key (the edge is bounded by the longest key). Every position is
    tried, not just the first hit — a quote of a second key can begin inside
    a quote of the first and outlast it. The right edge never moves back
    (a substring's suffix is a substring), so the runs are found in one pass;
    they are merged where they touch and the output is built from the gaps.

    Cost: O(len(text) × total credential length) in the worst case — one
    window lookup per position, and at most two substring checks per
    position amortised over the walk, because the right edge only advances.
    A body with no window in W pays the lookups only and comes back as it
    was, the same object.

    Eight characters is the floor, the same one `core.secrets_redaction`
    keeps, so this can never shred ordinary text; a key shorter than the
    floor contributes nothing. The key is not on the config while it is being
    tested, which is why the redaction filter cannot do this job.
    """
    out = str(text or "")
    keys = {
        key for key in (str(raw or "").strip() for raw in (api_keys or ()))
        if len(key) >= _SCRUB_MIN_LEN
    }
    if not keys or len(out) < _SCRUB_MIN_LEN:
        return out

    windows = {
        key[start:start + _SCRUB_MIN_LEN]
        for key in keys
        for start in range(len(key) - _SCRUB_MIN_LEN + 1)
    }
    longest = max(len(key) for key in keys)

    def in_a_key(run: str) -> bool:
        return any(run in key for key in keys)

    spans: list[list[int]] = []   # [start, end) pairs, in order, already merged
    n = len(out)
    i = 0
    j = 0   # the right edge of the run open at i; never moves back
    while i + _SCRUB_MIN_LEN <= n:
        if out[i:i + _SCRUB_MIN_LEN] not in windows:
            i += 1
            continue
        # out[i:j] is known to sit inside a key (it did for the earlier start
        # this edge was found from); only the extension is checked.
        j = max(j, i + _SCRUB_MIN_LEN)
        while j < n and j - i < longest and in_a_key(out[i:j + 1]):
            j += 1
        if spans and i <= spans[-1][1]:
            spans[-1][1] = max(spans[-1][1], j)
        else:
            spans.append([i, j])
        i += 1
    if not spans:
        return out

    pieces: list[str] = []
    cursor = 0
    for start, end in spans:
        pieces.append(out[cursor:start])
        pieces.append(REDACTED)
        cursor = end
    pieces.append(out[cursor:])
    return "".join(pieces)


def build_probe_provider(
    provider_name: str, *, api_key: str = "", base_url: str = "", model: str = ""
):
    """The class her voice will run on, built with what was typed on the door.

    The same classes the registry builds, so the test goes through the very
    code path the first reply will — never a hand-rolled HTTP call that could
    pass where she would fail. A provider that cannot be handed a pasted
    credential (a CLI login, the native API) is refused by name: the door
    offers a key field, and there is nothing to put in it.
    """
    name = str(provider_name or "").strip().lower()
    if name == "openai":
        from ai.llm_providers import OpenAIProvider

        return OpenAIProvider(api_key=api_key, default_model=model or config.OPENAI_MODEL)
    if name == "deepseek":
        from ai.llm_providers import DeepSeekProvider

        return DeepSeekProvider(api_key=api_key, default_model=model or config.DEEPSEEK_MODEL)
    if name == "local_openai":
        from ai.llm_providers import LocalOpenAIProvider

        return LocalOpenAIProvider(base_url=base_url, api_key=api_key, default_model=model)
    raise ValueError(f"{name or provider_name!r} cannot be tested with a pasted key")


async def probe_brain(
    provider_name: str,
    *,
    api_key: str = "",
    base_url: str = "",
    model: str = "",
    _provider_factory: Callable[..., object] = build_probe_provider,
) -> ProbeResult:
    """One PONG with the pasted credential, and the balance where there is one.

    Never raises, never writes: the caller decides what to do with the answer.
    The provider is closed whichever way it goes — it is a throwaway, and a
    stranger's first minute is several attempts.
    """
    try:
        provider = _provider_factory(
            provider_name, api_key=api_key, base_url=base_url, model=model
        )
    except Exception as exc:
        return ProbeResult(
            ok=False, model=str(model or ""), balance=None,
            error=scrub_credential(str(exc), api_key),
        )

    # What would actually run: what they typed, or the provider's own default.
    running_model = str(model or "").strip() or str(getattr(provider, "default_model", "") or "")
    try:
        try:
            response = await provider.chat_completion(
                messages=[{"role": "user", "content": PONG_PROMPT}],
                max_tokens=PONG_MAX_TOKENS,
                log_context="palli_setup_probe",
            )
        except Exception as exc:
            return ProbeResult(
                ok=False, model=running_model, balance=None,
                error=scrub_credential(str(exc), api_key),
            )
        if not response or not str(response).strip():
            return ProbeResult(
                ok=False, model=running_model, balance=None,
                error=scrub_credential(describe_empty_reply(provider), api_key),
            )
        return ProbeResult(
            ok=True,
            model=running_model,
            balance=await _probe_balance(provider_name, api_key),
            error=None,
        )
    finally:
        try:
            await provider.close()
        except Exception:
            pass


async def _probe_balance(provider_name: str, api_key: str) -> str | None:
    """What is left, in words, or None — which the page reads as "not reported
    by this provider". Never a guess, and never a reason to fail the probe."""
    if not has_balance_api(provider_name):
        return None
    info = await fetch_balance(provider_name, api_key=api_key)
    if info is None:
        return None
    return f"{info.total_balance:.2f} {info.currency}"


@dataclass(frozen=True)
class CodexLoginStatus:
    logged_in: bool
    message: str | None = None


def codex_login_status() -> CodexLoginStatus:
    """Check if the codex CLI is logged in. Returns CodexLoginStatus.

    Stage 1: detect-and-warn only. Does not launch codex login itself
    (the login flow is interactive and would conflict with Textual's
    terminal control).
    """
    # On Windows the codex CLI is typically a .cmd npm shim that subprocess
    # can't resolve from a bare "codex" argv without shell=True. Use
    # shutil.which to find the actual executable cross-platform.
    codex_path = shutil.which("codex")
    argv = [codex_path or "codex", "login", "status"]
    try:
        result = subprocess.run(
            argv,
            capture_output=True, text=True, timeout=5.0,
        )
    except FileNotFoundError:
        return CodexLoginStatus(logged_in=False, message="codex CLI not found in PATH")
    except subprocess.TimeoutExpired:
        return CodexLoginStatus(logged_in=False, message="codex login status timed out")

    if result.returncode == 0:
        # Real CLI writes "Logged in using ChatGPT" to stderr, not stdout —
        # fall back so we don't return an empty message on success.
        message = (result.stdout or result.stderr or "").strip()
        return CodexLoginStatus(logged_in=True, message=message)
    return CodexLoginStatus(
        logged_in=False,
        message=(result.stderr or result.stdout or "exit code 1").strip(),
    )


_MODELS_ENDPOINT = {
    "openai": "https://api.openai.com/v1/models",
    "anthropic": "https://api.anthropic.com/v1/models",
    "deepseek": "https://api.deepseek.com/models",
    "ollama": None,  # uses /api/tags instead, handled separately
    "codex": None,  # CLI; no HTTP /models endpoint
    "claude": None,  # CLI
    "local_openai": None,  # keyless local endpoint; the curated list is enough
}


async def _fetch_models_via_http(provider_name: str, api_key: str) -> list[dict]:
    url = _MODELS_ENDPOINT.get(provider_name)
    if not url:
        return []
    headers: dict[str, str] = {}
    if provider_name == "anthropic":
        headers["x-api-key"] = api_key
        headers["anthropic-version"] = "2023-06-01"
    elif api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    timeout = aiohttp.ClientTimeout(total=10.0)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(url, headers=headers) as resp:
            resp.raise_for_status()
            payload = await resp.json()
    return list(payload.get("data") or [])


async def refresh_models_from_api(
    catalog: ProviderCatalog, provider_name: str, *, api_key: str = "",
) -> list[ModelInfo]:
    """Fetch live model list from provider API; merge with curated list.

    On any error (network, auth, parse), silently falls back to the
    curated list. Doesn't mutate the catalog — returns the merged list
    for the caller to use.
    """
    curated = catalog.models_for(provider_name)
    try:
        api_models = await _fetch_models_via_http(provider_name, api_key)
    except Exception:
        return curated
    curated_ids = {m.id for m in curated}
    out = list(curated)
    for entry in api_models:
        model_id = str(entry.get("id", ""))
        if not model_id or model_id in curated_ids:
            continue
        out.append(ModelInfo(
            id=model_id,
            display_name=f"{model_id} (live)",
            supports_vision=False,
            supports_reasoning=False,
        ))
    return out
