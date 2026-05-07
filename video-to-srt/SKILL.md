---
name: video-to-srt
description: 为本地视频或音频文件生成与原始媒体时间轴对齐的 `.srt` 字幕，默认面向中文语音内容，并支持基于上下文的后期纠错。Use when Codex 需要把 `.mp4`、`.mov`、`.mkv`、`.mp3`、`.wav` 等媒体转成 SRT，或用户明确要求“提取字幕”“生成 srt”“做中文字幕”“时间戳要和视频一致”“自动修正错别字”“结合上下文润色字幕但不要改时间轴”。
---

# video-to-srt

把本地媒体文件转成标准 `.srt` 字幕，并尽量保持时间戳与原视频一致。默认优先处理中文语音，先做 ASR 转写，再做后期纠错。每个字幕块只允许一行文本；如果一行超过 `18` 个字，就在前面的自然气口处拆成新的字幕块和新的时间戳。

## Quick Start

在 skill 目录内优先运行：

```bash
python3 scripts/transcribe_media.py /absolute/path/to/video.mp4
```

如需输出到指定位置：

```bash
python3 scripts/transcribe_media.py \
  /absolute/path/to/video.mp4 \
  --output /absolute/path/to/video.srt
```

如需先用术语表提升识别率，再生成初稿：

```bash
python3 scripts/transcribe_media.py \
  /absolute/path/to/video.mp4 \
  --terms-file references/glossary-template.tsv
```

## Workflow

1. 确认输入是本地媒体文件，且路径明确。
2. 默认把 `--language` 设为 `zh`，除非用户明确要求别的语言。
3. 优先用 `scripts/transcribe_media.py` 生成原始 `.srt`，默认模型使用 `medium`。
4. 如果用户给了领域术语、学校名、品牌名、人名、课程名，先整理成 TSV 词表，再通过 `--terms-file` 提高识别率。
5. 对原始 `.srt` 做后期纠错：
   - 先用 `scripts/replace_terms_in_srt.py` 批量替换已知术语误写。
   - 再由 Codex 基于整段上下文修正明显错别字、断句和专有名词。
6. 纠错完成后，用 `scripts/validate_srt.py` 验证字幕是否满足：
   - 每个字幕块只有一行文本
   - 每行不超过 `18` 个字
   - 时间戳连续且不倒退

## Output Rules

- 保持 SRT 编号连续，从 `1` 开始。
- 保持时间格式为 `HH:MM:SS,mmm --> HH:MM:SS,mmm`。
- 保持整体时间轴与源媒体一致，不要跳时或倒退。
- 每个字幕块只能有一行文本。
- 每一行字幕不能超过 `18` 个字；超过时优先在标点、空格或自然气口处拆成新的字幕块和新的时间戳。
- 默认直接输出识别结果，不擅自翻译成别的语言。
- 后期纠错可以重排字幕块，以满足 `18` 字上限和单行字幕要求。
- 如果不确定某个专有名词，不要硬猜；优先向用户确认，或保留原词并标记待确认。

## Improve Accuracy

优先按下面顺序提升识别质量：

1. 用 `--model medium` 或更强模型；只有在速度优先时才降到 `small`。
2. 显式指定 `--language zh`。
3. 用 `--prompt` 或 `--terms-file` 注入术语提示。
4. 对输出的 `.srt` 做上下文纠错，但不要改时间轴。

## AI Correction Rules

在让 Codex 重写字幕文本时，严格遵守：

- 允许为了满足 `18` 字限制而拆分字幕块和重算子时间戳。
- 不要输出多行字幕；每个字幕块只能有一行文本。
- 结合前后文修正明显错别字、同音词误识别、断句不自然的问题。
- 优先修正中文专有名词，例如学校名、地名、课程名、馆藏名、作品名。
- 不凭空补写原视频里没说过的信息。
- 保留说话人口语风格，避免过度书面化。

如果需要更详细的纠错步骤，读取 `references/correction-workflow.md`。

## Common Commands

用更强模型转写：

```bash
python3 scripts/transcribe_media.py \
  /absolute/path/to/video.mp4 \
  --model medium \
  --max-line-length 18
```

用术语表转写：

```bash
python3 scripts/transcribe_media.py \
  /absolute/path/to/video.mp4 \
  --terms-file /absolute/path/to/glossary.tsv
```

先做术语替换，生成一个修正版：

```bash
python3 scripts/replace_terms_in_srt.py \
  /absolute/path/to/raw.srt \
  --terms-file /absolute/path/to/glossary.tsv \
  --output /absolute/path/to/corrected.srt \
  --max-line-length 18
```

校验修正版没有破坏时间戳：

```bash
python3 scripts/validate_srt.py \
  /absolute/path/to/corrected.srt \
  --max-line-length 18
```

## References

- `references/correction-workflow.md`
  作用：说明如何做“结合上下文纠错，并保持单行字幕与 18 字限制”的 AI 纠错。
- `references/glossary-template.tsv`
  作用：术语表模板，第一列写误识别，第二列写正确写法。

## Scripts

- `scripts/transcribe_media.py`
  作用：抽取音轨、探测可用后端、执行转写、写出标准 SRT。
- `scripts/replace_terms_in_srt.py`
  作用：根据 TSV 术语表批量替换已知误写。
- `scripts/validate_srt.py`
  作用：验证 SRT 是否为单行字幕，且每行不超过 18 个字。
