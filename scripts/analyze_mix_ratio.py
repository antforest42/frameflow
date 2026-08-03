#!/usr/bin/env python3
"""Estimate voice-to-music ratio when a reference mix uses a known stereo BGM."""

from __future__ import annotations

import argparse
import json
import math
import subprocess
from pathlib import Path

import numpy as np


def decode_stereo(
    ffmpeg: str,
    path: Path,
    *,
    start: float,
    duration: float,
    sample_rate: int,
) -> np.ndarray:
    result = subprocess.run(
        [
            ffmpeg,
            "-v",
            "error",
            "-ss",
            f"{start:.6f}",
            "-t",
            f"{duration:.6f}",
            "-i",
            str(path),
            "-map",
            "0:a:0",
            "-ac",
            "2",
            "-ar",
            str(sample_rate),
            "-f",
            "f32le",
            "-",
        ],
        check=True,
        capture_output=True,
    )
    samples = np.frombuffer(result.stdout, dtype="<f4")
    if samples.size < 2:
        raise SystemExit(f"音频没有可分析样本：{path}")
    return samples[: samples.size - samples.size % 2].reshape(-1, 2).astype(
        np.float64
    )


def rms(values: np.ndarray) -> float:
    return float(np.sqrt(np.mean(np.square(values))))


def db(value: float) -> float:
    return 20.0 * math.log10(max(value, 1e-12))


def correlation(left: np.ndarray, right: np.ndarray) -> float:
    left = left - np.mean(left)
    right = right - np.mean(right)
    denominator = math.sqrt(float(np.dot(left, left) * np.dot(right, right)))
    return float(np.dot(left, right) / denominator) if denominator else 0.0


def voiced_ratio(
    voice: np.ndarray,
    music: np.ndarray,
    sample_rate: int,
) -> tuple[float, float, float, int]:
    frame_size = max(1, round(sample_rate * 0.4))
    count = min(len(voice), len(music)) // frame_size
    voice_frames = voice[: count * frame_size].reshape(count, frame_size)
    music_frames = music[: count * frame_size].reshape(count, frame_size, 2)
    voice_rms = np.sqrt(np.mean(np.square(voice_frames), axis=1))
    music_rms = np.sqrt(np.mean(np.square(music_frames), axis=(1, 2)))
    voice_db = 20.0 * np.log10(np.maximum(voice_rms, 1e-12))
    threshold = max(-50.0, float(np.percentile(voice_db, 35)))
    active = voice_db >= threshold
    if not np.any(active):
        active = np.ones_like(voice_db, dtype=bool)
    active_voice = float(np.sqrt(np.mean(np.square(voice_frames[active]))))
    active_music = float(np.sqrt(np.mean(np.square(music_frames[active]))))
    return active_voice, active_music, db(active_voice / active_music), int(np.sum(active))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("reference", type=Path)
    parser.add_argument("bgm", type=Path)
    parser.add_argument("--start", type=float, default=12.0)
    parser.add_argument("--duration", type=float, default=180.0)
    parser.add_argument("--bgm-start", type=float)
    parser.add_argument("--current-narration", type=Path)
    parser.add_argument("--current-bgm", type=Path)
    parser.add_argument("--current-bgm-start", type=float, default=0.0)
    parser.add_argument("--narration-gain-db", type=float, default=2.0)
    parser.add_argument("--current-bgm-gain-db", type=float, default=0.0)
    parser.add_argument("--sample-rate", type=int, default=12000)
    parser.add_argument("--ffmpeg", default="ffmpeg")
    args = parser.parse_args()

    for label, path in (("参考混音", args.reference), ("候选 BGM", args.bgm)):
        if not path.is_file():
            raise SystemExit(f"找不到{label}：{path}")

    reference = decode_stereo(
        args.ffmpeg,
        args.reference,
        start=args.start,
        duration=args.duration,
        sample_rate=args.sample_rate,
    )
    bgm = decode_stereo(
        args.ffmpeg,
        args.bgm,
        start=args.bgm_start if args.bgm_start is not None else args.start,
        duration=args.duration,
        sample_rate=args.sample_rate,
    )
    length = min(len(reference), len(bgm))
    reference, bgm = reference[:length], bgm[:length]

    reference_side = (reference[:, 0] - reference[:, 1]) * 0.5
    bgm_side = (bgm[:, 0] - bgm[:, 1]) * 0.5
    denominator = float(np.dot(bgm_side, bgm_side))
    if denominator <= 1e-12:
        raise SystemExit("候选 BGM 没有足够的立体声侧向信息，无法拟合")
    music_gain = float(np.dot(reference_side, bgm_side) / denominator)
    music = bgm * music_gain
    residual = reference - music
    voice = np.mean(residual, axis=1)
    residual_side = (residual[:, 0] - residual[:, 1]) * 0.5
    active_voice, active_music, ratio_db, active_frames = voiced_ratio(
        voice,
        music,
        args.sample_rate,
    )
    report = {
        "schema_version": 1,
        "reference": str(args.reference.resolve()),
        "bgm": str(args.bgm.resolve()),
        "analysis_start_seconds": args.start,
        "analysis_duration_seconds": length / args.sample_rate,
        "sample_rate_hz": args.sample_rate,
        "bgm_side_correlation": round(
            correlation(reference_side, bgm_side), 6
        ),
        "fitted_bgm_gain_linear": round(music_gain, 8),
        "fitted_bgm_gain_db": round(db(abs(music_gain)), 3),
        "residual_left_right_correlation": round(
            correlation(residual[:, 0], residual[:, 1]), 6
        ),
        "residual_side_rms_dbfs": round(db(rms(residual_side)), 3),
        "active_voice_rms_dbfs": round(db(active_voice), 3),
        "active_music_rms_dbfs": round(db(active_music), 3),
        "voice_over_music_db": round(ratio_db, 3),
        "active_frame_count": active_frames,
        "reliable_same_bgm": (
            correlation(reference_side, bgm_side) >= 0.97
            and correlation(residual[:, 0], residual[:, 1]) >= 0.97
        ),
    }
    if args.current_narration or args.current_bgm:
        if not args.current_narration or not args.current_bgm:
            raise SystemExit(
                "--current-narration 与 --current-bgm 必须同时提供"
            )
        for label, path in (
            ("当前朗读", args.current_narration),
            ("当前 BGM", args.current_bgm),
        ):
            if not path.is_file():
                raise SystemExit(f"找不到{label}：{path}")
        current_narration = decode_stereo(
            args.ffmpeg,
            args.current_narration,
            start=0.0,
            duration=args.duration,
            sample_rate=args.sample_rate,
        )
        current_bgm = decode_stereo(
            args.ffmpeg,
            args.current_bgm,
            start=args.current_bgm_start,
            duration=args.duration,
            sample_rate=args.sample_rate,
        )
        current_length = min(len(current_narration), len(current_bgm))
        current_voice = np.mean(current_narration[:current_length], axis=1)
        current_voice *= 10.0 ** (args.narration_gain_db / 20.0)
        current_music = current_bgm[:current_length]
        current_music *= 10.0 ** (args.current_bgm_gain_db / 20.0)
        (
            current_voice_rms,
            current_music_rms,
            current_ratio_db,
            current_active_frames,
        ) = voiced_ratio(current_voice, current_music, args.sample_rate)
        report["current_mix"] = {
            "narration": str(args.current_narration.resolve()),
            "bgm": str(args.current_bgm.resolve()),
            "narration_gain_db": args.narration_gain_db,
            "bgm_gain_db": args.current_bgm_gain_db,
            "active_voice_rms_dbfs": round(db(current_voice_rms), 3),
            "active_music_rms_dbfs": round(db(current_music_rms), 3),
            "voice_over_music_db": round(current_ratio_db, 3),
            "active_frame_count": current_active_frames,
            "additional_bgm_gain_to_match_reference_db": round(
                current_ratio_db - ratio_db,
                3,
            ),
        }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
