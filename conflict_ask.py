"""冲突询问: 服务端 <-> 桌面版 UI 的文件式 IPC。

## 为什么需要它

内部口 (默认 8080) 被**别的进程**占着时, 网关只有两个选择:
直接抢占, 或者问用户。你要求的是后者 —— 尤其当那个进程**正在跑模型**
(可能有人在对话中) 时, 强杀会把正在进行的工作打断。

## 为什么用文件而不是 HTTP

现有机制里UI 已经用 `logs/guard_ui.beat` 这个心跳文件跟服务端互通
(`model_guard.ui_guard_alive`), 沿用同一套约定:

  - 服务端写 `logs/conflict_<token>.req.json`
  - UI 发现这个文件 -> 弹窗 -> 写 `logs/conflict_<token>.ans.json`
  - 服务端轮询等这个答复, 超时就退化成「等用完」

好处: 不新增监听端口 (18080 之外的端口都该留给各自的软件), 不引新依赖,
UI 没开时自然走降级路径 —— 那个 `.req.json` 永远没人应答。

## 三个选项

  force  强行结束对方, 立刻接管
  wait   排队等它自己用完 (对方不再收新请求时自动腾出来)
  reject 直接拒绝这次切换, 报错回去
"""
from __future__ import annotations

import json
import os
import time
import uuid
from typing import Any, Callable, Dict, Optional

REQ_PREFIX = "conflict_"
REQ_SUFFIX = ".req.json"
ANS_SUFFIX = ".ans.json"

# 选项常量
FORCE = "force"
WAIT = "wait"
REJECT = "reject"
DEFAULT = WAIT


def conflict_dir() -> str:
    import config
    return os.path.join(config.app_dir(), "logs")


def _req_path(token: str) -> str:
    return os.path.join(conflict_dir(), REQ_PREFIX + token + REQ_SUFFIX)


def _ans_path(token: str) -> str:
    return os.path.join(conflict_dir(), REQ_PREFIX + token + ANS_SUFFIX)


def ask(
    question: Dict[str, Any],
    log: Optional[Callable[[str], None]] = None,
    timeout: float = 300.0,
    poll: float = 0.4,
) -> Optional[str]:
    """问 UI「这个占着内部口的进程怎么处理」。

    返回 force / wait / reject; **UI 没开时返回 None** (表示没人应答,
    调用方应走「等用完」这条安全路径 —— 绝不自动强杀)。

    `timeout` 之内没人应答也返回 None: UI 卡住/关掉了都不该让请求无限挂着。
    """
    def _log(m: str) -> None:
        if log:
            log(m)

    token = uuid.uuid4().hex[:12]
    req = dict(question)
    req["token"] = token
    req["created"] = time.time()
    try:
        os.makedirs(conflict_dir(), exist_ok=True)
    except Exception:                                    # noqa: BLE001
        return None

    req_file = _req_path(token)
    try:
        with open(req_file, "w", encoding="utf-8") as f:
            json.dump(req, f, ensure_ascii=False)
    except Exception as e:                               # noqa: BLE001
        _log(f"[网关] 写冲突询问失败({e}), 改用「等用完」")
        return None

    ans_file = _ans_path(token)
    deadline = time.time() + max(1.0, float(timeout))
    try:
        while time.time() < deadline:
            try:
                with open(ans_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                choice = str(data.get("choice") or "").strip().lower()
                if choice in (FORCE, WAIT, REJECT):
                    return choice
            except FileNotFoundError:
                pass
            except Exception:                            # noqa: BLE001
                pass
            time.sleep(max(0.05, float(poll)))
        _log("[网关] 等 UI 答复超时, 按「等用完」处理")
        return None
    finally:
        for p in (req_file, ans_file):
            try:
                os.remove(p)
            except OSError:
                pass


def pending() -> list:
    """UI 侧调用: 列出现在等答复的询问 (按时间先后)。"""
    d = conflict_dir()
    out = []
    try:
        names = os.listdir(d)
    except OSError:
        return out
    for n in names:
        if not (n.startswith(REQ_PREFIX) and n.endswith(REQ_SUFFIX)):
            continue
        tok = n[len(REQ_PREFIX):-len(REQ_SUFFIX)]
        if os.path.isfile(_ans_path(tok)):
            continue                       # 已经答过了, 等服务端收走
        try:
            with open(os.path.join(d, n), "r", encoding="utf-8") as f:
                out.append(json.load(f))
        except Exception:                # noqa: BLE001
            continue
    out.sort(key=lambda r: r.get("created") or 0)
    return out


def answer(token: str, choice: str) -> bool:
    """UI 侧调用: 交卷。"""
    choice = str(choice or "").strip().lower()
    if choice not in (FORCE, WAIT, REJECT):
        return False
    try:
        os.makedirs(conflict_dir(), exist_ok=True)
        with open(_ans_path(token), "w", encoding="utf-8") as f:
            json.dump({"choice": choice, "at": time.time()}, f)
        return True
    except Exception:                                    # noqa: BLE001
        return False