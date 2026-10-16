"""`palli_ctl onboarding answer <target> <value>` (Batch 5, task 9, rulings
R15/R17). The first-contact questions are answered by writing
``pref_<target>: <value>`` on the owner's ``preferences`` document through
the versioned store — the same key core.first_contact_targets reads to
close the target. The wrap runs `onboarding answer checkin_posture
proactive` for the owner, whose memory file already records the answer;
"proactive" is not one of FIRST_CONTACT.md's options and is accepted as
free text. Everything runs against a tmp identity tree; never the owner's
files."""
from __future__ import annotations

import importlib.util
import shutil
from pathlib import Path

import pytest

from core.identity.frontmatter import parse_frontmatter

REPO_ROOT = Path(__file__).resolve().parents[2]

PREFERENCES = """---
doc_type: preferences
platform: owner
user_id: 1
version: 2
last_updated_by: system
preferences_summary: "{preferences_summary}"
pref_tea: oolong
owner_user_id: 1
---
# User Preferences

body
"""


def _load_module():
    module_path = REPO_ROOT / "scripts" / "palli_ctl.py"
    spec = importlib.util.spec_from_file_location("palli_ctl_onboarding_test_module", module_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A tmp identity tree with owner 1's PREFERENCES.md, the REAL
    FIRST_CONTACT.md, the identity service repointed at it and the brain's
    version recorder stubbed (the test_palli_ctl_memory recipe)."""
    root = tmp_path / "identity"
    (root / "core").mkdir(parents=True)
    (root / "owners" / "1").mkdir(parents=True)
    (root / "bootstrap").mkdir(parents=True)
    shutil.copytree(REPO_ROOT / "identity" / "templates", root / "templates")
    shutil.copy(REPO_ROOT / "identity" / "bootstrap" / "FIRST_CONTACT.md", root / "bootstrap" / "FIRST_CONTACT.md")
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
    prefs_path = root / "owners" / "1" / "PREFERENCES.md"
    prefs_path.write_text(PREFERENCES, encoding="utf-8")

    from core import database as database_module

    async def _noop(**_kwargs):
        return None

    monkeypatch.setattr(database_module.brain, "create_identity_document_version", _noop)
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
    return {"root": root, "preferences": prefs_path}


def _run(module, argv, *, owner_id=1):
    args = module.build_parser().parse_args(["onboarding", *argv])
    lines: list[str] = []
    code = module.run_onboarding_command(args, owner_id=owner_id, out=lines.append)
    return code, lines


def test_parser_knows_the_onboarding_group():
    module = _load_module()
    args = module.build_parser().parse_args(["onboarding", "answer", "checkin_posture", "proactive"])
    assert args.command == "onboarding" and args.onboarding_action == "answer"
    assert args.target == "checkin_posture" and args.value == "proactive"
    args = module.build_parser().parse_args(["onboarding", "answer", "checkin_posture", "only when something is due"])
    assert args.value == "only when something is due"


def test_answer_writes_the_pref_key_and_closes_the_target(home):
    from core.first_contact_targets import unfulfilled_targets_for_owner

    module = _load_module()
    assert "checkin_posture" in unfulfilled_targets_for_owner(owner_id=1)
    code, lines = _run(module, ["answer", "checkin_posture", "proactive"])
    assert code == 0
    assert lines == ["Recorded pref_checkin_posture: proactive on the owner's preferences document."]
    fm, _body = parse_frontmatter(home["preferences"].read_text(encoding="utf-8"))
    assert fm["pref_checkin_posture"] == "proactive"
    assert fm["pref_tea"] == "oolong"           # the rest of the doc is untouched
    assert fm["version"] == 3                   # written through the versioned store
    changelog = (home["root"] / "owners" / "1" / "CHANGELOG.md").read_text(encoding="utf-8")
    assert "onboarding_answer:checkin_posture" in changelog
    from core.identity.service import identity_service
    identity_service._compiled_cache = {}
    assert "checkin_posture" not in unfulfilled_targets_for_owner(owner_id=1)


def test_answer_accepts_free_text_and_a_pref_prefixed_target(home):
    module = _load_module()
    code, lines = _run(module, ["answer", "checkin_posture", "only when something is due"])
    assert code == 0
    fm, _ = parse_frontmatter(home["preferences"].read_text(encoding="utf-8"))
    assert fm["pref_checkin_posture"] == "only when something is due"
    # a target already spelled with the prefix does not become pref_pref_
    code, lines = _run(module, ["answer", "pref_voice_posture", "quiet"])
    assert code == 0 and lines == ["Recorded pref_voice_posture: quiet on the owner's preferences document."]
    fm, _ = parse_frontmatter(home["preferences"].read_text(encoding="utf-8"))
    assert fm["pref_voice_posture"] == "quiet" and "pref_pref_voice_posture" not in fm


def test_an_unknown_target_is_a_runtime_error_naming_the_valid_targets(home):
    """Fix round 1 (M9): a typo such as `checkin_postur` wrote a junk pref_
    key onto the live preferences doc, which then rendered in every reply.
    The target must be one of FIRST_CONTACT.md's required + optional
    targets; the value stays free text."""
    module = _load_module()
    with pytest.raises(RuntimeError) as excinfo:
        _run(module, ["answer", "checkin_postur", "proactive"])
    message = str(excinfo.value)
    assert "checkin_postur" in message
    for valid in ("user_name", "companion_style", "autonomy_preset", "checkin_posture", "voice_posture",
                  "relationship_style", "owner_pronouns"):
        assert valid in message
    assert home["preferences"].read_text(encoding="utf-8") == PREFERENCES
    # an optional target is valid too
    code, lines = _run(module, ["answer", "owner_pronouns", "they/them"])
    assert code == 0 and lines == ["Recorded pref_owner_pronouns: they/them on the owner's preferences document."]


def test_a_missing_first_contact_file_refuses_rather_than_guessing(home):
    module = _load_module()
    (home["root"] / "bootstrap" / "FIRST_CONTACT.md").unlink()
    with pytest.raises(RuntimeError) as excinfo:
        _run(module, ["answer", "checkin_posture", "proactive"])
    assert "FIRST_CONTACT.md" in str(excinfo.value)
    assert home["preferences"].read_text(encoding="utf-8") == PREFERENCES


def test_an_empty_target_or_value_is_a_runtime_error_and_writes_nothing(home):
    module = _load_module()
    for argv in (["answer", "  ", "proactive"], ["answer", "checkin_posture", "  "], ["answer", "pref_", "x"]):
        with pytest.raises(RuntimeError):
            _run(module, argv)
    assert home["preferences"].read_text(encoding="utf-8") == PREFERENCES


def test_a_refused_write_is_a_runtime_error(home, monkeypatch):
    module = _load_module()
    from core.identity.service import identity_service

    async def refused(**kwargs):
        return None

    monkeypatch.setattr(identity_service, "silently_maintain_document", refused)
    with pytest.raises(RuntimeError) as excinfo:
        _run(module, ["answer", "checkin_posture", "proactive"])
    assert "pref_checkin_posture" in str(excinfo.value)
    assert home["preferences"].read_text(encoding="utf-8") == PREFERENCES


def test_main_exits_1_when_the_write_is_refused(home, monkeypatch, capsys):
    module = _load_module()
    from core.identity.service import identity_service

    async def refused(**kwargs):
        return None

    monkeypatch.setattr(identity_service, "silently_maintain_document", refused)
    monkeypatch.setattr(module, "_memory_owner_id", lambda: 1)
    logged: list[str] = []
    monkeypatch.setattr(module, "append_launcher_log", logged.append)
    assert module.main(["onboarding", "answer", "checkin_posture", "proactive"]) == 1
    assert "pref_checkin_posture" in capsys.readouterr().err
    assert len(logged) == 1 and "pref_checkin_posture" in logged[0]


def test_onboarding_without_an_action_is_a_runtime_error(home):
    module = _load_module()
    with pytest.raises(RuntimeError) as excinfo:
        _run(module, [])
    assert "onboarding answer" in str(excinfo.value)
