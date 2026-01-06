"""LLM model configuration for Virtus Coach.

Centralized model definitions following the architecture:
- Orchestrator: GPT-5.2
- Core Coach Reasoning: GPT-5.2 (complex) or GPT-4.1-Turbo (default)
- Tool Planning: GPT-4o Mini
- User-Facing Language: GPT-4o Mini (Claude Sonnet optional)
- Embeddings: text-embedding-3-large
"""

# Orchestrator
ORCHESTRATOR_MODEL = "gpt-4o"

# Core Coach Reasoning
COACH_REASONING_DEFAULT = "gpt-4.1-turbo"
COACH_REASONING_COMPLEX = "gpt-4o"

# Tool Planning
TOOL_PLANNING_MODEL = "gpt-4o-mini"

# User-Facing Language
USER_FACING_MODEL = "gpt-4o-mini"
USER_FACING_MODEL_ALTERNATIVE = "claude-sonnet-4"  # Optional

# Embeddings
EMBEDDINGS_MODEL = "text-embedding-3-large"


def get_coach_reasoning_model(is_complex: bool = False) -> str:
    """Get the appropriate model for coach reasoning.

    Args:
        is_complex: Whether this is a complex reasoning case requiring GPT-5.2

    Returns:
        Model name string
    """
    return COACH_REASONING_COMPLEX if is_complex else COACH_REASONING_DEFAULT
