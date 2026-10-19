"""Second transcription with Qwen3-ASR (local, MLX on Apple Silicon).

An LLM-based recogniser: it uses sentence context, so it gets homophones right far
more often than Paraformer (墙/强, 篇/片, 要糖/药堂). It has no timestamps, so its text
is mapped onto Paraformer's tokens (see textfix.merge_second_opinion).

Run as a subprocess so MLX and torch never share a process:
    python -m mcut.asr_qwen <wav> <out.json> [hotword ...]
"""

import json
import sys
import time

MODEL = "mlx-community/Qwen3-ASR-1.7B-8bit"


def available() -> bool:
    try:
        import mlx_audio  # noqa: F401

        return True
    except ImportError:
        return False


def load_model():
    from mlx_audio.stt.utils import load_model as load

    return load(MODEL)


def transcribe(wav: str, out: str, hotwords: list[str]) -> None:
    model = load_model()
    t0 = time.time()
    # 10-minute chunks keep long recordings stable
    res = model.generate(wav, language="Chinese", hotwords=hotwords or None, chunk_duration=600.0)
    with open(out, "w") as f:
        json.dump({"engine": MODEL, "elapsed_s": time.time() - t0, "hotwords": hotwords, "text": res.text}, f, ensure_ascii=False)


if __name__ == "__main__":
    transcribe(sys.argv[1], sys.argv[2], sys.argv[3:])
