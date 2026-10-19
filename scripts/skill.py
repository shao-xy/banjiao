"""伴教 Skill 的 LLM 入口脚本。

大模型通过子进程调用本脚本读写本地数据，不需要后端 HTTP 即可完成全部持久化操作。

调用方式（PowerShell / Bash 都行）：
    python scripts/skill.py <action> [--json '<json参数>']

本脚本统一输出**一行 JSON** 到 stdout，便于上层 Agent 解析：
    成功：{"ok": true, "data": ...}
    失败：{"ok": false, "error": {"code": "...", "message": "...", "status": N, "hint": "...", "extra": {...}}}

Actions（全部不区分大小写）：
    # 课程
    course.list
    course.get          --json '{"id": "<course_id>"}'
    course.create       --json '{"name": "...", "class_group": "...", ...}'
    course.update       --json '{"id": "...", "name": "新名", ...}'
    course.delete       --json '{"id": "..."}'
    course.today
    course.week         --json '{"start": "2026-10-20"}'   # start 可选

    # 进度
    progress.list       --json '{"course_id": "..."}'     # course_id 可选
    progress.create     --json '{"course_id":"...","lesson_date":"...","topic":"...",...}'
    progress.update     --json '{"id":"...","completed":true,...}'
    progress.delete     --json '{"id":"..."}'

    # 作业
    homework.list       --json '{"only_pending":true,"overdue":false}'
    homework.create     --json '{"title":"...","published_at":"...","due_at":"...",...}'
    homework.update     --json '{"id":"...","status":"submitted",...}'
    homework.delete     --json '{"id":"..."}'
    homework.due_soon

    # 事务
    affair.list
    affair.create       --json '{"title":"...","kind":"meeting","due_at":"...",...}'
    affair.update       --json '{"id":"...","reminded":true}'
    affair.delete       --json '{"id":"..."}'
    affair.mark_reminded --json '{"id":"..."}'

    # 文档 / 向量
    doc.list
    doc.add_text        --json '{"title":"...","text":"...","path":"relative/path.md","course_id":"..."}'
    doc.add_file        --json '{"title":"...","rel_path":"outline.md"}'
    doc.remove          --json '{"id":"..."}'
    vec.search          --json '{"query":"向量检索关键词","top_k":5}'
    vec.stats

通用约定：
- 日期：字符串 "YYYY-MM-DD"
- 时间：字符串 "HH:MM"
- datetime：ISO 8601，如 "2026-10-18T15:00:00"
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

# 把 src/ 加入 sys.path，便于作为子进程被任意 CWD 调用
SKILL_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = SKILL_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from banjiao import service, vector_index  # noqa: E402
from banjiao.llm_errors import SkillError, not_found, bad_param, missing_param  # noqa: E402


# ---------- 输出工具 ----------

def _ok(data: Any) -> int:
    sys.stdout.write(json.dumps({"ok": True, "data": data}, ensure_ascii=False))
    sys.stdout.write("\n")
    return 0


def _fail(e: SkillError) -> int:
    sys.stdout.write(json.dumps(e.to_dict(), ensure_ascii=False))
    sys.stdout.write("\n")
    return 0  # 永远返回 0，把状态交给 stdout JSON；上层 Agent 据 ok 字段判断


def _wrap(callable_):
    """统一捕获 SkillError / Exception。"""
    try:
        result = callable_()
    except SkillError as e:
        return _fail(e)
    except Exception as e:  # noqa: BLE001
        return _fail(SkillError("internal_error", f"内部错误：{e}", status=500))
    return _ok(result)


def _load_json_arg(raw: str | None) -> dict:
    if not raw:
        return {}
    if raw == "@-":
        raw = sys.stdin.read()
    raw = raw.strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        raise SkillError(
            "bad_json",
            f"--json 参数不是合法 JSON：{e}",
            status=400,
            hint="请用双引号包裹字符串、并确保 UTF-8 直传。",
        )


# ---------- 路由 ----------

def route(action: str, raw: str | None) -> int:
    args = _load_json_arg(raw)

    a = action.strip().lower()

    # ===== 课程 =====
    if a == "course.list":
        return _wrap(lambda: [c.model_dump(mode="json") for c in service.list_courses()])

    if a == "course.get":
        if "id" not in args:
            return _fail(missing_param("id"))
        return _wrap(lambda: service.get_course(args["id"]).model_dump(mode="json"))

    if a == "course.create":
        from banjiao.models import CourseCreate
        try:
            payload = CourseCreate.model_validate(args)
        except Exception as e:
            return _fail(SkillError("bad_param", str(e), status=422))
        return _wrap(lambda: service.create_course(payload).model_dump(mode="json"))

    if a == "course.update":
        from banjiao.models import CourseUpdate
        if "id" not in args:
            return _fail(missing_param("id"))
        cid = args.pop("id")
        try:
            payload = CourseUpdate.model_validate(args)
        except Exception as e:
            return _fail(SkillError("bad_param", str(e), status=422))
        return _wrap(lambda: service.update_course(cid, payload).model_dump(mode="json"))

    if a == "course.delete":
        if "id" not in args:
            return _fail(missing_param("id"))
        cid = args["id"]
        return _wrap(lambda: service.delete_course(cid).model_dump(mode="json") | {"deleted": True})

    if a == "course.today":
        return _wrap(lambda: service.todays_lessons())

    if a == "course.week":
        from datetime import date
        start = None
        if "start" in args and args["start"]:
            try:
                start = date.fromisoformat(args["start"])
            except ValueError:
                return _fail(bad_param("start", "应为 YYYY-MM-DD"))
        return _wrap(lambda: service.week_lessons(start))

    # ===== 进度 =====
    if a == "progress.list":
        return _wrap(lambda: [p.model_dump(mode="json") for p in service.list_progress(args.get("course_id"))])

    if a == "progress.create":
        from banjiao.models import ProgressCreate
        try:
            payload = ProgressCreate.model_validate(args)
        except Exception as e:
            return _fail(SkillError("bad_param", str(e), status=422))
        return _wrap(lambda: service.create_progress(payload).model_dump(mode="json"))

    if a == "progress.update":
        from banjiao.models import ProgressUpdate
        if "id" not in args:
            return _fail(missing_param("id"))
        pid = args.pop("id")
        try:
            payload = ProgressUpdate.model_validate(args)
        except Exception as e:
            return _fail(SkillError("bad_param", str(e), status=422))
        return _wrap(lambda: service.update_progress(pid, payload).model_dump(mode="json"))

    if a == "progress.delete":
        if "id" not in args:
            return _fail(missing_param("id"))
        pid = args["id"]
        return _wrap(lambda: service.delete_progress(pid).model_dump(mode="json") | {"deleted": True})

    # ===== 作业 =====
    if a == "homework.list":
        return _wrap(lambda: [h.model_dump(mode="json") for h in service.list_homework(
            status=args.get("status"),
            only_pending=bool(args.get("only_pending", False)),
            overdue=bool(args.get("overdue", False)),
        )])

    if a == "homework.create":
        from banjiao.models import HomeworkCreate
        try:
            payload = HomeworkCreate.model_validate(args)
        except Exception as e:
            return _fail(SkillError("bad_param", str(e), status=422))
        return _wrap(lambda: service.create_homework(payload).model_dump(mode="json"))

    if a == "homework.update":
        from banjiao.models import HomeworkUpdate
        if "id" not in args:
            return _fail(missing_param("id"))
        hid = args.pop("id")
        try:
            payload = HomeworkUpdate.model_validate(args)
        except Exception as e:
            return _fail(SkillError("bad_param", str(e), status=422))
        return _wrap(lambda: service.update_homework(hid, payload).model_dump(mode="json"))

    if a == "homework.delete":
        if "id" not in args:
            return _fail(missing_param("id"))
        hid = args["id"]
        return _wrap(lambda: service.delete_homework(hid).model_dump(mode="json") | {"deleted": True})

    if a == "homework.due_soon":
        return _wrap(lambda: service.due_soon_homework())

    # ===== 事务 =====
    if a == "affair.list":
        return _wrap(lambda: [x.model_dump(mode="json") for x in service.list_affairs()])

    if a == "affair.create":
        from banjiao.models import AffairCreate
        try:
            payload = AffairCreate.model_validate(args)
        except Exception as e:
            return _fail(SkillError("bad_param", str(e), status=422))
        return _wrap(lambda: service.create_affair(payload).model_dump(mode="json"))

    if a == "affair.update":
        from banjiao.models import AffairUpdate
        if "id" not in args:
            return _fail(missing_param("id"))
        aid = args.pop("id")
        try:
            payload = AffairUpdate.model_validate(args)
        except Exception as e:
            return _fail(SkillError("bad_param", str(e), status=422))
        return _wrap(lambda: service.update_affair(aid, payload).model_dump(mode="json"))

    if a == "affair.delete":
        if "id" not in args:
            return _fail(missing_param("id"))
        aid = args["id"]
        return _wrap(lambda: service.delete_affair(aid).model_dump(mode="json") | {"deleted": True})

    if a == "affair.mark_reminded":
        if "id" not in args:
            return _fail(missing_param("id"))
        return _wrap(lambda: service.mark_affair_reminded(args["id"]).model_dump(mode="json"))

    # ===== 文档 / 向量 =====
    if a == "doc.list":
        return _wrap(lambda: [d.model_dump(mode="json") for d in service.list_documents()])

    if a == "doc.add_text":
        for need in ("title", "text", "path"):
            if need not in args:
                return _fail(missing_param(need))
        idx = vector_index.get_index()
        return _wrap(lambda: idx.add_text(
            title=args["title"],
            text=args["text"],
            path=args["path"],
            course_id=args.get("course_id"),
        ).model_dump(mode="json"))

    if a == "doc.add_file":
        for need in ("title", "rel_path"):
            if need not in args:
                return _fail(missing_param(need))
        idx = vector_index.get_index()
        return _wrap(lambda: idx.add_file(
            title=args["title"],
            rel_path=args["rel_path"],
            course_id=args.get("course_id"),
        ).model_dump(mode="json"))

    if a == "doc.remove":
        if "id" not in args:
            return _fail(missing_param("id"))
        did = args["id"]
        return _wrap(lambda: vector_index.get_index().remove(did))

    if a == "vec.search":
        if "query" not in args:
            return _fail(missing_param("query", "请告诉我要在大纲/课件里找什么"))
        idx = vector_index.get_index()
        return _wrap(lambda: idx.search(args["query"], top_k=int(args.get("top_k", 5))))

    if a == "vec.stats":
        return _wrap(lambda: vector_index.get_index().stats())

    # ===== 兜底 =====
    return _fail(SkillError(
        "unknown_action",
        f"未知 action: {action!r}",
        status=400,
        hint="执行 `python scripts/skill.py` 不带参数查看全部 action。",
        extra={"action": action},
    ))


# ---------- 自描述 ----------

HELP_TEXT = """伴教 Skill · LLM 入口脚本

用法：
    python scripts/skill.py <action> [--json '<json>']

数据落盘：直接在 SQLite 写入，无需启动后端。
输出：统一 JSON 到 stdout，{"ok": true | false, ...}

常见示例：
    # 今日课表
    python scripts/skill.py course.today

    # 新建课程
    python scripts/skill.py course.create --json '{\"name\":\"高等数学\",\"class_group\":\"2024-A\",\"weekday\":2,\"start_time\":\"08:00\",\"end_time\":\"09:50\",\"semester_start\":\"2026-09-01\",\"semester_end\":\"2027-01-15\",\"weeks\":[1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16]}'

    # 标记作业已提交
    python scripts/skill.py homework.update --json '{\"id\":\"<作业id>\",\"status\":\"submitted\"}'

    # 大纲检索
    python scripts/skill.py vec.search --json '{\"query\":\"极限定义\",\"top_k\":3}'

可用 action：
  course.list / course.get / course.create / course.update / course.delete / course.today / course.week
  progress.list / progress.create / progress.update / progress.delete
  homework.list / homework.create / homework.update / homework.delete / homework.due_soon
  affair.list / affair.create / affair.update / affair.delete / affair.mark_reminded
  doc.list / doc.add_text / doc.add_file / doc.remove
  vec.search / vec.stats
"""


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="skill.py",
        description="伴教 Skill：LLM 直接读写教学事务的入口脚本",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=HELP_TEXT.split("用法：")[1] if "用法：" in HELP_TEXT else "",
    )
    parser.add_argument("action", nargs="?", help="操作名，如 course.create")
    parser.add_argument("--json", help='JSON 参数；可用 "@-" 从 stdin 读取')
    parser.add_argument("--json-file", help='从文件读取 JSON 参数')
    args = parser.parse_args()

    if not args.action or args.action in ("-h", "--help"):
        sys.stdout.write(HELP_TEXT)
        return 0

    if args.json_file:
        json_payload = Path(args.json_file).read_text(encoding="utf-8")
    else:
        json_payload = args.json

    return route(args.action, json_payload)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except SkillError as e:
        # 任何 main() 内未捕获的 SkillError 都按标准化错误输出，永远 exit 0
        sys.stdout.write(json.dumps(e.to_dict(), ensure_ascii=False) + "\n")
        sys.stdout.flush()
        sys.exit(0)
    except Exception as e:  # noqa: BLE001
        # 兜底：内部异常也不向 Agent 抛 exit code，而是输出标准化错误 JSON
        sys.stdout.write(json.dumps(
            SkillError(
                "internal_error",
                f"内部错误：{type(e).__name__}",
                status=500,
            ).to_dict(),
            ensure_ascii=False,
        ) + "\n")
        sys.stdout.flush()
        sys.exit(0)
