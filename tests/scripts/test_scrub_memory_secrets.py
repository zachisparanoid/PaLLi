"""Batch 1, Task 4 (2026-09-25): the one-time scrub of the owner's memory file.

`rewrite` is pure: the vault's key literals become pointers to the files that
hold them, the owner's phone line points at a private file (the number comes
back in `found` for the caller to write there), every other phone-shaped
string is generalized, and the vault-lock rule is added once. `_amain` is
exercised only against a FAKE identity service and a tmp contact path — never
the real memory file.

Every value here is fake: 555-01xx phone numbers, made-up key literals.
"""
from __future__ import annotations

import asyncio
import json
import os
import stat
from types import SimpleNamespace

import pytest

from scripts import scrub_memory_secrets as scrub
from scripts.scrub_memory_secrets import rewrite

HINT = "~/.palli/owner_contact.json"
_FAKE_PASS = "tea-" + "kettle-" + "window-" + "lamp"
_FAKE_KEY = "Zm9vYmFy" + "YmF6cXV4" + "cXV1eA=="
_SPACED_PASS = "small " + "blue " + "boat " + "rain"


@pytest.fixture(autouse=True)
def _no_real_redaction(monkeypatch):
    """Hermetic: the generic redactor is identity unless a test says otherwise."""
    from core import secrets_redaction as sr
    monkeypatch.setattr(sr, "redact", lambda t: t)


def test_phone_line_moves_out_and_lookup_numbers_are_generalized():
    doc = ("## Durable Context\n"
           "- User's phone number is (555) 010-2233; explicitly invited PaLLi to call anytime (2026-08-01).\n"
           "- Three reverse phone lookups (555) 010-9999 and two earlier numbers) stalled.\n")
    out, found = rewrite(doc, phone_file_hint="~/.palli/owner_contact.json")
    assert "010-2233" not in out and "010-9999" not in out
    assert "is in ~/.palli/owner_contact.json" in out
    assert found["owner_phone"] == "(555) 010-2233"
    assert "- Vault writes:" in out  # the flock rule line is appended once


def test_the_rest_of_the_owner_phone_line_is_kept():
    doc = "## Durable Context\n- User's phone number is 555.010.2233; said to call anytime (2026-08-01).\n"
    out, found = rewrite(doc, phone_file_hint=HINT, vault_literals=[])
    assert ("- User's phone number is in ~/.palli/owner_contact.json (read it when you need it)"
            "; said to call anytime (2026-08-01).") in out
    assert found["owner_phone"] == "555.010.2233"
    assert found["owner_phone_lines"] == 1


def test_a_stray_open_paren_stays_in_the_text():
    doc = ("## Active Threads\n"
           "- Three reverse phone lookups (555-010-9999 and two earlier numbers) stalled.\n")
    out, found = rewrite(doc, phone_file_hint=HINT, vault_literals=[])
    assert "lookups ([a phone number] and two earlier numbers) stalled." in out
    assert found["phones_generalized"] == 1
    assert found["owner_phone"] is None


def test_vault_literals_become_pointers_to_their_own_files():
    doc = ("## Durable Context\n- likes tea\n\n"
           "## Useful References\n"
           f"- ~/vault.kdbx — a vault with 3 entries; user's key half: {_FAKE_PASS}\n"
           f"- the key file once read {_FAKE_KEY} in a repair\n")
    out, found = rewrite(doc, phone_file_hint=HINT, vault_literals=[_FAKE_PASS, _FAKE_KEY])
    assert _FAKE_PASS not in out and _FAKE_KEY not in out
    assert "user's key half: (in ~/.palli/vault/.passphrase — never written here)" in out
    assert "read (in ~/.palli/vault/vault.key — never written here) in a repair" in out
    assert found["vault_literals"] == 2


def test_a_vault_line_a_reflection_already_redacted_gets_the_pointer():
    doc = ("## Useful References\n"
           "- ~/vault.kdbx — a vault with 3 entries; user's key half: [REDACTED]\n")
    out, found = rewrite(doc, phone_file_hint=HINT, vault_literals=[])
    assert "user's key half: (in ~/.palli/vault/.passphrase — never written here)" in out
    assert "[REDACTED]" not in out
    assert found["redacted_vault_lines"] == 1


def test_the_generic_redactor_runs_after_the_vault_pointers(monkeypatch):
    from core import secrets_redaction as sr
    seen = []

    def fake_redact(text):
        seen.append(text)
        return text.replace("secret-" + "denylisted", "[REDACTED]")

    monkeypatch.setattr(sr, "redact", fake_redact)
    doc = f"## Durable Context\n- key half: {_FAKE_PASS}\n- a secret-" + "denylisted word\n"
    out, found = rewrite(doc, phone_file_hint=HINT, vault_literals=[_FAKE_PASS])
    assert all(_FAKE_PASS not in s for s in seen), "the vault literal is pointed, not redacted"
    assert "key half: (in ~/.palli/vault/.passphrase — never written here)" in out
    assert "- a [REDACTED] word" in out
    assert found["other_redactions"] == 1


def test_the_flock_line_lands_inside_durable_context_once():
    doc = "## Durable Context\n- likes tea\n\n## Active Threads\n- none\n"
    out, found = rewrite(doc, phone_file_hint=HINT, vault_literals=[])
    lines = out.splitlines()
    flock = [i for i, ln in enumerate(lines) if ln.startswith("- Vault writes:")]
    assert len(flock) == 1
    assert lines[flock[0] - 1] == "- likes tea"
    assert lines.index("## Active Threads") > flock[0]
    assert lines[flock[0]] == (
        "- Vault writes: run any script that saves the vault under `flock ~/.palli/vault/.lock` "
        "(two saves at once corrupted it on 2026-09-24); reads need no lock."
    )
    assert found["flock_line_added"] == 1


def test_rewrite_is_idempotent():
    doc = ("## Durable Context\n"
           "- User's phone number is 555.010.2233; said to call anytime (2026-08-01).\n\n"
           "## Active Threads\n"
           "- Three reverse phone lookups (555-010-9999 and two earlier numbers) stalled.\n\n"
           "## Useful References\n"
           f"- ~/vault.kdbx — key half: {_FAKE_PASS}\n")
    once, found1 = rewrite(doc, phone_file_hint=HINT, vault_literals=[_FAKE_PASS])
    assert scrub.total_changes(found1) > 0
    twice, found2 = rewrite(once, phone_file_hint=HINT, vault_literals=[_FAKE_PASS])
    assert twice == once
    assert scrub.total_changes(found2) == 0
    assert found2["owner_phone"] is None


def test_the_default_vault_literals_come_from_the_redactor(monkeypatch):
    from core import secrets_redaction as sr
    monkeypatch.setattr(sr, "_vault_secret_literals", lambda: [_FAKE_PASS, _FAKE_KEY])
    out, found = rewrite(f"## Durable Context\n- key half: {_FAKE_PASS}\n", phone_file_hint=HINT)
    assert _FAKE_PASS not in out and found["vault_literals"] == 1


# --- _amain, against a fake identity service only -------------------------

class _FakeService:
    def __init__(self, content: str):
        self.doc = SimpleNamespace(content=content, frontmatter={"doc_type": "memory", "version": 3})
        self.calls: list[dict] = []

    def compile_for_owner(self, *, owner_id):
        return SimpleNamespace(owner={"memory": self.doc})

    async def update_raw_document(self, **kw):
        self.calls.append(kw)


_MEMORY = ("## Durable Context\n"
           "- User's phone number is 555.010.2233; said to call anytime (2026-08-01).\n\n"
           "## Active Threads\n"
           "- Three reverse phone lookups (555-010-9999 and two earlier numbers) stalled.\n\n"
           "## Useful References\n"
           f"- ~/vault.kdbx — key half: {_FAKE_PASS}\n"
           f"- a spaced pass once read {_SPACED_PASS}\n")


def _run(argv, service, contact_path):
    return asyncio.run(scrub._amain(argv, service=service, contact_path=contact_path))


def test_dry_run_writes_nothing_and_prints_no_value(monkeypatch, tmp_path, capsys):
    from core import secrets_redaction as sr
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(sr, "_vault_secret_literals", lambda: [_FAKE_PASS, _SPACED_PASS])
    service = _FakeService(_MEMORY)
    contact = tmp_path / ".palli" / "owner_contact.json"
    rc = _run(["--owner-id", "1", "--dry-run"], service, contact)
    printed = capsys.readouterr()
    text = printed.out + printed.err
    assert rc == 0
    assert service.calls == []
    assert not contact.exists()
    for value in ("2233", "9999", "tea-kettle", "window", "lamp", "small", "boat"):
        assert value not in text, value
    assert "owner_phone_found: yes" in text
    assert "vault_literals: 2" in text


def test_the_real_path_writes_the_contact_file_600_then_the_memory(monkeypatch, tmp_path, capsys):
    from core import secrets_redaction as sr
    monkeypatch.setenv("HOME", str(tmp_path))
    # Both key literals readable: the real run refuses with fewer (final fix
    # wave) unless a reflection already redacted the key-half line.
    monkeypatch.setattr(sr, "_vault_secret_literals", lambda: [_FAKE_PASS, _FAKE_KEY])
    service = _FakeService(_MEMORY)
    contact = tmp_path / ".palli" / "owner_contact.json"
    old_umask = os.umask(0o022)
    try:
        rc = _run(["--owner-id", "7"], service, contact)
    finally:
        os.umask(old_umask)
    text = capsys.readouterr().out
    assert rc == 0
    assert json.loads(contact.read_text(encoding="utf-8")) == {"phone": "555.010.2233"}
    assert stat.S_IMODE(contact.stat().st_mode) == 0o600
    assert [p.name for p in contact.parent.iterdir()] == ["owner_contact.json"]  # no tmp left
    assert len(service.calls) == 1
    call = service.calls[0]
    assert call["user_id"] == 7 and call["identity_owner_id"] == 7
    assert call["platform"] == "owner" and call["doc_key"] == "memory"
    assert call["actor_type"] == "system" and call["actor_id"] == "batch1-scrub"
    assert call["reason"] == "Batch 1: secrets out of the memory file"
    assert call["allow_admin"] is True
    md = call["markdown"]
    assert md.startswith("---\ndoc_type: memory\n")
    assert "2233" not in md and "9999" not in md and _FAKE_PASS not in md
    assert "2233" not in text and _FAKE_PASS not in text


def test_nothing_to_change_is_a_no_op(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))
    service = _FakeService("## Durable Context\n- likes tea\n")
    once, _ = rewrite(service.doc.content, phone_file_hint=HINT, vault_literals=[])
    service.doc.content = once
    contact = tmp_path / ".palli" / "owner_contact.json"
    rc = _run(["--owner-id", "1"], service, contact)
    assert rc == 0 and service.calls == [] and not contact.exists()
    assert "nothing to change" in capsys.readouterr().out


# --- Final fix wave (2026-09-26), item 5: the real run refuses its dangerous --
# cases. A dry run keeps printing the counts; a real run writes NOTHING (no
# contact file, no memory update) and exits non-zero when (a) phones would be
# generalized but his own number was not found — it would be lost, not
# moved — or (b) fewer than two vault literals could be read and no
# already-redacted key-half line was handled — the passphrase may still be in
# the file, unfound.

_NO_OWNER_LINE = ("## Durable Context\n"
                  "- Mom's number: 555-010-7788.\n\n"
                  "## Useful References\n"
                  f"- ~/vault.kdbx — key half: {_FAKE_PASS}\n")


def test_real_run_refuses_when_phones_would_be_generalized_without_his_number(monkeypatch, tmp_path, capsys):
    from core import secrets_redaction as sr
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(sr, "_vault_secret_literals", lambda: [_FAKE_PASS, _FAKE_KEY])
    service = _FakeService(_NO_OWNER_LINE)
    contact = tmp_path / ".palli" / "owner_contact.json"

    rc = _run(["--owner-id", "1"], service, contact)

    text = capsys.readouterr().out
    assert rc != 0
    assert service.calls == [] and not contact.exists() and not contact.parent.exists()
    assert "phones_generalized: 1" in text and "owner_phone_found: no" in text
    assert "REFUSED" in text and "nothing written" in text.lower()
    assert "7788" not in text and _FAKE_PASS not in text


def test_the_dry_run_still_prints_the_counts_for_that_case(monkeypatch, tmp_path, capsys):
    from core import secrets_redaction as sr
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(sr, "_vault_secret_literals", lambda: [_FAKE_PASS, _FAKE_KEY])
    service = _FakeService(_NO_OWNER_LINE)
    contact = tmp_path / ".palli" / "owner_contact.json"

    rc = _run(["--owner-id", "1", "--dry-run"], service, contact)

    text = capsys.readouterr().out
    assert rc == 0 and service.calls == [] and not contact.exists()
    assert "phones_generalized: 1" in text and "dry run — nothing written" in text
    assert "NOTE: the real run would refuse" in text
    assert "7788" not in text and _FAKE_PASS not in text


def test_real_run_refuses_when_the_vault_literals_could_not_be_read(monkeypatch, tmp_path, capsys):
    from core import secrets_redaction as sr
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(sr, "_vault_secret_literals", lambda: [_FAKE_PASS])  # one of two
    service = _FakeService(_MEMORY)
    contact = tmp_path / ".palli" / "owner_contact.json"

    rc = _run(["--owner-id", "1"], service, contact)

    text = capsys.readouterr().out
    assert rc != 0
    assert service.calls == [] and not contact.exists() and not contact.parent.exists()
    assert "vault_literals_known: 1" in text
    assert "REFUSED" in text and "nothing written" in text.lower()
    assert "2233" not in text and _FAKE_PASS not in text


def test_an_already_redacted_key_half_line_lets_the_real_run_proceed(monkeypatch, tmp_path, capsys):
    from core import secrets_redaction as sr
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(sr, "_vault_secret_literals", lambda: [])  # none readable
    memory = ("## Durable Context\n"
              "- User's phone number is 555.010.2233; said to call anytime (2026-08-01).\n\n"
              "## Useful References\n"
              f"- ~/vault.kdbx — key half: {sr.REDACTED}\n")
    service = _FakeService(memory)
    contact = tmp_path / ".palli" / "owner_contact.json"

    rc = _run(["--owner-id", "1"], service, contact)

    text = capsys.readouterr().out
    assert rc == 0, text
    assert "redacted_vault_lines: 1" in text
    assert len(service.calls) == 1 and contact.exists()
