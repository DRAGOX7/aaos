from __future__ import annotations

from pathlib import Path

from sqlalchemy.orm import Session, sessionmaker

import aaos.rag as public_rag
from aaos.main import create_app
from aaos.rag import contracts as rag_contracts


def _schema() -> dict[str, object]:
    sessions: sessionmaker[Session] = sessionmaker()

    return create_app(
        session_factory=sessions,
    ).openapi()


def test_public_rag_module_exports_structural_contracts() -> None:
    assert (
        getattr(
            public_rag,
            "StructuralCitation",
            None,
        )
        is rag_contracts.StructuralCitation
    )

    assert (
        getattr(
            public_rag,
            "RagChunkStructureSourceSpanValue",
            None,
        )
        is rag_contracts.RagChunkStructureSourceSpanValue
    )


def test_live_openapi_exposes_structural_citation_union() -> None:
    schema = _schema()

    components = schema["components"]
    assert isinstance(components, dict)

    schemas = components["schemas"]
    assert isinstance(schemas, dict)

    assert "RagCitation" in schemas
    assert "RagStructuralCitation" in schemas

    structural = schemas["RagStructuralCitation"]
    assert isinstance(structural, dict)

    properties = structural["properties"]
    assert isinstance(properties, dict)

    assert set(properties) == {
        "document_id",
        "document_title",
        "unit_start",
        "unit_end",
        "heading",
        "table_index",
        "row_index",
        "column_index",
    }

    assert "page_start" not in properties
    assert "page_end" not in properties

    answer = schemas["RagAnswerResponse"]
    assert isinstance(answer, dict)

    answer_properties = answer["properties"]
    assert isinstance(answer_properties, dict)

    citations = answer_properties["citations"]
    assert isinstance(citations, dict)

    items = citations["items"]
    assert isinstance(items, dict)

    variants = items["anyOf"]
    assert isinstance(variants, list)

    refs = {
        variant["$ref"] for variant in variants if isinstance(variant, dict) and "$ref" in variant
    }

    assert refs == {
        "#/components/schemas/RagCitation",
        "#/components/schemas/RagStructuralCitation",
    }


def test_generated_browser_types_include_structural_citation() -> None:
    generated = Path("frontend/types/api.generated.ts").read_text(
        encoding="utf-8",
    )

    assert "RagStructuralCitation" in generated
    assert "unit_start" in generated
    assert "unit_end" in generated

    assert 'components["schemas"]["RagStructuralCitation"]' in generated
