"""伴教业务逻辑层。

LLM 入口脚本、FastAPI 路由都通过这里读写数据，保证两条路径行为一致。
所有方法要么返回领域对象（被 .model_dump(mode="json") 序列化为 dict），
要么返回标准化错误 SkillError。
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Optional

from .db import db
from .llm_errors import bad_param, missing_param, not_found
from .models import (
    Affair,
    AffairCreate,
    AffairUpdate,
    Course,
    CourseCreate,
    CourseUpdate,
    Document,
    Homework,
    HomeworkCreate,
    HomeworkUpdate,
    ProgressCreate,
    ProgressEntry,
    ProgressUpdate,
)


def _dump(obj) -> dict[str, Any]:
    return obj.model_dump(mode="json")


# ============== 课程 ==============

def list_courses() -> list[Course]:
    return db.list_courses()


def get_course(course_id: str) -> Course:
    c = db.get_course(course_id)
    if c is None:
        raise not_found("课程", course_id)
    return c


def create_course(data: CourseCreate | dict) -> Course:
    if isinstance(data, dict):
        d = data
    else:
        d = data.model_dump()
    if not d.get("name"):
        raise bad_param("name", "课程名不能为空")
    c = Course(**d)
    db.create_course(c)
    return c


def update_course(course_id: str, patch: CourseUpdate | dict) -> Course:
    c = get_course(course_id)
    fields = patch.model_dump(exclude_unset=True) if hasattr(patch, "model_dump") else patch
    if fields:
        fields = CourseUpdate.model_validate(fields).model_dump(exclude_unset=True)
    merged = c.model_copy(update=fields).model_dump()
    Course.model_validate(merged)
    for k, v in fields.items():
        setattr(c, k, v)
    c.updated_at = datetime.now()
    db.update_course(c)
    return c


def delete_course(course_id: str) -> Course:
    c = get_course(course_id)
    db.delete_course(course_id)
    return c


def _week_occurrences(course: Course, start: date, end: date) -> list[tuple[date, int]]:
    """返回课程在 [start, end] 内的 (上课日, 第几周) 列表。

    - 无 weekday：返回空（一次性课程？用进度记录自行管理 lesson_date）。
    - 有 weekday + 有 semester_start：按 weekday 计算周偏移，再用 weeks 列表过滤。
      weeks 为 None / 空列表视为"不限定周次"，所有周均生效；非空列表才做命中判定。
    - 有 weekday + 无 semester_start：按 weekday 推到 start-end 范围，周次固定为 1。
    """
    if course.weekday is None:
        return []
    weeks_filter: Optional[set[int]] = None
    if course.weeks:
        weeks_filter = set(course.weeks)
    out: list[tuple[date, int]] = []
    cur = start
    while cur <= end:
        if course.semester_end is not None and cur > course.semester_end:
            break
        if cur.weekday() == course.weekday:
            week_no: Optional[int] = None
            if course.semester_start is not None:
                delta_days = (cur - course.semester_start).days
                if delta_days < 0:
                    cur += timedelta(days=1)
                    continue
                week_no = delta_days // 7 + 1
                if weeks_filter is not None and week_no not in weeks_filter:
                    week_no = None
            if week_no is not None or course.semester_start is None:
                out.append((cur, week_no if week_no is not None else 1))
        cur += timedelta(days=1)
    return out


def todays_lessons(today: date | None = None) -> list[dict]:
    today = today or date.today()
    weekday = today.weekday()
    out: list[dict] = []
    for c in db.list_courses():
        if c.weekday != weekday:
            continue
        occs = _week_occurrences(c, today, today)
        if not occs:
            continue
        _, week_no = occs[0]
        out.append(_dump(c) | {"today_date": today.isoformat(), "week_no": week_no})
    out.sort(key=lambda x: x.get("start_time") or "")
    return out


def week_lessons(start: date | None = None) -> list[dict]:
    """返回 start 起 7 天内的全部课程（按日期 + 时间排序）。"""
    start = start or date.today()
    end = start + timedelta(days=6)
    out: list[dict] = []
    for c in db.list_courses():
        occs = _week_occurrences(c, start, end)
        for d, week_no in occs:
            out.append(
                {
                    **_dump(c),
                    "lesson_date": d.isoformat(),
                    "week_no": week_no,
                }
            )
    out.sort(key=lambda x: (x["lesson_date"], x.get("start_time") or ""))
    return out


# ============== 进度 ==============

def list_progress(course_id: str | None = None) -> list[ProgressEntry]:
    return db.list_progress(course_id)


def get_progress(pid: str) -> ProgressEntry:
    p = db.get_progress(pid)
    if p is None:
        raise not_found("进度记录", pid)
    return p


def create_progress(data: ProgressCreate | dict) -> ProgressEntry:
    fields = data.model_dump() if hasattr(data, "model_dump") else dict(data)
    if not fields.get("topic"):
        raise bad_param("topic", "本节主题不能为空")
    if db.get_course(fields["course_id"]) is None:
        raise not_found("课程", fields["course_id"])
    p = ProgressEntry(**fields)
    db.create_progress(p)
    return p


def update_progress(pid: str, patch: ProgressUpdate | dict) -> ProgressEntry:
    p = get_progress(pid)
    fields = patch.model_dump(exclude_unset=True) if hasattr(patch, "model_dump") else patch
    if fields:
        fields = ProgressUpdate.model_validate(fields).model_dump(exclude_unset=True)
    merged = p.model_copy(update=fields).model_dump()
    ProgressEntry.model_validate(merged)
    for k, v in fields.items():
        setattr(p, k, v)
    p.updated_at = datetime.now()
    db.update_progress(p)
    return p


def delete_progress(pid: str) -> ProgressEntry:
    p = get_progress(pid)
    db.delete_progress(pid)
    return p


# ============== 作业 ==============

def list_homework(
    status: str | None = None, only_pending: bool = False, overdue: bool = False
) -> list[Homework]:
    return db.list_homework(status=status, only_pending=only_pending, overdue=overdue)


def get_homework(hid: str) -> Homework:
    h = db.get_homework(hid)
    if h is None:
        raise not_found("作业", hid)
    return h


def create_homework(data: HomeworkCreate | dict) -> Homework:
    fields = data.model_dump() if hasattr(data, "model_dump") else dict(data)
    if not fields.get("title"):
        raise bad_param("title", "作业标题不能为空")
    if not fields.get("published_at"):
        raise missing_param("published_at", "请向用户确认作业发布日期")
    if fields.get("course_id") and db.get_course(fields["course_id"]) is None:
        raise not_found("课程", fields["course_id"])
    h = Homework(**fields)
    db.create_homework(h)
    return h


def update_homework(hid: str, patch: HomeworkUpdate | dict) -> Homework:
    h = get_homework(hid)
    fields = patch.model_dump(exclude_unset=True) if hasattr(patch, "model_dump") else patch
    if fields:
        fields = HomeworkUpdate.model_validate(fields).model_dump(exclude_unset=True)
    merged = h.model_copy(update=fields).model_dump()
    Homework.model_validate(merged)
    for k, v in fields.items():
        setattr(h, k, v)
    if h.status == "submitted" and h.submitted_at is None:
        h.submitted_at = date.today()
    h.updated_at = datetime.now()
    db.update_homework(h)
    return h


def delete_homework(hid: str) -> Homework:
    h = get_homework(hid)
    db.delete_homework(hid)
    return h


def due_soon_homework(today: date | None = None) -> list[dict]:
    """到期提醒：due_at - remind_before_days <= today <= due_at 且仍 pending。"""
    today = today or date.today()
    out: list[dict] = []
    for h in db.list_homework(only_pending=True):
        if h.due_at is None:
            continue
        deadline = h.due_at - timedelta(days=h.remind_before_days)
        if deadline <= today <= h.due_at:
            out.append(
                _dump(h)
                | {
                    "days_left": (h.due_at - today).days,
                    "remind_deadline": deadline.isoformat(),
                }
            )
    out.sort(key=lambda x: x["due_at"])
    return out


# ============== 事务 ==============

def list_affairs() -> list[Affair]:
    return db.list_affairs()


def get_affair(aid: str) -> Affair:
    a = db.get_affair(aid)
    if a is None:
        raise not_found("事务", aid)
    return a


def create_affair(data: AffairCreate | dict) -> Affair:
    fields = data.model_dump() if hasattr(data, "model_dump") else dict(data)
    if not fields.get("title"):
        raise bad_param("title", "事务标题不能为空")
    a = Affair(**fields)
    db.create_affair(a)
    return a


def update_affair(aid: str, patch: AffairUpdate | dict) -> Affair:
    a = get_affair(aid)
    fields = patch.model_dump(exclude_unset=True) if hasattr(patch, "model_dump") else patch
    if fields:
        fields = AffairUpdate.model_validate(fields).model_dump(exclude_unset=True)
    merged = a.model_copy(update=fields).model_dump()
    Affair.model_validate(merged)
    for k, v in fields.items():
        setattr(a, k, v)
    a.updated_at = datetime.now()
    db.update_affair(a)
    return a


def delete_affair(aid: str) -> Affair:
    a = get_affair(aid)
    db.delete_affair(aid)
    return a


def due_soon_affairs(now: datetime | None = None) -> list[dict]:
    """到期提醒：remind_deadline <= now <= due_at 且未提醒。"""
    now = now or datetime.now()
    out: list[dict] = []
    for a in db.list_affairs():
        if a.reminded or a.due_at is None:
            continue
        remind_at = a.due_at - timedelta(minutes=a.remind_before_minutes)
        if remind_at <= now <= a.due_at:
            out.append(
                _dump(a)
                | {
                    "minutes_left": int((a.due_at - now).total_seconds() // 60),
                    "remind_at": remind_at.isoformat(),
                }
            )
    return out


def mark_affair_reminded(aid: str) -> Affair:
    a = get_affair(aid)
    a.reminded = True
    a.updated_at = datetime.now()
    db.update_affair(a)
    return a


# ============== 文档 ==============

def list_documents() -> list[Document]:
    return db.list_documents()


def delete_document(did: str) -> Document:
    d = db.get_document(did)
    if d is None:
        raise not_found("文档", did)
    db.delete_document(did)
    return d
