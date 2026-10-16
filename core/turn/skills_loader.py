"""Load the skill catalog at prompts/skills/ for inclusion in her prompt.

Each skill is a markdown file with a small YAML-shape frontmatter. The
loader is permissive — missing fields fall back to defaults, malformed
files are skipped with a warning rather than failing the whole catalog.

The output is a single string that rides in her stable system prompt as a
`<capabilities>` block (owner only). Each skill renders as:

  - git_inspect (needs: git)
    when: ...
    examples in: prompts/skills/git_inspect.md

The cards' bodies are not rendered: she opens a card herself (read_file)
at the path the block names. ``render_skill_full`` returns one card's body
by name; nothing calls it today.
"""
from __future__ import annotations

import logging
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Skill:
    """A single skill loaded from a markdown file."""

    name: str
    path: Path
    when: str
    needs: tuple[str, ...]
    body: str

    @property
    def excerpt(self) -> str:
        """First few lines of the body, capped, for the catalog summary."""
        lines = [ln for ln in (self.body or "").splitlines() if ln.strip()]
        head = " ".join(lines[:3])
        return head[:200].strip()


_FRONTMATTER_RE = re.compile(
    r"^---\s*\n(?P<frontmatter>.*?)\n---\s*\n(?P<body>.*)\Z",
    re.DOTALL,
)


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    """Pull the YAML-shape frontmatter out of a markdown file.

    We don't import PyYAML to keep this dependency-light. The skill
    frontmatter is always shallow (name, when, needs) so a tiny
    line-based parser handles it.
    """
    match = _FRONTMATTER_RE.match(text or "")
    if not match:
        return ({}, text or "")
    raw = match.group("frontmatter")
    body = match.group("body")
    fields: dict = {}
    for line in raw.splitlines():
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip().lower()
        value = value.strip()
        if value.startswith("[") and value.endswith("]"):
            inner = value[1:-1]
            items = [s.strip().strip('"').strip("'") for s in inner.split(",") if s.strip()]
            fields[key] = tuple(items)
        else:
            fields[key] = value.strip('"').strip("'")
    return (fields, body)


def load_skills(skills_dir: Optional[Path] = None) -> tuple[Skill, ...]:
    """Load all valid skill files from skills_dir. Default: prompts/skills/."""
    if skills_dir is None:
        skills_dir = Path(__file__).resolve().parents[2] / "prompts" / "skills"
    if not skills_dir.exists() or not skills_dir.is_dir():
        return ()
    skills: list[Skill] = []
    for path in sorted(skills_dir.glob("*.md")):
        if path.name.lower() == "readme.md":
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except Exception as exc:
            logger.warning("skills loader: cannot read %s: %s", path, exc)
            continue
        fields, body = _parse_frontmatter(text)
        name = str(fields.get("name") or path.stem).strip()
        if not name:
            continue
        when = str(fields.get("when") or "").strip()
        raw_needs = fields.get("needs") or ()
        if isinstance(raw_needs, str):
            needs: tuple[str, ...] = (raw_needs,) if raw_needs else ()
        else:
            needs = tuple(str(n) for n in raw_needs if str(n).strip())
        skills.append(
            Skill(
                name=name,
                path=path,
                when=when,
                needs=needs,
                body=body.strip(),
            )
        )
    return tuple(skills)


_NEED_SPLIT = re.compile(r"\s+(?:or|OR)\s+")


def _need_satisfied(need: str) -> bool:
    """A need is satisfied when ANY of its "or"-alternatives names a binary
    on PATH ("powershell or cmd", "python OR ffmpeg fallback"); free-text
    alternatives are scanned word by word, so "ffmpeg fallback" counts when
    ffmpeg is present. Deterministic — a PATH check, not a guess."""
    for alt in _NEED_SPLIT.split(str(need or "").strip()):
        for token in alt.replace(",", " ").split():
            if shutil.which(token):
                return True
    return False


def skills_available_on_host(skills: tuple[Skill, ...]) -> tuple[Skill, ...]:
    """Only the skills this host can actually run (2026-08-19): a skill
    needs ALL its declared needs; no needs = always available. The live
    prompt had been advertising PowerShell-only skills (app_launch,
    clipboard, screen_capture, webcam…) on a headless Linux VM — 2.6k chars
    of capabilities she did not have, every turn."""
    return tuple(s for s in skills if all(_need_satisfied(n) for n in (s.needs or ())))


def _skill_lines(skill: Skill) -> list[str]:
    needs = f" (needs: {', '.join(skill.needs)})" if skill.needs else ""
    out = [f"  - {skill.name}{needs}"]
    if skill.when:
        out.append(f"    when: {skill.when}")
    if skill.excerpt:
        out.append(f"    examples in: prompts/skills/{skill.path.name}")
    return out


# Batch 7, her voice (audit 4.8): her own words, her real tools, and no
# prohibition. The block sits in the STABLE prefix, so nothing here may vary
# per turn: it is a function of the files in prompts/skills/ and of PATH only.
_CAPABILITIES_INTRO = (
    "Shell skills I have on this machine. When they ask for something one of "
    "these covers, I use local_shell, or read_file for files in my workspace, "
    "and build the command from the skill's examples. When none match, I "
    "still have a general shell and write the obvious command. These are the "
    "shell skills I have here; anything beyond them and the standard tools, "
    "I'd be making up."
)
# Fix round 1 (ruling C15): conditional — on a fresh install she has
# reached no other machine.
_ELSEWHERE_POINTER = (
    "If I reach another machine, like a Windows desktop over SSH, it may "
    "have more skills; their notes are in prompts/skills/."
)


def render_capabilities_block(skills: tuple[Skill, ...] | None = None) -> str:
    """Render the skills catalog as a compact <capabilities> block string.

    This block lands in her stable system prompt. She scans it for skills
    whose `when` matches the request and composes the matching shell command
    via local_shell (or reads a workspace file with read_file).

    The DEFAULT catalog (skills=None) lists only the skills this host can
    run (the PATH filter, :func:`skills_available_on_host`), plus ONE line
    saying another machine she reaches may have more, and where their
    notes are — whenever the filter left any card out. Audit 4.8 (Batch 7,
    approved in the 2026-09-25 fix plan) supersedes the 2026-08-19 grouping,
    which listed every card under Here / Elsewhere: the Windows cards cost
    ~1.5k chars every turn on a host where none of them runs. No OS check:
    the same rule holds on a Windows host. An explicit tuple renders flat,
    as given.
    """
    if skills is None:
        all_skills = load_skills()
        if not all_skills:
            return ""
        here = skills_available_on_host(all_skills)
        lines: list[str] = ["<capabilities>", _CAPABILITIES_INTRO]
        for skill in here:
            lines.extend(_skill_lines(skill))
        if len(here) < len(all_skills):
            lines.append(_ELSEWHERE_POINTER)
        lines.append("</capabilities>")
        return "\n".join(lines)
    if not skills:
        return ""
    lines = ["<capabilities>", _CAPABILITIES_INTRO]
    for skill in skills:
        lines.extend(_skill_lines(skill))
    lines.append("</capabilities>")
    return "\n".join(lines)


def render_skill_full(name: str) -> str:
    """Return the full body of a named skill, or empty string if not found."""
    for skill in load_skills():
        if skill.name == name:
            return skill.body
    return ""
