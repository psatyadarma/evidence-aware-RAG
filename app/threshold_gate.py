"""Cosine-similarity gate over frozen BGE vectors for structured evidence."""

from __future__ import annotations

import math
from time import perf_counter
from typing import Optional, Sequence

import numpy as np
from pydantic import Field, model_validator

from app.models import RetrievalResult, RetrievalUnit, StrictModel
from app.semantic_retrieval import EmbeddingArtifactError, EmbeddingEncoder, QUERY_PREFIX


class EvidenceSimilarity(StrictModel):
    evidence_id: str = Field(min_length=1)
    score: float


class ThresholdScore(StrictModel):
    maximum_similarity: Optional[float]
    evidence_scores: list[EvidenceSimilarity]
    scoring_latency_ms: float = Field(ge=0.0)

    @model_validator(mode="after")
    def maximum_matches_scores(self) -> "ThresholdScore":
        if not self.evidence_scores:
            if self.maximum_similarity is not None:
                raise ValueError("zero evidence requires a NONE threshold score")
        elif self.maximum_similarity != max(item.score for item in self.evidence_scores):
            raise ValueError("maximum similarity must equal the largest evidence score")
        return self


class StructuredEvidenceSimilarityScorer:
    """Score exactly the units returned by the frozen structured retriever."""

    def __init__(
        self,
        units: Sequence[RetrievalUnit],
        embeddings: np.ndarray,
        query_encoder: EmbeddingEncoder,
        *,
        query_prefix: str = QUERY_PREFIX,
    ) -> None:
        ordered = tuple(sorted(units, key=lambda unit: unit.evidence_id))
        ids = [unit.evidence_id for unit in ordered]
        if not ordered or len(ids) != len(set(ids)):
            raise EmbeddingArtifactError("scorer requires unique non-empty retrieval units")
        matrix = np.asarray(embeddings)
        if matrix.dtype != np.float32 or matrix.ndim != 2 or matrix.shape[0] != len(ordered):
            raise EmbeddingArtifactError(
                "embedding matrix must be float32 with one row per ordered retrieval unit"
            )
        if not np.isfinite(matrix).all():
            raise EmbeddingArtifactError("embedding matrix contains non-finite values")
        if not np.allclose(np.linalg.norm(matrix, axis=1), 1.0, rtol=1e-5, atol=1e-6):
            raise EmbeddingArtifactError("embedding matrix rows must be L2-normalized")
        self._embeddings = matrix
        self._index = {evidence_id: index for index, evidence_id in enumerate(ids)}
        self._encoder = query_encoder
        self._query_prefix = query_prefix

    def score(
        self, question: str, structured_results: Sequence[RetrievalResult]
    ) -> ThresholdScore:
        started = perf_counter()
        if not structured_results:
            return ThresholdScore(
                maximum_similarity=None,
                evidence_scores=[],
                scoring_latency_ms=(perf_counter() - started) * 1_000,
            )
        unknown = [
            item.evidence.evidence_id
            for item in structured_results
            if item.evidence.evidence_id not in self._index
        ]
        if unknown:
            raise EmbeddingArtifactError(
                "structured evidence is absent from frozen embeddings: " + ", ".join(unknown)
            )
        encoded = self._encoder.encode(
            [self._query_prefix + " " + question],
            batch_size=1,
            show_progress_bar=False,
            convert_to_numpy=True,
            normalize_embeddings=True,
        )
        query = np.asarray(encoded, dtype=np.float32)
        if query.shape != (1, self._embeddings.shape[1]) or not np.isfinite(query).all():
            raise EmbeddingArtifactError("query embedding has an invalid shape or value")
        norm = float(np.linalg.norm(query[0]))
        if norm == 0.0:
            raise EmbeddingArtifactError("query embedding is a zero vector")
        vector = query[0] / norm
        evidence_scores = [
            EvidenceSimilarity(
                evidence_id=item.evidence.evidence_id,
                score=float(self._embeddings[self._index[item.evidence.evidence_id]] @ vector),
            )
            for item in structured_results
        ]
        return ThresholdScore(
            maximum_similarity=max(item.score for item in evidence_scores),
            evidence_scores=evidence_scores,
            scoring_latency_ms=(perf_counter() - started) * 1_000,
        )


class SimilarityThresholdGate:
    def __init__(self, threshold: float) -> None:
        if not math.isfinite(threshold):
            raise ValueError("threshold must be finite")
        self.threshold = threshold

    def allows(self, score: ThresholdScore) -> bool:
        return (
            score.maximum_similarity is not None
            and score.maximum_similarity >= self.threshold
        )
