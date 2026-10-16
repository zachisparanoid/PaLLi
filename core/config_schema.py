"""Pydantic schema mirroring the validation rules of `core.config.Config`.

Phase 8d (engineering discipline). Replaces the 237-line imperative
`Config.validate()` body with a declarative Pydantic model. The behavior
is unchanged: `Config.validate()` still returns True/False, callers
(launcher_runtime, launcher_support) continue to short-circuit startup
on a False return. What changes is *how* the rules are expressed —
Pydantic field constraints + model_validators, in one place, where each
rule lives next to the field it protects.

Why this exists:
  - Discoverability: every rule on a field is now adjacent to the
    field, not buried in a 500-line procedural method.
  - Error quality: ValidationError aggregates ALL violations in one
    pass. The old method returned on the first failure, hiding the
    rest. Operators now see the complete punch list.
  - Future load-time strictness: a follow-up can flip strict mode on
    in __init__ (raise instead of validate-on-demand) without touching
    every test. The schema is the single source of truth.

The schema is a `BaseModel` (not `BaseSettings`) — env-var loading and
default population stay in `core/config.py` exactly as they are. This
file is a *validator*, not a *loader*. That keeps the migration tight
and the blast radius small.
"""

from __future__ import annotations

import ipaddress
import logging
import re
from urllib.parse import urlparse
from typing import Any, Dict, List, Optional

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    ValidationInfo,
    model_validator,
)

from core.punch_list import PunchItem, PunchList
from core.setup_mode import dashboard_address
from core.trusted_hosts import is_allowed_name, parse_allowed_hosts
from core.trusted_networks import DEFAULT_ALLOWED_NETWORKS, parse_networks

logger = logging.getLogger(__name__)


# =============================================================================
# The punch list (stranger-proofing week 1, task 1 — launch gap §5.1.1)
#
# A validator's message says what is wrong. A stranger also needs to know WHERE
# to fix it, and whether it stops her starting at all. So:
#   HARD — she cannot start; the item is numbered and the boot exits 2.
#   SOFT — an optional feature loses its key: the feature is off for this boot,
#          the item is listed under the numbered ones, and she starts.
#
# A model built inside a raising validator never reaches the caller, so the soft
# findings travel out through the validation CONTEXT — a plain dict the caller
# owns, which survives the ValidationError. Callers that pass no context (the
# doctor's read-only inspection, the schema's own tests) collect nothing and
# behave exactly as they did before.
# =============================================================================

DEFAULT_WHERE = "set it in .env.local"
WHERE_BY_KEY: Dict[str, str] = {
    "DISCORD_TOKEN": "set it in .env.local when you want Discord",
    "TELEGRAM_BOT_TOKEN": (
        "set it in .env.local when you want Telegram; SETUP.md has the "
        "BotFather walk-through"
    ),
    "LOCAL_OWNER_DISCORD_USER_ID": (
        "set it in .env.local, or leave it and run guest-only"
    ),
    "LOCAL_OWNER_TELEGRAM_USER_ID": (
        "set it in .env.local, or leave it and run guest-only"
    ),
    "BACKEND_ALLOWED_NETWORKS": (
        "fix it or clear the setting to run open on purpose"
    ),
    "EXA_API_KEY": (
        "set it in .env.local, or turn features.web_search off in data/palli.yaml"
    ),
    "DEEPGRAM_API_KEY": (
        "set it in .env.local, or choose another provider under voice: in "
        "data/palli.yaml"
    ),
    "MOBILE_PUBLIC_URL": (
        "set it in .env.local to the address your phone reaches this backend at; "
        "SETUP.md has the phone app section"
    ),
}
_KEY_TOKEN = re.compile(r"\b[A-Z][A-Z0-9_]{2,}\b")

# Setup mode (first door, task 1, 2026-09-17). The two providers that are a
# brain only once a key is pasted, and the key each one wants. A local server
# needs an address rather than a key and codex needs a CLI login: neither can be
# fixed by pasting a key on the door, so neither is ever setup mode.
BRAIN_KEY_BY_PROVIDER: Dict[str, str] = {
    "openai": "OPENAI_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
}
# The punch item's key. Not a setting's name on purpose — the line is about her
# having no brain, and the doctor finds it by this key to say the same sentence.
BRAIN_PUNCH_KEY = "LLM"
# The two chat doors setup mode shuts: each one's runtime verdict, and the key of
# the line a door gets when it WOULD have opened (its token is set).
DOOR_PUNCH_KEY_BY_VERDICT: Dict[str, str] = {
    "effective_discord_enabled": "Discord",
    "effective_telegram_enabled": "Telegram",
    "effective_mobile_enabled": "Mobile",
}
# Setup mode's "would have opened" line, for a door with no token (the mobile
# door, 2026-10-13): "its token is set" would be untrue there.
_DOOR_WAITING_WHERE: Dict[str, str] = {
    "Mobile": "it is switched on; it opens at the first restart after you add a brain",
}


def where_for(key: str) -> str:
    return WHERE_BY_KEY.get(key, DEFAULT_WHERE)


def _sink(info: ValidationInfo) -> Optional[dict]:
    """The caller's collector, or None when nobody is collecting."""
    return info.context if isinstance(info.context, dict) else None


def _soft(info: ValidationInfo, key: str, why: str, *, where: Optional[str] = None) -> None:
    """A warning for this boot, said out loud — a feature turned off for lack of a key, or a
    setting that leaves her running in a reduced way; never a reason to refuse to start.

    `where` is for the line whose way out is not a fixed sentence per key (setup mode names
    an address and a provider's key); everything else keeps the WHERE_BY_KEY text."""
    collector = _sink(info)
    if collector is None:
        return
    collector.setdefault("warnings", []).append(
        PunchItem(key, why, where if where is not None else where_for(key))
    )


def _hard(info: ValidationInfo, key: str, why: str) -> None:
    """A blocker that does NOT raise, so the checks after it still run.

    Pydantic aborts the whole after-validator chain on the first raise, and a
    punch list that reveals one problem per boot is not a punch list. A check
    recorded here is collected first and reported with the rest by
    validate_config; with no collector it is silent, exactly like the soft
    items (see `_sink`).
    """
    collector = _sink(info)
    if collector is None:
        return
    collector.setdefault("blockers", []).append(PunchItem(key, why, where_for(key)))


def _effective(info: ValidationInfo, name: str, value: Any) -> None:
    """Boot's verdict for this run, for the runtime reader that must honor it."""
    collector = _sink(info)
    if collector is not None:
        collector.setdefault("effective", {})[name] = value


def _telegram_library_present() -> bool:
    """Is python-telegram-bot importable in THIS interpreter?

    Asked because the failure it catches is silent otherwise: a stranger who
    set the token but has not run `pip install -r requirements.txt` gets a
    door that opens on nothing, with the reason buried in a log line nobody
    reads. Named at module level so tests can pin the answer instead of
    depending on what the machine running them happens to have installed.

    Unanswerable counts as PRESENT: a weird import system must not produce
    an accusation, and the client's own lazy import says the same thing
    honestly if it turns out to be wrong.
    """
    try:
        import importlib.util

        return importlib.util.find_spec("telegram") is not None
    except Exception:  # noqa: BLE001
        return True


def _is_loopback_bind(host: Any) -> bool:
    """True when only this machine can reach the socket.

    Judged by ADDRESS, not by spelling (week 2, task 4, review fix): a set
    of strings read `0:0:0:0:0:0:0:1` and `::ffff:127.0.0.1` — the same
    loopback, written differently — as wide binds, and refused to start
    over a typo that could only ever have hurt the one machine.

    The two cases ipaddress cannot answer keep their own line: "" reads as
    unset, and core/config.py:182 turns unset into 127.0.0.1; "localhost"
    is the name of the loopback everywhere it resolves. Any other name we
    cannot judge counts as wide — the safe side of this question is the
    one that refuses to start.
    """
    value = str(host or "").strip().strip("[]").lower()
    if not value or value == "localhost":
        return True
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return False
    if address.version == 6 and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    return address.is_loopback


def _display_host(host: Any) -> str:
    """The bind, written as an address a person can actually open.

    A wildcard bind is not a destination: `0.0.0.0` and `::` say "every
    interface on this machine", which is how the socket is CONFIGURED, not
    where anybody types — and `http://::1:8000` is not a URL at all, it is a
    malformed one. The one interface a reader certainly has is loopback, so
    every one of those spellings displays as 127.0.0.1. A real IPv6 address
    keeps its own value and gets the brackets a URL authority needs. Names we
    cannot parse ("localhost", a hostname) are printed as written — they are
    already what a person would type.

    DISPLAY only. Nothing here decides anything; `_is_loopback_bind` above is
    still the one that judges whether a bind is reachable from elsewhere.
    """
    value = str(host or "").strip().strip("[]")
    if not value:
        # "" reads as unset, and core/config.py:182 turns unset into loopback.
        return "127.0.0.1"
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return value
    if address.version == 6 and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    if address.is_unspecified or (address.version == 6 and address.is_loopback):
        return "127.0.0.1"
    return f"[{address.compressed}]" if address.version == 6 else address.compressed


# =============================================================================
# Allowed-value sets — kept as module-level constants so callers (and the
# Config docstring/help command later) can introspect what's valid.
# =============================================================================

ALLOWED_LLM_PROVIDERS = frozenset({"openai", "deepseek", "local_openai", "codex"})
ALLOWED_DEPLOYMENT_MODES = frozenset({"cloud", "local", "local_single_user"})
ALLOWED_STT_PROVIDERS = frozenset({"deepgram", "faster_whisper"})
ALLOWED_TTS_PROVIDERS = frozenset({"deepgram", "lemonfox", "pyttsx3"})
ALLOWED_VOICE_ENGINES = frozenset({"classic"})
ALLOWED_VOICE_RUNTIME_MODES = frozenset({"streaming", "buffered"})
ALLOWED_VOICE_INTERACTION_MODES = frozenset({"direct_address", "passive", "copilot"})
ALLOWED_VOICE_PASSIVE_POLICIES = frozenset({"off", "experimental", "enabled"})
ALLOWED_LOCAL_VOICE_DEFAULT_MODES = frozenset({"push_to_talk", "passive", "quiet"})
ALLOWED_LOCAL_VOICE_PRESENCE_PROFILES = frozenset({"quiet", "balanced", "social"})
ALLOWED_VOICE_DEEPGRAM_ENCODINGS = frozenset({"linear16", "opus"})
ALLOWED_VOICE_LOCAL_STT_DEVICES = frozenset({"auto", "cpu", "cuda"})
ALLOWED_VOICE_LOCAL_MODEL_HOSTS = frozenset({"ollama", "lm_studio"})
ALLOWED_AGENTIC_MODES = frozenset({"off", "hybrid", "always"})
ALLOWED_AGENTIC_ROLLOUT_MODES = frozenset({"shadow", "enforce"})
ALLOWED_AGENTIC_QUEUE_MODES = frozenset({"collect", "steer", "interrupt"})
ALLOWED_AGENTIC_CANCEL_SCOPES = frozenset({"run_only", "run_and_descendants", "root_tree"})
ALLOWED_AGENTIC_REDIRECT_MODES = frozenset({"replace_goal", "interrupt", "steer"})
ALLOWED_AGENTIC_STALE_POLICIES = frozenset({"keep", "supersede", "deny"})
ALLOWED_AGENTIC_LANE_MODES = frozenset({"off", "auto"})
ALLOWED_AGENTIC_STREAMING_BREAKS = frozenset({"text_end", "message_end"})
ALLOWED_DISCORD_AGENTIC_PREVIEW_MODES = frozenset({"off", "partial", "block"})
ALLOWED_WEB_OPS_VISIT_MODES = frozenset({"pageview", "session", "path_window"})
ALLOWED_WEB_OPS_DETAIL_LEVELS = frozenset({"minimal", "masked", "full"})


def _check_in_set(field_name: str, value: str, allowed: frozenset, plugin_runtime_enabled: bool = False) -> Optional[str]:
    """Returns an error message if `value` not in `allowed`, else None.

    When plugin_runtime is enabled, unknown values are demoted to a
    warning (logged) and accepted — the plugin runtime may resolve them.
    """
    if value in allowed:
        return None
    if plugin_runtime_enabled:
        logger.warning(
            "%s=%s will be resolved through the plugin runtime",
            field_name,
            value,
        )
        return None
    return f"{field_name} must be one of: {', '.join(sorted(allowed))}"


# =============================================================================
# Schema. Mirrors the subset of Config fields that have validation rules in
# `Config.validate()`. Fields without rules (purely informational strings,
# defaults that always parse) are intentionally absent — adding them here
# without rules would just be noise.
# =============================================================================


class CoreConfigSchema(BaseModel):
    """Pydantic mirror of validated Config fields.

    Construct via `CoreConfigSchema.from_config(config_instance)` — the
    field defaults below are placeholders so each field is `Optional`
    and the schema can validate any combination the test fixtures use.
    """

    model_config = ConfigDict(
        extra="allow",  # Config has many fields without rules; ignore them.
        validate_assignment=False,  # We validate once, not on each setter.
    )

    # --- Deployment / environment ---------------------------------------
    PALLI_DEPLOYMENT_MODE: str = ""
    ENVIRONMENT: str = ""
    ENABLE_LEGACY_HOSTED_ACCESS: bool = False
    ALLOW_INSECURE_CORS: bool = False
    PLUGIN_RUNTIME_ENABLED: bool = True

    # --- LLM provider routing ------------------------------------------
    LLM_PROVIDER: str = ""
    OPENAI_API_KEY: str = ""
    DEEPSEEK_API_KEY: str = ""
    LOCAL_LLM_API_BASE: str = ""
    LOCAL_LLM_MODEL: str = ""
    CODEX_MODEL: str = ""
    CODEX_TIMEOUT_SECONDS: float = 0.0
    LLM_RESPONSE_PROVIDER: str = ""
    LLM_ANALYSIS_PROVIDER: str = ""
    LLM_MEMORY_PROVIDER: str = ""
    LLM_DECISION_PROVIDER: str = ""
    LLM_PROACTIVE_PROVIDER: str = ""
    LLM_REFLECTION_PROVIDER: str = ""
    LLM_WEB_SEARCH_PROVIDER: str = ""
    LLM_VISION_PROVIDER: str = ""

    # --- Voice features ------------------------------------------------
    ENABLE_VOICE_FEATURES: bool = False
    VOICE_ENGINE_DEFAULT: str = "classic"
    STT_PROVIDER: str = "deepgram"
    TTS_PROVIDER: str = "deepgram"
    DEEPGRAM_API_KEY: str = ""
    VOICE_LOCAL_STT_MODEL: str = "small.en"
    # Superseded 2026-08-20 (step 31): Cartesia cut entirely; lemonfox is the
    # TTS provider of record. Fields stay — removing a settings KEY is its
    # own migration; nothing validates against them any more.
    CARTESIA_API_KEY: str = ""
    CARTESIA_TTS_VOICE_ID: str = ""
    VOICE_RUNTIME_MODE: str = "buffered"
    VOICE_INTERACTION_MODE: str = "direct_address"
    VOICE_PASSIVE_POLICY: str = "experimental"
    LOCAL_VOICE_DEFAULT_MODE: str = "push_to_talk"
    LOCAL_VOICE_PRESENCE_PROFILE: str = "balanced"
    VOICE_SHORT_REPLY_MAX_CHARS: int = Field(default=220, ge=40)
    VOICE_BARGE_IN_MIN_RMS: float = Field(default=900.0, ge=0.0)
    VOICE_BARGE_IN_MIN_DURATION_MS: float = Field(default=220.0, ge=0.0)
    VOICE_BARGE_IN_MAX_GAP_MS: float = Field(default=160.0, gt=0.0)
    VOICE_DEEPGRAM_STREAM_ENCODING: str = "linear16"
    VOICE_DEEPGRAM_STREAM_SAMPLE_RATE: int = Field(default=48000, gt=0)
    VOICE_DEEPGRAM_STREAM_CHANNELS: int = Field(default=2, gt=0)
    VOICE_DEEPGRAM_STREAM_CHUNK_MS: int = Field(default=80, ge=20)
    VOICE_LOCAL_STT_DEVICE: str = "auto"
    VOICE_LOCAL_MODEL_HOST: str = "ollama"
    VOICE_OLLAMA_API_BASE: str = "http://127.0.0.1:11434/v1"
    VOICE_LM_STUDIO_API_BASE: str = "http://127.0.0.1:1234/v1"
    # Superseded 2026-08-20 (step 31): the realtime bridge is deleted;
    # fields stay — removing a settings KEY is its own migration.
    OPENAI_REALTIME_SESSION_URL: str = "https://api.openai.com/v1/realtime/sessions"
    OPENAI_REALTIME_WS_URL: str = "wss://api.openai.com/v1/realtime"
    VOICE_OPENAI_REALTIME_MODEL: str = "gpt-4o-realtime-preview"
    VOICE_OPENAI_REALTIME_VOICE: str = "alloy"
    VOICE_OPENAI_REALTIME_TRANSCRIPTION_MODEL: str = "gpt-4o-mini-transcribe"
    VOICE_OPENAI_REALTIME_TRANSCRIPTION_LANGUAGE: str = "en"
    VOICE_DEBUG_RECORD_MIN_SECONDS: float = Field(default=4.0, ge=0.0)
    VOICE_DEBUG_RECORD_IDLE_FLUSH_SECONDS: float = Field(default=1.5, gt=0.0)
    VOICE_DIRECT_ADDRESS_FOLLOWUP_WINDOW_SECONDS: float = Field(default=20.0, ge=0.0)
    VOICE_CONTINUATION_GRACE_MS: int = Field(default=650, ge=0)
    VOICE_EAGER_EOT_THRESHOLD: float = Field(default=0.55, ge=0.0)
    VOICE_EOT_THRESHOLD: float = Field(default=0.7, ge=0.0)
    VOICE_EOT_TIMEOUT_MS: int = Field(default=1200, ge=0)

    # --- Web search / identity ------------------------------------------
    ENABLE_WEB_SEARCH: bool = False
    EXA_API_KEY: str = ""
    ENABLE_IDENTITY_ARCHITECTURE: bool = True
    IDENTITY_ROOT_PATH: str = "identity"
    IDENTITY_CHAT_ANALYSIS_COOLDOWN_SECONDS: int = Field(default=900, ge=0)

    # --- Bounded reflection ---------------------------------------------
    BOUNDED_REFLECTION_POST_TURN_COOLDOWN_SECONDS: int = Field(default=21600, ge=0)
    BOUNDED_REFLECTION_PERIODIC_INTERVAL_MINUTES: int = Field(default=360, ge=1)
    BOUNDED_REFLECTION_ACTIVITY_LOOKBACK_HOURS: int = Field(default=72, ge=1)

    # --- Runtime / surface reliability ----------------------------------
    RUNTIME_MAX_RECOVERY_RESUME_ATTEMPTS: int = Field(default=1, ge=0)
    SURFACE_RELIABILITY_MAX_AUTO_RECOVERY_ATTEMPTS: int = Field(default=1, ge=0)

    # --- Home Assistant -------------------------------------------------
    HOME_ASSISTANT_ENABLED: bool = False
    HOME_ASSISTANT_URL: str = ""
    HOME_ASSISTANT_TOKEN: str = ""
    HOME_ASSISTANT_OWNER_ENTITY_IDS: List[str] = Field(default_factory=list)

    # --- Presence / ambient timing --------------------------------------
    LOCAL_MACHINE_PRESENCE_POLL_SECONDS: float = Field(default=10.0, gt=0.0)
    HOME_CONTEXT_RECONCILE_SECONDS: float = Field(default=120.0, gt=0.0)
    HOME_CONTEXT_EVENT_DEDUP_SECONDS: float = Field(default=20.0, ge=0.0)
    AMBIENT_ARRIVAL_COOLDOWN_SECONDS: float = Field(default=1800.0, ge=0.0)
    AMBIENT_MACHINE_UNLOCK_GRACE_SECONDS: float = Field(default=180.0, ge=0.0)
    AMBIENT_ARRIVAL_LOCKED_THRESHOLD_SECONDS: float = Field(default=600.0, ge=0.0)
    AMBIENT_ARRIVAL_IDLE_THRESHOLD_SECONDS: float = Field(default=1200.0, ge=0.0)

    # --- Operator browser/desktop ---------------------------------------
    OPERATOR_BROWSER_PROFILE_DIR: str = "data/operator-browser/profiles"
    OPERATOR_BROWSER_DOWNLOADS_DIR: str = "data/operator-browser/downloads"
    OPERATOR_ADAPTER_HEALTH_POLL_SECONDS: float = Field(default=30.0, gt=0.0)
    OPERATOR_SCREENSHOT_DIR: str = "data/operator-desktop/captures"
    OPERATOR_SCREENSHOT_RETENTION_SECONDS: float = Field(default=1800.0, ge=0.0)
    OPERATOR_VISION_INSPECTION_MAX_AGE_SECONDS: float = Field(default=300.0, gt=0.0)

    # --- Backend socket: where she listens, and who may reach her ---------
    # Week 2 task 4. Declaring them here is the whole plumbing: from_config
    # feeds every public Config attribute into the schema.
    BACKEND_HOST: str = "127.0.0.1"
    BACKEND_ALLOWED_NETWORKS: str = DEFAULT_ALLOWED_NETWORKS
    # Week 2 task 7: declared for the same reason — the line that says Discord
    # is off for this boot names the doors that are open, and it cannot name
    # them from a field the schema cannot see.
    BACKEND_PORT: int = 8000

    # --- Database / messaging --------------------------------------------
    ENABLE_DISCORD_BOT: bool = True
    DISCORD_TOKEN: str = ""
    # Week 2 task 3: the owner's raw Discord snowflake. Blank is allowed —
    # it just means nobody is the owner on Discord (see the validator below).
    LOCAL_OWNER_DISCORD_USER_ID: str = ""
    # Telegram door (2026-09-14): the owner's Telegram user id. Blank is
    # allowed — it just means nobody is the owner on Telegram, which the
    # validator below says out loud once the door is actually open.
    LOCAL_OWNER_TELEGRAM_USER_ID: str = ""
    # Telegram door (2026-09-14, task 2): the door itself. OFF by default —
    # an install that has never heard of Telegram grows no new behaviour and
    # no new warning.
    ENABLE_TELEGRAM_BOT: bool = False
    TELEGRAM_BOT_TOKEN: str = ""
    # The mobile door (2026-10-13): the switch, the address for the pairing
    # QR, and the host list the address is checked against.
    ENABLE_MOBILE_DOOR: bool = False
    MOBILE_PUBLIC_URL: str = ""
    BACKEND_ALLOWED_HOSTS: str = ""
    DATABASE_PATH: str = "data/palli-local.sqlite3"


    # --- Agentic settings -----------------------------------------------
    AGENTIC_MODE: str = "hybrid"
    AGENTIC_ROLLOUT_MODE: str = "enforce"
    AGENTIC_MAX_STEPS: int = Field(default=5, ge=1)
    AGENTIC_STEP_TIMEOUT_SECONDS: float = Field(default=45.0, gt=0.0)
    AGENTIC_RUN_TIMEOUT_SECONDS: float = Field(default=120.0, gt=0.0)
    AGENTIC_CONFIRM_TIMEOUT_SECONDS: int = Field(default=600, ge=30)
    AGENTIC_MAX_PENDING_CONFIRMATIONS_PER_USER: int = Field(default=3, ge=0)
    AGENTIC_POST_CONFIRM_MAX_STEPS: int = Field(default=3, ge=1)
    AGENTIC_POST_CONFIRM_TIMEOUT_SECONDS: float = Field(default=60.0, gt=0.0)
    AGENTIC_MAX_SAME_COMMAND_ATTEMPTS_PER_RUN: int = Field(default=2, ge=1)
    AGENTIC_QUEUE_MODE: str = "collect"
    AGENTIC_CONTROL_CANCEL_SCOPE: str = "run_and_descendants"
    AGENTIC_CONTROL_REDIRECT_MODE: str = "replace_goal"
    AGENTIC_STALE_CONFIRMATION_POLICY: str = "supersede"
    AGENTIC_LANE_ROUTING_MODE: str = "auto"
    AGENTIC_QUEUE_DEBOUNCE_MS: int = Field(default=1000, ge=0)
    AGENTIC_QUEUE_CAP: int = Field(default=20, ge=1)
    AGENT_DOWNLOAD_ROOT: str = "data/agent_downloads"
    AGENT_DOWNLOAD_MAX_BYTES: int = Field(default=100 * 1024 * 1024, gt=0)
    AGENTIC_SESSION_IDLE_TTL_SECONDS: int = Field(default=1800, gt=0)
    AGENTIC_SESSION_ARCHIVE_RETENTION_DAYS: int = Field(default=14, ge=1)
    AGENTIC_SESSION_REPLY_BACK_MAX_ROUNDS: int = Field(default=1, ge=0)
    AGENTIC_COMPACTION_TRIGGER_STEPS: int = Field(default=6, ge=1)
    AGENTIC_COMPACTION_RECENT_OBSERVATIONS: int = Field(default=4, ge=1)
    AGENTIC_COMPACTION_RECENT_HISTORY_LINES: int = Field(default=8, ge=1)
    AGENTIC_MODEL_RETRY_MAX_ATTEMPTS: int = Field(default=1, ge=0)
    AGENTIC_TOOL_RETRY_MAX_ATTEMPTS: int = Field(default=1, ge=0)
    AGENTIC_MAX_RUN_TOKENS: int = Field(default=24000, ge=1)
    AGENTIC_CHILD_MAX_STEPS: int = Field(default=4, ge=1)
    AGENTIC_CHILD_RUN_TIMEOUT_SECONDS: float = Field(default=90.0, gt=0.0)
    AGENTIC_CHILD_MAX_RUN_TOKENS: int = Field(default=8000, ge=1)
    AGENTIC_ROOT_MAX_CHILD_RUNS: int = Field(default=6, ge=1)
    AGENTIC_ROOT_MAX_CHILD_TOTAL_SECONDS: float = Field(default=240.0, gt=0.0)
    AGENTIC_ROOT_MAX_CHILD_TOTAL_TOKENS: int = Field(default=32000, ge=1)
    AGENTIC_BUDGET_WARN_RATIO: float = Field(default=0.8, gt=0.0, lt=1.0)
    AGENTIC_STREAMING_BREAK: str = "text_end"
    AGENTIC_STREAMING_MIN_CHARS: int = Field(default=140, ge=1)
    AGENTIC_STREAMING_MAX_CHARS: int = 1200  # validated against MIN below
    AGENTIC_STREAMING_IDLE_MS: int = Field(default=450, ge=0)
    DISCORD_AGENTIC_PREVIEW_MODE: str = "block"

    # --- Web ops ---------------------------------------------------------
    WEB_OPS_VISIT_MODE: str = "path_window"
    WEB_OPS_VISIT_WINDOW_SECONDS: int = Field(default=1800, ge=60)
    WEB_OPS_DETAIL_LEVEL: str = "full"
    WEB_OPS_EVENT_TYPES: List[str] = Field(default_factory=list)

    # --- CORS ------------------------------------------------------------
    FRONTEND_ORIGINS: List[str] = Field(default_factory=list)
    FRONTEND_ORIGIN_REGEX: Optional[str] = None
    FRONTEND_URL: str = ""

    # =========================================================================
    # Cross-field validators. Each one mirrors a block from Config.validate().
    # =========================================================================

    @model_validator(mode="after")
    def _validate_messaging_platform(self, info: ValidationInfo) -> "CoreConfigSchema":
        """Week 2 task 7: an enabled Discord bot with no token is SOFT.

        Week 1 made this a blocker, on the premise that Discord was the only
        room she had: a stranger who left the token blank got a healthy-looking
        boot, no bot, and nothing to search for. The owner's ruling of
        2026-09-10 replaces that premise — the DASHBOARD is a stranger's first
        door, and Discord is where she lives with you day to day. So a blank
        token is no longer a reason to refuse to start. It turns Discord off
        for this boot, like every other missing key for an optional surface,
        and the line names the doors that ARE open: "Discord is off" on its own
        would leave a stranger with nowhere to go.

        `effective_discord_enabled` is the verdict the runtime reads
        (core/launcher_runtime.py's channel map, the health readout, doctor's
        optional-capability check). False whenever the flag is off or the token
        is blank. Each of those consumers still ANDs the raw token on top —
        belt and braces, by the reviewer's ruling: `Config` declares this True
        at class level, so a process that reads it before validate() has run
        must not open a Discord door on a blank token.

        FIRST of the after-validators, and recorded rather than raised, so the
        line survives a validator below it raising. Anything defined above this
        would take the token off a stranger's first report; keep it here.
        """
        has_token = bool(str(self.DISCORD_TOKEN or "").strip())
        if self.ENABLE_DISCORD_BOT and not has_token:
            host = _display_host(self.BACKEND_HOST)
            dashboard = str(self.FRONTEND_URL or "").strip() or "http://localhost:3000"
            _soft(
                info,
                "DISCORD_TOKEN",
                "no token, so the Discord door is closed for this boot; the "
                f"backend is still up at http://{host}:{self.BACKEND_PORT} and "
                f"the dashboard at {dashboard} when you run it",
            )
        _effective(
            info,
            "effective_discord_enabled",
            bool(self.ENABLE_DISCORD_BOT) and has_token,
        )
        return self

    @model_validator(mode="after")
    def _validate_telegram_door(self, info: ValidationInfo) -> "CoreConfigSchema":
        """Telegram door (2026-09-14, task 2): the same ruling as Discord's.

        An enabled door with no token is SOFT — it turns the door off for
        this boot and names where she IS reachable, because "Telegram is off"
        on its own would leave a stranger with nowhere to go. The flag is off
        by default, so an install that never asked for this door never sees a
        word about it.

        The second way this door fails is NOT Discord's: the library is an
        ordinary pip dependency, so a token can be set on a tree where
        `python-telegram-bot` was never installed. That is a door that opens
        on nothing, and the only place it can be caught before a stranger
        spends an evening on it is here.

        `effective_telegram_enabled` is the verdict the runtime reads
        (core/launcher_runtime's channel map, the health readout). Each
        consumer still ANDs the raw token on top — belt and braces, the same
        rule the Discord validator above records.
        """
        has_token = bool(str(self.TELEGRAM_BOT_TOKEN or "").strip())
        installed = True
        if self.ENABLE_TELEGRAM_BOT and not has_token:
            host = _display_host(self.BACKEND_HOST)
            dashboard = str(self.FRONTEND_URL or "").strip() or "http://localhost:3000"
            _soft(
                info,
                "TELEGRAM_BOT_TOKEN",
                "no token, so the Telegram door is closed for this boot; the "
                f"backend is still up at http://{host}:{self.BACKEND_PORT} and "
                f"the dashboard at {dashboard} when you run it",
            )
        elif self.ENABLE_TELEGRAM_BOT and has_token:
            installed = _telegram_library_present()
            if not installed:
                _soft(
                    info,
                    "TELEGRAM_BOT_TOKEN",
                    "python-telegram-bot is not installed, so the Telegram "
                    "door is closed for this boot; install it "
                    "(`pip install -r requirements.txt`) and restart",
                )
        _effective(
            info,
            "effective_telegram_enabled",
            bool(self.ENABLE_TELEGRAM_BOT) and has_token and installed,
        )
        return self

    @model_validator(mode="after")
    def _validate_mobile_door(self, info: ValidationInfo) -> "CoreConfigSchema":
        """The mobile door (2026-10-13, spec §9). It has no token, so it never
        shuts for lack of one; what can be wrong is the road to it, and every
        such thing is said out loud — never a refusal to start."""
        if self.ENABLE_MOBILE_DOOR:
            url = str(self.MOBILE_PUBLIC_URL or "").strip()
            if not url:
                _soft(info, "MOBILE_PUBLIC_URL",
                      "the mobile door is on with no address for the pairing QR, so pairing "
                      "will ask for the address by hand")
            else:
                try:
                    host = (urlparse(url).hostname or "").strip().lower()
                except ValueError:
                    # "http://[abc": urlparse refuses it, and an unguarded refusal would
                    # stop the boot over a phone setting without naming it.
                    host = ""
                if not host:
                    # Also what a scheme-less "100.64.0.7:8000" reads as: no host at all.
                    _soft(info, "MOBILE_PUBLIC_URL",
                          "it is not an address a phone can use; it needs http:// and a host, "
                          "like http://palli-box.example.ts.net:8000")
                elif not is_allowed_name(host, parse_allowed_hosts(self.BACKEND_ALLOWED_HOSTS)):
                    # The host gate's own reader and predicate, so a name written with a
                    # scheme, a port or a trailing dot is judged the way the gate judges it.
                    _soft(info, "MOBILE_PUBLIC_URL",
                          f"{host} is not in BACKEND_ALLOWED_HOSTS, so the backend's host check "
                          "will refuse the phone",
                          where="add it to BACKEND_ALLOWED_HOSTS in .env.local")
            if _is_loopback_bind(self.BACKEND_HOST):
                _soft(info, "BACKEND_HOST",
                      "the mobile door is on but the backend listens only on 127.0.0.1, so no "
                      "phone can reach it",
                      where="set BACKEND_HOST=0.0.0.0 in .env.local (the network gate still "
                            "admits only BACKEND_ALLOWED_NETWORKS)")
        _effective(info, "effective_mobile_enabled", bool(self.ENABLE_MOBILE_DOOR))
        return self

    @model_validator(mode="after")
    def _validate_owner_identity(self, info: ValidationInfo) -> "CoreConfigSchema":
        """Week 2 task 3 (gap §5.1.5): Discord on, no owner id — SOFT.

        With no owner snowflake to compare against, nobody who types in her
        channel can be proved to be the owner, so everyone is a guest: a
        plain reply, no shell, no files, nothing written into his memory.
        That is a legitimate way to run her — and it is also what a stranger
        gets by accident on their first boot, wondering why she ignores every
        tool they ask for. Never a blocker; always said out loud.

        Only when the bot is effectively ON (enabled AND holding a token):
        with no token Discord is off for this boot (task 7) and already says
        so, and a second line about an id for a channel that never opens is
        noise.

        UNUSABLE counts as unset. A value that is not all digits is not a
        snowflake: core/local_mode.py:86-94 logs "is not numeric; ignoring"
        and leaves the owner's discord id NULL, which lands a stranger in
        exactly the guest-only world above while the key LOOKS set. That is
        how the template itself used to ship it (a trailing `# comment`
        became the value), and a placeholder like "your-id-here" does the
        same. The line names the value back at them so it is findable.
        """
        if not (self.ENABLE_DISCORD_BOT and str(self.DISCORD_TOKEN or "").strip()):
            return self

        owner_did = str(self.LOCAL_OWNER_DISCORD_USER_ID or "").strip()
        if not owner_did:
            # Batch 8 Task 11 A2: this line reads .env.local only — it has no
            # database. An id stored on the owner's row (seeded once, or linked
            # on the dashboard) still makes him the owner, so the line says
            # what it can know and leaves the verdict to boot: the local_owner
            # stage (core/backend_bootstrap.py) logs the real warning only
            # when the stored owner has no id either.
            _soft(
                info,
                "LOCAL_OWNER_DISCORD_USER_ID",
                "Discord is on but no owner id is in .env.local; if none is "
                "stored either, everyone she meets is a guest (no shell, no "
                "files, no memory of hers) — checked against the stored owner "
                "at boot",
            )
        elif not owner_did.isdigit():
            _soft(
                info,
                "LOCAL_OWNER_DISCORD_USER_ID",
                f'"{owner_did}" is not a Discord snowflake and will be '
                "ignored; until a numeric id is set, everyone she meets is "
                "a guest",
            )
        return self

    @model_validator(mode="after")
    def _validate_telegram_owner_identity(self, info: ValidationInfo) -> "CoreConfigSchema":
        """Telegram door: who the owner is on it, or that nobody is.

        Task 1 could only say the second half of this — a set-but-unusable id
        — because there was no door yet for a blank one to matter at. Task 2
        opened the door, so this now mirrors `_validate_owner_identity`
        exactly:

        Only when the bot is effectively ON (enabled AND holding a token).
        With no token the door is closed for this boot and already says so,
        and a second line about an id for a room that never opens is noise.

        BLANK: nobody can be proved to be him on Telegram, so everyone is a
        guest — a legitimate way to run her, and also what a stranger gets by
        accident, wondering why she ignores every tool they ask for.

        UNUSABLE counts as unset: core/local_mode.py logs "is not numeric;
        ignoring" and leaves users.telegram_user_id NULL, which lands them in
        exactly that guest-only world while the key LOOKS set. The line names
        the value back at them so it is findable.
        """
        if not (self.ENABLE_TELEGRAM_BOT and str(self.TELEGRAM_BOT_TOKEN or "").strip()):
            return self

        value = str(self.LOCAL_OWNER_TELEGRAM_USER_ID or "").strip()
        if not value:
            # Batch 8 Task 11 A2, the twin: same reason as the Discord line.
            _soft(
                info,
                "LOCAL_OWNER_TELEGRAM_USER_ID",
                "Telegram is on but no owner id is in .env.local; if none is "
                "stored either, everyone she meets on Telegram is a guest (no "
                "shell, no files, no memory of hers) — checked against the "
                "stored owner at boot",
            )
        elif not value.isdigit():
            _soft(
                info,
                "LOCAL_OWNER_TELEGRAM_USER_ID",
                f'"{value}" is not a Telegram user id and will be ignored; '
                "until a numeric id is set, everyone she meets on Telegram "
                "is a guest",
            )
        return self

    @model_validator(mode="after")
    def _validate_trusted_networks(self, info: ValidationInfo) -> "CoreConfigSchema":
        """Week 2 task 4: a list that fails to parse locks the door.

        BACKEND_ALLOWED_NETWORKS is the last thing between a stranger on the
        network and a chat endpoint that can reach local_shell. Every
        unparseable token used to be dropped with a log line nobody reads, so
        one typo emptied the list — and an empty list is OPEN. The setting
        failed in the intruder's favour, silently.

        What boot says now:

          * blank — nothing. "" is a documented choice: run open on purpose.
          * nothing parsed — HARD. The gate is locked (no IP peer passes,
            loopback included), so she would answer nobody; and a locked gate
            arrived at by accident is a typo, not a decision.
          * some token failed, bind NOT loopback — HARD. The socket is on
            every network the host sits on and the list of who may reach her
            is wrong. Refuse to start.
          * some token failed, bind loopback — SOFT. Only this machine can
            reach the socket, so it is a thing to fix, not a reason not to
            start; the tokens that parsed are the list.

        Either way the good tokens are used and the bad one is named: a bad
        token never widens the door.
        """
        policy = parse_networks(self.BACKEND_ALLOWED_NETWORKS)
        if not policy.rejected:
            return self

        quoted = ", ".join(f'"{token}"' for token in policy.rejected)
        why = (
            f"{quoted} is not a CIDR"
            if len(policy.rejected) == 1
            else f"{quoted} are not CIDRs"
        )
        if policy.networks and _is_loopback_bind(self.BACKEND_HOST):
            _soft(info, "BACKEND_ALLOWED_NETWORKS", why)
        else:
            _hard(info, "BACKEND_ALLOWED_NETWORKS", why)
        return self

    @model_validator(mode="after")
    def _validate_enums(self) -> "CoreConfigSchema":
        """Enum-like fields whose Pydantic-Field default doesn't suffice
        (because we want plugin-runtime fallback to demote unknown values
        to warnings rather than hard failures).
        """
        errors: List[str] = []

        for field, allowed in (
            ("PALLI_DEPLOYMENT_MODE", ALLOWED_DEPLOYMENT_MODES),
            ("VOICE_RUNTIME_MODE", ALLOWED_VOICE_RUNTIME_MODES),
            ("VOICE_INTERACTION_MODE", ALLOWED_VOICE_INTERACTION_MODES),
            ("LOCAL_VOICE_DEFAULT_MODE", ALLOWED_LOCAL_VOICE_DEFAULT_MODES),
            ("LOCAL_VOICE_PRESENCE_PROFILE", ALLOWED_LOCAL_VOICE_PRESENCE_PROFILES),
            ("VOICE_PASSIVE_POLICY", ALLOWED_VOICE_PASSIVE_POLICIES),
            ("VOICE_DEEPGRAM_STREAM_ENCODING", ALLOWED_VOICE_DEEPGRAM_ENCODINGS),
            ("VOICE_LOCAL_STT_DEVICE", ALLOWED_VOICE_LOCAL_STT_DEVICES),
            ("VOICE_LOCAL_MODEL_HOST", ALLOWED_VOICE_LOCAL_MODEL_HOSTS),
            ("AGENTIC_MODE", ALLOWED_AGENTIC_MODES),
            ("AGENTIC_ROLLOUT_MODE", ALLOWED_AGENTIC_ROLLOUT_MODES),
            ("AGENTIC_QUEUE_MODE", ALLOWED_AGENTIC_QUEUE_MODES),
            ("AGENTIC_CONTROL_CANCEL_SCOPE", ALLOWED_AGENTIC_CANCEL_SCOPES),
            ("AGENTIC_CONTROL_REDIRECT_MODE", ALLOWED_AGENTIC_REDIRECT_MODES),
            ("AGENTIC_STALE_CONFIRMATION_POLICY", ALLOWED_AGENTIC_STALE_POLICIES),
            ("AGENTIC_LANE_ROUTING_MODE", ALLOWED_AGENTIC_LANE_MODES),
            ("AGENTIC_STREAMING_BREAK", ALLOWED_AGENTIC_STREAMING_BREAKS),
            ("DISCORD_AGENTIC_PREVIEW_MODE", ALLOWED_DISCORD_AGENTIC_PREVIEW_MODES),
            ("WEB_OPS_VISIT_MODE", ALLOWED_WEB_OPS_VISIT_MODES),
            ("WEB_OPS_DETAIL_LEVEL", ALLOWED_WEB_OPS_DETAIL_LEVELS),
        ):
            value = getattr(self, field)
            if value not in allowed:
                errors.append(
                    f"{field} must be one of: {', '.join(sorted(allowed))} "
                    f"(got {value!r})"
                )

        if errors:
            raise ValueError("; ".join(errors))
        return self

    @model_validator(mode="after")
    def _validate_llm_provider_keys(self, info: ValidationInfo) -> "CoreConfigSchema":
        """The selected LLM_PROVIDER and the per-task overrides must each
        have the credentials/configuration they need. Plugin runtime
        relaxes the strict allowlist (unknown providers are deferred).

        First door, task 1 (2026-09-17): the TOP-LEVEL provider with no key at
        all is SETUP MODE, not a failure. Week 2's ruling made the dashboard a
        stranger's first door, and this was the hard error standing in front
        of it: no key, exit 2, nothing to open. Now she starts with
        `brain_ready` False, one soft line names the address where a brain can
        be added, and both chat doors are shut for this boot whatever their
        tokens say — she must not answer on Discord with no brain. A key that
        is present but WRONG is not setup mode; the live test catches that.

        Only that one check went soft. A local server with no address, the
        codex checks and every per-task override below stay hard: none of them
        is a first boot, and none can be fixed by pasting a key on the door.

        Defined BELOW the two door validators on purpose. Pydantic runs
        after-validators in definition order and the collector's `effective`
        dict keeps the last write, so closing the doors here is the last word
        (tests/core/test_setup_mode.py pins the order).
        """
        errors: List[str] = []
        provider = (self.LLM_PROVIDER or "").lower()
        plugin_runtime = self.PLUGIN_RUNTIME_ENABLED

        # Top-level LLM_PROVIDER: a missing key is setup mode, recorded and soft.
        brain_key = BRAIN_KEY_BY_PROVIDER.get(provider, "")
        brain_ready = not brain_key or bool(str(getattr(self, brain_key) or "").strip())
        _effective(info, "brain_ready", brain_ready)
        if not brain_ready:
            _soft(
                info,
                BRAIN_PUNCH_KEY,
                "no brain configured",
                where=(
                    f"open {dashboard_address(self.FRONTEND_URL)} to add one "
                    f"(or set {brain_key} in .env.local)"
                ),
            )
            # What the two door validators above decided, before it is overruled.
            decided = dict((_sink(info) or {}).get("effective", {}))
            for verdict, door in DOOR_PUNCH_KEY_BY_VERDICT.items():
                _effective(info, verdict, False)
                # A door that was shut anyway already has its line from its own
                # validator. One that WOULD have opened needs this one: "no
                # token" would be a lie, and silence would leave a stranger
                # staring at a bot that never comes online.
                if decided.get(verdict):
                    _soft(
                        info,
                        door,
                        "closed until a brain is configured",
                        where=_DOOR_WAITING_WHERE.get(
                            door,
                            "its token is set; it opens at the first restart "
                            "after you add a brain",
                        ),
                    )
        if provider == "local_openai":
            if not self.LOCAL_LLM_API_BASE.strip():
                errors.append(
                    "LOCAL_LLM_API_BASE is required when using local_openai provider"
                )
            if not self.LOCAL_LLM_MODEL.strip():
                errors.append(
                    "LOCAL_LLM_MODEL is required when using local_openai provider"
                )
        if provider == "codex":
            if not self.CODEX_MODEL.strip():
                errors.append("CODEX_MODEL is required when using codex provider")
            if self.CODEX_TIMEOUT_SECONDS <= 0:
                errors.append("CODEX_TIMEOUT_SECONDS must be greater than 0")
            # NOTE: Codex CLI login check stays in Config.validate() because
            # it does subprocess I/O — schema validators must be pure.

        # Per-task provider checks — same constraints
        task_provider_fields = (
            "LLM_RESPONSE_PROVIDER",
            "LLM_ANALYSIS_PROVIDER",
            "LLM_MEMORY_PROVIDER",
            "LLM_DECISION_PROVIDER",
            "LLM_PROACTIVE_PROVIDER",
            "LLM_REFLECTION_PROVIDER",
            "LLM_WEB_SEARCH_PROVIDER",
            "LLM_VISION_PROVIDER",
        )
        for field_name in task_provider_fields:
            task_provider = (getattr(self, field_name) or "").strip().lower()
            if not task_provider:
                continue
            if task_provider not in ALLOWED_LLM_PROVIDERS:
                if not plugin_runtime:
                    errors.append(
                        f"{field_name} must be one of: openai, deepseek, "
                        f"local_openai, codex"
                    )
                # else: plugin runtime resolves it — accept silently
                continue
            if not brain_ready and task_provider == provider:
                # Ruling P3 refined (first door, task 1; the owner's yes
                # 2026-09-17 22:36): an override naming the SAME provider as
                # the keyless top-level one wants the very key the door is
                # about to supply. One missing key is one soft line, not a
                # second, hard one — the owner's own walk, a key removed from a
                # scratch copy with the overrides still saying deepseek, must
                # land in setup mode. A DIFFERENT keyless provider stays hard.
                continue
            if task_provider == "openai" and not self.OPENAI_API_KEY:
                errors.append(f"{field_name}=openai requires OPENAI_API_KEY")
            elif task_provider == "deepseek" and not self.DEEPSEEK_API_KEY:
                errors.append(f"{field_name}=deepseek requires DEEPSEEK_API_KEY")
            elif task_provider == "local_openai":
                if not self.LOCAL_LLM_API_BASE.strip():
                    errors.append(
                        f"{field_name}=local_openai requires LOCAL_LLM_API_BASE"
                    )
                if not self.LOCAL_LLM_MODEL.strip():
                    errors.append(
                        f"{field_name}=local_openai requires LOCAL_LLM_MODEL"
                    )
            elif task_provider == "codex":
                if not self.CODEX_MODEL.strip():
                    errors.append(f"{field_name}=codex requires CODEX_MODEL")
                if self.CODEX_TIMEOUT_SECONDS <= 0:
                    errors.append("CODEX_TIMEOUT_SECONDS must be greater than 0")

        if provider and provider not in ALLOWED_LLM_PROVIDERS and not plugin_runtime:
            # Pre-existing behavior was a warning, not an error — preserved.
            logger.warning(
                "Unknown LLM provider %r, will default to OpenAI", provider
            )
        elif provider and provider not in ALLOWED_LLM_PROVIDERS:
            logger.warning(
                "LLM_PROVIDER=%s will be resolved through the plugin runtime",
                provider,
            )

        if errors:
            raise ValueError("; ".join(errors))
        return self

    @model_validator(mode="after")
    def _validate_voice_features(self, info: ValidationInfo) -> "CoreConfigSchema":
        """Voice provider/key coherence and engine-specific constraints.

        Mirrors Config.validate() lines 1087-1122. Only enforced when
        ENABLE_VOICE_FEATURES is true; otherwise voice fields are
        unconstrained (PaLLi can run text-only).

        E.5 Wave 4 step 4 (2026-05-13): VOICE_ENGINE_DEFAULT, STT_PROVIDER,
        TTS_PROVIDER moved to data/palli.yaml -> voice.*. Resolution
        order: explicit payload override (tests) > settings_service
        (runtime via from_config).
        """
        if not self.ENABLE_VOICE_FEATURES:
            return self

        errors: List[str] = []
        plugin_runtime = self.PLUGIN_RUNTIME_ENABLED

        # Source the three migrated voice fields from explicit payload
        # if provided, else from the YAML snapshot.
        def _resolve(name: str, schema_field_default: str, yaml_attr: str) -> str:
            if name in self.model_fields_set:
                return str(getattr(self, name) or schema_field_default)
            try:
                import importlib
                _settings = importlib.import_module("core.settings_boot").settings_service
                return str(
                    getattr(_settings.snapshot().voice, yaml_attr).value
                    or schema_field_default
                )
            except Exception:
                return str(getattr(self, name) or schema_field_default)

        voice_engine = _resolve("VOICE_ENGINE_DEFAULT", "classic", "engine")
        stt_provider = _resolve("STT_PROVIDER", "deepgram", "stt_provider")
        tts_provider = _resolve("TTS_PROVIDER", "deepgram", "tts_provider")

        if voice_engine not in ALLOWED_VOICE_ENGINES:
            errors.append("VOICE_ENGINE_DEFAULT must be: classic")
        if (
            stt_provider not in ALLOWED_STT_PROVIDERS
            and not plugin_runtime
        ):
            errors.append(
                "STT_PROVIDER must be one of: deepgram, faster_whisper"
            )
        if (
            tts_provider not in ALLOWED_TTS_PROVIDERS
            and not plugin_runtime
        ):
            errors.append(
                "TTS_PROVIDER must be one of: deepgram, lemonfox, pyttsx3"
            )

        # Week 1 task 1: the two Deepgram key checks are SOFT. A stranger with no
        # Deepgram account had a text companion he could not boot — the key buys
        # voice, and nothing else, so its absence turns voice off and says so.
        if stt_provider == "deepgram" and not self.DEEPGRAM_API_KEY:
            _soft(
                info,
                "DEEPGRAM_API_KEY",
                "speech-to-text is set to deepgram but there is no key, "
                "so listening is off for this boot",
            )
            _effective(info, "effective_stt_provider", "off")
        else:
            _effective(info, "effective_stt_provider", stt_provider)
        if (
            stt_provider == "faster_whisper"
            and not self.VOICE_LOCAL_STT_MODEL.strip()
        ):
            errors.append(
                "VOICE_LOCAL_STT_MODEL is required when STT_PROVIDER=faster_whisper"
            )
        if tts_provider == "deepgram" and not self.DEEPGRAM_API_KEY:
            _soft(
                info,
                "DEEPGRAM_API_KEY",
                "text-to-speech is set to deepgram but there is no key, "
                "so her voice is off for this boot",
            )
            _effective(info, "effective_tts_provider", "off")
        else:
            _effective(info, "effective_tts_provider", tts_provider)
        # Step 31 (2026-08-20): the cartesia key-pair validation block left
        # with the provider itself; "cartesia" now fails the allowed-set
        # check above. Lemonfox's key check stays env-side best-effort (the
        # client logs-and-returns-None without it — voice degrades, never
        # blocks boot).
        # Step 31 (2026-08-20): the openai_realtime_bridge key requirement
        # left with the deleted bridge — "openai_realtime_bridge" now fails
        # the allowed-set check above.

        if errors:
            raise ValueError("; ".join(errors))
        return self

    @model_validator(mode="after")
    def _validate_voice_runtime_constants(self) -> "CoreConfigSchema":
        """Voice runtime URLs and model names that must be non-empty
        regardless of whether voice features are enabled.

        Mirrors Config.validate() lines 1123-1216 which run unconditionally —
        these are the URLs/model names baked into the runtime even when the
        voice features feature-flag is off.
        """
        errors: List[str] = []
        # Step 31 (2026-08-20): the six OPENAI_REALTIME_* names left this
        # tuple with the deleted bridge — nothing bakes them into the
        # runtime any more (their schema FIELDS stay; key removal is its
        # own migration).
        non_empty_fields = (
            "VOICE_LOCAL_STT_MODEL",
            "VOICE_OLLAMA_API_BASE",
            "VOICE_LM_STUDIO_API_BASE",
        )
        for field_name in non_empty_fields:
            value = str(getattr(self, field_name) or "").strip()
            if not value:
                errors.append(f"{field_name} must not be empty")

        if errors:
            raise ValueError("; ".join(errors))
        return self

    @model_validator(mode="after")
    def _validate_web_search(self, info: ValidationInfo) -> "CoreConfigSchema":
        # E.5 Wave 4 step 3 (2026-05-13): web_search moved off
        # core.config.Config onto data/palli.yaml -> features.web_search.
        # The launcher validator still cross-checks "if web_search is
        # on, EXA_API_KEY (an env secret) must be present".
        #
        # Resolution order:
        #   1. If the caller explicitly passed ENABLE_WEB_SEARCH (e.g.
        #      tests model_validate-ing a payload dict), trust that.
        #   2. Otherwise, read from settings_service (the runtime path
        #      through CoreConfigSchema.from_config).
        #
        # The settings_service import is done via importlib so the
        # Phase-8a static cycle detector doesn't see core.config_schema
        # statically importing core.settings_boot (which transitively
        # touches the ai.llm_* registry).
        if "ENABLE_WEB_SEARCH" in self.model_fields_set:
            web_search_enabled = bool(self.ENABLE_WEB_SEARCH)
        else:
            try:
                import importlib
                _settings = importlib.import_module("core.settings_boot").settings_service
                web_search_enabled = bool(_settings.snapshot().features.web_search)
            except Exception:
                web_search_enabled = bool(self.ENABLE_WEB_SEARCH)
        # Week 1 task 1: SOFT. Web search is an extra; a missing key costs the
        # stranger the tool, not the companion.
        if web_search_enabled and not self.EXA_API_KEY:
            _soft(info, "EXA_API_KEY", "web search is on but there is no key")
            _effective(info, "effective_web_search_enabled", False)
        else:
            _effective(
                info, "effective_web_search_enabled", bool(web_search_enabled)
            )
        return self

    @model_validator(mode="after")
    def _validate_identity(self) -> "CoreConfigSchema":
        if self.ENABLE_IDENTITY_ARCHITECTURE and not self.IDENTITY_ROOT_PATH.strip():
            raise ValueError(
                "IDENTITY_ROOT_PATH must not be empty when identity architecture is enabled"
            )
        return self

    @model_validator(mode="after")
    def _validate_home_assistant(self) -> "CoreConfigSchema":
        # E.5 Wave 4 step 3 (2026-05-13): home_assistant moved off
        # core.config.Config onto data/palli.yaml -> features.home_assistant.
        # Resolution: explicit payload override (tests) > settings_service
        # (runtime via from_config). Importlib used to keep this file
        # out of the Phase-8a static import cycle detector.
        if "HOME_ASSISTANT_ENABLED" in self.model_fields_set:
            home_assistant_enabled = bool(self.HOME_ASSISTANT_ENABLED)
        else:
            try:
                import importlib
                _settings = importlib.import_module("core.settings_boot").settings_service
                home_assistant_enabled = bool(
                    _settings.snapshot().features.home_assistant
                )
            except Exception:
                home_assistant_enabled = bool(self.HOME_ASSISTANT_ENABLED)
        if not home_assistant_enabled:
            return self
        errors: List[str] = []
        if not self.HOME_ASSISTANT_URL.strip():
            errors.append("HOME_ASSISTANT_URL is required when HOME_ASSISTANT_ENABLED=true")
        if not self.HOME_ASSISTANT_TOKEN.strip():
            errors.append("HOME_ASSISTANT_TOKEN is required when HOME_ASSISTANT_ENABLED=true")
        if not self.HOME_ASSISTANT_OWNER_ENTITY_IDS:
            errors.append(
                "HOME_ASSISTANT_OWNER_ENTITY_IDS must include at least one entity "
                "when HOME_ASSISTANT_ENABLED=true"
            )
        if errors:
            raise ValueError("; ".join(errors))
        return self

    @model_validator(mode="after")
    def _validate_operator_paths(self) -> "CoreConfigSchema":
        errors: List[str] = []
        for field_name in (
            "OPERATOR_BROWSER_PROFILE_DIR",
            "OPERATOR_BROWSER_DOWNLOADS_DIR",
            "OPERATOR_SCREENSHOT_DIR",
            "AGENT_DOWNLOAD_ROOT",
            "DATABASE_PATH",
        ):
            if not str(getattr(self, field_name) or "").strip():
                errors.append(f"{field_name} must not be empty")
        if errors:
            raise ValueError("; ".join(errors))
        return self

    @model_validator(mode="after")
    def _validate_agentic_streaming_range(self) -> "CoreConfigSchema":
        if self.AGENTIC_STREAMING_MAX_CHARS < self.AGENTIC_STREAMING_MIN_CHARS:
            raise ValueError(
                "AGENTIC_STREAMING_MAX_CHARS must be >= AGENTIC_STREAMING_MIN_CHARS"
            )
        return self

    @model_validator(mode="after")
    def _validate_web_ops_event_types(self) -> "CoreConfigSchema":
        """WEB_OPS_EVENT_TYPES must be non-empty AND every entry must be in
        the allowed set (which depends on legacy hosted access — events
        like 'checkout_started' are only allowed there).
        """
        if not self.WEB_OPS_EVENT_TYPES:
            raise ValueError("WEB_OPS_EVENT_TYPES must include at least one event type")

        # Mirror Config.get_default_web_ops_event_types() — that's the
        # allowed set. Imported lazily to avoid a circular import.
        allowed = {"visit", "signup", "email_verified"}
        is_legacy_hosted = self.ENABLE_LEGACY_HOSTED_ACCESS or (
            self.PALLI_DEPLOYMENT_MODE == "cloud"
        )
        if is_legacy_hosted:
            allowed.update({"checkout_started", "payment_succeeded"})
        invalid = [evt for evt in self.WEB_OPS_EVENT_TYPES if evt not in allowed]
        if invalid:
            raise ValueError(
                f"WEB_OPS_EVENT_TYPES contains invalid values: {', '.join(invalid)}"
            )
        return self

    @model_validator(mode="after")
    def _validate_cors_policy(self) -> "CoreConfigSchema":
        """Production environments require explicit CORS configuration.

        Mirrors Config.validate() lines 1461-1489. Wildcards in
        FRONTEND_ORIGINS are blocked in production unless
        ALLOW_INSECURE_CORS is explicitly set.
        """
        is_prod = (self.ENVIRONMENT or "").lower() in ("production", "prod")

        if (
            "*" in (self.FRONTEND_ORIGINS or [])
            and not self.ALLOW_INSECURE_CORS
            and is_prod
        ):
            raise ValueError(
                "CORS misconfigured: FRONTEND_ORIGINS contains '*'. "
                "Remove it or set ALLOW_INSECURE_CORS=true (not recommended for production)."
            )

        if not self.FRONTEND_ORIGINS and not self.FRONTEND_ORIGIN_REGEX:
            if self.ALLOW_INSECURE_CORS:
                logger.warning(
                    "CORS not configured: allowing all origins because "
                    "ALLOW_INSECURE_CORS=true. Do not use this in production."
                )
            elif is_prod:
                raise ValueError(
                    "CORS not configured: set FRONTEND_ORIGINS or "
                    "FRONTEND_ORIGIN_REGEX (production requires this)."
                )
            else:
                logger.warning(
                    "CORS not configured: set FRONTEND_ORIGINS or "
                    "FRONTEND_ORIGIN_REGEX. Development will infer a single "
                    "origin from FRONTEND_URL."
                )
        return self

    # =========================================================================
    # Helpers
    # =========================================================================

    @classmethod
    def from_config(
        cls, config: Any, *, context: Optional[dict] = None
    ) -> "CoreConfigSchema":
        """Build the schema from a Config instance and validate.

        `context` is the punch-list collector (see the top of this file). Pass
        one to learn what the validators turned off; omit it for a plain
        pass/fail inspection.

        Duck-typed on the argument — accepts any object whose public
        attributes line up with the schema's field names. We avoid
        importing `core.config.Config` here because that would create a
        static cycle (config → config_schema → config) which the
        Phase-8a boundary tests would correctly flag.

        Unknown Config fields are accepted via `extra="allow"` and stored
        on the model — diagnostics stay complete (operators can inspect
        the full validated state) without the schema having to enumerate
        every Config field.
        """
        # Build a dict from every public attribute on the Config instance.
        payload = {}
        for key in dir(config):
            if key.startswith("_") or callable(getattr(type(config), key, None)):
                continue
            payload[key] = getattr(config, key)
        return cls.model_validate(payload, context=context)


def punch_key(message: str) -> str:
    """The setting a message is about: its first ALL_CAPS token."""
    match = _KEY_TOKEN.search(message)
    return match.group(0) if match else "CONFIG"


def _apply_punch_state(config: Any, punch: PunchList, effective: Dict[str, Any]) -> None:
    """Hand boot's verdict back to the config object the runtime reads.

    Defensive because `validate_config` is also called with stand-ins
    (SimpleNamespace in the schema tests, the doctor's duck-typed objects).
    """
    for name, value in (("punch_list", punch), *effective.items()):
        try:
            setattr(config, name, value)
        except Exception:  # pragma: no cover - a read-only stand-in
            logger.debug("could not record %s on the config object", name)


def validate_config(config: Any) -> bool:
    """Validate `config` against the Pydantic schema.

    On failure: log every reported error in the same format the legacy
    `Config.validate()` body used (one logging.error per violation, plus
    the field name), then return False. On success: return True.

    Side-effect parity with the legacy method is the point — callers
    (launcher_runtime.py, launcher_support.py) keep working.

    Week 1 task 1 adds one more side effect: `config.punch_list` — a fresh
    PunchList every call — carrying the numbered hard items and the soft ones
    the validators turned off, plus the `effective_*` fields the runtime reads
    instead of guessing at a keyless feature.
    """
    punch = PunchList()
    collector: Dict[str, Any] = {}
    ok = True
    try:
        CoreConfigSchema.from_config(config, context=collector)
    except ValidationError as exc:
        ok = False
        for err in exc.errors():
            field = ".".join(str(p) for p in err.get("loc", ())) or "<unknown>"
            msg = err.get("msg", "")
            # `model_validator` errors are reported under loc=() by
            # Pydantic; in that case the message itself names the field.
            if field == "<unknown>" or field == "":
                logger.error("%s", msg)
            else:
                logger.error("%s: %s", field, msg)
            # The message is the sentence the operator needs, kept verbatim
            # apart from Pydantic's own "Value error, " prefix.
            why = str(msg).removeprefix("Value error, ")
            key = field if field not in ("", "<unknown>") else punch_key(why)
            punch.add(key, why, where_for(key), hard=True)
    for blocker in collector.get("blockers", []):
        logger.error("%s", blocker.why)
        punch.add(blocker.key, blocker.why, blocker.where, hard=True)
        ok = False
    for warning in collector.get("warnings", []):
        punch.add(warning.key, warning.why, warning.where, hard=False)
    _apply_punch_state(config, punch, dict(collector.get("effective", {})))
    return ok
