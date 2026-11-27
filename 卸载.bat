@echo off
chcp 65001 >nul
title HELLDIVERS 2 // TACTICAL COMMAND - Uninstall
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0uninstall.ps1"
echo.
pause
