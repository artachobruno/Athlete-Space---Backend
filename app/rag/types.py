"""Canonical RAG types for deterministic retrieval system.

This module defines the core data structures used throughout the RAG pipeline.
All types are frozen dataclasses to ensure immutability and deterministic behavior.
"""

from dataclasses import dataclass
from typing import Literal

Domain = Literal["training_philosophy", "training_principles"]


@dataclass(frozen=True)
class RagDocument:
    """A canonical RAG document with validated metadata.

    All fields are required and validated during ingestion.
    This ensures deterministic filtering and retrieval.
    """

    doc_id: str
    domain: Domain
    category: str
    subcategory: str
    tags: list[str]
    race_types: list[str]
    risk_level: str
    audience: str
    requires: list[str]
    prohibits: list[str]
    source: str
    version: str
    content: str


@dataclass(frozen=True)
class RagChunk:
    """A chunk of text from a RAG document with metadata.

    Each chunk is deterministically generated and includes
    all necessary metadata for filtering and retrieval.
    """

    chunk_id: str
    doc_id: str
    text: str
    metadata: dict[str, str]
