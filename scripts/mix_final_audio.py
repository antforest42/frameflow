#!/usr/bin/env python3
"""Mix narration and a review-selected body BGM into the final picture track."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path


def probe_duration(ffprobe: str, media: Path) -> float:
    result = subprocess.run(
        [
            ffprobe,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(media),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return float(result.stdout.strip())


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def resolve_path(base: Path, value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else base / path


def load_bgm_catalog(config_path: Path, config: dict, asset_root: Path) -> tuple[Path, dict] | None:
    audio = config.get("audio", {})
    catalog_asset = audio.get("bgm_catalog_asset", "bgm/catalog.json")
    catalog_path = resolve_path(asset_root, catalog_asset)
    if not catalog_path.is_file():
        return None
    catalog = load_json(catalog_path)
    options = catalog.get("options")
    if not isinstance(options, list) or not options:
        raise SystemExit(f"BGM 曲库没有可用选项：{catalog_path}")
    return catalog_path, catalog


def choose_bgm_id(
    args: argparse.Namespace,
    config: dict,
    catalog: dict | None,
    approval: dict,
) -> str:
    approval_id = approval.get("selected_bgm_id")
    default_id = (
        config.get("audio", {}).get("default_bgm_id")
        or (catalog or {}).get("default_id")
        or "A"
    )
    return str(args.bgm_id or approval_id or default_id).strip()


def resolve_catalog_option(
    catalog_path: Path,
    catalog: dict,
    selected_id: str,
) -> tuple[dict, dict]:
    options = {
        str(option.get("id", "")).upper(): option
        for option in catalog["options"]
        if isinstance(option, dict) and option.get("id")
    }
    key = selected_id.upper()
    if key not in options:
        available = "、".join(sorted(options))
        raise SystemExit(f"BGM 选项 {selected_id!r} 不存在；可用选项：{available}")
    intro_key = str(catalog.get("intro_source_id", "A")).upper()
    if intro_key not in options:
        raise SystemExit(f"BGM 曲库缺少片头固定音源 {intro_key}")
    for option in (options[intro_key], options[key]):
        asset = resolve_path(catalog_path.parent, option.get("asset", ""))
        if not asset.is_file():
            raise SystemExit(f"BGM 曲库文件不存在：{asset}")
    return options[intro_key], options[key]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("picture_track", type=Path)
    parser.add_argument("narration", type=Path)
    parser.add_argument("config", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--approval", type=Path)
    parser.add_argument("--bgm-id", help="临时覆盖审核页选择，例如 A 或 B")
    parser.add_argument("--bgm-gain-db", type=float, help="临时覆盖正文 BGM 增益")
    parser.add_argument(
        "--intro-bgm-gain-db",
        type=float,
        help="临时覆盖片头固定音源增益",
    )
    parser.add_argument("--ffmpeg", default="ffmpeg")
    parser.add_argument("--ffprobe", default="ffprobe")
    args = parser.parse_args()

    config = load_json(args.config)
    approval = {}
    if args.approval:
        if not args.approval.is_file():
            raise SystemExit(f"找不到审核确认文件：{args.approval}")
        approval = load_json(args.approval)
    include_intro = approval.get("include_intro", True)
    if not isinstance(include_intro, bool):
        raise SystemExit("approval.include_intro 必须是 true 或 false")
    asset_root = resolve_path(args.config.parent, config.get("asset_root", "."))
    catalog_loaded = load_bgm_catalog(args.config, config, asset_root)
    catalog_path, catalog = catalog_loaded if catalog_loaded else (None, None)
    selected_id = choose_bgm_id(args, config, catalog, approval)

    if catalog_path and catalog:
        intro_option, selected_option = resolve_catalog_option(
            catalog_path, catalog, selected_id
        )
        intro_bgm = resolve_path(catalog_path.parent, intro_option["asset"])
        selected_bgm = resolve_path(catalog_path.parent, selected_option["asset"])
        intro_id = str(intro_option["id"])
        selected_id = str(selected_option["id"])
        selected_name = str(selected_option.get("name") or selected_id)
        body_start = float(selected_option.get("body_start_seconds", 0.0))
    else:
        if selected_id.upper() != "A":
            raise SystemExit("当前配置没有 BGM 曲库，只能使用兼容选项 A")
        selected_id = intro_id = "A"
        selected_name = "原版 BGM"
        intro_bgm = selected_bgm = resolve_path(
            asset_root, config["audio"]["bgm_asset"]
        )
        body_start = 0.0

    for label, path in (
        ("画面视频", args.picture_track),
        ("朗读音频", args.narration),
        ("片头固定音源", intro_bgm),
        ("所选正文 BGM", selected_bgm),
    ):
        if not path.is_file():
            raise SystemExit(f"缺少{label}：{path}")

    duration = probe_duration(args.ffprobe, args.picture_track)
    narration_duration = probe_duration(args.ffprobe, args.narration)
    tail_seconds = float(config.get("timing", {}).get("tail_seconds", 0.0))
    configured_intro_seconds = float(
        config.get("timing", {}).get("lead_in_seconds", 3.933)
    )
    narration_trim_start = 0.0 if include_intro else configured_intro_seconds
    effective_narration_duration = max(0.0, narration_duration - narration_trim_start)
    if effective_narration_duration > duration + 0.15:
        raise SystemExit(
            "朗读比画面更长："
            f"画面{duration:.3f}秒，有效朗读{effective_narration_duration:.3f}秒"
        )
    if duration - effective_narration_duration > tail_seconds + 0.15:
        raise SystemExit(
            "画面与朗读时长差异超过片尾留白："
            f"画面{duration:.3f}秒，有效朗读{effective_narration_duration:.3f}秒"
        )

    narration_gain = float(config["audio"].get("narration_volume_db", 2.0))
    bgm_gain = float(
        args.bgm_gain_db
        if args.bgm_gain_db is not None
        else config["audio"].get("volume_db", 0.0)
    )
    intro_bgm_gain = float(
        args.intro_bgm_gain_db
        if args.intro_bgm_gain_db is not None
        else config["audio"].get("intro_volume_db", 0.0)
    )
    limiter = float(config["audio"].get("limiter", 0.944))
    intro_seconds = min(duration, configured_intro_seconds) if include_intro else 0.0
    if not catalog_path:
        body_start = configured_intro_seconds
    audio_codec = config["output"]["audio_codec"]
    audio_bitrate = config["output"]["audio_bitrate"]
    audio_sample_rate = int(config["output"].get("audio_sample_rate", 48000))

    command = [
        args.ffmpeg,
        "-y",
        "-v",
        "error",
        "-i",
        str(args.picture_track),
        "-i",
        str(args.narration),
    ]
    if not include_intro:
        command.extend(["-stream_loop", "-1", "-i", str(selected_bgm)])
        bgm_filter = (
            f"[2:a]atrim=start={body_start:.6f}:duration={duration:.6f},"
            "asetpts=PTS-STARTPTS,"
            f"volume={bgm_gain:g}dB,"
            f"aresample={audio_sample_rate}[bgm];"
        )
    elif duration <= intro_seconds:
        command.extend(["-stream_loop", "-1", "-i", str(intro_bgm)])
        bgm_filter = (
            f"[2:a]atrim=duration={duration:.6f},asetpts=PTS-STARTPTS,"
            f"volume={intro_bgm_gain:g}dB,"
            f"aresample={audio_sample_rate}[bgm];"
        )
    else:
        command.extend(["-stream_loop", "-1", "-i", str(intro_bgm)])
        body_duration = duration - intro_seconds
        if selected_id.upper() == intro_id.upper():
            bgm_filter = (
                "[2:a]asplit=2[intro_source][body_source];"
                f"[intro_source]atrim=start=0:duration={intro_seconds:.6f},"
                "asetpts=PTS-STARTPTS,"
                f"volume={intro_bgm_gain:g}dB[intro_bgm];"
                f"[body_source]atrim=start={body_start:.6f}:"
                f"duration={body_duration:.6f},asetpts=PTS-STARTPTS,"
                f"volume={bgm_gain:g}dB[body_bgm];"
                "[intro_bgm][body_bgm]concat=n=2:v=0:a=1,"
                f"aresample={audio_sample_rate}[bgm];"
            )
        else:
            command.extend(
                ["-stream_loop", "-1", "-i", str(selected_bgm)]
            )
            bgm_filter = (
                f"[2:a]atrim=start=0:duration={intro_seconds:.6f},"
                "asetpts=PTS-STARTPTS,"
                f"volume={intro_bgm_gain:g}dB[intro_bgm];"
                f"[3:a]atrim=start={body_start:.6f}:"
                f"duration={body_duration:.6f},asetpts=PTS-STARTPTS,"
                f"volume={bgm_gain:g}dB[body_bgm];"
                "[intro_bgm][body_bgm]concat=n=2:v=0:a=1,"
                f"aresample={audio_sample_rate}[bgm];"
            )
    filter_graph = (
        f"[1:a]atrim=start={narration_trim_start:.6f},asetpts=PTS-STARTPTS,"
        "pan=stereo|c0=c0|c1=c0,"
        f"aresample={audio_sample_rate},"
        f"volume={narration_gain:g}dB,"
        f"apad=whole_dur={duration:.6f}[narration];"
        + bgm_filter
        + "[narration][bgm]amix=inputs=2:duration=longest:"
        f"dropout_transition=0,alimiter=limit={limiter:g}:level=false[mix]"
    )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    command.extend(
        [
            "-filter_complex",
            filter_graph,
            "-map",
            "0:v:0",
            "-map",
            "[mix]",
            "-t",
            f"{duration:.6f}",
            "-c:v",
            "copy",
            "-c:a",
            audio_codec,
            "-b:a",
            audio_bitrate,
            "-ac",
            "2",
            "-ar",
            str(audio_sample_rate),
            "-movflags",
            "+faststart",
            str(args.output),
        ]
    )
    subprocess.run(command, check=True)
    if include_intro:
        timeline_message = f"片头0—{intro_seconds:.3f}秒保留原音源"
    else:
        timeline_message = (
            f"无片头；已裁掉朗读前{narration_trim_start:.3f}秒静音，"
            "正文画面、人声与所选BGM从0秒同步开始"
        )
    print(
        f"最终声音已合成：{timeline_message}；"
        f"正文BGM={selected_id}（{selected_name}）；"
        f"人声{narration_gain:+g}dB，片头背景音乐{intro_bgm_gain:+g}dB，"
        f"正文背景音乐{bgm_gain:+g}dB，"
        "立体声输出，不使用自动压低背景音乐。"
    )
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
