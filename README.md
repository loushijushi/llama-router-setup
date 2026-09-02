# llama.cpp Router 管理工具

一个可视化的 [llama.cpp](https://github.com/ggml-org/llama.cpp) `llama-server --models-preset` 路由模式管理工具。

- 🖥️ 图形界面配置多模型路由
- 🪟 把 `llama-server` 注册为 Windows 服务（开机自启、崩溃重启）
- 📊 实时监控窗口（类似直接命令行调用看到的速度/日志）
- 🛡️ 显存守护 watchdog（GPU 紧张时自动休眠释放）
- 🌍 支持新电脑从零环境搭建（缺啥自动装）

## 📥 下载安装

前往 [**Releases 页面**](https://github.com/loushijushi/llama-router-setup/releases) 下载最新版本的 zip 包：

```
llama-router-setup-v1.0.0.zip
```

解压后双击 `manager-ui.bat` 即可。首次运行会自动检查环境并引导补齐缺失的依赖。

> 没有 git / Python 经验？直接下载 zip 是最简单的安装方式。

## ✨ 功能特性

| 功能 | 说明 |
|---|---|
| 多模型管理 | 在 UI 里增删改查模型条目（路径、别名、草稿模型等） |
| 常用/扩展参数 | 自动展开 200+ 个 llama-server 参数，每个有中文说明和默认值 |
| Windows 服务 | 用 NSSM 把 llama-server 注册成 `llama-router` 服务 |
| 显存守护 | 可选 watchdog 服务，显存不足时自动 unload 模型 |
| 实时监控 | 监控窗口拉满整个宽度，日志带颜色高亮、悬停看模型详情 |
| 环境检查 | 新电脑上自动检测 Python / tkinter / NSSM / llama.cpp，缺啥装啥 |
| 自动安装 | Python 用 winget 一键装，llama.cpp 给下载链接 |

## 🚀 快速开始

### 系统要求

- Windows 10/11
- Python 3.8+（含 tkinter）
- llama.cpp 完整安装（`llama-server.exe` 所在目录）
- 模型文件（`.gguf` 格式）

### 安装

1. **下载 zip 包** (推荐大多数用户)
   - 去 [Releases](https://github.com/loushijushi/llama-router-setup/releases) 下载最新版本
   - 解压到任意目录 (如 `D:\llama-router-setup\`)
   - 双击 `manager-ui.bat` 启动

   或用 git:
   ```bash
   git clone https://github.com/loushijushi/llama-router-setup.git
   cd llama-router-setup
   ```

2. **首次运行** (UI 会弹出向导引导你)
   - 双击 `manager-ui.bat`
   - **环境检查**：「环境」标签页会列出所有依赖状态, 缺什么点「修复」自动装
   - **设置 llama.cpp 目录**：「全局」标签页 → 「llama.cpp 安装目录」选你电脑上的 llama.cpp 根目录 (含 `llama-server.exe`)
   - **添加模型**：「模型」标签页 → 左侧点「新增」→ 选你的 `.gguf` 文件
   - **启动服务**：「服务」标签页 → 点「安装」→ 点「启动」

3. **日常使用**
   - 双击 `manager-ui.bat` 启动管理界面
   - 点「📊 实时监控」查看实时日志和模型状态

### 配置说明

- `config.json` (首次运行自动生成, 不会入库) 存你的所有配置
- `config.example.json` 是模板, 仅作参考
- 你可以**手动编辑** `config.json` 或在 UI 里改, 效果一样
- `logs/` 目录存服务运行日志, 不入库

### 详细文档

- `安装指南.txt` — 完整安装/使用 FAQ
- 启动 UI 后点「❓ 帮助」按钮查看内置帮助

## 🖼️ 界面预览

启动后默认在「环境」页检查依赖是否齐全：

```
[✓] Python 3.8+   : Python 3.12.x
[✓] tkinter        : 可用
[✓] NSSM           : 内置 tools/nssm.exe
[✗] llama.cpp     : C:\llama.cpp\llama-server.exe 未找到  [修复]
[✗] 模型文件      : 路径无效                          [修复]
```

服务页可以一键安装/启停服务：

```
[ 安装 ] [ 卸载 ] [ 启动 ] [ 停止 ] [ 重启 ] [ 前台运行 ] [ 刷新状态 ] [ 📊 实时监控 ]
```

模型页分左右栏：左侧模型列表，右侧该模型的所有参数（按 scope 过滤）。

监控窗口拉满整个窗口，模型状态只在顶部一行：

```
健康: ok │ 模型: ● ornith-35b  ◐ qwen3.6-35b
[悬停 ornirth-35b 弹出详细 tooltip，显示 ctx-size, batch-size, cache-type 等所有参数]
┌────────────────────────────────────────────────────────────┐
│ 📜 实时日志 (router.out.log + router.err.log)              │
│ 跟跳: ⏸ PAUSED  (滚轮向上查看时自动暂停)                  │
│                                                            │
│ [72992] 5.43.275 I slot print_timing: tg=45.23 t/s ...   │
│ [72992] 5.46.276 I slot print_timing: tg=45.01 t/s ...   │
│ ...                                                        │
└────────────────────────────────────────────────────────────┘
```

## 📁 项目结构

```
llama-router-setup/
├── manager-ui.bat             # 主入口: 启动管理界面
├── install-router.bat         # 一键安装为 Windows 服务
├── uninstall-router.bat       # 卸载服务
├── status-router.bat          # 查看服务状态 + 最近日志
├── find_python.cmd            # 找 Python (优先用真路径, 避开 MS Store 占位)
├── launch_ui_hidden.vbs       # 静默启动 Python UI (无空白 cmd 窗口)
├── show_python_missing.ps1    # 找不到 Python 时弹窗引导安装
│
├── config.py                  # 配置 schema + 加载/保存
├── generate_preset.py         # config.json -> router-preset.ini
├── service.py                 # NSSM 服务管理 (install/uninstall/start/...)
├── startup_check.py           # 环境检查 (CLI + API 两种调用方式)
├── watchdog.py                # 显存守护服务
├── ui.py                      # 主 UI (Tkinter)
│
├── config.example.json        # 配置模板 (复制为 config.json 后修改)
├── user_preferences.json      # UI 偏好 (首次运行自动生成)
├── 安装指南.txt                # 中文 FAQ
│
├── tools/
│   └── nssm.exe               # 内置 NSSM (~360KB, 无需另装)
│
├── logs/                      # 服务日志 (运行时生成, .gitignore)
└── router-preset.ini          # llama-server 读取的预设 (运行时生成, .gitignore)
```

## 🛠️ 技术栈

- **Python 3.8+** + Tkinter (内置 GUI)
- **NSSM** (Non-Sucking Service Manager) — 把任意 exe 注册为 Windows 服务
- **llama.cpp** 的 `llama-server --models-preset` 路由模式
- 纯 stdlib 实现，**无第三方 Python 依赖**

## ❓ 常见问题

**Q: 监控窗口看不到新日志？**
A: 点「🔧 重读日志」或「🔁 重启服务」按钮恢复。NSSM 的文件句柄偶尔会卡住。

**Q: 我有多个模型，能不能并发？**
A: 当前 `models_max=1`，模型按需加载（旧模型自动 sleep 释放显存）。如需真并发可改为 2+。

**Q: 不想要 thinking 模式的输出？**
A: 「模型」页勾上 `reasoning` 参数，值 `off`（实际效果取决于模型，部分模型会忽略）。

更多问题见 `安装指南.txt` 或在 UI 里点「❓ 帮助」。

## 🛠️ 开发与发布

只对项目维护者/贡献者有意义。

**克隆开发版**:
```bash
git clone https://github.com/loushijushi/llama-router-setup.git
cd llama-router-setup
```

**打 zip 包** (用于本地测试):
```bash
py build_release.py                 # 默认版本 0.1.0
py build_release.py --version 1.2.3
```
输出在 `dist/` 目录。

**发布新版本到 GitHub Releases**:
1. 修改 `VERSION` 文件 (或 git tag 携带版本号)
2. 推送 tag:
   ```bash
   git tag v1.0.0
   git push --tags
   ```
3. GitHub Actions 自动:
   - 在 Windows runner 上构建 zip
   - 创建 Release (含自动生成的 changelog)
   - 上传 zip 作为 binary
4. 几分钟后在 https://github.com/loushijushi/llama-router-setup/releases 看到

CI 还会对每个 PR / push 检查 Python 语法、CRLF 行尾、`.gitignore` 覆盖、必需文件存在等。

## 📜 许可证

MIT License — 详见 [LICENSE](LICENSE) 文件。

## 🙏 致谢

- [llama.cpp](https://github.com/ggml-org/llama.cpp) — 整个项目的基础
- [NSSM](https://nssm.cc/) — Windows 服务管理
- 所有贡献者和用户
