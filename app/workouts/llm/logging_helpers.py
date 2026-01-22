"""Helper functions for logging LLM requests and responses."""

from __future__ import annotations

from loguru import logger


def log_llm_request(
    context: str,
    system_prompt: str,
    user_prompt: str,
    attempt: int | None = None,
) -> None:
    """Log the actual prompt submitted to LLM.
    
    Args:
        context: Context description (e.g., "Workout Step Generation")
        system_prompt: System prompt sent to LLM
        user_prompt: User prompt sent to LLM
        attempt: Optional attempt number for retries
    """
    extra_data: dict[str, str | int] = {
        "system_prompt": system_prompt,
        "user_prompt": user_prompt,
        "full_prompt": f"System: {system_prompt}\n\nUser: {user_prompt}",
    }
    if attempt is not None:
        extra_data["attempt"] = attempt
    
    logger.debug(
        f"LLM Request: {context} - PROMPT SUBMITTED",
        **extra_data,
    )


def log_llm_raw_response(
    context: str,
    result: object,
    attempt: int | None = None,
) -> str | None:
    """Log the raw JSON/text response from LLM before parsing.
    
    Args:
        context: Context description (e.g., "Workout Step Generation")
        result: pydantic_ai result object
        attempt: Optional attempt number for retries
        
    Returns:
        Raw response text if found, None otherwise
    """
    raw_response_text = None
    
    # Try to extract raw response from result object
    if hasattr(result, "all_messages"):
        # Handle both method and property access
        all_messages = result.all_messages() if callable(result.all_messages) else result.all_messages
        if all_messages:
            # Get the last assistant message which contains the raw response
            for msg in reversed(all_messages):
            if hasattr(msg, "content") and msg.content:
                raw_response_text = str(msg.content)
                break
            elif isinstance(msg, dict) and msg.get("role") == "assistant":
                raw_response_text = str(msg.get("content", ""))
                break
    
    # Also try result.data if available
    if not raw_response_text and hasattr(result, "data"):
        data = result.data
        if hasattr(data, "text"):
            raw_response_text = str(data.text)
        elif isinstance(data, dict) and "text" in data:
            raw_response_text = str(data["text"])
        elif isinstance(data, str):
            raw_response_text = data
    
    extra_data: dict[str, str | int] = {}
    if attempt is not None:
        extra_data["attempt"] = attempt
    
    if raw_response_text:
        logger.debug(
            f"LLM Response: {context} - RAW JSON OUTPUT",
            raw_response=raw_response_text,
            raw_response_length=len(raw_response_text),
            **extra_data,
        )
    else:
        logger.debug(
            f"LLM Response: {context} - Could not extract raw response from result",
            **extra_data,
        )
    
    return raw_response_text


def log_llm_extracted_fields(
    context: str,
    parsed_output: object,
    attempt: int | None = None,
) -> None:
    """Log the extracted/parsed fields from LLM response.
    
    Args:
        context: Context description (e.g., "Workout Step Generation")
        parsed_output: Parsed Pydantic model output
        attempt: Optional attempt number for retries
    """
    extra_data: dict[str, str | int | dict | list] = {}
    if attempt is not None:
        extra_data["attempt"] = attempt
    
    # Try to get model dump
    if hasattr(parsed_output, "model_dump_json"):
        try:
            extra_data["extracted_json"] = parsed_output.model_dump_json(indent=2)
        except Exception:
            pass
    
    if hasattr(parsed_output, "model_dump"):
        try:
            extra_data["extracted_dict"] = parsed_output.model_dump()
        except Exception:
            pass
    
    # Log key fields if they exist
    if hasattr(parsed_output, "__dict__"):
        for key, value in parsed_output.__dict__.items():
            if not key.startswith("_"):
                # Limit size of logged values
                if isinstance(value, str) and len(value) > 1000:
                    extra_data[key] = value[:1000] + "... (truncated)"
                elif isinstance(value, (list, dict)) and len(str(value)) > 1000:
                    extra_data[key] = f"{type(value).__name__} (length: {len(value) if hasattr(value, '__len__') else 'unknown'})"
                else:
                    extra_data[key] = value
    
    logger.debug(
        f"LLM Response: {context} - EXTRACTED FIELDS",
        **extra_data,
    )
