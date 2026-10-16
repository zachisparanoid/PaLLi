"""Base Textual App used by all three skins."""
from __future__ import annotations

from textual.app import App


class PalliConfigBase(App):
    """Base class with shared chrome (header, footer) and key bindings."""

    BINDINGS = [
        ("q", "quit", "Quit"),
    ]
