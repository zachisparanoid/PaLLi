"""Modal screens for editing tasks and providers."""
from __future__ import annotations

from typing import Callable

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Footer, Input, Label, OptionList
from textual.widgets.option_list import Option


class TaskEditModal(ModalScreen):
    """Edit a single task: pick provider, then pick model.

    Dismisses with `(provider, model)` on Save, or `None` on Cancel/Escape.
    """

    BINDINGS = [("escape", "dismiss", "Cancel")]

    def __init__(
        self,
        *,
        task_name: str,
        current_provider: str,
        current_model: str,
        provider_choices: list[str],
        models_for: Callable,  # callable(provider_name) -> list[ModelInfo]
    ) -> None:
        super().__init__()
        self._task_name = task_name
        self._current_provider = current_provider
        self._current_model = current_model
        self._provider_choices = provider_choices
        self._models_for = models_for
        self._selected_provider: str | None = None
        self._selected_model: str | None = None
        # Cache the currently displayed model list so we can resolve by index
        # in OptionSelected — model ids are NOT guaranteed unique within a
        # provider (data/palli.yaml has duplicates), so we can't use Option
        # id= for the model picker.
        self._displayed_models: list = []

    def compose(self) -> ComposeResult:
        with Vertical(id="modal-body"):
            yield Label(f"Edit task: {self._task_name}", classes="modal-title")
            yield Label("Provider:")
            opts = [Option(name, id=name) for name in self._provider_choices]
            yield OptionList(*opts, id="provider-picker")
            yield Label("", id="model-label")
            yield OptionList(id="model-picker")
            yield Button("Save", id="save-btn", variant="primary")
            yield Button("Cancel", id="cancel-btn")
        yield Footer()

    def on_mount(self) -> None:
        provider_picker = self.query_one("#provider-picker", OptionList)
        try:
            provider_picker.highlighted = self._provider_choices.index(
                self._current_provider
            )
        except ValueError:
            pass
        self._refresh_models(self._current_provider)

    def _refresh_models(self, provider: str) -> None:
        models = self._models_for(provider)
        self._displayed_models = list(models)
        picker = self.query_one("#model-picker", OptionList)
        picker.clear_options()
        # Don't pass id= — model ids can collide within a single provider
        # (e.g. deepseek-v4-flash appears twice in data/palli.yaml), and
        # duplicate Option ids raise DuplicateID. We resolve selection via
        # option_index against self._displayed_models instead.
        for m in models:
            picker.add_option(Option(m.id))
        try:
            picker.highlighted = [m.id for m in models].index(self._current_model)
        except ValueError:
            picker.highlighted = 0 if models else None
        self.query_one("#model-label", Label).update(
            f"Model ({len(models)} available):"
        )

    def on_option_list_option_selected(
        self, event: OptionList.OptionSelected
    ) -> None:
        if event.option_list.id == "provider-picker":
            self._selected_provider = event.option.id
            self._refresh_models(self._selected_provider)
        elif event.option_list.id == "model-picker":
            idx = event.option_index
            if 0 <= idx < len(self._displayed_models):
                self._selected_model = self._displayed_models[idx].id

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "save-btn":
            provider = self._selected_provider or self._current_provider
            model = self._selected_model or self._current_model
            self.dismiss((provider, model))
        elif event.button.id == "cancel-btn":
            self.dismiss(None)


class ProviderEditModal(ModalScreen):
    """Edit a provider: api_key (masked), base_url, default_model.

    Dismisses with a dict of `{api_key, base_url, default_model}` on Save,
    or `None` on Cancel/Escape. Providers without an HTTP API key (codex,
    ollama) skip the api_key Input and pass through the current value.
    """

    BINDINGS = [("escape", "dismiss", "Cancel")]

    def __init__(
        self,
        *,
        provider_name: str,
        requires_api_key: bool,
        current_api_key: str,
        current_base_url: str,
        current_default_model: str,
    ) -> None:
        super().__init__()
        self._provider_name = provider_name
        self._requires_api_key = requires_api_key
        self._current_api_key = current_api_key
        self._current_base_url = current_base_url
        self._current_default_model = current_default_model

    def compose(self) -> ComposeResult:
        with Vertical(id="modal-body"):
            yield Label(
                f"Edit provider: {self._provider_name}", classes="modal-title"
            )
            if self._requires_api_key:
                yield Label("API key:")
                yield Input(
                    value=self._current_api_key,
                    password=True,
                    id="api-key-input",
                )
            else:
                yield Label("(this provider does not use an HTTP API key)")
            yield Label("Base URL (optional):")
            yield Input(value=self._current_base_url, id="base-url-input")
            yield Label("Default model:")
            yield Input(
                value=self._current_default_model, id="default-model-input"
            )
            yield Button("Save", id="save-btn", variant="primary")
            yield Button("Cancel", id="cancel-btn")
        yield Footer()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "save-btn":
            api_key = (
                self.query_one("#api-key-input", Input).value
                if self._requires_api_key
                else self._current_api_key
            )
            base_url = self.query_one("#base-url-input", Input).value
            default_model = self.query_one("#default-model-input", Input).value
            self.dismiss({
                "api_key": api_key,
                "base_url": base_url,
                "default_model": default_model,
            })
        elif event.button.id == "cancel-btn":
            self.dismiss(None)


class QuitConfirmModal(ModalScreen):
    """Confirm before discarding unsaved changes."""

    BINDINGS = [("escape", "dismiss", "Cancel")]

    def compose(self) -> ComposeResult:
        with Vertical(id="modal-body"):
            yield Label("You have unsaved changes.", classes="modal-title")
            yield Label("What would you like to do?")
            yield Button("Save and quit", id="save-quit-btn", variant="primary")
            yield Button("Quit without saving", id="quit-anyway-btn", variant="error")
            yield Button("Cancel", id="cancel-btn")
        yield Footer()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "save-quit-btn":
            self.dismiss("save-and-quit")
        elif event.button.id == "quit-anyway-btn":
            self.dismiss("quit-anyway")
        elif event.button.id == "cancel-btn":
            self.dismiss(None)
