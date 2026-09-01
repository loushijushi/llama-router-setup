Set WshShell = CreateObject("WScript.Shell")
Set objArgs = WScript.Arguments
If objArgs.Count < 1 Then
    WScript.Quit 1
End If
pyExe = objArgs(0)
' 第 2 个参数 0 = 隐藏窗口 (隐藏启动的 Python 进程的 cmd 窗口)
' 第 3 个参数 False = 不等待 (脚本立即返回，不阻塞 bat)
WshShell.Run """" & pyExe & """ -m ui", 0, False
