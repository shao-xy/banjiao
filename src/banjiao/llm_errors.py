"""标准化错误输出：LLM 脚本、HTTP 接口统一使用。"""

from typing import Any, Optional


class SkillError(Exception):
    """Skill 层语义错误。LLM 入口脚本捕获后转 JSON；HTTP 层映射到对应状态码。"""

    def __init__(
        self,
        code: str,
        message: str,
        status: int = 400,
        hint: Optional[str] = None,
        extra: Optional[dict[str, Any]] = None,
    ):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.hint = hint
        self.extra = extra or {}

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "ok": False,
            "error": {"code": self.code, "message": self.message, "status": self.status},
        }
        if self.hint:
            out["error"]["hint"] = self.hint
        if self.extra:
            out["error"]["extra"] = self.extra
        return out


def not_found(what: str, ident: Any) -> SkillError:
    return SkillError(
        code="not_found",
        message=f"{what} {ident!r} 不存在",
        status=404,
        hint="请先确认 id 是否正确，必要时调用 list 类接口获取可用 id。",
        extra={"resource": what, "id": ident},
    )


def bad_param(field: str, reason: str) -> SkillError:
    return SkillError(
        code="bad_param",
        message=f"字段 {field!r} 非法：{reason}",
        status=422,
        hint="请按字段定义修正后再调用。",
        extra={"field": field},
    )


def missing_param(field: str, ask: str | None = None) -> SkillError:
    return SkillError(
        code="missing_param",
        message=f"缺少必填字段 {field!r}",
        status=400,
        hint=ask or f"请向用户询问 {field} 的取值后再次调用。",
        extra={"field": field},
    )
