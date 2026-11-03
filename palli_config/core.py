"""Core configuration logic for the PaLLi config TUI.

Owns reading and writing .env.local and data/palli.yaml. Skins call into
this layer; they never touch the storage files directly.
"""
from __future__ import annotations

import copy
import os
import tempfile
from dataclasses import dataclass, field
from io import StringIO
from pathlib import Path

from ruamel.yaml import YAML

from core.llm_capabilities import PROVIDERS as CAPABILITIES
from palli_config.brain import (
    ENV_LANE_MODEL_KEYS,
    ENV_LANE_PROVIDER_KEYS,
    ROUTING_LANES,
    load_picture,
)

# Round-trip YAML loader/dumper. Preserves comments and quoting so we don't
# lose the institutional-knowledge comments in data/palli.yaml on save.
_RT_YAML = YAML(typ="rt")
_RT_YAML.preserve_quotes = True
_RT_YAML.indent(mapping=2, sequence=4, offset=2)
_RT_YAML.width = 4096  # never fold a long row (Ruling 8, 2026-09-04) — one writer fidelity with core/settings_service.py

# 2026-09-03 (configurator R4): the same ten lanes and seven providers the runtime
# has — from the schema and the capability matrix, never a private copy.
TASKS = ROUTING_LANES
PROVIDERS = tuple(CAPABILITIES)


@dataclass
class TaskConfig:
    provider: str = ""
    model: str = ""


@dataclass
class ProviderConfig:
    name: str
    api_key: str = ""
    base_url: str = ""
    default_model: str = ""
    enabled: bool = True


@dataclass
class ConfigState:
    tasks: dict[str, TaskConfig] = field(default_factory=dict)
    providers: dict[str, ProviderConfig] = field(default_factory=dict)
    disagreements: list[str] = field(default_factory=list)


def _parse_env_lines(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        out[key.strip()] = value.strip()
    return out


def _read_env_file(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    return _parse_env_lines(path.read_text(encoding="utf-8"))


def _read_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as f:
        return _RT_YAML.load(f) or {}


def load_state(*, env_local: Path, palli_yaml: Path) -> ConfigState:
    env = _read_env_file(env_local)
    yaml_data = _read_yaml(palli_yaml)
    routing = (yaml_data.get("llm") or {}).get("routing") or {}

    # 2026-09-03 (configurator R4): resolved the way the RUNTIME resolves it —
    # her voice from LLM_PROVIDER / LLM_RESPONSE_MODEL in env, every other lane
    # from the yaml first. The TUI used to prefer env for every lane, which the
    # runtime does not; its "disagreements" named the wrong winner.
    picture = load_picture(env_local=env_local, palli_yaml=palli_yaml)
    tasks: dict[str, TaskConfig] = {}
    disagreements: list[str] = []
    for task in TASKS:
        lane = picture.lanes[task]
        tasks[task] = TaskConfig(provider=lane.provider, model=lane.model)
        yaml_block = routing.get(task) or {}
        yaml_provider = str(yaml_block.get("provider", ""))
        yaml_model = str(yaml_block.get("model", ""))
        if task == "response":
            # Her voice's row is the runtime's truth (Ruling 10/18): an absent
            # LLM_RESPONSE_MODEL reads as the OpenAI default, which is what she runs.
            if yaml_provider and (yaml_provider, yaml_model) != (lane.provider, lane.model):
                disagreements.append(
                    f"task 'response': her voice runs env LLM_PROVIDER={lane.provider!r} / "
                    f"LLM_RESPONSE_MODEL={lane.model!r}; yaml routing.response says "
                    f"{yaml_provider!r} / {yaml_model!r} (the yaml is ignored for this lane)"
                )
            continue
        env_provider = env.get(ENV_LANE_PROVIDER_KEYS.get(task, ""), "")
        env_model = env.get(ENV_LANE_MODEL_KEYS.get(task, ""), "")
        if env_provider and yaml_provider and env_provider != yaml_provider:
            disagreements.append(
                f"task '{task}' provider: yaml={yaml_provider!r} wins; env={env_provider!r} is ignored"
            )
        if env_model and yaml_model and env_model != yaml_model:
            disagreements.append(
                f"task '{task}' model: yaml={yaml_model!r} wins; env={env_model!r} is ignored"
            )

    providers: dict[str, ProviderConfig] = {}
    for name in PROVIDERS:
        providers[name] = ProviderConfig(
            name=name,
            api_key=env.get(_API_KEY_ENV.get(name, ""), ""),
            base_url=env.get(_BASE_URL_ENV.get(name, ""), ""),
            default_model=env.get(_DEFAULT_MODEL_ENV.get(name, ""), ""),
            enabled=True,
        )
    return ConfigState(tasks=tasks, providers=providers, disagreements=disagreements)


_API_KEY_ENV = {
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
}
# Each provider's own default-model key, from the matrix (R4): seven, not four.
_DEFAULT_MODEL_ENV = {name: cap.model_env_key for name, cap in CAPABILITIES.items()}
# The real base-url keys (the old f"{NAME}_BASE_URL" invented LOCAL_OPENAI_BASE_URL,
# which nothing reads). The two CLIs have no base url.
_BASE_URL_ENV = {
    "openai": "OPENAI_BASE_URL",
    "anthropic": "ANTHROPIC_BASE_URL",
    "deepseek": "DEEPSEEK_BASE_URL",
    "local_openai": "LOCAL_LLM_API_BASE",
    "ollama": "OLLAMA_BASE_URL",
}


def _atomic_write(path: Path, content: str) -> None:
    """Write content to a temp file in the same directory, then rename.

    Renames are atomic on POSIX and on Windows when both files are on the
    same filesystem. Crash-safe — either the new content fully lands or
    the old file remains untouched.
    """
    parent = path.parent
    parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix=path.name, suffix=".tmp", dir=str(parent))
    try:
        # newline="" writes the string's own endings untranslated (Ruling 19). With the
        # default, "\n" would become os.linesep and a CRLF render would double up.
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


_MANAGED_BLOCK_HEADER = "# --- managed by palli_configure (do not edit by hand) ---"
_MANAGED_BLOCK_FOOTER = "# --- end managed block ---"


def _strip_managed_block(lines: list[str]) -> list[str]:
    """Drop any prior palli_configure-managed block from a list of lines.

    Without this, save -> load -> save would accumulate a stale, empty
    header/footer pair on every cycle (writer/reader drift).
    """
    out: list[str] = []
    in_block = False
    for raw_line in lines:
        stripped = raw_line.strip()
        if not in_block and stripped == _MANAGED_BLOCK_HEADER:
            in_block = True
            continue
        if in_block:
            if stripped == _MANAGED_BLOCK_FOOTER:
                in_block = False
            continue
        out.append(raw_line)
    # If we never closed the block (file truncated mid-block), drop trailing.
    return out


def _render_env_local(state: ConfigState, original_text: str) -> str:
    """Produce the new .env.local content, preserving unrelated lines."""
    managed_keys: set[str] = {"LLM_PROVIDER"}
    for task in TASKS:
        if task in ENV_LANE_PROVIDER_KEYS:
            managed_keys.add(ENV_LANE_PROVIDER_KEYS[task])
            managed_keys.add(ENV_LANE_MODEL_KEYS[task])
    for prov in PROVIDERS:
        if prov in _API_KEY_ENV:
            managed_keys.add(_API_KEY_ENV[prov])
        if prov in _DEFAULT_MODEL_ENV:
            managed_keys.add(_DEFAULT_MODEL_ENV[prov])
        if prov in _BASE_URL_ENV:
            managed_keys.add(_BASE_URL_ENV[prov])

    # Ruling 19 (2026-09-04): which managed keys the file already carried — read from
    # the WHOLE original, the previous managed block included, so a save is idempotent.
    # core/config.py reads present-but-empty and absent differently, so a key that was
    # there must still be there afterwards even when its value is empty.
    present_managed = managed_keys & {
        line.split("=", 1)[0].strip()
        for line in (raw_line.strip() for raw_line in original_text.splitlines())
        if line and not line.startswith("#") and "=" in line
    }

    # Strip any previously-emitted managed block so we don't accumulate
    # stale header/footer markers on repeated saves.
    source_lines = _strip_managed_block(original_text.splitlines())

    # Keep all lines that aren't keys we manage.
    preserved_lines: list[str] = []
    for raw_line in source_lines:
        line = raw_line.strip()
        if not line or line.startswith("#"):
            preserved_lines.append(raw_line)
            continue
        if "=" not in line:
            preserved_lines.append(raw_line)
            continue
        key = line.split("=", 1)[0].strip()
        if key not in managed_keys:
            preserved_lines.append(raw_line)

    # Trim trailing blank lines from the preserved section so the gap
    # before the managed block is always exactly one blank line.
    while preserved_lines and preserved_lines[-1].strip() == "":
        preserved_lines.pop()

    # Append managed assignments.
    managed_lines: list[str] = []
    managed_lines.append(_MANAGED_BLOCK_HEADER)
    # Her voice reads LLM_PROVIDER, never LLM_RESPONSE_PROVIDER (spec §3.4). Until
    # 2026-09-03 this block never wrote it, so the TUI could not move her voice.
    response = state.tasks["response"]
    if response.provider:
        managed_lines.append(f"LLM_PROVIDER={response.provider}")
    for task in TASKS:
        tc = state.tasks[task]
        if task not in ENV_LANE_PROVIDER_KEYS:
            continue  # narrative_author: no env key exists; the yaml row carries it
        if tc.provider:
            managed_lines.append(f"{ENV_LANE_PROVIDER_KEYS[task]}={tc.provider}")
        if tc.model:
            managed_lines.append(f"{ENV_LANE_MODEL_KEYS[task]}={tc.model}")
    for prov_name in PROVIDERS:
        pc = state.providers[prov_name]
        if pc.api_key and prov_name in _API_KEY_ENV:
            managed_lines.append(f"{_API_KEY_ENV[prov_name]}={pc.api_key}")
        if pc.default_model and prov_name in _DEFAULT_MODEL_ENV:
            managed_lines.append(f"{_DEFAULT_MODEL_ENV[prov_name]}={pc.default_model}")
        if pc.base_url and prov_name in _BASE_URL_ENV:
            managed_lines.append(f"{_BASE_URL_ENV[prov_name]}={pc.base_url}")
    # Ruling 19: nothing that was in the file leaves it. A managed key whose value is
    # empty is written back empty rather than dropped — present-empty is not absent.
    emitted = {line.split("=", 1)[0] for line in managed_lines if "=" in line}
    for key in sorted(present_managed - emitted):
        managed_lines.append(f"{key}=")
    managed_lines.append(_MANAGED_BLOCK_FOOTER)
    # Keep the file's own newline style (.env.local is CRLF — plan finding 6).
    newline = "\r\n" if "\r\n" in original_text else "\n"
    return newline.join(preserved_lines + [""] + managed_lines) + newline


def _render_palli_yaml(state: ConfigState, original_yaml: dict, edited: set[str]) -> str:
    """Produce the new palli.yaml content with updated llm.routing block.

    Uses ruamel.yaml round-trip mode so comments + key order in the original
    survive the rewrite.

    Ruling 19 (2026-09-04): only the lanes he actually changed this session are
    touched. An unedited row is the yaml's own value — or, for her voice, env-governed
    and merely RESOLVED for display — and rewriting it from that resolution would
    silently move what ai/llm_client.py reads back through get_task_model(). A lane
    is written whole or removed: never the half-row the schema refuses.
    """
    new_yaml = copy.deepcopy(original_yaml)
    llm = new_yaml.setdefault("llm", {})
    routing = llm.setdefault("routing", {})
    for task in TASKS:
        if task not in edited:
            continue
        tc = state.tasks[task]
        if not (tc.provider and tc.model):
            routing.pop(task, None)
            continue
        block = routing.get(task)
        if not isinstance(block, dict):
            # Absent, or present as an explicit null — either way there is no row to
            # keep. (An existing map is reused so its inner comments survive.)
            block = {}
            routing[task] = block
        block["provider"] = tc.provider
        block["model"] = tc.model
    buf = StringIO()
    _RT_YAML.dump(new_yaml, buf)
    return buf.getvalue()


def save_all(buf, *, env_local: Path, palli_yaml: Path) -> None:
    """Atomically write all staged changes to env.local + palli.yaml.

    Renders both files in memory first; if either render fails, neither
    file gets written. Then writes both via temp-file + rename so a crash
    mid-write leaves the original intact.
    """
    # Local import to avoid circular dependency: buffer.py imports from core.
    from palli_config.buffer import ConfigBuffer  # noqa: F401

    state = buf.effective_state()
    # Ruling 19: the lanes he actually moved this session — the yaml half writes no
    # others. The env half is unchanged (Ruling 18): it still states every lane.
    edited = {change.target for change in buf.pending_changes() if change.kind == "task"}
    # Bytes, not read_text: universal-newline mode would hand back "\n" endings and
    # _render_env_local could no longer see the file's real style (plan finding 6).
    original_env = env_local.read_bytes().decode("utf-8") if env_local.exists() else ""
    original_yaml = _read_yaml(palli_yaml)
    new_env_text = _render_env_local(state, original_env)
    new_yaml_text = _render_palli_yaml(state, original_yaml, edited)
    _atomic_write(env_local, new_env_text)
    _atomic_write(palli_yaml, new_yaml_text)
