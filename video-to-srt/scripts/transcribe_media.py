#!/usr/bin/env python3
from __future__ import annotations

import argparse
import contextlib
import importlib
import importlib.util
import json
import math
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Iterable


SUPPORTED_EXTENSIONS = {
    ".mp4",
    ".mov",
    ".mkv",
    ".avi",
    ".mp3",
    ".m4a",
    ".wav",
    ".flac",
    ".aac",
    ".webm",
}

SOFT_BREAK_CHARS = set("，。！？；：、,!?;: ")


class UserVisibleError(RuntimeError):
    pass


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Turn a local media file into a timestamp-aligned SRT subtitle file.",
    )
    parser.add_argument("input", help="Path to a local video or audio file")
    parser.add_argument(
        "-o",
        "--output",
        help="Output SRT path. Defaults to <input_stem>.srt next to the input file.",
    )
    parser.add_argument(
        "--language",
        default="zh",
        help="Language code for transcription. Defaults to zh.",
    )
    parser.add_argument(
        "--backend",
        default="auto",
        choices=["auto", "whisper-cli", "faster-whisper", "openai-whisper", "mlx-whisper"],
        help="Force a specific transcription backend.",
    )
    parser.add_argument(
        "--model",
        default="medium",
        help="ASR model name. Defaults to medium.",
    )
    parser.add_argument(
        "--compute-type",
        default="int8",
        help="Compute type for faster-whisper. Defaults to int8.",
    )
    parser.add_argument(
        "--device",
        default="auto",
        help="Device hint for supported backends. Defaults to auto.",
    )
    parser.add_argument(
        "--prompt",
        default="",
        help="Optional vocabulary/context prompt to improve recognition.",
    )
    parser.add_argument(
        "--terms-file",
        help="Optional TSV glossary file. The second column is appended to the prompt.",
    )
    parser.add_argument(
        "--keep-audio",
        action="store_true",
        help="Keep the extracted WAV file for debugging.",
    )
    parser.add_argument(
        "--max-line-length",
        type=int,
        default=18,
        help="Maximum characters per subtitle line. Defaults to 18.",
    )
    return parser.parse_args()


def ensure_media_path(path_str: str) -> Path:
    path = Path(path_str).expanduser().resolve()
    if not path.exists():
        raise UserVisibleError(f"输入文件不存在: {path}")
    if not path.is_file():
        raise UserVisibleError(f"输入路径不是文件: {path}")
    if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
        print(f"[warning] 文件扩展名 {path.suffix} 不在常见媒体列表中，仍会继续尝试。", file=sys.stderr)
    return path


def ensure_ffmpeg() -> str:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise UserVisibleError("未找到 ffmpeg，无法从视频中抽取音轨。")
    return ffmpeg


def ensure_ffprobe() -> str | None:
    return shutil.which("ffprobe")


def run_command(command: list[str], *, capture_output: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        check=False,
        text=True,
        capture_output=capture_output,
    )


def extract_audio(input_path: Path, wav_path: Path) -> None:
    ffmpeg = ensure_ffmpeg()
    command = [
        ffmpeg,
        "-y",
        "-i",
        str(input_path),
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-c:a",
        "pcm_s16le",
        str(wav_path),
    ]
    result = run_command(command)
    if result.returncode != 0:
        raise UserVisibleError(f"ffmpeg 抽取音轨失败:\n{result.stderr.strip()}")


def probe_duration_seconds(input_path: Path) -> float | None:
    ffprobe = ensure_ffprobe()
    if not ffprobe:
        return None
    command = [
        ffprobe,
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "json",
        str(input_path),
    ]
    result = run_command(command)
    if result.returncode != 0:
        return None
    with contextlib.suppress(json.JSONDecodeError, KeyError, TypeError, ValueError):
        payload = json.loads(result.stdout)
        return float(payload["format"]["duration"])
    return None


def format_srt_timestamp(seconds: float) -> str:
    safe = max(0.0, seconds)
    millis = int(round(safe * 1000))
    hours, rem = divmod(millis, 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    secs, ms = divmod(rem, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{ms:03d}"


def normalize_text(text: str) -> str:
    cleaned = " ".join(part.strip() for part in text.splitlines() if part.strip()).strip()
    return cleaned


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


def split_segment(segment: dict, max_line_length: int) -> list[dict]:
    chunks = split_subtitle_text(segment["text"], max_line_length)
    if len(chunks) == 1:
        return [{**segment, "text": chunks[0]}]

    start = float(segment["start"])
    end = float(segment["end"])
    duration = max(0.0, end - start)
    weights = [max(1, visible_length(chunk)) for chunk in chunks]
    total_weight = sum(weights)

    split_segments: list[dict] = []
    cursor = start
    for index, (chunk, weight) in enumerate(zip(chunks, weights), start=1):
        if index == len(chunks):
            chunk_end = end
        else:
            chunk_duration = duration * (weight / total_weight) if total_weight else 0.0
            chunk_end = min(end, cursor + chunk_duration)
        split_segments.append({"start": cursor, "end": chunk_end, "text": chunk})
        cursor = chunk_end
    return split_segments


def build_prompt(user_prompt: str, terms_file: str | None) -> str:
    parts: list[str] = []
    if user_prompt.strip():
        parts.append(user_prompt.strip())
    if terms_file:
        terms_path = Path(terms_file).expanduser().resolve()
        glossary_terms: list[str] = []
        for raw_line in terms_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            columns = line.split("\t")
            if len(columns) >= 2:
                glossary_terms.append(columns[1].strip())
        if glossary_terms:
            parts.append("可能出现的术语：" + "，".join(term for term in glossary_terms if term))
    return "。".join(part for part in parts if part)


def normalize_segments(raw_segments: Iterable[dict], duration: float | None) -> list[dict]:
    normalized: list[dict] = []
    for raw in raw_segments:
        text = normalize_text(str(raw.get("text", "")))
        if not text:
            continue
        start = max(0.0, float(raw.get("start", 0.0)))
        end = max(start, float(raw.get("end", start)))
        if duration is not None:
            start = min(start, duration)
            end = min(end, duration)
        if math.isclose(end, start):
            end = start + 0.5
            if duration is not None:
                end = min(end, duration)
        normalized.append({"start": start, "end": end, "text": text})
    for index in range(len(normalized) - 1):
        current = normalized[index]
        nxt = normalized[index + 1]
        if current["end"] > nxt["start"]:
            current["end"] = max(current["start"], nxt["start"])
            if math.isclose(current["end"], current["start"]):
                current["end"] = min(nxt["start"], current["start"] + 0.2)
    return normalized


def reshape_segments(segments: list[dict], max_line_length: int) -> list[dict]:
    reshaped: list[dict] = []
    for segment in segments:
        reshaped.extend(split_segment(segment, max_line_length))
    fixed: list[dict] = []
    for index, segment in enumerate(reshaped):
        start = float(segment["start"])
        end = float(segment["end"])
        if index < len(reshaped) - 1:
            next_start = float(reshaped[index + 1]["start"])
            end = min(end, next_start)
        if end <= start:
            end = start + 0.001
        fixed.append({"start": start, "end": end, "text": segment["text"]})
    return fixed


def write_srt(output_path: Path, segments: list[dict], max_line_length: int) -> None:
    one_line_segments = reshape_segments(segments, max_line_length)
    lines: list[str] = []
    for idx, segment in enumerate(one_line_segments, start=1):
        lines.append(str(idx))
        lines.append(
            f"{format_srt_timestamp(segment['start'])} --> {format_srt_timestamp(segment['end'])}"
        )
        lines.append(segment["text"])
        lines.append("")
    output_path.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")


def detect_available_backends() -> list[str]:
    backends: list[str] = []
    if shutil.which("whisper"):
        backends.append("whisper-cli")
    if importlib.util.find_spec("faster_whisper") is not None:
        backends.append("faster-whisper")
    if importlib.util.find_spec("whisper") is not None:
        backends.append("openai-whisper")
    if importlib.util.find_spec("mlx_whisper") is not None:
        backends.append("mlx-whisper")
    return backends


def transcribe_with_whisper_cli(
    audio_path: Path,
    language: str,
    model: str,
    forced_output: Path,
    prompt: str,
) -> list[dict]:
    whisper = shutil.which("whisper")
    if not whisper:
        raise UserVisibleError("当前机器没有安装 whisper 命令行。")
    with tempfile.TemporaryDirectory(prefix="video-to-srt-whisper-") as temp_dir:
        command = [
            whisper,
            str(audio_path),
            "--language",
            language,
            "--task",
            "transcribe",
            "--model",
            model,
            "--output_format",
            "srt",
            "--output_dir",
            temp_dir,
            "--verbose",
            "False",
        ]
        if prompt:
            command.extend(["--initial_prompt", prompt])
        result = run_command(command)
        if result.returncode != 0:
            raise UserVisibleError(f"whisper 命令行转写失败:\n{result.stderr.strip()}")
        generated = Path(temp_dir) / f"{audio_path.stem}.srt"
        if not generated.exists():
            raise UserVisibleError("whisper 命令行执行成功，但没有生成 SRT 文件。")
        forced_output.write_text(generated.read_text(encoding="utf-8"), encoding="utf-8")
    return []


def transcribe_with_faster_whisper(
    audio_path: Path,
    language: str,
    model: str,
    compute_type: str,
    device: str,
    prompt: str,
) -> list[dict]:
    from faster_whisper import WhisperModel  # type: ignore

    model_kwargs = {"device": device, "compute_type": compute_type}
    if device == "auto":
        model_kwargs["device"] = "cpu"
    whisper_model = WhisperModel(model, **model_kwargs)
    transcribe_kwargs = {
        "audio": str(audio_path),
        "language": language,
        "vad_filter": True,
    }
    if prompt:
        transcribe_kwargs["initial_prompt"] = prompt
    segments, _info = whisper_model.transcribe(**transcribe_kwargs)
    return [
        {"start": float(segment.start), "end": float(segment.end), "text": segment.text}
        for segment in segments
    ]


def transcribe_with_openai_whisper(
    audio_path: Path,
    language: str,
    model: str,
    prompt: str,
) -> list[dict]:
    whisper = importlib.import_module("whisper")
    whisper_model = whisper.load_model(model)
    transcribe_kwargs = {
        "language": language,
        "task": "transcribe",
    }
    if prompt:
        transcribe_kwargs["initial_prompt"] = prompt
    result = whisper_model.transcribe(str(audio_path), **transcribe_kwargs)
    segments = result.get("segments") or []
    return [
        {"start": float(segment["start"]), "end": float(segment["end"]), "text": segment["text"]}
        for segment in segments
    ]


def transcribe_with_mlx_whisper(
    audio_path: Path,
    language: str,
    model: str,
    prompt: str,
) -> list[dict]:
    mlx_whisper = importlib.import_module("mlx_whisper")
    transcribe_kwargs = {
        "path_or_hf_repo": model,
        "language": language,
    }
    if prompt:
        transcribe_kwargs["initial_prompt"] = prompt
    result = mlx_whisper.transcribe(str(audio_path), **transcribe_kwargs)
    segments = result.get("segments") or []
    return [
        {"start": float(segment["start"]), "end": float(segment["end"]), "text": segment["text"]}
        for segment in segments
    ]


def choose_backends(forced_backend: str) -> list[str]:
    if forced_backend != "auto":
        return [forced_backend]
    return detect_available_backends()


def build_default_output(input_path: Path) -> Path:
    return input_path.with_suffix(".srt")


def main() -> int:
    args = parse_args()
    input_path = ensure_media_path(args.input)
    output_path = Path(args.output).expanduser().resolve() if args.output else build_default_output(input_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    prompt = build_prompt(args.prompt, args.terms_file)

    available = detect_available_backends()
    selected_backends = choose_backends(args.backend)
    if not selected_backends:
        available_text = ", ".join(available) if available else "无"
        raise UserVisibleError(
            "没有检测到可用的语音转写后端。\n"
            f"当前可用后端: {available_text}\n"
            "请先安装 whisper、faster-whisper、openai-whisper 或 mlx_whisper 之一。"
        )

    duration = probe_duration_seconds(input_path)
    with tempfile.TemporaryDirectory(prefix="video-to-srt-audio-") as temp_dir:
        wav_path = Path(temp_dir) / f"{input_path.stem}.wav"
        extract_audio(input_path, wav_path)
        if args.keep_audio:
            preserved_wav = output_path.with_suffix(".wav")
            shutil.copy2(wav_path, preserved_wav)
            print(f"[info] 已保留中间音频: {preserved_wav}", file=sys.stderr)

        last_error: Exception | None = None
        for backend in selected_backends:
            try:
                print(f"[info] 尝试后端: {backend}", file=sys.stderr)
                if backend == "whisper-cli":
                    transcribe_with_whisper_cli(wav_path, args.language, args.model, output_path, prompt)
                    print(f"[done] 已生成 SRT: {output_path}")
                    return 0
                if backend == "faster-whisper":
                    raw_segments = transcribe_with_faster_whisper(
                        wav_path,
                        args.language,
                        args.model,
                        args.compute_type,
                        args.device,
                        prompt,
                    )
                elif backend == "openai-whisper":
                    raw_segments = transcribe_with_openai_whisper(
                        wav_path,
                        args.language,
                        args.model,
                        prompt,
                    )
                elif backend == "mlx-whisper":
                    raw_segments = transcribe_with_mlx_whisper(
                        wav_path,
                        args.language,
                        args.model,
                        prompt,
                    )
                else:
                    raise UserVisibleError(f"未知后端: {backend}")

                segments = normalize_segments(raw_segments, duration)
                if not segments:
                    raise UserVisibleError(f"{backend} 没有返回可写入字幕的有效分段。")
                write_srt(output_path, segments, args.max_line_length)
                print(f"[done] 已生成 SRT: {output_path}")
                return 0
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                print(f"[warning] 后端 {backend} 失败: {exc}", file=sys.stderr)
                continue

    if last_error is None:
        raise UserVisibleError("未完成转写，但也没有捕获到明确错误。")
    raise UserVisibleError(f"所有后端都失败了，最后一个错误是: {last_error}")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except UserVisibleError as exc:
        print(f"[error] {exc}", file=sys.stderr)
        raise SystemExit(1)
