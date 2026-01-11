"""Offline evaluation harness for RAG retrieval.

This module provides golden query sets and evaluation metrics
for testing retrieval quality.
"""

from dataclasses import dataclass

from app.rag.retrieve.retriever import RagRetriever
from app.rag.types import Domain


@dataclass
class GoldenQuery:
    """A golden query with expected results."""

    query: str
    domain: Domain
    race_type: str
    athlete_tags: list[str]
    expected_doc_ids: set[str]
    k: int = 5


@dataclass
class RetrievalEvalResult:
    """Evaluation result for a single query."""

    query: str
    retrieved_doc_ids: set[str]
    expected_doc_ids: set[str]
    precision_at_k: float
    recall_at_k: float
    f1_at_k: float


def precision_at_k(retrieved: set[str], expected: set[str], k: int) -> float:
    """Compute precision@K.

    Args:
        retrieved: Set of retrieved document IDs
        expected: Set of expected document IDs
        k: K value

    Returns:
        Precision@K score (0.0-1.0)
    """
    if not retrieved:
        return 0.0

    # Take top K retrieved
    retrieved_top_k = list(retrieved)[:k]
    retrieved_set = set(retrieved_top_k)

    if not retrieved_set:
        return 0.0

    intersection = retrieved_set & expected
    return len(intersection) / len(retrieved_set)


def recall_at_k(retrieved: set[str], expected: set[str], k: int) -> float:
    """Compute recall@K.

    Args:
        retrieved: Set of retrieved document IDs
        expected: Set of expected document IDs
        k: K value

    Returns:
        Recall@K score (0.0-1.0)
    """
    if not expected:
        return 1.0

    # Take top K retrieved
    retrieved_top_k = list(retrieved)[:k]
    retrieved_set = set(retrieved_top_k)

    intersection = retrieved_set & expected
    return len(intersection) / len(expected)


def f1_at_k(retrieved: set[str], expected: set[str], k: int) -> float:
    """Compute F1@K.

    Args:
        retrieved: Set of retrieved document IDs
        expected: Set of expected document IDs
        k: K value

    Returns:
        F1@K score (0.0-1.0)
    """
    precision = precision_at_k(retrieved, expected, k)
    recall = recall_at_k(retrieved, expected, k)

    if precision + recall == 0:
        return 0.0

    return 2 * (precision * recall) / (precision + recall)


def evaluate_query(
    retriever: RagRetriever,
    golden_query: GoldenQuery,
) -> RetrievalEvalResult:
    """Evaluate retrieval for a single golden query.

    Args:
        retriever: RAG retriever instance
        golden_query: Golden query with expected results

    Returns:
        Evaluation result
    """
    chunks = retriever.retrieve_chunks(
        query=golden_query.query,
        domain=golden_query.domain,
        race_type=golden_query.race_type,
        athlete_tags=golden_query.athlete_tags,
        k=golden_query.k,
    )

    retrieved_doc_ids = {chunk.doc_id for chunk in chunks}

    precision = precision_at_k(retrieved_doc_ids, golden_query.expected_doc_ids, golden_query.k)
    recall = recall_at_k(retrieved_doc_ids, golden_query.expected_doc_ids, golden_query.k)
    f1 = f1_at_k(retrieved_doc_ids, golden_query.expected_doc_ids, golden_query.k)

    return RetrievalEvalResult(
        query=golden_query.query,
        retrieved_doc_ids=retrieved_doc_ids,
        expected_doc_ids=golden_query.expected_doc_ids,
        precision_at_k=precision,
        recall_at_k=recall,
        f1_at_k=f1,
    )


def evaluate_retrieval(
    retriever: RagRetriever,
    golden_queries: list[GoldenQuery],
) -> list[RetrievalEvalResult]:
    """Evaluate retrieval on a set of golden queries.

    Args:
        retriever: RAG retriever instance
        golden_queries: List of golden queries

    Returns:
        List of evaluation results
    """
    results: list[RetrievalEvalResult] = []

    for golden_query in golden_queries:
        try:
            result = evaluate_query(retriever, golden_query)
            results.append(result)
        except Exception as e:
            # Log error but continue evaluation
            print(f"Error evaluating query '{golden_query.query}': {e}")

    return results


def compute_aggregate_metrics(results: list[RetrievalEvalResult]) -> dict:
    """Compute aggregate metrics across all evaluation results.

    Args:
        results: List of evaluation results

    Returns:
        Dictionary with aggregate metrics
    """
    if not results:
        return {
            "num_queries": 0,
            "avg_precision": 0.0,
            "avg_recall": 0.0,
            "avg_f1": 0.0,
        }

    avg_precision = sum(r.precision_at_k for r in results) / len(results)
    avg_recall = sum(r.recall_at_k for r in results) / len(results)
    avg_f1 = sum(r.f1_at_k for r in results) / len(results)

    return {
        "num_queries": len(results),
        "avg_precision": avg_precision,
        "avg_recall": avg_recall,
        "avg_f1": avg_f1,
    }


# Golden query sets for testing
GOLDEN_QUERIES: list[GoldenQuery] = [
    GoldenQuery(
        query="What training philosophy is best for injury-prone marathon runners?",
        domain="training_philosophy",
        race_type="marathon",
        athlete_tags=["injury_prone"],
        expected_doc_ids={"8020_polarized", "durability_first"},
        k=5,
    ),
    GoldenQuery(
        query="How should I structure intensity distribution for a 5K race?",
        domain="training_principles",
        race_type="5k",
        athlete_tags=[],
        expected_doc_ids={"intensity_distribution"},
        k=5,
    ),
    GoldenQuery(
        query="What are the progression rules for increasing training load?",
        domain="training_principles",
        race_type="marathon",
        athlete_tags=[],
        expected_doc_ids={"progression_rules"},
        k=5,
    ),
    GoldenQuery(
        query="How do I taper before a race?",
        domain="training_principles",
        race_type="marathon",
        athlete_tags=[],
        expected_doc_ids={"tapering"},
        k=5,
    ),
]
