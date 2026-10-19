---
name: mcut
description: Auto-edit a voice-over / talking video (Mandarin + English) — fix speech-recognition errors, remove filler sounds, stutters, retakes and long pauses, add burned-in subtitles, then let the user review everything in a browser page before export. Use when the user wants to cut, clean up or subtitle a recording or product walkthrough video, or mentions mcut.
argument-hint: "<video file>"
allowed-tools: Bash(${CLAUDE_PLUGIN_ROOT}/bin/mcut *)
---

# mcut — auto-cut a voice-over video

All commands go through the launcher `${CLAUDE_PLUGIN_ROOT}/bin/mcut` (written as `mcut` below). It installs uv and the Python environment on first use; nothing else needs to be installed. Everything derived from a video lives in a folder next to it: `<video dir>/<stem>.mcut/`.

Your job in the loop is the part rules can't do: getting the words right from context (proofreading), and deciding which lines are retakes, false starts and stutters. Filler sounds (呃/嗯) and long pauses are handled automatically.

## 1. Prep (transcribe + automatic cuts)

```
${CLAUDE_PLUGIN_ROOT}/bin/mcut prep "<video>"
```

Run it with `run_in_background` and wait for it to finish. Two recognisers run: Paraformer (timing, filler sounds) and Qwen3-ASR (an LLM-based recogniser that gets homophones right far more often; its wording is adopted wherever they disagree). On a machine that has never run mcut, the first run builds the Python environment (~1 min) and downloads ~4.5 GB of speech models (~15–20 min); tell the user up front. If they just want to get the machine ready, `mcut setup` does only that. After setup, prep takes ~3–4 minutes per 24 minutes of video on Apple Silicon. Re-running is cheap: finished steps are skipped (`--force` redoes them).

## 2. Proofread (fix recognition errors)

Subtitles are made from the transcript, so every misrecognition ends up on screen. Fix the text before deciding cuts.

1. `mcut glossary` — the user's known names (artists, brands, sticker series). Prefer these spellings.
2. `mcut diffs "<video>"` — every place the recognisers disagreed: the line as it now reads, with the adopted Qwen3-ASR reading in `[brackets]` and Paraformer's reading in the margin. Qwen3-ASR is usually right on ordinary words; Paraformer is sometimes right on names, or on what was literally said when Qwen3-ASR smoothed over a slip of the tongue. Revert those.
3. Read `<stem>.mcut/lines.txt` in full with the Read tool (one `id|text` line per phrase; ~550 lines for 24 minutes) and fix any other error the context makes obvious — a homophone that makes no sense in context (e.g. corpses that 面向强而立 → 面向墙而立), a garbled term.
4. Write `{"fix": [{"id": <line id>, "from": "<exact current text>", "to": "<corrected text>"}]}` to `<stem>.mcut/claude_fixes.json` and run `mcut fix "<video>" "<stem>.mcut/claude_fixes.json"`. `from` is any exact substring of the current line — a word or short phrase is best — without punctuation; `to` may add punctuation such as 《》 for titles. Each applied fix is echoed as `before -> after`. Runs accumulate and re-running an already-applied fix is a no-op, so you can fix a `REJECTED` item and re-run the same file. lines.txt is rewritten with the corrected text.
5. Rules: fix misrecognitions only. Keep the speaker's words and word order, don't polish grammar, don't remove stutters or fillers here (that's the cutting step), don't touch 呃/嗯. Qwen3-ASR writes 它 for almost every "tā"; change it to 他/她 only where the referent is clearly a person. Make a recurring term consistent (e.g. the same brand or 底卡 spelled one way throughout).
6. Names you can't verify: don't guess silently. List them for the user with your best guess; they can fix them in the review page (Replace all, with "glossary" ticked so future videos get them right). When the user confirms a spelling, `mcut replace "<video>" <wrong> <right>` fixes it everywhere and `mcut glossary add <right>` remembers it.

## 3. Decide the cuts

1. `mcut rules` — print the cutting rules. Follow them exactly; they define the JSON format too.
2. Go through the whole (now corrected) transcript in order. Typical things to find:
   - the speaker introduces something, stops, and introduces it again (drop the whole first attempt, which can be 10+ lines);
   - a sentence started 2–3 times before it comes out right (drop the abandoned starts);
   - stutters inside a line such as 没没开灯, 这这些, 分呃分章节 (delete the first copy);
   - a lone hesitation 啊 line.
   `mcut lines "<video>" --from N --to M` prints a slice if you need to re-check a stretch.
3. Write the JSON to `<stem>.mcut/claude_cuts.json`. For transcripts over ~300 lines, write it in one file but think in passes of ~150 lines, keeping in mind that a retake can straddle a pass boundary.

## 4. Apply and check

```
${CLAUDE_PLUGIN_ROOT}/bin/mcut apply "<video>" "<stem>.mcut/claude_cuts.json" -v
```

- `-v` prints every affected line with your cuts in `[brackets]` and automatic cuts in `{braces}`. Read it: everything bracketed must be redundant, and what remains must read fluently. Fix and re-apply if not. Re-applying replaces all your previous cuts, including any the user switched off on the review page; cuts the user added themselves are kept. So after the user has started reviewing, don't re-apply without telling them.
- `REJECTED` lines mean a fragment wasn't found verbatim in that line (copy it exactly, without punctuation) or a line id was out of range. Fix and re-apply.
- To read the edited result as a whole (what will actually be heard and shown), run `mcut cues "<video>"`; check the joins around each retake read naturally.
- Tell the user briefly: original → edited length, the main retakes you removed (line ids + a few words each), how many text fixes you made, and any names you weren't sure about.

## 5. Review (the user's step)

Start the review page in the background (it keeps running) and give the user the URL it prints:

```
${CLAUDE_PLUGIN_ROOT}/bin/mcut review "<video>"
```

Run it with `run_in_background`. It opens the browser automatically. Tell the user:
- struck-through text is cut, coloured by reason; click a cut to restore it, click again to re-cut;
- select text and press X to cut it, R to restore; Alt-click toggles a single character; ⌘Z undoes cut changes;
- clicking the video plays/pauses; clicking the subtitle on the video pauses it and edits that subtitle in place (Enter saves, Esc cancels); the edit is saved as a correction of the words underneath, so it survives later cut changes and is used by Export;
- dotted-underlined text was corrected (hover shows what was heard); ✎ at the end of a line edits its text; "Replace all" fixes a misheard name everywhere (tick "glossary" to remember it for future videos);
- the Style tab picks the subtitle font (from `~/.config/mcut/fonts/`; if it's empty on a new machine, tell the user to copy their font files there) and sets size, colours, outline, shadow, background box and position; the preview on the video is rendered by the same code that burns subtitles in, so it is exactly what Export produces. "Save as default for new videos" stores it in `~/.config/mcut/style.json`. Sentence-final 啊 is hidden in subtitles by default (checkbox there);
- "Skip cuts" plays the video as it will be exported, with subtitles (the page jumps at every cut; the exported file instead speed-ramps the picture over short cuts so it doesn't jump);
- Export writes `<stem>.mcut/<stem>_edited.mp4` and a matching `.srt`; "Quick preview (960p)" is faster for checking.

Edits on the page are saved to `<stem>.mcut/cuts.json` immediately, so you can read the user's changes afterwards.

## Without the page

- `mcut render "<video>"` renders the current edit (add `--preview` for a fast 960p check, `--window 0 180` for the first 3 minutes of source, `--no-burn` to skip burned-in subtitles, `--no-smooth` for hard jump cuts everywhere).
- `mcut status "<video>"` shows edited length and cut counts.
- `mcut subs "<video>" file.json` overrides the text of single subtitles by key (from `mcut cues`); prefer `mcut fix`, which survives later cut changes.
- `mcut ai "<video>" --model <id>` decides the cuts with an OpenAI-compatible API model instead of you (env: `MCUT_LLM_API_KEY`, `MCUT_LLM_BASE_URL`, default OpenRouter).
- `mcut doctor` checks ffmpeg, the hardware encoder, the subtitle font and the speech-model cache.

## Limits

- The recognisers are Mandarin-first; English words inside Chinese speech are recognised, but an all-English video will transcribe poorly.
- Qwen3-ASR needs Apple Silicon; elsewhere only Paraformer's text is used, so proofreading matters more.
- Cuts adjacent to drawn-out 呃 can occasionally leave a short tail of the filler; the user can extend the cut on the review page.
