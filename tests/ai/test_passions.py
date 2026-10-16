"""Organ 3 module: gates, receipts, nightly journal, weekly interests."""
from __future__ import annotations

import json
from datetime import datetime
from types import SimpleNamespace

import pytest


def test_flag_defaults_false():
    from core.settings_schema import CompanionConfig
    assert CompanionConfig().passions is False


def test_passions_enabled_requires_both(monkeypatch):
    from ai import passions
    # Re-patch to use the real implementation (normally off-by-default for test isolation).
    monkeypatch.setattr(passions, "passions_enabled", passions.passions_enabled_impl)

    monkeypatch.setattr(passions, "companion_settings", lambda: None)
    assert passions.passions_enabled() is False
    monkeypatch.setattr(
        passions, "companion_settings",
        lambda: SimpleNamespace(passions=True, night_cycle=False),
    )
    assert passions.passions_enabled() is False
    monkeypatch.setattr(
        passions, "companion_settings",
        lambda: SimpleNamespace(passions=True, night_cycle=True),
    )
    assert passions.passions_enabled() is True


def test_first_authorship_receipt(tmp_path):
    from ai import passions
    assert passions.first_authorship_done(repo_root=tmp_path) is False
    (tmp_path / "INTERESTS.md").write_text("## Bridges\nhow it started\n", encoding="utf-8")
    assert passions.first_authorship_done(repo_root=tmp_path) is True
    assert "Bridges" in passions.read_interests_text(repo_root=tmp_path)


def test_prompt_files_carry_the_laws():
    from pathlib import Path
    nightly = Path("prompts/passions_nightly.txt").read_text(encoding="utf-8")
    for needle in ("<<DATE>>", "<<DAY_MATERIAL>>", "<<INTERESTS>>", "<<MADE_THING_RULE>>"):
        assert needle in nightly
    assert "most days" in nightly.lower()          # the quiet-day law
    assert "strict JSON" in nightly
    interests = Path("prompts/passions_interests.txt").read_text(encoding="utf-8")
    for needle in ("<<MODE>>", "<<CURRENT>>", "<<EVIDENCE>>"):
        assert needle in interests
    # Recognition-shaped entry law (2026-07-21 owner revision): a citable
    # hook, the became-yours turn, themes-not-only-topics — never a count.
    assert "hook" in interests.lower()
    assert "turn" in interests.lower()
    assert "theme" in interests.lower()
    assert "repeated" not in interests.lower()  # the count law is gone
    assert "fade" in interests.lower()          # honest fading (unchanged)


# ── nightly journal pass (Task 4) ───────────────────────────────────────
#
# `_gather_day_material` is monkeypatched everywhere below — the real
# reader hits ai/night_cycle_store, ai/day_gist_store and
# ai/inner_monologue_store, none of which this suite exercises directly.
# Every test forces `passions_enabled` explicitly (never the live default)
# and passes repo_root=tmp_path, so nothing ever touches identity/palli.


def _fake_material_async(text):
    """Stub for passions._gather_day_material: an async callable that always
    yields the same day-material dict, regardless of how it's called."""

    async def _material(**kwargs):
        return {"reflection": text, "gist": text, "dives": []}

    return _material


def _write_page(root, date, *, made_thing=False, body="a day, counted."):
    """A journal page straight onto disk — where only the made-thing SCAN is
    under test, never the pen."""
    from ai import passions

    jdir = root / "journal"
    jdir.mkdir(parents=True, exist_ok=True)
    text = body if not made_thing else f"{body}\n\n{passions.MADE_THING_HEADING}\nsomething small.\n"
    (jdir / f"{date}.md").write_text(text, encoding="utf-8")


@pytest.mark.asyncio
async def test_nightly_journal_earned_page_lands_via_gate(tmp_path, monkeypatch):
    from ai import passions
    (tmp_path / "INTERESTS.md").write_text("## Humidity control\nstarted with his root cellar\n", encoding="utf-8")
    monkeypatch.setattr(passions, "passions_enabled", lambda: True)
    monkeypatch.setattr(passions, "_gather_day_material", _fake_material_async("a day that moved"))

    async def author(prompt):
        return '{"earned": true, "page": "The humidity thread clicked today.", "reasoning": "it moved"}'

    async def gate_llm(prompt):
        return '{"approve": true, "reason": "faithful"}'

    res = await passions.run_nightly_journal(
        owner_id=1, date="2026-07-20", llm_call=author, gate_llm_call=gate_llm,
        now=1785000000.0, repo_root=tmp_path,
    )
    assert res.get("wrote") is True
    page = tmp_path / "journal" / "2026-07-20.md"
    assert page.is_file() and "humidity thread" in page.read_text(encoding="utf-8")
    # committed with her reasoning
    import subprocess
    msg = subprocess.run(["git", "log", "-1", "--format=%s"], cwd=tmp_path,
                         capture_output=True, text=True).stdout
    assert "it moved" in msg


@pytest.mark.asyncio
async def test_nightly_journal_flag_off_no_writes(tmp_path, monkeypatch):
    """flag-off ⇒ {"skipped": "flag_off"}, checked before anything else —
    the LLM callables raise if reached at all, and tmp_path stays empty."""
    from ai import passions
    monkeypatch.setattr(passions, "passions_enabled", lambda: False)

    async def never(prompt):
        raise AssertionError("flag-off must reject before any LLM call")

    res = await passions.run_nightly_journal(
        owner_id=1, date="2026-07-20", llm_call=never, gate_llm_call=never,
        now=1785000000.0, repo_root=tmp_path,
    )
    assert res == {"skipped": "flag_off"}
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_nightly_journal_waits_on_first_authorship(tmp_path, monkeypatch):
    """No INTERESTS.md receipt yet ⇒ {"skipped": "waiting_first_authorship"},
    also checked before the LLM."""
    from ai import passions
    monkeypatch.setattr(passions, "passions_enabled", lambda: True)

    async def never(prompt):
        raise AssertionError("must never reach the LLM without the first-authorship receipt")

    res = await passions.run_nightly_journal(
        owner_id=1, date="2026-07-20", llm_call=never, gate_llm_call=never,
        now=1785000000.0, repo_root=tmp_path,
    )
    assert res == {"skipped": "waiting_first_authorship"}
    assert not (tmp_path / "journal").exists()


@pytest.mark.asyncio
async def test_nightly_journal_quiet_day_no_page(tmp_path, monkeypatch):
    """Her own judgment that a day earned nothing ⇒ no page, no gate call,
    just the honest {"skipped": "quiet_day", "reasoning": ...}."""
    from ai import passions
    (tmp_path / "INTERESTS.md").write_text("## Humidity control\nstarted with his root cellar\n", encoding="utf-8")
    monkeypatch.setattr(passions, "passions_enabled", lambda: True)
    monkeypatch.setattr(passions, "_gather_day_material", _fake_material_async("an ordinary day"))

    async def author(prompt):
        return '{"earned": false, "reasoning": "nothing moved today"}'

    async def gate_llm(prompt):
        raise AssertionError("the gate must never be reached on a quiet day")

    res = await passions.run_nightly_journal(
        owner_id=1, date="2026-07-20", llm_call=author, gate_llm_call=gate_llm,
        now=1785000000.0, repo_root=tmp_path,
    )
    assert res == {"skipped": "quiet_day", "reasoning": "nothing moved today"}
    assert not (tmp_path / "journal").exists()


@pytest.mark.asyncio
async def test_nightly_journal_gate_reject_no_write(tmp_path, monkeypatch):
    """A page the judge writes but the gate won't approve never lands; the
    result carries gate_rejected, not wrote."""
    from ai import passions
    (tmp_path / "INTERESTS.md").write_text("## Humidity control\nstarted with his root cellar\n", encoding="utf-8")
    monkeypatch.setattr(passions, "passions_enabled", lambda: True)
    monkeypatch.setattr(passions, "_gather_day_material", _fake_material_async("a day that moved"))

    async def author(prompt):
        return '{"earned": true, "page": "a page with something invented in it", "reasoning": "it moved"}'

    async def gate_llm(prompt):
        return '{"approve": false, "reason": "invented detail"}'

    res = await passions.run_nightly_journal(
        owner_id=1, date="2026-07-20", llm_call=author, gate_llm_call=gate_llm,
        now=1785000000.0, repo_root=tmp_path,
    )
    assert res == {"gate_rejected": "invented detail"}
    assert not (tmp_path / "journal" / "2026-07-20.md").exists()


@pytest.mark.asyncio
async def test_nightly_journal_made_thing_rule_flips_with_the_week(tmp_path, monkeypatch):
    """The <<MADE_THING_RULE>> text the author sees flips on
    made_thing_used_this_week: permissive with nothing on file in the PAGE's
    ISO week, restrictive once a page carrying the heading lands in it.

    The week is read off the page DATE (2026-07-20/21/22 are all ISO W30), so
    `now` no longer decides anything here — it used to, which made this test
    silently dependent on the runner's local timezone."""
    from ai import passions
    (tmp_path / "INTERESTS.md").write_text("## Humidity control\nstarted with his root cellar\n", encoding="utf-8")
    monkeypatch.setattr(passions, "passions_enabled", lambda: True)
    monkeypatch.setattr(passions, "_gather_day_material", _fake_material_async("a quiet day"))
    now = 1785000000.0  # irrelevant to the cap now; only the page dates matter

    seen_prompts = []

    async def author(prompt):
        seen_prompts.append(prompt)
        return '{"earned": false, "reasoning": "just checking the rule text"}'

    async def gate_llm(prompt):
        raise AssertionError("quiet day — the gate must never be reached")

    # Nothing in journal/ yet this week ⇒ the permissive rule.
    res = await passions.run_nightly_journal(
        owner_id=1, date="2026-07-20", llm_call=author, gate_llm_call=gate_llm,
        now=now, repo_root=tmp_path,
    )
    assert res["skipped"] == "quiet_day"
    assert "is available this week" in seen_prompts[-1]
    assert "already made something" not in seen_prompts[-1]

    # A made thing landed in the same ISO week as the next page (2026-07-21
    # and 2026-07-22 are both W30) — written directly to disk; only
    # made_thing_used_this_week's scan is under test.
    _write_page(tmp_path, "2026-07-21", made_thing=True, body="a quieter day, still counted.")

    res2 = await passions.run_nightly_journal(
        owner_id=1, date="2026-07-22", llm_call=author, gate_llm_call=gate_llm,
        now=now, repo_root=tmp_path,
    )
    assert res2["skipped"] == "quiet_day"
    assert "already made something" in seen_prompts[-1]
    assert "is available this week" not in seen_prompts[-1]


def test_made_thing_scan_keys_off_the_pages_own_iso_week(tmp_path):
    """Review fix (2026-07-20): the scan derived its week from the TICK's
    `now`, but the page being written is dated `date` — and the night cycle
    supplies date = now − 1 day. The two diverge on exactly one night a week
    (Monday's 04:00 tick writes Sunday's page), so the cap looked in the wrong
    ISO week and any week could hold two made things.

    Timezone-robust by construction: both sides are ISO date strings, so
    nothing here depends on the local clock."""
    from ai import passions

    _write_page(tmp_path, "2026-07-22", made_thing=True)  # Wednesday, ISO 2026-W30

    # Sunday 2026-07-26 is the SAME ISO week (W30) ⇒ the cap is spent.
    assert passions.made_thing_used_this_week(tmp_path, "2026-07-26") is True
    # Monday 2026-07-27 opens a fresh ISO week (W31) ⇒ available again.
    assert passions.made_thing_used_this_week(tmp_path, "2026-07-27") is False
    # ...and the Sunday before it (W30) is still spent.
    assert passions.made_thing_used_this_week(tmp_path, "2026-07-20") is True


def test_made_thing_scan_survives_an_unreadable_page(tmp_path):
    """An unreadable page proves nothing — it must be skipped, not raised
    through. p.read_text sat OUTSIDE the try guarding strptime, so a
    UnicodeDecodeError (a ValueError, not an OSError) escaped the scan."""
    from ai import passions

    jdir = tmp_path / "journal"
    jdir.mkdir(parents=True, exist_ok=True)
    (jdir / "2026-07-21.md").write_bytes(b"a latin-1 page: caf\xe9 and \xff\xfe\n")  # invalid UTF-8

    assert passions.made_thing_used_this_week(tmp_path, "2026-07-22") is False
    # a readable made thing later that week still counts
    _write_page(tmp_path, "2026-07-23", made_thing=True)
    assert passions.made_thing_used_this_week(tmp_path, "2026-07-22") is True


@pytest.mark.asyncio
async def test_made_thing_cap_holds_on_the_monday_boundary(tmp_path, monkeypatch):
    """THE Monday-boundary hole, end to end: a made thing already on file for
    ISO 2026-W30, the page under construction dated Sunday 2026-07-26 (W30),
    and the tick's `now` at Monday 2026-07-27 04:00 (W31) — production's exact
    shape. Keying off `now` scanned W31, found nothing, and let Sunday's page
    carry a SECOND made thing for the week. It must be refused."""
    from ai import passions

    (tmp_path / "INTERESTS.md").write_text("## Humidity control\nstarted with his root cellar\n", encoding="utf-8")
    monkeypatch.setattr(passions, "passions_enabled", lambda: True)
    monkeypatch.setattr(passions, "_gather_day_material", _fake_material_async("a day that moved"))

    _write_page(tmp_path, "2026-07-22", made_thing=True)  # Wednesday, W30 — the week's one made thing
    now = datetime(2026, 7, 27, 4, 0, 0).timestamp()  # Monday 04:00, ISO W31

    seen_prompts = []

    async def author(prompt):
        seen_prompts.append(prompt)
        return '{"earned": true, "page": "something\\n\\n## A made thing\\nx", "reasoning": "r"}'

    async def gate_llm(prompt):
        raise AssertionError("the belt must refuse a second made thing without reaching the gate")

    res = await passions.run_nightly_journal(
        owner_id=1, date="2026-07-26", llm_call=author, gate_llm_call=gate_llm,
        now=now, repo_root=tmp_path,
    )
    assert res == {"gate_rejected": "made_thing_cap"}
    assert not (tmp_path / "journal" / "2026-07-26.md").exists()
    # the same `allowed` drives the prompt's rule text — it must be restrictive too
    assert "already made something" in seen_prompts[-1]
    assert "is available this week" not in seen_prompts[-1]


@pytest.mark.asyncio
async def test_unreadable_page_does_not_stall_the_night(tmp_path, monkeypatch):
    """One non-UTF-8 page used to kill the organ for the REST of that ISO
    week: the decode error escaped the scan into run_nightly_journal's outer
    handler ⇒ {"error": ...} and nothing written — and the bad page stays on
    disk, so every following night that week died identically, invisibly
    (the health line never read result["passions"] either)."""
    from ai import passions

    (tmp_path / "INTERESTS.md").write_text("## Humidity control\nstarted with his root cellar\n", encoding="utf-8")
    monkeypatch.setattr(passions, "passions_enabled", lambda: True)
    monkeypatch.setattr(passions, "_gather_day_material", _fake_material_async("a day that moved"))

    jdir = tmp_path / "journal"
    jdir.mkdir(parents=True, exist_ok=True)
    (jdir / "2026-07-20.md").write_bytes(b"a latin-1 page: caf\xe9\n")  # Monday, same ISO week

    async def author(prompt):
        return '{"earned": true, "page": "The humidity thread clicked today.", "reasoning": "it moved"}'

    async def gate_llm(prompt):
        return '{"approve": true, "reason": "faithful"}'

    res = await passions.run_nightly_journal(
        owner_id=1, date="2026-07-22", llm_call=author, gate_llm_call=gate_llm,
        now=datetime(2026, 7, 23, 4, 0, 0).timestamp(), repo_root=tmp_path,
    )
    assert res.get("wrote") is True, res
    assert (tmp_path / "journal" / "2026-07-22.md").is_file()


@pytest.mark.asyncio
async def test_made_thing_result_key_reports_only_what_landed(tmp_path, monkeypatch):
    """`made_thing` is telemetry about the page that ACTUALLY became hers, so
    it must be read off the penned slice (page[:JOURNAL_PAGE_CAP]). A made
    thing that fell off the far side of the cap never reached her repo and
    must not be counted as one — otherwise the week's cap is spent by a
    section nobody can read."""
    from ai import passions

    (tmp_path / "INTERESTS.md").write_text("## Humidity control\nstarted with his root cellar\n", encoding="utf-8")
    monkeypatch.setattr(passions, "passions_enabled", lambda: True)
    monkeypatch.setattr(passions, "_gather_day_material", _fake_material_async("a day that moved"))

    # the heading sits entirely beyond the pen's cap
    overlong = "x" * passions.JOURNAL_PAGE_CAP + f"\n\n{passions.MADE_THING_HEADING}\ny\n"

    async def author(prompt):
        return json.dumps({"earned": True, "page": overlong, "reasoning": "it moved"})

    async def gate_llm(prompt):
        return '{"approve": true, "reason": "faithful"}'

    res = await passions.run_nightly_journal(
        owner_id=1, date="2026-07-22", llm_call=author, gate_llm_call=gate_llm,
        now=datetime(2026, 7, 22, 4, 0, 0).timestamp(), repo_root=tmp_path,
    )
    assert res.get("wrote") is True, res
    landed = (tmp_path / "journal" / "2026-07-22.md").read_text(encoding="utf-8")
    assert passions.MADE_THING_HEADING not in landed  # it never made it onto the page
    assert res["made_thing"] is False  # ...so telemetry must not claim it did


@pytest.mark.asyncio
async def test_nightly_journal_llm_garbage_errors_nothing_written(tmp_path, monkeypatch):
    """An unparseable judgment call is an error result, never a crash and
    never a write."""
    from ai import passions
    (tmp_path / "INTERESTS.md").write_text("## Humidity control\nstarted with his root cellar\n", encoding="utf-8")
    monkeypatch.setattr(passions, "passions_enabled", lambda: True)
    monkeypatch.setattr(passions, "_gather_day_material", _fake_material_async("a day that moved"))

    async def author(prompt):
        return "not json at all, just noise"

    async def gate_llm(prompt):
        raise AssertionError("an unparseable judgment must never reach the gate")

    res = await passions.run_nightly_journal(
        owner_id=1, date="2026-07-20", llm_call=author, gate_llm_call=gate_llm,
        now=1785000000.0, repo_root=tmp_path,
    )
    assert res == {"error": "unparseable_judgment"}
    assert not (tmp_path / "journal").exists()


@pytest.mark.asyncio
async def test_nightly_journal_second_run_same_date_skips_before_llm(tmp_path, monkeypatch):
    """Once today's page exists, a second pass for the same date must never
    reach the LLM or the pen's immutability check — it skips on the
    page-exists read alone."""
    from ai import passions
    (tmp_path / "INTERESTS.md").write_text("## Humidity control\nstarted with his root cellar\n", encoding="utf-8")
    monkeypatch.setattr(passions, "passions_enabled", lambda: True)
    monkeypatch.setattr(passions, "_gather_day_material", _fake_material_async("a day that moved"))

    async def author(prompt):
        return '{"earned": true, "page": "The humidity thread clicked today.", "reasoning": "it moved"}'

    async def gate_llm(prompt):
        return '{"approve": true, "reason": "faithful"}'

    first = await passions.run_nightly_journal(
        owner_id=1, date="2026-07-20", llm_call=author, gate_llm_call=gate_llm,
        now=1785000000.0, repo_root=tmp_path,
    )
    assert first.get("wrote") is True

    async def never(prompt):
        raise AssertionError("page_exists must be caught before any LLM call")

    second = await passions.run_nightly_journal(
        owner_id=1, date="2026-07-20", llm_call=never, gate_llm_call=never,
        now=1785000000.0, repo_root=tmp_path,
    )
    assert second == {"skipped": "page_exists"}


@pytest.mark.asyncio
async def test_nightly_journal_made_thing_belt_refuses_without_pen(tmp_path, monkeypatch):
    """The belt deterministic (line ~169 of run_nightly_journal): when
    earned=true and the page contains ## A made thing, but a made thing
    was already used this week, return gate_rejected without calling the gate
    LLM and without writing the page."""
    from ai import passions
    (tmp_path / "INTERESTS.md").write_text("## Humidity control\nstarted with his root cellar\n", encoding="utf-8")
    monkeypatch.setattr(passions, "passions_enabled", lambda: True)
    monkeypatch.setattr(passions, "_gather_day_material", _fake_material_async("a day that moved"))

    now = 1785000000.0  # irrelevant to the cap now; only the page dates matter
    # A prior page in the same ISO week as the one being written (2026-07-21
    # and 2026-07-20 are both W30), its made thing already spent.
    _write_page(tmp_path, "2026-07-21", made_thing=True, body="a quieter day, still counted.")

    async def author(prompt):
        return '{"earned": true, "page": "something\\n\\n## A made thing\\nx", "reasoning": "r"}'

    async def gate_llm(prompt):
        raise AssertionError("the gate must never be called when the belt catches a made thing cap")

    res = await passions.run_nightly_journal(
        owner_id=1, date="2026-07-20", llm_call=author, gate_llm_call=gate_llm,
        now=now, repo_root=tmp_path,
    )
    assert res == {"gate_rejected": "made_thing_cap"}
    assert not (tmp_path / "journal" / "2026-07-20.md").exists()


# ── weekly interests reviser (Task 5) ────────────────────────────────────
#
# Marker semantics (week_key_for/_claim_week/_read_week_marker/_release_week)
# are inherited verbatim from ai.narrative_self — imported straight into
# ai.passions, never cloned; these tests exercise them through
# run_weekly_interests rather than re-testing the helpers themselves (that
# coverage lives in tests/ai/test_narrative_self.py). Every test forces
# passions_enabled explicitly, passes repo_root=tmp_path and an explicit
# state_path=tmp_path/..., so nothing ever touches identity/palli or
# data/passions/. _gather_week_evidence is monkeypatched in every test below
# except the last, which targets the evidence-reader drift risk directly.


def _fake_evidence_async(text="the week's evidence"):
    """Stub for passions._gather_week_evidence: an async callable that
    always yields the same evidence string, regardless of arguments."""

    async def _evidence(**kwargs):
        return text

    return _evidence


@pytest.mark.asyncio
async def test_weekly_interests_flag_off_no_writes(tmp_path, monkeypatch):
    from ai import passions

    monkeypatch.setattr(passions, "passions_enabled", lambda: False)

    async def never(prompt):
        raise AssertionError("flag-off must reject before any LLM call")

    state_path = tmp_path / "state" / ".state.json"
    res = await passions.run_weekly_interests(
        owner_id=1, llm_call=never, gate_llm_call=never,
        now=1785000000.0, repo_root=tmp_path, state_path=state_path,
    )
    assert res == {"skipped": "flag_off"}
    assert not state_path.exists()


@pytest.mark.asyncio
async def test_weekly_interests_waits_on_first_authorship_week_not_burned(tmp_path, monkeypatch):
    """No INTERESTS.md receipt yet ⇒ {"skipped": "waiting_first_authorship"},
    checked before the marker is ever claimed — the week must NOT be burned
    by a pass that never actually ran."""
    from ai import passions

    monkeypatch.setattr(passions, "passions_enabled", lambda: True)

    async def never(prompt):
        raise AssertionError("must never reach the LLM without the first-authorship receipt")

    state_path = tmp_path / "state" / ".state.json"
    res = await passions.run_weekly_interests(
        owner_id=1, llm_call=never, gate_llm_call=never,
        now=1785000000.0, repo_root=tmp_path, state_path=state_path,
    )
    assert res == {"skipped": "waiting_first_authorship"}
    assert not state_path.exists()  # the week was NOT burned


@pytest.mark.asyncio
async def test_weekly_interests_already_this_week_skips_before_llm(tmp_path, monkeypatch):
    from ai import passions
    from ai.narrative_self import _claim_week, week_key_for

    (tmp_path / "INTERESTS.md").write_text("## Bridges\nhow it started\n", encoding="utf-8")
    monkeypatch.setattr(passions, "passions_enabled", lambda: True)

    now = 1785000000.0
    state_path = tmp_path / "state" / ".state.json"
    assert _claim_week(state_path, week_key_for(now)) == "claimed"

    async def never(prompt):
        raise AssertionError("already_this_week must skip before any LLM call")

    res = await passions.run_weekly_interests(
        owner_id=1, llm_call=never, gate_llm_call=never,
        now=now, repo_root=tmp_path, state_path=state_path,
    )
    assert res == {"skipped": "already_this_week"}


@pytest.mark.asyncio
async def test_weekly_interests_unwritable_marker_fails_closed(tmp_path, monkeypatch):
    """Fail-closed: if the week marker cannot persist, the pass must NOT
    run (it would otherwise re-author INTERESTS.md every single night) —
    loud error, zero LLM work."""
    from ai import passions

    (tmp_path / "INTERESTS.md").write_text("## Bridges\nhow it started\n", encoding="utf-8")
    monkeypatch.setattr(passions, "passions_enabled", lambda: True)

    async def never(prompt):
        raise AssertionError("LLM reached with an unwritable marker")

    blocker = tmp_path / "blocker"
    blocker.write_text("a file where the state DIR should be", encoding="utf-8")
    res = await passions.run_weekly_interests(
        owner_id=1, llm_call=never, gate_llm_call=never,
        now=1785000000.0, repo_root=tmp_path,
        state_path=blocker / ".state.json",  # parent is a FILE ⇒ persist fails
    )
    assert res == {"error": "week_marker_unwritable"}


@pytest.mark.asyncio
async def test_weekly_interests_no_change_releases_week(tmp_path, monkeypatch):
    """A pass that authors nothing gives the week back — the marker rolls
    back to whatever it held before this claim, so a real pass can retry the
    same week later (the narrative ritual's exact law)."""
    from ai import passions
    from ai.narrative_self import _claim_week, _read_week_marker, week_key_for

    (tmp_path / "INTERESTS.md").write_text("## Bridges\nhow it started\n", encoding="utf-8")
    monkeypatch.setattr(passions, "passions_enabled", lambda: True)
    monkeypatch.setattr(passions, "_gather_week_evidence", _fake_evidence_async())

    state_path = tmp_path / "state" / ".state.json"
    now = 1785000000.0
    previous_week = week_key_for(now - 8 * 86400)  # always a different ISO week
    assert _claim_week(state_path, previous_week) == "claimed"

    async def author(prompt):
        return '{"revise": false, "reasoning": "the file already tells the truth"}'

    async def never(prompt):
        raise AssertionError("revise=false must never reach the gate")

    res = await passions.run_weekly_interests(
        owner_id=1, llm_call=author, gate_llm_call=never,
        now=now, repo_root=tmp_path, state_path=state_path,
    )
    assert res == {"skipped": "no_change", "reasoning": "the file already tells the truth"}
    assert _read_week_marker(state_path) == previous_week  # rolled back, not kept


@pytest.mark.asyncio
async def test_weekly_interests_midpass_explosion_releases_week(tmp_path, monkeypatch):
    """A transient explosion mid-pass (the canonical case: a brain outage in
    llm_call) must not burn the week — the outer-release law inherited from
    the narrative ritual: the marker rolls back to whatever it held before
    this claim, same as a pass that authors nothing."""
    from ai import passions
    from ai.narrative_self import _claim_week, _read_week_marker, week_key_for

    (tmp_path / "INTERESTS.md").write_text("## Bridges\nhow it started\n", encoding="utf-8")
    monkeypatch.setattr(passions, "passions_enabled", lambda: True)
    monkeypatch.setattr(passions, "_gather_week_evidence", _fake_evidence_async())

    state_path = tmp_path / "state" / ".state.json"
    now = 1785000000.0
    previous_week = week_key_for(now - 8 * 86400)  # always a different ISO week
    assert _claim_week(state_path, previous_week) == "claimed"

    async def author(prompt):
        raise RuntimeError("brain outage")

    async def never(prompt):
        raise AssertionError("a mid-pass explosion must never reach the gate")

    res = await passions.run_weekly_interests(
        owner_id=1, llm_call=author, gate_llm_call=never,
        now=now, repo_root=tmp_path, state_path=state_path,
    )
    assert "error" in res
    assert _read_week_marker(state_path) == previous_week  # rolled back, not kept


@pytest.mark.asyncio
async def test_weekly_interests_revise_true_writes_via_gate(tmp_path, monkeypatch):
    from ai import passions

    (tmp_path / "INTERESTS.md").write_text("## Bridges\nhow it started\n", encoding="utf-8")
    monkeypatch.setattr(passions, "passions_enabled", lambda: True)
    monkeypatch.setattr(passions, "_gather_week_evidence", _fake_evidence_async())

    async def author(prompt):
        return (
            '{"revise": true, "new_text": "## Bridges\\nhow it started\\n'
            '## Humidity control\\na new thread this week\\n", '
            '"reasoning": "cooling kept coming up"}'
        )

    async def gate_llm(prompt):
        return '{"approve": true, "reason": "faithful"}'

    res = await passions.run_weekly_interests(
        owner_id=1, llm_call=author, gate_llm_call=gate_llm,
        now=1785000000.0, repo_root=tmp_path, state_path=tmp_path / "state" / ".state.json",
    )
    assert res.get("wrote") is True
    assert "sha" in res
    assert "Humidity control" in passions.read_interests_text(repo_root=tmp_path)
    import subprocess

    msg = subprocess.run(
        ["git", "log", "-1", "--format=%s"], cwd=tmp_path, capture_output=True, text=True
    ).stdout
    assert "cooling kept coming up" in msg


@pytest.mark.asyncio
async def test_weekly_interests_gate_reject_keeps_week(tmp_path, monkeypatch):
    """Real authored work that the gate judges and rejects keeps the claim —
    the narrative ritual's same law: an attempt was judged, so the week does
    not retry tonight."""
    from ai import passions

    (tmp_path / "INTERESTS.md").write_text("## Bridges\nhow it started\n", encoding="utf-8")
    monkeypatch.setattr(passions, "passions_enabled", lambda: True)
    monkeypatch.setattr(passions, "_gather_week_evidence", _fake_evidence_async())

    async def author(prompt):
        return '{"revise": true, "new_text": "## Bridges\\nsomething invented\\n", "reasoning": "r"}'

    async def gate_llm(prompt):
        return '{"approve": false, "reason": "invented detail"}'

    state_path = tmp_path / "state" / ".state.json"
    now = 1785000000.0
    res = await passions.run_weekly_interests(
        owner_id=1, llm_call=author, gate_llm_call=gate_llm,
        now=now, repo_root=tmp_path, state_path=state_path,
    )
    assert res == {"gate_rejected": "invented detail"}
    assert "something invented" not in passions.read_interests_text(repo_root=tmp_path)

    # the claim survives: a same-week retry skips without touching the LLM
    async def never(prompt):
        raise AssertionError("the week was kept — a same-week retry must skip before the LLM")

    res2 = await passions.run_weekly_interests(
        owner_id=1, llm_call=never, gate_llm_call=never,
        now=now, repo_root=tmp_path, state_path=state_path,
    )
    assert res2 == {"skipped": "already_this_week"}


@pytest.mark.asyncio
async def test_weekly_interests_entry_count_rail_bites_before_gate_llm(tmp_path, monkeypatch):
    """The 5-entry rail lives in constitutional_gate, pre-LLM: a 6-entry
    proposal is gate_rejected without ever reaching gate_llm_call, while the
    author LLM — which is what produced the 6 entries — was reached."""
    from ai import passions

    (tmp_path / "INTERESTS.md").write_text("## Bridges\nhow it started\n", encoding="utf-8")
    monkeypatch.setattr(passions, "passions_enabled", lambda: True)
    monkeypatch.setattr(passions, "_gather_week_evidence", _fake_evidence_async())

    six_entries = "\n".join(f"## Interest {i}\nline\n" for i in range(6))
    author_calls = []

    async def author(prompt):
        author_calls.append(prompt)
        return json.dumps({"revise": True, "new_text": six_entries, "reasoning": "six threads this week"})

    async def gate_llm(prompt):
        raise AssertionError("the entry-count rail must reject before the gate LLM is ever called")

    res = await passions.run_weekly_interests(
        owner_id=1, llm_call=author, gate_llm_call=gate_llm,
        now=1785000000.0, repo_root=tmp_path, state_path=tmp_path / "state" / ".state.json",
    )
    assert len(author_calls) == 1  # the author LLM WAS reached
    assert res == {"gate_rejected": "interests_entries: 6 > 5"}
    assert "Interest 0" not in passions.read_interests_text(repo_root=tmp_path)


@pytest.mark.asyncio
async def test_gather_week_evidence_real_reader_signatures(monkeypatch):
    """Direct exercise of the real evidence-reader calls (not monkeypatched
    away, unlike every test above) — the 2026-07-19 seam map flagged two
    drifts from the brief's literal code: stake_ledger.recent_stakes takes
    days_back, not days, and Stake is a frozen dataclass (attribute access),
    not a dict. A wrong call or attribute here raises TypeError/AttributeError
    that _gather_week_evidence's per-reader except-Exception would silently
    swallow in production (an empty section, no crash) — so this proves the
    adapted calls actually produce content instead of quietly failing."""
    from ai import passions
    import ai.curiosity_research as cr
    import ai.day_gist_store as dgs
    import ai.night_cycle_store as ncs
    import ai.stake_ledger as sl
    from ai.inner_monologue_store import MonologueEntry

    monkeypatch.setattr(
        cr, "recent_research_entries",
        lambda *, days_back, limit: [
            MonologueEntry(
                timestamp=1.0, thought="root cellars are neat",
                situation_snapshot={}, tick_metadata={"topic": "humidity control"},
            )
        ],
    )
    monkeypatch.setattr(
        dgs, "read_recent",
        lambda *, days_back: [dgs.DayGistEntry(day="2026-07-18", text="fixed the pump")],
    )
    monkeypatch.setattr(
        ncs, "read_recent",
        lambda *, limit: [ncs.NightCycleEntry(date="2026-07-18", text="satisfied, quietly")],
    )

    async def fake_recent_stakes(*, days_back):
        assert days_back == 30
        return [
            sl.Stake(
                id=1, kind="wonder", stake="whether the check valve will hold",
                status="open", resolution="", horizon_at=None,
                source_channel_id="c", last_surfaced_at=None,
                created_at=1.0, updated_at=1.0, resolved_at=None,
            ),
            sl.Stake(
                id=2, kind="hope", stake="a calmer week",
                status="open", resolution="", horizon_at=None,
                source_channel_id="c", last_surfaced_at=None,
                created_at=1.0, updated_at=1.0, resolved_at=None,
            ),
        ]

    monkeypatch.setattr(sl, "recent_stakes", fake_recent_stakes)

    evidence = await passions._gather_week_evidence(owner_id=1, now=1785000000.0)
    assert "humidity control" in evidence and "root cellars are neat" in evidence
    assert "fixed the pump" in evidence
    assert "satisfied, quietly" in evidence
    assert "whether the check valve will hold" in evidence  # kind=wonder included
    assert "a calmer week" not in evidence  # kind=hope excluded (not wonder/taste)


@pytest.mark.asyncio
async def test_gather_week_evidence_keeps_stakes_on_a_rich_week(monkeypatch):
    """Review fix (2026-07-20): sections were appended dives → gists →
    reflections → stakes and the JOINED string truncated at 8k, so on an
    evidence-rich week the wonder/taste stakes — the single strongest signal
    for the prompt's "an interest ENTERS only on repeated genuine contact"
    law — were silently dropped WHOLE.

    Per-section budgets (ai.narrative_self._SECTION_BUDGETS_WEEKLY's
    precedent) plus stakes first: every section survives a fat week, and any
    overflow now clips within a section instead of deleting the last one."""
    from ai import passions
    import ai.curiosity_research as cr
    import ai.day_gist_store as dgs
    import ai.night_cycle_store as ncs
    import ai.stake_ledger as sl
    from ai.inner_monologue_store import MonologueEntry

    fat = "y" * 2000  # a single week's worth of very talkative sources
    monkeypatch.setattr(
        cr, "recent_research_entries",
        lambda *, days_back, limit: [
            MonologueEntry(
                timestamp=float(i), thought=f"dive {i} {fat}",
                situation_snapshot={}, tick_metadata={"topic": "humidity control"},
            )
            for i in range(10)
        ],
    )
    monkeypatch.setattr(
        dgs, "read_recent",
        lambda *, days_back: [
            dgs.DayGistEntry(day=f"2026-07-1{i}", text=f"gist {i} {fat}") for i in range(7)
        ],
    )
    monkeypatch.setattr(
        ncs, "read_recent",
        lambda *, limit: [
            ncs.NightCycleEntry(date=f"2026-07-1{i}", text=f"reflection {i} {fat}") for i in range(7)
        ],
    )

    async def fake_recent_stakes(**kwargs):
        return [
            sl.Stake(
                id=1, kind="wonder", stake="whether the check valve will hold",
                status="open", resolution="", horizon_at=None,
                source_channel_id="c", last_surfaced_at=None,
                created_at=1.0, updated_at=1.0, resolved_at=None,
            ),
        ]

    monkeypatch.setattr(sl, "recent_stakes", fake_recent_stakes)

    evidence = await passions._gather_week_evidence(owner_id=1, now=1785000000.0)

    # the strongest signal survives a fat week
    assert "whether the check valve will hold" in evidence
    # ...and so does every other section — none is deleted whole
    assert "dive 0" in evidence
    assert "gist 0" in evidence
    assert "reflection 0" in evidence
    assert len(evidence) <= 8000


# ── night-cycle composition (Task 6) ─────────────────────────────────────
#
# run_passions_step is the night cycle's one call into this organ: journal,
# then interests, both through the same author lane (passions_author_llm_call,
# cloned from narrative's reflection-lane seam) and the shared narrative gate
# (narrative_gate_llm_call, imported not cloned — one gate for both organs'
# revisions). Both runners are monkeypatched away here; their own behavior is
# covered above and in test_narrative_self.py.


@pytest.mark.asyncio
async def test_run_passions_step_composes_journal_and_interests(monkeypatch):
    """Both runners are called with the wired lanes, and their results land
    under "journal"/"interests" respectively."""
    from ai import passions
    from ai.narrative_self import narrative_gate_llm_call

    calls = []

    async def fake_journal(*, owner_id, date, llm_call, gate_llm_call, now):
        calls.append(("journal", owner_id, date, now))
        assert llm_call is passions.passions_author_llm_call
        assert gate_llm_call is narrative_gate_llm_call
        return {"wrote": True, "sha": "abc123"}

    async def fake_interests(*, owner_id, llm_call, gate_llm_call, now):
        calls.append(("interests", owner_id, now))
        assert llm_call is passions.passions_author_llm_call
        assert gate_llm_call is narrative_gate_llm_call
        return {"skipped": "already_this_week"}

    monkeypatch.setattr(passions, "run_nightly_journal", fake_journal)
    monkeypatch.setattr(passions, "run_weekly_interests", fake_interests)

    res = await passions.run_passions_step(owner_id=1, date="2026-07-20", now=1785000000.0)

    assert res == {
        "journal": {"wrote": True, "sha": "abc123"},
        "interests": {"skipped": "already_this_week"},
    }
    assert calls == [
        ("journal", 1, "2026-07-20", 1785000000.0),
        ("interests", 1, 1785000000.0),
    ]


@pytest.mark.asyncio
async def test_run_passions_step_flag_off_both_skip(monkeypatch):
    """End-to-end (no monkeypatched runners): flag off ⇒ both keys carry the
    real runners' own {"skipped": "flag_off"} — the composition adds nothing
    of its own to hide behind."""
    from ai import passions

    monkeypatch.setattr(passions, "passions_enabled", lambda: False)

    res = await passions.run_passions_step(owner_id=1, date="2026-07-20", now=1785000000.0)
    assert res == {
        "journal": {"skipped": "flag_off"},
        "interests": {"skipped": "flag_off"},
    }


# ── the Monday defect (2026-08-22): size_cap rejections every week since 08-03 ─
# The prompt states the page's char cap; the model overshoots anyway (models
# cannot count characters). The rail stays exactly as it is; the pass gets
# ONE retry with the overage fed back — the LLM decides what to cut.

def _oversize_over_cap(over: int = 500) -> int:
    """`over` characters past whatever the live ceiling is.

    Derived, never hardcoded: when the caps went 2,400 → 3,000 on 2026-09-22
    this file's fixed 2,900 quietly fell UNDER the rail and the test stopped
    exercising anything while still passing its own name.
    """
    from ai.narrative_self import doc_size_cap
    return doc_size_cap("INTERESTS.md") + over


def _oversize_interests(n_chars: int | None = None) -> str:
    n_chars = _oversize_over_cap() if n_chars is None else n_chars
    body = "## Bridges\n" + ("how it started and where it is pulling me, in my own words. " * 80)
    return body[:n_chars]


@pytest.mark.asyncio
async def test_weekly_interests_size_cap_retries_once_with_the_overage_fed_back(tmp_path, monkeypatch):
    from ai import passions

    (tmp_path / "INTERESTS.md").write_text("## Bridges\nhow it started\n", encoding="utf-8")
    monkeypatch.setattr(passions, "passions_enabled", lambda: True)
    monkeypatch.setattr(passions, "_gather_week_evidence", _fake_evidence_async())
    prompts = []

    async def author(prompt):
        prompts.append(prompt)
        if len(prompts) == 1:
            return '{"revise": true, "new_text": %s, "reasoning": "r"}' % __import__("json").dumps(_oversize_interests())
        return '{"revise": true, "new_text": "## Bridges\\nhow it started, and now the humidity thread.\\n", "reasoning": "tightened"}'

    gate_calls = []

    async def gate_llm(prompt):
        gate_calls.append(prompt)
        return '{"approve": true, "reason": "faithful"}'

    state_path = tmp_path / "state" / ".state.json"
    res = await passions.run_weekly_interests(
        owner_id=1, llm_call=author, gate_llm_call=gate_llm,
        now=1785000000.0, repo_root=tmp_path, state_path=state_path,
    )
    assert res.get("wrote") is True and res.get("size_cap_retry") is True
    from ai.narrative_self import doc_size_cap as _cap
    _c = _cap("INTERESTS.md")
    assert len(prompts) == 2
    assert f"{_oversize_over_cap():,}" in prompts[1]   # what she wrote
    assert f"{_c:,}" in prompts[1]                      # the ceiling she is judged by
    assert "500" in prompts[1]                          # the overage
    assert len(gate_calls) == 1   # the size rail is code — the gate LLM saw only the fitting draft
    assert "humidity thread" in passions.read_interests_text(repo_root=tmp_path)


@pytest.mark.asyncio
async def test_weekly_interests_size_cap_twice_is_rejected_as_today(tmp_path, monkeypatch):
    from ai import passions

    (tmp_path / "INTERESTS.md").write_text("## Bridges\nhow it started\n", encoding="utf-8")
    monkeypatch.setattr(passions, "passions_enabled", lambda: True)
    monkeypatch.setattr(passions, "_gather_week_evidence", _fake_evidence_async())
    calls = []

    async def author(prompt):
        calls.append(prompt)
        return '{"revise": true, "new_text": %s, "reasoning": "r"}' % __import__("json").dumps(_oversize_interests(_oversize_over_cap(300)))

    async def gate_llm(prompt):
        raise AssertionError("the size rail is code — the gate LLM must not be called")

    res = await passions.run_weekly_interests(
        owner_id=1, llm_call=author, gate_llm_call=gate_llm,
        now=1785000000.0, repo_root=tmp_path, state_path=tmp_path / "state" / ".state.json",
    )
    assert str(res.get("gate_rejected", "")).startswith("size_cap:")
    assert len(calls) == 2   # one retry, never a loop
