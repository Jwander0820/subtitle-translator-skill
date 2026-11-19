#!/usr/bin/env python3
"""Media helper: list tracks, copy text subtitle tracks, and transcribe speech locally.

Reads video/audio directly with PyAV, which ships with faster-whisper, so FFmpeg is not
needed. --list-streams shows the tracks; --extract-subtitle copies an ASS/SRT track
unchanged. Otherwise transcribes one audio track with faster-whisper into a NEW run
directory (source.srt, segments.jsonl, asr.json); --render-only rebuilds the SRT without
repeating inference. No translation or OCR.
"""

import argparse
import hashlib
import json
import math
import os
import sys
import unicodedata
import wave
from dataclasses import asdict
from importlib.metadata import version
from pathlib import Path

SENTENCE_END = tuple("。！？!?…")
SOFT_BREAK = tuple("、，,;；:：")
PAUSE = 0.8  # Seconds of silence treated as a sentence-strength boundary.
SHORT_PAUSE = 0.3  # A full stop counts as a sentence end only with a pause ("Dr. Smith").
# Outro/credit lines Whisper learned from video subtitles and emits over silence or music.
# Matched on lowercase text without spaces or punctuation; a hit is a review flag only.
HALLUCINATION_PHRASES = (
    "thanksforwatching", "thankyouforwatching", "pleasesubscribe", "amaraorg",
    "感谢观看", "感謝觀看", "谢谢观看", "謝謝觀看", "字幕志愿者", "字幕志願者", "点赞订阅", "點贊訂閱",
    "ご視聴ありがとうございました", "ご清聴ありがとうございました", "チャンネル登録",
)


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def plain_wav_duration(path):
    """Duration of a mono 16 kHz 16-bit WAV, or None for any other media (decoded with PyAV)."""
    try:
        with wave.open(str(path), "rb") as audio:
            if (audio.getnchannels(), audio.getframerate(), audio.getsampwidth()) != (1, 16000, 2):
                return None
            duration = audio.getnframes() / audio.getframerate()
    except (wave.Error, EOFError):
        return None
    if duration <= 0:
        raise ValueError("Empty WAV input.")
    return duration


def load_samples(path):
    """Read a plain WAV with the standard library."""
    import numpy
    with wave.open(str(path), "rb") as audio:
        frames = audio.readframes(audio.getnframes())
    return numpy.frombuffer(frames, dtype="<i2").astype(numpy.float32) / 32768.0


def seconds_of(stream, value):
    return None if value is None else round(float(value * stream.time_base), 3)


def list_streams(path):
    import av
    with av.open(str(path)) as container:
        rows = []
        for stream in container.streams:
            metadata = stream.metadata or {}
            # Attachment streams (e.g. fonts muxed with ASS) have no codec context.
            row = {"index": stream.index, "type": stream.type,
                   "codec": getattr(stream.codec_context, "name", None),
                   "language": metadata.get("language"), "title": metadata.get("title"),
                   "start_seconds": seconds_of(stream, stream.start_time)}
            if stream.type == "audio":
                row["channels"] = stream.codec_context.channels
            flags = getattr(stream, "disposition", None)
            if flags is not None:  # default / forced / hearing_impaired matter for track choice.
                try:
                    row["disposition"] = [flag.name for flag in type(flags) if flag.value and flag in flags]
                except TypeError:
                    row["disposition"] = int(flags)
            rows.append(row)
        return rows


SUBTITLE_SUFFIX = {"ass": ".ass", "ssa": ".ass", "subrip": ".srt", "srt": ".srt"}
IMAGE_SUBTITLES = {"hdmv_pgs_subtitle", "dvd_subtitle", "dvb_subtitle", "xsub", "dvb_teletext"}


def extract_subtitle(path, stream_index, output):
    """Copy a text subtitle stream out unchanged, like `ffmpeg -map 0:N -c:s copy`."""
    import av
    output = Path(output)
    if output.exists():
        raise FileExistsError(f"{output} exists; choose a new name.")
    with av.open(str(path)) as container:
        stream = next((s for s in container.streams if s.index == stream_index), None)
        if stream is None or stream.type != "subtitle":
            raise ValueError(f"Stream {stream_index} is not a subtitle stream; see --list-streams.")
        codec = stream.codec_context.name
        if codec in IMAGE_SUBTITLES:
            raise ValueError(f"Stream {stream_index} is {codec}, an image subtitle: it needs OCR, which "
                             "this skill does not do.")
        if codec not in SUBTITLE_SUFFIX:
            raise ValueError(f"Stream {stream_index} is {codec} text; this helper only copies ASS/SRT. "
                             f"Convert it with FFmpeg (optional, see tooling.md): "
                             f'ffmpeg -n -i "{path}" -map 0:{stream_index} "{output.with_suffix(".srt")}"')
        if output.suffix.lower() != SUBTITLE_SUFFIX[codec]:
            raise ValueError(f"A {codec} stream is copied unchanged; name the output *{SUBTITLE_SUFFIX[codec]}.")
        count = 0
        with av.open(str(output), "w", format=SUBTITLE_SUFFIX[codec][1:]) as out:
            if hasattr(out, "add_stream_from_template"):  # PyAV 13+
                target = out.add_stream_from_template(stream)
            else:
                target = out.add_stream(template=stream)
            for packet in container.demux(stream):
                if packet.dts is None:
                    continue
                packet.stream = target
                out.mux(packet)
                count += 1
    return codec, count


def decode_audio(path, stream_index):
    """Decode one audio stream to mono 16 kHz float32 with PyAV (bundled with faster-whisper)."""
    import av
    import numpy
    with av.open(str(path)) as container:
        audio_streams = list(container.streams.audio)
        if stream_index is None:
            if len(audio_streams) != 1:
                found = ", ".join(f"{s.index} ({(s.metadata or {}).get('language') or '?'})" for s in audio_streams)
                raise ValueError(f"{len(audio_streams)} audio streams ({found or 'none'}); "
                                 "pick the original-language one with --audio-stream INDEX "
                                 "(see --list-streams).")
            stream = audio_streams[0]
        else:
            stream = next((s for s in container.streams if s.index == stream_index), None)
            if stream is None or stream.type != "audio":
                raise ValueError(f"Stream {stream_index} is not an audio stream; see --list-streams.")
        resampler = av.AudioResampler(format="s16", layout="mono", rate=16000)
        chunks, skipped = [], 0
        for packet in container.demux(stream):
            try:
                frames = packet.decode()
            except av.error.InvalidDataError:
                skipped += 1  # Corrupt packets are dropped, as FFmpeg would.
                continue
            for frame in frames:
                chunks.extend(chunk.to_ndarray().reshape(-1) for chunk in resampler.resample(frame))
        chunks.extend(chunk.to_ndarray().reshape(-1) for chunk in resampler.resample(None))
        if not chunks:
            raise ValueError("No audio decoded from the selected stream.")
        samples = numpy.concatenate(chunks).astype(numpy.float32) / 32768.0
        info = {"index": stream.index, "codec": stream.codec_context.name,
                "language": (stream.metadata or {}).get("language"),
                "start_seconds": seconds_of(stream, stream.start_time) or 0.0,
                "corrupt_packets_skipped": skipped}
        return samples, info


def timestamp(milliseconds):
    seconds, ms = divmod(milliseconds, 1000)
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours:02}:{minutes:02}:{seconds:02},{ms:03}"


def clean(text):
    return " ".join(text.split())


def width(text):
    """Display columns: East Asian wide characters count as two."""
    return sum(0 if unicodedata.combining(char) else
               2 if unicodedata.east_asian_width(char) in "WF" else 1 for char in text)


def script_class(char):
    code = ord(char)
    if 0x3040 <= code <= 0x309F:
        return "hiragana"
    if 0x30A0 <= code <= 0x30FF or 0xFF66 <= code <= 0xFF9F:
        return "katakana"
    if 0x3400 <= code <= 0x4DBF or 0x4E00 <= code <= 0x9FFF or 0xF900 <= code <= 0xFAFF:
        return "han"
    if char.isdigit():
        return "digit"
    return "latin" if char.isalpha() else "other"


def join_strength(before, after):
    """Word-boundary likelihood between tokens of unspaced text (Japanese, Chinese).

    Whisper's "words" there are sub-word tokens with no gap between them, so cut where a
    particle or inflection meets a new content word, and avoid splitting kanji compounds,
    katakana words, and numbers with their counters.
    """
    if not before.strip() or not after.strip() or after[0].isspace():
        return 0
    left, right = script_class(before.rstrip()[-1]), script_class(after.lstrip()[0])
    if left == "hiragana" and right in ("han", "katakana", "digit", "latin"):
        return 1
    if (left, right) in (("han", "han"), ("katakana", "katakana"), ("digit", "han"),
                         ("digit", "digit"), ("latin", "latin"), ("han", "hiragana")):
        return -1.5
    return 0


def split_words(words, boundaries, max_seconds, max_width):
    """Divide words into (start, end, text, word count) cues; None when timings look broken.

    boundaries holds the word indexes that end an original ASR segment.
    """
    def joined(group):
        return clean("".join(word["word"] for word in group))

    def divide(first, last):  # Inclusive word index range.
        group = words[first:last + 1]
        total = width(joined(group))
        fits = group[-1]["end"] - group[0]["start"] <= max_seconds and total <= max_width
        gaps = [words[index + 1]["start"] - words[index]["end"] for index in range(first, last)]
        ends = [words[index]["word"].rstrip() for index in range(first, last)]
        # A sentence end followed by a pause always separates cues. A pause alone does not:
        # word alignment next to silence is too unreliable to cut a phrase on timing only.
        forced = [step for step, gap in enumerate(gaps)
                  if gap >= SHORT_PAUSE and ends[step].endswith(SENTENCE_END + (".",))]
        if (fits and not forced) or first == last:  # A single token cannot be divided further.
            return [(first, last)]

        def score(step):  # Boundary strength; lopsided cuts (orphans) cost, mid-range ones barely.
            join = join_strength(words[first + step]["word"], words[first + step + 1]["word"])
            if step in forced or gaps[step] >= PAUSE or ends[step].endswith(SENTENCE_END):
                strength = 3
            elif ends[step].endswith(SOFT_BREAK):
                strength = 2
            elif first + step in boundaries:
                strength = 1.5  # Whisper's own break: the fallback when punctuation is absent.
            else:
                strength = (gaps[step] >= SHORT_PAUSE) + join
            distance = abs(width(joined(words[first:first + step + 1])) / max(total, 1) - 0.5)
            return strength - 10 * max(0, distance - 0.2) - 0.5 * distance

        cut = first + max(forced if fits else range(len(gaps)), key=score)
        return divide(first, cut) + divide(cut + 1, last)

    pieces, previous = [], -1
    for first, last in divide(0, len(words) - 1):
        start, end, text = words[first]["start"], words[last]["end"], joined(words[first:last + 1])
        if not (math.isfinite(start) and math.isfinite(end) and previous <= start < end and text):
            return None
        pieces.append((start, end, text, last - first + 1))
        previous = start
    return pieces


def repair_leading_tokens(row):
    """Move a segment's first tokens next to the rest of their word.

    After silence, Whisper often pins the first token of a segment to the previous segment's
    end and starts the rest of the same word seconds later (失 ... 敗, 100 ... 年). A long gap
    inside a compound is an alignment error, so the token moves to just before its partner.
    Returns (row, repaired); segments.jsonl keeps the raw times.
    """
    words = row.get("words") or []
    count = 0
    while (count + 1 < len(words) and words[count + 1]["start"] - words[count]["end"] >= PAUSE
           and join_strength(words[count]["word"], words[count + 1]["word"]) < 0):
        count += 1
    if not count:
        return row, False
    fixed = [dict(word) for word in words]
    for index in range(count - 1, -1, -1):
        following = fixed[index + 1]["start"]
        length = min(fixed[index]["end"] - fixed[index]["start"], 0.5)
        fixed[index]["start"], fixed[index]["end"] = following - length, following
    return dict(row, words=fixed, start=fixed[0]["start"]), True


def units(rows, max_seconds):
    """Join segments that stop mid-sentence to the next one, so cues can follow sentences.

    Only punctuated text is joined: without punctuation (common in Japanese output) there is
    no better boundary than Whisper's own, and joining would run sentences together.
    """
    marks = set(SENTENCE_END + SOFT_BREAK + (".",))
    result = []
    for number, row in rows:
        last = result[-1] if result else None
        if (last and max_seconds > 0 and row.get("words") and last[-1][1].get("words")
                and not clean(last[-1][1]["text"]).endswith(SENTENCE_END + (".",))
                and marks & set(last[-1][1]["text"] + row["text"])
                and row["start"] - last[-1][1]["end"] < PAUSE
                and row["start"] - last[0][1]["start"] <= 4 * max_seconds):  # Bounded chains.
            last.append((number, row))
        else:
            result.append([(number, row)])
    return result


def pieces_for(unit, max_seconds, max_width):
    """(start, end, text, segment numbers) cues for one unit, falling back to raw segments."""
    raw = [(row["start"], row["end"], clean(row["text"]), [number]) for number, row in unit]
    if max_seconds <= 0:
        return raw
    words, owners, boundaries = [], [], set()
    for number, row in unit:
        kept = [word for word in row.get("words") or [] if word["word"].strip()]
        if not kept:
            return raw
        words += kept
        owners += [number] * len(kept)
        boundaries.add(len(words) - 1)
    split = split_words(words, boundaries, max_seconds, max_width if max_width > 0 else math.inf)
    if split is None or (len(unit) == 1 and len(split) == 1):
        return raw  # Broken timings, or an unchanged segment: keep Whisper's own text.
    result, cursor = [], 0
    for start, end, text, count in split:
        result.append((start, end, text, sorted(set(owners[cursor:cursor + count]))))
        cursor += count
    return result


def render_segments(segments, duration, offset=0, max_seconds=7, max_width=84):
    """Keep every nonempty segment's text; flags are review hints, never deletions."""
    if not math.isfinite(offset):
        raise ValueError("Offset must be finite.")
    rows = [(number, row) for number, row in enumerate(segments, 1) if clean(row["text"])]
    stats = {"empty_segments_skipped": len(segments) - len(rows)}
    # Punctuation-only segments (",") come from decoding loops over music and carry no text.
    rows = [(number, row) for number, row in rows if any(char.isalnum() for char in row["text"])]
    stats.update(punctuation_segments_skipped=len(segments) - stats["empty_segments_skipped"] - len(rows),
                 segments_joined=0, segments_split=0)
    # Decoding loops can also step back in time; order cues by time and flag them.
    unordered = {number for (_, earlier), (number, row) in zip(rows, rows[1:]) if row["start"] < earlier["start"]}
    rows.sort(key=lambda item: item[1]["start"])
    repaired = set()
    if max_seconds > 0:
        for position, (number, row) in enumerate(rows):
            fixed, changed = repair_leading_tokens(row)
            if changed:
                rows[position] = (number, fixed)
                repaired.add(number)
    stats["word_timings_repaired"] = len(repaired)
    blocks, flags = [], []
    long_seconds, long_width = max_seconds or 7, max_width or 84
    previous_start, previous_end, previous_text = -1, 0, None
    for unit in units(rows, max_seconds):
        stats["segments_joined"] += len(unit) - 1
        pieces = pieces_for(unit, max_seconds, max_width)
        stats["segments_split"] += len(pieces) > len(unit)
        scores = dict(unit)
        for start, end, text, numbers in pieces:
            index = len(blocks) + 1
            label = f"Segment {numbers[0]}"
            if not all(math.isfinite(t) for t in (start, end)) or not 0 <= start < end <= duration + 0.1:
                raise ValueError(f"{label}: invalid or out-of-audio timing.")
            a, b = round((start + offset) * 1000), round((end + offset) * 1000)
            if a < 0 or b <= a:
                raise ValueError(f"{label}: invalid timing after offset/rounding.")
            if "\x00" in text:
                raise ValueError(f"{label}: NUL character; inspect raw ASR output.")
            sources = [scores[number] for number in numbers]
            reasons = []
            if min(row.get("avg_logprob", 0) for row in sources) < -1:
                reasons.append("low_log_probability")
            if max(row.get("no_speech_prob", 0) for row in sources) > 0.6:
                reasons.append("possible_non_speech")
            if max(row.get("compression_ratio", 0) for row in sources) > 2.4:
                reasons.append("possible_repetition")
            if text == previous_text:
                reasons.append("repeated_adjacent_text")
            if end - start > long_seconds or width(text) > long_width:
                reasons.append("long_cue_review_segmentation")
            if end - start < 0.3:
                reasons.append("very_short_cue_check_timing")
            if repaired.intersection(numbers):
                reasons.append("word_timing_repaired")
            if start < previous_start or unordered.intersection(numbers):
                reasons.append("out_of_order_timing")
            if any(char in text for char in "<>{}\\"):
                reasons.append("literal_markup_characters")
            # Check whole source segments too: a phrase can be split across several cues.
            bare = "".join(char for char in (text + "".join(row["text"] for row in sources)).casefold()
                           if char.isalnum())
            if any(phrase in bare for phrase in HALLUCINATION_PHRASES):
                reasons.append("known_hallucination_phrase")
            if "�" in text:
                reasons.append("broken_characters")
            if start < previous_end:
                reasons.append("overlap")
            if start - previous_end > 15:
                reasons.append("gap_before_check_audio")
            if reasons:
                flags.append({"cue": index, "segments": numbers, "start": a / 1000, "end": b / 1000,
                              "reasons": reasons})
            blocks.append(f"{index}\n{timestamp(a)} --> {timestamp(b)}\n{text}\n")
            previous_start, previous_end, previous_text = start, end, text
    if not blocks:
        raise ValueError("No speech segments; do not create an empty translation source.")
    if duration - previous_end > 15:
        flags.append({"cue": len(blocks), "reasons": ["trailing_gap_check_audio"]})
    stats["cue_count"] = len(blocks)
    return "\n".join(blocks), flags, stats


def write_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def finish(args, manifest, rows, duration, report):
    """Render the SRT from finished inference; a failure here never discards segments."""
    try:
        srt, flags, stats = render_segments(rows, duration, args.offset,
                                            args.max_cue_seconds, args.max_cue_width)
        source = args.output_dir / args.srt_name
        with source.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(srt)
    except Exception as error:
        if manifest.get("status") != "complete":
            manifest.update(status="render_failed", error=str(error))
            write_json(report, manifest)
        raise
    manifest.pop("error", None)
    manifest.update(status="complete", source_srt=args.srt_name, review_flags=flags, source_srt_sha256=sha256(source),
                    offset_seconds=args.offset, max_cue_seconds=args.max_cue_seconds,
                    max_cue_width=args.max_cue_width, **stats)
    write_json(report, manifest)
    return manifest


def register_cuda_libraries():
    """Windows: expose cuBLAS/cuDNN from NVIDIA pip wheels (nvidia-*-cu12) to ctranslate2."""
    if os.name != "nt":
        return []
    import importlib.util
    spec = importlib.util.find_spec("nvidia")
    if spec is None or not spec.submodule_search_locations:
        return []
    found = []
    for root in spec.submodule_search_locations:
        for directory in sorted(Path(root).glob("*/bin")):
            if any(directory.glob("*.dll")):
                found.append(str(directory))
    for directory in found:
        os.add_dll_directory(directory)
    if found:  # ctranslate2 loads cuBLAS lazily by name, which also searches PATH.
        os.environ["PATH"] = os.pathsep.join(found + [os.environ.get("PATH", "")])
    return found


def resolve_device(device):
    if device != "auto":
        return device
    import ctranslate2
    return "cuda" if ctranslate2.get_cuda_device_count() else "cpu"


def default_model_cache():
    """Inside a virtual environment, keep model weights there so the project holds everything."""
    return Path(sys.prefix) / "whisper-models" if sys.prefix != sys.base_prefix else None


def run(args, model_factory=None, pipeline_factory=None):
    if not math.isfinite(args.offset):
        raise ValueError("Offset must be finite.")
    if Path(args.srt_name).name != args.srt_name or not args.srt_name.endswith(".srt"):
        raise ValueError("--srt-name must be a bare .srt filename inside the run directory.")
    report = args.output_dir / "asr.json"
    if args.render_only:
        manifest = json.loads(report.read_text(encoding="utf-8"))
        if manifest.get("status") not in ("transcribed", "render_failed", "complete"):
            raise ValueError("Run has no finished inference; transcribe into a new run directory.")
        if manifest.get("audio_sha256") != sha256(args.audio):
            raise ValueError("Media file does not match this run.")
        lines = (args.output_dir / "segments.jsonl").read_text(encoding="utf-8").splitlines()
        return finish(args, manifest, [json.loads(line) for line in lines if line], manifest["duration"], report)
    if not args.language:
        raise ValueError("--language is required: ja, en, etc.; auto only when unknown.")
    if args.batch_size and not args.vad:
        raise ValueError("--batch-size relies on VAD chunking; add --vad (and check it keeps the dialogue).")
    if args.output_dir.exists():
        raise FileExistsError("Run directory already exists; reuse it with --render-only or choose a new one.")
    duration = plain_wav_duration(args.audio)
    samples, stream_info = str(args.audio), None
    if duration is None or args.audio_stream is not None:
        samples, stream_info = decode_audio(args.audio, args.audio_stream)
        duration = len(samples) / 16000
    real_backend = model_factory is None
    if real_backend:
        os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
        if args.device != "cpu":
            register_cuda_libraries()
        from faster_whisper import WhisperModel
        model_factory = WhisperModel
        if args.batch_size:
            from faster_whisper import BatchedInferencePipeline
            pipeline_factory = BatchedInferencePipeline
        backend_version = version("faster-whisper")
        device = resolve_device(args.device)
        if stream_info is None:
            samples = load_samples(args.audio)
    else:
        backend_version = "test-double"
        device = "cpu" if args.device == "auto" else args.device
    cache = args.model_cache or default_model_cache()
    load_options = {"local_files_only": args.local_files_only}
    if cache:
        load_options["download_root"] = str(cache)
    if stream_info and stream_info["start_seconds"]:
        print(f"Note: the audio stream starts at {stream_info['start_seconds']}s; if cues run early, "
              f"re-render with --offset {stream_info['start_seconds']}.", flush=True)

    def settings(device):  # turbo everywhere: the most accurate default tested; small only on request.
        return (args.model or "turbo", args.compute_type or ("float16" if device == "cuda" else "int8"))

    model_name, compute_type = settings(device)
    options = {"language": None if args.language == "auto" else args.language, "task": "transcribe",
               "beam_size": 5, "vad_filter": args.vad, "word_timestamps": True}
    # Optional arguments are passed only when set, so older backends keep working.
    if args.hotwords:
        options["hotwords"] = args.hotwords
    if args.initial_prompt:
        options["initial_prompt"] = args.initial_prompt
    if args.hallucination_silence_threshold is not None:
        options["hallucination_silence_threshold"] = args.hallucination_silence_threshold
    if args.batch_size:
        options["batch_size"] = args.batch_size  # Batched windows are always independent.
    else:  # Independent windows by default: conditioning let decoding loops spread on anime audio.
        options["condition_on_previous_text"] = args.condition_on_previous_text
    digest = sha256(args.audio)
    args.output_dir.mkdir(parents=True, exist_ok=False)
    manifest = {
        "schema": 1, "status": "running", "backend": "faster-whisper",
        "backend_version": backend_version, "audio": str(args.audio.resolve()),
        "audio_sha256": digest, "audio_stream": stream_info, "duration": duration,
        "model": model_name, "model_cache": str(cache) if cache else None,
        "device": device, "compute_type": compute_type,
        "requested_device": args.device, "requested_language": args.language,
        "local_files_only": args.local_files_only, "transcribe_options": options,
    }
    write_json(report, manifest)
    try:
        model = model_factory(model_name, device=device, compute_type=compute_type, **load_options)
        if real_backend and args.device == "auto" and device == "cuda":
            # A visible GPU can still lack cuBLAS/cuDNN, which only fails at first inference.
            try:
                import numpy
                list(model.transcribe(numpy.zeros(16000, numpy.float32), language="en")[0])
            except Exception as error:
                print(f"GPU unusable ({error}); falling back to CPU.", flush=True)
                device = "cpu"
                model_name, compute_type = settings(device)
                manifest.update(device=device, model=model_name, compute_type=compute_type,
                                device_fallback=str(error))
                write_json(report, manifest)
                model = model_factory(model_name, device=device, compute_type=compute_type, **load_options)
        transcriber = pipeline_factory(model=model) if args.batch_size else model
        segments, info = transcriber.transcribe(samples, **options)
        manifest.update(language=info.language, language_probability=info.language_probability,
                        duration_after_vad=info.duration_after_vad)
        rows = []
        next_progress = 0.0
        # Iteration performs inference. Retain partial results if a later segment fails.
        with (args.output_dir / "segments.jsonl").open("x", encoding="utf-8") as handle:
            for segment in segments:
                row = asdict(segment)
                handle.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n")
                handle.flush()
                rows.append(row)
                if row["end"] >= next_progress:  # About twenty lines per run, not one per segment.
                    print(f"Transcribed through {row['end']:.0f}s / {duration:.0f}s", flush=True)
                    next_progress = row["end"] + duration / 20
    except BaseException as error:
        manifest.update(status="failed", error=str(error))
        write_json(report, manifest)
        raise
    manifest.update(status="transcribed", segment_count=len(rows))
    write_json(report, manifest)
    return finish(args, manifest, rows, duration, report)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("audio", type=Path, metavar="media", help="Video or audio file (any format FFmpeg reads).")
    parser.add_argument("output_dir", type=Path, nargs="?", help="New run directory.")
    parser.add_argument("--list-streams", action="store_true", help="Print the media's tracks as JSON and exit.")
    parser.add_argument("--extract-subtitle", nargs=2, metavar=("INDEX", "OUTPUT"),
                        help="Copy a text subtitle stream (ASS/SRT) to a new file unchanged, then exit.")
    parser.add_argument("--audio-stream", type=int,
                        help="Absolute stream index from --list-streams; required when there are several audio tracks.")
    parser.add_argument("--language", help="Source language: ja, en, etc.; auto only when unknown.")
    parser.add_argument("--model", help="Multilingual model name (turbo, small, large-v3, ...) or local "
                                        "CTranslate2 model directory. Default: turbo.")
    parser.add_argument("--model-cache", type=Path,
                        help="Where downloaded models are stored. Default: whisper-models inside the active venv.")
    parser.add_argument("--device", choices=("auto", "cpu", "cuda"), default="auto",
                        help="auto uses a working CUDA GPU and otherwise falls back to CPU.")
    parser.add_argument("--compute-type", help="Default: int8 on CPU, float16 on CUDA.")
    parser.add_argument("--offset", type=float, default=0, help="Seconds added to cue times to match the video.")
    parser.add_argument("--local-files-only", action="store_true", help="Fail instead of downloading missing model files.")
    parser.add_argument("--vad", action="store_true",
                        help="Skip audio that Silero VAD judges non-speech. It can drop dialogue over music.")
    parser.add_argument("--no-vad", action="store_true", help=argparse.SUPPRESS)  # Former flag; now the default.
    parser.add_argument("--hotwords", help="Names/terms to bias recognition, e.g. from the work context.")
    parser.add_argument("--initial-prompt", help="Short source-language text showing expected spelling and style.")
    parser.add_argument("--condition-on-previous-text", action="store_true",
                        help="Feed each window the previous text: more consistent spelling, but loops can spread.")
    parser.add_argument("--no-condition-on-previous-text", action="store_true", help=argparse.SUPPRESS)  # Now default.
    parser.add_argument("--hallucination-silence-threshold", type=float,
                        help="Seconds; skip silent stretches around a suspected hallucination.")
    parser.add_argument("--batch-size", type=int, default=0,
                        help="Use batched inference (faster, mainly on GPU); 0 keeps sequential decoding.")
    parser.add_argument("--max-cue-seconds", type=float, default=7,
                        help="Split longer segments, and pauses between sentences, at word boundaries; 0 keeps raw segments.")
    parser.add_argument("--max-cue-width", type=int, default=84,
                        help="Split wider segments (display columns; wide characters = 2).")
    parser.add_argument("--render-only", action="store_true",
                        help="Rebuild the SRT from an existing run's segments.jsonl; no inference.")
    parser.add_argument("--srt-name", default="source.srt", help="SRT filename inside the run directory; never overwritten.")
    args = parser.parse_args()
    try:
        if args.list_streams:
            print(json.dumps(list_streams(args.audio), ensure_ascii=False, indent=1))
            return 0
        if args.extract_subtitle:
            index, output = args.extract_subtitle
            codec, count = extract_subtitle(args.audio, int(index), output)
            print(f"Copied {count} {codec} events from stream {index} to {output}.")
            return 0
        if args.output_dir is None:
            raise ValueError("output_dir is required (a new run directory).")
        result = run(args)
        print(f"Created {result['cue_count']} draft source cues in {args.srt_name} "
              f"({len(result['review_flags'])} flagged); quick-check the flags in asr.json, then translate.")
        return 0
    except (Exception, KeyboardInterrupt) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        if any(name in str(error).lower() for name in ("cublas", "cudnn")):
            print("CUDA libraries are missing: install them per the faster-whisper GPU requirements, "
                  "or retry with --device cpu in a new run directory.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
