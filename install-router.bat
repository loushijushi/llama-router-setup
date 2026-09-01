@echo off
chcp 65001 >nul
title llama.cpp Router - Install Service
cd /d "%~dp0"

set "PYTHONIOENCODING=utf-8"

echo ============================================
echo   llama.cpp Router - Install as Service
echo ============================================
echo.

call "%~dp0find_python.cmd"
if errorlevel 1 goto :no_python

echo [Python] %_PY_EXE%
echo.

"%_PY_EXE%" "%~dp0startup_check.py"
if errorlevel 1 (
    echo.
    echo [Environment check] Issues found, aborting install.
    pause
    exit /b 1
)

"%_PY_EXE%" -m service install
if errorlevel 1 (
    echo.
    echo Install failed. See error above.
    pause
    exit /b 1
)
echo.
echo ============================================
echo   Install complete!
echo   URL    : http://127.0.0.1:8080
echo   Models : curl http://127.0.0.1:8080/v1/models
echo   Config : double-click manager-ui.bat
echo ============================================
echo.
pause
exit /b 0

:no_python
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0show_python_missing.ps1"
exit /b 1
