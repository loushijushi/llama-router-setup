@echo off
chcp 65001 >nul
title llama.cpp Router - Status
cd /d "%~dp0"

call "%~dp0find_python.cmd"
if errorlevel 1 goto :no_python

echo === Service Status ===
"%_PY_EXE%" -c "import service; print(service.status())"
echo.

echo === Watchdog Status ===
"%_PY_EXE%" -c "import service; print(service.watchdog_status())"
echo.

echo === Models ===
curl -s http://127.0.0.1:8080/v1/models
echo.
echo.

echo === Recent Log (20 lines) ===
"%_PY_EXE%" -c "import service, os; p=service.out_log(); print('(no log: '+p+')' if not os.path.isfile(p) else '\n'.join(open(p,encoding='utf-8',errors='replace').read().splitlines()[-20:]))" 2>nul
echo.
pause
exit /b 0

:no_python
echo [ERROR] Python not found.
pause
exit /b 1
