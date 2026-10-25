"""Synthetic ASR backend; no weights, network, or real recording required."""

import argparse
import importlib.util
import json
import sys
import tempfile
import unittest
import wave
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace

SCRIPTS = Path(__file__).resolve().parents[1] / "skills/subtitle-translator/scripts"


def load(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


asr = load("transcribe_audio")
io = load("subtitle_io")


@dataclass
class Segment:
    start: float = 1.0
    end: float = 2.0
    text: str = "こんにちは。"
    avg_logprob: float = -0.2
    no_speech_prob: float = 0.1
    compression_ratio: float = 1.0
    words: list = field(default_factory=list)


class TranscriptionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.audio = self.root / "audio.wav"
        with wave.open(str(self.audio), "wb") as handle:
            handle.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
            handle.writeframes(b"\0\0" * 16000 * 5)
        self.args = argparse.Namespace(audio=self.audio, output_dir=self.root / "run", model="small",
                                       device="cpu", compute_type="int8", language="ja", offset=3.0,
                                       local_files_only=True, no_vad=False)

    def factory(self, rows=None, error=None):
        owner = self

        class Model:
            def __init__(self, model, **kwargs):
                owner.model_args = (model, kwargs)

            def transcribe(self, audio, **kwargs):
                owner.transcribe_args = kwargs

                def generate():
                    yield from rows if rows is not None else [Segment()]
                    if error:
                        raise RuntimeError(error)

                return generate(), SimpleNamespace(language="ja", language_probability=0.98,
                                                    duration_after_vad=1.0)
        return Model

    def test_full_handoff_preserves_original_and_timing(self):
        original = self.audio.read_bytes()
        result = asr.run(self.args, self.factory())
        self.assertEqual(result["status"], "complete")
        self.assertEqual(self.audio.read_bytes(), original)
        self.assertEqual(self.transcribe_args["task"], "transcribe")
        self.assertTrue(self.transcribe_args["vad_filter"])
        self.assertTrue(self.model_args[1]["local_files_only"])
        source = self.args.output_dir / "source.srt"
        self.assertIn("00:00:04,000 --> 00:00:05,000", source.read_text(encoding="utf-8"))
        raw = json.loads((self.args.output_dir / "segments.jsonl").read_text(encoding="utf-8"))
        self.assertEqual(raw["start"], 1.0)  # Raw data stays WAV-relative.
        self.assertEqual(result["source_srt_sha256"], asr.sha256(source))
        doc = io.load_document(source)
        mapping = io.make_map(doc)
        mapping["cues"][0]["translation"] = "你好。"
        text, _ = io.assemble(doc, mapping, "translation-first")
        output = self.root / "bilingual.srt"
        io.write_new(output, text)
        self.assertEqual(io.validate(doc, mapping, output, "translation-first"), [])
        self.assertIn("你好。\nこんにちは。", text)

    def test_collision_does_not_overwrite(self):
        asr.run(self.args, self.factory())
        report = self.args.output_dir / "asr.json"
        before = report.read_bytes()
        with self.assertRaises(FileExistsError):
            asr.run(self.args, self.factory())
        self.assertEqual(report.read_bytes(), before)

    def test_partial_failure_preserves_evidence_without_srt(self):
        with self.assertRaisesRegex(RuntimeError, "interrupted"):
            asr.run(self.args, self.factory(error="interrupted"))
        self.assertFalse((self.args.output_dir / "source.srt").exists())
        report = json.loads((self.args.output_dir / "asr.json").read_text(encoding="utf-8"))
        self.assertEqual(report["status"], "failed")
        self.assertEqual(len((self.args.output_dir / "segments.jsonl").read_text(encoding="utf-8").splitlines()), 1)

    def test_no_speech_fails_instead_of_empty_translation_source(self):
        with self.assertRaisesRegex(ValueError, "No speech"):
            asr.run(self.args, self.factory(rows=[]))
        self.assertFalse((self.args.output_dir / "source.srt").exists())

    def test_auto_language_and_vad_override(self):
        self.args.language = "auto"
        self.args.no_vad = True
        asr.run(self.args, self.factory())
        self.assertIsNone(self.transcribe_args["language"])
        self.assertFalse(self.transcribe_args["vad_filter"])

    def test_flags_do_not_remove_genuine_repetitions(self):
        rows = [dict(start=1, end=2, text="Yes", avg_logprob=-1.2),
                dict(start=2, end=3, text="Yes", no_speech_prob=0.8)]
        text, flags = asr.render_segments(rows, 5)
        self.assertEqual(text.count("Yes"), 2)
        self.assertIn("repeated_adjacent_text", flags[1]["reasons"])
        self.assertIn("low_log_probability", flags[0]["reasons"])

    def test_bad_segments_rejected(self):
        for row in [dict(start=-1, end=2, text="x"), dict(start=1, end=7, text="x"),
                    dict(start=1, end=1, text="x"), dict(start=float("nan"), end=2, text="x"),
                    dict(start=1, end=2, text=" "), dict(start=1, end=2, text="<bad>"),
                    dict(start=1, end=1.00001, text="x")]:
            with self.subTest(row=row), self.assertRaises(ValueError):
                asr.render_segments([row], 5)
        with self.assertRaises(ValueError):
            asr.render_segments([dict(start=1, end=2, text="x")], 5, offset=-2)
        with self.assertRaises(ValueError):
            asr.render_segments([dict(start=2, end=3, text="x"), dict(start=1, end=2, text="y")], 5)

    def test_timestamp_carry_and_unicode_whitespace(self):
        text, _ = asr.render_segments([dict(start=59.9996, end=61, text=" Hello\n\n世界 ")], 65)
        self.assertIn("00:01:00,000", text)
        self.assertIn("Hello 世界", text)

    def test_wrong_audio_format_fails_before_run(self):
        with wave.open(str(self.audio), "wb") as handle:
            handle.setparams((2, 2, 16000, 0, "NONE", "not compressed"))
            handle.writeframes(b"\0" * 16000)
        with self.assertRaisesRegex(ValueError, "mono"):
            asr.run(self.args, self.factory())
        self.assertFalse(self.args.output_dir.exists())


if __name__ == "__main__":
    unittest.main()
