"""Week 2 task 5 — what the SHIPPED config switches on, and what it leaves off.

`data/palli.example.yaml` is the file a stranger's first boot copies into place
(`core/settings_boot.py` `seed_from_example`), so its flag values are the defaults of
every install but the author's. Her organs ship ON — that is the product. The forge does
not: inside `features.forged_tools` she writes, tests and hot-installs her own Python
tools into the running process, autonomously and with no confirmation. That is an
experiment worth having and not a thing to hand somebody on the first boot, before they
have read what it does.

This module reads the EXAMPLE and never `data/palli.yaml`: it ships, and a fresh clone
has no live config at all. The generator's own tests (`tests/scripts/`) stay home.

Two properties, one for each half of the claim:

  * the file says `false`, with the reason printed above the row — a flag nobody can
    find the story of is a flag nobody turns on deliberately;
  * a real boot from that file agrees. The value in the yaml is only a promise; the
    gate is `_forged_tools_enabled()`, and what it decides is whether `forge_tool` is
    on her belt at all. A subprocess is the only honest place to watch a boot happen.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import yaml as pyyaml

REPO = Path(__file__).resolve().parents[2]
EXAMPLE = REPO / "data" / "palli.example.yaml"


def _comments_above(text: str, row: str) -> str:
    """The contiguous comment lines printed immediately above one row."""
    lines = text.splitlines()
    index = next(i for i, line in enumerate(lines) if line.strip().startswith(row))
    found: list[str] = []
    for i in range(index - 1, -1, -1):
        stripped = lines[i].strip()
        if not stripped.startswith("#"):
            break
        found.append(stripped)
    return "\n".join(reversed(found))


def test_the_example_ships_the_forge_off():
    data = pyyaml.safe_load(EXAMPLE.read_text(encoding="utf-8"))
    assert data["features"]["forged_tools"] is False


def test_the_forge_row_says_why_it_is_off():
    """The reason travels with the row. `false` with no note reads as an oversight."""
    above = _comments_above(EXAMPLE.read_text(encoding="utf-8"), "forged_tools:")
    assert "experimental" in above.lower(), above
    assert "SETUP.md" in above, above


def test_a_boot_from_the_example_puts_no_forge_on_her_belt():
    """The gate, exercised — not the yaml value restated.

    `_forged_tools_enabled()` is what `build_default_registry()` asks before it
    registers `forge_tool`, so flag-off has to mean a tool list byte-identical to a
    build without the forge. Booted in a subprocess because `settings_service` is
    built at import from PALLI_SETTINGS_PATH: in-process there is no boot left to
    watch.
    """
    probe = (
        "import core.settings_boot as sb;"
        "sb.boot_settings();"
        "from core.agent.builtin_tools import _forged_tools_enabled, build_default_registry;"
        "print('gate', _forged_tools_enabled());"
        "print('tools', ' '.join(sorted(build_default_registry().all_names())))"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=str(REPO),
        env={**os.environ, "PALLI_SETTINGS_PATH": str(EXAMPLE)},
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert result.returncode == 0, result.stderr[-2000:]
    lines = result.stdout.splitlines()
    gate = next(ln for ln in lines if ln.startswith("gate "))
    tools = next(ln for ln in lines if ln.startswith("tools ")).split()[1:]
    assert gate == "gate False", result.stdout
    assert "forge_tool" not in tools, tools
    # non-vacuous: the registry really was built, and her other hands are on it
    assert "read_file" in tools, tools
