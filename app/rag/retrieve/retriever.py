"""Deterministic retrieval API for RAG system.

This module implements metadata-first retrieval with vector similarity,
following the pipeline: metadata filter → rule-based exclusion → vector search → top-K.
"""

import importlib
from pathlib import Path

from app.rag.embed.embedder import embed_query
from app.rag.index.metadata_index import MetadataIndex
from app.rag.index.vector_index import VectorIndex
from app.rag.retrieve.filters import filter_by_athlete_tags, is_retrieval_safe
from app.rag.types import Domain, RagChunk


class RagRetriever:
    """RAG retriever with metadata-first filtering."""

    def __init__(
        self,
        vector_index: VectorIndex,
        metadata_index: MetadataIndex,
    ):
        """Initialize retriever.

        Args:
            vector_index: Vector index for similarity search
            metadata_index: Metadata index for filtering
        """
        self.vector_index = vector_index
        self.metadata_index = metadata_index

    def retrieve_chunks(
        self,
        *,
        query: str,
        domain: Domain,
        race_type: str,
        athlete_tags: list[str],
        k: int,
    ) -> list[RagChunk]:
        """Retrieve chunks using metadata-first pipeline.

        Pipeline:
        1. Metadata filter (domain, race_type)
        2. Rule-based exclusion (athlete_tags)
        3. Vector similarity search
        4. Top-K return

        Args:
            query: Query text
            domain: Required domain
            race_type: Required race type
            athlete_tags: Athlete tags for safety filtering
            k: Number of chunks to return

        Returns:
            List of retrieved chunks (may be empty if all filtered out)

        Raises:
            ValueError: If retrieval fails
        """
        # Step 1: Metadata filter
        filtered_chunks = self.metadata_index.filter(
            domain=domain,
            race_type=race_type,
        )

        if not filtered_chunks:
            return []

        # Step 2: Rule-based exclusion
        filtered_chunks = filter_by_athlete_tags(filtered_chunks, athlete_tags)

        if not filtered_chunks:
            return []

        # Step 3: Vector similarity search
        try:
            query_vector = embed_query(query)
        except Exception as e:
            raise ValueError(f"Failed to embed query: {e}") from e

        # Search all chunks, then filter results to only include chunks
        # that passed metadata/rule filters
        # This approach works with the current architecture where vector_index
        # contains all chunks. For larger corpora, a filtered vector index
        # would be more efficient.
        filtered_chunk_ids = {chunk.chunk_id for chunk in filtered_chunks}

        # Search more chunks than needed to account for filtering
        search_k = min(k * 5, self.vector_index.size())
        all_results = self.vector_index.search(query_vector, k=search_k)

        # Filter results to only include chunks that passed metadata/rule filters
        filtered_results: list[tuple[RagChunk, float]] = [
            (chunk, score) for chunk, score in all_results if chunk.chunk_id in filtered_chunk_ids
        ]

        # Step 4: Top-K return
        top_k = filtered_results[:k]
        return [chunk for chunk, _ in top_k]

    def retrieve_chunks_with_safety_check(
        self,
        *,
        query: str,
        domain: Domain,
        race_type: str,
        athlete_tags: list[str],
        k: int,
        min_chunks: int = 1,
    ) -> tuple[list[RagChunk], bool]:
        """Retrieve chunks with safety check.

        Args:
            query: Query text
            domain: Required domain
            race_type: Required race type
            athlete_tags: Athlete tags for safety filtering
            k: Number of chunks to return
            min_chunks: Minimum chunks required for safe retrieval

        Returns:
            Tuple of (retrieved chunks, is_safe)
        """
        chunks = self.retrieve_chunks(
            query=query,
            domain=domain,
            race_type=race_type,
            athlete_tags=athlete_tags,
            k=k,
        )

        safe = is_retrieval_safe(chunks, min_chunks=min_chunks)
        return chunks, safe

    @classmethod
    def from_artifacts(cls, artifacts_dir: Path) -> "RagRetriever":
        """Load retriever from pre-computed artifacts.

        This method loads artifacts from disk and constructs a retriever.
        It never generates embeddings or reads corpus files.

        Args:
            artifacts_dir: Directory containing artifacts

        Returns:
            RagRetriever instance

        Raises:
            RuntimeError: If artifacts are missing or manifest mismatch
        """
        # Import here to avoid circular import at module level
        # Using importlib to break the cycle
        pipeline_module = importlib.import_module("app.rag.pipeline")

        _, _, retriever = pipeline_module.RagPipeline.load_from_artifacts(artifacts_dir)
        return retriever
