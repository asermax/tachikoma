# Memory

## Overview

Automatic memory extraction from conversations and static injection of memory indexes for context enrichment. After a conversation ends, the system analyzes the exchange and persists learnings as structured markdown files — episodic summaries, user facts, and preferences. At startup, facts and preferences MEMORY.md indexes are injected into foundational context as navigable sections, allowing the agent to browse and read individual files on demand. A nightly maintenance system periodically consolidates, prunes, and deduplicates stored memories.

## Sub-Capabilities

| Capability | Description | Status |
|------------|-------------|--------|
| [memory-extraction](memory-extraction.md) | Post-conversation analysis that extracts and persists memories; nightly maintenance for consolidation, pruning, and deduplication | ✓ |
| [memory-context-retrieval](memory-context-retrieval.md) | Static injection of facts/preferences indexes into foundational context at startup | ✓ |
