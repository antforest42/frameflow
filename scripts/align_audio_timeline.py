#!/usr/bin/env python3
"""Align display subtitle boundaries to punctuation pauses in narration audio."""

from __future__ import annotations

import argparse
import json
import math
import re
import subprocess
import unicodedata
from bisect import bisect_left
from pathlib import Path

PAUSE_MARKS = "，、：；。！？,;:!?"


def frame_to_timestamp(frame: int, fps: int) -> str:
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


def is_spoken(character: str) -> bool:
    return (
        not character.isspace()
        and not unicodedata.category(character).startswith("P")
    )


def normalize_spoken(text: str) -> str:
    return "".join(character for character in text if is_spoken(character))


def parse_subtitle_texts(path: Path) -> list[tuple[int, str]]:
    pattern = re.compile(
        r"(?P<h1>\d{2}):(?P<m1>\d{2}):(?P<s1>\d{2}),(?P<ms1>\d{3})"
        r"\s+-->\s+"
        r"(?P<h2>\d{2}):(?P<m2>\d{2}):(?P<s2>\d{2}),(?P<ms2>\d{3})"
    )
    cues: list[tuple[int, str]] = []
    source = path.read_text(encoding="utf-8-sig").strip()
    for block in re.split(r"\r?\n\s*\r?\n", source):
        lines = block.splitlines()
        if len(lines) >= 3 and pattern.fullmatch(lines[1].strip()):
            cues.append((int(lines[0].strip()), "\n".join(lines[2:])))
    return cues


def audio_duration(ffprobe: str, audio: Path) -> float:
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


def detect_silences(
    ffmpeg: str,
    audio: Path,
    threshold_db: float,
    minimum_seconds: float,
) -> list[tuple[float, float]]:
    result = subprocess.run(
        [
            ffmpeg,
            "-hide_banner",
            "-nostats",
            "-i",
            str(audio),
            "-af",
            (
                f"silencedetect=noise={threshold_db:g}dB:"
                f"d={minimum_seconds:g}"
            ),
            "-f",
            "null",
            "NUL",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    starts = [
        float(value)
        for value in re.findall(r"silence_start: ([0-9.]+)", result.stderr)
    ]
    ends = [
        float(value)
        for value in re.findall(r"silence_end: ([0-9.]+)", result.stderr)
    ]
    return list(zip(starts, ends))


def choose_pause_anchors(
    punctuation_positions: list[int],
    silences: list[tuple[float, float]],
    lead_in_seconds: float,
    duration: float,
    total_spoken: int,
) -> list[tuple[int, float]]:
    """Select an ordered punctuation subset closest to detected pause progress."""
    if not punctuation_positions or not silences:
        return []
    if len(silences) > len(punctuation_positions):
        silences = sorted(
            silences,
            key=lambda pair: pair[1] - pair[0],
            reverse=True,
        )[: len(punctuation_positions)]
        silences.sort()

    punctuation_progress = [
        position / total_spoken for position in punctuation_positions
    ]
    silence_progress = [
        (((start + end) / 2) - lead_in_seconds)
        / max(0.001, duration - lead_in_seconds)
        for start, end in silences
    ]
    punct_count = len(punctuation_positions)
    silence_count = len(silences)
    infinity = float("inf")
    costs = [
        [infinity] * (silence_count + 1)
        for _ in range(punct_count + 1)
    ]
    previous: list[list[tuple[int, int] | None]] = [
        [None] * (silence_count + 1)
        for _ in range(punct_count + 1)
    ]
    costs[0][0] = 0.0
    for punct_index in range(punct_count):
        for silence_index in range(silence_count + 1):
            current = costs[punct_index][silence_index]
            if not math.isfinite(current):
                continue
            if punct_count - punct_index > silence_count - silence_index:
                if current < costs[punct_index + 1][silence_index]:
                    costs[punct_index + 1][silence_index] = current
                    previous[punct_index + 1][silence_index] = (
                        punct_index,
                        silence_index,
                    )
            if silence_index < silence_count:
                candidate = current + (
                    punctuation_progress[punct_index]
                    - silence_progress[silence_index]
                ) ** 2
                if candidate < costs[punct_index + 1][silence_index + 1]:
                    costs[punct_index + 1][silence_index + 1] = candidate
                    previous[punct_index + 1][silence_index + 1] = (
                        punct_index,
                        silence_index,
                    )

    punct_index = punct_count
    silence_index = silence_count
    matches: list[tuple[int, float]] = []
    while punct_index or silence_index:
        prior = previous[punct_index][silence_index]
        if prior is None:
            return []
        prior_punct, prior_silence = prior
        if silence_index == prior_silence + 1:
            matches.append(
                (
                    punctuation_positions[prior_punct],
                    silences[prior_silence][1],
                )
            )
        punct_index, silence_index = prior
    matches.reverse()
    return matches


def interpolate_time(
    spoken_position: int,
    anchors: list[tuple[int, float]],
) -> float:
    positions = [position for position, _ in anchors]
    right = bisect_left(positions, spoken_position)
    if right < len(anchors) and anchors[right][0] == spoken_position:
        return anchors[right][1]
    if right == 0 or right == len(anchors):
        raise ValueError("字幕位置超出音频对齐锚点")
    left_position, left_time = anchors[right - 1]
    right_position, right_time = anchors[right]
    progress = (
        (spoken_position - left_position)
        / (right_position - left_position)
    )
    return left_time + progress * (right_time - left_time)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--input-srt", type=Path, required=True)
    parser.add_argument("--output-srt", type=Path, required=True)
    parser.add_argument("--timeline", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--audio", type=Path, required=True)
    parser.add_argument("--plan", type=Path)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--fps", type=int, default=30)
    parser.add_argument("--lead-in-frames", type=int, default=118)
    parser.add_argument("--silence-threshold-db", type=float, default=-45)
    parser.add_argument("--minimum-silence-seconds", type=float, default=0.20)
    parser.add_argument("--ffmpeg", default="ffmpeg")
    parser.add_argument("--ffprobe", default="ffprobe")
    args = parser.parse_args()

    source = args.source.read_text(encoding="utf-8-sig")
    cues = parse_subtitle_texts(args.input_srt)
    if not cues:
        raise SystemExit("没有可对齐的显示字幕")
    source_spoken = normalize_spoken(source)
    cue_spoken = "".join(normalize_spoken(text) for _, text in cues)
    if source_spoken != cue_spoken:
        raise SystemExit("显示字幕没有完整覆盖朗读正文")

    total_spoken = len(source_spoken)
    spoken_position = 0
    punctuation_positions: list[int] = []
    for character in source:
        if is_spoken(character):
            spoken_position += 1
        elif character in PAUSE_MARKS:
            punctuation_positions.append(spoken_position)

    lead_in_seconds = args.lead_in_frames / args.fps
    duration = audio_duration(args.ffprobe, args.audio)
    detected = detect_silences(
        args.ffmpeg,
        args.audio,
        args.silence_threshold_db,
        args.minimum_silence_seconds,
    )
    usable_silences = [
        (start, end)
        for start, end in detected
        if start >= lead_in_seconds - 0.05
        and end < duration - 0.05
    ]
    pause_anchors = choose_pause_anchors(
        punctuation_positions,
        usable_silences,
        lead_in_seconds,
        duration,
        total_spoken,
    )
    anchors = [(0, lead_in_seconds), *pause_anchors, (total_spoken, duration)]
    anchors = sorted(dict(anchors).items())

    cumulative_positions: list[int] = []
    cursor = 0
    for _, text in cues:
        cursor += len(normalize_spoken(text))
        cumulative_positions.append(cursor)

    start_frames = [args.lead_in_frames]
    end_frames: list[int] = []
    prior = args.lead_in_frames
    for cue_index, position in enumerate(cumulative_positions):
        mapped = interpolate_time(position, anchors)
        frame = (
            math.ceil(mapped * args.fps - 1e-9)
            if cue_index == len(cues) - 1
            else round(mapped * args.fps)
        )
        frame = max(prior + 1, frame)
        end_frames.append(frame)
        prior = frame
        if cue_index < len(cues) - 1:
            start_frames.append(frame)

    blocks: list[str] = []
    timeline_cues: list[dict] = []
    for (index, text), start_frame, end_frame in zip(
        cues, start_frames, end_frames
    ):
        blocks.append(
            f"{index}\n"
            f"{frame_to_timestamp(start_frame, args.fps)} --> "
            f"{frame_to_timestamp(end_frame, args.fps)}\n"
            f"{text}"
        )
        timeline_cues.append(
            {
                "index": index,
                "start_frame": start_frame,
                "end_frame": end_frame,
                "duration_frames": end_frame - start_frame,
                "gap_after_frames": 0,
                "effective_hanzi": len(normalize_spoken(text)),
                "text": text,
            }
        )

    args.output_srt.parent.mkdir(parents=True, exist_ok=True)
    args.timeline.parent.mkdir(parents=True, exist_ok=True)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.output_srt.write_text(
        "\n\n".join(blocks) + "\n",
        encoding="utf-8",
    )
    write_json(
        args.timeline,
        {
            "schema_version": 3,
            "subtitle_profile": "audio_aligned_v1",
            "fps": args.fps,
            "lead_in_frames": args.lead_in_frames,
            "audio_path": str(args.audio),
            "audio_duration_seconds": duration,
            "alignment_method": "punctuation_pause_anchors",
            "cues": timeline_cues,
        },
    )
    report = {
        "audio_duration_seconds": duration,
        "spoken_characters": total_spoken,
        "punctuation_candidates": len(punctuation_positions),
        "detected_pauses": len(usable_silences),
        "matched_pause_anchors": len(pause_anchors),
        "first_cue_start_frame": start_frames[0],
        "last_cue_end_frame": end_frames[-1],
        "minimum_cue_frames": min(
            end - start for start, end in zip(start_frames, end_frames)
        ),
        "maximum_cue_frames": max(
            end - start for start, end in zip(start_frames, end_frames)
        ),
    }
    write_json(args.report, report)
    if args.plan:
        plan = json.loads(args.plan.read_text(encoding="utf-8"))
        try:
            audio_path = str(
                args.audio.resolve().relative_to(args.plan.resolve().parent)
            )
        except ValueError:
            audio_path = str(args.audio.resolve())
        try:
            srt_path = str(
                args.output_srt.resolve().relative_to(
                    args.plan.resolve().parent
                )
            )
        except ValueError:
            srt_path = str(args.output_srt.resolve())
        plan["subtitle_profile"] = "audio_aligned_v1"
        plan["srt_path"] = srt_path
        plan["narration_audio_path"] = audio_path
        plan["production_status"] = "audio_aligned"
        write_json(args.plan, plan)
    if args.config:
        config = json.loads(args.config.read_text(encoding="utf-8"))
        config.setdefault("timing", {})[
            "subtitle_profile"
        ] = "audio_aligned_v1"
        write_json(args.config, config)
    print(
        f"已按朗读音频对齐{len(cues)}条显示字幕，"
        f"使用{len(pause_anchors)}个停顿锚点，时长{duration:.3f}秒"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
