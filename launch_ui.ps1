# 启动 llama-router-setup UI (隐藏窗口, 不留黑色 cmd 窗口)
# 用法: powershell -NoProfile -ExecutionPolicy Bypass -File launch_ui.ps1 <python.exe>
param(
    [Parameter(Mandatory = $true)]
    [string]$PyExe
)

$ErrorActionPreference = 'Continue'
$root = $PSScriptRoot
$logDir = Join-Path $root 'logs'
if (-not (Test-Path $logDir)) {
    New-Item -ItemType Directory -Path $logDir | Out-Null
}
$log = Join-Path $logDir 'ui_launch.log'

# 用隐藏的 cmd 包一层做重定向: 无窗口 + stdout/stderr 合并到日志 + 退出码传递
# /S 让 cmd 可靠剥离最外层引号 (否则带引号的 /c 命令解析不稳定)
# UseShellExecute=$true 避免子进程继承本脚本的句柄 (否则调用方等管道关闭会挂起)
$psi = New-Object System.Diagnostics.ProcessStartInfo
$psi.FileName = $env:ComSpec
$psi.Arguments = "/s /c `"`"$PyExe`" -m ui > `"$log`" 2>&1`""
$psi.WorkingDirectory = $root
$psi.UseShellExecute = $true
$psi.WindowStyle = [System.Diagnostics.ProcessWindowStyle]::Hidden
$proc = [System.Diagnostics.Process]::Start($psi)

# 观察期: 大多数启动崩溃会在几秒内发生
Start-Sleep -Seconds 5

if ($proc.HasExited -and $proc.ExitCode -ne 0) {
    Write-Host ''
    Write-Host '=========================================='
    Write-Host "  UI failed to start! (exit $($proc.ExitCode))"
    Write-Host '  Full log: logs\ui_launch.log'
    Write-Host '=========================================='
    Write-Host ''
    if (Test-Path $log) {
        Get-Content $log | ForEach-Object { Write-Host $_ }
    }
    Write-Host ''
    try {
        Read-Host 'Press Enter to exit'
    } catch {
        Start-Sleep -Seconds 60
    }
    exit $proc.ExitCode
}
exit 0
