"""Which files she can READ, and which she can only hold (telegram door,
2026-09-14, task 2).

The two lists and the question they answer were written inside
bots/discord/intake.py because Discord was the only door. Neither is about
Discord: a ``.py`` file is text at every door, and an ``.xlsx`` is not.
Re-implemented per door they would drift within a week, and the drift would
be invisible — a file quietly landing in the wrong bucket reads as "she
ignored it".

The buckets themselves are DEFECT_RECORD §19's: text goes into the prompt,
everything else into the inbox with a line naming it, and nothing at all
falls through.
"""
from __future__ import annotations

# Text/code MIME types and filename suffixes we treat as readable
# attachments. The legacy bot.py read these into the prompt so PaLLi
# could see code/log/config files dropped into chat.
TEXT_MIME_PREFIXES = ("text/", "application/json", "application/xml")
TEXT_FILENAME_SUFFIXES = (
    ".txt", ".md", ".py", ".json", ".yaml", ".yml", ".csv", ".tsv",
    ".log", ".conf", ".ini", ".toml", ".xml", ".html", ".css", ".js",
    ".ts", ".tsx", ".jsx", ".sh", ".bash", ".zsh", ".ps1", ".bat",
    ".sql", ".rs", ".go", ".java", ".c", ".cpp", ".h", ".hpp", ".rb",
    ".php", ".pl", ".lua", ".swift", ".kt", ".dart", ".gradle", ".env",
)


def is_text_like(*, mime: str | None, filename: str | None) -> bool:
    """True when this attachment is something she can read as text.

    Either half is enough: a server that mislabels a ``.py`` as
    application/octet-stream is common, and so is a text/plain file with no
    extension at all.
    """
    content_type = (str(mime or "")).lower()
    if any(content_type.startswith(prefix) for prefix in TEXT_MIME_PREFIXES):
        return True
    name = (str(filename or "")).lower()
    return any(name.endswith(suffix) for suffix in TEXT_FILENAME_SUFFIXES)
