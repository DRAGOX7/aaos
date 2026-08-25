from __future__ import annotations

import pytest

from aaos.moodle.browser_models import BrowserPolicyError
from aaos.moodle.browser_parsing import parse_frontend_announcement_list

_ORIGIN = "https://moodle.example.edu"


def test_announcement_parser_ignores_extra_query_variant_backed_by_canonical_link() -> None:
    html = """
    <html>
      <body>
        <h1>Announcements</h1>
        <a href="/current/mod/forum/discuss.php?d=800">Announcement</a>
        <a href="/current/mod/forum/discuss.php?d=800&parent=123">
          Alternate link to the same discussion
        </a>
      </body>
    </html>
    """

    records = parse_frontend_announcement_list(
        html,
        course_source_id="42",
        expected_origin=_ORIGIN,
    )

    assert tuple(record.source_id for record in records) == ("800",)
    assert tuple(record.course_source_id for record in records) == ("42",)


def test_announcement_parser_rejects_extra_query_variant_without_canonical_link() -> None:
    html = """
    <html>
      <body>
        <h1>Announcements</h1>
        <a href="/current/mod/forum/discuss.php?d=800">Announcement</a>
        <a href="/current/mod/forum/discuss.php?d=801&parent=123">
          Unbacked alternate discussion
        </a>
      </body>
    </html>
    """

    with pytest.raises(BrowserPolicyError):
        parse_frontend_announcement_list(
            html,
            course_source_id="42",
            expected_origin=_ORIGIN,
        )
