"""Embedding-only template selector.

This module provides a simple, deterministic template selector that:
- Always returns exactly one template
- No thresholds
- No fallbacks
- O(N) complexity
- Uses embeddings only

The only way this fails is if the template library is empty (configuration error).
"""

from dataclasses import dataclass

import numpy as np
from loguru import logger

from app.domains.training_plan.models import PlanRuntimeContext, SessionTemplate
from app.domains.training_plan.template_query_builder import build_template_query
from app.embeddings.embedding_service import get_embedding_service


@dataclass(frozen=True)
class EmbeddedTemplate:
    """Template with precomputed embedding.

    Attributes:
        template: Session template
        embedding: Precomputed embedding vector
        template_id: Unique template identifier
        session_type: Session type for filtering
    """

    template: SessionTemplate
    embedding: list[float]
    template_id: str
    session_type: str


class TemplateLibrary:
    """Preloaded template library with embeddings.

    This class holds all templates with their embeddings, loaded at startup.
    It provides a simple interface for template selection via embedding similarity.
    """

    def __init__(self, templates: list[EmbeddedTemplate]) -> None:
        """Initialize template library.

        Args:
            templates: List of embedded templates

        Raises:
            RuntimeError: If template library is empty
        """
        if not templates:
            raise RuntimeError(
                "Template library is empty — cannot run embedding-only selector. "
                "Ensure templates are precomputed with embeddings."
            )
        self.templates = templates
        logger.info(f"Initialized TemplateLibrary with {len(templates)} templates")

    def select_template(
        self,
        *,
        domain: str,
        session_type: str,
        race_distance: str | None = None,
        phase: str | None = None,
        philosophy: str | None = None,
    ) -> SessionTemplate:
        """Select template using embedding similarity.

        This method:
        1. Builds a deterministic query string
        2. Embeds the query once
        3. Computes cosine similarity against all templates
        4. Returns the template with highest similarity (argmax)

        Args:
            domain: Training domain (e.g., "running")
            session_type: Session type (e.g., "easy", "threshold")
            race_distance: Race distance (e.g., "5k") or None
            phase: Training phase (e.g., "build", "taper") or None
            philosophy: Philosophy identifier (e.g., "daniels") or None

        Returns:
            Selected SessionTemplate (always returns exactly one)

        Raises:
            RuntimeError: If template library is empty (should never happen after init)
        """
        if not self.templates:
            raise RuntimeError("Template library is empty — cannot select template")

        # Build deterministic query
        query_text = build_template_query(
            domain=domain,
            session_type=session_type,
            race_distance=race_distance,
            phase=phase,
            philosophy=philosophy,
        )

        # Embed query once
        embedding_service = get_embedding_service()
        query_embedding = embedding_service.embed_text(query_text)

        # Compute cosine similarity for ALL templates and find argmax (no filtering)
        best_template = max(
            self.templates,
            key=lambda t: _cosine_similarity(query_embedding, t.embedding),
        )

        logger.debug(
            "Selected template via embedding",
            template_id=best_template.template_id,
            session_type=session_type,
            domain=domain,
        )

        return best_template.template


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two vectors.

    Args:
        a: First vector
        b: Second vector

    Returns:
        Cosine similarity score (0-1)
    """
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b)))


# Global template library instance (initialized at startup)
_template_library: TemplateLibrary | None = None


def is_template_library_initialized() -> bool:
    """Return True if the global template library has been initialized."""
    return _template_library is not None


def initialize_template_library(templates: list[EmbeddedTemplate]) -> None:
    """Initialize global template library at startup.

    Args:
        templates: List of embedded templates

    Raises:
        RuntimeError: If template library is empty
    """
    global _template_library
    _template_library = TemplateLibrary(templates)
    logger.info("Global template library initialized")


def get_template_library() -> TemplateLibrary:
    """Get global template library instance.

    Returns:
        TemplateLibrary instance

    Raises:
        RuntimeError: If template library not initialized
    """
    if _template_library is None:
        raise RuntimeError(
            "Template library not initialized. "
            "Call initialize_template_library() at startup."
        )
    return _template_library
