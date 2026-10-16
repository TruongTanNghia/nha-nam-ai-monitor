@echo off
chcp 65001 >nul
cd /d "%~dp0"
rem Tat may chu + duong ham. Them -GoTuChay de bo che do tu chay cung Windows
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\dung.ps1" %*
pause
