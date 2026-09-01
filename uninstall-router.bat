@echo off
chcp 65001 >nul
title llama.cpp Router - Uninstall Service
cd /d "%~dp0"

call "%~dp0find_python.cmd"
if errorlevel 1 goto :no_python

echo [Python] %_PY_EXE%
"%_PY_EXE%" -m service uninstall
if errorlevel 1 (
    echo.
    echo Uninstall failed. See error above.
    pause
    exit /b 1
)
echo.
echo Service removed. You can still run router-run.bat for foreground mode.
pause
exit /b 0

:no_python
echo [ERROR] Python not found.
pause
exit /b 1
