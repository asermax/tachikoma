# Memory

## Overview

Automatic memory extraction from conversations and retrieval of relevant memories for context enrichment. After a conversation ends, the system analyzes the exchange and persists learnings as structured markdown files — episodic summaries, user facts, and preferences. On every message, stored memories are searched for context relevant to the user's message, using session forking when conversation context is available to make informed relevance decisions.

## Sub-Capabilities

| Capability | Description | Status |
|------------|-------------|--------|
| [memory-extraction](memory-extraction.md) | Post-conversation analysis that extracts and persists memories | ✓ |
| [memory-context-retrieval](memory-context-retrieval.md) | Search stored memories for context relevant to user messages | ✓ |
