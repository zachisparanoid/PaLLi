from __future__ import annotations

import importlib.util
from pathlib import Path
import sys


def _load_module():
    module_path = Path(__file__).resolve().parents[2] / "scripts" / "wipe_palli_memory.py"
    spec = importlib.util.spec_from_file_location("wipe_palli_memory_test_module", module_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _seed_repo(root: Path) -> dict[str, Path]:
    data_dir = root / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    db_path = data_dir / "palli-local.sqlite3"
    wal_path = data_dir / "palli-local.sqlite3-wal"
    shm_path = data_dir / "palli-local.sqlite3-shm"
    db_path.write_text("db", encoding="utf-8")
    wal_path.write_text("wal", encoding="utf-8")
    shm_path.write_text("shm", encoding="utf-8")

    identity_root = root / "identity"
    core_root = identity_root / "core"
    templates_root = identity_root / "templates"
    bootstrap_root = identity_root / "bootstrap"
    owners_root = identity_root / "owners"
    users_root = identity_root / "users"

    for path in (core_root, templates_root, bootstrap_root, owners_root, users_root):
        path.mkdir(parents=True, exist_ok=True)

    (core_root / "CORE_IDENTITY.md").write_text("core", encoding="utf-8")
    (templates_root / "MEMORY.md").write_text("template", encoding="utf-8")
    (bootstrap_root / "FIRST_CONTACT.md").write_text("bootstrap", encoding="utf-8")
    (owners_root / ".gitkeep").write_text("\n", encoding="utf-8")
    (users_root / ".gitkeep").write_text("\n", encoding="utf-8")

    owner_dir = owners_root / "7"
    owner_memory_dir = owner_dir / "memory"
    owner_memory_dir.mkdir(parents=True, exist_ok=True)
    (owner_dir / "MEMORY.md").write_text("owner memory", encoding="utf-8")
    (owner_memory_dir / "note.md").write_text("note", encoding="utf-8")

    user_platform_dir = users_root / "discord"
    user_dir = user_platform_dir / "9"
    user_dir.mkdir(parents=True, exist_ok=True)
    (user_dir / "PERSONALITY_PROFILE.md").write_text("user profile", encoding="utf-8")

    return {
        "db_path": db_path,
        "wal_path": wal_path,
        "shm_path": shm_path,
        "identity_root": identity_root,
        "core_file": core_root / "CORE_IDENTITY.md",
        "template_file": templates_root / "MEMORY.md",
        "bootstrap_file": bootstrap_root / "FIRST_CONTACT.md",
        "owners_root": owners_root,
        "users_root": users_root,
        "owner_dir": owner_dir,
        "user_platform_dir": user_platform_dir,
        "user_dir": user_dir,
    }


def _seed_runtime_state(root: Path) -> dict[str, Path]:
    data_dir = root / "data"
    inner_monologue = data_dir / "inner_monologue"
    digests = inner_monologue / "digests"
    delegations = data_dir / "delegations"
    captures = data_dir / "operator-desktop" / "captures"
    for d in (inner_monologue, digests, delegations, captures):
        d.mkdir(parents=True, exist_ok=True)
    (inner_monologue / "2026-04-25.jsonl").write_text("im", encoding="utf-8")
    (digests / "digest.json").write_text("d", encoding="utf-8")
    deleg_dir = delegations / "deleg_abc"
    deleg_dir.mkdir()
    (deleg_dir / "transcript.txt").write_text("t", encoding="utf-8")
    (captures / "shot.png").write_text("png", encoding="utf-8")
    launcher = data_dir / "launcher-state.json"
    cli_state = data_dir / "cli_agent_state.json"
    launcher.write_text("ls", encoding="utf-8")
    cli_state.write_text("cs", encoding="utf-8")
    return {
        "inner_monologue": inner_monologue,
        "delegations": delegations,
        "captures": captures,
        "launcher": launcher,
        "cli_state": cli_state,
    }


def _seed_archive_backups(root: Path) -> dict[str, Path]:
    data_dir = root / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    foo_bak = data_dir / "foo.bak"
    sqlite_bak = data_dir / "palli-local.sqlite3.pre-wave-3.bak"
    inner_bak = data_dir / "inner_monologue.pre-2026-05-05-wipe.bak"
    inner_bak.mkdir(parents=True, exist_ok=True)
    (inner_bak / "old.jsonl").write_text("old", encoding="utf-8")
    nested_dir = data_dir / "somesubdir"
    nested_dir.mkdir(parents=True, exist_ok=True)
    nested_bak = nested_dir / "nested.bak"
    nested_bak.write_text("nested", encoding="utf-8")
    reset_old = data_dir / "reset-backups" / "memory-reset-20260504-old"
    reset_old.mkdir(parents=True, exist_ok=True)
    (reset_old / "preserved.txt").write_text("p", encoding="utf-8")
    foo_bak.write_text("foo", encoding="utf-8")
    sqlite_bak.write_text("sqlite", encoding="utf-8")
    return {
        "foo_bak": foo_bak,
        "sqlite_bak": sqlite_bak,
        "inner_bak": inner_bak,
        "nested_bak": nested_bak,
        "reset_old": reset_old,
    }


def _seed_test_artifacts(root: Path) -> dict[str, Path]:
    data_dir = root / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    probe = data_dir / ".palli-sqlite-probe-abc123.sqlite3"
    audit = data_dir / "phase7d_audit_test_xyz.sqlite3"
    probe.write_text("probe", encoding="utf-8")
    audit.write_text("audit", encoding="utf-8")
    return {"probe": probe, "audit": audit}


def _backup_path_for(backup_root: Path, source: Path) -> Path:
    return backup_root / source.relative_to(source.anchor)


def test_collect_reset_plan_targets_database_and_mutable_identity_only(tmp_path):
    module = _load_module()
    paths = _seed_repo(tmp_path)

    plan = module.collect_reset_plan(
        repo_root=tmp_path,
        database_path="data/palli-local.sqlite3",
        identity_root="identity",
    )

    assert plan.database_path == paths["db_path"]
    assert set(plan.database_files) == {
        paths["db_path"],
        paths["wal_path"],
        paths["shm_path"],
    }
    assert set(plan.mutable_identity_entries) == {
        paths["owner_dir"],
        paths["user_platform_dir"],
    }
    assert paths["core_file"] not in plan.mutable_identity_entries
    assert paths["template_file"] not in plan.mutable_identity_entries
    assert paths["bootstrap_file"] not in plan.mutable_identity_entries


def test_execute_reset_plan_removes_runtime_memory_and_preserves_foundation(tmp_path):
    module = _load_module()
    paths = _seed_repo(tmp_path)
    plan = module.collect_reset_plan(
        repo_root=tmp_path,
        database_path="data/palli-local.sqlite3",
        identity_root="identity",
    )

    backup_path = module.execute_reset_plan(plan, create_backup=False)

    assert backup_path is None
    assert not paths["db_path"].exists()
    assert not paths["wal_path"].exists()
    assert not paths["shm_path"].exists()
    assert not paths["owner_dir"].exists()
    assert not paths["user_platform_dir"].exists()
    assert paths["core_file"].exists()
    assert paths["template_file"].exists()
    assert paths["bootstrap_file"].exists()
    assert (paths["owners_root"] / ".gitkeep").exists()
    assert (paths["users_root"] / ".gitkeep").exists()


def test_execute_reset_plan_can_backup_removed_state(tmp_path):
    module = _load_module()
    paths = _seed_repo(tmp_path)
    plan = module.collect_reset_plan(
        repo_root=tmp_path,
        database_path="data/palli-local.sqlite3",
        identity_root="identity",
    )
    backup_root = tmp_path / "backup"

    backup_path = module.execute_reset_plan(
        plan,
        create_backup=True,
        backup_dir=backup_root,
    )

    assert backup_path == backup_root.resolve()
    assert _backup_path_for(backup_root.resolve(), paths["db_path"]).exists()
    assert _backup_path_for(backup_root.resolve(), paths["owner_dir"]).exists()
    assert _backup_path_for(backup_root.resolve(), paths["user_platform_dir"]).exists()


def test_collect_reset_plan_wipes_unknown_identity_root_dirs(tmp_path):
    """Privacy-operation invariant: anything under identity/ that is not
    in the preserve set ({core, templates, bootstrap}) gets wiped — even
    a future identity root we haven't invented yet."""
    module = _load_module()
    paths = _seed_repo(tmp_path)

    fictional_root = paths["identity_root"] / "agents"
    fictional_root.mkdir()
    fictional_child = fictional_root / "AGENT_PROFILE.md"
    fictional_child.write_text("agent", encoding="utf-8")

    plan = module.collect_reset_plan(
        repo_root=tmp_path,
        database_path="data/palli-local.sqlite3",
        identity_root="identity",
    )

    flat_entries = set(plan.mutable_identity_entries)
    assert any(
        entry == fictional_child or fictional_child in entry.rglob("*")
        for entry in flat_entries
    ), f"Expected the fictional agents root or its child to be in the wipe plan, got {flat_entries}"
    assert (paths["identity_root"] / "core") not in flat_entries
    assert (paths["identity_root"] / "templates") not in flat_entries
    assert (paths["identity_root"] / "bootstrap") not in flat_entries


def test_runtime_state_is_collected_backed_up_wiped_and_described(tmp_path):
    """All five runtime-state targets show up in the plan, get backed
    up, get wiped, and appear in the dry-run description."""
    module = _load_module()
    _seed_repo(tmp_path)
    runtime = _seed_runtime_state(tmp_path)
    backup_root = tmp_path / "backup"

    plan = module.collect_reset_plan(
        repo_root=tmp_path,
        database_path="data/palli-local.sqlite3",
        identity_root="identity",
    )

    runtime_set = set(plan.runtime_state_paths)
    assert runtime["inner_monologue"] in runtime_set
    assert runtime["delegations"] in runtime_set
    assert runtime["captures"] in runtime_set
    assert runtime["launcher"] in runtime_set
    assert runtime["cli_state"] in runtime_set

    description = module.describe_plan(plan)
    assert "Runtime state to remove" in description
    assert "data/inner_monologue" in description.replace("\\", "/")
    assert "data/delegations" in description.replace("\\", "/")
    assert "launcher-state.json" in description
    assert "cli_agent_state.json" in description

    module.execute_reset_plan(plan, create_backup=True, backup_dir=backup_root)

    assert not runtime["inner_monologue"].exists()
    assert not runtime["delegations"].exists()
    assert not runtime["captures"].exists()
    assert not runtime["launcher"].exists()
    assert not runtime["cli_state"].exists()

    assert _backup_path_for(backup_root.resolve(), runtime["inner_monologue"]).exists()
    assert _backup_path_for(backup_root.resolve(), runtime["launcher"]).exists()


def test_archive_backups_top_level_wiped_nested_and_reset_backups_preserved(tmp_path):
    """Archive backup wipe is non-recursive (top level of data/ only) and
    explicitly does not match data/reset-backups/* (prior-wipe archives)."""
    module = _load_module()
    _seed_repo(tmp_path)
    backups = _seed_archive_backups(tmp_path)
    backup_root = tmp_path / "backup"

    plan = module.collect_reset_plan(
        repo_root=tmp_path,
        database_path="data/palli-local.sqlite3",
        identity_root="identity",
    )

    archive_set = set(plan.archive_backup_paths)
    assert backups["foo_bak"] in archive_set
    assert backups["sqlite_bak"] in archive_set
    assert backups["inner_bak"] in archive_set
    assert backups["nested_bak"] not in archive_set, (
        "Non-recursive guard failed: nested .bak in subdir should not match"
    )
    assert backups["reset_old"] not in archive_set, (
        "Prior-wipe reset-backups dir must be preserved"
    )

    module.execute_reset_plan(plan, create_backup=True, backup_dir=backup_root)

    assert not backups["foo_bak"].exists()
    assert not backups["sqlite_bak"].exists()
    assert not backups["inner_bak"].exists()
    assert backups["nested_bak"].exists(), "Non-recursive guard failed in execute"
    assert backups["reset_old"].exists(), "Prior reset-backups directory was wiped"

    description = module.describe_plan(plan)
    assert "Archive backups to remove" in description


def test_test_artifacts_collected_wiped_and_live_db_not_double_listed(tmp_path):
    """Probe + audit-test files get wiped; live palli-local.sqlite3 stays
    in database_files (not in test_artifact_paths)."""
    module = _load_module()
    paths = _seed_repo(tmp_path)
    artifacts = _seed_test_artifacts(tmp_path)

    plan = module.collect_reset_plan(
        repo_root=tmp_path,
        database_path="data/palli-local.sqlite3",
        identity_root="identity",
    )

    artifact_set = set(plan.test_artifact_paths)
    assert artifacts["probe"] in artifact_set
    assert artifacts["audit"] in artifact_set
    assert paths["db_path"] not in artifact_set, (
        "Live DB must not be double-listed in test_artifact_paths"
    )
    assert paths["db_path"] in plan.database_files

    module.execute_reset_plan(plan, create_backup=False)

    assert not artifacts["probe"].exists()
    assert not artifacts["audit"].exists()

    description = module.describe_plan(plan)
    assert "Test artifact files to remove" in description


def test_full_clean_slate_acceptance(tmp_path):
    """End-to-end: state in all six layers + test junk + a prior
    reset-backups dir + identity/core, templates, bootstrap content
    + data/palli.yaml. Run the wipe. Verify the spec's acceptance
    criteria hold."""
    module = _load_module()
    paths = _seed_repo(tmp_path)
    runtime = _seed_runtime_state(tmp_path)
    backups = _seed_archive_backups(tmp_path)
    artifacts = _seed_test_artifacts(tmp_path)

    palli_yaml = tmp_path / "data" / "palli.yaml"
    palli_yaml.write_text("config", encoding="utf-8")

    plan = module.collect_reset_plan(
        repo_root=tmp_path,
        database_path="data/palli-local.sqlite3",
        identity_root="identity",
    )

    backup_root = tmp_path / "data" / "reset-backups" / "memory-reset-20260506-current"
    module.execute_reset_plan(plan, create_backup=True, backup_dir=backup_root)

    # --- Memory wiped (all six layers + test artifacts) ---
    for path in (
        paths["db_path"],
        paths["wal_path"],
        paths["shm_path"],
        paths["owner_dir"],
        paths["user_platform_dir"],
        runtime["inner_monologue"],
        runtime["delegations"],
        runtime["captures"],
        runtime["launcher"],
        runtime["cli_state"],
        backups["foo_bak"],
        backups["sqlite_bak"],
        backups["inner_bak"],
        artifacts["probe"],
        artifacts["audit"],
    ):
        assert not path.exists(), f"Expected {path} to be wiped"

    # --- Preserved (PaLLi's "code" + user config + prior reset-backups) ---
    for path in (
        paths["core_file"],
        paths["template_file"],
        paths["bootstrap_file"],
        palli_yaml,
        backups["nested_bak"],
        backups["reset_old"],
    ):
        assert path.exists(), f"Expected {path} to be preserved"

    # --- gitkeep recreated ---
    assert (paths["owners_root"] / ".gitkeep").exists()
    assert (paths["users_root"] / ".gitkeep").exists()

    # --- Backup destination is itself preserved (NOT wiped) ---
    assert backup_root.exists()
    assert _backup_path_for(backup_root.resolve(), paths["db_path"]).exists()
    assert _backup_path_for(backup_root.resolve(), runtime["inner_monologue"]).exists()
    assert _backup_path_for(backup_root.resolve(), backups["foo_bak"]).exists()


# ── her self-authored pages are not "memory" to wipe (item 7d, 2026-08-19) ──

def test_her_self_pages_are_preserved_by_default(tmp_path):
    """identity/palli/ (her story, interests, voice page, journal — and its
    .git) postdated the preserve list; a --yes run would have deleted her
    self-authorship as if it were his runtime memory (08-17 code read
    §2.4). Preserved by default; --include-self-pages opts in, loudly."""
    import scripts.wipe_palli_memory as wipe
    identity = tmp_path / "identity"
    for d in ("core", "templates", "bootstrap", "palli", "owners"):
        (identity / d).mkdir(parents=True)
    (identity / "palli" / "MY_STORY.md").write_text("mine", encoding="utf-8")
    (identity / "palli" / ".git").mkdir()
    (identity / "owners" / "1").mkdir()
    (identity / "owners" / "1" / "MEMORY.md").write_text("his", encoding="utf-8")
    (tmp_path / "data").mkdir()
    plan = wipe.collect_reset_plan(repo_root=tmp_path, database_path="data/x.sqlite3",
                                   identity_root=str(identity))
    names = {p.relative_to(identity).as_posix() for p in plan.mutable_identity_entries}
    assert "owners/1" in names
    assert not any(n.startswith("palli") for n in names)
    assert identity / "palli" in plan.identity_preserve_roots

    plan2 = wipe.collect_reset_plan(repo_root=tmp_path, database_path="data/x.sqlite3",
                                    identity_root=str(identity), include_self_pages=True)
    names2 = {p.relative_to(identity).as_posix() for p in plan2.mutable_identity_entries}
    assert "palli/MY_STORY.md" in names2 and "palli/.git" in names2
