"""Synthetic fixtures only; no personal or copyrighted subtitle samples."""

import copy
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills/subtitle-translator/scripts/subtitle_io.py"
SPEC = importlib.util.spec_from_file_location("subtitle_io", SCRIPT)
io = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = io
SPEC.loader.exec_module(io)

SRT = "7\n00:00:01,000 --> 00:00:03,000\nHello\nworld\n\n12\n00:00:02,500 --> 00:00:04,000\nOh!\n"
ASS = (
    "[Script Info]\nScriptType: v4.00+\nPlayResX: 1920\n"
    "[V4+ Styles]\nFormat: Name, Fontname\nStyle: Default,Arial\n"
    "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    "Comment: 0,0:00:01.00,0:00:02.00,Default,,0,0,0,,Keep this comment\n"
    "Dialogue: 0,0:00:01.00,0:00:03.00,Default,,0,0,0,,{\\an8}Hello, world\\NAgain\n"
    "Dialogue: 1,0:00:02.50,0:00:04.00,Default,Speaker,3,4,5,,Oh!\n"
)


class SubtitleIOTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)

    def source(self, fmt="ass", text=None):
        path = self.folder / ("source." + fmt)
        path.write_text(text if text is not None else (ASS if fmt == "ass" else SRT), encoding="utf-8")
        return path, io.load_document(path)

    def mapping(self, doc):
        mapping = io.make_map(doc)
        for row, translated in zip(mapping["cues"], ["你好，世界\n再一次", "喔！"]):
            row["translation"] = translated
        return mapping

    def test_srt_preserves_ids_timing_overlap_and_order_all_layouts(self):
        _, doc = self.source("srt")
        for layout in ("translation-only", "translation-first", "source-first"):
            with self.subTest(layout=layout):
                mapping = self.mapping(doc)
                expected, warnings = io.assemble(doc, mapping, layout)
                output = self.folder / (layout + ".srt")
                io.write_new(output, expected)
                self.assertEqual(io.validate(doc, mapping, output, layout), warnings)
                self.assertEqual([cue.id for cue in io.load_document(output).cues], ["7", "12"])
                if layout == "translation-first":
                    self.assertIn("你好，世界\n再一次\nHello\nworld", expected)

    def test_ass_preserves_fields_style_comment_comma_and_alignment(self):
        _, doc = self.source()
        for layout in ("translation-only", "translation-first", "source-first"):
            mapping = self.mapping(doc)
            expected, _ = io.assemble(doc, mapping, layout)
            output = self.folder / (layout + ".ass")
            io.write_new(output, expected)
            io.validate(doc, mapping, output, layout)
            self.assertIn("Style: Default,Arial", expected)
            self.assertIn("Keep this comment", expected)
            first = io.load_document(output).cues[0].source
            self.assertTrue(first.startswith(r"{\an8}"))
            self.assertEqual(first.count(r"{\an8}"), 1)
            if layout == "translation-first":
                self.assertEqual(first, r"{\an8}你好，世界\N再一次\NHello, world\NAgain")

    def test_srt_whitespace_only_separator_lines(self):
        for separator in ("\n \n", "\n\t \n"):
            with self.subTest(separator=repr(separator)):
                source = SRT.replace("world\n\n12", "world" + separator + "12")
                _, doc = self.source("srt", source)
                self.assertEqual([cue.id for cue in doc.cues], ["7", "12"])
                self.assertEqual(doc.cues[0].source, "Hello\nworld")
                mapping = self.mapping(doc)
                expected, _ = io.assemble(doc, mapping, "translation-first")
                self.assertIn("Hello\nworld" + separator + "12", expected)
                output = self.folder / f"spaced{len(separator)}.srt"
                io.write_new(output, expected)
                io.validate(doc, mapping, output, "translation-first")

    def test_ass_empty_dialogue_preserved_and_skipped(self):
        empty = ("Dialogue: 0,0:00:01.50,0:00:02.00,Default,,0,0,0,,\n"
                 "Dialogue: 0,0:00:01.60,0:00:02.00,Default,,0,0,0,,{\\an8}\n")
        source = ASS.replace("Dialogue: 1,", empty + "Dialogue: 1,")
        _, doc = self.source(text=source)
        self.assertEqual([cue.id for cue in doc.cues], ["1", "4"])
        self.assertEqual(doc.skipped, 2)
        mapping = self.mapping(doc)
        expected, _ = io.assemble(doc, mapping, "translation-first")
        self.assertIn(empty, expected)
        output = self.folder / "empty-events.ass"
        io.write_new(output, expected)
        io.validate(doc, mapping, output, "translation-first")

    def test_map_binding_and_alignment_errors(self):
        _, doc = self.source()
        good = self.mapping(doc)
        bad_maps = []
        for key, value in (("source_sha256", "wrong"), ("format", "srt")):
            bad = copy.deepcopy(good)
            bad[key] = value
            bad_maps.append(bad)
        bad = copy.deepcopy(good)
        bad["cues"].reverse()
        bad_maps.append(bad)
        bad = copy.deepcopy(good)
        bad["cues"][1]["id"] = "1"
        bad_maps.append(bad)
        bad = copy.deepcopy(good)
        bad["cues"][0]["source"] = "changed"
        bad_maps.append(bad)
        bad = copy.deepcopy(good)
        bad["cues"].pop()
        bad_maps.append(bad)
        for bad in bad_maps:
            with self.subTest(mapping=bad), self.assertRaises(ValueError):
                io.assemble(doc, bad, "translation-first")

    def test_missing_unsafe_and_failed_translations_rejected(self):
        _, doc = self.source()
        for value in (None, "", " ", "one\n\ntwo", "{\\p1}drawing", "<b>style</b>",
                      r"wrong\Nbreak", "[Translation failed: blocked]", "[TODO]"):
            mapping = self.mapping(doc)
            mapping["cues"][0]["translation"] = value
            with self.subTest(value=value), self.assertRaises(ValueError):
                io.assemble(doc, mapping, "translation-first")

    def test_advanced_markup_rejected(self):
        for markup in (r"{\p1}m 0 0 l 1 1", r"{\k30}Hello", r"{\pos(20,30)}Hello",
                       r"Hello{\i1}world", r"Hello\hworld", r"Hello\nworld"):
            with self.subTest(markup=markup), self.assertRaises(ValueError):
                self.source(text=ASS.replace(r"{\an8}Hello, world\NAgain", markup))
        with self.assertRaises(ValueError):
            self.source("srt", SRT.replace("Hello", "<i>Hello</i>"))

    def test_changed_timing_and_source_detected_even_when_count_matches(self):
        _, doc = self.source()
        mapping = self.mapping(doc)
        expected, _ = io.assemble(doc, mapping, "translation-first")
        output = self.folder / "translated.ass"
        for changed in (expected.replace("0:00:03.00", "0:00:03.01"),
                        expected.replace("Hello, world", "Other source"),
                        expected.replace("你好", "您好"),
                        expected.replace("Style: Default,Arial", "Style: Default,Other")):
            output.write_text(changed, encoding="utf-8")
            with self.assertRaises(ValueError):
                io.validate(doc, mapping, output, "translation-first")

    def test_input_defects_and_duplicate_json_keys(self):
        for source in (SRT.replace("12\n", "7\n"), SRT.replace("00:00:03,000", "00:00:00,500")):
            with self.assertRaises(ValueError):
                self.source("srt", source)
        with self.assertRaises(ValueError):
            self.source(text=ASS.replace("Effect, Text", "Text, Effect"))
        mapping_path = self.folder / "duplicate.json"
        mapping_path.write_text('{"schema":1,"schema":1}', encoding="utf-8")
        with self.assertRaises(ValueError):
            io.read_map(mapping_path)

    def test_bom_crlf_and_punctuation_review(self):
        path = self.folder / "source.srt"
        path.write_bytes(b"\xef\xbb\xbf" + SRT.replace("\n", "\r\n").encode())
        doc = io.load_document(path)
        mapping = self.mapping(doc)
        mapping["cues"][0]["translation"] = "……"
        _, warnings = io.assemble(doc, mapping, "translation-first")
        self.assertEqual(len(warnings), 1)

    def test_cli_prepare_build_validate_and_collision(self):
        path, _ = self.source()
        mapping_path = self.folder / "work.json"
        output = self.folder / "output.ass"
        def run(*args):
            return subprocess.run([sys.executable, str(SCRIPT), *map(str, args)],
                                  capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(run("prepare", path, mapping_path).returncode, 0)
        doc = io.load_document(path)
        mapping_path.write_text(json.dumps(self.mapping(doc), ensure_ascii=False), encoding="utf-8")
        for command in ("build", "validate"):
            result = run(command, path, mapping_path, output, "--layout", "translation-first")
            self.assertEqual(result.returncode, 0, result.stderr)
        before = path.read_bytes()
        self.assertNotEqual(run("build", path, mapping_path, path, "--layout", "translation-only").returncode, 0)
        self.assertEqual(path.read_bytes(), before)
        self.assertNotEqual(run("build", path, mapping_path, output, "--layout", "translation-first").returncode, 0)


if __name__ == "__main__":
    unittest.main()
