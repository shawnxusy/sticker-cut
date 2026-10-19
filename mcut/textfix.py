"""Text corrections layered over the timed tokens.

Paraformer's tokens carry the timing and never change. Corrections are stored as
fixes on token spans (from the second recogniser, from Claude's proofreading, or from
the user), so they survive any change to the cuts. `display_tokens` applies them.
"""

import difflib
import re
from dataclasses import dataclass, replace

from mcut.transcript import PUNCT, Token

PRIORITY = {"qwen": 0, "claude": 1, "user": 2}  # later sources win where they overlap
FILLER_CHARS = set("呃嗯啊额哦")


@dataclass
class Fix:
    start: int  # first token index (inclusive)
    end: int  # last token index (inclusive)
    text: str
    source: str  # qwen | claude | user


def _spread(text: str, n: int) -> list[str]:
    """Distribute replacement text over n tokens: one unit (a CJK character or a whole
    English word) per token, the remainder on the last, empty tokens if it runs out."""
    if n == 1:
        return [text]
    units = re.findall(r"[A-Za-z0-9']+\s*|.", text)
    head = units[: n - 1]
    return head + [""] * (n - 1 - len(head)) + ["".join(units[n - 1 :])]


def display_tokens(tokens: list[Token], fixes: list[Fix]) -> list[Token]:
    out = [replace(t) for t in tokens]
    for f in sorted(fixes, key=lambda f: (PRIORITY.get(f.source, 9), f.start)):
        for k, piece in zip(range(f.start, f.end + 1), _spread(f.text, f.end - f.start + 1)):
            out[k].t = piece
    return out


def diff_fixes(toks: list[Token], new_text: str, source: str, keep_deletes: bool = True,
               skip_fillers: bool = False) -> list[Fix]:
    """Fixes that turn the text of `toks` (a contiguous run) into `new_text`."""
    new = "".join(c for c in new_text if c not in PUNCT)
    new = re.sub(r"\s+", " ", new)
    new = re.sub(r"(?<![A-Za-z0-9]) | (?![A-Za-z0-9])", "", new).strip()  # keep spaces only between English words
    chars, owner = [], []
    for t in toks:
        for c in t.t:
            chars.append(c)
            owner.append(t.i)
    old = "".join(chars)
    first = toks[0].i
    by_index = {t.i: t for t in toks}

    fixes = []
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, old, new, autojunk=False).get_opcodes():
        if op == "equal" or (op == "delete" and not keep_deletes):
            continue
        if skip_fillers and (set(old[i1:i2]) & FILLER_CHARS or set(new[j1:j2]) & FILLER_CHARS):
            continue
        if op == "insert":  # attach to the token before (or the first token)
            k = owner[i1 - 1] if i1 > 0 else first
            t = by_index[k]
            text = t.t + new[j1:j2] if i1 > 0 else new[j1:j2] + t.t
            fixes.append(Fix(k, k, text, source))
            continue
        a, b = owner[i1], owner[i2 - 1]
        lead = "".join(c for c, o in zip(chars[:i1], owner[:i1]) if o == a)
        tail = "".join(c for c, o in zip(chars[i2:], owner[i2:]) if o == b)
        block = sorted(set(owner[i1:i2]))
        if block == list(range(a, b + 1)):
            fixes.append(Fix(a, b, lead + new[j1:j2] + tail, source))
        else:  # the tokens aren't adjacent (cut tokens sit between them): one fix per token
            fixes.append(Fix(a, a, lead + new[j1:j2], source))
            fixes += [Fix(k, k, "", source) for k in block[1:-1]]
            fixes.append(Fix(b, b, tail, source))
    return fixes


def merge_second_opinion(tokens: list[Token], text: str) -> list[Fix]:
    """Fixes that adopt the second recogniser's wording. Only substitutions and
    insertions are taken (never deletions), and filler sounds are left alone."""
    return diff_fixes(tokens, text, "qwen", keep_deletes=False, skip_fillers=True)


def replace_in_line(line_toks: list[Token], old: str, new: str, source: str) -> list[Fix] | None:
    """Fixes for replacing the first occurrence of `old` in a line; None if absent."""
    text = "".join(t.t for t in line_toks)
    old = "".join(c for c in old if c not in PUNCT and not c.isspace())
    if not old or old not in text:
        return None
    return diff_fixes(line_toks, text.replace(old, new, 1), source)
