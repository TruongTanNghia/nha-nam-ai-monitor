@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist server\.env copy server\.env.example server\.env >nul

rem Khong mo ra Internet khi chua co mat khau
python -c "import sys; sys.path.insert(0, 'server'); import config; sys.exit(0 if config.APP_PASSWORD else 1)"
if errorlevel 1 (
  echo [!] Chua dat APP_PASSWORD trong server\.env - dat mat khau truoc khi dua web ra Internet.
  pause
  exit /b 1
)

set "CF=cloudflared"
where cloudflared >nul 2>nul || set "CF=%ProgramFiles(x86)%\cloudflared\cloudflared.exe"

start "May chu AI Nha Nam" cmd /k python server\app.py
timeout /t 6 >nul

echo.
echo Link cong khai la dong co dang https://....trycloudflare.com ben duoi.
echo Dang nhap: tai khoan APP_USER / mat khau APP_PASSWORD trong server\.env
echo Link se doi moi lan chay lai file nay. Dong cua so nay = tat duong ham.
echo.
"%CF%" tunnel --no-autoupdate --url http://localhost:5000
