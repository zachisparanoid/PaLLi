"""Configurable truncation helpers.

Centralizes the inline-content truncation that's scattered across the
spine, delegation, and reflect modules. Each truncation site reads its
limit from `core.config.config` so values are env-tunable.

A `limit` value of `0` means "no truncation — return the full string."
This is the sentinel for the diagnostic-mode and per-site overrides
where a user wants to see what PaLLi would process unbounded.

The diagnostic master switch (`TRUNCATION_DIAGNOSTIC_MODE`) zeros every
toggle-category limit at import time. It does NOT affect:

  - Sanity-guard truncation in reflect.py (max_items/max_chars on
    string-lists going into long-term memory). Those protect the DB
    from LLM hallucinations and aren't economy-driven.
  - Discord 2000-character protocol limits (which aren't truncation —
    they're protocol compliance).
  - Pure logging trims (60-char message previews etc.) which exist
    for human log readability, not for PaLLi behavior.

If you want to expand the diagnostic surface to also cover those, add
helpers here and route the relevant sites through them.
"""
from __future__ import annotations


def truncate(text: str | None, limit: int, *, suffix: str = "") -> str:
    """Truncate `text` to at most `limit` characters.

    A `limit` of 0 returns the full string unchanged (no truncation).
    Negative limits are treated the same as 0 — defensive against
    misconfiguration; we never want negative slicing surprises.

    When the input is shorter than `limit`, returns the input as-is.
    When longer, truncates to `limit - len(suffix)` chars and appends
    the suffix (e.g. "..." or " (truncated)") if provided.
    """
    if text is None:
        return ""
    s = str(text)
    if limit <= 0:
        return s
    if len(s) <= limit:
        return s
    if suffix and limit > len(suffix):
        return s[: limit - len(suffix)] + suffix
    return s[:limit]


def truncate_list(
    items: list,
    *,
    max_items: int,
    max_chars: int,
    coerce_str: bool = True,
) -> list[str]:
    """Cap a list to at most `max_items` entries and each entry to
    `max_chars` chars.

    Both 0 sentinels apply independently: `max_items=0` means unlimited
    item count, `max_chars=0` means unlimited per-entry length.

    By default, coerces each entry through `str()` and strips it.
    Empty/whitespace-only entries are dropped.
    """
    if not items:
        return []
    out: list[str] = []
    item_cap = len(items) if max_items <= 0 else max_items
    for entry in items[:item_cap]:
        text = str(entry).strip() if coerce_str else entry
        if not text:
            continue
        out.append(truncate(text, max_chars))
    return out
