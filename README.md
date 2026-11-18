# Subtitle Translator

An [Agent Skill](https://agentskills.io/specification) for translating SRT and ASS subtitles with consistent terminology across episodes, bilingual output, and checks against the source. It can extract an embedded text-subtitle track from a local video, or use local speech recognition to generate source subtitles when usable text subtitles are absent, then translate them.

Its aim is a usable translation when none exists: a good first pass delivered simply, not a professional finished subtitle. The instructions are written in English but work with any language pair. Translation runs on the model and tools already in your AI agent; no translation service, API key, or integration is bundled.

## Quick start

1. Copy the whole `skills/subtitle-translator` folder into your agent's skills directory (see [Install](#install)).
2. Reload skills or restart the agent.
3. In the folder with your subtitles, ask:

   > Translate `episode-05.ja.srt` into Traditional Chinese for Taiwan, with Chinese above Japanese.

The result is a new file, `episode-05.zh-TW+ja.srt`; the original stays untouched:

```text
12
00:01:02,300 --> 00:01:04,100
我們走吧。
行こう。
```

## What it does

- Follows your target language/locale and reuses confirmed presentation choices across episodes.
- Preserves SRT cue IDs and timing, or ASS event fields, styles, and positioning.
- Produces translation only, translation above source, or source above translation.
- Checks each video's current subtitle-track metadata before selecting a language and CC/SDH track.
- When no usable text subtitles exist, selects the audio track, transcribes it locally with faster-whisper using tested default settings, removes obvious recognition junk, and translates the result.
- Uses neighboring dialogue to understand fragments while preserving meaningful pauses, interruptions, and reveals.
- Gives your supplied glossary priority, keeps names consistent across episodes, and can apply a later glossary to existing translations.
- Saves progress in source-bound cue maps and reports structural, semantic, and playback checks separately.
- Renames finished outputs to match video filenames on request, checking for collisions and preserving content.

Out of scope: video downloading, OCR (including hard/burned-in subtitles), and retiming existing subtitles. ASS stays ASS when styling matters; converting to SRT is an optional extra step that can lose formatting.

## Install

Copy the **complete** `skills/subtitle-translator` folder into your host's skills directory. Keep references, scripts, and the license together.

| Agent | Personal directory | Project directory |
| --- | --- | --- |
| [Codex](https://learn.chatgpt.com/docs/build-skills) | `~/.agents/skills/` | `.agents/skills/` |
| [Claude Code](https://code.claude.com/docs/en/skills) | `~/.claude/skills/` | `.claude/skills/` |
| [Gemini CLI](https://geminicli.com/docs/cli/skills/) | `~/.gemini/skills/` | `.gemini/skills/` |

For example, a personal Codex installation ends at `~/.agents/skills/subtitle-translator/SKILL.md`. On Windows, `~` is your user profile folder. Avoid installing duplicate copies for the same host.

## Use

Ask your agent to use **Subtitle Translator**, or invoke `$subtitle-translator` in Codex or `/subtitle-translator` in Claude Code. More examples:

> Extract the Japanese SDH track from `episode-06.mkv`, then translate it into Traditional Chinese for Taiwan. Keep ASS, with Chinese above Japanese. Reuse the series glossary.

> This Japanese video has no text subtitles. Transcribe it locally, then translate into Traditional Chinese for Taiwan with Chinese above Japanese.

> Transcribe this English interview locally, then produce translated subtitles. Use the existing CPU environment and cached model.

> Use this updated glossary to revise episodes 5–10. Preserve the Japanese, timestamps, and styles. Keep a backup and a change log.

> Rename the finished subtitles to match each video's basename, retaining `.ass` and the individual episode numbers.

The agent asks only for information that matters and is not already established. Budget-first is the default; quality-first and highest-quality add review passes. These are effort preferences: they do not guarantee accuracy or switch models in a host that cannot.

### Work files

Keep episodes of the same work in one folder. The agent maintains a compact `subtitle-context.<target-locale>.md` there for names, terms, active glossary choices, references, presentation, and open questions. Detailed cue changes go in a separate revision log.

| File | Purpose |
| --- | --- |
| `episode-05.fr.srt` | Translation only |
| `episode-05.fr+ja.ass` | French above Japanese, in the existing ASS layout |
| `episode-05.work.json` | Source-bound cue map for progress and assembly |
| `episode-05.fr+ja.unresolved.md` | Open questions, kept out of the subtitles |
| `subtitle-context.fr.md` | Reusable terminology and decisions |

By default, cue count, order, and timing stay the same; meaning may be redistributed locally within a continuous utterance. Source overlaps and genuine repetitions are kept for inspection.

### What gets installed

Translating an existing SRT/ASS needs nothing beyond your agent; the optional map helper uses Python 3.10+ if it is available. Video work uses one project-local Python environment that the agent creates beside your files after asking; nothing is installed system-wide, and deleting `.venv-asr` removes it all.

| Needed for | Item | Size |
| --- | --- | --- |
| Everything below | Python 3.10+ | — |
| Reading video tracks, copying text subtitles, transcription | `faster-whisper` (pip; includes PyAV, FFmpeg's libraries for Python, so **FFmpeg itself is not needed**) | ~150 MB |
| Transcription | `turbo` model, downloaded on first use into `.venv-asr/whisper-models` | ~1.6 GB |
| Faster transcription (optional, NVIDIA GPU) | CUDA 12 cuBLAS/cuDNN (pip) | ~1.3 GB |
| Converting MP4 `mov_text` or WebVTT subtitle tracks (optional, only when met) | FFmpeg, from the system or `imageio-ffmpeg` (pip) | ~100 MB |

Image-based tracks (PGS/DVD) and burned-in subtitles need OCR, which is out of scope.

`scripts/transcribe_audio.py` lists a video's tracks, copies ASS/SRT subtitle tracks unchanged, and transcribes the original-language audio straight from the video into a draft SRT split at sentence boundaries, with review flags for likely hallucinations and timing problems. Its defaults were picked on Japanese anime episodes: model `turbo` everywhere (more accurate than `small`, and faster even on CPU), GPU when it works and otherwise CPU, no VAD (it dropped dialogue under music), and independent decoding windows (fewer repetition loops). A 24-minute episode took about 1 minute on an RTX 3090 and 7 minutes on a 16-core CPU, with the same dialogue coverage. No transcription API key is required; cloud-agent translation still processes subtitle text through that service. See [the transcription workflow](skills/subtitle-translator/references/transcription.md).

`scripts/subtitle_io.py` (Python 3.10+, standard library only) prepares a JSON translation map, merges translated batches, assembles the output, and validates it against the source, map, and layout. It also flags fast reading speed, wide lines, copied source text, and glossary gaps for review. It does **not** translate or call any service. It interprets plain SRT and ASS with plain text, hard line breaks, and leading alignment overrides; cues with other styling, drawing, or karaoke markup pass through as raw cues that the agent writes in full. See [the helper reference](skills/subtitle-translator/references/tooling.md).

## Checks

- **Structural:** IDs/order, timing, source-language preservation, non-text fields, formatting, missing cues, and unfinished placeholders.
- **ASR quick check, when applicable:** remove flagged hallucinations and unwanted song lyrics; obvious misrecognitions are read by meaning during translation. No listening pass by default.
- **Semantic:** the translation compared with the source, including terminology, negation, uncertainty, references, and timing of reveals.
- **Playback/AV:** fonts, readability, positioning/collisions, and alignment with the actual video/audio.

A file that parses is not proof of an accurate translation or a good bilingual layout. The agent reports which checks it actually ran. Poor source transcription and ambiguous visual references may need audio/video evidence.

## Status and limitations

- Used locally in Codex for six complete Japanese-to-Traditional-Chinese ASS episodes, followed by a glossary revision and video-matching renames. This is operational experience, not an accuracy benchmark, and those episodes have not had a full playback review.
- Other language pairs and hosts are untested. Install paths were checked against each host's documentation on 2026-10-25.
- Speech recognition produces draft subtitles, not verified captions or SDH. On two anime episodes checked against their burned-in subtitles, the default settings covered about 90% of dialogue lines; misses were mostly speech under loud music, background voices, and homophones.
- With a cloud model, subtitle text and context are processed by that service under its terms. The skill contains no credentials or personal subtitle material.

## License

[MIT](LICENSE). The license covers this skill's instructions and tooling; it does not change rights in anyone's videos or subtitles.
