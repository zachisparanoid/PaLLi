from __future__ import annotations

import importlib.util
import logging
import shutil
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.config import config
from core.database import brain
from core.host_platform import host_platform_service

logger = logging.getLogger(__name__)


class DesktopPerceptionService:
    def _repo_root(self) -> Path:
        return Path(__file__).resolve().parents[1]

    def _resolve_local_path(self, raw_path: str, *, default_relative: str) -> Path:
        text = str(raw_path or "").strip() or default_relative
        path = Path(text).expanduser()
        if not path.is_absolute():
            path = (self._repo_root() / path).resolve()
        else:
            path = path.resolve()
        return path

    def _capture_dir(self, owner_user_id: int) -> Path:
        base = self._resolve_local_path(
            getattr(config, "OPERATOR_SCREENSHOT_DIR", ""),
            default_relative="data/operator-desktop/captures",
        )
        return base / f"owner-{int(owner_user_id)}"

    def _binary_available(self, name: str) -> bool:
        return host_platform_service.command_available(name)

    def _module_available(self, module_name: str) -> bool:
        try:
            return importlib.util.find_spec(module_name) is not None
        except ModuleNotFoundError:
            return False

    def _ocr_available(self) -> bool:
        return bool(getattr(config, "OPERATOR_OCR_ENABLED", True)) and self._module_available("pytesseract") and self._binary_available("tesseract")

    def _vision_available(self) -> bool:
        return bool(getattr(config, "OPERATOR_DESKTOP_VISION_ENABLED", True))

    def _capture_backend(self) -> str:
        return host_platform_service.capture_backend(
            module_available=self._module_available,
            binary_available=self._binary_available,
        )

    def _list_windows(self) -> List[Dict[str, Any]]:
        return host_platform_service.list_windows()

    def _focused_window_snapshot(self) -> Optional[Dict[str, Any]]:
        return host_platform_service.focused_window_snapshot()

    def _match_windows(self, query: str) -> List[Dict[str, Any]]:
        return host_platform_service.match_windows(str(query or ""))

    async def get_passive_snapshot(self) -> Dict[str, Any]:
        """Cheap read of current desktop state. No capture, no OCR, no LLM work.

        Returns focused-window metadata, adapter health, and the capture
        backend name. Intended for consumers like the inner monologue that
        need situational awareness without triggering an operator task.
        """
        return {
            "focused_window": host_platform_service.focused_window_snapshot(),
            "adapter_health": await self.get_adapter_health(),
            "capture_backend": self._capture_backend(),
        }

    async def get_runtime_snapshot(self, *, owner_user_id: int) -> Dict[str, Any]:
        state = await brain.get_operator_state(owner_user_id=int(owner_user_id))
        metadata = dict((state or {}).get("metadata") or {})
        perception = dict(metadata.get("desktop_perception") or {})
        await self._prune_captures(owner_user_id=int(owner_user_id))
        return {
            "adapter_health": await self.get_adapter_health(),
            "last_inspection": dict(perception.get("last_inspection") or {}),
            "last_resolved_target": dict(perception.get("last_resolved_target") or {}),
        }

    async def get_adapter_health(self) -> Dict[str, Any]:
        backend = self._capture_backend()
        screenshot_status = "ready" if backend else "degraded"
        ocr_status = "ready" if self._ocr_available() else ("disabled" if not getattr(config, "OPERATOR_OCR_ENABLED", True) else "degraded")
        vision_status = "ready" if self._vision_available() else "disabled"
        return {
            "desktop_capture": {
                "status": screenshot_status,
                "message": f"{backend} capture available." if backend else "No screenshot backend is available.",
                "updated_at": time.time(),
            },
            "desktop_ocr": {
                "status": ocr_status,
                "message": "pytesseract + tesseract available." if ocr_status == "ready" else "OCR dependencies are unavailable.",
                "updated_at": time.time(),
            },
            "desktop_vision": {
                "status": vision_status,
                "message": "Vision inspection enabled." if vision_status == "ready" else "Vision inspection disabled.",
                "updated_at": time.time(),
            },
        }

    async def capture_view(
        self,
        *,
        owner_user_id: int,
        capture_scope: str = "focused_window",
        window_query: str = "",
        region: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        backend = self._capture_backend()
        if not backend:
            raise RuntimeError("desktop_capture_unavailable")
        capture_dir = self._capture_dir(int(owner_user_id))
        capture_dir.mkdir(parents=True, exist_ok=True)
        capture_id = f"capture_{uuid.uuid4().hex[:10]}"
        output_path = capture_dir / f"{capture_id}.png"
        geometry, window = self._resolve_capture_target(
            capture_scope=capture_scope,
            window_query=window_query,
            region=region,
        )
        self._capture_to_file(backend=backend, output_path=output_path, geometry=geometry)
        created_at = time.time()
        artifact_id = ""
        expires_at: Optional[float] = None
        retention_seconds = float(
            getattr(config, "OPERATOR_SCREENSHOT_RETENTION_SECONDS", 1800.0) or 1800.0
        )
        if retention_seconds > 0:
            expires_at = created_at + retention_seconds
        try:
            artifact_id = f"art_{capture_id}"
            await brain.create_runtime_artifact(
                artifact_id=artifact_id,
                owner_user_id=int(owner_user_id),
                source_kind="desktop_capture",
                source_id=capture_id,
                artifact_kind="image",
                storage_path=str(output_path),
                content_type="image/png",
                expires_at=expires_at,
                metadata={
                    "label": f"Desktop capture {capture_id}",
                    "capture_id": capture_id,
                    "capture_scope": capture_scope,
                    "backend": backend,
                    "window": window,
                    "geometry": geometry,
                },
                created_at=created_at,
            )
        except Exception:
            artifact_id = ""
            logger.debug(
                "Failed to persist runtime artifact for desktop capture %s",
                capture_id,
                exc_info=True,
            )
        return {
            "capture_id": capture_id,
            "artifact_id": artifact_id,
            "capture_scope": capture_scope,
            "path": str(output_path),
            "backend": backend,
            "created_at": created_at,
            "expires_at": expires_at,
            "window": window,
            "geometry": geometry,
        }

    async def extract_text(
        self,
        *,
        owner_user_id: int,
        capture_scope: str = "focused_window",
        window_query: str = "",
        region: Optional[Dict[str, Any]] = None,
        capture_record: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        record = capture_record or await self.capture_view(
            owner_user_id=int(owner_user_id),
            capture_scope=capture_scope,
            window_query=window_query,
            region=region,
        )
        blocks = self._extract_ocr_blocks(record)
        return {
            "capture": record,
            "ocr_blocks": blocks,
            "ocr_backend": "pytesseract" if blocks or self._ocr_available() else "unavailable",
        }

    async def classify_click_target(
        self,
        *,
        owner_user_id: int,
        target_text: str = "",
        candidate_id: str = "",
        current_focused_window: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        snapshot = await self.get_runtime_snapshot(owner_user_id=int(owner_user_id))
        inspection = dict(snapshot.get("last_inspection") or {})
        if not inspection:
            return {"decision": "block", "reason": "inspection_required"}
        created_at = float(inspection.get("created_at") or 0.0)
        if (time.time() - created_at) > float(getattr(config, "OPERATOR_VISION_INSPECTION_MAX_AGE_SECONDS", 300.0) or 300.0):
            return {"decision": "confirm", "reason": "stale_inspection"}
        if inspection.get("sensitive_detected"):
            return {"decision": "block", "reason": "sensitive_ui_blocked"}
        inspection_window = dict(((inspection.get("capture") or {}).get("window") or {}))
        if current_focused_window and inspection_window:
            current_id = str(current_focused_window.get("window_id") or "").strip().lower()
            inspect_id = str(inspection_window.get("window_id") or "").strip().lower()
            if current_id and inspect_id and current_id != inspect_id:
                return {"decision": "confirm", "reason": "window_changed_since_inspection"}
        matches = self._match_candidates(
            list(inspection.get("target_candidates") or []),
            target_text=str(target_text or "").strip(),
            candidate_id=str(candidate_id or "").strip(),
        )
        if not matches:
            return {"decision": "block", "reason": "target_not_found"}
        if len(matches) > 1:
            return {"decision": "confirm", "reason": "ambiguous_target_resolution"}
        candidate = matches[0]
        if str(candidate.get("source") or "").strip() == "vision":
            return {"decision": "confirm", "reason": "vision_only_target_requires_confirmation", "candidate": candidate}
        return {"decision": "allow", "reason": "resolved_target", "candidate": candidate}

    async def resolve_click_target(
        self,
        *,
        owner_user_id: int,
        target_text: str = "",
        candidate_id: str = "",
    ) -> Dict[str, Any]:
        snapshot = await self.get_runtime_snapshot(owner_user_id=int(owner_user_id))
        inspection = dict(snapshot.get("last_inspection") or {})
        decision = await self.classify_click_target(
            owner_user_id=int(owner_user_id),
            target_text=target_text,
            candidate_id=candidate_id,
            current_focused_window=self._focused_window_snapshot(),
        )
        candidate = dict(decision.get("candidate") or {})
        if not candidate:
            raise RuntimeError(str(decision.get("reason") or "target_not_found"))
        bbox = dict(candidate.get("bbox") or {})
        x = int(bbox.get("x") or 0) + max(int(bbox.get("width") or 0) // 2, 0)
        y = int(bbox.get("y") or 0) + max(int(bbox.get("height") or 0) // 2, 0)
        resolved = {
            "resolved_at": time.time(),
            "inspection_id": str(inspection.get("inspection_id") or ""),
            "candidate_id": str(candidate.get("candidate_id") or ""),
            "label": str(candidate.get("label") or ""),
            "x": x,
            "y": y,
            "window": dict(((inspection.get("capture") or {}).get("window") or {})),
            "source": str(candidate.get("source") or ""),
        }
        return resolved

    async def focus_capture_window(self, *, owner_user_id: int) -> Dict[str, Any]:
        snapshot = await self.get_runtime_snapshot(owner_user_id=int(owner_user_id))
        inspection = dict(snapshot.get("last_inspection") or {})
        window = dict(((inspection.get("capture") or {}).get("window") or {}))
        if not window:
            return {"ok": False, "reason": "no_inspection_window"}
        window_id = str(window.get("window_id") or "").strip()
        if not window_id:
            return {"ok": False, "reason": "missing_window_id"}
        return {"ok": bool(host_platform_service.focus_window(window_id)), "window": window}

    async def _persist_runtime_state(
        self,
        *,
        owner_user_id: int,
        last_inspection: Optional[Dict[str, Any]] = None,
        last_resolved_target: Optional[Dict[str, Any]] = None,
        operator_action_id: Optional[str] = None,
    ) -> None:
        snapshot = await self.get_runtime_snapshot(owner_user_id=int(owner_user_id))
        perception = {
            "last_inspection": last_inspection if last_inspection is not None else snapshot.get("last_inspection") or {},
            "last_resolved_target": last_resolved_target if last_resolved_target is not None else snapshot.get("last_resolved_target") or {},
        }
        await brain.upsert_operator_state(
            owner_user_id=int(owner_user_id),
            last_operator_activity_at=time.time(),
            metadata={"desktop_perception": perception},
        )
        clean_action_id = str(operator_action_id or "").strip()
        if clean_action_id:
            await brain.update_operator_action(
                action_id=clean_action_id,
                metadata={"desktop_perception": perception},
            )

    async def store_resolved_target(
        self,
        *,
        owner_user_id: int,
        resolved_target: Dict[str, Any],
        operator_action_id: Optional[str] = None,
    ) -> None:
        await self._persist_runtime_state(
            owner_user_id=int(owner_user_id),
            last_resolved_target=dict(resolved_target or {}),
            operator_action_id=str(operator_action_id or "").strip() or None,
        )

    async def _prune_captures(self, *, owner_user_id: int) -> None:
        retention = float(getattr(config, "OPERATOR_SCREENSHOT_RETENTION_SECONDS", 1800.0) or 1800.0)
        if retention <= 0:
            return
        capture_dir = self._capture_dir(int(owner_user_id))
        if not capture_dir.exists():
            return
        cutoff = time.time() - retention
        for candidate in capture_dir.glob("*.png"):
            try:
                if candidate.stat().st_mtime < cutoff:
                    candidate.unlink(missing_ok=True)
            except Exception:
                logger.debug("Failed to prune capture %s", candidate, exc_info=True)

    def _resolve_capture_target(
        self,
        *,
        capture_scope: str,
        window_query: str,
        region: Optional[Dict[str, Any]],
    ) -> tuple[Optional[Dict[str, Any]], Dict[str, Any]]:
        scope = str(capture_scope or "focused_window").strip().lower()
        if scope == "region" and isinstance(region, dict):
            geometry = {
                "x": int(region.get("x") or 0),
                "y": int(region.get("y") or 0),
                "width": max(int(region.get("width") or 0), 1),
                "height": max(int(region.get("height") or 0), 1),
            }
            return geometry, {}
        if scope == "window_query" and str(window_query or "").strip():
            matches = self._match_windows(window_query)
            if len(matches) == 1 and all(key in matches[0] for key in ("x", "y", "width", "height")):
                match = matches[0]
                return {
                    "x": int(match.get("x") or 0),
                    "y": int(match.get("y") or 0),
                    "width": max(int(match.get("width") or 0), 1),
                    "height": max(int(match.get("height") or 0), 1),
                }, match
        if scope == "full_screen":
            return None, {}
        focused = self._focused_window_snapshot() or {}
        if focused and all(key in focused for key in ("x", "y", "width", "height")):
            return {
                "x": int(focused.get("x") or 0),
                "y": int(focused.get("y") or 0),
                "width": max(int(focused.get("width") or 0), 1),
                "height": max(int(focused.get("height") or 0), 1),
            }, focused
        return None, focused

    def _capture_to_file(self, *, backend: str, output_path: Path, geometry: Optional[Dict[str, Any]]) -> None:
        host_platform_service.capture_to_file(
            backend=backend,
            output_path=output_path,
            geometry=geometry,
        )

    def _extract_ocr_blocks(self, capture: Dict[str, Any]) -> List[Dict[str, Any]]:
        if not self._ocr_available():
            return []
        import pytesseract
        from PIL import Image

        image = Image.open(str(capture["path"]))
        data = pytesseract.image_to_data(image, output_type=pytesseract.Output.DICT)
        blocks: List[Dict[str, Any]] = []
        geometry = dict(capture.get("geometry") or {})
        origin_x = int(geometry.get("x") or 0)
        origin_y = int(geometry.get("y") or 0)
        count = len(data.get("text") or [])
        for index in range(count):
            text = str((data.get("text") or [""])[index] or "").strip()
            if not text:
                continue
            try:
                conf = float((data.get("conf") or ["0"])[index] or 0.0)
            except Exception:
                conf = 0.0
            left = int((data.get("left") or [0])[index] or 0)
            top = int((data.get("top") or [0])[index] or 0)
            width = int((data.get("width") or [0])[index] or 0)
            height = int((data.get("height") or [0])[index] or 0)
            blocks.append(
                {
                    "text": text,
                    "confidence": conf,
                    "bbox": {
                        "x": origin_x + left,
                        "y": origin_y + top,
                        "width": width,
                        "height": height,
                    },
                }
            )
        return blocks

    def _match_candidates(
        self,
        candidates: List[Dict[str, Any]],
        *,
        target_text: str,
        candidate_id: str,
    ) -> List[Dict[str, Any]]:
        clean_candidate_id = str(candidate_id or "").strip()
        if clean_candidate_id:
            return [item for item in candidates if str(item.get("candidate_id") or "").strip() == clean_candidate_id]
        clean_target = str(target_text or "").strip().lower()
        if not clean_target:
            return candidates
        exact = [item for item in candidates if str(item.get("label") or "").strip().lower() == clean_target]
        if exact:
            return exact
        return [item for item in candidates if clean_target in str(item.get("label") or "").strip().lower()]


desktop_perception_service = DesktopPerceptionService()
