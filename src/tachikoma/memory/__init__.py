"""Memory package: extraction processors, context provider, and bootstrap hook.

Provides post-processing processors for extracting episodic memories,
facts, and preferences from conversations. Also includes a context
provider for retrieving relevant memories during pre-processing,
and the bootstrap hook for initializing the memories directory structure.
"""

from tachikoma.memory.context_provider import MemoryContextProvider
from tachikoma.memory.episodic import EpisodicProcessor
from tachikoma.memory.facts import FactsProcessor
from tachikoma.memory.hooks import memory_hook
from tachikoma.memory.preferences import PreferencesProcessor

__all__ = [
    "EpisodicProcessor",
    "FactsProcessor",
    "MemoryContextProvider",
    "PreferencesProcessor",
    "memory_hook",
]
