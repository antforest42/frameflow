#!/usr/bin/env python3
"""Validate a project before deterministic rendering."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
from pathlib import Path

from common import (
    count_effective_hanzi,
    load_json,
    normalize_spoken_text,
    parse_srt,
    resolve_path,
    rgb_to_hex,
    risky_cue_ending,
    sample_background_rgb,
    strip_subtitle_punctuation,
)
from generate_manbo_tts import clean_source_text, sha256_text


PRODUCTION_STATUSES = (
    "script_prepared",
    "narration_generated",
    "audio_aligned",
    "awaiting_image_approval",
    "images_approved",
    "completed",
)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def media_duration(path: Path) -> float | None:
    if shutil.which("ffprobe") is None:
        return None
    probe = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        capture_output=True,
        text=True,
    )
    if probe.returncode:
        return None
    try:
        return float(probe.stdout.strip())
    except ValueError:
        return None


def contains_sensitive_manifest_data(value: object) -> bool:
    if isinstance(value, dict):
        for key, child in value.items():
            normalized_key = str(key).strip().lower()
            if normalized_key in {
                "api_key",
                "authorization",
                "request_headers",
                "request_url",
                "audio_url",
                "token",
            }:
                return True
            if contains_sensitive_manifest_data(child):
                return True
    elif isinstance(value, list):
        return any(contains_sensitive_manifest_data(item) for item in value)
    elif isinstance(value, str):
        lowered = value.lower()
        return "authorization: bearer " in lowered or "?key=" in lowered
    return False


def validate_narration_manifest(
    plan: dict,
    config: dict,
    root: Path,
    errors: list[str],
) -> None:
    manifest_value = plan.get("narration_manifest_path")
    if not manifest_value:
        errors.append(
            "plan.narration_manifest_path is required for Milora narration"
        )
        return
    manifest_path = resolve_path(root, manifest_value)
    if not manifest_path.is_file():
        errors.append(f"narration manifest missing: {manifest_path}")
        return
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as error:
        errors.append(f"narration manifest is invalid JSON: {error}")
        return
    if not isinstance(manifest, dict):
        errors.append("narration manifest must be a JSON object")
        return

    if manifest.get("schema_version") != 1:
        errors.append("narration manifest schema_version must be 1")
    if manifest.get("provider") != "milora_manbo":
        errors.append("narration manifest provider must be milora_manbo")
    if manifest.get("endpoint_mode") not in {"free", "vip"}:
        errors.append("narration manifest endpoint_mode must be free or vip")
    if contains_sensitive_manifest_data(manifest):
        errors.append("narration manifest contains API credentials or request URLs")

    source_value = plan.get("normalized_text_path")
    source_path = resolve_path(root, source_value) if source_value else None
    if source_path is not None and source_path.is_file():
        source_text = clean_source_text(
            source_path.read_text(encoding="utf-8-sig")
        )
        if manifest.get("source_sha256") != sha256_text(source_text):
            errors.append(
                "narration manifest source_sha256 does not match "
                "normalized_script.txt"
            )
    else:
        source_text = ""

    narration_config = config.get("narration", {})
    expected_lead_frames = int(
        narration_config.get(
            "lead_in_frames",
            config.get("timing", {}).get("lead_in_frames", 0),
        )
    )
    expected_fps = int(config["canvas"]["fps"])
    if manifest.get("lead_in_frames") != expected_lead_frames:
        errors.append("narration manifest lead_in_frames does not match config")
    if manifest.get("fps") != expected_fps:
        errors.append("narration manifest fps does not match config")

    resolved_audio: dict[str, Path] = {}
    for key in ("body_audio_path", "output_audio_path"):
        value = manifest.get(key)
        if not isinstance(value, str) or not value:
            errors.append(f"narration manifest {key} is required")
            continue
        path = resolve_path(root, value)
        resolved_audio[key] = path
        if not path.is_file():
            errors.append(f"narration manifest audio missing: {path}")
            continue
        expected_hash = manifest.get(key.replace("_path", "_sha256"))
        if expected_hash and file_sha256(path) != expected_hash:
            errors.append(f"narration manifest {key} hash does not match")

    narration_value = plan.get("narration_audio_path")
    output_path = resolved_audio.get("output_audio_path")
    if narration_value and output_path is not None:
        if resolve_path(root, narration_value).resolve() != output_path.resolve():
            errors.append(
                "narration manifest output_audio_path does not match "
                "plan.narration_audio_path"
            )

    body_path = resolved_audio.get("body_audio_path")
    if (
        body_path is not None
        and output_path is not None
        and body_path.is_file()
        and output_path.is_file()
    ):
        body_duration = media_duration(body_path)
        output_duration = media_duration(output_path)
        tolerance = 1 / expected_fps + 0.02
        if body_duration is not None and output_duration is not None:
            declared_body = manifest.get("body_duration_seconds")
            declared_output = manifest.get("output_duration_seconds")
            if (
                not isinstance(declared_body, (int, float))
                or abs(float(declared_body) - body_duration) > tolerance
            ):
                errors.append(
                    "narration manifest body_duration_seconds is inaccurate"
                )
            if (
                not isinstance(declared_output, (int, float))
                or abs(float(declared_output) - output_duration) > tolerance
            ):
                errors.append(
                    "narration manifest output_duration_seconds is inaccurate"
                )
            expected_lead = expected_lead_frames / expected_fps
            if abs((output_duration - body_duration) - expected_lead) > tolerance:
                errors.append(
                    "narration output does not contain the configured lead-in"
                )

    segments = manifest.get("segments")
    if not isinstance(segments, list) or not segments:
        errors.append("narration manifest segments must be a non-empty list")
        return
    indices: list[int] = []
    segment_texts: list[str] = []
    for position, segment in enumerate(segments, 1):
        if not isinstance(segment, dict):
            errors.append(f"narration segment {position} is not an object")
            continue
        index = segment.get("index")
        if not isinstance(index, int):
            errors.append(f"narration segment {position} has no integer index")
        else:
            indices.append(index)
        text = segment.get("text")
        if not isinstance(text, str) or not text:
            errors.append(f"narration segment {position} text is missing")
        else:
            segment_texts.append(text)
            if segment.get("characters") != len(text):
                errors.append(
                    f"narration segment {position} character count is wrong"
                )
            if segment.get("text_sha256") != sha256_text(text):
                errors.append(
                    f"narration segment {position} text hash is wrong"
                )
        audio_value = segment.get("audio_path")
        if not isinstance(audio_value, str) or not audio_value:
            errors.append(f"narration segment {position} audio_path is missing")
            continue
        audio_path = resolve_path(root, audio_value)
        if not audio_path.is_file():
            errors.append(f"narration segment audio missing: {audio_path}")
            continue
        expected_hash = segment.get("audio_sha256")
        if expected_hash and file_sha256(audio_path) != expected_hash:
            errors.append(f"narration segment {position} audio hash is wrong")

    if indices and indices != list(range(1, len(segments) + 1)):
        errors.append("narration segment indices must be consecutive from 1")
    if source_text and normalize_spoken_text("".join(segment_texts)) != (
        normalize_spoken_text(source_text)
    ):
        errors.append("narration segments do not completely cover the source")


def configured_cover_variants(config: dict) -> dict[str, dict]:
    cover = config["cover"]
    variants = cover.get("variants")
    if isinstance(variants, dict):
        return variants
    return {
        "landscape": {
            "width": int(cover["width"]),
            "height": int(cover["height"]),
        }
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("plan", type=Path)
    parser.add_argument("approval", type=Path)
    parser.add_argument("config", type=Path)
    parser.add_argument("--skip-tools", action="store_true")
    args = parser.parse_args()

    errors: list[str] = []
    plan = load_json(args.plan)
    approval = load_json(args.approval)
    config = load_json(args.config)
    root = args.plan.parent
    asset_root = resolve_path(
        args.config.parent, config.get("asset_root", ".")
    )

    plan_schema = plan.get("schema_version")
    if not isinstance(plan_schema, int) or plan_schema < 1:
        errors.append("plan.schema_version must be a positive integer")
        plan_schema = 1
    production_status = plan.get("production_status")
    if plan_schema >= 2:
        if production_status not in PRODUCTION_STATUSES:
            errors.append(
                "plan.production_status must be one of: "
                + ", ".join(PRODUCTION_STATUSES)
            )
            status_index = -1
        else:
            status_index = PRODUCTION_STATUSES.index(production_status)
        provider = plan.get("narration_provider")
        if status_index >= PRODUCTION_STATUSES.index("narration_generated"):
            if provider not in {"milora_manbo", "user_supplied"}:
                errors.append(
                    "plan.narration_provider must be milora_manbo or "
                    "user_supplied after narration generation"
                )
            if not plan.get("narration_audio_path"):
                errors.append(
                    "plan.narration_audio_path is required after "
                    "narration generation"
                )
        if approval.get("schema_version") != 2:
            errors.append("schema 2 plans require a schema 2 approval file")
        if plan.get("brand_text") != config.get("brand_text"):
            errors.append("plan.brand_text must match config.brand_text")
    else:
        status_index = -1

    if approval.get("approved") is not True:
        errors.append("approval.approved is not true")
    include_intro = approval.get("include_intro", True)
    if not isinstance(include_intro, bool):
        errors.append("approval.include_intro must be true or false")
    selected_bgm_id = str(approval.get("selected_bgm_id", "A"))
    catalog_asset = config.get("audio", {}).get(
        "bgm_catalog_asset", "bgm/catalog.json"
    )
    catalog_path = resolve_path(asset_root, catalog_asset)
    if catalog_path.is_file():
        catalog = load_json(catalog_path)
        bgm_ids = {
            str(option.get("id"))
            for option in catalog.get("options", [])
            if isinstance(option, dict) and option.get("id")
        }
        if selected_bgm_id not in bgm_ids:
            errors.append(
                "approval.selected_bgm_id is not present in the BGM catalog"
            )
    elif selected_bgm_id != "A":
        errors.append("approval.selected_bgm_id requires a BGM catalog")
    intro_lines = approval.get("intro_lines")
    if (
        not isinstance(intro_lines, list)
        or not 1 <= len(intro_lines) <= 3
        or any(not isinstance(line, str) or not line.strip() for line in intro_lines)
    ):
        errors.append("approval.intro_lines must contain 1 to 3 non-empty lines")

    approved_covers = approval.get("covers")
    if approved_covers is not None:
        if not isinstance(approved_covers, dict):
            errors.append("approval.covers must be an object")
            cover_layouts: dict[str, dict] = {}
        else:
            cover_layouts = {
                str(key): value
                for key, value in approved_covers.items()
                if isinstance(value, dict)
            }
        required_variants = {"landscape", "portrait"}
        missing_variants = sorted(required_variants - set(cover_layouts))
        if missing_variants:
            errors.append(
                "approval.covers is missing variants: "
                + ", ".join(missing_variants)
            )
    elif isinstance(approval.get("cover"), dict):
        cover_layouts = {"landscape": approval["cover"]}
    else:
        cover_layouts = {}
        errors.append("approval must contain covers or legacy cover settings")

    variant_configs = configured_cover_variants(config)
    for variant_key, approved_cover in cover_layouts.items():
        variant_config = variant_configs.get(
            variant_key,
            variant_configs.get("landscape", config["cover"]),
        )
        cover_lines = approved_cover.get("title_lines")
        if (
            not isinstance(cover_lines, list)
            or not 1 <= len(cover_lines) <= 6
            or any(not isinstance(line, str) or not line.strip() for line in cover_lines)
        ):
            errors.append(
                f"approval cover {variant_key}.title_lines must contain "
                "1 to 6 non-empty lines"
            )

        accent_indices = approved_cover.get("accent_char_indices", [])
        cover_character_count = len(
            "".join(
                character
                for line in (cover_lines or [])
                for character in str(line)
                if not character.isspace()
            )
        )
        if (
            not isinstance(accent_indices, list)
            or any(
                not isinstance(value, int)
                or value < 0
                or value >= cover_character_count
                for value in accent_indices
            )
        ):
            errors.append(
                f"approval cover {variant_key}.accent_char_indices "
                "contains invalid indices"
            )

        character_scales = approved_cover.get(
            "character_size_scales",
            {},
        )
        invalid_character_scales = not isinstance(character_scales, dict)
        if isinstance(character_scales, dict):
            for raw_index, scale in character_scales.items():
                try:
                    index = int(raw_index)
                except (TypeError, ValueError):
                    invalid_character_scales = True
                    break
                if (
                    str(index) != str(raw_index)
                    or index < 0
                    or index >= cover_character_count
                    or not isinstance(scale, (int, float))
                    or not 0.5 <= float(scale) <= 2.0
                ):
                    invalid_character_scales = True
                    break
        if invalid_character_scales:
            errors.append(
                f"approval cover {variant_key}.character_size_scales "
                "contains invalid indices or scales"
            )

        width = int(variant_config["width"])
        height = int(variant_config["height"])
        numeric_bounds = {
            "text_center_x": (0, width),
            "first_line_center_y": (0, height),
            "font_size": (24, 300),
            "subject_center_x": (0, width),
            "subject_center_y": (0, height),
            "subject_scale": (0.1, 3.0),
        }
        for key, (minimum, maximum) in numeric_bounds.items():
            value = approved_cover.get(key)
            if not isinstance(value, (int, float)) or not minimum <= value <= maximum:
                errors.append(
                    f"approval cover {variant_key}.{key} must be "
                    f"between {minimum} and {maximum}"
                )
        optional_numeric_bounds = {
            "line_spacing_px": (0, 200),
            "letter_spacing_px": (-20, 80),
        }
        for key, (minimum, maximum) in optional_numeric_bounds.items():
            value = approved_cover.get(key)
            if value is None:
                continue
            if (
                not isinstance(value, (int, float))
                or not minimum <= value <= maximum
            ):
                errors.append(
                    f"approval cover {variant_key}.{key} must be "
                    f"between {minimum} and {maximum}"
                )

    scene_ids = [str(scene["id"]).zfill(2) for scene in plan.get("scenes", [])]
    approved_ids = [str(value).zfill(2) for value in approval.get("approved_scene_ids", [])]
    if sorted(scene_ids) != sorted(approved_ids):
        errors.append(
            f"approved scene IDs do not match plan: expected {scene_ids}, got {approved_ids}"
        )

    srt_path = resolve_path(root, plan.get("srt_path", ""))
    if not srt_path.exists():
        errors.append(f"SRT does not exist: {srt_path}")
        cues = []
    else:
        cues = parse_srt(srt_path)
        if not cues:
            errors.append("SRT contains no valid cues")
        punctuated = [
            str(cue.index)
            for cue in cues
            if cue.text != strip_subtitle_punctuation(cue.text)
        ]
        if punctuated:
            errors.append(
                "subtitle trailing punctuation found in cues: "
                + ", ".join(punctuated)
            )
        expected_indices = list(range(1, len(cues) + 1))
        actual_indices = [cue.index for cue in cues]
        if actual_indices != expected_indices:
            errors.append(
                "SRT cue indices must be consecutive from 1: "
                f"got {actual_indices}"
            )

    timing = config.get("timing", {})
    subtitle_profile = timing.get("subtitle_profile")
    if subtitle_profile:
        supported_profiles = {
            "privacy_reference_v1",
            "audio_aligned_v1",
            "audio_aligned_v2",
        }
        if subtitle_profile not in supported_profiles:
            errors.append(f"unsupported subtitle profile: {subtitle_profile}")
        if plan.get("subtitle_profile") != subtitle_profile:
            errors.append(
                "plan.subtitle_profile must match config timing profile: "
                f"expected {subtitle_profile!r}, "
                f"got {plan.get('subtitle_profile')!r}"
            )

        normalized_text_value = plan.get("normalized_text_path")
        subtitle_lines_value = plan.get("subtitle_lines_path")
        if not normalized_text_value:
            errors.append("plan.normalized_text_path is required")
        if not subtitle_lines_value:
            errors.append("plan.subtitle_lines_path is required")

        normalized_text_path = (
            resolve_path(root, normalized_text_value)
            if normalized_text_value
            else None
        )
        subtitle_lines_path = (
            resolve_path(root, subtitle_lines_value)
            if subtitle_lines_value
            else None
        )
        for label, path in (
            ("normalized text", normalized_text_path),
            ("subtitle lines", subtitle_lines_path),
        ):
            if path is not None and not path.exists():
                errors.append(f"{label} file missing: {path}")

        srt_text = normalize_spoken_text("".join(cue.text for cue in cues))
        if normalized_text_path is not None and normalized_text_path.exists():
            normalized_source = normalized_text_path.read_text(
                encoding="utf-8-sig"
            )
            normalized_text = normalize_spoken_text(normalized_source)
            first_paragraph = next(
                (
                    paragraph.strip()
                    for paragraph in normalized_source.replace(
                        "\r\n", "\n"
                    ).split("\n\n")
                    if paragraph.strip()
                ),
                "",
            )
            plan_title = normalize_spoken_text(str(plan.get("title", "")))
            if (
                plan_title
                and normalize_spoken_text(first_paragraph) == plan_title
            ):
                errors.append(
                    "normalized_script.txt must contain body text only; "
                    "the title is reserved for the silent intro"
                )
            if normalized_text != srt_text:
                errors.append(
                    "normalized source text does not exactly match SRT text"
                )
        if subtitle_lines_path is not None and subtitle_lines_path.exists():
            line_texts = [
                strip_subtitle_punctuation(line.strip())
                for line in subtitle_lines_path.read_text(
                    encoding="utf-8-sig"
                ).splitlines()
                if line.strip()
            ]
            if line_texts != [cue.text for cue in cues]:
                errors.append(
                    "subtitle_lines.txt does not exactly match SRT cue text"
                )

        if cues:
            fps = int(config["canvas"]["fps"])
            lead_in_frames = int(timing["lead_in_frames"])
            approved_length_outliers_raw = plan.get(
                "approved_subtitle_length_outlier_cues", []
            )
            if (
                not isinstance(approved_length_outliers_raw, list)
                or any(
                    not isinstance(value, int) or value < 1
                    for value in approved_length_outliers_raw
                )
            ):
                errors.append(
                    "plan.approved_subtitle_length_outlier_cues must be "
                    "a list of positive cue numbers"
                )
                approved_length_outliers: set[int] = set()
            else:
                approved_length_outliers = set(approved_length_outliers_raw)
            observed_length_outliers: set[int] = set()
            first_start_frame = round(cues[0].start * fps)
            if first_start_frame != lead_in_frames:
                errors.append(
                    "first subtitle frame does not match profile: "
                    f"expected {lead_in_frames}, got {first_start_frame}"
                )

            for position, cue in enumerate(cues):
                count = max(1, count_effective_hanzi(cue.text))
                if not 3 <= count <= 14:
                    observed_length_outliers.add(cue.index)
                    if cue.index not in approved_length_outliers:
                        errors.append(
                            f"cue {cue.index} has {count} effective characters; "
                            "approved range is 3-14"
                        )
                if position < len(cues) - 1:
                    risky_ending = risky_cue_ending(cue.text)
                    if risky_ending:
                        errors.append(
                            f"cue {cue.index} ends with hanging phrase "
                            f"{risky_ending!r}"
                        )
                start_frame = round(cue.start * fps)
                end_frame = round(cue.end * fps)
                if end_frame <= start_frame:
                    errors.append(
                        f"cue {cue.index} has a non-positive duration"
                    )

                if (
                    subtitle_profile == "privacy_reference_v1"
                    and end_frame > start_frame
                ):
                    base_cue_frames = int(timing["base_cue_frames"])
                    frames_per_hanzi = int(
                        timing["frames_per_effective_hanzi"]
                    )
                    minimum_cue_frames = int(
                        timing["minimum_cue_frames"]
                    )
                    expected_duration = max(
                        minimum_cue_frames,
                        base_cue_frames + count * frames_per_hanzi,
                    )
                    if end_frame - start_frame != expected_duration:
                        errors.append(
                            f"cue {cue.index} duration is "
                            f"{end_frame - start_frame} frames; "
                            f"expected {expected_duration}"
                        )

                if position < len(cues) - 1:
                    next_start = round(cues[position + 1].start * fps)
                    actual_gap = next_start - end_frame
                    if subtitle_profile in {
                        "audio_aligned_v1",
                        "audio_aligned_v2",
                    }:
                        expected_gap = 0
                    else:
                        gap_pattern = [
                            int(value)
                            for value in timing[
                                "inter_cue_gap_pattern_frames"
                            ]
                        ]
                        if not gap_pattern or any(
                            value < 0 for value in gap_pattern
                        ):
                            errors.append(
                                "timing.inter_cue_gap_pattern_frames must be "
                                "non-empty and non-negative"
                            )
                            gap_pattern = [0]
                        expected_gap = gap_pattern[
                            position % len(gap_pattern)
                        ]
                    if actual_gap != expected_gap:
                        errors.append(
                            f"gap after cue {cue.index} is {actual_gap} "
                            f"frames; expected {expected_gap}"
                        )

            unused_length_outliers = (
                approved_length_outliers - observed_length_outliers
            )
            if unused_length_outliers:
                errors.append(
                    "plan.approved_subtitle_length_outlier_cues contains "
                    "non-outlier cue numbers: "
                    + ", ".join(
                        str(value) for value in sorted(unused_length_outliers)
                    )
                )

            if subtitle_profile in {
                "audio_aligned_v1",
                "audio_aligned_v2",
            }:
                if (
                    plan_schema >= 2
                    and status_index
                    < PRODUCTION_STATUSES.index("audio_aligned")
                ):
                    errors.append(
                        "audio-aligned subtitles require production_status "
                        "audio_aligned or later"
                    )
                narration_value = plan.get("narration_audio_path")
                if not narration_value:
                    errors.append(
                        "plan.narration_audio_path is required for "
                        "audio-aligned subtitle profiles"
                    )
                else:
                    narration_path = resolve_path(root, narration_value)
                    if not narration_path.exists():
                        errors.append(
                            f"narration audio missing: {narration_path}"
                        )
                    elif shutil.which("ffprobe") is not None:
                        probe = subprocess.run(
                            [
                                "ffprobe",
                                "-v",
                                "error",
                                "-show_entries",
                                "format=duration",
                                "-of",
                                (
                                    "default=noprint_wrappers=1:"
                                    "nokey=1"
                                ),
                                str(narration_path),
                            ],
                            capture_output=True,
                            text=True,
                        )
                        if probe.returncode == 0:
                            narration_duration = float(
                                probe.stdout.strip()
                            )
                            cue_end = cues[-1].end
                            if abs(narration_duration - cue_end) > (
                                1 / fps + 0.02
                            ):
                                errors.append(
                                    "last subtitle does not end with "
                                    "narration audio: "
                                    f"subtitle={cue_end:.3f}s, "
                                    f"audio={narration_duration:.3f}s"
                                )

    should_validate_manifest = bool(plan.get("narration_manifest_path"))
    if (
        plan_schema >= 2
        and plan.get("narration_provider") == "milora_manbo"
        and status_index >= PRODUCTION_STATUSES.index("narration_generated")
    ):
        should_validate_manifest = True
    if should_validate_manifest:
        validate_narration_manifest(plan, config, root, errors)

    canvas_width = int(config["canvas"]["width"])
    subtitle_center_x = int(
        config["text_layers"]["subtitle"].get("center_x", canvas_width // 2)
    )
    if subtitle_center_x != canvas_width // 2:
        errors.append(
            "subtitle center_x must equal the canvas center: "
            f"expected {canvas_width // 2}, got {subtitle_center_x}"
        )

    coverage: dict[int, list[str]] = {cue.index: [] for cue in cues}
    previous_end = 0
    for scene in plan.get("scenes", []):
        scene_id = str(scene["id"]).zfill(2)
        start, end = int(scene["cue_start"]), int(scene["cue_end"])
        if start > end:
            errors.append(f"scene {scene_id} has reversed cue range")
        if start < previous_end:
            errors.append(f"scene {scene_id} overlaps an earlier cue range")
        previous_end = end
        image_path = resolve_path(root, scene.get("image_path", ""))
        if not image_path.exists():
            errors.append(f"scene {scene_id} image missing: {image_path}")
        image_scale = scene.get("image_scale", 1.0)
        if (
            isinstance(image_scale, bool)
            or not isinstance(image_scale, (int, float))
            or not 0.5 <= image_scale <= 1.6
        ):
            errors.append(
                f"scene {scene_id} image_scale must be between 0.5 and 1.6"
            )
        for offset_key in ("image_offset_x_px", "image_offset_y_px"):
            offset_value = scene.get(offset_key, 0)
            if (
                isinstance(offset_value, bool)
                or not isinstance(offset_value, (int, float))
                or not -400 <= offset_value <= 400
            ):
                errors.append(
                    f"scene {scene_id} {offset_key} must be between -400 and 400"
                )
        for cue_index in range(start, end + 1):
            if cue_index in coverage:
                coverage[cue_index].append(scene_id)
            else:
                errors.append(f"scene {scene_id} references missing cue {cue_index}")

    missing = [str(index) for index, owners in coverage.items() if not owners]
    duplicate = [str(index) for index, owners in coverage.items() if len(owners) > 1]
    if missing:
        errors.append("uncovered cues: " + ", ".join(missing))
    if duplicate:
        errors.append("multiply covered cues: " + ", ".join(duplicate))

    background = resolve_path(
        asset_root, config["canvas"]["background_asset"]
    )
    font = resolve_path(asset_root, config["font"]["asset"])
    bgm = resolve_path(asset_root, config["audio"]["bgm_asset"])
    configured_left = config.get("text_layers", {}).get("top_left", {})
    if not isinstance(configured_left, dict):
        configured_left = {}
    left_brand = {
        "text_source": "plan.title",
        "avatar_asset": "brand-avatar.png",
        "pixel_font_size": 31,
        **configured_left,
    }
    if left_brand.get("text_source") != "plan.title":
        errors.append("top-left header text_source must be plan.title")
    brand_avatar = resolve_path(asset_root, left_brand["avatar_asset"])
    for label, path in (
        ("background", background),
        ("font", font),
        ("bgm", bgm),
        ("brand avatar", brand_avatar),
    ):
        if not path.exists():
            errors.append(f"{label} asset missing: {path}")

    if background.exists():
        actual = rgb_to_hex(sample_background_rgb(background))
        expected = config["canvas"].get("expected_background_hex", "").upper()
        if expected and actual.upper() != expected:
            errors.append(f"background is {actual}, expected {expected}")

    if not args.skip_tools:
        for tool in ("ffmpeg", "ffprobe"):
            if shutil.which(tool) is None:
                errors.append(f"required tool not found: {tool}")

    if errors:
        print("Project validation failed:")
        for error in errors:
            print(f"- {error}")
        return 1
    print(
        f"Project valid: {len(cues)} cues, {len(scene_ids)} scenes, "
        "left header=plan.title"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
