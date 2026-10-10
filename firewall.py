"""Windows 防火墙: 放行日志转发 (log_relay) 监听端口。

背景: 远程那台机器不开入站规则时, Windows 是**静默丢包**而不是回 RST,
所以监控窗口只会看到「超时」, 看起来像"没连上/服务没起", 极难定位。
这里给出一键放行 + 状态查询, 并在失败时回退成一条可手动执行的 netsh 命令。

约定和 service.py 一致: 每个动作返回 (ok, msg)。
"""
import base64
import ctypes
import subprocess
from typing import Tuple

RULE_PREFIX = "llama-router-setup log-relay"
_PS = "powershell"


def rule_name(port) -> str:
    """规则显示名 (端口写进名字, 换端口不会拿到旧规则)。"""
    return f"{RULE_PREFIX} {int(port)}"


def add_cmd(port) -> str:
    """给用户复制/手动执行的 netsh 命令 (UAC 被取消时的兜底)。"""
    return (f'netsh advfirewall firewall add rule name="{rule_name(port)}" '
            f"dir=in action=allow protocol=TCP localport={int(port)}")


def _is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def _b64(script: str) -> str:
    """-EncodedCommand 用的 base64 (UTF-16LE), 彻底绕开引号/转义问题。"""
    return base64.b64encode(script.encode("utf-16-le")).decode("ascii")


def _run_ps(script: str, timeout: float = 60.0) -> Tuple[int, str]:
    """本进程直接跑一段 PowerShell (需要已提权)。"""
    try:
        r = subprocess.run(
            [_PS, "-NoProfile", "-EncodedCommand", _b64(script)],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=timeout)
    except Exception as e:
        return 1, str(e)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def _run_ps_elevated(script: str, timeout: float = 180.0) -> Tuple[int, str]:
    """弹 UAC 提权执行 (Start-Process -Verb RunAs)。

    用户在 UAC 上点「否」时 PowerShell 会报错返回非 0 —— 交给调用方用
    exists() 复核, 再决定是不是退回手动命令。
    """
    outer = (
        "Start-Process powershell -Verb RunAs -Wait -WindowStyle Hidden "
        f"-ArgumentList '-NoProfile','-EncodedCommand','{_b64(script)}'"
    )
    return _run_ps(outer, timeout=timeout)


def exists(port) -> bool:
    """本机是否已有放行该端口的入站规则 (netsh 查询, 不需要提权)。"""
    name = rule_name(port).lower()
    try:
        r = subprocess.run(
            ["netsh", "advfirewall", "firewall", "show", "rule",
             "name=" + rule_name(port)],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=30)
    except Exception:
        return False
    if r.returncode != 0:
        return False
    # rc=0 之外再确认输出里确实带规则名, 避免语言差异误判
    return name in ((r.stdout or "") + (r.stderr or "")).lower()


def allow_inbound(port) -> Tuple[bool, str]:
    """放行 TCP port 入站。幂等: 已经有规则就直接成功。"""
    try:
        port = int(port)
    except (TypeError, ValueError):
        return False, f"端口号不合法: {port!r}"
    if not (1 <= port <= 65535):
        return False, f"端口号范围 1-65535: {port}"
    if exists(port):
        return True, f"端口 {port} 已放行 (规则已存在)"

    script = (
        f'New-NetFirewallRule -DisplayName "{rule_name(port)}" '
        f"-Direction Inbound -Action Allow -Protocol TCP "
        f"-LocalPort {port} -Profile Any | Out-Null"
    )
    if _is_admin():
        _run_ps(script)
    else:
        _run_ps_elevated(script)

    if exists(port):
        return True, f"端口 {port} 已放行 (入站 TCP {port})"
    return False, (f"放行端口 {port} 未生效 (可能取消了 UAC 或没有权限)。\n"
                   f"请用管理员身份执行:\n{add_cmd(port)}")


def remove_inbound(port) -> Tuple[bool, str]:
    """撤销放行。本来就没规则也算成功。"""
    try:
        port = int(port)
    except (TypeError, ValueError):
        return False, f"端口号不合法: {port!r}"
    if not (1 <= port <= 65535):
        return False, f"端口号范围 1-65535: {port}"
    if not exists(port):
        return True, f"端口 {port} 本来就没放行"
    script = (f'Remove-NetFirewallRule -DisplayName "{rule_name(port)}" '
              f"-ErrorAction SilentlyContinue")
    if _is_admin():
        _run_ps(script)
    else:
        _run_ps_elevated(script)
    if not exists(port):
        return True, f"已撤销端口 {port} 的放行"
    return False, (f"撤销端口 {port} 未生效。请用管理员身份执行:\n"
                   f'netsh advfirewall firewall delete rule name="{rule_name(port)}"')


def status_text(port) -> str:
    """给界面用的一句话状态。"""
    try:
        port = int(port)
    except (TypeError, ValueError):
        return "端口不合法"
    try:
        return f"端口 {port}: 已放行" if exists(port) else f"端口 {port}: 未放行"
    except Exception as e:
        return f"端口 {port}: 查询失败 ({e})"


def main(argv) -> int:
    """命令行: firewall.py allow|remove|status <port>"""
    if len(argv) < 3:
        print(__doc__ or "")
        print("用法: py firewall.py allow|remove|status <port>")
        return 2
    action, port = argv[1], argv[2]
    if action == "allow":
        ok, msg = allow_inbound(port)
    elif action == "remove":
        ok, msg = remove_inbound(port)
    else:
        print(status_text(port))
        return 0
    print(msg)
    return 0 if ok else 1


if __name__ == "__main__":
    import sys
    raise SystemExit(main(sys.argv))
