#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate that an SRT file uses one text line per cue and stays within the line length limit.",
    )
    parser.add_argument("candidate", help="Path to the SRT file to validate")
    parser.add_argument(
        "--max-line-length",
        type=int,
        default=18,
        help="Maximum characters per subtitle line. Defaults to 18.",
    )
    return parser.parse_args()


def visible_length(text: str) -> int:
    return sum(1 for char in text if not char.isspace())


def parse_timestamp_value(value: str) -> float:
    hh, mm, rest = value.split(":")
    ss, ms = rest.split(",")
    return int(hh) * 3600 + int(mm) * 60 + int(ss) + int(ms) / 1000


def parse_blocks(path: Path) -> list[tuple[int, float, float, list[str]]]:
    content = path.read_text(encoding="utf-8").strip()
    if not content:
        return []
    blocks: list[tuple[int, float, float, list[str]]] = []
    for raw_block in content.split("\n\n"):
        lines = raw_block.splitlines()
        if len(lines) < 3:
            raise ValueError(f"SRT 块格式错误: {raw_block}")
        cue_id = int(lines[0].strip())
        start_text, end_text = lines[1].strip().split(" --> ")
        start = parse_timestamp_value(start_text)
        end = parse_timestamp_value(end_text)
        blocks.append((cue_id, start, end, [line.rstrip() for line in lines[2:]]))
    return blocks


def main() -> int:
    args = parse_args()
    blocks = parse_blocks(Path(args.candidate).expanduser().resolve())
    previous_end = -1.0
    expected_cue_id = 1

    for cue_id, start, end, text_lines in blocks:
        if cue_id != expected_cue_id:
            raise SystemExit(f"[error] 字幕序号不连续: 期望 {expected_cue_id}，实际 {cue_id}")
        expected_cue_id += 1

        if start < previous_end:
            raise SystemExit(f"[error] 时间戳倒退: cue {cue_id}")
        if end <= start:
            raise SystemExit(f"[error] 时间戳无效: cue {cue_id}")
        previous_end = end

        if len(text_lines) != 1:
            raise SystemExit(f"[error] cue {cue_id} 不是单行字幕。")
        if visible_length(text_lines[0]) > args.max_line_length:
            raise SystemExit(
                f"[error] cue {cue_id} 超过 {args.max_line_length} 字: {text_lines[0]}"
            )

    print(
        f"[done] 校验通过: 共 {len(blocks)} 个字幕块，单行字幕，且每行不超过 {args.max_line_length} 字。"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
