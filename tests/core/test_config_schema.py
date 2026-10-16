"""Focused tests for the Pydantic CoreConfigSchema (Phase 8d).

The existing `test_config_validation_matrix.py` tests verify that
`Config.validate()` still returns the right True/False values — those
tests prove behavior parity with the legacy 491-line validate() body.

This file tests the schema directly. It covers:
  - The aggregate-error behavior: a single ValidationError reports ALL
    violations, not just the first one (the legacy method short-circuited
    on first failure, hiding the rest).
  - That the schema is a pure validator (no I/O, no global state) — can
    be constructed from any duck-typed object.
  - That `extra="allow"` lets unknown Config fields pass through without
    errors, so the schema doesn't need to enumerate every Config field.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from core.config_schema import (
    ALLOWED_AGENTIC_MODES,
    ALLOWED_DEPLOYMENT_MODES,
    CoreConfigSchema,
    validate_config,
)


def _minimal_valid_payload() -> dict:
    """A payload that should pass validation cleanly.

    Mirrors the defaults the schema uses but with the cross-field
    requirements satisfied (e.g. an LLM provider key set, no voice
    features that would require additional fields).
    """
    return {
        "PALLI_DEPLOYMENT_MODE": "local",
        "ENVIRONMENT": "development",
        "ENABLE_LEGACY_HOSTED_ACCESS": False,
        "ALLOW_INSECURE_CORS": False,
        "PLUGIN_RUNTIME_ENABLED": True,
        "LLM_PROVIDER": "openai",
        "OPENAI_API_KEY": "sk-test",
        # Week 1 task 1: the bot on with a blank token is a blocker; these cases
        # are about everything else.
        "ENABLE_DISCORD_BOT": False,
        "ENABLE_VOICE_FEATURES": False,
        "ENABLE_WEB_SEARCH": False,
        "ENABLE_IDENTITY_ARCHITECTURE": True,
        "IDENTITY_ROOT_PATH": "identity",
        "DATABASE_PATH": "data/palli-local.sqlite3",
        "FRONTEND_ORIGINS": ["http://localhost:3000"],
        "WEB_OPS_EVENT_TYPES": ["visit", "signup"],
    }


def test_schema_accepts_valid_minimum():
    """Sanity check — the minimal payload passes."""
    schema = CoreConfigSchema.model_validate(_minimal_valid_payload())
    assert schema.LLM_PROVIDER == "openai"
    assert schema.PALLI_DEPLOYMENT_MODE == "local"


def test_schema_aggregates_field_violations():
    """Pydantic surfaces every field-level violation in one ValidationError.

    The legacy `Config.validate()` returned False on the first failing
    if-statement, hiding all subsequent problems behind it. Pydantic
    aggregates field-level errors so operators see the complete punch
    list on first run.

    NOTE: Pydantic only runs model_validators AFTER all fields validate,
    so a field error short-circuits cross-field validators. Even so,
    aggregating multiple field errors together is a strict improvement
    over the legacy first-fail-and-stop behavior.
    """
    payload = _minimal_valid_payload()
    # Three independent field-level violations — must all surface together.
    payload["AGENTIC_MAX_STEPS"] = 0            # below ge=1
    payload["VOICE_SHORT_REPLY_MAX_CHARS"] = 5  # below ge=40
    payload["WEB_OPS_VISIT_WINDOW_SECONDS"] = 30  # below ge=60

    with pytest.raises(ValidationError) as excinfo:
        CoreConfigSchema.model_validate(payload)

    errors = excinfo.value.errors()
    # All three violations are reported in ONE error, not one-at-a-time.
    error_fields = {".".join(str(p) for p in e.get("loc", ())) for e in errors}
    assert "AGENTIC_MAX_STEPS" in error_fields
    assert "VOICE_SHORT_REPLY_MAX_CHARS" in error_fields
    assert "WEB_OPS_VISIT_WINDOW_SECONDS" in error_fields


def test_schema_enum_validator_aggregates_within_self():
    """Within `_validate_enums`, every bad enum value is reported together,
    not just the first one. Compare to the legacy method which returned
    on the very first if-statement that hit.
    """
    payload = _minimal_valid_payload()
    payload["AGENTIC_MODE"] = "broken"             # invalid enum
    payload["VOICE_RUNTIME_MODE"] = "weird"        # invalid enum
    payload["AGENTIC_QUEUE_MODE"] = "unknown"      # invalid enum

    with pytest.raises(ValidationError) as excinfo:
        CoreConfigSchema.model_validate(payload)
    msg = str(excinfo.value)
    assert "AGENTIC_MODE" in msg
    assert "VOICE_RUNTIME_MODE" in msg
    assert "AGENTIC_QUEUE_MODE" in msg


def test_schema_extra_fields_are_allowed():
    """The schema must not reject Config fields it doesn't enumerate.

    Config has ~150 fields without explicit validation rules; the schema
    only adds rules for the ~80 that have one. Setting extra="allow" lets
    the rest pass through untouched, so the schema can be the validator
    for the full Config without enumerating every cosmetic string field.
    """
    payload = _minimal_valid_payload()
    payload["SOME_UNKNOWN_FIELD"] = "anything"
    payload["RANDOM_TIMEOUT_SECONDS"] = 42.0

    schema = CoreConfigSchema.model_validate(payload)
    # Extra fields are accessible on the model (extra="allow" stores them).
    assert getattr(schema, "SOME_UNKNOWN_FIELD") == "anything"
    assert getattr(schema, "RANDOM_TIMEOUT_SECONDS") == 42.0


def test_schema_rejects_invalid_deployment_mode():
    """Enum-like validation works."""
    payload = _minimal_valid_payload()
    payload["PALLI_DEPLOYMENT_MODE"] = "wibble"

    with pytest.raises(ValidationError) as excinfo:
        CoreConfigSchema.model_validate(payload)
    assert "PALLI_DEPLOYMENT_MODE" in str(excinfo.value)


def test_schema_rejects_codex_without_model():
    """Cross-field validator: codex provider requires CODEX_MODEL set."""
    payload = _minimal_valid_payload()
    payload["LLM_PROVIDER"] = "codex"
    payload["CODEX_MODEL"] = ""
    payload["CODEX_TIMEOUT_SECONDS"] = 0.0  # also invalid (must be > 0)

    with pytest.raises(ValidationError) as excinfo:
        CoreConfigSchema.model_validate(payload)
    msg = str(excinfo.value)
    assert "CODEX_MODEL" in msg
    assert "CODEX_TIMEOUT_SECONDS" in msg


def test_schema_voice_runtime_constants_validated_unconditionally():
    """Voice model names still baked into the runtime must be non-empty
    even when voice features are disabled.

    (Step 31, 2026-08-20: the six OPENAI_REALTIME_* names left the
    non-empty tuple with the deleted bridge — this pin moved to
    VOICE_LOCAL_STT_MODEL, which classic STT still bakes in.)
    """
    payload = _minimal_valid_payload()
    payload["ENABLE_VOICE_FEATURES"] = False
    payload["VOICE_LOCAL_STT_MODEL"] = ""

    with pytest.raises(ValidationError) as excinfo:
        CoreConfigSchema.model_validate(payload)
    assert "VOICE_LOCAL_STT_MODEL" in str(excinfo.value)


def test_schema_cors_wildcard_blocked_in_production():
    """Cross-field rule: '*' in FRONTEND_ORIGINS is rejected in prod
    unless ALLOW_INSECURE_CORS is explicitly set.
    """
    payload = _minimal_valid_payload()
    payload["ENVIRONMENT"] = "production"
    payload["FRONTEND_ORIGINS"] = ["*"]
    payload["ALLOW_INSECURE_CORS"] = False

    with pytest.raises(ValidationError) as excinfo:
        CoreConfigSchema.model_validate(payload)
    assert "CORS misconfigured" in str(excinfo.value)


def test_schema_cors_wildcard_allowed_with_explicit_opt_in():
    """The same wildcard is OK when ALLOW_INSECURE_CORS=true."""
    payload = _minimal_valid_payload()
    payload["ENVIRONMENT"] = "production"
    payload["FRONTEND_ORIGINS"] = ["*"]
    payload["ALLOW_INSECURE_CORS"] = True

    # Should not raise.
    schema = CoreConfigSchema.model_validate(payload)
    assert "*" in schema.FRONTEND_ORIGINS


def test_schema_web_ops_event_types_legacy_hosted():
    """checkout_started / payment_succeeded only valid when legacy
    hosted access is enabled (or deployment mode is 'cloud').
    """
    payload = _minimal_valid_payload()
    payload["ENABLE_LEGACY_HOSTED_ACCESS"] = False
    payload["WEB_OPS_EVENT_TYPES"] = ["visit", "checkout_started"]

    with pytest.raises(ValidationError) as excinfo:
        CoreConfigSchema.model_validate(payload)
    assert "checkout_started" in str(excinfo.value)

    # Same payload with legacy hosted enabled — should pass.
    payload["ENABLE_LEGACY_HOSTED_ACCESS"] = True
    schema = CoreConfigSchema.model_validate(payload)
    assert "checkout_started" in schema.WEB_OPS_EVENT_TYPES


def test_validate_config_returns_bool_for_legacy_callers():
    """`validate_config` is a thin wrapper that returns True/False, so
    callers like Config.validate() can keep their bool contract.
    """
    valid_obj = SimpleNamespace(**_minimal_valid_payload())
    # SimpleNamespace exposes attributes the same way Config does.
    # validate_config also reads a few extra fields via dir() — if any
    # are missing, they default to falsy values which still validate
    # (HOME_ASSISTANT_ENABLED=False makes the home-assistant validator
    # a no-op, etc.), so the minimal payload is enough to pass.
    # Add any missing required fields via the schema defaults.
    for f, v in CoreConfigSchema.model_fields.items():
        if not hasattr(valid_obj, f) and v.default is not None:
            setattr(valid_obj, f, v.default)
    assert validate_config(valid_obj) is True

    invalid_obj = SimpleNamespace(**_minimal_valid_payload())
    invalid_obj.AGENTIC_MODE = "totally_invalid"
    for f, v in CoreConfigSchema.model_fields.items():
        if not hasattr(invalid_obj, f) and v.default is not None:
            setattr(invalid_obj, f, v.default)
    assert validate_config(invalid_obj) is False


def test_allowed_sets_exported_for_introspection():
    """The frozenset constants are deliberately public so future tooling
    (palli_ctl doctor, settings UI, etc.) can introspect what's valid.
    """
    assert "local" in ALLOWED_DEPLOYMENT_MODES
    assert "cloud" in ALLOWED_DEPLOYMENT_MODES
    assert "hybrid" in ALLOWED_AGENTIC_MODES
    assert "off" in ALLOWED_AGENTIC_MODES
