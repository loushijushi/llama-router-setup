@echo off
chcp 65001 >nul 2>&1
title llama.cpp Router UI
cd /d "%~dp0"
set "PYTHONIOENCODING=utf-8"

set "_PY=%~1"
if "%_PY%"=="" set "_PY=python"

if not exist "%~dp0logs" mkdir "%~dp0logs"
set "_LOG=%~dp0logs\ui_launch.log"

echo Starting UI... (log: logs\ui_launch.log)
"%_PY%" -m ui > "%_LOG%" 2>&1
set "_ERR=%ERRORLEVEL%"

if not "%_ERR%"=="0" (
    echo.
    echo ==================================================
    echo   UI failed to start! ^(exit code %_ERR%^)
    echo   Full log: logs\ui_launch.log
    echo ==================================================
    echo.
    type "%_LOG%"
    echo.
    pause
    exit /b %_ERR%
)
exit /b 0
