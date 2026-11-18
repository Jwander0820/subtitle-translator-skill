# Optional helpers and setup

The skill can run without any script; the two helpers make the common paths repeatable. Neither translates or calls an API.

| Script | Does | Needs |
| --- | --- | --- |
| `scripts/subtitle_io.py` | Translation map, batch merging, assembly, validation | Python 3.10+ only (standard library) |
| `scripts/transcribe_audio.py` | Lists a video's tracks, copies text subtitle tracks, transcribes speech | The media environment below |

## Media environment

Everything goes in one project-local virtual environment, `.venv-asr`, beside the task files. Nothing is installed system-wide, and deleting the folder removes packages and models together.

| Item | Size | Required |
| --- | --- | --- |
| Python 3.10+ | — | Yes; ask the user to install it if missing |
| `faster-whisper` (pip) | ~150 MB | Yes. It includes PyAV, FFmpeg's own libraries for Python: audio decoded through it was sample-identical to the `ffmpeg` command's, so no separate FFmpeg is needed |
| Model weights, `turbo` | ~1.6 GB, downloaded on first transcription into `.venv-asr/whisper-models` | Transcription only |
| CUDA 12 cuBLAS/cuDNN (pip) | ~1.3 GB | Optional, NVIDIA GPU only: a 24-minute episode took about 1 min on an RTX 3090 vs 7 min on a 16-core CPU, same quality |
| FFmpeg via `imageio-ffmpeg` (pip) | ~100 MB | Optional, only to convert subtitle tracks the helper refuses to copy (MP4 `mov_text`, WebVTT) |

Check for an existing `.venv-asr` first. Otherwise, after telling the user the download sizes and getting a go-ahead:

```text
python -m venv .venv-asr
<venv-python> -m pip install faster-whisper
<venv-python> -m pip install nvidia-cublas-cu12 "nvidia-cudnn-cu12==9.*"   # optional, NVIDIA GPU
```

Install FFmpeg only when a track needs converting, and prefer an `ffmpeg` already on the system. Otherwise put it in the same environment and get its path:

```text
<venv-python> -m pip install imageio-ffmpeg
<venv-python> -c "import imageio_ffmpeg; print(imageio_ffmpeg.get_ffmpeg_exe())"
```

`<venv-python>` is `.venv-asr\Scripts\python.exe` on Windows, `.venv-asr/bin/python` elsewhere; run the media helper with it. A display driver alone is not enough for the GPU. Without the CUDA packages, transcription falls back to CPU automatically. On Windows the helper finds the pip CUDA libraries itself. If the GPU requirements change, see the [faster-whisper requirements](https://github.com/SYSTRAN/faster-whisper#requirements). `--model-cache DIR` stores models elsewhere; `--local-files-only` forbids downloads.

```text
<venv-python> "<skill-dir>/scripts/transcribe_audio.py" "episode.mkv" --list-streams
<venv-python> "<skill-dir>/scripts/transcribe_audio.py" "episode.mkv" --extract-subtitle 3 "episode.ja.ass"
<venv-python> "<skill-dir>/scripts/transcribe_audio.py" "episode.mkv" "episode.asr-run" --language ja
```

Stream use is covered in [formats.md](formats.md); transcription in [transcription.md](transcription.md).

## Translation map helper

`scripts/subtitle_io.py` needs only Python 3.10+ and does not extract tracks or research names.

```text
python "<skill-dir>/scripts/subtitle_io.py" prepare "episode.ja.ass" "episode.work.json"
python "<skill-dir>/scripts/subtitle_io.py" fill "episode.work.json" "episode.batch-01.json"
python "<skill-dir>/scripts/subtitle_io.py" status "episode.work.json"
python "<skill-dir>/scripts/subtitle_io.py" build "episode.ja.ass" "episode.work.json" "episode.fr+ja.ass" --layout translation-first --glossary "subtitle-context.fr.md"
python "<skill-dir>/scripts/subtitle_io.py" validate "episode.ja.ass" "episode.work.json" "episode.fr+ja.ass" --layout translation-first
```

Replace `<skill-dir>` with the installed skill folder. Subtitle and map paths are relative to the current working directory, normally the folder holding the task files. SRT uses the same commands with `.srt` files. New maps and outputs must not already exist. The source can never be the output.

## Map and batches

`prepare` creates a JSON map holding the exact source SHA-256 and one cue per line, so ranges are cheap to read. Each cue has `id`, `start` and `seconds` (read-only timing hints for pacing and scene gaps), `source`, and `translation: null`. For SRT, IDs are the original numeric IDs; for ASS, IDs are 1-based Dialogue ordinals. Cues without text are left out of the map and kept unchanged; ASS ones still count toward ordinals, so IDs can skip numbers.

Write each translated range as a small batch file and merge it with `fill`:

```json
{"12": "First translation", "13": "Line one\nline two"}
```

`fill` is all-or-nothing and replaces the map atomically. It rejects unknown IDs, empty or blank lines, failure placeholders, and markup in plain cues. It refuses to change an existing different translation unless `--replace` is given, which is the route for deliberate revisions such as a later glossary. It then prints progress; `status` prints the same translated count and missing ID ranges at any time. Editing `translation` fields directly also works; keep IDs, source text, and the fingerprint unchanged. Newlines in a translation are JSON `\n`, not literal ASS commands.

## Raw-markup cues

The helper interprets plain text only: SRT without markup, and ASS text with `\N` plus optional leading alignment-only blocks such as `{\an8}`. Any other cue (inline styling, `<i>`, positions, fades, drawing mode, karaoke, soft breaks, hard spaces, or literal `<>{}\` characters) is marked `"raw": true` and listed by `prepare`. For these, `translation` is the complete final cue text, inserted verbatim regardless of `--layout`: write the tags, the translation, and the source half of a bilingual cue yourself, using `\N` in ASS. Reason about tag scope as described in [formats.md](formats.md); for drawings or other events with no translatable text, copy the source unchanged. The helper checks only that raw text stays one well-formed cue.

## Build, validate, review flags

Choose `--layout translation-only`, `translation-first`, or `source-first`. `build` refuses to run until every translation is filled. It verifies the full expected document, not just cue counts: original language in bilingual output, metadata, headers, and tags. After writing, it reopens and validates the file. Run `validate` again after copying or later editing. Changed source fingerprints, missing/reordered/duplicated IDs, altered source text, empty translations, and known failure placeholders are errors.

`PASS` is followed by `REVIEW:` lines, one per kind with compact cue ranges. Inspect them; they never change the output:

- raw-markup cues inserted verbatim;
- punctuation-only translations, and translations identical to their source;
- reading speed above `--max-cps` (default 20 display columns per second) and lines wider than `--max-line-width` (default 42 columns). East Asian wide characters count as two columns. Set the confirmed locale limits, or 0 to disable;
- with `--glossary FILE`, source terms whose target is absent from that cue and its neighbours. The file is a Markdown table whose first two columns are source and target (the work context file works as is) or tab-separated lines. Matching is literal, so inflected forms and deliberate short forms can be false positives.

No script can determine if a source was faithfully translated; language, meaning, and render checks remain the agent's responsibility.

## Input limits

UTF-8 input (with or without BOM); output is UTF-8 with LF line endings. Other encodings are reported, not guessed. SRT needs numeric IDs and standard timestamps; empty or whitespace-only lines separate cues. ASS needs Text last in `[Events] Format:`; commas in Text work. Input overlaps are preserved; source end-before-start times are errors. This is not a general ASS parser.
