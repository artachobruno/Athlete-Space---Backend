"""Error types for coach module.

Distinct error types to differentiate user errors vs developer errors.
"""


class MissingUserSlotError(Exception):
    """Raised when a user slot is missing BEFORE slot gate validation.

    This is a user-facing error that should result in clarification.
    """

    def __init__(self, slot_name: str, message: str | None = None):
        self.slot_name = slot_name
        self.message = message or f"Missing required slot: {slot_name}"
        super().__init__(self.message)


class ToolContractViolationError(Exception):
    """Raised when a tool contract is violated AFTER slot gate validation.

    This is a developer error - tools should never reach this point if
    slots are properly validated. This indicates a bug in the tool or
    slot validation logic.
    """

    def __init__(self, tool_name: str, message: str | None = None):
        self.tool_name = tool_name
        self.message = message or f"Tool contract violation for {tool_name}: {message}"
        super().__init__(self.message)
