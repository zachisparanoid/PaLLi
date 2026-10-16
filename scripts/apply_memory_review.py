#!/usr/bin/env python3
"""Apply (or undo) the owner-reviewed MEMORY.md reset of 2026-08-18.

Reads docs/2026-08-18-memory-review/MEMORY.proposed.md (edit it first, as
you like) and writes it through the identity store — versioned, actor
"memory_review", so --undo can restore the previous version. Frontmatter is
taken from the LIVE document (the proposed file carries only the body).

  venv/bin/python scripts/apply_memory_review.py --dry-run   (default; prints a diff)
  venv/bin/python scripts/apply_memory_review.py --apply
  venv/bin/python scripts/apply_memory_review.py --undo      (restores the pre-apply body saved beside the proposal)
"""
from __future__ import annotations

import argparse
import asyncio
import difflib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import core.config  # noqa: E402,F401
from core.identity.frontmatter import dump_frontmatter  # noqa: E402
from core.identity.service import identity_service  # noqa: E402

REVIEW_DIR = ROOT / "docs" / "2026-08-18-memory-review"
PROPOSED = REVIEW_DIR / "MEMORY.proposed.md"
BACKUP = REVIEW_DIR / "MEMORY.pre-apply.body.md"
OWNER_ID = 1


def _split_body(text: str) -> str:
    """Body only — the proposed file may or may not carry frontmatter."""
    if text.startswith("---\n"):
        end = text.find("\n---\n", 4)
        if end != -1:
            return text[end + 5:].lstrip("\n")
    return text


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--undo", action="store_true")
    args = ap.parse_args()

    bundle = identity_service.compile_for_owner(owner_id=OWNER_ID)
    doc = bundle.owner.get("memory")
    if doc is None:
        print("no live MEMORY.md for owner", OWNER_ID)
        return 1
    current = doc.content or ""

    if args.undo:
        if not BACKUP.exists():
            print("nothing to undo:", BACKUP, "missing")
            return 1
        new_body = BACKUP.read_text(encoding="utf-8")
        reason = "memory_review_undo"
    else:
        new_body = _split_body(PROPOSED.read_text(encoding="utf-8"))
        reason = "memory_review_2026_08_18"

    diff = "\n".join(difflib.unified_diff(
        current.splitlines(), new_body.splitlines(), "live", "proposed", lineterm="", n=1))
    print(diff or "(no difference)")
    print(f"\nlive body {len(current):,} chars → new body {len(new_body):,} chars")
    if not (args.apply or args.undo) or args.dry_run:
        print("DRY RUN — nothing written")
        return 0
    if not args.undo:
        BACKUP.write_text(current, encoding="utf-8")
    await identity_service.update_raw_document(
        user_id=OWNER_ID, platform="owner", doc_key="memory",
        markdown=dump_frontmatter(doc.frontmatter, new_body),
        # system actor: the memory doc allows raw edits only through the
        # self-maintenance door (the reflect/sweep path), not the owner door.
        actor_type="system", actor_id="memory_review", reason=reason,
        identity_owner_id=OWNER_ID,
    )
    print("APPLIED —", reason)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
