# Local source-language transcription

Use when local video/audio has no usable text subtitles. The goal is a usable draft source to translate, not verified captions: run the helper with its defaults, skim the flags, translate, and say the source is an unchecked ASR draft. No API key is needed and audio stays local; subtitle text sent to a cloud agent still goes through that service.

This route transcribes speech only: no OCR of hard/image subtitles, speaker labels, or sound captions.

## Transcribe

Set up the media environment from [tooling.md](tooling.md) if it is not there yet. The helper reads the video directly; no audio extraction or FFmpeg is needed:

```text
<venv-python> "<skill-dir>/scripts/transcribe_audio.py" "episode.mkv" "episode.asr-run" --language ja
```

With one audio track, that is all. With several, list them, pick the original-language dialogue track (not a dub, commentary, or audio description), and ask if it is unclear:

```text
<venv-python> "<skill-dir>/scripts/transcribe_audio.py" "episode.mkv" --list-streams
<venv-python> "<skill-dir>/scripts/transcribe_audio.py" "episode.mkv" "episode.asr-run" --language ja --audio-stream 2
```

Set the source language explicitly (`ja`, `en`, …); `auto` only when unknown. Always transcribe the original language and let the agent translate.

The defaults are the best balance found on Japanese anime episodes (2026-11-18), so keep them unless there is a reason:

| Default | Why | Change when |
| --- | --- | --- |
| Model `turbo` | More accurate than `small` and faster on both GPU and CPU (decoding dominates, and turbo's decoder is small); `large-v3` was slower and looped without VAD | The user names a model: `--model small` saves download size (0.5 GB vs 1.6 GB) |
| `--device auto`: a working CUDA GPU, otherwise CPU (recorded as `device_fallback`) | 24-minute episode: about 1 minute on an RTX 3090, 7 minutes on a 16-core CPU, with the same dialogue coverage (about 90%) | — |
| No VAD | Silero VAD dropped 20–38% of dialogue lines that ran over music | Clean speech with long silences: `--vad` |
| Independent decoding windows | Stops repetition loops spreading (161 → 43 unmatched lines on one episode) | Spelling drifts between windows: `--condition-on-previous-text` |

Optional: `--hotwords "name1 name2"` biases toward known names; `--initial-prompt` shows the expected script and punctuation (for example, a Traditional Chinese sentence for `zh`, which `--language zh` alone does not choose). Both can plant words that were never spoken. On a GPU, `--batch-size 8 --vad` is faster but inherits the VAD losses above.

Cue times follow the audio track. If the helper reports that the track starts late, or cues look shifted in the player, `--offset SECONDS` adds a constant; it cannot fix drift.

## Outputs

The run directory must be new:

- `source.srt`: the draft source. Cues follow sentences using word timings; anything over 7 s or 84 display columns is split at the strongest boundary, without breaking Japanese/Chinese words. Alignment errors after silence are repaired and flagged.
- `asr.json`: settings, detected language, `status`, and review flags.
- `segments.jsonl`: raw segments and word times.

Only `status: complete` produces a usable SRT. `failed`/`running` means inference stopped: use a new directory to retry. `render_failed` means inference finished but the SRT was not written; after fixing the cause, rebuild without re-running the model (pass the same media file):

```text
<venv-python> "<skill-dir>/scripts/transcribe_audio.py" "episode.mkv" "episode.asr-run" --render-only --srt-name "source.v2.srt"
```

`--render-only` also re-renders with a different `--offset` or `--max-cue-seconds`.

## Quick check, then translate

Skim the flags in `asr.json` and fix only what is clearly wrong, in a copy such as `episode.ja.srt`:

- `known_hallucination_phrase` ("ご視聴ありがとうございました", "thanks for watching") at the start, end, or over music: usually invented, so delete the cue.
- Runs of `repeated_adjacent_text` or `low_log_probability` over the opening/ending song: lyrics or loops. Keep them only if the user wants songs subtitled.
- `literal_markup_characters` (such as `>>`): remove the characters.
- `very_short_cue_check_timing`, `word_timing_repaired`, `out_of_order_timing`, `broken_characters`: timing or text may be off; fine to leave in a draft.

Then continue with the normal workflow: create or update the work context file (names and terms; ASR often garbles names, so research them), then `subtitle_io.py prepare` on that SRT. While translating, read obvious misrecognitions by their meaning (for example 単身不妊中 → 単身赴任中) and keep the Japanese unchanged in bilingual output. List removed cues and doubtful readings in `<output-basename>.unresolved.md`.

Tell the user what to expect: dialogue under loud music, background voices, and homophones are the usual misses; songs are not subtitled unless asked; the source was not checked by listening.
