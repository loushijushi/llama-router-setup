"""配置文件 schema + 读写 + 用户偏好。

每条参数：
  key            参数名 (= ini 段中的 key，自动转 LLAMA_ARG_<KEY> 环境变量)
  label          中文显示名
  default        默认值 (字符串)
  kind           text / int / float / bool / choice
  scope          global | model | both
  category       common (常用) / advanced (扩展)
  hint           一句话提示 (UI 上显示在输入框旁)
  description    详细中文说明 (悬停 tooltip + 问号弹窗)
"""
import json
import os
import sys
from typing import Any, Dict, List, Optional

APP_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(APP_DIR, "config.json")
PRESET_PATH = os.path.join(APP_DIR, "router-preset.ini")
README_PATH = os.path.join(APP_DIR, "安装指南.txt")
README_FALLBACK = os.path.join(APP_DIR, "README.txt")
PREFS_PATH = os.path.join(APP_DIR, "user_preferences.json")


def preset_path_for(cfg: Dict[str, Any]) -> str:
    """llama-server 实际读取的 router-preset.ini 路径 (在 llama_dir 下)。"""
    llama_dir = (cfg.get("llama_dir") or r"C:\llama.cpp").rstrip("\\/")
    return os.path.join(llama_dir, "router-preset.ini")


def Param(key: str, label: str, default: str, *,
          kind: str = "text", scope: str = "global",
          category: str = "advanced", hint: str = "",
          description: str = "") -> Dict[str, Any]:
    return {
        "key": key, "label": label, "default": default,
        "kind": kind, "scope": scope,
        "category": category, "hint": hint, "description": description,
    }


def _D(s: str) -> str:
    return s


PARAM_SCHEMA: List[Dict[str, Any]] = [
    # ============== 性能 / 上下文 ==============
    Param("threads", "CPU 线程数", "8", kind="int", scope="both", category="advanced",
          hint="-1 = 自动",
          description=_D("设置 CPU 推理时的线程数。-1 表示让 llama.cpp 根据 CPU 核心数自动选择。"
                         "一般设为物理核心数即可。")),
    Param("threads-batch", "批处理线程数", "", kind="int", scope="global", category="advanced",
          hint="留空跟随 --threads",
          description=_D("Prompt 处理阶段的线程数。留空时与 --threads 相同。")),
    Param("ctx-size", "上下文长度", "262144", kind="int", scope="both", category="common",
          hint="0 = 按模型元数据",
          description=_D("模型一次能处理的最大 token 数。值越大越吃显存。"
                         "0 表示用模型训练时的原始长度。")),
    Param("batch-size", "逻辑批大小", "512", kind="int", scope="both", category="common",
          hint="推荐 512",
          description=_D("单次前向传播处理的 token 数上限。越大吞吐越高但首 token 延迟略增。")),
    Param("ubatch-size", "物理批大小", "", kind="int", scope="both", category="advanced",
          hint="留空=默认 512",
          description=_D("真实一次送入硬件的子批大小。必须 ≤ batch-size。")),
    Param("keep", "保留首 N token", "0", kind="int", scope="both", category="advanced",
          hint="-1 = 全部",
          description=_D("截断上下文时保留开头的 token 数。-1 表示永不截断。")),
    Param("parallel", "并行槽数", "1", kind="int", scope="both", category="common",
          hint="同时处理的请求数",
          description=_D("允许同时处理多少个请求。等于 1 时是单请求模式。")),
    Param("cont-batching", "连续批处理", "true", kind="bool", scope="both", category="advanced",
          description=_D("开启后可以把不同请求的 token 拼在一起批处理。")),
    Param("cache-prompt", "Prompt 缓存", "true", kind="bool", scope="both", category="advanced",
          description=_D("缓存 prompt 的 KV，复用相同 system prompt 的请求能省大量计算。")),
    Param("cache-reuse", "缓存复用最小块", "0", kind="int", scope="both", category="advanced",
          hint="0=禁用",
          description=_D("通过 KV shifting 复用 prompt cache 的最小块大小。")),
    Param("kv-unified", "统一 KV 缓冲", "true", kind="bool", scope="both", category="advanced",
          description=_D("使用跨 slot 共享的统一 KV buffer。")),
    Param("no-mmap", "禁用内存映射", "false", kind="bool", scope="both", category="advanced",
          description=_D("默认开启 mmap 加快加载。设为 true 时不映射。")),
    # ============== 注意力 / RoPE ==============
    Param("flash-attn", "Flash Attention", "on", kind="choice", scope="both", category="common",
          hint="on / off / auto",
          description=_D("开启后用 Flash Attention 算法，可显著降低显存占用并加速。")),
    Param("swa-full", "全尺寸 SWA 缓存", "false", kind="bool", scope="both", category="advanced",
          description=_D("对带 SWA 的模型使用全尺寸 KV 缓存。")),
    Param("rope-scaling", "RoPE 缩放方式", "none", kind="choice", scope="both", category="advanced",
          hint="none / linear / yarn",
          description=_D("位置编码缩放算法。none/linear/yarn。")),
    Param("rope-scale", "RoPE 缩放因子", "1", kind="float", scope="both", category="advanced",
          description=_D("把上下文扩到 N 倍。")),
    Param("rope-freq-base", "RoPE 基础频率", "0", kind="float", scope="both", category="advanced",
          hint="0 = 从模型读",
          description=_D("覆盖 RoPE 频率基础值。0=用模型自带值。")),
    Param("yarn-orig-ctx", "YaRN 原始 ctx", "0", kind="int", scope="both", category="advanced",
          description=_D("YaRN 算法的原始 ctx 长度。0=从模型读。")),
    Param("yarn-ext-factor", "YaRN 外推因子", "-1.0", kind="float", scope="both", category="advanced",
          description=_D("YaRN 外推混合系数。-1=用模型自带值。")),
    Param("yarn-attn-factor", "YaRN 注意力因子", "-1.0", kind="float", scope="both", category="advanced",
          description=_D("YaRN 注意力缩放。-1=用模型自带值。")),
    Param("yarn-beta-slow", "YaRN beta_slow", "-1.0", kind="float", scope="both", category="advanced",
          description=_D("YaRN beta_slow。-1=用模型自带值。")),
    Param("yarn-beta-fast", "YaRN beta_fast", "-1.0", kind="float", scope="both", category="advanced",
          description=_D("YaRN beta_fast。-1=用模型自带值。")),
    # ============== KV 缓存 ==============
    Param("cache-type-k", "KV K 量化", "q8_0", kind="choice", scope="both", category="common",
          hint="f16/f32/q8_0/q4_0 等",
          description=_D("Key 缓存的量化类型。q8_0 是质量与显存的最佳平衡点。")),
    Param("cache-type-v", "KV V 量化", "q8_0", kind="choice", scope="both", category="common",
          hint="f16/f32/q8_0/q4_0 等",
          description=_D("Value 缓存的量化类型。V 对量化更敏感。")),
    Param("kv-offload", "KV 卸载到 GPU", "true", kind="bool", scope="both", category="advanced",
          description=_D("把 KV 缓存放到 GPU 显存里。")),
    Param("cache-ram", "cache-ram (MiB)", "8192", kind="int", scope="both", category="advanced",
          hint="-1=不限 0=禁用",
          description=_D("KV 缓存占用的内存上限 MiB。")),
    Param("defrag-thold", "KV defrag 阈值", "-1.0", kind="float", scope="both", category="advanced",
          description=_D("[已弃用] KV 缓存碎片整理阈值。")),
    # ============== GPU / 加载 ==============
    Param("n-gpu-layers", "GPU 层数", "0", kind="int", scope="both", category="common",
          hint="99=全部 0=纯CPU auto=自动",
          description=_D("放到 GPU 上的 transformer 层数。99 或 auto=全部；0=纯 CPU。\n"
                         "⚠ 默认 0 (由 llama.cpp 自动决定)，你的 llama.cpp 参考里没传这个参数。")),
    Param("split-mode", "多卡拆分模式", "layer", kind="choice", scope="both", category="advanced",
          hint="none/layer/row/tensor",
          description=_D("多卡时怎么切分模型。layer 按层切；row 按权重行切；tensor 按张量切。")),
    Param("tensor-split", "张量拆分比例", "", kind="text", scope="both", category="advanced",
          hint="如 3,1",
          description=_D("多卡时各 GPU 的权重比例。")),
    Param("main-gpu", "主 GPU 索引", "0", kind="int", scope="both", category="advanced",
          description=_D("split-mode=none 或 row 模式时的主 GPU 编号。")),
    Param("device", "使用设备", "", kind="text", scope="both", category="advanced",
          hint="逗号分隔，留空=自动",
          description=_D("逗号分隔的设备列表。")),
    Param("override-tensor", "张量类型覆盖", "", kind="text", scope="both", category="advanced",
          hint="pattern=type,...",
          description=_D("高级：把指定张量强制放到指定 buffer 类型。")),
    Param("cpu-moe", "MoE 权重留 CPU", "false", kind="bool", scope="both", category="advanced",
          description=_D("把混合专家(MoE)模型的所有专家权重留在 CPU。")),
    Param("n-cpu-moe", "前 N 层 MoE 留 CPU", "0", kind="int", scope="both", category="advanced",
          description=_D("只把前 N 层的 MoE 权重留 CPU。")),
    Param("n-cpu-ffn", "前 N 层 FFN 留 CPU", "0", kind="int", scope="both", category="advanced",
          description=_D("稠密模型专用：把前 N 层的 FFN 权重留 CPU。")),
    Param("fit", "自动适配显存", "true", kind="bool", scope="both", category="advanced",
          description=_D("让 llama.cpp 自动调整参数以适配设备显存。")),
    Param("fit-target", "显存余量 (MiB)", "1024", kind="int", scope="both", category="advanced",
          description=_D("fit 模式为每张卡预留多少 MiB 余量。")),
    Param("fit-ctx", "fit 最小 ctx", "4096", kind="int", scope="both", category="advanced",
          description=_D("fit 模式允许的最小 ctx。")),
    Param("load-mode", "加载模式", "auto", kind="choice", scope="both", category="advanced",
          hint="auto/mmap/mlock/dio",
          description=_D("auto 默认；mmap 内存映射；mlock 锁内存；dio 用 DirectIO。")),
    Param("tensor-read-lazy", "懒读张量", "auto", kind="choice", scope="both", category="advanced",
          hint="on/off/auto",
          description=_D("对超过 4 GiB 的张量按需从磁盘读。")),
    Param("numa", "NUMA 优化", "distribute", kind="choice", scope="both", category="advanced",
          hint="distribute/isolate/numactl",
          description=_D("NUMA 架构 CPU 上的优化策略。")),
    Param("poll", "轮询级别", "50", kind="int", scope="both", category="common",
          hint="0-100",
          description=_D("CPU 轮询等待工作的等级。0=不用轮询，100=全力轮询。默认 50。")),
    Param("poll-batch", "批处理轮询", "", kind="int", scope="both", category="advanced",
          hint="留空=跟随",
          description=_D("批处理(prompt 评估)阶段的轮询级别。留空跟随 --poll。")),
    # ============== 采样 ==============
    Param("temp", "温度", "0.8", kind="float", scope="both", category="common",
          hint="0=确定 1=随机",
          description=_D("采样温度。0=完全确定性；0.7-1.0=正常对话；1.5+=更有创造性但容易胡扯。")),
    Param("top-k", "top-k", "40", kind="int", scope="both", category="common",
          hint="0=禁用",
          description=_D("只从概率最高的 K 个 token 中采样。40 是常用值。")),
    Param("top-p", "top-p", "0.95", kind="float", scope="both", category="common",
          hint="0-1, 1.0=禁用",
          description=_D("核采样：从累积概率达到 p 的最小集合里采样。0.9-0.95 是常用值。")),
    Param("min-p", "min-p", "0.05", kind="float", scope="both", category="advanced",
          hint="0=禁用",
          description=_D("最小概率采样：丢弃概率过低的 token。0.05 是常用值。")),
    Param("top-nsigma", "top-n-sigma", "-1.0", kind="float", scope="both", category="advanced",
          description=_D("top-n-sigma 采样。-1=禁用。")),
    Param("typical-p", "typical-p", "1.0", kind="float", scope="both", category="advanced",
          hint="1.0=禁用",
          description=_D("locally typical 采样。")),
    Param("repeat-last-n", "重复惩罚窗口", "64", kind="int", scope="both", category="advanced",
          hint="0=禁用",
          description=_D("对最近 N 个 token 施加重复惩罚。")),
    Param("repeat-penalty", "重复惩罚", "1.0", kind="float", scope="both", category="common",
          hint="1.0=禁用",
          description=_D("惩罚重复 token 的倍数。1.0=不惩罚；1.05-1.1 是常用值。")),
    Param("presence-penalty", "存在惩罚", "0.0", kind="float", scope="both", category="advanced",
          description=_D("对已出现过的任何 token 一律施加固定惩罚。")),
    Param("frequency-penalty", "频率惩罚", "0.0", kind="float", scope="both", category="advanced",
          description=_D("对已出现过的 token 按出现次数施加线性惩罚。")),
    Param("dry-multiplier", "DRY 乘子", "0.0", kind="float", scope="both", category="advanced",
          hint="0=禁用",
          description=_D("DRY 采样的乘子。0=禁用；0.8-1.5 范围可减少重复短语。")),
    Param("dry-base", "DRY 基准", "1.75", kind="float", scope="both", category="advanced",
          description=_D("DRY 采样基础值。默认 1.75。")),
    Param("dry-allowed-length", "DRY 允许长度", "2", kind="int", scope="both", category="advanced",
          description=_D("短于此长度的重复不被惩罚。")),
    Param("dry-penalty-last-n", "DRY 惩罚窗口", "64", kind="int", scope="both", category="advanced",
          description=_D("DRY 采样考虑最近多少个 token。")),
    Param("mirostat", "Mirostat", "0", kind="int", scope="both", category="advanced",
          hint="0=关 1=v1 2=v2",
          description=_D("Mirostat 采样算法。0=禁用；1=Mirostat 1.0；2=Mirostat 2.0。")),
    Param("mirostat-lr", "Mirostat lr", "0.1", kind="float", scope="both", category="advanced",
          description=_D("Mirostat 的学习率 eta。")),
    Param("mirostat-ent", "Mirostat 熵", "5.0", kind="float", scope="both", category="advanced",
          description=_D("Mirostat 目标熵 tau。")),
    Param("seed", "随机种子", "-1", kind="int", scope="both", category="advanced",
          hint="-1=随机",
          description=_D("固定种子能让输出可复现。-1=用时间随机种子。")),
    Param("samplers", "采样器序列", "", kind="text", scope="both", category="advanced",
          hint="分号分隔；空=默认",
          description=_D("自定义采样器顺序。例: top_k;top_p;temp。")),
    Param("ignore-eos", "忽略 EOS", "false", kind="bool", scope="both", category="advanced",
          description=_D("忽略 EOS token 一直生成。")),
    # ============== 推理 / 模板 ==============
    Param("jinja", "Jinja 模板引擎", "true", kind="bool", scope="both", category="common",
          description=_D("使用 Jinja 模板引擎渲染 chat prompt。")),
    Param("chat-template", "内置 chat 模板", "", kind="choice", scope="both", category="common",
          hint="llama3 / chatml / deepseek2 ...",
          description=_D("选择内置模板。空=从模型元数据自动选。")),
    Param("chat-template-file", "自定义 chat 模板文件", "", kind="text", scope="both", category="advanced",
          description=_D("指向你自己的 jinja 模板文件路径。")),
    Param("reasoning", "思考模式", "auto", kind="choice", scope="both", category="common",
          hint="on/off/auto",
          description=_D("是否启用模型的思考/推理模式(Qwen3 / DeepSeek-R1 等)。")),
    Param("reasoning-format", "思考格式", "auto", kind="choice", scope="both", category="advanced",
          hint="none/deepseek/deepseek-legacy/auto",
          description=_D("思考内容的返回格式。")),
    Param("reasoning-budget", "思考预算 (token)", "-1", kind="int", scope="both", category="advanced",
          hint="-1=无限 0=立即结束",
          description=_D("限制思考阶段最大 token 数。")),
    Param("reasoning-effort", "思考强度", "default", kind="choice", scope="both", category="advanced",
          hint="default/minimal/low/medium/high",
          description=_D("把思考强度传给 chat 模板。")),
    Param("prefill-assistant", "预填 assistant", "true", kind="bool", scope="both", category="advanced",
          description=_D("当最后一条消息是 assistant 时是否预填它。")),
    Param("ctx-checkpoints", "上下文检查点", "32", kind="int", scope="both", category="advanced",
          description=_D("每个 slot 保留多少上下文检查点。")),
    Param("context-shift", "上下文位移", "false", kind="bool", scope="both", category="advanced",
          description=_D("无限生成时是否自动位移上下文。")),
    # ============== 多模态 ==============
    Param("mmproj-offload", "mmproj 卸载到 GPU", "true", kind="bool", scope="model", category="common",
          description=_D("视觉模型的 mmproj 投影文件是否放到 GPU。")),
    Param("mmproj-device", "mmproj 设备", "", kind="text", scope="model", category="advanced",
          hint="留空=自动",
          description=_D("mmproj 投影文件用哪块 GPU。")),
    Param("image-min-tokens", "图像最小 tokens", "0", kind="int", scope="model", category="advanced",
          description=_D("每张图最少占多少 token。0=用模型默认。")),
    Param("image-max-tokens", "图像最大 tokens", "0", kind="int", scope="model", category="advanced",
          description=_D("每张图最多占多少 token。")),
    Param("mtmd-batch-max-tokens", "多模态批 tokens", "1024", kind="int", scope="model", category="advanced",
          description=_D("多模态编码时一次最多处理多少 image token。")),
    # ============== 推测解码 ==============
    Param("spec-type", "推测类型", "", kind="choice", scope="model", category="common",
          hint="draft-mtp / draft-dflash / draft-dflash2 / draft-dspark / ngram-*",
          description=_D("推测解码类型。MTP/DFlash/DFlash2 模型自动识别。")),
    Param("spec-draft-model", "草稿模型路径", "", kind="text", scope="model", category="common",
          hint="MTP/DFlash 等 .gguf 路径",
          description=_D("推测解码的草稿模型 .gguf 绝对路径。")),
    Param("spec-draft-ngl", "草稿 GPU 层数", "auto", kind="text", scope="model", category="common",
          hint="auto / 99 / 0",
          description=_D("草稿模型放多少层到 GPU。")),
    Param("spec-draft-device", "草稿模型设备", "", kind="text", scope="model", category="advanced",
          description=_D("草稿模型用哪个 GPU。留空自动。")),
    Param("spec-draft-n-max", "草稿最大 token", "3", kind="int", scope="model", category="advanced",
          description=_D("每轮推测最多生成多少个候选 token。")),
    Param("spec-draft-n-min", "草稿最小 token", "0", kind="int", scope="model", category="advanced",
          description=_D("少于这个数不触发验证。")),
    Param("spec-draft-p-split", "草稿拆分概率", "0.1", kind="float", scope="model", category="advanced",
          description=_D("触发推测的拆分概率阈值。")),
    Param("spec-draft-p-min", "草稿最小概率", "0.0", kind="float", scope="model", category="advanced",
          description=_D("贪心模式下的最小接受概率。")),
    Param("spec-draft-threads", "草稿 CPU 线程", "", kind="int", scope="model", category="advanced",
          description=_D("草稿模型用的 CPU 线程数。留空跟随主模型。")),
    Param("spec-draft-threads-batch", "草稿批线程", "", kind="int", scope="model", category="advanced",
          description=_D("草稿模型批处理线程。")),
    Param("spec-draft-cache-type-k", "草稿 KV K", "f16", kind="choice", scope="model", category="advanced",
          description=_D("草稿模型的 K 缓存量化。f16 即可。")),
    Param("spec-draft-cache-type-v", "草稿 KV V", "f16", kind="choice", scope="model", category="advanced",
          description=_D("草稿模型的 V 缓存量化。")),
    Param("spec-draft-backend-sampling", "草稿后端采样", "true", kind="bool", scope="model", category="advanced",
          description=_D("把草稿采样放到后端。")),
    # ============== 杂项 ==============
    Param("warmup", "加载时预热", "true", kind="bool", scope="both", category="advanced",
          description=_D("加载完模型后空跑一次预热。")),
    Param("check-tensors", "校验张量数据", "false", kind="bool", scope="both", category="advanced",
          description=_D("加载时校验每个张量的数值。调试用。")),
    Param("op-offload", "host op 卸载", "true", kind="bool", scope="both", category="advanced",
          description=_D("把 host tensor 操作卸载到 device。")),
    Param("no-host", "绕过 host 缓冲", "false", kind="bool", scope="both", category="advanced",
          description=_D("绕过 host buffer 让 device 用更多缓冲。")),
    Param("repack", "权重重打包", "true", kind="bool", scope="both", category="advanced",
          description=_D("按目标硬件重打包权重，提升速度。")),
    Param("rpc", "RPC 服务器", "", kind="text", scope="both", category="advanced",
          hint="host:port,...",
          description=_D("远程推理的 RPC 服务器列表。")),
    Param("timeout", "server 超时 (秒)", "3600", kind="int", scope="global", category="advanced",
          description=_D("HTTP server 读写超时秒数。")),
    Param("api-key", "API 鉴权 key", "", kind="text", scope="global", category="advanced",
          hint="逗号分隔多个",
          description=_D("客户端调用时要带的 API key。")),
    Param("cors-origins", "CORS 来源", "", kind="text", scope="global", category="advanced",
          hint="* = 全部",
          description=_D("允许跨域的来源。")),
    Param("metrics", "启用 /metrics", "false", kind="bool", scope="global", category="advanced",
          description=_D("开启 Prometheus 监控端点。")),
    Param("embedding", "仅 embedding 模式", "false", kind="bool", scope="global", category="advanced",
          description=_D("开启后只能调用 embedding 端点。")),
    Param("rerank", "启用 rerank 端点", "false", kind="bool", scope="global", category="advanced",
          description=_D("开启 reranking 端点。")),
    Param("webui", "启用 Web UI", "true", kind="bool", scope="global", category="advanced",
          description=_D("内置 Web 聊天界面。")),
]

SCHEMA_INDEX: Dict[str, Dict[str, Any]] = {p["key"]: p for p in PARAM_SCHEMA}


# ----------------------------------------------------------------------
# 用户偏好
# ----------------------------------------------------------------------
def _default_prefs() -> Dict[str, Any]:
    return {
        "common_keys": [
            # 全局常用
            "threads", "ctx-size", "batch-size", "flash-attn",
            "cache-type-k", "cache-type-v", "jinja", "poll",
            # 模型常用
            "parallel", "temp", "top-p", "top-k", "repeat-penalty",
            "reasoning", "chat-template", "mmproj-offload",
        ],
        "extra_params": [],
    }


def load_prefs() -> Dict[str, Any]:
    if not os.path.exists(PREFS_PATH):
        save_prefs(_default_prefs())
        return json.loads(json.dumps(_default_prefs()))
    with open(PREFS_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    base = _default_prefs()
    base.update(data)
    if not isinstance(base.get("extra_params"), list):
        base["extra_params"] = []
    return base


def save_prefs(prefs: Dict[str, Any]) -> None:
    with open(PREFS_PATH, "w", encoding="utf-8") as f:
        json.dump(prefs, f, ensure_ascii=False, indent=4)


def common_keys() -> List[str]:
    return list(load_prefs().get("common_keys", []))


def extra_params() -> List[Dict[str, Any]]:
    return list(load_prefs().get("extra_params", []))


def all_known_keys() -> List[str]:
    return [p["key"] for p in PARAM_SCHEMA] + [e["key"] for e in extra_params()]


# ----------------------------------------------------------------------
# 配置加载/保存
# ----------------------------------------------------------------------
DEFAULT_CONFIG: Dict[str, Any] = {
    "llama_dir": r"C:\llama.cpp",
    "host": "0.0.0.0",
    "port": 8080,
    "models_max": 1,
    "sleep_idle_seconds": 3600,
    "watchdog": {
        "enabled": False,
        "free_mb_threshold": 2048,
        "check_interval_seconds": 10,
        "min_idle_seconds": 0,
    },
    "models": [],
}


def load(path: str = CONFIG_PATH) -> Dict[str, Any]:
    if not os.path.exists(path):
        save(DEFAULT_CONFIG, path)
        return json.loads(json.dumps(DEFAULT_CONFIG))
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return _merge_defaults(data)


def save(cfg: Dict[str, Any], path: str = CONFIG_PATH) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=4)


def _merge_defaults(data: Dict[str, Any]) -> Dict[str, Any]:
    out = json.loads(json.dumps(DEFAULT_CONFIG))
    for k, v in data.items():
        if k == "models":
            out["models"] = [_normalize_model(m) for m in v]
        elif k == "watchdog" and isinstance(v, dict):
            out["watchdog"].update(v)
        else:
            out[k] = v
    for m in out["models"]:
        _ensure_model_params(m)
    return out


def _merge_params_dict(target: Dict[str, Any], src: Any) -> None:
    if not isinstance(src, dict):
        return
    for k, v in src.items():
        if isinstance(v, dict) and "enabled" in v and "value" in v:
            target[k] = v
        else:
            target[k] = {"enabled": True, "value": "" if v is None else str(v)}


def _normalize_model(m: Dict[str, Any]) -> Dict[str, Any]:
    base = {"params": {}}
    base.update(m)
    base.setdefault("enabled", True)
    base.setdefault("alias", base.get("id", ""))
    base.setdefault("mmproj", "")
    base.setdefault("draft_model", "")
    base.setdefault("draft_type", "")
    # 草稿模型专用设置项 (UI 上独立显示，不在参数子表里出现)
    base.setdefault("draft_n_max", "3")
    base.setdefault("draft_n_min", "0")
    base.setdefault("draft_p_split", "0.1")
    base.setdefault("draft_p_min", "0.0")
    base["params"] = base.get("params", {}) or {}
    return base


def _default_enabled(key: str) -> bool:
    """新建条目时该 key 是否默认启用：常用参数 = True，其他 = False。"""
    return key in set(common_keys())


def _ensure_model_params(m: Dict[str, Any]) -> None:
    """补全 model 缺失的参数。

    常用 key (在 user_preferences.common_keys) 默认 enabled=True；
    其他默认 enabled=False，需要用户在 UI 里勾选。
    """
    params = m.setdefault("params", {})
    for p in PARAM_SCHEMA:
        if p["scope"] not in ("model", "both"):
            continue
        if p["key"] not in params:
            params[p["key"]] = {
                "enabled": _default_enabled(p["key"]),
                "value": p["default"],
            }
    for p in extra_params():
        if p.get("scope", "model") not in ("model", "both", "global"):
            continue
        if p["key"] not in params:
            params[p["key"]] = {
                "enabled": _default_enabled(p["key"]),
                "value": p.get("default", ""),
            }


def app_dir() -> str:
    return APP_DIR


def is_frozen() -> bool:
    return getattr(sys, "frozen", False)


def list_kinds_choices() -> Dict[str, List[str]]:
    return {
        "flash-attn": ["on", "off", "auto"],
        "rope-scaling": ["none", "linear", "yarn"],
        "cache-type-k": ["f32", "f16", "bf16", "q8_0", "q4_0", "q4_1", "iq4_nl", "q5_0", "q5_1"],
        "cache-type-v": ["f32", "f16", "bf16", "q8_0", "q4_0", "q4_1", "iq4_nl", "q5_0", "q5_1"],
        "spec-draft-cache-type-k": ["f32", "f16", "bf16", "q8_0", "q4_0", "q4_1", "iq4_nl", "q5_0", "q5_1"],
        "spec-draft-cache-type-v": ["f32", "f16", "bf16", "q8_0", "q4_0", "q4_1", "iq4_nl", "q5_0", "q5_1"],
        "split-mode": ["none", "layer", "row", "tensor"],
        "load-mode": ["auto", "none", "mmap", "mlock", "mmap+mlock", "dio"],
        "tensor-read-lazy": ["on", "off", "auto"],
        "numa": ["distribute", "isolate", "numactl"],
        "reasoning": ["on", "off", "auto"],
        "reasoning-format": ["none", "deepseek", "deepseek-legacy", "auto"],
        "reasoning-effort": ["default", "minimal", "low", "medium", "high", "xhigh", "max"],
        "chat-template": [
            "", "bailing", "bailing2", "chatglm3", "chatglm4", "chatml", "command-r",
            "deepseek", "deepseek2", "deepseek3", "exaone-moe", "exaone3", "exaone4",
            "falcon3", "gemma", "gpt-oss", "granite", "grok-2", "hunyuan-dense", "hunyuan-moe",
            "hunyuan-vl", "kimi-k2", "llama2", "llama3", "llama4", "megrez", "minicpm",
            "mistral-v1", "mistral-v3", "mistral-v7", "monarch", "openchat", "orion",
            "phi3", "phi4", "rwkv-world", "smolvlm", "vicuna", "vicuna-orca", "yandex", "zephyr",
        ],
        "spec-type": [
            "", "draft-simple", "draft-eagle3", "draft-mtp", "draft-dflash", "draft-dflash2",
            "draft-dspark", "ngram-simple", "ngram-map-k", "ngram-map-k4v",
            "ngram-mod", "ngram-cache", "none",
        ],
        "spec-draft-ngl": ["auto", "0", "99"],
    }


def lookup(key: str) -> Optional[Dict[str, Any]]:
    if key in SCHEMA_INDEX:
        return SCHEMA_INDEX[key]
    for e in extra_params():
        if e["key"] == key:
            return e
    return None
