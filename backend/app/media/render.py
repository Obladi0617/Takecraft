import subprocess
import time
from pathlib import Path

from ..db import abs_path, project_path
from ..domain import Take, TimelineClip

RENDER_WIDTH = 1280
RENDER_HEIGHT = 720
RENDER_FPS = 24


def probe_has_audio(path: Path) -> bool:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "a",
            "-show_entries",
            "stream=index",
            "-of",
            "csv=p=0",
            str(path),
        ],
        capture_output=True,
        text=True,
    )
    return bool(result.stdout.strip())


def _segment_cmd(src: Path, dst: Path, ss: float, dur: float, volume: float) -> list[str]:
    vf = (
        f"scale={RENDER_WIDTH}:{RENDER_HEIGHT}:force_original_aspect_ratio=decrease,"
        f"pad={RENDER_WIDTH}:{RENDER_HEIGHT}:(ow-iw)/2:(oh-ih)/2,"
        f"setsar=1,fps={RENDER_FPS}"
    )
    if probe_has_audio(src):
        return [
            "ffmpeg", "-y", "-loglevel", "error",
            "-ss", f"{ss:.3f}", "-i", str(src), "-t", f"{dur:.3f}",
            "-vf", vf,
            "-af", f"volume={volume:.2f}",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-c:a", "aac", "-ar", "48000", "-ac", "2",
            str(dst),
        ]
    return [
        "ffmpeg", "-y", "-loglevel", "error",
        "-ss", f"{ss:.3f}", "-i", str(src),
        "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
        "-t", f"{dur:.3f}",
        "-map", "0:v", "-map", "1:a",
        "-vf", vf,
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-c:a", "aac", "-ar", "48000", "-ac", "2",
        str(dst),
    ]


def render_timeline(project_id: str, clips: list[tuple[TimelineClip, Take]]) -> dict:
    if not clips:
        raise ValueError("时间线为空，无法渲染")

    render_id = time.strftime("%Y%m%d_%H%M%S")
    out_rel = f"renders/final_{render_id}.mp4"
    out = abs_path(project_id, out_rel)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp_dir = project_path(project_id) / "renders" / f"tmp_{render_id}"
    tmp_dir.mkdir(parents=True, exist_ok=True)

    segments: list[Path] = []
    try:
        for i, (clip, take) in enumerate(clips):
            src = abs_path(project_id, take.original_path)
            if not src.exists():
                raise FileNotFoundError(f"素材缺失: {take.original_path}")
            source_out = clip.source_out if clip.source_out is not None else take.duration
            dur = max(source_out - clip.source_in, 0.1)
            seg = tmp_dir / f"seg_{i:03d}.mp4"
            result = subprocess.run(
                _segment_cmd(src, seg, clip.source_in, dur, clip.volume),
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                raise RuntimeError(
                    f"片段 {clip.id} 渲染失败: {result.stderr[-400:]}"
                )
            segments.append(seg)

        concat_list = tmp_dir / "list.txt"
        concat_list.write_text(
            "\n".join(f"file '{seg.name}'" for seg in segments), encoding="utf-8"
        )
        result = subprocess.run(
            [
                "ffmpeg", "-y", "-loglevel", "error",
                "-f", "concat", "-safe", "0", "-i", str(concat_list),
                "-c", "copy", str(out),
            ],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(f"拼接失败: {result.stderr[-400:]}")
    finally:
        for seg in segments:
            seg.unlink(missing_ok=True)
        (tmp_dir / "list.txt").unlink(missing_ok=True)
        tmp_dir.rmdir()

    total = sum(
        max(
            (c.source_out if c.source_out is not None else t.duration) - c.source_in,
            0.0,
        )
        for c, t in clips
    )
    return {"id": render_id, "path": out_rel, "duration": round(total, 2)}
