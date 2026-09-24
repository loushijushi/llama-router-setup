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
REM 最小窗口启动; stderr 写入 logs\ui_launch.log; 崩溃时显示错误并暂停 (不闪退)
start "llama-router-ui" /min "%ComSpec%" /c call "%~dp0launch_ui_logged.cmd" "%_PY_EXE%"
exit /b 0

:no_python
echo ============================================
echo   [ERROR] Python not found!
echo ============================================
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0show_python_missing.ps1"
if errorlevel 1 (
    echo.
    echo [ERROR] Python 安装指引窗口打开失败，请手动安装 Python 3.8+:
    echo         https://www.python.org/downloads/
    echo         安装时勾选 "Add python.exe to PATH"
    pause
)
exit /b 1
