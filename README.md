# FrameFlow

> 把一篇写好的中文知识文案，变成一条按真实朗读节奏生长的简笔画视频。
>
> 音频优先｜4:3 知识视频｜词级字幕对齐｜AI 配图｜人工审核｜横竖双封面｜Codex Skill

<p align="center">
  <img src="./examples/images/frameflow-body-frame.png" alt="FrameFlow 生成的知识视频正文画面" width="100%">
</p>

FrameFlow 是这套工作流的产品名。仓库当前保留的 Codex Skill 触发名是 `$life-evolution-video-maker`。

---

## 这个仓库是什么

FrameFlow 是一套面向中文知识内容的 AI 视频生产工作流。它接收用户已经写好的标题和正文，先生成连续朗读，再用真实音频反向确定字幕、配图和成片时间轴，最后经过人工审核，输出带朗读、字幕、背景音乐和简笔画配图的 4:3 视频。

它不是一个“估算每句话几秒”的字幕模板，也不是把一组图片机械拼起来的幻灯片工具。它的核心目标是：

**让画面服从真实的说话节奏，让创意可以审核，让审核后的生产可以稳定复现。**

一句话：先让声音发生，再让每一帧找到自己的位置。

---

## 为什么是音频优先

很多自动视频流程会先按字数估算时长，再逐句生成配音。这样很容易出现字幕追着声音跑、画面提前切走、停顿不自然等问题。

FrameFlow 的顺序相反：

1. 标题与正文分离，标题不进入朗读和正文字幕。
2. 调用曼波（MiloraAPI）生成连续正文朗读。
3. 通过 faster-whisper 获取词级时间戳。
4. 用真实词级时间重算字幕和场景驻留区间。
5. 根据已经锁定的时间轴规划、生成和审核配图。
6. 审核通过后，再执行封面、渲染、混音和发布。

这意味着视频总时长不是猜出来的，字幕切换也不是按平均字速硬切出来的。

```mermaid
flowchart LR
    A[标题 + 正文] --> B[连续朗读]
    B --> C[词级时间戳]
    C --> D[字幕与场景时间轴]
    D --> E[AI 配图]
    E --> F{人工审核}
    F -->|局部返工| E
    F -->|全部通过| G[双封面 + 渲染 + 混音]
    G --> H[MP4 + 横竖封面]
```

---

## 适合谁用

特别适合：

- 已经有中文知识类文案，希望把它稳定做成视频的人
- 做观点、成长、心理、社会观察和方法论内容的创作者
- 在意朗读节奏、字幕断句和画面切换是否自然的人
- 希望 AI 负责大部分生产，但自己保留最终审美决定权的人
- 想长期复用一套固定画风、字幕、封面和声音规范的人
- 使用 Codex 搭建个人内容生产流水线的人

不太适合：

- 只提供一个主题，希望工具自动代写整篇观点的人
- 需要真人口播、数字人、影视级运镜或复杂角色动画的人
- 需要 9:16 竖屏正文成片，而不只是竖版封面的人
- 希望跳过配图审核、全自动直接发布的人
- 需要在线多人协作剪辑或完整 NLE 时间线编辑的人

---

## 它会产出什么

一次完整运行默认会得到：

- 一条 1440 × 1080、30 fps 的 4:3 知识视频
- 连续中文朗读与词级对齐字幕
- 一组与正文场景对应的 AI 简笔画配图
- 可选的 3.933 秒片头
- 审核页选定的正文背景音乐
- 1660 × 1242 横版封面
- 1242 × 1660 竖版封面
- 一个可集中检查配图、BGM、片头和双封面的本地审核页
- 完整但收纳在 `项目文件/` 内的字幕、音频、时间轴、审批和诊断文件

外层发布目录只保留最终视频、两张封面和内部项目文件夹，避免散落一地的 SRT、JSON、HTML 和中间文件。

---

## 示例效果

下面的图片全部来自同一个真实项目：**《离网络越近，离现实越远》**。

### 正文成片画面

<p align="center">
  <img src="./examples/images/frameflow-body-frame.png" alt="FrameFlow 正文视频画面实例" width="100%">
</p>

正文画面固定包含本期标题、品牌栏、简笔画场景和烧录字幕。画面切换使用 0.5 秒左移转场，字幕与场景时间均来自真实朗读音频。

### 一篇文章的整组配图

<p align="center">
  <img src="./examples/images/frameflow-contact-sheet.png" alt="FrameFlow 为一篇文章生成的 20 张场景配图" width="100%">
</p>

这个案例包含 20 个正文场景。每张图只承载一个核心关系，以黑色简化人物为主体，用少量粉、青、橙、绿、红色标记语义重点。

### 横版与竖版双封面

<p align="center">
  <img src="./examples/images/frameflow-cover-landscape.png" alt="FrameFlow 横版 4:3 封面" width="56%">
  &nbsp;&nbsp;
  <img src="./examples/images/frameflow-cover-portrait.png" alt="FrameFlow 竖版 3:4 封面" width="28%">
</p>

横版与竖版共享同一主体素材和背景色，但分别保存文案、强调字、坐标、字号、行距、字距和主体缩放，不是把横版简单裁成竖版。

---

## 核心能力

### 1. 连续朗读，而不是逐条字幕配音

正文按自然句和接口限制拆成少量大段生成，合并时不人为插入停顿。中断后可复用已经成功的分段，避免重复消耗 API 额度。

### 2. 真实词级时间戳

朗读完成后，FrameFlow 使用本地 faster-whisper 获取词级时间戳，再把原文强制对齐到真实语音。只有词级识别不可用时，才会退回停顿与文本长度插值，并要求人工抽检。

### 3. 语义字幕切分

字幕不是按固定字数截断。系统会理解主谓宾、修饰、并列、转折和因果关系，再切成适合屏幕阅读的显示单元，并保留句中逗号、顿号、引号和冒号。

### 4. 原生 AI 配图与整组画风控制

默认使用二维平面简笔画：黑色实心人物、少量纯色语义符号、浅米色背景、无写实材质和无体积渲染。配图由当前环境的原生图片生成能力完成，不使用 SVG 小人或占位图冒充最终图片。

### 5. 滚动式图片生成

图片任务采用最多 3 张在途的连续滚动任务池。一张完成后立即补发下一张，同时在本地做背景归一化和单图初审，减少固定批次等待造成的空转。

### 6. 审核是硬门槛

全部配图生成后，系统会先交付本地 `review.html`。用户可以在同一页中：

- 检查每张图片及其对应字幕和原文
- 选择正文 BGM 并试听预览
- 决定是否保留片头
- 修改片头文案与分行
- 分别编辑横版和竖版封面文案
- 选择强调色字符和单字字号
- 拖动文字与主体，调整整体字号、行距和字距
- 复制最终审批数据

没有 `approved: true` 且没有覆盖全部场景的审批文件，最终渲染不会继续。

### 7. 审核后确定性生产

审核前允许生成和返工；审核后的字幕、排版、转场、声音和编码由脚本确定性执行。这样既保留 AI 创意，也避免最后一步出现不可解释的随机变化。

---

## 安装

### 运行环境

FrameFlow 当前面向 Codex 本地工作流。建议准备：

- Codex，以及可用的原生图片生成能力
- Python 3
- Pillow 与 NumPy
- FFmpeg 和 ffprobe
- 本地 faster-whisper 运行时与模型
- 曼波（MiloraAPI）API Key
- Node.js；如果要运行审核页自动交互回归，还需要 Playwright

> 本地安装包可以预置 faster-whisper 等运行时；只克隆 GitHub 源码时，需要自行补齐被 `.gitignore` 排除的本地运行时和模型。

### 克隆到 Codex Skills 目录

Windows PowerShell：

```powershell
git clone https://github.com/antforest42/life-evolution-video-maker.git "$env:USERPROFILE\.codex\skills\life-evolution-video-maker"
```

macOS / Linux：

```bash
git clone https://github.com/antforest42/life-evolution-video-maker.git "${CODEX_HOME:-$HOME/.codex}/skills/life-evolution-video-maker"
```

### 配置朗读 API Key

Windows PowerShell：

```powershell
$env:MILORA_API_KEY="你的 API Key"
```

macOS / Linux：

```bash
export MILORA_API_KEY="你的 API Key"
```

API Key 只从环境变量读取，不要写进 Skill、脚本、配置、日志或项目文件。

---

## 怎么用

### 直接制作一条视频

在 Codex 中输入：

```text
使用 $life-evolution-video-maker 把下面的中文文案制作成一条完整视频。

标题：离网络越近，离现实越远

正文：
<粘贴已经写好的正文>

开始制作。
```

“开始制作”表示允许把本期正文发送给曼波生成朗读。标题不会发送，也不会进入朗读或正文字幕。

### 先做到审核页

```text
使用 $life-evolution-video-maker 处理下面的标题和正文。
先完成连续朗读、词级字幕对齐、场景规划和配图，生成 review.html 后暂停，等我审核。

标题：<标题>
正文：<正文>
```

### 局部返工

```text
图07：人物距离更近，删除右侧问号。
图12：通过。
其余全部通过。
```

系统只重做被点名的图片，保留其余已经通过的内容。

### 审核后完成成片

```text
我已经审核完成，并保存了 approval.json。
请验证项目、生成横竖双封面、完成渲染和混音，再发布最终文件。
```

---

## 一次完整工作流

1. 接收标题和正文，初始化本期 `项目文件/`。
2. 分离标题，整理正文标点与误换行。
3. 按语义切分显示字幕。
4. 调用曼波生成连续朗读。
5. 本地转写词级时间戳，并重算 SRT 与时间轴。
6. 根据真实时长规划场景，复用或生成配图。
7. 生成统一审核页并暂停。
8. 根据用户反馈局部返工，保存 `approval.json`。
9. 验证字幕、音频、图片覆盖和审批状态。
10. 生成横竖双封面、无声画面轨和最终混音成片。
11. 清理发布目录并运行只读体检。

---

## 最终项目结构

```text
projects/<项目名>/
├── <视频标题>.mp4
├── （封面）<视频标题>.png
├── （封面-竖版）<视频标题>.png
└── 项目文件/
    ├── input.txt
    ├── normalized_script.txt
    ├── subtitle_lines.txt
    ├── subtitles.srt
    ├── timeline.json
    ├── plan.json
    ├── config.json
    ├── review.html
    ├── approval.json
    ├── 朗读音频_成片.wav
    ├── images/
    ├── cover.png
    ├── cover_portrait.png
    ├── final_picture_track.mp4
    └── final_with_voice.mp4
```

---

## 仓库结构

```text
.
├── README.md
├── SKILL.md
├── agents/
│   └── openai.yaml
├── assets/
│   ├── approved-samples/
│   ├── style-reference/
│   ├── bgm/
│   ├── fonts/
│   ├── background.png
│   ├── brand-avatar.png
│   └── default_config.json
├── examples/
│   └── images/
├── references/
│   ├── audio-alignment.md
│   ├── image-generation.md
│   ├── manifest-schema.md
│   ├── style-guide.md
│   ├── subtitle-segmentation.md
│   └── workflow-and-review.md
├── scripts/
│   ├── generate_manbo_tts.py
│   ├── transcribe_word_timestamps.py
│   ├── align_from_word_timestamps.py
│   ├── generate_review.py
│   ├── build_cover.py
│   ├── render_video.py
│   ├── mix_final_audio.py
│   ├── publish_project.py
│   └── check_skill.py
└── tests/
    └── fixtures/review-schema2/
```

---

## 质量门槛

FrameFlow 会明确拦截这些问题：

- 标题混入正文朗读或字幕
- 使用估算时长冒充真实音频时间轴
- 词级对齐、字幕连续性或内部标点异常
- 场景图片缺失、时间轴覆盖不完整或图片不可读
- 配图出现误导性的可读文字或整组画风漂移
- 审批文件不存在、未全部通过或没有覆盖全部场景
- 横版与竖版封面状态互相覆盖
- 最终视频、封面、音频或发布目录不符合约定

一键只读体检：

```bash
python -B scripts/check_skill.py . --projects-root projects
```

如果审核页生成器或交互逻辑有改动，体检还会使用固定测试项目实际执行点击、拖动和数据断言。`--skip-review-browser` 只能用于排查没有浏览器的环境，带提醒的结果不代表正式回归通过。

---

## 隐私、外部服务与费用

- 只有在用户明确说“开始”“制作”或“执行”后，正文才会发送给曼波生成朗读。
- 标题、API Key 和其他本地文件不在这项授权范围内。
- 新配图会发送给当前环境的图片生成服务。
- 朗读和图片生成服务可能消耗额度或产生费用，请以各自服务的实际规则为准。
- 用户可以随时撤销外发授权，或要求改用自己提供的纯朗读音频。

---

## 注意事项

- 当前流程重点服务中文知识类文案和 4:3 正文视频。
- FrameFlow 不替用户扩写观点、替换选题或改写事实。
- AI 图片可能出现错字、肢体错误、画风漂移或多余元素，所以审核不能省略。
- 图片中默认不生成文字；需要表达信息时，优先使用箭头、锁、问号、放大镜等图形符号。
- 已经通过的图片优先复用，只有语义变化或用户明确要求时才重做。
- `review.html` 是本地审核工具，不是云端协作后台。

---

## README 参考

本文档的组织方式参考了 [Ian Xiaohei Illustrations](https://github.com/helloianneo/ian-xiaohei-illustrations)：先说明工具是什么，再用真实实例展示效果，最后给出安装、使用、工作流和注意事项。FrameFlow 的功能描述、命令和案例均来自本仓库的实际实现与现有项目产物。
