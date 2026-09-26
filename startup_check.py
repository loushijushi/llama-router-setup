"""启动前置检查：检测 Python、llama.cpp、tkinter、NSSM、模型文件。

两种调用方式：
  1) CLI: python startup_check.py  -> 人类可读的检查报告 (旧 .bat 调用方式)
  2) API: from startup_check import run_all; result = run_all()
           -> 结构化结果 [{key, name, ok, detail, fix_label, fix_fn}, ...]
           UI 用 API 显示"环境"页 + 一键修复。
"""
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Any, Callable, List, Optional

import config


# =========================================================================
# 结构化检查结果
# =========================================================================
@dataclass
class CheckItem:
    key: str
    name: str
    ok: bool
    detail: str = ""
    fix_label: str = ""
    fix_fn: Optional[Callable[[], tuple]] = None
    fix_target: str = ""  # "self" 自我修复 (如改config),  "external" 启动外部安装

    def to_dict(self) -> dict:
        return {
            "key": self.key, "name": self.name, "ok": self.ok,
            "detail": self.detail, "fix_label": self.fix_label,
            "fix_target": self.fix_target,
        }


# =========================================================================
# 修复函数
# =========================================================================
def fix_winget(pkg_id: str) -> tuple:
    """通过 winget 安装包。返回 (ok, msg)。"""
    if not shutil.which("winget"):
        return False, "winget 不可用 (Windows 10 1809 以下或未安装 App Installer)"
    # winget 需要管理员权限；Start-Process -Verb RunAs 提权
    try:
        # 用 ps 提权后运行；本进程不阻塞
        ps_cmd = (
            f"Start-Process winget -ArgumentList 'install --id {pkg_id} -e --accept-source-agreements --accept-package-agreements' "
            f"-Verb RunAs -Wait -WindowStyle Normal"
        )
        subprocess.Popen(
            ["powershell", "-NoProfile", "-Command", ps_cmd],
            creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0),
        )
        return True, f"已启动 winget 安装 {pkg_id} (会弹 UAC 窗口)，完成后请回到本窗口"
    except Exception as e:
        return False, f"启动 winget 失败: {e}"


def fix_open_url(url: str) -> tuple:
    try:
        os.startfile(url)  # type: ignore[attr-defined]
        return True, f"已打开浏览器: {url}"
    except Exception as e:
        return False, f"无法打开浏览器: {e}"


# =========================================================================
# 检查项
# =========================================================================
def check_python() -> CheckItem:
    v = sys.version_info
    ok = v >= (3, 8)
    detail = f"Python {v.major}.{v.minor}.{v.micro} ({sys.executable})"
    item = CheckItem(key="python", name="Python 3.8+", ok=ok, detail=detail)
    if not ok:
        item.fix_label = "用 winget 安装 Python 3.12"
        item.fix_fn = lambda: fix_winget("Python.Python.3.12")
        item.fix_target = "external"
    return item


def check_tkinter() -> CheckItem:
    try:
        import tkinter  # noqa: F401
        return CheckItem(key="tkinter", name="tkinter (GUI 库)", ok=True, detail="可用")
    except Exception as e:
        item = CheckItem(
            key="tkinter", name="tkinter (GUI 库)", ok=False,
            detail=f"不可用: {e}",
        )
        item.fix_label = "重新安装 Python (勾选 tcl/tk)"
        item.fix_fn = lambda: fix_winget("Python.Python.3.12")
        item.fix_target = "external"
        return item


def check_nssm() -> CheckItem:
    bundled = os.path.join(config.app_dir(), "tools", "nssm.exe")
    if os.path.isfile(bundled):
        return CheckItem(key="nssm", name="NSSM (服务管理)", ok=True,
                         detail=f"内置: {bundled}")
    if shutil.which("nssm"):
        return CheckItem(key="nssm", name="NSSM (服务管理)", ok=True,
                         detail="PATH 里的 nssm")
    item = CheckItem(key="nssm", name="NSSM (服务管理)", ok=False,
                     detail="未找到 (tools/nssm.exe 不存在，PATH 也没有)")
    if shutil.which("winget"):
        item.fix_label = "用 winget 安装 NSSM"
        item.fix_fn = lambda: fix_winget("NSSM.NSSM")
        item.fix_target = "external"
    elif shutil.which("choco"):
        item.fix_label = "用 choco 安装 NSSM"
        # choco 也需要管理员
        item.fix_fn = lambda: _fix_choco("nssm")
        item.fix_target = "external"
    else:
        item.fix_label = "打开 nssm.cc 手动下载"
        item.fix_fn = lambda: fix_open_url("https://nssm.cc/download")
        item.fix_target = "external"
    return item


def _fix_choco(pkg: str) -> tuple:
    if not shutil.which("choco"):
        return False, "choco 不可用"
    try:
        ps_cmd = (
            f"Start-Process choco -ArgumentList 'install {pkg} -y' "
            f"-Verb RunAs -Wait -WindowStyle Normal"
        )
        subprocess.Popen(
            ["powershell", "-NoProfile", "-Command", ps_cmd],
            creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0),
        )
        return True, f"已启动 choco 安装 {pkg}"
    except Exception as e:
        return False, f"启动 choco 失败: {e}"


def check_llama_dir() -> CheckItem:
    cfg = config.load()
    ld = (cfg.get("llama_dir") or "").rstrip("\\/")
    if not ld:
        item = CheckItem(key="llama_cpp", name="llama.cpp 二进制",
                         ok=False, detail="config.json 中 llama_dir 为空")
        item.fix_label = "在「全局」页设置 llama_dir"
        item.fix_target = "self"
        return item
    if not os.path.isdir(ld):
        item = CheckItem(key="llama_cpp", name="llama.cpp 二进制",
                         ok=False, detail=f"llama_dir 不存在: {ld}")
        item.fix_label = "打开 llama.cpp 下载页"
        item.fix_fn = lambda: fix_open_url("https://github.com/ggml-org/llama.cpp/releases")
        item.fix_target = "external"
        return item
    exe = os.path.join(ld, "llama-server.exe")
    if not os.path.isfile(exe):
        item = CheckItem(
            key="llama_cpp", name="llama.cpp 二进制", ok=False,
            detail=f"未找到 llama-server.exe: {exe}\n  请把 llama_dir 设为 llama.cpp 完整安装目录",
        )
        item.fix_label = "打开 llama.cpp 下载页"
        item.fix_fn = lambda: fix_open_url("https://github.com/ggml-org/llama.cpp/releases")
        item.fix_target = "external"
        return item
    return CheckItem(key="llama_cpp", name="llama.cpp 二进制", ok=True,
                     detail=f"{exe}")


def check_models() -> CheckItem:
    cfg = config.load()
    enabled = [m for m in cfg.get("models", []) if m.get("enabled", True)]
    if not enabled:
        return CheckItem(key="models", name="模型文件", ok=False,
                         detail="config.json 中没有启用的模型")
    missing: List[str] = []
    for m in enabled:
        mid = m.get("id", "?")
        path = m.get("model", "")
        if not path:
            missing.append(f"[{mid}] 未设置 model 路径")
        elif not os.path.isfile(path):
            missing.append(f"[{mid}] 模型文件不存在: {path}")
        mm = m.get("mmproj", "")
        if mm and not os.path.isfile(mm):
            missing.append(f"[{mid}] mmproj 不存在: {mm}")
        dm = m.get("draft_model", "")
        if dm and m.get("draft_enabled", bool(dm)) and not os.path.isfile(dm):
            missing.append(f"[{mid}] 草稿模型不存在: {dm}")
    if missing:
        return CheckItem(
            key="models", name="模型文件", ok=False,
            detail="\n".join(missing) + "\n  模型文件需从 HuggingFace 等来源手动下载",
        )
    return CheckItem(
        key="models", name="模型文件", ok=True,
        detail=f"{len(enabled)} 个启用模型，文件均存在",
    )


def check_admin() -> CheckItem:
    """是否以管理员运行。安装服务需要。"""
    try:
        import ctypes
        is_admin = bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        is_admin = False
    item = CheckItem(
        key="admin", name="管理员权限",
        ok=is_admin,
        detail="是" if is_admin else "否 (安装服务需要)",
    )
    if not is_admin:
        item.fix_label = "查看如何以管理员重启"
        item.fix_fn = lambda: fix_open_url("https://learn.microsoft.com/zh-cn/windows/uwp/get-started/enable-your-device-for-development")
        item.fix_target = "info"
    return item


# =========================================================================
# 汇总
# =========================================================================
def run_all() -> List[CheckItem]:
    return [
        check_python(),
        check_tkinter(),
        check_nssm(),
        check_llama_dir(),
        check_models(),
        check_admin(),
    ]


# =========================================================================
# 旧 CLI 入口 (保持兼容 .bat 调用)
# =========================================================================
def main() -> int:
    print("=" * 50)
    print("  llama.cpp Router 环境检查")
    print("=" * 50)
    print()

    items = run_all()
    errs: List[str] = []

    for it in items:
        mark = "✓" if it.ok else "✗"
        print(f"  [{mark}] {it.name}: {it.detail}")
        if not it.ok:
            if it.fix_label:
                errs.append(f"[{it.name}] {it.detail}\n   -> {it.fix_label}")
            else:
                errs.append(f"[{it.name}] {it.detail}")

    print()
    if errs:
        print("发现以下问题：")
        for e in errs:
            print("  - " + e)
        print()
        print("请按提示修复 (或启动主 UI 的「环境」页一键修复)。")
        return 1
    print("一切就绪 ✓")
    return 0


if __name__ == "__main__":
    sys.exit(main())
