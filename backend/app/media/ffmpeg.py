import shutil
import subprocess
from pathlib import Path

from ..config import settings

FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")


def ffmpeg_available() -> bool:
    return FFMPEG is not None


def probe_duration(path: Path) -> float | None:
    if not FFPROBE:
        return None
    result = subprocess.run(
        [
            FFPROBE,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=nw=1:nk=1",
            str(path),
        ],
        capture_output=True,
        text=True,
    )
    try:
        return float(result.stdout.strip())
    except ValueError:
        return None


def make_proxy(src: Path, dst: Path, height: int | None = None) -> Path | None:
    if not FFMPEG:
        return None
    dst.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        FFMPEG,
        "-y",
        "-i",
        str(src),
        "-vf",
        f"scale=-2:{height or settings.proxy_height}",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "23",
        "-c:a",
        "aac",
        "-movflags",
        "+faststart",
        str(dst),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        return None
    return dst
