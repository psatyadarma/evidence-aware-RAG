from pathlib import Path

import numpy as np
import pytest

from app.models import (
    DocumentMetadata,
    ObservationStatus,
    RetrievalResult,
    RetrievalUnit,
    TemporalCoverage,
)
from app.threshold_gate import SimilarityThresholdGate, StructuredEvidenceSimilarityScorer
from evaluation.threshold_selection import (
    ThresholdObservation,
    binary_gate_label,
    select_threshold,
)
from app.models import AnswerabilityClassification


class FakeEncoder:
    def __init__(self, vector):
        self.vector = np.asarray([vector], dtype=np.float32)
        self.calls = []

    def encode(self, sentences, **kwargs):
        self.calls.append((list(sentences), kwargs))
        return self.vector


def _unit(evidence_id, value):
    return RetrievalUnit(
        evidence_id=evidence_id,
        document_id="doc",
        source_id="source",
        record_id=value,
        year=2020,
        text=evidence_id,
        value=value,
        raw_value=str(value),
        status=ObservationStatus.OBSERVED,
        dimensions={"series": "Resident Population"},
        metadata=DocumentMetadata(
            source_url="https://example.gov.sg/data",
            dataset_name="Dataset",
            publisher="Publisher",
            licence="Terms",
            retrieved_at="2026-01-01",
            temporal_coverage=TemporalCoverage(start="2020", end="2020"),
        ),
    )


def _result(unit, rank):
    return RetrievalResult(
        evidence=unit, score=1.0, rank=rank, retriever="structured_schema_v1"
    )


def test_scores_only_structured_evidence_and_returns_maximum():
    units = [_unit("b", 2), _unit("a", 1), _unit("c", 3)]
    embeddings = np.asarray([[1.0, 0.0], [0.0, 1.0], [-1.0, 0.0]], dtype=np.float32)
    encoder = FakeEncoder([1.0, 0.0])
    scorer = StructuredEvidenceSimilarityScorer(units, embeddings, encoder)

    score = scorer.score("question", [_result(units[0], 1), _result(units[2], 2)])

    # Matrix rows map to evidence-ID order a,b,c: b=0 and c=-1.
    assert score.maximum_similarity == pytest.approx(0.0)
    assert [item.score for item in score.evidence_scores] == pytest.approx([0.0, -1.0])
    assert encoder.calls[0][0][0].endswith(" question")


def test_zero_structured_evidence_returns_none_without_encoding():
    unit = _unit("a", 1)
    encoder = FakeEncoder([1.0, 0.0])
    scorer = StructuredEvidenceSimilarityScorer(
        [unit], np.asarray([[1.0, 0.0]], dtype=np.float32), encoder
    )

    score = scorer.score("question", [])

    assert score.maximum_similarity is None
    assert score.evidence_scores == []
    assert encoder.calls == []


def test_threshold_boundary_equality_allows_generation():
    unit = _unit("a", 1)
    scorer = StructuredEvidenceSimilarityScorer(
        [unit], np.asarray([[1.0, 0.0]], dtype=np.float32), FakeEncoder([0.6, 0.8])
    )
    score = scorer.score("question", [_result(unit, 1)])

    assert SimilarityThresholdGate(score.maximum_similarity).allows(score)
    assert not SimilarityThresholdGate(
        np.nextafter(score.maximum_similarity, np.inf)
    ).allows(score)


@pytest.mark.parametrize(
    ("label", "expected"),
    [
        (AnswerabilityClassification.ANSWERABLE, "ALLOW"),
        (AnswerabilityClassification.PARTIALLY_ANSWERABLE, "ALLOW"),
        (AnswerabilityClassification.INSUFFICIENT_EVIDENCE, "ABSTAIN"),
        (AnswerabilityClassification.OUT_OF_SCOPE, "ABSTAIN"),
    ],
)
def test_binary_label_mapping(label, expected):
    assert binary_gate_label(label) == expected


def test_threshold_selection_maximizes_macro_f1_deterministically():
    observations = [
        ThresholdObservation(id="a", label="ALLOW", score=0.9),
        ThresholdObservation(id="b", label="ALLOW", score=0.8),
        ThresholdObservation(id="c", label="ABSTAIN", score=0.3),
        ThresholdObservation(id="d", label="ABSTAIN", score=None),
    ]

    first = select_threshold(observations)
    second = select_threshold(observations)

    assert first == second
    assert first.selected_binary_macro_f1 == 1.0
    assert first.selected_threshold == 0.8


def test_threshold_tie_break_prefers_recall_then_higher_threshold():
    observations = [
        ThresholdObservation(id="a", label="ALLOW", score=0.9),
        ThresholdObservation(id="b", label="ABSTAIN", score=0.8),
        ThresholdObservation(id="c", label="ALLOW", score=0.7),
        ThresholdObservation(id="d", label="ABSTAIN", score=0.6),
    ]

    result = select_threshold(observations)
    best_macro = max(item.binary_macro_f1 for item in result.candidates)
    tied = [item for item in result.candidates if item.binary_macro_f1 == best_macro]
    best_recall = max(item.abstention_recall for item in tied)
    finalists = [item for item in tied if item.abstention_recall == best_recall]

    assert result.selected_threshold == max(item.threshold for item in finalists)


def test_threshold_script_has_no_reserved_benchmark_reference():
    root = Path(__file__).resolve().parents[1]
    source = (root / "scripts" / "select_threshold.py").read_text(encoding="utf-8")
    assert "DEVELOPMENT_BENCHMARK" in source
    assert "held" + "out" not in source.casefold()
