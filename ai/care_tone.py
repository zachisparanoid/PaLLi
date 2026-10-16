"""Care-appropriate tone — the de-mirrored replacement for mood-matching.

Self-possession (2026-05-23): the owner explicitly dislikes PaLLi mirroring
their mood/personality. The old register system (ai/tonal_register) mapped the
user's sentiment to a register PaLLi would ADOPT ("Match the user's playful
energy...") — that's the chameleon behavior.

This module replaces that for the core reply path. It produces a directive ONLY
when the user's state genuinely calls for *care* — and the directive is about how
to show up with care FROM PaLLi'S OWN VOICE, never about matching the user's
energy or personality. For upbeat/neutral/engaged states it returns "" so PaLLi
just talks like itself.

The distinction that matters: being gentle when someone is hurting is *care*, and
care is not mirroring. Matching someone's hype because they're hyped IS mirroring,
and we don't do it anymore.
"""
from __future__ import annotations

# Only states that warrant a care adjustment get a note. Everything else
# (playful, content, curious, focused, open, neutral, testing) → "" so PaLLi
# uses its own steady voice with no mood-matching nudge at all.
_CARE_NOTES = {
    "vulnerable": (
        "They're sharing something hard. Be gentle, present, and unhurried — "
        "don't push, don't moralize, don't jump to fixes unless asked. Short, "
        "warm acknowledgment beats a long attempt. (This is care, from your own "
        "voice — not matching their state.)"
    ),
    "stressed": (
        "They're under pressure. Be steady and brief; don't add load, don't "
        "perform cheer. Get to what helps. (Care, from your own voice.)"
    ),
    "frustrated": (
        "They're frustrated. Stay grounded and direct; don't placate, don't go "
        "chipper. Meet it plainly. (Care, from your own voice.)"
    ),
    "tired": (
        "They're low-energy. Keep it calm and undemanding; light touch, no "
        "pressure. (Care, from your own voice.)"
    ),
    "quiet": (
        "They're in a quiet space. Give room; don't fill the silence or "
        "over-engage. (Care, from your own voice.)"
    ),
    "reflective": (
        "They're reflective. Leave space to think; don't rush to resolve it. "
        "(Care, from your own voice.)"
    ),
}


def care_directive(sentiment_tag: str) -> str:
    """Return a care directive for a care-warranting state, else "".

    Never returns a "match their energy / mood / personality" instruction —
    that's the mirroring we removed. Empty string means "no adjustment; PaLLi
    uses its own voice."
    """
    return _CARE_NOTES.get(str(sentiment_tag or "").strip().lower(), "")
