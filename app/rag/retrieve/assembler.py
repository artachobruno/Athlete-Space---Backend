"""Context assembler for structured RAG output.

This module assembles retrieved chunks into structured context,
ensuring proper ordering and labeling.
"""

from dataclasses import dataclass

from app.rag.types import RagChunk


@dataclass
class RagContext:
    """Structured RAG context with citations."""

    chunks: list[RagChunk]
    citations: list[str]


def assemble_context(chunks: list[RagChunk]) -> RagContext:
    """Assemble chunks into structured context.

    Args:
        chunks: Retrieved chunks

    Returns:
        RagContext with ordered chunks and citations
    """
    # Sort chunks by section_type priority, then by doc_id for consistency
    section_priority = {"principles": 0, "constraints": 1, "anti_patterns": 2, "other": 3}

    sorted_chunks = sorted(
        chunks,
        key=lambda c: (
            section_priority.get(c.metadata.get("section_type", "other"), 3),
            c.doc_id,
        ),
    )

    # Generate citations (doc_id for now, can be enhanced)
    citations = sorted({chunk.doc_id for chunk in sorted_chunks})

    return RagContext(chunks=sorted_chunks, citations=citations)


def format_context_for_llm(context: RagContext) -> str:
    """Format context for LLM consumption (optional helper).

    Args:
        context: RAG context

    Returns:
        Formatted string
    """
    parts: list[str] = []

    for chunk in context.chunks:
        section_type = chunk.metadata.get("section_type", "other")
        section_title = chunk.metadata.get("section_title", "")
        doc_id = chunk.doc_id

        parts.append(f"[{doc_id}] {section_type}: {section_title}")
        parts.append(chunk.text)
        parts.append("")

    return "\n".join(parts)
