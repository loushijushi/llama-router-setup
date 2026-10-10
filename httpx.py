"""本机 HTTP 请求的统一 opener。

## 为什么不能直接用 `urllib.request.urlopen`

这台机器上跑着代理 (Karing), 系统代理配置**不保证**把 `127.0.0.1` 放进
`no_proxy`。实测: `urllib.request.urlopen('http://127.0.0.1:18080/health')`
**30/30 全超时 (每次 3 秒)**, 而 PowerShell 秒回 200; 同一段代码只要显式
禁掉代理 (`ProxyHandler({})`) 就立刻 200。

后果不是「慢一点」, 而是**整个 UI 误报「无法连接」** —— 监控窗口的健康标签、
状态卡片全变红, 而网关其实好好的、日志还在滚。

所以: **凡是连 127.0.0.1 / localhost / ::1 的地方都走这里**, 一次修好,
不用在每个调用点记得加参数。
"""
from __future__ import annotations

import urllib.request

# 只对「本机地址」禁代理 —— 远程监控 (192.168.x.x) 仍要按系统设置走代理,
# 因为那台机器可能确实需要代理才能访问。
_LOCAL = ("127.0.0.1", "localhost", "::1", "0.0.0.0")


def is_local(host: str) -> bool:
    h = (host or "").strip().lower().strip("[]")
    return h in _LOCAL or h.startswith("127.")


def open_url(url: str, timeout: float = 10.0, headers=None):
    """打开 url。本机地址**绕过系统代理**, 其余按系统设置。"""
    req = urllib.request.Request(url, headers=headers or {})
    host = req.host or ""
    if is_local(host):
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        return opener.open(req, timeout=timeout)
    return urllib.request.urlopen(req, timeout=timeout)