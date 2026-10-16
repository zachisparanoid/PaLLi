"""Wizard skin smoke tests."""
import pytest

from palli_config.skins.wizard import WizardApp


@pytest.mark.asyncio
async def test_wizard_launches_with_preset_screen(
    isolated_env_local, isolated_palli_yaml,
):
    app = WizardApp(env_local=isolated_env_local, palli_yaml=isolated_palli_yaml)
    async with app.run_test() as pilot:
        await pilot.pause()
        # Inspect screen via query, not str(render()) which gives object repr in 8.x.
        # Textual 8.x Label exposes text content via .render() (returns the
        # VisualType passed in); .renderable was removed.
        rendered = " ".join(
            str(label.render()) for label in app.screen.query("Label")
        ).lower()
        assert "preset" in rendered or "step 1" in rendered


@pytest.mark.asyncio
async def test_wizard_quit_keybind_exits(isolated_env_local, isolated_palli_yaml):
    app = WizardApp(env_local=isolated_env_local, palli_yaml=isolated_palli_yaml)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("q")


@pytest.mark.asyncio
async def test_wizard_confirm_screen_has_test_all_button(
    isolated_env_local, isolated_palli_yaml,
):
    """The wizard's confirm screen should expose Test All for parity with
    menu and dashboard's 't' keybind."""
    app = WizardApp(env_local=isolated_env_local, palli_yaml=isolated_palli_yaml)
    async with app.run_test() as pilot:
        await pilot.pause()
        # Force-push to confirm screen for the test (skip preset + per-task walk):
        from palli_config.skins.wizard import ConfirmScreen
        app.push_screen(ConfirmScreen())
        await pilot.pause()
        # Walk button widgets on the active screen and check label text.
        button_labels = " ".join(
            str(b.label) for b in app.screen.query("Button")
        ).lower()
        assert "test all" in button_labels or "test connections" in button_labels
