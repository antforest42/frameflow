#!/usr/bin/env python3
"""Create one full-text, punctuated SRT cue for continuous TTS."""

from __future__ import annotations

import argparse
import re
from pathlib import Path

from common import frame_to_timestamp, normalize_spoken_text


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("source_text", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--fps", type=int, default=30)
    parser.add_argument("--lead-in-frames", type=int, default=118)
    parser.add_argument(
        "--placeholder-end-seconds",
        type=int,
        default=300,
        help="Generous container end; actual narration audio sets final timing",
    )
    args = parser.parse_args()

    if args.fps <= 0:
        raise SystemExit("fps must be positive")
    if args.lead_in_frames < 0:
        raise SystemExit("lead-in frames must be non-negative")
    if args.placeholder_end_seconds * args.fps <= args.lead_in_frames:
        raise SystemExit("placeholder end must be after the narration start")

    source = args.source_text.read_text(encoding="utf-8-sig")
    paragraphs = [
        re.sub(r"\s+", "", paragraph)
        for paragraph in re.split(r"\r?\n\s*\r?\n", source)
        if paragraph.strip()
    ]
    narration = "".join(paragraphs)
    if not narration or not normalize_spoken_text(narration):
        raise SystemExit("Narration source is empty")

    start = frame_to_timestamp(args.lead_in_frames, args.fps)
    end = frame_to_timestamp(
        args.placeholder_end_seconds * args.fps,
        args.fps,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        f"1\n{start} --> {end}\n{narration}\n",
        encoding="utf-8",
    )
    print(
        f"Wrote one continuous narration cue with "
        f"{len(normalize_spoken_text(narration))} spoken characters"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
