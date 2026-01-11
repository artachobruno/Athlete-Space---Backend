"""Observe SDK integration for LLM observability.

This module provides tracing for LLM calls, tool execution, and conversation flows.
Uses traceloop-sdk under the hood (Observe's OpenLLMetry-based SDK).
"""

import contextlib
from typing import Any

from loguru import logger

try:
    from opentelemetry import trace as otel_trace_module
except ImportError:
    otel_trace_module = None

try:
    from traceloop import Traceloop
except ImportError:
    Traceloop = None


class _NoOpTrace:
    """No-op trace context manager for when Observe is disabled."""

    def __enter__(self) -> "_NoOpTrace":
        return self

    def __exit__(self, *args: Any) -> None:
        pass


class _ObserveSDK:
    """Observe SDK wrapper.

    Provides a simplified API that wraps traceloop-sdk.
    """

    def __init__(self) -> None:
        """Initialize Observe SDK."""
        self._enabled = False
        self._traceloop = None
        self._initialized = False

    def init(
        self,
        api_key: str = "",
        enabled: bool = False,
        sample_rate: float = 1.0,
    ) -> None:
        """Initialize Observe SDK.

        Args:
            api_key: Observe API key
            enabled: Whether tracing is enabled
            sample_rate: Sampling rate (0.0-1.0)
        """
        self._enabled = enabled
        if not enabled:
            logger.info("Observe tracing is disabled")
            return

        if Traceloop is None:
            logger.warning(
                "traceloop-sdk not installed. Install with: pip install traceloop-sdk. "
                "Tracing will be disabled."
            )
            self._enabled = False
            return

        try:
            self._traceloop = Traceloop
            # Initialize Traceloop with minimal config
            # Note: Actual configuration happens via environment variables
            # TRACELOOP_BASE_URL, TRACELOOP_TRACE_CONTENT, etc.
            Traceloop.init(
                app_name="virtus-coach",
                api_key=api_key if api_key else None,
            )
            self._initialized = True
            logger.info(
                "Observe SDK initialized",
                sample_rate=sample_rate,
                has_api_key=bool(api_key),
            )
        except ImportError:
            logger.warning(
                "traceloop-sdk not installed. Install with: pip install traceloop-sdk. "
                "Tracing will be disabled."
            )
            self._enabled = False
        except Exception as e:
            logger.error(f"Failed to initialize Observe SDK: {e}", exc_info=True)
            self._enabled = False

    def trace(self, name: str, metadata: dict[str, Any] | None = None) -> Any:
        """Create a trace span.

        Args:
            name: Trace name
            metadata: Optional metadata dictionary

        Returns:
            Context manager for the trace span
        """
        if not self._enabled or not self._initialized:
            return _NoOpTrace()

        if otel_trace_module is None:
            return _NoOpTrace()

        try:
            tracer = otel_trace_module.get_tracer(__name__)
            span = tracer.start_as_current_span(name)
            if metadata:
                for key, value in metadata.items():
                    span.set_attribute(key, str(value))
        except Exception as e:
            logger.debug(f"Failed to create trace span: {e}")
            return _NoOpTrace()
        else:
            return span

    def set_association_properties(self, properties: dict[str, str]) -> None:
        """Set association properties for tracing (user_id, conversation_id, etc.).

        Args:
            properties: Dictionary of properties to associate with traces
        """
        if not self._enabled or not self._initialized:
            return

        try:
            if self._traceloop:
                self._traceloop.set_association_properties(properties)
        except Exception as e:
            logger.debug(f"Failed to set association properties: {e}")


# Global instance
_observe = _ObserveSDK()


def init(api_key: str = "", enabled: bool = False, sample_rate: float = 1.0) -> None:
    """Initialize Observe SDK (called once at app startup).

    Args:
        api_key: Observe API key
        enabled: Whether tracing is enabled
        sample_rate: Sampling rate (0.0-1.0)
    """
    _observe.init(api_key=api_key, enabled=enabled, sample_rate=sample_rate)


def trace(name: str, metadata: dict[str, Any] | None = None) -> Any:
    """Create a trace span.

    Args:
        name: Trace name (e.g., "llm.coach_response", "tool.plan_race_build")
        metadata: Optional metadata dictionary

    Returns:
        Context manager for the trace span

    Example:
        with observe.trace("llm.coach_response", metadata={"model": "gpt-4"}):
            response = call_llm(...)
    """
    return _observe.trace(name, metadata)


def set_association_properties(properties: dict[str, str]) -> None:
    """Set association properties for tracing.

    Args:
        properties: Dictionary of properties (e.g., {"user_id": "123", "conversation_id": "c_..."})

    Example:
        observe.set_association_properties({"user_id": user_id, "conversation_id": conv_id})
    """
    _observe.set_association_properties(properties)
