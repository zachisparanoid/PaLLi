"""Phase 8e — legacy hosted-product residue inventory.

PaLLi's local product line doesn't use the legacy hosted-tier features
(Stripe billing, MFA-gated login, beta-seat reservations). Those code
paths exist in the codebase as dormant artifacts. This module is the
single home for "where is the legacy stuff?" knowledge — used by
`palli_ctl.py doctor` to detect residue and (with `--fix`) to surface
guidance on cleaning it up.

Why a dedicated module:
  - Knowledge consolidation. Adding a new legacy artifact means
    extending one inventory, not grepping eleven files for
    `legacy_hosted_access_enabled()` checks.
  - Doctor as cleanup home. The VISION says: "Consolidate legacy
    hosted-product code paths into `palli_ctl.py doctor --fix`."
    Centralizing detection is step one; centralizing cleanup is
    a follow-up that lands here.
  - Hot-path discipline. Production code should not branch on
    `ENABLE_LEGACY_HOSTED_ACCESS` at every reply turn. That check
    belongs in startup config validation and one-shot
    diagnostic/cleanup commands — never on the per-message path.

Intentionally NOT in this module:
  - Destructive cleanup (DELETE FROM users WHERE ...). That's a
    follow-up — needs careful idempotency and a backup-first
    contract before we ship it.
  - Side-effecting migration logic. `check_legacy_residue` is a pure
    inspector; it never mutates the DB.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ResidueFinding:
    """A single piece of legacy residue detected on disk or in config."""

    code: str          # short stable id (e.g. "stripe_customer_rows")
    message: str       # human-readable description
    count: int = 0     # how many rows / how big — 0 means presence-only
    severity: str = "warning"  # "ok", "warning", or "core_blocker"

    def is_clean(self) -> bool:
        return self.severity == "ok"


@dataclass
class ResidueReport:
    findings: List[ResidueFinding] = field(default_factory=list)
    inspected_database: Optional[str] = None

    @property
    def has_residue(self) -> bool:
        """True if at least one non-ok finding exists."""
        return any(not f.is_clean() for f in self.findings)

    def summary(self) -> str:
        non_ok = [f for f in self.findings if not f.is_clean()]
        if non_ok:
            return (
                f"Legacy hosted-product residue detected ({len(non_ok)} item(s)). "
                f"Run `palli_ctl.py doctor --fix` for guidance."
            )
        # No residue. Distinguish "we inspected and found nothing" from
        # "we couldn't inspect" — the former is good news, the latter
        # is just an absence of signal.
        if self.inspected_database is not None:
            return "No legacy hosted-product residue detected."
        return "No legacy residue inventory was performed."

    def fix_guidance(self) -> List[str]:
        """Lines of operator guidance for the residue. Each finding maps
        to one or more concrete suggestions; empty list when clean.
        """
        guidance: List[str] = []
        for finding in self.findings:
            if finding.is_clean():
                continue
            guidance.extend(_GUIDANCE_FOR_CODE.get(finding.code, [
                f"- {finding.code}: {finding.message} — manual review needed."
            ]))
        return guidance


# ---------------------------------------------------------------------------
# Guidance text. Kept here (not inline) so updates don't churn the inspector.
# Each guidance block is plain text printed by `palli_ctl.py doctor --fix`.
# ---------------------------------------------------------------------------
_GUIDANCE_FOR_CODE: dict[str, List[str]] = {
    "config_legacy_flag_on_in_local_mode": [
        "- ENABLE_LEGACY_HOSTED_ACCESS=true while PALLI_DEPLOYMENT_MODE=local.",
        "  This combination keeps Stripe/MFA/beta-seat code paths warm",
        "  even though the local deployment never uses them.",
        "  Fix: set ENABLE_LEGACY_HOSTED_ACCESS=false in .env.local (the",
        "  default) unless you specifically need the hosted flows enabled.",
    ],
    "stripe_customer_rows": [
        "- users.stripe_customer_id has non-null values for one or more rows.",
        "  These were populated when the hosted billing flow was active.",
        "  Manual review: confirm no live Stripe subscription depends on these IDs.",
        "  Cleanup (when you're sure): UPDATE users SET stripe_customer_id = NULL;",
    ],
    "mfa_secret_rows": [
        "- users.mfa_secret has non-null values for one or more rows.",
        "  TOTP secrets from the legacy MFA-gated login flow.",
        "  Manual review: any user still using TOTP-on-login? Local mode normally doesn't.",
        "  Cleanup (when you're sure): UPDATE users SET mfa_secret = NULL, mfa_enabled = 0;",
    ],
    "beta_seat_reservations": [
        "- beta_seat_reservations table has rows.",
        "  These tracked beta-program seats during the hosted launch.",
        "  Local mode doesn't honor them — the BetaAccessStore is dormant.",
        "  Cleanup (when you're sure): DELETE FROM beta_seat_reservations;",
    ],
}


def _safe_count(conn: sqlite3.Connection, sql: str) -> int:
    """Count helper that returns 0 when the table/column is missing."""
    try:
        cursor = conn.execute(sql)
        row = cursor.fetchone()
        return int(row[0]) if row else 0
    except sqlite3.OperationalError:
        # Table or column doesn't exist — treat as zero residue.
        return 0


def check_legacy_residue(
    *,
    config: object,
    database_path: Optional[Path] = None,
) -> ResidueReport:
    """Detect legacy hosted-product residue without touching it.

    Args:
        config: A Config-like object — must expose `ENABLE_LEGACY_HOSTED_ACCESS`,
            `PALLI_DEPLOYMENT_MODE`, and `DATABASE_PATH`. Pass `core.config.config`
            in production; pass a SimpleNamespace in tests.
        database_path: Override DB path (for tests). Defaults to
            `config.DATABASE_PATH`.

    Returns:
        A `ResidueReport` listing each piece of residue found. The report
        is the source of truth for the doctor "legacy residue" check
        and the doctor `--fix` guidance.
    """
    findings: List[ResidueFinding] = []
    db_path: Optional[Path] = None

    # ------------------------------------------------------------------
    # Config-level checks. Cheap; always run.
    # ------------------------------------------------------------------
    flag_on = bool(getattr(config, "ENABLE_LEGACY_HOSTED_ACCESS", False))
    deployment_mode = str(
        getattr(config, "PALLI_DEPLOYMENT_MODE", "") or ""
    ).strip().lower()
    is_local = deployment_mode in {"local", "local_single_user"}

    if flag_on and is_local:
        findings.append(
            ResidueFinding(
                code="config_legacy_flag_on_in_local_mode",
                message=(
                    "ENABLE_LEGACY_HOSTED_ACCESS=true while running in local mode "
                    "— legacy code paths are warm but unused"
                ),
                severity="warning",
            )
        )

    # ------------------------------------------------------------------
    # Database checks. Skipped silently if the DB doesn't exist (fresh
    # install or test fixture without a DB).
    # ------------------------------------------------------------------
    if database_path is not None:
        db_path = database_path
    else:
        raw_path = getattr(config, "DATABASE_PATH", "")
        if raw_path:
            db_path = Path(raw_path)

    if db_path and db_path.exists():
        try:
            with sqlite3.connect(str(db_path)) as conn:
                stripe_count = _safe_count(
                    conn,
                    "SELECT COUNT(*) FROM users WHERE stripe_customer_id IS NOT NULL "
                    "AND TRIM(stripe_customer_id) != ''",
                )
                if stripe_count:
                    findings.append(
                        ResidueFinding(
                            code="stripe_customer_rows",
                            message=f"users.stripe_customer_id non-null on {stripe_count} row(s)",
                            count=stripe_count,
                            severity="warning",
                        )
                    )

                mfa_count = _safe_count(
                    conn,
                    "SELECT COUNT(*) FROM users WHERE mfa_secret IS NOT NULL "
                    "AND TRIM(mfa_secret) != ''",
                )
                if mfa_count:
                    findings.append(
                        ResidueFinding(
                            code="mfa_secret_rows",
                            message=f"users.mfa_secret non-null on {mfa_count} row(s)",
                            count=mfa_count,
                            severity="warning",
                        )
                    )

                beta_count = _safe_count(conn, "SELECT COUNT(*) FROM beta_seat_reservations")
                if beta_count:
                    findings.append(
                        ResidueFinding(
                            code="beta_seat_reservations",
                            message=f"beta_seat_reservations table has {beta_count} row(s)",
                            count=beta_count,
                            severity="warning",
                        )
                    )
        except sqlite3.Error as exc:
            logger.warning(
                "Could not inspect DB for legacy residue: %s", exc
            )

    return ResidueReport(
        findings=findings,
        inspected_database=str(db_path) if db_path else None,
    )
