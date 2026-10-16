from __future__ import annotations

import argparse
import shutil
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Sequence


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.config import config


PRESERVE_IDENTITY_DIRS = ("core", "templates", "bootstrap")
# Her self-authored pages (identity/palli/: her story, interests, voice page,
# journal — and its git history) are HERS, not his runtime memory. They
# postdated this list, so a --yes run would have deleted them as "mutable
# identity" (2026-08-17 code read §2.4). Preserved by default since
# 2026-08-19; --include-self-pages opts in, on purpose.
SELF_PAGES_DIR = "palli"


RUNTIME_STATE_TARGETS = (
    "inner_monologue",
    "delegations",
    "operator-desktop/captures",
    "launcher-state.json",
    "cli_agent_state.json",
)


def collect_runtime_state_paths(data_dir: Path) -> list[Path]:
    """Resolve RUNTIME_STATE_TARGETS against the data directory.
    Targets that don't exist are silently skipped — partial hits are valid."""
    paths: list[Path] = []
    if not data_dir.exists():
        return paths
    for target in RUNTIME_STATE_TARGETS:
        candidate = data_dir / target
        if candidate.exists():
            paths.append(candidate)
    return paths


ARCHIVE_BACKUP_GLOBS = (
    "*.bak",
    "*.sqlite3*.bak",
    "inner_monologue.pre-*-wipe*",
)


def collect_archive_backup_paths(data_dir: Path) -> list[Path]:
    """Match top-level entries under data/ against ARCHIVE_BACKUP_GLOBS.

    Non-recursive by design: data_dir.glob(pat) matches only direct
    children of data_dir, not files inside subdirectories. This is
    critical so that data/reset-backups/<timestamp>/*.bak is NOT swept
    up — those are the script's own backup destinations.
    """
    paths: list[Path] = []
    if not data_dir.exists():
        return paths
    seen: set[Path] = set()
    for pattern in ARCHIVE_BACKUP_GLOBS:
        for match in sorted(data_dir.glob(pattern), key=lambda p: p.name.lower()):
            if match in seen:
                continue
            seen.add(match)
            paths.append(match)
    return paths


TEST_ARTIFACT_GLOBS = (
    ".palli-sqlite-probe-*.sqlite3",
    "phase7d_audit_test_*.sqlite3",
)


def collect_test_artifact_paths(
    data_dir: Path,
    *,
    exclude: tuple[Path, ...] = (),
) -> list[Path]:
    """Match top-level files under data/ against TEST_ARTIFACT_GLOBS.

    Excludes any path in `exclude` to avoid double-listing the live DB.
    """
    excluded = {p.resolve() for p in exclude}
    paths: list[Path] = []
    if not data_dir.exists():
        return paths
    seen: set[Path] = set()
    for pattern in TEST_ARTIFACT_GLOBS:
        for match in sorted(data_dir.glob(pattern), key=lambda p: p.name.lower()):
            if match.resolve() in excluded:
                continue
            if match in seen:
                continue
            seen.add(match)
            paths.append(match)
    return paths


def collect_mutable_identity_entries(
    identity_root: Path, *, include_self_pages: bool = False
) -> list[Path]:
    """Iterate identity/ and return every concrete entry under any
    non-preserve top-level dir.

    Privacy-operation invariant: under identity/, the preserve-list is
    small and closed; everything else is downstream of templates and
    rebuildable. New mutable identity roots get wiped automatically —
    except her own pages (SELF_PAGES_DIR), which are not rebuildable from
    templates and not his memory; those go only on --include-self-pages.
    """
    entries: list[Path] = []
    if not identity_root.exists():
        return entries
    for top_level in sorted(identity_root.iterdir(), key=lambda p: p.name.lower()):
        if not top_level.is_dir():
            continue
        if top_level.name in PRESERVE_IDENTITY_DIRS:
            continue
        if top_level.name == SELF_PAGES_DIR and not include_self_pages:
            continue
        for child in sorted(top_level.iterdir(), key=lambda p: p.name.lower()):
            if child.name == ".gitkeep":
                continue
            entries.append(child)
    return entries


@dataclass(frozen=True)
class MemoryResetPlan:
    repo_root: Path
    database_path: Path
    database_files: tuple[Path, ...]
    identity_root: Path
    mutable_identity_entries: tuple[Path, ...]
    identity_preserve_roots: tuple[Path, ...]
    runtime_state_paths: tuple[Path, ...]
    archive_backup_paths: tuple[Path, ...]
    test_artifact_paths: tuple[Path, ...]


def _resolve_repo_path(*, repo_root: Path, configured: str, default: str) -> Path:
    candidate = str(configured or "").strip() or default
    path = Path(candidate)
    if not path.is_absolute():
        path = repo_root / path
    return path.resolve()


def resolve_identity_root(*, repo_root: Path, configured_path: str | None = None) -> Path:
    return _resolve_repo_path(
        repo_root=repo_root,
        configured=str(
            configured_path
            if configured_path is not None
            else getattr(config, "IDENTITY_ROOT_PATH", "identity")
        ),
        default="identity",
    )


def sqlite_sidecars(db_path: Path) -> tuple[Path, ...]:
    return (
        db_path.with_name(f"{db_path.name}-wal"),
        db_path.with_name(f"{db_path.name}-shm"),
        db_path.with_name(f"{db_path.name}-journal"),
    )


def collect_reset_plan(
    *,
    repo_root: Path | None = None,
    database_path: str | None = None,
    identity_root: str | None = None,
    include_self_pages: bool = False,
) -> MemoryResetPlan:
    root = (repo_root or REPO_ROOT).resolve()
    db_path = _resolve_repo_path(
        repo_root=root,
        configured=str(
            database_path
            if database_path is not None
            else getattr(config, "DATABASE_PATH", "data/palli-local.sqlite3")
        ),
        default="data/palli-local.sqlite3",
    )
    identity_root_path = resolve_identity_root(repo_root=root, configured_path=identity_root)

    database_files: list[Path] = []
    for candidate in (db_path, *sqlite_sidecars(db_path)):
        if candidate.exists():
            database_files.append(candidate)

    mutable_identity_entries = collect_mutable_identity_entries(
        identity_root_path, include_self_pages=include_self_pages
    )
    preserve_roots = tuple(
        identity_root_path / name
        for name in (PRESERVE_IDENTITY_DIRS + (() if include_self_pages else (SELF_PAGES_DIR,)))
    )

    data_dir = db_path.parent
    runtime_state_paths = collect_runtime_state_paths(data_dir)
    archive_backup_paths = collect_archive_backup_paths(data_dir)
    test_artifact_paths = collect_test_artifact_paths(
        data_dir, exclude=tuple(database_files)
    )

    return MemoryResetPlan(
        repo_root=root,
        database_path=db_path,
        database_files=tuple(database_files),
        identity_root=identity_root_path,
        mutable_identity_entries=tuple(mutable_identity_entries),
        identity_preserve_roots=tuple(preserve_roots),
        runtime_state_paths=tuple(runtime_state_paths),
        archive_backup_paths=tuple(archive_backup_paths),
        test_artifact_paths=tuple(test_artifact_paths),
    )


def build_default_backup_dir(*, repo_root: Path) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return repo_root / "data" / "reset-backups" / f"memory-reset-{stamp}"


def _copy_path_to_backup(*, source: Path, backup_root: Path) -> None:
    relative = source.relative_to(source.anchor) if source.is_absolute() else source
    destination = backup_root / relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    if source.is_dir():
        shutil.copytree(source, destination, dirs_exist_ok=True)
    else:
        shutil.copy2(source, destination)


def backup_reset_plan(plan: MemoryResetPlan, *, backup_dir: Path) -> Path:
    backup_root = backup_dir.resolve()
    backup_root.mkdir(parents=True, exist_ok=True)
    for path in (
        *plan.database_files,
        *plan.mutable_identity_entries,
        *plan.runtime_state_paths,
        *plan.archive_backup_paths,
        *plan.test_artifact_paths,
    ):
        if path.exists():
            _copy_path_to_backup(source=path, backup_root=backup_root)
    return backup_root


def _remove_path(path: Path) -> None:
    try:
        if path.is_dir():
            shutil.rmtree(path)
        elif path.exists():
            path.unlink()
    except PermissionError as exc:
        raise RuntimeError(
            f"Failed to remove `{path}` because it appears to be in use. Stop PaLLi first, then retry."
        ) from exc


def _recreate_identity_roots(identity_root: Path) -> None:
    for root_name in ("owners", "users"):
        target = identity_root / root_name
        target.mkdir(parents=True, exist_ok=True)
        gitkeep = target / ".gitkeep"
        if not gitkeep.exists():
            gitkeep.write_text("\n", encoding="utf-8")


def execute_reset_plan(
    plan: MemoryResetPlan,
    *,
    create_backup: bool = True,
    backup_dir: Path | None = None,
) -> Path | None:
    backup_path: Path | None = None
    if create_backup:
        backup_path = backup_reset_plan(
            plan,
            backup_dir=(backup_dir or build_default_backup_dir(repo_root=plan.repo_root)),
        )

    for path in plan.database_files:
        _remove_path(path)
    for path in plan.mutable_identity_entries:
        _remove_path(path)
    for path in plan.runtime_state_paths:
        _remove_path(path)
    for path in plan.archive_backup_paths:
        _remove_path(path)
    for path in plan.test_artifact_paths:
        _remove_path(path)
    _recreate_identity_roots(plan.identity_root)
    return backup_path


def describe_plan(plan: MemoryResetPlan) -> str:
    lines = [
        "PaLLi memory wipe plan",
        f"- Database target: {plan.database_path}",
    ]
    if plan.database_files:
        lines.append("- Database files to remove:")
        lines.extend(f"  - {path}" for path in plan.database_files)
    else:
        lines.append("- Database files to remove: none found")

    if plan.mutable_identity_entries:
        lines.append("- Mutable identity entries to remove:")
        lines.extend(f"  - {path}" for path in plan.mutable_identity_entries)
    else:
        lines.append("- Mutable identity entries to remove: none found")

    if plan.runtime_state_paths:
        lines.append("- Runtime state to remove:")
        lines.extend(f"  - {path}" for path in plan.runtime_state_paths)

    if plan.archive_backup_paths:
        lines.append("- Archive backups to remove:")
        lines.extend(f"  - {path}" for path in plan.archive_backup_paths)

    if plan.test_artifact_paths:
        lines.append("- Test artifact files to remove:")
        lines.extend(f"  - {path}" for path in plan.test_artifact_paths)

    lines.append("- Identity roots preserved:")
    lines.extend(f"  - {path}" for path in plan.identity_preserve_roots)
    lines.append("- Owner/user identity docs will be regenerated from templates on next startup.")
    return "\n".join(lines)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Wipe PaLLi runtime memory: SQLite database, mutable identity "
            "docs (everything under identity/ except core/templates/bootstrap), "
            "runtime state, archive backups, and accumulated test artifacts."
        )
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Actually perform the wipe. Without this flag the script only prints the plan.",
    )
    parser.add_argument(
        "--no-backup",
        action="store_true",
        help="Skip the automatic backup snapshot.",
    )
    parser.add_argument(
        "--backup-dir",
        type=Path,
        help="Optional backup directory. Defaults to data/reset-backups/<timestamp>/",
    )
    parser.add_argument(
        "--database-path",
        help="Override DATABASE_PATH for the wipe target.",
    )
    parser.add_argument(
        "--identity-root",
        help="Override IDENTITY_ROOT_PATH for the wipe target.",
    )
    parser.add_argument(
        "--include-self-pages",
        action="store_true",
        help=("ALSO delete identity/palli/ — her own story, interests, voice page, journal and "
              "their git history. Preserved by default (2026-08-19): they are hers, not the owner's memory."),
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    plan = collect_reset_plan(
        repo_root=REPO_ROOT,
        database_path=args.database_path,
        identity_root=args.identity_root,
        include_self_pages=bool(getattr(args, "include_self_pages", False)),
    )

    print(describe_plan(plan))
    if not args.yes:
        print("\nDry run only. Re-run with `--yes` to perform the wipe.")
        return 0

    backup_path = execute_reset_plan(
        plan,
        create_backup=not bool(args.no_backup),
        backup_dir=args.backup_dir,
    )
    if backup_path is not None:
        print(f"\nBackup created at: {backup_path}")
    else:
        print("\nBackup skipped.")
    print("PaLLi runtime memory wipe complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
