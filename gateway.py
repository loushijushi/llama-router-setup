# -*- coding: utf-8 -*-
"""llama-router 网关 —— Windows 服务 `llama-router` 的进程本体。

为什么要它
----------
config.json 里的模型分三组, 它们跑在**三个不同的运行时**上:

  * 主池 (默认组)  -> llama-server.exe, router 模式 (--models-preset),
                      `/v1/models` 只列主池, 组内切换由 router 自己完成;
  * KVMem 档位     -> llama-kvmem-server.exe, **不支持** --models-preset
                      (单模型进程), 一次只能跑一个档位, 换档位必须重启;
  * Strata 档位    -> C:\\Strata 的 serve/server.py (自带 OpenAI 兼容层),
                      单模型进程, 参数全在 strata-<档位>.json 里,
                      压根不吃 llama flag; 加载 35-55GB 到内存, 起步 1-3 分钟。

本机只有 16GB 显存, 三组不可能同时常驻。所以由本进程占住对外端口
(cfg.host:cfg.port, 默认 0.0.0.0:18080), 把请求按 `model` 字段路由:

  /v1/models          -> 读 config.json 合成, 三组模型都列出来
                         (tags 里是 pool / kvmem / strata)
  /models, /props     -> 同上 (老接口): /props 还会把后端自报的
                         role 改写成 router, 否则前端会退化成单模型界面;
                         Strata 没有这个接口, 网关自己合成一份
  /models/sse         -> 网关端这条流: 主池原生流逐块中继, 单模型组没有这个
                         接口就自己按 /v1/models 合成状态事件
                         (Strata 取 /health.loaded)
  /models/load|unload -> 按 model 切组; 单模型组 (KVMem/Strata) 没有
                         这两个接口, 网关代答 (unload = 整个停掉进程)
  其它 (含 /v1/*, /v1/messages) -> 找到 model 属于哪一组, 确保那一组的后端
                         在跑, 再把请求原样转发 (支持 SSE 流式)
  /health             -> 网关自己的健康状态 + 当前后端

Strata 活跃时网关**完全让路**: 它的接口原样转发, 一个字节都不改
(PASSTHROUGH_GROUPS), 网关只负责停掉其它占资源的后端 + 转请求。

三个后端互斥: 切换 = 等在途请求结束 -> 停当前后端 -> 起新的 -> 等就绪。
切换期间到达的请求**同步阻塞等待** (保证客户端一次成功)。就绪超时按组
分开: 主池/KVMem 用 gateway.ready_timeout (300s), Strata 用
strata.ready_timeout (900s)。

后端只绑回环, 不对外, 而且**三个共用同一个内部口** (默认 8080, llama.cpp
和 Strata 的原生默认端口) —— 它们本来就互斥, 同一时刻只跑一个, 共用不会
撞; 这样谁都不用为了接入网关去改自己的端口。
**对外永远只有 cfg.host:cfg.port 那一个口** (默认 0.0.0.0:18080, 刻意避开
8080) —— 远程调用方只需要知道这一个地址。
"""
from __future__ import annotations

import http.client
import io
import json
import os
import socket
import subprocess
import sys
import threading
import time
import traceback
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import unquote, urlparse

import config

POOL = "pool"
KVMEM = "kvmem"
STRATA = "strata"
GROUPS = (POOL, KVMEM, STRATA)

# 组的中文名 (日志/health 用)
GROUP_CN = {POOL: "主池", KVMEM: "KVMem", STRATA: "Strata"}
# 只有主池是「多模型进程池」, 另外两组都是单模型进程 (换档位 = 重启进程)
SINGLE_MODEL_GROUPS = (KVMEM, STRATA)

# 这些路径**不因为被访问就把模型拉起来**。开着网页版、浏览器里的 PWA
# service worker 每次上线都要 GET 一下 /sw.js, 网页版自己还会轮询
# /props 和 /models/sse —— 它们都不该顺手把 40GB 的 Strata 拖进内存。
NO_LOAD_PATHS = frozenset((
    "/health", "/healthz", "/v1/models", "/models", "/props",
    "/models/sse", "/slots", "/metrics", "/api/tags", "/api/ps",
))

# 静态资源: 这些是浏览器自己要的文件, 与模型无关。后端没跑的时候回 404
# (宁可页面报缺文件, 也不要为了取一张图标去加载一个模型)
_ASSET_SUFFIX = frozenset((
    ".js", ".mjs", ".css", ".map", ".json", ".txt", ".webmanifest",
    ".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico", ".webp",
    ".woff", ".woff2", ".ttf", ".wasm",
))


def is_no_load_path(path: str) -> bool:
    """这个路径要不要「不加载模型地回答」。"""
    p = (path or "").split("?", 1)[0]
    if p in NO_LOAD_PATHS:
        return True
    # 单查一个模型 (/v1/models/<id>) 也是查目录
    if p.startswith("/v1/models/"):
        return True
    return looks_like_asset(p)


def looks_like_asset(path: str) -> bool:
    p = (path or "").split("?", 1)[0]
    return p.lower().endswith(tuple(_ASSET_SUFFIX))

# 逐跳头, 不许透传 (否则会和网关自己的响应头打架)
HOP_BY_HOP = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailers", "transfer-encoding", "upgrade",
}
# 这两个由网关按实际情况重新决定
FORWARD_STRIP = HOP_BY_HOP | {"host", "content-length"}

MAX_BODY = 64 * 1024 * 1024
UPSTREAM_TIMEOUT = 600.0        # 转发时等后端的秒数 (生成可能很久)
LOG_ROTATE_BYTES = 10 * 1024 * 1024
SSE_POLL = 0.5                  # 网关自答 /models/sse 时轮询状态的间隔


class BackendError(Exception):
    """后端起不来 / 等不到就绪 —— 网关回 503。"""


# ----------------------------------------------------------------------
# 模型清单
# ----------------------------------------------------------------------
def default_group(cfg: Dict[str, Any]) -> str:
    """「默认组」= config.serve_mode 指定的那一组 (认不出来的当主池)。

    按需加载时网关开机不加载任何模型, 于是「不带 model 的请求」没有
    active 可跟, 只能按这个解析 —— 它替代了老逻辑里 self.active or POOL
    的位置。
    """
    mode = str((cfg or {}).get("serve_mode") or "router").strip().lower()
    return mode if mode in SINGLE_MODEL_GROUPS else POOL


def backend_port_of(cfg: Dict[str, Any], group: str) -> int:
    """某一组的后端该听哪个内部端口。

    主池 / KVMem 共用 `gateway.backend_port` (默认 8080) —— 它们本来就互斥,
    同一时刻只跑一个, 各占一个端口纯属浪费。Strata 用它自己 json 里写的那个
    (见 strata.port_of)。

    对外永远只有 `config.host:config.port` 那一个口。
    """
    if group == STRATA:
        import strata
        try:
            return int(strata.port_of(cfg))
        except Exception:                                # noqa: BLE001
            pass
    gw = (cfg or {}).get("gateway") or {}
    try:
        return int(gw.get("backend_port") or 8080)
    except (TypeError, ValueError):
        return 8080


def preload_of(cfg: Dict[str, Any]) -> bool:
    gw = (cfg or {}).get("gateway") or {}
    return bool(gw.get("preload", False))


def group_of(model: Dict[str, Any]) -> str:
    rt = model.get("runtime")
    if rt == config.KVMEM_RUNTIME:
        return KVMEM
    if rt == config.STRATA_RUNTIME:
        return STRATA
    return POOL


# Strata 活跃时网关要**完全让路**: 它自己的接口原样转发,
# 一个字节都不改。
# 之前网关替 Strata 合成了 /props(role 改写成 router)和 /models/sse —— 那是
# llama.cpp 网页版的口径, Strata 没有这些接口也没有这些语义, 硬塞给它
# 的后果是: 浏览器里开着的 llama.cpp 网页版以为后面是 router, 拿到一个
# 200 的 /models/sse 就一直轮询, 接着把 /tools、/build.json、
# /v1/streams/* 这些 **只有 llama-server 才有**的接口打过去, 全部 404,
# 网页版直接报错。实测就是这样被抓到的。
#
# 网关在这两条路上只做两件事:
#   1. 停掉其它占资源的后端 (显存/内存就它一份)
#   2. 把请求原样转给它
# /v1/models 例外 —— 那份三组合并的目录是网关自己的「聚合入口」, 用来统一
# 选组; 后端自己那份 /v1/models 只报它当前那一个模型 (这是它如实反映
# 「现在跑的是谁」, 不该被网关的目录盖掉)。其它接口一概不碰。
PASSTHROUGH_GROUPS = frozenset((STRATA,))


def catalog(cfg: Dict[str, Any]) -> List[Dict[str, str]]:
    """config 里所有启用的模型 -> [{id, alias, group}]。"""
    out: List[Dict[str, str]] = []
    for m in cfg.get("models", []) or []:
        if not m.get("enabled", True):
            continue
        mid = str(m.get("id") or "").strip()
        if not mid:
            continue
        out.append({"id": mid,
                    "alias": str(m.get("alias") or "").strip(),
                    "group": group_of(m)})
    return out


# 上游 /v1/models 里值得透传过来的字段。**上下文长度必须带上** —— 客户端靠它决定
# 发多长, 丢了就只能瞎猜 (Hermes 就因为拿不到而一直按 8196 发)。
UPSTREAM_META_KEYS = (
    "max_model_len", "context_length", "root",
    "supported_reasoning_efforts", "default_reasoning_effort",
)


def build_models_payload(cfg: Dict[str, Any],
                         created: Optional[int] = None) -> Dict[str, Any]:
    """OpenAI 风格的 /v1/models 响应 —— 主池 + KVMem + Strata 三组都在里面。"""
    if created is None:
        try:
            created = int(os.path.getmtime(config.CONFIG_PATH))
        except Exception:
            created = int(time.time())
    data = []
    for e in catalog(cfg):
        aliases = [e["id"]]
        if e["alias"] and e["alias"] != e["id"]:
            aliases.append(e["alias"])
        data.append({
            "id": e["id"],
            "aliases": aliases,
            "tags": [e["group"]],
            "object": "model",
            "owned_by": "llamacpp",
            "created": created,
            "status": {"value": "unloaded"},
        })
    return {"data": data, "object": "list"}


def model_node(cfg: Dict[str, Any], name: str) -> Optional[Dict[str, Any]]:
    key = (name or "").strip().lower()
    if not key:
        return None
    for e in catalog(cfg):
        if e["id"].lower() == key or (e["alias"] and e["alias"].lower() == key):
            payload = build_models_payload(cfg)
            for node in payload["data"]:
                if node["id"] == e["id"]:
                    return node
            return None
    return None


def resolve(cfg: Dict[str, Any], name: Any) -> Optional[Tuple[str, str]]:
    """请求里的 model 字段 -> (model_id, group); 认不出返回 None。"""
    if not isinstance(name, str) or not name.strip():
        return None
    key = name.strip().lower()
    for e in catalog(cfg):
        if e["id"].lower() == key:
            return e["id"], e["group"]
        if e["alias"] and e["alias"].lower() == key:
            return e["id"], e["group"]
    return None


def decode_model(body: bytes) -> Optional[str]:
    if not body:
        return None
    try:
        obj = json.loads(body.decode("utf-8", "replace"))
    except Exception:
        return None
    if isinstance(obj, dict):
        v = obj.get("model")
        if isinstance(v, str):
            return v
    return None


# ----------------------------------------------------------------------
# 小工具
# ----------------------------------------------------------------------
def http_get(url: str, timeout: float = 5.0) -> Tuple[int, bytes]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return int(r.status), r.read()
    except urllib.error.HTTPError as e:
        try:
            return int(e.code), e.read()
        except Exception:
            return int(e.code), b""
    except Exception:
        return 0, b""


def port_busy(port: int, host: str = "127.0.0.1") -> bool:
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind((host, int(port)))
        return False
    except OSError:
        return True
    finally:
        s.close()


def _rotate(path: str) -> None:
    """网关自己接管日志后没人轮转了, 起服务前超 10MB 就挪走。"""
    try:
        if os.path.exists(path) and os.path.getsize(path) > LOG_ROTATE_BYTES:
            ts = time.strftime("%Y%m%dT%H%M%S")
            os.replace(path, f"{path}.{ts}.log")
    except Exception:
        pass


def _read_text(path: str, limit: int = 4096) -> str:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read(limit)
    except Exception:
        return ""


# ----------------------------------------------------------------------
# 后端进程
# ----------------------------------------------------------------------
class Backend:
    """一组模型对应的后端进程 (只能有一个在跑)。"""

    def __init__(self, kind: str, port: int,
                 log: Optional[Callable[[str], None]] = None,
                 cfg: Optional[Dict[str, Any]] = None) -> None:
        assert kind in GROUPS, kind
        self.kind = kind
        self.port = int(port)
        self._log = log or (lambda m: None)
        self.proc: Optional[subprocess.Popen] = None
        self.model_id: Optional[str] = None      # KVMem: 正在跑的档位 id
        self._handles: List[Any] = []
        # 这一组的配置留一份给 running()/stop() 用 —— 它们被调用时拿不到 cfg。
        self.cfg: Dict[str, Any] = cfg if cfg is not None else {}

    # ---------- 描述 ----------
    @property
    def name(self) -> str:
        return GROUP_CN.get(self.kind, self.kind)

    def log_paths(self) -> Tuple[str, str]:
        import service
        if self.kind == POOL:
            return service.out_log(), service.err_log()
        if self.kind == KVMEM:
            import kvmem
            return kvmem.out_log(), kvmem.err_log()
        import strata
        return strata.out_log(), strata.err_log()

    def _command_strata(self, cfg: Dict[str, Any],
                        model: Optional[Dict[str, Any]]) -> Tuple[str, List[str],
                                                                  Dict[str, str], str]:
        """Strata 分支: 起的是它自带的 python + serve/server.py。

        它不是「一个 exe 直接接 flag」, 而是一个 HTTP 服务把参数转交给
        strata.exe —— 所以 exe 位放 python, 参数是 server.py 的。
        """
        import strata
        py = strata.find_python(cfg)
        if not py or not os.path.isfile(py):
            raise BackendError(f"未找到 Strata 的 python: {py or '(空)'}")
        if not strata.find_server(cfg):
            root = strata.install_dir(cfg)
            raise BackendError(f"未找到 serve/server.py (已找 {root or '默认目录'})")
        target = model or strata.active_model(cfg) or {}
        cfgp = strata.config_path_of(target)
        if not cfgp or not os.path.isfile(cfgp):
            raise BackendError(
                "没有可用的 Strata 档位: 请先在「🌐 Strata」页登记一个 strata-*.json"
                + (f" (当前指向 {cfgp}, 文件不存在)" if cfgp else ""))
        return py, strata.build_args(cfg, target), dict(os.environ), strata.install_dir(cfg)

    def _command(self, cfg: Dict[str, Any],
                 model: Optional[Dict[str, Any]]) -> Tuple[str, List[str],
                                                           Dict[str, str], str]:
        if self.kind == STRATA:
            return self._command_strata(cfg, model)
        if self.kind == POOL:
            llama_dir = str(cfg.get("llama_dir") or r"C:\llama.cpp").rstrip("\\/")
            exe = os.path.join(llama_dir, "llama-server.exe")
            if not os.path.isfile(exe):
                raise BackendError(f"未找到 {exe}")
            # preset 落盘可能比 config 旧, 起之前重生成一次
            try:
                import generate_preset
                generate_preset.generate(cfg)
            except Exception as e:                       # noqa: BLE001
                self._log(f"[{self.name}] 生成 router-preset.ini 失败: {e}")
            args = [
                "--models-preset", config.preset_path_for(cfg),
                "--models-max", str(cfg.get("models_max", 1)),
                "--sleep-idle-seconds", str(cfg.get("sleep_idle_seconds", 3600)),
                "--host", "127.0.0.1",
                "--port", str(self.port),
            ]
            return exe, args, dict(os.environ), llama_dir

        import kvmem
        exe = kvmem.find_exe(cfg)
        if not exe or not os.path.isfile(exe):
            root = (cfg.get("kvmem") or {}).get("install_dir", "")
            raise BackendError(f"未找到 llama-kvmem-server.exe (已找 {root or '默认目录'})")
        target = model or kvmem.active_model(cfg) or {}
        if not str(target.get("model") or "").strip():
            raise BackendError("没有 KVMem 档位: 请先在「⚡ KVMem」页添加/选中一个档位")
        # 后端只绑回环: 复制一份 cfg 改 host/port, 不动真实配置
        c2 = dict(cfg)
        c2["host"] = "127.0.0.1"
        c2["port"] = self.port
        args = kvmem.build_args(c2, target)
        env = dict(os.environ)
        try:
            env.update(kvmem._env_dict(c2))              # CUDA_VISIBLE_DEVICES 等
        except Exception:
            pass
        return exe, args, env, os.path.dirname(exe)

    # ---------- 生命周期 ----------
    def running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def start(self, cfg: Dict[str, Any],
              model: Optional[Dict[str, Any]] = None) -> None:
        self.cfg = cfg
        exe, args, env, cwd = self._command(cfg, model)
        out_p, err_p = self.log_paths()
        os.makedirs(os.path.dirname(out_p), exist_ok=True)
        _rotate(out_p)
        _rotate(err_p)
        fo = open(out_p, "a", encoding="utf-8", errors="replace")
        fe = open(err_p, "a", encoding="utf-8", errors="replace")
        self._handles = [fo, fe]
        self._log(f"[{self.name}] 启动 {exe} {' '.join(args)}")
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        self.proc = subprocess.Popen(
            [exe] + args, cwd=cwd, env=env, stdout=fo, stderr=fe,
            creationflags=flags)
        self.model_id = str((model or {}).get("id") or "") or None
        if self.kind in SINGLE_MODEL_GROUPS and not self.model_id:
            # 没显式给档位时回落到该组自己记着的 active_id
            try:
                if self.kind == KVMEM:
                    mod = kvmem
                else:
                    import strata as mod
                self.model_id = str((mod.active_model(cfg) or {}).get("id") or "") or None
            except Exception:
                self.model_id = None

    def stop(self, timeout: float = 20.0) -> None:
        self.model_id = None
        proc = self.proc
        self.proc = None
        if proc is not None and proc.poll() is None:
            try:
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                               capture_output=True, timeout=15)
            except Exception:
                pass
            try:
                proc.wait(timeout=timeout)
            except Exception:
                pass
        for f in self._handles:
            try:
                f.close()
            except Exception:
                pass
        self._handles = []
        # 等端口真正放开 (父子进程可能有残留)
        deadline = time.time() + timeout
        while time.time() < deadline:
            if not port_busy(self.port):
                return
            time.sleep(0.3)
        # 还没放开: 按**端口占用者 PID** 收, 而不是按镜像名 ——
        # Strata 的后端是 python.exe, `taskkill /IM python.exe` 会把桌面版和
        # 网关自己一起杀掉。而且三个后端共用这个口, 按镜像名杀还可能误伤
        # 下一个要起的那一组。
        for pid in _port_owner_pids(self.port):
            try:
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                               capture_output=True, timeout=20)
            except Exception:
                pass
        deadline = time.time() + 10
        while time.time() < deadline and port_busy(self.port):
            time.sleep(0.3)

    def _health_says_ready(self, code: int, body: bytes) -> bool:
        """/health 200 是不是等于「能干活了」。"""
        return code == 200

    def wait_ready(self, timeout: float) -> None:
        """阻塞到 /health 明确说「好了」(见 _health_says_ready)。"""
        deadline = time.time() + max(1.0, float(timeout))
        url = f"http://127.0.0.1:{self.port}/health"
        last = ""
        while time.time() < deadline:
            if self.proc is not None and self.proc.poll() is not None:
                _, err_p = self.log_paths()
                tail = _read_text(err_p, 1500).strip().splitlines()[-8:]
                raise BackendError(
                    f"[{self.name}] 后端进程已退出 (code={self.proc.returncode})\n"
                    + "\n".join(tail))
            code, body = http_get(url, timeout=5.0)
            if self._health_says_ready(code, body):
                self._log(f"[{self.name}] 就绪 (127.0.0.1:{self.port})")
                return
            note = str(code)
            if note != last:
                last = note
                self._log(f"[{self.name}] 等待就绪中 (/health -> {note})")
            time.sleep(0.5)
        raise BackendError(
            f"[{self.name}] 等待就绪超时 ({int(timeout)}s), "
            f"后端 {url} 仍未就绪; 请看 logs\\"
            + os.path.basename(self.log_paths()[1]))


# ----------------------------------------------------------------------
# 状态机: 互斥 + 同步等待
# ----------------------------------------------------------------------
class GatewayState:
    def __init__(self, cfg_loader: Callable[[], Dict[str, Any]],
                 backends: Dict[str, Backend],
                 log: Callable[[str], None]) -> None:
        self.cfg_loader = cfg_loader
        self.backends = backends
        self._log = log
        self._lock = threading.RLock()
        self._cv = threading.Condition(self._lock)
        self.active: Optional[str] = None
        self.active_model: Optional[str] = None
        # 线程 -> [组, 是否已进入转发阶段]。enter 先记下 (未 commit),
        # ensure 切完再 commit —— 这样「正在等切换」的请求不会被当成
        # 在途请求 (否则它等切换、切换等它, 直接死锁到超时)。
        #
        # `_owners` 用**自己的一把锁**, 不用 `self._lock`: 后者会被 `ensure()`
        # 整个持有 (含最长 600s 的 wait_ready), 一旦切进去, 读 `_owners` 的
        # `/health` 就跟着卡住 —— 而 `/health` 是 UI 状态栏和监控窗口每秒轮询的,
        # 卡住的表现就是「网关没响应」。踩过: 这把锁只在这几个方法里持有
        # 微秒级时间, 所以另起一把最省事。
        # 锁序: 只允许 `self._lock` -> `_owners_lock`, 反向禁止 (否则死锁)。
        self._owners_lock = threading.Lock()
        self._owners: Dict[int, List[Any]] = {}
        self.switching = False
        self.last_error: str = ""

    # ---------- 在途请求 ----------
    def enter(self, group: Optional[str]) -> None:
        with self._owners_lock:
            self._owners[threading.get_ident()] = [group, False]

    def commit(self) -> None:
        with self._owners_lock:
            o = self._owners.get(threading.get_ident())
            if o is not None:
                o[1] = True

    def leave(self, group: Optional[str] = None) -> None:
        with self._owners_lock:
            self._owners.pop(threading.get_ident(), None)
        # notify_all 要 `self._lock`, 所以**先放掉 _owners_lock 再拿 self._lock**
        # —— 反过来就等于 self._lock -> _owners_lock, 和上面的锁序冲突
        with self._lock:
            self._cv.notify_all()

    def _committed(self) -> List[str]:
        me = threading.get_ident()
        with self._owners_lock:
            return [str(v[0]) for tid, v in self._owners.items()
                    if v[1] and tid != me]

    def _wait_inflight(self, grace: float) -> None:
        """调用方必须已持有 self._lock。只等已经进入转发阶段的请求。"""
        others = self._committed()
        if not others:
            return
        self._log(f"[网关] 等 {len(others)} 个在途请求结束 (最多 {int(grace)}s)…")
        deadline = time.time() + max(0.0, float(grace))
        while True:
            others = self._committed()
            if not others:
                return
            if time.time() >= deadline:
                self._log(f"[网关] 超时, 还有 {len(others)} 个在途请求, 强制切换")
                return
            self._cv.wait(timeout=min(1.0, max(0.05, deadline - time.time())))

    def _resolve_conflict(self, cfg: Dict[str, Any], group: str,
                          model_id: Optional[str]) -> str:
        """起新后端之前, 内部口被外部进程占着 -> 问用户怎么处理。

        返回 force / wait / reject / timeout(没人应答)。**默认走 wait**:
        宁可让这次请求排队等, 也不擅自把人家正在用的模型杀掉。
        """
        b = self.backends.get(group)
        if b is None:
            return "wait"
        cf = _port_conflict_info(b.port, group, model_id or "", cfg, self._log)
        if cf is None:
            return "wait"
        choice = resolve_port_conflict(cf, cfg, self._log,
                                       getattr(self, "conflict_ask", None))
        if choice in ("force", "wait", "reject"):
            if choice == "force":
                # 用户明确要求强行结束: 此时才按端口精确收进程
                for pid in cf["pids"]:
                    self._log(f"[网关] 你选了强行结束 -> 收掉 pid {pid}")
                    try:
                        subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                                       capture_output=True, timeout=20)
                    except Exception:                # noqa: BLE001
                        pass
                deadline = time.time() + 15
                while time.time() < deadline and port_busy(b.port):
                    time.sleep(0.3)
            elif choice == "wait":
                self._log("[网关] 你选了等它用完 -> 排在后面")
            else:
                raise BackendError(
                    f"已按你的选择放弃切换: 内部口 {cf['port']} 仍被 "
                    f"pid {cf['pids']} 占用")
        return choice

    # ---------- 切换 ----------
    def _wait_gpu_free(self, cfg: Dict[str, Any], group: str,
                       model_id: Optional[str],
                       b: "Backend") -> bool:
        """等占着内部口的那个模型自己腾出显存。

        判据在 `gpu_busy` (显存 + GPU 利用率双重):
          * 显存掉 >= 50%  -> 对方主动关了, **立刻可切**
          * 否则 GPU 空闲满 grace 秒 -> 到点自动切

        返回 True 表示「可以切了」。等不到也不强杀 —— 后面的
        `_resolve_conflict` 会去问用户。
        """
        if not port_busy(b.port):
            return True
        gw = cfg.get("gateway") or {}
        try:
            import gpu_busy
            grace = float(gw.get("gpu_idle_grace",
                                 gpu_busy.IDLE_GRACE) or gpu_busy.IDLE_GRACE)
            w = gpu_busy.BusyWatch(log=self._log, idle_grace=grace,
                                   interval=float(gw.get("gpu_sample_seconds", 5) or 5),
                                   idle_samples=int(gw.get("gpu_idle_samples", 3) or 3),
                                   gpu_idle_pct=int(gw.get("gpu_idle_pct", 20) or 20),
                                   vram_drop_ratio=float(gw.get("vram_drop_ratio", 0.5) or 0.5))
            w.arm()
        except Exception as e:                          # noqa: BLE001
            self._log(f"[网关] 起不了 GPU 忙碌判据({e}), 交给用户决定")
            return False
        try:
            deadline = time.time() + grace + 15.0
            while time.time() < deadline:
                if not port_busy(b.port):
                    self._log("[GPU] 内部口已经空了")
                    return True
                why = w.ready()
                if why:
                    self._log(f"[GPU] {why} -> 可以切 (显存/GPU: {w.describe()})")
                    return True
                self._cv.wait(timeout=1.0)
            self._log(f"[GPU] 等了 {int(grace)}s 还没腾出来 "
                      f"({w.describe()}) -> 问你怎么处理")
            return False
        finally:
            w.stop()

    def ensure(self, group: Optional[str], model_id: Optional[str],
               path: str = "") -> str:
        """保证 group 这一组的后端在跑且档位正确 (阻塞到就绪), 返回最终组。"""
        cfg = self.cfg_loader()
        gw = cfg.get("gateway") or {}
        inflight_grace = float(gw.get("inflight_grace", 120) or 120)

        with self._lock:
            if group is None:
                # 没带 model / 认不出 -> 交给当前正在跑的后端。
                # 必须在这里(锁内)解析, 才能看到切换完成后的 active。
                # **按需加载模式下 active 一开始是 None**(开机不加载任何
                # 模型), 这时退回 serve_mode 指定的那一组, 而不是无脑主池 ——
                # 否则「默认用 KVMem」的人发一个不带 model 的请求, 会被
                # 悄悄拉起主池。
                group = self.active or default_group(cfg)
                model_id = None
            # 路径级兜底: router 管理接口只该由主池回答 (KVMem 是单模型进程,
            # 没有 /models 语义); 主池没在跑就照转, 让后端自己回 404。
            if path.startswith("/models") or path in ("/slots", "/props", "/metrics"):
                if group in SINGLE_MODEL_GROUPS and self.active == group:
                    group = POOL if self.backends[POOL].running() else group
            if group not in GROUPS:
                raise BackendError(f"未知模型组: {group}")
            owner = self._owners.get(threading.get_ident())
            if owner is not None:
                with self._owners_lock:
                    owner = self._owners.get(threading.get_ident())
                if owner is not None:
                    owner[0] = group       # 修正 in-flight 记录里的组名
            b = self.backends[group]
            same = (self.active == group and b.running()
                    and (group == POOL or not model_id or b.model_id == model_id))
            if same:
                return group
            self.switching = True
            try:
                self._wait_inflight(inflight_grace)
                # 互斥: 先停另一个组。KVMem 换档位还得把**自己**也停掉 ——
                # 单模型进程改不了已经跑起来的档位, 只能重启 (不停就 start
                # 会开出第二个进程, 端口直接被自己占着)。
                restart_self = (group in SINGLE_MODEL_GROUPS and b.running()
                                and bool(model_id) and b.model_id != model_id)
                for g, other in self.backends.items():
                    if g == group and not restart_self:
                        continue
                    if other.running() or self.active == g:
                        self._log(f"[网关] 切换: 停掉 {other.name}")
                        other.stop()
                target = model_id
                model_entry = None
                if group == KVMEM:
                    model_entry = self._kvmem_entry(cfg, model_id)
                    target = str(model_entry.get("id") or model_id or "")
                elif group == STRATA:
                    model_entry = self._strata_entry(cfg, model_id)
                    target = str(model_entry.get("id") or model_id or "")
                self._log(f"[网关] 切换到 {b.name}"
                          + (f" / {target}" if target else ""))
                # 内部口被**外部进程**占着(桌面端自己加载的模型 / 上轮残留)
                # -> 停下来问用户, 不直接抢。强杀会把人家正在进行的对话打断,
                # 这正是要避免的。
                # 内部口被别的进程 (桌面端自己加载的模型 / 上轮残留) 占着:
                # 先用「显存 + GPU」双重判据等它腾出来 —— 对方主动关了模型
                # (显存掉一半) 就立刻切, 否则 GPU 空闲满 grace 秒也自动切。
                # 腾不出来才问用户, **绝不直接抢**。
                self._wait_gpu_free(cfg, group, target, b)
                self._resolve_conflict(cfg, group, target)
                b.start(cfg, model_entry)
                b.wait_ready(self._ready_timeout(cfg, group))
                self.active = group
                self.active_model = b.model_id
                self.last_error = ""
                self._persist_serve_mode(group)
            except Exception as e:                      # noqa: BLE001
                self.last_error = str(e)
                try:
                    self.backends[group].stop()
                except Exception:
                    pass
                if self.active == group:
                    self.active = None
                    self.active_model = None
                raise
            finally:
                self.switching = False
                self._cv.notify_all()
            return group

    def _kvmem_entry(self, cfg: Dict[str, Any],
                     model_id: Optional[str]) -> Dict[str, Any]:
        for m in cfg.get("models", []) or []:
            if group_of(m) != KVMEM:
                continue
            if model_id and str(m.get("id") or "") == model_id:
                return m
        if model_id:
            raise BackendError(f"KVMem 档位不存在: {model_id}")
        import kvmem
        entry = kvmem.active_model(cfg)
        if not entry:
            raise BackendError("没有 KVMem 档位: 请先在「⚡ KVMem」页添加/选中一个档位")
        return entry

    def _ready_timeout(self, cfg: Dict[str, Any], group: str) -> float:
        """切到某组时等它就绪的秒数。

        Strata 要把 35-55GB 拉到内存, 1-3 分钟起步, 拿主池那 300s 去卡它
        会误判成「起不来」—— 所以它有自己更大的超时。
        """
        gw = cfg.get("gateway") or {}
        if group == STRATA:
            st = cfg.get("strata") or {}
            try:
                return float(st.get("ready_timeout", 900) or 900)
            except (TypeError, ValueError):
                return 900.0
        try:
            return float(gw.get("ready_timeout", 300) or 300)
        except (TypeError, ValueError):
            return 300.0

    def _strata_entry(self, cfg: Dict[str, Any],
                      model_id: Optional[str]) -> Dict[str, Any]:
        import strata
        for m in cfg.get("models", []) or []:
            if group_of(m) != STRATA:
                continue
            if model_id and str(m.get("id") or "") == model_id:
                return m
        if model_id:
            raise BackendError(f"Strata 档位不存在: {model_id}")
        entry = strata.active_model(cfg)
        if not entry:
            raise BackendError(
                "没有 Strata 档位: 请先在「🌐 Strata」页登记一个 strata-*.json")
        return entry

    def _persist_serve_mode(self, group: str) -> None:
        """把「当前活跃组」写回 config.serve_mode, 让 UI 状态栏跟得上。"""
        try:
            want = group if group in SINGLE_MODEL_GROUPS else "router"
            cur = config.load()
            if str(cur.get("serve_mode") or "router") != want:
                cur["serve_mode"] = want
                config.save(cur)
        except Exception:
            pass

    def unload(self, group: str) -> None:
        """/models/unload 由网关代答: KVMem / Strata 都是单模型进程,
        释放显存/内存只能整个停掉。停完没有活跃后端了, serve_mode 记回
        router (下次从主池起)。"""
        with self._lock:
            if group not in SINGLE_MODEL_GROUPS or self.active != group:
                return
            b = self.backends[group]
            if b.running():
                self._log(f"[网关] 卸载: 停掉 {b.name} 后端 (单模型进程)")
                b.stop()
            self.active = None
            self.active_model = None
            self._persist_serve_mode(POOL)

    def snapshot(self) -> Dict[str, Any]:
        # **不加锁**: /health 是 UI 状态栏和监控窗口每秒轮询的, 而切换/加载
        # 期间 ensure() 会握着锁不放 (Strata 要 1-3 分钟) —— 上了锁这里就
        # 跟着一起卡住, 用户看到的是「网关没响应」。这里读的都是普通属性,
        # 最多读到切换中的中间态, 而 switching 字段本来就说明「正在切」。
        counts = {g: 0 for g in GROUPS}
        for g in self._committed():
            if g in counts:
                counts[g] += 1
        # 按**实际注册的后端**报, 不按 GROUPS —— 测试会注入只含两组的
        # 假后端表, 按 GROUPS 取会 KeyError 把 /health 打成 500
        states = {g: ("running" if b.running() else "stopped")
                  for g, b in self.backends.items()}
        # active 只在**那一组真的在跑**时才报: 按需加载下后端可能已经
        # 停了, 还报着 active 会让 UI 显示「运行时 主池」而其实什么都没
        # 加载 (self.active 内部还要留着, 切换时要用)
        live = self.active if states.get(self.active) == "running" else None
        return {
            "active": live,
            "active_model": self.active_model if live else None,
            "switching": self.switching,
            "inflight": counts,
            "last_error": self.last_error,
            "backends": states,
        }


# ----------------------------------------------------------------------
# 网关应用 (把状态机 + HTTP 细节凑在一起, 便于测试注入假后端)
# ----------------------------------------------------------------------
class GatewayApp:
    def __init__(self, cfg_loader: Optional[Callable[[], Dict[str, Any]]] = None,
                 backends: Optional[Dict[str, Backend]] = None,
                 log: Optional[Callable[[str], None]] = None) -> None:
        self.cfg_loader = cfg_loader or config.load
        self.log = log or self._default_log
        cfg = self.cfg_loader()
        gw = cfg.get("gateway") or {}
        if backends is None:
            # 后端共用一个内部口 (见 backend_port_of)。它们互斥, 不会
            # 同时占着, 所以共用不会撞 —— 万一真撞上了 (比如上一轮的残留),
            # _free_stale_backends 会按端口占用者精确清理。
            shared = backend_port_of(cfg, POOL)
            backends = {
                POOL: Backend(POOL, shared, self.log),
                KVMEM: Backend(KVMEM, backend_port_of(cfg, KVMEM), self.log),
                STRATA: Backend(STRATA, backend_port_of(cfg, STRATA), self.log),
            }
        self.backends = backends
        self.state = GatewayState(self.cfg_loader, self.backends, self.log)
        # 聚合 /v1/models 要问后端三遍 (/health 状态 + /v1/models 元数据 +
        # /v1/stats 的 KV 容量)。网页版和监控都在高频轮询这个接口, 不缓存就是
        # 1 : 3 放大 —— 实测能把引擎日志刷成每 2 秒一条 `GET /v1/models 200 OK`,
        # 每条源端口还都不同, 看着像在「不停换端口调模型」。
        # 这些值只随加载/卸载变, 2 秒 TTL 足够; 切档时会显式清掉。
        self._up_cache: Dict[str, Tuple[float, Any]] = {}
        self._up_cache_lock = threading.Lock()

    def _upstream(self, port: int, path: str, timeout: float = 3.0) -> Any:
        """打后端一次, 带 2s TTL 缓存。失败也缓存 (免得对端不通时反复超时)。"""
        key = f"{port}{path}"
        now = time.monotonic()
        with self._up_cache_lock:
            hit = self._up_cache.get(key)
            if hit is not None and now - hit[0] < 2.0:
                return hit[1]
        try:
            code, raw = http_get(f"http://127.0.0.1:{port}{path}", timeout=timeout)
            obj = None
            if code == 200:
                parsed = json.loads(raw.decode("utf-8", "replace"))
                obj = parsed if isinstance(parsed, dict) else {"data": parsed}
            else:
                obj = {"__code__": code}
        except Exception:                                # noqa: BLE001
            obj = None
        with self._up_cache_lock:
            self._up_cache[key] = (now, obj)
        return obj

    def invalidate_upstream(self) -> None:
        with self._up_cache_lock:
            self._up_cache.clear()

    @staticmethod
    def _default_log(msg: str) -> None:
        line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}"
        sys.stderr.write(line + "\n")
        sys.stderr.flush()
        # 镜像到 router.err.log: UI 的「实时监控」和远程 log_relay 只盯
        # router.*.log 这两个文件, 网关的切换过程也要在那里看得见。
        try:
            import service
            with open(service.err_log(), "a", encoding="utf-8",
                      errors="replace") as f:
                f.write(line + "\n")
        except Exception:
            pass

    # ---------- 响应体 ----------
    def health_payload(self) -> Dict[str, Any]:
        cfg = self.cfg_loader()
        snap = self.state.snapshot()
        return {
            "status": "ok",
            "role": "gateway",
            "host": cfg.get("host", "0.0.0.0"),
            "port": int(cfg.get("port", 8080) or 8080),
            "active": snap["active"],
            "active_model": snap["active_model"],
            "switching": snap["switching"],
            "inflight": snap["inflight"],
            "backends": snap["backends"],
            "last_error": snap["last_error"],
            "models": len(catalog(self.cfg_loader())),
            # False = 按需加载(默认): 现在没加载任何模型, 带 model 的请求
            # 来了才加载对应那一组
            "preload": preload_of(cfg),
        }

    def models_payload_cached(self) -> Tuple[bytes, str]:
        """聚合目录 -> (序列化后的字节, ETag)。带 TTL, 命中时不碰后端。

        为什么: 网页版(PWA) 会**每秒轮询** /models 和 /health, 停不下来 ——
        那是浏览器行为, 服务端控制不了。所以只能让每次轮询便宜到可以忽略:
        这里把整份 JSON 序列化结果缓存住, 命中时连 `build_models_payload` 都不跑。

        ETag 给客户端一个「用你手上那份就行」的出口: 条件请求回 304, body 是
        空的 —— 浏览器真会尊重的话就更省了 (llama.cpp 网页版目前不尊重, 但
        别的客户端会)。
        """
        now = time.monotonic()
        # 缓存键 = (时刻, 活跃组, 活跃档位)。**档位也要算进去** —— 同一组内
        # 换档位时组名不变, 只按组清缓存就会把切档前的状态发给客户端。
        key = (self.state.active, self.state.active_model)
        hit = getattr(self, "_mp_cache", None)
        if (hit is not None and hit[0] is not None and now - hit[0] < float(
                (self.cfg_loader().get("gateway") or {}).get("catalog_ttl", 3.0) or 3.0)
                and hit[3] == key):
            return hit[1], hit[2]
        payload = self.models_payload()
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        etag = '"%x-%x"' % (int(time.time()), len(raw))
        self._mp_cache = (now, raw, etag, key)
        return raw, etag

    def models_payload(self) -> Dict[str, Any]:
        """三组都列出来; 当前后端的真实 status 尽力合进来 (失败不影响)。"""
        cfg = self.cfg_loader()
        # 切档了就别用缓存 —— 缓存里还是上一个后端的口径, 会把 loaded/unloaded
        # 和上下文长度报错到客户端身上。按活跃组分桶, 比挂在切档路径上可靠。
        group = self.state.active
        if group != getattr(self, "_up_cache_group", None):
            self._up_cache_group = group
            self.invalidate_upstream()
            self._mp_cache = None      # 档位变了, 序列化缓存一并作废
        payload = build_models_payload(cfg)
        if not group:
            return payload
        b = self.backends.get(group)
        if b is None or not b.running():
            return payload
        if group == STRATA:
            self._merge_strata_status(payload, b, cfg)
            return payload
        try:
            code, body = http_get(f"http://127.0.0.1:{b.port}/v1/models", timeout=3.0)
            if code != 200:
                return payload
            upstream = {str(n.get("id")): n
                        for n in (json.loads(body.decode("utf-8", "replace"))
                                  .get("data") or []) if isinstance(n, dict)}
            for node in payload["data"]:
                src = upstream.get(node["id"])
                if not src:
                    continue
                for key in ("status", "architecture") + UPSTREAM_META_KEYS:
                    if key in src:
                        node[key] = src[key]
        except Exception:
            pass
        return payload

    def _upstream_index(self, port: int, path: str) -> Dict[str, Dict[str, Any]]:
        """上游 /v1/models -> {id: node}, 带 TTL 缓存 (见 self._upstream)。"""
        obj = self._upstream(port, path)
        if not isinstance(obj, dict):
            return {}
        return {str(n.get("id")): n
                for n in (obj.get("data") or []) if isinstance(n, dict)}

    def _merge_strata_status(self, payload: Dict[str, Any],
                             b: "Backend",
                             cfg: Optional[Dict[str, Any]] = None) -> None:
        """Strata 没有 /v1/models —— 用它自己的 /health 合成状态。

        serve/server.py 的 /health 里有 `loaded`, 正好对上 Web UI 认的
        loaded / unloaded, 免得模型明明在显存里却被前端判成「不可用」。
        """
        obj = self._upstream(b.port, "/health")
        if not isinstance(obj, dict) or "__code__" in obj:
            return
        value = "loaded" if obj.get("loaded") else "unloaded"
        # **只给「真正在跑的那个」打 loaded**, 不能把同一个状态套给所有 Strata
        # 档位 —— Strata 是单模型进程, 同一时刻只可能有一个 loaded。旧实现对
        # 所有 tags==[strata] 的条目一律赋同一个值, 于是登记了两个档位时
        # 两个都显示「激活」, 而实际只有一个在显存里。
        try:
            import strata
            act = strata.active_model(cfg if cfg is not None
                                      else self.cfg_loader())
            active_id = str((act or {}).get("id") or "")
        except Exception:                            # noqa: BLE001
            active_id = ""
        # 后端自报的 loaded 才是真相; 拿不到 active_id 时退回「全部 unloaded」
        # —— 宁可都不标, 也不能谎报两个都在跑。
        for node in payload["data"]:
            if node.get("tags") != [STRATA]:
                continue
            mid = str(node.get("id") or "")
            on = value == "loaded" and bool(active_id) and mid == active_id
            node["status"] = {"value": "loaded" if on else "unloaded",
                              "args": []}

    def model_payload(self, name: str) -> Optional[Dict[str, Any]]:
        """单个模型 (Web UI 会按 id 单查一条)。

        必须走 models_payload() 把当前后端的真实 status 合进来 —— 只用
        config 合成的话 status 恒为 unloaded, 已经进显存的模型在前端
        也会被判成「不可用」。
        """
        key = (name or "").strip().lower()
        if not key:
            return None
        for node in self.models_payload()["data"]:
            if str(node.get("id") or "").lower() == key:
                return node
            if any(str(a).lower() == key for a in node.get("aliases") or []):
                return node
        return None

    # ---------- 转发 ----------
    def fallback_props(self) -> Dict[str, Any]:
        """后端没有 /props 时 (Strata) 自己合成一份。

        只带前端真正会读的字段: role 必须是 router (否则 detectRole() 切
        单模型界面), build_info 留空字符串 —— 前端拿它做「有新版本」比较,
        空值不会误报。
        """
        return {
            "role": "router",
            "build_info": "",
            "max_instances": 1,
            "models_autoload": True,
            "model_path": "none",
            "default_generation_settings": {"params": None, "n_ctx": 0},
            "ui_settings": {},
        }

    def route(self, body: bytes) -> Tuple[Optional[str], Optional[str], Optional[str]]:
        """请求 -> (group, model_id, 原始 model 字段)。

        认不出 (没带 model / 不在清单里) 时 group 返回 None, 由 ensure()
        在**拿到锁之后**再解析成「当前活跃组」—— 不能在这里用 state.active,
        否则别人正在切换时, 一个无 model 的轮询会拿着旧值把后端再切回去。
        """
        name = decode_model(body)
        hit = resolve(self.cfg_loader(), name)
        if hit:
            model_id, group = hit
            return group, model_id, name
        return None, None, name

    def forward(self, handler: "GatewayHandler", group: str,
                body: bytes) -> None:
        """把请求原样转给 group 的后端 (在途计数由调用方负责)。"""
        b = self.backends[group]
        conn = http.client.HTTPConnection("127.0.0.1", b.port,
                                          timeout=UPSTREAM_TIMEOUT)
        try:
            headers = {k: v for k, v in handler.headers.items()
                       if k.lower() not in FORWARD_STRIP}
            headers["Host"] = f"127.0.0.1:{b.port}"
            conn.request(handler.command, handler.path,
                         body=body if body else None, headers=headers)
            resp = conn.getresponse()
            handler.send_response_to_backend(resp)
        finally:
            conn.close()


# ----------------------------------------------------------------------
# HTTP
# ----------------------------------------------------------------------
class GatewayHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "llama-router"
    sys_version = ""

    # ---- 基础 ----
    @property
    def app(self) -> GatewayApp:
        return self.server.app          # type: ignore[attr-defined]

    def log_message(self, fmt: str, *args: Any) -> None:      # 不做反向 DNS
        sys.stderr.write('%s - - [%s] %s\n'
                         % (self.client_address[0], self.log_date_time_string(),
                            fmt % args))
        sys.stderr.flush()

    # ---- 入口 ----
    def do_GET(self) -> None:
        self._handle()

    def do_POST(self) -> None:
        self._handle()

    def do_PUT(self) -> None:
        self._handle()

    def do_DELETE(self) -> None:
        self._handle()

    def do_PATCH(self) -> None:
        self._handle()

    def do_HEAD(self) -> None:
        self._handle()

    def do_OPTIONS(self) -> None:
        self._handle()

    def _handle(self) -> None:
        path = urlparse(self.path).path
        try:
            if path in ("/health", "/healthz"):
                return self._send_json(200, self.app.health_payload())
            if path == "/v1/models" and self.command == "GET":
                return self._send_catalog_cached()
            if path.startswith("/v1/models/") and self.command == "GET":
                name = unquote(path[len("/v1/models/"):])
                node = self.app.model_payload(name)
                if node is None:
                    return self._send_json(404, {
                        "error": {"message": f"model not found: {path}",
                                  "type": "invalid_request_error"}})
                return self._send_json(200, node)
            # 老接口 /models: KVMem 单模型进程没有它 (404), 网关按同一份
            # 目录答 —— 两组模型都列得出来。
            if path == "/models" and self.command == "GET":
                return self._send_catalog_cached()
            # Strata 活跃: 它自己的接口原样转发, 网关不合成、
            # 不改写。 (它没有的接口就让它的 404 说话, 别替它编一个)
            if self.app.state.active in PASSTHROUGH_GROUPS:
                if path == "/models/sse":
                    # Strata 不是 llama-server, **没有 /models/sse**,
                    # 实测恒 404。而浏览器的 EventSource 碰到 404 会当成
                    # 「流挂了」立刻重连 -> 每 2 秒一次的死循环 (实测 0.5 次/秒),
                    # 把引擎访问日志刷成 `GET /models/sse 404`, 每行还带个不同的
                    # 临时源端口, 看着像在不停换端口调模型。
                    # **204 是 EventSource 规范里「永久别再连」的状态码** ——
                    # 客户端收到就彻底停止重连, 噪声从源头消失, 也不用我们去编
                    # 一条假的 SSE 事件流 (那会让前端以为真的有状态更新)。
                    code, raw, ctype = self._fetch_from_backend(
                        self.app.state.active, None)
                    if code == 200:
                        return self._send_raw(code, ctype, raw)
                    return self._send_no_content()
                # /props 是**例外**, 不能透传 404:
                #   1) 这个后端不是 llama-server, 压根没有 /props (实测 Strata
                #      的 /props 和 /models/sse 都回 404)
                #   2) 网页版把 `/props?model=...&autoload=false` 当成「服务起没起」
                #      的探针, 拿到 404 就显示「服务未启动」, 然后**每秒几十次**地
                #      重试 (实测一分钟几千条, 把访问日志刷爆)
                #   3) detectRole() 只认 role=router, 不然退化成单模型界面
                if path == "/props" or path.startswith("/props?"):
                    return self._handle_props_passthrough(path)
            if path == "/props" and self.command in ("GET", "POST"):
                return self._handle_props(path)
            # Hermes 这类客户端还会探 /v1/props 和 Ollama 风格的 /api/tags。
            # Strata 自己**没有**这两个端点 (实测直接 404), 所以日志里会出现
            # 一串 404 探测。它们不是错误, 但客户端可能因为拿不到而一直等 ——
            # 聚合层本来就要把后端差异吃掉, 这里用 /v1/models 的数据合成一份。
            if path == "/v1/props" and self.command == "GET":
                return self._handle_props(path)
            if path == "/api/tags" and self.command == "GET":
                return self._handle_api_tags()
            # /status: 监控窗口要显示「当前在 prefill 还是生成、第几个 token、
            # 多少 tok/s」。Strata 有 /status, llama-server(KVMem/主池)没有。
            # 网关统一答: 后端有就透传, 没有就按 health + 在途请求合成一份,
            # 保证监控那边永远拿得到结构一致的字段。
            if path == "/status" and self.command == "GET":
                return self._handle_status()
            if path == "/models/sse" and self.command == "GET":
                return self._handle_models_sse()
            self._proxy(path)
        except BackendError as e:
            self._send_json(503, {"error": {"message": str(e),
                                            "type": "server_error"}})
        except ConnectionError:
            # 客户端把连接掐了 (关标签页 / 刷新 / 前端自己重连 SSE) —— 这是
            # 常态, 不是服务器错误。必须在这里挡住, 否则会当成 500 写进访问日志
            # 并往 stderr 吐一整段 traceback (/models/sse 每次重连都会刷一屏)。
            self.close_connection = True
        except Exception as e:                              # noqa: BLE001
            # 500 一定要留下痕迹, 否则线上只看到一条孤零零的访问日志
            sys.stderr.write("%s - - [%s] %s %s -> 500 %s\n"
                             % (self.client_address[0], self.log_date_time_string(),
                                self.command, self.path, traceback.format_exc()))
            sys.stderr.flush()
            try:
                self._send_json(500, {"error": {"message": f"{type(e).__name__}: {e}",
                                                "type": "server_error"}})
            except Exception:
                self.close_connection = True

    # ---- 转发 ----
    def _proxy(self, path: str) -> None:
        body = self._read_body()
        group, model_id, _name = self.app.route(body)
        st = self.app.state
        if model_id is None and not any(b.running() for b in st.backends.values()):
            # 一个后端都没跑时: 只有「点名了要哪个模型」的请求才允许去加载。
            # 没带 model 的, 只读/静态的照合成, 其它 (比如真正要生成了却
            # 忘了写 model) 回 503 让人改 —— 猜错就是白拉 40GB。
            # 已经有后端在跑就不走这里: 照常 ensure (同一组会立刻返回) +
            # 计入在途, 转发语义跟以前完全一样。
            if is_no_load_path(path) or not preload_of(self.app.cfg_loader()):
                return self._answer_without_loading(path)
        st.enter(group)                 # 先占位: 别的线程切换时会等我
        try:
            if path == "/models/unload" and group in SINGLE_MODEL_GROUPS:
                # 单模型进程没有 /models/unload: 释放显存/内存只能整个
                # 停掉。**不能**先 ensure —— 它可能根本没在跑, 不该为了卸载
                # 又把它拉起来 (拉起来要 1-3 分钟, 而用户只是想腾地方)。
                st.unload(group)
                return self._send_json(200,
                                       {"status": "ok",
                                        "model": model_id or ""})
            # 组的解析 / 管理接口兜底 / 切换都在锁内完成, 避免拿到过期的
            # active 把后端切回去 (见 route() 的说明)
            group = st.ensure(group, model_id, path)
            if path == "/models/load" and group in SINGLE_MODEL_GROUPS and model_id:
                # 单模型进程没有这个接口 (Strata 只有 /v1/load, 且它要求
                # 请求来自自己的页面), 但切换已经做完了
                st.commit()
                return self._send_json(200, {"status": "ok", "model": model_id})
            if self.command not in ("GET", "HEAD", "OPTIONS"):
                # 只有带请求体的写/生成才算「在途」。浏览器挂着的常驻读
                # (如 llama-server 的 /models/SSE) 掐了客户端会自己重连,
                # 没必要让每次切换都先空等 inflight_grace 再强切。
                st.commit()
            self.app.forward(self, group, body)
        finally:
            st.leave(group)

    def _answer_without_loading(self, path: str) -> None:
        """一个后端都没在跑时, 怎么回答这个「不带 model」的请求。

        目录/角色类的由网关自答 (那是网关自己的聚合入口), 静态资源回 404,
        剩下的 (真的要生成却没写 model) 回 503 并说清要带什么 ——
        宁可报错也不要替用户猜一个 40GB 的模型加载起来。
        """
        p = (path or "").split("?", 1)[0]
        if p in ("/health", "/healthz"):
            return self._send_json(200, self.app.health_payload())
        if p in ("/v1/models", "/models"):
            return self._send_catalog_cached()
        if p == "/props":
            return self._send_json(200, self.app.fallback_props())
        if p == "/models/sse":
            return self._handle_models_sse()
        if looks_like_asset(p):
            # 静态资源: 后端没在跑就没有这些文件。回 404 好过为了取一张图标
            # 去加载一个模型 (浏览器会立刻重试, 变成死循环)
            return self._send_json(404, {
                "error": {"message": "no backend loaded",
                          "type": "not_found_error"}})
        return self._send_json(503, {
            "error": {
                "message": ("当前没有已加载的模型: 请在请求里带上 model 字段"
                            "(例如 {\"model\": \"<档位名>\"}), "
                            "网关会按需加载对应的那一组"),
                "type": "server_error",
            }})

    def _read_body(self) -> bytes:
        te = (self.headers.get("Transfer-Encoding") or "").lower()
        if "chunked" in te:
            chunks: List[bytes] = []
            while True:
                line = self.rfile.readline(65536).strip()
                if b";" in line:
                    line = line.split(b";", 1)[0]
                try:
                    n = int(line, 16)
                except ValueError:
                    break
                if n <= 0:
                    while True:
                        t = self.rfile.readline(65536)
                        if t in (b"\r\n", b"\n", b""):
                            break
                    break
                chunks.append(self.rfile.read(n))
                self.rfile.read(2)
            return b"".join(chunks)
        raw = self.headers.get("Content-Length")
        if not raw:
            return b""
        try:
            n = int(raw)
        except ValueError:
            return b""
        if n <= 0:
            return b""
        if n > MAX_BODY:
            raise BackendError(f"请求体过大 ({n} bytes)")
        return self.rfile.read(n)

    # ---- 网关自己端的接口 ----
    def _fetch_from_backend(self, group: str, body: bytes
                            ) -> Tuple[int, bytes, str]:
        """整包转发 (需要网关改写响应体的接口), 不走流式。"""
        b = self.app.backends[group]
        conn = http.client.HTTPConnection("127.0.0.1", b.port,
                                          timeout=UPSTREAM_TIMEOUT)
        try:
            headers = {k: v for k, v in self.headers.items()
                       if k.lower() not in FORWARD_STRIP}
            headers["Host"] = f"127.0.0.1:{b.port}"
            headers["Accept-Encoding"] = "identity"   # 网关要改体, 别用压缩
            conn.request(self.command, self.path,
                         body=body if body else None, headers=headers)
            resp = conn.getresponse()
            return resp.status, resp.read() or b"", \
                resp.getheader("Content-Type") or ""
        finally:
            conn.close()

    def _handle_props_passthrough(self, path: str) -> None:
        """活跃组是 Strata 时的 `/props`。

        分两种情况, **别弄混**:

        - 上游答得上 (Strata 自己有 /props) -> **一字不改地透传, 连 role 都不许
          注入**。这是 §23 修过的坑: 注入 `role=router` 会让浏览器里开着的
          llama.cpp 网页版以为后面真有个 router, 于是去轮询 /tools、/build.json
          这些只有 llama-server 才有的接口 -> 全部 404 -> 网页版报错。
        - 上游答不上 (Strata 不是 llama-server, /props 恒 404) -> 自己合成一份
          带 role=router 的。**404 绝不能外泄**: 网页版拿
          `/props?model=...&autoload=false` 当「服务起没起」的探针, 404 会被判成
          「服务未启动」, 然后每秒几十次地重试 (实测一分钟几千条)。

        **这里绝不切档。** `autoload=false` 的意思就是「别给我加载」: 网页版为了把
        模型下拉框列全, 会挨个组问 `/props?model=...`, 一问就切档等于自己跟���己
        打架 (刚点开就发现档位被探针切走了)。真要加载走 /v1/chat/completions 或
        /models/load, 那里的 model 字段才是真意图。
        """
        group = self.app.state.active
        b = self.app.backends.get(group) if group else None
        if b is not None:
            try:
                code, raw, ctype = self._fetch_from_backend(group, None)
                if code == 200:
                    return self._send_raw(code, ctype, raw)   # 原样, 不改写
            except Exception:                                # noqa: BLE001
                pass
        return self._send_json(200, self.app.fallback_props())

    def _handle_status(self) -> None:
        """`GET /status` —— 给监控窗口的实时进度。

        Strata 自己有 `/status` (state/phase/prompt_tokens/generated/tok_s...),
        直接透传。KVMem / 主池是 llama-server, **没有** `/status`, 透传会 404;
        这里按 health + 当前模型合成一份字段齐全的空壳, 让监控永远拿得到同一套
        字段, 不用为每个后端写分支。
        """
        group = self.app.state.active
        try:
            code, raw, _ct = self._fetch_from_backend(group, None)
        except Exception:
            code, raw = 0, b""
        if code == 200 and raw:
            try:
                data = json.loads(raw.decode("utf-8"))
                data.setdefault("backend", group)
                return self._send_json(200, data)
            except Exception:
                pass
        # 后端没有 /status: 合成一份, 字段名和 Strata 对齐。
        health = self.app.health_payload()
        model = None
        try:
            for n in (self.app.models_payload().get("data") or []):
                if str(n.get("id")):
                    model = str(n.get("id"))
                    break
        except Exception:
            pass
        loaded = str(health.get("status", "")) == "ok"
        self._send_json(200, {
            "backend": group,
            "state": "idle" if loaded else "unloaded",
            "queued": 0,
            "phase": None,
            "model": model,
            "prompt_tokens": None,
            "prompt_total": None,
            "generated": None,
            "max_tokens": None,
            "elapsed_s": None,
            "tok_s": None,
            "tok_s_mean": None,
            "prefill_tok_s_mean": None,
        })

    def _handle_api_tags(self) -> None:
        """`GET /api/tags` —— Ollama 风格的模型列表。

        Strata 没有这个端点 (直接 404), 但 Hermes 这类客户端会拿它
        当**兼容性探测**: 探不到就可能一直等。聚合层的职责就是把后端差异在
        这里吃掉, 所以用同一份 /v1/models 数据合成 Ollama 的 schema。
        """
        try:
            payload = self.app.models_payload()
        except Exception:
            self._send_json(503, {"error": {"message": "catalog unavailable",
                                            "type": "server_error"}})
            return
        models = []
        for n in (payload.get("data") or []):
            mid = str(n.get("id") or "")
            if not mid:
                continue
            models.append({
                "name": mid,
                "model": mid,
                "size": 0,
                "digest": "",
                "modified_at": "",
                # Hermes 只看有没有 / 名字对不对, 这两个字段给它个合理默认
                "details": {
                    "parent_model": "",
                    "format": "gguf",
                    "family": "",
                    "families": [],
                    "parameter_size": "",
                    "quantization_level": "",
                },
            })
        self._send_json(200, {"models": models})

    def _handle_props(self, path: str) -> None:
        """/props —— 网关只在**主池 / KVMem** 这两组的口径上改写 role。

        Strata 活跃时压根不会走到这里 (见 PASSTHROUGH_GROUPS): 它自己就有
        /props, 原样转发才是对的。

        llama-server 只有在 --models-preset (router 模式) 下才自报
        role="router"; KVMem 是单模型进程, 自报 role="model"。前端
        detectRole() 一看不是 router 就切「单模型界面」: getDisplayOption()
        只回当前这一个模型, 模型选择器自然就没有别的可选 —— 线上现象
        「选了 KVMem 档位加载后网页版只剩这一个模型」就是这么来的。
        网关对外永远是**所有组**模型的目录, 所以这里必须报 router。
        """
        body = self._read_body()
        group, model_id, _ = self.app.route(body)
        st = self.app.state
        # 按需加载: 一个后端都没跑就别为了 /props 去加载一个模型 ——
        # 网页版开着就会轮询这个接口, 每次等 1-3 分钟是不能接受的。
        if not any(b.running() for b in st.backends.values()):
            return self._send_json(200, self.app.fallback_props())
        st.enter(group)
        try:
            group = st.ensure(group, model_id, path)
            code, raw, ctype = self._fetch_from_backend(group, body)
            if code == 404:
                # Strata 没有 /props (它不是 llama-server)。但前端只认
                # role=router 才会显示两组模型的目录, 所以缺了就自己合成一份,
                # 免得网页版又退化成「单模型界面」。
                return self._send_json(200, self.app.fallback_props())
            try:
                obj = json.loads(raw.decode("utf-8", "replace"))
            except Exception:                                # noqa: BLE001
                return self._send_raw(code, ctype, raw)
            if not isinstance(obj, dict):
                return self._send_raw(code, ctype, raw)
            obj["role"] = "router"
            return self._send_json(code, obj)
        finally:
            st.leave(group)

    def _handle_models_sse(self) -> None:
        """/models/sse —— 这条流由网关端着。

        主池 (router) 有原生 /models/sse -> 逐块中继, 进度/休眠事件照旧;
        KVMem / Strata 都是单模型进程, 根本没有这个接口 (404) -> 网关按
        /v1/models 的合并结果自己合成 model_status 事件 (Strata 的状态
        来自它自己的 /health.loaded)。

        以前这里直接往当前后端转发: KVMem 在跑时前端每秒吃一个 404,
        routerModels 永远停在旧值, 模型选择器也就只剩当前那一个。
        """
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True
        except OSError:
            return
        # 连上先补一条 models_reload: 前端收到会重新 GET /v1/models 对状态
        if not self._sse_write({"event": "models_reload"}):
            return
        st = self.app.state
        last: Dict[str, str] = {}
        while True:
            if st.switching:                    # 后端正在换, 先别报旧状态
                time.sleep(SSE_POLL)
                continue
            if st.active == POOL and self.app.backends[POOL].running():
                return self._relay_model_events()
            try:
                nodes = self.app.models_payload()["data"]
            except Exception:                                # noqa: BLE001
                nodes = []
            for node in nodes:
                mid = str(node.get("id") or "")
                val = str(((node.get("status") or {}).get("value"))
                          or "unloaded")
                if last.get(mid) == val:
                    continue
                last[mid] = val
                if not self._sse_write({"event": "model_status",
                                        "model": mid,
                                        "data": {"status": val}}):
                    return
            time.sleep(SSE_POLL)

    def _relay_model_events(self) -> None:
        """主池的原生状态流, 逐块中继到浏览器 (读法同 send_response_to_backend)。"""
        b = self.app.backends[POOL]
        conn = http.client.HTTPConnection("127.0.0.1", b.port,
                                          timeout=UPSTREAM_TIMEOUT)
        try:
            headers = {k: v for k, v in self.headers.items()
                       if k.lower() not in FORWARD_STRIP}
            headers["Host"] = f"127.0.0.1:{b.port}"
            headers["Accept-Encoding"] = "identity"
            conn.request("GET", self.path, headers=headers)
            resp = conn.getresponse()
            if resp.status != 200:            # 头已经发出去了, 只能收尾重连
                return
            reader = getattr(resp, "read1", None) or resp.read
            while True:
                try:
                    chunk = reader(65536)
                except (AttributeError, NotImplementedError,
                        io.UnsupportedOperation):
                    reader = resp.read
                    chunk = reader(65536)
                if not chunk:
                    break
                self.wfile.write(chunk)
                self.wfile.flush()
        except (OSError, http.client.HTTPException):
            pass
        finally:
            conn.close()

    def _sse_write(self, obj: Dict[str, Any]) -> bool:
        try:
            rec = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.wfile.write(b"data: " + rec + b"\n\n")
            self.wfile.flush()
            return True
        except Exception:                                    # noqa: BLE001
            return False

    # ---- 回写 ----
    def send_response_to_backend(self, resp: "http.client.HTTPResponse") -> None:
        ctype = resp.getheader("Content-Type") or ""
        clen = resp.getheader("Content-Length")
        # 没有 Content-Length (后端边生成边发) 或 SSE -> 逐块透传
        streaming = (not clen) or ("text/event-stream" in ctype)
        headers = [(k, v) for k, v in resp.getheaders()
                   if k.lower() not in HOP_BY_HOP and k.lower() != "content-length"]

        self.send_response(resp.status, resp.reason)
        for k, v in headers:
            self.send_header(k, v)

        if self.command == "HEAD":
            # HEAD 不带体: 头按上游的回, 别去 read() 一把把体读掉
            if not streaming:
                self.send_header("Content-Length", str(clen or 0))
            else:
                self.send_header("Connection", "close")
                self.close_connection = True
            self.end_headers()
            return

        if streaming:
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True
            # 这里**不能**用 read(): 它会一直阻塞到凑够 n 字节或读到流结束,
            # 流式输出会被整条攒到 EOF 才发给浏览器。read1() 有多少发多少,
            # 才是真正的逐块透传。
            reader = getattr(resp, "read1", None) or resp.read
            while True:
                try:
                    chunk = reader(65536)
                except (AttributeError, NotImplementedError,
                        io.UnsupportedOperation):
                    reader = resp.read
                    chunk = reader(65536)
                if not chunk:
                    break
                self.wfile.write(chunk)
                self.wfile.flush()
            return

        body = resp.read() or b""
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


    def _send_no_content(self) -> None:
        """204 —— EventSource 收到它就**永久停止重连** (见 _handle 里 /models/sse)。"""
        self.send_response(204)
        self.send_header("Content-Length", "0")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True

    def _send_catalog_cached(self) -> None:
        """/models 与 /v1/models 共用: 发缓存好的序列化字节 + ETag。

        网页版 (PWA) 每秒轮询这两个接口, 停不下来 —— 那是浏览器行为, 服务端
        控制不了。所以只能让每次轮询便宜到可以忽略:
          * 内容命中缓存时**连目录都不重建**, 连 json 都不重新编码
          * 带 ETag: 客户端拿 If-None-Match 来就回 304, body 为空
          * 带 Cache-Control: 尊重它的客户端会自己少发
        """
        raw, etag = self.app.models_payload_cached()
        try:
            if (self.headers.get("If-None-Match") or "").strip() == etag:
                self.send_response(304)
                self.send_header("ETag", etag)
                self.send_header("Content-Length", "0")
                self.send_header("Connection", "close")
                self.end_headers()
                self.close_connection = True
                return
        except Exception:                                # noqa: BLE001
            pass
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("ETag", etag)
        self.send_header("Cache-Control", "no-cache, max-age=2")
        self.send_header("Connection", "close")
        self.end_headers()
        if self.command != "HEAD":
            try:
                self.wfile.write(raw)
            except Exception:                            # noqa: BLE001
                pass
        self.close_connection = True


    def _send_json(self, code: int, obj: Any) -> None:
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Connection", "close")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)
        self.close_connection = True

    def _send_raw(self, code: int, ctype: str, raw: bytes) -> None:
        """后端回的不是网关认识的格式时, 原样端给客户端。"""
        self.send_response(code)
        if ctype:
            self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Connection", "close")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(raw)
        self.close_connection = True


class GatewayServer(ThreadingHTTPServer):
    daemon_threads = True

    # Windows 上这个开关必须小心, 两个坑:
    #
    # 1) 开着它 (= `SO_REUSEADDR`) 时, Windows 允许**第二个进程绑同一个口**。
    #    「服务重启时旧进程还没退干净」这种再普通不过的情况, 就变成两个网关
    #    同时监听 0.0.0.0:18080 —— Windows 把连接随机丢给其中一个, 那个没人
    #    管的孤儿会吞掉请求, 表现就是「浏览器时好时坏 / 连不上」。
    #
    # 2) 关掉它又会踩另一个坑: 上一个进程留下一堆 TIME_WAIT 连接时, 紧接着
    #    的重启会 bind 失败 (WSAEADDRINUSE)。而浏览器/PWA 会持续轮询
    #    `/health` / `sw.js`, TIME_WAIT 几乎必然存在。
    #
    # 所以走第三条路: `allow_reuse_address` 关掉, 改用 `SO_EXCLUSIVEADDRUSE`
    # (见 server_bind)。它在 TIME_WAIT 时照样能绑, 但别的进程抢不走 —— 这才
    # 是「服务器独占端口」真正想要的语义。
    allow_reuse_address = False
    app: GatewayApp

    def server_bind(self) -> None:
        if os.name == "nt":
            # 必须在 bind **之前**设, 否则不起作用
            try:
                self.socket.setsockopt(socket.SOL_SOCKET,
                                       socket.SO_EXCLUSIVEADDRUSE, 1)
            except OSError:
                pass          # 老系统没有这个选项, 退回普通语义
        super().server_bind()


# ----------------------------------------------------------------------
# 启动
# ----------------------------------------------------------------------
def _port_owner_pids(port: int) -> List[int]:
    """谁在监听这个端口 (Windows: netstat -ano)。拿不到就返回空。"""
    try:
        out = subprocess.run(["netstat", "-ano", "-p", "TCP"],
                             capture_output=True, timeout=20)
        txt = (out.stdout or b"").decode("utf-8", "replace")
    except Exception:                                    # noqa: BLE001
        return []
    pids: List[int] = []
    for line in txt.splitlines():
        parts = line.split()
        # 形如: TCP  0.0.0.0:8080  0.0.0.0:0  LISTENING  1234
        if len(parts) < 5 or parts[0].upper() != "TCP":
            continue
        if not parts[1].rpartition(":")[2] == str(port):
            continue
        if parts[3].upper() != "LISTENING":
            continue
        try:
            pids.append(int(parts[4]))
        except ValueError:
            continue
    return pids


def _proc_cmdline(pid: int) -> str:
    """这个 PID 的命令行 (用来判断是不是我们认识的后端)。

    新版 Windows 已经不带 wmic 了, 所以走 PowerShell 的 CIM。
    读不到就返回空 —— 宁可「认不出来不杀」, 也不要瞎杀。
    """
    ps = (f"Get-CimInstance Win32_Process -Filter \"ProcessId={pid}\" "
          f"| Select-Object -ExpandProperty CommandLine")
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
            capture_output=True, timeout=25)
        txt = (out.stdout or b"").decode("utf-8", "replace")
        return " ".join(txt.split())
    except Exception:                                    # noqa: BLE001
        return ""


# 认得出来的后端 (只按命令行里的可执行文件/脚本判断, 绝不按 python.exe 这种
# 通用名去杀 —— 那会把网关自己和桌面版一起送走)
#
# 只有「通用名 / 我们自己起的名字」写死; 各软件的**真实可执行路径一律现取**
# (见 backend_marks), 这样升级换目录、或新增一个模型/软件都能自动跟上。
_STATIC_MARKS = ("llama-server.exe", "llama-kvmem-server.exe",
                 r"serve\server.py", "serve/server.py")

# 进程特征缓存 (动态那部分)。设一次 cwd 变了不用反复问磁盘。
_marks_cache: Dict[str, Tuple[float, Tuple[str, ...]]] = {}
_MARKS_TTL = 30.0

# **绝不**拿这些名字当进程特征: 网关自己、别的 Python 程序都叫它们。
_GENERIC_EXE_NAMES = frozenset((
    "python.exe", "pythonw.exe", "node.exe", "java.exe", "dotnet.exe",
    "cmd.exe", "powershell.exe", "pwsh.exe", "conhost.exe"))

# 目录特征往上收时撞到这些就停: 再往上是公共区域, 会误匹配。
_COMMON_DIR_WORDS = frozenset((
    "users", "program files", "program files (x86)", "programdata",
    "appdata", "local", "roaming", "temp", "tmp", "windows", "opt",
    "usr", "etc", "var", "home", "root", "lib", "bin", "python",
    "venv", ".venv", "scripts", "site-packages", "python312", "python314"))


def backend_marks(cfg: Dict[str, Any]) -> Tuple[str, ...]:
    """**动态**的进程识别特征表: 静态名 + 各软件自报的安装路径。

    为什么不能写死: 之前 `_KNOWN_BACKEND_MARKS` 里 `strata.exe` 是硬编码
    字符串。一旦 Strata 换安装目录、或者新注册一个跑在别的路径上的后端,
    认不出来就只会打一句「不像我们认识的后端, 不自动杀」—— 那个残留进程会把
    内部口占着, 后面每个请求都起不来。

    现在改成问各软件自己要路径 (install_dir / find_python / …),
    拼成**完整 exe 路径**和**软件目录**两种形态去匹配命令行。

    两条硬规矩 (踩过的坑):
      1. **绝不收通用名** (`python.exe` / `pythonw.exe` / `node.exe` …)。
         网关自己都叫 python.exe, 拿它当特征会让清理逻辑把**自己**也杀掉。
      2. 目录片段只取**软件自己的那一层** (`C:\\Strata`),
         不取 `C:\\Users\\xxx\\AppData\\Local` 这种公共上级 —— 那能匹配上
         半个系统上的进程。
    """
    now = time.monotonic()
    key = str(config.CONFIG_PATH)
    hit = _marks_cache.get(key)
    if hit is not None and now - hit[0] < _MARKS_TTL:
        return hit[1]

    marks: List[str] = list(_STATIC_MARKS)

    def _add_exe(path: Any) -> None:
        """只收完整的 exe 路径 (含文件名), 不收目录。"""
        if not path:
            return
        p2 = str(path)
        base = os.path.basename(p2).lower()
        # 通用名绝不能当特征 —— 会连网关自己一起匹配上
        if base in _GENERIC_EXE_NAMES:
            return
        marks.append(p2)
        marks.append(base)

    def _add_dir(path: Any, depth: int = 2) -> None:
        r"""收软件自己那一层目录 (depth 层), **但要够具体**。

        收紧的理由: `C:` 和 `C:\Users\xx\AppData\Local` 这种能匹配上半个
        系统上的进程 —— 一旦拿它当特征, 清理逻辑会误杀毫不相干的程序。
        判定标准: 往上收时撞到公共词 (Users / AppData / Local / venv …)
        就停, 不能再往上收。
        """
        if not path:
            return
        cur = str(path).rstrip("\\/")
        for _ in range(max(0, depth)):
            if not cur or os.path.dirname(cur) == cur:
                break
            leaf = os.path.basename(cur)
            if not leaf or leaf.endswith(":") or leaf.lower() in _COMMON_DIR_WORDS:
                break                      # 到公共层就停, 不能再往上收了
            marks.append(cur)
            cur = os.path.dirname(cur).rstrip("\\/")

    try:
        import strata
        _add_dir(strata.install_dir(cfg), depth=2)
        _add_exe(os.path.join(str(strata.install_dir(cfg)),
                              "engine", "strata.exe"))
        _add_exe(strata.find_server(cfg))
    except Exception:                                    # noqa: BLE001
        pass

    out = tuple(dict.fromkeys(m for m in marks if m))
    _marks_cache[key] = (now, out)
    return out


def match_backend_mark(cmd: str, marks: Iterable[str]) -> Optional[str]:
    """命令行里命中了哪个特征 (返回第一个命中的), 没命中返回 None。"""
    if not cmd:
        return None
    low = str(cmd).lower().replace("/", "\\")
    for m in marks:
        if m and m.lower().replace("/", "\\") in low:
            return m
    return None


# 冲突询问的默认等候时间 (秒)。UI 没应答 / 用户没选满这么久 -> 退化成「等用完」。
CONFLICT_ASK_TIMEOUT = 300.0


def _port_conflict_info(port: int, want_group: str,
                        want_model: str, cfg: Dict[str, Any],
                        log: Callable[[str], None]) -> Optional[Dict[str, Any]]:
    """内部口被**别的进程**占着 -> 返回冲突详情 (没冲突返回 None)。

    只在「即将起新后端、而这个口不是我们自己在跑」时才需要问:
      - 占着的进程命令行**不**命中动态特征表 -> 是外部程序(比如用户自己
        用 START-HERE.bat 起的 Strata), 我们不该随便杀, 得问
      - 命中了但 `self.proc` 为空 -> 是上一轮的残留或桌面端起的, 也可以问

    **绝不**因为「没带 model 的轮询」就弹窗 —— 网页版会高频问 /props 和
    /health, 弹一次就够受的。
    """
    if not port_busy(port):
        return None
    pids = _port_owner_pids(port)
    if not pids:
        return None
    marks = backend_marks(cfg)
    holders = []
    for pid in pids:
        cmd = _proc_cmdline(pid)
        holders.append({"pid": pid, "cmd": cmd[:200],
                        "mark": match_backend_mark(cmd, marks)})
    who = holders[0]
    if who["mark"] and not who["cmd"]:
        return None                      # 读不到命令行就别瞎猜
    return {
        "port": port,
        "pids": [h["pid"] for h in holders],
        "holders": holders,
        "known": [h["mark"] for h in holders if h["mark"]],
        "want_group": want_group,
        "want_group_cn": GROUP_CN.get(want_group, want_group),
        "want_model": want_model,
    }


def resolve_port_conflict(cf: Dict[str, Any], cfg: Dict[str, Any],
                          log: Callable[[str], None],
                          resolver: Optional[Callable[..., Optional[str]]] = None
                          ) -> str:
    """问用户「这个占着内部口的进程怎么处理」。返回 force / wait / reject / timeout。

    - force   强行结束对方(对方不在我们的自动清理范围内时才可能需要)
    - wait    等它自己腾出来 —— **默认**, 绝不自动强杀
    - reject  直接拒绝这次切换
    - timeout 没人应答(UI 没开) -> 当作 wait
    """
    ask = resolver
    if ask is None:
        import conflict_ask
        ask = conflict_ask.ask
    log(f"[网关] 内部口 {cf['port']} 被 pid {cf['pids']} 占着 "
        f"(要起 {cf['want_group_cn']}"
        + (f" / {cf['want_model']}" if cf.get("want_model") else "")
        + ")")
    for h in cf["holders"]:
        log(f"[网关]   pid {h['pid']}"
            + (f" 认出是 {h['mark']}" if h.get("mark") else " 不认识")
            + f": {h['cmd'] or '(读不到)'}")
    try:
        choice = ask(
            {"kind": "port_conflict",
             "port": cf["port"], "pids": cf["pids"],
             "holders": cf["holders"], "known": cf["known"],
             "want_group": cf["want_group"],
             "want_group_cn": cf["want_group_cn"],
             "want_model": cf.get("want_model") or ""},
            log=log, timeout=float(CONFLICT_ASK_TIMEOUT))
    except Exception as e:                              # noqa: BLE001
        log(f"[网关] 询问冲突时出错({e}), 按「等用完」处理")
        choice = None
    if choice is None:
        log("[网关] 没人应答 -> 等它自己用完, **不强行结束**")
        return "timeout"
    log(f"[网关] 你的选择: {choice}")
    return choice



def _free_shared_port(app: GatewayApp) -> None:
    """内部口被上一轮的残留占着 -> 按**端口占用者 PID** 精确清理。

    三个后端共用一个内部口, 所以「谁占着」就是唯一的那一个残留进程。做法:
    找到监听该端口的 PID, 确认它是**我们认识的后端**再 taskkill /T 走进程树;
    认不出来 (比如用户自己用 START-HERE.bat 起了个 Strata, 或者别的程序)
    就不动它, 只把话说明白 —— 误杀别人的进程比起不来更难查。
    """
    ports = {b.port for b in app.backends.values()}
    for port in sorted(ports):
        if not port_busy(port):
            continue
        pids = _port_owner_pids(port)
        if not pids:
            app.log(f"[网关] 内部口 127.0.0.1:{port} 被占用, 但查不出占用者"
                    f"(需要管理员权限看 netstat?); 请手动关掉占用它的进程")
            continue
        for pid in pids:
            cmd = _proc_cmdline(pid)
            marks = backend_marks(app.cfg_loader())
            hit_mark = match_backend_mark(cmd, marks)
            known = [hit_mark] if hit_mark else []
            if not known:
                app.log(f"[网关] 内部口 127.0.0.1:{port} 被 pid {pid} 占着, "
                        f"但不像我们认识的后端, **不自动杀** "
                        f"(命令: {cmd[:120] or '读不到'})。"
                        f"请自己关掉它, 否则后端起不来。")
                continue
            app.log(f"[网关] 内部口 127.0.0.1:{port} 被残留 {known[0]} "
                    f"(pid {pid}) 占着, 清理掉")
            try:
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                               capture_output=True, timeout=20)
            except Exception:                            # noqa: BLE001
                pass
        deadline = time.time() + 15
        while time.time() < deadline and port_busy(port):
            time.sleep(0.3)


def _free_stale_backends(app: GatewayApp) -> None:
    """网关起来时清残留。

    三个后端共用一个内部口, 所以「占着那个口」的就是上一轮残留的那一个
    进程 —— 按端口占用者 PID 精确清理, 不再按镜像名杀 (杀 python.exe 会
    连网关自己和桌面版一起送走)。认不出来的占用者一律不动, 只说明情况。
    """
    _free_shared_port(app)


def _guard_active_fn(app: GatewayApp) -> Callable[[], bool]:
    """服务端守护该不该干活: 必须是主池, 且桌面版 UI 不在跑。
    两边都跑会出现"弹窗已答、但模型早被另一边卸了"的竞态, 所以让位给 UI。"""
    import model_guard
    import service
    logs_dir = service.log_dir()
    return lambda: (app.state.active == POOL
                    and not model_guard.ui_guard_alive(logs_dir))


def _start_model_guard(app: GatewayApp) -> Optional[Any]:
    """主池的 LRU 换挡守护: 服务进程常驻了, UI 关掉也能继续。"""
    try:
        cfg = app.cfg_loader()
        mg_cfg = cfg.get("model_guard") or {}
        if not mg_cfg.get("enabled", True):
            return None
        import model_guard
        import service
        policy = str(mg_cfg.get("busy_policy") or "ask").strip().lower()
        if policy not in ("wait", "now"):
            policy = "wait"                      # 服务里没有 GUI, ask 没意义
        guard = model_guard.ModelGuard(
            base=f"http://127.0.0.1:{app.backends[POOL].port}",
            models_max=int(cfg.get("models_max", 1) or 1),
            log_path=service.err_log(),
            busy_policy=policy,
            poll_interval=float(mg_cfg.get("poll_interval_seconds", 1) or 1),
            wanted_ttl=float(mg_cfg.get("wanted_ttl_seconds", 30) or 30),
            log_fn=app.log,
            active_fn=_guard_active_fn(app),
            paused_msg="主池后端不在跑, 或桌面版 UI 正在跑 (由它来弹窗), 服务端守护让位",
            resume_msg="接管换挡 (桌面版已退出 / 主池已就位)",
        )
        guard.daemon = True
        guard.start()
        app.log("[网关] model_guard 已启动 (主池换挡)")
        return guard
    except Exception as e:                          # noqa: BLE001
        app.log(f"[网关] model_guard 启动失败: {e}")
        return None


def main() -> int:
    cfg = config.load()
    app = GatewayApp()
    host = str(cfg.get("host") or "0.0.0.0")
    port = int(cfg.get("port") or 18080)
    app.log(f"[网关] llama-router 网关启动 {host}:{port} "
            + "(后端 "
            + " / ".join(f"127.0.0.1:{b.port}={GROUP_CN.get(g, g)}"
                         for g, b in app.backends.items())
            + ")")
    _free_stale_backends(app)

    # 首选后端: preload=true 才在开机就起 (默认 false = 按需加载,
    # 开机不把任何模型拖进内存, 收到带 model 的请求才加载那一组)
    mode = str(cfg.get("serve_mode") or "router")
    first = mode if mode in SINGLE_MODEL_GROUPS else POOL
    if preload_of(cfg):
        try:
            app.state.ensure(first, None)
        except Exception as e:                          # noqa: BLE001
            app.log(f"[网关] 首选后端 {first} 启动失败: {e}")
    else:
        app.log(f"[网关] 按需加载: 暂不启动任何后端 "
                f"(默认组 {first}); 收到带 model 的请求再加载对应那一组。"
                f"要开机就预加载, 把 config.gateway.preload 设成 true")

    guard = _start_model_guard(app)

    # 端口被占 = 有另一个网关还在跑 (常见于重装服务时旧进程没退干净)。
    # 这里必须自己报清楚: 否则它会「起不来」或者更糟 —— 在 Windows 上曾经
    # 因为 allow_reuse_address 抢同一个口, 变成两个网关各接一半请求。
    try:
        server = GatewayServer((host, port), GatewayHandler)
    except OSError as e:
        owners = _port_owner_pids(port)
        detail = ""
        for pid in owners:
            detail = f" (占用者 pid {pid}: {_proc_cmdline(pid)[:120]})"
            break
        app.log(f"[网关] **绑不上 {host}:{port}** —— {e}{detail}\n"
                f"         说明已经有网关在跑这个口了。先在「服务」页停掉服务, "
                f"或结束那个进程, 再启动。")
        try:
            if guard is not None:
                guard.stop()
        except Exception:
            pass
        return 2
    server.app = app
    app.log(f"[网关] 对外监听 http://{host}:{port}")

    def _shutdown(*_a: Any) -> None:
        try:
            if guard is not None:
                guard.stop()
        except Exception:
            pass
        for b in app.backends.values():
            try:
                b.stop()
            except Exception:
                pass
        app.log("[网关] 已退出, 后端已停止")

    import atexit
    atexit.register(_shutdown)
    try:
        server.serve_forever(poll_interval=0.5)
    finally:
        _shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
