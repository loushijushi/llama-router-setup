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
    echo [1/6] git init
    git init
    if errorlevel 1 (
        echo [ERROR] git init 失败
        pause
        exit /b 1
    )
) else (
    echo [1/6] git 已初始化, 跳过
)
echo.

REM ---- 2) 配置 user / email (如果未设置) ----
echo [2/6] 配置 git user
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
echo [3/6] 检查 .gitignore
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
echo [4/6] git add + commit
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

REM ---- 5) 配置 remote URL ----
echo [5/6] 配置远程仓库
echo.
echo 请先去 https://github.com/new 创建一个空仓库:
echo   - Repository name: llama-router-setup
echo   - Visibility: Public
echo   - Start with a template: No template
echo   - Add README / .gitignore / License: 全部 Off
echo 然后复制仓库 URL 粘贴到下面 (HTTPS 或 SSH 都行).
echo.
set /p "REMOTE_URL=GitHub 仓库 URL (例如 https://github.com/yourname/llama-router-setup.git): "

if "!REMOTE_URL!"=="" (
    echo [ERROR] 未提供 URL, 退出
    pause
    exit /b 1
)

git remote remove origin 2>nul
git remote add origin "!REMOTE_URL!"
git branch -M main
echo       remote: !REMOTE_URL!
echo.

REM ---- 6) 推送到 GitHub (处理空仓库 / 冲突) ----
echo [6/6] 推送到 GitHub
echo.
echo 正在推送 ... (首次推送可能要求输入 GitHub 用户名 + Personal Access Token)
echo.

REM 先尝试普通推送
git push -u origin main
set "PUSH_ERR=!errorlevel!"

if not "!PUSH_ERR!"=="0" goto :push_failed
goto :push_ok

:push_failed
echo.
echo ============================================
echo   首次推送失败, 尝试自动修复 ...
echo ============================================
echo.

REM 检查本地和远端 main 分支是否有共同祖先
echo 步骤 1/3: 尝试拉取远端信息
git fetch origin
if errorlevel 1 (
    echo [ERROR] 无法连接 GitHub, 请检查网络或仓库地址
    pause
    exit /b 1
)

git branch -r
echo.

REM 情况 1: 远端有 README 等初始文件 (用户勾了模板/勾了 Add README)
REM 解决: 拉取下来 merge, 或者用 ours 策略保留本地
echo 步骤 2/3: 检测远端是否有冲突文件 (README/.gitignore/LICENSE)
git ls-remote origin main >nul 2>&1
if not errorlevel 1 (
    for /f "tokens=*" %%F in ('git ls-tree --name-only origin/main 2^>nul') do (
        set "REMOTE_FILE=%%F"
        if /i not "!REMOTE_FILE!"=="" (
            if exist "!REMOTE_FILE!" (
                echo       远端有 !REMOTE_FILE! 但本地也有, 准备用本地版本覆盖远端
            )
        )
    )
)

REM 情况 2: 远端是空仓库但有 README 占位
REM 解决: 强制推送 (--force) 覆盖
echo.
echo 步骤 3/3: 强制推送到空仓库 (覆盖 GitHub 默认初始化的 README)
echo.
set /p "FORCE_CONFIRM=即将执行 git push --force-with-lease, 确认吗? (Y/N): "
if /i "!FORCE_CONFIRM!"=="Y" (
    git push --force-with-lease -u origin main
    if not errorlevel 1 (
        echo.
        echo ============================================
        echo   强制推送成功!
        echo ============================================
        goto :push_ok
    )
    echo.
    echo --force-with-lease 失败, 尝试 --force (无保护)
    git push --force -u origin main
    if not errorlevel 1 (
        echo.
        echo ============================================
        echo   强制推送成功!
        echo ============================================
        goto :push_ok
    ) else (
        echo.
        echo ============================================
        echo   推送仍然失败
        echo ============================================
        echo.
        echo 可能原因和解决办法:
        echo   1) GitHub 认证失败 - 需要 Personal Access Token (PAT)
        echo      ^- 打开 https://github.com/settings/tokens
        echo      ^- Generate new token (classic), 勾选 'repo'
        echo      ^- 推送时把 token 嵌进 URL:
        echo         https://YOUR_TOKEN@github.com/你的用户名/llama-router-setup.git
        echo.
        echo   2) 仓库地址拼写错误
        echo.
        echo   3) 远端不是空仓库 (有 README 等) 且没有 force 权限
        echo      ^- 把 URL 改成带 force 标记的:
        echo         git push --force https://YOUR_TOKEN@github.com/你的用户名/llama-router-setup.git main:main
        pause
        exit /b 1
    )
) else (
    echo 已取消. 如果远端有 README 等, 手动解决:
    echo   git pull origin main --allow-unrelated-histories
    echo   git push -u origin main
    pause
    exit /b 1
)

:push_ok
echo.
echo ============================================
echo   发布完成!
echo ============================================
echo.
echo 仓库地址: !REMOTE_URL!
echo.
echo 后续:
echo   - git status       查看未提交变更
echo   - git add -A       暂存所有变更
echo   - git commit -m "msg"   提交
echo   - git push         推送到 GitHub
echo.
echo 查看: 打开 !REMOTE_URL! 应该能看到所有文件
echo.
pause
