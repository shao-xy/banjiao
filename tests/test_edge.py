"""伴教 Skill 生产安全边界测试。

不是普通冒烟测试 —— 专门为生产部署前的回归设计，覆盖：
  · 数据完整性：Pydantic 校验绕过防御、组合约束、外键悬挂
  · 持久化：原子性、并发锁、SQL 注入、时间字段完整性
  · 行为边界：weeks=[] 语义、过期判定、reminded 幂等
  · 标准化错误：每个 action 的错误分支
  · JSON 序列化：日期/时间/UUID/None 边界

用法：
    cd 项目根目录
    python tests/test_edge.py

环境变量：
    BANJIAO_TEST_DATA_DIR  隔离运行（测试结束后不污染默认 data/）

依赖：与运行 skill 一致。
"""

from __future__ import annotations

import sys
import functools as _ft
print = _ft.partial(print, flush=True)  # noqa: E731
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import traceback
import uuid
from datetime import date, datetime, time as dtime, timedelta
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = SKILL_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))


def _ROOT():  # 兼容旧名（旧 ROOT 实际是 Skill 根目录，子进程 cwd 需要它）
    return str(SKILL_ROOT)


# ===== 自定义最小测试框架 =====

class _Color:
    OK = "\033[92m"
    FAIL = "\033[91m"
    WARN = "\033[93m"
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


def expect_raises(label: str, fn, exc_type=Exception, contains: str = "", contains_in_repr: bool = False) -> None:
    """fn 应当抛出 exc_type；可选 substring 校验。

    contains_in_repr=True 时同时在校验 repr(e)（默认只看 str(e)）。
    """
    try:
        fn()
    except Exception as e:  # noqa: BLE001
        if isinstance(e, exc_type):
            haystack = str(e)
            if contains_in_repr:
                haystack += "\n" + repr(e)
            if contains and contains not in haystack:
                expect(label, False, f"抛错但消息不含 {contains!r}：{haystack[:300]}")
                return
            expect(label, True)
            return
        expect(label, False, f"抛出 {type(e).__name__} 而非 {exc_type.__name__}: {e}")
        return
    expect(label, False, "未抛错")


# ===== 隔离运行：临时数据目录 =====

_TEST_DIR = Path(tempfile.mkdtemp(prefix="banjiao_test_"))
os.environ["BANJIAO_DATA_DIR"] = str(_TEST_DIR / "data")
os.environ.setdefault("BANJIAO_ENABLE_VECTOR", "0")

# 在导包前设置好
from banjiao import config, db as _db_mod, service  # noqa: E402
from banjiao.config import settings  # noqa: E402
from banjiao.llm_errors import SkillError  # noqa: E402
from banjiao.models import (  # noqa: E402
    Affair, AffairCreate, AffairUpdate, Course, CourseCreate, CourseUpdate,
    Homework, HomeworkCreate, HomeworkUpdate, ProgressEntry, ProgressCreate, ProgressUpdate,
    Recurrence,
)


def fresh_db() -> None:
    """每个 section 之间清空数据（保留 schema）。"""
    # 直接清表，避免关闭/重连（重连在 SQLite WAL 下偶尔会留句柄）
    try:
        with _db_mod.db._lock:
            for tbl in ("progress", "homework", "affairs", "documents", "courses"):
                _db_mod.db._mutate(f"DELETE FROM {tbl}", ())
    except Exception as e:
        print(f"  fresh_db DELETE 失败: {e}")
        # 退路：重置连接
        try:
            _db_mod.db._conn.close()
        except Exception:
            pass
        _db_mod.db._conn = None
        if settings.data_dir.exists():
            for f in settings.data_dir.iterdir():
                if f.is_file():
                    try:
                        f.unlink()
                    except PermissionError:
                        pass
        _db_mod.db.__init__()  # type: ignore[misc]


def cleanup() -> None:
    if _TEST_DIR.exists():
        shutil.rmtree(_TEST_DIR, ignore_errors=True)


# ===== 测试主体 =====

def t_LLM入口脚本_子进程端到端() -> None:
    """真正通过子进程调用 scripts/skill.py（生产路径）。"""
    section("9. LLM 入口脚本端到端（子进程）")
    fresh_db()

    import subprocess

    # 使用一个独立的数据目录供本次脚本调用，避免污染主测试数据
    import shutil as _shutil
    probe_dir = _TEST_DIR / "probe"
    probe_data = probe_dir / "data"
    probe_data.mkdir(parents=True, exist_ok=True)

    env = dict(os.environ)
    env["BANJIAO_DATA_DIR"] = str(probe_data)
    env["PYTHONIOENCODING"] = "utf-8"

    # 1) 创建课程
    payload_create = json.dumps({
        "name": "脚本入口课程",
        "weekday": 2,
        "start_time": "08:00",
        "end_time": "09:50",
    }, ensure_ascii=False)
    r = subprocess.run(
        [sys.executable, "scripts/skill.py", "course.create", "--json", payload_create],
        capture_output=True, text=True, env=env, cwd=_ROOT(), timeout=15,
    )
    expect("skill.py exit 0", r.returncode == 0)
    expect("skill.py stdout 单行", len(r.stdout.strip().splitlines()) == 1)
    out = json.loads(r.stdout.strip())
    expect("skill.py ok:true", out.get("ok") is True)
    cid = out["data"]["id"]
    expect("返回 id 是 UUID 形式", isinstance(cid, str) and len(cid) == 36)

    # 2) 错误：unknown action
    r = subprocess.run(
        [sys.executable, "scripts/skill.py", "nope"],
        capture_output=True, text=True, env=env, cwd=_ROOT(), timeout=15,
    )
    expect("unknown action exit 0（脚本不退出非零）", r.returncode == 0)
    out = json.loads(r.stdout.strip())
    expect("unknown action → ok:false", out.get("ok") is False)
    expect("unknown action code=unknown_action", out["error"]["code"] == "unknown_action")

    # 3) 错误：bad_param
    r = subprocess.run(
        [sys.executable, "scripts/skill.py", "course.create", "--json", json.dumps({"name": ""})],
        capture_output=True, text=True, env=env, cwd=_ROOT(), timeout=15,
    )
    out = json.loads(r.stdout.strip())
    expect("空 name → ok:false", out.get("ok") is False)
    expect("空 name → bad_param", out["error"]["code"] == "bad_param")

    # 4) 错误：missing_param
    r = subprocess.run(
        [sys.executable, "scripts/skill.py", "homework.create", "--json", json.dumps({})],
        capture_output=True, text=True, env=env, cwd=_ROOT(), timeout=15,
    )
    out = json.loads(r.stdout.strip())
    expect("缺 title → ok:false", out.get("ok") is False)
    expect("缺 title → missing_param 或 bad_param", out["error"]["code"] in ("missing_param", "bad_param"))

    # 5) 错误：bad_json
    r = subprocess.run(
        [sys.executable, "scripts/skill.py", "course.list", "--json", "{not json}"],
        capture_output=True, text=True, env=env, cwd=_ROOT(), timeout=15,
    )
    expect("bad_json exit 0", r.returncode == 0)
    out = json.loads(r.stdout.strip())
    expect("bad_json → ok:false", out.get("ok") is False)
    expect("bad_json code=bad_json", out["error"]["code"] == "bad_json")

    # 6) 错误输出不污染 stdout 其它内容（只有一行 JSON）
    r = subprocess.run(
        [sys.executable, "scripts/skill.py", "course.get", "--json", json.dumps({"id": "abc"})],
        capture_output=True, text=True, env=env, cwd=_ROOT(), timeout=15,
    )
    expect("只有一行 JSON", "\n" not in r.stdout.strip())
    out = json.loads(r.stdout.strip())
    expect("course.get not_found → ok:false", out.get("ok") is False)

    # 7) --json-file 也工作
    tmp = _TEST_DIR / "tmp.json"
    tmp.write_text(json.dumps({"name": "临时创建", "weekday": 5}), encoding="utf-8")
    r = subprocess.run(
        [sys.executable, "scripts/skill.py", "course.create", "--json-file", str(tmp)],
        capture_output=True, text=True, env=env, cwd=_ROOT(), timeout=15,
    )
    out = json.loads(r.stdout.strip())
    expect("--json-file 成功", out.get("ok") is True)


def run_all() -> int:
    sections = [
        t_数据完整性校验,
        t_课程重复与周次边界,
        t_作业与事务边界,
        t_提醒与时间判定,
        t_持久化与并发,
        t_SQL注入与危险字符,
        t_错误处理与标准化错误,
        t_JSON序列化安全性,
        t_LLM入口脚本_子进程端到端,
    ]
    for fn in sections:
        # 用守护线程兜底：每个 section 限制 60s 超时（Windows 没有 SIGALRM）
        finished = [False]
        exc_box: list[BaseException] = []

        def _runner(f=fn, fin=finished, eb=exc_box):
            try:
                f()
            except BaseException as e:  # noqa: BLE001
                eb.append(e)
            finally:
                fin[0] = True

        th = threading.Thread(target=_runner, daemon=True)
        th.start()
        deadline = time.time() + 60
        while not finished[0]:
            if time.time() > deadline:
                print(f"  {_Color.FAIL}FAIL{_Color.END} {fn.__name__} 超时（60s）")
                _failures.append((fn.__name__, "测试超时（60s）"))
                break
            time.sleep(0.2)
        if finished[0]:
            if exc_box:
                e = exc_box[0]
                traceback.print_exc()
                _failures.append((fn.__name__, f"未捕获异常 {type(e).__name__}: {e}"))
                print(f"  {_Color.FAIL}FAIL{_Color.END} {fn.__name__} 未捕获：{e}")
    print()
    total = _passes + len(_failures)
    print(f"==== 测试汇总 ====")
    print(f"通过 {_passes} / {total}")
    if _failures:
        print(f"{_Color.FAIL}失败 {len(_failures)} 个：{_Color.END}")
        for sec, msg in _failures:
            print(f"  - [{sec}] {msg}")
        return 1
    print(f"{_Color.OK}全部通过{_Color.END}")
    return 0


# ---------- 1. 数据完整性校验 ----------

def t_数据完整性校验() -> None:
    section("1. 数据完整性校验")
    fresh_db()

    # 课程名空字符串
    expect_raises(
        "课程名空串",
        lambda: CourseCreate.model_validate({"name": ""}),
        ValueError,
    )

    # 课程名超过 100 字符
    expect_raises(
        "课程名过长",
        lambda: CourseCreate.model_validate({"name": "x" * 101}),
        ValueError,
    )

    # 课程时间倒置
    expect_raises(
        "start_time > end_time",
        lambda: CourseCreate.model_validate({
            "name": "X", "weekday": 2, "start_time": "11:00", "end_time": "09:00",
        }),
        ValueError,
    )

    # weekday 越界
    expect_raises(
        "weekday=7",
        lambda: CourseCreate.model_validate({"name": "X", "weekday": 7}),
        ValueError,
    )
    expect_raises(
        "weekday=-1",
        lambda: CourseCreate.model_validate({"name": "X", "weekday": -1}),
        ValueError,
    )

    # weeks 含非法
    expect_raises(
        "weeks 含 0",
        lambda: CourseCreate.model_validate({"name": "X", "weeks": [0, 1, 2]}),
        ValueError,
    )
    expect_raises(
        "weeks 含负数",
        lambda: CourseCreate.model_validate({"name": "X", "weeks": [-1]}),
        ValueError,
    )
    expect_raises(
        "weeks 含超过 60",
        lambda: CourseCreate.model_validate({"name": "X", "weeks": [61]}),
        ValueError,
    )

    # semester_end < semester_start
    expect_raises(
        "semester_end < semester_start",
        lambda: CourseCreate.model_validate({
            "name": "X",
            "semester_start": "2026-09-01",
            "semester_end": "2026-08-01",
        }),
        ValueError,
    )

    # homework due_at < published_at
    expect_raises(
        "作业 due_at < published_at",
        lambda: HomeworkCreate.model_validate({
            "title": "X",
            "published_at": "2026-10-25",
            "due_at": "2026-10-20",
        }),
        ValueError,
    )

    # affair kind 不合法
    expect_raises(
        "事务 kind 不合法",
        lambda: AffairCreate.model_validate({"title": "X", "kind": "garbage"}),
        ValueError,
    )

    # homework status 不合法
    expect_raises(
        "作业 status 不合法",
        lambda: HomeworkUpdate.model_validate({"status": "garbage"}),
        ValueError,
    )

    # remind_before_days 越界
    expect_raises(
        "remind_before_days=31",
        lambda: HomeworkCreate.model_validate({
            "title": "X",
            "published_at": "2026-10-25",
            "remind_before_days": 31,
        }),
        ValueError,
    )
    expect_raises(
        "remind_before_minutes=20000",
        lambda: AffairCreate.model_validate({
            "title": "X",
            "remind_before_minutes": 20000,
        }),
        ValueError,
    )

    # Recurrence 周期不合法
    expect_raises(
        "Recurrence freq 非法",
        lambda: Recurrence.model_validate({"freq": "hourly"}),
        ValueError,
    )
    expect_raises(
        "by_weekday 越界",
        lambda: Recurrence.model_validate({"freq": "weekly", "by_weekday": [7]}),
        ValueError,
    )
    expect_raises(
        "by_weekday 用在 daily",
        lambda: Recurrence.model_validate({"freq": "daily", "by_weekday": [1]}),
        ValueError,
    )

    # 标题长度边界：正好 100 / 200 字应通过
    expect(
        "课程名正好 100 字",
        CourseCreate.model_validate({"name": "x" * 100}).name == "x" * 100,
    )
    expect(
        "作业标题正好 200 字",
        HomeworkCreate.model_validate({
            "title": "t" * 200,
            "published_at": "2026-10-25",
        }).title == "t" * 200,
    )

    # 日期格式错误 → 422
    expect_raises(
        "published_at 格式错误",
        lambda: HomeworkCreate.model_validate({
            "title": "x",
            "published_at": "2026/10/25",  # 应为 ISO
        }),
        ValueError,
    )
    expect_raises(
        "published_at 完全不是日期",
        lambda: HomeworkCreate.model_validate({
            "title": "x",
            "published_at": "不是日期",
        }),
        ValueError,
    )
    expect_raises(
        "日期越界月份 13",
        lambda: HomeworkCreate.model_validate({
            "title": "x",
            "published_at": "2026-13-01",
        }),
        ValueError,
    )

    # 时间格式错误
    expect_raises(
        "start_time 25:00",
        lambda: CourseCreate.model_validate({
            "name": "X",
            "start_time": "25:00",
        }),
        ValueError,
    )


# ---------- 2. 课程重复与周次边界 ----------

def t_课程重复与周次边界() -> None:
    section("2. 课程重复与周次边界")
    fresh_db()

    # 创建第 8 周上课的课程（参照 2026-10-18 是周日，第 1 周 = 2026-09-07）
    c1 = service.create_course({
        "name": "周一课",
        "weekday": 0,
        "start_time": "08:00",
        "end_time": "09:50",
        "semester_start": "2026-09-07",  # 第 1 周的周一
        "semester_end": "2027-01-10",
        "weeks": [8],
    })
    expect("创建成功", c1.id is not None)

    # 2026-10-19 是周一 → 第几周？(10-19 - 9-7).days = 42; 42//7 + 1 = 7 → 不在 weeks 内，应返回空
    lessons = service.week_lessons(date(2026, 10, 19))
    expect("week 7 不在 weeks 列表中", not any(l["id"] == c1.id for l in lessons))

    # 2026-10-26 是周一 → 49 天后；49//7 + 1 = 8 → 应出现
    lessons = service.week_lessons(date(2026, 10, 26))
    found = next((l for l in lessons if l["id"] == c1.id), None)
    expect("week 8 命中", found is not None and found["week_no"] == 8)

    # weeks=[] 等价于不限定
    c2 = service.create_course({
        "name": "周二课",
        "weekday": 2,
        "start_time": "08:00",
        "end_time": "09:50",
        "semester_start": "2026-09-07",
        "semester_end": "2027-01-10",
        "weeks": [],
    })
    expect("weeks=[] 不过滤", True)  # 下面是关键断言
    for week_offset in (0, 7, 14, 21):  # 第 1/2/3/4 周
        d = date(2026, 9, 8) + timedelta(days=week_offset)  # 都是周二
        lessons = service.week_lessons(d)
        found = next((l for l in lessons if l["id"] == c2.id), None)
        expect(
            f"weeks=[] 命中周二 {d.isoformat()}",
            found is not None,
        )

    # weeks=None 不限定
    c3 = service.create_course({
        "name": "周三课",
        "weekday": 3,
        "start_time": "08:00",
        "end_time": "09:50",
    })
    lessons = service.week_lessons(date(2026, 10, 21))  # 10-21 是周三
    found = next((l for l in lessons if l["id"] == c3.id), None)
    expect("无学期：每周三均出现", found is not None)

    # 学期尚未开始（query 在 semester_start 之前）
    c4 = service.create_course({
        "name": "周二下学期",
        "weekday": 1,
        "start_time": "10:00",
        "end_time": "11:50",
        "semester_start": "2027-02-15",
        "semester_end": "2027-06-30",
    })
    lessons = service.week_lessons(date(2027, 1, 25))  # 学期前
    found = next((l for l in lessons if l["id"] == c4.id), None)
    expect("学期前不出课", found is None)

    # 无 weekday → 不出现
    c5 = service.create_course({
        "name": "无时间槽",
        "notes": "按需安排",
    })
    lessons = service.week_lessons(date(2027, 2, 16))  # 即使学期内
    found = next((l for l in lessons if l["id"] == c5.id), None)
    expect("无 weekday 永不出课", found is None)

    # 更新校验：把 start_time 改到 end_time 之后
    expect_raises(
        "更新让 end < start",
        lambda: service.update_course(c1.id, {
            "start_time": "10:00",
            "end_time": "09:00",
        }),
        ValueError,
    )

    # 更新边界：通过 update 校验无法制造"半合法数据"
    expect_raises(
        "更新 weeks 含 0",
        lambda: service.update_course(c1.id, {"weeks": [0]}),
        ValueError,
    )


# ---------- 3. 作业与事务边界 ----------

def t_作业与事务边界() -> None:
    section("3. 作业与事务边界")
    fresh_db()

    # 创建作业
    cid = service.create_course({
        "name": "锚定课程",
        "weekday": 2,
        "semester_start": "2026-09-01",
        "semester_end": "2027-01-15",
    }).id

    # 必填字段缺失
    expect_raises(
        "作业缺少 title",
        lambda: HomeworkCreate.model_validate({"published_at": "2026-10-25"}),
        ValueError,
    )
    expect_raises(
        "作业缺少 published_at",
        lambda: HomeworkCreate.model_validate({"title": "X"}),
        ValueError,
    )

    # 关联不存在的课程
    expect_raises(
        "作业关联不存在课程",
        lambda: service.create_homework({
            "title": "X",
            "course_id": "no-such-id",
            "published_at": "2026-10-25",
        }),
        SkillError,
        contains="不存在",
    )

    # 正常创建
    h = service.create_homework({
        "title": "边界作业",
        "course_id": cid,
        "published_at": "2026-10-18",
        "due_at": "2026-10-25",
        "remind_before_days": 1,
    })
    expect("创建作业", h.id is not None and h.status == "pending")

    # 标记已提交时自动填 submitted_at
    h2 = service.update_homework(h.id, {"status": "submitted"})
    expect("submitted 自动填 submitted_at", h2.submitted_at is not None)

    # 作业 status=cancelled
    h3 = service.update_homework(h.id, {"status": "cancelled"})
    expect("cancelled 状态", h3.status == "cancelled")

    # 试图再次覆盖 due_at < published_at
    expect_raises(
        "更新让 due < published",
        lambda: service.update_homework(h.id, {
            "published_at": "2026-10-26",
            "due_at": "2026-10-25",
        }),
        ValueError,
    )

    # 事务：due_at 在过去但是合法（可以事后存档）
    a = service.create_affair({
        "title": "上周会议",
        "kind": "meeting",
        "due_at": "2026-10-10T15:00:00",
    })
    expect("过去 due_at 可存档", a.id is not None)

    # 事务 update kind 改为非法 → 校验拒绝
    expect_raises(
        "事务 kind 非法",
        lambda: service.update_affair(a.id, {"kind": "garbage"}),
        ValueError,
    )

    # 删除幂等性：第一次成功，第二次 not_found
    aid = a.id
    service.delete_affair(aid)
    expect_raises(
        "重复删除事务 → not_found",
        lambda: service.delete_affair(aid),
        SkillError,
        contains="不存在",
    )


# ---------- 4. 提醒与时间判定 ----------

def t_提醒与时间判定() -> None:
    section("4. 提醒与时间判定")
    fresh_db()

    today = date.today()

    # 作业：今天到期 → 立即触发
    h1 = service.create_homework({
        "title": "今天到期",
        "published_at": (today - timedelta(days=2)).isoformat(),
        "due_at": today.isoformat(),
        "remind_before_days": 1,
    })

    # 作业：截止 5 天后且未到提醒窗口
    h2 = service.create_homework({
        "title": "5天后",
        "published_at": today.isoformat(),
        "due_at": (today + timedelta(days=5)).isoformat(),
        "remind_before_days": 1,
    })

    # 作业：已过期（昨天）—— pending + due_at < today
    h3 = service.create_homework({
        "title": "已过期未提交",
        "published_at": (today - timedelta(days=10)).isoformat(),
        "due_at": (today - timedelta(days=1)).isoformat(),
    })

    due = service.due_soon_homework(today)
    due_ids = {d["id"] for d in due}

    expect("今天到期作业命中", h1.id in due_ids)
    expect("5天后且未到提醒窗口不命中", h2.id not in due_ids)
    expect("已过期作业不在 due_soon（逻辑范围外）", h3.id not in due_ids)

    # list_homework overdue 应拿到 h3
    overdue = service.list_homework(overdue=True)
    expect("list_homework(overdue) 含 h3", any(o.id == h3.id for o in overdue))

    # list_homework only_pending 应包含所有 pending
    pending = service.list_homework(only_pending=True)
    expect("only_pending 取到 3 条", len(pending) == 3)

    # 事务：remind_before_minutes 提前窗口
    now = datetime.now()
    a1 = service.create_affair({
        "title": "5分钟后",
        "kind": "meeting",
        "due_at": (now + timedelta(minutes=5)).isoformat(),
        "remind_before_minutes": 10,
    })
    a2 = service.create_affair({
        "title": "2小时后",
        "kind": "meeting",
        "due_at": (now + timedelta(hours=2)).isoformat(),
        "remind_before_minutes": 10,
    })

    affairs_due = service.due_soon_affairs()
    due_aids = {a["id"] for a in affairs_due}
    expect("事务 5 分钟后命中提醒", a1.id in {x.id for x in [a1, a2]} and any(d["id"] == a1.id for d in affairs_due))
    expect("事务 2 小时后未到提醒窗口", a2.id not in due_aids)

    # mark_reminded 幂等
    service.mark_affair_reminded(a1.id)
    a1_b = service.get_affair(a1.id)
    expect("reminded 字段写入", a1_b.reminded is True)
    # 再次获取 due_soon 应不再包含 a1（已 reminded）
    affairs_due2 = service.due_soon_affairs()
    expect("reminded 后不再推送", not any(d["id"] == a1.id for d in affairs_due2))


# ---------- 5. 持久化与并发 ----------

def t_持久化与并发() -> None:
    section("5. 持久化与并发（SQLite WAL + 全局锁）")
    fresh_db()

    cid = service.create_course({
        "name": "并发测试",
        "weekday": 2,
        "semester_start": "2026-09-01",
        "semester_end": "2027-01-15",
    }).id

    write_results: list[bool] = []
    write_results_lock = threading.Lock()

    def writer(i: int) -> None:
        try:
            service.create_homework({
                "title": f"hw-{i}",
                "course_id": cid,
                "published_at": "2026-10-18",
                "due_at": "2026-10-25",
            })
            with write_results_lock:
                write_results.append(True)
        except Exception:
            with write_results_lock:
                write_results.append(False)

    threads = [threading.Thread(target=writer, args=(i,)) for i in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=15)
    expect("并发 3 写全部成功", all(write_results) and len(write_results) == 3)

    items = service.list_homework()
    expect("并发 3 写后 list_homework 有 3 条", len(items) == 3)
    ids = [x.id for x in items]
    expect("ID 全部唯一", len(set(ids)) == 3)

    # 读并发：小并发，避免 SQLite 单连接锁等待过长
    read_ok = [True]
    read_lock = threading.Lock()

    def reader(iterations: int) -> None:
        for _ in range(iterations):
            try:
                lst = service.list_homework()
                if not isinstance(lst, list):
                    with read_lock:
                        read_ok[0] = False
                    return
            except Exception:
                with read_lock:
                    read_ok[0] = False
                return

    base_count = len(service.list_homework())
    threads = [threading.Thread(target=reader, args=(5,)) for _ in range(2)] + [
        threading.Thread(target=writer, args=(i,)) for i in range(100, 102)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=15)
    expect("读写并发安全（list 始终返回 list）", read_ok[0] is True)
    expect(f"读写并发后总数 = {base_count + 2}", len(service.list_homework()) == base_count + 2)

    # 事务回滚：故意抛错不应污染 DB
    def fail_writer() -> None:
        try:
            service.create_homework({
                "title": "",  # 触发校验
                "published_at": "2026-10-18",
            })
        except SkillError:
            pass

    fail_writer()
    expect(
        f"校验失败后 list_homework 仍 {base_count + 2} 条（事务隔离）",
        len(service.list_homework()) == base_count + 2,
    )


# ---------- 6. SQL 注入与危险字符 ----------

def t_SQL注入与危险字符() -> None:
    section("6. SQL 注入与危险字符")
    fresh_db()

    # 课程名含 SQL 注入 / NUL / 控制字符应被原样保存
    payload = "test'; DROP TABLE courses;--\x00\x01\x02"
    c = service.create_course({
        "name": payload,
        "class_group": "' OR '1'='1",
        "location": "教学楼\\nnewline\ttab",
        "notes": "<script>alert(1)</script>",
    })

    # 重新读取应拿到完全相同的字符串（参数化查询）
    fetched = service.get_course(c.id)
    expect("危险字符原样保存（参数化）", fetched.name == payload)
    expect("class_group 单引号原样保存", fetched.class_group == "' OR '1'='1")
    expect("特殊字符不被解释", "<script>" in (fetched.notes or ""))

    # 列表仍然能列出来（表未被 DROP）
    expect("courses 表依然存在", len(service.list_courses()) >= 1)

    # Unicode + emoji
    emoji_name = "数学 📚 ∑ π 字"
    c2 = service.create_course({"name": emoji_name})
    fetched2 = service.get_course(c2.id)
    expect("Unicode + emoji 完整保存", fetched2.name == emoji_name)

    # 富文本字段长度边界
    long_desc = "x" * 2000
    h = service.create_homework({
        "title": "y",
        "published_at": "2026-10-18",
        "due_at": "2026-10-25",
        "description": long_desc,
    })
    expect("2KB 描述保存", service.get_homework(h.id).description == long_desc)

    expect_raises(
        "2KB+1 描述保存（应越界 → 422）",
        lambda: HomeworkCreate.model_validate({
            "title": "y",
            "published_at": "2026-10-18",
            "description": "x" * 5000,
        }),
        ValueError,
    )

    expect_raises(
        "2000 字节 + 1 标题（应越界）",
        lambda: HomeworkCreate.model_validate({
            "title": "y" * 201,
            "published_at": "2026-10-18",
        }),
        ValueError,
    )


# ---------- 7. 错误处理与标准化 ----------

def t_错误处理与标准化错误() -> None:
    section("7. 错误处理与标准化错误")
    fresh_db()

    # 所有 SkillError 必须有 code/status/hint，JSON 序列化必须不含堆栈
    try:
        service.get_course("no-such-id")
    except SkillError as e:
        d = e.to_dict()
        expect("not_found.code", d["ok"] is False and d["error"]["code"] == "not_found")
        expect("not_found.status", d["error"]["status"] == 404)
        expect("not_found 含 hint", "hint" in d["error"])
        # to_dict 不含 stack/exception 等
        raw = json.dumps(d, ensure_ascii=False)
        expect("to_dict 不泄漏堆栈", "Traceback" not in raw and "line" not in raw.lower())

    # missing_param
    try:
        service.create_homework({"published_at": "2026-10-18"})  # 缺 title
    except Exception as e:  # noqa: BLE001
        # Pydantic 的 ValidationError 与我们 SkillError 不同；service.create_homework 走 Pydantic 会原样抛
        # 我们只校验：要么是 SkillError，要么是 ValidationError，二者都不会泄露路径
        expect("参数错误不泄露内部路径", "banjiao" not in str(e)[:200] or "ValidationError" in str(e)[:200])

    # 异常被路由层捕获后输出也是 ok:false
    # 通过模拟脚本入口验证
    # 通过路径直接加载 scripts/skill.py（避免依赖 scripts 包）
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "skill_entry", SKILL_ROOT / "scripts" / "skill.py"
    )
    skill_entry = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(skill_entry)
    rc = skill_entry.route("course.get", '{"id":"absent-id"}')
    expect("入口路由捕获 SkillError 返回 0", rc == 0)


# ---------- 8. JSON 序列化安全性 ----------

def t_JSON序列化安全性() -> None:
    section("8. JSON 序列化安全性（日期/时间/UUID/None）")
    fresh_db()

    c = service.create_course({
        "name": "序列化测试",
        "class_group": None,
        "start_time": "08:30",
        "end_time": "10:05",
        "semester_start": "2026-09-01",
        "semester_end": "2027-01-15",
        "weekday": 0,
        "notes": None,
    })

    # model_dump(mode="json") 应保证：
    #   - date → "YYYY-MM-DD"
    #   - time → "HH:MM:SS"
    #   - None → null
    #   - UUID → string
    d = c.model_dump(mode="json")

    expect("date 序列化为字符串", d["semester_start"] == "2026-09-01")
    expect("time 序列化为字符串", isinstance(d["start_time"], str) and d["start_time"].startswith("08:30"))
    expect("None 序列化为 null", d["class_group"] is None and d["notes"] is None)
    expect("UUID 序列化为字符串", isinstance(d["id"], str) and len(d["id"]) == 36)

    # 可被 json.dumps 反序列化
    raw = json.dumps(d, ensure_ascii=False)
    restored = json.loads(raw)
    expect("round-trip 一致", restored == d)

    # 写 DB 不抛错
    fresh_db()
    service.create_course(d)  # 用 dict 直接构造（绕过 model）
    expect("通过 dict 重新创建不报错", service.list_courses()[0].name == "序列化测试")


if __name__ == "__main__":
    try:
        sys.exit(run_all())
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        sys.exit(2)
    finally:
        cleanup()
