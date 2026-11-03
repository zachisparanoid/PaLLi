from __future__ import annotations

import ctypes
import importlib.util
import os
import platform as py_platform
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional

from core.config import config


def _module_available(module_name: str) -> bool:
    try:
        return importlib.util.find_spec(module_name) is not None
    except ModuleNotFoundError:
        return False


def _command_available(name: str) -> bool:
    return bool(shutil.which(name))


def _run_command(args: List[str], *, check: bool = True, timeout: float = 20.0) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=timeout,
        check=check,
    )


def _normalize_query_list(queries: Iterable[str]) -> List[str]:
    return [str(item or "").strip().lower() for item in queries if str(item or "").strip()]


def _match_windows_from_list(windows: List[Dict[str, Any]], *queries: str) -> List[Dict[str, Any]]:
    normalized = _normalize_query_list(queries)
    if not normalized:
        return []
    matches: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for item in windows:
        haystack = " ".join(
            [
                str(item.get("window_id") or ""),
                str(item.get("wm_class") or ""),
                str(item.get("title") or ""),
                str(item.get("process_name") or ""),
            ]
        ).lower()
        if any(query in haystack for query in normalized):
            window_id = str(item.get("window_id") or "")
            if window_id and window_id not in seen:
                matches.append(item)
                seen.add(window_id)
    return matches


@dataclass(slots=True)
class HostPlatformSnapshot:
    host_platform: str
    host_backend: str
    display_server: str
    description: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "host_platform": self.host_platform,
            "host_backend": self.host_backend,
            "display_server": self.display_server,
            "description": self.description,
        }


class BaseHostBackend:
    platform_name = "unsupported"
    backend_name = "unsupported"

    def display_server(self) -> str:
        return ""

    def description(self) -> str:
        return self.backend_name.replace("_", " ")

    def supports_open_target(self) -> bool:
        return False

    def supports_notifications(self) -> bool:
        return False

    def supports_window_control(self) -> bool:
        return False

    def supports_input_control(self) -> bool:
        return False

    def supports_machine_presence(self) -> bool:
        return False

    def list_windows(self) -> List[Dict[str, Any]]:
        return []

    def focused_window_snapshot(self) -> Optional[Dict[str, Any]]:
        return None

    def match_windows(self, *queries: str) -> List[Dict[str, Any]]:
        return _match_windows_from_list(self.list_windows(), *queries)

    def open_target(self, target: str, *, kind: str = "auto") -> bool:
        return False

    def reveal_path(self, path: str) -> bool:
        return False

    def notify(self, *, title: str, body: str, urgency: str = "normal") -> bool:
        return False

    def focus_window(self, window_id: str) -> bool:
        return False

    def type_text(self, text: str, *, delay_ms: int = 20) -> bool:
        return False

    def send_keys(self, keys: str) -> bool:
        return False

    def click(self, *, x: int, y: int, button: int = 1) -> bool:
        return False

    def capture_backend(
        self,
        *,
        module_available: Callable[[str], bool] = _module_available,
        binary_available: Callable[[str], bool] = _command_available,
    ) -> str:
        if module_available("mss"):
            return "mss"
        return ""

    def capture_to_file(
        self,
        *,
        backend: str,
        output_path: Path,
        geometry: Optional[Dict[str, Any]],
    ) -> None:
        if backend != "mss":
            raise RuntimeError("desktop_capture_unavailable")
        import mss
        import mss.tools

        with mss.mss() as sct:
            if geometry:
                monitor = {
                    "left": int(geometry["x"]),
                    "top": int(geometry["y"]),
                    "width": int(geometry["width"]),
                    "height": int(geometry["height"]),
                }
            else:
                monitor = sct.monitors[0]
            shot = sct.grab(monitor)
            mss.tools.to_png(shot.rgb, shot.size, output=str(output_path))

    def detect_machine_state(self, *, idle_threshold_seconds: float) -> str:
        raise RuntimeError("machine_presence_unsupported")

    def capability_health(
        self,
        *,
        desktop_enabled: bool,
        input_enabled: bool,
        machine_presence_enabled: bool,
        capture_available: bool,
        ocr_available: bool,
    ) -> Dict[str, Any]:
        now = time.time()
        return {
            "desktop_open": {
                "status": "ready" if desktop_enabled and self.supports_open_target() else ("disabled" if not desktop_enabled else "degraded"),
                "message": "Desktop open/reveal routing is available." if self.supports_open_target() else "Desktop open/reveal routing is unavailable on this host.",
                "updated_at": now,
            },
            "desktop_notifications": {
                "status": "ready" if desktop_enabled and self.supports_notifications() else ("disabled" if not desktop_enabled else "degraded"),
                "message": "Desktop notifications are available." if self.supports_notifications() else "Desktop notifications are unavailable on this host.",
                "updated_at": now,
            },
            "window_control": {
                "status": "ready" if desktop_enabled and self.supports_window_control() else ("disabled" if not desktop_enabled else "degraded"),
                "message": "Window enumeration and focus are available." if self.supports_window_control() else "Window enumeration and focus are unavailable on this host.",
                "updated_at": now,
            },
            "input_control": {
                "status": (
                    "ready"
                    if desktop_enabled and input_enabled and self.supports_input_control()
                    else ("disabled" if (not desktop_enabled or not input_enabled) else "degraded")
                ),
                "message": "Keyboard and mouse input control are available." if self.supports_input_control() else "Keyboard and mouse input control are unavailable on this host.",
                "updated_at": now,
            },
            "machine_presence": {
                "status": (
                    "ready"
                    if machine_presence_enabled and self.supports_machine_presence()
                    else ("disabled" if not machine_presence_enabled else "degraded")
                ),
                "message": "Machine presence is available." if self.supports_machine_presence() else "Machine presence is unavailable on this host.",
                "updated_at": now,
            },
            "desktop_capture": {
                "status": "ready" if capture_available else "degraded",
                "message": "Desktop capture is available." if capture_available else "Desktop capture is unavailable on this host.",
                "updated_at": now,
            },
            "desktop_ocr": {
                "status": "ready" if ocr_available else ("disabled" if not getattr(config, "OPERATOR_OCR_ENABLED", True) else "degraded"),
                "message": "OCR is available." if ocr_available else "OCR is unavailable on this host.",
                "updated_at": now,
            },
        }


class LinuxX11Backend(BaseHostBackend):
    platform_name = "linux"
    backend_name = "linux_x11"

    def display_server(self) -> str:
        return "x11"

    def description(self) -> str:
        return "Linux X11"

    def supports_open_target(self) -> bool:
        return _command_available("xdg-open")

    def supports_notifications(self) -> bool:
        return _command_available("notify-send")

    def supports_window_control(self) -> bool:
        return _command_available("wmctrl") and _command_available("xdotool")

    def supports_input_control(self) -> bool:
        return _command_available("xdotool")

    def supports_machine_presence(self) -> bool:
        return _command_available("loginctl")

    def list_windows(self) -> List[Dict[str, Any]]:
        if not _command_available("wmctrl"):
            return []
        result = _run_command(["wmctrl", "-lGx"], check=False, timeout=10.0)
        if result.returncode != 0:
            result = _run_command(["wmctrl", "-lx"], check=False, timeout=10.0)
            if result.returncode != 0:
                return []
            windows: List[Dict[str, Any]] = []
            for line in str(result.stdout or "").splitlines():
                parts = line.split(None, 4)
                if len(parts) < 5:
                    continue
                windows.append(
                    {
                        "window_id": parts[0],
                        "desktop": parts[1],
                        "host": parts[2],
                        "wm_class": parts[3],
                        "title": parts[4],
                    }
                )
            return windows

        windows: List[Dict[str, Any]] = []
        for line in str(result.stdout or "").splitlines():
            parts = line.split(None, 7)
            if len(parts) < 8:
                continue
            windows.append(
                {
                    "window_id": parts[0],
                    "desktop": parts[1],
                    "x": int(parts[2]),
                    "y": int(parts[3]),
                    "width": int(parts[4]),
                    "height": int(parts[5]),
                    "wm_class": parts[6],
                    "title": parts[7],
                }
            )
        return windows

    def focused_window_snapshot(self) -> Optional[Dict[str, Any]]:
        if not _command_available("xdotool"):
            return None
        focus = _run_command(["xdotool", "getwindowfocus"], check=False, timeout=10.0)
        if focus.returncode != 0:
            return None
        window_id = str(focus.stdout or "").strip()
        title_result = _run_command(["xdotool", "getwindowfocus", "getwindowname"], check=False, timeout=10.0)
        title = str(title_result.stdout or "").strip()
        match = next((item for item in self.list_windows() if str(item.get("window_id") or "").lower() == window_id.lower()), None)
        snapshot = {
            "window_id": window_id,
            "title": title or str((match or {}).get("title") or ""),
            "wm_class": str((match or {}).get("wm_class") or ""),
        }
        if match:
            for key in ("x", "y", "width", "height"):
                if key in match:
                    snapshot[key] = int(match.get(key) or 0)
        return snapshot

    def open_target(self, target: str, *, kind: str = "auto") -> bool:
        if not self.supports_open_target():
            return False
        _run_command(["xdg-open", target], check=False, timeout=10.0)
        return True

    def reveal_path(self, path: str) -> bool:
        if not self.supports_open_target():
            return False
        resolved = Path(path).expanduser().resolve()
        target = resolved.parent if resolved.is_file() else resolved
        _run_command(["xdg-open", str(target)], check=False, timeout=10.0)
        return True

    def notify(self, *, title: str, body: str, urgency: str = "normal") -> bool:
        if not self.supports_notifications():
            return False
        _run_command(["notify-send", "-u", urgency, title, body], check=False, timeout=10.0)
        return True

    def focus_window(self, window_id: str) -> bool:
        if not self.supports_window_control():
            return False
        _run_command(["wmctrl", "-ia", str(window_id)], check=False, timeout=10.0)
        return True

    def type_text(self, text: str, *, delay_ms: int = 20) -> bool:
        if not self.supports_input_control():
            return False
        _run_command(["xdotool", "type", "--delay", str(max(int(delay_ms), 0)), str(text)], check=False, timeout=20.0)
        return True

    def send_keys(self, keys: str) -> bool:
        if not self.supports_input_control():
            return False
        _run_command(["xdotool", "key", str(keys)], check=False, timeout=10.0)
        return True

    def click(self, *, x: int, y: int, button: int = 1) -> bool:
        if not self.supports_input_control():
            return False
        _run_command(["xdotool", "mousemove", str(int(x)), str(int(y)), "click", str(int(button))], check=False, timeout=10.0)
        return True

    def capture_backend(
        self,
        *,
        module_available: Callable[[str], bool] = _module_available,
        binary_available: Callable[[str], bool] = _command_available,
    ) -> str:
        if module_available("mss"):
            return "mss"
        if binary_available("grim"):
            return "grim"
        if binary_available("gnome-screenshot"):
            return "gnome-screenshot"
        if binary_available("import"):
            return "import"
        return ""

    def capture_to_file(
        self,
        *,
        backend: str,
        output_path: Path,
        geometry: Optional[Dict[str, Any]],
    ) -> None:
        if backend == "mss":
            return super().capture_to_file(backend=backend, output_path=output_path, geometry=geometry)
        if backend == "grim":
            args = ["grim", str(output_path)]
            if geometry:
                args = ["grim", "-g", f'{geometry["x"]},{geometry["y"]} {geometry["width"]}x{geometry["height"]}', str(output_path)]
            _run_command(args, check=False, timeout=20.0)
            return
        if backend == "gnome-screenshot":
            _run_command(["gnome-screenshot", "-f", str(output_path)], check=False, timeout=20.0)
            return
        if backend == "import":
            if geometry:
                _run_command(
                    ["import", "-window", "root", "-crop", f'{geometry["width"]}x{geometry["height"]}+{geometry["x"]}+{geometry["y"]}', str(output_path)],
                    check=False,
                    timeout=20.0,
                )
            else:
                _run_command(["import", "-window", "root", str(output_path)], check=False, timeout=20.0)
            return
        raise RuntimeError("desktop_capture_unavailable")

    def detect_machine_state(self, *, idle_threshold_seconds: float) -> str:
        session_id = str(os.environ.get("XDG_SESSION_ID", "")).strip()
        if not session_id:
            username = os.environ.get("USER", "").strip()
            proc = _run_command(["loginctl", "list-sessions", "--no-legend"], check=False, timeout=10.0)
            if proc.returncode != 0:
                raise RuntimeError("loginctl_list_sessions_failed")
            for raw_line in str(proc.stdout or "").splitlines():
                parts = raw_line.strip().split()
                if len(parts) >= 3 and parts[2] == username:
                    session_id = parts[0]
                    break
        if not session_id:
            raise RuntimeError("no_linux_login_session")
        proc = _run_command(
            ["loginctl", "show-session", session_id, "-p", "Active", "-p", "LockedHint", "-p", "IdleHint"],
            check=False,
            timeout=10.0,
        )
        if proc.returncode != 0:
            raise RuntimeError("loginctl_show_session_failed")
        props: Dict[str, str] = {}
        for raw_line in str(proc.stdout or "").splitlines():
            if "=" not in raw_line:
                continue
            key, value = raw_line.split("=", 1)
            props[key.strip()] = value.strip().lower()
        if props.get("LockedHint") == "yes":
            return "locked"
        if props.get("IdleHint") == "yes":
            return "idle"
        if props.get("Active") == "yes":
            return "active"
        return "unknown"


class LinuxWaylandBackend(LinuxX11Backend):
    backend_name = "linux_wayland"

    def display_server(self) -> str:
        return "wayland"

    def description(self) -> str:
        return "Linux Wayland"

    def supports_window_control(self) -> bool:
        return False

    def supports_input_control(self) -> bool:
        return False

    def list_windows(self) -> List[Dict[str, Any]]:
        return []

    def focused_window_snapshot(self) -> Optional[Dict[str, Any]]:
        return None

    def focus_window(self, window_id: str) -> bool:
        _ = window_id
        return False

    def type_text(self, text: str, *, delay_ms: int = 20) -> bool:
        _ = (text, delay_ms)
        return False

    def send_keys(self, keys: str) -> bool:
        _ = keys
        return False

    def click(self, *, x: int, y: int, button: int = 1) -> bool:
        _ = (x, y, button)
        return False

    def capability_health(
        self,
        *,
        desktop_enabled: bool,
        input_enabled: bool,
        machine_presence_enabled: bool,
        capture_available: bool,
        ocr_available: bool,
    ) -> Dict[str, Any]:
        items = super().capability_health(
            desktop_enabled=desktop_enabled,
            input_enabled=input_enabled,
            machine_presence_enabled=machine_presence_enabled,
            capture_available=capture_available,
            ocr_available=ocr_available,
        )
        items["window_control"]["message"] = "Wayland is detected; generic window enumeration and focus are not parity-ready in this pass."
        items["input_control"]["message"] = "Wayland is detected; generic keyboard and mouse control are intentionally degraded in this pass."
        return items


class WindowsBackend(BaseHostBackend):
    platform_name = "windows"
    backend_name = "windows_native"

    def display_server(self) -> str:
        return "win32"

    def description(self) -> str:
        return "Windows Native"

    def supports_open_target(self) -> bool:
        return hasattr(os, "startfile")

    def supports_notifications(self) -> bool:
        return _module_available("win11toast")

    def supports_window_control(self) -> bool:
        return self._user32_available()

    def supports_input_control(self) -> bool:
        return self._user32_available() and (_module_available("pywinauto") or True)

    def supports_machine_presence(self) -> bool:
        return self._user32_available()

    def _user32_available(self) -> bool:
        return hasattr(ctypes, "windll") and hasattr(ctypes.windll, "user32")

    def _enum_windows(self) -> List[int]:
        if not self._user32_available():
            return []
        user32 = ctypes.windll.user32
        windows: List[int] = []
        callback_type = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

        def _callback(hwnd: int, lparam: int) -> bool:
            if not user32.IsWindowVisible(hwnd):
                return True
            windows.append(int(hwnd))
            return True

        user32.EnumWindows(callback_type(_callback), 0)
        return windows

    def _window_text(self, hwnd: int) -> str:
        user32 = ctypes.windll.user32
        length = user32.GetWindowTextLengthW(hwnd)
        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buffer, length + 1)
        return str(buffer.value or "").strip()

    def _window_class(self, hwnd: int) -> str:
        user32 = ctypes.windll.user32
        buffer = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, buffer, 255)
        return str(buffer.value or "").strip()

    def _window_rect(self, hwnd: int) -> Dict[str, int]:
        import ctypes.wintypes

        user32 = ctypes.windll.user32
        rect = ctypes.wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        return {
            "x": int(rect.left),
            "y": int(rect.top),
            "width": max(int(rect.right - rect.left), 0),
            "height": max(int(rect.bottom - rect.top), 0),
        }

    def _window_process_name(self, hwnd: int) -> str:
        if not bool(getattr(config, "HOST_CAPABILITY_DIAGNOSTICS_VERBOSE", False)):
            return ""
        try:
            import ctypes.wintypes

            user32 = ctypes.windll.user32
            pid = ctypes.wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            raw_pid = int(pid.value or 0)
            if raw_pid <= 0:
                return ""
            proc = subprocess.run(
                ["powershell", "-NoProfile", "-Command", f"(Get-Process -Id {raw_pid}).ProcessName"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=5,
                check=False,
            )
            return str(proc.stdout or "").strip().lower()
        except Exception:
            return ""

    def list_windows(self) -> List[Dict[str, Any]]:
        if not self._user32_available():
            return []
        windows: List[Dict[str, Any]] = []
        for hwnd in self._enum_windows():
            title = self._window_text(hwnd)
            wm_class = self._window_class(hwnd)
            if not title and not wm_class:
                continue
            item = {
                "window_id": str(hwnd),
                "title": title,
                "wm_class": wm_class,
                "process_name": self._window_process_name(hwnd),
            }
            item.update(self._window_rect(hwnd))
            windows.append(item)
        return windows

    def focused_window_snapshot(self) -> Optional[Dict[str, Any]]:
        if not self._user32_available():
            return None
        hwnd = int(ctypes.windll.user32.GetForegroundWindow() or 0)
        if hwnd <= 0:
            return None
        snapshot = {
            "window_id": str(hwnd),
            "title": self._window_text(hwnd),
            "wm_class": self._window_class(hwnd),
            "process_name": self._window_process_name(hwnd),
        }
        snapshot.update(self._window_rect(hwnd))
        return snapshot

    def open_target(self, target: str, *, kind: str = "auto") -> bool:
        _ = kind
        if not self.supports_open_target():
            return False
        os.startfile(target)  # type: ignore[attr-defined]
        return True

    def reveal_path(self, path: str) -> bool:
        if not self.supports_open_target():
            return False
        resolved = str(Path(path).expanduser().resolve())
        subprocess.run(["explorer.exe", f"/select,{resolved}"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=10, check=False)
        return True

    def notify(self, *, title: str, body: str, urgency: str = "normal") -> bool:
        _ = urgency
        if not self.supports_notifications():
            return False
        try:
            from win11toast import toast

            toast(title or "PaLLi", body or "")
            return True
        except Exception:
            return False

    def focus_window(self, window_id: str) -> bool:
        if not self.supports_window_control():
            return False
        hwnd = int(str(window_id or "0") or 0)
        if hwnd <= 0:
            return False
        user32 = ctypes.windll.user32
        user32.ShowWindow(hwnd, 5)
        return bool(user32.SetForegroundWindow(hwnd))

    def type_text(self, text: str, *, delay_ms: int = 20) -> bool:
        _ = delay_ms
        try:
            from pywinauto.keyboard import send_keys

            send_keys(str(text or ""), with_spaces=True, pause=max(int(delay_ms), 0) / 1000.0)
            return True
        except Exception:
            return False

    def send_keys(self, keys: str) -> bool:
        try:
            from pywinauto.keyboard import send_keys

            send_keys(str(keys or ""))
            return True
        except Exception:
            return False

    def click(self, *, x: int, y: int, button: int = 1) -> bool:
        if not self._user32_available():
            return False
        user32 = ctypes.windll.user32
        user32.SetCursorPos(int(x), int(y))
        if int(button) == 2:
            down, up = 0x0008, 0x0010
        else:
            down, up = 0x0002, 0x0004
        user32.mouse_event(down, 0, 0, 0, 0)
        user32.mouse_event(up, 0, 0, 0, 0)
        return True

    def detect_machine_state(self, *, idle_threshold_seconds: float) -> str:
        if not self._user32_available():
            raise RuntimeError("windows_user32_unavailable")
        user32 = ctypes.windll.user32
        try:
            input_desktop = user32.OpenInputDesktop(0, False, 0x0100)
            if not input_desktop:
                return "locked"
            user32.CloseDesktop(input_desktop)
        except Exception:
            pass

        class LASTINPUTINFO(ctypes.Structure):
            _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]

        info = LASTINPUTINFO()
        info.cbSize = ctypes.sizeof(LASTINPUTINFO)
        if not user32.GetLastInputInfo(ctypes.byref(info)):
            raise RuntimeError("windows_idle_detection_failed")
        idle_ms = ctypes.windll.kernel32.GetTickCount() - info.dwTime
        if idle_ms >= max(float(idle_threshold_seconds or 60.0), 1.0) * 1000.0:
            return "idle"
        return "active"


class UnsupportedBackend(BaseHostBackend):
    def __init__(self, platform_name: str) -> None:
        self.platform_name = platform_name
        self.backend_name = f"{platform_name}_unsupported"

    def description(self) -> str:
        return f"{self.platform_name.title()} unsupported"


class HostPlatformService:
    def __init__(self) -> None:
        self._backend: Optional[BaseHostBackend] = None

    def _display_server(self) -> str:
        session_type = str(os.environ.get("XDG_SESSION_TYPE", "") or "").strip().lower()
        if session_type in {"x11", "wayland"}:
            return session_type
        if os.environ.get("WAYLAND_DISPLAY"):
            return "wayland"
        if os.environ.get("DISPLAY"):
            return "x11"
        return ""

    def _platform_mode(self) -> str:
        return str(getattr(config, "HOST_PLATFORM_MODE", "auto") or "auto").strip().lower() or "auto"

    def reset_cache(self) -> None:
        self._backend = None

    def backend(self) -> BaseHostBackend:
        if self._backend is not None:
            return self._backend
        mode = self._platform_mode()
        system_name = py_platform.system().lower()
        if mode == "linux_x11":
            self._backend = LinuxX11Backend()
        elif mode == "linux_wayland":
            self._backend = LinuxWaylandBackend()
        elif mode in {"windows", "windows_native"}:
            self._backend = WindowsBackend()
        elif system_name == "linux":
            display = self._display_server()
            self._backend = LinuxWaylandBackend() if display == "wayland" else LinuxX11Backend()
        elif system_name == "windows":
            self._backend = WindowsBackend()
        else:
            self._backend = UnsupportedBackend(system_name or "unknown")
        return self._backend

    def platform_snapshot(self) -> Dict[str, Any]:
        backend = self.backend()
        display_server = backend.display_server() or self._display_server()
        snapshot = HostPlatformSnapshot(
            host_platform=str(backend.platform_name or py_platform.system().lower() or "unknown"),
            host_backend=str(backend.backend_name or "unknown"),
            display_server=display_server,
            description=backend.description(),
        )
        return snapshot.to_dict()

    def command_available(self, name: str) -> bool:
        return _command_available(name)

    def list_windows(self) -> List[Dict[str, Any]]:
        return self.backend().list_windows()

    def match_windows(self, *queries: str) -> List[Dict[str, Any]]:
        return self.backend().match_windows(*queries)

    def focused_window_snapshot(self) -> Optional[Dict[str, Any]]:
        return self.backend().focused_window_snapshot()

    def open_target(self, target: str, *, kind: str = "auto") -> bool:
        return self.backend().open_target(str(target or ""), kind=kind)

    def reveal_path(self, path: str) -> bool:
        return self.backend().reveal_path(str(path or ""))

    def notify(self, *, title: str, body: str, urgency: str = "normal") -> bool:
        return self.backend().notify(title=title, body=body, urgency=urgency)

    def focus_window(self, window_id: str) -> bool:
        return self.backend().focus_window(str(window_id or ""))

    def type_text(self, text: str, *, delay_ms: int = 20) -> bool:
        return self.backend().type_text(str(text or ""), delay_ms=delay_ms)

    def send_keys(self, keys: str) -> bool:
        return self.backend().send_keys(str(keys or ""))

    def click(self, *, x: int, y: int, button: int = 1) -> bool:
        return self.backend().click(x=int(x), y=int(y), button=int(button))

    def capture_backend(
        self,
        *,
        module_available: Callable[[str], bool] = _module_available,
        binary_available: Callable[[str], bool] = _command_available,
    ) -> str:
        return self.backend().capture_backend(module_available=module_available, binary_available=binary_available)

    def capture_to_file(
        self,
        *,
        backend: str,
        output_path: Path,
        geometry: Optional[Dict[str, Any]],
    ) -> None:
        self.backend().capture_to_file(backend=backend, output_path=output_path, geometry=geometry)

    def machine_presence_state(self) -> str:
        idle_threshold = float(getattr(config, "LOCAL_MACHINE_PRESENCE_POLL_SECONDS", 10.0) or 10.0)
        return self.backend().detect_machine_state(idle_threshold_seconds=max(idle_threshold, 2.0))

    def capability_health(
        self,
        *,
        desktop_enabled: bool,
        input_enabled: bool,
        machine_presence_enabled: bool,
        capture_available: bool = False,
        ocr_available: bool = False,
    ) -> Dict[str, Any]:
        return self.backend().capability_health(
            desktop_enabled=desktop_enabled,
            input_enabled=input_enabled,
            machine_presence_enabled=machine_presence_enabled,
            capture_available=capture_available,
            ocr_available=ocr_available,
        )


host_platform_service = HostPlatformService()
