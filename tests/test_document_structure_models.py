from __future__ import annotations

from typing import cast

from sqlalchemy import Table


def test_document_version_model_maps_structural_provenance_fields() -> None:
    from aaos.persistence.document_models import DocumentVersionORM

    table = cast(Table, DocumentVersionORM.__table__)

    assert "provenance_mode" in table.c
    assert table.c.provenance_mode.nullable is False
    assert table.c.page_count.nullable is True

    check_names = {
        constraint.name for constraint in table.constraints if constraint.name is not None
    }

    assert {
        "ck_document_versions_provenance_mode",
        "ck_document_versions_provenance_shape",
    } <= check_names


def test_document_structure_unit_model_matches_migration_contract() -> None:
    from aaos.persistence.document_models import DocumentStructureUnitORM

    table = cast(Table, DocumentStructureUnitORM.__table__)

    assert set(table.c.keys()) == {
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

    assert table.c.document_version_id.nullable is False
    assert table.c.ordinal.nullable is False
    assert table.c.kind.nullable is False
    assert table.c.text.nullable is False

    foreign_keys = tuple(table.c.document_version_id.foreign_keys)
    assert len(foreign_keys) == 1
    assert foreign_keys[0].target_fullname == "document_versions.id"
    assert foreign_keys[0].ondelete == "RESTRICT"

    constraint_names = {
        constraint.name for constraint in table.constraints if constraint.name is not None
    }

    assert {
        "uq_document_structure_units_version_ordinal",
        "ck_document_structure_units_id",
        "ck_document_structure_units_document_version_id",
        "ck_document_structure_units_ordinal",
        "ck_document_structure_units_kind",
        "ck_document_structure_units_shape",
    } <= constraint_names

    assert {index.name for index in table.indexes} >= {
        "ix_document_structure_units_version_ordinal",
    }
