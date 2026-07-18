@echo off
chcp 65001 > nul
setlocal EnableExtensions

cd /d "%~dp0"

echo.
echo ========================================
echo  Project final check
echo ========================================
echo.

if exist ".venv\Scripts\python.exe" (
    set "PYTHON=.venv\Scripts\python.exe"
) else (
    set "PYTHON=python"
)

"%PYTHON%" check_project.py
set "RESULT=%ERRORLEVEL%"

echo.
if "%RESULT%"=="0" (
    echo All checks passed.
    echo Run build_exe.bat next.
) else (
    echo Checks failed.
    echo Fix the FAIL message above first.
)

echo.
pause
exit /b %RESULT%
