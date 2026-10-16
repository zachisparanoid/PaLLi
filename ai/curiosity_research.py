"""Downtime curiosity research — a companion faculty (2026-07-01).

The inner monologue may emit an optional ``curiosity``: a topic PaLLi
wants to go read about for ITSELF during downtime — possibly never
discussed with the owner at all. This module runs that research in the
background:

  1. search the live web via the Exa client (no summarizer pipeline —
     one LLM call total);
  2. one LLM call in PaLLi's own voice: what did I actually learn, and
     is it genuinely worth sharing unprompted?
  3. write the note back into the monologue stream
     (``entry_kind="curiosity_research"``) so it joins PaLLi's real
     memory: recent-thoughts context, weekly digests, the works;
  4. when worth sharing, the entry carries an observation
     ``proactive_candidate`` — the candidate rail ingests it into the
     open-threads queue and the arbiter picks the moment (step 19: the
     old decide_surface lane is deleted). PaLLi's own rabbit hole
     becomes first contact with zero extra plumbing.

Gates: ``companion.curiosity_research`` faculty flag,
``features.web_search``, and a rolling daily cap. Strictly best-effort —
failures are logged and dropped; the monologue tick never blocks on it.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from pathlib import Path
from typing import Any, Optional

from ai.inner_monologue_store import MonologueEntry, read_entries, write_entry
from ai.llm_client import llm
from ai.llm_routing import get_task_model
from ai.prompt_loader import prompt_loader
from core.config import nas_root
from core.llm_json import safe_parse_json_object
from core.prompt_format import format_prompt
from core.setup_mode import lane_idle
from core.turn.companion_flags import companion_settings, faculty_enabled

logger = logging.getLogger(__name__)

_DEFAULT_RESEARCH_PER_DAY = 3
_RESEARCH_WINDOW_SECONDS = 24 * 3600
# Multi-round dives (2026-07-21, owner free-reign request): one sitting may
# follow its own question up to this many search rounds before the single
# synthesized note. Deterministic bound; the "worth another look?" judgment
# itself is the LLM's (§0 rule). Every round spends one daily-budget unit.
_MAX_DIVE_ROUNDS = 3
_FOLLOWUP_NUM_RESULTS = 3

# §0.10 — the read-only NAS dive (step 20). Bounded on every axis; the
# walk NEVER writes and never follows symlinks.
_NAS_MAX_ENTRIES = 4000
_NAS_MAX_DEPTH = 4
_NAS_MAX_RESULTS = 5
_NAS_TEXT_EXT = {".txt", ".md", ".log", ".json", ".csv", ".py", ".html"}


def _nas_dive_root() -> str:
    """Where the dive walks, or "" when there is no NAS.

    ``CURIOSITY_NAS_ROOT`` stays as this feature's own override (it predates
    the shared knob); otherwise the house NAS, which is ONE knob for the
    whole app (PALLI_NAS_ROOT -> config.NAS_ROOT, step 28 task 2). Read at
    call time so a test or a re-export moves it.
    """
    own = os.environ.get("CURIOSITY_NAS_ROOT")
    return (own if own is not None else nas_root()).strip()


def research_daily_cap() -> int:
    """The owner's knob (companion.research_daily_cap), defaulting to the
    conservative 3 when unset/unbooted/garbage. A budget, so deterministic
    code owns it (the §0 rule's explicit exemption class)."""
    comp = companion_settings()
    try:
        cap = int(getattr(comp, "research_daily_cap", _DEFAULT_RESEARCH_PER_DAY))
    except (TypeError, ValueError):
        cap = _DEFAULT_RESEARCH_PER_DAY
    # 0 = UNLIMITED (owner convention 2026-07-28; 07c Task 2, built
    # 2026-08-06). The old `cap >= 1 else default` silently rewrote an
    # explicit 0 to 3 — the knob ignored the owner. Negatives and garbage
    # still fall to the default.
    return cap if cap >= 0 else _DEFAULT_RESEARCH_PER_DAY


def curiosity_nas_daily_cap() -> int:
    """NAS wandering's OWN cap (companion.curiosity_nas_daily_cap; step 23,
    spec §0.3). 0 = UNLIMITED (the 07c convention) and the default —
    unbooted, garbage, or negative all read as unlimited: the owner's call
    is that the drive is hers to wander. A budget, so deterministic code
    owns it (the §0 rule's explicit exemption class)."""
    comp = companion_settings()
    try:
        cap = int(getattr(comp, "curiosity_nas_daily_cap", 0))
    except (TypeError, ValueError):
        cap = 0
    return cap if cap > 0 else 0


def _nas_deny() -> list[str]:
    """Top-level NAS names that do not exist to the dive's walk
    (companion.nas_deny; step 23, spec §0.1). EMPTY by the owner's explicit
    call ("no folders listed in denylist at this time") — quiet machinery
    he may populate later, never the centerpiece. Garbage reads as empty."""
    comp = companion_settings()
    raw = getattr(comp, "nas_deny", None) if comp is not None else None
    if not isinstance(raw, (list, tuple)):
        return []
    return [str(n).strip() for n in raw if str(n).strip()]

# The verbatim pre-2026-07-19 downtime-curiosity paragraphs from
# prompts/inner_monologue.txt. Flag-off ticks render THIS into the
# {curiosity_guidance} placeholder, so flag-off prompts are byte-identical
# to the old file. Captured at the moment the placeholder replaced it —
# do not retype, do not reflow (tests pin the boundaries and shape).
CURIOSITY_GUIDANCE_OFF = (
    "The time between conversations is yours, and you're allowed to spend "
    "some of it on your own head. If something in this tick sparks genuine "
    "curiosity — a topic you'd like to go read about for YOURSELF, not a "
    "task for them, and it doesn't need to relate to them at all — name it in "
    "`curiosity` as a short, search-engine-ready phrase (\"history of "
    "cursive handwriting decline\", \"why flamingos stand on one leg\"). "
    "The research happens in the background; what you learn joins your own "
    "thoughts, and only the rare genuinely-shareable find ever gets "
    "mentioned.\n\nMost ticks this should be null. A couple of times a day "
    "is plenty — curiosity is a spark, not a quota. Don't re-research "
    "something you've already gone down (check your recent thoughts above)."
)


def continuity_guidance(
    *,
    recent_topics: list[tuple[str, float]],
    interests: list[str] | None = None,
) -> str:
    """The flag-ON downtime-curiosity paragraphs: same spark-not-quota budget,
    but continuation of a live thread is explicitly allowed, and she can SEE
    her recent threads. Which topic counts as 'the same thread' is her
    judgment at the tick and the research LLM's at the dive — never code's.

    ``interests`` (2026-07-20, Task 7): her standing INTERESTS.md headings,
    from ai.passions.read_interests_headings(). Truthy only — a "what you're
    cultivating" block is spliced in between the intro and the threads
    block; None (the default) or an empty list must reproduce the exact
    pre-2026-07-20 bytes, so this composes ``cultivating`` as its own
    "" default and never touches ``lines`` (the threads block's own local),
    the one thing that would make the two blocks silently swap content."""
    if recent_topics:
        lines = "\n".join(
            f"· {topic} ({time.strftime('%b %d', time.localtime(ts))})"
            for topic, ts in recent_topics
        )
    else:
        lines = "(none lately)"
    cultivating = ""
    if interests:
        cultivating_lines = "\n".join(f"· {i}" for i in interests[:5])
        cultivating = (
            "What you're cultivating (your standing interests — reading that serves "
            f"one of these is how it deepens):\n{cultivating_lines}\n\n"
        )
    return (
        "The time between conversations is yours, and you're allowed to spend some of it "
        "on your own head. If something in this tick sparks genuine curiosity — a topic "
        "you'd like to go read about for YOURSELF, not a task for them, and it doesn't need "
        "to relate to them at all — name it in `curiosity` as a short, search-engine-ready "
        "phrase. The research happens in the background; what you learn joins your own "
        "thoughts, and only the rare genuinely-shareable find ever gets mentioned.\n\n"
        f"{cultivating}"
        "Threads you've been down lately:\n"
        f"{lines}\n\n"
        "Going back down a thread that's still live is how an interest becomes yours — "
        "name it again and you'll pick up where your notes left off. Don't reopen one "
        "you've exhausted just to have something to do; a dead end honestly left is also "
        "yours.\n\n"
        "Most ticks this should be null. A couple of times a day is plenty — curiosity is "
        "a spark, not a quota."
    )


def _exa_client():
    """Indirection point (patched in tests)."""
    from ai.web_search import web_search_engine
    return web_search_engine.exa_client


def count_research_today() -> int:
    """Research runs in the rolling 24h window (best-effort; 0 on error)."""
    try:
        cutoff = time.time() - _RESEARCH_WINDOW_SECONDS
        entries = read_entries(days_back=2, substantive_only=False, limit=300)
        # Budget counts search ROUNDS, not dives — a 3-round dive spends 3
        # units of the owner's daily cap. Legacy entries (no rounds key)
        # count as the single round they were.
        return sum(
            max(1, int((e.tick_metadata or {}).get("rounds", 1) or 1))
            for e in entries
            if e.timestamp >= cutoff
            and (e.tick_metadata or {}).get("entry_kind") == "curiosity_research"
            # Step 23 (spec §0.3): NAS wandering never spends the web
            # budget. Pre-modality entries were all web.
            and (e.tick_metadata or {}).get("modality", "web") != "nas"
        )
    except Exception as exc:
        logger.debug("curiosity: count_research_today failed (%s); assuming 0", exc)
        return 0


def count_nas_research_today() -> int:
    """NAS dives in the rolling 24h window — her wandering's own counter
    (step 23, spec §0.3): never the web budget's, never counted by it.
    Dives, not rounds (a NAS dive is single-round by construction)."""
    try:
        cutoff = time.time() - _RESEARCH_WINDOW_SECONDS
        entries = read_entries(days_back=2, substantive_only=False, limit=300)
        return sum(
            1 for e in entries
            if e.timestamp >= cutoff
            and (e.tick_metadata or {}).get("entry_kind") == "curiosity_research"
            and (e.tick_metadata or {}).get("modality") == "nas"
        )
    except Exception as exc:
        logger.debug("curiosity: count_nas_research_today failed (%s); assuming 0", exc)
        return 0


def curiosity_continuity_enabled() -> bool:
    """THE gate for this default-OFF repair (the consolidation_enabled
    pattern): unbooted settings or a missing flag => OFF. Also requires the
    curiosity_research faculty itself — continuity of dives that can never
    happen is a half-on state, and half-on reads as OFF
    (the narrative_self_enabled precedent).
    Spec: docs/superpowers/specs/2026-07-19-curiosity-continuity-design.md"""
    comp = companion_settings()
    return (
        comp is not None
        and bool(getattr(comp, "curiosity_continuity", False))
        and faculty_enabled("curiosity_research")
    )


def recent_research_entries(*, days_back: int = 14, limit: int = 3) -> list[MonologueEntry]:
    """Her recent completed dives, newest first. Filtering by the entry_kind
    WE stamp is data plumbing (exempt from the LLM-over-heuristics rule, same
    class as count_research_today). Never raises — continuity is best-effort."""
    try:
        entries = read_entries(days_back=int(days_back), substantive_only=True)
    except Exception as exc:
        logger.debug("curiosity: dive reader failed: %s", exc)
        return []
    dives = [
        e for e in entries
        if (e.tick_metadata or {}).get("entry_kind") == "curiosity_research"
    ]
    return dives[: max(0, int(limit))]


def recent_research_topics(*, days_back: int = 14, limit: int = 5) -> list[tuple[str, float]]:
    """(topic, timestamp) pairs for the monologue tick's guidance, newest
    first. Entries whose topic was never stamped are skipped here (nothing to
    name) but still count as notes for recent_research_entries."""
    out: list[tuple[str, float]] = []
    for e in recent_research_entries(days_back=days_back, limit=50):
        topic = str((e.tick_metadata or {}).get("topic") or "").strip()
        if topic:
            out.append((topic, float(e.timestamp)))
        if len(out) >= max(0, int(limit)):
            break
    return out


def research_allowed(modality: str = "web") -> tuple[bool, str]:
    """All gates in one place: faculty flag, modality flag, daily cap.
    The web_search feature flag is web-specific; the NAS modality has its
    OWN owner flag (§0.10 — his separate flip)."""
    try:
        if not faculty_enabled("curiosity_research"):
            return (False, "companion.curiosity_research faculty flag is off")
    except Exception:
        pass
    if modality == "nas":
        comp = companion_settings()
        if comp is None or not bool(getattr(comp, "curiosity_nas_dives", False)):
            return (False, "companion.curiosity_nas_dives is off")
        # Step 23 (spec §0.3): NAS wandering has its OWN cap (0 = unlimited,
        # the default) and its own counter — the web budget is untouched
        # by it in both directions.
        nas_cap = curiosity_nas_daily_cap()
        if nas_cap > 0 and count_nas_research_today() >= nas_cap:
            return (False, f"daily nas dive cap reached ({nas_cap})")
        return (True, "")
    from core.config import config
    if not bool(getattr(config, "effective_web_search_enabled", True)):
        # Boot turned it off for this run: the setting is on, the key is not
        # there (week 1 task 1).
        return (False, "web search has no key this boot")
    try:
        from core.settings_boot import settings_service
        snap = settings_service.snapshot()
        if snap is not None and not bool(snap.features.web_search):
            return (False, "features.web_search is off")
    except Exception:
        pass
    cap = research_daily_cap()
    if cap > 0 and count_research_today() >= cap:
        return (False, f"daily research cap reached ({cap})")
    return (True, "")


def maybe_queue_research(
    *, curiosity: Optional[str], owner_user_id: Optional[int],
    modality: str = "web",
) -> bool:
    """Schedule a background research run if the gates pass.

    Called from the monologue tick after write_entry (and the daydream
    tick, step 20 — where modality may be "nas"). Returns True when a
    task was scheduled. Never raises.
    """
    topic = str(curiosity or "").strip()
    if len(topic) < 3 or owner_user_id is None:
        return False
    # Setup mode (first door, 2026-09-17): both lanes that queue a dive are
    # idle already; this is the dive's own scheduler saying the same.
    if lane_idle("curiosity"):
        return False
    try:
        allowed, reason = research_allowed(modality)
        if not allowed:
            logger.debug("curiosity: skipping %r — %s", topic[:60], reason)
            return False
        task = asyncio.create_task(
            research_curiosity(
                topic=topic, owner_user_id=int(owner_user_id),
                modality=modality,
            ),
            name="curiosity_research",
        )

        def _log_failure(done: asyncio.Task[Any]) -> None:
            try:
                done.result()
            except Exception as exc:
                logger.warning("curiosity: research task failed: %s", exc)

        task.add_done_callback(_log_failure)
        logger.info("curiosity: following a thread — %r", topic[:80])
        return True
    except Exception as exc:
        logger.warning("curiosity: could not queue research: %s", exc)
        return False


def _nas_probe(topic: str, *, root: Optional[str] = None) -> dict[str, Any]:
    """Read-only NAS dive (re-spec §4.7; dignity charter Option A): walk a
    bounded slice of the house files for names touching the topic; small
    text heads become 'summaries', everything else a size/mtime line.
    NEVER writes, never follows symlinks; every bound is a constant.
    Step 23 (spec §0.1): the owner's ``nas_deny`` names — top level only —
    simply do not exist to the walk; the list is EMPTY by his call."""
    root = root or _nas_dive_root()
    if not root:
        # No NAS on this install. Say so and walk nothing — `Path("")` is
        # the process CWD (her own substrate), which is not a house drive.
        logger.info("curiosity: no NAS configured (PALLI_NAS_ROOT unset) — no dive")
        return {"results": []}
    base = Path(root)
    deny = set(_nas_deny())
    tokens = [t for t in re.split(r"[^a-z0-9]+", (topic or "").lower())
              if len(t) >= 3]
    results: list[dict[str, Any]] = []
    seen = 0
    try:
        for dirpath, dirnames, filenames in os.walk(base, followlinks=False):
            rel_depth = len(Path(dirpath).relative_to(base).parts)
            if rel_depth == 0 and deny:
                dirnames[:] = [d for d in dirnames if d not in deny]
                filenames = [f for f in filenames if f not in deny]
            if rel_depth >= _NAS_MAX_DEPTH:
                dirnames[:] = []
            for name in sorted(filenames):
                seen += 1
                if seen > _NAS_MAX_ENTRIES or len(results) >= _NAS_MAX_RESULTS:
                    raise StopIteration
                if tokens and not any(t in name.lower() for t in tokens):
                    continue
                p = Path(dirpath) / name
                try:
                    st = p.stat()
                    if (p.suffix.lower() in _NAS_TEXT_EXT
                            and st.st_size <= 200_000):
                        summary = p.read_text(
                            encoding="utf-8", errors="replace"
                        )[:500]
                    else:
                        summary = (
                            f"{st.st_size} bytes, modified "
                            + time.strftime("%Y-%m-%d",
                                            time.localtime(st.st_mtime))
                        )
                except OSError:
                    continue
                results.append({
                    "title": str(p.relative_to(base)),
                    "url": f"nas://{p.relative_to(base)}",
                    "summary": summary,
                })
    except StopIteration:
        pass
    except Exception as exc:
        logger.warning("curiosity: nas probe failed: %s", exc)
    return {"results": results}


def _render_sources(results: list[dict[str, Any]], limit: int = 5) -> str:
    lines: list[str] = []
    for i, r in enumerate(results[:limit], 1):
        title = str(r.get("title") or "(untitled)")
        url = str(r.get("url") or "")
        published = str(r.get("publishedDate") or "")
        body = " ".join(str(r.get("summary") or r.get("text") or "").split())[:1200]
        lines.append(f"Source {i}: {title}")
        if url:
            lines.append(f"  URL: {url}")
        if published:
            lines.append(f"  Published: {published}")
        if body:
            lines.append(f"  Content: {body}")
    return "\n".join(lines)


async def research_curiosity(
    *, topic: str, owner_user_id: int, modality: str = "web",
) -> Optional[MonologueEntry]:
    """Run one curiosity research pass; returns the written entry or None.
    Holds her time (2026-08-18: one thing at a time — a workbench session
    and a dive never run together; whichever starts first goes first)."""
    from ai import her_time

    async with her_time.hold("a curiosity dive"):
        return await _research_curiosity_locked(
            topic=topic, owner_user_id=owner_user_id, modality=modality,
        )


async def _research_curiosity_locked(
    *, topic: str, owner_user_id: int, modality: str = "web",
) -> Optional[MonologueEntry]:
    try:
        if modality == "nas":
            payload = await asyncio.to_thread(_nas_probe, topic)
        else:
            payload = await _exa_client().search(
                query=topic, num_results=5, summary=True,
            )
    except Exception as exc:
        logger.warning("curiosity: %s search failed for %r: %s",
                       modality, topic[:60], exc)
        return None
    results = (payload or {}).get("results") or []
    if not results:
        logger.info("curiosity: no results for %r; letting it go", topic[:60])
        return None

    template = prompt_loader.load_prompt("curiosity_research")
    if not template:
        logger.warning("curiosity: prompt template missing")
        return None
    # Continuity (2026-07-19): flag-on, her recent dive notes ride along and
    # the LLM judges whether this topic is the same thread — code never
    # string-matches topics (the §0 LLM-over-heuristics rule). Flag-off (or
    # no prior notes) fills the placeholder with "" — byte-identical render.
    prior_notes = ""
    if curiosity_continuity_enabled():
        prior = recent_research_entries(days_back=14, limit=3)
        if prior:
            note_lines = "\n".join(
                f"[{time.strftime('%b %d', time.localtime(e.timestamp))}] {e.thought}"
                for e in prior
            )
            prior_notes = (
                "\nYour own earlier research notes — they may or may not relate to "
                "this topic; you judge. Build on the ones that are the same thread, "
                "ignore the rest, and say so honestly if this picks up where one "
                "left off:\n" + note_lines + "\n"
            )
    # Multi-round dives (2026-07-21): the note call may return a
    # followup_query instead of the note — one more search, sources
    # accumulate, ONE synthesized note at the end. Bounds are deterministic
    # (max rounds + the owner's daily budget, counted in rounds); whether
    # another look is WORTH it is her judgment, never a counter's.
    model = get_task_model("reflection", fallback=get_task_model("memory"))
    spent_today = count_research_today()
    cap = research_daily_cap()
    all_results: list[dict[str, Any]] = list(results)
    followups: list[str] = []
    rounds = 1
    search_dead = False
    data: Any = None
    # NAS dives are single-round in v1 (re-spec §4.7) — the follow-up
    # machinery is a web-search shape.
    max_rounds = 1 if modality == "nas" else _MAX_DIVE_ROUNDS
    while True:
        followup_available = (
            rounds < max_rounds
            and (cap <= 0 or (spent_today + rounds) < cap)
            and not search_dead
        )
        if followup_available:
            round_status = (
                f"\nThis is look {rounds} of up to {_MAX_DIVE_ROUNDS} this "
                "sitting. If what you just read opens one specific question "
                "that one more search would genuinely serve, return "
                "followup_query (a real search query, not a topic label) "
                "INSTEAD of the note — its sources will be added and you'll "
                "write one note over everything. If you already have what "
                "you need, just write the note.\n"
            )
        else:
            round_status = (
                "\nThis was your last look this sitting — write the note "
                "now from everything above; no follow-up is available.\n"
            )
        rendered = format_prompt(
            template,
            topic=topic,
            prior_notes=prior_notes,
            sources=_render_sources(all_results, limit=len(all_results)),
            current_time=time.strftime("%Y-%m-%d %I:%M %p", time.localtime()),
            round_status=round_status,
        )
        raw = await llm.chat_completion(
            [{"role": "system", "content": rendered}],
            model=model,
            temperature=0.6,
            max_tokens=800,
            log_context="curiosity_research",
        )
        data = safe_parse_json_object(raw or "")
        if not isinstance(data, dict):
            logger.warning("curiosity: unparseable research note for %r", topic[:60])
            return None
        note_so_far = str(data.get("note") or "").strip()
        followup = str(data.get("followup_query") or "").strip()
        if note_so_far or not followup or not followup_available:
            break
        followups.append(followup)
        rounds += 1
        try:
            more = await _exa_client().search(
                query=followup, num_results=_FOLLOWUP_NUM_RESULTS, summary=True,
            )
            new_results = (more or {}).get("results") or []
        except Exception as exc:
            logger.warning(
                "curiosity: follow-up search failed for %r (%s); writing from "
                "what the earlier rounds gathered",
                followup[:60], exc,
            )
            new_results = []
        if new_results:
            all_results.extend(new_results)
            logger.info(
                "curiosity: follow-up look %d — %r (+%d sources)",
                rounds, followup[:70], len(new_results),
            )
        else:
            search_dead = True

    note = str(data.get("note") or "").strip()
    if not note:
        return None
    worth_sharing = bool(data.get("worth_sharing"))
    framing = str(data.get("share_framing") or "").strip()

    entry = MonologueEntry(
        timestamp=time.time(),
        thought=note,
        situation_snapshot={},
        tick_metadata={
            "entry_kind": "curiosity_research",
            "topic": topic,
            "modality": modality,
            "substantive": True,
            "source_count": len(all_results),
            "rounds": rounds,
            "followup_queries": followups,
        },
        sentiment=None,
        proactive_candidate=(framing if (worth_sharing and framing) else None),
        candidate_kind=("observation" if (worth_sharing and framing) else None),
        candidate_action_hint=None,
    )
    write_entry(entry)
    # An occasion for her daydream lane (re-spec §4.6): a dive coming
    # back is a natural moment to wonder what's next. Fire-and-forget.
    try:
        from ai.daydream import notice as _daydream_notice

        _daydream_notice("curiosity_returned")
    except Exception:
        pass
    logger.info(
        "curiosity: researched %r → note written%s",
        topic[:60],
        " + share candidate" if entry.proactive_candidate else "",
    )
    return entry
