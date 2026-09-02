@echo off
chcp 65001 >nul
cd /d "%~dp0"

set "PYTHONIOENCODING=utf-8"

REM 找 Python
call "%~dp0find_python.cmd"
if errorlevel 1 (
    echo [ERROR] 找不到 Python, 请先安装
    pause
    exit /b 1
)

echo [Python] %_PY_EXE%
echo.

REM 接受可选的 --version 参数
"%_PY_EXE%" "%~dp0build_release.py" %*
