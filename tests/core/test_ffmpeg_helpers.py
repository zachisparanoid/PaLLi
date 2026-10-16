from __future__ import annotations

import sys

import pytest

from core.ffmpeg import FFmpegError, run_ffmpeg, run_ffmpeg_checked


@pytest.mark.asyncio
async def test_run_ffmpeg_success():
    result = await run_ffmpeg([sys.executable, "-c", "print('ok')"], timeout_seconds=2.0)
    assert result.returncode == 0
    assert "ok" in result.stdout


@pytest.mark.asyncio
async def test_run_ffmpeg_checked_raises_on_nonzero():
    with pytest.raises(FFmpegError) as exc:
        await run_ffmpeg_checked(
            [sys.executable, "-c", "import sys; sys.stderr.write('bad'); sys.exit(2)"],
            timeout_seconds=2.0,
        )
    assert "code=2" in str(exc.value)


@pytest.mark.asyncio
async def test_run_ffmpeg_timeout():
    with pytest.raises(FFmpegError) as exc:
        await run_ffmpeg(
            [sys.executable, "-c", "import time; time.sleep(0.5)"],
            timeout_seconds=0.05,
        )
    assert "timed out" in str(exc.value)
