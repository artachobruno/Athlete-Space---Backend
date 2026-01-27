"""Session text generation via LLM (B6.3).

This module handles:
- LLM call for session text generation
- Post-LLM constraint validation
- Retry logic with exponential backoff
"""

import asyncio
import json
import traceback
from pathlib import Path

from loguru import logger
from pydantic import ValidationError
from pydantic_ai import Agent

from app.coach.config.models import USER_FACING_MODEL
from app.domains.training_plan.models import SessionTextInput, SessionTextOutput
from app.domains.training_plan.schemas import SessionTextOutputSchema
from app.services.llm.model import get_model


def _load_prompt() -> str:
    """Load session text generator prompt from local filesystem.

    Returns:
        Prompt content as string

    Raises:
        FileNotFoundError: If prompt file doesn't exist
    """
    prompt_dir = Path(__file__).parent.parent.parent / "planner" / "prompts"
    prompt_path = prompt_dir / "session_text_generator.txt"

    if not prompt_path.exists():
        raise FileNotFoundError(f"Session text generator prompt not found: {prompt_path}")

    return prompt_path.read_text(encoding="utf-8")


def _build_user_message(input_data: SessionTextInput) -> str:
    """Build user message with input data.

    Args:
        input_data: Session text input

    Returns:
        Formatted user message string
    """
    message_parts = [
        "Generate a workout description using the following information.",
        "",
        f"Philosophy: {input_data.philosophy_id}",
        f"Race distance: {input_data.race_distance or 'None'}",
        f"Phase: {input_data.phase}",
        f"Week index: {input_data.week_index}",
        f"Session type: {input_data.day_type.value}",
        "",
        f"Allocated distance: {input_data.allocated_distance_mi} miles",
    ]

    if input_data.allocated_duration_min is not None:
        message_parts.append(f"Allocated duration: {input_data.allocated_duration_min} minutes")
    else:
        message_parts.append("Allocated duration: None")

    message_parts.extend([
        "",
        f"Template kind: {input_data.template_kind}",
        "Template parameters:",
        json.dumps(input_data.params, indent=2, default=str),
        "",
        "Constraints:",
        json.dumps(input_data.constraints, indent=2, default=str),
    ])

    return "\n".join(message_parts)


def validate_llm_output(input_data: SessionTextInput, output: SessionTextOutput) -> bool:
    """Validate LLM output against input constraints.

    Args:
        input_data: Original input with constraints
        output: LLM-generated output

    Returns:
        True if valid, False if constraints violated
    """
    # Check total distance
    total_distance = output.computed.get("total_distance_mi", 0.0)
    if not isinstance(total_distance, (int, float)):
        logger.warning("total_distance_mi is not numeric", value=total_distance)
        return False

    if total_distance > input_data.allocated_distance_mi + 0.05:
        logger.warning(
            "Distance constraint violated",
            allocated=input_data.allocated_distance_mi,
            computed=total_distance,
        )
        return False

    # Check hard minutes
    hard_minutes = output.computed.get("hard_minutes", 0)
    if not isinstance(hard_minutes, int):
        logger.warning("hard_minutes is not an integer", value=hard_minutes)
        return False

    hard_minutes_max = input_data.constraints.get("hard_minutes_max")
    if (
        hard_minutes_max is not None
        and isinstance(hard_minutes_max, (int, float))
        and hard_minutes > int(hard_minutes_max)
    ):
        logger.warning(
            "Hard minutes constraint violated",
            allocated=int(hard_minutes_max),
            computed=hard_minutes,
        )
        return False

    # Check intensity minutes
    intensity_minutes = output.computed.get("intensity_minutes", {})
    if not isinstance(intensity_minutes, dict):
        logger.warning("intensity_minutes is not a dict", value=intensity_minutes)
        return False

    for intensity_key, intensity_value in intensity_minutes.items():
        if not isinstance(intensity_value, int):
            logger.warning(f"intensity_minutes[{intensity_key}] is not an integer", value=intensity_value)
            return False

        constraint_key = f"{intensity_key}_minutes_max"
        constraint_value = input_data.constraints.get(constraint_key)
        if (
            constraint_value is not None
            and isinstance(constraint_value, (int, float))
            and intensity_value > int(constraint_value)
        ):
            logger.warning(
                f"Intensity minutes constraint violated for {intensity_key}",
                allocated=int(constraint_value),
                computed=intensity_value,
            )
            return False

    return True


def _raise_constraint_violation_error(max_attempts: int, template_id: str) -> None:
    """Raise error for constraint violation after all attempts.

    Args:
        max_attempts: Maximum number of attempts
        template_id: Template ID that failed

    Raises:
        ValueError: Always raises
    """
    raise ValueError(
        f"LLM output violates constraints after {max_attempts} attempts. "
        f"Template: {template_id}"
    )


def _is_retryable_error(error: Exception) -> bool:
    """Check if an error is retryable (transient).

    Args:
        error: Exception to check

    Returns:
        True if error is retryable, False otherwise
    """
    error_str = str(error).lower()
    error_type = type(error).__name__

    # Rate limit errors
    if "rate limit" in error_str or "429" in error_str or "quota" in error_str:
        return True

    # Connection/timeout errors
    if any(
        keyword in error_str
        for keyword in [
            "timeout",
            "connection",
            "network",
            "unavailable",
            "service unavailable",
            "503",
            "502",
            "500",
        ]
    ):
        return True

    # API errors that might be transient
    return error_type in {"APIConnectionError", "APIError", "RateLimitError", "TimeoutError"}


def _calculate_backoff_delay(attempt: int, base_delay: float = 1.0, max_delay: float = 60.0) -> float:
    """Calculate exponential backoff delay.

    Args:
        attempt: Current attempt number (0-indexed)
        base_delay: Base delay in seconds
        max_delay: Maximum delay in seconds

    Returns:
        Delay in seconds
    """
    delay = base_delay * (2 ** attempt)
    return min(delay, max_delay)


def _extract_raw_response(result) -> str | None:
    """Extract raw response text from pydantic_ai result object.

    Args:
        result: Result object from agent.run()

    Returns:
        Raw response text if found, None otherwise
    """
    # Try to extract from result.messages
    if hasattr(result, "messages") and result.messages:
        for msg in reversed(result.messages):
            if hasattr(msg, "role") and hasattr(msg, "content"):
                if msg.role == "assistant":
                    return str(msg.content)
            elif isinstance(msg, dict) and msg.get("role") == "assistant":
                return str(msg.get("content", ""))

    # Try result.data if available
    if hasattr(result, "data"):
        data = result.data
        if hasattr(data, "text"):
            return str(data.text)
        if isinstance(data, dict) and "text" in data:
            return str(data["text"])
        if isinstance(data, str):
            return data

    return None


async def generate_session_text_llm(
    input_data: SessionTextInput,
    retry_on_violation: bool = True,
) -> SessionTextOutput:
    """Generate session text via LLM with validation.

    Args:
        input_data: Session text input
        retry_on_violation: Whether to retry once on constraint violation

    Returns:
        SessionTextOutput with generated text

    Raises:
        ValidationError: If schema validation fails after retries
        RuntimeError: If LLM call fails
        ValueError: If constraints cannot be satisfied
    """
    logger.info(
        "Generating session text via LLM",
        template_id=input_data.template_id,
        allocated_distance=input_data.allocated_distance_mi,
    )

    # Load system prompt
    system_prompt = _load_prompt()

    # Build user message
    user_message = _build_user_message(input_data)

    # Create agent with schema output
    model = get_model("openai", USER_FACING_MODEL)
    agent = Agent(
        model=model,
        system_prompt=system_prompt,
        output_type=SessionTextOutputSchema,
    )

    # Retry configuration
    # For constraint violations: 2 attempts (initial + 1 retry)
    # For transient errors: 5 attempts with exponential backoff
    max_attempts_constraint = 2 if retry_on_violation else 1
    max_attempts_transient = 5

    last_error: Exception | None = None

    for attempt in range(max_attempts_transient):
        try:
            logger.debug("Calling LLM for session text", attempt=attempt + 1, template_id=input_data.template_id)

            # Log full prompt (system + user) after all variables are substituted
            full_prompt = f"System Prompt:\n{system_prompt}\n\nUser Prompt:\n{user_message}"
            # Use opt(raw=True) to prevent loguru from interpreting curly braces in JSON as format placeholders
            logger.opt(raw=True).info(
                f"LLM Prompt: Session Text Generation (attempt {attempt + 1}, template_id={input_data.template_id})\n{full_prompt}"
            )
            logger.info(
                "LLM Prompt: Session Text Generation metadata",
                attempt=attempt + 1,
                template_id=input_data.template_id,
                system_prompt=system_prompt,
                user_prompt=user_message,
                full_prompt=full_prompt,
            )

            result = await agent.run(user_message)

            # Extract raw response from result
            raw_response_text = _extract_raw_response(result)

            # Log raw response
            if raw_response_text:
                raw_log_msg = (
                    f"LLM Response: Session Text Generation - RAW "
                    f"(attempt {attempt + 1}, template_id={input_data.template_id}, length={len(raw_response_text)})\n"
                    f"{raw_response_text}"
                )
                # Use opt(raw=True) to prevent loguru from interpreting curly braces in JSON as format placeholders
                logger.opt(raw=True).info(raw_log_msg)
                logger.info(
                    "LLM Response: Session Text Generation - RAW metadata",
                    attempt=attempt + 1,
                    template_id=input_data.template_id,
                    raw_response=raw_response_text,
                    raw_response_length=len(raw_response_text),
                )
            else:
                logger.warning(
                    "LLM Response: Could not extract raw response from result",
                    attempt=attempt + 1,
                    template_id=input_data.template_id,
                    result_type=type(result).__name__,
                    result_attrs=dir(result),
                )

            # Parse schema output
            parsed = result.output

            # Log parsed/extracted response
            parsed_dict = parsed.model_dump() if hasattr(parsed, "model_dump") else {
                "title": parsed.title,
                "description": parsed.description,
                "structure": parsed.structure,
                "computed": parsed.computed,
            }
            parsed_json = json.dumps(parsed_dict, indent=2, default=str)
            intensity_minutes = parsed_dict.get("computed", {}).get("intensity_minutes")
            intensity_minutes_type = type(intensity_minutes).__name__ if intensity_minutes is not None else "NoneType"
            parsed_log_msg = (
                f"LLM Response: Session Text Generation - PARSED (attempt {attempt + 1}, template_id={input_data.template_id})\n"
                f"intensity_minutes type: {intensity_minutes_type}\n"
                f"intensity_minutes value: {intensity_minutes}\n"
                f"Full parsed output:\n{parsed_json}"
            )
            # Use opt(raw=True) to prevent loguru from interpreting curly braces in JSON as format placeholders
            logger.opt(raw=True).info(parsed_log_msg)
            logger.info(
                "LLM Response: Session Text Generation - PARSED metadata",
                attempt=attempt + 1,
                template_id=input_data.template_id,
                parsed_output=parsed_dict,
                parsed_computed=parsed_dict.get("computed", {}),
                parsed_intensity_minutes=intensity_minutes,
                parsed_intensity_minutes_type=intensity_minutes_type,
            )

            # Convert to domain model
            output = SessionTextOutput(
                title=parsed.title,
                description=parsed.description,
                structure=parsed.structure,
                computed=parsed.computed,
            )

            # Validate constraints
            if validate_llm_output(input_data, output):
                logger.info(
                    "Session text generated successfully",
                    template_id=input_data.template_id,
                    hard_minutes=output.computed.get("hard_minutes", 0),
                )
                return output

            # Constraint violation
            if attempt < max_attempts_constraint - 1:
                logger.warning(
                    "Constraint violation detected, retrying",
                    attempt=attempt + 1,
                    template_id=input_data.template_id,
                )
                continue

            # Final attempt failed
            logger.error(
                "Constraint validation failed after all attempts",
                template_id=input_data.template_id,
            )
            _raise_constraint_violation_error(max_attempts_constraint, input_data.template_id)

        except ValidationError as e:
            if attempt < max_attempts_constraint - 1:
                logger.warning("Schema validation failed, retrying", extra={"attempt": attempt + 1, "error": str(e)})
                continue
            logger.error("Schema validation failed after all attempts", extra={"error": str(e)})
            raise ValidationError(f"Schema validation failed after {max_attempts_constraint} attempts: {e}") from e

        except Exception as e:
            last_error = e
            error_traceback = "".join(traceback.format_exception(type(e), e, e.__traceback__))
            is_retryable = _is_retryable_error(e)

            logger.error(
                "LLM call failed",
                error=str(e),
                error_type=type(e).__name__,
                attempt=attempt + 1,
                max_attempts=max_attempts_transient,
                template_id=input_data.template_id,
                model=USER_FACING_MODEL,
                is_retryable=is_retryable,
                traceback=error_traceback,
            )

            # If not retryable, fail immediately
            if not is_retryable:
                raise RuntimeError(f"LLM call failed with non-retryable error: {type(e).__name__}: {e}") from e

            # If retryable and we have attempts left, wait and retry
            if attempt < max_attempts_transient - 1:
                delay = _calculate_backoff_delay(attempt)
                logger.warning(
                    "Retrying LLM call after backoff",
                    attempt=attempt + 2,
                    max_attempts=max_attempts_transient,
                    delay_seconds=delay,
                    error_type=type(e).__name__,
                )
                await asyncio.sleep(delay)
                continue

            # All retries exhausted
            raise RuntimeError(
                f"LLM call failed after {max_attempts_transient} attempts: {type(e).__name__}: {e}"
            ) from e

    # Should never reach here, but just in case
    if last_error:
        raise RuntimeError(
            f"LLM call failed after {max_attempts_transient} attempts: {type(last_error).__name__}: {last_error}"
        ) from last_error
    raise RuntimeError("Failed to generate session text after all attempts")
