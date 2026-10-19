"""伴教配置（环境变量 + .env）。"""

from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict

PACKAGE_DIR = Path(__file__).resolve().parent
SRC_DIR = PACKAGE_DIR.parent
SKILL_ROOT = SRC_DIR.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="BANJIAO_",
        env_file=str(SKILL_ROOT / ".env"),
    )

    app_name: str = "伴教 Skill 内核"
    host: str = "127.0.0.1"
    port: int = 30826

    data_dir: Path = SKILL_ROOT / "data"
    files_dir: Path = SKILL_ROOT / "files"
    vector_dir: Path = SKILL_ROOT / "vector_store"
    docs_dir: Path = SKILL_ROOT / "docs"

    enable_vector: bool = False
    vector_model: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"


settings = Settings()
