from __future__ import annotations


def test_session_lifetime_report_has_only_fixed_process_bound_outcome() -> None:
    from aaos.moodle.session_lifetime import SessionLifetimeReport, render_session_lifetime_report

    rendered = render_session_lifetime_report(SessionLifetimeReport("AUTHENTICATED", "LOGIN_ROUTE"))

    assert rendered.splitlines() == [
        "SESSION_LIFETIME_DIAGNOSTIC",
        "SAME_CONTEXT_SESSION=AUTHENTICATED",
        "RELAUNCHED_SESSION=LOGIN_ROUTE",
        "PROFILE_BINDING_MATCH=PASS",
        "CLEAN_RELAUNCH=PASS",
        "SESSION_LIFETIME_RESULT=PROCESS_BOUND_SESSION_CONFIRMED",
    ]
