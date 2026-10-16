"""What a tool returns when it ran and it did not work.

Tools return strings and never raise — that contract is older than this module
and it stands. The cost was that every string looked like a success: a command
that exited 3, a command that timed out, an artifact that was not there and a
path she was refused all came back as ``is_error=False`` tool results. The
error streak that wakes her on a second failure (``core/agent/runner.py``)
never counted them, and the record of her deeds filed them as things done.

So a tool that ran and failed returns ``ToolFailure(<the same text as before>)``
instead of the bare string. ``ToolRegistry.dispatch`` is the only place that
looks at the type: a ``ToolFailure`` becomes ``is_error=True``. Everything
else — every caller that stringifies a tool's return — sees exactly the text
it saw before, because ``str(ToolFailure)`` is its text.

An exception is still an exception: dispatch keeps catching those and marking
them errors. This is for the failures a tool handles itself.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ToolFailure:
    """A tool RETURNS this (it is not an exception) to say: I ran, it failed."""

    text: str

    def __str__(self) -> str:
        return self.text
