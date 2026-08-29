"""Embedded, generation-isolated Chroma adapter for the Phase 4 dense boundary."""

from __future__ import annotations

from math import isfinite
from pathlib import Path
from typing import Any

import chromadb
from chromadb.config import Settings

from aaos.rag.config import RagRuntimePaths
from aaos.rag.contracts import (
    DenseSearchHit,
    DenseVectorRecord,
    RagErrorCode,
    RagGenerationIdentity,
    RagOperationalError,
)

_COLLECTION_NAME = "aaos_dense"
_COLLECTION_CONFIGURATION: Any = {"hnsw": {"space": "l2"}}


class _ChromaDenseIndex:
    """Private lifecycle wrapper around one generation's local Chroma collection."""

    def __init__(self, generation: RagGenerationIdentity, collection: Any) -> None:
        self._dimensions = generation.embedding_dimensions
        self._collection = collection
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
        if not records:
            return

        try:
            self._collection.upsert(
                ids=[record.chunk_key for record in records],
                embeddings=[list(record.vector) for record in records],
                metadatas=[
                    {"document_version_id": record.document_version_id} for record in records
                ],
            )
        except Exception as error:
            raise RagOperationalError(RagErrorCode.DENSE_INDEX_WRITE_FAILED) from error

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
        if not allowed_document_version_ids:
            return ()

        try:
            result = self._collection.query(
                query_embeddings=[list(vector)],
                n_results=limit,
                where={"document_version_id": {"$in": list(allowed_document_version_ids)}},
                include=["distances", "metadatas"],
            )
            ids = result["ids"]
            distances = result["distances"]
            metadatas = result["metadatas"]
            if (
                not isinstance(ids, list)
                or not isinstance(distances, list)
                or not isinstance(metadatas, list)
                or len(ids) != 1
                or len(distances) != 1
                or len(metadatas) != 1
                or not isinstance(ids[0], list)
                or not isinstance(distances[0], list)
                or not isinstance(metadatas[0], list)
                or len(ids[0]) != len(distances[0])
                or len(ids[0]) != len(metadatas[0])
                or len(ids[0]) > limit
            ):
                raise ValueError("invalid Chroma query result")
            candidates: list[tuple[float, str]] = []
            for chunk_key, distance, metadata in zip(ids[0], distances[0], metadatas[0]):
                if (
                    not isinstance(chunk_key, str)
                    or not chunk_key.strip()
                    or isinstance(distance, bool)
                    or not isinstance(distance, (int, float))
                    or not isfinite(distance)
                    or not isinstance(metadata, dict)
                ):
                    raise ValueError("invalid Chroma query result")
                document_version_id = metadata.get("document_version_id")
                if (
                    isinstance(document_version_id, bool)
                    or not isinstance(document_version_id, int)
                    or document_version_id <= 0
                    or document_version_id not in allowed_document_version_ids
                ):
                    raise ValueError("invalid Chroma query result")
                candidates.append((float(distance), chunk_key))
            if len({chunk_key for _, chunk_key in candidates}) != len(candidates):
                raise ValueError("invalid Chroma query result")
            return tuple(
                DenseSearchHit(chunk_key, rank, float(distance))
                for rank, (distance, chunk_key) in enumerate(sorted(candidates), start=1)
            )
        except RagOperationalError:
            raise
        except Exception as error:
            raise RagOperationalError(RagErrorCode.DENSE_INDEX_UNAVAILABLE) from error

    def contains(self, chunk_keys: tuple[str, ...]) -> bool:
        self._require_open()
        if not isinstance(chunk_keys, tuple):
            raise ValueError("chunk_keys must be a tuple")
        if any(not isinstance(chunk_key, str) or not chunk_key.strip() for chunk_key in chunk_keys):
            raise ValueError("chunk_keys must contain non-blank strings")
        if not chunk_keys:
            return True
        try:
            result = self._collection.get(ids=list(chunk_keys), include=[])
            return set(result["ids"]) == set(chunk_keys)
        except Exception as error:
            raise RagOperationalError(RagErrorCode.DENSE_INDEX_UNAVAILABLE) from error

    def count(self) -> int:
        self._require_open()
        try:
            return int(self._collection.count())
        except Exception as error:
            raise RagOperationalError(RagErrorCode.DENSE_INDEX_UNAVAILABLE) from error

    def close(self) -> None:
        self._closed = True

    def _require_open(self) -> None:
        if self._closed:
            raise RagOperationalError(RagErrorCode.DENSE_INDEX_UNAVAILABLE)

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


class ChromaDenseIndexFactory:
    """Create local, deterministic Chroma indexes isolated by trusted generation identity."""

    def __init__(self, paths: RagRuntimePaths) -> None:
        if not isinstance(paths, RagRuntimePaths):
            raise ValueError("paths must be a RagRuntimePaths")
        self._chroma_root = paths.chroma_root
        self._handles_by_generation: dict[RagGenerationIdentity, set[_ChromaDenseIndex]] = {}

    def open(self, generation: RagGenerationIdentity) -> _ChromaDenseIndex:
        if not isinstance(generation, RagGenerationIdentity):
            raise ValueError("generation must be a RagGenerationIdentity")
        try:
            client = chromadb.PersistentClient(
                path=str(self._generation_path(generation)),
                settings=Settings(anonymized_telemetry=False),
            )
            collection = client.get_or_create_collection(
                _COLLECTION_NAME,
                configuration=_COLLECTION_CONFIGURATION,
                embedding_function=None,
            )
        except Exception as error:
            raise RagOperationalError(RagErrorCode.DENSE_INDEX_UNAVAILABLE) from error

        index = _ChromaDenseIndex(generation, collection)
        self._handles_by_generation.setdefault(generation, set()).add(index)
        return index

    def remove(self, generation: RagGenerationIdentity) -> None:
        if not isinstance(generation, RagGenerationIdentity):
            raise ValueError("generation must be a RagGenerationIdentity")
        for index in self._handles_by_generation.pop(generation, set()):
            index.close()

        generation_path = self._generation_path(generation)
        if not generation_path.exists():
            return
        try:
            client = chromadb.PersistentClient(
                path=str(generation_path),
                settings=Settings(anonymized_telemetry=False),
            )
            client.delete_collection(_COLLECTION_NAME)
        except Exception as error:
            raise RagOperationalError(RagErrorCode.DENSE_INDEX_UNAVAILABLE) from error

    def _generation_path(self, generation: RagGenerationIdentity) -> Path:
        return self._chroma_root / (
            f"generation-{generation.generation_id}-"
            f"{generation.config_fingerprint}-r{generation.content_revision}-"
            f"d{generation.embedding_dimensions}"
        )
