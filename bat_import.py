"""从已有的 .bat 启动脚本导入模型 / KVMem 档位配置。

用户手头可能已经写好了一批启动 bat (比如分支目录里的 start-*.bat)。
这个模块把 bat 里的**每一个**参数原样搬进 config.json：

  * 解析 `set K=V` / `cd /d` / `^` 续行 / 注释 / `pause` 等 bat 语法；
  * 找到真正调用 server 的那一行, 拆成 token；
  * 用 `-h` 输出反查每个 flag 的长名 (短名 -c/-ctk/-a 等归一) 和「是否吃值」,
    于是 `-n -1` 这种负数值不会被误判成 flag；
  * 长名在 schema 里 -> 写进模型 params；在字段位 (-m/--mmproj/--alias)
    -> 写进模型字段；--host/--port -> 只记录在导入报告里 (主池与 KVMem
    已统一用 config.host/config.port, 不照抄 bat 的值)；两边都没有 ->
    **自动登记成「额外参数」** (user_preferences), 这样 bat 里的冷门 flag
    也不会丢, 照样能进 preset / kvmem 命令行。

按调用的 exe 分流：
  llama-kvmem-server.exe -> runtime=kvmem 的档位 (⚡ KVMem)
  llama-server.exe       -> 主池模型条目
  其它 exe               -> 拒绝, 不猜
"""
import os
import re
import subprocess
import json
from typing import Any, Dict, List, Optional, Tuple

import config

KVMEM_EXE = "llama-kvmem-server.exe"
POOL_EXE = "llama-server.exe"

# 落到模型条目字段, 不进 params
FIELD_OF = {"model": "model", "mmproj": "mmproj", "alias": "alias"}
# 服务级参数: 只记录不落盘 (主池与 KVMem 分支统一用 config.host/config.port)
SERVER_OF = {"host": "host", "port": "port"}
# 草稿快捷字段 -> 模型顶层 draft_* 字段
DRAFT_FIELD_OF = {
    "spec-type": "draft_type",
    "spec-draft-model": "draft_model",
    "spec-draft-n-max": "draft_n_max",
}

_METAVAR = re.compile(r"^[A-Z][A-Z0-9_]*(?:[,/-][A-Z0-9_]+)*$")
_TOKEN = re.compile(r'"[^"]*"|\S+')

_HELP_CACHE: Dict[Tuple[str, int, int], Tuple[Dict[str, str], Dict[str, bool]]] = {}


# ----------------------------------------------------------------------
# bat 文本
# ----------------------------------------------------------------------
def read_text(path: str) -> str:
    raw = open(path, "rb").read()
    for enc in ("utf-8-sig", "utf-8"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    for enc in ("gbk", "cp936", "utf-16"):
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, UnicodeError):
            continue
    return raw.decode("utf-8", "replace")


def logical_lines(text: str) -> List[str]:
    """把 `^` 续行拼成逻辑行。bat 里 server 调用是一整串续行。"""
    out: List[str] = []
    buf = ""
    for raw in text.splitlines():
        s = raw.rstrip("\r\n")
        if s.endswith("^"):
            buf += s[:-1].rstrip() + " "
            continue
        out.append(buf + s)
        buf = ""
    if buf:
        out.append(buf)
    return out


def tokenize(s: str) -> List[str]:
    return [t[1:-1] if len(t) >= 2 and t[0] == '"' and t[-1] == '"' else t
            for t in _TOKEN.findall(s)]


def is_flag(t: str) -> bool:
    """`-1` 是数值不是 flag, 所以只认字母 / `-` 开头的。"""
    return len(t) > 1 and t[0] == "-" and (t[1].isalpha() or t[1] == "-")


class Script:
    __slots__ = ("path", "exe", "argv", "env", "cwd", "unresolved", "notes")

    def __init__(self, path: str) -> None:
        self.path = path
        self.exe = ""
        self.argv: List[str] = []
        self.env: Dict[str, str] = {}
        self.cwd = ""
        self.unresolved: List[str] = []
        self.notes: List[str] = []


def parse_script(path: str) -> Script:
    sc = Script(path)
    for line in logical_lines(read_text(path)):
        s = line.strip()
        if not s:
            continue
        low = s.lower()
        if low.startswith("::") or low.startswith("rem ") or low == "rem":
            continue
        if low.startswith("rem"):
            continue
        if low in ("@echo off", "echo off", "@echo.", "echo on"):
            continue
        if low.startswith("set ") or low.startswith("@set "):
            body = s.split(None, 1)[1] if " " in s else ""
            k, sep, v = body.partition("=")
            if sep and k.strip():
                sc.env[k.strip().lstrip("@").lstrip("%").rstrip("%")] = v.strip()
            continue
        if low.startswith("cd ") or low.startswith("@cd ") or low.startswith("pushd "):
            body = s.split(None, 1)[1]
            body = re.sub(r"^/d\s+", "", body, flags=re.I)
            sc.cwd = body.strip().strip('"')
            continue
        if low in ("pause", "exit", "goto :eof") or low.startswith("goto "):
            continue
        if low.startswith("if ") or low.startswith("for ") or low.startswith("call "):
            sc.notes.append(f"复杂控制流未解析, 已跳过: {s[:60]}")
            continue
        if sc.exe:
            sc.notes.append(f"第二个可执行调用被忽略: {s[:60]}")
            continue
        # 可能是 server 调用行
        if is_flag(s.split()[0]) or " " not in s:
            sc.notes.append(f"无法识别的行, 已跳过: {s[:60]}")
            continue
        toks = tokenize(s)
        exe = toks[0]
        if not exe.lower().endswith(".exe"):
            sc.notes.append(f"首个 token 不是 exe, 已跳过: {s[:60]}")
            continue
        sc.exe = exe
        sc.argv = toks[1:]
    for i, t in enumerate(sc.argv):
        for m in re.finditer(r"%[^%\s]+%", t):
            sc.unresolved.append(m.group(0))
    if not sc.exe:
        raise ValueError("bat 里没有找到 .exe 调用行")
    return sc


# ----------------------------------------------------------------------
# -h -> flag 元数据
# ----------------------------------------------------------------------
def help_text(exe: str) -> str:
    try:
        r = subprocess.run([exe, "-h"], capture_output=True, timeout=90)
    except Exception:
        return ""
    return ((r.stdout or b"").decode("utf-8", "replace")
            + (r.stderr or b"").decode("utf-8", "replace"))


def _known_names() -> set:
    return set(config.SCHEMA_INDEX) | {e["key"] for e in config.extra_params()}


def parse_help(text: str) -> Tuple[Dict[str, str], Dict[str, bool]]:
    """-> (by_flag: 原始flag -> 长名, takes: 原始flag -> 是否吃值)。"""
    by_flag: Dict[str, str] = {}
    takes: Dict[str, bool] = {}
    for line in text.splitlines():
        if not line.strip() or not line.lstrip().startswith("-"):
            continue
        # flag 列: 描述前一定有 2+ 空格 (llama 的 -h 是对齐的)
        col = re.split(r"\s{2,}", line.lstrip(), maxsplit=1)[0]
        groups = [g.strip() for g in re.split(r",\s*|\s+/\s+", col) if g.strip()]
        flags: List[str] = []
        has_meta = False
        for g in groups:
            parts = g.split()
            if not parts or not is_flag(parts[0]) and not parts[0].startswith("--"):
                continue
            f0 = parts[0]
            if len(f0) < 2 or f0[1] not in "-abcdefghijklmnopqrstuvwxyz":
                continue
            flags.append(f0)
            if len(parts) > 1 and _METAVAR.match(parts[1]):
                has_meta = True
        if not flags:
            continue
        # 同一行的所有 flag 共享同一个「是否吃值」;
        # 规范名: 先挑已知的 (schema/额外参数), 再挑去掉 no- 后已知的,
        # 否则取最后一个非 --no- 的 (llama 习惯把真名写在前面/后面都有)。
        longs = [f for f in flags if f.startswith("--")]
        cand = [f for f in longs if not f.startswith("--no-")] or longs or flags
        known = _known_names()
        names = [f.lstrip("-") for f in cand]
        canon = next((n for n in reversed(names) if n in known), None)
        if canon is None:
            # `--no-mmproj-offload` 单独成行时, 规范名是它去掉 no- 的那个
            canon = next((n[3:] for n in reversed(names)
                          if n.startswith("no-") and n[3:] in known), None)
        if canon is None:
            canon = names[-1]
        for f in flags:
            by_flag.setdefault(f, canon)
            takes[f] = has_meta
    return by_flag, takes


def resolve_exe(sc: "Script", fallback_dirs: Optional[List[str]] = None) -> str:
    """bat 里通常写裸名 (靠 `cd /d` 进 bin), 拿去跑 -h 前必须还原成真实路径。"""
    exe = (sc.exe or "").strip().strip('"')
    base = os.path.basename(exe)
    cands: List[str] = []
    if os.path.isabs(exe):
        cands.append(exe)
    if sc.cwd:
        cands.append(os.path.join(sc.cwd, base))
        cands.append(os.path.join(sc.cwd, exe) if not os.path.isabs(exe) else exe)
    for d in (fallback_dirs or []):
        if d:
            cands.append(os.path.join(d, base))
            cands.append(os.path.join(d, "bin", base))
    cands.append(exe)
    for c in cands:
        if c and os.path.isfile(c):
            return os.path.abspath(c)
    return exe


def flag_meta(exe: str) -> Tuple[Dict[str, str], Dict[str, bool]]:
    try:
        st = os.stat(exe)
        key = (exe, int(st.st_mtime), int(st.st_size))
    except OSError:
        key = (exe, 0, 0)
    if key in _HELP_CACHE:
        return _HELP_CACHE[key]
    meta = parse_help(help_text(exe))
    _HELP_CACHE[key] = meta
    return meta


def _schema_keys() -> set:
    return set(config.SCHEMA_INDEX) | {e["key"] for e in config.extra_params()}


def resolve_key(raw: str, by_flag: Dict[str, str],
                schema: set) -> Tuple[Optional[str], bool, bool]:
    """原始 flag -> (配置 key, 是否取反(--no-x), 是否认识)。"""
    key = by_flag.get(raw)
    if key is None:
        if raw.startswith("--no-"):
            key = raw[5:]
        else:
            return None, False, False
    if key.startswith("no-") and key[3:]:
        if key in schema:
            return key, False, True          # --no-host 这种独立存在的 flag
        return key[3:], True, True           # --no-mmproj-offload -> mmproj-offload=false
    neg = raw.startswith("--no-") and raw.lstrip("-") != key
    return key, neg, True


# ----------------------------------------------------------------------
# 解析出的参数
# ----------------------------------------------------------------------
class Parsed:
    __slots__ = ("fields", "server", "draft", "params", "unknown", "notes")

    def __init__(self) -> None:
        self.fields: Dict[str, str] = {}
        self.server: Dict[str, str] = {}
        self.draft: Dict[str, str] = {}
        self.params: Dict[str, str] = {}      # key -> value (值可为空字符串)
        self.unknown: List[str] = []          # schema 里没有、要登记成额外参数的
        self.notes: List[str] = []


def parse_args(sc: Script, schema: Optional[set] = None,
               exe_path: Optional[str] = None) -> Parsed:
    schema = schema if schema is not None else _schema_keys()
    by_flag, takes = flag_meta(exe_path or sc.exe)
    out = Parsed()
    toks = list(sc.argv)
    i = 0
    while i < len(toks):
        t = toks[i]
        if not is_flag(t):
            out.notes.append(f"多余的位置参数被忽略: {t}")
            i += 1
            continue
        if "=" in t and t.startswith("--"):
            flag, _, inline = t.partition("=")
            val: Optional[str] = inline
            i += 1
        else:
            flag = t
            tv = takes.get(flag)
            if tv is None:
                tv = not (i + 1 >= len(toks) or is_flag(toks[i + 1]))
            if tv:
                if i + 1 < len(toks):
                    val = toks[i + 1]
                    i += 2
                else:
                    val = ""
                    out.notes.append(f"{flag} 缺少取值, 按空值处理")
                    i += 1
            else:
                val = None
                i += 1

        # 短名归一 + 取反识别
        raw_key = flag.lstrip("-")
        key = by_flag.get(flag) or raw_key
        neg = False
        rk, neg2, _ok = resolve_key(flag, by_flag, schema)
        if rk:
            key, neg = rk, neg2

        if neg:
            value = "false"
        elif val is None:
            value = "true"                   # 纯布尔 flag
        else:
            value = val

        if key in FIELD_OF:
            out.fields[FIELD_OF[key]] = value
        elif key in SERVER_OF:
            out.server[SERVER_OF[key]] = value
        elif key in DRAFT_FIELD_OF:
            out.draft[DRAFT_FIELD_OF[key]] = value
            out.params[key] = value
        else:
            if key not in schema:
                out.unknown.append(key)      # -> 登记成额外参数, 一个都不丢
            out.params[key] = value
    out.unknown = list(dict.fromkeys(out.unknown))
    return out


# ----------------------------------------------------------------------
# 造模型条目
# ----------------------------------------------------------------------
def _slug(text: str) -> str:
    s = re.sub(r"[\s\\/:*?\"<>|]+", "-", (text or "").strip()).strip("-")
    return s or "imported"


def target_of(exe: str) -> str:
    base = os.path.basename(exe.strip('"')).lower()
    if base == KVMEM_EXE:
        return "kvmem"
    if base == POOL_EXE:
        return "pool"
    raise ValueError(f"暂不支持从 {base or exe} 导入 (只认 {KVMEM_EXE} / {POOL_EXE})")


def build_model(sc: Script, p: Parsed, target: str) -> Dict[str, Any]:
    stem = os.path.splitext(os.path.basename(sc.path))[0]
    mid = _slug(stem)
    model: Dict[str, Any] = {
        "id": mid,
        "alias": p.fields.get("alias") or mid,
        "model": p.fields.get("model", ""),
        "mmproj": p.fields.get("mmproj", ""),
        "enabled": True,
        "runtime": config.KVMEM_RUNTIME if target == "kvmem" else "",
        # **不设置 kvmem_port。** bat 的 --port 只进导入报告 (端口统一读
        # config.port), 所以这个字段不是 bat 的产物。以前这里写死 "" ,
        # 覆盖已有档位时就把用户手工设的 kvmem_port 清掉 —— 三个 bat 覆盖
        # 同一个档位, 于是出现「被命中的档位端口空了、没命中的档位还留着」
        # 这种自相矛盾的结果。新建档位时 config.load() 的规范化会补 ""。
        "params": {},
    }

    # 草稿快捷字段: bat 是唯一真相, 顶层和 params 双写 (与 UI「保存」保持一致)
    if p.draft:
        model["draft_enabled"] = True
        model["draft_en"] = {k: True for k in config.DRAFT_SHORTCUT_KEYS}
    model["draft_model"] = p.draft.get("draft_model", "")
    model["draft_type"] = p.draft.get("draft_type", "")
    model["draft_n_max"] = p.draft.get("draft_n_max", "3")

    # 先按 schema 补齐所有 key, 再**全部关掉** —— bat 没写的参数就不该生效
    config._ensure_model_params(model)
    for k in list(model["params"]):
        model["params"][k]["enabled"] = False
    for k, v in p.params.items():
        model["params"][k] = {"enabled": True, "value": v}
    # 快捷字段同步一份进 params (主池 preset 从这里读 spec-draft-n-max 等)
    for k, field in DRAFT_FIELD_OF.items():
        if field in p.draft:
            model["params"][k] = {"enabled": True, "value": p.draft[field]}
    if not model["model"]:
        raise ValueError("bat 里没有 -m/--model 模型路径, 无法导入")
    return model


def _norm_path(p: str) -> str:
    s = os.path.normpath(str(p or "").strip().strip('"')).replace("/", "\\")
    return os.path.normcase(s)


# 区分「同一个 gguf 的不同档位」的关键参数 —— 用户的 kvmem 三档就是
# 靠这三个 + kvmem-budget 区分的 (路径全都一样)
_MATCH_KEYS = ("kvmem-budget", "ctx-size", "kvmem-gen-reserve", "reasoning-budget")


def _match_score(old: Dict[str, Any], new: Dict[str, Any]) -> int:
    """路径不同 -> -1; 相同 -> 关键参数对上的个数。"""
    want = _norm_path(new.get("model", ""))
    if not want or want != _norm_path(old.get("model", "")):
        return -1
    op, np_ = old.get("params") or {}, new.get("params") or {}
    # 同一个 gguf 就算"路径相同", 命中底分就是 1 —— 调用方按 `score > 0` 挑
    # 最高分那一档。底分 0 的话, 当 bat 的预算跟配置里全不一样时 (比如配置里
    # 手动调大了 kvmem-budget), 同路径也会被判成"没匹配上" -> 平白多出一个
    # 同模型的重复档位, 把原档位的 id 顶掉。
    score = 1
    for k in _MATCH_KEYS:
        a, b = op.get(k), np_.get(k)
        if (isinstance(a, dict) and isinstance(b, dict)
                and a.get("enabled") and b.get("enabled")
                and str(a.get("value")) == str(b.get("value"))):
            score += 1
    return score


def apply_to_config(cfg: Dict[str, Any], model: Dict[str, Any],
                    server: Dict[str, str], target: str) -> Tuple[bool, str]:
    """写回 cfg。匹配顺序: 同 id -> 同 runtime 且同模型路径 + 关键参数打分。
    后者是为了不凭空多出一个重复档位 (kvmem 三档共用一个 gguf,
    光看路径分不出谁是谁)。返回 (是否新建, 覆盖原因)。"""
    models = cfg.setdefault("models", [])
    # 匹配池: 默认就是实时的 models, 但 import_bats 会传入**批次开始前的快照**。
    # 为什么要快照: 匹配打分依赖 _MATCH_KEYS (kvmem-budget / ctx-size /
    # gen-reserve / reasoning-budget), 而合并会把这些字段改成当前 bat 的值。
    # 于是同一批里第二个 bat 看到的是「被第一个 bat 改过」的档位, 打分结果
    # 取决于 bat 之间的先后 -> 再导入一次匹配对象就漂移了, 幂等性被破坏。
    # 对快照匹配 = 这一批里每个 bat 都基于同一份初始状态做决定, 结果稳定。
    match_pool = cfg.get("__match_pool__") if cfg.get("__match_pool__") is not None else models
    idx, reason = -1, "new"
    for i, m in enumerate(match_pool):
        if m.get("id") == model.get("id"):
            idx, reason = i, "id"
            break
    if idx < 0:
        best_i, best_s = -1, 0
        for i, m in enumerate(match_pool):
            if m.get("runtime", "") != model.get("runtime", ""):
                continue
            s = _match_score(m, model)
            if s > best_s:
                best_i, best_s = i, s
        if best_i >= 0:
            idx, reason = best_i, "model-path"

    if idx < 0:
        models.append(model)
        # 新建的同步进匹配池, 保持与 models 的下标一一对应
        if match_pool is not models:
            match_pool.append(model)
        added = True
    else:
        old = models[idx] or {}
        if reason == "model-path":
            # 保留原来的 id/alias, 否则 kvmem.active_id 会失效
            model["id"] = old.get("id") or model["id"]
            model["alias"] = old.get("alias") or model["alias"]
        # **合并, 不是整体替换。** 直接 `models[idx] = model` 会把旧条目里
        # bat 根本没有的字段全部抹掉 (用户写的 _note / api_key / base_url /
        # 手工调过的 draft_* …)。三个 bat 指向同一个 gguf 时会反复覆盖同一档位,
        # 于是「导一次丢一批用户数据」, 而且导两次和导一次结果不同 ——
        # 幂等性直接被破坏。改成: bat 提供的字段覆盖, bat 没提供的保留旧的。
        merged = dict(old)
        merged.update(model)
        # params 同理按 key 合并, 否则整个参数字典被换成 bat 的那份
        _op = dict(old.get("params") or {})
        _op.update(model.get("params") or {})
        merged["params"] = _op
        models[idx] = merged
        added = False

    # server (bat 的 --host/--port) 不写回配置: 主池与 KVMem 分支已统一
    # 用顶层的 host/port, 运行时一律读 config.host / config.port。
    return added, reason


def _infer_kind(value: str) -> str:
    v = str(value).strip()
    if v.lower() in ("true", "false", "1", "0", "yes", "no", "on", "off"):
        return "bool"
    if re.fullmatch(r"-?\d+", v):
        return "int"
    if re.fullmatch(r"-?\d*\.\d+", v):
        return "float"
    return "text"


def register_extra_params(keys: List[str], target: str,
                          values: Dict[str, str]) -> List[str]:
    """把 schema 里没有的 flag 登记成额外参数, 一个都不丢。"""
    if not keys:
        return []
    prefs = config.load_prefs()
    existing = list(prefs.get("extra_params") or [])
    have = {e["key"] for e in existing}
    scope = "kvmem" if target == "kvmem" else "model"
    added = []
    for key in keys:
        if not key or key in have or key in config.SCHEMA_INDEX:
            continue
        existing.append({
            "key": key,
            "label": key,
            "scope": scope,
            "kind": _infer_kind(values.get(key, "")),
            "default": str(values.get(key, "")),
            "category": "advanced",
            "description": f"从 bat 导入的参数: --{key}",
        })
        have.add(key)
        added.append(key)
    prefs["extra_params"] = existing
    config.save_prefs(prefs)
    return added


# ----------------------------------------------------------------------
# 顶层入口
# ----------------------------------------------------------------------
def import_bat(cfg: Dict[str, Any], path: str) -> Dict[str, Any]:
    """导入单个 bat。返回报告 dict; 会原地改 cfg。"""
    sc = parse_script(path)
    target = target_of(sc.exe)
    exe = resolve_exe(sc, [cfg.get("llama_dir", ""),
                           (cfg.get("kvmem") or {}).get("install_dir", "")])
    p = parse_args(sc, exe_path=exe)

    report: Dict[str, Any] = {
        "path": path,
        "id": _slug(os.path.splitext(os.path.basename(path))[0]),
        "target": target,
        "exe": exe,
        "notes": list(sc.notes) + list(p.notes),
        "unknown": list(p.unknown),
        "env": dict(sc.env),
    }
    if not flag_meta(exe)[0]:
        report["notes"].append(
            f"读不到 {os.path.basename(exe)} 的 -h, 短参数归一可能不完整")
    if sc.unresolved:
        report["notes"].append("含未解析的环境变量: " + ", ".join(
            dict.fromkeys(sc.unresolved)))

    # 1) 先把不认识的 flag 登记成额外参数 (要在 _ensure_model_params 之前)
    values = {k: p.params.get(k, "true") for k in p.unknown}
    registered = register_extra_params(p.unknown, target, values)
    if registered:
        report["notes"].append("已登记为额外参数: " + ", ".join(registered))

    # 2) 造条目并覆盖
    model = build_model(sc, p, target)
    added, reason = apply_to_config(cfg, model, p.server, target)
    report["id"] = model["id"]
    report["added"] = added
    report["overwrite"] = ("" if added else reason)
    report["model"] = model
    if reason == "model-path":
        report["notes"].append(
            f"未找到同 id 条目, 按「同一模型路径 + 关键档位参数」"
            f"覆盖了已有条目 {model['id']!r}")
    report["param_count"] = sum(
        1 for v in model["params"].values()
        if isinstance(v, dict) and v.get("enabled"))

    # 3) bat 的环境变量 -> kvmem 服务环境
    if target == "kvmem":
        kv = cfg.setdefault("kvmem", {})
        if "CUDA_VISIBLE_DEVICES" in sc.env:
            kv["cuda_device"] = sc.env["CUDA_VISIBLE_DEVICES"]
        if "CUDA_DEVICE_ORDER" in sc.env:
            kv["cuda_device_order"] = sc.env["CUDA_DEVICE_ORDER"]
        if p.server:
            got = " ".join(f"--{k} {v}" for k, v in p.server.items())
            report["notes"].append(
                f"{got} 已忽略 —— 与主池统一用 config.host/config.port "
                f"(在「全局」页改)")
    else:
        for k in ("CUDA_VISIBLE_DEVICES", "CUDA_DEVICE_ORDER"):
            if k in sc.env:
                report["notes"].append(
                    f"{k}={sc.env[k]} 无法落到主池条目 (服务级环境变量, 需手工处理)")
        if p.server:
            report["notes"].append(
                "--host/--port 是服务级参数, 未写入模型条目 (在「全局」页改)")
    return report


def import_bats(cfg: Dict[str, Any], paths: List[str]) -> List[Dict[str, Any]]:
    out = []
    # 批次开始前拍一份匹配快照 (见 apply_to_config 里的说明)。同一批里多个
    # bat 指向同一个 gguf 时, 它们必须基于同一份初始状态做匹配决策, 否则
    # 谁先谁后会互相改写 _MATCH_KEYS, 导致重复导入的结果和第一次不一致。
    cfg["__match_pool__"] = json.loads(json.dumps(cfg.get("models") or []))
    try:
        for p in paths:
            try:
                out.append(import_bat(cfg, p))
            except Exception as e:
                out.append({"path": p, "error": str(e), "notes": [], "unknown": []})
    finally:
        cfg.pop("__match_pool__", None)
    return out
