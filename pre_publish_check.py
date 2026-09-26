"""发布前自动检查 (在 AI 助手或开发者准备发布时调用)。

检查项:
  1. 当前修改了什么文件
  2. README.md 是否提到当前版本号 (没有就提示更新)
  3. 安装指南.txt 是否包含最新功能说明 (有 TODO/FIXME 标记就提示)
  4. CHANGELOG.md 是否有本次发布的条目 (没有就提示填写)
  5. VERSION 文件版本号是否与上次发布 tag 一致 (一致说明没递增, 提示)
  6. 是否有未提交/未推送的修改 (防止发布时丢失)
  7. README 中是否有过时内容
  8. **隐私检查**: 本机配置 (config.json / user_preferences.json /
     router-preset.ini) 是否会被公开 —— 跟踪状态、.gitignore、
     build_release.py 排除项、以及跟踪文件里有没有本机模型路径/模型名/api_key

用法:
  py pre_publish_check.py              # 默认检查
  py pre_publish_check.py --auto-fix   # 给出建议后自动生成 CHANGELOG 草稿
"""
import argparse
import json
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

# 强制 UTF-8 输出
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


def run(cmd: list, cwd: str = None) -> tuple:
    """Run shell command, return (returncode, stdout, stderr)."""
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", cwd=cwd)
    return r.returncode, r.stdout.strip(), r.stderr.strip()


def git(*args) -> tuple:
    """Run git command in the project root."""
    return run(["git", "-C", str(PROJECT_ROOT)] + list(args))


def private_markers() -> list:
    """从本机 config.json 提取有辨识度的标识 (模型路径 / 模型名 / api_key)。

    只读, 不会修改或删除 config.json。
    """
    cfg = PROJECT_ROOT / "config.json"
    if not cfg.exists():
        return []
    try:
        data = json.loads(cfg.read_text(encoding="utf-8"))
    except Exception:
        return []
    found = []
    for m in data.get("models", []):
        for k in ("model", "mmproj", "base_url", "api_key", "id", "alias"):
            v = str(m.get(k) or "").strip()
            if v:
                found.append(v)
    for m in list(found):
        if "\\" in m or "/" in m:
            found.append(m.replace("\\", "/"))
    seen, out = set(), []
    for s in found:
        if len(s) < 4 or s in seen:
            continue
        seen.add(s)
        out.append(s)
    return out


def mask_secret(s: str) -> str:
    """报错信息里不要原样打印 api_key。"""
    if len(s) <= 8:
        return s[:2] + "***"
    return s[:4] + "***" + s[-4:]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--auto-fix", action="store_true",
                        help="自动生成 CHANGELOG.md 草稿 (从 git log 自动生成)")
    args = parser.parse_args()

    print("=" * 60)
    print("  发布前自动检查 (pre_publish_check.py)")
    print("=" * 60)
    print()

    issues = []
    warnings = []
    passed = []

    # ----- 1. 检查未推送/未提交修改 -----
    rc, out, _ = git("status", "--short", "--branch")
    if out:
        lines = out.split("\n", 1)
        branch_info = lines[0]
        changes = lines[1] if len(lines) > 1 else ""
        if changes.strip():
            issues.append(f"有未提交的修改:\n{changes}")
        else:
            passed.append("工作区干净 (无未提交修改)")
    else:
        issues.append("无法读取 git status")

    # 检查 ahead/behind
    rc, out, _ = git("rev-list", "--count", "--left-right", "@{u}...HEAD")
    if rc == 0 and out:
        behind, ahead = out.split("\t")
        if ahead != "0":
            warnings.append(f"本地有 {ahead} 个 commit 未推送到远端 (发布前需要 push)")
        if behind != "0":
            warnings.append(f"远端有 {behind} 个 commit 本地没有 (需要先 pull)")

    # ----- 2. 检查 VERSION 文件 -----
    ver_file = PROJECT_ROOT / "VERSION"
    if not ver_file.exists():
        issues.append("VERSION 文件不存在, 需要新建并填入当前版本号")
    else:
        current_ver = ver_file.read_text(encoding="utf-8").strip()
        print(f"[VERSION] 当前版本: {current_ver}")

        # 对比上次 git tag
        rc, out, _ = git("tag", "--list", "--sort=-version:refname")
        if rc == 0 and out:
            tags = [t for t in out.split("\n") if t.startswith("v")]
            if tags:
                last_tag = tags[0]
                last_ver = last_tag.lstrip("v")
                if last_ver == current_ver:
                    warnings.append(
                        f"VERSION 还是 {current_ver} (上次 tag 也是这个).\n"
                        f"   发布前请先递增版本号, 否则会创建重复的 release"
                    )
                else:
                    passed.append(f"版本号已递增: {last_ver} -> {current_ver}")
        else:
            warnings.append("无法读取 git tag (没有 tag?)")

    # ----- 3. 检查 README.md -----
    readme = PROJECT_ROOT / "README.md"
    if not readme.exists():
        issues.append("README.md 不存在")
    else:
        content = readme.read_text(encoding="utf-8")
        if "VERSION" in str(PROJECT_ROOT) and current_ver:
            if current_ver not in content:
                warnings.append(
                    f"README.md 中没有提到当前版本号 {current_ver}\n"
                    f"   建议在 README 顶部徽章或示例中更新"
                )
            else:
                passed.append(f"README.md 包含版本号 {current_ver}")
        # 检查是否有未替换的占位符
        if "TODO" in content or "FIXME" in content or "XXX" in content:
            warnings.append("README.md 含有 TODO/FIXME/XXX 占位符, 建议替换或删除")

    # ----- 4. 检查安装指南.txt -----
    guide = PROJECT_ROOT / "安装指南.txt"
    if not guide.exists():
        warnings.append("安装指南.txt 不存在 (建议添加中文 FAQ)")
    else:
        content = guide.read_text(encoding="utf-8")
        if "TODO" in content or "FIXME" in content:
            warnings.append("安装指南.txt 含有 TODO/FIXME 占位符, 建议替换")

    # ----- 5. 检查 CHANGELOG.md -----
    changelog = PROJECT_ROOT / "CHANGELOG.md"
    if not changelog.exists():
        warnings.append(
            "CHANGELOG.md 不存在\n"
            "   建议创建并记录每次发布内容 (格式参考 Keep a Changelog)"
        )
        if args.auto_fix:
            create_changelog_template()
            passed.append("已自动创建 CHANGELOG.md 模板")
    else:
        content = changelog.read_text(encoding="utf-8")
        if current_ver and current_ver not in content:
            warnings.append(
                f"CHANGELOG.md 中没有当前版本 {current_ver} 的条目\n"
                f"   建议在 [Unreleased] 写完后移到 [{current_ver}] - {date.today()}"
            )
        else:
            passed.append(f"CHANGELOG.md 包含版本 {current_ver}")

    # ----- 6. 自动从 git log 生成 CHANGELOG 草稿 -----
    if args.auto_fix and changelog.exists():
        append_changelog_from_git(changelog, current_ver)

    # ----- 7. 检查 README 中过时内容 -----
    if readme.exists():
        content = readme.read_text(encoding="utf-8")
        # 常见过时内容标记
        outdated = []
        if "v1.0.0.zip" in content:
            outdated.append("下载示例还是 'llama-router-setup-v1.0.0.zip'")
        if "默认版本 0.1.0" in content:
            outdated.append("build_release.py 默认版本 0.1.0")
        if outdated:
            warnings.append("README.md 可能有过时内容:\n  - " + "\n  - ".join(outdated))

    # ----- 8. 隐私检查: 本机配置绝不能被公开 -----
    print("[隐私检查] 本机配置是否会被发布出去 ...")
    PRIVATE_FILES = ("config.json", "user_preferences.json", "router-preset.ini")

    # 8.1 必须被 .gitignore 必须没被跟踪
    for pf in PRIVATE_FILES:
        rc_ig, _, _ = git("check-ignore", "-q", pf)
        if rc_ig != 0:
            issues.append(
                f"{pf} 不在 .gitignore 里 -> 一旦 git add 就会被推到公开仓库\n"
                f"   处理: 把 {pf} 加进 .gitignore")
        rc_tr, _, _ = git("ls-files", "--error-unmatch", pf)
        if rc_tr == 0:
            issues.append(
                f"{pf} 已被 git 跟踪 (会随仓库公开!)\n"
                f"   处理: git rm --cached {pf} && git commit -m 'remove private {pf}'")
        else:
            passed.append(f"{pf} 未被 git 跟踪")

    # 8.2 build_release.py 必须把它们排除
    br = PROJECT_ROOT / "build_release.py"
    if br.exists():
        br_txt = br.read_text(encoding="utf-8")
        for pf in ("config.json", "user_preferences.json", "router-preset.ini"):
            if f'"{pf}"' not in br_txt and f"'{pf}'" not in br_txt:
                issues.append(
                    f"build_release.py 没有排除 {pf} -> 会被打进发布 zip")
        if "verify_zip_privacy" in br_txt:
            passed.append("build_release.py 打包后会复扫 zip (verify_zip_privacy)")
        else:
            warnings.append("build_release.py 缺少打包后的隐私复扫 (verify_zip_privacy)")
    else:
        warnings.append("build_release.py 不存在, 无法校验打包排除项")

    # 8.3 跟踪文件里不能出现本机模型路径 / 模型名 / api_key
    markers = private_markers()
    if markers:
        rc_ls, tracked, _ = git("-c", "core.quotepath=false", "ls-files")
        leaked = []
        if rc_ls == 0:
            for rel in tracked.splitlines():
                rel = rel.strip()
                if not rel:
                    continue
                fp = PROJECT_ROOT / rel
                if not fp.is_file() or fp.stat().st_size > 2 * 1024 * 1024:
                    continue
                try:
                    text = fp.read_text(encoding="utf-8")
                except Exception:
                    try:
                        text = fp.read_text(encoding="gbk")
                    except Exception:
                        continue
                for mk in markers:
                    if mk in text:
                        leaked.append((rel, mk))
        if leaked:
            for rel, mk in leaked:
                issues.append(f"跟踪文件 {rel} 泄漏本机信息: 含 '{mask_secret(mk)}'")
        else:
            passed.append(f"跟踪文件未发现本机配置痕迹 (比对 {len(markers)} 个标识)")
    else:
        print("  (本机没有 config.json, 跳过内容比对)")

    # ----- 输出结果 -----
    print()
    print("=" * 60)
    if passed:
        print("[✓] 通过:")
        for p in passed:
            print(f"  - {p}")
        print()
    if warnings:
        print("[!] 警告 (建议处理):")
        for w in warnings:
            print(f"  - {w}")
        print()
    if issues:
        print("[✗] 错误 (必须处理):")
        for i in issues:
            print(f"  - {i}")
        print()

    if issues:
        print("[结论] 有错误, 不建议发布")
        sys.exit(1)
    elif warnings:
        print("[结论] 有警告, 建议处理后再发布")
        sys.exit(0)
    else:
        print("[结论] 一切就绪, 可以发布了")
        sys.exit(0)


def create_changelog_template():
    """创建 CHANGELOG.md 模板。"""
    from datetime import date
    template = f"""# 更新日志 (Changelog)

本项目的所有重要变更都记录在此文件。

格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/),
版本号遵循 [Semantic Versioning](https://semver.org/lang/zh-CN/)。

## [Unreleased]

### 新增 (Added)

### 变更 (Changed)

### 修复 (Fixed)

### 移除 (Removed)

---

## [{PROJECT_VERSION}] - {date.today().isoformat()}

### 新增 (Added)

### 变更 (Changed)

### 修复 (Fixed)
"""
    (PROJECT_ROOT / "CHANGELOG.md").write_text(template, encoding="utf-8")
    print(f"  -> CHANGELOG.md 已创建")


def append_changelog_from_git(changelog_path: Path, current_ver: str):
    """从上次 tag 到当前的 git commits, 自动填充到 [Unreleased] 段。"""
    rc, last_tag, _ = git("describe", "--tags", "--abbrev=0")
    if rc != 0:
        print("  -> 无法获取 last tag, 跳过")
        return
    rc, log, _ = git("log", f"{last_tag}..HEAD", "--pretty=format:%s")
    if rc != 0 or not log.strip():
        print(f"  -> 自 {last_tag} 以来无新 commit, 跳过")
        return
    print(f"  -> 自 {last_tag} 以来 {len(log.split(chr(10)))} 个新 commit")

    # 简单分类
    content = changelog_path.read_text(encoding="utf-8")
    added, changed, fixed = [], [], []
    for line in log.split("\n"):
        line = line.strip()
        if not line:
            continue
        low = line.lower()
        if any(k in low for k in ["fix", "bug", "修复"]):
            fixed.append(line)
        elif any(k in low for k in ["add", "feat", "new", "增加", "新增", "添加"]):
            added.append(line)
        else:
            changed.append(line)

    # 替换 [Unreleased] 段
    new_unreleased = "## [Unreleased]\n\n"
    if added:
        new_unreleased += "### 新增 (Added)\n\n" + "\n".join(f"- {x}" for x in added) + "\n\n"
    if changed:
        new_unreleased += "### 变更 (Changed)\n\n" + "\n".join(f"- {x}" for x in changed) + "\n\n"
    if fixed:
        new_unreleased += "### 修复 (Fixed)\n\n" + "\n".join(f"- {x}" for x in fixed) + "\n\n"
    new_unreleased += "---\n\n"

    # 用正则替换
    new_content = re.sub(
        r"## \[Unreleased\][\s\S]*?---\n\n",
        new_unreleased,
        content,
        count=1,
    )
    if new_content == content:
        # 没匹配上, 在文件最上面插入
        new_content = new_unreleased + content
    changelog_path.write_text(new_content, encoding="utf-8")
    print(f"  -> CHANGELOG.md [Unreleased] 段已更新 (added={len(added)}, changed={len(changed)}, fixed={len(fixed)})")


# 项目根目录
PROJECT_ROOT = Path(__file__).parent.resolve()
PROJECT_VERSION = (PROJECT_ROOT / "VERSION").read_text(encoding="utf-8").strip() \
    if (PROJECT_ROOT / "VERSION").exists() else "0.0.0"


if __name__ == "__main__":
    main()
