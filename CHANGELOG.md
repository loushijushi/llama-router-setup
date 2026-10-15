# 更新日志 (Changelog)

本项目的所有重要变更都记录在此文件。

格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [Semantic Versioning](https://semver.org/lang/zh-CN/)。

## [Unreleased]

## [0.2.0] - 2026-10-15

> 破坏性版本。移除了 FreeToken 集成，模型分组变为 `主池 / KVMem / Strata` 三组。
> 从 0.1.1 升级的用户请注意：**FreeToken 相关配置与 UI 入口已全部消失**。

### 破坏性变更

- **彻底移除 FreeToken 集成** —— 回到「从来没有过 FreeToken」的状态
  - 删除 `ftbridge.py`（整文件，1197 行）
  - 移除了自动/手动 KV 缓存管理，router 不再干预 FreeToken 的参数与缓存调度
  - 清理历史遗留的 `config.json.bak*` 备份文件（曾被 git 跟踪，内含真实模型名）

### 新增

- **监控窗口进度条** —— 直观显示 prefill / 生成，不用翻日志
  - 新增 `GET /status`：Strata 透传，llama-server 系后端由网关合成同构空壳
  - KVMem 组的进度从其日志解析（`prompt processing` / `n_gen` 行），此前恒为空

### 修复

- **网关卡死**：进度查询 `/status` 曾复用 600 秒的上游超时，而监控每 2 秒轮询一次，
  线程被逐个占住不放，最终网关饿死成「端口还在听、但请求立即被关闭」
- **Strata 档位状态显示错位**：切换档位只回写了 `serve_mode`，没回写
  `config.strata.active_id`。Strata 是单模型进程且只有 `/health`，该字段是唯一的
  真相来源，不回写则面板永远显示上一个档位
- **监控日志停止刷新**：判定是否需要重绘时只比较每文件 buffer 的**长度**，而 buffer
  是 `deque(maxlen=300)`，填满后挤掉旧行、长度恒为 300，此后所有新日志都被判为
  「无变化」。改用单调递增的累计行数
- **日志面板跟跳**：
  - 「在底部」判定误加 `top >= 0.999` 条件，而 `top` 天然等于 `1 - 可见比例`
    （停在最底时约 0.97），导致滚轮/拖动到最底也恢复不了跟跳
  - 滚轮事件在 Tk 应用滚动**之前**就读 `yview()`，读到旧位置被守卫挡掉
  - 暂停浏览时记绝对行号，而重绘会重排文件块导致行号位移，改为记行内容
- **KVMem 推测解码参数**：config 暴露了完整 llama-server 草稿参数族，而
  `llama-kvmem-server` 只支持 `--spec-type` / `--spec-draft-n-max` /
  `--spec-draft-p-min` / `--spec-kv-dtype` 四个，官方明确不支持独立草稿模型
- **KVMem 多模态 prefill 崩溃**：Qwen3.8-27B 的 VL 部分要求至少 1024 图像 token，
  配 512 会导致大 prompt prefill 到 99% 后整体重跑（实测白等 455 秒）
- **bat 导入丢数据**：覆盖已有档位时整体替换，会抹掉 `_note`/`api_key`/手工设定的
  `draft_*`；改为合并
- **bat 重复导入不幂等**：共用同一 gguf 的多个 bat 会互相改写匹配打分所依赖的字段，
  改为按批次快照匹配
- **监控窗口拖动卡死**：`<Configure>` 持续到来时拖动标志永不收尾，日志永久冻结
- **面板底部不是活跃日志**：改为按文件 mtime 做安静/活跃二分，组内顺序保持稳定

### 内部

- 发布流程：`pre_publish_check.py` 隐私门禁 + `build_release.py` 打包后复扫
- `.gitignore` 补 `config.json.bak*`，避免用户配置再次被误跟踪

## [0.1.1] - 2026-09-26

- **彻底移除 FreeToken 集成** —— 回到「从来没有过 FreeToken」的状态
  - 删除 `ftbridge.py`（整文件）
  - `gateway.py`：删掉 `FREETOKEN` 常量与分组、`backend_port_of()` 的 FT 分支、
    `freetoken_is_ready()`、`_freetoken_entry()`、`_command_freetoken()`、
    `_freetoken_daemon*()`、`_freetoken_running()`、`_start_freetoken()`、
    `_stop_freetoken()`、`_merge_freetoken_status()`、`_cap_context_to_kv()`、
    `_sweep_orphan_spawn_workers()`，以及所有 `import ftbridge`
    —— 现在是**三个**模型组：`pool` / `kvmem` / `strata`
  - `config.py`：删掉 `FREETOKEN_RUNTIME` 和 DEFAULTS 里整个 `"freetoken"` 段
  - `ui.py`：删掉「⚡ FreeToken」标签页（及其后端面板）、监控窗口的
    `freetoken.*.log` 日志源、`/engine/*` 轮询噪声白名单；
    标签顺序改为 服务 / 全局 / 模型 / ⚡KVMem / 🌐Strata / 偏好 / 环境
  - `config.json` / `config.example.json`：删掉 `"freetoken"` 段和
    `runtime == "freetoken"` 的档位条目
  - **`/props` passthrough、`_handle_props()`、`_handle_api_tags()` 原样保留** ——
    它们是通用合成端点，不依赖 FreeToken；主池 / KVMem / Strata 的行为不变
  - 历史条目里的 FreeToken 记录保留（那是真实发生过的变更），但对应代码已不存在

### 新增

- **监控窗口进度条** —— 直观显示 prefill / 生成，不用翻日志
  - `gateway.py` 新增 `GET /status`：Strata 有 `/status` 就透传，KVMem / 主池
    （llama-server 没有这个端点）由网关合成一份字段一致的空壳
  - `ui.py` 监控窗口顶部加进度条 + 文案，读 `/status` 每 2 秒刷新：
    生成中 = 定长条 `generated/max` + tok/s；读 prompt（prefill）= 不定长动画 +
    总 token 数；空闲/未加载 = 归零
  - Strata 的 `/status` 顶层只有 `busy/phase/generated/max_tokens/prompt_tokens`
    （没有 `state`/`tok_s`），判定用 `busy`，`tok_s` 缺失时退回日志 tail 的实时速度

### 固定

- **FreeToken 完全归 FT 管，网关退成纯中转**
  - 删掉 `ftbridge.grow_kv_cache()` / `AUTO_KV_TOKENS = 65536` / `DEFAULT_KV_TOKENS`，
    以及配置项 `freetoken.autogrow_kv` / `kv_tokens` / `moe_cache_size`
    和 UI 上的「↩ 重设 KV 缓存」按钮。网关不再替
    FreeToken 决定它的缓存池（KV 与 MoE 专家缓存抢同一份
    `cache_budget_bytes`，网关替它算配比只会拖累它）。
    现在只做三件事：经 daemon 启停 engine、中转请求、**只读**
    `/v1/cache/status` 给界面展示几何。
  - 清空了档位级 `freetoken_args` 里的 `--memory-ratio 0.85`

    `--moe-cache-auto --max-running-requests 4`。它们本来就是 FT 的默认值，
    传不传结果一样，但表态上是网关在代 FT 选调度策略 ——
    而 FT 的自动策略会根据当时显存书动动（实测同一模型
    两次启动分别给 8196/4681 与 8210/5122）。
  - `freetoken.extra_args` 改用 `shlex` 解析，带空格的路径不再被
    按空格切碎（之前 `--mmproj "D:\my models\x.gguf"` 会变三段）。

- **网关不再改 FreeToken 的端口**——这条是桌面版之前一直显示

  「模型没加载、正在加载中」的根因
  - FT 官方常量 (`freetoken/daemon/server.py`)：`DEFAULT_PORT = 1900`、
    `DEFAULT_SERVE_PORT = 1919`。桌面版按 1919 去找引擎。
  - 而 `ftbridge.port_of()` 以前强制返回 `gateway.backend_port` (8080)，
    把引擎塞进了网关的内部口 → 桌面版找不到。
  - 现在两边都对齐官方约定：控制面 **1900** / 引擎 **1919**，
    并且自建 daemon 的 `--state-dir` 也改用桌面版那份，不再写到

    `daemon-router` 子目录（否则 `serve.json` 分役，桌面版看不到）。
  - `ensure_daemon()` 本来就优先探测已有 daemon 并复用，因此桌面版

    开着时两边共用同一个 daemon。

- **对客户端声明 FreeToken 的上下文**：新配 `freetoken.kv_target`（默认 262144）
  - FreeToken 的 `/v1/models` **不带** `max_model_len` / `context_length`，客户端

    （Hermes 等）拿不到只能自己猜，以前它一直按 8196 发。
  - 有就报 `kv_target`（用户设的上限，网关信任它）；没配才退而报

    引擎实际值（只读 `/v1/stats`）。
  - 不读不改 FT 的任何状态。不一致时 FT 会**明确 400**拒绝

    （`prompt is too long: X tokens > N maximum`），不会静默截断。

### 修复
- **UI 拖动窗口发滼**（`<Configure>` 每像素都重做布局）
  - `_layout_models()` 缓存标签尺寸、**宽度不变就不重排**、高度不变就不写

    `configure(height=)`（写了会再触发一次 `<Configure>` 形成自反馈），加重入保护。
    返回值 `placed` 仍然保留。
  - 新增 `_canvas_sync()`，统一处理三处内嵌滚动区：`itemconfig` 会**反向触发**

    `inner` 的 `<Configure>`，两个 handler 每帧互相唤醒。现在宽度真变了才
    `itemconfig`，`scrollregion` 结果变了才写。
- **实时监控的跳回与不跟随**（用户拖回历史时被拉回底部）
  - 文件顺序改为**稳定**：已见过的文件保持原顺序，新文件追到末尾。
    以前按「最后吐字时间」排序，模型一加载某个文件开始吐字 →
    它整个 buffer 位置全变 → 视图回跳到「模型加载行」。
  - 手动回翻改为按**行号**钉住（不是 yview 比例）—— 比例会随总行数变化漂移。
  - 滚动条拖动不再被忽略（原来走 `_on_scroll(None)` 被早返，拉不动也不暂停跟踪，
    下一帧就被 `see(END)` 拉回底部）。只有拉回底部才恢复跟踪，无自动恢复。
  - 删掉 `_refresh_ui()` 里重复的第二次 `_render_logs()` 调用 —— 每秒看看少 3000 次文本插入。
- `bat_import._match_score()` 同路径时返回 0，而调用方要求 `> 0` 才算命中 —— 导致导入同一个 gguf
  的 bat 时**新建重复档位并顶掉原 id**。加了路径命中底分 1。
- `config.py` 补入 6 个 KVMem 参数定义（`kvmem-gpu-ratio` / `cpu-gb` / `nvme-gb` /

  `sink-tokens` / `recent-tokens` / `query-max-tokens`）—— 之前写进 `config.json` 会被**静默丢弃**。

### 变更
- KVMem 两档（`kvmem-coding` / `kvmem-longtext`）合并为单档 `kvmem`，
  `kvmem-fast` 已删除。两档原本的 `budget + gen_reserve` 总量都是 **36864**（官方文档：

  `GPU KV size is budget + gen_reserve`），合并时逐项取宽会把总量顶到 57344，

  显存被挤掉工作区 → 变慢；现在回到 **24576 + 12288 = 36864** 与 `start-coding.bat` 一致。
- 新降 `--flash-attn on`（bat 里没有的唯一偏离项，文档说会传播到 MTP）。
- Strata 新增档位 `一个 IQ3_S 档位`（仅登记自己的 json，未改它的参数）。

### 清理
- 清掉 FreeToken 的僵尸状态：死进程的 `daemon.pid`（PID 56428 / 7244）、残留的

  `serve.json`（port 8080 / 死 PID 56976）以及历次崩溃留下的 4 个 `.bak`。
- 隐私扫描器不再把产品/runtime 名（如 `kvmem`）当成「本机信息」全量误报。


### 变更
- **约定：FreeToken 的加载统一归网关，桌面端只读**
  - daemon 一次只管一个 engine，而桌面端是用 1919 请求的、网关用 8080 ——
    谁最后请求谁赢。为了让「桌面端只读」这条约定真的成立：
    - 加载前先检查引擎是不是已经在 8080 上；不是就用 `/engine/switch` 切回来
      （实测 27–34 秒），而不是 `start` 撞 409
    - FreeToken 页的 daemon 面板在引擎被挪走时**明确提示**并给一个
      **「↩ 抢回 8080」** 按钮（`freetoken.reclaim()`）—— 不用特意发个请求去
      触发切换
    - 网关要腾显存给下一组时会连带停掉桌面端起的那个（16GB 卡上必须这样），
      并写明「很可能是桌面端自己加载的」，免得看着像路由在乱杀
  - 桌面端点加载不会坏掉什么，只是那一小段时间路由这边是空的；下次从路由调用
    就自动回来
- **FreeToken 改为「经它自己的 daemon 起停 engine」—— 桌面版终于看得见调用了**
  - 上一版是网关自己 spawn `ft serve`。那样桌面版**完全看不见**：实测它那个
    daemon 的 `/engine/status` 一直是 `running:false`，engine 起在 1919 或
    8080 都没它什么事。补一份 `serve.json` 再等也没用 —— **认领（adopt）只在
    daemon 启动时发生，运行期轮询不会补认**（实测 25 秒不变）。所以想让用户在
    自己的软件里看到调用，唯一正路是**经它启动**
  - 改动：网关不再 spawn engine，改调 daemon 的公开控制面
    （`POST /engine/start|switch|stop`，`GET /engine/status|health|metrics`，
    端点都在 `ft ctl --help` / `ft daemon --help` 里列着）
  - **两边同时有效**：engine 跑在共用内部口 `8080`，网关转发过去，软件那边也
    能直连 `http://127.0.0.1:8080/v1` —— 实测两个入口都通
  - **只跑一个 engine**：上一版桌面版的 engine 和路由的 engine 是两个进程，
    同时加载就是双份显存；现在共用同一个
  - 换模型走 `/engine/switch`（daemon 内部做「停旧→起新」，封账、等在途请求、
    超时强杀都归它），不自己拼这套收尾逻辑
  - **端口给 8080 而不是它原生的 1919**：四组共用内部口是上一轮刚立下的不变量，
    破坏它的代价（残留清理/互斥判定/端口归属都要多一条分支）大于桌面版上显示
    一个略怪的端口
  - 桌面版没开时网关自启一个 daemon 兜底（`autostart_daemon`，默认开）。兜底
    的 daemon 用**独立的 state-dir**（`.freetoken\daemon-router`），因为
    FreeToken 的 daemon 是**单实例锁**的 —— 实测用桌面版那同一个 state-dir 再起
    一个会被直接拒绝：`another ft daemon holds ...\daemon.pid`，那正好把兜底
    堵死
  - daemon 这条路完全走不通时（比如压根没装 FreeToken），**退回自己 spawn**
    `ft serve`，宁可这样也不能让请求失败 —— 但会在日志里明说「**FreeToken
    桌面版将看不到这次调用**」，不让人白等
  - FreeToken 页多一块「daemon 控制面」面板 + 「🔄 探测 daemon」按钮：桌面版
    看不到调用时先来这里确认一眼 daemon 在不在
  - 监控窗口改 tail daemon 写的 engine 日志
    （`<state-dir>/logs/serve-<port>.log`），因为网关不再自己 spawn，原来的
    `freetoken.err.log` 会是空的

### 修复
- **监控窗口里引擎日志刷满 `GET /v1/models`，每条源端口都不同，看着像「不停换
  端口调模型」且全部「调用成功」**：那些行里的 `127.0.0.1:53454` 之类是
  **HTTP 连接的临时源端口**，不是模型服务端口 —— 每条都是 200，只是网页版和监控
  在高频轮询。根因是聚合 `/v1/models` 要问后端**三遍**（`/health` 状态 +
  `/v1/models` 元数据 + `/v1/stats` 的 KV 容量），1 : 3 放大，叠加监控每 2 秒
  一轮轮询，实测把引擎日志刷到 **2.1 次/秒**
  - 上游查询加 2s TTL 缓存（`GatewayApp._upstream`），按活跃组分桶，切档时失效
  - 实测 20 秒内引擎收到的请求从 **44 条降到 8 条**（-82%），上下文元数据透传不受影响
  - 附：主池那侧 `proxying request to model ... on port 52647` 是 **llama.cpp 自己
    router 模式**的行为（每个模型实例一个临时端口），只有模型重载才会换端口，
    不是网关在换
- **日志不自动跟跳，永远停在最上面，每次都得先点「立即刷新」**：
  `_apply_wrap` 末尾按 `yview()[1] < 0.999` 判定「用户不在最底端 → 暂停跟跳」，
  而**默认折行是在 `MonitorWindow.__init__` 里应用的** —— 那时日志框还是空的，
  **空控件的 `yview()[1]` 不等于 1.0**，于是跟跳在建好之前就被关掉。之后
  `_render_logs` 一直走 `yview_moveto(saved_y=0.0)`，视图被**永久钉死在顶部**
  （实测 40 轮重绘 `top` 恒为 `0.0000`）；而 `_on_scroll` 只在手动滚动时触发，
  所以永远回不到 ON，唯一出路就是「立即刷新」
  - 现在先确认框里已经渲染出内容（`end-1c` 行数 > 1）再判暂停，空框不许关跟跳
  - 实测：修复前 40 轮 `top` 恒为 0.0000；修复后随内容上升并停在末尾
    （`top=0.9756 / bottom=0.9774`），构造完 `_follow_tail` 即为 True
  - 附带：重绘期间用 `_rendering_logs` 守卫忽略滚动事件，每轮先清标志，避免
    「程序自己的滚动」被当成用户翻历史，也避免一次异常就让标志永远为 True
- **日志里一直在「换不同的端口」调模型**：两个独立的坑叠在一起
  - **`ensure_daemon` 每次都无条件 spawn**。它的 docstring 写着「优先用已经活着的
    那个」，但那个检查**只写在 `autostart_daemon=false` 分支里** —— 默认开着
    自启时每次调用都新起一个 daemon。FreeToken 的 daemon 是**单实例锁 + 端口
    独占**，于是次次抢绑失败：
    `ERROR: [Errno 10048] error while attempting to bind on address ('127.0.0.1', 19000)`
    刷满 `freetoken.err.log`，而真正的监听者是别人，我们自己那个变成不监听却
    活着的空壳进程。现在先探活、活着就复用；端口被别人抢赢时也不硬等，探一下就
    认领，自己那个退出失败会被识别出来（而不是继续空等）
  - **心跳被自己的模态框饿死**。`guard_ui.beat` 原来挂在 Tk 的 `after` 上，而
    `_guard_ask` 会**阻塞主线程**等用户点按钮 —— 那个期间 `after` 不跑，心跳一停
    超过 5 秒，服务端守护就判定「桌面版死了」抢着接管；用户点完、心跳恢复，又
    让位。日志里于是「接管换挡 / 服务端守护让位」成对刷屏（实测 5 分钟 12 轮），
    看着像在不停换端口，**其实两个守护谁也没多干一点活**
    - 心跳改跑在**自己的线程**上，不再依赖 Tk 事件循环
    - `GUARD_BEAT_MAX_AGE` 5s → **20s**（一次 GC 或磁盘卡顿就够 miss 掉 5 秒的）
  - 抖动 → 主池反复换挡重载 → **每次重载 llama.cpp 换一个临时端口**
    （`proxying request to model ... on port 52647`），这就是「一直在换不同的端口」
    的观感来源
  - 附注（排查时差点误判）：venv 里的 `python.exe` 只有 46KB，是 **uv 的转发垫片**，
    会 re-exec 到 `AppData\Roaming\uv\python\...` 的真解释器。所以进程列表里每个
    daemon / engine 都**成对出现**（垫片 + 真身，垫片只有 6MB、0% CPU，在等儿子），
    那**不是重复进程、更不是僵尸**，只有一个 daemon 和一个引擎
- **网页版显示「服务未启动」，监控里一直刷 apex 模型调用失败**：活跃组是
  FreeToken / Strata 时，`/props` 被原样透传，而这两个后端**不是 llama-server，
  压根没有 `/props`**（实测 `/props` 和 `/models/sse` 全是 404）
  - 网页版把 `/props?model=...&autoload=false` 当成「服务起没起」的**探针**，
    拿到 404 就判成「服务未启动」，然后**每秒几十次**地重试 —— 实测一分钟几千条，
    把访问日志刷爆，监控里看起来就像「一直在调 apex 而且全失败」（其实那些全是
    `/props` 探针，不是生成请求）
  - 现在：上游答得上就**一字不改**地透传；答不上（FreeToken 恒 404）就自己
    合成一份带 `role=router` 的，**404 绝不外泄**
  - **不注入 `role`**：这是 §23 修过的坑 —— 给 Strata 注入 `role=router` 会让
    网页版以为后面真有个 router，去轮询 `/tools`、`/build.json` 这些只有
    llama-server 才有的接口 → 全 404 → 网页版报错
  - **这里绝不切档**：`autoload=false` 的意思就是「别给我加载」。网页版为了把
    模型下拉框列全会挨个组问 `/props?model=...`，一问就切档等于自己跟自己打架
    （刚点开就发现档位被探针切走了）。实测探完 9 个模型，`active` 纹丝不动
- **外部客户端（Hermes 等）明明配了 262144，却一直按 8196 发**：两个独立的坑叠在一起
  - 其一：`~/.hermes/config.yaml` 里 `base_url` 指的是 **`1919`**（FreeToken 桌面端
    自己的引擎口），**不是网关的 `18080`**。那条路完全绕过网关，自然拿不到上面那条
    自动扩容；而桌面端界面上又是「满的」，特别容易误判成「配置没错啊」
  - 其二：**网关聚合 `/v1/models` 时把上游的上下文长度全丢了**。条目是从 config
    合成的（id / aliases / tags / `owned_by: llamacpp`），FreeToken 那条分支
    **连上游 `/v1/models` 都没取**（只看 `/health` 的状态），所以
    `max_model_len` / `context_length` 一个都没带过来 —— 客户端拿不到就只能瞎猜。
    实测 Hermes 从 `http://127.0.0.1:18080/v1/models` 读到的这两个字段是 `None`
  - 现在：上游的 `max_model_len` / `context_length` / `root` /
    `owned_by` 都会透传（主池 / KVMem / FreeToken 三条路都补上了）；并且会把
    声明值**压到引擎实际能吃下的 KV 容量**（新增 `freetoken.kv_capacity`）——
    万一自动扩容失败，对外报的就是真实值（比如 8196）而不是 262144，这样客户端
    会自己截断，而不是让引擎把超长请求整条丢掉
  - 顺带修了一个 500：`/v1/models` 在 `_cap_context_to_kv` 里用了模块级不存在的
    `freetoken` 名字（`gateway.py` 一律是**惰性导入**，因为别的机器可能没装
    FreeToken），直接 `NameError`
- **FreeToken 的上下文只有 8196，长对话被引擎直接丢弃**
  - 现象：`/v1/stats` 的 `kv.total_pages` 只有 **8196**（而模型声明
    `ctx: 262144`），于是 Hermes 那边表现为「上下文 8196」，长一点的会话根本
    发不出去。引擎日志写得很直白：
    `WARNING Input sequence length 49016 exceeds 8196, request 0 is dropped.`
  - **不是配错了，是漏了一个两步流程里的第二步**。FreeToken 启动时只按当前策略
    自动分配**一点点** KV（本机实测：`--moe-cache-auto` 给出
    `moe_cache_size=4681 num_pages=8196` → KV 只有 0.16 GiB），剩下的预算全给了
    MoE 专家缓存（7.74 GiB），而且**它自己不会再长**。要拿到满上下文必须显式
    `POST /v1/cache/rebuild`。桌面版启动后会调这一步，所以它界面上是满的
  - 扩 KV **必须同时把 MoE 调小** —— 两者抢同一份 `cache_budget_bytes`，引擎
    不会替你缩，只涨 KV 的请求会被拒（它自己算得很清楚）：
    `requested cache (moe=4681 slots, kv=262144 pages, mamba=24 slots) needs
    12.74 GiB > budget 7.90 GiB`
  - 网关现在在 `wait_ready` 之后（早于第一个请求转发）自动做这一步：先读
    `GET /v1/cache/status` 拿到实时几何（`kv_per_token=20480`、
    `moe_per_expert=1775616`、`mamba_per_slot=64389120`、
    `cache_budget_bytes`），按剩余预算折出 MoE 专家数，再 POST rebuild；
    **被拒就按它自己的理由逐次退让**（预算口径对不齐是常态 ——
    `/v1/cache/status` 报 9.40 GiB 而 rebuild 实际用 7.90 GiB，所以先打八折）
  - 实测：KV 8196 → **262144 页**，MoE 专家 4681 → 1430；50410 token 的请求
    通过（修复前同长度直接被丢）
  - 失败不致命 —— 引擎保留旧缓存继续服务，只是上下文短；日志会写明「现在 KV
    只有 N 页，长对话可能被引擎丢掉」，不装作没事
  - 新增 `freetoken.kv_tokens`（0 = 自动跟模型 ctx 走）与
    `freetoken.moe_cache_size`（0 = 自动折算）；FreeToken 页多一个
    **「↩ 重设 KV 缓存」**按钮，统计面板显示 `KV 用掉/总页数`，不足模型上限时
    直接提示
- **「模型加载了但没运行成功」+ `/health` 跟着卡死**：三个叠加的问题（都是
  FreeToken 经 daemon 这条路才暴露的）
  - **身份校验把加载中的 engine 判成了「不是 FreeToken」**。判据当时是
    `instance_id + maintenance`，但看它的 `server/control_api.py::build_health`：
    **只有 `status == "ok"` 那一支才带 `maintenance`**，加载期间那支没有。而
    `wait_ready` 恰恰是在加载期间轮询 —— 于是每一次都被判成「8080 上不是
    FreeToken」，白等到超时。判据改成三支都有的 `instance_id`
  - **`running()` 只看「daemon 说有 engine」就认定 FreeToken 在跑**。但
    FreeToken 的 daemon **一次只管一个 engine**，而桌面版是用它自己的默认端口
    **1919** 请求的 —— 谁最后请求谁赢，我们放在 8080 上的那个会被顶掉。那种
    情况下 daemon 报 `running: true` 而 8080 上什么都没有：网关以为 8080 被占
    （别的组不敢 bind），状态栏一直显示 `freetoken: running`，实际什么都调不通。
    现在要求「引擎在跑 **且** 就在我们这个口上」，否则按没在跑处理
  - **`/health` 被一次卡住的切换堵死**。`snapshot()` 的注释写着「不加锁」，但它
    走的 `_committed()` 里有一句 `with self._lock` —— 而 `self._lock` 被
    `ensure()` **整个**持有（含最长 600s 的 `wait_ready`）。实测一次卡住的切换
    让 `/health` 挂 25s 以上，而 UI 状态栏和监控窗口每秒都在轮询它，18080 上
    堆出几百条 CLOSE_WAIT/FIN_WAIT_2。`_owners` 改用**自己的一把锁**（只持有
    微秒级），锁序 `self._lock → _owners_lock`、反向禁止
  - 另外两处配套：`running()` 已经是我们这个口时不再重起；引擎被顶到别的口时
    `wait_ready` 立刻说清楚（哪个口、为什么、怎么办），不白等到超时；跑着但
    端口不对时走 `switch`（实测 `engine/start` 在已有 engine 时回 **409**）
- **两个 FreeToken 相关的真 bug（都是共用内部口带来的）**
  - **`find_daemon` 会返回一个从没探测成功过的端口**：它为了挨个试候选端口，
    给 `_ctl_json` 传了个 `freetoken` **子字典**，而端口是从**整份配置**里取的
    —— 那个子字典没有 `freetoken` 键，于是**静默退回默认 19000**。结果每个
    候选其实都在问 19000，却以为挨个试过了。本机桌面版的 daemon 正好在 19000，
    所以实机上完全看不出来。改成 `_ctl_json` 强制收**显式 base URL**，没有
    「形状对不上就悄悄走默认」的余地
  - **`running()` 会把主池误判成「FreeToken 还在跑」**：`llama-server` 的
    `/health` 就是 `{"status":"ok"}`，只认状态值的话，切换到主池之后网关会一直
    显示 `freetoken: running`（实测到了），互斥判定跟着一起错。现在加
    `_looks_like_freetoken()` 验身份 —— `instance_id` 是 FreeToken 独有的字段，
    llama-server 和 Strata 都没有

### 新增
- **监控窗口的实时日志可以换行显示了**（默认开）
  - 一行日志动辄几百上千字符（整条 prompt、一串 JSON），原来 `wrap=none` +
    横向滚动条，得多拖才能看全，而且横拖之后完全分不清哪一行是新来的
  - 顶栏加一个「换行」复选框。开着 = 按窗口宽度折行、横向滚动条自动收掉；
    关掉 = 原来的行为（整行不折行，用横向滚动条看整行，某些人看堆栈/表格
    反而更习惯）。选择记在 `user_preferences.json` 的
    `monitor_wrap_lines`，下次开窗口还记得
  - 存偏好而不是 config.json：这是**看**日志的偏好，不是配置的一部分，
    否则配置文件每次看日志都被改写，还容易和「保存」互相覆盖
  - 顺手记下一个 Tk 的坑（实测确认，省得下次再走一遍弯路）：
    **`tk.Text` 没有 `-wraplength` 这个选项**（那是 Label/Button 的），
    设了直接 `TclError: unknown option "-wraplength"`；而且
    `wrap=word` 的 Text **本来就会**按控件当前宽度折行、窗口改大小自动重排
    （同一条 400 字符日志，宽 1100 占 4 个显示行、拉窄到 260 占 13 行），
    所以只需要切 `wrap` 这一个选项，不需要（也不能）去手动回写 wraplength
- **FreeToken (`freetoken` 包) 作为第四个模型组接进网关**
  - FreeToken 是 FlashML 的本地 MoE-offload 推理运行时，自带 OpenAI **和**
    Anthropic 兼容的 `ft serve`。定位和 KVMem / Strata 一样：档位进**同一份
    models 清单**（`runtime == "freetoken"`），由网关统一对外
  - 新增 `freetoken.py`：在 `%LOCALAPPDATA%\FreeToken` 定位它自带的 venv 与
    `ft.exe`、扫描模型目录生成档位、拼 `python -m freetoken.cli serve` 参数、
    读 `/health` 与 `/v1/stats`、拉起桌面版
  - **模型是目录**（`--model-path` 收目录），不是单个 `.gguf`；参数是
    FreeToken 自己的 flag（`--moe-backend` / `--memory-ratio` 等），写在档位
    条目的 `freetoken_args` 里，**一个 llama flag 都不加** —— 所以
    `_ensure_model_params` 遇到它照样清空 `params`，`generate_preset` 也把它
    挡在 `router-preset.ini` 之外
  - 新增「⚡ FreeToken」标签页：与其余三页**同一套骨架**，另加一个
    **「🚀 打开 FreeToken 设置」**按钮拉起它自己的桌面版（单独一行，因为
    桌面版不参与网关这一组的生命周期 —— 两边同时加载同一模型会双份显存）
  - 额外透传它独有的接口：`/v1/messages`（Anthropic 口径）、`/v1/responses`、
    `/v1/stats`、`/v1/requests`。它活跃时网关**完全让路**
    （`PASSTHROUGH_GROUPS` 加了它），一个字节都不改写
  - **修一个真问题**：FreeToken 的 `/health` 在**加载期间也回 200**
    （`{"status":"loading","phase":...}`），而网关原来「200 = 就绪」。于是请求
    在权重还没搬进显存时就被转过去，客户端拿到 503 `model is still loading`。
    `Backend.wait_ready()` 改成认状态：只有 `status == "ok"` 才算就绪，
    `status == "error"` 直接把原因抛出来（不再干等到超时）
  - **安装目录必须在注册表 `ProfileList` 里找**：Windows 服务是
    **LocalSystem** 跑的，而 FreeToken 是 per-user 装的，服务的
    `%LOCALAPPDATA%` 指向 `systemprofile` 什么也看不到。现在按
    「本进程 LOCALAPPDATA → 各用户 profile 的 AppData\Local」逐个探测，
    命中唯一一个就落盘到 `config.freetoken.install_dir`（命中多个则不猜，
    让用户显式指定）
  - 残留认领加 `freetoken.cli serve` / `freetoken.cli` / `ft.exe`：一个 serve 是
    **两个** python 进程（venv 的壳 + uv 管的 cpython-3.12），只认壳会漏掉真正
    占着端口的那个；而 `python.exe` 是通用名，绝不能按镜像名杀
  - 实测：本机 35B A3B 冷加载约 60 秒；切组后 FreeToken 进程树完全回收，
    内部口 8080 交还给下一组

### 变更
- **对外端口默认改成 `18080`，三个后端在内部共用 `8080`**
  - 原来对外也是 `8080`，而 `8080` 恰好是 `llama-server` 和 Strata 的**原生
    默认端口** —— 于是「换个端口」这种配置只能去改别人软件的端口。网关让开，
    三个后端就能各用各的原生端口，谁都不用被改
  - `config.port` 默认 `8080` → `18080`；远程/客户端地址随之改成
    `http://<本机IP>:18080/v1`
  - `gateway.pool_port` / `kvmem_port` / `strata_port` 三个字段合并成
    **`gateway.backend_port`（默认 8080）**。三组互斥，同一时刻只跑一个，
    共用一个口不会撞；Strata 例外，它用**自己档位 json 里写的**端口（照它
    `run-*.bat` 的做法），并且 `strata.port_of()` 现在认档位参数，不再读
    网关字段
  - 顺手修掉一个真 bug：`strata.build_args(cfg, entry)` 之前算端口用的是
    *当前活跃*档位而不是传进来的那个 `entry`，切档位时会带上上一个档位的口

### 修复
- **修「浏览器连不上 18080」：两个网关进程抢同一个对外端口**
  - 现场是 `netstat -ano` 里 `0.0.0.0:18080` 有**两个** LISTENING pid。旧网关
    是上次装服务时留下的孤儿（父进程 nssm 已经没了，它还活着），两个进程
    同时监听，Windows 把连接**随机**丢给其中一个 —— 那个没人管的孤儿会把
    请求吞掉，于是表现为「时好时坏 / 连不上」，日志里却什么错都没有
  - 根因是 `GatewayServer.allow_reuse_address = True`：这个属性在 POSIX 上
    只是「TIME_WAIT 期间也允许重绑」，但在 Windows 上它设的是
    `SO_REUSEADDR`，语义是**允许第二个进程绑同一个口**
  - 改成 `allow_reuse_address = False` 后又踩到第二个坑：上一代进程留下一堆
    TIME_WAIT 时，紧接着的重启会 bind 失败（`WSAEADDRINUSE`）。而浏览器 /
    PWA 会持续轮询 `/health`、`sw.js`，TIME_WAIT 几乎必然存在
  - 最终走第三条路：`allow_reuse_address = False` + 在 `server_bind()` 里
    bind **之前**设 `SO_EXCLUSIVEADDRUSE`。它在 TIME_WAIT 时照样能绑，但
    别的进程抢不走 —— 这才是「服务器独占端口」真正想要的语义
  - `main()` 里 bind 失败不再裸崩：打印占用者的 pid 与命令行、给出下一步
    做法，并返回退出码 2
  - 装服务时新增 `_kill_old_gateways()`：清掉 `nssm stop` 之后仍然活着的
    孤儿 `gateway.py`（`nssm stop` 不保证子进程退干净）。只认领父进程不是
    nssm 的那些，免得把正在服务的进程杀了又被 `AppExit=Restart` 拉起来。
    按 PID + 命令行认领，不按镜像名杀 `python.exe`
- **残留后端改按「端口占用者 PID」精确清理，不再按镜像名杀**
  - `Backend.stop()` 的兜底原来是 `taskkill /IM llama-server.exe` /
    `/IM llama-kvmem-server.exe`（Strata 因为是 `python.exe` 被特意排除）。
    三组共用一个口之后这有两个问题：可能误伤**下一个要起的那一组**，而且
    判据是镜像名而不是「谁占着这个口」
  - 现在统一走 `netstat -ano` 找到监听该端口的 PID → `taskkill /F /T /PID`。
    Strata 后端是 `python.exe`，`taskkill /IM python.exe` 会把桌面版和网关
    自己一起杀掉，这条路本来就不能走
  - 新增 `_free_shared_port()`：占用者命令行**认不出来就明确说明、不动手**
    （宁可起不来也不要误杀），并指出可能要管理员权限才能看到 netstat
  - `_proc_cmdline()` 改走 PowerShell 的 CIM —— 新版 Windows 已经不带
    `wmic` 了，原来那条命令恒定返回空，等于「什么都认不出来」
- **Strata 档位 JSON 的 `port` 现在真的被用上**：之前 `port_of()` 读的是
  `gateway.strata_port`，档位 json 里那个 `port` 一直只是摆设

### 已知限制
- **FreeToken 不支持 `presence_penalty` / `frequency_penalty`，而且是「静默丢弃」**
  - 调查过「在网关这边注入后端不支持的参数」这个想法，结论是**对 FreeToken
    不可行**，记在这里省得以后重走一遍：
    - `api_models.py` 的 `ChatCompletionRequest` **确实声明**了
      `presence_penalty: float = 0.0` / `frequency_penalty: float = 0.0`
      （`extra="allow"`，其余没见过的字段也照吞不误）
    - 但 `generation.py:resolve_sampling()` 只映射 `temperature` / `top_k` /
      `top_p` / `max_tokens` / `ignore_eos` / `stop`
    - 而 `core.py:SamplingParams` 这个 dataclass 的字段只有 `temperature,
      top_k, top_p, ignore_eos, max_tokens, stop_strs` —— **没有任何 penalty
      旋钮**。字段在 HTTP 层解析完就被丢掉了
  - 实测（经网关打到 FreeToken 后端）：
    - `presence_penalty=0.8` → **200**，接受
    - `presence_penalty="不是数字"` → **422**（证明字段真有类型校验，不是被
      `extra=allow` 吞掉的）
    - `logit_bias={"123":1}` → **400 `logit_bias is not supported`** ——
      FreeToken 对「知道自己不支持」的字段会明确拒绝
    - 诱导重复的 prompt + `temperature=0` 贪心解码，`presence_penalty` 取
      0.0 与 2.0 → 输出**逐字节相同**（真实现了的话贪心 argmax 也会被推开）
  - **为什么不能靠网关注入补上**：penalty 需要在 logits 上生效，HTTP 代理
    拿不到 logits，所以不存在「网关自己算一遍」的退路。而且**静默不生效比
    直接报错更糟** —— 配了抑制重复却照样重复，还收不到任何提示，排查时会
    怀疑模型、怀疑上下文、怀疑路由，就是想不到「这个参数被丢了」
  - 另一层代价：网关现在是**逐字节转发**请求体和响应头，任何注入都要新增
    「解析 JSON → 改写 → 再发」，会破坏「FreeToken 活跃时网关完全让路、
    一字节不改」这条原则，换来的却是一个无效功能
  - 四组的实际支持情况（penalty 类参数）：

    | 组 | 支持 | 依据 |
    |---|---|---|
    | `pool` | ✅ | `llama-server --presence-penalty` |
    | `kvmem` | ✅ | `llama-kvmem-server --presence-penalty P [-2,2]` |
    | `strata` | ✅ | `serve/server.py:360-362` 读 `sampling.presence_penalty` |
    | `freetoken` | ❌ | 引擎 `SamplingParams` 无此字段 |

  - 实践结论：FreeToken 上实际能控的只有 `temperature` / `top_k` / `top_p` /
    `stop` / `ignore_eos`；另三组照常传 penalty 即可，网关原样透传，不做任何
    加工

### 新增
- **按需加载：服务起来不再加载任何模型，收到请求才加载对应那一组**
  - 新增 `config.gateway.preload`，**默认 `false`**。老行为是网关开机就按
    `serve_mode` 把后端拉起来 —— 对 Strata 意味着**开机就把 40GB 拖进内存**，
    哪怕你一整天都不用它
  - 现在开机只监听端口：`/health` 报 `active: null`、三组后端全 `stopped`，
    `/v1/models` 照样列全 9 个模型（目录是 config 合成的，不需要后端）
  - 请求带 `model` → 网关按名字解析出组，**同步加载那一组并等就绪**
    （Strata 实测 46s），再把请求原样转过去；客户端一次成功，不用来回重试
  - **没带 `model` 时不猜**：按需加载 + 一个后端都没跑 → 回 503 并写清
    「请在请求里带上 model 字段」，不再悄悄拉起 `serve_mode` 那一组
    （猜错就是白等 1-3 分钟，还占着显存/内存）。`preload: true` 时保留老行为
  - **只读/静态请求一律不触发加载**：`/health` `/v1/models` `/models` `/props`
    `/models/sse` `/slots` `/metrics`，以及任何静态资源（`.js` `.css` `.png`
    `manifest.webmanifest` …）
    - 这条是被实测逼出来的：浏览器里开着的 llama.cpp 网页版，其 **PWA service
      worker 每 60 秒 `GET /sw.js` 一次**，老逻辑把它当成「没带 model 的
      请求」→ 按 `serve_mode` 加载 → 用户明明只想安静待着，模型却自己加载了。
      现在静态资源在后端没跑时回 404（宁可页面缺文件也不要为一张图标加载模型）
  - `serve_mode` 的语义从「开机预热哪一组」变成「**不带 model 的请求用哪一组
    作为默认**」+ UI 上的「设为当前档位」；`ensure()` 里 `active` 为空时按它解析
  - `/health` 增加 `preload` 字段；**`active` 只在该组真的在跑时才报** ——
    老实现后端停了还报着 `active`，状态栏会显示「运行时 主池」而其实什么都没加载
  - `snapshot()` **不再持锁**：`/health` 是 UI 状态栏和监控窗口每秒轮询的，
    加载期间 `ensure()` 握着锁不放（Strata 1-3 分钟），上了锁 `/health` 就跟着
    卡成「网关无响应」
  - UI：「全局」页服务参数区加「开机预加载模型」勾选（默认不勾）；状态栏改成
    问网关「现在真的加载了什么」，按需加载且空载时显示
    `未加载（按需加载，默认 主池）`
  - 新增测试 `test_gateway.py` §22（8 组：只读不加载 / preload 字段 /
    按需加载 / `/props` 轮询不重启 / unload 回空载 / 静态资源不加载 /
    不带 model 不猜 / preload=true 保留老行为）
- **Strata (`C:\Strata`) 作为第三个模型组接进网关：一个端口统一调用**
  - Strata 自带 OpenAI 兼容服务（`serve/server.py` + `strata.exe`），把 125B
    的 MoE 摊到「显存 + 内存 + SSD」上跑。以前它只能自己占 `127.0.0.1:8080`
    起，和本项目的网关**直接抢同一个端口**，两边只能活一个；现在按 `model`
    字段路由，三组共用网关这一个对外口
  - 新增 `strata.py`：定位 `.venv\Scripts\python.exe` 与 `serve/server.py`、
    组装 `--engine strata --config <档位 json>`、扫描 `install_dir` 下可用的
    `strata-*.json`、登记档位、起停服务
- **`--host/--port` 永远显式给成回环口，端口取它自己 json 里写的那个**
     （照它 `run-*.bat` 的做法）：`serve/server.py` 只看命令行的 `--port`，
     json 里的 `"port"` 它自己不读 —— 以前靠 bat 传 `--port 8080` 才固定在
     8080。网关不自己编端口，Strata 听 `127.0.0.1:8080`，与它的启动器一致
   - 档位进**同一份 models 清单**（`runtime == "strata"`），`/v1/models` 三组合并，
     `tags` 里是 `pool` / `kvmem` / `strata`；**远程调用方只需要知道一个
     base URL**，按模型名选组：`http://<本机IP>:18080/v1`
  - **Strata 档位不吃任何 llama flag**（它的参数全在 `strata-<档位>.json`
    里），所以 `_ensure_model_params` 遇到它直接把 `params` 清空 —— 补出来的
    几百个 llama 参数只会让人误以为改了能生效；`generate_preset` 也把它挡在
    `router-preset.ini` 之外（混进去会让整个 preset 解析失败）
  - **就绪超时按组分开**：Strata 要把 ~40GB 拉进内存（实测 27s 加载 +
    首次请求共 46s），拿主池那 300s 去卡它会误判成「起不来」，所以走
    `strata.ready_timeout`（默认 900s）
  - **`Backend.stop()` 绝不 `taskkill /IM python.exe`**：Strata 的后端就是
    python 进程，按镜像名杀会把网关自己和桌面版一起杀掉。只对有专属 exe 名
    的主池/KVMem 做兜底，Strata 靠 `taskkill /T /PID` 走进程树
  - Strata 没有 `/v1/models`，模型状态从它自己的 `/health.loaded` 合成；
    `/props` 万一 404（不同版本）由网关代答一份 `role=router` 的，网页版
    才不会退化成「单模型界面」
  - 新增 UI 页「🌐 Strata」：扫描 `install_dir` 里的 json、登记/移除档位、
    选当前档位、管服务（应用并重装/切回主池/启停/前台）、调就绪超时；
    **页内带参数编辑器，直接读写那份 `strata-<档位>.json`**（见下）
  - 「实时监控」把 `strata.out.log` / `strata.err.log` 一起 tail（三组一份面板）
  - 新增测试 `test_strata.py`（88 项断言）、`test_strata_ui.py`（7 节）、
    `test_gateway.py` §21（10 组）
- **网关 `gateway.py`：服务本体从「后端 exe」换成一个进程内路由**
  - 以前 `llama-router` 服务直接指向 `llama-server.exe` 或
    `llama-kvmem-server.exe`，换运行时 = 换 exe 重装服务；单卡 16GB 下
    两个后端不可能同时常驻，远程调用方也就只能看到其中一组模型。
  - 现在服务跑的是 `gateway.py`：占住顶层 `host/port`（默认 `0.0.0.0:8080`），
    后端只绑 `127.0.0.1:8081`（主池）/ `127.0.0.1:8082`（KVMem），不对外。
    - `GET /v1/models` 由网关**合成**，主池 + KVMem 两组模型都列出来
      （`tags` 里是 `pool` / `kvmem`），并把当前后端的真实 `status` /
      `architecture` 尽力合并进去；`GET /v1/models/<id 或别名>` 也认。
    - 其它请求按 body 里的 `model` 字段路由（**id 和别名都认，大小写
      不敏感**）；认不出（没带 `model` / 不在清单里）就交给当前后端。
  - **互斥切换**：同一时刻只跑一个后端。切换 = 等在途请求收尾
    （`gateway.inflight_grace`，默认 120s，超时强制切）→ 停旧 → 起新 →
    等 `/health` 返回 200（`gateway.ready_timeout`，默认 300s）。
    切换期间到达的请求**同步阻塞**到就绪，客户端一次成功。
  - 响应回写：有 `Content-Length`（非 SSE）时由网关自己读完整体再重算长度
    一并发出；没有长度或 `text/event-stream` 时逐块透传并 `Connection: close`。
  - 新增 `config.gateway` 配置段（`pool_port` / `kvmem_port` /
    `ready_timeout` / `inflight_grace`），老配置载入时自动补默认值。
  - `service.install()` 一律注册网关（`python gateway.py`，`AppDirectory`
    = 项目目录，日志 `logs/gateway.out.log` / `logs/gateway.err.log`）；
    `kvmem.install()`（「应用并重装」）保留原来的档位校验和 `serve_mode`
    标记，内部改为委托 `service.install()`。**切换后端不再需要换 exe**，
    UI 上的「应用并重装 / 切回主池」照旧可用（重装只是让网关重启、读到
    新的 `serve_mode`）。
  - 网关切换后端时把 `serve_mode` 回写进 `config.json`，UI 状态栏改成
    **从磁盘读**，所以状态栏始终跟得上实际活跃组；网关自己的日志也镜像
    一份到 `logs/router.err.log`，「实时监控」和远程 `log_relay` 照旧能
    看见切换过程。
  - 主池的 LRU 换挡守护（`model_guard`）搬进网关进程常驻，UI 关掉也继续，
    `busy_policy=ask` 在无 GUI 的服务里自动降级为 `wait`。
  - 新增测试 `test_gateway.py`（20 节：清单 / 路由 / 互斥切换 / 同步等待 /
    在途收尾 / 超时强切 / SSE / 503 / `role` 改写 / load·unload 代答）；
    `test_serve_mode.py` 第 3/7/8 节
    改成断言「统一装网关」。
- **「从已有 .bat 导入全部参数」**（「⚡ KVMem」页的「📂 导入 bat…」+ 「模型」页的「导入bat」，
  同一个入口）
  - **手动多选文件**（`filedialog.askopenfilenames`），**不自动扫目录、不支持拖入**
    （Tk 9.0 没有内置 DnD，`tkinterdnd2` 也没装）
  - 只认 `.bat`，不认 `.ps1`；`llama-kvmem-server.exe` → 落 KVMem 档位，
    `llama-server.exe` → 落主池条目，其它 exe 直接报错拒绝（不猜）
  - **bat 里每一个 flag 都会进 config**：跑一次 `<exe> -h` 归一短名/别名/取值
    （`-c`→`ctx-size`、`-n`→`n-predict`、`-ctk`→`cache-type-k`、`--no-X` → `X=false`），
    schema 里没有的自动登记成**额外参数**（`user_preferences.json`），一个都不丢；
    导入策略是「先按 schema 补齐所有参数 → **全部关掉** → 只把 bat 出现的置真」，
    bat 是唯一真相
  - 条目匹配顺序：**同 id** → **同 runtime 且同模型路径 + 关键档位参数打分**
    （`kvmem-budget`/`ctx-size`/`kvmem-gen-reserve`/`reasoning-budget` 每对上一个 +1）。
    这样共用同一个 gguf 的多个档位各归各的、不会全灌进第一条；
    覆盖时**保留原 id/alias**，`kvmem.active_id` 不会失效
  - `set K=V` → `config.kvmem.cuda_device` / `cuda_device_order`；
    主池条目的 `--host`/`--port` 是服务级的，只提示去「全局」页改
  - 导入后重生成 `router-preset.ini` 并刷新模型列表 / 档位区 / 两个参数面板 + 汇总弹窗；
    分支档位照旧不进 preset（`render()` 的 `runtime == "kvmem"` 闸门）；
    重复导入幂等（第二次与第一次结果逐字节相同）
  - 新模块 `bat_import.py`（`parse_script` / `parse_args` / `parse_help` /
    `build_model` / `apply_to_config` / `import_bats`），新测试 `test_bat_import.py`
- **新标签页「⚡ KVMem」—— 专门配置 `kvmem-llama.cpp` 分支**
  - `llama-kvmem-server` 是**独立单槽程序**（自述 *Does not patch llama-server*），
    不进主路由池；主 llama-server 也不认识 `--kvmem-*`，
    因此它的条目**只进 config.json、绝不写进 `router-preset.ini`**
    （`generate_preset.render()` 对 `runtime == "kvmem"` 直接跳过）
  - **与主池共用同一份模型清单**（同一条 `config.json models`），
    靠 `runtime` 字段区分；模型页列表里带 `⚡分支` 标记
    （`kvmem_port` 从一开始就不参与运行时，端口统一读顶层 `port`）
  - 档位互斥，一次只跑一个：档位区单选 + `config.kvmem.active_id`，
    「✔ 设为当前档位」/「➡ 去模型页编辑」/「📋 复制调用地址」
  - 运行服务区：**应用并重装 / 切回主池** / 启动 / 停止 / 重启 /
    前台运行 / 刷新状态 / 打开日志，
    与主池共用同一个服务 `llama-router` 和同一个端口；另有 CUDA 设备输入
    （写进服务环境变量，不占配置文件）
  - 参数区 `ParamPanel(scope="kvmem")`，9 个新参数（`kvmem`、`kvmem-budget`、
    `kvmem-gen-reserve`、`kvmem-block-tokens`、`kvmem-query-policy`、
    `kvmem-method`、`enable-thinking`、`ui-dir`、`verbosity`），
    常用列表用独立的 `kvmem_common_keys()`（不跟主模型页的 `common_keys()` 混用）
  - 与模型页共用的 `both` 参数只在**激活的那一页**落盘：
    切标签互相 `reload()`，保存时两个面板都 flush，避免旧值互相覆盖；
    `DRAFT_BOX_KEYS` / `MMKV_BOX_KEYS` 在 kvmem 面板同样排除，一个 key 全项目只出现一次
  - 标签顺序变为 服务(0) / 全局(1) / 模型(2) / **⚡KVMem(3)** / 偏好(4) / 环境(5)，
    `_on_tab_changed` 的环境页索引 4 → 5（否则环境页永不刷新）
  - 新模块 `kvmem.py`：`build_args()` 逐参数对齐分支自带的 3 个 bat
    （`start-coding` / `start-fast` / `start-longtext`），
    另加 `--alias <id>` 便于日志归因 + `--kvmem` 开关；
    CUDA 环境变量走 NSSM `AppEnvironmentExtra`
  - `service.py` 的 `start/stop/restart/is_installed/status` 参数化为
    `name=SERVICE_NAME`，全部共用默认的 `llama-router`
  - 预置 3 条档位，对应分支目录里自带的 3 个 bat（编码 / 快速问答 / 长文），
    三者端口相同（统一 `config.port`），同一时刻只有一个在跑，不会冲突
- **模型页新增「视觉模型 / KV 内存加载」折叠区块**（贴在「推测解码」下方，和它一样：
  开关栏常驻、面板默认折叠、点开关展开；右侧面板因此全部让给模型参数）
  - 左栏高度有限，面板做成**固定高度 + 纵向滚动条 + 鼠标滚轮**，窗口再矮也能滚着看完
  - 窗口够高时 `expand` 会把剩余高度全给面板，能一次看到更多
  - 分三组，共 12 项，每项自带「写入」勾选框：**勾了才写进 preset.ini，不勾就沿用 llama.cpp 默认值**
    - `视觉模型 (mmproj)`：`mmproj-auto`（**新参数**）、`mmproj-offload`、`mmproj-device`、
      `image-min-tokens`、`image-max-tokens`、`mtmd-batch-max-tokens`
    - `KV 缓存`：`kv-offload`、`kv-unified`、`cache-ram`、`cache-type-k`、`cache-type-v`
    - `模型加载`：`load-mode`（auto / none / mmap / mlock / mmap+mlock / dio）
  - `cache-type-k` / `cache-type-v` 虽是常用参数，但按模型各自生效，因此**一并挪进本区块**；
    这 12 个 key（`config.MMKV_BOX_KEYS`）不再在右侧「常用 / 扩展参数」子表里重复出现
  - 注：本机 llama-server（b11200）不含 `kvmem-llama.cpp` 的 `--kvmem-*` 系列参数，
    故未引入；「KV 落内存」用已有的 `cache-ram` + `cache-type-k/v` + `kv-offload` 表达
- **实时监控窗口「监控地址」历史下拉**
  - 地址栏改为可输入下拉框，记录最近用过的地址（默认最多 10 条，可调 1..50）
  - 新增「⚙ 历史设置」：改条数上限 / 点选立即连接 / 清空历史；切到远程地址自动入档，
    非法地址不入档，条数超限自动截断
  - 历史存在 `user_preferences.json`（发布包不含该文件，隐私闸门照常拦截）
- **远程日志读不到时给出可操作的提示**（原来只有一句异常字符串）
  - 超时 → 「远程防火墙拦了端口 N，点『🛡 放行端口』」
  - 拒绝连接 → 「没人监听：要么没勾『日志转发』，要么远程版本过旧（需 ≥0.1.2）」
  - 另有 404 / 主机名解析失败 / 网络不可达 / 转发服务报错 四种针对性文案
  - 提示同时出现在日志区顶部指示器、地址栏 tooltip、模型 tooltip
- **防火墙一键放行**（监控窗口「🛡 放行端口」按钮 + 旁边实时状态）
  - 新增 `firewall.py`：检测 / 添加 / 删除 Windows 防火墙入站规则，
    规则名固定前缀 `llama-router-setup log-relay`，可反复执行（幂等）
  - 非管理员自动提权（UAC，用 `-EncodedCommand` 避开引号转义）；
    拒绝提权时回退成一条可复制的 `netsh advfirewall firewall add rule ...` 命令
  - 加规则后**复验一次**再更新状态，避免「以为放行了其实没有」
  - 根因说明：Windows 对被拦的入站端口是**静默丢包**（不是回 RST），
    所以界面上只表现为「超时」，极难定位
- **模型自动换挡 `model_guard`**：旧模型正在生成时 router 不会自己腾位，
  新模型请求会排队到客户端超时 —— 新增守护盯 router 日志 + `/models` + 子进程
  `/slots` 的 `is_processing`，按 `busy_policy`（ask / wait / now）卸载最旧常驻模型
  （详见 `config.example.json` 的 `model_guard` 段）
- **日志转发 `log_relay`**：llama-server 没有任何日志类 HTTP 接口，远程监控窗口看不到
  对端日志 —— 新增极小只读服务（`/health`、`/logs/<文件名>?since=<偏移>`，支持轮转），
  让远程「实时监控」tail 到与本机一致的内容（默认关闭，开启后监听 `0.0.0.0`，
  详见 `config.example.json` 的 `log_relay` 段）

### 变更
- **服务本体改跑网关 `gateway.py`**（取代上面「App 按 `serve_mode` 指向某个
  后端 exe」的做法，那一条描述的是历史状态）
  - `service.install()` 不再按 `serve_mode` 分发，一律注册
    `python gateway.py`；`serve_mode` 降级为**「当前活跃组」的标记**，
    由网关在切换后回写，`run_foreground()` 仍按 `serve_mode` 直接拉后端 exe
  - 服务自身日志改写 `logs/gateway.out.log` / `logs/gateway.err.log`
    （后端日志仍是 `router.*.log` / `kvmem.*.log`）
- **主池与 KVMem 分支合并成「一个服务 + 一个端口」**（切换运行时 = 重装服务）
  - 只保留一个 NSSM 服务 `llama-router`，App 按新字段 `config.serve_mode`
    （`"router"` / `"kvmem"`，默认 `"router"`）指向 `llama-server` 或
    `llama-kvmem-server`；`llama-kvmem` 服务名退役，装主池时自动清掉
  - **端口统一**：KVMem 分支也用 `config.host` / `config.port`（默认
    `0.0.0.0:8080`），不再各占一个端口 —— `kvmem.host` / `kvmem.port` /
    模型条目的 `kvmem_port` 标记为**已废弃**（字段保留以兼容老配置，运行时不再读）
  - **切换 = 重装这一个服务**（约 1 秒，需管理员）：
    - 「服务」页「安装」→ 先写 `serve_mode="router"` 再重装
    - 「⚡ KVMem」页的「注册服务 / 卸载服务」改名为
      **「应用并重装」/「切回主池」**，分别写 `serve_mode` 后重装
    - 状态栏显示 `主服务: <状态>  运行时: 主池|KVMem 档位 <id>  端口 8080`
  - `kvmem.py`：`SERVICE_NAME` = `service.SERVICE_NAME`；
    `port_of()` / `base_url()` 改读顶层 `port`（对内地址固定 `127.0.0.1`，
    与 watchdog / model_guard 一致）；`build_args()` 输出
    `--host <config.host> --port <config.port>`（不照抄 bat 的 `127.0.0.1:18200`）
  - `service.py`：新增 `serve_mode_of()`；`install()` / `run_foreground()`
    按 `serve_mode` 分发（函数内延迟 `import kvmem` 避免循环依赖）；
    `_kill_old_server()` 改为同时杀 `llama-server.exe` 和
    `llama-kvmem-server.exe`，防止残留进程占着 8080
  - **`watchdog` / `model_guard` 在 KVMem 运行时暂停卸载换挡**：
    kvmem 单槽、没有 router 的 `/models/unload` 语义 —— 模式为 `kvmem` 时
    watchdog 只监显存不卸载，`ModelGuard` 走新的 `active_fn` 闸门空转
    （每轮重读 config，切回主池自动恢复）
  - bat 导入：bat 的 `--host` / `--port` 只记在导入报告里
    （提示「已忽略 —— 与主池统一用 config.host/config.port」），
    不再写 `kvmem.host` / `kvmem_port`
  - portproxy / 防火墙规则不变（本来就都指向 8080）
  - 新测试 `test_serve_mode.py`

### 修复
- **调用 Strata 时网关不该替它「改行为」—— 网页端直接报错**
  - **现象**：Strata 在跑时用浏览器打开网关口，网页端一堆报错
  - **根因**：网关把 **llama.cpp 网页版那一套口径**也套在了 Strata 身上 ——
    `/props` 强行注入 `role=router`、`/models/sse` 自己合成一个 200 的流。
    浏览器里的 llama.cpp 网页版因此以为后面是个 router，接着去轮询
    `/tools`、`/build.json`、`/v1/streams/*` 这些**只有 llama-server 才有的
    接口**（`tools/server/server.cpp:357` 有 `/tools`，Strata 没有），全部
    404 —— 网页版报错。网关等于**替 Strata 改了行为**
  - **改法**：Strata 活跃时网关**完全让路**（`PASSTHROUGH_GROUPS`）
    - `/props`、`/models/sse` 原样转发，一个字节都不改：Strata 的 `/props`
      **根本没有 `role` 字段**（它不是 llama.cpp router），以前是我们注入的；
      它自己的 `chat_template` / `model_alias` 等必须一字不少
    - 它没有的接口就让**它的 404** 说话，网关不合成、不兜底
    - 切回主池 / KVMem 时老口径不变（`role` 仍改写成 `router`）
  - 网关在 Strata 这条路上只做两件事：**停掉其它占资源的后端** + **原样转发**。
    唯一保留的是 `/v1/models` —— 那是网关自己的聚合入口（Strata 压根没实现
    这个接口），不算改 Strata
  - 新增 `test_gateway.py` §23（5 组）
- **「🌐 Strata」页和「模型」「⚡KVMem」两页布局不一致，且模型位置只能手改 json**
  - **三个标签页统一骨架**：顶部说明条 → 左（档位列表 + 按钮行
    `新增/删除/上移/下移/导入` + 基础信息 + 右上角「💾 保存」）→ 右（参数面板）
    → 底部（运行服务）。位置、分栏、按钮排布、列表格式（`[id] alias (停用) ✔`）
    都对齐另两页，肌肉记忆通用；**字段内容按三种软件各自的参数体系**
  - Strata 页照葫芦画瓢补上「基础信息」区（此前只有参数面板）：
    ID / 别名 / **主模型 .gguf** / mmproj / 引擎 json / 启用
  - **模型位置默认自动、也能手动换**：
    - 默认「自动」= 直接读 `strata-<档位>.json` 的 `--native`（Strata 自己
      装出来的配置），输入框置灰只读
    - 取消勾选「自动」→ 输入框可写，「…」挑 `.gguf`；保存时写回 json 的
      `--native`，**且只在 `--ple-gguf` 原本就跟着 `--native` 同一个分片时**
      才一起换（用户特意指定了另一张 PLE 表就不动它）
    - 手动选的路径写的是 json 而不是 config —— json 才是 Strata 真正读的东
      西，不搞「界面一套、实际另一套」
  - mmproj 对应 json 的 `vision.mmproj`；**json 里没有 `vision` 段时拒绝填**
    并说明原因（凭空造一个缺 exe/model 的段，视觉编码器起不来，比没有更糟）
  - 「新增」= 挑一个 `.gguf`，照当前档位**复制一份 json** 换模型（沿用全部
    参数，只换 `--native`/`--ple-gguf`/`vision.model`）再登记成新档位；
    「导入json」= 弹窗列出 `install_dir` 下能用的 `strata-*.json`（带不能用的
    原因），选中即登记
  - 「上移/下移」调 `cfg.models` 里的次序（与另两页同一个操作）；
    换 Strata 安装目录也挪进参数面板（原来单独占一块，位置与另两页不齐）
  - 一个「💾 保存」把基础信息和参数一起落盘（此前参数要单独按一次「保存到
    json」，很容易只改了一半）
  - 修 `_refresh_strata_tab` 没填 `W.model_idx` —— 列表看着能选，保存/移动
    却取不到选中项
- **「🌐 Strata」页光能登记档位、没地方调参数**（第一版把参数面板省了，等于
  把调参的活推给手改 json）
  - 页内加参数编辑器，**直接读写那个档位的 `strata-<档位>.json`** —— 那个
    json 就是唯一真相（`serve/server.py` 的 `engine_args()` 原样透传给
    `strata.exe`），所以不存在「UI 里的值」和「实际生效的值」两套
  - 摆出 16 项常用参数（`--max-context` / `--kv` / `--kv-resident` /
    `--expert-cache` / `--prefill` / `--spec` / `--mtp` / `--pool-workers` /
    `--vram-reserve-mib` / `--pcie-frac` / `--vision` 等），每项带说明、
    布尔项是勾选、`--kv` 和 `--rope-scaling` 给下拉候选
  - **刻意不硬编完整 flag 白名单**：Strata 的 flag 还在快速增加，白名单一
    stale 就会把新参数当成不认识。不认得的 token 一律原样保留，用户也能在
    「其它参数」框里自由增删
  - 保存时**认得的 flag 保持原来的相对顺序**（改个数值不会把文件重排一遍），
    不认得的 token 保留但统一挪到末尾（argv 顺序本身无语义），缩进照原样
    保留，**写前留 `.bak`**
  - `--pack` / `--native` / `--ple-gguf` / `--expert-profile` 作为「这个档位
    是谁」的标识参数**只读** —— 改了会指到别的模型/包
  - 顶层键（`vision` / `lib_dirs` / `layer_split` / `gpus_asked` …）我们不碰，
    保存后逐字节不变；整数项填错直接拦下并弹窗，不会写坏 json
  - 另给「📂 用记事本打开」兜底，冷门 flag 也能改
- **「实时监控」看不见 KVMem 组：模型条被挤出窗口、日志一行都不动**（`ui.py`）
  - **模型条是单行、还跟状态栏抢空间**：模型标签原来 `pack(side=LEFT)`
    塞在 `status_bar` 一行里（同行还有健康指示器），实测行里只剩 961px 而
    8 个标签要 1350px —— 排在最后的 KVMem 三个标签落在窗口外
    （`x=0/1`），看起来就是「这一组的模型没显示」。改为模型条**独占一整行**
    （`models_row`），`_layout_models()` 用 `place` 手工排版、放不下就折行，
    窗口缩放 / 模型增删后都会重排；该函数返回 `[(x, y, 宽), ...]`，
    便于单测在窗口没映射时断言「没被挤出去」
  - **只 tail 主池两份日志**：`_log_paths()` 只回 `router.out.log` /
    `router.err.log`，本地和远程（`log_relay`）都轮询不到
    `kvmem.out.log` / `kvmem.err.log`。改为固定 4 份（`_LOG_FILES`），
    kvmem 两份算**可选**：没跑过 KVMem 不算「找不到日志」；远程连不上
    转发服务时**第一个文件就退出本轮**，否则 4 份 × 3s 超时，
    顶部指示器迟迟不报「日志转发不可达」
  - **面板最下面永远压着一段死日志**：渲染固定「err 两百行 + out 八百行」，
    而主池不跑时 `router.out.log` 几小时不增长，自动跟跳跳到底看到的
    一直是它 —— 感受就是「日志完全没有更新」。改为**每个文件各存一份 buffer**，
    按「谁最后吐过新行」排序，最下面永远是当前活跃那组日志的尾巴；
    顶部指示器补 `N 秒没有新行 (服务空闲?)`（>60s 转橙色），
    读得到文件但内容不动时不再是一片假绿
  - 日志面板标题写明「主池 `router.*.log` + KVMem `kvmem.*.log`」，
    模型 tooltip 补「组: 主池 / KVMem」（按 `/v1/models` 的 `tags`）
  - 新增测试：`test_mon_health.py` case5b / case7、`test_monitor_remote.py` §10f
- **「模型换挡 | 当前运行时不是主池 (KVMem 档位), 换挡已暂停」提示不准，
  且两套守护会抢着卸同一个模型**
  - **判定脱节**：UI 端 `ModelGuard` 的 `active_fn` 读的是**内存里的**
    `self.cfg.serve_mode`，只有保存设置时才刷新；网关切档后会回写
    `config.json`，UI 却没重读 —— 网关已经在跑主池，UI 还按 KVMem 把换挡
    闸着。改为直接问网关 `GET /health` 的 `active`（带 3s 缓存，避免每秒
    开一条新连接；对端不通时维持上一次判断，不抖暂停/恢复日志）
  - **文案不准**：`active_fn` 返回 False 一律报「KVMem 档位」，实际还可能是
    主池后端压根没起来。改成「当前运行时不是主池, 换挡已暂停 (主池一跑起来
    就自动恢复)」并补一条恢复日志；`paused_msg` / `resume_msg` 做成构造参数，
    网关那侧说的是「桌面版在跑，让位」/「接管换挡」
  - **两套守护抢活干**：网关侧和桌面版各跑一个 `ModelGuard`，`busy_policy=ask`
    时只有 GUI 能弹窗，两边同时卸会出现「弹窗已答、但模型早被另一边卸了」。
    现在桌面版每秒写 `logs/guard_ui.beat`，网关看到心跳新鲜就让位
    （`model_guard.ui_guard_alive()`，阈值 5s）；心跳过期 / UI 退出 / 关掉
    守护就自动接管。`py model_guard.py` 独立进程启动时同样先看心跳
  - 新增测试：`test_model_guard.py` §6/§7、`test_gateway.py` §0、
    `test_model_guard_ui.py` §6
- **选了 KVMem 档位加载后，网页版就只剩这一个模型可选**（`gateway.py` 管理接口）
  - **根因是 `/props` 的 `role`**：llama-server 只有带 `--models-preset`（router
    模式）才自报 `role="router"`，KVMem 单模型进程自报 `role="model"`。Web UI 的
    `detectRole()` 一看不是 router 就切「单模型界面」—— `getDisplayOption()` 只回
    当前这一个模型，选择器里自然没有别的可选。改为网关改写 `/props`，
    对外恒报 `role="router"`（网关对外永远是两组模型的目录）
  - **`/models/sse` 改由网关端着**：KVMem 单模型进程根本没有这个接口（404），
    前端每秒吃一个 404，`routerModels` 永远不更新，`load()` 一直等不到 `loaded`
    事件而卡住。现在主池在跑时逐块中继它的原生状态流，KVMem 在跑（或没有后端）
    时由网关按 `/v1/models` 的合并结果自己合成 `model_status`，
    连上仍先补一条 `models_reload`
  - **老接口 `GET /models` 以前 404**（KVMem 没有）：改由网关按同一份目录回答
  - **`POST /models/load` 打到 KVMem 组**：`ensure()` 已经按档位把进程起好了，
    后端却没这个接口（404 → 前端报「加载失败」），改由网关代答 200；
    打到主池组的照旧转给主池 router 在组内换
  - **`POST /models/unload` 打到 KVMem 组**：单模型进程要释放显存只能整个退出，
    改由网关停掉后端并把 `serve_mode` 记回 `router`；它根本没在跑时**不会**
    为了卸载又把它拉起来
  - **KVMem 换档位会开出第二个进程**（`ensure()` 互斥逻辑）：原来只停
    `g != group` 的那一组，KVMem 自己已经跑着时直接 `start()` → 旧进程还占着
    端口。改为档位变了就先把**自己**也停掉再起
  - 新增 `test_gateway.py` §18（`/props` 恒 `role=router`）、§19（`/models`
    老接口 + load/unload 代答）、§20（KVMem 组 `/models/sse` 由网关合成）
- **Web UI 一直判「模型不可用」，但显存里模型明明已经加载**（`gateway.py` 转发链路）
  - **SSE 被整条攒到流结束才发出去**：`send_response_to_backend()` 对没有
    `Content-Length` 的流式响应用 `resp.read(65536)` 读 —— 它会一直阻塞到凑够
    65536 字节或读到 EOF 才返回，于是 `/models/sse` 的 `model_status` /
    `status_change` 一条都发不到浏览器，Web UI 的 `routerModels` 永远停在旧值：
    模型已经进显存仍被判成未加载，点它就去 `POST /models/load`，被后端回 400
    `model is already running`。改为 `resp.read1()`（有多少发多少，不支持时才
    退回 `read()`），流式聊天输出也因此变成真正边生成边发
  - **连上 `/models/sse` 先补发一条 `{"event":"models_reload"}`**：切换后端时
    这条流会被掐断，恰好落在断线窗口里的状态事件就丢了；前端 1 秒后重连，靠
    补发的这一条重新拉 `GET /v1/models`，状态能自己纠正回来
  - **`GET /v1/models/<id>` 恒回 `unloaded`**：单模型查询原来只走
    `model_node()`（纯按 config 合成），从不合并后端真实 `status`，而 Web UI
    正是拿这条判断「模型可用吗」。改为复用 `models_payload()` 的合并结果（id
    和别名都能查）
  - 客户端中途掐断连接（关标签页 / 刷新 / 前端重连 SSE）原先被当成 500 记进
    访问日志并往 stderr 吐整段 traceback，现按 `ConnectionError` 静默收尾；
    真正的未预期异常仍会带 traceback 写进 `logs/gateway.err.log`
  - 新增 `test_gateway.py` §16（SSE 逐块透传 + `models_reload` 补发，断言后端
    首条状态事件 < 0.8s 到）、§17（单模型查询带真实 status / 别名 / 404）
- **显存守护（watchdog）从不卸载模型**：router `/models` 返回的 `status` 是
  `{"value": ..., "args": [...]}` 对象，旧代码直接拿它跟 `"sleeping"` / `"loaded"`
  字符串比较 → 永远不相等 → `_pick_unload_target()` 恒返回 `None`。
  新增 `_status_value()` 统一取出 `value` 再比较
- **参数里的失效 key `no-mmap`**：新版 llama-server 已无 `--no-mmap`
  （实测 b11200 `--help` 全文 0 处命中），留着会被 `llama_known_keys()` 挡掉
  - 从 `PARAM_SCHEMA` 删除，并在 `_migrate_param_keys()` 里自动清理（幂等）
  - 旧配置若真的勾过 `no-mmap=true`，自动迁移为 `load-mode=none`（语义等价）；
    没勾的直接丢弃，用户已单独设过 `load-mode` 时不动它
  - `generate_preset` 再加一道防御：配置里即便残留 `no-mmap` 也不写入 preset
- **切换会被「读请求」卡住 120 秒，还会来回抖动**（`gateway.py` 竞态）
  - `route()` 在拿到状态锁**之前**读 `state.active`：别人正在切换时，
    一个不带 `model` 的请求（浏览器开着 KVMem 聊天界面时每 2 秒一次的
    `GET /models/sse`）会读到旧值；等它拿到锁时切换已经完成，于是立刻
    把后端**再切回原来那一组** —— 表现为刚切过去就弹回来 + 一次 120s 空等
  - 改为 `route()` 认不出 `model` 时返回 `group=None`，由 `ensure()` 在
    **锁内**解析成当前活跃组（管理接口「只该由主池回答」的兜底也一并挪进锁内），
    并把解析结果写回 in-flight 记录
  - 只有**带请求体的方法**（POST/PUT/PATCH/DELETE）才算「在途」：
    常驻的 `GET`（模型列表 SSE）掐断后客户端会自己重连，不该让每次切换
    都先空等 `gateway.inflight_grace` 再强切
  - `GET /health` 新增 `inflight` 字段（按组的在途计数），方便看出是谁在挡切换
- **监控地址历史清洗会污染下拉框**：原实现对非字符串条目做 `str()`，
  于是 `"123"` / `"None"` 会混进历史列表 —— 改为直接丢弃非法条目

---

## [0.1.1] - 2026-09-26

### 新增
- **发布隐私保障：本机配置绝不会被打进发布包**（你自己的 `config.json` 全程只读，不受影响）
  - 发布包**只含 `config.example.json` 空白模板**，不含 `config.json` /
    `user_preferences.json` / `router-preset.ini` / `logs/`（首次运行由程序自动生成空配置）
  - 三层防护：
    1. `.gitignore` + git 未跟踪（防推到公开仓库）
    2. `build_release.py` 的 `EXCLUDE_FILES` 与新增的 `PRIVATE_FILES` 双重兜底
    3. 打包后 `verify_zip_privacy()` **复扫 zip 里每个文本文件**，
       对照本机 `config.json` 里的模型路径 / 模型名 / `api_key` 逐个比对
  - 一旦违规：打印清单（敏感值打码，不原样输出 api_key）、**删除 zip**、退出码 1
  - `pre_publish_check.py` 新增第 8 项隐私检查（跟踪状态 / `.gitignore` /
    打包排除项 / 跟踪文件内容比对），违规直接判定「不建议发布」
  - CI `release.yml` 补 `$LASTEXITCODE` 校验 + zip 内容复核
    （原实现 PowerShell 不会因非零退出码让步骤失败，等于隐私闸门形同虚设）
  - 新增 `test_release_privacy.py` 8 例，覆盖：git 跟踪状态、排除清单、
    污染模板必须打包失败且删包、干净项目打包成功、真实包零泄漏、
    **打包前后 `config.json` 指纹完全一致**
- **偏好页左侧「所有参数」列表支持搜索 + 排序**（100+ 项里找参数不再靠肉眼扫）
  - 搜索框：大小写不敏感，匹配 key / 标签 / 作用域 / 分类，**中文标签也能搜**（搜「物理批」命中 `ubatch-size`）
    - 查询串开头的 `-` / `--` 会被忽略：搜 `-ub`、`--ubatch-size`、`ub` 结果一致
    - 右上角实时显示「共 N 项, 匹配 M 项」，按回车跳到第一条匹配
  - 排序：点列头 升序 → 降序 → 恢复默认（schema 原始顺序），列头显示 ▲ / ▼ 标识当前排序
    - 可排序列：`key`、标签、作用域、分类；切到另一列自动从升序开始
  - 搜索不影响右侧「已设为常用」列表；过滤状态下点 `>> 加入` 仍按真实 key 工作
  - **顺带修了列错位**：原来候选树只有 `scope` / `category` 两列却塞了三个值，
    标签被挤进「作用域」列、分类整列丢失 —— 现为 `标签 / 作用域 / 分类` 三列各就各位

### 变更
- 推测解码改为**独立启用开关**：模型页新增「启用推测解码」勾选项，不再靠填草稿路径隐式开启
  - **草稿文件变为可选项**：模型自带草稿（MTP 权重内置于主模型）或用 ngram 时可留空，只选推测类型即可
  - 关闭开关后 `spec-type` / `spec-draft-*` 一律不写入 preset.ini（含旧配置 params 残留）
  - 老配置无 `draft_enabled` 字段时按旧行为推断（有草稿路径 = 已启用），无需手工迁移
- **6 个草稿高级参数改为可选项**，放在**推测解码面板底部的「高级 (可选)」区**，
  每项自带勾选框，不再藏在参数子表第 60+ 项里：
  `spec-draft-device`、`spec-draft-threads`、`spec-draft-threads-batch`、
  `spec-draft-type-k`、`spec-draft-type-v`、`spec-draft-backend-sampling`
  - 原来被 `startswith("spec-draft-")` 一刀切排除，schema 定义了却永远用不到
  - 上一版改放到「显示扩展参数」区，但排在 75 项里的第 64-69 位，几乎看不到 —— 已移回草稿面板
  - **不勾 = 不写入 ini**；勾了但草稿总开关关闭时也不写入，且不丢用户设置
  - 草稿面板专有的 13 个 key（`config.DRAFT_BOX_KEYS`，含 `spec-type`）不再在参数子表重复出现
    （子表那份会被草稿面板的值覆盖，属于静默失效）
- **推测解码面板里直接显示的 7 个字段，每个都加了独立勾选框**（勾了才写入 preset.ini）：
  草稿模型 .gguf、推测类型、`n-max`、`n-min`、`p-split`、`p-min`、草稿 GPU 层数
  - 勾选框自带字段名，取消勾选后配对控件自动置灰（视觉上即表示这项不生效）
  - 状态存进模型配置的 `draft_en` 字段；老配置没有该字段时自动补为「全勾」，
    等价于旧行为（开关开着就写），**零迁移、输出逐字节不变**
  - 取消勾选的字段既不走顶层快捷字段，也不走 params 残留，ini 里彻底不出现

### 修复
- **推测解码面板的字段勾选框，取消勾选一次就永久灰掉、再也点不回来**
  - 根因：`_apply_draft_en` 联动置灰时，把勾选框自己也列进了禁用名单，
    取消勾选 → 勾选框连同配对控件一起变成 `disabled` → 死锁
  - 现在勾选框本身永远保持可点，只置灰它配对的输入控件（输入控件恢复后可编辑）
  - 勾选框单独存放在 `_draft_en_cbs`，`_apply_draft_en` 里再加一道 `is cb` 跳过保护
- **一个错误 key 导致整个 router-preset.ini 解析失败，所有模型都加载不了**
  - 根因：`spec-draft-cache-type-k` / `-v` 是写错的 key，llama-server 只认
    `spec-draft-type-k` / `-v`；preset 解析器遇到未知 key 直接放弃整个文件
  - 实测确认：`option 'spec-draft-cache-type-k' not recognized in preset 'bad'`
    → `failed to parse server config file` → 一个模型都加载不了
  - 已改名，并在 `config._normalize_model` / `_ensure_model_params` 里自动迁移旧 key
    （保留 `enabled` / `value`，新旧并存以新 key 为准，迁移幂等）
- **同类问题治本**：新增 `config.llama_known_keys()`，按 `llama-server --help` 取本机
  真正认识的 flag 集合（按 exe 的 mtime/size 缓存，升级 llama.cpp 自动失效）
  - `generate_preset` 生成时跳过本机不认识的 key，UI 保存后在日志里提示被跳过的项
  - 用户在偏好页手写的额外参数打错字，不再会毁掉整个 preset
  - 实测本机 build 10685 不认识 `n-cpu-ffn`、`lazy-mode`，均被自动跳过
- `tensor-read-lazy` 是写错的 key，llama-server 真名是 `lazy-mode`（已改名 + 旧 key 迁移）
- 启动闪退：`manager-ui.bat` 崩溃时红色报错一闪而过看不清
  - 新增 `launch_ui.ps1` + `launch_ui_logged.cmd`：隐藏窗口启动 UI，stderr 写入 `logs\ui_launch.log`
  - 启动失败时窗口停住显示完整错误并暂停；正常时几秒后自动关闭
  - **不再残留黑色 cmd 窗口**（旧版最小化窗口会一直驻留到 UI 关闭）
  - `ui.py` 崩溃兜底：traceback 保存到 `logs\ui_crash.log`
  - 找不到 Python 且安装指引窗口也打不开时，暂停显示提示
  - 偏好栏箭头方向反转已修复（`>> 加入` / `移出 <<`）
- `'e' is not recognized` 随机报错（新机器必现）
  - `find_python.cmd` 改为纯 ASCII（中文 REM + `chcp 65001` 会让 cmd 解析错乱）
  - 去掉 `echo | findstr` 管道，改用延迟扩展字符串替换过滤 MS Store 占位
- `show_python_missing.ps1` 在中文 Windows 上打不开
  - 加 UTF-8 BOM（PowerShell 5.1 无 BOM 按 GBK 读，中文破坏语法）
  - 修复 winget 按钮命令的嵌套引号错误，改用 `-EncodedCommand`
- 偏好页「上移/下移」看起来无效，且顺序与模型页参数面板不一致
  - 根因：已选常用列表始终按内置 schema 固定顺序渲染，不读 `common_keys` 的存储顺序
  - 现在偏好页列表严格按 `common_keys` 顺序显示，与模型页常用区顺序一致
  - 偏好页增删/移动常用参数、增删额外参数后**立即重建参数面板**，无需切页再切回
  - 重复 key 去重保护；用户手改配置文件遗留的未知 key 不再静默丢失
- 模型页从「启用推测解码」的模型切到未启用的模型时，草稿面板不隐藏
  - `_sync_draft_box` 原来只看折叠状态不看启用开关，现两者都判断
- **实时监控窗口「日志不动」**
  - 根因一：`_tail_loop` 把整个读日志循环包在 `except Exception: continue` 里，
    任何读取失败都被静默吞掉，界面上只表现为日志停止刷新，毫无提示
    现在读取异常 / 日志文件不存在 / 行数，都会在窗口顶部指示器里直接写出来
  - 根因二：「重读日志」按钮直接调用 `_refresh_ui()`，而它内部会再挂一条 `after(500)`
    定时刷新链 —— 每点一次按钮就多一条，链成倍堆积会把界面拖垮
    现在只做一次渲染，定时链永远只有一条
  - 指示器文案：绿色「日志读取正常 · 已加载 N 行」/ 橙色「找不到日志: …」/
    红色「日志读取异常 — 具体异常」

---

## [0.1.0] - 2026-09-21

### 修复
- 偏好栏箭头方向：`>> 加入`（向右）和 `移出 <<`（向左），与实际移动方向一致
- 更新至 v0.1.0 版本号

---

## [0.0.5] - 2026-09-07

### 新增
- 模型编辑面板新增 `base_url` 和 `api_key` 字段（可选）
  - `api_key` 输入框默认显示为 `****`，新增眼睛图标按钮可切换明文/密码显示
  - 默认值为空（留空 = 用本机 llama.cpp / 无密钥）
- 首次运行向导：空配置时弹窗引导用户设置
- **首次运行向导**：空配置时弹窗引导用户设置
- **`pre_publish_check.py`**：发布前自动检查脚本
  - 检查 VERSION 是否已递增
  - 检查 README 是否有 TODO/过时示例
  - 检查 CHANGELOG 是否有本次版本条目
  - 检查工作区是否干净
  - 加 `--auto-fix` 可自动从 git log 生成 CHANGELOG [Unreleased] 段
- **`CHANGELOG.md`**：正式记录每次发布的变更
- **release.yml 增强**：从 CHANGELOG.md 自动提取本次版本的更新内容作为 release notes
  - 不再用 GitHub 默认的"自动生成 release notes"（质量太差）

### 变更
- **模型 tab 布局重构**：左侧（模型列表 + 基础信息 + 折叠的推测解码）| 右侧（全部给模型参数）
- 模型参数面板从 ~350px 提升到 **600+px**（占满右侧栏所有高度）
- 基础信息改 2 列紧凑布局（6 行 → 3 行 + 启用）
- 推测解码默认展开（一般不用改）
- 顶部保存条缩短（更紧凑）
- 移除底部重复的保存按钮和提示文字
- 顶部保存按钮改用标准 ttk 样式（不再用橙色高亮），与界面整体风格统一
- **release.yml** 改为从 CHANGELOG.md 自动提取本次版本条目作为 release notes

### 修复
- 修正 VERSION 文件不同步问题（之前发布没更新）
- 模型编辑器的 `base_url` 缺失问题（之前只在 README 中提到）
- 顶部提示文字改用柔和的颜色，不再使用橙色等过于鲜艳的背景色

### 项目流程
- **新规则**：以后不会自动发布
  - 修改 → commit + push（main 分支）
  - 想发布时显式说"发布 v0.0.x"
  - 我会先跑 `pre_publish_check.py` 确认一切就绪
  - 然后打 tag 触发 GitHub Actions 自动打包 + 发布

---

## [0.0.4] - 2026-09-06

### 新增
- 模型配置新增 `base_url` 和 `api_key` 字段（可选，留空使用本机 llama.cpp）
- 实时监控窗口：自动跟跳 / 暂停 / 立即刷新 模式
- 实时监控窗口：鼠标悬停模型名显示参数 tooltip，点击打开 JSON 详情
- 实时监控窗口：日誌按类型高亮（info/warn/error/load/slot/speed）
- 首次运行向导：空配置时弹窗引导用户设置
- GitHub Actions：推 tag 自动打包 + 发布 Release
- CI：每次 push 自动检查语法 / CRLF / .gitignore / 必需文件

### 变更
- 模型 tab 布局：左侧（模型列表 + 基础信息 + 推测解码折叠面板） | 右侧（全部给模型参数）
- 基础信息改 2 列布局（6 行 → 3 行 + 启用）
- 推测解码默认折叠，需要时手动展开
- 主标签顺序改为：服务 / 全局 / 模型 / 偏好 / 环境

### 修复
- 启动监控窗口时不再残留空白 cmd 窗口（用 VBS 静默启动）
- Python 3.14 + py.exe launcher stdin 重定向问题（find_python.cmd 优先用真路径）
- bat/cmd 文件统一改为 CRLF 行尾
- .bat/.cmd 文件中的中文字符串在 cmd 下乱码（加 chcp 65001 + PYTHONIOENCODING=utf-8）

---

## [0.0.3] - 2026-09-05

### 新增
- 模型编辑表单新增 `base_url` 和 `api_key` 字段
- 实时监控窗口（实时日志 + 模型状态 + HTTP 端点）
- README + 中文 FAQ 文档

### 修复
- NSSM 日志文件句柄偶尔卡住，加"重读日志"和"重启服务"按钮
- 监控窗口 tooltip 闪烁问题
- 监控窗口在用户查看历史时跳到最新（加跟跳 / 暂停逻辑）

---

## [0.0.2] - 2026-09-02

### 新增
- 首次运行向导（空配置弹窗引导）
- 实时监控窗口
- config.example.json 模板（带详细注释）
- 项目主页、README、CI 工作流
- publish_to_github.bat 开发者工具

### 修复
- build_release.py 在 Windows runner 上 UTF-8 编码问题

---

## [0.0.1] - 2026-09-01

### 新增
- 第一个可用版本
- 核心功能：图形界面配置 llama.cpp 路由模式
- Windows 服务管理（用 NSSM 注册 llama-router 服务）
- 可选 watchdog 服务（显存守护）
- 环境检查（Python / tkinter / NSSM / llama.cpp）
- 200+ 个 llama-server 参数的支持（常用 / 扩展分类）
- 完整的 .bat / .cmd / .ps1 启动脚本
