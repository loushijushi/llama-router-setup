"""GPU 忙碌判据: 用**显存 + GPU 利用率**双重判断「模型还在不在用」。

## 为什么不用「在途请求」当判据

原来只看「已经进入转发阶段的请求数」。但**正在对话、此刻没有活跃请求**的
模型 (比如你在聊天框里打字、等它想) 在那个判据眼里就是空闲 —— 网关会以为没
人在用, 直接把它停掉, 正在进行的工作被强行打断。

## 判据

  * **显存还在**  -> 模型还加载着, 不能想当然地杀
  * **显存掉了**  -> 对方主动关了模型 (你在桌面端点了卸载 / 关了窗口),
                     这时候**立刻**就能起新的, 不用等
  * **GPU 忙**    -> 正在生成, 继续等
  * **GPU 空闲**  -> 没在生成, 计入「空闲 300 秒」的倒计时

阈值(用户定的):
  - 显存: **掉了 50%** 才算「对方主动让了」
  - GPU: **低于 20%** 算空闲 (刻意放宽 —— 瞬时采样会抖, 卡在 20% 比卡在 10%
    更能反映「真的在生成」)

## 为什么走 nvidia-smi 而不是 pynvml

这台机器上**没有装 pynvml / nvidia-ml-py**, 而逐进程显存查询
(`--query-compute-apps`) 在这台 Windows 上返回 `[N/A]` (驱动没给)。

**这正好不影响我们**: 只需要**总量**, `--query-gpu` 是能拿到的。

代价是每次采样要起一个 nvidia-smi 子进程 (约 100-300ms), 所以采样间隔不能
太密 —— 默认 5 秒。
"""
from __future__ import annotations

import re
import subprocess
import threading
import time
from typing import Callable, Dict, List, Optional

# ---------------------------------------------------------------- 阈值 (用户定)
# 显存掉一半才算「对方主动让了」。不要调得更严: FreeToken / Strata 关掉模型
# 后显存不一定归零 —— 桌面版本身、别的模型、CUDA context 都还占着一点。
VRAM_DROP_RATIO = 0.50
# GPU 利用率低于 20% 算空闲。刻意放宽, 因为瞬时采样抖得厉害。
GPU_IDLE_PCT = 20
# 连续几次采样都空闲才认 (防抖)。5s 一次 -> 约 25 秒。
IDLE_SAMPLES = 3
# 空闲多久后可以自动切换 (秒)
IDLE_GRACE = 300.0
# 采样间隔
SAMPLE_INTERVAL = 5.0


def _nvidia_smi(args: List[str], timeout: float = 5.0) -> Optional[str]:
    try:
        r = subprocess.run(["nvidia-smi"] + args, capture_output=True,
                           text=True, errors="replace", timeout=timeout)
        if r.returncode != 0:
            return None
        return r.stdout
    except Exception:                                    # noqa: BLE001
        return None


def sample_gpu() -> Optional[Dict[str, int]]:
    """采一次: {util_pct, mem_used_mib, mem_total_mib}。拿不到返回 None。"""
    out = _nvidia_smi(["--query-gpu=utilization.gpu,memory.used,memory.total",
                       "--format=csv,noheader,nounits"])
    if not out:
        return None
    line = out.strip().splitlines()[0] if out.strip() else ""
    if not line:
        return None
    nums = re.findall(r"-?\d+", line)
    if len(nums) < 3:
        return None
    try:
        return {"util_pct": int(nums[0]), "mem_used_mib": int(nums[1]),
                "mem_total_mib": int(nums[2])}
    except (ValueError, IndexError):
        return None


class BusyWatch:
    """盯着「等不等得到空位」。

    用法:
        w = BusyWatch(); w.arm()          # 记下当前显存作基线
        ...等一会儿...
        w.state()                          # 每次问它
    """

    def __init__(self, log: Optional[Callable[[str], None]] = None,
                 idle_grace: float = IDLE_GRACE,
                 vram_drop_ratio: float = VRAM_DROP_RATIO,
                 gpu_idle_pct: int = GPU_IDLE_PCT,
                 idle_samples: int = IDLE_SAMPLES,
                 interval: float = SAMPLE_INTERVAL) -> None:
        self.log = log
        self.idle_grace = float(idle_grace)
        self.vram_drop_ratio = float(vram_drop_ratio)
        self.gpu_idle_pct = int(gpu_idle_pct)
        self.idle_samples = int(idle_samples)
        self.interval = float(interval)
        self.base_mib: Optional[int] = None
        self.idle_since: Optional[float] = None
        self.last: Optional[Dict[str, int]] = None
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()

    # ---------- 生命周期 ----------
    def arm(self) -> None:
        """开始盯着 (切档前调一次, 记基线)。"""
        with self._lock:
            self.base_mib = None
            self.idle_since = None
        s = sample_gpu()
        if s:
            with self._lock:
                self.base_mib = s["mem_used_mib"]
                self.last = s
        self._start()

    def _start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="gpu-busy",
                                        daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.wait(self.interval):
            s = sample_gpu()
            if not s:
                continue
            with self._lock:
                self.last = s
                if self.base_mib is None:
                    self.base_mib = s["mem_used_mib"]
                busy_gpu = s["util_pct"] >= self.gpu_idle_pct
                if busy_gpu:
                    self.idle_since = None
                elif self.idle_since is None:
                    self.idle_since = time.time()

    # ---------- 判定 ----------
    def _snapshot(self) -> Optional[Dict[str, int]]:
        with self._lock:
            return dict(self.last) if self.last else None

    def vram_released(self) -> Optional[bool]:
        """显存是不是明显掉了 (掉够 50%)?

        True  = 对方主动让了 -> **可以立刻切**
        False = 还占着
        None  = 没采到数据
        """
        s = self._snapshot()
        with self._lock:
            base = self.base_mib
        if not s or not base:
            return None
        if base <= 0:
            return None
        return (base - s["mem_used_mib"]) >= base * self.vram_drop_ratio

    def idle_seconds(self) -> float:
        """GPU 已经连续空闲多久了。"""
        with self._lock:
            return 0.0 if self.idle_since is None else (time.time() - self.idle_since)

    def ready(self) -> Optional[str]:
        """能不能切了? 返回「切」的理由, 还不能切返回 None。

        规则 (按优先级):
          1. 显存掉了 >= 50%      -> "显存降了, 对方已让"   **立刻**
          2. GPU 空闲满 grace 秒 -> "GPU 空闲够久了"       到点自动
        """
        rel = self.vram_released()
        if rel:
            s = self._snapshot() or {}
            if self.log:
                self.log(f"[GPU] 显存从 {self.base_mib} 掉到 "
                         f"{s.get('mem_used_mib')} MiB "
                         f"(>={int(self.vram_drop_ratio * 100)}%), 对方已让 -> 可切")
            return "显存降了"
        if rel is False:
            idle = self.idle_seconds()
            if idle >= self.idle_grace:
                if self.log:
                    self.log(f"[GPU] 显存还在, 但 GPU 已空闲 {int(idle)}s "
                             f"(>= {int(self.idle_grace)}s) -> 到点自动切")
                return "GPU 空闲够久"
        return None

    def describe(self) -> str:
        s = self._snapshot()
        if not s:
            return "GPU 状态未知 (nvidia-smi 采不到)"
        base = self.base_mib if self.base_mib is not None else s["mem_used_mib"]
        return (f"显存 {s['mem_used_mib']}MiB (基线 {base}) / "
                f"GPU {s['util_pct']}% / 已空闲 {int(self.idle_seconds())}s")