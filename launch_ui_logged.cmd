@echo off
chcp 65001 >nul 2>&1
title llama.cpp Router UI
cd /d "%~dp0"
set "PYTHONIOENCODING=utf-8"

set "_PY=%~1"
if "%_PY%"=="" set "_PY=python"

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0launch_ui.ps1" "%_PY%"
exit /b %ERRORLEVEL%
