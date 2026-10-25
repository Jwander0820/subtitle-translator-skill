# Subtitle formats and local extraction

Read for local-video inputs or ASS work. Examples use generic paths; discover executable locations in the current environment rather than embedding one user's installation paths.

## Probe each video

```text
ffprobe -v error -select_streams s -show_entries stream=index,codec_name:stream_tags=language,title:stream_disposition=hearing_impaired,forced,default -of json "episode.mkv"
```

Match language, codec, track title (CC/SDH), hearing-impaired/forced flags, and user intent. A default track may be a different language; a forced track often contains only signs/foreign speech. Metadata can be absent or unreliable: inspect text samples when needed. If multiple plausible tracks remain, ask. No CC label does not prove the absence of sound descriptions.

The reported `index` is an absolute stream index, including audio/video. Use `-map 0:INDEX`. Do not confuse it with `-map 0:s:N` (subtitle-relative ordinal), and do not reuse an index from another episode.

For an ASS stream at absolute index 8:

```text
ffmpeg -hide_banner -loglevel error -n -i "episode.mkv" -map 0:8 -c:s copy "episode.ja-SDH.ass"
```

For SubRip, preserve it as SRT. Extract an unmodified source first. If a conversion is useful, make an additional file; ASS→SRT can lose positioning, styling, animation, and drawing information. Image subtitles such as PGS/DVD require a separate OCR workflow; do not label them extracted text or silently transcribe audio instead. If no usable text subtitles exist and audio-derived subtitles meet the request, continue with [transcription.md](transcription.md). Discover existing tools before proposing an installation. FFmpeg is optional and is not bundled.

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

The alignment applies to the event; do not remove it from the translation or repeat tags indiscriminately. For color/font resets, positions, transforms, fades, drawing mode (`\p`), karaoke, or animated signs, reason about tag scope and timing. Merely concatenating two tagged strings can leak styling or render translated prose as a vector drawing. Use a format-aware approach and inspect the result; the optional helper rejects these advanced cases rather than pretending to support them.

Preserving styles does not guarantee target-script font coverage or readable bilingual layout. Fonts may fall back differently across players. Don't silently shrink fonts, change positions, or reflow signs to solve collisions: make a justified adjustment within the user's scope and disclose it. Without a playback check, say layout remains unverified.

Validate raw structure against the extracted master as well as successful parsing. An output that FFprobe accepts can still contain missing translations, overlapping text, or malformed visual intent.
