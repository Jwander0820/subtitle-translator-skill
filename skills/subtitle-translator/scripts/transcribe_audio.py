#!/usr/bin/env python3
"""Local faster-whisper transcription of a selected mono 16 kHz PCM WAV.

Produces source.srt and asr.json in a NEW run directory. No translation or OCR.
"""

import argparse
import hashlib
import json
import math
import sys
import wave
from dataclasses import asdict
from importlib.metadata import version
from pathlib import Path


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def wav_duration(path):
    with wave.open(str(path), "rb") as audio:
        if (audio.getnchannels(), audio.getframerate(), audio.getsampwidth()) != (1, 16000, 2):
            raise ValueError("Expected mono 16 kHz signed 16-bit PCM WAV; select/convert the audio first.")
        duration = audio.getnframes() / audio.getframerate()
    if duration <= 0:
        raise ValueError("Empty WAV input.")
    return duration


def timestamp(milliseconds):
    seconds, ms = divmod(milliseconds, 1000)
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours:02}:{minutes:02}:{seconds:02},{ms:03}"


def render_segments(segments, duration, offset=0):
    """Keep every nonempty segment; flags are review hints, never deletions."""
    if not math.isfinite(offset):
        raise ValueError("Offset must be finite.")
    blocks, flags = [], []
    previous_start, previous_end, previous_text = -1, 0, None
    for index, row in enumerate(segments, 1):
        start, end = row["start"], row["end"]
        if not all(math.isfinite(t) for t in (start, end)) or not 0 <= start < end <= duration + 0.1:
            raise ValueError(f"Segment {index}: invalid or out-of-audio timing.")
        if start < previous_start:
            raise ValueError(f"Segment {index}: out-of-order timing.")
        a, b = round((start + offset) * 1000), round((end + offset) * 1000)
        if a < 0 or b <= a:
            raise ValueError(f"Segment {index}: invalid timing after offset/rounding.")
        text = " ".join(row["text"].split())
        if not text:
            raise ValueError(f"Segment {index}: empty text; inspect raw ASR output.")
        if any(char in text for char in "<>{}\\\x00"):
            raise ValueError(f"Segment {index}: unsupported literal markup; inspect raw ASR output.")
        reasons = []
        if row.get("avg_logprob", 0) < -1:
            reasons.append("low_log_probability")
        if row.get("no_speech_prob", 0) > 0.6:
            reasons.append("possible_non_speech")
        if row.get("compression_ratio", 0) > 2.4:
            reasons.append("possible_repetition")
        if text == previous_text:
            reasons.append("repeated_adjacent_text")
        if end - start > 7 or len(text) > 84:
            reasons.append("long_cue_review_segmentation")
        if start < previous_end:
            reasons.append("overlap")
        if start - previous_end > 15:
            reasons.append("gap_before_check_audio")
        if reasons:
            flags.append({"cue": index, "start": a / 1000, "end": b / 1000, "reasons": reasons})
        blocks.append(f"{index}\n{timestamp(a)} --> {timestamp(b)}\n{text}\n")
        previous_start, previous_end, previous_text = start, end, text
    if not blocks:
        raise ValueError("No speech segments; do not create an empty translation source.")
    if duration - previous_end > 15:
        flags.append({"cue": len(blocks), "reasons": ["trailing_gap_check_audio"]})
    return "\n".join(blocks), flags


def write_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def run(args, model_factory=None):
    duration = wav_duration(args.audio)
    if not math.isfinite(args.offset):
        raise ValueError("Offset must be finite.")
    if args.output_dir.exists():
        raise FileExistsError("Run directory already exists; reuse completed artifacts or choose a new directory.")
    if model_factory is None:
        from faster_whisper import WhisperModel
        model_factory = WhisperModel
        backend_version = version("faster-whisper")
    else:
        backend_version = "test-double"
    digest = sha256(args.audio)
    args.output_dir.mkdir(parents=True, exist_ok=False)
    manifest = {
        "schema": 1, "status": "running", "backend": "faster-whisper",
        "backend_version": backend_version, "audio": str(args.audio.resolve()),
        "audio_sha256": digest, "duration": duration,
        "model": args.model, "device": args.device, "compute_type": args.compute_type,
        "requested_language": args.language, "offset_seconds": args.offset,
        "task": "transcribe", "vad_filter": not args.no_vad, "beam_size": 5,
        "word_timestamps": True, "local_files_only": args.local_files_only,
    }
    report = args.output_dir / "asr.json"
    write_json(report, manifest)
    try:
        model = model_factory(args.model, device=args.device, compute_type=args.compute_type,
                              local_files_only=args.local_files_only)
        segments, info = model.transcribe(
            str(args.audio), language=None if args.language == "auto" else args.language,
            task="transcribe", beam_size=5, vad_filter=not args.no_vad, word_timestamps=True,
        )
        manifest.update(language=info.language, language_probability=info.language_probability,
                        duration_after_vad=info.duration_after_vad)
        rows = []
        # Iteration performs inference. Retain partial results if a later segment fails.
        with (args.output_dir / "segments.jsonl").open("x", encoding="utf-8") as handle:
            for segment in segments:
                row = asdict(segment)
                handle.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n")
                handle.flush()
                rows.append(row)
                print(f"Transcribed through {row['end']:.1f}s / {duration:.1f}s", flush=True)
        srt, flags = render_segments(rows, duration, args.offset)
        source = args.output_dir / "source.srt"
        with source.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(srt)
        manifest.update(status="complete", cue_count=len(rows), review_flags=flags,
                        source_srt_sha256=sha256(source))
        write_json(report, manifest)
        return manifest
    except BaseException as error:
        manifest.update(status="failed", error=str(error))
        write_json(report, manifest)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("audio", type=Path)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--language", required=True, help="Source language: ja, en, etc.; auto only when unknown.")
    parser.add_argument("--model", default="small", help="Multilingual model name or local CTranslate2 model directory.")
    parser.add_argument("--device", choices=("cpu", "cuda"), default="cpu")
    parser.add_argument("--compute-type", default="int8")
    parser.add_argument("--offset", type=float, default=0, help="Seconds added to WAV-relative times to match video.")
    parser.add_argument("--local-files-only", action="store_true", help="Fail instead of downloading missing model files.")
    parser.add_argument("--no-vad", action="store_true", help="Disable VAD for a justified comparison run.")
    args = parser.parse_args()
    try:
        result = run(args)
        print(f"Created {result['cue_count']} draft source cues; review ASR before translation.")
        return 0
    except (Exception, KeyboardInterrupt) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
