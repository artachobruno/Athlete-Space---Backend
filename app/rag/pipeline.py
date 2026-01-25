"""RAG ingestion and indexing pipeline.

This module provides the complete pipeline for ingesting documents,
chunking, embedding, and building indexes.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from app.rag.retrieve.retriever import RagRetriever

from app.rag.embed.embedder import EmbeddedChunk, embed_chunks
from app.rag.embed.model_lock import (
    EMBEDDING_MODEL,
    EMBEDDING_PROVIDER,
    EMBEDDING_VERSION,
    assert_embedding_lock,
)
from app.rag.index.metadata_index import MetadataIndex
from app.rag.index.vector_index import VectorIndex
from app.rag.ingest.chunker import chunk_corpus
from app.rag.ingest.loader import load_corpus
from app.rag.ingest.manifest import (
    create_artifact_manifest,
    create_manifest,
    load_artifact_manifest,
    save_artifact_manifest,
    save_manifest,
)
from app.rag.ingest.normalizer import normalize_corpus
from app.rag.types import RagChunk, RagDocument


class RagPipeline:
    """Complete RAG ingestion and indexing pipeline."""

    def __init__(
        self,
        corpus_dir: Path,
        metadata_path: Path,
    ):
        """Initialize pipeline.

        Args:
            corpus_dir: Root directory containing philosophy and principle files
            metadata_path: Path to metadata.yaml
        """
        self.corpus_dir = corpus_dir
        self.metadata_path = metadata_path

    def ingest(self) -> tuple[list[RagDocument], list[EmbeddedChunk], RagRetriever]:
        """Run complete ingestion pipeline.

        Steps:
        1. Load documents
        2. Normalize documents
        3. Chunk documents
        4. Embed chunks
        5. Build indexes
        6. Create retriever

        Returns:
            Tuple of (documents, embedded_chunks, retriever)
        """
        # Step 1: Load
        raw_documents = load_corpus(self.corpus_dir)

        # Step 2: Normalize
        documents = normalize_corpus(raw_documents, self.metadata_path, self.corpus_dir)

        # Step 3: Chunk
        chunks = chunk_corpus(documents)

        # Step 4: Embed
        embedded_chunks = embed_chunks(chunks)

        # Step 5: Build indexes
        vector_index = VectorIndex(embedded_chunks)
        metadata_index = MetadataIndex(chunks)

        # Step 6: Create retriever
        from app.rag.retrieve.retriever import RagRetriever  # noqa: PLC0415

        retriever = RagRetriever(
            vector_index=vector_index,
            metadata_index=metadata_index,
        )

        return documents, embedded_chunks, retriever

    def ingest_with_manifest(
        self, manifest_output_path: Path | None = None
    ) -> tuple[list[RagDocument], list[EmbeddedChunk], RagRetriever]:
        """Run ingestion pipeline and save manifest.

        Args:
            manifest_output_path: Optional path to save manifest

        Returns:
            Tuple of (documents, embedded_chunks, retriever)
        """
        documents, embedded_chunks, retriever = self.ingest()

        # Create and save manifest
        doc_ids = [doc.doc_id for doc in documents]

        manifest = create_manifest(doc_ids)

        if manifest_output_path:
            save_manifest(manifest, manifest_output_path)

        return documents, embedded_chunks, retriever

    def build_and_persist(self, output_dir: Path, embedding_version: str = EMBEDDING_VERSION) -> None:
        """Build RAG index and persist artifacts to disk.

        This method is called ONCE during build/CI, never at runtime.
        It generates embeddings, builds indexes, and saves all artifacts.

        Args:
            output_dir: Directory to save artifacts
            embedding_version: Embedding version (defaults to locked version)

        Raises:
            RuntimeError: If embedding version doesn't match locked version
        """
        if embedding_version != EMBEDDING_VERSION:
            raise RuntimeError(
                f"Embedding version mismatch: requested {embedding_version}, "
                f"locked version is {EMBEDDING_VERSION}"
            )

        # Run full ingestion pipeline
        documents, embedded_chunks, _ = self.ingest()

        # Ensure output directory exists
        output_dir.mkdir(parents=True, exist_ok=True)

        # Extract chunks and embeddings
        chunks = [ec.chunk for ec in embedded_chunks]
        embeddings = np.array([ec.vector for ec in embedded_chunks], dtype=np.float32)

        # Save chunks as JSON
        chunks_path = output_dir / "chunks.json"
        chunks_data = [asdict(chunk) for chunk in chunks]
        with chunks_path.open("w", encoding="utf-8") as f:
            json.dump(chunks_data, f, indent=2)

        # Save embeddings as .npy
        embeddings_path = output_dir / "embeddings.npy"
        np.save(embeddings_path, embeddings)

        # Create and save artifact manifest
        doc_ids = [doc.doc_id for doc in documents]
        manifest = create_artifact_manifest(
            embedding_version=embedding_version,
            embedding_model=EMBEDDING_MODEL,
            embedding_provider=EMBEDDING_PROVIDER,
            chunk_count=len(chunks),
            doc_ids=doc_ids,
        )
        manifest_path = output_dir / "manifest.json"
        save_artifact_manifest(manifest, manifest_path)

    @staticmethod
    def load_from_artifacts(artifacts_dir: Path) -> tuple[list[RagChunk], np.ndarray, RagRetriever]:
        """Load RAG artifacts from disk and reconstruct retriever.

        This method is called at runtime to load pre-computed artifacts.
        It never generates embeddings or reads corpus files.

        Args:
            artifacts_dir: Directory containing artifacts

        Returns:
            Tuple of (chunks, embeddings_array, retriever)

        Raises:
            RuntimeError: If artifacts are missing or manifest mismatch
        """
        # Check artifacts exist
        chunks_path = artifacts_dir / "chunks.json"
        embeddings_path = artifacts_dir / "embeddings.npy"
        manifest_path = artifacts_dir / "manifest.json"

        if not chunks_path.exists() or not embeddings_path.exists() or not manifest_path.exists():
            raise RuntimeError(
                f"RAG artifacts missing in {artifacts_dir} — run build_rag_index.py to generate artifacts"
            )

        # Load manifest and validate
        manifest = load_artifact_manifest(manifest_path)
        assert_embedding_lock(manifest.embedding_model, manifest.embedding_version)

        # Load chunks
        with chunks_path.open(encoding="utf-8") as f:
            chunks_data = json.load(f)
        chunks = [RagChunk(**data) for data in chunks_data]

        # Load embeddings
        embeddings = np.load(embeddings_path)

        # Validate dimensions match
        if len(chunks) != embeddings.shape[0]:
            raise RuntimeError(
                f"Chunk count mismatch: {len(chunks)} chunks but {embeddings.shape[0]} embeddings"
            )

        # Rebuild indexes using optimized interface that avoids list conversion
        # This saves memory by passing numpy arrays directly instead of converting
        # to lists and back to numpy arrays
        vector_index = VectorIndex(chunks=chunks, vectors=embeddings)
        metadata_index = MetadataIndex(chunks)

        # Create retriever
        from app.rag.retrieve.retriever import RagRetriever  # noqa: PLC0415

        retriever = RagRetriever(
            vector_index=vector_index,
            metadata_index=metadata_index,
        )

        return chunks, embeddings, retriever
