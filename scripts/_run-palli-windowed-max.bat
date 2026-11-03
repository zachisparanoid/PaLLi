@echo off
title PaLLi Backend (--more-everything)
set PYTHONUNBUFFERED=1
cd /d "%~dp0.."
rem Convenience launcher: cranks BOTH perception and chattiness for this run.
rem Hand-edit .env back to baseline if you want this to feel calmer next boot.
"%~dp0..\venv\Scripts\python.exe" main.py --more-everything %*
echo.
echo --- PaLLi exited with code %ERRORLEVEL% ---
pause
