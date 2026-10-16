"""
Voice text cleaner - removes markdown and emoji for TTS
"""

import re
import logging

logger = logging.getLogger(__name__)


def clean_text_for_voice(text: str) -> str:
    """
    Clean text for voice synthesis by removing markdown and emoji.

    Args:
        text: Original text with markdown/emoji

    Returns:
        Cleaned text suitable for TTS
    """
    if not text:
        return text

    cleaned = text

    # Remove markdown bold/italic formatting
    # **bold** or __bold__ -> bold
    cleaned = re.sub(r'\*\*(.+?)\*\*', r'\1', cleaned)
    cleaned = re.sub(r'__(.+?)__', r'\1', cleaned)

    # *italic* or _italic_ -> italic
    cleaned = re.sub(r'\*(.+?)\*', r'\1', cleaned)
    cleaned = re.sub(r'_(.+?)_', r'\1', cleaned)

    # Remove strikethrough ~~text~~
    cleaned = re.sub(r'~~(.+?)~~', r'\1', cleaned)

    # Remove inline code `code`
    cleaned = re.sub(r'`(.+?)`', r'\1', cleaned)

    # Remove code blocks ```code```
    cleaned = re.sub(r'```[\s\S]*?```', '', cleaned)

    # Remove markdown links [text](url) -> text
    cleaned = re.sub(r'\[(.+?)\]\(.+?\)', r'\1', cleaned)

    # Remove markdown headers ### Header -> Header
    cleaned = re.sub(r'^#{1,6}\s+', '', cleaned, flags=re.MULTILINE)

    # Remove markdown list markers (-, *, +)
    cleaned = re.sub(r'^\s*[-*+]\s+', '', cleaned, flags=re.MULTILINE)

    # Remove markdown numbered list markers (1., 2., etc)
    cleaned = re.sub(r'^\s*\d+\.\s+', '', cleaned, flags=re.MULTILINE)

    # Remove blockquotes (> text)
    cleaned = re.sub(r'^\s*>\s+', '', cleaned, flags=re.MULTILINE)

    # Remove emoji (Unicode emoji characters)
    # This removes most emoji but keeps regular text
    emoji_pattern = re.compile(
        "["
        "\U0001F600-\U0001F64F"  # emoticons
        "\U0001F300-\U0001F5FF"  # symbols & pictographs
        "\U0001F680-\U0001F6FF"  # transport & map symbols
        "\U0001F700-\U0001F77F"  # alchemical symbols
        "\U0001F780-\U0001F7FF"  # Geometric Shapes Extended
        "\U0001F800-\U0001F8FF"  # Supplemental Arrows-C
        "\U0001F900-\U0001F9FF"  # Supplemental Symbols and Pictographs
        "\U0001FA00-\U0001FA6F"  # Chess Symbols
        "\U0001FA70-\U0001FAFF"  # Symbols and Pictographs Extended-A
        "\U00002702-\U000027B0"  # Dingbats
        "\U000024C2-\U0001F251"  # Enclosed characters
        "]+",
        flags=re.UNICODE
    )
    cleaned = emoji_pattern.sub('', cleaned)

    # Remove Discord custom emoji format <:name:id> or <a:name:id>
    cleaned = re.sub(r'<a?:\w+:\d+>', '', cleaned)

    # Remove emoji shortcodes like :smile: :heart:
    # A shortcode name always starts with a letter or underscore, never a
    # digit — \w alone also matched digit runs, which ate the colons out of
    # times ("10:30:45" -> "1045") and ratios ("3:2:1" -> "31"). Verbalising
    # times/ratios into words is out of scope here; this only keeps their
    # digits and colons intact for the TTS to read as-is.
    cleaned = re.sub(r':[A-Za-z_][\w+-]*:', '', cleaned)
    # Discord's digit-only shortcodes (":100:") go too, but only when no
    # digit or colon touches them on either side — "10:30:45", "3:2:1",
    # "12:00:00" and "9:05" keep every digit and colon.
    cleaned = re.sub(r'(?<![\d:]):\d+:(?![\d:])', '', cleaned)

    # Clean up multiple spaces
    cleaned = re.sub(r'\s+', ' ', cleaned)

    # Clean up multiple newlines
    cleaned = re.sub(r'\n\s*\n\s*\n+', '\n\n', cleaned)

    # Trim whitespace
    cleaned = cleaned.strip()

    return cleaned


def should_clean_for_voice(metadata: dict) -> bool:
    """
    Determine if text should be cleaned for voice based on metadata.

    Args:
        metadata: Message metadata

    Returns:
        True if text should be cleaned for voice
    """
    if not metadata:
        return False

    # Check if this is a voice session
    if metadata.get("voice_session", False):
        return True

    # Check if this is always listening
    if metadata.get("always_listening", False):
        return True

    return False
