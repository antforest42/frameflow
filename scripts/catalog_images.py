#!/usr/bin/env python3
"""Create a lightweight semantic catalog from numbered image filenames."""

from __future__ import annotations

import argparse
import hashlib
import re
from pathlib import Path

from common import write_json


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("image_dir", type=Path)
    parser.add_argument("--prefix", default="approved")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    files = sorted(
        path
        for path in args.image_dir.iterdir()
        if path.is_file() and path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}
    )
    items = []
    for index, path in enumerate(files, 1):
        title = re.sub(r"^[0-9]+[a-zA-Z]?[_-]?", "", path.stem)
        items.append(
            {
                "id": f"{args.prefix}-{index:02d}",
                "file": path.name,
                "title": title,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
        )
    output = args.output or args.image_dir / "catalog.json"
    write_json(
        output,
        {
            "schema_version": 1,
            "count": len(items),
            "items": items,
        },
    )
    print(f"Cataloged {len(items)} images in {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

