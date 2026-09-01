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
    image_backend: str = "mock"  # mock | modelscope
    video_backend: str = "mock"  # mock | minimax | modelscope
    video_generation_concurrency: int = 1  # 规格书第 24 节

    # 云端 API（规格补丁：云本地协同；Key 缺失时相应后端不可用）
    minimax_api_key: str = ""
    minimax_base_url: str = "https://api.minimax.cn"
    minimax_video_model: str = "MiniMax-H3"
    modelscope_api_key: str = ""
    modelscope_base_url: str = "https://api-inference.modelscope.cn"
    modelscope_image_model: str = "Qwen/Qwen-Image"
    modelscope_video_model: str = "Wan-AI/Wan2.2-T2V-Fast"

    model_config = {"env_prefix": "FILMAGENT_"}


settings = Settings()
