"""Provider validation — PONG ping returns ValidationResult."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from palli_config.providers import ValidationResult, validate_provider


@pytest.mark.asyncio
async def test_validate_returns_ok_on_successful_pong():
    fake_provider = MagicMock()
    fake_provider.chat_completion = AsyncMock(return_value="PONG")
    result = await validate_provider("openai", _provider_factory=lambda _: fake_provider)
    assert isinstance(result, ValidationResult)
    assert result.ok is True
    assert result.error is None
    assert result.latency_ms >= 0


@pytest.mark.asyncio
async def test_validate_returns_error_on_exception():
    fake_provider = MagicMock()
    fake_provider.chat_completion = AsyncMock(side_effect=RuntimeError("auth failed"))
    result = await validate_provider("openai", _provider_factory=lambda _: fake_provider)
    assert result.ok is False
    assert "auth failed" in (result.error or "")


@pytest.mark.asyncio
async def test_validate_returns_error_on_none_response():
    """Reasoning models that burn budget return None — count as failure for validation."""
    fake_provider = MagicMock()
    fake_provider.chat_completion = AsyncMock(return_value=None)
    result = await validate_provider("openai", _provider_factory=lambda _: fake_provider)
    assert result.ok is False
    assert result.error is not None
