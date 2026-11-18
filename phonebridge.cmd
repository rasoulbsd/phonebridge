@echo off
rem Double-click to open Phonebridge. Runs without a console window; logs go to
rem %LOCALAPPDATA%\Phonebridge\phonebridge.log. If it's already running, this just opens the window.
cd /d "%~dp0"
start "" ".venv\Scripts\pythonw.exe" -m phonebridge %*
