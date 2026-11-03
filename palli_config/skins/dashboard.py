"""Dashboard skin — single live screen with everything visible."""
from __future__ import annotations

from pathlib import Path

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import DataTable, Footer, Header, Label, Static

from core.llm_capabilities import LANES, check_route
from palli_config.buffer import ConfigBuffer
from palli_config.core import TASKS, load_state, save_all
from palli_config.providers import ProviderCatalog
from palli_config.skins.base import PalliConfigBase
from palli_config.skins.modals import ProviderEditModal, TaskEditModal
from palli_config.skins.widgets import _MARK  # one mark table for both skins


class DashboardApp(PalliConfigBase):
    CSS_PATH = "../styles/dashboard.tcss"
    BINDINGS = [
        ("s", "save", "Save"),
        ("t", "test", "Test"),
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
            with Vertical(id="task-area"):
                yield Label("Tasks", classes="panel-title")
                yield DataTable(id="task-table", zebra_stripes=True, cursor_type="row")
            with Vertical(id="provider-area"):
                yield Label("Providers", classes="panel-title")
                yield DataTable(id="provider-table", zebra_stripes=True, cursor_type="row")
        with Vertical(id="health-area"):
            yield Label("Health", classes="panel-title")
            yield Static("(press 't' to test connections)", id="health-line")
        yield Footer()

    def on_mount(self) -> None:
        self._refresh_tables()

        eff = self._buffer.effective_state()
        codex_tasks = [name for name, tc in eff.tasks.items() if tc.provider == "codex"]
        if codex_tasks:
            from palli_config.providers import codex_login_status
            status = codex_login_status()
            if not status.logged_in:
                self.query_one("#health-line", Static).update(
                    f"⚠ codex CLI not logged in; affected tasks: {', '.join(codex_tasks)} "
                    f"— run `codex login` to fix"
                )

    def _refresh_tables(self) -> None:
        eff = self._buffer.effective_state()
        task_table = self.query_one("#task-table", DataTable)
        task_table.clear(columns=True)
        task_table.add_columns("lane", "provider", "model")
        for task in TASKS:
            tc = eff.tasks[task]
            # 2026-09-03 (spec §3.6): the same plain name and route verdict the menu shows.
            mark = _MARK[check_route(tc.provider, task).level] if tc.provider else " "
            task_table.add_row(
                f"{mark} {LANES[task].plain_name}", tc.provider, tc.model, key=task,
            )

        provider_table = self.query_one("#provider-table", DataTable)
        provider_table.clear(columns=True)
        provider_table.add_columns("provider", "key", "default model")
        for prov_name, prov_cfg in eff.providers.items():
            key_status = "set" if prov_cfg.api_key else (
                "n/a" if not self._catalog.requires_api_key(prov_name) else "missing"
            )
            provider_table.add_row(
                prov_name, key_status, prov_cfg.default_model, key=prov_name,
            )

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        # event.row_key.value is the str key passed to add_row(..., key=...).
        if event.data_table.id == "task-table":
            task = event.row_key.value
            current = self._buffer.effective_state().tasks[task]
            modal = TaskEditModal(
                task_name=task,
                current_provider=current.provider,
                current_model=current.model,
                provider_choices=list(self._catalog.provider_names()),
                models_for=lambda p: self._catalog.models_for(p),
            )
            self.push_screen(
                modal,
                lambda result, t=task: self._on_task_edit_done(t, result),
            )
        elif event.data_table.id == "provider-table":
            prov = event.row_key.value
            current = self._buffer.effective_state().providers[prov]
            modal = ProviderEditModal(
                provider_name=prov,
                requires_api_key=self._catalog.requires_api_key(prov),
                current_api_key=current.api_key,
                current_base_url=current.base_url,
                current_default_model=current.default_model,
            )
            self.push_screen(
                modal,
                lambda result, p=prov: self._on_provider_edit_done(p, result),
            )

    def _on_task_edit_done(self, task: str, result) -> None:
        if result is None:
            return
        provider, model = result
        self._buffer.set_task(task, provider=provider, model=model)
        self._refresh_tables()

    def _on_provider_edit_done(self, provider: str, result) -> None:
        if result is None:
            return
        for field, value in result.items():
            self._buffer.set_provider_field(provider, field, value)
        self._refresh_tables()

    def action_save(self) -> None:
        save_all(self._buffer, env_local=self._env_local, palli_yaml=self._palli_yaml)
        self.query_one("#health-line", Static).update("✓ saved")

    async def action_test(self) -> None:
        from palli_config.providers import validate_provider
        eff = self._buffer.effective_state()
        used = sorted({tc.provider for tc in eff.tasks.values() if tc.provider})
        statuses: list[str] = []
        for prov in used:
            self.query_one("#health-line", Static).update(f"testing {prov}...")
            result = await validate_provider(prov)
            if result.ok:
                statuses.append(f"✓ {prov} ({result.latency_ms}ms)")
            else:
                statuses.append(f"✗ {prov}")
        self.query_one("#health-line", Static).update("  ·  ".join(statuses))
