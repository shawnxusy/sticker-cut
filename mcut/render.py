"""Render the edit: audio stitched in numpy with short crossfades, video cut with
ffmpeg's select filter, subtitles burned in (optional) and written as .srt."""

import tempfile
import wave
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from mcut import burn, ffmpeg
from mcut.burn import Style
from mcut.plan import FPS, Cue, Envelope, Settings, Timeline, keep_ranges, subtitle_cues, to_srt, video_map
from mcut.project import Project, State

SR = 48000
XFADE = 0.02  # seconds of overlap at each join


@dataclass
class Plan:
    ranges: list[tuple[float, float]]
    timeline: Timeline
    cues: list[Cue]
    settings: Settings
    style: Style


def make_plan(proj: Project, st: State, env: Envelope | None = None, window: tuple[float, float] | None = None) -> Plan:
    tokens = proj.display_tokens(st)
    deleted = st.deleted()
    if window:
        deleted |= {t.i for t in tokens if t.s < window[0] or t.e > window[1]}
    ranges = keep_ranges(tokens, deleted, env or Envelope(str(proj.audio)), st.settings)
    tl = Timeline(ranges)
    cues = subtitle_cues(tokens, deleted, tl, st.subs, st.settings.sub_hide_endings)
    return Plan(ranges, tl, cues, st.settings, st.style)


def decode_audio(video: str, start: float, end: float) -> np.ndarray:
    raw = ffmpeg.run(["-ss", f"{start}", "-to", f"{end}", "-i", video, "-vn", "-ac", "2", "-ar", str(SR), "-f", "f32le", "-"],
                     capture_output=True).stdout
    return np.frombuffer(raw, dtype=np.float32).reshape(-1, 2)


def _sample_edges(ranges: list[tuple[float, float]]) -> list[int]:
    """Output sample index where each piece starts (plus the total), rounded from the
    cumulative duration so rounding never accumulates."""
    acc, edges = 0.0, [0]
    for a, b in ranges:
        acc += b - a
        edges.append(round(acc * SR))
    return edges


def stitch_audio(x: np.ndarray, ranges: list[tuple[float, float]], offset: float) -> np.ndarray:
    """Each piece is taken XFADE/2 wider on both sides and overlap-added with an
    equal-power crossfade, so the output length is exactly sum(b - a)."""
    h = int(XFADE * SR / 2)
    edges = _sample_edges(ranges)
    out = np.zeros((edges[-1] + 2 * h, 2), dtype=np.float32)
    ramp = np.sin(np.linspace(0, np.pi / 2, 2 * h, dtype=np.float32))[:, None]
    for (a, _), pos, end in zip(ranges, edges, edges[1:]):
        n = end - pos
        s0 = round((a - offset) * SR) - h
        seg = np.zeros((n + 2 * h, 2), dtype=np.float32)
        lo, hi = max(s0, 0), min(s0 + n + 2 * h, len(x))
        seg[lo - s0 : hi - s0] = x[lo:hi]
        seg[: 2 * h] *= ramp
        seg[-2 * h :] *= ramp[::-1]
        out[pos : pos + n + 2 * h] += seg
    return out[h : h + edges[-1]]


def video_filters(pieces: list[tuple[float, float, float, float]], offset: float) -> str:
    """select + setpts chain implementing the picture timing from plan.video_map:
    keep the source frames each piece needs, retime them onto the output timeline,
    then resample to a constant frame rate (dropping frames inside speed ramps)."""
    keep, pts = [], []
    for s0, s1, o0, o1 in pieces:
        s0, s1 = s0 - offset, s1 - offset
        keep.append(f"between(t,{s0:.4f},{s1:.4f})")
        rate = (o1 - o0) / (s1 - s0)
        pts.append(f"gte(T,{s0:.4f})*lt(T,{s1:.4f})*({o0:.4f}+(T-{s0:.4f})*{rate:.6f})")
    return f"fps={FPS},select='{'+'.join(keep)}',setpts='({'+'.join(pts)})/TB',fps={FPS}"


def write_wav(path: str, x: np.ndarray) -> None:
    pcm = (np.clip(x, -1, 1) * 32767).astype(np.int16)
    with wave.open(path, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())


def render(proj: Project, plan: Plan, out: Path, preview: bool = False, burn_subs: bool = True, progress=None) -> Path:
    """progress(fraction) is called while encoding."""
    video = str(proj.video)
    ranges = plan.ranges
    start, end = ranges[0][0], ranges[-1][1]
    out.parent.mkdir(parents=True, exist_ok=True)
    out.with_suffix(".srt").write_text(to_srt(plan.cues))

    with tempfile.TemporaryDirectory() as tmp:
        wav = f"{tmp}/audio.wav"
        write_wav(wav, stitch_audio(decode_audio(video, start, end), ranges, start))
        inputs = ["-ss", f"{start}", "-to", f"{end}", "-i", video, "-i", wav]
        graph = f"[0:v]{video_filters(video_map(ranges, plan.settings), start)}"
        if burn_subs and plan.cues:
            cues = burn.cue_stream(plan.cues, burn.frame_size(video), tmp, plan.timeline.duration, plan.style)
            inputs += ["-f", "concat", "-safe", "0", "-i", cues]
            graph += "[cut];[cut][2:v]overlay=0:0:eof_action=pass"
        graph += (",scale=-2:960" if preview else "") + "[v]"
        script = f"{tmp}/graph.txt"
        Path(script).write_text(graph)

        args = [*inputs, "-filter_complex_script", script, "-map", "[v]", "-map", "1:a",
                *ffmpeg.h264_args("4M" if preview else "14M"), "-c:a", "aac", "-b:a", "192k",
                "-movflags", "+faststart", "-progress", "pipe:1", str(out)]
        proc = ffmpeg.popen(args, stdout=-1, text=True)
        total = plan.timeline.duration
        for line in proc.stdout:
            value = line.strip().removeprefix("out_time_us=")
            if progress and line.startswith("out_time_us=") and value.isdigit():
                progress(min(int(value) / 1e6 / total, 1.0))
        if proc.wait():
            raise RuntimeError(f"ffmpeg failed with exit code {proc.returncode}")
    return out
