---
name: webcam_capture
when: User asks for a photo from the webcam, or wants you to "snap a pic", "take a webcam shot", "grab a frame from the camera"
needs: [python, opencv via cv2]
---

# Webcam capture (Windows + OpenCV)

There's a small bundled Python script at `scripts/webcam_snap.py` that
captures one frame from the default webcam (or a named device) and
writes a JPEG.

To capture from the default device:

```
local_shell:
python scripts/webcam_snap.py --out C:/Users/<you>/Desktop/claude/palli-local-refactor/data/operator-desktop/captures/webcam.jpg
```

To target a specific device by name substring (e.g. when there's both
a Logitech C922 and an OBS virtual camera plugged in, prefer the C922):

```
local_shell:
python scripts/webcam_snap.py --device "C922" --out C:/Users/<you>/Desktop/claude/palli-local-refactor/data/operator-desktop/captures/webcam.jpg
```

To send the produced file, call `deliver_artifact` with its path.

If the script reports no camera available, say so honestly. Don't
promise to "try a different source" unless the user names one.
