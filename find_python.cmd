@echo off
chcp 65001 >nul 2>&1
setlocal EnableExtensions

set "_PY_EXE="
set "_TMP=%TEMP%\__find_py_%RANDOM%.txt"

REM ==========================================
REM 优先在已知安装目录里找实际 Python 解释器
REM 避免拿到 MS Store 占位 python.exe 或 py.exe launcher (在 Python 3.14 上有 stdin 问题)
REM ==========================================
for %%P in (
    "%LOCALAPPDATA%\Programs\Python\Python314\python.exe"
    "%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
    "%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
    "%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
    "%LOCALAPPDATA%\Programs\Python\Python310\python.exe"
    "%LOCALAPPDATA%\Programs\Python\Python39\python.exe"
    "C:\Python314\python.exe"
    "C:\Python313\python.exe"
    "C:\Python312\python.exe"
    "C:\Python311\python.exe"
    "C:\Python310\python.exe"
    "C:\Python39\python.exe"
    "C:\Program Files\Python314\python.exe"
    "C:\Program Files\Python313\python.exe"
    "C:\Program Files\Python312\python.exe"
    "C:\Program Files\Python311\python.exe"
    "C:\Program Files\Python310\python.exe"
    "%USERPROFILE%\AppData\Local\Programs\Python\Python314\python.exe"
    "%USERPROFILE%\AppData\Local\Programs\Python\Python313\python.exe"
    "%USERPROFILE%\AppData\Local\Programs\Python\Python312\python.exe"
    "%USERPROFILE%\AppData\Local\Programs\Python\Python311\python.exe"
    "%USERPROFILE%\AppData\Local\Programs\Python\Python310\python.exe"
) do (
    if exist "%%~fP" (
        set "_PY_EXE=%%~fP"
        goto :done
    )
)

REM ==========================================
REM 备用: PATH 里的 python.exe / py.exe
REM 过滤掉 MS Store 占位 (在 "WindowsApps" 目录下)
REM ==========================================
where python.exe > "%_TMP%" 2>nul
if not errorlevel 1 (
    for /f "usebackq tokens=* delims=" %%P in ("%_TMP%") do (
        if not "%%P"=="" if not "%%P"=="INFO:" (
            echo %%P | findstr /I /C:"WindowsApps" >nul
            if errorlevel 1 (
                set "_PY_EXE=%%P"
                goto :done
            )
        )
    )
)

where py.exe > "%_TMP%" 2>nul
if not errorlevel 1 (
    for /f "usebackq tokens=* delims=" %%P in ("%_TMP%") do (
        if not "%%P"=="" if not "%%P"=="INFO:" (
            echo %%P | findstr /I /C:"WindowsApps" >nul
            if errorlevel 1 (
                set "_PY_EXE=%%P"
                goto :done
            )
        )
    )
)

del "%_TMP%" 2>nul
endlocal & exit /b 1

:done
"%_PY_EXE%" --version > "%_TMP%" 2>nul
del "%_TMP%" 2>nul
if errorlevel 1 (
    endlocal & exit /b 1
)
endlocal & set "_PY_EXE=%_PY_EXE%" & exit /b 0
