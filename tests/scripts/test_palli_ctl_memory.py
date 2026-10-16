"""`palli_ctl memory archived` and `palli_ctl memory restore <mcb_id>` (Batch 5,
task 6, rulings R29/R43). The nightly sweep moves lines to MEMORY_ARCHIVE.md;
these are the owner's two levers over it: see what moved (with the id each
line is known by) and bring one line back. Everything runs against a tmp
identity tree; the brain's version recorder and the reference bump are
stubs. Never the owner's files."""
from __future__ import annotations

import importlib.util
import shutil
from pathlib import Path

import pytest

from core.identity.frontmatter import parse_frontmatter
from core.memory_bullets import item_id_for

REPO_ROOT = Path(__file__).resolve().parents[2]

MEMORY = """---
doc_type: memory
platform: owner
user_id: 1
version: 3
owner_user_id: 1
---
# Memory

## Durable Context

- [2026-05-01] Has a cat named Miso

## Active Threads

- No active curated threads yet.

## Important Decisions

- No important decisions recorded yet.

## Useful References

- No durable references recorded yet.
"""

ARCHIVE = """---
doc_type: memory_archive
platform: owner
user_id: 1
version: 2
owner_user_id: 1
---
# Memory Archive

## Durable Context

- [2026-01-01] Prefers casual tone [archived 2026-09-01]

## Active Threads

- [2026-06-01] Investigate the router dropping overnight [archived 2026-09-10]
- [2026-06-02] Cellar build budget — offered a checklist [archived 2026-09-10]

## Important Decisions

- Nothing archived yet.

## Useful References

- Nothing archived yet.
"""

ROUTER = "- [2026-06-01] Investigate the router dropping overnight"
CELLAR = "- [2026-06-02] Cellar build budget — offered a checklist"
TONE = "- [2026-01-01] Prefers casual tone"


def _load_module():
    module_path = REPO_ROOT / "scripts" / "palli_ctl.py"
    spec = importlib.util.spec_from_file_location("palli_ctl_memory_test_module", module_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A tmp identity tree with owner 1's MEMORY.md and MEMORY_ARCHIVE.md, the
    identity service repointed at it (the test_first_contact_targets recipe),
    the brain's version recorder and memory_refs.bump stubbed."""
    root = tmp_path / "identity"
    (root / "core").mkdir(parents=True)
    (root / "owners" / "1").mkdir(parents=True)
    shutil.copytree(REPO_ROOT / "identity" / "templates", root / "templates")
    for doc_filename, doc_type in [
        ("CORE_IDENTITY.md", "core_identity"),
        ("OPERATING_PRINCIPLES.md", "operating_principles"),
        ("CAPABILITIES_AND_BOUNDARIES.md", "capabilities_and_boundaries"),
        ("VOICE_IDENTITY.md", "voice_identity"),
    ]:
        (root / "core" / doc_filename).write_text(
            f"---\ndoc_type: {doc_type}\nplatform: global\nuser_id: 0\nversion: 1\n---\nbody\n",
            encoding="utf-8",
        )
    memory_path = root / "owners" / "1" / "MEMORY.md"
    archive_path = root / "owners" / "1" / "MEMORY_ARCHIVE.md"
    memory_path.write_text(MEMORY, encoding="utf-8")
    archive_path.write_text(ARCHIVE, encoding="utf-8")

    from core import database as database_module

    async def _noop(**_kwargs):
        return None

    monkeypatch.setattr(database_module.brain, "create_identity_document_version", _noop)

    import core.memory_refs as memory_refs

    bumps = []

    async def fake_bump(owner_id, item_ids, now=None):
        bumps.append((owner_id, list(item_ids)))

    monkeypatch.setattr(memory_refs, "bump", fake_bump)

    monkeypatch.setattr("core.config.config.IDENTITY_ROOT_PATH", str(root), raising=False)
    from core.identity.document_store import IdentityDocumentStore
    from core.identity.materialization import IdentityBundleMaterializer
    from core.identity.service import identity_service

    store = IdentityDocumentStore(identity_root=str(root))
    monkeypatch.setattr(identity_service, "document_store", store)
    monkeypatch.setattr(identity_service, "materializer", IdentityBundleMaterializer(document_store=store))
    for name in ("identity_root", "core_root", "template_root", "user_root", "owner_root"):
        monkeypatch.setattr(identity_service, name, getattr(store, name))
    monkeypatch.setattr(identity_service, "_compiled_cache", {}, raising=False)
    return {"root": root, "memory": memory_path, "archive": archive_path, "bumps": bumps}


def _run(module, argv, *, owner_id=1):
    args = module.build_parser().parse_args(["memory", *argv])
    lines: list[str] = []
    code = module.run_memory_command(args, owner_id=owner_id, out=lines.append)
    return code, lines


def test_parser_knows_the_memory_group():
    module = _load_module()
    args = module.build_parser().parse_args(["memory", "archived", "--grep", "router"])
    assert args.command == "memory" and args.memory_action == "archived" and args.grep == "router"
    args = module.build_parser().parse_args(["memory", "archived"])
    assert args.grep is None
    args = module.build_parser().parse_args(["memory", "restore", "mcb_0123456789ab"])
    assert args.memory_action == "restore" and args.item_id == "mcb_0123456789ab"


def test_archived_lists_every_archived_line_with_its_id(home):
    module = _load_module()
    code, lines = _run(module, ["archived"])
    assert code == 0
    assert lines == [
        f"{item_id_for(TONE)}  Durable Context  2026-09-01  [2026-01-01] Prefers casual tone",
        f"{item_id_for(ROUTER)}  Active Threads  2026-09-10  [2026-06-01] Investigate the router dropping overnight",
        f"{item_id_for(CELLAR)}  Active Threads  2026-09-10  [2026-06-02] Cellar build budget — offered a checklist",
    ]
    # read-only: the files did not move
    assert home["memory"].read_text(encoding="utf-8") == MEMORY
    assert home["archive"].read_text(encoding="utf-8") == ARCHIVE


def test_archived_grep_filters_case_insensitively(home):
    module = _load_module()
    code, lines = _run(module, ["archived", "--grep", "ROUTER"])
    assert code == 0 and len(lines) == 1 and lines[0].startswith(item_id_for(ROUTER))
    code, lines = _run(module, ["archived", "--grep", "nothing like this"])
    assert code == 0 and lines == ["Nothing archived matches 'nothing like this'."]


def test_archived_with_an_empty_archive_says_so(home):
    module = _load_module()
    home["archive"].write_text(ARCHIVE.split("# Memory Archive", 1)[0] + "# Memory Archive\n\n## Durable Context\n\n- Nothing archived yet.\n", encoding="utf-8")
    code, lines = _run(module, ["archived"])
    assert code == 0 and lines == ["Nothing archived yet."]


def test_restore_moves_the_line_back_and_strips_the_marker(home):
    module = _load_module()
    router_id = item_id_for(ROUTER)
    code, lines = _run(module, ["restore", router_id])
    assert code == 0
    assert lines == [f"Restored to Active Threads of MEMORY.md: {ROUTER}"]

    memory_fm, memory_body = parse_frontmatter(home["memory"].read_text(encoding="utf-8"))
    archive_fm, archive_body = parse_frontmatter(home["archive"].read_text(encoding="utf-8"))
    # back in the same section, marker gone, the section's placeholder gone
    assert memory_body == (
        "# Memory\n\n"
        "## Durable Context\n\n"
        "- [2026-05-01] Has a cat named Miso\n\n"
        "## Active Threads\n\n"
        f"{ROUTER}\n\n"
        "## Important Decisions\n\n"
        "- No important decisions recorded yet.\n\n"
        "## Useful References\n\n"
        "- No durable references recorded yet.\n"
    )
    # out of the archive; its neighbour stays
    assert "router" not in archive_body and f"{CELLAR} [archived 2026-09-10]" in archive_body
    assert "[2026-01-01] Prefers casual tone [archived 2026-09-01]" in archive_body
    # both writes went through the versioned store
    assert memory_fm["version"] == 4 and archive_fm["version"] == 3
    changelog = (home["root"] / "owners" / "1" / "CHANGELOG.md").read_text(encoding="utf-8")
    assert changelog.count(f"reason=`memory_restore:{router_id}`") == 2
    # a restore counts as a use, so tonight's sweep does not move it straight back
    assert home["bumps"] == [(1, [router_id])]
    # and the archive listing no longer shows it
    code, listed = _run(module, ["archived"])
    assert code == 0 and len(listed) == 2 and not any(router_id in ln for ln in listed)


def test_restore_appends_a_missing_section_to_memory(home):
    module = _load_module()
    home["memory"].write_text(MEMORY.replace("\n## Useful References\n\n- No durable references recorded yet.\n", ""), encoding="utf-8")
    home["archive"].write_text(ARCHIVE.replace("## Useful References\n\n- Nothing archived yet.\n",
                                               "## Useful References\n\n- the router's admin page [archived 2026-09-12]\n"), encoding="utf-8")
    code, _ = _run(module, ["restore", item_id_for("- the router's admin page")])
    assert code == 0
    _, memory_body = parse_frontmatter(home["memory"].read_text(encoding="utf-8"))
    assert memory_body.endswith("## Important Decisions\n\n- No important decisions recorded yet.\n\n## Useful References\n\n- the router's admin page\n")


def test_restore_of_an_unknown_id_is_a_runtime_error_and_writes_nothing(home):
    module = _load_module()
    with pytest.raises(RuntimeError) as excinfo:
        _run(module, ["restore", "mcb_000000000000"])
    assert "mcb_000000000000" in str(excinfo.value) and "memory archived" in str(excinfo.value)
    assert home["memory"].read_text(encoding="utf-8") == MEMORY
    assert home["archive"].read_text(encoding="utf-8") == ARCHIVE
    assert home["bumps"] == []


def test_main_exits_1_on_an_unknown_id(home, monkeypatch, capsys):
    module = _load_module()
    monkeypatch.setattr(module, "_memory_owner_id", lambda: 1)
    # main()'s RuntimeError path appends to the REAL logs/launcher/launcher.log
    # (fix round 1, I2): the seam is faked here, as tests/scripts/test_palli_ctl.py does
    logged: list[str] = []
    monkeypatch.setattr(module, "append_launcher_log", logged.append)
    assert module.main(["memory", "restore", "mcb_000000000000"]) == 1
    assert "mcb_000000000000" in capsys.readouterr().err
    assert len(logged) == 1 and "mcb_000000000000" in logged[0]


def test_restore_of_a_line_the_reflection_re_added_only_clears_the_archive(home):
    """Fix round 1 (M4): MEMORY.md already carries the line (re-added, so the
    same id); the restore removes the archive copy and adds no duplicate."""
    module = _load_module()
    re_added = "- Investigate the router dropping overnight"  # unstamped: the id ignores stamps
    home["memory"].write_text(MEMORY.replace("- No active curated threads yet.", re_added), encoding="utf-8")
    code, lines = _run(module, ["restore", item_id_for(ROUTER)])
    assert code == 0
    assert lines == [f"Already in Active Threads of MEMORY.md: {ROUTER} — removed from the archive."]
    _, memory_body = parse_frontmatter(home["memory"].read_text(encoding="utf-8"))
    _, archive_body = parse_frontmatter(home["archive"].read_text(encoding="utf-8"))
    assert memory_body.count("Investigate the router") == 1 and re_added in memory_body
    assert "Investigate the router" not in archive_body and CELLAR in archive_body


def test_memory_without_an_action_is_a_runtime_error(home):
    module = _load_module()
    with pytest.raises(RuntimeError) as excinfo:
        _run(module, [])
    assert "memory archived" in str(excinfo.value)


def test_identity_root_follows_root_dir_when_the_setting_is_relative(tmp_path, monkeypatch):
    module = _load_module()
    from core.identity.service import identity_service

    # register the singleton's current attributes with monkeypatch so the repoint is undone
    for name in ("document_store", "materializer", "identity_root", "core_root", "template_root", "user_root", "owner_root"):
        monkeypatch.setattr(identity_service, name, getattr(identity_service, name))
    monkeypatch.setattr(identity_service, "_compiled_cache", {}, raising=False)

    monkeypatch.setattr(module.config, "IDENTITY_ROOT_PATH", "identity", raising=False)
    module._point_identity_service_at(tmp_path)
    assert identity_service.identity_root == str(tmp_path / "identity")
    assert identity_service.document_store.owner_root == str(tmp_path / "identity" / "owners")

    # an absolute setting was resolved right when the singleton was built: left alone
    monkeypatch.setattr(module.config, "IDENTITY_ROOT_PATH", str(tmp_path / "elsewhere"), raising=False)
    store_before = identity_service.document_store
    module._point_identity_service_at(tmp_path / "ignored")
    assert identity_service.document_store is store_before
    assert identity_service.identity_root == str(tmp_path / "identity")
