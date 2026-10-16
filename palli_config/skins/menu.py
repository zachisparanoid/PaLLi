"""Menu skin — default. Single home screen with Tasks + Providers panels."""
from __future__ import annotations

from pathlib import Path

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Footer, Header, Label, OptionList, Static

from palli_config.buffer import ConfigBuffer
from palli_config.core import load_state
from palli_config.providers import ProviderCatalog
from palli_config.skins.base import PalliConfigBase
from palli_config.skins.modals import (
    ProviderEditModal,
    QuitConfirmModal,
    TaskEditModal,
)
from palli_config.skins.widgets import ProviderOptionList, TaskOptionList


class MenuApp(PalliConfigBase):
    CSS_PATH = "../styles/menu.tcss"
    BINDINGS = [
        ("s", "save", "Save"),
        ("t", "test", "Test connections"),
        ("q", "quit", "Quit"),
    ]

    def __init__(self, *, env_local: Path, palli_yaml: Path) -> None:
        super().__init__()
        self._env_local = env_local
        self._palli_yaml = palli_yaml
        self._state = load_state(env_local=env_local, palli_yaml=palli_yaml)
        self._buffer = ConfigBuffer(self._state)
        self._catalog = ProviderCatalog.load(palli_yaml)

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        with Horizontal():
            with Vertical(id="tasks-panel"):
                yield Label("Tasks", classes="panel-title")
                yield TaskOptionList(id="task-list")
            with Vertical(id="providers-panel"):
                yield Label("Providers", classes="panel-title")
                yield ProviderOptionList(id="provider-list")
        yield Static("", id="status-line")
        yield Footer()

    def on_mount(self) -> None:
        eff = self._buffer.effective_state()
        task_list = self.query_one("#task-list", TaskOptionList)
        task_list.populate(eff.tasks)
        # Pre-highlight the first task so Enter immediately opens the modal —
        # OptionList does not auto-highlight on focus, leaving the user to
        # press Down before any interaction.
        if task_list.option_count:
            task_list.highlighted = 0
        self.query_one(
            "#provider-list", ProviderOptionList
        ).populate(eff.providers, self._catalog.requires_api_key)

        warnings: list[str] = []
        if self._state.disagreements:
            warnings.append(
                f"⚠ {len(self._state.disagreements)} env/yaml disagreement(s)"
            )
        # Codex login status check — warn if any task uses codex but CLI is not authed.
        codex_tasks = [name for name, tc in eff.tasks.items() if tc.provider == "codex"]
        if codex_tasks:
            from palli_config.providers import codex_login_status
            status = codex_login_status()
            if not status.logged_in:
                warnings.append(
                    f"⚠ codex CLI not logged in; tasks using codex will fail "
                    f"({', '.join(codex_tasks)}) — run `codex login` to fix"
                )
        if warnings:
            self.query_one("#status-line", Static).update("  ·  ".join(warnings))

    def action_save(self) -> None:
        from palli_config.core import save_all
        save_all(self._buffer, env_local=self._env_local, palli_yaml=self._palli_yaml)
        self.query_one("#status-line", Static).update("✓ saved")

    def action_quit(self) -> None:
        if self._buffer.pending_changes():
            self.push_screen(QuitConfirmModal(), self._on_quit_confirm)
        else:
            self.exit()

    def _on_quit_confirm(self, result) -> None:
        if result == "save-and-quit":
            from palli_config.core import save_all
            save_all(
                self._buffer,
                env_local=self._env_local,
                palli_yaml=self._palli_yaml,
            )
            self.exit()
        elif result == "quit-anyway":
            self.exit()

    async def action_test(self) -> None:
        from palli_config.providers import validate_provider
        eff = self._buffer.effective_state()
        # Find providers actually used by at least one task.
        used_providers = {
            tc.provider for tc in eff.tasks.values() if tc.provider
        }
        statuses: list[str] = []
        for prov in sorted(used_providers):
            self.query_one("#status-line", Static).update(
                f"testing {prov}..."
            )
            result = await validate_provider(prov)
            if result.ok:
                statuses.append(f"✓ {prov} ({result.latency_ms}ms)")
            else:
                statuses.append(f"✗ {prov}: {result.error}")
        self.query_one("#status-line", Static).update("  ·  ".join(statuses))

    def on_option_list_option_selected(
        self, event: OptionList.OptionSelected
    ) -> None:
        if event.option_list.id == "task-list":
            task_name = event.option.id
            current = self._buffer.effective_state().tasks[task_name]
            modal = TaskEditModal(
                task_name=task_name,
                current_provider=current.provider,
                current_model=current.model,
                provider_choices=list(self._catalog.provider_names()),
                models_for=lambda p: self._catalog.models_for(p),
            )
            # Option A: bind task_name via lambda closure rather than instance state.
            self.push_screen(
                modal,
                lambda result, name=task_name: self._on_task_edit_done(name, result),
            )
        elif event.option_list.id == "provider-list":
            provider_name = event.option.id
            current = self._buffer.effective_state().providers[provider_name]
            modal = ProviderEditModal(
                provider_name=provider_name,
                requires_api_key=self._catalog.requires_api_key(provider_name),
                current_api_key=current.api_key,
                current_base_url=current.base_url,
                current_default_model=current.default_model,
            )
            self.push_screen(
                modal,
                lambda result, name=provider_name: self._on_provider_edit_done(
                    name, result
                ),
            )

    def _on_task_edit_done(self, task_name: str, result) -> None:
        if result is None:
            return
        provider, model = result
        self._buffer.set_task(task_name, provider=provider, model=model)
        eff = self._buffer.effective_state()
        self.query_one("#task-list", TaskOptionList).populate(eff.tasks)
        if self._buffer.pending_changes():
            self.query_one("#status-line", Static).update("● unsaved changes")

    def _on_provider_edit_done(self, provider_name: str, result) -> None:
        if result is None:
            return
        for field, value in result.items():
            self._buffer.set_provider_field(provider_name, field, value)
        eff = self._buffer.effective_state()
        self.query_one("#provider-list", ProviderOptionList).populate(
            eff.providers, self._catalog.requires_api_key,
        )
        if self._buffer.pending_changes():
            self.query_one("#status-line", Static).update("● unsaved changes")
