# 更新日志 (Changelog)

本项目的所有重要变更都记录在此文件。

格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [Semantic Versioning](https://semver.org/lang/zh-CN/)。

## [Unreleased]

- 修复「偏好」标签页中「加入」和「移出」按钮箭头方向反转

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
