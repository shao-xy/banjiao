"""伴教配置（环境变量）。

不依赖 pydantic_settings（冷启动开销过大，且对单教师单机场景没必要）。
读 `BANJIAO_*` 环境变量；缺省走 SKILL_ROOT 默认目录。
"""

import os
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
SRC_DIR = PACKAGE_DIR.parent
SKILL_ROOT = SRC_DIR.parent


# 学校课程默认时间表（11 节；可在 data/school 时通过 school.set_schedule 覆盖）。
# 早晨 + 下午 + 晚上各 1 节。
DEFAULT_SCHOOL_SCHEDULE: dict = {
    "morning": [
        {"no": 1, "start": "08:00", "end": "08:45"},
        {"no": 2, "start": "08:50", "end": "09:35"},
        {"no": 3, "start": "09:50", "end": "10:35"},
        {"no": 4, "start": "10:40", "end": "11:25"},
        {"no": 5, "start": "11:30", "end": "12:15"},
    ],
    "afternoon": [
        {"no": 6, "start": "13:30", "end": "14:15"},
        {"no": 7, "start": "14:20", "end": "15:05"},
        {"no": 8, "start": "15:20", "end": "16:05"},
        {"no": 9, "start": "16:10", "end": "16:55"},
        {"no": 10, "start": "17:00", "end": "17:45"},
    ],
    "evening": [
        {"no": 11, "start": "19:00", "end": "19:45"},
    ],
}


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


class _Settings:
    app_name: str = "伴教 Skill 内核"
    host: str = os.environ.get("BANJIAO_HOST", "127.0.0.1")
    port: int = int(os.environ.get("BANJIAO_PORT", "30826"))

    data_dir: Path = Path(os.environ.get("BANJIAO_DATA_DIR", str(SKILL_ROOT / "data")))
    files_dir: Path = Path(os.environ.get("BANJIAO_FILES_DIR", str(SKILL_ROOT / "files")))
    vector_dir: Path = Path(os.environ.get("BANJIAO_VECTOR_DIR", str(SKILL_ROOT / "vector_store")))
    docs_dir: Path = Path(os.environ.get("BANJIAO_DOCS_DIR", str(SKILL_ROOT / "docs")))

    enable_vector: bool = _env_bool("BANJIAO_ENABLE_VECTOR", False)
    vector_model: str = os.environ.get(
        "BANJIAO_VECTOR_MODEL",
        "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
    )

    def __repr__(self) -> str:
        return (
            f"_Settings(data_dir={self.data_dir!r}, files_dir={self.files_dir!r}, "
            f"enable_vector={self.enable_vector})"
        )


settings = _Settings()
