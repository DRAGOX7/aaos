from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

from aaos.persistence.database import create_database_engine

PROJECT_ROOT = Path(__file__).resolve().parents[1]
TARGET_REVISION = "0008_document_structural_provenance"


def _config(database_path: Path) -> Config:
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.set_main_option("aaos.database_path", str(database_path))
    return config


def _revision(database_path: Path) -> str | None:
    engine = create_database_engine(database_path)
    try:
        with engine.connect() as connection:
            return connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one_or_none()
    finally:
        engine.dispose()


def test_structural_provenance_migration_extends_current_head(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "structural.db"

    command.upgrade(_config(database_path), TARGET_REVISION)

    assert _revision(database_path) == TARGET_REVISION

    engine = create_database_engine(database_path)
    try:
        inspector = inspect(engine)

        assert "document_structure_units" in inspector.get_table_names()

        version_columns = {
            column["name"]: column for column in inspector.get_columns("document_versions")
        }

        assert "provenance_mode" in version_columns
        assert version_columns["provenance_mode"]["nullable"] is False
        assert version_columns["page_count"]["nullable"] is True
    finally:
        engine.dispose()


def test_structure_unit_schema_has_exact_provenance_columns(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "structure-columns.db"
    command.upgrade(_config(database_path), TARGET_REVISION)

    engine = create_database_engine(database_path)
    try:
        inspector = inspect(engine)

        assert {column["name"] for column in inspector.get_columns("document_structure_units")} == {
            "id",
            "document_version_id",
            "ordinal",
            "kind",
            "text",
            "heading_level",
            "table_index",
            "row_index",
            "column_index",
            "created_at",
        }

        assert {index["name"] for index in inspector.get_indexes("document_structure_units")} >= {
            "ix_document_structure_units_version_ordinal",
        }
    finally:
        engine.dispose()


def test_structure_unit_has_version_foreign_key_and_unique_ordinal(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "structure-integrity.db"
    command.upgrade(_config(database_path), TARGET_REVISION)

    engine = create_database_engine(database_path)
    try:
        inspector = inspect(engine)

        foreign_keys = inspector.get_foreign_keys("document_structure_units")
        assert len(foreign_keys) == 1
        assert foreign_keys[0]["referred_table"] == "document_versions"
        assert foreign_keys[0]["options"]["ondelete"] == "RESTRICT"

        unique_constraints = {
            item["name"] for item in inspector.get_unique_constraints("document_structure_units")
        }
        assert "uq_document_structure_units_version_ordinal" in unique_constraints
    finally:
        engine.dispose()


def test_structural_schema_rejects_invalid_unit_shapes(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "structure-checks.db"
    command.upgrade(_config(database_path), TARGET_REVISION)

    engine = create_database_engine(database_path)
    try:
        inspector = inspect(engine)
        checks = {
            item["name"] for item in inspector.get_check_constraints("document_structure_units")
        }

        assert checks >= {
            "ck_document_structure_units_id",
            "ck_document_structure_units_document_version_id",
            "ck_document_structure_units_ordinal",
            "ck_document_structure_units_kind",
            "ck_document_structure_units_shape",
        }
    finally:
        engine.dispose()


def test_structural_migration_allows_docx_content_blob(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "docx-content-blob.db"
    command.upgrade(_config(database_path), TARGET_REVISION)

    digest = "a" * 64
    storage_key = f"sha256/{digest[:2]}/{digest}.docx"
    media_type = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

    engine = create_database_engine(database_path)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO content_blobs (
                        id,
                        sha256,
                        size_bytes,
                        media_type,
                        storage_key,
                        created_at
                    )
                    VALUES (
                        1,
                        :sha256,
                        128,
                        :media_type,
                        :storage_key,
                        '2026-01-01T00:00:00Z'
                    )
                    """
                ),
                {
                    "sha256": digest,
                    "media_type": media_type,
                    "storage_key": storage_key,
                },
            )

        with engine.connect() as connection:
            stored = connection.execute(
                text(
                    """
                    SELECT media_type, storage_key
                    FROM content_blobs
                    WHERE id = 1
                    """
                )
            ).one()

        assert stored.media_type == media_type
        assert stored.storage_key == storage_key
    finally:
        engine.dispose()


def test_structural_migration_preserves_pdf_storage_hash_binding(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "pdf-storage-binding.db"
    command.upgrade(_config(database_path), TARGET_REVISION)

    digest = "b" * 64

    # Correct total length and .pdf suffix, but deliberately violates
    # the content-addressed sha256/<prefix>/<digest>.pdf layout.
    malformed_storage_key = ("x" * 74) + ".pdf"

    assert len(malformed_storage_key) == 78

    engine = create_database_engine(database_path)
    try:
        with pytest.raises(
            IntegrityError,
            match="ck_content_blobs_storage_key",
        ):
            with engine.begin() as connection:
                connection.execute(
                    text(
                        """
                        INSERT INTO content_blobs (
                            id,
                            sha256,
                            size_bytes,
                            media_type,
                            storage_key,
                            created_at
                        )
                        VALUES (
                            1,
                            :sha256,
                            128,
                            'application/pdf',
                            :storage_key,
                            '2026-01-01T00:00:00Z'
                        )
                        """
                    ),
                    {
                        "sha256": digest,
                        "storage_key": malformed_storage_key,
                    },
                )
    finally:
        engine.dispose()


_DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def _seed_common_document_rows(
    engine: Engine,
    *,
    digest: str,
    media_type: str,
    storage_key: str,
) -> None:
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO courses (
                    id, source_system, source_id, title
                )
                VALUES (
                    1, 'test', 'course-1', 'Course'
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO remote_files (
                    id,
                    course_id,
                    source_system,
                    source_id,
                    title,
                    remote_url
                )
                VALUES (
                    1,
                    1,
                    'test',
                    'file-1',
                    'File',
                    NULL
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO documents (
                    id,
                    remote_file_id,
                    ingestion_status,
                    created_at,
                    updated_at
                )
                VALUES (
                    1,
                    1,
                    'READY',
                    '2026-01-01T00:00:00Z',
                    '2026-01-01T00:00:00Z'
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO content_blobs (
                    id,
                    sha256,
                    size_bytes,
                    media_type,
                    storage_key,
                    created_at
                )
                VALUES (
                    1,
                    :digest,
                    128,
                    :media_type,
                    :storage_key,
                    '2026-01-01T00:00:00Z'
                )
                """
            ),
            {
                "digest": digest,
                "media_type": media_type,
                "storage_key": storage_key,
            },
        )


def _seed_structural_version(engine: Engine) -> None:
    digest = "c" * 64
    _seed_common_document_rows(
        engine,
        digest=digest,
        media_type=_DOCX_MIME,
        storage_key=f"sha256/{digest[:2]}/{digest}.docx",
    )

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO document_versions (
                    id,
                    document_id,
                    version_number,
                    content_blob_id,
                    source_filename,
                    ingested_at,
                    extraction_status,
                    provenance_mode,
                    page_count
                )
                VALUES (
                    1,
                    1,
                    1,
                    1,
                    'source.docx',
                    '2026-01-01T00:00:00Z',
                    'SUCCESS',
                    'STRUCTURE',
                    NULL
                )
                """
            )
        )


def test_upgrade_backfills_existing_pdf_version_as_page_provenance(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "backfill-page.db"
    config = _config(database_path)

    command.upgrade(config, "0007_phase8_visual_provenance")

    digest = "d" * 64
    engine = create_database_engine(database_path)
    try:
        _seed_common_document_rows(
            engine,
            digest=digest,
            media_type="application/pdf",
            storage_key=f"sha256/{digest[:2]}/{digest}.pdf",
        )

        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO document_versions (
                        id,
                        document_id,
                        version_number,
                        content_blob_id,
                        source_filename,
                        ingested_at,
                        extraction_status,
                        page_count
                    )
                    VALUES (
                        1,
                        1,
                        1,
                        1,
                        'source.pdf',
                        '2026-01-01T00:00:00Z',
                        'SUCCESS',
                        3
                    )
                    """
                )
            )
    finally:
        engine.dispose()

    command.upgrade(config, TARGET_REVISION)

    engine = create_database_engine(database_path)
    try:
        with engine.connect() as connection:
            row = connection.execute(
                text(
                    """
                    SELECT provenance_mode, page_count
                    FROM document_versions
                    WHERE id = 1
                    """
                )
            ).one()

        assert row.provenance_mode == "PAGE"
        assert row.page_count == 3
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    ("provenance_mode", "page_count"),
    (
        ("BROKEN", 1),
        ("PAGE", None),
        ("STRUCTURE", 1),
    ),
)
def test_document_version_provenance_shape_is_enforced(
    tmp_path: Path,
    provenance_mode: str,
    page_count: int | None,
) -> None:
    database_path = tmp_path / f"invalid-{provenance_mode}-{page_count}.db"
    command.upgrade(_config(database_path), TARGET_REVISION)

    digest = "e" * 64
    engine = create_database_engine(database_path)
    try:
        _seed_common_document_rows(
            engine,
            digest=digest,
            media_type="application/pdf",
            storage_key=f"sha256/{digest[:2]}/{digest}.pdf",
        )

        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                connection.execute(
                    text(
                        """
                        INSERT INTO document_versions (
                            id,
                            document_id,
                            version_number,
                            content_blob_id,
                            source_filename,
                            ingested_at,
                            extraction_status,
                            provenance_mode,
                            page_count
                        )
                        VALUES (
                            1,
                            1,
                            1,
                            1,
                            'source.bin',
                            '2026-01-01T00:00:00Z',
                            'SUCCESS',
                            :provenance_mode,
                            :page_count
                        )
                        """
                    ),
                    {
                        "provenance_mode": provenance_mode,
                        "page_count": page_count,
                    },
                )
    finally:
        engine.dispose()


def test_structural_migration_preserves_docx_storage_hash_binding(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "docx-storage-binding.db"
    command.upgrade(_config(database_path), TARGET_REVISION)

    digest = "f" * 64
    malformed_storage_key = ("x" * 74) + ".docx"

    assert len(malformed_storage_key) == 79

    engine = create_database_engine(database_path)
    try:
        with pytest.raises(
            IntegrityError,
            match="ck_content_blobs_storage_key",
        ):
            with engine.begin() as connection:
                connection.execute(
                    text(
                        """
                        INSERT INTO content_blobs (
                            id,
                            sha256,
                            size_bytes,
                            media_type,
                            storage_key,
                            created_at
                        )
                        VALUES (
                            1,
                            :digest,
                            128,
                            :media_type,
                            :storage_key,
                            '2026-01-01T00:00:00Z'
                        )
                        """
                    ),
                    {
                        "digest": digest,
                        "media_type": _DOCX_MIME,
                        "storage_key": malformed_storage_key,
                    },
                )
    finally:
        engine.dispose()


def test_clean_structural_migration_downgrade_to_0007_succeeds(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "clean-downgrade.db"
    config = _config(database_path)

    command.upgrade(config, TARGET_REVISION)
    command.downgrade(config, "0007_phase8_visual_provenance")

    engine = create_database_engine(database_path)
    try:
        inspector = inspect(engine)

        assert "document_structure_units" not in inspector.get_table_names()

        version_columns = {
            column["name"]: column for column in inspector.get_columns("document_versions")
        }

        assert "provenance_mode" not in version_columns
        assert version_columns["page_count"]["nullable"] is False
    finally:
        engine.dispose()


def test_downgrade_refuses_before_ddl_when_structure_versions_exist(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "structural-downgrade.db"
    config = _config(database_path)

    command.upgrade(config, TARGET_REVISION)

    engine = create_database_engine(database_path)
    try:
        _seed_structural_version(engine)

        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO document_structure_units (
                        id,
                        document_version_id,
                        ordinal,
                        kind,
                        text,
                        heading_level,
                        table_index,
                        row_index,
                        column_index,
                        created_at
                    )
                    VALUES (
                        1,
                        1,
                        1,
                        'PARAGRAPH',
                        'structural text',
                        NULL,
                        NULL,
                        NULL,
                        NULL,
                        '2026-01-01T00:00:00Z'
                    )
                    """
                )
            )
    finally:
        engine.dispose()

    with pytest.raises(
        RuntimeError,
        match="Cannot downgrade structural provenance",
    ):
        command.downgrade(
            config,
            "0007_phase8_visual_provenance",
        )

    engine = create_database_engine(database_path)
    try:
        inspector = inspect(engine)

        assert "document_structure_units" in inspector.get_table_names()
        assert "provenance_mode" in {
            column["name"] for column in inspector.get_columns("document_versions")
        }

        with engine.connect() as connection:
            assert (
                connection.scalar(
                    text(
                        """
                    SELECT COUNT(*)
                    FROM document_structure_units
                    """
                    )
                )
                == 1
            )

            assert (
                connection.scalar(text("SELECT version_num FROM alembic_version"))
                == TARGET_REVISION
            )
    finally:
        engine.dispose()


def test_downgrade_refuses_before_ddl_when_docx_blobs_exist(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "docx-downgrade.db"
    config = _config(database_path)

    command.upgrade(config, TARGET_REVISION)

    digest = "9" * 64
    engine = create_database_engine(database_path)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO content_blobs (
                        id,
                        sha256,
                        size_bytes,
                        media_type,
                        storage_key,
                        created_at
                    )
                    VALUES (
                        1,
                        :digest,
                        128,
                        :media_type,
                        :storage_key,
                        '2026-01-01T00:00:00Z'
                    )
                    """
                ),
                {
                    "digest": digest,
                    "media_type": _DOCX_MIME,
                    "storage_key": f"sha256/{digest[:2]}/{digest}.docx",
                },
            )
    finally:
        engine.dispose()

    with pytest.raises(
        RuntimeError,
        match="Cannot downgrade structural provenance",
    ):
        command.downgrade(
            config,
            "0007_phase8_visual_provenance",
        )

    engine = create_database_engine(database_path)
    try:
        inspector = inspect(engine)

        assert "document_structure_units" in inspector.get_table_names()
        assert "provenance_mode" in {
            column["name"] for column in inspector.get_columns("document_versions")
        }

        with engine.connect() as connection:
            assert (
                connection.scalar(
                    text(
                        """
                    SELECT COUNT(*)
                    FROM content_blobs
                    WHERE media_type = :media_type
                    """
                    ),
                    {"media_type": _DOCX_MIME},
                )
                == 1
            )

            assert (
                connection.scalar(text("SELECT version_num FROM alembic_version"))
                == TARGET_REVISION
            )
    finally:
        engine.dispose()


def test_populated_pdf_database_downgrades_cleanly_to_0007(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "populated-pdf-downgrade.db"
    config = _config(database_path)

    command.upgrade(config, TARGET_REVISION)

    digest = "8" * 64
    engine = create_database_engine(database_path)
    try:
        _seed_common_document_rows(
            engine,
            digest=digest,
            media_type="application/pdf",
            storage_key=f"sha256/{digest[:2]}/{digest}.pdf",
        )

        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO document_versions (
                        id,
                        document_id,
                        version_number,
                        content_blob_id,
                        source_filename,
                        ingested_at,
                        extraction_status,
                        provenance_mode,
                        page_count
                    )
                    VALUES (
                        1,
                        1,
                        1,
                        1,
                        'source.pdf',
                        '2026-01-01T00:00:00Z',
                        'SUCCESS',
                        'PAGE',
                        2
                    )
                    """
                )
            )
    finally:
        engine.dispose()

    command.downgrade(config, "0007_phase8_visual_provenance")

    engine = create_database_engine(database_path)
    try:
        with engine.connect() as connection:
            row = connection.execute(
                text(
                    """
                    SELECT page_count
                    FROM document_versions
                    WHERE id = 1
                    """
                )
            ).one()

            assert row.page_count == 2
            assert (
                connection.scalar(text("SELECT version_num FROM alembic_version"))
                == "0007_phase8_visual_provenance"
            )
    finally:
        engine.dispose()


def test_populated_pdf_page_survives_upgrade_and_downgrade(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "populated-pdf-page-roundtrip.db"
    config = _config(database_path)

    command.upgrade(config, "0007_phase8_visual_provenance")

    digest = "7" * 64
    engine = create_database_engine(database_path)
    try:
        _seed_common_document_rows(
            engine,
            digest=digest,
            media_type="application/pdf",
            storage_key=f"sha256/{digest[:2]}/{digest}.pdf",
        )

        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO document_versions (
                        id,
                        document_id,
                        version_number,
                        content_blob_id,
                        source_filename,
                        ingested_at,
                        extraction_status,
                        page_count
                    )
                    VALUES (
                        1,
                        1,
                        1,
                        1,
                        'source.pdf',
                        '2026-01-01T00:00:00Z',
                        'SUCCESS',
                        1
                    )
                    """
                )
            )
            connection.execute(
                text(
                    """
                    INSERT INTO document_pages (
                        id,
                        document_version_id,
                        page_number,
                        text,
                        text_source,
                        width_points,
                        height_points,
                        created_at
                    )
                    VALUES (
                        1,
                        1,
                        1,
                        'page text',
                        'EMBEDDED_PDF',
                        612.0,
                        792.0,
                        '2026-01-01T00:00:00Z'
                    )
                    """
                )
            )
    finally:
        engine.dispose()

    command.upgrade(config, TARGET_REVISION)

    engine = create_database_engine(database_path)
    try:
        with engine.connect() as connection:
            page = connection.execute(
                text(
                    """
                    SELECT
                        document_version_id,
                        page_number,
                        text,
                        text_source
                    FROM document_pages
                    WHERE id = 1
                    """
                )
            ).one()

            assert page.document_version_id == 1
            assert page.page_number == 1
            assert page.text == "page text"
            assert page.text_source == "EMBEDDED_PDF"

            version = connection.execute(
                text(
                    """
                    SELECT provenance_mode, page_count
                    FROM document_versions
                    WHERE id = 1
                    """
                )
            ).one()

            assert version.provenance_mode == "PAGE"
            assert version.page_count == 1
            assert connection.exec_driver_sql("PRAGMA foreign_key_check").first() is None
    finally:
        engine.dispose()

    command.downgrade(config, "0007_phase8_visual_provenance")

    engine = create_database_engine(database_path)
    try:
        with engine.connect() as connection:
            page = connection.execute(
                text(
                    """
                    SELECT
                        document_version_id,
                        page_number,
                        text,
                        text_source
                    FROM document_pages
                    WHERE id = 1
                    """
                )
            ).one()

            assert page.document_version_id == 1
            assert page.page_number == 1
            assert page.text == "page text"
            assert page.text_source == "EMBEDDED_PDF"

            assert (
                connection.scalar(
                    text(
                        """
                        SELECT page_count
                        FROM document_versions
                        WHERE id = 1
                        """
                    )
                )
                == 1
            )
            assert connection.exec_driver_sql("PRAGMA foreign_key_check").first() is None
            assert (
                connection.scalar(text("SELECT version_num FROM alembic_version"))
                == "0007_phase8_visual_provenance"
            )
    finally:
        engine.dispose()
