---
description: "Instructions for sending files and media to the user via Telegram (images, documents, audio, video)"
---

# Sending Files via Telegram

You have access to the `send_file` tool for delivering files directly to the user's Telegram chat.

## When to Use

- After generating an image, chart, or diagram
- When a created document is more useful as a file than pasted inline
- When the user explicitly requests a file to be sent
- When file content is binary or too large to paste as text

## Parameters

- **file_path** (required): Path to the file. Accepted forms:
  - workspace-relative (e.g. `exports/report.pdf`)
  - absolute under the workspace, the system temporary directory, or a
    configured extra root
- **caption** (optional): Brief description, max 1024 characters

## Media Types

Files are automatically detected by extension and rendered appropriately:

- **Images** (.png, .jpg, .jpeg, .gif, .webp) — displayed as photo
- **Audio** (.mp3, .ogg, .wav, .flac) — displayed in audio player
- **Video** (.mp4, .avi, .mov, .webm) — displayed in video player
- **Everything else** — sent as a document attachment

## Constraints

- The file must exist on disk and be a regular file (directories are rejected)
- The file must live under one of: the workspace, the system temporary directory
  (e.g. `/tmp`), or any root declared in `telegram.send_file.extra_roots`
- Telegram enforces a 50MB upload limit (10MB for photos)
- If sending fails, the error names the allowed roots so a valid location can be picked next
