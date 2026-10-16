"""What PaLLi is in the middle of, for the moments it talks while it works.

The owner, 2026-10-12: PaLLi acknowledges and chats about the work in its
own words ("palli is its own entity and should be able to converse and
acknowledge with whatever language it sees best fit"). This module holds
the facts it needs for that: what was asked, what ran and what came back,
what is running now, how long it has been. It never holds words for PaLLi
to say.

One writer, the runner (``core.agent.steering.set_work_progress``). The
readers (the mid-task judge, the talk sidecar's parent snapshot, a pause)
get the text from ``steering.get_now_doing``, rendered when it is read so
the elapsed time is true at that moment."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Sequence

ASKED_CHARS = 400
NOTE_CHARS = 300
RECENT_ROWS = 6
RECENT_ROW_CHARS = 300
RENDER_MAX_CHARS = 3000
PAUSED_NOTE_CHARS = 500


def squash(text: Any, limit: int) -> str:
    """One line of at most ``limit`` characters; an ellipsis marks a cut."""
    one = " ".join(str(text or "").split())
    return one if len(one) <= limit else one[: max(0, limit - 1)] + "…"


def elapsed_words(seconds: float) -> str:
    s = max(0, int(seconds))
    if s < 60:
        return f"{s} s"
    if s < 3600:
        return f"{s // 60} min {s % 60} s"
    return f"{s // 3600} h {(s % 3600) // 60} min"


@dataclass(frozen=True)
class WorkProgress:
    asked: str                 # what they asked for, this run's words
    started_at: float          # time.monotonic() when the run started
    step: int = 0              # model calls made so far
    done: str = ""             # promise_gate.render_digest of the calls so far
    running: str = ""          # the calls running right now, one "- name(input)" line each
    note: str = ""             # what PaLLi wrote beside its latest calls (a working note)
    recent: str = ""           # the conversation just before this run

    def render(self, now: float) -> str:
        steps = f"{self.step} model step" + ("" if self.step == 1 else "s")
        head = [
            "What they asked: " + (squash(self.asked, ASKED_CHARS) or "(no words; a file or a picture)"),
            f"Working on it for {elapsed_words(now - self.started_at)}; {steps} so far.",
        ]
        tail = ["Running right now:\n" + self.running if self.running
                else "Running right now: nothing; between steps, deciding what comes next"]
        if self.note:
            tail.append("My latest working note: " + squash(self.note, NOTE_CHARS))
        lines = head + ([self._done_block(RENDER_MAX_CHARS - len("\n".join(head + tail)) - 1)]
                        if self.done else []) + tail
        text = "\n".join(lines)
        return text if len(text) <= RENDER_MAX_CHARS else text[: RENDER_MAX_CHARS - 1] + "…"

    def _done_block(self, budget: int) -> str:
        """The steps done, newest kept first when they do not all fit
        (Fable's review, 10-12: a long run must keep its latest steps)."""
        rows = self.done.splitlines()
        title = "Done so far:"
        kept: list[str] = []
        used = len(title) + 40                     # room for the "not shown" line
        for row in reversed(rows):
            if used + len(row) + 1 > budget:
                break
            kept.append(row)
            used += len(row) + 1
        kept.reverse()
        dropped = len(rows) - len(kept)
        if dropped:
            title += f"\n({dropped} earlier step{'s' if dropped != 1 else ''} not shown)"
        return "\n".join([title] + kept)

    def paused_note(self) -> str:
        """One line for the pause slots (the judge's, the resume and the
        paused-task notes): what was asked and the last step done. No
        durations, so it stays true however long the pause lasts (Fable's
        review, 10-12)."""
        parts = ["what they asked: " + (squash(self.asked, ASKED_CHARS // 2) or "(no words)")]
        last = (self.done.splitlines() or [""])[-1].lstrip("- ").strip()
        if last:
            parts.append("last done: " + squash(last, 200))
        return squash("; ".join(parts), PAUSED_NOTE_CHARS)


def running_lines(tool_uses: Iterable[Any]) -> str:
    """The calls about to run, one bounded line each, in the digest's format."""
    from core.agent.promise_gate import digest_entry
    out = []
    for tu in tool_uses:
        e = digest_entry(getattr(tu, "name", ""), getattr(tu, "input", None), "", False)
        out.append(f"- {e.name}({e.input})")
    return "\n".join(out)


def recent_exchange(messages: Sequence[Any], *, limit: int = RECENT_ROWS) -> str:
    """The last few things said before this run, oldest first: PaLLi's own
    lines as "me", the owner's as "them", a guest's as "someone else". Tool
    rows and bare tool calls carry no words and are skipped. Walks from the
    end, so a 13,000-row room costs only the rows it keeps."""
    from core.agent.session import TextBlock
    rows: list[str] = []
    for m in reversed(messages):
        role = getattr(m, "role", "")
        if role not in ("user", "assistant"):
            continue
        text = " ".join(b.text for b in (getattr(m, "content", None) or [])
                        if isinstance(b, TextBlock)).strip()
        if not text:
            continue
        if role == "assistant":
            who = "me"
        else:
            kind = str((getattr(m, "speaker", None) or {}).get("kind") or "")
            who = "someone else" if kind == "guest" else "them"
        rows.append(f"{who}: {squash(text, RECENT_ROW_CHARS)}")
        if len(rows) >= limit:
            break
    return "\n".join(reversed(rows))
