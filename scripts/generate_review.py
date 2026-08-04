#!/usr/bin/env python3
"""Generate a self-contained interactive review page and approval template."""

from __future__ import annotations

import argparse
import base64
import html
import json
import mimetypes
from io import BytesIO
from pathlib import Path

from PIL import Image

from common import (
    load_json,
    parse_srt,
    resolve_path,
    rgb_to_hex,
    sample_background_rgb,
    write_json,
)
from normalize_background import connected_background_mask, median_border_rgb


def data_uri(path: Path, mime: str | None = None) -> str:
    media_type = mime or mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{media_type};base64,{encoded}"


def preview_image_data_uri(path: Path, max_size: tuple[int, int] = (960, 720)) -> str:
    image = Image.open(path).convert("RGBA")
    image.thumbnail(max_size, Image.Resampling.LANCZOS)
    buffer = BytesIO()
    image.save(buffer, format="WEBP", quality=84, method=6)
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/webp;base64,{encoded}"


def cover_subject_data_uri(path: Path) -> str:
    image = Image.open(path).convert("RGBA")
    if image.getchannel("A").getextrema() == (255, 255):
        external = connected_background_mask(image, median_border_rgb(image), 42)
        image.putalpha(external.point(lambda value: 255 - value))
    bbox = image.getchannel("A").getbbox()
    if bbox:
        image = image.crop(bbox)
    image.thumbnail((1200, 1000), Image.Resampling.LANCZOS)
    buffer = BytesIO()
    image.save(buffer, format="WEBP", quality=88, method=6)
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/webp;base64,{encoded}"


def normalized_text(value: str) -> str:
    return "".join(value.split())


def character_size_scales(value: object) -> dict[str, float]:
    if not isinstance(value, dict):
        return {}
    result: dict[str, float] = {}
    for raw_index, raw_scale in value.items():
        try:
            index = int(raw_index)
            scale = float(raw_scale)
        except (TypeError, ValueError):
            continue
        if index >= 0 and 0.5 <= scale <= 2.0:
            result[str(index)] = round(scale, 2)
    return result


def cover_variants(config: dict) -> dict[str, dict]:
    cover = config["cover"]
    variants = cover.get("variants")
    if isinstance(variants, dict) and {"landscape", "portrait"} <= set(variants):
        return variants
    return {
        "landscape": {
            "label": "横版 4:3",
            "width": int(cover["width"]),
            "height": int(cover["height"]),
            "font_size": int(cover["font_size"]),
            "line_spacing_px": int(cover["line_spacing_px"]),
            "first_line_center_y": int(cover["first_line_center_y"]),
            "text_center_x": int(cover.get("text_center_x", cover["width"] // 2)),
            "image_area": dict(cover["image_area"]),
        },
        "portrait": {
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
        },
    }


def auto_break_lines(text: str, max_characters: int) -> list[str]:
    source_lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not source_lines:
        return [text.strip()] if text.strip() else [""]
    result: list[str] = []
    break_characters = "，。！？；：、"
    for source_line in source_lines:
        remaining = source_line
        while len(remaining) > max_characters:
            window = remaining[: max_characters + 1]
            candidates = [
                index + 1
                for index, character in enumerate(window)
                if character in break_characters
                and index + 1 >= max(2, max_characters // 2)
            ]
            split_at = candidates[-1] if candidates else max_characters
            result.append(remaining[:split_at].strip())
            remaining = remaining[split_at:].strip()
        if remaining:
            result.append(remaining)
    if len(result) > 6:
        result = result[:5] + ["".join(result[5:])]
    return result


def plan_cover_lines(
    plan: dict,
    accent_hex: str,
) -> tuple[list[str], list[int]]:
    values = plan.get("cover", {}).get("title_lines") or []
    lines = [
        str(value.get("text", "")) if isinstance(value, dict) else str(value)
        for value in values
    ]
    lines = [line for line in lines if line.strip()] or [
        str(value) for value in (plan.get("intro_lines") or [plan.get("title", "")])
    ]
    accent_indices: list[int] = []
    offset = 0
    for value, text in zip(values, lines):
        if (
            isinstance(value, dict)
            and str(value.get("color", "")).upper() == accent_hex.upper()
        ):
            accent_indices.extend(
                offset + index
                for index, character in enumerate(
                    character for character in text if not character.isspace()
                )
            )
        offset += sum(not character.isspace() for character in text)
    return lines, accent_indices


def layout_seed(
    *,
    key: str,
    variant: dict,
    source_text: str,
    default_accent_indices: list[int],
    approval: dict,
    legacy_cover_config: dict,
) -> dict:
    width, height = int(variant["width"]), int(variant["height"])
    area = variant["image_area"]
    approved_covers = approval.get("covers")
    approved_layout = (
        approved_covers.get(key, {})
        if isinstance(approved_covers, dict)
        and isinstance(approved_covers.get(key), dict)
        else {}
    )
    legacy_layout = (
        approval.get("cover", {})
        if isinstance(approval.get("cover"), dict)
        else {}
    )

    if approved_layout:
        lines = [
            str(line)
            for line in approved_layout.get("title_lines", [])
            if str(line).strip()
        ] or auto_break_lines(source_text, 16 if key == "landscape" else 9)
        return {
            "label": str(variant.get("label", key)),
            "width": width,
            "height": height,
            "titleLines": lines,
            "accentCharIndices": [
                int(value)
                for value in approved_layout.get("accent_char_indices", [])
            ],
            "textCenterX": int(
                approved_layout.get(
                    "text_center_x",
                    variant.get("text_center_x", width // 2),
                )
            ),
            "firstLineCenterY": int(
                approved_layout.get(
                    "first_line_center_y",
                    variant["first_line_center_y"],
                )
            ),
            "fontSize": int(
                approved_layout.get("font_size", variant["font_size"])
            ),
            "lineSpacing": int(
                approved_layout.get(
                    "line_spacing_px",
                    variant["line_spacing_px"],
                )
            ),
            "letterSpacing": int(
                approved_layout.get("letter_spacing_px", 0)
            ),
            "characterSizeScales": character_size_scales(
                approved_layout.get("character_size_scales", {})
            ),
            "subjectCenterX": int(
                approved_layout.get(
                    "subject_center_x",
                    (int(area["left"]) + int(area["right"])) // 2,
                )
            ),
            "subjectCenterY": int(
                approved_layout.get(
                    "subject_center_y",
                    (int(area["top"]) + int(area["bottom"])) // 2,
                )
            ),
            "subjectScale": float(approved_layout.get("subject_scale", 1.0)),
            "subjectBaseWidth": int(area["right"] - area["left"]),
            "subjectBaseHeight": int(area["bottom"] - area["top"]),
        }

    legacy_lines = [
        str(line)
        for line in legacy_layout.get("title_lines", [])
        if str(line).strip()
    ]
    text_for_layout = "".join(legacy_lines) if legacy_lines else source_text
    lines = auto_break_lines(text_for_layout, 16 if key == "landscape" else 9)
    if key == "landscape" and legacy_layout:
        old_width = int(legacy_cover_config.get("width", width))
        old_height = int(legacy_cover_config.get("height", height))
        scale_x = width / max(1, old_width)
        scale_y = height / max(1, old_height)
        font_scale = min(scale_x, scale_y)
        return {
            "label": str(variant.get("label", key)),
            "width": width,
            "height": height,
            "titleLines": lines,
            "accentCharIndices": [
                int(value)
                for value in legacy_layout.get(
                    "accent_char_indices", default_accent_indices
                )
            ],
            "textCenterX": int(
                round(
                    float(
                        legacy_layout.get("text_center_x", old_width // 2)
                    )
                    * scale_x
                )
            ),
            "firstLineCenterY": int(
                round(
                    float(
                        legacy_layout.get(
                            "first_line_center_y",
                            legacy_cover_config.get("first_line_center_y", 270),
                        )
                    )
                    * scale_y
                )
            ),
            "fontSize": int(
                round(
                    float(
                        legacy_layout.get(
                            "font_size",
                            legacy_cover_config.get("font_size", 84),
                        )
                    )
                    * font_scale
                )
            ),
            "lineSpacing": int(
                legacy_layout.get(
                    "line_spacing_px",
                    variant["line_spacing_px"],
                )
            ),
            "letterSpacing": int(
                legacy_layout.get("letter_spacing_px", 0)
            ),
            "characterSizeScales": character_size_scales(
                legacy_layout.get("character_size_scales", {})
            ),
            "subjectCenterX": int(
                round(
                    float(
                        legacy_layout.get("subject_center_x", old_width // 2)
                    )
                    * scale_x
                )
            ),
            "subjectCenterY": int(
                round(
                    float(
                        legacy_layout.get(
                            "subject_center_y", int(old_height * 0.72)
                        )
                    )
                    * scale_y
                )
            ),
            "subjectScale": float(legacy_layout.get("subject_scale", 1.0)),
            "subjectBaseWidth": int(area["right"] - area["left"]),
            "subjectBaseHeight": int(area["bottom"] - area["top"]),
        }

    return {
        "label": str(variant.get("label", key)),
        "width": width,
        "height": height,
        "titleLines": lines,
        "accentCharIndices": [
            int(value)
            for value in legacy_layout.get(
                "accent_char_indices", default_accent_indices
            )
        ],
        "textCenterX": int(variant.get("text_center_x", width // 2)),
        "firstLineCenterY": int(variant["first_line_center_y"]),
        "fontSize": int(variant["font_size"]),
        "lineSpacing": int(variant["line_spacing_px"]),
        "letterSpacing": 0,
        "characterSizeScales": {},
        "subjectCenterX": (int(area["left"]) + int(area["right"])) // 2,
        "subjectCenterY": (int(area["top"]) + int(area["bottom"])) // 2,
        "subjectScale": 1.0,
        "subjectBaseWidth": int(area["right"] - area["left"]),
        "subjectBaseHeight": int(area["bottom"] - area["top"]),
    }


def review_bgm_options(
    config: dict,
    asset_root: Path,
    approval: dict,
) -> tuple[list[dict], str]:
    audio = config.get("audio", {})
    catalog_path = resolve_path(
        asset_root,
        audio.get("bgm_catalog_asset", "bgm/catalog.json"),
    )
    if not catalog_path.is_file():
        return [
            {
                "id": "A",
                "label": "选项 A",
                "name": "原版 BGM",
                "previewSrc": "",
                "durationSeconds": None,
            }
        ], "A"

    catalog = load_json(catalog_path)
    options: list[dict] = []
    for raw_option in catalog.get("options", []):
        if not isinstance(raw_option, dict) or not raw_option.get("id"):
            continue
        preview_path = resolve_path(
            catalog_path.parent,
            raw_option.get("preview_asset", ""),
        )
        options.append(
            {
                "id": str(raw_option["id"]),
                "label": str(raw_option.get("label") or raw_option["id"]),
                "name": str(raw_option.get("name") or "背景音乐"),
                "previewSrc": data_uri(preview_path, "audio/mpeg")
                if preview_path.is_file()
                else "",
                "durationSeconds": raw_option.get("duration_seconds"),
            }
        )
    if not options:
        raise SystemExit(f"BGM 曲库没有可用选项：{catalog_path}")
    available = {option["id"] for option in options}
    selected = str(
        approval.get("selected_bgm_id")
        or audio.get("default_bgm_id")
        or catalog.get("default_id")
        or options[0]["id"]
    )
    if selected not in available:
        selected = str(catalog.get("default_id") or options[0]["id"])
    return options, selected


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("plan", type=Path)
    parser.add_argument("output_html", type=Path)
    parser.add_argument("--approval-template", type=Path)
    parser.add_argument("--config", type=Path)
    args = parser.parse_args()

    plan = load_json(args.plan)
    root = args.plan.parent
    config_path = args.config or root / "config.json"
    if not config_path.exists():
        raise SystemExit(
            "Review page needs config.json; pass it with --config when it is elsewhere"
        )
    config = load_json(config_path)
    assets = resolve_path(config_path.parent, config.get("asset_root", "."))
    cues = {
        cue.index: cue
        for cue in parse_srt(resolve_path(root, plan["srt_path"]))
    }

    scene_ids: list[str] = []
    cards: list[str] = []
    for scene in plan.get("scenes", []):
        scene_id = str(scene["id"]).zfill(2)
        scene_ids.append(scene_id)
        image_path = resolve_path(root, scene["image_path"])
        image_html = (
            f'<img src="{preview_image_data_uri(image_path)}" alt="图{scene_id}">'
            if image_path.exists()
            else '<div class="missing">图片尚未生成</div>'
        )
        cue_start, cue_end = int(scene["cue_start"]), int(scene["cue_end"])
        text = "<br>".join(
            html.escape(cues[index].text)
            for index in range(cue_start, cue_end + 1)
            if index in cues
        )
        refs = "、".join(scene.get("reference_ids", [])) or "待记录"
        cards.append(
            f"""
            <article class="scene-card">
              <div class="scene-visual">{image_html}</div>
              <div class="scene-body">
                <div class="scene-meta">
                  <strong>图{scene_id}</strong>
                  <span>字幕 {cue_start}—{cue_end}</span>
                </div>
                <h3>{html.escape(scene.get("core_relation", "未填写核心关系"))}</h3>
                <p>{text}</p>
                <details>
                  <summary>查看画面说明</summary>
                  <dl>
                    <dt>动作</dt><dd>{html.escape(scene.get("subjects_and_action", ""))}</dd>
                    <dt>符号</dt><dd>{html.escape("、".join(scene.get("symbols", [])))}</dd>
                    <dt>参考</dt><dd>{html.escape(refs)}</dd>
                  </dl>
                </details>
              </div>
            </article>
            """
        )

    existing_approval_path = root / "approval.json"
    existing_approval = (
        load_json(existing_approval_path)
        if existing_approval_path.exists()
        else {}
    )
    bgm_options, selected_bgm_id = review_bgm_options(
        config,
        assets,
        existing_approval,
    )
    include_intro_value = existing_approval.get("include_intro", True)
    include_intro = (
        include_intro_value if isinstance(include_intro_value, bool) else True
    )
    intro_lines = existing_approval.get("intro_lines") or plan.get(
        "intro_lines"
    ) or [plan.get("title", "")]
    plan_lines, plan_accent_indices = plan_cover_lines(
        plan,
        config["colors"]["accent"],
    )
    legacy_lines = (
        existing_approval.get("cover", {}).get("title_lines", [])
        if isinstance(existing_approval.get("cover"), dict)
        else []
    )
    source_cover_text = "".join(
        str(line) for line in (legacy_lines or plan_lines)
    )

    cover_image_path = resolve_path(
        root, plan.get("cover", {}).get("image_path", "")
    )
    cover_image_src = (
        cover_subject_data_uri(cover_image_path)
        if cover_image_path.exists()
        else ""
    )
    background_path = resolve_path(
        assets, config["canvas"]["background_asset"]
    )
    measured_background = rgb_to_hex(sample_background_rgb(background_path))
    font_path = resolve_path(assets, config["font"]["asset"])
    # Cover copy is intentionally editable, so embed the complete font.
    font_src = data_uri(font_path, "font/otf")
    variants = cover_variants(config)
    cover_payload = {
        key: {
            **layout_seed(
                key=key,
                variant=variant,
                source_text=source_cover_text,
                default_accent_indices=plan_accent_indices,
                approval=existing_approval,
                legacy_cover_config=config["cover"],
            ),
            "imageSrc": cover_image_src,
            "background": measured_background,
        }
        for key, variant in variants.items()
        if key in {"landscape", "portrait"}
    }
    existing_approved_ids = {
        str(value).zfill(2)
        for value in existing_approval.get("approved_scene_ids", [])
    }
    scenes_approved = (
        existing_approval.get("approved") is True
        and existing_approved_ids == set(scene_ids)
    )
    payload = {
        "title": plan.get("title", ""),
        "introTextNormalized": normalized_text(plan.get("title", "")),
        "introLines": intro_lines,
        "brandText": plan.get("brand_text", config.get("brand_text", "")),
        "covers": cover_payload,
        "colors": {
            "ink": config["colors"]["ink"],
            "accent": config["colors"]["accent"],
        },
        "expectedSceneIds": scene_ids,
        "scenesApproved": scenes_approved,
        "bgmOptions": bgm_options,
        "selectedBgmId": selected_bgm_id,
        "includeIntro": include_intro,
    }
    payload_json = json.dumps(payload, ensure_ascii=False).replace("<", "\\u003c")

    template = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>__TITLE__｜最终确认</title>
<style>
@font-face{font-family:"ReviewSerif";src:url("__FONT_SRC__") format("opentype");font-weight:700;font-style:normal;font-display:swap}
:root{--paper:#f7f3eb;--ink:#171614;--muted:#756f66;--accent:#b53316;--panel:#fffdfa;--line:#d9d1c5;--soft:#eee7dc;--success:#1f6b48}
*{box-sizing:border-box}
html{scroll-behavior:smooth}
body{margin:0;background:var(--paper);color:var(--ink);font-family:Inter,"PingFang SC","Microsoft YaHei",sans-serif}
button,input,textarea{font:inherit}
button{cursor:pointer}
.shell{max-width:1660px;margin:auto;padding:26px 28px 84px}
.page-head{display:flex;align-items:flex-end;justify-content:space-between;gap:24px;padding:8px 0 22px;border-bottom:1px solid var(--line)}
.page-head h1{font-family:ReviewSerif,serif;font-size:32px;margin:0 0 6px;line-height:1.25}
.page-head p{margin:0;color:var(--muted)}
.status{white-space:nowrap;padding:10px 14px;border:1px solid var(--line);background:var(--panel);border-radius:10px;color:var(--muted)}
.workspace{display:grid;grid-template-columns:minmax(720px,1.45fr) minmax(330px,.55fr);gap:24px;align-items:start;margin-top:24px}
.preview-column{position:sticky;top:16px}
.panel{background:rgba(255,253,250,.88);border:1px solid var(--line);border-radius:16px}
.panel-head{padding:16px 18px;border-bottom:1px solid var(--line);display:flex;align-items:center;justify-content:space-between;gap:12px}
.panel-head h2{font-size:17px;margin:0}
.tabs{display:flex;gap:6px}
.tab{border:1px solid transparent;background:transparent;color:var(--muted);padding:7px 11px;border-radius:8px}
.tab.active{background:var(--ink);color:white}
.preview-wrap{padding:18px}
.cover-help{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:12px;color:var(--muted);font-size:13px}
.cover-help strong{color:var(--ink)}
.cover-grid{display:grid;grid-template-columns:minmax(0,1.36fr) minmax(280px,.74fr);gap:14px;align-items:start}
.cover-card{min-width:0}
.cover-card-head{display:flex;align-items:center;justify-content:space-between;gap:10px;margin-bottom:8px}
.cover-card-title{display:flex;align-items:baseline;gap:8px}
.cover-card-title strong{font-size:14px}
.cover-card-title span{font-size:11px;color:var(--muted)}
.canvas-actions{display:flex;gap:5px;flex-wrap:wrap;justify-content:flex-end}
.canvas-action{border:1px solid var(--line);background:var(--panel);border-radius:7px;padding:5px 8px;font-size:11px;color:var(--ink)}
.canvas-action:hover{border-color:var(--accent)}
.type-tools{display:flex;align-items:center;justify-content:flex-end;gap:7px;flex-wrap:wrap;margin:0 0 9px;padding:7px 8px;background:rgba(238,231,220,.58);border:1px solid var(--line);border-radius:9px}
.type-control{display:flex;align-items:center;gap:4px;min-width:0}
.type-control-label{font-size:10px;color:var(--muted);white-space:nowrap}
.type-button{min-width:27px;height:25px;border:1px solid var(--line);background:var(--panel);color:var(--ink);border-radius:6px;padding:0 7px;font-size:11px;line-height:1}
.type-button:hover{border-color:var(--accent)}
.type-value{min-width:38px;text-align:center;font-size:10px;font-variant-numeric:tabular-nums;color:var(--ink)}
.selection-tools .type-value{min-width:44px}
.cover-stage{position:relative;width:100%;overflow:hidden;background:var(--paper);border:1px solid var(--line);box-shadow:0 14px 38px rgba(50,38,21,.08);touch-action:none;user-select:none}
.cover-stage:focus-within{border-color:rgba(181,51,22,.55)}
.cover-layer{position:absolute}
.cover-layer.selected{outline:2px solid var(--accent);outline-offset:3px}
.subject-layer{transform:translate(-50%,-50%);z-index:1;cursor:move}
.subject-layer img{width:100%;height:100%;display:block;object-fit:contain;pointer-events:none;-webkit-user-drag:none}
.title-layer{transform:translateX(-50%);z-index:3;width:max-content;min-width:90px}
.title-editor{font-family:ReviewSerif,serif;font-weight:700;white-space:pre;text-align:center;line-height:1;outline:0;cursor:text;user-select:text;min-width:90px;caret-color:var(--accent)}
.title-editor span{display:inline-block;vertical-align:middle;line-height:1}
.title-editor span.accent{color:var(--accent)}
.title-editor span.selection-preview{background:#dce7ff;box-shadow:0 0 0 1px rgba(55,93,170,.18)}
.move-handle{display:none;position:absolute;left:50%;top:-29px;transform:translateX(-50%);border:0;background:var(--ink);color:white;border-radius:999px;padding:4px 9px;font-size:10px;white-space:nowrap;cursor:move}
.resize-handle{display:none;position:absolute;right:-13px;bottom:-13px;width:26px;height:26px;border:2px solid white;background:var(--accent);color:white;border-radius:50%;align-items:center;justify-content:center;font-size:12px;box-shadow:0 2px 8px rgba(0,0,0,.2);cursor:nwse-resize}
.edge-handle{display:none;position:absolute;width:15px;height:15px;border:2px solid var(--accent);background:white;border-radius:50%;padding:0;box-shadow:0 2px 7px rgba(0,0,0,.16)}
.line-spacing-handle{left:50%;bottom:0;transform:translate(-50%,50%);cursor:ns-resize}
.letter-spacing-handle{right:0;top:50%;transform:translate(50%,-50%);cursor:ew-resize}
.cover-layer.selected .move-handle{display:block}
.cover-layer.selected .resize-handle{display:flex}
.cover-layer.selected .edge-handle{display:block}
.cover-card-foot{min-height:34px;padding:7px 2px 0;color:var(--muted);font-size:11px;line-height:1.45}
.cover-error{color:#a21d13}
.intro-stage{display:flex;aspect-ratio:4/3;align-items:center;justify-content:center;padding:8%;background:var(--paper);border:1px solid var(--line);font-family:ReviewSerif,serif;color:#000;text-align:center;font-size:clamp(26px,4vw,52px);line-height:1.38;white-space:pre-line}
[hidden]{display:none!important}
.fixed-note{padding:11px 18px 16px;color:var(--muted);font-size:13px}
.editors{display:grid;gap:14px}
.editor{background:var(--panel);border:1px solid var(--line);border-radius:16px;overflow:hidden}
.editor summary{list-style:none;padding:17px 18px;font-weight:700;display:flex;align-items:center;justify-content:space-between;cursor:pointer}
.editor summary::-webkit-details-marker{display:none}
.editor summary::after{content:"＋";color:var(--muted);font-size:20px;font-weight:400}
.editor[open] summary::after{content:"－"}
.editor-body{padding:0 18px 18px;border-top:1px solid var(--line)}
.field{margin-top:16px}
.field label{display:block;font-size:13px;font-weight:700;margin-bottom:7px}
.hint{font-size:12px;color:var(--muted);margin:6px 0 0;line-height:1.55}
input[type=text],textarea{width:100%;border:1px solid var(--line);background:#fff;padding:10px 11px;border-radius:9px;color:var(--ink);outline:none}
input:focus,textarea:focus{border-color:var(--accent);box-shadow:0 0 0 3px rgba(181,51,22,.1)}
textarea{resize:vertical;line-height:1.55;min-height:76px}
.candidate-row{display:flex;flex-wrap:wrap;gap:7px;margin-top:9px}
.candidate{border:1px solid var(--line);background:var(--paper);border-radius:999px;padding:6px 10px;font-size:12px}
.candidate:hover{border-color:var(--accent)}
.bgm-options{display:grid;gap:9px;margin-top:14px}
.bgm-choice{display:grid;grid-template-columns:22px minmax(0,1fr);gap:10px;padding:12px;border:1px solid var(--line);border-radius:11px;background:var(--paper);transition:border-color .16s ease,box-shadow .16s ease}
.bgm-choice.selected{border-color:var(--accent);box-shadow:0 0 0 2px rgba(181,51,22,.1)}
.bgm-choice input{width:18px;height:18px;margin:2px 0 0;accent-color:var(--accent)}
.bgm-choice-head{display:flex;align-items:baseline;justify-content:space-between;gap:12px;margin-bottom:8px}
.bgm-choice-head strong{font-size:14px}.bgm-choice-head span{font-size:12px;color:var(--muted)}
.bgm-choice audio{display:block;width:100%;height:36px}
.bgm-no-preview{color:var(--muted);font-size:12px;padding:6px 0}
.intro-mode-options{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:9px;margin-top:14px}
.intro-mode-choice{display:grid;grid-template-columns:22px minmax(0,1fr);gap:10px;padding:12px;border:1px solid var(--line);border-radius:11px;background:var(--paper);transition:border-color .16s ease,box-shadow .16s ease}
.intro-mode-choice.selected{border-color:var(--accent);box-shadow:0 0 0 2px rgba(181,51,22,.1)}
.intro-mode-choice input{width:18px;height:18px;margin:2px 0 0;accent-color:var(--accent)}
.intro-mode-choice strong{display:block;font-size:14px;margin-bottom:4px}
.intro-mode-choice span{display:block;color:var(--muted);font-size:12px;line-height:1.5}
.editor.disabled .editor-body{opacity:.55}
.editor.disabled textarea{cursor:not-allowed}
.error{color:#a21d13;font-size:12px;margin-top:7px;min-height:18px}
.approval{margin-top:0;padding:18px;background:var(--ink);color:white;border-radius:16px}
.check{display:flex;align-items:flex-start;gap:10px;line-height:1.45}
.check input{margin-top:3px;width:18px;height:18px;accent-color:var(--accent)}
.primary{width:100%;border:0;background:var(--accent);color:white;padding:13px 16px;border-radius:10px;font-weight:800;margin-top:14px}
.primary:disabled{opacity:.42;cursor:not-allowed}
.copy-button{width:100%;border:1px solid rgba(255,255,255,.28);background:transparent;color:white;padding:10px 14px;border-radius:10px;margin-top:8px;transition:transform .12s ease,background .2s ease,border-color .2s ease,color .2s ease}
.copy-button:not(:disabled):hover{border-color:white}
.copy-button:not(:disabled):active{transform:translateY(2px) scale(.985)}
.copy-button.success{background:var(--success);border-color:var(--success);animation:copy-pop .34s ease}
.copy-button.failure{background:#8e261d;border-color:#b84c42;animation:copy-shake .28s ease}
@keyframes copy-pop{0%{transform:scale(.97)}55%{transform:scale(1.025)}100%{transform:scale(1)}}
@keyframes copy-shake{0%,100%{transform:translateX(0)}33%{transform:translateX(-4px)}66%{transform:translateX(4px)}}
.toast{position:fixed;left:50%;bottom:28px;z-index:20;transform:translate(-50%,18px);opacity:0;pointer-events:none;background:var(--ink);color:white;border-radius:999px;padding:10px 16px;font-size:13px;box-shadow:0 10px 28px rgba(0,0,0,.2);transition:opacity .2s ease,transform .2s ease}
.toast.show{opacity:1;transform:translate(-50%,0)}
.toast.success{background:var(--success)}
.toast.failure{background:#8e261d}
.scenes{margin-top:36px;padding-top:26px;border-top:1px solid var(--line)}
.scenes-head{display:flex;justify-content:space-between;align-items:flex-end;gap:16px;margin-bottom:16px}
.scenes h2{font-family:ReviewSerif,serif;font-size:26px;margin:0}
.scenes-head p{color:var(--muted);margin:0}
.scene-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:16px}
.scene-card{background:var(--panel);border:1px solid var(--line);border-radius:14px;overflow:hidden}
.scene-visual{height:250px;background:var(--paper);display:flex;align-items:center;justify-content:center;padding:12px}
.scene-visual img{max-width:100%;max-height:100%;object-fit:contain}
.scene-body{padding:15px}
.scene-meta{display:flex;justify-content:space-between;color:var(--muted);font-size:12px}
.scene-meta strong{color:var(--ink);font-size:15px}
.scene-card h3{font-size:15px;margin:11px 0 7px}
.scene-card p{font-family:ReviewSerif,serif;line-height:1.6;margin:0;font-size:14px}
.scene-card details{margin-top:10px;color:var(--muted);font-size:12px}
.scene-card summary{cursor:pointer}
dl{display:grid;grid-template-columns:42px 1fr;gap:5px;margin:9px 0 0}dd{margin:0}
.missing{color:var(--accent)}
@media(max-width:1180px){.workspace{grid-template-columns:1fr}.preview-column{position:relative;top:0}}
@media(max-width:760px){.shell{padding:18px 14px 64px}.page-head{display:block}.status{display:inline-block;margin-top:14px}.preview-wrap{padding:10px}.cover-grid{grid-template-columns:1fr}.scene-grid{grid-template-columns:1fr}.scene-visual{height:220px}}
</style>
</head>
<body>
<main class="shell">
  <header class="page-head">
    <div><h1>__TITLE__</h1><p>最终确认页 · 在封面画布内直接编辑，再导出确认文件</p></div>
    <div class="status" id="saveStatus">修改会自动保存在本页</div>
  </header>

  <section class="workspace">
    <div class="preview-column panel">
      <div class="panel-head">
        <h2>即时预览</h2>
        <div class="tabs">
          <button class="tab active" data-tab="cover">双尺寸封面</button>
          <button class="tab" data-tab="intro">片头</button>
        </div>
      </div>
      <div class="preview-wrap">
        <div id="coverPanel">
          <div class="cover-help">
            <strong>直接在图上调整</strong>
            <span>右下角等比缩放；底边中点调行距；右边中点调字距；点击画布空白处进入纯预览。</span>
          </div>
          <div class="cover-grid" id="coverGrid"></div>
        </div>
        <div class="intro-stage" id="introStage" hidden></div>
      </div>
      <div class="fixed-note">正文左上角固定显示头像与本期主标题，文字字号与右上角品牌语一致；该栏随本期主标题自动更新。</div>
    </div>

    <div class="editors">
      <details class="editor" open>
        <summary>1. 正文背景音乐</summary>
        <div class="editor-body">
          <div class="bgm-options" id="bgmOptions"></div>
          <p class="hint">试听片段只用于选择；最终压制使用对应的完整原始音频。保留片头时，前 3.933 秒声音固定保持原版；不要片头时，所选 BGM 与正文从 0 秒同步开始。</p>
        </div>
      </details>

      <details class="editor" open>
        <summary>2. 是否保留片头</summary>
        <div class="editor-body">
          <div class="intro-mode-options" id="introModeOptions">
            <label class="intro-mode-choice" data-intro-mode="include">
              <input type="radio" name="introMode" value="include">
              <div><strong>保留片头</strong><span>保留前 3.933 秒标题动画和原片头声音，随后进入正文。</span></div>
            </label>
            <label class="intro-mode-choice" data-intro-mode="omit">
              <input type="radio" name="introMode" value="omit">
              <div><strong>不要片头</strong><span>移除前 3.933 秒，正文画面、朗读和所选 BGM 从 0 秒同步开始。</span></div>
            </label>
          </div>
        </div>
      </details>

      <details class="editor" id="introEditor" open>
        <summary>3. 片头标题内容与分行</summary>
        <div class="editor-body">
          <div class="field">
            <label for="introInput">直接修改文字，按回车调整为 1—3 行</label>
            <textarea id="introInput" rows="3"></textarea>
            <p class="hint">片头文字和换行都可以修改，保持 1—3 个非空行。</p>
            <div class="error" id="introError"></div>
          </div>
        </div>
      </details>

      <div class="approval">
        <label class="check"><input id="sceneApproval" type="checkbox"><span>我已检查下方全部配图、字幕对应关系和两张封面，确认通过。</span></label>
        <button class="primary" id="downloadApproval" disabled>确认并下载 approval.json</button>
        <button class="copy-button" id="copyApproval" disabled>复制确认数据</button>
      </div>
    </div>
  </section>

  <section class="scenes">
    <div class="scenes-head"><div><h2>图片—字幕对应关系</h2><p>本区只用于检查，不在此修改图片。</p></div><strong>共 __SCENE_COUNT__ 张</strong></div>
    <div class="scene-grid">__SCENE_CARDS__</div>
  </section>
</main>
<div class="toast" id="toast" role="status" aria-live="polite"></div>
<script id="reviewData" type="application/json">__PAYLOAD__</script>
<script>
const seed=JSON.parse(document.getElementById("reviewData").textContent);
const storageKey="frameflow-review-v2:"+seed.title;
const $=id=>document.getElementById(id);
const compact=value=>value.replace(/\\s/g,"");
const clamp=(value,min,max)=>Math.min(max,Math.max(min,Number(value)));
const deepCopy=value=>JSON.parse(JSON.stringify(value));
const coverDefaults=Object.fromEntries(Object.entries(seed.covers).map(([key,value])=>[key,{
  coverText:value.titleLines.join("\\n"),
  accentIndices:[...value.accentCharIndices],
  textX:value.textCenterX,
  textY:value.firstLineCenterY,
  fontSize:value.fontSize,
  lineSpacing:value.lineSpacing,
  letterSpacing:value.letterSpacing,
  characterSizeScales:{...(value.characterSizeScales||{})},
  subjectX:value.subjectCenterX,
  subjectY:value.subjectCenterY,
  subjectScale:value.subjectScale
}]));
const defaults={
  introText:seed.introLines.join("\\n"),
  includeIntro:seed.includeIntro,
  covers:coverDefaults,
  scenesApproved:seed.scenesApproved,
  selectedBgmId:seed.selectedBgmId
};
let stored={};
try{stored=JSON.parse(localStorage.getItem(storageKey)||"{}")}catch(error){}
let state={
  ...defaults,
  ...stored,
  covers:Object.fromEntries(Object.keys(seed.covers).map(key=>[
    key,
    {...coverDefaults[key],...((stored.covers||{})[key]||{})}
  ]))
};
state.includeIntro=state.includeIntro!==false;
if(!seed.bgmOptions.some(option=>option.id===state.selectedBgmId)){
  state.selectedBgmId=seed.selectedBgmId;
}
Object.keys(seed.covers).forEach(key=>{
  const layout=state.covers[key];
  layout.characterSizeScales={
    ...coverDefaults[key].characterSizeScales,
    ...(layout.characterSizeScales||{})
  };
  layout.lineSpacing=Number.isFinite(Number(layout.lineSpacing))
    ?Number(layout.lineSpacing)
    :coverDefaults[key].lineSpacing;
  layout.letterSpacing=Number.isFinite(Number(layout.letterSpacing))
    ?Number(layout.letterSpacing)
    :coverDefaults[key].letterSpacing;
});
let selectedLayer=null;
let textSelections={};
let interaction=null;
let copyFeedbackTimer=null;
let toastTimer=null;

function save(message="已自动保存"){
  localStorage.setItem(storageKey,JSON.stringify(state));
  $("saveStatus").textContent=message;
}

function ordinalCount(value){
  return [...value].filter(character=>!/[\\s\\n]/.test(character)).length;
}

function validIntro(){
  if(!state.includeIntro){
    $("introError").textContent="";
    return true;
  }
  const lines=state.introText.split("\\n").filter(line=>line.trim());
  const valid=state.introText.trim().length>0&&lines.length>=1&&lines.length<=3;
  $("introError").textContent=valid?"":"片头标题需保持 1—3 个非空行。";
  return valid;
}

function validCover(key){
  const layout=state.covers[key];
  const lines=layout.coverText.split("\\n").filter(line=>line.trim());
  const valid=layout.coverText.trim().length>0&&lines.length>=1&&lines.length<=6;
  const error=$("coverError-"+key);
  if(error)error.textContent=valid?"点击文字可继续改文案；修改文案后请重新选择强调色。":"封面文案需保持 1—6 个非空行。";
  return valid;
}

function titleFragment(
  text,
  accentIndices,
  selection,
  characterSizeScales,
  baseFontSize,
  letterSpacing,
  ratio
){
  const accents=new Set(accentIndices||[]);
  const sizeScales=characterSizeScales||{};
  const fragment=document.createDocumentFragment();
  let ordinal=0;
  const characters=[...text];
  characters.forEach((character,characterIndex)=>{
    if(character==="\\n"){
      fragment.append(document.createElement("br"));
      return;
    }
    const span=document.createElement("span");
    span.textContent=character;
    if(!/\\s/.test(character)){
      if(accents.has(ordinal))span.classList.add("accent");
      if(selection&&ordinal>=selection.start&&ordinal<selection.end){
        span.classList.add("selection-preview");
      }
      const scale=clamp(sizeScales[String(ordinal)]||1,.5,2);
      span.style.fontSize=(baseFontSize*scale*ratio)+"px";
      ordinal++;
    }
    if(
      characterIndex<characters.length-1&&
      characters[characterIndex+1]!=="\\n"
    ){
      span.style.marginRight=(letterSpacing*ratio)+"px";
    }
    fragment.append(span);
  });
  return fragment;
}

function refreshTitleCharacterStyles(editor,layout,ratio){
  const nodes=[...editor.childNodes];
  let ordinal=0;
  nodes.forEach((node,index)=>{
    if(node.nodeName!=="SPAN")return;
    const character=node.textContent||"";
    const isCharacter=!/\\s/.test(character);
    const scale=isCharacter
      ?clamp(layout.characterSizeScales[String(ordinal)]||1,.5,2)
      :1;
    node.style.fontSize=(layout.fontSize*scale*ratio)+"px";
    const next=nodes[index+1];
    node.style.marginRight=(
      next&&next.nodeName!=="BR"
        ?layout.letterSpacing*ratio
        :0
    )+"px";
    if(isCharacter)ordinal++;
  });
}

function buildCoverCanvases(){
  const cards=Object.entries(seed.covers).map(([key,cover])=>{
    const card=document.createElement("article");
    card.className="cover-card";
    card.dataset.coverKey=key;
    card.innerHTML=`
      <div class="cover-card-head">
        <div class="cover-card-title"><strong>${cover.label}</strong><span>${cover.width} × ${cover.height}px</span></div>
        <div class="canvas-actions">
          <button class="canvas-action" type="button" data-action="accent-add">选中设强调色</button>
          <button class="canvas-action" type="button" data-action="accent-remove">取消强调色</button>
          <button class="canvas-action" type="button" data-action="reset">复位排版</button>
        </div>
      </div>
      <div class="type-tools" aria-label="${cover.label}文字细节">
        <div class="type-control selection-tools">
          <span class="type-control-label">所选字大小</span>
          <button class="type-button" type="button" data-type-action="character-minus" aria-label="缩小所选文字">−</button>
          <output class="type-value" id="characterSizeValue-${key}">未选择</output>
          <button class="type-button" type="button" data-type-action="character-plus" aria-label="放大所选文字">＋</button>
          <button class="type-button" type="button" data-type-action="character-reset">还原</button>
        </div>
      </div>
      <div class="cover-stage" id="coverStage-${key}" data-key="${key}">
        <div class="cover-layer subject-layer" id="subjectLayer-${key}" data-layer="subject">
          <img alt="${cover.label}封面主体图" draggable="false">
          <button class="resize-handle" type="button" aria-label="缩放主体图">↘</button>
        </div>
        <div class="cover-layer title-layer" id="titleLayer-${key}" data-layer="title">
          <button class="move-handle" type="button" aria-label="移动封面文字">移动文字</button>
          <div class="title-editor" id="titleEditor-${key}" contenteditable="true" spellcheck="false" aria-label="编辑${cover.label}封面文案"></div>
          <button class="edge-handle line-spacing-handle" type="button" aria-label="拖动调整行距"></button>
          <button class="edge-handle letter-spacing-handle" type="button" aria-label="拖动调整字距"></button>
          <button class="resize-handle" type="button" aria-label="调整封面文字大小">↘</button>
        </div>
      </div>
      <div class="cover-card-foot" id="coverError-${key}"></div>`;
    const stage=card.querySelector(".cover-stage");
    stage.style.aspectRatio=`${cover.width}/${cover.height}`;
    stage.addEventListener("pointerdown",event=>{
      if(!event.target.closest(".cover-layer")){
        clearCanvasSelection();
      }
    });
    card.querySelectorAll("[data-action^='accent'],[data-type-action]").forEach(button=>{
      button.addEventListener("pointerdown",event=>{
        event.preventDefault();
      });
    });
    card.querySelector("[data-action='accent-add']").addEventListener("click",()=>changeAccent(key,true));
    card.querySelector("[data-action='accent-remove']").addEventListener("click",()=>changeAccent(key,false));
    card.querySelector("[data-type-action='character-minus']").addEventListener("click",()=>adjustCharacterSize(key,-.1));
    card.querySelector("[data-type-action='character-plus']").addEventListener("click",()=>adjustCharacterSize(key,.1));
    card.querySelector("[data-type-action='character-reset']").addEventListener("click",()=>adjustCharacterSize(key,0,true));
    card.querySelector("[data-action='reset']").addEventListener("click",()=>{
      state.covers[key]=deepCopy(coverDefaults[key]);
      selectedLayer={key,layer:"title"};
      save("已恢复"+cover.label+"的初始排版");
      render();
    });
    const editor=card.querySelector(".title-editor");
    editor.addEventListener("focus",()=>selectLayer(key,"title"));
    editor.addEventListener("click",()=>selectLayer(key,"title"));
    editor.addEventListener("input",()=>{
      const text=editor.innerText.replace(/\\r/g,"").replace(/\\n+$/,"");
      if(text!==state.covers[key].coverText){
        const sameCharacters=compact(text)===compact(state.covers[key].coverText);
        state.covers[key].coverText=text;
        if(!sameCharacters){
          state.covers[key].accentIndices=[];
          state.covers[key].characterSizeScales={};
        }
      }
      textSelections[key]=null;
      save();
      updateTypographyStatus(key);
      updateApprovalState();
    });
    editor.addEventListener("keyup",()=>{
      if(captureTextSelection(key,true))renderVariant(key,true);
    });
    editor.addEventListener("mouseup",()=>{
      if(captureTextSelection(key,true))renderVariant(key,true);
    });
    editor.addEventListener("blur",()=>renderVariant(key));
    const titleLayer=card.querySelector(".title-layer");
    titleLayer.querySelector(".move-handle").addEventListener("pointerdown",event=>startInteraction(event,key,"title","move"));
    titleLayer.querySelector(".line-spacing-handle").addEventListener("pointerdown",event=>startInteraction(event,key,"title","line-spacing"));
    titleLayer.querySelector(".letter-spacing-handle").addEventListener("pointerdown",event=>startInteraction(event,key,"title","letter-spacing"));
    titleLayer.querySelector(".resize-handle").addEventListener("pointerdown",event=>startInteraction(event,key,"title","resize"));
    const subjectLayer=card.querySelector(".subject-layer");
    subjectLayer.addEventListener("pointerdown",event=>{
      if(event.target.closest(".resize-handle"))return;
      startInteraction(event,key,"subject","move");
    });
    subjectLayer.querySelector(".resize-handle").addEventListener("pointerdown",event=>startInteraction(event,key,"subject","resize"));
    return card;
  });
  $("coverGrid").replaceChildren(...cards);
}

function buildBgmOptions(){
  const cards=seed.bgmOptions.map(option=>{
    const label=document.createElement("label");
    label.className="bgm-choice";
    label.dataset.bgmId=option.id;
    const duration=Number(option.durationSeconds);
    const minutes=Number.isFinite(duration)
      ?Math.floor(duration/60)+":"+String(Math.round(duration%60)).padStart(2,"0")
      :"";
    label.innerHTML=`
      <input type="radio" name="bgmOption" value="${option.id}">
      <div>
        <div class="bgm-choice-head"><strong>${option.label} · ${option.name}</strong><span>${minutes}</span></div>
        ${option.previewSrc
          ?`<audio controls preload="metadata" src="${option.previewSrc}"></audio>`
          :`<div class="bgm-no-preview">此兼容选项暂无试听片段</div>`}
      </div>`;
    label.querySelector("input").addEventListener("change",event=>{
      if(!event.target.checked)return;
      state.selectedBgmId=event.target.value;
      save("背景音乐已选择 "+event.target.value);
      render();
    });
    return label;
  });
  $("bgmOptions").replaceChildren(...cards);
}

function selectLayer(key,layer){
  selectedLayer={key,layer};
  Object.keys(seed.covers).forEach(coverKey=>{
    const subject=$("subjectLayer-"+coverKey);
    const title=$("titleLayer-"+coverKey);
    if(subject)subject.classList.toggle(
      "selected",
      selectedLayer?.key===coverKey&&selectedLayer?.layer==="subject"
    );
    if(title)title.classList.toggle(
      "selected",
      selectedLayer?.key===coverKey&&selectedLayer?.layer==="title"
    );
  });
}

function clearCanvasSelection(){
  selectedLayer=null;
  textSelections={};
  const selection=window.getSelection();
  if(selection)selection.removeAllRanges();
  if(document.activeElement?.classList?.contains("title-editor")){
    document.activeElement.blur();
  }
  render();
}

function renderVariant(key,forceTitle=false){
  const cover=seed.covers[key];
  const layout=state.covers[key];
  const stage=$("coverStage-"+key);
  if(!stage)return;
  const ratio=stage.clientWidth/cover.width;
  stage.style.background=cover.background;

  const subject=$("subjectLayer-"+key);
  subject.classList.toggle("selected",selectedLayer?.key===key&&selectedLayer?.layer==="subject");
  subject.style.left=(layout.subjectX*ratio)+"px";
  subject.style.top=(layout.subjectY*ratio)+"px";
  subject.style.width=(cover.subjectBaseWidth*layout.subjectScale*ratio)+"px";
  subject.style.height=(cover.subjectBaseHeight*layout.subjectScale*ratio)+"px";
  subject.querySelector("img").src=cover.imageSrc;

  const title=$("titleLayer-"+key);
  title.classList.toggle("selected",selectedLayer?.key===key&&selectedLayer?.layer==="title");
  title.style.left=(layout.textX*ratio)+"px";
  title.style.top=((layout.textY-layout.fontSize*.58)*ratio)+"px";
  const editor=$("titleEditor-"+key);
  editor.style.fontSize=(layout.fontSize*ratio)+"px";
  editor.style.lineHeight=((layout.fontSize+layout.lineSpacing)*ratio)+"px";
  if(forceTitle||document.activeElement!==editor){
    editor.replaceChildren(
      titleFragment(
        layout.coverText,
        layout.accentIndices,
        textSelections[key],
        layout.characterSizeScales,
        layout.fontSize,
        layout.letterSpacing,
        ratio
      )
    );
  }
  refreshTitleCharacterStyles(editor,layout,ratio);
  title.querySelector(".line-spacing-handle").hidden=
    layout.coverText.split("\\n").filter(line=>line.trim()).length<2;
  updateTypographyStatus(key);
  validCover(key);
}

function render(){
  Object.keys(seed.covers).forEach(renderVariant);
  $("introStage").textContent=state.includeIntro
    ?state.introText
    :"本期不保留片头";
  if($("introInput").value!==state.introText)$("introInput").value=state.introText;
  $("introInput").disabled=!state.includeIntro;
  $("introEditor").classList.toggle("disabled",!state.includeIntro);
  document.querySelectorAll(".intro-mode-choice").forEach(card=>{
    const selected=(card.dataset.introMode==="include")===state.includeIntro;
    card.classList.toggle("selected",selected);
    const input=card.querySelector("input");
    if(input)input.checked=selected;
  });
  $("sceneApproval").checked=state.scenesApproved;
  document.querySelectorAll(".bgm-choice").forEach(card=>{
    const selected=card.dataset.bgmId===state.selectedBgmId;
    card.classList.toggle("selected",selected);
    const input=card.querySelector("input");
    if(input)input.checked=selected;
  });
  updateApprovalState();
}

function repairPersistedLetterSpacing(){
  let repaired=false;
  Object.keys(seed.covers).forEach(key=>{
    const cover=seed.covers[key];
    const layout=state.covers[key];
    const fits=bounds=>bounds.left>=12&&bounds.right<=cover.width-12;
    const current=layerBoundsInCanvas(key,"title");
    if(fits(current)||layout.letterSpacing<=-20)return;

    const original=layout.letterSpacing;
    let low=-20;
    let high=original;
    layout.letterSpacing=low;
    renderVariant(key);
    if(!fits(layerBoundsInCanvas(key,"title"))){
      layout.letterSpacing=original;
      renderVariant(key);
      return;
    }

    for(let index=0;index<12;index++){
      const middle=(low+high)/2;
      layout.letterSpacing=middle;
      renderVariant(key);
      if(fits(layerBoundsInCanvas(key,"title"))){
        low=middle;
      }else{
        high=middle;
      }
    }
    layout.letterSpacing=low;
    renderVariant(key);
    repaired=true;
  });
  if(repaired)save("已自动修正旧版中超出画布的字距");
}

function updateApprovalState(){
  const coversValid=Object.keys(seed.covers).every(validCover);
  const bgmValid=seed.bgmOptions.some(option=>option.id===state.selectedBgmId);
  const valid=validIntro()&&coversValid&&bgmValid&&state.scenesApproved;
  $("downloadApproval").disabled=!valid;
  $("copyApproval").disabled=!valid;
}

function captureTextSelection(key,preserveOnCollapsed=false){
  const editor=$("titleEditor-"+key);
  const selection=window.getSelection();
  if(!selection||selection.rangeCount===0){
    if(!preserveOnCollapsed)textSelections[key]=null;
    return textSelections[key]||null;
  }
  const range=selection.getRangeAt(0);
  if(!editor.contains(range.commonAncestorContainer)){
    if(!preserveOnCollapsed)textSelections[key]=null;
    return textSelections[key]||null;
  }
  const prefix=document.createRange();
  prefix.selectNodeContents(editor);
  prefix.setEnd(range.startContainer,range.startOffset);
  const start=ordinalCount(prefix.toString());
  const end=start+ordinalCount(range.toString());
  const characterCount=ordinalCount(state.covers[key].coverText);
  const captured={
    start:clamp(Math.min(start,end),0,characterCount),
    end:clamp(Math.max(start,end),0,characterCount)
  };
  if(captured.start===captured.end){
    if(!preserveOnCollapsed)textSelections[key]=null;
  }else{
    textSelections[key]=captured;
  }
  return textSelections[key];
}

function changeAccent(key,add){
  const selection=textSelections[key];
  if(!selection||selection.start===selection.end){
    showToast("请先在封面文字中选中要强调的字",false);
    return;
  }
  const accents=new Set(state.covers[key].accentIndices||[]);
  let changed=0;
  for(let index=selection.start;index<selection.end;index++){
    if(add&&!accents.has(index)){
      accents.add(index);
      changed++;
    }else if(!add&&accents.has(index)){
      accents.delete(index);
      changed++;
    }
  }
  if(changed===0){
    showToast(
      add?"选中的文字已经是强调色":"选中的文字没有强调色",
      false
    );
    return;
  }
  state.covers[key].accentIndices=[...accents].sort((a,b)=>a-b);
  textSelections[key]=null;
  save(add?"已设置强调色":"已取消强调色");
  renderVariant(key,true);
  showToast(
    add?`已将 ${changed} 个字设为强调色`:`已取消 ${changed} 个字的强调色`,
    true
  );
}

function updateTypographyStatus(key){
  const layout=state.covers[key];
  const characterValue=$("characterSizeValue-"+key);
  if(!characterValue)return;
  const selection=textSelections[key];
  if(!selection||selection.start===selection.end){
    characterValue.textContent="未选择";
    return;
  }
  const scales=[];
  for(let index=selection.start;index<selection.end;index++){
    scales.push(Number(layout.characterSizeScales[String(index)]||1));
  }
  const first=scales[0]||1;
  characterValue.textContent=scales.every(value=>Math.abs(value-first)<.001)
    ?Math.round(first*100)+"%"
    :"混合";
}

function adjustCharacterSize(key,delta,reset=false){
  const selection=textSelections[key];
  if(!selection||selection.start===selection.end){
    showToast("请先在封面文字中选中要调整的字",false);
    return;
  }
  const layout=state.covers[key];
  const scales={...(layout.characterSizeScales||{})};
  let changed=0;
  for(let index=selection.start;index<selection.end;index++){
    const mapKey=String(index);
    const current=Number(scales[mapKey]||1);
    const next=reset?1:clamp(current+delta,.5,2);
    if(Math.abs(next-current)<.001)continue;
    if(Math.abs(next-1)<.001){
      delete scales[mapKey];
    }else{
      scales[mapKey]=Number(next.toFixed(2));
    }
    changed++;
  }
  if(changed===0){
    showToast(reset?"所选文字已经是默认大小":"所选文字已达到大小限制",false);
    return;
  }
  layout.characterSizeScales=scales;
  selectLayer(key,"title");
  save(reset?"所选文字已恢复默认大小":"所选文字大小已保存");
  renderVariant(key,true);
  showToast(
    reset
      ?`已还原 ${changed} 个字`
      :`已调整 ${changed} 个字的大小`,
    true
  );
}

function pointInCanvas(key,event){
  const stage=$("coverStage-"+key);
  const rect=stage.getBoundingClientRect();
  const cover=seed.covers[key];
  return {
    x:(event.clientX-rect.left)*cover.width/rect.width,
    y:(event.clientY-rect.top)*cover.height/rect.height
  };
}

function layerBoundsInCanvas(key,layer){
  const stage=$("coverStage-"+key);
  const element=$(layer==="title"?"titleLayer-"+key:"subjectLayer-"+key);
  const stageRect=stage.getBoundingClientRect();
  const elementRect=element.getBoundingClientRect();
  const cover=seed.covers[key];
  const scaleX=cover.width/stageRect.width;
  const scaleY=cover.height/stageRect.height;
  const left=(elementRect.left-stageRect.left)*scaleX;
  const top=(elementRect.top-stageRect.top)*scaleY;
  const width=elementRect.width*scaleX;
  const height=elementRect.height*scaleY;
  return {
    left,
    top,
    right:left+width,
    bottom:top+height,
    width,
    height,
    centerX:left+width/2,
    centerY:top+height/2
  };
}

function keepTitleAnchor(key,anchorType,startBounds){
  const layout=state.covers[key];
  const current=layerBoundsInCanvas(key,"title");
  if(anchorType==="left"){
    layout.textX+=startBounds.left-current.left;
  }else{
    layout.textX+=startBounds.centerX-current.centerX;
    layout.textY+=startBounds.centerY-current.centerY;
  }
  renderVariant(key);
}

function startInteraction(event,key,layer,mode){
  event.preventDefault();
  event.stopPropagation();
  selectLayer(key,layer);
  if(layer==="title"&&mode!=="move"){
    renderVariant(key,true);
  }
  const point=pointInCanvas(key,event);
  const layout=state.covers[key];
  const startBounds=layerBoundsInCanvas(key,layer);
  const center=layer==="title"
    ?{x:startBounds.centerX,y:startBounds.centerY}
    :{x:layout.subjectX,y:layout.subjectY};
  interaction={
    pointerId:event.pointerId,
    key,layer,mode,
    startPoint:point,
    startLayout:deepCopy(layout),
    startBounds,
    startDistance:Math.max(20,Math.hypot(point.x-center.x,point.y-center.y))
  };
  if(event.currentTarget.setPointerCapture)event.currentTarget.setPointerCapture(event.pointerId);
}

window.addEventListener("pointermove",event=>{
  if(!interaction||event.pointerId!==interaction.pointerId)return;
  const {
    key,
    layer,
    mode,
    startPoint,
    startLayout,
    startBounds,
    startDistance
  }=interaction;
  const point=pointInCanvas(key,event);
  const layout=state.covers[key];
  const cover=seed.covers[key];
  if(mode==="move"){
    const dx=point.x-startPoint.x,dy=point.y-startPoint.y;
    if(layer==="title"){
      layout.textX=clamp(startLayout.textX+dx,0,cover.width);
      layout.textY=clamp(startLayout.textY+dy,0,cover.height);
    }else{
      layout.subjectX=clamp(startLayout.subjectX+dx,0,cover.width);
      layout.subjectY=clamp(startLayout.subjectY+dy,0,cover.height);
    }
  }else if(mode==="line-spacing"){
    const lineCount=startLayout.coverText
      .split("\\n")
      .filter(line=>line.trim()).length;
    const gaps=Math.max(1,lineCount-1);
    layout.lineSpacing=clamp(
      startLayout.lineSpacing+(point.y-startPoint.y)/gaps,
      0,
      200
    );
  }else if(mode==="letter-spacing"){
    const maxCharacters=Math.max(
      1,
      ...startLayout.coverText
        .split("\\n")
        .map(line=>ordinalCount(line))
    );
    const gaps=Math.max(1,maxCharacters-1);
    const minimumRight=startBounds.left+90;
    const maximumRight=Math.max(minimumRight,cover.width-12);
    const targetRight=clamp(
      startBounds.right+(point.x-startPoint.x),
      minimumRight,
      maximumRight
    );
    layout.letterSpacing=clamp(
      startLayout.letterSpacing+(targetRight-startBounds.right)/gaps,
      -20,
      80
    );
    layout.textX=startLayout.textX;
    renderVariant(key);
    keepTitleAnchor(key,"left",startBounds);
    return;
  }else{
    const center=layer==="title"
      ?{x:startBounds.centerX,y:startBounds.centerY}
      :{x:startLayout.subjectX,y:startLayout.subjectY};
    const factor=Math.max(.2,Math.hypot(point.x-center.x,point.y-center.y)/startDistance);
    if(layer==="title"){
      layout.textX=startLayout.textX;
      layout.textY=startLayout.textY;
      layout.fontSize=clamp(startLayout.fontSize*factor,30,260);
      layout.lineSpacing=clamp(startLayout.lineSpacing*factor,0,200);
      layout.letterSpacing=clamp(startLayout.letterSpacing*factor,-20,80);
      renderVariant(key);
      keepTitleAnchor(key,"center",startBounds);
      return;
    }else{
      layout.subjectScale=clamp(startLayout.subjectScale*factor,.25,2.5);
    }
  }
  renderVariant(key);
});

window.addEventListener("pointerup",event=>{
  if(!interaction||event.pointerId!==interaction.pointerId)return;
  const completedMode=interaction.mode;
  interaction=null;
  const message={
    "line-spacing":"行距已保存",
    "letter-spacing":"字距已保存",
    "resize":"等比缩放已保存",
    "move":"位置已保存"
  }[completedMode]||"画布调整已保存";
  save(message);
  render();
});

function coverApproval(key){
  const layout=state.covers[key];
  return {
    title_lines:layout.coverText.split("\\n").filter(line=>line.trim()),
    accent_char_indices:[...(layout.accentIndices||[])].sort((a,b)=>a-b),
    text_center_x:Math.round(layout.textX),
    first_line_center_y:Math.round(layout.textY),
    font_size:Math.round(layout.fontSize),
    line_spacing_px:Math.round(layout.lineSpacing),
    letter_spacing_px:Math.round(layout.letterSpacing),
    character_size_scales:Object.fromEntries(
      Object.entries(layout.characterSizeScales||{})
        .map(([index,scale])=>[String(index),Number(Number(scale).toFixed(2))])
        .filter(([,scale])=>Math.abs(scale-1)>.001)
    ),
    subject_center_x:Math.round(layout.subjectX),
    subject_center_y:Math.round(layout.subjectY),
    subject_scale:Number(Number(layout.subjectScale).toFixed(2))
  };
}

function approvalData(){
  return {
    schema_version:2,
    approved:true,
    selected_bgm_id:state.selectedBgmId,
    include_intro:state.includeIntro,
    intro_lines:state.introText.split("\\n").filter(line=>line.trim()),
    covers:Object.fromEntries(Object.keys(seed.covers).map(key=>[key,coverApproval(key)])),
    approved_scene_ids:seed.expectedSceneIds
  };
}

function showToast(message,success){
  const toast=$("toast");
  clearTimeout(toastTimer);
  toast.textContent=message;
  toast.className="toast show "+(success?"success":"failure");
  toastTimer=setTimeout(()=>{toast.className="toast"},2200);
}

function showCopyFeedback(success,message){
  const button=$("copyApproval");
  clearTimeout(copyFeedbackTimer);
  button.classList.remove("success","failure");
  void button.offsetWidth;
  button.classList.add(success?"success":"failure");
  button.textContent=success?"已复制 ✓":"复制失败，请重试";
  $("saveStatus").textContent=message;
  showToast(message,success);
  copyFeedbackTimer=setTimeout(()=>{
    button.classList.remove("success","failure");
    button.textContent="复制确认数据";
  },2200);
}

$("introInput").addEventListener("input",event=>{state.introText=event.target.value;save();render()});
document.querySelectorAll('input[name="introMode"]').forEach(input=>{
  input.addEventListener("change",event=>{
    if(!event.target.checked)return;
    state.includeIntro=event.target.value==="include";
    save(state.includeIntro?"已选择保留片头":"已选择不要片头");
    render();
  });
});
$("sceneApproval").addEventListener("change",event=>{state.scenesApproved=event.target.checked;save();render()});

document.querySelectorAll(".tab").forEach(button=>button.addEventListener("click",()=>{
  document.querySelectorAll(".tab").forEach(tab=>tab.classList.toggle("active",tab===button));
  const cover=button.dataset.tab==="cover";
  $("coverPanel").hidden=!cover;
  $("introStage").hidden=cover;
}));

$("downloadApproval").addEventListener("click",()=>{
  const blob=new Blob([JSON.stringify(approvalData(),null,2)],{type:"application/json"});
  const link=document.createElement("a");
  link.href=URL.createObjectURL(blob);
  link.download="approval.json";
  link.click();
  setTimeout(()=>URL.revokeObjectURL(link.href),1000);
  $("saveStatus").textContent="确认文件已下载";
  showToast("approval.json 已下载",true);
});

$("copyApproval").addEventListener("click",async()=>{
  const text=JSON.stringify(approvalData(),null,2);
  let copied=false;
  try{
    await navigator.clipboard.writeText(text);
    copied=true;
  }catch(error){
    const field=document.createElement("textarea");
    field.value=text;
    field.style.position="fixed";
    field.style.opacity="0";
    document.body.append(field);
    field.select();
    try{copied=document.execCommand("copy")}catch(fallbackError){copied=false}
    field.remove();
  }
  showCopyFeedback(
    copied,
    copied?"确认数据已复制到剪贴板":"复制未成功，请使用下载按钮"
  );
});

document.addEventListener("pointerdown",event=>{
  const keepsSelection=event.target.closest(
    ".cover-layer,.canvas-actions,.type-tools"
  );
  if(!keepsSelection&&(selectedLayer||Object.values(textSelections).some(Boolean))){
    clearCanvasSelection();
  }
});

window.__reviewTestApi={
  snapshot:()=>({
    state:deepCopy(state),
    selectedLayer:selectedLayer?{...selectedLayer}:null,
    textSelections:deepCopy(textSelections),
    interaction:interaction
      ?{key:interaction.key,layer:interaction.layer,mode:interaction.mode}
      :null
  }),
  approval:()=>deepCopy(approvalData()),
  bounds:(key,layer)=>({...layerBoundsInCanvas(key,layer)})
};

buildCoverCanvases();
buildBgmOptions();
window.addEventListener("resize",render);
render();
repairPersistedLetterSpacing();
</script>
</body>
</html>"""

    page = (
        template.replace("__TITLE__", html.escape(plan.get("title", "未命名视频")))
        .replace("__FONT_SRC__", font_src)
        .replace("__SCENE_COUNT__", str(len(scene_ids)))
        .replace("__SCENE_CARDS__", "".join(cards))
        .replace("__PAYLOAD__", payload_json)
    )

    args.output_html.parent.mkdir(parents=True, exist_ok=True)
    args.output_html.write_text(page, encoding="utf-8")
    approval_path = args.approval_template or args.output_html.with_name(
        "approval-template.json"
    )
    write_json(
        approval_path,
        {
            "schema_version": 2,
            "approved": False,
            "selected_bgm_id": selected_bgm_id,
            "include_intro": include_intro,
            "intro_lines": intro_lines,
            "covers": {
                key: {
                    "title_lines": cover["titleLines"],
                    "accent_char_indices": cover["accentCharIndices"],
                    "text_center_x": cover["textCenterX"],
                    "first_line_center_y": cover["firstLineCenterY"],
                    "font_size": cover["fontSize"],
                    "line_spacing_px": cover["lineSpacing"],
                    "letter_spacing_px": cover["letterSpacing"],
                    "character_size_scales": cover["characterSizeScales"],
                    "subject_center_x": cover["subjectCenterX"],
                    "subject_center_y": cover["subjectCenterY"],
                    "subject_scale": cover["subjectScale"],
                }
                for key, cover in cover_payload.items()
            },
            "approved_scene_ids": [],
            "expected_scene_ids": scene_ids,
            "notes": "在 review.html 的横版与竖版画布中直接调整并下载 approval.json。",
        },
    )
    print(f"Wrote interactive review page to {args.output_html}")
    print(f"Wrote approval template to {approval_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
