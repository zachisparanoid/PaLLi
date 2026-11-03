"""The reply-length governor — the register fix (2026-08-22).

Before every reply a small cadence call votes a shape: short / medium /
long, and the vote arrives in her prompt WITH its definition ("short —
one or two sentences, a hard shape"). Measured 08-03→08-22 over 272
matched turns: "short" ran median 33 words but a third blew past 50 (p90
123); "medium" ran median 119 and over half blew past 110 (p90 266). A
suggestion she reads is not a budget. Owner's rule: budgets are code.

What this module is:
- the caps (code): short ≤ 50 words, medium ≤ 110, long uncapped;
- the meaning decision (LLM): over the cap → ONE compression call in her
  voice (``compress`` is injected — the adapter binds it to her live
  provider under the human-facing lane ``reply_govern``);
- nothing is ever trimmed (2026-09-07): a failed compression sends the reply
  whole (DEFECT_RECORD §21); a compression that answered but still overshot
  is sent as the shorter text; up to TOLERANCE × cap is not governed at all;
- the shape's travel: the runner sets ``current_reply_shape`` per turn
  (conversation turns only — never her workbench) and the provider
  adapter reads it at the one seam where her final reply exists before
  her session, the Discord chunks and the reflection see it (the custody
  law: all four stay identical);
- the prompt half: ``render_shape_note`` rides at the very END of the
  message list, right before she generates.

Exemptions: no vote → untouched · "long" → untouched · a code block →
untouched (a requested breakdown is never mangled) · not a final reply
(a tool-call iteration) → untouched. Flag: ``companion.reply_governor``
(flag-off byte-identical).
"""
from __future__ import annotations

import contextvars
import logging
import re
from dataclasses import dataclass
from typing import Awaitable, Callable, Optional

logger = logging.getLogger(__name__)
essential_logger = logging.getLogger("palli.essential")

# The caps are code. Words, not sentences: sentence counting lies on
# abbreviations, URLs and lists; words are honest and cheap.
WORD_CAPS = {"short": 50, "medium": 110}

current_reply_shape: contextvars.ContextVar[str] = contextvars.ContextVar(
    "palli_reply_shape", default=""
)

Compressor = Callable[[str, int], Awaitable[Optional[str]]]


def cap_for(length) -> Optional[int]:
    return WORD_CAPS.get(str(length or "").strip().lower())


def word_count(text: str) -> int:
    return len(str(text or "").split())


_FENCE = re.compile(r"```")


def has_code_block(text: str) -> bool:
    return bool(_FENCE.search(str(text or "")))


# DEFECT_RECORD §14 (2026-09-01): the shrink-ray. A 490-word transcript
# compressed to 40; a 535-word correction to 26 — and inverted. A reply that
# overshoots its cap by this factor is proof the VOTE was wrong, not the
# reply: it passes through untouched. Code, not judgment (owner rule 07-16).
OVERSHOOT_FLOOR = 3.0
# 2026-09-07 (his call): the tolerance band. A reply up to TOLERANCE × the cap
# passes untouched — 121 words against a 110 cap is not a register failure, and
# on 09-03 16:55 governing it cost the last sentence. Above the band, one
# compression call; below the 3× floor.
TOLERANCE = 1.35


def needs_governing(text: str, length) -> bool:
    cap = cap_for(length)
    if cap is None or not text:
        return False
    if has_code_block(text):
        return False
    n = word_count(text)
    if n >= cap * OVERSHOOT_FLOOR:
        import logging
        logging.getLogger("palli.essential").info(
            "reply_govern: overshoot floor — %d words vs cap %d (%s); passing through "
            "untouched, the vote was wrong, not the reply", n, cap, str(length).lower(),
        )
        return False
    if n <= cap * TOLERANCE:
        return False          # inside the tolerance band: her words as written
    return n > cap


_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


def trim_to_cap(text: str, cap: int) -> str:
    """Keep whole sentences while they fit under ``cap`` words; if even the
    first sentence overshoots, hard-cut at the cap and mark it (…)."""
    text = str(text or "").strip()
    kept: list[str] = []
    total = 0
    for sentence in _SENTENCE_END.split(text):
        n = word_count(sentence)
        if total + n > cap:
            break
        kept.append(sentence)
        total += n
    if kept:
        return " ".join(kept).strip()
    words = text.split()
    return " ".join(words[:cap]).rstrip(" ,;:—-") + "…"


@dataclass(frozen=True)
class GovernResult:
    text: str
    action: str          # kept | compressed | trimmed | kept_whole
    length: str
    before_words: int
    after_words: int


async def govern_reply(text: str, length, *, compress: Compressor) -> GovernResult:
    """Return the reply as it should be sent. Never raises."""
    before = word_count(text)
    cap = cap_for(length)
    if not needs_governing(text, length):
        return GovernResult(text=text, action="kept", length=str(length or ""), before_words=before, after_words=before)
    # DEFECT_RECORD §21 (2026-09-02): the reply as she wrote it survives in
    # the debug log whatever happens to it below.
    logger.debug("reply_govern: before (%d words, cap %d, %s): %s", before, cap, str(length).lower(), text)
    candidate: Optional[str] = None
    failed = False
    try:
        candidate = await compress(text, cap)
    except Exception as exc:
        failed = True
        logger.warning("reply_govern: compression failed (%s); sending the reply whole", exc)
    if candidate and candidate.strip() and not has_code_block(candidate) and word_count(candidate) <= cap:
        return GovernResult(text=candidate.strip(), action="compressed", length=str(length),
                            before_words=before, after_words=word_count(candidate))
    if failed or not (candidate and candidate.strip()):
        # DEFECT_RECORD §21 (2026-09-02): the meaning half failed (an empty
        # answer, a timeout) — the presentation half does not get to guess.
        # On 09-01 three failed calls ended in a sentence-boundary trim that
        # cut a 310-word reply to 83. The reply goes out whole; the vote
        # is logged as unenforced.
        return GovernResult(text=text, action="kept_whole", length=str(length),
                            before_words=before, after_words=before)
    # 2026-09-07 (his call): compression answered but still overshoots. Four
    # trims since 09-03 each took the last sentence — the punchline. Send
    # whichever is shorter, whole; nothing is cut at a sentence boundary.
    cand = candidate.strip()
    if not has_code_block(cand) and word_count(cand) < before:
        return GovernResult(text=cand, action="compressed", length=str(length),
                            before_words=before, after_words=word_count(cand))
    return GovernResult(text=text, action="kept_whole", length=str(length),
                        before_words=before, after_words=before)


# Draft 3 (R14, T5; awaiting the owner's word-for-word answer, Q11): when the
# first look the runner took said let it pass or wait (the owner's turn, or a
# friend's DM: the coordinator's ruling after T5), nothing at the end
# of the reply prompt pushes her to talk against it (10-12 17:56: "This one
# wants a short answer" ended the prompt and she wrote "Standing by." into a
# talk between the owner and a friend that her reads had called not hers).
# The length note says what it limits only if she answers, and her own look
# is the very last line (variant A). Each text once, here.
LOOK_HOLDS_BACK = ("stay_quiet", "wait")
IF_I_ANSWER_SHORT = "(If I do answer, it's short: a line or two, under {cap} words. {limit})"
IF_I_ANSWER_MEDIUM = "(If I do answer, a few sentences fit: one small paragraph, under {cap} words. {limit})"
LOOK_LINES = {
    "stay_quiet": "(My first look: let it pass — {reason}.)",
    "wait": "(My first look: this looks unfinished, more may be coming — {reason}.)",
}
# The owner, 10-13 (Fable's text, approved word for word): a friend's reply
# call, while the owner's room note stands and her first look said answer,
# ends with this line. The look read the note with its words; her reply sees
# only the neutral line, so it says the note was weighed. Never beside a
# look line (that look held back), so "her look is the last line" holds.
LOOK_READ_THE_NOTE = "(My first look read the owner's note and said answer.)"


def render_look_line(decision, reason: str = "") -> str:
    """Her first look's own line, the last of the trailing note, after a
    look that let it pass or waited; "" after any other. The reason is the
    look's own, on one line (a closing full stop is the line's own; a
    question or exclamation mark is the reason's, and stays)."""
    template = LOOK_LINES.get(str(decision or "").strip().lower())
    if template is None:
        return ""
    words = " ".join(str(reason or "").split()).rstrip(".").rstrip()
    if not words:
        return template.replace(" — {reason}", "")
    if words[-1] in "!?…":
        # Her reason's own end mark stays and the line adds no full stop to
        # it (Fable's review, 10-13: "why would I?." never reaches her).
        return template.replace("{reason}.)", "{reason})").format(reason=words)
    return template.format(reason=words)


def render_shape_note(length, *, tools: bool = True, look: str | None = None) -> str:
    """The prompt half: one plain note at the END of the messages, right
    before she generates. It rides in the same trailing system message as
    the volatile tail (the cadence directive included), so short and medium
    are said here and only here. The number comes from :data:`WORD_CAPS`
    (the governor enforces it in code); only the words are prose. Empty for
    long/unvoted turns (nothing to add). ``look`` is the decision of the
    first look the runner took (R14): after "stay_quiet" or "wait" the
    note is Draft 3's conditional form; anything else, today's note."""
    cap = cap_for(length)
    if cap is None:
        return ""
    # 2026-09-02 (the desktop-Qwen evening): a small model follows the LAST hard
    # instruction — "short" read as "answer in one line, don't act", and she typed a
    # fake "[Attachment: …]" instead of calling deliver_artifact with 23 tools on
    # offer. The note says what it governs: her words, never whether she acts.
    # Batch 7, her voice (audit 4.9): one plain sentence, no machine form.
    # Step 2, add-on (a): a turn that offers no tools (a guest's) is never
    # told they are allowed; the limit is still on her words.
    limit = ("The limit is on my words; tools are always allowed." if tools
             else "The limit is on my words.")
    if str(look or "").strip().lower() in LOOK_HOLDS_BACK:
        # R14 (Draft 3): her look let it pass or waited — if she answers.
        template = IF_I_ANSWER_SHORT if str(length).lower() == "short" else IF_I_ANSWER_MEDIUM
        return template.format(cap=cap, limit=limit)
    if str(length).lower() == "short":
        return f"(This one wants a short answer — a line or two, under {cap} words. {limit})"
    return f"(A few sentences fit here — one small paragraph, under {cap} words. {limit})"


def governor_enabled() -> bool:
    try:
        from core.turn.companion_flags import companion_settings

        comp = companion_settings()
        return comp is not None and bool(getattr(comp, "reply_governor", False))
    except Exception:
        return False


_COMPRESS_FALLBACK = (
    "You are PaLLi, compressing your own reply before it is sent. Keep the one thing that matters, "
    "your own voice and first person, the same warmth and the same facts. No preamble, no summary of "
    "what you cut, no quotation marks. At most <<CAP>> words (<<LENGTH>>). Reply with the compressed "
    "text only."
)


def compression_prompt(cap: int, length: str) -> str:
    template = ""
    try:
        from ai.prompt_loader import prompt_loader

        template = prompt_loader.load_prompt("reply_govern") or ""
    except Exception:
        template = ""
    return (template or _COMPRESS_FALLBACK).replace("<<CAP>>", str(cap)).replace("<<LENGTH>>", str(length))


def log_governed(result: GovernResult) -> None:
    if result.action == "kept":
        return
    if result.action == "kept_whole":
        essential_logger.info(
            "reply_govern: compression failed — sent whole, %d words vs the %s cap (DEFECT_RECORD §21)",
            result.before_words, result.length,
        )
        return
    essential_logger.info(
        "reply_govern: %s %d→%d words (%s)", result.length, result.before_words, result.after_words, result.action
    )
