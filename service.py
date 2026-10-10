"""封装 NSSM 服务管理操作。

所有方法都不抛异常给 UI，返回 (ok, message) 元组，UI 只需展示。
"""
import json
import os
import shutil
import subprocess
import sys
from typing import List, Tuple

import config

SERVICE_NAME = "llama-router"
WATCHDOG_SERVICE_NAME = "llama-router-watchdog"
# 旧版本给 KVMem 分支单独注册的服务名, 已与主池合并, 只在迁移时用来清掉
LEGACY_KVMEM_SERVICE = "llama-kvmem"


def bundled_nssm() -> str:
    """项目自带 nssm.exe 路径。"""
    return os.path.join(config.app_dir(), "tools", "nssm.exe")


def nssm_path() -> str:
    """返回要使用的 nssm 可执行文件：优先内置，否则 PATH 里的。"""
    p = bundled_nssm()
    if os.path.isfile(p):
        return p
    w = shutil.which("nssm")
    return w if w else p  # 不存在时返回内置路径，让 nssm 自己报错


def llama_dir() -> str:
    """从 config 读取 llama_dir，自动 strip 末尾分隔符。"""
    return config.load().get("llama_dir", r"C:\llama.cpp").rstrip("\\/")


def preset_path() -> str:
    return os.path.join(llama_dir(), "router-preset.ini")


def log_dir() -> str:
    """日志目录：项目自带的 logs/ (不依赖 llama_dir 路径，便于用户查找)。"""
    p = os.path.join(config.app_dir(), "logs")
    os.makedirs(p, exist_ok=True)
    return p


def out_log() -> str:
    return os.path.join(log_dir(), "router.out.log")


def err_log() -> str:
    return os.path.join(log_dir(), "router.err.log")


def gateway_script() -> str:
    """网关进程脚本 (服务 llama-router 实际运行的程序)。"""
    return os.path.join(config.app_dir(), "gateway.py")


def gateway_out_log() -> str:
    """网关进程自己的 stdout (后端进程的日志仍写 router.*.log)。"""
    return os.path.join(log_dir(), "gateway.out.log")


def gateway_err_log() -> str:
    return os.path.join(log_dir(), "gateway.err.log")


def _is_admin() -> bool:
    """Windows 上检测是否以管理员权限运行。"""
    try:
        import ctypes
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def need_admin_hint() -> str:
    return "此操作需要管理员权限，请以管理员身份运行本程序（或对应的 .bat）。"


def which(cmd: str) -> bool:
    return shutil.which(cmd) is not None


def ensure_nssm() -> Tuple[bool, str]:
    """确保 nssm 可用。

    探测顺序：
      1. 项目内置 tools/nssm.exe (60KB，随项目一起分发)
      2. PATH 中的 nssm.exe
      3. choco install nssm (如果有 choco)
    """
    bundled = bundled_nssm()
    if os.path.isfile(bundled):
        return True, f"NSSM (内置): {bundled}"
    if which("nssm"):
        return True, "NSSM (PATH)"
    if which("choco"):
        r = subprocess.run(
            ["choco", "install", "nssm", "-y"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        if r.returncode != 0:
            return False, f"choco 安装 NSSM 失败:\n{r.stderr or r.stdout}"
        if which("nssm"):
            return True, "NSSM 已通过 choco 安装"
        return False, "安装后仍找不到 nssm，请新开一个终端再试。"
    return False, (
        f"未找到 NSSM，且项目目录下的 tools/nssm.exe 也缺失 ({bundled})。\n"
        "请重新下载完整项目（包含 tools/ 子目录），或安装 NSSM：\n"
        "  1) 用 winget:  winget install NSSM.NSSM\n"
        "  2) 用 choco:   choco install nssm -y\n"
        "  3) 手动: 从 https://nssm.cc 下载 nssm.exe 放到 tools/ 下"
    )


def _kill_old_server() -> None:
    """停掉所有可能占着 host:port 的服务进程 (主池 / KVMem 分支)。

    外加**孤儿网关**。只靠 `nssm stop` 是不够的: 它停了服务, 但如果旧网关
    进程没跟着退干净 (正在处理请求 / atexit 卡住), 那个进程会继续占着对外
    端口。Windows 上两个进程可以同时 bind 同一个口 (`SO_REUSEADDR`), 于是
    请求被随机丢给那个没人管的孤儿 —— 表现是「浏览器时好时坏 / 连不上」。
    这里按 PID + 命令行精确认领, 绝不按镜像名杀 python.exe。
    """
    for img in ("llama-server.exe", "llama-kvmem-server.exe"):
        subprocess.run(["taskkill", "/F", "/IM", img], capture_output=True)
    _kill_old_gateways()


def _kill_old_gateways(include_nssm_child: bool = False) -> None:
    """结束「没人管的」gateway.py 进程。

    只认领**孤儿**: 父进程已经没了、或者不是 nssm 的。服务自己拉起来的那个
    gateway.py 父进程是 nssm.exe, 默认跳过 —— 不然这个函数会把正在正常
    服务的进程杀掉, nssm 又按 AppExit=Restart 把它拉起来, 变成反复横跳。
    (install() 里会先 `nssm stop` 再调它, 那时服务进程已经不算「在跑」了。)
    """
    script = gateway_script()
    try:
        import gateway as _gw
    except Exception:                                    # noqa: BLE001
        return
    own = {os.getpid(), os.getppid()}
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command",
             "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" "
             "| Select-Object ProcessId,ParentProcessId,CommandLine "
             "| ConvertTo-Json -Compress"],
            capture_output=True, timeout=25)
        items = json.loads((out.stdout or b"{}").decode("utf-8", "replace")
                           or "{}")
    except Exception:                                    # noqa: BLE001
        return
    if isinstance(items, dict):
        items = [items]
    for it in items or []:
        try:
            pid = int(it.get("ProcessId"))
        except (TypeError, ValueError):
            continue
        if pid in own:
            continue
        cmd = str(it.get("CommandLine") or "")
        if "gateway.py" not in cmd or script.lower() not in cmd.lower():
            continue
        parent = 0
        try:
            parent = int(it.get("ParentProcessId") or 0)
        except (TypeError, ValueError):
            pass
        if not include_nssm_child and parent:
            # 父进程还在, 且不是我们(UI/python) -> 归 nssm 管, 别动
            pname = ""
            try:
                p = subprocess.run(
                    ["powershell", "-NoProfile", "-NonInteractive", "-Command",
                     f"(Get-CimInstance Win32_Process -Filter "
                     f"\"ProcessId={parent}\").Name"],
                    capture_output=True, timeout=15)
                pname = (p.stdout or b"").decode("utf-8", "replace").strip()
            except Exception:                            # noqa: BLE001
                pass
            if pname and "python" not in pname.lower():
                continue
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                       capture_output=True)


def serve_mode_of(cfg) -> str:
    """当前运行时: "router" (主池) / "kvmem" / "strata" / "freetoken"。"""
    mode = str((cfg or {}).get("serve_mode") or "router").strip().lower()
    return mode if mode in ("kvmem", "strata", "freetoken") else "router"


def gateway_base_url(cfg) -> str:
    """网关对外的调用地址 (四组模型共用这一个口)。

    绑 0.0.0.0 时这里给 127.0.0.1:本机自用最省事; 要给别的机器调用就把
    127.0.0.1 换成这台机器的局域网 IP —— 端口不变。
    """
    cfg = cfg or {}
    return f"http://127.0.0.1:{int(cfg.get('port', 18080) or 18080)}/v1"


def _build_server_args(cfg) -> List[str]:
    llama_dir = cfg["llama_dir"].rstrip("\\/")
    return [
        "--models-preset", os.path.join(llama_dir, "router-preset.ini"),
        "--models-max", str(cfg.get("models_max", 1)),
        "--sleep-idle-seconds", str(cfg.get("sleep_idle_seconds", 1800)),
        "--host", cfg.get("host", "0.0.0.0"),
        "--port", str(cfg.get("port", 18080)),
    ]


def install(cfg) -> Tuple[bool, str]:
    """注册并启动 llama-router 服务 —— 服务跑的是网关 gateway.py。

    网关占住 cfg.host:cfg.port, 把请求按 model 字段转给主池 (llama-server)
    或 KVMem (llama-kvmem-server) 后端; 两个后端互斥, 切换由网关自己完成。
    所以这一个服务对两种 serve_mode 都成立, 切换后端不用再重装服务
    (serve_mode 仍保留为「当前活跃组」的标记, 网关切换时会回写它)。
    """
    if not _is_admin():
        return False, need_admin_hint()
    ok, msg = ensure_nssm()
    if not ok:
        return False, msg

    llama_dir = cfg["llama_dir"].rstrip("\\/")
    exe = os.path.join(llama_dir, "llama-server.exe")
    if not os.path.exists(exe):
        return False, f"未找到 {exe}，请先安装 llama.cpp 或修改 config.json 的 llama_dir。"

    script = gateway_script()
    if not os.path.exists(script):
        return False, f"未找到 {script}，请重新下载完整项目。"

    os.makedirs(os.path.join(llama_dir, "logs"), exist_ok=True)

    nssm = nssm_path()

    _kill_old_server()
    subprocess.run([nssm, "stop", SERVICE_NAME], capture_output=True)
    subprocess.run([nssm, "remove", SERVICE_NAME, "confirm"], capture_output=True)
    # 服务已经停了, 这时任何还在跑的 gateway.py 都是「nssm 没收拾干净」留下的
    # 孤儿 —— 它还占着对外端口, 必须清掉, 否则新服务起来会「时好时坏」。
    _kill_old_gateways(include_nssm_child=True)
    # 端口/服务合并后 llama-kvmem 不再单独注册, 顺手清掉旧的
    subprocess.run([nssm, "stop", LEGACY_KVMEM_SERVICE], capture_output=True)
    subprocess.run([nssm, "remove", LEGACY_KVMEM_SERVICE, "confirm"], capture_output=True)

    py = _python_exe()
    r = subprocess.run(
        [nssm, "install", SERVICE_NAME, py, script],
        capture_output=True, text=True, encoding="utf-8", errors="replace"
    )
    if r.returncode != 0:
        return False, f"nssm install 失败:\n{r.stderr or r.stdout}"

    out = gateway_out_log()
    err = gateway_err_log()
    os.makedirs(os.path.dirname(out), exist_ok=True)
    subprocess.run([nssm, "set", SERVICE_NAME, "AppDirectory", config.app_dir()], capture_output=True)
    subprocess.run([nssm, "set", SERVICE_NAME, "AppStdout", out], capture_output=True)
    subprocess.run([nssm, "set", SERVICE_NAME, "AppStderr", err], capture_output=True)
    subprocess.run([nssm, "set", SERVICE_NAME, "AppRotateFiles", "1"], capture_output=True)
    subprocess.run([nssm, "set", SERVICE_NAME, "AppRotateBytes", "10485760"], capture_output=True)
    subprocess.run([nssm, "set", SERVICE_NAME, "Start", "SERVICE_AUTO_START"], capture_output=True)
    subprocess.run([nssm, "set", SERVICE_NAME, "AppExit", "Default", "Restart"], capture_output=True)
    subprocess.run([nssm, "set", SERVICE_NAME, "AppRestartDelay", "3000"], capture_output=True)

    r = subprocess.run([nssm, "start", SERVICE_NAME], capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        return False, f"nssm start 失败:\n{r.stderr or r.stdout}\n请查看 logs\\gateway.err.log"
    # 若 watchdog 已存在，建立依赖关系
    _ensure_service_dependency()
    return True, "服务已安装并启动 (网关)"


def _ensure_service_dependency() -> None:
    """让 watchdog 服务在 llama-router 之后启动 (依赖链)。失败不影响主服务。"""
    # 检查 watchdog 是否已注册
    r = subprocess.run(["sc", "query", WATCHDOG_SERVICE_NAME],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        return
    # 把 watchdog 加为 llama-router 的依赖
    subprocess.run(
        ["sc", "config", WATCHDOG_SERVICE_NAME, "depend=", "llama-router"],
        capture_output=True,
    )


def uninstall() -> Tuple[bool, str]:
    if not _is_admin():
        return False, need_admin_hint()
    nssm = nssm_path()
    subprocess.run([nssm, "stop", SERVICE_NAME], capture_output=True)
    subprocess.run([nssm, "remove", SERVICE_NAME, "confirm"], capture_output=True)
    subprocess.run([nssm, "stop", LEGACY_KVMEM_SERVICE], capture_output=True)
    subprocess.run([nssm, "remove", LEGACY_KVMEM_SERVICE, "confirm"], capture_output=True)
    return True, "服务已卸载"


def _sc_query(name: str = SERVICE_NAME) -> str:
    r = subprocess.run(["sc", "query", name], capture_output=True, text=True, encoding="utf-8", errors="replace")
    return r.stdout if r.returncode == 0 else ""


def _service_state(name: str = SERVICE_NAME) -> str:
    """返回 STOPPED / RUNNING / START_PENDING / ... / NOT_INSTALLED / UNKNOWN"""
    out = _sc_query(name)
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("STATE"):
            parts = line.split(":", 1)[1].strip().split()
            if len(parts) >= 2:
                return parts[1]
    return "NOT_INSTALLED" if not out else "UNKNOWN"


def _wait_service_state(target: str, timeout: float = 10.0,
                        name: str = SERVICE_NAME) -> bool:
    """轮询服务状态直到达到 target 或超时。"""
    import time as _t
    deadline = _t.time() + timeout
    while _t.time() < deadline:
        if _service_state(name) == target:
            return True
        _t.sleep(0.5)
    return False


def _wait_service_not_exists(timeout: float = 5.0,
                             name: str = SERVICE_NAME) -> bool:
    """轮询直到服务被完全删除。"""
    import time as _t
    deadline = _t.time() + timeout
    while _t.time() < deadline:
        if _service_state(name) == "NOT_INSTALLED":
            return True
        _t.sleep(0.3)
    return False


def start(name: str = SERVICE_NAME, log_hint: str = "") -> Tuple[bool, str]:
    if not _is_admin():
        return False, need_admin_hint()
    state = _service_state(name)
    if state == "NOT_INSTALLED":
        return False, "服务未安装，请先点击「安装服务」"
    if state in ("RUNNING", "START_PENDING"):
        return True, f"服务已在运行 (当前状态: {state})"
    last_err = ""
    for attempt in (1, 2):
        r = subprocess.run(["sc", "start", name], capture_output=True, text=True, encoding="utf-8", errors="replace")
        if r.returncode == 0:
            break
        last_err = (r.stderr or r.stdout or "").strip()
        if attempt == 1 and "PAUSED" in last_err.upper():
            import time as _t
            _t.sleep(2)
            continue
        break
    if r.returncode != 0:
        if not last_err:
            last_err = f"sc 未返回错误详情，请查看 {log_hint or err_log()}"
        return False, f"启动失败 (当前状态 {state}): {last_err}"
    return True, "已启动"


def stop(name: str = SERVICE_NAME) -> Tuple[bool, str]:
    if not _is_admin():
        return False, need_admin_hint()
    state = _service_state(name)
    if state in ("STOPPED", "STOP_PENDING", "NOT_INSTALLED"):
        return True, f"服务已停止 (当前状态: {state})"
    r = subprocess.run(["sc", "stop", name], capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        err = (r.stderr or r.stdout or "").strip()
        if not err:
            err = "sc 未返回错误详情，请查看系统事件查看器"
        return False, f"停止失败 (当前状态 {state}): {err}"
    return True, "已停止"


def restart(name: str = SERVICE_NAME, log_hint: str = "") -> Tuple[bool, str]:
    if not _is_admin():
        return False, need_admin_hint()
    state = _service_state(name)
    if state == "NOT_INSTALLED":
        return False, "服务未安装，请先点击「安装服务」"
    subprocess.run(["sc", "stop", name], capture_output=True)
    # 等真正停止，最多 15 秒
    for _ in range(15):
        if _service_state(name) == "STOPPED":
            break
        import time
        time.sleep(1)
    r = subprocess.run(["sc", "start", name], capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        err = (r.stderr or r.stdout or "").strip()
        if not err:
            err = f"sc 未返回错误详情，请查看 {log_hint or 'logs\\\\router.err.log'}"
        return False, f"重启失败: {err}"
    return True, "已重启"


def is_installed(name: str = SERVICE_NAME) -> bool:
    r = subprocess.run(["sc", "query", name], capture_output=True, text=True, encoding="utf-8", errors="replace")
    return r.returncode == 0


def status(name: str = SERVICE_NAME) -> str:
    """返回服务状态文本，未安装返回 'NOT_INSTALLED'。"""
    return _service_state(name)


def install_bat_cmd() -> str:
    """生成可被 .bat 直接调用的命令行（用于 NSSM AppParameters 形式）。"""
    cfg = config.load()
    return " ".join(_build_server_args(cfg))


def run_foreground(cfg) -> Tuple[bool, str]:
    """前台运行当前运行时的 exe (用于测试)。返回是否启动成功。"""
    if serve_mode_of(cfg) == "kvmem":
        import kvmem as _kvmem
        return _kvmem.run_foreground(cfg)
    llama_dir = cfg["llama_dir"].rstrip("\\/")
    exe = os.path.join(llama_dir, "llama-server.exe")
    if not os.path.exists(exe):
        return False, f"未找到 {exe}"
    args = [exe] + _build_server_args(cfg)
    subprocess.Popen(args, cwd=llama_dir)
    return True, f"已在前台启动: {' '.join(args)}"


WATCHDOG_SERVICE_NAME = "llama-router-watchdog"


def _python_exe() -> str:
    """NSSM 服务内调用 Python 时使用 sys.executable。"""
    return sys.executable


def install_watchdog() -> Tuple[bool, str]:
    """注册并启动 watchdog 守护服务。"""
    if not _is_admin():
        return False, need_admin_hint()

    cfg = config.load()
    wd = cfg.get("watchdog", {}) or {}
    if not wd.get("enabled", False):
        return False, "watchdog 未启用 (config.json -> watchdog.enabled = false)，请先在 UI「服务」页勾选"

    py = _python_exe()
    app = config.app_dir()
    script = os.path.join(app, "watchdog.py")

    nssm = nssm_path()

    subprocess.run(["sc", "stop", WATCHDOG_SERVICE_NAME], capture_output=True)
    subprocess.run(["sc", "delete", WATCHDOG_SERVICE_NAME], capture_output=True)

    args = f'"{py}" "{script}"'
    r = subprocess.run(
        [nssm, "install", WATCHDOG_SERVICE_NAME, py, script],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if r.returncode != 0:
        return False, f"nssm install watchdog 失败:\n{r.stderr or r.stdout}"

    wd_out = os.path.join(log_dir(), "watchdog.out.log")
    wd_err = os.path.join(log_dir(), "watchdog.err.log")
    os.makedirs(log_dir(), exist_ok=True)
    subprocess.run([nssm, "set", WATCHDOG_SERVICE_NAME, "AppDirectory", app], capture_output=True)
    subprocess.run([nssm, "set", WATCHDOG_SERVICE_NAME, "AppStdout", wd_out], capture_output=True)
    subprocess.run([nssm, "set", WATCHDOG_SERVICE_NAME, "AppStderr", wd_err], capture_output=True)
    subprocess.run([nssm, "set", WATCHDOG_SERVICE_NAME, "AppRotateFiles", "1"], capture_output=True)
    subprocess.run([nssm, "set", WATCHDOG_SERVICE_NAME, "AppRotateBytes", "10485760"], capture_output=True)
    subprocess.run([nssm, "set", WATCHDOG_SERVICE_NAME, "Start", "SERVICE_AUTO_START"], capture_output=True)
    subprocess.run([nssm, "set", WATCHDOG_SERVICE_NAME, "AppExit", "Default", "Restart"], capture_output=True)
    subprocess.run([nssm, "set", WATCHDOG_SERVICE_NAME, "AppRestartDelay", "5000"], capture_output=True)

    # 启动 watchdog 服务在 llama-router 之后（依赖 nssm 依赖项）
    subprocess.run(["sc", "start", WATCHDOG_SERVICE_NAME], capture_output=True)
    _ensure_service_dependency()
    return True, f"watchdog 服务已注册 (依赖 llama-router)，日志: {log_dir}\\watchdog.out.log"


def uninstall_watchdog() -> Tuple[bool, str]:
    if not _is_admin():
        return False, need_admin_hint()
    subprocess.run(["sc", "stop", WATCHDOG_SERVICE_NAME], capture_output=True)
    subprocess.run(["sc", "delete", WATCHDOG_SERVICE_NAME], capture_output=True)
    return True, "watchdog 服务已卸载"


def watchdog_status() -> str:
    r = subprocess.run(["sc", "query", WATCHDOG_SERVICE_NAME], capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        return "NOT_INSTALLED"
    for line in r.stdout.splitlines():
        line = line.strip()
        if line.startswith("STATE"):
            parts = line.split(":", 1)[1].strip().split()
            if len(parts) >= 2:
                return parts[1]
    return "UNKNOWN"


_CMDS = {
    "install": lambda: install(config.load()),
    "uninstall": uninstall,
    "start": start,
    "stop": stop,
    "restart": restart,
    "status": lambda: (True, status()),
    "foreground": lambda: run_foreground(config.load()),
    "install_watchdog": install_watchdog,
    "uninstall_watchdog": uninstall_watchdog,
    "watchdog_status": lambda: (True, watchdog_status()),
}


def main(argv: List[str]) -> int:
    if len(argv) < 2 or argv[1] not in _CMDS:
        print("用法: py -m service <install|uninstall|start|stop|restart|status|foreground|install_watchdog|uninstall_watchdog|watchdog_status>")
        return 2
    ok, msg = _CMDS[argv[1]]()
    print(msg)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
