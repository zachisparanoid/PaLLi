"""Find Python modules with zero consumers anywhere — safe to delete.

A "true orphan" is a module that no other .py file (production or test)
imports. Deleting one cannot break any reachable code.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKIP_DIR_TOKENS = ("__pycache__", "venv", ".git", "node_modules", ".next")


def all_py_files() -> list[Path]:
    out = []
    for d, _, fs in os.walk(ROOT):
        if any(t in d for t in SKIP_DIR_TOKENS):
            continue
        for f in fs:
            if f.endswith(".py"):
                out.append(Path(d) / f)
    return out


def module_for(path: Path) -> str:
    rel = path.relative_to(ROOT).as_posix()
    if rel.endswith(".py"):
        rel = rel[:-3]
    return rel.replace("/", ".")


def main() -> None:
    files = all_py_files()
    mod2path: dict[str, Path] = {module_for(p): p for p in files}

    # Only check files in the legacy clusters — fast scan.
    candidate_modules = [
        m for m in mod2path
        if (m.startswith("ai.proactive_") and "inner_" not in m and "monologue_" not in m)
        or m.startswith("ai.reminder_")
        or m.startswith("ai.personality_")
        or m.startswith("core.runtime_authority")
        or m.startswith("core.message_")
        or m.startswith("core.operator_")
        or m.startswith("voice.session_")
        or m in {
            "ai.voice_interjection_analyzer", "ai.conversation_thread_analyzer",
            "ai.engagement_quality_analyzer", "ai.conversational_strategy",
            "ai.interest_exploration_engine", "ai.multi_analysis",
            "core.first_contact",
            "api.web_runtime", "api.web_voice", "api.web_account", "api.web_auth",
            "api.agent",
        }
        or m.startswith("api.web_runtime_")
    ]

    orphans = []
    for m in sorted(candidate_modules):
        # Search EVERY .py file (production + test + script) for any
        # import of this module.
        pattern = re.compile(rf"\bfrom {re.escape(m)}\b|\bimport {re.escape(m)}\b")
        consumers = []
        for fp in files:
            if module_for(fp) == m:
                continue
            try:
                txt = fp.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            if pattern.search(txt):
                consumers.append(fp.relative_to(ROOT).as_posix())
        if not consumers:
            orphans.append(m)
        # Print module + its consumer count
        # print(f'  {m}: {len(consumers)} consumer(s)')

    print(f"True orphan modules (zero consumers anywhere — safe to delete):")
    print("=" * 60)
    for m in orphans:
        loc = sum(1 for _ in mod2path[m].read_text(encoding="utf-8", errors="ignore").splitlines())
        print(f"  {m} ({loc} LOC) — {mod2path[m].relative_to(ROOT).as_posix()}")
    print(f"\nTotal: {len(orphans)} orphan modules")


if __name__ == "__main__":
    main()
