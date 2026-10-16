from __future__ import annotations

from core.voice_text_cleaner import clean_text_for_voice, should_clean_for_voice


def test_clean_text_for_voice_removes_markdown_and_emoji():
    raw = (
        "## Header\n"
        "**bold** _italic_ ~~gone~~ `inline`\n"
        "Link: [docs](https://example.com)\n"
        "- bullet\n"
        "Emoji 😀 <:wave:123> :smile:"
    )
    cleaned = clean_text_for_voice(raw)
    assert "Header" in cleaned
    assert "bold" in cleaned
    assert "italic" in cleaned
    assert "Link: docs" in cleaned
    assert "bullet" in cleaned
    assert "😀" not in cleaned
    assert "<:wave:123>" not in cleaned
    assert ":smile:" not in cleaned


def test_should_clean_for_voice():
    assert should_clean_for_voice(None) is False
    assert should_clean_for_voice({}) is False
    assert should_clean_for_voice({"voice_session": True}) is True
    assert should_clean_for_voice({"always_listening": True}) is True


def test_times_and_ratios_keep_their_colons():
    # The emoji-shortcode strip (":\w+:") used to eat digit-only colon runs
    # too, since \w matches digits. A shortcode name always starts with a
    # letter or underscore, so times and ratios should survive untouched.
    assert "10:30:45" in clean_text_for_voice("It's 10:30:45 now.")
    assert "3:2:1" in clean_text_for_voice("mix it 3:2:1")
    assert clean_text_for_voice("**bold** at 9:05") == "bold at 9:05"
    assert "12:00:00" in clean_text_for_voice("at 12:00:00")
    assert clean_text_for_voice("smile :smile: ok") == "smile ok"


def test_a_digit_only_discord_shortcode_is_stripped_but_times_are_not():
    """Final review deferred minor (T8/9): the narrowed shortcode strip no
    longer took Discord's digit-only ":100:". A second, narrow pass takes a
    digit-only shortcode only when no digit or colon touches it on either
    side, so a time or a ratio keeps every digit and colon."""
    assert clean_text_for_voice("that's :100: right") == "that's right"
    assert clean_text_for_voice(":100:") == ""
    assert clean_text_for_voice("nailed it :100::fire:") == "nailed it"
    assert "10:30:45" in clean_text_for_voice("It's 10:30:45 now.")
    assert "3:2:1" in clean_text_for_voice("mix it 3:2:1")
    assert "12:00:00" in clean_text_for_voice("at 12:00:00")
    assert clean_text_for_voice("at 9:05") == "at 9:05"
    assert clean_text_for_voice("at 10:30: sharp") == "at 10:30: sharp"
