"""mcut command line. Typical flow:

    mcut join A B C              (optional) join several recordings into one video first
    mcut prep VIDEO              transcribe (2 recognisers) + automatic cuts (fillers, pauses)
    mcut diffs / fix VIDEO       proofread: where the recognisers disagreed / apply text fixes
    mcut lines VIDEO             numbered transcript lines (for Claude / a model)
    mcut apply VIDEO cuts.json   add retake/stutter cuts decided by Claude
    mcut cues / subs VIDEO       list subtitles / fix recognition errors in them
    mcut review VIDEO            browser review page; export from there
    mcut render VIDEO            or render without the page
"""

import argparse
import json
import warnings
import os
import sys
import time
from pathlib import Path

warnings.filterwarnings("ignore", category=SyntaxWarning)  # jieba's old regex literals

from mcut import ffmpeg, glossary
from mcut.edit import RULES, apply_ai, line_text, llm_cuts, phrases, rule_cuts
from mcut.project import Project
from mcut.textfix import merge_second_opinion, replace_in_line
from mcut.transcript import from_funasr, load, save


def fmt(sec: float) -> str:
    tenths = round(sec * 10)
    return f"{tenths // 600}:{tenths % 600 / 10:04.1f}"


def cmd_prep(a) -> None:
    proj = Project.open(a.video)
    print(f"project: {proj.dir}")
    if a.force or not proj.audio.exists():
        ffmpeg.run(["-i", str(proj.video), "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(proj.audio)])
    proxy = None
    if a.force or not proj.proxy.exists():  # low-res copy for the review page, encoded while ASR runs
        proxy = ffmpeg.popen(["-i", str(proj.video), "-vf", "scale=-2:960", *ffmpeg.h264_args("2M"),
                              "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart", str(proj.proxy) + ".part.mp4"])
    from mcut import asr_qwen

    qwen = None
    if asr_qwen.available() and (a.force or not proj.qwen.exists()):  # second opinion on wording, on the GPU
        import subprocess

        qwen = subprocess.Popen([sys.executable, "-m", "mcut.asr_qwen", str(proj.audio), str(proj.qwen), *glossary.load()],
                                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    t0 = time.time()
    if a.force or not proj.asr.exists():
        from mcut.asr_funasr import transcribe

        print(f"transcribing {fmt(ffmpeg.duration(str(proj.video)))} of audio "
              "(first run downloads ~4.5 GB of speech models)...", flush=True)
        transcribe(str(proj.audio), str(proj.asr))
    tokens = from_funasr(str(proj.asr))
    for j in proj.joins:  # a clip join always ends a sentence (lines and subtitles break there)
        before = [t for t in tokens if t.e <= j + 0.05]
        if before and not before[-1].p:
            before[-1].p = "。"
    save(tokens, str(proj.tokens))
    if qwen:
        _, err = qwen.communicate()
        if qwen.returncode:
            print(f"warning: Qwen3-ASR failed, using Paraformer text only\n{err[-800:]}", file=sys.stderr)
    if a.force or not proj.asr.exists() or qwen:
        print(f"  transcribed in {time.time() - t0:.0f}s")

    st = proj.load_state()
    st.replace_source("rule", rule_cuts(tokens))
    if proj.qwen.exists():
        st.replace_fixes("qwen", merge_second_opinion(tokens, json.loads(proj.qwen.read_text())["text"]))
    proj.save_state(st)
    write_lines(proj, st)
    if proxy:
        if proxy.wait():
            print("warning: proxy encode failed; review page will play the original file", file=sys.stderr)
        else:
            Path(str(proj.proxy) + ".part.mp4").rename(proj.proxy)
            print(f"review proxy: {proj.proxy}")

    n_lines = len(phrases(tokens))
    print(f"{len(tokens)} tokens, {n_lines} lines -> {proj.lines}")
    n_q = sum(f.source == "qwen" for f in st.fixes)
    print(f"wording: {n_q} places where Qwen3-ASR's reading replaced Paraformer's (see mcut diffs)" if proj.qwen.exists()
          else "wording: Paraformer only (Qwen3-ASR needs Apple Silicon)")
    print(f"automatic cuts: {sum(c.source == 'rule' for c in st.cuts)} filler sounds; long pauses are shortened at render time")
    done_claude = sum(c.source == "claude" for c in st.cuts)
    done_user = sum(c.source == "user" for c in st.cuts) + sum(f.source == "user" for f in st.fixes)
    if done_claude or done_user:
        print(f"EXISTING WORK: {done_claude} cuts from Claude and {done_user} edits by the user are already in this project "
              "(kept). This video was processed before -- skip to the review page unless the user asks to redo the cuts.")
    else:
        print("next: proofread (mcut diffs / mcut fix), decide retake/stutter cuts (mcut rules, mcut apply), then mcut review")


def write_lines(proj: Project, st) -> None:
    proj.lines.write_text(lines_with_clips(proj, proj.display_tokens(st)) + "\n")


def lines_with_clips(proj: Project, tokens, ids: range | None = None) -> str:
    """Numbered lines; for a joined video, a `--- clip N: name ---` marker where each clip starts."""
    lines = phrases(tokens)
    clips = proj.clips if len(proj.clips) > 1 else []
    out, k = [], 0
    for i in ids or range(len(lines)):
        while k < len(clips) and tokens[lines[i][0]].s >= clips[k]["start"] - 0.05:
            out.append(f"--- clip {k + 1}: {clips[k]['name']} ---")
            k += 1
        out.append(f"{i}|{line_text(tokens, *lines[i])}")
    return "\n".join(out)


def cmd_join(a) -> None:
    from mcut.join import join

    first = Path(a.videos[0]).expanduser().resolve()
    out = Path(a.output).expanduser().resolve() if a.output else first.with_name(f"{first.stem}_joined{first.suffix}")
    if out.exists() and not a.force:
        sys.exit(f"{out} already exists (use --force to overwrite, or -o for another name)")
    clips = join([str(Path(v).expanduser().resolve()) for v in a.videos], str(out))
    proj = Project.open(str(out))
    proj.set_clips(clips)
    for k, c in enumerate(clips, 1):
        print(f"  clip {k}: {c['name']}  {fmt(c['start'])} - {fmt(c['end'])}")
    print(f"joined -> {out}\nnext: mcut prep \"{out}\"")


def cmd_rules(a) -> None:
    print(RULES)


def cmd_lines(a) -> None:
    proj = Project.open(a.video)
    tokens = proj.display_tokens()
    n = len(phrases(tokens))
    print(lines_with_clips(proj, tokens, range(a.start, min(a.end + 1 if a.end is not None else n, n))))


def _summary(proj: Project, st) -> None:
    from mcut.render import make_plan

    plan = make_plan(proj, st)
    src = ffmpeg.duration(str(proj.video))
    by = {}
    for c in st.cuts:
        if c.on:
            by[f"{c.source}/{c.reason}"] = by.get(f"{c.source}/{c.reason}", 0) + 1
    print(f"edited length {fmt(plan.timeline.duration)} of {fmt(src)} ({100 * plan.timeline.duration / src:.0f}%), "
          f"{len(plan.ranges)} pieces, {len(plan.cues)} subtitles")
    print("cuts: " + ", ".join(f"{k} {v}" for k, v in sorted(by.items())))


def cmd_apply(a) -> None:
    proj = Project.open(a.video)
    st = proj.load_state()
    tokens = proj.display_tokens(st)
    data = json.load(sys.stdin if a.file == "-" else open(a.file))
    cuts, rejected = apply_ai(tokens, data, a.source)
    st.replace_source(a.source, cuts)
    proj.save_state(st)

    lines = phrases(tokens)
    mine = {k for c in cuts for k in range(c.start, c.end + 1)}
    other = st.deleted() - mine
    print(f"applied {len(cuts)} cuts from {a.source} ({len(mine)} tokens)")
    if a.verbose:
        print("  [..] = your cuts, {..} = other cuts (automatic fillers, user edits)")
        for i, (lo, hi) in enumerate(lines):
            if not any(k in mine for k in range(lo, hi + 1)):
                continue
            shown, mark = "", None
            for t in tokens[lo : hi + 1]:
                m = "[]" if t.i in mine else "{}" if t.i in other else None
                if m != mark:
                    shown += (mark[1] if mark else "") + (m[0] if m else "")
                    mark = m
                shown += t.t
            print(f"  {i}| {shown + (mark[1] if mark else '')}")
    for r in rejected:
        print(f"REJECTED {json.dumps(r, ensure_ascii=False)}")
    _summary(proj, st)


def cmd_ai(a) -> None:
    proj = Project.open(a.video)
    tokens = proj.display_tokens()
    if not a.model:
        sys.exit("set --model or MCUT_LLM_MODEL (plus MCUT_LLM_BASE_URL / MCUT_LLM_API_KEY), e.g. an OpenRouter model id")
    cuts, stats = llm_cuts(tokens, a.model, a.base_url, a.api_key, log=print)
    st = proj.load_state()
    st.replace_source("llm:", cuts)
    proj.save_state(st)
    print(f"{len(cuts)} cuts; {stats['prompt_tokens']} prompt + {stats['completion_tokens']} completion tokens; "
          f"{len(stats['rejected'])} rejected")
    _summary(proj, st)


def cmd_diffs(a) -> None:
    """Where the two recognisers disagreed; the line shows Qwen3-ASR's reading (adopted)
    with Paraformer's in the margin."""
    proj = Project.open(a.video)
    st = proj.load_state()
    raw, shown = proj.raw_tokens(), proj.display_tokens(st)
    owner = {k: i for i, (lo, hi) in enumerate(phrases(raw)) for k in range(lo, hi + 1)}
    pronouns = 0
    for f in sorted((f for f in st.fixes if f.source == "qwen"), key=lambda f: f.start):
        i = owner[f.start]
        lo, hi = phrases(raw)[i]
        span = "".join(t.t for t in shown[f.start : f.end + 1])
        now = (line_text(shown, lo, f.start - 1) if f.start > lo else "") + f"[{span or '∅'}]" + \
              (line_text(shown, f.end + 1, hi) if f.end < hi else "")
        was = "".join(t.t for t in raw[f.start : f.end + 1])
        if span.strip("《》“”‘’\"'") == was:
            continue  # only punctuation differs, or the reading was already reverted
        if {span, was} <= {"他", "她", "它"}:
            pronouns += 1
            continue
        print(f"{i}| {now}    (Paraformer: {was or '-'})")
    if pronouns:
        print(f"({pronouns} 他/她/它-only differences not shown; Qwen3-ASR's 它 is used)")


def cmd_fix(a) -> None:
    """Apply text fixes {"fix": [{"id": line, "from": "...", "to": "..."}]}."""
    proj = Project.open(a.video)
    st = proj.load_state()
    data = json.load(sys.stdin if a.file == "-" else open(a.file))
    tokens = proj.display_tokens(st)
    lines = phrases(tokens)
    applied, rejected = 0, []
    for item in data.get("fix", []):
        i = item.get("id")
        if not isinstance(i, int) or not 0 <= i < len(lines):
            rejected.append({**item, "why": "line id out of range"})
            continue
        lo, hi = lines[i]
        fixes = replace_in_line(tokens[lo : hi + 1], item.get("from", ""), item.get("to", ""), a.source)
        if fixes is None and item.get("to") and item["to"] in line_text(tokens, lo, hi):
            continue  # already applied in an earlier run
        if fixes is None:
            rejected.append({**item, "why": f"'from' not found in line: {line_text(tokens, lo, hi)}"})
            continue
        st.add_fixes(fixes)
        before = line_text(tokens, lo, hi)
        tokens = proj.display_tokens(st)  # later fixes see earlier ones
        applied += 1
        print(f"  {i}| {before}  ->  {line_text(tokens, lo, hi)}")
    proj.save_state(st)
    write_lines(proj, st)
    print(f"applied {applied} text fixes from {a.source} (fixes accumulate across runs; a later fix on the same span wins)")
    for r in rejected:
        print(f"REJECTED {json.dumps(r, ensure_ascii=False)}")


def cmd_replace(a) -> None:
    proj = Project.open(a.video)
    st = proj.load_state()
    n = replace_all(proj, st, a.old, a.new, a.source)
    proj.save_state(st)
    write_lines(proj, st)
    print(f"replaced {n} occurrence(s) of {a.old} -> {a.new}")


def replace_all(proj: Project, st, old: str, new: str, source: str) -> int:
    from mcut.textfix import diff_fixes

    n = 0
    tokens = proj.display_tokens(st)
    for lo, hi in phrases(tokens):
        text = "".join(t.t for t in tokens[lo : hi + 1])
        if old and old != new and old in text:
            n += text.count(old)
            st.add_fixes(diff_fixes(tokens[lo : hi + 1], text.replace(old, new), source))
    return n


def cmd_glossary(a) -> None:
    terms = glossary.load()
    if a.action == "add":
        glossary.save(terms + a.terms)
    elif a.action == "remove":
        glossary.save([t for t in terms if t not in a.terms])
    print(f"{glossary.path()}:")
    print("\n".join(glossary.load()) or "(empty)")


def cmd_cues(a) -> None:
    from mcut.render import make_plan

    proj = Project.open(a.video)
    for c in make_plan(proj, proj.load_state()).cues:
        print(f"{c.key}|{fmt(c.s)}|{c.text}")


def cmd_subs(a) -> None:
    proj = Project.open(a.video)
    fixes = json.load(sys.stdin if a.file == "-" else open(a.file))
    st = proj.load_state()
    for k, v in fixes.items():
        if v:
            st.subs[int(k)] = v
        else:
            st.subs.pop(int(k), None)
    proj.save_state(st)
    print(f"{len(fixes)} subtitle fixes saved ({len(st.subs)} total)")


def cmd_status(a) -> None:
    proj = Project.open(a.video)
    _summary(proj, proj.load_state())


def cmd_render(a) -> None:
    from mcut.render import make_plan, render

    proj = Project.open(a.video)
    st = proj.load_state()
    if a.no_smooth:
        st.settings.smooth = False
    plan = make_plan(proj, st, window=a.window)
    out = Path(a.output) if a.output else proj.output.with_stem(proj.output.stem + ("_preview" if a.preview else ""))
    print(f"rendering {fmt(plan.timeline.duration)} -> {out}", flush=True)
    last = [0.0]

    def progress(f: float) -> None:
        if f - last[0] >= 0.1:
            last[0] = f
            print(f"  {f:.0%}", flush=True)

    render(proj, plan, out, preview=a.preview, burn_subs=not a.no_burn, progress=progress)
    print(f"wrote {out}\n      {out.with_suffix('.srt')}")


def cmd_review(a) -> None:
    from mcut.review.server import serve

    serve(Project.open(a.video), port=a.port, open_browser=not a.no_open)


def cmd_setup(a) -> None:
    from mcut import asr_qwen
    from mcut.asr_funasr import load_model

    print("downloading speech models (one time): Paraformer ~2 GB from ModelScope (~10-15 min), "
          "Qwen3-ASR ~2.5 GB from HuggingFace (~3-5 min)...", flush=True)
    load_model()
    if asr_qwen.available():
        asr_qwen.load_model()
    ffmpeg.h264_args("1M")
    print("ready")
    cmd_doctor(a)


def cmd_doctor(a) -> None:
    print(f"ffmpeg:  {ffmpeg.exe()}")
    print(f"encoder: {' '.join(ffmpeg.h264_args('1M')[:2])}")
    from mcut.burn import FONT_DIRS, _system_font, list_fonts

    print(f"font:    {_system_font()[0]} (default); {len(list_fonts())} more in {', '.join(map(str, FONT_DIRS))}")
    cache = Path(os.environ.get("MODELSCOPE_CACHE", Path.home() / ".cache/modelscope"))
    print(f"paraformer: {'cached' if any(cache.rglob('*paraformer*')) else 'not downloaded yet'} ({cache})")
    from mcut import asr_qwen

    if asr_qwen.available():
        hf = Path(os.environ.get("HF_HOME", Path.home() / ".cache/huggingface")) / "hub"
        name = "models--" + asr_qwen.MODEL.replace("/", "--")
        print(f"qwen3-asr:  {'cached' if (hf / name).exists() else 'not downloaded yet'} ({hf})")
    else:
        print("qwen3-asr:  unavailable (needs Apple Silicon)")
    print(f"glossary:   {len(glossary.load())} terms ({glossary.path()})")


def main() -> None:
    ap = argparse.ArgumentParser(prog="mcut", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(required=True)

    p = sub.add_parser("prep", help="transcribe and mark automatic cuts")
    p.add_argument("video")
    p.add_argument("--force", action="store_true", help="redo audio, proxy and transcription")
    p.set_defaults(fn=cmd_prep)

    p = sub.add_parser("join", help="join several recordings (in order) into one video, plain cuts between them")
    p.add_argument("videos", nargs="+")
    p.add_argument("-o", "--output", help="default: <first>_joined.<ext> next to the first clip")
    p.add_argument("--force", action="store_true")
    p.set_defaults(fn=cmd_join)

    p = sub.add_parser("diffs", help="list where the two recognisers disagreed (Qwen3-ASR's reading is used)")
    p.add_argument("video")
    p.set_defaults(fn=cmd_diffs)

    p = sub.add_parser("fix", help='apply text fixes {"fix": [{"id": line, "from": "...", "to": "..."}]}')
    p.add_argument("video")
    p.add_argument("file", help="JSON file, or - for stdin")
    p.add_argument("--source", default="claude")
    p.set_defaults(fn=cmd_fix)

    p = sub.add_parser("replace", help="replace a word everywhere in the transcript (e.g. a misheard name)")
    p.add_argument("video")
    p.add_argument("old")
    p.add_argument("new")
    p.add_argument("--source", default="user")
    p.set_defaults(fn=cmd_replace)

    p = sub.add_parser("glossary", help="list / add / remove recurring names used as recognition hints")
    p.add_argument("action", nargs="?", choices=["list", "add", "remove"], default="list")
    p.add_argument("terms", nargs="*")
    p.set_defaults(fn=cmd_glossary)

    p = sub.add_parser("rules", help="print the cutting rules for retakes/stutters")
    p.set_defaults(fn=cmd_rules)

    p = sub.add_parser("lines", help="print numbered transcript lines")
    p.add_argument("video")
    p.add_argument("--from", dest="start", type=int, default=0)
    p.add_argument("--to", dest="end", type=int)
    p.set_defaults(fn=cmd_lines)

    p = sub.add_parser("apply", help='apply {"drop": [...], "delete": [...]} cuts JSON')
    p.add_argument("video")
    p.add_argument("file", help="JSON file, or - for stdin")
    p.add_argument("--source", default="claude")
    p.add_argument("-v", "--verbose", action="store_true", help="show every affected line with cut tokens in [brackets]")
    p.set_defaults(fn=cmd_apply)

    p = sub.add_parser("ai", help="decide retake/stutter cuts with an OpenAI-compatible API model")
    p.add_argument("video")
    p.add_argument("--model", default=os.environ.get("MCUT_LLM_MODEL"))
    p.add_argument("--base-url", default=os.environ.get("MCUT_LLM_BASE_URL", "https://openrouter.ai/api/v1"))
    p.add_argument("--api-key", default=os.environ.get("MCUT_LLM_API_KEY"))
    p.set_defaults(fn=cmd_ai)

    p = sub.add_parser("cues", help="print subtitles of the current edit as key|time|text")
    p.add_argument("video")
    p.set_defaults(fn=cmd_cues)

    p = sub.add_parser("subs", help='save subtitle text fixes from JSON {"<key>": "fixed text"} ("" resets)')
    p.add_argument("video")
    p.add_argument("file", help="JSON file, or - for stdin")
    p.set_defaults(fn=cmd_subs)

    p = sub.add_parser("status", help="edited length and cut counts")
    p.add_argument("video")
    p.set_defaults(fn=cmd_status)

    p = sub.add_parser("review", help="open the review page")
    p.add_argument("video")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--no-open", action="store_true")
    p.set_defaults(fn=cmd_review)

    p = sub.add_parser("render", help="render the edited video + .srt")
    p.add_argument("video")
    p.add_argument("-o", "--output")
    p.add_argument("--preview", action="store_true", help="960p, lower bitrate")
    p.add_argument("--no-burn", action="store_true", help="don't burn subtitles into the picture")
    p.add_argument("--no-smooth", action="store_true", help="hard jump cuts instead of speed-ramping the picture over short cuts")
    p.add_argument("--window", nargs=2, type=float, metavar=("START", "END"), help="only this source span (seconds)")
    p.set_defaults(fn=cmd_render)

    p = sub.add_parser("setup", help="one-time: build the environment and download the speech models")
    p.set_defaults(fn=cmd_setup)

    p = sub.add_parser("doctor", help="check ffmpeg, encoder, font, models")
    p.set_defaults(fn=cmd_doctor)

    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
