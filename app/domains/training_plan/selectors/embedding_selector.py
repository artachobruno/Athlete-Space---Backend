"""Generic embedding selector for semantic retrieval.

This module provides a generic function for selecting the best item from a list
using embedding similarity. No filtering, no thresholds, no fallbacks - pure
embedding-based selection.

Guarantees:
- Always selects exactly one item (unless items list is empty, which should never happen)
- O(N) complexity
- No exceptions (unless items is empty, which is a programming error)
"""

from collections.abc import Callable
from typing import TypeVar

import numpy as np
from loguru import logger

T = TypeVar("T")


def cosine_similarity(vec1: list[float], vec2: list[float]) -> float:
    """Compute cosine similarity between two vectors.

    Args:
        vec1: First vector
        vec2: Second vector

    Returns:
        Cosine similarity score (0-1)
    """
    vec1_array = np.array(vec1, dtype=np.float32)
    vec2_array = np.array(vec2, dtype=np.float32)

    # Normalize vectors
    norm1 = np.linalg.norm(vec1_array)
    norm2 = np.linalg.norm(vec2_array)

    if norm1 == 0 or norm2 == 0:
        return 0.0

    vec1_normalized = vec1_array / norm1
    vec2_normalized = vec2_array / norm2

    # Cosine similarity is dot product of normalized vectors
    return float(np.dot(vec1_normalized, vec2_normalized))


def select_best_by_embedding(
    *,
    items: list[T],
    query_text: str,
    embed_fn: Callable[[str], list[float]],
    vector_attr: str = "embedding",
) -> T:
    """Select the best item from a list using embedding similarity.

    This function:
    1. Embeds the query text
    2. Computes cosine similarity for all items
    3. Returns the item with the highest similarity

    No filtering, no thresholds, no fallbacks - pure embedding selection.

    Args:
        items: List of items to select from (must be non-empty)
        query_text: Query text to embed
        embed_fn: Function that embeds text to a vector
        vector_attr: Attribute name on items that contains the embedding vector

    Returns:
        Item with the highest similarity score

    Raises:
        ValueError: If items list is empty (programming error)
        RuntimeError: If no item was selected (should never happen)
    """
    if not items:
        raise ValueError("Embedding selector received empty item list")

    query_vec = embed_fn(query_text)

    best_item = None
    best_score = float("-inf")

    for item in items:
        vec = getattr(item, vector_attr)
        score = cosine_similarity(query_vec, vec)
        if score > best_score:
            best_item = item
            best_score = score

    if best_item is None:
        raise RuntimeError("Failed to select item (should never happen)")

    selected_id = (
        getattr(best_item, "id", None)
        or getattr(best_item, "template_id", None)
        or getattr(best_item, "philosophy_id", None)
        or "unknown"
    )
    logger.info(
        "embedding_selection",
        selected_id=selected_id,
        score=round(best_score, 4),
        candidates_count=len(items),
    )

    return best_item
