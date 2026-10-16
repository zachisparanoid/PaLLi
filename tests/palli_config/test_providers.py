"""Provider catalog: read curated providers + models from palli.yaml."""
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from palli_config.providers import ProviderCatalog, refresh_models_from_api


def test_catalog_lists_every_runtime_provider(isolated_palli_yaml: Path):
    catalog = ProviderCatalog.load(isolated_palli_yaml)
    names = catalog.provider_names()
    # R4 (2026-09-03): all seven, from core.llm_capabilities — not the five of Stage 1
    assert set(names) == {"openai", "anthropic", "deepseek", "codex", "ollama", "local_openai", "claude"}


def test_catalog_returns_models_for_provider(isolated_palli_yaml: Path):
    catalog = ProviderCatalog.load(isolated_palli_yaml)
    deepseek_models = catalog.models_for("deepseek")
    assert any(m.id == "deepseek-v4-pro" for m in deepseek_models)


def test_catalog_returns_empty_for_unknown_provider(isolated_palli_yaml: Path):
    catalog = ProviderCatalog.load(isolated_palli_yaml)
    assert catalog.models_for("nonexistent") == []


def test_provider_requires_api_key(isolated_palli_yaml: Path):
    catalog = ProviderCatalog.load(isolated_palli_yaml)
    assert catalog.requires_api_key("openai") is True
    assert catalog.requires_api_key("anthropic") is True
    assert catalog.requires_api_key("deepseek") is True
    assert catalog.requires_api_key("codex") is False  # CLI auth
    assert catalog.requires_api_key("ollama") is False  # local keyless


from palli_config.providers import codex_login_status, CodexLoginStatus


def test_codex_login_status_invokes_correct_command():
    """Stronger contract: verify the subprocess argv we ship is the one the real CLI accepts.

    Implementation may resolve "codex" via shutil.which() to handle the Windows
    .cmd npm shim (subprocess can't find shims from a bare "codex" argv on
    Windows without shell=True). Assert the subcommand precisely; assert the
    executable resolves to *some* codex binary.
    """
    import os
    with patch("subprocess.run") as mock_run:
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = "Logged in using ChatGPT"
        codex_login_status()
    args, _kwargs = mock_run.call_args
    argv = args[0]
    # subcommand must be exactly "login status"
    assert argv[1:] == ["login", "status"]
    # executable must be "codex" or a path ending in codex (with optional .cmd/.exe)
    exe_basename = os.path.basename(argv[0]).lower()
    assert exe_basename in ("codex", "codex.cmd", "codex.exe", "codex.bat")


def test_codex_login_status_returns_logged_in_on_zero_exit():
    with patch("subprocess.run") as mock_run:
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = "Logged in using ChatGPT"
        status = codex_login_status()
    assert isinstance(status, CodexLoginStatus)
    assert status.logged_in is True


def test_codex_login_status_returns_not_logged_in_on_nonzero_exit():
    with patch("subprocess.run") as mock_run:
        mock_run.return_value.returncode = 1
        mock_run.return_value.stdout = ""
        mock_run.return_value.stderr = "Not logged in"
        status = codex_login_status()
    assert status.logged_in is False


def test_codex_login_status_returns_unknown_when_cli_missing():
    with patch("subprocess.run", side_effect=FileNotFoundError("codex")):
        status = codex_login_status()
    assert status.logged_in is False
    assert "not found" in (status.message or "").lower()


@pytest.mark.asyncio
async def test_refresh_returns_curated_when_api_succeeds_with_new_ids(
    isolated_palli_yaml,
):
    catalog = ProviderCatalog.load(isolated_palli_yaml)
    fake_api_response = {
        "data": [
            {"id": "deepseek-v4-pro"},  # already curated
            {"id": "deepseek-future-model"},  # new
        ]
    }
    with patch(
        "palli_config.providers._fetch_models_via_http",
        AsyncMock(return_value=fake_api_response["data"]),
    ):
        merged = await refresh_models_from_api(catalog, "deepseek")
    ids = [m.id for m in merged]
    assert "deepseek-v4-pro" in ids
    assert "deepseek-future-model" in ids


@pytest.mark.asyncio
async def test_refresh_falls_back_to_curated_on_api_error(isolated_palli_yaml):
    catalog = ProviderCatalog.load(isolated_palli_yaml)
    with patch(
        "palli_config.providers._fetch_models_via_http",
        AsyncMock(side_effect=RuntimeError("network error")),
    ):
        merged = await refresh_models_from_api(catalog, "deepseek")
    # Falls back silently to curated list.
    ids = [m.id for m in merged]
    assert "deepseek-v4-pro" in ids
