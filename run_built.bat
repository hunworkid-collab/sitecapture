@echo off
setlocal

cd /d "%~dp0"

set "EXE=dist\SiteCapture\SiteCapture.exe"

if not exist "%EXE%" (
    echo 실행파일이 없습니다.
    echo 먼저 build_exe.bat을 실행하세요.
    pause
    exit /b 1
)

start "" "%EXE%"

endlocal
