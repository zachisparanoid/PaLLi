"""A spoken reply and the short text line beside it (DEFECT §56, 2026-10-02).

While voice replies are on, she writes her reply as usual and the door speaks
it. Anything she'd need them to READ — a link, a code block, an inline code
span (numbers she wants seen go in backticks) — is said as "in the text" and
also goes out as one short text line, so she never needs a tool to add it
(the 10-01 20:03 loop: 51 voice messages from one reply). Stand-in text only.
"""
from __future__ import annotations

from core.voice_replies import split_for_voice


def test_a_plain_reply_is_all_spoken_and_has_no_text_line():
    spoken, text = split_for_voice("The deploy finished. Nothing else to report.")
    assert spoken == "The deploy finished. Nothing else to report."
    assert text == ""


def test_a_link_is_said_as_in_the_text_and_goes_as_the_text_line():
    spoken, text = split_for_voice("Here's the page: https://example.com/jobs/123. Have a look.")
    assert "https" not in spoken and "example.com" not in spoken
    assert "(link in the text)" in spoken
    assert spoken.endswith("Have a look.")
    assert text == "https://example.com/jobs/123"


def test_a_markdown_link_keeps_its_label_aloud_and_its_address_in_the_text():
    spoken, text = split_for_voice("Read [the posting](https://example.com/p) tonight.")
    assert spoken == "Read the posting (link in the text) tonight."
    assert text == "https://example.com/p"


def test_inline_code_is_spoken_and_also_goes_in_the_text():
    spoken, text = split_for_voice("Tomorrow's picks: `38 40 42 56 63` and `1 2 3 4 5`.")
    assert spoken == "Tomorrow's picks: 38 40 42 56 63 and 1 2 3 4 5."
    assert text == "38 40 42 56 63\n1 2 3 4 5"


def test_a_code_block_is_never_read_aloud():
    reply = "Run this:\n```bash\nls -la /tmp\n```\nThen tell me."
    spoken, text = split_for_voice(reply)
    assert "ls -la" not in spoken
    assert "(it's in the text)" in spoken
    assert text == "```bash\nls -la /tmp\n```"


def test_the_same_link_twice_is_in_the_text_once():
    spoken, text = split_for_voice("See https://example.com/a and again https://example.com/a")
    assert text == "https://example.com/a"
    assert spoken.count("(link in the text)") == 2


def test_a_link_inside_backticks_is_in_the_text_once_and_never_read_aloud():
    spoken, text = split_for_voice("The address is `https://example.com/x`.")
    assert "example.com" not in spoken
    assert text == "https://example.com/x"


def test_a_reply_that_is_only_a_link_has_nothing_to_say():
    spoken, text = split_for_voice("https://example.com/only")
    assert spoken in ("", "(link in the text)")
    assert text == "https://example.com/only"
