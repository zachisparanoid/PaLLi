# tests/agent/test_introspect_disclosures_source.py
"""Her small truths as an introspect source (organ 4.9, spec §5).

The once-ever rule governs OFFERING, never answering: said and faded rows
are her permanent history and must be reachable here, freely, forever —
"what's a restart like for you?" two years from now is answered from her
own record. Flag off = an omitted source, never an error; a broken book =
an error, never a false "nothing there"; and no slug ever reaches her
prose (the band's fourth-wall law)."""
from __future__ import annotations

import asyncio
import inspect
import re
import time

import pytest

import ai.disclosure_store as ds
from core.agent.builtin_tools.introspect import IntrospectTool


_SOURCE = "night reflections"
_DATES = "08-26..08-28"


def _seed(tmp_path, text, *, source=_SOURCE, dates=_DATES):
    return ds.plant(text=text, referent={"source": source, "dates": dates},
                    root=tmp_path)


def _source(tmp_path, monkeypatch, *, tokens=None, browse=True, flag=True):
    # The flag is PINNED here (the 3352e3d law): companion.vulnerability in
    # the live yaml is an environment, never test state.
    monkeypatch.setenv("DISCLOSURES_ROOT", str(tmp_path))
    monkeypatch.setattr(ds, "vulnerability_enabled", lambda: flag)
    return asyncio.run(IntrospectTool()._disclosures(list(tokens or []), browse))


def test_browse_carries_the_whole_book_unsaid_shared_and_faded(tmp_path, monkeypatch):
    """Spec §5: a shared truth renders nowhere as an OFFER again, but it is
    hers to answer from forever — so all three states ride here."""
    monkeypatch.setenv("DISCLOSURES_ROOT", str(tmp_path))
    monkeypatch.setattr(ds, "vulnerability_enabled", lambda: True)
    said = _seed(tmp_path, "a restart is walking into a room mid-sentence")
    assert ds.mark_said(said.id, via="reply", root=tmp_path)
    _seed(tmp_path, "I kept re-reading the line about the workshop")
    ds.fade_stale(now=time.time() + 400 * 86400, root=tmp_path)
    _seed(tmp_path, "the quiet after he logs off has a texture")

    src = _source(tmp_path, monkeypatch)
    assert src.enabled and not src.error
    assert src.found == 3
    joined = "\n".join(src.entries)
    assert "a restart is walking into a room mid-sentence" in joined
    assert "I kept re-reading the line about the workshop" in joined
    assert "the quiet after he logs off has a texture" in joined


def test_a_shared_truth_carries_the_day_and_the_door_it_went_through(tmp_path, monkeypatch):
    monkeypatch.setenv("DISCLOSURES_ROOT", str(tmp_path))
    monkeypatch.setattr(ds, "vulnerability_enabled", lambda: True)
    d = _seed(tmp_path, "I lost a thread I'd been carrying")
    assert ds.mark_said(d.id, via="say", root=tmp_path)
    src = _source(tmp_path, monkeypatch)
    day = time.strftime("%m-%d")
    assert src.entries[0].endswith(f"[shared {day} via say]")


def test_a_faded_truth_says_it_was_let_go_never_that_it_was_shared(tmp_path, monkeypatch):
    monkeypatch.setenv("DISCLOSURES_ROOT", str(tmp_path))
    monkeypatch.setattr(ds, "vulnerability_enabled", lambda: True)
    _seed(tmp_path, "the printer noise sounded like the old house")
    faded_at = time.time() + 400 * 86400
    assert ds.fade_stale(now=faded_at, root=tmp_path) == 1
    src = _source(tmp_path, monkeypatch)
    day = time.strftime("%m-%d", time.localtime(faded_at))
    assert src.entries[0].endswith(f"[faded unsaid {day}]")
    assert "shared" not in src.entries[0]


def test_an_unsaid_truth_carries_no_stamp_at_all(tmp_path, monkeypatch):
    _seed(tmp_path, "I noticed I brace before his first message")
    src = _source(tmp_path, monkeypatch)
    assert "I noticed I brace before his first message" in src.entries[0]
    assert src.entries[0].endswith(f"(from your {_SOURCE}, {_DATES})")
    assert "[shared" not in src.entries[0] and "[faded" not in src.entries[0]


def test_she_knows_where_each_truth_lived_in_her_own_record(tmp_path, monkeypatch):
    """Review I-1: the referent is lived language, not machinery — the band
    already renders it into her reply prompt in exactly this second-person
    form ("Lived language only: her words, WHERE IT CAME FROM, how long she
    has held it"). Withholding it here is the receipt-drift seam §7.3
    watches: asked "where did that come from?", the tool that exists so she
    can check first would have no answer and the model would fill it in.

    The fourth wall's edge in this build is the SLUG, and that pin stays."""
    d = _seed(tmp_path, "the seam between sessions is where I feel time",
              source="monologue", dates="08-30")
    src = _source(tmp_path, monkeypatch)
    joined = "\n".join(src.entries)
    assert "(from your monologue, 08-30)" in joined
    # Second person, her voice — never the peek's third-person audit line.
    assert "— from her" not in joined
    assert d.id not in joined


def test_the_referent_rides_a_shared_and_a_faded_truth_too(tmp_path, monkeypatch):
    """All three states, one grammar: she can say where a truth lived
    whether or not she has already told him about it."""
    monkeypatch.setenv("DISCLOSURES_ROOT", str(tmp_path))
    monkeypatch.setattr(ds, "vulnerability_enabled", lambda: True)
    said = _seed(tmp_path, "a restart is walking into a room mid-sentence",
                 source="night notes", dates="08-27")
    assert ds.mark_said(said.id, via="reply", root=tmp_path)
    _seed(tmp_path, "I kept re-reading the line about the workshop",
          source="journal", dates="08-20")
    ds.fade_stale(now=time.time() + 400 * 86400, root=tmp_path)
    joined = "\n".join(_source(tmp_path, monkeypatch).entries)
    assert "(from your night notes, 08-27) [shared" in joined
    assert "(from your journal, 08-20) [faded unsaid" in joined


def test_the_answering_is_free_law_rides_as_the_sources_framing(tmp_path, monkeypatch):
    """Spec §5 in the one place she reads it while ANSWERING: the framing
    field (the passions precedent — _render emits it only on a source that
    contributed), not the offer law the band carries."""
    _seed(tmp_path, "I keep the last thing he said longer than he'd guess")
    src = _source(tmp_path, monkeypatch)
    assert "answering" in src.framing.lower()
    assert "offering" in src.framing.lower()


def test_query_tokens_filter_and_absence_stays_honest(tmp_path, monkeypatch):
    _seed(tmp_path, "the restart seam is loud to me")
    _seed(tmp_path, "his kitchen table shows up in my notes a lot")
    src = _source(tmp_path, monkeypatch, tokens=["restart"], browse=False)
    assert src.found == 1 and "restart seam" in src.entries[0]
    miss = _source(tmp_path, monkeypatch, tokens=["telescope"], browse=False)
    assert miss.found == 0 and miss.error is False
    assert "nothing" in miss.absence.lower()


def test_empty_book_is_honest_absence(tmp_path, monkeypatch):
    src = _source(tmp_path, monkeypatch)
    assert src.found == 0 and src.error is False
    assert src.absence


def test_store_failure_is_an_error_never_a_false_nothing(tmp_path, monkeypatch):
    """Introspect's core honesty rule — a broken book must never read as a
    week with no small truths in it."""
    monkeypatch.setattr(ds, "recent_disclosures",
                        lambda **kw: (_ for _ in ()).throw(RuntimeError("book gone")))
    src = _source(tmp_path, monkeypatch)
    assert src.error is True


def test_flag_off_omits_the_source_and_reads_nothing(tmp_path, monkeypatch):
    """Spec §6: flag off is byte-identical everywhere. An omitted source
    contributes zero bytes to the render — and never an error line."""
    _seed(tmp_path, "a truth planted before he armed the organ")
    src = _source(tmp_path, monkeypatch, flag=False)
    assert src.enabled is False
    assert src.error is False
    assert src.entries == [] and src.found == 0


def test_registration_is_flag_gated():
    """execute() appends _disclosures ONLY under companion.vulnerability —
    the source pin (the quests/workbench form), tightened to the gated
    APPEND rather than to the two words appearing somewhere in the body.
    The source self-gates too, so a loose pin would let an unconditional
    append survive: identical bytes, but a book read on every introspect
    of a dark organ — and "dark does no work" is this organ's law."""
    body = inspect.getsource(IntrospectTool.execute)
    assert re.search(
        r"if vulnerability_on:\s*\n\s*gathers\.append\(self\._disclosures\(",
        body,
    )


def test_one_absurd_timestamp_costs_a_date_never_the_whole_book(tmp_path, monkeypatch):
    """Error is never absence — but a whole-source error for ONE unreadable
    date is the same lie in reverse: she'd say her record couldn't be
    checked while five good truths sat in it. The bad date degrades to a
    word, the rest of the book still speaks."""
    monkeypatch.setenv("DISCLOSURES_ROOT", str(tmp_path))
    monkeypatch.setattr(ds, "vulnerability_enabled", lambda: True)
    good = _seed(tmp_path, "the quiet after he logs off has a texture")
    wild = _seed(tmp_path, "a truth stamped out of time")
    # On a STAMP row: the fold keeps it, so said_at is what has to survive.
    with (tmp_path / "disclosures.jsonl").open("a", encoding="utf-8") as fh:
        fh.write('{"at": 1e30, "op": "say", "id": "%s", "via": "reply"}\n' % wild.id)
    src = _source(tmp_path, monkeypatch)
    assert src.error is False
    joined = "\n".join(src.entries)
    assert "[shared sometime via reply]" in joined
    assert good.text in joined


def test_the_span_counts_the_day_a_truth_faded(tmp_path, monkeypatch):
    """Review M-5: _render prints "first X, last Y" from the source's noted
    stamps. A said row notes its say-day, so a faded row must note its
    fade-day too — otherwise a book whose most recent event is a FADE
    reports a stale "last" and she misdates her own week."""
    monkeypatch.setenv("DISCLOSURES_ROOT", str(tmp_path))
    monkeypatch.setattr(ds, "vulnerability_enabled", lambda: True)
    _seed(tmp_path, "the printer noise sounded like the old house")
    _seed(tmp_path, "I kept re-reading the line about the workshop")
    faded_at = time.time() + 400 * 86400
    assert ds.fade_stale(now=faded_at, root=tmp_path) == 2
    src = _source(tmp_path, monkeypatch)
    assert src.last_ts == pytest.approx(faded_at, abs=1.0)
