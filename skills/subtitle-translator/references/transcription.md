# Local source-language transcription

Use when the user supplies local video/audio without usable text subtitles and wants translated subtitles. The agent orchestrates local tools, reviews the source, then performs translation using the existing workflow. No transcription API key is needed. Audio stays local in this backend; subtitle text sent to a cloud agent still uses that service. Model downloads and package installation require network access, disk space, and the host's normal permissions.

## Route and inspect

First inspect supplied sidecars and subtitle tracks as described in `formats.md`. Prefer a matching full-dialogue source; a forced signs-only track is not a substitute. Preserve useful partial text as reference rather than treating it as complete dialogue. If only image tracks/hard subtitles exist, explain that this route transcribes speech, not visible text. No OCR, speaker identification, sound-effect captions, or recovery of onscreen labels is provided.

Inspect audio language, title, duration, channel count, and timing:

```text
ffprobe -v error -show_entries format=start_time,duration:stream=index,codec_type,codec_name,start_time,duration,channels:stream_tags=language,title -of json "episode.mkv"
```

Choose the original-language dialogue audio, not a dub, commentary, or audio-description track. Metadata alone may be insufficient; sample listening when available, or ask if selection remains ambiguous. Never reuse another file's stream index. Record video fingerprint, selected absolute audio index, extraction command, and timing origin in a per-run source note beside the artifacts.

## Runtime and model

Discover the current Python, FFmpeg/FFprobe, installed backend, available RAM/GPU, and cached models. Prefer a task-local virtual environment over changing the system Python. If installation is necessary within the user's task authorization, use the selected environment's Python:

```text
python -m venv .venv-asr
<venv-python> -m pip install faster-whisper
```

On Windows `<venv-python>` is `.venv-asr\Scripts\python.exe`; on Unix it is `.venv-asr/bin/python`. These are examples, not assumptions about installed paths. Record the working package version in run metadata. GPU requirements change: check the [upstream installation instructions](https://github.com/SYSTRAN/faster-whisper#requirements) before installing CUDA dependencies. A CPU run avoids CUDA setup; do not silently change model/device after a failed run.

Default starting point: multilingual `small`, CPU `int8`, explicit `ja` or `en`. This is a practical baseline, not an accuracy promise. For a quick low-resource trial, `base` is an option; for difficult dialogue, compare a larger multilingual model on a representative short sample if resources permit. Do not use `.en` models for Japanese or assume English-focused distilled models support it. A compatible NVIDIA GPU can use `--device cuda --compute-type float16` after verifying the runtime. Another installed local ASR backend is acceptable if it produces timed original-language text and equivalent provenance; the supplied helper specifically uses faster-whisper.

Model names may download weights on first use. Use an existing local model directory or `--local-files-only` when downloads are not wanted. Do not launch repeated model downloads as an automatic fallback. Report a missing dependency/model or resource failure with a concrete next step.

## Extract selected audio and transcribe

The helper accepts mono 16 kHz signed 16-bit PCM WAV, making audio selection explicit. For an inspected audio track at absolute index 1:

```text
ffmpeg -hide_banner -loglevel error -n -i "episode.mkv" -map 0:1 -vn -ac 1 -ar 16000 -c:a pcm_s16le "episode.audio.wav"
python "<skill-dir>/scripts/transcribe_audio.py" "episode.audio.wav" "episode.asr-run" --language ja
```

Do not remove silence or concatenate speech before transcription: timestamps must retain the original spacing. VAD is enabled within faster-whisper, which maps recognized times back to the input audio timeline. Downmixing may hurt unusual channel mixes; inspect a sample before a long run and use a justified channel selection when needed.

WAV timestamps start at zero. Container audio delay, nonzero starts, edits, or sample clips can differ from the player's video origin. Inspect timing and compare beginning/middle/end against playback; do not assume this extraction command preserves all container offsets. The helper's `--offset SECONDS` adds a known constant to SRT times (raw JSON remains WAV-relative). For a clipped test, include its verified origin when comparing against the full video. Negative resulting cue times are rejected. A constant offset does not repair drift/discontinuities; resolve these before freezing a source for translation.

Always transcribe the original language (`task=transcribe`), then translate with the agent. Whisper's `translate` task targets English, not arbitrary target locales. Use `--language en` for English; use `auto` only when unknown and verify its detected language. Mixed-language recordings need sample review; a single forced language is not a guarantee of multilingual coverage.

The run directory must be new. Outputs:

- `source.srt`: draft original-language segments, ready for source review.
- `segments.jsonl`: raw segments, word times, and model scores, flushed during inference. Times are relative to the WAV, before `--offset`.
- `asr.json`: input fingerprint, backend version, model/settings, detected language, status, SRT fingerprint, and review flags. Scores are heuristics, not calibrated accuracy probabilities.

Only `status: complete` means artifact generation completed, not that ASR is accurate. A failed/interrupted run can leave partial artifacts; never feed them directly into translation. An abrupt process termination can leave `running`. Reuse a completed matching run instead of retranscribing. The helper does not resume inference inside a partial run: preserve it for diagnosis and use a new run directory for a justified retry. It reports progress during segment iteration and never overwrites an existing run.

## Review the source, then hand off

Review flagged low-score segments, long cues, repeated text, gaps, proper names, numbers, and the start/middle/end of the recording. Gaps may be legitimate silence; repetitions may be genuine speech. VAD can omit quiet dialogue, while music/noise can produce invented text. Flags are not automatic deletion rules, and absence of flags does not establish complete coverage. For a suspicious passage, compare a bounded audio sample or one justified alternate model/VAD run; if still unresolved, retain uncertainty in notes rather than inventing dialogue. Do not loop through models indefinitely.

ASR segments are not necessarily readable subtitle units. When needed, make `episode.ja.reviewed.srt` separately, correcting supported recognition errors and using word timings/audio evidence for segmentation. Do not evenly divide time by character count or strip pauses to make timings look neat. Retain raw output and log substantive corrections; if no changes are necessary, the original generated SRT may be the frozen source. Without audio review, label it an unverified ASR draft and disclose inherited uncertainty in the translated delivery.

Once segmentation and timing are established, freeze the source and use the existing helper:

```text
python "<skill-dir>/scripts/subtitle_io.py" prepare "episode.ja.reviewed.srt" "episode.work.json"
```

The agent fills translations using work context and glossary, then runs the existing build/validate commands. Preserve this frozen source in bilingual output. If the source changes later, create a new source-bound map; do not keep translating against an obsolete fingerprint. Record ASR provenance in work context by linking the run, not copying every score into cross-episode terminology notes. Name final outputs from the video/episode, not the generic `source.srt` helper filename.

Report separately: ASR generation/review, translation review, structural validation, and playback alignment. Deliver requested SRT files plus necessary uncertainty notes; retain raw artifacts for reproducibility without requiring the user to inspect JSON.

Backend reference: [faster-whisper documentation](https://github.com/SYSTRAN/faster-whisper), checked 2026-10-25. Real ASR accuracy and speed require representative recordings and hardware tests.
