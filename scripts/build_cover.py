#!/usr/bin/env python3
"""Build approved landscape and portrait covers from one shared source image."""

from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

from common import hex_to_rgb, load_json, resolve_path, sample_background_rgb
from normalize_background import connected_background_mask, median_border_rgb


def transparent_subject(path: Path) -> Image.Image:
    image = Image.open(path).convert("RGBA")
    alpha = image.getchannel("A")
    if alpha.getextrema() == (255, 255):
        external = connected_background_mask(image, median_border_rgb(image), 42)
        image.putalpha(external.point(lambda value: 255 - value))
    bbox = image.getchannel("A").getbbox()
    return image.crop(bbox) if bbox else image


def cover_variants(config: dict) -> dict[str, dict]:
    cover = config["cover"]
    variants = cover.get("variants")
    if isinstance(variants, dict) and {"landscape", "portrait"} <= set(variants):
        return variants

    landscape = {
        "label": "横版 4:3",
        "width": int(cover["width"]),
        "height": int(cover["height"]),
        "font_size": int(cover["font_size"]),
        "line_spacing_px": int(cover["line_spacing_px"]),
        "first_line_center_y": int(cover["first_line_center_y"]),
        "text_center_x": int(cover.get("text_center_x", cover["width"] // 2)),
        "image_area": dict(cover["image_area"]),
    }
    portrait = {
        "label": "竖版 3:4",
        "width": 1242,
        "height": 1660,
        "font_size": 92,
        "line_spacing_px": 24,
        "first_line_center_y": 300,
        "text_center_x": 621,
        "image_area": {
            "left": 120,
            "top": 690,
            "right": 1122,
            "bottom": 1580,
        },
    }
    return {"landscape": landscape, "portrait": portrait}


def plan_cover_lines(plan: dict) -> list[str]:
    values = plan.get("cover", {}).get("title_lines") or []
    lines = [
        str(value.get("text", "")) if isinstance(value, dict) else str(value)
        for value in values
    ]
    return [line for line in lines if line.strip()] or [
        str(value) for value in (plan.get("intro_lines") or [plan.get("title", "")])
    ]


def approved_layout(
    approval: dict,
    key: str,
    variant: dict,
    legacy_cover_config: dict,
) -> dict:
    covers = approval.get("covers")
    if isinstance(covers, dict) and isinstance(covers.get(key), dict):
        return covers[key]
    if key == "landscape" and isinstance(approval.get("cover"), dict):
        layout = dict(approval["cover"])
        old_width = int(legacy_cover_config.get("width", variant["width"]))
        old_height = int(legacy_cover_config.get("height", variant["height"]))
        scale_x = int(variant["width"]) / max(1, old_width)
        scale_y = int(variant["height"]) / max(1, old_height)
        font_scale = min(scale_x, scale_y)
        for field in ("text_center_x", "subject_center_x"):
            if field in layout:
                layout[field] = round(float(layout[field]) * scale_x)
        for field in ("first_line_center_y", "subject_center_y"):
            if field in layout:
                layout[field] = round(float(layout[field]) * scale_y)
        if "font_size" in layout:
            layout["font_size"] = round(float(layout["font_size"]) * font_scale)
        if "line_spacing_px" in layout:
            layout["line_spacing_px"] = round(
                float(layout["line_spacing_px"]) * scale_y
            )
        if "letter_spacing_px" in layout:
            layout["letter_spacing_px"] = round(
                float(layout["letter_spacing_px"]) * scale_x
            )
        return layout
    return {}


def render_cover(
    *,
    plan: dict,
    approval: dict,
    config: dict,
    variant_key: str,
    variant: dict,
    output: Path,
    assets: Path,
    root: Path,
    subject_source: Image.Image,
    background_rgb: tuple[int, int, int],
) -> None:
    width, height = int(variant["width"]), int(variant["height"])
    canvas = Image.new("RGB", (width, height), background_rgb)
    draw = ImageDraw.Draw(canvas)
    layout = approved_layout(
        approval,
        variant_key,
        variant,
        config["cover"],
    )

    fallback_layout = approved_layout(
        approval,
        "landscape",
        cover_variants(config)["landscape"],
        config["cover"],
    )
    approved_lines = layout.get("title_lines") or fallback_layout.get("title_lines")
    lines = (
        [str(line) for line in approved_lines if str(line).strip()]
        if approved_lines
        else plan_cover_lines(plan)
    )
    font_size = int(layout.get("font_size", variant["font_size"]))
    font_path = resolve_path(assets, config["font"]["asset"])
    font_cache: dict[int, ImageFont.FreeTypeFont] = {}

    def sized_font(scale: float = 1.0) -> ImageFont.FreeTypeFont:
        size = max(12, int(round(font_size * scale)))
        if size not in font_cache:
            font_cache[size] = ImageFont.truetype(str(font_path), size)
        return font_cache[size]

    accent_indices = {
        int(value)
        for value in layout.get(
            "accent_char_indices",
            fallback_layout.get("accent_char_indices", []),
        )
    }
    text_center_x = int(
        layout.get("text_center_x", variant.get("text_center_x", width // 2))
    )
    first_line_y = int(
        layout.get("first_line_center_y", variant["first_line_center_y"])
    )
    line_spacing = int(
        layout.get("line_spacing_px", variant["line_spacing_px"])
    )
    letter_spacing = int(layout.get("letter_spacing_px", 0))
    raw_character_scales = layout.get("character_size_scales", {})
    character_scales: dict[int, float] = {}
    if isinstance(raw_character_scales, dict):
        for raw_index, raw_scale in raw_character_scales.items():
            try:
                index = int(raw_index)
                scale = float(raw_scale)
            except (TypeError, ValueError):
                continue
            if index >= 0 and 0.5 <= scale <= 2.0:
                character_scales[index] = scale
    line_step = font_size + line_spacing
    character_offset = 0
    for line_index, line in enumerate(lines):
        line_items: list[
            tuple[str, ImageFont.FreeTypeFont, int | None]
        ] = []
        probe_offset = character_offset
        for character in line:
            ordinal = None
            scale = 1.0
            if not character.isspace():
                ordinal = probe_offset
                scale = character_scales.get(probe_offset, 1.0)
                probe_offset += 1
            line_items.append((character, sized_font(scale), ordinal))
        line_width = sum(
            float(draw.textlength(character, font=character_font))
            for character, character_font, _ in line_items
        )
        if len(line_items) > 1:
            line_width += letter_spacing * (len(line_items) - 1)
        cursor_x = text_center_x - line_width / 2
        center_y = first_line_y + line_index * line_step
        for item_index, (character, character_font, ordinal) in enumerate(
            line_items
        ):
            color = (
                config["colors"]["accent"]
                if ordinal is not None and ordinal in accent_indices
                else config["colors"]["ink"]
            )
            draw.text(
                (cursor_x, center_y),
                character,
                font=character_font,
                fill=hex_to_rgb(color),
                anchor="lm",
            )
            cursor_x += float(
                draw.textlength(character, font=character_font)
            )
            if item_index < len(line_items) - 1:
                cursor_x += letter_spacing
            if ordinal is not None:
                character_offset += 1

    area = variant["image_area"]
    target_size = (
        int(area["right"] - area["left"]),
        int(area["bottom"] - area["top"]),
    )
    subject = ImageOps.contain(
        subject_source,
        target_size,
        Image.Resampling.LANCZOS,
    )
    subject_scale = float(layout.get("subject_scale", 1.0))
    if subject_scale != 1.0:
        subject = subject.resize(
            (
                max(1, int(round(subject.width * subject_scale))),
                max(1, int(round(subject.height * subject_scale))),
            ),
            Image.Resampling.LANCZOS,
        )
    subject_center_x = int(
        layout.get(
            "subject_center_x",
            (int(area["left"]) + int(area["right"])) // 2,
        )
    )
    subject_center_y = int(
        layout.get(
            "subject_center_y",
            (int(area["top"]) + int(area["bottom"])) // 2,
        )
    )
    x = int(round(subject_center_x - subject.width / 2))
    y = int(round(subject_center_y - subject.height / 2))
    canvas.paste(subject, (x, y), subject)

    output.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(output)
    print(
        f"Wrote {variant.get('label', variant_key)} cover "
        f"({width}x{height}) to {output}"
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("plan", type=Path)
    parser.add_argument("approval", type=Path)
    parser.add_argument("config", type=Path)
    parser.add_argument(
        "output",
        type=Path,
        help="Landscape output; portrait defaults to <stem>_portrait.png",
    )
    parser.add_argument("--portrait-output", type=Path)
    args = parser.parse_args()

    plan = load_json(args.plan)
    approval = load_json(args.approval)
    config = load_json(args.config)
    if approval.get("approved") is not True:
        raise SystemExit("Refusing to build covers: project is not approved")

    root = args.plan.parent
    assets = resolve_path(args.config.parent, config.get("asset_root", "."))
    background_path = resolve_path(assets, config["canvas"]["background_asset"])
    background_rgb = sample_background_rgb(background_path)
    image_path = resolve_path(root, plan.get("cover", {}).get("image_path", ""))
    if not image_path.exists():
        raise SystemExit(f"Cover image missing: {image_path}")
    subject_source = transparent_subject(image_path)

    suffix = args.output.suffix or ".png"
    portrait_output = args.portrait_output or args.output.with_name(
        f"{args.output.stem}_portrait{suffix}"
    )
    variants = cover_variants(config)
    render_cover(
        plan=plan,
        approval=approval,
        config=config,
        variant_key="landscape",
        variant=variants["landscape"],
        output=args.output,
        assets=assets,
        root=root,
        subject_source=subject_source,
        background_rgb=background_rgb,
    )
    render_cover(
        plan=plan,
        approval=approval,
        config=config,
        variant_key="portrait",
        variant=variants["portrait"],
        output=portrait_output,
        assets=assets,
        root=root,
        subject_source=subject_source,
        background_rgb=background_rgb,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
