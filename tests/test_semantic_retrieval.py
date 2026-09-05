from datetime import date
from pathlib import Path
from typing import Sequence

import numpy as np
import pytest

from app.models import (
    AnswerabilityClassification,
    BenchmarkCategory,
    BenchmarkExample,
    DocumentMetadata,
    ExpectedAnswer,
    ObservationStatus,
    RequiredClaim,
    RetrievalResult,
    RetrievalUnit,
    TemporalCoverage,
)
from app.semantic_retrieval import (
    QUERY_PREFIX,
    EmbeddingArtifactError,
    SemanticRetriever,
    build_embedding_artifact,
    load_embedding_artifact,
)
from evaluation.retrieval_evaluation import evaluate_retrieval


MODEL_ID = "test/model"
MODEL_REVISION = "abc123"


class FakeEncoder:
    def __init__(self, vectors: dict[str, Sequence[float]]) -> None:
        self.vectors = vectors
        self.calls: list[list[str]] = []

    def encode(
        self,
        sentences: Sequence[str],
        *,
        batch_size: int,
        show_progress_bar: bool,
        convert_to_numpy: bool,
        normalize_embeddings: bool,
    ) -> np.ndarray:
        self.calls.append(list(sentences))
        return np.asarray([self.vectors[text] for text in sentences], dtype=np.float32)


@pytest.fixture
def metadata() -> DocumentMetadata:
    return DocumentMetadata(
        source_url="https://data.gov.sg/datasets/example/view",
        dataset_name="Example dataset",
        publisher="SingStat",
        licence="Singapore Open Data Licence 1.0",
        retrieved_at=date(2026, 9, 3),
        temporal_coverage=TemporalCoverage(start="2025", end="2025"),
    )


def unit(evidence_id: str, text: str, metadata: DocumentMetadata) -> RetrievalUnit:
    return RetrievalUnit(
        evidence_id=evidence_id,
        document_id=f"example:{evidence_id}",
        source_id="example",
        record_id=1,
        year=2025,
        text=text,
        value=1,
        raw_value="1",
        status=ObservationStatus.OBSERVED,
        dimensions={"series": text},
        metadata=metadata,
    )


def build_fake_artifact(
    artifact_dir: Path,
    units: list[RetrievalUnit],
    vectors: dict[str, Sequence[float]],
    *,
    expected_dimension: int = 2,
) -> None:
    build_embedding_artifact(
        units,
        FakeEncoder(vectors),
        artifact_dir,
        model_id=MODEL_ID,
        model_revision=MODEL_REVISION,
        encoder_library="fake",
        encoder_library_version="1",
        batch_size=2,
        device="cpu",
        expected_dimension=expected_dimension,
    )


def test_embedding_artifact_round_trip_and_id_order(
    tmp_path: Path, metadata: DocumentMetadata
) -> None:
    units = [unit("b", "beta", metadata), unit("a", "alpha", metadata)]
    build_fake_artifact(tmp_path, units, {"alpha": [3, 0], "beta": [0, 2]})

    artifact_metadata, matrix = load_embedding_artifact(
        units,
        tmp_path,
        expected_model_id=MODEL_ID,
        expected_model_revision=MODEL_REVISION,
        expected_dimension=2,
    )

    assert artifact_metadata.unit_count == 2
    assert artifact_metadata.embedding_dimension == 2
    assert matrix.dtype == np.float32
    assert np.allclose(matrix, [[1, 0], [0, 1]])
    assert (tmp_path / "evidence_ids.json").read_text(encoding="utf-8") == '["a","b"]\n'


def test_stale_corpus_is_rejected(tmp_path: Path, metadata: DocumentMetadata) -> None:
    original = [unit("a", "alpha", metadata)]
    build_fake_artifact(tmp_path, original, {"alpha": [1, 0]})
    changed = [original[0].model_copy(update={"text": "changed alpha"})]

    with pytest.raises(EmbeddingArtifactError, match="corpus_sha256 mismatch"):
        load_embedding_artifact(
            changed,
            tmp_path,
            expected_model_id=MODEL_ID,
            expected_model_revision=MODEL_REVISION,
        )


def test_model_revision_mismatch_is_rejected(
    tmp_path: Path, metadata: DocumentMetadata
) -> None:
    units = [unit("a", "alpha", metadata)]
    build_fake_artifact(tmp_path, units, {"alpha": [1, 0]})

    with pytest.raises(EmbeddingArtifactError, match="model_revision mismatch"):
        load_embedding_artifact(
            units,
            tmp_path,
            expected_model_id=MODEL_ID,
            expected_model_revision="different",
        )


def test_build_rejects_unexpected_dimension(
    tmp_path: Path, metadata: DocumentMetadata
) -> None:
    units = [unit("a", "alpha", metadata)]

    with pytest.raises(EmbeddingArtifactError, match="returned dimension 2"):
        build_fake_artifact(
            tmp_path,
            units,
            {"alpha": [1, 0]},
            expected_dimension=3,
        )


def semantic_retriever(
    metadata: DocumentMetadata,
) -> tuple[SemanticRetriever, FakeEncoder]:
    units = [unit("c", "gamma", metadata), unit("a", "alpha", metadata), unit("b", "beta", metadata)]
    # SemanticRetriever sorts units by evidence ID, so rows correspond to a, b, c.
    matrix = np.asarray([[1, 0], [0, 1], [-1, 0]], dtype=np.float32)
    encoder = FakeEncoder(
        {
            QUERY_PREFIX + " query": [4, 1],
            QUERY_PREFIX + " tie": [1, 1],
        }
    )
    return SemanticRetriever(units, matrix, encoder), encoder


def test_normalized_dot_product_ranking_and_output_schema(
    metadata: DocumentMetadata,
) -> None:
    retriever, encoder = semantic_retriever(metadata)

    results = retriever.retrieve("query", 3)

    assert all(isinstance(result, RetrievalResult) for result in results)
    assert [result.evidence.evidence_id for result in results] == ["a", "b", "c"]
    assert [result.rank for result in results] == [1, 2, 3]
    assert results[0].evidence.metadata.publisher == "SingStat"
    assert encoder.calls == [[QUERY_PREFIX + " query"]]


def test_equal_scores_tie_break_by_evidence_id(metadata: DocumentMetadata) -> None:
    retriever, _ = semantic_retriever(metadata)

    results = retriever.retrieve("tie", 2)

    assert results[0].score == pytest.approx(results[1].score)
    assert [result.evidence.evidence_id for result in results] == ["a", "b"]


def test_empty_query_does_not_call_encoder(metadata: DocumentMetadata) -> None:
    retriever, encoder = semantic_retriever(metadata)

    assert retriever.retrieve("  ", 1) == []
    assert encoder.calls == []


@pytest.mark.parametrize("k", [0, -1])
def test_invalid_k_is_rejected(metadata: DocumentMetadata, k: int) -> None:
    retriever, _ = semantic_retriever(metadata)

    with pytest.raises(ValueError, match="greater than zero"):
        retriever.retrieve("query", k)


def test_large_k_and_repeated_ranking_are_deterministic(
    metadata: DocumentMetadata,
) -> None:
    retriever, _ = semantic_retriever(metadata)

    first = retriever.retrieve("query", 99)
    second = retriever.retrieve("query", 99)

    assert len(first) == 3
    assert first == second


def test_query_dimension_mismatch_is_rejected(metadata: DocumentMetadata) -> None:
    units = [unit("a", "alpha", metadata)]
    retriever = SemanticRetriever(
        units,
        np.asarray([[1, 0]], dtype=np.float32),
        FakeEncoder({QUERY_PREFIX + " query": [1, 0, 0]}),
    )

    with pytest.raises(EmbeddingArtifactError, match="query embedding shape"):
        retriever.retrieve("query", 1)


def test_evaluation_with_known_fake_vectors(metadata: DocumentMetadata) -> None:
    units = [unit("a", "alpha", metadata), unit("b", "beta", metadata)]
    retriever = SemanticRetriever(
        units,
        np.asarray([[1, 0], [0, 1]], dtype=np.float32),
        FakeEncoder({QUERY_PREFIX + " both": [1, 1]}),
    )
    example = BenchmarkExample(
        id="multi",
        question="both",
        category=BenchmarkCategory.AGGREGATION,
        answerability=AnswerabilityClassification.ANSWERABLE,
        expected_answer=ExpectedAnswer(canonical="Two", numeric_value=2),
        required_evidence=["a", "b"],
        claims=[
            RequiredClaim(
                claim="Both observations", supported=True, evidence_ids=["a", "b"]
            )
        ],
    )

    report, summary = evaluate_retrieval(
        [example], retriever, ks=(1, 2), retrieval_method="fake_semantic"
    )

    assert report[0]["recall_at_k"] == {"1": 0.5, "2": 1.0}
    assert summary["overall_mean_recall_at_k"] == {"1": 0.5, "2": 1.0}
    assert summary["retrieval_method"] == "fake_semantic"
