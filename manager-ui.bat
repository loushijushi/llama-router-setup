@echo off
chcp 65001 >nul
title llama.cpp Router - UI Manager
cd /d "%~dp0"

REM 强制 Python 用 UTF-8 输出 (避免中文在 cmd 乱码)
set "PYTHONIOENCODING=utf-8"

echo ============================================
echo   llama.cpp Router UI Manager
echo ============================================
echo.

REM ---- Find Python ----
call "%~dp0find_python.cmd"
if errorlevel 1 goto :no_python

echo [Python] [%_PY_EXE%]
echo.

REM ---- Environment check ----
"%_PY_EXE%" "%~dp0startup_check.py"
if errorlevel 1 (
    echo.
    echo [Environment check] 发现问题，UI 启动后请到「环境」页一键修复。
    echo                 等待 5 秒启动 UI... (Press Ctrl+C to cancel)
    timeout /t 5 /nobreak >nul
)

REM ---- Start UI ----
echo Starting UI...
REM 用 VBScript 静默启动 Python UI (不弹出 cmd 窗口)
cscript //NoLogo "%~dp0launch_ui_hidden.vbs" "%_PY_EXE%"
exit /b 0

:no_python
echo ============================================
echo   [ERROR] Python not found!
echo ============================================
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0show_python_missing.ps1"
exit /b 1
