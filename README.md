# Subtitle Translator

An [Agent Skill](https://agentskills.io/specification) for translating SRT and ASS subtitles with consistent terminology across episodes, bilingual output, and checks against the source. It can extract an embedded text-subtitle track from a local video, or use local speech recognition to generate source subtitles when usable text subtitles are absent, then translate them.

The instructions are written in English but work with any language pair. Translation runs on the model and tools already in your AI agent; no translation service, API key, or integration is bundled.

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
- When no usable text subtitles exist, selects the audio track and uses optional local faster-whisper transcription for Japanese, English, or other supported languages, followed by source review and translation.
- Uses neighboring dialogue to understand fragments while preserving meaningful pauses, interruptions, and reveals.
- Gives your supplied glossary priority, keeps names consistent across episodes, and can apply a later glossary to existing translations.
- Saves progress in source-bound cue maps and reports structural, semantic, and playback checks separately.
- Renames finished outputs to match video filenames on request, checking for collisions and preserving content.

Out of scope: video downloading, OCR (including hard/burned-in subtitles), and retiming existing subtitles. New ASR source segmentation/timing is established before translation. ASS stays ASS when styling matters; converting to SRT is an optional extra step that can lose formatting.

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

> This Japanese video has no text subtitles. Generate Japanese subtitles locally with faster-whisper, review suspicious passages, then translate into Traditional Chinese for Taiwan with Chinese above Japanese.

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

### Optional tools

Nothing needs to be installed to use the instructions. Extracting from video needs a tool such as FFmpeg/FFprobe, which this skill does not bundle. Image-based tracks (PGS/DVD) need a separate OCR workflow.

For speech transcription, the optional `scripts/transcribe_audio.py` requires faster-whisper in a suitable Python environment and model weights. It accepts a selected mono 16 kHz PCM WAV and writes source SRT, raw timed segments, and ASR metadata/review flags to a new directory. The baseline is multilingual `small` on CPU with `int8`; hardware and audio quality determine suitable settings. First use may download a model. No transcription API key is required; cloud-agent translation still processes subtitle text through that service. See [the transcription workflow](skills/subtitle-translator/references/transcription.md) for audio selection, installation, offsets, source review, and handoff.

The optional helper `scripts/subtitle_io.py` (Python 3.10+, standard library only) prepares a JSON translation map, assembles completed translations, and validates the output against the source, map, and layout. It does **not** translate or call any service. It supports plain SRT and ASS with plain text, hard line breaks, and leading alignment overrides, and refuses advanced styling, drawing, karaoke, or other markup that needs format-aware handling. See `references/tooling.md` in the skill folder.

## Checks

- **Structural:** IDs/order, timing, source-language preservation, non-text fields, formatting, missing cues, and unfinished placeholders.
- **ASR source review, when applicable:** recognition errors, suspicious gaps/repetitions, names/numbers, segmentation, and alignment with audio. Model scores only flag candidates for review.
- **Semantic:** the translation compared with the source, including terminology, negation, uncertainty, references, and timing of reveals.
- **Playback/AV:** fonts, readability, positioning/collisions, and alignment with the actual video/audio.

A file that parses is not proof of an accurate translation or a good bilingual layout. The agent reports which checks it actually ran. Poor source transcription and ambiguous visual references may need audio/video evidence.

## Status and limitations

- Used locally in Codex for six complete Japanese-to-Traditional-Chinese ASS episodes, followed by a glossary revision and video-matching renames. This is operational experience, not an accuracy benchmark, and those episodes have not had a full playback review.
- Other language pairs and hosts are untested. Install paths were checked against each host's documentation on 2026-10-25.
- The new ASR helper has synthetic integration tests; real Japanese/English recordings, model inference, and GPU performance have not yet been validated. Its generated SRT is a draft, not verified captions or SDH.
- With a cloud model, subtitle text and context are processed by that service under its terms. The skill contains no credentials or personal subtitle material.

## Tests

```bash
python -m unittest discover -s tests -v
```

## License

[MIT](LICENSE). The license covers this skill's instructions and tooling; it does not change rights in anyone's videos or subtitles.
