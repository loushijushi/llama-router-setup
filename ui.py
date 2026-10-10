"""llama.cpp Router 可视化配置工具 (Tkinter)

标签页：
  1. 服务      安装/卸载/启停/状态/前台运行 + 操作日志
  2. 全局      llama_dir/host/port/... + 全局参数开关表
  3. 模型      增删改查模型条目 + 草稿模型 (MTP/DFlash) + 参数子表
  4. ⚡KVMem   llama-kvmem-server 分支的档位/服务/专属参数
  5. 偏好      自定义哪些是常用参数 + 自定义额外参数
  6. 环境      环境自检

参数面板通用特性：
  * 拆为"常用"和"扩展"两个区域，扩展默认折叠，点按钮展开
  * 每个参数：勾选 + 标签 + 值输入 + 问号按钮
  * 内部用 Canvas+Scrollbar 包裹，支持滚轮滚动
  * 悬停参数标签显示简短提示 (tooltip)
  * 点问号弹窗显示详细中文说明
"""
import os
import re
import subprocess
import sys
import threading
import time
import tkinter as tk
import urllib.request
import urllib.error
import json
from collections import deque
from tkinter import filedialog, messagebox, ttk
from typing import Any, Callable, Dict, List, Optional, Tuple

import bat_import
import config
import firewall
import generate_preset
import kvmem
import model_guard
import log_relay
import httpx
import service
import startup_check
import strata

APP_TITLE = "llama.cpp Router 管理 v3"

# 桌面版守护的心跳文件; 网关看到它在跳就让位, 避免两套守护同时卸载
GUARD_BEAT_FILE = os.path.join(config.app_dir(), "logs", model_guard.GUARD_BEAT_NAME)
# /health 判定的缓存时长: 守护每秒问一次, 没必要每次都开一条新连接
GUARD_HEALTH_TTL = 3.0


# ----------------------------------------------------------------------
# 监控窗口的「日志换行」开关
#
# 记在 user_preferences.json 里 —— 这是**看**日志的偏好, 不是配置的一部分,
# 存 config.json 会让配置文件每次看日志都被改写 (还容易和「保存」互相覆盖)。
# 默认**开**: 一行几百上千字符的日志 (整条 prompt、JSON) 不换行就得拖横向
# 滚动条, 而且横拖之后完全分不清哪一行是新来的。
# ----------------------------------------------------------------------
def _wrap_pref() -> bool:
    try:
        return bool(config.load_prefs().get("monitor_wrap_lines", True))
    except Exception:                                    # noqa: BLE001
        return True


def _save_wrap_pref(on: bool) -> None:
    try:
        prefs = config.load_prefs()
        if bool(prefs.get("monitor_wrap_lines", True)) == bool(on):
            return
        prefs["monitor_wrap_lines"] = bool(on)
        config.save_prefs(prefs)
    except Exception:                                    # noqa: BLE001
        pass


# =========================================================================
# 帮助窗口
# =========================================================================
def _canvas_sync(cv, inner_id, inner, busy=None):
    """给「canvas + 内嵌 frame」这类滚动区的 <Configure> 处理器用。

    原来的写法有三个问题, 拖动窗口时每帧都踩:
      1. `itemconfig(inner_id, width=...)` 会改内部窗口 -> **反向触发 inner
         的 <Configure>` -> 那里又 `bbox("all")` + `configure(scrollregion)`,
         两个 handler 每帧互相叫醒。
      2. 宽度没变时也照样 itemconfig, 白白重排。
      3. **拖动刚开始时是一串突发** `<Configure>` (窗口激活/阴影/首次定位),
         每一帧都 itemconfig 就会触发整块内嵌面板的**全量重排**; 而后面
         那些帧 Tk 有了缓存, 只做增量更新 —— 所以观感是「开头卡, 拖着顺」。
         itemconfig 会一路重排 inner 的所有子控件, 很贵。

    现在: 突发用 `after_idle` 合并成一帧一次; 拖动中 (`busy()`) 只记下目标
    宽度不重排, 松手后由 `flush()` 补一次。
    """
    def _on_inner(_e=None):
        try:
            box = cv.bbox("all")
            if box and tuple(round(v) for v in box) != _canvas_sync.last.get(cv):
                _canvas_sync.last[cv] = tuple(round(v) for v in box)
                cv.configure(scrollregion=box)
        except Exception:
            pass

    def _apply(want):
        if _canvas_sync.w.get(cv) == want:
            return
        _canvas_sync.w[cv] = want
        try:
            cv.itemconfig(inner_id, width=want)
        except Exception:
            pass

    def _flush():
        """把挂起的宽度应用掉 (拖动结束后调用)。"""
        _canvas_sync.ids.pop(cv, None)
        want = _canvas_sync.pending.pop(cv, None)
        if want:
            _apply(want)

    def _on_canvas(e):
        want = e.width
        if _canvas_sync.w.get(cv) == want:
            return
        _canvas_sync.pending[cv] = want
        # 拖动中 -> 不重排, 留到 flush
        if busy is not None and busy():
            return
        # (3) 合并突发: 一帧内来多少个 <Configure> 都只排一次 itemconfig
        prev = _canvas_sync.ids.get(cv)
        if prev is not None:
            try:
                inner.after_cancel(prev)
            except Exception:
                pass
        _canvas_sync.ids[cv] = inner.after_idle(_flush)

    return _on_inner, _on_canvas, _flush


_canvas_sync.last = {}
_canvas_sync.w = {}
_canvas_sync.pending = {}
_canvas_sync.ids = {}


class HelpWindow(tk.Toplevel):
    def __init__(self, master: tk.Tk) -> None:
        super().__init__(master)
        self.title("帮助 - llama.cpp Router")
        self.geometry("780x600")
        self.minsize(640, 400)
        path = config.README_PATH
        if not os.path.isfile(path):
            path = config.README_FALLBACK
        if not os.path.isfile(path):
            tk.Label(self, text="未找到安装指南.txt", fg="red").pack(padx=20, pady=20)
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()
        except Exception as e:
            tk.Label(self, text=f"读取失败: {e}", fg="red").pack(padx=20, pady=20)
            return
        header = ttk.Frame(self, padding=8)
        header.pack(fill=tk.X)
        ttk.Label(header, text=f"📄 {os.path.basename(path)}",
                  font=("Segoe UI", 11, "bold")).pack(side=tk.LEFT)
        ttk.Button(header, text="关闭", command=self.destroy).pack(side=tk.RIGHT)
        body = ttk.Frame(self)
        body.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 8))
        text = tk.Text(body, wrap=tk.WORD, font=("Consolas", 10))
        scroll = ttk.Scrollbar(body, orient=tk.VERTICAL, command=text.yview)
        text.configure(yscrollcommand=scroll.set)
        text.insert("1.0", content)
        text.configure(state="disabled")
        text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)


# =========================================================================
# 详细说明弹窗
# =========================================================================
class ParamDetailWindow(tk.Toplevel):
    def __init__(self, master, p: Dict[str, Any]) -> None:
        super().__init__(master)
        self.title(f"参数说明 - {p['key']}")
        self.geometry("540x360")
        self.minsize(420, 280)
        self.transient(master)
        self.grab_set()

        header = ttk.Frame(self, padding=10)
        header.pack(fill=tk.X)
        ttk.Label(header, text=p.get("label", p["key"]),
                  font=("Segoe UI", 11, "bold")).pack(side=tk.LEFT)
        ttk.Label(header, text=f"  ({p['key']})",
                  foreground="#888").pack(side=tk.LEFT)

        body = ttk.Frame(self, padding=(10, 0))
        body.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 8))
        text = tk.Text(body, wrap=tk.WORD, font=("Microsoft YaHei", 10),
                       height=10, padx=4, pady=4)
        scroll = ttk.Scrollbar(body, orient=tk.VERTICAL, command=text.yview)
        text.configure(yscrollcommand=scroll.set)
        text.insert("1.0", p.get("description") or "（暂无说明）")
        text.configure(state="disabled")
        text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)

        footer = ttk.Frame(self, padding=8)
        footer.pack(fill=tk.X)
        ttk.Label(footer, text=f"类型: {p.get('kind', 'text')}    "
                  f"作用域: {p.get('scope', 'model')}    "
                  f"默认: {p.get('default', '')}",
                  foreground="#555").pack(side=tk.LEFT)
        ttk.Button(footer, text="关闭", command=self.destroy).pack(side=tk.RIGHT)


# =========================================================================
# Tooltip
# =========================================================================
class ToolTip:
    def __init__(self, widget: tk.Widget, text: str) -> None:
        self.widget = widget
        self.text = text
        self.tip: Optional[tk.Toplevel] = None
        self.after_id: Optional[str] = None
        widget.bind("<Enter>", self._on_enter, add="+")
        widget.bind("<Leave>", self._on_leave, add="+")
        widget.bind("<ButtonPress>", self._on_leave, add="+")

    def _on_enter(self, _evt) -> None:
        self._cancel()
        self.after_id = self.widget.after(600, self._show)

    def _on_leave(self, _evt) -> None:
        self._cancel()
        self._hide()

    def _cancel(self) -> None:
        if self.after_id:
            try:
                self.widget.after_cancel(self.after_id)
            except Exception:
                pass
            self.after_id = None

    def _show(self) -> None:
        if self.tip or not self.text:
            return
        x = self.widget.winfo_rootx() + 20
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 4
        self.tip = tw = tk.Toplevel(self.widget)
        tw.wm_overrideredirect(True)
        tw.wm_geometry(f"+{x}+{y}")
        tk.Label(tw, text=self.text, justify=tk.LEFT,
                 background="#ffffe0", foreground="#222",
                 relief=tk.SOLID, borderwidth=1,
                 font=("Microsoft YaHei", 9), padx=6, pady=4,
                 wraplength=380).pack()

    def _hide(self) -> None:
        if self.tip:
            try:
                self.tip.destroy()
            except Exception:
                pass
            self.tip = None


# =========================================================================
# 参数行：勾选 + 标签 + 值输入 + 问号
# =========================================================================
class ParamRow:
    def __init__(self, master, p: Dict[str, Any], choices: List[str],
                 get: Callable[[str], Any], set_: Callable[[str, Any], None],
                 on_change: Optional[Callable[[], None]] = None) -> None:
        self.p = p
        self._get = get
        self._set = set_
        self._on_change = on_change
        self.enabled_var = tk.BooleanVar(value=False)
        self.value_var = tk.StringVar()

        f = ttk.Frame(master)
        f.columnconfigure(2, weight=1)
        ttk.Checkbutton(f, variable=self.enabled_var, width=2,
                        command=self._fire).grid(row=0, column=0, padx=(0, 4))

        lbl_text = f"{p['key']}  ·  {p.get('label', '')}"
        lbl = ttk.Label(f, text=lbl_text, width=32, anchor=tk.W, cursor="question_arrow")
        lbl.grid(row=0, column=1, padx=4, sticky=tk.W)
        ToolTip(lbl, self._short_tooltip())

        if p["kind"] == "choice":
            self.input_widget = ttk.Combobox(f, textvariable=self.value_var,
                                              width=24, values=choices)
        elif p["kind"] == "bool":
            self.input_widget = ttk.Combobox(f, textvariable=self.value_var,
                                              width=8, values=["true", "false"])
        else:
            self.input_widget = ttk.Entry(f, textvariable=self.value_var, width=26)
        self.input_widget.grid(row=0, column=2, sticky=tk.EW, padx=4)

        ttk.Button(f, text="?", width=3,
                   command=self._show_detail).grid(row=0, column=3, padx=(4, 0))

        self.frame = f
        self.value_var.trace_add("write", lambda *_: self._fire())
        self.enabled_var.trace_add("write", lambda *_: self._fire())

    def _short_tooltip(self) -> str:
        parts = [self.p.get("label", "")]
        hint = (self.p.get("hint") or "").strip()
        if hint:
            parts.append(f"({hint})")
        desc = (self.p.get("description") or "").strip().split("\n", 1)[0]
        if desc:
            parts.append("\n" + desc[:120] + ("…" if len(desc) > 120 else ""))
        return "  ".join(p for p in parts if p)

    def _show_detail(self) -> None:
        ParamDetailWindow(self.frame.winfo_toplevel(), self.p)

    def _fire(self) -> None:
        # 立即写回 cfg，勾选/输入时实时生效
        self.save()
        if self._on_change:
            self._on_change()

    def load(self) -> None:
        node = self._get(self.p["key"])
        if not isinstance(node, dict):
            return
        self.enabled_var.set(bool(node.get("enabled", False)))
        self.value_var.set(str(node.get("value", self.p["default"])))

    def save(self) -> None:
        self._set(self.p["key"], {
            "enabled": bool(self.enabled_var.get()),
            "value": self.value_var.get().strip(),
        })


# =========================================================================
# 参数面板（带 Canvas 滚动）
# =========================================================================
# ParamPanel 的作用域 -> 允许出现的参数 scope 集合
#   model      模型页 (主池)             = model + both
#   kvmem      旧写法, 与 kvmem_full 等价  = kvmem + both + model
#   kvmem_full 「⚡ KVMem」页 = 与模型页完全一致 + kvmem 专属项
_SCOPE_SET = {
    "model": frozenset({"model", "both"}),
    "kvmem": frozenset({"kvmem", "both"}),
    "kvmem_full": frozenset({"kvmem", "both", "model"}),
}


class ParamPanel(ttk.Frame):
    def __init__(self, master, *, scope: str, get, set_, refresh_outer) -> None:
        super().__init__(master, padding=0)
        self.scope = scope
        self._get = get
        self._set = set_
        self._refresh_outer = refresh_outer
        self._common_rows: Dict[str, ParamRow] = {}
        self._advanced_rows: Dict[str, ParamRow] = {}
        self._advanced_visible = False
        self._build()

    def _build(self) -> None:
        head = ttk.Frame(self)
        head.pack(fill=tk.X, pady=(0, 4))
        ttk.Label(head, text="参数", font=("Segoe UI", 10, "bold")).pack(side=tk.LEFT)
        self.toggle_btn = ttk.Button(head, text="▶ 显示扩展参数", command=self._toggle_advanced)
        self.toggle_btn.pack(side=tk.RIGHT)

        self._canvas = tk.Canvas(self, borderwidth=0, highlightthickness=0)
        self._scroll = ttk.Scrollbar(self, orient=tk.VERTICAL, command=self._canvas.yview)
        self._canvas.configure(yscrollcommand=self._scroll.set)
        self._scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self._canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self._inner = ttk.Frame(self._canvas)
        self._inner_id = self._canvas.create_window((0, 0), window=self._inner, anchor=tk.NW)
        # busy=_dragging: 拖动中只记下目标宽度不重排 (重排会一路带到底下所有
        # 子控件), 松手后由 _end_drag -> _canvas_flush 补一次。
        _oi, _oc, _fl = _canvas_sync(self._canvas, self._inner_id, self._inner,
                                     busy=lambda: getattr(self, "_dragging", False))
        self._inner.bind("<Configure>", _oi)
        self._canvas.bind("<Configure>", _oc)
        self._canvas_flush = _fl
        self._bind_mousewheel(self._canvas)
        self._bind_mousewheel(self._inner)

        self.common_box = ttk.LabelFrame(self._inner, text="常用参数 (日常调节)", padding=6)
        self.common_box.pack(fill=tk.X, pady=(0, 4), padx=2)
        self.advanced_box = ttk.LabelFrame(self._inner, text="扩展参数 (高级调优)", padding=6)

        self._rebuild()

    def _bind_mousewheel(self, widget) -> None:
        def _on_wheel(e):
            delta = -1 * int(e.delta / 120) if e.delta else 0
            if delta == 0:
                if e.num == 4:
                    delta = -1
                elif e.num == 5:
                    delta = 1
            if delta:
                self._canvas.yview_scroll(delta, "units")
        widget.bind("<MouseWheel>", _on_wheel, add="+")
        widget.bind("<Button-4>", _on_wheel, add="+")
        widget.bind("<Button-5>", _on_wheel, add="+")

    def _toggle_advanced(self) -> None:
        if self._advanced_visible:
            self.advanced_box.pack_forget()
            self.toggle_btn.configure(text="▶ 显示扩展参数")
            self._advanced_visible = False
        else:
            self.advanced_box.pack(fill=tk.X, pady=(4, 4), padx=2)
            self.toggle_btn.configure(text="▼ 隐藏扩展参数")
            self._advanced_visible = True
        self._inner.update_idletasks()
        self._canvas.configure(scrollregion=self._canvas.bbox("all"))

    def show_advanced(self, show: bool = True) -> None:
        if show and not self._advanced_visible:
            self._toggle_advanced()
        elif not show and self._advanced_visible:
            self._toggle_advanced()

    def _rebuild(self) -> None:
        for c in self.common_box.winfo_children():
            c.destroy()
        for c in self.advanced_box.winfo_children():
            c.destroy()
        self._common_rows.clear()
        self._advanced_rows.clear()

        # 「⚡ KVMem」页有自己的常用列表, 不跟主模型页的 common_keys 混用;
        # kvmem_full = 模型页全部参数 + kvmem 专属项 (两个页面只差指向的程序)
        allowed = _SCOPE_SET.get(self.scope) or frozenset({self.scope, "both"})
        if self.scope == "kvmem":
            order_src = config.kvmem_common_keys()
        elif self.scope == "kvmem_full":
            kv = set(config.kvmem_common_keys())
            order_src = (list(config.kvmem_common_keys())
                         + [k for k in config.common_keys() if k not in kv])
        else:
            order_src = config.common_keys()
        common_set = set(order_src)
        applicable = []
        for p in config.PARAM_SCHEMA:
            if p["scope"] not in allowed:
                continue
            # 模型面板都排除两个独立区块专属的 key:
            # 同一条记录的同一个 key 全项目只出现一次, 否则两边会互相覆盖
            if self.scope in ("model", "kvmem", "kvmem_full") and (
                    p["key"] in config.DRAFT_BOX_KEYS
                    or p["key"] in config.MMKV_BOX_KEYS):
                continue
            applicable.append(p)
        for p in config.extra_params():
            if p.get("scope", "model") not in allowed | {"global"}:
                continue
            applicable.append(p)

        common_order = [k for k in order_src
                        if any(p["key"] == k for p in applicable)]
        advanced = [p for p in applicable if p["key"] not in common_set]
        common = [p for p in applicable if p["key"] in common_set]
        common.sort(key=lambda p: common_order.index(p["key"]) if p["key"] in common_order else 999)

        for p in common:
            self._make_row(self.common_box, p, self._common_rows)
        for p in advanced:
            self._make_row(self.advanced_box, p, self._advanced_rows)

        self._inner.update_idletasks()
        self._canvas.configure(scrollregion=self._canvas.bbox("all"))
        self._canvas.yview_moveto(0)

    def _make_row(self, parent, p, store) -> None:
        choices = config.list_kinds_choices().get(p["key"], [])
        row = ParamRow(parent, p, choices, get=self._get, set_=self._set,
                       on_change=self._on_change)
        row.load()
        row.frame.pack(fill=tk.X, pady=1)
        store[p["key"]] = row
        self._bind_mousewheel_recursive(row.frame)

    def _bind_mousewheel_recursive(self, widget) -> None:
        def _on_wheel(e):
            delta = -1 * int(e.delta / 120) if e.delta else 0
            if delta == 0:
                if e.num == 4:
                    delta = -1
                elif e.num == 5:
                    delta = 1
            if delta:
                self._canvas.yview_scroll(delta, "units")
        widget.bind("<MouseWheel>", _on_wheel, add="+")
        widget.bind("<Button-4>", _on_wheel, add="+")
        widget.bind("<Button-5>", _on_wheel, add="+")
        for child in widget.winfo_children():
            self._bind_mousewheel_recursive(child)

    def _on_change(self) -> None:
        if self._refresh_outer:
            self._refresh_outer()

    def reload(self) -> None:
        self._rebuild()
        if self._advanced_visible:
            self.show_advanced(True)

    def save_all(self) -> None:
        for row in self._common_rows.values():
            row.save()
        for row in self._advanced_rows.values():
            row.save()


# =========================================================================
# 偏好页
# =========================================================================
class PrefsTab(ttk.Frame):
    def __init__(self, master: tk.Tk, on_change: Optional[Callable[[], None]] = None) -> None:
        super().__init__(master, padding=10)
        self._on_change = on_change
        self._build()

    def _build(self) -> None:
        left = ttk.LabelFrame(self, text="常用参数 (勾选后会出现在主界面的「常用」区)", padding=8)
        left.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 4))
        right = ttk.LabelFrame(self, text="用户自定义额外参数 (不写 schema 也能用的参数)", padding=8)
        right.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(4, 0))
        self._build_common(left)
        self._build_extra(right)

    def _build_common(self, parent: ttk.Frame) -> None:
        toolbar = ttk.Frame(parent)
        toolbar.pack(fill=tk.X, pady=(0, 4))
        ttk.Label(toolbar, text="拖动可调整顺序", foreground="#888").pack(side=tk.LEFT)
        ttk.Button(toolbar, text="全选", command=lambda: self._set_all_common(True)).pack(side=tk.RIGHT, padx=2)
        ttk.Button(toolbar, text="全不选", command=lambda: self._set_all_common(False)).pack(side=tk.RIGHT, padx=2)

        body = ttk.Frame(parent)
        body.pack(fill=tk.BOTH, expand=True)

        cand_box = ttk.Frame(body)
        cand_box.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 4))

        # 标题 + 匹配计数
        head = ttk.Frame(cand_box)
        head.pack(fill=tk.X, pady=(0, 2))
        ttk.Label(head, text="所有参数 (勾选加入常用):").pack(side=tk.LEFT)
        self._cand_count = tk.StringVar(value="")
        ttk.Label(head, textvariable=self._cand_count,
                  foreground="#888").pack(side=tk.RIGHT)

        # 搜索: 大小写不敏感, 匹配 key / 标签 / 作用域 / 分类
        # 查询串开头的 - 和 -- 会被忽略 -> 搜 "-ub" / "--ubatch" / "ub" 都能命中
        sf = ttk.Frame(cand_box)
        sf.pack(fill=tk.X, pady=(0, 3))
        ttk.Label(sf, text="搜索:").pack(side=tk.LEFT)
        self.cand_query = tk.StringVar()
        self.cand_query.trace_add("write", self._render_cand)
        self._cand_entry = ttk.Entry(sf, textvariable=self.cand_query)
        self._cand_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(4, 4))
        ttk.Button(sf, text="清空", width=6,
                   command=lambda: self.cand_query.set("")).pack(side=tk.LEFT)
        self._cand_entry.bind("<Return>", self._cand_jump_first)
        ttk.Label(cand_box, text="点列头排序: 升序 → 降序 → 恢复默认",
                  foreground="#888").pack(anchor=tk.W, pady=(0, 3))

        self._cand_sort: tuple = ("order", False)  # (列, 是否倒序); order = schema 原始顺序
        self.cand_tree = ttk.Treeview(cand_box, columns=("label", "scope", "category"),
                                      show="tree headings", selectmode="browse", height=18)
        self.cand_tree.heading("#0", text="key",
                               command=lambda: self._on_cand_sort("key"))
        self.cand_tree.heading("label", text="标签",
                               command=lambda: self._on_cand_sort("label"))
        self.cand_tree.heading("scope", text="作用域",
                               command=lambda: self._on_cand_sort("scope"))
        self.cand_tree.heading("category", text="分类",
                               command=lambda: self._on_cand_sort("category"))
        self.cand_tree.column("#0", width=150, anchor=tk.W)
        self.cand_tree.column("label", width=150, anchor=tk.W)
        self.cand_tree.column("scope", width=70, anchor=tk.W)
        self.cand_tree.column("category", width=70, anchor=tk.W)
        cand_sb = ttk.Scrollbar(cand_box, orient=tk.VERTICAL, command=self.cand_tree.yview)
        self.cand_tree.configure(yscrollcommand=cand_sb.set)
        self.cand_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        cand_sb.pack(side=tk.RIGHT, fill=tk.Y)
        self.cand_tree.bind("<Control-f>",
                            lambda _e: (self._cand_entry.focus_set(), "break"))

        mid = ttk.Frame(body)
        mid.pack(side=tk.LEFT, fill=tk.Y, padx=4)
        ttk.Button(mid, text=">> 加入", command=self._add_to_common).pack(pady=2)
        ttk.Button(mid, text="移出 <<", command=self._remove_from_common).pack(pady=2)
        ttk.Button(mid, text="上移", command=lambda: self._move_common(-1)).pack(pady=2)
        ttk.Button(mid, text="下移", command=lambda: self._move_common(1)).pack(pady=2)

        sel_box = ttk.Frame(body)
        sel_box.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(4, 0))
        ttk.Label(sel_box, text="已设为常用 (按 UI 显示顺序):").pack(anchor=tk.W)
        self.sel_tree = ttk.Treeview(sel_box, columns=("label",),
                                     show="tree headings", selectmode="browse", height=18)
        self.sel_tree.heading("#0", text="key")
        self.sel_tree.heading("label", text="标签")
        self.sel_tree.column("label", width=160, anchor=tk.W)
        sel_sb = ttk.Scrollbar(sel_box, orient=tk.VERTICAL, command=self.sel_tree.yview)
        self.sel_tree.configure(yscrollcommand=sel_sb.set)
        self.sel_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sel_sb.pack(side=tk.RIGHT, fill=tk.Y)

        ttk.Label(parent,
            text="说明：常用参数区会显示在「全局」和「模型」标签页顶部，扩展参数默认折叠。\n"
                 "改动后点「保存偏好」才能生效。",
            foreground="#555", justify=tk.LEFT).pack(anchor=tk.W, pady=(4, 0))

        bottom = ttk.Frame(parent)
        bottom.pack(fill=tk.X, pady=(6, 0))
        ttk.Button(bottom, text="保存偏好", command=self._save_common).pack(side=tk.RIGHT)

        self._refresh_common_lists()

    def _build_extra(self, parent: ttk.Frame) -> None:
        toolbar = ttk.Frame(parent)
        toolbar.pack(fill=tk.X, pady=(0, 4))
        ttk.Button(toolbar, text="新增参数", command=self._add_extra).pack(side=tk.LEFT, padx=2)
        ttk.Button(toolbar, text="编辑", command=self._edit_extra).pack(side=tk.LEFT, padx=2)
        ttk.Button(toolbar, text="删除", command=self._del_extra).pack(side=tk.LEFT, padx=2)
        ttk.Button(toolbar, text="保存", command=self._save_extra).pack(side=tk.RIGHT)

        ttk.Label(parent,
            text="任何 llama-server 支持的 CLI 参数都可以加进来。\n"
                 "填入的 key 会原样写入 ini (会自动作为 LLAMA_ARG_<KEY> 传给 server)。",
            foreground="#555", justify=tk.LEFT).pack(anchor=tk.W, pady=(0, 4))

        self.extra_tree = ttk.Treeview(parent, columns=("label", "scope", "kind", "default"),
                                       show="tree headings", selectmode="browse", height=20)
        self.extra_tree.heading("#0", text="key")
        self.extra_tree.heading("label", text="标签")
        self.extra_tree.heading("scope", text="作用域")
        self.extra_tree.heading("kind", text="类型")
        self.extra_tree.heading("default", text="默认")
        self.extra_tree.column("label", width=140)
        self.extra_tree.column("scope", width=60)
        self.extra_tree.column("kind", width=60)
        self.extra_tree.column("default", width=80)
        self.extra_tree.pack(fill=tk.BOTH, expand=True)
        self.extra_tree.bind("<Double-1>", lambda _e: self._edit_extra())
        self._refresh_extra_list()

    def _refresh_common_lists(self) -> None:
        prefs = config.load_prefs()
        common = list(prefs.get("common_keys", []))
        for iid in self.sel_tree.get_children():
            self.sel_tree.delete(iid)
        # 已选列表必须按 common_keys 的存储顺序渲染,
        # 否则上移/下移看不出效果, 且与参数面板顺序不一致
        label_of = {p["key"]: p.get("label", "") for p in config.PARAM_SCHEMA}
        seen = set()
        for k in common:
            if k in seen:
                continue
            seen.add(k)
            self.sel_tree.insert("", tk.END, iid=k, text=k,
                                 values=(label_of.get(k, ""),))
        # 候选列表缓存下来, 搜索/排序都基于它做, 避免反复读 schema
        self._cand_all = [p for p in config.PARAM_SCHEMA if p["key"] not in seen]
        self._render_cand()
        self._notify_change()

    # ----------------- 候选列表: 搜索 + 排序 -----------------
    @staticmethod
    def _cand_match(p: dict, q: str) -> bool:
        return any(q in str(p.get(f, "") or "").lower()
                   for f in ("key", "label", "scope", "category"))

    def _cand_rows(self) -> List[dict]:
        rows = list(getattr(self, "_cand_all", []))
        q = (self.cand_query.get() if hasattr(self, "cand_query") else "").strip().lower()
        if q:
            q = q.lstrip("-")           # 搜 "-ub" / "--ubatch" 等价于 "ub"
            if q:
                rows = [p for p in rows if self._cand_match(p, q)]
        col, rev = self._cand_sort
        if col != "order":
            rows = sorted(rows, key=lambda p: (str(p.get(col, "") or "").lower(),
                                               p["key"]))
            if rev:
                rows.reverse()
        return rows

    def _render_cand(self, *_a) -> None:
        """按当前搜索词 + 排序方式重建候选树 (保持滚动与选中项)。"""
        if not hasattr(self, "cand_tree"):
            return
        keep = self.cand_tree.selection()
        keep = keep[0] if keep else None
        rows = self._cand_rows()
        for iid in self.cand_tree.get_children():
            self.cand_tree.delete(iid)
        for p in rows:
            self.cand_tree.insert("", tk.END, iid=p["key"], text=p["key"],
                                  values=(p.get("label", ""), p.get("scope", ""),
                                          p.get("category", "")))
        if keep and self.cand_tree.exists(keep):
            self.cand_tree.selection_set(keep)
            self.cand_tree.see(keep)
        if hasattr(self, "_cand_count"):
            total = len(getattr(self, "_cand_all", []))
            q = self.cand_query.get().strip()
            self._cand_count.set(
                f"共 {total} 项, 匹配 {len(rows)} 项" + (f" 「{q}」" if q else ""))
        self._update_cand_headings()

    def _on_cand_sort(self, col: str) -> None:
        """点列头: 升序 -> 降序 -> 恢复 schema 默认顺序。"""
        cur, rev = self._cand_sort
        if cur != col:
            self._cand_sort = (col, False)
        elif not rev:
            self._cand_sort = (col, True)
        else:
            self._cand_sort = ("order", False)
        self._render_cand()

    def _update_cand_headings(self) -> None:
        col, rev = self._cand_sort
        for c, t in (("#0", "key"), ("label", "标签"),
                     ("scope", "作用域"), ("category", "分类")):
            arrow = ""
            if c == col:
                arrow = " ▼" if rev else " ▲"
            try:
                self.cand_tree.heading(c, text=t + arrow)
            except tk.TclError:
                pass

    def _cand_jump_first(self, _e=None):
        """搜索框回车 -> 跳到第一条匹配项。"""
        kids = self.cand_tree.get_children()
        if kids:
            self.cand_tree.selection_set(kids[0])
            self.cand_tree.see(kids[0])
        return "break"

    def _notify_change(self) -> None:
        if self._on_change:
            self._on_change()

    def _set_all_common(self, add: bool) -> None:
        prefs = config.load_prefs()
        common = list(prefs.get("common_keys", []))
        if add:
            for p in config.PARAM_SCHEMA:
                if p["key"] not in common:
                    common.append(p["key"])
        else:
            common = []
        prefs["common_keys"] = common
        config.save_prefs(prefs)
        self._refresh_common_lists()

    def _add_to_common(self) -> None:
        sel = self.cand_tree.selection()
        if not sel:
            return
        prefs = config.load_prefs()
        common = list(prefs.get("common_keys", []))
        for k in sel:
            if k not in common:
                common.append(k)
        prefs["common_keys"] = common
        config.save_prefs(prefs)
        self._refresh_common_lists()

    def _remove_from_common(self) -> None:
        sel = self.sel_tree.selection()
        if not sel:
            return
        prefs = config.load_prefs()
        common = [k for k in prefs.get("common_keys", []) if k not in sel]
        prefs["common_keys"] = common
        config.save_prefs(prefs)
        self._refresh_common_lists()

    def _move_common(self, delta: int) -> None:
        sel = self.sel_tree.selection()
        if not sel:
            return
        k = sel[0]
        prefs = config.load_prefs()
        common = list(prefs.get("common_keys", []))
        if k not in common:
            return
        idx = common.index(k)
        new_idx = idx + delta
        if new_idx < 0 or new_idx >= len(common):
            return
        common[idx], common[new_idx] = common[new_idx], common[idx]
        prefs["common_keys"] = common
        config.save_prefs(prefs)
        self._refresh_common_lists()
        self.sel_tree.selection_set(k)

    def _save_common(self) -> None:
        messagebox.showinfo("保存", "常用参数已保存。回到「全局」/「模型」页可看到新顺序。")

    def _refresh_extra_list(self) -> None:
        for iid in self.extra_tree.get_children():
            self.extra_tree.delete(iid)
        for p in config.extra_params():
            self.extra_tree.insert("", tk.END, iid=p["key"], text=p["key"],
                                   values=(p.get("label", ""),
                                           p.get("scope", "model"),
                                           p.get("kind", "text"),
                                           p.get("default", "")))

    def _add_extra(self) -> None:
        ExtraParamDialog(self, None, self._on_extra_saved)

    def _edit_extra(self) -> None:
        sel = self.extra_tree.selection()
        if not sel:
            return
        key = sel[0]
        item = next((p for p in config.extra_params() if p["key"] == key), None)
        if not item:
            return
        ExtraParamDialog(self, item, self._on_extra_saved)

    def _del_extra(self) -> None:
        sel = self.extra_tree.selection()
        if not sel:
            return
        if not messagebox.askyesno("确认", f"删除额外参数 [{sel[0]}] ?"):
            return
        prefs = config.load_prefs()
        prefs["extra_params"] = [p for p in prefs.get("extra_params", []) if p["key"] != sel[0]]
        config.save_prefs(prefs)
        self._refresh_extra_list()
        self._notify_change()

    def _on_extra_saved(self, new_param: Dict[str, Any]) -> None:
        prefs = config.load_prefs()
        existing = list(prefs.get("extra_params", []))
        for i, p in enumerate(existing):
            if p["key"] == new_param["key"]:
                existing[i] = new_param
                break
        else:
            existing.append(new_param)
        prefs["extra_params"] = existing
        config.save_prefs(prefs)
        self._refresh_extra_list()
        self._notify_change()

    def _save_extra(self) -> None:
        messagebox.showinfo("保存", "额外参数已保存。回到「全局」/「模型」页会包含这些参数。")


class ExtraParamDialog(tk.Toplevel):
    def __init__(self, master, existing: Optional[Dict[str, Any]],
                 on_save: Callable[[Dict[str, Any]], None]) -> None:
        super().__init__(master)
        self.title("编辑额外参数" if existing else "新增额外参数")
        self.geometry("480x520")
        self.transient(master)
        self.grab_set()
        self._on_save = on_save
        self._build(existing)

    def _build(self, existing: Optional[Dict[str, Any]]) -> None:
        f = ttk.Frame(self, padding=12)
        f.pack(fill=tk.BOTH, expand=True)

        self.var_key = tk.StringVar(value=existing["key"] if existing else "")
        self.var_label = tk.StringVar(value=existing.get("label", "") if existing else "")
        self.var_default = tk.StringVar(value=existing.get("default", "") if existing else "")
        self.var_kind = tk.StringVar(value=existing.get("kind", "text") if existing else "text")
        self.var_scope = tk.StringVar(value=existing.get("scope", "model") if existing else "model")
        self.var_hint = tk.StringVar(value=existing.get("hint", "") if existing else "")
        self.var_category = tk.StringVar(value=existing.get("category", "common") if existing else "common")
        self.var_choices = tk.StringVar(value=",".join(existing.get("choices", [])) if existing else "")
        self.var_desc = tk.Text(self, wrap=tk.WORD, height=8, font=("Microsoft YaHei", 10))
        if existing and existing.get("description"):
            self.var_desc.insert("1.0", existing["description"])

        rows = [
            ("key (参数名 / ini 段 key)", self.var_key, "entry"),
            ("label (UI 显示名)", self.var_label, "entry"),
            ("kind (类型)", None, "combo_kind"),
            ("scope (作用域)", None, "combo_scope"),
            ("category (在 UI 中的分类)", None, "combo_cat"),
            ("default (默认值)", self.var_default, "entry"),
            ("hint (UI 一行提示)", self.var_hint, "entry"),
            ("choices (kind=choice 时的可选项, 逗号分隔)", self.var_choices, "entry"),
        ]
        for i, (label, var, kind) in enumerate(rows):
            ttk.Label(f, text=label + ":").grid(row=i, column=0, sticky=tk.W, pady=4, padx=(0, 8))
            if kind == "entry":
                ttk.Entry(f, textvariable=var, width=40).grid(row=i, column=1, sticky=tk.EW, pady=4)
            elif kind == "combo_kind":
                ttk.Combobox(f, textvariable=self.var_kind, width=20,
                             values=["text", "int", "float", "bool", "choice"],
                             state="readonly").grid(row=i, column=1, sticky=tk.W, pady=4)
            elif kind == "combo_scope":
                ttk.Combobox(f, textvariable=self.var_scope, width=20,
                             values=["global", "model", "both"],
                             state="readonly").grid(row=i, column=1, sticky=tk.W, pady=4)
            elif kind == "combo_cat":
                ttk.Combobox(f, textvariable=self.var_category, width=20,
                             values=["common", "advanced"],
                             state="readonly").grid(row=i, column=1, sticky=tk.W, pady=4)
        ttk.Label(f, text="description (详细说明):").grid(
            row=len(rows), column=0, sticky=tk.NW, pady=4, padx=(0, 8))
        self.var_desc.grid(row=len(rows), column=1, sticky=tk.NSEW, pady=4)
        f.rowconfigure(len(rows), weight=1)
        f.columnconfigure(1, weight=1)

        bottom = ttk.Frame(self, padding=8)
        bottom.pack(fill=tk.X)
        ttk.Button(bottom, text="取消", command=self.destroy).pack(side=tk.RIGHT, padx=4)
        ttk.Button(bottom, text="保存", command=self._save).pack(side=tk.RIGHT)

    def _save(self) -> None:
        key = self.var_key.get().strip()
        if not key:
            messagebox.showerror("错误", "key 不能为空", parent=self)
            return
        import re
        if not re.match(r"^[a-zA-Z][a-zA-Z0-9\-_]*$", key):
            messagebox.showerror("错误", "key 只能含字母数字短横线下划线，且以字母开头", parent=self)
            return
        if key in config.SCHEMA_INDEX:
            messagebox.showerror("错误", f"key [{key}] 已是内置参数，不能用", parent=self)
            return
        choices = [c.strip() for c in self.var_choices.get().split(",") if c.strip()]
        new_param = {
            "key": key,
            "label": self.var_label.get().strip() or key,
            "default": self.var_default.get().strip(),
            "kind": self.var_kind.get(),
            "scope": self.var_scope.get(),
            "category": self.var_category.get(),
            "hint": self.var_hint.get().strip(),
            "description": self.var_desc.get("1.0", tk.END).strip(),
        }
        if choices:
            new_param["choices"] = choices
        self._on_save(new_param)
        self.destroy()


# =========================================================================
# 实时监控窗口 (日志 tail + HTTP 状态)
# =========================================================================
# =========================================================================
# 监控地址解析 (支持 host / host:port / http://host:port / [v6]:port)
# =========================================================================
_LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost", "::1", "0.0.0.0", ""})


def parse_host_port(text: str,
                    default_host: str = "127.0.0.1",
                    default_port: int = 8080) -> tuple:
    """把用户在「监控地址」格里填的东西解析成 (host, port)。

    支持: `192.168.1.10` / `192.168.1.10:9000` / `http://host:9000/` /
          `[fe80::1]:9000`。空格子 -> 用默认值。
    解析不了时抛 ValueError (调用方负责把原因显示给用户)。
    """
    s = (text or "").strip()
    if not s:
        return default_host, int(default_port)
    low = s.lower()
    for proto in ("http://", "https://"):
        if low.startswith(proto):
            s = s[len(proto):]
            low = s.lower()
    s = s.split("/", 1)[0].strip()          # 去掉 http://host/path 的 path
    if not s:
        return default_host, int(default_port)

    if s.startswith("["):                    # [fe80::1]:9000
        m = re.match(r"^\[([^\]]+)\](?::(\d+))?$", s)
        if not m:
            raise ValueError("IPv6 请写成 [地址]:端口")
        host, port_s = m.group(1), m.group(2)
        port = int(port_s) if port_s else int(default_port)
    else:
        if s.count(":") > 1:
            raise ValueError("IPv6 请写成 [地址]:端口")
        if ":" in s:
            host, port_s = s.rsplit(":", 1)
            port_s = port_s.strip()
            if not port_s.isdigit():
                raise ValueError(f"端口必须是数字: {port_s!r}")
            port = int(port_s)
        else:
            host, port = s, int(default_port)

    host = host.strip()
    if not host:
        raise ValueError("地址不能为空")
    if not (1 <= port <= 65535):
        raise ValueError(f"端口范围 1-65535: {port}")
    return host, port


def is_local_host(host: str) -> bool:
    """host 是不是本机 (决定日志/速度要不要标注"仅本机")。"""
    h = (host or "").strip().lower().strip("[]")
    if h in _LOCAL_HOSTS:
        return True
    try:
        import socket
        me = set()
        me.add(socket.gethostbyname(socket.gethostname()))
        for info in socket.getaddrinfo(socket.gethostname(), None):
            me.add(info[4][0])
        for info in socket.getaddrinfo(h, None):
            if info[4][0] in me:
                return True
    except Exception:
        pass
    return False


# choice 型参数的候选值 (照 strata.exe --help 里的枚举)
_STRATA_CHOICES = {
    "--kv": ["fp16", "int8", "q4_0", "k8v4"],
    "--rope-scaling": ["none", "linear", "yarn"],
}


def _strata_choices(flag: str) -> List[str]:
    return list(_STRATA_CHOICES.get(flag, []))


def model_status_value(m: dict) -> str:
    """模型状态值。

    本机/新版本 llama-server 返回 dict {"value": ..., "args": [...]};
    旧版本只返回字符串。远程版本可能不一致, 这里统一兜住,
    否则整轮 _render_status 会抛异常, 状态条就永远停在旧数据上。
    """
    st = (m or {}).get("status")
    if isinstance(st, dict):
        return str(st.get("value") or "?")
    if st is None:
        return "?"
    return str(st)


def model_status_args(m: dict) -> list:
    st = (m or {}).get("status")
    if isinstance(st, dict) and isinstance(st.get("args"), list):
        return list(st["args"])
    return []


# ----------------------------------------------------------------------
# 监控窗口的「纯轮询」过滤
# ----------------------------------------------------------------------
# 后台轮询是**正常且必要**的, 不是故障: 网页版靠 /v1/models 列模型下拉框, 靠
# /props?autoload=false 当「服务起没起」的探针, EventSource 探 /models/sse。
# 但这些行在引擎日志里每 2 秒一条, 每行还带一个**不同的 TCP 临时源端口**, 几百行
# 下来把真正的生成/加载/错误全埋了 —— 用户看到的就是「不停在换端口调模型」。
#
# 只认两种标准访问日志格式 (uvicorn/FastAPI 和 http.server), 而且**只丢 GET/HEAD**:
# POST /v1/chat/completions、/models/load、/models/unload 一律保留, 那是真实动作。
_POLL_GET_PATHS = (
    "/v1/models", "/v1/models/", "/models", "/models/sse",
    "/health", "/healthz", "/props", "/sw.js", "/service-worker.js",
    "/slots", "/metrics", "/api/tags", "/api/ps",
)
_RE_ACCESS_LOG = re.compile(
    r'"(?:GET|HEAD)\s+(/\S*?)(?:\?[^"\s]*)?\s+HTTP/[\d.]+"')


def is_poll_noise(line: str) -> bool:
    """这一行是不是「纯轮询」(只有访问日志里的保活/目录探测, 没有信息量)。"""
    m = _RE_ACCESS_LOG.search(line or "")
    if not m:
        return False
    path = m.group(1)
    return any(path == p or path.startswith(p + "/") for p in _POLL_GET_PATHS)


# 旧名保留 (测试和别处按这个名字引用)
_is_poll_noise = is_poll_noise


# =========================================================================

class MonitorWindow(tk.Toplevel):
    """实时显示 llama-server 日志和模型状态。

    日志: 主池 router.*.log + KVMem kvmem.*.log + Strata strata.*.log 合成一份
          实时 tail (颜色高亮);
    模型: 拉 /v1/models + /health, 主池 + KVMem 两组一起列 (放不下自动换行)。
    """

    def __init__(self, master: tk.Tk, host: str = "127.0.0.1", port: int = 8080,
                 addr: Optional[str] = None) -> None:
        super().__init__(master)
        self.title("llama.cpp Router - 实时监控")
        self.geometry("1200x720")
        self.minsize(900, 500)
        # 本机默认地址 (「本机」按钮回到这里); addr 是上次保存的监控地址
        self._local_host = host
        self._local_port = int(port)
        if addr:
            try:
                host, port = parse_host_port(addr, host, port)
            except ValueError:
                pass
        self._host = host
        self._port = int(port)
        self._remote = not is_local_host(host)
        self._stop = False
        # 换地址时立刻唤醒 HTTP 轮询, 不必干等 2 秒的 sleep
        self._http_kick = threading.Event()
        self._http_addr = f"{self._host}:{self._port}"   # 当前状态区实际在轮询的地址
        # 远程日志走 log_relay 转发 (端口两边都用配置里的 log_relay.port)
        self._relay_port = self._load_relay_port()
        self._remote_offsets: Dict[str, int] = {}        # 文件名 -> 字节偏移
        # 首帧就给出"连接中", 否则切换地址后 _render_status 会 early-return,
        # 界面上留着上一个地址的旧数据, 看起来就是"没有更新"
        self._http_state: Dict[str, Any] = {
            "health": {"status": "连接中…"}, "models": [], "ts": 0.0,
        }
        self._last_log_pos: Dict[str, int] = {}  # path -> file position
        self._log_buffer: Dict[str, deque] = {
            "out": deque(maxlen=2000),
            "err": deque(maxlen=1000),
        }
        # 每个日志文件单独存一份 + 记下"谁最后吐过新行"。
        # 渲染按这个时间排序: 内容最少的文件 (主池不跑时 router.out.log
        # 永远停在几小时前) 必须排在最上面, 否则自动跟跳跳到底看到的
        # 一直是那段死日志, 用户的感受就是「日志完全没有更新」。
        self._file_lines: Dict[str, deque] = {}
        self._file_seen: Dict[str, float] = {}
        # 解析后的最后生成速度 (model_id -> {tg, tg_3s, ts})
        self._last_speed: Dict[str, Dict[str, Any]] = {}
        # 日志是否自动跟跳 (True=跟到最新, False=用户在看历史, 不自动滚)
        self._follow_tail = True
        # tail 健康状态 (供顶部指示器显示; 之前读失败被静默吞掉, 用户只看到"日志不动")
        self._tail_last_ok: float = 0.0   # 最近一次成功读取的时刻
        self._tail_err: str = ""          # 最近一次读取异常
        self._tail_missing: List[str] = []  # 当前不存在的日志文件
        self._tail_lines: int = 0         # 已累计读到的行数
        self._tail_read_ok: bool = False  # 本轮是否至少成功读了一个日志文件
        self._build_ui()
        # 启动后台线程
        self._log_thread = threading.Thread(target=self._tail_loop, daemon=True)
        self._http_thread = threading.Thread(target=self._http_loop, daemon=True)
        self._log_thread.start()
        self._http_thread.start()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        # 拖动窗口时挂起重绘: 拖动每帧都来 <Configure>, 而日志重绘是
        # 「删掉整个 Text + 逐行插最多 1500 行」, 两者撞在一起必卡。
        # 停止收到 <Configure> 后 150ms 没人再 reset, _end_drag 补一次渲染。
        self._dragging = False
        self._drag_timer = None
        self._drag_since = 0.0
        self.bind("<Configure>", self._begin_drag, add="+")
        self.bind("<ButtonRelease-1>", self._end_drag, add="+")
        # UI 刷新
        self.after(300, self._refresh_ui)

    def _build_ui(self) -> None:
        # 顶部: 标题 + 按钮
        top = ttk.Frame(self, padding=(10, 6))
        top.pack(fill=tk.X)
        ttk.Label(top, text="📊 实时监控 (类似直接命令行调用看到的输出)",
                  font=("Segoe UI", 11, "bold")).pack(side=tk.LEFT)
        # tail 健康指示器: 日志不动时, 这里会直接写出原因 (找不到文件 / 读取异常 / 读取正常)
        self.log_health_var = tk.StringVar(value="日志: 等待首次读取…")
        self.log_health_lbl = ttk.Label(top, textvariable=self.log_health_var,
                                        foreground="#555")
        self.log_health_lbl.pack(side=tk.LEFT, padx=(14, 0))
        # 「换行」开关: 一行日志动辄几百上千字符 (整条 prompt、一串 JSON),
        # 不换行就得拖横向滚动条, 而且横拖之后完全分不清哪一行是新来的。
        # 所以默认开; 关掉就是原来的行为 (不折行 + 横向滚动条)。
        self._wrap_var = tk.BooleanVar(value=_wrap_pref())
        self._wrap_chk = ttk.Checkbutton(top, text="换行", variable=self._wrap_var,
                                         command=self._apply_wrap)
        self._wrap_chk.pack(side=tk.RIGHT, padx=(8, 2))
        ToolTip(self._wrap_chk,
                "日志是否自动换行。关掉则整行不折行, 改用底部横向滚动条。\n"
                "整行对齐有时反而更易读 (比如看堆栈/表格)")
        ttk.Button(top, text="清空日志", command=self._clear_logs).pack(side=tk.RIGHT, padx=2)
        ttk.Button(top, text="🔄 立即刷新", command=self._force_follow).pack(side=tk.RIGHT, padx=2)
        ttk.Button(top, text="🔧 重读日志", command=self._reload_logs).pack(side=tk.RIGHT, padx=2)
        # 重启的是本机 NSSM 服务, 监控远程时禁掉 (否则用户以为在重启远程)
        self._btn_restart = ttk.Button(top, text="🔁 重启服务", command=self._restart_service)
        self._btn_restart.pack(side=tk.RIGHT, padx=2)

        # 监控地址行: 自己填远程电脑的 host:port, 状态区按它轮询
        addr_row = ttk.Frame(self, padding=(10, 2))
        addr_row.pack(fill=tk.X, padx=8, pady=(0, 4))
        ttk.Label(addr_row, text="监控地址:").pack(side=tk.LEFT)
        self._addr_var = tk.StringVar(value=self._http_addr)
        # 用 Combobox 而不是 Entry: 点格子就下拉出填过的地址 (见 _on_addr_click)
        self._addr_entry = ttk.Combobox(addr_row, textvariable=self._addr_var, width=30)
        self._addr_entry.pack(side=tk.LEFT, padx=(4, 6))
        self._addr_entry.bind("<Return>", lambda _e: self._apply_addr())
        # 下拉里点一条 -> 直接连过去 (选中就是明确意图)
        self._addr_entry.bind("<<ComboboxSelect>>", lambda _e: self._apply_addr())
        # Tk 默认只有点右侧那个小箭头才弹列表; 这里改成点格子就弹
        self._addr_entry.bind("<Button-1>", self._on_addr_click, add="+")
        ttk.Button(addr_row, text="连接", command=self._apply_addr).pack(side=tk.LEFT, padx=2)
        ttk.Button(addr_row, text="本机", command=self._reset_addr).pack(side=tk.LEFT, padx=2)
        self._btn_hist = ttk.Button(addr_row, text="⚙", width=3,
                                    command=self._open_hist_settings)
        self._btn_hist.pack(side=tk.LEFT, padx=2)
        ToolTip(self._btn_hist, "监控地址历史: 保留条数 / 清空")
        self._addr_history: List[str] = []
        self._history_refresh()
        self._addr_state_var = tk.StringVar(value="")
        self._addr_state_lbl = ttk.Label(addr_row, textvariable=self._addr_state_var,
                                         font=("Segoe UI", 9, "bold"),
                                         foreground="#00aa00")
        self._addr_state_lbl.pack(side=tk.LEFT, padx=(10, 0))
        self._addr_hint_lbl = ttk.Label(addr_row, text="", foreground="#888")
        self._addr_hint_lbl.pack(side=tk.LEFT, padx=(8, 0))
        # 本机/远程两种提示语由这里统一写 (避免和控件创建处重复一份分支)
        self._sync_remote_hint()

        # 状态条: 健康 (单行紧凑)
        status_bar = ttk.Frame(self, padding=(10, 2))
        status_bar.pack(fill=tk.X, padx=8, pady=(0, 4))
        self.health_var = tk.StringVar(value="健康: ?")
        self.health_lbl = ttk.Label(status_bar, textvariable=self.health_var,
                                    font=("Segoe UI", 9), foreground="#0066CC")
        self.health_lbl.pack(side=tk.LEFT, padx=(0, 16))

        # 进度条: 实时显示 prefill / 生成 (读网关 /status)。
        # 翻日志看进度太费劲, 这里给一条直观的: 阶段 + 第几个 token + tok/s。
        prog_row = ttk.Frame(self, padding=(10, 2))
        prog_row.pack(fill=tk.X, padx=8, pady=(0, 4))
        self.prog_bar = ttk.Progressbar(prog_row, length=200, mode="determinate",
                                        maximum=100)
        self.prog_bar.pack(side=tk.LEFT, padx=(0, 8))
        self.prog_var = tk.StringVar(value="进度: 空闲")
        self.prog_lbl = ttk.Label(prog_row, textvariable=self.prog_var,
                                  font=("Segoe UI", 9), foreground="#333333")
        self.prog_lbl.pack(side=tk.LEFT, fill=tk.X, expand=True)

        # 模型条**单独占一整行**: 主池 + KVMem 的模型都在这里, 一行放不下就
        # 自动换行 (见 _layout_models)。以前它跟健康标签挤在同一行用
        # pack(side=LEFT) 单行排, 模型一多 —— 尤其是排在后面的 KVMem 组 ——
        # 直接被挤出窗口右边, 表现成「实时监控里看不到那一组的模型」。
        models_row = ttk.Frame(self, padding=(10, 2))
        models_row.pack(fill=tk.X, padx=8, pady=(0, 4))
        ttk.Label(models_row, text="模型:", font=("Segoe UI", 9)).pack(
            side=tk.LEFT, padx=(0, 4))
        self.models_strip_frame = ttk.Frame(models_row)
        self.models_strip_frame.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.models_strip_frame.bind("<Configure>",
                                     lambda _e: self._layout_models(), add="+")

        # 主区域: 日志拉满 (无横向分割)
        log_box = ttk.Frame(self)
        log_box.pack(fill=tk.BOTH, expand=True, padx=8, pady=4)
        ttk.Label(log_box, text="📜 实时日志 (主池 router.*.log + KVMem kvmem.*.log "
                                "+ Strata strata.*.log)",
                  font=("Segoe UI", 10, "bold")).pack(anchor=tk.W)
        log_inner = ttk.Frame(log_box)
        log_inner.pack(fill=tk.BOTH, expand=True, pady=(2, 0))
        self.log_text = tk.Text(
            log_inner, wrap=tk.NONE, font=("Consolas", 9),
            background="#1e1e1e", foreground="#d4d4d4", insertbackground="#d4d4d4",
        )
        ysb = ttk.Scrollbar(log_inner, orient=tk.VERTICAL, command=self.log_text.yview)
        xsb = ttk.Scrollbar(log_inner, orient=tk.HORIZONTAL, command=self.log_text.xview)
        self.log_text.configure(yscrollcommand=ysb.set, xscrollcommand=xsb.set)
        self.log_text.grid(row=0, column=0, sticky=tk.NSEW)
        ysb.grid(row=0, column=1, sticky=tk.NS)
        xsb.grid(row=1, column=0, sticky=tk.EW)
        log_inner.rowconfigure(0, weight=1)
        log_inner.columnconfigure(0, weight=1)
        self._log_xsb = xsb
        self._init_log_tags()
        self.log_text.configure(state=tk.DISABLED)
        self._apply_wrap()

        # 滚动时检查是否在最底端, 不在最底端则暂停自动跟跳
        def _settle_follow():
            """在 Tk **真正滚完之后**再判定跟跳开关。

            滚轮之前必失效的原因: Tk 的 `<MouseWheel>` 类绑定是在用户绑定
            **之后**才执行的, 处理器里立刻读 yview() 拿到的还是滚动前的位置,
            和上次一比 `abs(top-last) < 1e-6` -> 直接 return, `_eval_follow`
            一次都没跑过 -> `_follow_tail` 永远是 False, 下一帧重绘又把用户弹回
            原处, 表现就是「滚轮怎么滚都回不到跟跳」。
            推迟到 idle 再读, 拿到的才是滚动后的真实位置, 「位置真的变了」这个
            守卫也才有意义 (重绘自己 see() 完会同步 `_follow_top`, idle 比对时
            相同 -> 不会被误判成用户操作)。
            """
            try:
                top, bottom = self.log_text.yview()
            except Exception:
                return
            last = getattr(self, "_follow_top", None)
            if last is not None and abs(top - last) < 1e-6:
                return
            self._follow_top = top
            _eval_follow(top, bottom)
        def _on_scroll(_e=None):
            # **别把程序自己的滚动当成用户在翻历史。** 以前这里谁一动就切状态,
            # 而 `_render_logs` 每 500ms 全量重绘 + `see(tk.END)`, 文本框一收缩
            # yview 就会抖一下 -> 跟跳被误判成 PAUSED, 用户观感就是「日志不跟了,
            # 得点一下立即刷新才继续」。所以:
            #   1) 正在重绘 -> 一律忽略
            #   2) 只有**真实事件** (滚轮/按键) 才判定, 纯 <Configure> 不算
            #   3) 判定推迟到 idle, 因为滚轮生效在处理器之后 (见 _settle_follow)
            if getattr(self, "_rendering_logs", False):
                return
            if _e is None:
                return
            self.after_idle(_settle_follow)
        def _eval_follow(top, bottom):
            """统一判定跟跳开关。**只有真正在底部**才恢复跟跳; 一旦用户
            往上翻就保持 PAUSED, 没有任何自动恢复路径 —— 要恢复必须他自己
            拉回底部或点「立即刷新」。
            """
            # 「在底部」只看 bottom。top 天然是 1-可见比例: 停在最底时可见的是
            # 最后几屏, top 就是 0.97 左右, 根本不会接近 1。以前这里还要求
            # `top >= 0.999`, 于是永远判不成立 -> 用户拖到底/滚到底都恢复不了
            # 跟跳, 只能点「立即刷新」(那条路径不经过这个判定)。探针实测:
            # top=0.9708 bottom=1.0000 却判 at_bottom=False。
            at_bottom = bottom >= 0.999
            if at_bottom:
                if not self._follow_tail:
                    self._follow_tail = True
                    self.follow_var.set("📍 跟跳: ON")
                    self.follow_lbl.configure(foreground="#00aa00")
                    self.stat_var.set("已恢复跟跳最新日志")
            else:
                if self._follow_tail:
                    self._follow_tail = False
                    self.follow_var.set("⏸ 跟跳: PAUSED")
                    self.follow_lbl.configure(foreground="#cc6600")
                    self.stat_var.set("已暂停跟跳 (滚动浏览历史) - 点「立即刷新」或拉到底恢复")
        def _user_scroll():
            """滚动条拖动 / yscrollcommand 回调: 没有 Tk 事件对象, 但仍然是
            用户在操作。以前这些路径走 _on_scroll() 且 _e=None, 第 1232 行直接
            return -> 拖滚动条从不置PAUSED, 下一帧 _render_logs 的 see(END)
            就把用户拽回底部, 表现成「手动往回翻不生效」。
            """
            if getattr(self, "_rendering_logs", False):
                return
            try:
                top, bottom = self.log_text.yview()
            except Exception:
                return
            _eval_follow(top, bottom)
        # 滚轮 + 拖动滚动条 + 键盘
        self.log_text.bind("<MouseWheel>", _on_scroll, add="+")
        self.log_text.bind("<Button-4>", _on_scroll, add="+")
        self.log_text.bind("<Button-5>", _on_scroll, add="+")
        self.log_text.bind("<KeyPress>", _on_scroll, add="+")
        ysb.config(command=lambda *a: (self.log_text.yview(*a), _user_scroll()))
        xsb.config(command=lambda *a: (self.log_text.xview(*a), _user_scroll()))
        # 键盘 PageUp/PageDown/Home/End 也算手动操作
        for _k in ("<Prior>", "<Next>", "<Home>", "<End>"):
            self.log_text.bind(_k, lambda e: (self.log_text.yview(
                tk.PRIOR if e.keysym == "Prior" else
                tk.NEXT if e.keysym == "Next" else
                tk.HOME if e.keysym == "Home" else tk.END),
                _user_scroll()), add="+")

        # 底部: 最近生成速度 + 操作状态
        bot = ttk.Frame(self, padding=(10, 4))
        bot.pack(fill=tk.X)
        self.stat_var = tk.StringVar(value="就绪")
        ttk.Label(bot, textvariable=self.stat_var, foreground="#555").pack(side=tk.LEFT)
        # 跟跳状态指示器 (固定位置, 永远可见)
        self.follow_var = tk.StringVar(value="📍 跟跳: ON")
        self.follow_lbl = ttk.Label(bot, textvariable=self.follow_var,
                                    font=("Segoe UI", 9, "bold"),
                                    foreground="#00aa00")
        self.follow_lbl.pack(side=tk.RIGHT)
        # 「过滤轮询」开关。后台轮询是正常保活, 但它每 2 秒一行、每行带个不同的
        # 临时源端口, 会把真正的生成/加载/错误全埋掉。默认折掉, 想排查时取消勾选。
        try:
            _pf = bool(config.load_prefs().get("monitor_filter_poll", True))
        except Exception:                                    # noqa: BLE001
            _pf = True
        self.filter_poll = tk.BooleanVar(value=_pf)
        self._poll_dropped = 0
        self.filter_poll_lbl = ttk.Checkbutton(
            bot, text="过滤轮询", variable=self.filter_poll,
            command=self._on_toggle_filter_poll)
        self.filter_poll_lbl.pack(side=tk.RIGHT, padx=(0, 10))

    # ---- 日志换行 ----
    def _on_toggle_filter_poll(self) -> None:
        """开关「过滤轮询」。取消勾选后会重读一遍日志文件, 把刚才折掉的补回来。"""
        on = bool(self.filter_poll.get())
        try:
            prefs = config.load_prefs()
            if bool(prefs.get("monitor_filter_poll", True)) != on:
                prefs["monitor_filter_poll"] = on
                config.save_prefs(prefs)
        except Exception:                                # noqa: BLE001
            pass
        self._poll_dropped = 0
        if not on:
            # 补回: 把各文件的光标退回文件头, 让 tail 重新读一遍
            for p in list(getattr(self, "_last_log_pos", {})):
                try:
                    self._last_log_pos[p] = 0
                except Exception:                        # noqa: BLE001
                    pass
            self.stat_var.set("已关掉轮询过滤, 重新读取日志")
        else:
            self.stat_var.set("已折掉后台轮询行 (每 2 秒一条, 全是保活探测)")

    def _poll_filter_note(self) -> str:
        n = getattr(self, "_poll_dropped", 0)
        if not n or not bool(self.filter_poll.get()):
            return ""
        return "  ·  已折叠 %d 条后台轮询" % n

    def _apply_wrap(self) -> None:
        """按复选框切换「换行 / 横向滚动」。

        只需要改 `wrap` 这一个选项: Tk 的 Text 在 `wrap=word/char` 下是
        **按控件当前宽度**折行、并且窗口改大小会自己重排 (实测过: 同一条
        400 字符的日志, 宽 1100 占 4 个显示行、拉窄到 260 占 13 行), 所以
        不需要、也不能去设 `-wraplength` —— 那是 Label/Button 的选项,
        Text 上设它会直接 TclError: unknown option "-wraplength"。

        换行时把横向滚动条收起来 (它没用了); 关掉换行再放回来 —— 留着一条
        永远拖不动的横条只会让人以为窗口还能横向挪。
        """
        on = bool(self._wrap_var.get())
        try:
            self.log_text.configure(wrap=tk.WORD if on else tk.NONE)
            if on:
                self._log_xsb.grid_remove()
            else:
                self._log_xsb.grid()
        except tk.TclError:
            return
        _save_wrap_pref(on)
        # 折行后「在不在最底端」的判定要跟着重算, 否则切回来会以为用户滚到
        # 了历史而暂停跟跳
        #
        # **但必须确认框里已经有东西了。** 默认折行是在 __init__ 里应用的, 那时
        # 日志框还是空的 —— 空控件的 yview()[1] 不等于 1.0, 于是这个判断会在
        # 建好之前就把跟跳关掉, 之后 `_render_logs` 一直走 yview_moveto(0.0),
        # 视图被**永久钉死在顶部** (实测 40 轮重绘 top 恒为 0.0000)。用户观感
        # 就是「日志不跟踪、永远在最上面, 每次都得点一下立即刷新」。
        try:
            rendered = int(self.log_text.index("end-1c").split(".")[0]) - 1
            if rendered > 0 and self.log_text.yview()[1] < 0.999:
                self._follow_tail = False
        except (tk.TclError, ValueError):
            pass

    def _init_log_tags(self) -> None:
        self.log_text.tag_configure("info", foreground="#d4d4d4")
        self.log_text.tag_configure("warn", foreground="#dcdcaa")
        self.log_text.tag_configure("error", foreground="#f48771")
        self.log_text.tag_configure("load", foreground="#9cdcfe")
        self.log_text.tag_configure("slot", foreground="#b5cea8")
        self.log_text.tag_configure("time", foreground="#888")
        self.log_text.tag_configure("gen_speed", foreground="#ffd700", font=("Consolas", 9, "bold"))

    def _clear_logs(self) -> None:
        self._log_buffer["out"].clear()
        self._log_buffer["err"].clear()
        self._file_lines.clear()
        self._file_seen.clear()
        self._tail_lines = 0
        # 速度是从日志里解析出来的: 换数据源/清空日志时必须一起丢掉,
        # 否则切到远程后 tooltip 会拿本机的老速度冒充远程的
        self._last_speed.clear()
        self.log_text.configure(state=tk.NORMAL)
        self.log_text.delete("1.0", tk.END)
        self.log_text.configure(state=tk.DISABLED)
        self._last_log_pos.clear()

    def _reload_logs(self) -> None:
        """强制从头重读日志 (用于 NSSM 文件句柄卡住的恢复)。"""
        self._last_log_pos.clear()
        self._log_buffer["out"].clear()
        self._log_buffer["err"].clear()
        self._file_lines.clear()
        self._file_seen.clear()
        self._tail_lines = 0
        self._tail_err = ""
        # 只渲染一次; 这里不能调 _refresh_ui(), 它会再挂一条 after 定时链,
        # 每点一次按钮就多一条, 链条堆积会把界面拖垮 (表现为"日志不动")
        self._render_logs()
        self.stat_var.set("已重置日志文件位置，正在重新读取...")

    def _restart_service(self) -> None:
        """通过 NSSM 重启 llama-router 服务 (恢复 NSSM 日志文件句柄)。"""
        if not messagebox.askyesno("重启服务", "确定重启 llama-router 服务？\n当前进行中的请求会中断。"):
            return
        self.stat_var.set("正在重启服务...")
        self.update()
        def do_restart():
            ok, msg = service.restart()
            self.after(0, lambda: self.stat_var.set(f"重启 {'成功' if ok else '失败'}: {msg}"))
        threading.Thread(target=do_restart, daemon=True).start()

    def _on_close(self) -> None:
        self._stop = True
        self._http_kick.set()      # 别让轮询线程卡在 wait 里退不出去
        self.destroy()

    # ----------------- 监控地址 -----------------
    @staticmethod
    def _load_relay_port() -> int:
        """远程日志转发端口 (本机配置; 两边跑同一个工具, 端口约定一致)。"""
        try:
            return int((config.load().get("log_relay") or {}).get(
                "port", log_relay.DEFAULT_PORT))
        except Exception:
            return log_relay.DEFAULT_PORT

    def _sync_remote_hint(self) -> None:
        """本机/远程 两种提示语和状态标签配色 (远程时明确告知日志只看本机)。"""
        try:
            if self._remote:
                self._addr_state_var.set("远程监控中")
                self._addr_state_lbl.configure(foreground="#cc6600")
                self._addr_hint_lbl.configure(
                    text=f"状态走 {self._host}:{self._port} · "
                         f"日志走转发 {self._host}:{self._relay_port} "
                         f"(远程需勾选「日志转发」并放行端口 {self._relay_port})")
                # 「重启服务」动的是本机 NSSM, 远程时禁掉避免误操作
                try:
                    self._btn_restart.configure(state=tk.DISABLED)
                except Exception:
                    pass
            else:
                self._addr_state_var.set("本机")
                self._addr_state_lbl.configure(foreground="#00aa00")
                self._addr_hint_lbl.configure(
                    text="可填另一台电脑的地址, 例如 192.168.1.10:8080")
                try:
                    self._btn_restart.configure(state=tk.NORMAL)
                except Exception:
                    pass
        except Exception:
            pass

    def _reset_addr(self) -> None:
        """一键切回本机 (构造时传进来的 host:port)。"""
        self._addr_var.set(f"{self._local_host}:{self._local_port}")
        self._apply_addr()

    def _apply_addr(self) -> None:
        """应用「监控地址」格: 改轮询目标 + 立即刷新 + 存进 user_preferences.json。"""
        raw = self._addr_var.get()
        try:
            host, port = parse_host_port(raw, self._local_host, self._local_port)
        except ValueError as e:
            # 不弹模态框: 输入框回车时弹窗很烦, 直接红字写在旁边
            self._addr_state_var.set(f"地址格式错误: {e}")
            self._addr_state_lbl.configure(foreground="#cc0000")
            return
        changed = (host, port) != (self._host, self._port)
        self._host, self._port = host, port
        self._remote = not is_local_host(host)
        self._addr_var.set(f"{host}:{port}")
        self._relay_port = self._load_relay_port()
        if changed:
            # 换地址不该把上一个地址的日志和偏移混进来
            self._remote_offsets.clear()
            self._clear_logs()
        # 清掉上一个地址留下的状态, 否则在下一轮轮询完成前, 界面还挂着旧地址的数据
        self._http_state = {"health": {"status": "连接中…"},
                            "models": [], "ts": 0.0}
        self._http_addr = f"{host}:{port}"
        self._http_kick.set()
        self._sync_remote_hint()
        self._render_status()
        addr = f"{host}:{port}"
        try:
            prefs = config.load_prefs()
            # 一次写盘: 上次填的地址 + 历史 (置顶 / 去重 / 按上限截断)
            hist = [addr] + [x for x in (prefs.get("monitor_addr_history") or [])
                             if str(x).strip() and str(x).strip() != addr]
            hist = hist[: self._clamp_history_max(prefs.get("monitor_addr_history_max"))]
            prefs["monitor_addr"] = addr
            prefs["monitor_addr_history"] = hist
            config.save_prefs(prefs)
        except Exception as e:
            self._addr_state_var.set(f"已切换 (地址未保存: {e})")
            return
        self._addr_history = hist
        try:
            self._addr_entry.configure(values=list(hist))
        except Exception:
            pass
        self.stat_var.set(f"监控地址已切换到 {addr}")

    # ----------------- 监控地址历史 (下拉选之前填过的) -----------------
    @staticmethod
    def _clamp_history_max(value: Any) -> int:
        """历史保留条数: 1..50, 脏值回落 10 (和 config._clean_addr_history_max 一致)。"""
        try:
            n = int(value)
        except (TypeError, ValueError):
            return 10
        return min(50, max(1, n))

    def _history_load(self) -> List[str]:
        """读历史 (config.load_prefs 里已经清洗过类型/去重/截断)。"""
        try:
            return list(config.load_prefs().get("monitor_addr_history") or [])
        except Exception:
            return []

    def _history_max(self) -> int:
        try:
            return self._clamp_history_max(
                config.load_prefs().get("monitor_addr_history_max"))
        except Exception:
            return 10

    def _history_refresh(self) -> None:
        """把历史回填到下拉 (构造时 + 每次写盘后调)。"""
        self._addr_history = self._history_load()
        try:
            self._addr_entry.configure(values=list(self._addr_history))
        except Exception:
            pass

    def _history_set_max(self, n: int) -> List[str]:
        """改保留条数并立刻按新条数截断历史, 写回 prefs。"""
        n = self._clamp_history_max(n)
        hist = self._history_load()[:n]
        try:
            prefs = config.load_prefs()
            prefs["monitor_addr_history_max"] = n
            prefs["monitor_addr_history"] = hist
            config.save_prefs(prefs)
        except Exception:
            pass
        self._addr_history = hist
        try:
            self._addr_entry.configure(values=list(hist))
        except Exception:
            pass
        return hist

    def _history_clear(self) -> None:
        """清空历史 (保留条数设置不动)。"""
        try:
            prefs = config.load_prefs()
            prefs["monitor_addr_history"] = []
            config.save_prefs(prefs)
        except Exception:
            pass
        self._addr_history = []
        try:
            self._addr_entry.configure(values=[])
        except Exception:
            pass

    def _on_addr_click(self, _event=None) -> None:
        """点格子就下拉出历史地址 (Tk 默认要点右侧小箭头)。

        已经弹着的时候不动它 — 交给 Tk 的类绑定去关,
        这样「点一下开、再点一下关」的切换才成立。
        """
        if not self._addr_history:
            return
        try:
            pop = self._addr_entry.tk.call("ttk::combobox::PopdownWindow",
                                           self._addr_entry)
            if int(self._addr_entry.tk.call("winfo", "viewable", pop)):
                return
        except Exception:
            pass
        self.after_idle(self._post_history)

    def _post_history(self) -> None:
        try:
            if str(self._addr_entry.cget("state")) == "disabled":
                return
            self._addr_entry.tk.call("ttk::combobox::Post", self._addr_entry)
        except Exception:
            pass

    def _open_hist_settings(self) -> None:
        """⚙弹窗: 调保留条数 / 看清存了哪些 / 一键清空。"""
        win = tk.Toplevel(self)
        win.title("监控地址历史")
        win.transient(self)
        win.resizable(False, False)

        body = ttk.Frame(win, padding=12)
        body.pack(fill=tk.BOTH, expand=True)

        hist = self._history_load()
        row = ttk.Frame(body)
        row.pack(fill=tk.X)
        ttk.Label(row, text="保留条数 (1-50):").pack(side=tk.LEFT)
        var = tk.StringVar(value=str(self._history_max()))
        spin = ttk.Spinbox(row, from_=1, to=50, textvariable=var, width=6)
        spin.pack(side=tk.LEFT, padx=(6, 0))
        cnt = tk.StringVar(value=f"当前 {len(hist)} 条")
        ttk.Label(body, textvariable=cnt, foreground="#888").pack(anchor=tk.W,
                                                                  pady=(8, 0))

        lst = tk.Listbox(body, width=36, height=min(8, max(3, len(hist))),
                         font=("Consolas", 9))
        lst.pack(fill=tk.X, pady=(4, 0))
        for h in hist:
            lst.insert(tk.END, h)

        def _reload(items: List[str]) -> None:
            lst.delete(0, tk.END)
            for h in items:
                lst.insert(tk.END, h)
            cnt.set(f"当前 {len(items)} 条")

        def _save() -> None:
            try:
                n = int(str(var.get()).strip())
            except ValueError:
                n = 10
            n = self._clamp_history_max(n)
            var.set(str(n))
            _reload(self._history_set_max(n))
            self.stat_var.set(f"监控地址历史: 保留最近 {n} 条")

        def _clear() -> None:
            self._history_clear()
            _reload([])
            self.stat_var.set("监控地址历史已清空")

        btns = ttk.Frame(body)
        btns.pack(fill=tk.X, pady=(12, 0))
        ttk.Button(btns, text="保存", command=_save).pack(side=tk.LEFT)
        ttk.Button(btns, text="清空历史", command=_clear).pack(side=tk.LEFT, padx=6)
        ttk.Button(btns, text="关闭", command=win.destroy).pack(side=tk.RIGHT)

        win.bind("<Escape>", lambda _e: win.destroy())
        win.update_idletasks()
        win.geometry("+%d+%d" % (self.winfo_rootx() + 40,
                                 self.winfo_rooty() + 70))
        spin.focus_set()

    # ----------------- 后台: 日志 tail -----------------
    # 主池 / KVMem / Strata 三组的日志都要 tail —— 运行时只有一组在跑,
    # 但用户看到的应该是同一份面板, 不该还要自己去分清「现在看的是哪个文件」。
    _LOG_FILES = ("router.out.log", "router.err.log",
                  "kvmem.out.log", "kvmem.err.log",
                  "strata.out.log", "strata.err.log")
    # 这几个文件只有跑过对应分支才会有, 缺了不算异常 (否则指示器天天报"找不到")
    _LOG_OPTIONAL = ("kvmem.out.log", "kvmem.err.log",
                     "strata.out.log", "strata.err.log")
    # 每个文件单独留多少行 (4 个文件, 总量和原来 out800+err200 一个量级)
    _FILE_LINE_MAX = 300
    # 首次读某个日志文件时, 从尾部往回退这么多字节再开始 (别从 0 把整个文件灌进来)
    _TAIL_SEEK_BACK = 64 * 1024
    # 拖动标志的安全上限(秒): 超过这么久还认为在拖动, 就当卡死强制收尾,
    # 否则 _dragging 一旦为 True 而 _end_drag 排不上, 日志面板会永久静止。
    _DRAG_STUCK = 2.0

    @classmethod
    def _log_paths(cls) -> List[str]:
        log_dir = os.path.join(config.app_dir(), "logs")
        return [os.path.join(log_dir, n) for n in cls._LOG_FILES]

    def _push_file(self, name: str, lines: List[str]) -> None:
        """把一个文件的新行同时写进「按 kind 合并的 buffer」和「按文件的 buffer」。

        合并的 `_log_buffer` 保留给健康指示器/旧逻辑用; 渲染走按文件的那份,
        这样才能按「谁最后有新内容」排序, 见 `_render_logs`。

        **纯轮询行在这里丢掉。** 后台确实得轮询 (网页版拿 /v1/models 列模型、
        拿 /props?autoload=false 当「服务起没起」的探针、EventSource 探
        /models/sse), 那是正常保活, 不是故障。但引擎日志里每 2 秒一条
        `GET /v1/models 200 OK`、每行还带个**不同的临时源端口**, 几百行下来把
        真正的生成/加载/错误全埋了 —— 用户看到的就是「不停在换端口调模型」。
        折叠掉, 想查的时候把「过滤轮询」勾去掉。
        """
        if not lines:
            return
        import time as _time
        kind = "out" if name.endswith(".out.log") else "err"
        merged = self._log_buffer[kind]
        per = self._file_lines.get(name)
        if per is None:
            per = deque(maxlen=self._FILE_LINE_MAX)
            self._file_lines[name] = per
        filt = bool(getattr(self, "_filter_poll", True))
        for ln in lines:
            ln = ln.rstrip("\n")
            if filt and _is_poll_noise(ln):
                self._poll_dropped = getattr(self, "_poll_dropped", 0) + 1
                continue
            merged.append(ln)
            per.append(ln)
            self._note_line(ln)
        self._file_seen[name] = _time.time()

    def _drop_kind(self, name: str) -> None:
        """文件被轮转/清空: 合并 buffer 和该 kind 下所有文件 buffer 一起丢。"""
        kind = "out" if name.endswith(".out.log") else "err"
        self._log_buffer[kind].clear()
        for k in [k for k in self._file_lines
                  if k.endswith(".out.log" if kind == "out" else ".err.log")]:
            self._file_lines.pop(k, None)
            self._file_seen.pop(k, None)

    _SPEED_RE = re.compile(
        r"n_gen\s*=\s*(\d+).*?tg\s*=\s*([\d.]+)\s*t/s.*?tg_3s\s*=\s*([\d.]+)")

    def _note_line(self, line: str) -> None:
        """记录一行日志时顺手解析生成速度 (本地读文件和远程拉转发共用)。"""
        import time as _time
        m = self._SPEED_RE.search(line)
        if m:
            self._last_speed["__latest__"] = {
                "n_gen": int(m.group(1)),
                "tg": float(m.group(2)),
                "tg_3s": float(m.group(3)),
                "ts": _time.time(),
            }

    def _tail_loop(self) -> None:
        """后台线程: 持续读日志新内容 -> buffer。

        数据源跟着「监控地址」走: 本机直接 tail 文件, 远程走 log_relay 转发。
        文件被 NSSM 轮转时 (router.out.log -> router.out-{ts}.log) 大小会缩小,
        此时 last_pos > new_size, 需要从 0 开始重新读。
        """
        import time as _time
        while not self._stop:
            try:
                if self._remote:
                    missing, err, read_ok = self._tail_remote_once()
                else:
                    missing, err, read_ok = self._tail_local_once()
                self._tail_missing = missing
                self._tail_err = err
                self._tail_read_ok = read_ok
                self._tail_last_ok = _time.time()
            except Exception as e:
                self._tail_err = f"tail 循环: {type(e).__name__}: {e}"
            _time.sleep(0.3)

    def _tail_local_once(self) -> Tuple[List[str], str, bool]:
        """读一次本机日志文件, 返回 (缺失文件, 异常, 是否至少读成功一个)。"""
        missing: List[str] = []
        err = ""
        read_ok = False
        for path in self._log_paths():
            if not os.path.isfile(path):
                # 这几个文件只有跑过对应分支才会有, 缺了不算异常
                if os.path.basename(path) not in self._LOG_OPTIONAL:
                    missing.append(os.path.basename(path))
                continue
            try:
                # 先看文件大小, 处理轮转/截断的情况
                try:
                    cur_size = os.path.getsize(path)
                except OSError:
                    continue
                # 打开时别从文件开头灌: kvmem.err.log 这种能到几 MB, 从 0
                # 读会把**几小时前的历史**全倒进面板 —— 用户以为「不停在出现」,
                # 其实那些是旧行 (启动横幅、几轮早就修好的 pid 冲突…)。
                # 首次读这个文件时从尾部往回退 _TAIL_SEEK_BACK 再开始。
                first_time = path not in self._last_log_pos
                last_pos = self._last_log_pos.get(path, 0)
                if first_time and cur_size > self._TAIL_SEEK_BACK:
                    last_pos = cur_size - self._TAIL_SEEK_BACK
                # **首次见到这个文件时, 用文件自己的 mtime 当「最后吐字时间」。**
                # 原来只有真读到新行才写 _file_seen, 于是冷启动时所有文件的
                # _file_seen 都缺省成「刚刚」-> 渲染里的「安静/活跃」二分判定
                # 全部命中「活跃」-> 退化成先来先排 -> strata.out.log 永远垫底,
                # 而「跟跳」看的就是最底下, 于是面板底部挂着几小时前的
                # "[strata] stopping", 真正在写的 kvmem 埋在中间 —— 用户看到的
                # 就是「模型在跑, 日志却是个老日志」。
                # 取 mtime 才能在**第一次渲染**就分对, 不必等 30 秒超时。
                if first_time:
                    try:
                        self._file_seen[os.path.basename(path)] = os.path.getmtime(path)
                    except OSError:
                        pass
                # 文件被轮转/截断 -> 从头开始
                if last_pos > cur_size:
                    last_pos = 0
                    self._last_log_pos[path] = 0
                with open(path, "r", encoding="utf-8", errors="replace") as f:
                    f.seek(last_pos)
                    new_lines = f.readlines()
                    self._last_log_pos[path] = f.tell()
                read_ok = True
            except Exception as e:
                # 之前这里是 continue: 读失败被静默吞掉, 界面上只表现为"日志不动"
                err = f"{os.path.basename(path)}: {type(e).__name__}: {e}"
                continue
            if not new_lines:
                continue
            self._tail_lines += len(new_lines)
            self._push_file(os.path.basename(path), new_lines)
            # 必须放在 _push_file **之后**: 它结尾会无条件把 _file_seen 写成
            # 「刚刚读到」, 在前面设的 mtime 会被冲掉。冷启动这一轮读到的其实
            # 是 64KB 回看窗口里的历史内容, 不代表「刚刚在写」, 所以这里用
            # mtime 把它还原回去, 渲染的安静/活跃二分才判得对。
            if first_time:
                try:
                    self._file_seen[os.path.basename(path)] = os.path.getmtime(path)
                except OSError:
                    pass
        return missing, err, read_ok

    def _tail_remote_once(self) -> Tuple[List[str], str, bool]:
        """向远程 log_relay 拉一次增量日志, 返回 (缺失, 异常, 是否读成功)。"""
        missing: List[str] = []
        err = ""
        read_ok = False
        for name in self._LOG_FILES:
            since = int(self._remote_offsets.get(name, 0))
            url = (f"http://{self._host}:{self._relay_port}"
                   f"/logs/{name}?since={since}")
            try:
                with httpx.open_url(url, timeout=3) as r:
                    chunk = json.loads(r.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                if e.code == 404:
                    if name not in self._LOG_OPTIONAL:
                        missing.append(name)
                else:
                    err = f"{name}: HTTP {e.code}"
                continue
            except Exception as e:
                err = f"{name}: {type(e).__name__}: {e}"
                # 转发服务连不上时后面几个文件必然也连不上, 先退出本轮:
                # 否则每个文件都干等一个超时 (4 个文件 = 4×3s),
                # 顶部指示器迟迟不报"日志转发不可达"。
                break
            read_ok = True
            self._remote_offsets[name] = int(chunk.get("offset") or 0)
            data = chunk.get("data") or ""
            if not data:
                continue
            if chunk.get("reset"):
                # 远程文件被轮转: 服务端已经从头给了, 旧内容要丢掉否则重复
                self._drop_kind(name)
            lines = [ln.rstrip("\n") for ln in data.split("\n")]
            if lines and lines[-1] == "":
                lines.pop()
            self._tail_lines += len(lines)
            self._push_file(name, lines)
        return missing, err, read_ok

    # ----------------- 后台: HTTP 轮询 -----------------
    def _http_loop(self) -> None:
        import time as _time
        while not self._stop:
            try:
                base = f"http://{self._host}:{self._port}"
                # /health
                try:
                    with httpx.open_url(f"{base}/health", timeout=1.0) as r:
                        health = json.loads(r.read().decode("utf-8"))
                except Exception as e:
                    health = {"status": f"无法连接: {e}"}
                # /v1/models
                try:
                    with httpx.open_url(f"{base}/v1/models", timeout=1.0) as r:
                        models_data = json.loads(r.read().decode("utf-8"))
                except Exception:
                    models_data = {"data": []}
                # /status: 实时进度 (阶段/prefill/生成/tok_s)。网关对没有这个
                # 端点的后端会合成空壳, 所以这里永远拿得到字段。
                try:
                    with httpx.open_url(f"{base}/status", timeout=1.0) as r:
                        status_data = json.loads(r.read().decode("utf-8"))
                except Exception:
                    status_data = {}
                self._http_state = {
                    "health": health,
                    "models": models_data.get("data", []),
                    "status": status_data,
                    "ts": _time.time(),
                }
            except Exception:
                self._http_state = {"health": {"status": "异常"}, "models": [],
                                    "status": {}, "ts": 0}
            # 固定 2 秒一轮 = 光这个线程就 1 次/秒 (/health + /v1/models),
            # 引擎/网关日志里那些轮询大半是我们自己制造的。**没变化就退避**:
            # 内容不变时逐步拉长到 15 秒, 一有变化立刻回到 2 秒。改了监控地址
            # 会 _http_kick.set() 立即醒来, 不受影响。
            sig = json.dumps([health, models_data, status_data],
                             sort_keys=True, default=str)
            if sig == getattr(self, "_http_last_sig", None):
                self._http_backoff = min(15.0, max(
                    2.0, getattr(self, "_http_backoff", 2.0) * 1.6))
            else:
                self._http_last_sig = sig
                self._http_backoff = 2.0
            self._http_kick.wait(self._http_backoff)
            self._http_kick.clear()

    # ----------------- UI 刷新 -----------------
    def _remote_tail_hint(self, err: str = "") -> str:
        """把 _tail_err 翻译成用户能照做的一步。

        三层原因必须分清 (都是在 192.168.x.x 上实测出来的):
          超时          → 远程防火墙**静默丢包** (只放行了 8080, 其它端口不回包)
          10061/refused → 端口通了但没人监听 (没勾日志转发 / 远程版本过旧)
          404           → 转发服务在跑, 只是还没有那个日志文件
        """
        port = self._relay_port
        raw = err or ""
        e = raw.lower()
        if "timed out" in e or "timeout" in e or "超时" in raw:
            return (f"远程防火墙拦了端口 {port} (静默丢包表现为超时): "
                    f"去远程那台开本软件 → 全局 → 日志转发 → 点「🛡 放行端口」")
        if "10061" in e or "refused" in e or "积极拒绝" in raw:
            return (f"远程 {port} 没人监听: 要么没勾「日志转发」, "
                    f"要么远程版本过旧 (需 ≥0.1.2 才有该功能)")
        if "404" in e:
            return "转发服务正常, 只是还没有 router 日志文件 (服务还没跑过)"
        if "http" in e:
            return f"远程转发服务返回错误: {raw}"
        if "getaddrinfo" in e or "nodename" in e or "name or service" in e:
            return "主机名解析失败, 检查「监控地址」"
        if "10060" in e or "forcibly closed" in e:
            return f"连不到远程 {port}, 检查「监控地址」和网络"
        return f"需远程 llama-router-setup 勾选「日志转发」并放行端口 {port}"

    def _update_log_health(self) -> None:
        """顶部指示器: 直接告诉用户"日志为什么不动" (之前读失败全被静默吞掉)。"""
        import time as _time
        try:
            scope = "远程日志" if self._remote else "日志"
            missing = self._tail_missing
            if self._tail_err:
                hint = ("" if not self._remote else
                        f" → {self._remote_tail_hint(self._tail_err)}")
                self.log_health_var.set(f"⚠ {scope}读取异常 — {self._tail_err}{hint}")
                self.log_health_lbl.configure(foreground="#cc0000")
            elif self._tail_read_ok:
                # 至少有一个日志读成功 -> 正常; 另一个不存在是常见情况 (服务没报错)
                note = f" · 缺 {'+'.join(missing)}" if missing else ""
                # 「读取正常」只说明文件读得到; 内容多久没动要另外写出来,
                # 否则用户看到绿色却盯着一段死日志, 只能自己猜
                idle = (_time.time() - max(self._file_seen.values())
                        if self._file_seen else 0.0)
                if idle > 60:
                    self.log_health_var.set(
                        f"{scope}读取正常 · 已加载 {self._tail_lines} 行{note}"
                        f" · {idle:.0f} 秒没有新行 (服务空闲?)")
                    self.log_health_lbl.configure(foreground="#cc6600")
                else:
                    self.log_health_var.set(
                        f"{scope}读取正常 · 已加载 {self._tail_lines} 行{note}")
                    self.log_health_lbl.configure(foreground="#00aa00")
            elif missing:
                self.log_health_var.set(f"⚠ 找不到{scope}: {' + '.join(missing)}")
                self.log_health_lbl.configure(foreground="#cc0000")
            else:
                age = _time.time() - self._tail_last_ok if self._tail_last_ok else 999
                if self._remote:
                    self.log_health_var.set(
                        f"⚠ {scope}还没连上 (已等 {age:.0f} 秒) → "
                        f"{self._remote_tail_hint('')}")
                else:
                    self.log_health_var.set(
                        f"⚠ {scope} {age:.0f} 秒没有更新 (文件未增长, 服务可能空闲)")
                self.log_health_lbl.configure(foreground="#cc6600")
        except Exception:
            pass

    def _refresh_ui(self) -> None:
        if self._stop:
            return
        try:
            # 日志 buffer -> Text
            # 每轮先清标志: 万一上次渲染中途抛异常, _rendering_logs 会永远停在
            # True, 之后所有滚动事件都被忽略 (跟跳再也不响应)。
            self._rendering_logs = False
            # 看门狗: _begin_drag 是 debounce, 只要 <Configure> 一直在来, 150ms
            # 的 _end_drag 就永远被重排, _dragging 卡在 True -> 日志永久不重绘。
            # <Configure> 不只拖窗口时来 (_refresh_ui 刷状态卡导致控件变尺寸同样
            # 会触发), 一旦形成「刷新 -> 变尺寸 -> Configure -> 跳过渲染 -> 再刷新」
            # 的自反馈, 就再也停不下来。这里兜底: 拖动标志超过 _DRAG_STUCK 秒
            # 就强制收尾, 保证无论什么在持续发 Configure, 日志都能恢复更新。
            if getattr(self, "_dragging", False):
                _since = getattr(self, "_drag_since", 0.0) or time.time()
                if time.time() - _since > self._DRAG_STUCK:
                    self._end_drag()
            # 日志 buffer -> Text
            #
            # **两个跳过条件**, 都是为了拖窗口时的手感:
            # 1) **拖动中**: 拖动时 Tk 事件循环本来就被 <Configure>/<Expose>
            #    占满, 这时候再来一次「删掉整个 Text + 逐行插 1500 行」必然卡
            #    一帧。松手后 _end_drag 会补一次, 不会丢内容。
            # 2) **内容没变**: 每 500ms 一次, 但日志常常几百毫秒才动一行。
            #    签名只用 Python 侧的 buffer 长度, 不碰 Tk, 几乎零成本。
            if not getattr(self, "_dragging", False):
                if self._logs_changed():
                    self._render_logs()
                elif getattr(self, "_pending_render", False):
                    self._pending_render = False
                    self._render_logs()
            # 状态 -> 右侧卡片
            self._render_status()
        except Exception as e:
            self.stat_var.set(f"渲染异常: {e}")
        self._update_log_health()
        self.after(500, self._refresh_ui)

    def _logs_changed(self) -> bool:
        """日志 buffer 跟上次渲染比有没有变 (纯 Python, 不碰 Tk)。"""
        try:
            sig = tuple((n, len(self._file_lines.get(n) or ()))
                        for n in sorted(self._file_lines))
        except Exception:
            return True
        if sig != getattr(self, "_logs_sig", None):
            self._logs_sig = sig
            return True
        return False

    def _end_drag(self, _e=None) -> None:
        """窗口停下来了 -> 补一次渲染, 把拖动期间攒下的内容画出来。"""
        self._dragging = False
        self._drag_timer = None
        # 内嵌滚动区的宽度也补一次 (拖动时被挂起了)
        try:
            fl = getattr(self, "_canvas_flush", None)
            if fl:
                fl()
        except Exception:
            pass
        self._logs_sig = None          # 强制下次判定为「变了」
        self._pending_render = True

    def _begin_drag(self, _e=None) -> None:
        # 只有 False -> True 的**首次**进入才记时间戳。连续 <Configure> 不刷新它,
        # 否则看门狗的时限会被无限往后推, 卡死时也就救不回来了。
        if not getattr(self, "_dragging", False):
            self._drag_since = time.time()
        self._dragging = True
        # 正经的 debounce: 每帧 <Configure> 都会走到这里, 所以每次都把上一个
        # 定时器撤掉重排。只有当 <Configure> 停止 (拖完/没在动) 时, 那个最后
        # 排上的 150ms 定时器才会真的到期 -> _end_drag 补渲染。
        prev = getattr(self, "_drag_timer", None)
        if prev is not None:
            try:
                self.after_cancel(prev)
            except Exception:
                pass
        self._drag_timer = self.after(150, self._end_drag)

    def _force_follow(self) -> None:
        """滚到底并重新开启自动跟跳 (供「立即刷新」按钮调用)。"""
        self._follow_tail = True
        self.follow_var.set("📍 跟跳: ON")
        self.follow_lbl.configure(foreground="#00aa00")
        try:
            self.log_text.configure(state=tk.NORMAL)
            self.log_text.see(tk.END)
            self.log_text.configure(state=tk.DISABLED)
        except Exception:
            pass
        self.stat_var.set("已滚到底, 恢复自动跟跳最新日志")
        # 立即刷新一次
        self._render_logs()

    def _render_logs(self) -> None:
        # 按「谁最后吐过新行」排序: 没在动的文件排前面, 面板最下面永远是
        # **当前活跃**那组日志的尾巴。以前固定「err 两百行 + out 八百行」,
        # 而主池不跑时 router.out.log 几小时不动, 自动跟跳跳到底看到的
        # 一直是那段死日志 —— 用户感受就是「日志完全没更新」。
        # 文件顺序必须**稳定**。以前按 _file_seen(最后吐字时间) 排序, 意图是让
        # 活跃日志排在最下面; 但模型一加载, 某个文件突然开始吐字 -> 它的
        # _file_seen 跳到最新 -> 整个 buffer 在 Text 里的位置全变了 -> 视图
        # 「回跳到模型加载行」。改成: 已见过的文件保持原顺序, 新文件追加到末尾。
        _prev = getattr(self, "_file_order", None) or []
        _known = set(_prev)
        order = [n for n in _prev if n in self._file_lines]
        order += sorted(n for n in self._file_lines if n not in _known)
        # **安静的上, 活跃的下, 组内顺序不动。**
        # 只按「先来先排」的话, strata.out.log 早被见过就永远钉在末尾 —— 而
        # 「跟跳」是 see(tk.END), 面板最底下就成了 strata 那句几小时前的
        # "stopping", 真正在写的 kvmem 反而埋在它上面。用户看到的正是
        # 「模型在跑, 日志却是个老日志」。
        # 曾经按活跃度整体排序, 但那样每来一批新日志整个 buffer 的位置都会变,
        # 视图反复回跳 (见下面 _follow_top 用行号而不是比例的原因)。所以这里
        # 只做「安静/活跃」二分: 组内稳定不跳, 跨组只在安静满 _QUIET 秒时挪一次。
        _now = time.time()
        _QUIET = 30.0
        _seen = getattr(self, "_file_seen", None) or {}
        _age = lambda n: _now - _seen.get(n, 0.0)
        order = [n for n in order if _age(n) > _QUIET] + \
                [n for n in order if _age(n) <= _QUIET]
        self._file_order = order
        all_lines: List[Tuple[str, str]] = []          # (kind, line)
        for name in order:
            kind = "out" if name.endswith(".out.log") else "err"
            for ln in self._file_lines[name]:
                all_lines.append((kind, ln))
        if not all_lines:
            # 兜底: 只有合并 buffer 有内容时 (直接往 _log_buffer 塞的老路径)
            out_lines = list(self._log_buffer["out"])
            err_lines = list(self._log_buffer["err"])
            if not out_lines and not err_lines:
                return
            all_lines = ([("err", ln) for ln in err_lines[-200:]]
                         + [("out", ln) for ln in out_lines[-800:]])
        all_lines = all_lines[-1500:]
        # 记住**顶部可见行的行号**, 而不是 yview 比例。比例是相对的: 新日志
        # 不断追加时总行数在变, 同一个比例在重建后对应的是另一行 -> 用户手动
        # 往上翻, 每 500ms 还在慢慢漂。行号也不行: 见下面 anchor_txt 的说明。
        anchor_txt = None
        anchor_frac = 0.0
        if not self._follow_tail:
            try:
                # **记这一行的内容, 不记它的行号。**
                # 行号是相对的: 这份 buffer 每次全量重建, 而重建时
                #   - 「安静/活跃」排序会把整块文件搬到别处
                #   - all_lines[-1500:] 会从顶上砍掉溢出的行
                # 两者都会让行号整体位移, see(行号) 于是落到别的行上 ——
                # 用户表现就是「往回翻之后日志自己在乱跳」。
                # 记文本内容, 重建后用 search 反查回来, 位移多少都不影响。
                anchor_txt = self.log_text.get("@0,0", "@0,0 lineend")
                try:
                    anchor_frac = self.log_text.yview()[0]
                except Exception:
                    anchor_frac = 0.0
            except Exception:
                anchor_txt = None

        self.log_text.configure(state=tk.NORMAL)
        # 全量重绘 (buffer 已经限长, 可直接写)
        # _rendering_logs 期间忽略一切滚动事件: 全量删除+插入会让 yview 复位,
        # 以前把跟跳误判成「用户在翻历史」而暂停。
        self._rendering_logs = True
        self.log_text.delete("1.0", tk.END)
        for kind, ln in all_lines:
            tag = "info"
            low = ln.lower()
            if "error" in low or "fail" in low or "exception" in low:
                tag = "error"
            elif "warn" in low:
                tag = "warn"
            elif "load_model" in ln or "loaded" in low:
                tag = "load"
            elif "slot" in low:
                tag = "slot"
            if "n_gen" in ln and "t/s" in ln:
                tag = "gen_speed"
            prefix = "E " if kind == "err" else "  "
            self.log_text.insert(tk.END, prefix + ln + "\n", tag)

        # 跟跳模式: 强制到底
        # 否则: 恢复到删除前的滚动位置 (避免跳到顶)
        if self._follow_tail:
            self.log_text.see(tk.END)
            self._follow_top = self.log_text.yview()[0]
        elif anchor_txt:
            # 用户在翻历史 -> 按**内容**钉住原来那一行, 谁也别动
            try:
                _probe = anchor_txt.strip()[:120]
                _idx = self.log_text.search(_probe, "1.0", tk.END,
                                            backwards=True) if _probe else ""
                if _idx:
                    self.log_text.see(_idx)
                else:
                    # 这一行已经被挤出 buffer -> 按比例兜底, 别硬跳到顶
                    self.log_text.yview_moveto(anchor_frac)
            except Exception:
                pass
            self._follow_top = self.log_text.yview()[0]
        self._rendering_logs = False
        # 折叠计数显示在开关上 (在 Tk 线程里做, _push_file 跑在 tail 线程不能碰控件)
        try:
            _n = getattr(self, "_poll_dropped", 0)
            self.filter_poll_lbl.configure(
                text=("过滤轮询 (%d)" % _n)
                if (_n and self.filter_poll.get()) else "过滤轮询")
        except Exception:                                # noqa: BLE001
            pass
        self.log_text.configure(state=tk.DISABLED)

    def _render_progress(self, st: dict) -> None:
        """进度条: 读网关 /status, 直观显示 prefill / 生成。

        Strata 的 /status 有 state/phase/prompt_tokens/generated/max_tokens/
        tok_s/elapsed_s; KVMem/主池是合成空壳 (state=idle, 其余 None)。
        - 生成中: 进度条 = generated/max_tokens, 文案带 tok/s
        - 读 prompt 中 (还没出第一个 token): 进度条走不定长, 文案带已读 token 数
        - 空闲/未加载: 进度条归零
        """
        s = st.get("status") or {}
        # Strata 的 /status 顶层字段是 busy/phase/generated/max_tokens/
        # prompt_tokens, **没有 state/tok_s/elapsed_s** (实测)。KVMem/主池走网关
        # 合成空壳, 有 state 没有 busy。所以两种都要认。
        state = str(s.get("state") or "")
        busy = bool(s.get("busy"))
        generating = busy or state == "generating"
        phase = str(s.get("phase") or "")
        gen = s.get("generated")
        mx = s.get("max_tokens")
        ptok = s.get("prompt_tokens")
        tok_s = s.get("tok_s")
        el = s.get("elapsed_s")
        # tok_s 不在 /status 里 -> 退回日志 tail 解析出的实时速度
        if tok_s is None:
            latest = getattr(self, "_last_speed", {}).get("__latest__", {})
            tok_s = latest.get("tg")
        prefill = generating and (gen in (None, 0))
        try:
            if generating and not prefill and gen is not None:
                frac = 0.0
                if mx:
                    frac = max(0.0, min(1.0, float(gen) / float(mx)))
                if getattr(self, "_prog_mode", None) != "determinate":
                    try:
                        self.prog_bar.stop()
                    except Exception:
                        pass
                    self.prog_bar.configure(mode="determinate")
                    self._prog_mode = "determinate"
                self.prog_bar["value"] = round(frac * 100, 1)
                rate = f"{tok_s:.1f} tok/s" if isinstance(tok_s, (int, float)) else "—"
                eltxt = f", {el:.0f}s" if isinstance(el, (int, float)) else ""
                self.prog_var.set(
                    f"进度: {phase or '生成'} {gen}/{mx if mx else '?'} tokens, {rate}{eltxt}")
                self.prog_lbl.configure(foreground="#0066cc")
            elif prefill:
                if getattr(self, "_prog_mode", None) != "indeterminate":
                    self.prog_bar.configure(mode="indeterminate")
                    self.prog_bar.start()
                    self._prog_mode = "indeterminate"
                pt = f"{ptok:,}" if isinstance(ptok, int) else "?"
                eltxt = f", {el:.0f}s" if isinstance(el, (int, float)) else ""
                self.prog_var.set(f"进度: 读取 prompt {pt} tokens{eltxt} (prefill)")
                self.prog_lbl.configure(foreground="#cc6600")
            else:
                if getattr(self, "_prog_mode", None) != "determinate":
                    try:
                        self.prog_bar.stop()
                    except Exception:
                        pass
                    self.prog_bar.configure(mode="determinate")
                    self._prog_mode = "determinate"
                self.prog_bar["value"] = 0
                txt = "空闲" if state in ("idle", "") else (state or "未加载")
                self.prog_var.set(f"进度: {txt}")
                self.prog_lbl.configure(foreground="#888888")
        except Exception:
            pass

    def _render_status(self) -> None:
        """更新状态条 (模型标签原地更新, 避免 destroy/recreate 导致 tooltip 闪烁)。"""
        # 没有轮询结果时也不能 early-return: 那样界面会一直挂着上一个地址的旧数据,
        # 看起来就是"填了远程地址但内容没更新"。
        st = getattr(self, "_http_state", None) or {
            "health": {"status": "等待首次轮询…"}, "models": [], "ts": 0.0}
        health = st.get("health", {})
        hstatus = str(health.get("status", "?"))
        if hstatus == "ok":
            hcolor = "#00aa00"
        elif hstatus in ("连接中…", "等待首次轮询…"):
            hcolor = "#cc6600"
        else:
            hcolor = "#cc0000"
        # 带上地址: 盯着远程电脑时, 一眼能看出这一行状态是谁的
        self.health_var.set(f"[{self._http_addr}] 健康: {hstatus}")
        self.health_lbl.configure(foreground=hcolor)
        # 侧标签要反映**真实连接结果**, 不能只凭"你填了个远程地址"就写远程监控中
        if self._remote:
            if hstatus == "ok":
                self._addr_state_var.set("已连接")
                self._addr_state_lbl.configure(foreground="#00aa00")
            elif hstatus in ("连接中…", "等待首次轮询…"):
                self._addr_state_var.set("连接中")
                self._addr_state_lbl.configure(foreground="#cc6600")
            else:
                self._addr_state_var.set("无法连接")
                self._addr_state_lbl.configure(foreground="#cc0000")
        models = st.get("models", [])
        self._render_progress(st)
        # 生成速度是从日志 tail 出来的; 没拿到远程日志之前不显示, 避免冒充
        latest = self._last_speed.get("__latest__", {})

        # 与现有标签对齐: 按 model id 复用现有 Label, 没有则新建
        existing = {}
        for c in self.models_strip_frame.winfo_children():
            mid = getattr(c, "_model_id", None)
            if mid is not None:
                existing[mid] = c
            else:
                # 不属于本系统的标签 (例如 "(无可用模型)"), 删除
                c.destroy()

        seen_ids = set()
        for m in models:
            mid = m.get("id", "?")
            seen_ids.add(mid)
            status = model_status_value(m)
            if status == "loaded":
                stxt, scolor = "●", "#00aa00"
            elif status == "sleeping":
                # 已经在内存里只是闲置, 是"常驻"而不是"没加载"
                stxt, scolor = "●", "#0088cc"
            elif status == "unloaded":
                stxt, scolor = "○", "#888"
            elif status == "loading":
                stxt, scolor = "◐", "#cc6600"
            else:
                stxt, scolor = "?", "#cc6600"
            label_text = f"{stxt} {mid}"
            tip = self._build_model_tooltip(m, latest)
            lbl = existing.get(mid)
            if lbl is None:
                # 不 pack: 位置由 _layout_models 用 place 排 (要能换行)
                lbl = tk.Label(self.models_strip_frame,
                               cursor="question_arrow", padx=6, pady=1)
                self._bind_tooltip(lbl, tip)
                lbl.bind("<Button-1>", lambda _e, mod=m: self._open_model_detail(mod))
            # 原地更新 (保留 widget 实例, 保留所有事件绑定, 避免闪烁)
            lbl.configure(text=label_text, fg=scolor,
                          font=("Segoe UI", 9, "bold"))
            # 更新 tooltip 内容 (延迟到下一次 Enter 触发)
            lbl._tooltip_text = tip
            lbl._model_id = mid
            lbl._model_data = m

        # 删除已下线的模型标签
        for mid, lbl in existing.items():
            if mid not in seen_ids:
                lbl.destroy()
        self._layout_models()

    def _layout_models(self, avail: Optional[int] = None) -> None:
        """把模型标签排成**可换行**的多行布局, **尽量少干活**。

        这个方法挂在 `<Configure>` 上, 而拖动窗口时 Tk 每移动一个像素就触发
        一次 —— 原来每帧都对每个标签问一遍 winfo_reqwidth/reqheight 再 place(),
        一屏十几个标签就是每像素几十次 Tcl 往返, 拖起来明显发涩。

        三处优化:
          1. **缓存标签尺寸** —— reqwidth/reqheight 只在标签重建后才会变,
             平移窗口时不会变, 没必要反复问 Tk。
          2. **宽度没变就不重排** —— 拖动过程中宽度大多数帧是同一个值,
             排布结果必然一样, 直接返回。
          3. **高度相同就不 configure** —— 原来每帧都写 height, 而写 height
             会改变 frame 尺寸 -> 再触发一次 `<Configure>` -> 自反馈。
        """
        placed: List[Tuple[int, int, int]] = []
        if getattr(self, "_in_layout_models", False):
            return placed                        # 重入保护
        self._in_layout_models = True
        try:
            fr = self.models_strip_frame
            kids = fr.winfo_children()
            if not kids:
                fr.configure(height=0)
                return placed
            if avail is None:
                avail = fr.winfo_width()
                if avail <= 1:                   # 还没映射好, 先按需求宽度算一遍
                    avail = max(fr.winfo_reqwidth(), 1)
            sig = (id(fr), len(kids), avail)
            if getattr(self, "_strip_sig", None) == sig:
                # (2) 宽度没变 -> 排布结果和上次一样。但**仍要返回 placed**:
                # 调用方 (单测) 靠这个返回值判断排布是否正确, 不能因为
                # "省掉重排" 就把结果也省了。
                return list(getattr(self, "_strip_placed", ()))
            cache = getattr(self, "_chip_size", None)
            if cache is None:
                cache = self._chip_size = {}
            x = y = 0
            row_h = 0
            for c in kids:
                hit = cache.get(id(c))
                if hit is None:                  # (1) 只问一次
                    hit = (c.winfo_reqwidth(), c.winfo_reqheight())
                    cache[id(c)] = hit
                cw, h = hit
                w = cw + 8                       # 8 = 原来 pack 的 padx + 间隙
                if x and x + w > avail:
                    x = 0
                    y += row_h + 4
                    row_h = 0
                c.place(x=x, y=y)
                placed.append((x, y, cw))
                x += w
                row_h = max(row_h, h)
            need = y + row_h
            if getattr(self, "_strip_height", None) != need:
                self._strip_height = need
                fr.configure(height=need)        # (3) 变了才写
            live = {id(c) for c in kids}
            for k in [k for k in cache if k not in live]:
                cache.pop(k, None)               # 标签被重建过 -> 丢掉旧尺寸
            self._strip_placed = list(placed)
            self._strip_sig = sig
        except Exception:
            pass
        finally:
            self._in_layout_models = False
        return placed
    def _build_model_tooltip(self, m: dict, latest: dict) -> str:
        """构造模型 tooltip 文本 (多行)。"""
        lines = []
        mid = m.get("id", "?")
        lines.append(f"模型: {mid}")
        tags = m.get("tags") or []
        if tags:
            lines.append("组: " + ", ".join(
                {"pool": "主池", "kvmem": "KVMem",
                 "strata": "Strata"}.get(str(t), str(t))
                for t in tags))
        aliases = m.get("aliases", [])
        if aliases:
            lines.append(f"别名: {', '.join(aliases)}")
        status = model_status_value(m)
        lines.append(f"状态: {status}")
        if status in ("loaded", "sleeping"):
            args = model_status_args(m)
            kvs = {}
            for i, a in enumerate(args):
                if a.startswith("--") and i + 1 < len(args) and not args[i+1].startswith("--"):
                    kvs[a[2:]] = args[i + 1]
                elif a.startswith("--") and "=" in a:
                    k, v = a[2:].split("=", 1)
                    kvs[k] = v
            if kvs:
                lines.append("")
                lines.append("参数:")
                for k in ("host", "port", "alias", "ctx-size", "batch-size",
                          "cache-type-k", "cache-type-v", "flash-attn", "threads",
                          "n-gpu-layers", "parallel", "poll", "jinja"):
                    if k in kvs:
                        lines.append(f"  {k} = {kvs[k]}")
        if latest:
            lines.append("")
            lines.append(f"最近生成: {latest.get('n_gen', '?')} tokens")
            lines.append(f"  速度: {latest.get('tg', 0):.1f} t/s (3s平均 {latest.get('tg_3s', 0):.1f} t/s)")
        arch = m.get("architecture", {})
        if arch:
            in_m = arch.get("input_modalities", [])
            out_m = arch.get("output_modalities", [])
            if in_m or out_m:
                lines.append(f"模态: {'/'.join(in_m) or '?'} -> {'/'.join(out_m) or '?'}")
        meta = m.get("meta", {})
        if meta:
            n_params = meta.get("n_params", 0)
            if n_params:
                lines.append(f"参数: {n_params/1e9:.1f}B")
            ftype = meta.get("ftype", "")
            if ftype:
                lines.append(f"量化: {ftype}")
            n_ctx = meta.get("n_ctx_train", 0)
            if n_ctx:
                lines.append(f"训练上下文: {n_ctx}")
        if self._remote:
            lines.append("")
            lines.append(f"⚠ 监控地址 {self._http_addr} 为远程")
            if self._tail_read_ok:
                lines.append(f"  日志/生成速度来自远程转发 {self._host}:{self._relay_port}")
            else:
                lines.append("  远程日志未连上 → " + self._remote_tail_hint(self._tail_err))
        return "\n".join(lines)

    def _bind_tooltip(self, widget, text: str) -> None:
        """为 widget 绑定悬停 tooltip。

        防闪烁设计:
        - 文本从 widget._tooltip_text 读取, _render_status 更新时只改文本
        - 显示/隐藏都有 150ms 延迟, 鼠标在 label 和 tooltip 边界抖动时不闪烁
        - 鼠标进入 tooltip 窗口时, 即使触发 Leave 也不隐藏
        """
        widget._tooltip_text = text
        # 状态变量: [tooltip_window, show_after_id, hide_after_id]
        state = [None, None, None]

        def _show():
            state[1] = None
            if state[0] is not None:
                return
            # 再次检查鼠标是否还在 widget 上 (避免延迟期间鼠标已移开)
            try:
                if not widget.winfo_containing(widget.winfo_rootx() + 2, widget.winfo_rooty() + 2) in (widget,):
                    # 也允许在 tooltip 窗口内
                    if state[0] is None:
                        return
            except Exception:
                pass
            x = widget.winfo_rootx() + 20
            y = widget.winfo_rooty() + widget.winfo_height() + 4
            tw = tk.Toplevel(widget)
            tw.wm_overrideredirect(True)
            tw.wm_attributes("-topmost", True)
            tw.wm_geometry(f"+{x}+{y}")
            tip_text = getattr(widget, "_tooltip_text", text)
            lbl = tk.Label(tw, text=tip_text, justify=tk.LEFT,
                           background="#ffffe0", foreground="#000",
                           font=("Consolas", 9), relief=tk.SOLID, borderwidth=1,
                           padx=8, pady=6)
            lbl.pack()
            state[0] = tw
            # 鼠标进入 tooltip 窗口时, 取消挂起的 hide
            def on_tip_enter(_e):
                if state[2] is not None:
                    widget.after_cancel(state[2])
                    state[2] = None
            def on_tip_leave(_e):
                _hide()
            tw.bind("<Enter>", on_tip_enter)
            tw.bind("<Leave>", on_tip_leave)

        def _hide():
            state[2] = None
            if state[0] is not None:
                try:
                    state[0].destroy()
                except Exception:
                    pass
                state[0] = None

        def on_enter(_e=None):
            # 取消挂起的 hide
            if state[2] is not None:
                widget.after_cancel(state[2])
                state[2] = None
            # 延迟 150ms 显示 (避免快速划过时闪烁)
            if state[0] is None and state[1] is None:
                state[1] = widget.after(150, _show)

        def on_leave(_e=None):
            # 取消挂起的 show
            if state[1] is not None:
                widget.after_cancel(state[1])
                state[1] = None
            # 延迟 200ms 隐藏 (给鼠标移到 tooltip 留时间)
            if state[0] is not None and state[2] is None:
                state[2] = widget.after(200, _hide)

        def on_destroy(_e=None):
            # widget 销毁时清理
            for tid in (state[1], state[2]):
                if tid is not None:
                    try:
                        widget.after_cancel(tid)
                    except Exception:
                        pass
            if state[0] is not None:
                try:
                    state[0].destroy()
                except Exception:
                    pass

        widget.bind("<Enter>", on_enter)
        widget.bind("<Leave>", on_leave)
        widget.bind("<Destroy>", on_destroy)

    def _open_model_detail(self, m: dict) -> None:
        """打开一个窗口显示该模型的完整信息。"""
        win = tk.Toplevel(self)
        mid = m.get("id", "?")
        win.title(f"模型详情 - {mid}")
        win.geometry("700x500")
        win.minsize(500, 350)
        text = tk.Text(win, wrap=tk.WORD, font=("Consolas", 9),
                       background="#fafafa", padx=10, pady=10)
        ysb = ttk.Scrollbar(win, orient=tk.VERTICAL, command=text.yview)
        text.configure(yscrollcommand=ysb.set)
        text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        ysb.pack(side=tk.RIGHT, fill=tk.Y)
        # 完整 JSON
        text.insert("1.0", json.dumps(m, ensure_ascii=False, indent=2))
        text.configure(state=tk.DISABLED)
        # 关闭按钮
        btn_frame = ttk.Frame(win, padding=6)
        btn_frame.pack(fill=tk.X)
        ttk.Button(btn_frame, text="关闭", command=win.destroy).pack(side=tk.RIGHT)


# =========================================================================
# 主应用
# =========================================================================
class ModelWorkspace:
    """一个「模型工作区」= 模型列表 + 基础信息编辑器 + 推测解码 + 视觉/KV + 参数面板。

    「模型」页(主池) 与「⚡ KVMem」页(档位) 结构完全一致, 只有两处不同:
      * 过滤条件  —— 各管各的清单, kvmem 条目不再混进模型页
      * 参数 scope —— KVMem 页是「模型页全部 + kvmem 专属」
    模型页直接把属性放在 App 上 (self.xxx), 本类的实例供 KVMem 页使用,
    两边由同一套 _build_* / _on_* 方法构建, 保证界面逐项一致。
    """

    def __init__(self, kind: str) -> None:
        self.ws_kind = kind                  # "pool" | "kvmem"
        self.model_idx: List[int] = []       # listbox 下标 -> cfg["models"] 下标
        self.selected_cfg: Optional[int] = None
        self.selected_id: Optional[str] = None


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("1180x740")
        self.minsize(1000, 640)
        self.cfg: Dict[str, Any] = config.load()
        self._choices = config.list_kinds_choices()
        self._param_panels: List[ParamPanel] = []
        # 每次启动开一份新的选中行诊断日志 (logs/ 不进 git 也不进发布包)
        self._sel_log("__session_start__", truncate=True)
        self._build_ui()
        self._refresh_all()
        self._refresh_env()
        self._poll_status()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        # 首次运行向导 (空配置时)
        self.after(500, self._first_run_wizard)
        # 模型自动换挡 (LRU) 后台守护
        self._guard: Optional[model_guard.ModelGuard] = None
        self._guard_dialog_open = False
        self._guard_pending: Any = None
        self._guard_tick_started = False
        self._guard_beat_started = False
        self._guard_base = f"http://127.0.0.1:{int(self.cfg.get('port', 8080))}"
        self._guard_active_last = True
        self._guard_active_at = 0.0
        self._start_model_guard()
        # 日志转发 (供远程监控窗口 tail 本机日志)
        self._relay: Optional[log_relay.LogRelay] = None
        self._start_log_relay()

    def _first_run_wizard(self) -> None:
        """首次运行/配置为空时, 弹出向导引导用户设置 llama_dir 和添加模型。"""
        try:
            ld = (self.cfg.get("llama_dir") or "").rstrip("\\/")
            models = [m for m in self.cfg.get("models", []) if m.get("enabled", True)]
            # 已有完整配置就不打扰
            if ld and os.path.isdir(ld) and models:
                return
            # 没有任何配置 -> 弹窗
            if not ld or not os.path.isdir(ld):
                if not messagebox.askyesno(
                    "首次运行向导",
                    "看起来是首次运行, 需要先设置 llama.cpp 安装目录 (含 llama-server.exe).\n\n"
                    "是否现在打开「全局」页面设置?",
                ):
                    return
                self.nb.select(1)  # 切到「全局」tab
                return
            if not models:
                if not messagebox.askyesno(
                    "首次运行向导",
                    f"llama_dir 已设置 ({ld}), 但还没有配置模型.\n\n"
                    "是否现在打开「模型」页面添加模型?",
                ):
                    return
                self.nb.select(2)  # 切到「模型」tab
        except Exception:
            pass

    def _on_close(self) -> None:
        if messagebox.askyesno("退出", "确定要退出吗？\n（服务仍会在后台运行）"):
            if self._guard is not None:
                self._guard.stop()
                self._guard = None
            self._clear_guard_beat()
            self._stop_log_relay()
            self.destroy()

    # ---------------- 模型自动换挡 (LRU) ----------------
    def _start_model_guard(self) -> None:
        """按配置启动 model_guard 后台线程 (enabled=false 时不启动)。"""
        mg = self.cfg.get("model_guard", {}) or {}
        if self._guard is not None:
            self._guard.stop()
            self._guard = None
        if not mg.get("enabled", True):
            self._clear_guard_beat()
            return
        base = f"http://127.0.0.1:{int(self.cfg.get('port', 8080))}"
        self._guard_base = base
        self._guard = model_guard.ModelGuard(
            base=base,
            models_max=lambda: int(self.cfg.get("models_max", 1)),
            log_path=os.path.join(config.app_dir(), "logs", "router.err.log"),
            busy_policy=str(mg.get("busy_policy", "ask")),
            poll_interval=float(mg.get("poll_interval_seconds", 1)),
            wanted_ttl=float(mg.get("wanted_ttl_seconds", 30)),
            ask_fn=self._guard_ask,
            log_fn=self._guard_log,
            # KVMem 运行时是单槽, 没有 router 的 /models 换挡语义。
            # 判定用网关自己的 /health (配置只在保存时才写回), 本地配置改动
            # 会比实际运行时慢一拍, 导致"网关说在 KVMem / 守护说不是"的脱节。
            active_fn=self._guard_active,
        )
        self._guard.start()
        if not self._guard_beat_started:
            self._guard_beat_started = True
            self._guard_beat()
        if not self._guard_tick_started:
            self._guard_tick_started = True
            self.after(200, self._guard_tick)

    def _guard_active(self) -> bool:
        """网关当前是不是主池。带 3s 缓存: 守护每秒问一次, 不能每次都去连
        网关 (对端不通时 connect 会拖满超时, 守护线程停不下来)。
        查不到就维持上一次的判断, 免得抖出误导的暂停/恢复日志。"""
        now = time.monotonic()
        if now - self._guard_active_at < GUARD_HEALTH_TTL:
            return self._guard_active_last
        self._guard_active_at = now
        try:
            with httpx.open_url(
                    f"{self._guard_base}/health", timeout=0.5) as r:
                h = json.loads(r.read().decode("utf-8"))
            if "active" in h:
                self._guard_active_last = str(h.get("active") or "") == "pool"
        except Exception:
            pass
        return self._guard_active_last

    def _guard_beat(self) -> None:
        """心跳文件: 网关据此知道桌面版守护还在跑 (活着就让给它弹窗, 不重复卸载)。

        **必须自己一个线程, 绝不能挂在 Tk 的 after 上**。以前是
        `self.after(1000, ...)`, 结果心跳被 UI 自己的模态框饿死:
        `_guard_ask` 会阻塞主线程等用户点按钮, 那个期间 after 不跑, 心跳一停
        超过 `GUARD_BEAT_MAX_AGE`, 服务端守护就以为桌面版死了 -> 抢着接管;
        等用户点完、心跳恢复 -> 又让位。日志里于是「接管换挡 / 服务端守护让位」
        成对刷屏, 看着像在不停换端口, 实际两个守护谁也没多干一点活。
        """
        if self._guard is None:
            self._guard_beat_started = False
            return
        stop = getattr(self, "_guard_beat_stop", None)
        if stop is not None:
            stop.set()
        stop = threading.Event()
        self._guard_beat_stop = stop

        def _beat() -> None:
            while not stop.wait(1.0):
                if self._guard is None:
                    return
                try:
                    with open(GUARD_BEAT_FILE, "w", encoding="utf-8") as f:
                        f.write(str(time.time()))
                except Exception:
                    pass

        threading.Thread(target=_beat, name="guard-beat",
                         daemon=True).start()

    @staticmethod
    def _clear_guard_beat() -> None:
        try:
            os.remove(GUARD_BEAT_FILE)
        except OSError:
            pass

    # ---------------- 日志转发 (供远程监控窗口读取本机日志) ----------------
    def _start_log_relay(self) -> None:
        """按配置启动 log_relay 只读转发服务 (默认关闭)。"""
        lr = self.cfg.get("log_relay", {}) or {}
        self._stop_log_relay()
        if not lr.get("enabled", False):
            return
        try:
            port = int(lr.get("port", log_relay.DEFAULT_PORT))
            if not (1 <= port <= 65535):
                raise ValueError(f"端口范围 1-65535: {port}")
        except (TypeError, ValueError) as e:
            self._log(f"日志转发配置不合法, 未启动: {e}")
            return
        relay = log_relay.LogRelay(os.path.join(config.app_dir(), "logs"), port=port)
        if relay.start():
            self._relay = relay
            self._log(f"日志转发已启动 0.0.0.0:{relay.port} (远程监控窗口可读取本机日志)")
        else:
            self._log(f"日志转发启动失败: {relay.error}")

    def _stop_log_relay(self) -> None:
        relay = getattr(self, "_relay", None)
        if relay is not None:
            relay.stop()
            self._relay = None

    # ----------------- 防火墙放行 (日志转发端口) -----------------
    def _fw_port(self) -> int:
        """按钮/状态标签用的端口: 优先看格子里填的, 不合法就回落已保存配置。"""
        for value in (self.var_lr_port.get() if hasattr(self, "var_lr_port") else None,
                      (self.cfg.get("log_relay") or {}).get("port"),
                      8099):
            try:
                n = int(str(value).strip())
            except (TypeError, ValueError):
                continue
            if 1 <= n <= 65535:
                return n
        return 8099

    def _refresh_fw_status(self) -> None:
        """刷新「端口 N: 已放行/未放行」。netsh 查询约 0.2s, 只在需要时调。"""
        if not hasattr(self, "var_fw_state"):
            return
        port = self._fw_port()
        try:
            self.var_fw_state.set(firewall.status_text(port))
        except Exception as e:
            self.var_fw_state.set(f"端口 {port}: 查询失败 ({e})")

    def _on_fw_allow(self) -> None:
        """后台放行当前端口 (非管理员会弹 UAC)。

        子线程**绝不碰 Tk** (after 从非主线程调用会抛 "main thread is not in
        main loop", 极端情况下直接崩), 结果先塞进 _fw_result, 再由主线程
        自己的定时器来取 —— 和 _guard_tick 同一个套路。
        """
        port = self._fw_port()
        self.status_var.set(f"正在放行防火墙入站端口 {port} …")
        self._fw_result = None

        def task():
            try:
                self._fw_result = firewall.allow_inbound(port)
            except Exception as e:
                self._fw_result = (False, f"异常: {e}")

        threading.Thread(target=task, daemon=True).start()
        self.after(150, self._poll_fw_result)

    def _poll_fw_result(self) -> None:
        try:
            if not self.winfo_exists():
                return
        except Exception:
            return
        r = getattr(self, "_fw_result", None)
        if r is None:
            self.after(150, self._poll_fw_result)
            return
        self._fw_result = None
        self._on_fw_done(bool(r[0]), str(r[1]))

    def _on_fw_done(self, ok: bool, msg: str) -> None:
        self._log(("OK  " if ok else "FAIL") + " | " + msg.replace("\n", " | "))
        if not ok:
            messagebox.showerror("放行失败", msg)
        self._refresh_fw_status()
        self.status_var.set((msg.splitlines() or [""])[0][:80])

    def _guard_tick(self) -> None:
        """主线程定时器: 把守护线程排队的弹窗请求真正弹出来。

        Tk 句柄不能在别的线程里调 (会抛 "main thread is not in main loop"),
        所以 _guard_ask 只负责排队 + 阻塞等结果, messagebox 一律在这里弹。
        """
        pend = self._guard_pending
        if pend is not None:
            victim, wanted, done, box = pend
            self._guard_pending = None
            self._guard_dialog_open = True
            try:
                if not self.winfo_exists():
                    box["a"] = "skip"
                else:
                    r = messagebox.askyesnocancel(
                        "旧模型正在使用中",
                        f"模型「{victim}」正在生成内容。\n"
                        f"但你请求了模型「{wanted}」，需要先卸载「{victim}」才能加载。\n\n"
                        f"是　→ 立即卸载「{victim}」并加载「{wanted}」(当前生成会被中断)\n"
                        f"否　→ 保留「{victim}」，等它这次用完后再自动卸载并切换\n"
                        f"取消 → 本次不处理",
                        parent=self)
                    box["a"] = ("now" if r is True else
                                "wait" if r is False else "skip")
            except Exception:
                box["a"] = "skip"
            finally:
                self._guard_dialog_open = False
                done.set()
        try:
            self.after(200, self._guard_tick)
        except Exception:
            self._guard_tick_started = False

    def _guard_log(self, msg: str) -> None:
        """守护线程 -> Tk 主线程, 写进服务页日志框。"""
        try:
            self.after(0, lambda: self._log("模型换挡 | " + msg))
        except Exception:
            pass

    def _guard_ask(self, victim: str, wanted: str) -> str:
        """守护线程调用: 排队一个弹窗请求, 阻塞等主线程给结果。

        返回 now / wait / skip。超时或已有请求在排队时返回 skip。
        """
        if self._guard_pending is not None:
            return "skip"
        done = threading.Event()
        box: Dict[str, Any] = {}
        self._guard_pending = (victim, wanted, done, box)
        if not done.wait(120.0):
            self._guard_pending = None
            return "skip"
        return str(box.get("a") or "skip")

    def _build_ui(self) -> None:
        top = ttk.Frame(self, padding=(8, 6))
        top.pack(fill=tk.X)
        ttk.Label(top, text="llama.cpp Router 管理",
                  font=("Segoe UI", 12, "bold")).pack(side=tk.LEFT)
        ttk.Button(top, text="❓ 帮助", command=self._on_help).pack(side=tk.RIGHT, padx=2)

        self.nb = ttk.Notebook(self)
        self.nb.pack(fill=tk.BOTH, expand=True, padx=8, pady=4)
        # 标签顺序: 服务(0) / 全局(1) / 模型(2) / ⚡KVMem(3) / 🌐Strata(4)
        #           / 偏好(5) / 环境(6)
        self._build_service_tab()
        self._build_global_tab()
        self._build_models_tab()
        self._build_kvmem_tab()
        self._build_strata_tab()
        self._build_prefs_tab()
        self._build_env_tab()
        self.nb.bind("<<NotebookTabChanged>>", self._on_tab_changed)

        bar = ttk.Frame(self, padding=(8, 4))
        bar.pack(fill=tk.X, side=tk.BOTTOM)
        self.status_var = tk.StringVar(value="就绪")
        ttk.Label(bar, textvariable=self.status_var, anchor=tk.W).pack(side=tk.LEFT)
        self.svc_state_var = tk.StringVar(value="服务状态: 未知")
        ttk.Label(bar, textvariable=self.svc_state_var, anchor=tk.E).pack(side=tk.RIGHT)

    def _on_tab_changed(self, _evt) -> None:
        current = self.nb.index(self.nb.select())
        # 标签顺序: 服务(0) / 全局(1) / 模型(2) / ⚡KVMem(3) / 🌐Strata(4)
        #           / 偏好(5) / 环境(6)
        if current == 6:  # 环境
            self._refresh_env()
            return
        if current in (1, 2, 3):
            # 模型页和 KVMem 页的面板可能持有同一条记录的同一批 key
            # (作用域有交集), 谁被激活谁先从 cfg 重读一遍 —— 否则切页回来
            # 还拿着旧值, 一保存就把另一边刚改的覆盖掉。
            for p in self._param_panels:
                p.reload()
        if current == 3:  # ⚡KVMem
            self._refresh_kvmem_tab()
        if current == 4:  # 🌐Strata
            self._refresh_strata_tab()

    def _build_env_tab(self) -> None:
        f = ttk.Frame(self.nb, padding=10)
        self.nb.add(f, text="环境")

        top = ttk.Frame(f)
        top.pack(fill=tk.X, pady=(0, 8))
        ttk.Label(top, text="环境检查 (新电脑必须先看这里)",
                  font=("Segoe UI", 10, "bold")).pack(side=tk.LEFT)
        ttk.Button(top, text="🔄 重新检查", command=self._refresh_env).pack(side=tk.RIGHT)

        self.env_summary_var = tk.StringVar(value="正在检查...")
        ttk.Label(f, textvariable=self.env_summary_var,
                  font=("Segoe UI", 10), foreground="#0066CC").pack(fill=tk.X, pady=(0, 6))

        box = ttk.LabelFrame(f, text="检查项 (绿色 ✓ 表示通过，红色 ✗ 需要修复)", padding=8)
        box.pack(fill=tk.BOTH, expand=True)
        self.env_items_frame = ttk.Frame(box)
        self.env_items_frame.pack(fill=tk.BOTH, expand=True)
        self._env_rows: List[Dict[str, Any]] = []
        # 提前建好行 (后续 _refresh_env 填充状态)
        check_names = [
            ("python", "Python 3.8+", "运行本工具的 Python"),
            ("tkinter", "tkinter (GUI 库)", "Python 自带的图形库"),
            ("nssm", "NSSM (服务管理)", "把 llama-server 注册为 Windows 服务"),
            ("llama_cpp", "llama.cpp 二进制", "llama-server.exe 所在目录"),
            ("models", "模型文件", "config.json 中启用的所有 .gguf / mmproj"),
            ("admin", "管理员权限", "安装/卸载/启停服务需要"),
        ]
        for key, name, desc in check_names:
            row = ttk.Frame(self.env_items_frame)
            row.pack(fill=tk.X, pady=4)
            row.columnconfigure(2, weight=1)
            status_lbl = ttk.Label(row, text="○", font=("Segoe UI", 14, "bold"), width=2)
            status_lbl.grid(row=0, column=0, padx=(0, 6))
            ttk.Label(row, text=name, font=("Segoe UI", 10, "bold"), width=20, anchor=tk.W
                      ).grid(row=0, column=1, padx=4, sticky=tk.W)
            detail_lbl = ttk.Label(row, text="", foreground="#666", wraplength=600, justify=tk.LEFT)
            detail_lbl.grid(row=0, column=2, padx=4, sticky=tk.W)
            fix_btn = ttk.Button(row, text="修复", width=10, state=tk.DISABLED)
            fix_btn.grid(row=0, column=3, padx=4)
            ttk.Label(row, text=desc, foreground="#999", font=("Segoe UI", 9)
                      ).grid(row=1, column=1, columnspan=3, sticky=tk.W, padx=4)
            self._env_rows.append({
                "key": key, "status_lbl": status_lbl, "detail_lbl": detail_lbl, "fix_btn": fix_btn,
            })

        help_text = (
            "说明:\n"
            "  • Python / tkinter: 用 winget 自动安装 (需本机有 winget，Win10 1809+/Win11 自带)\n"
            "  • NSSM: 项目已内置 tools/nssm.exe，无需额外安装\n"
            "  • llama.cpp 二进制: 从 github.com/ggml-org/llama.cpp/releases 下载最新 release zip，"
            "解压到任意目录 (推荐纯英文路径，如 D:\\llama.cpp)，然后到「全局」页改 llama_dir\n"
            "  • 模型文件 (.gguf): 从 HuggingFace 等来源下载，路径在「模型」页设置"
        )
        ttk.Label(f, text=help_text, foreground="#666", justify=tk.LEFT,
                  wraplength=1000, padding=(8, 12)).pack(fill=tk.X, pady=(8, 0))

    def _refresh_env(self) -> None:
        """重跑所有检查并更新 UI。"""
        items = startup_check.run_all()
        ok_count = sum(1 for it in items if it.ok)
        total = len(items)
        if ok_count == total:
            self.env_summary_var.set(f"✓ 全部就绪 ({ok_count}/{total}) - 可以开始使用")
        else:
            self.env_summary_var.set(
                f"⚠ 发现 {total - ok_count} 项问题 ({ok_count}/{total} 通过) - 点右侧「修复」按钮")

        by_key = {it.key: it for it in items}
        for row in self._env_rows:
            key = row["key"]
            it = by_key.get(key)
            if not it:
                continue
            if it.ok:
                row["status_lbl"].configure(text="✓", foreground="#00aa00")
                row["detail_lbl"].configure(text=it.detail, foreground="#444")
                row["fix_btn"].configure(state=tk.DISABLED, text="已就绪")
            else:
                row["status_lbl"].configure(text="✗", foreground="#cc0000")
                row["detail_lbl"].configure(text=it.detail, foreground="#cc0000")
                if it.fix_label and it.fix_fn:
                    row["fix_btn"].configure(
                        state=tk.NORMAL, text=it.fix_label,
                        command=lambda it=it: self._on_env_fix(it),
                    )
                else:
                    row["fix_btn"].configure(state=tk.DISABLED, text="无自动修复")

    def _on_env_fix(self, it: "startup_check.CheckItem") -> None:
        if not messagebox.askyesno("环境修复",
            f"即将执行：{it.fix_label}\n\n确定继续？"):
            return
        try:
            ok, msg = it.fix_fn()
        except Exception as e:
            ok, msg = False, f"修复函数异常: {e}"
        if ok:
            messagebox.showinfo("已启动", msg + "\n\n完成后点「🔄 重新检查」验证。")
        else:
            messagebox.showerror("失败", msg)
        self._log(f"[环境修复 {it.name}] {msg}")

    def _build_service_tab(self) -> None:
        f = ttk.Frame(self.nb, padding=10)
        self.nb.add(f, text="服务")
        info = ("服务名: llama-router (Windows Service via NSSM)\n"
                "安装后开机自启、崩溃自动重启、关闭终端不影响运行。\n"
                "这个服务跑的是网关 gateway.py，主池 / KVMem / Strata 三组都经它"
                "转发，对外共用同一个端口 config.port (默认 18080，刻意让开 8080，"
                "因为 8080 是 llama-server 和 Strata 的原生默认端口)。\n"
                "改完配置后点「保存并重启服务」即可生效。\n"
                "提示: 安装/卸载/启停、以及切换运行时都需要管理员权限。")
        ttk.Label(f, text=info, justify=tk.LEFT, foreground="#555").grid(
            row=0, column=0, columnspan=4, sticky=tk.W, pady=(0, 8))

        box1 = ttk.LabelFrame(f, text="llama-router 主服务", padding=6)
        box1.grid(row=1, column=0, columnspan=4, sticky=tk.EW, pady=4)
        for i, (label, cmd) in enumerate([
            ("安装", self._on_install), ("卸载", self._on_uninstall),
            ("启动", self._on_start), ("停止", self._on_stop), ("重启", self._on_restart),
            ("前台运行", self._on_run_fg), ("刷新状态", self._on_refresh_status),
        ]):
            ttk.Button(box1, text=label, width=12, command=cmd).grid(
                row=0, column=i, padx=2, pady=2)
        ttk.Button(box1, text="📊 实时监控", width=14,
                   command=self._on_open_monitor).grid(
            row=0, column=len([
                ("安装", self._on_install), ("卸载", self._on_uninstall),
                ("启动", self._on_start), ("停止", self._on_stop), ("重启", self._on_restart),
                ("前台运行", self._on_run_fg), ("刷新状态", self._on_refresh_status),
            ]), padx=2, pady=2)

        box2 = ttk.LabelFrame(f, text="llama-router-watchdog  显存守护", padding=6)
        box2.grid(row=2, column=0, columnspan=4, sticky=tk.EW, pady=4)
        for i, (label, cmd) in enumerate([
            ("安装 watchdog", self._on_install_wd),
            ("卸载 watchdog", self._on_uninstall_wd),
            ("刷新 watchdog 状态", self._on_refresh_wd),
        ]):
            ttk.Button(box2, text=label, width=20, command=cmd).grid(
                row=0, column=i, padx=2, pady=2)
        self.wd_state_var = tk.StringVar(value="watchdog: 未知")
        ttk.Label(box2, textvariable=self.wd_state_var).grid(
            row=1, column=0, columnspan=3, sticky=tk.W, pady=(2, 0))

        box_files = ttk.LabelFrame(f, text="配置文件 (双击打开手动编辑)", padding=6)
        box_files.grid(row=3, column=0, columnspan=4, sticky=tk.EW, pady=4)
        for i, (label, path) in enumerate([
            ("config.json (主配置)", config.CONFIG_PATH),
            ("router-preset.ini (生成的模型预设)", config.preset_path_for(self.cfg)),
            ("user_preferences.json (UI 偏好)", config.PREFS_PATH),
        ]):
            ttk.Button(box_files, text=f"📂 打开 {label}", width=32,
                       command=lambda p=path: self._open_file(p)).grid(
                row=i, column=0, padx=2, pady=2, sticky=tk.W)
            ttk.Label(box_files, text=path, foreground="#666").grid(
                row=i, column=1, padx=8, sticky=tk.W)

        ttk.Label(f, text="操作日志:").grid(row=4, column=0, columnspan=4, sticky=tk.W, pady=(8, 2))
        self.log = tk.Text(f, height=14, wrap=tk.WORD, font=("Consolas", 9))
        self.log.grid(row=5, column=0, columnspan=4, sticky=tk.NSEW)
        f.rowconfigure(5, weight=1)
        f.columnconfigure(3, weight=1)
        ttk.Button(f, text="清空日志", command=lambda: self.log.delete("1.0", tk.END)).grid(
            row=6, column=0, pady=4, sticky=tk.W)

    def _open_file(self, path: str) -> None:
        """用系统默认程序打开文件 (通常是记事本)。"""
        if not os.path.isfile(path):
            messagebox.showerror("文件不存在", f"找不到文件:\n{path}")
            return
        try:
            if sys.platform == "win32":
                os.startfile(path)  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", path])
            else:
                subprocess.Popen(["xdg-open", path])
        except Exception as e:
            messagebox.showerror("打开失败", f"无法打开 {path}\n\n{e}")

    def _on_install(self) -> None:
        self._save_config()
        # 服务页装的是主池: 把运行时标成 router, 分发逻辑照 config 走
        self.cfg["serve_mode"] = "router"
        config.save(self.cfg)
        self._run_bg(service.install, self.cfg, "正在安装服务...")

    def _on_uninstall(self) -> None:
        if not messagebox.askyesno("确认", "确定要卸载 llama-router 服务吗？"):
            return
        self._run_bg(service.uninstall, None, "正在卸载...")

    def _on_start(self) -> None:
        self._run_bg(service.start, None, "启动中...")

    def _on_stop(self) -> None:
        self._run_bg(service.stop, None, "停止中...")

    def _on_restart(self) -> None:
        self._save_config()
        self._run_bg(service.restart, None, "重启中...")

    def _on_run_fg(self) -> None:
        self._save_config()
        ok, msg = service.run_foreground(self.cfg)
        self._log(msg)
        messagebox.showinfo("前台运行", msg)

    def _on_refresh_status(self) -> None:
        self._refresh_status()

    def _on_open_monitor(self) -> None:
        """打开实时监控窗口 (类似直接命令行调用看到的输出)。"""
        port = int(self.cfg.get("port", 8080))
        # 记住上次填的监控地址 (本机或远程)
        try:
            addr = str(config.load_prefs().get("monitor_addr") or "") or None
        except Exception:
            addr = None
        MonitorWindow(self, host="127.0.0.1", port=port, addr=addr)

    def _on_install_wd(self) -> None:
        self._save_config()
        self._run_bg(service.install_watchdog, None, "正在安装 watchdog...")

    def _on_uninstall_wd(self) -> None:
        if not messagebox.askyesno("确认", "确定要卸载 watchdog 服务吗？"):
            return
        self._run_bg(service.uninstall_watchdog, None, "正在卸载 watchdog...")

    def _on_refresh_wd(self) -> None:
        self._refresh_wd_status()

    def _on_help(self) -> None:
        HelpWindow(self)

    def _build_models_tab(self) -> None:
        f = ttk.Frame(self.nb, padding=8)
        self.nb.add(f, text="模型")
        # 本页 = 主池工作区: 只管 runtime != kvmem 的条目
        self.ws_kind = "pool"
        self.model_idx = []
        # 顶部提示条 (与全局界面风格统一, 无橙色)
        top_bar = ttk.Frame(f)
        top_bar.pack(fill=tk.X, side=tk.TOP, pady=(0, 8))
        ttk.Label(top_bar,
                  text="  👉 在左侧点「新增」添加你的 .gguf 模型, 或点击「删除」移除已有模型"
                       "（KVMem 档位在「⚡ KVMem」页，与这里同一套界面）",
                  foreground="#555").pack(side=tk.LEFT, fill=tk.Y, padx=4, pady=4)

        # 左右分栏: 左 = 模型列表 + 基础信息 (上下排列), 右 = 模型参数 (占满)
        paned = ttk.PanedWindow(f, orient=tk.HORIZONTAL)
        paned.pack(fill=tk.BOTH, expand=True)

        # ===== 左侧 (模型列表 + 基础信息) =====
        left = ttk.Frame(paned, padding=(0, 0, 6, 0))
        paned.add(left, weight=1)
        # 模型列表
        ttk.Label(left, text="已配置模型:").pack(anchor=tk.W)
        list_frame = ttk.Frame(left)
        list_frame.pack(fill=tk.X, pady=(2, 4))
        self.model_list = tk.Listbox(list_frame, height=6, exportselection=False,
                                     font=("Consolas", 10))
        self.model_list.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.model_list.bind("<<ListboxSelect>>", self._on_select_model)
        # 选中项只在「按下」那一刻定死: 拖动不让高亮跟着鼠标扫, 松手也不改
        self.model_list.bind("<ButtonPress-1>",
                             lambda e: self._on_list_press(e, W=self), add="+")
        self.model_list.bind("<B1-Motion>",
                             lambda e: self._on_list_motion(e, W=self), add="+")
        self.model_list.bind("<Motion>",
                             lambda e: self._on_list_hover(e, W=self), add="+")
        self.model_list.bind("<ButtonRelease-1>",
                             lambda e: self._on_list_release(e, W=self),
                             add="+")
        lb_sb = ttk.Scrollbar(list_frame, orient=tk.VERTICAL, command=self.model_list.yview)
        lb_sb.pack(side=tk.RIGHT, fill=tk.Y)
        self.model_list.config(yscrollcommand=lb_sb.set)
        # 增删上下移按钮 (一行)
        lb_btns = ttk.Frame(left)
        lb_btns.pack(fill=tk.X, pady=(0, 6))
        for label, cmd in [
            ("新增", self._on_add_model),
            ("删除", self._on_del_model),
            ("上移", lambda: self._move(-1)),
            ("下移", lambda: self._move(1)),
            ("导入bat", self._on_import_bats),
        ]:
            ttk.Button(lb_btns, text=label, command=cmd).pack(
                side=tk.LEFT, expand=True, fill=tk.X, padx=1)
        # 分隔线
        ttk.Separator(left, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=(0, 6))
        # 编辑器 (基础信息 + 推测解码) 在左侧下面
        self._build_model_editor(left)
        # 「视觉模型 / KV 内存加载」折叠区块钉在左下角
        self._build_mmkv_panel(left)

        # ===== 右侧 (模型参数 - 占满全部高度) =====
        right = ttk.Frame(paned)
        paned.add(right, weight=3)
        self.model_right = right
        self._build_model_params_panel(right)

    def _build_model_editor(self, master: ttk.Frame, W=None) -> None:
        W = W if W is not None else self   # 工作区: 模型页=App 自身, KVMem 页=实例
        W._editor_master = master  # 保存引用供 _toggle_draft_box 用
        # 顶部保存条 — 与全局页面位置一致（右上角，标准 ttk 风格，无橙色）
        top_bar = ttk.Frame(master)
        top_bar.pack(fill=tk.X, padx=4, pady=(0, 4), side=tk.TOP)
        ttk.Label(top_bar, text="⚠  修改后点右上角「保存」",
                  foreground="#666").pack(side=tk.LEFT, padx=10, pady=4)
        ttk.Button(top_bar, text="💾  保存",
                   command=lambda: self._on_save_model(W)).pack(side=tk.RIGHT, padx=10, pady=2)

        # ===== 基础信息 (2 列紧凑布局) =====
        box1 = ttk.LabelFrame(master, text="基础信息", padding=6)
        box1.pack(fill=tk.X, padx=4, pady=(0, 4))
        W.var_id = tk.StringVar()
        W.var_alias = tk.StringVar()
        W.var_model = tk.StringVar()
        W.var_mmproj = tk.StringVar()
        W.var_enabled = tk.BooleanVar(value=True)
        W.var_base_url = tk.StringVar()   # API 端点 (留空表示用本机 llama.cpp)
        W.var_api_key = tk.StringVar()    # API 密钥 (留空表示无)
        W.var_api_key_visible = tk.BooleanVar(value=False)  # 控制密码显示/隐藏
        W._api_key_entry = None  # 保存 Entry 引用用于切换显示/隐藏
        # 2 列布局: row 0 是 ID/alias, row 1 是 model/mmproj, row 2 是 base_url/api_key
        # (row, col, label, var, browse_ft, kind)
        info_rows = [
            (0, 0, "ID*",          W.var_id,      None,  "text"),
            (0, 2, "别名 alias",   W.var_alias,   None,  "text"),
            (1, 0, "主模型 .gguf*", W.var_model,   "gguf", "text"),
            (1, 2, "mmproj",       W.var_mmproj,  "gguf", "text"),
            (2, 0, "base_url",     W.var_base_url, None,  "text"),
            (2, 2, "api_key",      W.var_api_key,  None,  "password"),
        ]
        for row, col, label, var, browse_ft, kind in info_rows:
            sub = ttk.Frame(box1)
            sub.grid(row=row, column=col, sticky=tk.EW, padx=(0, 8), pady=2)
            ttk.Label(sub, text=label + ":").pack(side=tk.LEFT)
            show = "*" if kind == "password" else ""
            entry = ttk.Entry(sub, textvariable=var, show=show)
            entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(4, 0))
            if kind == "password":
                # 保存 Entry 引用用于切换显示/隐藏
                W._api_key_entry = entry
                ttk.Button(sub, text="👁", width=3,
                           command=lambda: self._toggle_api_key(W)).pack(side=tk.LEFT, padx=(2, 0))
            if browse_ft:
                ttk.Button(sub, text="…",
                           command=lambda v=var: self._browse(v, browse_ft)).pack(side=tk.LEFT, padx=(2, 0))
        # row 3: 启用 + 保存提示
        ttk.Checkbutton(box1, text="启用 (未启用则不加入 preset.ini)",
                        variable=W.var_enabled).grid(row=3, column=0, columnspan=4, sticky=tk.W, pady=(2, 0))
        # 2 列等宽扩展
        box1.columnconfigure(0, weight=1)
        box1.columnconfigure(1, weight=0)
        box1.columnconfigure(2, weight=1)
        box1.columnconfigure(3, weight=0)

        # ===== 推测解码 (独立启用开关, 默认折叠, 用按钮展开) =====
        # 占 0 空间, 给参数面板更多位置
        draft_toggle_bar = ttk.Frame(master)
        draft_toggle_bar.pack(fill=tk.X, padx=4, pady=(0, 2))
        W.var_draft_enabled = tk.BooleanVar(value=False)
        ttk.Checkbutton(draft_toggle_bar,
                        text="启用推测解码 (MTP / DFlash / ngram)",
                        variable=W.var_draft_enabled,
                        command=lambda: self._on_draft_enable_toggle(W)
                        ).pack(side=tk.LEFT, padx=(4, 12))
        W._draft_collapsed = tk.BooleanVar(value=False)
        W._draft_collapse_cb = ttk.Checkbutton(draft_toggle_bar,
                        text="▾  详细设置 (点此折叠)",
                        variable=W._draft_collapsed,
                        command=lambda: self._toggle_draft_box(W)
                        )
        W._draft_collapse_cb.pack(side=tk.LEFT)
        # 启用开关默认关 -> 详细面板先禁用折叠按钮, 不显示
        W._draft_collapse_cb.configure(state="disabled")
        W.draft_box = ttk.LabelFrame(master, text="推测解码 (Draft Model)", padding=6)
        W.var_draft_model = tk.StringVar()
        W.var_draft_type = tk.StringVar()
        W.var_draft_n_max = tk.StringVar(value="3")
        W.var_draft_n_min = tk.StringVar(value="0")
        W.var_draft_p_split = tk.StringVar(value="0.1")
        W.var_draft_p_min = tk.StringVar(value="0.0")
        W.var_draft_ngl = tk.StringVar(value="auto")
        # 每个快捷字段前面一个勾选框: 勾了才写入 preset.ini (draft_en 存进模型配置)
        W.var_draft_en: Dict[str, Any] = {
            k: tk.BooleanVar(value=True) for k in config.DRAFT_SHORTCUT_KEYS
        }
        W._draft_en_widgets: Dict[str, List[Any]] = {}
        W._draft_en_cbs: Dict[str, Any] = {}  # 勾选框本身 (永远保持可点)

        def _cb(row_key: str, text: str, row: int, col: int = 0,
                col_span: int = 1) -> None:
            """快捷字段的勾选框 (框内文字即字段名) + 联动禁用配对控件。

            注意: 勾选框本身**不能**进 _draft_en_widgets, 否则取消勾选时
            会连自己一起置灰, 用户再也点不回来 (实测 bug)。
            """
            cb = ttk.Checkbutton(W.draft_box, text=text,
                                 variable=W.var_draft_en[row_key],
                                 command=lambda: self._apply_draft_en(W))
            cb.grid(row=row, column=col, columnspan=col_span,
                    sticky=tk.W, pady=3, padx=(0, 4))
            W._draft_en_cbs[row_key] = cb

        def _pair(row_key: str, widget) -> None:
            W._draft_en_widgets.setdefault(row_key, []).append(widget)

        # 第 0 行: 草稿模型路径 (可选项 — 模型自带草稿时留空)
        _cb("spec-draft-model", "草稿模型 .gguf (可选):", 0)
        _e = ttk.Entry(W.draft_box, textvariable=W.var_draft_model)
        _e.grid(row=0, column=1, columnspan=2, sticky=tk.EW, pady=3)
        _pair("spec-draft-model", _e)
        _b = ttk.Button(W.draft_box, text="浏览...",
                        command=lambda: self._browse(W.var_draft_model, "gguf"))
        _b.grid(row=0, column=3, padx=4)
        _pair("spec-draft-model", _b)
        # 第 1 行: 推测类型 + 提示
        _cb("spec-type", "推测类型:", 1)
        _c = ttk.Combobox(W.draft_box, textvariable=W.var_draft_type, width=14,
                          values=self._choices["spec-type"])
        _c.grid(row=1, column=1, sticky=tk.W, pady=3)
        _pair("spec-type", _c)
        ttk.Label(W.draft_box, text="(留空按文件名自动猜; 无草稿文件时请选类型)",
                  foreground="#888").grid(row=1, column=2, columnspan=2,
                                          padx=4, sticky=tk.W)
        # 第 2 行: n-max / n-min
        _cb("spec-draft-n-max", "n-max:", 2, col=0)
        _s = ttk.Spinbox(W.draft_box, textvariable=W.var_draft_n_max, width=8,
                         from_=1, to=16)
        _s.grid(row=2, column=1, sticky=tk.W, pady=3)
        _pair("spec-draft-n-max", _s)
        _cb("spec-draft-n-min", "n-min:", 2, col=2)
        _s = ttk.Spinbox(W.draft_box, textvariable=W.var_draft_n_min, width=8,
                         from_=0, to=16)
        _s.grid(row=2, column=3, sticky=tk.W, pady=3)
        _pair("spec-draft-n-min", _s)
        # 第 3 行: p-split / p-min
        _cb("spec-draft-p-split", "p-split:", 3, col=0)
        _s = ttk.Spinbox(W.draft_box, textvariable=W.var_draft_p_split, width=8,
                         from_=0.0, to=1.0, increment=0.05)
        _s.grid(row=3, column=1, sticky=tk.W, pady=3)
        _pair("spec-draft-p-split", _s)
        _cb("spec-draft-p-min", "p-min:", 3, col=2)
        _s = ttk.Spinbox(W.draft_box, textvariable=W.var_draft_p_min, width=8,
                         from_=0.0, to=1.0, increment=0.05)
        _s.grid(row=3, column=3, sticky=tk.W, pady=3)
        _pair("spec-draft-p-min", _s)
        # 第 4 行: ngl
        _cb("spec-draft-ngl", "草稿 GPU 层数:", 4)
        _c = ttk.Combobox(W.draft_box, textvariable=W.var_draft_ngl, width=14,
                          values=self._choices["spec-draft-ngl"])
        _c.grid(row=4, column=1, sticky=tk.W, pady=3)
        _pair("spec-draft-ngl", _c)
        ttk.Label(W.draft_box, text="(auto 自动, 0 纯 CPU, 99 全 GPU)",
                  foreground="#888").grid(row=4, column=2, columnspan=2,
                                          padx=4, sticky=tk.W)

        # ===== 高级可选项: 每项独立勾选, 勾了才写入 ini =====
        ttk.Separator(W.draft_box, orient=tk.HORIZONTAL).grid(
            row=5, column=0, columnspan=4, sticky=tk.EW, pady=(6, 3))
        ttk.Label(W.draft_box, text="高级 (可选, 勾选后才写入 preset.ini)",
                  foreground="#555").grid(row=6, column=0, columnspan=4,
                                          sticky=tk.W, pady=(0, 2))
        W.var_draft_opt: Dict[str, Any] = {}
        for i, key in enumerate(config.DRAFT_OPT_KEYS):
            meta = config.SCHEMA_INDEX.get(key, {})
            en = tk.BooleanVar(value=False)
            va = tk.StringVar(value=str(meta.get("default", "")))
            W.var_draft_opt[key] = (en, va)
            r = 7 + i
            ttk.Checkbutton(W.draft_box, variable=en).grid(
                row=r, column=0, sticky=tk.W, pady=2, padx=(0, 2))
            ttk.Label(W.draft_box, text=meta.get("label", key)).grid(
                row=r, column=1, sticky=tk.W, pady=2, padx=(0, 6))
            kind = meta.get("kind", "text")
            if kind == "choice":
                ttk.Combobox(W.draft_box, textvariable=va, width=14,
                             values=self._choices.get(key, [])).grid(
                    row=r, column=2, sticky=tk.W, pady=2)
            elif kind == "bool":
                ttk.Combobox(W.draft_box, textvariable=va, width=8,
                             values=["true", "false"]).grid(
                    row=r, column=2, sticky=tk.W, pady=2)
            else:
                ttk.Entry(W.draft_box, textvariable=va, width=16).grid(
                    row=r, column=2, sticky=tk.W, pady=2)
            if meta.get("hint"):
                ttk.Label(W.draft_box, text=meta["hint"],
                          foreground="#888").grid(row=r, column=3,
                                                  sticky=tk.W, padx=(8, 0))
        W.draft_box.columnconfigure(1, weight=1)
        # 初始不显示: 由 _on_select_model 按 draft_enabled 同步 (启用开关默认关)

    def _apply_draft_en(self, W=None) -> None:
        W = W if W is not None else self
        """快捷字段勾选框 -> 联动禁用/恢复配对控件 (视觉上也表示这项不生效)。

        勾选框本身永远保持可点: 一旦它也被置灰, 用户取消勾选后就再也改不回来。
        """
        for key, widgets in W._draft_en_widgets.items():
            on = bool(W.var_draft_en[key].get())
            cb = W._draft_en_cbs.get(key)
            for w in widgets:
                if w is cb:
                    continue
                try:
                    w.configure(state="normal" if on else "disabled")
                except tk.TclError:
                    pass

    def _build_mmkv_panel(self, master: ttk.Frame, W=None) -> None:
        W = W if W is not None else self
        """「视觉模型 / KV 内存加载」折叠区块 (贴在「推测解码」下方)。

        和「推测解码」一样: 开关栏常驻, 面板默认折叠, 点开关展开。
        左栏空间有限, 所以面板做成**固定高度 + 纵向滚动条 + 鼠标滚轮**,
        再窄的窗口也能滚着看完, 不会被裁掉。
        每行各带一个「写入」勾选框: 勾了才写 preset.ini, 不勾就沿用
        llama.cpp 默认值。这些 key 已从模型参数子表排除, 避免渲染两遍。
        """
        wrap = ttk.Frame(master)
        # expand=True: 窗口够高时, 剩余空间全给这个面板 (看得更多);
        # 窗口不够高时 pack 会压缩到可用高度, 靠滚动条/滚轮看剩下的
        wrap.pack(fill=tk.X, expand=True, padx=4, pady=(6, 0))
        W._mmkv_wrap = wrap

        W._mmkv_expanded = tk.BooleanVar(value=False)
        W._mmkv_collapse_cb = ttk.Checkbutton(
            wrap, text="▸  视觉模型 / KV 内存加载 (点此展开)",
            variable=W._mmkv_expanded, command=lambda: self._toggle_mmkv_box(W))
        W._mmkv_collapse_cb.pack(anchor=tk.W, padx=4, pady=(0, 2))

        W.mmkv_box = ttk.LabelFrame(wrap, text="视觉模型 / KV 内存加载", padding=6)
        ttk.Label(W.mmkv_box,
                  text="  👉 方框勾选 = 写入 preset.ini ；不勾 = 不写，沿用 llama.cpp 默认值",
                  foreground="#666").pack(anchor=tk.W, padx=2, pady=(0, 4))

        # ---- 滚动区: Canvas + 纵向滚动条, 内容放 inner Frame 里 ----
        area = ttk.Frame(W.mmkv_box)
        area.pack(fill=tk.BOTH, expand=True)
        W._mmkv_scroll = ttk.Scrollbar(area, orient=tk.VERTICAL)
        W._mmkv_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        W._mmkv_canvas = tk.Canvas(area, borderwidth=0, highlightthickness=0,
                                      height=280, yscrollcommand=W._mmkv_scroll.set)
        W._mmkv_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        W._mmkv_scroll.configure(command=W._mmkv_canvas.yview)
        W._mmkv_inner = ttk.Frame(W._mmkv_canvas)
        W._mmkv_inner_id = W._mmkv_canvas.create_window(
            (0, 0), window=W._mmkv_inner, anchor=tk.NW)
        _oi3, _oc3, _f3 = _canvas_sync(W._mmkv_canvas, W._mmkv_inner_id, W._mmkv_inner)
        W._mmkv_inner.bind("<Configure>", _oi3)
        W._mmkv_canvas.bind("<Configure>", _oc3)

        W._mmkv_rows: List[ParamRow] = []
        groups = (("视觉模型 (mmproj)", config.MMKV_VISION_KEYS),
                  ("KV 缓存", config.MMKV_KV_KEYS),
                  ("模型加载", config.MMKV_LOAD_KEYS))
        for title, keys in groups:
            sub = ttk.LabelFrame(W._mmkv_inner, text=title, padding=4)
            sub.pack(fill=tk.X, pady=(0, 4))
            for key in keys:
                p = config.lookup(key)
                if not isinstance(p, dict):
                    continue  # schema 改动后 key 没了, 跳过不炸
                row = ParamRow(sub, p, config.list_kinds_choices().get(key, []),
                               get=lambda k: self._get_model_param(k, W),
                               set_=lambda k, v: self._set_model_param(k, v, W))
                row.load()
                row.frame.pack(fill=tk.X, pady=1)
                W._mmkv_rows.append(row)

        # 滚轮: 递归绑到面板下所有控件 (绑父件收不到子件的滚轮事件)
        self._mmkv_bind_wheel(W.mmkv_box, W)
        # 初始折叠: 面板先不 pack (开关栏常驻)
        self._sync_mmkv_box(W)

    def _mmkv_bind_wheel(self, widget, W=None) -> None:
        W = W if W is not None else self
        """把鼠标滚轮绑到 widget 及其所有后代 -> 滚动 mmkv 面板。"""
        def _on_wheel(e):
            delta = -1 * int(e.delta / 120) if e.delta else 0
            if delta:
                W._mmkv_canvas.yview_scroll(delta, "units")
            elif getattr(e, "num", None) in (4, 5):
                W._mmkv_canvas.yview_scroll(-1 if e.num == 4 else 1, "units")
        widget.bind("<MouseWheel>", _on_wheel, add="+")
        for child in widget.winfo_children():
            self._mmkv_bind_wheel(child)

    def _toggle_mmkv_box(self, W=None) -> None:
        W = W if W is not None else self
        """展开/折叠「视觉模型 / KV 内存加载」面板, 同步开关栏文案。"""
        W._mmkv_collapse_cb.configure(
            text=("▾  视觉模型 / KV 内存加载 (点此折叠)"
                  if W._mmkv_expanded.get()
                  else "▸  视觉模型 / KV 内存加载 (点此展开)"))
        self._sync_mmkv_box(W)

    def _sync_mmkv_box(self, W=None) -> None:
        W = W if W is not None else self
        """按展开状态显示/隐藏面板 (开关栏永远可见)。"""
        if W._mmkv_expanded.get():
            W.mmkv_box.pack(fill=tk.X, expand=True, pady=(0, 4))
            # 展开后滚动区尺寸变了, 重新算一次滚动范围
            W._mmkv_canvas.configure(scrollregion=W._mmkv_canvas.bbox("all"))
        else:
            W.mmkv_box.pack_forget()

    def _mmkv_load(self, W=None) -> None:
        W = W if W is not None else self
        """切换/新增模型后, 把区块里各行刷成当前模型的参数值。"""
        for row in getattr(W, "_mmkv_rows", []):
            row.load()

    def _build_model_params_panel(self, master: ttk.Frame, W=None,
                                  scope: str = "model",
                                  title: str = "模型参数 (常用 / 扩展切换)") -> None:
        """右侧面板: 参数列表 (占满全部高度)。模型页与 KVMem 页用同一套结构。"""
        W = W if W is not None else self
        box3 = ttk.LabelFrame(master, text=title, padding=6)
        box3.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)
        panel = ParamPanel(
            box3, scope=scope,
            get=lambda k: self._get_model_param(k, W),
            set_=lambda k, v: self._set_model_param(k, v, W),
            refresh_outer=lambda: None,
        )
        W.model_param_panel = panel
        self._param_panels.append(panel)
        panel.pack(fill=tk.BOTH, expand=True)

    # ------------------------------------------------------------------
    # ⚡ KVMem 分支标签
    # ------------------------------------------------------------------
    def _build_kvmem_tab(self) -> None:
        f = ttk.Frame(self.nb, padding=8)
        self.nb.add(f, text="⚡ KVMem")
        # KVMem 工作区: 与「模型」页同一套控件结构, 只有过滤条件与参数 scope 不同
        self.kv = ModelWorkspace("kvmem")
        W = self.kv

        # 顶部提示条 (与「模型」页同款)
        top_bar = ttk.Frame(f)
        top_bar.pack(fill=tk.X, side=tk.TOP, pady=(0, 8))
        ttk.Label(
            top_bar,
            text="  👉 界面与「模型」页完全一致，只是由 llama-kvmem-server 运行；"
                 "程序路径在「全局」页的「KVMem-llama.cpp 程序」里改",
            foreground="#555").pack(side=tk.LEFT, fill=tk.Y, padx=4, pady=4)

        # ---- 运行服务 (钉在底部, 把中间整块让给 列表 + 编辑器 + 参数) ----
        box_svc = ttk.LabelFrame(
            f, text="运行服务 (llama-router，与主池共用同一个服务和同一个端口)",
            padding=6)
        box_svc.pack(side=tk.BOTTOM, fill=tk.X, pady=(6, 0))
        btns = [
            ("应用并重装", self._on_kvmem_install),
            ("切回主池", self._on_kvmem_to_pool),
            ("✔ 设为当前档位", self._on_kvmem_set_active),
            ("启动", self._on_kvmem_start),
            ("停止", self._on_kvmem_stop),
            ("重启", self._on_kvmem_restart),
            ("前台运行", self._on_kvmem_fg),
            ("刷新状态", self._on_kvmem_refresh_status),
            ("📂 打开日志", self._on_kvmem_open_log),
        ]
        for i, (label, cmd) in enumerate(btns):
            ttk.Button(box_svc, text=label, width=11, command=cmd).grid(
                row=0, column=i, padx=2, pady=2)
        self.kvmem_state_var = tk.StringVar(value="运行服务: 未知")
        ttk.Label(box_svc, textvariable=self.kvmem_state_var).grid(
            row=1, column=0, columnspan=5, sticky=tk.W, pady=(3, 0))
        ttk.Label(box_svc, text="CUDA 设备:").grid(
            row=1, column=5, sticky=tk.E, pady=(3, 0))
        self.kvmem_cuda_var = tk.StringVar(value="0")
        ttk.Entry(box_svc, textvariable=self.kvmem_cuda_var, width=6).grid(
            row=1, column=6, sticky=tk.W, pady=(3, 0), padx=(3, 0))
        ttk.Button(box_svc, text="保存 GPU 设置", width=13,
                   command=self._on_kvmem_save_env).grid(
            row=1, column=7, sticky=tk.W, pady=(3, 0), padx=(6, 0))
        self.kvmem_url_var = tk.StringVar(value="调用地址: -")
        ttk.Label(box_svc, textvariable=self.kvmem_url_var,
                  foreground="#0666CC").grid(
            row=2, column=0, columnspan=6, sticky=tk.W, pady=(3, 0))
        ttk.Button(box_svc, text="📋 复制调用地址",
                   command=self._on_kvmem_copy_url).grid(
            row=2, column=6, columnspan=2, sticky=tk.W, pady=(3, 0))
        self.kvmem_exe_var = tk.StringVar(value="分支程序: -")
        ttk.Label(box_svc, textvariable=self.kvmem_exe_var,
                  foreground="#888").grid(
            row=3, column=0, columnspan=6, sticky=tk.W, pady=(3, 0))
        self.kvmem_hint_var = tk.StringVar(value="")
        ttk.Label(box_svc, textvariable=self.kvmem_hint_var,
                  foreground="#888").grid(
            row=3, column=6, columnspan=3, sticky=tk.W, pady=(3, 0))
        box_svc.columnconfigure(0, weight=1)

        # ===== 左右分栏 (与「模型」页同款) =====
        paned = ttk.PanedWindow(f, orient=tk.HORIZONTAL)
        paned.pack(fill=tk.BOTH, expand=True)

        left = ttk.Frame(paned, padding=(0, 0, 6, 0))
        paned.add(left, weight=1)
        ttk.Label(left, text="KVMem 档位:").pack(anchor=tk.W)
        list_frame = ttk.Frame(left)
        list_frame.pack(fill=tk.X, pady=(2, 4))
        W.model_list = tk.Listbox(list_frame, height=6, exportselection=False,
                                  font=("Consolas", 10))
        W.model_list.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        W.model_list.bind("<<ListboxSelect>>",
                          lambda e: self._on_select_model(e, W=W))
        # 与「模型」页同一套: 按下定死选中项, 拖动不扫, 松手不改
        W.model_list.bind("<ButtonPress-1>",
                          lambda e: self._on_list_press(e, W=W), add="+")
        W.model_list.bind("<B1-Motion>",
                          lambda e: self._on_list_motion(e, W=W), add="+")
        W.model_list.bind("<Motion>",
                          lambda e: self._on_list_hover(e, W=W), add="+")
        W.model_list.bind("<ButtonRelease-1>",
                          lambda e: self._on_list_release(e, W=W), add="+")
        lb_sb = ttk.Scrollbar(list_frame, orient=tk.VERTICAL,
                              command=W.model_list.yview)
        lb_sb.pack(side=tk.RIGHT, fill=tk.Y)
        W.model_list.config(yscrollcommand=lb_sb.set)

        lb_btns = ttk.Frame(left)
        lb_btns.pack(fill=tk.X, pady=(0, 6))
        for label, cmd in [
            ("新增", lambda: self._on_add_model(W)),
            ("删除", lambda: self._on_del_model(W)),
            ("上移", lambda: self._move(-1, W)),
            ("下移", lambda: self._move(1, W)),
            ("导入bat", self._on_import_bats),
        ]:
            ttk.Button(lb_btns, text=label, command=cmd).pack(
                side=tk.LEFT, expand=True, fill=tk.X, padx=1)
        ttk.Separator(left, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=(0, 6))
        self._build_model_editor(left, W)
        self._build_mmkv_panel(left, W)

        right = ttk.Frame(paned)
        paned.add(right, weight=3)
        self._build_model_params_panel(
            right, W=W, scope="kvmem_full",
            title="KVMem 档位参数 (常用 / 扩展切换)")

        self._refresh_model_list(W=W)
        self._refresh_kvmem_labels()

    # ---- kvmem 标签页的状态与清单 ----
    def _kvmem_model(self) -> Optional[Dict[str, Any]]:
        return kvmem.active_model(self.cfg)

    def _refresh_kvmem_tab(self) -> None:
        """重建 KVMem 页的档位清单 + 状态 (切到本页时会自动调用)。"""
        if not hasattr(self, "kv"):
            return
        self._refresh_model_list(W=self.kv)
        self._refresh_kvmem_labels()

    def _refresh_kvmem_labels(self) -> None:
        if not hasattr(self, "kvmem_url_var"):
            return
        active = kvmem.active_model(self.cfg)
        if active:
            self.kvmem_url_var.set(
                f"调用地址: {kvmem.base_url(self.cfg, active)}  "
                f"(model = {active.get('alias') or active.get('id')})")
        else:
            self.kvmem_url_var.set("调用地址: -")
        exe = kvmem.find_exe(self.cfg)
        self.kvmem_exe_var.set(
            f"分支程序: {exe}" if exe else
            "分支程序: 未找到 (「全局」页的「KVMem-llama.cpp 程序」留空则自动探测)")
        self.kvmem_cuda_var.set(
            str((self.cfg.get("kvmem") or {}).get("cuda_device") or "0"))
        self._on_kvmem_refresh_status()

    def _on_kvmem_set_active(self) -> None:
        """把**当前选中的档位**设为运行时档位 (kvmem 单槽, 一次只跑一个)。"""
        m = self._ws_model(getattr(self, "kv", None))
        if not m:
            messagebox.showinfo("提示", "请先在左侧选择一个 KVMem 档位")
            return
        pid = str(m.get("id", ""))
        kv = self.cfg.setdefault("kvmem", {})
        if kv.get("active_id") == pid:
            self._log(f"KVMem 当前档位已经是 {pid}")
            return
        kv["active_id"] = pid
        config.save(self.cfg)
        self._log(f"KVMem 当前档位 -> {pid}")
        if kvmem.is_installed():
            self.kvmem_hint_var.set(
                "运行时已是 KVMem，切换档位后请点「应用并重装」重新写入参数。")
        self._refresh_kvmem_labels()

    def _on_kvmem_copy_url(self) -> None:
        active = kvmem.active_model(self.cfg)
        if not active:
            return
        url = kvmem.base_url(self.cfg, active)
        try:
            self.clipboard_clear()
            self.clipboard_append(url)
            self._log(f"已复制调用地址: {url}")
        except tk.TclError:
            self._log(f"调用地址: {url}")

    def _on_kvmem_save(self) -> None:
        """落盘 (两个工作区手上的未保存改动一起 flush)。"""
        self._save_config()
        self._refresh_kvmem_labels()
        if kvmem.is_installed():
            self.kvmem_hint_var.set("改了参数，需重新点「应用并重装」才生效。")

    def _on_kvmem_save_env(self) -> None:
        kv = self.cfg.setdefault("kvmem", {})
        kv["cuda_device"] = self.kvmem_cuda_var.get().strip()
        config.save(self.cfg)
        self._log(f"KVMem CUDA_VISIBLE_DEVICES = {kv['cuda_device']!r}")
        if kvmem.is_installed():
            self.kvmem_hint_var.set("改了 GPU 设置，需重新点「应用并重装」才生效。")

    def _kvmem_done(self, ok: bool, msg: str) -> None:
        self._log(("✔ " if ok else "✘ ") + msg)
        if not ok:
            messagebox.showerror("KVMem", msg)
        self._refresh_kvmem_tab()

    def _on_kvmem_install(self) -> None:
        """切到 KVMem 运行时: 重装 llama-router 服务 (App 指向分支 exe)。"""
        self._on_kvmem_save()
        self.cfg["serve_mode"] = "kvmem"
        config.save(self.cfg)
        self._kvmem_done(*service.install(self.cfg))

    def _on_kvmem_to_pool(self) -> None:
        """切回主池运行时: 重装 llama-router 服务 (App 指向 llama-server)。"""
        self._on_kvmem_save()
        self.cfg["serve_mode"] = "router"
        config.save(self.cfg)
        self._kvmem_done(*service.install(self.cfg))

    def _on_kvmem_start(self) -> None:
        self._kvmem_done(*kvmem.start())

    def _on_kvmem_stop(self) -> None:
        self._kvmem_done(*kvmem.stop())

    def _on_kvmem_restart(self) -> None:
        self._kvmem_done(*kvmem.restart())

    def _on_kvmem_fg(self) -> None:
        self._kvmem_done(*kvmem.run_foreground(self.cfg))

    def _on_kvmem_refresh_status(self) -> None:
        if not hasattr(self, "kvmem_state_var"):
            return
        st = kvmem.status()
        text = f"运行服务: {st}"
        active = kvmem.active_model(self.cfg)
        if active:
            port = kvmem.port_of(self.cfg, active)
            text += f"   端口 {port}"
            if kvmem.port_busy(port):
                text += "  (端口已被占用)"
        self.kvmem_state_var.set(text)

    def _on_kvmem_open_log(self) -> None:
        for path in (kvmem.err_log(), kvmem.out_log()):
            if os.path.isfile(path):
                self._open_file(path)
                return
        messagebox.showinfo("提示", "还没有 KVMem 日志，先应用并重装一次服务。")

    # ==============================================================
    # 「🌐 Strata」页
    #
    # 和「⚡ KVMem」页的差别: Strata 的参数**不在** config.json 里, 而是
    # 全在 install_dir 下的 strata-<档位>.json (那是唯一真相)。所以本页:
    #   * 左边 = 已登记的档位 (点一个, 右边加载它的参数)
    #   * 右上 = 扫描到的 json, 登记成档位
    #   * 右下 = 参数编辑器 —— 直接读写那个 json, 不是影子副本: 没管到的
    #     键 (vision / lib_dirs / layer_split ...) 原样保留, 排列顺序也
    #     不打乱, 保存前留 .bak
    # ==============================================================
    def _build_strata_tab(self) -> None:
        f = ttk.Frame(self.nb, padding=8)
        self.nb.add(f, text="🌐 Strata")
        # Strata 工作区: 与「模型」/「⚡KVMem」两页同一套骨架
        # (顶部说明 / 左=列表+基础信息 / 右=参数面板 / 底部=运行服务),
        # 只有「基础信息」和「参数面板」里的字段是 Strata 自己的。
        self.st = ModelWorkspace("strata")
        W = self.st

        # 顶部提示条 (与另两页同位置)
        top_bar = ttk.Frame(f)
        top_bar.pack(fill=tk.X, side=tk.TOP, pady=(0, 8))
        ttk.Label(
            top_bar,
            text="  👉 界面与「模型」「⚡KVMem」两页一致，只是由 Strata 运行；"
                 "参数直接改 strata-<档位>.json（唯一真相），"
                 "对外调用地址与另外两组共用同一个网关端口",
            foreground="#555").pack(side=tk.LEFT, fill=tk.Y, padx=4, pady=4)

        # ---- 运行服务 (钉在底部, 与 KVMem 页同位置) ----
        self._build_strata_service_box(f)

        paned = ttk.PanedWindow(f, orient=tk.HORIZONTAL)
        paned.pack(fill=tk.BOTH, expand=True)

        # ---- 左: 档位列表 (与另两页同位置) ----
        left = ttk.Frame(paned, padding=(0, 0, 6, 0))
        paned.add(left, weight=1)
        ttk.Label(left, text="已登记的 Strata 档位:").pack(anchor=tk.W)
        lf = ttk.Frame(left)
        lf.pack(fill=tk.X, pady=(2, 4))
        W.model_list = tk.Listbox(lf, height=6, exportselection=False,
                                  font=("Consolas", 10))
        W.model_list.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        W.model_list.bind("<<ListboxSelect>>", self._on_strata_select)
        sb = ttk.Scrollbar(lf, orient=tk.VERTICAL, command=W.model_list.yview)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        W.model_list.config(yscrollcommand=sb.set)
        # 按钮行与另两页同款: 新增 / 删除 / 上移 / 下移 / 导入
        lb_btns = ttk.Frame(left)
        lb_btns.pack(fill=tk.X, pady=(0, 6))
        for label, cmd in [
            ("新增", self._on_strata_add),
            ("删除", self._on_strata_remove),
            ("上移", lambda: self._strata_move(-1)),
            ("下移", lambda: self._strata_move(1)),
            ("导入json", self._on_strata_scan),
        ]:
            ttk.Button(lb_btns, text=label, command=cmd).pack(
                side=tk.LEFT, expand=True, fill=tk.X, padx=1)
        ttk.Separator(left, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=(0, 6))
        # 基础信息: 与另两页同一位置, 只是字段换成 Strata 的
        self._build_strata_editor(left, W)

        # ---- 右: 参数面板 (与另两页同一位置) ----
        right = ttk.Frame(paned, padding=(6, 0, 0, 0))
        paned.add(right, weight=2)
        box_par = ttk.LabelFrame(
            right, text="档位参数 (直接改 strata-<档位>.json，保存后重启服务生效)",
            padding=6)
        box_par.pack(fill=tk.BOTH, expand=True)
        self.strata_par_hint = tk.StringVar(value="")
        ttk.Label(box_par, textvariable=self.strata_par_hint, foreground="#555",
                  wraplength=420).pack(fill=tk.X, pady=(0, 4))
        # 标识路径 (只读): --native/--ple-gguf 在左边「基础信息」里改
        self.strata_path_vars: Dict[str, tk.StringVar] = {}
        pf = ttk.LabelFrame(box_par, text="标识路径 (只读)", padding=4)
        pf.pack(fill=tk.X, pady=(0, 6))
        for flag in strata.PATH_FLAGS:
            if flag in strata.EDITABLE_PATH_FLAGS:
                continue                       # 左边已经能改, 不重复显示
            row = ttk.Frame(pf)
            row.pack(fill=tk.X, pady=1)
            ttk.Label(row, text=flag, width=18, font=("Consolas", 9)).pack(
                side=tk.LEFT)
            var = tk.StringVar(value="")
            self.strata_path_vars[flag] = var
            ttk.Label(row, textvariable=var, foreground="#777",
                      font=("Consolas", 9)).pack(side=tk.LEFT, fill=tk.X,
                                                 expand=True)
        gf = ttk.LabelFrame(box_par, text="常用参数", padding=4)
        gf.pack(fill=tk.BOTH, expand=True)
        self.strata_flag_vars: Dict[str, Any] = {}
        for flag, kind, label, desc in strata.TIER_FLAGS:
            row = ttk.Frame(gf)
            row.pack(fill=tk.X, pady=1)
            ttk.Label(row, text=label, width=13, anchor=tk.W).pack(side=tk.LEFT)
            if kind == "bool":
                var = tk.BooleanVar(value=False)
                cb = ttk.Checkbutton(row, text=flag, variable=var)
                cb.pack(side=tk.LEFT)
                w = cb
            else:
                var = tk.StringVar(value="")
                if kind == "choice":
                    w = ttk.Combobox(row, textvariable=var, width=12,
                                     values=_strata_choices(flag))
                else:
                    w = ttk.Entry(row, textvariable=var, width=14)
                w.pack(side=tk.LEFT)
                ttk.Label(row, text=flag, width=26, font=("Consolas", 9),
                          foreground="#777").pack(side=tk.LEFT, padx=(6, 0))
            self.strata_flag_vars[flag] = var
            ToolTip(w, desc)
        of = ttk.LabelFrame(box_par, text="其它参数 (不在上面列表里的, 原样保留)",
                            padding=4)
        of.pack(fill=tk.X, pady=(6, 0))
        self.strata_others_var = tk.StringVar(value="")
        ttk.Label(of, text="空格分隔 (例如 --foo 1 --bar on); 保存时排在末尾").pack(
            anchor=tk.W)
        ttk.Entry(of, textvariable=self.strata_others_var).pack(fill=tk.X)
        pb = ttk.Frame(box_par)
        pb.pack(fill=tk.X, pady=(6, 0))
        ttk.Button(pb, text="📂 用记事本打开", width=17,
                   command=self._on_strata_open_json).pack(side=tk.LEFT)
        ttk.Button(pb, text="↺ 重读", width=9,
                   command=self._on_strata_reload_params).pack(side=tk.LEFT,
                                                                padx=2)
        self.strata_par_msg = tk.StringVar(value="")
        ttk.Label(pb, textvariable=self.strata_par_msg,
                  foreground="#a05000").pack(side=tk.LEFT, padx=(8, 0))
        self.strata_found_var = tk.StringVar(value="")
        ttk.Label(pb, textvariable=self.strata_found_var,
                  foreground="#777").pack(side=tk.RIGHT)
        ttk.Button(pb, text="📂 换安装目录", width=13,
                   command=self._on_strata_pick_dir).pack(side=tk.RIGHT,
                                                          padx=(0, 6))

        self._refresh_strata_tab()

    def _build_strata_service_box(self, parent: ttk.Frame) -> None:
        """底部运行服务区 (与「⚡KVMem」页同位置、同按钮排布)。"""
        box_svc = ttk.LabelFrame(
            parent,
            text="运行服务 (llama-router，与主池/KVMem 共用同一个服务和同一个对外端口)",
            padding=6)
        box_svc.pack(side=tk.BOTTOM, fill=tk.X, pady=(6, 0))
        btns = [
            ("应用并重装", self._on_strata_install),
            ("切回主池", self._on_strata_to_pool),
            ("✔ 设为当前档位", self._on_strata_set_active),
            ("启动", self._on_strata_start),
            ("停止", self._on_strata_stop),
            ("重启", self._on_strata_restart),
            ("前台运行", self._on_strata_fg),
            ("刷新状态", self._on_strata_refresh_status),
            ("📂 打开日志", self._on_strata_open_log),
        ]
        for i, (label, cmd) in enumerate(btns):
            ttk.Button(box_svc, text=label, width=11, command=cmd).grid(
                row=0, column=i, padx=2, pady=2)
        box_svc.columnconfigure(0, weight=1)
        self.strata_state_var = tk.StringVar(value="运行服务: 未知")
        ttk.Label(box_svc, textvariable=self.strata_state_var).grid(
            row=1, column=0, columnspan=4, sticky=tk.W, pady=(3, 0))
        ttk.Label(box_svc, text="就绪超时(秒):").grid(
            row=1, column=4, sticky=tk.E, pady=(3, 0))
        self.strata_timeout_var = tk.StringVar(value="900")
        ttk.Entry(box_svc, textvariable=self.strata_timeout_var,
                  width=6).grid(row=1, column=5, sticky=tk.W, padx=(3, 0))
        ttk.Button(box_svc, text="保存", width=8,
                   command=self._on_strata_save_timeout).grid(
            row=1, column=6, sticky=tk.W, padx=(6, 0))
        self.strata_url_var = tk.StringVar(value="调用地址: -")
        ttk.Label(box_svc, textvariable=self.strata_url_var,
                  foreground="#0666CC").grid(row=2, column=0, columnspan=5,
                                             sticky=tk.W, pady=(3, 0))
        ttk.Button(box_svc, text="📋 复制调用地址",
                   command=self._on_strata_copy_url).grid(
            row=2, column=5, columnspan=2, sticky=tk.W, pady=(3, 0))
        self.strata_hint_var = tk.StringVar(value="")
        ttk.Label(box_svc, textvariable=self.strata_hint_var,
                  foreground="#a05000").grid(row=3, column=0, columnspan=7,
                                             sticky=tk.W, pady=(3, 0))

    # ---- Strata 页: 基础信息 (与另两页同一位置) ----
    def _build_strata_editor(self, master: ttk.Frame, W) -> None:
        """基础信息区。

        摆的字段和「模型」/「⚡KVMem」页是同一套骨架 (ID / 别名 / 模型路径
        (带 … 浏览) / mmproj (带 … 浏览) / 启用 / 右上角保存), 只是换成了
        Strata 的口径:
          * 「主模型 .gguf」默认从 strata-<档位>.json 的 --native 自动读出来,
            「…」可以手动改指到别的 gguf (写回 json, 所以不会出现两套值);
          * 「mmproj」对应 json 的 vision.mmproj;
          * 多一行「引擎 json」—— Strata 的参数全在那个文件里, 得能换。
        """
        W = W if W is not None else self.st
        # 顶部保存条 — 与另两页同一位置 (右上角)
        top_bar = ttk.Frame(master)
        top_bar.pack(fill=tk.X, padx=4, pady=(0, 4), side=tk.TOP)
        ttk.Label(top_bar, text="⚠  修改后点右上角「保存」",
                  foreground="#666").pack(side=tk.LEFT, padx=10, pady=4)
        ttk.Button(top_bar, text="💾  保存",
                   command=lambda: self._on_strata_save_entry(W)).pack(
                       side=tk.RIGHT, padx=10, pady=2)

        box1 = ttk.LabelFrame(master, text="基础信息", padding=6)
        box1.pack(fill=tk.X, padx=4, pady=(0, 4))
        W.var_id = tk.StringVar()
        W.var_alias = tk.StringVar()
        W.var_model = tk.StringVar()          # -> --native
        W.var_mmproj = tk.StringVar()        # -> vision.mmproj
        W.var_enabled = tk.BooleanVar(value=True)
        W.var_tier_json = tk.StringVar()     # -> strata_config
        W.var_auto_model = tk.BooleanVar(value=True)   # 跟着 json 自动
        rows = [
            (0, 0, "ID*",          W.var_id,       None,   "text"),
            (0, 2, "别名 alias",   W.var_alias,    None,   "text"),
            (1, 0, "主模型 .gguf*", W.var_model,   "gguf", "text"),
            (1, 2, "mmproj",       W.var_mmproj,  "gguf", "text"),
            (2, 0, "引擎 json",    W.var_tier_json, "json", "text"),
        ]
        for row, col, label, var, browse_ft, kind in rows:
            sub = ttk.Frame(box1)
            sub.grid(row=row, column=col, sticky=tk.EW, padx=(0, 8), pady=2)
            ttk.Label(sub, text=label + ":").pack(side=tk.LEFT)
            entry = ttk.Entry(sub, textvariable=var)
            entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(4, 0))
            if label.startswith("主模型"):
                # 存一份引用: 「自动」开关要靠它切只读/可写
                W._strata_model_entry = entry
                ttk.Checkbutton(sub, text="自动", variable=W.var_auto_model,
                                command=lambda: self._on_strata_auto_model_toggle(W)
                                ).pack(side=tk.LEFT, padx=(4, 0))
            if browse_ft:
                ttk.Button(sub, text="…", width=3,
                           command=lambda v=var, k=browse_ft:
                           self._browse(v, k)).pack(side=tk.LEFT, padx=(2, 0))
        ttk.Checkbutton(box1, text="启用 (未启用则不出现在 /v1/models)",
                        variable=W.var_enabled).grid(row=3, column=0, columnspan=4,
                                                     sticky=tk.W, pady=(2, 0))
        box1.columnconfigure(0, weight=1)
        box1.columnconfigure(1, weight=0)
        box1.columnconfigure(2, weight=1)
        box1.columnconfigure(3, weight=0)
        # 「跟随 json 自动」默认开 -> 主模型框只读
        self._on_strata_auto_model_toggle(W)

    def _on_strata_auto_model_toggle(self, W=None) -> None:
        """「主模型」框的自动/手动切换。

        自动 = 照 strata-<档位>.json 里的 --native (唯一真相, 框只读);
        手动 = 用户自己挑一个 gguf, 保存时写回 json 的 --native
        (以及原本跟着 --native 的那个 --ple-gguf)。
        """
        W = W if W is not None else self.st
        entry = getattr(W, "_strata_model_entry", None)
        if entry is not None:
            entry.configure(state="disabled" if W.var_auto_model.get()
                            else "normal")

    # ---- Strata 页: 清单与状态 ----
    def _refresh_strata_tab(self) -> None:
        W = getattr(self, "st", None)
        if W is None or not hasattr(W, "model_list"):
            return
        cfg = self.cfg
        self.strata_timeout_var.set(str((cfg.get("strata") or {}).get(
            "ready_timeout", 900)))
        active = strata.active_model(cfg)
        W.model_list.delete(0, tk.END)
        W.model_idx = []                  # 列表行 -> cfg["models"] 下标
        sel_pos = None
        for i, m in enumerate(strata.strata_models(cfg)):
            mid = str(m.get("id") or "?")
            alias = m.get("alias") or ""
            tag = " (停用)" if not m.get("enabled", True) else ""
            mark = " ✔" if (active and str(active.get("id")) == mid) else ""
            W.model_list.insert(tk.END, f"[{mid}] {alias}{tag}{mark}")
            W.model_idx.append(self.cfg["models"].index(m))
            if active and str(active.get("id")) == mid:
                sel_pos = len(W.model_idx) - 1
        if sel_pos is not None:
            # 打开页面/刷新时把当前档位直接选中, 不用再点一次
            W.model_list.selection_clear(0, tk.END)
            W.model_list.selection_set(sel_pos)
            self._strata_select_at(sel_pos)
        else:
            self._strata_clear_params()
        if active:
            self.strata_url_var.set(
                f"调用地址: {service.gateway_base_url(cfg)}  "
                f"(model = {active.get('alias') or active.get('id')}，"
                f"后端内部口 {strata.port_of(cfg)})")
        else:
            self.strata_url_var.set("调用地址: -")
        self.strata_found_var.set(
            f"python {'✓' if strata.find_python(cfg) else '✗'}   "
            f"server.py {'✓' if strata.find_server(cfg) else '✗'}   "
            f"目录 {strata.install_dir(cfg)}")
        self._on_strata_refresh_status()

    # ---- 扫描 / 登记 ----
    def _on_strata_scan(self) -> None:
        """「导入json」: 弹窗列出 install_dir 下能用的 strata-*.json。"""
        items = strata.scan_configs(self.cfg)
        if not items:
            messagebox.showinfo(
                "Strata", f"{strata.install_dir(self.cfg)} 下没有找到 "
                          f"可用的 strata-*.json。\n"
                          f"(要先跑一次 Strata 的 SETUP/START-HERE 才会生成)")
            return
        dlg = tk.Toplevel(self)
        dlg.title("导入 Strata 档位 json")
        dlg.geometry("760x420")
        ttk.Label(dlg, text=f"  选一个登记成档位 (来自 {strata.install_dir(self.cfg)})",
                  foreground="#555").pack(anchor=tk.W, padx=8, pady=(8, 2))
        lf = ttk.Frame(dlg)
        lf.pack(fill=tk.BOTH, expand=True, padx=8)
        lb = tk.Listbox(lf, exportselection=False, font=("Consolas", 10))
        lb.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb = ttk.Scrollbar(lf, orient=tk.VERTICAL, command=lb.yview)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        lb.config(yscrollcommand=sb.set)
        detail = tk.StringVar(value="")
        ttk.Label(dlg, textvariable=detail, foreground="#555",
                  wraplength=720, justify=tk.LEFT).pack(fill=tk.X, padx=8,
                                                        pady=4)

        def render() -> None:
            lb.delete(0, tk.END)
            for it in items:
                if it["ok"]:
                    bits = [it["file"], f"model_name={it['id']}"]
                    if it["max_context"]:
                        bits.append(f"ctx={it['max_context']}")
                    if it["vision"]:
                        bits.append("vision")
                else:
                    bits = [it["file"], f"— {it['why']}"]
                lb.insert(tk.END, "  ".join(bits))
            lb.selection_set(0)
            show()

        def show(_evt=None) -> None:
            sel = lb.curselection()
            if not sel:
                return
            it = items[sel[0]]
            if it["ok"]:
                detail.set(f"✅ 可登记\n档位 id: {it['id']}\n"
                           f"引擎配置: {it['path']}\n"
                           f"别名: {', '.join(it['aliases']) or '(无)'}")
            else:
                detail.set(f"⚠ 不能登记\n{it['why']}")

        lb.bind("<<ListboxSelect>>", show)
        bf = ttk.Frame(dlg)
        bf.pack(fill=tk.X, padx=8, pady=6)

        def do_import() -> None:
            sel = lb.curselection()
            if not sel:
                return
            it = items[sel[0]]
            if not it["ok"]:
                messagebox.showerror("Strata", f"这个 json 不能用：\n{it['why']}")
                return
            dlg.destroy()
            self._strata_register_entry(it)

        ttk.Button(bf, text="登记为档位", width=13,
                   command=do_import).pack(side=tk.LEFT)
        ttk.Button(bf, text="重新扫描", width=11,
                   command=lambda: (dlg.destroy(), self._on_strata_scan())
                   ).pack(side=tk.LEFT, padx=2)
        ttk.Button(bf, text="关闭", width=9,
                   command=dlg.destroy).pack(side=tk.RIGHT)
        render()

    def _on_strata_add(self) -> None:
        """「新增」= 挑一个 gguf, 现生成一份档位 json 再登记。

        Strata 的档位必须有一份自己的 json (参数在那里面), 所以这里不是
        凭空加一条空记录, 而是**复制当前档位的 json** 换模型 —— 沿用它的
        全部参数, 只把 --native / --ple-gguf 指到新模型。
        """
        W = self.st
        cur = self._strata_sel_model()
        src = os.path.join(strata.install_dir(self.cfg),
                           f"strata-{(cur or {}).get('id') or 'new'}.json")
        model = filedialog.askopenfilename(
            title="选一个模型 .gguf (会照现有档位复制一份参数)",
            filetypes=[("GGUF", "*.gguf"), ("所有", "*.*")])
        if not model:
            return
        base = strata.config_path_of(cur) if cur else ""
        if not (base and os.path.isfile(base)):
            self._on_strata_scan()
            return
        stem = os.path.splitext(os.path.basename(model))[0]
        safe = "".join(c if c.isalnum() or c in "-_." else "-"
                       for c in stem).strip("-")[:40] or "model"
        dst = os.path.join(strata.install_dir(self.cfg),
                           f"strata-{safe}.json")
        if os.path.exists(dst):
            if not messagebox.askyesno(
                    "同名档位已存在", f"{os.path.basename(dst)} 已存在，"
                                       f"要用它覆盖吗？"):
                return
        try:
            with open(base, "r", encoding="utf-8-sig") as f:
                data = json.load(f)
        except Exception as e:                             # noqa: BLE001
            messagebox.showerror("Strata", f"读模板失败：{e}")
            return
        args = list(data.get("args") or [])
        new_args: List[str] = []
        i = 0
        while i < len(args):
            tok = args[i]
            if tok in ("--native", "--ple-gguf") and i + 1 < len(args):
                new_args += [tok, model]
                i += 2
                continue
            new_args.append(tok)
            i += 1
        if "--native" not in new_args:
            new_args = ["--native", model] + new_args
        data["args"] = new_args
        data["model_name"] = safe
        if isinstance(data.get("vision"), dict):
            data["vision"]["model"] = model
        data.pop("aliases", None)
        try:
            with open(dst, "w", encoding="utf-8", newline="\n") as f:
                json.dump(data, f, ensure_ascii=False, indent=1)
        except Exception as e:                             # noqa: BLE001
            messagebox.showerror("Strata", f"写 {os.path.basename(dst)} 失败：{e}")
            return
        self._log(f"Strata 新档位 -> {os.path.basename(dst)} (模型 {model})")
        self._strata_register_entry({"path": dst, "file": os.path.basename(dst),
                                     "id": safe, "ok": True, "why": "",
                                     "model_name": safe, "aliases": [],
                                     "vision": bool(data.get("vision")),
                                     "max_context": ""})

    def _strata_register_entry(self, it: Dict[str, Any]) -> None:
        try:
            entry = strata.entry_from_file(it["path"])
        except Exception as e:                             # noqa: BLE001
            messagebox.showerror("Strata", f"读取失败：{e}")
            return
        models = self.cfg.setdefault("models", [])
        for m in models:
            if (m.get("runtime") == config.STRATA_RUNTIME
                    and str(m.get("id")) == entry["id"]):
                m["strata_config"] = entry["strata_config"]
                m.setdefault("enabled", True)
                config.save(self.cfg)
                self._log(f"Strata 档位 {entry['id']} 已改指向 "
                          f"{entry['strata_config']}")
                self._refresh_strata_tab()
                return
        models.append(entry)
        st = self.cfg.setdefault("strata", {})
        if not st.get("active_id"):
            st["active_id"] = entry["id"]
        config.save(self.cfg)
        self._log(f"已登记 Strata 档位 {entry['id']} ({entry['strata_config']})")
        self._refresh_strata_tab()
        # 登记完直接把新档位选中, 方便接着调参
        for i, m in enumerate(strata.strata_models(self.cfg)):
            if str(m.get("id")) == entry["id"]:
                W = self.st
                W.model_list.selection_clear(0, tk.END)
                W.model_list.selection_set(i)
                self._strata_select_at(i)
                break

    def _strata_move(self, delta: int) -> None:
        """上移/下移: 调 cfg.models 里的次序 (与另两页同一个操作)。"""
        W = self.st
        pos = self._ws_cfg_index(W)
        if pos is None:
            return
        items = strata.strata_models(self.cfg)
        if not (0 <= pos < len(items)):
            return
        target = pos + delta
        if not (0 <= target < len(items)):
            return
        models = self.cfg["models"]
        i, j = models.index(items[pos]), models.index(items[target])
        models[i], models[j] = models[j], models[i]
        config.save(self.cfg)
        self._refresh_strata_tab()
        W.model_list.selection_clear(0, tk.END)
        W.model_list.selection_set(target)
        self._strata_select_at(target)

    def _on_strata_pick_dir(self) -> None:
        """换 Strata 安装目录 (装了多个版本 / 挪了位置时用)。"""
        d = filedialog.askdirectory(title="选择 Strata 安装目录",
                                    initialdir=strata.install_dir(self.cfg))
        if d:
            st = self.cfg.setdefault("strata", {})
            st["install_dir"] = d
            config.save(self.cfg)
            self._log(f"Strata 安装目录 -> {d}")
            self._refresh_strata_tab()

    def _on_strata_save_timeout(self) -> None:
        st = self.cfg.setdefault("strata", {})
        try:
            st["ready_timeout"] = max(30, int(self.strata_timeout_var.get().strip()))
        except ValueError:
            messagebox.showerror("Strata", "就绪超时必须是整数（秒）")
            return
        config.save(self.cfg)
        self._log(f"Strata 就绪超时 -> {st['ready_timeout']}s")
        self._refresh_strata_tab()

    def _strata_sel_model(self) -> Optional[Dict[str, Any]]:
        W = getattr(self, "st", None)
        if W is None:
            return None
        return self._ws_model(W)

    def _strata_select_at(self, pos: int) -> None:
        items = strata.strata_models(self.cfg)
        if not (0 <= pos < len(items)):
            return
        m = items[pos]
        cfgp = strata.config_path_of(m)
        W = self.st
        # 基础信息 (与另两页同一套字段)
        W.var_id.set(str(m.get("id") or ""))
        W.var_alias.set(str(m.get("alias") or ""))
        W.var_enabled.set(bool(m.get("enabled", True)))
        W.var_tier_json.set(cfgp)
        # 主模型: 默认「跟随 json 自动」, 框里显示 json 里的 --native
        W.var_auto_model.set(True)
        try:
            data = strata.load_tier(m)
            _mg, paths, _o, _ord = strata.parse_tier_args(
                list(data.get("args") or []))
            W.var_model.set(paths.get("--native", ""))
            vis = data.get("vision") or {}
            W.var_mmproj.set(str(vis.get("mmproj") or ""))
        except Exception:                                # noqa: BLE001
            W.var_model.set("")
            W.var_mmproj.set("")
        self._on_strata_auto_model_toggle(W)
        self._strata_load_params(m)

    def _on_strata_select(self, _evt=None) -> None:
        W = getattr(self, "st", None)
        sel = W.model_list.curselection() if W is not None else ()
        if not sel:
            self._strata_clear_params()
            return
        self._strata_select_at(sel[0])

    # ---- 档位参数: 读/写 strata-<档位>.json ----
    def _strata_clear_params(self) -> None:
        if not hasattr(self, "strata_flag_vars"):
            return
        for var in self.strata_flag_vars.values():
            try:
                var.set(False if isinstance(var, tk.BooleanVar) else "")
            except tk.TclError:
                pass
        for var in self.strata_path_vars.values():
            var.set("")
        self.strata_others_var.set("")
        self.strata_par_hint.set("先在左边选一个已登记的档位")

    def _strata_load_params(self, m: Dict[str, Any]) -> None:
        """把档位 json 里的 args 填进编辑器。"""
        try:
            data = strata.load_tier(m)
        except Exception as e:                            # noqa: BLE001
            self._strata_clear_params()
            self.strata_par_hint.set(f"⚠ 读不了 {strata.config_path_of(m)}: {e}")
            return
        managed, paths, others, order = strata.parse_tier_args(
            list(data.get("args") or []))
        self._strata_order = order
        for flag, var in self.strata_flag_vars.items():
            kind = strata._FLAG_KIND[flag][0]
            val = managed.get(flag)
            if kind == "bool":
                var.set(bool(val))
            else:
                var.set("" if val is None else str(val))
        for flag, var in self.strata_path_vars.items():
            var.set(paths.get(flag, ""))
        self.strata_others_var.set(" ".join(others))
        extra = [f for f in order if f not in strata._FLAG_KIND
                 and f not in strata.PATH_FLAGS]
        bits = [f"{len(managed)} 项常用参数"]
        if extra:
            bits.append(f"另有 {len(extra)} 个原样保留: {' '.join(extra)}")
        bits.append(f"model_name={data.get('model_name')}")
        if data.get("gpu") is not None:
            bits.append(f"gpu={data.get('gpu')}")
        bits.append(f"lib_dirs={len(data.get('lib_dirs') or [])} 项")
        self.strata_par_hint.set(" · ".join(bits))

    def _on_strata_reload_params(self) -> None:
        m = self._strata_sel_model()
        if not m:
            return
        self._strata_load_params(m)
        self.strata_par_msg.set("已重读")

    def _on_strata_open_json(self) -> None:
        m = self._strata_sel_model()
        if not m:
            messagebox.showinfo("提示", "请先在左边选择一个 Strata 档位")
            return
        p = strata.config_path_of(m)
        if not p or not os.path.isfile(p):
            messagebox.showerror("Strata", f"档位的 json 不存在:\n{p}")
            return
        try:
            os.startfile(p)                                # noqa: S606
        except Exception as e:                            # noqa: BLE001
            messagebox.showerror("Strata", f"打不开: {e}")

    def _on_strata_save_params(self) -> None:
        self._on_strata_save_entry(self.st)

    def _on_strata_save_entry(self, W=None) -> None:
        """「💾 保存」: 一次把基础信息和参数都落盘。

        基础信息里 ID/别名/启用 写 config.json 的模型条目, 其余(主模型 /
        mmproj / 引擎 json / 常用参数 / 其它 token) 写 strata-<档位>.json ——
        后者才是 Strata 真正读的东西, 所以不搞影子副本。
        """
        W = W if W is not None else getattr(self, "st", None)
        m = self._strata_sel_model()
        if W is None or not m:
            messagebox.showinfo("提示", "请先在左边选择一个 Strata 档位")
            return
        cfgp = strata.config_path_of(m)
        if not cfgp or not os.path.isfile(cfgp):
            messagebox.showerror("Strata", f"档位的 json 不存在:\n{cfgp}")
            return
        # ---- 参数 (常用项 / 其它 token / 标识路径) ----
        managed: Dict[str, Any] = {}
        for flag, var in self.strata_flag_vars.items():
            kind = strata._FLAG_KIND[flag][0]
            if kind == "bool":
                managed[flag] = bool(var.get())
                continue
            text = str(var.get() or "").strip()
            if not text:
                continue                              # 清空 = 删掉这条参数
            if kind == "int" and not text.lstrip("-").isdigit():
                messagebox.showerror("Strata",
                                     f"{flag} 必须是整数, 现在是 {text!r}")
                return
            managed[flag] = text
        others = [t for t in self.strata_others_var.get().split() if t.strip()]
        # 标识路径: --native/--ple-gguf 由基础信息决定 (自动=照 json, 手动=用户挑的)
        try:
            data = strata.load_tier(m)
        except Exception as e:                            # noqa: BLE001
            messagebox.showerror("Strata", f"读档位 json 失败: {e}")
            return
        _mg, old_paths, _o, order = strata.parse_tier_args(
            list(data.get("args") or []))
        paths = dict(old_paths)
        picked = W.var_model.get().strip()
        if not W.var_auto_model.get():
            if not picked:
                messagebox.showerror("Strata", "手动模式下要选一个 .gguf")
                return
            if not os.path.isfile(picked):
                messagebox.showerror("Strata", f"模型文件不存在:\n{picked}")
                return
            old_native = old_paths.get("--native", "")
            paths["--native"] = picked
            # --ple-gguf 原本就是跟着 --native 同一个分片时才一起换;
            # 用户特意指定了另一张 PLE 表就别动它
            if not paths.get("--ple-gguf") or paths.get("--ple-gguf") == old_native:
                paths["--ple-gguf"] = picked
        else:
            # 自动: 照 json 里的值, 不覆盖
            paths["--native"] = old_paths.get("--native", "")
            paths["--ple-gguf"] = old_paths.get("--ple-gguf", "")
        ok, msg = strata.save_tier(m, managed, paths, others, order)
        if not ok:
            self.strata_par_msg.set(msg)
            messagebox.showerror("Strata", msg)
            return
        # ---- mmproj -> json 的 vision.mmproj ----
        mmproj = W.var_mmproj.get().strip()
        cur_mm = str((data.get("vision") or {}).get("mmproj") or "")
        if mmproj != cur_mm:
            ok2, msg2 = strata.set_tier_mmproj(cfgp, mmproj)
            if not ok2:
                self._log("⚠ " + msg2)
            else:
                msg += "; " + (msg2 or "已更新 vision.mmproj")
        # ---- ID / 别名 / 启用 / 引擎 json -> config.json ----
        new_id = W.var_id.get().strip()
        new_alias = W.var_alias.get().strip() or new_id
        new_json = W.var_tier_json.get().strip() or cfgp
        old_id = str(m.get("id") or "")
        if new_id != old_id:
            for other in self.cfg.get("models", []):
                if (other.get("runtime") == config.STRATA_RUNTIME
                        and str(other.get("id") or "") == new_id):
                    messagebox.showerror(
                        "Strata", f"已经有一个叫 {new_id} 的 Strata 档位了")
                    return
        m["id"] = new_id or old_id
        m["alias"] = new_alias
        m["enabled"] = bool(W.var_enabled.get())
        m["strata_config"] = new_json
        st = self.cfg.setdefault("strata", {})
        if st.get("active_id") == old_id:
            st["active_id"] = m["id"]
        config.save(self.cfg)
        self.strata_par_msg.set("已保存")
        self._log("✔ " + msg)
        if strata.is_installed():
            self.strata_hint_var.set(
                "已保存, 要点「重启」才会用新参数 (加载 40GB, 约 1-3 分钟)。")
        self._refresh_strata_tab()

    def _on_strata_set_active(self) -> None:
        m = self._strata_sel_model()
        if not m:
            messagebox.showinfo("提示", "请先在左侧选择一个 Strata 档位")
            return
        pid = str(m.get("id") or "")
        st = self.cfg.setdefault("strata", {})
        if st.get("active_id") == pid:
            self._log(f"Strata 当前档位已经是 {pid}")
            return
        st["active_id"] = pid
        config.save(self.cfg)
        self._log(f"Strata 当前档位 -> {pid}")
        if strata.is_installed():
            self.strata_hint_var.set(
                "切换档位后请点「应用并重装」重新写入参数（Strata 加载要 1-3 分钟）。")
        self._refresh_strata_tab()

    def _on_strata_remove(self) -> None:
        m = self._strata_sel_model()
        if not m:
            messagebox.showinfo("提示", "请先在左侧选择一个 Strata 档位")
            return
        pid = str(m.get("id") or "")
        if not messagebox.askyesno(
                "移除档位",
                f"把 Strata 档位 {pid} 从清单里移除？\n"
                f"（只删清单条目，不动 {strata.install_dir(self.cfg)} 下的 json）"):
            return
        self.cfg["models"] = [m2 for m2 in self.cfg.get("models", [])
                              if not (m2.get("runtime") == config.STRATA_RUNTIME
                                      and str(m2.get("id") or "") == pid)]
        st = self.cfg.setdefault("strata", {})
        if st.get("active_id") == pid:
            st["active_id"] = ""
        config.save(self.cfg)
        self._log(f"已移除 Strata 档位 {pid}")
        self._refresh_strata_tab()

    def _on_strata_copy_url(self) -> None:
        url = service.gateway_base_url(self.cfg)
        try:
            self.clipboard_clear()
            self.clipboard_append(url)
            self._log(f"已复制调用地址: {url}")
        except tk.TclError:
            self._log(f"调用地址: {url}")

    def _strata_done(self, ok: bool, msg: str) -> None:
        self._log(("✔ " if ok else "✘ ") + msg)
        if not ok:
            messagebox.showerror("Strata", msg)
        self._refresh_strata_tab()

    def _on_strata_install(self) -> None:
        self.cfg["serve_mode"] = config.STRATA_RUNTIME
        config.save(self.cfg)
        self._strata_done(*strata.install(self.cfg))

    def _on_strata_to_pool(self) -> None:
        self.cfg["serve_mode"] = "router"
        config.save(self.cfg)
        self._strata_done(*service.install(self.cfg))

    def _on_strata_start(self) -> None:
        self._strata_done(*strata.start())

    def _on_strata_stop(self) -> None:
        self._strata_done(*strata.stop())

    def _on_strata_restart(self) -> None:
        self._strata_done(*strata.restart())

    def _on_strata_fg(self) -> None:
        self._strata_done(*strata.run_foreground(self.cfg))

    def _on_strata_refresh_status(self) -> None:
        if not hasattr(self, "strata_state_var"):
            return
        text = f"运行服务: {strata.status()}"
        port = strata.port_of(self.cfg)
        text += f"   Strata 后端 127.0.0.1:{port}"
        if strata.port_busy(port):
            text += "  (端口已被占用)"
        active = strata.active_model(self.cfg)
        text += f"   当前档位: {active.get('id') if active else '(未登记)'}"
        self.strata_state_var.set(text)

    def _on_strata_open_log(self) -> None:
        for path in (strata.err_log(), strata.out_log()):
            if os.path.isfile(path):
                self._open_file(path)
                return
        messagebox.showinfo("提示", "还没有 Strata 日志，先应用并重装一次服务。")

    def _on_draft_enable_toggle(self, W=None) -> None:
        W = W if W is not None else self
        """启用/停用推测解码: 控制折叠按钮可用性与面板显示。"""
        enabled = bool(W.var_draft_enabled.get())
        W._draft_collapse_cb.configure(state="normal" if enabled else "disabled")
        if not enabled:
            W.draft_box.pack_forget()
        else:
            self._sync_draft_box(W)

    def _toggle_draft_box(self, W=None) -> None:
        W = W if W is not None else self
        """展开/折叠推测解码面板。"""
        if not W.var_draft_enabled.get():
            W.draft_box.pack_forget()
            return
        self._sync_draft_box(W)

    def _sync_draft_box(self, W=None) -> None:
        W = W if W is not None else self
        """按启用开关 + 折叠状态显示/隐藏推测解码面板。"""
        if not W.var_draft_enabled.get() or W._draft_collapsed.get():
            W.draft_box.pack_forget()
            return
        # 展开 -> 插在 toggle bar 和下面的「视觉模型 / KV」区块之间,
        # 保证 推测解码 在上、视觉/KV 在下 (否则草稿面板会掉到最末尾)
        before = getattr(W, "_mmkv_wrap", None)
        if before is not None:
            W.draft_box.pack(fill=tk.X, padx=4, pady=(0, 4), before=before)
            return
        # 找不到就 fallback
        W.draft_box.pack(fill=tk.X, padx=4, pady=(0, 4))

    # ---- 工作区: listbox 下标 <-> cfg["models"] 下标 ----
    def _ws_cfg_index(self, W, pos: Optional[int] = None) -> Optional[int]:
        """当前选中行 (或指定行) 对应的 cfg["models"] 下标; 没选中返回 None。"""
        W = W if W is not None else self
        if pos is None:
            sel = W.model_list.curselection()
            if not sel:
                return None
            pos = sel[0]
        idx = getattr(W, "model_idx", []) or []
        return idx[pos] if 0 <= pos < len(idx) else None

    def _ws_model(self, W, pos: Optional[int] = None) -> Optional[Dict[str, Any]]:
        i = self._ws_cfg_index(W, pos)
        return self.cfg["models"][i] if i is not None else None

    def _get_model_param(self, key: str, W=None) -> Dict[str, Any]:
        W = W if W is not None else self
        m = self._ws_model(W)
        if not m:
            return {"enabled": False, "value": ""}
        return (m.get("params") or {}).get(key, {"enabled": False, "value": ""})

    def _set_model_param(self, key: str, value: Dict[str, Any], W=None) -> None:
        W = W if W is not None else self
        m = self._ws_model(W)
        if not m:
            return
        m.setdefault("params", {})[key] = value

    def _browse(self, var: tk.StringVar, kind: str) -> None:
        if kind == "gguf":
            path = filedialog.askopenfilename(filetypes=[("GGUF", "*.gguf"), ("所有", "*.*")])
        elif kind == "exe":
            path = filedialog.askopenfilename(
                filetypes=[("可执行文件", "*.exe"), ("所有", "*.*")])
        elif kind == "dir":
            path = filedialog.askdirectory()
        else:
            path = filedialog.askopenfilename()
        if path:
            var.set(path)
            if kind == "exe":
                self._sync_kvmem_exe_label()

    def _toggle_api_key(self, W=None) -> None:
        W = W if W is not None else self
        """切换 API Key 密码显示/隐藏"""
        visible = not W.var_api_key_visible.get()
        W.var_api_key_visible.set(visible)
        if W._api_key_entry:
            W._api_key_entry.configure(show="" if visible else "*")

    def _on_select_model(self, _evt=None, W=None) -> None:
        W = W if W is not None else self
        m = self._ws_model(W)
        if not m:
            self._sel_log(
                "SELECT ws=%s pos=%s NO_MODEL | %s"
                % (getattr(W, "ws_kind", "pool"),
                   W.model_list.curselection() or None,
                   self._sel_caller()))
            return
        self._sel_log(
            "SELECT ws=%s pos=%s cfg_idx=%s id=%s evt=%s | %s"
            % (getattr(W, "ws_kind", "pool"),
               W.model_list.curselection() or None,
               self._ws_cfg_index(W),
               m.get("id", "?"),
               "none" if _evt is None else type(_evt).__name__,
               self._sel_caller()))
        W.selected_cfg = self._ws_cfg_index(W)
        # 记住「用户当前看到的高亮行是哪条模型」, 清单重建时按它钉住高亮
        W.selected_id = str(m.get("id") or "")
        W.var_id.set(m.get("id", ""))
        W.var_alias.set(m.get("alias", ""))
        W.var_model.set(m.get("model", ""))
        W.var_mmproj.set(m.get("mmproj", ""))
        W.var_base_url.set(m.get("base_url", ""))
        W.var_api_key.set(m.get("api_key", ""))
        W.var_enabled.set(bool(m.get("enabled", True)))
        W.var_draft_enabled.set(bool(m.get("draft_enabled", bool(m.get("draft_model")))))
        W.var_draft_model.set(m.get("draft_model", ""))
        W.var_draft_type.set(m.get("draft_type", ""))
        W.var_draft_n_max.set(str(m.get("draft_n_max", "3")))
        W.var_draft_n_min.set(str(m.get("draft_n_min", "0")))
        W.var_draft_p_split.set(str(m.get("draft_p_split", "0.1")))
        W.var_draft_p_min.set(str(m.get("draft_p_min", "0.0")))
        W.var_draft_ngl.set(str(m.get("draft_ngl", "auto")))
        # 快捷字段勾选状态 (老配置无 draft_en -> 全勾)
        de = m.get("draft_en") or {}
        for key, var in W.var_draft_en.items():
            var.set(bool(de.get(key, True)))
        self._apply_draft_en(W)
        # 兼容老 cfg：草稿参数可能在 params 里
        pmap = m.get("params", {}) or {}
        if "spec-draft-n-max" in pmap and not m.get("draft_n_max"):
            W.var_draft_n_max.set(str(pmap["spec-draft-n-max"].get("value", "3")))
        if "spec-draft-n-min" in pmap and not m.get("draft_n_min"):
            W.var_draft_n_min.set(str(pmap["spec-draft-n-min"].get("value", "0")))
        if "spec-draft-p-split" in pmap and not m.get("draft_p_split"):
            W.var_draft_p_split.set(str(pmap["spec-draft-p-split"].get("value", "0.1")))
        if "spec-draft-p-min" in pmap and not m.get("draft_p_min"):
            W.var_draft_p_min.set(str(pmap["spec-draft-p-min"].get("value", "0.0")))
        if "spec-draft-ngl" in pmap and (not m.get("draft_ngl") or m.get("draft_ngl") == "auto"):
            v = str(pmap["spec-draft-ngl"].get("value", "auto"))
            if v and v != "auto":
                W.var_draft_ngl.set(v)
        # 高级可选项: 勾选状态 + 值都从 params 读
        for key, (en, va) in W.var_draft_opt.items():
            node = pmap.get(key)
            if isinstance(node, dict):
                en.set(bool(node.get("enabled", False)))
                if node.get("value") not in (None, ""):
                    va.set(str(node["value"]))
            else:
                en.set(False)
        # 同步推测解码面板可见性 (按启用开关 + 折叠状态)
        W._draft_collapse_cb.configure(
            state="normal" if W.var_draft_enabled.get() else "disabled")
        self._sync_draft_box(W)
        W.model_param_panel.reload()
        self._mmkv_load(W)

    def _on_list_press(self, evt, W=None) -> None:
        """记下「按下的那行」—— 选中项在按下这一刻定死, 之后怎么挪都不改。"""
        W = W if W is not None else self
        W._motion_logged = False
        W._hover_row = None
        W._press_row = None
        try:
            lst = W.model_list
            row = lst.nearest(evt.y) if lst.size() else -1
            bb = lst.bbox(row) if 0 <= row < lst.size() else None
            # 点在行上才算「按下了某行」; 点在末行下面的空白处不认
            if bb and bb[1] <= evt.y <= bb[1] + bb[3]:
                W._press_row = row
            self._sel_log("PRESS ws=%s row=%s | %s"
                          % (getattr(W, "ws_kind", "pool"), W._press_row,
                             self._sel_caller()))
        except Exception:
            W._press_row = None

    def _on_list_motion(self, evt, W=None) -> str:
        """按住鼠标拖动时 **不让高亮跟着鼠标扫**。

        Tk 的 browse 模式默认在 <B1-Motion> 里把选中项挪到指针所在行,
        手快一点就会看到高亮一行行扫过去、松手又跳回来。
        这里直接 break 掉默认行为: 选中项只在按下那刻定一次。
        """
        W = W if W is not None else self
        press = getattr(W, "_press_row", None)
        try:
            lst = W.model_list
            row = lst.nearest(evt.y) if lst.size() else -1
            if press is not None and row != press \
                    and not getattr(W, "_motion_logged", False):
                W._motion_logged = True
                self._sel_log(
                    "MOTION_FROZEN ws=%s press=%s cursor=%s sel=%s"
                    % (getattr(W, "ws_kind", "pool"), press, row,
                       lst.curselection() or None))
        except Exception:
            pass
        return "break"

    def _on_list_hover(self, evt, W=None) -> None:
        """纯悬停 (没按鼠标) 只记日志, 绝不动选中项 —— 用来证实
        「高亮跟着鼠标跳」到底发生在按住时还是悬停时。"""
        W = W if W is not None else self
        try:
            lst = W.model_list
            if not lst.size() or getattr(W, "_press_row", None) is not None:
                return                       # 拖动中交给 _on_list_motion
            row = lst.nearest(evt.y)
            if row == getattr(W, "_hover_row", None):
                return                       # 没跨行, 不刷屏
            W._hover_row = row
            self._sel_log("HOVER ws=%s cursor=%s sel=%s"
                          % (getattr(W, "ws_kind", "pool"), row,
                             lst.curselection() or None))
        except Exception:
            pass

    def _on_list_release(self, evt, W=None) -> str:
        """松开鼠标: 选中项以**按下的那行**为准。

        正常点击时 curselection 已经等于按下行, 这里什么都不做;
        拖动过 / 高亮被别的地方挪走了才补回按下行。
        """
        W = W if W is not None else self
        lst = W.model_list
        press = getattr(W, "_press_row", None)
        W._press_row = None
        if press is None:
            return "break"                  # 按下点不在行上, 保持原样
        try:
            if lst.curselection() != (press,):
                lst.selection_clear(0, tk.END)
                lst.selection_set(press)
                lst.see(press)
                self._on_select_model(W=W)
                self._sel_log(
                    "RELEASE_REPAIR ws=%s press=%s cur_before=%s | %s"
                    % (getattr(W, "ws_kind", "pool"), press, "other/none",
                       self._sel_caller()))
            else:
                self._sel_log(
                    "RELEASE ws=%s press=%s sel=%s | %s"
                    % (getattr(W, "ws_kind", "pool"), press,
                       lst.curselection(), self._sel_caller()))
        except Exception:
            pass
        return "break"

    def _on_add_model(self, W=None) -> None:
        W = W if W is not None else self
        base = "new-model"
        idx = 1
        existing = {m.get("id", "") for m in self.cfg["models"]}
        new_id = base
        while new_id in existing:
            idx += 1
            new_id = f"{base}-{idx}"
        m = {"id": new_id, "alias": new_id, "model": "", "mmproj": "",
             "base_url": "", "api_key": "",
             "draft_enabled": False,
             "draft_model": "", "draft_type": "",
             "draft_n_max": "3", "draft_n_min": "0",
             "draft_p_split": "0.1", "draft_p_min": "0.0", "draft_ngl": "auto",
             "draft_en": {k: True for k in config.DRAFT_SHORTCUT_KEYS},
             "enabled": True, "params": {}}
        # KVMem 页新增的条目直接就是档位 (与 bat 导入的条目同构)
        if getattr(W, "ws_kind", "pool") == "kvmem":
            m["runtime"] = config.KVMEM_RUNTIME
            m["kvmem_port"] = ""
        config._ensure_model_params(m)
        self.cfg["models"].append(m)
        self._refresh_model_list(select=len(self.cfg["models"]) - 1, W=W)

    def _on_del_model(self, W=None) -> None:
        W = W if W is not None else self
        idx = self._ws_cfg_index(W)
        if idx is None:
            return
        m = self.cfg["models"][idx]
        if not messagebox.askyesno("确认", f"删除模型 [{m.get('id')}] ?"):
            return
        del self.cfg["models"][idx]
        self._refresh_model_list(select=max(0, idx - 1), W=W)

    def _move(self, delta, W=None) -> None:
        """在**本工作区可见的**条目之间调整顺序 (跨运行时的条目互不干扰)。"""
        W = W if W is not None else self
        sel = W.model_list.curselection()
        if not sel:
            return
        pos = sel[0]
        other = pos + delta
        if other < 0 or other >= len(getattr(W, "model_idx", []) or []):
            return
        i, j = W.model_idx[pos], W.model_idx[other]
        self.cfg["models"][i], self.cfg["models"][j] = (
            self.cfg["models"][j], self.cfg["models"][i]
        )
        self._refresh_model_list(select=j, W=W)

    def _on_save_model(self, W=None) -> None:
        W = W if W is not None else self
        idx = self._ws_cfg_index(W)
        if idx is None:
            messagebox.showinfo("提示", "请先选择一个模型")
            return
        m = self.cfg["models"][idx]
        m["id"] = W.var_id.get().strip()
        m["alias"] = W.var_alias.get().strip()
        m["model"] = W.var_model.get().strip()
        m["mmproj"] = W.var_mmproj.get().strip()
        m["base_url"] = W.var_base_url.get().strip()   # 留空表示用本机
        m["api_key"] = W.var_api_key.get().strip()     # 留空表示无
        m["enabled"] = bool(W.var_enabled.get())
        m["draft_enabled"] = bool(W.var_draft_enabled.get())
        m["draft_model"] = W.var_draft_model.get().strip()
        m["draft_type"] = W.var_draft_type.get().strip()
        m["draft_n_max"] = W.var_draft_n_max.get().strip() or "3"
        m["draft_n_min"] = W.var_draft_n_min.get().strip() or "0"
        m["draft_p_split"] = W.var_draft_p_split.get().strip() or "0.1"
        m["draft_p_min"] = W.var_draft_p_min.get().strip() or "0.0"
        m["draft_ngl"] = W.var_draft_ngl.get().strip() or "auto"
        # 快捷字段勾选状态 (每个字段独立决定写不写 ini)
        m["draft_en"] = {k: bool(v.get()) for k, v in W.var_draft_en.items()}
        # 同步到 params 字典 (给 generate_preset 写 ini 使用)
        # 启用后草稿文件可选: 无路径时只写 spec-type 等其余草稿参数
        params = m.setdefault("params", {})
        # 高级可选项: 勾选状态直接落进 params (受草稿总开关抑制, 由 generate_preset 把关)
        for key, (en, va) in W.var_draft_opt.items():
            params[key] = {"enabled": bool(en.get()),
                           "value": va.get().strip()}
        if m["draft_enabled"]:
            en = m["draft_en"]
            if m["draft_model"]:
                params["spec-draft-model"] = {
                    "enabled": bool(en.get("spec-draft-model", True)),
                    "value": m["draft_model"]}
            else:
                params.pop("spec-draft-model", None)
            params["spec-draft-n-max"] = {"enabled": bool(en.get("spec-draft-n-max", True)),
                                          "value": m["draft_n_max"]}
            params["spec-draft-n-min"] = {"enabled": bool(en.get("spec-draft-n-min", True)),
                                          "value": m["draft_n_min"]}
            params["spec-draft-p-split"] = {"enabled": bool(en.get("spec-draft-p-split", True)),
                                            "value": m["draft_p_split"]}
            params["spec-draft-p-min"] = {"enabled": bool(en.get("spec-draft-p-min", True)),
                                          "value": m["draft_p_min"]}
            if m["draft_ngl"]:
                params["spec-draft-ngl"] = {"enabled": bool(en.get("spec-draft-ngl", True)),
                                            "value": m["draft_ngl"]}
        else:
            # 关闭开关 -> 清掉草稿快捷参数 (值来自顶层 draft_* 字段), 不写入 preset。
            # 高级可选项保留勾选状态, 由 generate_preset 按总开关抑制输出。
            for k in config.DRAFT_QUICK_KEYS:
                params.pop(k, None)
        if not m["id"]:
            messagebox.showerror("错误", "模型 ID 不能为空")
            return
        if not m["model"]:
            messagebox.showerror("错误", "模型文件路径不能为空")
            return
        ids = [x["id"] for x in self.cfg["models"]]
        if ids.count(m["id"]) > 1:
            messagebox.showerror("错误", f"模型 ID 重复: {m['id']}")
            return
        if m["draft_enabled"] and not m["draft_model"] and not m["draft_type"]:
            messagebox.showwarning(
                "提示",
                "已启用推测解码, 但既未选择推测类型也未指定草稿文件。\n"
                "模型自带草稿时请在「推测类型」里选 (如 draft-mtp / ngram-simple)。")
        W.model_param_panel.save_all()
        self._save_config()
        self._refresh_model_list(select=idx, W=W)
        if getattr(W, "ws_kind", "pool") == "kvmem":
            self._log("已保存 KVMem 档位配置")
            if kvmem.is_installed():
                self.kvmem_hint_var.set("改了参数，需重新点「应用并重装」才生效。")
        else:
            self._log("已保存模型配置")

    def _on_import_bats(self) -> None:
        """从已有的启动 .bat 导入参数 —— 用户手动挑文件, 不扫目录、不支持拖入。
        bat 里出现的每一个 flag 都会落到 config (schema 没有的登记成额外参数),
        同一条目重复导入按覆盖处理。"""
        # 先把两个面板手上的未保存改动落盘, 免得导入结果被旧值盖掉
        for p in list(self._param_panels):
            p.save_all()

        paths = list(filedialog.askopenfilenames(
            title="选择要导入的启动脚本 (可多选)",
            filetypes=[("批处理文件", "*.bat"), ("所有文件", "*.*")]))
        if not paths:
            return

        try:
            sel = self.model_list.curselection()
            sel0 = self._ws_cfg_index(self, sel[0]) if sel else None
        except Exception:
            sel0 = None

        reports = bat_import.import_bats(self.cfg, paths)
        ok = [r for r in reports if not r.get("error")]
        if not ok:
            msg = "\n".join(
                f"{os.path.basename(r.get('path', ''))}: {r.get('error')}"
                for r in reports)
            self._log("✘ 导入失败, 配置未改动\n" + msg)
            messagebox.showerror("导入 bat", "没有导入任何脚本:\n" + msg)
            return

        config.save(self.cfg)
        skipped: list = []
        preset_path = generate_preset.generate(self.cfg, skipped=skipped)
        self._log(f"已导入 {len(ok)}/{len(reports)} 个 .bat -> {config.CONFIG_PATH}"
                  f" (router-preset.ini 重新生成: {preset_path})")
        if skipped:
            self._log("注意: 本机 llama-server 不支持以下参数, 已跳过未写入: "
                      + ", ".join(skipped))
        for r in reports:
            name = os.path.basename(r.get("path", ""))
            if r.get("error"):
                self._log(f"  ✘ {name}: {r['error']}")
                continue
            how = ("新建" if r.get("added")
                   else "覆盖同 id" if r.get("overwrite") == "id"
                   else "覆盖已有条目")
            self._log(f"  ✔ {name} -> [{r.get('id')}] "
                      f"({how} · {r.get('target')} · {r.get('param_count', 0)} 个参数)")
            for n in r.get("notes") or []:
                self._log(f"      · {n}")

        # 列表 / 两个工作区的参数面板都可能因为新增的额外参数而变, 全刷一遍
        self._refresh_model_list(select=sel0)
        self._refresh_kvmem_tab()
        for attr in ("model_param_panel",):
            if hasattr(self, attr):
                getattr(self, attr).reload()
        for p in list(self._param_panels):
            p.reload()
        if hasattr(self, "_refresh_extra_list"):
            self._refresh_extra_list()

        bad = [r for r in reports if r.get("error")]
        lines = [f"成功 {len(ok)}/{len(reports)} 个脚本。", ""]
        for r in ok:
            lines.append(
                f"  {'新建' if r.get('added') else '覆盖'} [{r.get('id')}] "
                f"—— {r.get('param_count', 0)} 个参数"
                + (f"，登记了额外参数: {', '.join(r.get('unknown') or [])}"
                   if r.get("unknown") else ""))
        if bad:
            lines += ["", "失败:"] + [
                f"  {os.path.basename(r.get('path', ''))}: {r.get('error')}"
                for r in bad]
        if kvmem.is_installed():
            lines += ["", "运行服务已注册, 需要重新点「应用并重装」才用上新参数。"]
        messagebox.showinfo("导入 bat", "\n".join(lines))

    # ------------------------------------------------------------------
    # 选中行诊断: 记录「谁在什么时候把高亮挪到了哪一行」
    # 只写 logs/sel_debug.log (logs/ 不进 git、不进发布包), 每次启动重开一份。
    # ------------------------------------------------------------------
    def _sel_log(self, msg: str, truncate: bool = False) -> None:
        try:
            path = os.path.join(config.app_dir(), "logs", "sel_debug.log")
            os.makedirs(os.path.dirname(path), exist_ok=True)
            if not truncate and os.path.exists(path) and os.path.getsize(path) > 262144:
                return                       # 兜底: 单次会话最多 256KB
            with open(path, "w" if truncate else "a", encoding="utf-8") as f:
                f.write(msg + "\n")
        except Exception:
            pass

    @staticmethod
    def _sel_caller() -> str:
        try:
            import traceback
            st = traceback.extract_stack(limit=8)[:-1]
            return " <- ".join("%s:%s" % (fr.name, fr.lineno) for fr in reversed(st[-4:]))
        except Exception:
            return "?"

    def _refresh_model_list(self, select: Optional[int] = None, W=None) -> None:
        """重建当前工作区的列表 —— **各管各的清单**:
        模型页只列主池条目, 「⚡ KVMem」页只列档位, kvmem 条目不再混进模型页。

        select 是 **cfg["models"] 下标** (不是 listbox 下标)。
        没传 select 时按**模型 id** 保持原来的选中项 —— 用 id 而不是下标,
        是因为增删/移动之后下标会漂, 按下标找会把高亮挪到别的模型上。
        """
        W = W if W is not None else self
        # 重建前先记住「用户看到的选中项是哪条模型」, 候选按优先级:
        #   1) selected_id —— 由 _on_select_model 记下, 就是清单里高亮那行的 id,
        #      即便 cfg 在这一刻被人改过 (下标漂了) 也不会认错条目;
        #   2) 现在 curselection 那行按旧 model_idx 反查出来的 id (兜底);
        #   都没有 -> 视为「从没选中过」, 才允许落到第一行。
        cur = W.model_list.curselection()
        had_sel = bool(cur) or bool(str(getattr(W, "selected_id", "") or ""))
        keep_ids = []
        sid = str(getattr(W, "selected_id", "") or "")
        if sid:
            keep_ids.append(sid)
        if cur:
            keep_idx = self._ws_cfg_index(W, cur[0])
            if keep_idx is not None and 0 <= keep_idx < len(self.cfg["models"]):
                cid = str(self.cfg["models"][keep_idx].get("id") or "")
                if cid and cid not in keep_ids:
                    keep_ids.append(cid)

        want_kv = getattr(W, "ws_kind", "pool") == "kvmem"
        W.model_list.delete(0, tk.END)
        W.model_idx = []
        for i, m in enumerate(self.cfg["models"]):
            rt = m.get("runtime")
            # Strata 档位由「🌐 Strata」页托管 (它的参数在 strata-*.json 里,
            # 参数面板对它没有意义), 不能混进这两页
            if rt == config.STRATA_RUNTIME:
                continue
            if (rt == config.KVMEM_RUNTIME) != want_kv:
                continue
            mid = m.get("id", "?")
            alias = m.get("alias", "")
            tag = " (停用)" if not m.get("enabled", True) else ""
            draft = " ⚡草稿" if m.get("draft_enabled", m.get("draft_model")) else ""
            W.model_list.insert(tk.END, f"[{mid}] {alias}{tag}{draft}")
            W.model_idx.append(i)

        pos = None
        how = "none"
        if select is not None and select in W.model_idx:
            pos = W.model_idx.index(select)
            how = f"select(cfg[{select}])"
        elif keep_ids:
            # 按 id 找 (下标漂了 / cfg 被改过也能钉住同一条)
            for cid in keep_ids:
                for p, ci in enumerate(W.model_idx):
                    if str(self.cfg["models"][ci].get("id") or "") == cid:
                        pos = p
                        how = f"keep_id({cid})"
                        break
                if pos is not None:
                    break
            if pos is None and W.model_idx:
                # 原来那条已经不在清单里 (被删了) -> 退回第一条
                pos = 0
                how = "keep_id_miss->0(%s)" % ",".join(keep_ids)
        elif W.model_idx and not had_sel:
            # 从来没有过选中项 (初始化/空清单) -> 选第一条
            pos = 0
            how = "first(no prior selection)"
        if pos is not None:
            W.model_list.selection_set(pos)
            W.model_list.see(pos)
            self._on_select_model(W=W)
        self._sel_log(
            "REFRESH ws=%s select=%r pos=%s how=%s cur_before=%s"
            % (getattr(W, "ws_kind", "pool"), select, pos, how,
               cur if cur else None)
            + " | " + self._sel_caller())

    def _build_global_tab(self) -> None:
        f = ttk.Frame(self.nb, padding=0)
        self.nb.add(f, text="全局")

        # 滚动区域 (服务参数 + watchdog + 全局参数)
        outer = ttk.Frame(f)
        outer.pack(fill=tk.BOTH, expand=True)
        canvas = tk.Canvas(outer, borderwidth=0, highlightthickness=0)
        scroll = ttk.Scrollbar(outer, orient=tk.VERTICAL, command=canvas.yview)
        canvas.configure(yscrollcommand=scroll.set)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        inner = ttk.Frame(canvas)
        inner_id = canvas.create_window((0, 0), window=inner, anchor=tk.NW)
        _oi2, _oc2, _f2 = _canvas_sync(canvas, inner_id, inner)
        inner.bind("<Configure>", _oi2)
        canvas.bind("<Configure>", _oc2)
        def _wheel(e):
            delta = -1 * int(e.delta / 120) if e.delta else 0
            if delta == 0:
                if e.num == 4: delta = -1
                elif e.num == 5: delta = 1
            if delta: canvas.yview_scroll(delta, "units")
        canvas.bind("<MouseWheel>", _wheel, add="+")
        canvas.bind("<Button-4>", _wheel, add="+")
        canvas.bind("<Button-5>", _wheel, add="+")
        inner.bind("<MouseWheel>", _wheel, add="+")
        inner.bind("<Button-4>", _wheel, add="+")
        inner.bind("<Button-5>", _wheel, add="+")

        # 顶部保存按钮 (与模型编辑器的保存位置对应) — 右上角
        top_save = ttk.Frame(f)
        top_save.pack(fill=tk.X, pady=(0, 4))
        ttk.Label(top_save, text="提示: 改完点右上角「保存」会写 config.json + router-preset.ini 并触发后台保存",
                  foreground="#666").pack(side=tk.LEFT, padx=4)
        ttk.Button(top_save, text="💾  保存全局配置",
                   command=self._on_save_global).pack(side=tk.RIGHT, padx=4)

        f_content = ttk.Frame(inner, padding=10)
        f_content.pack(fill=tk.X, expand=True)

        box1 = ttk.LabelFrame(f_content, text="服务参数", padding=10)
        box1.pack(fill=tk.X, pady=(0, 8))
        self.var_llama_dir = tk.StringVar()
        self.var_host = tk.StringVar()
        self.var_port = tk.StringVar()
        self.var_max = tk.StringVar()
        self.var_idle = tk.StringVar()
        rows = [
            ("llama.cpp 安装目录", self.var_llama_dir, "dir"),
            ("监听地址 host", self.var_host, None),
            ("监听端口 port", self.var_port, None),
            ("同时驻留模型数 models-max", self.var_max, None),
            ("空闲 N 秒后自动卸载 (默认 3600 = 1 小时)", self.var_idle, None),
        ]
        for i, (label, var, browse) in enumerate(rows):
            ttk.Label(box1, text=label + ":").grid(row=i, column=0, sticky=tk.W, pady=3, padx=(0, 6))
            ttk.Entry(box1, textvariable=var, width=50).grid(row=i, column=1, sticky=tk.EW, pady=3)
            if browse == "dir":
                ttk.Button(box1, text="浏览...",
                           command=lambda v=var: self._browse_dir(v)).grid(row=i, column=2, padx=4)
        box1.columnconfigure(1, weight=1)
        # 按需加载 (默认): 服务起来不加载任何模型, 收到带 model 的请求才加载
        self.var_preload = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            box1, text="开机预加载模型 (不勾 = 按需加载: 收到带 model 的请求才加载那一组)",
            variable=self.var_preload).grid(row=len(rows), column=0, columnspan=3,
                                             sticky=tk.W, pady=(6, 0))
        ttk.Label(box1, text="", foreground="#555").grid(
            row=len(rows), column=3)

        # KVMem 分支程序 (「⚡ KVMem」页跑的就是它; 留空则按目录自动探测)
        box_kvmem = ttk.LabelFrame(
            f_content, text="KVMem-llama.cpp 程序 (「⚡ KVMem」页运行的分支程序)",
            padding=10)
        box_kvmem.pack(fill=tk.X, pady=(0, 8))
        self.var_kvmem_exe = tk.StringVar()
        ttk.Label(box_kvmem, text="程序路径 (llama-kvmem-server.exe):").grid(
            row=0, column=0, sticky=tk.W, pady=3, padx=(0, 6))
        ttk.Entry(box_kvmem, textvariable=self.var_kvmem_exe, width=50).grid(
            row=0, column=1, sticky=tk.EW, pady=3)
        ttk.Button(box_kvmem, text="浏览...",
                   command=lambda: self._browse(self.var_kvmem_exe, "exe")).grid(
            row=0, column=2, padx=4)
        ttk.Button(box_kvmem, text="自动查找",
                   command=self._on_kvmem_autofind_exe).grid(
            row=0, column=3, padx=4)
        self.var_kvmem_exe_used = tk.StringVar(value="当前实际使用: -")
        ttk.Label(box_kvmem, textvariable=self.var_kvmem_exe_used,
                  foreground="#888").grid(row=1, column=0, columnspan=4,
                                          sticky=tk.W, pady=(4, 0))
        ttk.Label(box_kvmem,
                  text="留空 = 按 KVMem 安装目录递归探测最新的 llama-kvmem-server.exe",
                  foreground="#888").grid(row=2, column=0, columnspan=4,
                                          sticky=tk.W)
        box_kvmem.columnconfigure(1, weight=1)
        self._sync_kvmem_exe_label()

        box_wd = ttk.LabelFrame(f_content, text="显存守护 watchdog", padding=10)
        box_wd.pack(fill=tk.X, pady=(0, 8))
        self.var_wd_enabled = tk.BooleanVar(value=False)
        self.var_wd_threshold = tk.StringVar()
        self.var_wd_interval = tk.StringVar()
        self.var_wd_min_idle = tk.StringVar()
        ttk.Checkbutton(box_wd, text="启用 watchdog 守护 (需 nvidia-smi 在 PATH 中)",
                        variable=self.var_wd_enabled).grid(row=0, column=0, columnspan=2, sticky=tk.W, pady=2)
        ttk.Label(box_wd, text="可用显存阈值 (MB):").grid(row=1, column=0, sticky=tk.W, pady=2)
        ttk.Entry(box_wd, textvariable=self.var_wd_threshold, width=12).grid(row=1, column=1, sticky=tk.W, pady=2)
        ttk.Label(box_wd, text="检测间隔 (秒):").grid(row=2, column=0, sticky=tk.W, pady=2)
        ttk.Entry(box_wd, textvariable=self.var_wd_interval, width=12).grid(row=2, column=1, sticky=tk.W, pady=2)
        ttk.Label(box_wd, text="卸载后冷却时间 (秒):").grid(row=3, column=0, sticky=tk.W, pady=2)
        ttk.Entry(box_wd, textvariable=self.var_wd_min_idle, width=12).grid(row=3, column=1, sticky=tk.W, pady=2)
        ttk.Label(box_wd,
                  text="(当可用显存低于阈值时，主动 POST /models/unload 卸载最久未用的模型)",
                  foreground="#888", justify=tk.LEFT).grid(
            row=4, column=0, columnspan=2, sticky=tk.W, pady=(4, 0))

        # 模型自动换挡 (LRU)
        box_mg = ttk.LabelFrame(f_content, text="模型自动换挡 (按加载时间卸载最旧的常驻模型)", padding=8)
        box_mg.pack(fill=tk.X, pady=(0, 8))
        self.var_mg_enabled = tk.BooleanVar(value=True)
        self.var_mg_policy = tk.StringVar(value="ask")
        ttk.Checkbutton(box_mg, text="启用 (有新模型请求且旧模型正在使用时接管切换)",
                        variable=self.var_mg_enabled).grid(row=0, column=0, columnspan=2, sticky=tk.W, pady=2)
        ttk.Label(box_mg, text="旧模型正在生成时:").grid(row=1, column=0, sticky=tk.W, pady=2)
        ttk.Combobox(box_mg, textvariable=self.var_mg_policy, state="readonly", width=14,
                     values=["ask", "wait", "now"]).grid(row=1, column=1, sticky=tk.W, pady=2)
        ttk.Label(box_mg,
                  text=("ask = 弹窗询问 (是=立即卸载 / 否=等它用完 / 取消=本次不处理), "
                        "wait = 直接等它用完, now = 立即卸载"),
                  foreground="#888", justify=tk.LEFT).grid(
            row=2, column=0, columnspan=2, sticky=tk.W, pady=(4, 0))

        # 日志转发 (llama-server 没有日志 HTTP 接口, 远程要看日志只能靠它)
        box_lr = ttk.LabelFrame(f_content, text="日志转发 (让远程监控窗口看到和本地一样的日志)", padding=8)
        box_lr.pack(fill=tk.X, pady=(0, 8))
        self.var_lr_enabled = tk.BooleanVar(value=False)
        self.var_lr_port = tk.StringVar(value="8099")
        ttk.Checkbutton(box_lr,
                        text="启用 (默认关闭; 开启后监听 0.0.0.0, 同网机器的监控窗口可读取本机 router 日志)",
                        variable=self.var_lr_enabled).grid(
            row=0, column=0, columnspan=2, sticky=tk.W, pady=2)
        ttk.Label(box_lr, text="监听端口:").grid(row=1, column=0, sticky=tk.W, pady=2)
        ttk.Entry(box_lr, textvariable=self.var_lr_port, width=12).grid(
            row=1, column=1, sticky=tk.W, pady=2)
        # 防火墙: 不放行的话, 同网机器连这个端口会被**静默丢包** (表现为"超时"),
        # 界面上只会看到"远程日志没连上", 所以必须在这里就能一键放行。
        ttk.Label(box_lr, text="防火墙:").grid(row=2, column=0, sticky=tk.W, pady=2)
        fw_row = ttk.Frame(box_lr)
        fw_row.grid(row=2, column=1, sticky=tk.W, pady=2)
        ttk.Button(fw_row, text="🛡 放行端口",
                   command=self._on_fw_allow).pack(side=tk.LEFT)
        self.var_fw_state = tk.StringVar(value="…")
        ttk.Label(fw_row, textvariable=self.var_fw_state,
                  foreground="#888").pack(side=tk.LEFT, padx=(8, 0))
        ttk.Label(box_lr,
                  text=("远程监控窗口的「监控地址」填 对方IP:API端口 即可; 两边都用这个端口读日志。"
                        "只需在远程勾选一次 + 放行一次, 本机不用动"),
                  foreground="#888", justify=tk.LEFT).grid(
            row=3, column=0, columnspan=2, sticky=tk.W, pady=(4, 0))
        self._refresh_fw_status()

        # 配置文件编辑入口 (手动编辑)
        box_files = ttk.LabelFrame(f_content, text="配置文件 (双击打开手动编辑)", padding=8)
        box_files.pack(fill=tk.X, pady=(0, 8))
        for i, (label, path) in enumerate([
            ("config.json (主配置)", config.CONFIG_PATH),
            ("router-preset.ini (生成的模型预设)", config.preset_path_for(self.cfg)),
            ("user_preferences.json (UI 偏好)", config.PREFS_PATH),
        ]):
            ttk.Label(box_files, text=label + ":").grid(row=i, column=0, sticky=tk.W, pady=2, padx=(0, 6))
            ttk.Label(box_files, text=path, foreground="#0066CC").grid(row=i, column=1, sticky=tk.W, pady=2)
            ttk.Button(box_files, text="📂 打开", width=8,
                       command=lambda p=path: self._open_file(p)).grid(row=i, column=2, padx=4, pady=2)
        box_files.columnconfigure(1, weight=1)

    def _browse_dir(self, var: tk.StringVar) -> None:
        d = filedialog.askdirectory()
        if d:
            var.set(d)

    def _kvmem_probe_cfg(self) -> Dict[str, Any]:
        """按输入框当前(可能未保存)的值拼一份 cfg, 供 find_exe 探测。"""
        probe = dict(self.cfg)
        kv = dict(self.cfg.get("kvmem") or {})
        kv["exe"] = self.var_kvmem_exe.get().strip() if hasattr(self, "var_kvmem_exe") else ""
        probe["kvmem"] = kv
        return probe

    def _sync_kvmem_exe_label(self) -> None:
        """刷新「当前实际使用: …」—— 手填且存在就用手填的, 否则用自动探测结果。"""
        if not hasattr(self, "var_kvmem_exe_used"):
            return
        exe = kvmem.find_exe(self._kvmem_probe_cfg())
        if exe:
            self.var_kvmem_exe_used.set(f"当前实际使用: {exe}")
        else:
            self.var_kvmem_exe_used.set(
                "当前实际使用: 未找到 (请手动「浏览…」选择 llama-kvmem-server.exe)")

    def _on_kvmem_autofind_exe(self) -> None:
        """「自动查找」: 清掉手填路径, 按安装目录探测并把结果填进输入框。"""
        saved = self.var_kvmem_exe.get().strip()
        self.var_kvmem_exe.set("")
        exe = kvmem.find_exe(self._kvmem_probe_cfg())
        self.var_kvmem_exe.set(exe)
        self._sync_kvmem_exe_label()
        if exe:
            self._log(f"KVMem 程序已自动查找: {exe}")
        else:
            self.var_kvmem_exe.set(saved)
            self._sync_kvmem_exe_label()
            messagebox.showwarning(
                "未找到",
                "在 KVMem 安装目录下没找到 llama-kvmem-server.exe，\n"
                "请点「浏览…」手动选择。")

    def _open_file(self, path: str) -> None:
        """用系统默认程序打开文件 (通常是记事本)。"""
        if not os.path.isfile(path):
            messagebox.showerror("文件不存在", f"找不到文件:\n{path}")
            return
        try:
            if sys.platform == "win32":
                os.startfile(path)  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", path])
            else:
                subprocess.Popen(["xdg-open", path])
        except Exception as e:
            messagebox.showerror("打开失败", f"无法打开 {path}\n\n{e}")

    def _on_save_global(self) -> None:
        try:
            self.cfg["llama_dir"] = self.var_llama_dir.get().strip() or r"C:\llama.cpp"
            self.cfg["host"] = self.var_host.get().strip() or "0.0.0.0"
            self.cfg["port"] = int(self.var_port.get().strip() or "18080")
            self.cfg["models_max"] = int(self.var_max.get().strip() or "1")
            self.cfg["sleep_idle_seconds"] = int(self.var_idle.get().strip() or "3600")
            gw = self.cfg.setdefault("gateway", {})
            gw["preload"] = bool(self.var_preload.get())
            kv = self.cfg.setdefault("kvmem", {})
            kv["exe"] = self.var_kvmem_exe.get().strip()
        except ValueError as e:
            messagebox.showerror("错误", f"数值字段不合法: {e}")
            return
        self._sync_kvmem_exe_label()
        try:
            wd = self.cfg.setdefault("watchdog", {})
            wd["enabled"] = bool(self.var_wd_enabled.get())
            wd["free_mb_threshold"] = int(self.var_wd_threshold.get().strip() or "2048")
            wd["check_interval_seconds"] = int(self.var_wd_interval.get().strip() or "10")
            wd["min_idle_seconds"] = int(self.var_wd_min_idle.get().strip() or "0")
        except ValueError as e:
            messagebox.showerror("错误", f"watchdog 数值字段不合法: {e}")
            return
        try:
            mg = self.cfg.setdefault("model_guard", {})
            mg["enabled"] = bool(self.var_mg_enabled.get())
            policy = str(self.var_mg_policy.get() or "ask").strip().lower()
            mg["busy_policy"] = policy if policy in ("ask", "wait", "now") else "ask"
        except ValueError as e:
            messagebox.showerror("错误", f"模型自动换挡字段不合法: {e}")
            return
        try:
            lr = self.cfg.setdefault("log_relay", {})
            lr["enabled"] = bool(self.var_lr_enabled.get())
            port = int(self.var_lr_port.get().strip() or "8099")
            if not (1 <= port <= 65535):
                raise ValueError(f"端口范围 1-65535: {port}")
            lr["port"] = port
        except ValueError as e:
            messagebox.showerror("错误", f"日志转发字段不合法: {e}")
            return
        self._save_config()
        self._start_log_relay()          # 立刻按新配置重启/关闭转发
        self._refresh_fw_status()        # 端口可能改了, 放行状态跟着变
        self._log("全局配置已保存")

    def _build_prefs_tab(self) -> None:
        f = ttk.Frame(self.nb, padding=0)
        self.nb.add(f, text="偏好")
        PrefsTab(f, on_change=self._on_prefs_changed).pack(fill=tk.BOTH, expand=True)

    def _on_prefs_changed(self) -> None:
        """偏好页常用区有增删/移动 -> 立即按新顺序重建参数面板。"""
        for p in self._param_panels:
            p.reload()

    def _refresh_all(self) -> None:
        self.var_llama_dir.set(self.cfg.get("llama_dir", r"C:\llama.cpp"))
        self.var_host.set(self.cfg.get("host", "0.0.0.0"))
        self.var_port.set(str(self.cfg.get("port", 8080)))
        self.var_max.set(str(self.cfg.get("models_max", 1)))
        self.var_idle.set(str(self.cfg.get("sleep_idle_seconds", 3600)))
        if hasattr(self, "var_preload"):
            self.var_preload.set(bool((self.cfg.get("gateway") or {}).get(
                "preload", False)))
        if hasattr(self, "var_kvmem_exe"):
            self.var_kvmem_exe.set(
                str((self.cfg.get("kvmem") or {}).get("exe") or ""))
            self._sync_kvmem_exe_label()
        wd = self.cfg.get("watchdog", {}) or {}
        self.var_wd_enabled.set(bool(wd.get("enabled", False)))
        self.var_wd_threshold.set(str(wd.get("free_mb_threshold", 2048)))
        self.var_wd_interval.set(str(wd.get("check_interval_seconds", 10)))
        self.var_wd_min_idle.set(str(wd.get("min_idle_seconds", 0)))
        mg = self.cfg.get("model_guard", {}) or {}
        self.var_mg_enabled.set(bool(mg.get("enabled", True)))
        policy = str(mg.get("busy_policy", "ask")).strip().lower()
        self.var_mg_policy.set(policy if policy in ("ask", "wait", "now") else "ask")
        lr = self.cfg.get("log_relay", {}) or {}
        self.var_lr_enabled.set(bool(lr.get("enabled", False)))
        self.var_lr_port.set(str(lr.get("port", 8099)))
        self._refresh_fw_status()
        self._refresh_model_list(select=0 if self.cfg["models"] else None)
        self._refresh_status()

    def _save_config(self) -> None:
        # 两个工作区的面板可能同时持有同一条记录的参数 (作用域有交集),
        # 落盘前都 flush 一遍, 再互相 reload, 免得一边的旧值盖掉另一边刚改的。
        for p in list(self._param_panels):
            p.save_all()
        config.save(self.cfg)
        skipped: list = []
        preset_path = generate_preset.generate(self.cfg, skipped=skipped)
        self._log(f"已写入 {config.CONFIG_PATH} 并重新生成 {preset_path}")
        if skipped:
            # 这些 key 本机 llama-server 不认识。若照写会让整个 preset 解析失败,
            # 所有模型都加载不了 —— 所以生成时跳过并在此提醒用户。
            self._log("注意: 本机 llama-server 不支持以下参数, 已跳过未写入: "
                      + ", ".join(skipped))
        for p in list(self._param_panels):
            p.reload()

    def _log(self, msg: str) -> None:
        self.log.insert(tk.END, msg + "\n")
        self.log.see(tk.END)
        self.status_var.set(msg[:80])

    def _run_bg(self, fn, arg, hint: str) -> None:
        self.status_var.set(hint)

        def task():
            try:
                if arg is None:
                    ok, msg = fn()
                else:
                    ok, msg = fn(arg)
                self.after(0, lambda: self._on_bg_done(ok, msg))
            except Exception as e:
                self.after(0, lambda: self._on_bg_done(False, f"异常: {e}"))

        threading.Thread(target=task, daemon=True).start()

    def _on_bg_done(self, ok: bool, msg: str) -> None:
        self._log(("OK  " if ok else "FAIL") + " | " + msg)
        if not ok:
            messagebox.showerror("失败", msg)
        self._refresh_status()

    def _refresh_status(self) -> None:
        # 网关在后端切换时会回写 serve_mode, 所以以磁盘为准 (内存里的
        # self.cfg 可能还是切之前的老值)。
        try:
            cur = config.load()
        except Exception:
            cur = self.cfg
        # 「按需加载」下服务起来不加载任何模型 —— 这时要说「未加载」,
        # 别拿 serve_mode 假装正在跑着某一组。
        live = self._gateway_active()
        mode = service.serve_mode_of(cur)
        if mode == "kvmem":
            act = kvmem.active_model(cur) or {}
            want = f"KVMem 档位 {act.get('alias') or act.get('id') or '?'}"
        elif mode == "strata":
            act = strata.active_model(cur) or {}
            want = f"Strata 档位 {act.get('alias') or act.get('id') or '?'}"
        else:
            want = "主池"
        preload = bool((cur.get("gateway") or {}).get("preload", False))
        if live:
            # 网关报的是组 id (kvmem), 这里翻成中文名, 和另几页口径一致
            cn = {"pool": "主池", "kvmem": "KVMem",
                  "strata": "Strata"}.get(str(live.get("active")),
                                             str(live.get("active")))
            tag = f"运行时 {cn} / {live.get('active_model') or '?'}"
        elif preload:
            tag = f"默认 {want}"
        else:
            tag = f"未加载（按需加载，默认 {want}）"
        self.svc_state_var.set(f"主服务: {service.status()}   {tag}   "
                               f"端口 {int(cur.get('port', 8080))}")
        self._refresh_wd_status()

    def _gateway_active(self) -> Optional[Dict[str, Any]]:
        """问网关现在到底加载了什么 (一次 2s 超时的本地请求, 失败就当没有)。

        不用这个而是读 serve_mode 的话, 「按需加载」时状态栏会一直显示
        「运行时 主池」而其实一个模型都没加载。
        """
        try:
            port = int(self.cfg.get("port", 18080) or 18080)
            with httpx.open_url(f"http://127.0.0.1:{port}/health",
                                        timeout=2) as r:
                obj = json.loads(r.read().decode("utf-8", "replace"))
            if obj.get("active"):
                return obj
        except Exception:
            pass
        return None

    def _refresh_wd_status(self) -> None:
        if hasattr(self, "wd_state_var"):
            self.wd_state_var.set(f"watchdog: {service.watchdog_status()}")

    def _poll_status(self) -> None:
        self._refresh_status()
        self._poll_conflicts()
        self.after(5000, self._poll_status)

    # ------------------------------------------------------------------
    # 端口冲突询问 (服务端写的 .req.json -> 这里弹窗 -> 回写 .ans.json)
    # ------------------------------------------------------------------
    def _poll_conflicts(self) -> None:
        """看服务端有没有在问「占着内部口的那个进程怎么处理」。"""
        try:
            import conflict_ask
        except Exception:                                # noqa: BLE001
            return
        try:
            todo = conflict_ask.pending()
        except Exception:                                # noqa: BLE001
            return
        for req in todo:
            token = str(req.get("token") or "")
            if not token or token in getattr(self, "_conflict_asked", ()):
                continue
            if not hasattr(self, "_conflict_asked"):
                self._conflict_asked = set()
            self._conflict_asked.add(token)
            # 弹窗要在 Tk 主线程里做 (本函数就是 after 回调, 已经在主线程)
            self.after(0, lambda r=req, t=token: self._ask_conflict(r, t))

    def _ask_conflict(self, req: dict, token: str) -> None:
        """弹窗问用户。**默认「等用完」** —— 绝不替用户决定杀掉正在用的模型。"""
        import conflict_ask
        port = req.get("port")
        holders = req.get("holders") or []
        want_cn = req.get("want_group_cn") or req.get("want_group") or ""
        want_model = req.get("want_model") or ""
        lines = [f"网关要起【{want_cn}"
                 + (f" / {want_model}" if want_model else "")
                 + f"】，但内部口 {port} 已经被占着：", ""]
        for h in holders[:4]:
            tag = "（认得出来）" if h.get("mark") else "（不认得）"
            lines.append(f"  pid {h.get('pid')} {tag}")
            cmd = (h.get("cmd") or "").strip()
            if cmd:
                lines.append(f"    {cmd[:150]}")
        body = "\n".join(lines)
        dlg = tk.Toplevel(self)
        dlg.title("端口冲突 — 需要你决定")
        dlg.transient(self)
        dlg.resizable(True, False)
        ttk.Label(dlg, text=body, justify=tk.LEFT, wraplength=560).pack(
            padx=14, pady=(12, 6))
        ttk.Label(dlg, text="正在进行的对话会**继续**，网关排队等它结束再换。",
                  foreground="#666", justify=tk.LEFT).pack(padx=14)
        frm = ttk.Frame(dlg)
        frm.pack(padx=14, pady=12, fill=tk.X)

        def _pick(choice: str) -> None:
            try:
                conflict_ask.answer(token, choice)
                self._log(f"模型换挡 | 端口 {port} 冲突: 你选了「{choice}」")
            except Exception as e:                       # noqa: BLE001
                self._log(f"端口冲突答复失败: {e}")
            dlg.destroy()

        ttk.Button(frm, text="等它用完（推荐）",
                   command=lambda: _pick(conflict_ask.WAIT)).pack(
                       side=tk.LEFT, padx=(0, 8))
        ttk.Button(frm, text="强行结束，立刻换",
                   command=lambda: _pick(conflict_ask.FORCE)).pack(
                       side=tk.LEFT, padx=(0, 8))
        ttk.Button(frm, text="这次不换",
                   command=lambda: _pick(conflict_ask.REJECT)).pack(side=tk.LEFT)
        dlg.grab_set()


def main() -> None:
    try:
        app = App()
        app.mainloop()
    except Exception:
        import traceback
        log_dir = os.path.join(config.app_dir(), "logs")
        try:
            os.makedirs(log_dir, exist_ok=True)
            log_path = os.path.join(log_dir, "ui_crash.log")
            with open(log_path, "w", encoding="utf-8") as f:
                traceback.print_exc(file=f)
        except Exception:
            log_path = "(写入 logs\\ui_crash.log 失败)"
        traceback.print_exc()
        print(f"\n[ERROR] UI crashed. Traceback saved to: {log_path}", flush=True)
        try:
            input("Press Enter to exit...")
        except Exception:
            pass
        raise


if __name__ == "__main__":
    main()
