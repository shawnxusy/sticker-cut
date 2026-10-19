"""Recurring names and terms (artists, brands, series) the recognisers can't know.
Fed to Qwen3-ASR as hotwords and shown to Claude when proofreading.
One term per line in ~/.config/mcut/glossary.txt (override with MCUT_GLOSSARY)."""

import os
from pathlib import Path


def path() -> Path:
    return Path(os.environ.get("MCUT_GLOSSARY", Path.home() / ".config/mcut/glossary.txt"))


def load() -> list[str]:
    p = path()
    if not p.exists():
        return []
    return [ln.strip() for ln in p.read_text().splitlines() if ln.strip() and not ln.startswith("#")]


def save(terms: list[str]) -> None:
    p = path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("# mcut glossary: one name/term per line\n" + "".join(f"{t}\n" for t in dict.fromkeys(terms)))
