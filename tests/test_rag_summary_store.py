"""Phase I3: the verified document-summary cache (Alembic 0013)."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from sqlalchemy import inspect
from test_rag_repository import _create_generation, _seed_phase3, repository_database

from aaos.rag.contracts import RagOperationalError
from aaos.rag.summary_contracts import (
    MAX_SUMMARY_POINTS,
    StoredDocumentSummary,
    SummaryCacheKey,
    SummaryPoint,
)


def _summary(document_version_id: int, generation_id: int) -> StoredDocumentSummary:
    return StoredDocumentSummary(
        key=SummaryCacheKey(
            document_version_id=document_version_id,
            generation_id=generation_id,
            content_revision=0,
            policy_version="summary-v1",
            model_identifier="qwen/qwen3.5-4b",
            language="en",
        ),
        points=(
            SummaryPoint("The course covers big data analytics.", ("chunk-a",)),
            SummaryPoint("Students use KNIME and RapidMiner.", ("chunk-b", "chunk-c")),
        ),
        sections_total=2,
        dropped_points=1,
    )


def test_migration_creates_the_summary_cache_table(tmp_path: Path) -> None:
    with repository_database(tmp_path) as (_repository, _sessions, engine):
        columns = {
            column["name"] for column in inspect(engine).get_columns("rag_document_summaries")
        }

    assert {
        "document_version_id",
        "generation_id",
        "content_revision",
        "policy_version",
        "model_identifier",
        "language",
        "sections_total",
        "dropped_points",
        "points_json",
    } <= columns


def test_summary_round_trips_only_for_the_exact_identity(tmp_path: Path) -> None:
    with repository_database(tmp_path) as (repository, sessions, _engine):
        ids = _seed_phase3(sessions)
        generation = _create_generation(repository)
        summary = _summary(ids["one_v1"], generation.generation_id)

        repository.put_document_summary(summary)

        assert repository.get_document_summary(summary.key) == summary
        for change in (
            {"content_revision": 1},
            {"policy_version": "summary-v2"},
            {"model_identifier": "other-model"},
            {"language": "ar"},
            {"document_version_id": ids["one_v2"]},
        ):
            assert repository.get_document_summary(replace(summary.key, **change)) is None


def test_rewriting_an_identical_key_keeps_the_first_summary(tmp_path: Path) -> None:
    with repository_database(tmp_path) as (repository, sessions, _engine):
        ids = _seed_phase3(sessions)
        generation = _create_generation(repository)
        first = _summary(ids["one_v1"], generation.generation_id)
        second = replace(first, points=(SummaryPoint("Different text.", ("chunk-z",)),))

        repository.put_document_summary(first)
        repository.put_document_summary(second)

        assert repository.get_document_summary(first.key) == first


def test_summary_rows_must_reference_real_document_versions(tmp_path: Path) -> None:
    with repository_database(tmp_path) as (repository, _sessions, _engine):
        generation = _create_generation(repository)

        with pytest.raises(RagOperationalError):
            repository.put_document_summary(_summary(999_999, generation.generation_id))


@pytest.mark.parametrize(
    "build",
    (
        lambda: SummaryPoint("", ("chunk-a",)),
        lambda: SummaryPoint("text", ()),
        lambda: SummaryPoint("text", ("chunk-a", "chunk-a")),
        lambda: SummaryCacheKey(1, 1, 0, "summary-v1", "model", "fr"),
        lambda: replace(_summary(1, 1), points=()),
        lambda: replace(
            _summary(1, 1),
            points=tuple(
                SummaryPoint(f"point {index}", (f"chunk-{index}",))
                for index in range(MAX_SUMMARY_POINTS + 1)
            ),
        ),
        lambda: replace(_summary(1, 1), sections_total=0),
        lambda: replace(_summary(1, 1), dropped_points=-1),
    ),
)
def test_malformed_summaries_are_rejected_before_storage(build: object) -> None:
    with pytest.raises(ValueError):
        build()  # type: ignore[operator]
