import asyncio
import colorsys
import hashlib
import subprocess
import uuid
from pathlib import Path

from .base import (
    GeneratedImage,
    GeneratedVideo,
    ImageGenerationRequest,
    VideoGenerationRequest,
)


def _hex_color(seed: int, offset: float) -> str:
    hue = ((seed * 137.508 + offset * 60.0) % 360) / 360
    r, g, b = colorsys.hsv_to_rgb(hue, 0.72, 0.92)
    return f"0x{int(r * 255):02x}{int(g * 255):02x}{int(b * 255):02x}"


def _run(cmd: list[str]) -> None:
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg 失败: {result.stderr[-300:]}")


def _prompt_offset(prompt: str) -> int:
    return int(hashlib.md5(prompt.encode()).hexdigest()[:6], 16) % 997


class MockImageGenerator:
    """无 GPU 环境的本地图像后端：ffmpeg 渐变，按 seed 变化颜色与方向。"""

    name = "mock-image"

    def __init__(self, workdir: Path):
        self.workdir = workdir

    async def generate(self, request: ImageGenerationRequest) -> list[GeneratedImage]:
        self.workdir.mkdir(parents=True, exist_ok=True)
        images: list[GeneratedImage] = []
        batch = uuid.uuid4().hex[:8]
        base_seed = (
            request.seed
            if request.seed is not None
            else uuid.uuid4().int % 100000
        )
        view_offset = _prompt_offset(request.prompt)
        for i in range(request.count):
            seed = (base_seed + view_offset + i * 977) % 100000
            dst = self.workdir / f"img_{batch}_{i:02d}.png"
            colors = 2 + seed % 5
            params = [
                f"gradients=s={request.width}x{request.height}",
                f"nb_colors={colors}",
                f"seed={seed}",
                f"x0={seed % request.width}",
                f"y0={(seed * 7) % request.height}",
                f"x1={(seed * 13) % request.width}",
                f"y1={(seed * 29) % request.height}",
            ]
            for c in range(colors):
                params.append(f"c{c}={_hex_color(seed, c)}")
            cmd = [
                "ffmpeg", "-y", "-loglevel", "error",
                "-f", "lavfi", "-i", ":".join(params),
                "-frames:v", "1", "-update", "1",
                str(dst),
            ]
            await asyncio.to_thread(_run, cmd)
            images.append(
                GeneratedImage(
                    path=str(dst),
                    model=self.name,
                    seed=seed,
                    width=request.width,
                    height=request.height,
                )
            )
        return images


class MockVideoGenerator:
    """无 GPU 环境的本地视频后端：testsrc2 + 噪点 + 正弦音，按 seed 变化色调。"""

    name = "mock-video"

    def __init__(self, workdir: Path):
        self.workdir = workdir
        self._tasks: dict[str, asyncio.Task] = {}
        self._results: dict[str, GeneratedVideo] = {}
        self._errors: dict[str, str] = {}
        self._cancelled: set[str] = set()

    async def submit(self, request: VideoGenerationRequest) -> str:
        job_id = f"mock_{uuid.uuid4().hex[:12]}"
        task = asyncio.create_task(self._run_job(job_id, request))
        self._tasks[job_id] = task
        return job_id

    async def _run_job(self, job_id: str, request: VideoGenerationRequest) -> None:
        dst = self.workdir / f"mock_{job_id}.mp4"
        try:
            self.workdir.mkdir(parents=True, exist_ok=True)
            seed = uuid.uuid4().int % 100000
            duration = max(request.duration, 1.0)
            freq = 220 + (seed % 660)
            noise = 4 + seed % 12
            filters = [
                f"hue=h={seed % 360}:s=1.4",
                f"noise=alls={noise}:allf=t",
            ]
            cmd = [
                "ffmpeg", "-y", "-loglevel", "error",
                "-f", "lavfi", "-i",
                f"testsrc2=size=1280x720:rate=24:duration={duration:.3f}",
                "-f", "lavfi", "-i", f"sine=frequency={freq}:duration={duration:.3f}",
                "-vf", ",".join(filters),
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
                "-c:a", "aac", "-ar", "48000", "-ac", "2",
                "-shortest", "-movflags", "+faststart",
                str(dst),
            ]
            await asyncio.to_thread(_run, cmd)
            if job_id in self._cancelled:
                dst.unlink(missing_ok=True)
                raise asyncio.CancelledError()
            self._results[job_id] = GeneratedVideo(
                path=str(dst),
                model=self.name,
                seed=seed,
                duration=duration,
            )
        except asyncio.CancelledError:
            dst.unlink(missing_ok=True)
            raise
        except Exception as e:  # noqa: BLE001
            self._errors[job_id] = str(e)

    async def status(self, job_id: str) -> str:
        if job_id in self._errors:
            return "FAILED"
        if job_id in self._results:
            return "DONE"
        task = self._tasks.get(job_id)
        if task is None:
            return "UNKNOWN"
        if task.cancelled():
            return "CANCELLED"
        if task.done():
            return "CANCELLED" if job_id in self._cancelled else "FAILED"
        return "RUNNING"

    async def cancel(self, job_id: str) -> None:
        self._cancelled.add(job_id)
        task = self._tasks.get(job_id)
        if task is not None and not task.done():
            task.cancel()

    async def result(self, job_id: str) -> GeneratedVideo:
        result = self._results.get(job_id)
        if result is None:
            error = self._errors.get(job_id, "job 未完成")
            raise RuntimeError(error)
        self._cleanup(job_id)
        return result

    def _cleanup(self, job_id: str) -> None:
        self._tasks.pop(job_id, None)
        self._results.pop(job_id, None)
        self._errors.pop(job_id, None)
        self._cancelled.discard(job_id)


class ImageMotionVideoGenerator(MockVideoGenerator):
    """Turn a real storyboard frame into a subtle cinematic H.264 shot."""

    name = "image-motion"

    async def submit(self, request: VideoGenerationRequest) -> str:
        job_id = f"motion_{uuid.uuid4().hex[:12]}"
        task = asyncio.create_task(self._run_job(job_id, request))
        self._tasks[job_id] = task
        return job_id

    async def _run_job(self, job_id: str, request: VideoGenerationRequest) -> None:
        dst = self.workdir / f"{job_id}.mp4"
        try:
            source = Path(request.first_frame or "")
            if not request.first_frame or not source.is_file():
                raise RuntimeError("镜头缺少可用的首帧分镜图，无法生成真实画面视频")
            self.workdir.mkdir(parents=True, exist_ok=True)
            seed = uuid.uuid4().int % 100000
            duration = max(request.duration, 1.0)
            frames = max(round(duration * 24), 1)
            x_expr = f"(iw-iw/zoom)*on/{frames}" if seed % 2 else "iw/2-(iw/zoom/2)"
            video_filter = (
                "scale=1600:900:force_original_aspect_ratio=increase,"
                "crop=1600:900,"
                f"zoompan=z='min(zoom+0.0012,1.16)':x='{x_expr}':"
                f"y='ih/2-(ih/zoom/2)':d=1:s=1280x720:fps=24,format=yuv420p"
            )
            cmd = [
                "ffmpeg", "-y", "-loglevel", "error", "-loop", "1", "-i", str(source),
                "-vf", video_filter, "-frames:v", str(frames), "-c:v", "libx264",
                "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", "-an",
                "-movflags", "+faststart", str(dst),
            ]
            await asyncio.to_thread(_run, cmd)
            if job_id in self._cancelled:
                dst.unlink(missing_ok=True)
                raise asyncio.CancelledError()
            self._results[job_id] = GeneratedVideo(
                path=str(dst), model=self.name, seed=seed, duration=duration
            )
        except asyncio.CancelledError:
            dst.unlink(missing_ok=True)
            raise
        except Exception as e:  # noqa: BLE001
            self._errors[job_id] = str(e)