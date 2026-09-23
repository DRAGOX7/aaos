from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import inspect

from aaos.persistence import rag_models
from aaos.persistence.database import create_database_engine

PROJECT_ROOT = Path(__file__).resolve().parents[1]
TARGET_REVISION = "0009_rag_structural_provenance"
PARENT_REVISION = "0008_document_structural_provenance"


def _config(database_path: Path) -> Config:
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.set_main_option(
        "aaos.database_path",
        str(database_path),
    )
    return config


def test_rag_structural_provenance_revision_extends_document_structure_head(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path / "revision.db")
    script = ScriptDirectory.from_config(config)

    revision = script.get_revision(TARGET_REVISION)

    assert revision is not None
    assert revision.down_revision == PARENT_REVISION


def test_rag_chunk_orm_declares_explicit_provenance_shape() -> None:
    table = rag_models.RagChunkORM.__table__

    assert "provenance_mode" in table.c
    assert table.c.provenance_mode.nullable is False
    assert table.c.page_start.nullable is True
    assert table.c.page_end.nullable is True

    checks = {
        constraint.name
        for constraint in table.constraints
        if constraint.name is not None
    }

    assert "ck_rag_chunks_provenance_mode" in checks
    assert "ck_rag_chunks_provenance_shape" in checks


def test_rag_structural_source_span_orm_is_a_sibling_of_page_spans() -> None:
    model = getattr(
        rag_models,
        "RagChunkStructureSourceSpanORM",
        None,
    )

    assert model is not None

    table = model.__table__

    assert table.name == "rag_chunk_structure_source_spans"
    assert tuple(table.c.keys()) == (
        "id",
        "rag_chunk_id",
        "document_structure_unit_id",
        "ordinal",
        "source_char_start",
        "source_char_end",
        "chunk_char_start",
        "chunk_char_end",
    )

    assert table.c.rag_chunk_id.nullable is False
    assert table.c.document_structure_unit_id.nullable is False
    assert table.c.ordinal.nullable is False


def test_migration_preserves_page_spans_and_adds_structure_spans(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "rag-structure.db"

    command.upgrade(
        _config(database_path),
        TARGET_REVISION,
    )

    engine = create_database_engine(database_path)

    try:
        schema = inspect(engine)

        assert "rag_chunk_source_spans" in schema.get_table_names()
        assert (
            "rag_chunk_structure_source_spans"
            in schema.get_table_names()
        )

        chunk_columns = {
            column["name"]: column
            for column in schema.get_columns("rag_chunks")
        }

        assert chunk_columns["provenance_mode"]["nullable"] is False
        assert chunk_columns["page_start"]["nullable"] is True
        assert chunk_columns["page_end"]["nullable"] is True

        chunk_checks = {
            item["name"]
            for item in schema.get_check_constraints("rag_chunks")
        }

        assert "ck_rag_chunks_provenance_mode" in chunk_checks
        assert "ck_rag_chunks_provenance_shape" in chunk_checks

        # Historical PAGE provenance remains PAGE-specific.
        page_columns = {
            column["name"]: column
            for column in schema.get_columns(
                "rag_chunk_source_spans"
            )
        }

        assert tuple(page_columns) == (
            "id",
            "rag_chunk_id",
            "document_page_id",
            "ordinal",
            "page_number",
            "page_char_start",
            "page_char_end",
            "chunk_char_start",
            "chunk_char_end",
        )
        assert page_columns["document_page_id"]["nullable"] is False
        assert page_columns["page_number"]["nullable"] is False

        structure_columns = {
            column["name"]: column
            for column in schema.get_columns(
                "rag_chunk_structure_source_spans"
            )
        }

        assert tuple(structure_columns) == (
            "id",
            "rag_chunk_id",
            "document_structure_unit_id",
            "ordinal",
            "source_char_start",
            "source_char_end",
            "chunk_char_start",
            "chunk_char_end",
        )

        foreign_keys = schema.get_foreign_keys(
            "rag_chunk_structure_source_spans"
        )

        targets = {
            (
                tuple(item["constrained_columns"]),
                item["referred_table"],
                tuple(item["referred_columns"]),
            )
            for item in foreign_keys
        }

        assert (
            ("rag_chunk_id",),
            "rag_chunks",
            ("id",),
        ) in targets

        assert (
            ("document_structure_unit_id",),
            "document_structure_units",
            ("id",),
        ) in targets

        unique_constraints = {
            (
                item["name"],
                tuple(item["column_names"]),
            )
            for item in schema.get_unique_constraints(
                "rag_chunk_structure_source_spans"
            )
        }

        assert (
            "uq_rag_chunk_structure_source_spans_chunk_ordinal",
            ("rag_chunk_id", "ordinal"),
        ) in unique_constraints

        checks = {
            item["name"]
            for item in schema.get_check_constraints(
                "rag_chunk_structure_source_spans"
            )
        }

        assert checks.issuperset(
            {
                "ck_rag_chunk_structure_source_spans_id",
                "ck_rag_chunk_structure_source_spans_chunk_id",
                "ck_rag_chunk_structure_source_spans_unit_id",
                "ck_rag_chunk_structure_source_spans_ordinal",
                "ck_rag_chunk_structure_source_spans_source_bounds",
                "ck_rag_chunk_structure_source_spans_chunk_bounds",
            }
        )
    finally:
        engine.dispose()
