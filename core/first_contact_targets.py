"""Lean target-tracking helper extracted from core/first_contact.py.

Reads identity/bootstrap/FIRST_CONTACT.md's frontmatter rules and the
current owner's identity documents to determine which onboarding targets
are still unfulfilled. The full pause/resume/extract/apply state machine
in core/first_contact.py (1389 lines) is replaced by the turn loop's
recall + decide + reflect pipeline in Wave 2 — that file gets deleted
in Wave 5.

This module is consumed by core/turn/recall.py to populate
TurnContext.unfulfilled_first_contact_targets, which decide.py uses to
optionally weave one shaping question into a reply.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

from core.config import config
from core.identity.frontmatter import parse_frontmatter
from core.identity.service import identity_service

logger = logging.getLogger(__name__)

# Batch 5, task 9 (memory that picks, 2026-09-28; rulings R15/R16/R49):
# when first contact happened, and whether the questions should still be
# raised. First contact = the owner's earliest identity_document_versions
# row (the first time their identity was written down). A found time is
# cached per process; None (no rows yet) is re-queried, because the first
# version may land on any turn and until it does the questions stay open.
_first_contact_cache: Dict[int, float] = {}


async def first_contact_at(owner_id: int) -> Optional[float]:
    """When this owner's identity was first written down — MIN(created_at)
    over their identity_document_versions rows (brain
    .first_identity_document_version_at) — cached per process once found.
    None when there are no rows yet, or when the brain cannot answer."""
    try:
        key = int(owner_id)
    except (TypeError, ValueError):
        return None
    cached = _first_contact_cache.get(key)
    if cached is not None:
        return cached
    try:
        from core.database import brain

        found = await brain.first_identity_document_version_at(user_id=key)
    except Exception as exc:
        logger.debug("first_contact_targets: first contact lookup failed for %s: %s", owner_id, exc)
        return None
    if found is None:
        return None
    value = float(found)
    _first_contact_cache[key] = value
    return value


def onboarding_nag_active(first_contact_at: Optional[float], now: float, nag_days: float) -> bool:
    """True while the first-contact questions should still be raised: no
    first contact recorded yet, or fewer than ``nag_days`` days since it.
    Pure; recall gates the unfulfilled-targets list on it (ruling R15)."""
    if first_contact_at is None:
        return True
    return (float(now) - float(first_contact_at)) < float(nag_days) * 86400.0


def onboarding_nag_days() -> int:
    """``memory.onboarding_nag_days`` from the settings snapshot; the
    schema's default (7) when settings cannot be read or the block is
    missing (the core.memory_refs.memory_core_budget pattern)."""
    try:
        from core.settings_boot import settings_service

        memory = settings_service.snapshot().memory
    except Exception:
        memory = None
    try:
        return int(getattr(memory, "onboarding_nag_days", 7))
    except (TypeError, ValueError):
        return 7


def is_first_contact_complete(*, owner_id: int) -> bool:
    """True when this owner has no unfulfilled first-contact targets.

    Phase C / C5 step 2 (2026-05-12): replaces the legacy
    ``first_contact_service.has_completed_setup``. Semantic shift:

      * Old: read the ``onboarding_progress.profile_setup_completed``
        flag — set by the wizard on completion.
      * New: read identity-doc state — does each required target have
        a non-default value in the owner's docs?

    Equivalent for owners who completed setup via the wizard. Subtler
    for owners who edited their identity docs (MEMORY.md /
    PERSONALITY_PROFILE.md) directly: they will be "complete" without
    a wizard-flag flip. This is consistent with the rest of the
    refactor's identity-docs-are-source-of-truth direction.
    """
    return not unfulfilled_targets_for_owner(owner_id=owner_id)


def unfulfilled_targets_for_owner(*, owner_id: int) -> list[str]:
    """Return the list of FIRST_CONTACT target names this owner has not yet satisfied.

    Empty list when:
    - FIRST_CONTACT.md is missing
    - All required targets have a non-default value in the owner's docs
    - The lookup raises (defensive: never block recall on a malformed bootstrap doc)
    """
    try:
        rules = _load_first_contact_rules()
    except Exception as exc:
        logger.warning("first_contact_targets: failed to load FIRST_CONTACT.md: %s", exc)
        return []
    if not rules:
        return []
    required = list(rules.get("required_targets") or [])
    completion_rules = rules.get("target_completion_rules") or {}

    try:
        bundle = identity_service.compile_for_owner(owner_id=owner_id)
    except Exception as exc:
        logger.warning("first_contact_targets: failed to compile identity for owner %s: %s", owner_id, exc)
        return list(required)

    unfulfilled: list[str] = []
    for target in required:
        rules_for_target = completion_rules.get(target) or []
        if _target_is_satisfied(target, rules_for_target, bundle):
            continue
        unfulfilled.append(target)
    return unfulfilled


def known_targets(identity_root: Optional[str] = None) -> List[str]:
    """FIRST_CONTACT.md's required targets, then its optional ones, without
    repeats — the vocabulary `palli_ctl onboarding answer` accepts (fix
    round 1, M9: a typo wrote a junk pref_ key that rendered in every
    reply). Reads the file under ``identity_root`` (default: the
    configured root); [] when it is missing or unreadable."""
    try:
        rules = _load_first_contact_rules(identity_root)
    except Exception as exc:
        logger.warning("first_contact_targets: failed to load FIRST_CONTACT.md: %s", exc)
        return []
    out: List[str] = []
    for name in list(rules.get("required_targets") or []) + list(rules.get("optional_targets") or []):
        text = str(name or "").strip()
        if text and text not in out:
            out.append(text)
    return out


def _load_first_contact_rules(identity_root: Optional[str] = None) -> dict[str, Any]:
    configured = identity_root or getattr(config, "IDENTITY_ROOT_PATH", "identity")
    identity_root = os.path.abspath(str(configured or "identity"))
    fc_path = os.path.join(identity_root, "bootstrap", "FIRST_CONTACT.md")
    if not os.path.isfile(fc_path):
        return {}
    with open(fc_path, "r", encoding="utf-8") as fh:
        text = fh.read()
    fm, _body = parse_frontmatter(text)
    return fm


def _target_is_satisfied(target: str, rules: list[dict], bundle: Any) -> bool:
    """A target is satisfied if any rule's check returns True."""
    for rule in rules:
        try:
            if _check_rule(target, rule, bundle):
                return True
        except Exception as exc:
            logger.debug("first_contact_targets: rule check failed for %s: %s", target, exc)
            continue
    return False


def _check_rule(target: str, rule: dict, bundle: Any) -> bool:
    source = rule.get("source")
    field_name = rule.get("field")
    match = rule.get("match")
    values = rule.get("values") or []

    actual: Any = None
    if source == "profile":
        # Read the personality_profile frontmatter for the field.
        pp = bundle.owner.get("personality_profile")
        if pp is None:
            return False
        actual = pp.frontmatter.get(field_name)
    elif source == "document":
        doc_key = rule.get("doc")
        doc = bundle.owner.get(doc_key)
        if doc is None:
            return False
        if match == "non_default":
            fields = rule.get("fields") or []
            for f in fields:
                value = str(doc.frontmatter.get(f, "")).strip()
                # A value is non-default if it's non-empty and not still a template
                # placeholder of the form {placeholder}.
                if value and not (value.startswith("{") and value.endswith("}")):
                    return True
            return False
        actual = doc.frontmatter.get(field_name)
    elif source == "preferences":
        # Batch 5, task 9 (ruling R17): the ``preferences`` identity doc.
        # A non-empty ``pref_<target>`` key means the owner answered the
        # question (any value — `palli_ctl onboarding answer`, or the
        # reflection's first_contact_resolutions); otherwise the rule's own
        # field, by dotted path into the frontmatter, under the rule's
        # operator, exactly like the document branch. This branch was
        # ``return False`` before, so checkin_posture could never close.
        doc = bundle.owner.get("preferences")
        if doc is None:
            return False
        raw = doc.frontmatter.get(f"pref_{target}")
        answered = "" if raw is None else str(raw).strip()
        # a {placeholder} is unfilled here as in the other branches (fix round 1, M11)
        if answered and not _is_placeholder(answered):
            return True
        actual = _dotted_get(doc.frontmatter, field_name)
    else:
        return False

    if match == "not_in":
        actual_str = str(actual or "").strip()
        # Template placeholders ({foo}) count as default/unfilled.
        if actual_str.startswith("{") and actual_str.endswith("}"):
            return False
        return actual_str not in {str(v) for v in values}
    if match == "equals":
        return actual == rule.get("value")
    return False


def _is_placeholder(text: str) -> bool:
    """A template value of the form ``{placeholder}`` — the shape the other
    branches treat as default / unfilled."""
    return text.startswith("{") and text.endswith("}")


def _dotted_get(frontmatter: Any, path: Any) -> Any:
    """``frontmatter["a"]["b"]`` for ``"a.b"`` — the literal key first, so a
    flat ``a.b`` key still reads. None when any step is missing."""
    if not isinstance(frontmatter, dict) or not path:
        return None
    key = str(path)
    if key in frontmatter:
        return frontmatter.get(key)
    node: Any = frontmatter
    for part in key.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node
