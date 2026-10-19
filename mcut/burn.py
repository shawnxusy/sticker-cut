"""Subtitle rendering. One renderer draws a subtitle as a transparent full-frame image;
the review page shows those images as its preview and the export overlays the very
same images on the video (fed to ffmpeg as one concat-demuxer stream), so what the
user styles is exactly what gets burned in. No libass needed."""

import functools
import io
import json
from dataclasses import asdict, dataclass, fields
from pathlib import Path

from PIL import Image, ImageColor, ImageDraw, ImageFilter, ImageFont

from mcut import ffmpeg
from mcut.plan import Cue

FONT_DIRS = [Path(__file__).parent.parent / "fonts", Path.home() / ".config/mcut/fonts"]
FONT_EXTS = {".ttf", ".otf", ".ttc"}
SYSTEM_FONTS = [  # default face, and fallback for characters a chosen font lacks
    ("/System/Library/Fonts/Hiragino Sans GB.ttc", 1),  # W6
    ("/System/Library/Fonts/STHeiti Medium.ttc", 0),
    ("/System/Library/Fonts/PingFang.ttc", 0),
    ("/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc", 0),
    ("C:/Windows/Fonts/msyhbd.ttc", 0),
    ("/Library/Fonts/Arial Unicode.ttf", 0),
]
DEFAULT_STYLE_FILE = Path.home() / ".config/mcut/style.json"


@dataclass
class Style:
    font: str = ""  # file name from FONT_DIRS; "" = system default (Hiragino Sans GB W6)
    size: float = 5.5  # % of the video's shorter side
    color: str = "#FFFFFF"
    outline_color: str = "#000000"
    outline: float = 10  # % of font size
    shadow: float = 35  # shadow opacity, 0-100
    shadow_blur: float = 8  # % of font size
    shadow_offset: float = 5  # % of font size, down and right
    box: float = 0  # background box opacity, 0-100
    box_color: str = "#000000"
    position: float = 80  # vertical centre, % of the frame height from the top

    @classmethod
    def from_dict(cls, d: dict | None) -> "Style":
        names = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in (d or {}).items() if k in names})

    @classmethod
    def default(cls) -> "Style":
        """The user's saved default (see save_default), else the built-in look."""
        try:
            return cls.from_dict(json.loads(DEFAULT_STYLE_FILE.read_text()))
        except (OSError, ValueError):
            return cls()

    def save_default(self) -> None:
        DEFAULT_STYLE_FILE.parent.mkdir(parents=True, exist_ok=True)
        DEFAULT_STYLE_FILE.write_text(json.dumps(asdict(self), ensure_ascii=False, indent=1))


# ---------------------------------------------------------------- fonts


def list_fonts() -> list[dict]:
    out = []
    for d in FONT_DIRS:
        if d.is_dir():
            out += [{"id": p.name, "name": p.stem} for p in sorted(d.iterdir()) if p.suffix.lower() in FONT_EXTS]
    return out


def _font_path(font_id: str) -> Path | None:
    for d in FONT_DIRS:
        p = d / font_id
        if font_id and p.is_file():
            return p
    return None


def _system_font() -> tuple[str, int]:
    for path, index in SYSTEM_FONTS:
        if Path(path).exists():
            return path, index
    raise FileNotFoundError("no CJK system font found; add one to mcut/burn.py SYSTEM_FONTS")


@functools.cache
def _coverage(path: str) -> frozenset[int]:
    from fontTools.ttLib import TTFont

    return frozenset((TTFont(path, lazy=True, fontNumber=0).getBestCmap() or {}).keys())


@functools.cache
def _load(path: str, index: int, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(path, size, index=index)


def _runs(text: str, style: Style, size: int) -> list[tuple[str, ImageFont.FreeTypeFont]]:
    """Split text into runs drawn with the chosen font, falling back to the system font
    for characters it doesn't have."""
    sys_path, sys_index = _system_font()
    fallback = _load(sys_path, sys_index, size)
    chosen = _font_path(style.font)
    if not chosen:
        return [(text, fallback)]
    main, have = _load(str(chosen), 0, size), _coverage(str(chosen))
    runs: list[tuple[str, ImageFont.FreeTypeFont]] = []
    for ch in text:
        f = main if ord(ch) in have or ch.isspace() else fallback
        if runs and runs[-1][1] is f:
            runs[-1] = (runs[-1][0] + ch, f)
        else:
            runs.append((ch, f))
    return runs


# ---------------------------------------------------------------- drawing


def _rgba(color: str, alpha: float = 100) -> tuple[int, int, int, int]:
    r, g, b = ImageColor.getrgb(color)[:3]
    return r, g, b, round(255 * max(0.0, min(alpha, 100)) / 100)


def render_subtitle(text: str, size: tuple[int, int], style: Style) -> Image.Image:
    """Transparent full-frame image with the subtitle drawn on it."""
    w, h = size
    frame = Image.new("RGBA", size, (0, 0, 0, 0))
    if not text.strip():
        return frame

    fs = max(8, round(min(w, h) * style.size / 100))
    runs = _runs(text, style, fs)
    width = sum(f.getlength(t) for t, f in runs)
    if width > w * 0.92:  # too wide for the frame: shrink to fit
        fs = max(8, int(fs * w * 0.92 / width))
        runs = _runs(text, style, fs)
        width = sum(f.getlength(t) for t, f in runs)

    stroke = round(fs * style.outline / 100)
    ascent, descent = runs[0][1].getmetrics()
    pad = stroke + round(fs * (style.shadow_blur + style.shadow_offset) / 100) + round(fs * 0.4)
    band_w, band_h = int(width) + 2 * pad, ascent + descent + 2 * pad
    band = Image.new("RGBA", (band_w, band_h), (0, 0, 0, 0))
    baseline = pad + ascent

    def draw_text(img: Image.Image, dx: int, dy: int, fill, stroke_fill) -> None:
        d, x = ImageDraw.Draw(img), pad + dx
        for t, f in runs:
            d.text((x, baseline + dy), t, font=f, anchor="ls", fill=fill, stroke_width=stroke, stroke_fill=stroke_fill)
            x += f.getlength(t)

    if style.box > 0:
        box_pad = round(fs * 0.3)
        ImageDraw.Draw(band).rounded_rectangle(
            (pad - box_pad - stroke, pad - box_pad, pad + width + box_pad + stroke, pad + ascent + descent + box_pad),
            radius=round(fs * 0.25), fill=_rgba(style.box_color, style.box))
    if style.shadow > 0:
        off = round(fs * style.shadow_offset / 100)
        shadow = Image.new("RGBA", band.size, (0, 0, 0, 0))
        black = _rgba("#000000", style.shadow)
        draw_text(shadow, off, off, black, black)
        shadow = shadow.filter(ImageFilter.GaussianBlur(fs * style.shadow_blur / 100))
        band = Image.alpha_composite(band, shadow)
    draw_text(band, 0, 0, _rgba(style.color), _rgba(style.outline_color))

    x = max(0, round((w - band_w) / 2))
    y = round(h * style.position / 100 - pad - (ascent + descent) / 2)
    frame.alpha_composite(band.crop((0, 0, min(band_w, w), band_h)), (x, max(0, min(y, h - band_h))))
    return frame


def png_bytes(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, "PNG", compress_level=1)
    return buf.getvalue()


@functools.cache
def frame_size(video: str) -> tuple[int, int]:
    """Display size of the video after rotation metadata is applied."""
    png = ffmpeg.run(["-i", video, "-frames:v", "1", "-f", "image2pipe", "-c:v", "png", "-"], capture_output=True).stdout
    return Image.open(io.BytesIO(png)).size


def cue_stream(cues: list[Cue], size: tuple[int, int], tmp: str, duration: float, style: Style) -> str:
    """Write one PNG per cue plus an ffconcat list timed on the output timeline."""
    blank = f"{tmp}/blank.png"
    Image.new("RGBA", size, (0, 0, 0, 0)).save(blank)

    entries, t = [], 0.0
    for k, c in enumerate(cues):
        if c.s > t:
            entries.append((blank, c.s - t))
        path = f"{tmp}/cue{k:05}.png"
        render_subtitle(c.text, size, style).save(path, compress_level=1)
        entries.append((path, c.e - max(c.s, t)))
        t = c.e
    entries.append((blank, max(duration - t, 0.04)))
    entries.append((blank, 0.04))  # concat demuxer ignores the last duration

    lst = f"{tmp}/cues.ffconcat"
    Path(lst).write_text("ffconcat version 1.0\n" + "".join(f"file '{p}'\nduration {d:.3f}\n" for p, d in entries))
    return lst


def font_sample(font_id: str, label: str, height: int = 44) -> bytes:
    """Small preview of a font for the picker: its name drawn in itself."""
    style = Style(font=font_id, size=100, outline=0, shadow=0, color="#222222")
    runs = _runs(label, style, round(height * 0.7))
    w = int(sum(f.getlength(t) for t, f in runs)) + 8
    img = Image.new("RGBA", (max(w, 1), height), (0, 0, 0, 0))
    d, x = ImageDraw.Draw(img), 4
    for t, f in runs:
        d.text((x, height * 0.78), t, font=f, anchor="ls", fill=(30, 30, 30, 255))
        x += f.getlength(t)
    return png_bytes(img)
