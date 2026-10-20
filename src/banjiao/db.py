"""伴教 SQLite 持久化层。

特点：
- 单机、单老师、无需认证
- 单连接 + RLock（事务化操作可自嵌套调用）
- WAL + synchronous=FULL
- 启动时自动建表（幂等）
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import date, datetime
from pathlib import Path

from .config import settings
from .models import Affair, Course, CourseBinding, CourseFile, Document, Homework, ProgressEntry


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS courses (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    class_group     TEXT,
    location        TEXT,
    semester_start  TEXT,
    semester_end    TEXT,
    start_time      TEXT,
    end_time        TEXT,
    weekday         INTEGER,
    week_pattern    TEXT NOT NULL DEFAULT 'all',
    weeks           TEXT NOT NULL DEFAULT '[]',
    weeks_range     TEXT,
    period_no       INTEGER,
    period_nos      TEXT,
    sections        TEXT,
    outline_path    TEXT,
    outline_cache   TEXT,
    notes           TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_courses_weekday ON courses(weekday);
CREATE INDEX IF NOT EXISTS idx_courses_semester ON courses(semester_start, semester_end);

CREATE TABLE IF NOT EXISTS progress (
    id          TEXT PRIMARY KEY,
    course_id   TEXT NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
    lesson_date TEXT NOT NULL,
    topic       TEXT NOT NULL,
    outline_week INTEGER,
    outline     TEXT,
    completed   INTEGER NOT NULL DEFAULT 0,
    notes       TEXT,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_progress_course ON progress(course_id);
CREATE INDEX IF NOT EXISTS idx_progress_date ON progress(lesson_date);

CREATE TABLE IF NOT EXISTS homework (
    id                  TEXT PRIMARY KEY,
    title               TEXT NOT NULL,
    course_id           TEXT REFERENCES courses(id) ON DELETE SET NULL,
    class_group         TEXT,
    description         TEXT,
    link                TEXT,
    published_at        TEXT NOT NULL,
    due_at              TEXT,
    remind_before_days  INTEGER NOT NULL DEFAULT 1,
    status              TEXT NOT NULL DEFAULT 'pending',
    submitted_at        TEXT,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_homework_due ON homework(due_at);
CREATE INDEX IF NOT EXISTS idx_homework_status ON homework(status);

CREATE TABLE IF NOT EXISTS affairs (
    id                    TEXT PRIMARY KEY,
    title                 TEXT NOT NULL,
    kind                  TEXT NOT NULL DEFAULT 'other',
    description           TEXT,
    due_at                TEXT,
    remind_before_minutes INTEGER NOT NULL DEFAULT 60,
    reminded              INTEGER NOT NULL DEFAULT 0,
    created_at            TEXT NOT NULL,
    updated_at            TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_affairs_due ON affairs(due_at);

CREATE TABLE IF NOT EXISTS documents (
    id           TEXT PRIMARY KEY,
    title        TEXT NOT NULL,
    path         TEXT NOT NULL,
    course_id    TEXT REFERENCES courses(id) ON DELETE SET NULL,
    indexed      INTEGER NOT NULL DEFAULT 0,
    chunk_count  INTEGER NOT NULL DEFAULT 0,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS course_bindings (
    id          TEXT PRIMARY KEY,
    top_kind    TEXT NOT NULL,
    dirname     TEXT NOT NULL,
    course_id   TEXT REFERENCES courses(id) ON DELETE SET NULL,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_bindings_top_dir ON course_bindings(top_kind, dirname);

CREATE TABLE IF NOT EXISTS course_files (
    id              TEXT PRIMARY KEY,
    course_id       TEXT REFERENCES courses(id) ON DELETE SET NULL,
    top_kind        TEXT,
    role            TEXT,
    path_rel        TEXT NOT NULL,
    filename        TEXT NOT NULL,
    sha256          TEXT NOT NULL,
    size            INTEGER NOT NULL,
    mtime_ns        INTEGER NOT NULL,
    is_current      INTEGER NOT NULL DEFAULT 1,
    superseded_at   TEXT,
    replaced_by     TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_files_current ON course_files(path_rel, is_current);
CREATE INDEX IF NOT EXISTS idx_files_sha ON course_files(sha256);
CREATE INDEX IF NOT EXISTS idx_files_course ON course_files(course_id);
"""



# ---------- 行映射 ----------

def _course_row(c: Course) -> dict:
    return {
        **c.model_dump(mode="json"),
        "weeks": json.dumps(c.weeks or [], ensure_ascii=False),
        "weeks_range": json.dumps(c.weeks_range, ensure_ascii=False) if c.weeks_range else None,
        "period_nos": json.dumps(c.period_nos, ensure_ascii=False) if c.period_nos else None,
        "sections": json.dumps(
            [s.model_dump(mode="json") for s in c.sections], ensure_ascii=False
        ) if c.sections else None,
        "outline_cache": json.dumps(c.outline_cache, ensure_ascii=False) if c.outline_cache is not None else None,
    }


def _row_to_course(row: sqlite3.Row) -> Course:
    d = dict(row)
    d["weeks"] = json.loads(d.get("weeks") or "[]")
    # weeks_range / period_nos / sections：JSON 文本 → Python 对象
    for key in ("weeks_range", "period_nos"):
        raw = d.get(key)
        if raw:
            try:
                d[key] = json.loads(raw)
            except Exception:
                d[key] = None
        else:
            d[key] = None
    raw_sections = d.get("sections")
    if raw_sections:
        try:
            d["sections"] = json.loads(raw_sections)
        except Exception:
            d["sections"] = None
    else:
        d["sections"] = None
    raw_cache = d.get("outline_cache")
    if raw_cache:
        try:
            d["outline_cache"] = json.loads(raw_cache)
        except Exception:
            d["outline_cache"] = None
    else:
        d["outline_cache"] = None
    return Course.model_validate(d)


def _progress_row(p: ProgressEntry) -> dict:
    return p.model_dump(mode="json")


def _row_to_progress(row: sqlite3.Row) -> ProgressEntry:
    return ProgressEntry.model_validate(dict(row))


def _homework_row(h: Homework) -> dict:
    return h.model_dump(mode="json")


def _row_to_homework(row: sqlite3.Row) -> Homework:
    return Homework.model_validate(dict(row))


def _affair_row(a: Affair) -> dict:
    return a.model_dump(mode="json")


def _row_to_affair(row: sqlite3.Row) -> Affair:
    return Affair.model_validate(dict(row))


def _document_row(d: Document) -> dict:
    return d.model_dump(mode="json")


def _row_to_document(row: sqlite3.Row) -> Document:
    return Document.model_validate(dict(row))


def _course_file_row(f: CourseFile) -> dict:
    d = f.model_dump(mode="json")
    return d


def _row_to_course_file(row: sqlite3.Row) -> CourseFile:
    return CourseFile.model_validate(dict(row))


def _now_iso() -> str:
    """统一时间戳（ISO 字符串），用于 superseded_at 等字段。"""
    from datetime import datetime as _dt
    return _dt.now().isoformat(timespec="seconds")


def _binding_row(b: CourseBinding) -> dict:
    return b.model_dump(mode="json")


def _row_to_binding(row: sqlite3.Row) -> CourseBinding:
    return CourseBinding.model_validate(dict(row))


# ---------- DB 类 ----------

class DB:
    def __init__(self):
        self.db_path: Path = settings.data_dir / "banjiao.db"
        # RLock 而非 Lock：让事务化操作内部嵌套调用也安全
        self._lock = threading.RLock()
        self._conn: sqlite3.Connection | None = None
        self._init_db()

    def _init_db(self):
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.executescript("PRAGMA journal_mode=WAL; PRAGMA synchronous=FULL;")
        conn.executescript(SCHEMA_SQL)
        conn.commit()
        self._conn = conn

    def _query_rows(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            return list(self._conn.execute(sql, params))

    def _mutate(self, sql: str, params: dict | tuple) -> None:
        with self._lock:
            with self._conn:
                self._conn.execute(sql, params)

    # ---- 课程 ----
    def create_course(self, c: Course) -> Course:
        self._mutate(
            "INSERT INTO courses (id,name,class_group,location,semester_start,semester_end,"
            "start_time,end_time,weekday,week_pattern,weeks,weeks_range,period_no,period_nos,"
            "sections,outline_path,outline_cache,notes,created_at,updated_at) "
            "VALUES (:id,:name,:class_group,:location,:semester_start,:semester_end,"
            ":start_time,:end_time,:weekday,:week_pattern,:weeks,:weeks_range,:period_no,:period_nos,"
            ":sections,:outline_path,:outline_cache,:notes,:created_at,:updated_at)",
            _course_row(c),
        )
        return c

    def get_course(self, cid: str) -> Course | None:
        rows = self._query_rows("SELECT * FROM courses WHERE id=?", (cid,))
        return _row_to_course(rows[0]) if rows else None

    def list_courses(self) -> list[Course]:
        return [
            _row_to_course(r)
            for r in self._query_rows("SELECT * FROM courses ORDER BY created_at")
        ]

    def update_course(self, c: Course) -> Course:
        self._mutate(
            "UPDATE courses SET name=:name, class_group=:class_group, location=:location, "
            "semester_start=:semester_start, semester_end=:semester_end, "
            "start_time=:start_time, end_time=:end_time, weekday=:weekday, "
            "week_pattern=:week_pattern, weeks=:weeks, weeks_range=:weeks_range, "
            "period_no=:period_no, period_nos=:period_nos, sections=:sections, "
            "outline_path=:outline_path, outline_cache=:outline_cache, "
            "notes=:notes, updated_at=:updated_at WHERE id=:id",
            _course_row(c),
        )
        return c

    def delete_course(self, cid: str) -> bool:
        rows = self._query_rows("SELECT id FROM courses WHERE id=?", (cid,))
        if not rows:
            return False
        self._mutate("DELETE FROM courses WHERE id=?", (cid,))
        return True

    # ---- 进度 ----
    def create_progress(self, p: ProgressEntry) -> ProgressEntry:
        self._mutate(
            "INSERT INTO progress (id,course_id,lesson_date,topic,outline_week,outline,"
            "completed,notes,created_at,updated_at) "
            "VALUES (:id,:course_id,:lesson_date,:topic,:outline_week,:outline,"
            ":completed,:notes,:created_at,:updated_at)",
            _progress_row(p),
        )
        return p

    def get_progress(self, pid: str) -> ProgressEntry | None:
        rows = self._query_rows("SELECT * FROM progress WHERE id=?", (pid,))
        return _row_to_progress(rows[0]) if rows else None

    def list_progress(self, course_id: str | None = None) -> list[ProgressEntry]:
        if course_id:
            rows = self._query_rows(
                "SELECT * FROM progress WHERE course_id=? ORDER BY lesson_date DESC, created_at",
                (course_id,),
            )
        else:
            rows = self._query_rows(
                "SELECT * FROM progress ORDER BY lesson_date DESC, created_at"
            )
        return [_row_to_progress(r) for r in rows]

    def update_progress(self, p: ProgressEntry) -> ProgressEntry:
        self._mutate(
            "UPDATE progress SET course_id=:course_id, lesson_date=:lesson_date, "
            "topic=:topic, outline_week=:outline_week, outline=:outline, "
            "completed=:completed, notes=:notes, "
            "updated_at=:updated_at WHERE id=:id",
            _progress_row(p),
        )
        return p

    def delete_progress(self, pid: str) -> bool:
        rows = self._query_rows("SELECT id FROM progress WHERE id=?", (pid,))
        if not rows:
            return False
        self._mutate("DELETE FROM progress WHERE id=?", (pid,))
        return True

    # ---- 作业 ----
    def create_homework(self, h: Homework) -> Homework:
        self._mutate(
            "INSERT INTO homework (id,title,course_id,class_group,description,link,"
            "published_at,due_at,remind_before_days,status,submitted_at,created_at,updated_at) "
            "VALUES (:id,:title,:course_id,:class_group,:description,:link,"
            ":published_at,:due_at,:remind_before_days,:status,:submitted_at,:created_at,:updated_at)",
            _homework_row(h),
        )
        return h

    def get_homework(self, hid: str) -> Homework | None:
        rows = self._query_rows("SELECT * FROM homework WHERE id=?", (hid,))
        return _row_to_homework(rows[0]) if rows else None

    def list_homework(
        self,
        status: str | None = None,
        only_pending: bool = False,
        overdue: bool = False,
    ) -> list[Homework]:
        sql = "SELECT * FROM homework WHERE 1=1"
        params: list = []
        if status:
            sql += " AND status=?"
            params.append(status)
        if only_pending:
            sql += " AND status='pending'"
        if overdue:
            sql += " AND status='pending' AND due_at IS NOT NULL AND due_at < ?"
            params.append(date.today().isoformat())
        sql += " ORDER BY due_at, published_at"
        return [_row_to_homework(r) for r in self._query_rows(sql, tuple(params))]

    def update_homework(self, h: Homework) -> Homework:
        self._mutate(
            "UPDATE homework SET title=:title, course_id=:course_id, class_group=:class_group, "
            "description=:description, link=:link, published_at=:published_at, due_at=:due_at, "
            "remind_before_days=:remind_before_days, status=:status, submitted_at=:submitted_at, "
            "updated_at=:updated_at WHERE id=:id",
            _homework_row(h),
        )
        return h

    def delete_homework(self, hid: str) -> bool:
        rows = self._query_rows("SELECT id FROM homework WHERE id=?", (hid,))
        if not rows:
            return False
        self._mutate("DELETE FROM homework WHERE id=?", (hid,))
        return True

    # ---- 事务 ----
    def create_affair(self, a: Affair) -> Affair:
        self._mutate(
            "INSERT INTO affairs (id,title,kind,description,due_at,remind_before_minutes,"
            "reminded,created_at,updated_at) VALUES (:id,:title,:kind,:description,:due_at,"
            ":remind_before_minutes,:reminded,:created_at,:updated_at)",
            _affair_row(a),
        )
        return a

    def get_affair(self, aid: str) -> Affair | None:
        rows = self._query_rows("SELECT * FROM affairs WHERE id=?", (aid,))
        return _row_to_affair(rows[0]) if rows else None

    def list_affairs(self) -> list[Affair]:
        return [
            _row_to_affair(r)
            for r in self._query_rows("SELECT * FROM affairs ORDER BY due_at")
        ]

    def update_affair(self, a: Affair) -> Affair:
        self._mutate(
            "UPDATE affairs SET title=:title, kind=:kind, description=:description, "
            "due_at=:due_at, remind_before_minutes=:remind_before_minutes, reminded=:reminded, "
            "updated_at=:updated_at WHERE id=:id",
            _affair_row(a),
        )
        return a

    def delete_affair(self, aid: str) -> bool:
        rows = self._query_rows("SELECT id FROM affairs WHERE id=?", (aid,))
        if not rows:
            return False
        self._mutate("DELETE FROM affairs WHERE id=?", (aid,))
        return True

    # ---- 文档 ----
    def create_document(self, d: Document) -> Document:
        self._mutate(
            "INSERT INTO documents (id,title,path,course_id,indexed,chunk_count,created_at,updated_at) "
            "VALUES (:id,:title,:path,:course_id,:indexed,:chunk_count,:created_at,:updated_at)",
            _document_row(d),
        )
        return d

    def get_document(self, did: str) -> Document | None:
        rows = self._query_rows("SELECT * FROM documents WHERE id=?", (did,))
        return _row_to_document(rows[0]) if rows else None

    def list_documents(self) -> list[Document]:
        return [_row_to_document(r) for r in self._query_rows("SELECT * FROM documents")]

    def update_document(self, d: Document) -> Document:
        self._mutate(
            "UPDATE documents SET title=:title, path=:path, course_id=:course_id, "
            "indexed=:indexed, chunk_count=:chunk_count, updated_at=:updated_at WHERE id=:id",
            _document_row(d),
        )
        return d

    def delete_document(self, did: str) -> bool:
        rows = self._query_rows("SELECT id FROM documents WHERE id=?", (did,))
        if not rows:
            return False
        self._mutate("DELETE FROM documents WHERE id=?", (did,))
        return True

    # ---- course_bindings ----
    def create_binding(self, b: CourseBinding) -> CourseBinding:
        try:
            self._mutate(
                "INSERT INTO course_bindings (id,top_kind,dirname,course_id,created_at,updated_at) "
                "VALUES (:id,:top_kind,:dirname,:course_id,:created_at,:updated_at)",
                _binding_row(b),
            )
        except Exception as e:
            from sqlite3 import IntegrityError as _I
            if isinstance(e, _I):
                raise ValueError(f"({b.top_kind}, {b.dirname}) 已存在绑定") from e
            raise
        return b

    def get_binding_by_dir(self, top_kind: str, dirname: str) -> CourseBinding | None:
        rows = self._query_rows(
            "SELECT * FROM course_bindings WHERE top_kind=? AND dirname=?",
            (top_kind, dirname),
        )
        return _row_to_binding(rows[0]) if rows else None

    def get_binding(self, bid: str) -> CourseBinding | None:
        rows = self._query_rows("SELECT * FROM course_bindings WHERE id=?", (bid,))
        return _row_to_binding(rows[0]) if rows else None

    def list_bindings(self) -> list[CourseBinding]:
        return [
            _row_to_binding(r)
            for r in self._query_rows("SELECT * FROM course_bindings ORDER BY top_kind, dirname")
        ]

    def get_binding_by_course(self, course_id: str) -> CourseBinding | None:
        rows = self._query_rows(
            "SELECT * FROM course_bindings WHERE course_id=?",
            (course_id,),
        )
        return _row_to_binding(rows[0]) if rows else None

    def delete_binding(self, bid: str) -> bool:
        rows = self._query_rows("SELECT id FROM course_bindings WHERE id=?", (bid,))
        if not rows:
            return False
        self._mutate("DELETE FROM course_bindings WHERE id=?", (bid,))
        return True

    # ---- course_files（清单 + 历史版本）----
    def upsert_file_current(self, f: CourseFile) -> CourseFile:
        """把当前文件版本写入：旧当前版本改为历史（is_current=0）。"""
        now_text = _now_iso()
        # 1) 找出该 path_rel 的当前行
        existing = self._query_rows(
            "SELECT * FROM course_files WHERE path_rel=? AND is_current=1",
            (f.path_rel,),
        )
        if existing:
            old_id = existing[0]["id"]
            old_sha = existing[0]["sha256"]
            new_id = f.id
            self._mutate(
                "UPDATE course_files SET is_current=0, superseded_at=?, replaced_by=?, updated_at=? "
                "WHERE id=?",
                (now_text, new_id, now_text, old_id),
            )
            # 如果旧 sha 与新 sha 相同（用户实际上重写了相同内容）则合并
            if old_sha == f.sha256:
                # 直接删旧的，把新的 is_current=1
                self._mutate("DELETE FROM course_files WHERE id=?", (old_id,))
                self._mutate(
                    "INSERT INTO course_files "
                    "(id,course_id,top_kind,role,path_rel,filename,sha256,size,mtime_ns,"
                    "is_current,superseded_at,replaced_by,created_at,updated_at) "
                    "VALUES (:id,:course_id,:top_kind,:role,:path_rel,:filename,:sha256,"
                    ":size,:mtime_ns,1,NULL,NULL,:created_at,:updated_at)",
                    _course_file_row(f),
                )
            else:
                # 历史保留：插入新行（is_current=1）
                self._mutate(
                    "INSERT INTO course_files "
                    "(id,course_id,top_kind,role,path_rel,filename,sha256,size,mtime_ns,"
                    "is_current,superseded_at,replaced_by,created_at,updated_at) "
                    "VALUES (:id,:course_id,:top_kind,:role,:path_rel,:filename,:sha256,"
                    ":size,:mtime_ns,1,NULL,NULL,:created_at,:updated_at)",
                    _course_file_row(f),
                )
        else:
            # 没有旧当前行：直接插
            self._mutate(
                "INSERT INTO course_files "
                "(id,course_id,top_kind,role,path_rel,filename,sha256,size,mtime_ns,"
                "is_current,superseded_at,replaced_by,created_at,updated_at) "
                "VALUES (:id,:course_id,:top_kind,:role,:path_rel,:filename,:sha256,"
                ":size,:mtime_ns,1,NULL,NULL,:created_at,:updated_at)",
                _course_file_row(f),
            )
        return f

    def get_file_current(self, path_rel: str) -> CourseFile | None:
        rows = self._query_rows(
            "SELECT * FROM course_files WHERE path_rel=? AND is_current=1",
            (path_rel,),
        )
        return _row_to_course_file(rows[0]) if rows else None

    def get_file_by_sha(self, sha256: str) -> CourseFile | None:
        rows = self._query_rows(
            "SELECT * FROM course_files WHERE sha256=? AND is_current=1",
            (sha256,),
        )
        return _row_to_course_file(rows[0]) if rows else None

    def list_files_current(
        self,
        course_id: str | None = None,
        role: str | None = None,
    ) -> list[CourseFile]:
        sql = "SELECT * FROM course_files WHERE is_current=1"
        params: list = []
        if course_id is not None:
            sql += " AND course_id=?"
            params.append(course_id)
        if role is not None:
            sql += " AND role=?"
            params.append(role)
        sql += " ORDER BY path_rel"
        return [_row_to_course_file(r) for r in self._query_rows(sql, tuple(params))]

    def list_history(self, path_rel: str) -> list[CourseFile]:
        rows = self._query_rows(
            "SELECT * FROM course_files WHERE path_rel=? ORDER BY created_at DESC",
            (path_rel,),
        )
        return [_row_to_course_file(r) for r in rows]

    def delete_files_for_path(self, path_rel: str) -> int:
        rows = self._query_rows(
            "SELECT id FROM course_files WHERE path_rel=?",
            (path_rel,),
        )
        for r in rows:
            self._mutate("DELETE FROM course_files WHERE id=?", (r["id"],))
        return len(rows)


db = DB()
