"""伴教数据模型（Pydantic）。

设计取舍：
- 单教师、无账号体系 → 没有 User / 鉴权字段
- 课程用 weekday + weeks + semester 字段表达"周次表"（不是通用 RRULE）
- 作业/事务/进度各自独立小表
- 字段约束（min_length/max_length/pattern）在 Create / Update 模型上一致
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, time
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator


# ============== 工具 ==============

def _new_id() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now()


# ============== 重复规则（课程专用，本项目当前未直接使用，保留供扩展） ==============

class Recurrence(BaseModel):
    """课程/事务的重复规则。沿用 RRULE 简化语义。"""

    freq: str = Field(pattern="^(daily|weekly|monthly|yearly)$")
    interval: int = Field(default=1, ge=1, le=365)
    by_weekday: Optional[list[int]] = None
    until: Optional[date] = None

    @model_validator(mode="after")
    def _check(self):
        if self.by_weekday is not None:
            if not all(0 <= w <= 6 for w in self.by_weekday):
                raise ValueError("by_weekday 取值必须为 0-6（周一=0）")
            if self.freq != "weekly":
                raise ValueError("by_weekday 仅用于 weekly 规则")
        return self


# ============== 课程 ==============

class Course(BaseModel):
    """课程基本信息 + 周次表。"""

    id: str = Field(default_factory=_new_id)
    name: str = Field(min_length=1, max_length=100)
    class_group: Optional[str] = Field(default=None, max_length=100)
    location: Optional[str] = Field(default=None, max_length=100)
    semester_start: Optional[date] = None
    semester_end: Optional[date] = None
    start_time: Optional[time] = None
    end_time: Optional[time] = None
    weekday: Optional[int] = Field(default=None, ge=0, le=6)
    weeks: Optional[list[int]] = None
    notes: Optional[str] = Field(default=None, max_length=2000)
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)

    @model_validator(mode="after")
    def _check(self):
        if self.start_time and self.end_time and self.end_time < self.start_time:
            raise ValueError("结束时间不能早于开始时间")
        if self.weeks is not None and (not all(1 <= w <= 60 for w in self.weeks)):
            raise ValueError("weeks 元素必须在 1..60 之间")
        if (
            self.semester_start is not None
            and self.semester_end is not None
            and self.semester_end < self.semester_start
        ):
            raise ValueError("semester_end 不能早于 semester_start")
        return self


class CourseCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    class_group: Optional[str] = Field(default=None, max_length=100)
    location: Optional[str] = Field(default=None, max_length=100)
    semester_start: Optional[date] = None
    semester_end: Optional[date] = None
    start_time: Optional[time] = None
    end_time: Optional[time] = None
    weekday: Optional[int] = Field(default=None, ge=0, le=6)
    weeks: Optional[list[int]] = None
    notes: Optional[str] = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def _check(self):
        if self.start_time and self.end_time and self.end_time < self.start_time:
            raise ValueError("结束时间不能早于开始时间")
        if self.weeks is not None and (not all(1 <= w <= 60 for w in self.weeks)):
            raise ValueError("weeks 元素必须在 1..60 之间")
        if (
            self.semester_start is not None
            and self.semester_end is not None
            and self.semester_end < self.semester_start
        ):
            raise ValueError("semester_end 不能早于 semester_start")
        return self


class CourseUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=100)
    class_group: Optional[str] = Field(default=None, max_length=100)
    location: Optional[str] = Field(default=None, max_length=100)
    semester_start: Optional[date] = None
    semester_end: Optional[date] = None
    start_time: Optional[time] = None
    end_time: Optional[time] = None
    weekday: Optional[int] = Field(default=None, ge=0, le=6)
    weeks: Optional[list[int]] = None
    notes: Optional[str] = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def _check(self):
        if self.start_time and self.end_time and self.end_time < self.start_time:
            raise ValueError("结束时间不能早于开始时间")
        if self.weeks is not None and (not all(1 <= w <= 60 for w in self.weeks)):
            raise ValueError("weeks 元素必须在 1..60 之间")
        if (
            self.semester_start is not None
            and self.semester_end is not None
            and self.semester_end < self.semester_start
        ):
            raise ValueError("semester_end 不能早于 semester_start")
        return self


# ============== 课程进度 ==============

class ProgressEntry(BaseModel):
    """某次课的实际进度记录。"""

    id: str = Field(default_factory=_new_id)
    course_id: str
    lesson_date: date
    topic: str = Field(min_length=1, max_length=200)
    outline: Optional[str] = Field(default=None, max_length=2000)
    completed: bool = False
    notes: Optional[str] = Field(default=None, max_length=2000)
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


class ProgressCreate(BaseModel):
    course_id: str
    lesson_date: date
    topic: str = Field(min_length=1, max_length=200)
    outline: Optional[str] = Field(default=None, max_length=2000)
    completed: bool = False
    notes: Optional[str] = Field(default=None, max_length=2000)


class ProgressUpdate(BaseModel):
    topic: Optional[str] = Field(default=None, min_length=1, max_length=200)
    outline: Optional[str] = Field(default=None, max_length=2000)
    completed: Optional[bool] = None
    notes: Optional[str] = Field(default=None, max_length=2000)


# ============== 作业 ==============

class Homework(BaseModel):
    id: str = Field(default_factory=_new_id)
    title: str = Field(min_length=1, max_length=200)
    course_id: Optional[str] = None
    class_group: Optional[str] = Field(default=None, max_length=100)
    description: Optional[str] = Field(default=None, max_length=4000)
    link: Optional[str] = Field(default=None, max_length=500)
    published_at: date
    due_at: Optional[date] = None
    remind_before_days: int = Field(default=1, ge=0, le=30)
    status: str = Field(default="pending", pattern="^(pending|submitted|overdue|cancelled)$")
    submitted_at: Optional[date] = None
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)

    @model_validator(mode="after")
    def _check(self):
        if self.due_at is not None and self.due_at < self.published_at:
            raise ValueError("due_at 不能早于 published_at")
        return self


class HomeworkCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    course_id: Optional[str] = None
    class_group: Optional[str] = Field(default=None, max_length=100)
    description: Optional[str] = Field(default=None, max_length=4000)
    link: Optional[str] = Field(default=None, max_length=500)
    published_at: date
    due_at: Optional[date] = None
    remind_before_days: int = Field(default=1, ge=0, le=30)

    @model_validator(mode="after")
    def _check(self):
        if self.due_at is not None and self.due_at < self.published_at:
            raise ValueError("due_at 不能早于 published_at")
        return self


class HomeworkUpdate(BaseModel):
    title: Optional[str] = Field(default=None, min_length=1, max_length=200)
    description: Optional[str] = Field(default=None, max_length=4000)
    link: Optional[str] = Field(default=None, max_length=500)
    published_at: Optional[date] = None
    due_at: Optional[date] = None
    remind_before_days: Optional[int] = Field(default=None, ge=0, le=30)
    status: Optional[str] = Field(default=None, pattern="^(pending|submitted|overdue|cancelled)$")
    submitted_at: Optional[date] = None

    @model_validator(mode="after")
    def _check(self):
        if (
            self.published_at is not None
            and self.due_at is not None
            and self.due_at < self.published_at
        ):
            raise ValueError("due_at 不能早于 published_at")
        return self


# ============== 教学事务 ==============

class Affair(BaseModel):
    """教学事务：教研会、材料提交、答辩等。"""

    id: str = Field(default_factory=_new_id)
    title: str = Field(min_length=1, max_length=200)
    kind: str = Field(default="other", pattern="^(meeting|material|defense|other)$")
    description: Optional[str] = Field(default=None, max_length=4000)
    due_at: Optional[datetime] = None
    remind_before_minutes: int = Field(default=60, ge=0, le=10080)
    reminded: bool = False
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


class AffairCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    kind: str = Field(default="other", pattern="^(meeting|material|defense|other)$")
    description: Optional[str] = Field(default=None, max_length=4000)
    due_at: Optional[datetime] = None
    remind_before_minutes: int = Field(default=60, ge=0, le=10080)


class AffairUpdate(BaseModel):
    title: Optional[str] = Field(default=None, min_length=1, max_length=200)
    kind: Optional[str] = Field(default=None, pattern="^(meeting|material|defense|other)$")
    description: Optional[str] = Field(default=None, max_length=4000)
    due_at: Optional[datetime] = None
    remind_before_minutes: Optional[int] = Field(default=None, ge=0, le=10080)
    reminded: Optional[bool] = None


# ============== 文档 ==============

class Document(BaseModel):
    """教学文档（大纲、课件）。用于向量检索。"""

    id: str = Field(default_factory=_new_id)
    title: str = Field(min_length=1, max_length=200)
    path: str = Field(min_length=1, max_length=500)
    course_id: Optional[str] = None
    indexed: bool = False
    chunk_count: int = 0
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


# ============== 对外标准响应模型 ==============

class CoursePublic(BaseModel):
    """对外课程信息。"""
    model_config = ConfigDict(from_attributes=True)
    id: str
    name: str
    class_group: Optional[str] = None
    location: Optional[str] = None
    semester_start: Optional[date] = None
    semester_end: Optional[date] = None
    start_time: Optional[time] = None
    end_time: Optional[time] = None
    weekday: Optional[int] = None
    weeks: Optional[list[int]] = None
    notes: Optional[str] = None
    created_at: datetime
    updated_at: datetime


# ============== 文件系统（阶段 ①） ==============

# 固定 7 个角色（教师的"文件夹"），不允许新增/修改。
# 用 Literal 让 Pydantic 强校验；序列化到 DB 用 string 形式。
FileRole = Literal[
    "slides",        # 幻灯片
    "lecture",       # 讲义
    "material",      # 资料
    "homework",      # 作业
    "submission",    # 学生提交
    "reference",     # 参考资料（大纲、其他老师材料）
    "outline",       # 大纲本身
]


# 顶层分类（4 选 1）
TopKind = Literal["教学", "科研", "事务", "文件"]


ROLE_DIRS = {
    "slides": "幻灯片",
    "lecture": "讲义",
    "material": "资料",
    "homework": "作业",
    "submission": "提交",
    "reference": "进度参考",
    "outline": "大纲",
}
# 中文名 → 枚举值
DIR_TO_ROLE = {v: k for k, v in ROLE_DIRS.items()}


# 允许的文件类型白名单（写得严一些，避免被教师误把 .exe 拖进来）
ALLOWED_EXTENSIONS = frozenset({
    ".pdf", ".ppt", ".pptx", ".doc", ".docx", ".xls", ".xlsx",
    ".md", ".txt", ".csv",
    ".png", ".jpg", ".jpeg", ".gif", ".webp",
    ".zip",  # 教案合集
})


class CourseFile(BaseModel):
    """课程文件清单一项。

    - path_rel：以 data/ 为根的相对路径（含顶层/课程/角色/文件名）
    - sha256 / size / mtime_ns：上次扫描确定的状态，用于增量检测
    - is_current：True = 此 sha 是当前文件；False = 历史版本（被覆盖但留了）
    """
    id: str = Field(default_factory=_new_id)
    course_id: Optional[str] = None         # 业务关联课；顶层非教学可空
    top_kind: Optional[TopKind] = None
    role: Optional[FileRole] = None
    path_rel: str = Field(min_length=1, max_length=1000)
    filename: str = Field(min_length=1, max_length=255)
    sha256: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
    size: int = Field(ge=0)
    mtime_ns: int = Field(ge=0)
    is_current: bool = True
    superseded_at: Optional[datetime] = None
    replaced_by: Optional[str] = None       # 当前 sha 的 id（指向 replaced 行的 current 行）
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


class CourseBinding(BaseModel):
    """顶层目录 ↔ Course 的绑定记录。

    单一顶层目录仅可绑定到一个 Course；绑定不强制存在数据库中的 Course
    （用于孤儿文件夹等待教师绑定时占位）。
    """
    id: str = Field(default_factory=_new_id)
    top_kind: TopKind
    dirname: str = Field(min_length=1, max_length=100)  # 顶层下子目录名
    course_id: Optional[str] = None                     # NULL = 孤儿
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)
