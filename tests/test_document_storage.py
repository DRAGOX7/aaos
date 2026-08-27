"""Security and lifecycle tests for the Phase 3 content-addressed blob store."""

from __future__ import annotations

from pathlib import Path

import pytest

from aaos.documents.contracts import (
    MAX_PDF_BYTES,
    BlobInstall,
    DocumentIngestionFailure,
    IngestionErrorCode,
)
from aaos.documents.storage import ContentBlobStore, StagedBlob, sanitize_source_filename

ABC_SHA256 = "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
ABC_STORAGE_KEY = f"sha256/ba/{ABC_SHA256}.pdf"


def assert_storage_failure(caught: pytest.ExceptionInfo[DocumentIngestionFailure]) -> None:
    assert caught.value.code is IngestionErrorCode.STORAGE_FAILED
    assert str(caught.value) == "document ingestion failed"


@pytest.mark.parametrize(
    ("unsafe", "expected"),
    (
        ("../../lecture.pdf", "lecture.pdf"),
        (r"..\..\lecture.pdf", "lecture.pdf"),
        (r"C:\something.pdf", "something.pdf"),
        ("/foo/bar.pdf", "bar.pdf"),
        ("lecture/../../x.pdf", "x.pdf"),
    ),
)
def test_filename_traversal_is_reduced_to_safe_display_basename(
    unsafe: str,
    expected: str,
) -> None:
    assert sanitize_source_filename(unsafe, fallback="document") == expected


def test_filename_sanitization_handles_controls_reserved_chars_and_devices() -> None:
    assert sanitize_source_filename("bad<name>\x00?.pdf. ", fallback="document") == (
        "bad_name__.pdf"
    )
    assert sanitize_source_filename("CON.pdf", fallback="document") == "_CON.pdf"
    assert sanitize_source_filename("LPT9", fallback="document") == "_LPT9"
    assert sanitize_source_filename(None, fallback="course-7") == "course-7.pdf"


def test_filename_sanitization_preserves_safe_arabic_without_normalization() -> None:
    value = "محاضرة ١.pdf"

    assert sanitize_source_filename(value, fallback="document") == value


def test_filename_sanitization_is_bounded_to_255_utf8_bytes() -> None:
    result = sanitize_source_filename("م" * 200 + ".pdf", fallback="document")

    assert len(result.encode("utf-8")) <= 255
    result.encode("utf-8").decode("utf-8")


def test_stage_hashes_exact_bytes_under_injected_temporary_root(tmp_path: Path) -> None:
    root = tmp_path / "store"
    staged = ContentBlobStore(root).stage(b"abc")

    assert staged.sha256 == ABC_SHA256
    assert staged.size_bytes == 3
    assert staged.temporary_path.parent == root / ".temporary"
    assert staged.temporary_path.is_file()
    assert staged.temporary_path.read_bytes() == b"abc"
    assert str(staged.temporary_path) not in repr(staged)
    assert "temporary_path" not in repr(staged)
    assert not (tmp_path / "data" / "documents").exists()


@pytest.mark.parametrize(
    "body",
    (b"", b"x" * (MAX_PDF_BYTES + 1)),
    ids=("empty", "oversized"),
)
def test_stage_rejects_empty_and_oversized_bodies(tmp_path: Path, body: bytes) -> None:
    root = tmp_path / "store"

    with pytest.raises(DocumentIngestionFailure) as caught:
        ContentBlobStore(root).stage(body)

    assert_storage_failure(caught)
    assert not (root / ".temporary").exists()


def test_first_install_atomically_moves_to_exact_relative_storage_key(
    tmp_path: Path,
) -> None:
    root = tmp_path / "store"
    store = ContentBlobStore(root)
    staged = store.stage(b"abc")

    install = store.install(staged)

    assert install == BlobInstall(
        sha256=ABC_SHA256,
        size_bytes=3,
        storage_key=ABC_STORAGE_KEY,
        created=True,
    )
    assert not Path(install.storage_key).is_absolute()
    assert not staged.temporary_path.exists()
    assert (root / Path(install.storage_key)).read_bytes() == b"abc"


def test_source_filename_has_no_influence_on_content_storage_key(tmp_path: Path) -> None:
    unsafe_names = (
        "../../lecture.pdf",
        r"..\..\lecture.pdf",
        r"C:\something.pdf",
        "/foo/bar.pdf",
        "lecture/../../x.pdf",
    )
    sanitized = tuple(
        sanitize_source_filename(value, fallback="document") for value in unsafe_names
    )
    root = tmp_path / "store"
    install = ContentBlobStore(root).install(ContentBlobStore(root).stage(b"abc"))

    assert sanitized == (
        "lecture.pdf",
        "lecture.pdf",
        "something.pdf",
        "bar.pdf",
        "x.pdf",
    )
    assert install.storage_key == ABC_STORAGE_KEY


def test_identical_reinstall_verifies_and_reuses_existing_blob(tmp_path: Path) -> None:
    store = ContentBlobStore(tmp_path / "store")
    first = store.install(store.stage(b"abc"))
    second_staged = store.stage(b"abc")

    second = store.install(second_staged)

    assert first.created is True
    assert second == BlobInstall(
        sha256=ABC_SHA256,
        size_bytes=3,
        storage_key=ABC_STORAGE_KEY,
        created=False,
    )
    assert not second_staged.temporary_path.exists()


@pytest.mark.parametrize("existing", (b"bad", b"too-long"))
def test_mismatching_existing_blob_fails_closed(
    tmp_path: Path,
    existing: bytes,
) -> None:
    root = tmp_path / "store"
    store = ContentBlobStore(root)
    staged = store.stage(b"abc")
    target = root / Path(ABC_STORAGE_KEY)
    target.parent.mkdir(parents=True)
    target.write_bytes(existing)

    with pytest.raises(DocumentIngestionFailure) as caught:
        store.install(staged)

    assert_storage_failure(caught)
    assert target.read_bytes() == existing
    assert staged.temporary_path.exists()


def test_existing_blob_target_must_be_regular_file(tmp_path: Path) -> None:
    root = tmp_path / "store"
    store = ContentBlobStore(root)
    staged = store.stage(b"abc")
    target = root / Path(ABC_STORAGE_KEY)
    target.mkdir(parents=True)

    with pytest.raises(DocumentIngestionFailure) as caught:
        store.install(staged)

    assert_storage_failure(caught)


def test_non_directory_content_parent_fails_closed(tmp_path: Path) -> None:
    root = tmp_path / "store"
    store = ContentBlobStore(root)
    staged = store.stage(b"abc")
    (root / "sha256").write_text("not a directory", encoding="utf-8")

    with pytest.raises(DocumentIngestionFailure) as caught:
        store.install(staged)

    assert_storage_failure(caught)


def test_symlink_or_reparse_content_parent_fails_closed(tmp_path: Path) -> None:
    root = tmp_path / "store"
    outside = tmp_path / "outside"
    outside.mkdir()
    store = ContentBlobStore(root)
    staged = store.stage(b"abc")
    link = root / "sha256"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlinks/reparse points are unavailable")

    with pytest.raises(DocumentIngestionFailure) as caught:
        store.install(staged)

    assert_storage_failure(caught)
    assert not (outside / "ba" / f"{ABC_SHA256}.pdf").exists()


def test_relative_and_non_normalized_store_roots_fail_closed(tmp_path: Path) -> None:
    for root in (Path("relative-store"), tmp_path / "child" / ".."):
        with pytest.raises(DocumentIngestionFailure) as caught:
            ContentBlobStore(root)
        assert_storage_failure(caught)


def test_discard_temporary_is_idempotent_for_owned_missing_file(tmp_path: Path) -> None:
    store = ContentBlobStore(tmp_path / "store")
    staged = store.stage(b"abc")

    store.discard_temporary(staged)
    store.discard_temporary(staged)

    assert not staged.temporary_path.exists()


def test_discard_rejects_prefix_confusion_and_preserves_outside_file(
    tmp_path: Path,
) -> None:
    root = tmp_path / "store"
    outside = tmp_path / "store-currentevil" / ".temporary"
    outside.mkdir(parents=True)
    outside_file = outside / "aaos-owned-looking.tmp"
    outside_file.write_bytes(b"abc")
    forged = StagedBlob(
        sha256=ABC_SHA256,
        size_bytes=3,
        temporary_path=outside_file,
    )

    with pytest.raises(DocumentIngestionFailure) as caught:
        ContentBlobStore(root).discard_temporary(forged)

    assert_storage_failure(caught)
    assert outside_file.read_bytes() == b"abc"


def test_compensation_removes_only_new_unreferenced_blob(tmp_path: Path) -> None:
    root = tmp_path / "store"
    store = ContentBlobStore(root)
    install = store.install(store.stage(b"abc"))
    target = root / Path(install.storage_key)

    store.compensate_new_blob(install, referenced=False)

    assert not target.exists()
    assert not target.parent.exists()


@pytest.mark.parametrize(
    ("created", "referenced"),
    (
        (False, False),
        (False, True),
        (True, True),
    ),
)
def test_compensation_preserves_preexisting_or_referenced_blob(
    tmp_path: Path,
    created: bool,
    referenced: bool,
) -> None:
    root = tmp_path / "store"
    store = ContentBlobStore(root)
    original = store.install(store.stage(b"abc"))
    install = BlobInstall(
        sha256=original.sha256,
        size_bytes=original.size_bytes,
        storage_key=original.storage_key,
        created=created,
    )
    target = root / Path(install.storage_key)

    store.compensate_new_blob(install, referenced=referenced)

    assert target.read_bytes() == b"abc"


def test_compensation_rejects_absolute_or_malformed_storage_key(tmp_path: Path) -> None:
    root = tmp_path / "store"
    outside = tmp_path / "outside.pdf"
    outside.write_bytes(b"abc")
    install = BlobInstall(
        sha256=ABC_SHA256,
        size_bytes=3,
        storage_key=str(outside),
        created=True,
    )

    with pytest.raises(DocumentIngestionFailure) as caught:
        ContentBlobStore(root).compensate_new_blob(install, referenced=False)

    assert_storage_failure(caught)
    assert outside.read_bytes() == b"abc"
