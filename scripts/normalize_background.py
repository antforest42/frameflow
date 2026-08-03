#!/usr/bin/env python3
"""Remove only the light background connected to the canvas edge.

Internal white details remain intact because the mask is connectivity-based.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter

from common import hex_to_rgb, rgb_to_hex, sample_background_rgb

DEFAULT_TOLERANCE = 42
DEFAULT_EDGE_SOFTNESS = 1.2


def median_border_rgb(image: Image.Image) -> tuple[int, int, int]:
    image = image.convert("RGB")
    width, height = image.size
    step = max(1, min(width, height) // 80)
    pixels: list[tuple[int, int, int]] = []
    for x in range(0, width, step):
        pixels.append(image.getpixel((x, 0)))
        pixels.append(image.getpixel((x, height - 1)))
    for y in range(0, height, step):
        pixels.append(image.getpixel((0, y)))
        pixels.append(image.getpixel((width - 1, y)))
    channels = list(zip(*pixels))
    return tuple(sorted(channel)[len(channel) // 2] for channel in channels)


def connected_background_mask(
    image: Image.Image, source_rgb: tuple[int, int, int], tolerance: int
) -> Image.Image:
    rgb = image.convert("RGB")
    reference = Image.new("RGB", rgb.size, source_rgb)
    difference = ImageChops.difference(rgb, reference)
    gray = difference.convert("L")
    candidate = gray.point(lambda value: 255 if value <= tolerance else 0, mode="L")
    width, height = candidate.size
    seeds = [
        (0, 0),
        (width - 1, 0),
        (0, height - 1),
        (width - 1, height - 1),
        (width // 2, 0),
        (width // 2, height - 1),
        (0, height // 2),
        (width - 1, height // 2),
    ]
    for seed in seeds:
        if candidate.getpixel(seed) == 255:
            ImageDraw.floodfill(candidate, seed, 128, thresh=0)
    return candidate.point(lambda value: 255 if value == 128 else 0, mode="L")


def load_foreground_image(
    path: str | Path,
    *,
    crop: bool,
    tolerance: int = DEFAULT_TOLERANCE,
    edge_softness: float = 0.0,
) -> Image.Image:
    """Load an RGBA subject, removing only edge-connected flat background."""
    image = Image.open(path).convert("RGBA")
    if image.getchannel("A").getextrema() == (255, 255):
        external = connected_background_mask(
            image,
            median_border_rgb(image),
            tolerance,
        )
        if edge_softness > 0:
            external = external.filter(ImageFilter.GaussianBlur(edge_softness))
        image.putalpha(external.point(lambda value: 255 - value))
    if crop:
        bbox = image.getchannel("A").getbbox()
        if bbox:
            image = image.crop(bbox)
    return image


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--background-image", type=Path)
    target.add_argument("--background-hex")
    parser.add_argument(
        "--mode", choices=("transparent", "composite"), default="transparent"
    )
    parser.add_argument("--tolerance", type=int, default=DEFAULT_TOLERANCE)
    parser.add_argument(
        "--edge-softness",
        type=float,
        default=DEFAULT_EDGE_SOFTNESS,
    )
    args = parser.parse_args()

    source = Image.open(args.input).convert("RGBA")
    source_rgb = median_border_rgb(source)
    target_rgb = (
        sample_background_rgb(args.background_image)
        if args.background_image
        else hex_to_rgb(args.background_hex)
    )
    external = connected_background_mask(source, source_rgb, args.tolerance)
    if args.edge_softness > 0:
        external = external.filter(ImageFilter.GaussianBlur(args.edge_softness))

    target_layer = Image.new("RGBA", source.size, (*target_rgb, 255))
    cleaned_rgb = Image.composite(target_layer, source, external).convert("RGBA")

    if args.mode == "transparent":
        cleaned_rgb.putalpha(external.point(lambda value: 255 - value))
        result = cleaned_rgb
    else:
        result = Image.alpha_composite(target_layer, cleaned_rgb)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    if args.output.suffix.lower() not in {".png", ".webp"} and result.mode == "RGBA":
        result = result.convert("RGB")
    result.save(args.output)
    print(
        f"{args.input.name}: source {rgb_to_hex(source_rgb)} -> "
        f"target {rgb_to_hex(target_rgb)} ({args.mode})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
