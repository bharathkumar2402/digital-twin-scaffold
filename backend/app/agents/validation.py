"""Agent output validation and retry-then-escalate safety engine (PROJECT_PLAN.md §4.4).

Non-negotiable rule 1: Every agent output is Pydantic-validated before it touches
state or the DB. No agent node returns raw dict/string data into `FacilityTwinState`
without a schema check. On validation failure: retry once with the error appended to
context, then halt that branch and log to `agent_runs` — never silently write
unvalidated data.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ValidationError

logger = logging.getLogger("agent_validation")

T = TypeVar("T", bound=BaseModel)


class AgentValidationError(Exception):
    """Raised when an agent output fails Pydantic schema validation."""

    def __init__(
        self,
        schema_name: str,
        errors: Sequence[Any],
        formatted_error: str,
        raw_data: object,
    ) -> None:
        self.schema_name = schema_name
        self.errors = list(errors)
        self.formatted_error = formatted_error
        self.raw_data = raw_data
        super().__init__(f"Agent output validation failed for {schema_name}:\n{formatted_error}")


class AgentEscalationRequired(Exception):
    """Raised when an agent output fails validation consecutively after retry.

    Per PROJECT_PLAN.md §4.4: Halts the pipeline branch and triggers human
    escalation rather than silently propagating unvalidated data into state or DB.
    """

    def __init__(
        self,
        agent_name: str,
        schema_name: str,
        validation_errors: list[str],
        raw_data: object,
    ) -> None:
        self.agent_name = agent_name
        self.schema_name = schema_name
        self.validation_errors = validation_errors
        self.raw_data = raw_data
        super().__init__(
            f"Agent '{agent_name}' failed schema validation after retry. "
            f"Halting pipeline branch for human escalation. Errors: {validation_errors}"
        )


@dataclass(frozen=True)
class AgentExecutionResult(Generic[T]):
    """Result of an agent invocation with validation and retry telemetry."""

    output: T
    attempts: int
    recovered_on_retry: bool
    retry_error_message: str | None


def format_validation_errors(exc: ValidationError) -> str:
    """Formats Pydantic ValidationError into a structured string suitable for LLM feedback."""
    formatted_lines: list[str] = []
    for err in exc.errors():
        loc = " -> ".join(str(elem) for elem in err.get("loc", []))
        msg = err.get("msg", "Invalid value")
        err_type = err.get("type", "value_error")
        input_val = err.get("input", None)
        line = f"- Field '{loc}': {msg} (type={err_type}, received={input_val!r})"
        formatted_lines.append(line)
    return "\n".join(formatted_lines)


def validate_agent_output(schema_cls: type[T], raw_data: object) -> T:
    """Validates raw agent data (dict, JSON string, or model) against schema_cls.

    Raises:
        AgentValidationError: If validation fails, with detailed field-level error descriptions.
    """
    if isinstance(raw_data, schema_cls):
        return raw_data

    payload: object = raw_data
    if isinstance(raw_data, str):
        try:
            payload = json.loads(raw_data)
        except json.JSONDecodeError as json_err:
            raise AgentValidationError(
                schema_name=schema_cls.__name__,
                errors=[{"msg": f"Malformed JSON: {json_err}", "type": "json_decode_error"}],
                formatted_error=f"Raw agent output is not valid JSON: {json_err}",
                raw_data=raw_data,
            ) from json_err

    if not isinstance(payload, dict):
        raise AgentValidationError(
            schema_name=schema_cls.__name__,
            errors=[{
                "msg": f"Expected dict, got {type(payload).__name__}",
                "type": "type_error",
            }],
            formatted_error=(
                f"Expected dictionary object for {schema_cls.__name__}, "
                f"received {type(payload).__name__}"
            ),
            raw_data=raw_data,
        )

    try:
        return schema_cls.model_validate(payload)
    except ValidationError as val_err:
        formatted = format_validation_errors(val_err)
        raise AgentValidationError(
            schema_name=schema_cls.__name__,
            errors=val_err.errors(),
            formatted_error=formatted,
            raw_data=raw_data,
        ) from val_err


async def execute_agent_with_retry(
    agent_name: str,
    schema_cls: type[T],
    agent_fn: Callable[[dict[str, Any]], Awaitable[object]],
    context: dict[str, Any],
    *,
    max_retries: int = 1,
) -> AgentExecutionResult[T]:
    """Executes an agent callable with the retry-then-escalate validation contract.

    Workflow per §4.4:
    1. Call agent_fn(context).
    2. Validate return value against schema_cls.
    3. If validation fails on attempt 1, append the formatted Pydantic error
       to context and retry once.
    4. If validation fails again, halt branch and raise AgentEscalationRequired
       for human intervention. Never writes unvalidated output.
    """
    accumulated_errors: list[str] = []

    # Attempt 1
    logger.info("Executing agent '%s' (attempt 1)", agent_name)
    raw_output = await agent_fn(context)
    try:
        validated = validate_agent_output(schema_cls, raw_output)
        return AgentExecutionResult(
            output=validated,
            attempts=1,
            recovered_on_retry=False,
            retry_error_message=None,
        )
    except AgentValidationError as err:
        logger.warning(
            "Agent '%s' failed output validation on attempt 1:\n%s",
            agent_name,
            err.formatted_error,
        )
        accumulated_errors.append(f"Attempt 1: {err.formatted_error}")

        if max_retries <= 0:
            raise AgentEscalationRequired(
                agent_name=agent_name,
                schema_name=schema_cls.__name__,
                validation_errors=accumulated_errors,
                raw_data=raw_output,
            ) from err

    # Attempt 2 (Retry with error feedback injected into context)
    retry_context = dict(context)
    retry_feedback = (
        f"Your previous output failed schema validation for {schema_cls.__name__}.\n"
        f"Validation errors:\n{accumulated_errors[-1]}\n"
        "Please fix the issues above and provide a valid response strictly adhering to the schema."
    )
    retry_context["last_validation_error"] = retry_feedback
    retry_context["validation_retry_attempt"] = 1

    logger.info(
        "Retrying agent '%s' with validation error feedback context (attempt 2)", agent_name
    )
    raw_retry_output = await agent_fn(retry_context)
    try:
        validated_retry = validate_agent_output(schema_cls, raw_retry_output)
        logger.info("Agent '%s' recovered successfully on attempt 2", agent_name)
        return AgentExecutionResult(
            output=validated_retry,
            attempts=2,
            recovered_on_retry=True,
            retry_error_message=accumulated_errors[-1],
        )
    except AgentValidationError as retry_err:
        logger.error(
            "Agent '%s' failed validation on retry (attempt 2). Halting for escalation:\n%s",
            agent_name,
            retry_err.formatted_error,
        )
        accumulated_errors.append(f"Attempt 2 (Retry): {retry_err.formatted_error}")
        raise AgentEscalationRequired(
            agent_name=agent_name,
            schema_name=schema_cls.__name__,
            validation_errors=accumulated_errors,
            raw_data=raw_retry_output,
        ) from retry_err

