@echo off
chcp 65001 >nul
title HELLDIVERS 2 // TACTICAL COMMAND - Install
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1"
if errorlevel 1 (
  echo.
  echo Installation failed. See the messages above.
)
echo.
pause
