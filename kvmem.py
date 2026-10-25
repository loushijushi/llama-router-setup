"""KVMem 分支 (llama-kvmem-server / ninfer-kvmem-server) 托管。

两套引擎并存: 老 llama-kvmem-server (v0.16, .gguf) 与 ninfer-kvmem-server
(v0.18.0, .ninfer)。档位按模型工件后缀自动选引擎 (见 engine_of), 各自有
独立的安装目录与 flag 方言 (见 build_args)。两者都自述是 "Independent
single-slot OpenAI-compatible server. Does not patch llama-server." ——
单槽、独立进程、不认 --preset 多模型池。因此:

  * 它的档位和主池模型存在 config.json 的**同一份 models 清单**里
    (runtime == "kvmem"), 方便一个列表统一管理、统一给其他程序看;
  * 但 generate_preset.render() 会把这些条目挡在 router-preset.ini 之外
    (主 llama-server b11200 不认识 --kvmem-*, 混进去会让整个 preset 解析失败);
  * 同一时刻只跑一个档位 (单槽 + 显存), 用 kvmem.active_id 选中;
  * 与主池共用**同一个服务 llama-router 和同一个端口** (config.host/
    config.port, 默认 0.0.0.0:8080) —— 一个服务只能指向一个 exe, 所以
    「切换运行时」= 按 config.serve_mode 重装这一个服务;
  * 老配置里的 kvmem.host / kvmem.port / 模型条目的 kvmem_port 已废弃,
    运行时一律读顶层的 host/port。

所有对外函数都不抛异常, 返回 (ok, message), 与 service.py 保持一致。
"""
import glob
import os
import re
import socket
import subprocess
from typing import Any, Dict, List, Optional, Sequence, Tuple

import config
import service

# 端口/服务合并后 KVMem 分支不再单独注册, 复用主池的 llama-router 服务名
SERVICE_NAME = service.SERVICE_NAME

_HELP_CACHE: Dict[Tuple[str, Tuple[int, int]], str] = {}
_KNOWN_CACHE: Dict[Tuple[str, Tuple[int, int]], frozenset] = {}


def is_kvmem(model: Dict[str, Any]) -> bool:
    return model.get("runtime") == config.KVMEM_RUNTIME


def kvmem_models(cfg: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    cfg = cfg if cfg is not None else config.load()
    return [m for m in cfg.get("models", []) if is_kvmem(m)]


def active_model(cfg: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    """当前档位。active_id 对不上时回落到第一个 kvmem 档位。"""
    cfg = cfg if cfg is not None else config.load()
    kv = cfg.get("kvmem") or {}
    want = (kv.get("active_id") or "").strip()
    items = kvmem_models(cfg)
    if want:
        for m in items:
            if m.get("id") == want:
                return m
    return items[0] if items else None


def port_of(cfg: Dict[str, Any], model: Dict[str, Any] = None) -> int:
    """与主池统一的端口 (config.port)。

    model 参数只为兼容旧调用保留; 老的 kvmem_port / kvmem.port 已废弃。
    """
    try:
        return int((cfg or {}).get("port") or 8080)
    except (TypeError, ValueError):
        return 8080


def base_url(cfg: Dict[str, Any], model: Dict[str, Any] = None) -> str:
    """客户端调用地址。绑 0.0.0.0 时对内仍用 127.0.0.1 访问 (与
    watchdog / model_guard 一致)。"""
    return f"http://127.0.0.1:{port_of(cfg, model)}/v1"


def _sig(path: str) -> Tuple[int, int]:
    try:
        st = os.stat(path)
        return (int(st.st_mtime), int(st.st_size))
    except OSError:
        return (0, 0)


# 两套 KVMem 引擎并存, 名字不同、方言不同:
#   * llama 引擎  -> llama-kvmem-server.exe  (v0.16 老版, 吃 .gguf + --mmproj)
#   * ninfer 引擎 -> ninfer-kvmem-server.exe (v0.18.0, 只吃单文件 .ninfer 工件)
# 引擎由档位的模型工件后缀决定 (见 engine_of), 也可在条目里写 "engine" 显式指定。
ENGINE_LLAMA = config.KV_ENGINE_LLAMA
ENGINE_NINFER = config.KV_ENGINE_NINFER
_EXE_BY_ENGINE = {
    ENGINE_LLAMA: "llama-kvmem-server.exe",
    ENGINE_NINFER: "ninfer-kvmem-server.exe",
}
_EXE_NAMES = (_EXE_BY_ENGINE[ENGINE_NINFER], _EXE_BY_ENGINE[ENGINE_LLAMA])


def engine_of(model: Optional[Dict[str, Any]]) -> str:
    """档位属于哪套引擎 (单一实现见 config.model_engine)。"""
    return config.model_engine(model or {})


def _is_ninfer(exe: str) -> bool:
    """该可执行文件是不是 ninfer 引擎 (而非老 llama.cpp 引擎)。"""
    return os.path.basename(exe or "").lower().startswith("ninfer")


def _search_exe(root: str, names: Sequence[str]) -> str:
    """在 root 下探测 bin/<name> 或 **/<name> (取 mtime 最新的那份)。"""
    root = (root or "").strip().strip('"')
    if not root or not os.path.isdir(root):
        return ""
    for name in names:
        direct = os.path.join(root, "bin", name)
        if os.path.isfile(direct):
            return direct
    for name in names:
        hits = glob.glob(os.path.join(root, "**", name), recursive=True)
        if hits:
            hits.sort(key=os.path.getmtime, reverse=True)
            return hits[0]
    return ""


def _exe_dirs(cfg: Dict[str, Any], engine: str) -> List[str]:
    """该引擎去哪些目录找 exe (本引擎专属目录优先, 再退回另一个 -- 兼容旧配置)。

    两套引擎各有安装目录: install_dir = 老 llama-kvmem-server,
    ninfer_install_dir = ninfer。老配置只有一个 install_dir 时两个引擎都指向它。
    """
    kv = cfg.get("kvmem") or {}
    legacy = (kv.get("install_dir") or "").strip().strip('"')
    ninfer = (kv.get("ninfer_install_dir") or "").strip().strip('"')
    if not legacy and not ninfer:
        legacy = os.path.join(cfg.get("llama_dir", r"C:\llama.cpp"), "kvmem-llama.cpp")
    if engine == ENGINE_NINFER:
        return [ninfer, legacy, ninfer]
    return [legacy, ninfer]


def find_exe(cfg: Optional[Dict[str, Any]] = None,
             model: Optional[Dict[str, Any]] = None,
             engine: Optional[str] = None) -> str:
    """定位指定引擎的 KVMem 分支后端可执行文件。

    优先级:
      1. 显式路径: 老引擎读 kvmem.exe, ninfer 读 kvmem.ninfer_exe (否则回落 exe);
      2. 该引擎专属目录下的同名 exe;
      3. 任一目录下的任意 KVMem exe (兼容只装了一版的旧配置)。
    engine 省略时按 model 的工件后缀推断; model 也省略时先 ninfer 后 llama。
    不写死版本号 —— 换 kvmem 发布包时不用改配置。
    """
    cfg = cfg if cfg is not None else config.load()
    kv = cfg.get("kvmem") or {}
    if engine is None:
        engine = engine_of(model) if model is not None else ""

    for key in (("ninfer_exe", "exe") if engine == ENGINE_NINFER else ("exe",)):
        exe = (kv.get(key) or "").strip().strip('"')
        if exe and os.path.isfile(exe):
            return exe

    names = (_EXE_BY_ENGINE[engine],) if engine in _EXE_BY_ENGINE else ()
    dirs = _exe_dirs(cfg, engine) if engine else [
        (kv.get("ninfer_install_dir") or "").strip().strip('"'),
        (kv.get("install_dir") or "").strip().strip('"'),
    ]
    for root in dirs:
        hit = _search_exe(root, names)
        if hit:
            return hit
    for root in dirs:                     # 退一步: 该目录里另一套引擎的 exe 也行
        hit = _search_exe(root, _EXE_NAMES)
        if hit:
            return hit
    return ""


def help_text(exe: str) -> str:
    if not exe:
        return ""
    ck = (exe, _sig(exe))
    if ck in _HELP_CACHE:
        return _HELP_CACHE[ck]
    text = ""
    try:
        r = subprocess.run([exe, "-h"], capture_output=True, timeout=90)
        text = (r.stdout or b"").decode("utf-8", "replace")
        text += (r.stderr or b"").decode("utf-8", "replace")
    except Exception:
        text = ""
    _HELP_CACHE[ck] = text
    return text


def known_keys(exe: str) -> frozenset:
    """分支程序自己认识的 flag 名 —— 与主路由的 config.llama_known_keys()
    完全独立, 两者 flag 集合不一样 (kvmem 有 --kvmem-*, 主路由有 --models-preset)。
    """
    if not exe:
        return frozenset()
    ck = (exe, _sig(exe))
    if ck in _KNOWN_CACHE:
        return _KNOWN_CACHE[ck]
    keys = frozenset(re.findall(r"--([a-z0-9][a-z0-9\-]*)", help_text(exe)))
    _KNOWN_CACHE[ck] = keys
    return keys


# ----------------------------------------------------------------------
# 命令行构造
# ----------------------------------------------------------------------
_BOOL_TRUE = ("true", "1", "yes", "on")

# 由 _draft_shortcut 统一产出的快捷字段, 参数循环里必须跳过 (否则写两遍)
_DRAFT_QUICK = frozenset({"spec-type", "spec-draft-model", "spec-draft-n-max"})


def _append_flag(out: List[str], key: str, value: Any, kind: str,
                 known: frozenset, skipped: Optional[List[str]]) -> None:
    """按 kind 把一个参数拼成 CLI 片段; 不认识的记进 skipped (不静默丢)。"""
    def _skip() -> None:
        if skipped is not None and key not in skipped:
            skipped.append(key)

    if known and key not in known and ("no-" + key) not in known:
        _skip()
        return

    if kind == "bool":
        truthy = str(value).strip().lower() in _BOOL_TRUE
        if truthy:
            if not known or key in known:
                out.append("--" + key)
            else:
                _skip()
        else:
            neg = "no-" + key
            if not known or neg in known:
                out.append("--" + neg)
            # 没有 --no-X 形式就只能不写 (默认值通常已经是我们要的)
        return

    val = str(value).strip()
    if not val:
        return
    if known and key not in known:
        _skip()
        return
    out.extend(["--" + key, val])


def _draft_shortcut(m: Dict[str, Any]) -> Dict[str, str]:
    """推测解码快捷字段 -> CLI 片段, 与 generate_preset.render() 同一套规则。"""
    on = bool(m.get("draft_enabled", bool(m.get("draft_model"))))
    if not on:
        return {}
    den = m.get("draft_en") or {}

    def en(k: str) -> bool:
        return bool(den.get(k, True))

    out: Dict[str, str] = {}
    dtype = str(m.get("draft_type") or "").strip()
    if dtype and en("spec-type"):
        out["spec-type"] = dtype
    dmodel = str(m.get("draft_model") or "").strip()
    if dmodel and en("spec-draft-model"):
        out["spec-draft-model"] = dmodel
    nmax = str(m.get("draft_n_max") or "").strip()
    if nmax and en("spec-draft-n-max"):
        out["spec-draft-n-max"] = nmax
    return out


def _supported(key: str, known: frozenset) -> bool:
    """这个分支程序认不认 key (bool 参数还要看 --no- 形式)。

    known 为空 = 没拿到 --help (程序没装 / 调用方显式不过滤) -> 一律放行,
    与 generate_preset 对 known=None 的既有口径一致。
    """
    if not known:
        return True
    return key in known or ("no-" + key) in known


# ninfer 引擎里「纯开关」型的 flag (key 就是去掉 -- 的名字)。
# 这些直接产出 --key; 值为假时尝试 --no-key (存在才写)。
_NINFER_BOOL = frozenset({
    "vision", "no-thinking", "kvmem-lrfu", "adaptive-mtp",
    "fast-prefill-kernel", "gdn-state-fp16", "wddm-evictable-budget",
})

# ninfer 档位允许写进命令行的参数键 (白名单)。
#
# 这份清单取自 config 里 scope="kvmem" 且 engine 为 ninfer/共享 的整张参数表
# (config.kvmem_keys_for_engine("ninfer")), 保证「⚡KVMem」页能编辑的参数与
# 命令行真正产出的 flag **永远同步** —— 换 ninfer 版本时只改 config.PARAM_SCHEMA
# 一处即可。
#
# 为什么需要白名单: config.load()/UI 保存时会把主池 llama 的那套 PARAM_SCHEMA
# 默认值**补进每个模型条目** (temp/top-k/ctx-size/cache-type-k ... 一百多项,
# 且常用项默认 enabled=True)。这些不是用户为 ninfer 档位写的, 直接按「enabled
# 就产出」会把一堆 llama flag 混进 ninfer 命令行。所以 ninfer 分支只认这份
# 显式清单 —— 不在表里的一律不产出 (也不记 skipped, 它们只是主池的默认项)。
_NINFER_KEYS = config.kvmem_keys_for_engine(ENGINE_NINFER)

# 老 llama-kvmem-server 引擎专属的 scope="kvmem" 键 (两引擎共享的键两边都认)。
_LLAMA_KV_KEYS = config.kvmem_keys_for_engine(ENGINE_LLAMA)


def _build_args_ninfer(cfg: Dict[str, Any], model: Dict[str, Any],
                       known: frozenset,
                       skipped: Optional[List[str]]) -> List[str]:
    """模型条目 -> ninfer-kvmem-server 命令行 (不含 exe 本身)。

    ninfer 方言与老 llama.cpp 方言不同, 关键差异:
      * 模型是**位置参数** (不是 -m <gguf>);
      * 视觉塔内嵌在单文件 .ninfer 里, 没有 --mmproj, 用 --vision +
        --vision-residency 控制;
      * 别名用 --model-id (不是 --alias);
      * 参数只认 _NINFER_KEYS 白名单里的键 (见其注释), 再按 --help 的
        known 过滤一刀。
    """
    args: List[str] = []
    mp = str(model.get("model") or "").strip()
    if mp:
        args.append(mp)                       # ninfer: 模型是位置参数
    args.extend(["--host", str(cfg.get("host") or "0.0.0.0"),
                 "--port", str(port_of(cfg, model))])
    alias = str(model.get("alias") or "").strip()
    if alias:
        args.extend(["--model-id", alias])    # ninfer 用 --model-id

    params = model.get("params") or {}
    for k, node in params.items():
        if k not in _NINFER_KEYS:
            continue                          # 主池 llama schema 补的默认项
        if not isinstance(node, dict) or not node.get("enabled"):
            continue
        if not _supported(k, known):
            if skipped is not None and k not in skipped:
                skipped.append(k)
            continue
        if k in _NINFER_BOOL:
            if str(node.get("value", "")).strip().lower() in _BOOL_TRUE:
                if not known or k in known:
                    args.append("--" + k)
            else:
                neg = "no-" + k
                if not known or neg in known:
                    args.append("--" + neg)
            continue
        val = str(node.get("value", "")).strip()
        if val:
            args.extend(["--" + k, val])
    return args


def build_args(cfg: Dict[str, Any], model: Dict[str, Any],
               known: Optional[frozenset] = None,
               skipped: Optional[List[str]] = None) -> List[str]:
    """模型条目 -> KVMem 分支命令行参数 (不含 exe 本身)。

    按**档位的引擎**分派 (两套引擎并存, 见 engine_of):
      * ninfer 引擎   -> _build_args_ninfer (位置参数 / --kv-dtype / --spec mtp ...);
      * 老 llama 引擎 -> _build_args_llama  (-m <gguf> / --mmproj / --spec-type ...)。
    引擎由模型工件后缀决定, 与后端装在哪、目录里放了几版无关。
    """
    engine = engine_of(model)
    exe = find_exe(cfg, model, engine)
    if known is None:
        known = known_keys(exe)
    if engine == ENGINE_NINFER:
        return _build_args_ninfer(cfg, model, known, skipped)
    return _build_args_llama(cfg, model, known, skipped)


def _build_args_llama(cfg: Dict[str, Any], model: Dict[str, Any],
                      known: frozenset,
                      skipped: Optional[List[str]]) -> List[str]:
    """老 llama-kvmem-server 引擎的命令行 (llama.cpp 方言)。

    分支程序与主路由的 flag 集合**不一样**, 而 config.json 里档位和主池模型
    是同一份清单 —— 主池 llama-server 认识的那组 spec-draft-* (草稿模型路径/
    GPU 层数/线程/草稿 KV 量化...) 在 llama-kvmem-server 的 --help 里一个都
    没有 (官方 README: "independent draft models remain unsupported")。
    那种「本来就不适用」不是配置写错, 所以这里按 known 直接**不产出**,
    也不记进 skipped —— skipped 只应反映意外, 不该被这类常态项刷屏。

    另外: ninfer 引擎专属的 scope="kvmem" flag 也一律跳过 (不在 _LLAMA_KV_KEYS
    里), 免得同一份 params 在两个引擎之间互相污染。
    """
    args: List[str] = []
    mp = str(model.get("model") or "").strip()
    if mp:
        args.extend(["-m", mp])
    proj = str(model.get("mmproj") or "").strip()
    if proj:
        args.extend(["--mmproj", proj])
    # 与主池统一: 用顶层 host/port (bat 里的 --host/--port 只作记录)
    args.extend(["--host", str((cfg.get("host") or "0.0.0.0")),
                 "--port", str(port_of(cfg, model))])
    alias = str(model.get("alias") or "").strip()
    if alias:
        args.extend(["--alias", alias])

    params = model.get("params") or {}
    schema = {p["key"]: p for p in config.all_params_meta()}
    shortcut = _draft_shortcut(model)
    draft_on = bool(model.get("draft_enabled", bool(model.get("draft_model"))))
    for p in config.all_params_meta():
        k = p["key"]
        if p.get("scope") == "kvmem" and k not in _LLAMA_KV_KEYS:
            continue                      # ninfer 专属 flag, 老引擎不认识
        node = params.get(k)
        if not isinstance(node, dict) or not node.get("enabled"):
            continue
        # 快捷字段一律交给 _draft_shortcut, 否则同一行会写两遍
        if k in _DRAFT_QUICK:
            continue
        # 草稿关掉时 spec-* 一概不写 (与 generate_preset.render 同一套规则)
        if not draft_on and k.startswith("spec-"):
            continue
        # 分支程序 --help 里没有的 flag 直接不产出 (见 build_args 的 docstring)
        if not _supported(k, known):
            continue
        _append_flag(args, k, node.get("value", ""), p.get("kind", "text"),
                     known, skipped)

    for k, v in shortcut.items():
        # _draft_shortcut 是整组产出的, 同样按 known 裁一刀
        if not _supported(k, known):
            continue
        kind = (schema.get(k) or {}).get("kind", "text")
        _append_flag(args, k, v, kind, known, skipped)

    # 分支自带聊天界面默认开 (三个 bat 都是 --webui);
    # 模型条目里写了 webui 就以条目为准 (bat 里出现 --no-webui 时能还原)
    if "webui" not in params and (not known or "webui" in known):
        args.append("--webui")
    return args


def command_line(cfg: Dict[str, Any], model: Dict[str, Any]) -> str:
    """给 NSSM AppParameters 用的单行命令行 (路径带空格时加引号)。"""
    parts = []
    for a in build_args(cfg, model):
        parts.append(f'"{a}"' if (" " in a or "\t" in a) else a)
    return " ".join(parts)


# ----------------------------------------------------------------------
# 端口 / 实例
# ----------------------------------------------------------------------
def _env_pairs(cfg: Dict[str, Any]) -> List[str]:
    """bat 里那两行 set CUDA_VISIBLE_DEVICES / CUDA_DEVICE_ORDER。"""
    kv = cfg.get("kvmem") or {}
    pairs: List[str] = []
    dev = str(kv.get("cuda_device") or "").strip()
    if dev:
        pairs.append(f"CUDA_VISIBLE_DEVICES={dev}")
    order = str(kv.get("cuda_device_order") or "").strip()
    if order:
        pairs.append(f"CUDA_DEVICE_ORDER={order}")
    return pairs


def _env_dict(cfg: Dict[str, Any]) -> Dict[str, str]:
    out = {}
    for pair in _env_pairs(cfg):
        k, _, v = pair.partition("=")
        out[k] = v
    return out


def port_busy(port: int, host: str = "") -> bool:
    """端口是否已有进程在监听 (connect 探测; bind 在 Windows 上会漏掉
    别人绑 0.0.0.0 的情况)。"""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(0.5)
    try:
        return s.connect_ex((host or "127.0.0.1", int(port))) == 0
    except OSError:
        return False
    finally:
        s.close()


def kill_instance() -> None:
    """强杀残留的分支进程 (安装服务前调用, 避免端口/显存冲突)。

    新旧引擎进程名都清一遍, 免得上一个版本的残留占着显存/端口。
    """
    for name in _EXE_NAMES:
        subprocess.run(["taskkill", "/F", "/IM", name], capture_output=True)


# ----------------------------------------------------------------------
# NSSM 服务
# ----------------------------------------------------------------------
def out_log() -> str:
    return os.path.join(service.log_dir(), "kvmem.out.log")


def err_log() -> str:
    return os.path.join(service.log_dir(), "kvmem.err.log")


def status() -> str:
    return service.status(SERVICE_NAME)


def is_installed() -> bool:
    return service.is_installed(SERVICE_NAME)


def _check_ready(cfg: Dict[str, Any]) -> Tuple[bool, str, str, Dict[str, Any]]:
    """安装/启动前的公共校验。返回 (ok, msg, exe, model)。"""
    model = active_model(cfg)
    if not model:
        return False, "没有 KVMem 档位：请先在「⚡ KVMem」页添加/选中一个档位。", "", {}
    exe = find_exe(cfg, model)
    if not exe:
        kv = cfg.get("kvmem") or {}
        root = kv.get("install_dir", "")
        return False, (f"未找到 KVMem 后端 (ninfer-kvmem-server.exe / "
                       f"llama-kvmem-server.exe, 已找 {root or '默认目录'})。"), "", model
    mp = str(model.get("model") or "").strip()
    if not mp or not os.path.isfile(mp):
        return False, f"模型文件不存在: {mp or '(空)'}", exe, model
    # mmproj 只对老 llama.cpp 引擎适用; ninfer 的视觉塔内嵌在 .ninfer 里。
    proj = str(model.get("mmproj") or "").strip()
    if proj and not _is_ninfer(exe) and not os.path.isfile(proj):
        return False, f"mmproj 文件不存在: {proj}", exe, model
    return True, "", exe, model


def install(cfg: Optional[Dict[str, Any]] = None,
            start_service: bool = True) -> Tuple[bool, str]:
    """「应用并重装」: 校验当前档位 -> 标 serve_mode=kvmem -> 装网关服务。

    一个 Windows 服务只能指向一个程序, 现在这个程序就是网关 gateway.py:
    网关占住 config.port, 按请求里的 model 字段把流量转给主池或 KVMem
    后端 (两个后端互斥, 由网关切换)。所以切档位不再靠换 exe, 这里的重装
    只是让网关重新启动、读到新的 serve_mode 并拉起对应的后端。

    start_service=False 时只注册不拉起 —— 档位会立刻占显存, 不一定想现在跑。
    """
    cfg = cfg if cfg is not None else config.load()
    cfg["serve_mode"] = "kvmem"
    ok, msg, _exe, model = _check_ready(cfg)
    if not ok:
        return False, msg
    ok, msg = service.install(cfg)
    if not ok:
        return False, msg
    if not start_service:
        service.stop(SERVICE_NAME)
        return True, (f"网关服务已注册但未启动 (档位 {model.get('id')}), "
                      f"需要时点「启动」")
    port = port_of(cfg, model)
    return True, f"网关服务已安装并启动 (档位 {model.get('id')}, 端口 {port})"


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
    st = status()
    if st == "NOT_INSTALLED":
        return False, "运行服务未安装，请先在「⚡ KVMem」页点「应用并重装」"
    if st in ("RUNNING", "START_PENDING"):
        return True, f"KVMem 服务已在运行 (状态 {st})"
    cfg = config.load()
    model = active_model(cfg)
    if model:
        port = port_of(cfg, model)
        if port_busy(port) and port != 80:
            return False, (f"端口 {port} 已被占用 (多半是手动启动的 bat 实例)，"
                           f"请先关掉它，或改用「停止后重装服务」。")
    return service.start(SERVICE_NAME, log_hint=err_log())


def stop() -> Tuple[bool, str]:
    return service.stop(SERVICE_NAME)


def restart() -> Tuple[bool, str]:
    if not service._is_admin():
        return False, service.need_admin_hint()
    if status() == "NOT_INSTALLED":
        return False, "运行服务未安装，请先在「⚡ KVMem」页点「应用并重装」"
    return service.restart(SERVICE_NAME, log_hint=err_log())


def run_foreground(cfg: Optional[Dict[str, Any]] = None) -> Tuple[bool, str]:
    """直接拉起一个分支进程 (不走服务, 便于看输出 / 不需要管理员)。"""
    cfg = cfg if cfg is not None else config.load()
    ok, msg, exe, model = _check_ready(cfg)
    if not ok:
        return False, msg
    port = port_of(cfg, model)
    if port_busy(port):
        return False, f"端口 {port} 已被占用，无法再启动一个实例。"
    cwd = os.path.dirname(exe)
    args = [exe] + build_args(cfg, model)
    try:
        env = os.environ.copy()
        env.update(_env_dict(cfg))
        subprocess.Popen(args, cwd=cwd, env=env)
    except OSError as e:
        return False, f"启动失败: {e}"
    return True, f"已前台启动: {exe}\n端口 {port}"
