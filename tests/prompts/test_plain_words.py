"""Plain words (Batch 0, 2026-09-25): no heading she reads may teach the
figurative family. The owner ruled 2026-08-23 ("on her mind", not "holding")
and 2026-09-25 (words people actually use). The list lives HERE, in code,
never in a prompt — naming a tic inside a prompt keeps it salient.

Scanned: every prompts/*.txt heading and every heading-shaped literal in
ai/**/*.py and core/**/*.py. Not scanned (operator-facing, never hers):
prompts/preset_prompts.json and prompts/skills/*.md."""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# Plain "hold" stays out on purpose: it is the impulse's JSON value
# ("kind": "none|say|do|hold"). "you hold" is the prose form and is in.
PHRASE_FAMILY = re.compile(
    r"\b(holding|held|carrying|carried|carry|sitting with|sits with|sit with|"
    r"stood in|stand in|undercurrent|landed|land|the shape of|circling|"
    r"you hold)\b",
    re.IGNORECASE,
)

# A prompt-file heading: an ALL-CAPS run at the start of a line that ends the
# line with ':' (optionally after a parenthetical), or a markdown '## ' line.
# A heading may sit behind a run of {placeholder} slots that collapse to zero
# bytes when their organ is off ("{fears_block}WHAT YOU CARE ABOUT (…):").
_TXT_HEADING = re.compile(
    r"^(?:\{[A-Za-z_]+\})*(?:## .+|[A-Z][A-Z'’ ,\-]{5,}.*:)\s*$"
)
# A heading inside a Python string literal, three shapes:
#   1. "## Heading" — the literal may go on with '\n' escapes and {slots}
#      ("## Heading\n", f"## Heading\n{body}"), so the reply prompt's own
#      headings and the f-string sections are read, not skipped;
#   2. _Source(label="...");
#   3. an ALL-CAPS heading at the start of a literal (optionally after a
#      '\n' escape): two or more caps words then ' (' or ' —', or a caps run
#      then ':'. Tight on purpose — a caps word inside ordinary prose
#      ("RESOLVES (the repair …") or SQL ("SELECT COUNT(*)") is not one.
_CAPS_WORD = r"[A-Z][A-Z'’,\-]*"
_PY_HEADING = re.compile(
    r"\"(## [^\"\\\n]+)(?:\\n|\{[^{}\"\\]*\})*\""
    r"|label=\"([^\"\\\n]+)\""
    r"|\"(?:\\n)?("
    rf"(?:{_CAPS_WORD}(?: {_CAPS_WORD})+ [(—]|[A-Z][A-Z'’ ,\-]{{4,}}:)"
    r"[^\"\\\n]*)"
)

# Every Python file that could render a heading into one of her prompts:
# all of ai/ and core/. The regexes are tight enough that the whole tree
# scans clean of prose and SQL, so no allow-list is needed.
def _py_files() -> list[Path]:
    return sorted(
        p for pattern in ("ai/**/*.py", "core/**/*.py") for p in ROOT.glob(pattern)
    )


def _py_headings(text: str) -> list[str]:
    return [
        m.group(1) or m.group(2) or m.group(3) for m in _PY_HEADING.finditer(text)
    ]


def _headings() -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    for path in sorted((ROOT / "prompts").glob("*.txt")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if _TXT_HEADING.match(line):
                found.append((str(path.relative_to(ROOT)), line))
    for path in _py_files():
        for head in _py_headings(path.read_text(encoding="utf-8")):
            found.append((str(path.relative_to(ROOT)), head))
    return found


def test_scan_finds_headings():
    heads = _headings()
    assert len(heads) > 30, heads  # the scan reads real files, not nothing


def test_no_heading_teaches_the_phrase_family():
    bad = [(p, h) for p, h in _headings() if PHRASE_FAMILY.search(h)]
    assert bad == [], "\n".join(f"{p}: {h}" for p, h in bad)


def test_parenthetical_counts_as_part_of_the_heading():
    assert _TXT_HEADING.match("WHAT YOU WANT (things you're carrying):")
    assert PHRASE_FAMILY.search("WHAT YOU WANT (things you're carrying):")


def test_machine_values_are_not_headings():
    # '"hold" means ...' is prose about a JSON value, never a heading.
    assert not _TXT_HEADING.match('"hold" means something is worth keeping in mind on purpose.')


def test_placeholder_prefixed_caps_heading_is_a_heading():
    line = "{expectations_block}{fears_block}WHAT YOU CARE ABOUT (stakes you hold):"
    assert _TXT_HEADING.match(line)
    assert PHRASE_FAMILY.search(line)
    # a placeholder alone, or prose behind one, is not a heading
    assert not _TXT_HEADING.match("{previous_moment_block}")
    assert not _TXT_HEADING.match("{disclosures_block}Then, separately, your impulse:")


def test_python_caps_heading_is_scanned():
    src = (
        'x = (\n'
        '    "\\nWHAT PALLI ITSELF IS HOLDING (its own open stakes — "\n'
        '    "hopes, worries):\\n"\n'
        ')\n'
        '_HEADER_LINE = (\n'
        '    "YOUR HELD FEARS — yours, in your own words (whether THIS moment "\n'
        ')\n'
        'bases.append("MY OPEN STAKES:\\n" + stakes)\n'
    )
    heads = _py_headings(src)
    assert heads == [
        "WHAT PALLI ITSELF IS HOLDING (its own open stakes — ",
        "YOUR HELD FEARS — yours, in your own words (whether THIS moment ",
        "MY OPEN STAKES:",
    ]
    assert PHRASE_FAMILY.search(heads[0]) and PHRASE_FAMILY.search(heads[1])


def test_python_caps_branch_skips_prose_and_sql():
    for literal in (
        '"RESOLVES (the repair landed, or it settled between you), the resolution "',
        '"SELECT COUNT(*) FROM stakes WHERE status = \'open\'"',
        '"Most turns they stay unspoken. If this moment touches one — a hope "',
        '"If — and only if — this moment GENUINELY touches one, add to your "',
    ):
        assert _py_headings(literal) == [], literal


def test_python_markdown_heading_with_interpolation_is_scanned():
    src = (
        'parts.append(f"## Inner Monologue Digest (last week)\\n{digest}")\n'
        'section = (\n'
        '    "## What you\'re holding (your own stakes)\\n"\n'
        '    "These are yours.\\n"\n'
        ')\n'
    )
    assert _py_headings(src) == [
        "## Inner Monologue Digest (last week)",
        "## What you're holding (your own stakes)",
    ]


def test_you_hold_is_in_the_family_but_the_json_value_is_not():
    assert PHRASE_FAMILY.search("## The fears you hold (yours)")
    assert not PHRASE_FAMILY.search('"kind": "none|say|do|hold"')
    assert not PHRASE_FAMILY.search("views hold no slot")


def test_the_freshness_question_is_asked_in_plain_words():
    """Fix round 1 (R68): "still lands as new" was the family's "land" in
    a line she reads before every message she sends on her own. The same
    question, in plain words; the phrase the arbiter's tests key on stays."""
    text = (ROOT / "prompts" / "arbiter_freshness.txt").read_text(encoding="utf-8")
    assert not re.search(r"\blands?\b|\blanded\b|\blanding\b", text, re.IGNORECASE)
    assert "It is FRESH if it still reads as new\nand worth their attention." in text
    assert "STILL TRUE and STILL WORTH SENDING" in text
