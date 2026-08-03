# 曼波 API 朗读与反向对齐

## 默认方案：音频优先

文案定稿后先生成朗读，再确定最终字幕、场景驻留和视频总时长。标题可在片头展示，并固定用于正文左上角页眉；它不发送给接口，也不进入正文字幕。

默认调用与官方桌面程序相同的接口：

- 免费地址：`https://api.milorapart.top/apis/mbAIsc`
- 带 Key 地址：`https://api.milorapart.top/apis/mbAIscvip`
- 请求：GET
- 参数：`text`、`format`、`speed`
- 可选 Key：查询参数 `key` 与 `Authorization: Bearer ...`
- 成功响应：`code` 为 `200`，`url` 为音频下载地址

正文会离开本机并发送给第三方服务。API Key 只能从 `MILORA_API_KEY` 环境变量读取，不得写入任何文件。

## 自动生成

运行：

```text
scripts/generate_manbo_tts.py normalized_script.txt 朗读音频_成片.wav --plan plan.json --config config.json
```

默认处理：

1. 按句号、问号、感叹号、分号和主题段落优先自然拆分。
2. 单段默认不超过260个字符；超限时再在逗号、冒号和顿号处拆分。
3. 每段单独请求并保存到 `曼波朗读分段/`。
4. 每段保存文字哈希侧车文件；中断后只补失败段。
5. 统一转为48kHz、单声道、16位WAV。
6. 双向修剪每段头尾的接口填充静音，不删除段内自然停顿。
7. 直接拼接各段，不插入额外间隔。默认最多保留开头0.03秒、结尾0.08秒的连接静音。
8. 在正文朗读前加入第118帧的片头静音，即 `118 / 30 = 3.933333…` 秒。

输出：

- `朗读音频_成片.wav`：含片头静音，直接用于最终合成和对齐。
- `朗读音频_成片_正文.wav`：不含片头静音的连续正文。
- `曼波朗读分段/`：可断点复用的分段WAV和文字哈希。
- `朗读生成记录.json`：不含 Key 的生成记录。

含片头静音的母版始终保留，避免重新生成或重新对齐。审批文件 `include_intro: true` 时直接使用完整母版；`include_intro: false` 时，最终混音从第118帧对应位置裁入朗读，画面字幕与场景同步前移，使人声从0秒开始。这个选择只改变最终压制时间轴，不覆盖两份朗读资产或原始对齐记录。

## 首选：词级时间戳强制对齐

停顿检测无法可靠区分句号、逗号和枚举顿号，可能导致一条字幕整体提前或滞后数秒。最终成片优先使用本地 Faster-Whisper 生成词级时间戳，再把识别字符强制匹配回已知原文。

运行：

```text
scripts/transcribe_word_timestamps.py \
  朗读音频_成片_正文.wav \
  whisper_word_timestamps.json \
  --source normalized_script.txt

scripts/align_from_word_timestamps.py \
  --source normalized_script.txt \
  --input-srt subtitles.srt \
  --word-timestamps whisper_word_timestamps.json \
  --body-audio 朗读音频_成片_正文.wav \
  --final-audio 朗读音频_成片.wav \
  --output-srt subtitles.srt \
  --timeline timeline.json \
  --report word_alignment_report.json \
  --plan plan.json \
  --config config.json
```

对齐规则：

1. 忽略空白和标点后，正文与显示字幕必须完全一致。
2. 第一条正文字幕固定从第118帧开始。
3. 识别结果与原文的序列相似度不得低于85%；低于阈值时拒绝生成正式时间轴。
4. 使用识别词的起止时间作为字符锚点；识别错字只在相邻可靠字符之间插值，不使用识别文本替换原文。
5. 相邻显示字幕连续衔接；显示换字不改变朗读本身。
6. 最后一条字幕与朗读音频结尾对齐，允许视频按配置保留极短片尾。
7. 更新 `plan.json` 和 `config.json` 的 `subtitle_profile` 为 `audio_aligned_v2`。

## 备用：停顿插值

只有本地词级识别确实不可用时，才运行 `scripts/align_audio_timeline.py` 生成 `audio_aligned_v1`。该方法只能用于预览或人工抽检后的成片，不能仅凭脚本校验就认定同步可靠。

至少人工抽检：

- 开头第一个长枚举后的句子；
- 中段一个包含多个逗号的长句；
- 末尾三条字幕；
- API分段连接点前后各一条字幕。

## 备用人工音频

只有接口不可用、额度耗尽、用户拒绝把正文发送给第三方，或用户明确指定其他音色时，才使用人工纯朗读音频。

备用时可以用 `scripts/make_narration_srt.py` 生成剪映连续朗读容器，但它不再是默认交付步骤。收到人工音频后仍必须加入或识别3.933秒片头，并优先运行词级时间戳强制对齐。

## 故障处理

- 请求失败：最多重试3次，指数退避，并复用已成功分段。
- 免费额度耗尽：等待额度恢复，或由用户在系统环境中设置 `MILORA_API_KEY`。
- 服务返回非 HTTPS 音频地址、本机地址或内网地址：拒绝下载。下载前解析域名并确认所有地址均为公网地址；每次跳转都重新检查，最终响应地址也必须再次验证。
- 某段时长异常或无法解码：只重做该段。
- 连接点出现明显空白：只重新清理对应两段头尾，不改整篇语速。
- 不得为了继续生产而静默替换为其他音色。
