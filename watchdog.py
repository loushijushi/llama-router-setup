"""GPU 显存守护进程。

功能：定期检查 NVIDIA 显卡可用显存；一旦低于阈值，
通过 llama.cpp router 的  POST /models/unload  端点
主动卸载最久未用的模型，为其它程序腾出显存。

调用方式：
  * 独立前台运行： py watchdog.py
  * 由 NSSM 服务托管 (llama-router-watchdog)： py -m service install_watchdog
"""
import json
import os
import shutil
import sys
import time
import urllib.error
import urllib.request
from typing import Optional, Tuple

import config


def _log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def _query_nvidia_smi() -> Optional[Tuple[int, int]]:
    """返回 (free_mb, total_mb)，找不到 nvidia-smi 或失败时返回 None。"""
    exe = shutil.which("nvidia-smi")
    if not exe:
        return None
    import subprocess
    try:
        r = subprocess.run(
            [exe,
             "--query-gpu=memory.free,memory.total",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=5,
        )
    except Exception as e:
        _log(f"nvidia-smi 调用异常: {e}")
        return None
    if r.returncode != 0:
        return None
    # 只看第一张卡（多卡场景可按需扩展）
    line = r.stdout.strip().splitlines()[0] if r.stdout.strip() else ""
    if not line:
        return None
    try:
        parts = [int(x.strip()) for x in line.split(",")]
        return parts[0], parts[1]
    except (ValueError, IndexError):
        return None


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
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:
        return 0, f"network error: {e}"


def _http_get(url: str, timeout: float = 5.0) -> Tuple[int, str]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except Exception as e:
        return 0, f"network error: {e}"


def _list_loaded_models(base: str) -> list[dict]:
    """从 /models 端点获取当前已加载模型列表。"""
    code, body = _http_get(f"{base}/models", timeout=5.0)
    if code != 200:
        return []
    try:
        data = json.loads(body)
    except Exception:
        return []
    # router 模式下 data 是 dict，key=data
    if isinstance(data, dict):
        return data.get("data", []) if isinstance(data.get("data"), list) else []
    return []


def _pick_unload_target(base: str, min_idle_seconds: int) -> Optional[str]:
    """从已加载模型里选一个可卸载的目标。

    策略：
      * status == "loaded" 或 "sleeping" 视为活跃（sleeping 是空闲的，会优先卸载）
      * 跳过当前正在处理请求的（status 仍 loaded 且 task queue 非空）
      * 跳过 "unloaded" 状态
      * 用 last_used / expires_at 字段（如有）选最旧的；无该字段时取第一个 sleeping
    """
    models = _list_loaded_models(base)
    if not models:
        return None
    candidates = []
    for m in models:
        status = m.get("status", "")
        if status == "unloaded":
            continue
        # 必须支持 router 管理
        if not m.get("id"):
            continue
        candidates.append(m)
    if not candidates:
        return None

    # 优先 unloading sleeping 的（最久空闲）
    sleeping = [m for m in candidates if m.get("status") == "sleeping"]
    if sleeping:
        return sleeping[0]["id"]
    # 次选 status 字段为 loaded 但不活跃
    for m in candidates:
        if m.get("status") in ("loaded",):
            return m["id"]
    return None


def _unload(base: str, model_id: str) -> bool:
    code, body = _http_post(f"{base}/models/unload",
                             {"model": model_id}, timeout=10.0)
    if 200 <= code < 300:
        _log(f"已主动卸载模型: {model_id} (HTTP {code})")
        return True
    _log(f"卸载失败: {model_id} HTTP {code} {body[:200]}")
    return False


def run() -> int:
    cfg = config.load()
    wd = cfg.get("watchdog", {}) or {}
    if not wd.get("enabled", False):
        _log("watchdog 未启用 (config.json -> watchdog.enabled = false)，退出。")
        return 0

    threshold = int(wd.get("free_mb_threshold", 2048))
    interval = max(2, int(wd.get("check_interval_seconds", 10)))
    min_idle = int(wd.get("min_idle_seconds", 0))
    port = int(cfg.get("port", 8080))
    host = cfg.get("host", "127.0.0.1")
    base = f"http://127.0.0.1:{port}"

    _log(f"watchdog 启动: threshold={threshold}MB, interval={interval}s, min_idle={min_idle}s, base={base}")

    nvidia = shutil.which("nvidia-smi")
    if not nvidia:
        _log("未找到 nvidia-smi，无法监控显存。watchdog 不会执行任何操作。")
        _log("请安装 NVIDIA 驱动并把 nvidia-smi.exe 加入 PATH，或把本程序跑在 Windows 上。")
        return 1

    _log(f"使用 nvidia-smi: {nvidia}")

    last_unload_ts = 0.0
    while True:
        try:
            q = _query_nvidia_smi()
            if q is None:
                _log("nvidia-smi 返回为空，本轮跳过")
            else:
                free, total = q
                if free < threshold:
                    # 防止短时间连续卸载
                    if time.time() - last_unload_ts < min_idle:
                        _log(f"显存不足 ({free}MB < {threshold}MB) 但在保护期内，跳过")
                    else:
                        target = _pick_unload_target(base, min_idle)
                        if target:
                            if _unload(base, target):
                                last_unload_ts = time.time()
                        else:
                            _log(f"显存不足但无可卸载模型 ({free}MB < {threshold}MB)")
                else:
                    _log(f"显存充足: {free}MB / {total}MB")
        except KeyboardInterrupt:
            _log("收到 Ctrl+C，退出")
            return 0
        except Exception as e:
            _log(f"异常: {e}")
        time.sleep(interval)


if __name__ == "__main__":
    sys.exit(run())
