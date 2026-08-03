#!/usr/bin/env python3
"""Restore internal source punctuation while removing cue-ending marks."""

from __future__ import annotations

import argparse
from pathlib import Path

from common import restore_internal_subtitle_punctuation


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("source_text", type=Path)
    parser.add_argument("subtitle_lines", type=Path)
    args = parser.parse_args()

    original_lines = args.subtitle_lines.read_text(
        encoding="utf-8-sig"
    ).splitlines()
    non_empty_lines = [line.strip() for line in original_lines if line.strip()]
    restored = iter(
        restore_internal_subtitle_punctuation(
            args.source_text.read_text(encoding="utf-8-sig"),
            non_empty_lines,
        )
    )
    output_lines = [
        next(restored) if line.strip() else ""
        for line in original_lines
    ]
    args.subtitle_lines.write_text(
        "\n".join(output_lines).rstrip() + "\n",
        encoding="utf-8",
    )
    print(
        f"Synchronized punctuation for {len(non_empty_lines)} subtitle lines"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
