#!/usr/bin/env python3
"""Prepare, fill, assemble, and verify SRT / ASS translation maps. No translation."""

import argparse
import hashlib
import json
import os
import re
import sys
import unicodedata
from dataclasses import dataclass
from pathlib import Path

LAYOUTS = ("translation-only", "translation-first", "source-first")
PLACEHOLDER = re.compile(
    r"\[\s*(?:translation failed|translation error|untranslated|TODO)\b", re.IGNORECASE)


@dataclass(frozen=True)
class Cue:
    id: str
    start: int  # Text offsets in the normalized document, not display times.
    end: int
    source: str
    prefix: str = ""
    raw: bool = False  # Markup the helper does not interpret; the map holds the full cue text.
    time: str = ""  # Start timestamp, verbatim.
    seconds: float = 0.0


@dataclass(frozen=True)
class Document:
    text: str
    sha256: str
    format: str
    cues: list[Cue]
    skipped: int = 0  # Cues without text, preserved untranslated.


def read_text(path):
    raw = Path(path).read_bytes()
    try:
        text = raw.decode("utf-8-sig").replace("\r\n", "\n")
    except UnicodeDecodeError:
        raise ValueError(f"{path}: not UTF-8; convert the encoding first (it is not guessed).") from None
    if "\r" in text or "\x00" in text:
        raise ValueError(f"{path}: unsupported line endings or NUL bytes; inspect the encoding.")
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


def duration(start, end, ass=False):
    milliseconds = time_value(end, ass) - time_value(start, ass)
    if milliseconds <= 0:
        raise ValueError(f"Non-positive cue duration: {start} --> {end}")
    return milliseconds / 1000


def ass_parts(text):
    prefix = re.match(r"^(?:\{\\an[1-9]\})*", text).group()
    body = text[len(prefix):]
    # Other overrides, drawing, karaoke, soft breaks: scope is unknown, so pass through raw.
    if re.search(r"[{}]", body) or "\\" in body.replace(r"\N", ""):
        return "", text, True
    return prefix, body, False


def load_document(path):
    text, digest = read_text(path)
    fmt = Path(path).suffix.lower().lstrip(".")
    cues = []
    skipped = 0
    if fmt == "srt":
        # Whitespace-only lines separate cues, like empty ones.
        for block in re.finditer(r"[^\n]*\S[^\n]*(?:\n[^\n]*\S[^\n]*)*", text):
            parts = block.group().split("\n", 2)
            if len(parts) < 2 or not re.fullmatch(r"[0-9]+", parts[0]):
                raise ValueError(f"Unsupported SRT cue at {parts[0]!r}: numeric ID and timing required.")
            timing = parts[1].split(" --> ")
            if len(timing) != 2:
                raise ValueError(f"Cue {parts[0]}: unsupported SRT timing line.")
            seconds = duration(*timing)
            if len(parts) == 2:
                skipped += 1
                continue
            start = block.start() + len(parts[0]) + len(parts[1]) + 2
            raw = bool(re.search(r"[<>{}\\]", parts[2]))
            cues.append(Cue(parts[0], start, block.end(), parts[2], raw=raw,
                            time=timing[0], seconds=seconds))
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
                begin = values[fields.index("start")].strip()
                seconds = duration(begin, values[fields.index("end")].strip(), ass=True)
                ordinal += 1
                prefix, body, raw = ass_parts(values[-1])
                if not body.strip():
                    skipped += 1  # Keep the ordinal so later IDs stay stable.
                else:
                    start = offset + len(bare) - len(values[-1])
                    cues.append(Cue(str(ordinal), start, offset + len(bare), values[-1], prefix,
                                    raw, begin, seconds))
            offset += len(line)
    else:
        raise ValueError("Only .srt and .ass are supported.")
    if not cues:
        raise ValueError("No subtitle cues found.")
    if len({cue.id for cue in cues}) != len(cues):
        raise ValueError("Duplicate cue IDs; inspect the source before translating.")
    return Document(text, digest, fmt, cues, skipped)


def make_map(doc):
    rows = []
    for cue in doc.cues:
        row = {"id": cue.id, "start": cue.time, "seconds": cue.seconds,
               "source": cue.source, "translation": None}
        if cue.raw:
            row["raw"] = True
        rows.append(row)
    return {"schema": 1, "source_sha256": doc.sha256, "format": doc.format, "cues": rows}


def dump_map(mapping):
    """One cue per line: cheap to read in ranges and to diff."""
    head = {key: value for key, value in mapping.items() if key != "cues"}
    rows = ",\n".join(json.dumps(row, ensure_ascii=False) for row in mapping["cues"])
    return json.dumps(head, ensure_ascii=False)[:-1] + ', "cues": [\n' + rows + "\n]}\n"


def reject_duplicate_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def read_json(path):
    return json.loads(read_text(path)[0], object_pairs_hook=reject_duplicate_keys)


def check_map(mapping):
    if not isinstance(mapping, dict) or mapping.get("schema") != 1:
        raise ValueError("Unsupported translation-map schema.")
    rows = mapping.get("cues")
    if not isinstance(rows, list) or not all(
            isinstance(row, dict) and isinstance(row.get("id"), str) for row in rows):
        raise ValueError("Malformed translation-map cues.")
    return rows


def check_translation(cue_id, text, raw, fmt):
    if not isinstance(text, str) or not text.strip():
        raise ValueError(f"Cue {cue_id}: missing or empty translation.")
    if "\r" in text or "\x00" in text:
        raise ValueError(f"Cue {cue_id}: unsupported control characters.")
    if any(not line.strip() for line in text.split("\n")):
        raise ValueError(f"Cue {cue_id}: blank translation line.")
    if PLACEHOLDER.search(text):
        raise ValueError(f"Cue {cue_id}: unfinished/error placeholder.")
    if raw:
        if fmt == "ass" and "\n" in text:
            raise ValueError(f"Cue {cue_id}: raw ASS text is one line; write \\N for breaks.")
    elif any(char in text for char in "\\{}<>"):
        raise ValueError(f"Cue {cue_id}: translation must be plain text with real newlines.")


def width(text):
    """Display columns: East Asian wide characters count as two."""
    return sum(0 if unicodedata.combining(char) else
               2 if unicodedata.east_asian_width(char) in "WF" else 1 for char in text)


def id_ranges(ids, limit=40):
    groups = []
    for cue_id in ids:
        number = int(cue_id)
        if groups and number == groups[-1][1] + 1:
            groups[-1][1] = number
        else:
            groups.append([number, number])
    shown = ", ".join(str(a) if a == b else f"{a}-{b}" for a, b in groups[:limit])
    return shown + (f", ... (+{len(groups) - limit} more ranges)" if len(groups) > limit else "")


def read_glossary(path):
    """Source/target pairs from a Markdown table (first two columns) or TSV lines."""
    pairs = []
    for line in read_text(path)[0].split("\n"):
        line = line.strip()
        cells = line.strip("|").split("|") if line.startswith("|") else line.split("\t")
        cells = [cell.strip() for cell in cells]
        if (len(cells) < 2 or not cells[0] or not cells[1] or cells[0].startswith("<")
                or set(cells[0]) <= set("-: ") or cells[0].casefold() == "source"):
            continue
        pairs.append((cells[0], cells[1]))
    if not pairs:
        raise ValueError(f"{path}: no glossary rows found (Markdown table or TSV expected).")
    return pairs


def assemble(doc, mapping, layout, max_cps=20, max_width=42, glossary=()):
    rows = check_map(mapping)
    if mapping.get("source_sha256") != doc.sha256 or mapping.get("format") != doc.format:
        raise ValueError("Translation map does not match this exact source file.")
    if len(rows) != len(doc.cues):
        raise ValueError("Translation-map cue count mismatch.")
    if layout not in LAYOUTS:
        raise ValueError("Unknown layout.")
    pieces = []
    notes = {}  # Review label -> cue IDs; flags for the agent, never automatic fixes.
    cursor = 0
    for cue, row in zip(doc.cues, rows):
        if (row["id"] != cue.id or row.get("source") != cue.source
                or bool(row.get("raw")) != cue.raw):
            raise ValueError(f"Cue {cue.id}: changed/reordered ID or source text.")
        translated = row.get("translation")
        check_translation(cue.id, translated, cue.raw, doc.format)
        if cue.raw:
            notes.setdefault("raw-markup cue inserted verbatim; check tags and layout", []).append(cue.id)
            body = translated
        else:
            if not any(char.isalnum() for char in translated):
                notes.setdefault("punctuation-only translation", []).append(cue.id)
            if max_cps and width(translated.replace("\n", "")) / cue.seconds > max_cps:
                notes.setdefault(f"reading speed above {max_cps:g} columns/s", []).append(cue.id)
            if max_width and any(width(line) > max_width for line in translated.split("\n")):
                notes.setdefault(f"line wider than {max_width} columns", []).append(cue.id)
            source = cue.source[len(cue.prefix):]
            separator = "\n"
            if doc.format == "ass":
                translated = translated.replace("\n", r"\N")
                separator = r"\N"
            if translated.strip() == source.strip() and any(char.isalnum() for char in source):
                notes.setdefault("translation identical to source", []).append(cue.id)
            if layout == "translation-only":
                body = translated
            elif layout == "translation-first":
                body = translated + separator + source
            else:
                body = source + separator + translated
        pieces.extend((doc.text[cursor:cue.start], cue.prefix + body))
        cursor = cue.end
    pieces.append(doc.text[cursor:])
    texts = [row["translation"] for row in rows]
    for term, target in glossary:
        for index, cue in enumerate(doc.cues):
            # Meaning may be redistributed across adjacent cues, so accept neighbours.
            if term in cue.source and target not in "\n".join(texts[max(index - 1, 0):index + 2]):
                notes.setdefault(f"glossary {term} -> {target} not found in or beside", []).append(cue.id)
    return "".join(pieces), [f"{label}: cues {id_ranges(ids)}" for label, ids in notes.items()]


def write_new(path, text):
    # Exclusive creation refuses source overwrite, collisions, and existing symlinks.
    with Path(path).open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(text)


def status(mapping):
    rows = check_map(mapping)
    missing = [row["id"] for row in rows if not isinstance(row.get("translation"), str)]
    lines = [f"{len(rows) - len(missing)}/{len(rows)} cues translated."]
    if missing:
        lines.append(f"Missing: {id_ranges(missing)}")
    raw = [row["id"] for row in rows if row.get("raw")]
    if raw:
        lines.append(f"Raw-markup cues (translation = complete cue text, tags included): {id_ranges(raw)}")
    return "\n".join(lines)


def fill(map_path, batch_path, replace):
    """Merge {"id": "translation"} into the map: all or nothing, then replace atomically."""
    mapping = read_json(map_path)
    rows = {row["id"]: row for row in check_map(mapping)}
    batch = read_json(batch_path)
    if not isinstance(batch, dict) or not batch:
        raise ValueError('Batch must be a nonempty JSON object: {"cue id": "translation"}.')
    unknown = [key for key in batch if key not in rows]
    if unknown:
        raise ValueError(f"Batch IDs not in the map: {', '.join(unknown[:20])}")
    conflicts = []
    for key, text in batch.items():
        check_translation(key, text, bool(rows[key].get("raw")), mapping.get("format"))
        if rows[key].get("translation") not in (None, text) and not replace:
            conflicts.append(key)
    if conflicts:
        raise ValueError(f"Already translated differently: cues {id_ranges(conflicts)}; "
                         "pass --replace for a deliberate revision.")
    for key, text in batch.items():
        rows[key]["translation"] = text
    temporary = Path(map_path).with_name(Path(map_path).name + ".tmp")
    temporary.write_text(dump_map(mapping), encoding="utf-8", newline="\n")
    os.replace(temporary, map_path)
    return f"Merged {len(batch)} cues. " + status(mapping)


def validate(doc, mapping, output, layout, **checks):
    if Path(output).suffix.lower() != "." + doc.format:
        raise ValueError("Output extension must match source format.")
    expected, notes = assemble(doc, mapping, layout, **checks)
    actual, _ = read_text(output)
    if actual != expected:
        raise ValueError("Output differs from the source-bound map/layout; inspect manual edits.")
    parsed = load_document(output)
    if len(parsed.cues) != len(doc.cues):
        raise ValueError("Output cue count mismatch.")
    return notes


def main():
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(errors="backslashreplace")  # Legacy consoles must not crash reports.
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    sub = commands.add_parser("prepare", help="Create a source-bound JSON translation map.")
    sub.add_argument("source", type=Path)
    sub.add_argument("mapping", type=Path)
    sub = commands.add_parser("fill", help='Merge a {"cue id": "translation"} JSON batch into the map.')
    sub.add_argument("mapping", type=Path)
    sub.add_argument("batch", type=Path)
    sub.add_argument("--replace", action="store_true", help="Allow changing existing translations.")
    sub = commands.add_parser("status", help="Report translated and missing cue ranges.")
    sub.add_argument("mapping", type=Path)
    for name in ("build", "validate"):
        sub = commands.add_parser(name)
        sub.add_argument("source", type=Path)
        sub.add_argument("mapping", type=Path)
        sub.add_argument("output", type=Path)
        sub.add_argument("--layout", required=True, choices=LAYOUTS)
        sub.add_argument("--max-cps", type=float, default=20,
                         help="Review flag: display columns per second (wide characters = 2); 0 disables.")
        sub.add_argument("--max-line-width", type=int, default=42,
                         help="Review flag: display columns per translated line; 0 disables.")
        sub.add_argument("--glossary", type=Path,
                         help="Markdown table or TSV of source/target terms to check for.")
    args = parser.parse_args()
    try:
        if args.command == "status":
            print(status(read_json(args.mapping)))
            return 0
        if args.command == "fill":
            print(fill(args.mapping, args.batch, args.replace))
            return 0
        doc = load_document(args.source)
        if args.command == "prepare":
            write_new(args.mapping, dump_map(make_map(doc)))
            print(f"Prepared {len(doc.cues)} cues; fill translation fields before building.")
            raw = [cue.id for cue in doc.cues if cue.raw]
            if raw:
                print(f"Raw-markup cues (write the complete cue text, tags included): {id_ranges(raw)}")
            if doc.skipped:
                print(f"Skipped {doc.skipped} cues without text; they stay unchanged.")
            return 0
        mapping = read_json(args.mapping)
        checks = {"max_cps": args.max_cps, "max_width": args.max_line_width,
                  "glossary": read_glossary(args.glossary) if args.glossary else ()}
        if args.command == "build":
            if args.output.suffix.lower() != "." + doc.format:
                raise ValueError("Output extension must match source format.")
            expected, _ = assemble(doc, mapping, args.layout, **checks)
            write_new(args.output, expected)
        notes = validate(doc, mapping, args.output, args.layout, **checks)
        print(f"PASS: {len(doc.cues)} cues; source, layout, timing and non-text structure match.")
        for note in notes:
            print(f"REVIEW: {note}", file=sys.stderr)
        return 0
    except (ValueError, OSError, UnicodeError, TypeError, KeyError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
