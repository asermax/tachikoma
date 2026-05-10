"""Condition evaluator for workflow steps.

Evaluates natural-language condition prompts by forking the current SDK
session into a read-only sub-agent.  Returns structured results and
never raises — callers check ``is_error`` instead.

See ADR-014 for the session-context threading that makes forking
available to MCP tool handlers.
"""

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from loguru import logger

from tachikoma.agent_defaults import AgentDefaults
from tachikoma.post_processing import fork_and_capture
from tachikoma.sessions.model import Session
from tachikoma.workflows.model import StepState

_log = logger.bind(component="workflow_conditions")

_ALLOW_RULES = [
    "Read(*)",
    "Glob",
    "Grep",
]

_TOOLS = ["Read", "Glob", "Grep"]

_RETRY_PROMPT = (
    "Your previous response could not be parsed as JSON. "
    'Please respond with ONLY a JSON object: '
    '{"passes": true/false, "reason": "brief explanation"}'
)

_PROMPT_TEMPLATE = """\
Evaluate this workflow step condition. You have read-only access to the workspace.

## Condition
{condition_prompt}

## Workflow Context
- Scratchpad path: {scratchpad_path}
- Step states: {step_states_json}

Read any files you need to determine if the condition is met,
including the scratchpad and workspace files.

You MUST respond with ONLY a JSON object (no other text):
{{"passes": true, "reason": "brief explanation"}}
or
{{"passes": false, "reason": "brief explanation"}}"""


@dataclass(frozen=True)
class ConditionResult:
    """Outcome of evaluating a step condition."""

    passes: bool
    is_error: bool
    reason: str


def _build_evaluation_prompt(
    condition_prompt: str,
    step_states: dict[str, StepState],
    scratchpad_path: str,
) -> str:
    return _PROMPT_TEMPLATE.format(
        condition_prompt=condition_prompt,
        scratchpad_path=scratchpad_path,
        step_states_json=json.dumps(step_states),
    )


def _parse_evaluation_response(raw: str) -> ConditionResult:
    """Parse the sub-agent's JSON response.

    Returns a ConditionResult with is_error=True on any parsing failure.
    """
    text = raw.strip()

    # The agent may wrap the JSON in markdown fences.
    if text.startswith("```"):
        lines = text.splitlines()
        # Drop opening and closing fences.
        lines = [line for line in lines if not line.strip().startswith("```")]
        text = "\n".join(lines).strip()

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return ConditionResult(
            passes=False,
            is_error=True,
            reason=f"Non-JSON response from condition evaluator: {raw[:200]}",
        )

    if not isinstance(data, dict):
        return ConditionResult(
            passes=False,
            is_error=True,
            reason=f"Expected JSON object, got {type(data).__name__}: {raw[:200]}",
        )

    passes = data.get("passes")
    reason = data.get("reason", "")

    if not isinstance(passes, bool):
        return ConditionResult(
            passes=False,
            is_error=True,
            reason=f"Missing or non-boolean 'passes' field: {raw[:200]}",
        )

    return ConditionResult(passes=passes, is_error=False, reason=str(reason))


async def _call_subagent(
    session: Session, prompt: str, agent_defaults: AgentDefaults,
) -> str | None:
    return await fork_and_capture(
        session, prompt, agent_defaults,
        tools=_TOOLS, allow=_ALLOW_RULES, model=agent_defaults.processor_model,
    )


async def evaluate_condition(
    condition_prompt: str,
    step_states: dict[str, StepState],
    scratchpad_path: str,
    workspace_path: Path,
    agent_defaults: AgentDefaults,
    sdk_session_id: str | None,
) -> ConditionResult:
    """Evaluate a condition prompt via a forked read-only sub-agent.

    Never raises.  Returns ConditionResult with is_error=True on any
    failure (SDK error, timeout, non-JSON response, missing session).
    """
    if sdk_session_id is None:
        _log.debug("No SDK session ID available, assuming condition passes")
        return ConditionResult(
            passes=True,
            is_error=False,
            reason="No session available for condition evaluation, assuming passes",
        )

    session = Session(
        id="condition-eval",
        started_at=datetime.now(UTC),
        sdk_session_id=sdk_session_id,
    )

    prompt = _build_evaluation_prompt(condition_prompt, step_states, scratchpad_path)

    try:
        response = await _call_subagent(session, prompt, agent_defaults)
    except Exception as exc:
        _log.warning("Condition evaluation failed: {err}", err=str(exc))
        return ConditionResult(
            passes=False,
            is_error=True,
            reason=f"Condition evaluator error: {exc}",
        )

    if not response:
        return ConditionResult(
            passes=False,
            is_error=True,
            reason="Condition evaluator returned empty response",
        )

    result = _parse_evaluation_response(response)
    if not result.is_error:
        return result

    _log.warning(
        "Condition evaluator returned non-JSON, retrying: {reason}",
        reason=result.reason,
    )

    try:
        retry_response = await _call_subagent(session, _RETRY_PROMPT, agent_defaults)
    except Exception as exc:
        _log.warning("Condition evaluation retry failed: {err}", err=str(exc))
        return result

    if not retry_response:
        return result

    return _parse_evaluation_response(retry_response)
