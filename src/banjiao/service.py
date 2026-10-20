"""伴教业务逻辑层。

LLM 入口脚本、FastAPI 路由都通过这里读写数据，保证两条路径行为一致。
所有方法要么返回领域对象（被 .model_dump(mode="json") 序列化为 dict），
要么返回标准化错误 SkillError。
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from typing import Any, Optional

from .db import db
from .config import settings
from .llm_errors import SkillError, bad_param, missing_param, not_found
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


def _expand_weeks_range(weeks_range: Optional[list[int]], week_pattern: str) -> Optional[set[int]]:
    """把 weeks_range=[起,止] + week_pattern 展开成具体的周次集合（B3）。

    例：weeks_range=[1,7], week_pattern="odd" → {1,3,5,7}
        weeks_range=[1,15], week_pattern="all" → {1..15}
    """
    if not weeks_range:
        return None
    lo, hi = weeks_range
    if week_pattern == "odd":
        return set(range(lo if lo % 2 == 1 else lo + 1, hi + 1, 2))
    if week_pattern == "even":
        return set(range(lo if lo % 2 == 0 else lo + 1, hi + 1, 2))
    return set(range(lo, hi + 1))


def _course_all_sections(course: Course) -> list[Any]:
    """返回课程的所有上课时段（B1）。

    sections 非空 → 直接返回；
    否则把扁平字段当作唯一时段（向后兼容）。
    """
    if course.sections:
        return list(course.sections)
    return [course]


def _section_week_filter(section: Any) -> Optional[set[int]]:
    """取一个时段的周次过滤集合（支持 weeks / weeks_range + week_pattern）。"""
    if section.weeks:
        return set(section.weeks)
    if section.weeks_range:
        return _expand_weeks_range(section.weeks_range, section.week_pattern)
    if section.week_pattern in ("odd", "even"):
        return None  # 走奇偶逻辑
    return None


def _week_occurrences(course: Course, start: date, end: date) -> list[tuple[date, int]]:
    """返回课程在 [start, end] 内的 (上课日, 第几周) 列表。

    支持多时段（sections）：一门课多个 weekday/周次模式/节次都算。
    """
    sections = _course_all_sections(course)
    all_out: list[tuple[date, int]] = []
    seen: set[tuple[date, int]] = set()

    for sec in sections:
        if sec.weekday is None:
            continue
        weeks_filter = _section_week_filter(sec)
        cur = start
        while cur <= end:
            if course.semester_end is not None and cur > course.semester_end:
                break
            if cur.weekday() == sec.weekday:
                week_no: Optional[int] = None
                if course.semester_start is not None:
                    delta_days = (cur - course.semester_start).days
                    if delta_days < 0:
                        cur += timedelta(days=1)
                        continue
                    week_no = delta_days // 7 + 1
                    # week_pattern 过滤
                    if sec.week_pattern == "odd" and week_no % 2 == 0:
                        week_no = None
                    elif sec.week_pattern == "even" and week_no % 2 == 1:
                        week_no = None
                    elif weeks_filter is not None and week_no not in weeks_filter:
                        week_no = None
                if week_no is not None or course.semester_start is None:
                    key = (cur, week_no if week_no is not None else 1)
                    if key not in seen:
                        seen.add(key)
                        all_out.append(key)
            cur += timedelta(days=1)
    all_out.sort(key=lambda x: x[0])
    return all_out


def _section_time_text(sec: Any) -> tuple[str, str]:
    """取一个时段的 (start_time, end_time) 文本；连排用 period_nos 时从课表推。"""
    st = getattr(sec, "start_time", None)
    et = getattr(sec, "end_time", None)
    st_txt = st.strftime("%H:%M") if st else ""
    et_txt = et.strftime("%H:%M") if et else ""
    return st_txt, et_txt


def _section_period_nos(sec: Any) -> list[int]:
    """取一个时段占用的节次列表（连排）。"""
    pnos = getattr(sec, "period_nos", None)
    if pnos:
        return list(pnos)
    pno = getattr(sec, "period_no", None)
    return [pno] if pno else []


def _resolve_period_times(period_nos: list[int]) -> Optional[tuple[str, str]]:
    """用学校时间表把节次列表解析成 (最早 start, 最晚 end)；任一节缺失则返回 None。"""
    if not period_nos:
        return None
    slots = [period_resolve(p) for p in period_nos]
    if any(s is None for s in slots):
        return None
    starts = [s["start"] for s in slots if s]
    ends = [s["end"] for s in slots if s]
    return min(starts), max(ends)


def todays_lessons(today: date | None = None) -> list[dict]:
    """今天的课。多时段课程每段一行，方便教师看清"今天几节课、分别什么时间"。"""
    today = today or date.today()
    out: list[dict] = []
    for c in db.list_courses():
        for sec in _course_all_sections(c):
            if sec.weekday != today.weekday():
                continue
            occs = _week_occurrences(c, today, today)
            if not occs:
                continue
            _, week_no = occs[0]
            st_txt, et_txt = _section_time_text(sec)
            pnos = _section_period_nos(sec)
            row = _dump(c) | {"today_date": today.isoformat(), "week_no": week_no}
            # 用该时段的节次/时间覆盖 course 级别（多时段时 course 级只是主时段）
            if pnos:
                row["period_nos"] = pnos
                if not st_txt:
                    resolved = _resolve_period_times(pnos)
                    if resolved:
                        st_txt, et_txt = resolved
            if st_txt:
                row["start_time"] = st_txt
            if et_txt:
                row["end_time"] = et_txt
            if len(_course_all_sections(c)) > 1:
                row["is_multi_section"] = True
            out.append(row)
    out.sort(key=lambda x: (x.get("start_time") or "99:99"))
    return out


def week_lessons(start: date | None = None, weeks: int = 1) -> list[dict]:
    """返回 start 起 weeks 周内的全部课程（按日期 + 时间排序）。

    weeks=1 → 7 天；weeks=2 → 14 天（F4：不再硬编码只能查一周）
    """
    start = start or date.today()
    if weeks < 1 or weeks > 20:
        raise bad_param("weeks", "取值 1..20")
    end = start + timedelta(days=7 * weeks - 1)
    out: list[dict] = []
    for c in db.list_courses():
        sections = _course_all_sections(c)
        occs = _week_occurrences(c, start, end)
        # 按 (日期, 时段) 展开：一门课多个时段 → 同一天多行
        by_date: dict[date, int] = {d: w for d, w in occs}
        for d, week_no in occs:
            for sec in sections:
                if sec.weekday != d.weekday():
                    continue
                st_txt, et_txt = _section_time_text(sec)
                pnos = _section_period_nos(sec)
                if pnos and not st_txt:
                    resolved = _resolve_period_times(pnos)
                    if resolved:
                        st_txt, et_txt = resolved
                row = {
                    **_dump(c),
                    "lesson_date": d.isoformat(),
                    "week_no": week_no,
                }
                if pnos:
                    row["period_nos"] = pnos
                if st_txt:
                    row["start_time"] = st_txt
                if et_txt:
                    row["end_time"] = et_txt
                if len(sections) > 1:
                    row["is_multi_section"] = True
                out.append(row)
    out.sort(key=lambda x: (x["lesson_date"], x.get("start_time") or "99:99"))
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
    course = db.get_course(fields["course_id"])
    if course is None:
        raise not_found("课程", fields["course_id"])
    p = ProgressEntry(**fields)
    db.create_progress(p)
    return p


def progress_create_with_warning(data: ProgressCreate | dict) -> dict:
    """progress.create 的返回包装：额外附 F3 的日期告警（若有）。

    create_progress 保持返回领域模型（service 层惯例），本函数只给 CLI/API 用。
    """
    p = create_progress(data)
    out = _dump(p)
    course = db.get_course(p.course_id)
    if course is not None:
        warn = _progress_date_warning(course, p.lesson_date)
        if warn:
            out["warning"] = warn
    return out


def _progress_date_warning(course: Course, lesson_date: date) -> str | None:
    """检查 lesson_date 是否落在该课程的上课日；不是则返回提示（不抛错）。"""
    if course.weekday is None and not course.sections:
        return None
    occs = _week_occurrences(course, lesson_date, lesson_date)
    if occs:
        return None
    wd = lesson_date.isoformat()
    if course.weekday is not None:
        cn = "一二三四五六日"
        return (
            f"提示：{wd}（周{cn[lesson_date.weekday()]}）"
            f"不是该课程的上课日（weekday={course.weekday}，周{cn[course.weekday]}）。"
            "若为补记历史进度可忽略；否则请核对课程时间或日期。"
        )
    return f"提示：{wd} 不在该课程的任何上课时段内。若为补记历史进度可忽略；否则请核对。"


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


# ============== 文件系统（阶段 ①） ==============

import shutil as _shutil
from pathlib import Path as _Path
import hashlib as _hashlib
import re as _re

from .models import (
    CourseBinding,
    CourseFile,
    FileRole,
    TopKind,
    ROLE_DIRS,
    DIR_TO_ROLE,
    ALLOWED_EXTENSIONS,
)


_TOP_KINDS: tuple[str, ...] = ("教学", "科研", "事务", "文件")


def _data_root() -> _Path:
    """data/ 的 Path；运行期 settings.data_dir 已解析。"""
    return settings.data_dir


def _safe_path(rel: str | _Path) -> _Path:
    """解析 rel 为 data/ 子树下的绝对路径。禁止：绝对路径、含 '..' 段、空串。

    仅允许纯相对路径组件，用 forward-slash 风格（Windows 也接受）。
    """
    if rel is None:
        raise bad_param("path", "不能为空")
    if isinstance(rel, _Path):
        rel = str(rel)
    rel = rel.strip()
    if not rel:
        raise bad_param("path", "不能为空")
    if rel.startswith(("/", "\\")) or _re.match(r"^[A-Za-z]:[\\/]", rel):
        raise bad_param("path", "禁止绝对路径")
    # 规范化：用正斜杠
    normalized = rel.replace("\\", "/")
    parts = [p for p in normalized.split("/") if p != ""]
    for p in parts:
        if p == "..":
            raise bad_param("path", "禁止 '..' 路径段")
    full = _data_root()
    for p in parts:
        full = full / p
    # 二次校验：必须落在 data_root 之内
    try:
        full_resolved = full.resolve()
        root_resolved = _data_root().resolve()
        full_resolved.relative_to(root_resolved)
    except ValueError:
        raise bad_param("path", "路径必须落在 data 之内")
    return full_resolved


def _check_extension(filename: str) -> None:
    ext = _Path(filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise bad_param(
            "filename",
            f"扩展名 {ext!r} 不在白名单 {sorted(ALLOWED_EXTENSIONS)}",
        )


def _sha256_file(p: _Path) -> str:
    h = _hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _scan_file_record(rel: str, course_id: str | None, top_kind: str | None, role: str | None) -> CourseFile | None:
    """增量扫描单个文件：mtime 不变则复用；变了重算 sha。"""
    full = _safe_path(rel)
    if not full.exists() or not full.is_file():
        return None
    st = full.stat()
    mtime_ns = st.st_mtime_ns
    cur = db.get_file_current(rel)
    if cur and cur.mtime_ns == mtime_ns and cur.size == st.st_size:
        # 缓存命中；如果绑定信息变了（role / course_id）顺便更新
        if cur.course_id != course_id or cur.top_kind != top_kind or cur.role != role:
            # 当前 cache 已在 db 写入时携带；信息变动应该走 upsert；这里只是补字段
            cur.course_id = course_id
            cur.top_kind = top_kind
            cur.role = role
            cur.updated_at = datetime.now()
            db._mutate(
                "UPDATE course_files SET course_id=?, top_kind=?, role=?, updated_at=? WHERE id=?",
                (course_id, top_kind, role, cur.updated_at.isoformat(timespec="seconds"), cur.id),
            )
        return cur
    # 算 sha
    sha = _sha256_file(full)
    rec = CourseFile(
        course_id=course_id,
        top_kind=top_kind,
        role=role,
        path_rel=rel,
        filename=full.name,
        sha256=sha,
        size=st.st_size,
        mtime_ns=mtime_ns,
    )
    return db.upsert_file_current(rec)


# ============== fs.* ==============

def fs_init() -> list[dict]:
    """幂等创建 4 个顶层文件夹（教学/科研/事务/文件）。

    不删任何已存在内容；返回每个顶层是否存在。
    """
    root = _data_root()
    result: list[dict] = []
    for kind in _TOP_KINDS:
        p = root / kind
        created = False
        if not p.exists():
            p.mkdir(parents=True, exist_ok=False)
            created = True
        result.append({"top_kind": kind, "path": f"{kind}/", "created": created})
    return result


def fs_list_top_dirs() -> list[dict]:
    """列出 4 个顶层下所有子文件夹，附 binding 信息（是否绑定到 Course）。"""
    bindings = {(_b.top_kind, _b.dirname): _b for _b in db.list_bindings()}
    out: list[dict] = []
    root = _data_root()
    for kind in _TOP_KINDS:
        top = root / kind
        out.append({"top_kind": kind, "path": f"{kind}/", "type": "top"})
        if top.exists() and top.is_dir():
            for child in sorted(top.iterdir(), key=lambda p: p.name):
                if not child.is_dir():
                    continue
                # 跳过隐藏/系统目录
                if child.name.startswith(".") or child.name.startswith("_archive"):
                    continue
                bind = bindings.get((kind, child.name))
                out.append({
                    "top_kind": kind,
                    "dirname": child.name,
                    "path": f"{kind}/{child.name}/",
                    "binding": (
                        {
                            "id": bind.id,
                            "course_id": bind.course_id,
                            "updated_at": bind.updated_at.isoformat(timespec="seconds"),
                        }
                        if bind else None
                    ),
                    "is_current": True,
                })
    return out


def fs_bind_course(top_kind: TopKind, dirname: str, course_id: str) -> dict:
    """把顶层目录绑定到 Course。"""
    if top_kind not in _TOP_KINDS:
        raise bad_param("top_kind", f"必须为 {_TOP_KINDS} 之一")
    if db.get_course(course_id) is None:
        raise not_found("课程", course_id)
    full = _safe_path(f"{top_kind}/{dirname}")
    if not full.exists() or not full.is_dir():
        raise not_found(f"{top_kind}/{dirname}", "")
    existing = db.get_binding_by_dir(top_kind, dirname)
    if existing is not None:
        raise SkillError(
            "already_bound",
            f"{top_kind}/{dirname} 已绑定 (course_id={existing.course_id})",
            status=409,
            hint="如要更换绑定，先 fs.unbind_course 再 fs.bind_course。",
            extra={"existing_course_id": existing.course_id},
        )
    by_course = db.get_binding_by_course(course_id)
    if by_course is not None:
        raise SkillError(
            "course_already_bound",
            f"课程 {course_id} 已绑定到 {by_course.top_kind}/{by_course.dirname}",
            status=409,
            hint="一个课程仅可绑定一个目录。",
        )
    bind = CourseBinding(top_kind=top_kind, dirname=dirname, course_id=course_id)
    db.create_binding(bind)
    return {
        "id": bind.id,
        "top_kind": bind.top_kind,
        "dirname": bind.dirname,
        "course_id": bind.course_id,
    }


def fs_unbind_course(binding_id: str) -> dict:
    """解绑：仅断关联，不删目录与文件。"""
    bind = db.get_binding(binding_id)
    if bind is None:
        raise not_found("绑定", binding_id)
    db.delete_binding(binding_id)
    return {"id": binding_id, "deleted": True}


# ============== course.list_files + fs.scan_course ==============

def _scan_dir(rel: str, course_id: str | None) -> list[CourseFile]:
    """扫描一个目录（含子目录），仅识别固定 7 个角色子目录。"""
    full = _safe_path(rel)
    if not full.exists() or not full.is_dir():
        return []
    out: list[CourseFile] = []
    for child in full.iterdir():
        if child.is_dir():
            dir_name = child.name
            role = DIR_TO_ROLE.get(dir_name)
            if role is None:
                # 非角色子目录：跳过（不入清单）
                continue
            sub_rel = f"{rel}/{dir_name}"
            for f in child.iterdir():
                if not f.is_file():
                    continue
                fr = f"{sub_rel}/{f.name}"
                try:
                    _check_extension(f.name)
                    rec = _scan_file_record(fr, course_id, _top_kind_of(rel), role)
                except SkillError:
                    raise
                except Exception:
                    # 跳过不符合的文件
                    continue
                if rec is not None:
                    out.append(rec)
    return out


def _top_kind_of(rel: str) -> TopKind | None:
    parts = rel.replace("\\", "/").split("/")
    if parts and parts[0] in _TOP_KINDS:
        return parts[0]  # type: ignore[return-value]
    return None


def fs_scan_course(course_id: str) -> list[dict]:
    """增量扫描某课程绑定目录下的所有文件。

    行为：
    - 找到 (top_kind, dirname) 该课程的绑定
    - 列举固定 7 个角色子目录下的文件
    - 每个文件用 mtime_ns + size 判定是否变化，变化才重算 sha256
    - 返回清单（去重为当前行）+ 是否需要更新 knowledge
    """
    bind = db.get_binding_by_course(course_id)
    if bind is None:
        # 未绑定：返回空（教师应先 fs.bind_course）
        return []
    base_rel = f"{bind.top_kind}/{bind.dirname}"
    records = _scan_dir(base_rel, course_id)
    return [_dump_file(r) for r in records]


def fs_scan_top_dir(top_kind: TopKind, dirname: str) -> list[dict]:
    """扫描任意顶层目录（即使没绑定 Course 也能扫）。"""
    if top_kind not in _TOP_KINDS:
        raise bad_param("top_kind", f"必须为 {_TOP_KINDS} 之一")
    bind = db.get_binding_by_dir(top_kind, dirname)
    base_rel = f"{top_kind}/{dirname}"
    records = _scan_dir(base_rel, bind.course_id if bind else None)
    return [_dump_file(r) for r in records]


def course_list_files(
    course_id: str | None = None,
    role: FileRole | None = None,
) -> list[dict]:
    """列出数据库已知的所有当前文件。

    注意：这是一个数据库缓存视图，不重新扫描磁盘。如要刷新请先 fs_scan_course。
    """
    if course_id:
        # 校验 course 存在（与旧接口保持一致）
        if db.get_course(course_id) is None:
            raise not_found("课程", course_id)
    rows = db.list_files_current(course_id=course_id, role=role)
    return [_dump_file(r) for r in rows]


def _dump_file(f: CourseFile) -> dict:
    return {
        "id": f.id,
        "course_id": f.course_id,
        "top_kind": f.top_kind,
        "role": f.role,
        "path": f.path_rel,
        "filename": f.filename,
        "sha256": f.sha256,
        "size": f.size,
        "mtime_ns": f.mtime_ns,
        "updated_at": f.updated_at.isoformat(timespec="seconds"),
    }


# ============== fs.archive（教师主动备份；同 backup 语义） ==============

def fs_archive(path_rel: str) -> dict:
    """把文件或文件夹"打快照"到 data/_archive/<时间戳>/...。

    不删除原内容、不覆盖：
        - 文件 → 拷一份 _archive_<时间戳>_<原名>
        - 目录 → 整个复制到 _archive/<时间戳>_<dirname>/

    设计意图：教师若担心覆盖错，先 archive 再写；可随时手工清理 _archive。
    """
    full = _safe_path(path_rel)
    if not full.exists():
        raise not_found("路径", path_rel)
    archive_root = _data_root() / "_archive"
    archive_root.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    if full.is_file():
        dst = archive_root / f"{ts}_{full.name}"
        _shutil.copy2(full, dst)
        rel_dst = str(dst.relative_to(_data_root())).replace("\\", "/")
        return {"archived": True, "src": path_rel, "dst": rel_dst, "kind": "file"}
    # directory
    dst = archive_root / f"{ts}_{full.name}"
    _shutil.copytree(full, dst)
    rel_dst = str(dst.relative_to(_data_root())).replace("\\", "/")
    n = sum(1 for _ in dst.rglob("*") if _.is_file())
    return {"archived": True, "src": path_rel, "dst": rel_dst, "kind": "dir", "files": n}


# ============== fs.delete（硬约束 confirm） ==============

def fs_delete(path_rel: str, confirm: str | None = None) -> dict:
    """删除文件或目录。

    硬约束（防误删）：
    - 删除非空目录 → 必须 confirm="YES_DELETE_<sha8>"，sha8 是对该目录的指纹（递归内容）
    - 删除文件 → 必须 confirm="YES_DELETE_<sha8>"，sha8 是该文件 sha256 前 8 位
    - 空目录 → 不需要 confirm（递归计数为 0）

    删除后：清除 course_files 中所有相关行（不分当前/历史）。
    """
    full = _safe_path(path_rel)
    if not full.exists():
        raise not_found("路径", path_rel)
    is_dir = full.is_dir()

    # 计算"指纹"
    if is_dir:
        all_files = list(full.rglob("*"))
        # 排序后 sha
        h = _hashlib.sha256()
        for f in sorted(all_files, key=lambda p: str(p).lower()):
            if f.is_file():
                rel_inside = str(f.relative_to(full)).replace("\\", "/")
                st = f.stat()
                h.update(rel_inside.encode("utf-8"))
                h.update(str(st.st_size).encode("utf-8"))
                h.update(str(st.st_mtime_ns).encode("utf-8"))
                # 也混入 sha 以增加熵
                h.update(_sha256_file(f).encode("ascii"))
        fingerprint = h.hexdigest()[:8]
        items_count = len([p for p in all_files if p.is_file()])
    else:
        sha = _sha256_file(full)
        fingerprint = sha[:8]
        items_count = 1

    expected = f"YES_DELETE_{fingerprint}"

    if items_count > 0:
        if not confirm:
            raise SkillError(
                "delete_requires_confirm",
                f"{path_rel} 含 {items_count} 项内容，必须显式 confirm='{expected}'。",
                status=400,
                hint="再发一次：fs.delete 并把 confirm 字段填入上面给的字符串。",
                extra={"required_confirm": expected, "items_count": items_count},
            )
        if confirm != expected:
            raise SkillError(
                "confirm_mismatch",
                f"confirm 与 {expected!r} 不匹配",
                status=400,
                extra={"required_confirm": expected},
            )

    # 实际删除：文件 / 目录
    deleted_paths: list[str] = []
    affected_cf_paths: list[str] = []

    if is_dir:
        # 收集所有将删除的文件路径
        for f in full.rglob("*"):
            if f.is_file():
                rp = str(f.relative_to(_data_root())).replace("\\", "/")
                affected_cf_paths.append(rp)
        shutil.rmtree(full)
    else:
        affected_cf_paths.append(path_rel)
        full.unlink()
        deleted_paths.append(path_rel)

    # 同步数据库：删 course_files 行
    cf_deleted = 0
    for p in affected_cf_paths:
        cf_deleted += db.delete_files_for_path(p)

    return {
        "deleted": True,
        "path": path_rel,
        "items_count": items_count,
        "course_files_rows_removed": cf_deleted,
    }


# ============== 学校课程时间表 + 节次解析 ==============

from .config import DEFAULT_SCHOOL_SCHEDULE

_SCHEDULE_FILE = "_school_schedule.json"  # 写在 data/ 下


def _schedule_path() -> _Path:
    return _data_root() / _SCHEDULE_FILE


def _validate_schedule(payload: dict) -> None:
    """校验学校时间表格式。三个 key：morning/afternoon/evening，每个为 [{no, start, end}]。"""
    if not isinstance(payload, dict):
        raise bad_param("schedule", "必须为对象")
    for bucket in ("morning", "afternoon", "evening"):
        if bucket not in payload:
            raise bad_param(bucket, "缺失")
        items = payload[bucket]
        if not isinstance(items, list):
            raise bad_param(bucket, "必须为列表")
        for it in items:
            if not isinstance(it, dict):
                raise bad_param(bucket, "每项必须为对象")
            for k in ("no", "start", "end"):
                if k not in it:
                    raise bad_param(bucket, f"每项必须有 {k}")
            if not isinstance(it["no"], int) or not (1 <= it["no"] <= 12):
                raise bad_param(bucket, "no 必须为 1..12")
            for tk in ("start", "end"):
                if not isinstance(it[tk], str) or len(it[tk]) != 5 or it[tk][2] != ":":
                    raise bad_param(bucket, f"{tk} 必须为 'HH:MM'")


def school_get_schedule() -> dict:
    """返回当前生效的学校课程时间表。优先 data/_school_schedule.json，否则默认。

    返回值额外带 `source` 字段（B4）：
        - "file"：来自教师覆盖的 data/_school_schedule.json
        - "builtin_default"：使用内置的 11 节默认表
    这样调用方不会把"默认表"误判成"空课表"。
    """
    p = _schedule_path()
    if p.exists():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            _validate_schedule(data)
            return {"source": "file", **data}
        except Exception:
            pass  # 损坏就回退默认（绝不抛）
    return {"source": "builtin_default", **DEFAULT_SCHOOL_SCHEDULE}


def school_set_schedule(schedule: dict) -> dict:
    """覆盖学校课程时间表（落盘到 data/_school_schedule.json）。"""
    _validate_schedule(schedule)
    p = _schedule_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(schedule, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"updated": True, "path": str(p.relative_to(_data_root())).replace("\\", "/")}


def period_resolve(period_no: int) -> dict | None:
    """根据节次查学校时间表，返回 {no,start,end} 或 None。"""
    if not isinstance(period_no, int) or not (1 <= period_no <= 12):
        return None
    sched = school_get_schedule()
    for bucket in ("morning", "afternoon", "evening"):
        for it in sched.get(bucket, []):
            if it.get("no") == period_no:
                return {"no": it["no"], "start": it["start"], "end": it["end"]}
    return None


def fs_import_file(
    src: str,
    top_kind: str = "文件",
    dirname: str = "资料",
    filename: str | None = None,
    role: str | None = None,
    archive: bool = False,
) -> dict:
    """把外部文件导入 Skill 管理的目录（A4 / D2）。

    解决两件事：
    1. 教师 / Agent 从任意位置（下载目录、桌面、学校共享）拷文件进来，
       绕开 shell 的路径转义坑（PowerShell 的 `[...]` 通配符、中文路径）
       —— 文件操作在 Python 侧完成，不经过 shell。
    2. 顶层归档目录（`文件/资料/` 等）不是课程目录，没有 7 角色结构；
       本 action 接受任意 `文件/<子目录>/<文件名>` 路径并建立索引记录。

    参数：
        src: 源文件绝对路径（也接受相对当前工作目录的路径）
        top_kind: 顶层分类，默认 "文件"
        dirname: 顶层下的子目录（如 "资料"、"规章制度"）；可省略则用 src 的文件名建在顶层
        filename: 目标文件名；省略则用 src 的文件名
        role: 可选；给 7 角色之一（slides/lecture/... 或中文）时记录 role
        archive: True 则先备份同名旧文件到 _archive
    """
    src_path = _Path(src).expanduser()
    if not src_path.is_absolute():
        src_path = (_Path.cwd() / src_path).resolve()
    if not src_path.exists() or not src_path.is_file():
        raise not_found("源文件", str(src))

    if top_kind not in _TOP_KINDS:
        raise bad_param("top_kind", f"必须为 {_TOP_KINDS} 之一")
    if not dirname:
        raise bad_param("dirname", "不能为空（如 '资料'）")
    if "/" in dirname or "\\" in dirname or dirname in (".", ".."):
        raise bad_param("dirname", "只能是顶层下的单个子目录名")

    target_name = filename or src_path.name
    if "/" in target_name or "\\" in target_name:
        raise bad_param("filename", "只能是文件名，不能含路径分隔符")
    _check_extension(target_name)

    dest_dir = _safe_path(f"{top_kind}/{dirname}")
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / target_name

    archived = None
    if dest.exists():
        if archive:
            archived = fs_archive(f"{top_kind}/{dirname}/{target_name}").get("dst")
        elif dest.stat().st_size > 0:
            raise SkillError(
                "target_exists",
                f"{top_kind}/{dirname}/{target_name} 已存在且非空",
                status=409,
                hint="加 archive=true 备份旧文件后再导入，或换 filename。",
            )

    _shutil.copy2(src_path, dest)

    # 建立索引记录（role 可选；非课程目录的 role 允许为 None）
    rel = f"{top_kind}/{dirname}/{target_name}"
    role_key: str | None = None
    if role:
        if role in DIR_TO_ROLE:  # 中文
            role_key = DIR_TO_ROLE[role]
        elif role in ROLE_DIRS:  # 英文枚举
            role_key = role
        else:
            raise bad_param("role", f"必须为 7 个固定角色之一：{sorted(ROLE_DIRS.keys())}")
    cf = _scan_file_record(rel, None, top_kind, role_key)
    return {
        "imported": True,
        "src": str(src_path),
        "path": rel,
        "filename": target_name,
        "top_kind": top_kind,
        "dirname": dirname,
        "role": role_key,
        "size": dest.stat().st_size,
        "sha256": cf.sha256 if cf else _sha256_file(dest),
        "archived_old": archived,
    }


def fs_list_files(top_kind: str, dirname: str, subdir: str | None = None) -> list[dict]:
    """列出任意顶层子目录（含非课程目录）下的文件（D3）。

    与 course.list_files 的区别：不需要 course_id，也不限于 7 个角色目录。
    subdir 可选，用于再往下一层（如 `文件/规章制度/2025/`）。
    """
    if top_kind not in _TOP_KINDS:
        raise bad_param("top_kind", f"必须为 {_TOP_KINDS} 之一")
    rel_base = f"{top_kind}/{dirname}" if not subdir else f"{top_kind}/{dirname}/{subdir}"
    base = _safe_path(rel_base)
    if not base.exists() or not base.is_dir():
        raise not_found("目录", rel_base)
    out: list[dict] = []
    for p in sorted(base.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(_data_root()).as_posix()
        # 角色：从倒数第二层目录名反查（若恰好是 7 角色之一）
        role_key = None
        parent_name = p.parent.name
        if parent_name in DIR_TO_ROLE:
            role_key = DIR_TO_ROLE[parent_name]
        # 增量扫描写 DB
        cf = _scan_file_record(rel, None, top_kind, role_key)
        out.append(
            {
                "path": rel,
                "filename": p.name,
                "role": role_key,
                "top_kind": top_kind,
                "dirname": dirname,
                "size": p.stat().st_size,
                "sha256": cf.sha256 if cf else "",
                "mtime_ns": p.stat().st_mtime_ns,
            }
        )
    return out


def course_apply_period(course_id: str, force: bool = False) -> Course:
    """用学校时间表把节次填成 start_time/end_time。

    - 支持连排：period_nos=[3,4,5] → start = 第3节开始，end = 第5节结束
    - 支持多时段：sections 里每个时段各自按自己的 period_nos 填
    - 已填了时间则不覆盖；force=True 时强制重算（学校课表调整后用）
    """
    c = get_course(course_id)
    changed = False
    from datetime import time as _time

    def _apply(sec: Any, label: str) -> bool:
        nonlocal changed
        pnos = _section_period_nos(sec)
        if not pnos:
            return False
        if sec.start_time is not None and sec.end_time is not None and not force:
            return False
        slots = [(p, period_resolve(p)) for p in pnos]
        missing = [p for p, s in slots if s is None]
        if missing:
            raise SkillError(
                "period_not_in_schedule",
                f"{label} 节次 {missing} 不在学校时间表中",
                status=422,
                hint="先调用 school.get_schedule 确认节次，或显式传 start_time/end_time 覆盖。",
                extra={"missing_periods": missing},
            )
        resolved = [s for _, s in slots if s]
        sec.start_time = _time(int(min(s["start"] for s in resolved)[:2]),
                               int(min(s["start"] for s in resolved)[3:]))
        sec.end_time = _time(int(max(s["end"] for s in resolved)[:2]),
                             int(max(s["end"] for s in resolved)[3:]))
        changed = True
        return True

    # 1) 多时段：逐个 section 处理
    if c.sections:
        for i, sec in enumerate(c.sections, start=1):
            _apply(sec, f"第 {i} 个时段")
        if changed:
            c.updated_at = datetime.now()
            db.update_course(c)
        return c

    # 2) 扁平字段
    if c.period_no is None and not c.period_nos:
        return c
    if c.start_time is not None and c.end_time is not None and not force:
        return c  # 已显式填了，不覆盖
    pnos = list(c.period_nos) if c.period_nos else [c.period_no]
    slots = [(p, period_resolve(p)) for p in pnos]
    missing = [p for p, s in slots if s is None]
    if missing:
        raise SkillError(
            "period_not_in_schedule",
            f"节次 {missing} 不在学校时间表中",
            status=422,
            hint="先调用 school.get_schedule 确认节次，或显式传 start_time/end_time 覆盖。",
            extra={"missing_periods": missing},
        )
    resolved = [s for _, s in slots if s]
    earliest = min(s["start"] for s in resolved)
    latest = max(s["end"] for s in resolved)
    c.start_time = _time(int(earliest[:2]), int(earliest[3:]))
    c.end_time = _time(int(latest[:2]), int(latest[3:]))
    c.updated_at = datetime.now()
    db.update_course(c)
    return c


# ============== 阶段 ③：文件读写 + 历史 + 文本写入 ==============

import base64 as _base64

# 文本后缀（read 时直接返回字符串；二进制后缀 → base64）
_TEXT_EXTS = frozenset({".md", ".txt", ".csv"})
# 二进制后缀（write_text 拒绝）
_BINARY_EXTS = frozenset({".pdf", ".ppt", ".pptx", ".doc", ".docx", ".xls", ".xlsx",
                          ".png", ".jpg", ".jpeg", ".gif", ".webp", ".zip"})
# 单文件大小硬上限（读取与写入均生效）
_MAX_FILE_BYTES = 20 * 1024 * 1024


def _set_max_file_bytes(n: int) -> None:
    """仅供测试调用的临时阈值切换。生产代码不应使用。"""
    global _MAX_FILE_BYTES
    _MAX_FILE_BYTES = n


def _resolve_course_file(course_id: str, role: str, filename: str) -> tuple[_Path, str]:
    """根据 (course_id, role, filename) 解析为 (绝对路径, path_rel)。

    课程必须存在；role 必须是 7 个固定枚举之一；filename 经 _safe_path 兜底安全。
    role 同时接受中文（"大纲"）和英文枚举（"outline"）。
    """
    zh_set = set(ROLE_DIRS.values())
    en_set = set(ROLE_DIRS.keys())
    if role in zh_set:
        role_zh = role
    elif role in en_set:
        role_zh = ROLE_DIRS[role]
    else:
        raise bad_param("role", f"必须为 7 个固定角色之一：{sorted(zh_set)} 或 {sorted(en_set)}")
    c = get_course(course_id)
    bind = db.get_binding_by_course(course_id)
    if bind is None:
        raise not_found("课程绑定", f"课程 {course_id} 尚未绑定到任何文件夹")
    rel = f"{bind.top_kind}/{bind.dirname}/{role_zh}/{filename}"
    full = _safe_path(rel)
    return full, rel


def fs_read_file(course_id: str, role: str, filename: str) -> dict:
    """读取课程某个角色下的一个文件。

    - 文本后缀（.md/.txt/.csv）→ 返回 content 字符串 + encoding='utf-8'
    - 二进制后缀 → 返回 content_base64 + encoding='base64' + size
    - > 20MB → 拒绝（鼓励走归档/向量分块）
    - 文件不存在 → not_found

    返回字段：{course_id, role, path, filename, size, sha256, mtime_ns,
               encoding: 'utf-8'|'base64', content / content_base64}
    """
    if not filename:
        raise bad_param("filename", "不能为空")
    full, rel = _resolve_course_file(course_id, role, filename)
    if not full.exists() or not full.is_file():
        raise not_found("文件", rel)
    size = full.stat().st_size
    if size > _MAX_FILE_BYTES:
        raise SkillError(
            "file_too_large",
            f"{rel} 大小 {size}B 超过 20MB 上限",
            status=413,
            hint="大文件请用 fs.archive 备份后单独分发，不要全量灌给 LLM。",
            extra={"size": size, "max": _MAX_FILE_BYTES},
        )
    # 同步数据库（mtime 变了再重算 sha）
    bind = db.get_binding_by_course(course_id)
    role_key = DIR_TO_ROLE[full.parent.name]
    cf = _scan_file_record(rel, course_id, bind.top_kind, role_key)
    out = {
        "course_id": course_id,
        "role": role_key,
        "path": rel,
        "filename": full.name,
        "size": size,
        "sha256": cf.sha256 if cf else _sha256_file(full),
        "mtime_ns": full.stat().st_mtime_ns,
    }
    if full.suffix.lower() in _TEXT_EXTS:
        try:
            text = full.read_text(encoding="utf-8")
        except UnicodeDecodeError as e:
            raise SkillError(
                "decode_error",
                f"{rel} 不是有效 UTF-8 文本：{e}",
                status=422,
                hint="二进制文件请改用二进制读取（自动本任务）。",
            )
        out["encoding"] = "utf-8"
        out["content"] = text
    else:
        out["encoding"] = "base64"
        out["content_base64"] = _base64.b64encode(full.read_bytes()).decode("ascii")
    return out


def fs_file_history(course_id: str, role: str, filename: str) -> dict:
    """列出该文件所有版本（当前 + 历史）。

    返回：
        {course_id, role, filename, current: {...}|None,
         history: [{sha256, size, mtime_ns, created_at, superseded_at, replaced_by}, ...]}
    """
    if not filename:
        raise bad_param("filename", "不能为空")
    full, rel = _resolve_course_file(course_id, role, filename)
    bind = db.get_binding_by_course(course_id)
    role_key = DIR_TO_ROLE[full.parent.name]
    # 先扫描保证 current 行存在
    _scan_file_record(rel, course_id, bind.top_kind, role_key)
    rows = db.list_history(rel)
    current = next((r for r in rows if r.is_current), None)
    history = [
        {
            "sha256": r.sha256,
            "size": r.size,
            "mtime_ns": r.mtime_ns,
            "created_at": r.created_at.isoformat(timespec="seconds"),
            "superseded_at": r.superseded_at.isoformat(timespec="seconds") if r.superseded_at else None,
            "replaced_by": r.replaced_by,
            "is_current": r.is_current,
        }
        for r in rows
    ]
    return {
        "course_id": course_id,
        "role": role_key,
        "filename": full.name,
        "path": rel,
        "current": (
            {
                "sha256": current.sha256,
                "size": current.size,
                "mtime_ns": current.mtime_ns,
                "created_at": current.created_at.isoformat(timespec="seconds"),
            }
            if current else None
        ),
        "history": history,
        "versions": len(history),
    }


def fs_write_text(course_id: str, role: str, filename: str, text: str) -> dict:
    """向课程某个角色写入**文本**文件（硬覆盖）。

    - 二进制后缀一律拒绝（避免 LLM 误传 base64 当文本）
    - 文本大小上限同 read（20MB）
    - 写入前自动触发增量扫描（旧 sha 进 history，新 sha 变 current）
    - 文件所在目录不存在则自动创建
    """
    if not filename:
        raise bad_param("filename", "不能为空")
    if not isinstance(text, str):
        raise bad_param("text", "必须为字符串")
    if len(text.encode("utf-8")) > _MAX_FILE_BYTES:
        raise SkillError(
            "file_too_large",
            f"写入文本 {len(text)} 字符超过 20MB 编码上限",
            status=413,
            extra={"size": len(text.encode("utf-8")), "max": _MAX_FILE_BYTES},
        )
    full, rel = _resolve_course_file(course_id, role, filename)
    if full.suffix.lower() in _BINARY_EXTS:
        raise bad_param(
            "filename",
            f"{full.suffix} 是二进制后缀，不允许 fs.write_text 覆盖；如需替换请用操作系统",
        )
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_text(text, encoding="utf-8")
    # 同步数据库（upsert_file_current 会把旧 current 改 history）
    bind = db.get_binding_by_course(course_id)
    role_key = DIR_TO_ROLE[full.parent.name]
    cf = _scan_file_record(rel, course_id, bind.top_kind, role_key)
    return {
        "written": True,
        "course_id": course_id,
        "role": role_key,
        "path": rel,
        "filename": full.name,
        "size": full.stat().st_size,
        "sha256": cf.sha256 if cf else _sha256_file(full),
        "mtime_ns": full.stat().st_mtime_ns,
    }


# ============== 阶段 ④：大纲 + 进度结合 ==============

def course_set_outline_path(course_id: str, filename: str) -> Course:
    """设置课程的大纲文件名（如 outline.md）。文件必须落在绑定目录的"大纲"角色子目录里。"""
    if not filename:
        raise bad_param("filename", "不能为空")
    c = get_course(course_id)
    bind = db.get_binding_by_course(course_id)
    if bind is None:
        raise not_found("课程绑定", f"课程 {course_id} 尚未绑定到任何文件夹")
    rel = f"{bind.top_kind}/{bind.dirname}/大纲/{filename}"
    full = _safe_path(rel)
    if not full.exists() or not full.is_file():
        raise not_found("大纲文件", rel)
    c.outline_path = filename
    c.outline_cache = None  # 文件改了或首次设置 → 旧 cache 失效
    c.updated_at = datetime.now()
    db.update_course(c)
    return c


def course_get_outline(course_id: str) -> dict:
    """读取课程大纲的当前状态。

    返回：
        course_id, outline_path, has_file, file_size, file_mtime_ns,
        cache_present: bool   (outline_cache 是否非空)
        cache_mtime_ns: int|None  (缓存的 file_mtime_ns；用于判断 stale)
        stale: bool           (cache 与磁盘文件 mtime 不一致 → 建议重新解析)
        text: str|None        (若 file 可达且 .md/.txt/.csv → 原始文本；否则 None)
        cached: dict|None     (大模型解析后回填的结构化大纲)
    """
    c = get_course(course_id)
    bind = db.get_binding_by_course(course_id)
    has_file = False
    file_size = 0
    file_mtime_ns: Optional[int] = None
    text: Optional[str] = None
    if c.outline_path and bind is not None:
        rel = f"{bind.top_kind}/{bind.dirname}/大纲/{c.outline_path}"
        try:
            full = _safe_path(rel)
            if full.exists() and full.is_file():
                has_file = True
                st = full.stat()
                file_size = st.st_size
                file_mtime_ns = st.st_mtime_ns
                if full.suffix.lower() in _TEXT_EXTS:
                    try:
                        text = full.read_text(encoding="utf-8")
                    except UnicodeDecodeError:
                        text = None
        except Exception:
            pass
    cache = c.outline_cache or {}
    cache_mtime = cache.get("file_mtime_ns") if isinstance(cache, dict) else None
    stale = bool(
        has_file and c.outline_cache is not None and file_mtime_ns != cache_mtime
    )
    return {
        "course_id": course_id,
        "outline_path": c.outline_path,
        "has_file": has_file,
        "file_size": file_size,
        "file_mtime_ns": file_mtime_ns,
        "cache_present": c.outline_cache is not None,
        "cache_mtime_ns": cache_mtime,
        "stale": stale,
        "text": text,
        "cached": c.outline_cache,
    }


def _validate_outline_parsed(parsed: dict) -> None:
    """校验 refresh_outline 的 parsed 结构（C3）。

    规则：
    - parsed.weeks 必须是 list
    - 每项必须有 topics（非空 list of str）
    - week 若提供则 1..60；同一 week 不可重复
    """
    if not isinstance(parsed, dict):
        raise bad_param("parsed", "必须为对象")
    weeks = parsed.get("weeks")
    if weeks is None:
        raise bad_param("parsed.weeks", "缺失；应为 [{week:1, topics:[\"...\"]}, ...]")
    if not isinstance(weeks, list):
        raise bad_param("parsed.weeks", "必须为列表")
    if not weeks:
        raise bad_param("parsed.weeks", "不能为空")
    seen: set[int] = set()
    for i, wk in enumerate(weeks):
        if not isinstance(wk, dict):
            raise bad_param(f"parsed.weeks[{i}]", "必须为对象 {week, topics}")
        topics = wk.get("topics")
        if not topics or not isinstance(topics, list) or len(topics) == 0:
            raise bad_param(f"parsed.weeks[{i}].topics", "必须为非空列表")
        if not all(isinstance(t, str) and t.strip() for t in topics):
            raise bad_param(f"parsed.weeks[{i}].topics", "每项必须为非空字符串")
        w = wk.get("week")
        if w is not None:
            if not isinstance(w, int) or not (1 <= w <= 60):
                raise bad_param(f"parsed.weeks[{i}].week", "必须为 1..60 的整数")
            if w in seen:
                raise bad_param(f"parsed.weeks[{i}].week", f"周次 {w} 重复")
            seen.add(w)


def course_refresh_outline(course_id: str, parsed: dict) -> Course:
    """大模型把大纲解析完回填。parsed 形如：
        {weeks: [{week: 1, topics: ["极限", "连续"]}, ...], file_mtime_ns: 123456}

    Skill 不调 LLM，只把大模型产出存进 outline_cache，并把当前 file_mtime_ns 写入。
    下次 course.get_outline 用它判断 stale。
    """
    c = get_course(course_id)
    _validate_outline_parsed(parsed)
    # 注入当前文件 mtime（若 caller 没传）
    if "file_mtime_ns" not in parsed and c.outline_path:
        bind = db.get_binding_by_course(course_id)
        if bind is not None:
            rel = f"{bind.top_kind}/{bind.dirname}/大纲/{c.outline_path}"
            try:
                full = _safe_path(rel)
                if full.exists() and full.is_file():
                    parsed["file_mtime_ns"] = full.stat().st_mtime_ns
            except Exception:
                pass
    c.outline_cache = parsed
    c.updated_at = datetime.now()
    db.update_course(c)
    return c


def course_suggest_outline(course_id: str, topics: list[str], weeks_total: int | None = None) -> dict:
    """给大模型一个"周次映射建议"（C3 的 --dry-run）。

    输入：课程 + 平铺的章节列表（["第1章 极限", "第2章 连续", ...]）
    输出：按教学周均匀分配的映射建议（不落库，确认后再调 refresh_outline）

    启发式：
    - 教学周数优先取 course 的实际学期长度（有 semester_start/end 就按周一到期末算），
      否则取 weeks_total 参数，否则按 topics 数量 1:1
    - 章节按顺序均匀摊到这些教学周里；不足则部分周多章，多余的章留到最后一并给出提醒
    """
    c = get_course(course_id)
    if not topics or not isinstance(topics, list):
        raise bad_param("topics", "必须为非空字符串列表")

    # 1) 决定教学周数
    total_weeks: Optional[int] = weeks_total
    if total_weeks is None and c.semester_start is not None:
        end = c.semester_end
        if end is None:
            today = date.today()
            end = min(today, c.semester_start + timedelta(weeks=30))
        if end >= c.semester_start:
            total_weeks = ((end - c.semester_start).days // 7) + 1
    if not total_weeks or total_weeks < 1:
        total_weeks = len(topics)

    # 2) 均匀摊派
    n = len(topics)
    per_week = max(1, -(-n // total_weeks))  # ceil
    mapping: list[dict] = []
    for i, t in enumerate(topics):
        wk = min(total_weeks, (i // per_week) + 1)
        if wk == total_weeks and mapping and mapping[-1]["week"] == wk:
            mapping[-1]["topics"].append(str(t))
        else:
            mapping.append({"week": wk, "topics": [str(t)]})

    return {
        "course_id": course_id,
        "topics_count": n,
        "weeks_total": total_weeks,
        "weeks_used": len(mapping),
        "chapters_per_week": per_week,
        "suggestion": mapping,
        "note": (
            "这是按'均匀分配'给出的建议，仅供参考。"
            "确认或调整后，用 course.refresh_outline 落库"
            "（parsed={'weeks': <调整后的 mapping>}）。"
        ),
        "dry_run": True,
    }


def course_next_topic(course_id: str, target_date: date | None = None) -> dict:
    """根据大纲 + ProgressEntry，给出"target_date 应该讲到哪里"的事实卡。

    返回字段：
        course_id, target_date, current_week, target_week,
        outline_target: str|None   (大纲中 target_week 的 topics 拼接)
        recent_topic: str|None     (最后一次 ProgressEntry.topic)
        lag_weeks: int             (大纲周次 - 已讲周次；>0 表示落后)
        lesson_date: date|None     (target_date 当周是否有课；None 表示没课)
        hint: str
    """
    c = get_course(course_id)
    target_date = target_date or date.today()
    progress = list_progress(course_id)
    progress.sort(key=lambda p: p.lesson_date, reverse=True)
    last = progress[0] if progress else None

    # target_week
    target_week: Optional[int] = None
    if c.semester_start is not None:
        delta_days = (target_date - c.semester_start).days
        if delta_days >= 0:
            target_week = delta_days // 7 + 1

    # 命中检查：target_date 当天是否应上课（依 weekday / week_pattern / semester_end）
    lesson_date: Optional[date] = None
    if c.weekday is not None:
        occs = _week_occurrences(c, target_date, target_date)
        if occs:
            lesson_date = occs[0][0]

    # 找 outline_cache 里 target_week 的 topics
    outline_target_topics: Optional[str] = None
    if isinstance(c.outline_cache, dict):
        for wk in c.outline_cache.get("weeks", []) or []:
            if not isinstance(wk, dict):
                continue
            if wk.get("week") == target_week:
                topics = wk.get("topics") or []
                outline_target_topics = "\n".join(str(t) for t in topics) if topics else None

    # ---- C2 修复：current_week 优先用 ProgressEntry.outline_week ----
    # 语义：outline_week = "这次课讲的是大纲里的第几周"（教师/LLM 显式记录）
    #       回退：lesson_date 反推教学周序号（只看日期，不知道讲到第几章）
    # 注意：这段不依赖 outline_cache 是否存在（修复原 bug）
    last_done_week: Optional[int] = None
    current_week_source: Optional[str] = None
    if last is not None:
        if last.outline_week is not None:
            last_done_week = last.outline_week
            current_week_source = "progress.outline_week"
        elif c.semester_start is not None:
            last_delta = (last.lesson_date - c.semester_start).days
            if last_delta >= 0:
                last_done_week = last_delta // 7 + 1
                current_week_source = "lesson_date"
    else:
        current_week_source = None

    # ---- 计算"落后/超前" ----
    # C1 修复：last_done_week 为 None（尚无进度记录）时 lag_weeks 返回 None，
    #          而非退化成 target_week - 0 = target_week（读起来像"落后 N 周"）。
    if target_week is None:
        lag_weeks = None
    elif last_done_week is None:
        lag_weeks = None  # 尚无进度记录，无法计算落后
    else:
        lag_weeks = target_week - last_done_week

    if target_week is None:
        hint = "课程未设置 semester_start，无法推算周次；请先用 course.update 补 semester_start。"
    elif last_done_week is None:
        hint = (
            "尚无进度记录，无法计算落后周数。"
            f"大纲建议本周（第 {target_week} 周）讲：{outline_target_topics or '（大纲未提供该周内容）'}"
        )
    elif lag_weeks == 0:
        hint = "大纲周次与已讲一致，无需补进度。"
    elif lag_weeks > 0:
        hint = f"大纲本周应讲第 {target_week} 周，已讲到第 {last_done_week} 周，落后 {lag_weeks} 周。"
    elif lag_weeks < 0:
        hint = f"已讲到第 {last_done_week} 周，大纲本周才到第 {target_week} 周，超前 {-lag_weeks} 周。"
    else:
        hint = "无明确周次差异。"

    return {
        "course_id": course_id,
        "target_date": target_date.isoformat(),
        "target_week": target_week,
        "current_week": last_done_week,
        "current_week_source": current_week_source,
        "outline_target": outline_target_topics,
        "recent_topic": last.topic if last is not None else None,
        "recent_lesson_date": last.lesson_date.isoformat() if last is not None else None,
        "lag_weeks": lag_weeks,
        "lesson_date": lesson_date.isoformat() if lesson_date else None,
        "hint": hint,
    }
