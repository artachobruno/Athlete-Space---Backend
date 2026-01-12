"""Embedding-only session template selector.

This module provides a simple, deterministic template selector that:
- Always returns exactly one template
- No thresholds
- No fallbacks
- O(N) complexity
- Uses embeddings only

The only way this fails is if the template list is empty (configuration error).
"""

import numpy as np
from loguru import logger

from app.domains.training_plan.models import SessionTemplate
from app.embeddings.embedding_service import get_embedding_service


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two vectors.

    Args:
        a: First vector
        b: Second vector

    Returns:
        Cosine similarity score (0-1)
    """
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b)))


def select_best_template(
    *,
    templates_with_embeddings: list[tuple[SessionTemplate, list[float]]],
    query_text: str,
) -> SessionTemplate:
    """Select best template using embedding similarity.

    This function:
    1. Embeds the query text once
    2. Computes cosine similarity against all templates
    3. Returns the template with highest similarity (argmax)

    Args:
        templates_with_embeddings: List of tuples (SessionTemplate, embedding)
        query_text: Query text to match against

    Returns:
        Selected SessionTemplate (always returns exactly one)

    Raises:
        AssertionError: If templates list is empty (should never happen)
    """
    if not templates_with_embeddings:
        raise AssertionError("Template list is empty — cannot select template")

    # Embed query once
    embedding_service = get_embedding_service()
    query_vec = embedding_service.embed_text(query_text)

    # Find best template by cosine similarity
    best_template: SessionTemplate | None = None
    best_score = -1.0

    for template, template_embedding in templates_with_embeddings:
        score = _cosine_similarity(query_vec, template_embedding)
        if score > best_score:
            best_template = template
            best_score = score

    # Hard check: This should never fire if templates list is non-empty
    if best_template is None:
        raise RuntimeError("Embedding selector returned no template")

    logger.info(
        "Single-day template selected",
        template_id=best_template.template_id,
        score=best_score,
    )

    return best_template
