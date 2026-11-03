"""Capture one frame from the webcam to a file.

Usage:
    python scripts/webcam_snap.py --out path.jpg
    python scripts/webcam_snap.py --device "C922" --out path.jpg
    python scripts/webcam_snap.py --index 0 --out path.jpg

Behavior:
- If --device <substring> is given, scans available capture devices and
  picks the first whose name contains the substring (case-insensitive).
- Else if --index N is given, opens that index directly.
- Else opens index 0 (the system default).

On Windows we use OpenCV's DirectShow backend so device-name discovery
works. On other platforms we fall back to the default backend, which
doesn't expose names — falls back to index-only selection.

Exits 0 on successful capture. Exits non-zero with a single-line stderr
message describing the failure (no usable device, capture failed, write
failed). The caller (PaLLi's local_shell) reads exit code + stderr to
decide whether to surface "couldn't capture" vs "captured at <path>".
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path


def _list_dshow_device_names() -> list[str]:
    """Best-effort enumeration of DirectShow capture device names on Windows.

    Returns an empty list on non-Windows or if enumeration fails. Used
    only for substring-match device selection; index selection works
    without it.
    """
    if sys.platform != "win32":
        return []
    try:
        import subprocess

        # ffmpeg's dshow input lists devices to stderr; we don't actually
        # capture, just list. ffmpeg may not be installed — that's fine,
        # we degrade to index-only.
        result = subprocess.run(
            ["ffmpeg", "-list_devices", "true", "-f", "dshow", "-i", "dummy"],
            capture_output=True,
            text=True,
            timeout=8,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, Exception):
        return []
    names: list[str] = []
    for line in (result.stderr or "").splitlines():
        # ffmpeg dshow lines look like:
        #   [dshow @ 000...] "Logitech HD Pro Webcam C922" (video)
        if '"' not in line or "(video)" not in line.lower():
            continue
        try:
            start = line.index('"') + 1
            end = line.index('"', start)
        except ValueError:
            continue
        name = line[start:end].strip()
        if name:
            names.append(name)
    return names


def _resolve_device_index(device_substring: str) -> int | None:
    if not device_substring:
        return None
    names = _list_dshow_device_names()
    needle = device_substring.lower().strip()
    for idx, name in enumerate(names):
        if needle in name.lower():
            return idx
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description="Capture one frame from a webcam.")
    parser.add_argument("--out", required=True, help="output image path (.jpg or .png)")
    parser.add_argument("--device", default="", help="device-name substring (case-insensitive)")
    parser.add_argument("--index", type=int, default=None, help="device index (default: 0)")
    parser.add_argument(
        "--warmup",
        type=int,
        default=3,
        help="discard N initial frames to let exposure stabilize (default 3)",
    )
    args = parser.parse_args()

    try:
        import cv2  # type: ignore[import-not-found]
    except ImportError:
        sys.stderr.write(
            "OpenCV (cv2) is not installed. Install it with: "
            "venv/Scripts/python.exe -m pip install opencv-python\n"
        )
        return 2

    # Resolve which device to open.
    device_index: int = 0
    if args.device:
        resolved = _resolve_device_index(args.device)
        if resolved is None:
            available = ", ".join(_list_dshow_device_names()) or "(none enumerated)"
            sys.stderr.write(
                f"No capture device matched substring {args.device!r}. "
                f"Available: {available}\n"
            )
            return 3
        device_index = resolved
    elif args.index is not None:
        device_index = int(args.index)

    # On Windows, prefer the DirectShow backend so multi-device systems
    # behave consistently. Fall back to the default backend elsewhere.
    backend = cv2.CAP_DSHOW if sys.platform == "win32" else cv2.CAP_ANY
    cap = cv2.VideoCapture(device_index, backend)
    if not cap.isOpened():
        sys.stderr.write(
            f"Failed to open capture device at index {device_index} "
            f"(backend={backend}). Check that the camera is connected "
            f"and not in use by another app.\n"
        )
        return 4
    try:
        for _ in range(max(0, int(args.warmup))):
            cap.read()
        ok, frame = cap.read()
        if not ok or frame is None:
            sys.stderr.write(
                "Capture device opened but returned no frame. "
                "Camera may be busy or unconfigured.\n"
            )
            return 5
        out_path = Path(args.out).expanduser().resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        if not cv2.imwrite(str(out_path), frame):
            sys.stderr.write(f"cv2.imwrite failed for {out_path}\n")
            return 6
        sys.stdout.write(f"captured: {out_path}\n")
        return 0
    finally:
        cap.release()


if __name__ == "__main__":
    sys.exit(main())
