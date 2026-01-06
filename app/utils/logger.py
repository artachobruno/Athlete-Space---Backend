"""Logger configuration and setup."""

import sys

from loguru import logger


def setup_logger(
    level: str = "INFO",
    log_file: str | None = None,
    rotation: str = "10 MB",
    retention: str = "7 days",
) -> None:
    """Configure loguru logger with specified settings.

    Args:
        level: Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        log_file: Optional file path for logging. If None, only console logging.
        rotation: Log rotation size (e.g., "10 MB")
        retention: Log retention period (e.g., "7 days")
    """
    # Remove default handler
    logger.remove()

    # Add console handler with format
    logger.add(
        sys.stderr,
        format=(
            "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
            "<level>{level: <8}</level> | "
            "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
            "<level>{message}</level>"
        ),
        level=level,
        colorize=True,
    )

    # Add file handler if specified
    if log_file:
        logger.add(
            log_file,
            rotation=rotation,
            retention=retention,
            level=level,
            format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function}:{line} - {message}",
        )
