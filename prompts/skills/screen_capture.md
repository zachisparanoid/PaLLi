---
name: screen_capture
when: User asks for a screenshot of their desktop, "take a screen", "what's on my screen"
needs: [powershell]
---

# Desktop screen capture

Prefer the dedicated `screenshot` tier-1 tool — it uses the configured
backend (Pillow / mss / pyautogui) and writes the image to the
operator captures dir, then signals delivery:

```
screenshot
```

If the dedicated tool reports an unavailable backend, fall back to
shell using `ffmpeg` if installed:

```
local_shell:
ffmpeg -f gdigrab -framerate 1 -i desktop -frames:v 1 -y C:/Users/<you>/Desktop/claude/palli-local-refactor/data/operator-desktop/captures/screen.png
```

To send the produced file, call `deliver_artifact` with its path.

If neither path works, say so honestly — don't promise a third
option that doesn't exist.
