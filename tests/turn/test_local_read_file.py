"""Tests for core.turn.local_read_file — workspace-scoped file reader."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from core.turn.local_read_file import _allowed_roots, _is_inside_any, execute_local_read_file


def test_disabled_short_circuits(tmp_path):
    f = tmp_path / "x.txt"
    f.write_text("content")
    with patch("core.turn.local_read_file.config") as mock_config:
        mock_config.LOCAL_READ_FILE_ENABLED = False
        mock_config.AGENTIC_WORKSPACE_ROOT = str(tmp_path)
        mock_config.DESKTOP_ACTIVITY_WATCH_DIRS = ""
        mock_config.LOCAL_READ_FILE_MAX_BYTES = 1000
        result = execute_local_read_file(path=str(f))
    assert result.allowed is False
    assert "disabled" in result.refused_reason


def test_empty_path_refused():
    with patch("core.turn.local_read_file.config") as mock_config:
        mock_config.LOCAL_READ_FILE_ENABLED = True
        mock_config.AGENTIC_WORKSPACE_ROOT = "."
        mock_config.DESKTOP_ACTIVITY_WATCH_DIRS = ""
        mock_config.LOCAL_READ_FILE_MAX_BYTES = 1000
        result = execute_local_read_file(path="")
    assert result.allowed is False
    assert result.refused_reason == "empty path"


def test_path_inside_workspace_succeeds(tmp_path):
    f = tmp_path / "hello.py"
    f.write_text("print('hi')")
    with patch("core.turn.local_read_file.config") as mock_config:
        mock_config.LOCAL_READ_FILE_ENABLED = True
        mock_config.AGENTIC_WORKSPACE_ROOT = str(tmp_path)
        mock_config.DESKTOP_ACTIVITY_WATCH_DIRS = ""
        mock_config.LOCAL_READ_FILE_MAX_BYTES = 1000
        result = execute_local_read_file(path=str(f))
    assert result.allowed is True
    assert "print" in result.text


def test_path_outside_workspace_refused(tmp_path):
    inside = tmp_path / "inside"
    inside.mkdir()
    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()
    outside_file = outside_dir / "secret.txt"
    outside_file.write_text("nope")
    with patch("core.turn.local_read_file.config") as mock_config:
        mock_config.LOCAL_READ_FILE_ENABLED = True
        mock_config.AGENTIC_WORKSPACE_ROOT = str(inside)
        mock_config.DESKTOP_ACTIVITY_WATCH_DIRS = ""
        mock_config.LOCAL_READ_FILE_MAX_BYTES = 1000
        result = execute_local_read_file(path=str(outside_file))
    assert result.allowed is False
    assert "outside the allowed roots" in result.refused_reason


def test_relative_path_resolves_against_workspace(tmp_path):
    f = tmp_path / "rel.txt"
    f.write_text("relative content")
    with patch("core.turn.local_read_file.config") as mock_config:
        mock_config.LOCAL_READ_FILE_ENABLED = True
        mock_config.AGENTIC_WORKSPACE_ROOT = str(tmp_path)
        mock_config.DESKTOP_ACTIVITY_WATCH_DIRS = ""
        mock_config.LOCAL_READ_FILE_MAX_BYTES = 1000
        result = execute_local_read_file(path="rel.txt")
    assert result.allowed is True
    assert "relative content" in result.text


def test_truncation_marks_truncated(tmp_path):
    f = tmp_path / "big.txt"
    f.write_text("x" * 5000)
    with patch("core.turn.local_read_file.config") as mock_config:
        mock_config.LOCAL_READ_FILE_ENABLED = True
        mock_config.AGENTIC_WORKSPACE_ROOT = str(tmp_path)
        mock_config.DESKTOP_ACTIVITY_WATCH_DIRS = ""
        mock_config.LOCAL_READ_FILE_MAX_BYTES = 100
        result = execute_local_read_file(path=str(f))
    assert result.allowed is True
    assert result.truncated is True
    assert len(result.text) <= 100


def test_binary_file_is_flagged_not_decoded(tmp_path):
    f = tmp_path / "bin.dat"
    f.write_bytes(b"\x00\x01\x02\x03binary content")
    with patch("core.turn.local_read_file.config") as mock_config:
        mock_config.LOCAL_READ_FILE_ENABLED = True
        mock_config.AGENTIC_WORKSPACE_ROOT = str(tmp_path)
        mock_config.DESKTOP_ACTIVITY_WATCH_DIRS = ""
        mock_config.LOCAL_READ_FILE_MAX_BYTES = 1000
        result = execute_local_read_file(path=str(f))
    assert result.allowed is True
    assert result.is_binary is True
    assert result.text == ""
