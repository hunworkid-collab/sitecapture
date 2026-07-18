@echo off
chcp 65001 > nul
set "PYTHON=python"
if exist ".venv\Scripts\python.exe" set "PYTHON=.venv\Scripts\python.exe"

set /p "KEYWORD=검색 키워드를 입력하세요: "
set /p "DOMAIN=검색 도메인을 입력하세요: "
if not defined KEYWORD goto :missing_input
if not defined DOMAIN goto :missing_input

"%PYTHON%" main.py --keyword "%KEYWORD%" --domain "%DOMAIN%" --delay 5 --verbose
goto :done

:missing_input
echo 키워드와 도메인을 모두 입력해야 합니다.
exit /b 2

:done
pause
