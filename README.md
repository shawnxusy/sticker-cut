# mcut

Turns a raw voice-over or walkthrough recording (Mandarin + English) into an edited video with burned-in subtitles. It removes filler sounds, stutters, retakes and long pauses, fixes speech-recognition errors, and lets you review every cut and subtitle in a browser page before export. It runs as a Claude Code plugin on your Mac.

## Install and use

**You need:** a Mac with Apple Silicon and [Claude Code](https://claude.com/claude-code). Nothing else; the plugin sets up its own tools.

**1. Install** (in Claude Code), then restart Claude Code:

```
/plugin marketplace add shawnxusy/sticker-cut
/plugin install mcut@mcut
```

**2. One-time setup.** Ask Claude to **"run mcut setup"**. It downloads the speech models (~4.5 GB, about 20 minutes). Optionally copy your subtitle fonts (`.ttf` / `.otf`) into `~/.config/mcut/fonts/`.

**3. Edit a video.** Put the recording anywhere, for example `~/Movies/mcut/`, and run:

```
/mcut ~/Movies/mcut/IMG_1234.MOV
```

To combine several recordings into one video, list them in the order you want:

```
/mcut combine ~/Movies/mcut/IMG_1234.MOV ~/Movies/mcut/IMG_1235.MOV ~/Movies/mcut/IMG_1236.MOV
```

Claude transcribes the video (about 3–4 minutes per 24 minutes of video), fixes misheard words, and marks the cuts: retakes, false starts, stutters, 呃/嗯 and long pauses. For combined recordings, that includes sections you re-recorded in a later clip. It then tells you what it changed and which names it wasn't sure about, and opens the review page.

**4. Review in the browser.** The page opens by itself at **http://127.0.0.1:8765** (localhost). If that port is taken, it uses the next free one (8766, …) and Claude gives you the exact link. Running `/mcut` again on the same video reopens the same page.

| To… | Do this |
|---|---|
| Watch the edit | Click the video to play/pause. With **Skip cuts** ticked, it plays as it will be exported, with subtitles. |
| Restore or add a cut | Struck-through text is cut. Click it to restore it, click again to re-cut. Select text and press **X** to cut or **R** to restore. **⌘Z** undoes. |
| Fix a subtitle | Click the subtitle on the video: it pauses and becomes editable (**Enter** saves, **Esc** cancels). Or click **✎** at the end of a transcript line. |
| Fix a name everywhere | **Find / Replace with → Replace all**. Tick **remember for future videos** so the speech model and Claude know the name next time. |
| Style the subtitles | **Style** tab: font, size, text and outline colour, outline width, shadow, background box, position. The preview is exactly what gets burned in. **Save as default for new videos** keeps the look. |
| Export | Choose **Quick preview (960p)** to check or **Full quality** for the final file, then **Export**. |

**5. Result.** Your original is never changed. The edit goes next to it:

```
~/Movies/mcut/IMG_1234.MOV                     original
~/Movies/mcut/IMG_1234.mcut/IMG_1234_edited.mp4  edited video, subtitles burned in
~/Movies/mcut/IMG_1234.mcut/IMG_1234_edited.srt  subtitles as a file
```

You can come back later: `/mcut` on the same video reopens it where you left off.

**Updating:** `/plugin` → Installed → mcut → **Update now**, then restart Claude Code.

---

## How it works

- **Two speech recognisers, both local.** FunASR Paraformer-zh provides character timing and filler sounds. Qwen3-ASR, an LLM-based recogniser running on MLX on Apple Silicon, provides better wording, especially for homophones. Its reading is used wherever the two disagree. Claude then proofreads from context; `mcut diffs` shows every disagreement.
- **Glossary.** Recurring names (artists, brands, series) go in `~/.config/mcut/glossary.txt`. They're passed to Qwen3-ASR as hints and Claude prefers those spellings.
- **Cut decisions.** Fillers (呃/嗯) and long pauses are handled by rules. Retakes, false starts and stutters are decided by Claude following `mcut/rules.md`, or by any OpenAI-compatible model (`mcut ai`). Pauses are shortened, not removed: about 0.45 s after a sentence, 0.3 s after a comma.
- **Natural cuts.** Cut points are snapped into silence, with 20 ms audio crossfades. At short cuts the picture is speed-ramped (about 1.3×) instead of jumping. Long cuts and joins between recordings stay plain cuts.
- **Subtitles.** Built from the corrected transcript, split at word boundaries. A sentence-final 啊 is hidden in subtitles (the audio keeps it). Text corrections are stored per character, so they survive later changes to the cuts.
- **Combining recordings.** Clips from the same phone (same codec, size, frame rate and rotation) are joined without re-encoding. Others are converted to match the first clip. Clip boundaries appear as `--- clip N ---` markers for Claude and as dividers on the review page.

## First run and models

The first `mcut` command on a machine installs uv, Python 3.12 and the Python dependencies into `~/.cache/mcut/venv` (about 1 minute). The first transcription, or `mcut setup`, downloads:

- Paraformer, VAD and punctuation models from ModelScope: ~2 GB, ~10–15 min, cached in `~/.cache/modelscope`;
- Qwen3-ASR from HuggingFace: ~2.5 GB, ~3–5 min, cached in `~/.cache/huggingface`.

`mcut doctor` shows what's installed and cached. ffmpeg comes bundled with the `imageio-ffmpeg` package and uses the Mac's hardware encoder.

## Files and folders

- **Videos** can live anywhere you can write to; avoid iCloud/Dropbox folders while working. Each video gets a `<name>.mcut/` folder next to it, about 2 GB per 24 minutes of video, mostly the export and the review proxy. After exporting you can delete `proxy.mp4` and `audio16k.wav`; they're rebuilt if needed.
- **Moving to another Mac:** copy the video together with its `.mcut` folder.
- **Project folder contents:** `audio16k.wav`, `asr.json` (Paraformer), `asr_qwen.json` (Qwen3-ASR), `tokens.json`, `lines.txt` (corrected transcript), `proxy.mp4` (960p, for the review page), `cuts.json` (cuts and their on/off state, text fixes, subtitle style, settings), `project.json` (source video, joined clips), and the outputs.
- **Per-machine settings in `~/.config/mcut/`:** `fonts/` (subtitle fonts), `glossary.txt`, `style.json` (default subtitle look). Copy them to carry your setup to another Mac. Fonts aren't in the repo on purpose: most Chinese display fonts may not be redistributed, and many are free for personal use only.

## Command line (without Claude)

```
git clone https://github.com/shawnxusy/sticker-cut.git && cd sticker-cut
bin/mcut join A.MOV B.MOV C.MOV                      # optional: combine recordings -> A_joined.MOV
bin/mcut prep VIDEO                                  # transcribe + automatic cuts
bin/mcut ai VIDEO --model deepseek/deepseek-chat     # retake/stutter cuts by an API model (MCUT_LLM_API_KEY; OpenRouter by default)
bin/mcut review VIDEO                                # review page, Export button
bin/mcut render VIDEO [--preview] [--no-burn] [--no-smooth]   # or render without the page
```

Other commands: `diffs`, `fix`, `replace`, `glossary`, `lines`, `rules`, `apply`, `cues`, `subs`, `status`, `setup`, `doctor` (`bin/mcut --help`).

## Repository layout

| Path | What |
|---|---|
| `skills/mcut/SKILL.md` | the workflow Claude follows |
| `mcut/rules.md` | cutting rules, shared by the skill and `mcut ai` |
| `mcut/` | Python package: `cli`, `join`, `asr_funasr`, `asr_qwen`, `textfix` (text corrections over timed tokens), `glossary`, `edit` (cut decisions), `plan` (cut points, pauses, speed ramps, subtitles), `render`, `burn` (subtitle rendering), `review/` (browser page) |
| `bin/mcut` | launcher: installs uv on first use, keeps the venv in `~/.cache/mcut/venv` |
| `eval/` | hand-labelled reference cuts for the first 3 minutes of a sample; score a run with `python -m mcut.evaluate eval/IMG_2524_0-180.gold.json <cuts.json>` |
