# 更新日志 (Changelog)

本项目的所有重要变更都记录在此文件。

格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [Semantic Versioning](https://semver.org/lang/zh-CN/)。

## [Unreleased]

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
