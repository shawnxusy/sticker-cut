"""Transcribe a 16 kHz mono wav with FunASR Paraformer-zh (local, free).

Writes a JSON with sentences and per-token (char / English word) timestamps.
Models (~1 GB) are downloaded from ModelScope on first use.
"""

import json
import logging
import time
from pathlib import Path


def load_model():
    """Loads (and on first use downloads) the speech models."""
    import jieba
    from funasr import AutoModel

    jieba.setLogLevel(logging.ERROR)  # the punctuation model segments with jieba
    logging.getLogger().setLevel(logging.ERROR)
    return AutoModel(
        model="paraformer-zh",
        vad_model="fsmn-vad",
        punc_model="ct-punc",
        disable_update=True,
        disable_pbar=True,
        disable_log=True,
    )


def transcribe(wav: str, out: str) -> dict:
    model = load_model()
    t0 = time.time()
    res = model.generate(input=wav, batch_size_s=300, sentence_timestamp=True, disable_pbar=True)[0]
    elapsed = time.time() - t0

    sentences = [
        {
            "start": s["start"] / 1000,
            "end": s["end"] / 1000,
            "text": s["text"],
            "tokens": [[a / 1000, b / 1000] for a, b in s.get("timestamp", [])],
        }
        for s in res.get("sentence_info", [])
    ]
    d = {"engine": "funasr-paraformer-zh", "elapsed_s": elapsed, "text": res["text"], "sentences": sentences}
    Path(out).write_text(json.dumps(d, ensure_ascii=False, indent=1))
    return d
