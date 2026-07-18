@echo off
setlocal EnableExtensions

cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" (
    set "PYTHON=.venv\Scripts\python.exe"
) else (
    set "PYTHON=python"
)

echo.
echo ========================================
echo  Google actual capture one-job test
echo ========================================
echo.

set /p "KEYWORD=검색 키워드를 입력하세요: "
set /p "DOMAIN=검색 도메인을 입력하세요: "
if not defined KEYWORD goto :missing_input
if not defined DOMAIN goto :missing_input

"%PYTHON%" live_test.py ^
    --keyword "%KEYWORD%" ^
    --domain "%DOMAIN%"

set "RESULT=%ERRORLEVEL%"

echo.

if "%RESULT%"=="0" (
    echo Test passed.
) else (
    echo Test failed.
    echo Check the log above.
)

echo.
pause

exit /b %RESULT%

:missing_input
echo 키워드와 도메인을 모두 입력해야 합니다.
pause
exit /b 2
