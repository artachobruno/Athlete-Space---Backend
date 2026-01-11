"""Vector index for cosine similarity search.

This module provides exact cosine similarity search over embedded chunks.
No ANN (approximate nearest neighbor) is used - exact search is acceptable
for the corpus size.
"""


import numpy as np

from app.rag.embed.embedder import EmbeddedChunk
from app.rag.types import RagChunk


class VectorIndex:
    """In-memory vector index with exact cosine similarity search."""

    def __init__(self, embedded_chunks: list[EmbeddedChunk]):
        """Initialize vector index.

        Args:
            embedded_chunks: List of embedded chunks to index
        """
        self.chunks: list[RagChunk] = [ec.chunk for ec in embedded_chunks]
        self.vectors = np.array([ec.vector for ec in embedded_chunks], dtype=np.float32)

        # Normalize vectors for cosine similarity
        norms = np.linalg.norm(self.vectors, axis=1, keepdims=True)
        norms = np.where(norms == 0, 1, norms)  # Avoid division by zero
        self.normalized_vectors = self.vectors / norms

    def search(self, query_vector: list[float], k: int) -> list[tuple[RagChunk, float]]:
        """Search for top-K chunks by cosine similarity.

        Args:
            query_vector: Query embedding vector
            k: Number of results to return

        Returns:
            List of (chunk, similarity_score) tuples, sorted by similarity descending
        """
        if not self.chunks:
            return []

        # Normalize query vector
        query_array = np.array(query_vector, dtype=np.float32)
        query_norm = np.linalg.norm(query_array)
        if query_norm == 0:
            return []

        normalized_query = query_array / query_norm

        # Compute cosine similarities
        similarities = np.dot(self.normalized_vectors, normalized_query)

        # Get top-K indices
        top_k_indices = np.argsort(similarities)[::-1][:k]

        # Return chunks with scores
        results: list[tuple[RagChunk, float]] = []

        for idx in top_k_indices:
            chunk = self.chunks[idx]
            score = float(similarities[idx])
            results.append((chunk, score))

        return results

    def size(self) -> int:
        """Get the number of chunks in the index.

        Returns:
            Number of chunks
        """
        return len(self.chunks)
