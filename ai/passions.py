"""Passions & the Journal — Selfhood organ 3 (2026-07-19 spec).

Nightly: ONE LLM judgment — did today earn a journal page? — and, on yes,
the page itself, written through the constitutional gate into her repo
(create-only). Weekly (ISO-claimed): she revises INTERESTS.md from the
week's evidence. Both wait on the first-authorship receipt (INTERESTS.md
exists — only scripts/passions_first_authorship.py --apply creates it).
Rides NightCycleEngine._run_once as step 4; never raises.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from ai.narrative_self import (
    doc_size_cap,
    INTERESTS_DOC,
    Revision,
    _claim_week,
    _clip_block,
    _read_week_marker,
    _release_week,
    commit_revision,
    constitutional_gate,
    identity_palli_root,
    read_laws_text,
    strip_frontmatter,
    week_key_for,
)
from core.turn.companion_flags import companion_settings

logger = logging.getLogger(__name__)
essential_logger = logging.getLogger("palli.essential")

PASSIONS_STATE = Path("data/passions/.state.json")
JOURNAL_PAGE_CAP = 2400
MADE_THING_HEADING = "## A made thing"


def passions_enabled() -> bool:
    """THE gate (ships-OFF idiom): unbooted ⇒ OFF; requires night_cycle —
    the writers ride its tick, so passions-without-night is half-on, and
    half-on reads as OFF (the narrative_self_enabled precedent)."""
    comp = companion_settings()
    return (
        comp is not None
        and bool(getattr(comp, "passions", False))
        and bool(getattr(comp, "night_cycle", False))
    )


# Alias for gate-logic tests to restore the real implementation when the
# autouse conftest guard patches passions_enabled off.
passions_enabled_impl = passions_enabled


def first_authorship_done(repo_root: Optional[Path] = None) -> bool:
    """The receipt: INTERESTS.md exists in her repo. Only the apply driver
    creates it, so both writers skip (loudly) until the owner has run the
    ritual. No process-global latch — a file check is cheap and un-cached
    truth (the narrative latch needed a battery-reset fix; not repeated)."""
    root = Path(repo_root) if repo_root is not None else identity_palli_root()
    return (root / INTERESTS_DOC).is_file()


def read_interests_text(repo_root: Optional[Path] = None) -> str:
    root = Path(repo_root) if repo_root is not None else identity_palli_root()
    try:
        return strip_frontmatter((root / INTERESTS_DOC).read_text(encoding="utf-8"))
    except OSError:
        return ""


def read_interests_headings(repo_root: Optional[Path] = None) -> list[str]:
    """Her standing interests, heading text only (2026-07-20, Task 7) — for
    the monologue tick's guidance, which wants names to point downtime
    reading at, not the full file (that's read_interests_text, for the
    nightly author). Parses '## ' headings, first line each, prefix
    stripped, capped at 5. Riding read_interests_text's own OSError floor
    means this never raises either: missing/unreadable ⇒ []."""
    headings: list[str] = []
    for line in read_interests_text(repo_root=repo_root).splitlines():
        if line.startswith("## "):
            headings.append(line[3:].strip())
        if len(headings) >= 5:
            break
    return headings


def _week_key_of_page(page_date: str) -> str:
    """ISO week of a PAGE's date string (YYYY-MM-DD). Deliberately NOT derived
    from a timestamp: the tick's clock and the page's date are different days
    (see made_thing_used_this_week)."""
    iso = datetime.strptime(page_date, "%Y-%m-%d").isocalendar()
    return f"{iso[0]}-W{iso[1]:02d}"


def made_thing_used_this_week(root: Path, page_date: str) -> bool:
    """Deterministic: scan the ISO week OF THE PAGE BEING WRITTEN for the
    heading. Derived from files — no extra state to poison (the W29 lesson's
    cousin).

    The week comes from ``page_date``, never from the tick's ``now`` (review
    fix 2026-07-20). The night cycle dates a page ``now − 1 day``
    (NightCycleEngine._reflection_date), so on the first night tick of an ISO
    week (06:30 since 2026-10-05; 04:00 before) the two sit in different
    weeks — Monday's tick is W31 while the page it writes is Sunday's, W30.
    Keying off ``now`` scanned the wrong week,
    found nothing, and let Sunday's page carry a second made thing: a cap
    specified as once-per-ISO-week that any week could pass twice.

    An unreadable page proves nothing and must not stall the organ: a
    UnicodeDecodeError is a ValueError, not an OSError, so before the guard
    below one non-UTF-8 page escaped into run_nightly_journal's outer handler
    and killed EVERY night for the rest of that week — the bad page stays on
    disk, so the failure repeated, silently."""
    week = _week_key_of_page(page_date)
    jdir = Path(root) / "journal"
    if not jdir.is_dir():
        return False
    for p in sorted(jdir.glob("????-??-??.md")):
        try:
            iso = datetime.strptime(p.stem, "%Y-%m-%d").isocalendar()
        except ValueError:
            continue
        if f"{iso[0]}-W{iso[1]:02d}" != week:
            continue
        try:
            text = p.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue  # unreadable ⇒ no made thing found HERE; keep scanning
        if MADE_THING_HEADING in text:
            return True
    return False


async def _gather_day_material(*, owner_id: int, date: str) -> Dict[str, Any]:
    """Cheap reads only — the night already paid for the expensive work."""
    out: Dict[str, Any] = {"reflection": "", "gist": "", "dives": []}
    try:
        from ai import night_cycle_store
        for e in night_cycle_store.read_recent(limit=3):
            if getattr(e, "date", "") == date:
                out["reflection"] = e.text
    except Exception as exc:
        logger.debug("passions: reflection read failed: %s", exc)
    try:
        from ai import day_gist_store
        gist_entry = day_gist_store.read_day_gist(day=date)
        out["gist"] = gist_entry.text if gist_entry is not None else ""
    except Exception as exc:
        logger.debug("passions: gist read failed: %s", exc)
    try:
        from ai.inner_monologue_store import read_entries_for_day
        out["dives"] = [
            e.thought for e in read_entries_for_day(day=date, substantive_only=True)
            if (e.tick_metadata or {}).get("entry_kind") == "curiosity_research"
        ]
    except Exception as exc:
        logger.debug("passions: dive read failed: %s", exc)
    return out


async def run_nightly_journal(
    *, owner_id: int, date: str, llm_call, gate_llm_call,
    now: float, repo_root: Optional[Path] = None,
) -> Dict[str, Any]:
    """Never raises (except CancelledError). One judgment+page call; page
    lands through gate + pen, create-only, for THIS date alone."""
    try:
        if not passions_enabled():
            return {"skipped": "flag_off"}
        root = Path(repo_root) if repo_root is not None else identity_palli_root()
        if not first_authorship_done(repo_root=root):
            logger.warning("passions: journal waiting on first authorship")
            return {"skipped": "waiting_first_authorship"}
        doc = f"journal/{date}.md"
        if (root / doc).is_file():
            return {"skipped": "page_exists"}
        material = await _gather_day_material(owner_id=owner_id, date=date)
        # Corrections of record (2026-08-17): the day's corrections ride the
        # page's material FIRST — a day a correction was made always earns
        # its page (the 08-14 correction day got none; the false line then
        # outweighed the truth in her weekly). The block carries the law.
        corrections_block = ""
        try:
            from ai.corrections_store import render_corrections_block

            corrections_block = render_corrections_block(budget=900, day=date)
        except Exception as exc:
            logger.debug("passions: corrections unavailable: %s", exc)
        if not any([material["reflection"], material["gist"], material["dives"], corrections_block]):
            return {"skipped": "no_material"}

        from ai.prompt_loader import prompt_loader
        template = prompt_loader.load_prompt("passions_nightly")
        if not template:
            return {"error": "prompt_missing"}
        # The page's OWN week, not the tick's — they differ on the first tick
        # of every ISO week, and this is a per-week cap on pages.
        allowed = not made_thing_used_this_week(root, date)
        made_rule = (
            "A made thing is available this week, if the day genuinely produced one."
            if allowed else
            "You have already made something this week — no made thing tonight, whatever the day was."
        )
        day_block = "\n\n".join(filter(None, [
            corrections_block,
            material["reflection"] and f"Last night's reflection:\n{material['reflection']}",
            material["gist"] and f"The day's gist:\n{material['gist']}",
            material["dives"] and "Your research notes today:\n" + "\n".join(f"- {d}" for d in material["dives"]),
        ]))
        prompt = (
            template.replace("<<DATE>>", date)
            .replace("<<DAY_MATERIAL>>", day_block)
            .replace("<<INTERESTS>>", read_interests_text(repo_root=root) or "(no interests file content)")
            .replace("<<MADE_THING_RULE>>", made_rule)
        )
        raw = await llm_call(prompt)
        from core.llm_json import safe_parse_json_object
        data = safe_parse_json_object(raw or "")
        if not isinstance(data, dict) or not isinstance(data.get("earned"), bool):
            return {"error": "unparseable_judgment"}
        if not data["earned"]:
            return {"skipped": "quiet_day", "reasoning": str(data.get("reasoning") or "")}
        page = str(data.get("page") or "").strip()
        if not page:
            return {"error": "earned_but_empty_page"}
        if not allowed and MADE_THING_HEADING in page:
            return {"gate_rejected": "made_thing_cap"}  # belt over the prompt's suspenders
        penned = page[:JOURNAL_PAGE_CAP]
        revision = Revision(doc=doc, new_text=penned,
                            reasoning=str(data.get("reasoning") or f"journal {date}"))
        verdict = await constitutional_gate(
            revision=revision, laws_text=read_laws_text(),
            current_text="", llm_call=gate_llm_call,
            evidence_text=day_block, expected_journal_date=date,
        )
        if verdict.error:
            return {"error": f"gate_error: {verdict.reason}"}
        if not verdict.approve:
            logger.warning("passions: journal page rejected: %s", verdict.reason)
            return {"gate_rejected": verdict.reason}
        sha = commit_revision(revision=revision, repo_root=root)
        essential_logger.info("passions: journal page written for %s (%s)", date, sha[:8])
        # Telemetry about the page that actually BECAME hers: read off the
        # penned slice, not the full authored text — a made thing that fell
        # off the far side of the cap never landed (review fix 2026-07-20).
        return {"wrote": True, "sha": sha, "made_thing": MADE_THING_HEADING in penned}
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.warning("passions: nightly journal failed: %s", exc)
        return {"error": str(exc)}


async def run_weekly_interests(
    *, owner_id: int, llm_call, gate_llm_call, now: float,
    repo_root: Optional[Path] = None, state_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """At most one pass per ISO week (claim-before-work, fail-closed on an
    unwritable marker, released whenever the pass lands nothing — no
    authored change, or a mid-pass explosion including a failed commit).
    Never raises (except CancelledError)."""
    try:
        if not passions_enabled():
            return {"skipped": "flag_off"}
        root = Path(repo_root) if repo_root is not None else identity_palli_root()
        if not first_authorship_done(repo_root=root):
            logger.warning("passions: weekly interests waiting on first authorship")
            return {"skipped": "waiting_first_authorship"}
        spath = Path(state_path) if state_path is not None else PASSIONS_STATE
        week = week_key_for(now)
        previous = _read_week_marker(spath)
        claim = _claim_week(spath, week)
        if claim == "already":
            return {"skipped": "already_this_week"}
        if claim == "unwritable":
            return {"error": "week_marker_unwritable"}

        # From here the claim is live: a transient mid-pass explosion (LLM
        # outage, a reader raising, commit_revision's pen/git failing) must
        # give the week back so tomorrow's tick retries — the narrative
        # ritual's exact outer-release law (narrative_self.run_weekly_ritual).
        try:
            current = read_interests_text(repo_root=root)
            evidence = await _gather_week_evidence(owner_id=owner_id, now=now)
            from ai.prompt_loader import prompt_loader
            template = prompt_loader.load_prompt("passions_interests")
            if not template:
                _release_week(spath, week, previous)
                return {"error": "prompt_missing"}
            prompt = (
                template.replace("<<MODE>>", "weekly revision — revise, don't rewrite")
                .replace("<<CURRENT>>", current or "(empty)")
                .replace("<<EVIDENCE>>", evidence or "(a thin week)")
            )
            raw = await llm_call(prompt)
            from core.llm_json import safe_parse_json_object
            data = safe_parse_json_object(raw or "")
            if not isinstance(data, dict) or not isinstance(data.get("revise"), bool):
                _release_week(spath, week, previous)
                return {"error": "unparseable_revision"}
            if not data["revise"]:
                _release_week(spath, week, previous)
                return {"skipped": "no_change", "reasoning": str(data.get("reasoning") or "")}
            revision = Revision(
                doc=INTERESTS_DOC,
                new_text=str(data.get("new_text") or ""),
                reasoning=str(data.get("reasoning") or "weekly interests revision"),
            )
            verdict = await constitutional_gate(
                revision=revision, laws_text=read_laws_text(),
                current_text=current, llm_call=gate_llm_call, evidence_text=evidence,
            )
            # The Monday defect (2026-08-22): every Monday since 08-03 her
            # revision was thrown away by the size rail — the prompt states
            # the page's stated char cap, and the model overshoots anyway (models
            # cannot count characters). The rail stays exactly as it is; the
            # pass gets ONE retry with the overage fed back, and the LLM
            # decides what to cut. A second overshoot is rejected as before.
            size_cap_retry = False
            if (not verdict.error and not verdict.approve
                    and str(verdict.reason or "").startswith("size_cap:")):
                cap = doc_size_cap(INTERESTS_DOC)
                n = len(revision.new_text)
                logger.warning("passions: interests revision over the size cap (%d > %d); retrying once with the overage fed back", n, cap)
                retry_prompt = (
                    prompt
                    + f"\n\nYOUR PREVIOUS DRAFT WAS {n:,} CHARACTERS; THE FILE HOLDS {cap:,}. "
                    + f"You are over by {n - cap:,} characters. Tighten the same revision to fit — "
                    + "cut the least-alive lines, keep every entry's hook and where it is pulling you. "
                    + "Return the same strict JSON."
                )
                raw2 = await llm_call(retry_prompt)
                data2 = safe_parse_json_object(raw2 or "")
                if isinstance(data2, dict) and data2.get("revise") is True and str(data2.get("new_text") or "").strip():
                    size_cap_retry = True
                    revision = Revision(
                        doc=INTERESTS_DOC,
                        new_text=str(data2.get("new_text") or ""),
                        reasoning=str(data2.get("reasoning") or revision.reasoning),
                    )
                    verdict = await constitutional_gate(
                        revision=revision, laws_text=read_laws_text(),
                        current_text=current, llm_call=gate_llm_call, evidence_text=evidence,
                    )
            if verdict.error:
                _release_week(spath, week, previous)
                return {"error": f"gate_error: {verdict.reason}"}
            if not verdict.approve:
                # Real work happened (an authored attempt was judged) — keep
                # the claim, same law as the narrative ritual.
                logger.warning("passions: interests revision rejected: %s", verdict.reason)
                return {"gate_rejected": verdict.reason}
            sha = commit_revision(revision=revision, repo_root=root)
            essential_logger.info("passions: INTERESTS.md revised (%s)%s", sha[:8], " after a size-cap retry" if size_cap_retry else "")
            out = {"wrote": True, "sha": sha}
            if size_cap_retry:
                out["size_cap_retry"] = True
            return out
        except asyncio.CancelledError:
            # Mirrors narrative_self.run_weekly_ritual exactly: post-claim
            # cancellation propagates WITHOUT releasing the week (only a
            # generic Exception below releases it).
            raise
        except Exception as exc:
            logger.warning("passions: weekly interests mid-pass explosion: %s", exc)
            _release_week(spath, week, previous)
            return {"error": str(exc)}
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.warning("passions: weekly interests failed: %s", exc)
        return {"error": str(exc)}


# 2026-08 (wanting): raised in lockstep with the new "quests" section so the
# sum-below-cap intent below still holds (budgets sum 9,600; 200 spare).
WEEK_EVIDENCE_CAP = 9800
# Per-section budgets, ai.narrative_self._SECTION_BUDGETS_WEEKLY's precedent.
# They sum below the cap, leaving headroom for the joining newlines, so the
# overall cap below can never bite and no section can be deleted whole.
_WEEK_SECTION_BUDGETS = {"stakes": 1800, "notes": 1200, "quests": 600, "dives": 2600, "gists": 1900, "reflections": 1500}


async def _gather_week_evidence(*, owner_id: int, now: float) -> str:
    """The lived record the reviser writes from, capped ~8k chars: wonder/taste
    stakes, dive notes (with topics), day-gists, night reflections.

    Stakes come FIRST and every section is budgeted (review fix 2026-07-20).
    The old code appended stakes LAST and truncated the joined string, so on
    an evidence-rich week the wonders and tastes — the strongest signal for
    the prompt's entry law (recognition-shaped since the 2026-07-21 owner
    revision: a citable hook, never frequency) — vanished entirely, silently.

    The windows differ ON PURPOSE and the labels say so: the day-shaped
    sources are this week, while stakes reach back a month because a wonder
    held across weeks is exactly the kind of alive, citable hook the law asks
    her to point at (and recent_stakes windows on updated_at, so a 7-day
    window would drop exactly the long-standing wonders that matter most).

    Two adaptations from the brief's literal code (2026-07-19 signature
    check): stake_ledger.recent_stakes's real kwarg is days_back, not days;
    and Stake is a frozen dataclass, not a dict, so its fields are read by
    attribute (s.kind, s.stake), not s.get("kind")/s["kind"]."""
    sections: Dict[str, str] = {}
    try:
        from ai.stake_ledger import recent_stakes
        stakes = await recent_stakes(days_back=30)
        wt = [s for s in stakes if s.kind in ("wonder", "taste")]
        if wt:
            sections["stakes"] = "Wonders and tastes you've been holding (the past month):\n" + "\n".join(
                f"- [{s.kind}] {s.stake}" for s in wt
            )
    except Exception as exc:
        logger.debug("passions: stakes evidence failed: %s", exc)
    # The pen (2026-07-27): right after stakes, so stakes-first holds.
    try:
        from ai.narrative_self import _notes_section

        notes_block = _notes_section(budget=_WEEK_SECTION_BUDGETS["notes"], days=7)
        if notes_block:
            sections["notes"] = (
                "Things you wrote down for yourself this week:\n" + notes_block
            )
    except Exception as exc:
        logger.debug("passions: notes evidence failed: %s", exc)
    # The wishbook (step 20): what she's carrying is interests evidence.
    try:
        from ai.narrative_self import _quests_section

        quests_block = _quests_section(budget=_WEEK_SECTION_BUDGETS["quests"])
        if quests_block:
            sections["quests"] = (
                "Wishes you carried this week:\n" + quests_block
            )
    except Exception as exc:
        logger.debug("passions: quests evidence failed: %s", exc)
    try:
        from ai.curiosity_research import recent_research_entries
        dives = recent_research_entries(days_back=7, limit=10)
        if dives:
            sections["dives"] = "Your research notes this week:\n" + "\n".join(
                f"- [{(e.tick_metadata or {}).get('topic', '?')}] {e.thought}" for e in dives
            )
    except Exception as exc:
        logger.debug("passions: dives evidence failed: %s", exc)
    try:
        from ai import day_gist_store
        gists = day_gist_store.read_recent(days_back=7)
        if gists:
            sections["gists"] = "Your days this week:\n" + "\n".join(
                f"- [{g.day}] {g.text}" for g in gists
            )
    except Exception as exc:
        logger.debug("passions: gists evidence failed: %s", exc)
    try:
        from ai import night_cycle_store
        refl = night_cycle_store.read_recent(limit=7)
        if refl:
            sections["reflections"] = "Your reflections this week:\n" + "\n".join(
                f"- [{e.date}] {e.text}" for e in refl
            )
    except Exception as exc:
        logger.debug("passions: reflections evidence failed: %s", exc)
    parts = [
        _clip_block(sections[key], _WEEK_SECTION_BUDGETS[key])
        for key in ("stakes", "notes", "quests", "dives", "gists", "reflections")
        if sections.get(key)
    ]
    # Corrections of record (2026-08-17) — FIRST, outside the per-section
    # budgets, own cap: a correction outranks any gist/reflection below it.
    try:
        from ai.corrections_store import render_corrections_block

        block = render_corrections_block(budget=1000)
        if block:
            parts.insert(0, block)
    except Exception as exc:
        logger.debug("passions: corrections unavailable: %s", exc)
    return "\n\n".join(parts)[:WEEK_EVIDENCE_CAP + 1000]


# ── the real LLM deps the night cycle injects (Task 6) ───────────────────
#
# Cloned from ai.narrative_self's _make_llm_call seam (same reflective
# register the night's reflection/day-gists/narrative already use — owner
# decision #2, carried forward). max_tokens=4000, not a smaller ceiling: the
# reflection lane's model (deepseek-v4-flash) spends reasoning tokens
# against this same budget and starved at a lower one during the narrative
# build (87cc2d6). The gate is narrative's own analysis-lane call — imported
# below in run_passions_step, never cloned: one gate for both organs.


def _make_llm_call(*, task: str, fallback_task: str, temperature: float, max_tokens: int, context: str):
    async def _call(prompt: str) -> str:
        from ai.llm_client import llm
        from ai.llm_routing import get_task_model

        model = get_task_model(task, fallback=get_task_model(fallback_task))
        response = await asyncio.wait_for(
            llm.chat_completion(
                [{"role": "user", "content": prompt}],
                model=model,
                temperature=temperature,
                max_tokens=max_tokens,
                log_context=context,
            ),
            timeout=180,  # patient-lane envelope: 2x60s ceiling + headroom (2026-08-09)
        )
        return response or ""

    return _call


passions_author_llm_call = _make_llm_call(
    task="reflection", fallback_task="memory", temperature=0.7, max_tokens=4000,
    context="passions_author",
)


async def run_passions_step(*, owner_id: int, date: str, now: float) -> Dict[str, Any]:
    """The night cycle's one call into this organ: journal, then interests,
    both riding the author lane above and the shared narrative gate. Both
    runners are already never-raise (except CancelledError) on their own, so
    no try/except here — night_cycle.py wraps this call for defense in
    depth, the same shape as its consolidation/narrative attaches."""
    from ai.narrative_self import narrative_gate_llm_call
    out: Dict[str, Any] = {}
    out["journal"] = await run_nightly_journal(
        owner_id=owner_id, date=date,
        llm_call=passions_author_llm_call, gate_llm_call=narrative_gate_llm_call,
        now=now,
    )
    out["interests"] = await run_weekly_interests(
        owner_id=owner_id,
        llm_call=passions_author_llm_call, gate_llm_call=narrative_gate_llm_call,
        now=now,
    )
    return out
