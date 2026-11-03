#!/usr/bin/env python3
"""Thin entry point for the interactive PaLLi configuration TUI.

Usage:
    python scripts/palli_configure.py             # menu skin (default)
    python scripts/palli_configure.py --wizard    # wizard skin
    python scripts/palli_configure.py --dashboard # dashboard skin
"""
import sys
from pathlib import Path

# Ensure repo root is importable
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from palli_config.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
