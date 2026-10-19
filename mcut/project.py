"""A project is a folder next to the video (<stem>.mcut/) holding everything derived
from it, so work can be resumed or moved to another machine."""

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from mcut.burn import Style
from mcut.edit import Cut
from mcut.plan import Settings
from mcut.textfix import Fix, display_tokens
from mcut.transcript import Token, load

VIDEO_EXTS = {".mov", ".mp4", ".m4v", ".mkv"}


@dataclass
class Project:
    video: Path
    dir: Path

    @classmethod
    def open(cls, path: str) -> "Project":
        """Accepts the video file or its .mcut folder."""
        p = Path(path).expanduser().resolve()
        if p.suffix == ".mcut" and p.is_dir():
            meta = json.loads((p / "project.json").read_text())
            return cls(Path(meta["video"]), p)
        if not p.is_file():
            raise FileNotFoundError(p)
        proj = cls(p, p.with_name(p.stem + ".mcut"))
        proj.dir.mkdir(exist_ok=True)
        (proj.dir / "project.json").write_text(json.dumps({"video": str(p)}, ensure_ascii=False))
        return proj

    @property
    def name(self) -> str:
        return self.video.stem

    def path(self, name: str) -> Path:
        return self.dir / name

    audio = property(lambda self: self.path("audio16k.wav"))
    asr = property(lambda self: self.path("asr.json"))
    tokens = property(lambda self: self.path("tokens.json"))
    lines = property(lambda self: self.path("lines.txt"))
    qwen = property(lambda self: self.path("asr_qwen.json"))
    proxy = property(lambda self: self.path("proxy.mp4"))
    output = property(lambda self: self.path(f"{self.video.stem}_edited.mp4"))

    # ---- edit state: cuts + subtitle text overrides + settings
    def load_state(self) -> "State":
        f = self.path("cuts.json")
        if not f.exists():
            return State()
        d = json.loads(f.read_text())
        return State(
            cuts=[Cut(**c) for c in d.get("cuts", [])],
            subs={int(k): v for k, v in d.get("subs", {}).items()},
            settings=Settings(**d.get("settings", {})),
            fixes=[Fix(**f) for f in d.get("fixes", [])],
            style=Style.from_dict(d["style"]) if "style" in d else Style.default(),
        )

    def save_state(self, st: "State") -> None:
        d = {"cuts": [asdict(c) for c in st.cuts], "fixes": [asdict(f) for f in st.fixes],
             "subs": {str(k): v for k, v in st.subs.items()}, "settings": asdict(st.settings), "style": asdict(st.style)}
        self.path("cuts.json").write_text(json.dumps(d, ensure_ascii=False, indent=1))

    def raw_tokens(self) -> list[Token]:
        return load(str(self.tokens))

    def display_tokens(self, st: "State | None" = None) -> list[Token]:
        """Timed tokens with all text corrections applied -- what Claude, the page and
        the subtitles see."""
        return display_tokens(self.raw_tokens(), (st or self.load_state()).fixes)


@dataclass
class State:
    cuts: list[Cut] = field(default_factory=list)
    subs: dict[int, str] = field(default_factory=dict)  # cue key (first token index) -> corrected text
    settings: Settings = field(default_factory=Settings)
    fixes: list[Fix] = field(default_factory=list)  # text corrections on token spans
    style: Style = field(default_factory=Style.default)  # subtitle look

    def deleted(self) -> set[int]:
        out = set()
        for c in self.cuts:
            if c.on:
                out.update(range(c.start, c.end + 1))
        return out

    def replace_fixes(self, source: str, fixes: list[Fix]) -> None:
        self.fixes = [f for f in self.fixes if f.source != source] + fixes

    def add_fixes(self, fixes: list[Fix]) -> None:
        """Newer fixes replace older ones of the same source on the same span."""
        spans = {(f.start, f.end, f.source) for f in fixes}
        self.fixes = [f for f in self.fixes if (f.start, f.end, f.source) not in spans] + fixes

    def replace_source(self, prefix: str, cuts: list[Cut]) -> None:
        """Swap in a fresh set of cuts from one source (rule / claude / llm:<model>)."""
        self.cuts = [c for c in self.cuts if not c.source.startswith(prefix)] + cuts
        self.cuts.sort(key=lambda c: c.start)
