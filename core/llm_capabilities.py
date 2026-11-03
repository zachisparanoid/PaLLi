"""The capability matrix — what each provider can do, what each lane needs.

A GATE, deterministic on purpose (owner rule 2026-07-16: meaning decisions go
to an LLM; gates, budgets and denylists stay in code). It describes what THIS
CODEBASE can do with a provider — which wire dialect core/agent/dispatch.py
reaches it by, whether it can emit a real tool call — so no config surface can
offer a combination the runtime cannot honour. Spec:
docs/superpowers/specs/2026-09-03-provider-configurator-design.md §3.1, §3.6.

No project imports, on purpose: core/settings_schema.py imports the preset env
allowlist from here, and tests/core/test_llm_capabilities_canary.py holds this
table to what ai/llm_provider_registry.py and core/agent/dispatch.py do.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Mapping


class Auth(str, Enum):
    API_KEY = "api_key"
    CLI_LOGIN = "cli_login"
    NONE = "none"


class Dialect(str, Enum):
    """How core/agent/dispatch.py::_get_openai_compat_client reaches the provider."""

    OPENAI_COMPAT = "openai_compat"  # Path 1: a real AsyncOpenAI client, native tool calls
    ANTHROPIC = "anthropic"          # the native call_anthropic branch (the parked build)
    SDK_ADAPTER = "sdk_adapter"      # Path 2: PaLLiProviderSDKAdapter, <tool_call> text tags
    UNSUPPORTED = "unsupported"      # dispatch would hand the runner the wrong client


class ToolCalls(str, Enum):
    NATIVE = "native"
    EMULATED = "emulated"
    NONE = "none"


class Governance(str, Enum):
    ENV_ONLY = "env_only"  # her brain: LLM_PROVIDER + LLM_RESPONSE_MODEL in .env.local
    YAML = "yaml"          # llm.routing.<lane> in data/palli.yaml


class Level(str, Enum):
    OK = "ok"
    WARN = "warn"
    BLOCK = "block"


@dataclass(frozen=True)
class ProviderCapability:
    auth: Auth
    runner_dialect: Dialect
    tool_calls: ToolCalls
    streaming: bool
    vision: bool
    model_env_key: str  # the env var that is this provider's own default model
    plain_name: str     # "Claude Code (your subscription)" — what a person reads (§3.6)
    cost: str           # "no dollar cost · counts against your Max weekly quota"
    cost_kind: str      # per_token | subscription | free — what "what this means" compares
    note: str = ""      # the plain-English reason a refusal prints; starts lowercase
    plan: str = ""      # where the build that would unblock it is written up.
                        # Appended to check_route's reason (the CLI, for him);
                        # never shown on the door, where a stranger has no use
                        # for a path into somebody else\'s plans (sweep 09-18).


@dataclass(frozen=True)
class LaneRequirement:
    needs_tool_calls: bool
    needs_vision: bool
    governed_by: Governance
    severity: str    # mirrors ai/llm_routing.TASK_ROUTING_SEVERITY
    plain_name: str  # "What she keeps and recalls" (§3.6)


@dataclass(frozen=True)
class RouteVerdict:
    level: Level
    reason: str
    suggestion: str | None = None  # a provider key; the renderer turns it into a name

    @property
    def ok(self) -> bool:
        return self.level is Level.OK

    @property
    def blocked(self) -> bool:
        return self.level is Level.BLOCK


BRAIN_LANE = "response"

_EMULATED = (
    "cannot make real tool calls. PaLLi fakes them by asking the model to type "
    "<tool_call> tags into its answer, and the one time that was measured "
    "(2026-09-02) it produced none at all. She would talk to you and never be "
    "able to act."
)

PROVIDERS: dict[str, ProviderCapability] = {
    "deepseek": ProviderCapability(
        auth=Auth.API_KEY, runner_dialect=Dialect.OPENAI_COMPAT, tool_calls=ToolCalls.NATIVE,
        streaming=True, vision=False, model_env_key="DEEPSEEK_MODEL",
        plain_name="DeepSeek V4", cost="pay per token · cheap, fast", cost_kind="per_token",
    ),
    "openai": ProviderCapability(
        auth=Auth.API_KEY, runner_dialect=Dialect.OPENAI_COMPAT, tool_calls=ToolCalls.NATIVE,
        streaming=True, vision=True, model_env_key="OPENAI_MODEL",
        plain_name="OpenAI", cost="pay per token", cost_kind="per_token",
    ),
    "anthropic": ProviderCapability(
        auth=Auth.API_KEY, runner_dialect=Dialect.UNSUPPORTED, tool_calls=ToolCalls.NATIVE,
        streaming=True, vision=False, model_env_key="ANTHROPIC_MODEL",
        plain_name="Claude (API)",
        cost="pay per token · the most capable, the most expensive", cost_kind="per_token",
        # Plain English, 2026-09-18: this sentence is on a stranger's first
        # screen, under a button they cannot press. It used to name a source
        # file and an internal build document, which tells them nothing they
        # can act on and reads like somebody else's notebook.
        note=(
            "cannot be her voice yet — the code that would talk to it is only half "
            "built, so she would be handed a client she cannot use. Pick another "
            "brain for now; Claude by API is planned."
        ),
        plan="docs/superpowers/plans/2026-09-02-anthropic-brain-kickoff.md",
    ),
    "claude": ProviderCapability(
        auth=Auth.CLI_LOGIN, runner_dialect=Dialect.SDK_ADAPTER, tool_calls=ToolCalls.EMULATED,
        streaming=False, vision=True, model_env_key="CLAUDE_CLI_MODEL",
        plain_name="Claude Code (your subscription)",
        cost="no dollar cost · counts against your Claude Max weekly quota", cost_kind="subscription",
        note=_EMULATED,
    ),
    "codex": ProviderCapability(
        auth=Auth.CLI_LOGIN, runner_dialect=Dialect.SDK_ADAPTER, tool_calls=ToolCalls.EMULATED,
        streaming=False, vision=False, model_env_key="CODEX_MODEL",
        plain_name="Codex (your subscription)",
        cost="no dollar cost · counts against your ChatGPT quota", cost_kind="subscription",
        note=_EMULATED,
    ),
    "local_openai": ProviderCapability(
        auth=Auth.NONE, runner_dialect=Dialect.OPENAI_COMPAT, tool_calls=ToolCalls.NATIVE,
        streaming=True, vision=False, model_env_key="LOCAL_LLM_MODEL",
        plain_name="Ollama", cost="free · private · ~72 words/sec", cost_kind="free",
    ),
    "ollama": ProviderCapability(
        auth=Auth.NONE, runner_dialect=Dialect.SDK_ADAPTER, tool_calls=ToolCalls.EMULATED,
        streaming=False, vision=True, model_env_key="OLLAMA_MODEL",
        plain_name="Ollama (native API)",
        cost="free · private · cannot use her hands", cost_kind="free",
        note=(
            _EMULATED
            + " Use \"Ollama\" instead: the same machine and the same model "
            "through its OpenAI-style door, where her tool calls are real."
        ),
    ),
}

LANES: dict[str, LaneRequirement] = {
    "response": LaneRequirement(
        needs_tool_calls=True, needs_vision=False, governed_by=Governance.ENV_ONLY,
        severity="error", plain_name="Her voice — the live conversation",
    ),
    "decision": LaneRequirement(
        needs_tool_calls=False, needs_vision=False, governed_by=Governance.YAML,
        severity="error", plain_name="Whether to reply at all",
    ),
    "analysis": LaneRequirement(
        needs_tool_calls=False, needs_vision=False, governed_by=Governance.YAML,
        severity="error", plain_name="Reading what you said",
    ),
    "appraisal": LaneRequirement(
        needs_tool_calls=False, needs_vision=False, governed_by=Governance.YAML,
        severity="warning", plain_name="What mattered, after a reply",
    ),
    "memory": LaneRequirement(
        needs_tool_calls=False, needs_vision=False, governed_by=Governance.YAML,
        severity="warning", plain_name="What she keeps and recalls",
    ),
    "proactive": LaneRequirement(
        needs_tool_calls=False, needs_vision=False, governed_by=Governance.YAML,
        severity="warning", plain_name="Whether to speak first",
    ),
    "reflection": LaneRequirement(
        needs_tool_calls=False, needs_vision=False, governed_by=Governance.YAML,
        severity="warning", plain_name="What she thinks about later",
    ),
    "web_search": LaneRequirement(
        needs_tool_calls=False, needs_vision=False, governed_by=Governance.YAML,
        severity="info", plain_name="Reading the web",
    ),
    "narrative_author": LaneRequirement(
        needs_tool_calls=False, needs_vision=False, governed_by=Governance.YAML,
        severity="warning", plain_name="The story of her own days",
    ),
    "vision": LaneRequirement(
        needs_tool_calls=False, needs_vision=True, governed_by=Governance.YAML,
        severity="info", plain_name="Looking at pictures you send",
    ),
}

# The env keys a preset may carry (spec §3.3 R2): a preset chooses a brain, it
# never carries an identity. Anything credential-shaped is refused BY NAME so a
# new provider cannot widen this by accident.
BRAIN_ENV_KEYS: frozenset[str] = frozenset(
    {"LLM_PROVIDER", "LLM_RESPONSE_MODEL", "CADENCE_MODEL"}
    | {cap.model_env_key for cap in PROVIDERS.values()}
)
FORBIDDEN_ENV_FRAGMENTS: tuple[str, ...] = ("KEY", "TOKEN", "SECRET", "PASSWORD")


def env_key_allowed_in_preset(key: str) -> bool:
    name = str(key or "")
    upper = name.upper()
    if any(fragment in upper for fragment in FORBIDDEN_ENV_FRAGMENTS):
        return False
    return name in BRAIN_ENV_KEYS


def suggest_brain_provider(blocked: str) -> str:
    """A provider that CAN drive her brain: the same cost kind if one exists,
    else DeepSeek (what she runs today)."""
    kind = PROVIDERS[blocked].cost_kind if blocked in PROVIDERS else ""
    for name, cap in PROVIDERS.items():
        if (
            cap.cost_kind == kind
            and cap.tool_calls is ToolCalls.NATIVE
            and cap.runner_dialect is not Dialect.UNSUPPORTED
        ):
            return name
    return "deepseek"


def check_route(
    provider: str, lane: str, *, env: Mapping[str, str] | None = None
) -> RouteVerdict:
    """Is (provider, lane) something the runtime can honour?

    Rules, in order (spec §3.1): unknown lane/provider → BLOCK; the brain lane
    on an unwired dialect or a non-native tool caller → BLOCK; a vision lane
    on a provider without eyes → WARN; the brain lane with LLM_RESPONSE_MODEL
    absent from `env` (the runtime runs the OpenAI default — DEFECT_RECORD
    §22) or present-but-empty with the provider's own key unset → WARN; else
    OK.
    """
    provider_key = str(provider or "").strip().lower()
    lane_key = str(lane or "").strip().lower()
    requirement = LANES.get(lane_key)
    if requirement is None:
        return RouteVerdict(Level.BLOCK, f"{lane_key!r} is not one of her lanes.")
    cap = PROVIDERS.get(provider_key)
    if cap is None:
        return RouteVerdict(Level.BLOCK, f"{provider_key!r} is not a provider PaLLi knows.")

    who = cap.plain_name
    if requirement.needs_tool_calls:
        if cap.runner_dialect is Dialect.UNSUPPORTED or cap.tool_calls is not ToolCalls.NATIVE:
            note = cap.note or "cannot make real tool calls."
            if cap.plan:
                note = f"{note} The build that finishes it: {cap.plan}."
            return RouteVerdict(
                Level.BLOCK, f"{who} {note}", suggestion=suggest_brain_provider(provider_key)
            )
    if requirement.needs_vision and not cap.vision:
        return RouteVerdict(
            Level.WARN, f"{who} cannot look at pictures; this lane would quietly stop working."
        )
    if env is not None and lane_key == BRAIN_LANE:
        # Mirrors core/config.py + core/agent/dispatch.py:211. LLM_RESPONSE_MODEL ABSENT → config
        # defaults it to OPENAI_MODEL and the runner uses THAT (DEFECT_RECORD §22's shape);
        # PRESENT BUT EMPTY → _default_runner_model(provider) → the provider's own key.
        if "LLM_RESPONSE_MODEL" not in env:
            if provider_key != "openai":
                return RouteVerdict(
                    Level.WARN,
                    f"{who} has no model named for her voice, so she would run the OpenAI "
                    "default model instead (DEFECT_RECORD §22).",
                )
        elif not str(env.get("LLM_RESPONSE_MODEL", "") or "").strip():
            if not str(env.get(cap.model_env_key, "") or "").strip():
                return RouteVerdict(
                    Level.WARN,
                    f"{who} has no model named for her voice; she would run PaLLi's built-in "
                    "default for it (DEFECT_RECORD §22).",
                )
    return RouteVerdict(Level.OK, "")
