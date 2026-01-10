"""Execution Controller Validators.

Validates that the orchestrator behaves as an execution controller,
not a conversational coach. These validators auto-fail chatty output.
"""

from typing import Protocol

from loguru import logger


class OrchestratorDecision(Protocol):
    """Protocol for orchestrator decision objects to avoid circular imports."""

    message: str
    target_action: str | None
    missing_slots: list[str]
    next_question: str | None
    should_execute: bool


def validate_single_question(message: str, missing_slots: list[str]) -> tuple[bool, str | None]:
    """Validate that message contains exactly one question when slots are missing.

    Args:
        message: User-facing message
        missing_slots: List of missing slots

    Returns:
        Tuple of (is_valid, error_message)
    """
    if not missing_slots:
        # No missing slots - single question rule doesn't apply
        return True, None

    question_count = message.count("?")
    if question_count > 1:
        error_msg = (
            f"Single-question rule violated: {question_count} questions found. "
            f"Must ask exactly ONE question when missing_slots={missing_slots}"
        )
        return False, error_msg

    # Check for multiple questions combined with "and" (e.g., "What's X and Y?")
    # This pattern asks for multiple things in one question
    message_lower = message.lower()

    # If missing multiple slots, check if question asks for multiple things
    if len(missing_slots) > 1:
        # Count question words that suggest asking for multiple things
        question_words = ["what", "which", "when", "where"]
        question_word_count = sum(1 for word in question_words if word in message_lower)

        # If we're missing multiple slots and question uses "and" or has multiple question words, it's likely asking for multiple
        if " and " in message_lower and question_word_count > 0:
            error_msg = (
                f"Single-question rule violated: Question appears to ask for multiple slots using 'and'. "
                f"Must ask for ONE slot at a time. Missing slots: {missing_slots}"
            )
            return False, error_msg

    # Check for paragraph breaks (multiple questions implied)
    paragraph_count = len(message.split("\n\n"))
    if paragraph_count > 1:
        error_msg = f"Single-question rule violated: {paragraph_count} paragraphs found. Must be a single question, no paragraphs."
        return False, error_msg

    return True, None


def validate_no_advice_before_execution(
    message: str,
    target_action: str | None,
    missing_slots: list[str],
) -> tuple[bool, str | None]:
    """Validate that advice is not given before execution.

    Args:
        message: User-facing message
        target_action: Target action to execute
        missing_slots: List of missing slots

    Returns:
        Tuple of (is_valid, error_message)
    """
    if not target_action or not missing_slots:
        # No executable action or no missing slots - advice ban doesn't apply
        return True, None

    advice_keywords = [
        "you should",
        "you should focus on",
        "it's important to",
        "remember to",
        "here's what",
        "here are some",
        "tips",
        "advice",
        "recommendation",
        "we'll focus on",
        "we'll work on",
        "build mileage",
        "gradually",
        "training theory",
    ]

    message_lower = message.lower()
    found_keywords = [keyword for keyword in advice_keywords if keyword in message_lower]

    if found_keywords:
        error_msg = (
            f"Advice ban violated: Found advice keywords {found_keywords} in message. "
            f"Must only ask for missing slots ({missing_slots}), not provide advice."
        )
        return False, error_msg

    return True, None


def validate_no_chatty_response(
    message: str,
    target_action: str | None,
    missing_slots: list[str],
) -> tuple[bool, str | None]:
    """Validate that response is slot-oriented, not chatty.

    Args:
        message: User-facing message
        target_action: Target action to execute
        missing_slots: List of missing slots

    Returns:
        Tuple of (is_valid, error_message)
    """
    if not target_action or not missing_slots:
        # No executable action or no missing slots - chatty check doesn't apply
        return True, None

    # Check sentence count (chatty responses have many sentences)
    sentence_endings = message.count(".") + message.count("!") + message.count("?")
    if sentence_endings > 2:
        error_msg = (
            f"Chatty response detected: {sentence_endings} sentences found. "
            f"Must be slot-oriented (single question) when missing_slots={missing_slots}"
        )
        return False, error_msg

    # Check for chatty phrases
    chatty_phrases = [
        "let's start",
        "let's begin",
        "thinking about",
        "goals",
        "let me help",
        "i'll help you",
    ]
    message_lower = message.lower()
    found_phrases = [phrase for phrase in chatty_phrases if phrase in message_lower]

    if found_phrases:
        error_msg = (
            f"Chatty response detected: Found chatty phrases {found_phrases}. Must be slot-oriented when missing_slots={missing_slots}"
        )
        return False, error_msg

    return True, None


def validate_execution_controller_decision(decision: OrchestratorDecision) -> tuple[bool, list[str]]:
    """Validate that decision follows execution controller rules.

    Args:
        decision: Orchestrator decision to validate

    Returns:
        Tuple of (is_valid, list_of_errors)
    """
    errors: list[str] = []

    # Validate single-question rule
    if decision.missing_slots:
        is_valid, error_msg = validate_single_question(decision.message, decision.missing_slots)
        if not is_valid and error_msg:
            errors.append(error_msg)

    # Validate no advice before execution
    is_valid, error_msg = validate_no_advice_before_execution(
        decision.message,
        decision.target_action,
        decision.missing_slots,
    )
    if not is_valid and error_msg:
        errors.append(error_msg)

    # Validate no chatty response
    is_valid, error_msg = validate_no_chatty_response(
        decision.message,
        decision.target_action,
        decision.missing_slots,
    )
    if not is_valid and error_msg:
        errors.append(error_msg)

    # Validate core invariant: every message must fill slot, ask for slot, or trigger execution
    if decision.target_action:
        if decision.missing_slots and not decision.next_question and "?" not in decision.message:
            errors.append(
                f"Core invariant violated: target_action={decision.target_action}, "
                f"missing_slots={decision.missing_slots}, but no question asked to fill slots"
            )

        # Core invariant: if slots complete and target_action exists, must execute
        # BUT: Allow edge case where weekly planning requires race plan first
        # In that case, target_action might be plan_race_build with missing_slots from race plan requirements
        if not decision.missing_slots and not decision.should_execute and decision.target_action and decision.target_action != "plan_week":
            errors.append(
                f"Core invariant violated: target_action={decision.target_action}, "
                f"missing_slots=[], but should_execute=False. Must execute when slots complete."
            )

    is_valid = len(errors) == 0

    if not is_valid:
        logger.error(
            "Execution controller validation failed",
            target_action=decision.target_action,
            missing_slots=decision.missing_slots,
            errors=errors,
            message_preview=decision.message[:100],
        )

    return is_valid, errors
