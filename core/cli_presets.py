"""CLI preset flags applied AFTER core.config imports.

Why this exists: `core.config` calls `load_dotenv(..., override=True)`
at import time, which clobbers any `os.environ` values set earlier in
the process. So CLI overrides have to land AFTER config has finished
loading, and they have to mutate the config singleton directly (not
just os.environ — downstream code reads `config.X`, not `os.getenv(X)`).

Usage from `main.py`:

    from core.config import config
    from core.cli_presets import parse_and_apply
    parse_and_apply()  # mutates `config` attributes in place

Two flags currently supported:
- `--more-perception`: turn the desktop activity watcher up — finer poll,
  longer rolling window, broader file lookback, more recent files in
  summaries. Just makes her see more of the machine. Doesn't change how
  often she speaks.
- `--more-chatty`: lower the bars for proactive outreach — confidence
  threshold, gap floors, daily cap, randomness boosts. Independent of
  perception. Just makes her speak more.

A third combined alias `--more-everything` (or `--max`) applies both
presets.

Each preset is a flat dict of {config_attr_name: typed_value}. The
applier mutates the singleton; values persist for the life of the
process. .env stays untouched, so the next un-flagged run goes back to
baseline.

Implementation notes:
- Logs to stderr (the daily log file isn't configured yet at this point
  in startup) so the user sees which overrides applied.
- Does NOT modify sys.argv — keeps parsing additive so other CLI
  consumers downstream see the original argv.
- Both os.environ AND the config attribute are written, so callers that
  read `os.environ` or re-instantiate Config later see consistent values.
"""
from __future__ import annotations

import os
import sys
from typing import Any, Dict, List, Tuple


# --- Preset definitions ----------------------------------------------------
#
# The values are the runtime types config attributes expect. parse_and_apply
# converts to str for os.environ but writes the typed value onto config.

MORE_PERCEPTION: Dict[str, Any] = {
    "DESKTOP_ACTIVITY_POLL_SECONDS": 15.0,
    "DESKTOP_ACTIVITY_WINDOW_SECONDS": 3600.0,
    "DESKTOP_ACTIVITY_FILE_LOOKBACK_SECONDS": 1800.0,
    "DESKTOP_ACTIVITY_MAX_RECENT_FILES": 20,
}

MORE_CHATTY: Dict[str, Any] = {
    "PROACTIVE_CONFIDENCE_THRESHOLD": 0.40,
    "MAX_DAILY_PROACTIVE_MESSAGES": 12,
    "PROACTIVE_ABSOLUTE_MIN_GAP_MINUTES": 30,
    "MAX_PROACTIVE_FREQUENCY_HOURS": 1.0,
    "MIN_CONVERSATION_GAP_HOURS": 0.25,
    "PROACTIVE_CHECK_INTERVAL_MINUTES": 5,
    "PROACTIVE_RANDOMNESS_LOW_CHANCE": 0.40,
    "PROACTIVE_RANDOMNESS_MED_CHANCE": 0.55,
    "PROACTIVE_RANDOMNESS_HIGH_CHANCE": 0.65,
}

# Inverse presets — dial PaLLi DOWN from .env baseline. Useful when she
# feels too eager or too watchful for the current moment.

LESS_PERCEPTION: Dict[str, Any] = {
    "DESKTOP_ACTIVITY_POLL_SECONDS": 60.0,
    "DESKTOP_ACTIVITY_WINDOW_SECONDS": 900.0,
    "DESKTOP_ACTIVITY_FILE_LOOKBACK_SECONDS": 300.0,
    "DESKTOP_ACTIVITY_MAX_RECENT_FILES": 4,
}

LESS_CHATTY: Dict[str, Any] = {
    "PROACTIVE_CONFIDENCE_THRESHOLD": 0.70,
    "MAX_DAILY_PROACTIVE_MESSAGES": 4,
    "PROACTIVE_ABSOLUTE_MIN_GAP_MINUTES": 180,
    "MAX_PROACTIVE_FREQUENCY_HOURS": 4.0,
    "MIN_CONVERSATION_GAP_HOURS": 1.0,
    "PROACTIVE_CHECK_INTERVAL_MINUTES": 30,
    "PROACTIVE_RANDOMNESS_LOW_CHANCE": 0.10,
    "PROACTIVE_RANDOMNESS_MED_CHANCE": 0.20,
    "PROACTIVE_RANDOMNESS_HIGH_CHANCE": 0.25,
}


def _emit(message: str) -> None:
    """Write a startup notice to stderr (logging isn't fully configured yet)."""
    try:
        sys.stderr.write(f"[cli_presets] {message}\n")
        sys.stderr.flush()
    except Exception:
        pass


def _apply(preset_name: str, overrides: Dict[str, Any]) -> List[Tuple[str, Any, Any]]:
    """Apply a typed override dict to BOTH os.environ AND the config singleton."""
    # Local import — keeps this module importable before config is ready
    # (used by tests). Real callers must import config first.
    from core.config import config

    applied: List[Tuple[str, Any, Any]] = []
    for key, value in overrides.items():
        old = getattr(config, key, "(missing)")
        os.environ[key] = str(value)
        try:
            setattr(config, key, value)
        except Exception as exc:
            _emit(f"  WARN: could not setattr config.{key}: {exc}")
            continue
        applied.append((key, old, value))
    _emit(f"applied preset {preset_name}: {len(applied)} config overrides")
    for key, old, new in applied:
        _emit(f"  config.{key}: {old!r} -> {new!r}")
    return applied


def parse_and_apply(argv: List[str] | None = None) -> Dict[str, bool]:
    """Parse PaLLi CLI presets from argv and apply overrides to the config singleton.

    Returns a dict mapping flag names to whether they were applied. Safe
    to call multiple times — each application is idempotent in practice
    because the override values are constants.

    The argv parser is intentionally lightweight (no argparse) to avoid
    extra import surface this early in startup. Flags can appear in any
    order. Unknown args are ignored — they belong to other layers
    downstream.
    """
    args = list(argv if argv is not None else sys.argv[1:])
    flags = {
        "more_perception": False,
        "more_chatty": False,
        "more_everything": False,
        "less_perception": False,
        "less_chatty": False,
        "less_everything": False,
    }
    for arg in args:
        token = arg.strip().lower()
        if token in {"--more-perception", "--more_perception"}:
            flags["more_perception"] = True
        elif token in {"--more-chatty", "--more_chatty"}:
            flags["more_chatty"] = True
        elif token in {"--more-everything", "--more_everything", "--max"}:
            flags["more_everything"] = True
        elif token in {"--less-perception", "--less_perception"}:
            flags["less_perception"] = True
        elif token in {"--less-chatty", "--less_chatty"}:
            flags["less_chatty"] = True
        elif token in {"--less-everything", "--less_everything", "--quiet", "--min"}:
            flags["less_everything"] = True

    if flags["more_everything"]:
        flags["more_perception"] = True
        flags["more_chatty"] = True
    if flags["less_everything"]:
        flags["less_perception"] = True
        flags["less_chatty"] = True

    if flags["more_perception"]:
        _apply("--more-perception", MORE_PERCEPTION)
    if flags["more_chatty"]:
        _apply("--more-chatty", MORE_CHATTY)
    if flags["less_perception"]:
        _apply("--less-perception", LESS_PERCEPTION)
    if flags["less_chatty"]:
        _apply("--less-chatty", LESS_CHATTY)

    return flags
