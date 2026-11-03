"""Let the HTTP acknowledgement finish, then restart through the normal launcher."""
import os
from pathlib import Path
import sys
import time

if __name__ == "__main__":
    time.sleep(1.5)
    launcher = Path(__file__).resolve().parent / "palli_ctl.py"
    os.execv(sys.executable, [sys.executable, str(launcher), "restart"])
