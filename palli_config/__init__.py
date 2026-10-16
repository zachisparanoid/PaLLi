"""Interactive PaLLi configuration TUI (Stage 1).

See docs/superpowers/specs/2026-05-14-palli-config-tui-design.md.
"""

from core.version import read_version

# One version for the whole tree (week 1 task 5). This said 0.1.0 while
# core/main.py said 2.0.0; VERSION at the repo root is now the only literal.
__version__ = read_version()
