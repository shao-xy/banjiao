"""v0.3.0 测试：v0.2.1 试用问题清单的修复验证。

覆盖：
    A1  PowerShell 吃引号的裸 JSON 自动修补
    A2  --json-file 兼容 UTF-8 BOM
    A3  --out-file 写 UTF-8 无 BOM 文件（stdout 干净）
    B1  Course.sections 一门课多时段
    B2  period_nos 连排节次 + apply_period 连排取最早-最晚 + force 重算
    B3  weeks_range 周次区间 + 单双周组合
    C3  refresh_outline 严格结构校验 + suggest_outline 建议映射
    A4/D2  fs.import_file 公开导入 action
    D3  fs.list_files 列出顶层归档目录
    F4  course.week weeks=N 查未来多周
    F3  progress.create 日期告警（warning 而非报错）
    B4  school.get_schedule 带 source 字段
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
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

_TEST_DIR = Path(tempfile.mkdtemp(prefix="banjiao_v030_test_"))
os.environ["BANJIAO_DATA_DIR"] = str(_TEST_DIR / "data")
os.environ.setdefault("BANJIAO_ENABLE_VECTOR", "0")

for mod in list(sys.modules):
    if mod.startswith("banjiao."):
        del sys.modules[mod]
sys.modules.pop("banjiao", None)

from banjiao import db as _db_mod, service, models  # noqa: E402
from banjiao.config import settings  # noqa: E402
from banjiao.llm_errors import SkillError  # noqa: E402

SKILL_PY = ROOT / "scripts" / "skill.py"


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


def run_skill(*args: str, env_extra: dict | None = None) -> tuple[int, str, str]:
    """跑一次 skill.py 子进程，返回 (returncode, stdout, stderr)。"""
    env = dict(os.environ)
    if env_extra:
        env.update(env_extra)
    r = subprocess.run(
        [sys.executable, str(SKILL_PY), *args],
        capture_output=True, text=True, cwd=str(ROOT), env=env, timeout=120,
    )
    return r.returncode, r.stdout.strip(), r.stderr


def call_json(action: str, payload: dict | None = None) -> dict:
    args = [action]
    if payload is not None:
        args += ["--json", json.dumps(payload, ensure_ascii=False)]
    _, out, _ = run_skill(*args)
    return json.loads(out)


def make_course(name: str, **extra) -> models.Course:
    return service.create_course({"name": name, **extra})


# ===== A. Windows / PowerShell 适配 =====

def t_A1_裸JSON自动修补() -> None:
    section("A1 · PowerShell 吃掉引号的裸 JSON 自动修补")
    fresh_data_dir()
    # 模拟 PS 5.1：双引号被吃掉，Python 收到 {name:xxx,weekday:1}
    env = dict(os.environ)
    env["BANJIAO_DATA_DIR"] = str(_TEST_DIR / "data")
    r = subprocess.run(
        [sys.executable, str(SKILL_PY), "course.create", "--json",
         "{name:裸JSON课程,weekday:1,semester_start:2026-03-02}"],
        capture_output=True, text=True, cwd=str(ROOT), env=env, timeout=60,
    )
    out = json.loads(r.stdout.strip())
    expect("裸 JSON 被接受", out.get("ok") is True, str(out)[:200])
    expect("name 正确还原", out.get("data", {}).get("name") == "裸JSON课程")
    expect("weekday 正确还原", out.get("data", {}).get("weekday") == 1)

    # 正常 JSON（带引号）不能被破坏
    out2 = call_json("course.create", {"name": "正常JSON课程", "weekday": 2})
    expect("正常 JSON 不受影响", out2.get("ok") is True)
    expect("正常 name", out2["data"]["name"] == "正常JSON课程")


def t_A2_BOM兼容() -> None:
    section("A2 · --json-file 兼容 UTF-8 BOM")
    fresh_data_dir()
    p = _TEST_DIR / "bom.json"
    p.write_text(json.dumps({"name": "BOM课程", "weekday": 3}, ensure_ascii=False),
                 encoding="utf-8-sig")
    rc, out, _ = run_skill("course.create", "--json-file", str(p))
    payload = json.loads(out)
    expect("BOM 文件能读", payload.get("ok") is True, str(payload)[:200])
    expect("name 正确", payload.get("data", {}).get("name") == "BOM课程")

    # 普通 UTF-8（无 BOM）也要能读
    p2 = _TEST_DIR / "nobom.json"
    p2.write_text(json.dumps({"name": "无BOM课程", "weekday": 4}, ensure_ascii=False),
                  encoding="utf-8")
    rc, out2, _ = run_skill("course.create", "--json-file", str(p2))
    expect("无 BOM 文件也能读", json.loads(out2).get("ok") is True)


def t_A3_out_file() -> None:
    section("A3 · --out-file 写 UTF-8 无 BOM 文件")
    fresh_data_dir()
    call_json("course.create", {"name": "outfile测试", "weekday": 1})
    of = _TEST_DIR / "result.json"
    rc, stdout, _ = run_skill("course.list", "--out-file", str(of))
    expect("stdout 为空（结果只进文件）", stdout == "", f"stdout={stdout[:80]}")
    expect("文件已生成", of.exists())
    raw = of.read_bytes()
    expect("无 UTF-8 BOM", not raw.startswith(b"\xef\xbb\xbf"))
    parsed = json.loads(raw.decode("utf-8"))
    expect("文件内容可解析且 ok=True", parsed.get("ok") is True)

    # 错误结果也能写文件
    of2 = _TEST_DIR / "err.json"
    rc2, stdout2, _ = run_skill("course.get", "--json", '{"id":"nonexistent"}',
                                "--out-file", str(of2))
    expect("错误时 stdout 也为空", stdout2 == "")
    err_parsed = json.loads(of2.read_text(encoding="utf-8"))
    expect("错误 JSON 已落文件", err_parsed.get("ok") is False)
    expect("错误码正确", err_parsed["error"]["code"] == "not_found")


# ===== B. 建模缺陷 =====

def t_B1_多时段() -> None:
    section("B1 · Course.sections 一门课多时段")
    fresh_data_dir()
    c = make_course(
        "操作系统",
        semester_start="2026-03-02", semester_end="2026-06-30",
        sections=[
            {"weekday": 1, "week_pattern": "all", "period_nos": [3, 4, 5]},
            {"weekday": 2, "week_pattern": "odd", "weeks_range": [1, 7], "period_nos": [8, 9]},
            {"weekday": 1, "week_pattern": "odd", "weeks_range": [9, 15], "period_nos": [1, 2]},
        ],
    )
    expect("3 个时段已保存", len(c.sections) == 3)
    expect("section 1 连排 3-5", c.sections[0].period_nos == [3, 4, 5])
    expect("section 2 weeks_range 保存", c.sections[1].weeks_range == [1, 7])

    # 重新从 DB 读，验证 JSON 序列化往返
    c2 = service.get_course(c.id)
    expect("从 DB 读回 sections 完整", len(c2.sections) == 3)
    expect("读回 period_nos 正确", c2.sections[0].period_nos == [3, 4, 5])
    expect("读回 weeks_range 正确", c2.sections[1].weeks_range == [1, 7])
    expect("读回 week_pattern 正确", c2.sections[1].week_pattern == "odd")

    # 校验：weekday 越界
    expect_raises(
        "section weekday=7 拒",
        lambda: service.create_course({
            "name": "非法", "sections": [{"weekday": 7, "period_nos": [1]}]}),
        Exception, contains="weekday",
    )
    # 校验：period_nos 越界
    expect_raises(
        "section period_nos=[13] 拒",
        lambda: service.create_course({
            "name": "非法2", "sections": [{"weekday": 1, "period_nos": [13]}]}),
        Exception, contains="period_nos",
    )
    # 校验：weeks_range 反了
    expect_raises(
        "weeks_range=[7,1] 拒",
        lambda: service.create_course({
            "name": "非法3",
            "sections": [{"weekday": 1, "weeks_range": [7, 1]}]}),
        Exception, contains="weeks_range",
    )


def t_B1_课表按段展开() -> None:
    section("B1 · 课表查询按段展开成多行")
    fresh_data_dir()
    # 一门课：周二 3-5 节全期 + 周三 8-9 节单周
    c = make_course(
        "多时段课",
        semester_start="2026-03-02", semester_end="2026-06-30",
        sections=[
            {"weekday": 1, "week_pattern": "all", "period_nos": [3, 4, 5]},
            {"weekday": 2, "week_pattern": "odd", "period_nos": [8, 9]},
        ],
    )
    # 第 1 周（2026-03-02 周一 ~ 03-08 周日）
    lessons = service.week_lessons(date(2026, 3, 2))
    # 周二（03-03）命中第 1 段；周三（03-04）第 1 周是奇数 → 命中第 2 段
    tue = [x for x in lessons if x["lesson_date"] == "2026-03-03"]
    wed = [x for x in lessons if x["lesson_date"] == "2026-03-04"]
    expect("周二有课", len(tue) == 1)
    expect("周三有课（奇数周）", len(wed) == 1)
    expect("周二标记多时段", tue[0].get("is_multi_section") is True)
    expect("周二带 period_nos", tue[0].get("period_nos") == [3, 4, 5])
    expect("周三带 period_nos", wed[0].get("period_nos") == [8, 9])

    # 第 2 周的周三（偶数周）应无课
    lessons2 = service.week_lessons(date(2026, 3, 9))
    wed2 = [x for x in lessons2 if x["lesson_date"] == "2026-03-11"]
    expect("第 2 周周三无课（偶数周被过滤）", len(wed2) == 0)


def t_B2_连排节次() -> None:
    section("B2 · period_nos 连排 + apply_period 取最早-最晚")
    fresh_data_dir()
    service.fs_init()
    # 第 3-5 节：09:50 ~ 12:15
    c = make_course(
        "连排课", weekday=1, period_nos=[3, 4, 5],
        semester_start="2026-03-02", semester_end="2026-06-30",
    )
    c2 = service.course_apply_period(c.id)
    expect("连排 start = 第3节开始 09:50", c2.start_time == time(9, 50))
    expect("连排 end = 第5节结束 12:15", c2.end_time == time(12, 15))

    # 学校课表调整后 force 重算
    service.school_set_schedule({
        "morning": [
            {"no": 1, "start": "08:00", "end": "08:45"},
            {"no": 2, "start": "08:50", "end": "09:35"},
            {"no": 3, "start": "10:00", "end": "10:45"},
            {"no": 4, "start": "10:50", "end": "11:35"},
            {"no": 5, "start": "11:40", "end": "12:25"},
        ],
        "afternoon": [], "evening": [],
    })
    c3 = service.course_apply_period(c.id, force=True)
    expect("force 后 start 更新为 10:00", c3.start_time == time(10, 0))
    expect("force 后 end 更新为 12:25", c3.end_time == time(12, 25))

    # 缺节次报错
    expect_raises(
        "period_nos 含不存在的 12 节报错",
        lambda: service.course_apply_period(
            make_course("缺节次", weekday=3, period_nos=[12]).id),
        SkillError, contains="不在学校时间表",
    )


def t_B3_weeks_range() -> None:
    section("B3 · weeks_range 周次区间 + 单双周组合")
    fresh_data_dir()
    sem_start = date(2026, 3, 2)  # 第 1 周周一

    # [1,7] + odd → 1,3,5,7
    c = make_course("区间单周", weekday=1, weeks_range=[1, 7], week_pattern="odd",
                    semester_start="2026-03-02", semester_end="2026-06-30")
    occs = service._week_occurrences(c, sem_start, date(2026, 3, 29))  # 4 周
    expect("第 1 周命中", occs[0][1] == 1)
    expect("第 3 周命中", len([o for o in occs if o[1] == 3]) == 1)
    expect("第 2 周不命中", len([o for o in occs if o[1] == 2]) == 0)
    expect("4 周内命中 2 次", len(occs) == 2)

    # [1,15] + all → 1..15
    c2 = make_course("区间全周", weekday=1, weeks_range=[1, 15], week_pattern="all",
                     semester_start="2026-03-02", semester_end="2026-06-30")
    occs2 = service._week_occurrences(c2, sem_start, date(2026, 3, 29))
    expect("全周区间 4 周内命中 4 次", len(occs2) == 4)

    # weeks 与 weeks_range 互斥
    expect_raises(
        "weeks + weeks_range 同时给拒",
        lambda: service.create_course({
            "name": "互斥", "weeks": [1, 3], "weeks_range": [1, 7],
            "week_pattern": "custom"}),
        Exception, contains="weeks_range",
    )


# ===== C. 大纲 =====

def t_C3_结构校验() -> None:
    section("C3 · refresh_outline 严格结构校验")
    fresh_data_dir()
    c = make_course("大纲校验")

    cases = [
        ({}, "缺 weeks"),
        ({"weeks": []}, "空列表"),
        ({"weeks": [{"week": 1, "topics": []}]}, "topics 空"),
        ({"weeks": [{"week": 1, "topics": ["  "]}]}, "topics 空白串"),
        ({"weeks": [{"week": 0, "topics": ["x"]}]}, "week=0"),
        ({"weeks": [{"week": 61, "topics": ["x"]}]}, "week=61"),
        ({"weeks": [{"week": 1, "topics": ["a"]}, {"week": 1, "topics": ["b"]}]}, "week 重复"),
        ({"weeks": "notalist"}, "weeks 非列表"),
    ]
    for parsed, why in cases:
        expect_raises(
            f"{why} 被拒",
            lambda pp=parsed: service.course_refresh_outline(c.id, pp),
            SkillError,
        )
    # 合法通过
    r = service.course_refresh_outline(c.id, {"weeks": [{"week": 1, "topics": ["极限"]}]})
    expect("合法 parsed 通过", r.outline_cache is not None)


def t_C3_suggest_outline() -> None:
    section("C3 · suggest_outline 建议周次映射")
    fresh_data_dir()
    c = make_course(
        "大纲建议", semester_start="2026-03-02", semester_end="2026-04-12")  # 6 周
    r = service.course_suggest_outline(
        c.id, ["第1章 A", "第2章 B", "第3章 C", "第4章 D", "第5章 E", "第6章 F"])
    expect("weeks_total = 6（按学期长度算）", r["weeks_total"] == 6)
    expect("chapters_per_week = 1", r["chapters_per_week"] == 1)
    expect("weeks_used = 6", r["weeks_used"] == 6)
    expect("dry_run 标记", r["dry_run"] is True)
    expect("不落库（outline_cache 仍空）", c.outline_cache is None)
    # 确认后落库
    service.course_refresh_outline(c.id, {"weeks": r["suggestion"]})
    c2 = service.get_course(c.id)
    expect("确认后 cache 写入", c2.outline_cache is not None)
    expect("cache 周次数量正确", len(c2.outline_cache["weeks"]) == 6)

    # 章节多于周数 → 部分周多章
    r2 = service.course_suggest_outline(c.id, ["c1", "c2", "c3"], weeks_total=2)
    expect("3 章 / 2 周 → per_week=2", r2["chapters_per_week"] == 2)


# ===== D. 文件系统 =====

def t_D2_import_file() -> None:
    section("A4/D2 · fs.import_file 公开导入 action")
    fresh_data_dir()
    service.fs_init()
    # 造一个"外部文件"（模拟下载目录里的文件）
    src = _TEST_DIR / "苏州工学院高教研究课题管理办法-苏工[2025]15号.pdf"
    src.write_bytes(b"%PDF-1.4 fake content for import test" * 10)

    r = service.fs_import_file(str(src), top_kind="文件", dirname="资料")
    expect("导入成功", r["imported"] is True)
    expect("path 正确", r["path"].startswith("文件/资料/"))
    expect("方括号文件名保留", "[2025]" in r["filename"])
    dest = settings.data_dir / "文件" / "资料" / src.name
    expect("目标文件存在", dest.exists())
    expect("内容一致", dest.read_bytes() == src.read_bytes())

    # 带 role 索引
    src2 = _TEST_DIR / "notes.md"
    src2.write_text("# notes", encoding="utf-8")
    r2 = service.fs_import_file(str(src2), top_kind="文件", dirname="资料",
                               filename="imported.md", role="material")
    expect("role 标准化为枚举", r2["role"] == "material")

    # 同名已存在且非空 → 409（除非 archive）
    expect_raises(
        "同名非空冲突报错",
        lambda: service.fs_import_file(str(src2), top_kind="文件", dirname="资料",
                                       filename="imported.md"),
        SkillError, contains="已存在",
    )
    # archive=true → 先备份再覆盖
    r3 = service.fs_import_file(str(src2), top_kind="文件", dirname="资料",
                                filename="imported.md", archive=True)
    expect("archive 模式成功", r3["imported"] is True)
    expect("旧文件已备份", r3.get("archived_old") is not None)

    # 源文件不存在
    expect_raises(
        "源文件不存在报错",
        lambda: service.fs_import_file(str(_TEST_DIR / "nonexistent.pdf")),
        SkillError, contains="不存在",
    )
    # 扩展名不在白名单
    bad = _TEST_DIR / "malware.exe"
    bad.write_bytes(b"MZ")
    expect_raises(
        "白名单外扩展名拒",
        lambda: service.fs_import_file(str(bad), top_kind="文件", dirname="资料"),
        SkillError, contains="白名单",
    )
    # dirname 含路径分隔符 → 拒
    expect_raises(
        "dirname 含 / 拒",
        lambda: service.fs_import_file(str(src2), top_kind="文件", dirname="a/b"),
        SkillError, contains="dirname",
    )


def t_D3_list_files() -> None:
    section("D3 · fs.list_files 列出顶层归档目录（含非课程目录）")
    fresh_data_dir()
    service.fs_init()
    # 文件/资料/ 下放几个文件，含子目录
    d = settings.data_dir / "文件" / "资料"
    d.mkdir(parents=True, exist_ok=True)
    (d / "a.pdf").write_bytes(b"x" * 100)
    (d / "b.docx").write_bytes(b"y" * 200)
    sub = d / "2025"
    sub.mkdir(exist_ok=True)
    (sub / "c.txt").write_text("c", encoding="utf-8")
    # 文件/规章制度/ 下放文件（无角色子目录）
    d2 = settings.data_dir / "文件" / "规章制度"
    d2.mkdir(parents=True, exist_ok=True)
    (d2 / "d.pdf").write_bytes(b"z" * 50)

    # D1 修复验证：非角色目录也能列出（D1 之前 scan_top_dir 返回 []）
    listing = service.fs_list_files("文件", "资料")
    expect("资料下 3 个文件（含子目录）", len(listing) == 3)
    names = {f["filename"] for f in listing}
    expect("a.pdf 在列表", "a.pdf" in names)
    expect("子目录里的 c.txt 也在", "c.txt" in names)

    # 规章制度（非角色目录）
    listing2 = service.fs_list_files("文件", "规章制度")
    expect("规章制度下 1 个文件", len(listing2) == 1)
    expect("role 为 None（非角色目录）", listing2[0]["role"] is None)
    expect("有 sha256", len(listing2[0]["sha256"]) == 64)

    # 指定 subdir
    listing3 = service.fs_list_files("文件", "资料", subdir="2025")
    expect("subdir 过滤生效", len(listing3) == 1 and listing3[0]["filename"] == "c.txt")

    # 不存在的目录
    expect_raises(
        "目录不存在报错",
        lambda: service.fs_list_files("文件", "不存在"),
        SkillError, contains="不存在",
    )


# ===== F. 小项 =====

def t_F4_week_lessons_多周() -> None:
    section("F4 · course.week 支持查未来多周")
    fresh_data_dir()
    make_course("每周课", weekday=1, week_pattern="all",
                semester_start="2026-03-02", semester_end="2026-06-30")
    # 第 1 周：1 次（周二）
    w1 = service.week_lessons(date(2026, 3, 2), weeks=1)
    expect("1 周 1 次课", len(w1) == 1)
    # 2 周：2 次
    w2 = service.week_lessons(date(2026, 3, 2), weeks=2)
    expect("2 周 2 次课", len(w2) == 2)
    # 4 周：4 次
    w4 = service.week_lessons(date(2026, 3, 2), weeks=4)
    expect("4 周 4 次课", len(w4) == 4)
    # 越界
    expect_raises(
        "weeks=0 拒",
        lambda: service.week_lessons(date(2026, 3, 2), weeks=0),
        SkillError, contains="weeks",
    )
    expect_raises(
        "weeks=21 拒",
        lambda: service.week_lessons(date(2026, 3, 2), weeks=21),
        SkillError, contains="weeks",
    )
    # CLI 层也支持
    r = call_json("course.week", {"start": "2026-03-02", "weeks": 2})
    expect("CLI weeks=2 生效", r.get("ok") is True and len(r["data"]) == 2)


def t_F3_progress_warning() -> None:
    section("F3 · progress.create 日期告警（warning 不报错）")
    fresh_data_dir()
    c = make_course("周二课", weekday=1, semester_start="2026-03-02",
                    semester_end="2026-06-30")
    # 周一（不是上课日）→ 应有 warning
    r = service.progress_create_with_warning({
        "course_id": c.id, "lesson_date": "2026-03-02", "topic": "补记"})
    expect("仍创建成功", r.get("id") is not None)
    expect("带 warning 字段", "warning" in r)
    expect("warning 提到不是上课日", "不是该课程的上课日" in r["warning"])

    # 周二（是上课日）→ 无 warning
    r2 = service.progress_create_with_warning({
        "course_id": c.id, "lesson_date": "2026-03-03", "topic": "正常"})
    expect("上课日无 warning", "warning" not in r2)

    # CLI 层也带 warning
    r3 = call_json("progress.create", {
        "course_id": c.id, "lesson_date": "2026-03-02", "topic": "CLI告警"})
    expect("CLI 返回 warning", "warning" in r3.get("data", {}))


def t_B4_schedule_source() -> None:
    section("B4 · school.get_schedule 带 source 字段")
    fresh_data_dir()
    # 无覆盖文件 → builtin_default
    sch = service.school_get_schedule()
    expect("source=builtin_default", sch["source"] == "builtin_default")
    expect("morning 5 节", len(sch["morning"]) == 5)
    expect("evening 1 节", len(sch["evening"]) == 1)

    # 写入覆盖 → source=file
    service.school_set_schedule({
        "morning": [{"no": 1, "start": "07:50", "end": "08:30"}],
        "afternoon": [], "evening": [],
    })
    sch2 = service.school_get_schedule()
    expect("source=file", sch2["source"] == "file")
    expect("读到自定义时间", sch2["morning"][0]["start"] == "07:50")

    # CLI 层也带 source
    r = call_json("school.get_schedule")
    expect("CLI 带 source", r["data"].get("source") == "file")


def main() -> int:
    sections = [
        t_A1_裸JSON自动修补,
        t_A2_BOM兼容,
        t_A3_out_file,
        t_B1_多时段,
        t_B1_课表按段展开,
        t_B2_连排节次,
        t_B3_weeks_range,
        t_C3_结构校验,
        t_C3_suggest_outline,
        t_D2_import_file,
        t_D3_list_files,
        t_F4_week_lessons_多周,
        t_F3_progress_warning,
        t_B4_schedule_source,
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
    print("\n==== v0.3.0 修复验证汇总 ====")
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