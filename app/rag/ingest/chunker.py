"""Deterministic chunker for RAG documents.

This module chunks documents by semantic headers, ensuring
reproducible chunk IDs and comprehensive metadata.
"""

import hashlib
import re

import tiktoken

from app.rag.types import RagChunk, RagDocument

# Use cl100k_base encoding (GPT-4 tokenizer)
ENCODING = tiktoken.get_encoding("cl100k_base")

# Chunk size constraints
MIN_CHUNK_TOKENS = 50
MAX_CHUNK_TOKENS = 500
TARGET_CHUNK_TOKENS = 400


def count_tokens(text: str) -> int:
    """Count tokens in text using tiktoken.

    Args:
        text: Text to count

    Returns:
        Token count
    """
    return len(ENCODING.encode(text))


def extract_sections(content: str) -> list[dict[str, str]]:
    """Extract semantic sections from markdown content.

    Args:
        content: Markdown content

    Returns:
        List of section dicts with 'title' and 'content'
    """
    sections: list[dict[str, str]] = []

    # Split by markdown headers (## and ###)
    header_pattern = r"^(#{2,3})\s+(.+)$"
    lines = content.split("\n")
    current_section: list[str] = []
    current_title = "Introduction"
    current_level = 0

    for line in lines:
        header_match = re.match(header_pattern, line)
        if header_match:
            # Save previous section
            if current_section:
                sections.append(
                    {
                        "title": current_title,
                        "content": "\n".join(current_section).strip(),
                        "level": str(current_level),
                    }
                )

            # Start new section
            current_level = len(header_match.group(1))
            current_title = header_match.group(2).strip()
            current_section = []
        else:
            current_section.append(line)

    # Add final section
    if current_section:
        sections.append(
            {
                "title": current_title,
                "content": "\n".join(current_section).strip(),
                "level": str(current_level),
            }
        )

    return sections


def determine_section_type(section_title: str) -> str:
    """Determine section type from title.

    Args:
        section_title: Section title

    Returns:
        Section type: 'principles', 'constraints', 'anti_patterns', or 'other'
    """
    title_lower = section_title.lower()

    if any(
        keyword in title_lower
        for keyword in ["principle", "core principle", "adaptation", "target"]
    ):
        return "principles"

    if any(
        keyword in title_lower
        for keyword in [
            "constraint",
            "rule",
            "sequencing",
            "gating",
            "guardrail",
            "structure",
        ]
    ):
        return "constraints"

    if any(
        keyword in title_lower
        for keyword in ["anti-pattern", "anti pattern", "failure", "avoid", "do not"]
    ):
        return "anti_patterns"

    return "other"


def split_large_section(section_content: str, max_tokens: int) -> list[str]:
    """Split a section that exceeds max_tokens into smaller chunks.

    Args:
        section_content: Section content
        max_tokens: Maximum tokens per chunk

    Returns:
        List of chunk texts
    """
    if count_tokens(section_content) <= max_tokens:
        return [section_content]

    # Split by paragraphs first
    paragraphs = section_content.split("\n\n")
    chunks: list[str] = []
    current_chunk: list[str] = []

    for para in paragraphs:
        para_tokens = count_tokens(para)
        current_tokens = count_tokens("\n\n".join(current_chunk))

        if current_tokens + para_tokens <= max_tokens:
            current_chunk.append(para)
            continue

        # Paragraph doesn't fit - save current chunk if exists
        if current_chunk:
            chunks.append("\n\n".join(current_chunk))
            current_chunk = []

        # If single paragraph is too large, split by sentences
        if para_tokens > max_tokens:
            sentences = re.split(r"(?<=[.!?])\s+", para)
            for sent in sentences:
                sent_tokens = count_tokens(sent)
                if current_tokens + sent_tokens <= max_tokens:
                    current_chunk.append(sent)
                    current_tokens += sent_tokens
                else:
                    if current_chunk:
                        chunks.append("\n\n".join(current_chunk))
                    current_chunk = [sent]
                    current_tokens = sent_tokens
        else:
            current_chunk = [para]

    if current_chunk:
        chunks.append("\n\n".join(current_chunk))

    return chunks


def generate_chunk_id(doc_id: str, section_title: str, chunk_index: int) -> str:
    """Generate deterministic chunk ID.

    Args:
        doc_id: Document ID
        section_title: Section title
        chunk_index: Index of chunk within section

    Returns:
        Deterministic chunk ID (SHA256 hash)
    """
    content = f"{doc_id}:{section_title}:{chunk_index}"
    return hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]


def chunk_document(doc: RagDocument) -> list[RagChunk]:
    """Chunk a document into semantic chunks.

    Args:
        doc: Document to chunk

    Returns:
        List of RagChunk instances
    """
    chunks: list[RagChunk] = []
    sections = extract_sections(doc.content)

    for section in sections:
        section_title = section["title"]
        section_content = section["content"]
        section_type = determine_section_type(section_title)

        # Skip empty sections
        if not section_content.strip():
            continue

        # Split large sections
        section_chunks = split_large_section(section_content, MAX_CHUNK_TOKENS)

        for idx, chunk_text in enumerate(section_chunks):
            # Skip chunks that are too small
            if count_tokens(chunk_text) < MIN_CHUNK_TOKENS:
                continue

            chunk_id = generate_chunk_id(doc.doc_id, section_title, idx)

            # Build metadata
            metadata: dict[str, str] = {
                "doc_id": doc.doc_id,
                "philosophy_id": doc.doc_id,  # Same as doc_id for philosophies
                "domain": doc.domain,
                "race_types": ",".join(doc.race_types),
                "risk_level": doc.risk_level,
                "audience": doc.audience,
                "section_type": section_type,
                "section_title": section_title,
                "category": doc.category,
                "subcategory": doc.subcategory,
                "tags": ",".join(doc.tags),
                "requires": ",".join(doc.requires),
                "prohibits": ",".join(doc.prohibits),
            }

            chunk = RagChunk(
                chunk_id=chunk_id,
                doc_id=doc.doc_id,
                text=chunk_text,
                metadata=metadata,
            )

            chunks.append(chunk)

    return chunks


def chunk_corpus(documents: list[RagDocument]) -> list[RagChunk]:
    """Chunk all documents in the corpus.

    Args:
        documents: List of documents to chunk

    Returns:
        List of all chunks
    """
    all_chunks: list[RagChunk] = []

    for doc in documents:
        chunks = chunk_document(doc)
        all_chunks.extend(chunks)

    return all_chunks
