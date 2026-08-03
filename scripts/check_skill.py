#!/usr/bin/env python3
"""Run a read-only health check for the life-evolution video skill."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

sys.dont_write_bytecode = True


REQUIRED_FILES = (
    "SKILL.md",
    "agents/openai.yaml",
    "assets/default_config.json",
    "assets/background.png",
    "assets/brand-avatar.png",
    "assets/bgm.mp3",
    "assets/bgm/catalog.json",
    "assets/bgm/mix-ratio-calibration.json",
    "assets/bgm/option-a.mp3",
    "assets/bgm/option-b-suxi.mp3",
    "assets/bgm/previews/option-a-preview.mp3",
    "assets/bgm/previews/option-b-preview.mp3",
    "assets/fonts/SourceHanSerifSC-Bold.otf",
    "assets/style-reference/catalog.json",
    "assets/approved-samples/catalog.json",
    "references/style-guide.md",
    "references/subtitle-segmentation.md",
    "references/workflow-and-review.md",
    "references/manifest-schema.md",
    "references/audio-alignment.md",
    "references/image-generation.md",
    "scripts/test_review_interactions.cjs",
    "tests/fixtures/review-schema2/config.json",
    "tests/fixtures/review-schema2/plan.json",
    "tests/fixtures/review-schema2/approval.json",
    "tests/fixtures/review-schema2/subtitles.srt",
)

LEGACY_SCRIPTS = {
    "create_regression_fixture.py",
    "package_deliverables.py",
}

REVIEW_MARKERS = (
    "selection-preview",
    "captureTextSelection(key,true)",
    "layout.characterSizeScales",
    "片头文字和换行都可以修改",
    "state.introText.trim().length>0",
    "line-spacing-handle",
    "letter-spacing-handle",
    "clearCanvasSelection",
    "adjustCharacterSize(key,.1)",
    "character_size_scales",
    "copyApproval",
    "layerBoundsInCanvas",
    'keepTitleAnchor(key,"left",startBounds)',
    'keepTitleAnchor(key,"center",startBounds)',
    "repairPersistedLetterSpacing",
    "refreshTitleCharacterStyles",
    "layout.lineSpacing=clamp(startLayout.lineSpacing*factor,0,200)",
    "layout.letterSpacing=clamp(startLayout.letterSpacing*factor,-20,80)",
    'id="introModeOptions"',
    "state.includeIntro",
    "include_intro:state.includeIntro",
)
REVIEW_BGM_MARKERS = (
    "state.selectedBgmId",
    "selected_bgm_id:state.selectedBgmId",
    'id="bgmOptions"',
)
REVIEW_GENERATOR_FORBIDDEN_MARKERS = (
    'id="subtitleInput"',
    "subtitleCandidates",
    "selected_subtitle:state.subtitle.trim()",
)

FIXED_BRAND_AVATAR_SHA256 = (
    "AD4202AC1A0E29EF8919E5615AB8C9CCE9DB175F7A833FF94615DEF6304EF344"
)
FIXED_LEFT_BRAND_LAYOUT = {
    "text_source": "plan.title",
    "avatar_asset": "brand-avatar.png",
    "avatar_size_px": 64,
    "avatar_margin_x": 12,
    "avatar_margin_y": 12,
    "text_gap_px": 14,
    "text_y_px": 29,
    "pixel_font_size": 31,
    "anchor": "top_left",
}
FIXED_BGM_B = {
    "label": "选项 B",
    "name": "溯溪",
    "asset": "option-b-suxi.mp3",
    "preview_asset": "previews/option-b-preview.mp3",
    "body_start_seconds": 0.225147,
    "detected_leading_silence_seconds": 0.225147,
    "sample_rate_hz": 44100,
    "channels": 2,
    "bit_rate": 192001,
    "sha256": "BE53022DD547A6EE1B3A519E2F8DF819548C49F3D370A284CCC4FEE8C6552B3F",
    "preview_sha256": "EEB39584DB45970CCEA184C4212A6B84479E0D0B181720DF2AD345C90CD9057B",
}

SKILL_POLICY_MARKERS = {
    "SKILL.md": (
        "此安装已记录用户的长期明确授权",
        "无需再次询问",
        "该授权只覆盖正文发送至本 Skill 配置的曼波接口",
        "二维平面简笔画为硬性生成方向",
        "例外只在生成后判断",
        "整组画风漂移",
        "最多3张在途",
        "禁止固定批次屏障",
        "avoidable_ready_idle_seconds = 0",
        "task_package_ready_to_first_request_seconds",
        "图片后处理流水线",
        "详细耗时报告不得阻塞审核页",
        "不得因为60秒工具等待上限中止",
        "不得把重叠请求耗时之和当作真实墙钟耗时",
        "实际切换保留/不要片头",
        "完成片头文案编辑与换行",
        "横竖双封面分别编辑文案与换行",
        "真实的自动点击、拖动与断言",
        "头像 + 本期主标题",
        "字号必须与右上角品牌语完全一致",
        "审核页选择正文 BGM",
        "审核页选择是否保留前118帧片头",
        "保留片头时前118帧声音固定沿用选项 A",
        "无片头时正文画面、人声与所选 BGM 从0秒同步开始",
        "正文第118帧与所选 BGM 的首个有效声音同步",
        "永久曲库选项 B“溯溪”",
        "正文背景音乐 `+7.1 dB`",
        "人声平均高于 BGM 约 `4.1 dB`",
        "完整保留 BGM 立体声",
    ),
    "references/style-guide.md": (
        "平面原则与局部例外",
        "3D、雕塑、玩具或写实插画不是可切换的第二套风格",
        "正文固定品牌栏",
        "本期主标题",
        "plan.title",
    ),
    "references/image-generation.md": (
        "连续滚动任务池（禁止固定批次屏障）",
        "first_completed(active)",
        "enqueue_postprocess(finished)",
        "图片后处理流水线",
        "task_package_ready_to_first_request_seconds",
        "详细耗时报告不得阻塞审核页",
        "avoidable_ready_idle_seconds",
        "不改变原生图片模型、完整提示词、参考图、原始分辨率、审核标准或输出质量",
        "平面为原则，例外只在成图后判断",
        "整组风格漂移",
        "隐喻创意与3D风格是两个独立判断",
    ),
    "references/workflow-and-review.md": (
        "配图调度：消除批次空转",
        "任务包完成后立即发出首批",
        "任一张返回后，先做最小计时和路径记录，再立即补发下一张已就绪任务",
        "复制、背景归一化和单图初审",
        "不得把重叠请求耗时之和当作真实总耗时",
        "审核页优先交付",
        "详细耗时报告不得阻塞审核页",
        "不得因为60秒工具等待上限中止",
        "配图审核：平面为原则",
        "局部例外",
        "整组风格漂移",
        "固定审核页回归",
        "不得再以功能代码存在、页面脚本语法正确或人工试过一次代替自动交互回归",
        "固定品牌栏不进入审核页或审批数据",
        "审核页选择正文 BGM",
        "审核页选择是否保留片头",
        "保留片头时，片头声音不受 BGM 选择影响",
        "不要片头时",
        "使正文开始与 BGM 起声严格同步",
        "参考中人声高于背景音乐约4.08 dB",
        "最终混音只提高正文 BGM",
        "输出48kHz、192kbps双声道AAC",
    ),
}
SKILL_POLICY_FORBIDDEN_MARKERS = {
    "references/image-generation.md": (
        "保持系列画风，但允许模型使用恰当的渐变",
    ),
}
INVALID_FILENAME_CHARACTERS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


class HealthCheck:
    def __init__(self) -> None:
        self.passed = 0
        self.warnings: list[str] = []
        self.failures: list[str] = []

    def pass_check(self, message: str) -> None:
        self.passed += 1
        print(f"[通过] {message}")

    def warn(self, message: str) -> None:
        self.warnings.append(message)
        print(f"[提醒] {message}")

    def fail(self, message: str) -> None:
        self.failures.append(message)
        print(f"[失败] {message}")

    def finish(self) -> int:
        print(
            "\n体检结果："
            f"{self.passed}项通过，"
            f"{len(self.warnings)}项提醒，"
            f"{len(self.failures)}项失败"
        )
        return 1 if self.failures else 0


def load_json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def safe_filename(value: str) -> str:
    cleaned = INVALID_FILENAME_CHARACTERS.sub("＿", value).strip().rstrip(". ")
    return cleaned or "未命名视频"


def check_skill_metadata(root: Path, health: HealthCheck) -> None:
    skill_path = root / "SKILL.md"
    text = skill_path.read_text(encoding="utf-8-sig")
    parts = text.split("---", 2)
    if len(parts) != 3:
        health.fail("SKILL.md 缺少完整的 YAML 头部")
        return
    fields: dict[str, str] = {}
    for raw_line in parts[1].splitlines():
        if ":" not in raw_line:
            continue
        key, value = raw_line.split(":", 1)
        fields[key.strip()] = value.strip()
    if fields.get("name") != "life-evolution-video-maker":
        health.fail("SKILL.md 的 name 不正确")
    elif not fields.get("description"):
        health.fail("SKILL.md 的 description 为空")
    else:
        missing_policy: list[str] = []
        for relative, markers in SKILL_POLICY_MARKERS.items():
            policy_text = (root / relative).read_text(encoding="utf-8-sig")
            absent = [marker for marker in markers if marker not in policy_text]
            if absent:
                missing_policy.append(relative)
        forbidden_policy: list[str] = []
        for relative, markers in SKILL_POLICY_FORBIDDEN_MARKERS.items():
            policy_text = (root / relative).read_text(encoding="utf-8-sig")
            if any(marker in policy_text for marker in markers):
                forbidden_policy.append(relative)
        if missing_policy:
            health.fail(
                "Skill 固定规则不完整："
                + "、".join(sorted(set(missing_policy)))
            )
        elif forbidden_policy:
            health.fail(
                "Skill 仍在默认提示词中主动邀请立体画风："
                + "、".join(sorted(set(forbidden_policy)))
            )
        else:
            health.pass_check("Skill 名称、触发描述与固定规则有效")

    agent_path = root / "agents" / "openai.yaml"
    agent_text = agent_path.read_text(encoding="utf-8-sig")
    if "$life-evolution-video-maker" not in agent_text:
        health.fail("agents/openai.yaml 的默认提示未引用 Skill 名称")
    else:
        health.pass_check("Skill 界面元数据与调用名称一致")


def check_required_files(root: Path, health: HealthCheck) -> None:
    missing = [relative for relative in REQUIRED_FILES if not (root / relative).is_file()]
    if missing:
        health.fail("缺少固定文件：" + "、".join(missing))
    else:
        health.pass_check("固定配置、资产和参考文件齐全")


def check_config(root: Path, health: HealthCheck) -> None:
    config = load_json(root / "assets" / "default_config.json")
    if not isinstance(config, dict):
        health.fail("default_config.json 不是 JSON 对象")
        return
    failures: list[str] = []
    if config.get("brand_text") != "个体感受丨时代观察丨生活思考":
        failures.append("右上角文字")
    text_layers = config.get("text_layers", {})
    left_brand = text_layers.get("top_left", {})
    right_brand = text_layers.get("top_right", {})
    for key, expected in FIXED_LEFT_BRAND_LAYOUT.items():
        if left_brand.get(key) != expected:
            failures.append(f"左上角固定品牌栏 {key}")
    avatar_path = root / "assets" / "brand-avatar.png"
    if avatar_path.is_file() and sha256(avatar_path).upper() != FIXED_BRAND_AVATAR_SHA256:
        failures.append("左上角固定头像哈希")
    if left_brand.get("pixel_font_size") != right_brand.get("pixel_font_size"):
        failures.append("左右品牌文字字号一致性")
    if config.get("timing", {}).get("subtitle_profile") != "privacy_reference_v1":
        failures.append("音频生成前字幕状态")
    variants = config.get("cover", {}).get("variants", {})
    if (
        variants.get("landscape", {}).get("width"),
        variants.get("landscape", {}).get("height"),
    ) != (1660, 1242):
        failures.append("横版封面尺寸")
    if (
        variants.get("portrait", {}).get("width"),
        variants.get("portrait", {}).get("height"),
    ) != (1242, 1660):
        failures.append("竖版封面尺寸")
    audio = config.get("audio", {})
    if (
        audio.get("narration_volume_db") != 2.0
        or audio.get("intro_volume_db") != 0.0
        or audio.get("volume_db") != 7.1
        or audio.get("target_voice_over_bgm_db") != 4.08
        or audio.get("mix_channel_layout") != "stereo"
    ):
        failures.append("朗读与背景音乐音量")
    if audio.get("bgm_catalog_asset") != "bgm/catalog.json":
        failures.append("BGM 曲库")
    if audio.get("default_bgm_id") != "A":
        failures.append("默认 BGM 选项")
    if config.get("output", {}).get("audio_sample_rate") != 48000:
        failures.append("最终音频采样率")
    if failures:
        health.fail("默认配置不符合已确认方案：" + "、".join(failures))
    else:
        health.pass_check("默认配置符合已确认的画面、声音和封面规则")


def check_catalog(root: Path, relative: str, health: HealthCheck) -> None:
    catalog_path = root / relative
    catalog = load_json(catalog_path)
    if not isinstance(catalog, dict) or not isinstance(catalog.get("items"), list):
        health.fail(f"{relative} 格式无效")
        return
    items = catalog["items"]
    problems: list[str] = []
    if catalog.get("count") != len(items):
        problems.append("声明数量与实际数量不同")
    ids = [str(item.get("id", "")) for item in items if isinstance(item, dict)]
    if len(ids) != len(set(ids)):
        problems.append("存在重复编号")
    for item in items:
        if not isinstance(item, dict):
            problems.append("存在无效条目")
            continue
        file_path = catalog_path.parent / str(item.get("file", ""))
        if not file_path.is_file():
            problems.append(f"缺少 {item.get('file')}")
            continue
        if sha256(file_path).lower() != str(item.get("sha256", "")).lower():
            problems.append(f"{item.get('id')} 哈希不一致")
    if problems:
        health.fail(f"{relative}：" + "；".join(problems))
    else:
        health.pass_check(f"{relative} 的数量、编号、文件和哈希一致")


def check_bgm_catalog(root: Path, health: HealthCheck) -> None:
    catalog_path = root / "assets" / "bgm" / "catalog.json"
    catalog = load_json(catalog_path)
    problems: list[str] = []
    if not isinstance(catalog, dict):
        health.fail("BGM 曲库格式无效")
        return
    options = catalog.get("options")
    if not isinstance(options, list):
        health.fail("BGM 曲库缺少 options")
        return
    ids = [str(option.get("id", "")) for option in options if isinstance(option, dict)]
    if ids != ["A", "B"]:
        problems.append("选项必须依次为 A、B")
    if catalog.get("default_id") != "A" or catalog.get("intro_source_id") != "A":
        problems.append("默认与片头固定音源必须为 A")
    if abs(float(catalog.get("intro_duration_seconds", 0)) - 3.933) > 0.001:
        problems.append("片头固定时长必须为 3.933 秒")
    option_b = next(
        (
            option
            for option in options
            if isinstance(option, dict) and str(option.get("id")) == "B"
        ),
        None,
    )
    if option_b is None:
        problems.append("缺少固定选项 B 溯溪")
    else:
        for key, expected in FIXED_BGM_B.items():
            if key == "preview_sha256":
                continue
            actual = option_b.get(key)
            if isinstance(expected, float):
                try:
                    matched = abs(float(actual) - expected) <= 0.000001
                except (TypeError, ValueError):
                    matched = False
            else:
                matched = actual == expected
            if not matched:
                problems.append(f"固定选项 B 的 {key} 被改变")
    ffmpeg = shutil.which("ffmpeg")
    for option in options:
        if not isinstance(option, dict):
            problems.append("存在无效选项")
            continue
        asset = catalog_path.parent / str(option.get("asset", ""))
        preview = catalog_path.parent / str(option.get("preview_asset", ""))
        if not asset.is_file():
            problems.append(f"{option.get('id')} 缺少完整音频")
        elif sha256(asset).upper() != str(option.get("sha256", "")).upper():
            problems.append(f"{option.get('id')} 完整音频哈希不一致")
        if not preview.is_file():
            problems.append(f"{option.get('id')} 缺少试听片段")
        elif option.get("id") == "B" and sha256(preview).upper() != FIXED_BGM_B[
            "preview_sha256"
        ]:
            problems.append("固定选项 B 试听文件哈希不一致")
        try:
            body_start = float(option.get("body_start_seconds", 0))
        except (TypeError, ValueError):
            problems.append(f"{option.get('id')} 正文起播点无效")
            continue
        if body_start < 0:
            problems.append(f"{option.get('id')} 正文起播点不能小于0")
        detected = option.get("detected_leading_silence_seconds")
        if detected is not None and abs(body_start - float(detected)) > 0.001:
            problems.append(f"{option.get('id')} 正文起播点未对齐已检测静音")
        if detected is not None and ffmpeg and asset.is_file():
            threshold = float(option.get("silence_detection_threshold_db", -50))
            result = subprocess.run(
                [
                    ffmpeg,
                    "-hide_banner",
                    "-nostats",
                    "-t",
                    "12",
                    "-i",
                    str(asset),
                    "-af",
                    f"silencedetect=noise={threshold:g}dB:d=0.02",
                    "-f",
                    "null",
                    "-",
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            messages = result.stdout + result.stderr
            start_match = re.search(r"silence_start:\s*([0-9.]+)", messages)
            end_match = re.search(r"silence_end:\s*([0-9.]+)", messages)
            measured = (
                float(end_match.group(1))
                if start_match
                and end_match
                and float(start_match.group(1)) <= 0.001
                else 0.0
            )
            if abs(measured - float(detected)) > 0.03:
                problems.append(
                    f"{option.get('id')} 实测开头静音{measured:.3f}秒，"
                    f"目录记录为{float(detected):.3f}秒"
                )
            trimmed = subprocess.run(
                [
                    ffmpeg,
                    "-hide_banner",
                    "-nostats",
                    "-i",
                    str(asset),
                    "-af",
                    f"atrim=start={body_start:.6f}:duration=0.5,"
                    "asetpts=PTS-STARTPTS,"
                    f"silencedetect=noise={threshold:g}dB:d=0.02",
                    "-f",
                    "null",
                    "-",
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            trimmed_messages = trimmed.stdout + trimmed.stderr
            trimmed_start = re.search(
                r"silence_start:\s*([0-9.]+)", trimmed_messages
            )
            if trimmed_start and float(trimmed_start.group(1)) <= 0.001:
                problems.append(
                    f"{option.get('id')} 从正文起播点读取后仍有可检测空白"
                )
    if problems:
        health.fail("BGM 曲库：" + "；".join(problems))
    else:
        health.pass_check(
            "BGM A/B 完整音频、试听片段、哈希与片头固定规则一致，"
            "选项 B 溯溪已防回退锁定"
        )


def check_mix_calibration(root: Path, health: HealthCheck) -> None:
    path = root / "assets" / "bgm" / "mix-ratio-calibration.json"
    calibration = load_json(path)
    expected = {
        "reference_sha256": "9B5C7D711A0119C0991AAEADDB1D10FF55BC5A058046187BA29834B95888B80B",
        "reference_voice_over_music_db": 4.077,
        "required_additional_body_bgm_gain_db": 7.09,
        "adopted_body_bgm_gain_db": 7.1,
        "intro_bgm_gain_db": 0.0,
        "narration_gain_db": 2.0,
    }
    mismatched = [
        key for key, value in expected.items() if calibration.get(key) != value
    ]
    if calibration.get("bgm_side_correlation", 0) < 0.999:
        mismatched.append("bgm_side_correlation")
    if mismatched:
        health.fail("参考混音比例校准记录不一致：" + "、".join(mismatched))
    else:
        health.pass_check("参考混音确认人声高于 BGM 约4.08dB，正文 BGM 增益为+7.1dB")


def check_images(root: Path, health: HealthCheck) -> None:
    try:
        from PIL import Image
    except ImportError:
        health.fail("缺少 Pillow，无法读取图片资产")
        return
    image_paths: list[Path] = []
    for relative in (
        "assets/background.png",
        "assets/brand-avatar.png",
        "assets/cover-reference.png",
    ):
        path = root / relative
        if path.is_file():
            image_paths.append(path)
    for folder in (
        root / "assets" / "style-reference",
        root / "assets" / "approved-samples",
    ):
        image_paths.extend(
            path
            for path in folder.iterdir()
            if path.is_file()
            and path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}
        )
    failures: list[str] = []
    for path in image_paths:
        try:
            with Image.open(path) as image:
                image.verify()
        except Exception as error:  # noqa: BLE001 - aggregate health report
            failures.append(f"{path.name}: {error}")
    if failures:
        health.fail("图片资产无法解码：" + "；".join(failures))
    else:
        health.pass_check(f"{len(image_paths)}张固定图片均可正常读取")


def check_python_scripts(root: Path, health: HealthCheck) -> None:
    script_dir = root / "scripts"
    failures: list[str] = []
    scripts = sorted(script_dir.glob("*.py"))
    for path in scripts:
        try:
            compile(
                path.read_text(encoding="utf-8-sig"),
                str(path),
                "exec",
            )
        except SyntaxError as error:
            failures.append(f"{path.name}:{error.lineno} {error.msg}")
    if failures:
        health.fail("Python 脚本语法错误：" + "；".join(failures))
    else:
        health.pass_check(f"{len(scripts)}个 Python 脚本语法有效")

    present_legacy = sorted(
        path.name for path in scripts if path.name in LEGACY_SCRIPTS
    )
    if present_legacy:
        health.fail("仍存在已停用的旧脚本：" + "、".join(present_legacy))
    else:
        health.pass_check("旧回归生成器和旧打包脚本已停用")

    cache_files = list(script_dir.glob("__pycache__/*.pyc"))
    if cache_files:
        health.warn(f"工作目录中有{len(cache_files)}个 Python 缓存文件")


def check_review_generator(root: Path, health: HealthCheck) -> None:
    generator = root / "scripts" / "generate_review.py"
    text = generator.read_text(encoding="utf-8-sig")
    missing = [
        marker
        for marker in REVIEW_MARKERS + REVIEW_BGM_MARKERS
        if marker not in text
    ]
    forbidden = [
        marker for marker in REVIEW_GENERATOR_FORBIDDEN_MARKERS if marker in text
    ]
    if missing:
        health.fail("审核页生成器缺少交互逻辑：" + "、".join(missing))
    elif forbidden:
        health.fail(
            "审核页生成器仍包含已停用的副标题控件或数据："
            + "、".join(forbidden)
        )
    else:
        health.pass_check(
            "审核页生成器排除固定品牌栏，并保留片头与双封面文案编辑、空白取消选择、"
            "锚点稳定的三向控制框、强调色、单字字号和复制反馈逻辑"
        )


def locate_playwright() -> Path | None:
    configured = os.environ.get("REVIEW_PLAYWRIGHT_PATH")
    candidates = [
        Path(configured) if configured else None,
        Path(sys.executable).resolve().parent.parent
        / "node"
        / "node_modules"
        / "playwright",
    ]
    node = shutil.which("node")
    if node:
        node_path = Path(node).resolve()
        candidates.extend(
            [
                node_path.parent / "node_modules" / "playwright",
                node_path.parent.parent / "node_modules" / "playwright",
            ]
        )
    for candidate in candidates:
        if candidate and (candidate / "package.json").is_file():
            return candidate
    return None


def check_review_interactions(
    root: Path,
    health: HealthCheck,
    *,
    contract_only: bool,
) -> None:
    node = shutil.which("node")
    if node is None:
        health.fail("缺少 Node.js，无法运行审核页自动交互回归")
        return
    script = root / "scripts" / "test_review_interactions.cjs"
    syntax = subprocess.run(
        [node, "--check", str(script)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if syntax.returncode:
        health.fail("审核页自动交互脚本语法错误：" + syntax.stderr.strip())
        return

    command = [
        node,
        str(script),
        "--root",
        str(root),
        "--python",
        sys.executable,
    ]
    if contract_only:
        command.append("--contract-only")
    else:
        playwright = locate_playwright()
        if playwright is None:
            health.fail(
                "找不到 Playwright，无法实际点击和拖动固定审核页；"
                "可通过 REVIEW_PLAYWRIGHT_PATH 指定模块目录"
            )
            return
        command.extend(["--playwright-path", str(playwright)])

    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode:
        details = (result.stderr or result.stdout).strip()
        health.fail("固定审核页自动交互回归失败：" + details)
        return
    try:
        report = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        health.fail(f"审核页自动交互报告不是有效 JSON：{error}")
        return
    if report.get("passed") is not True:
        health.fail("审核页自动交互报告没有通过")
        return
    checks = report.get("checks")
    count = len(checks) if isinstance(checks, list) else 0
    if contract_only:
        health.pass_check("独立 schema 2 固定项目可重新生成审核页")
        health.warn(
            "本次明确跳过了浏览器自动点击与拖动；"
            "此结果不能用于固定正式 Skill"
        )
    else:
        health.pass_check(
            f"独立 schema 2 审核页完成{count}项自动点击、拖动与状态断言"
        )


def check_tts_safety_and_chunking(root: Path, health: HealthCheck) -> None:
    script_path = root / "scripts" / "generate_manbo_tts.py"
    specification = importlib.util.spec_from_file_location(
        "life_evolution_manbo_healthcheck",
        script_path,
    )
    if specification is None or specification.loader is None:
        health.fail("无法载入曼波朗读脚本")
        return
    module = importlib.util.module_from_spec(specification)
    try:
        specification.loader.exec_module(module)
        module.validate_download_url("https://8.8.8.8/audio.wav")
        unsafe_urls = (
            "http://8.8.8.8/audio.wav",
            "https://localhost/audio.wav",
            "https://127.0.0.1/audio.wav",
            "https://[::1]/audio.wav",
            "https://user:password@8.8.8.8/audio.wav",
        )
        for url in unsafe_urls:
            try:
                module.validate_download_url(url)
            except ValueError:
                continue
            raise AssertionError(f"未拒绝不安全地址：{url}")

        original_getaddrinfo = module.socket.getaddrinfo
        try:
            module.socket.getaddrinfo = lambda *args, **kwargs: [
                (
                    module.socket.AF_INET,
                    module.socket.SOCK_STREAM,
                    6,
                    "",
                    ("10.0.0.8", 443),
                )
            ]
            try:
                module.validate_download_url(
                    "https://private.example/audio.wav"
                )
            except ValueError:
                pass
            else:
                raise AssertionError("未拒绝解析到内网的下载地址")
        finally:
            module.socket.getaddrinfo = original_getaddrinfo

        sample = (
            "这是一段用于检查自然分段的完整句子，"
            "必须保留标点并完整覆盖正文。"
        ) * 24
        chunks = module.chunk_text(sample, 260)
        if len(chunks) < 2 or any(len(chunk) > 260 for chunk in chunks):
            raise AssertionError("朗读分段数量或长度不正确")
        if module.normalize_spoken_text("".join(chunks)) != (
            module.normalize_spoken_text(sample)
        ):
            raise AssertionError("朗读分段没有完整覆盖正文")
    except Exception as error:  # noqa: BLE001 - aggregate health report
        health.fail(f"曼波朗读安全与自然分段自检失败：{error}")
    else:
        health.pass_check("曼波朗读会拒绝内网地址并完整自然分段")


def package_files(root: Path) -> dict[str, Path]:
    files: dict[str, Path] = {}
    for relative in ("SKILL.md",):
        path = root / relative
        if path.is_file():
            files[relative] = path
    for folder_name in ("agents", "assets", "references", "scripts", "tests"):
        folder = root / folder_name
        if not folder.exists():
            continue
        for path in folder.rglob("*"):
            if not path.is_file() or "__pycache__" in path.parts:
                continue
            files[path.relative_to(root).as_posix()] = path
    return files


def compare_skill_roots(
    root: Path,
    compare_root: Path,
    health: HealthCheck,
) -> None:
    if not compare_root.is_dir():
        health.fail(f"正式安装目录不存在：{compare_root}")
        return
    source_files = package_files(root)
    installed_files = package_files(compare_root)
    all_paths = sorted(set(source_files) | set(installed_files))
    differences: list[str] = []
    for relative in all_paths:
        source = source_files.get(relative)
        installed = installed_files.get(relative)
        if source is None:
            differences.append(f"仅安装版有 {relative}")
        elif installed is None:
            differences.append(f"安装版缺少 {relative}")
        elif sha256(source) != sha256(installed):
            differences.append(f"内容不同 {relative}")
    if differences:
        health.fail("工作区与正式安装版不一致：" + "；".join(differences))
    else:
        health.pass_check("工作区与正式安装版逐文件一致")


def check_review_html(
    review_path: Path,
    health: HealthCheck,
    *,
    require_latest_interactions: bool,
) -> None:
    text = review_path.read_text(encoding="utf-8-sig")
    missing = [marker for marker in REVIEW_MARKERS if marker not in text]
    if missing and require_latest_interactions:
        health.warn(
            f"{review_path.parent.parent.name} 的历史审核页尚未包含最新交互："
            + "、".join(missing)
        )
    start = text.find("<script>")
    end = text.find("</script>", start + 8)
    if start < 0 or end < 0:
        health.fail(f"{review_path} 缺少可检查的脚本")
        return
    node = shutil.which("node")
    if node is None:
        health.fail("缺少 Node.js，无法检查审核页脚本")
        return
    result = subprocess.run(
        [node, "--check"],
        input=text[start + 8 : end],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode:
        health.fail(f"{review_path} 脚本语法错误：{result.stderr.strip()}")
    else:
        health.pass_check(f"{review_path.parent.parent.name} 审核页脚本有效")


def check_projects(
    root: Path,
    projects_root: Path,
    health: HealthCheck,
) -> None:
    validator = root / "scripts" / "validate_project.py"
    project_dirs = sorted(
        plan.parent
        for plan in projects_root.glob("*/项目文件/plan.json")
        if (plan.parent / "approval.json").is_file()
        and (plan.parent / "config.json").is_file()
    )
    if not project_dirs:
        health.warn("没有找到可回归的正式项目目录")
        return
    for project_dir in project_dirs:
        plan = load_json(project_dir / "plan.json")
        result = subprocess.run(
            [
                sys.executable,
                "-X",
                "utf8",
                "-B",
                str(validator),
                str(project_dir / "plan.json"),
                str(project_dir / "approval.json"),
                str(project_dir / "config.json"),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if result.returncode:
            health.fail(
                f"{project_dir.parent.name} 项目验证失败："
                + (result.stdout or result.stderr).strip()
            )
        else:
            health.pass_check(f"{project_dir.parent.name} 项目验证通过")
        review_path = project_dir / "review.html"
        if review_path.is_file():
            approval = load_json(project_dir / "approval.json")
            approval_schema = (
                approval.get("schema_version")
                if isinstance(approval, dict)
                else None
            )
            check_review_html(
                review_path,
                health,
                require_latest_interactions=approval_schema == 2,
            )
        else:
            health.warn(f"{project_dir.parent.name} 没有审核页可供回归")

        if not isinstance(plan, dict):
            health.fail(f"{project_dir.parent.name} 的 plan.json 不是对象")
            continue
        title = safe_filename(str(plan.get("title", "")))
        required_names = {
            "项目文件",
            f"{title}.mp4",
            f"（封面）{title}.png",
        }
        if plan.get("schema_version") == 2:
            required_names.add(f"（封面-竖版）{title}.png")
        allowed_names = required_names | {
            f"（封面-竖版）{title}.png",
            "desktop.ini",
        }
        actual_names = {
            path.name for path in project_dir.parent.iterdir()
        }
        missing_names = sorted(required_names - actual_names)
        unexpected_names = sorted(actual_names - allowed_names)
        if missing_names or unexpected_names:
            details: list[str] = []
            if missing_names:
                details.append("缺少 " + "、".join(missing_names))
            if unexpected_names:
                details.append("外层多出 " + "、".join(unexpected_names))
            health.fail(
                f"{project_dir.parent.name} 发布目录不干净："
                + "；".join(details)
            )
        else:
            health.pass_check(
                f"{project_dir.parent.name} 外层只保留成片、封面和项目文件"
            )


def check_tools(health: HealthCheck) -> None:
    missing = [
        tool
        for tool in ("ffmpeg", "ffprobe", "node")
        if shutil.which(tool) is None
    ]
    if missing:
        health.fail("缺少运行工具：" + "、".join(missing))
    else:
        health.pass_check("ffmpeg、ffprobe 和 Node.js 均可用")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "只读检查 Skill、资产、脚本和现有正式项目；"
            "审核页回归只写系统临时目录。"
        )
    )
    parser.add_argument(
        "skill_root",
        nargs="?",
        type=Path,
        default=Path(__file__).resolve().parent.parent,
    )
    parser.add_argument("--compare-root", type=Path)
    parser.add_argument("--projects-root", type=Path)
    parser.add_argument(
        "--skip-review-browser",
        action="store_true",
        help="只检查固定项目和测试契约；明确跳过浏览器点击拖动，结果会产生提醒",
    )
    args = parser.parse_args()

    root = args.skill_root.resolve()
    health = HealthCheck()
    if not root.is_dir():
        health.fail(f"Skill 目录不存在：{root}")
        return health.finish()

    check_required_files(root, health)
    if health.failures:
        return health.finish()
    check_skill_metadata(root, health)
    check_config(root, health)
    check_catalog(root, "assets/style-reference/catalog.json", health)
    check_catalog(root, "assets/approved-samples/catalog.json", health)
    check_bgm_catalog(root, health)
    check_mix_calibration(root, health)
    check_images(root, health)
    check_python_scripts(root, health)
    check_review_generator(root, health)
    check_review_interactions(
        root,
        health,
        contract_only=args.skip_review_browser,
    )
    check_tts_safety_and_chunking(root, health)
    check_tools(health)

    if args.projects_root:
        check_projects(root, args.projects_root.resolve(), health)
    if args.compare_root:
        compare_skill_roots(root, args.compare_root.resolve(), health)
    return health.finish()


if __name__ == "__main__":
    raise SystemExit(main())
