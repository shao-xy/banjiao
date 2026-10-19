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
import sys
import zipfile
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT_DIR = SKILL_ROOT / "dist"


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
    p = argparse.ArgumentParser(description="把 banjiao/ 目录打包成 Skill zip")
    p.add_argument("--output", type=Path, default=None,
                   help="输出路径（默认 dist/banjiao.zip）")
    p.add_argument("--no-tests", action="store_true",
                   help="不打包 tests/ 目录（产物更小，给 Agent 用够了）")
    p.add_argument("--version", default=None,
                   help="写入 PACKAGE.md 的版本号（如 0.2.0）")
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

    return 0


if __name__ == "__main__":
    sys.exit(main())