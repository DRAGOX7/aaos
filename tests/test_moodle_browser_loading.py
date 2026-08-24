from __future__ import annotations

import pytest


def test_course_index_loading_diagnostic_reports_bounded_structural_progression_only() -> None:
    from aaos.moodle.browser_parsing import diagnose_course_index_loading

    initial = '<body class="page-mycourses"></body>'
    settled = (
        '<body class="page-mycourses"></body>'
        '<a data-moodle-course="true" '
        'href="https://moodle.example.edu/current/course/view.php?id=42">Private</a>'
        '<nav data-pagination-pages="0" data-pagination-exhausted="true"></nav>'
    )

    diagnostic = diagnose_course_index_loading(
        (initial, settled, settled), expected_origin="https://moodle.example.edu"
    )

    assert diagnostic.render().splitlines() == [
        "COURSE_INDEX_LOADING_DIAGNOSTIC",
        "PAGE_IDENTITY=MY_COURSES",
        "INITIAL_COURSE_LINK_COUNT=0",
        "SETTLED_COURSE_LINK_COUNT=1",
        "INITIAL_NUMERIC_ID_COUNT=0",
        "SETTLED_NUMERIC_ID_COUNT=1",
        "INITIAL_EMPTY_MARKER=NO",
        "SETTLED_EMPTY_MARKER=NO",
        "DOM_CHANGED_AFTER_LOAD=YES",
    ]
    assert "Private" not in diagnostic.render()
    assert "42" not in diagnostic.render()


def test_request_observation_classifies_blocked_ajax_post_without_url_data() -> None:
    from aaos.moodle.browser_navigation import classify_request_observation

    observation = classify_request_observation(
        "POST",
        "https://moodle.example.edu/current/lib/ajax/service.php?sesskey=redacted",
        "xhr",
        base_origin="https://moodle.example.edu",
    )

    assert observation.render() == "POST|MOODLE_SAME_ORIGIN|MOODLE_AJAX_SERVICE|XHR_FETCH|BLOCKED"
    assert "sesskey" not in observation.render()
    assert "moodle.example.edu" not in observation.render()


def test_request_observation_classifies_allowed_static_get() -> None:
    from aaos.moodle.browser_navigation import classify_request_observation

    observation = classify_request_observation(
        "GET",
        "https://moodle.example.edu/current/theme/styles.css",
        "stylesheet",
        base_origin="https://moodle.example.edu",
    )

    assert observation.render() == "GET|MOODLE_SAME_ORIGIN|STATIC_ASSET|STYLESHEET|ALLOWED"


def test_loading_diagnostic_never_invokes_course_parser(monkeypatch: pytest.MonkeyPatch) -> None:
    import aaos.moodle.browser_parsing as parsing

    monkeypatch.setattr(
        parsing,
        "parse_course_index",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("parser must not run")),
    )

    diagnostic = parsing.diagnose_course_index_loading(
        ("<body class='page-mycourses'></body>",) * 3,
        expected_origin="https://moodle.example.edu",
    )

    assert diagnostic.render().endswith("DOM_CHANGED_AFTER_LOAD=NO")


def test_static_asset_classifier_reports_fixed_classes_without_path_output() -> None:
    from aaos.moodle.browser_navigation import classify_static_asset_request

    origin = "https://moodle.example.edu"
    canonical_wwwroot = origin + "/current"
    assert (
        classify_static_asset_request(
            origin + "/current/theme/styles.php?theme=boost", canonical_wwwroot
        )
        == "THEME_STYLES"
    )
    assert (
        classify_static_asset_request(origin + "/current/lib/javascript.php", canonical_wwwroot)
        == "MOODLE_CORE_JS"
    )
    assert (
        classify_static_asset_request(
            origin + "/current/lib/amd/build/core.min.js", canonical_wwwroot
        )
        == "MOODLE_AMD_JS"
    )
    assert (
        classify_static_asset_request(
            origin + "/current/pluginfile.php/1/file.pdf", canonical_wwwroot
        )
        == "PLUGINFILE"
    )
    assert (
        classify_static_asset_request(origin + "/current/my/index.php", canonical_wwwroot)
        == "OTHER_MOODLE"
    )


def test_static_asset_classifier_normalizes_only_the_exact_canonical_moodle_base_path() -> None:
    from aaos.moodle.browser_navigation import classify_static_asset_request

    origin = "https://moodle.example.edu"
    canonical_wwwroot = origin + "/current"

    assert (
        classify_static_asset_request(
            origin + "/current/theme/styles.php/42/boost/all.css", canonical_wwwroot
        )
        == "THEME_STYLES"
    )
    assert (
        classify_static_asset_request(
            origin + "/current/lib/javascript.php/42/core.js", canonical_wwwroot
        )
        == "MOODLE_CORE_JS"
    )
    assert (
        classify_static_asset_request(
            origin + "/current/lib/requirejs.php/42/core/main.min.js", canonical_wwwroot
        )
        == "MOODLE_AMD_JS"
    )
    assert (
        classify_static_asset_request(origin + "/currentevil/theme/styles.php", canonical_wwwroot)
        == "OTHER_MOODLE"
    )
    assert (
        classify_static_asset_request(origin + "/foo/current/theme/styles.php", canonical_wwwroot)
        == "OTHER_MOODLE"
    )
    assert (
        classify_static_asset_request(
            "https://other.example.edu/current/theme/styles.php", canonical_wwwroot
        )
        == "OTHER_ORIGIN"
    )


def test_static_asset_classifier_uses_the_supplied_application_base_without_approving_routes() -> (
    None
):
    from aaos.moodle.browser_navigation import classify_static_asset_request

    origin = "https://moodle.example.edu"
    canonical_wwwroot = origin + "/moodle"

    assert (
        classify_static_asset_request(origin + "/moodle/theme/styles.php", canonical_wwwroot)
        == "THEME_STYLES"
    )
    assert (
        classify_static_asset_request(
            origin + "/moodle/pluginfile.php/1/file.pdf", canonical_wwwroot
        )
        == "PLUGINFILE"
    )
    assert (
        classify_static_asset_request(origin + "/moodle/lib/ajax/service.php", canonical_wwwroot)
        == "OTHER_MOODLE"
    )
    assert (
        classify_static_asset_request(origin + "/moodle/admin/index.php", canonical_wwwroot)
        == "OTHER_MOODLE"
    )
