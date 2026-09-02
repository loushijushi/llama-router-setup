# llama.cpp Router 管理工具

[![Release](https://img.shields.io/github/v/release/loushijushi/llama-router-setup)](https://github.com/loushijushi/llama-router-setup/releases)
[![License](https://img.shields.io/github/license/loushijushi/llama-router-setup)](LICENSE)

一个可视化的 [llama.cpp](https://github.com/ggml-org/llama.cpp) `llama-server --models-preset` 路由模式管理工具。

- 🖥️ 图形界面配置多模型路由
- 🪟 把 `llama-server` 注册为 Windows 服务（开机自启、崩溃重启）
- 📊 实时监控窗口（实时日志 + token 生成速度，跟直接命令行调用一样）
- 🛡️ 显存守护 watchdog（GPU 紧张时自动休眠释放）
- 🌍 新电脑从零环境搭建（环境检查 + 一键修复）
- 🧭 首次运行向导（空配置自动引导设置）

## 📥 下载安装

去 [**Releases 页面**](https://github.com/loushijushi/llama-router-setup/releases) 下载最新 zip 包（当前 `v0.0.2`，约 200 KB）：

```
llama-router-setup-v0.0.2.zip
```

解压到任意目录（**不要**有中文路径），双击 `manager-ui.bat` 即可。

> 没有 git / Python 经验？直接下 zip 是最简单的安装方式。

## ✨ 功能特性

| 功能 | 说明 |
|---|---|
| **首次运行向导** | 空配置自动弹窗引导设置 llama_dir + 添加模型 |
| **多模型管理** | 在 UI 里增删改查模型条目（路径、别名、草稿模型等） |
| **常用/扩展参数** | 自动展开 200+ 个 llama-server 参数，每个有中文说明和默认值 |
| **Windows 服务** | 用 NSSM 把 llama-server 注册成 `llama-router` 服务 |
| **显存守护** | 可选 watchdog 服务，显存不足时自动 unload 模型 |
| **实时监控** | 监控窗口拉满整个宽度，日志带颜色高亮、自动跟跳、悬停看模型详情 |
| **环境检查** | 新电脑上自动检测 Python / tkinter / NSSM / llama.cpp，缺啥装啥 |
| **一键修复** | Python 用 winget 自动装，llama.cpp 给下载链接 |
| **零依赖** | 纯 Python 标准库实现，不需要 `pip install` 任何东西 |

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
   - 弹窗"首次运行向导" → 选**「全局」**标签页 → 设置 `llama_dir`（你的 llama.cpp 根目录）
   - 切到**「模型」**标签页 → 左侧点**「新增」** → 选你的 `.gguf` 文件
   - 切到**「环境」**标签页 → 缺什么点「修复」按钮自动装
   - 切到**「服务」**标签页 → 点**「安装」**注册 Windows 服务 → 点**「启动」**

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
✓ Python 3.8+   : Python 3.12.x
✓ tkinter        : 可用
✓ NSSM           : 内置 tools/nssm.exe
✗ llama.cpp     : C:\llama.cpp\llama-server.exe 未找到  [修复]
✗ 模型文件      : 路径无效                          [修复]
```

**「服务」**页可一键安装/启停服务：

```
[ 安装 ] [ 卸载 ] [ 启动 ] [ 停止 ] [ 重启 ] [ 前台运行 ] [ 刷新状态 ] [ 📊 实时监控 ]
```

**「模型」**页分左右栏（顶部橙色提示条提示点「新增」）：

```
👉 在左侧点「新增」添加你的 .gguf 模型
┌──────────┬─────────────────────────────────────┐
│ 模型列表  │ 选中模型的所有参数（常用 / 扩展切换）│
│ my-model  │  threads [✓] 8                        │
│          │  ctx-size [✓] 262144                  │
│ + 新增    │  batch-size [✓] 512                  │
│ - 删除    │  cache-type-k [✓] q8_0               │
└──────────┴─────────────────────────────────────┘
```

**「📊 实时监控」**窗口拉满整个窗口，模型状态只在顶部一行：

```
健康: ok │ 模型: ● ornith-35b  ◐ qwen3.6-35b       ← 悬停查看详情，点击打开 JSON
[ 🔁 重启服务 ] [ 🔧 重读日志 ] [ 🔄 立即刷新 ] [ 清空日志 ]
┌────────────────────────────────────────────────────────────┐
│ 📜 实时日志 (router.out.log + router.err.log)              │
│ 📍 跟跳: ON  ← 滚轮向上自动暂停，点「立即刷新」恢复  │
│                                                            │
│ [72992] 5.43.275 I slot print_timing: tg=45.23 t/s ...   │
│ [72992] 5.46.276 I slot print_timing: tg=45.01 t/s ...   │
│ [72992] 5.49.291 I slot print_timing: tg=44.85 t/s ...   │
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
├── launch_ui_hidden.vbs       # 静默启动 Python UI (不弹 cmd 窗口)
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
├── VERSION                    # 当前版本号 (0.0.2)
├── LICENSE                    # MIT 许可证
├── README.md                  # 本文件
├── 安装指南.txt                # 中文 FAQ
│
├── logs/                      # 服务日志 (运行时生成, .gitignore)
├── dist/                      # 打包产物 (本地, .gitignore)
└── __pycache__/               # Python 缓存 (.gitignore)
```

## 🛠️ 技术栈

- **Python 3.8+** + Tkinter（内置 GUI）
- **NSSM** (Non-Sucking Service Manager) — 把任意 exe 注册为 Windows 服务
- **llama.cpp** 的 `llama-server --models-preset` 路由模式
- **GitHub Actions** — CI 自动化 + Release 自动打包
- 纯 stdlib 实现，**无第三方 Python 依赖**

## ❓ 常见问题

**Q: 监控窗口看不到新日志？**
A: 点**「🔧 重读日志」**或**「🔁 重启服务」**按钮恢复。NSSM 的文件句柄偶尔会卡住。

**Q: 多个模型能不能并发？**
A: 当前 `models_max=1`，模型按需加载（旧模型自动 sleep 释放显存）。如需真并发可改为 2+。

**Q: 不想要 thinking 模式的输出？**
A: 在「模型」页勾上 `reasoning` 参数，值 `off`。⚠️ 注意：**Qwen3 / Ornith 等模型可能在 chat template 层面强制启用 thinking，本参数不一定能完全关闭**。如果关不掉，可以增大 `max_tokens` 让模型有更多空间同时输出思考和正式回复。

**Q: 实时监控悬停模型名没反应？**
A: 等几秒让首次 HTTP 轮询完成（`/v1/models`），之后悬停会显示 tooltip。

**Q: 双击 `manager-ui.bat` 弹出一个 cmd 窗口？**
A: 现在的版本用 VBS 静默启动，**不会**弹 cmd 窗口。如果还弹，请确认你用的是 `v0.0.2+` 版本。

**Q: 路径里有中文/空格会出问题吗？**
A: 项目目录路径有中文一般没事，但**强烈建议安装到纯英文路径**（如 `D:\llama-router-setup\`），避免 NSSM 解析异常。

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
py build_release.py --version 0.0.3 # 指定版本
py build_release.py --no-config     # 不带 config.example.json
```
输出在 `dist/` 目录。

### 发布新版本到 GitHub Releases

**当前约定**：版本号从 `0.0.1` 开始，按 `0.0.1 → 0.0.2 → 0.0.3 → ...` 递增。

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
   git tag v0.0.3
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

- **Release** (`.github/workflows/release.yml`): 推 tag 时自动打包发布

## 📜 许可证

MIT License — 详见 [LICENSE](LICENSE) 文件。

## 🙏 致谢

- [llama.cpp](https://github.com/ggml-org/llama.cpp) — 整个项目的基础
- [NSSM](https://nssm.cc/) — Windows 服务管理
- [GitHub Actions](https://github.com/features/actions) — 自动化 CI/CD
- 所有贡献者和用户
