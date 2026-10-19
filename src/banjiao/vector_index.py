"""可选的向量索引模块（教学大纲/课件检索）。

设计：
- 默认关闭（settings.enable_vector=False），按需打开
- 依赖 sentence-transformers；离线无该包时所有方法抛 SkillError
- 使用纯 Python 余弦计算 + JSON 存储（无需 numpy/faiss），万级以下足够
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import settings
from .db import db
from .llm_errors import SkillError
from .models import Document


def _chunk_text(text: str, chunk_size: int = 500, overlap: int = 50) -> list[str]:
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return []
    chunks: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        end = min(i + chunk_size, n)
        chunks.append(text[i:end])
        if end == n:
            break
        i = max(end - overlap, i + 1)
    return chunks


def _index_file() -> Path:
    return settings.vector_dir / "index.json"


def _cosine(a: list[float], b: list[float]) -> float:
    # 已归一化，点积即可
    return sum(x * y for x, y in zip(a, b))


class VectorIndex:
    """教学文档向量检索。"""

    def __init__(self):
        self.enabled: bool = settings.enable_vector
        self._model = None
        self._index_path = _index_file()
        self._items: list[dict[str, Any]] = []
        self._load()

    def _ensure_model(self):
        if self._model is not None:
            return
        if not self.enabled:
            raise SkillError(
                "vector_disabled",
                "向量检索未启用。设置环境变量 BANJIAO_ENABLE_VECTOR=1 后重启 Skill。",
                status=400,
                hint="伴教默认关闭向量检索以减少依赖；如需启用请安装 sentence-transformers。",
            )
        try:
            from sentence_transformers import SentenceTransformer  # type: ignore
        except ImportError as e:
            raise SkillError(
                "vector_unavailable",
                "未安装 sentence-transformers，无法进行向量检索。",
                status=503,
                hint="pip install sentence-transformers；或关闭向量检索。",
            ) from e
        self._model = SentenceTransformer(settings.vector_model)

    def _load(self) -> None:
        if not self._index_path.exists():
            self._items = []
            return
        try:
            self._items = json.loads(self._index_path.read_text(encoding="utf-8"))
        except Exception:
            self._items = []

    def _save(self) -> None:
        self._index_path.parent.mkdir(parents=True, exist_ok=True)
        self._index_path.write_text(
            json.dumps(self._items, ensure_ascii=False),
            encoding="utf-8",
        )

    def add_text(
        self,
        title: str,
        text: str,
        path: str,
        course_id: str | None = None,
    ) -> Document:
        """把一段文本入库并建立索引；返回 Document 记录。"""
        self._ensure_model()
        chunks = _chunk_text(text)
        if not chunks:
            raise SkillError("empty_doc", "文档内容为空，未建立索引。", status=400)

        d = db.create_document(
            Document(
                title=title,
                path=path,
                course_id=course_id,
            )
        )
        # 覆盖语义：先移除同名 doc 的旧索引
        self._items = [x for x in self._items if x["doc_id"] != d.id]
        vectors = self._model.encode(chunks, normalize_embeddings=True).tolist()  # type: ignore
        for i, (chunk, vec) in enumerate(zip(chunks, vectors)):
            self._items.append(
                {
                    "doc_id": d.id,
                    "chunk_id": i,
                    "text": chunk,
                    "vec": vec,
                }
            )
        d.indexed = True
        d.chunk_count = len(chunks)
        d.updated_at = datetime.now()
        db.update_document(d)
        self._save()
        return d

    def add_file(
        self,
        title: str,
        rel_path: str,
        course_id: str | None = None,
    ) -> Document:
        """读取 docs/ 下的文本文件入库。"""
        full = settings.docs_dir / rel_path
        if not full.exists() or not full.is_file():
            raise SkillError(
                "file_not_found",
                f"文件 {rel_path} 不存在（应位于 {settings.docs_dir}）。",
                status=404,
                hint="把大纲/课件放到 docs/ 目录再调用本接口。",
            )
        text = full.read_text(encoding="utf-8", errors="ignore")
        return self.add_text(title=title, text=text, path=rel_path, course_id=course_id)

    def search(self, query: str, top_k: int = 5) -> list[dict[str, Any]]:
        if not query.strip():
            raise SkillError("bad_param", "查询语句不能为空", status=400, extra={"field": "query"})
        self._ensure_model()
        if not self._items:
            return []
        qvec = self._model.encode([query], normalize_embeddings=True)[0].tolist()  # type: ignore
        scored: list[tuple[float, dict]] = []
        for it in self._items:
            s = _cosine(qvec, it["vec"])
            scored.append((s, it))
        scored.sort(key=lambda x: x[0], reverse=True)
        out: list[dict] = []
        for s, it in scored[:top_k]:
            doc = db.get_document(it["doc_id"])
            out.append(
                {
                    "score": round(float(s), 4),
                    "doc_id": it["doc_id"],
                    "title": doc.title if doc else "?",
                    "path": doc.path if doc else "?",
                    "chunk_id": it["chunk_id"],
                    "text": it["text"],
                }
            )
        return out

    def remove(self, doc_id: str) -> bool:
        before = len(self._items)
        self._items = [x for x in self._items if x["doc_id"] != doc_id]
        if len(self._items) == before:
            return False
        self._save()
        return True

    def stats(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "doc_count": len({x["doc_id"] for x in self._items}),
            "chunk_count": len(self._items),
            "model": settings.vector_model,
        }


_index: VectorIndex | None = None


def get_index() -> VectorIndex:
    global _index
    if _index is None:
        _index = VectorIndex()
    return _index
