"""Error types for state module.

Business logic errors that should not be logged as database errors.
"""


class NoTrainingDataError(RuntimeError):
    """Raised when no training data is available for a user.

    This is a business logic error (expected condition), not a database error.
    It should be handled gracefully by callers and not logged as a database error.
    """
