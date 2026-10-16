"""Wizard skin — linear walkthrough.

Sequence:
  1. Preset selection (or skip to per-task)
  2. Per-task walkthrough (provider then model, 10 lanes)
  3. Per-provider walkthrough (api_key + base_url + default_model, 7 providers)
  4. Final confirmation with diff
"""
from __future__ import annotations

from pathlib import Path

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import Screen
from textual.widgets import Button, Footer, Header, Label, OptionList, Static
from textual.widgets.option_list import Option

from palli_config.buffer import ConfigBuffer
from palli_config.core import TASKS, load_state, save_all
from palli_config.providers import ProviderCatalog
from palli_config.skins.base import PalliConfigBase

PER_TASK_LABEL = "Per-task (no preset, walk each task)"


def preset_options(palli_yaml: Path) -> list[tuple[str, dict | None]]:
    """The YAML presets (R3, 2026-09-03 — one definition), each as
    (label, routing overrides); the last entry is the per-task walk."""
    import yaml

    from core.llm_capabilities import PROVIDERS as CAPABILITIES

    data = yaml.safe_load(palli_yaml.read_text(encoding="utf-8")) or {}
    out: list[tuple[str, dict | None]] = []
    for name, entry in (data.get("presets") or {}).items():
        overrides = (entry or {}).get("overrides") or {}
        response = overrides.get("llm.routing.response") or {}
        provider = str(response.get("provider", "") or "")
        plain = CAPABILITIES[provider].plain_name if provider in CAPABILITIES else provider
        out.append((f"{name} — her voice on {plain}", overrides))
    out.append((PER_TASK_LABEL, None))
    return out


class PresetScreen(Screen):
    BINDINGS = [("q", "app.quit", "Quit")]

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        with Vertical(id="screen-body"):
            yield Label("Wizard — Step 1 of 3", classes="step-label")
            yield Label("Pick a starting preset (or skip to per-task setup):")
            self._presets = preset_options(self.app._palli_yaml)
            yield OptionList(
                *[Option(label, id=f"preset-{i}") for i, (label, _) in enumerate(self._presets)],
                id="preset-list",
            )
        yield Footer()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        idx = int(event.option.id.removeprefix("preset-"))
        _, overrides = self._presets[idx]
        if overrides:
            for key, value in overrides.items():
                if key.startswith("llm.routing.") and isinstance(value, dict):
                    lane = key.split(".")[-1]
                    if lane in TASKS:
                        self.app._buffer.set_task(lane, provider=str(value["provider"]), model=str(value["model"]))
        self.app.push_screen(TaskWalkScreen(task_index=0))


class TaskWalkScreen(Screen):
    BINDINGS = [("q", "app.quit", "Quit"), ("escape", "back", "Back")]

    def __init__(self, *, task_index: int) -> None:
        super().__init__()
        self._task_index = task_index

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        task = TASKS[self._task_index]
        current = self.app._buffer.effective_state().tasks[task]
        with Vertical(id="screen-body"):
            yield Label(
                f"Wizard — Step 2 ({self._task_index + 1} of {len(TASKS)})",
                classes="step-label",
            )
            yield Label(f"Task: {task}")
            yield Label(f"Currently: {current.provider} :: {current.model}")
            yield Label("Choose provider:")
            yield OptionList(
                *[
                    Option(name, id=name)
                    for name in self.app._catalog.provider_names()
                ],
                id="provider-list",
            )
            yield Label("Or skip this task to keep current value:")
            yield Button("Skip", id="skip-btn")
        yield Footer()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        provider = event.option.id
        self.app.push_screen(ModelWalkScreen(
            task_index=self._task_index, provider=provider,
        ))

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "skip-btn":
            self._advance()

    def _advance(self) -> None:
        next_idx = self._task_index + 1
        if next_idx < len(TASKS):
            self.app.push_screen(TaskWalkScreen(task_index=next_idx))
        else:
            self.app.push_screen(ConfirmScreen())

    def action_back(self) -> None:
        self.app.pop_screen()


class ModelWalkScreen(Screen):
    BINDINGS = [("q", "app.quit", "Quit"), ("escape", "back", "Back")]

    def __init__(self, *, task_index: int, provider: str) -> None:
        super().__init__()
        self._task_index = task_index
        self._provider = provider

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        models = self.app._catalog.models_for(self._provider)
        with Vertical(id="screen-body"):
            yield Label(
                f"Task '{TASKS[self._task_index]}' — provider {self._provider}",
                classes="step-label",
            )
            yield Label("Choose model:")
            yield OptionList(
                *[Option(m.id, id=m.id) for m in models],
                id="model-list",
            )
        yield Footer()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        task = TASKS[self._task_index]
        self.app._buffer.set_task(task, provider=self._provider, model=event.option.id)
        next_idx = self._task_index + 1
        # Pop only the model screen so the just-completed task screen stays
        # on the stack — escape from the next task then rewinds to it,
        # matching Skip's behavior and the spec's "Esc returns to previous".
        self.app.pop_screen()
        if next_idx < len(TASKS):
            self.app.push_screen(TaskWalkScreen(task_index=next_idx))
        else:
            self.app.push_screen(ConfirmScreen())

    def action_back(self) -> None:
        self.app.pop_screen()


class ConfirmScreen(Screen):
    BINDINGS = [("q", "app.quit", "Quit"), ("t", "test_all", "Test All")]

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        with Vertical(id="screen-body"):
            yield Label("Wizard — Step 3 of 3 — Confirm", classes="step-label")
            yield Label("Pending changes:")
            changes = self.app._buffer.pending_changes()
            if not changes:
                yield Label("(no changes)")
            for c in changes:
                yield Label(f"  {c.kind} '{c.target}'.{c.field}: {c.old_value!r} → {c.new_value!r}")
            yield Static("", id="test-status")
            yield Button("Test All connections", id="test-btn")
            yield Button("Save", id="save-btn", variant="primary")
            yield Button("Discard and quit", id="quit-btn", variant="error")
        yield Footer()

    async def action_test_all(self) -> None:
        await self._run_test_all()

    async def _run_test_all(self) -> None:
        from palli_config.providers import validate_provider
        eff = self.app._buffer.effective_state()
        used = sorted({tc.provider for tc in eff.tasks.values() if tc.provider})
        statuses: list[str] = []
        for prov in used:
            self.query_one("#test-status", Static).update(f"testing {prov}...")
            result = await validate_provider(prov)
            if result.ok:
                statuses.append(f"✓ {prov} ({result.latency_ms}ms)")
            else:
                statuses.append(f"✗ {prov}")
        self.query_one("#test-status", Static).update("  ·  ".join(statuses))

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "test-btn":
            self.run_action("test_all")
        elif event.button.id == "save-btn":
            save_all(
                self.app._buffer,
                env_local=self.app._env_local,
                palli_yaml=self.app._palli_yaml,
            )
            self.app.exit()
        elif event.button.id == "quit-btn":
            self.app.exit()


class WizardApp(PalliConfigBase):
    CSS_PATH = "../styles/wizard.tcss"
    BINDINGS = [("q", "quit", "Quit")]

    def __init__(self, *, env_local: Path, palli_yaml: Path) -> None:
        super().__init__()
        self._env_local = env_local
        self._palli_yaml = palli_yaml
        self._state = load_state(env_local=env_local, palli_yaml=palli_yaml)
        self._buffer = ConfigBuffer(self._state)
        self._catalog = ProviderCatalog.load(palli_yaml)

    def on_mount(self) -> None:
        self.push_screen(PresetScreen())
