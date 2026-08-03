#!/usr/bin/env python3
"""Publish a clean project root containing only final media and work files."""

from __future__ import annotations

import argparse
import os
import re
import shutil
from pathlib import Path

from common import load_json


INVALID_FILENAME_CHARACTERS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def safe_filename(value: str) -> str:
    cleaned = INVALID_FILENAME_CHARACTERS.sub("＿", value).strip().rstrip(". ")
    return cleaned or "未命名视频"


def resolve_media(work_dir: Path, value: Path) -> Path:
    return value if value.is_absolute() else work_dir / value


def atomic_copy(source: Path, destination: Path) -> None:
    temporary = destination.with_name(destination.name + ".publishing")
    shutil.copy2(source, temporary)
    os.replace(temporary, destination)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "work_dir",
        type=Path,
        help="Internal working directory, normally <project>/项目文件",
    )
    parser.add_argument(
        "output_dir",
        type=Path,
        help="Clean outer project directory",
    )
    parser.add_argument(
        "--video",
        type=Path,
        default=Path("final_with_voice.mp4"),
    )
    parser.add_argument(
        "--cover",
        type=Path,
        default=Path("cover.png"),
        help="Landscape 4:3 cover",
    )
    parser.add_argument(
        "--portrait-cover",
        type=Path,
        default=Path("cover_portrait.png"),
        help="Portrait 3:4 cover",
    )
    parser.add_argument("--work-folder-name", default="项目文件")
    args = parser.parse_args()

    work_dir = args.work_dir.resolve()
    output_dir = args.output_dir.resolve()
    expected_work_dir = (output_dir / args.work_folder_name).resolve()
    if work_dir != expected_work_dir:
        raise SystemExit(
            "work_dir must be the output directory's 项目文件 folder"
        )

    plan_path = work_dir / "plan.json"
    if not plan_path.exists():
        raise SystemExit(f"Missing project plan: {plan_path}")
    plan = load_json(plan_path)
    title = safe_filename(str(plan.get("title", "")))

    video_source = resolve_media(work_dir, args.video)
    cover_source = resolve_media(work_dir, args.cover)
    portrait_cover_source = resolve_media(work_dir, args.portrait_cover)
    for label, path in (
        ("video", video_source),
        ("landscape cover", cover_source),
        ("portrait cover", portrait_cover_source),
    ):
        if not path.exists() or not path.is_file():
            raise SystemExit(f"Missing {label}: {path}")

    output_dir.mkdir(parents=True, exist_ok=True)
    video_output = output_dir / f"{title}.mp4"
    cover_output = output_dir / f"（封面）{title}.png"
    portrait_cover_output = output_dir / f"（封面-竖版）{title}.png"
    allowed_names = {
        args.work_folder_name,
        video_output.name,
        cover_output.name,
        portrait_cover_output.name,
        "desktop.ini",
    }
    unexpected = [
        path.name
        for path in output_dir.iterdir()
        if path.name not in allowed_names
    ]
    if unexpected:
        raise SystemExit(
            "Clean output directory contains unexpected items: "
            + ", ".join(sorted(unexpected))
        )

    atomic_copy(video_source, video_output)
    atomic_copy(cover_source, cover_output)
    atomic_copy(portrait_cover_source, portrait_cover_output)
    print(f"Published video: {video_output}")
    print(f"Published landscape cover: {cover_output}")
    print(f"Published portrait cover: {portrait_cover_output}")
    print(f"Project files: {work_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
