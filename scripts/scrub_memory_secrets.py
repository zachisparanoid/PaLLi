#!/usr/bin/env python3
"""Batch 1 (2026-09-25): take the secrets out of the owner's memory file, once.

Her memory of him (identity MEMORY.md) rides in every prompt. Two values sat
in it that must never travel: the vault passphrase ("user's key half: …") and
his phone number. This one-time scrub

  (a) replaces each vault key literal (the redactor's own literals, found by
      VALUE, never by guessing at the line's shape) with a pointer to the file
      that holds it, and turns a "key half: [REDACTED]" a reflection already
      wrote into the same pointer;
  (b) moves his phone number out: the line now says where it lives
      (~/.palli/owner_contact.json, mode 600 — her tools can read it) and keeps
      the rest of the sentence;
  (c) generalizes every other phone-shaped string to "[a phone number]";
  (d) runs the known-literal redactor over what is left;
  (e) adds, once, the vault-lock rule under "## Durable Context".

Run it from the repository root, after the restart that ships Batch 1:

  venv/bin/python scripts/scrub_memory_secrets.py --owner-id 1 --dry-run
  venv/bin/python scripts/scrub_memory_secrets.py --owner-id 1

--dry-run prints counts and the changed lines MASKED (every known secret
redacted, every run of 8+ key-ish characters shown as <LONG>, every digit as
#) and writes nothing. The script never prints a value, in any mode. Running
it twice changes nothing the second time.

The real run REFUSES — exits 3 and writes nothing, no contact file and no
memory update — in the two cases where writing would lose something:
phone-shaped strings would be generalized but his own number was not found
(it would be lost, not moved), or fewer than both vault key literals could be
read and no already-redacted "key half" line was handled (a passphrase still
in the file cannot be found by its value). The dry run shows the counts.
"""
from __future__ import annotations

import argparse
import asyncio
import difflib
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Optional, Sequence

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from core import secrets_redaction as sr  # noqa: E402
from core.agent.payload_masking import PATTERNS, looks_secret  # noqa: E402

CONTACT_HINT = "~/.palli/owner_contact.json"
PASSPHRASE_POINTER = "(in ~/.palli/vault/.passphrase — never written here)"
KEYFILE_POINTER = "(in ~/.palli/vault/vault.key — never written here)"
# Only when the redactor could not read BOTH key files: its list then no longer
# says which literal came from which file, so the pointer names the folder.
VAULT_DIR_POINTER = "(in ~/.palli/vault/ — never written here)"
_POINTER_BY_FILE = {".passphrase": PASSPHRASE_POINTER, "vault.key": KEYFILE_POINTER}
PHONE_PLACEHOLDER = "[a phone number]"
DURABLE_CONTEXT = "## Durable Context"
FLOCK_PREFIX = "- Vault writes:"
FLOCK_LINE = (
    "- Vault writes: run any script that saves the vault under `flock ~/.palli/vault/.lock` "
    "(two saves at once corrupted it on 2026-09-24); reads need no lock."
)

# The one phone grammar (core/agent/payload_masking.py) — never a second one.
_PHONE = next(p for name, p, _group in PATTERNS if name == "phone")
_OWNER_PHONE = re.compile(r"(user['’]?s phone number is[ \t]+)(" + _PHONE.pattern + r")", re.IGNORECASE)
_KEY_HALF_REDACTED = re.compile(r"(key half:[ \t]*)" + re.escape(sr.REDACTED), re.IGNORECASE)

# Everything rewrite() changes; `owner_phone` and the two informational
# counts are not changes.
CHANGE_KEYS = (
    "vault_literals",
    "redacted_vault_lines",
    "owner_phone_lines",
    "phones_generalized",
    "other_redactions",
    "flock_line_added",
)
INFO_KEYS = ("vault_literals_known", "secret_shaped_lines_left")


def total_changes(found: dict) -> int:
    return sum(int(found.get(k) or 0) for k in CHANGE_KEYS)


def _phone_pointer(hint: str) -> str:
    return f"in {hint} (read it when you need it)"


def _split_stray_paren(match: str) -> tuple[str, str]:
    """The phone grammar allows an optional "(" before the area code, so in
    "lookups (555-010-9999 and two more)" it takes the sentence's own
    parenthesis. Give that one back to the sentence."""
    if match.startswith("(") and ")" not in match:
        return "(", match[1:]
    return "", match


def _digits(value: str) -> str:
    return re.sub(r"\D", "", value)


def _vault_pairs(vault_literals: Optional[Sequence[str]]) -> list[tuple[str, str]]:
    """(literal, pointer) pairs. An explicit list is positional against
    ``sr.VAULT_SECRET_FILES`` (.passphrase, then vault.key). The default is the
    redactor's own list, which skips a file it cannot read — so it is
    positional only when it came back whole."""
    if vault_literals is None:
        literals = list(sr._vault_secret_literals())
        positional = len(literals) == len(sr.VAULT_SECRET_FILES)
    else:
        literals = list(vault_literals)
        positional = True
    pairs: list[tuple[str, str]] = []
    for index, literal in enumerate(literals):
        literal = str(literal or "").strip()
        if len(literal) < sr._SECRET_MIN_LEN:  # the redactor's own floor: never shred ordinary text
            continue
        if positional and index < len(sr.VAULT_SECRET_FILES):
            pointer = _POINTER_BY_FILE.get(sr.VAULT_SECRET_FILES[index], VAULT_DIR_POINTER)
        else:
            pointer = VAULT_DIR_POINTER
        pairs.append((literal, pointer))
    return sorted(pairs, key=lambda pair: len(pair[0]), reverse=True)


def _add_to_durable_context(text: str, bullet: str) -> str:
    """Append ``bullet`` as the last line of the Durable Context section,
    before the blank lines that close it; add the section if it is missing."""
    crlf = "\r\n" in text
    lines = text.split("\n")
    start = next((i for i, ln in enumerate(lines) if ln.rstrip("\r").strip() == DURABLE_CONTEXT), None)
    if start is None:
        nl = "\r\n" if crlf else "\n"
        body = text.rstrip("\r\n")
        return (body + nl + nl if body else "") + DURABLE_CONTEXT + nl + bullet + nl
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
    at = end
    while at - 1 > start and not lines[at - 1].strip():
        at -= 1
    lines.insert(at, bullet + ("\r" if crlf else ""))
    return "\n".join(lines)


def rewrite(
    markdown: str,
    *,
    phone_file_hint: str,
    vault_literals: Optional[Sequence[str]] = None,
) -> tuple[str, dict[str, Any]]:
    """The scrubbed memory text and what was found. Pure: no file is written.

    ``found`` holds counts only, plus ``owner_phone`` — the one value, for the
    caller to move into the private contact file. Never print it."""
    found: dict[str, Any] = {key: 0 for key in CHANGE_KEYS + INFO_KEYS}
    found["owner_phone"] = None
    text = markdown or ""

    # (a) The vault's key literals, by value, BEFORE the generic redactor —
    # which would otherwise turn them into an anonymous [REDACTED].
    pairs = _vault_pairs(vault_literals)
    found["vault_literals_known"] = len(pairs)
    for literal, pointer in pairs:
        text, n = re.subn(re.escape(literal), lambda _m, p=pointer: p, text, flags=re.IGNORECASE)
        found["vault_literals"] += n
    # A reflection since the restart may have redacted the passphrase already
    # (the belt in core/turn/reflect.py): give that line its pointer too.
    text, n = _KEY_HALF_REDACTED.subn(lambda m: m.group(1) + PASSPHRASE_POINTER, text)
    found["redacted_vault_lines"] = n

    # (b) His number moves out; the rest of the sentence stays. Before the
    # generic redactor on purpose: if his denylist carries the number, the
    # redactor would blank it first and the number would be lost, not moved.
    def _owner(m: re.Match[str]) -> str:
        paren, phone = _split_stray_paren(m.group(2))
        if found["owner_phone"] is None:
            found["owner_phone"] = phone
        elif _digits(phone) != _digits(found["owner_phone"]):
            return m.group(0)  # a second, different number: (c) generalizes it
        found["owner_phone_lines"] += 1
        return m.group(1) + paren + _phone_pointer(phone_file_hint)

    text = _OWNER_PHONE.sub(_owner, text)

    # (c) Every other phone-shaped string.
    def _generic(m: re.Match[str]) -> str:
        paren, _phone = _split_stray_paren(m.group(0))
        found["phones_generalized"] += 1
        return paren + PHONE_PLACEHOLDER

    text = _PHONE.sub(_generic, text)

    # (d) The known-literal redactor over the rest (his denylist, her config
    # credentials).
    before = text.count(sr.REDACTED)
    text = sr.redact(text)
    found["other_redactions"] = max(0, text.count(sr.REDACTED) - before)

    # (e) The vault-lock rule, once.
    if not any(ln.lstrip().startswith(FLOCK_PREFIX) for ln in text.split("\n")):
        text = _add_to_durable_context(text, FLOCK_LINE)
        found["flock_line_added"] = 1

    found["secret_shaped_lines_left"] = sum(1 for ln in text.split("\n") if looks_secret(ln))
    return text, found


# --- printing: counts and masked lines, never a value ------------------------

_LONG_RUN = re.compile(r"[A-Za-z0-9_+/=-]{8,}")
_DIGIT = re.compile(r"\d")


def _mask_line(line: str, literals: Sequence[str], safe: Sequence[str]) -> str:
    """Known secrets out first (a spaced passphrase has no 8-char run), then
    every 8+ run of key-ish characters and every digit — except inside this
    script's own constant text, which is safe and keeps the diff readable."""
    for literal in sorted(literals, key=len, reverse=True):
        line = re.sub(re.escape(literal), lambda _m: sr.REDACTED, line, flags=re.IGNORECASE)
    line = sr.redact(line)
    splitter = re.compile("(" + "|".join(re.escape(s) for s in sorted(safe, key=len, reverse=True)) + ")")
    out = []
    for part in splitter.split(line):
        if part in safe:
            out.append(part)
        else:
            out.append(_DIGIT.sub("#", _LONG_RUN.sub("<LONG>", part)))
    return "".join(out)


def masked_diff(old: str, new: str, *, literals: Sequence[str] = ()) -> list[str]:
    safe = [s for s in (
        FLOCK_LINE, PASSPHRASE_POINTER, KEYFILE_POINTER, VAULT_DIR_POINTER,
        _phone_pointer(CONTACT_HINT), PHONE_PLACEHOLDER, sr.REDACTED,
    ) if s]
    old_lines, new_lines = old.splitlines(), new.splitlines()
    matcher = difflib.SequenceMatcher(a=old_lines, b=new_lines, autojunk=False)
    out: list[str] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        out.extend("- " + _mask_line(ln, literals, safe) for ln in old_lines[i1:i2])
        out.extend("+ " + _mask_line(ln, literals, safe) for ln in new_lines[j1:j2])
    return out


def refusal_reasons(found: dict, *, vault_files: int = 2) -> list[str]:
    """Why a real run must not write, in plain words; empty when it may.
    Counts only — never a value."""
    reasons: list[str] = []
    if int(found.get("phones_generalized") or 0) > 0 and not found.get("owner_phone"):
        reasons.append(
            f"{found['phones_generalized']} phone number(s) would become \"{PHONE_PLACEHOLDER}\", "
            "but no line says \"User's phone number is …\" — his own number would be lost, "
            "not moved to the contact file."
        )
    known = int(found.get("vault_literals_known") or 0)
    if known < vault_files and not int(found.get("redacted_vault_lines") or 0):
        reasons.append(
            f"only {known} of {vault_files} vault key literals could be read, and no "
            "already-redacted \"key half\" line was found — a passphrase still in the "
            "file cannot be found by its value."
        )
    return reasons


def _print_counts(found: dict) -> None:
    for key in CHANGE_KEYS + INFO_KEYS:
        print(f"{key}: {int(found.get(key) or 0)}")
    print(f"owner_phone_found: {'yes' if found.get('owner_phone') else 'no'}")


# --- the private contact file -----------------------------------------------

def write_private_json(path: Path, payload: dict) -> None:
    """Mode 600 from its first byte (never world-readable, even briefly), and
    atomic: a temp file beside it, fsynced, then renamed over."""
    path = Path(path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        tmp.unlink()
    except FileNotFoundError:
        pass
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        os.fchmod(fd, 0o600)  # the umask can only narrow 0o600; this pins it exactly
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise
    try:
        dir_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    except OSError:
        pass


# --- main ---------------------------------------------------------------------

def _load_memory(service: Any, owner_id: int) -> Any:
    bundle = service.compile_for_owner(owner_id=owner_id)
    return bundle.owner.get("memory")


async def _amain(
    argv: Optional[Sequence[str]] = None,
    *,
    service: Any = None,
    contact_path: Optional[Path] = None,
) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--owner-id", dest="owner_id", type=int, required=True)
    ap.add_argument("--dry-run", dest="dry_run", action="store_true",
                    help="print counts and masked changed lines; write nothing")
    args = ap.parse_args(argv)

    if service is None:
        import core.config  # noqa: F401 — the same bootstrap as scripts/apply_memory_review.py
        from core.identity.service import identity_service as service
    if contact_path is None:
        contact_path = Path.home() / ".palli" / "owner_contact.json"

    doc = _load_memory(service, args.owner_id)
    if doc is None:
        print(f"owner {args.owner_id}: no memory document — nothing to do")
        return 1
    current = doc.content or ""
    new, found = rewrite(current, phone_file_hint=CONTACT_HINT)

    _print_counts(found)
    known, files = found["vault_literals_known"], len(sr.VAULT_SECRET_FILES)
    if known < files:
        print(f"WARNING: only {known} of {files} vault key literals could be read — "
              "a passphrase not yet redacted cannot be found by its value")
    if found["secret_shaped_lines_left"]:
        print("NOTE: secret-shaped lines remain (not phone numbers) — look at them by hand")

    if new == current or total_changes(found) == 0:
        print("nothing to change")
        return 0

    # Final fix wave (2026-09-26): the two cases where writing loses something.
    reasons = refusal_reasons(found, vault_files=len(sr.VAULT_SECRET_FILES))

    if args.dry_run:
        print("changed lines (masked):")
        for line in masked_diff(current, new, literals=[lit for lit, _p in _vault_pairs(None)]):
            print(line)
        for reason in reasons:
            print(f"NOTE: the real run would refuse: {reason}")
        print("dry run — nothing written")
        return 0

    if reasons:
        for reason in reasons:
            print(f"REFUSED: {reason}")
        print("Nothing written. Look at the dry run (--dry-run), fix the cause, then run it again.")
        return 3

    # A reflection may have written between our read and now: never overwrite it.
    again = _load_memory(service, args.owner_id)
    if again is None or (again.content or "") != current:
        print("the memory file changed while scrubbing — nothing written; run it again")
        return 2

    if found["owner_phone"]:
        write_private_json(contact_path, {"phone": found["owner_phone"]})
        print("contact file written (mode 600)")

    from core.identity.frontmatter import dump_frontmatter
    await service.update_raw_document(
        user_id=args.owner_id,
        platform="owner",
        doc_key="memory",
        markdown=dump_frontmatter(doc.frontmatter, new),
        actor_type="system",
        actor_id="batch1-scrub",
        reason="Batch 1: secrets out of the memory file",
        allow_admin=True,
        identity_owner_id=args.owner_id,
    )
    print("memory file updated")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_amain()))
