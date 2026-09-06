import shutil
import subprocess
from pathlib import Path

from ..config import settings

def _resolve_binary(name: str) -> str | None:
    """Resolve FFmpeg tools from PATH or the repository-local Windows bundle.

    The backend is also launched directly by tests, IDEs and recovery scripts, so
    it must not rely on Start-Takecraft-ModelScope.cmd having amended PATH first.
    """
    from_path = shutil.which(name)
    if from_path:
        return from_path

    repo_root = Path(__file__).resolve().parents[3]
    executable = f"{name}.exe" if not name.lower().endswith(".exe") else name
    candidates = sorted((repo_root / ".tools").glob(f"ffmpeg-*/bin/{executable}"))
    return str(candidates[-1]) if candidates else None


FFMPEG = _resolve_binary("ffmpeg")
FFPROBE = _resolve_binary("ffprobe")


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


def trim_to_duration(src: Path, dst: Path, duration: float) -> Path | None:
    """Re-encode a generated clip to the exact screenplay duration.

    Video models emit only legal frame counts, so a 1.5-second request may be
    returned as 1.625 seconds. Re-encoding before Take/proxy creation keeps the
    source, UI duration and final timeline consistent.
    """
    if not FFMPEG or not src.is_file() or duration <= 0:
        return None
    dst.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [
            FFMPEG, "-y", "-i", str(src), "-t", f"{duration:.3f}",
            "-c:v", "libx264", "-preset", "fast", "-crf", "18",
            "-c:a", "aac", "-movflags", "+faststart", str(dst),
        ],
        capture_output=True,
        text=True,
    )
    return dst if result.returncode == 0 and dst.is_file() else None


def extract_tail_frame(src: Path, dst: Path, offset: float = 0.12) -> Path | None:
    """提取片尾前的清晰帧，供同场景下一镜作为首帧。"""
    if not FFMPEG or not src.is_file():
        return None
    dst.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [FFMPEG, "-y", "-sseof", f"-{max(offset, 0.04):.3f}", "-i", str(src),
         "-frames:v", "1", "-update", "1", str(dst)],
        capture_output=True,
        text=True,
    )
    return dst if result.returncode == 0 and dst.is_file() else None
