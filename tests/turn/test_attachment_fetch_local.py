"""``fetch_attachment_bytes`` gains ONE branch: a local file (telegram door,
task 2).

Discord hands out https URLs anyone holding the link can GET. Telegram's
file URL carries her bot token in the path, so this door materializes the
bytes to disk at intake and puts a ``file://`` ref on the turn instead. The
readers downstream (recall's vision read, the text-attachment read, §19's
inbox) all reach their bytes through this one function, so teaching it to
read a local file is the whole of it — nothing else changes.

The http path must stay byte-identical: that is the regression guard here.
"""
from __future__ import annotations

import pytest

from core.turn.attachments import fetch_attachment_bytes


@pytest.mark.asyncio
async def test_a_file_url_is_read_from_disk(tmp_path):
    path = tmp_path / "notes.txt"
    path.write_bytes(b"the torque spec is 12 Nm")

    assert await fetch_attachment_bytes(f"file://{path}") == b"the torque spec is 12 Nm"


@pytest.mark.asyncio
async def test_a_bare_existing_path_is_read_too(tmp_path):
    """ImageRef.local_path is passed to this function as the 'url' by
    describe_images, so a plain path has to work as well as a file:// one."""
    path = tmp_path / "photo.jpg"
    path.write_bytes(b"\xff\xd8\xff\xd9")

    assert await fetch_attachment_bytes(str(path)) == b"\xff\xd8\xff\xd9"


@pytest.mark.asyncio
async def test_a_missing_local_file_is_none_not_a_raise(tmp_path):
    assert await fetch_attachment_bytes(f"file://{tmp_path / 'gone.txt'}") is None


@pytest.mark.asyncio
async def test_an_http_url_still_goes_through_aiohttp(monkeypatch):
    """The regression guard: Discord's path is untouched. Prove it by
    watching the aiohttp session get built and the URL handed to it."""
    import aiohttp

    seen: dict = {}

    class _Resp:
        status = 200

        async def read(self):
            return b"discord bytes"

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    class _Session:
        def __init__(self, *a, **kw):
            pass

        def get(self, url, **kw):
            seen["url"] = url
            return _Resp()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(aiohttp, "ClientSession", _Session)

    out = await fetch_attachment_bytes("https://cdn.discordapp.com/attachments/1/2/x.txt")

    assert out == b"discord bytes"
    assert seen["url"] == "https://cdn.discordapp.com/attachments/1/2/x.txt"
