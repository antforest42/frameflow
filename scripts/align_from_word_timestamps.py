#!/usr/bin/env python3
"""Align reviewed display cues from local ASR word timestamps."""

from __future__ import annotations

import argparse
import difflib
import json
import math
import re
import subprocess
import unicodedata
from pathlib import Path


TIME_RE = re.compile(
    r"(?P<h1>\d{2}):(?P<m1>\d{2}):(?P<s1>\d{2}),(?P<ms1>\d{3})"
    r"\s+-->\s+"
    r"(?P<h2>\d{2}):(?P<m2>\d{2}):(?P<s2>\d{2}),(?P<ms2>\d{3})"
)


def normalized_characters(text: str) -> list[str]:
    return [
        character
        for character in text
        if not character.isspace()
        and not unicodedata.category(character).startswith("P")
    ]


def parse_srt_texts(path: Path) -> list[tuple[int, str]]:
    cues: list[tuple[int, str]] = []
    for block in re.split(
        r"\r?\n\s*\r?\n",
        path.read_text(encoding="utf-8-sig").strip(),
    ):
        lines = block.splitlines()
        if len(lines) >= 3 and TIME_RE.fullmatch(lines[1].strip()):
            cues.append((int(lines[0].strip()), "\n".join(lines[2:])))
    return cues


def frame_timestamp(frame: int, fps: int) -> str:
    total_ms = max(0, frame * 1000 // fps)
    hours, remainder = divmod(total_ms, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, milliseconds = divmod(remainder, 1000)
    return (
        f"{hours:02d}:{minutes:02d}:{seconds:02d},"
        f"{milliseconds:03d}"
    )


def write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def probe_duration(ffprobe: str, audio: Path) -> float:
    result = subprocess.run(
        [
            ffprobe,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(audio),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return float(result.stdout.strip())


def expand_asr_characters(words: list[dict]) -> tuple[list[str], list[float], list[float]]:
    characters: list[str] = []
    starts: list[float] = []
    ends: list[float] = []
    for word in words:
        word_characters = normalized_characters(str(word.get("text", "")))
        if not word_characters:
            continue
        start = float(word["start"])
        end = max(start, float(word["end"]))
        step = (end - start) / len(word_characters)
        for index, character in enumerate(word_characters):
            characters.append(character)
            starts.append(start + step * index)
            ends.append(start + step * (index + 1))
    return characters, starts, ends


def build_source_intervals(
    source_chars: list[str],
    asr_chars: list[str],
    asr_starts: list[float],
    asr_ends: list[float],
    body_duration: float,
) -> tuple[list[float], list[float], dict]:
    source_count = len(source_chars)
    starts: list[float | None] = [None] * source_count
    ends: list[float | None] = [None] * source_count
    matcher = difflib.SequenceMatcher(
        None,
        source_chars,
        asr_chars,
        autojunk=False,
    )
    exact_matches = 0
    substituted = 0

    for tag, source_start, source_end, asr_start, asr_end in matcher.get_opcodes():
        source_length = source_end - source_start
        asr_length = asr_end - asr_start
        if tag == "equal":
            exact_matches += source_length
            for offset in range(source_length):
                starts[source_start + offset] = asr_starts[asr_start + offset]
                ends[source_start + offset] = asr_ends[asr_start + offset]
        elif tag == "replace" and source_length and asr_length:
            substituted += source_length
            block_start = asr_starts[asr_start]
            block_end = asr_ends[asr_end - 1]
            step = (block_end - block_start) / source_length
            for offset in range(source_length):
                starts[source_start + offset] = block_start + step * offset
                ends[source_start + offset] = block_start + step * (offset + 1)

    known = [index for index, value in enumerate(starts) if value is not None]
    if not known:
        raise ValueError("语音识别结果与原文没有可用匹配")

    first_known = known[0]
    if first_known:
        first_time = float(starts[first_known])
        step = first_time / max(1, first_known)
        for index in range(first_known):
            starts[index] = step * index
            ends[index] = step * (index + 1)

    for left, right in zip(known, known[1:]):
        if right == left + 1:
            continue
        gap_start = float(ends[left])
        gap_end = float(starts[right])
        count = right - left - 1
        step = max(0.001, (gap_end - gap_start) / count)
        for offset in range(1, count + 1):
            index = left + offset
            starts[index] = gap_start + step * (offset - 1)
            ends[index] = gap_start + step * offset

    last_known = known[-1]
    if last_known < source_count - 1:
        gap_start = float(ends[last_known])
        count = source_count - last_known - 1
        step = max(0.001, (body_duration - gap_start) / count)
        for offset in range(1, count + 1):
            index = last_known + offset
            starts[index] = gap_start + step * (offset - 1)
            ends[index] = gap_start + step * offset

    resolved_starts = [float(value) for value in starts]
    resolved_ends = [float(value) for value in ends]
    for index in range(source_count):
        if index:
            resolved_starts[index] = max(
                resolved_starts[index],
                resolved_starts[index - 1],
            )
        resolved_ends[index] = max(
            resolved_starts[index] + 0.001,
            resolved_ends[index],
        )
    return (
        resolved_starts,
        resolved_ends,
        {
            "source_characters": source_count,
            "asr_characters": len(asr_chars),
            "exact_matches": exact_matches,
            "substituted_source_characters": substituted,
            "exact_source_coverage": exact_matches / source_count,
            "sequence_similarity": matcher.ratio(),
        },
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--input-srt", type=Path, required=True)
    parser.add_argument("--word-timestamps", type=Path, required=True)
    parser.add_argument("--body-audio", type=Path, required=True)
    parser.add_argument("--final-audio", type=Path, required=True)
    parser.add_argument("--output-srt", type=Path, required=True)
    parser.add_argument("--timeline", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--plan", type=Path)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--fps", type=int, default=30)
    parser.add_argument("--lead-in-frames", type=int, default=118)
    parser.add_argument("--ffprobe", default="ffprobe")
    args = parser.parse_args()

    source_chars = normalized_characters(
        args.source.read_text(encoding="utf-8-sig")
    )
    cues = parse_srt_texts(args.input_srt)
    cue_chars = [
        normalized_characters(text)
        for _, text in cues
    ]
    if source_chars != [character for cue in cue_chars for character in cue]:
        raise SystemExit("显示字幕没有完整覆盖原文")

    timestamp_data = json.loads(
        args.word_timestamps.read_text(encoding="utf-8")
    )
    asr_chars, asr_starts, asr_ends = expand_asr_characters(
        timestamp_data.get("words", [])
    )
    body_duration = probe_duration(args.ffprobe, args.body_audio)
    final_duration = probe_duration(args.ffprobe, args.final_audio)
    source_starts, source_ends, quality = build_source_intervals(
        source_chars,
        asr_chars,
        asr_starts,
        asr_ends,
        body_duration,
    )
    if quality["sequence_similarity"] < 0.85:
        raise SystemExit(
            "识别文本与原文相似度不足，拒绝生成正式时间轴："
            f"{quality['sequence_similarity']:.3f}"
        )

    lead_seconds = args.lead_in_frames / args.fps
    cumulative_positions: list[int] = []
    cursor = 0
    for characters in cue_chars:
        cursor += len(characters)
        cumulative_positions.append(cursor)

    boundaries_seconds = [lead_seconds]
    for position in cumulative_positions[:-1]:
        boundaries_seconds.append(
            lead_seconds + source_starts[position]
        )
    boundaries_seconds.append(final_duration)

    boundary_frames = [args.lead_in_frames]
    for value in boundaries_seconds[1:-1]:
        boundary_frames.append(
            max(boundary_frames[-1] + 1, round(value * args.fps))
        )
    final_frame = max(
        boundary_frames[-1] + 1,
        math.ceil(final_duration * args.fps - 1e-9),
    )
    boundary_frames.append(final_frame)

    blocks: list[str] = []
    timeline_cues: list[dict] = []
    for position, ((index, text), characters) in enumerate(
        zip(cues, cue_chars)
    ):
        start_frame = boundary_frames[position]
        end_frame = boundary_frames[position + 1]
        blocks.append(
            f"{index}\n"
            f"{frame_timestamp(start_frame, args.fps)} --> "
            f"{frame_timestamp(end_frame, args.fps)}\n"
            f"{text}"
        )
        timeline_cues.append(
            {
                "index": index,
                "start_frame": start_frame,
                "end_frame": end_frame,
                "duration_frames": end_frame - start_frame,
                "gap_after_frames": 0,
                "effective_hanzi": len(characters),
                "text": text,
            }
        )

    args.output_srt.parent.mkdir(parents=True, exist_ok=True)
    args.output_srt.write_text(
        "\n\n".join(blocks) + "\n",
        encoding="utf-8",
    )
    write_json(
        args.timeline,
        {
            "schema_version": 4,
            "subtitle_profile": "audio_aligned_v2",
            "fps": args.fps,
            "lead_in_frames": args.lead_in_frames,
            "audio_path": str(args.final_audio),
            "audio_duration_seconds": final_duration,
            "alignment_method": "faster_whisper_word_timestamps",
            "cues": timeline_cues,
        },
    )
    quality.update(
        {
            "body_audio_duration_seconds": body_duration,
            "final_audio_duration_seconds": final_duration,
            "first_cue_start_frame": boundary_frames[0],
            "last_cue_end_frame": boundary_frames[-1],
            "minimum_cue_frames": min(
                cue["duration_frames"] for cue in timeline_cues
            ),
            "maximum_cue_frames": max(
                cue["duration_frames"] for cue in timeline_cues
            ),
        }
    )
    write_json(args.report, quality)
    if args.plan:
        plan = json.loads(args.plan.read_text(encoding="utf-8"))
        try:
            srt_path = str(
                args.output_srt.resolve().relative_to(
                    args.plan.resolve().parent
                )
            )
        except ValueError:
            srt_path = str(args.output_srt.resolve())
        try:
            narration_path = str(
                args.final_audio.resolve().relative_to(
                    args.plan.resolve().parent
                )
            )
        except ValueError:
            narration_path = str(args.final_audio.resolve())
        plan["subtitle_profile"] = "audio_aligned_v2"
        plan["srt_path"] = srt_path
        plan["narration_audio_path"] = narration_path
        plan["production_status"] = "audio_aligned"
        write_json(args.plan, plan)
    if args.config:
        config = json.loads(args.config.read_text(encoding="utf-8"))
        config.setdefault("timing", {})[
            "subtitle_profile"
        ] = "audio_aligned_v2"
        write_json(args.config, config)
    print(
        f"已用词级时间戳对齐{len(cues)}条字幕，"
        f"文本相似度{quality['sequence_similarity']:.1%}，"
        f"精确字符覆盖{quality['exact_source_coverage']:.1%}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
