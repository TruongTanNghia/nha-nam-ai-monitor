@echo off
chcp 65001 >nul
cd /d "%~dp0"
rem Tat may chu + duong ham con chay ngam, bo tu chay cung Windows (ban cai cu)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\dung.ps1"
pause
