"""Her memory of them, in her own voice (Batch 5, Task 10, 2026-09-28).

A line of MEMORY.md is PaLLi's memory of the person she talks with. For a
long time the reflection wrote those lines as case notes — "User is working
on…", "PaLLi told the user…" — and the file read like a chart about a
patient. From now on every line is written as PaLLi about them: "They'd
rather I…", "They told me…", "They're working on…". The reflection prompt
asks for that directly; this module is the belt behind the prompt and the
engine of the one-time migration (scripts/memory_first_person.py).

What is code and what is the model's (ruling R19): the SHAPE is detected in
code — ``is_third_person`` is a plain regex, no judgment of meaning — and the
WORDING is the model's, asked for through ``build_rewrite_messages`` and read
back through ``parse_rewrite_reply`` under a strict contract (one line per
input line, same count, same order, same ``- `` and leading stamps). What a
rewrite may not change is guarded in code (ruling R52): ``guard`` compares
the multiset of fact tokens — proper names, ISO dates, ``[8-hex]`` ids, paths
and file names, URLs, emails, numbers (money and percent tokens too) and
double-quoted spans — between the old line and the new, and a rewrite that
moves any of them is refused; the caller keeps the line as written. Names
(fix round 1, ruling R58): every capitalized word of the old line must
survive in the new one — a mid-sentence word as a whole word, a sentence
opener of four letters or more by its four-letter stem (so it may be
inflected); a capitalized word added mid-sentence is refused.
A refusal's reason names token kinds and counts, never a value.

A leaf on purpose (ruling R57): ``re`` and the bullet grammar only. Both
core/turn/reflect.py (one call per reflection, over the NEW lines) and the
migration script (batches over the whole file) import it, so there is one
source of truth for the shape, the guard and the protocol.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from typing import Mapping, Optional

from core.memory_bullets import _TEMPLATE_PLACEHOLDER_BULLETS, _split_bullet

# --- the instruction (the only prompt text of this feature; ruling R57) ------

INSTRUCTION = (
    "You are PaLLi. The lines below come from your own memory of the person you "
    "talk with, and they are written like case notes — \"User is…\", \"The user "
    "said…\", \"PaLLi told the user…\". Rewrite each line in your own voice about "
    "them: they are \"they/them\" (\"They'd rather I…\", \"They told me…\", "
    "\"They're working on…\") and you are \"I/me/my\". Never \"User\", never "
    "\"the user\", never \"PaLLi\" in the third person.\n"
    "\n"
    "Change the wording only. Every fact stays exactly as written, byte for byte: "
    "the names of people and things, dates, [8-character] ids, file paths and "
    "file names, web addresses, email addresses, numbers (money, percentages, "
    "counts, times), anything inside double quotes and anything inside backticks "
    "(a `code span` stays exactly as it is). Keep the meaning and about "
    "the same length. Do not add, drop, merge, split or reorder lines, and do not "
    "add anything a line does not say.\n"
    "\n"
    "Reply with the rewritten lines only: one line for each input line, in the "
    "same order, each starting with the same \"- \" and the same leading "
    "[YYYY-MM-DD] stamps as its input. No numbering, no commentary, no code fences."
)

# --- the shape ------------------------------------------------------------------

_QUOTED_RE = re.compile(r"\"[^\"\n]+\"|“[^”\n]+”")
# "User …", "User's …", "The user …" at the start of the body (any case); the
# word must end there — "Username" is not "User".
_THIRD_PERSON_START_RE = re.compile(r"^(?:the\s+)?user(?:['’]s)?(?=[\s:,;.!?]|$)", re.IGNORECASE)
# PaLLi named as a word, in her own spelling ("palli.yaml" is a file, not her).
_PALLI_WORD_RE = re.compile(r"\bPaLLi\b")


def is_third_person(body: str) -> bool:
    """Does this memory line read as a case note about them? A shape check
    only: the body (the whole line or the bare text — the ``- `` and any
    leading ``[YYYY-MM-DD]`` stamps are dropped first) starts with "User ",
    "User's" or "The user " in any case, or names PaLLi as a word outside
    double quotes (quoted text is verbatim and is never rewritten)."""
    _stamp, text, _key = _split_bullet(body)
    if not text:
        return False
    if _THIRD_PERSON_START_RE.match(text):
        return True
    return bool(_PALLI_WORD_RE.search(_QUOTED_RE.sub(" ", text)))


# --- the guard (ruling R52; fix round 1) -----------------------------------------

# One scan, first alternative wins at each position, so a file name inside a
# path, a domain inside a URL or an address inside an email is one token.
# Paths: POSIX (~/, /, ./, ../), Windows drive (C:\…, a trailing backslash
# allowed) and UNC (\\host\share); none ends in a sentence's period.
_TOKEN_RE = re.compile(
    r"(?P<url>(?:https?://|www\.)[^\s\"“”‘’<>()\[\]]*[^\s\"“”‘’<>()\[\].,;:!?])"
    r"|(?P<email>[\w.+-]+@[\w-]+(?:\.[\w-]+)+)"
    r"|(?P<winpath>[A-Za-z]:\\(?:[^\s\"“”‘’()\[\];,]*[^\s\"“”‘’()\[\];,.])?"
    r"|\\\\[^\s\"“”‘’()\[\];,]*[^\s\"“”‘’()\[\];,.])"
    r"|(?P<path>(?<![\w])(?:~|\.{1,2})?/[^\s\"“”‘’()\[\];,]*[^\s\"“”‘’()\[\];,.])"
    r"|(?P<quoted>\"[^\"\n]+\"|“[^”\n]+”)"
    r"|(?P<code>`[^`\n]+`)"
    r"|(?P<id>\[[0-9a-f]{8}\])"
    r"|(?P<date>\d{4}-\d{2}-\d{2})"
    r"|(?P<filename>[\w-]+(?:\.[\w-]+)*\.[A-Za-z][A-Za-z0-9]{0,5}\b)"
    r"|(?P<alnum>[A-Za-z_][\w-]*\d[\w-]*)"
    r"|(?P<number>[$€£]?\d[\d,]*(?:\.\d+)?(?:%|[A-Za-z]{1,3}\b)?)"
    # a name starts at a word start; a lowercase-led camelCase word (macOS,
    # iPhone) is ONE token — an inner capital run must never be a "word"
    r"|(?P<name>(?<![A-Za-z0-9])(?:[a-z]+[A-Z][A-Za-z]*|[A-Z][A-Za-z]+))"
)
# What a refusal names — the KIND of token, never its value (a value can be a
# secret the provider seam masked and the model echoed back).
_KIND_LABEL = {
    "url": "url", "email": "email", "winpath": "path", "path": "path",
    "quoted": "quoted span", "code": "code span", "id": "id", "date": "date",
    "filename": "file name", "alnum": "word with a digit", "number": "number",
    "name": "name",
}
# Capitalized words the rewrite is MEANT to change or that carry no fact: the
# two subjects, pronouns, articles, demonstratives and the small connectives
# that open a clause. Every other capitalized word is a name.
_NOT_A_NAME = frozenset({
    "User", "Users", "PaLLi", "Palli", "PALLI",
    "They", "Them", "Their", "Theirs", "Themselves",
    "He", "Him", "His", "Himself", "She", "Her", "Hers", "Herself",
    "It", "Its", "Itself", "We", "Us", "Our", "Ours", "Me", "My", "Mine",
    "You", "Your", "Yours", "Yourself",
    "The", "An", "This", "That", "These", "Those", "There", "Here",
    "And", "But", "Or", "So", "Not", "No", "Yes", "Also", "Then", "When",
    "If", "Because", "Since", "While", "As", "At", "In", "On", "For", "To",
    "Of", "With", "By", "From", "Into", "Over", "Under", "About", "After",
    "Before", "Until", "Than", "Though", "Although", "Whether",
})
_SENTENCE_END = (".", "!", "?")
_FRAME_RE = re.compile(r"^\s*-\s+((?:\[\d{4}-\d{2}-\d{2}\]\s*)*)")
_STAMP_IN_FRAME_RE = re.compile(r"\[(\d{4}-\d{2}-\d{2})\]")
_A_LETTER_RE = re.compile(r"[^\W\d_]")


def _scan(text: str):
    """Every guarded token of a body as (kind, value, is_opener): the token
    classes above, with a one-char "e.g."-style stem-and-extension dropped
    from the file names and the words of ``_NOT_A_NAME`` dropped from the
    names. ``is_opener`` is set on a name that starts the body or follows
    ". ", "! " or "? " — a sentence opener."""
    for match in _TOKEN_RE.finditer(text or ""):
        kind, value = match.lastgroup, match.group(0)
        if kind == "filename":
            stem, _dot, ext = value.rpartition(".")
            if len(stem) == 1 and len(ext) == 1:  # "e.g.", "a.m.", "U.S." — prose, not a file
                continue
        opener = False
        if kind == "name":
            if value in _NOT_A_NAME:
                continue
            before = text[: match.start()].rstrip()
            opener = not before or before.endswith(_SENTENCE_END)
        yield kind, value, opener


def guarded_tokens(text: str) -> Counter:
    """The multiset of fact tokens in a line's body, as (kind label, value)
    pairs — everything but the names, which have their own rule (below)."""
    return Counter((_KIND_LABEL[kind], value) for kind, value, _opener in _scan(text) if kind != "name")


def _has_whole_word(word: str, text: str) -> bool:
    return re.search(r"(?<![a-z0-9])" + re.escape(word.lower()) + r"(?![a-z0-9])", text.lower()) is not None


def _shares_a_stem(word: str, text: str) -> bool:
    """Ruling R58: does ``text`` hold a word equal to ``word`` or sharing its
    first four letters, case-insensitively? ("Wants" survives as "want",
    "Prefers" as "prefer"; "Northwind" does not survive as "Contoso".)"""
    stem = word[:4].lower()
    return any(w.startswith(stem) for w in re.findall(r"[A-Za-z]+", text.lower()))


def _survives(value: str, opener: bool, text: str) -> bool:
    if opener and len(value) >= 4:
        return _shares_a_stem(value, text)
    return _has_whole_word(value, text)


def _name_faults(old_body: str, new_body: str, allowed: tuple[str, ...] = ()) -> tuple[int, int]:
    """(missing, added) — the name rule (fix round 1, ruling R58). Every
    capitalized word of the OLD body must still appear in the new body,
    case-insensitively: a mid-sentence word (a name: "Dr. Patel", "Northwind",
    "Mochi") as a whole word; a SENTENCE OPENER of four letters or more as a
    word equal to it or sharing its first four letters, so the rewording
    may inflect it ("Wants…" → "They want…", "Prefers…" → "They prefer…",
    "Decided…" → "They decided…") while "Northwind sent" → "Contoso sent" and
    "Dr. Patel" → "Dr. Jones" still fail (no four-letter stem shared); an
    opener shorter than four letters keeps the whole-word rule. A
    capitalized word the NEW body adds is a fault when it sits mid-sentence
    — an opener the rewording introduced ("…tea. Especially in the
    evening.") is grammar, not a new fact."""
    missing = sum(
        1 for kind, value, opener in _scan(old_body)
        if kind == "name" and not _survives(value, opener, new_body)
    )
    allowed_lower = {a.lower() for a in allowed if a}
    added = sum(
        1 for kind, value, opener in _scan(new_body)
        if kind == "name" and not opener and not _has_whole_word(value, old_body)
        and value.lower() not in allowed_lower
    )
    return missing, added


def _split_frame(line: str) -> tuple[Optional[list[str]], str]:
    """``"- [d1] [d2] body"`` -> (["d1", "d2"], "body"); (None, line) when the
    line is not a ``- `` bullet at all."""
    match = _FRAME_RE.match(line or "")
    if not match:
        return None, line or ""
    stamps = _STAMP_IN_FRAME_RE.findall(match.group(1))
    return stamps, (line or "")[match.end():]


def _counts(counter: Counter) -> str:
    return ", ".join(f"{n} × {label}" for label, n in sorted(counter.items()))


def guard_failure(old_line: str, new_line: str, *, allowed_names: tuple[str, ...] = ()) -> Optional[str]:
    """Why a rewritten line must NOT replace the original, in plain words —
    or None when it may. The new line must start with ``- ``, carry the same
    leading ``[YYYY-MM-DD]`` stamps in the same order, have a body with at
    least one word, hold exactly the same fact tokens (``guarded_tokens``)
    as the old, and keep every name (``_name_faults``). The reason names
    token KINDS and COUNTS only — never a value."""
    old_stamps, old_body = _split_frame(old_line)
    new_stamps, new_body = _split_frame(new_line)
    if new_stamps is None:
        return 'the line does not start with "- "'
    if (old_stamps or []) != new_stamps:
        return "the leading date stamps changed"
    if not new_body.strip():
        return "the new line is empty"
    if not _A_LETTER_RE.search(new_body):
        return "the new line has no words"
    old_tokens, new_tokens = guarded_tokens(old_body), guarded_tokens(new_body)
    missing = Counter(label for label, _value in (old_tokens - new_tokens).elements())
    added = Counter(label for label, _value in (new_tokens - old_tokens).elements())
    names_missing, names_added = _name_faults(old_body, new_body, allowed_names)
    if names_missing:
        missing["name"] += names_missing
    if names_added:
        added["name"] += names_added
    if missing or added:
        parts = []
        if missing:
            parts.append("missing: " + _counts(missing))
        if added:
            parts.append("added: " + _counts(added))
        return "the facts changed — " + "; ".join(parts)
    return None


def guard(old_line: str, new_line: str, *, allowed_names: tuple[str, ...] = ()) -> bool:
    """True when ``new_line`` may replace ``old_line``: same frame, same facts.
    ``allowed_names``: names the rewrite may introduce (her person's, once
    known) without counting as a new fact."""
    return guard_failure(old_line, new_line, allowed_names=allowed_names) is None


# --- the person (2026-09-29, owner call): her name for them once she knows it ---

_PRONOUN_SETS = {
    "he/him": ("he", "him", "his"),
    "she/her": ("she", "her", "her"),
    "they/them": ("they", "them", "their"),
}
_STAND_IN_NAMES = frozenset({"", "user", "the user", "owner", "operator", "local operator"})
_NEUTRAL_SENTENCE = (
    'they are "they/them" ("They\'d rather I…", "They told me…", '
    '"They\'re working on…") and you are "I/me/my"'
)


@dataclass(frozen=True)
class Person:
    """Who her memory lines are about: a name once she knows it (else ""),
    and the pronouns to use ("he/him", "she/her" or "they/them"). A fresh
    install knows no name, so every shipped prompt renders the neutral
    wording; an owner who has told her their name gets it in every line."""
    name: str = ""
    pronouns: str = "they/them"

    @property
    def known(self) -> bool:
        return bool(self.name)

    @property
    def words(self) -> tuple[str, str, str]:
        return _PRONOUN_SETS.get(self.pronouns, _PRONOUN_SETS["they/them"])


def person(name: str = "", pronouns: str = "") -> Person:
    n = " ".join(str(name or "").split())
    if n.lower() in _STAND_IN_NAMES:
        n = ""
    pr = str(pronouns or "").strip().lower()
    if pr not in _PRONOUN_SETS:
        pr = "they/them"
    return Person(name=n, pronouns=pr)


def person_sentence(who: Optional[Person] = None) -> str:
    """The one sentence of the instruction that says who "they" are."""
    who = who or Person()
    if not who.known:
        return _NEUTRAL_SENTENCE
    subj, obj, poss = who.words
    if who.pronouns == "they/them":
        return (
            f'they are {who.name} — say "{who.name}" when a line names them and "they/them" after '
            f'("{who.name} would rather I…", "{who.name} told me…", "They\'re working on…") and you are "I/me/my"'
        )
    return (
        f'they are {who.name} — say "{who.name}" when a line names them and "{subj}/{obj}" after '
        f'("{who.name} would rather I…", "{who.name} told me…", "{subj.capitalize()}\'s working on…") and you are "I/me/my"'
    )


def build_instruction(who: Optional[Person] = None) -> str:
    return INSTRUCTION.replace(_NEUTRAL_SENTENCE, person_sentence(who))


def short_rule(who: Optional[Person] = None) -> str:
    """The one-paragraph rule for the reflection prompt's extraction rules."""
    who = who or Person()
    if not who.known:
        return (
            'Write every memory line in PaLLi\'s own voice about them — "They\'d rather I…",\n'
            '"They told me…", "They\'re working on…" — never "User…", never "PaLLi" in the third person.'
        )
    subj, obj, poss = who.words
    return (
        f'Write every memory line in PaLLi\'s own voice about {who.name} — "{who.name} would rather I…",\n'
        f'"{who.name} told me…", "{subj.capitalize()}\'s working on…" — by name, then "{subj}/{obj}"; '
        'never "User…", never "PaLLi" in the third person.'
    )


def tone_rule(who: Optional[Person] = None) -> str:
    """The "Tone of memory entries" block of the reflection prompt."""
    who = who or Person()
    if not who.known:
        return (
            "These lines are PaLLi\'s own memory of them, and they will read them. Write\n"
            'every one in PaLLi\'s own voice about them: they are "they/them", PaLLi is\n'
            '"I/me/my" — never "User", never "the user", never "PaLLi" in the third person.\n'
            "Examples:\n\n"
            '- "They\'re working on a Rust project called Atlas." (good)\n'
            '- "User is working on a Rust project called Atlas." (bad)\n'
            '- "I\'m working on a Rust project called Atlas." (bad — "I" is PaLLi, not them)\n'
            '- "They dropped the meditation habit; their schedule was too inconsistent." (good)\n'
            '- "User dropped the meditation habit; cited inconsistent schedule." (bad)\n'
            '- "I told them the backup ran clean last night." (good)\n'
            '- "PaLLi told the user the backup ran clean last night." (bad)'
        )
    subj, obj, poss = who.words
    S = subj.capitalize()
    return (
        f"These lines are PaLLi\'s own memory of {who.name}, and {who.name} will read them. Write\n"
        f'every one in PaLLi\'s own voice about {who.name}: by name, then "{subj}/{obj}"; PaLLi is\n'
        '"I/me/my" — never "User", never "the user", never "PaLLi" in the third person.\n'
        "Examples:\n\n"
        f'- "{who.name} is working on a Rust project called Atlas." (good)\n'
        '- "User is working on a Rust project called Atlas." (bad)\n'
        '- "I\'m working on a Rust project called Atlas." (bad — "I" is PaLLi, not them)\n'
        f'- "{S} dropped the meditation habit; {poss} schedule was too inconsistent." (good)\n'
        '- "User dropped the meditation habit; cited inconsistent schedule." (bad)\n'
        f'- "I told {obj} the backup ran clean last night." (good)\n'
        '- "PaLLi told the user the backup ran clean last night." (bad)'
    )


# --- the batch protocol ---------------------------------------------------------

def build_rewrite_messages(lines: list[str], *, who: Optional[Person] = None) -> list[dict]:
    """The one call's messages: the instruction as the system turn, the lines
    to rewrite (whole bullets, ``- `` and stamps included) one per line as
    the user turn, in the order the reply must keep."""
    return [
        {"role": "system", "content": build_instruction(who)},
        {"role": "user", "content": "\n".join(lines)},
    ]


def parse_rewrite_reply(text: Optional[str], expected_count: int) -> Optional[list[str]]:
    """The reply's bullet lines, or None when the contract is broken. Blank
    lines and code fences are ignored; text before the first bullet is a
    preamble and ignored too; anything else that is not a bullet after the
    first one (a wrapped line, a closing remark) breaks the contract, as does
    any count other than ``expected_count``."""
    if not text:
        return None
    lines: list[str] = []
    for raw in str(text).splitlines():
        line = raw.strip()
        if not line or line.startswith("```"):
            continue
        if line.startswith("- "):
            lines.append(line)
        elif lines:
            return None
    if len(lines) != expected_count:
        return None
    return lines


# --- the candidates and the write-back ------------------------------------------

@dataclass(frozen=True)
class Candidate:
    """A bullet a rewrite may touch: its line index in the document (split at
    "\\n"), the ``## `` section it sits under, and the line itself — stripped
    of leading whitespace and any trailing "\\r", the way it is sent."""
    index: int
    section: str
    line: str


def plan_candidates(text: str, *, previous: str = "") -> list[Candidate]:
    """Every bullet of ``text`` that reads as a case note and may be
    rewritten: a ``- `` line that ``is_third_person``, is not one of the
    templates' placeholder bullets, and is not already in ``previous`` (a
    line on disk is never touched — the reflection passes the document as
    it was before this turn; the migration passes nothing). ``previous`` is
    read both ways — split at "\\n" and at every ``splitlines()`` boundary —
    like the memory belt, so a line holding U+2028 is not mistaken for new."""
    on_disk: set[str] = set()
    if previous:
        on_disk = {ln.rstrip("\r").strip() for ln in previous.split("\n")} | {
            ln.strip() for ln in previous.splitlines()
        }
    out: list[Candidate] = []
    section = ""
    for index, part in enumerate((text or "").split("\n")):
        line = part.rstrip("\r").strip()
        if line.startswith("## "):
            section = line[3:].strip()
            continue
        if not line.startswith("- ") or line in on_disk:
            continue
        _stamp, body, key = _split_bullet(line)
        if not body or key in _TEMPLATE_PLACEHOLDER_BULLETS:
            continue
        if not is_third_person(body):
            continue
        out.append(Candidate(index=index, section=section, line=line))
    return out


def replace_lines(text: str, replacements: Mapping[int, str]) -> str:
    """``text`` with the lines at the given indexes (split at "\\n") replaced
    by the new lines; every other byte, each replaced line's indentation and
    its trailing "\\r" are kept as they were."""
    if not replacements:
        return text
    parts = (text or "").split("\n")
    for index, new_line in replacements.items():
        part = parts[index]
        body = part.rstrip("\r")
        lead = body[: len(body) - len(body.lstrip())]
        parts[index] = lead + str(new_line).strip() + ("\r" if part.endswith("\r") else "")
    return "\n".join(parts)


__all__ = [
    "INSTRUCTION",
    "Candidate",
    "build_rewrite_messages",
    "guard",
    "guard_failure",
    "guarded_tokens",
    "is_third_person",
    "parse_rewrite_reply",
    "plan_candidates",
    "replace_lines",
]
