"""生成可分发的 zip 包 (用于 GitHub Releases)。

用法:
    py build_release.py                 # 默认打包当前目录到 dist/llama-router-setup-v0.1.0.zip
    py build_release.py --version 1.2.3 --output /tmp/release.zip
    py build_release.py --no-config     # 不带 config.example.json (更精简)

打包内容:
    - 所有 .py / .bat / .cmd / .ps1 / .vbs / .json(模板) / .md / .txt / .cmd 文件
    - tools/nssm.exe + tools/README.txt
    - .github/workflows/release.yml  (CI 配置, 让别人也能复现)
    - 不带: config.json (用户配置) / logs/ / __pycache__/ / *.pyc / 发布产物本身
"""
import argparse
import os
import sys
import zipfile
from pathlib import Path

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
EXCLUDE_FILES = {
    "config.json",
    "user_preferences.json",
    "config.example.json.local",
    ".gitignore",  # 开发者文件, 不需要给最终用户
    "publish_to_github.bat",  # 开发者用
    "build_release.py",  # 脚本本身
    "build_release.bat",  # 脚本本身
    "router-preset.ini",
    "LICENSE",  # 用户可能不需要看许可证, 留 LICENSE 也行, 这里选择不打包
}
EXCLUDE_EXTS = {
    ".pyc", ".pyo", ".pyd", ".so", ".dll", ".exe.bak",
    ".tmp", ".bak", ".swp", ".log",
}
# LICENSE 是个例外, 仍然想打包
EXCLUDE_FILES.discard("LICENSE")


def should_include(path: Path, project_root: Path) -> bool:
    """检查这个文件/目录是否应该被打包。"""
    rel = path.relative_to(project_root)
    parts = rel.parts
    # 排除目录
    for part in parts[:-1] if path.is_file() else parts:
        if part in EXCLUDE_DIRS:
            return False
    # 排除文件
    if path.is_file():
        if path.name in EXCLUDE_FILES:
            return False
        if path.suffix.lower() in EXCLUDE_EXTS:
            return False
    return True


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
    """确定版本号: 命令行参数 > 从 VERSION 文件读 > 从 git tag 读 > 默认 0.1.0。"""
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
    return "0.1.0"


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
            f"项目主页: https://github.com/loushijushi/llama-router-setup\n"
        )
        zf.writestr(f"{top_name}/INSTALL.txt", readme)

        for src, rel in files:
            arcname = f"{top_name}/{rel}"
            zf.write(src, arcname)
            total_size += src.stat().st_size

    zip_size = output.stat().st_size
    return {
        "version": version,
        "file_count": len(files),
        "uncompressed_size": total_size,
        "zip_size": zip_size,
        "compression_ratio": (1 - zip_size / total_size) * 100 if total_size else 0,
        "output": str(output),
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
