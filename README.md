# video-to-srt skill

This repository hosts a reusable Codex skill for generating timestamp-aligned `.srt` subtitles from local video or audio files, tuned for Chinese-language content by default.

The workflow supports:

1. ASR transcription for a raw subtitle draft
2. Post-correction for obvious typo and terminology errors
3. Single-line subtitle cues with a default 18-character limit per cue

## Skill Folder

```text
video-to-srt/
  SKILL.md
  agents/openai.yaml
  references/
  scripts/
```

## Install

Copy the `video-to-srt/` folder into your global Codex skills directory:

```bash
mkdir -p "${CODEX_HOME:-$HOME/.codex}/skills"
cp -R video-to-srt "${CODEX_HOME:-$HOME/.codex}/skills/"
```

After that, you can invoke it in Codex with prompts like:

- `Use $video-to-srt to generate an SRT subtitle file for this local video.`
- `Use $video-to-srt to transcribe this mp4 into Chinese subtitles with one line per cue and at most 18 characters per line.`

## Requirements

- `ffmpeg`
- At least one transcription backend such as `faster-whisper`, `openai-whisper`, `mlx_whisper`, or the `whisper` CLI

## Notes

- Local media files, generated subtitles, and virtual environments are ignored by `.gitignore`.
- The repository does not bundle model weights.
