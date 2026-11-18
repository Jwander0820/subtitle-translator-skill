# Subtitle formats and local extraction

Read for local-video inputs or ASS work. The commands use the media helper and its environment from [tooling.md](tooling.md); FFmpeg is optional. An existing FFmpeg works too (`ffprobe` to list, `ffmpeg -map 0:INDEX -c:s copy` to copy).

## Probe each video

```text
<venv-python> "<skill-dir>/scripts/transcribe_audio.py" "episode.mkv" --list-streams
```

Each stream shows its absolute `index`, `type`, `codec`, `language`, `title`, and `disposition` flags (`default`, `forced`, `hearing_impaired`). Match language, title (CC/SDH), flags, and user intent. A default track may be a different language; a forced track often contains only signs/foreign speech. Metadata can be absent or unreliable: inspect text samples when needed. If multiple plausible tracks remain, ask. No CC label does not prove the absence of sound descriptions. Indexes count all streams, including video and audio; never reuse one from another episode.

Copy a text subtitle stream unchanged, keeping its format (`ass`/`ssa` → `.ass`, `subrip` → `.srt`):

```text
<venv-python> "<skill-dir>/scripts/transcribe_audio.py" "episode.mkv" --extract-subtitle 8 "episode.ja-SDH.ass"
```

In testing the copy matched FFmpeg's stream copy byte for byte; it never overwrites a file. Other text tracks, such as MP4 `mov_text` or WebVTT, are refused with a ready FFmpeg command that converts them to SRT; install FFmpeg at that point as described in [tooling.md](tooling.md). If a conversion is useful, make an additional file; ASS→SRT can lose positioning, styling, animation, and drawing information. Image subtitles (PGS, DVD, DVB) are refused: they need OCR, which this skill does not do; say so rather than silently transcribing audio instead. If no usable text subtitles exist and audio-derived subtitles meet the request, continue with [transcription.md](transcription.md).

Keep the source video intact; don't remux it just to translate subtitles. Write to an authorized output directory, not necessarily beside a read-only video. After extraction, record source path/fingerprint and selected track metadata in work context.

## SRT

Preserve IDs, timestamp lines, ordering, and original bilingual text. Keep supported inline markup scoped to each language. Do not strip all angle brackets with a generic regex: some text is literal, and styling can carry meaning. Keep a raw original even if a text-only companion is useful.

## ASS

Read the file's actual `[Events]` `Format:` line. The Text field commonly comes last and can contain commas, so use bounded splitting. Stable event ordinals are useful work IDs; ASS does not provide SRT-style numeric IDs. Preserve Layer, Start, End, Style, Name, margins, Effect, headers, styles, non-dialogue events, and event ordering.

Preserve `\N` hard line breaks, overrides such as `{\an8}`, and meaningful inline markup. For a simple source with a leading alignment override:

```text
source: {\an8}Hello\Nworld
translation-first: {\an8}Bonjour\Nle monde\NHello\Nworld
```

The alignment applies to the event; do not remove it from the translation or repeat tags indiscriminately. For color/font resets, positions, transforms, fades, drawing mode (`\p`), karaoke, or animated signs, reason about tag scope and timing. Merely concatenating two tagged strings can leak styling or render translated prose as a vector drawing. Use a format-aware approach and inspect the result; the optional helper passes such cues through as raw cues whose full text you write, rather than pretending to interpret them.

Preserving styles does not guarantee target-script font coverage or readable bilingual layout. Fonts may fall back differently across players. Don't silently shrink fonts, change positions, or reflow signs to solve collisions: make a justified adjustment within the user's scope and disclose it. Without a playback check, say layout remains unverified.

Validate raw structure against the extracted master as well as successful parsing. An output that a player accepts can still contain missing translations, overlapping text, or malformed visual intent.
