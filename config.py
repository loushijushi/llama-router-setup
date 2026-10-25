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


def llama_server_path(cfg: Dict[str, Any]) -> str:
    llama_dir = (cfg.get("llama_dir") or r"C:\llama.cpp").rstrip("\\/")
    return os.path.join(llama_dir, "llama-server.exe")


# llama-server --help 认识的 flag 名缓存: {(exe, mtime, size): frozenset}
_KNOWN_KEYS_CACHE: Dict[tuple, Optional[frozenset]] = {}

# preset 解析器额外接受的 key (不以 -- 形式出现在 --help 里)
_PRESET_BUILTIN_KEYS = frozenset({"version", "model", "alias", "mmproj"})


def llama_known_keys(cfg: Dict[str, Any], refresh: bool = False) -> Optional[frozenset]:
    """本机 llama-server 认识的 preset key 集合。

    以 `llama-server --help` 的 --flag 名为准 (router-preset 的 key 即 CLI flag 名)。
    拿不到时返回 None，调用方应跳过过滤 (保持旧行为)。
    结果按 exe 的 (路径, mtime, size) 缓存，升级 llama.cpp 后自动失效。
    """
    exe = llama_server_path(cfg)
    try:
        st = os.stat(exe)
    except OSError:
        return None
    ck = (os.path.abspath(exe), st.st_mtime, st.st_size)
    if not refresh and ck in _KNOWN_KEYS_CACHE:
        return _KNOWN_KEYS_CACHE[ck]

    import re
    import subprocess
    try:
        proc = subprocess.run([exe, "--help"], capture_output=True, timeout=20)
        raw = (proc.stdout or b"") + (proc.stderr or b"")
        text = raw.decode("utf-8", errors="replace")
    except Exception:
        _KNOWN_KEYS_CACHE[ck] = None
        return None
    if not text.strip():
        _KNOWN_KEYS_CACHE[ck] = None
        return None
    keys = frozenset(set(re.findall(r"--([a-z0-9][a-z0-9\-]*)", text)) | set(_PRESET_BUILTIN_KEYS))
    _KNOWN_KEYS_CACHE[ck] = keys
    return keys


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
    # 注意: 旧的 no-mmap (--no-mmap) 在新版 llama-server 里已不存在,
    # 等价替代是 load-mode (auto/none/mmap/mlock/mmap+mlock/dio)。
    # 见 REMOVED_PARAM_KEYS / _migrate_param_keys() 的迁移。
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
          hint="需 llama.cpp 支持 -ncffn",
          description=_D("稠密模型专用：把前 N 层的 FFN 权重留 CPU (llama-server: --n-cpu-ffn)。\n"
                         "老版本 llama.cpp 不认识该参数, 生成 preset 时会被自动跳过并提示。")),
    Param("fit", "自动适配显存", "true", kind="bool", scope="both", category="advanced",
          description=_D("让 llama.cpp 自动调整参数以适配设备显存。")),
    Param("fit-target", "显存余量 (MiB)", "1024", kind="int", scope="both", category="advanced",
          description=_D("fit 模式为每张卡预留多少 MiB 余量。")),
    Param("fit-ctx", "fit 最小 ctx", "4096", kind="int", scope="both", category="advanced",
          description=_D("fit 模式允许的最小 ctx。")),
    Param("load-mode", "加载模式", "auto", kind="choice", scope="both", category="advanced",
          hint="auto/mmap/mlock/dio",
          description=_D("auto 默认；mmap 内存映射；mlock 锁内存；dio 用 DirectIO。")),
    Param("lazy-mode", "懒读张量 (lazy-mode)", "auto", kind="choice", scope="both", category="advanced",
          hint="on/off/auto (需 llama.cpp 支持)",
          description=_D("对超过 4 GiB 的张量按需从磁盘读 (llama-server: --lazy-mode)。\n"
                         "老版本 llama.cpp 不认识该参数, 生成 preset 时会被自动跳过并提示。")),
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
    Param("mmproj-auto", "自动加载 mmproj", "true", kind="bool", scope="model", category="common",
          hint="不勾=不写入, 沿用 llama 默认(自动加载)",
          description=_D("llama-server: --mmproj-auto / --no-mmproj-auto。\n"
                         "启动时自动按 mmproj 路径加载视觉投影；没有 mmproj 路径时该项无作用。")),
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
    # ============== 推测解码 (主池 llama-server) ==============
    # 这一组只服务主池 llama-server (b11200) 的**独立草稿模型** —— spec-draft-*
    # 一整组都在。KVMem 分支 v0.18.0 (ninfer) 的推测解码是另一套方言
    # (--spec / --draft-tokens / --adaptive-mtp / --ngram-*), 属于 scope="kvmem",
    # 见上面「KVMem 分支」段; 老 llama-kvmem-server 的 4 个 spec flag 已废弃。
    # 下面这组里 KVMem 分支 --help 没有的一律 scope="pool": 主池照常写进
    # router-preset.ini, KVMem 页与 KVMem 分支不再产出 (见 POOL_SCOPED_KEYS)。
    Param("spec-type", "推测类型", "", kind="choice", scope="pool", category="common",
          hint="draft-mtp / draft-dflash / draft-dflash2 / draft-dspark / ngram-*",
          description=_D("推测解码类型。MTP/DFlash/DFlash2 模型自动识别。")),
    # -- 以下 10 项 kvmem 分支的二进制**不认识** (见上面的说明), 只能主池用 --
    Param("spec-draft-model", "草稿模型路径", "", kind="text", scope="pool", category="common",
          hint="MTP/DFlash 等 .gguf 路径",
          description=_D("推测解码的草稿模型 .gguf 绝对路径。\n"
                         "⚠ 主池 llama-server 专用 —— llama-kvmem-server 不支持独立草稿模型。")),
    Param("spec-draft-ngl", "草稿 GPU 层数", "auto", kind="text", scope="pool", category="common",
          hint="auto / 99 / 0",
          description=_D("草稿模型放多少层到 GPU。\n"
                         "⚠ 主池 llama-server 专用 —— kvmem 分支没有独立草稿模型, 也就没有草稿的 GPU 层数。")),
    Param("spec-draft-device", "草稿模型设备", "", kind="text", scope="pool", category="advanced",
          hint="需启用推测解码",
          description=_D("草稿模型用哪个 GPU (如 0)。留空自动。\n"
                         "⚠ 主池 llama-server 专用 —— kvmem 分支没有独立草稿模型。\n"
                         "仅在「启用推测解码」勾选且本项也勾选时才写入 preset。")),
    Param("spec-draft-n-max", "草稿最大 token", "3", kind="int", scope="pool", category="advanced",
          description=_D("每轮推测最多生成多少个候选 token (--spec-draft-n-max)。")),
    Param("spec-draft-n-min", "草稿最小 token", "0", kind="int", scope="pool", category="advanced",
          description=_D("少于这个数不触发验证。\n"
                         "⚠ 主池 llama-server 专用 —— kvmem 分支的 --help 里没有这一项。")),
    Param("spec-draft-p-split", "草稿拆分概率", "0.1", kind="float", scope="pool", category="advanced",
          description=_D("触发推测的拆分概率阈值。\n"
                         "⚠ 主池 llama-server 专用 —— kvmem 分支的 --help 里没有这一项。")),
    Param("spec-draft-p-min", "草稿最小概率", "0.0", kind="float", scope="pool", category="advanced",
          description=_D("贪心模式下的最小接受概率 (--spec-draft-p-min)。")),
    Param("spec-draft-threads", "草稿 CPU 线程", "", kind="int", scope="pool", category="advanced",
          hint="需启用推测解码",
          description=_D("草稿模型用的 CPU 线程数。留空跟随主模型。\n"
                         "⚠ 主池 llama-server 专用 —— kvmem 分支没有独立草稿模型。\n"
                         "仅在「启用推测解码」勾选且本项也勾选时才写入 preset。")),
    Param("spec-draft-threads-batch", "草稿批线程", "", kind="int", scope="pool", category="advanced",
          hint="需启用推测解码",
          description=_D("草稿模型批处理线程。\n"
                         "⚠ 主池 llama-server 专用 —— kvmem 分支没有独立草稿模型。\n"
                         "仅在「启用推测解码」勾选且本项也勾选时才写入 preset。")),
    Param("spec-draft-type-k", "草稿 KV K", "f16", kind="choice", scope="pool", category="advanced",
          hint="需启用推测解码",
          description=_D("草稿模型的 K 缓存量化 (llama-server: --spec-draft-type-k)。f16 即可。\n"
                         "⚠ 主池 llama-server 专用 —— KVMem 分支 (ninfer) 的 KV 精度用 --kv-dtype。\n"
                         "仅在「启用推测解码」勾选且本项也勾选时才写入 preset。")),
    Param("spec-draft-type-v", "草稿 KV V", "f16", kind="choice", scope="pool", category="advanced",
          hint="需启用推测解码",
          description=_D("草稿模型的 V 缓存量化 (llama-server: --spec-draft-type-v)。\n"
                         "⚠ 主池 llama-server 专用 —— KVMem 分支 (ninfer) 的 KV 精度用 --kv-dtype。\n"
                         "仅在「启用推测解码」勾选且本项也勾选时才写入 preset。")),
    Param("spec-draft-backend-sampling", "草稿后端采样", "true", kind="bool", scope="pool", category="advanced",
          hint="需启用推测解码",
          description=_D("把草稿采样放到后端 (默认 true)。\n"
                         "⚠ 主池 llama-server 专用 —— kvmem 分支没有独立草稿模型。\n"
                          "仅在「启用推测解码」勾选且本项也勾选时才写入 preset。")),
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
    # ============== KVMem 分支 (ninfer-kvmem-server, v0.18.0) ==============
    # scope="kvmem": 只在「⚡ KVMem」页的参数面板里出现, 不进全局页, 也
    # 不进主路由的 router-preset.ini —— 主 llama-server 不认识 --kvmem-* /
    # --kv-dtype 这些 flag, 写进去会让整个 preset 解析失败、所有模型都加载不了。
    # generate_preset.render() 里另有 runtime 闸门做兜底。
    #
    # 键名与 ninfer-kvmem-server 的 CLI flag **一一对应** (去掉 --), 由
    # kvmem._build_args_ninfer 按白名单产出。ninfer 方言与老 llama-kvmem-server
    # 不同: 模型是位置参数、视觉塔内嵌 (.ninfer, 无 --mmproj)、--kv-dtype 单值、
    # 淘汰策略改用 LRFU。整表即 config.KVMEM_SCOPED_KEYS 的集合。
    Param("max-context", "上下文上限 (token)", "262144", kind="int", scope="kvmem", category="common",
          description=_D("单请求的逻辑上下文上限 (--max-context)。\n"
                         "决定 prompt 最长能到多少 token。")),
    Param("kvmem-budget", "KVMem 工作集 (token)", "20480", kind="int", scope="kvmem", category="common",
          hint="64 对齐",
          description=_D("GPU 常驻历史 token 数 B (--kvmem-budget, 64 对齐)。\n"
                         "越大越占显存, 但长文越不容易掉出工作集。")),
    Param("kvmem-gen-reserve", "生成预留 (token)", "8192", kind="int", scope="kvmem", category="common",
          hint="64 对齐",
          description=_D("decode 阶段追加工作区 R (--kvmem-gen-reserve, 64 对齐)。\n"
                         "⚠ 与 B 之和决定能否加载 —— 本机 RTX 5080 实测 B+R ≤ ~30000。")),
    Param("kv-dtype", "KV 存储精度", "int8", kind="choice", scope="kvmem", category="common",
          description=_D("KV 存储类型 (--kv-dtype)。\n"
                         "bf16 默认; int8/fp8 省显存; rk*/nvfp4/k8v4 为压缩格式。")),
    Param("spec", "推测解码后端", "mtp", kind="choice", scope="kvmem", category="common",
          description=_D("speculative decoding backend (--spec): mtp / dflash / dflash2。")),
    Param("draft-tokens", "每轮草稿数", "4", kind="int", scope="kvmem", category="common",
          hint="1..15",
          description=_D("每轮草稿 token 数 (--draft-tokens, 1..15)。")),
    Param("default-thinking-budget", "思考预算上限 (token)", "8192", kind="int", scope="kvmem",
          category="common", hint="0 = 不思考",
          description=_D("模型自发出思考时的默认预算上限 (--default-thinking-budget)。\n"
                         "客户端仍可用 reasoning_effort 按请求覆盖。")),
    Param("prefill-chunk", "Prefill 分块 (token)", "1024", kind="int", scope="kvmem", category="advanced",
          hint="128 的倍数",
          description=_D("prefill 分块大小 (--prefill-chunk, 128 的倍数)。")),
    Param("max-concurrency", "并发解码请求", "1", kind="int", scope="kvmem", category="advanced",
          hint="1..8",
          description=_D("同时解码的请求数 (--max-concurrency, 1..8)。")),
    Param("kvmem-sessions", "保留历史会话", "1", kind="int", scope="kvmem", category="advanced",
          hint="1..16",
          description=_D("保留的 Host 历史数 (--kvmem-sessions, 1..16)。")),
    Param("default-max-tokens", "默认最大输出 (token)", "16384", kind="int", scope="kvmem",
          category="advanced",
          description=_D("请求未指定 max_tokens 时的输出上限 (--default-max-tokens)。")),
    Param("kvmem-host-mib", "Host KV 预算 (MiB)", "12288", kind="int", scope="kvmem", category="advanced",
          description=_D("Host 原生 KV 载荷预算 H, MiB (--kvmem-host-mib)。")),
    Param("kvmem-sink-tokens", "常驻前缀 (token)", "8192", kind="int", scope="kvmem",
          category="advanced", hint="0 = 一个页",
          description=_D("永远保留的前缀 (--kvmem-sink-tokens, 默认 0 = 一个 64-token 页)。\n"
                         "系统提示放这里, 长对话里不会被挤掉导致跑题。")),
    Param("kvmem-recent-tokens", "常驻近期 (token)", "1024", kind="int", scope="kvmem",
          category="advanced",
          description=_D("永远保留的最近后缀 (--kvmem-recent-tokens, 默认 64 = 一页)。\n"
                         "保证刚才说过的话在任何淘汰策略下都不丢。")),
    Param("kvmem-lrfu", "LRFU 淘汰", "true", kind="bool", scope="kvmem", category="advanced",
          description=_D("按衰减后的检索历史排序要保留的块 (--kvmem-lrfu)。\n"
                         "取代了老版本的 --kvmem-method / --kvmem-query-policy。")),
    Param("kvmem-lrfu-lambda", "LRFU 衰减系数", "0.5", kind="text", scope="kvmem", category="advanced",
          hint="0..1",
          description=_D("历史衰减 (--kvmem-lrfu-lambda, [0,1], 默认 0.5)。")),
    Param("kvmem-lrfu-top-frac", "LRFU 命中比例", "0.9", kind="text", scope="kvmem", category="advanced",
          hint="(0,1]",
          description=_D("命中比例 (--kvmem-lrfu-top-frac, (0,1], 默认 0.9)。")),
    Param("kvmem-score-agg", "打分聚合方式", "max", kind="choice", scope="kvmem", category="advanced",
          description=_D("GQA 头间聚合 (--kvmem-score-agg): mean / max。")),
    Param("ngram-draft-tokens", "N-gram 复制上限", "31", kind="int", scope="kvmem", category="advanced",
          hint="1..63",
          description=_D("每轮最多复制 N 个 token (--ngram-draft-tokens, 1..63)。")),
    Param("ngram-min-match", "N-gram 最短匹配", "12", kind="int", scope="kvmem", category="advanced",
          hint="4..64",
          description=_D("复制所依据的最短匹配长度 (--ngram-min-match, 4..64)。")),
    Param("adaptive-mtp", "自适应 MTP", "true", kind="bool", scope="kvmem", category="advanced",
          description=_D("每轮 MTP 校验 3..draft-tokens 个 (--adaptive-mtp)。")),
    Param("vision", "启用视觉", "true", kind="bool", scope="kvmem", category="advanced",
          description=_D("接受图像/视频并加载视觉塔 (--vision)。\n"
                         "ninfer 的视觉塔内嵌在 .ninfer 里, 没有 --mmproj。")),
    Param("vision-residency", "视觉塔驻留", "cpu", kind="choice", scope="kvmem", category="advanced",
          description=_D("视觉塔驻留方式 (--vision-residency):\n"
                         "resident = 常驻显存; overlay = 借显存 (别名 --vision-offload);\n"
                         "cpu = 跑在 CPU 线程 (无设备视觉显存)。")),
    Param("vision-max-merged", "单图合并上限", "1024", kind="int", scope="kvmem", category="advanced",
          hint="64..16384",
          description=_D("单个媒体项的合并 token 上限 (--vision-max-merged, 64..16384)。")),
    Param("device-profile", "设备调优档", "off", kind="choice", scope="kvmem", category="advanced",
          description=_D("设备调优档 (--device-profile): auto / off / calibrate。")),
    Param("no-thinking", "默认关闭思考", "false", kind="bool", scope="kvmem", category="advanced",
          description=_D("默认不思考 (--no-thinking)。\n"
                         "客户端可用 reasoning_effort 按请求覆盖。")),
    Param("fast-prefill-kernel", "快速 prefill 内核", "false", kind="bool", scope="kvmem",
          category="advanced",
          description=_D("用快速内核 prefill int8 / rk* KV (--fast-prefill-kernel)。")),
    Param("gdn-state-fp16", "GDN 状态 FP16", "false", kind="bool", scope="kvmem", category="advanced",
          description=_D("GDN 循环状态用 FP16 (--gdn-state-fp16, 省一半显存)。")),
    Param("media-cache-mib", "媒体缓存 (MiB)", "1024", kind="int", scope="kvmem", category="advanced",
          hint="0 = 关闭",
          description=_D("保留已准备的媒体 (--media-cache-mib, 默认 1024, 0 关闭)。")),
    Param("wddm-evictable-budget", "WDDM 可回收预算", "true", kind="bool", scope="kvmem",
          category="advanced",
          description=_D("按专用显存预算并把权重 arena 标为可回收 "
                         "(--wddm-evictable-budget)。\n"
                         "WDDM 下显存紧张时也能加载 —— 权重借可回收的共享内存, "
                         "代价是压力大时解码可能变慢。\n"
                         "关掉后引擎退回「专用显存严格规划」, 空闲显存不足会\n"
                         "直接 FATAL 拒绝启动。本机 RTX 5080 16GB 实测: 关掉需\n"
                         "≥14.3 GiB 空闲才加载; 开启后 ~12.8 GiB 空闲即可加载。")),

    # ============== KVMem 分支 (老 llama-kvmem-server, v0.16) ==============
    # 与上面的 ninfer 新引擎**并存**: 同一份 schema, 靠 engine 标记区分
    # (见下面的分组代码)。这批 flag 只有老 llama.cpp 引擎认识 —— ninfer
    # 的 --help 里一个都没有, 所以只给老档位产出 (kvmem._build_args_llama)。
    Param("kvmem", "启用 KVMem", "true", kind="bool", scope="kvmem", category="common",
          description=_D("打开 KVMem 工作集管理 (--kvmem / --no-kvmem, 默认开)。")),
    Param("kvmem-block-tokens", "块大小 (token)", "128", kind="int", scope="kvmem",
          category="common", hint="默认 128",
          description=_D("KVMem 分块粒度 (--kvmem-block-tokens, 默认 128)。")),
    Param("enable-thinking", "默认开启思考", "false", kind="bool", scope="kvmem",
          category="common",
          description=_D("Qwen 思考默认打开 (--enable-thinking, 默认关; 请求可覆盖)。")),
    Param("kvmem-query-policy", "检索查询策略", "user", kind="choice", scope="kvmem",
          category="common",
          description=_D("用最后一条 user 消息做检索查询 (--kvmem-query-policy):\n"
                         "user = 取最后 user span (默认); legacy = 旧行为。")),
    Param("kvmem-method", "淘汰策略", "retrieval", kind="choice", scope="kvmem",
          category="advanced",
          description=_D("保留哪些块 (--kvmem-method): recency / retrieval (默认)。")),
    Param("kvmem-query-last", "查询回退长度", "64", kind="int", scope="kvmem",
          category="advanced",
          description=_D("最后 user span 缺失时的回退查询长度 (--kvmem-query-last, 默认 64)。")),
    Param("kvmem-query-max-tokens", "查询长度上限 (token)", "0", kind="int", scope="kvmem",
          category="advanced", hint="0 = 不限",
          description=_D("把 last-user 检索查询截到这么多 token (--kvmem-query-max-tokens)。")),
    Param("kvmem-query-replay", "查询回放模式", "auto", kind="choice", scope="kvmem",
          category="advanced",
          description=_D("检索查询的回放方式 (--kvmem-query-replay): legacy / auto (默认)。")),
    Param("kvmem-mtp-state", "MTP 状态模式", "replay", kind="choice", scope="kvmem",
          category="advanced",
          description=_D("MTP 状态处理 (--kvmem-mtp-state): snapshots / auto /\n"
                         "replay (默认, 配 MTP 用)。")),
    Param("kvmem-gpu-ratio", "GPU 池占比", "0.50", kind="text", scope="kvmem",
          category="advanced", hint="(0,1]",
          description=_D("槽池显存上限占整卡比例 (--kvmem-gpu-ratio, 默认 0.50)。")),
    Param("kvmem-cpu-gb", "CPU 溢写 (GiB)", "0", kind="text", scope="kvmem",
          category="advanced", hint="0 = 关",
          description=_D("CPU 溢写区大小 GiB (--kvmem-cpu-gb, 0 = 关)。\n"
                         "显存不够时把工作集溢出到内存, 不换引擎就能跑更长上下文。")),
    Param("kvmem-nvme-gb", "NVMe 溢写 (GiB)", "0", kind="text", scope="kvmem",
          category="advanced", hint="0 = 关",
          description=_D("NVMe 文件大小 GiB (--kvmem-nvme-gb, 0 = 关)。")),
    Param("kvmem-nvme-dir", "NVMe 目录", "", kind="text", scope="kvmem", category="advanced",
          description=_D("NVMe 溢写目录 (--kvmem-nvme-dir, 默认 /tmp/kvmem_nvme)。")),
    Param("kvmem-harvest-v", "回收 V 到内存", "false", kind="bool", scope="kvmem",
          category="advanced",
          description=_D("prefill 时把 V 回收进内存 (--kvmem-harvest-v, 默认关)。")),
    Param("kvmem-raw-k-nvme", "原样 KV 存 NVMe", "false", kind="bool", scope="kvmem",
          category="advanced",
          description=_D("把原始 K/V 存 NVMe (--kvmem-raw-k-nvme, 需 --kvmem-nvme-gb)。")),
    Param("spec-kv-dtype", "MTP KV 精度", "f16", kind="choice", scope="kvmem",
          category="advanced",
          description=_D("MTP 草稿 KV 类型 (--spec-kv-dtype, 默认 f16)。")),
    Param("ui-dir", "内置 UI 目录", "", kind="text", scope="kvmem", category="advanced",
          description=_D("从该目录提供静态聊天 UI (--ui-dir)。")),
    Param("verbosity", "日志级别", "3", kind="int", scope="kvmem", category="advanced",
          hint="0..5",
          description=_D("日志级别 (--verbosity, 0 静默 .. 5 调试, 默认 3)。")),
]

# ----------------------------------------------------------------------
# 两套 KVMem 引擎并存 (engine 标记)
# ----------------------------------------------------------------------
# 开关型 KVMem 分支有两个引擎 (引擎由档位的模型工件后缀决定, 见 model_engine):
#   * KV_ENGINE_LLAMA  = 老 llama-kvmem-server (v0.16, 吃 .gguf + --mmproj),
#                        支持 GPU/CPU/NVMe 分层, 上面新加的 --kvmem-* 就是它的;
#   * KV_ENGINE_NINFER = ninfer-kvmem-server (v0.18.0, 吃 .ninfer 单文件),
#                        flag 方言不同 (位置参数 / --kv-dtype / LRFU ...)。
# 两边同名同义的键 (engine 留空) 共用一份, UI 与命令行都两边可见。
KV_ENGINE_LLAMA = "llama"
KV_ENGINE_NINFER = "ninfer"
KV_ENGINES = (KV_ENGINE_LLAMA, KV_ENGINE_NINFER)

# 两引擎共享 (flag 名/语义一致) 的 scope="kvmem" 键。
_KV_SHARED_KEYS = frozenset({
    "kvmem-budget", "kvmem-gen-reserve", "kvmem-sink-tokens", "kvmem-recent-tokens",
})

# 老 llama-kvmem-server 专属的 scope="kvmem" 键 (上面那批)。
_KV_LLAMA_KEYS = frozenset({
    "kvmem", "kvmem-block-tokens", "kvmem-method", "kvmem-query-last",
    "kvmem-query-max-tokens", "kvmem-query-replay", "kvmem-query-policy",
    "kvmem-mtp-state", "kvmem-gpu-ratio", "kvmem-cpu-gb", "kvmem-nvme-gb",
    "kvmem-nvme-dir", "kvmem-harvest-v", "kvmem-raw-k-nvme",
    "enable-thinking", "spec-kv-dtype", "ui-dir", "verbosity",
})

# 给每条 scope="kvmem" 参数打 engine 标记 (没在上面两份名单里的按 ninfer 处理)。
for _p in PARAM_SCHEMA:
    if _p.get("scope") != "kvmem":
        continue
    if _p["key"] in _KV_SHARED_KEYS:
        _p["engine"] = ""
    elif _p["key"] in _KV_LLAMA_KEYS:
        _p["engine"] = KV_ENGINE_LLAMA
    else:
        _p["engine"] = KV_ENGINE_NINFER
del _p


def model_engine(m: Dict[str, Any]) -> str:
    """档位属于哪套 KVMem 引擎: 显式 "engine" 字段优先, 否则按工件后缀推。

    .ninfer -> ninfer 引擎; 其它 (.gguf 等) -> 老 llama 引擎。
    """
    m = m or {}
    explicit = str(m.get("engine") or "").strip().lower()
    if explicit in KV_ENGINES:
        return explicit
    if str(m.get("model") or "").strip().lower().endswith(".ninfer"):
        return KV_ENGINE_NINFER
    return KV_ENGINE_LLAMA


def kvmem_keys_for_engine(engine: str) -> frozenset:
    """该引擎「⚡KVMem」页能编辑的 scope="kvmem" 键 (engine 留空 = 两引擎共享)。"""
    return frozenset(
        p["key"] for p in PARAM_SCHEMA
        if p.get("scope") == "kvmem" and p.get("engine", "") in ("", engine)
    )


# 「⚡KVMem」页在该引擎下能显示的 scope 集合: 老 llama.cpp 引擎还能显示
# scope in (both, model) 的通用项 (ctx-size / cache-type-* / flash-attn ...);
# ninfer 只认自己那套 kvmem flag。ui.py 据此按引擎切换面板。
KV_PANEL_SCOPES_NINFER = frozenset({"kvmem"})
KV_PANEL_SCOPES_LLAMA = frozenset({"kvmem", "both", "model"})


def kvmem_panel_scopes(engine: str) -> frozenset:
    return (KV_PANEL_SCOPES_LLAMA if engine == KV_ENGINE_LLAMA
            else KV_PANEL_SCOPES_NINFER)


def kvmem_panel_common_pool(engine: str) -> frozenset:
    """该引擎面板「常用」区允许出现的 key 全集 (用于校验用户偏好)。"""
    scopes = kvmem_panel_scopes(engine)
    return frozenset(
        p["key"] for p in PARAM_SCHEMA
        if p.get("scope") in scopes
        and (p.get("scope") != "kvmem" or p.get("engine", "") in ("", engine))
    )


SCHEMA_INDEX: Dict[str, Dict[str, Any]] = {p["key"]: p for p in PARAM_SCHEMA}

# runtime 字段取值: 空串 = 走主路由 llama-server (b11200) 的模型池;
# "kvmem" = 走 KVMem 分支 (ninfer-kvmem-server), 只由「⚡ KVMem」页托管。
# "strata" = 走 Strata 分支 (C:\Strata, 自带 OpenAI 兼容服务),
# 只由「🌐 Strata」页托管; 它的参数全在 strata-<档位>.json 里, 不吃 llama flag。
KVMEM_RUNTIME = "kvmem"
STRATA_RUNTIME = "strata"

KVMEM_SCOPED_KEYS = frozenset(p["key"] for p in PARAM_SCHEMA if p["scope"] == "kvmem")

# scope="pool" = **主池 llama-server 专用**, KVMem 页/分支一律不产出。
# 起因是二进制能力差异 (不是 UI 偏好): 主池 b11200 认独立草稿模型 (spec-draft-*
# 一整组), 而 KVMem 分支 (ninfer) 的推测解码是另一套方言 --spec / --draft-tokens
# / --adaptive-mtp / --ngram-* (那几条属于 scope="kvmem")。两套 flag 互不认识,
# 传错了只会被跳过 —— 所以按二进制限定作用域, 主池仍需要这组, 一个都不能删。
POOL_SCOPED_KEYS = frozenset(p["key"] for p in PARAM_SCHEMA if p["scope"] == "pool")

# KVMem 分支 (ninfer) 的推测解码 (spec) 专属 key —— 全部 scope="kvmem"。
# ninfer 的草稿是内建 MTP + 可选 ngram 复制, 没有独立草稿模型:
#   --spec mtp|dflash|dflash2     后端
#   --draft-tokens N              每轮草稿数
#   --adaptive-mtp                自适应 (每轮 3..draft-tokens)
#   --ngram-draft-tokens N        ngram 复制上限
#   --ngram-min-match N           ngram 最短匹配
# 保留这份集合只为「⚡KVMem」页的草稿相关判断; ninfer 命令行由
# kvmem._build_args_ninfer 按 config.KVMEM_SCOPED_KEYS 白名单产出。
KVMEM_SPEC_KEYS = frozenset({
    "spec", "draft-tokens", "adaptive-mtp",
    "ngram-draft-tokens", "ngram-min-match",
})

# 「推测解码」草稿面板 (draft_box) 专属的 key: 面板里已有控件,
# 不在模型参数子表里重复出现 (子表那份会被 draft_* 顶层字段覆盖, 属于静默失效)。
# 全是主池 llama-server 的独立草稿模型 flag —— 「⚡KVMem」页不再渲染草稿面板。
#   DRAFT_BOX_KEYS == DRAFT_QUICK_KEYS | DRAFT_OPT_KEYS | {"spec-type"}
DRAFT_BOX_KEYS = frozenset({
    "spec-type",
    "spec-draft-model", "spec-draft-n-max", "spec-draft-n-min",
    "spec-draft-p-split", "spec-draft-p-min", "spec-draft-ngl",
    "spec-draft-device", "spec-draft-threads", "spec-draft-threads-batch",
    "spec-draft-type-k", "spec-draft-type-v", "spec-draft-backend-sampling",
})

# 草稿面板「高级」区的可选项: 各自带勾选框, 勾了才写入 ini。
# 全是 scope="pool" 的主池专属项 (KVMem 页不渲染草稿面板)。
DRAFT_OPT_KEYS = (
    "spec-draft-device", "spec-draft-threads", "spec-draft-threads-batch",
    "spec-draft-type-k", "spec-draft-type-v", "spec-draft-backend-sampling",
)

# 草稿面板「快捷」区直接显示的 7 个字段: 每个自带勾选框, 勾了才写入 ini。
#   值存在顶层 draft_* 字段, 勾选状态存在 m["draft_en"]。
#   draft_en 缺省为 True —— 老配置没这个字段时保持旧行为 (开关开着就写)。
DRAFT_SHORTCUT_KEYS = (
    "spec-draft-model", "spec-type",
    "spec-draft-n-max", "spec-draft-n-min",
    "spec-draft-p-split", "spec-draft-p-min", "spec-draft-ngl",
)

# 快捷字段: 关闭「启用推测解码」时会从 params 里清掉 (值来自 draft_* 顶层字段)。
# 高级可选项不清 —— 只是不写 ini, 用户的勾选状态要保留。
DRAFT_QUICK_KEYS = frozenset({
    "spec-draft-model", "spec-draft-n-max", "spec-draft-n-min",
    "spec-draft-p-split", "spec-draft-p-min", "spec-draft-ngl",
})

# 「视觉模型 / KV 内存加载」独立区块 (模型页右侧面板顶部) 专属的 key。
# 和 DRAFT_BOX_KEYS 同理: 区块里已有控件, 不在模型参数子表里重复出现。
# 顺序即渲染顺序, 分三组显示。
MMKV_VISION_KEYS = (
    "mmproj-auto", "mmproj-offload", "mmproj-device",
    "image-min-tokens", "image-max-tokens", "mtmd-batch-max-tokens",
)
MMKV_KV_KEYS = (
    "kv-offload", "kv-unified", "cache-ram",
    "cache-type-k", "cache-type-v",
)
MMKV_LOAD_KEYS = (
    "load-mode",
)
MMKV_BOX_KEYS = frozenset(MMKV_VISION_KEYS + MMKV_KV_KEYS + MMKV_LOAD_KEYS)

# schema 里已删除的 key -> 加载时从用户 params 里清掉 (否则会留在配置里当垃圾)。
# 老 llama-kvmem-server 的那批 kvmem flag 已随「双引擎并存」重新加回 schema
# (engine=llama, 见上面的分组), 不再在这里丢弃 —— 之前迁到 ninfer 时被删掉,
# 现在两个引擎都保留, 老档位 (kvmem-coding 等) 要照旧能跑。
REMOVED_PARAM_KEYS = frozenset({
    "no-mmap",
})

# 旧版 schema 里写错的 key -> llama-server 真正认识的 key。
# 错的 key 会让整个 router-preset.ini 解析失败 (所有模型都加载不了)，故加载时自动迁移。
PARAM_KEY_ALIASES = {
    "spec-draft-cache-type-k": "spec-draft-type-k",
    "spec-draft-cache-type-v": "spec-draft-type-v",
    "tensor-read-lazy": "lazy-mode",
}


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
        # 「⚡ KVMem」页参数面板里当「常用」显示的 key (其余进「扩展参数」折叠区)。
        # KVMem 分支 (ninfer) 有自己的一整套 flag, 与主池 llama 参数不通用,
        # 所以这里刻意不混入主池的 ctx-size / parallel / jinja。
        "kvmem_common_keys": [
            "kvmem-budget", "kvmem-gen-reserve", "max-context",
            "kv-dtype", "spec", "draft-tokens", "default-thinking-budget",
            "wddm-evictable-budget",
        ],
        # 老 llama-kvmem-server 引擎的「常用」清单 (方言不同, 单独一份)。
        "kvmem_common_keys_llama": [
            "kvmem-budget", "kvmem-gen-reserve", "ctx-size",
            "kvmem-block-tokens", "enable-thinking",
            "cache-type-k", "cache-type-v",
        ],
        # 监控窗口「监控地址」下拉里出现过的地址 (最近的在最前)
        "monitor_addr_history": [],
        # 上面最多留几条 (1..50)
        "monitor_addr_history_max": 10,
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
    # kvmem 常用项必须落在「⚡KVMem」页当前引擎的面板里 —— 两个引擎各有一份
    # 清单 (ninfer 用 kvmem_common_keys, 老 llama 引擎用 kvmem_common_keys_llama)。
    # 只要清单里出现过无效键 (说明是旧格式), 就整体回默认; 否则保留用户的自定义。
    for field, engine in (("kvmem_common_keys", KV_ENGINE_NINFER),
                          ("kvmem_common_keys_llama", KV_ENGINE_LLAMA)):
        valid_kv = kvmem_panel_common_pool(engine)
        raw_kv = base.get(field)
        raw_kv = raw_kv if isinstance(raw_kv, list) else []
        filtered_kv = [k for k in raw_kv if isinstance(k, str) and k in valid_kv]
        if not filtered_kv or len(filtered_kv) != len(raw_kv):
            filtered_kv = list(_default_prefs()[field])
        base[field] = filtered_kv
    base["monitor_addr_history_max"] = _clean_addr_history_max(
        base.get("monitor_addr_history_max"))
    base["monitor_addr_history"] = _clean_addr_history(
        base.get("monitor_addr_history"), base["monitor_addr_history_max"])
    return base


def _clean_addr_history_max(value: Any) -> int:
    """历史条数上限: 必须是 1..50 的整数, 否则回落默认 10。"""
    try:
        n = int(value)
    except (TypeError, ValueError):
        return 10
    return min(50, max(1, n))


def _clean_addr_history(value: Any, limit: int) -> List[str]:
    """历史记录: 只留非空字符串、去重 (靠前的优先)、按上限截断。

    非字符串 (数字/null) 直接丢掉 —— 手编 user_preferences.json 时很容易
    写成 [123, null], 转成 "123"/"None" 会污染下拉框。
    """
    if not isinstance(value, list):
        return []
    out: List[str] = []
    for item in value:
        if not isinstance(item, str):
            continue
        s = item.strip()
        if not s or s in out:
            continue
        out.append(s)
        if len(out) >= limit:
            break
    return out


def save_prefs(prefs: Dict[str, Any]) -> None:
    with open(PREFS_PATH, "w", encoding="utf-8") as f:
        json.dump(prefs, f, ensure_ascii=False, indent=4)


def common_keys() -> List[str]:
    return list(load_prefs().get("common_keys", []))


def kvmem_common_keys(engine: str = KV_ENGINE_NINFER) -> List[str]:
    """「⚡ KVMem」参数面板里当常用显示的 key (两个引擎各有一份)。"""
    field = ("kvmem_common_keys_llama" if engine == KV_ENGINE_LLAMA
             else "kvmem_common_keys")
    keys = load_prefs().get(field)
    if not isinstance(keys, list) or not keys:
        return list(_default_prefs()[field])
    valid = set(kvmem_panel_common_pool(engine))
    return [k for k in keys if isinstance(k, str) and k in valid]


def extra_params() -> List[Dict[str, Any]]:
    return list(load_prefs().get("extra_params", []))


def all_params_meta() -> List[Dict[str, Any]]:
    """内置 schema + 用户额外参数 (bat 导入的冷门 flag 会落在后者)。"""
    return list(PARAM_SCHEMA) + list(extra_params())


def all_known_keys() -> List[str]:
    return [p["key"] for p in PARAM_SCHEMA] + [e["key"] for e in extra_params()]


# ----------------------------------------------------------------------
# 配置加载/保存
# ----------------------------------------------------------------------
DEFAULT_CONFIG: Dict[str, Any] = {
    "llama_dir": r"C:\llama.cpp",
    "host": "0.0.0.0",
    # **对外端口 = 网关自己的口**。刻意避开 8080: 那是 llama-server 和
    # Strata 的原生默认端口, 谁先起谁占, 于是「换个端口」这种配置就只能去
    # 改别人软件的端口。网关让开, 三个后端就能各用各的原生端口。
    # 远程客户端连的是这个口 (http://<本机IP>:18080/v1)。
    "port": 18080,
    # 当前运行时: 只有一个 Windows 服务 llama-router, 装它就等于装网关。
    # 网关按请求里的 model 字段转给主池 / KVMem / Strata 其中一组。
    "serve_mode": "router",
    "models_max": 1,
    "sleep_idle_seconds": 3600,
    "watchdog": {
        "enabled": False,
        "free_mb_threshold": 2048,
        "check_interval_seconds": 10,
        "min_idle_seconds": 0,
    },
    # 模型自动换挡 (LRU): 新请求要加载的模型不在常驻列表里、
    # 而最旧的常驻模型正在被使用时, 按 busy_policy 处理。
    #   ask  -> GUI 弹窗让用户选 (无 GUI 自动降级为 wait)
    #   wait -> 等它用完再自动卸载
    #   now  -> 立即卸载
    "model_guard": {
        "enabled": True,
        "poll_interval_seconds": 1,
        "wanted_ttl_seconds": 30,
        "busy_policy": "ask",
    },
    # 日志转发: llama-server 没有日志类 HTTP 接口, 远程监控想看到和本地一样的
    # 日志面板, 只能由本机把 logs\\router.*.log 只读地转发出去 (默认关闭)。
    "log_relay": {
        "enabled": False,
        "port": 8099,
    },
    # KVMem 分支 (ninfer-kvmem-server): 单槽独立进程, 不进 router-preset.ini。
    #   install_dir/exe 留空 -> 运行时在 install_dir 下自动探测
    #     ninfer-kvmem-server.exe / llama-kvmem-server.exe (不写死版本号,
    #     换 kvmem 发布包时不用改配置)。
    #   host/port 已废弃 (与主池统一): 运行时一律读顶层的 host/port。
    #   保留字段只为兼容老配置, 服务不再读它们, 档位的 kvmem_port 字段同理。
    # 网关 (gateway.py): 服务 llama-router 的进程本体, 占住顶层 host/port,
    # 按请求里的 model 字段把流量转给主池 / KVMem / Strata 后端。
    #   backend_port: **三个后端共用的内部口**。它们本来就互斥(同一时刻只跑
    #     一个), 所以没必要各占一个 —— 共用之后谁都能用各自的原生默认端口
    #     (llama.cpp / Strata 都是 8080), 不用为了接入网关去改它们的端口。
    #     Strata 更特殊: 它只认命令行 --port, 自己 json 里的 port 不参与
    #     绑定, 所以它用自己那份配置写的值 (照它的 run-*.bat)。
    #   ready_timeout: 切换时等后端 /health 返回 200 的秒数 (含模型加载);
    #   Strata 另用自己的 strata.ready_timeout (它要 1-3 分钟)。
    #   inflight_grace: 停旧后端前等在途请求收尾的秒数。
    #   preload: **默认 false = 按需加载** —— 服务起来不加载任何模型, 收到
    #     带 model 的请求才把那一组拉起来 (Strata 那样 40GB 的模型不会被
    #     一开机就拖进内存)。true = 开机就按 serve_mode 起好, 第一个请求
    #     不用等加载。
    "gateway": {
        "backend_port": 8080,
        "ready_timeout": 300,
        "inflight_grace": 120,
        # --- GPU 忙碌判据 (切档前等占着内部口的那个模型腾出显存) ---
        # 判据是「显存 + GPU 利用率」双重, 不是在途请求数 —— 正在对话但此刻
        # 没有活跃请求的模型, 在「在途请求」眼里是空闲的, 会被误杀。
        # 显存: 掉够这个比例就算对方主动关了 -> 立刻可切
        "vram_drop_ratio": 0.5,
        # GPU 利用率低于这个值算空闲 (刻意放宽, 采样会抖)
        "gpu_idle_pct": 20,
        # 连续几次采样都空闲才认 (防抖), 每次间隔 gpu_sample_seconds
        "gpu_idle_samples": 3,
        "gpu_sample_seconds": 5,
        # 显存还在、但 GPU 空闲满这么久 -> 到点自动切
        "gpu_idle_grace": 300,
        "preload": False,
    },
    "kvmem": {
        "enabled": True,
        # 老 llama-kvmem-server (engine=llama, 吃 .gguf + --mmproj) 的安装目录。
        "install_dir": r"C:\llama.cpp\kvmem-llama.cpp",
        # ninfer-kvmem-server (engine=ninfer, 吃 .ninfer 单文件) 的安装目录。
        # 两套引擎并存: 档位按模型工件后缀自动选 (见 model_engine / kvmem.find_exe)。
        "ninfer_install_dir": r"D:\AI\kvmem\kvmem-v0.18.0-ninfer-windows-rtx50-sm120a",
        "exe": "",
        # 老引擎想用固定绝对路径时写这里 (ninfer 引擎用 "ninfer_exe")。
        "ninfer_exe": "",
        "host": "127.0.0.1",
        "port": 18200,
        "active_id": "",
        # 对应 bat 里的 set CUDA_VISIBLE_DEVICES=0 / CUDA_DEVICE_ORDER=PCI_BUS_ID
        "cuda_device": "0",
        "cuda_device_order": "PCI_BUS_ID",
    },
    # Strata 分支 (C:\Strata): 自带 OpenAI 兼容服务 serve/server.py,
    # 引擎参数全在 strata-<档位>.json 里 (模型条目的 strata_config 指向它)。
    "strata": {
        "enabled": True,
        "install_dir": r"C:\Strata",
        "python": "",          # 留空 -> 自动用 <install_dir>\.venv\Scripts\python.exe
        "server": "",          # 留空 -> 自动用 <install_dir>\serve\server.py
        "active_id": "",
        # 35-55GB 拉到内存要 1-3 分钟, 别拿主池那 300s 去卡它
        "ready_timeout": 900,
        "lazy": False,         # True = 进程先起、模型等第一次请求再加载
        "api_monitor": False,
        "gpu": "",
        "api_key": "",         # 留空 = 后端不鉴权 (它只绑回环, 对外靠网关)
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
        elif k == "model_guard" and isinstance(v, dict):
            out["model_guard"].update(v)
        elif k == "log_relay" and isinstance(v, dict):
            out["log_relay"].update(v)
        elif k == "gateway" and isinstance(v, dict):
            out["gateway"].update(v)
        elif k == "kvmem" and isinstance(v, dict):
            out["kvmem"].update(v)
        elif k == "strata" and isinstance(v, dict):
            out["strata"].update(v)
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


def _migrate_param_keys(params: Dict[str, Any]) -> None:
    """就地把写错的历史 key 换成 llama-server 认识的名字 (幂等)。"""
    for old, new in PARAM_KEY_ALIASES.items():
        if old not in params:
            continue
        if new in params:
            # 新旧并存: 以新 key 为准，丢弃旧的
            params.pop(old, None)
        else:
            params[new] = params.pop(old)
    _drop_removed_params(params)


def _drop_removed_params(params: Dict[str, Any]) -> None:
    """清掉 schema 里已删除的 key (幂等, 见 REMOVED_PARAM_KEYS)。

    no-mmap (--no-mmap) 在新版 llama-server 里已经不存在了, 等价设置是
    load-mode = none。只有在旧配置确实打开过 no-mmap 且 load-mode 没被
    用户单独设过时才迁移, 否则直接丢弃。
    其余 REMOVED_PARAM_KEYS (v0.18.0 ninfer 迁移废弃的老 kvmem flag) 没有
    等价物, 直接丢弃 —— 留着只会让「⚡KVMem」页出现改了也不生效的死项。
    """
    node = params.pop("no-mmap", None)
    if (isinstance(node, dict) and node.get("enabled")
            and str(node.get("value") or "").strip().lower()
            in ("true", "1", "yes", "on")):
        cur = params.get("load-mode")
        if not (isinstance(cur, dict) and cur.get("enabled")):
            params["load-mode"] = {"enabled": True, "value": "none"}
    for key in REMOVED_PARAM_KEYS:
        if key != "no-mmap":
            params.pop(key, None)


def _normalize_model(m: Dict[str, Any]) -> Dict[str, Any]:
    base = {"params": {}}
    base.update(m)
    base.setdefault("enabled", True)
    base.setdefault("alias", base.get("id", ""))
    base.setdefault("mmproj", "")
    base.setdefault("draft_model", "")
    base.setdefault("draft_type", "")
    # 推测解码独立开关 (草稿文件路径是可选项, 模型自带草稿时可留空)
    # 老配置没有该字段: 有草稿路径即视为已启用, 保持旧行为
    base.setdefault("draft_enabled", bool(base.get("draft_model")))
    # 草稿模型专用设置项 (UI 上独立显示，不在参数子表里出现)
    base.setdefault("draft_n_max", "3")
    base.setdefault("draft_n_min", "0")
    base.setdefault("draft_p_split", "0.1")
    base.setdefault("draft_p_min", "0.0")
    # 运行时归属: "" = 主路由 (llama-server) 模型池;
    # "kvmem" = KVMem 分支 (ninfer-kvmem-server), 只由「⚡ KVMem」页托管。
    # "strata" = Strata 分支, 只由「🌐 Strata」页托管。
    base.setdefault("runtime", "")
    # Strata 档位指向自己的引擎配置 json (参数都在那个文件里, 不走 params)
    if base.get("runtime") == STRATA_RUNTIME:
        base.setdefault("strata_config", "")
    # 档位端口 (已废弃): 与主池统一用顶层 port, 运行时不再读这个字段。
    # 保留只为兼容老配置和 bat 导入留痕。
    base.setdefault("kvmem_port", "")
    base["params"] = base.get("params", {}) or {}
    _migrate_param_keys(base["params"])
    # 快捷字段勾选状态: 老配置没有该字段 -> 默认全勾 (等价于旧行为: 开关开着就写)
    de = base.get("draft_en")
    if not isinstance(de, dict):
        de = {}
    base["draft_en"] = {k: bool(de.get(k, True)) for k in DRAFT_SHORTCUT_KEYS}
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
    _migrate_param_keys(params)
    if m.get("runtime") == STRATA_RUNTIME:
        # Strata 档位的参数全在 strata-<档位>.json 里 (serve/server.py 传给
        # strata.exe 的是那个 args 数组), 一个 llama flag 都不吃。
        # 这里补出来的几百个 llama 参数只会让人误以为改了能生效, 所以清空。
        params.clear()
        return
    # kvmem 专属参数只给 kvmem 档位补齐 —— 主池模型不需要, 写了也用不上
    kvmem_only = m.get("runtime") == KVMEM_RUNTIME
    engine = model_engine(m)
    kvmem_common = set(kvmem_common_keys(engine)) if kvmem_only else set()
    for p in PARAM_SCHEMA:
        if p["scope"] == "kvmem":
            if not kvmem_only:
                continue
            # 只补本引擎的 kvmem 项 —— 另一套引擎的 flag 补进去也用不上。
            if p.get("engine", "") not in ("", engine):
                continue
            # kvmem 常用项默认勾上, 否则几十项全要手动勾一遍
            enabled, default = p["key"] in kvmem_common, p["default"]
        elif p["scope"] in ("model", "both", "pool"):
            # scope="pool" = 主池 llama-server 专属 (草稿路径/GPU 层数/线程/
            # 草稿 KV 量化那一组)。ninfer 的 --help 里根本没有这些 flag, 补进去
            # 只会让人以为改了能生效 —— 所以 ninfer 档位一律不补。
            # 老 llama-kvmem-server 本身就是 llama.cpp 系, 认得这些 flag, 照补。
            if p["scope"] == "pool" and not (kvmem_only and engine == KV_ENGINE_LLAMA):
                continue
            enabled, default = _default_enabled(p["key"]), p["default"]
        else:
            continue
        if p["key"] not in params:
            params[p["key"]] = {"enabled": enabled, "value": default}
    for p in extra_params():
        scope = p.get("scope", "model")
        if scope == "kvmem":
            # kvmem 专属的额外参数 (bat 导入的冷门 flag) 只给 kvmem 档位
            if not kvmem_only:
                continue
        elif scope not in ("model", "both", "global", "pool"):
            continue
        elif kvmem_only and scope in ("model", "pool"):
            # 主池专属的额外参数不该塞进 kvmem 档位 (kvmem 面板也看不到它)
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
        "spec-draft-type-k": ["f32", "f16", "bf16", "q8_0", "q4_0", "q4_1", "iq4_nl", "q5_0", "q5_1"],
        "spec-draft-type-v": ["f32", "f16", "bf16", "q8_0", "q4_0", "q4_1", "iq4_nl", "q5_0", "q5_1"],
        "split-mode": ["none", "layer", "row", "tensor"],
        "load-mode": ["auto", "none", "mmap", "mlock", "mmap+mlock", "dio"],
        "lazy-mode": ["on", "off", "auto"],
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
        # KVMem 分支 (ninfer) 的枚举 —— 取自 ninfer --help。
        "kv-dtype": ["bf16", "int8", "fp8", "rk8v4", "rk4v4", "rk4v4-e8",
                     "rk2v4-e8", "nvfp4", "k8v4"],
        "spec": ["mtp", "dflash", "dflash2"],
        "vision-residency": ["resident", "overlay", "cpu"],
        "kvmem-score-agg": ["mean", "max"],
        "device-profile": ["auto", "off", "calibrate"],
        # 老 llama-kvmem-server 引擎的枚举 (取自它的 --help)。
        "kvmem-method": ["recency", "retrieval"],
        "kvmem-query-policy": ["legacy", "user"],
        "kvmem-query-replay": ["legacy", "auto"],
        "kvmem-mtp-state": ["snapshots", "auto", "replay"],
        "spec-kv-dtype": ["f16", "f32", "q8_0", "q5_0", "q4_0"],
    }


def lookup(key: str) -> Optional[Dict[str, Any]]:
    if key in SCHEMA_INDEX:
        return SCHEMA_INDEX[key]
    for e in extra_params():
        if e["key"] == key:
            return e
    return None
