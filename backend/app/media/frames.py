"""审核前的抽帧与确定性画质闸门（规格补丁：AI Dailies 真实看片）。

只依赖 ffmpeg/ffprobe：项目环境没有 numpy/cv2/PIL，而 signalstats 已经给出
逐帧亮度（YAVG）与帧间亮度差（YDIF），足够做黑帧/静止/时长这类硬判定。
闸门命中就不再调用视觉模型——既省 token，也保证同一份坏素材判定可复现。
"""

import hashlib
import json
import subprocess
from pathlib import Path

from pydantic import BaseModel, Field

from ..config import settings
from .ffmpeg import FFMPEG, FFPROBE

# 亮度均值低于此值算黑帧；帧间亮度差低于此值算静止帧（YDIF 单位与 YAVG 同为 0~255）
BLACK_YAVG = 8.0
FREEZE_YDIF = 0.05
MIN_WIDTH = 256
MIN_HEIGHT = 144


class VideoProbe(BaseModel):
    duration: float = 0.0
    width: int = 0
    height: int = 0
    fps: float = 0.0
    frames: int = 0
    decodable: bool = False
    error: str = ""


class FrameSample(BaseModel):
    index: int  # 原视频帧号
    time: float
    path: str
    digest: str = ""


class VideoStats(BaseModel):
    probe: VideoProbe = Field(default_factory=VideoProbe)
    expected_duration: float = 0.0
    duration_error: float = 0.0
    yavg_mean: float = 0.0
    motion_mean: float = 0.0  # YDIF 均值：越大运动越明显
    flicker: float = 0.0  # 相邻帧亮度变化均值：越大越闪
    black_ratio: float = 0.0
    freeze_ratio: float = 0.0
    sampled_frames: int = 0

    def summary(self) -> str:
        """给视觉模型的事实性上下文（只描述测量值，不含评分）。"""
        p = self.probe
        return (
            f"时长 {p.duration:.2f}s（期望 {self.expected_duration:.2f}s，误差 {self.duration_error:.2f}s）"
            f"；分辨率 {p.width}x{p.height}；{p.frames} 帧 @ {p.fps:.1f}fps"
            f"；平均亮度 {self.yavg_mean:.1f}/255；黑帧占比 {self.black_ratio:.0%}"
            f"；帧间运动量 {self.motion_mean:.2f}；静止帧占比 {self.freeze_ratio:.0%}"
            f"；亮度闪烁 {self.flicker:.2f}；送审抽帧 {self.sampled_frames} 张"
        )


class GateFailure(BaseModel):
    code: str
    decision: str  # RETAKE | REJECT
    detail: str


def _escape_filter_path(path: Path) -> str:
    """filtergraph 里 , ; : ' \\ 都是语法字符，路径含这些字符必须转义。"""
    text = str(path)
    for ch in ("\\", "'", ":", ",", ";"):
        text = text.replace(ch, "\\" + ch)
    return text


def probe_video(path: Path) -> VideoProbe:
    if not FFPROBE or not path.exists():
        return VideoProbe(error=f"文件不可读: {path.name}")
    result = subprocess.run(
        [
            FFPROBE, "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=width,height,r_frame_rate,nb_frames,duration",
            "-of", "json", str(path),
        ],
        capture_output=True,
        text=True,
    )
    try:
        stream = (json.loads(result.stdout or "{}").get("streams") or [{}])[0]
    except json.JSONDecodeError as e:
        return VideoProbe(error=f"ffprobe 输出解析失败: {e}")

    def number(value: object) -> float:
        try:
            return float(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return 0.0

    fps = 0.0
    rate = str(stream.get("r_frame_rate") or "")
    if "/" in rate:
        num, _, den = rate.partition("/")
        if number(den):
            fps = number(num) / number(den)
    frames = int(number(stream.get("nb_frames")))
    duration = number(stream.get("duration"))
    if not frames and fps and duration:
        frames = int(round(fps * duration))
    return VideoProbe(
        duration=duration,
        width=int(number(stream.get("width"))),
        height=int(number(stream.get("height"))),
        fps=fps,
        frames=frames,
        decodable=bool(result.returncode == 0 and (frames or duration)),
        error=result.stderr.strip()[-200:] if result.returncode else "",
    )


def frame_stats(path: Path, expected_duration: float) -> VideoStats:
    probe = probe_video(path)
    stats = VideoStats(
        probe=probe,
        expected_duration=expected_duration,
        duration_error=abs(probe.duration - expected_duration)
        if probe.duration
        else expected_duration,
    )
    if not probe.decodable or not FFPROBE:
        return stats

    result = subprocess.run(
        [
            FFPROBE, "-v", "error", "-f", "lavfi",
            "-i", f"movie={_escape_filter_path(path)},signalstats",
            "-show_entries", "frame_tags=lavfi.signalstats.YAVG,lavfi.signalstats.YDIF",
            "-of", "csv=p=0",
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        stats.probe.error = result.stderr.strip()[-200:] or "signalstats 失败"
        stats.probe.decodable = False
        return stats

    brightness: list[float] = []
    motion: list[float] = []
    for line in result.stdout.splitlines():
        fields = [f for f in line.split(",") if f.strip()]
        if len(fields) < 2:
            continue
        try:
            brightness.append(float(fields[0]))
            motion.append(float(fields[1]))
        except ValueError:
            continue
    if not brightness:
        stats.probe.decodable = False
        stats.probe.error = "signalstats 未产出任何帧"
        return stats

    stats.probe.frames = len(brightness)
    stats.yavg_mean = sum(brightness) / len(brightness)
    stats.black_ratio = sum(1 for y in brightness if y < BLACK_YAVG) / len(brightness)
    # 首帧没有前帧可比，YDIF 恒为 0，计入静止比会虚高
    deltas = motion[1:]
    stats.motion_mean = sum(deltas) / len(deltas) if deltas else 0.0
    stats.freeze_ratio = (
        sum(1 for d in deltas if d < FREEZE_YDIF) / len(deltas) if deltas else 0.0
    )
    steps = [abs(brightness[i] - brightness[i - 1]) for i in range(1, len(brightness))]
    stats.flicker = sum(steps) / len(steps) if steps else 0.0
    return stats


def gate_failures(stats: VideoStats) -> list[GateFailure]:
    """确定性硬闸门。命中即短路：不再抽帧、不再调用视觉模型。"""
    probe = stats.probe
    failures: list[GateFailure] = []
    if not probe.decodable:
        failures.append(
            GateFailure(
                code="decode",
                decision="REJECT",
                detail=f"视频无法解码：{probe.error or '未知错误'}",
            )
        )
        return failures
    if stats.duration_error > settings.review_duration_tolerance:
        failures.append(
            GateFailure(
                code="duration",
                decision="RETAKE",
                detail=(
                    f"实际时长 {probe.duration:.2f}s 与镜头设定 "
                    f"{stats.expected_duration:.2f}s 相差 {stats.duration_error:.2f}s"
                    f"（容差 {settings.review_duration_tolerance:.2f}s）"
                ),
            )
        )
    if stats.black_ratio > settings.review_black_ratio_max:
        failures.append(
            GateFailure(
                code="blackout",
                decision="RETAKE",
                detail=f"黑帧占比 {stats.black_ratio:.0%}，画面基本不可用",
            )
        )
    # 阈值定得极端（默认 90%）：静止机位+静止主体是合法镜头，只有近乎静帧才算生成失败
    if stats.freeze_ratio > settings.review_freeze_ratio_max:
        failures.append(
            GateFailure(
                code="freeze",
                decision="RETAKE",
                detail=f"静止帧占比 {stats.freeze_ratio:.0%}，疑似输出为静帧",
            )
        )
    if probe.width < MIN_WIDTH or probe.height < MIN_HEIGHT:
        failures.append(
            GateFailure(
                code="resolution",
                decision="RETAKE",
                detail=f"分辨率 {probe.width}x{probe.height} 低于 {MIN_WIDTH}x{MIN_HEIGHT}",
            )
        )
    return failures


def sample_indices(total: int, count: int) -> list[int]:
    """均匀取 count 帧，首尾必含（首尾帧最容易暴露生成崩边）。"""
    if total <= 0:
        return []
    count = max(1, min(count, total))
    if count == 1 or total == 1:
        return [0]
    step = (total - 1) / (count - 1)
    picked = list(dict.fromkeys(int(round(i * step)) for i in range(count)))
    if picked[-1] != total - 1:
        picked.append(total - 1)
    return picked


def sample_frames(
    src: Path,
    out_dir: Path,
    probe: VideoProbe,
    count: int = 8,
    max_width: int = 896,
    quality: int = 5,
) -> list[FrameSample]:
    """一次 ffmpeg 调用抽出全部送审帧（JPEG），失败返回空列表由调用方判闸门。"""
    if not FFMPEG or not probe.decodable or not probe.frames:
        return []
    indices = sample_indices(probe.frames, count)
    if not indices:
        return []
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("frame_*.jpg"):
        old.unlink()
    select = "+".join(f"eq(n\\,{i})" for i in indices)
    cmd = [
        FFMPEG, "-y", "-v", "error", "-i", str(src),
        "-vf", f"select={select},scale=w='min({max_width},iw)':h=-2",
        "-frames:v", str(len(indices)), "-q:v", str(quality),
        str(out_dir / "frame_%03d.jpg"),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        return []

    samples: list[FrameSample] = []
    for position, index in enumerate(indices):
        file = out_dir / f"frame_{position + 1:03d}.jpg"
        if not file.exists():
            continue
        data = file.read_bytes()
        samples.append(
            FrameSample(
                index=index,
                time=(index / probe.fps) if probe.fps else 0.0,
                path=str(file),
                digest=hashlib.md5(data).hexdigest()[:12],
            )
        )
    return samples
