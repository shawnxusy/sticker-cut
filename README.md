# mcut

Auto-cut voice-over videos (Mandarin + English): removes filler sounds, stutters, retakes and long pauses, adds burned-in subtitles, and lets you review every cut in a browser page before export.

- **Transcription**: two local recognisers. FunASR Paraformer-zh gives character timing and filler sounds; Qwen3-ASR (LLM-based, MLX on Apple Silicon) gives better wording, especially homophones. Claude then proofreads from context, and you can fix any line or misheard name on the review page. Recurring names go in a glossary (`~/.config/mcut/glossary.txt`, `mcut glossary add …`), which is used as recognition hints.
- **Retakes / stutters**: decided by Claude in the loop (the `/mcut` skill), or by any OpenAI-compatible model (`mcut ai`).
- **Fillers (呃/嗯) and long pauses**: automatic rules. Pauses are shortened, not removed (≈0.45 s after a sentence, 0.3 s after a comma).
- **Cutting**: cut points are snapped into silence, with 20 ms crossfades. Video follows the audio within half a frame.
- **Output**: `<video dir>/<stem>.mcut/<stem>_edited.mp4` plus a `.srt` file.

## Set up on a new Mac (Claude Code)

In Claude Code:

```
/plugin marketplace add shawnxusy/sticker-cut
/plugin install mcut@mcut
```

Restart Claude Code (or run `/reload-plugins`), then:

```
/mcut ~/Movies/IMG_1234.MOV
```

The first run installs uv, Python 3.12 and the dependencies (~1 min), then downloads ~4.5 GB of speech models (Paraformer from ModelScope ~10–15 min, Qwen3-ASR from HuggingFace ~3–5 min; one time). To get that out of the way right after installing, ask Claude to "run mcut setup" (or run `~/.claude/plugins/cache/mcut/mcut/*/bin/mcut setup`). After that, prep takes about 3–4 minutes per 24 minutes of video on Apple Silicon. The glossary is per machine; copy `~/.config/mcut/glossary.txt` to carry it over.

To update: `/plugin` → Installed → mcut → Update now.

## Several recordings

`/mcut` with several files (or `mcut join A.MOV B.MOV C.MOV`) joins them into one video first, with plain cuts. Clips from the same phone are joined without re-encoding. Claude also catches sections re-recorded in a later clip.

## Use without Claude

```
git clone https://github.com/shawnxusy/sticker-cut.git && cd sticker-cut
bin/mcut prep VIDEO          # transcribe + automatic cuts
bin/mcut ai VIDEO --model deepseek/deepseek-chat   # needs MCUT_LLM_API_KEY (OpenRouter by default)
bin/mcut review VIDEO        # review page, Export button
```

Run `bin/mcut --help` for all commands (`diffs`, `fix`, `replace`, `glossary`, `lines`, `apply`, `cues`, `render`, `status`, `setup`, `doctor`).

## Subtitle look

The review page's Style tab sets the font, size, colours, outline, shadow, background box and position. Its preview is rendered by the same code as the burn-in, so it shows exactly what Export produces. Fonts come from `~/.config/mcut/fonts/` (copy your .ttf/.otf files there on each Mac; a local `fonts/` folder in the checkout also works and is git-ignored). Characters a font lacks fall back to the system CJK font. "Save as default for new videos" writes `~/.config/mcut/style.json`. Fonts are deliberately not in the repo: most Chinese display fonts may not be redistributed, and many are free for personal use only.

## Layout

| Path | What |
|---|---|
| `skills/mcut/SKILL.md` | the workflow Claude follows |
| `mcut/rules.md` | cutting rules, shared by the skill and `mcut ai` |
| `mcut/` | Python package: `cli`, `asr_funasr`, `asr_qwen`, `textfix` (text corrections over timed tokens), `glossary`, `edit` (cut decisions), `plan` (cut points, pauses, speed ramps, subtitles), `render`, `burn`, `review/` |
| `bin/mcut` | launcher; installs uv on first use, keeps the venv in `~/.cache/mcut/venv` |
| `eval/` | hand-labelled reference cuts for the first 3 min of IMG_2524; score a run with `python -m mcut.evaluate eval/IMG_2524_0-180.gold.json <cuts.json>` |

## Per-video project folder (`<stem>.mcut/`)

`audio16k.wav`, `asr.json` (Paraformer), `asr_qwen.json` (Qwen3-ASR), `tokens.json`, `lines.txt` (corrected text), `proxy.mp4` (960p, used by the review page), `cuts.json` (all cuts and their on/off state, text fixes, subtitle overrides, settings), and the outputs. Copy the folder along with the video to continue on another machine.
