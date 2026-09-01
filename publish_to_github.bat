@echo off
chcp 65001 >nul
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"

set "PYTHONIOENCODING=utf-8"

echo ============================================
echo   llama.cpp Router - GitHub 发布工具
echo ============================================
echo.

REM ---- 1) 必需工具检查 ----
where git >nul 2>&1
if errorlevel 1 (
    echo [ERROR] git 未安装, 请先安装 Git for Windows:
    echo         https://git-scm.com/download/win
    pause
    exit /b 1
)

REM 检查是否已初始化
if not exist ".git" (
    echo [1/5] git init
    git init
    if errorlevel 1 (
        echo [ERROR] git init 失败
        pause
        exit /b 1
    )
) else (
    echo [1/5] git 已初始化, 跳过
)
echo.

REM ---- 2) 配置 user / email (如果未设置) ----
echo [2/5] 配置 git user
for /f "tokens=*" %%V in ('git config user.name 2^>nul') do set "GIT_NAME=%%V"
if not defined GIT_NAME (
    set /p "GIT_NAME=请输入 GitHub 用户名: "
    git config user.name "!GIT_NAME!"
)
for /f "tokens=*" %%V in ('git config user.email 2^>nul') do set "GIT_EMAIL=%%V"
if not defined GIT_EMAIL (
    set /p "GIT_EMAIL=请输入 GitHub 邮箱: "
    git config user.email "!GIT_EMAIL!"
)
echo       user:  !GIT_NAME!
echo       email: !GIT_EMAIL!
echo.

REM ---- 3) 检查 .gitignore 是否覆盖了敏感文件 ----
echo [3/5] 检查 .gitignore
if not exist ".gitignore" (
    echo [ERROR] 缺少 .gitignore 文件
    pause
    exit /b 1
)
findstr /C:"config.json" .gitignore >nul
if errorlevel 1 (
    echo [WARN] .gitignore 中没有 config.json, 建议加上避免泄露本地路径
)
findstr /C:"logs/" .gitignore >nul
if errorlevel 1 (
    echo [WARN] .gitignore 中没有 logs/, 建议加上避免把日志文件入库
)
echo.

REM ---- 4) 添加并提交 ----
echo [4/5] git add + commit
git add -A
git status --short
echo.
echo 上面是要提交的文件列表, 确认后继续.
pause
git commit -m "Initial commit: llama.cpp Router Manager v3"
if errorlevel 1 (
    echo [WARN] 没有变更需要提交, 或 commit 失败
) else (
    echo OK: initial commit done
)
echo.

REM ---- 5) 推送到 GitHub ----
echo [5/5] 推送到 GitHub
echo.
echo 请先去 https://github.com/new 创建一个空仓库 (不要勾选 Add README / .gitignore / License)
echo 然后把仓库地址 (HTTPS 或 SSH) 粘贴到下面:
echo.
set /p "REMOTE_URL=GitHub 仓库 URL (例如 https://github.com/yourname/llama-router-setup.git): "

if "!REMOTE_URL!"=="" (
    echo [WARN] 未提供 URL, 跳过推送. 之后可以手动:
    echo       git remote add origin !REMOTE_URL!
    echo       git push -u origin main
) else (
    git remote remove origin 2>nul
    git remote add origin "!REMOTE_URL!"
    git branch -M main
    echo 推送到 origin/main ...
    git push -u origin main
    if errorlevel 1 (
        echo [ERROR] 推送失败, 请检查:
        echo       1) 仓库地址是否正确
        echo       2) GitHub 账号是否有写入权限
        echo       3) 是否配置了 SSH key 或 Personal Access Token
        echo.
        echo Windows 推荐用 Personal Access Token (PAT) 方式:
        echo   1. 打开 https://github.com/settings/tokens
        echo   2. Generate new token (classic), 勾选 'repo'
        echo   3. 推送时把 token 放进 URL: https://TOKEN@github.com/yourname/repo.git
        pause
        exit /b 1
    )
    echo OK: pushed to !REMOTE_URL!
)
echo.

echo ============================================
echo   发布完成!
echo ============================================
echo.
echo 后续:
echo   - git status  查看变更
echo   - git add -A 暂存
echo   - git commit -m "message" 提交
echo   - git push 推送到 GitHub
echo.
pause
