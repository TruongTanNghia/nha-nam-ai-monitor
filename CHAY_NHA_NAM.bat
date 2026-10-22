@echo off
chcp 65001 >nul
title Nha Nam AI - DONG CUA SO NAY DE TAT
cd /d "%~dp0"
rem 1 lan bam: tu cai (lan dau) + chay web + may chu AI + link tu xa Cloudflare. Dong cua so = tat het.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\chay.ps1" %*
echo.
echo May chu da dung. Bam phim bat ky de dong cua so.
pause >nul
