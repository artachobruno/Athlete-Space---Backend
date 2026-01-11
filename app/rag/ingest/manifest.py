"""Manifest generation for RAG corpus.

This module creates and manages ingestion manifests for auditability.
"""

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass
class RagManifest:
    """Manifest tracking corpus ingestion.

    This manifest is saved alongside the index for auditability
    and reproducibility.
    """

    version: str
    doc_ids: list[str]
    created_at: str


@dataclass
class RagArtifactManifest:
    """Manifest for persisted RAG artifacts.

    Includes embedding model info, chunk count, and scope hash
    for drift detection and versioning.
    """

    version: str
    embedding_model: str
    embedding_version: str
    embedding_provider: str
    chunk_count: int
    created_at: str
    scope_hash: str


def create_manifest(doc_ids: list[str], version: str = "1.0") -> RagManifest:
    """Create a new manifest.

    Args:
        doc_ids: List of document IDs in the corpus
        version: Manifest version

    Returns:
        RagManifest instance
    """
    return RagManifest(
        version=version,
        doc_ids=sorted(doc_ids),
        created_at=datetime.now(timezone.utc).isoformat(),
    )


def compute_scope_hash(doc_ids: list[str]) -> str:
    """Compute hash of document IDs for scope tracking.

    Args:
        doc_ids: List of document IDs

    Returns:
        SHA256 hash of sorted doc_ids
    """
    sorted_ids = sorted(doc_ids)
    ids_str = ",".join(sorted_ids)
    return hashlib.sha256(ids_str.encode("utf-8")).hexdigest()[:12]


def create_artifact_manifest(
    embedding_version: str,
    embedding_model: str,
    embedding_provider: str,
    chunk_count: int,
    doc_ids: list[str],
) -> RagArtifactManifest:
    """Create artifact manifest with embedding metadata.

    Args:
        embedding_version: Embedding version (e.g., "v1.0")
        embedding_model: Embedding model name
        embedding_provider: Embedding provider (e.g., "openai")
        chunk_count: Number of chunks
        doc_ids: List of document IDs for scope hash

    Returns:
        RagArtifactManifest instance
    """
    return RagArtifactManifest(
        version=embedding_version,
        embedding_model=embedding_model,
        embedding_version=embedding_version,
        embedding_provider=embedding_provider,
        chunk_count=chunk_count,
        created_at=datetime.now(timezone.utc).isoformat(),
        scope_hash=compute_scope_hash(doc_ids),
    )


def save_artifact_manifest(manifest: RagArtifactManifest, output_path: Path) -> None:
    """Save artifact manifest to disk.

    Args:
        manifest: Manifest to save
        output_path: Path to save manifest JSON
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with Path(output_path).open("w", encoding="utf-8") as f:
        json.dump(asdict(manifest), f, indent=2)


def load_artifact_manifest(manifest_path: Path) -> RagArtifactManifest:
    """Load artifact manifest from disk.

    Args:
        manifest_path: Path to manifest JSON

    Returns:
        RagArtifactManifest instance
    """
    with Path(manifest_path).open(encoding="utf-8") as f:
        data = json.load(f)
        return RagArtifactManifest(**data)


def save_manifest(manifest: RagManifest, output_path: Path) -> None:
    """Save manifest to disk.

    Args:
        manifest: Manifest to save
        output_path: Path to save manifest JSON
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with Path(output_path).open("w", encoding="utf-8") as f:
        json.dump(asdict(manifest), f, indent=2)


def load_manifest(manifest_path: Path) -> RagManifest:
    """Load manifest from disk.

    Args:
        manifest_path: Path to manifest JSON

    Returns:
        RagManifest instance
    """
    with Path(manifest_path).open(encoding="utf-8") as f:
        data = json.load(f)
        return RagManifest(**data)
