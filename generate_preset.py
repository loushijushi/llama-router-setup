"""根据 config.json 生成 llama.cpp router-preset.ini。

输出格式与 llama.cpp 的 preset.cpp 解析器兼容：
  * 段名 = 模型 ID
  * key = value 形式；空 value 不写
  * 仅当 enabled=True 且 value 非空时才输出该行
  * 既支持内置 schema，也支持用户在偏好里加的"额外参数"（任意 key）
"""
from typing import Any, Dict, List, Optional, Set

import config


def _all_params_meta() -> List[Dict[str, Any]]:
    """内置 schema + 用户额外参数。"""
    return list(config.PARAM_SCHEMA) + list(config.extra_params())


def _kv_lines(scope: str, params: Dict[str, Any],
             skip_keys: set = None, skip_values: Dict[str, str] = None,
             known: Optional[Set[str]] = None,
             skipped: Optional[List[str]] = None) -> List[str]:
    """拼出 `key = value` 行。

    known  非 None 时: 只输出其中包含的 key (本机 llama-server 认识的集合)。
                     被挡掉的 key 记入 skipped —— 否则一个未知 key 会让整个
                     router-preset.ini 解析失败, 所有模型都加载不了。
    """
    skip_keys = skip_keys or set()
    skip_values = skip_values or {}
    out: List[str] = []

    def emit(k: str, val: str) -> None:
        if k in skip_keys or k in skip_values and skip_values[k] == val:
            return
        if known is not None and k not in known:
            if skipped is not None and k not in skipped:
                skipped.append(k)
            return
        out.append(f"{k} = {val}")

    def wanted(k: str, node: Any) -> Optional[str]:
        if not isinstance(node, dict) or not node.get("enabled", False):
            return None
        val = (node.get("value") or "").strip()
        return val or None

    for p in _all_params_meta():
        if p["scope"] not in (scope, "both"):
            continue
        k = p["key"]
        if k in skip_keys or k not in params:
            continue
        val = wanted(k, params[k])
        if val:
            emit(k, val)

    # 用户额外参数中可能 schema 没记录 (防御性)
    known_meta = {p["key"] for p in _all_params_meta()}
    for k, node in params.items():
        if k in known_meta or k in skip_keys:
            continue
        val = wanted(k, node)
        if val:
            emit(k, val)
    return out


def render(cfg: Dict[str, Any], known: Optional[Set[str]] = None,
           skipped: Optional[List[str]] = None) -> str:
    lines: List[str] = []
    lines.append("; llama.cpp Router 模式模型预设 (由 config.json 自动生成，请勿手工编辑)")
    lines.append("; 段名 = 模型ID，alias = 别名，两者都可作为 API 请求里的 model 参数")
    lines.append("version = 1")
    lines.append("")

    def put(key: str, val: str) -> None:
        """写一行; known 存在且不含该 key 时挡掉并记入 skipped。"""
        if known is not None and key not in known:
            if skipped is not None and key not in skipped:
                skipped.append(key)
            return
        lines.append(f"{key} = {val}")

    for m in cfg.get("models", []):
        if not m.get("enabled", True):
            continue
        mid = m.get("id", "").strip()
        if not mid:
            continue
        label = m.get("alias") or mid
        lines.append(f"; 模型: {label}")
        lines.append(f"[{mid}]")
        model_path = (m.get("model") or "").strip()
        if model_path:
            put("model", model_path)
        mmproj = (m.get("mmproj") or "").strip()
        if mmproj:
            put("mmproj", mmproj)
        alias = (m.get("alias") or "").strip()
        if alias:
            put("alias", alias)

        # 草稿模型快捷字段 (draft_enabled 独立开关; 草稿文件可选)
        draft_on = bool(m.get("draft_enabled", bool(m.get("draft_model"))))
        # 每个快捷字段还有独立勾选框 (draft_en), 勾了才写这一行
        den = m.get("draft_en") or {}

        def _en(k: str) -> bool:
            return bool(den.get(k, True))

        draft_model = (m.get("draft_model") or "").strip()
        draft_type = (m.get("draft_type") or "").strip()
        if draft_on and draft_model and not draft_type:
            name = draft_model.lower()
            for prefix, d_type in (("mtp-", "draft-mtp"),
                                   ("dflash2-", "draft-dflash2"),
                                   ("dflash-", "draft-dflash"),
                                   ("dspark-", "draft-dspark")):
                if prefix in name:
                    draft_type = d_type
                    break
        if not draft_on:
            draft_model = ""
            draft_type = ""
        if draft_model and _en("spec-draft-model"):
            put("spec-draft-model", draft_model)
        if draft_type and _en("spec-type"):
            put("spec-type", draft_type)

        mp = m.get("params", {}) or {}
        excl = set()
        if draft_model:
            excl.add("spec-draft-model")
        if draft_type:
            excl.add("spec-type")
        # 快捷字段被单独取消勾选的, params 里那份也不许写出来
        excl.update(k for k in config.DRAFT_SHORTCUT_KEYS if not _en(k))
        if not draft_on:
            # 开关关闭: params 里的草稿/推测参数一律不写 (兼容旧配置残留)
            excl.update(k for k in mp if k == "spec-type" or k.startswith("spec-draft-"))
        # 模型段总是显式写出 (即使与全局段相同),
        # 这样用户能在 ini 里看到自己勾选/设置的状态
        for ln in _kv_lines("model", mp, skip_keys=excl, known=known, skipped=skipped):
            lines.append(ln)

        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def generate(cfg: Dict[str, Any], out_path: str = None,
             skipped: Optional[List[str]] = None) -> str:
    """写出 router-preset.ini 并返回路径。

    skipped 传入 list 时, 会填入「本机 llama-server 不认识、因此被跳过」的 key。
    这些 key 若照写会让整个 preset 解析失败 (所有模型都加载不了)。
    """
    if out_path is None:
        out_path = config.preset_path_for(cfg)
    known = config.llama_known_keys(cfg)
    content = render(cfg, known=known, skipped=skipped)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(content)
    return out_path


if __name__ == "__main__":
    import sys
    print(generate(config.load()))
    sys.exit(0)
