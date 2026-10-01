"""Local re-extraction of a stored PDF version with the current extractor."""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
from pdf_fixtures import valid_multipage_pdf, valid_one_page_pdf
from sqlalchemy import select
from test_document_persistence import blob, document, migrated_session, page, version

from aaos.documents.reextract import ReextractError, reextract_pdf_version
from aaos.persistence.database import create_database_engine, create_session_factory
from aaos.persistence.document_models import DocumentPageORM


@contextmanager
def stored_version(
    tmp_path: Path,
    body: bytes,
    *,
    page_count: int = 1,
    stored_text: str = "STALE",
    storage_key: str | None = None,
    sha256: str | None = None,
) -> Iterator[tuple[object, Path, int]]:
    content_root = tmp_path / "documents"
    digest = hashlib.sha256(body).hexdigest()
    key = storage_key or f"sha256/{digest[:2]}/{digest}.pdf"
    target = content_root / key
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(body)
    with migrated_session(tmp_path) as (session, remote_file_id):
        document_row = document(remote_file_id)
        blob_row = blob(sha256=sha256 or digest, size_bytes=len(body), storage_key=key)
        session.add_all((document_row, blob_row))
        session.flush()
        version_row = version(document_row.id, blob_row.id, page_count=page_count)
        session.add(version_row)
        session.flush()
        session.add_all(
            page(version_row.id, page_number=number, text=f"{stored_text} {number}")
            for number in range(1, page_count + 1)
        )
        session.commit()
        engine = create_database_engine(tmp_path / "document.db")
        try:
            yield create_session_factory(engine), content_root, version_row.id
        finally:
            engine.dispose()


def _texts(sessions: object, version_id: int) -> list[str]:
    with sessions() as session:  # type: ignore[operator]
        return list(
            session.scalars(
                select(DocumentPageORM.text)
                .where(DocumentPageORM.document_version_id == version_id)
                .order_by(DocumentPageORM.page_number)
            )
        )


def test_reextract_rewrites_only_changed_page_text_and_is_idempotent(tmp_path: Path) -> None:
    with stored_version(tmp_path, valid_one_page_pdf()) as (sessions, root, version_id):
        first = reextract_pdf_version(sessions, root, version_id)
        second = reextract_pdf_version(sessions, root, version_id)

        assert first.changed_pages == (1,)
        assert first.page_count == 1
        assert "Hello AAOS" in _texts(sessions, version_id)[0]
        assert second.changed_pages == ()


def test_reextract_refuses_a_page_count_change(tmp_path: Path) -> None:
    with stored_version(tmp_path, valid_multipage_pdf(), page_count=1) as (
        sessions,
        root,
        version_id,
    ):
        with pytest.raises(ReextractError) as error:
            reextract_pdf_version(sessions, root, version_id)

        assert error.value.code == "PAGE_COUNT_CHANGED"
        assert _texts(sessions, version_id) == ["STALE 1"]


def test_reextract_refuses_a_blob_whose_hash_does_not_match(tmp_path: Path) -> None:
    with stored_version(tmp_path, valid_one_page_pdf(), sha256="b" * 64) as (
        sessions,
        root,
        version_id,
    ):
        with pytest.raises(ReextractError) as error:
            reextract_pdf_version(sessions, root, version_id)

        assert error.value.code == "BLOB_INTEGRITY_FAILED"
        assert _texts(sessions, version_id) == ["STALE 1"]


def test_blob_reads_are_confined_to_the_content_store(tmp_path: Path) -> None:
    """The database already constrains storage keys; the reader checks again."""
    from aaos.documents.reextract import _read_verified_blob

    body = valid_one_page_pdf()
    (tmp_path / "outside.pdf").write_bytes(body)
    root = tmp_path / "documents"
    root.mkdir()

    with pytest.raises(ReextractError) as error:
        _read_verified_blob(
            root,
            "../outside.pdf",
            sha256=hashlib.sha256(body).hexdigest(),
            size_bytes=len(body),
        )

    assert error.value.code == "BLOB_INTEGRITY_FAILED"


def test_reextract_reports_an_unknown_version(tmp_path: Path) -> None:
    with stored_version(tmp_path, valid_one_page_pdf()) as (sessions, root, version_id):
        with pytest.raises(ReextractError) as error:
            reextract_pdf_version(sessions, root, version_id + 99)

        assert error.value.code == "VERSION_NOT_FOUND"


@pytest.mark.parametrize(
    "argv",
    (
        ["reextract"],
        ["reextract", "--document-version-id"],
        ["reextract", "--document-version-id", "0"],
        ["reextract", "--remote-file-id", "3"],
    ),
)
def test_reextract_command_rejects_malformed_arguments(
    argv: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    from aaos.documents.cli import run_documents_command

    assert run_documents_command(argv) == 2
    assert "reextract" in capsys.readouterr().err
