"""Join several recordings into one video before processing, with plain cuts between
them. Clips from the same camera/settings are joined without re-encoding; others are
converted to the first clip's format. The clip boundaries are kept in the project so
Claude can spot a section re-recorded in a later clip (a retake across clips)."""

import re
import subprocess
import tempfile
from pathlib import Path

from mcut import ffmpeg


def _signature(path: str) -> dict:
    """Codec parameters that must match for a lossless (stream copy) join."""
    err = subprocess.run([ffmpeg.exe(), "-hide_banner", "-i", path], capture_output=True, text=True).stderr
    video = re.search(r"Stream #0:\d+.*?: Video: (\w+).*?, (\d+)x(\d+)[, ].*?([\d.]+) fps", err)
    audio = re.search(r"Stream #0:\d+.*?: Audio: (\w+).*?, (\d+) Hz, (\w+)", err)
    rotation = re.search(r"rotation of (-?[\d.]+) degrees", err)
    if not video:
        raise ValueError(f"no video stream in {path}")
    return {
        "video": video.groups(),
        "audio": audio.groups() if audio else None,
        "rotation": rotation.group(1) if rotation else "0",
    }


def join(paths: list[str], out: str, log=print) -> list[dict]:
    """Write the joined video; returns [{"name", "start", "end"}] in seconds."""
    sigs = [_signature(p) for p in paths]
    clips, t = [], 0.0
    for p in paths:
        d = ffmpeg.duration(p)
        clips.append({"name": Path(p).name, "start": round(t, 3), "end": round(t + d, 3)})
        t += d

    with tempfile.TemporaryDirectory() as tmp:
        if all(s == sigs[0] for s in sigs):
            log(f"joining {len(paths)} clips without re-encoding...")
            lst = Path(tmp) / "list.txt"
            lst.write_text("".join(f"file '{Path(p).resolve()}'\n" for p in paths))
            ffmpeg.run(["-f", "concat", "-safe", "0", "-i", str(lst), "-map", "0:v:0", "-map", "0:a:0",
                        "-c", "copy", "-movflags", "+faststart", out])
        else:
            from mcut.burn import frame_size

            log(f"clips differ in format; converting {len(paths)} clips to match the first...")
            dw, dh = frame_size(paths[0])  # display size after rotation
            fps, n = sigs[0]["video"][3], len(paths)
            inputs, parts = [], []
            for k, p in enumerate(paths):
                inputs += ["-i", p]
                parts.append(f"[{k}:v]scale={dw}:{dh}:force_original_aspect_ratio=decrease,"
                             f"pad={dw}:{dh}:(ow-iw)/2:(oh-ih)/2,fps={fps},setsar=1,"
                             # frames are already upright: drop the rotation flag so players don't turn them again
                             f"sidedata=mode=delete:type=DISPLAYMATRIX[v{k}];"
                             f"[{k}:a]aresample=48000,aformat=channel_layouts=stereo[a{k}]")
            graph = ";".join(parts) + ";" + "".join(f"[v{k}][a{k}]" for k in range(n)) + f"concat=n={n}:v=1:a=1[v][a]"
            ffmpeg.run([*inputs, "-filter_complex", graph, "-map", "[v]", "-map", "[a]",
                        *ffmpeg.h264_args("18M"), "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", out])
    return clips
