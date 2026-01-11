"""Canonical RAG context for orchestrator integration.

This module defines the only allowed interface between RAG and orchestration.
All RAG data must flow through this schema - no raw markdown, embeddings, or free text blobs.
"""

from dataclasses import dataclass
from typing import Literal

RagConfidence = Literal["low", "medium", "high"]


@dataclass(frozen=True)
class RagChunk:
    """RAG chunk with structured metadata for orchestrator.

    This is the only format the orchestrator sees - no raw text, no embeddings.
    """

    id: str
    domain: str
    title: str
    summary: str
    tags: list[str]
    source_id: str


@dataclass(frozen=True)
class RagContext:
    """RAG context for orchestrator decision biasing.

    This is the only allowed interface between RAG and orchestration.
    All RAG data must flow through this schema.
    """

    query: str
    confidence: RagConfidence
    chunks: list[RagChunk]

    def is_actionable(self) -> bool:
        """Check if RAG context is actionable (medium or high confidence).

        Returns:
            True if confidence is medium or high, False otherwise
        """
        return self.confidence in {"medium", "high"}
