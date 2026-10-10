"""Strata 分支 (C:\\Strata) 托管。

Strata 是 llama.cpp/ggml 的一个分支引擎, 自带一个 OpenAI 兼容的 HTTP 服务
(`serve/server.py`), 把 125B 的 MoE 摊到「显存 + 内存 + SSD」上跑。定位和
KVMem 分支一样 —— 档位进**同一份 models 清单** (`runtime == "strata"`),
由网关统一对外, 但**不认 llama-server 的任何 flag**:

  * 它的全部参数都在 `strata-<档位>.json` 里 (exe / args / tokenizer /
    vision / max-context ...), 这个 json 才是唯一真相;
  * 绑定的 host/port **完全由命令行 --host/--port 决定** (json 里的
    `"port": 8080` 只是记录, 不参与绑定), 所以端口统一归网关管, 后端
    永远钉在回环上, 对外只有网关那一个口;
  * 它有 `/health` (带 `loaded`) 和 `/v1/load|/v1/unload`, 但**没有**
    `/v1/models` / `/props` / `/models/sse` —— 那几个由网关合成 (同 KVMem);
  * 启动比 KVMem 慢得多 (35-55GB 拉到内存, 1-3 分钟), 所以就绪超时
    要单独放大 (`strata.ready_timeout`), 别拿主池那 300s 去卡它。

所有对外函数都不抛异常, 返回 (ok, message), 与 service.py / kvmem.py 一致。
"""
import glob
import json
import os
import socket
import subprocess
import sys
from typing import Any, Dict, List, Optional, Tuple

import config
import service

# 端口/服务合并后 Strata 分支不单独注册, 复用主池的 llama-router 服务名
SERVICE_NAME = service.SERVICE_NAME

DEFAULT_INSTALL_DIR = r"C:\Strata"
SERVER_REL = os.path.join("serve", "server.py")
VENV_PYTHON_REL = os.path.join(".venv", "Scripts", "python.exe")


# ----------------------------------------------------------------------
# 档位 (config.json 的 models 清单)
# ----------------------------------------------------------------------
def is_strata(model: Dict[str, Any]) -> bool:
    return model.get("runtime") == config.STRATA_RUNTIME


def strata_models(cfg: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    cfg = cfg if cfg is not None else config.load()
    return [m for m in cfg.get("models", []) if is_strata(m)]


def active_model(cfg: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    """当前档位。active_id 对不上时回落到第一个 Strata 档位。"""
    cfg = cfg if cfg is not None else config.load()
    st = cfg.get("strata") or {}
    want = str(st.get("active_id") or "").strip()
    items = strata_models(cfg)
    if want:
        for m in items:
            if str(m.get("id") or "") == want:
                return m
    return items[0] if items else None


def config_path_of(entry: Dict[str, Any]) -> str:
    """档位条目 -> 它自己的 strata-<档位>.json 路径。"""
    return str((entry or {}).get("strata_config") or "").strip().strip('"')


# ----------------------------------------------------------------------
# 定位可执行文件
# ----------------------------------------------------------------------
def install_dir(cfg: Optional[Dict[str, Any]] = None) -> str:
    cfg = cfg if cfg is not None else config.load()
    d = str((cfg.get("strata") or {}).get("install_dir") or "").strip().strip('"')
    return d or DEFAULT_INSTALL_DIR


def find_server(cfg: Optional[Dict[str, Any]] = None) -> str:
    """定位 serve/server.py (Strata 的 HTTP 入口)。"""
    cfg = cfg if cfg is not None else config.load()
    explicit = str((cfg.get("strata") or {}).get("server") or "").strip().strip('"')
    if explicit and os.path.isfile(explicit):
        return explicit
    root = install_dir(cfg)
    direct = os.path.join(root, SERVER_REL)
    if os.path.isfile(direct):
        return direct
    hits = glob.glob(os.path.join(root, "**", "server.py"), recursive=True)
    hits = [h for h in hits if os.path.basename(os.path.dirname(h)) == "serve"]
    if not hits:
        return ""
    hits.sort(key=os.path.getmtime, reverse=True)
    return hits[0]


def find_python(cfg: Optional[Dict[str, Any]] = None) -> str:
    """定位跑 server.py 的 python。

    Strata 自带 .venv (里面才有 numpy / 引擎依赖), 所以优先用它;
    找不到就用当前解释器 —— 那是「用户在 PowerShell 里直接跑」的情况。
    """
    cfg = cfg if cfg is not None else config.load()
    explicit = str((cfg.get("strata") or {}).get("python") or "").strip().strip('"')
    if explicit and os.path.isfile(explicit):
        return explicit
    root = install_dir(cfg)
    venv = os.path.join(root, VENV_PYTHON_REL)
    if os.path.isfile(venv):
        return venv
    return sys.executable or "python"


def port_of(cfg: Dict[str, Any],
            entry: Optional[Dict[str, Any]] = None) -> int:
    """Strata 后端该听哪个端口 —— **它自己那份配置里写的**。

    `serve/server.py` 只看命令行的 `--port` (bind 那行是
    `Server((a.host, a.port))`), json 里的 `"port"` 它自己根本不读 ——
    它平时是靠 `run-*.bat` 传 `--port` 才固定在 8080 的。所以这里照它
    bat 的做法: 从**这个档位**的 json 里读出那个值再传回去, 等于「按它
    原本的方式启动」, 而不是网关自己编一个端口出来。

    读不到就用 `serve/server.py` 的 argparse 默认 8095。
    """
    entry = entry if entry is not None else active_model(cfg)
    if entry:
        try:
            data = load_tier(entry)
            val = data.get("port")
            if val not in (None, ""):
                return int(val)
        except Exception:                                # noqa: BLE001
            pass
    return STRATA_DEFAULT_PORT


# serve/server.py 里 --port 的 argparse 默认值
STRATA_DEFAULT_PORT = 8095


def ready_timeout_of(cfg: Dict[str, Any]) -> float:
    """Strata 加载很慢, 就绪超时单独放大 (默认 15 分钟)。"""
    st = (cfg or {}).get("strata") or {}
    try:
        return float(st.get("ready_timeout", 900) or 900)
    except (TypeError, ValueError):
        return 900.0


# ----------------------------------------------------------------------
# 命令行
# ----------------------------------------------------------------------
def build_args(cfg: Dict[str, Any], entry: Dict[str, Any]) -> List[str]:
    """server.py 的参数。

    只给 `--host/--port` 这两个网络参数, **取值来自它自己的配置**
    (端口见 `port_of`)。引擎参数一个不碰: `--engine strata --config <json>`
    之后, serve/server.py 会把那个 json 的 args 原样透传给 strata.exe。
    """
    st = (cfg or {}).get("strata") or {}
    args = [
        find_server(cfg),
        "--engine", "strata",
        "--config", config_path_of(entry),
        "--host", "127.0.0.1",
        "--port", str(port_of(cfg, entry)),
    ]
    if st.get("lazy"):
        # 进程先起、模型不加载: 第一次请求才 load (省 35-55GB 内存)
        args.append("--lazy")
    if st.get("api_monitor"):
        args.append("--api-monitor")
    gpu = st.get("gpu")
    if gpu is not None and str(gpu).strip() != "":
        args += ["--gpu", str(gpu).strip()]
    key = str(st.get("api_key") or "").strip()
    if key:
        # 空 key 会被 Strata 当成"关掉鉴权"直接退出 (见 serve/server.py), 所以只给非空
        args += ["--api-key", key]
    return args


def command_line(cfg: Dict[str, Any], entry: Dict[str, Any]) -> str:
    return subprocess.list2cmdline(build_args(cfg, entry))


# ----------------------------------------------------------------------
# 扫描 install_dir 里的 strata-*.json (登记新档位用)
# ----------------------------------------------------------------------
def scan_configs(cfg: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """列出 install_dir 下能直接当档位用的 json。

    认两种: `strata-<档位>.json` (SETUP/自己建的), 以及任何含
    `exe` + `model_name` 的 json —— 这两个字段就是 Strata 引擎配置的必要条件。
    读不动的 (坏 json / 缺字段) 也会列出来, 带 ok=False 和原因, 让 UI 提示。
    """
    root = install_dir(cfg)
    out: List[Dict[str, Any]] = []
    if not root or not os.path.isdir(root):
        return out
    for path in sorted(glob.glob(os.path.join(root, "*.json"))):
        base = os.path.basename(path)
        if base in ("package.json", "tsconfig.json") or base.endswith(".log"):
            continue
        item: Dict[str, Any] = {
            "path": path,
            "file": base,
            "id": "",
            "ok": False,
            "why": "",
            "model_name": "",
            "aliases": [],
            "vision": False,
            "max_context": "",
        }
        try:
            with open(path, "r", encoding="utf-8-sig") as f:
                data = json.load(f)
        except Exception as e:                        # noqa: BLE001
            item["why"] = f"读不了: {e}"
            out.append(item)
            continue
        if not isinstance(data, dict) or not data.get("exe") or not data.get("model_name"):
            item["why"] = "不是 Strata 引擎配置 (缺 exe / model_name)"
            out.append(item)
            continue
        item["model_name"] = str(data.get("model_name") or "")
        item["id"] = item["model_name"]
        aliases = data.get("aliases") or []
        if isinstance(aliases, str):
            aliases = [a.strip() for a in aliases.split(",") if a.strip()]
        item["aliases"] = [str(a) for a in aliases]
        item["vision"] = bool(data.get("vision"))
        eng = data.get("args") or []
        for i, a in enumerate(eng):
            if a == "--max-context" and i + 1 < len(eng):
                item["max_context"] = str(eng[i + 1])
        # exe / tokenizer 的路径按 json 里的 cwd 解析 (Strata 自己也是这么做的)
        cwd = data.get("cwd") or root
        exe = str(data["exe"])
        if not os.path.isabs(exe):
            exe = os.path.abspath(os.path.join(cwd, exe))
        if not os.path.isfile(exe):
            item["why"] = f"引擎不存在: {exe}"
            out.append(item)
            continue
        tok = str(data.get("tokenizer") or "")
        if not os.path.isfile(os.path.join(tok, "vocab.json")):
            item["why"] = f"tokenizer 不完整 (缺 vocab.json): {tok or '(空)'}"
            out.append(item)
            continue
        item["ok"] = True
        out.append(item)
    return out


def entry_from_file(path: str) -> Dict[str, Any]:
    """把一个 strata-*.json 变成 config.json 里的档位条目。"""
    with open(path, "r", encoding="utf-8-sig") as f:
        data = json.load(f)
    mid = str(data.get("model_name") or os.path.splitext(os.path.basename(path))[0])
    aliases = data.get("aliases") or []
    if isinstance(aliases, str):
        aliases = [a.strip() for a in aliases.split(",") if a.strip()]
    alias = str(aliases[0]) if aliases else mid
    return {
        "id": mid,
        "alias": alias,
        "enabled": True,
        "runtime": config.STRATA_RUNTIME,
        "strata_config": os.path.abspath(path),
        "params": {},
    }


# ----------------------------------------------------------------------
# 档位参数: 直接编辑 strata-<档位>.json
#
# Strata 的 `args` 数组就是给 strata.exe 的命令行 (serve/server.py 的
# engine_args() 原样透传), 所以「调参数」= 改这个数组, 而这个 json 就是
# 唯一真相。这里不维护任何影子副本: 读的是它、写回的也是它, 我们没管到的
# 键 (vision / lib_dirs / layer_split ...) 原样保留。
#
# 刻意**不**硬编一份完整的 flag 白名单 —— Strata 的 flag 还在快速增加
# (strata.exe --help 已有上百个), 白名单一 stale 就会把新参数当成不认识。
# 下面这组只是「值得在 UI 上摆出来」的常用项, 其余 token 一律原样带过。
# ----------------------------------------------------------------------
# (flag, kind, 标签, 说明)  kind: int | float | text | bool | choice
TIER_FLAGS: Tuple[Tuple[str, str, str, str], ...] = (
    ("--max-context", "int", "最大上下文",
     "KV/state 容量 (格)。越大越吃内存和显存"),
    ("--kv", "choice", "KV 精度",
     "fp16 最省事 / int8 显存减半 / q4_0 再减半 / k8v4 混合"),
    ("--kv-resident", "int", "KV 常驻格数",
     "每层留这么多格在显存, 其余流式放内存。0 = 全留显存 (min 20480)"),
    ("--expert-cache", "text", "显存专家数",
     "留多少专家常驻显存: auto = 按显存自动, 或直接写个数"),
    ("--expert-cache-per-layer", "bool", "每层独立槽位",
     "开: 每层有自己的槽位而不是被前几层抢光 (实测命中率更高)"),
    ("--prefill", "text", "预填分块",
     "auto / auto:16384 / 具体 token 数 (越大预填越快, 峰值内存越高)"),
    ("--spec", "int", "推测解码窗口",
     "一次猜几个 token。0 = 关; 4 左右是速度和质量的平衡点"),
    ("--spec-min-p", "float", "推测接受概率",
     "低于这个接受概率就不采用草稿"),
    ("--mtp", "path", "MTP 草稿目录",
     "草稿模型的目录 (留空则不用 MTP)"),
    ("--prompt-cache", "int", "对话检查点数",
     "在请求之间保留几个对话快照 (0 = 每次从头读)"),
    ("--conversation-cache-mib", "int", "对话缓存 MiB",
     "停放对话的内存预算, 0 = 关"),
    ("--rope-scaling", "choice", "RoPE 扩展",
     "none / linear / yarn —— 超过训练长度时用"),
    ("--pool-workers", "int", "CPU 专家线程",
     "0 = 所有物理核心。线程太多会跟别的程序抢"),
    ("--vram-reserve-mib", "int", "显存预留 MiB",
     "给显卡留多少不占用 —— 别的程序也要用显存时调大"),
    ("--pcie-frac", "float", "PCIe 占比",
     "0~1, 调大少走 PCIe (专家是从 SSD 读的, 影响首字延迟)"),
    ("--vision", "bool", "读图", "开: 引擎接受图片 (需要 vision 段的 mmproj)"),
)

_FLAG_KIND = {f[0]: (f[1], f[2], f[3]) for f in TIER_FLAGS}
_FLAG_ORDER = [f[0] for f in TIER_FLAGS]
# 这些是「这个档位是谁」的标识参数
PATH_FLAGS = ("--pack", "--native", "--ple-gguf", "--expert-profile")
# 其中这两个在 UI 的「基础信息」里可以手动改 (指向别的模型/分片);
# 另两个 (pack / expert-profile) 换了会指到别的包和专家档案, 只读。
EDITABLE_PATH_FLAGS = ("--native", "--ple-gguf")


def tier_mmproj(entry: Dict[str, Any]) -> str:
    """档位 json 的 vision.mmproj (读图档位才有)。"""
    try:
        return str((load_tier(entry).get("vision") or {}).get("mmproj") or "")
    except Exception:                                    # noqa: BLE001
        return ""


def set_tier_mmproj(path: str, mmproj: str) -> Tuple[bool, str]:
    """改档位 json 的 vision.mmproj。**没有 vision 段就不凭空造一个** ——
    造出来的段缺 exe/model, Strata 会起视觉编码器失败, 比没有更糟。"""
    if not os.path.isfile(path):
        return False, f"档位的 json 不存在: {path}"
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            data = json.load(f)
    except Exception as e:                                # noqa: BLE001
        return False, f"读 {os.path.basename(path)} 失败: {e}"
    vis = data.get("vision")
    if not isinstance(vis, dict):
        if not mmproj:
            return True, ""                              # 本来就没有, 也没让加
        return False, ("这个档位的 json 里没有 vision 段, 不能填 mmproj "
                       "(需要它自己带 mmproj 的那份配置)")
    if not mmproj:
        vis.pop("mmproj", None)
    else:
        vis["mmproj"] = mmproj
    try:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            json.dump(data, f, ensure_ascii=False, indent=_detect_indent(path))
        os.replace(tmp, path)
    except Exception as e:                                # noqa: BLE001
        return False, f"写 {os.path.basename(path)} 失败: {e}"
    return True, f"已更新 {os.path.basename(path)} 的 vision.mmproj"


def load_tier(entry: Dict[str, Any]) -> Dict[str, Any]:
    """读档位 json。失败时抛异常 (UI 负责弹窗)。"""
    path = config_path_of(entry)
    if not path or not os.path.isfile(path):
        raise FileNotFoundError(f"档位的 json 不存在: {path or '(空)'}")
    with open(path, "r", encoding="utf-8-sig") as f:
        return json.load(f)


def parse_tier_args(args: List[str]) -> Tuple[Dict[str, Any], Dict[str, str],
                                            List[str], List[str]]:
    """`args` 数组 -> (常用项, 标识路径, 其余 token, 原来的 flag 顺序)。

    布尔项出现即为真; 其余按「flag + 下一个 token 是它的值」拆。
    最后那个顺序表是为了 save 时**照原样排列**, 不因为在 UI 上编辑过就
    把用户的 json 重排一遍 (diff 一片噪音, 也不好回退)。
    """
    managed: Dict[str, Any] = {}
    paths: Dict[str, str] = {}
    others: List[str] = []
    order: List[str] = []
    i = 0
    n = len(args)
    while i < n:
        tok = args[i]
        if tok in _FLAG_KIND and _FLAG_KIND[tok][0] == "bool":
            managed[tok] = True
            order.append(tok)
            i += 1
            continue
        if tok in _FLAG_KIND and i + 1 < n:
            managed[tok] = args[i + 1]
            order.append(tok)
            i += 2
            continue
        if tok in PATH_FLAGS and i + 1 < n:
            paths[tok] = args[i + 1]
            order.append(tok)
            i += 2
            continue
        others.append(tok)
        i += 1
    return managed, paths, others, order


def _fmt_value(kind: str, value: Any) -> str:
    if kind == "bool":
        return ""
    return str(value).strip()


def build_tier_args(managed: Dict[str, Any], paths: Dict[str, str],
                    others: List[str],
                    order: Optional[List[str]] = None) -> List[str]:
    """(常用项, 标识路径, 其余) -> `args` 数组。

    * **认得的 flag 保持原来的相对顺序** (order), 新加的接在后面 —— 在 UI
      上改个数值不会把用户的 json 重排一遍 (diff 一片噪音, 也不好回退);
    * 值写成空的 (文本项留空 / 布尔关) 就整条丢掉, 所以「清空某项」= 删掉它;
    * **不认得的 token 原样保留, 但统一排在最后** —— 我们不知道它们夹在哪
      两个 flag 中间才是对的, 与其猜不如靠后站 (Strata 按顺序解析 argv,
      位置本身无语义)。
    """
    seq = [f for f in (order or []) if f in PATH_FLAGS or f in _FLAG_KIND]
    seq += [f for f in list(PATH_FLAGS) + _FLAG_ORDER if f not in seq]
    out: List[str] = []
    for flag in seq:
        if flag in PATH_FLAGS:
            v = str(paths.get(flag) or "").strip()
            if v:
                out += [flag, v]
            continue
        if flag not in managed:
            continue
        kind = _FLAG_KIND[flag][0]
        if kind == "bool":
            if managed[flag]:
                out.append(flag)
            continue
        text = _fmt_value(kind, managed[flag])
        if text:
            out += [flag, text]
    out += [str(t) for t in others if str(t).strip()]
    return out


def _detect_indent(path: str) -> int:
    """照原样保留缩进, 别把用户的 json 重排一遍 (Strata 的 setup 写的是 1 空格)。"""
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            for line in f:
                if line.startswith((" ", "\t")):
                    return len(line) - len(line.lstrip(" \t"))
    except OSError:
        pass
    return 1


def save_tier(entry: Dict[str, Any], managed: Dict[str, Any],
              paths: Dict[str, str], others: List[str],
              order: Optional[List[str]] = None,
              top: Optional[Dict[str, Any]] = None) -> Tuple[bool, str]:
    """把参数写回档位 json。**只改 args 和显式给的顶层键**, 其余原样保留。

    写前留一份 .bak —— 这文件是唯一真相, 手滑改坏了要能退回去。
    """
    path = config_path_of(entry)
    if not path or not os.path.isfile(path):
        return False, f"档位的 json 不存在: {path or '(空)'}"
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            data = json.load(f)
    except Exception as e:                                # noqa: BLE001
        return False, f"读 {os.path.basename(path)} 失败: {e}"
    if not isinstance(data, dict):
        return False, f"{os.path.basename(path)} 不是 json 对象, 不敢改"
    data["args"] = build_tier_args(managed, paths, others, order)
    for k, v in (top or {}).items():
        data[k] = v
    try:
        with open(path, "r", encoding="utf-8") as f:      # 备份原样一份
            orig = f.read()
        with open(path + ".bak", "w", encoding="utf-8") as f:
            f.write(orig)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            json.dump(data, f, ensure_ascii=False,
                      indent=_detect_indent(path))
        os.replace(tmp, path)
    except Exception as e:                                # noqa: BLE001
        return False, f"写 {os.path.basename(path)} 失败: {e}"
    return True, (f"已保存到 {os.path.basename(path)}"
                  f" (备份 {os.path.basename(path)}.bak); "
                  f"需重启服务后生效 —— 加载 40GB 要 1-3 分钟")


# ----------------------------------------------------------------------
# 端口
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
    """问后端 /health; 没起来返回 None。"""
    import urllib.error
    import urllib.request
    url = f"http://127.0.0.1:{port_of(cfg)}/health"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            obj = json.loads(r.read().decode("utf-8", "replace"))
        return obj if isinstance(obj, dict) else None
    except Exception:
        return None


# ----------------------------------------------------------------------
# 日志 / 服务
# ----------------------------------------------------------------------
def out_log() -> str:
    return os.path.join(service.log_dir(), "strata.out.log")


def err_log() -> str:
    return os.path.join(service.log_dir(), "strata.err.log")


def status() -> str:
    return service.status(SERVICE_NAME)


def is_installed() -> bool:
    return service.is_installed(SERVICE_NAME)


def _check_ready(cfg: Dict[str, Any]) -> Tuple[bool, str, Dict[str, Any]]:
    """安装/启动前的公共校验。返回 (ok, msg, model)。"""
    model = active_model(cfg)
    if not model:
        return False, "没有 Strata 档位：请先在「🌐 Strata」页登记一个 strata-*.json。", {}
    srv = find_server(cfg)
    if not srv:
        root = install_dir(cfg)
        return False, f"未找到 serve/server.py (已找 {root or '默认目录'})。", model
    cfgp = config_path_of(model)
    if not cfgp or not os.path.isfile(cfgp):
        return False, f"档位的 json 不存在: {cfgp or '(空)'}", model
    return True, "", model


def install(cfg: Optional[Dict[str, Any]] = None,
            start_service: bool = True) -> Tuple[bool, str]:
    """「应用并重装」: 校验档位 -> 标 serve_mode=strata -> 装网关服务。

    和 KVMem 一样: Windows 服务指向的永远是网关 gateway.py, 网关占住
    config.port 按 model 字段转给三组后端之一。所以这里的「重装」只是让
    网关重启、读到新的 serve_mode 并拉起对应后端。
    """
    cfg = cfg if cfg is not None else config.load()
    cfg["serve_mode"] = config.STRATA_RUNTIME
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
                  f"Strata 后端 127.0.0.1:{port_of(cfg)})")


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
        return False, "运行服务未安装，请先在「🌐 Strata」页点「应用并重装」"
    if st in ("RUNNING", "START_PENDING"):
        return True, f"网关服务已在运行 (状态 {st})"
    cfg = config.load()
    if active_model(cfg):
        port = port_of(cfg)
        if port_busy(port):
            return False, (f"端口 {port} 已被占用 (多半是手动跑的 "
                           f"run-*.bat / START-HERE.bat)，请先关掉它。")
    return service.start(SERVICE_NAME, log_hint=err_log())


def stop() -> Tuple[bool, str]:
    return service.stop(SERVICE_NAME)


def restart() -> Tuple[bool, str]:
    if not service._is_admin():
        return False, service.need_admin_hint()
    if status() == "NOT_INSTALLED":
        return False, "运行服务未安装，请先在「🌐 Strata」页点「应用并重装」"
    return service.restart(SERVICE_NAME, log_hint=err_log())


def run_foreground(cfg: Optional[Dict[str, Any]] = None) -> Tuple[bool, str]:
    """直接拉起一个 Strata 进程 (不走服务, 便于看输出 / 不需要管理员)。"""
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
    print(f"[strata] {py} {command_line(cfg, model)}")
    try:
        proc = subprocess.Popen([py] + args, cwd=install_dir(cfg),
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except Exception as e:                             # noqa: BLE001
        return False, f"启动失败: {e}"
    return True, (f"已在后台启动 Strata (pid={proc.pid}), 加载完成前 /health "
                  f"不通; 日志看 {os.path.basename(err_log())}")