#!/usr/bin/env python3
"""Transcribe narration with local Faster-Whisper word timestamps."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("audio", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--source", type=Path)
    parser.add_argument(
        "--runtime",
        type=Path,
        default=Path("tools/faster-whisper-runtime"),
    )
    parser.add_argument(
        "--download-root",
        type=Path,
        default=Path("tools/faster-whisper-models"),
    )
    parser.add_argument("--model", default="small")
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--compute-type", default="int8")
    parser.add_argument("--beam-size", type=int, default=5)
    args = parser.parse_args()

    runtime = args.runtime.resolve()
    if not runtime.exists():
        raise SystemExit(f"缺少Faster-Whisper运行环境：{runtime}")
    sys.path.insert(0, str(runtime))

    from faster_whisper import WhisperModel

    prompt = None
    if args.source:
        prompt = args.source.read_text(encoding="utf-8-sig").strip()
    args.download_root.mkdir(parents=True, exist_ok=True)
    model = WhisperModel(
        args.model,
        device=args.device,
        compute_type=args.compute_type,
        download_root=str(args.download_root.resolve()),
    )
    segments_generator, info = model.transcribe(
        str(args.audio.resolve()),
        language="zh",
        task="transcribe",
        beam_size=args.beam_size,
        word_timestamps=True,
        condition_on_previous_text=True,
        initial_prompt=prompt,
        vad_filter=False,
    )
    segments = []
    all_words = []
    for segment in segments_generator:
        words = [
            {
                "start": word.start,
                "end": word.end,
                "text": word.word,
                "probability": word.probability,
            }
            for word in (segment.words or [])
        ]
        segments.append(
            {
                "id": segment.id,
                "start": segment.start,
                "end": segment.end,
                "text": segment.text,
                "avg_logprob": segment.avg_logprob,
                "no_speech_prob": segment.no_speech_prob,
                "words": words,
            }
        )
        all_words.extend(words)
        print(
            f"[{segment.start:.2f}-{segment.end:.2f}] {segment.text}",
            flush=True,
        )

    payload = {
        "schema_version": 1,
        "engine": "faster-whisper",
        "model": args.model,
        "device": args.device,
        "compute_type": args.compute_type,
        "language": info.language,
        "language_probability": info.language_probability,
        "audio_duration_seconds": info.duration,
        "transcription_duration_seconds": info.duration_after_vad,
        "segments": segments,
        "words": all_words,
        "transcript": "".join(segment["text"] for segment in segments),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"已写入{len(all_words)}个词级时间戳：{args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
