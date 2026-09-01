"""根据 config.json 生成 llama.cpp router-preset.ini。

输出格式与 llama.cpp 的 preset.cpp 解析器兼容：
  * 段名 = 模型 ID
  * key = value 形式；空 value 不写
  * 仅当 enabled=True 且 value 非空时才输出该行
  * 既支持内置 schema，也支持用户在偏好里加的"额外参数"（任意 key）
"""
from typing import Any, Dict, List

import config


def _all_params_meta() -> List[Dict[str, Any]]:
    """内置 schema + 用户额外参数。"""
    return list(config.PARAM_SCHEMA) + list(config.extra_params())


def _kv_lines(scope: str, params: Dict[str, Any],
             skip_keys: set = None, skip_values: Dict[str, str] = None) -> List[str]:
    skip_keys = skip_keys or set()
    skip_values = skip_values or {}
    out: List[str] = []
    for p in _all_params_meta():
        if p["scope"] not in (scope, "both"):
            continue
        k = p["key"]
        if k in skip_keys:
            continue
        if k not in params:
            continue
        node = params[k]
        if not isinstance(node, dict):
            continue
        if not node.get("enabled", False):
            continue
        val = (node.get("value") or "").strip()
        if not val:
            continue
        if k in skip_values and skip_values[k] == val:
            continue
        out.append(f"{k} = {val}")

    # 用户额外参数中可能 schema 没记录 (防御性)
    for k, node in params.items():
        if any(p["key"] == k for p in _all_params_meta()):
            continue
        if k in skip_keys:
            continue
        if not isinstance(node, dict):
            continue
        if not node.get("enabled", False):
            continue
        val = (node.get("value") or "").strip()
        if not val:
            continue
        if k in skip_values and skip_values[k] == val:
            continue
        out.append(f"{k} = {val}")
    return out


def render(cfg: Dict[str, Any]) -> str:
    lines: List[str] = []
    lines.append("; llama.cpp Router 模式模型预设 (由 config.json 自动生成，请勿手工编辑)")
    lines.append("; 段名 = 模型ID，alias = 别名，两者都可作为 API 请求里的 model 参数")
    lines.append("version = 1")
    lines.append("")

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
            lines.append(f"model = {model_path}")
        mmproj = (m.get("mmproj") or "").strip()
        if mmproj:
            lines.append(f"mmproj = {mmproj}")
        alias = (m.get("alias") or "").strip()
        if alias:
            lines.append(f"alias = {alias}")

        # 草稿模型快捷字段
        draft_model = (m.get("draft_model") or "").strip()
        draft_type = (m.get("draft_type") or "").strip()
        if draft_model and not draft_type:
            name = draft_model.lower()
            for prefix, d_type in (("mtp-", "draft-mtp"),
                                   ("dflash2-", "draft-dflash2"),
                                   ("dflash-", "draft-dflash"),
                                   ("dspark-", "draft-dspark")):
                if prefix in name:
                    draft_type = d_type
                    break
        if draft_model:
            lines.append(f"spec-draft-model = {draft_model}")
        if draft_type:
            lines.append(f"spec-type = {draft_type}")

        mp = m.get("params", {}) or {}
        excl = set()
        if draft_model:
            excl.add("spec-draft-model")
        if draft_type:
            excl.add("spec-type")
        # 模型段总是显式写出 (即使与全局段相同),
        # 这样用户能在 ini 里看到自己勾选/设置的状态
        for ln in _kv_lines("model", mp, skip_keys=excl):
            k = ln.split("=", 1)[0].strip()
            if k in excl:
                continue
            lines.append(ln)

        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def generate(cfg: Dict[str, Any], out_path: str = None) -> str:
    if out_path is None:
        out_path = config.preset_path_for(cfg)
    content = render(cfg)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(content)
    return out_path


if __name__ == "__main__":
    import sys
    print(generate(config.load()))
    sys.exit(0)
