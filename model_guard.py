"""模型自动卸载守护 (LRU by load time)

背景 (llama.cpp router 模式, build 10685):
  * 当前模型**空闲**时, router 会在新请求 join 的 tick 里自己换模型:
        `tick: evicting idle LRU name=X for a queued request`
  * 但旧模型**正在生成**时 (`req_count != 0`), `pick_victim()` 会跳过它,
    新模型的请求只能排队, 客户端很快超时:
        `join: request for name=Y queued at position 1`
        `got exception: {"error":{"code":500,"message":"request cancelled while waiting...}}`

本模块补上"旧模型正在被使用"这一段:
  1. 盯 logs/router.err.log, 找出"用户正在请求某个还没常驻的模型"(wanted);
  2. GET /models 拿到常驻集合 (status.value in loaded/sleeping) 和子进程端口;
  3. GET <子进程>/slots 的 `is_processing` 判断该模型是否正在被使用;
  4. 按 load time 取最旧的常驻模型 (排除 wanted 自己) 作为 victim:
        - 空闲 -> 直接 POST /models/unload (router 自己也会做, 这里保证确定性);
        - 忙   -> 按 busy_policy 处理:
            ask  (默认, GUI 里) -> 弹窗让用户选
                 「是」立即卸载, 加载最新模型
                 「否」保留到用完再自动卸载
                 「取消」本次不处理
            wait -> 直接等它用完再卸载 (无 GUI 时 ask 也降级成 wait)
            now  -> 立即卸载

用法:
  * UI 里后台线程自动跑 (ui.py 负责弹窗);
  * 独立前台: `py model_guard.py` (无 GUI, ask 降级为 wait);
  * 单测直接 `ModelGuard(...).step()`, 全部 IO 都可 monkeypatch。
"""
import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.request
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

# ----------------------------------------------------------------------
# router.err.log 行解析
# ----------------------------------------------------------------------
# 时间戳形如 `31.11.701.682` (分.秒.毫秒.微秒), 只用来定位日志行, 不做计算
RE_TS = re.compile(r"^\d+\.\d+\.\d+\.\d+\s")
RE_SPAWN = re.compile(r"load:\s+spawning server instance with name=(.+?) on port (\d+)")
RE_EXIT = re.compile(r"run:\s+instance name=(.+?) exited with status")
RE_JOIN = re.compile(r"join:\s+request for name=(.+?) queued at position")
RE_WAIT = re.compile(r"ensure_model:\s+waiting until model name=(.+?) is fully loaded")
RE_LOAD = re.compile(r"ensure_model:\s+slot available, loading queued model name=(.+)")

# 常驻状态 (这些状态占用 models_max 的一个名额)
RESIDENT_STATES = ("loaded", "sleeping")

# 桌面版守护的心跳: ui.py **独立线程**每秒写一次, 网关读它来决定要不要让位。
# 5s 太紧: 一次 GC / 一次磁盘卡顿就能让心跳 miss 掉, 于是服务端守护误判
# 「桌面版死了」抢着接管, 心跳一恢复又让位 —— 成对刷屏。给足余量。
GUARD_BEAT_NAME = "guard_ui.beat"
GUARD_BEAT_MAX_AGE = 20.0


def ui_guard_alive(logs_dir: str, max_age: float = GUARD_BEAT_MAX_AGE) -> bool:
    """桌面版守护还在跑吗。活着就让给它: 只有它能弹 busy_policy=ask 的窗,
    两边同时卸载会出现"弹窗已答、但模型早被另一边卸了"的竞态。"""
    try:
        age = time.time() - os.path.getmtime(os.path.join(logs_dir, GUARD_BEAT_NAME))
    except OSError:
        return False
    return age < max_age


def _port_of(args: Any) -> Optional[int]:
    """从 status.args 里取出 `--port` 的值。"""
    if not isinstance(args, (list, tuple)):
        return None
    for i, a in enumerate(args):
        if a == "--port" and i + 1 < len(args):
            try:
                return int(args[i + 1])
            except (TypeError, ValueError):
                return None
        if isinstance(a, str) and a.startswith("--port="):
            try:
                return int(a.split("=", 1)[1])
            except (IndexError, ValueError):
                return None
    return None


def _http_get(url: str, timeout: float = 5.0) -> Tuple[int, str]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        try:
            body = e.read().decode("utf-8", "replace")
        except Exception:
            body = ""
        return e.code, body
    except Exception:
        return 0, ""


def _http_post(url: str, body: dict, timeout: float = 5.0) -> Tuple[int, str]:
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        try:
            return e.code, e.read().decode("utf-8", "replace")
        except Exception:
            return e.code, ""
    except Exception:
        return 0, ""


# ----------------------------------------------------------------------
# 日志尾巴
# ----------------------------------------------------------------------
class LogWatcher:
    """增量读 logs/router.err.log, 维护 "常驻顺序" 和 "正在等谁" 两张表。

    * `load_order`: 模型名 -> 递增序号 (最后加载的序号最大), 缺项视为最旧 (-1)。
    * `wanted`:     模型名 -> 最后一次被请求的墙钟时间 (供 wanted_ttl 过滤)。

    首次 poll 会把整个文件当历史回放一遍, 只建 load_order, 不记 wanted
    (否则一启动就会拿老日志里的陈年请求去触发卸载)。
    """

    def __init__(self, path: str) -> None:
        self.path = path
        self.pos = 0
        self._partial = b""
        self._replay = True
        self.load_order: Dict[str, int] = {}
        self.wanted: Dict[str, float] = {}
        self._seq = 0

    # -- 常驻顺序 ------------------------------------------------------
    def order_of(self, name: str) -> int:
        return self.load_order.get(name, -1)

    # -- 读日志 --------------------------------------------------------
    def poll(self, now: Optional[float] = None) -> int:
        """读取新增内容, 返回本次处理的行数。"""
        if now is None:
            now = time.time()
        try:
            size = os.path.getsize(self.path)
        except OSError:
            return 0
        if self.pos > size:      # NSSM 轮转 / 截断
            self.pos = 0
            self._partial = b""
        try:
            with open(self.path, "rb") as f:
                f.seek(self.pos)
                data = f.read()
                self.pos = f.tell()
        except OSError:
            return 0
        if not data and not self._partial:
            if self._replay:
                self._replay = False
            return 0
        buf = self._partial + data
        chunks = buf.split(b"\n")
        self._partial = chunks.pop()
        n = 0
        for raw in chunks:
            line = raw.decode("utf-8", "replace").rstrip("\r")
            self.feed(line, now)
            n += 1
        if self._replay:
            self._replay = False
        return n

    def feed(self, line: str, now: float) -> Optional[Tuple[str, str]]:
        """解析一行日志并更新内部状态, 返回 (事件, 模型名)。"""
        m = RE_SPAWN.search(line)
        if m:
            name = m.group(1).strip()
            self.load_order[name] = self._seq
            self._seq += 1
            self.wanted.pop(name, None)
            return ("spawn", name)

        m = RE_EXIT.search(line)
        if m:
            name = m.group(1).strip()
            self.load_order.pop(name, None)
            self.wanted.pop(name, None)
            return ("exit", name)

        m = RE_LOAD.search(line)
        if m:
            name = m.group(1).strip()
            self.wanted.pop(name, None)
            return ("load", name)

        m = RE_JOIN.search(line) or RE_WAIT.search(line)
        if m:
            name = m.group(1).strip()
            if self._replay:
                # 历史回放, 只为了让 load_order 完整
                return ("wanted-old", name)
            self.wanted[name] = now
            return ("wanted", name)
        return None


# ----------------------------------------------------------------------
# 守护本体
# ----------------------------------------------------------------------
class ModelGuard(threading.Thread):
    """发现 "要加载新模型但旧模型正在用" 时, 按 LRU + busy_policy 决定怎么换。"""

    def __init__(
        self,
        base: str,
        models_max: Union[int, Callable[[], int]],
        log_path: str,
        *,
        busy_policy: str = "ask",
        poll_interval: float = 1.0,
        wanted_ttl: float = 30.0,
        ask_fn: Optional[Callable[[str, str], str]] = None,
        log_fn: Optional[Callable[[str], None]] = None,
        http_timeout: float = 5.0,
        watcher: Optional[LogWatcher] = None,
        active_fn: Optional[Callable[[], bool]] = None,
        paused_msg: Optional[str] = None,
        resume_msg: Optional[str] = None,
    ) -> None:
        super().__init__(name="model-guard", daemon=True)
        self.base = base.rstrip("/")
        self.models_max = models_max
        self.watcher = watcher if watcher is not None else LogWatcher(log_path)
        self.busy_policy = (busy_policy or "ask").strip().lower()
        self.poll_interval = max(0.2, float(poll_interval))
        self.wanted_ttl = max(1.0, float(wanted_ttl))
        self.ask_fn = ask_fn
        self.log_fn = log_fn
        self.http_timeout = float(http_timeout)
        # 返回 False -> 本线程只空转: 换挡是 router 的 /models 语义,
        # KVMem 运行时是单槽, 没有「常驻集合/卸载」这回事。
        self.active_fn = active_fn
        self.paused_msg = paused_msg or (
            "当前运行时不是主池, 换挡已暂停 (主池一跑起来就自动恢复)")
        self.resume_msg = resume_msg or "换挡已恢复"

        self.deferred: Dict[str, str] = {}   # victim -> wanted (用户选了"等用完")
        self.asked: Dict[str, str] = {}      # victim -> wanted (用户选了"本次不处理")
        self._stop_evt = threading.Event()
        self._warned = set()

    # ---------------- 线程 ----------------
    def stop(self) -> None:
        self._stop_evt.set()

    def run(self) -> None:
        while not self._stop_evt.is_set():
            try:
                if self.active_fn is not None and not self.active_fn():
                    self._warned.discard("__resumed")
                    self._warn_once("__paused", self.paused_msg)
                else:
                    if "__paused" in self._warned:
                        self._warned.discard("__paused")
                        self._warn_once("__resumed", self.resume_msg)
                    self.step()
            except Exception as e:                      # noqa: BLE001
                self._log(f"异常 {type(e).__name__}: {e}")
            self._stop_evt.wait(self.poll_interval)

    # ---------------- 工具 ----------------
    def _log(self, msg: str) -> None:
        if self.log_fn:
            try:
                self.log_fn(msg)
            except Exception:
                pass

    def _warn_once(self, key: str, msg: str) -> None:
        if key in self._warned:
            return
        self._warned.add(key)
        self._log(msg)

    def max_models(self) -> int:
        v = self.models_max
        if callable(v):
            try:
                v = v()
            except Exception:
                v = 1
        try:
            return max(1, int(v))
        except (TypeError, ValueError):
            return 1

    # ---------------- HTTP ----------------
    def snapshot(self) -> Optional[List[Dict[str, Any]]]:
        """GET /models -> [{id, state, port, args}], 失败返回 None。"""
        code, body = _http_get(f"{self.base}/models", timeout=self.http_timeout)
        if code != 200:
            return None
        try:
            data = json.loads(body)
        except Exception:
            return None
        items = data.get("data") if isinstance(data, dict) else data
        if not isinstance(items, list):
            return None
        out: List[Dict[str, Any]] = []
        for m in items:
            if not isinstance(m, dict):
                continue
            st = m.get("status") if isinstance(m.get("status"), dict) else {}
            args = st.get("args") or []
            out.append({
                "id": str(m.get("id", "")),
                "state": str(st.get("value", "")),
                "args": args,
                "port": _port_of(args),
            })
        return out

    def is_busy(self, rec: Dict[str, Any]) -> Optional[bool]:
        """子进程 /slots 的 is_processing。True=忙 False=闲 None=查不到(保守不动作)。"""
        if rec.get("state") == "sleeping":
            return False
        port = rec.get("port")
        if not port:
            self._warn_once("no-port", f"模型 {rec.get('id')} 拿不到子进程端口, 本次跳过判断")
            return None
        code, body = _http_get(f"http://127.0.0.1:{port}/slots", timeout=3.0)
        if code != 200:
            self._warn_once(
                "no-slots",
                f"子进程 /slots 不可用 (HTTP {code}), 守护不再主动卸载, 交给 router 自己处理",
            )
            return None
        try:
            slots = json.loads(body)
        except Exception:
            return None
        if not isinstance(slots, list):
            return None
        for s in slots:
            if not isinstance(s, dict):
                continue
            if s.get("is_processing"):
                return True
            for nt in (s.get("next_token") or []):
                if isinstance(nt, dict) and nt.get("has_next_token"):
                    return True
        return False

    def unload(self, model_id: str) -> bool:
        code, body = _http_post(
            f"{self.base}/models/unload", {"model": model_id}, timeout=10.0)
        if 200 <= code < 300:
            self._log(f"已卸载模型 {model_id} (HTTP {code})")
            return True
        if "not found" in (body or "").lower():
            # router 自己已经换掉了, 视作成功
            return True
        self._log(f"卸载失败 {model_id}: HTTP {code} {(body or '')[:160]}")
        return False

    # ---------------- 核心决策 ----------------
    def _lru(self, resident: List[Dict[str, Any]],
             exclude: set) -> Optional[Dict[str, Any]]:
        cands = [m for m in resident if m["id"] not in exclude]
        if not cands:
            return None
        cands.sort(key=lambda m: self.watcher.order_of(m["id"]))
        return cands[0]

    def _policy(self, victim: str, wanted: str) -> str:
        p = self.busy_policy
        if p == "now":
            return "now"
        if p == "wait":
            return "wait"
        if self.ask_fn is None:
            self._warn_once(
                "no-gui",
                "busy_policy=ask 但没有弹窗回调 (无 GUI), 自动降级为 wait",
            )
            return "wait"
        try:
            ans = self.ask_fn(victim, wanted)
        except Exception as e:                          # noqa: BLE001
            self._log(f"弹窗失败, 降级为 wait: {e}")
            ans = "wait"
        return ans if ans in ("now", "wait", "skip") else "skip"

    def _resident(self, snap: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        return [m for m in snap if m.get("state") in RESIDENT_STATES]

    def step(self) -> None:
        """一次完整评估。可单独调用 (单测/无 GUI 场景)。"""
        self.watcher.poll()
        now = time.time()

        # 1) 过期的 wanted
        for n in [n for n, t in self.watcher.wanted.items()
                  if now - t > self.wanted_ttl]:
            del self.watcher.wanted[n]

        snap = self.snapshot()
        if snap is None:
            return
        resident = self._resident(snap)
        resident_ids = {m["id"] for m in resident}
        state_by_id = {m["id"]: m.get("state", "") for m in snap}

        # 2) 已经加载 / 正在加载 -> 请求被满足
        for n in list(self.watcher.wanted):
            if n in resident_ids or state_by_id.get(n) == "loading":
                del self.watcher.wanted[n]
        wanted = [n for n in self.watcher.wanted if n not in resident_ids]

        # 3) 清理过期的决策
        for k in [k for k in self.deferred if k not in resident_ids]:
            del self.deferred[k]
        for k, v in list(self.asked.items()):
            if k not in resident_ids or v not in self.watcher.wanted:
                del self.asked[k]

        if not resident:
            return

        # 4) 上一轮选了"保留到用完" -> 一空闲就卸
        for vic in list(self.deferred):
            if vic not in resident_ids:
                del self.deferred[vic]
                continue
            rec = next((m for m in resident if m["id"] == vic), None)
            if rec is None:
                continue
            if self.is_busy(rec) is False:
                tgt = self.deferred.pop(vic)
                if self.unload(vic):
                    self._log(f"{vic} 已用完, 自动卸载 -> 切换到 {tgt}")
                resident = [m for m in (self.snapshot() or [])
                            if m.get("state") in RESIDENT_STATES]
                resident_ids = {m["id"] for m in resident}

        # 5) 容量满且还有 wanted -> 每轮最多腾一个位置 (一次 unload)
        limit = self.max_models()
        if wanted and len(resident) >= limit:
            victim = self._lru(resident, set(wanted))
            if victim is not None:
                vid = victim["id"]
                if vid in self.deferred or self.asked.get(vid) == wanted[0]:
                    pass                              # 用户已表态, 等下一轮
                else:
                    busy = self.is_busy(victim)
                    if busy is None:
                        pass                          # 查不到 -> 不动, 交给 router
                    elif not busy:
                        self.unload(vid)              # 空闲: 直接换
                    else:
                        action = self._policy(vid, wanted[0])
                        if action == "now":
                            self.unload(vid)
                        elif action == "wait":
                            self.deferred[vid] = wanted[0]
                            self._log(f"{vid} 正在使用中, 等它用完再卸载 (目标 {wanted[0]})")
                        else:                         # skip
                            self.asked[vid] = wanted[0]
                            self._log(f"{vid} 正在使用中, 本次不处理")


# ----------------------------------------------------------------------
# 独立运行
# ----------------------------------------------------------------------
def main(argv: Optional[List[str]] = None) -> int:
    import config as _config          # 延迟导入, 单测导入本模块时不碰 config
    cfg = _config.load()
    mg = cfg.get("model_guard") or {}
    if not mg.get("enabled", True):
        print("[model-guard] 未启用 (config.json -> model_guard.enabled=false)", flush=True)
        return 0
    import service as _service
    if ui_guard_alive(_service.log_dir()):
        print("[model-guard] 桌面版 UI 正在跑 (由它来弹窗), 独立进程退出", flush=True)
        return 0
    if _service.serve_mode_of(cfg) == "kvmem":
        print("[model-guard] 当前运行时是 KVMem 档位 (单槽), 换挡逻辑不适用, 退出",
              flush=True)
        return 0
    policy = str(mg.get("busy_policy", "ask")).strip().lower()
    if policy == "ask":
        print("[model-guard] 无 GUI, busy_policy=ask 降级为 wait", flush=True)
        policy = "wait"
    base = f"http://127.0.0.1:{int(cfg.get('port', 8080))}"
    guard = ModelGuard(
        base=base,
        models_max=lambda: int(cfg.get("models_max", 1)),
        log_path=os.path.join(_config.app_dir(), "logs", "router.err.log"),
        busy_policy=policy,
        poll_interval=float(mg.get("poll_interval_seconds", 1)),
        wanted_ttl=float(mg.get("wanted_ttl_seconds", 30)),
        log_fn=lambda m: print(f"[model-guard] {m}", flush=True),
    )
    print(f"[model-guard] 启动 base={base} policy={policy} "
          f"interval={guard.poll_interval}s ttl={guard.wanted_ttl}s", flush=True)
    guard.start()
    try:
        while True:
            time.sleep(1.0)
    except KeyboardInterrupt:
        print("[model-guard] 收到 Ctrl+C, 退出", flush=True)
    finally:
        guard.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
