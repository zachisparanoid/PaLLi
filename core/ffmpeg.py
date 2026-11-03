"""
Async ffmpeg helpers.

Goal: never block the main asyncio event loop on ffmpeg conversions, and cap
concurrent conversions to prevent CPU spikes.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Optional, Sequence

from core.config import config


def _default_concurrency() -> int:
    raw = getattr(config, "FFMPEG_MAX_CONCURRENCY", 2)
    try:
        value = int(raw)
    except Exception:
        value = 2
    return max(1, min(16, value))


_FFMPEG_SEMAPHORE = asyncio.Semaphore(_default_concurrency())


@dataclass(frozen=True)
class FFmpegResult:
    returncode: int
    stdout: str
    stderr: str


class FFmpegError(RuntimeError):
    pass


async def run_ffmpeg(
    args: Sequence[str],
    *,
    timeout_seconds: Optional[float] = 90.0,
) -> FFmpegResult:
    if not args:
        raise ValueError("ffmpeg args are required")

    async with _FFMPEG_SEMAPHORE:
        proc = await asyncio.create_subprocess_exec(
            *list(args),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            if timeout_seconds is None:
                out_b, err_b = await proc.communicate()
            else:
                out_b, err_b = await asyncio.wait_for(proc.communicate(), timeout=float(timeout_seconds))
        except asyncio.TimeoutError as e:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            await proc.wait()
            raise FFmpegError(f"ffmpeg timed out after {timeout_seconds}s") from e

        stdout = out_b.decode("utf-8", errors="replace") if out_b else ""
        stderr = err_b.decode("utf-8", errors="replace") if err_b else ""
        return FFmpegResult(returncode=int(proc.returncode or 0), stdout=stdout, stderr=stderr)


async def run_ffmpeg_checked(
    args: Sequence[str],
    *,
    timeout_seconds: Optional[float] = 90.0,
) -> FFmpegResult:
    result = await run_ffmpeg(args, timeout_seconds=timeout_seconds)
    if result.returncode != 0:
        raise FFmpegError(f"ffmpeg failed (code={result.returncode}): {result.stderr.strip()}")
    return result

