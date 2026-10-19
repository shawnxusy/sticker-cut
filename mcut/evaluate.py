"""Score a cuts JSON against a hand-labelled gold file (token-level precision/recall).

Usage: uv run python -m mcut.evaluate work/gold/IMG_2524_0-180.json work/cuts/<run>.json
"""

import json
import sys

from mcut.edit import keep_to_deleted, line_text, phrases
from mcut.transcript import load


def main(gold_path: str, cuts_path: str) -> None:
    gold = json.load(open(gold_path))
    tokens = load(gold["tokens"])
    lines = phrases(tokens)
    lo, hi = gold["lines"]
    span = set(range(lines[lo][0], lines[hi][1] + 1))

    want = set()
    for ed in gold["edits"]:
        a, b = lines[ed["id"]]
        want.update(keep_to_deleted(tokens[a : b + 1], ed["keep"]))
    got = set()
    for c in json.load(open(cuts_path))["cuts"]:
        if c.get("on", True):
            got.update(k for k in range(c["start"], c["end"] + 1) if k in span)

    tp = len(want & got)
    p = tp / len(got) if got else 0
    r = tp / len(want) if want else 0
    print(f"precision {p:.2f}  recall {r:.2f}  (gold {len(want)} tokens, predicted {len(got)})")
    show = lambda ks: ", ".join(f"{i}:{''.join(tokens[k].t for k in range(a, b + 1) if k in ks)}" for i, (a, b) in enumerate(lines[lo : hi + 1], lo) if any(k in ks for k in range(a, b + 1)))
    print("missed:", show(want - got))
    print("extra: ", show(got - want))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
