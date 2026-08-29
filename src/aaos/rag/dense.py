"""Deterministic in-memory implementation of the dense-index boundary for tests."""

from __future__ import annotations

from math import isfinite

from aaos.rag.contracts import DenseSearchHit, DenseVectorRecord, RagGenerationIdentity


class InMemoryDenseIndex:
    """An isolated, deterministic dense index used only by unit tests."""

    def __init__(
        self,
        generation: RagGenerationIdentity,
        records: dict[str, DenseVectorRecord],
    ) -> None:
        self._dimensions = generation.embedding_dimensions
        self._records = records
        self._closed = False

    def upsert(self, records: tuple[DenseVectorRecord, ...]) -> None:
        self._require_open()
        if not isinstance(records, tuple):
            raise ValueError("records must be a tuple")

        chunk_keys: set[str] = set()
        for record in records:
            if not isinstance(record, DenseVectorRecord):
                raise ValueError("records must contain DenseVectorRecord values")
            if record.chunk_key in chunk_keys:
                raise ValueError("duplicate chunk_key in upsert records")
            chunk_keys.add(record.chunk_key)
            self._validate_vector(record.vector)

        for record in records:
            self._records[record.chunk_key] = record

    def search(
        self,
        vector: tuple[float, ...],
        *,
        limit: int,
        allowed_document_version_ids: tuple[int, ...],
    ) -> tuple[DenseSearchHit, ...]:
        self._require_open()
        self._validate_vector(vector)
        if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
            raise ValueError("limit must be a positive integer")
        self._validate_allowed_document_version_ids(allowed_document_version_ids)

        candidates = sorted(
            (
                (self._squared_l2_distance(vector, record.vector), record.chunk_key)
                for record in self._records.values()
                if record.document_version_id in allowed_document_version_ids
            ),
            key=lambda candidate: (candidate[0], candidate[1]),
        )
        return tuple(
            DenseSearchHit(chunk_key, rank, distance)
            for rank, (distance, chunk_key) in enumerate(candidates[:limit], start=1)
        )

    def contains(self, chunk_keys: tuple[str, ...]) -> bool:
        self._require_open()
        if not isinstance(chunk_keys, tuple):
            raise ValueError("chunk_keys must be a tuple")
        if any(not isinstance(chunk_key, str) or not chunk_key.strip() for chunk_key in chunk_keys):
            raise ValueError("chunk_keys must contain non-blank strings")
        return all(chunk_key in self._records for chunk_key in chunk_keys)

    def count(self) -> int:
        self._require_open()
        return len(self._records)

    def close(self) -> None:
        self._closed = True

    def _require_open(self) -> None:
        if self._closed:
            raise RuntimeError("dense index is closed")

    def _validate_vector(self, vector: object) -> None:
        if not isinstance(vector, tuple) or len(vector) != self._dimensions:
            raise ValueError(f"vector must have exactly {self._dimensions} dimensions")
        if any(isinstance(value, bool) or not isinstance(value, (int, float)) for value in vector):
            raise ValueError("vector must contain finite numeric values")
        if any(not isfinite(value) for value in vector):
            raise ValueError("vector must contain finite numeric values")

    @staticmethod
    def _validate_allowed_document_version_ids(document_version_ids: object) -> None:
        if not isinstance(document_version_ids, tuple):
            raise ValueError("allowed_document_version_ids must be a tuple")
        if any(
            isinstance(document_version_id, bool)
            or not isinstance(document_version_id, int)
            or document_version_id <= 0
            for document_version_id in document_version_ids
        ):
            raise ValueError("allowed_document_version_ids must contain positive integers")

    @staticmethod
    def _squared_l2_distance(vector_a: tuple[float, ...], vector_b: tuple[float, ...]) -> float:
        distance = sum((value_a - value_b) ** 2 for value_a, value_b in zip(vector_a, vector_b))
        if not isfinite(distance):
            raise ValueError("vector distance must be finite")
        return distance


class InMemoryDenseIndexFactory:
    """Factory for generation-isolated in-memory indexes used by unit tests."""

    def __init__(self) -> None:
        self._records_by_generation: dict[RagGenerationIdentity, dict[str, DenseVectorRecord]] = {}
        self._handles_by_generation: dict[RagGenerationIdentity, set[InMemoryDenseIndex]] = {}

    def open(self, generation: RagGenerationIdentity) -> InMemoryDenseIndex:
        if not isinstance(generation, RagGenerationIdentity):
            raise ValueError("generation must be a RagGenerationIdentity")
        records = self._records_by_generation.setdefault(generation, {})
        index = InMemoryDenseIndex(generation, records)
        self._handles_by_generation.setdefault(generation, set()).add(index)
        return index

    def remove(self, generation: RagGenerationIdentity) -> None:
        if not isinstance(generation, RagGenerationIdentity):
            raise ValueError("generation must be a RagGenerationIdentity")
        self._records_by_generation.pop(generation, None)
        for index in self._handles_by_generation.pop(generation, set()):
            index.close()
