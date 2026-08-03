#!/usr/bin/env python3
"""Create one editable per-video project without creating approval state."""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

from common import load_json, write_json


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--text-file", type=Path)
    args = parser.parse_args()

    skill_dir = Path(__file__).resolve().parent.parent
    assets_dir = skill_dir / "assets"
    output = args.output_dir.resolve()
    if output.exists() and any(output.iterdir()):
        raise SystemExit(f"Project directory is not empty: {output}")

    output.mkdir(parents=True, exist_ok=True)
    (output / "images").mkdir(exist_ok=True)
    config = load_json(assets_dir / "default_config.json")
    config["asset_root"] = str(assets_dir)
    write_json(output / "config.json", config)

    if args.text_file:
        if not args.text_file.exists():
            raise SystemExit(f"Text file does not exist: {args.text_file}")
        shutil.copy2(args.text_file, output / "input.txt")
    else:
        (output / "input.txt").touch(exist_ok=True)
    (output / "normalized_script.txt").touch(exist_ok=True)
    (output / "subtitle_lines.txt").touch(exist_ok=True)

    print(f"Created project at {output}")
    print("Approval state was not created; rendering remains locked.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
