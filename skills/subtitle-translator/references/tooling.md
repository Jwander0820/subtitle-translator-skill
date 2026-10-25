# Optional assembly and verification helper

For local ASR before translation, see [transcription.md](transcription.md) and `scripts/transcribe_audio.py`. That separate helper requires faster-whisper; the assembly helper below remains standard-library-only.

`scripts/subtitle_io.py` requires Python 3.10+ and only the standard library. It does not translate, research names, extract video tracks, or call any API. The skill can also run without it.

```text
python "<skill-dir>/scripts/subtitle_io.py" prepare "episode.ja.ass" "episode.work.json"
python "<skill-dir>/scripts/subtitle_io.py" build "episode.ja.ass" "episode.work.json" "episode.fr+ja.ass" --layout translation-first
python "<skill-dir>/scripts/subtitle_io.py" validate "episode.ja.ass" "episode.work.json" "episode.fr+ja.ass" --layout translation-first
```

Replace `<skill-dir>` with the installed skill folder. Subtitle and map paths are relative to the current working directory, normally the folder holding the task files. SRT uses the same commands with `.srt` files. All new outputs must not already exist. The source can never be the output.

`prepare` creates a JSON object containing the exact source SHA-256 and ordered cue objects. Each cue has an ID, source text, and `translation: null`. Fill translation fields only, keeping IDs, source text, and source fingerprint unchanged. The map is a resumable intermediate artifact: don't build until every translation field is a nonempty string. For SRT, IDs are the original numeric IDs; for ASS, IDs are 1-based Dialogue ordinals. ASS Dialogue events with empty text (or only an alignment block) are left out of the map and kept unchanged; they still count toward ordinals, so IDs can skip numbers. Newlines in a translation are JSON `\n`, not literal ASS commands.

Choose `--layout translation-only`, `translation-first`, or `source-first`. The helper verifies the full expected document, not just cue counts. This includes original language in bilingual output, metadata, headers, and tags. After writing, it reopens and validates the file. Run `validate` again after copying or later editing. Changed source fingerprints, missing/reordered/duplicated IDs, altered source text, empty translations, and known translation failure placeholders are errors.

Supported subset:

- UTF-8 input (with or without BOM), preserved timing and order; output uses UTF-8 and LF line endings.
- Plain-text SRT with numeric IDs and standard timestamps. Empty or whitespace-only lines separate cues. Styled SRT, timestamp settings, and literal markup are rejected for manual/format-aware handling.
- ASS with Text last in `[Events] Format:`, plain text, `\N`, and optional leading alignment-only blocks such as `{\an8}`. Commas in Text work. Unknown overrides, soft breaks, hard spaces, inline styling, drawing mode, and karaoke are rejected.

Input overlaps are preserved; source end-before-start times are errors. Punctuation-only translations are reported as semantic review flags, not automatically discarded. No script can determine if a source was faithfully translated; language, meaning, and render checks remain the agent's responsibility. The helper does not guess a legacy encoding or implement a general ASS parser.
