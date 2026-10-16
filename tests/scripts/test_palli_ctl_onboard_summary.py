"""`onboard` prints its summary once per key.

The final review's M7: `onboard --non-interactive` printed
`VOICE_ENGINE_DEFAULT=classic` twice, because the key sits twice in the tuple
`configure()` walks when it echoes what it wrote. Cosmetic, and exactly the kind
of thing a stranger reads as "did it just do that twice?" on their first run.

An AST canary rather than a run: `configure` writes `.env.local`, syncs settings and
re-runs the doctor, none of which a test may do to a live install.
"""
from __future__ import annotations

import ast
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SOURCE = (REPO / "scripts" / "palli_ctl.py").read_text(encoding="utf-8")


def _summary_keys() -> list[str]:
    """The key tuple `configure` iterates to echo the values it wrote."""
    tree = ast.parse(SOURCE)
    for node in ast.walk(tree):
        if not (isinstance(node, ast.FunctionDef) and node.name == "configure"):
            continue
        for inner in ast.walk(node):
            if not isinstance(inner, ast.For):
                continue
            if not isinstance(inner.iter, ast.Tuple):
                continue
            keys = [e.value for e in inner.iter.elts
                    if isinstance(e, ast.Constant) and isinstance(e.value, str)]
            if "LLM_PROVIDER" in keys:
                return keys
    raise AssertionError("configure()'s summary key tuple was not found")


def test_the_summary_tuple_was_found_and_is_the_real_one():
    keys = _summary_keys()
    assert "VOICE_ENGINE_DEFAULT" in keys and len(keys) > 10


def test_no_key_is_echoed_twice():
    counts = Counter(_summary_keys())
    assert [key for key, n in counts.items() if n > 1] == []
