"""The owner, 2026-10-12: PaLLi acknowledges and talks while it works, in
whatever words it sees fit. Its working rules say HOW it speaks mid-task
(chime_in) and leave whether, when and how to it: no reasons listed, no
phrases banned, no lines to copy."""
from unittest.mock import patch

from scripts.prompt_size_report import count_prohibitions, find_scripted_quotes

OLD_BLOCK_CHARS = 860      # the block (tools on) before 10-12; her stable prompt may only shrink


def _block(tools=True):
    from core.agent import duplex as dx
    with patch.object(dx, "duplex_enabled", lambda: True):
        return dx.render_duplex_block(tools=tools)


def test_the_working_rules_leave_speaking_to_her():
    block = _block()
    assert "whenever and however I choose" in block
    for gone in ("I work quietly", "a find, a fork", "busy-ack", "progress report"):
        assert gone not in block, gone


def test_the_working_rules_hand_her_no_lines_and_no_bans():
    block = _block()
    assert find_scripted_quotes(block) == []
    assert count_prohibitions(block) == 0


def test_the_rewrite_does_not_grow_her_stable_prompt():
    assert len(_block()) <= OLD_BLOCK_CHARS
    assert len(_block(tools=False)) < len(_block())


def test_chime_in_describes_the_act_without_rules_on_when():
    from core.agent.builtin_tools.duplex_hands import ChimeInTool
    text = ChimeInTool.description
    for gone in ("Never", "busy-ack", "progress report", "silence is the honest answer"):
        assert gone not in text, gone
    assert find_scripted_quotes(text) == []
    assert "300" in text
