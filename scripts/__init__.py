"""`scripts` is a package so `palli_ctl = "scripts.palli_ctl:main"` resolves.

Empty on purpose (week 1 task 5). Every module here is still a standalone
script run as `venv/bin/python scripts/<name>.py`; this file adds an import
path, not a runtime.
"""
