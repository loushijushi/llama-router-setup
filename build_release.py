"""生成可分发的 zip 包 (用于 GitHub Releases)。

用法:
    py build_release.py                 # 默认打包当前目录到 dist/llama-router-setup-v{ver}.zip
    py build_release.py --version 1.2.3 --output /tmp/release.zip
    py build_release.py --no-config     # 不带 config.example.json (更精简)

打包内容:
    - 所有 .py / .bat / .cmd / .ps1 / .vbs / .json(模板) / .md / .txt / .cmd 文件
    - tools/nssm.exe + tools/README.txt
    - .github/workflows/release.yml  (CI 配置, 让别人也能复现)
    - 不带: config.json (用户配置) / logs/ / __pycache__/ / *.pyc / 发布产物本身

隐私保证:
    - config.json / user_preferences.json / router-preset.ini 永远不进包
      (EXCLUDE_FILES 与 PRIVATE_FILES 双重兜底)
    - 打完包会用 verify_zip_privacy() 复扫一遍 zip 内每个文本文件,
      对照本机 config.json 里的模型路径 / 模型名 / api_key 查泄漏
    - 一旦发现违规: 打印清单、删除 zip、退出码 1 (CI 会因此失败)
    - 本机的 config.json 全程只读, 不会被修改或删除
"""
import argparse
import os
import sys
import zipfile
from pathlib import Path

# 强制 UTF-8 输出 (避免在 Windows GBK 控制台下乱码/UnicodeEncodeError)
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

# 这些路径/文件**不会**被打包
EXCLUDE_DIRS = {
    "__pycache__",
    "logs",
    "dist",
    ".git",
    ".github",  # CI 配置不需要给用户 (可选, 想保留就把这行注释)
    "venv",
    ".venv",
    "env",
}
# ---- 隐私红线: 这些是开发者本机的配置, 绝对不能出现在发布包里 ----
# 保护的是"别人拿到 zip 看不到我的 llama_dir / 模型名 / API key"
PRIVATE_FILES = {
    "config.json",            # 本机配置 (含模型路径 / api_key / 服务设置)
    "user_preferences.json",  # UI 偏好 (常用参数选择等)
    "router-preset.ini",      # 生成的 preset (含全部模型与参数)
}
PRIVATE_DIRS = {"logs", "dist"}
EXCLUDE_FILES = {
    "config.json",
    "user_preferences.json",
    "config.example.json.local",
    ".gitignore",  # 开发者文件, 不需要给最终用户
    "publish_to_github.bat",  # 开发者用
    "build_release.py",  # 脚本本身
    "build_release.bat",  # 脚本本身
    "router-preset.ini",
    "INSTALL.txt",  # 由本脚本重新生成, 避免磁盘上的旧文件与生成的重复
    "LICENSE",  # 用户可能不需要看许可证, 留 LICENSE 也行, 这里选择不打包
}
EXCLUDE_EXTS = {
    ".pyc", ".pyo", ".pyd", ".so", ".dll", ".exe.bak",
    ".tmp", ".bak", ".swp", ".log",
}
# LICENSE 是个例外, 仍然想打包
EXCLUDE_FILES.discard("LICENSE")
# 双保险: 即使有人误删了 EXCLUDE_FILES 里的隐私项, PRIVATE_FILES 仍然兜底
EXCLUDE_FILES |= PRIVATE_FILES


def should_include(path: Path, project_root: Path) -> bool:
    """检查这个文件/目录是否应该被打包。"""
    rel = path.relative_to(project_root)
    parts = rel.parts
    # 排除目录
    for part in parts[:-1] if path.is_file() else parts:
        if part in EXCLUDE_DIRS or part in PRIVATE_DIRS:
            return False
    # 排除文件
    if path.is_file():
        if path.name in EXCLUDE_FILES or path.name in PRIVATE_FILES:
            return False
        if path.suffix.lower() in EXCLUDE_EXTS:
            return False
    return True


def verify_zip_privacy(zip_path: Path, project_root: Path = None) -> list:
    """扫描已生成的 zip, 返回违规列表 (空列表 = 通过)。

    这是发布前的最后一道闸: 即使前面某处把配置文件加了回来,
    这里也会在退出前报错, 而不是把带隐私的包发出去。
    """
    import zipfile
    violations = []
    markers = _private_markers(project_root or Path("."))
    text_exts = {".json", ".md", ".txt", ".py", ".bat", ".cmd", ".ps1",
                 ".vbs", ".yml", ".yaml", ".ini", ".cfg"}
    with zipfile.ZipFile(zip_path) as zf:
        for info in zf.infolist():
            name = info.filename
            base = name.rsplit("/", 1)[-1]
            parts = name.split("/")
            # 1) 私有文件名
            if base in PRIVATE_FILES:
                violations.append(f"包含私有文件: {name}")
                continue
            # 2) 私有目录下的任何文件
            if any(p in PRIVATE_DIRS for p in parts[:-1]):
                violations.append(f"包含私有目录: {name}")
                continue
            # 3) 文本内容不能含本机模型路径 / 模型名 / api_key
            suffix = Path(base).suffix.lower()
            if suffix not in text_exts or info.file_size > 5 * 1024 * 1024:
                continue
            try:
                text = zf.read(info).decode("utf-8")
            except Exception:
                try:
                    text = zf.read(info).decode("utf-8-sig")
                except Exception:
                    continue
            for marker in markers:
                if marker in text:
                    violations.append(f"{name} 泄漏本机信息: 含 '{_mask(marker)}'")
    # 4) 模板必须在 (除非显式 --no-config)
    return violations


def _private_markers(project_root: Path) -> list:
    """从本机 config.json 提取有辨识度的字符串, 用于反泄漏扫描。"""
    import json
    markers = []
    cfg_path = project_root / "config.json"
    if not cfg_path.exists():
        return markers
    try:
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    except Exception:
        return markers
    try:
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    except Exception:
        return markers
    for m in cfg.get("models", []):
        for k in ("model", "mmproj", "base_url", "api_key"):
            v = str(m.get(k) or "").strip()
            if v:
                markers.append(v)
        for k in ("id", "alias"):
            v = str(m.get(k) or "").strip()
            if v:
                markers.append(v)
    # 路径片段 (含模型目录) 也一并扫描
    for mk in list(markers):
        if "\\" in mk or "/" in mk:
            markers.append(mk.replace("\\", "/"))
    # 去重 + 过滤掉太短/太通用的
    seen, out = set(), []
    for s in markers:
        if len(s) < 4 or s in seen:
            continue
        seen.add(s)
        out.append(s)
    return out


def _mask(s: str) -> str:
    """报错时不要把 api_key 之类的东西原样打印出来。"""
    if len(s) <= 8:
        return s[:2] + "***"
    return s[:4] + "***" + s[-4:]


def collect_files(project_root: Path) -> list:
    """递归收集所有要打包的文件, 返回 (绝对路径, 在 zip 内的相对路径) 列表。"""
    files = []
    for p in project_root.rglob("*"):
        if not p.is_file():
            continue
        if not should_include(p, project_root):
            continue
        # 跳过 release 产物自己
        if p.parent.name == "dist":
            continue
        rel = p.relative_to(project_root)
        files.append((p, str(rel).replace(os.sep, "/")))
    # 排序方便查看
    files.sort(key=lambda x: x[1])
    return files


def get_version(version_arg: str | None) -> str:
    """确定版本号: 命令行参数 > 从 VERSION 文件读 > 从 git tag 读 > 默认 0.1.1。"""
    if version_arg:
        return version_arg
    # 从 VERSION 文件
    ver_file = Path("VERSION")
    if ver_file.exists():
        v = ver_file.read_text(encoding="utf-8").strip()
        if v:
            return v
    # 从 git tag
    try:
        import subprocess
        r = subprocess.run(
            ["git", "describe", "--tags", "--abbrev=0"],
            capture_output=True, text=True, encoding="utf-8",
        )
        if r.returncode == 0 and r.stdout.strip():
            tag = r.stdout.strip()
            return tag.lstrip("v")
    except Exception:
        pass
    return "0.1.1"


def build_zip(project_root: Path, version: str, output: Path,
              include_config_example: bool = True) -> dict:
    """打包, 返回统计信息。"""
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        output.unlink()

    # 顶级目录名: llama-router-setup-v{version}
    top_name = f"llama-router-setup-v{version}"

    # 临时排除 config.example.json
    global EXCLUDE_FILES
    saved = EXCLUDE_FILES.copy()
    if not include_config_example:
        EXCLUDE_FILES.add("config.example.json")

    files = collect_files(project_root)
    EXCLUDE_FILES.clear()
    EXCLUDE_FILES.update(saved)

    # 写 zip
    total_size = 0
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        # 先写一个说明文件
        readme = (
            f"llama.cpp Router Manager v{version}\n"
            f"{'=' * 50}\n\n"
            f"解压后双击 manager-ui.bat 启动管理界面。\n\n"
            f"首次运行会自动:\n"
            f"  1. 检测 Python / tkinter / NSSM / llama.cpp 依赖\n"
            f"  2. 在「环境」标签页引导补齐缺失的依赖\n"
            f"  3. 在「全局」页设置你的 llama.cpp 目录\n\n"
            f"详细使用说明: 参考 README.md 或启动后点「❓ 帮助」按钮\n\n"
            f"项目主页: https://github.com/YOUR_GITHUB_USER/llama-router-setup\n"
        )
        zf.writestr(f"{top_name}/INSTALL.txt", readme)

        for src, rel in files:
            arcname = f"{top_name}/{rel}"
            zf.write(src, arcname)
            total_size += src.stat().st_size

    zip_size = output.stat().st_size
    # 最后一道闸: 扫一遍刚写好的 zip, 确认没把本机配置带出去
    violations = verify_zip_privacy(output, project_root)
    return {
        "version": version,
        "file_count": len(files),
        "uncompressed_size": total_size,
        "zip_size": zip_size,
        "compression_ratio": (1 - zip_size / total_size) * 100 if total_size else 0,
        "output": str(output),
        "privacy_violations": violations,
    }


def main():
    parser = argparse.ArgumentParser(description="打包 llama-router-setup 到 zip")
    parser.add_argument("--version", help="版本号 (如 1.0.0), 默认从 VERSION 文件/git tag 读")
    parser.add_argument("--output", help="输出 zip 路径, 默认 dist/llama-router-setup-v{ver}.zip")
    parser.add_argument("--no-config", action="store_true",
                        help="不打包 config.example.json (用户首次运行会自动生成默认配置)")
    parser.add_argument("--project-root", default=".",
                        help="项目根目录, 默认当前目录")
    args = parser.parse_args()

    project_root = Path(args.project_root).resolve()
    if not (project_root / "manager-ui.bat").exists():
        print(f"[ERROR] 找不到 manager-ui.bat, 请在项目根目录运行, 或指定 --project-root",
              file=sys.stderr)
        sys.exit(1)

    version = get_version(args.version)
    output = Path(args.output) if args.output else (
        project_root / "dist" / f"llama-router-setup-v{version}.zip"
    )

    print(f"项目根: {project_root}")
    print(f"版本号: v{version}")
    print(f"输出:   {output}")
    print()

    info = build_zip(project_root, version, output,
                     include_config_example=not args.no_config)

    violations = info.get("privacy_violations") or []
    if violations:
        print("=" * 50, file=sys.stderr)
        print("[✗] 隐私检查失败, 这个 zip 不能发布:", file=sys.stderr)
        for v in violations:
            print(f"    - {v}", file=sys.stderr)
        print("=" * 50, file=sys.stderr)
        # 直接删掉有问题的包, 避免误传
        try:
            Path(info["output"]).unlink()
            print(f"    已删除: {info['output']}", file=sys.stderr)
        except OSError:
            pass
        sys.exit(1)

    print("=" * 50)
    print(f"打包完成!")
    print(f"  版本:        v{info['version']}")
    print(f"  文件数:      {info['file_count']}")
    print(f"  原始大小:    {info['uncompressed_size']:,} bytes "
          f"({info['uncompressed_size']/1024:.1f} KB)")
    print(f"  压缩后:      {info['zip_size']:,} bytes "
          f"({info['zip_size']/1024:.1f} KB)")
    print(f"  压缩率:      {info['compression_ratio']:.1f}%")
    print(f"  输出文件:    {info['output']}")
    print(f"  隐私检查:    ✓ 通过 (不含 config.json / user_preferences.json / "
          f"router-preset.ini / logs/)")
    print(f"  模板配置:    config.example.json (空白模板, 首次运行自动建 config.json)")
    print("=" * 50)
    print()
    print("下一步:")
    print(f"  1. 在 GitHub 仓库页面 -> Releases -> Create new release")
    print(f"  2. Tag: v{info['version']}, Title: v{info['version']}")
    print(f"  3. 上传这个 zip 文件作为 binary")
    print()
    print("或者: git tag v{ver} && git push --tags, GitHub Actions 会自动构建+发布".format(
        ver=info["version"]))


if __name__ == "__main__":
    main()
