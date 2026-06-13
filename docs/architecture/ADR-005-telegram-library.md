# ADR-005: Telegram Library

**Status**: Accepted
**Date**: 2026-06-11

## Context

Telegram is Tachikoma's primary interface. The channel needs a TypeScript library covering long polling, full media support (photos, audio, voice, documents, stickers, video, animations), message editing for streamed responses, and clean middleware for the channel extension.

## Decision

Use **grammY** for the Telegram channel extension.

- Long polling against the Bot API (no webhook/public endpoint required for a self-hosted service)
- Typed Bot API surface kept current with Telegram releases
- Official plugin ecosystem available where needed (throttler for rate limits, files for downloads)

The channel remains thin per the architecture: grammY handles transport and Bot API mechanics, while rendering decisions (message splitting, streaming edits, media descriptors) live in the telegram extension and all conversation logic stays in the coordinator.

## Consequences

### Positive

- TypeScript-first with complete, up-to-date Bot API typings — media handling and reactions are typed end to end
- Middleware model maps naturally to channel concerns (auth allowlist, media interception, error handling)
- Actively maintained with strong documentation; the de facto modern choice in the TS ecosystem

### Negative

- Bot API rate limits are not handled out of the box — the throttler plugin (or deliberate edit pacing for streamed responses) must be part of the channel design
- A framework-specific update model to learn; the media descriptor table is hand-built rather than provided by the library

## Alternatives Considered

- **Telegraf**: long-standing but slower-moving typings and a less ergonomic API; grammY is its spiritual successor
- **node-telegram-bot-api**: callback-era design, weak types
- **Raw Bot API client**: full control but reimplements polling, update dispatch, and file handling for no benefit
