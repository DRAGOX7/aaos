import pytest

from aaos.academic.source import RawRemoteFile
from aaos.moodle.browser_models import BrowserPolicyError
from aaos.moodle.embedded_drive import (
    DriveFolderReference,
    parse_embedded_drive_files,
    parse_embedded_drive_folders,
)

COURSE_ID = "2037"


def test_course_html_discovers_only_approved_embedded_drive_folders() -> None:
    html = """
    <iframe
      src="https://drive.google.com/embeddedfolderview?id=folder_A#list">
    </iframe>
    <iframe
      src="https://drive.google.com/embeddedfolderview?id=folder_B#grid">
    </iframe>
    """

    result = parse_embedded_drive_folders(html)

    assert result == (
        DriveFolderReference("folder_A"),
        DriveFolderReference("folder_B"),
    )


@pytest.mark.parametrize(
    "src",
    [
        "http://drive.google.com/embeddedfolderview?id=folder_A",
        "https://evil.example/embeddedfolderview?id=folder_A",
        "https://drive.google.com/file/d/folder_A/view",
        "https://drive.google.com/embeddedfolderview",
        "https://drive.google.com/embeddedfolderview?id=",
        "https://drive.google.com/embeddedfolderview?id=one&id=two",
        "javascript:alert(1)",
    ],
)
def test_course_html_rejects_unapproved_drive_embed_shapes(src: str) -> None:
    html = f'<iframe src="{src}"></iframe>'

    with pytest.raises(BrowserPolicyError):
        parse_embedded_drive_folders(html)


def test_drive_frame_maps_top_level_files_to_provider_scoped_remote_files() -> None:
    html = """
    <div class="flip-entry">
      <a href="https://drive.google.com/file/d/file_ALPHA_123/view?usp=drive_link">
        <div class="flip-entry-title">
          A STAI 301 Study notes - Machine learning and Data minig.pdf
        </div>
      </a>
    </div>

    <div class="flip-entry">
      <a href="https://drive.google.com/file/d/file_BETA_456/view?usp=drive_link">
        <div class="flip-entry-title">
          A STAI 401 Study notes - Introduction to KNIME.docx
        </div>
      </a>
    </div>
    """

    result = parse_embedded_drive_files(
        html,
        course_source_id=COURSE_ID,
    )

    assert result == (
        RawRemoteFile(
            source_id="drive-file:2037:file_ALPHA_123",
            course_source_id="2037",
            title="A STAI 301 Study notes - Machine learning and Data minig.pdf",
            remote_url=None,
        ),
        RawRemoteFile(
            source_id="drive-file:2037:file_BETA_456",
            course_source_id="2037",
            title="A STAI 401 Study notes - Introduction to KNIME.docx",
            remote_url=None,
        ),
    )


def test_drive_frame_deduplicates_the_same_file_identity() -> None:
    html = """
    <a href="https://drive.google.com/file/d/file_ALPHA_123/view">
      <div class="flip-entry-title">Lecture.pdf</div>
    </a>
    <a href="https://drive.google.com/file/d/file_ALPHA_123/view?usp=drive_link">
      <div class="flip-entry-title">Lecture.pdf</div>
    </a>
    """

    result = parse_embedded_drive_files(
        html,
        course_source_id=COURSE_ID,
    )

    assert len(result) == 1
    assert result[0].source_id == "drive-file:2037:file_ALPHA_123"


@pytest.mark.parametrize(
    "href",
    [
        "https://evil.example/file/d/file_ALPHA_123/view",
        "http://drive.google.com/file/d/file_ALPHA_123/view",
        "https://drive.google.com/open?id=file_ALPHA_123",
        "https://drive.google.com/file/d//view",
        "javascript:alert(1)",
    ],
)
def test_drive_frame_rejects_unapproved_file_targets(href: str) -> None:
    html = f"""
    <a href="{href}">
      <div class="flip-entry-title">Lecture.pdf</div>
    </a>
    """

    with pytest.raises(BrowserPolicyError):
        parse_embedded_drive_files(
            html,
            course_source_id=COURSE_ID,
        )


@pytest.mark.parametrize(
    "title",
    [
        "",
        "   ",
        "../private.pdf",
        "bad\x00name.pdf",
    ],
)
def test_drive_frame_rejects_missing_or_unsafe_titles(title: str) -> None:
    html = f"""
    <a href="https://drive.google.com/file/d/file_ALPHA_123/view">
      <div class="flip-entry-title">{title}</div>
    </a>
    """

    with pytest.raises(BrowserPolicyError):
        parse_embedded_drive_files(
            html,
            course_source_id=COURSE_ID,
        )
