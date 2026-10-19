"""Turn token-level cut decisions into a list of time ranges to keep, plus subtitles.

Cut boundaries are moved into silence using an RMS envelope of the audio, and pauses
are shortened (not removed) to a length that depends on the punctuation.
"""

import wave
from dataclasses import dataclass

import numpy as np

from mcut.transcript import Token

HOP = 0.01  # envelope resolution (s)
FPS = 30


@dataclass
class Settings:
    max_pause: float = 0.5  # longer silences get shortened
    pause_sentence: float = 0.45  # pause kept after 。？！ when we cut
    pause_comma: float = 0.3  # after ，、；：
    pause_word: float = 0.15  # mid-sentence (e.g. a stutter was removed)
    edge_pad: float = 0.04  # never cut closer than this to detected speech
    smooth: bool = True  # speed-ramp the picture across short cuts instead of jumping
    smooth_speed: float = 1.3  # preferred picture speed inside a ramp
    smooth_max_speed: float = 2.0  # cuts that would need more than this stay hard cuts
    smooth_room: float = 0.6  # max seconds of output on each side of a cut used for the ramp
    sub_hide_endings: str = "啊"  # particles dropped from subtitles where they end a clause (audio unchanged)


class Envelope:
    def __init__(self, wav_path: str):
        with wave.open(wav_path) as w:
            sr = w.getframerate()
            x = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768
        hop = int(sr * HOP)
        n = len(x) // hop
        rms = np.sqrt((x[: n * hop].reshape(n, hop) ** 2).mean(axis=1) + 1e-10)
        self.db = 20 * np.log10(rms)
        floor, speech = np.percentile(self.db, [10, 90])
        self.thr = floor + 0.3 * (speech - floor)

    def _f(self, t: float) -> int:
        return int(np.clip(t / HOP, 0, len(self.db) - 1))

    def speech_end(self, t: float, limit: float) -> float:
        """First quiet point at/after t (bounded by limit)."""
        a, b = self._f(t), self._f(limit)
        for k in range(a, b):
            if self.db[k] < self.thr:
                return k * HOP
        return self.valley(t, min(limit, t + 0.05))

    def speech_start(self, t: float, limit: float) -> float:
        """Last quiet point at/before t (bounded below by limit)."""
        a, b = self._f(t), self._f(limit)
        for k in range(a, b, -1):
            if self.db[k] < self.thr:
                return (k + 1) * HOP
        # continuous speech: ASR tends to end drawn-out fillers (呃/嗯) too early, so the
        # real boundary is usually a little after the next token's reported start
        return self.valley(max(limit, t - 0.05), t + 0.12)

    def valley(self, a: float, b: float) -> float:
        """Quietest point in [a, b] -- where to cut inside continuous speech."""
        i, j = self._f(a), max(self._f(b), self._f(a) + 1)
        return (i + int(np.argmin(self.db[i:j]))) * HOP


def _pause_for(tok: Token, s: Settings) -> float:
    if any(c in tok.p for c in "。？！?!."):
        return s.pause_sentence
    if tok.p:
        return s.pause_comma
    return s.pause_word


def keep_ranges(tokens: list[Token], deleted: set[int], env: Envelope, s: Settings = Settings()) -> list[tuple[float, float]]:
    kept = [t for t in tokens if t.i not in deleted]
    if not kept:
        return []

    def prev_end(t: Token) -> float:
        return tokens[t.i - 1].e if t.i > 0 else 0.0

    def next_start(t: Token) -> float:
        return tokens[t.i + 1].s if t.i + 1 < len(tokens) else t.e + 1.0

    def open_at(t: Token, want_lead: float) -> float:
        lo = prev_end(t)
        onset = env.speech_start(t.s, lo)
        return max(lo, onset - max(want_lead, s.edge_pad))

    def close_at(t: Token, want_tail: float) -> float:
        hi = next_start(t)
        offset = env.speech_end(t.e, hi)
        return min(hi, offset + max(want_tail, s.edge_pad))

    ranges = []
    start = open_at(kept[0], 0.15)
    for a, b in zip(kept, kept[1:]):
        gap = b.s - a.e
        if b.i == a.i + 1 and gap <= s.max_pause:
            continue  # contiguous, nothing to remove
        p = _pause_for(a, s)
        if b.i == a.i + 1:
            p = min(p, gap)
        ranges.append((start, close_at(a, p * 0.6)))
        start = open_at(b, p * 0.4)
    ranges.append((start, close_at(kept[-1], 0.3)))

    # merge overlaps, drop slivers (audio is cut exactly; video follows within half a frame)
    out = []
    for a, b in ranges:
        if b - a < 0.06:
            continue
        if out and a <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], b))
        else:
            out.append((a, b))
    return out


def video_map(ranges: list[tuple[float, float]], s: Settings = Settings(),
              joins: list[float] = ()) -> list[tuple[float, float, float, float]]:
    """Piecewise-linear picture timing as (src_start, src_end, out_start, out_end) pieces.

    Audio is cut exactly at the ranges. At a short cut the picture instead keeps
    running through the removed moment, played slightly faster over a window on
    both sides, so there is no visible jump; audio and picture realign at the window
    edges. Cuts that would need more than smooth_max_speed stay hard cuts, and so do
    the joins between clips of a joined video (`joins`, source seconds).
    """
    out_starts = np.concatenate([[0], np.cumsum([b - a for a, b in ranges])])
    # (w_left, w_right) of output time borrowed from each side of boundary k|k+1
    ramps: list[tuple[float, float] | None] = []
    for k in range(len(ranges) - 1):
        gap = ranges[k + 1][0] - ranges[k][1]
        at_join = any(ranges[k][1] - 0.05 <= j <= ranges[k + 1][0] + 0.05 for j in joins)
        if not s.smooth or gap <= 0 or at_join:
            ramps.append(None)
            continue
        want = gap / (2 * (s.smooth_speed - 1))
        left_room = (ranges[k][1] - ranges[k][0]) / (1 if k == 0 else 2)
        right_room = (ranges[k + 1][1] - ranges[k + 1][0]) / (1 if k + 2 == len(ranges) else 2)
        wl = min(want, left_room, s.smooth_room)
        wr = min(want, right_room, s.smooth_room)
        speed = (wl + wr + gap) / (wl + wr) if wl + wr > 0 else float("inf")
        ramps.append((wl, wr) if speed <= s.smooth_max_speed else None)

    pieces = []
    for k, (a, b) in enumerate(ranges):
        o = float(out_starts[k])
        lo = ramps[k - 1][1] if k > 0 and ramps[k - 1] else 0.0  # taken by the ramp on the left
        hi = ramps[k][0] if k < len(ramps) and ramps[k] else 0.0
        if b - a - lo - hi > 1e-6:
            pieces.append((a + lo, b - hi, o + lo, o + (b - a) - hi))
        if k < len(ramps) and ramps[k]:
            wl, wr = ramps[k]
            na = ranges[k + 1][0]
            pieces.append((b - wl, na + wr, o + (b - a) - wl, o + (b - a) + wr))
    return pieces


class Timeline:
    """Maps source time to output time for a list of kept ranges."""

    def __init__(self, ranges: list[tuple[float, float]]):
        self.ranges = ranges
        self.offsets = np.concatenate([[0], np.cumsum([b - a for a, b in ranges])])

    @property
    def duration(self) -> float:
        return float(self.offsets[-1])

    def map(self, t: float) -> float:
        for k, (a, b) in enumerate(self.ranges):
            if t < a:
                return float(self.offsets[k])
            if t <= b:
                return float(self.offsets[k] + t - a)
        return self.duration


# ---------------------------------------------------------------- subtitles

SUB_MAX = 16  # display width per subtitle (CJK char = 1, English word ~ len/2)
SUB_MERGE = 12  # short clauses are joined up to this width
SENTENCE_END = "。？！?!."


def _width(toks: list[Token]) -> int:
    return sum(len(t.t) if not t.t.isascii() else (len(t.t) + 1) // 2 for t in toks)


def _breaks(toks: list[Token]) -> list[int]:
    """Token positions k where a line may break between toks[k-1] and toks[k]
    without splitting a word (jieba segmentation)."""
    import jieba

    jieba.setLogLevel(60)
    starts, pos = {}, 0
    for k, t in enumerate(toks):
        starts[pos] = k
        pos += len(t.t)
    out, pos = [], 0
    for w in jieba.cut("".join(t.t for t in toks)):
        pos += len(w)
        if pos in starts and starts[pos] > 0:
            out.append(starts[pos])
    return out


def _split(toks: list[Token]) -> list[list[Token]]:
    if _width(toks) <= SUB_MAX:
        return [toks]
    total = _width(toks)

    def score(k: int) -> float:
        balance = abs(_width(toks[:k]) - total / 2) / total
        return toks[k].s - toks[k - 1].e - balance  # prefer pauses near the middle

    ks = _breaks(toks) or list(range(1, len(toks)))
    k = max(ks, key=score)
    return _split(toks[:k]) + _split(toks[k:])


def _join(toks: list[Token], hide_endings: str = "") -> str:
    text = ""
    for x in toks:
        last = x is toks[-1]
        if x.t and x.t in hide_endings and (x.p or last):  # e.g. a sentence-final 啊
            if x.p and not last and text and not text.endswith(" "):
                text += " "
            continue
        if text and x.t[:1].isascii() and x.t[:1].isalnum() and text[-1].isascii() and text[-1].isalnum():
            text += " "
        text += x.t
        if not last and x.p:
            text += " "  # Chinese subtitle convention: spaces instead of commas
    return text.strip()


@dataclass
class Cue:
    key: int  # index of the first token; subtitle text overrides are keyed by it
    last: int  # index of the last token
    src_s: float  # source time
    src_e: float
    s: float  # output time
    e: float
    text: str


def subtitle_cues(tokens: list[Token], deleted: set[int], tl: Timeline, overrides: dict[int, str] | None = None,
                  hide_endings: str = "") -> list[Cue]:
    overrides = overrides or {}
    kept = [t for t in tokens if t.i not in deleted]
    clauses, cur = [], []
    for k, t in enumerate(kept):
        cur.append(t)
        nxt = kept[k + 1] if k + 1 < len(kept) else None
        if nxt is None or t.p or nxt.s - t.e > 0.6:
            clauses.append(cur)
            cur = []

    merged = []
    for c in clauses:
        prev = merged[-1] if merged else None
        if prev and not any(ch in prev[-1].p for ch in SENTENCE_END) and _width(prev) + _width(c) <= SUB_MERGE:
            prev.extend(c)
        else:
            merged.append(list(c))

    cues = [
        Cue(g[0].i, g[-1].i, g[0].s, g[-1].e, tl.map(g[0].s), tl.map(g[-1].e), overrides.get(g[0].i, _join(g, hide_endings)))
        for c in merged
        for g in _split(c)
    ]
    for c, n in zip(cues, cues[1:]):
        if n.s - c.e < 0.5:  # close small gaps so subtitles don't flicker
            c.e = n.s
            c.src_e = n.src_s
    return [c for c in cues if c.e > c.s and c.text.strip()]


def to_srt(cues: list[Cue]) -> str:
    def ts(t: float) -> str:
        ms = int(round(t * 1000))
        return f"{ms // 3600000:02}:{ms // 60000 % 60:02}:{ms // 1000 % 60:02},{ms % 1000:03}"

    return "\n".join(f"{k}\n{ts(c.s)} --> {ts(c.e)}\n{c.text}\n" for k, c in enumerate(cues, 1))
