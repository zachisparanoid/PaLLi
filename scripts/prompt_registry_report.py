#!/usr/bin/env python3
"""
Generate an inventory report of PaLLi's prompt system.

Outputs:
  - docs/prompt_reconciliation_report.md

This is a "Phase 0" observability tool: it does not change runtime behavior.
"""

from __future__ import annotations

import re
import string
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set, Tuple


REPO_ROOT = Path(__file__).resolve().parents[1]
PROMPTS_DIR = REPO_ROOT / "prompts"
DOCS_DIR = REPO_ROOT / "docs"
REPORT_PATH = DOCS_DIR / "prompt_reconciliation_report.md"

CODE_DIRS = ["ai", "core", "api", "bots"]
CODE_SUFFIXES = {".py"}


@dataclass(frozen=True)
class PromptFileInfo:
    name: str
    path: Path
    size_bytes: int
    variables: Tuple[str, ...]
    json_hint: bool
    strict_json_hint: bool


def iter_code_files() -> Iterable[Path]:
    for rel in CODE_DIRS + ["main.py"]:
        p = REPO_ROOT / rel
        if p.is_file():
            yield p
            continue
        if not p.exists():
            continue
        for f in p.rglob("*"):
            if f.is_file() and f.suffix in CODE_SUFFIXES:
                yield f


def _extract_variables(template: str) -> List[str]:
    """
    Extract python .format() placeholders using string.Formatter, which respects escaped braces.
    """
    formatter = string.Formatter()
    vars_found: Set[str] = set()
    for _, field_name, _, _ in formatter.parse(template):
        if not field_name:
            continue
        # field_name can include format specs/indexing like "foo[0]" or "foo.bar"
        base = re.split(r"[\[\.]", field_name, maxsplit=1)[0].strip()
        if base:
            vars_found.add(base)
    return sorted(vars_found)


def load_prompt_files() -> List[PromptFileInfo]:
    prompts: List[PromptFileInfo] = []
    if not PROMPTS_DIR.exists():
        return prompts

    for path in sorted(PROMPTS_DIR.glob("*.txt")):
        try:
            content = path.read_text(encoding="utf-8")
        except Exception:
            content = ""
        variables = tuple(_extract_variables(content))
        lowered = content.lower()
        json_hint = "json" in lowered and ("respond with json" in lowered or "return json" in lowered)
        strict_json_hint = "strict json" in lowered or "respond with strict json" in lowered or "return strict json" in lowered
        prompts.append(
            PromptFileInfo(
                name=path.stem,
                path=path,
                size_bytes=path.stat().st_size,
                variables=variables,
                json_hint=json_hint,
                strict_json_hint=strict_json_hint,
            )
        )
    return prompts


def scan_prompt_loader_calls(files: Iterable[Path]) -> Dict[str, Set[str]]:
    """
    Return mapping: prompt_name -> set of "file.py:line" callsites for prompt_loader.load_prompt("name")
    """
    pattern = re.compile(r"""prompt_loader\.load_prompt\(\s*["']([a-zA-Z0-9_\-]+)["']\s*\)""")
    results: Dict[str, Set[str]] = {}
    for f in files:
        try:
            text = f.read_text(encoding="utf-8")
        except Exception:
            continue
        for m in pattern.finditer(text):
            name = m.group(1)
            line = text.count("\n", 0, m.start()) + 1
            results.setdefault(name, set()).add(f"{f.relative_to(REPO_ROOT)}:{line}")
    return results


def scan_direct_prompt_reads(files: Iterable[Path]) -> List[Tuple[str, str]]:
    """
    Find hard-coded reads like open("prompts/foo.txt") or os.path.exists("prompts/foo.txt").

    Returns list of (path, context_snippet).
    """
    hits: List[Tuple[str, str]] = []
    needle = "prompts/"
    for f in files:
        try:
            text = f.read_text(encoding="utf-8")
        except Exception:
            continue
        if needle not in text:
            continue
        for i, line in enumerate(text.splitlines(), start=1):
            if needle in line:
                rel = str(f.relative_to(REPO_ROOT))
                hits.append((f"{rel}:{i}", line.strip()[:200]))
    return hits


def _read_prompt_loader_expected_list() -> List[str]:
    path = REPO_ROOT / "ai" / "prompt_loader.py"
    if not path.exists():
        return []
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        return []

    # Very small heuristic: parse the expected_prompts = [ ... ] block.
    m = re.search(r"expected_prompts\s*=\s*\[(.*?)\]\s*", text, re.DOTALL)
    if not m:
        return []
    block = m.group(1)
    names = re.findall(r"""["']([a-zA-Z0-9_\-]+)["']""", block)
    return sorted(set(names))


def render_report(
    prompts: List[PromptFileInfo],
    loader_calls: Dict[str, Set[str]],
    direct_reads: List[Tuple[str, str]],
    expected: List[str],
) -> str:
    prompt_names = {p.name for p in prompts}
    used_via_loader = set(loader_calls.keys())
    expected_set = set(expected)

    unused_prompts = sorted(prompt_names - used_via_loader)
    missing_prompt_files = sorted(used_via_loader - prompt_names)
    expected_missing_files = sorted(expected_set - prompt_names)

    lines: List[str] = []
    lines.append("# Prompt Reconciliation Report\n")
    lines.append("Generated by `scripts/prompt_registry_report.py`.\n")

    lines.append("## Summary\n")
    lines.append(f"- Prompt files found: **{len(prompts)}**")
    lines.append(f"- `prompt_loader.load_prompt()` unique names: **{len(used_via_loader)}**")
    lines.append(f"- PromptLoader startup expected list: **{len(expected)}**")
    if missing_prompt_files:
        lines.append(f"- ⚠️ `load_prompt()` names with no `prompts/<name>.txt` file: **{len(missing_prompt_files)}**")
    if expected_missing_files:
        lines.append(f"- ⚠️ PromptLoader expects missing files: **{len(expected_missing_files)}**")
    if unused_prompts:
        lines.append(f"- ℹ️ Prompt files not referenced via `load_prompt()` (may be legacy or used via direct reads): **{len(unused_prompts)}**")
    lines.append("")

    if direct_reads:
        lines.append("## Direct Reads (bypassing PromptLoader)\n")
        for loc, snippet in direct_reads[:200]:
            lines.append(f"- `{loc}` — `{snippet}`")
        if len(direct_reads) > 200:
            lines.append(f"- …and {len(direct_reads) - 200} more")
        lines.append("")

    lines.append("## Prompt Files\n")
    lines.append("| Prompt | Size | Vars | JSON? | STRICT JSON? | Used by | In startup audit? |")
    lines.append("|---|---:|---:|:---:|:---:|---:|:---:|")
    for p in prompts:
        used_by = len(loader_calls.get(p.name, set()))
        in_expected = "✅" if p.name in expected_set else ""
        lines.append(
            f"| `{p.name}` | {p.size_bytes} | {len(p.variables)} | {'✅' if p.json_hint else ''} | {'✅' if p.strict_json_hint else ''} | {used_by} | {in_expected} |"
        )
    lines.append("")

    lines.append("## Variables per Prompt (placeholders)\n")
    for p in prompts:
        vars_text = ", ".join(f"`{v}`" for v in p.variables) if p.variables else "_(none)_"
        lines.append(f"- `{p.name}`: {vars_text}")
    lines.append("")

    lines.append("## PromptLoader callsites\n")
    for name in sorted(loader_calls.keys()):
        lines.append(f"### `{name}`")
        for loc in sorted(loader_calls[name]):
            lines.append(f"- `{loc}`")
        lines.append("")

    if missing_prompt_files:
        lines.append("## Missing prompt files (referenced but not found)\n")
        for name in missing_prompt_files:
            lines.append(f"- `{name}`")
        lines.append("")

    if expected_missing_files:
        lines.append("## Startup audit expects missing files\n")
        for name in expected_missing_files:
            lines.append(f"- `{name}`")
        lines.append("")

    if unused_prompts:
        lines.append("## Unreferenced prompt files (via `load_prompt()`)\n")
        for name in unused_prompts:
            lines.append(f"- `{name}`")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    code_files = list(iter_code_files())
    prompts = load_prompt_files()
    loader_calls = scan_prompt_loader_calls(code_files)
    direct_reads = scan_direct_prompt_reads(code_files)
    expected = _read_prompt_loader_expected_list()

    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    report = render_report(prompts, loader_calls, direct_reads, expected)
    REPORT_PATH.write_text(report, encoding="utf-8")
    print(f"Wrote report: {REPORT_PATH.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

