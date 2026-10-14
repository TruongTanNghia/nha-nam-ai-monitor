@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist server\.env copy server\.env.example server\.env >nul
echo Dang cai thu vien (lan dau hoi lau)...
python -m pip install -q -r server\requirements.txt
echo.
echo Mo trinh duyet: http://localhost:5000
start "" http://localhost:5000
python server\app.py
pause
