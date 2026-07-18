@echo off
chcp 65001 > nul
python -m venv .venv
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
echo 설치가 완료되었습니다.
pause
