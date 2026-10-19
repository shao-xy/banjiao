"""阶段 ② 测试：单双周/区间周 + 节次 + 学校课程时间表 + fs.archive。

新增 action：
    school.get_schedule
    school.set_schedule
    course.apply_period
    fs.archive

约束：
    Course.week_pattern ∈ {all, odd, even, custom}
        custom 必填 weeks
        odd/even 不应再传 weeks
        weeks 元素 1..60
    Course.period_no ∈ 1..12
        若 start_time/end_time 都空 → course.apply_period 从 schedule 填充
        若已填 → 不覆盖
    school_schedule 落盘 data/_school_schedule.json
        损坏 → 回退默认（绝不抛）
        set 时校验三段 bucket + no/start/end 格式
    fs.archive：文件 / 目录；不存在路径报错；不删原内容；写入 _archive
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from datetime import date, time
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

_TEST_DIR = Path(tempfile.mkdtemp(prefix="banjiao_sched_test_"))
os.environ["BANJIAO_DATA_DIR"] = str(_TEST_DIR / "data")
os.environ.setdefault("BANJIAO_ENABLE_VECTOR", "0")

for mod in list(sys.modules):
    if mod.startswith("banjiao."):
        del sys.modules[mod]
sys.modules.pop("banjiao", None)

from banjiao import config, db as _db_mod, service, models  # noqa: E402
from banjiao.config import settings  # noqa: E402
from banjiao.llm_errors import SkillError  # noqa: E402
from banjiao.models import CourseCreate  # noqa: E402


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


# ===== 测试组 =====

def t_week_pattern_校验() -> None:
    section("2.1 Course 周次模式校验")
    fresh_data_dir()
    # all 不带 weeks → OK
    c1 = CourseCreate(name="A", weekday=1)
    expect("all + 无 weeks 通过", c1.week_pattern == "all")

    # odd 不带 weeks → OK（不允许带）
    odd = CourseCreate.model_validate({"name": "A", "weekday": 1, "week_pattern": "odd"})
    expect("odd 通过", odd.week_pattern == "odd")

    # odd 错带 weeks → 拒
    expect_raises(
        "odd 不应带 weeks",
        lambda: CourseCreate.model_validate({"name": "A", "weekday": 1, "week_pattern": "odd", "weeks": [1, 2]}),
        Exception,
        contains="odd",
    )

    # custom 不带 weeks → 拒
    expect_raises(
        "custom 必须带 weeks",
        lambda: CourseCreate.model_validate({"name": "A", "weekday": 1, "week_pattern": "custom"}),
        Exception,
        contains="custom",
    )

    # custom 带 weeks → OK
    cu = CourseCreate.model_validate({"name": "A", "weekday": 1, "week_pattern": "custom", "weeks": [1, 3, 5]})
    expect("custom + weeks 通过", cu.week_pattern == "custom" and cu.weeks == [1, 3, 5])

    # weeks 元素越界（61）→ 拒
    expect_raises(
        "weeks 元素 > 60 拒",
        lambda: CourseCreate.model_validate({"name": "A", "weekday": 1, "week_pattern": "custom", "weeks": [61]}),
        Exception,
        contains="1..60",
    )

    # period_no 范围
    p1 = CourseCreate(name="A", weekday=1, period_no=1)
    expect("period_no=1 通过", p1.period_no == 1)
    expect_raises(
        "period_no=13 拒",
        lambda: CourseCreate.model_validate({"name": "A", "weekday": 1, "period_no": 13}),
        Exception,
        contains="period_no",
    )


def t_week_occurrences_单双周() -> None:
    section("2.2 _week_occurrences：单双周 / 自定义")
    fresh_data_dir()
    # 学期：第 1 周 = 2026-03-02（周一）~ 2026-03-08（周日）
    # 用每个周二开课：odd → 第 1,3,5... 周的周二
    sem_start = date(2026, 3, 2)
    c_odd = models.Course(
        name="单周", weekday=1,  # 周二
        week_pattern="odd",
        semester_start=sem_start, semester_end=date(2026, 5, 31),
    )
    occs = service._week_occurrences(c_odd, date(2026, 3, 2), date(2026, 3, 29))  # 4 周
    expect("odd 4 周内命中 2 次", len(occs) == 2)
    expect("odd 第 1 周命中", occs[0][1] == 1)
    expect("odd 第 3 周命中", occs[1][1] == 3)

    c_even = models.Course(
        name="双周", weekday=1,
        week_pattern="even",
        semester_start=sem_start, semester_end=date(2026, 5, 31),
    )
    occs2 = service._week_occurrences(c_even, date(2026, 3, 2), date(2026, 3, 29))
    expect("even 4 周内命中 2 次", len(occs2) == 2)
    expect("even 第 2 周命中", occs2[0][1] == 2)
    expect("even 第 4 周命中", occs2[1][1] == 4)

    c_custom = models.Course(
        name="自定义", weekday=1,
        week_pattern="custom", weeks=[2, 4],
        semester_start=sem_start, semester_end=date(2026, 5, 31),
    )
    occs3 = service._week_occurrences(c_custom, date(2026, 3, 2), date(2026, 3, 29))
    expect("custom 4 周内命中 2 次（2,4 周）", len(occs3) == 2)
    expect("custom 第 2 周命中", occs3[0][1] == 2)
    expect("custom 第 4 周命中", occs3[1][1] == 4)


def t_school_schedule_默认与覆盖() -> None:
    section("2.3 school.get_schedule：默认 / 覆盖 / 损坏回退")
    fresh_data_dir()
    # 默认
    sch = service.school_get_schedule()
    expect("默认有 morning 5 节", len(sch["morning"]) == 5)
    expect("默认 evening 第一节 19:00", sch["evening"][0]["start"] == "19:00")

    # 自定义并落盘
    custom = {
        "morning": [{"no": 1, "start": "07:50", "end": "08:30"}],
        "afternoon": [],
        "evening": [],
    }
    r = service.school_set_schedule(custom)
    expect("set_schedule updated", r.get("updated") is True)
    sch2 = service.school_get_schedule()
    expect("重读读到自定义", sch2["morning"][0]["start"] == "07:50")

    # 损坏文件 → 回退默认
    (settings.data_dir / "_school_schedule.json").write_text("{not json}", encoding="utf-8")
    sch3 = service.school_get_schedule()
    expect("损坏回退到默认", len(sch3["morning"]) == 5)


def t_school_schedule_set_校验() -> None:
    section("2.4 school.set_schedule：入参校验")
    fresh_data_dir()
    expect_raises(
        "缺 morning 拒",
        lambda: service.school_set_schedule({"afternoon": [], "evening": []}),
        SkillError,
        contains="morning",
    )
    expect_raises(
        "morning 项缺 no 拒",
        lambda: service.school_set_schedule({"morning": [{"start": "08:00", "end": "08:45"}], "afternoon": [], "evening": []}),
        SkillError,
        contains="no",
    )
    expect_raises(
        "no=13 拒",
        lambda: service.school_set_schedule({
            "morning": [{"no": 13, "start": "08:00", "end": "08:45"}],
            "afternoon": [], "evening": []
        }),
        SkillError,
        contains="1..12",
    )
    expect_raises(
        "时间格式错 拒",
        lambda: service.school_set_schedule({
            "morning": [{"no": 1, "start": "8:00", "end": "08:45"}],
            "afternoon": [], "evening": []
        }),
        SkillError,
        contains="HH:MM",
    )


def t_course_apply_period() -> None:
    section("2.5 course.apply_period：节次 → 时间")
    fresh_data_dir()
    c = service.create_course({
        "name": "高数", "weekday": 2,
        "semester_start": "2026-09-01", "semester_end": "2027-01-15",
        "period_no": 1, "week_pattern": "all",
    })
    expect("未填时间前 start_time 为空", c.start_time is None)
    c2 = service.course_apply_period(c.id)
    expect("应用节次 1 后 start_time=08:00", c2.start_time == time(8, 0))
    expect("应用节次 1 后 end_time=08:45", c2.end_time == time(8, 45))

    # 已填了 start_time → 不覆盖
    c3 = service.create_course({
        "name": "物理", "weekday": 3,
        "semester_start": "2026-09-01", "semester_end": "2027-01-15",
        "start_time": "10:00", "end_time": "11:00", "period_no": 1,
    })
    c3b = service.course_apply_period(c3.id)
    expect("已填 start_time 不被覆盖", c3b.start_time == time(10, 0))

    # 越界节次
    c4 = service.create_course({
        "name": "化学", "weekday": 4,
        "semester_start": "2026-09-01", "semester_end": "2027-01-15",
        "period_no": 11,
    })
    # 11 在默认 schedule 的 evening 里 → 应填
    c4b = service.course_apply_period(c4.id)
    expect("period_no=11 存在 → 应用", c4b.start_time is not None)
    expect("period_no=11 → 19:00", c4b.start_time == time(19, 0))

    # 教师自定义时间表抹掉 evening，再 apply 应报错
    # 先把 c4 的 start_time/end_time 抹掉，强制重新从 schedule 取
    service.update_course(c4.id, {"start_time": None, "end_time": None})
    service.school_set_schedule({
        "morning": [{"no": 1, "start": "08:00", "end": "08:45"}],
        "afternoon": [], "evening": [],
    })
    expect_raises(
        "schedule 没 11 节 → apply 报错",
        lambda: service.course_apply_period(c4.id),
        SkillError,
        contains="不在学校时间表",
    )


def t_fs_archive() -> None:
    section("2.6 fs.archive：备份文件 / 目录 / 路径不存在")
    fresh_data_dir()
    service.fs_init()
    (settings.data_dir / "教学" / "数学分析" / "幻灯片").mkdir(parents=True)
    p = settings.data_dir / "教学" / "数学分析" / "幻灯片" / "ch1.pptx"
    p.write_bytes(b"original content")
    r = service.fs_archive("教学/数学分析/幻灯片/ch1.pptx")
    expect("archive 文件 success", r.get("archived") is True)
    expect("archive src 等于原路径", r["src"] == "教学/数学分析/幻灯片/ch1.pptx")
    expect("archive dst 以 _archive 开头", r["dst"].startswith("_archive/"))
    expect("原文件仍在", p.exists())

    # 目录
    r2 = service.fs_archive("教学/数学分析")
    expect("archive 目录 success", r2.get("archived") is True)
    expect("archive 目录 files 计数 = 1", r2.get("files") == 1)

    # 路径不存在
    expect_raises(
        "archive 不存在路径报错",
        lambda: service.fs_archive("教学/不存在"),
        SkillError,
        contains="不存在",
    )


def main() -> int:
    sections = [
        t_week_pattern_校验,
        t_week_occurrences_单双周,
        t_school_schedule_默认与覆盖,
        t_school_schedule_set_校验,
        t_course_apply_period,
        t_fs_archive,
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
    print(f"\n==== 测试汇总 ====")
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