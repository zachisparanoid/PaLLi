"""Dashboard skin smoke tests."""
import pytest

from textual.widgets import DataTable

from palli_config.skins.dashboard import DashboardApp


@pytest.mark.asyncio
async def test_dashboard_launches_with_task_table(
    isolated_env_local, isolated_palli_yaml,
):
    app = DashboardApp(env_local=isolated_env_local, palli_yaml=isolated_palli_yaml)
    async with app.run_test() as pilot:
        await pilot.pause()
        table = app.query_one("#task-table", DataTable)
        assert table.row_count == 10  # ten lanes since R4 (2026-09-03)
        # Confirm her voice's row appears (walk DataTable rows directly —
        # str(table.render()) returns object repr in Textual 8.x, not text).
        # Plain names since R4 (2026-09-03): the lane column reads "Her voice — …".
        found = False
        for i in range(table.row_count):
            row = table.get_row_at(i)
            if any("her voice" in str(cell).lower() for cell in row):
                found = True
                break
        assert found, "task table should include her voice's lane"


@pytest.mark.asyncio
async def test_dashboard_q_exits(isolated_env_local, isolated_palli_yaml):
    app = DashboardApp(env_local=isolated_env_local, palli_yaml=isolated_palli_yaml)
    async with app.run_test() as pilot:
        await pilot.press("q")
