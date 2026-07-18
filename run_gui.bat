@echo off
setlocal
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" gui_main.py
) else (
  python gui_main.py
)
endlocal
