"""Shared widgets across all three skins."""
from __future__ import annotations

from textual.widgets import OptionList
from textual.widgets.option_list import Option

from core.llm_capabilities import LANES, Level, PROVIDERS as CAPABILITIES, check_route

_MARK = {Level.OK: "✓", Level.WARN: "⚠", Level.BLOCK: "✗"}


class TaskOptionList(OptionList):
    """A scrollable list of tasks with provider::model values shown inline."""

    def populate(self, tasks: dict) -> None:
        self.clear_options()
        for task_name, task_cfg in tasks.items():
            value = f"{task_cfg.provider} :: {task_cfg.model}" if task_cfg.provider else "(unset)"
            # 2026-09-03 (spec §3.6): the lane's plain name, and whether this route can work.
            verdict = check_route(task_cfg.provider, task_name) if task_cfg.provider else None
            mark = _MARK[verdict.level] if verdict else " "
            plain = LANES[task_name].plain_name if task_name in LANES else task_name
            label = f"{mark} {plain:34s} {value}"
            self.add_option(Option(label, id=task_name))


class ProviderOptionList(OptionList):
    """Scrollable list of providers with current state shown inline."""

    def populate(self, providers: dict, requires_key) -> None:
        self.clear_options()
        for prov_name, prov_cfg in providers.items():
            if requires_key(prov_name):
                state = "configured" if prov_cfg.api_key else "no key"
            else:
                state = "ready" if prov_cfg.enabled else "disabled"
            plain = CAPABILITIES[prov_name].plain_name if prov_name in CAPABILITIES else prov_name
            label = f"{plain:34s} {state}"
            self.add_option(Option(label, id=prov_name))
