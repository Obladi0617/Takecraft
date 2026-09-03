import asyncio
import json
import mimetypes
import random
import uuid
from pathlib import Path

import httpx

from ..config import settings
from .base import (
    GeneratedImage,
    GeneratedVideo,
    ImageGenerationRequest,
    VideoGenerationRequest,
)

POLL_INTERVAL = 5.0
POLL_TIMEOUT = 900.0


class CloudJobRegistry:
    """submit/status/result 三段式（规格 §21 云端 API 模式）的进程内任务表。"""

    def __init__(self):
        self._tasks: dict[str, asyncio.Task] = {}
        self._results: dict[str, object] = {}
        self._errors: dict[str, str] = {}
        self._cancelled: set[str] = set()

    def track(self, job_id: str, task: asyncio.Task) -> str:
        self._tasks[job_id] = task
        return job_id

    def succeed(self, job_id: str, result: object) -> None:
        self._results[job_id] = result

    def fail(self, job_id: str, message: str) -> None:
        self._errors[job_id] = message

    async def status(self, job_id: str) -> str:
        if job_id in self._results:
            return "DONE"
        if job_id in self._errors:
            return "FAILED"
        task = self._tasks.get(job_id)
        if task is None:
            return "UNKNOWN"
        if task.cancelled():
            return "CANCELLED"
        if task.done():
            return "FAILED"
        return "RUNNING"

    async def cancel(self, job_id: str) -> None:
        self._cancelled.add(job_id)
        task = self._tasks.get(job_id)
        if task is not None and not task.done():
            task.cancel()

    async def result(self, job_id: str) -> object:
        if job_id in self._results:
            value = self._results.pop(job_id)
            self._tasks.pop(job_id, None)
            self._errors.pop(job_id, None)
            return value
        raise RuntimeError(self._errors.get(job_id, "云任务未完成或已取消"))


async def _download(client: httpx.AsyncClient, url: str, dst: Path) -> Path:
    dst.parent.mkdir(parents=True, exist_ok=True)
    async with client.stream("GET", url) as resp:
        resp.raise_for_status()
        with dst.open("wb") as f:
            async for chunk in resp.aiter_bytes(1024 * 1024):
                f.write(chunk)
    return dst


def _headers(api_key: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }


class ModelScopeImageGenerator:
    """ModelScope API-Inference 图像后端（异步任务协议）。

    契约来源：modelscope/ms-agent ms_image_gen.py 与官方文档。
    """

    name = "modelscope"

    def __init__(self, workdir: Path):
        self.workdir = workdir
        if not settings.modelscope_api_key:
            raise RuntimeError("API Key 缺失：请设置 FILMAGENT_MODELSCOPE_API_KEY")

    async def generate(self, request: ImageGenerationRequest) -> list[GeneratedImage]:
        base = settings.modelscope_base_url.rstrip("/")
        model = settings.modelscope_image_model
        if request.width > request.height:
            size = "1664x928"
        elif request.height > request.width:
            size = "928x1664"
        else:
            size = "1328x1328"
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                f"{base}/v1/images/generations",
                headers={
                    **_headers(settings.modelscope_api_key),
                    "X-ModelScope-Async-Mode": "true",
                },
                json={
                    "model": model,
                    "prompt": request.prompt,
                    "negative_prompt": request.negative_prompt or "",
                    "size": size,
                },
            )
            resp.raise_for_status()
            task_id = resp.json()["task_id"]

            deadline = asyncio.get_event_loop().time() + POLL_TIMEOUT
            while asyncio.get_event_loop().time() < deadline:
                await asyncio.sleep(POLL_INTERVAL)
                result = await client.get(
                    f"{base}/v1/tasks/{task_id}",
                    headers={
                        **_headers(settings.modelscope_api_key),
                        "X-ModelScope-Task-Type": "image_generation",
                    },
                )
                result.raise_for_status()
                data = result.json()
                if data.get("task_status") == "SUCCEED":
                    urls = data.get("output_images", [])
                    images = []
                    for i, url in enumerate(urls[: request.count]):
                        dst = self.workdir / f"ms_{task_id}_{i:02d}.png"
                        await _download(client, url, dst)
                        images.append(
                            GeneratedImage(
                                path=str(dst),
                                model=f"{self.name}:{model}",
                                width=request.width,
                                height=request.height,
                            )
                        )
                    return images
                if data.get("task_status") == "FAILED":
                    raise RuntimeError(f"ModelScope 图像任务失败: {data}")
            raise TimeoutError("ModelScope 图像任务轮询超时")


class _CloudVideoBackend:
    """云视频后端公共骨架：submit→status→result 三段式 + 进程内任务表。"""

    name = "cloud"

    async def submit(self, request: VideoGenerationRequest) -> str:
        job_id = f"cloud_{uuid.uuid4().hex[:12]}"
        task = asyncio.create_task(self._run(job_id, request))
        self.registry.track(job_id, task)
        return job_id

    async def _run(self, job_id: str, request: VideoGenerationRequest) -> None:
        try:
            video = await self._generate(request)
            self.registry.succeed(job_id, video)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            self.registry.fail(job_id, str(e))

    async def status(self, job_id: str) -> str:
        return await self.registry.status(job_id)

    async def cancel(self, job_id: str) -> None:
        await self.registry.cancel(job_id)

    async def result(self, job_id: str) -> GeneratedVideo:
        return await self.registry.result(job_id)  # type: ignore[return-value]


class ModelScopeVideoGenerator(_CloudVideoBackend):
    """ModelScope API-Inference 视频后端。

    与图像任务同构的异步协议（任务式提交 + /v1/tasks 轮询 +
    output_videos 结果字段）；字段名以线上实测为准，未带 Key 时构造即报错。
    """

    name = "modelscope"

    def __init__(self, workdir: Path):
        self.workdir = workdir
        self.registry = CloudJobRegistry()
        if not settings.modelscope_api_key:
            raise RuntimeError("API Key 缺失：请设置 FILMAGENT_MODELSCOPE_API_KEY")

    async def _generate(self, request: VideoGenerationRequest) -> GeneratedVideo:
        base = settings.modelscope_base_url.rstrip("/")
        model = settings.modelscope_video_model
        auth = _headers(settings.modelscope_api_key)
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                f"{base}/v1/services/aigc/video-generation",
                headers={**auth, "X-ModelScope-Async-Mode": "true"},
                json={
                    "model": model,
                    "prompt": request.prompt,
                    "negative_prompt": request.negative_prompt or "",
                },
            )
            resp.raise_for_status()
            task_id = resp.json()["task_id"]

            deadline = asyncio.get_event_loop().time() + POLL_TIMEOUT
            while asyncio.get_event_loop().time() < deadline:
                await asyncio.sleep(POLL_INTERVAL)
                result = await client.get(
                    f"{base}/v1/tasks/{task_id}",
                    headers={
                        **auth,
                        "X-ModelScope-Task-Type": "video_generation",
                    },
                )
                result.raise_for_status()
                data = result.json()
                if data.get("task_status") == "SUCCEED":
                    urls = data.get("output_videos") or []
                    if not urls:
                        raise RuntimeError(f"任务成功但无视频 URL: {data}")
                    dst = self.workdir / f"ms_{task_id}.mp4"
                    await _download(client, urls[0], dst)
                    return GeneratedVideo(
                        path=str(dst),
                        model=f"{self.name}:{model}",
                        duration=request.duration,
                    )
                if data.get("task_status") == "FAILED":
                    raise RuntimeError(f"ModelScope 视频任务失败: {data}")
            raise TimeoutError("ModelScope 视频任务轮询超时")


class MiniMaxVideoGenerator(_CloudVideoBackend):
    """MiniMax H3 API 后端（规格 §22：首选候选 Video Generator 的云形态）。

    契约：POST /v1/video_generation 提交；GET /v2/query/video_generation/{task_id}
    轮询（succeeded/failed/cancelled）；成功后从 task.content.url / file.download_url
    下载。
    """

    name = "minimax"

    def __init__(self, workdir: Path):
        self.workdir = workdir
        self.registry = CloudJobRegistry()
        if not settings.minimax_api_key:
            raise RuntimeError("API Key 缺失：请设置 FILMAGENT_MINIMAX_API_KEY")

    async def _generate(self, request: VideoGenerationRequest) -> GeneratedVideo:
        base = settings.minimax_base_url.rstrip("/")
        model = settings.minimax_video_model
        headers = _headers(settings.minimax_api_key)
        payload: dict = {
            "model": model,
            "prompt": request.prompt,
            "duration": max(int(round(request.duration)), 1),
        }
        if request.negative_prompt:
            payload["negative_prompt"] = request.negative_prompt
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                f"{base}/v1/video_generation", headers=headers, json=payload
            )
            resp.raise_for_status()
            task_id = resp.json()["task_id"]

            deadline = asyncio.get_event_loop().time() + POLL_TIMEOUT
            while asyncio.get_event_loop().time() < deadline:
                await asyncio.sleep(POLL_INTERVAL)
                result = await client.get(
                    f"{base}/v2/query/video_generation/{task_id}", headers=headers
                )
                result.raise_for_status()
                data = result.json()
                status = data.get("status") or data.get("task_status")
                if status in ("succeeded", "SUCCEED", "success"):
                    url = self._extract_url(data)
                    if not url:
                        raise RuntimeError(f"任务成功但无视频 URL: {data}")
                    dst = self.workdir / f"mm_{task_id}.mp4"
                    await _download(client, url, dst)
                    return GeneratedVideo(
                        path=str(dst),
                        model=f"{self.name}:{model}",
                        duration=request.duration,
                    )
                if status in ("failed", "FAILED", "fail"):
                    raise RuntimeError(f"MiniMax 任务失败: {data}")
            raise TimeoutError("MiniMax 任务轮询超时")

    @staticmethod
    def _extract_url(data: dict) -> str | None:
        task = data.get("task") or {}
        content = task.get("content") or {}
        if isinstance(content, dict) and content.get("url"):
            return content["url"]
        file = data.get("file") or {}
        if isinstance(file, dict):
            for key in ("download_url", "file_url"):
                if file.get(key):
                    return file[key]
        return data.get("download_url")


class ComfyUIVideoGenerator(_CloudVideoBackend):
    """MiniMax H3 image-to-video through a trusted ComfyUI HTTP endpoint."""

    name = "comfyui:minimax-h3"

    def __init__(self, workdir: Path):
        self.workdir = workdir
        self.registry = CloudJobRegistry()
        self.workflow_path = Path(__file__).parent / "workflows" / "minimax_h3_i2v.api.json"

    async def _generate(self, request: VideoGenerationRequest) -> GeneratedVideo:
        source = Path(request.first_frame or "")
        if not request.first_frame or not source.is_file():
            raise RuntimeError("DGX 图生视频需要有效的首帧分镜图")
        base = settings.comfyui_base_url.rstrip("/")
        timeout = httpx.Timeout(60.0, read=120.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            health = await client.get(f"{base}/system_stats")
            health.raise_for_status()
            mime = mimetypes.guess_type(source.name)[0] or "application/octet-stream"
            upload = await client.post(
                f"{base}/upload/image",
                files={"image": (source.name, source.read_bytes(), mime)},
                data={"type": "input", "subfolder": "takecraft", "overwrite": "true"},
            )
            upload.raise_for_status()
            uploaded = upload.json()
            image_name = "/".join(
                part for part in (uploaded.get("subfolder", ""), uploaded["name"]) if part
            )
            workflow = json.loads(self.workflow_path.read_text(encoding="utf-8"))
            seed = random.SystemRandom().randrange(0, 2**48)
            workflow["114"]["inputs"]["image"] = image_name
            workflow["104"]["inputs"]["prompt"] = request.prompt
            workflow["111"]["inputs"]["value"] = max(5.0, float(request.duration))
            workflow["115"]["inputs"]["aspect_ratio"] = "16:9 (Widescreen)"
            workflow["115"]["inputs"]["megapixels"] = settings.comfyui_megapixels
            workflow["15"]["inputs"]["noise_seed"] = seed
            workflow["92"]["inputs"]["filename_prefix"] = f"video/Takecraft_{request.shot_id}"
            queued = await client.post(
                f"{base}/prompt",
                json={"prompt": workflow, "client_id": f"takecraft-{uuid.uuid4()}"},
            )
            if queued.is_error:
                raise RuntimeError(f"DGX 工作流提交失败: {queued.text[:1500]}")
            prompt_id = queued.json()["prompt_id"]
            deadline = asyncio.get_running_loop().time() + settings.comfyui_timeout
            while asyncio.get_running_loop().time() < deadline:
                await asyncio.sleep(POLL_INTERVAL)
                response = await client.get(f"{base}/history/{prompt_id}")
                response.raise_for_status()
                record = response.json().get(prompt_id)
                if not record:
                    continue
                status = record.get("status", {})
                if status.get("status_str") == "error":
                    messages = [m[1] for m in status.get("messages", []) if m[0] == "execution_error"]
                    raise RuntimeError(f"DGX 视频生成失败: {(messages[-1] if messages else status)}")
                output = record.get("outputs", {}).get("92", {})
                files = output.get("images") or output.get("videos") or []
                if not files:
                    raise RuntimeError(f"DGX 任务完成但没有视频输出: {record.get('outputs', {})}")
                item = files[0]
                video = await client.get(
                    f"{base}/view",
                    params={"filename": item["filename"], "subfolder": item.get("subfolder", ""), "type": item.get("type", "output")},
                )
                video.raise_for_status()
                self.workdir.mkdir(parents=True, exist_ok=True)
                dst = self.workdir / f"dgx_{prompt_id}.mp4"
                dst.write_bytes(video.content)
                await client.post(f"{base}/history", json={"delete": [prompt_id]})
                return GeneratedVideo(path=str(dst), model=self.name, seed=seed, duration=request.duration)
            raise TimeoutError(f"DGX 视频任务 {prompt_id} 超过等待时间")
