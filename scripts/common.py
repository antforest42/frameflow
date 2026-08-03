#!/usr/bin/env python3
"""Shared helpers for the life-evolution video workflow."""

from __future__ import annotations

import json
import math
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from PIL import Image


@dataclass(frozen=True)
class Cue:
    index: int
    start: float
    end: float
    text: str


TIME_RE = re.compile(
    r"(?P<h1>\d{2}):(?P<m1>\d{2}):(?P<s1>\d{2}),(?P<ms1>\d{3})"
    r"\s+-->\s+"
    r"(?P<h2>\d{2}):(?P<m2>\d{2}):(?P<s2>\d{2}),(?P<ms2>\d{3})"
)

RISKY_CUE_ENDINGS = (
    "不会",
    "不能",
    "需要",
    "应该",
    "可以",
    "可能",
    "一到",
    "只要",
    "如果",
    "因为",
    "虽然",
    "由于",
    "为了",
    "但是",
    "而且",
    "所以",
    "把",
    "被",
    "对",
    "向",
    "跟",
    "和",
    "与",
    "或",
    "但",
    "却",
    "而",
)

TRAILING_SUBTITLE_PUNCTUATION = "，,。.!！?？;；:：、…—"
OPENING_SUBTITLE_PUNCTUATION = "“‘（(《【「『〈"
CLOSING_SUBTITLE_PUNCTUATION = "”’）)》】」』〉"


def load_json(path: str | Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path: str | Path, value: object) -> None:
    Path(path).write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def timestamp_to_seconds(h: int, m: int, s: int, ms: int) -> float:
    return h * 3600 + m * 60 + s + ms / 1000


def seconds_to_timestamp(seconds: float) -> str:
    total_ms = max(0, int(round(seconds * 1000)))
    h, rem = divmod(total_ms, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def frame_to_timestamp(frame: int, fps: int) -> str:
    """Format a frame boundary using the floor convention used by 剪映 SRT."""
    total_ms = max(0, frame * 1000 // fps)
    h, rem = divmod(total_ms, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def parse_srt(path: str | Path) -> list[Cue]:
    text = Path(path).read_text(encoding="utf-8-sig").strip()
    if not text:
        return []
    cues: list[Cue] = []
    for block in re.split(r"\r?\n\s*\r?\n", text):
        lines = [line.rstrip("\r") for line in block.splitlines()]
        if len(lines) < 3:
            continue
        try:
            index = int(lines[0].strip())
        except ValueError:
            continue
        match = TIME_RE.fullmatch(lines[1].strip())
        if not match:
            continue
        values = {key: int(value) for key, value in match.groupdict().items()}
        start = timestamp_to_seconds(
            values["h1"], values["m1"], values["s1"], values["ms1"]
        )
        end = timestamp_to_seconds(
            values["h2"], values["m2"], values["s2"], values["ms2"]
        )
        cues.append(Cue(index, start, end, "\n".join(lines[2:])))
    return cues


def count_effective_hanzi(text: str) -> int:
    ignored = r"\s，。！？；：“”‘’、,.!?;:（）()《》【】\[\]—…·"
    return len(re.sub(f"[{ignored}]", "", text))


def strip_subtitle_punctuation(text: str) -> str:
    """Remove only cue-ending marks while preserving internal punctuation."""
    value = text.strip()
    closing_marks: list[str] = []
    while value and value[-1] in CLOSING_SUBTITLE_PUNCTUATION:
        closing_marks.append(value[-1])
        value = value[:-1].rstrip()
    value = value.rstrip(TRAILING_SUBTITLE_PUNCTUATION).rstrip()
    if not value:
        return ""
    return value + "".join(reversed(closing_marks))


def normalize_spoken_text(text: str) -> str:
    """Normalize text for exact source-to-subtitle coverage comparisons."""
    return "".join(
        character
        for character in text
        if not character.isspace()
        and not unicodedata.category(character).startswith("P")
    )


def restore_internal_subtitle_punctuation(
    source_text: str, subtitle_lines: list[str]
) -> list[str]:
    """Project source punctuation into cue interiors without adding ending marks."""
    spoken_lines = [
        normalize_spoken_text(line)
        for line in subtitle_lines
        if line.strip()
    ]
    if not spoken_lines or any(not line for line in spoken_lines):
        raise ValueError("subtitle lines must contain spoken characters")

    source_spoken = normalize_spoken_text(source_text)
    subtitle_spoken = "".join(spoken_lines)
    if source_spoken != subtitle_spoken:
        raise ValueError(
            "subtitle text does not exactly cover the normalized source"
        )

    def is_spoken(character: str) -> bool:
        return (
            not character.isspace()
            and not unicodedata.category(character).startswith("P")
        )

    cursor = 0
    restored: list[str] = []
    for spoken_line in spoken_lines:
        output: list[str] = []
        while cursor < len(source_text) and not is_spoken(source_text[cursor]):
            if source_text[cursor] in OPENING_SUBTITLE_PUNCTUATION:
                output.append(source_text[cursor])
            cursor += 1

        for position, expected in enumerate(spoken_line):
            if cursor >= len(source_text) or source_text[cursor] != expected:
                raise ValueError(
                    "source and subtitle characters diverged while "
                    "restoring punctuation"
                )
            output.append(source_text[cursor])
            cursor += 1
            if position < len(spoken_line) - 1:
                while (
                    cursor < len(source_text)
                    and not is_spoken(source_text[cursor])
                ):
                    if not source_text[cursor].isspace():
                        output.append(source_text[cursor])
                    cursor += 1

        lookahead = cursor
        while (
            lookahead < len(source_text)
            and not is_spoken(source_text[lookahead])
        ):
            if source_text[lookahead] in CLOSING_SUBTITLE_PUNCTUATION:
                output.append(source_text[lookahead])
            lookahead += 1

        restored_line = strip_subtitle_punctuation("".join(output))
        if not restored_line:
            raise ValueError("punctuation restoration produced an empty cue")
        restored.append(restored_line)

    return restored


def risky_cue_ending(text: str) -> str | None:
    """Return a likely hanging phrase at a cue boundary, if present."""
    normalized = strip_subtitle_punctuation(text).strip()
    return next(
        (
            ending
            for ending in RISKY_CUE_ENDINGS
            if normalized.endswith(ending)
        ),
        None,
    )


def snap_up_to_frame(seconds: float, fps: int) -> float:
    return math.ceil(seconds * fps - 1e-9) / fps


def hex_to_rgb(value: str) -> tuple[int, int, int]:
    raw = value.strip().lstrip("#")
    if len(raw) != 6:
        raise ValueError(f"Expected #RRGGBB, got {value!r}")
    return tuple(int(raw[i : i + 2], 16) for i in (0, 2, 4))


def rgb_to_hex(rgb: Iterable[int]) -> str:
    r, g, b = (int(x) for x in rgb)
    return f"#{r:02X}{g:02X}{b:02X}"


def sample_background_rgb(path: str | Path) -> tuple[int, int, int]:
    image = Image.open(path).convert("RGB")
    width, height = image.size
    points = [
        (0, 0),
        (width - 1, 0),
        (0, height - 1),
        (width - 1, height - 1),
        (width // 2, 0),
        (width // 2, height - 1),
    ]
    channels = list(zip(*(image.getpixel(point) for point in points)))
    return tuple(sorted(channel)[len(channel) // 2] for channel in channels)


def resolve_path(base: str | Path, value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else Path(base) / path
