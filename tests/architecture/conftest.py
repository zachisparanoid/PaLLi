"""Shared utilities for architectural-boundary tests.

The functions here walk the repository, parse Python files, and produce
the structured representation each rule-set test consumes. Keeping the
walk in one place ensures every test agrees on what counts as "an
in-repo module."

Phase 8a addition. The companion `test_delegation_boundaries.py` file
predates this conftest and uses its own helpers — that's fine; the
delegation rules are narrower and self-contained.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, FrozenSet, List, Set


# Top-level packages whose internal layering Phase 8a polices.
SOURCE_TREES: FrozenSet[str] = frozenset({"ai", "core", "bots", "voice"})


# Directories anywhere in the path that disqualify a file from analysis.
SKIP_DIR_NAMES: FrozenSet[str] = frozenset({
    "venv",
    "__pycache__",
    ".git",
    "node_modules",
    ".tmp",
    ".pytest_cache",
    ".playwright-mcp",
    "data",
    "docs",
    "tests",
    "scripts",
    "deprecated",
    "archive",
    "logs",
    "website",
})


@dataclass(frozen=True)
class RepoModule:
    """One Python module in one of the source trees we police."""

    rel_path: str          # e.g. "core/message_processor.py"
    module_name: str       # e.g. "core.message_processor"
    tree: str              # e.g. "core"
    imports: FrozenSet[str]  # full set of names this module imports


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _path_is_skipped(path: Path, root: Path) -> bool:
    rel_parts = path.relative_to(root).parts
    return any(part in SKIP_DIR_NAMES for part in rel_parts)


def _module_name_for(rel_path: Path) -> str:
    """Convert a relative path to its dotted module name.

    Drops the trailing `.py`. `__init__.py` becomes the package's name
    (i.e. the parent directory's dotted form).
    """
    parts = list(rel_path.with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _walk_imports(file_path: Path) -> Set[str]:
    """Return the set of module names imported by a single Python file.

    Both `import x.y` and `from x.y import z` contribute the dotted
    prefix `x.y`. Relative imports (`from . import x`) are resolved
    against the file's package; bare relative imports become `<empty>`
    and are filtered out.
    """
    try:
        source = file_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return set()
    try:
        tree = ast.parse(source, filename=str(file_path))
    except SyntaxError:
        return set()
    found: Set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name:
                    found.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level and node.level > 0:
                # Relative import — resolve against caller's package.
                continue
            if node.module:
                found.add(node.module)
    return found


def collect_repo_modules() -> List[RepoModule]:
    """Scan the source trees and return one RepoModule per Python file.

    Caching is unnecessary at scale this small (~hundreds of files);
    pytest's per-session fixture caching wraps this anyway.
    """
    root = repo_root()
    modules: List[RepoModule] = []
    for tree_name in SOURCE_TREES:
        tree_root = root / tree_name
        if not tree_root.is_dir():
            continue
        for path in tree_root.rglob("*.py"):
            if _path_is_skipped(path, root):
                continue
            rel = path.relative_to(root)
            modules.append(
                RepoModule(
                    rel_path=rel.as_posix(),
                    module_name=_module_name_for(rel),
                    tree=tree_name,
                    imports=frozenset(_walk_imports(path)),
                )
            )
    modules.sort(key=lambda m: m.rel_path)
    return modules


def in_repo_imports(module: RepoModule) -> Set[str]:
    """Filter `module.imports` down to only those that resolve to other
    in-repo modules. External imports (stdlib, third-party) are ignored.
    """
    return {imp for imp in module.imports if imp.split(".", 1)[0] in SOURCE_TREES}


def imports_into_tree(module: RepoModule, tree: str) -> Set[str]:
    """Return the imports from `module` that target `tree` (e.g. 'bots').

    Both bare `tree` (e.g. `import bots`) and `tree.x.y` style match.
    """
    return {
        imp
        for imp in module.imports
        if imp == tree or imp.startswith(f"{tree}.")
    }
