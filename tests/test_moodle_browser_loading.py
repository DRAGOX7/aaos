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
        "https://moodle.example.edu/current/theme/styles.php/42/boost/all.css?theme=boost",
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


def test_bootstrap_route_classifier_distinguishes_fixed_static_files_from_handlers() -> None:
    from aaos.moodle.browser_navigation import classify_bootstrap_route_request

    origin = "https://moodle.example.edu"
    canonical_wwwroot = origin + "/current"

    assert (
        classify_bootstrap_route_request(
            origin + "/current/lib/thirdpartylib.min.js", canonical_wwwroot
        )
        == "STATIC_LIB_FILE"
    )
    assert (
        classify_bootstrap_route_request(
            origin + "/current/lib/amd/build/core.min.js", canonical_wwwroot
        )
        == "STATIC_AMD_BUILD_FILE"
    )
    assert (
        classify_bootstrap_route_request(
            origin + "/current/lib/javascript.php?cache=private", canonical_wwwroot
        )
        == "MOODLE_HANDLER_OTHER"
    )
    assert (
        classify_bootstrap_route_request(
            origin + "/current/lib/unapproved.php/42/file.js", canonical_wwwroot
        )
        == "MOODLE_HANDLER_OTHER"
    )


def test_bootstrap_route_classifier_fails_closed_for_academic_ajax_pluginfile_and_other_origin() -> (
    None
):
    from aaos.moodle.browser_navigation import classify_bootstrap_route_request

    origin = "https://moodle.example.edu"
    canonical_wwwroot = origin + "/current"

    assert (
        classify_bootstrap_route_request(
            origin + "/current/course/view.php?id=42", canonical_wwwroot
        )
        == "ACADEMIC_ROUTE"
    )
    assert (
        classify_bootstrap_route_request(
            origin + "/current/lib/ajax/service.php", canonical_wwwroot
        )
        == "AJAX_SERVICE"
    )
    assert (
        classify_bootstrap_route_request(
            origin + "/current/pluginfile.php/1/file.pdf", canonical_wwwroot
        )
        == "PLUGINFILE"
    )
    assert (
        classify_bootstrap_route_request(
            "https://other.example.edu/current/lib/thirdpartylib.min.js", canonical_wwwroot
        )
        == "OTHER_ORIGIN"
    )


def test_request_summary_aggregates_only_blocked_other_moodle_requests_without_urls() -> None:
    from aaos.moodle.browser_navigation import _RequestObserver, classify_request_observation

    observer = _RequestObserver()
    observer.record(
        classify_request_observation(
            "GET",
            "https://moodle.example.edu/current/lib/thirdpartylib.min.js?secret=redacted",
            "script",
            base_origin="https://moodle.example.edu",
            canonical_wwwroot="https://moodle.example.edu/current",
        ),
        url="https://moodle.example.edu/current/lib/thirdpartylib.min.js?secret=redacted",
        canonical_wwwroot="https://moodle.example.edu/current",
    )
    observer.record(
        classify_request_observation(
            "POST",
            "https://moodle.example.edu/current/lib/handler.php?sesskey=redacted",
            "xhr",
            base_origin="https://moodle.example.edu",
            canonical_wwwroot="https://moodle.example.edu/current",
        ),
        url="https://moodle.example.edu/current/lib/handler.php?sesskey=redacted",
        canonical_wwwroot="https://moodle.example.edu/current",
    )

    summary = observer.snapshot()

    assert summary.other_moodle_resource_counts == (("SCRIPT", 1), ("XHR_FETCH", 1))
    assert summary.other_moodle_method_counts == (("GET", 1), ("POST", 1))
    assert summary.bootstrap_route_counts == (
        ("MOODLE_HANDLER_OTHER", 1),
        ("STATIC_LIB_FILE", 1),
    )
    assert "secret" not in repr(summary)
    assert "sesskey" not in repr(summary)


def test_bootstrap_route_rendering_uses_fixed_counts_without_request_data() -> None:
    from aaos.moodle.browser import render_bootstrap_route_diagnostic
    from aaos.moodle.browser_navigation import BrowserRequestSummary

    rendered = render_bootstrap_route_diagnostic(
        BrowserRequestSummary(
            other_moodle_resource_counts=(("SCRIPT", 1),),
            other_moodle_method_counts=(("GET", 1),),
            other_origin_resource_counts=(("FONT", 1),),
            bootstrap_route_counts=(("STATIC_LIB_FILE", 1),),
        )
    )

    assert rendered.splitlines() == [
        "MOODLE_BOOTSTRAP_ROUTE_DIAGNOSTIC",
        "OTHER_MOODLE_SCRIPT=1",
        "OTHER_MOODLE_STYLESHEET=0",
        "OTHER_MOODLE_IMAGE=0",
        "OTHER_MOODLE_FONT=0",
        "OTHER_MOODLE_XHR_FETCH=0",
        "OTHER_MOODLE_DOCUMENT=0",
        "OTHER_MOODLE_OTHER=0",
        "OTHER_MOODLE_GET=1",
        "OTHER_MOODLE_HEAD=0",
        "OTHER_MOODLE_POST=0",
        "OTHER_MOODLE_OTHER_METHOD=0",
        "OTHER_ORIGIN_FONT=1",
        "STATIC_LIB_FILE_ATTEMPTED=1",
        "BOOTSTRAP_ROUTE_STATUS=SPECIFIC_STATIC_DEPENDENCY_FOUND",
    ]
    assert "?" not in rendered
    assert "http" not in rendered


def test_moodle_handler_classifier_recognizes_only_yui_combo_as_static_infrastructure() -> None:
    from aaos.moodle.browser_navigation import (
        classify_moodle_handler_request,
        handler_static_eligibility,
    )

    origin = "https://moodle.example.edu"
    canonical_wwwroot = origin + "/current"

    handler = classify_moodle_handler_request(
        origin + "/current/theme/yui_combo.php?rollup=private", canonical_wwwroot
    )

    assert handler == "MOODLE_YUI_COMBO_HANDLER"
    assert handler_static_eligibility(handler) == "VALIDATED_STATIC"
    assert (
        classify_moodle_handler_request(
            origin + "/current/theme/font.php?private=redacted", canonical_wwwroot
        )
        == "MOODLE_HANDLER_OTHER"
    )
    assert handler_static_eligibility("MOODLE_HANDLER_OTHER") == "AMBIGUOUS"


def test_handler_summary_keeps_only_blocked_handler_resource_types_without_request_data() -> None:
    from aaos.moodle.browser_navigation import _RequestObserver, classify_request_observation

    observer = _RequestObserver()
    for url, resource_type in (
        ("https://moodle.example.edu/current/theme/yui_combo.php?secret=redacted", "script"),
        ("https://moodle.example.edu/current/theme/font.php?secret=redacted", "font"),
    ):
        observer.record(
            classify_request_observation(
                "GET",
                url,
                resource_type,
                base_origin="https://moodle.example.edu",
                canonical_wwwroot="https://moodle.example.edu/current",
            ),
            url=url,
            canonical_wwwroot="https://moodle.example.edu/current",
        )

    summary = observer.snapshot()

    assert summary.moodle_handler_resource_counts == (("MOODLE_HANDLER_OTHER", "FONT", 1),)
    assert "secret" not in repr(summary)


def test_moodle_handler_rendering_exposes_fixed_counts_and_eligibility_only() -> None:
    from aaos.moodle.browser import render_moodle_handler_diagnostic
    from aaos.moodle.browser_navigation import BrowserRequestSummary

    rendered = render_moodle_handler_diagnostic(
        BrowserRequestSummary(
            moodle_handler_resource_counts=(
                ("MOODLE_HANDLER_OTHER", "FONT", 2),
                ("MOODLE_YUI_COMBO_HANDLER", "SCRIPT", 2),
                ("MOODLE_YUI_COMBO_HANDLER", "STYLESHEET", 1),
            )
        )
    )

    assert rendered.splitlines() == [
        "MOODLE_HANDLER_DIAGNOSTIC",
        "MOODLE_YUI_COMBO_HANDLER_SCRIPT=2",
        "MOODLE_YUI_COMBO_HANDLER_STYLESHEET=1",
        "MOODLE_YUI_COMBO_HANDLER_FONT=0",
        "MOODLE_YUI_COMBO_HANDLER_IMAGE=0",
        "MOODLE_YUI_COMBO_HANDLER_OTHER=0",
        "MOODLE_YUI_COMBO_HANDLER_STATIC_ELIGIBILITY=VALIDATED_STATIC",
        "MOODLE_HANDLER_OTHER_SCRIPT=0",
        "MOODLE_HANDLER_OTHER_STYLESHEET=0",
        "MOODLE_HANDLER_OTHER_FONT=2",
        "MOODLE_HANDLER_OTHER_IMAGE=0",
        "MOODLE_HANDLER_OTHER_OTHER=0",
        "MOODLE_HANDLER_OTHER_STATIC_ELIGIBILITY=AMBIGUOUS",
        "VALIDATED_STATIC_SCRIPT_DEPENDENCY_COUNT=2",
        "VALIDATED_STATIC_STYLESHEET_DEPENDENCY_COUNT=1",
        "AMBIGUOUS_HANDLER_COUNT=2",
        "HANDLER_CLASSIFICATION_STATUS=STATIC_DEPENDENCIES_IDENTIFIED",
    ]
    assert "?" not in rendered
    assert "http" not in rendered


def test_final_bootstrap_script_diagnostic_reports_only_the_unresolved_handler_script() -> None:
    from aaos.moodle.browser import render_final_bootstrap_script_diagnostic
    from aaos.moodle.browser_navigation import BrowserRequestSummary

    rendered = render_final_bootstrap_script_diagnostic(
        BrowserRequestSummary(
            moodle_handler_resource_counts=(
                ("MOODLE_HANDLER_OTHER", "FONT", 2),
                ("MOODLE_HANDLER_OTHER", "SCRIPT", 1),
                ("MOODLE_YUI_COMBO_HANDLER", "SCRIPT", 1),
            )
        )
    )

    assert rendered.splitlines() == [
        "FINAL_BOOTSTRAP_SCRIPT_DIAGNOSTIC",
        "AMBIGUOUS_SCRIPT_COUNT=1",
        "SCRIPT_HANDLER_CLASS=MOODLE_HANDLER_OTHER",
        "STATIC_ELIGIBILITY=AMBIGUOUS",
    ]
    assert "?" not in rendered
    assert "http" not in rendered
    assert "FONT" not in rendered


def test_final_bootstrap_script_diagnostic_does_not_promote_unexpected_handler_counts() -> None:
    from aaos.moodle.browser import render_final_bootstrap_script_diagnostic
    from aaos.moodle.browser_navigation import BrowserRequestSummary

    rendered = render_final_bootstrap_script_diagnostic(
        BrowserRequestSummary(
            moodle_handler_resource_counts=(("MOODLE_HANDLER_OTHER", "SCRIPT", 2),)
        )
    )

    assert rendered.splitlines() == [
        "FINAL_BOOTSTRAP_SCRIPT_DIAGNOSTIC",
        "AMBIGUOUS_SCRIPT_COUNT=2",
        "SCRIPT_HANDLER_CLASS=MOODLE_HANDLER_OTHER",
        "STATIC_ELIGIBILITY=AMBIGUOUS",
    ]
