"""Memory package: extraction processors, context provider, and bootstrap hook.

Provides post-processing processors for extracting episodic memories,
facts, and preferences from conversations, plus a transcript archive
processor. Also includes a context provider for retrieving relevant
memories during pre-processing, the bootstrap hook for initializing
the memories directory structure, maintenance tick functions for
scheduled memory store cleanup, and index rebuild support for
MEMORY.md index files.
"""

from tachikoma.memory.context_provider import MemoryContextProvider
from tachikoma.memory.episodic import EpisodicProcessor
from tachikoma.memory.facts import FactsProcessor
from tachikoma.memory.hooks import memory_hook
from tachikoma.memory.index import run_index_rebuild
from tachikoma.memory.maintenance import (
    context_maintenance_tick,
    episodic_maintenance_tick,
    facts_maintenance_tick,
    preferences_maintenance_tick,
)
from tachikoma.memory.preferences import PreferencesProcessor
from tachikoma.memory.transcripts import TranscriptArchiveProcessor

__all__ = [
    "EpisodicProcessor",
    "FactsProcessor",
    "MemoryContextProvider",
    "PreferencesProcessor",
    "TranscriptArchiveProcessor",
    "context_maintenance_tick",
    "episodic_maintenance_tick",
    "facts_maintenance_tick",
    "memory_hook",
    "preferences_maintenance_tick",
    "run_index_rebuild",
]
