"""The smoke detector: did every organ write — and when one did not, was
there nothing to do, or did it break?

Every organ fails silent by design — a dead night cycle just stops writing,
a starved consolidation just leaves no gist, and nothing notices until a
human greps the data directory. This module turns an absence into one loud
log line, and into the table under one line of `palli_ctl doctor`.

Three answers, and only three (scripts/organ_liveness.py maps exactly these):

- ``wrote`` — an outcome inside the window: a row, an event, a dated file,
  a commit in her repo.
- ``quiet`` — nothing written, and a trace says it ran without error: it
  had nothing to do. An occasioned organ with nothing in the window is
  quiet too, and never anything else.
- ``stale`` — it should have written and did not: a recorded step error,
  or no trace at all when the run was due. A recorded error of an organ's
  scheduled step is read FIRST: stakes, views and fears have writers
  outside their step (a turn's appraisal, a view updated, a reflection's
  plant), and their writes must never hide a step that fails every night.

An organ whose flag is off is left out of the report (``flags``; the
night cycle, the CLI and the doctor pass them from the install's
settings), so a deliberately dark organ never reads as stale.

Status comes from what an organ wrote, never from a job marker alone
(Batch 6, item 3.8). Where each outcome is read:

- DAILY — night_cycle, day_gists, present_moment, inner_monologue: a dated
  file at or after the due night, judged against the night cycle's run
  time (06:30 by default since 2026-10-05; 04:00 before). day_gists is
  also quiet when the night's record shows the consolidation ran and had
  no gist to write.
- NIGHTLY, steps of the night pass — stakes (rows of the stakes table of
  kind hope, worry, taste or wonder, by ``updated_at``), views (rows of
  kind view; else the pass's own ``views/.state.json``), disclosures
  (plant and fade events in ``disclosures/disclosures.jsonl``), tonight
  (``tonight/<day>.json``: a pick is wrote, a null night is quiet).
  Otherwise the night's record decides. rest_of_night: every step of the
  pass with no line of its own (the memory sweep, the preferences
  rebuild, the thread retirement, the owner picture, the targeted
  correction pass, the consolidation's aging and reconciling, and any step
  added later), judged by the record alone: stale when one of them failed
  on the due night, and the detail names which.
- WEEKLY — narrative_self and passions: commits to their own pages in her
  repo since the week their marker claims (the marker is written before
  the work, so it alone proves nothing); a claim with no commit is stale,
  unless the week's record shows every story page came back unchanged.
  callbacks: plant events in ``callbacks/callbacks.jsonl``, else the
  harvest's ``callbacks/.state.json``. fears: plant, face and release
  events in ``fears/fears.jsonl`` (a touch is the moment's noticing, not
  the book written), else the weekly re-look's result in the record.
- OCCASIONED — daydream, workbench, arbiter (dated files), corrections
  (events), forge (the manifest's ``created_at``), regret (rows of kind
  regret), hands (the deeds ring): wrote or quiet, never stale.
- surprise rides the present moment: wrote when a moment in the window
  carries a surprise, quiet when moments were written without one, and no
  line of its own while the present moment is not writing.

The night's record is ``night_cycle/.outcomes/<night>.json``: what every
step of the pass returned, and what the night's health line names as a
failure, saved by the night cycle at the end of each pass
(ai/night_cycle.py). On the night the weekly ritual runs, its per-page
results and the fears re-look's result sit under ``weekly``. The record
began with Batch 6, on the oldest night on file: a night older than that
(or a week whose ritual ran before it) left no record and has no evidence
either way, so it reads quiet and says when the record began. Nights that
ran with no record folder at all ran under the code before it: until the
first night under this code the record has not begun, and what it would
judge reads quiet and says the record begins with the next night (the
night cycle's own line still judges the pass). No night and no record at
all keeps the fresh install's answer. A record that is there but cannot
be read is named, and stale: the pass wrote something it cannot prove.

Three consumers: the night tick loop logs ``report.summary()`` once a day
after the night pass (off the event loop, guarded — the checker must never
kill the night task), scripts/organ_liveness.py prints the table for cron
and hands, and `palli_ctl doctor` prints it under one line.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

from ai import organ_liveness_sources as src
from ai.narrative_self import INTERESTS_DOC, NARRATIVE_DOCS

# When the due night turns over: the night cycle's RUN time, in hours after
# local midnight (6.5 = 06:30, ai.night_cycle.night_run_at). Since 10-05 the
# night starts at 06:30, not at the 04:00 day boundary (the owner: "move the
# night run off-peak"); a boundary left at 04:00 would call yesterday's
# night stale from 04:00 until the pass ran at 06:30. Deliberately a default
# parameter, not an import — the engine passes its own run time, and
# install_report reads the install's (importing at load would create a
# cycle with the wire-in).
DEFAULT_BOUNDARY_HOUR = 6.5

DAILY_ORGANS = ("night_cycle", "day_gists", "present_moment", "inner_monologue")
NIGHTLY_ORGANS = ("stakes", "views", "disclosures", "tonight", "rest_of_night")
WEEKLY_ORGANS = ("narrative_self", "passions", "callbacks", "fears")
OCCASIONED_ORGANS = ("daydream", "workbench", "arbiter", "corrections", "forge", "regret")
# Every line the report can carry, in report order.
ORGANS = (DAILY_ORGANS + NIGHTLY_ORGANS + ("surprise",) + WEEKLY_ORGANS
          + OCCASIONED_ORGANS + ("hands",))

# Kinds in the stakes table with lines of their own: not "stakes".
_OWN_LINE_KINDS = ("view", "regret")
# Her repo's pages, per weekly organ: the log is filtered by path.
_PAGES = {
    "narrative_self": tuple(NARRATIVE_DOCS),
    "passions": (INTERESTS_DOC, "journal"),
}
# What each book's scheduled step writes. A fear touch and a small truth
# said are other doors' events; a correction's file snapshot is bookkeeping.
_BOOK_OPS = {
    "disclosures": ("plant", "fade"),
    "callbacks": ("plant",),
    "fears": ("plant", "face", "release"),
    "corrections": ("plant", "release", "edit"),
}
_WEEK_KEY = re.compile(r"^(\d{4})-W(\d{2})$")
# The night's steps (ai/night_cycle.py, _run_once) whose failure a line of
# its own reads: the reflection is the night cycle's, the consolidation's
# day gist is day_gists', the weekly ritual is the story pages' and the
# fears re-look's, and the rest are named by their lines. Every other step
# — and any step added later — is rest_of_night's, and so are the
# consolidation's aging and reconciling, which have no line of their own.
_STEPS_WITH_A_LINE = frozenset({
    "reflection", "consolidation", "narrative", "callbacks_harvest", "stake_review",
    "views", "passions", "disclosure_harvest", "tonight",
})
_CONSOLIDATION_REST = ("aging", "reconcile")
# What a line says while nights have run but the record has not begun.
NOT_BEGUN = "not recorded yet — the night's record begins with the next night"


@dataclass
class LivenessEntry:
    name: str
    status: str  # "wrote" | "stale" | "quiet"
    detail: str


@dataclass
class LivenessReport:
    checked_at: datetime
    entries: list[LivenessEntry] = field(default_factory=list)

    @property
    def stale_names(self) -> list[str]:
        return [e.name for e in self.entries if e.status == "stale"]

    @property
    def healthy(self) -> bool:
        return not self.stale_names

    def summary(self) -> str:
        wrote = [e.name for e in self.entries if e.status == "wrote"]
        quiet = [e.name for e in self.entries if e.status == "quiet"]
        parts = [f"{len(wrote)} wrote"]
        if quiet:
            parts.append(f"quiet: {','.join(quiet)}")
        if self.healthy:
            return f"organ_liveness: OK — {'; '.join(parts)}"
        return (
            f"organ_liveness: STALE [{','.join(self.stale_names)}] — "
            f"{'; '.join(parts)}"
        )


# ── the clock every judge reads ──────────────────────────────────────────

@dataclass(frozen=True)
class _Window:
    due_night: date      # the newest night the pass should have looked back on
    since: float         # local midnight at the start of the due night
    lawful: frozenset    # this ISO week and the one before
    week_since: float    # Monday 00:00 of the earlier lawful week


def _epoch(day: date) -> float:
    return datetime.combine(day, time.min).timestamp()


def _iso_week(d: date) -> str:
    year, week, _ = d.isocalendar()
    return f"{year}-W{week:02d}"


def _week_start(week_key: str) -> Optional[float]:
    m = _WEEK_KEY.match(str(week_key or ""))
    if not m:
        return None
    try:
        return _epoch(date.fromisocalendar(int(m.group(1)), int(m.group(2)), 1))
    except ValueError:
        return None


def _window(now: datetime, boundary_hour: float) -> _Window:
    shifted_today = (now - timedelta(hours=boundary_hour)).date()
    due_night = shifted_today - timedelta(days=1)
    earlier_monday = shifted_today - timedelta(days=shifted_today.weekday() + 7)
    return _Window(
        due_night=due_night,
        since=_epoch(due_night),
        lawful=frozenset({_iso_week(shifted_today), _iso_week(shifted_today - timedelta(days=7))}),
        week_since=_epoch(earlier_monday),
    )


# ── the night's record (written by ai/night_cycle.py, ruling R51) ────────

@dataclass
class _Night:
    """The record of the due night (or a newer one), or an empty one — and
    what the record as a whole says: the night it began, and the nights
    whose record is there but cannot be read."""
    night: Optional[date] = None
    steps: Dict[str, Any] = field(default_factory=dict)
    failed: Dict[str, Any] = field(default_factory=dict)
    began: Optional[date] = None          # the oldest night on file; None: no record yet
    unreadable: Tuple[date, ...] = ()     # nights whose file is there but cannot be read
    not_begun: bool = False               # nights have run, and no record folder yet

    def result(self, step: str) -> Dict[str, Any]:
        value = self.steps.get(step)
        return value if isinstance(value, dict) else {}

    def ran(self, step: str) -> bool:
        return self.night is not None and step in self.steps

    def failure(self, step: str) -> str:
        """What the night named as this step's failure, or its error, or ""."""
        named = self.failed.get(step)
        if named:
            return str(named)
        error = self.result(step).get("error")
        return f"{step}: {error}" if error else ""

    def unrecorded(self, day: date) -> str:
        """Why ``day`` has no record through no fault of its own: nights
        have run but the record has not begun (no record folder yet — every
        night so far ran under the code before it), or ``day`` is older than
        the first night on file (the record began with Batch 6; a night run
        by the code before it left none). In both cases there is no
        evidence either way. "" otherwise — and when no night has run at
        all, which keeps the fresh install's answer."""
        if self.not_begun:
            return NOT_BEGUN
        if self.began is not None and day < self.began:
            return f"not recorded — the night's record began {self.began}"
        return ""

    def unread(self, since: date, until: Optional[date] = None) -> str:
        """A record between ``since`` and ``until`` that is there but cannot
        be read, named; "" when there is none. The pass wrote something it
        cannot prove, so it is not read as "no record"."""
        bad = [d for d in self.unreadable if d >= since and (until is None or d <= until)]
        return f"the record of the night of {bad[-1]} could not be read" if bad else ""


def _outcomes_dir(data_root: Path) -> Path:
    return data_root / "night_cycle" / ".outcomes"


def _weekly_since(win: _Window) -> date:
    """The oldest night a lawful week's ritual can be recorded on: the day
    before the earlier lawful week's Monday (the Monday pass looks back on
    Sunday)."""
    return date.fromtimestamp(win.week_since) - timedelta(days=1)


def _ritual_night(week_key: str) -> Optional[date]:
    """The night a week's ritual is due on: the Sunday before its Monday."""
    start = _week_start(week_key)
    return date.fromtimestamp(start) - timedelta(days=1) if start is not None else None


def _night_files(data_root: Path) -> bool:
    """A night pass left its own files: its claim file or a reflection."""
    night = Path(data_root) / "night_cycle"
    return (night / ".engine_state.json").is_file() or src.newest_dated(night, ".jsonl") is not None


def _due_night(data_root: Path, win: _Window) -> _Night:
    directory = _outcomes_dir(data_root)
    if not directory.exists() and _night_files(data_root):
        # Nights ran, under the code before the record (R73): the record
        # has not begun. A folder that is there, even empty, has — the
        # first write under this code makes it.
        return _Night(not_begun=True)
    files = src.dated_files(directory, ".json")
    began = files[0][0] if files else None
    unreadable = tuple(day for day, path in files
                       if day >= _weekly_since(win) and src.read_json(path) is None)
    found = src.dated_records(directory, ".json", win.due_night)
    if not found:
        return _Night(began=began, unreadable=unreadable)
    night, record = found[-1]     # the due night itself when there is one
    steps = record.get("steps")
    failed = record.get("failed")
    return _Night(night, steps if isinstance(steps, dict) else {},
                  failed if isinstance(failed, dict) else {},
                  began=began, unreadable=unreadable)


def _weekly_records(data_root: Path, win: _Window) -> List[Dict[str, Any]]:
    """Every ``weekly`` section since the earlier lawful week began, newest
    first (from the day of slack _weekly_since allows)."""
    out = []
    for _night, record in src.dated_records(_outcomes_dir(data_root), ".json", _weekly_since(win)):
        weekly = record.get("weekly")
        if isinstance(weekly, dict):
            out.append(weekly)
    return out


# ── how each organ is judged ─────────────────────────────────────────────

def _brief(result: Any) -> str:
    """A step's result in a few words, for the report's detail."""
    if not isinstance(result, dict):
        return str(result)[:80]
    parts = [f"{k} {v}" for k, v in result.items()
             if v is None or isinstance(v, (str, int, float, bool))]
    return (", ".join(parts) or "nothing to report")[:80]


def _count(counts: Mapping[str, int]) -> str:
    return ", ".join(f"{n} {op}" for op, n in sorted(counts.items()))


def _unreadable(what: str, why: str) -> str:
    return f" ({what} unreadable: {why})" if why else ""


def _also(wrote: str) -> str:
    return f" (and {wrote})" if wrote else ""


def _by_record(organ: str, step: str, night: _Night, win: _Window, *,
               wrote: str = "", ran: str = "", note: str = "") -> LivenessEntry:
    """A scheduled organ, judged with the night's record. A failure the
    record names for its step is stale FIRST — even when the window has an
    outcome (``wrote``), which other doors may have written, and which the
    detail names all the same. Then an outcome is wrote; a record of the
    due night that cannot be read is stale (it might have named a
    failure); a trace that it ran (``ran``, the organ's own record, or the
    night's) is quiet; a due night the record does not reach (older than
    its first night, or the record not begun) is quiet, with no evidence
    either way; nothing at all is stale — the run was due."""
    failure = night.failure(step)
    if failure:
        return LivenessEntry(organ, "stale", failure + _also(wrote) + note)
    if wrote:
        return LivenessEntry(organ, "wrote", wrote)
    unread = night.unread(win.due_night, win.due_night)
    if unread:
        return LivenessEntry(organ, "stale", unread + note)
    if ran:
        return LivenessEntry(organ, "quiet", ran + note)
    if night.ran(step):
        return LivenessEntry(organ, "quiet",
                             f"the night of {night.night} ran it: {_brief(night.steps[step])}" + note)
    unrecorded = night.unrecorded(win.due_night)
    if unrecorded:
        return LivenessEntry(organ, "quiet", f"the night of {win.due_night}: {unrecorded}" + note)
    return LivenessEntry(organ, "stale", f"no record of the night of {win.due_night}" + note)


def _daily(organ: str, data_root: Path, win: _Window, night: _Night) -> LivenessEntry:
    newest = src.newest_dated(data_root / organ)
    if newest is not None and newest >= win.due_night:
        return LivenessEntry(organ, "wrote", f"newest {newest}")
    stale = LivenessEntry(organ, "stale", f"newest {newest or 'none'}; expected >= {win.due_night}")
    if organ != "day_gists" or night.night is None:
        return stale
    gist = night.result("consolidation").get("day_gist")
    if not isinstance(gist, dict):
        return stale
    if gist.get("error"):
        return LivenessEntry(organ, "stale", f"the day's gist: {gist['error']}")
    if not gist.get("wrote"):
        reason = gist.get("reason") or "nothing to write"
        return LivenessEntry(organ, "quiet", f"the night of {night.night} had no gist to write ({reason})")
    return stale


def _stakes(win: _Window, rows: Mapping[str, int], night: _Night, note: str) -> LivenessEntry:
    # A turn's appraisal updates these rows nearly every turn: they never
    # hide a nightly review that failed (_by_record reads it first).
    n = sum(v for kind, v in rows.items() if kind not in _OWN_LINE_KINDS)
    wrote = f"{n} row(s) updated since {win.due_night}" if n else ""
    return _by_record("stakes", "stake_review", night, win, wrote=wrote, note=note)


def _views(data_root: Path, win: _Window, rows: Mapping[str, int], night: _Night,
           note: str) -> LivenessEntry:
    # A view updated, muted or unmuted in a turn is a row too: it never
    # hides a views pass that failed (_by_record reads it first).
    n = rows.get("view", 0)
    wrote = f"{n} view(s) updated since {win.due_night}" if n else ""
    ran = ""
    state = {} if n else (src.read_json(data_root / "views" / ".state.json") or {})
    last_night = str(state.get("last_night") or "")
    if not n and last_night >= win.due_night.isoformat():
        formed = int(state.get("formed") or 0)
        if formed:
            wrote = f"formed {formed} on the night of {last_night}"
        else:
            # A view she already holds (a twin) is the rails working, and
            # the night's health line does not name it, so it is no failure
            # here either: quiet, with the twin named. Any other refusal is
            # named on that line, and _by_record reads it as stale first.
            refused = night.result("views").get("refused")
            ran = f"the pass ran for {last_night}: no view formed" + (f" ({refused})" if refused else "")
    return _by_record("views", "views", night, win, wrote=wrote, ran=ran, note=note)


def _disclosures(data_root: Path, win: _Window, night: _Night) -> LivenessEntry:
    # The window opens at midnight of the due night, so the plant of the
    # pass that ran that morning is inside it: it never hides tonight's
    # failed harvest (_by_record reads it first).
    counts, why = src.events_since(data_root / "disclosures" / "disclosures.jsonl",
                                   win.since, _BOOK_OPS["disclosures"])
    wrote = f"{_count(counts)} since {win.due_night}" if counts else ""
    return _by_record("disclosures", "disclosure_harvest", night, win,
                      wrote=wrote, note=_unreadable("her book", why))


def _tonight(data_root: Path, win: _Window, night: _Night) -> LivenessEntry:
    # The pick is for the day the pass starts: the day after the due night.
    found = src.dated_records(data_root / "tonight", ".json", win.due_night + timedelta(days=1))
    wrote = ran = ""
    if found:
        day, record = found[0]
        if record.get("pick"):
            said = " (said)" if record.get("said_at") else ""
            wrote = f"a pick for {day}{said}"
        else:
            refused = f" (refused: {record['refused']})" if record.get("refused") else ""
            ran = f"no pick for {day}{refused}"
    return _by_record("tonight", "tonight", night, win, wrote=wrote, ran=ran)


def _rest_failures(night: _Night) -> List[str]:
    """What the record names as failed among the steps with no line of
    their own, in the order the pass ran them."""
    out: List[str] = []
    for step in dict.fromkeys([*night.steps, *night.failed]):
        if step == "consolidation":
            # Its day gist is day_gists'; the step as a whole, its aging and
            # its reconciling are nobody else's.
            result = night.result(step)
            if result.get("error"):
                out.append(f"consolidation: {result['error']}")
            for sub in _CONSOLIDATION_REST:
                part = result.get(sub)
                if isinstance(part, dict) and part.get("error"):
                    out.append(f"consolidation {sub}: {part['error']}")
        elif step not in _STEPS_WITH_A_LINE:
            why = night.failure(step)
            if why:
                out.append(why)
    return out


def _rest_of_night(win: _Window, night: _Night) -> LivenessEntry:
    """The night's steps with no line of their own, judged by the record
    alone: one of them failed → stale, naming which; the due night's
    record with none failed → quiet. The record's floors hold as for every
    scheduled organ: a record of the due night that cannot be read is
    stale; a due night the record does not reach is quiet; no record of a
    due night is stale — the run was due."""
    failed = _rest_failures(night)
    if failed:
        return LivenessEntry("rest_of_night", "stale", f"the night of {night.night}: " + "; ".join(failed))
    unread = night.unread(win.due_night, win.due_night)
    if unread:
        return LivenessEntry("rest_of_night", "stale", unread)
    if night.night == win.due_night:
        return LivenessEntry("rest_of_night", "quiet", f"the night of {win.due_night}: no other step failed")
    unrecorded = night.unrecorded(win.due_night)
    if unrecorded:
        return LivenessEntry("rest_of_night", "quiet", f"the night of {win.due_night}: {unrecorded}")
    return LivenessEntry("rest_of_night", "stale", f"no record of the night of {win.due_night}")


def _surprise(data_root: Path, win: _Window) -> LivenessEntry:
    written, surprised = src.moments_since(data_root / "present_moment", win.due_night)
    if surprised:
        return LivenessEntry("surprise", "wrote",
                             f"{surprised} of {written} moment(s) since {win.due_night} carried a surprise")
    return LivenessEntry("surprise", "quiet",
                         f"{written} moment(s) since {win.due_night}, none carried a surprise")


def _pages(organ: str, data_root: Path, win: _Window, night: _Night,
           weekly: List[Dict[str, Any]], identity_root: Optional[Path]) -> LivenessEntry:
    """A weekly organ that writes pages in her repo (R53): its marker says
    which week it claimed, and a commit to its own pages since that week
    began says it wrote. The marker alone proves nothing — it is written
    before the work, and kept after a week whose every page was refused."""
    marker = src.read_json(data_root / organ / ".state.json") or {}
    week = str(marker.get("last_week") or "")
    if week not in win.lawful:
        # A week of no change gives the interests claim back, so the marker
        # falls behind; the night's record says the pass ran and chose none.
        interests = night.result("passions").get("interests")
        if (organ == "passions" and isinstance(interests, dict)
                and interests.get("skipped") == "no_change" and not night.failure("passions")):
            return LivenessEntry(organ, "quiet", f"the night of {night.night}: nothing to change")
        return LivenessEntry(organ, "stale",
                             f"last_week {week or 'none'}; expected one of {sorted(win.lawful)}")
    committed, why = (None, "")
    if identity_root is not None:
        committed, why = src.last_commit_at(identity_root, _PAGES[organ])
    started = _week_start(week) or win.week_since
    if committed is not None and committed >= started:
        when = datetime.fromtimestamp(committed).strftime("%Y-%m-%d %H:%M")
        return LivenessEntry(organ, "wrote", f"committed {when} (week {week})")
    # No repo where the settings point is named as such, not as "no page".
    note = f" ({why})" if why.startswith(src.NO_REPO) else _unreadable("her repo", why)
    claimed = f"week {week} claimed, no page committed since it began"
    if organ == "narrative_self":
        pages = next((w.get("pages") for w in weekly if w.get("week") == week), None)
        if (isinstance(pages, dict) and pages
                and all(p == {"committed": "no_change"} for p in pages.values())):
            return LivenessEntry(organ, "quiet", f"week {week}: every page came back unchanged" + note)
        # Its every-page-unchanged answer lives in the night's record: a
        # ritual that ran before the record began left none to read.
        ritual = _ritual_night(week)
        if pages is None and ritual is not None:
            unread = night.unread(ritual)
            if unread:
                return LivenessEntry(organ, "stale", f"{claimed}; {unread}" + note)
            unrecorded = night.unrecorded(ritual)
            if unrecorded:
                return LivenessEntry(organ, "quiet",
                                     f"{claimed}; its ritual (the night of {ritual}): {unrecorded}" + note)
    return LivenessEntry(organ, "stale", claimed + note)


def _callbacks(data_root: Path, win: _Window, night: _Night) -> LivenessEntry:
    # The window reaches back to the earlier lawful week, so last week's
    # plant would otherwise hide this week's broken harvest: a recorded
    # failure is read first.
    counts, why = src.events_since(data_root / "callbacks" / "callbacks.jsonl",
                                   win.week_since, _BOOK_OPS["callbacks"])
    wrote = f"{_count(counts)} since {date.fromtimestamp(win.week_since)}" if counts else ""
    note = _unreadable("her book", why)
    failure = night.failure("callbacks_harvest")
    if failure:
        return LivenessEntry("callbacks", "stale", failure + _also(wrote) + note)
    if wrote:
        return LivenessEntry("callbacks", "wrote", wrote)
    state = src.read_json(data_root / "callbacks" / ".state.json") or {}
    week = str(state.get("last_week") or "")
    if week in win.lawful:
        return LivenessEntry("callbacks", "quiet", f"the harvest ran for {week}: no new bit" + note)
    if night.not_begun:
        # Not through _by_record: its trace is its own record, and before
        # the night's record begins there is no failure to read either.
        return LivenessEntry("callbacks", "quiet", f"the harvest of {max(win.lawful)}: {NOT_BEGUN}" + note)
    return LivenessEntry("callbacks", "stale",
                         f"no harvest recorded for {' or '.join(sorted(win.lawful))}" + note)


def _fears(data_root: Path, win: _Window, night: _Night,
           weekly: List[Dict[str, Any]]) -> LivenessEntry:
    # A reflection plants fears too, so a plant in the window never hides a
    # re-look that failed: the week's record is read first. Its failures
    # are the re-look's error or refusal, and a ritual that ended in an
    # error before it reached the re-look; a ritual skipped for a lawful
    # reason (the flag off, the first authorship pending, the week already
    # done) records none of them.
    counts, why = src.events_since(data_root / "fears" / "fears.jsonl",
                                   win.week_since, _BOOK_OPS["fears"])
    wrote = f"{_count(counts)} since {date.fromtimestamp(win.week_since)}" if counts else ""
    note = _unreadable("her book", why)
    record = next((w for w in weekly if w.get("week") in win.lawful), None)
    relook = record.get("fears") if record is not None else None
    if isinstance(relook, dict):
        week = record.get("week")
        if relook.get("not_reached"):
            return LivenessEntry("fears", "stale",
                                 f"the ritual of {week} never reached the re-look: {relook['not_reached']}"
                                 + _also(wrote) + note)
        for key in ("error", "rejected"):
            if relook.get(key):
                return LivenessEntry("fears", "stale",
                                     f"the re-look of {week}: {key} {relook[key]}" + _also(wrote) + note)
    if wrote:
        return LivenessEntry("fears", "wrote", wrote)
    if not isinstance(relook, dict):
        unread = night.unread(_weekly_since(win))
        if unread:
            return LivenessEntry("fears", "stale", unread + note)
        # This week's ritual ran before the record began, or the record has
        # not begun: no evidence either way until the first ritual on record.
        this_week = max(win.lawful)
        ritual = _ritual_night(this_week)
        unrecorded = night.unrecorded(ritual) if ritual is not None else ""
        if unrecorded:
            return LivenessEntry("fears", "quiet",
                                 f"the re-look of {this_week} (the night of {ritual}): {unrecorded}" + note)
        return LivenessEntry("fears", "stale",
                             f"no record of the weekly re-look for {' or '.join(sorted(win.lawful))}" + note)
    return LivenessEntry("fears", "quiet", f"the re-look of {record.get('week')} ran: {_brief(relook)}" + note)


def _occasioned(organ: str, data_root: Path, win: _Window, rows: Mapping[str, int],
                db_note: str) -> LivenessEntry:
    if organ == "corrections":
        counts, why = src.events_since(data_root / "corrections" / "corrections.jsonl",
                                       win.since, _BOOK_OPS["corrections"])
        found, what, note = sum(counts.values()), f"{_count(counts)}", _unreadable("the ledger", why)
    elif organ == "forge":
        found, why = src.forged_since(data_root / "forge" / "manifest.json", win.since)
        what, note = f"{found} tool(s) forged", _unreadable("the manifest", why)
    elif organ == "regret":
        found = rows.get("regret", 0)
        what, note = f"{found} regret row(s) updated", db_note
    else:
        newest = src.newest_dated(data_root / organ)
        if newest is not None and newest >= win.due_night:
            return LivenessEntry(organ, "wrote", f"newest {newest}")
        return LivenessEntry(organ, "quiet", f"newest {newest or 'none'} (lawful)")
    if found:
        return LivenessEntry(organ, "wrote", f"{what} since {win.due_night}" + note)
    return LivenessEntry(organ, "quiet", f"nothing since {win.due_night} (lawful)" + note)


def _read_deeds(root: Optional[Path] = None) -> list:
    """Her last-24h deeds — the hands count's source. Isolated seam so
    tests inject; failures mean an empty day, never a crash. ``root`` is
    the caller's data_root — without it this read was cwd-relative and
    ignored check_liveness's data_root entirely (the parked step-33 item)."""
    try:
        from ai.recent_deeds import read_recent_deeds
        return read_recent_deeds(limit=200, max_age_hours=24.0, root=root)
    except Exception:
        return []


def _hands(data_root: Path) -> LivenessEntry:
    # Her hands (step 33): how much she ACTED in the last day — the
    # owner's "see where it goes" eyes. Occasioned: quiet is lawful.
    acts = [
        d for d in _read_deeds(data_root)
        if d.get("kind") == "hands"
        or (d.get("kind") == "sent" and "her_hands" in str(d.get("detail", "")))
    ]
    if acts:
        return LivenessEntry("hands", "wrote", f"{len(acts)} acts (24h)")
    return LivenessEntry("hands", "quiet", "no acts (lawful)")


def check_liveness(
    *,
    now: datetime,
    data_root: Path,
    expected: Optional[Iterable[str]] = None,
    boundary_hour: float = DEFAULT_BOUNDARY_HOUR,
    flags: Optional[Mapping[str, bool]] = None,
    db_path: Optional[Path] = None,
    identity_root: Optional[Path] = None,
) -> LivenessReport:
    """Judge every organ by what it wrote.

    ``flags`` maps an organ to whether it is on (``liveness_flags``); an
    organ that is off has no line, and one the map does not name is on.
    ``expected`` narrows which scheduled organs are required. Every source
    is passed in, so nothing is read from the process's own defaults:
    ``db_path`` is her database (opened read-only) and ``identity_root``
    her repo; either left out is simply not read. Never raises for a store
    it cannot read — the organ's line names it.
    """
    data_root = Path(data_root)
    win = _window(now, boundary_hour)

    def on(organ: str) -> bool:
        return flags is None or bool(flags.get(organ, True))

    required = set(expected) if expected is not None else set(
        DAILY_ORGANS + NIGHTLY_ORGANS + WEEKLY_ORGANS)

    def judged(organ: str) -> bool:
        return on(organ) and organ in required

    night = _due_night(data_root, win)
    rows: Dict[str, int] = {}
    db_why = ""
    if db_path is not None and any(on(o) for o in ("stakes", "views", "regret")):
        rows, db_why = src.stake_rows_since(Path(db_path), win.since)
    db_note = _unreadable("database", db_why)
    report = LivenessReport(checked_at=now)
    add = report.entries.append

    for organ in DAILY_ORGANS:
        if judged(organ):
            add(_daily(organ, data_root, win, night))
    if judged("stakes"):
        add(_stakes(win, rows, night, db_note))
    if judged("views"):
        add(_views(data_root, win, rows, night, db_note))
    if judged("disclosures"):
        add(_disclosures(data_root, win, night))
    if judged("tonight"):
        add(_tonight(data_root, win, night))
    if judged("rest_of_night"):
        add(_rest_of_night(win, night))
    moment = next((e for e in report.entries if e.name == "present_moment"), None)
    if on("surprise") and moment is not None and moment.status == "wrote":
        add(_surprise(data_root, win))
    weekly = _weekly_records(data_root, win)
    for organ in ("narrative_self", "passions"):
        if judged(organ):
            add(_pages(organ, data_root, win, night, weekly, identity_root))
    if judged("callbacks"):
        add(_callbacks(data_root, win, night))
    if judged("fears"):
        add(_fears(data_root, win, night, weekly))
    for organ in OCCASIONED_ORGANS:
        if on(organ):
            add(_occasioned(organ, data_root, win, rows, db_note))
    if on("hands"):
        add(_hands(data_root))
    return report


# ── flags, and an install read from outside ──────────────────────────────

def liveness_flags(settings: Any) -> Optional[Dict[str, bool]]:
    """``{organ: on}`` for every organ, from a settings snapshot (its
    ``companion`` and ``features``), or None when there is none — then
    every organ is expected. An organ is on exactly when the code that
    writes it runs: whatever rides the night pass needs the night cycle
    too (the half-on rule of narrative_self_enabled and passions_enabled),
    the fears re-look rides the story pages' weekly ritual, and inner
    monologue and corrections have no flag at all."""
    if settings is None:
        return None
    companion = getattr(settings, "companion", None)
    features = getattr(settings, "features", None)

    def c(name: str) -> bool:
        return bool(getattr(companion, name, False))

    night, moment, ledger = c("night_cycle"), c("present_moment"), c("stake_ledger")
    story = night and c("narrative_self")
    return {
        "night_cycle": night,
        "day_gists": night and c("consolidation"),
        "present_moment": moment,
        "inner_monologue": True,
        "stakes": night and ledger,
        "views": night and ledger and c("opinions"),
        "disclosures": night and c("vulnerability"),
        "tonight": night and c("tonight"),
        "rest_of_night": night,
        "surprise": moment and c("surprise"),
        "narrative_self": story,
        "passions": night and c("passions"),
        "callbacks": night and c("idiolect"),
        "fears": story and c("fears"),
        "daydream": c("wanting"),
        "workbench": c("own_work"),
        "arbiter": moment and c("arbiter"),
        "corrections": True,
        "forge": bool(getattr(features, "forged_tools", False)),
        "regret": ledger and c("regret"),
        "hands": bool(getattr(features, "her_hands", False)),
    }


def flags_from_yaml(path: Path) -> Optional[Dict[str, bool]]:
    """The flags of the settings file at ``path``, read without seeding or
    booting anything (the doctor's `_own_work_enabled` way). A file that is
    not there or cannot be read gives None — every organ expected — and
    the doctor's config check reports a broken file on its own line."""
    try:
        from core.settings_service import SettingsService

        if not Path(path).is_file():
            return None
        return liveness_flags(SettingsService(yaml_path=Path(path)).load())
    except Exception:
        return None


def run_hours_from_yaml(path: Path) -> float:
    """The night's run time in hours after local midnight (6.5 = 06:30),
    from the settings file at ``path`` the way flags_from_yaml reads it —
    so the doctor's due night turns over when this install's night runs.
    A file that is not there or cannot be read gives the default."""
    try:
        from core.settings_service import SettingsService

        if not Path(path).is_file():
            return DEFAULT_BOUNDARY_HOUR
        companion = getattr(SettingsService(yaml_path=Path(path)).load(), "companion", None)
        if companion is None:
            return DEFAULT_BOUNDARY_HOUR
        # Imported here: the night cycle imports this module's wire-in.
        from ai.night_cycle import night_run_offset

        return night_run_offset(companion).total_seconds() / 3600.0
    except Exception:
        return DEFAULT_BOUNDARY_HOUR


def night_cycle_switched_off(data_root: Path) -> bool:
    """True when the install's settings switch the night cycle off — then
    no night runs, and "no night has run yet" would be said forever. A
    settings file that is not there or cannot be read is not "off" (every
    organ expected, as flags_from_yaml has it)."""
    flags = flags_from_yaml(Path(data_root) / "palli.yaml")
    return flags is not None and not flags.get("night_cycle", True)


def night_has_run(data_root: Path) -> bool:
    """True once any night pass has run on this install: its claim file, a
    reflection or a night's record is there."""
    return (_night_files(data_root)
            or src.newest_dated(_outcomes_dir(Path(data_root)), ".json") is not None)


def _anchored(repo_root: Path, setting: str) -> Path:
    path = Path(setting)
    return path if path.is_absolute() else repo_root / path


def install_report(repo_root: Path, *, now: datetime,
                   data_root: Optional[Path] = None) -> Optional[LivenessReport]:
    """The liveness of the install at ``repo_root``, as a separate process
    (the doctor, the CLI) reads it: that process never changes directory,
    so data/, the database and her repo are anchored at the repo root, as
    the install's settings name them, and the flags and the night's run
    time come from data/palli.yaml. None when no night has run there yet —
    nothing is due.
    """
    from core.config import config

    repo_root = Path(repo_root)
    data_root = Path(data_root) if data_root is not None else repo_root / "data"
    if not night_has_run(data_root):
        return None
    db_setting = str(getattr(config, "DATABASE_PATH", "") or "").strip() or "data/palli-local.sqlite3"
    identity_setting = str(getattr(config, "IDENTITY_ROOT_PATH", "") or "").strip() or "identity"
    return check_liveness(
        now=now,
        data_root=data_root,
        boundary_hour=run_hours_from_yaml(data_root / "palli.yaml"),
        flags=flags_from_yaml(data_root / "palli.yaml"),
        db_path=_anchored(repo_root, db_setting),
        identity_root=_anchored(repo_root, identity_setting) / "palli",
    )
