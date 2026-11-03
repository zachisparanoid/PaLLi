@echo off
title PaLLi Backend
set PYTHONUNBUFFERED=1
cd /d "%~dp0.."
rem Pass through any CLI flags (e.g. --more-perception, --more-chatty, --more-everything)
"%~dp0..\venv\Scripts\python.exe" main.py %*
echo.
echo --- PaLLi exited with code %ERRORLEVEL% ---
pause
