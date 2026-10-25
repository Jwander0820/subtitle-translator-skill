#!/usr/bin/env python3
"""Prepare, assemble, and verify plain SRT / alignment-only ASS. No translation."""

import argparse
import hashlib
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Cue:
    id: str
    start: int  # Text offsets in the normalized document, not display times.
    end: int
    source: str
    prefix: str = ""


@dataclass(frozen=True)
class Document:
    text: str
    sha256: str
    format: str
    cues: list[Cue]
    skipped: int = 0  # Empty ASS Dialogue events, preserved untranslated.


def read_text(path):
    raw = Path(path).read_bytes()
    text = raw.decode("utf-8-sig").replace("\r\n", "\n")
    if "\r" in text or "\x00" in text:
        raise ValueError("Unsupported line endings or NUL bytes; inspect the encoding.")
    return text, hashlib.sha256(raw).hexdigest()


def time_value(value, ass=False):
    pattern = r"(\d+):([0-5]\d):([0-5]\d)\.([0-9]{2})" if ass else (
        r"(\d{2,}):([0-5]\d):([0-5]\d),([0-9]{3})"
    )
    match = re.fullmatch(pattern, value)
    if not match:
        raise ValueError(f"Unsupported timestamp: {value!r}")
    h, m, s, fraction = map(int, match.groups())
    return (h * 3600 + m * 60 + s) * 1000 + fraction * (10 if ass else 1)


def check_timing(start, end, ass=False):
    if time_value(end, ass) <= time_value(start, ass):
        raise ValueError(f"Non-positive cue duration: {start} --> {end}")


def ass_parts(text):
    prefix = re.match(r"^(?:\{\\an[1-9]\})*", text).group()
    body = text[len(prefix):]
    # Refuse unknown scope, drawing, karaoke, soft breaks, or literal overrides.
    if re.search(r"[{}]", body) or "\\" in body.replace(r"\N", ""):
        raise ValueError("Advanced ASS markup requires format-aware handling.")
    return prefix, body


def load_document(path):
    text, digest = read_text(path)
    fmt = Path(path).suffix.lower().lstrip(".")
    cues = []
    skipped = 0
    if fmt == "srt":
        # Whitespace-only lines separate cues, like empty ones.
        for block in re.finditer(r"[^\n]*\S[^\n]*(?:\n[^\n]*\S[^\n]*)*", text):
            parts = block.group().split("\n", 2)
            if len(parts) != 3 or not re.fullmatch(r"[0-9]+", parts[0]):
                raise ValueError("Unsupported SRT cue: numeric ID, timing, and text required.")
            timing = parts[1].split(" --> ")
            if len(timing) != 2:
                raise ValueError("Unsupported SRT timing line.")
            check_timing(*timing)
            if re.search(r"[<>{}\\]", parts[2]):
                raise ValueError("SRT markup requires format-aware handling.")
            start = block.start() + len(parts[0]) + len(parts[1]) + 2
            cues.append(Cue(parts[0], start, block.end(), parts[2]))
    elif fmt == "ass":
        section = ""
        fields = None
        offset = 0
        ordinal = 0
        for line in text.splitlines(keepends=True):
            bare = line.removesuffix("\n")
            stripped = bare.strip()
            if stripped.startswith("[") and stripped.endswith("]"):
                section = stripped.casefold()
                fields = None
            elif section == "[events]" and bare.casefold().startswith("format:"):
                fields = [field.strip().casefold() for field in bare.split(":", 1)[1].split(",")]
                if (not fields or fields[-1] != "text" or len(fields) != len(set(fields))
                        or not {"start", "end", "style"}.issubset(fields)):
                    raise ValueError("Unsupported ASS Events Format; unique fields and Text last required.")
            elif bare.casefold().startswith("dialogue:"):
                if section != "[events]" or not fields:
                    raise ValueError("ASS Dialogue requires an Events Format declaration.")
                values = bare.split(":", 1)[1].split(",", len(fields) - 1)
                if len(values) != len(fields):
                    raise ValueError("Malformed ASS Dialogue fields.")
                check_timing(values[fields.index("start")].strip(),
                             values[fields.index("end")].strip(), ass=True)
                ordinal += 1
                prefix, body = ass_parts(values[-1])
                if not body.strip():
                    skipped += 1  # Keep the ordinal so later IDs stay stable.
                else:
                    start = offset + len(bare) - len(values[-1])
                    cues.append(Cue(str(ordinal), start, offset + len(bare), values[-1], prefix))
            offset += len(line)
    else:
        raise ValueError("Only .srt and .ass are supported.")
    if not cues:
        raise ValueError("No subtitle cues found.")
    if len({cue.id for cue in cues}) != len(cues):
        raise ValueError("Duplicate cue IDs; inspect the source before translating.")
    if any(not cue.source.strip() for cue in cues):
        raise ValueError("Empty source cue; inspect before translating.")
    return Document(text, digest, fmt, cues, skipped)


def make_map(doc):
    return {
        "schema": 1, "source_sha256": doc.sha256, "format": doc.format,
        "cues": [{"id": cue.id, "source": cue.source, "translation": None} for cue in doc.cues],
    }


def reject_duplicate_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def read_map(path):
    return json.loads(read_text(path)[0], object_pairs_hook=reject_duplicate_keys)


def assemble(doc, mapping, layout):
    if not isinstance(mapping, dict) or mapping.get("schema") != 1:
        raise ValueError("Unsupported translation-map schema.")
    if mapping.get("source_sha256") != doc.sha256 or mapping.get("format") != doc.format:
        raise ValueError("Translation map does not match this exact source file.")
    rows = mapping.get("cues")
    if not isinstance(rows, list) or len(rows) != len(doc.cues):
        raise ValueError("Translation-map cue count mismatch.")
    if layout not in {"translation-only", "translation-first", "source-first"}:
        raise ValueError("Unknown layout.")
    pieces = []
    warnings = []
    cursor = 0
    for cue, row in zip(doc.cues, rows):
        if not isinstance(row, dict) or row.get("id") != cue.id or row.get("source") != cue.source:
            raise ValueError(f"Cue {cue.id}: changed/reordered ID or source text.")
        translated = row.get("translation")
        if not isinstance(translated, str) or not translated.strip():
            raise ValueError(f"Cue {cue.id}: missing or empty translation.")
        if any(char in translated for char in ("\r", "\x00", "\\", "{", "}", "<", ">")):
            raise ValueError(f"Cue {cue.id}: translation must be plain text with real newlines.")
        if any(not line.strip() for line in translated.split("\n")):
            raise ValueError(f"Cue {cue.id}: blank translation line.")
        if re.search(r"\[\s*(?:translation failed|translation error|untranslated|TODO)\b",
                     translated, re.IGNORECASE):
            raise ValueError(f"Cue {cue.id}: unfinished/error placeholder.")
        if not any(char.isalnum() for char in translated):
            warnings.append(f"Cue {cue.id}: punctuation-only translation needs semantic review.")
        if doc.format == "ass":
            source = cue.source[len(cue.prefix):]
            translated = translated.replace("\n", r"\N")
            separator = r"\N"
        else:
            source = cue.source
            separator = "\n"
        if layout == "translation-only":
            body = translated
        elif layout == "translation-first":
            body = translated + separator + source
        else:
            body = source + separator + translated
        pieces.extend((doc.text[cursor:cue.start], cue.prefix + body))
        cursor = cue.end
    pieces.append(doc.text[cursor:])
    return "".join(pieces), warnings


def write_new(path, text):
    # Exclusive creation refuses source overwrite, collisions, and existing symlinks.
    with Path(path).open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(text)


def validate(doc, mapping, output, layout):
    if Path(output).suffix.lower() != "." + doc.format:
        raise ValueError("Output extension must match source format.")
    expected, warnings = assemble(doc, mapping, layout)
    actual, _ = read_text(output)
    if actual != expected:
        raise ValueError("Output differs from the source-bound map/layout; inspect manual edits.")
    parsed = load_document(output)
    if len(parsed.cues) != len(doc.cues):
        raise ValueError("Output cue count mismatch.")
    return warnings


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("prepare", help="Create a source-bound JSON translation map.")
    prepare.add_argument("source", type=Path)
    prepare.add_argument("mapping", type=Path)
    for name in ("build", "validate"):
        sub = commands.add_parser(name)
        sub.add_argument("source", type=Path)
        sub.add_argument("mapping", type=Path)
        sub.add_argument("output", type=Path)
        sub.add_argument("--layout", required=True,
                         choices=("translation-only", "translation-first", "source-first"))
    args = parser.parse_args()
    try:
        doc = load_document(args.source)
        if args.command == "prepare":
            write_new(args.mapping, json.dumps(make_map(doc), ensure_ascii=False, indent=2) + "\n")
            print(f"Prepared {len(doc.cues)} cues; fill translation fields before building.")
            if doc.skipped:
                print(f"Skipped {doc.skipped} empty Dialogue events; they stay unchanged.")
            return 0
        mapping = read_map(args.mapping)
        if args.command == "build":
            if args.output.suffix.lower() != "." + doc.format:
                raise ValueError("Output extension must match source format.")
            expected, _ = assemble(doc, mapping, args.layout)
            write_new(args.output, expected)
        warnings = validate(doc, mapping, args.output, args.layout)
        print(f"PASS: {len(doc.cues)} cues; source, layout, timing and non-text structure match.")
        for warning in warnings:
            print(f"REVIEW: {warning}", file=sys.stderr)
        return 0
    except (ValueError, OSError, UnicodeError, TypeError, KeyError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
