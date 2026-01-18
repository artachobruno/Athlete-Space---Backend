"""Style LLM layer for rewriting structured coaching decisions into natural messages.

This layer is NON-AUTHORITATIVE:
- It rewrites decisions
- It does NOT decide, compute, retrieve, or execute
"""

from app.responses.input_builder import build_style_input
from app.responses.style_llm import generate_coach_message
from app.responses.validator import validate_message

__all__ = ["build_style_input", "generate_coach_message", "validate_message"]
