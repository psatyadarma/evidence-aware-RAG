"""Persisted dense embeddings and exact semantic retrieval."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Literal, Protocol, Sequence

import numpy as np
from pydantic import Field, model_validator

from app.models import RetrievalResult, RetrievalUnit, StrictModel


MODEL_ID = "BAAI/bge-small-en-v1.5"
MODEL_REVISION = "5c38ec7c405ec4b44b94cc5a9bb96e735b38267a"
MODEL_DIMENSION = 384
QUERY_PREFIX = "Represent this sentence for searching relevant passages:"
SEMANTIC_RETRIEVER_NAME = "semantic_bge_small_en_v1_5"
ARTIFACT_SCHEMA_VERSION = 1
MATRIX_FILENAME = "embeddings.npy"
EVIDENCE_IDS_FILENAME = "evidence_ids.json"
METADATA_FILENAME = "metadata.json"
SHA256_PATTERN = r"^[0-9a-f]{64}$"


class EmbeddingArtifactError(ValueError):
    """An embedding artifact is missing, corrupt, stale, or incompatible."""


class EmbeddingEncoder(Protocol):
    def encode(
        self,
        sentences: Sequence[str],
        *,
        batch_size: int,
        show_progress_bar: bool,
        convert_to_numpy: bool,
        normalize_embeddings: bool,
    ) -> np.ndarray:
        """Encode text into one vector per input string."""


class EmbeddingArtifactMetadata(StrictModel):
    schema_version: Literal[1] = ARTIFACT_SCHEMA_VERSION
    model_id: str = Field(min_length=1)
    model_revision: str = Field(min_length=1)
    encoder_library: str = Field(min_length=1)
    encoder_library_version: str = Field(min_length=1)
    query_prefix: str = Field(min_length=1)
    document_prefix: str = ""
    normalized: Literal[True] = True
    dtype: Literal["float32"] = "float32"
    embedding_dimension: int = Field(gt=0)
    unit_count: int = Field(gt=0)
    corpus_sha256: str = Field(pattern=SHA256_PATTERN)
    retrieval_text_sha256: str = Field(pattern=SHA256_PATTERN)
    matrix_sha256: str = Field(pattern=SHA256_PATTERN)
    evidence_ids_sha256: str = Field(pattern=SHA256_PATTERN)
    matrix_file: Literal["embeddings.npy"] = MATRIX_FILENAME
    evidence_ids_file: Literal["evidence_ids.json"] = EVIDENCE_IDS_FILENAME
    created_at_utc: datetime
    build_duration_seconds: float = Field(ge=0.0)
    batch_size: int = Field(gt=0)
    device: str = Field(min_length=1)

    @model_validator(mode="after")
    def timestamp_is_utc(self) -> "EmbeddingArtifactMetadata":
        if self.created_at_utc.tzinfo is None:
            raise ValueError("created_at_utc must include a timezone")
        if self.created_at_utc.utcoffset().total_seconds() != 0:
            raise ValueError("created_at_utc must use UTC")
        return self


def _ordered_units(units: Sequence[RetrievalUnit]) -> tuple[RetrievalUnit, ...]:
    ordered = tuple(sorted(units, key=lambda unit: unit.evidence_id))
    evidence_ids = [unit.evidence_id for unit in ordered]
    if len(evidence_ids) != len(set(evidence_ids)):
        raise EmbeddingArtifactError("retrieval units contain duplicate evidence IDs")
    if not ordered:
        raise EmbeddingArtifactError("cannot build or load embeddings for an empty corpus")
    return ordered


def _update_framed(digest: "hashlib._Hash", payload: bytes) -> None:
    digest.update(len(payload).to_bytes(8, byteorder="big"))
    digest.update(payload)


def corpus_checksum(units: Sequence[RetrievalUnit]) -> str:
    digest = hashlib.sha256()
    for unit in _ordered_units(units):
        payload = json.dumps(
            unit.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        _update_framed(digest, payload)
    return digest.hexdigest()


def retrieval_text_checksum(units: Sequence[RetrievalUnit]) -> str:
    digest = hashlib.sha256()
    for unit in _ordered_units(units):
        _update_framed(digest, unit.evidence_id.encode("utf-8"))
        _update_framed(digest, unit.text.encode("utf-8"))
    return digest.hexdigest()


def _file_checksum(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _normalized_float32(matrix: np.ndarray, *, label: str) -> np.ndarray:
    array = np.asarray(matrix, dtype=np.float32)
    if array.ndim != 2:
        raise EmbeddingArtifactError(f"{label} must be a two-dimensional matrix")
    if not np.isfinite(array).all():
        raise EmbeddingArtifactError(f"{label} contains non-finite values")
    norms = np.linalg.norm(array, axis=1, keepdims=True)
    if np.any(norms == 0):
        raise EmbeddingArtifactError(f"{label} contains a zero vector")
    return np.ascontiguousarray(array / norms, dtype=np.float32)


def build_embedding_artifact(
    units: Sequence[RetrievalUnit],
    encoder: EmbeddingEncoder,
    artifact_dir: Path,
    *,
    model_id: str,
    model_revision: str,
    encoder_library: str,
    encoder_library_version: str,
    batch_size: int,
    device: str,
    expected_dimension: int | None = None,
) -> EmbeddingArtifactMetadata:
    if batch_size <= 0:
        raise ValueError("batch_size must be greater than zero")
    ordered = _ordered_units(units)
    started = perf_counter()
    encoded = encoder.encode(
        [unit.text for unit in ordered],
        batch_size=batch_size,
        show_progress_bar=True,
        convert_to_numpy=True,
        normalize_embeddings=True,
    )
    matrix = _normalized_float32(encoded, label="corpus embeddings")
    if matrix.shape[0] != len(ordered):
        raise EmbeddingArtifactError(
            f"encoder returned {matrix.shape[0]} rows for {len(ordered)} units"
        )
    if expected_dimension is not None and matrix.shape[1] != expected_dimension:
        raise EmbeddingArtifactError(
            f"encoder returned dimension {matrix.shape[1]}; "
            f"expected {expected_dimension}"
        )

    artifact_dir.mkdir(parents=True, exist_ok=True)
    matrix_path = artifact_dir / MATRIX_FILENAME
    ids_path = artifact_dir / EVIDENCE_IDS_FILENAME
    metadata_path = artifact_dir / METADATA_FILENAME
    matrix_temp = matrix_path.with_suffix(".npy.tmp")
    ids_temp = ids_path.with_suffix(".json.tmp")
    metadata_temp = metadata_path.with_suffix(".json.tmp")
    try:
        with matrix_temp.open("wb") as output:
            np.save(output, matrix, allow_pickle=False)
        with ids_temp.open("w", encoding="utf-8", newline="\n") as output:
            json.dump(
                [unit.evidence_id for unit in ordered],
                output,
                ensure_ascii=False,
                separators=(",", ":"),
            )
            output.write("\n")
        duration = perf_counter() - started
        metadata = EmbeddingArtifactMetadata(
            model_id=model_id,
            model_revision=model_revision,
            encoder_library=encoder_library,
            encoder_library_version=encoder_library_version,
            query_prefix=QUERY_PREFIX,
            embedding_dimension=matrix.shape[1],
            unit_count=matrix.shape[0],
            corpus_sha256=corpus_checksum(ordered),
            retrieval_text_sha256=retrieval_text_checksum(ordered),
            matrix_sha256=_file_checksum(matrix_temp),
            evidence_ids_sha256=_file_checksum(ids_temp),
            created_at_utc=datetime.now(timezone.utc),
            build_duration_seconds=duration,
            batch_size=batch_size,
            device=device,
        )
        with metadata_temp.open("w", encoding="utf-8", newline="\n") as output:
            output.write(metadata.model_dump_json(indent=2))
            output.write("\n")
        matrix_temp.replace(matrix_path)
        ids_temp.replace(ids_path)
        metadata_temp.replace(metadata_path)
    except Exception:
        matrix_temp.unlink(missing_ok=True)
        ids_temp.unlink(missing_ok=True)
        metadata_temp.unlink(missing_ok=True)
        raise
    return metadata


def load_embedding_artifact(
    units: Sequence[RetrievalUnit],
    artifact_dir: Path,
    *,
    expected_model_id: str,
    expected_model_revision: str,
    expected_query_prefix: str = QUERY_PREFIX,
    expected_dimension: int | None = None,
) -> tuple[EmbeddingArtifactMetadata, np.ndarray]:
    ordered = _ordered_units(units)
    metadata_path = artifact_dir / METADATA_FILENAME
    try:
        metadata = EmbeddingArtifactMetadata.model_validate_json(
            metadata_path.read_text(encoding="utf-8")
        )
    except Exception as exc:
        raise EmbeddingArtifactError(f"cannot load embedding metadata: {exc}") from exc

    expected_values = {
        "model_id": (metadata.model_id, expected_model_id),
        "model_revision": (metadata.model_revision, expected_model_revision),
        "query_prefix": (metadata.query_prefix, expected_query_prefix),
        "unit_count": (metadata.unit_count, len(ordered)),
        "corpus_sha256": (metadata.corpus_sha256, corpus_checksum(ordered)),
        "retrieval_text_sha256": (
            metadata.retrieval_text_sha256,
            retrieval_text_checksum(ordered),
        ),
    }
    if expected_dimension is not None:
        expected_values["embedding_dimension"] = (
            metadata.embedding_dimension,
            expected_dimension,
        )
    for field, (actual, expected) in expected_values.items():
        if actual != expected:
            raise EmbeddingArtifactError(
                f"embedding artifact {field} mismatch: {actual!r} != {expected!r}"
            )

    matrix_path = artifact_dir / metadata.matrix_file
    ids_path = artifact_dir / metadata.evidence_ids_file
    try:
        if _file_checksum(matrix_path) != metadata.matrix_sha256:
            raise EmbeddingArtifactError("embedding matrix checksum mismatch")
        if _file_checksum(ids_path) != metadata.evidence_ids_sha256:
            raise EmbeddingArtifactError("evidence ID file checksum mismatch")
    except OSError as exc:
        raise EmbeddingArtifactError(f"cannot read embedding artifact: {exc}") from exc
    try:
        evidence_ids = json.loads(ids_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise EmbeddingArtifactError(f"cannot load evidence IDs: {exc}") from exc
    expected_ids = [unit.evidence_id for unit in ordered]
    if evidence_ids != expected_ids:
        raise EmbeddingArtifactError("embedding evidence IDs do not match retrieval units")

    try:
        matrix = np.load(matrix_path, allow_pickle=False, mmap_mode="r")
    except Exception as exc:
        raise EmbeddingArtifactError(f"cannot load embedding matrix: {exc}") from exc
    if matrix.dtype != np.dtype(metadata.dtype):
        raise EmbeddingArtifactError(
            f"embedding dtype {matrix.dtype} does not match {metadata.dtype}"
        )
    if matrix.shape != (metadata.unit_count, metadata.embedding_dimension):
        raise EmbeddingArtifactError(
            f"embedding shape {matrix.shape} does not match metadata "
            f"({metadata.unit_count}, {metadata.embedding_dimension})"
        )
    if not np.isfinite(matrix).all():
        raise EmbeddingArtifactError("embedding matrix contains non-finite values")
    norms = np.linalg.norm(matrix, axis=1)
    if not np.allclose(norms, 1.0, rtol=1e-5, atol=1e-6):
        raise EmbeddingArtifactError("embedding matrix rows are not L2-normalized")
    return metadata, matrix


class SemanticRetriever:
    """Exact dot-product search over L2-normalized corpus embeddings."""

    def __init__(
        self,
        units: Sequence[RetrievalUnit],
        embeddings: np.ndarray,
        query_encoder: EmbeddingEncoder,
        *,
        query_prefix: str = QUERY_PREFIX,
        retriever_name: str = SEMANTIC_RETRIEVER_NAME,
    ) -> None:
        self._units = _ordered_units(units)
        matrix = np.asarray(embeddings)
        if matrix.ndim != 2 or matrix.shape[0] != len(self._units):
            raise EmbeddingArtifactError(
                "embedding matrix shape must be (retrieval unit count, dimension)"
            )
        if matrix.dtype != np.float32:
            raise EmbeddingArtifactError("embedding matrix must use float32")
        if not np.isfinite(matrix).all():
            raise EmbeddingArtifactError("embedding matrix contains non-finite values")
        if not np.allclose(np.linalg.norm(matrix, axis=1), 1.0, rtol=1e-5, atol=1e-6):
            raise EmbeddingArtifactError("embedding matrix rows are not L2-normalized")
        self._embeddings = matrix
        self._query_encoder = query_encoder
        self._query_prefix = query_prefix
        self._retriever_name = retriever_name

    def retrieve(self, question: str, k: int) -> list[RetrievalResult]:
        if k <= 0:
            raise ValueError("k must be greater than zero")
        if not question.strip():
            return []
        query = self._query_encoder.encode(
            [self._query_prefix + " " + question],
            batch_size=1,
            show_progress_bar=False,
            convert_to_numpy=True,
            normalize_embeddings=True,
        )
        query_matrix = _normalized_float32(query, label="query embedding")
        if query_matrix.shape != (1, self._embeddings.shape[1]):
            raise EmbeddingArtifactError(
                f"query embedding shape {query_matrix.shape} does not match "
                f"(1, {self._embeddings.shape[1]})"
            )
        scores = self._embeddings @ query_matrix[0]
        # Units are ordered by evidence ID, so the index is the deterministic tie key.
        ordered_indices = np.lexsort((np.arange(len(self._units)), -scores))
        return [
            RetrievalResult(
                evidence=self._units[index],
                score=float(scores[index]),
                rank=rank,
                retriever=self._retriever_name,
            )
            for rank, index in enumerate(
                ordered_indices[: min(k, len(self._units))], start=1
            )
        ]

    @property
    def corpus_size(self) -> int:
        return len(self._units)

    @property
    def embedding_dimension(self) -> int:
        return self._embeddings.shape[1]
