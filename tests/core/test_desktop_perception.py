from __future__ import annotations

import time
from pathlib import Path

import pytest

from core.config import config
from core.desktop_perception import DesktopPerceptionService


@pytest.mark.asyncio
async def test_get_adapter_health_degrades_when_capture_and_ocr_are_unavailable(monkeypatch):
    service = DesktopPerceptionService()
    monkeypatch.setattr(config, "OPERATOR_DESKTOP_VISION_ENABLED", True, raising=False)
    monkeypatch.setattr(config, "OPERATOR_OCR_ENABLED", True, raising=False)
    monkeypatch.setattr(service, "_capture_backend", lambda: "")
    monkeypatch.setattr(service, "_ocr_available", lambda: False)

    health = await service.get_adapter_health()

    assert health["desktop_capture"]["status"] == "degraded"
    assert health["desktop_ocr"]["status"] == "degraded"
    assert health["desktop_vision"]["status"] == "ready"


@pytest.mark.asyncio
async def test_classify_click_target_allows_unique_ocr_candidate(monkeypatch):
    service = DesktopPerceptionService()
    now = time.time()

    async def fake_snapshot(*, owner_user_id: int):
        assert owner_user_id == 7
        return {
            "last_inspection": {
                "inspection_id": "inspect_1",
                "created_at": now,
                "capture": {"window": {"window_id": "0x1"}},
                "target_candidates": [
                    {
                        "candidate_id": "ocr_1",
                        "label": "Retry",
                        "bbox": {"x": 10, "y": 20, "width": 30, "height": 10},
                        "source": "ocr",
                    }
                ],
                "sensitive_detected": False,
            },
            "last_resolved_target": {},
        }

    monkeypatch.setattr(service, "get_runtime_snapshot", fake_snapshot)
    monkeypatch.setattr(config, "OPERATOR_VISION_INSPECTION_MAX_AGE_SECONDS", 300, raising=False)

    decision = await service.classify_click_target(
        owner_user_id=7,
        target_text="Retry",
        current_focused_window={"window_id": "0x1"},
    )

    assert decision["decision"] == "allow"
    assert decision["reason"] == "resolved_target"
    assert decision["candidate"]["candidate_id"] == "ocr_1"


@pytest.mark.asyncio
async def test_classify_click_target_confirms_vision_only_guess(monkeypatch):
    service = DesktopPerceptionService()
    now = time.time()

    async def fake_snapshot(*, owner_user_id: int):
        assert owner_user_id == 7
        return {
            "last_inspection": {
                "inspection_id": "inspect_2",
                "created_at": now,
                "capture": {"window": {"window_id": "0x1"}},
                "target_candidates": [
                    {
                        "candidate_id": "vision_1",
                        "label": "Retry",
                        "bbox": {},
                        "source": "vision",
                    }
                ],
                "sensitive_detected": False,
            },
            "last_resolved_target": {},
        }

    monkeypatch.setattr(service, "get_runtime_snapshot", fake_snapshot)
    monkeypatch.setattr(config, "OPERATOR_VISION_INSPECTION_MAX_AGE_SECONDS", 300, raising=False)

    decision = await service.classify_click_target(
        owner_user_id=7,
        target_text="Retry",
        current_focused_window={"window_id": "0x1"},
    )

    assert decision["decision"] == "confirm"
    assert decision["reason"] == "vision_only_target_requires_confirmation"


@pytest.mark.asyncio
async def test_capture_view_persists_runtime_artifact(monkeypatch, tmp_path: Path):
    service = DesktopPerceptionService()
    captured: dict[str, object] = {}

    monkeypatch.setattr(service, "_capture_backend", lambda: "mss")
    monkeypatch.setattr(service, "_capture_dir", lambda owner_user_id: tmp_path / f"owner-{owner_user_id}")
    monkeypatch.setattr(service, "_resolve_capture_target", lambda **kwargs: ({"x": 1, "y": 2, "width": 3, "height": 4}, {"window_id": "0x1"}))

    def fake_capture_to_file(*, backend: str, output_path: Path, geometry):
        output_path.write_bytes(b"png")

    async def fake_create_runtime_artifact(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(service, "_capture_to_file", fake_capture_to_file)
    monkeypatch.setattr("core.desktop_perception.brain.create_runtime_artifact", fake_create_runtime_artifact)
    monkeypatch.setattr(config, "OPERATOR_SCREENSHOT_RETENTION_SECONDS", 300.0, raising=False)

    record = await service.capture_view(owner_user_id=7)

    assert record["artifact_id"].startswith("art_capture_")
    assert record["expires_at"] == pytest.approx(record["created_at"] + 300.0)
    assert Path(record["path"]).exists()
    assert captured["artifact_id"] == record["artifact_id"]
    assert captured["owner_user_id"] == 7
    assert captured["source_kind"] == "desktop_capture"
    assert captured["source_id"] == record["capture_id"]
    assert captured["artifact_kind"] == "image"
    assert captured["storage_path"] == record["path"]
    assert captured["content_type"] == "image/png"
