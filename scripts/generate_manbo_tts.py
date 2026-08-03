#!/usr/bin/env python3
"""Generate a continuous Manbo narration track through MiloraAPI."""

from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import os
import re
import shutil
import socket
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import unicodedata
from pathlib import Path

FREE_ENDPOINT = "https://api.milorapart.top/apis/mbAIsc"
VIP_ENDPOINT = "https://api.milorapart.top/apis/mbAIscvip"
DEFAULT_API_KEY_ENV = "MILORA_API_KEY"
STRONG_BOUNDARIES = "。！？!?；;"
SOFT_BOUNDARIES = "，,:：、"


def normalize_spoken_text(text: str) -> str:
    return "".join(
        character
        for character in text
        if not character.isspace()
        and not unicodedata.category(character).startswith("P")
    )


def write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def clean_source_text(value: str) -> str:
    value = value.replace("\r\n", "\n").replace("\r", "\n").strip()
    value = re.sub(r"[ \t]+", "", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    if not normalize_spoken_text(value):
        raise ValueError("朗读正文为空")
    return value


def split_at_boundaries(text: str, boundary_marks: str) -> list[str]:
    pieces: list[str] = []
    cursor = 0
    for index, character in enumerate(text):
        if character in boundary_marks or character == "\n":
            piece = text[cursor : index + 1]
            if piece:
                pieces.append(piece)
            cursor = index + 1
    if cursor < len(text):
        pieces.append(text[cursor:])
    return pieces


def split_oversized_unit(unit: str, maximum_chars: int) -> list[str]:
    if len(unit) <= maximum_chars:
        return [unit]
    soft_units = split_at_boundaries(unit, SOFT_BOUNDARIES)
    if len(soft_units) > 1:
        output: list[str] = []
        buffer = ""
        for piece in soft_units:
            if buffer and len(buffer) + len(piece) > maximum_chars:
                output.append(buffer)
                buffer = ""
            if len(piece) > maximum_chars:
                if buffer:
                    output.append(buffer)
                    buffer = ""
                output.extend(
                    piece[index : index + maximum_chars]
                    for index in range(0, len(piece), maximum_chars)
                )
            else:
                buffer += piece
        if buffer:
            output.append(buffer)
        return output
    return [
        unit[index : index + maximum_chars]
        for index in range(0, len(unit), maximum_chars)
    ]


def chunk_text(text: str, maximum_chars: int) -> list[str]:
    if maximum_chars < 80:
        raise ValueError("单段最大字符数不得小于80")
    strong_units = split_at_boundaries(text, STRONG_BOUNDARIES)
    units: list[str] = []
    for unit in strong_units:
        units.extend(split_oversized_unit(unit, maximum_chars))

    chunks: list[str] = []
    buffer = ""
    for unit in units:
        if buffer and len(buffer) + len(unit) > maximum_chars:
            chunks.append(buffer.strip())
            buffer = ""
        buffer += unit
    if buffer.strip():
        chunks.append(buffer.strip())

    if not chunks or any(len(chunk) > maximum_chars for chunk in chunks):
        raise ValueError("正文自然分段失败")
    if normalize_spoken_text("".join(chunks)) != normalize_spoken_text(text):
        raise ValueError("朗读分段未能完整覆盖正文")
    return chunks


def validate_download_url(value: str) -> str:
    parsed = urllib.parse.urlparse(value)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError("API返回了不安全的音频地址")
    if parsed.username or parsed.password:
        raise ValueError("API返回了带身份信息的音频地址")
    hostname = parsed.hostname.lower()
    if hostname in {"localhost", "localhost.localdomain"}:
        raise ValueError("API返回了本机音频地址")
    try:
        addresses = [ipaddress.ip_address(hostname)]
    except ValueError:
        try:
            addresses = [
                ipaddress.ip_address(address[4][0])
                for address in socket.getaddrinfo(
                    hostname,
                    parsed.port or 443,
                    type=socket.SOCK_STREAM,
                )
            ]
        except (OSError, ValueError) as error:
            raise ValueError("无法验证API返回的音频地址") from error
    if not addresses or any(not address.is_global for address in addresses):
        raise ValueError("API返回了内网或非公网音频地址")
    return value


class ValidatingRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Reject a redirect before urllib connects to an unsafe destination."""

    def redirect_request(
        self,
        request: urllib.request.Request,
        file_pointer: object,
        code: int,
        message: str,
        headers: object,
        new_url: str,
    ) -> urllib.request.Request | None:
        validate_download_url(new_url)
        return super().redirect_request(
            request,
            file_pointer,
            code,
            message,
            headers,
            new_url,
        )


def request_audio_url(
    text: str,
    audio_format: str,
    speed: int,
    api_key: str,
    timeout_seconds: float,
) -> str:
    endpoint = VIP_ENDPOINT if api_key else FREE_ENDPOINT
    query = {
        "text": text,
        "format": audio_format,
        "speed": str(speed),
    }
    if api_key:
        query["key"] = api_key
    url = endpoint + "?" + urllib.parse.urlencode(query)
    headers = {
        "Accept": "application/json",
        "User-Agent": "life-evolution-video-maker/1.0",
    }
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        payload = json.loads(response.read().decode("utf-8-sig"))
    if payload.get("code") != 200:
        raise RuntimeError(
            f"曼波API返回错误：{payload.get('msg') or payload.get('code')}"
        )
    audio_url = payload.get("url")
    if not isinstance(audio_url, str) or not audio_url:
        raise RuntimeError("曼波API响应中缺少音频地址")
    return validate_download_url(audio_url)


def download_file(url: str, output: Path, timeout_seconds: float) -> None:
    validate_download_url(url)
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "life-evolution-video-maker/1.0"},
    )
    opener = urllib.request.build_opener(ValidatingRedirectHandler())
    with opener.open(request, timeout=timeout_seconds) as response:
        validate_download_url(response.geturl())
        with output.open("wb") as target:
            shutil.copyfileobj(response, target)
    if output.stat().st_size < 1024:
        raise RuntimeError("下载到的音频文件过小")


def run_checked(command: list[str]) -> None:
    subprocess.run(command, check=True)


def normalize_segment(
    ffmpeg: str,
    source: Path,
    output: Path,
    threshold_db: float,
    join_leading_silence: float,
    join_trailing_silence: float,
) -> None:
    trim_leading = (
        "silenceremove="
        "start_periods=1:start_duration=0.03:"
        f"start_threshold={threshold_db:g}dB:"
        f"start_silence={join_leading_silence:g}"
    )
    trim_trailing_reversed = (
        "silenceremove="
        "start_periods=1:start_duration=0.03:"
        f"start_threshold={threshold_db:g}dB:"
        f"start_silence={join_trailing_silence:g}"
    )
    silence_filter = (
        f"{trim_leading},areverse,"
        f"{trim_trailing_reversed},areverse"
    )
    run_checked(
        [
            ffmpeg,
            "-y",
            "-v",
            "error",
            "-i",
            str(source),
            "-af",
            silence_filter,
            "-ac",
            "1",
            "-ar",
            "48000",
            "-c:a",
            "pcm_s16le",
            str(output),
        ]
    )


def audio_duration(ffprobe: str, audio: Path) -> float:
    result = subprocess.run(
        [
            ffprobe,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(audio),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return float(result.stdout.strip())


def concat_segments(ffmpeg: str, segments: list[Path], output: Path) -> None:
    with tempfile.TemporaryDirectory(prefix="manbo-concat-") as temporary:
        list_path = Path(temporary) / "segments.txt"
        list_path.write_text(
            "\n".join(
                "file '"
                + path.resolve().as_posix().replace("'", "'\\''")
                + "'"
                for path in segments
            )
            + "\n",
            encoding="utf-8",
        )
        run_checked(
            [
                ffmpeg,
                "-y",
                "-v",
                "error",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(list_path),
                "-ac",
                "1",
                "-ar",
                "48000",
                "-c:a",
                "pcm_s16le",
                str(output),
            ]
        )


def prepend_lead_in(
    ffmpeg: str,
    body_audio: Path,
    output: Path,
    lead_in_seconds: float,
) -> None:
    run_checked(
        [
            ffmpeg,
            "-y",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-t",
            f"{lead_in_seconds:.9f}",
            "-i",
            "anullsrc=r=48000:cl=mono",
            "-i",
            str(body_audio),
            "-filter_complex",
            "[0:a][1:a]concat=n=2:v=0:a=1[out]",
            "-map",
            "[out]",
            "-ar",
            "48000",
            "-ac",
            "1",
            "-c:a",
            "pcm_s16le",
            str(output),
        ]
    )


def synthesize_with_retries(
    chunk: str,
    segment_number: int,
    output: Path,
    *,
    api_key: str,
    audio_format: str,
    speed: int,
    timeout_seconds: float,
    retries: int,
    ffmpeg: str,
    ffprobe: str,
    threshold_db: float,
    join_leading_silence: float,
    join_trailing_silence: float,
) -> None:
    errors: list[str] = []
    for attempt in range(1, retries + 1):
        try:
            audio_url = request_audio_url(
                chunk,
                audio_format,
                speed,
                api_key,
                timeout_seconds,
            )
            with tempfile.TemporaryDirectory(
                prefix=f"manbo-{segment_number:02d}-"
            ) as temporary:
                raw = Path(temporary) / f"source.{audio_format}"
                download_file(audio_url, raw, timeout_seconds)
                normalize_segment(
                    ffmpeg,
                    raw,
                    output,
                    threshold_db,
                    join_leading_silence,
                    join_trailing_silence,
                )
            if audio_duration(ffprobe, output) <= 0.2:
                raise RuntimeError("生成的朗读片段为空")
            return
        except (
            OSError,
            ValueError,
            RuntimeError,
            subprocess.CalledProcessError,
            urllib.error.URLError,
            socket.timeout,
        ) as error:
            errors.append(str(error))
            if attempt < retries:
                time.sleep(min(8, 2 ** (attempt - 1)))
    raise RuntimeError(
        f"第{segment_number}段朗读生成失败：{errors[-1]}"
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--segments-dir", type=Path)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--body-output", type=Path)
    parser.add_argument("--plan", type=Path)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--format", choices=("wav", "mp3"))
    parser.add_argument("--speed", type=int)
    parser.add_argument("--max-chars", type=int)
    parser.add_argument("--api-key-env")
    parser.add_argument("--fps", type=int)
    parser.add_argument("--lead-in-frames", type=int)
    parser.add_argument("--timeout-seconds", type=float, default=120)
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--silence-threshold-db", type=float, default=-45)
    parser.add_argument("--join-leading-silence", type=float)
    parser.add_argument("--join-trailing-silence", type=float)
    parser.add_argument("--ffmpeg", default="ffmpeg")
    parser.add_argument("--ffprobe", default="ffprobe")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只输出自然分段计划，不调用API",
    )
    args = parser.parse_args()

    narration_config: dict = {}
    canvas_config: dict = {}
    if args.config:
        config = json.loads(args.config.read_text(encoding="utf-8"))
        narration_config = config.get("narration", {})
        canvas_config = config.get("canvas", {})
    args.format = args.format or narration_config.get("format", "wav")
    args.speed = (
        args.speed
        if args.speed is not None
        else int(narration_config.get("speed", 0))
    )
    args.max_chars = (
        args.max_chars
        if args.max_chars is not None
        else int(narration_config.get("max_chunk_characters", 260))
    )
    args.api_key_env = (
        args.api_key_env
        or narration_config.get("api_key_env", DEFAULT_API_KEY_ENV)
    )
    args.fps = (
        args.fps
        if args.fps is not None
        else int(canvas_config.get("fps", 30))
    )
    args.lead_in_frames = (
        args.lead_in_frames
        if args.lead_in_frames is not None
        else int(narration_config.get("lead_in_frames", 118))
    )
    args.join_leading_silence = (
        args.join_leading_silence
        if args.join_leading_silence is not None
        else float(
            narration_config.get("join_leading_silence_seconds", 0.03)
        )
    )
    args.join_trailing_silence = (
        args.join_trailing_silence
        if args.join_trailing_silence is not None
        else float(
            narration_config.get("join_trailing_silence_seconds", 0.08)
        )
    )

    if not -50 <= args.speed <= 50:
        raise SystemExit("speed必须位于-50到50之间")
    if args.fps <= 0 or args.lead_in_frames < 0:
        raise SystemExit("片头时间参数无效")
    if args.retries < 1:
        raise SystemExit("retries必须大于0")
    if shutil.which(args.ffmpeg) is None or shutil.which(args.ffprobe) is None:
        raise SystemExit("找不到ffmpeg或ffprobe")

    source_text = clean_source_text(
        args.source.read_text(encoding="utf-8-sig")
    )
    chunks = chunk_text(source_text, args.max_chars)
    if args.dry_run:
        for index, chunk in enumerate(chunks, 1):
            print(f"{index:02d} | {len(chunk)}字符 | {chunk}")
        print(f"共{len(chunks)}段，不调用API")
        return 0

    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    segments_dir = (
        args.segments_dir.resolve()
        if args.segments_dir
        else output.parent / "曼波朗读分段"
    )
    segments_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = (
        args.manifest.resolve()
        if args.manifest
        else output.parent / "朗读生成记录.json"
    )
    body_output = (
        args.body_output.resolve()
        if args.body_output
        else output.with_name(output.stem + "_正文.wav")
    )
    api_key = os.environ.get(args.api_key_env, "").strip()
    existing_manifest: dict = {}
    if manifest_path.exists():
        try:
            existing_manifest = json.loads(
                manifest_path.read_text(encoding="utf-8")
            )
        except (OSError, json.JSONDecodeError):
            existing_manifest = {}
    existing_entries = {
        entry.get("text_sha256"): entry
        for entry in existing_manifest.get("segments", [])
        if isinstance(entry, dict)
    }

    segment_paths: list[Path] = []
    manifest_segments: list[dict] = []
    for index, chunk in enumerate(chunks, 1):
        chunk_hash = sha256_text(chunk)
        segment = segments_dir / f"朗读分段_{index:02d}.wav"
        sidecar = segment.with_suffix(".json")
        sidecar_data: dict = {}
        if sidecar.exists():
            try:
                sidecar_data = json.loads(
                    sidecar.read_text(encoding="utf-8")
                )
            except (OSError, json.JSONDecodeError):
                sidecar_data = {}
        cached = existing_entries.get(chunk_hash)
        can_reuse = (
            (
                cached is not None
                or sidecar_data.get("text_sha256") == chunk_hash
            )
            and segment.exists()
            and segment.stat().st_size > 1024
        )
        if can_reuse:
            print(f"复用第{index}/{len(chunks)}段朗读")
        else:
            print(f"生成第{index}/{len(chunks)}段朗读")
            synthesize_with_retries(
                chunk,
                index,
                segment,
                api_key=api_key,
                audio_format=args.format,
                speed=args.speed,
                timeout_seconds=args.timeout_seconds,
                retries=args.retries,
                ffmpeg=args.ffmpeg,
                ffprobe=args.ffprobe,
                threshold_db=args.silence_threshold_db,
                join_leading_silence=args.join_leading_silence,
                join_trailing_silence=args.join_trailing_silence,
            )
            write_json(
                sidecar,
                {
                    "index": index,
                    "characters": len(chunk),
                    "text": chunk,
                    "text_sha256": chunk_hash,
                    "audio_path": segment.name,
                    "audio_sha256": sha256_file(segment),
                    "duration_seconds": audio_duration(
                        args.ffprobe, segment
                    ),
                },
            )
        segment_paths.append(segment)
        manifest_segments.append(
            {
                "index": index,
                "characters": len(chunk),
                "text": chunk,
                "text_sha256": chunk_hash,
                "audio_path": str(segment.relative_to(output.parent)),
                "audio_sha256": sha256_file(segment),
                "duration_seconds": audio_duration(args.ffprobe, segment),
            }
        )

    concat_segments(args.ffmpeg, segment_paths, body_output)
    lead_in_seconds = args.lead_in_frames / args.fps
    prepend_lead_in(args.ffmpeg, body_output, output, lead_in_seconds)
    body_duration = audio_duration(args.ffprobe, body_output)
    final_duration = audio_duration(args.ffprobe, output)
    manifest = {
        "schema_version": 1,
        "provider": "milora_manbo",
        "endpoint_mode": "vip" if api_key else "free",
        "api_key_env": args.api_key_env,
        "source_path": str(args.source.resolve()),
        "source_sha256": sha256_text(source_text),
        "format": args.format,
        "speed": args.speed,
        "max_chunk_characters": args.max_chars,
        "join_policy": {
            "leading_silence_seconds": args.join_leading_silence,
            "trailing_silence_seconds": args.join_trailing_silence,
            "threshold_db": args.silence_threshold_db,
        },
        "lead_in_frames": args.lead_in_frames,
        "fps": args.fps,
        "lead_in_seconds": lead_in_seconds,
        "body_audio_path": str(body_output.relative_to(output.parent)),
        "body_audio_sha256": sha256_file(body_output),
        "output_audio_path": str(output.relative_to(output.parent)),
        "output_audio_sha256": sha256_file(output),
        "body_duration_seconds": body_duration,
        "output_duration_seconds": final_duration,
        "segments": manifest_segments,
    }
    write_json(manifest_path, manifest)
    if args.plan:
        plan_path = args.plan.resolve()
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        try:
            narration_path = str(output.relative_to(plan_path.parent))
        except ValueError:
            narration_path = str(output)
        plan["narration_provider"] = "milora_manbo"
        plan["narration_audio_path"] = narration_path
        try:
            plan["narration_manifest_path"] = str(
                manifest_path.relative_to(plan_path.parent)
            )
        except ValueError:
            plan["narration_manifest_path"] = str(manifest_path)
        plan["production_status"] = "narration_generated"
        write_json(plan_path, plan)
    print(
        f"朗读生成完成：{len(chunks)}段，"
        f"正文{body_duration:.3f}秒，成片音频{final_duration:.3f}秒"
    )
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
