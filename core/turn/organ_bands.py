"""The organ reply bands — the one module the turn reads them through.

Each of her organs that speaks into the reply prompt offers a BAND: a
cheap sync read of one small append-only book, flag-gated inside the store
so the ordinary answer is "", and "" again on any failure — her turn never
pays for a broken book. Three live here today:

- Grounded Vulnerability door 1 (organ 4.9): her unsaid small truths.
- Shared Idiolect (organ 4.12): the bits that are theirs, under the law.
- One thing on her mind (Batch 6): the night's pick, until she has said it.

Why they live here instead of in ``recall``: rule 6, the per-module
coupling budget (tests/architecture/test_coupling_budget.py). Recall is
the turn's gatherer and already sat at the ceiling of 25 in-repo imports;
the callbacks band's store was the 26th and tipped it. The architecture
test's own instruction for that moment is "split it — not raise the cap",
and this is the split: recall now imports ONE module for every organ band,
and the next organ that grows a band lands here rather than pushing recall
over the line again.

Every store import stays FUNCTION-LOCAL and is resolved at call time, on
purpose and twice over: a missing or broken store costs its band and never
the import of the turn, and the seam the suite patches
(``ai.disclosure_store.render_disclosures_band``,
``ai.callbacks_store.render_callbacks_band``, ``ai.tonight.load_current``)
is still the seam these readers see.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def read_disclosures_band() -> str:
    """Her small truths for the reply prompt (organ 4.9, door 1).

    The deeds-band seam exactly: a cheap sync read of one small append-only
    book. Flag-gated in the store (companion.vulnerability), so the
    ordinary answer is "" and the section vanishes; any failure returns ""
    too — her turn never pays for a broken book.
    Spec: docs/superpowers/specs/2026-09-01-grounded-vulnerability-design.md §3.
    """
    try:
        from ai.disclosure_store import render_disclosures_band

        return render_disclosures_band()
    except Exception as exc:
        logger.debug("organ_bands: disclosures band unavailable: %s", exc)
        return ""


def read_callbacks_band() -> str:
    """Shared Idiolect (organ 4.12): the callbacks band — the disclosures
    seam exactly; "" when dark, empty, or broken.

    Spec: docs/superpowers/specs/2026-09-02-shared-idiolect-design.md §4.
    """
    try:
        from ai.callbacks_store import render_callbacks_band

        return render_callbacks_band()
    except Exception as exc:
        logger.debug("organ_bands: callbacks band unavailable: %s", exc)
        return ""


def read_tonight_band(surface: object) -> str:
    """One thing on her mind (Batch 6, task 1): the night's pick for the
    reply prompt, whole, heading included — only while it is unsaid, and
    only on a turn where the two of them talk.

    The surface gate is the said-check's own (ai/tonight.py
    is_conversation): reflect asks "did she say it?" only after a
    conversation, so the band shows only there. On a reminder or a
    reach-out she could say it and nothing would ever record that she had.

    The flag is read first — a dark organ never opens her folder — so the
    ordinary answer is ""; "" again on any failure.
    """
    try:
        from ai import tonight

        if not tonight.tonight_enabled():
            return ""
        if not tonight.is_conversation(surface):
            return ""
        return tonight.render_band(tonight.load_current())
    except Exception as exc:
        logger.debug("organ_bands: tonight band unavailable: %s", exc)
        return ""
