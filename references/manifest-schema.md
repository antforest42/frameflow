# 项目清单格式

`plan.json` 是项目状态的单一事实来源。

```json
{
  "schema_version": 2,
  "title": "你身边的人，为什么总是打听你的隐私",
  "intro_lines": [
    "你身边的人，为什么总是",
    "打听你的隐私？"
  ],
  "brand_text": "个体感受丨时代观察丨生活思考",
  "normalized_text_path": "normalized_script.txt",
  "subtitle_lines_path": "subtitle_lines.txt",
  "subtitle_profile": "audio_aligned_v2",
  "approved_subtitle_length_outlier_cues": [],
  "srt_path": "subtitles.srt",
  "narration_provider": "milora_manbo",
  "narration_audio_path": "朗读音频_成片.wav",
  "narration_manifest_path": "朗读生成记录.json",
  "production_status": "audio_aligned",
  "scenes": [
    {
      "id": "01",
      "cue_start": 1,
      "cue_end": 3,
      "source_text": "对应的完整原文",
      "core_relation": "一个人追问，另一个人保持边界",
      "subjects_and_action": "右侧人物拿放大镜靠近，左侧人物后退",
      "symbols": ["放大镜", "问号"],
      "accent_colors": ["青色"],
      "reference_ids": ["original-22", "approved-02"],
      "forbidden": ["任何文字"],
      "image_path": "images/01.png",
      "image_scale": 1.0,
      "image_offset_x_px": 0,
      "image_offset_y_px": 0,
      "status": "generated"
    }
  ],
  "cover": {
    "title_lines": [
      {
        "text": "你身边的人，为什么总是",
        "color": "#000000"
      },
      {
        "text": "打听你的隐私？",
        "color": "#B53316"
      }
    ],
    "image_path": "images/00.png"
  }
}
```

## 字段要求

- `normalized_text_path` 只包含正文；首段不得重复 `title`。
- `subtitle_lines_path` 每行是完成语义审核的显示单元，内部标点与正文一致，末尾不保留收尾标点。
- `subtitle_profile` 在真实朗读生成前可临时为 `privacy_reference_v1`；词级时间戳对齐后必须为 `audio_aligned_v2`。只有识别不可用且人工抽检通过时才允许使用备用的 `audio_aligned_v1`。
- `approved_subtitle_length_outlier_cues` 只记录经过人工语义审核、为了避免悬空断句而保留的3—14字范围外字幕编号；校验器会拒绝未登记的超长或过短字幕，也会拒绝已经不再越界的陈旧编号。
- `narration_provider` 默认是 `milora_manbo`，人工备用音频记为 `user_supplied`。
- `narration_audio_path` 指向含第118帧片头静音的最终朗读WAV。
- `narration_manifest_path` 指向不含 API Key 的生成记录。
- `production_status` 按顺序使用：
  - `script_prepared`
  - `narration_generated`
  - `audio_aligned`
  - `awaiting_image_approval`
  - `images_approved`
  - `completed`
- 场景编号连续且唯一；每个字幕编号恰好由一个场景覆盖。
- `image_path` 必须指向实际文件。
- `image_scale`、`image_offset_x_px` 和 `image_offset_y_px` 是可选的正文成片显示参数，默认分别为1.0、0、0。只在用户认为已批准图片的主体显示过小时逐场景调整；不得为了统一大小而全局裁切或放大所有图片。
- 正文左上角页眉的头像和版式由固定资产与配置提供，文字必须读取 `plan.title`，不得读取副标题、账号文字或审批覆盖值。
- `approval.json` 的 `intro_lines` 允许用户修改文字与分行，但必须包含一至三个非空行。

## 朗读生成记录

`朗读生成记录.json` 至少包含：

```json
{
  "schema_version": 1,
  "provider": "milora_manbo",
  "endpoint_mode": "free",
  "api_key_env": "MILORA_API_KEY",
  "source_sha256": "正文哈希",
  "speed": 0,
  "max_chunk_characters": 260,
  "lead_in_frames": 118,
  "fps": 30,
  "body_audio_path": "朗读音频_成片_正文.wav",
  "body_audio_sha256": "正文音频哈希",
  "output_audio_path": "朗读音频_成片.wav",
  "output_audio_sha256": "成片朗读哈希",
  "segments": [
    {
      "index": 1,
      "characters": 238,
      "text_sha256": "分段哈希",
      "audio_path": "曼波朗读分段/朗读分段_01.wav",
      "audio_sha256": "分段音频哈希",
      "duration_seconds": 42.5
    }
  ]
}
```

记录中的正文哈希、音频路径、时长和音频哈希必须与现有文件一致。历史项目补录时可增加
`"record_origin": "reconstructed_from_existing_artifacts"`，但不得猜测或改写已有音频。
记录中不得出现 API Key、Authorization 请求头或带鉴权参数的请求地址。

## 图片生成计时

`image_generation_timing.json` 用于区分远程模型、客户端调度、项目图片处理和审核页生成，不得把这些阶段混成单一“图片耗时”。至少记录：

```json
{
  "task_package_ready_utc": "2026-07-30T12:00:00Z",
  "first_queued_utc": "2026-07-30T12:00:05Z",
  "last_result_utc": "2026-07-30T12:12:00Z",
  "review_page_started_utc": "2026-07-30T12:12:40Z",
  "review_page_ready_utc": "2026-07-30T12:12:46Z",
  "task_package_ready_to_first_request_seconds": 5.0,
  "last_result_to_review_page_seconds": 46.0,
  "review_page_generation_seconds": 6.0,
  "avoidable_ready_idle_seconds": 0.0,
  "entries": [
    {
      "image_id": "01",
      "queued_utc": "2026-07-30T12:00:05Z",
      "started_utc": "2026-07-30T12:00:05Z",
      "completed_utc": "2026-07-30T12:01:10Z",
      "project_ready_utc": "2026-07-30T12:01:12Z",
      "source_path": "原生输出路径",
      "project_path": "images/01.png"
    }
  ]
}
```

任务包完成后立即发出首批；每张图补位后直接归一化写入项目。审核页生成时间只计算 `generate_review.py` 命令，不包含图片复制、归一化、审核或报告写作。详细报告可以在审核页交付后生成。

## 审批文件

统一审核页导出的 `approval.json` 可以包含：

```json
{
  "schema_version": 2,
  "approved": true,
  "selected_bgm_id": "A",
  "include_intro": true,
  "intro_lines": ["你身边的人为什么总是", "打听你的隐私"],
  "approved_scene_ids": ["01", "02", "03"],
  "covers": {
    "landscape": {
      "title_lines": ["你身边的人为什么总是", "打听你的隐私"],
      "accent_char_indices": [10, 11, 12, 13],
      "text_center_x": 830,
      "first_line_center_y": 305,
      "font_size": 98,
      "line_spacing_px": 24,
      "letter_spacing_px": 0,
      "character_size_scales": {"10": 1.2, "11": 1.2},
      "subject_center_x": 830,
      "subject_center_y": 885,
      "subject_scale": 1.0
    },
    "portrait": {
      "title_lines": ["你身边的人", "为什么总是打听", "你的隐私"],
      "accent_char_indices": [10, 11, 12, 13],
      "text_center_x": 621,
      "first_line_center_y": 300,
      "font_size": 92,
      "line_spacing_px": 24,
      "letter_spacing_px": 0,
      "character_size_scales": {},
      "subject_center_x": 621,
      "subject_center_y": 1135,
      "subject_scale": 1.0
    }
  }
}
```

`covers.landscape` 对应1660×1242横版，`covers.portrait` 对应1242×1660竖版。两套布局共享 `plan.cover.image_path` 指向的主体素材和同一背景色，但文案、分行、强调字、坐标、整体字号、行距、字距、单字字号和主体缩放分别保存。横版封面、竖版封面和片头 `intro_lines` 的文案与分行都允许在审核页分别修改。

`selected_bgm_id` 是审核页选定的正文背景音乐稳定编号。新审批文件必须输出该字段；缺少该字段的旧项目兼容为选项 A。完整音频与试听片段的对应关系只由 `assets/bgm/catalog.json` 管理，审批文件不得记录临时绝对路径。

`include_intro` 是审核页选定的片头开关，必须是布尔值；新审批文件必须输出该字段，缺少该字段的旧项目兼容为 `true`。值为 `true` 时，最终混音的前118帧固定使用 A 的片头声音，正文开始后才按 `selected_bgm_id` 切换；值为 `false` 时，画面、字幕、场景和朗读统一裁掉前118帧，所选 BGM 从其 `body_start_seconds` 起播，正文各轨从0秒同步开始。`intro_lines` 在无片头模式下仍保留，方便之后切回而不丢失排版。

`accent_char_indices` 与 `character_size_scales` 的字符编号都按各自封面文案去除空白后从0开始。`character_size_scales` 只记录偏离默认值的字符，数值是相对于 `font_size` 的0.5—2.0倍比例；未记录字符按1.0处理。`line_spacing_px` 为相邻行在整体字号之外增加的像素，`letter_spacing_px` 为相邻字符间距。封面主体图只能移动和等比缩放，不在审核页重新生成。旧项目的单个 `cover` 字段及缺少新增排版字段的审批数据仅作兼容输入；新审核页必须导出 `schema_version: 2` 和完整的 `covers`。

旧审批文件中的 `selected_subtitle` 仅作兼容冗余字段读取，不再参与校验或渲染；新审核页不得继续输出该字段。
