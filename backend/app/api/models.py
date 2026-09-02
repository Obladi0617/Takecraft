"""模型运行时状态接口。"""

from fastapi import APIRouter

from ..agents import get_text_model
from ..config import settings

router = APIRouter(prefix="/api/v1/models", tags=["models"])


@router.get("/health")
async def model_health():
    try:
        result = await get_text_model().health()
    except Exception as exc:  # noqa: BLE001 - health endpoint must report config errors
        result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    return {
        "text": result,
        "image_backend": settings.image_backend,
        "video_backend": settings.video_backend,
        "vlm_backend": settings.vlm_backend,
    }
