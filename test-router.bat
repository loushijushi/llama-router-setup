@echo off
chcp 65001 >nul
title llama.cpp Router - Test Models
cd /d "%~dp0"

echo ============================================
echo   Model Switching Test
echo ============================================
echo.

call "%~dp0find_python.cmd"
if errorlevel 1 goto :no_python

echo [Python] %_PY_EXE%

echo [1] Models list:
curl -s http://127.0.0.1:8080/v1/models
echo.
echo.

for /f "delims=" %%A in ('"%_PY_EXE%" -c "import config; print(' '.join(m.get('alias') or m.get('id','') for m in config.load()['models'] if m.get('enabled',True)))"') do set "ALIASES=%%A"

if "%ALIASES%"=="" (
    echo [ERROR] No enabled models. Configure them first.
    pause
    exit /b 1
)

echo [2] Calling each model (first call may take 30-60s):
for %%A in (%ALIASES%) do (
    echo.
    echo ----- Model: %%A -----
    curl -s http://127.0.0.1:8080/v1/chat/completions ^
      -H "Content-Type: application/json" ^
      -d "{\"model\":\"%%A\",\"messages\":[{\"role\":\"user\",\"content\":\"用一句话介绍你自己\"}]}"
    echo.
)

echo.
echo Done.
pause
exit /b 0

:no_python
echo [ERROR] Python not found.
pause
exit /b 1
