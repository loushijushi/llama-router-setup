@echo off
chcp 65001 >nul
title llama.cpp Router - Foreground
cd /d "%~dp0"

echo ============================================
echo   llama.cpp Router - Foreground mode (testing)
echo   Close this window to stop
echo ============================================
echo.

call "%~dp0find_python.cmd"
if errorlevel 1 goto :no_python

echo [Python] %_PY_EXE%
"%_PY_EXE%" -c "import sys, os; sys.path.insert(0, '.'); from config import load; from generate_preset import generate; from service import run_foreground; cfg=load(); generate(cfg); print('preset.ini regenerated'); ok,msg=run_foreground(cfg); print(msg); sys.exit(0 if ok else 1)"

echo.
echo Service exited.
pause
exit /b 0

:no_python
echo [ERROR] Python not found.
pause
exit /b 1
