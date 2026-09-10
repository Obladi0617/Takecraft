import os
from pathlib import Path

from dotenv import load_dotenv
from pydantic import Field
from pydantic_settings import BaseSettings

# Load .env file from backend directory
dotenv_path = Path(__file__).resolve().parents[1] / ".env"
if dotenv_path.exists():
    # This repository's runtime profile is authoritative.  Without override,
    # stale user-level FILMAGENT_* variables silently win over backend/.env
    # (the previous unavailable ModelScope model kept being selected).
    load_dotenv(dotenv_path, override=True)

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
    image_backend: str = "mock"  # mock | modelscope | comfyui
    video_backend: str = "mock"  # mock | image_motion | comfyui | minimax | modelscope
    image_generation_concurrency: int = 1  # 图片模型占用较大，默认串行
    video_generation_concurrency: int = 1  # 规格书第 24 节
    comfyui_base_url: str = "http://127.0.0.1:8188"
    # Long R2V scenes can take close to an hour at 1 MP.  Keep enough room for
    # generation plus video/audio decoding and saving on the DGX.
    comfyui_timeout: float = 7200.0
    comfyui_megapixels: float = 0.4
    comfyui_image_steps: int = 20
    comfyui_image_cfg: float = 4.0
    comfyui_image_shift: float = 3.1

    # 云端 API（规格补丁：云本地协同；Key 缺失时相应后端不可用）
    minimax_api_key: str = ""
    minimax_base_url: str = "https://api.minimax.cn"
    minimax_video_model: str = "MiniMax-H3"
    modelscope_api_key: str = ""
    modelscope_base_url: str = "https://api-inference.modelscope.cn"
    modelscope_image_model: str = "Qwen/Qwen-Image"
    modelscope_video_model: str = "Wan-AI/Wan2.2-T2V-Fast"

    # 文本模型（Agent 大脑；openai 兼容端点皆可）
    llm_backend: str = "mock"  # mock | openai
    llm_api_key: str = ""
    llm_base_url: str = "https://api-inference.modelscope.cn"
    llm_model: str = "deepseek-ai/DeepSeek-V4-Pro"
    # Optional direct-route override for machines whose VPN/TUN fake-DNS
    # intercepts a domestic API endpoint.  The original hostname is retained
    # for both the HTTP Host header and TLS SNI verification.
    llm_connect_ip: str = ""
    llm_local_address: str = ""
    llm_timeout: float = 120.0
    llm_max_retries: int = 3
    llm_retry_base_delay: float = 0.5
    llm_max_tokens: int = 4096
    llm_reasoning_effort: str = ""
    llm_json_mode: bool = False
    llm_temperature: float = 0.2

    # 视觉理解模型（Reviewer 真实看片；openai 兼容端点皆可，抽帧走 base64 image_url）
    vlm_backend: str = "mock"  # mock | openai
    vlm_api_key: str = ""
    vlm_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode"
    vlm_model: str = "qwen3-vl-plus"
    vlm_max_tokens: int = 1200
    vlm_timeout: float = 300.0
    # vLLM 扩展参数，只给本地量化模型用（云端兼容端点会 400），0 表示不下发
    vlm_repetition_penalty: float = 0.0

    # AI Dailies 审核：闸门阈值来自 ffmpeg signalstats，命中即不调用视觉模型
    review_concurrency: int = 2
    review_frame_count: int = 8
    review_max_width: int = 896
    review_jpeg_quality: int = 4
    review_keep_threshold: float = 75.0
    review_reject_floor: float = 45.0
    review_item_floor: int = 40
    review_duration_tolerance: float = 0.2
    review_black_ratio_max: float = 0.5
    review_freeze_ratio_max: float = 0.9
    review_compare: bool = True
    review_compare_max_candidates: int = 3

    # 自动重抽上限（规格 §12）
    max_auto_retake_rounds: int = 2

    model_config = {"env_prefix": "FILMAGENT_"}


settings = Settings()
