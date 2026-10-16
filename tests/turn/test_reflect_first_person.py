"""Batch 5, Task 10 (memory that picks, 2026-09-28): her memory of them is
written in her own voice.

Two belts. The prompt tells the reflection to write every memory line as
PaLLi about them ("They'd rather I…", "They told me…"), never "User…",
never "PaLLi" in the third person. And the writer catches what still slips
through: the NEW items of a reflection that read as case notes are sent, all
together, in one extra provider call on the ``reflect_first_person`` lane
(the reflect model, low temperature) and come back in her voice — each
rewrite guarded (names, dates, ids, paths, numbers, quoted spans byte-exact)
or the item is kept as written. An error keeps every item as written.

Fix round 1: the rewrite happens BEFORE the document is read, so the read →
append → belt → write stretch has no await in it (another writer's line
landed during the rewrite is never overwritten); the Batch 1 belt runs over
the items before they are sent (a credential-shaped item never reaches the
model); and every log line names token kinds and counts, never a value.
"""
from __future__ import annotations

import logging
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from core.turn import reflect
from core.turn.types import ReflectionMemoryUpdates

ROOT = Path(__file__).resolve().parents[2]

_EXISTING = "- User's phone number is in ~/.palli/owner_contact.json; explicitly invited PaLLi to call anytime (2026-08-01)."
_DOC = (
    "## Durable Context\n"
    "- likes tea\n"
    f"{_EXISTING}\n"
    "\n"
    "## Active Threads\n"
    "- No active curated threads yet.\n"
    "\n"
    "## Important Decisions\n"
    "- No important decisions recorded yet.\n"
    "\n"
    "## Useful References\n"
    "- No durable references recorded yet.\n"
)
_SK = "sk-" + "abcdefghijklmnopqrstuvwxyz012345"


class _FakeStore:
    """The identity service's two doors, recording the ORDER of every read,
    rewrite and write; ``on_rewrite`` lets a test play another reflection
    that writes while this one awaits the model."""

    def __init__(self, content: str):
        self.content = content
        self.events: list[str] = []
        self.written: dict = {}

    def compile_for_owner(self, *, owner_id):
        self.events.append("read")
        doc = SimpleNamespace(content=self.content, frontmatter={"doc_type": "memory", "version": 3})
        return SimpleNamespace(owner={"memory": doc})

    async def update_raw_document(self, **kw):
        self.events.append("write")
        self.written.update(kw)


class _FakeLLM:
    def __init__(self, reply=None, exc: Exception | None = None, store: _FakeStore | None = None, on_rewrite=None):
        self.reply, self.exc, self.calls = reply, exc, []
        self.store, self.on_rewrite = store, on_rewrite

    async def chat_completion(self, **kw):
        self.calls.append(kw)
        if self.store is not None:
            self.store.events.append("rewrite")
        if self.on_rewrite:
            self.on_rewrite()
        if self.exc:
            raise self.exc
        return self.reply


async def _apply(updates: ReflectionMemoryUpdates, fake: _FakeLLM, content: str = _DOC) -> dict:
    store = fake.store or _FakeStore(content)
    fake.store = store
    with patch.object(reflect.identity_service, "compile_for_owner", side_effect=store.compile_for_owner), \
         patch.object(reflect.identity_service, "update_raw_document", side_effect=store.update_raw_document), \
         patch.object(reflect, "llm", fake), \
         patch.object(reflect, "_resolve_reflect_model", lambda: "fake-model"):
        await reflect._apply_memory_updates(owner_id=1, updates=updates)
    return store.written


def _sent(fake: _FakeLLM, index: int = 0) -> str:
    return fake.calls[index]["messages"][-1]["content"]


@pytest.fixture(autouse=True)
def _no_real_redaction(monkeypatch):
    from core import secrets_redaction as sr
    monkeypatch.setattr(sr, "redact", lambda t: t)


@pytest.mark.asyncio
async def test_only_the_third_person_item_is_sent_once_on_its_lane_and_rewritten():
    fake = _FakeLLM(reply="- They prefer tea over coffee.\n")
    written = await _apply(ReflectionMemoryUpdates(
        durable_context_add=["User prefers tea over coffee.", "They told me they walk at dawn."],
    ), fake)
    assert len(fake.calls) == 1
    call = fake.calls[0]
    assert call["log_context"] == "reflect_first_person"
    assert call["model"] == "fake-model"
    assert call["temperature"] <= 0.3
    assert _sent(fake) == "- User prefers tea over coffee."          # the first-person item is never sent
    md = written["markdown"]
    assert "- They prefer tea over coffee." in md
    assert "User prefers tea over coffee." not in md
    assert "- They told me they walk at dawn." in md


@pytest.mark.asyncio
async def test_a_thread_item_is_sent_bare_and_gets_its_stamp_when_appended():
    fake = _FakeLLM(reply="- They're fixing the NAS backup.\n")
    written = await _apply(ReflectionMemoryUpdates(active_threads_add=["User is fixing the NAS backup."]), fake)
    stamp = time.strftime("%Y-%m-%d")
    assert _sent(fake) == "- User is fixing the NAS backup."         # the stamp is the appender's, added after
    assert f"- [{stamp}] They're fixing the NAS backup." in written["markdown"]


@pytest.mark.asyncio
async def test_an_existing_third_person_line_is_never_sent():
    fake = _FakeLLM(reply="- They prefer tea over coffee.\n")
    written = await _apply(ReflectionMemoryUpdates(durable_context_add=["User prefers tea over coffee."]), fake)
    assert _EXISTING not in _sent(fake)
    assert _EXISTING in written["markdown"]                 # and it stays exactly as it was


@pytest.mark.asyncio
async def test_a_re_emitted_existing_line_is_still_deduplicated_not_appended_in_her_voice():
    fake = _FakeLLM(reply="- Their number is in ~/.palli/owner_contact.json; they told me to call anytime (2026-08-01).\n")
    written = await _apply(ReflectionMemoryUpdates(durable_context_add=[_EXISTING[2:]]), fake)
    assert written == {}, "the line is already on disk — nothing to write"


@pytest.mark.asyncio
async def test_no_third_person_item_means_no_call():
    fake = _FakeLLM(reply="never used")
    written = await _apply(ReflectionMemoryUpdates(durable_context_add=["They'd rather I call before noon."]), fake)
    assert fake.calls == []
    assert "- They'd rather I call before noon." in written["markdown"]


# --- IMPORTANT 1: no await between the read and the write ------------------------

@pytest.mark.asyncio
async def test_the_rewrite_happens_before_the_read_and_the_write_follows_the_read_without_an_await():
    fake = _FakeLLM(reply="- They prefer tea.\n")
    await _apply(ReflectionMemoryUpdates(durable_context_add=["User prefers tea."]), fake)
    assert fake.store.events == ["rewrite", "read", "write"]


@pytest.mark.asyncio
async def test_a_line_another_reflection_wrote_during_the_rewrite_is_not_lost():
    store = _FakeStore(_DOC)

    def _other_reflection_writes():
        store.content = store.content.replace("- likes tea\n", "- likes tea\n- They mentioned a trip to Lisbon.\n")

    fake = _FakeLLM(reply="- They prefer tea.\n", store=store, on_rewrite=_other_reflection_writes)
    written = await _apply(ReflectionMemoryUpdates(durable_context_add=["User prefers tea."]), fake)
    md = written["markdown"]
    assert "- They mentioned a trip to Lisbon." in md
    assert "- They prefer tea." in md


# --- IMPORTANT 4: a secret never reaches the model or the log ---------------------

@pytest.mark.asyncio
async def test_a_credential_shaped_item_is_dropped_before_the_rewrite_and_never_logged(caplog):
    caplog.set_level(logging.DEBUG, logger="core.turn.reflect")
    fake = _FakeLLM(reply="- They prefer tea.\n")
    written = await _apply(ReflectionMemoryUpdates(
        durable_context_add=[f"User's API key is {_SK}", "User prefers tea."],
    ), fake)
    assert len(fake.calls) == 1
    assert _SK not in str(fake.calls[0]["messages"])
    assert _sent(fake) == "- User prefers tea."
    assert _SK not in written["markdown"] and "- They prefer tea." in written["markdown"]
    assert all(_SK not in r.getMessage() for r in caplog.records)
    assert "dropped 1 secret-shaped memory line(s)" in caplog.text


@pytest.mark.asyncio
async def test_a_rewrite_that_fails_the_guard_keeps_the_item_and_logs_kinds_only(caplog):
    caplog.set_level(logging.INFO, logger="core.turn.reflect")
    fake = _FakeLLM(reply="- They run 6 km on Mondays.\n")
    written = await _apply(ReflectionMemoryUpdates(durable_context_add=["User runs 5 km on Mondays."]), fake)
    md = written["markdown"]
    assert "- User runs 5 km on Mondays." in md and "6 km" not in md
    refused = [r.getMessage() for r in caplog.records if "refused" in r.getMessage()]
    assert len(refused) == 1
    assert "missing: 1 × number; added: 1 × number" in refused[0]
    assert "6" not in refused[0] and "5 km" not in refused[0] and "Mondays" not in refused[0]


@pytest.mark.asyncio
async def test_a_reply_that_breaks_the_count_keeps_every_item_as_written():
    fake = _FakeLLM(reply="- They prefer tea.\n- They run 5 km on Mondays.\n- an extra line\n")
    written = await _apply(ReflectionMemoryUpdates(
        durable_context_add=["User prefers tea.", "User runs 5 km on Mondays."],
    ), fake)
    md = written["markdown"]
    assert "- User prefers tea." in md and "- User runs 5 km on Mondays." in md


@pytest.mark.asyncio
async def test_a_provider_error_keeps_every_item_as_written(caplog):
    caplog.set_level(logging.WARNING, logger="core.turn.reflect")
    fake = _FakeLLM(exc=RuntimeError("provider down"))
    written = await _apply(ReflectionMemoryUpdates(durable_context_add=["User prefers tea."]), fake)
    assert "- User prefers tea." in written["markdown"]
    assert "her voice" in caplog.text


@pytest.mark.asyncio
async def test_an_empty_reply_keeps_every_item_as_written():
    fake = _FakeLLM(reply="")
    written = await _apply(ReflectionMemoryUpdates(durable_context_add=["User prefers tea."]), fake)
    assert "- User prefers tea." in written["markdown"]


@pytest.mark.asyncio
async def test_an_unchanged_reply_is_not_counted_as_a_rewrite(caplog):
    caplog.set_level(logging.INFO, logger="core.turn.reflect")
    fake = _FakeLLM(reply="- User prefers tea.\n")
    written = await _apply(ReflectionMemoryUpdates(durable_context_add=["User prefers tea."]), fake)
    assert "- User prefers tea." in written["markdown"]
    assert "rewritten in her voice" not in caplog.text


# --- the prompt (R18) ---------------------------------------------------------

def test_the_reflect_prompt_asks_for_her_own_voice():
    # The template carries two placeholders (2026-09-29: her person by name once
    # known); a fresh install renders the neutral wording, pinned here.
    import core.memory_first_person as fp
    text = (ROOT / "prompts" / "turn_reflect.txt").read_text(encoding="utf-8")
    text = text.replace("{memory_person_short}", fp.short_rule()).replace("{memory_person_rule}", fp.tone_rule())
    assert "Write in third person" not in text
    assert '"User is working on a Rust project called Atlas." (good)' not in text
    assert "Write every memory line in PaLLi's own voice about them" in text
    assert 'never "User…", never "PaLLi" in the third person' in text
    assert "## Tone of memory entries" in text
    assert '"They\'re working on a Rust project called Atlas." (good)' in text
    assert '"User is working on a Rust project called Atlas." (bad)' in text
    assert "(good)" in text.split("## Tone of memory entries")[1]
    assert "\r" not in text


@pytest.mark.asyncio
async def test_a_multi_line_item_reaches_the_document_as_one_line():
    """Fix wave (IMPORTANT 3): an item with an embedded newline used to lose
    everything after the newline at the "- " belt, silently, on every
    reflection. Its whitespace is flattened first — one bullet, one line —
    so both halves land in one line, whether the item goes to the model
    (a case note) or straight to the file (already in her voice)."""
    fake = _FakeLLM(reply="- They keep the spare key under the blue pot.\n")
    written = await _apply(ReflectionMemoryUpdates(
        durable_context_add=["User keeps the spare key\nunder the blue pot.", "They water the\n  ferns\ton Sundays."],
    ), fake)
    assert _sent(fake) == "- User keeps the spare key under the blue pot."   # sent flattened
    md = written["markdown"]
    assert "- They keep the spare key under the blue pot." in md
    assert "- They water the ferns on Sundays." in md
    assert "\nunder the blue pot." not in md and "\n  ferns" not in md
    assert "spare key\n" not in md


@pytest.mark.asyncio
async def test_a_multi_line_secret_in_a_new_item_is_redacted_before_the_flatten_and_never_sent(monkeypatch):
    """Ruling R66 (the fix wave's re-review): the one-line flatten must not
    defeat the secrets belt. ``sr.redact`` matches a known literal exactly
    and the vault key file is one multi-line literal (the shape
    test_reflect_memory_belt.py models), so a new item carrying it is
    redacted BEFORE its whitespace is flattened — the model never sees the
    key, whole or flattened, and the saved line reads ``[REDACTED]``."""
    from core import secrets_redaction as sr

    block = "<Key>\n  <Data>" + "QUJD" + "REVGR0g=</Data>\n</Key>"
    flat = " ".join(block.split())
    monkeypatch.setattr(sr, "redact", lambda t: t.replace(block, "[REDACTED]"))
    fake = _FakeLLM(reply="- They keep the vault key on the NAS: [REDACTED]\n")
    written = await _apply(ReflectionMemoryUpdates(
        durable_context_add=[f"User keeps the vault key on the NAS: {block}", f"They keep a spare copy: {block}"],
    ), fake)
    assert len(fake.calls) == 1
    seen_by_the_model = "\n".join(str(m["content"]) for m in fake.calls[0]["messages"])
    assert block not in seen_by_the_model and flat not in seen_by_the_model and "QUJD" not in seen_by_the_model
    assert _sent(fake) == "- User keeps the vault key on the NAS: [REDACTED]"
    md = written["markdown"]
    assert "QUJD" not in md and flat not in md and block not in md
    assert "- They keep the vault key on the NAS: [REDACTED]" in md
    assert "- They keep a spare copy: [REDACTED]" in md


def test_the_reflection_prompt_names_them_once_known_and_stays_neutral_otherwise():
    """The template carries two placeholders; a fresh install renders the
    neutral rule byte-for-byte, an owner with a real name gets the name."""
    from types import SimpleNamespace
    from ai.prompt_loader import prompt_loader
    import core.memory_first_person as fp
    from core.agent.context_adapter import resolve_owner_person

    template = prompt_loader.load_prompt("turn_reflect")
    assert "{memory_person_short}" in template and "{memory_person_rule}" in template
    neutral = fp.person()
    assert '"they/them"' in fp.tone_rule(neutral) and "Robin" not in fp.tone_rule(neutral)

    class _Doc:
        frontmatter = {"display_name": "Robin", "pronouns": "he/him"}
    known = fp.person(*resolve_owner_person(SimpleNamespace(owner={"personality_profile": _Doc()})))
    assert known.name == "Robin" and known.pronouns == "he/him"
    stand_in = fp.person(*resolve_owner_person(SimpleNamespace(owner={"personality_profile": SimpleNamespace(frontmatter={"display_name": "Local Operator"})})))
    assert stand_in.known is False
