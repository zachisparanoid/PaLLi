"""The wizard's preset screen lists the YAML presets — not its own list."""
import pytest

from palli_config.skins.wizard import WizardApp


@pytest.mark.asyncio
async def test_wizard_presets_come_from_the_yaml(isolated_env_local, isolated_palli_yaml):
    app = WizardApp(env_local=isolated_env_local, palli_yaml=isolated_palli_yaml)
    async with app.run_test() as pilot:
        await pilot.pause()
        # app.screen, not app: Textual 8.x App.query_one searches from
        # _get_dom_base() — the DEFAULT screen at the base of the stack — so it
        # never sees a pushed screen. tests/palli_config/test_skin_wizard.py
        # queries through app.screen for the same reason.
        prompts = [str(opt.prompt) for opt in app.screen.query_one("#preset-list").options]
        assert any(p.startswith("deepseek-primary") for p in prompts)
        assert any(p.startswith("claude-oauth") for p in prompts)
        assert prompts[-1].startswith("Per-task")
        assert not any("Anthropic Sonnet 4.6 (everything)" in p for p in prompts)
