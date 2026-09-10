import asyncio
import json
import math
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

_COMFYUI_MODEL_SWITCH_LOCK = asyncio.Lock()
_COMFYUI_RESIDENT_MODEL: str | None = None


async def _prepare_comfyui_model(
    client: httpx.AsyncClient, base: str, model_key: str
) -> None:
    """Release memory only when changing model families, never between sibling jobs."""
    global _COMFYUI_RESIDENT_MODEL
    async with _COMFYUI_MODEL_SWITCH_LOCK:
        if _COMFYUI_RESIDENT_MODEL == model_key:
            return
        response = await client.post(
            f"{base}/free",
            json={"unload_models": True, "free_memory": True},
        )
        response.raise_for_status()
        _COMFYUI_RESIDENT_MODEL = model_key


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


async def _modelscope_request(
    client: httpx.AsyncClient, method: str, url: str, **kwargs
) -> httpx.Response:
    """Retry ModelScope's dynamic AIGC throttling without duplicating a task."""
    for attempt in range(5):
        response = await client.request(method, url, **kwargs)
        if response.status_code != 429:
            return response
        if attempt == 4:
            return response
        retry_after = response.headers.get("Retry-After")
        try:
            delay = float(retry_after) if retry_after else 5.0 * (2**attempt)
        except ValueError:
            delay = 5.0 * (2**attempt)
        await asyncio.sleep(min(delay, 60.0))
    raise RuntimeError("unreachable")

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
            resp = await _modelscope_request(
                client, "POST",
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
                result = await _modelscope_request(
                    client, "GET",
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


class ComfyUIImageGenerator:
    """DGX image generation: Qwen text-to-image and FLUX.2 multi-reference edit."""

    name = "comfyui:qwen-image"

    def __init__(self, workdir: Path):
        self.workdir = workdir

    @staticmethod
    def _workflow(request: ImageGenerationRequest, seed: int, prefix: str) -> dict:
        width = max(256, (int(request.width) // 16) * 16)
        height = max(256, (int(request.height) // 16) * 16)
        negative = request.negative_prompt or "low quality, blurry, distorted, watermark, text"
        return {
            "1": {"class_type": "UNETLoader", "inputs": {
                "unet_name": "qwen_image_fp8_e4m3fn.safetensors", "weight_dtype": "default"}},
            "2": {"class_type": "CLIPLoader", "inputs": {
                "clip_name": "qwen_2.5_vl_7b_fp8_scaled.safetensors",
                "type": "qwen_image", "device": "default"}},
            "3": {"class_type": "VAELoader", "inputs": {
                "vae_name": "qwen_image_vae.safetensors"}},
            "4": {"class_type": "ModelSamplingAuraFlow", "inputs": {
                "model": ["1", 0], "shift": settings.comfyui_image_shift}},
            "5": {"class_type": "CLIPTextEncode", "inputs": {
                "text": request.prompt, "clip": ["2", 0]}},
            "6": {"class_type": "CLIPTextEncode", "inputs": {
                "text": negative, "clip": ["2", 0]}},
            "7": {"class_type": "EmptySD3LatentImage", "inputs": {
                "width": width, "height": height, "batch_size": 1}},
            "8": {"class_type": "KSampler", "inputs": {
                "model": ["4", 0], "seed": seed,
                "steps": settings.comfyui_image_steps,
                "cfg": settings.comfyui_image_cfg,
                "sampler_name": "euler", "scheduler": "simple",
                "positive": ["5", 0], "negative": ["6", 0],
                "latent_image": ["7", 0], "denoise": 1.0}},
            "9": {"class_type": "VAEDecode", "inputs": {
                "samples": ["8", 0], "vae": ["3", 0]}},
            "10": {"class_type": "SaveImage", "inputs": {
                "images": ["9", 0], "filename_prefix": prefix}},
        }

    @staticmethod
    def _reference_workflow(
        request: ImageGenerationRequest, seed: int, prefix: str, image_names: list[str]
    ) -> dict:
        """Native FLUX.2 Dev reference-latent workflow (supports chained images)."""
        width = max(256, (int(request.width) // 16) * 16)
        height = max(256, (int(request.height) // 16) * 16)
        workflow: dict[str, dict] = {
            "1": {"class_type": "UNETLoader", "inputs": {
                "unet_name": "flux2_dev_fp8mixed.safetensors", "weight_dtype": "default"}},
            "2": {"class_type": "CLIPLoader", "inputs": {
                "clip_name": "mistral_3_small_flux2_bf16.safetensors",
                "type": "flux2", "device": "default"}},
            "3": {"class_type": "VAELoader", "inputs": {
                "vae_name": "full_encoder_small_decoder.safetensors"}},
            "4": {"class_type": "CLIPTextEncode", "inputs": {
                "text": request.prompt, "clip": ["2", 0]}},
            "5": {"class_type": "FluxGuidance", "inputs": {
                "conditioning": ["4", 0], "guidance": 4.0}},
            "6": {"class_type": "EmptyFlux2LatentImage", "inputs": {
                "width": width, "height": height, "batch_size": 1}},
            "7": {"class_type": "RandomNoise", "inputs": {"noise_seed": seed}},
            "8": {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "euler"}},
            "9": {"class_type": "Flux2Scheduler", "inputs": {
                "steps": max(20, settings.comfyui_image_steps), "width": width, "height": height}},
        }
        conditioning: list[object] = ["5", 0]
        for index, image_name in enumerate(image_names[:10]):
            load_id = str(20 + index * 4)
            scale_id = str(21 + index * 4)
            encode_id = str(22 + index * 4)
            ref_id = str(23 + index * 4)
            workflow[load_id] = {"class_type": "LoadImage", "inputs": {"image": image_name}}
            workflow[scale_id] = {"class_type": "ImageScaleToTotalPixels", "inputs": {
                "image": [load_id, 0], "upscale_method": "lanczos", "megapixels": 1.0,
                "resolution_steps": 1}}
            workflow[encode_id] = {"class_type": "VAEEncode", "inputs": {
                "pixels": [scale_id, 0], "vae": ["3", 0]}}
            workflow[ref_id] = {"class_type": "ReferenceLatent", "inputs": {
                "conditioning": conditioning, "latent": [encode_id, 0]}}
            conditioning = [ref_id, 0]
        workflow.update({
            "10": {"class_type": "BasicGuider", "inputs": {
                "model": ["1", 0], "conditioning": conditioning}},
            "11": {"class_type": "SamplerCustomAdvanced", "inputs": {
                "noise": ["7", 0], "guider": ["10", 0], "sampler": ["8", 0],
                "sigmas": ["9", 0], "latent_image": ["6", 0]}},
            "12": {"class_type": "VAEDecode", "inputs": {
                "samples": ["11", 0], "vae": ["3", 0]}},
            "13": {"class_type": "SaveImage", "inputs": {
                "images": ["12", 0], "filename_prefix": prefix}},
        })
        return workflow

    @staticmethod
    async def _upload_references(
        client: httpx.AsyncClient, base: str, references: list[str]
    ) -> list[str]:
        uploaded_names: list[str] = []
        for index, raw_path in enumerate(references[:10]):
            source = Path(raw_path)
            if not source.is_file():
                continue
            mime = mimetypes.guess_type(source.name)[0] or "application/octet-stream"
            upload = await client.post(
                f"{base}/upload/image",
                files={"image": (f"ref_{index}_{source.name}", source.read_bytes(), mime)},
                data={"type": "input", "subfolder": "takecraft/image_refs", "overwrite": "true"},
            )
            upload.raise_for_status()
            result = upload.json()
            uploaded_names.append("/".join(
                part for part in (result.get("subfolder", ""), result["name"]) if part
            ))
        return uploaded_names

    async def generate(self, request: ImageGenerationRequest) -> list[GeneratedImage]:
        base = settings.comfyui_base_url.rstrip("/")
        timeout = httpx.Timeout(60.0, read=120.0)
        images: list[GeneratedImage] = []
        async with httpx.AsyncClient(timeout=timeout) as client:
            health = await client.get(f"{base}/system_stats")
            health.raise_for_status()
            reference_names = await self._upload_references(client, base, request.references)
            model_key = "flux2-reference" if reference_names else "qwen-image"
            await _prepare_comfyui_model(client, base, model_key)
            for index in range(max(1, request.count)):
                seed = (
                    int(request.seed) + index
                    if request.seed is not None
                    else random.SystemRandom().randrange(0, 2**48)
                )
                prefix = f"image/Takecraft_{uuid.uuid4().hex[:10]}"
                workflow = (
                    self._reference_workflow(request, seed, prefix, reference_names)
                    if reference_names
                    else self._workflow(request, seed, prefix)
                )
                queued = await client.post(
                    f"{base}/prompt",
                    json={"prompt": workflow, "client_id": f"takecraft-{uuid.uuid4()}"},
                )
                if queued.is_error:
                    raise RuntimeError(f"DGX 图片工作流提交失败: {queued.text[:1500]}")
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
                        messages = [
                            message[1]
                            for message in status.get("messages", [])
                            if message[0] == "execution_error"
                        ]
                        raise RuntimeError(
                            f"DGX 图片生成失败: "
                            f"{(messages[-1] if messages else status)}"
                        )
                    output_node = "13" if reference_names else "10"
                    files = record.get("outputs", {}).get(output_node, {}).get("images") or []
                    if not files:
                        raise RuntimeError(
                            f"DGX 图片生成完成但没有图片输出: {record.get('outputs', {})}"
                        )
                    item = files[0]
                    result = await client.get(
                        f"{base}/view",
                        params={
                            "filename": item["filename"],
                            "subfolder": item.get("subfolder", ""),
                            "type": item.get("type", "output"),
                        },
                    )
                    result.raise_for_status()
                    self.workdir.mkdir(parents=True, exist_ok=True)
                    destination = self.workdir / f"dgx_{prompt_id}_{index:02d}.png"
                    destination.write_bytes(result.content)
                    await client.post(f"{base}/history", json={"delete": [prompt_id]})
                    images.append(
                        GeneratedImage(
                            path=str(destination),
                            model="comfyui:flux2-reference" if reference_names else self.name,
                            seed=seed,
                            width=max(256, (int(request.width) // 16) * 16),
                            height=max(256, (int(request.height) // 16) * 16),
                        )
                    )
                    break
                else:
                    raise TimeoutError(f"DGX 图片任务 {prompt_id} 超过等待时间")
        return images

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
    """MiniMax H3 reference-to-video through a trusted ComfyUI HTTP endpoint.

    支持多图参考（角色 + 场景）和首尾帧衔接。
    """

    name = "comfyui:minimax-h3"

    def __init__(self, workdir: Path):
        self.workdir = workdir
        self.registry = CloudJobRegistry()
        self.workflow_path = Path(__file__).parent / "workflows" / "minimax_h3_r2v.api.json"

    async def _upload_image(self, client: httpx.AsyncClient, base: str, path: Path) -> str:
        """Upload image to ComfyUI and return the image name."""
        mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        upload = await client.post(
            f"{base}/upload/image",
            files={"image": (path.name, path.read_bytes(), mime)},
            data={"type": "input", "subfolder": "takecraft", "overwrite": "true"},
        )
        upload.raise_for_status()
        uploaded = upload.json()
        return "/".join(part for part in (uploaded.get("subfolder", ""), uploaded["name"]) if part)

    async def _generate(self, request: VideoGenerationRequest) -> GeneratedVideo:
        import logging
        logger = logging.getLogger(__name__)
        logger.info(f"[COMFYUI-R2V] shot={request.shot_id} first_frame={request.first_frame} ref_images={len(request.reference_images)}")
        print(f"[COMFYUI-R2V DEBUG] Starting generation for shot {request.shot_id}")
        print(f"[COMFYUI-R2V DEBUG] Prompt length: {len(request.prompt)}, References: {len(request.reference_images)}")

        # Collect all reference images: first_frame + reference_images
        all_images: list[Path] = []
        if request.first_frame and Path(request.first_frame).is_file():
            all_images.append(Path(request.first_frame))
        for p in request.reference_images:
            pp = Path(p)
            if pp.is_file() and pp not in all_images:
                all_images.append(pp)
        if not all_images:
            raise RuntimeError("DGX r2v 视频生成需要至少一张参考图（首帧或资产参考图）")
        logger.info(f"[COMFYUI-R2V] using {len(all_images)} images: {[p.name for p in all_images[:5]]}")
        print(f"[COMFYUI-R2V DEBUG] Collected {len(all_images)} reference images")

        base = settings.comfyui_base_url.rstrip("/")
        timeout = httpx.Timeout(60.0, read=120.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            print(f"[COMFYUI-R2V DEBUG] Connecting to ComfyUI at {base}")
            health = await client.get(f"{base}/system_stats")
            health.raise_for_status()
            print(f"[COMFYUI-R2V DEBUG] ComfyUI health check passed")
            await _prepare_comfyui_model(client, base, "minimax-h3")
            print(f"[COMFYUI-R2V DEBUG] Model preparation complete")

            # Upload all reference images
            image_names: list[str] = []
            for img_path in all_images:
                print(f"[COMFYUI-R2V DEBUG] Uploading {img_path.name}")
                name = await self._upload_image(client, base, img_path)
                image_names.append(name)
            print(f"[COMFYUI-R2V DEBUG] Uploaded {len(image_names)} images: {image_names[:3]}")

            workflow = json.loads(self.workflow_path.read_text(encoding="utf-8"))
            seed = random.SystemRandom().randrange(0, 2**48)

            # MiniMaxH3ReferenceToVideo exposes an auto-growing ref_images input
            # with a maximum of nine pictures.  These must be wired to
            # ``ref_images``; wiring image tensors to ``ref_videos`` makes the
            # workflow validate poorly and, more importantly, bypasses the
            # reference-image conditioning path.
            limited_image_names = image_names[:9]

            # Create LoadImage nodes for each reference image
            load_image_nodes = []
            for i, img_name in enumerate(limited_image_names):
                node_id = f"114_{i}" if i > 0 else "114"
                if i == 0:
                    workflow[node_id]["inputs"]["image"] = img_name
                else:
                    workflow[node_id] = {
                        "class_type": "LoadImage",
                        "inputs": {"image": img_name, "upload": "image"}
                    }
                load_image_nodes.append(node_id)

            # COMFY_AUTOGROW_V3 uses dotted materialized keys in API-format
            # workflows.  Supplying a normal list makes ComfyUI collapse a
            # one-item list to a Tensor, while this node expects a mapping and
            # calls ``ref_images.values()`` at execution time.
            workflow["104"]["inputs"].pop("ref_images", None)
            for index, node_id in enumerate(load_image_nodes):
                workflow["104"]["inputs"][
                    f"ref_images.ref_image_{index}"
                ] = [node_id, 0]
            workflow["104"]["inputs"]["ref_videos"] = []
            workflow["104"]["inputs"]["ref_video_audios"] = []
            workflow["104"]["inputs"]["ref_audios"] = []
            picture_tags = ", ".join(
                f"<Picture {index}>" for index in range(1, len(load_image_nodes) + 1)
            )
            workflow["104"]["inputs"]["prompt"] = (
                f"Reference images available: {picture_tags}. "
                f"Preserve the identities, costumes, props, architecture, and visual style "
                f"shown in those reference images. {request.prompt}"
            )
            # H3 consumes an explicit frame count; the template's fixed 124
            # frames only covers about five seconds.  Scene-level generation
            # can be longer, so round *up* to H3's legal 5 + 17n sequence.
            # Rounding up is important because the result is later split into
            # exact per-shot Takes and must cover the final segment completely.
            requested_frames = max(int(math.ceil(float(request.duration) * 24.0)), 124)
            h3_length = 5 + int(math.ceil((requested_frames - 5) / 17.0)) * 17
            workflow["104"]["inputs"]["length"] = min(h3_length, 3600)
            workflow["111"]["inputs"]["value"] = max(1.0, float(request.duration))
            workflow["115"]["inputs"]["aspect_ratio"] = "16:9 (Widescreen)"
            workflow["115"]["inputs"]["megapixels"] = settings.comfyui_megapixels
            workflow["15"]["inputs"]["noise_seed"] = seed
            workflow["92"]["inputs"]["filename_prefix"] = f"video/Takecraft_{request.shot_id}"

            print(f"[COMFYUI-R2V DEBUG] Submitting workflow with {len(limited_image_names)} reference images")
            queued = await client.post(
                f"{base}/prompt",
                json={"prompt": workflow, "client_id": f"takecraft-{uuid.uuid4()}"},
            )
            if queued.is_error:
                print(f"[COMFYUI-R2V DEBUG] Submission failed: {queued.text[:500]}")
                raise RuntimeError(f"DGX 工作流提交失败：{queued.text[:1500]}")
            prompt_id = queued.json()["prompt_id"]
            print(f"[COMFYUI-R2V DEBUG] Workflow submitted successfully, prompt_id={prompt_id}")
            deadline = asyncio.get_running_loop().time() + settings.comfyui_timeout
            while asyncio.get_running_loop().time() < deadline:
                await asyncio.sleep(POLL_INTERVAL)
                response = await client.get(f"{base}/history/{prompt_id}")
                response.raise_for_status()
                record = response.json().get(prompt_id)
                if not record:
                    continue
                status = record.get("status", {})
                print(f"[COMFYUI-R2V DEBUG] ComfyUI status: {status.get('status_str', 'unknown')}")
                if status.get("status_str") == "error":
                    messages = [m[1] for m in status.get("messages", []) if m[0] == "execution_error"]
                    error_detail = messages[-1] if messages else status
                    print(f"[COMFYUI-R2V DEBUG] ComfyUI execution error: {error_detail}")
                    raise RuntimeError(f"DGX 视频生成失败：{error_detail}")
                output = record.get("outputs", {}).get("92", {})
                files = output.get("images") or output.get("videos") or []
                if not files:
                    raise RuntimeError(f"DGX 任务完成但没有视频输出：{record.get('outputs', {})}")
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
