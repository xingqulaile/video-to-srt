#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

SOFT_BREAK_CHARS = set("，。！？；：、,!?;: ")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Apply glossary-based replacements to an SRT file without changing timestamps.",
    )
    parser.add_argument("input", help="Path to the source SRT file")
    parser.add_argument("--terms-file", required=True, help="TSV file with wrong<TAB>correct pairs")
    parser.add_argument("-o", "--output", help="Output SRT path. Defaults to <input_stem>.corrected.srt")
    parser.add_argument(
        "--max-line-length",
        type=int,
        default=18,
        help="Maximum characters per subtitle line. Defaults to 18.",
    )
    return parser.parse_args()


def read_terms(path: Path) -> list[tuple[str, str]]:
    replacements: list[tuple[str, str]] = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) != 2:
            raise ValueError(f"术语表格式错误，必须是两列 TSV: {line}")
        wrong, correct = parts[0].strip(), parts[1].strip()
        if wrong and correct:
            replacements.append((wrong, correct))
    replacements.sort(key=lambda item: len(item[0]), reverse=True)
    return replacements


def is_timestamp_line(line: str) -> bool:
    return " --> " in line


def visible_length(text: str) -> int:
    return sum(1 for char in text if not char.isspace())


def find_split_index(text: str, max_line_length: int) -> int:
    visible = 0
    candidate_positions: list[int] = []
    for index, char in enumerate(text):
        if not char.isspace():
            visible += 1
        if char in SOFT_BREAK_CHARS:
            candidate_positions.append(index + 1)
        if visible >= max_line_length:
            threshold = max(1, max_line_length // 2)
            for position in reversed(candidate_positions):
                if visible_length(text[:position]) >= threshold:
                    return position
            return index + 1
    return len(text)


def split_subtitle_text(text: str, max_line_length: int) -> list[str]:
    if visible_length(text) <= max_line_length:
        return [text.strip()]

    remaining = text.strip()
    chunks: list[str] = []
    while remaining:
        if visible_length(remaining) <= max_line_length:
            chunks.append(remaining.strip())
            break
        split_index = find_split_index(remaining, max_line_length)
        current = remaining[:split_index].strip()
        if not current:
            current = remaining[:max_line_length].strip()
            split_index = len(current)
        chunks.append(current)
        remaining = remaining[split_index:].lstrip()
    return [line for line in chunks if line]


def replace_text(text: str, replacements: list[tuple[str, str]]) -> str:
    updated = text
    for wrong, correct in replacements:
        updated = updated.replace(wrong, correct)
    return updated


def parse_timestamp_range(timestamp: str) -> tuple[float, float]:
    start_text, end_text = timestamp.split(" --> ")
    return parse_timestamp_value(start_text), parse_timestamp_value(end_text)


def parse_timestamp_value(value: str) -> float:
    hh, mm, rest = value.split(":")
    ss, ms = rest.split(",")
    return int(hh) * 3600 + int(mm) * 60 + int(ss) + int(ms) / 1000


def format_timestamp_value(seconds: float) -> str:
    millis = max(0, int(round(seconds * 1000)))
    hours, rem = divmod(millis, 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    secs, ms = divmod(rem, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{ms:03d}"


def split_text_with_timestamps(text: str, start: float, end: float, max_line_length: int) -> list[tuple[float, float, str]]:
    chunks = split_subtitle_text(text, max_line_length)
    if len(chunks) == 1:
        return [(start, end, chunks[0])]

    duration = max(0.0, end - start)
    weights = [max(1, visible_length(chunk)) for chunk in chunks]
    total_weight = sum(weights)
    results: list[tuple[float, float, str]] = []
    cursor = start
    for index, (chunk, weight) in enumerate(zip(chunks, weights), start=1):
        if index == len(chunks):
            chunk_end = end
        else:
            chunk_end = min(end, cursor + duration * (weight / total_weight))
        if chunk_end <= cursor:
            chunk_end = cursor + 0.001
        results.append((cursor, chunk_end, chunk))
        cursor = chunk_end
    return results


def replace_text_lines(content: str, replacements: list[tuple[str, str]], max_line_length: int) -> str:
    output_blocks: list[str] = []
    next_cue_id = 1
    for raw_block in content.strip().split("\n\n"):
        lines = raw_block.splitlines()
        if len(lines) < 2:
            continue
        timestamp = lines[1].strip()
        text = " ".join(line.strip() for line in lines[2:] if line.strip())
        updated_text = replace_text(text, replacements)
        start, end = parse_timestamp_range(timestamp)
        split_chunks = split_text_with_timestamps(updated_text, start, end, max_line_length)
        for chunk_start, chunk_end, chunk_text in split_chunks:
            output_blocks.append(
                "\n".join(
                    [
                        str(next_cue_id),
                        f"{format_timestamp_value(chunk_start)} --> {format_timestamp_value(chunk_end)}",
                        chunk_text,
                    ]
                )
            )
            next_cue_id += 1
    return "\n\n".join(output_blocks).strip() + "\n"


def main() -> int:
    args = parse_args()
    input_path = Path(args.input).expanduser().resolve()
    terms_path = Path(args.terms_file).expanduser().resolve()
    output_path = (
        Path(args.output).expanduser().resolve()
        if args.output
        else input_path.with_name(f"{input_path.stem}.corrected.srt")
    )

    replacements = read_terms(terms_path)
    content = input_path.read_text(encoding="utf-8")
    updated = replace_text_lines(content, replacements, args.max_line_length)
    output_path.write_text(updated, encoding="utf-8")
    print(f"[done] 已写出术语替换版 SRT: {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
