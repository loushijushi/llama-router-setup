# llama.cpp Router 管理工具

[![Release](https://img.shields.io/github/v/release/loushijushi/llama-router-setup)](https://github.com/loushijushi/llama-router-setup/releases)
[![License](https://img.shields.io/github/license/loushijushi/llama-router-setup)](LICENSE)

一个可视化的 [llama.cpp](https://github.com/ggml-org/llama.cpp) `llama-server --models-preset` 路由模式管理工具。

- 🖥️ 图形界面配置多模型路由
- 🪟 把 `llama-server` 注册为 Windows 服务（开机自启、崩溃重启）
- 📊 实时监控窗口（实时日志 + token 生成速度，跟直接命令行调用一样）
- 📈 **监控窗口进度条**：预填充/生成都显示百分比 + 速度 + 预估剩余，一根条，看一眼就懂
- 🛡️ 显存守护 watchdog（GPU 紧张时自动休眠释放）
- 🔓 **强制释放显存/内存**：网关 `POST /admin/release`，停掉所有后端并结束任何还在跑的引擎进程（不管是谁加载的）
- 🌍 新电脑从零环境搭建（环境检查 + 一键修复）
- 🧭 首次运行向导（空配置自动引导设置）

## 📥 下载安装

去 [**Releases 页面**](https://github.com/loushijushi/llama-router-setup/releases) 下载最新版本的 zip 包（当前 `v0.3.0`，约 200 KB）：

```
llama-router-setup-v0.3.0.zip
```

解压到任意目录（**不要**有中文路径），双击 `manager-ui.bat` 即可。

> 没有 git / Python 经验？直接下 zip 是最简单的安装方式。

> **📝 填写指引（首次使用必读）**：
> 1. 启动 UI → 「全局」标签页 → 「llama.cpp 安装目录」→ 选你的 llama.cpp 根目录（含 `llama-server.exe`）
> 2. 「模型」标签页 → 左侧点「新增」→ 填 `id` + 选 `.gguf` 模型文件
> 3. 「服务」标签页 → 点「安装」注册服务 → 点「启动」

## ✨ 功能特性

| 功能 | 说明 |
|---|---|
| **首次运行向导** | 空配置自动弹窗引导设置 llama_dir + 添加模型 |
| **多模型管理** | 在 UI 里增删改查模型条目（路径、别名、推测解码开关等，草稿文件可选） |
| **常用/扩展参数** | 自动展开 200+ 个 llama-server 参数，每个有中文说明和默认值；保存时按本机 `llama-server --help` 核对，不认识的 key 自动跳过并提示，不会让整个 preset 解析失败 |
| **Windows 服务** | 用 NSSM 把 llama-server 注册成 `llama-router` 服务 |
| **显存守护** | 可选 watchdog 服务，显存不足时自动 unload 模型 |
| **实时监控** | 监控窗口拉满整个宽度，日志带颜色高亮、自动跟跳、悬停看模型详情 |
| **监控窗口进度条** | 预填充阶段显示「已读 prompt / 本轮 prompt 总量」，生成阶段显示「已生成 / 本轮可生成上限 (max_tokens)」，都带百分比、速度和预估剩余，一根条 |
| **强制释放显存/内存** | 网关 `POST /admin/release`，停掉所有后端并结束任何还在跑的引擎进程（网关、桌面端、手动启动的均可）；监控窗口和服务页均增加「🔓 释放显存/内存」按钮 |
| **环境检查** | 新电脑上自动检测 Python / tkinter / NSSM / llama.cpp，缺啥装啥 |
| **一键修复** | Python 用 winget 自动装，llama.cpp 给下载链接 |
| **零依赖** | 纯 Python 标准库实现，不需要 `pip install` 任何东西 |
| **一个端口统一调用** | 主池 / KVMem / Strata 三组后端都挂在同一个网关端口上（默认 `0.0.0.0:18080`），按请求里的 `model` 名自动路由；远程调用方只需要知道这一个地址 |

## 🌐 三个模型组 / 一个端口

服务 `llama-router` 里跑的是网关 `gateway.py`，它占住对外端口，把请求按
`model` 字段转给对应那一组的后端。**三组互斥**（16GB 显存装不下两个），
同一时刻只跑一个：

| 组 | 后端 | 内部端口 | 特点 |
|---|---|---|---|
| `pool` 主池 | `llama-server.exe`（router 模式） | `127.0.0.1:8080` | 多模型池，组内切换不用重启 |
| `kvmem` | `llama-kvmem-server.exe` | `127.0.0.1:8080` | 单模型进程，换档位 = 重启 |
| `strata` | `C:\Strata` 的 `serve/server.py` | `127.0.0.1:8080` | 单模型进程，参数在 `strata-*.json` 里，加载 ~40GB 要 1-3 分钟；PolyStrata 档位（`strata-poly.exe`）已迁 `PolyStrata-0.1.45-poly.3` |

三组**共用同一个内部口**（`gateway.backend_port`，默认 8080）。它们本来就互斥，
同一时刻只跑一个，共用不会撞 —— 换来的是**谁都不用为了接入网关改自己的端口**，
llama.cpp 和 Strata 都留在它们各自的原生默认端口上。Strata 只认命令行的
`--port`（`serve/server.py` 里是 `Server((a.host, a.port))`，json 里的 `port`
它自己不读），所以按它 `run-*.bat` 的做法从档位 json 里读出那个值传回去。

对外端口**故意让开 8080**（默认 `18080`）：8080 是 llama.cpp 和 Strata 的原生
默认端口，谁先起谁占，网关去抢只会打架。

内部端口只绑回环、不对外；**对外永远只有 `config.host:config.port` 一个口**。

远程/客户端统一这样连（把 IP 换成本机局域网地址）：

```text
base URL : http://<本机IP>:18080/v1
模型名   : 主池条目名 / 你在「⚡KVMem」页起的档位名 / 你在「🌐Strata」页登记的档位名
```

（档位名是**你自己起的**，所以文档里不写死具体名字 —— 写死了发布包的隐私检查
会当成泄漏本机信息拦下来。以 `GET /v1/models` 返回的为准。）

`GET http://<本机IP>:18080/v1/models` 会一次列出全部三组（`tags` 里是
`pool` / `kvmem` / `strata`）。客户端不需要知道背后是谁在跑 ——
带上 `model` 就行，网关会自己切组（切组期间请求会同步等待，一次成功）。

### 🌐 Strata：网关完全让路

Strata 活跃时网关**完全让路**（`PASSTHROUGH_GROUPS`），一个字节都不改写 ——
`/v1/models` 是网关自己的聚合目录（用来统一选组），后端自己那份
只报它当前那一个模型；其它接口（`/props`、`/models/sse`）原样转发，
它没有的就让它的 404 说话。

### 外部客户端怎么接（Hermes 等）

外部客户端要指到**网关的公开口 `18080`**：

```yaml
# ~/.hermes/config.yaml
model:
  default: <模型 id>
  provider: custom
  base_url: http://127.0.0.1:18080/v1
  api_key: <随便填非空>
```

另外网关的 `/v1/models` 会把上游的 `max_model_len` / `context_length`
**透传**出来，客户端才读得到该发多长。

> 顺带一提：有些后端的 `/v1/stats` 是 snake_case（`total_bytes`），
> 另一些是 camelCase（`totalBytes`）。两个都认，别只写一种。

### 默认按需加载：开机不加载任何模型

服务起来**不加载任何模型**（`/health` 里 `active: null`、三组后端全
`stopped`），收到请求才加载对应那一组：

- 请求**带 `model`** → 加载那一组并等就绪，再把请求转过去（客户端一次成功，
  不用重试）。Strata 首次约 46 秒
- **不带 `model`** → 回 503 并提示带上模型名。网关**不会猜**要加载哪一组
  （猜错就是白等 1-3 分钟，还白占显存/内存）
- `/v1/models`、`/props`、`/health` 等只读接口，以及浏览器要的静态资源，
  **都不会触发加载** —— 列目录、看状态不该启动模型

想要「开机就预热 `serve_mode` 那一组」：在「全局」页勾上**开机预加载模型**
（写 `config.gateway.preload = true`），或直接 `POST /models/load`。

> 注意：开着网页版时，浏览器的 PWA service worker 会每分钟请求一次 `/sw.js`。
> 这类请求已被归为「静态资源」，不会加载模型；后端没跑时它拿到 404。

### 调 Strata 时，网关只做两件事

Strata 活跃时，网关**不碰它的任何东西**：

- **停掉其它占资源的后端**（显存/内存就它一份）
- **把请求原样转给它**：`/props`、`/models/sse` 都不合成、不改写。它没有的
  接口就让它的 404 说话 —— 实际执行、报错、页面全是 Strata 自己那套

> 实测踩过的坑：网关以前会给 Strata 注入 `role=router` 并合成
> `/models/sse`，浏览器里的 llama.cpp 网页版因此以为后面是 router，接着去
> 轮询 `/tools`、`/build.json` 这些**只有 llama-server 才有**的接口，全部
> 404，网页版直接报错。Strata 的 `/props` 本身**没有 `role` 字段**。

唯一的例外是 `GET /v1/models`：那是网关自己的聚合目录（Strata 没实现这个
接口），用来让远程调用方知道有哪些模型名可用 —— 三组的都在里面。

### 三个标签页的界面是一样的

「模型」/「⚡KVMem」/「🌐Strata」三页用**同一套骨架**，
位置、分栏、按钮排布都一致，只有字段内容按各自的参数体系换：

```
顶部说明条
├─ 左：档位列表 + [扫描/导入][删除][上移][下移] + 基础信息 + 右上角 💾保存
├─ 右：参数面板
└─ 底部：运行服务（安装 / 启停 / 重启 / 调用地址）
```

### Strata 档位怎么换模型、调参

Strata 的参数**不在** `config.json` 里，而是全在 `C:\Strata\strata-<档位>.json`
的 `args` 数组里（`serve/server.py` 会原样透传给 `strata.exe`）。所以
**「🌐 Strata」页直接读写那个 json** —— 不会出现「UI 里改了但没生效」的两套值：

- **模型位置默认自动**：直接读 json 的 `--native`（Strata 自己装出来的配置），
  输入框置灰。取消勾选「自动」就能用「…」手动挑别的 `.gguf`，保存时写回 json
- 常用项（上下文、KV 精度/常驻、专家缓存、预填、推测解码、CPU 线程、
  显存预留、PCIe 占比、读图…）在右侧面板直接改，每项都有说明
- `--pack` / `--expert-profile` 是「这个档位是谁」的标识，**只读**
- 列表里没有的 flag 不会丢，原样保留，也可以在「其它参数」框里自由增删
- 保存时认得的 flag 保持原顺序、不认得的挪到末尾、缩进照旧，并留一份 `.bak`
- 改完要**重启服务**才生效（加载 40GB，约 1–3 分钟）

## 🚀 快速开始

### 系统要求

- Windows 10/11
- Python 3.8+（含 tkinter）
- llama.cpp 完整安装（`llama-server.exe` 所在目录）
- 模型文件（`.gguf` 格式）

### 安装

1. **下载 zip 包**（推荐大多数用户）
   - 去 [Releases](https://github.com/loushijushi/llama-router-setup/releases) 下载最新版本
   - 解压到任意目录（**路径不要有中文**）
   - 双击 `manager-ui.bat` 启动

   或用 git:
   ```bash
   git clone https://github.com/loushijushi/llama-router-setup.git
   cd llama-router-setup
   ```

2. **首次运行**（UI 会自动弹出向导引导你）
   - 双击 `manager-ui.bat`
   - 弹窗"首次运行向导" → 自动跳到**「全局」**标签页 → 设置 `llama_dir`（你的 llama.cpp 根目录）
   - 切到**「模型」**标签页 → 左侧点**「新增」** → 选你的 `.gguf` 文件
   - 切到**「服务」**标签页 → 点**「安装」**注册 Windows 服务 → 点**「启动」**
   - 切到**「环境」**标签页 → 缺什么点「修复」按钮自动装

> **标签顺序**：服务 / 全局 / 模型 / ⚡KVMem / 🌐Strata / 偏好 / 环境
> 平时主要用前 3 个，偏好和环境偶尔用到。

3. **日常使用**
   - 双击 `manager-ui.bat` 启动管理界面
   - 点**「📊 实时监控」**查看实时日志和模型状态

### 配置说明

| 文件 | 用途 | 是否入库 |
|---|---|---|
| `config.json` | 你的实际配置（路径、模型等） | ❌ 不入库（`.gitignore`） |
| `config.example.json` | 模板，参考用 | ✅ 入库 |
| `user_preferences.json` | UI 偏好（首次运行自动生成） | ❌ 不入库 |
| `logs/` | 服务运行日志 | ❌ 不入库 |
| `router-preset.ini` | llama-server 实际读取的预设 | ❌ 不入库（在 `llama_dir` 下） |

你可以**手动编辑** `config.json` 或在 UI 里改，效果一样。

### 详细文档

- `安装指南.txt` — 完整安装/使用 FAQ
- 启动 UI 后点「❓ 帮助」按钮查看内置帮助

## 🖼️ 界面预览

启动后默认在**「环境」**页检查依赖是否齐全：

```
✓ Python 3.8+   : 已找到
✓ tkinter        : 可用
✓ NSSM           : 内置 tools/nssm.exe
✗ llama.cpp     : 未配置（请到「全局」页设置）
✗ 模型文件      : 未配置（请到「模型」页添加）
```

**「服务」**页可一键安装/启停服务：

```
[ 安装 ] [ 卸载 ] [ 启动 ] [ 停止 ] [ 重启 ] [ 前台运行 ] [ 刷新状态 ] [ 📊 实时监控 ]
```

**「模型」**页（顶部提示条提示点「新增」）：

```
👉 在左侧点「新增」添加你的 .gguf 模型
┌──────────┬─────────────────────────────────────┐
│ 模型列表  │ 选中模型的所有参数（常用 / 扩展切换）│
│ my-model  │  [✓] threads    8                     │
│          │  [✓] ctx-size   262144                 │
│ + 新增    │  [✓] batch-size 512                   │
│ - 删除    │  [✓] flash-attn on                    │
└──────────┴─────────────────────────────────────┘
```

**「📊 实时监控」**窗口拉满整个窗口，模型状态只在顶部一行：

```
健康: ok │ 模型: ● model-1  ○ model-2
[ 🔁 重启服务 ] [ 🔧 重读日志 ] [ 🔄 立即刷新 ] [ 清空日志 ]
┌────────────────────────────────────────────────────────────┐
│ 📜 实时日志 (router.out.log + router.err.log)              │
│ 📍 跟跳: ON  ← 滚轮向上自动暂停，点「立即刷新」恢复  │
│                                                            │
│ [PID] 0.43.275 I slot print_timing: tg=45.23 t/s ...    │
│ [PID] 0.46.276 I slot print_timing: tg=45.01 t/s ...    │
│ ...                                                        │
└────────────────────────────────────────────────────────────┘
```

实时监控特性：
- **自动跟跳**：默认滚到底显示最新日志
- **滚轮向上**自动暂停跟跳，方便回看历史
- **点「立即刷新」**或滚到底 → 恢复跟跳
- **悬停模型名** → 弹出 tooltip 显示所有参数 + 最近生成速度
- **点击模型名** → 弹出窗口显示完整 JSON

## 📁 项目结构

```
llama-router-setup/
├── .github/workflows/
│   ├── ci.yml                  # PR/push 自动化检查 (语法/CRLF/必需文件)
│   └── release.yml             # tag 推送触发自动打包 + 发布 Release
│
├── manager-ui.bat             # 主入口: 启动管理界面
├── install-router.bat         # 一键安装为 Windows 服务
├── uninstall-router.bat       # 卸载服务
├── status-router.bat          # 查看服务状态 + 最近日志
├── test-router.bat            # 测试路由连通性
│
├── find_python.cmd            # 找 Python (优先用真路径, 避开 MS Store 占位)
├── launch_ui_logged.cmd       # 启动 UI 的命令行入口 (调 launch_ui.ps1)
├── launch_ui.ps1              # 隐藏窗口启动 UI; 失败时显示日志并暂停
├── launch_ui_hidden.vbs       # (可选) 完全静默启动, 不留任何窗口
├── show_python_missing.ps1    # 找不到 Python 时弹窗引导安装
├── publish_to_github.bat      # (开发者) 一键发布到 GitHub
├── build_release.bat          # (开发者) 打包 zip 用于测试
│
├── config.py                  # 配置 schema + 加载/保存
├── config.example.json        # 配置模板 (含详细注释)
├── generate_preset.py         # config.json -> router-preset.ini
├── service.py                 # NSSM 服务管理 (install/uninstall/start/...)
├── startup_check.py           # 环境检查 (CLI + API 两种调用方式)
├── watchdog.py                # 显存守护服务
├── ui.py                      # 主 UI (Tkinter) - 含所有标签页和监控窗口
├── build_release.py           # (开发者) 打包脚本
│
├── tools/
│   ├── nssm.exe               # 内置 NSSM (~360KB, 无需另装)
│   └── README.txt             # NSSM 来源说明
│
├── VERSION                    # 当前版本号
├── LICENSE                    # MIT 许可证
├── README.md                  # 本文件
├── 安装指南.txt                # 中文 FAQ
│
├── logs/                      # 服务日志 (运行时生成, .gitignore)
├── dist/                      # 打包产物 (本地, .gitignore)
└── __pycache__/               # Python 缓存 (.gitignore)
```

> **📝 第一次使用提示**：
> - 项目目录建议放到纯英文路径（如 `D:\llama-router-setup\`）
> - 完整填写说明在 `安装指南.txt` 里

## 🛠️ 技术栈

- **Python 3.8+** + Tkinter（内置 GUI）
- **NSSM** (Non-Sucking Service Manager) — 把任意 exe 注册为 Windows 服务
- **llama.cpp** 的 `llama-server --models-preset` 路由模式
- **GitHub Actions** — CI 自动化 + Release 自动打包
- 纯 stdlib 实现，**无第三方 Python 依赖**

## ❓ 常见问题

**Q: 监控窗口看不到新日志？**
A: 点**「🔧 重读日志」**或**「🔁 重启服务」**按钮恢复。NSSM 的文件句柄偶尔会卡住。

**Q: 一行日志太长，看不全？**
A: 顶栏的**「换行」**就是干这个的（默认开着）：按窗口宽度折行，横向滚动条会自动收掉。
关掉它就变回「整行不折行 + 横向滚动条」，看堆栈、对齐的表格时反而更顺手。这个选择会被记住。

**Q: 多个模型能不能并发？**
A: 当前 `models_max=1`，模型按需加载（旧模型自动 sleep 释放显存）。如需真并发可改为 2+。

**Q: 不想要 thinking 模式的输出？**
A: 在「模型」页勾上 `reasoning` 参数，值 `off`。⚠️ 注意：**Qwen3 / Ornith 等模型可能在 chat template 层面强制启用 thinking，本参数不一定能完全关闭**。如果关不掉，可以增大 `max_tokens` 让模型有更多空间同时输出思考和正式回复。

**Q: 实时监控悬停模型名没反应？**
A: 等几秒让首次 HTTP 轮询完成（`/v1/models`），之后悬停会显示 tooltip。

**Q: 双击 `manager-ui.bat` 后窗口一闪就退出 / 报错看不清？**
A: UI 现在**隐藏窗口**启动，`manager-ui.bat` 窗口几秒后自动关闭（不会残留黑色窗口）。如果启动失败，窗口会**停住并显示完整报错**（按任意键关闭）。若仍闪退，请把 `logs\ui_launch.log` 和 `logs\ui_crash.log` 发给维护者。

**Q: 想要完全无窗口启动？**
A: 可用 `launch_ui_hidden.vbs` 代替（崩溃时错误只在日志里，看不到窗口）。

**Q: 路径里有中文/空格会出问题吗？**
A: 项目目录路径有中文一般没事，但**强烈建议安装到纯英文路径**，避免 NSSM 解析异常。

更多问题见 `安装指南.txt` 或在 UI 里点「❓ 帮助」。

## 🛠️ 开发与发布

**只对项目维护者/贡献者有意义**。

### 克隆开发版
```bash
git clone https://github.com/loushijushi/llama-router-setup.git
cd llama-router-setup
```

### 本地打 zip 包
```bash
py build_release.py                 # 使用 VERSION 文件里的版本
py build_release.py --version 0.2.0 # 指定版本
py build_release.py --no-config     # 不带 config.example.json
```
输出在 `dist/` 目录。

### 发布新版本到 GitHub Releases

**当前约定**：版本号遵循语义化版本 (SemVer)。已发布 `v0.3.0`（新增进度条合并、强制释放显存/内存、PolyStrata 档位迁 poly.3），后续按 `v0.3.x` 递增。

发布流程：
1. 修改 `VERSION` 文件
2. 提交代码：
   ```bash
   git add -A
   git commit -m "描述修改"
   git push
   ```
3. 推 tag 触发自动发布：
   ```bash
    git tag v0.3.0
    git push --tags
   ```
4. GitHub Actions 自动：
   - 在 Windows runner 上运行 `build_release.py`
   - 验证 zip 生成
   - 创建 GitHub Release（自动生成 changelog）
   - 上传 zip 作为 binary
5. 几分钟后在 https://github.com/loushijushi/llama-router-setup/releases 看到

### 首次发布到 GitHub
如果是第一次从本地推到 GitHub，运行 `publish_to_github.bat`，按提示操作。

### CI 自动化

- **CI** (`.github/workflows/ci.yml`): 每次 push / PR 自动检查
  - Python 语法编译
  - `.bat`/`.cmd` 文件 CRLF 行尾
  - `.gitignore` 覆盖关键文件
  - 必需文件存在

- **Release** (`.github/workflows/release.yml`): 推 tag 时自动
  - 用 `build_release.py` 打包 zip
  - 从 `CHANGELOG.md` 自动提取本次版本的更新内容作为 release notes
  - 同时列出 git commits (作为补充)
  - 创建 GitHub Release + 上传 zip

### 发布前检查

发布前跑 `py pre_publish_check.py` 自动检查:
- VERSION 文件是否已递增
- README 中是否有过时内容
- CHANGELOG.md 中是否有本次版本条目
- 工作区是否干净 (无未提交修改)
- 加 `--auto-fix` 可自动从 git log 生成 CHANGELOG [Unreleased] 段

每个版本的详细变更见 [CHANGELOG.md](CHANGELOG.md)。

## 📜 许可证

MIT License — 详见 [LICENSE](LICENSE) 文件。

## 🙏 致谢

- [llama.cpp](https://github.com/ggml-org/llama.cpp) — 整个项目的基础
- [NSSM](https://nssm.cc/) — Windows 服务管理
- [GitHub Actions](https://github.com/features/actions) — 自动化 CI/CD
- 所有贡献者和用户
