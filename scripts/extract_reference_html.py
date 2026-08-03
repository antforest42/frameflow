#!/usr/bin/env python3
"""Extract embedded reference images and semantic labels from the source HTML."""

from __future__ import annotations

import argparse
import base64
import hashlib
import re
from html.parser import HTMLParser
from pathlib import Path

from common import write_json


class CardParser(HTMLParser):
    VOID_TAGS = {"img", "br", "hr", "meta", "link", "input", "source", "wbr"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.cards: list[dict] = []
        self._card: dict | None = None
        self._depth = 0
        self._text: list[str] = []

    @staticmethod
    def _classes(attrs: list[tuple[str, str | None]]) -> set[str]:
        raw = dict(attrs).get("class") or ""
        return set(raw.split())

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        classes = self._classes(attrs)
        if tag == "article" and "card" in classes:
            self._card = {"texts": [], "alt": "", "src": ""}
            self._depth = 1
            self._text = []
            return
        if self._card is None:
            return
        if tag not in self.VOID_TAGS:
            self._depth += 1
        if tag == "img":
            values = dict(attrs)
            self._card["alt"] = values.get("alt") or ""
            self._card["src"] = values.get("src") or ""

    def handle_endtag(self, tag: str) -> None:
        if self._card is None:
            return
        if tag in self.VOID_TAGS:
            return
        self._depth -= 1
        if tag == "article" and self._depth == 0:
            clean = [" ".join(text.split()) for text in self._text]
            self._card["texts"] = [text for text in clean if text]
            self.cards.append(self._card)
            self._card = None
            self._text = []

    def handle_data(self, data: str) -> None:
        if self._card is not None and data.strip():
            self._text.append(data)


def extension_for(mime: str) -> str:
    return {"jpeg": "jpg", "jpg": "jpg", "png": "png", "webp": "webp"}.get(
        mime.lower(), mime.lower()
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("html", type=Path)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--prefix", default="original")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    if args.output_dir.exists() and any(args.output_dir.iterdir()) and not args.force:
        raise SystemExit(
            f"{args.output_dir} is not empty; pass --force to replace matching outputs"
        )
    args.output_dir.mkdir(parents=True, exist_ok=True)

    doc = CardParser()
    doc.feed(args.html.read_text(encoding="utf-8"))
    catalog: list[dict] = []
    data_re = re.compile(r"^data:image/([^;]+);base64,(.+)$", re.S)

    for number, card in enumerate(doc.cards, 1):
        match = data_re.match(card["src"])
        if not match:
            continue
        mime, payload = match.groups()
        raw = base64.b64decode(payload)
        ext = extension_for(mime)
        item_id = f"{args.prefix}-{number:02d}"
        filename = f"{item_id}.{ext}"
        (args.output_dir / filename).write_bytes(raw)
        texts = card["texts"]
        title = card["alt"] or (texts[1] if len(texts) > 1 else item_id)
        catalog.append(
            {
                "id": item_id,
                "file": filename,
                "title": title,
                "context": texts,
                "sha256": hashlib.sha256(raw).hexdigest(),
            }
        )

    write_json(
        args.output_dir / "catalog.json",
        {
            "schema_version": 1,
            "source": args.html.name,
            "count": len(catalog),
            "items": catalog,
        },
    )
    print(f"Extracted {len(catalog)} images to {args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
