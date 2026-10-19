r"""把 banjiao/ 目录打包成标准 Skill 包（zip）。

输出位置（默认 dist/ 目录）：
    dist/banjiao.zip                - 整个 Skill 目录（含 SKILL.md）
    dist/banjiao-v0.2.0.zip         - 带版本号

大模型加载方式（取决于平台）：
    - Claude / Cursor / Windsurf：拖入 zip 或解压后的目录即可
    - Coze / Dify：上传 zip
    - 通用：解压后让 Agent 读 SKILL.md，调用 scripts/skill.py

用法：
    python scripts\package_skill.py
    python scripts\package_skill.py --output d:\my_skills\banjiao.zip
    python scripts\package_skill.py --no-tests  # 不带 tests/ 目录（更小）
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT_DIR = SKILL_ROOT / "dist"
GITHUB_REPO = "shao-xy/banjiao"  # ← 改这里


def package(output: Path, include_tests: bool, include_git: bool, version: str | None = None) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        output.unlink()
    print(f"→ 打包到 {output}")
    files_added = 0
    dirs_added = 0

    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        # 必须包含的核心
        must_have = ["SKILL.md", "README.md", "requirements.txt", "run_web.py", ".gitignore"]
        for name in must_have:
            p = SKILL_ROOT / name
            if p.exists():
                zf.write(p, arcname=f"banjiao/{name}")
                files_added += 1

        # 目录：scripts / src
        for sub in ("scripts", "src"):
            base = SKILL_ROOT / sub
            if not base.exists():
                continue
            for p in base.rglob("*"):
                # 跳过 __pycache__ 与 .pyc（编译缓存，没必要进 Skill）
                rel_parts = p.relative_to(SKILL_ROOT).parts
                if any(part == "__pycache__" for part in rel_parts):
                    continue
                if p.suffix == ".pyc":
                    continue
                if p.is_file():
                    arcname = f"banjiao/{p.relative_to(SKILL_ROOT).as_posix()}"
                    zf.write(p, arcname=arcname)
                    files_added += 1
                elif p.is_dir():
                    dirs_added += 1

        # 可选：tests / NOTES
        if include_tests:
            tests = SKILL_ROOT / "tests"
            if tests.exists():
                for p in tests.rglob("*"):
                    if any(part == "__pycache__" for part in p.relative_to(SKILL_ROOT).parts):
                        continue
                    if p.suffix == ".pyc":
                        continue
                    if p.is_file():
                        arcname = f"banjiao/{p.relative_to(SKILL_ROOT).as_posix()}"
                        zf.write(p, arcname=arcname)
                        files_added += 1

        if include_git:
            git = SKILL_ROOT / ".git"
            if git.exists():
                # .git 一般不入 Skill 包
                pass

        # 加 PACKAGE.md 说明文件
        if version:
            readme = (
                f"# 伴教 Skill v{version}\n\n"
                f"打包时间：{__import__('datetime').datetime.now().isoformat(timespec='seconds')}\n\n"
                f"## 加载后第一步\n\n"
                f"1. 阅读 `banjiao/SKILL.md` 了解能力边界与 actions\n"
                f"2. 安装依赖：`pip install -r banjiao/requirements.txt`\n"
                f"3. 测试入口：`python banjiao/scripts/skill.py`（无参打印帮助）\n"
                f"4. 跑演示：`python banjiao/scripts/demo.py`\n"
                f"5. 启动网页：`python banjiao/run_web.py`\n\n"
                f"## Actions 一览\n\n"
                f"- 课程：course.list / get / create / update / delete / today / week / apply_period /\n"
                f"  set_outline_path / get_outline / refresh_outline / next_topic\n"
                f"- 进度：progress.list / create / update / delete\n"
                f"- 作业：homework.list / create / update / delete / due_soon\n"
                f"- 事务：affair.list / create / update / delete / mark_reminded\n"
                f"- 文件系统（7 角色 + 增量扫描 + 历史 + 归档）：\n"
                f"  fs.init / list_top_dirs / bind_course / unbind_course / scan_course / scan_top_dir\n"
                f"  course.list_files / fs.read_file / fs.file_history / fs.write_text / fs.archive / fs.delete\n"
                f"- 学校时间表：school.get_schedule / school.set_schedule\n"
                f"- 文档/向量（可选）：doc.* / vec.*\n"
            )
            zf.writestr("banjiao/PACKAGE.md", readme)
            files_added += 1

    size_mb = output.stat().st_size / 1024 / 1024
    print(f"✓ 完成：{files_added} 个文件 + {dirs_added} 个目录，{size_mb:.2f} MB")
    return output


def main() -> int:
    p = argparse.ArgumentParser(description="把 banjiao/ 目录打包成 Skill zip / 解压目录")
    p.add_argument("--output", type=Path, default=None,
                   help="输出路径（默认 dist/banjiao.zip）")
    p.add_argument("--no-tests", action="store_true",
                   help="不打包 tests/ 目录（产物更小，给 Agent 用够了）")
    p.add_argument("--version", default=None,
                   help="写入 PACKAGE.md 的版本号（如 0.2.0）")
    p.add_argument("--extract", action="store_true",
                   help="额外解压出【解压即用】目录（适合直接拖入 TRAE/Claude）")
    p.add_argument("--release", action="store_true",
                   help="打包后自动 gh release create v{version}（需要 gh CLI 已登录）")
    args = p.parse_args()

    output = args.output or (DEFAULT_OUTPUT_DIR / "banjiao.zip")
    package(
        output=output,
        include_tests=not args.no_tests,
        include_git=False,
        version=args.version,
    )

    # 同时输出"无测试版"到 dist/
    if not args.no_tests and args.output is None:
        slim = DEFAULT_OUTPUT_DIR / "banjiao-slim.zip"
        package(output=slim, include_tests=False, include_git=False, version=args.version)
        print(f"\n两个版本：\n  完整: {output}\n  精简: {slim}")

        if args.extract:
            # 把精简版解压到 dist/banjiao-extracted/（TRAE 可直接拖入）
            extract_dir = DEFAULT_OUTPUT_DIR / "banjiao-extracted"
            if extract_dir.exists():
                shutil.rmtree(extract_dir, ignore_errors=True)
            extract_dir.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(slim) as zf:
                zf.extractall(extract_dir)
            print(f"  解压: {extract_dir}/banjiao/  ← 直接拖入 TRAE / Claude 对话窗")

        # GitHub Release（可选）
        if args.release:
            if not args.version:
                print("✗ --release 必须同时传 --version")
                return 1
            tag = f"v{args.version}"
            print(f"\n→ 创建 GitHub Release {tag}")
            notes = (
                f"## 伴教 Skill {tag}\n\n"
                f"直接下载 banjiao-slim.zip（0.06 MB，不含 tests/），解压后让 Agent 读 SKILL.md，\n"
                f"调 scripts/skill.py 子进程操作 SQLite 数据。\n\n"
                f"### 大模型加载\n\n"
                f"- **TRAE / Claude 对话窗**：拖入 `banjiao/` 文件夹\n"
                f"- **zip URL**：https://github.com/{GITHUB_REPO}/releases/download/{tag}/banjiao-slim.zip\n"
                f"- **git clone**：`git clone https://github.com/{GITHUB_REPO}.git`\n"
            )
            try:
                r = subprocess.run(
                    ["gh", "release", "create", tag,
                     str(slim),  # banjiao-slim.zip
                     "--repo", GITHUB_REPO,
                     "--title", f"伴教 Skill {tag}",
                     "--notes", notes,
                     "--latest"],  # 标记为 latest
                    capture_output=True, text=True,
                )
                if r.returncode == 0:
                    print(f"✓ Release 创建成功：{r.stdout.strip()}")
                else:
                    print(f"✗ gh release 失败：{r.stderr.strip()}")
                    print("  可能原因：gh CLI 未登录 / repo 不存在 / 权限不足")
                    print("  手动替代：gh release create " + tag + " " + str(slim))
            except FileNotFoundError:
                print("✗ gh 命令未找到。请先安装 GitHub CLI：https://cli.github.com/")
        elif not args.release:
            print(f"\n  安装到 TRAE：拖入 {extract_dir}/banjiao/ 整个文件夹")
            print(f"  或 zip URL（发布后）：https://github.com/{GITHUB_REPO}/releases/latest/download/banjiao-slim.zip")

    return 0


if __name__ == "__main__":
    sys.exit(main())