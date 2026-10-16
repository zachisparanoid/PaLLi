"""The setup API — probe, write, hot swap, status (first door, task 2, 2026-09-17).

Spec: docs/superpowers/specs/2026-09-17-first-door-design.md §2, §3, §4.

Task 1 made a missing brain key SETUP MODE instead of exit 2: she boots, both
chat doors stay shut, every thinking lane is quiet, and the punch list names
the dashboard address. This is the two endpoints that address leads to.

`GET /api/setup/status` is what the page reads to decide what to show. It
changes nothing, so it takes the local-owner rule and not the CSRF pair — the
page has to be able to read it before `GET /auth/me` has minted the cookie.

`POST /api/setup/brain` is the one that acts, so it takes both, in the chat
route's order (auth first, so an anonymous caller gets a clean 401 rather than
a CSRF 403). It does five things, in this order and no other:

  1. asks the capability matrix whether her voice can run on this provider at
     all, and refuses out loud if not — a PONG through Anthropic would PASS,
     the key would be saved, and her first reply would break, because dispatch
     never selects the native Anthropic path;
  2. tests the pasted credential live, through the very class she will run on;
  3. writes `.env.local` — and only now, because nothing is written until the
     test passes (spec §1 step 2);
  4. puts the same values on the running `config` and swaps the provider
     registry in place, so her brain comes up without a restart;
  5. answers with what happened, including the two things that still need one:
     `restart_required` when the swap itself failed, and `doors_need_restart`
     for a Discord or Telegram door this boot shut for lack of a brain.

Two rules the whole file is built around. The pasted key is never written onto
the live `config` to make the probe work — a failed probe must leave the
process exactly as it found it. And the key never reaches a log record or a
response: the redaction that covers her own credentials by construction cannot
cover this one yet, so the request registers it with the filter for its own
duration (`core.secrets_redaction.covering`) and hands over to that
construction rule with `refresh()` the moment the key lands on the config.
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any, Dict, List, Literal, Mapping, Optional, get_args

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from pydantic import BaseModel as PydanticBaseModel

from api.web_auth import _require_user
from core.config import config
from core.config_schema import BRAIN_KEY_BY_PROVIDER
from core.llm_capabilities import BRAIN_LANE, LANES, PROVIDERS, Auth, check_route
from core.local_mode import local_mode_enabled
from core.secrets_redaction import covering, redact, refresh as refresh_redaction
from core.setup_mode import dashboard_address, doors_shut_for_lack_of_a_brain
from core.web_security import require_csrf
from palli_config.brain import brain_env_updates
from palli_config.envfile import atomic_write_text, set_env_keys
from palli_config.providers import ProbeResult, probe_brain

logger = logging.getLogger(__name__)


class _NoEchoRoute(APIRoute):
    """A malformed request to this router is never quoted back to its sender.

    FastAPI's validation error carries `"input"`, and for some error shapes that
    input is the WHOLE request body — so a body that failed to parse could put
    the pasted brain key in the 422 it got back (fix round 1, minor 2). Every
    other rule in this file says the key never appears in a response, and a rule
    with an exception is not a rule.

    Belt and braces with the request model below, which has no required field
    and therefore cannot raise the `missing` error that quotes the whole body:
    this drops `input` (and `ctx`, which can carry a value too) from whatever
    else pydantic might raise, without this module having to know which shapes
    those are.
    """

    def get_route_handler(self):
        handle = super().get_route_handler()

        async def no_echo(request: Request) -> Response:
            try:
                return await handle(request)
            except RequestValidationError as exc:
                raise HTTPException(
                    status_code=422,
                    detail=[
                        {k: v for k, v in dict(error).items() if k not in ("input", "ctx")}
                        for error in exc.errors()
                    ],
                ) from None

        return no_echo


router = APIRouter(prefix="/api/setup", tags=["setup"], route_class=_NoEchoRoute)

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# The brains the door offers. Anthropic is on the list so the page can show the
# matrix's honest refusal rather than pretending the option does not exist
# (ruling P1); anything else is refused by hand, with a plain sentence.
DoorProvider = Literal["deepseek", "openai", "anthropic", "local_openai"]
DOOR_PROVIDERS: tuple[str, ...] = get_args(DoorProvider)

# Probe → write → swap runs alone. Two POSTs that interleaved could write one
# provider's lines and swap in another's registry.
_brain_lock = asyncio.Lock()

# Only for `_restore_config`: an attribute Config does not declare at all must
# be put back as absent, not as None.
_ABSENT = object()


def _env_local_path() -> Path:
    """The one place the API decides where `.env.local` is.

    One function so a test can point it somewhere harmless — in the build
    worktree the real path is a SYMLINK to the owner's live file.
    """
    return PROJECT_ROOT / ".env.local"


class BrainRequest(PydanticBaseModel):
    """What the door's first step sends.

    Not one required field, and `provider` is a plain `str` rather than the
    Literal (fix round 1, minor 2): a `missing` or `literal_error` validation
    error quotes the offending input back, and for `missing` that input is the
    whole body — the pasted key with it. An unknown or blank provider is
    refused by hand instead, with a sentence that echoes nothing.

    Which of the other three is needed depends on the provider, so all three
    stay optional and the handler says which one is missing.
    """

    provider: str = ""
    api_key: Optional[str] = None
    base_url: Optional[str] = None
    model: Optional[str] = None


# ---------------------------------------------------------------------------
# GET /api/setup/status
# ---------------------------------------------------------------------------


def _door_state(verdict_name: str, flag_name: str, token_name: str) -> str:
    """"open" | "closed" | "unset" for one chat door.

    "open" is BOOT's verdict for this run, not a fourth place that re-derives
    it, ANDed with the raw token — the same belt-and-braces rule the health
    readout and the doctor keep, because Config declares the flags at class
    level and an object that never went through validate() must not report a
    door open on a blank token. "closed" therefore covers every way a door with
    a token can be shut: the flag off, the library missing, or setup mode.
    """
    token = str(getattr(config, token_name, "") or "").strip()
    if not token:
        return "unset"
    is_open = bool(
        getattr(config, verdict_name, getattr(config, flag_name, False) and token)
    ) and bool(token)
    return "open" if is_open else "closed"


def _mobile_door_state() -> str:
    """The phone app's door (2026-10-13). It has no token, so the three
    states read: "unset" — switched off (the card shows the lines to add);
    "closed" — on, but shut this boot (setup mode); "open" — on and open
    (the card offers to pair a phone)."""
    if not bool(getattr(config, "ENABLE_MOBILE_DOOR", False)):
        return "unset"
    return "open" if bool(getattr(config, "effective_mobile_enabled", False)) else "closed"


def _doors_need_restart(*, a_brain_is_coming: bool = False) -> bool:
    """True when a brain is here now but a door is still waiting on a restart.

    The bot tasks are created once, at launcher start, from the channel map
    boot built — so a door this boot shut for lack of a brain cannot come up
    in place the way the provider registry can. Saying so is the honest
    answer; pretending otherwise would leave a stranger watching for a bot
    that never arrives (spec §2).

    `a_brain_is_coming` is the save-but-no-swap case (fix round 1, minor 6). The
    key IS in `.env.local`, so the restart the answer is already asking for is
    the same restart those doors are waiting on — but `brain_ready` has been
    rolled back, so reading it alone would answer `restart_required: true,
    doors_need_restart: false` and make the two fields describe different
    worlds.
    """
    has_brain = bool(getattr(config, "brain_ready", True)) or a_brain_is_coming
    return has_brain and bool(doors_shut_for_lack_of_a_brain())


def _her_voice_model() -> str:
    """The model her voice would actually run, by the expression dispatch uses.

    Read from the same three places, in the same order, as
    core/agent/dispatch.py — LLM_RESPONSE_MODEL, then the active provider's own
    default, then OPENAI_MODEL — so the page cannot show a model she is not on.

    Guarded (fix round 1, minor 4): `core.agent.dispatch` is a heavy import, and
    `GET /status` is the one endpoint that must answer on a half-broken fresh
    clone. An ImportError anywhere in that chain would turn the door into a 500
    on exactly the install that needs it, so a failure falls back to the two
    values that need nothing but `config`.
    """
    try:
        from ai.llm_client import llm
        from core.agent.dispatch import _default_runner_model

        active = str(getattr(llm.provider, "provider_name", "") or "").lower()
        return (
            str(getattr(config, "LLM_RESPONSE_MODEL", "") or "")
            or _default_runner_model(active)
            or str(getattr(config, "OPENAI_MODEL", "gpt-4o"))
        )
    except Exception as exc:
        logger.warning(
            "the model her voice would run could not be resolved from the runtime: %s", exc
        )
        return str(getattr(config, "LLM_RESPONSE_MODEL", "") or "") or str(
            getattr(config, "OPENAI_MODEL", "gpt-4o")
        )


def _provider_menu() -> List[Dict[str, Any]]:
    """The picker, with the matrix's own words for both the name and any
    refusal. Nothing here is hand-written: a provider that becomes usable for
    her voice becomes selectable on the door by the same edit."""
    menu: List[Dict[str, Any]] = []
    for name in DOOR_PROVIDERS:
        capability = PROVIDERS[name]
        verdict = check_route(name, BRAIN_LANE)
        menu.append(
            {
                "name": name,
                "label": capability.plain_name,
                "available": not verdict.blocked,
                # `capability.note`, not `verdict.reason`: the verdict carries a
                # path to the plan for him on the command line, and a stranger
                # on the door has no use for a pointer into somebody else's
                # build notes (release sweep, 2026-09-18).
                "reason": f"{capability.plain_name} {capability.note}" if verdict.blocked else None,
                "needs": ["api_key"] if capability.auth is Auth.API_KEY else ["base_url", "model"],
            }
        )
    return menu


@router.get("/status")
async def setup_status(http_request: Request) -> Dict[str, Any]:
    """What the door page needs to decide what to show. Reads only.

    Before a brain exists there is no model to name, so `model` is empty rather
    than a shipped default nobody chose. It used to carry that default, which
    read as a decision already made — the page ignored it, but anything else
    reading this endpoint would have believed it (release sweep, 2026-09-18).
    """
    await _require_user(http_request)
    return {
        "brain_ready": bool(getattr(config, "brain_ready", True)),
        "provider": str(getattr(config, "LLM_PROVIDER", "") or ""),
        "model": _her_voice_model() if bool(getattr(config, "brain_ready", True)) else "",
        "local_mode": bool(local_mode_enabled()),
        "doors": {
            "discord": _door_state(
                "effective_discord_enabled", "ENABLE_DISCORD_BOT", "DISCORD_TOKEN"
            ),
            "telegram": _door_state(
                "effective_telegram_enabled", "ENABLE_TELEGRAM_BOT", "TELEGRAM_BOT_TOKEN"
            ),
            "mobile": _mobile_door_state(),
        },
        "doors_need_restart": _doors_need_restart(),
        "address": dashboard_address(getattr(config, "FRONTEND_URL", "")),
        "providers": _provider_menu(),
    }


# ---------------------------------------------------------------------------
# POST /api/setup/brain
# ---------------------------------------------------------------------------


def _env_updates(
    provider: str, *, model: str, api_key: str, base_url: str
) -> Dict[str, str]:
    """Every line this provider needs in `.env.local`, and no others.

    Her voice's three come from `palli_config.brain.brain_env_updates` — the
    same rule `palli_ctl brain` writes, so LLM_RESPONSE_MODEL is always
    rewritten and a stale one from the last provider can never survive to
    become the model she runs. The credential's env name comes from the
    schema's own map; a local server needs an address instead, and a key only
    if one was typed.
    """
    updates = brain_env_updates(provider, model)
    key_name = BRAIN_KEY_BY_PROVIDER.get(provider, "")
    if key_name:
        updates[key_name] = api_key
    if provider == "local_openai":
        updates["LOCAL_LLM_API_BASE"] = base_url
        if api_key:
            updates["LOCAL_LLM_API_KEY"] = api_key
    return updates


def _write_env_local(updates: Mapping[str, str]) -> None:
    """The shared writer: the named keys set, every other line byte-identical.

    Read as BYTES, never `Path.read_text`: `.env.local` is CRLF, and universal
    newlines would hand `set_env_keys` LF text, which would rewrite the whole
    file and put every untouched line in the owner's diff.
    """
    path = _env_local_path()
    text = path.read_bytes().decode("utf-8") if path.exists() else ""
    atomic_write_text(path, set_env_keys(text, dict(updates)))


def _snapshot_config(names: List[str]) -> Dict[str, Any]:
    return {name: getattr(config, name, _ABSENT) for name in names}


def _restore_config(before: Mapping[str, Any]) -> None:
    for name, value in before.items():
        if value is _ABSENT:
            try:
                delattr(config, name)
            except AttributeError:
                pass
        else:
            setattr(config, name, value)
    # What came off the config must stop being redacted and what went back on
    # must start again — the same announcement as below, in reverse.
    refresh_redaction()


async def _activate(provider: str, updates: Mapping[str, str]) -> bool:
    """Bring the brain up on the running process. True when she is live now.

    The order is the one that works (pinned by
    tests/core/test_setup_mode.py::test_a_brain_that_arrives_swaps_the_real_provider_back_in):
    the values onto `config`, then the verdict, then the rebuild — providers
    read their credential at construction and the rebuild reads `brain_ready`
    fresh, so either one done late would swap a keyless provider back in.

    Every env key this writes is also a `Config` attribute of the same name,
    which is what makes the in-place update possible at all. `config.validate()`
    is deliberately NOT re-run: it would flip the two door verdicts to open
    while no Discord or Telegram task exists, and the status would start lying.

    On failure nothing is left half-done (spec §3). The key stays in
    `.env.local` — that is why a restart is enough — and the process goes back
    to being honestly in setup mode, so the answer can say "saved; restart to
    use it" and mean it.
    """
    from ai.llm_client import llm

    before = _snapshot_config([*updates.keys(), "brain_ready"])
    previous_default = llm.default_provider_name
    try:
        for name, value in updates.items():
            setattr(config, name, value)
        # The key is a credential her config holds now, so the rule that
        # redacts those by construction covers it — but the log filter rebuilds
        # its literals lazily and nothing watches the config. Saying so here is
        # what makes the cover continuous: `covering()` releases it moments
        # later, and until the final wave the handover worked only because that
        # release happened to move the same cache key.
        refresh_redaction()
        config.brain_ready = True
        await llm.activate_brain(provider)
        return True
    except Exception as exc:
        logger.warning(
            "the brain was saved but the live swap failed, so a restart is needed: %s",
            redact(f"{type(exc).__name__}: {exc}"),
        )
        _restore_config(before)
        llm.default_provider_name = previous_default
        try:
            # Back to the registry a boot with no brain builds. Best effort:
            # the first failure is the one worth reporting, and this one must
            # not replace it.
            await llm.replace_provider_registry({})
        except Exception as rebuild_error:
            logger.warning(
                "setup mode's registry could not be rebuilt after the failed swap: %s",
                redact(f"{type(rebuild_error).__name__}: {rebuild_error}"),
            )
        return False


def _settings_service():
    """The live settings service, looked up late so a test can stand in for it."""
    from core.settings_boot import settings_service

    return settings_service


def _lane_overrides(provider: str, model: str) -> Dict[str, Any]:
    """Every lane this provider can actually serve, on the model just proved.

    Her voice reads `.env.local`; her nine other lanes read `data/palli.yaml`
    (`ai/llm_routing`, which consults the yaml row first). Move only her voice
    and a stranger who pastes a cloud key gets a companion that talks perfectly
    and remembers nothing — memory, reflection, appraisal and the rest still
    pointed at a local model server they never installed. That is the split
    `palli_ctl brain` exists to close, and at the door it closes itself.

    The model is the one the probe just confirmed, not a preset's guess: a
    shipped preset can name a model this account has never been given.

    A lane that needs to read images is left exactly where it is when the
    provider has no vision — trading a lane that might work for one that
    certainly cannot is not an improvement.
    """
    can_see = bool(PROVIDERS[provider].vision)
    overrides: Dict[str, Any] = {"llm.default_provider": provider}
    for lane, requirement in LANES.items():
        if requirement.needs_vision and not can_see:
            continue
        overrides[f"llm.routing.{lane}"] = {"provider": provider, "model": model}
    return overrides


def _move_the_lanes(provider: str, model: str) -> str:
    """Point her background lanes at the brain that just arrived.

    Returns what to tell the page: "moved", or "not moved: <plain reason>".
    Never raises. The key has already passed and is already saved; a settings
    file that will not take the routing must not undo that, and she can still
    talk with her voice alone.
    """
    try:
        service = _settings_service()
        proposal = service.propose(
            _lane_overrides(provider, model),
            reason="the first brain, picked at the door",
        )
        change_id = str(getattr(proposal, "change_id", "") or "")
        if not change_id:
            return "kept"
        service.apply(change_id)
    except Exception as exc:
        reason = redact(f"{type(exc).__name__}: {exc}")
        logger.warning("the brain arrived but her lanes could not follow it: %s", reason)
        return f"not moved: {reason}"
    logger.info("her background lanes now follow %s", provider)
    return "moved"


async def _probe_write_swap(
    provider: str, *, api_key: str, base_url: str, model: str
) -> Dict[str, Any]:
    # Read BEFORE anything moves: only the FIRST brain takes her lanes with it.
    # Somebody who already has one has had every chance to tune their routing,
    # and a brain change from the door must not flatten it.
    her_first_brain = not bool(getattr(config, "brain_ready", True))

    result: ProbeResult = await probe_brain(
        provider, api_key=api_key, base_url=base_url, model=model
    )
    if not result.ok:
        # The provider's own words, scrubbed: some of them quote the request,
        # and the request carries the key.
        error = redact(str(result.error or "").strip() or "the brain did not answer")
        logger.info("the brain test failed for %s: %s", provider, error)
        return {"ok": False, "error": error}

    # The model that will run. The probe reports it; a probe through a stand-in
    # that reported none falls back to the provider's own key, because writing
    # LLM_RESPONSE_MODEL= blank would ALSO blank that key and leave her voice on
    # PaLLi's built-in default (fix round 1, minor 8). With nothing anywhere to
    # fall back to, say so rather than write two empty lines.
    model_key = PROVIDERS[provider].model_env_key
    running_model = (
        str(result.model or "").strip()
        or model
        or str(getattr(config, model_key, "") or "").strip()
    )
    if not running_model:
        return {
            "ok": False,
            "error": (
                f"{PROVIDERS[provider].plain_name} answered, but no model is named for it — "
                f"type one on the door, or set {model_key} in .env.local."
            ),
        }

    updates = _env_updates(provider, model=running_model, api_key=api_key, base_url=base_url)
    try:
        _write_env_local(updates)
    except Exception as exc:
        # The probe PASSED and then the file would not take it: a read-only or
        # wrong-owned `.env.local`, or a value with a line break in it. That is
        # a sentence a stranger can act on, not a 500 (fix round 1, minor 3).
        # Nothing is on the config and nothing is swapped, so the process is
        # exactly where it was.
        reason = redact(f"{type(exc).__name__}: {exc}")
        logger.warning("the brain tested fine but .env.local could not be written: %s", reason)
        return {
            "ok": False,
            "error": f"the key tested fine but .env.local could not be written: {reason}",
        }

    swapped = await _activate(provider, updates)
    lanes = _move_the_lanes(provider, running_model) if her_first_brain else "kept"
    return {
        "ok": True,
        "provider": provider,
        "model": running_model,
        "balance": result.balance,
        # What became of her nine background lanes: "moved" (they follow the
        # brain now), "kept" (this was not the first brain), or the plain
        # reason they could not be moved. The key is saved either way.
        "lanes": lanes,
        # `a_brain_is_coming` when the swap failed: the key is on disk, so the
        # restart this answer already asks for is the one those doors want too.
        "doors_need_restart": _doors_need_restart(a_brain_is_coming=not swapped),
        "restart_required": not swapped,
    }


@router.post("/brain")
async def set_brain(payload: BrainRequest, http_request: Request) -> Dict[str, Any]:
    # The chat route's pair, in its order (api/chat.py): auth first, so an
    # anonymous caller gets a clean 401 rather than a CSRF 403. A brain swap is
    # as state-changing as a turn, and the double-submit cookie is the only
    # thing between a malicious page open on this LAN and it.
    await _require_user(http_request)
    require_csrf(http_request)

    provider = str(payload.provider or "").strip().lower()
    api_key = str(payload.api_key or "").strip()
    base_url = str(payload.base_url or "").strip()
    model = str(payload.model or "").strip()

    # Blank or unknown, refused here rather than by the request model (fix round
    # 1, minor 2). The sentence echoes NOTHING the caller sent: a field that
    # arrives holding something it should not must not come straight back out.
    if provider not in DOOR_PROVIDERS:
        raise HTTPException(
            status_code=400,
            detail=f"Name one of the brains this door offers: {', '.join(DOOR_PROVIDERS)}.",
        )

    # Ruling P1: the matrix's refusal comes FIRST, with nothing probed and
    # nothing written. A provider her voice cannot run on would pass a PONG.
    verdict = check_route(provider, BRAIN_LANE)
    if verdict.blocked:
        return {"ok": False, "error": verdict.reason}

    capability = PROVIDERS[provider]
    if capability.auth is Auth.API_KEY and not api_key:
        raise HTTPException(status_code=400, detail=f"{capability.plain_name} needs an API key.")
    if provider == "local_openai":
        if not base_url:
            raise HTTPException(
                status_code=400,
                detail="A local server needs the address it answers on, e.g. http://127.0.0.1:11434/v1.",
            )
        if not model:
            raise HTTPException(
                status_code=400, detail="A local server needs the name of the model to run."
            )

    # One at a time (probe → write → swap), and the pasted key covered by the
    # log filter for exactly as long as the call lasts.
    async with _brain_lock:
        with covering(api_key):
            return await _probe_write_swap(
                provider, api_key=api_key, base_url=base_url, model=model
            )
