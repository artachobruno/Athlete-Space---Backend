"""In-memory vector store for semantic retrieval.

This module provides a simple, fast in-memory vector store using numpy for
cosine similarity computation. Designed to be loaded once at startup and
used read-only during request handling.
"""

from dataclasses import dataclass

import numpy as np
from loguru import logger


@dataclass
class EmbeddedItem:
    """An item with its embedding and metadata.

    Attributes:
        id: Unique identifier
        embedding: Embedding vector (list of floats)
        metadata: Metadata dictionary (not embedded, used for filtering)
    """

    id: str
    embedding: list[float]
    metadata: dict[str, str | int | list[str]]


class VectorStore:
    """In-memory vector store for semantic retrieval.

    Thread-safe for read operations. Load embeddings once at startup,
    then query efficiently using cosine similarity.

    Attributes:
        items: List of embedded items
        embeddings_matrix: Numpy array of embeddings (N x D)
        ids: List of item IDs (parallel to embeddings_matrix)
        metadata: List of metadata dicts (parallel to embeddings_matrix)
    """

    def __init__(self, items: list[EmbeddedItem]) -> None:
        """Initialize vector store from embedded items.

        Args:
            items: List of embedded items to store
        """
        if not items:
            self.items: list[EmbeddedItem] = []
            self.embeddings_matrix: np.ndarray = np.array([])
            self.ids: list[str] = []
            self.metadata: list[dict[str, str | int | list[str]]] = []
            logger.warning("VectorStore initialized with empty items list")
            return

        self.items = items
        self.ids = [item.id for item in items]
        self.metadata = [item.metadata for item in items]

        # Convert embeddings to numpy array for efficient computation
        embeddings_list = [item.embedding for item in items]
        self.embeddings_matrix = np.array(embeddings_list, dtype=np.float32)

        # Normalize embeddings for cosine similarity
        norms = np.linalg.norm(self.embeddings_matrix, axis=1, keepdims=True)
        norms = np.where(norms == 0, 1, norms)  # Avoid division by zero
        self.embeddings_matrix /= norms

        logger.info(
            f"Initialized VectorStore with {len(items)} items, "
            f"embedding_dim={self.embeddings_matrix.shape[1]}"
        )

    def query(
        self,
        query_embedding: list[float],
        top_k: int = 5,
        min_similarity: float = 0.0,
        candidate_ids: set[str] | None = None,
    ) -> list[tuple[str, float, dict[str, str | int | list[str]]]]:
        """Query the vector store for similar items.

        Args:
            query_embedding: Query embedding vector
            top_k: Number of top results to return
            min_similarity: Minimum similarity threshold (0.0 to 1.0)
            candidate_ids: Optional set of candidate IDs to filter by (only search within these)

        Returns:
            List of tuples: (item_id, similarity_score, metadata)
            Sorted by similarity (highest first)
        """
        if len(self.items) == 0:
            return []

        # Normalize query embedding
        query_vec = np.array(query_embedding, dtype=np.float32)
        query_norm = np.linalg.norm(query_vec)
        if query_norm == 0:
            logger.warning("Query embedding has zero norm")
            return []
        query_vec /= query_norm

        # Compute cosine similarities (dot product of normalized vectors)
        similarities = np.dot(self.embeddings_matrix, query_vec)

        # Filter by candidate IDs first (if provided)
        if candidate_ids is not None:
            candidate_mask = np.array([self.ids[i] in candidate_ids for i in range(len(self.ids))])
            similarities = np.where(candidate_mask, similarities, -1.0)

        # Filter by minimum similarity
        mask = similarities >= min_similarity
        filtered_indices = np.where(mask)[0]
        filtered_similarities = similarities[filtered_indices]

        if len(filtered_indices) == 0:
            return []

        # Get top-k indices
        top_indices = np.argsort(filtered_similarities)[::-1][:top_k]

        # Build results
        results: list[tuple[str, float, dict[str, str | int | list[str]]]] = []
        for idx in top_indices:
            original_idx = filtered_indices[idx]
            similarity = float(filtered_similarities[idx])
            item_id = self.ids[original_idx]
            metadata = self.metadata[original_idx]
            results.append((item_id, similarity, metadata))

        return results

    def get_item(self, item_id: str) -> EmbeddedItem | None:
        """Get an item by ID.

        Args:
            item_id: Item identifier

        Returns:
            EmbeddedItem if found, None otherwise
        """
        for item in self.items:
            if item.id == item_id:
                return item
        return None

    def size(self) -> int:
        """Get number of items in the store.

        Returns:
            Number of items
        """
        return len(self.items)
