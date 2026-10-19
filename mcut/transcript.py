"""Normalize ASR output into a flat token list.

Token = one Chinese character or one English word, with start/end seconds and
the punctuation that follows it (FunASR's sentence_info splitting drifts by a
character, so punctuation is re-derived from the global punctuated text).
"""

import json
import re
from dataclasses import asdict, dataclass

PUNCT = "，。？！、；：,.?!;:"
_TOKEN_RE = re.compile(r"[A-Za-z0-9']+|[^\sA-Za-z0-9'" + re.escape(PUNCT) + r"]|[" + re.escape(PUNCT) + r"]")


@dataclass
class Token:
    i: int
    t: str
    s: float
    e: float
    p: str = ""  # punctuation right after this token


def from_funasr(path: str) -> list[Token]:
    d = json.load(open(path))
    stamps = [ts for s in d["sentences"] for ts in s["tokens"]]
    pieces = _TOKEN_RE.findall(d["text"])
    tokens: list[Token] = []
    for piece in pieces:
        if piece in PUNCT:
            if tokens:
                tokens[-1].p += piece
            continue
        k = len(tokens)
        if k >= len(stamps):
            raise ValueError(f"more text tokens than timestamps at {piece!r} (#{k})")
        tokens.append(Token(k, piece, stamps[k][0], stamps[k][1]))
    if len(tokens) != len(stamps):
        raise ValueError(f"{len(tokens)} text tokens vs {len(stamps)} timestamps")
    return tokens


def save(tokens: list[Token], path: str) -> None:
    json.dump([asdict(t) for t in tokens], open(path, "w"), ensure_ascii=False)


def load(path: str) -> list[Token]:
    return [Token(**t) for t in json.load(open(path))]
