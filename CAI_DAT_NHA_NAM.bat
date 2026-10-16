@echo off
chcp 65001 >nul
cd /d "%~dp0"
rem Cai dat 1 lan tren may khach: Python, thu vien, Cloudflare Tunnel, cau hinh, tu chay cung Windows
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\cai_dat.ps1" %*
echo.
pause
