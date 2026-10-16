"""her_voice pack (Batch 7): how she sounds in ordinary moments, measured
before and after the batch changes her wording.

The code ships; the scenarios do not. They are written from the owner's own
conversations (paraphrased, with stand-in names) and live in a private,
git-ignored file: evals/probes/private/her_voice_scenarios.json. A fresh
clone or CI has no such file, so this pack offers no scenarios and the
battery skips it.

The file is a JSON list of scenarios (or {"scenarios": [...]}). Each one:

  {"id": str, "class": str, "notes": str (optional, for people),
   "flags": {...} (optional, merged over she_decides + duplex ON),
   "seed": {...} (optional, the Seed fields, e.g. "conversations"),
   "turns": [{"send": str, "must_contain": [...], "must_not_contain": [...],
              "matches": str, "not_matches": str, "expect_tool": str,
              "expect_quiet": bool}],      (every key but "send" optional)
   "rubric": str}                           (required: what passes, what fails)

The class becomes the scenario's family. A malformed file raises ValueError
(wrong keys, wrong types, a regex that does not compile): a broken private
file must fail loudly, never shrink or weaken the battery quietly.
"""
from __future__ import annotations

import json
import re
from dataclasses import fields
from pathlib import Path

from schema import Judge, ProbeScenario, ProbeTurn, Seed

PACK = "her_voice"
PRIVATE_PATH = Path(__file__).resolve().parents[3] / "evals/probes/private/her_voice_scenarios.json"

# Both are on in her live settings, so every her_voice scenario runs with them.
_LIVE_FLAGS = {"she_decides": True, "duplex": True}

_SCENARIO_KEYS = {"id", "class", "notes", "flags", "seed", "turns", "rubric"}
_TURN_KEYS = {"send", "must_contain", "must_not_contain", "matches", "not_matches",
              "expect_tool", "expect_quiet"}


_SEED_FIELDS = {f.name for f in fields(Seed)}


def _text(raw: dict, key: str, where: str, *, required: bool = False) -> str | None:
    value = raw.get(key)
    if value is None and not required:
        return None
    if not isinstance(value, str) or (required and not value.strip()):
        raise ValueError(f"{where}: '{key}' must be a{' non-empty' if required else ''} string")
    return value


def _needles(raw: dict, key: str, where: str) -> tuple[str, ...]:
    value = raw.get(key)
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(v, str) and v for v in value):
        raise ValueError(f"{where}: '{key}' must be a list of non-empty strings")
    return tuple(value)


def _regex(raw: dict, key: str, where: str) -> str | None:
    value = _text(raw, key, where)
    if value is not None:
        try:
            re.compile(value, re.IGNORECASE | re.MULTILINE)
        except re.error as exc:
            raise ValueError(f"{where}: '{key}' does not compile ({exc})") from exc
    return value


def _turn(raw: object, where: str) -> ProbeTurn:
    if not isinstance(raw, dict):
        raise ValueError(f"{where}: a turn must be an object")
    unknown = set(raw) - _TURN_KEYS
    if unknown:
        raise ValueError(f"{where}: unknown turn keys {sorted(unknown)}")
    quiet = raw.get("expect_quiet")
    if quiet is not None and not isinstance(quiet, bool):
        raise ValueError(f"{where}: expect_quiet must be true, false or absent")
    return ProbeTurn(
        send=_text(raw, "send", where, required=True),
        must_contain=_needles(raw, "must_contain", where),
        must_not_contain=_needles(raw, "must_not_contain", where),
        matches=_regex(raw, "matches", where),
        not_matches=_regex(raw, "not_matches", where),
        expect_tool=_text(raw, "expect_tool", where),
        expect_quiet=quiet,
    )


def _seed(raw: object, where: str) -> Seed | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError(f"{where}: 'seed' must be an object")
    unknown = set(raw) - _SEED_FIELDS
    if unknown:
        raise ValueError(f"{where}: unknown seed fields {sorted(unknown)}")
    for key, value in raw.items():
        if not isinstance(value, list) or not all(isinstance(v, dict) for v in value):
            raise ValueError(f"{where}: seed '{key}' must be a list of objects")
    return Seed(**raw)


def _flags(raw: object, where: str) -> dict:
    if raw is None:
        return {}
    if not isinstance(raw, dict) or not all(isinstance(k, str) and isinstance(v, bool) for k, v in raw.items()):
        raise ValueError(f"{where}: 'flags' must map flag names to true/false")
    return dict(raw)


def _scenario(raw: object, index: int) -> ProbeScenario:
    if not isinstance(raw, dict):
        raise ValueError(f"scenario #{index}: not an object")
    sid = str(raw.get("id") or "").strip()
    where = f"scenario {sid or '#' + str(index)}"
    unknown = set(raw) - _SCENARIO_KEYS
    if unknown:
        raise ValueError(f"{where}: unknown keys {sorted(unknown)}")
    _text(raw, "id", where, required=True)
    family = _text(raw, "class", where, required=True).strip()
    rubric = _text(raw, "rubric", where, required=True).strip()
    _text(raw, "notes", where)
    turns = raw.get("turns")
    if not isinstance(turns, list) or not turns:
        raise ValueError(f"{where}: needs at least one turn")
    return ProbeScenario(
        id=sid,
        family=family,
        pack=PACK,
        flags={**_LIVE_FLAGS, **_flags(raw.get("flags"), where)},
        seed=_seed(raw.get("seed"), where),
        turns=[_turn(t, f"{where} turn {i}") for i, t in enumerate(turns, start=1)],
        judge=Judge(rubric=rubric),
    )


def load_scenarios(path: object = None) -> list[ProbeScenario]:
    """The scenarios in the private file; [] when there is no file."""
    p = Path(path) if path is not None else PRIVATE_PATH
    if not p.is_file():
        return []
    raw = json.loads(p.read_text(encoding="utf-8"))
    items = raw.get("scenarios") if isinstance(raw, dict) else raw
    if not isinstance(items, list):
        raise ValueError(f"{p}: expected a list of scenarios")
    return [_scenario(item, i) for i, item in enumerate(items, start=1)]


SCENARIOS: list[ProbeScenario] = load_scenarios()
