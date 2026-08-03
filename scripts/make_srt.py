#!/usr/bin/env python3
"""Create a temporary display SRT from validated semantic units."""

from __future__ import annotations

import argparse
from pathlib import Path

from common import (
    count_effective_hanzi,
    frame_to_timestamp,
    normalize_spoken_text,
    risky_cue_ending,
    strip_subtitle_punctuation,
    write_json,
)


def parse_gap_pattern(value: str) -> list[int]:
    try:
        pattern = [int(part.strip()) for part in value.split(",")]
    except ValueError as error:
        raise argparse.ArgumentTypeError(
            "gap pattern must be comma-separated non-negative frame counts"
        ) from error
    if not pattern or any(frame < 0 for frame in pattern):
        raise argparse.ArgumentTypeError(
            "gap pattern must be comma-separated non-negative frame counts"
        )
    return pattern


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "lines",
        type=Path,
        help="UTF-8 text, one reviewed semantic/prosodic unit per line",
    )
    parser.add_argument("output", type=Path)
    parser.add_argument("--timeline-json", type=Path)
    parser.add_argument(
        "--source-text",
        type=Path,
        help="Normalized full spoken text used to verify no words were lost or added",
    )
    parser.add_argument("--fps", type=int, default=30)
    parser.add_argument("--lead-in-frames", type=int, default=118)
    parser.add_argument("--base-cue-frames", type=int, default=4)
    parser.add_argument("--frames-per-hanzi", type=int, default=5)
    parser.add_argument("--minimum-frames", type=int, default=25)
    parser.add_argument(
        "--gap-pattern-frames",
        type=parse_gap_pattern,
        default=parse_gap_pattern("0,1"),
        help="Repeating gap after each cue; approved default is 0,1",
    )
    parser.add_argument("--minimum-hanzi", type=int, default=3)
    parser.add_argument("--maximum-hanzi", type=int, default=14)
    parser.add_argument(
        "--allow-length-outliers",
        action="store_true",
        help="Allow reviewed units outside the approved 3-14 character range",
    )
    args = parser.parse_args()

    lines = []
    for source_line in args.lines.read_text(encoding="utf-8-sig").splitlines():
        text = strip_subtitle_punctuation(source_line.strip())
        if text:
            lines.append(text)
    if not lines:
        raise SystemExit("No subtitle lines found")

    if args.fps <= 0:
        raise SystemExit("fps must be positive")
    if min(
        args.lead_in_frames,
        args.base_cue_frames,
        args.frames_per_hanzi,
        args.minimum_frames,
    ) < 0:
        raise SystemExit("frame timing values must be non-negative")
    if args.minimum_hanzi <= 0 or args.maximum_hanzi < args.minimum_hanzi:
        raise SystemExit("invalid subtitle character range")

    counts = [max(1, count_effective_hanzi(text)) for text in lines]
    outliers = [
        f"{index}:{text}({count})"
        for index, (text, count) in enumerate(zip(lines, counts), 1)
        if not args.minimum_hanzi <= count <= args.maximum_hanzi
    ]
    if outliers and not args.allow_length_outliers:
        raise SystemExit(
            "Subtitle units outside the approved character range: "
            + ", ".join(outliers)
        )
    risky_boundaries = [
        f"{index}:{text}(ends with {ending})"
        for index, text in enumerate(lines[:-1], 1)
        if (ending := risky_cue_ending(text))
    ]
    if risky_boundaries:
        raise SystemExit(
            "Likely broken semantic boundaries: "
            + ", ".join(risky_boundaries)
        )

    if args.source_text:
        source = normalize_spoken_text(
            args.source_text.read_text(encoding="utf-8-sig")
        )
        subtitle_text = normalize_spoken_text("".join(lines))
        if source != subtitle_text:
            mismatch = next(
                (
                    index
                    for index, (left, right) in enumerate(
                        zip(source, subtitle_text)
                    )
                    if left != right
                ),
                min(len(source), len(subtitle_text)),
            )
            raise SystemExit(
                "Subtitle text does not exactly cover normalized source text "
                f"(first mismatch at character {mismatch + 1}; "
                f"source={len(source)}, subtitles={len(subtitle_text)})"
            )

    cursor_frame = args.lead_in_frames
    blocks: list[str] = []
    timeline: list[dict] = []

    for index, (text, count) in enumerate(zip(lines, counts), 1):
        duration_frames = max(
            args.minimum_frames,
            args.base_cue_frames + count * args.frames_per_hanzi,
        )
        start_frame = cursor_frame
        end_frame = start_frame + duration_frames
        gap_after_frames = (
            args.gap_pattern_frames[(index - 1) % len(args.gap_pattern_frames)]
            if index < len(lines)
            else 0
        )
        blocks.append(
            f"{index}\n{frame_to_timestamp(start_frame, args.fps)} --> "
            f"{frame_to_timestamp(end_frame, args.fps)}\n{text}"
        )
        timeline.append(
            {
                "index": index,
                "start_frame": start_frame,
                "end_frame": end_frame,
                "duration_frames": duration_frames,
                "gap_after_frames": gap_after_frames,
                "effective_hanzi": count,
                "text": text,
            }
        )
        cursor_frame = end_frame + gap_after_frames

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("\n\n".join(blocks) + "\n", encoding="utf-8")
    if args.timeline_json:
        write_json(
            args.timeline_json,
            {
                "schema_version": 2,
                "subtitle_profile": "privacy_reference_v1",
                "fps": args.fps,
                "lead_in_frames": args.lead_in_frames,
                "base_cue_frames": args.base_cue_frames,
                "frames_per_effective_hanzi": args.frames_per_hanzi,
                "minimum_cue_frames": args.minimum_frames,
                "inter_cue_gap_pattern_frames": args.gap_pattern_frames,
                "cues": timeline,
            },
        )
    print(f"Wrote {len(lines)} cues to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
