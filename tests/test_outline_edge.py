"""阶段 ④ 测试：大纲读取/缓存/进度结合。

新增 action：
    course.set_outline_path  --json '{"id":"...","filename":"outline.md"}'
    course.get_outline       --json '{"id":"..."}'
    course.refresh_outline   --json '{"id":"...","parsed":{"weeks":[{"week":1,"topics":["极限"]}]}}'
    course.next_topic        --json '{"id":"...","target_date":"2026-10-20"}'

约束：
    set_outline_path 必须绑定 + 大纲角色子目录里有该文件
    get_outline 给出 has_file / cache_present / stale 三个布尔事实
    refresh_outline 不调 LLM；只把 parsed 写回 outline_cache + file_mtime_ns
    next_topic 用大纲 + ProgressEntry 算出事实卡：target_week / current_week / lag_weeks
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
SRC_DIR = ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))


# ===== 复用最小测试框架 =====

class _Color:
    OK = "\033[92m"
    FAIL = "\033[91m"
    END = "\033[0m"


_failures: list[tuple[str, str]] = []
_passes = 0
_section = ""


def section(name: str) -> None:
    global _section
    _section = name
    print(f"\n=== {name} ===")


def expect(label: str, cond: bool, detail: str = "") -> None:
    global _passes
    if cond:
        _passes += 1
        print(f"  {_Color.OK}PASS{_Color.END} {label}")
    else:
        msg = f"[{_section}] {label}" + (f"  -- {detail}" if detail else "")
        _failures.append((_section, msg))
        print(f"  {_Color.FAIL}FAIL{_Color.END} {label}{('  -- ' + detail) if detail else ''}")


def expect_raises(label: str, fn, exc_type=Exception, contains: str = "") -> None:
    try:
        fn()
    except Exception as e:  # noqa: BLE001
        if isinstance(e, exc_type):
            haystack = str(e)
            if contains and contains not in haystack:
                expect(label, False, f"抛错但消息不含 {contains!r}：{haystack[:300]}")
                return
            expect(label, True)
            return
        expect(label, False, f"抛出 {type(e).__name__} 而非 {exc_type.__name__}: {e}")
        return
    expect(label, False, "未抛错")


# ===== 隔离数据目录 =====

_TEST_DIR = Path(tempfile.mkdtemp(prefix="banjiao_outline_test_"))
os.environ["BANJIAO_DATA_DIR"] = str(_TEST_DIR / "data")
os.environ.setdefault("BANJIAO_ENABLE_VECTOR", "0")

for mod in list(sys.modules):
    if mod.startswith("banjiao."):
        del sys.modules[mod]
sys.modules.pop("banjiao", None)

from banjiao import config, db as _db_mod, service, models  # noqa: E402
from banjiao.config import settings  # noqa: E402
from banjiao.llm_errors import SkillError  # noqa: E402


def cleanup() -> None:
    if _TEST_DIR.exists():
        shutil.rmtree(_TEST_DIR, ignore_errors=True)


def fresh_data_dir() -> None:
    try:
        _db_mod.db._conn.close()
    except Exception:
        pass
    _db_mod.db._conn = None
    if _TEST_DIR.exists():
        shutil.rmtree(_TEST_DIR, ignore_errors=True)
    _TEST_DIR.mkdir(parents=True, exist_ok=True)
    (_TEST_DIR / "data").mkdir(parents=True, exist_ok=True)
    _db_mod.db.__init__()


def make_bound_course(name: str = "数学分析", **extra) -> models.Course:
    service.fs_init()
    c = service.create_course({"name": name, **extra})
    top = settings.data_dir / "教学" / name
    top.mkdir(parents=True, exist_ok=True)
    for r in ("幻灯片", "讲义", "资料", "作业", "提交", "进度参考", "大纲"):
        (top / r).mkdir(exist_ok=True)
    service.fs_bind_course("教学", name, c.id)
    return c


def write_outline(name: str, content: str) -> Path:
    p = settings.data_dir / "教学" / name / "大纲" / "outline.md"
    p.write_text(content, encoding="utf-8")
    return p


# ===== 测试组 =====

def t_set_outline_path() -> None:
    section("4.1 course.set_outline_path：必须文件存在")
    fresh_data_dir()
    c = make_bound_course()
    # 没文件 → not_found
    expect_raises(
        "未放文件 set → not_found",
        lambda: service.course_set_outline_path(c.id, "outline.md"),
        SkillError,
        contains="大纲文件",
    )
    # 写文件
    write_outline("数学分析", "# outline v1")
    c2 = service.course_set_outline_path(c.id, "outline.md")
    expect("set 后 outline_path 写入", c2.outline_path == "outline.md")
    expect("set 后 cache 清空", c2.outline_cache is None)


def t_get_outline() -> None:
    section("4.2 course.get_outline：has_file / stale 判定")
    fresh_data_dir()
    c = make_bound_course()
    write_outline("数学分析", "v1")
    service.course_set_outline_path(c.id, "outline.md")
    o1 = service.course_get_outline(c.id)
    expect("has_file True", o1["has_file"] is True)
    expect("cache_present False（刚 set）", o1["cache_present"] is False)
    expect("stale False（无 cache）", o1["stale"] is False)
    expect("text 含 v1", o1["text"] is not None and "v1" in o1["text"])

    # refresh_outline 后 cache_present True；mtime 一致 → stale False
    service.course_refresh_outline(c.id, {"weeks": [{"week": 1, "topics": ["极限"]}]})
    o2 = service.course_get_outline(c.id)
    expect("refresh 后 cache_present True", o2["cache_present"] is True)
    expect("refresh 后 stale False（mtime 同步）", o2["stale"] is False)
    expect("cached.weeks 第 1 周 topics=极限", o2["cached"]["weeks"][0]["topics"] == ["极限"])

    # 文件改了 mtime 变 → stale True
    time.sleep(0.05)
    (settings.data_dir / "教学" / "数学分析" / "大纲" / "outline.md").write_text("v2 完全不同", encoding="utf-8")
    o3 = service.course_get_outline(c.id)
    expect("文件改后 stale True", o3["stale"] is True)
    expect("text 含 v2", "v2" in o3["text"])


def t_refresh_outline_格式() -> None:
    section("4.3 course.refresh_outline 入参校验")
    fresh_data_dir()
    c = make_bound_course()
    write_outline("数学分析", "x")
    service.course_set_outline_path(c.id, "outline.md")
    expect_raises(
        "parsed 非 dict 拒",
        lambda: service.course_refresh_outline(c.id, "not a dict"),
        SkillError,
        contains="parsed",
    )
    # 合法 dict → ok
    c2 = service.course_refresh_outline(c.id, {"weeks": [{"week": 1, "topics": ["A"]}]})
    expect("合法 parsed 后 outline_cache 非空", c2.outline_cache is not None)


def t_next_topic() -> None:
    section("4.4 course.next_topic：事实卡")
    fresh_data_dir()
    # 学期：2026-03-02（周一）= 第 1 周
    c = make_bound_course(
        name="高数",
        weekday=1,  # 周二
        start_time="08:00", end_time="09:50",
        semester_start="2026-03-02",
        semester_end="2026-06-30",
        week_pattern="all",
    )
    # 写大纲 + set + refresh
    write_outline("高数", "# 大纲")
    service.course_set_outline_path(c.id, "outline.md")
    parsed = {
        "weeks": [
            {"week": 1, "topics": ["极限定义", "极限运算"]},
            {"week": 2, "topics": ["连续", "间断点"]},
            {"week": 3, "topics": ["导数"]},
            {"week": 4, "topics": ["微分中值定理"]},
        ]
    }
    service.course_refresh_outline(c.id, parsed)

    # 场景 A：无进度 + target 第 2 周 → current_week=None, lag_weeks=2
    r1 = service.course_next_topic(c.id, date(2026, 3, 9))  # 周一 = 第 2 周
    expect("无进度 current_week None", r1["current_week"] is None)
    expect("target_week 2", r1["target_week"] == 2)
    expect("outline_target 含 连续", r1["outline_target"] is not None and "连续" in r1["outline_target"])
    expect("hint 提示未开讲", r1["hint"].startswith("还没有"))

    # 场景 B：建一条 progress 第 1 周 → current_week=1, target=3 → lag=2
    service.create_progress({"course_id": c.id, "lesson_date": "2026-03-03", "topic": "极限定义"})
    r2 = service.course_next_topic(c.id, date(2026, 3, 17))  # 第 3 周
    expect("有进度 current_week 1", r2["current_week"] == 1)
    expect("target_week 3", r2["target_week"] == 3)
    expect("recent_topic=极限定义", r2["recent_topic"] == "极限定义")
    expect("lag_weeks=2", r2["lag_weeks"] == 2)
    expect("hint 含 落后 2", "落后 2" in r2["hint"])
    expect("outline_target 含 导数", r2["outline_target"] is not None and "导数" in r2["outline_target"])

    # 场景 C：再上一课到第 2 周 → lag=1
    service.create_progress({"course_id": c.id, "lesson_date": "2026-03-10", "topic": "连续"})
    r3 = service.course_next_topic(c.id, date(2026, 3, 17))
    expect("再上一课 current_week=2", r3["current_week"] == 2)
    expect("lag_weeks=1", r3["lag_weeks"] == 1)

    # 场景 D：超前（last 是第 4 周，target 是第 2 周）→ lag=-2, hint 含 超前
    service.create_progress({"course_id": c.id, "lesson_date": "2026-03-24", "topic": "微分中值定理"})
    r4 = service.course_next_topic(c.id, date(2026, 3, 9))  # 仍查第 2 周
    expect("超前 current_week=4", r4["current_week"] == 4)
    expect("lag_weeks=-2", r4["lag_weeks"] == -2)
    expect("hint 含 超前", "超前" in r4["hint"])

    # 场景 E：target_date 当天是否有课
    r5 = service.course_next_topic(c.id, date(2026, 3, 17))  # 周二
    expect("target 周二 → lesson_date 非空", r5["lesson_date"] is not None)
    r6 = service.course_next_topic(c.id, date(2026, 3, 19))  # 周四
    expect("target 周四 → lesson_date 为空（这门课周二）", r6["lesson_date"] is None)


def t_next_topic_无大纲() -> None:
    section("4.5 next_topic：无大纲 cache 时不崩")
    fresh_data_dir()
    c = make_bound_course(name="物理", weekday=2, semester_start="2026-03-02")
    r = service.course_next_topic(c.id, date(2026, 3, 17))
    expect("无大纲 outline_target 为 None", r["outline_target"] is None)
    expect("无大纲 current_week=None", r["current_week"] is None)
    expect("无大纲 lag_weeks 仍可计算", isinstance(r["lag_weeks"], int))


def main() -> int:
    sections = [
        t_set_outline_path,
        t_get_outline,
        t_refresh_outline_格式,
        t_next_topic,
        t_next_topic_无大纲,
    ]
    fails = 0
    for fn in sections:
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            fails += 1
            print(f"  {_Color.FAIL}FAIL{_Color.END} {fn.__name__} 抛异常：{e}")
            import traceback
            traceback.print_exc()
    print(f"\n==== 阶段 ④ 测试汇总 ====")
    print(f"通过 {_passes}")
    if _failures or fails:
        print(f"失败 {len(_failures) + fails}")
        for s, m in _failures:
            print(f"  [{s}] {m}")
    else:
        print("全部通过")
    cleanup()
    return 0 if not _failures and not fails else 1


if __name__ == "__main__":
    sys.exit(main())