"""llama.cpp Router 可视化配置工具 (Tkinter)

四个标签页：
  1. 服务    安装/卸载/启停/状态/前台运行 + 操作日志
  2. 模型    增删改查模型条目 + 草稿模型 (MTP/DFlash) + 参数子表
  3. 全局    llama_dir/host/port/... + 全局参数开关表
  4. 偏好    自定义哪些是常用参数 + 自定义额外参数

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
import tkinter as tk
import urllib.request
import json
from collections import deque
from tkinter import filedialog, messagebox, ttk
from typing import Any, Callable, Dict, List, Optional

import config
import generate_preset
import service
import startup_check

APP_TITLE = "llama.cpp Router 管理 v3"


# =========================================================================
# 帮助窗口
# =========================================================================
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
        self._inner.bind("<Configure>",
                         lambda _e: self._canvas.configure(scrollregion=self._canvas.bbox("all")))
        self._canvas.bind("<Configure>",
                          lambda e: self._canvas.itemconfig(self._inner_id, width=e.width))
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

        common_set = set(config.common_keys())
        applicable = []
        for p in config.PARAM_SCHEMA:
            if p["scope"] not in (self.scope, "both"):
                continue
            # model scope 下排除 spec-draft-* (草稿区有专有输入框)
            if self.scope == "model" and p["key"].startswith("spec-draft-"):
                continue
            applicable.append(p)
        for p in config.extra_params():
            if p.get("scope", "model") not in (self.scope, "both", "global"):
                continue
            applicable.append(p)

        common_order = [k for k in config.common_keys()
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
    def __init__(self, master: tk.Tk) -> None:
        super().__init__(master, padding=10)
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
        ttk.Label(cand_box, text="所有参数 (勾选加入常用):").pack(anchor=tk.W)
        self.cand_tree = ttk.Treeview(cand_box, columns=("scope", "category"),
                                      show="tree headings", selectmode="browse", height=18)
        self.cand_tree.heading("#0", text="参数 / 标签")
        self.cand_tree.heading("scope", text="作用域")
        self.cand_tree.heading("category", text="分类")
        self.cand_tree.column("scope", width=80, anchor=tk.W)
        self.cand_tree.column("category", width=70, anchor=tk.W)
        cand_sb = ttk.Scrollbar(cand_box, orient=tk.VERTICAL, command=self.cand_tree.yview)
        self.cand_tree.configure(yscrollcommand=cand_sb.set)
        self.cand_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        cand_sb.pack(side=tk.RIGHT, fill=tk.Y)

        mid = ttk.Frame(body)
        mid.pack(side=tk.LEFT, fill=tk.Y, padx=4)
        ttk.Button(mid, text="<< 加入", command=self._add_to_common).pack(pady=2)
        ttk.Button(mid, text="移出 >>", command=self._remove_from_common).pack(pady=2)
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
        common = prefs.get("common_keys", [])
        for iid in self.cand_tree.get_children():
            self.cand_tree.delete(iid)
        for iid in self.sel_tree.get_children():
            self.sel_tree.delete(iid)
        for p in config.PARAM_SCHEMA:
            if p["key"] in common:
                self.sel_tree.insert("", tk.END, iid=p["key"], text=p["key"],
                                     values=(p.get("label", ""),))
            else:
                self.cand_tree.insert("", tk.END, iid=p["key"], text=p["key"],
                                      values=(p.get("label", ""), p.get("scope", ""), p.get("category", "")))

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
class MonitorWindow(tk.Toplevel):
    """实时显示 llama-server 日志和模型状态。

    左半: router.out.log + router.err.log 的实时 tail (颜色高亮)
    右半: 拉 /v1/models + /health 端点, 显示每个模型状态/最后生成速度
    """

    def __init__(self, master: tk.Tk, host: str = "127.0.0.1", port: int = 8080) -> None:
        super().__init__(master)
        self.title("llama.cpp Router - 实时监控")
        self.geometry("1200x720")
        self.minsize(900, 500)
        self._host = host
        self._port = port
        self._stop = False
        self._last_log_pos: Dict[str, int] = {}  # path -> file position
        self._log_buffer: Dict[str, deque] = {
            "out": deque(maxlen=2000),
            "err": deque(maxlen=1000),
        }
        # 解析后的最后生成速度 (model_id -> {tg, tg_3s, ts})
        self._last_speed: Dict[str, Dict[str, Any]] = {}
        # 日志是否自动跟跳 (True=跟到最新, False=用户在看历史, 不自动滚)
        self._follow_tail = True
        self._build_ui()
        # 启动后台线程
        self._log_thread = threading.Thread(target=self._tail_loop, daemon=True)
        self._http_thread = threading.Thread(target=self._http_loop, daemon=True)
        self._log_thread.start()
        self._http_thread.start()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        # UI 刷新
        self.after(300, self._refresh_ui)

    def _build_ui(self) -> None:
        # 顶部: 标题 + 按钮
        top = ttk.Frame(self, padding=(10, 6))
        top.pack(fill=tk.X)
        ttk.Label(top, text="📊 实时监控 (类似直接命令行调用看到的输出)",
                  font=("Segoe UI", 11, "bold")).pack(side=tk.LEFT)
        ttk.Button(top, text="清空日志", command=self._clear_logs).pack(side=tk.RIGHT, padx=2)
        ttk.Button(top, text="🔄 立即刷新", command=self._force_follow).pack(side=tk.RIGHT, padx=2)
        ttk.Button(top, text="🔧 重读日志", command=self._reload_logs).pack(side=tk.RIGHT, padx=2)
        ttk.Button(top, text="🔁 重启服务", command=self._restart_service).pack(side=tk.RIGHT, padx=2)

        # 状态条: 健康 + 最近速度 + 模型列表 (单行紧凑)
        status_bar = ttk.Frame(self, padding=(10, 2))
        status_bar.pack(fill=tk.X, padx=8, pady=(0, 4))
        self.health_var = tk.StringVar(value="健康: ?")
        self.health_lbl = ttk.Label(status_bar, textvariable=self.health_var,
                                    font=("Segoe UI", 9), foreground="#0066CC")
        self.health_lbl.pack(side=tk.LEFT, padx=(0, 16))
        ttk.Separator(status_bar, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=4)
        ttk.Label(status_bar, text="模型:", font=("Segoe UI", 9)
                  ).pack(side=tk.LEFT, padx=(4, 4))
        self.models_strip_frame = ttk.Frame(status_bar)
        self.models_strip_frame.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=4)

        # 主区域: 日志拉满 (无横向分割)
        log_box = ttk.Frame(self)
        log_box.pack(fill=tk.BOTH, expand=True, padx=8, pady=4)
        ttk.Label(log_box, text="📜 实时日志 (router.out.log + router.err.log)",
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
        self._init_log_tags()
        self.log_text.configure(state=tk.DISABLED)

        # 滚动时检查是否在最底端, 不在最底端则暂停自动跟跳
        def _on_scroll(_e=None):
            try:
                # yview 返回 (top, bottom) 比例, bottom=1.0 表示在底端
                top, bottom = self.log_text.yview()
                at_bottom = bottom >= 0.999
            except Exception:
                at_bottom = True
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
        # 滚轮 + 拖动滚动条 + 键盘
        self.log_text.bind("<MouseWheel>", _on_scroll, add="+")
        self.log_text.bind("<Button-4>", _on_scroll, add="+")
        self.log_text.bind("<Button-5>", _on_scroll, add="+")
        self.log_text.bind("<KeyPress>", _on_scroll, add="+")
        ysb.config(command=lambda *a: (self.log_text.yview(*a), _on_scroll()))
        xsb.config(command=lambda *a: (self.log_text.xview(*a), _on_scroll()))

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
        self.log_text.configure(state=tk.NORMAL)
        self.log_text.delete("1.0", tk.END)
        self.log_text.configure(state=tk.DISABLED)
        self._last_log_pos.clear()

    def _reload_logs(self) -> None:
        """强制从头重读日志 (用于 NSSM 文件句柄卡住的恢复)。"""
        self._last_log_pos.clear()
        self._log_buffer["out"].clear()
        self._log_buffer["err"].clear()
        self._refresh_ui()
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
        self.destroy()

    # ----------------- 后台: 日志 tail -----------------
    def _log_paths(self) -> List[str]:
        log_dir = os.path.join(config.app_dir(), "logs")
        out_path = os.path.join(log_dir, "router.out.log")
        err_path = os.path.join(log_dir, "router.err.log")
        return [out_path, err_path]

    def _tail_loop(self) -> None:
        """后台线程: 持续读日志新内容, 解析生成速度, 放入 buffer。

        文件被 NSSM 轮转时 (router.out.log -> router.out-{ts}.log) 大小会缩小,
        此时 last_pos > new_size, 需要从 0 开始重新读。
        """
        import time as _time
        while not self._stop:
            try:
                for path in self._log_paths():
                    if not os.path.isfile(path):
                        continue
                    try:
                        # 先看文件大小, 处理轮转/截断的情况
                        try:
                            cur_size = os.path.getsize(path)
                        except OSError:
                            continue
                        last_pos = self._last_log_pos.get(path, 0)
                        # 文件被截断/轮转了 -> 从头开始
                        if last_pos > cur_size:
                            last_pos = 0
                            self._last_log_pos[path] = 0
                        with open(path, "r", encoding="utf-8", errors="replace") as f:
                            f.seek(last_pos)
                            new_lines = f.readlines()
                            self._last_log_pos[path] = f.tell()
                    except Exception:
                        continue
                    if not new_lines:
                        continue
                    buf = self._log_buffer["out"] if path.endswith(".out.log") else self._log_buffer["err"]
                    for line in new_lines:
                        line = line.rstrip("\n")
                        buf.append(line)
                        # 解析 slot 速度行: "n_gen =    354, tg =  58.64 t/s, tg_3s =  60.76 t/s"
                        m = re.search(r"n_gen\s*=\s*(\d+).*?tg\s*=\s*([\d.]+)\s*t/s.*?tg_3s\s*=\s*([\d.]+)", line)
                        if m:
                            self._last_speed["__latest__"] = {
                                "n_gen": int(m.group(1)),
                                "tg": float(m.group(2)),
                                "tg_3s": float(m.group(3)),
                                "ts": _time.time(),
                            }
            except Exception:
                pass
            _time.sleep(0.3)

    # ----------------- 后台: HTTP 轮询 -----------------
    def _http_loop(self) -> None:
        import time as _time
        while not self._stop:
            try:
                base = f"http://{self._host}:{self._port}"
                # /health
                try:
                    with urllib.request.urlopen(f"{base}/health", timeout=2) as r:
                        health = json.loads(r.read().decode("utf-8"))
                except Exception as e:
                    health = {"status": f"无法连接: {e}"}
                # /v1/models
                try:
                    with urllib.request.urlopen(f"{base}/v1/models", timeout=2) as r:
                        models_data = json.loads(r.read().decode("utf-8"))
                except Exception:
                    models_data = {"data": []}
                self._http_state = {
                    "health": health,
                    "models": models_data.get("data", []),
                    "ts": _time.time(),
                }
            except Exception:
                self._http_state = {"health": {"status": "异常"}, "models": [], "ts": 0}
            _time.sleep(2.0)

    # ----------------- UI 刷新 -----------------
    def _refresh_ui(self) -> None:
        if self._stop:
            return
        try:
            # 日志 buffer -> Text
            self._render_logs()
            # 状态 -> 右侧卡片
            self._render_status()
        except Exception as e:
            self.stat_var.set(f"渲染异常: {e}")
        self.after(500, self._refresh_ui)

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
        out_lines = list(self._log_buffer["out"])
        err_lines = list(self._log_buffer["err"])
        if not out_lines and not err_lines:
            return
        # 记录删除前的滚动位置 (fractional 0.0-1.0), 删除+重新插入后恢复
        # 否则 yview 会被重置到 0.0, 导致用户向上查看时跳到顶
        try:
            saved_y = self.log_text.yview()[0] if self.log_text.yview()[1] > 0 else 0.0
        except Exception:
            saved_y = 0.0

        self.log_text.configure(state=tk.NORMAL)
        # 全量重绘 (buffer 已经限长 2000 行, 可接受)
        self.log_text.delete("1.0", tk.END)
        all_lines: List[tuple] = []  # (kind, line)
        # 先错误 (红) 再正常输出
        for ln in err_lines[-200:]:
            all_lines.append(("err", ln))
        for ln in out_lines[-800:]:
            all_lines.append(("out", ln))
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
        else:
            try:
                self.log_text.yview_moveto(saved_y)
            except Exception:
                pass
        self.log_text.configure(state=tk.DISABLED)

    def _render_status(self) -> None:
        """更新状态条 (模型标签原地更新, 避免 destroy/recreate 导致 tooltip 闪烁)。"""
        st = getattr(self, "_http_state", None)
        if not st:
            return
        health = st.get("health", {})
        hstatus = health.get("status", "?")
        hcolor = "#00aa00" if hstatus == "ok" else "#cc0000"
        self.health_var.set(f"健康: {hstatus}")
        self.health_lbl.configure(foreground=hcolor)
        models = st.get("models", [])
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
            status_obj = m.get("status", {})
            status = status_obj.get("value", "?")
            if status == "loaded":
                stxt = "●"
                scolor = "#00aa00"
            elif status == "unloaded":
                stxt = "○"
                scolor = "#888"
            elif status == "loading":
                stxt = "◐"
                scolor = "#cc6600"
            else:
                stxt = "?"
                scolor = "#cc6600"
            label_text = f"{stxt} {mid}"
            tip = self._build_model_tooltip(m, latest)
            lbl = existing.get(mid)
            if lbl is None:
                lbl = tk.Label(self.models_strip_frame,
                               cursor="question_arrow", padx=6, pady=1)
                lbl.pack(side=tk.LEFT, padx=2)
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

    def _build_model_tooltip(self, m: dict, latest: dict) -> str:
        """构造模型 tooltip 文本 (多行)。"""
        lines = []
        mid = m.get("id", "?")
        lines.append(f"模型: {mid}")
        aliases = m.get("aliases", [])
        if aliases:
            lines.append(f"别名: {', '.join(aliases)}")
        status_obj = m.get("status", {})
        status = status_obj.get("value", "?")
        lines.append(f"状态: {status}")
        if status == "loaded":
            args = status_obj.get("args", [])
            kvs = {}
            for i, a in enumerate(args):
                if a.startswith("--") and i + 1 < len(args) and not args[i+1].startswith("--"):
                    kvs[a[2:]] = args[i+1]
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
class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("1180x740")
        self.minsize(1000, 640)
        self.cfg: Dict[str, Any] = config.load()
        self._choices = config.list_kinds_choices()
        self._param_panels: List[ParamPanel] = []
        self._build_ui()
        self._refresh_all()
        self._refresh_env()
        self._poll_status()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        # 首次运行向导 (空配置时)
        self.after(500, self._first_run_wizard)

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
            self.destroy()

    def _build_ui(self) -> None:
        top = ttk.Frame(self, padding=(8, 6))
        top.pack(fill=tk.X)
        ttk.Label(top, text="llama.cpp Router 管理",
                  font=("Segoe UI", 12, "bold")).pack(side=tk.LEFT)
        ttk.Button(top, text="❓ 帮助", command=self._on_help).pack(side=tk.RIGHT, padx=2)

        self.nb = ttk.Notebook(self)
        self.nb.pack(fill=tk.BOTH, expand=True, padx=8, pady=4)
        # 标签顺序: 服务(0) / 全局(1) / 模型(2) / 偏好(3) / 环境(4)
        self._build_service_tab()
        self._build_global_tab()
        self._build_models_tab()
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
        # 标签顺序: 服务(0) / 全局(1) / 模型(2) / 偏好(3) / 环境(4)
        if current == 4:  # 环境
            self._refresh_env()
        elif current in (1, 2):  # 全局 / 模型
            for p in self._param_panels:
                p.reload()

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
            "解压到任意目录 (如 C:\\llama.cpp)，然后到「全局」页改 llama_dir\n"
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
                "改完配置后点「保存并重启服务」即可生效。\n"
                "提示: 安装/卸载/启停都需要管理员权限。")
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
        MonitorWindow(self, host="127.0.0.1", port=port)

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
        # 顶部橙色提示条 (强调要点击下方「新增」按钮)
        top_bar = tk.Frame(f, bg="#FFA500", height=36)
        top_bar.pack(fill=tk.X, side=tk.TOP, pady=(0, 8))
        top_bar.pack_propagate(False)
        tk.Label(top_bar, text="  👉 在左侧点「新增」添加你的 .gguf 模型, 或点击「删除」移除已有模型",
                 bg="#FFA500", fg="white", font=("Segoe UI", 10, "bold"),
                 anchor=tk.W).pack(side=tk.LEFT, fill=tk.Y)

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
        ]:
            ttk.Button(lb_btns, text=label, command=cmd).pack(
                side=tk.LEFT, expand=True, fill=tk.X, padx=1)
        # 分隔线
        ttk.Separator(left, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=(0, 6))
        # 编辑器 (基础信息 + 推测解码) 在左侧下面
        self._build_model_editor(left)

        # ===== 右侧 (模型参数 - 占满全部高度) =====
        right = ttk.Frame(paned)
        paned.add(right, weight=3)
        self.model_right = right
        self._build_model_params_panel(right)

    def _build_model_editor(self, master: ttk.Frame) -> None:
        self._editor_master = master  # 保存引用供 _toggle_draft_box 用
        # 顶部醒目保存条 (改动后容易注意到)
        top_bar = tk.Frame(master, bg="#FFA500", height=32)
        top_bar.pack(fill=tk.X, padx=4, pady=(0, 4), side=tk.TOP)
        top_bar.pack_propagate(False)
        tk.Label(top_bar, text="⚠  修改后必须点「保存」",
                 bg="#FFA500", fg="black", font=("", 9, "bold")).pack(side=tk.LEFT, padx=10, pady=4)
        tk.Button(top_bar, text="💾  保存",
                  bg="#FF6B35", fg="white", font=("", 9, "bold"),
                  activebackground="#FF8C5A", activeforeground="white",
                  relief=tk.RAISED, bd=2, padx=12, pady=2,
                  command=self._on_save_model).pack(side=tk.RIGHT, padx=10, pady=2)

        # ===== 基础信息 (2 列紧凑布局) =====
        box1 = ttk.LabelFrame(master, text="基础信息", padding=6)
        box1.pack(fill=tk.X, padx=4, pady=(0, 4))
        self.var_id = tk.StringVar()
        self.var_alias = tk.StringVar()
        self.var_model = tk.StringVar()
        self.var_mmproj = tk.StringVar()
        self.var_enabled = tk.BooleanVar(value=True)
        self.var_base_url = tk.StringVar()   # API 端点 (留空表示用本机 llama.cpp)
        self.var_api_key = tk.StringVar()    # API 密钥 (留空表示无)
        # 2 列布局: row 0 是 ID/alias, row 1 是 model/mmproj, row 2 是 base_url/api_key
        # (row, col, label, var, browse_ft, kind)
        info_rows = [
            (0, 0, "ID*",          self.var_id,      None,  "text"),
            (0, 2, "别名 alias",   self.var_alias,   None,  "text"),
            (1, 0, "主模型 .gguf*", self.var_model,   "gguf", "text"),
            (1, 2, "mmproj",       self.var_mmproj,  "gguf", "text"),
            (2, 0, "base_url",     self.var_base_url, None,  "text"),
            (2, 2, "api_key",      self.var_api_key,  None,  "password"),
        ]
        for row, col, label, var, browse_ft, kind in info_rows:
            sub = ttk.Frame(box1)
            sub.grid(row=row, column=col, sticky=tk.EW, padx=(0, 8), pady=2)
            ttk.Label(sub, text=label + ":").pack(side=tk.LEFT)
            show = "*" if kind == "password" else ""
            entry = ttk.Entry(sub, textvariable=var, show=show)
            entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(4, 0))
            if browse_ft:
                ttk.Button(sub, text="…",
                           command=lambda v=var: self._browse(v, browse_ft)).pack(side=tk.LEFT, padx=(2, 0))
        # row 3: 启用 + 保存提示
        ttk.Checkbutton(box1, text="启用 (未启用则不加入 preset.ini)",
                        variable=self.var_enabled).grid(row=3, column=0, columnspan=4, sticky=tk.W, pady=(2, 0))
        # 2 列等宽扩展
        box1.columnconfigure(0, weight=1)
        box1.columnconfigure(1, weight=0)
        box1.columnconfigure(2, weight=1)
        box1.columnconfigure(3, weight=0)

        # ===== 推测解码 (默认折叠, 用按钮展开) =====
        # 占 0 空间, 给参数面板更多位置
        draft_toggle_bar = ttk.Frame(master)
        draft_toggle_bar.pack(fill=tk.X, padx=4, pady=(0, 2))
        self._draft_collapsed = tk.BooleanVar(value=False)
        ttk.Checkbutton(draft_toggle_bar,
                        text="▾  推测解码 (Draft Model)  —— MTP / DFlash (点此折叠)",
                        variable=self._draft_collapsed,
                        command=self._toggle_draft_box
                        ).pack(side=tk.LEFT)
        self.draft_box = ttk.LabelFrame(master, text="推测解码 (Draft Model)", padding=6)
        self.var_draft_model = tk.StringVar()
        self.var_draft_type = tk.StringVar()
        self.var_draft_n_max = tk.StringVar(value="3")
        self.var_draft_n_min = tk.StringVar(value="0")
        self.var_draft_p_split = tk.StringVar(value="0.1")
        self.var_draft_p_min = tk.StringVar(value="0.0")
        self.var_draft_ngl = tk.StringVar(value="auto")
        # 第 0 行: 草稿模型路径
        ttk.Label(self.draft_box, text="草稿模型 .gguf:").grid(row=0, column=0, sticky=tk.W, pady=3, padx=(0, 6))
        ttk.Entry(self.draft_box, textvariable=self.var_draft_model).grid(row=0, column=1, columnspan=2, sticky=tk.EW, pady=3)
        ttk.Button(self.draft_box, text="浏览...",
                   command=lambda: self._browse(self.var_draft_model, "gguf")).grid(row=0, column=3, padx=4)
        # 第 1 行: 推测类型 + 提示
        ttk.Label(self.draft_box, text="推测类型:").grid(row=1, column=0, sticky=tk.W, pady=3, padx=(0, 6))
        ttk.Combobox(self.draft_box, textvariable=self.var_draft_type, width=14,
                     values=self._choices["spec-type"]).grid(row=1, column=1, sticky=tk.W, pady=3)
        ttk.Label(self.draft_box, text="(留空按文件名自动猜)",
                  foreground="#888").grid(row=1, column=2, columnspan=2, padx=4, sticky=tk.W)
        # 第 2 行: n-max / n-min
        ttk.Label(self.draft_box, text="n-max:").grid(row=2, column=0, sticky=tk.W, pady=3, padx=(0, 6))
        ttk.Spinbox(self.draft_box, textvariable=self.var_draft_n_max, width=8, from_=1, to=16).grid(
            row=2, column=1, sticky=tk.W, pady=3)
        ttk.Label(self.draft_box, text="n-min:").grid(row=2, column=2, sticky=tk.W, pady=3, padx=(8, 6))
        ttk.Spinbox(self.draft_box, textvariable=self.var_draft_n_min, width=8, from_=0, to=16).grid(
            row=2, column=3, sticky=tk.W, pady=3)
        # 第 3 行: p-split / p-min
        ttk.Label(self.draft_box, text="p-split:").grid(row=3, column=0, sticky=tk.W, pady=3, padx=(0, 6))
        ttk.Spinbox(self.draft_box, textvariable=self.var_draft_p_split, width=8, from_=0.0, to=1.0, increment=0.05).grid(
            row=3, column=1, sticky=tk.W, pady=3)
        ttk.Label(self.draft_box, text="p-min:").grid(row=3, column=2, sticky=tk.W, pady=3, padx=(8, 6))
        ttk.Spinbox(self.draft_box, textvariable=self.var_draft_p_min, width=8, from_=0.0, to=1.0, increment=0.05).grid(
            row=3, column=3, sticky=tk.W, pady=3)
        # 第 4 行: ngl
        ttk.Label(self.draft_box, text="草稿 GPU 层数:").grid(row=4, column=0, sticky=tk.W, pady=3, padx=(0, 6))
        ttk.Combobox(self.draft_box, textvariable=self.var_draft_ngl, width=14,
                     values=self._choices["spec-draft-ngl"]).grid(row=4, column=1, sticky=tk.W, pady=3)
        ttk.Label(self.draft_box, text="(auto 自动, 0 纯 CPU, 99 全 GPU)",
                  foreground="#888").grid(row=4, column=2, columnspan=2, padx=4, sticky=tk.W)
        self.draft_box.columnconfigure(1, weight=1)
        # 默认展开: 立刻 pack 一次 (如果默认 False, 用户需要在 _toggle_draft_box 手动调)
        if not self._draft_collapsed.get():
            self.draft_box.pack(fill=tk.X, padx=4, pady=(0, 4))

    def _build_model_params_panel(self, master: ttk.Frame) -> None:
        """右侧面板: 仅显示模型参数 (占满全部高度)。"""
        box3 = ttk.LabelFrame(master, text="模型参数 (常用 / 扩展切换)", padding=6)
        box3.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)
        self.model_param_panel = ParamPanel(
            box3, scope="model",
            get=lambda k: self._get_model_param(k),
            set_=lambda k, v: self._set_model_param(k, v),
            refresh_outer=lambda: None,
        )
        self._param_panels.append(self.model_param_panel)
        self.model_param_panel.pack(fill=tk.BOTH, expand=True)

    def _toggle_draft_box(self) -> None:
        """展开/折叠推测解码面板。"""
        if self._draft_collapsed.get():
            # 折叠 -> 移除
            self.draft_box.pack_forget()
        else:
            # 展开 -> 插在 toggle bar 和 box3 (模型参数) 之间
            for w in self._editor_master.winfo_children():
                if isinstance(w, ttk.LabelFrame) and w.cget("text") == "模型参数":
                    self.draft_box.pack(fill=tk.X, padx=4, pady=(0, 4), before=w)
                    return
            # 找不到就 fallback
            self.draft_box.pack(fill=tk.X, padx=4, pady=(0, 4))

    def _get_model_param(self, key: str) -> Dict[str, Any]:
        sel = self.model_list.curselection()
        if not sel:
            return {"enabled": False, "value": ""}
        m = self.cfg["models"][sel[0]]
        return m.get("params", {}).get(key, {"enabled": False, "value": ""})

    def _set_model_param(self, key: str, value: Dict[str, Any]) -> None:
        sel = self.model_list.curselection()
        if not sel:
            return
        m = self.cfg["models"][sel[0]]
        m.setdefault("params", {})[key] = value

    def _browse(self, var: tk.StringVar, kind: str) -> None:
        if kind == "gguf":
            path = filedialog.askopenfilename(filetypes=[("GGUF", "*.gguf"), ("所有", "*.*")])
        else:
            path = filedialog.askopenfilename()
        if path:
            var.set(path)

    def _on_select_model(self, _evt=None) -> None:
        sel = self.model_list.curselection()
        if not sel:
            return
        m = self.cfg["models"][sel[0]]
        self.var_id.set(m.get("id", ""))
        self.var_alias.set(m.get("alias", ""))
        self.var_model.set(m.get("model", ""))
        self.var_mmproj.set(m.get("mmproj", ""))
        self.var_base_url.set(m.get("base_url", ""))
        self.var_api_key.set(m.get("api_key", ""))
        self.var_enabled.set(bool(m.get("enabled", True)))
        self.var_draft_model.set(m.get("draft_model", ""))
        self.var_draft_type.set(m.get("draft_type", ""))
        self.var_draft_n_max.set(str(m.get("draft_n_max", "3")))
        self.var_draft_n_min.set(str(m.get("draft_n_min", "0")))
        self.var_draft_p_split.set(str(m.get("draft_p_split", "0.1")))
        self.var_draft_p_min.set(str(m.get("draft_p_min", "0.0")))
        self.var_draft_ngl.set(str(m.get("draft_ngl", "auto")))
        # 兼容老 cfg：草稿参数可能在 params 里
        pmap = m.get("params", {}) or {}
        if "spec-draft-n-max" in pmap and not m.get("draft_n_max"):
            self.var_draft_n_max.set(str(pmap["spec-draft-n-max"].get("value", "3")))
        if "spec-draft-n-min" in pmap and not m.get("draft_n_min"):
            self.var_draft_n_min.set(str(pmap["spec-draft-n-min"].get("value", "0")))
        if "spec-draft-p-split" in pmap and not m.get("draft_p_split"):
            self.var_draft_p_split.set(str(pmap["spec-draft-p-split"].get("value", "0.1")))
        if "spec-draft-p-min" in pmap and not m.get("draft_p_min"):
            self.var_draft_p_min.set(str(pmap["spec-draft-p-min"].get("value", "0.0")))
        if "spec-draft-ngl" in pmap and (not m.get("draft_ngl") or m.get("draft_ngl") == "auto"):
            v = str(pmap["spec-draft-ngl"].get("value", "auto"))
            if v and v != "auto":
                self.var_draft_ngl.set(v)
        self.model_param_panel.reload()

    def _on_add_model(self) -> None:
        base = "new-model"
        idx = 1
        existing = {m.get("id", "") for m in self.cfg["models"]}
        new_id = base
        while new_id in existing:
            idx += 1
            new_id = f"{base}-{idx}"
        m = {"id": new_id, "alias": new_id, "model": "", "mmproj": "",
             "base_url": "", "api_key": "",
             "draft_model": "", "draft_type": "",
             "draft_n_max": "3", "draft_n_min": "0",
             "draft_p_split": "0.1", "draft_p_min": "0.0", "draft_ngl": "auto",
             "enabled": True, "params": {}}
        config._ensure_model_params(m)
        self.cfg["models"].append(m)
        self._refresh_model_list(select=len(self.cfg["models"]) - 1)

    def _on_del_model(self) -> None:
        sel = self.model_list.curselection()
        if not sel:
            return
        idx = sel[0]
        m = self.cfg["models"][idx]
        if not messagebox.askyesno("确认", f"删除模型 [{m.get('id')}] ?"):
            return
        del self.cfg["models"][idx]
        self._refresh_model_list(select=max(0, idx - 1))

    def _move(self, delta: int) -> None:
        sel = self.model_list.curselection()
        if not sel:
            return
        idx = sel[0]
        new_idx = idx + delta
        if new_idx < 0 or new_idx >= len(self.cfg["models"]):
            return
        self.cfg["models"][idx], self.cfg["models"][new_idx] = (
            self.cfg["models"][new_idx], self.cfg["models"][idx]
        )
        self._refresh_model_list(select=new_idx)

    def _on_save_model(self) -> None:
        sel = self.model_list.curselection()
        if not sel:
            messagebox.showinfo("提示", "请先选择一个模型")
            return
        idx = sel[0]
        m = self.cfg["models"][idx]
        m["id"] = self.var_id.get().strip()
        m["alias"] = self.var_alias.get().strip()
        m["model"] = self.var_model.get().strip()
        m["mmproj"] = self.var_mmproj.get().strip()
        m["base_url"] = self.var_base_url.get().strip()   # 留空表示用本机
        m["api_key"] = self.var_api_key.get().strip()     # 留空表示无
        m["enabled"] = bool(self.var_enabled.get())
        m["draft_model"] = self.var_draft_model.get().strip()
        m["draft_type"] = self.var_draft_type.get().strip()
        m["draft_n_max"] = self.var_draft_n_max.get().strip() or "3"
        m["draft_n_min"] = self.var_draft_n_min.get().strip() or "0"
        m["draft_p_split"] = self.var_draft_p_split.get().strip() or "0.1"
        m["draft_p_min"] = self.var_draft_p_min.get().strip() or "0.0"
        m["draft_ngl"] = self.var_draft_ngl.get().strip() or "auto"
        # 同步到 params 字典 (给 generate_preset 写 ini 使用)
        params = m.setdefault("params", {})
        if m["draft_model"]:
            params["spec-draft-model"] = {"enabled": True, "value": m["draft_model"]}
            params["spec-draft-n-max"] = {"enabled": True, "value": m["draft_n_max"]}
            params["spec-draft-n-min"] = {"enabled": True, "value": m["draft_n_min"]}
            params["spec-draft-p-split"] = {"enabled": True, "value": m["draft_p_split"]}
            params["spec-draft-p-min"] = {"enabled": True, "value": m["draft_p_min"]}
            if m["draft_ngl"]:
                params["spec-draft-ngl"] = {"enabled": True, "value": m["draft_ngl"]}
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
        self.model_param_panel.save_all()
        self._save_config()
        self._refresh_model_list(select=idx)
        self._log("已保存模型配置")

    def _refresh_model_list(self, select: Optional[int] = None) -> None:
        self.model_list.delete(0, tk.END)
        for m in self.cfg["models"]:
            mid = m.get("id", "?")
            alias = m.get("alias", "")
            tag = " (停用)" if not m.get("enabled", True) else ""
            draft = " ⚡草稿" if m.get("draft_model") else ""
            self.model_list.insert(tk.END, f"[{mid}] {alias}{tag}{draft}")
        if select is not None and 0 <= select < self.model_list.size():
            self.model_list.selection_set(select)
            self.model_list.see(select)
            self._on_select_model()

    def _build_global_tab(self) -> None:
        f = ttk.Frame(self.nb, padding=0)
        self.nb.add(f, text="全局")

        # 顶部醒目保存条 (改动后容易注意到)
        top_bar = tk.Frame(f, bg="#FFA500", height=44)
        top_bar.pack(fill=tk.X, side=tk.TOP)
        top_bar.pack_propagate(False)
        tk.Label(top_bar, text="⚠  修改参数后必须点「保存」才能写入 config.json + router-preset.ini ！",
                 bg="#FFA500", fg="black", font=("", 10, "bold")).pack(side=tk.LEFT, padx=10, pady=8)
        tk.Button(top_bar, text="💾  保存全局配置",
                  bg="#FF6B35", fg="white", font=("", 10, "bold"),
                  activebackground="#FF8C5A", activeforeground="white",
                  relief=tk.RAISED, bd=2, padx=16, pady=4,
                  command=self._on_save_global).pack(side=tk.RIGHT, padx=10, pady=6)

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
        inner.bind("<Configure>",
                   lambda _e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>",
                    lambda e: canvas.itemconfig(inner_id, width=e.width))
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

        # 固定底栏 (始终可见)
        bottom = ttk.Frame(f, padding=(10, 6, 10, 6), relief=tk.RAISED, borderwidth=1)
        bottom.pack(fill=tk.X, side=tk.BOTTOM)
        ttk.Label(bottom, text="提示: 改完点「保存」会写 config.json + router-preset.ini 并触发后台保存。",
                  foreground="#666").pack(side=tk.LEFT, padx=4)
        ttk.Button(bottom, text="💾  保存全局配置", command=self._on_save_global).pack(side=tk.RIGHT, padx=4)

    def _browse_dir(self, var: tk.StringVar) -> None:
        d = filedialog.askdirectory()
        if d:
            var.set(d)

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
            self.cfg["port"] = int(self.var_port.get().strip() or "8080")
            self.cfg["models_max"] = int(self.var_max.get().strip() or "1")
            self.cfg["sleep_idle_seconds"] = int(self.var_idle.get().strip() or "3600")
        except ValueError as e:
            messagebox.showerror("错误", f"数值字段不合法: {e}")
            return
        try:
            wd = self.cfg.setdefault("watchdog", {})
            wd["enabled"] = bool(self.var_wd_enabled.get())
            wd["free_mb_threshold"] = int(self.var_wd_threshold.get().strip() or "2048")
            wd["check_interval_seconds"] = int(self.var_wd_interval.get().strip() or "10")
            wd["min_idle_seconds"] = int(self.var_wd_min_idle.get().strip() or "0")
        except ValueError as e:
            messagebox.showerror("错误", f"watchdog 数值字段不合法: {e}")
            return
        self._save_config()
        self._log("全局配置已保存")

    def _build_prefs_tab(self) -> None:
        f = ttk.Frame(self.nb, padding=0)
        self.nb.add(f, text="偏好")
        PrefsTab(f).pack(fill=tk.BOTH, expand=True)

    def _refresh_all(self) -> None:
        self.var_llama_dir.set(self.cfg.get("llama_dir", r"C:\llama.cpp"))
        self.var_host.set(self.cfg.get("host", "0.0.0.0"))
        self.var_port.set(str(self.cfg.get("port", 8080)))
        self.var_max.set(str(self.cfg.get("models_max", 1)))
        self.var_idle.set(str(self.cfg.get("sleep_idle_seconds", 3600)))
        wd = self.cfg.get("watchdog", {}) or {}
        self.var_wd_enabled.set(bool(wd.get("enabled", False)))
        self.var_wd_threshold.set(str(wd.get("free_mb_threshold", 2048)))
        self.var_wd_interval.set(str(wd.get("check_interval_seconds", 10)))
        self.var_wd_min_idle.set(str(wd.get("min_idle_seconds", 0)))
        self._refresh_model_list(select=0 if self.cfg["models"] else None)
        self._refresh_status()

    def _save_config(self) -> None:
        if hasattr(self, "model_param_panel"):
            self.model_param_panel.save_all()
        config.save(self.cfg)
        preset_path = generate_preset.generate(self.cfg)
        self._log(f"已写入 {config.CONFIG_PATH} 并重新生成 {preset_path}")

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
        self.svc_state_var.set(f"主服务: {service.status()}")
        self._refresh_wd_status()

    def _refresh_wd_status(self) -> None:
        if hasattr(self, "wd_state_var"):
            self.wd_state_var.set(f"watchdog: {service.watchdog_status()}")

    def _poll_status(self) -> None:
        self._refresh_status()
        self.after(5000, self._poll_status)


def main() -> None:
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
