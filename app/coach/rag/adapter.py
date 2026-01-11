"""RAG adapter for orchestrator integration.

This module provides an isolation layer between RAG internals and the orchestrator.
The orchestrator never sees raw RagChunk, Embedding, or VectorIndex objects.
"""

from pathlib import Path

from loguru import logger

from app.coach.rag.context import RagChunk, RagConfidence, RagContext
from app.rag.ingest.manifest import load_artifact_manifest
from app.rag.retrieve.confidence import compute_confidence
from app.rag.retrieve.retriever import RagRetriever
from app.rag.types import Domain


class OrchestratorRagAdapter:
    """Adapter that isolates orchestrator from RAG internals.

    This adapter:
    - Loads retriever from pre-computed artifacts (never triggers ingestion)
    - Returns only RagContext (canonical interface)
    - Never exposes embeddings, raw chunks, or internal types
    """

    def __init__(self, artifacts_dir: Path):
        """Initialize adapter from pre-computed artifacts.

        Args:
            artifacts_dir: Directory containing pre-computed RAG artifacts

        Raises:
            RuntimeError: If artifacts are missing or manifest mismatch
        """
        # Load retriever from artifacts (never triggers ingestion)
        self.retriever = RagRetriever.from_artifacts(artifacts_dir)

        # Load manifest for logging
        manifest_path = artifacts_dir / "manifest.json"
        manifest = load_artifact_manifest(manifest_path)

        logger.info(
            "[RAG] Loaded frozen index",
            version=manifest.embedding_version,
            model=manifest.embedding_model,
            chunks=manifest.chunk_count,
        )

    def retrieve_context(
        self,
        query: str,
        race_type: str,
        athlete_tags: list[str],
    ) -> RagContext:
        """Retrieve RAG context for orchestrator decision biasing.

        This is the ONLY way the orchestrator accesses RAG data.
        Returns structured, confidence-scored context only.

        Args:
            query: User query text
            race_type: Race type for filtering
            athlete_tags: Athlete tags for safety filtering

        Returns:
            RagContext with confidence and structured chunks

        Raises:
            ValueError: If retrieval fails
        """
        try:
            # Retrieve chunks using RAG pipeline
            chunks = self.retriever.retrieve_chunks(
                query=query,
                domain="training_philosophy",
                race_type=race_type,
                athlete_tags=athlete_tags,
                k=5,
            )

            # Compute confidence score
            confidence_result = compute_confidence(chunks, min_chunks=1, ideal_chunks=5)

            # Map confidence score to literal type
            confidence_literal: RagConfidence
            if confidence_result.score < 0.4:
                confidence_literal = "low"
            elif confidence_result.score < 0.7:
                confidence_literal = "medium"
            else:
                confidence_literal = "high"

            # Convert RAG chunks to orchestrator chunks
            orchestrator_chunks: list[RagChunk] = []
            chunk_ids: list[str] = []
            for chunk in chunks:
                # Extract tags from metadata (comma-separated string)
                tags_str = chunk.metadata.get("tags", "")
                tags = [tag.strip() for tag in tags_str.split(",") if tag.strip()] if tags_str else []

                # Create summary from chunk text (truncate to first 200 chars)
                summary = chunk.text[:200] + "..." if len(chunk.text) > 200 else chunk.text

                orchestrator_chunk = RagChunk(
                    id=chunk.chunk_id,
                    domain=chunk.metadata.get("domain", ""),
                    title=chunk.metadata.get("section_title", "Untitled"),
                    summary=summary,
                    tags=tags,
                    source_id=chunk.doc_id,
                )
                orchestrator_chunks.append(orchestrator_chunk)
                chunk_ids.append(chunk.chunk_id)

            # Log retrieval usage
            logger.info(
                "[RAG] Retrieval used",
                confidence=confidence_literal,
                chunks=chunk_ids,
                chunk_count=len(chunks),
            )

            return RagContext(
                query=query,
                confidence=confidence_literal,
                chunks=orchestrator_chunks,
            )

        except Exception as e:
            logger.warning(
                "RAG retrieval failed, returning empty context",
                query=query[:100] if query else "",
                race_type=race_type,
                error=str(e),
                exc_info=True,
            )
            # Return empty context with low confidence on failure
            return RagContext(
                query=query,
                confidence="low",
                chunks=[],
            )
