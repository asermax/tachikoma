from typing import Any

from claude_agent_sdk.types import AssistantMessage, AssistantMessageError, ResultMessage


def make_assistant(
    content: list[Any],
    error: AssistantMessageError | None = None,
) -> AssistantMessage:
    return AssistantMessage(content=content, model="claude-sonnet-4-5", error=error)


def make_result(
    session_id: str = "sess-test",
    total_cost_usd: float | None = 0.01,
    is_error: bool = False,
    result: str | None = None,
) -> ResultMessage:
    return ResultMessage(
        subtype="success" if not is_error else "error",
        duration_ms=100,
        duration_api_ms=80,
        is_error=is_error,
        num_turns=1,
        session_id=session_id,
        total_cost_usd=total_cost_usd,
        usage={"input_tokens": 10},
        result=result,
    )
