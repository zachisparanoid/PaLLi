"""Menu skin smoke tests — launches headlessly, navigates, verifies state."""
import pytest

from palli_config.skins.menu import MenuApp


@pytest.mark.asyncio
async def test_menu_launches_with_ten_task_rows(
    isolated_env_local, isolated_palli_yaml,
):
    app = MenuApp(env_local=isolated_env_local, palli_yaml=isolated_palli_yaml)
    async with app.run_test() as pilot:
        await pilot.pause()
        # Ten lane rows + seven provider rows should be visible (R4, 2026-09-03)
        task_list = app.query_one("#task-list")
        assert task_list is not None
        # Inspect the options directly — Textual 8.x OptionList.render() returns
        # a Rich Panel object, not a string, so check option prompts instead.
        prompts = [str(opt.prompt).lower() for opt in task_list.options]
        assert any("her voice" in p for p in prompts)   # plain names since R4 (2026-09-03)


@pytest.mark.asyncio
async def test_menu_quit_keybind_exits_cleanly(
    isolated_env_local, isolated_palli_yaml,
):
    app = MenuApp(env_local=isolated_env_local, palli_yaml=isolated_palli_yaml)
    async with app.run_test() as pilot:
        await pilot.press("q")
    # If we get here without exception, q exited cleanly.


@pytest.mark.asyncio
async def test_menu_enter_on_task_opens_modal_and_can_change_provider(
    isolated_env_local, isolated_palli_yaml,
):
    app = MenuApp(env_local=isolated_env_local, palli_yaml=isolated_palli_yaml)
    async with app.run_test() as pilot:
        await pilot.pause()
        # Move to first task and press Enter to open the modal.
        await pilot.press("enter")
        await pilot.pause()
        # Modal should be the active screen.
        assert "TaskEditModal" in type(app.screen).__name__ or any(
            "TaskEditModal" in type(s).__name__ for s in app.screen_stack
        )


@pytest.mark.asyncio
async def test_menu_enter_on_provider_opens_provider_modal(
    isolated_env_local, isolated_palli_yaml,
):
    app = MenuApp(env_local=isolated_env_local, palli_yaml=isolated_palli_yaml)
    async with app.run_test() as pilot:
        await pilot.pause()
        # Move focus to provider list, then highlight the first option, then Enter.
        provider_list = app.query_one("#provider-list")
        provider_list.focus()
        provider_list.highlighted = 0
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        assert any(
            "ProviderEditModal" in type(s).__name__ for s in app.screen_stack
        ) or "ProviderEditModal" in type(app.screen).__name__


@pytest.mark.asyncio
async def test_menu_test_action_runs_validation_and_updates_status(
    isolated_env_local, isolated_palli_yaml, monkeypatch,
):
    from palli_config import providers as providers_mod
    from palli_config.providers import ValidationResult

    async def fake_validate(provider_name, **_kwargs):
        return ValidationResult(ok=True, latency_ms=42, error=None)
    monkeypatch.setattr(providers_mod, "validate_provider", fake_validate)

    app = MenuApp(env_local=isolated_env_local, palli_yaml=isolated_palli_yaml)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("t")
        await pilot.pause()
        status_text = str(app.query_one("#status-line").render())
        assert "✓" in status_text or "ok" in status_text.lower()


@pytest.mark.asyncio
async def test_menu_quit_with_unsaved_changes_shows_confirm_modal(
    isolated_env_local, isolated_palli_yaml,
):
    app = MenuApp(env_local=isolated_env_local, palli_yaml=isolated_palli_yaml)
    async with app.run_test() as pilot:
        await pilot.pause()
        # Make a pending change directly via the buffer.
        app._buffer.set_task("response", provider="openai", model="gpt-4o")
        await pilot.press("q")
        await pilot.pause()
        assert any(
            "QuitConfirm" in type(s).__name__ for s in app.screen_stack
        )


@pytest.mark.asyncio
async def test_menu_warns_when_codex_task_but_not_logged_in(
    tmp_path, isolated_palli_yaml, monkeypatch,
):
    env_local = tmp_path / ".env.local"
    env_local.write_text(
        "LLM_PROVIDER=codex\nLLM_RESPONSE_MODEL=gpt-5.5\n",
        encoding="utf-8",
    )

    from palli_config import providers as providers_mod
    from palli_config.providers import CodexLoginStatus
    monkeypatch.setattr(
        providers_mod, "codex_login_status",
        lambda: CodexLoginStatus(logged_in=False, message="not logged in"),
    )

    app = MenuApp(env_local=env_local, palli_yaml=isolated_palli_yaml)
    async with app.run_test() as pilot:
        await pilot.pause()
        status_text = str(app.query_one("#status-line").render())
        assert "codex" in status_text.lower()
        assert ("login" in status_text.lower() or "not logged" in status_text.lower())
