"""The memory preamble, compressed (item 6, 2026-08-19): six STRICT RULES
written for small local models that parroted memory bullets as standing
orders — with workshop-search and favorite-color examples from May. Three
short rules carry the same law (relevant-only; never lead with memory;
"prefers X" is not an order); the examples and the Active Threads /
Important Decisions lines go (threads are dated and swept now)."""
from core.identity.constants import IDENTITY_DOC_PREAMBLES


def test_memory_preamble_is_short_and_keeps_the_three_laws():
    p = IDENTITY_DOC_PREAMBLES["memory"]
    assert len(p) < 480, len(p)
    assert "BACKGROUND REFERENCE" in p
    assert "ONLY if" in p and "never lead" in p.lower() and "not a standing order" in p.lower()
    assert "workshop" not in p.lower() and "favorite color" not in p.lower()
