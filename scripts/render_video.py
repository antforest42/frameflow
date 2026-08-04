#!/usr/bin/env python3
"""Render the approved picture track with burned subtitles."""

from __future__ import annotations

import argparse
import math
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

from common import (
    Cue,
    hex_to_rgb,
    load_json,
    parse_srt,
    resolve_path,
    sample_background_rgb,
)
from normalize_background import connected_background_mask, median_border_rgb

_TEXT_LAYER_CACHE: dict[tuple, Image.Image] = {}
_DEFAULT_LEFT_BRAND = {
    "text_source": "plan.title",
    "avatar_asset": "brand-avatar.png",
    "avatar_size_px": 64,
    "avatar_margin_x": 12,
    "avatar_margin_y": 12,
    "text_gap_px": 14,
    "text_y_px": 29,
    "pixel_font_size": 31,
}


@dataclass
class Scene:
    scene_id: str
    start: float
    end: float
    image: Image.Image
    x: int
    y: int


def left_brand_config(config: dict) -> dict:
    """Return the fixed header layout, including fallback for older projects."""
    configured = config.get("text_layers", {}).get("top_left", {})
    if not isinstance(configured, dict) or not configured.get("avatar_asset"):
        return dict(_DEFAULT_LEFT_BRAND)
    return {**_DEFAULT_LEFT_BRAND, **configured}


def draw_fixed_brand_header(
    image: Image.Image,
    assets: Path,
    config: dict,
    left_title_text: str,
    right_brand_text: str,
) -> None:
    text_layers = config["text_layers"]
    left = left_brand_config(config)
    right = text_layers["top_right"]
    font_size = int(right["pixel_font_size"])
    font_path = resolve_path(assets, config["font"]["asset"])
    font = ImageFont.truetype(str(font_path), font_size)

    avatar_size = int(left["avatar_size_px"])
    avatar_x = int(left["avatar_margin_x"])
    avatar_y = int(left["avatar_margin_y"])
    avatar_path = resolve_path(assets, left["avatar_asset"])
    with Image.open(avatar_path) as source_avatar:
        avatar = source_avatar.convert("RGBA").resize(
            (avatar_size, avatar_size),
            Image.Resampling.LANCZOS,
        )
    image.alpha_composite(avatar, (avatar_x, avatar_y))

    draw = ImageDraw.Draw(image)
    left_x = avatar_x + avatar_size + int(left["text_gap_px"])
    draw.text(
        (left_x, int(left["text_y_px"])),
        left_title_text,
        font=font,
        fill=hex_to_rgb(config["colors"]["accent"]),
        anchor="la",
    )
    draw.text(
        (image.width - int(right["margin_x"]), int(right["margin_y"])),
        right_brand_text,
        font=font,
        fill=hex_to_rgb(config["colors"]["ink"]),
        anchor="ra",
    )


def draw_centered_lines(
    image: Image.Image,
    lines: list[str],
    center: tuple[int, int],
    font: ImageFont.FreeTypeFont,
    fill: tuple[int, int, int],
    spacing: int,
    horizontal_scale: float = 1.0,
    opacity: float = 1.0,
    scale: float = 1.0,
    supersample: int = 1,
) -> None:
    supersample = max(1, int(supersample))
    render_font = (
        font.font_variant(size=font.size * supersample)
        if supersample > 1
        else font
    )
    render_spacing = spacing * supersample
    text = "\n".join(lines)
    cache_key = (
        text,
        str(getattr(font, "path", "")),
        font.size,
        fill,
        spacing,
        horizontal_scale,
        supersample,
    )
    layer = _TEXT_LAYER_CACHE.get(cache_key)
    if layer is None:
        measure = ImageDraw.Draw(Image.new("L", (1, 1)))
        bbox = measure.multiline_textbbox(
            (0, 0),
            text,
            font=render_font,
            spacing=render_spacing,
            align="center",
        )
        padding = 8 * supersample
        width = max(1, int(round(bbox[2] - bbox[0])))
        height = max(1, int(round(bbox[3] - bbox[1])))
        layer = Image.new(
            "RGBA",
            (width + padding * 2, height + padding * 2),
            (0, 0, 0, 0),
        )
        layer_draw = ImageDraw.Draw(layer)
        layer_draw.multiline_text(
            (padding - bbox[0], padding - bbox[1]),
            text,
            font=render_font,
            fill=(*fill, 255),
            spacing=render_spacing,
            align="center",
        )
        if horizontal_scale != 1.0:
            layer = layer.resize(
                (
                    max(1, int(round(layer.width * horizontal_scale))),
                    layer.height,
                ),
                Image.Resampling.LANCZOS,
            )
        _TEXT_LAYER_CACHE[cache_key] = layer

    if scale != 1.0:
        scale = max(0.01, scale)
        layer = layer.resize(
            (
                max(1, int(round(layer.width * scale))),
                max(1, int(round(layer.height * scale))),
            ),
            Image.Resampling.LANCZOS,
        )

    opacity = max(0.0, min(1.0, opacity))
    if opacity < 1.0:
        layer = layer.copy()
        alpha = layer.getchannel("A")
        layer.putalpha(alpha.point(lambda value: int(round(value * opacity))))

    if supersample > 1:
        target = Image.new(
            "RGBA",
            (image.width * supersample, image.height * supersample),
            (0, 0, 0, 0),
        )
        target.alpha_composite(
            layer,
            (
                int(round(center[0] * supersample - layer.width / 2)),
                int(round(center[1] * supersample - layer.height / 2)),
            ),
        )
        image.alpha_composite(
            target.resize(image.size, Image.Resampling.LANCZOS)
        )
    else:
        image.alpha_composite(
            layer,
            (
                int(round(center[0] - layer.width / 2)),
                int(round(center[1] - layer.height / 2)),
            ),
        )


def ease_out_quad(progress: float) -> float:
    progress = max(0.0, min(1.0, progress))
    return 1.0 - (1.0 - progress) ** 2


def intro_opacity(time_seconds: float, fade_seconds: float) -> float:
    if fade_seconds <= 0:
        return 1.0
    return ease_out_quad(time_seconds / fade_seconds)


def intro_scale(
    time_seconds: float,
    zoom_seconds: float,
    start_scale: float,
) -> float:
    if zoom_seconds <= 0:
        return 1.0
    eased = ease_out_quad(time_seconds / zoom_seconds)
    return start_scale + (1.0 - start_scale) * eased


def subject_from_image(path: Path) -> Image.Image:
    image = Image.open(path).convert("RGBA")
    alpha = image.getchannel("A")
    if alpha.getextrema() == (255, 255):
        external = connected_background_mask(image, median_border_rgb(image), 42)
        external = external.filter(ImageFilterGaussian(1.0))
        image.putalpha(external.point(lambda value: 255 - value))
    # Keep the original canvas. The approved samples intentionally use
    # off-centre composition; cropping to the alpha bbox changes that layout.
    return image


def ImageFilterGaussian(radius: float):
    from PIL import ImageFilter

    return ImageFilter.GaussianBlur(radius)


def prepare_scene(
    scene_data: dict,
    cues: dict[int, Cue],
    root: Path,
    image_area: dict,
) -> Scene:
    cue_start = cues[int(scene_data["cue_start"])]
    cue_end = cues[int(scene_data["cue_end"])]
    subject = subject_from_image(resolve_path(root, scene_data["image_path"]))
    target = (
        image_area["right"] - image_area["left"],
        image_area["bottom"] - image_area["top"],
    )
    subject = ImageOps.contain(subject, target, Image.Resampling.LANCZOS)
    image_scale = float(scene_data.get("image_scale", 1.0))
    if not 0.5 <= image_scale <= 1.6:
        raise SystemExit(
            f"scene {scene_data['id']} image_scale must be between 0.5 and 1.6"
        )
    if image_scale != 1.0:
        subject = subject.resize(
            (
                max(1, int(round(subject.width * image_scale))),
                max(1, int(round(subject.height * image_scale))),
            ),
            Image.Resampling.LANCZOS,
        )
    offset_x = int(scene_data.get("image_offset_x_px", 0))
    offset_y = int(scene_data.get("image_offset_y_px", 0))
    x = (
        image_area["left"]
        + (target[0] - subject.width) // 2
        + offset_x
    )
    y = (
        image_area["top"]
        + (target[1] - subject.height) // 2
        + offset_y
    )
    return Scene(
        str(scene_data["id"]).zfill(2),
        cue_start.start,
        cue_end.end,
        subject,
        x,
        y,
    )


def active_cue(cues: list[Cue], time_seconds: float) -> Cue | None:
    for cue in cues:
        if cue.start <= time_seconds < cue.end:
            return cue
    return None


def active_scene_index(scenes: list[Scene], time_seconds: float) -> int | None:
    current = None
    for index, scene in enumerate(scenes):
        if scene.start <= time_seconds:
            current = index
        else:
            break
    return current


def paste_layer(canvas: Image.Image, layer: Image.Image, x: int, y: int) -> None:
    canvas.alpha_composite(layer, (x, y))


def fitted_font(
    font_path: Path,
    preferred_size: int,
    minimum_size: int,
    text: str,
    max_width: int,
) -> ImageFont.FreeTypeFont:
    size = preferred_size
    while size > minimum_size:
        font = ImageFont.truetype(str(font_path), size)
        box = font.getbbox(text)
        if box[2] - box[0] <= max_width:
            return font
        size -= 1
    return ImageFont.truetype(str(font_path), minimum_size)


def render_frames(
    process: subprocess.Popen,
    duration: float,
    fps: int,
    canvas_size: tuple[int, int],
    intro_base: Image.Image,
    body_base: Image.Image,
    intro_lines: list[str],
    intro_end: float,
    intro_font: ImageFont.FreeTypeFont,
    intro_color: tuple[int, int, int],
    intro_y: int,
    intro_spacing: int,
    intro_horizontal_scale: float,
    intro_fade_seconds: float,
    intro_start_scale: float,
    intro_zoom_seconds: float,
    intro_supersample: int,
    subtitle_font: ImageFont.FreeTypeFont,
    subtitle_font_path: Path,
    subtitle_minimum_size: int,
    subtitle_max_width: int,
    subtitle_color: tuple[int, int, int],
    subtitle_x: int,
    subtitle_y: int,
    cues: list[Cue],
    scenes: list[Scene],
    transition_seconds: float,
) -> None:
    total_frames = int(math.ceil(duration * fps))
    width, height = canvas_size
    for frame_index in range(total_frames):
        time_seconds = frame_index / fps
        if time_seconds < intro_end:
            frame = intro_base.copy()
            draw_centered_lines(
                frame,
                intro_lines,
                (width // 2, intro_y),
                intro_font,
                intro_color,
                intro_spacing,
                intro_horizontal_scale,
                intro_opacity(time_seconds, intro_fade_seconds),
                intro_scale(
                    time_seconds,
                    intro_zoom_seconds,
                    intro_start_scale,
                ),
                intro_supersample,
            )
        else:
            frame = body_base.copy()
            draw = ImageDraw.Draw(frame)
            transition_index = None
            transition_progress = 0.0
            if transition_seconds > 0:
                half = transition_seconds / 2
                for index in range(1, len(scenes)):
                    boundary = scenes[index].start
                    if boundary - half <= time_seconds < boundary + half:
                        transition_index = index
                        transition_progress = (
                            time_seconds - (boundary - half)
                        ) / transition_seconds
                        break
            if transition_index is not None:
                scene = scenes[transition_index]
                previous = scenes[transition_index - 1]
                paste_layer(
                    frame,
                    previous.image,
                    int(previous.x - transition_progress * width),
                    previous.y,
                )
                paste_layer(
                    frame,
                    scene.image,
                    int(scene.x + (1 - transition_progress) * width),
                    scene.y,
                )
            else:
                scene_index = active_scene_index(scenes, time_seconds)
                if scene_index is not None:
                    scene = scenes[scene_index]
                    paste_layer(frame, scene.image, scene.x, scene.y)
            cue = active_cue(cues, time_seconds)
            if cue:
                cue_font = fitted_font(
                    subtitle_font_path,
                    subtitle_font.size,
                    subtitle_minimum_size,
                    cue.text,
                    subtitle_max_width,
                )
                draw.multiline_text(
                    (subtitle_x, subtitle_y),
                    cue.text,
                    font=cue_font,
                    fill=subtitle_color,
                    spacing=4,
                    align="center",
                    anchor="mm",
                )
        try:
            process.stdin.write(frame.convert("RGB").tobytes())
        except BrokenPipeError as exc:
            raise RuntimeError("FFmpeg stopped while receiving frames") from exc
        if frame_index and frame_index % (fps * 5) == 0:
            print(
                f"Rendered {frame_index / fps:.0f}/{duration:.0f}s",
                file=sys.stderr,
                flush=True,
            )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("plan", type=Path)
    parser.add_argument("approval", type=Path)
    parser.add_argument("config", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument(
        "--preview-seconds",
        type=float,
        help="Render only the opening N seconds for calibration",
    )
    parser.add_argument(
        "--picture-only",
        action="store_true",
        help="Write a silent picture track for the final narration mix",
    )
    args = parser.parse_args()

    plan = load_json(args.plan)
    approval = load_json(args.approval)
    config = load_json(args.config)
    if approval.get("approved") is not True:
        raise SystemExit("Refusing to render: approval.approved is not true")
    include_intro = approval.get("include_intro", True)
    if not isinstance(include_intro, bool):
        raise SystemExit("approval.include_intro must be true or false")

    root = args.plan.parent
    assets = resolve_path(
        args.config.parent, config.get("asset_root", ".")
    )
    width = int(config["canvas"]["width"])
    height = int(config["canvas"]["height"])
    fps = int(config["canvas"]["fps"])
    background_path = resolve_path(assets, config["canvas"]["background_asset"])
    background_rgb = sample_background_rgb(background_path)
    font_path = resolve_path(assets, config["font"]["asset"])
    text_layers = config["text_layers"]
    subtitle_font = ImageFont.truetype(
        str(font_path), text_layers["subtitle"]["pixel_font_size"]
    )
    intro_font = ImageFont.truetype(
        str(font_path), text_layers["intro_title"]["pixel_font_size"]
    )

    intro_base = Image.new("RGBA", (width, height), (*background_rgb, 255))
    body_base = intro_base.copy()
    draw = ImageDraw.Draw(body_base)
    line_y = int(config["canvas"]["line_y_px"])
    line_thickness = int(config["canvas"].get("line_thickness_px", 2))
    draw.rectangle(
        (0, line_y, width, line_y + line_thickness - 1),
        fill=hex_to_rgb(config["colors"]["ink"]),
    )
    brand = approval.get("brand_text") or plan.get("brand_text") or config["brand_text"]
    draw_fixed_brand_header(body_base, assets, config, str(plan["title"]), brand)

    srt_path = resolve_path(root, plan["srt_path"])
    cues = parse_srt(srt_path)
    if not cues:
        raise SystemExit("SRT contains no cues")
    intro_shift = 0.0 if include_intro else cues[0].start
    if intro_shift:
        cues = [
            Cue(
                cue.index,
                max(0.0, cue.start - intro_shift),
                max(0.0, cue.end - intro_shift),
                cue.text,
            )
            for cue in cues
        ]
    cue_map = {cue.index: cue for cue in cues}
    scenes = [
        prepare_scene(scene, cue_map, root, config["image_area"])
        for scene in plan["scenes"]
    ]
    full_duration = cues[-1].end + config["timing"]["tail_seconds"]
    duration = min(full_duration, args.preview_seconds) if args.preview_seconds else full_duration
    intro_end = cues[0].start if include_intro else 0.0
    output_config = config["output"]

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="frameflow-render-") as temp:
        silent = Path(temp) / "silent.mp4"
        encode_command = [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            "-s",
            f"{width}x{height}",
            "-r",
            str(fps),
            "-i",
            "-",
            "-an",
            "-c:v",
            output_config["video_codec"],
            "-preset",
            output_config["preset"],
            "-crf",
            str(output_config["crf"]),
            "-pix_fmt",
            "yuv420p",
            str(silent),
        ]
        encoder = subprocess.Popen(
            encode_command, stdin=subprocess.PIPE, stderr=subprocess.PIPE
        )
        assert encoder.stdin is not None
        render_frames(
            encoder,
            duration,
            fps,
            (width, height),
            intro_base,
            body_base,
            approval.get("intro_lines")
            or plan.get("intro_lines")
            or [plan["title"]],
            intro_end,
            intro_font,
            hex_to_rgb(config["colors"]["intro"]),
            text_layers["intro_title"]["center_y"],
            text_layers["intro_title"]["line_spacing_px"],
            text_layers["intro_title"].get("horizontal_scale", 1.0),
            float(text_layers["intro_title"].get("fade_in_seconds", 0.0)),
            float(text_layers["intro_title"].get("start_scale", 1.0)),
            float(text_layers["intro_title"].get("zoom_in_seconds", 0.0)),
            int(text_layers["intro_title"].get("supersample", 4)),
            subtitle_font,
            font_path,
            int(text_layers["subtitle"].get("minimum_pixel_font_size", 56)),
            int(text_layers["subtitle"].get("max_width_px", width - 80)),
            hex_to_rgb(config["colors"]["ink"]),
            int(text_layers["subtitle"].get("center_x", width // 2)),
            text_layers["subtitle"]["center_y"],
            cues,
            scenes,
            config["transition"]["duration_seconds"],
        )
        encoder.stdin.close()
        stderr = encoder.stderr.read().decode("utf-8", errors="replace")
        return_code = encoder.wait()
        if return_code:
            raise SystemExit(f"Video encoder failed:\n{stderr}")

        if args.picture_only:
            shutil.copy2(silent, args.output)
            print(f"Wrote picture track to {args.output}")
            return 0

        bgm_path = resolve_path(assets, config["audio"]["bgm_asset"])
        mux_command = [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-i",
            str(silent),
            "-stream_loop",
            "-1",
            "-i",
            str(bgm_path),
            "-filter:a",
            f"volume={config['audio']['volume_db']}dB",
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-t",
            f"{duration:.3f}",
            "-c:v",
            "copy",
            "-c:a",
            output_config["audio_codec"],
            "-b:a",
            output_config["audio_bitrate"],
            "-movflags",
            "+faststart",
            str(args.output),
        ]
        subprocess.run(mux_command, check=True)
    print(f"Wrote preview video to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
