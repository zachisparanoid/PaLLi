"""Batch 5, task 9 (memory that picks, 2026-09-28; rulings R15, R16, R17,
R49): the onboarding check-in question can be ANSWERED, and its nag ends
after the first week.

Until now the ``preferences`` branch of ``_check_rule`` was ``return
False`` — so ``checkin_posture`` could never be satisfied, the reflection
parsed a ``checkin_posture`` resolution and dropped it (no branch wrote
it), and the reflection prompt carried the question forever. Now: a
non-empty ``pref_<target>`` key on the preferences doc satisfies the
target (the owner answered it — any value), else the rule's own field is
evaluated by dotted path with the rule's operator, like the document
branch; the reflection writes ``pref_checkin_posture``; and recall drops
the whole unfulfilled list once ``memory.onboarding_nag_days`` (7) have
passed since the owner's first identity-document version.

The ``tmp_identity`` fixture is tests/core/test_first_contact_targets.py's
recipe with a PREFERENCES.md and the REAL checkin_posture rules.
"""
from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, create_autospec

import pytest

from core.identity.service import identity_service  # noqa: F401  (a sane birth before any patch)

DAY = 86400.0

_CHECKIN_RULES = (
    '[{"source": "preferences", "field": "proactive_consent.enabled", "match": "equals", "value": false}, '
    '{"source": "preferences", "field": "proactive_consent.source", "match": "not_in", "values": ["", "local_default"]}, '
    '{"source": "preferences", "field": "check_in_frequency", "match": "not_in", "values": ["", "normal", "daily"]}]'
)


def _preferences_doc(extra: str = "") -> str:
    return (
        "---\ndoc_type: preferences\nplatform: owner\nuser_id: 1\nversion: 1\n"
        "last_updated_by: system\npreferences_summary: \"{preferences_summary}\"\n"
        f"{extra}owner_user_id: 1\n---\n# User Preferences\nbody\n"
    )


@pytest.fixture
def tmp_identity(tmp_path: Path, monkeypatch):
    root = tmp_path / "identity"
    (root / "core").mkdir(parents=True)
    (root / "owners" / "1").mkdir(parents=True)
    (root / "templates").mkdir(parents=True)
    (root / "bootstrap").mkdir(parents=True)
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
    (root / "owners" / "1" / "PREFERENCES.md").write_text(_preferences_doc(), encoding="utf-8")
    (root / "bootstrap" / "FIRST_CONTACT.md").write_text(
        "---\ndoc_type: first_contact\nscope: bootstrap\nplatform: global\nversion: 1\n"
        'required_targets: ["checkin_posture"]\n'
        f'target_completion_rules: {{"checkin_posture": {_CHECKIN_RULES}}}\n'
        "---\n# First Contact\nbody\n",
        encoding="utf-8",
    )

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
    return root


def _set_preferences(root: Path, extra: str) -> None:
    (root / "owners" / "1" / "PREFERENCES.md").write_text(_preferences_doc(extra), encoding="utf-8")
    from core.identity.service import identity_service
    identity_service._compiled_cache = {}


def _unfulfilled() -> list[str]:
    from core.first_contact_targets import unfulfilled_targets_for_owner
    return unfulfilled_targets_for_owner(owner_id=1)


# ── the preferences branch ────────────────────────────────────────────────

def test_the_question_is_open_when_nothing_answers_it(tmp_identity):
    assert _unfulfilled() == ["checkin_posture"]


def test_an_answered_pref_key_satisfies_the_target_whatever_its_value(tmp_identity):
    _set_preferences(tmp_identity, "pref_checkin_posture: proactive\n")
    assert _unfulfilled() == []
    # not one of FIRST_CONTACT.md's question_options either — any value the owner gave
    _set_preferences(tmp_identity, 'pref_checkin_posture: "only when something is due"\n')
    assert _unfulfilled() == []


def test_an_empty_pref_key_does_not_answer_it(tmp_identity):
    _set_preferences(tmp_identity, 'pref_checkin_posture: ""\n')
    assert _unfulfilled() == ["checkin_posture"]


def test_a_placeholder_pref_value_does_not_answer_it(tmp_identity):
    """Fix round 1 (M11): the other branches treat `{placeholder}` as
    unfilled; a `pref_<target>` value shaped like one is not an answer."""
    _set_preferences(tmp_identity, 'pref_checkin_posture: "{pref_checkin_posture}"\n')
    assert _unfulfilled() == ["checkin_posture"]
    _set_preferences(tmp_identity, 'pref_checkin_posture: "{checkin_posture}"\n')
    assert _unfulfilled() == ["checkin_posture"]
    _set_preferences(tmp_identity, 'pref_checkin_posture: "{not a placeholder, a real note}"\n')
    assert _unfulfilled() == ["checkin_posture"]   # the guard is by shape, as in the other branches


def test_known_targets_are_first_contacts_required_then_optional(tmp_identity):
    """Fix round 1 (M9): the CLI validates its target against these."""
    from core.first_contact_targets import known_targets

    assert known_targets() == ["checkin_posture"]
    assert known_targets(str(tmp_identity)) == ["checkin_posture"]
    (tmp_identity / "bootstrap" / "FIRST_CONTACT.md").write_text(
        "---\ndoc_type: first_contact\nversion: 1\n"
        'required_targets: ["user_name", "checkin_posture"]\noptional_targets: ["owner_pronouns", "user_name"]\n'
        "---\nbody\n",
        encoding="utf-8",
    )
    assert known_targets() == ["user_name", "checkin_posture", "owner_pronouns"]
    (tmp_identity / "bootstrap" / "FIRST_CONTACT.md").unlink()
    assert known_targets() == []


def test_the_rules_own_field_still_satisfies_it(tmp_identity):
    # not_in ["", "normal", "daily"]
    _set_preferences(tmp_identity, "check_in_frequency: frequent\n")
    assert _unfulfilled() == []
    _set_preferences(tmp_identity, "check_in_frequency: normal\n")
    assert _unfulfilled() == ["checkin_posture"]
    _set_preferences(tmp_identity, 'check_in_frequency: "{check_in_frequency}"\n')   # a placeholder is unfilled
    assert _unfulfilled() == ["checkin_posture"]


def test_a_dotted_field_walks_into_the_frontmatter(tmp_identity):
    # equals false
    _set_preferences(tmp_identity, 'proactive_consent: {"enabled": false, "source": "local_default"}\n')
    assert _unfulfilled() == []
    # not_in ["", "local_default"]
    _set_preferences(tmp_identity, 'proactive_consent: {"enabled": true, "source": "him"}\n')
    assert _unfulfilled() == []
    _set_preferences(tmp_identity, 'proactive_consent: {"enabled": true, "source": "local_default"}\n')
    assert _unfulfilled() == ["checkin_posture"]


def test_a_missing_preferences_doc_leaves_the_question_open(tmp_identity):
    (tmp_identity / "owners" / "1" / "PREFERENCES.md").unlink()
    from core.identity.service import identity_service
    identity_service._compiled_cache = {}
    assert _unfulfilled() == ["checkin_posture"]


# ── the nag window ────────────────────────────────────────────────────────

def test_onboarding_nag_active_truth_table():
    from core.first_contact_targets import onboarding_nag_active

    now = 1_800_000_000.0
    assert onboarding_nag_active(None, now, 7) is True                 # no first contact yet: keep asking
    assert onboarding_nag_active(now - 3 * DAY, now, 7) is True
    assert onboarding_nag_active(now - 6.99 * DAY, now, 7) is True
    assert onboarding_nag_active(now - 7 * DAY, now, 7) is False        # the week is up
    assert onboarding_nag_active(now - 30 * DAY, now, 7) is False
    assert onboarding_nag_active(now - 30 * DAY, now, 60) is True       # a longer setting
    assert onboarding_nag_active(now - 1.0, now, 0) is False            # 0 days: never nag once contact exists
    assert onboarding_nag_active(None, now, 0) is True


def test_onboarding_nag_days_reads_the_settings_snapshot_and_falls_back_to_7(monkeypatch):
    from types import SimpleNamespace

    import core.settings_boot as settings_boot
    from core.first_contact_targets import onboarding_nag_days

    monkeypatch.setattr(
        settings_boot.settings_service, "snapshot",
        lambda: SimpleNamespace(memory=SimpleNamespace(onboarding_nag_days=3)),
    )
    assert onboarding_nag_days() == 3
    monkeypatch.setattr(settings_boot.settings_service, "snapshot", lambda: SimpleNamespace(memory=None))
    assert onboarding_nag_days() == 7

    def boom():
        raise RuntimeError("settings not booted")

    monkeypatch.setattr(settings_boot.settings_service, "snapshot", boom)
    assert onboarding_nag_days() == 7


@pytest.mark.asyncio
async def test_first_contact_at_caches_a_found_time_per_process_and_requeries_none(monkeypatch):
    import core.first_contact_targets as fct
    from core.database import brain

    monkeypatch.setattr(fct, "_first_contact_cache", {})
    answers = {"calls": 0, "value": None}

    async def fake_min(*, user_id):
        answers["calls"] += 1
        return answers["value"]

    monkeypatch.setattr(brain, "first_identity_document_version_at", fake_min)
    assert await fct.first_contact_at(5) is None
    assert await fct.first_contact_at(5) is None
    assert answers["calls"] == 2                     # None is not cached: the first version may land any turn
    answers["value"] = 1234.5
    assert await fct.first_contact_at(5) == 1234.5
    assert await fct.first_contact_at(5) == 1234.5
    assert answers["calls"] == 3                     # a found time is cached for the process
    assert await fct.first_contact_at(6) == 1234.5   # another owner: its own query
    assert answers["calls"] == 4


@pytest.mark.asyncio
async def test_first_contact_at_is_none_when_the_brain_fails(monkeypatch):
    import core.first_contact_targets as fct
    from core.database import brain

    monkeypatch.setattr(fct, "_first_contact_cache", {})

    async def boom(*, user_id):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(brain, "first_identity_document_version_at", boom)
    assert await fct.first_contact_at(5) is None


# ── the brain: MIN(created_at) over the owner's identity_document_versions ─

_VERSIONS_OWNER = 736_251


@pytest.mark.asyncio
async def test_brain_first_identity_document_version_at_is_the_earliest_row_or_none():
    from core.database import brain

    await brain.initialize()
    conn = await brain.get_connection()
    try:
        await conn.execute("DELETE FROM identity_document_versions WHERE user_id = $1", _VERSIONS_OWNER)
    finally:
        await conn.close()
    try:
        assert await brain.first_identity_document_version_at(user_id=_VERSIONS_OWNER) is None
        before = time.time()
        await brain.create_identity_document_version(
            user_id=_VERSIONS_OWNER, platform="owner", doc_key="memory", file_path="MEMORY.md", version=1,
            actor_type="system", actor_id="test", reason="first", content="# Memory\n", frontmatter={},
        )
        first = await brain.first_identity_document_version_at(user_id=_VERSIONS_OWNER)
        assert isinstance(first, float) and before - 1 <= first <= time.time() + 1
        await brain.create_identity_document_version(
            user_id=_VERSIONS_OWNER, platform="owner", doc_key="preferences", file_path="PREFERENCES.md",
            version=2, actor_type="system", actor_id="test", reason="second", content="x", frontmatter={},
        )
        assert await brain.first_identity_document_version_at(user_id=_VERSIONS_OWNER) == first
        # another owner's rows do not count
        assert await brain.first_identity_document_version_at(user_id=_VERSIONS_OWNER + 1) is None
    finally:
        conn = await brain.get_connection()
        try:
            await conn.execute("DELETE FROM identity_document_versions WHERE user_id = $1", _VERSIONS_OWNER)
        finally:
            await conn.close()


@pytest.mark.asyncio
async def test_first_contact_reads_the_same_date_after_the_history_prune(tmp_path):
    """Batch 8, task 3 (Review Focus 3): the nightly prune of identity
    history keeps each stream's first copy, so first contact — MIN(created_at)
    over a user's rows, any platform or document — reads the same date before
    and after, on a temp database, with a hundred days of copies to prune."""
    from core.database import PalliBrain
    from core.identity.history_prune import prune_history

    db = PalliBrain(db_path=str(tmp_path / "first-contact.sqlite3"))
    await db.initialize()

    rows = [(1, "discord", "preferences", 1, 0.5 * DAY)]          # her very first note of user 1
    for user in (1, 2):
        for doc_key in ("changelog", "memory"):
            for d in range(1, 101):
                for i in range(3):
                    rows.append((user, "owner", doc_key, (d - 1) * 3 + i + 1, d * DAY + i))

    try:
        conn = await db.get_connection()
        try:
            async with conn.transaction():
                for user_id, platform, doc_key, version, at in rows:
                    await conn.execute(
                        "INSERT INTO identity_document_versions (user_id, platform, doc_key, file_path, "
                        "version, actor_type, actor_id, reason, content, frontmatter, created_at) "
                        "VALUES ($1, $2, $3, $4, $5, 'system', 'test', 'save', 'body', '{}', $6)",
                        user_id, platform, doc_key, f"{doc_key}.md", version, at,
                    )
        finally:
            await conn.close()
        before = {u: await db.first_identity_document_version_at(user_id=u) for u in (1, 2)}
        result = await prune_history(db, keep_last=10, daily_days=30, now=101 * DAY)
        assert result.removed > 0
        after = {u: await db.first_identity_document_version_at(user_id=u) for u in (1, 2)}
        assert after == before == {1: 0.5 * DAY, 2: 1 * DAY}
    finally:
        await db.close()


# ── recall: the gate ──────────────────────────────────────────────────────

def _load_recall():
    from tests.agent.conftest import load_module
    return load_module("core/turn/recall.py", "recall_under_test_for_onboarding")


def _stub_recall(monkeypatch, *, first_contact, nag_days=7):
    import ai.fact_retrieval as fact_retrieval
    import ai.inner_memory_index as ami
    import core.conversation_history_store as chs
    import core.first_contact_targets as fct
    import core.identity.service as identity_service_module

    monkeypatch.setattr(fact_retrieval, "get_decay_weighted_facts", AsyncMock(return_value=[]))
    monkeypatch.setattr(identity_service_module.identity_service, "compile_for_owner", lambda owner_id: None)
    monkeypatch.setattr(chs.ConversationHistoryStore, "get_conversation_history", AsyncMock(return_value=[]))
    monkeypatch.setattr(ami, "query", AsyncMock(return_value=[]))
    monkeypatch.setattr(fct, "unfulfilled_targets_for_owner", lambda owner_id: ["checkin_posture", "voice_posture"])

    async def fake_first_contact_at(owner_id):
        return first_contact

    monkeypatch.setattr(fct, "first_contact_at", fake_first_contact_at)
    monkeypatch.setattr(fct, "onboarding_nag_days", lambda: nag_days)


def _turn():
    from core.turn.types import Turn, TurnMetadata
    return Turn(surface="dashboard", owner_id=7, content="hey", images=[], metadata=TurnMetadata())


@pytest.mark.asyncio
async def test_no_nag_thirty_days_after_first_contact(monkeypatch):
    _stub_recall(monkeypatch, first_contact=time.time() - 30 * DAY)
    ctx = await _load_recall().build_turn_context(_turn())
    assert ctx.unfulfilled_first_contact_targets == ()


@pytest.mark.asyncio
async def test_the_list_within_the_first_week(monkeypatch):
    _stub_recall(monkeypatch, first_contact=time.time() - 2 * DAY)
    ctx = await _load_recall().build_turn_context(_turn())
    assert ctx.unfulfilled_first_contact_targets == ("checkin_posture", "voice_posture")


@pytest.mark.asyncio
async def test_the_list_when_there_is_no_first_contact_yet(monkeypatch):
    _stub_recall(monkeypatch, first_contact=None)
    ctx = await _load_recall().build_turn_context(_turn())
    assert ctx.unfulfilled_first_contact_targets == ("checkin_posture", "voice_posture")


@pytest.mark.asyncio
async def test_the_setting_widens_the_window(monkeypatch):
    _stub_recall(monkeypatch, first_contact=time.time() - 30 * DAY, nag_days=60)
    ctx = await _load_recall().build_turn_context(_turn())
    assert ctx.unfulfilled_first_contact_targets == ("checkin_posture", "voice_posture")


@pytest.mark.asyncio
async def test_a_failing_first_contact_lookup_never_breaks_the_turn(monkeypatch):
    _stub_recall(monkeypatch, first_contact=None)
    import core.first_contact_targets as fct

    async def boom(owner_id):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(fct, "first_contact_at", boom)
    ctx = await _load_recall().build_turn_context(_turn())
    assert ctx.unfulfilled_first_contact_targets == ()


# ── the reflection writes the answer ──────────────────────────────────────

@pytest.mark.asyncio
async def test_the_reflection_writes_the_checkin_answer_to_the_preferences_doc():
    from unittest.mock import patch

    import core.turn.reflect as reflect_module
    from core.identity.service import IdentityService

    service = create_autospec(IdentityService, instance=True)
    service.silently_maintain_document = AsyncMock(return_value=MagicMock())
    service.update_structured_document = AsyncMock(return_value=MagicMock())
    with patch("core.turn.reflect.identity_service", service):
        applied = await reflect_module._apply_first_contact_resolutions(
            owner_id=42, resolutions={"checkin_posture": "rare"},
        )
    assert applied is True
    service.silently_maintain_document.assert_awaited_once()
    call = service.silently_maintain_document.await_args
    assert call.kwargs["doc_key"] == "preferences"
    assert call.kwargs["updates"] == {"pref_checkin_posture": "rare"}
    assert call.kwargs["user_id"] == 42 and call.kwargs["identity_owner_id"] == 42
    assert call.kwargs["platform"] == "owner"
    assert call.kwargs["actor_id"] == "reflect" and "first_contact" in call.kwargs["reason"]
    service.update_structured_document.assert_not_awaited()


@pytest.mark.asyncio
async def test_an_empty_checkin_answer_writes_nothing():
    from unittest.mock import patch

    import core.turn.reflect as reflect_module
    from core.identity.service import IdentityService

    service = create_autospec(IdentityService, instance=True)
    service.silently_maintain_document = AsyncMock(return_value=MagicMock())
    with patch("core.turn.reflect.identity_service", service):
        applied = await reflect_module._apply_first_contact_resolutions(
            owner_id=42, resolutions={"checkin_posture": "   "},
        )
    assert applied is False
    service.silently_maintain_document.assert_not_awaited()
