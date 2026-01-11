"""Markdown document loader with YAML frontmatter parsing.

This module loads and parses Markdown files from the RAG corpus,
validating required fields and rejecting forbidden domains.
"""

import re
from pathlib import Path
from typing import Optional

import yaml

from app.rag.types import Domain, RagDocument


def parse_frontmatter(content: str) -> tuple[dict[str, str | list[str]], str]:
    """Parse YAML frontmatter from Markdown content.

    Args:
        content: Full markdown file content

    Returns:
        Tuple of (frontmatter dict, body content)
    """
    frontmatter_pattern = r"^---\s*\n(.*?)\n---\s*\n(.*)$"
    match = re.match(frontmatter_pattern, content, re.DOTALL)

    if not match:
        raise ValueError("Missing or malformed YAML frontmatter")

    frontmatter_text = match.group(1)
    body = match.group(2).strip()

    try:
        frontmatter = yaml.safe_load(frontmatter_text)
        if not isinstance(frontmatter, dict):
            raise TypeError("Frontmatter must be a YAML dictionary")
    except yaml.YAMLError as e:
        raise ValueError(f"Invalid YAML frontmatter: {e}") from e
    else:
        return frontmatter, body


def load_document(file_path: Path) -> RagDocument:
    """Load and parse a single Markdown document.

    Args:
        file_path: Path to the markdown file

    Returns:
        RagDocument with validated fields

    Raises:
        ValueError: If required fields are missing or invalid
    """
    content = file_path.read_text(encoding="utf-8")
    frontmatter, body = parse_frontmatter(content)

    # Extract required fields
    doc_id = frontmatter.get("id")
    if not doc_id or not isinstance(doc_id, str):
        raise ValueError(f"Missing or invalid 'id' field in {file_path}")

    domain_raw = frontmatter.get("domain")
    if not domain_raw or not isinstance(domain_raw, str):
        raise ValueError(f"Missing or invalid 'domain' field in {file_path}")

    # Normalize domain (will be fully validated in normalizer)
    domain = domain_raw

    category = frontmatter.get("category", "")
    if not isinstance(category, str):
        raise TypeError(f"Invalid 'category' field in {file_path}")

    # Extract list fields
    tags = frontmatter.get("tags", [])
    if not isinstance(tags, list):
        tags = []
    tags = [str(t) for t in tags if isinstance(t, (str, int, float))]

    race_types = frontmatter.get("race_types", [])
    if not isinstance(race_types, list):
        race_types = []
    race_types = [str(rt) for rt in race_types if isinstance(rt, (str, int, float))]

    requires = frontmatter.get("requires", [])
    if not isinstance(requires, list):
        requires = []
    requires = [str(r) for r in requires if isinstance(r, (str, int, float))]

    prohibits = frontmatter.get("prohibits", [])
    if not isinstance(prohibits, list):
        prohibits = []
    prohibits = [str(p) for p in prohibits if isinstance(p, (str, int, float))]

    risk_level = frontmatter.get("risk_level", "low")
    if not isinstance(risk_level, str):
        risk_level = "low"

    audience = frontmatter.get("audience", "all")
    if not isinstance(audience, str):
        audience = "all"

    source = frontmatter.get("source", "canonical")
    if not isinstance(source, str):
        source = "canonical"

    version = frontmatter.get("version", "1.0")
    if not isinstance(version, str):
        version = "1.0"

    # Determine subcategory from file path
    subcategory = ""
    parts = file_path.parts
    if "philosophies" in parts:
        idx = parts.index("philosophies")
        if idx + 1 < len(parts):
            subcategory = parts[idx + 1]  # e.g., "running" or "ultra"
    elif "principles" in parts:
        subcategory = "principle"

    # Domain will be normalized later, cast for now
    domain_typed: Domain = domain  # type: ignore[assignment]

    return RagDocument(
        doc_id=doc_id,
        domain=domain_typed,  # Will be normalized later
        category=category,
        subcategory=subcategory,
        tags=tags,
        race_types=race_types,
        risk_level=risk_level,
        audience=audience,
        requires=requires,
        prohibits=prohibits,
        source=source,
        version=version,
        content=body,
    )


def load_corpus(corpus_dir: Path) -> list[RagDocument]:
    """Load all Markdown documents from the corpus directory.

    Args:
        corpus_dir: Root directory containing philosophy and principle files

    Returns:
        List of loaded RagDocument instances
    """
    documents: list[RagDocument] = []

    # Load philosophy files
    philosophies_dir = corpus_dir / "philosophies"
    if philosophies_dir.exists():
        for category_dir in philosophies_dir.iterdir():
            if category_dir.is_dir():
                for md_file in category_dir.glob("*.md"):
                    try:
                        doc = load_document(md_file)
                        documents.append(doc)
                    except Exception as e:
                        raise ValueError(f"Failed to load {md_file}: {e}") from e

    # Load principle files
    principles_dir = corpus_dir / "principles"
    if principles_dir.exists():
        for md_file in principles_dir.glob("*.md"):
            try:
                doc = load_document(md_file)
                documents.append(doc)
            except Exception as e:
                raise ValueError(f"Failed to load {md_file}: {e}") from e

    return documents
