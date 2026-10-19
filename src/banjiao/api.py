"""伴教 FastAPI 后端（按需临时启动）。

由 run_web.py 启动。本文件不常驻，没有请求时不会消耗资源。
只读不做 heavy 操作；大部分 CRUD 由 LLM 脚本通过 service 层完成。
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .config import settings
from .llm_errors import SkillError
from .models import (
    AffairCreate,
    AffairUpdate,
    CourseCreate,
    CourseUpdate,
    HomeworkCreate,
    HomeworkUpdate,
    ProgressCreate,
    ProgressUpdate,
)
from . import service, vector_index


WEB_DIR = Path(__file__).resolve().parent / "web"


app = FastAPI(title=settings.app_name)


def _err(e: SkillError) -> JSONResponse:
    return JSONResponse(status_code=e.status, content=e.to_dict())


@app.get("/api/health")
def health():
    return {"status": "ok", "app": settings.app_name, "vector_enabled": settings.enable_vector}


# ---- 课程 ----

@app.get("/api/courses")
def list_courses():
    return [c.model_dump(mode="json") for c in service.list_courses()]


@app.post("/api/courses", status_code=201)
def create_course(body: CourseCreate):
    try:
        c = service.create_course(body)
    except SkillError as e:
        raise HTTPException(status_code=e.status, detail=e.to_dict())
    return c.model_dump(mode="json")


@app.get("/api/courses/today")
def today_lessons():
    return service.todays_lessons()


@app.get("/api/courses/week")
def week_lessons(start: str | None = None):
    from datetime import date
    s = date.fromisoformat(start) if start else None
    return service.week_lessons(s)


@app.get("/api/courses/{cid}")
def get_course(cid: str):
    try:
        return service.get_course(cid).model_dump(mode="json")
    except SkillError as e:
        raise HTTPException(status_code=e.status, detail=e.to_dict())


@app.patch("/api/courses/{cid}")
def update_course(cid: str, body: CourseUpdate):
    try:
        c = service.update_course(cid, body)
    except SkillError as e:
        raise HTTPException(status_code=e.status, detail=e.to_dict())
    return c.model_dump(mode="json")


@app.delete("/api/courses/{cid}")
def delete_course(cid: str):
    try:
        c = service.delete_course(cid)
    except SkillError as e:
        raise HTTPException(status_code=e.status, detail=e.to_dict())
    return {"ok": True, "id": c.id}


# ---- 进度 ----

@app.get("/api/progress")
def list_progress(course_id: str | None = None):
    return [p.model_dump(mode="json") for p in service.list_progress(course_id)]


@app.post("/api/progress", status_code=201)
def create_progress(body: ProgressCreate):
    try:
        p = service.create_progress(body)
    except SkillError as e:
        raise HTTPException(status_code=e.status, detail=e.to_dict())
    return p.model_dump(mode="json")


@app.patch("/api/progress/{pid}")
def update_progress(pid: str, body: ProgressUpdate):
    try:
        p = service.update_progress(pid, body)
    except SkillError as e:
        raise HTTPException(status_code=e.status, detail=e.to_dict())
    return p.model_dump(mode="json")


@app.delete("/api/progress/{pid}")
def delete_progress(pid: str):
    try:
        service.delete_progress(pid)
    except SkillError as e:
        raise HTTPException(status_code=e.status, detail=e.to_dict())
    return {"ok": True}


# ---- 作业 ----

@app.get("/api/homework")
def list_homework(
    status: str | None = None,
    only_pending: bool = False,
    overdue: bool = False,
):
    return [h.model_dump(mode="json") for h in
            service.list_homework(status=status, only_pending=only_pending, overdue=overdue)]


@app.get("/api/homework/due-soon")
def homework_due_soon():
    return service.due_soon_homework()


@app.post("/api/homework", status_code=201)
def create_homework(body: HomeworkCreate):
    try:
        h = service.create_homework(body)
    except SkillError as e:
        raise HTTPException(status_code=e.status, detail=e.to_dict())
    return h.model_dump(mode="json")


@app.patch("/api/homework/{hid}")
def update_homework(hid: str, body: HomeworkUpdate):
    try:
        h = service.update_homework(hid, body)
    except SkillError as e:
        raise HTTPException(status_code=e.status, detail=e.to_dict())
    return h.model_dump(mode="json")


@app.delete("/api/homework/{hid}")
def delete_homework(hid: str):
    try:
        service.delete_homework(hid)
    except SkillError as e:
        raise HTTPException(status_code=e.status, detail=e.to_dict())
    return {"ok": True}


# ---- 事务 ----

@app.get("/api/affairs")
def list_affairs():
    return [a.model_dump(mode="json") for a in service.list_affairs()]


@app.get("/api/affairs/due-soon")
def affairs_due_soon():
    return service.due_soon_affairs()


@app.post("/api/affairs", status_code=201)
def create_affair(body: AffairCreate):
    try:
        a = service.create_affair(body)
    except SkillError as e:
        raise HTTPException(status_code=e.status, detail=e.to_dict())
    return a.model_dump(mode="json")


@app.patch("/api/affairs/{aid}")
def update_affair(aid: str, body: AffairUpdate):
    try:
        a = service.update_affair(aid, body)
    except SkillError as e:
        raise HTTPException(status_code=e.status, detail=e.to_dict())
    return a.model_dump(mode="json")


@app.delete("/api/affairs/{aid}")
def delete_affair(aid: str):
    try:
        service.delete_affair(aid)
    except SkillError as e:
        raise HTTPException(status_code=e.status, detail=e.to_dict())
    return {"ok": True}


@app.post("/api/affairs/{aid}/reminded")
def mark_reminded(aid: str):
    try:
        a = service.mark_affair_reminded(aid)
    except SkillError as e:
        raise HTTPException(status_code=e.status, detail=e.to_dict())
    return a.model_dump(mode="json")


# ---- 文档 / 向量 ----

@app.get("/api/documents")
def list_documents():
    return [d.model_dump(mode="json") for d in service.list_documents()]


@app.get("/api/vector/stats")
def vector_stats():
    try:
        return vector_index.get_index().stats()
    except SkillError as e:
        return _err(e)


@app.get("/api/vector/search")
def vector_search(q: str, top_k: int = 5):
    try:
        return vector_index.get_index().search(q, top_k=top_k)
    except SkillError as e:
        return _err(e)


# ============== 文件系统（阶段 ①-③） ==============

@app.get("/api/fs/top-dirs")
def fs_list_top_dirs():
    return service.fs_list_top_dirs()


@app.post("/api/fs/init")
def fs_init():
    return service.fs_init()


class FsBindBody(BaseModel):
    top_kind: str
    dirname: str
    course_id: str


@app.post("/api/fs/bind-course")
def fs_bind_course(body: FsBindBody):
    try:
        return service.fs_bind_course(body.top_kind, body.dirname, body.course_id)
    except SkillError as e:
        raise HTTPException(status_code=e.status, detail=e.to_dict())


@app.delete("/api/fs/bindings/{binding_id}")
def fs_unbind_course(binding_id: str):
    try:
        return service.fs_unbind_course(binding_id)
    except SkillError as e:
        raise HTTPException(status_code=e.status, detail=e.to_dict())


@app.post("/api/fs/scan-course")
def fs_scan_course(body: dict):
    try:
        return service.fs_scan_course(body["course_id"])
    except SkillError as e:
        raise HTTPException(status_code=e.status, detail=e.to_dict())


@app.get("/api/courses/{cid}/files")
def course_list_files(cid: str, role: str | None = None):
    try:
        return service.course_list_files(cid, role=role)
    except SkillError as e:
        raise HTTPException(status_code=e.status, detail=e.to_dict())


@app.get("/api/courses/{cid}/files/{role}/{filename}")
def fs_read_file(cid: str, role: str, filename: str):
    try:
        return service.fs_read_file(cid, role, filename)
    except SkillError as e:
        raise HTTPException(status_code=e.status, detail=e.to_dict())


@app.get("/api/courses/{cid}/files/{role}/{filename}/history")
def fs_file_history(cid: str, role: str, filename: str):
    try:
        return service.fs_file_history(cid, role, filename)
    except SkillError as e:
        raise HTTPException(status_code=e.status, detail=e.to_dict())


class FsWriteTextBody(BaseModel):
    text: str


@app.put("/api/courses/{cid}/files/{role}/{filename}")
def fs_write_text(cid: str, role: str, filename: str, body: FsWriteTextBody):
    try:
        return service.fs_write_text(cid, role, filename, body.text)
    except SkillError as e:
        raise HTTPException(status_code=e.status, detail=e.to_dict())


@app.post("/api/fs/archive")
def fs_archive(body: dict):
    try:
        return service.fs_archive(body["path"])
    except SkillError as e:
        raise HTTPException(status_code=e.status, detail=e.to_dict())


class FsDeleteBody(BaseModel):
    path: str
    confirm: str | None = None


@app.post("/api/fs/delete")
def fs_delete(body: FsDeleteBody):
    try:
        return service.fs_delete(body.path, confirm=body.confirm)
    except SkillError as e:
        raise HTTPException(status_code=e.status, detail=e.to_dict())


# ============== 学校课程时间表 ==============

@app.get("/api/school/schedule")
def school_get_schedule():
    return service.school_get_schedule()


class SchoolSetBody(BaseModel):
    schedule: dict


@app.put("/api/school/schedule")
def school_set_schedule(body: SchoolSetBody):
    try:
        return service.school_set_schedule(body.schedule)
    except SkillError as e:
        raise HTTPException(status_code=e.status, detail=e.to_dict())


# ============== 大纲 + 进度结合（阶段 ④） ==============

@app.post("/api/courses/{cid}/apply-period")
def course_apply_period(cid: str):
    try:
        return service.course_apply_period(cid).model_dump(mode="json")
    except SkillError as e:
        raise HTTPException(status_code=e.status, detail=e.to_dict())


class CourseSetOutlineBody(BaseModel):
    filename: str


@app.post("/api/courses/{cid}/outline-path")
def course_set_outline_path(cid: str, body: CourseSetOutlineBody):
    try:
        return service.course_set_outline_path(cid, body.filename).model_dump(mode="json")
    except SkillError as e:
        raise HTTPException(status_code=e.status, detail=e.to_dict())


@app.get("/api/courses/{cid}/outline")
def course_get_outline(cid: str):
    try:
        return service.course_get_outline(cid)
    except SkillError as e:
        raise HTTPException(status_code=e.status, detail=e.to_dict())


class CourseRefreshOutlineBody(BaseModel):
    parsed: dict


@app.put("/api/courses/{cid}/outline-cache")
def course_refresh_outline(cid: str, body: CourseRefreshOutlineBody):
    try:
        return service.course_refresh_outline(cid, body.parsed).model_dump(mode="json")
    except SkillError as e:
        raise HTTPException(status_code=e.status, detail=e.to_dict())


@app.get("/api/courses/{cid}/next-topic")
def course_next_topic(cid: str, target_date: str | None = None):
    try:
        from datetime import date as _date
        td = _date.fromisoformat(target_date) if target_date else None
        return service.course_next_topic(cid, td)
    except SkillError as e:
        raise HTTPException(status_code=e.status, detail=e.to_dict())
    except ValueError:
        raise HTTPException(status_code=400, detail={"code": "bad_param", "message": "target_date 应为 YYYY-MM-DD"})


# ---- 静态前端（极简 HTML） ----

@app.get("/")
def index():
    return FileResponse(WEB_DIR / "index.html")


app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")
