from tachikoma.buffer.items import BufferedItem


def build_shutdown_digest(items: list[BufferedItem]) -> str:
    """Build a combined digest prompt from pending buffered items.

    Produces a single prompt with a preamble instructing the agent to
    summarize all items rather than act on them individually.
    """
    preamble = (
        "⟨Shutdown digest⟩ The assistant is shutting down. "
        "The items below were buffered and are being delivered now as a final batch. "
        "Summarize them concisely for the user; do not act on them."
    )

    parts: list[str] = [preamble, ""]

    for idx, item in enumerate(items, start=1):
        priority_label = item.priority.name.lower()

        kind_label = item.kind

        source_clause = f", source: {item.source_id}" if item.source_id else ""

        parts.append(f"— Item {idx} ({priority_label}, {kind_label}{source_clause}) —")
        parts.append(item.prompt)
        parts.append("")

    return "\n".join(parts)
