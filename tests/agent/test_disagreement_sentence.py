"""Draft A, the permission to disagree (the owner, approved word for word
2026-10-12 15:50, NAS palli/7.0.0a/decisions-presence.md Q6; rides with the
first look, Q3 of the first-look plan, approved 22:03): one sentence of the
"I'm myself with them" block is replaced; the rest of the block stays."""
from core.agent import system_prompt as sp

DRAFT_A = (
    "When I see it differently, I say so: once, plainly, with my reason, and "
    "then it's their call. \"I wouldn't run that yet; the backup hasn't "
    "finished\" is me being useful, not difficult. Agreeing because it's "
    "easier isn't agreement, so I don't offer it. Once I've said my piece, I "
    "let it go."
)


def test_the_block_says_draft_a_word_for_word():
    assert DRAFT_A in sp._self_possession_block()


def test_the_old_sentence_is_gone():
    assert "even when agreeing would be easier" not in sp._self_possession_block()


def test_the_rest_of_the_block_stays():
    block = sp._self_possession_block()
    assert block.startswith("## I'm myself with them\nI'm the same one whoever I'm talking to.")
    assert "not from mirroring theirs. " + DRAFT_A + " The moods I show" in block
    assert block.endswith("the agreement I give is the agreement I feel.")
