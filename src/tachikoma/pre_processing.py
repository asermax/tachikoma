"""Pre-processing pipeline for enriching messages with context.

Provides a reusable, pluggable pipeline that runs ContextProvider instances
in parallel before the agent sees a message. Used by memory context providers,
skill providers, and other context enrichment handlers.
"""

import asyncio
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING

from claude_agent_sdk.types import (
    McpHttpServerConfig,
    McpSdkServerConfig,
    McpSSEServerConfig,
    McpStdioServerConfig,
)
from loguru import logger

if TYPE_CHECKING:
    from claude_agent_sdk.types import AgentDefinition

_log = logger.bind(component="pre_processing")

# Type alias for MCP server configurations
McpServerConfig = (
    McpStdioServerConfig | McpSSEServerConfig | McpHttpServerConfig | McpSdkServerConfig
)

# Valid XML tag name pattern: must start with letter/underscore, followed by
# letters, numbers, hyphens, or underscores
_XML_TAG_PATTERN = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_-]*$")


@dataclass
class ContextResult:
    """A named XML-tagged context block returned by a provider.

    Attributes:
        tag: The XML tag name (e.g., "memories"). Must be a valid XML tag name
            (starts with letter/underscore, contains only alphanumeric, hyphens,
            underscores).
        content: The context content to wrap in XML tags.
        mcp_servers: Optional MCP server configurations to pass to the coordinator.
            Used by context providers that also provide tools (e.g., project management).
        agents: Optional dict of agent definitions to load for this session.
            Providers can return agents that should be available for delegation.
            Defaults to None for backward compatibility with existing providers.
    """

    tag: str
    content: str
    mcp_servers: dict[str, McpServerConfig] | None = None
    agents: "dict[str, AgentDefinition] | None" = None

    def __post_init__(self) -> None:
        """Validate that tag is a valid XML tag name."""
        if not self.tag or not self.tag.strip():
            raise ValueError("ContextResult.tag must be non-empty")
        if not _XML_TAG_PATTERN.match(self.tag):
            raise ValueError(
                f"ContextResult.tag must be a valid XML tag name: {self.tag!r}"
            )


class ContextProvider(ABC):
    """Abstract base class for context providers.

    Subclasses implement provide() to return a named context block
    or None if no context is relevant. The ABC defines only the
    interface contract — no SDK coupling is inherited.
    """

    @abstractmethod
    async def provide(self, message: str) -> ContextResult | None:
        """Provide context relevant to the user message.

        Args:
            message: The user's message text.

        Returns:
            A ContextResult with the context, or None if no relevant context.
        """
        ...


class PreProcessingPipeline:
    """Runs registered ContextProvider instances in parallel with error isolation.

    Usage::

        pipeline = PreProcessingPipeline()
        pipeline.register(MemoryContextProvider(cwd))
        pipeline.register(SkillsContextProvider(cwd))
        results = await pipeline.run(message)

    Individual provider failures are logged but don't prevent other
    providers from completing.
    """

    def __init__(self) -> None:
        self._providers: list[ContextProvider] = []

    def register(self, provider: ContextProvider) -> None:
        """Register a provider to run on pipeline execution.

        Args:
            provider: The provider to register.
        """
        self._providers.append(provider)

    async def run(self, message: str) -> list[ContextResult]:
        """Run all registered providers in parallel.

        If no providers are registered, returns an empty list immediately.

        Individual provider failures are logged per DES-002 but don't
        propagate or prevent other providers from completing.

        Args:
            message: The user's message text.

        Returns:
            List of successful, non-None ContextResult instances.
        """
        if not self._providers:
            return []

        results = await asyncio.gather(
            *[p.provide(message) for p in self._providers],
            return_exceptions=True,
        )

        successful: list[ContextResult] = []

        for provider, result in zip(self._providers, results, strict=True):
            if isinstance(result, Exception):
                _log.exception(
                    "Provider failed: provider={name} err={err}",
                    name=provider.__class__.__name__,
                    err=str(result),
                )
            elif result is not None:
                successful.append(result)

        return successful


def assemble_context(results: list[ContextResult], message: str) -> str:
    """Assemble context results into an enriched message.

    Wraps each result in XML tags and prepends them to the original message.
    If no results exist, returns the original message unchanged.

    Args:
        results: List of ContextResult instances to assemble.
        message: The original user message.

    Returns:
        The enriched message with context prepended, or the original message
        if no results.
    """
    if not results:
        return message

    blocks = [f"<{r.tag}>\n{r.content}\n</{r.tag}>" for r in results]

    return "\n\n".join(blocks) + "\n\n" + message
