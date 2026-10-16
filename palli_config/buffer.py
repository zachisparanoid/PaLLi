"""Staged-changes buffer in front of ConfigState.

The buffer holds pending edits in memory. Skins set values via the buffer;
nothing persists until save_all() (in core.py) flushes the buffer.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Literal

from palli_config.core import ConfigState

ChangeKind = Literal["task", "provider"]


@dataclass(frozen=True)
class Change:
    kind: ChangeKind
    target: str  # task name or provider name
    field: str
    old_value: str
    new_value: str


_PROVIDER_FIELDS = ("api_key", "base_url", "default_model")


class ConfigBuffer:
    """Holds pending edits in front of a base ConfigState."""

    def __init__(self, base_state: ConfigState) -> None:
        self._base = base_state
        self._working = copy.deepcopy(base_state)

    def set_task(self, task: str, *, provider: str, model: str) -> None:
        if task not in self._working.tasks:
            raise ValueError(f"unknown task: {task!r}")
        tc = self._working.tasks[task]
        tc.provider = provider
        tc.model = model

    def set_provider_field(self, provider: str, field: str, value: str) -> None:
        if provider not in self._working.providers:
            raise ValueError(f"unknown provider: {provider!r}")
        if field not in _PROVIDER_FIELDS:
            raise ValueError(f"unknown provider field: {field!r}")
        pc = self._working.providers[provider]
        setattr(pc, field, value)

    def revert(self) -> None:
        self._working = copy.deepcopy(self._base)

    def effective_state(self) -> ConfigState:
        return copy.deepcopy(self._working)

    def pending_changes(self) -> list[Change]:
        out: list[Change] = []
        for task_name, base_task in self._base.tasks.items():
            work_task = self._working.tasks[task_name]
            if work_task.provider != base_task.provider:
                out.append(Change(
                    kind="task", target=task_name, field="provider",
                    old_value=base_task.provider, new_value=work_task.provider,
                ))
            if work_task.model != base_task.model:
                out.append(Change(
                    kind="task", target=task_name, field="model",
                    old_value=base_task.model, new_value=work_task.model,
                ))
        for prov_name, base_prov in self._base.providers.items():
            work_prov = self._working.providers[prov_name]
            for fld in _PROVIDER_FIELDS:
                if getattr(work_prov, fld) != getattr(base_prov, fld):
                    out.append(Change(
                        kind="provider", target=prov_name, field=fld,
                        old_value=getattr(base_prov, fld),
                        new_value=getattr(work_prov, fld),
                    ))
        return out
