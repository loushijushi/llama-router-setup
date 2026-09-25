# Show-Python-Missing.ps1
# 当 manager-ui.bat 探测不到 Python 时被调用
# 弹出一个清晰的中文安装指引窗口

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

$form = New-Object System.Windows.Forms.Form
$form.Text = "llama.cpp Router - 需要安装 Python"
$form.Size = New-Object System.Drawing.Size(640, 380)
$form.StartPosition = "CenterScreen"
$form.FormBorderStyle = "FixedDialog"
$form.MaximizeBox = $false

$lbl = New-Object System.Windows.Forms.Label
$lbl.Location = New-Object System.Drawing.Point(20, 16)
$lbl.Size = New-Object System.Drawing.Size(580, 24)
$lbl.Font = New-Object System.Drawing.Font("Microsoft YaHei", 11, [System.Drawing.FontStyle]::Bold)
$lbl.Text = "未检测到 Python，请先安装后再运行本程序"
$form.Controls.Add($lbl)

$txt = New-Object System.Windows.Forms.TextBox
$txt.Location = New-Object System.Drawing.Point(20, 50)
$txt.Size = New-Object System.Drawing.Size(580, 200)
$txt.Multiline = $true
$txt.ReadOnly = $true
$txt.ScrollBars = "Vertical"
$txt.Font = New-Object System.Drawing.Font("Microsoft YaHei", 10)
$txt.Text = @"
推荐方式 1：从 python.org 下载安装 (适合大多数人)
  1. 打开浏览器访问 https://www.python.org/downloads/
  2. 点击 "Download Python 3.12.x" (任一 3.8 以上版本均可)
  3. 双击下载的 python-3.12.x-amd64.exe 运行
  4. ⚠ 关键步骤：在第一个安装界面最下方勾选
        ☑ Add python.exe to PATH
     然后点 "Install Now"
  5. 等待安装完成 (约 1-2 分钟)
  6. 重新双击 manager-ui.bat

推荐方式 2：用 winget 命令安装 (适合会敲命令的人)
  1. 按 Win 键，输入 cmd，右键 "以管理员身份运行"
  2. 执行:  winget install Python.Python.3.12
  3. 重启一次资源管理器 (或注销重登) 让 PATH 生效
  4. 重新双击 manager-ui.bat

推荐方式 3：用 Chocolatey 安装 (适合开发者)
  1. 以管理员身份运行 cmd
  2. 执行:  choco install python -y
  3. 重新双击 manager-ui.bat

如果以上都不方便：
  请联系项目作者索要一个 PyInstaller 打包好的 exe 版本
  (不需要安装 Python，但体积大一些，约 15 MB)
"@
$form.Controls.Add($txt)

$btnDownload = New-Object System.Windows.Forms.Button
$btnDownload.Location = New-Object System.Drawing.Point(20, 270)
$btnDownload.Size = New-Object System.Drawing.Size(180, 36)
$btnDownload.Text = "打开 python.org 下载页"
$btnDownload.Font = New-Object System.Drawing.Font("Microsoft YaHei", 10)
$btnDownload.Add_Click({
    Start-Process "https://www.python.org/downloads/"
})
$form.Controls.Add($btnDownload)

$btnWinget = New-Object System.Windows.Forms.Button
$btnWinget.Location = New-Object System.Drawing.Point(210, 270)
$btnWinget.Size = New-Object System.Drawing.Size(180, 36)
$btnWinget.Text = "用 winget 安装 (管理员)"
$btnWinget.Font = New-Object System.Drawing.Font("Microsoft YaHei", 10)
$btnWinget.Add_Click({
    try {
        # 用 -EncodedCommand 规避多层引号嵌套问题
        $inner = 'winget install Python.Python.3.12 -y; Write-Host "安装完成！关闭本窗口后请重新双击 manager-ui.bat" -ForegroundColor Green; pause'
        $enc = [Convert]::ToBase64String([System.Text.Encoding]::Unicode.GetBytes($inner))
        Start-Process -FilePath "powershell" -ArgumentList "-NoProfile", "-EncodedCommand", $enc -Verb RunAs
    } catch {
        [System.Windows.Forms.MessageBox]::Show("无法启动安装: $_`n请手动以管理员身份运行:`nwinget install Python.Python.3.12", "提示", "OK", "Information")
    }
})
$form.Controls.Add($btnWinget)

$btnClose = New-Object System.Windows.Forms.Button
$btnClose.Location = New-Object System.Drawing.Point(490, 270)
$btnClose.Size = New-Object System.Drawing.Size(120, 36)
$btnClose.Text = "关闭"
$btnClose.Add_Click({ $form.Close() })
$form.Controls.Add($btnClose)

[void]$form.ShowDialog()
