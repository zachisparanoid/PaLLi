"""Wave 5 deletion-safety audit.

For each candidate deletion cluster, lists every external (non-test,
non-cluster-internal) module that imports something from it. A cluster
with zero external consumers is safely deletable; one with consumers
needs each consumer fixed (or itself deleted) first.

Run: venv/Scripts/python.exe scripts/audit_wave5_consumers.py
"""
from __future__ import annotations

import os
import re
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKIP_DIR_TOKENS = ("__pycache__", "venv", ".git", "node_modules", ".next")


def all_py_files() -> list[Path]:
    out: list[Path] = []
    for dirpath, _, fnames in os.walk(ROOT):
        if any(tok in dirpath for tok in SKIP_DIR_TOKENS):
            continue
        for f in fnames:
            if f.endswith(".py"):
                out.append(Path(dirpath) / f)
    return out


def module_for(path: Path) -> str:
    rel = path.relative_to(ROOT).as_posix()
    if rel.endswith(".py"):
        rel = rel[:-3]
    return rel.replace("/", ".")


def main() -> None:
    files = all_py_files()
    mod2path: dict[str, Path] = {module_for(p): p for p in files}

    clusters: dict[str, list[str]] = {
        "ai_proactive (replaced by core.turn.proactive)": [
            m for m in mod2path
            if m.startswith("ai.proactive_")
            and "inner_" not in m
            and "monologue_" not in m
        ],
        "ai_reminder (except time_parser, kept for now)": [
            m for m in mod2path
            if m.startswith("ai.reminder_")
            and m != "ai.reminder_time_parser"
        ],
        "core_runtime_authority (247KB god object)": [
            m for m in mod2path if m.startswith("core.runtime_authority")
        ],
        "core_message_star (the 4090-line message_processor + siblings)": [
            m for m in mod2path if m.startswith("core.message_")
        ],
        "core_operator_star (40-file operator cluster)": [
            m for m in mod2path if m.startswith("core.operator_")
        ],
        "api_web_runtime_voice_account_auth_agent": [
            m for m in mod2path
            if m in {"api.web_runtime", "api.web_voice", "api.web_account",
                     "api.web_auth", "api.agent"}
            or m.startswith("api.web_runtime_")
        ],
        "voice_session_flow_legacy": [
            m for m in mod2path
            if m.startswith("voice.session_")
            or m.endswith(".session_turn_flow")
        ],
        "core.first_contact (1389-line state machine, dissolved)": [
            "core.first_contact"
        ] if "core.first_contact" in mod2path else [],
        "core.identity.profile_store": [
            "core.identity.profile_store"
        ] if "core.identity.profile_store" in mod2path else [],
        "ai_personality_engines": [
            m for m in mod2path
            if m in {
                "ai.personality_engine",
                "ai.personality_generation",
                "ai.personality_learning",
                "ai.personality_response_style",
                "ai.personality_styler",
            }
        ],
        "ai_legacy_analyzers": [
            m for m in mod2path
            if m in {
                "ai.voice_interjection_analyzer",
                "ai.conversation_thread_analyzer",
                "ai.engagement_quality_analyzer",
                "ai.conversational_strategy",
                "ai.interest_exploration_engine",
                "ai.multi_analysis",
            }
        ],
    }

    for name, mods in clusters.items():
        print()
        print("=" * 72)
        print(f"{name}  —  {len(mods)} modules")
        print("=" * 72)
        if not mods:
            print("  (no matching modules in repo)")
            continue
        cluster_set = set(mods)
        external_consumers: dict[str, set[str]] = defaultdict(set)
        for fp in files:
            rel = fp.relative_to(ROOT).as_posix()
            if rel.startswith("tests/") or rel.startswith("docs/"):
                continue
            if rel.startswith("scripts/"):
                continue
            if module_for(fp) in cluster_set:
                continue
            try:
                txt = fp.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            for m in mods:
                pattern = rf"\bfrom {re.escape(m)}\b|\bimport {re.escape(m)}\b"
                if re.search(pattern, txt):
                    external_consumers[m].add(rel)
        if not external_consumers:
            print("  *** NO external non-test consumers — cluster is safely deletable as a unit ***")
            continue
        for m, callers in sorted(external_consumers.items()):
            print(f"  {m}: {len(callers)} consumer(s)")
            for c in sorted(callers)[:6]:
                print(f"    - {c}")


if __name__ == "__main__":
    main()
