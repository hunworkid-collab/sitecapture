@echo off
chcp 65001 > nul
setlocal EnableExtensions

cd /d "%~dp0"

set "PYTHON=.venv\Scripts\python.exe"
set "APP_NAME=SiteCapture"

echo.
echo ========================================
echo  SiteCapture build
echo ========================================
echo.

if not exist "%PYTHON%" (
    echo 가상환경을 생성합니다.
    python -m venv .venv
    if errorlevel 1 py -3 -m venv .venv
    if errorlevel 1 goto :error
)

echo 필요한 패키지를 설치합니다.
"%PYTHON%" -m pip install --upgrade pip
if errorlevel 1 goto :error
"%PYTHON%" -m pip install -r requirements.txt
if errorlevel 1 goto :error

echo.
echo 기존 빌드 폴더를 삭제합니다.
if exist "build" rmdir /s /q "build"
if exist "dist\%APP_NAME%" rmdir /s /q "dist\%APP_NAME%"
if exist "%APP_NAME%.spec" del /q "%APP_NAME%.spec"

echo.
echo 실행파일을 생성합니다.
"%PYTHON%" -m PyInstaller ^
    --noconfirm ^
    --clean ^
    --onedir ^
    --windowed ^
    --name "%APP_NAME%" ^
    gui_main.py
if errorlevel 1 goto :error

echo.
echo ========================================
echo  Build complete
echo ========================================
echo.
echo 실행파일:
echo %CD%\dist\%APP_NAME%\%APP_NAME%.exe
echo.
explorer "%CD%\dist\%APP_NAME%"
pause
exit /b 0

:error
echo.
echo ========================================
echo  Build failed
echo ========================================
echo.
echo 위 오류 내용을 확인하세요.
echo.
pause
exit /b 1
