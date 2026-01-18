"""PHASE 6 — KNOWLEDGE GROUNDING CONTRACT.

The RAG system is explanatory only.

RAG output:
- MAY be shown to the user
- MAY be included in rationales
- MUST NOT influence decisions
- MUST NOT influence confidence
- MUST NOT influence execution

This module defines the contract that all knowledge/explanation
integration must follow.
"""

from typing import TypedDict


class KnowledgeSnippet(TypedDict, total=False):
    """Normalized knowledge snippet for explanations.

    This is the ONLY format that knowledge should be exposed in.
    All fields are optional to handle various RAG output formats.
    """

    id: str
    title: str
    excerpt: str  # Truncated text excerpt (max 500 chars)
    source: str  # Source identifier (e.g., "internal", "philosophy", "principle")
    relevance: float  # Relevance score (0.0-1.0), if available
