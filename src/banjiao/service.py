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


def _week_occurrences(course: Course, start: date, end: date) -> list[tuple[date, int]]:
    """返回课程在 [start, end] 内的 (上课日, 第几周) 列表。

    - 无 weekday：返回空（一次性课程？用进度记录自行管理 lesson_date）。
    - 有 weekday + 有 semester_start：按 weekday 计算周偏移。
      然后用 week_pattern / weeks 过滤：
        * "all"   → 所有周
        * "odd"   → 仅奇数周
        * "even"  → 仅偶数周
        * "custom"→ 仅 weeks 列表里的周次
    - 有 weekday + 无 semester_start：按 weekday 推到 start-end 范围，周次固定为 1。
    """
    if course.weekday is None:
        return []
    weeks_filter: Optional[set[int]] = None
    if course.week_pattern == "custom":
        weeks_filter = set(course.weeks or [])
    elif course.week_pattern == "odd":
        weeks_filter = None  # 走下方奇偶逻辑
    elif course.week_pattern == "even":
        weeks_filter = None
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
                # week_pattern 过滤
                if course.week_pattern == "odd" and week_no % 2 == 0:
                    week_no = None
                elif course.week_pattern == "even" and week_no % 2 == 1:
                    week_no = None
                elif course.week_pattern == "custom":
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
    """返回当前生效的学校课程时间表。优先 data/_school_schedule.json，否则默认。"""
    p = _schedule_path()
    if p.exists():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            _validate_schedule(data)
            return data
        except Exception:
            pass  # 损坏就回退默认（绝不抛）
    return DEFAULT_SCHOOL_SCHEDULE


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
                return it if False else {"no": it["no"], "start": it["start"], "end": it["end"]}
    return None


def course_apply_period(course_id: str) -> Course:
    """若 Course.period_no 有值且 start_time/end_time 为空，则从学校时间表填充。"""
    c = get_course(course_id)
    if c.period_no is None:
        return c
    if c.start_time is not None and c.end_time is not None:
        return c  # 已显式填了，不覆盖
    slot = period_resolve(c.period_no)
    if slot is None:
        raise SkillError(
            "period_not_in_schedule",
            f"节次 {c.period_no} 不在学校时间表中",
            status=422,
            hint="先调用 school.get_schedule 确认节次，或显式传 start_time/end_time 覆盖。",
        )
    from datetime import time as _time
    c.start_time = _time(int(slot["start"][:2]), int(slot["start"][3:]))
    c.end_time = _time(int(slot["end"][:2]), int(slot["end"][3:]))
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
