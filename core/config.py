"""
Updated configuration for unified architecture
"""

import math
import os
import logging
import shutil
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, List, Optional, Tuple
from urllib.parse import urlparse
from dotenv import load_dotenv

if TYPE_CHECKING:  # the annotation on Config.punch_list; no import at runtime,
    # so nothing changes about what core.config drags in at boot.
    from core.punch_list import PunchList

# Load environment variables from repo-local files. override=True so .env
# beats stale shell exports on live runs — EXCEPT under the test sentinel:
# the suite (tests/conftest.py) exports PALLI_TEST_DATA_ROOT plus redirected
# DATABASE_PATH / transcript roots, and any SUBPROCESS a test-driven turn
# spawns inherits those. Without the sentinel, this override silently
# re-pointed such subprocesses at the owner's LIVE database (2026-07-09:
# three suite runs each scheduled a real "Call the co-op" reminder through
# a spawned child that no in-process guard could see). Live behavior is
# identical when the sentinel is unset.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_ENV_OVERRIDE = not bool(os.getenv("PALLI_TEST_DATA_ROOT"))
load_dotenv(_PROJECT_ROOT / ".env", override=_ENV_OVERRIDE)
load_dotenv(_PROJECT_ROOT / ".env.local", override=_ENV_OVERRIDE)


def _env_float_or_none(name: str, default: float) -> float | None:
    """A number setting where BLANK means something of its own: "leave it to
    the provider". Unset -> ``default``; set but blank -> ``None`` (the caller
    then leaves the keyword out of the request, never sends 0.0); anything
    that is not a finite number of 0 or more -> ``default`` with a warning, so
    a typo neither stops her booting nor quietly changes how she talks (a
    -0.9 temperature would 400 every reply: the outage line on every turn).
    No upper bound: what a provider accepts above 1 is the provider's call.
    Defined up here because the class body below cannot see functions
    defined after it.
    """
    raw = os.getenv(name)
    if raw is None:
        return default
    if not raw.strip():
        return None
    try:
        value = float(raw)
    except ValueError:
        value = math.nan
    if not math.isfinite(value) or value < 0:
        logging.getLogger(__name__).warning(
            "%s is not a number of 0 or more; using the default %s", name, default,
        )
        return default
    return value


class Config:
    """Unified configuration for PaLLi"""

    # Environment
    ENVIRONMENT: str = os.getenv("ENVIRONMENT", "development")
    PALLI_DEPLOYMENT_MODE: str = os.getenv("PALLI_DEPLOYMENT_MODE", "local").lower()
    ENABLE_LEGACY_HOSTED_ACCESS: bool = (
        os.getenv("ENABLE_LEGACY_HOSTED_ACCESS", "false").lower() == "true"
    )
    LOCAL_DASHBOARD_NO_AUTH: bool = os.getenv("LOCAL_DASHBOARD_NO_AUTH", "true").lower() == "true"
    LOCAL_OWNER_EMAIL: str = os.getenv("LOCAL_OWNER_EMAIL", "local@palli.local")
    LOCAL_OWNER_DISPLAY_NAME: str = os.getenv("LOCAL_OWNER_DISPLAY_NAME", "Local Operator")
    # The pronouns she uses for her person until she learns theirs at first
    # contact (week 1, task 4). The profile's `pronouns` field wins once it is
    # written; this is the read-time fallback. "" reads as unset: a real
    # checkout's .env.local fills any var a test leaves blank.
    LOCAL_OWNER_PRONOUNS: str = (os.getenv("LOCAL_OWNER_PRONOUNS") or "").strip() or "they/them"
    # Operator's Discord user_id (an 18-19 digit number: Discord's Developer Mode, then Copy User ID). Empty string =
    # unset; ensure_local_owner() leaves users.discord_user_id as NULL.
    # When set, ensure_local_owner() populates the column on first run for
    # users where it's currently NULL — never overwriting an existing value.
    LOCAL_OWNER_DISCORD_USER_ID: str = os.getenv("LOCAL_OWNER_DISCORD_USER_ID", "")
    # Operator's Telegram user_id (a plain integer, e.g. 424242424). Same
    # contract as the Discord id above: empty = unset (users.telegram_user_id
    # stays NULL and everyone she meets on Telegram is a guest); when set,
    # ensure_local_owner() populates the column on first run for users where
    # it is currently NULL — never overwriting an existing value.
    LOCAL_OWNER_TELEGRAM_USER_ID: str = os.getenv("LOCAL_OWNER_TELEGRAM_USER_ID", "")

    # LLM Configuration
    LLM_PROVIDER: str = os.getenv("LLM_PROVIDER", "deepseek")

    # OpenAI Configuration
    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
    OPENAI_MODEL: str = os.getenv("OPENAI_MODEL", "gpt-4o")

    # Official local Codex client bridge (supports ChatGPT login through Codex CLI)
    CODEX_CLI_PATH: str = os.getenv("CODEX_CLI_PATH", "codex").strip() or "codex"
    # 2026-05-13: default to gpt-5.5 (works on ChatGPT-auth Codex CLI accounts).
    # gpt-5.1-codex-max requires an API-key Codex account; ChatGPT accounts
    # get rejected with "model not supported" — see Codex CLI error responses.
    CODEX_MODEL: str = os.getenv("CODEX_MODEL", "gpt-5.5").strip() or "gpt-5.5"
    CODEX_PROFILE: str = os.getenv("CODEX_PROFILE", "").strip()
    CODEX_WORKDIR: str = os.getenv("CODEX_WORKDIR", "").strip()
    CODEX_TIMEOUT_SECONDS: float = float(os.getenv("CODEX_TIMEOUT_SECONDS", "120"))

    # DeepSeek Configuration
    # 2026-05-13: DeepSeek moved to V4 family. Per /models API response:
    #   - deepseek-v4-pro    (higher capability, reasoning-heavy)
    #   - deepseek-v4-flash  (faster, cheaper, still reasoning-prone)
    # Both are reasoning-prone — keep max_tokens generous (>=200) or the
    # model burns its entire budget on internal reasoning and returns ''.
    DEEPSEEK_API_KEY: str = os.getenv("DEEPSEEK_API_KEY", "")
    DEEPSEEK_MODEL: str = os.getenv("DEEPSEEK_MODEL", "deepseek-v4-pro")
    # Below this, the preflight and the doctor say the account is running dry
    # (week 1 task 1b). The balance hit zero twice in one week without a word:
    # every reply failed and nothing anywhere said why.
    LLM_LOW_BALANCE_WARN: float = float(os.getenv("LLM_LOW_BALANCE_WARN", "2.0"))

    # Anthropic Configuration (HTTP API, distinct from the CLI-based
    # claude_cli provider). Used by ai.llm_providers.AnthropicProvider.
    ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")
    ANTHROPIC_MODEL: str = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6")
    # 2026-09-04 (configurator task 3 review): the Claude CLI's own model key. Three readers
    # already did getattr(config, "CLAUDE_CLI_MODEL", …) against an attribute this class never
    # declared, so every one of them was inert. Default "" on purpose: the provider keeps its own
    # "haiku" fallback and the router keeps the caller's fallback when the env var is unset.
    CLAUDE_CLI_MODEL: str = os.getenv("CLAUDE_CLI_MODEL", "")
    ANTHROPIC_BASE_URL: str = os.getenv("ANTHROPIC_BASE_URL", "https://api.anthropic.com")

    # ReAct agent loop configuration (added by react-agent-loop branch).
    # PALLI_AGENT_LOOP is deliberately absent: it lived here defaulting to
    # "pipeline" while the only gate that anything calls —
    # core/agent/dispatch.py::_resolve_loop_mode — reads os.environ at call
    # time and defaults to "react". Nothing read this attribute, so the two
    # were never forced to agree; all it did was mislead greps into thinking
    # the dormant pipeline was the default engine. Deleted 2026-07-16;
    # os.environ is the single source. Don't re-add a second one.
    PALLI_DEBOUNCE_MS: int = int(os.getenv("PALLI_DEBOUNCE_MS", "3000"))
    TOOL_RESULT_MAX_BYTES: int = int(os.getenv("TOOL_RESULT_MAX_BYTES", "10240"))
    MAX_REACT_ITERATIONS: int = int(os.getenv("MAX_REACT_ITERATIONS", "50"))
    # D.4 step 2: override the model used for the cadence-shaping pass
    # in the ReAct agent loop. The cadence pass is a small text-shaping
    # step that typically does not need the full-size main runner model
    # — a cheaper/faster model usually suffices. Empty string (default)
    # means "use the provider's default model" (preserves pre-D.4
    # behavior). See core/agent/dispatch.py::_build_cadence_llm_callable
    # for the consumer.
    CADENCE_MODEL: str = os.getenv("PALLI_CADENCE_MODEL", "") or os.getenv("CADENCE_MODEL", "")

    # Optional local OpenAI-compatible LLM backend
    LOCAL_LLM_API_BASE: str = os.getenv("LOCAL_LLM_API_BASE", "http://127.0.0.1:11434/v1")
    LOCAL_LLM_API_KEY: str = os.getenv("LOCAL_LLM_API_KEY", "")
    LOCAL_LLM_MODEL: str = os.getenv("LOCAL_LLM_MODEL", "qwen3:14b")
    # 2026-09-02: per-request reasoning control on Ollama's OpenAI-style endpoint
    # ("none" | "low" | "medium" | "high"; empty = don't send). A Qwen brain makes
    # real tool calls on this path only with thinking off.
    LOCAL_LLM_REASONING_EFFORT: str = os.getenv("LOCAL_LLM_REASONING_EFFORT", "").strip().lower()

    # Native Ollama backend (ai/llm_ollama.py — /api/chat endpoint).
    # Distinct from LOCAL_LLM_API_BASE because the native provider uses
    # /api/* (not /v1/*) so it can pass think:false, format:json,
    # keep_alive:-1 — flags the OpenAI-compat layer can't express.
    OLLAMA_BASE_URL: str = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
    OLLAMA_MODEL: str = os.getenv("OLLAMA_MODEL", "qwen3:14b")
    OLLAMA_TIMEOUT_SECONDS: float = float(os.getenv("OLLAMA_TIMEOUT_SECONDS", "180"))

    # Per-task model routing (model name determines provider)
    LLM_RESPONSE_PROVIDER: str = os.getenv("LLM_RESPONSE_PROVIDER", "").strip().lower()
    LLM_ANALYSIS_PROVIDER: str = os.getenv("LLM_ANALYSIS_PROVIDER", "").strip().lower()
    LLM_MEMORY_PROVIDER: str = os.getenv("LLM_MEMORY_PROVIDER", "").strip().lower()
    LLM_DECISION_PROVIDER: str = os.getenv("LLM_DECISION_PROVIDER", "").strip().lower()
    LLM_PROACTIVE_PROVIDER: str = os.getenv("LLM_PROACTIVE_PROVIDER", "").strip().lower()
    LLM_REFLECTION_PROVIDER: str = os.getenv("LLM_REFLECTION_PROVIDER", "").strip().lower()
    LLM_WEB_SEARCH_PROVIDER: str = os.getenv("LLM_WEB_SEARCH_PROVIDER", "").strip().lower()
    LLM_VISION_PROVIDER: str = os.getenv("LLM_VISION_PROVIDER", "").strip().lower()
    LLM_APPRAISAL_PROVIDER: str = os.getenv("LLM_APPRAISAL_PROVIDER", "").strip().lower()
    LLM_RESPONSE_MODEL: str = os.getenv("LLM_RESPONSE_MODEL", OPENAI_MODEL)
    # Her reply temperature (Batch 4 task 6, owner call 1): 0.9 was hard-coded
    # in the reply call; it is his choice, so it is a setting now. Blank = the
    # provider's own default (the keyword is left out, never sent as 0.0).
    LLM_REPLY_TEMPERATURE: float | None = _env_float_or_none("LLM_REPLY_TEMPERATURE", 0.9)
    LLM_ANALYSIS_MODEL: str = os.getenv("LLM_ANALYSIS_MODEL", OPENAI_MODEL)
    LLM_MEMORY_MODEL: str = os.getenv("LLM_MEMORY_MODEL", LLM_ANALYSIS_MODEL)
    LLM_DECISION_MODEL: str = os.getenv("LLM_DECISION_MODEL", LLM_ANALYSIS_MODEL)
    LLM_PROACTIVE_MODEL: str = os.getenv("LLM_PROACTIVE_MODEL", LLM_RESPONSE_MODEL)
    LLM_REFLECTION_MODEL: str = os.getenv("LLM_REFLECTION_MODEL", LLM_MEMORY_MODEL)
    LLM_WEB_SEARCH_MODEL: str = os.getenv("LLM_WEB_SEARCH_MODEL", LLM_ANALYSIS_MODEL)
    LLM_VISION_MODEL: str = os.getenv("LLM_VISION_MODEL", "gpt-4o")
    # Stake appraiser (2026-07-06) — env fallback only; palli.yaml
    # llm.routing.appraisal is the primary source of truth.
    LLM_APPRAISAL_MODEL: str = os.getenv("LLM_APPRAISAL_MODEL", LLM_ANALYSIS_MODEL)
    # Present Moment appraisal lane (2026-07-24): falls back to the
    # reflection lane's model — same "background self-work" class.
    LLM_PRESENT_MOMENT_MODEL: str = os.getenv("LLM_PRESENT_MOMENT_MODEL", LLM_REFLECTION_MODEL)

    # Platform Tokens
    DISCORD_TOKEN: str = os.getenv("DISCORD_TOKEN", "")
    # Telegram door (2026-09-14, task 2): the token BotFather issues.
    TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    DISCORD_PRIMARY_GUILD_ID: Optional[int] = int(os.getenv("DISCORD_PRIMARY_GUILD_ID")) if os.getenv("DISCORD_PRIMARY_GUILD_ID") else None
    DISCORD_FOUNDING_MEMBER_ROLE_ID: Optional[int] = int(os.getenv("DISCORD_FOUNDING_MEMBER_ROLE_ID")) if os.getenv("DISCORD_FOUNDING_MEMBER_ROLE_ID") else None
    DISCORD_DEDUP_TTL_SECONDS: int = int(os.getenv("DISCORD_DEDUP_TTL_SECONDS", "600"))
    DISCORD_DEDUP_MAX_SIZE: int = int(os.getenv("DISCORD_DEDUP_MAX_SIZE", "20000"))
    # Guest budget (stranger-proofing week 1, 2026-09-08; Batch 8, task 2b).
    # The owner gate decided WHAT a guest gets; this decides how many guest
    # turns run at once, GLOBAL across all guests, because the bill is global.
    # Since Batch 8 it is a queue: a guest turn over it waits for a free slot
    # (no canned line), and the per-guest rate limit is gone on the owner's
    # word (09-30: "Drop the limit."). Floored at 1 where it is read.
    GUEST_MAX_IN_FLIGHT: int = int(os.getenv("GUEST_MAX_IN_FLIGHT") or "2")
    DEEPGRAM_API_KEY: str = os.getenv("DEEPGRAM_API_KEY", "")
    EXA_API_KEY: str = os.getenv("EXA_API_KEY", "")


    # Backend Configuration
    BACKEND_URL: str = os.getenv("BACKEND_URL", "http://localhost:8000")
    # Loopback by default (stranger-proofing week 1, 2026-09-08). 0.0.0.0 put
    # the no-login dashboard AND the chat endpoint — which can reach
    # local_shell — on every network the host sits on, with only the CIDR gate
    # below in the way. A knob is not a default: set 0.0.0.0 deliberately for a
    # LAN/tailnet dashboard. "" reads as unset, not as "bind everything".
    BACKEND_HOST: str = os.getenv("BACKEND_HOST") or "127.0.0.1"
    # Who may reach the backend at all (item 7b, 2026-08-19): comma-separated
    # CIDRs checked against the socket peer by core.trusted_networks. Default
    # = loopback + Tailscale's CGNAT range; "" = open (the old LAN-open
    # behavior, explicit). Second line, not the first: since 2026-09-08 the
    # bind is loopback, so this gate only matters once BACKEND_HOST is widened.
    # A list that fails to parse LOCKS the door, it never opens it: a typo
    # refuses every peer and boot says which token is not a CIDR (week 2, task
    # 4) — only a blank setting runs open.
    BACKEND_ALLOWED_NETWORKS: str = os.getenv(
        "BACKEND_ALLOWED_NETWORKS", "127.0.0.0/8,::1/128,100.64.0.0/10"
    )
    # Which NAMES a request may be addressed to (2026-10-08, against DNS
    # rebinding — core.trusted_hosts): localhost and any IP address always
    # pass; a name such as a tailnet or LAN hostname passes only when listed
    # here (comma- or space-separated). "" = no extra names, never "any name".
    BACKEND_ALLOWED_HOSTS: str = os.getenv("BACKEND_ALLOWED_HOSTS", "") or ""
    BACKEND_PORT: int = int(os.getenv("BACKEND_PORT", "8000"))

    # Web frontend / local dashboard
    FRONTEND_URL: str = os.getenv("FRONTEND_URL", "http://localhost:3000")

    # Frontend / CORS
    FRONTEND_ORIGINS: List[str]
    FRONTEND_ORIGIN_REGEX: Optional[str]
    ALLOW_INSECURE_CORS: bool = os.getenv("ALLOW_INSECURE_CORS", "false").lower() == "true"
    # 2026-06-11: enforce CSRF even in local-auth-bypass mode. Bypass mode has
    # NO authentication, so the double-submit cookie is the only thing standing
    # between a malicious webpage (opened on any LAN/tailnet device) and
    # state-changing endpoints — including chat, which can reach local_shell.
    # Emergency hatch only; leave true.
    CSRF_PROTECT_LOCAL: bool = os.getenv("CSRF_PROTECT_LOCAL", "true").lower() == "true"
    # 2026-06-11: refuse obviously destructive shell commands in the agent's
    # local_shell tool (rm -rf on roots, mkfs, dd to block devices, etc.).
    LOCAL_SHELL_GUARD_ENABLED: bool = os.getenv("LOCAL_SHELL_GUARD_ENABLED", "true").lower() == "true"
    # Stranger-proofing week 1 (2026-09-08): the ceiling local_shell clamps the
    # model's timeout_seconds into. Unbounded before, so one command could hold
    # a turn open indefinitely.
    LOCAL_SHELL_MAX_TIMEOUT_SECONDS: int = int(
        os.getenv("LOCAL_SHELL_MAX_TIMEOUT_SECONDS") or "300"
    )
    # Extra roots read_file/deliver_artifact may reach, colon-separated. The
    # repo, her workbench and the inbox are always in; anything else is a
    # deliberate act (core/agent/builtin_tools/roots.py).
    FILE_TOOL_EXTRA_ROOTS: str = os.getenv("FILE_TOOL_EXTRA_ROOTS", "")

    # Database Configuration (SQLite)
    DATABASE_PATH: str = os.getenv("DATABASE_PATH", "data/palli-local.sqlite3")
    DATABASE_URL: str = os.getenv("DATABASE_URL", "")

    # Web auth (cookie-based JWT)
    JWT_SECRET: str = os.getenv("JWT_SECRET", "")
    SESSION_COOKIE_NAME: str = os.getenv("SESSION_COOKIE_NAME", "palli_session")
    CSRF_COOKIE_NAME: str = os.getenv("CSRF_COOKIE_NAME", "palli_csrf")
    SESSION_COOKIE_SECURE: bool = os.getenv("SESSION_COOKIE_SECURE", "false").lower() == "true"
    SESSION_COOKIE_MAX_AGE_SECONDS: int = int(os.getenv("SESSION_COOKIE_MAX_AGE_SECONDS", "1209600"))  # 14 days

    # The user agent read_page sends when it fetches a web page (Batch 8,
    # task 9). It was RESEND_USER_AGENT, a Resend-era name nothing read any
    # more (ai/url_browsing.py, its last reader, is long gone).
    READ_PAGE_USER_AGENT: str = os.getenv(
        "READ_PAGE_USER_AGENT",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36 PaLLi/1.0 (+https://trypalli.com)",
    )

    # Legal / consent versions (store a date string on acceptance for auditability)
    TOS_VERSION: str = os.getenv("TOS_VERSION", "2026-01-31")
    PRIVACY_VERSION: str = os.getenv("PRIVACY_VERSION", "2026-01-31")

    # Legacy hosted billing settings (kept dormant for the local product line
    # unless legacy hosted access is explicitly re-enabled)
    STRIPE_SECRET_KEY: str = os.getenv("STRIPE_SECRET_KEY", "")
    STRIPE_WEBHOOK_SECRET: str = os.getenv("STRIPE_WEBHOOK_SECRET", "")

    # Google OAuth (optional)
    GOOGLE_CLIENT_ID: str = os.getenv("GOOGLE_CLIENT_ID", "")
    GOOGLE_CLIENT_SECRET: str = os.getenv("GOOGLE_CLIENT_SECRET", "")
    GOOGLE_OAUTH_REDIRECT_URL: str = os.getenv("GOOGLE_OAUTH_REDIRECT_URL", "")

    # Discord OAuth (for account linking)
    DISCORD_CLIENT_ID: str = os.getenv("DISCORD_CLIENT_ID", "")
    DISCORD_CLIENT_SECRET: str = os.getenv("DISCORD_CLIENT_SECRET", "")
    DISCORD_OAUTH_REDIRECT_URL: str = os.getenv("DISCORD_OAUTH_REDIRECT_URL", "")

    # Legacy hosted access limits
    BETA_SEAT_CAPACITY: int = int(os.getenv("BETA_SEAT_CAPACITY", "100"))
    
    # Legacy hosted pricing configuration
    FOUNDING_MEMBER_INITIATION_FEE_CENTS: int = int(os.getenv("FOUNDING_MEMBER_INITIATION_FEE_CENTS", "0"))
    FOUNDING_MEMBER_MONTHLY_RATE_CENTS: int = int(os.getenv("FOUNDING_MEMBER_MONTHLY_RATE_CENTS", "1900"))  # $19/mo
    STANDARD_MONTHLY_RATE_CENTS: int = int(os.getenv("STANDARD_MONTHLY_RATE_CENTS", "2900"))

    # Message Processing Configuration
    MESSAGE_PROCESSING_DELAY: float = float(os.getenv("MESSAGE_PROCESSING_DELAY", "5.0"))

    # Typing timeout (seconds) - how long to wait for typing indicators to expire
    TYPING_TIMEOUT: float = float(os.getenv("TYPING_TIMEOUT", "10.0"))

    # Feature Flags
    # On by default, but "on" is not "configured": a blank DISCORD_TOKEN
    # degrades Discord to off for that boot with a line saying so, rather than
    # refusing to start (week 2 task 7; owner ruling 2026-09-10 — the dashboard
    # is a stranger's first door). The runtime reads effective_discord_enabled,
    # below, never this flag on its own.
    ENABLE_DISCORD_BOT: bool = os.getenv("ENABLE_DISCORD_BOT", "true").lower() == "true"

    # Telegram door (2026-09-14, task 2). OFF by default, unlike Discord's:
    # an existing install that has never heard of Telegram must behave
    # exactly as it did, and a door nobody asked for should not appear on a
    # stranger's punch list either. On without a token degrades to off for
    # the boot with a line saying so — week 2 task 7's ruling, on the second
    # door. The runtime reads effective_telegram_enabled, never this flag.
    ENABLE_TELEGRAM_BOT: bool = os.getenv("ENABLE_TELEGRAM_BOT", "false").lower() == "true"
    # The mobile door (2026-10-13). OFF by default, like Telegram's: an install
    # that never asked for a phone app grows nothing new. It has no token —
    # phones pair by code (the door page, or `palli_ctl phone pair`).
    ENABLE_MOBILE_DOOR: bool = os.getenv("ENABLE_MOBILE_DOOR", "false").lower() == "true"
    # Where a phone reaches this backend, put in the pairing QR — e.g.
    # http://<host>.<tailnet>.ts.net:8000. Blank: pairing asks for it by hand.
    MOBILE_PUBLIC_URL: str = os.getenv("MOBILE_PUBLIC_URL", "") or ""

    ENABLE_VOICE_FEATURES: bool = os.getenv("ENABLE_VOICE_FEATURES", "true").lower() == "true"
    # E.5 Wave 4 step 3 (2026-05-13): web_search moved to palli.yaml
    # features.web_search. Read via settings_service.snapshot().features.
    ENABLE_IDENTITY_ARCHITECTURE: bool = os.getenv("ENABLE_IDENTITY_ARCHITECTURE", "true").lower() == "true"
    IDENTITY_ROOT_PATH: str = os.getenv("IDENTITY_ROOT_PATH", "identity")
    IDENTITY_ENABLE_DASHBOARD_EDITING: bool = os.getenv("IDENTITY_ENABLE_DASHBOARD_EDITING", "true").lower() == "true"
    IDENTITY_ENABLE_CHAT_EDITING: bool = os.getenv("IDENTITY_ENABLE_CHAT_EDITING", "true").lower() == "true"
    IDENTITY_ENABLE_SILENT_SELF_MAINTENANCE: bool = (
        os.getenv("IDENTITY_ENABLE_SILENT_SELF_MAINTENANCE", "true").lower() == "true"
    )
    IDENTITY_ENABLE_PRIVILEGED_AUTONOMY: bool = (
        os.getenv("IDENTITY_ENABLE_PRIVILEGED_AUTONOMY", "false").lower() == "true"
    )
    IDENTITY_CHAT_ANALYSIS_COOLDOWN_SECONDS: int = int(
        os.getenv("IDENTITY_CHAT_ANALYSIS_COOLDOWN_SECONDS", "900")
    )
    ENABLE_BOUNDED_REFLECTION: bool = os.getenv("ENABLE_BOUNDED_REFLECTION", "true").lower() == "true"
    # 2026-05-13 — E.5 Wave 4 step 2 (DB-HIGH-1 closure): the cognitive
    # ENABLE_* flags (interaction_mode_router, pragmatic_perception,
    # emotional_state, topic_boundaries, relationship_calibration,
    # delivery_planner) used to be PALLI_ENABLE_* env reads here that
    # defaulted False — but data/palli.yaml already declared them True
    # AND the perception/calibration modules in ai/* run unconditionally,
    # so the env-flag layer was both dead and silently inverted. Wave 4
    # made settings.features.<flag> the canonical source; the 2026-07-16
    # inert-flag cut then deleted those fields as well, the Great Excision
    # (2026-07-09) having already removed the faculties they gated. There
    # is no layer left to read — don't re-add one. NB: the live
    # ENABLE_BOUNDED_REFLECTION above is a different flag that merely
    # shares a name; it feeds core/identity/compiler.py's permissions.
    BOUNDED_REFLECTION_POST_TURN_COOLDOWN_SECONDS: int = int(
        os.getenv("BOUNDED_REFLECTION_POST_TURN_COOLDOWN_SECONDS", "21600")
    )
    BOUNDED_REFLECTION_PERIODIC_INTERVAL_MINUTES: int = int(
        os.getenv("BOUNDED_REFLECTION_PERIODIC_INTERVAL_MINUTES", "360")
    )
    BOUNDED_REFLECTION_ACTIVITY_LOOKBACK_HOURS: int = int(
        os.getenv("BOUNDED_REFLECTION_ACTIVITY_LOOKBACK_HOURS", "72")
    )

    # Inner monologue situation context - "stalled" threshold for active
    # operator workflows. If an active workflow has not been updated within
    # this many hours, the situation context flags it as stalled.
    MONOLOGUE_STALL_THRESHOLD_HOURS: int = int(
        os.getenv("MONOLOGUE_STALL_THRESHOLD_HOURS", "24")
    )

    # Stage 1 delegation bridge settings (PaLLi → Claude Code)
    DELEGATION_ENABLED: bool = os.getenv("DELEGATION_ENABLED", "false").lower() == "true"
    MAX_CONCURRENT_DELEGATIONS_PER_USER: int = int(
        os.getenv("MAX_CONCURRENT_DELEGATIONS_PER_USER", "3")
    )
    DELEGATION_IDLE_TIMEOUT_SECONDS: int = int(
        os.getenv("DELEGATION_IDLE_TIMEOUT_SECONDS", "900")
    )
    DELEGATION_HARD_TIMEOUT_SECONDS: int = int(
        os.getenv("DELEGATION_HARD_TIMEOUT_SECONDS", "1800")
    )
    DELEGATION_PROGRESS_MIN_INTERVAL_SECONDS: int = int(
        os.getenv("DELEGATION_PROGRESS_MIN_INTERVAL_SECONDS", "120")
    )
    CLAUDE_CODE_WORKSPACE_DIR: str = os.getenv(
        "CLAUDE_CODE_WORKSPACE_DIR",
        str(Path(__file__).resolve().parent.parent),
    )
    # Permission mode passed to ClaudeAgentOptions. Options:
    #   "default"           — every tool call prompts for approval
    #                         (unusable for async delegation; no UI to approve)
    #   "acceptEdits"       — auto-accepts Edit/Write tools only, blocks
    #                         browser/bash/MCP tools
    #   "bypassPermissions" — auto-accepts everything (matches the design
    #                         spec's "full machine access, same as
    #                         dev-mode usage" intent for Stage 2 pilot)
    # For the Stage 2 pilot we default to bypassPermissions so Claude Code
    # can actually compose browser + shell + MCP tools the way the
    # delegation spec envisions. Override via env var in constrained
    # environments where approval-gated delegation is preferred.
    DELEGATION_PERMISSION_MODE: str = os.getenv(
        "DELEGATION_PERMISSION_MODE", "bypassPermissions"
    )
    # Safety cap on Claude Code turns per delegation session. Protects
    # against an infinite tool loop (e.g. make → test → edit → make →
    # test → edit with no progress) burning through Claude Max weekly
    # quota on junk work. Legitimate full-app builds rarely exceed ~200
    # turns; 500 gives comfortable headroom while still bounding a
    # runaway agent. Passed to ClaudeAgentOptions.max_turns in
    # ai/delegation/bridge.py. Set to 0 or a very large number to
    # effectively disable the cap.
    DELEGATION_MAX_TURNS: int = int(
        os.getenv("DELEGATION_MAX_TURNS", "300")
    )
    # 2026-04-23 interactive delegation: when Claude pauses mid-task with
    # `status=needs_input`, PaLLi can auto-answer via its local LLM and
    # send a follow-up prompt to unstick the session. This caps how many
    # consecutive PaLLi-auto-reply turns we allow before forcing a
    # session-level escalation (either to the user or as a failure).
    # Each iteration here costs one combined_decision-equivalent LLM
    # call + one extra Claude turn, so keep it modest.
    DELEGATION_MAX_FOLLOWUPS: int = int(
        os.getenv("DELEGATION_MAX_FOLLOWUPS", "5")
    )

    # ─── Phase 3 Sentiment Engine settings ────────────────────────
    # How many hours after a sentiment reading's updated_at until we
    # treat it as stale. Stale readings still preserve their tag for
    # transparency but consumers see confidence forced to 0.0 and
    # is_stale=True. Covers the "left PaLLi idle overnight, don't let
    # yesterday's frustrated reading color this morning's reply" case.
    SENTIMENT_STALENESS_HOURS: int = int(
        os.getenv("SENTIMENT_STALENESS_HOURS", "6")
    )

    # 2026-05-12 — Open-Threads Agenda (T17): the legacy ENABLE_COMPANION_AGENDA
    # + COMPANION_AGENDA_* knob block, and the ENABLE_OPEN_THREADS_AGENDA cutover
    # flag, were all deleted. The agenda is now unconditional.
    # 2026-05-13 — E.5 Wave 4 step 3: the OPEN_THREADS_* tuning knobs
    # moved to data/palli.yaml `features.open_threads_*`. Consumers read
    # them via settings_service.snapshot().features.<knob>.
    MEMORY_WORKSPACE_ENABLED: bool = os.getenv("MEMORY_WORKSPACE_ENABLED", "true").lower() == "true"
    MEMORY_INDEX_ENABLED: bool = os.getenv("MEMORY_INDEX_ENABLED", "true").lower() == "true"
    MEMORY_LOCAL_EMBEDDINGS_ENABLED: bool = os.getenv("MEMORY_LOCAL_EMBEDDINGS_ENABLED", "true").lower() == "true"
    MEMORY_LOCAL_EMBEDDINGS_MODEL: str = os.getenv(
        "MEMORY_LOCAL_EMBEDDINGS_MODEL",
        "all-MiniLM-L6-v2",
    )
    MEMORY_REMOTE_EMBEDDINGS_MODEL: str = os.getenv(
        "MEMORY_REMOTE_EMBEDDINGS_MODEL",
        "text-embedding-3-small",
    )
    MEMORY_SEARCH_TOP_K: int = int(os.getenv("MEMORY_SEARCH_TOP_K", "8"))
    MEMORY_SEARCH_MAX_CONTEXT_ITEMS: int = int(os.getenv("MEMORY_SEARCH_MAX_CONTEXT_ITEMS", "4"))
    PLUGIN_RUNTIME_ENABLED: bool = os.getenv("PLUGIN_RUNTIME_ENABLED", "true").lower() == "true"
    PLUGIN_DIRECTORIES: List[str]
    PLUGIN_API_VERSION: str = os.getenv("PLUGIN_API_VERSION", "1")
    PLUGIN_DISCOVER_ON_START: bool = os.getenv("PLUGIN_DISCOVER_ON_START", "true").lower() == "true"
    RUNTIME_MAX_RECOVERY_RESUME_ATTEMPTS: int = int(
        os.getenv("RUNTIME_MAX_RECOVERY_RESUME_ATTEMPTS", "1")
    )
    # E.5 Wave 4 step 3 (2026-05-13): home_assistant feature flag moved
    # to palli.yaml features.home_assistant. The URL/TOKEN remain in
    # os.getenv per the capped-scope decision (per-environment secrets,
    # not config). Read the enabled flag via
    # settings_service.snapshot().features.home_assistant.
    HOME_ASSISTANT_URL: str = os.getenv("HOME_ASSISTANT_URL", "")
    HOME_ASSISTANT_TOKEN: str = os.getenv("HOME_ASSISTANT_TOKEN", "")
    HOME_ASSISTANT_VERIFY_SSL: bool = os.getenv("HOME_ASSISTANT_VERIFY_SSL", "true").lower() == "true"
    HOME_ASSISTANT_OWNER_ENTITY_IDS: List[str] = [
        item.strip() for item in os.getenv("HOME_ASSISTANT_OWNER_ENTITY_IDS", "").split(",") if item.strip()
    ]
    HOME_ASSISTANT_PRIVATE_ENTITY_IDS: List[str] = [
        item.strip() for item in os.getenv("HOME_ASSISTANT_PRIVATE_ENTITY_IDS", "").split(",") if item.strip()
    ]
    HOME_ASSISTANT_SHARED_ENTITY_IDS: List[str] = [
        item.strip() for item in os.getenv("HOME_ASSISTANT_SHARED_ENTITY_IDS", "").split(",") if item.strip()
    ]
    LOCAL_MACHINE_PRESENCE_ENABLED: bool = (
        os.getenv("LOCAL_MACHINE_PRESENCE_ENABLED", "true").lower() == "true"
    )
    LOCAL_MACHINE_PRESENCE_POLL_SECONDS: float = float(
        os.getenv("LOCAL_MACHINE_PRESENCE_POLL_SECONDS", "10.0")
    )
    HOME_CONTEXT_RECONCILE_SECONDS: float = float(
        os.getenv("HOME_CONTEXT_RECONCILE_SECONDS", "120.0")
    )
    HOME_CONTEXT_EVENT_DEDUP_SECONDS: float = float(
        os.getenv("HOME_CONTEXT_EVENT_DEDUP_SECONDS", "20.0")
    )
    AMBIENT_ARRIVAL_COOLDOWN_SECONDS: float = float(
        os.getenv("AMBIENT_ARRIVAL_COOLDOWN_SECONDS", "1800.0")
    )
    # Suppress arrival greetings when the owner and PaLLi exchanged messages
    # within this window — "welcome home" mid-conversation reads as broken
    # (2026-07-10). 0 disables the check.
    AMBIENT_ARRIVAL_RECENT_CONVERSATION_SECONDS: float = float(
        os.getenv("AMBIENT_ARRIVAL_RECENT_CONVERSATION_SECONDS", "1800.0")
    )
    AMBIENT_MACHINE_UNLOCK_GRACE_SECONDS: float = float(
        os.getenv("AMBIENT_MACHINE_UNLOCK_GRACE_SECONDS", "180.0")
    )
    AMBIENT_ARRIVAL_LOCKED_THRESHOLD_SECONDS: float = float(
        os.getenv("AMBIENT_ARRIVAL_LOCKED_THRESHOLD_SECONDS", "600.0")
    )
    AMBIENT_ARRIVAL_IDLE_THRESHOLD_SECONDS: float = float(
        os.getenv("AMBIENT_ARRIVAL_IDLE_THRESHOLD_SECONDS", "1200.0")
    )
    OPERATOR_BROWSER_ENABLED: bool = (
        os.getenv("OPERATOR_BROWSER_ENABLED", "true").lower() == "true"
    )
    OPERATOR_BROWSER_PROFILE_DIR: str = os.getenv(
        "OPERATOR_BROWSER_PROFILE_DIR",
        "data/operator-browser/profiles",
    )
    OPERATOR_BROWSER_DOWNLOADS_DIR: str = os.getenv(
        "OPERATOR_BROWSER_DOWNLOADS_DIR",
        "data/operator-browser/downloads",
    )
    OPERATOR_BROWSER_HEADLESS: bool = (
        os.getenv("OPERATOR_BROWSER_HEADLESS", "false").lower() == "true"
    )
    OPERATOR_DESKTOP_ENABLED: bool = (
        os.getenv("OPERATOR_DESKTOP_ENABLED", "true").lower() == "true"
    )
    OPERATOR_DESKTOP_ALLOW_INPUT_CONTROL: bool = (
        os.getenv("OPERATOR_DESKTOP_ALLOW_INPUT_CONTROL", "true").lower() == "true"
    )
    OPERATOR_ADAPTER_HEALTH_POLL_SECONDS: float = float(
        os.getenv("OPERATOR_ADAPTER_HEALTH_POLL_SECONDS", "30.0")
    )
    OPERATOR_DESKTOP_VISION_ENABLED: bool = (
        os.getenv("OPERATOR_DESKTOP_VISION_ENABLED", "true").lower() == "true"
    )
    OPERATOR_SCREENSHOT_DIR: str = os.getenv(
        "OPERATOR_SCREENSHOT_DIR",
        "data/operator-desktop/captures",
    )
    OPERATOR_SCREENSHOT_RETENTION_SECONDS: float = float(
        os.getenv("OPERATOR_SCREENSHOT_RETENTION_SECONDS", "1800.0")
    )
    OPERATOR_OCR_ENABLED: bool = (
        os.getenv("OPERATOR_OCR_ENABLED", "true").lower() == "true"
    )
    OPERATOR_VISION_INSPECTION_MAX_AGE_SECONDS: float = float(
        os.getenv("OPERATOR_VISION_INSPECTION_MAX_AGE_SECONDS", "300.0")
    )
    HOST_PLATFORM_MODE: str = os.getenv("HOST_PLATFORM_MODE", "auto").strip().lower() or "auto"
    HOST_CAPABILITY_DIAGNOSTICS_VERBOSE: bool = (
        os.getenv("HOST_CAPABILITY_DIAGNOSTICS_VERBOSE", "false").lower() == "true"
    )
    WINDOWS_NOTIFICATION_BACKEND: str = (
        os.getenv("WINDOWS_NOTIFICATION_BACKEND", "win11toast").strip().lower() or "win11toast"
    )

    # Website ops alerts (internal Discord notifications for funnel events)
    WEB_OPS_ALERTS_ENABLED: bool = os.getenv("WEB_OPS_ALERTS_ENABLED", "true").lower() == "true"
    WEB_OPS_DISCORD_CHANNEL_ID: Optional[int] = (
        int(os.getenv("WEB_OPS_DISCORD_CHANNEL_ID"))
        if os.getenv("WEB_OPS_DISCORD_CHANNEL_ID")
        else None
    )
    WEB_OPS_VISIT_MODE: str = os.getenv("WEB_OPS_VISIT_MODE", "path_window").lower()
    WEB_OPS_VISIT_WINDOW_SECONDS: int = int(os.getenv("WEB_OPS_VISIT_WINDOW_SECONDS", "1800"))
    WEB_OPS_DETAIL_LEVEL: str = os.getenv("WEB_OPS_DETAIL_LEVEL", "full").lower()
    WEB_OPS_INCLUDE_BOT_TRAFFIC: bool = (
        os.getenv("WEB_OPS_INCLUDE_BOT_TRAFFIC", "false").lower() == "true"
    )
    WEB_OPS_EVENT_TYPES: List[str] = []

    # Agentic execution loop (default off for safety)
    # The tool forge (step 34, spec §8): per-call ceiling on a forged tool's
    # execute — a bug-belt (hers as much as his), not a permission. Timeout
    # counts as an error toward the three-strikes auto-disable.
    FORGE_TOOL_TIMEOUT_SECONDS: float = float(
        os.getenv("FORGE_TOOL_TIMEOUT_SECONDS", "30")
    )
    # Forge-session budgets (spec §8): rounds/minutes ceilings on the walled
    # drafting session — code, not prose (manifesto §2.4).
    FORGE_SESSION_MAX_MINUTES: float = float(
        os.getenv("FORGE_SESSION_MAX_MINUTES", "20")
    )
    FORGE_SESSION_MAX_ROUNDS: int = int(os.getenv("FORGE_SESSION_MAX_ROUNDS", "40"))

    AGENTIC_ENABLED: bool = os.getenv("AGENTIC_ENABLED", "false").lower() == "true"
    AGENTIC_MODE: str = os.getenv("AGENTIC_MODE", "hybrid").lower()
    AGENTIC_ROLLOUT_MODE: str = os.getenv("AGENTIC_ROLLOUT_MODE", "enforce").lower()
    AGENTIC_MAX_STEPS: int = int(os.getenv("AGENTIC_MAX_STEPS", "5"))
    AGENTIC_STEP_TIMEOUT_SECONDS: float = float(os.getenv("AGENTIC_STEP_TIMEOUT_SECONDS", "45"))
    AGENTIC_RUN_TIMEOUT_SECONDS: float = float(os.getenv("AGENTIC_RUN_TIMEOUT_SECONDS", "120"))
    AGENTIC_ROUTE_CONFIDENCE_THRESHOLD: float = float(
        os.getenv("AGENTIC_ROUTE_CONFIDENCE_THRESHOLD", "0.55")
    )
    AGENTIC_DM_ONLY: bool = os.getenv("AGENTIC_DM_ONLY", "true").lower() == "true"
    AGENTIC_ALLOW_AUTONOMOUS_CLI: bool = (
        os.getenv("AGENTIC_ALLOW_AUTONOMOUS_CLI", "true").lower() == "true"
    )
    AGENTIC_ALLOW_PACKAGE_INSTALLS: bool = (
        os.getenv("AGENTIC_ALLOW_PACKAGE_INSTALLS", "false").lower() == "true"
    )
    AGENTIC_INSTALL_CONFIRM_REQUIRED: bool = (
        os.getenv("AGENTIC_INSTALL_CONFIRM_REQUIRED", "true").lower() == "true"
    )
    AGENTIC_CONFIRM_REUSE_EXISTING_TOKEN: bool = (
        os.getenv("AGENTIC_CONFIRM_REUSE_EXISTING_TOKEN", "true").lower() == "true"
    )
    AGENTIC_MAX_PENDING_CONFIRMATIONS_PER_USER: int = int(
        os.getenv("AGENTIC_MAX_PENDING_CONFIRMATIONS_PER_USER", "3")
    )
    AGENTIC_CONFIRM_TIMEOUT_SECONDS: int = int(
        os.getenv("AGENTIC_CONFIRM_TIMEOUT_SECONDS", "600")
    )
    AGENTIC_AUTO_RESUME_AFTER_APPROVAL: bool = (
        os.getenv("AGENTIC_AUTO_RESUME_AFTER_APPROVAL", "true").lower() == "true"
    )
    AGENTIC_POST_CONFIRM_MAX_STEPS: int = int(
        os.getenv("AGENTIC_POST_CONFIRM_MAX_STEPS", "3")
    )
    AGENTIC_POST_CONFIRM_TIMEOUT_SECONDS: float = float(
        os.getenv("AGENTIC_POST_CONFIRM_TIMEOUT_SECONDS", "60")
    )
    AGENTIC_MAX_SAME_COMMAND_ATTEMPTS_PER_RUN: int = int(
        os.getenv("AGENTIC_MAX_SAME_COMMAND_ATTEMPTS_PER_RUN", "2")
    )
    AGENTIC_MAX_COMMAND_OUTPUT_CHARS: int = int(
        os.getenv("AGENTIC_MAX_COMMAND_OUTPUT_CHARS", "6000")
    )
    AGENTIC_WORKSPACE_ROOT: str = os.getenv("AGENTIC_WORKSPACE_ROOT", os.getcwd())
    AGENTIC_QUEUE_MODE: str = os.getenv("AGENTIC_QUEUE_MODE", "collect").lower()
    AGENTIC_QUEUE_DEBOUNCE_MS: int = int(os.getenv("AGENTIC_QUEUE_DEBOUNCE_MS", "1000"))
    AGENTIC_QUEUE_CAP: int = int(os.getenv("AGENTIC_QUEUE_CAP", "20"))
    AGENTIC_NL_CONTROL_ENABLED: bool = (
        os.getenv("AGENTIC_NL_CONTROL_ENABLED", "true").lower() == "true"
    )
    AGENTIC_CONTROL_REQUIRE_ACTIVE_WORK: bool = (
        os.getenv("AGENTIC_CONTROL_REQUIRE_ACTIVE_WORK", "true").lower() == "true"
    )
    AGENTIC_CONTROL_CANCEL_SCOPE: str = os.getenv(
        "AGENTIC_CONTROL_CANCEL_SCOPE", "run_and_descendants"
    ).lower()
    AGENTIC_CONTROL_REDIRECT_MODE: str = os.getenv(
        "AGENTIC_CONTROL_REDIRECT_MODE", "replace_goal"
    ).lower()
    AGENTIC_STALE_CONFIRMATION_POLICY: str = os.getenv(
        "AGENTIC_STALE_CONFIRMATION_POLICY", "supersede"
    ).lower()
    AGENTIC_LANE_ROUTING_MODE: str = os.getenv(
        "AGENTIC_LANE_ROUTING_MODE", "auto"
    ).lower()
    AGENTIC_SESSION_IDLE_TTL_SECONDS: int = int(
        os.getenv("AGENTIC_SESSION_IDLE_TTL_SECONDS", "1800")
    )
    AGENTIC_SESSION_ARCHIVE_RETENTION_DAYS: int = int(
        os.getenv("AGENTIC_SESSION_ARCHIVE_RETENTION_DAYS", "14")
    )
    AGENTIC_SESSION_REPLY_BACK_MAX_ROUNDS: int = int(
        os.getenv("AGENTIC_SESSION_REPLY_BACK_MAX_ROUNDS", "1")
    )
    AGENTIC_COMPACTION_TRIGGER_STEPS: int = int(
        os.getenv("AGENTIC_COMPACTION_TRIGGER_STEPS", "6")
    )
    AGENTIC_COMPACTION_RECENT_OBSERVATIONS: int = int(
        os.getenv("AGENTIC_COMPACTION_RECENT_OBSERVATIONS", "4")
    )
    AGENTIC_COMPACTION_RECENT_HISTORY_LINES: int = int(
        os.getenv("AGENTIC_COMPACTION_RECENT_HISTORY_LINES", "8")
    )
    AGENTIC_MODEL_RETRY_MAX_ATTEMPTS: int = int(
        os.getenv("AGENTIC_MODEL_RETRY_MAX_ATTEMPTS", "1")
    )
    AGENTIC_TOOL_RETRY_MAX_ATTEMPTS: int = int(
        os.getenv("AGENTIC_TOOL_RETRY_MAX_ATTEMPTS", "1")
    )
    AGENTIC_MAX_RUN_TOKENS: int = int(os.getenv("AGENTIC_MAX_RUN_TOKENS", "24000"))
    AGENTIC_CHILD_MAX_STEPS: int = int(os.getenv("AGENTIC_CHILD_MAX_STEPS", "4"))
    AGENTIC_CHILD_RUN_TIMEOUT_SECONDS: float = float(
        os.getenv("AGENTIC_CHILD_RUN_TIMEOUT_SECONDS", "90")
    )
    AGENTIC_CHILD_MAX_RUN_TOKENS: int = int(
        os.getenv("AGENTIC_CHILD_MAX_RUN_TOKENS", "8000")
    )
    AGENTIC_ROOT_MAX_CHILD_RUNS: int = int(os.getenv("AGENTIC_ROOT_MAX_CHILD_RUNS", "6"))
    AGENTIC_ROOT_MAX_CHILD_TOTAL_SECONDS: float = float(
        os.getenv("AGENTIC_ROOT_MAX_CHILD_TOTAL_SECONDS", "240")
    )
    AGENTIC_ROOT_MAX_CHILD_TOTAL_TOKENS: int = int(
        os.getenv("AGENTIC_ROOT_MAX_CHILD_TOTAL_TOKENS", "32000")
    )
    AGENTIC_BUDGET_WARN_RATIO: float = float(os.getenv("AGENTIC_BUDGET_WARN_RATIO", "0.8"))
    AGENTIC_STREAMING_ENABLED: bool = os.getenv("AGENTIC_STREAMING_ENABLED", "true").lower() == "true"
    AGENTIC_STREAMING_BREAK: str = os.getenv("AGENTIC_STREAMING_BREAK", "text_end").lower()
    AGENTIC_STREAMING_MIN_CHARS: int = int(os.getenv("AGENTIC_STREAMING_MIN_CHARS", "140"))
    AGENTIC_STREAMING_MAX_CHARS: int = int(os.getenv("AGENTIC_STREAMING_MAX_CHARS", "1200"))
    AGENTIC_STREAMING_IDLE_MS: int = int(os.getenv("AGENTIC_STREAMING_IDLE_MS", "450"))
    AGENTIC_SUBAGENTS_ENABLED: bool = (
        os.getenv("AGENTIC_SUBAGENTS_ENABLED", "false").lower() == "true"
    )
    DISCORD_AGENTIC_PREVIEW_MODE: str = os.getenv("DISCORD_AGENTIC_PREVIEW_MODE", "block").lower()

    # Autonomous research + install tool surface
    AGENT_DOWNLOAD_ROOT: str = os.getenv(
        "AGENT_DOWNLOAD_ROOT", "data/agent_downloads"
    )
    AGENT_DOWNLOAD_MAX_BYTES: int = int(
        os.getenv("AGENT_DOWNLOAD_MAX_BYTES", str(100 * 1024 * 1024))  # 100 MB
    )
    AGENTIC_BROWSER_PROFILE_DIR: str = os.getenv(
        "AGENTIC_BROWSER_PROFILE_DIR", ""
    )
    AGENTIC_BROWSER_HEADLESS: bool = (
        os.getenv("AGENTIC_BROWSER_HEADLESS", "true").strip().lower()
        in {"1", "true", "yes", "on"}
    )

    # Voice and proactive features
    ENABLE_ALWAYS_LISTENING: bool = os.getenv("ENABLE_ALWAYS_LISTENING", "true").lower() == "true"
    # E.5 Wave 4 step 3 (2026-05-13): proactive_conversations moved to
    # palli.yaml features.proactive_conversations. The flag had no
    # actual code consumers via config.* (verified by grep); deleted.
    ENABLE_PERSONALITY_LEARNING: bool = os.getenv("ENABLE_PERSONALITY_LEARNING", "true").lower() == "true"
    PERSONALITY_ENHANCEMENT_STYLER: bool = os.getenv("PERSONALITY_ENHANCEMENT_STYLER", "true").lower() == "true"
    ENABLE_RESPONSE_STYLE_PLANNER: bool = os.getenv("ENABLE_RESPONSE_STYLE_PLANNER", "true").lower() == "true"

    # Voice runtime behavior
    # E.5 Wave 4 step 4 (2026-05-13): STT_PROVIDER, TTS_PROVIDER,
    # VOICE_ENGINE_DEFAULT, VOICE_INTERACTION_MODE moved to data/palli.yaml
    # -> voice.*. Consumers read via settings_service.snapshot().voice.
    VOICE_RUNTIME_MODE: str = os.getenv("VOICE_RUNTIME_MODE", "buffered").lower()
    VOICE_PASSIVE_POLICY: str = os.getenv("VOICE_PASSIVE_POLICY", "experimental").lower()
    VOICE_BARGE_IN_ENABLED: bool = os.getenv("VOICE_BARGE_IN_ENABLED", "true").lower() == "true"
    VOICE_BARGE_IN_MIN_RMS: float = float(os.getenv("VOICE_BARGE_IN_MIN_RMS", "900"))
    VOICE_BARGE_IN_MIN_DURATION_MS: float = float(os.getenv("VOICE_BARGE_IN_MIN_DURATION_MS", "220"))
    VOICE_BARGE_IN_MAX_GAP_MS: float = float(os.getenv("VOICE_BARGE_IN_MAX_GAP_MS", "160"))
    VOICE_SHORT_REPLY_MAX_CHARS: int = int(os.getenv("VOICE_SHORT_REPLY_MAX_CHARS", "220"))
    VOICE_MIRROR_LONG_RESPONSES_TO_TEXT: bool = os.getenv("VOICE_MIRROR_LONG_RESPONSES_TO_TEXT", "true").lower() == "true"
    VOICE_MIRROR_NOTICE_IN_SPEECH: bool = os.getenv("VOICE_MIRROR_NOTICE_IN_SPEECH", "true").lower() == "true"
    # Step 31 (2026-08-20, spec §5): a hung provider must never wedge the
    # voice loop or leave her mid-stage forever. The patient-lane class
    # (organs get 60s); timeout fails to silence-with-log, never a partial.
    VOICE_RESPONSE_DEADLINE_SECONDS: float = float(os.getenv("VOICE_RESPONSE_DEADLINE_SECONDS", "60"))
    VOICE_DIRECT_ADDRESS_FOLLOWUP_WINDOW_SECONDS: float = float(
        os.getenv("VOICE_DIRECT_ADDRESS_FOLLOWUP_WINDOW_SECONDS", "20.0")
    )
    VOICE_CONTINUATION_GRACE_MS: int = int(os.getenv("VOICE_CONTINUATION_GRACE_MS", "650"))
    VOICE_DEEPGRAM_STREAM_MODEL: str = os.getenv("VOICE_DEEPGRAM_STREAM_MODEL", "flux-general-en")
    VOICE_DEEPGRAM_STREAM_ENCODING: str = os.getenv("VOICE_DEEPGRAM_STREAM_ENCODING", "linear16").lower()
    VOICE_DEEPGRAM_STREAM_SAMPLE_RATE: int = int(os.getenv("VOICE_DEEPGRAM_STREAM_SAMPLE_RATE", "48000"))
    VOICE_DEEPGRAM_STREAM_CHANNELS: int = int(os.getenv("VOICE_DEEPGRAM_STREAM_CHANNELS", "2"))
    VOICE_DEEPGRAM_STREAM_CHUNK_MS: int = int(os.getenv("VOICE_DEEPGRAM_STREAM_CHUNK_MS", "80"))
    VOICE_LOCAL_STT_MODEL: str = os.getenv("VOICE_LOCAL_STT_MODEL", "small.en")
    VOICE_LOCAL_STT_DEVICE: str = os.getenv("VOICE_LOCAL_STT_DEVICE", "auto").lower()
    VOICE_LOCAL_STT_COMPUTE_TYPE: str = os.getenv("VOICE_LOCAL_STT_COMPUTE_TYPE", "").lower()
    VOICE_LOCAL_TTS_VOICE: str = os.getenv("VOICE_LOCAL_TTS_VOICE", "")
    VOICE_LOCAL_MODEL_HOST: str = os.getenv("VOICE_LOCAL_MODEL_HOST", "ollama").lower()
    VOICE_OLLAMA_API_BASE: str = os.getenv("VOICE_OLLAMA_API_BASE", "http://127.0.0.1:11434/v1")
    VOICE_OLLAMA_MODEL: str = os.getenv("VOICE_OLLAMA_MODEL", "")
    VOICE_LM_STUDIO_API_BASE: str = os.getenv("VOICE_LM_STUDIO_API_BASE", "http://127.0.0.1:1234/v1")
    VOICE_LM_STUDIO_MODEL: str = os.getenv("VOICE_LM_STUDIO_MODEL", "")
    # Step 31 (2026-08-20, spec §7): the OPENAI_REALTIME_* /
    # VOICE_OPENAI_REALTIME_* attrs left with the deleted bridge experiment.
    VOICE_EAGER_EOT_THRESHOLD: float = float(os.getenv("VOICE_EAGER_EOT_THRESHOLD", "0.55"))
    VOICE_EOT_THRESHOLD: float = float(os.getenv("VOICE_EOT_THRESHOLD", "0.7"))
    VOICE_EOT_TIMEOUT_MS: int = int(os.getenv("VOICE_EOT_TIMEOUT_MS", "1200"))
    VOICE_OPUS_LIBRARY_PATH: str = os.getenv("VOICE_OPUS_LIBRARY_PATH", "")
    VOICE_TRACE_LOGGING_ENABLED: bool = os.getenv("VOICE_TRACE_LOGGING_ENABLED", "false").lower() == "true"
    VOICE_TRACE_FRAME_LOG_EVERY: int = int(os.getenv("VOICE_TRACE_FRAME_LOG_EVERY", "40"))
    VOICE_DEBUG_RECORD_INPUT_ENABLED: bool = os.getenv("VOICE_DEBUG_RECORD_INPUT_ENABLED", "false").lower() == "true"
    VOICE_DEBUG_RECORD_MIN_SECONDS: float = float(os.getenv("VOICE_DEBUG_RECORD_MIN_SECONDS", "4.0"))
    VOICE_DEBUG_RECORD_IDLE_FLUSH_SECONDS: float = float(os.getenv("VOICE_DEBUG_RECORD_IDLE_FLUSH_SECONDS", "1.5"))
    VOICE_DEBUG_RECORDINGS_DIR: str = os.getenv("VOICE_DEBUG_RECORDINGS_DIR", "logs/voice-debug-recordings")
    # E.5 Wave 4 step 4 (2026-05-13): LOCAL_TUI_ENABLED moved to
    # data/palli.yaml -> voice.local_tui.enabled. Read via
    # settings_service.snapshot().voice.local_tui.enabled.
    LOCAL_VOICE_SAMPLE_RATE: int = int(os.getenv("LOCAL_VOICE_SAMPLE_RATE", "48000"))
    LOCAL_VOICE_CHANNELS: int = int(os.getenv("LOCAL_VOICE_CHANNELS", "1"))
    LOCAL_VOICE_BLOCKSIZE: int = int(os.getenv("LOCAL_VOICE_BLOCKSIZE", "960"))
    LOCAL_VOICE_INPUT_DEVICE: str = os.getenv("LOCAL_VOICE_INPUT_DEVICE", "")
    LOCAL_VOICE_OUTPUT_DEVICE: str = os.getenv("LOCAL_VOICE_OUTPUT_DEVICE", "")
    LOCAL_VOICE_PUSH_TO_TALK_KEY: str = os.getenv("LOCAL_VOICE_PUSH_TO_TALK_KEY", "r")
    LOCAL_TUI_CHANNEL_ID: int = int(os.getenv("LOCAL_TUI_CHANNEL_ID", "0"))
    # E.5 Wave 4 step 4 (2026-05-13): LOCAL_VOICE_DEFAULT_MODE moved
    # to data/palli.yaml -> voice.local_tui.default_mode. Read via
    # settings_service.snapshot().voice.local_tui.default_mode.
    LOCAL_VOICE_PRESENCE_PROFILE: str = os.getenv("LOCAL_VOICE_PRESENCE_PROFILE", "balanced").lower()
    LOCAL_PASSIVE_SILENCE_THRESHOLD_SECONDS: float = float(os.getenv("LOCAL_PASSIVE_SILENCE_THRESHOLD_SECONDS", "1.2"))
    LOCAL_PASSIVE_MAX_BUFFER_TIME_SECONDS: float = float(os.getenv("LOCAL_PASSIVE_MAX_BUFFER_TIME_SECONDS", "12.0"))
    LOCAL_PASSIVE_ACTIVITY_THRESHOLD: int = int(os.getenv("LOCAL_PASSIVE_ACTIVITY_THRESHOLD", "220"))
    LOCAL_PASSIVE_MIN_SPEECH_DURATION_SECONDS: float = float(os.getenv("LOCAL_PASSIVE_MIN_SPEECH_DURATION_SECONDS", "0.35"))
    # Lemonfox TTS (2026-07-14, promoted 2026-08-20): joined as the Cartesia
    # credit-outage fallback; step 31 cut Cartesia entirely (owner's call,
    # CARTESIA_* attrs deleted with it) and lemonfox became her provider of
    # record. LEMONFOX_TTS_VOICE is HER voice (owner picked olivia,
    # 2026-08-21, set in .env.local); the "sarah" default is only the
    # unconfigured floor.
    LEMONFOX_API_KEY: str = os.getenv("LEMONFOX_API_KEY", "")
    LEMONFOX_TTS_VOICE: str = os.getenv("LEMONFOX_TTS_VOICE", "sarah")

    # Entitlement policy. Local mode defaults these off so Discord and proactive
    # behavior do not depend on hosted subscription state.
    REQUIRE_HAS_ACCESS_FOR_VOICE: bool = os.getenv("REQUIRE_HAS_ACCESS_FOR_VOICE", "false").lower() == "true"
    REQUIRE_HAS_ACCESS_FOR_PROACTIVE: bool = os.getenv("REQUIRE_HAS_ACCESS_FOR_PROACTIVE", "false").lower() == "true"

    # Voice session tuning (always-listening)
    VOICE_SILENCE_THRESHOLD_SECONDS: float = float(os.getenv("VOICE_SILENCE_THRESHOLD_SECONDS", "2.5"))
    VOICE_MAX_BUFFER_TIME_SECONDS: float = float(os.getenv("VOICE_MAX_BUFFER_TIME_SECONDS", "10.0"))
    VOICE_ACTIVITY_THRESHOLD: int = int(os.getenv("VOICE_ACTIVITY_THRESHOLD", "200"))
    VOICE_MIN_SPEECH_DURATION_SECONDS: float = float(os.getenv("VOICE_MIN_SPEECH_DURATION_SECONDS", "0.3"))
    VOICE_INACTIVE_USER_CLEANUP_SECONDS: float = float(os.getenv("VOICE_INACTIVE_USER_CLEANUP_SECONDS", "120.0"))

    # Short-term follow-up features
    ENABLE_SHORT_TERM_FOLLOWUP: bool = os.getenv("ENABLE_SHORT_TERM_FOLLOWUP", "true").lower() == "true"
    FOLLOWUP_MIN_DELAY_SECONDS: int = int(os.getenv("FOLLOWUP_MIN_DELAY_SECONDS", "300"))
    FOLLOWUP_MAX_DELAY_SECONDS: int = int(os.getenv("FOLLOWUP_MAX_DELAY_SECONDS", "600"))
    FOLLOWUP_MAX_DAILY_PER_USER: int = int(os.getenv("FOLLOWUP_MAX_DAILY_PER_USER", "5"))
    FOLLOWUP_CONFIDENCE_THRESHOLD: float = float(os.getenv("FOLLOWUP_CONFIDENCE_THRESHOLD", "0.6"))

    # Conversational ideation features
    ENABLE_CONVERSATIONAL_IDEATION: bool = os.getenv("ENABLE_CONVERSATIONAL_IDEATION", "true").lower() == "true"
    IDEATION_TANGENT_PROBABILITY: float = float(os.getenv("IDEATION_TANGENT_PROBABILITY", "0.30"))  # 30% chance
    IDEATION_TOPIC_SHIFT_PROBABILITY: float = float(os.getenv("IDEATION_TOPIC_SHIFT_PROBABILITY", "0.10"))  # 10% chance

    # Passive listening configuration
    PASSIVE_LISTENING_MODE: bool = os.getenv("PASSIVE_LISTENING_MODE", "true").lower() == "true"
    PASSIVE_CHECK_INTERVAL_SECONDS: float = float(os.getenv("PASSIVE_CHECK_INTERVAL_SECONDS", "15.0"))
    PASSIVE_UTTERANCE_CHECK_THRESHOLD: int = int(os.getenv("PASSIVE_UTTERANCE_CHECK_THRESHOLD", "5"))
    PASSIVE_MIN_CONFIDENCE_TO_SPEAK: float = float(os.getenv("PASSIVE_MIN_CONFIDENCE_TO_SPEAK", "0.75"))
    PASSIVE_MAX_SILENCE_BEFORE_CHECK_SECONDS: float = float(os.getenv("PASSIVE_MAX_SILENCE_BEFORE_CHECK_SECONDS", "30.0"))
    PASSIVE_ALWAYS_RESPOND_TO_DIRECT_ADDRESS: bool = os.getenv("PASSIVE_ALWAYS_RESPOND_TO_DIRECT_ADDRESS", "true").lower() == "true"
    PASSIVE_MAX_CONTEXT_BUFFER_SIZE: int = int(os.getenv("PASSIVE_MAX_CONTEXT_BUFFER_SIZE", "10"))

    # SaaS / multi-tenant UX
    # How often (in seconds) to remind admins in an unlinked Discord guild
    # that they need to link the server to a PaLLi account before the bot
    # will respond in channels.
    UNLINKED_GUILD_NOTICE_COOLDOWN: float = float(os.getenv("UNLINKED_GUILD_NOTICE_COOLDOWN", "3600"))
    
    # Proactive conversation limits
    MIN_DAILY_PROACTIVE_MESSAGES: int = int(os.getenv("MIN_DAILY_PROACTIVE_MESSAGES", "1"))
    MAX_DAILY_PROACTIVE_MESSAGES: int = int(os.getenv("MAX_DAILY_PROACTIVE_MESSAGES", "3"))
    MIN_CONVERSATION_GAP_HOURS: float = float(os.getenv("MIN_CONVERSATION_GAP_HOURS", "1.0"))  # 1 hour minimum
    MAX_PROACTIVE_FREQUENCY_HOURS: float = float(os.getenv("MAX_PROACTIVE_FREQUENCY_HOURS", "3.0"))  # 3 hours
    PROACTIVE_CHECK_INTERVAL_MINUTES: int = int(os.getenv("PROACTIVE_CHECK_INTERVAL_MINUTES", "15"))  # 15 minutes
    PROACTIVE_CONFIDENCE_THRESHOLD: float = float(os.getenv("PROACTIVE_CONFIDENCE_THRESHOLD", "0.7"))
    PROACTIVE_ABSOLUTE_MIN_GAP_MINUTES: int = int(os.getenv("PROACTIVE_ABSOLUTE_MIN_GAP_MINUTES", "60"))  # 60 minutes absolute minimum
    # Phase 4 — monologue-fed proactive pipeline. Staleness window for
    # monologue `proactive_candidate` hints to be considered. Beyond this
    # the candidate is treated as past tense and ignored.
    PROACTIVE_CANDIDATE_STALENESS_HOURS: int = int(os.getenv("PROACTIVE_CANDIDATE_STALENESS_HOURS", "6"))

    # Present Moment tier-1 pacing (spec §8). The gap DEFERS, never cancels.
    PRESENT_MOMENT_MIN_GAP_SECONDS: int = int(os.getenv("PRESENT_MOMENT_MIN_GAP_SECONDS", "600"))
    PRESENT_MOMENT_MAX_CYCLES_PER_DAY: int = int(os.getenv("PRESENT_MOMENT_MAX_CYCLES_PER_DAY", "120"))
    PRESENT_MOMENT_HEARTBEAT_SECONDS: int = int(os.getenv("PRESENT_MOMENT_HEARTBEAT_SECONDS", "1200"))
    PRESENT_MOMENT_STALE_AFTER_SECONDS: int = int(os.getenv("PRESENT_MOMENT_STALE_AFTER_SECONDS", "21600"))
    PRESENT_MOMENT_SENTINEL_HOURS: int = int(os.getenv("PRESENT_MOMENT_SENTINEL_HOURS", "12"))
    PRESENT_MOMENT_ARRIVAL_ABSENCE_SECONDS: int = int(os.getenv("PRESENT_MOMENT_ARRIVAL_ABSENCE_SECONDS", "1800"))

    # Enhanced proactive conversation settings
    PROACTIVE_RANDOMNESS_LOW_CHANCE: float = float(os.getenv("PROACTIVE_RANDOMNESS_LOW_CHANCE", "0.20"))  # 20% for 0.3-0.5 confidence
    PROACTIVE_RANDOMNESS_MED_CHANCE: float = float(os.getenv("PROACTIVE_RANDOMNESS_MED_CHANCE", "0.40"))  # 40% for 0.5-0.7 confidence
    PROACTIVE_RANDOMNESS_HIGH_CHANCE: float = float(os.getenv("PROACTIVE_RANDOMNESS_HIGH_CHANCE", "0.35"))  # 35% for 0.7+ confidence
    PROACTIVE_RELATIONSHIP_MAINTENANCE_DAYS: float = float(os.getenv("PROACTIVE_RELATIONSHIP_MAINTENANCE_DAYS", "3.0"))  # Auto-boost after 3+ days
    PROACTIVE_RELATIONSHIP_MAINTENANCE_MAX_BOOST: float = float(os.getenv("PROACTIVE_RELATIONSHIP_MAINTENANCE_MAX_BOOST", "0.3"))  # Max +0.3 confidence boost
    PROACTIVE_ENABLE_RANDOMNESS_BOOST: bool = os.getenv("PROACTIVE_ENABLE_RANDOMNESS_BOOST", "true").lower() == "true"
    PROACTIVE_ENABLE_RELATIONSHIP_MAINTENANCE: bool = os.getenv("PROACTIVE_ENABLE_RELATIONSHIP_MAINTENANCE", "true").lower() == "true"
    PROACTIVE_LOGGER_VERBOSE: bool = os.getenv("PROACTIVE_LOGGER_VERBOSE", "false").lower() == "true"

    # Admin allowlists for proactive channel outreach
    PROACTIVE_ALLOWED_GUILD_IDS: List[int] = []
    PROACTIVE_ALLOWED_CHANNEL_IDS: List[int] = []

    # Proactive decision strategy
    # - "classic": existing single-pass decision
    # - "llm_guarded": add extra LLM timing + content self-checks
    PROACTIVE_STRATEGY: str = os.getenv("PROACTIVE_STRATEGY", "classic").lower()

    # Jarvis-direction (2026-05-05) — desktop activity watcher tunables.
    # The watcher runs an in-memory rolling timeline of (focused window,
    # machine state, recent files in watched dirs) on a periodic tick.
    # Used by the inner monologue's situation_context as a 6th sense.
    DESKTOP_ACTIVITY_POLL_SECONDS: float = float(os.getenv("DESKTOP_ACTIVITY_POLL_SECONDS", "30") or "30")
    DESKTOP_ACTIVITY_WINDOW_SECONDS: float = float(os.getenv("DESKTOP_ACTIVITY_WINDOW_SECONDS", "1800") or "1800")
    # 2026-08-18: the desktop senses (focused window + activity watcher) can
    # be switched off entirely — a headless host has nothing to sense, and a
    # sense that reports "unknown" every 30s is worse than none.
    DESKTOP_ACTIVITY_ENABLED: bool = os.getenv("DESKTOP_ACTIVITY_ENABLED", "true").lower() == "true"
    DESKTOP_ACTIVITY_WATCH_DIRS: str = os.getenv("DESKTOP_ACTIVITY_WATCH_DIRS", "") or ""
    DESKTOP_ACTIVITY_FILE_LOOKBACK_SECONDS: float = float(os.getenv("DESKTOP_ACTIVITY_FILE_LOOKBACK_SECONDS", "600") or "600")
    DESKTOP_ACTIVITY_MAX_RECENT_FILES: int = int(os.getenv("DESKTOP_ACTIVITY_MAX_RECENT_FILES", "8") or "8")

    # The house NAS mount — ONE knob for the whole app (step 28 task 2).
    # EMPTY ("" — the default) means there is NO NAS, and every consumer
    # (her "where you live" passage, the workbench write-belt, the curiosity
    # dive) says so and does nothing rather than guarding, advertising or
    # walking a path that is not there. Read it through nas_root() below,
    # never off this class directly: class attributes freeze at import.
    NAS_ROOT: str = os.getenv("PALLI_NAS_ROOT", "").strip()

    # Local Shell tier-1 tool — direct subprocess execution on the host
    # (bypasses Claude Code SDK / Anthropic AUP refusal path). Safety
    # gating lives in the read-only allow-list + LLM intent classifier
    # in core/turn/local_shell.py, modulated by the user's autonomy
    # policy from ACTION_PREFERENCES.md.
    LOCAL_SHELL_ENABLED: bool = (os.getenv("LOCAL_SHELL_ENABLED", "true") or "true").lower() == "true"
    LOCAL_SHELL_TIMEOUT_SECONDS: float = float(os.getenv("LOCAL_SHELL_TIMEOUT_SECONDS", "30") or "30")
    LOCAL_SHELL_MAX_OUTPUT_CHARS: int = int(os.getenv("LOCAL_SHELL_MAX_OUTPUT_CHARS", "8000") or "8000")
    LOCAL_SHELL_DEFAULT_INTERPRETER: str = os.getenv("LOCAL_SHELL_DEFAULT_INTERPRETER", "auto") or "auto"

    # Local Read-File tier-1 tool — read a file scoped to AGENTIC_WORKSPACE_ROOT
    # + DESKTOP_ACTIVITY_WATCH_DIRS so PaLLi can speak about its actual contents.
    LOCAL_READ_FILE_ENABLED: bool = (os.getenv("LOCAL_READ_FILE_ENABLED", "true") or "true").lower() == "true"
    LOCAL_READ_FILE_MAX_BYTES: int = int(os.getenv("LOCAL_READ_FILE_MAX_BYTES", "120000") or "120000")

    # Proactive Action — when the inner monologue emits a candidate with
    # candidate_kind="action", route through local_shell/read_file directly
    # instead of synthesizing a Discord message.
    PROACTIVE_ACTION_ENABLED: bool = (os.getenv("PROACTIVE_ACTION_ENABLED", "true") or "true").lower() == "true"

    # Proactive Turn-Loop Delivery — when ON, core/turn/proactive
    # delivers its synthesized turn to Discord via channel_runtime. ON
    # by default — this is the only path that ships proactives today.
    # Flip OFF only if you want PaLLi to *generate* proactives without
    # actually delivering them (e.g. for offline log analysis).
    PROACTIVE_TURN_LOOP_DELIVERY_ENABLED: bool = (
        os.getenv("PROACTIVE_TURN_LOOP_DELIVERY_ENABLED", "true") or "true"
    ).lower() == "true"

    # Debug Logging
    ENABLE_DEBUG_LOGGING: bool = os.getenv("ENABLE_DEBUG_LOGGING", "false").lower() == "true"
    LOG_LLM_RESPONSE_CALLS: bool = os.getenv("LOG_LLM_RESPONSE_CALLS", "false").lower() == "true"
    LOG_FULL_DISCORD_MESSAGES: bool = os.getenv("LOG_FULL_DISCORD_MESSAGES", "false").lower() == "true"
    _IS_PROD_ENV: bool = (os.getenv("ENVIRONMENT", "development") or "").lower() in ("production", "prod")
    LOG_REDACT_USER_CONTENT: bool = os.getenv(
        "LOG_REDACT_USER_CONTENT",
        "true" if _IS_PROD_ENV else "false",
    ).lower() == "true"
    LOG_USER_CONTENT_PREVIEW_CHARS: int = int(
        os.getenv("LOG_USER_CONTENT_PREVIEW_CHARS", "0" if _IS_PROD_ENV else "50")
    )

    # Learning Parameters
    PERSONALITY_LEARNING_RATE: float = float(os.getenv("PERSONALITY_LEARNING_RATE", "0.1"))
    CONTEXT_WINDOW_SIZE: int = int(os.getenv("CONTEXT_WINDOW_SIZE", "50"))
    # Cross-platform / unified memory (safest default: only unify DM/1:1 context)
    UNIFIED_DM_MEMORY_ENABLED: bool = os.getenv("UNIFIED_DM_MEMORY_ENABLED", "true").lower() == "true"
    # When enabled, unified DM memory is also used for background/proactive flows
    # (short-term followups, proactive agent decisions, etc.). Default off.
    UNIFIED_DM_MEMORY_BACKGROUND_ENABLED: bool = (
        os.getenv("UNIFIED_DM_MEMORY_BACKGROUND_ENABLED", "false").lower() == "true"
    )
    # Tone/profile mode for response shaping
    # - "natural": minimize mirroring, keep voice consistent and human
    # - "legacy": prior behavior (more style guidance injected)
    PALLI_TONE_MODE: str = os.getenv("PALLI_TONE_MODE", "natural").lower()
    # Response length budgeting mode:
    # - "adaptive": use per-turn length buckets + hard max token caps
    # - "legacy": revert to the old fixed large max token cap
    PALLI_LENGTH_MODE: str = os.getenv("PALLI_LENGTH_MODE", "adaptive").lower()
    PALLI_LENGTH_BUCKET_TINY_MAX_TOKENS: int = int(
        os.getenv("PALLI_LENGTH_BUCKET_TINY_MAX_TOKENS", "200")
    )
    PALLI_LENGTH_BUCKET_SHORT_MAX_TOKENS: int = int(
        os.getenv("PALLI_LENGTH_BUCKET_SHORT_MAX_TOKENS", "350")
    )
    PALLI_LENGTH_BUCKET_MEDIUM_MAX_TOKENS: int = int(
        os.getenv("PALLI_LENGTH_BUCKET_MEDIUM_MAX_TOKENS", "650")
    )
    PALLI_LENGTH_BUCKET_LONG_MAX_TOKENS: int = int(
        os.getenv("PALLI_LENGTH_BUCKET_LONG_MAX_TOKENS", "1200")
    )
    TRUNCATE_LLM_HISTORY_MESSAGES: bool = os.getenv("TRUNCATE_LLM_HISTORY_MESSAGES", "true").lower() == "true"
    # Even when user-history truncation is disabled, truncating assistant history helps
    # reduce self-mirroring (the model copying its own last phrasing/punctuation).
    TRUNCATE_ASSISTANT_HISTORY_MESSAGES: bool = os.getenv("TRUNCATE_ASSISTANT_HISTORY_MESSAGES", "true").lower() == "true"
    ASSISTANT_HISTORY_MESSAGE_CHAR_LIMIT: int = int(os.getenv("ASSISTANT_HISTORY_MESSAGE_CHAR_LIMIT", "200"))
    USE_ROLE_SEPARATED_LLM_HISTORY: bool = os.getenv("USE_ROLE_SEPARATED_LLM_HISTORY", "false").lower() == "true"
    DEFAULT_LLM_CONTEXT_WINDOW_SIZE: int = min(CONTEXT_WINDOW_SIZE, 80)
    DEFAULT_ANALYSIS_CONTEXT_WINDOW_SIZE: int = min(CONTEXT_WINDOW_SIZE, 120)
    DEFAULT_MEMORY_CONTEXT_WINDOW_SIZE: int = min(CONTEXT_WINDOW_SIZE, 100)
    DEFAULT_PROACTIVE_CONTEXT_WINDOW_SIZE: int = min(CONTEXT_WINDOW_SIZE, 200)

    LLM_CONTEXT_WINDOW_SIZE: int = int(
        os.getenv("LLM_CONTEXT_WINDOW_SIZE", str(DEFAULT_LLM_CONTEXT_WINDOW_SIZE))
    )
    ANALYSIS_CONTEXT_WINDOW_SIZE: int = int(
        os.getenv("ANALYSIS_CONTEXT_WINDOW_SIZE", str(DEFAULT_ANALYSIS_CONTEXT_WINDOW_SIZE))
    )
    MEMORY_CONTEXT_WINDOW_SIZE: int = int(
        os.getenv("MEMORY_CONTEXT_WINDOW_SIZE", str(DEFAULT_MEMORY_CONTEXT_WINDOW_SIZE))
    )
    PROACTIVE_CONTEXT_WINDOW_SIZE: int = int(
        os.getenv("PROACTIVE_CONTEXT_WINDOW_SIZE", str(DEFAULT_PROACTIVE_CONTEXT_WINDOW_SIZE))
    )

    # Style planner only needs recent messages to decide question usage (saves ~2500 tokens per call)
    STYLE_PLANNER_CONTEXT_WINDOW: int = int(os.getenv("STYLE_PLANNER_CONTEXT_WINDOW", "10"))

    # Admin Configuration
    ADMIN_USER_IDS: List[int] = []
    ALLOWED_DISCORD_USER_IDS: List[int] = []
    AGENTIC_ALLOWED_DISCORD_USER_IDS: List[int] = []
    AGENTIC_DEBUG_ALLOW_USER_IDS: List[int] = []
    DEV_ALLOW_UNLINKED_GUILDS: List[int] = []
    DEV_ALLOW_UNLINKED_CHANNELS: List[int] = []
    TRUSTED_PROXY_IPS: List[str] = ["127.0.0.1", "::1"]

    # Boot's verdict for THIS run (stranger-proofing week 1, task 1). validate()
    # replaces all four every time it runs: the punch list is what a stranger is
    # told to fix, and the effective_* fields are what the runtime reads instead
    # of the configured value when a key for an optional feature is missing. The
    # defaults mean "nothing has been decided yet", so a reader that runs before
    # the first validate() behaves exactly as it did before this existed.
    punch_list: Optional["PunchList"] = None
    effective_web_search_enabled: bool = True
    effective_stt_provider: str = ""
    effective_tts_provider: str = ""
    effective_discord_enabled: bool = True
    # Telegram door (2026-09-14, task 2): False at class level, because the
    # door's own flag is off by default — an unvalidated read must not open
    # a door the operator never asked for.
    effective_telegram_enabled: bool = False
    # The mobile door (2026-10-13): False at class level for the same reason.
    effective_mobile_enabled: bool = False
    # Setup mode (first door, task 1, 2026-09-17): False only when the top-level
    # provider needs a key and has none. She still starts — both doors shut,
    # every thinking lane idle, a turn answered with one fixed sentence — and
    # the setup API flips this back on the running process once a key is saved
    # (core/setup_mode.py). True at class level for the reason above: a reader
    # that runs before the first validate() must behave as it always did.
    brain_ready: bool = True

    def __init__(self):
        self.PALLI_DEPLOYMENT_MODE = (
            os.getenv("PALLI_DEPLOYMENT_MODE", self.PALLI_DEPLOYMENT_MODE).strip().lower()
        )
        self.ENABLE_LEGACY_HOSTED_ACCESS = (
            os.getenv(
                "ENABLE_LEGACY_HOSTED_ACCESS",
                "true" if self.ENABLE_LEGACY_HOSTED_ACCESS else "false",
            ).lower()
            == "true"
        )
        self.AGENTIC_WORKSPACE_ROOT = self._normalize_existing_dir(
            os.getenv("AGENTIC_WORKSPACE_ROOT", self.AGENTIC_WORKSPACE_ROOT),
            default=os.getcwd(),
            setting_name="AGENTIC_WORKSPACE_ROOT",
        )

        repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        plugin_dirs = os.getenv("PLUGIN_DIRECTORIES", "plugins")
        parsed_plugin_dirs: List[str] = []
        for raw_value in str(plugin_dirs or "").split(","):
            clean = raw_value.strip()
            if not clean:
                continue
            candidate = os.path.expanduser(clean)
            if not os.path.isabs(candidate):
                candidate = os.path.join(repo_root, candidate)
            normalized = os.path.abspath(candidate)
            if normalized not in parsed_plugin_dirs:
                parsed_plugin_dirs.append(normalized)
        self.PLUGIN_DIRECTORIES = parsed_plugin_dirs or [os.path.join(repo_root, "plugins")]

        # Parse admin user IDs
        admin_ids_str = os.getenv("ADMIN_USER_IDS", "")
        if admin_ids_str:
            try:
                self.ADMIN_USER_IDS = [
                    int(uid.strip()) for uid in admin_ids_str.split(",") if uid.strip()
                ]
            except ValueError:
                logging.warning("Invalid ADMIN_USER_IDS format in environment variables")

        allow_discord_user_ids = os.getenv("ALLOWED_DISCORD_USER_IDS", "")
        if allow_discord_user_ids:
            try:
                self.ALLOWED_DISCORD_USER_IDS = [
                    int(uid.strip()) for uid in allow_discord_user_ids.split(",") if uid.strip()
                ]
            except ValueError:
                logging.warning("Invalid ALLOWED_DISCORD_USER_IDS format in environment variables")

        agentic_allowed_ids = os.getenv("AGENTIC_ALLOWED_DISCORD_USER_IDS", "")
        if agentic_allowed_ids:
            try:
                self.AGENTIC_ALLOWED_DISCORD_USER_IDS = [
                    int(uid.strip()) for uid in agentic_allowed_ids.split(",") if uid.strip()
                ]
            except ValueError:
                logging.warning(
                    "Invalid AGENTIC_ALLOWED_DISCORD_USER_IDS format in environment variables"
                )

        agentic_debug_ids = os.getenv("AGENTIC_DEBUG_ALLOW_USER_IDS", "")
        if agentic_debug_ids:
            try:
                self.AGENTIC_DEBUG_ALLOW_USER_IDS = [
                    int(uid.strip()) for uid in agentic_debug_ids.split(",") if uid.strip()
                ]
            except ValueError:
                logging.warning(
                    "Invalid AGENTIC_DEBUG_ALLOW_USER_IDS format in environment variables"
                )

        self.AGENTIC_SUBAGENTS_ENABLED = (
            os.getenv("AGENTIC_SUBAGENTS_ENABLED", "false").lower() == "true"
        )

        # Temporary development allowlist for unlinked Discord guilds/channels.
        # This lets you keep PaLLi talking in specific places while the SaaS
        # layer is still under construction.
        allow_guilds = os.getenv("DEV_ALLOW_UNLINKED_GUILDS", "")
        if allow_guilds:
            try:
                self.DEV_ALLOW_UNLINKED_GUILDS = [
                    int(gid.strip()) for gid in allow_guilds.split(",") if gid.strip()
                ]
            except ValueError:
                logging.warning("Invalid DEV_ALLOW_UNLINKED_GUILDS format in environment variables")

        allow_channels = os.getenv("DEV_ALLOW_UNLINKED_CHANNELS", "")
        if allow_channels:
            try:
                self.DEV_ALLOW_UNLINKED_CHANNELS = [
                    int(cid.strip()) for cid in allow_channels.split(",") if cid.strip()
                ]
            except ValueError:
                logging.warning("Invalid DEV_ALLOW_UNLINKED_CHANNELS format in environment variables")

        trusted_proxy_ips = os.getenv("TRUSTED_PROXY_IPS", "")
        if trusted_proxy_ips:
            self.TRUSTED_PROXY_IPS = [
                ip.strip() for ip in trusted_proxy_ips.split(",") if ip.strip()
            ]
        if self.LOG_USER_CONTENT_PREVIEW_CHARS < 0:
            self.LOG_USER_CONTENT_PREVIEW_CHARS = 0

        proactive_guilds = os.getenv("PROACTIVE_ALLOWED_GUILD_IDS", "")
        if proactive_guilds:
            try:
                self.PROACTIVE_ALLOWED_GUILD_IDS = [
                    int(gid.strip()) for gid in proactive_guilds.split(",") if gid.strip()
                ]
            except ValueError:
                logging.warning("Invalid PROACTIVE_ALLOWED_GUILD_IDS format in environment variables")

        proactive_channels = os.getenv("PROACTIVE_ALLOWED_CHANNEL_IDS", "")
        if proactive_channels:
            try:
                self.PROACTIVE_ALLOWED_CHANNEL_IDS = [
                    int(cid.strip()) for cid in proactive_channels.split(",") if cid.strip()
                ]
            except ValueError:
                logging.warning("Invalid PROACTIVE_ALLOWED_CHANNEL_IDS format in environment variables")

        default_web_ops_events = self.get_default_web_ops_event_types()
        web_ops_events = os.getenv(
            "WEB_OPS_EVENT_TYPES",
            ",".join(default_web_ops_events),
        )
        parsed_events = [evt.strip().lower() for evt in web_ops_events.split(",") if evt.strip()]
        self.WEB_OPS_EVENT_TYPES = parsed_events or default_web_ops_events

        # CORS origin configuration - must be explicitly set for security
        origins = os.getenv("FRONTEND_ORIGINS", "")
        self.FRONTEND_ORIGINS = [
            origin.strip() for origin in origins.split(",") if origin.strip()
        ]

        # FRONTEND_ORIGIN_REGEX must be explicitly configured - no default wildcard
        self.FRONTEND_ORIGIN_REGEX = os.getenv("FRONTEND_ORIGIN_REGEX") or None

    @staticmethod
    def _normalize_existing_dir(raw_value: Optional[str], *, default: str, setting_name: str) -> str:
        candidate = str(raw_value or "").strip()
        fallback = os.path.abspath(os.path.expanduser(str(default or os.getcwd())))

        if not candidate:
            return fallback

        normalized = os.path.abspath(os.path.expanduser(candidate))
        if os.path.isdir(normalized):
            return normalized

        logging.warning(
            "%s=%r does not exist on this machine; falling back to %s",
            setting_name,
            candidate,
            fallback,
        )
        return fallback

    def _codex_cli_login_available(self) -> bool:
        executable = str(self.CODEX_CLI_PATH or "").strip()
        if not executable:
            return False
        if os.path.isabs(executable):
            resolved = executable if os.path.exists(executable) else None
        else:
            resolved = shutil.which(executable)
        if not resolved:
            logging.error("CODEX_CLI_PATH=%s could not be resolved", executable)
            return False
        try:
            result = subprocess.run(
                [resolved, "login", "status"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
        except Exception as exc:
            logging.error("Failed to query Codex CLI login status: %s", exc)
            return False
        if result.returncode != 0:
            logging.error("Codex CLI is installed but not logged in")
            return False
        return True

    def validate(self) -> bool:
        """Validate configuration via CoreConfigSchema (Phase 8d).

        The declarative validation rules now live in
        `core/config_schema.py:CoreConfigSchema`. This method delegates
        to it and adds the two pieces that don't belong in a pure
        Pydantic model:

          1. The Codex CLI subprocess login check (impure I/O — Pydantic
             validators must be side-effect-free for caching reasons).
          2. The "no messaging platform configured" informational warning
             (a soft signal that doesn't block startup).

        The bool return contract is preserved: True == ok to start, False
        == fatal config error logged. Callers are launcher_runtime.py
        and launcher_support.py, both of which short-circuit on False.
        """
        from core.config_schema import validate_config

        if not validate_config(self):
            return False

        # Codex CLI requires a login subprocess probe. Only run it when
        # codex is selected for the top-level provider OR any per-task
        # provider — otherwise we'd spawn a subprocess on every PaLLi
        # startup for nothing.
        provider = (self.LLM_PROVIDER or "").lower()
        task_providers = (
            (self.LLM_RESPONSE_PROVIDER or "").lower(),
            (self.LLM_ANALYSIS_PROVIDER or "").lower(),
            (self.LLM_MEMORY_PROVIDER or "").lower(),
            (self.LLM_DECISION_PROVIDER or "").lower(),
            (self.LLM_PROACTIVE_PROVIDER or "").lower(),
            (self.LLM_REFLECTION_PROVIDER or "").lower(),
            (self.LLM_WEB_SEARCH_PROVIDER or "").lower(),
            (self.LLM_VISION_PROVIDER or "").lower(),
        )
        if provider == "codex" or any(p == "codex" for p in task_providers):
            if not self._codex_cli_login_available():
                # The one check that is not in the schema, so it has to write its
                # own punch item — a failed validate() must never print a blank
                # list (stranger-proofing week 1, task 1).
                punch = getattr(self, "punch_list", None)
                if punch is not None:
                    # Imported HERE, not at module scope: core.config is the
                    # bottom of the import graph and must not depend on
                    # palli_config. `ctl_prefix` itself imports only shutil, so
                    # the edge is one-way and safe inside the call.
                    from palli_config.ctl_prefix import ctl_prefix

                    punch.add(
                        "CODEX_CLI",
                        "a provider routes to codex but the Codex CLI is not logged in",
                        f"run `{ctl_prefix()} auth codex`",
                    )
                return False

        # Non-blocking platform warning. Discord is currently the only
        # platform that gates here; Telegram/Slack are tracked separately.
        # Read from boot's verdict (set by validate_config above) so this log
        # line and the punch list can never disagree about whether the Discord
        # door opened this boot.
        platforms_enabled = any([
            self.effective_discord_enabled,
        ])
        if not platforms_enabled:
            logging.warning("No messaging platforms configured; running in API-only mode")

        return True

    def local_deployment_mode_enabled(self) -> bool:
        return str(self.PALLI_DEPLOYMENT_MODE or "").strip().lower() in {"local", "local_single_user"}

    def legacy_hosted_access_enabled(self) -> bool:
        mode = str(self.PALLI_DEPLOYMENT_MODE or "").strip().lower()
        return bool(self.ENABLE_LEGACY_HOSTED_ACCESS) or mode == "cloud"

    def get_default_web_ops_event_types(self) -> List[str]:
        events = ["visit", "signup", "email_verified"]
        if self.legacy_hosted_access_enabled():
            events.extend(["checkout_started", "payment_succeeded"])
        return events

    def get_frontend_origin(self) -> Optional[str]:
        """Return the normalized origin (scheme://host[:port]) for FRONTEND_URL."""
        raw = (self.FRONTEND_URL or "").strip()
        if not raw:
            return None
        try:
            parsed = urlparse(raw)
        except Exception:
            return None
        if not parsed.scheme or not parsed.netloc:
            return None
        return f"{parsed.scheme}://{parsed.netloc}"

    def get_backend_origin(self) -> Optional[str]:
        """Return the normalized origin (scheme://host[:port]) for BACKEND_URL."""
        raw = (self.BACKEND_URL or "").strip()
        if not raw:
            return None
        try:
            parsed = urlparse(raw)
        except Exception:
            return None
        if not parsed.scheme or not parsed.netloc:
            return None
        return f"{parsed.scheme}://{parsed.netloc}"

    @staticmethod
    def _get_url_host_port(raw: Optional[str], *, default_port: int) -> Optional[Tuple[str, int]]:
        clean = str(raw or "").strip()
        if not clean:
            return None
        try:
            parsed = urlparse(clean)
        except Exception:
            return None
        host = parsed.hostname
        if not host:
            return None
        try:
            port = int(parsed.port or default_port)
        except Exception:
            return None
        return host, port

    def get_frontend_host_port(self) -> Optional[Tuple[str, int]]:
        """Return the host/port derived from FRONTEND_URL."""
        return self._get_url_host_port(self.FRONTEND_URL, default_port=3000)

    def get_backend_host_port(self) -> Optional[Tuple[str, int]]:
        """Return the host/port derived from BACKEND_URL."""
        return self._get_url_host_port(self.BACKEND_URL, default_port=self.BACKEND_PORT)

# --- Truncation tuning -----------------------------------------------------
# Default values match the previously-hardcoded magic numbers exactly, so
# behavior is identical until a user overrides via env. See core/truncation.py
# for which categories are toggle-affected by the diagnostic master flag.
#
# Set any individual value to 0 to disable that specific truncation. Set
# TRUNCATION_DIAGNOSTIC_MODE=true to zero every toggle-category limit at
# once (useful for "what would PaLLi process unbounded" smoke runs).
def _trunc_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return max(0, int(raw))
    except ValueError:
        return default


_TRUNCATION_DIAGNOSTIC_MODE = (
    os.getenv("TRUNCATION_DIAGNOSTIC_MODE", "false").lower() == "true"
)


def _trunc_value(env_name: str, default: int) -> int:
    """When diagnostic mode is on, return 0 (unlimited). Otherwise read env."""
    if _TRUNCATION_DIAGNOSTIC_MODE:
        return 0
    return _trunc_env(env_name, default)


# attach to Config class
Config.TRUNCATION_DIAGNOSTIC_MODE = _TRUNCATION_DIAGNOSTIC_MODE

# Group A — system-prompt content (what PaLLi sees per turn). Toggle-affected.
Config.TRUNCATION_DELEGATION_SUMMARY_IN_PROMPT = _trunc_value(
    "TRUNCATION_DELEGATION_SUMMARY_IN_PROMPT", 240,
)
Config.TRUNCATION_IDENTITY_EXCERPT_IN_TIER2 = _trunc_value(
    "TRUNCATION_IDENTITY_EXCERPT_IN_TIER2", 1500,
)
Config.TRUNCATION_TASK_HINT_IN_PROMPT = _trunc_value(
    "TRUNCATION_TASK_HINT_IN_PROMPT", 200,
)
Config.TRUNCATION_DELEGATION_VOICED_SUMMARY = _trunc_value(
    "TRUNCATION_DELEGATION_VOICED_SUMMARY", 500,
)
Config.TRUNCATION_DELEGATION_BRIDGE_SUMMARY = _trunc_value(
    "TRUNCATION_DELEGATION_BRIDGE_SUMMARY", 600,
)
Config.TRUNCATION_DELEGATION_HISTORY_ROW = _trunc_value(
    "TRUNCATION_DELEGATION_HISTORY_ROW", 240,
)

# Group B — conversation history window. Toggle-affected.
Config.TRUNCATION_HISTORY_LIMIT = _trunc_value(
    "TRUNCATION_HISTORY_LIMIT", 20,
)
Config.TRUNCATION_HISTORY_CONTENT_MAX_CHARS = _trunc_value(
    "TRUNCATION_HISTORY_CONTENT_MAX_CHARS", 600,
)


def nas_root() -> str:
    """The house NAS mount, resolved at CALL time. "" = there is no NAS.

    ``Config.NAS_ROOT`` is the import-time snapshot of ``PALLI_NAS_ROOT`` —
    the declared knob — but a class attribute is frozen the moment this
    module loads, so a test (monkeypatch.setenv) or a live re-export would
    never be seen by the three consumers. The environment therefore wins
    whenever it is SET, and set to "" it means exactly that: no NAS. Unset,
    the snapshot answers (which is "" on a fresh install, and whatever
    .env / .env.local said on a configured one).
    """
    env = os.getenv("PALLI_NAS_ROOT")
    return (Config.NAS_ROOT if env is None else env).strip()


# Global configuration instance
config = Config()
