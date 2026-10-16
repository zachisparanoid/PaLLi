"""Tests for the Phase 8e legacy-residue inspector.

The inspector is a pure read-only check — it never mutates state. These
tests build small SQLite fixtures with and without each piece of legacy
residue and verify the inspector reports them correctly.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from core.legacy_residue import (
    ResidueFinding,
    ResidueReport,
    check_legacy_residue,
)


def _make_clean_fixture(tmp_path: Path) -> Path:
    """A DB schema that has the legacy COLUMNS but no legacy DATA."""
    db_path = tmp_path / "clean.sqlite3"
    with sqlite3.connect(str(db_path)) as conn:
        conn.executescript(
            """
            CREATE TABLE users (
                id INTEGER PRIMARY KEY,
                stripe_customer_id TEXT,
                mfa_secret TEXT,
                mfa_enabled INTEGER DEFAULT 0
            );
            CREATE TABLE beta_seat_reservations (
                id INTEGER PRIMARY KEY,
                user_id INTEGER,
                status TEXT
            );
            -- Insert one user with no legacy data — this represents the
            -- expected local-mode steady state.
            INSERT INTO users (id, stripe_customer_id, mfa_secret) VALUES (1, NULL, NULL);
            """
        )
    return db_path


def _make_residue_fixture(tmp_path: Path) -> Path:
    """A DB with all three flavors of legacy residue populated."""
    db_path = tmp_path / "residue.sqlite3"
    with sqlite3.connect(str(db_path)) as conn:
        conn.executescript(
            """
            CREATE TABLE users (
                id INTEGER PRIMARY KEY,
                stripe_customer_id TEXT,
                mfa_secret TEXT,
                mfa_enabled INTEGER DEFAULT 0
            );
            CREATE TABLE beta_seat_reservations (
                id INTEGER PRIMARY KEY,
                user_id INTEGER,
                status TEXT
            );
            -- Two users with stripe IDs, one of them also with MFA secret.
            INSERT INTO users (id, stripe_customer_id, mfa_secret)
              VALUES (1, 'cus_abc123', 'TOTP_SECRET_X');
            INSERT INTO users (id, stripe_customer_id, mfa_secret)
              VALUES (2, 'cus_def456', NULL);
            INSERT INTO users (id, stripe_customer_id, mfa_secret)
              VALUES (3, NULL, NULL);
            -- Three legacy beta-seat reservations.
            INSERT INTO beta_seat_reservations (user_id, status) VALUES (1, 'active');
            INSERT INTO beta_seat_reservations (user_id, status) VALUES (2, 'expired');
            INSERT INTO beta_seat_reservations (user_id, status) VALUES (3, 'active');
            """
        )
    return db_path


def _config(**kwargs):
    """Minimal Config-like object the inspector accepts."""
    defaults = dict(
        ENABLE_LEGACY_HOSTED_ACCESS=False,
        PALLI_DEPLOYMENT_MODE="local",
        DATABASE_PATH="",
    )
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


# =============================================================================
# Clean-state cases — should produce no findings
# =============================================================================


def test_clean_db_with_default_config_produces_no_findings(tmp_path: Path):
    db = _make_clean_fixture(tmp_path)
    report = check_legacy_residue(config=_config(), database_path=db)
    assert not report.has_residue
    assert "No legacy hosted-product residue detected" in report.summary()


def test_no_database_path_returns_clean_report():
    """If config.DATABASE_PATH is empty, only config-level checks run.
    With the default config (legacy off), that's a clean report.
    """
    report = check_legacy_residue(config=_config())
    assert not report.has_residue
    assert report.inspected_database is None


def test_database_path_pointing_to_nonexistent_file_is_safe(tmp_path: Path):
    """Inspector silently skips DB inspection when the file isn't there
    — fresh installs are not residue.
    """
    report = check_legacy_residue(
        config=_config(),
        database_path=tmp_path / "does_not_exist.sqlite3",
    )
    assert not report.has_residue


# =============================================================================
# Residue cases — each should be detected with correct counts
# =============================================================================


def test_residue_db_detects_stripe_mfa_and_beta(tmp_path: Path):
    db = _make_residue_fixture(tmp_path)
    report = check_legacy_residue(config=_config(), database_path=db)

    assert report.has_residue
    by_code = {f.code: f for f in report.findings}
    # Two users have stripe IDs (rows 1 and 2).
    assert by_code["stripe_customer_rows"].count == 2
    # One user has an MFA secret (row 1).
    assert by_code["mfa_secret_rows"].count == 1
    # Three beta-seat reservations.
    assert by_code["beta_seat_reservations"].count == 3


def test_legacy_flag_on_in_local_mode_reports_warning():
    """ENABLE_LEGACY_HOSTED_ACCESS=true while PALLI_DEPLOYMENT_MODE=local
    is the most common operator misconfig — report it.
    """
    cfg = _config(ENABLE_LEGACY_HOSTED_ACCESS=True, PALLI_DEPLOYMENT_MODE="local")
    report = check_legacy_residue(config=cfg)
    codes = {f.code for f in report.findings}
    assert "config_legacy_flag_on_in_local_mode" in codes


def test_legacy_flag_on_in_cloud_mode_does_NOT_warn():
    """When deployment mode is cloud, the flag being on is expected and
    shouldn't fire the misconfig warning.
    """
    cfg = _config(ENABLE_LEGACY_HOSTED_ACCESS=True, PALLI_DEPLOYMENT_MODE="cloud")
    report = check_legacy_residue(config=cfg)
    codes = {f.code for f in report.findings}
    assert "config_legacy_flag_on_in_local_mode" not in codes


# =============================================================================
# Fix guidance — each finding maps to actionable text
# =============================================================================


def test_fix_guidance_contains_concrete_sql_for_each_finding(tmp_path: Path):
    """Operator running `doctor --fix` needs to know what to do, not
    just that something's wrong. Guidance must include the concrete SQL.
    """
    db = _make_residue_fixture(tmp_path)
    report = check_legacy_residue(config=_config(), database_path=db)
    guidance_text = "\n".join(report.fix_guidance())

    # Each detected finding should produce guidance.
    assert "stripe_customer_id" in guidance_text
    assert "mfa_secret" in guidance_text
    assert "beta_seat_reservations" in guidance_text
    # Concrete SQL hints — operators can copy-paste these (after backup).
    assert "UPDATE users" in guidance_text
    assert "DELETE FROM beta_seat_reservations" in guidance_text


def test_fix_guidance_is_empty_when_clean(tmp_path: Path):
    db = _make_clean_fixture(tmp_path)
    report = check_legacy_residue(config=_config(), database_path=db)
    assert report.fix_guidance() == []


# =============================================================================
# ResidueReport / ResidueFinding properties
# =============================================================================


def test_residue_finding_is_clean_returns_true_only_for_ok_severity():
    assert ResidueFinding(code="x", message="m", severity="ok").is_clean()
    assert not ResidueFinding(code="x", message="m", severity="warning").is_clean()
    assert not ResidueFinding(code="x", message="m", severity="core_blocker").is_clean()


def test_empty_report_summary_says_no_inspection_was_performed():
    """A report with zero findings (e.g., from skipping DB) reports that
    nothing was inspected, NOT that everything is clean."""
    report = ResidueReport()
    assert "No legacy residue inventory was performed" in report.summary()


# =============================================================================
# Defensive coverage — DB with missing columns/tables
# =============================================================================


def test_inspector_tolerates_missing_legacy_columns(tmp_path: Path):
    """A truly fresh DB might not even have the legacy columns yet (if
    schema migration hasn't run). The inspector must not raise.
    """
    db_path = tmp_path / "no_legacy_cols.sqlite3"
    with sqlite3.connect(str(db_path)) as conn:
        conn.executescript(
            """
            -- users table without stripe_customer_id / mfa_secret columns
            CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT);
            -- no beta_seat_reservations table
            INSERT INTO users (id, name) VALUES (1, 'test');
            """
        )
    report = check_legacy_residue(config=_config(), database_path=db_path)
    assert not report.has_residue
