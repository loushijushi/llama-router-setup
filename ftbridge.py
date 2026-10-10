"""FreeToken 分支托管。

FreeToken (`freetoken` pip 包, FlashML) 是另一个「MoE 摊到显存+内存」的
推理运行时, 自带 OpenAI **和** Anthropic 兼容的 HTTP 服务 (`ft serve`)。
定位和 KVMem / Strata 完全一样 —— 档位进同一份 models 清单
(`runtime == "freetoken"`), 由网关统一对外, 但**不认 llama-server 的任何
flag**:

  * 它的参数是 `ft serve` 自己的 flag (`--model-path` / `--moe-backend` /
    `--memory-ratio` ...), 一个 llama flag 都不吃;
  * `--host/--port` 决定绑定, 所以后端钉在回环上, 对外只有网关那一个口;
  * 模型是**目录** (桌面版下载到 `models_dir` 下的一个一个子目录), 不是
    单个 .gguf —— 所以档位登记的是目录路径;
  * 一个进程一个模型 —— 和 KVMem/Strata 一样属于「单模型组」。

它比 Strata 好接的地方在于**自带一套正规控制面**: `ft daemon` 在
`127.0.0.1:1900`(桌面版实际用 19000) 暴露
`/engine/start|switch|stop|status|metrics|logs`。但网关**不用它** ——
我们直接 spawn `ft serve`(和 Strata 分支同一套生命周期代码), 理由:
按需加载要求网关独占起停, 否则「网关说没加载、桌面版其实加载着」
这类不一致很难排查。daemon 控制面留着不用, 桌面版照旧自己玩。

注意它每个 serve 是**两个 python 进程**: venv 的 python.exe 只是壳,
真正执行的是 uv 管的 cpython-3.12。所以清理必须走进程树 (gateway 里已经
是 `taskkill /F /T /PID`), 镜像名是 `python.exe` 绝不能按 `/IM` 杀。

所有对外函数都不抛异常, 返回 (ok, message), 与 service.py / kvmem.py /
strata.py 一致。
"""
import glob
import json
import os
import socket
import subprocess
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

import config
import service

SERVICE_NAME = service.SERVICE_NAME

def _local_appdata() -> str:
    return os.environ.get("LOCALAPPDATA") or os.path.expandvars(r"%LOCALAPPDATA%")


# FreeToken 把运行时装在 %LOCALAPPDATA%\FreeToken, 桌面版在它旁边的
# "FreeToken Desktop" 里 —— 两者都是 **per-user** 安装, 所以跟着
# LOCALAPPDATA 走, 别把用户名写死。
DEFAULT_INSTALL_DIR = os.path.join(_local_appdata(), "FreeToken")
DEFAULT_DESKTOP_EXE = os.path.join(_local_appdata(), "FreeToken Desktop",
                                   "freetoken-desktop.exe")
DEFAULT_MODELS_DIR = r"D:\AI\freetoken\models"

# Windows 服务是 **LocalSystem** 跑的, 而 FreeToken 是装在**某个用户**的
# profile 下的 —— 对服务来说 %LOCALAPPDATA% 指向 systemprofile, 根本看不到
# 用户装的东西。所以要在 ProfileList 里找。
# 注意: 传给 winreg 的是 **不带 HKLM\ 前缀** 的子路径 —— 写成
# r"HKLM\SOFTWARE\..." 会拼成 HKLM\HKLM\SOFTWARE\... 然后 FileNotFoundError
# (PowerShell 的 HKLM:\ 才带前缀, winreg 不带)。
_PROFILE_LIST_KEY = r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\ProfileList"
# 注册表里 ProfileImagePath 有时写成 \??\C:\Users\x, 要去掉那个前缀
_NT_PREFIX = "\\??\\"


def _profile_dirs() -> List[str]:
    r"""所有「可能装了 FreeToken」的 profile 根目录, 最好用的排前面。

    先是本进程的 %USERPROFILE% (UI 以用户身份跑时就是这个, 直接命中), 再是
    注册表 ProfileList 里各用户的 profile —— 服务以 LocalSystem 跑时只有这条
    能管用 (它的 %USERPROFILE% 指向 systemprofile)。
    """
    out: List[str] = []
    cur = os.environ.get("USERPROFILE")
    if cur:
        out.append(cur)
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, _PROFILE_LIST_KEY) as k:
            i = 0
            while True:
                try:
                    # EnumKey 的返回 arity 在 Python 版本间变过 (2 个还是
                    # 3 个), 所以只取第一个 —— 那才是子键名。
                    enum = winreg.EnumKey(k, i)
                except OSError:
                    break
                i += 1
                sid = enum[0] if isinstance(enum, (tuple, list)) else enum
                try:
                    with winreg.OpenKey(k, sid) as sk:
                        prof, _ = winreg.QueryValueEx(sk, "ProfileImagePath")
                except OSError:
                    continue
                prof = str(prof)
                # 注册表里有时写成 \??\C:\Users\x, 去掉前缀
                if prof.startswith(_NT_PREFIX):
                    prof = prof[len(_NT_PREFIX):]
                # 服务账号的 profile 存成 %systemroot%\..., 不展开的话
                # 拼出来的路径永远不存在 (虽然那几份里本来也没有 FreeToken,
                # 但留着这种假路径以后排查会绕)。
                prof = os.path.expandvars(prof)
                if prof and prof not in out:
                    out.append(prof)
    except Exception:                                    # noqa: BLE001
        pass
    return out


def _discover_under(sub: str, marker: str) -> str:
    """在所有 profile 下找 `<profile>/<sub>/<marker>` 存在的那个。

    命中多个就返回空 —— 那说明机器上有好几个人各装了一份, 猜错比找不到更
    麻烦, 这时候让用户用配置里的字段明确指一个。
    """
    hits: List[str] = []
    for prof in _profile_dirs():
        p = os.path.join(prof, sub, marker)
        if os.path.isdir(p):
            hits.append(p)
    return hits[0] if len(hits) == 1 else ""


def _discover_install_dir() -> str:
    """在所有 profile 下找 `AppData/Local/FreeToken` 安装目录。"""
    return _discover_under(os.path.join("AppData", "Local"), "FreeToken")


VENV_PYTHON_REL = os.path.join("venv", "Scripts", "python.exe")
FT_EXE_REL = os.path.join("venv", "Scripts", "ft.exe")

# ft serve 的 argparse 默认端口。它不是 8080 —— 这是唯一一个需要改端口的
# 后端 (llama.cpp / Strata 的原生默认都是 8080), 但 `--port` 就是干这个的。
FT_SERVE_DEFAULT_PORT = 1919
# 它自带的 daemon 控制面端口。FT 官方 `freetoken/daemon/server.py`:
#     DEFAULT_PORT = 1900
# 早先这里写的是 19000 (按"桌面版起在 19000"的旧观察), 现在统一回**官方值
# 1900** —— 端口是 FT 自己的事, 网关不该按自己的记忆去改它。19000 仍留在
# 候选里, 万一你的 FT 确实是那个版本, 照样能探到。
FT_DAEMON_UPSTREAM_PORT = 1900
FT_DAEMON_DESKTOP_PORT = 1900
FT_DAEMON_LEGACY_PORT = 19000
DAEMON_PORT_CANDIDATES = (FT_DAEMON_DESKTOP_PORT, FT_DAEMON_LEGACY_PORT)


# ----------------------------------------------------------------------
# 档位 (config.json 的 models 清单)
# ----------------------------------------------------------------------
def is_freetoken(model: Dict[str, Any]) -> bool:
    return model.get("runtime") == config.FREETOKEN_RUNTIME


def freetoken_models(cfg: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    cfg = cfg if cfg is not None else config.load()
    return [m for m in cfg.get("models", []) if is_freetoken(m)]


def active_model(cfg: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    """当前档位。active_id 对不上时回落到第一个 FreeToken 档位。"""
    cfg = cfg if cfg is not None else config.load()
    ft = cfg.get("freetoken") or {}
    want = str(ft.get("active_id") or "").strip()
    items = freetoken_models(cfg)
    if want:
        for m in items:
            if str(m.get("id") or "") == want:
                return m
    return items[0] if items else None


def model_dir_of(entry: Dict[str, Any]) -> str:
    """档位条目 -> 模型目录 (ft serve 的 --model-path)。"""
    e = entry or {}
    return str(e.get("freetoken_model") or e.get("model") or "").strip().strip('"')


# ----------------------------------------------------------------------
# 定位可执行文件
# ----------------------------------------------------------------------
def install_dir(cfg: Optional[Dict[str, Any]] = None) -> str:
    """FreeToken 安装目录。

    解析顺序: 配置里显式写的 -> 探测到的。探测这一步必须有, 因为 Windows
    服务是 LocalSystem 跑的, 而 FreeToken 装在某个用户的 profile 下 ——
    服务的 %LOCALAPPDATA% 指向 systemprofile, 什么也看不到。
    """
    cfg = cfg if cfg is not None else config.load()
    d = str((cfg.get("freetoken") or {}).get("install_dir") or "").strip().strip('"')
    if d:
        return d
    return _discover_install_dir() or DEFAULT_INSTALL_DIR


def models_dir(cfg: Optional[Dict[str, Any]] = None) -> str:
    """模型根目录。**自动扫描的来源** —— 桌面版下载的模型都落在这下面。"""
    cfg = cfg if cfg is not None else config.load()
    d = str((cfg.get("freetoken") or {}).get("models_dir") or "").strip().strip('"')
    return d or DEFAULT_MODELS_DIR


def find_python(cfg: Optional[Dict[str, Any]] = None) -> str:
    """定位跑 `ft serve` 的 python。

    必须是它自带 venv 里那个 —— torch / tvm-ffi / flashlib 都装在那儿,
    拿系统 python 一定 import 失败。
    """
    cfg = cfg if cfg is not None else config.load()
    explicit = str((cfg.get("freetoken") or {}).get("python") or "").strip().strip('"')
    if explicit and os.path.isfile(explicit):
        return explicit
    root = install_dir(cfg)
    venv = os.path.join(root, VENV_PYTHON_REL)
    if os.path.isfile(venv):
        return venv
    return ""


def find_ft_exe(cfg: Optional[Dict[str, Any]] = None) -> str:
    """定位 ft.exe (只用来在 UI 上显示版本 / 诊断, 起服务用 python -m)。"""
    cfg = cfg if cfg is not None else config.load()
    root = install_dir(cfg)
    ft = os.path.join(root, FT_EXE_REL)
    return ft if os.path.isfile(ft) else ""


def desktop_exe(cfg: Optional[Dict[str, Any]] = None) -> str:
    """桌面版 exe (「打开 FreeToken 设置」按钮用)。

    桌面版装在安装目录旁边的 "FreeToken Desktop" 里, 但两者的 LocalAppData
    不一定属于同一个人 (服务/桌面版混装的情况), 所以两个候选都试。
    """
    cfg = cfg if cfg is not None else config.load()
    explicit = str((cfg.get("freetoken") or {}).get("desktop_exe") or "").strip().strip('"')
    if explicit and os.path.isfile(explicit):
        return explicit
    seen: List[str] = [DEFAULT_DESKTOP_EXE]
    root = install_dir(cfg)
    if root:
        guessed = os.path.join(os.path.dirname(root.rstrip("\\/")),
                               "FreeToken Desktop", "freetoken-desktop.exe")
        if guessed not in seen:
            seen.append(guessed)
    for lad in [os.path.join(p, "AppData", "Local") for p in _profile_dirs()]:
        p = os.path.join(lad, "FreeToken Desktop", "freetoken-desktop.exe")
        if p not in seen:
            seen.append(p)
    for p in seen:
        if os.path.isfile(p):
            return p
    return ""


def version(cfg: Optional[Dict[str, Any]] = None) -> str:
    """装的是哪个版本 (从 dist-info 目录名读, 不必起进程)。"""
    root = install_dir(cfg)
    hits = glob.glob(os.path.join(root, "venv", "Lib", "site-packages",
                                  "freetoken-*.dist-info"))
    if not hits:
        return ""
    base = os.path.basename(hits[0])
    return base[len("freetoken-"):-len(".dist-info")] if base.startswith("freetoken-") else ""


def port_of(cfg: Dict[str, Any]) -> int:
    """FreeToken 引擎该听哪个口。

    **FT 自己的端口, 网关不改。** 默认 1919 —— 那是 FT 官方定的:
        daemon/server.py:  DEFAULT_PORT = 1900 (控制面) / DEFAULT_SERVE_PORT = 1919 (引擎)

    以前这里返回 `gateway.backend_port` (8080), 也就是强行把 FT 塞进网关的
    内部口。后果是桌面版按它自己的约定去 1919 找引擎, 什么都找不到, 永远
    显示「模型没加载, 正在加载中」—— 而模型明明在跑。端口归 FT 自己管。
    """
    ft = (cfg or {}).get("freetoken") or {}
    try:
        return int(ft.get("port") or 1919)
    except (TypeError, ValueError):
        return 1919


def ready_timeout_of(cfg: Dict[str, Any]) -> float:
    """加载慢, 就绪超时单独放大 (默认 10 分钟)。

    实测这台机器上 35B A3B 的 p95 是 42 秒、首 token 7.8 秒, 但那是**已
    加载**之后; 从零加载还要把权重搬进显存, 冷启动比 Strata 慢的机器也有。
    """
    ft = (cfg or {}).get("freetoken") or {}
    try:
        return float(ft.get("ready_timeout", 600) or 600)
    except (TypeError, ValueError):
        return 600.0


# ----------------------------------------------------------------------
# 命令行
# ----------------------------------------------------------------------
def build_args(cfg: Dict[str, Any], entry: Dict[str, Any]) -> List[str]:
    """`python -m freetoken.cli serve` 的参数。

    只给三样东西: 模型目录、绑定地址、以及 `--served-model-name` (让它对外
    报我们登记的档位名, 而不是目录名)。其余引擎参数是 FreeToken 自己的
    flag, 一个 llama flag 都不加 —— 那些写在 models 清单条目的
    `freetoken_args` 里 (一个字符串, 原样拆成 argv)。
    """
    ft = (cfg or {}).get("freetoken") or {}
    args = [
        "-m", "freetoken.cli", "serve",
        "--model", model_dir_of(entry),
        "--host", "127.0.0.1",
        "--port", str(port_of(cfg)),
    ]
    name = str((entry or {}).get("id") or "").strip()
    if name:
        args += ["--served-model-name", name]
    # 桌面版那套 CORS 是给 tauri 界面用的, 网关不代理浏览器, 带上没意义
    extra = str((entry or {}).get("freetoken_args") or ft.get("extra_args") or "").strip()
    if extra:
        args += [a for a in extra.split() if a]
    return args


def command_line(cfg: Dict[str, Any], entry: Dict[str, Any]) -> str:
    return subprocess.list2cmdline([find_python(cfg) or "python"]
                                   + build_args(cfg, entry))


# ----------------------------------------------------------------------
# daemon 控制面 (让 FreeToken 桌面版看见「模型被调用」的唯一正路)
#
# 为什么走它而不是自己 spawn:
#   桌面版只认它自己那个 daemon (`ft daemon`, 19000)。网关自己 spawn 出来的
#   `ft serve` 对它**完全不存在** —— 实测过: engine 起在 1919/8085, 桌面版的
#   daemon `/engine/status` 一直是 `running:false`, 轮询 25 秒也不会变成
#   `adopted:true` (认领只在 daemon **启动时**发生, 运行期不会补认)。
#   所以要让用户在自己的软件里看到调用, 唯一的办法是**经它启动**。
#
# 这么做还顺带解决了两件事:
#   * 只跑**一个** engine —— 现在这个架构下, 桌面版的 engine 和路由的 engine
#     是两个进程, 同时加载就是双份显存;
#   * 两个入口同时有效 —— 引擎在 daemon 管的端口上, 网关转发过去, 软件那边
#     也能直接调。
#
# 端点都是公开有文档的 (`ft ctl --help` / `ft daemon --help` 里就列着):
#   GET  /health              daemon 自身健康
#   GET  /engine/status       当前 engine 状态 (含 running/adopted/model/port/pid)
#   GET  /engine/health       **代理到 engine** 的 /health (含 loading 进度)
#   GET  /engine/metrics      显存占用
#   GET  /engine/stats        代理到 engine 的 /v1/stats (吞吐/请求/p95)
#   POST /engine/start        {model, port, args[]}   -> {pid, idempotent}
#   POST /engine/switch       {model, port, args[], force?} (换模型, 内部先停后起)
#   POST /engine/stop         {force?}
#   GET  /engine/logs         SSE 日志流
# 注意 /engine/start 是**异步**的: 它很快就返回 {pid}, 真正把权重搬进显存还要
# 几十秒 —— 所以就绪判断必须另外轮询 engine 的 /health, 不能拿 start 的返回
# 当成「好了」。
# ----------------------------------------------------------------------
def _daemon_cfg(cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    cfg = cfg if cfg is not None else config.load()
    return cfg.get("freetoken") or {}


def daemon_port(cfg: Optional[Dict[str, Any]] = None) -> int:
    """daemon 控制面端口。桌面版实际用 19000, 上游默认 1900。"""
    v = _daemon_cfg(cfg).get("daemon_port")
    try:
        if v not in (None, ""):
            return int(v)
    except (TypeError, ValueError):
        pass
    return FT_DAEMON_DESKTOP_PORT


def daemon_base(cfg: Optional[Dict[str, Any]] = None) -> str:
    return f"http://127.0.0.1:{daemon_port(cfg)}"


def state_dir(cfg: Optional[Dict[str, Any]] = None) -> str:
    """桌面版 daemon 的 state-dir —— 它的 engine 日志和 serve.json 在这下面。

    这是**读**日志用的路径 (监控要 tail 桌面版 daemon 写的 engine 输出)。
    """
    explicit = str(_daemon_cfg(cfg).get("state_dir") or "").strip().strip('"')
    if explicit:
        return explicit
    found = _discover_under(".freetoken", "daemon")
    if found:
        return found
    env = os.environ.get("FREETOKEN_DAEMON_DIR")
    if env:
        return env
    return os.path.join(os.path.expanduser("~"), ".freetoken", "daemon")


def own_state_dir(cfg: Optional[Dict[str, Any]] = None) -> str:
    """**我们自启**的那个 daemon 专用的 state-dir。

    刻意和桌面版那份分开 —— 因为 FreeToken 的 daemon 是**单实例锁**的
    (`<state-dir>/daemon.pid` 一个进程独占)。实测: 桌面版的 daemon 活着时,
    让网关用同一个 state-dir 再起一个, 会被直接拒绝:

        ft daemon: another ft daemon holds ...\\.freetoken\\daemon\\daemon.pid

    那正好是「桌面版开着 + 我们自启」的场景 —— 而自启只在**桌面版的 daemon 不在**
    时才做, 所以本机同时只会有一个 daemon; 但锁文件是按目录算的, 一旦残留
    (上次没退干净) 就会把兜底堵死。所以给自启的 daemon 一个独立目录, 彻底
    避开这个锁 —— 宁可两个 daemon 各自独立, 也不要一个都起不来。
    """
    base = str(_daemon_cfg(cfg).get("state_dir") or "").strip().strip('"')
    if base:
        root = base
    else:
        root = os.path.join(os.path.expanduser("~"), ".freetoken")
        if _discover_under(".freetoken", "daemon"):
            root = os.path.dirname(state_dir(cfg))
    return os.path.join(root, "daemon-router")


def _ctl_json(method: str, path: str,
              body: Optional[Dict[str, Any]] = None,
              base: Optional[str] = None,
              cfg: Optional[Dict[str, Any]] = None,
              timeout: float = 10.0) -> Tuple[int, Any]:
    """调一次 daemon 控制面。返回 (http_code, 解析后的 json 或错误文本)。

    `base` 是**完整的 base URL** (`http://127.0.0.1:<port>`), 用来在「挨个试
    候选端口」时明确指定到底问谁; 平时不给就用 `cfg` 里的端口。

    为什么强制显式传 base: 早先这里只收 `cfg`, 于是 `find_daemon` 为了换端口
    传了一个 `freetoken` **子字典**, 而 `daemon_port()` 是从**整份配置**里取
    端口的 —— 那个子字典里没有 `freetoken` 键, 于是**静默退回默认 19000**。
    结果每个候选端口实际都在问 19000, 而调用方以为挨个试过了, 于是
    `find_daemon` 会返回一个**从没真正探测成功**的端口。用 base URL 就没有
    这种「形状对不上就悄悄走默认」的余地。
    """
    import urllib.error
    import urllib.request
    url = (base or daemon_base(cfg)) + path
    data = None
    headers = {"Accept": "application/json"}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            code = r.status
    except urllib.error.HTTPError as e:
        raw = e.read()
        code = e.code
    except Exception as e:                                # noqa: BLE001
        return 0, f"{type(e).__name__}: {e}"
    txt = raw.decode("utf-8", "replace")
    try:
        return code, json.loads(txt) if txt.strip() else {}
    except ValueError:
        return code, txt


def daemon_health(cfg: Optional[Dict[str, Any]] = None,
                  timeout: float = 3.0) -> Optional[Dict[str, Any]]:
    """daemon 自己在不在。返回 None = 没人监听 (不是错误)。"""
    code, obj = _ctl_json("GET", "/health", None, None, cfg, timeout)
    if code != 200 or not isinstance(obj, dict):
        return None
    return obj


def daemon_alive(cfg: Optional[Dict[str, Any]] = None,
                 timeout: float = 3.0) -> bool:
    return daemon_health(cfg, timeout) is not None


def find_daemon(cfg: Optional[Dict[str, Any]] = None,
                probe_timeout: float = 1.2) -> int:
    """在候选端口里找一个活着的 daemon。返回 0 = 一个都没有。

    桌面版起在 19000, 但如果哪天它换了端口 (或者用户只装了命令行版、自己手动
    起了 1900), 也不该就此认输 —— 逐个试。

    每个候选都用**显式 base URL** 去问, 保证「返回的端口」一定是「真正探测
    成功过的那个」—— 见 `_ctl_json` 里那个坑的说明。
    """
    want = daemon_port(cfg)
    order = [want] + [p for p in DAEMON_PORT_CANDIDATES if p != want]
    for p in order:
        code, obj = _ctl_json("GET", "/health", None,
                              f"http://127.0.0.1:{p}", None, probe_timeout)
        if code == 200 and isinstance(obj, dict):
            _last_daemon_port = p
            return p
    return 0


def engine_status(cfg: Optional[Dict[str, Any]] = None,
                  timeout: float = 1.2) -> Optional[Dict[str, Any]]:
    """当前 engine 状态 (running / model / port / pid / adopted)。"""
    code, obj = _ctl_json("GET", "/engine/status", None, None, cfg, timeout)
    if code != 200 or not isinstance(obj, dict):
        return None
    return obj


def engine_on_port(cfg: Optional[Dict[str, Any]] = None) -> int:
    """daemon 管的 engine 现在在哪个端口 (0 = 没有在跑)。

    这个值**不一定等于** `port_of(cfg)`: FreeToken 的 daemon 一次只管**一个**
    engine, 而桌面版是用它自己的默认端口 (1919) 请求的。所以谁最后请求谁赢 ——
    桌面版在自己界面里点一下加载, 就可能把我们放在 8080 上的那个顶掉。
    网关必须知道自己那个到底还在不在, 不能只看 `running`。
    """
    st = engine_status(cfg)
    if not st or not st.get("running"):
        return 0
    try:
        return int(st.get("port") or 0)
    except (TypeError, ValueError):
        return 0


def engine_ours(cfg: Optional[Dict[str, Any]] = None) -> bool:
    """daemon 管的 engine 是不是**我们**要的那个 (在共用内部口上)。"""
    return engine_on_port(cfg) == port_of(cfg) and engine_on_port(cfg) != 0


def engine_running(cfg: Optional[Dict[str, Any]] = None) -> bool:
    st = engine_status(cfg)
    return bool(st and st.get("running"))


def engine_running_model(cfg: Optional[Dict[str, Any]] = None) -> str:
    """现在跑着的 engine 用的是哪个模型目录。"""
    st = engine_status(cfg)
    return str((st or {}).get("model") or "")


def engine_health(cfg: Optional[Dict[str, Any]] = None,
                  timeout: float = 5.0) -> Optional[Dict[str, Any]]:
    """daemon **代理到 engine** 的 /health —— 带 loading 进度, 比问 engine 强。"""
    code, obj = _ctl_json("GET", "/engine/health", None, None, cfg, timeout)
    if code != 200 or not isinstance(obj, dict):
        return None
    return obj


def engine_metrics(cfg: Optional[Dict[str, Any]] = None,
                   timeout: float = 5.0) -> Optional[Dict[str, Any]]:
    code, obj = _ctl_json("GET", "/engine/metrics", None, None, cfg, timeout)
    return obj if (code == 200 and isinstance(obj, dict)) else None


def engine_log(cfg: Optional[Dict[str, Any]] = None) -> str:
    """engine 日志的主位置 (`<桌面版 state-dir>/logs/serve-<port>.log`)。"""
    return os.path.join(state_dir(cfg), "logs", f"serve-{port_of(cfg)}.log")


def engine_logs(cfg: Optional[Dict[str, Any]] = None) -> List[str]:
    """engine 日志的所有可能位置。

    **哪个 state-dir 取决于 engine 是谁起的**: 桌面版的 daemon 写它自己那份;
    网关自启的 daemon 写 `daemon-router` 那份 (见 own_state_dir)。两个都返回 ——
    「桌面版没开」时只有后者存在, 而缺的那个本来就不存在, 监控会当可选文件跳过。
    """
    port = port_of(cfg)
    out = [os.path.join(state_dir(cfg), "logs", f"serve-{port}.log")]
    own = os.path.join(own_state_dir(cfg), "logs", f"serve-{port}.log")
    if own not in out:
        out.append(own)
    return out


def ensure_daemon(cfg: Optional[Dict[str, Any]] = None,
                  wait: float = 20.0) -> Tuple[bool, str]:
    """确保有个 daemon 在跑, 返回 (ok, 说明)。

    优先用**已经活着的**那个 (通常是桌面版起的) —— 复用它才谈得上「让桌面版
    看见调用」。只有当它不在时, 我们才自己起一个; 自启的 daemon 用**独立的
    state-dir** (见 own_state_dir), 因为 FreeToken 的 daemon 是单实例锁的。
    """
    cfg = cfg if cfg is not None else config.load()
    # **先看有没有已经在跑的, 有就直接复用。** 这不是优化, 是正确性:
    # FreeToken 的 daemon 是单实例锁 + 端口独占, 无脑再起一个只会抢绑失败
    # (`Errno 10048 ... 19000`) 然后在那儿空转、把 err.log 刷满 (实测几十行),
    # 真正的监听者还是别人 —— 我们自己那个进程变成不监听却占着资源的僵尸。
    # 桌面版起的 daemon 最常见, 复用它才谈得上「让桌面版看见调用」。
    if daemon_alive(cfg, timeout=2.0):
        return True, f"复用已在运行的 daemon ({daemon_base(cfg)})"
    if not bool(_daemon_cfg(cfg).get("autostart_daemon", True)):
        return False, ("没有 FreeToken daemon 在跑, 而配置里关掉了自启。\n"
                       "请打开 FreeToken 桌面版, 或把 "
                       "freetoken.autostart_daemon 设成 true")
    py = find_python(cfg)
    if not py:
        return False, ("找不到 FreeToken 的 python, 起不了 daemon "
                       "(必须是它自带 venv 里的那个)")
    # 用桌面版那份 state-dir, 不是 own_state_dir() 的 daemon-router 子目录。
    # 之前刻意分开是怕跟桌面版抢 daemon.pid (ft daemon: another ft daemon
    # holds ...), 但那个代价太大: serve.json 写在 daemon-router 里, 桌面版
    # 读的是 daemon 目录 -> 打开桌面版永远显示"模型没加载, 正在加载中",
    # 而模型明明在跑。**同一个 state-dir 才是对的** —— 上面 daemon_alive()
    # 已经先探测过桌面版的 daemon, 它在跑就直接复用, 根本走不到这里;
    # 只有桌面版没开时才会自建, 而那时 daemon.pid 没人占。
    sd = state_dir(cfg)
    try:
        os.makedirs(os.path.join(sd, "logs"), exist_ok=True)
    except Exception:                                    # noqa: BLE001
        pass
    port = daemon_port(cfg)
    log = err_log()
    try:
        os.makedirs(os.path.dirname(log), exist_ok=True)
        fh = open(log, "a", encoding="utf-8", errors="replace")
    except Exception:                                    # noqa: BLE001
        fh = subprocess.DEVNULL                          # type: ignore[assignment]
    argv = [py, "-m", "freetoken.cli", "daemon",
            "--host", "127.0.0.1", "--port", str(port), "--state-dir", sd]
    try:
        proc = subprocess.Popen(argv, cwd=install_dir(cfg), stdout=fh, stderr=fh,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)
                                | getattr(subprocess, "DETACHED_PROCESS", 0))
    except Exception as e:                               # noqa: BLE001
        return False, f"起 daemon 失败: {e}"
    deadline = time.time() + max(1.0, float(wait))
    while time.time() < deadline:
        if daemon_alive(cfg, timeout=2.0):
            return True, f"已自启 daemon ({daemon_base(cfg)}, state-dir {sd})"
        # 端口是别人的了 (典型的: 桌面版刚好在这几百毫秒里抢赢了)。那就用
        # 别人的, 别硬等 —— 我们这个进程已经废了。
        if proc.poll() is not None:
            if daemon_alive(cfg, timeout=2.0):
                return True, (f"复用了同时启动的 daemon "
                              f"({daemon_base(cfg)}) —— 我们自己那个抢绑 "
                              f"{port} 失败已退出")
            return False, (f"daemon 起不来 ({daemon_base(cfg)} 绑不上, "
                           f"看 {os.path.basename(log)})")
        time.sleep(0.5)
    return False, (f"daemon 起了但 {wait:.0f}s 内没在 {daemon_base(cfg)} 上就绪; "
                   f"看 {os.path.basename(log)}")


def engine_start(cfg: Dict[str, Any], entry: Dict[str, Any],
                 timeout: float = 120.0) -> Tuple[bool, str]:
    """经 daemon 启动 engine。返回 (ok, 说明)。

    同一模型已经在跑时 daemon 会回 `idempotent: true` (省掉一次 40-60 秒的
    重载); 跑着别的模型时 daemon 会拒绝, 这时改用 `engine_switch`。
    注意这里是**异步**的: 返回时权重还没搬进显存, 就绪要另外轮询。
    """
    cfg = cfg if cfg is not None else config.load()
    mdir = model_dir_of(entry)
    if not mdir or not os.path.isdir(mdir):
        return False, f"模型目录不存在: {mdir or '(空)'}"
    ok, msg = ensure_daemon(cfg)
    if not ok:
        return False, msg
    body = {"model": mdir, "port": port_of(cfg),
            "args": _serve_args_for_daemon(cfg, entry)}
    code, obj = _ctl_json("POST", "/engine/start", body, None, cfg, timeout)
    if code == 200 and isinstance(obj, dict) and obj.get("pid"):
        idem = " (已在运行, 复用)" if obj.get("idempotent") else ""
        return True, f"daemon 已拉起 engine (pid {obj['pid']}, 端口 {port_of(cfg)}){idem}"
    return False, f"daemon 拒绝启动: HTTP {code} {_err_text(obj)}"


def engine_switch(cfg: Dict[str, Any], entry: Dict[str, Any],
                  force: bool = False,
                  timeout: float = 180.0) -> Tuple[bool, str]:
    """换模型: 让 daemon 内部「先停后起」(它会封账、优雅停机、超时强杀)。

    这是 daemon 存在的意义 —— 这些收尾活儿自己写要出一堆边界情况。
    """
    cfg = cfg if cfg is not None else config.load()
    mdir = model_dir_of(entry)
    if not mdir or not os.path.isdir(mdir):
        return False, f"模型目录不存在: {mdir or '(空)'}"
    ok, msg = ensure_daemon(cfg)
    if not ok:
        return False, msg
    body: Dict[str, Any] = {"model": mdir, "port": port_of(cfg),
                            "args": _serve_args_for_daemon(cfg, entry)}
    if force:
        body["force"] = True
    code, obj = _ctl_json("POST", "/engine/switch", body, None, cfg, timeout)
    if code == 200 and isinstance(obj, dict):
        return True, f"daemon 已切换到 {os.path.basename(mdir)} (pid {obj.get('pid')})"
    return False, f"daemon 切换失败: HTTP {code} {_err_text(obj)}"


def engine_stop(cfg: Optional[Dict[str, Any]] = None,
                force: bool = False,
                timeout: float = 60.0) -> Tuple[bool, str]:
    """经 daemon 停掉 engine (它会封账并等在途请求收尾)。"""
    cfg = cfg if cfg is not None else config.load()
    body = {"force": True} if force else {}
    code, obj = _ctl_json("POST", "/engine/stop", body, None, cfg, timeout)
    if code == 200 and isinstance(obj, dict):
        return True, "daemon 已停掉 engine"
    if code == 0:
        return False, f"没有 daemon 在跑 ({_err_text(obj)})"
    return False, f"daemon 停 engine 失败: HTTP {code} {_err_text(obj)}"


def cache_status(port: Optional[int] = None,
                 timeout: float = 15.0) -> Optional[Dict[str, Any]]:
    """`GET /v1/cache/status` —— 缓存池的实时几何与预算。"""
    port = port or port_of(config.load())
    import urllib.request
    try:
        with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/v1/cache/status", timeout=timeout) as r:
            obj = json.loads(r.read().decode("utf-8", "replace"))
        return obj if isinstance(obj, dict) else None
    except Exception:                                    # noqa: BLE001
        return None


def model_ctx(port: Optional[int] = None,
              timeout: float = 15.0) -> int:
    """这个模型声明的上下文长度 (`/v1/stats` 的 model.ctx)。"""
    port = port or port_of(config.load())
    import urllib.request
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/v1/stats",
                                    timeout=timeout) as r:
            obj = json.loads(r.read().decode("utf-8", "replace"))
        return int((obj.get("model") or {}).get("ctx") or 0)
    except Exception:                                    # noqa: BLE001
        return 0


def kv_pages(cfg: Optional[Dict[str, Any]] = None) -> int:
    """当前 KV 缓存有多少页 (≈ 能存多少 token, page_size 通常是 1)。"""
    st = stats(cfg)
    try:
        return int(((st or {}).get("kv") or {}).get("total_pages") or 0)
    except (TypeError, ValueError):
        return 0


def kv_capacity(port: Optional[int] = None, timeout: float = 3.0) -> int:
    """引擎**实际**能吃下多少 token (KV 页数 x page_size, 不是模型标称的 ctx)。

    这两个数会不一样, 而且不一样的时候听 KV 的: 启动时只自动分一点点 KV, 剩下
    的预算全给了 MoE 专家缓存, 而且它自己不会再长。引擎对超长输入不是截断而是
    **整条丢弃**:
    `WARNING Input sequence length 49016 exceeds 8196, request 0 is dropped.`
    所以对外报的上下文必须以这个为准 —— 不然客户端会照着标称的 262144 发, 然后
    请求人间蒸发, 还查不出是谁的锅。
    """
    port = port or port_of(config.load())
    import urllib.request
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/v1/stats",
                                    timeout=timeout) as r:
            obj = json.loads(r.read().decode("utf-8", "replace"))
        kv = obj.get("kv") or {}
        return int(kv.get("total_pages") or 0) * int(kv.get("page_size") or 1)
    except Exception:                                    # noqa: BLE001
        return 0


def _engine_post(port: int, path: str, body: Dict[str, Any],
                 timeout: float) -> Tuple[int, Any]:
    """直接打 engine (不是 daemon) 的一个 POST。"""
    import urllib.error
    import urllib.request
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw, code = r.read(), r.status
    except urllib.error.HTTPError as e:
        raw, code = e.read(), e.code
    except Exception as e:                                # noqa: BLE001
        return 0, f"{type(e).__name__}: {e}"
    txt = raw.decode("utf-8", "replace")
    try:
        return code, json.loads(txt) if txt.strip() else {}
    except ValueError:
        return code, txt


def reclaim(cfg: Optional[Dict[str, Any]] = None,
            entry: Optional[Dict[str, Any]] = None) -> Tuple[bool, str]:
    """把 daemon 管的 engine **抢回网关的共用内部口**。

    为什么需要: FreeToken 的 daemon 一次只管一个 engine, 桌面端用 1919 请求、
    网关用 8080, 谁最后请求谁赢。所以桌面端点一下加载, 网关这个就被顶掉了。

    这里是给「桌面端只读、加载统一归网关」这个约定准备的动作 —— 不用发一个
    请求去触发切换, 点一下就行。
    """
    cfg = cfg if cfg is not None else config.load()
    want = port_of(cfg)
    on = engine_on_port(cfg)
    if on == want:
        return True, f"引擎已经在 {want}, 不用动"
    entry = entry or active_model(cfg)
    if not entry:
        return False, ("没有 FreeToken 档位, 不知道该抢回哪个模型 "
                       "(请先在「⚡ FreeToken」页登记一个)")
    if on:
        return engine_switch(cfg, entry)
    return engine_start(cfg, entry)


def _err_text(obj: Any) -> str:
    if isinstance(obj, dict):
        for k in ("error", "detail", "message"):
            if obj.get(k):
                return str(obj[k])
    return str(obj)[:200]


def _split_args(text: str) -> List[str]:
    """按 shell 习惯切参数: 支持引号与转义。

    以前是 `text.split()` —— 纯按空格切, 于是
        extra_args: '--mmproj "D:\\my models\\x.gguf"'
    会被切成 '--mmproj' '"D:\\my' 'models\\x.gguf"' 三段, 带空格的路径直接
    废掉。既然网关对 FreeToken 是纯转发, 参数原样过去就该是它的本意。
    """
    try:
        import shlex
        return shlex.split(text, posix=False)
    except Exception:                                    # noqa: BLE001
        return [a for a in text.split() if a]


def _serve_args_for_daemon(cfg: Dict[str, Any],
                           entry: Dict[str, Any]) -> List[str]:
    """交给 daemon 去起 engine 时该传的 `args`。

    **不含 `--model` / `--port` / `--host`** —— 那三个是 daemon 自己拼的
    (`model` 和 `port` 是 `/engine/start` 的独立字段, host 由 daemon 固定成
    回环)。重复传会跟它自己加的值打架, 所以这里只留「额外 flag」。
    """
    extra = str((entry or {}).get("freetoken_args")
                or (cfg.get("freetoken") or {}).get("extra_args") or "").strip()
    args: List[str] = []
    if extra:
        args = _split_args(extra)
    # 桌面版的 tauri 界面需要 CORS 才能从自己的协议里连; 网关不代理浏览器,
    # 但 engine 是共用的, 带上它不影响什么, 反而不带的话桌面版可能连不上
    if not any(a.startswith("--cors-origins") for a in args):
        args += ["--cors-origins",
                 "tauri://localhost,http://tauri.localhost,http://localhost:1420"]
    return args


# ----------------------------------------------------------------------
# 扫描模型目录 (登记新档位用)
# ----------------------------------------------------------------------
def _looks_like_model(path: str) -> Tuple[bool, str]:
    """一个目录像不像 FreeToken 能加载的模型。

    `--model-path` 收的是模型**目录**, 里面得有权重。按 FreeToken 自己的
    习惯认这几种: HF 目录 (config.json)、safetensors 分片、gguf 文件。
    认不出来也列出来 (ok=False + 原因), 让 UI 提示, 而不是悄悄不显示 ——
    扫不到比报错更难查。
    """
    if not os.path.isdir(path):
        return False, "不是目录"
    names = set(os.listdir(path))
    if "config.json" in names:
        return True, ""
    if any(n.endswith(".safetensors") for n in names):
        return True, ""
    if any(n.endswith(".gguf") for n in names):
        return True, ""
    if not names:
        return False, "空目录"
    return False, "没找到 config.json / *.safetensors / *.gguf"


def scan_models(cfg: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """列出 models_dir 下每个能当档位用的模型目录。

    跳开 `.hf-cache` 这种点开头的目录 (HF 的缓存, 不是模型)。
    """
    root = models_dir(cfg)
    out: List[Dict[str, Any]] = []
    if not root or not os.path.isdir(root):
        return out
    for path in sorted(glob.glob(os.path.join(root, "*"))):
        if not os.path.isdir(path):
            continue
        name = os.path.basename(path)
        if name.startswith("."):
            continue
        ok, why = _looks_like_model(path)
        out.append({
            "path": os.path.abspath(path),
            "dir": name,
            "id": name,
            "ok": ok,
            "why": why,
        })
    return out


def entry_from_dir(path: str) -> Dict[str, Any]:
    """把一个模型目录变成 config.json 里的档位条目。"""
    ap = os.path.abspath(path)
    mid = os.path.basename(ap)
    return {
        "id": mid,
        "alias": mid,
        "enabled": True,
        "runtime": config.FREETOKEN_RUNTIME,
        "freetoken_model": ap,
        "freetoken_args": "",
        "params": {},
    }


# ----------------------------------------------------------------------
# 端口 / 健康
# ----------------------------------------------------------------------
def port_busy(port: int, host: str = "") -> bool:
    """端口是否已被占用 (尝试绑定即知)。"""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind((host or "127.0.0.1", int(port)))
        return False
    except OSError:
        return True
    finally:
        s.close()


def health(cfg: Dict[str, Any], timeout: float = 3.0) -> Optional[Dict[str, Any]]:
    """问后端 /health; 没起来返回 None。

    FreeToken 的 /health 有完整的生命周期: loading(带 phase/progress) /
    ok(带 model、instance_id、uptime_s) / error(带 message)。所以网关判断
    「就绪」比 Strata 那套准得多 —— loading 也算活着, 但还没能干活。
    要判断「能干活」请用 `is_ready`(它还会验身份)。
    """
    return health_at(port_of(cfg), timeout)


def health_at(port: int, timeout: float = 3.0) -> Optional[Dict[str, Any]]:
    """问某个口上的 /health, 解析成 dict; 连不上/不是 json 返回 None。"""
    import urllib.request
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health",
                                    timeout=timeout) as r:
            obj = json.loads(r.read().decode("utf-8", "replace"))
        return obj if isinstance(obj, dict) else None
    except Exception:                                    # noqa: BLE001
        return None


def _looks_like_freetoken(obj: Dict[str, Any]) -> bool:
    """这个 /health 是不是 FreeToken engine 回的。

    **必须验身份, 不能只看 `status == "ok"`** —— 四个后端共用内部口 8080,
    而 llama-server 的 /health 就是 `{"status": "ok"}` (实测), Strata 也是个
    `status: ok`。只认状态值的话, 「切到主池之后」会把主池误判成 FreeToken 还在
    跑 —— 那会让网关以为 8080 被 FreeToken 占着, 互斥判定也跟着错。

    认 **`instance_id`** 这个字段: 看它的 `server/control_api.py::build_health`,
    三个分支 (loading / ok / error) **都**带 `instance_id`, 而 llama-server 和
    Strata 都没有。

    **别拿 `maintenance` 当判据** —— 只有 `status == "ok"` 那支才有它, 加载
    期间的那支没有。曾经用 `instance_id + maintenance` 当判据, 结果
    `wait_ready` 恰恰在加载期间轮询, 每一次都被判成「不是 FreeToken」, 白等到
    超时。踩过, 别再来一遍。
    """
    return isinstance(obj, dict) and bool(obj.get("instance_id"))


def is_ready_at(port: int, timeout: float = 3.0) -> bool:
    """这个口上是不是一个**已经能干活**的 FreeToken engine。

    两个条件都要: 身份是 FreeToken (见 `_looks_like_freetoken`), 且状态是 ok ——
    它加载期间也回 200 (`{"status":"loading","phase":...}`), 那是给桌面版看
    进度用的, 那时还不能接请求。
    """
    obj = health_at(port, timeout)
    return bool(obj) and _looks_like_freetoken(obj) and \
        str(obj.get("status") or "").lower() == "ok"


def is_ready(cfg: Dict[str, Any], timeout: float = 3.0) -> bool:
    """/health 明确说 ok 且确实是 FreeToken 才算就绪。"""
    return is_ready_at(port_of(cfg), timeout)


def stats(cfg: Dict[str, Any], timeout: float = 3.0) -> Optional[Dict[str, Any]]:
    """`/v1/stats` —— 显存占用 / 吞吐 / 请求数, 给 UI 的「基础信息」用。"""
    import urllib.request
    url = f"http://127.0.0.1:{port_of(cfg)}/v1/stats"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            obj = json.loads(r.read().decode("utf-8", "replace"))
        return obj if isinstance(obj, dict) else None
    except Exception:
        return None


# ----------------------------------------------------------------------
# 日志
# ----------------------------------------------------------------------
def out_log() -> str:
    return os.path.join(service.log_dir(), "freetoken.out.log")


def err_log() -> str:
    return os.path.join(service.log_dir(), "freetoken.err.log")


# ----------------------------------------------------------------------
# 打开桌面版 (UI 上的「设置」按钮)
# ----------------------------------------------------------------------
def open_desktop(cfg: Optional[Dict[str, Any]] = None) -> Tuple[bool, str]:
    """拉起 FreeToken 桌面版, 让你在里面设置。

    **先校验 exe 存不存在, 再看有没有在跑** —— 顺序反了的话, 配置里的
    `desktop_exe` 指错了也会报「已经在运行」, 让人以为按钮是好的。
    已经在跑就不重复拉 (它有单实例, 再起一个只会把焦点抢过去)。

    注意: 桌面版会自己管一个 daemon + engine, **和网关这一组互不相干** ——
    两边同时加载同一个模型会双份吃显存。所以按钮旁边必须提醒这一点。
    """
    exe = desktop_exe(cfg)
    if not exe or not os.path.isfile(exe):
        return False, (f"未找到 FreeToken 桌面版 "
                       f"(已找 {DEFAULT_DESKTOP_EXE}) —— 只装了命令行版？")
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command",
             "(Get-Process freetoken-desktop -ErrorAction SilentlyContinue)"
             " | Select-Object -First 1 -ExpandProperty Id"],
            capture_output=True, timeout=15)
        running = (out.stdout or b"").decode("utf-8", "replace").strip()
        if running:
            return True, f"FreeToken 已经在运行 (pid {running}), 已把窗口叫到前台"
    except Exception:                                    # noqa: BLE001
        pass
    try:
        subprocess.Popen([exe], cwd=os.path.dirname(exe),
                         creationflags=getattr(subprocess, "DETACHED_PROCESS", 0)
                         | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
    except Exception as e:                               # noqa: BLE001
        return False, f"启动 FreeToken 失败: {e}"
    return True, "已打开 FreeToken 桌面版"


# ----------------------------------------------------------------------
# 公共校验 / 服务开关
# ----------------------------------------------------------------------
def _check_ready(cfg: Dict[str, Any]) -> Tuple[bool, str, Dict[str, Any]]:
    """起 FreeToken 前的公共校验。返回 (ok, msg, model)。"""
    model = active_model(cfg)
    if not model:
        return False, ("没有 FreeToken 档位：请先在「⚡ FreeToken」页从 "
                       f"{models_dir(cfg)} 里登记一个模型。"), {}
    py = find_python(cfg)
    if not py:
        return False, (f"未找到 FreeToken 的 python "
                       f"(已找 {os.path.join(install_dir(cfg), VENV_PYTHON_REL)})。\n"
                       f"多半是这台机器上装了多份、探测有歧义 —— 在"
                       f"「⚡ FreeToken」页把「安装目录」明确填一下。"), model
    mdir = model_dir_of(model)
    if not mdir or not os.path.isdir(mdir):
        return False, f"模型目录不存在: {mdir or '(空)'}", model
    return True, "", model


def remember_install_dir(cfg: Optional[Dict[str, Any]] = None) -> str:
    """把探测到的安装目录**写回 config.json**。

    服务是 LocalSystem 跑��, 而 FreeToken 装在某个用户的 profile 下, 所以
    每次启动都得在注册表里翻一遍 profile 才能找到。翻到之后落盘, 下次直接读
    配置 —— 顺带也解决了「以后换了用户」这种歧义 (那时手工改这一个字段就
    行, 不用改代码)。
    """
    cfg = cfg if cfg is not None else config.load()
    ft = cfg.setdefault("freetoken", {})
    if str(ft.get("install_dir") or "").strip():
        return str(ft["install_dir"])
    found = _discover_install_dir()
    if not found:
        return ""
    ft["install_dir"] = found
    try:
        config.save(cfg)
    except Exception:                                    # noqa: BLE001
        pass
    return found


def install(cfg: Optional[Dict[str, Any]] = None,
            start_service: bool = True) -> Tuple[bool, str]:
    """「应用并重装」: 校验档位 -> 标 serve_mode=freetoken -> 装网关服务。"""
    cfg = cfg if cfg is not None else config.load()
    cfg["serve_mode"] = config.FREETOKEN_RUNTIME
    remember_install_dir(cfg)            # 落盘, 别让服务每次都去翻注册表
    ok, msg, model = _check_ready(cfg)
    if not ok:
        return False, msg
    ok, msg = service.install(cfg)
    if not ok:
        return False, msg
    if not start_service:
        service.stop(SERVICE_NAME)
        return True, (f"网关服务已注册但未启动 (档位 {model.get('id')}), "
                      f"需要时点「启动」")
    return True, (f"网关服务已安装并启动 (档位 {model.get('id')}, "
                  f"FreeToken 后端 127.0.0.1:{port_of(cfg)})")


def uninstall() -> Tuple[bool, str]:
    if not service._is_admin():
        return False, service.need_admin_hint()
    service.stop(SERVICE_NAME)
    subprocess.run([service.nssm_path(), "remove", SERVICE_NAME, "confirm"],
                   capture_output=True)
    return True, "运行服务已卸载"


def start() -> Tuple[bool, str]:
    if not service._is_admin():
        return False, service.need_admin_hint()
    st = service.status(SERVICE_NAME)
    if st == "NOT_INSTALLED":
        return False, "运行服务未安装，请先在「⚡ FreeToken」页点「应用并重装」"
    if st in ("RUNNING", "START_PENDING"):
        return True, f"网关服务已在运行 (状态 {st})"
    cfg = config.load()
    if active_model(cfg):
        port = port_of(cfg)
        if port_busy(port):
            return False, (f"端口 {port} 已被占用 (多半是桌面版自己起的 "
                           f"engine，或手动跑的 ft serve)，请先关掉它。")
    return service.start(SERVICE_NAME, log_hint=err_log())


def stop() -> Tuple[bool, str]:
    return service.stop(SERVICE_NAME)


def restart() -> Tuple[bool, str]:
    if not service._is_admin():
        return False, service.need_admin_hint()
    if service.status(SERVICE_NAME) == "NOT_INSTALLED":
        return False, "运行服务未安装，请先在「⚡ FreeToken」页点「应用并重装」"
    return service.restart(SERVICE_NAME, log_hint=err_log())


def run_foreground(cfg: Optional[Dict[str, Any]] = None) -> Tuple[bool, str]:
    """直接拉起一个 ft serve (不走服务, 便于看输出 / 不需要管理员)。"""
    cfg = cfg if cfg is not None else config.load()
    ok, msg, model = _check_ready(cfg)
    if not ok:
        return False, msg
    port = port_of(cfg)
    if port_busy(port):
        return False, f"端口 {port} 已被占用，无法再启动一个实例。"
    py = find_python(cfg)
    args = build_args(cfg, model)
    os.makedirs(service.log_dir(), exist_ok=True)
    print(f"[freetoken] {py} {subprocess.list2cmdline(args)}")
    try:
        proc = subprocess.Popen([py] + args, cwd=install_dir(cfg),
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except Exception as e:                               # noqa: BLE001
        return False, f"启动失败: {e}"
    return True, (f"已在后台启动 ft serve (pid={proc.pid}); /health 会在"
                  f"权重搬进显存后才回 ok; 日志看 {os.path.basename(err_log())}")

# 最近一次 find_daemon 探到的端口 (探到就是活的; 没探到不更新)
_last_daemon_port = 0


def daemon_and_status(cfg: Optional[Dict[str, Any]] = None,
                      probe_timeout: float = 1.2) -> Tuple[int, Optional[Dict[str, Any]]]:
    """一次往返拿到 (daemon 端口, engine 状态)。daemon 不在时返回 (0, None)。

    为什么要合并: 原来调用方要先 `find_daemon()` 再 `engine_status()`, 而后者
    内部**又**探一遍 daemon —— 同一个进程被问两遍, `/health` 因此要 4 秒以上,
    网页版和监控的轮询全被拖爆 (实测 UI 直接误报「无法连接」)。
    """
    port = find_daemon(cfg, probe_timeout=probe_timeout)
    if not port:
        return 0, None
    code, obj = _ctl_json("GET", "/engine/status", None,
                          f"http://127.0.0.1:{port}", None, probe_timeout)
    if code == 200 and isinstance(obj, dict):
        return port, obj
    return port, None
