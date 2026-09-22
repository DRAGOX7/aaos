from aaos.academic.source import RawCourse, SourceCollectionFailure
from aaos.moodle.browser import MoodleBrowserSource
from aaos.moodle.browser_models import BrowserPolicyError
from aaos.moodle.browser_navigation import BrowserDocument
from aaos.moodle.collection import EntityBundle
from aaos.moodle.embedded_drive import DriveFolderReference

_ORIGIN = "https://moodle.example.edu"


class _Navigator:
    failure_stage = "NONE"

    def __init__(self) -> None:
        self.drive_reads: list[str] = []

    def open_course_detail(self, identifier: int) -> BrowserDocument:
        assert identifier == 42
        return BrowserDocument(
            "COURSE_DETAIL",
            _ORIGIN,
            "/current/course/view.php",
            "text/html",
            """
            <a href="/current/mod/resource/view.php?id=900">Native Moodle PDF</a>

            <iframe
              src="https://drive.google.com/embeddedfolderview?id=folder_A#list">
            </iframe>

            <iframe
              src="https://drive.google.com/embeddedfolderview?id=folder_B#grid">
            </iframe>
            """,
        )

    def read_embedded_drive_folder(
        self,
        folder: DriveFolderReference,
    ) -> str:
        self.drive_reads.append(folder.folder_id)

        if folder.folder_id == "folder_A":
            return """
            <a href="https://drive.google.com/file/d/file_ALPHA_123/view?usp=drive_link">
              <div class="flip-entry-title">Lecture A.pdf</div>
            </a>
            """

        if folder.folder_id == "folder_B":
            return """
            <a href="https://drive.google.com/file/d/file_BETA_456/view?usp=drive_link">
              <div class="flip-entry-title">Lecture B.docx</div>
            </a>

            <a href="https://drive.google.com/file/d/file_ALPHA_123/view?usp=drive_link">
              <div class="flip-entry-title">Lecture A.pdf</div>
            </a>
            """

        raise AssertionError("unexpected folder")

    def open_announcement_list(self, identifier: int) -> BrowserDocument:
        raise AssertionError(f"announcement navigation must not run: {identifier}")


def test_browser_source_merges_native_and_embedded_drive_files() -> None:
    navigator = _Navigator()

    source = MoodleBrowserSource(
        navigator,
        "moodle:fixture:embedded-drive",
        _ORIGIN,
    )

    result = source.collect_course_entities(
        RawCourse("42", "Synthetic"),
    )

    assert isinstance(result, EntityBundle)

    assert navigator.drive_reads == [
        "folder_A",
        "folder_B",
    ]

    assert tuple(record.source_id for record in result.remote_files) == (
        "resource-cm:900",
        "drive-file:42:file_ALPHA_123",
        "drive-file:42:file_BETA_456",
    )

    assert tuple(record.title for record in result.remote_files) == (
        "Native Moodle PDF",
        "Lecture A.pdf",
        "Lecture B.docx",
    )

    assert all(record.course_source_id == "42" for record in result.remote_files)

    assert result.is_complete is True


def test_browser_source_fails_closed_when_embedded_drive_read_fails() -> None:
    class FailingNavigator(_Navigator):
        def read_embedded_drive_folder(
            self,
            folder: DriveFolderReference,
        ) -> str:
            del folder
            raise BrowserPolicyError()

    source = MoodleBrowserSource(
        FailingNavigator(),
        "moodle:fixture:embedded-drive",
        _ORIGIN,
    )

    result = source.collect_course_entities(
        RawCourse("42", "Synthetic"),
    )

    assert isinstance(result, SourceCollectionFailure)
    assert result.code == "browser_parser_schema"
