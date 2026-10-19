"""Decide which tokens to cut: rule pass (filler sounds) + an AI pass (retakes, stutters).

The AI pass (Claude in the loop, or any OpenAI-compatible model) returns line ranges
to drop and exact fragments to delete; both are validated against the transcript, so
the model can only delete, never rewrite.
"""

import json
import re
from dataclasses import dataclass
from pathlib import Path

from mcut.transcript import _TOKEN_RE, PUNCT, Token

FILLERS = {"呃", "嗯", "额", "um", "uh", "umm", "uhm", "er", "erm", "hmm"}
PHRASE_GAP = 0.4  # seconds of silence that also ends a line
RULES = (Path(__file__).parent / "rules.md").read_text()


@dataclass
class Cut:
    start: int  # first token index (inclusive)
    end: int  # last token index (inclusive)
    reason: str
    source: str  # rule | claude | llm:<model> | user
    on: bool = True


def phrases(tokens: list[Token]) -> list[tuple[int, int]]:
    """(first, last) token index of each line, split at punctuation or pauses."""
    out, start = [], 0
    for k, t in enumerate(tokens):
        last = k == len(tokens) - 1
        if last or t.p or tokens[k + 1].s - t.e > PHRASE_GAP:
            out.append((start, k))
            start = k + 1
    return out


def line_text(tokens: list[Token], a: int, b: int) -> str:
    text = ""
    for t in tokens[a : b + 1]:
        if text and re.match(r"[A-Za-z0-9]", t.t) and re.search(r"[A-Za-z0-9]$", text):
            text += " "
        text += t.t + t.p
    return text


def numbered_lines(tokens: list[Token], ids: range | None = None) -> str:
    lines = phrases(tokens)
    ids = ids or range(len(lines))
    return "\n".join(f"{i}|{line_text(tokens, *lines[i])}" for i in ids)


def rule_cuts(tokens: list[Token]) -> list[Cut]:
    return [Cut(t.i, t.i, "filler", "rule") for t in tokens if t.t.lower() in FILLERS]


# ---------------------------------------------------------------- validating AI output


def _norm(s: str) -> list[str]:
    return [p.lower() for p in _TOKEN_RE.findall(s) if p not in PUNCT]


def keep_to_deleted(line_tokens: list[Token], keep: str) -> list[int] | None:
    """Token indices deleted by `keep`, matching right-to-left so the later copy of a
    stutter survives. None if `keep` is not a subsequence of the line."""
    want = _norm(keep)
    kept: set[int] = set()
    j = len(line_tokens) - 1
    for w in reversed(want):
        while j >= 0 and line_tokens[j].t.lower() != w:
            j -= 1
        if j < 0:
            return None
        kept.add(line_tokens[j].i)
        j -= 1
    return [t.i for t in line_tokens if t.i not in kept]


def fragment_tokens(line_tokens: list[Token], fragment: str) -> list[int] | None:
    """Token indices of the first occurrence of `fragment` in the line, matched
    character by character (a corrected token may hold 0 or several characters)."""
    want = "".join(_norm(fragment))
    chars, owner = [], []
    for t in line_tokens:
        for c in t.t.lower():
            chars.append(c)
            owner.append(t.i)
    pos = "".join(chars).find(want) if want else -1
    if pos < 0:
        return None
    a, b = owner[pos], owner[pos + len(want) - 1]
    # an English word must be taken whole
    if (pos > 0 and owner[pos - 1] == a) or (pos + len(want) < len(owner) and owner[pos + len(want)] == b):
        return None
    return list(range(a, b + 1))


def _runs(indices, reasons: dict | None = None) -> list[tuple[int, int]]:
    """Contiguous runs of token indices; with `reasons`, a run also breaks where the reason changes."""
    runs = []
    for i in sorted(indices):
        same = reasons is None or reasons.get(i) == reasons.get(runs[-1][1]) if runs else False
        if runs and i == runs[-1][1] + 1 and same:
            runs[-1][1] = i
        else:
            runs.append([i, i])
    return [tuple(r) for r in runs]


def apply_ai(tokens: list[Token], data: dict, source: str, ids: range | None = None) -> tuple[list[Cut], list[dict]]:
    """Validate {"drop": [...], "delete": [...]} and turn it into cuts.
    Returns (cuts, rejected items)."""
    lines = phrases(tokens)
    ids = ids or range(len(lines))
    deleted: dict[int, str] = {}
    rejected = []

    for d in data.get("drop", []):
        lo, hi = d.get("from"), d.get("to", d.get("from"))
        if not (isinstance(lo, int) and isinstance(hi, int) and lo in ids and hi in ids and lo <= hi):
            rejected.append({**d, "why": "line ids out of range"})
            continue
        for k in range(lines[lo][0], lines[hi][1] + 1):
            deleted.setdefault(k, d.get("reason", "retake"))
    for f in data.get("delete", []):
        i = f.get("id")
        if i not in ids:
            rejected.append({**f, "why": "line id out of range"})
            continue
        got = fragment_tokens(tokens[lines[i][0] : lines[i][1] + 1], f.get("text", ""))
        if got is None:
            rejected.append({**f, "why": "text not found in the line as whole characters/words (English words cannot be split): " + line_text(tokens, *lines[i])})
            continue
        for k in got:
            deleted.setdefault(k, f.get("reason", "stutter"))

    return [Cut(a, b, deleted[a], source) for a, b in _runs(deleted, deleted)], rejected


def parse_json(text: str) -> dict:
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S)
    a, b = text.find("{"), text.rfind("}")
    return json.loads(text[a : b + 1])


# ---------------------------------------------------------------- API models (no Claude in the loop)


def llm_cuts(tokens: list[Token], model: str, base_url: str | None = None, api_key: str | None = None,
             chunk: int = 150, overlap: int = 25, log=print) -> tuple[list[Cut], dict]:
    from openai import OpenAI

    client = OpenAI(base_url=base_url, api_key=api_key or "none")
    n_lines = len(phrases(tokens))
    source = f"llm:{model}"
    deleted: dict[int, str] = {}
    stats = {"model": model, "calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "rejected": []}

    for c0 in range(0, n_lines, chunk - overlap):
        ids = range(c0, min(c0 + chunk, n_lines))
        resp = client.chat.completions.create(
            model=model,
            temperature=0,
            messages=[{"role": "system", "content": RULES}, {"role": "user", "content": numbered_lines(tokens, ids)}],
        )
        stats["calls"] += 1
        if resp.usage:
            stats["prompt_tokens"] += resp.usage.prompt_tokens
            stats["completion_tokens"] += resp.usage.completion_tokens
        try:
            cuts, rejected = apply_ai(tokens, parse_json(resp.choices[0].message.content), source, ids)
        except (ValueError, AttributeError) as e:
            log(f"  lines {ids.start}-{ids.stop - 1}: unparseable response ({e})")
            continue
        stats["rejected"] += rejected
        for c in cuts:  # union across overlapping chunks
            for k in range(c.start, c.end + 1):
                deleted.setdefault(k, c.reason)
        log(f"  lines {ids.start}-{ids.stop - 1}: {len(cuts)} cuts")
        if ids.stop == n_lines:
            break

    return [Cut(a, b, deleted[a], source) for a, b in _runs(deleted)], stats
