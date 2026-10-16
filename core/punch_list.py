"""The punch list — what a stranger must set before she can start (launch gap §5.1.1)."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class PunchItem:
    key: str
    why: str
    where: str


@dataclass
class PunchList:
    items: list[PunchItem] = field(default_factory=list)
    warnings: list[PunchItem] = field(default_factory=list)

    def add(self, key: str, why: str, where: str, *, hard: bool = True) -> None:
        (self.items if hard else self.warnings).append(PunchItem(key, why, where))

    def __bool__(self) -> bool:
        return bool(self.items)

    def _warnings_brain_first(self) -> list["PunchItem"]:
        """The brain line first, then the rest in the order the validators found them.

        A stranger with no brain reads the top line and nothing else. Validator
        order put the Discord door above it, so the first sentence of the first
        hour was about a chat app they may not even use, while the one line that
        tells them where to go was third. Nothing else is reordered.
        """
        brain = [p for p in self.warnings if p.key == "LLM"]
        return brain + [p for p in self.warnings if p.key != "LLM"]

    def render(self) -> str:
        lines: list[str] = []
        if self.items:
            lines.append("PaLLi cannot start yet. Fix these, in order:")
            lines += [f"{i}. {p.key} — {p.why}; {p.where}" for i, p in enumerate(self.items, 1)]
        if self.warnings:
            lines.append("Warnings for this boot (she runs; each names its setting):")
            lines += [f"- {p.key} — {p.why}; {p.where}" for p in self._warnings_brain_first()]
        return "\n".join(lines)
