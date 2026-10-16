#!/usr/bin/env python3
"""One-time repair of the owner's PERSONALITY_PROFILE.md placeholder lines
(item 6, 2026-08-19). The template rendered "Preferred display name: Local
Operator" / "Known interests: no explicit interest list yet" / "Preferred
name if explicitly confirmed: Local Operator" at creation and nobody ever
filled them — she read them in every reply. This is HIS document
(user-owned); the repair uses his display name from the frontmatter and
points interests at the nightly picture (MEMORY_SUMMARY) instead of a
stale list. Through the identity store (owner actor, versioned).

  venv/bin/python scripts/repair_profile_placeholders.py --dry-run
  venv/bin/python scripts/repair_profile_placeholders.py --apply
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import core.config  # noqa: E402,F401
from core.identity.frontmatter import dump_frontmatter  # noqa: E402
from core.identity.service import identity_service  # noqa: E402

OWNER_ID = 1


def repair_body(body: str, display_name: str) -> str:
    out = []
    for ln in body.splitlines():
        s = ln.strip()
        if s.startswith("- Their preferred display name for me:") or s.startswith("- Preferred display name:"):
            out.append(f"- What they like to be called: {display_name}")
        elif s.startswith("- Known interests:"):
            out.append("- What they're into: see my memory summary (redrawn nightly) and my memory of them")
        elif s.startswith("- Preferred name if explicitly confirmed:"):
            continue
        else:
            out.append(ln)
    return "\n".join(out).rstrip("\n") + "\n"


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    bundle = identity_service.compile_for_owner(owner_id=OWNER_ID)
    doc = bundle.owner.get("personality_profile")
    if doc is None:
        print("no personality profile"); return 1
    name = str((doc.frontmatter or {}).get("display_name") or "").strip() or "the owner"
    new_body = repair_body(doc.content or "", name)
    if new_body.strip() == (doc.content or "").strip():
        print("nothing to repair"); return 0
    import difflib
    print("\n".join(difflib.unified_diff((doc.content or "").splitlines(), new_body.splitlines(), "live", "repaired", lineterm="", n=1)))
    if not args.apply or args.dry_run:
        print("\nDRY RUN — nothing written"); return 0
    await identity_service.update_raw_document(
        user_id=OWNER_ID, platform="owner", doc_key="personality_profile",
        markdown=dump_frontmatter(doc.frontmatter, new_body),
        actor_type="user", actor_id="profile_repair", reason="placeholder_repair_2026_08_19",
        identity_owner_id=OWNER_ID,
    )
    print("APPLIED")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
