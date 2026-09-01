from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings

REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    data_dir: Path = REPO_ROOT / "data"
    proxy_height: int = 720
    cors_origins: list[str] = Field(
        default_factory=lambda: [
            "http://localhost:5173",
            "http://127.0.0.1:5173",
        ]
    )

    model_config = {"env_prefix": "FILMAGENT_"}


settings = Settings()
