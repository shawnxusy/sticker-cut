"""ffmpeg access. Uses the binary bundled with imageio-ffmpeg so a fresh machine needs
nothing besides uv; set MCUT_FFMPEG to use another build."""

import functools
import os
import re
import subprocess


@functools.cache
def exe() -> str:
    if path := os.environ.get("MCUT_FFMPEG"):
        return path
    import imageio_ffmpeg

    return imageio_ffmpeg.get_ffmpeg_exe()


def run(args: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run([exe(), "-nostdin", "-hide_banner", "-v", "error", "-y", *args], check=True, **kw)


def popen(args: list[str], **kw) -> subprocess.Popen:
    return subprocess.Popen([exe(), "-nostdin", "-hide_banner", "-v", "error", "-y", *args], **kw)


@functools.cache
def h264_args(bitrate: str) -> tuple[str, ...]:
    """Hardware H.264 on Macs, libx264 elsewhere (or if VideoToolbox is unavailable)."""
    try:
        run(["-f", "lavfi", "-i", "testsrc=size=320x240:rate=30", "-frames:v", "3", "-c:v", "h264_videotoolbox", "-f", "null", "-"],
            capture_output=True)
        return ("-c:v", "h264_videotoolbox", "-b:v", bitrate)
    except subprocess.CalledProcessError:
        return ("-c:v", "libx264", "-preset", "veryfast", "-b:v", bitrate)


def duration(path: str) -> float:
    err = subprocess.run([exe(), "-hide_banner", "-i", path], capture_output=True, text=True).stderr
    m = re.search(r"Duration: (\d+):(\d+):([\d.]+)", err)
    if not m:
        raise ValueError(f"cannot read duration of {path}")
    h, mi, s = m.groups()
    return int(h) * 3600 + int(mi) * 60 + float(s)
