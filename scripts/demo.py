"""伴教 Skill 端到端演示脚本。

从空 data/ 目录开始，用子进程调 scripts/skill.py（**走生产路径**），
把典型流程跑一遍：建课 → 绑文件夹 → 写大纲 → 刷 cache → 算 next_topic
→ 看本周课表 → 看时间表 → 写作业 → 写进度 → 重建 PDF/PPT。

每步打印一行"步骤说明 + JSON 结果"，跑完会输出 SKILL.md / README.md 没写清楚的隐藏行为。

用法（PowerShell）：
    cd banjiao
    # 用临时 data 目录演示（推荐）
    $env:BANJIAO_DATA_DIR = "$env:TEMP\\banjiao_demo_$(Get-Random)"
    python scripts\\demo.py

或者直接跑（污染默认 data/）：
    python scripts\\demo.py
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

# 让 SKILL_ROOT / src 可被 import（不调 service，只调 skill.py 子进程）
SKILL_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = SKILL_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

# sys.executable = 当前 Python 解释器
PYTHON = sys.executable


def _banner(title: str) -> None:
    line = "─" * 64
    print(f"\n{line}\n  {title}\n{line}")


def call_skill(action: str, payload: dict | None = None) -> dict:
    """调一次 scripts/skill.py 子进程，返回解析后的 JSON dict。"""
    cmd = [PYTHON, "-u", str(SKILL_ROOT / "scripts" / "skill.py"), action]
    if payload is not None:
        cmd += ["--json", json.dumps(payload, ensure_ascii=False)]
    proc = subprocess.run(cmd, capture_output=True, text=True, cwd=str(SKILL_ROOT), env=os.environ)
    out = (proc.stdout or "").strip()
    if not out:
        raise RuntimeError(f"skill.py {action} 没输出 stdout；stderr={proc.stderr!r}")
    return json.loads(out)


def step(desc: str, action: str, payload: dict | None = None, expect_ok: bool = True) -> dict:
    """打印一步说明 + 调 skill + 检查 ok + 简洁结果。"""
    print(f"\n▶ {desc}")
    print(f"  → skill.py {action} {payload if payload else ''}")
    result = call_skill(action, payload)
    ok = result.get("ok") is True
    if expect_ok and not ok:
        print(f"  ✗ 失败：{result.get('error')}")
        raise SystemExit(1)
    if ok:
        data = result.get("data")
        # 只展示最关键几个字段
        if isinstance(data, dict):
            keys = [k for k in ("id", "name", "ok", "written", "has_file", "stale",
                                  "target_week", "current_week", "lag_weeks",
                                  "versions", "lessons_count") if k in data]
            brief = {k: data[k] for k in keys}
        elif isinstance(data, list):
            brief = f"list len={len(data)}"
        else:
            brief = data
        print(f"  ✓ {brief}")
    else:
        print(f"  · 错误：{result.get('error')}")
    return result


def main() -> int:
    print("═" * 64)
    print("  伴教 Skill 端到端演示")
    print(f"  Python: {PYTHON}")
    print(f"  data_dir: {os.environ.get('BANJIAO_DATA_DIR') or '(默认 SKILL_ROOT/data)'}")
    print("═" * 64)

    # ============== 准备：清空演示 data 目录 ==============
    data_dir_env = os.environ.get("BANJIAO_DATA_DIR")
    if data_dir_env:
        d = Path(data_dir_env)
        if d.exists():
            shutil.rmtree(d, ignore_errors=True)
        print(f"\n[清空] {d}")

    # ============== 步骤 1: fs.init ==============
    _banner("步骤 1/9 · 文件系统初始化")
    step("建 4 个顶层文件夹（教学/科研/事务/文件）", "fs.init")

    # ============== 步骤 2: 准备物理目录 ==============
    _banner("步骤 2/9 · 物理目录准备")
    data_dir = Path(os.environ["BANJIAO_DATA_DIR"]) if data_dir_env else (SKILL_ROOT / "data")
    course_dir = data_dir / "教学" / "高等数学"
    outline_dir = course_dir / "大纲"
    slides_dir = course_dir / "幻灯片"
    for d in (course_dir, outline_dir, slides_dir):
        d.mkdir(parents=True, exist_ok=True)
    print(f"  → mkdir {course_dir}")

    # ============== 步骤 3: course.create ==============
    _banner("步骤 3/9 · 建课程（含单双周 + 节次）")
    r = step(
        "建课：单周周二 1-2 节 + 学校第 1 节",
        "course.create",
        {
            "name": "高等数学",
            "class_group": "2024-A",
            "location": "理科楼 A301",
            "weekday": 2,
            "semester_start": "2026-09-07",
            "semester_end": "2027-01-10",
            "week_pattern": "odd",
            "period_no": 1,
        },
    )
    course_id = r["data"]["id"]
    print(f"  course_id = {course_id}")

    # ============== 步骤 4: fs.bind_course ==============
    _banner("步骤 4/9 · 把课程绑到文件夹")
    step(
        "绑：教学/高等数学 → course_id",
        "fs.bind_course",
        {"top_kind": "教学", "dirname": "高等数学", "course_id": course_id},
    )

    # ============== 步骤 5: course.apply_period ==============
    _banner("步骤 5/9 · 节次 → 起止时间")
    step("用学校时间表第 1 节（08:00-08:45）填 start_time/end_time",
         "course.apply_period", {"id": course_id})

    # ============== 步骤 6: 写大纲 + 刷 cache + 读 ==============
    _banner("步骤 6/9 · 写大纲 → 刷新缓存 → 读")
    outline_file = outline_dir / "outline.md"
    outline_file.write_text(
        """# 高等数学（上）大纲

第 1 周：极限的定义、极限的四则运算
第 3 周：连续函数与间断点
第 5 周：导数定义、求导法则
第 7 周：微分中值定理
第 9 周：不定积分
第 11 周：定积分
""",
        encoding="utf-8",
    )
    print(f"  → 已写 {outline_file.name} ({outline_file.stat().st_size} 字节)")

    step("指定大纲文件", "course.set_outline_path",
         {"id": course_id, "filename": "outline.md"})

    parsed_outline = {
        "weeks": [
            {"week": 1, "topics": ["极限定义", "极限四则运算"]},
            {"week": 3, "topics": ["连续", "间断点"]},
            {"week": 5, "topics": ["导数", "求导法则"]},
            {"week": 7, "topics": ["微分中值定理"]},
            {"week": 9, "topics": ["不定积分"]},
            {"week": 11, "topics": ["定积分"]},
        ]
    }
    step("回填大纲 cache（模拟大模型解析后）", "course.refresh_outline",
         {"id": course_id, "parsed": parsed_outline})

    r = step("读大纲（应 has_file=True, cache_present=True, stale=False）",
             "course.get_outline", {"id": course_id})

    # ============== 步骤 7: next_topic 几次不同日期 ==============
    _banner("步骤 7/9 · 下一讲建议（不同时期）")
    step("开学前：target 9-15（week 1）", "course.next_topic",
         {"id": course_id, "target_date": "2026-09-15"})
    step("开学第 3 周：target 9-29（week 3）", "course.next_topic",
         {"id": course_id, "target_date": "2026-09-29"})

    # 加一条 progress
    step("记第 1 周进度：讲了'极限定义'",
         "progress.create",
         {"course_id": course_id, "lesson_date": "2026-09-08", "topic": "极限定义"})

    step("再算：target 9-29 仍说'极限'（current_week=1, lag=2）",
         "course.next_topic", {"id": course_id, "target_date": "2026-09-29"})

    # ============== 步骤 8: 写作业 + 进度 ==============
    _banner("步骤 8/9 · 作业 + 事务")
    step("发作业：习题 1.1（due 5 天后）", "homework.create",
         {"title": "习题 1.1 极限计算",
          "course_id": course_id, "class_group": "2024-A",
          "published_at": "2026-09-08", "due_at": "2026-09-13"})

    step("添加事务：周五教研会", "affair.create",
         {"title": "本周教研会", "kind": "meeting",
          "due_at": "2026-09-11T15:00:00", "remind_before_minutes": 60})

    # ============== 步骤 9: 看本周课表 + 时间表 ==============
    _banner("步骤 9/9 · 课程表 + 学校时间表")
    r = step("本周课表（基于 odd 周）", "course.week", {})
    if isinstance(r.get("data"), list):
        print(f"  → 本周 {len(r['data'])} 节课")

    step("学校默认时间表", "school.get_schedule")

    # ============== 文件系统列表（看一眼） ==============
    _banner("附录 · 当前文件系统（教学/高等数学）")
    for role in ("大纲", "幻灯片"):
        d = course_dir / role
        if d.exists():
            for f in sorted(d.iterdir()):
                if f.is_file():
                    print(f"  {role}/  {f.name}  ({f.stat().st_size}B)")

    print(f"\n{'═' * 64}")
    print("  ✓ 演示完成")
    print(f"  data_dir: {data_dir}")
    print("  下一步：python run_web.py → 浏览器打开 127.0.0.1:30826 看可视化")
    print(f"{'═' * 64}")
    return 0


if __name__ == "__main__":
    sys.exit(main())