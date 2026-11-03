"""Who is thinking for PaLLi — the live picture, and how to move it.

Spec: docs/superpowers/specs/2026-09-03-provider-configurator-design.md §3.2, §3.4.
Two truths this module encodes, read out of the runtime on 2026-09-03:

* Her voice (the ReAct runner) takes its provider from LLM_PROVIDER and its
  model from LLM_RESPONSE_MODEL — .env.local, read once at boot — and never
  reads llm.routing.response. When LLM_RESPONSE_MODEL is absent, config
  defaults it to OPENAI_MODEL and that is what she runs (DEFECT_RECORD §22);
  only a present-but-empty key falls to the provider's own model key.
* Every other lane takes llm.routing.<lane> from data/palli.yaml first, then
  LLM_<LANE>_PROVIDER / _MODEL, then LLM_PROVIDER (ai/llm_routing.get_task_provider).

The picture is read straight from the files — .env then .env.local overriding,
the way python-dotenv loads them — never from core.config, so tests can use tmp
copies and the answer cannot depend on the calling process.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

import yaml

from core.llm_capabilities import BRAIN_LANE, LANES, PROVIDERS, Level, RouteVerdict, check_route
from core.settings_schema import PalliSettings, RoutingConfig
from core.settings_service import SettingsServiceError
from palli_config.envfile import atomic_write_text, parse_env, set_env_keys

# core/config.py: os.getenv("LLM_PROVIDER", "deepseek") — pinned by
# tests/palli_config/test_brain_picture.py::test_code_default_matches_core_config
CODE_DEFAULT_PROVIDER = "deepseek"

# core/config.py: os.getenv("OPENAI_MODEL", "gpt-4o") — what her voice runs when LLM_RESPONSE_MODEL
# is ABSENT (config defaults LLM_RESPONSE_MODEL to OPENAI_MODEL; dispatch.py:211 uses it first).
# Pinned by tests/palli_config/test_brain_picture.py::test_code_default_openai_model_matches_core_config
CODE_DEFAULT_OPENAI_MODEL = "gpt-4o"

ROUTING_LANES: tuple[str, ...] = tuple(RoutingConfig.model_fields)

# The env keys the runtime reads per lane (ai/llm_routing.TASK_PROVIDER_FIELDS /
# TASK_MODEL_FIELDS); narrative_author has none. Pinned by a canary in the tests.
ENV_LANE_PROVIDER_KEYS: dict[str, str] = {
    lane: f"LLM_{lane.upper()}_PROVIDER" for lane in ROUTING_LANES if lane != "narrative_author"
}
ENV_LANE_MODEL_KEYS: dict[str, str] = {
    lane: f"LLM_{lane.upper()}_MODEL" for lane in ROUTING_LANES if lane != "narrative_author"
}


class BrainError(ValueError):
    """A plain-English reason the request cannot be planned."""


@dataclass(frozen=True)
class LaneState:
    lane: str
    provider: str
    model: str
    source: str  # env | code_default | yaml | env_lane | env_default
    verdict: RouteVerdict

    @property
    def plain_name(self) -> str:
        return LANES[self.lane].plain_name

    @property
    def pair(self) -> tuple[str, str]:
        return (self.provider, self.model)


@dataclass(frozen=True)
class Picture:
    lanes: dict[str, LaneState]  # every routing lane, ROUTING_LANES order
    cadence_model: str           # raw CADENCE_MODEL; "" = the cadence judge follows her voice
    default_provider: str        # llm.default_provider: the fallback for callers with no routing row
    env: dict[str, str]
    yaml: dict[str, Any]

    @property
    def brain(self) -> LaneState:
        return self.lanes[BRAIN_LANE]


def read_env_files(*paths: Path | None) -> dict[str, str]:
    """Later files override earlier ones — python-dotenv loads .env, then .env.local."""
    env: dict[str, str] = {}
    for path in paths:
        if path is not None:
            env.update(parse_env(_read_env_text(Path(path))))
    return env


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _own_model(provider: str, env: Mapping[str, str]) -> str:
    cap = PROVIDERS.get(provider)
    return _clean(env.get(cap.model_env_key, "")) if cap else ""


def resolve_lane(lane: str, env: Mapping[str, str], yaml_doc: Mapping[str, Any]) -> LaneState:
    """One lane, resolved exactly as the runtime resolves it (module docstring)."""
    if lane == BRAIN_LANE:
        provider = _clean(env.get("LLM_PROVIDER", "")).lower()
        source = "env" if provider else "code_default"
        provider = provider or CODE_DEFAULT_PROVIDER
        raw = env.get("LLM_RESPONSE_MODEL")
        if raw is None:
            # Absent: core/config.py defaults LLM_RESPONSE_MODEL to OPENAI_MODEL and the runner
            # uses that first (dispatch.py:211) — DEFECT_RECORD §22's shape, shown, not hidden.
            model = _clean(env.get("OPENAI_MODEL", "")) or CODE_DEFAULT_OPENAI_MODEL
        else:
            # Present: the value, or (empty) the provider's own key, as _default_runner_model does.
            model = _clean(raw) or _own_model(provider, env)
        return LaneState(lane, provider, model, source, check_route(provider, lane, env=env))

    routing = (yaml_doc.get("llm") or {}).get("routing") or {}
    row = routing.get(lane) or {}
    if row.get("provider") and row.get("model"):
        provider, model = _clean(row["provider"]).lower(), _clean(row["model"])
        return LaneState(lane, provider, model, "yaml", check_route(provider, lane))

    env_provider = _clean(env.get(ENV_LANE_PROVIDER_KEYS.get(lane, ""), "")).lower()
    lane_model = _clean(env.get(ENV_LANE_MODEL_KEYS.get(lane, ""), ""))
    if env_provider:
        model = lane_model or _own_model(env_provider, env)
        return LaneState(lane, env_provider, model, "env_lane", check_route(env_provider, lane))

    provider = _clean(env.get("LLM_PROVIDER", "")).lower() or CODE_DEFAULT_PROVIDER
    model = lane_model or _own_model(provider, env)
    return LaneState(lane, provider, model, "env_default", check_route(provider, lane))


def load_picture(*, env_local: Path, palli_yaml: Path, env_base: Path | None = None) -> Picture:
    env = read_env_files(env_base, env_local)
    yaml_doc = yaml.safe_load(Path(palli_yaml).read_text(encoding="utf-8")) or {}
    lanes = {lane: resolve_lane(lane, env, yaml_doc) for lane in ROUTING_LANES}
    llm = yaml_doc.get("llm") or {}
    return Picture(
        lanes=lanes,
        cadence_model=_clean(env.get("PALLI_CADENCE_MODEL") or env.get("CADENCE_MODEL") or ""),
        default_provider=_clean(llm.get("default_provider")).lower(),
        env=env,
        yaml=yaml_doc,
    )


@dataclass(frozen=True)
class Target:
    name: str                             # the preset name, or "pin"
    lanes: dict[str, tuple[str, str]]     # every routing lane after preset ⊕ except ⊕ pins
    yaml_overrides: dict[str, Any]        # dot-paths for SettingsService.propose
    env_updates: dict[str, str]           # allowlisted brain keys for .env.local
    excepted: tuple[str, ...]

    def moving(self, picture: Picture) -> list[str]:
        return [lane for lane in ROUTING_LANES if self.lanes[lane] != picture.lanes[lane].pair]

    def staying(self, picture: Picture) -> list[str]:
        return [lane for lane in ROUTING_LANES if self.lanes[lane] == picture.lanes[lane].pair]

    def touches_brain(self) -> bool:
        return "LLM_PROVIDER" in self.env_updates or "LLM_RESPONSE_MODEL" in self.env_updates


def brain_env_updates(provider: str, model: str) -> dict[str, str]:
    """The `.env.local` lines that move HER VOICE to (provider, model).

    All three, every time. Her voice reads LLM_PROVIDER and LLM_RESPONSE_MODEL
    from the env and nothing else (module docstring), so a stale
    LLM_RESPONSE_MODEL left behind by the last provider would otherwise survive
    the move and be the model she actually ran. The provider's own model key
    goes with them so an unrouted caller lands on the same model.

    Shared with the setup API (first door, task 2, 2026-09-17), which writes
    exactly this set when a stranger pastes a key on the door — one rule, two
    surfaces.
    """
    return {
        "LLM_PROVIDER": provider,
        "LLM_RESPONSE_MODEL": model,
        PROVIDERS[provider].model_env_key: model,
    }


def _curated_models(settings: PalliSettings, provider: str) -> list[str] | None:
    """The model ids a CURATED provider lists in the yaml; None when the provider's models
    are discovered live (discovery: api) and so cannot be checked here."""
    from core.settings_schema import DiscoveryMode

    definition = settings.providers.get(provider)
    if definition is None or definition.discovery is not DiscoveryMode.CURATED:
        return None
    return [m.id for m in definition.models]


def plain_settings_error(exc: BaseException) -> str:
    """The human clause of a settings/env error — never pydantic's decoration.

    "proposed changes failed validation: 1 validation error for PalliSettings
      Value error, X [type=...]" becomes "X".
    """
    text = str(exc)
    if "Value error, " in text:
        text = text.split("Value error, ", 1)[1]
    text = text.split(" [type=", 1)[0]
    text = text.split("For further information", 1)[0]
    return " ".join(text.split()).rstrip(".") + "."


def resolve_target(
    picture: Picture,
    settings: PalliSettings,
    *,
    preset: str | None = None,
    except_lanes: Iterable[str] = (),
    pins: Mapping[str, tuple[str, str]] | None = None,
) -> Target:
    """Preset first, then `--except` removes lanes from it, then pins win (spec §3.4)."""
    lanes_list = ", ".join(ROUTING_LANES)
    excepted = tuple(_clean(lane).lower() for lane in except_lanes)
    for lane in excepted:
        if lane not in LANES:
            raise BrainError(f"{lane!r} is not one of her lanes. Lanes: {lanes_list}.")
    pins = {_clean(k).lower(): (_clean(p).lower(), _clean(m)) for k, (p, m) in dict(pins or {}).items()}
    for lane, (provider, model) in pins.items():
        if lane not in LANES:
            raise BrainError(f"{lane!r} is not one of her lanes. Lanes: {lanes_list}.")
        if provider not in PROVIDERS:
            raise BrainError(f"{provider!r} is not a provider PaLLi knows. Providers: {', '.join(PROVIDERS)}.")
        if not model:
            raise BrainError(f"name a model for {lane!r} as provider/model, e.g. claude/haiku.")
    for lane, (provider, model) in pins.items():
        # Ruling 17: a mistyped model must be refused HERE, naming the models on offer.
        # Left to the schema it comes back as a pydantic paragraph, which §3.2/§4 forbid.
        # Presets are validated when the yaml loads, so only pins need this.
        offered = _curated_models(settings, provider)
        if offered is not None and model not in offered:
            raise BrainError(
                f"{model!r} is not a model {PROVIDERS[provider].plain_name} offers. "
                f"Models: {', '.join(offered)}."
            )
    if preset is None and not pins:
        raise BrainError("nothing to do: name a preset, or --lane <lane> --to <provider>/<model>.")

    lanes = {lane: picture.lanes[lane].pair for lane in ROUTING_LANES}
    yaml_overrides: dict[str, Any] = {}
    env_updates: dict[str, str] = {}

    if preset is not None:
        definition = settings.presets.get(preset)
        if definition is None:
            raise BrainError(f"{preset!r} is not a preset. Presets: {', '.join(settings.presets)}.")
        for key, value in definition.overrides.items():
            lane = key.split(".")[-1] if key.startswith("llm.routing.") else None
            if lane is not None and lane in excepted:
                continue
            yaml_overrides[key] = value
            if lane is not None and isinstance(value, dict):
                lanes[lane] = (_clean(value.get("provider")).lower(), _clean(value.get("model")))
        env_updates.update(definition.env)
        if BRAIN_LANE in excepted:
            # Ruling 11 (2026-09-04): her voice is not moving, so .env.local is not touched at
            # all — the env half exists only to move her voice. (A provider's own model key is
            # therefore not written either; unrouted callers keep that provider's built-in default.)
            env_updates.clear()

    for lane, (provider, model) in pins.items():
        lanes[lane] = (provider, model)
        yaml_overrides[f"llm.routing.{lane}"] = {"provider": provider, "model": model}
        if lane == BRAIN_LANE:
            env_updates.update(brain_env_updates(provider, model))

    return Target(
        name=preset or "pin", lanes=lanes, yaml_overrides=yaml_overrides,
        env_updates=env_updates, excepted=excepted,
    )


@dataclass(frozen=True)
class Check:
    """One preflight line: a route verdict for a lane, or a liveness probe for a provider."""

    label: str
    level: "Level"
    detail: str
    lane: str | None = None
    suggestion: str | None = None


# ---------------------------------------------------------------------------
# Preflight · plan · apply · undo (spec §3.2 steps 2–7; plan finding 2 for undo)
# ---------------------------------------------------------------------------

ROLLBACK_FILENAME = "brain-rollback.json"


def _read_env_text(path: Path) -> str:
    """The file's own bytes, decoded — never `Path.read_text`.

    `.env.local` is CRLF. `read_text` opens in universal-newline mode and would
    hand back "\\n" endings, so `set_env_keys` (which takes the style from the
    text it is given) would rewrite the whole file LF and every untouched line
    would show up in a diff. Reading bytes keeps the style intact.
    """
    return path.read_bytes().decode("utf-8") if path.exists() else ""


def preflight(picture: Picture, target: Target, *, prober: Callable | None = None) -> list[Check]:
    """Probes first (one per distinct provider the moving lanes land on), then
    "her voice is not moving" when it isn't, then a line per moving lane whose
    route verdict is not OK. A failed probe is a WARN, never a BLOCK — a cold
    desktop must not stop a move away from it."""
    checks: list[Check] = []
    moving = target.moving(picture)
    env_after = {**picture.env, **target.env_updates}

    providers = list(dict.fromkeys(target.lanes[lane][0] for lane in moving))
    if providers and prober is not None:
        models = {p: next(target.lanes[l][1] for l in moving if target.lanes[l][0] == p) for p in providers}
        results = prober(providers, env_after, models) or {}
        for provider in providers:
            result = results.get(provider)
            name = PROVIDERS[provider].plain_name if provider in PROVIDERS else provider
            if result is None:
                # Ruling 16: a prober that skipped this one says nothing about it, and a
                # provider that vanishes from the list reads as a provider that is fine.
                checks.append(Check(name, Level.WARN, "no answer from the check"))
                continue
            checks.append(Check(name, Level.OK if result.ok else Level.WARN, result.status))

    if BRAIN_LANE not in moving:
        brain = picture.brain
        checks.append(Check("Her voice", Level.OK, f"not moving ({brain.model or brain.provider})", lane=BRAIN_LANE))

    for lane in moving:
        provider, _ = target.lanes[lane]
        verdict = check_route(provider, lane, env=env_after if lane == BRAIN_LANE else None)
        if verdict.level is Level.OK:
            continue
        label = "Her voice" if lane == BRAIN_LANE else LANES[lane].plain_name
        checks.append(Check(label, verdict.level, verdict.reason, lane=lane, suggestion=verdict.suggestion))
    return checks


@dataclass(frozen=True)
class Plan:
    target: Target
    checks: list[Check]
    change_id: str                       # "" when the yaml half is already in place
    yaml_diff: list[dict[str, Any]]      # ProposalResult.diff: {path, old, new}
    env_text_before: str
    env_text_after: str
    env_before: dict[str, str | None]    # pre-image of every touched key; None = absent

    @property
    def blocks(self) -> list[Check]:
        return [c for c in self.checks if c.level is Level.BLOCK]

    @property
    def is_noop(self) -> bool:
        return not self.change_id and self.env_text_after == self.env_text_before


def plan_writes(picture: Picture, target: Target, *, env_local: Path, settings_service: Any, checks: Iterable[Check]) -> Plan:
    """Render both halves in memory; write nothing (spec §3.2 step 3). The yaml
    half goes through SettingsService.propose, so a model the catalogue does not
    list fails HERE, before a byte moves."""
    env_path = Path(env_local)
    env_text = _read_env_text(env_path)
    current = parse_env(env_text)
    env_before = {key: current.get(key) for key in target.env_updates}
    env_after = set_env_keys(env_text, target.env_updates) if target.env_updates else env_text
    change_id, diff = "", []
    if target.yaml_overrides:
        proposal = settings_service.propose(target.yaml_overrides, reason=f"brain: {target.name}")
        change_id, diff = proposal.change_id, list(proposal.diff)
    return Plan(target, list(checks), change_id, diff, env_text, env_after, env_before)


def refuse_if_staged(git_status: Callable[[], str]) -> None:
    """`.env.local` is never committed. `git_status` returns the output of
    `git status --porcelain -- .env.local`; an index-column A/M/R/C means staged."""
    for line in str(git_status() or "").splitlines():
        if line[:1] in ("A", "M", "R", "C") and line.rstrip().endswith(".env.local"):
            raise BrainError(
                ".env.local is staged in git. Unstage it (`git restore --staged .env.local`) — it is never committed."
            )


@dataclass(frozen=True)
class Applied:
    rollback_path: Path
    wrote_env: bool
    wrote_yaml: bool
    rollback_token: str


def apply_plan(plan: Plan, *, env_local: Path, settings_service: Any, rollback_path: Path, git_status: Callable[[], str] | None = None) -> Applied:
    """Env first, then yaml (spec §3.2 step 5). If the yaml apply raises, the env
    half is put back before re-raising, so no half-state ever exists — and the
    service's own jargon is turned into plain English on the way out. Then the
    rollback record — both pre-images — at `rollback_path`, mode 600.

    Ruling 16 (2026-09-04): when neither half moved, the record is left exactly as
    it was and nothing is written. A re-run of the same command must never replace
    the way home with a pre-image of where he already is.
    """
    if git_status is not None:
        refuse_if_staged(git_status)
    env_path, record_path = Path(env_local), Path(rollback_path)
    wrote_env = plan.env_text_after != plan.env_text_before
    if wrote_env:
        atomic_write_text(env_path, plan.env_text_after)
    wrote_yaml, token = False, ""
    if plan.change_id:
        try:
            result = settings_service.apply(plan.change_id)
        except SettingsServiceError as exc:
            # The service's own words carry a change_id and "re-propose" — jargon for
            # a machine, not for him. The commonest way to land here by far is reading
            # the preview for longer than the 60 s proposal TTL (Ruling 16).
            if wrote_env:
                atomic_write_text(env_path, plan.env_text_before)
            if "expired" in str(exc):
                raise BrainError(
                    "the preview went stale — it is only good for a minute. "
                    "Run the command again."
                ) from exc
            raise BrainError(f"the change could not be applied: {exc}") from exc
        except Exception:
            if wrote_env:
                atomic_write_text(env_path, plan.env_text_before)
            raise
        wrote_yaml, token = True, str(getattr(result, "rollback_token", "") or "")
    if not wrote_env and not wrote_yaml:
        # Ruling 16: nothing moved, so the way home on disk stays what it was. Writing a
        # record here would overwrite the real pre-image with the CURRENT state, and the
        # next undo would report success, change nothing, and delete the way back.
        return Applied(record_path, False, False, "")
    record = {
        "applied_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "target": plan.target.name,
        "excepted": list(plan.target.excepted),
        "env_before": plan.env_before,
        "yaml_before": {entry["path"]: entry["old"] for entry in plan.yaml_diff},
        "settings_rollback_token": token,
    }
    record_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(record_path, json.dumps(record, indent=2) + "\n")
    try:
        os.chmod(record_path, 0o600)
    except OSError:
        pass
    return Applied(record_path, wrote_env, wrote_yaml, token)


def undo_preview(rollback_path: Path) -> str:
    """What --undo would put back, in plain words, without touching anything (final review F3)."""
    record_path = Path(rollback_path)
    if not record_path.exists():
        raise BrainError("nothing to undo: no change is on record.")
    record = json.loads(record_path.read_text(encoding="utf-8"))
    parts = [name for name, pre in ((".env.local", record.get("env_before")), ("data/palli.yaml", record.get("yaml_before"))) if pre]
    what = " and ".join(parts) if parts else "nothing"
    lanes = [key.split(".")[-1] for key in (record.get("yaml_before") or {}) if key.startswith("llm.routing.")]
    detail = f" ({len(lanes)} lane{'s' if len(lanes) != 1 else ''} in the yaml)" if lanes else ""
    return f"Dry run — nothing was written. Undo would put back {what}{detail}, reversing '{record.get('target', '')}' applied {record.get('applied_at', '?')}."


@dataclass(frozen=True)
class Undone:
    env_restored: bool
    yaml_restored: bool
    message: str


def undo(
    *,
    env_local: Path,
    settings_service: Any,
    rollback_path: Path,
    git_status: Callable[[], str] | None = None,
) -> Undone:
    """Reverse the last apply from its record: the yaml pre-image first (the half
    that can fail), re-proposed through the service, then the env pre-image (None
    deletes the key). Works from any process, never expires (plan finding 2).

    Ruling 15 (2026-09-04): yaml before env, the mirror of apply_plan's env before
    yaml. The yaml half is the one that can refuse — validation, a write error —
    and the env half is a plain atomic byte write, so failing first leaves nothing
    half-undone: .env.local as the apply left it, the record still on disk, a
    retry idempotent.

    Ruling 17 (2026-09-04): the staged-file guard applies to undo too — it writes
    .env.local exactly as apply does — and it runs before the record is read, so a
    refusal leaves the way home untouched.
    """
    if git_status is not None:
        refuse_if_staged(git_status)
    record_path = Path(rollback_path)
    if not record_path.exists():
        raise BrainError("nothing to undo: no change is on record.")
    record = json.loads(record_path.read_text(encoding="utf-8"))
    env_restored = yaml_restored = False

    yaml_before = record.get("yaml_before") or {}
    if yaml_before:
        proposal = settings_service.propose(yaml_before, reason=f"undo: {record.get('target', '')}")
        if proposal.change_id:
            settings_service.apply(proposal.change_id)
        # Ruling 16: only what actually moved. A pre-image that already matches the file
        # is not a restoration, and the sentence below must not claim one.
        yaml_restored = bool(proposal.change_id)

    env_before = record.get("env_before") or {}
    if env_before:
        env_path = Path(env_local)
        text = _read_env_text(env_path)
        restored = set_env_keys(text, env_before)
        env_restored = restored != text
        if env_restored:
            atomic_write_text(env_path, restored)

    record_path.unlink(missing_ok=True)
    parts = [name for name, did in ((".env.local", env_restored), ("data/palli.yaml", yaml_restored)) if did]
    what = " and ".join(parts) if parts else "nothing"
    return Undone(env_restored, yaml_restored, f"Put back {what}. Takes effect when PaLLi next restarts.")
