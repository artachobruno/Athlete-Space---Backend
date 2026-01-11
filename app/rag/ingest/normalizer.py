"""Document normalizer for RAG ingestion.

This module normalizes document metadata, enforces scope locking,
and validates domain constraints.
"""

from pathlib import Path

import yaml

from app.rag.types import Domain, RagDocument


def load_metadata_config(metadata_path: Path) -> dict:
    """Load and parse metadata.yaml configuration.

    Args:
        metadata_path: Path to metadata.yaml

    Returns:
        Configuration dictionary
    """
    with Path(metadata_path).open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def normalize_domain(domain_raw: str, category: str) -> Domain:
    """Normalize domain field to canonical domain type.

    Args:
        domain_raw: Raw domain value from frontmatter
        category: Document category

    Returns:
        Normalized Domain value

    Raises:
        ValueError: If domain cannot be normalized to allowed domain
    """
    # Handle legacy "running" domain in principles
    if domain_raw == "running" and category == "principle":
        return "training_principles"

    # Direct mapping
    if domain_raw == "training_philosophy":
        return "training_philosophy"
    if domain_raw == "training_principles":
        return "training_principles"

    # Default based on category (for philosophy files)
    if category in {"running", "ultra"}:
        return "training_philosophy"
    if category == "principle":
        return "training_principles"

    # If domain_raw is already a valid Domain, return it
    if domain_raw in {"training_philosophy", "training_principles"}:
        return domain_raw  # type: ignore[return-value]

    raise ValueError(f"Cannot normalize domain '{domain_raw}' with category '{category}'")


def normalize_id(doc_id: str) -> str:
    """Normalize document ID to canonical format.

    Args:
        doc_id: Raw document ID

    Returns:
        Normalized ID (lowercase, underscores)
    """
    return doc_id.lower().replace("-", "_").replace(" ", "_")


def normalize_tags(tags: list[str]) -> list[str]:
    """Normalize tag list.

    Args:
        tags: Raw tag list

    Returns:
        Normalized tag list (lowercase, deduplicated, sorted)
    """
    normalized = [t.lower().replace("-", "_").replace(" ", "_") for t in tags]
    return sorted(set(normalized))


def normalize_race_types(race_types: list[str]) -> list[str]:
    """Normalize race type list.

    Args:
        race_types: Raw race type list

    Returns:
        Normalized race type list (lowercase, deduplicated, sorted)
    """
    normalized = [rt.lower().replace("-", "_") for rt in race_types]
    return sorted(set(normalized))


def normalize_document(
    doc: RagDocument, metadata_config: dict, corpus_dir: Path  # noqa: ARG001
) -> RagDocument:
    """Normalize a document according to scope rules.

    Args:
        doc: Raw document from loader
        metadata_config: Metadata configuration dict
        corpus_dir: Corpus root directory

    Returns:
        Normalized RagDocument

    Raises:
        ValueError: If document violates scope rules
    """
    # Check scope_locked
    if not metadata_config.get("scope_locked"):
        raise ValueError(f"Corpus scope is not locked. Cannot ingest {doc.doc_id}")

    # Normalize domain
    allowed_domains = metadata_config.get("allowed_domains", [])
    normalized_domain = normalize_domain(doc.domain, doc.category)

    if normalized_domain not in allowed_domains:
        raise ValueError(
            f"Document {doc.doc_id} has domain '{normalized_domain}' "
            f"not in allowed domains: {allowed_domains}"
        )

    # Normalize fields
    normalized_id = normalize_id(doc.doc_id)
    normalized_tags = normalize_tags(doc.tags)
    normalized_race_types = normalize_race_types(doc.race_types)
    normalized_requires = normalize_tags(doc.requires)
    normalized_prohibits = normalize_tags(doc.prohibits)

    # Strip examples and prescriptions from content
    # This is a simple heuristic - in practice, content should be pre-cleaned
    content = doc.content

    # Remove example sections if present (basic heuristic)
    content_lines = content.split("\n")
    filtered_lines: list[str] = []
    skip_example = False

    for line in content_lines:
        # Skip example sections
        if "## Example" in line or "### Example" in line:
            skip_example = True
            continue
        if skip_example and line.startswith("##"):
            skip_example = False
        if not skip_example:
            filtered_lines.append(line)

    normalized_content = "\n".join(filtered_lines).strip()

    return RagDocument(
        doc_id=normalized_id,
        domain=normalized_domain,
        category=doc.category,
        subcategory=doc.subcategory,
        tags=normalized_tags,
        race_types=normalized_race_types,
        risk_level=doc.risk_level.lower(),
        audience=doc.audience.lower(),
        requires=normalized_requires,
        prohibits=normalized_prohibits,
        source=doc.source,
        version=doc.version,
        content=normalized_content,
    )


def normalize_corpus(
    documents: list[RagDocument], metadata_path: Path, corpus_dir: Path
) -> list[RagDocument]:
    """Normalize all documents in the corpus.

    Args:
        documents: Raw documents from loader
        metadata_path: Path to metadata.yaml
        corpus_dir: Corpus root directory

    Returns:
        List of normalized RagDocument instances
    """
    metadata_config = load_metadata_config(metadata_path)
    normalized: list[RagDocument] = []

    for doc in documents:
        try:
            normalized_doc = normalize_document(doc, metadata_config, corpus_dir)
            normalized.append(normalized_doc)
        except ValueError as e:
            raise ValueError(f"Failed to normalize {doc.doc_id}: {e}") from e

    return normalized
