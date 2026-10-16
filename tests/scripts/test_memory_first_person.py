"""Batch 5, Task 10 (memory that picks, 2026-09-28): the one-time, reviewed
migration ``scripts/memory_first_person.py``.

Exercised ONLY against a fake identity service (two documents in a tmp
root) and a fake LLM — never the owner's file. ``--dry-run`` writes the
review document and, beside it, the plan (memory-first-person-plan.json:
every outcome and each document's sha256) and nothing else; ``--apply``
loads that plan with NO model call (ruling R60), errors out plainly without
one, aborts if a document no longer matches the plan or moves before the
write, writes a plain ``.bak`` beside each changed file, writes each changed
document through the store with the migration's reason, and only THEN
writes its own record (memory-first-person-applied.md) — the dry-run's two
files are never overwritten. ``--apply --replan`` is today's fresh model
call, writing a fresh plan first. A batch whose reply breaks
the count contract is retried line by line; an EMPTY reply (the provider's
"I failed") is not retried, and two empty replies in a row stop the run. A
line whose rewrite fails the guard, or comes back unchanged, is kept as
written and listed with the reason (token kinds and counts, never a value).

Every value here is made up.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from core.identity.frontmatter import dump_frontmatter
from scripts import memory_first_person as script

TODAY = "2026-09-28"

PHONE_OLD = "- User's phone number is in ~/.palli/owner_contact.json; explicitly invited PaLLi to call anytime (2026-08-01)."
PHONE_NEW = "- Their number is in ~/.palli/owner_contact.json; they told me to call anytime (2026-08-01)."
KM_OLD = "- User runs 5 km on Mondays."
KM_BAD = "- They run 6 km on Mondays."          # the guard refuses this one
NAS_OLD = "- [2026-09-01] User is fixing the NAS backup."
NAS_NEW = "- [2026-09-01] They're fixing the NAS backup."
PG_OLD = "- User decided on Postgres."
PG_NEW = "- They decided on Postgres."
ARCH_OLD = "- [2026-08-03] User dropped the meditation habit; PaLLi noticed. [archived 2026-09-28]"
ARCH_NEW = "- [2026-08-03] They dropped the meditation habit; I noticed. [archived 2026-09-28]"

REWRITES = {PHONE_OLD: PHONE_NEW, KM_OLD: KM_BAD, NAS_OLD: NAS_NEW, PG_OLD: PG_NEW, ARCH_OLD: ARCH_NEW}

MEMORY = (
    "## Durable Context\n"
    f"{PHONE_OLD}\n"
    "- They like tea.\n"
    f"{KM_OLD}\n"
    "\n"
    "## Active Threads\n"
    f"{NAS_OLD}\n"
    "\n"
    "## Important Decisions\n"
    f"{PG_OLD}\n"
    "\n"
    "## Useful References\n"
    "- No durable references recorded yet.\n"
)
ARCHIVE = "## Archived\n" f"{ARCH_OLD}\n"
CANDIDATES = 5  # 4 in MEMORY.md + 1 in the archive


class _FakeService:
    def __init__(self, root: Path, memory: str = MEMORY, archive: str | None = ARCHIVE):
        self.root = root
        root.mkdir(parents=True, exist_ok=True)
        self.docs = {
            "memory": self._doc("memory", "MEMORY.md", memory),
            "memory_archive": self._doc("memory_archive", "MEMORY_ARCHIVE.md", archive) if archive is not None else None,
        }
        self.calls: list[dict] = []
        self.reads = 0
        self.on_read = None

    def _doc(self, key: str, name: str, content: str):
        frontmatter = {"doc_type": key, "version": 3}
        path = self.root / name
        path.write_text(dump_frontmatter(frontmatter, content), encoding="utf-8")
        return SimpleNamespace(doc_key=key, path=str(path), frontmatter=frontmatter, content=content)

    def compile_for_owner(self, *, owner_id: int):
        self.reads += 1
        if self.on_read:
            self.on_read(self)
        return SimpleNamespace(owner=dict(self.docs))

    async def update_raw_document(self, **kw):
        self.calls.append(kw)


class _FakeLLM:
    """Answers every batch with the scripted rewrite of each line it was
    sent. ``breaks``: that many first replies come back one line short (the
    count contract broken). ``raises``: that many first calls raise.
    ``empties``: that many first replies are "" (the provider's failure
    shape). ``identical``: lines echoed back unchanged."""

    def __init__(self, *, breaks: int = 0, raises: int = 0, empties: int = 0, identical: frozenset[str] = frozenset()):
        self.calls: list[dict] = []
        self.breaks, self.raises, self.empties, self.identical = breaks, raises, empties, identical

    def sent(self, call: dict) -> list[str]:
        return call["messages"][-1]["content"].splitlines()

    async def chat_completion(self, **kw):
        self.calls.append(kw)
        if self.raises:
            self.raises -= 1
            raise RuntimeError("provider down")
        if self.empties:
            self.empties -= 1
            return ""
        lines = [ln if ln in self.identical else REWRITES.get(ln, ln) for ln in self.sent(kw)]
        if self.breaks:
            self.breaks -= 1
            lines = lines[:-1]
        return "\n".join(lines) + "\n"


def _run(argv, service, llm, tmp_path: Path):
    return asyncio.run(script._amain(
        argv, service=service, llm_client=llm,
        diff_path=tmp_path / "docs" / "rounds" / "memory-first-person-diff.md",
        applied_path=tmp_path / "docs" / "rounds" / "memory-first-person-applied.md",
        plan_path=tmp_path / "docs" / "rounds" / "memory-first-person-plan.json",
        today=TODAY,
    ))


def _dry_run(service, tmp_path: Path, llm=None, owner: str = "1", *extra) -> int:
    """The owner's first step: a dry run with its own fake model; the plan
    and the review document are then on disk for --apply."""
    return _run(["--owner-id", owner, "--dry-run", "--model", "m", *extra], service, llm or _FakeLLM(), tmp_path)


def _diff(tmp_path: Path) -> Path:
    return tmp_path / "docs" / "rounds" / "memory-first-person-diff.md"


def _plan(tmp_path: Path) -> Path:
    return tmp_path / "docs" / "rounds" / "memory-first-person-plan.json"


def _plan_json(tmp_path: Path) -> dict:
    return json.loads(_plan(tmp_path).read_text(encoding="utf-8"))


def _applied(tmp_path: Path) -> Path:
    return tmp_path / "docs" / "rounds" / "memory-first-person-applied.md"


def _bak_files(root: Path) -> list[str]:
    return sorted(p.name for p in root.iterdir() if ".bak" in p.name)


# --- the flags --------------------------------------------------------------

def test_it_never_runs_without_a_mode_flag(tmp_path):
    service, llm = _FakeService(tmp_path / "owners" / "1"), _FakeLLM()
    with pytest.raises(SystemExit) as exc:
        _run(["--owner-id", "1"], service, llm, tmp_path)
    assert exc.value.code != 0
    with pytest.raises(SystemExit) as exc:
        _run(["--owner-id", "1", "--dry-run", "--apply"], service, llm, tmp_path)
    assert exc.value.code != 0
    assert llm.calls == [] and service.calls == [] and _bak_files(service.root) == []


def test_replan_goes_with_apply_only(tmp_path):
    service, llm = _FakeService(tmp_path / "owners" / "1"), _FakeLLM()
    with pytest.raises(SystemExit) as exc:
        _run(["--owner-id", "1", "--dry-run", "--replan"], service, llm, tmp_path)
    assert exc.value.code != 0
    assert llm.calls == [] and not _plan(tmp_path).exists() and not _diff(tmp_path).exists()


# --- dry run ----------------------------------------------------------------

def test_dry_run_writes_the_review_document_and_nothing_else(tmp_path, capsys):
    service, llm = _FakeService(tmp_path / "owners" / "1"), _FakeLLM()
    before = {p.name: p.read_text(encoding="utf-8") for p in service.root.iterdir()}

    rc = _run(["--owner-id", "1", "--dry-run", "--model", "fake-model"], service, llm, tmp_path)

    out = capsys.readouterr().out
    assert rc == 0
    assert service.calls == [] and _bak_files(service.root) == []
    assert {p.name: p.read_text(encoding="utf-8") for p in service.root.iterdir()} == before
    assert not _applied(tmp_path).exists()
    assert sorted(p.name for p in _diff(tmp_path).parent.iterdir()) == [
        "memory-first-person-diff.md", "memory-first-person-plan.json",
    ]
    text = _diff(tmp_path).read_text(encoding="utf-8")
    assert "dry run" in text.splitlines()[0]
    assert "memory-first-person-plan.json" in text and "no new model call" in text
    assert "MEMORY.md" in text and "MEMORY_ARCHIVE.md" in text
    assert "Durable Context" in text and "Archived" in text
    assert f"before: {PHONE_OLD}" in text and f"after: {PHONE_NEW}" in text
    assert f"before: {ARCH_OLD}" in text and f"after: {ARCH_NEW}" in text
    assert f"before: {KM_OLD}" in text and KM_BAD not in text
    assert "kept as written: the facts changed — missing: 1 × number; added: 1 × number" in text
    assert "candidates: 5" in text and "rewritten: 4" in text and "kept as written: 1" in text
    assert "dry run" in out and "nothing else written" in out and "plan written" in out
    assert "candidates: 5" in out


def test_dry_run_saves_the_plan_beside_the_review_document(tmp_path):
    """Ruling R60: the plan --apply will apply is written by the dry run —
    per document its sha256 as planned and every outcome: section, line
    index, the line before, the line after (null when kept) and the
    verdict with its reason."""
    service = _FakeService(tmp_path / "owners" / "1")
    assert _dry_run(service, tmp_path) == 0
    plan = _plan_json(tmp_path)
    assert plan["owner_id"] == 1 and plan["today"] == TODAY and plan["stopped"] is False
    assert set(plan["documents"]) == {"memory", "memory_archive"}
    memory = plan["documents"]["memory"]
    assert memory["name"] == "MEMORY.md"
    assert memory["sha256"] == hashlib.sha256(MEMORY.encode("utf-8")).hexdigest()
    outcomes = {o["before"]: o for o in memory["outcomes"]}
    assert set(outcomes) == {PHONE_OLD, KM_OLD, NAS_OLD, PG_OLD}
    phone = outcomes[PHONE_OLD]
    assert phone == {"section": "Durable Context", "index": MEMORY.split("\n").index(PHONE_OLD),
                     "before": PHONE_OLD, "after": PHONE_NEW, "verdict": "rewrite", "reason": None}
    km = outcomes[KM_OLD]
    assert km["verdict"] == "kept" and km["after"] is None
    assert km["reason"] == "the facts changed — missing: 1 × number; added: 1 × number"
    assert KM_BAD not in _plan(tmp_path).read_text(encoding="utf-8")   # a refused rewrite is not in the plan
    archive = plan["documents"]["memory_archive"]
    assert archive["sha256"] == hashlib.sha256(ARCHIVE.encode("utf-8")).hexdigest()
    assert [o["after"] for o in archive["outcomes"]] == [ARCH_NEW]


def test_dry_run_sends_only_third_person_lines_one_batch_on_the_lane(tmp_path):
    service, llm = _FakeService(tmp_path / "owners" / "1"), _FakeLLM()
    _run(["--owner-id", "1", "--dry-run", "--model", "fake-model"], service, llm, tmp_path)
    assert len(llm.calls) == 1
    call = llm.calls[0]
    assert call["log_context"] == "reflect_first_person" and call["model"] == "fake-model"
    assert call["temperature"] <= 0.3
    sent = llm.sent(call)
    assert sent == [PHONE_OLD, KM_OLD, NAS_OLD, PG_OLD, ARCH_OLD]
    assert "- They like tea." not in sent and "- No durable references recorded yet." not in sent


def test_batches_follow_the_batch_size(tmp_path):
    service, llm = _FakeService(tmp_path / "owners" / "1"), _FakeLLM()
    _run(["--owner-id", "1", "--dry-run", "--model", "m", "--batch", "2"], service, llm, tmp_path)
    assert [len(llm.sent(c)) for c in llm.calls] == [2, 2, 1]


def test_a_batch_that_breaks_the_count_contract_is_retried_line_by_line(tmp_path):
    service, llm = _FakeService(tmp_path / "owners" / "1"), _FakeLLM(breaks=1)
    rc = _run(["--owner-id", "1", "--dry-run", "--model", "m"], service, llm, tmp_path)
    assert rc == 0
    assert [len(llm.sent(c)) for c in llm.calls] == [5, 1, 1, 1, 1, 1]
    text = _diff(tmp_path).read_text(encoding="utf-8")
    assert f"after: {PHONE_NEW}" in text and "rewritten: 4" in text


# --- M5: an empty reply is the provider failing — no line-by-line retry ----------

def test_a_provider_error_is_not_retried_line_by_line(tmp_path):
    service, llm = _FakeService(tmp_path / "owners" / "1"), _FakeLLM(raises=1)
    rc = _run(["--owner-id", "1", "--dry-run", "--model", "m"], service, llm, tmp_path)
    assert rc == 0
    assert len(llm.calls) == 1
    text = _diff(tmp_path).read_text(encoding="utf-8")
    assert "rewritten: 0" in text and "kept as written: 5" in text
    assert text.count("kept as written: no reply from the model") == 5
    assert PHONE_NEW not in text


def test_one_empty_reply_is_skipped_and_the_run_goes_on(tmp_path):
    service, llm = _FakeService(tmp_path / "owners" / "1"), _FakeLLM(empties=1)
    rc = _run(["--owner-id", "1", "--dry-run", "--model", "m", "--batch", "2"], service, llm, tmp_path)
    assert rc == 0
    assert [len(llm.sent(c)) for c in llm.calls] == [2, 2, 1]
    text = _diff(tmp_path).read_text(encoding="utf-8")
    assert "rewritten: 3" in text and "kept as written: 2" in text
    assert f"after: {NAS_NEW}" in text and PHONE_NEW not in text


def test_two_empty_replies_in_a_row_stop_the_run(tmp_path, capsys):
    service, llm = _FakeService(tmp_path / "owners" / "1"), _FakeLLM(empties=2)
    rc = _run(["--owner-id", "1", "--dry-run", "--model", "m", "--batch", "2"], service, llm, tmp_path)
    out = capsys.readouterr().out
    assert rc == 3
    assert [len(llm.sent(c)) for c in llm.calls] == [2, 2]           # the third batch was never asked
    assert "two empty replies" in out and "stopped" in out.lower()
    text = _diff(tmp_path).read_text(encoding="utf-8")
    assert "stopped" in text.lower() and "rewritten: 0" in text and "kept as written: 5" in text
    assert text.count("kept as written: no reply from the model") == 4
    assert text.count("kept as written: the run stopped") == 1


def test_a_stopped_replan_writes_nothing(tmp_path, capsys):
    service, llm = _FakeService(tmp_path / "owners" / "1"), _FakeLLM(empties=2)
    rc = _run(["--owner-id", "1", "--apply", "--replan", "--model", "m", "--batch", "2"], service, llm, tmp_path)
    out = capsys.readouterr().out
    assert rc == 3
    assert service.calls == [] and _bak_files(service.root) == []
    assert not _applied(tmp_path).exists() and not _diff(tmp_path).exists() and not _plan(tmp_path).exists()
    assert "nothing written" in out.lower()


def test_nothing_to_do_makes_no_call(tmp_path, capsys):
    service = _FakeService(tmp_path / "owners" / "1", memory="## Durable Context\n- They like tea.\n", archive="## Archived\n")
    llm = _FakeLLM()
    rc = _run(["--owner-id", "1", "--apply", "--replan", "--model", "m"], service, llm, tmp_path)
    assert rc == 0 and llm.calls == [] and service.calls == [] and _bak_files(service.root) == []
    assert not _applied(tmp_path).exists() and not _diff(tmp_path).exists() and not _plan(tmp_path).exists()
    assert "candidates: 0" in capsys.readouterr().out


# --- apply ------------------------------------------------------------------

def test_apply_backs_up_then_writes_both_documents_then_its_own_record(tmp_path, capsys):
    service = _FakeService(tmp_path / "owners" / "7")
    originals = {p.name: p.read_text(encoding="utf-8") for p in service.root.iterdir()}
    assert _dry_run(service, tmp_path, owner="7") == 0
    the_dry_run = _diff(tmp_path).read_text(encoding="utf-8")
    the_plan = _plan(tmp_path).read_text(encoding="utf-8")
    capsys.readouterr()
    llm = _FakeLLM(identical=frozenset(REWRITES))   # WOULD answer differently now — and is never asked

    rc = _run(["--owner-id", "7", "--apply", "--model", "m"], service, llm, tmp_path)

    out = capsys.readouterr().out
    assert rc == 0
    assert llm.calls == []                                     # ruling R60: no model call on --apply
    assert _bak_files(service.root) == [
        f"MEMORY.md.pre-first-person-{TODAY}.bak",
        f"MEMORY_ARCHIVE.md.pre-first-person-{TODAY}.bak",
    ]
    assert (service.root / f"MEMORY.md.pre-first-person-{TODAY}.bak").read_text(encoding="utf-8") == originals["MEMORY.md"]
    assert (service.root / f"MEMORY_ARCHIVE.md.pre-first-person-{TODAY}.bak").read_text(encoding="utf-8") == originals["MEMORY_ARCHIVE.md"]
    assert len(service.calls) == 2
    by_key = {c["doc_key"]: c for c in service.calls}
    assert set(by_key) == {"memory", "memory_archive"}
    for call in service.calls:
        assert call["user_id"] == 7 and call["identity_owner_id"] == 7 and call["platform"] == "owner"
        assert call["reason"] == "first_person_migration"
        assert call["actor_type"] == "system" and call["actor_id"] == "memory_first_person"
        assert call["allow_admin"] is True
    md = by_key["memory"]["markdown"]
    assert md.startswith("---\ndoc_type: memory\n")
    assert PHONE_NEW in md and PHONE_OLD not in md
    assert NAS_NEW in md and PG_NEW in md
    assert KM_OLD in md and KM_BAD not in md              # the guard's refusal: kept as written
    assert "- They like tea." in md and "- No durable references recorded yet." in md
    arch = by_key["memory_archive"]["markdown"]
    assert arch.startswith("---\ndoc_type: memory_archive\n") and ARCH_NEW in arch
    assert "candidates: 5" in out and "rewritten: 4" in out and "kept as written: 1" in out
    # M4: the record is written AFTER the store writes, to its own file; the
    # dry-run's review document and plan are left exactly as the owner read them.
    applied = _applied(tmp_path).read_text(encoding="utf-8")
    assert "applied" in applied.splitlines()[0] and f"after: {PHONE_NEW}" in applied
    assert "from the plan the dry run saved" in applied and "plan loaded" in out
    assert _diff(tmp_path).read_text(encoding="utf-8") == the_dry_run
    assert _plan(tmp_path).read_text(encoding="utf-8") == the_plan


def test_apply_writes_exactly_the_planned_rewrites_and_nothing_the_plan_refused(tmp_path):
    """Ruling R60, the point: what lands is the plan's accepted rewrites —
    the diff the owner read — line for line; a rewrite the plan kept as
    written stays as written; and a plan edited by hand to accept a
    different line lands THAT line, because --apply reads the plan, not the
    model."""
    service = _FakeService(tmp_path / "owners" / "1")
    assert _dry_run(service, tmp_path) == 0
    plan = _plan_json(tmp_path)
    for o in plan["documents"]["memory"]["outcomes"]:
        if o["before"] == PG_OLD:
            o["after"] = "- They settled on Postgres."   # the owner's own wording, in the plan
    _plan(tmp_path).write_text(json.dumps(plan), encoding="utf-8")

    llm = _FakeLLM()
    rc = _run(["--owner-id", "1", "--apply", "--model", "m"], service, llm, tmp_path)
    assert rc == 0 and llm.calls == []
    by_key = {c["doc_key"]: c for c in service.calls}
    md = by_key["memory"]["markdown"]
    expected = MEMORY.replace(PHONE_OLD, PHONE_NEW).replace(NAS_OLD, NAS_NEW).replace(PG_OLD, "- They settled on Postgres.")
    assert md == dump_frontmatter({"doc_type": "memory", "version": 3}, expected)
    assert KM_OLD in md and KM_BAD not in md and PG_NEW not in md
    assert by_key["memory_archive"]["markdown"] == dump_frontmatter(
        {"doc_type": "memory_archive", "version": 3}, ARCHIVE.replace(ARCH_OLD, ARCH_NEW),
    )
    assert "after: - They settled on Postgres." in _applied(tmp_path).read_text(encoding="utf-8")


def test_apply_without_a_plan_errors_plainly(tmp_path, capsys):
    service, llm = _FakeService(tmp_path / "owners" / "1"), _FakeLLM()
    rc = _run(["--owner-id", "1", "--apply", "--model", "m"], service, llm, tmp_path)
    out = capsys.readouterr().out
    assert rc == 4
    assert "no plan to apply" in out and "--dry-run" in out and "nothing written" in out
    assert llm.calls == [] and service.calls == [] and _bak_files(service.root) == []
    assert not _applied(tmp_path).exists()


def test_apply_refuses_a_plan_for_another_owner(tmp_path, capsys):
    service, llm = _FakeService(tmp_path / "owners" / "1"), _FakeLLM()
    assert _dry_run(service, tmp_path) == 0
    rc = _run(["--owner-id", "2", "--apply", "--model", "m"], _FakeService(tmp_path / "owners" / "2"), llm, tmp_path)
    assert rc == 4 and "for owner 1, not 2" in capsys.readouterr().out
    assert llm.calls == [] and service.calls == []


def _tamper(tmp_path: Path, doc_key: str, fix) -> None:
    """Rewrite the saved plan with ``fix(plan_dict)`` applied — a plan edited
    by hand, or by a bug, into one of the shapes --apply must refuse."""
    plan = _plan_json(tmp_path)
    fix(plan)
    _plan(tmp_path).write_text(json.dumps(plan), encoding="utf-8")


def _nothing_written(service, llm, tmp_path: Path) -> bool:
    return (llm.calls == [] and service.calls == [] and _bak_files(service.root) == []
            and not _applied(tmp_path).exists())


def test_apply_refuses_a_plan_whose_index_disagrees_with_its_before(tmp_path, capsys):
    """Ruling R66: a plan is checked against the document as re-read before
    any backup or write — an outcome whose index points at a different line
    than its ``before`` would overwrite the wrong line."""
    service, llm = _FakeService(tmp_path / "owners" / "1"), _FakeLLM()
    assert _dry_run(service, tmp_path) == 0
    km_index = MEMORY.split("\n").index(KM_OLD)

    def swap(plan):
        for o in plan["documents"]["memory"]["outcomes"]:
            if o["before"] == PHONE_OLD:
                o["index"] = km_index

    _tamper(tmp_path, "memory", swap)
    rc = _run(["--owner-id", "1", "--apply", "--model", "m"], service, llm, tmp_path)
    out = capsys.readouterr().out
    assert rc == 4
    assert f"MEMORY.md, outcome 0 says line {km_index} is a different line" in out and "nothing written" in out
    assert _nothing_written(service, llm, tmp_path)


def test_apply_refuses_a_plan_with_an_index_outside_the_document(tmp_path, capsys):
    """Ruling R66: before the fix this raised IndexError AFTER both backups
    and MEMORY.md were written — a partial apply with no record."""
    service, llm = _FakeService(tmp_path / "owners" / "1"), _FakeLLM()
    assert _dry_run(service, tmp_path) == 0

    def out_of_range(plan):
        plan["documents"]["memory_archive"]["outcomes"][0]["index"] = 999

    _tamper(tmp_path, "memory_archive", out_of_range)
    rc = _run(["--owner-id", "1", "--apply", "--model", "m"], service, llm, tmp_path)
    out = capsys.readouterr().out
    assert rc == 4
    assert "MEMORY_ARCHIVE.md, outcome 0 points at line 999 but MEMORY_ARCHIVE.md has" in out and "nothing written" in out
    assert _nothing_written(service, llm, tmp_path)


def test_apply_refuses_a_plan_with_a_bad_owner_id_or_a_missing_key(tmp_path, capsys):
    """Ruling R66: a non-int owner id, a missing index and a rewrite with no
    line after each end in a plain message and exit 4, never a traceback."""
    service, llm = _FakeService(tmp_path / "owners" / "1"), _FakeLLM()
    assert _dry_run(service, tmp_path) == 0
    saved = _plan(tmp_path).read_text(encoding="utf-8")

    def bad_owner(plan):
        plan["owner_id"] = "one"

    _tamper(tmp_path, "memory", bad_owner)
    rc = _run(["--owner-id", "1", "--apply", "--model", "m"], service, llm, tmp_path)
    assert rc == 4 and "names no usable owner id ('one')" in capsys.readouterr().out

    _plan(tmp_path).write_text(saved, encoding="utf-8")

    def no_index(plan):
        del plan["documents"]["memory"]["outcomes"][1]["index"]

    _tamper(tmp_path, "memory", no_index)
    rc = _run(["--owner-id", "1", "--apply", "--model", "m"], service, llm, tmp_path)
    assert rc == 4 and "MEMORY.md, outcome 1 is missing index, before or verdict" in capsys.readouterr().out

    _plan(tmp_path).write_text(saved, encoding="utf-8")

    def rewrite_without_a_line(plan):
        plan["documents"]["memory"]["outcomes"][0]["after"] = None

    _tamper(tmp_path, "memory", rewrite_without_a_line)
    rc = _run(["--owner-id", "1", "--apply", "--model", "m"], service, llm, tmp_path)
    assert rc == 4 and "MEMORY.md, outcome 0 is a rewrite with no line after" in capsys.readouterr().out
    assert _nothing_written(service, llm, tmp_path)


def test_apply_aborts_when_a_document_changed_since_the_dry_run(tmp_path, capsys):
    """The re-read guard, extended (R60): the plan carries each document's
    sha256 as planned; a reflection that wrote in between makes --apply
    write nothing and say so."""
    service, llm = _FakeService(tmp_path / "owners" / "1"), _FakeLLM()
    assert _dry_run(service, tmp_path) == 0
    service.docs["memory"].content += "- They mentioned a trip.\n"
    rc = _run(["--owner-id", "1", "--apply", "--model", "m"], service, llm, tmp_path)
    out = capsys.readouterr().out
    assert rc == 2
    assert "MEMORY.md changed since the dry run" in out and "nothing written" in out.lower()
    assert llm.calls == [] and service.calls == [] and _bak_files(service.root) == []
    assert not _applied(tmp_path).exists()


def test_apply_aborts_when_a_document_moved_between_the_plan_and_the_write(tmp_path, capsys):
    """The original re-read guard, on both paths: the document moves AFTER
    the plan is loaded (or made) and before the write."""
    # from the plan: reads are the dry run (1), the load (2), the re-read (3)
    service, llm = _FakeService(tmp_path / "owners" / "1"), _FakeLLM()
    assert _dry_run(service, tmp_path) == 0

    def _reflection_wrote_late(svc):
        if svc.reads == 3:
            svc.docs["memory"].content += "- They mentioned a trip.\n"

    service.on_read = _reflection_wrote_late
    rc = _run(["--owner-id", "1", "--apply", "--model", "m"], service, llm, tmp_path)
    out = capsys.readouterr().out
    assert rc == 2 and llm.calls == []
    assert service.calls == [] and _bak_files(service.root) == []
    assert not _applied(tmp_path).exists()
    assert "changed since the dry run" in out and "nothing written" in out.lower()

    # --replan: the plan read (1), the re-read (2)
    service, llm = _FakeService(tmp_path / "owners" / "1"), _FakeLLM()

    def _reflection_wrote(svc):
        if svc.reads == 2:
            svc.docs["memory"].content += "- They mentioned a trip.\n"

    service.on_read = _reflection_wrote
    rc = _run(["--owner-id", "1", "--apply", "--replan", "--model", "m"], service, llm, tmp_path)
    out = capsys.readouterr().out
    assert rc == 2
    assert service.calls == [] and _bak_files(service.root) == []
    assert not _applied(tmp_path).exists()
    assert "changed while planning" in out and "nothing written" in out.lower()


def test_apply_replan_asks_the_model_again_and_writes_a_fresh_plan_first(tmp_path, capsys):
    """--apply --replan is today's behaviour, made explicit: a fresh model
    call, a fresh plan file written before the documents, then the writes
    and the record — which says the owner did not read these lines first."""
    service, llm = _FakeService(tmp_path / "owners" / "1"), _FakeLLM()
    rc = _run(["--owner-id", "1", "--apply", "--replan", "--model", "m"], service, llm, tmp_path)
    out = capsys.readouterr().out
    assert rc == 0
    assert len(llm.calls) == 1
    assert "plan written (--replan)" in out
    plan = _plan_json(tmp_path)
    assert plan["owner_id"] == 1 and {o["after"] for o in plan["documents"]["memory"]["outcomes"]} >= {PHONE_NEW, NAS_NEW, PG_NEW}
    assert [c["doc_key"] for c in service.calls] == ["memory", "memory_archive"]
    assert "--replan" in _applied(tmp_path).read_text(encoding="utf-8")
    assert not _diff(tmp_path).exists()   # a replan writes no review document; the applied record is its record


def test_apply_writes_only_the_document_that_changed(tmp_path):
    service = _FakeService(tmp_path / "owners" / "1", archive="## Archived\n- They dropped the habit.\n")
    assert _dry_run(service, tmp_path) == 0
    llm = _FakeLLM()
    rc = _run(["--owner-id", "1", "--apply", "--model", "m"], service, llm, tmp_path)
    assert rc == 0 and llm.calls == []
    assert [c["doc_key"] for c in service.calls] == ["memory"]
    assert _bak_files(service.root) == [f"MEMORY.md.pre-first-person-{TODAY}.bak"]


def test_an_unchanged_reply_is_kept_as_written_and_earns_no_backup_or_write(tmp_path):
    service = _FakeService(tmp_path / "owners" / "1")
    assert _dry_run(service, tmp_path, _FakeLLM(identical=frozenset({ARCH_OLD}))) == 0
    rc = _run(["--owner-id", "1", "--apply", "--model", "m"], service, _FakeLLM(), tmp_path)
    assert rc == 0
    assert [c["doc_key"] for c in service.calls] == ["memory"]
    assert _bak_files(service.root) == [f"MEMORY.md.pre-first-person-{TODAY}.bak"]
    applied = _applied(tmp_path).read_text(encoding="utf-8")
    assert "kept as written: the model returned the line unchanged" in applied
    assert "rewritten: 3" in applied and "kept as written: 2" in applied


def test_a_missing_archive_is_skipped(tmp_path, capsys):
    service = _FakeService(tmp_path / "owners" / "1", archive=None)
    assert _dry_run(service, tmp_path) == 0
    capsys.readouterr()
    rc = _run(["--owner-id", "1", "--apply", "--model", "m"], service, _FakeLLM(), tmp_path)
    out = capsys.readouterr().out
    assert rc == 0
    assert [c["doc_key"] for c in service.calls] == ["memory"]
    assert _bak_files(service.root) == [f"MEMORY.md.pre-first-person-{TODAY}.bak"]
    assert "MEMORY_ARCHIVE.md" not in _applied(tmp_path).read_text(encoding="utf-8")
    assert "candidates: 4" in out and "rewritten: 3" in out


def test_a_backup_never_overwrites_an_earlier_one(tmp_path):
    service = _FakeService(tmp_path / "owners" / "1")
    earlier = service.root / f"MEMORY.md.pre-first-person-{TODAY}.bak"
    earlier.write_text("the earlier backup\n", encoding="utf-8")
    assert _dry_run(service, tmp_path) == 0
    rc = _run(["--owner-id", "1", "--apply", "--model", "m"], service, _FakeLLM(), tmp_path)
    assert rc == 0
    assert earlier.read_text(encoding="utf-8") == "the earlier backup\n"
    assert len([n for n in _bak_files(service.root) if n.startswith("MEMORY.md.")]) == 2


# --- her person by name once she knows it (owner call 2026-09-29) --------------

def test_a_fresh_install_writes_the_neutral_wording():
    import core.memory_first_person as fp
    who = fp.person("", "")
    assert not who.known and who.pronouns == "they/them"
    assert fp.build_instruction(who) == fp.INSTRUCTION
    assert "They'd rather I" in fp.short_rule(who) and "They're working on" in fp.tone_rule(who)


def test_stand_in_names_are_not_names():
    import core.memory_first_person as fp
    for stand_in in ("User", "the user", "Local Operator", "owner", ""):
        assert fp.person(stand_in, "he/him").known is False


def test_a_known_name_reaches_every_wording():
    import core.memory_first_person as fp
    who = fp.person("Robin", "he/him")
    assert who.known and who.words == ("he", "him", "his")
    for text in (fp.build_instruction(who), fp.short_rule(who), fp.tone_rule(who)):
        assert "Robin" in text and "they/them" not in text and "he/him" in text
    assert "Robin is working on a Rust project called Atlas" in fp.tone_rule(who)
    assert "I told him the backup ran clean" in fp.tone_rule(who)


def test_unknown_pronouns_fall_back_to_neutral_with_the_name():
    import core.memory_first_person as fp
    who = fp.person("Sam", "xe/xem")
    assert who.pronouns == "they/them" and "Sam" in fp.short_rule(who) and "They're working on" in fp.person_sentence(who)


def test_the_rewrite_messages_carry_the_person():
    import core.memory_first_person as fp
    msgs = fp.build_rewrite_messages(["- User likes tea."], who=fp.person("Robin", "he/him"))
    assert msgs[0]["role"] == "system" and "Robin" in msgs[0]["content"]
    assert fp.build_rewrite_messages(["- User likes tea."])[0]["content"] == fp.INSTRUCTION


def test_the_guard_allows_the_persons_name_and_nothing_else():
    import core.memory_first_person as fp
    old = "- I told the user the Thingino camera is back on Tuesday."
    assert fp.guard(old, "- I told Robin the Thingino camera is back on Tuesday.", allowed_names=("Robin",))
    assert not fp.guard(old, "- I told Robin the Thingino camera is back on Tuesday.")            # a new mid-sentence name is a new fact unless allowed
    assert not fp.guard(old, "- I told Robin the Contoso camera is back on Tuesday.", allowed_names=("Robin",))  # the guard still holds elsewhere
